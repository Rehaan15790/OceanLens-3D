"""Round-6 features in the real page: the public plugin API (a variable and an instrument registered at runtime),
importing every file in samples/ (markers appear, a click opens the profile), the import dialog, the log colour
scale, the 3D isosurface, and server mode (the page served by server/serve.py lists the server's datasets)."""
import base64
import json
import sys
import threading
import unittest
from pathlib import Path

from helpers import REPO, STATE, AppTestCase, CDPError, pending

sys.path.insert(0, str(REPO / 'server'))

API = '!window.OceanLens'
IMPORT = '!(window.OceanLens && OceanLens.readData && OceanLens.importParsed)'
ISO3D = "typeof iso3dStats === 'undefined'"
SAMPLES = sorted(p for p in (REPO / 'samples').glob('*') if p.suffix.lower() in ('.nc', '.csv'))

# water nodes away from land with a sea floor below 150 m (as in test_render)
NODES = """(()=>{ const out=[];
  for (let idx=0; idx<gridPts.length && out.length<30; idx+=37) {
    const p=gridPts[idx]; if (p.i<1 || p.j<1 || p.i>LAT_N-2 || p.j>LON_N-2 || gridLand[idx] || gridSeabed[idx] < 150) continue;
    const near=[idx-1, idx+1, idx-LON_N, idx+LON_N];
    if (near.some(k=>k<0 || k>=gridPts.length || gridLand[k] || gridSeabed[k] < 150)) continue;
    out.push([p.lat, p.lon, 100]); }
  return out; })()"""


def slice_mismatches(sample_js, scale):
    """JS: compare the 100 m slice drawn on screen with the colour bar, for a value function `sample_js(lat, lon)`."""
    return f"""(()=>{{ const r=__ol.render, s={STATE}, bad=[];
      const hits = r.pixelsAt({NODES}, {{isolate:true}});
      for (const h of hits) {{
        if (!h.inView || !h.rgb) continue;
        const v = ({sample_js})(h.at.lat, h.at.lon);
        const want = r.expectedColour(s.palette, v, s.cbMin, s.cbMax, '{scale}');
        const d = Math.max(...h.rgb.map((c,i)=>Math.abs(c-want[i])));
        if (d > 2) bad.push(`${{h.at.lat.toFixed(2)}},${{h.at.lon.toFixed(2)}}: drawn ${{h.rgb}}, bar ${{want}}`);
      }}
      return {{n: hits.filter(h=>h.inView).length, bad}}; }})()"""


def open_imported_profile(self, ids):
    """Click the markers of the given instrument ids until the profile panel opens on one of them."""
    self.b.key('l')
    self.b.click('#resetViewBtn')
    self.b.pump(0.3)
    marks = self.js(f"__ol.render.marks().filter(m=>{json.dumps(sorted(ids))}.includes(m.id)).map(m=>[m.id, m.x, m.y])")
    self.assertTrue(marks, 'no marker on screen for the imported profiles')
    for mid, x, y in marks[:12]:
        self.b.click_at(x, y)
        self.b.pump(0.3)
        if self.js("document.getElementById('profile').classList.contains('show')") and \
                self.js('profileState.floatId') in ids:
            break
    else:
        self.fail(f'clicking {len(marks[:12])} imported markers never opened their profile')
    self.b.key('Escape')
    self.b.pump(0.2)


class PluginApi(AppTestCase):
    data_mode = 'real'

    @pending('plugin API (architect, plugins.js)', probe=API)
    def test_register_variable_at_runtime(self):
        got = self.js("""(()=>{ const seen=[]; OceanLens.on('variable', e=>seen.push(e.key));
          OceanLens.registerVariable('testlat', {label:'Latitude test', unit:'deg', palette:'heat', scale:'linear', dims:3,
            sample:(lat, lon, depth, t)=>lat, ranges:{real:[6, 22], synth:[6, 22]}, model:{real:true, synth:true}});
          __ol.flush(); return {seen, pill: !!document.querySelector('.pill[data-v="testlat"]')}; })()""")
        self.assertEqual(got['seen'], ['testlat'], "no 'variable' event")
        self.assertTrue(got['pill'], 'no variable pill for the runtime variable')
        self.b.key('1')                                   # the camera preset test_render uses
        self.js("__ol.actions.setVariable('testlat'); __ol.actions.setMode('slice'); __ol.actions.setDepth(4)")
        self.b.pump(0.3)
        self.assertEqual(self.js(f'{STATE}.variable'), 'testlat')
        res = self.js(slice_mismatches("(lat, lon)=>lat", 'linear'))
        self.assertGreaterEqual(res['n'], 15, f"only {res['n']} test points on screen")
        self.assertFalse(res['bad'], 'the runtime variable is not what is drawn:\n' + '\n'.join(res['bad'][:8]))
        self.js("__ol.actions.setVariable('temp')")
        self.assertNoProblems()

    @pending('plugin API (architect, plugins.js)', probe=API)
    def test_register_instrument_at_runtime(self):
        r = self.js("""(()=>{ OceanLens.registerInstrument('testbuoy', {label:'Test buoy', marker:'cross', variables:['temp'],
            kindLabel:()=>'Test buoy'});
          const res = OceanLens.addDataset({id:'rt-1', name:'runtime', source:'import', type:'testbuoy',
            profiles:[{id:'TB-1', lat:15.5, lon:88.5, time:META.times[__ol.state.time], z:[0, 10, 50, 100],
                       vars:{temp:[29, 28.6, 27.1, 24.0]}}]});
          __ol.actions.setOverlay('showOther', true); __ol.actions.setShowAllImported(true);
          return {res, inst: instrumentsNow().filter(i=>i.kind==='testbuoy').map(i=>i.id)}; })()""")
        self.assertEqual(r['res']['profiles'], 1, json.dumps(r['res']))
        self.assertEqual(r['inst'], ['TB-1'])
        open_imported_profile(self, {'TB-1'})
        self.js("OceanLens.removeDataset('rt-1')")
        self.assertEqual(self.js("instrumentsNow().filter(i=>i.kind==='testbuoy').length"), 0)
        self.assertNoProblems()

