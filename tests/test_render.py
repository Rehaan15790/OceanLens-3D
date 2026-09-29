"""Rendering correctness through graphics' test hooks (window.__ol.render): the colours drawn on an opaque surface
match the colour bar, and profiling stems are drawn through the translucent layers."""
import unittest

from helpers import STATE, AppTestCase, pending

HOOKS = '!(window.__ol && __ol.render && __ol.render.pixelsAt)'
PENDING = 'render test hooks (graphics, render.js, stage 3.1)'

# 30 water nodes at least one grid step from land and with a sea floor below 150 m
NODES = """(()=>{ const out=[];
  for (let idx=0; idx<gridPts.length && out.length<30; idx+=37) {
    const p=gridPts[idx]; if (p.i<1 || p.j<1 || p.i>LAT_N-2 || p.j>LON_N-2 || gridLand[idx] || gridSeabed[idx] < 150) continue;
    const near=[idx-1, idx+1, idx-LON_N, idx+LON_N];
    if (near.some(k=>k<0 || k>=gridPts.length || gridLand[k] || gridSeabed[k] < 150)) continue;
    out.push([p.lat, p.lon, 100]); }
  return out; })()"""


class ColourMatch(AppTestCase):
    @pending(PENDING, probe=HOOKS)
    def test_depth_slice_matches_the_colour_bar(self):
        self.b.key('1')
        self.b.key('d')
        self.b.pump(0.3)
        self.assertEqual(self.js(f'{STATE}.depthIdx'), 4, 'expected the 100 m slice')
        bad = self.js(f"""(()=>{{ const r=__ol.render, s={STATE}, bad=[];
          const hits = r.pixelsAt({NODES}, {{isolate:true}});
          for (const h of hits) {{
            if (!h.inView || !h.rgb) continue;
            const v = fieldTemp(h.at.lat, h.at.lon, 100, s.time);
            const want = r.expectedColour(s.palette, v, s.cbMin, s.cbMax);
            const d = Math.max(...h.rgb.map((c,i)=>Math.abs(c-want[i])));
            if (d > 2) bad.push(`${{h.at.lat.toFixed(2)}},${{h.at.lon.toFixed(2)}}: drawn ${{h.rgb}}, bar ${{want}}`);
          }}
          return {{n: hits.filter(h=>h.inView).length, bad}}; }})()""")
        self.assertGreaterEqual(bad['n'], 20, f"only {bad['n']} test points were on screen")
        self.assertFalse(bad['bad'], 'colours off the bar by more than 2/255:\n' + '\n'.join(bad['bad'][:10]))
        self.b.key('l')


class StemsVisible(AppTestCase):
    data_mode = 'real'

    @pending(PENDING, probe=HOOKS)
    def test_profiling_stems_show_through_the_layers(self):
        self.b.key('1')
        self.b.key('l')
        self.b.pump(0.3)
        diff = self.js("""(()=>{ const r=__ol.render;
          const id = r.marks('argo')[0].id, inst = instrumentsNow().find(i=>i.id===id);
          const pt = [[inst.lat, inst.lon, 200]];
          r.toggle('stems', true); const a = r.pixelsAt(pt)[0];
          r.toggle('stems', false); const b = r.pixelsAt(pt)[0]; r.toggle('stems', true);
          return a.inView && b.inView ? Math.max(...a.rgb.map((c,i)=>Math.abs(c-b.rgb[i]))) : null; })()""")
        self.assertIsNotNone(diff, 'the test point was off screen')
        self.assertGreaterEqual(diff, 3, 'hiding the stems changed nothing: they are not drawn below the surface')


if __name__ == '__main__':
    unittest.main()
