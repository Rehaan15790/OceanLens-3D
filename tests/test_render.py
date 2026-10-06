"""Rendering correctness through graphics' test hooks (window.__ol.render): the colours drawn on an opaque surface
match the colour bar, profiling stems are drawn through the translucent layers, and the look of the 3D view:
whole float marks, the grid on the heat map, visible current streaks and arrows, unfogged land, a seabed closed
below the sea but open below India's long coast, and a shaded 3D isosurface."""
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


# pixels whose colour changes by more than `over` when the named group is hidden (with `darker`, only those the group
# darkens), over the whole canvas (one render each)
CHANGED = """((name, over, hide, darker)=>{ const t = __ol.render.toggle, off = (hide || []).filter(g=>t(g, false));
  const grab = ()=>{ const c = Render.renderForExport(); return c.getContext('2d').getImageData(0, 0, c.width, c.height).data; };
  t(name, true); const a = grab(); t(name, false); const b = grab(); t(name, true); off.forEach(g=>t(g, true));
  let n = 0;
  for (let i=0; i<a.length; i+=4) if ((darker ? Math.max(b[i]-a[i], b[i+1]-a[i+1], b[i+2]-a[i+2])
                                             : Math.max(Math.abs(a[i]-b[i]), Math.abs(a[i+1]-b[i+1]), Math.abs(a[i+2]-b[i+2]))) > over) n++;
  return n; })"""

# rows 1-3 px below the centre of up to 8 Argo marks with no other mark nearby, from one render
MARK_ROWS = """(()=>{ const r = renderer.domElement.getBoundingClientRect(), ms = __ol.render.marks('argo');
  const lone = ms.filter(m=>ms.every(o=>o===m || Math.hypot(o.x-m.x, o.y-m.y) > 20)).slice(0, 8), pts = [];
  lone.forEach(m=>{ for (let dy=1; dy<=3; dy++) pts.push([m.x - r.left, m.y - r.top + dy]); });
  const px = __ol.render.pixelsAtScreen(pts);
  return lone.map((m, k)=>({id: m.id, rows: px.slice(k*3, k*3+3).map(p=>p.rgb)})); })()"""