class ImportSamples(AppTestCase):
    data_mode = 'real'

    @pending('import plumbing (architect import.js, science formats.js)', probe=IMPORT)
    def test_every_sample_file_imports(self):
        self.assertTrue(SAMPLES, 'samples/ has no .nc or .csv files')
        for path in SAMPLES:
            with self.subTest(sample=path.name):
                b64 = base64.b64encode(path.read_bytes()).decode()
                r = self.js(f"""(async()=>{{ const bin=atob('{b64}'), u8=new Uint8Array(bin.length);
                    for (let i=0;i<bin.length;i++) u8[i]=bin.charCodeAt(i);
                    const p = await OceanLens.readData({json.dumps(path.name)}, u8.buffer);
                    if (p.error) return {{error:p.error}};
                    const r = OceanLens.importParsed(p, p.needsMapping ? {{mapping:p.mapping}} : {{}});
                    __ol.actions.setShowAllImported(true); __ol.actions.setOverlay('showOther', true);
                    const ids = instrumentsNow().filter(i=>i.imported && ((i.dataset && i.dataset.id) || i.dataset)===r.id)
                      .map(i=>i.id);
                    return {{format:p.format, type:p.type, id:r.id, profiles:r.profiles, ids}}; }})()""",
                            await_promise=True)
                self.assertNotIn('error', r, f"{path.name}: {r.get('error')}")
                self.assertGreater(r['profiles'], 0, f'{path.name}: no profiles imported')
                self.assertTrue(r['ids'], f'{path.name}: imported profiles are not instruments on the map')
                open_imported_profile(self, set(r['ids']))
                self.js(f"OceanLens.removeDataset({json.dumps(r['id'])})")
        self.assertNoProblems()

    @pending('import dialog (ux import-ui.js)', probe="typeof importUI === 'undefined'")
    def test_import_dialog_adds_and_removes(self):
        path = next(p for p in SAMPLES if p.suffix == '.csv')
        text = json.dumps(path.read_text(encoding='utf-8'))
        self.b.click('#addDataBtn')
        self.b.pump(0.3)
        self.js(f"importUI.readFile(new File([{text}], {json.dumps(path.name)}, {{type:'text/csv'}}))", await_promise=True)
        self.b.wait("(()=>{ const b=document.getElementById('importAdd'); return !b.disabled && b.getBoundingClientRect().width>0; })()", 5)
        self.click_control('#importAdd')
        try:
            self.b.wait("document.querySelectorAll('#userDataList button').length", 5)
        except CDPError:
            self.fail('the imported file is not listed')
        self.b.click('#userDataList button')
        self.b.pump(0.3)
        self.assertEqual(self.js("OceanLens.datasets().filter(d=>d.source==='import').length"), 0)
        self.assertNoProblems()


