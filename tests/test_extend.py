"""Round-6 features in the real page: the public plugin API (a variable and an instrument registered at runtime),
importing every file in samples/ (markers appear, a click opens the profile), real-world file layouts (Argo GDAC
multi-profile NetCDF, ERDDAP CSV with a units row, OceanGliders glider tables), the import dialog (new profiles are
brought into view and opened), the log colour scale, the 3D isosurface, and server mode (the page served by
server/serve.py lists the server's datasets)."""
import base64
import datetime
import json
import math
import sys
import threading
import unittest
from pathlib import Path

from helpers import REPO, STATE, AppTestCase, CDPError, pending

sys.path.insert(0, str(REPO / 'server'))
sys.path.insert(0, str(REPO / 'data-pipeline'))

API = '!window.OceanLens'
IMPORT = '!(window.OceanLens && OceanLens.readData && OceanLens.importParsed)'
ISO3D = "typeof iso3dStats === 'undefined'"
SAMPLES = sorted(p for p in (REPO / 'samples').glob('*') if p.suffix.lower() in ('.nc', '.csv'))
# profiles each sample must give (gliders: dives and climbs, not one profile per sample)
SAMPLE_PROFILES = {'argo-5907328_prof.nc': 3, 'argo-amphan-samples.csv': 3, 'argo-erddap-2020-05-18.csv': 3,
                   'bgc-argo-2902120-c227.nc': 1, 'ctd-synthetic-cast.csv': 1, 'glider-humpback504-2016-07-05.nc': 4,
                   'glider-marlin505-2016-07-06.csv': 6}
READ_SAMPLE = """(async()=>{{ const bin=atob('{b64}'), u8=new Uint8Array(bin.length);
    for (let i=0;i<bin.length;i++) u8[i]=bin.charCodeAt(i);
    {body} }})()"""


def sample_js(path, body):
    """JS that reads a sample file into `u8` (a Uint8Array) and runs `body` (async; return a value)."""
    return READ_SAMPLE.format(b64=base64.b64encode(Path(path).read_bytes()).decode(), body=body)


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

    @pending('plugin API (architect, plugins.js)', probe=API)
    def test_adcp_profiles_show_current_speed(self):
        r = self.js("""(()=>{ const t=__ol.state.time; const res=OceanLens.addDataset({id:'adcp-1',name:'adcp',source:'import',
          type:'adcp',profiles:[{id:'ADCP-1',lat:15.5,lon:88.5,time:META.times[t],z:[10,30,60],vars:{u:[0.3,0.2,null],v:[0.4,0.0,0.1]}}]});
          __ol.actions.setOverlay('showOther',true); const i=instrumentsNow().find(x=>x.kind==='adcp');
          return {n:res.profiles, cur:Array.from(i.obs.vars.cur,v=>Number.isFinite(v)?+v.toFixed(3):null)}; })()""")
        self.assertEqual(r, {'n': 1, 'cur': [0.5, 0.2, None]})
        open_imported_profile(self, {'ADCP-1'})
        self.assertEqual(self.js('profileState.tab'), 'cur')
        self.js("OceanLens.removeDataset('adcp-1')")
        self.assertNoProblems()

    @pending('plugin API (architect, plugins.js)', probe=API)
    def test_hf_radar_product_maps_surface_current(self):
        r = self.js("""(()=>{ const t=__ol.state.time, c=[];
          for(let la=17;la<=19.001;la+=0.1) for(let lo=86;lo<=88.001;lo+=0.1)
            c.push({lat:+la.toFixed(2),lon:+lo.toFixed(2),time:META.times[t],vars:{u:[0.3],v:[0.4]}});
          OceanLens.addDataset({id:'hfr-1',name:'radar',source:'import',type:'hfradar',profiles:c}); __ol.flush();
          const g=field2dGrid('hfr',t).filter(v=>v!==null); __ol.actions.setVariable('hfr');
          return {n:g.length, lo:Math.min(...g), hi:Math.max(...g), marks:instrumentsNow().filter(i=>i.kind==='hfradar').length,
                  pill:!!document.querySelector('.pill[data-v="hfr"]'), view:currentView()}; })()""")
        self.assertGreater(r['n'], 10)
        self.assertAlmostEqual(r['lo'], 0.5, 3)
        self.assertAlmostEqual(r['hi'], 0.5, 3)
        self.assertEqual((r['marks'], r['pill'], r['view']), (0, True, 'field2d'))
        self.js("__ol.actions.setVariable('temp'); OceanLens.removeDataset('hfr-1')")
        self.assertNoProblems()