class Look3D(AppTestCase):
    """What a mouse user sees in the 3D view, reached through the app's own controls."""
    data_mode = 'real'

    def pill(self, group, value):
        attr = 'v' if group == 'varPills' else 'm'
        self.b.click(f'#{group} .pill[data-{attr}="{value}"]')
        self.b.pump(0.4)
        self.js('__ol.flush()')

    def home(self):
        self.pill('varPills', 'temp')
        self.pill('modePills', 'surface')
        self.b.click('#resetViewBtn')
        self.b.pump(0.3)

    def set_stretch(self, tenths):
        """The Depth stretch slider, as dragging it does."""
        self.click_control('#exSlider')
        self.js(f"(()=>{{ const e=document.querySelector('#exSlider'); e.value='{tenths}';"
                " e.dispatchEvent(new Event('input', {bubbles:true})); })()")
        self.b.pump(0.3)
        self.js('__ol.flush()')

    def test_float_marks_are_whole_on_the_sea_and_the_heat_map(self):
        self.home()
        for var in ('temp', 'tchp'):
            self.pill('varPills', var)
            marks = self.js(MARK_ROWS)
            self.assertGreaterEqual(len(marks), 5, f'only {len(marks)} separate Argo marks on screen')
            cut = [f"{m['id']}: {m['rows']}" for m in marks
                   if not all(p and min(p) >= 200 and max(p) - min(p) <= 16 for p in m['rows'])]
            self.assertFalse(cut, f'{var}: the surface shows through the lower half of these marks '
                                  '(rows 1-3 px below the centre):\n' + '\n'.join(cut))
        self.home()
        self.assertNoProblems()

    def test_grid_shows_on_the_heat_potential_map_but_not_on_land(self):
        self.home()
        self.pill('varPills', 'tchp')
        n = self.js(CHANGED + "('graticule', 24)")
        self.assertGreater(n, 3000, f'hiding the grid changed only {n} px in Cyclone heat potential: the map covers it')
        land = self.js("""(()=>{ const t = __ol.render.toggle, p = [[20, 77.5, 0], [17.5, 76, 0]];
          const a = __ol.render.pixelsAt(p); t('graticule', false); const b = __ol.render.pixelsAt(p); t('graticule', true);
          return a.map((h, i)=>Math.max(...h.rgb.map((c, k)=>Math.abs(c - b[i].rgb[k])))); })()""")
        self.assertLessEqual(max(land), 2, f'grid lines are drawn over India ({land})')
        self.home()

    def test_current_streaks_and_arrows_stand_out_from_the_field(self):
        self.home()
        self.pill('varPills', 'cur')
        n = self.js(CHANGED + "('particles', 60)")
        self.assertGreater(n, 1500, f'the current streaks change only {n} px by more than 60/255: they take the field colours')
        self.pill('modePills', 'slice')
        n = self.js(CHANGED + "('arrows', 60, ['particles'])")
        self.assertGreater(n, 3000, f'the slice arrows change only {n} px by more than 60/255')
        n = self.js(CHANGED + "('arrows', 60, ['particles'], true)")
        self.assertGreater(n, 800, f'the slice arrows darken only {n} px by more than 60/255: they have no dark edge '
                                   'against the light end of the palette')
        self.home()
        self.assertNoProblems()

    def test_land_keeps_its_colour_when_the_view_is_pulled_back(self):
        india = "__ol.render.pixelsAt([[18, 77, 0]])[0].rgb"
        self.home()
        at_home = self.js(india)
        self.set_stretch(80)
        self.b.click('#resetViewBtn')
        self.b.pump(0.3)
        reset = self.js(india)
        x, y = self.screen_of('floorMesh')
        for _ in range(3):                  # 6 units each: past the zoom-out limit
            self.b.wheel(x, y, 500)
        self.b.pump(0.3)
        far = self.js(india)
        self.set_stretch(30)
        self.home()
        for name, rgb in (('8x after Reset view', reset), ('8x zoomed out', far)):
            self.assertIsNotNone(rgb, f'India is off screen at {name}')
            self.assertLessEqual(max(abs(c - h) for c, h in zip(rgb, at_home)), 25,
                                 f'India is {rgb} at {name} against {at_home} at the home view: fogged dark')
        self.assertNoProblems()

    def test_seabed_is_closed_below_sri_lanka(self):
        self.home()
        rgb = self.js("""(()=>{ const [x, z] = toXZ(LAT0, 80.6), v = new THREE.Vector3(x, -3, z).project(camera), cv = renderer.domElement;
          return __ol.render.pixelAtScreen((v.x*0.5 + 0.5)*cv.clientWidth, (-v.y*0.5 + 0.5)*cv.clientHeight).rgb; })()""")
        self.assertIsNotNone(rgb, 'the south face below Sri Lanka is off screen')
        self.assertGreater(max(abs(c - b) for c, b in zip(rgb, (6, 14, 26))), 15,
                           f'below Sri Lanka the box is open: {rgb} is the backdrop')

    def test_basins_show_below_india_from_the_north(self):
        self.home()
        self.pill('modePills', 'layers')
        x, y = self.js("(()=>{ const r = renderer.domElement.getBoundingClientRect(); return [r.left + r.width/2, r.top + r.height/2]; })()")
        self.b.drag(x, y, x - 480, y, steps=20)          # orbit round to the north
        self.b.pump(0.3)
        az = self.js('camAz')
        # points on the north side below India's coast: the layers behind them show through, as below any coast
        n = self.js("""(()=>{ const pts = [];
          for (let lon=74; lon<=86; lon++) for (let d=100; d<=900; d+=100) pts.push([LAT1, lon, d]);
          const a = __ol.render.pixelsAt(pts), h = __ol.render.pixelsAt(pts, {hide:['layers']});
          return a.filter((p, k)=>p.rgb && h[k].rgb && Math.max(...p.rgb.map((c, j)=>Math.abs(c - h[k].rgb[j]))) > 8).length; })()""")
        self.home()
        self.assertGreater(az, 2.8, f'the drag turned the view only to azimuth {az:.2f}')
        self.assertGreater(n, 20, f"from the north the layers show at only {n} of 117 points below India's coast: "
                                  'a wall hides the basins')
        self.assertNoProblems()

    def test_isosurface_3d_is_shaded_by_its_relief(self):
        self.home()
        self.pill('modePills', 'iso3d')
        flat = self.iso3d_shading()
        self.pill('varPills', 'sal')          # 35 PSU: walls and sheets
        steep = self.iso3d_shading()
        self.home()
        for name, shade, spread in (('26 °C', flat, 0.1), ('35 PSU', steep, 0.3)):
            self.assertGreater(shade['n'], 20000, f"the {name} isosurface covers only {shade['n']} px")
            self.assertGreater(shade['p95'] - shade['p5'], spread, f"{name}: shading spans only "
                               f"{shade['p5']:.3f}-{shade['p95']:.3f} of the key colour: the sheet reads flat")
            self.assertLessEqual(shade['p95'], 1.02, f"{name}: the sheet is brighter than the key colour ({shade['p95']:.3f})")

    def iso3d_shading(self):
        """p5 and p95 of the isosurface's pixels as a fraction of its key colour."""
        return self.js("""(()=>{ const s = __ol.state, t = __ol.render.toggle;
          const key = __ol.render.expectedColour(s.palette, s.isoVal, s.cbMin, s.cbMax, s.scale), ks = key[0] + key[1] + key[2];
          const off = ['graticule', 'coast', 'stems', 'markers', 'track', 'box', 'land'].filter(g=>t(g, false));
          const grab = ()=>{ const c = Render.renderForExport(); return c.getContext('2d').getImageData(0, 0, c.width, c.height).data; };
          const a = grab(); t('iso3d', false); const b = grab(); t('iso3d', true); off.forEach(g=>t(g, true));
          const r = [];
          for (let i=0; i<a.length; i+=4) if (a[i] !== b[i] || a[i+1] !== b[i+1] || a[i+2] !== b[i+2]) r.push((a[i] + a[i+1] + a[i+2])/ks);
          r.sort((p, q)=>p - q);
          return {n: r.length, p5: r[Math.floor(r.length*0.05)], p95: r[Math.floor(r.length*0.95)]}; })()""")


if __name__ == '__main__':
    unittest.main()