class LogScaleAndIsosurface(AppTestCase):
    data_mode = 'real'

    @pending('log colour scale (architect setScale, graphics rampPos)',
             probe="!(window.__ol && __ol.actions && __ol.actions.setScale)")
    def test_log_scale_slice_matches_the_bar(self):
        self.b.key('1')
        self.js("__ol.actions.setVariable('temp'); __ol.actions.setMode('slice'); __ol.actions.setDepth(4);"
                "__ol.actions.setScale('log')")
        self.b.pump(0.3)
        self.assertEqual(self.js('colorbarSpec().scale'), 'log')
        pos = self.js("[Render.rampPos(10, 5, 40, 'log'), Math.log10(2)/Math.log10(8)]")
        self.assertAlmostEqual(pos[0], pos[1], places=6, msg='log ramp position is not log10-linear')
        res = self.js(slice_mismatches("(lat, lon)=>fieldTemp(lat, lon, 100, __ol.state.time)", 'log'))
        self.assertGreaterEqual(res['n'], 15, f"only {res['n']} test points on screen")
        self.assertFalse(res['bad'], 'log-scale colours off the bar by more than 2/255:\n' + '\n'.join(res['bad'][:8]))
        self.js("__ol.actions.setScale('linear')")
        self.assertNoProblems()

    @pending('3D isosurface (graphics isosurface.js)', probe=ISO3D)
    def test_isosurface_3d_builds_and_follows_the_value(self):
        self.js("__ol.actions.setVariable('temp'); __ol.actions.setMode('iso3d'); __ol.actions.setIso(26)")
        self.b.pump(0.4)
        a = self.js('({mode: __ol.state.mode, tri: iso3dStats.triangles, ms: iso3dStats.ms, vis: iso3dMesh.visible})')
        self.assertEqual(a['mode'], 'iso3d')
        self.assertTrue(a['vis'], 'the isosurface mesh is hidden in its own view')
        self.assertGreater(a['tri'], 500, 'the 26 degC surface has almost no triangles')
        self.js('__ol.actions.setIso(20)')
        self.b.pump(0.3)
        b = self.js('({tri: iso3dStats.triangles, ms: iso3dStats.ms})')
        self.assertNotEqual(a['tri'], b['tri'], 'changing the value did not rebuild the surface')
        self.assertLess(b['ms'], 250, f"rebuild took {b['ms']:.0f} ms (budget about 50 ms on real GPUs)")
        self.js("__ol.actions.setMode('surface')")
        self.assertNoProblems()


class ColourAndLayerControls(AppTestCase):
    """The colour bar editor (palette, min/max, linear/log), the variable selector, layer opacity and vertical
    exaggeration, driven through their own controls."""

    def set_control(self, selector, value, event):
        self.click_control(selector)        # opens the menu that holds it, and focuses it as a user would
        self.js(f"(()=>{{ const e=document.querySelector({json.dumps(selector)}); e.value={json.dumps(str(value))};"
                f" e.dispatchEvent(new Event('{event}', {{bubbles:true}})); }})()")
        self.b.pump(0.15)

    def test_controls_drive_the_state_and_colour_bar(self):
        self.click_control('.pill[data-v="sal"]')
        self.assertEqual(self.js(f'{STATE}.variable'), 'sal')
        self.set_control('#paletteSel', 'heat', 'change')
        self.set_control('#cbMin', '33.5', 'change')
        self.set_control('#cbMax', '36', 'change')
        s = self.js(f"(()=>{{ const s={STATE}; return [s.palette, s.cbMin, s.cbMax, colorbarSpec().palette]; }})()")
        self.assertEqual(s, ['heat', 33.5, 36, 'heat'])
        self.click_control('#scaleToggle [data-scale="log"]')
        self.assertEqual(self.js(f'{STATE}.scale'), 'log')
        self.click_control('#scaleToggle [data-scale="linear"]')
        self.set_control('#opSlider', 60, 'input')
        self.set_control('#exSlider', 50, 'input')
        self.assertEqual(self.js(f'[{STATE}.opacity, {STATE}.exagg]'), [0.6, 5])
        self.click_control('#cbReset')
        self.assertNotEqual(self.js(f'{STATE}.cbMin'), 33.5, 'reset left the custom range')
        self.click_control('.pill[data-v="temp"]')
        self.assertNoProblems()


class ServerMode(AppTestCase):
    """The page served over http by server/serve.py (port 8775) finds the server's datasets."""
    enter = False

    @classmethod
    def setUpClass(cls):
        import serve
        from cdp import default_build
        cls.srv = serve.make_server('127.0.0.1', 0, app=default_build(), quiet=True)
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.url = f'http://127.0.0.1:{cls.srv.server_address[1]}/'
        super().setUpClass()

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        cls.srv.shutdown()
        cls.srv.server_close()

    @pending('server mode (architect import.js)', probe="!(window.OceanLens && 'server' in OceanLens)")
    def test_served_page_lists_server_datasets(self):
        from cdp import open_app
        self.assertEqual(open_app(self.b, self.url), 'ready')
        self.b.wait('window.OceanLens && OceanLens.server && OceanLens.server.live === true', timeout=10)
        ids = self.js('OceanLens.server.datasets.map(d=>d.id)')
        self.assertIn('amphan-2020', ids)
        away = [r['url'] for r in self.b.external_requests() if not r['url'].startswith(self.url)]
        self.assertFalse(away, f'requests left the server: {away[:3]}')
        self.assertNoProblems()


if __name__ == '__main__':
    unittest.main()