class ImportSamples(AppTestCase):
    data_mode = 'real'

    @pending('import plumbing (architect import.js, science formats.js)', probe=IMPORT)
    def test_every_sample_file_imports(self):
        self.assertTrue(SAMPLES, 'samples/ has no .nc or .csv files')
        readme = (REPO / 'samples' / 'README.md').read_text(encoding='utf-8')
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
                    return {{format:p.format, type:p.type, id:r.id, profiles:r.profiles, skipped:r.skipped, ids,
                      needsMapping:p.needsMapping}}; }})()""",
                            await_promise=True)
                self.assertNotIn('error', r, f"{path.name}: {r.get('error')}")
                self.assertIn(f'`{path.name}`', readme, f'{path.name}: not described in samples/README.md')
                self.assertGreater(r['profiles'], 0, f'{path.name}: no profiles imported')
                self.assertEqual(r['skipped'], 0, f'{path.name}: profiles outside the map')
                self.assertFalse(r['needsMapping'], f'{path.name}: the columns were not recognised from the header')
                if path.name in SAMPLE_PROFILES:
                    self.assertEqual(r['profiles'], SAMPLE_PROFILES[path.name], f'{path.name}: profile count')
                self.assertTrue(r['ids'], f'{path.name}: imported profiles are not instruments on the map')
                open_imported_profile(self, set(r['ids']))
                self.js(f"OceanLens.removeDataset({json.dumps(r['id'])})")
        self.assertNoProblems()

    @pending('import plumbing (architect import.js, science formats.js)', probe=IMPORT)
    def test_argo_netcdf_in_cdf2_and_cdf5(self):
        """netcdf3.js on the 64-bit-offset and 64-bit-data variants, through the Argo profile mapping."""
        import numpy as np
        import ncio
        nl = 4
        dims = {'N_PROF': 1, 'N_LEVELS': nl, 'STRING8': 8}
        f4 = lambda a: np.array(a, np.float32).reshape(1, nl)
        vars_ = {
            'PLATFORM_NUMBER': {'dims': ('N_PROF', 'STRING8'), 'data': np.array([list('9999001 ')], 'S1')},
            'CYCLE_NUMBER': {'dims': ('N_PROF',), 'data': np.array([12], np.int32)},
            'DATA_MODE': {'dims': ('N_PROF',), 'data': np.array([b'D'], 'S1')},
            'JULD': {'dims': ('N_PROF',), 'data': np.array([25704.5]),
                     'attrs': {'units': 'days since 1950-01-01 00:00:00 UTC', '_FillValue': 999999.0}},
            'JULD_QC': {'dims': ('N_PROF',), 'data': np.array([b'1'], 'S1')},
            'LATITUDE': {'dims': ('N_PROF',), 'data': np.array([15.5])},
            'LONGITUDE': {'dims': ('N_PROF',), 'data': np.array([88.5])},
            'POSITION_QC': {'dims': ('N_PROF',), 'data': np.array([b'1'], 'S1')},
        }
        for name, vals in (('PRES', [5, 50, 100, 200]), ('TEMP', [29.5, 28.9, 25.0, 17.0]), ('PSAL', [33.2, 33.9, 34.6, 35.0])):
            for suffix in ('', '_ADJUSTED'):
                vars_[name + suffix] = {'dims': ('N_PROF', 'N_LEVELS'), 'data': f4(vals), 'attrs': {'_FillValue': np.float32(99999)}}
                vars_[name + suffix + '_QC'] = {'dims': ('N_PROF', 'N_LEVELS'), 'data': np.array([list('1111')], 'S1')}
        for fmt in ('CDF-2', 'CDF-5'):
            with self.subTest(fmt=fmt):
                b64 = base64.b64encode(ncio.write_bytes(dims, vars_, {'Conventions': 'Argo-3.1 CF-1.6'}, fmt)).decode()
                r = self.js(f"""(async()=>{{ const bin=atob('{b64}'), u8=new Uint8Array(bin.length);
                    for (let i=0;i<bin.length;i++) u8[i]=bin.charCodeAt(i);
                    const nc = NetCDF3.parse(u8.buffer.slice(0));
                    const p = await OceanLens.readData('R9999001_012.nc', u8.buffer);
                    if (p.error) return {{error:p.error, version:nc.version}};
                    const r = OceanLens.importParsed(p); __ol.actions.setShowAllImported(true);
                    const i = instrumentsNow().find(x=>x.imported && x.dataset===r.id);
                    OceanLens.removeDataset(r.id);
                    return {{version:nc.version, format:p.formatLabel, profiles:r.profiles, T:i && i.obs.T}}; }})()""",
                            await_promise=True)
                self.assertNotIn('error', r, f"{fmt}: {r.get('error')}")
                self.assertEqual(r['version'], {'CDF-2': 2, 'CDF-5': 5}[fmt])
                self.assertEqual(r['profiles'], 1)
                self.assertIn('Argo', r['format'])
                np.testing.assert_allclose(r['T'], [29.5, 28.9, 25.0, 17.0], atol=1e-4)
        self.assertNoProblems()

    @pending('import plumbing (architect import.js, science formats.js)', probe=IMPORT)
    def test_ego_glider_and_cf_profile_netcdf(self):
        """The EGO glider and generic CF NetCDF mappings, on synthetic files written by ncio.py."""
        import numpy as np
        from ncio_fixtures import cf_profiles_bytes, ego_glider_bytes
        for fx in (ego_glider_bytes(), cf_profiles_bytes()):
            with self.subTest(format=fx['format']):
                b64 = base64.b64encode(fx['data']).decode()
                r = self.js(f"""(async()=>{{ const bin=atob('{b64}'), u8=new Uint8Array(bin.length);
                    for (let i=0;i<bin.length;i++) u8[i]=bin.charCodeAt(i);
                    const p = await OceanLens.readData({json.dumps(fx['filename'])}, u8.buffer);
                    if (p.error) return {{error:p.error}};
                    const r = OceanLens.importParsed(p); __ol.actions.setShowAllImported(true);
                    __ol.actions.setOverlay('showGlider', true); __ol.actions.setOverlay('showOther', true);
                    const insts = instrumentsNow().filter(i=>i.imported && i.dataset===r.id).sort((a, b)=>a.ms-b.ms);
                    const f = insts[0];
                    return {{label:p.formatLabel, want:IMPORT_FORMATS[{json.dumps(fx['format'])}].label, type:p.type,
                            id:r.id, profiles:r.profiles, ids:insts.map(i=>i.id), first:f.id, z:f.levels,
                            T:Array.from(f.obs.T, v=>Number.isFinite(v) ? v : null),
                            warnings:(p.warnings || []).concat(r.warnings || [])}}; }})()""", await_promise=True)
                self.assertNotIn('error', r, f"{fx['filename']}: {r.get('error')}")
                self.assertEqual(r['label'], r['want'], 'detected as another format')
                self.assertEqual((r['type'], r['profiles'], r['first']), (fx['type'], fx['profiles'], fx['first_id']))
                np.testing.assert_allclose(r['z'], fx['first_z'], atol=0.01)
                self.assertEqual([t is None for t in r['T']], [t is None for t in fx['first_temp']], 'missing levels')
                np.testing.assert_allclose([t for t in r['T'] if t is not None],
                                           [t for t in fx['first_temp'] if t is not None], atol=1e-4)
                for w in fx['warnings']:
                    self.assertTrue(any(w in x for x in r['warnings']), f'no warning containing {w!r}: {r["warnings"]}')
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
        self.b.wait("document.getElementById('importAnnounce').textContent", 5)   # the panel scrolls to the notice
        self.b.click('#userDataList button')
        self.b.pump(0.3)
        self.assertEqual(self.js("OceanLens.datasets().filter(d=>d.source==='import').length"), 0)
        self.assertNoProblems()

    def test_real_world_file_layouts(self):
        """Files as the data centres serve them: an Argo GDAC multi-profile file (record variables with N_HISTORY = 0;
        a copy cut short by even one byte is still refused), ERDDAP's default CSV (a units row under the header) and
        OceanGliders tables as .nc (time valid_max 90000, no profile id, QC columns all missing) and .csv; a glider
        becomes whole dives and climbs, each from the surface to about 1000 m. Every profile is whole: no level is
        100 m or more from the next (the Argo profiles run 0-2000 dbar without a missing packet)."""
        want = {'argo-5907328_prof.nc': ('Argo profile NetCDF', 'argo'),
                'argo-erddap-2020-05-18.csv': ('Delimited text (comma)', 'argo'),
                'glider-humpback504-2016-07-05.nc': ('ERDDAP tabledap NetCDF', 'glider'),
                'glider-marlin505-2016-07-06.csv': ('Delimited text (comma)', 'glider')}
        for name, (label, kind) in want.items():
            with self.subTest(sample=name):
                nc = 'NetCDF3.parse(u8.buffer.slice(0))' if name.endswith('.nc') else 'null'
                r = self.js(sample_js(REPO / 'samples' / name, f"""const nc = {nc};
                    const p = await OceanLens.readData({json.dumps(name)}, u8.buffer);
                    if (p.error) return {{error:p.error}};
                    const r = OceanLens.importParsed(p);
                    const insts = OceanLens.observations(p.type).filter(i=>i.dataset===r.id);
                    OceanLens.removeDataset(r.id);
                    const cut = [];
                    for (const n of [1, 2000]) cut.push((await OceanLens.readData({json.dumps(name)}, u8.buffer.slice(0, u8.length - n))).error);
                    return {{label:p.formatLabel, type:p.type, profiles:r.profiles, units:p.units,
                      warnings:(p.warnings || []).concat(r.warnings || []), ids:[...new Set(insts.map(i=>i.id))],
                      levels:insts.map(i=>i.levels.length), top:insts.map(i=>i.levels[0]), deepest:insts.map(i=>i.maxDepth), cut,
                      gap:insts.map(i=>Math.max(...i.levels.slice(1).map((z, k)=>z - i.levels[k]))),
                      numrecs:nc && nc.numrecs, history:nc && (nc.dims.find(d=>d.name==='N_HISTORY') || {{}}).size,
                      times:nc && nc.vars.time ? Array.from(nc.read('time')).every(Number.isFinite) : null}};"""),
                            await_promise=True)
                self.assertNotIn('error', r, f"{name}: {r.get('error')}")
                self.assertEqual((r['label'], r['type'], r['profiles']), (label, kind, SAMPLE_PROFILES[name]))
                self.assertFalse([w for w in r['warnings'] if 'not recognised' in w], r['warnings'])
                self.assertTrue(all(g < 100 for g in r['gap']), f"a profile has a hole: largest gaps {r['gap']} m")
                if kind == 'argo':
                    self.assertTrue(all(i.startswith('ARGO-') for i in r['ids']), f"not named as Argo floats: {r['ids']}")
                if name.endswith('_prof.nc'):
                    self.assertEqual((r['numrecs'], r['history']), (0, 0), 'not the N_HISTORY = 0 layout')
                    for err in r['cut']:
                        self.assertRegex(err or '', 'end of the file|ends early', 'a truncated copy was read')
                    self.assertTrue(all(n >= 50 and t < 10 and z > 1900 for n, t, z in zip(r['levels'], r['top'], r['deepest'])),
                                    f"not every profile is 0-2000 m: {r['levels']} {r['top']} {r['deepest']}")
                if name.endswith('.csv'):
                    self.assertIn('degree_Celsius', r['units'], 'the ERDDAP units row was not read as units')
                if kind == 'glider':
                    self.assertEqual(len(r['ids']), 1, f"one glider, not {r['ids'][:5]}")
                    self.assertTrue(all(n >= 50 and t < 5 and z > 900 for n, t, z in zip(r['levels'], r['top'], r['deepest'])),
                                    f"not whole dives and climbs: {r['levels']} {r['top']} {r['deepest']}")
                if name == 'glider-humpback504-2016-07-05.nc':
                    self.assertTrue(r['times'], 'times masked by the valid range')
        self.assertNoProblems()

    def test_dense_trajectory_splits_into_dives(self):
        """A glider sampled every second (steps well under the 5 dbar turn threshold) still splits into its dive,
        climb and second dive, with or without an id column, and each starts where the last turned (the samples at
        a turn belong to both). One row per time with a fixed depth does not split, nor does a mooring whose sensor
        is knocked down by 30 m, when the file name or a type column says it is a mooring."""
        ramp = [i * 0.5 for i in range(201)] + [100 - i * 0.5 for i in range(1, 201)] + [i * 0.5 for i in range(1, 121)]
        rows = [f'G1,2020-05-18T06:{k // 60:02d}:{k % 60:02d}Z,15.5,88.5,{p},{30 - p / 10:.2f}' for k, p in enumerate(ramp)]
        # a sensor at 100 dbar every 10 minutes for two days, knocked down by up to 30 dbar
        t0 = datetime.datetime(2020, 5, 17)
        knock = [((t0 + datetime.timedelta(minutes=10 * k)).strftime('%Y-%m-%dT%H:%M:%SZ'),
                  100 + 15 * (1 - math.cos(2 * math.pi * k / 74.5))) for k in range(288)]
        files = {'with id': ('dense.csv', 'id,time,lat,lon,pres,temp\n' + '\n'.join(rows)),
                 'no id': ('dense.csv', 'time,lat,lon,pres,temp\n' + '\n'.join(r.split(',', 1)[1] for r in rows)),
                 'fixed depth': ('dense.csv', 'id,time,lat,lon,depth,temp\n' + '\n'.join(
                     f'M1,2020-05-18T06:{k // 60:02d}:{k % 60:02d}Z,15.5,88.5,50,{28 + k / 100:.2f}' for k in range(30))),
                 'mooring by name': ('rama-mooring.csv', 'id,time,lat,lon,pres,temp\n' + '\n'.join(
                     f'RAMA-15N90E,{t},15,90,{p:.1f},{25 + math.sin(k / 10):.2f}' for k, (t, p) in enumerate(knock))),
                 'mooring by type': ('dense.csv', 'id,type,time,lat,lon,pres,temp\n' + '\n'.join(
                     f'RAMA-15N90E,mooring,{t},15,90,{p:.1f},{25 + math.sin(k / 10):.2f}' for k, (t, p) in enumerate(knock)))}
        got = {}
        for case, (name, text) in files.items():
            got[case] = self.js(f"""(async()=>{{ const p = await OceanLens.readData({json.dumps(name)}, {json.dumps(text)});
                const d = p._dataset; return p.error || {{type:d.type, z:d.profiles.map(x=>[x.z.length, x.z[0]])}}; }})()""",
                                await_promise=True)
        self.assertEqual([len(got['with id']['z']), len(got['no id']['z'])], [3, 3], got)
        self.assertTrue(all(n >= 50 for n, _ in got['with id']['z']), got)
        self.assertLess(got['with id']['z'][2][1], 1, f"the second dive lost its top to the climb: {got['with id']['z']}")
        self.assertEqual(len(got['fixed depth']['z']), 30, 'a fixed-depth time series was split as a trajectory')
        for case in ('mooring by name', 'mooring by type'):
            self.assertEqual(len(got[case]['z']), 288, f'{case}: a mooring was split into dives and climbs')
            self.assertNotEqual(got[case]['type'], 'glider', case)
        self.assertNoProblems()

    def test_units_and_valid_ranges_as_data_centres_write_them(self):
        """A units row with CF spellings (degree_Celsius, 1e-3, umol kg-1, m s-1, cm s-1) is read as units and
        converted. NetCDF valid_min/valid_max mask neither times and coordinates nor every value of a variable (wrong
        metadata, as in ERDDAP glider tables), but still mask the values outside them otherwise."""
        import numpy as np
        import ncio
        text = ('time,latitude,longitude,depth,temperature,salinity,oxygen,u,v\n'
                'UTC,degrees_north,degrees_east,m,degree_Celsius,1e-3,umol kg-1,m s-1,cm s-1\n'
                '2020-05-18T06:00:00Z,15.5,88.5,5,29.5,33.9,200,0.3,10\n'
                '2020-05-18T06:00:00Z,15.5,88.5,50,28.9,34.1,190,0.2,5\n')
        r = self.js(f"""(async()=>{{ const p = await OceanLens.readData('units.csv', {json.dumps(text)});
            const d = p._dataset, v = d && d.profiles[0].vars;
            return {{needsMapping:p.needsMapping, units:p.units, warnings:p.warnings, temp:v && v.temp, oxy:v && v.oxy,
                     v:v && v.v}}; }})()""", await_promise=True)
        self.assertFalse(r['needsMapping'], f"no header found above the units row: {r['warnings']}")
        self.assertEqual(r['units'][4], 'degree_Celsius')
        self.assertFalse([w for w in r['warnings'] if 'not recognised' in w], r['warnings'])
        np.testing.assert_allclose([r['temp'], r['oxy'], r['v']], [[29.5, 28.9], [200, 190], [0.1, 0.05]], atol=1e-6)
        n = 6
        f4 = lambda a, **at: {'dims': ('row',), 'data': np.array(a, np.float32), 'attrs': at}
        data = ncio.write_bytes({'row': n}, {
            'time': {'dims': ('row',), 'data': 1.5897e9 + 60.0 * np.arange(n),
                     'attrs': {'units': 'seconds since 1970-01-01T00:00:00Z', 'valid_min': 0.0, 'valid_max': 90000.0}},
            'latitude': f4([0.5] + [15.5] * (n - 1), units='degrees_north', axis='Y', valid_min=np.float32(-1),
                           valid_max=np.float32(1)),
            'TEMP': f4([29.5, 28.9, 25.0, 20.0, 25.1, 28.8], valid_min=np.float32(40), valid_max=np.float32(50)),
            'PSAL': f4([33.9, 34.1, 36.5, 34.9, 34.8, 34.2], valid_min=np.float32(30), valid_max=np.float32(35)),
        }, {'cdm_data_type': 'Trajectory'})
        b64 = base64.b64encode(data).decode()
        r = self.js(f"""(()=>{{ const bin=atob('{b64}'), u8=new Uint8Array(bin.length);
            for (let i=0;i<bin.length;i++) u8[i]=bin.charCodeAt(i);
            const nc = NetCDF3.parse(u8.buffer), fin = k=>Array.from(nc.read(k), Number.isFinite);
            return {{time:fin('time'), lat:fin('latitude'), temp:fin('TEMP'), sal:fin('PSAL')}}; }})()""")
        self.assertEqual(r, {'time': [True] * n, 'lat': [True] * n, 'temp': [True] * n,
                             'sal': [True, True, False, True, True, True]})
        self.assertNoProblems()

    def test_added_profiles_are_brought_into_view(self):
        """A file chosen in Add data and added with a click is on screen with its first profile open, and the notice
        under Your data, scrolled into view, says how: the timeline moves to the analysis nearest the first profile
        (BGC-Argo, 17 May 2020), stays when new profiles already show (Argo CSV, 15-17 May), or Show all times turns
        on (a 2016 glider), with that switch in view too. A collapsed right panel (a narrow window) opens. Another
        dive of the same glider, once opened, stays open through an update; removing the glider turns Show all times
        off again."""
        self.js("__ol.actions.setShowAllImported(false); __ol.actions.setTime(0); __ol.actions.closePanels()")
        self.addCleanup(self.js, "(()=>{ shell.closeDialog(); OceanLens.datasets().filter(d=>d.source==='import')"
                                 ".forEach(d=>OceanLens.removeDataset(d.id)); __ol.actions.setShowAllImported(false);"
                                 " __ol.actions.setTime(0); __ol.actions.setVariable('temp'); shell.setPanel('right', true); })()")
        cases = (('bgc-argo-2902120-c227.nc', 'moved', 'ARGO-2902120'), ('argo-amphan-samples.csv', 'stays', 'ARGO-2902120'),
                 ('glider-humpback504-2016-07-05.nc', 'all', 'Humpback_504'))
        added = []
        for name, how, first in cases:
            with self.subTest(sample=name):
                t0 = self.js('__ol.state.time')
                self.js("shell.setPanel('right', false)")       # as below 1100 px
                self.b.click('#addDataBtn')
                self.b.pump(0.3)
                doc = self.b.send('DOM.getDocument')
                node = self.b.send('DOM.querySelector', nodeId=doc['root']['nodeId'], selector='#importInput')
                self.b.send('DOM.setFileInputFiles', nodeId=node['nodeId'], files=[str(REPO / 'samples' / name)])
                self.b.wait("(()=>{ const b=document.getElementById('importAdd');"
                            " return !b.hidden && b.getBoundingClientRect().width>0; })()", 10)
                self.click_control('#importAdd')
                self.b.wait("document.getElementById('importAnnounce').textContent", 5)
                self.b.pump(0.3)
                got = self.js(f"""(()=>{{ __ol.flush(); const d=OceanLens.datasets().find(x=>x.name==={json.dumps(name)});
                    const el=document.getElementById('importAnnounce'), r=el.getBoundingClientRect(),
                      L=document.getElementById('left').getBoundingClientRect(),
                      sw=document.getElementById('userDataOpts').getBoundingClientRect();
                    return {{id:d.id, time:__ol.state.time, all:__ol.state.showAllImported,
                      right:!document.getElementById('app').classList.contains('right-collapsed'),
                      swInView:sw.height>0 && sw.top>=L.top && sw.bottom<=L.bottom+1,
                      open:document.getElementById('profile').classList.contains('show') && profileState.inst.dataset===d.id
                        ? profileState.inst.id : null,
                      drawn:markerItems.filter(it=>it.userData.data && it.userData.data.inst.dataset===d.id && instrumentVisible(it.vis)).length,
                      now:instrumentsNow().filter(i=>i.dataset===d.id).length,
                      text:el.textContent, inView:r.height>0 && r.top>=L.top && r.bottom<=L.bottom+1,
                      rect:[r.top, r.bottom, L.top, L.bottom]}}; }})()""")
                added.append(got['id'])
                self.assertEqual(got['open'], first, 'the profile panel did not open on the first new profile')
                self.assertGreater(got['now'], 0, 'none of the new profiles shows at this time')
                self.assertGreater(got['drawn'], 0, 'no marker is drawn for the new profiles')
                self.assertTrue(got['inView'], f"the notice is not on screen: {got['rect']}")
                self.assertTrue(got['swInView'], 'the Show all times switch is not on screen')
                self.assertTrue(got['right'], 'the profile opened in a collapsed panel')
                self.assertEqual(got['all'], how == 'all')
                self.assertIn(f'Profile {first} is open', got['text'])
                if how == 'all':
                    self.assertEqual(got['time'], t0)
                    self.assertIn('the first is from 5 Jul 2016, 05:49 UTC), so Show all times is now on', got['text'])
                elif how == 'moved':
                    self.assertEqual(got['time'], 4, 'the timeline did not move to 16 May')
                    self.assertIn('The timeline moved to 16 May 2020, 12:00 UTC', got['text'])
                else:
                    self.assertEqual(got['time'], t0, 'the timeline moved although new profiles were showing')
                    self.assertNotIn('timeline moved', got['text'])
        # the dives share one id: the third, opened as a click on its marker does, survives choosing Salinity
        key = self.js(f"""(()=>{{ const i=instrumentsNow().filter(x=>x.dataset==={json.dumps(added[-1])})[2];
            __ol.actions.openProfile(i.userData); return i.key; }})()""")
        self.click_control('.pill[data-v="sal"]')
        self.b.pump(0.3)
        self.assertEqual(self.js("[__ol.state.variable, profileState.inst && profileState.inst.key]"), ['sal', key],
                         'the panel went back to the first dive')
        self.b.click(f'#userDataList button[data-id="{added[-1]}"]')     # the glider turned Show all times on
        self.b.pump(0.3)
        self.assertFalse(self.js('__ol.state.showAllImported'), 'Show all times stayed on after the glider was removed')
        for i in added[:-1]:
            self.b.click(f'#userDataList button[data-id="{i}"]')
            self.b.pump(0.3)
        self.js("__ol.actions.setTime(0); __ol.actions.setVariable('temp')")
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
        # values at or below zero: no log range starting at or below 0, and they take the bottom colour
        g = self.js("(()=>{ const a=__ol.actions; a.setScale('linear'); a.setRange(-1, 40); a.setScale('log');"
                    " const min=colorbarSpec().min, refused=a.setRange(-1, 40);"
                    " return [min, refused, Render.rampPos(-5, 0.1, 10, 'log'), Render.rampPos(0, 0.1, 10, 'log')]; })()")
        self.assertGreater(g[0], 0, 'switching to log kept a minimum at or below 0')
        self.assertFalse(g[1], 'a log range starting below 0 was accepted')
        self.assertEqual(g[2:], [0, 0], 'values <= 0 must sit at the bottom of a log ramp')
        self.js("__ol.actions.setScale('linear'); __ol.actions.resetRange()")
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
