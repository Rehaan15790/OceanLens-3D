"""End-to-end smoke tests through real mouse and keyboard input, in both data modes, with a console gate:
any console error, warning or uncaught exception during a test fails it."""
import json
import unittest

from helpers import DATA_MODE, STATE, TOUR_ON, AppTestCase, output_dir, pending
from pngdiff import decode

VARIABLES = (('1', 'temp'), ('2', 'sal'), ('3', 'cur'), ('4', 'tchp'))
MODES = (('l', 'layers'), ('d', 'slice'), ('i', 'iso'))
KEY_RANGE = "document.getElementById('keyRange').textContent"
KEY_TITLE = "document.getElementById('keyTitle').textContent.replace(/\\u00a0/g, ' ')"
# what the key range should say: the spec the 3D view was coloured with
SPEC_RANGE = "(()=>{const c=colorbarSpec(); return `Colour range ${c.min} to ${c.max} ${c.unit}.`;})()"
# labels under the colour bar: [text, left edge, right edge, position along the bar 0-1, is a mark]
KEY_LABELS = """[...document.querySelectorAll('#keyTicks .key-tick')].map(e=>{ const r=e.getBoundingClientRect();
  return [e.textContent, r.left, r.right, parseFloat(e.style.left)/100, e.classList.contains('mark')]; })"""
# the PNG export's key painted on its own canvas: every text with its extent, the bar's columns, and where the
# spec's marks and ticks belong on the 260 px bar (bar from x 20 to 280)
EXPORT_KEY = """(()=>{ const c=document.createElement('canvas'); c.width=300; c.height=600; const g=c.getContext('2d'), s=keySpec();
  const out={text:[], bar:0, empty:!!s.empty}, ft=g.fillText.bind(g), fr=g.fillRect.bind(g), at=v=>20+rampT(v, s.min, s.max, s.scale||'linear')*260;
  g.fillText=(t,x,y)=>{ const w=g.measureText(t).width, l=g.textAlign==='center' ? x-w/2 : g.textAlign==='right' ? x-w : x;
    out.text.push([t, l, l+w, g.textAlign, g.font.startsWith('600')]); ft(t,x,y); };
  g.fillRect=(x,y,w,h)=>{ if(w===1 && h===12) out.bar++; fr(x,y,w,h); };
  paintKey(g, s, 10, 590, 280);
  out.marks=s.marks.map(m=>[m.text, at(m.v)]); out.ticks=s.ticks.map(t=>[t.text, at(t.v)]); out.ends=[s.ends.min, s.ends.max];
  return out; })()"""
# client coordinates of a point on the sea surface
SEA_POINT = """(()=>{ const v=new THREE.Vector3((%f-(LON0+LON1)/2)*SCALE_XZ, 0, -(%f-(LAT0+LAT1)/2)*SCALE_XZ).project(camera);
  const r=renderer.domElement.getBoundingClientRect(); return [r.left+(v.x*0.5+0.5)*r.width, r.top+(-v.y*0.5+0.5)*r.height]; })()"""


class Boot(AppTestCase):
    enter = False

    def test_boot_has_no_console_problems(self):
        self.mark = 0
        self.assertNoProblems()

    @pending('ux removes the Google Fonts link (PLAN 1k, stage 3.1)',
             probe="!!document.querySelector('link[href*=\"fonts.googleapis\"]')")
    def test_no_network_requests(self):
        ext = [r['url'][:100] for r in self.b.external_requests()]
        self.assertFalse(ext, f'the page requested {len(ext)} external URL(s): {ext}')


class Controls:
    """Mixed into one test class per data mode."""

    def press(self, selector):
        """A real click, then the update it asked for (normally on the next frame) is applied at once."""
        self.b.click(selector)
        self.js('__ol.flush()')
        self.b.pump(0.1)

    def pill(self, group, value):
        """A real click on a variable ('v') or view ('m') pill."""
        self.press(f'#{"varPills" if group == "v" else "modePills"} .pill[data-{group}="{value}"]')

    def visible(self, selector):
        return self.js(f"(()=>{{const e=document.querySelector({json.dumps(selector)}); if(!e) return false;"
                       f" const r=e.getBoundingClientRect(); return r.width>0 && r.height>0;}})()")

    def track(self, selector):
        """Left end, right end and middle height of a range input, scrolled into view."""
        return self.js(f"(()=>{{const e=document.querySelector({json.dumps(selector)}); e.scrollIntoView({{block:'nearest'}});"
                       f" const r=e.getBoundingClientRect(); return [r.left, r.right, r.top+r.height/2];}})()")

    def test_depth_slider_by_mouse(self):
        self.pill('v', 'temp')
        self.pill('m', 'slice')
        self.assertTrue(self.visible('#depthSlider'), 'no Depth slider in the Depth slice view')
        x0, x1, y = self.track('#depthSlider')
        self.b.click_at(x1 - 2, y)
        self.js('__ol.flush()')
        last = self.js('DEPTHS.length-1')
        self.assertEqual(self.js(f'{STATE}.depthIdx'), last, 'a click at the right end of the slider')
        self.assertIn(f"{self.js(f'DEPTHS[{last}]')} m", self.js(KEY_TITLE))
        self.b.drag(x1 - 2, y, x0 + 2, y)
        self.js('__ol.flush()')
        self.assertEqual(self.js(f'{STATE}.depthIdx'), 0, 'a drag to the left end of the slider')
        self.assertEqual(self.js("document.getElementById('roVar').textContent"), 'Temperature at 0 m')
        self.pill('m', 'surface')
        self.assertFalse(self.visible('#depthSlider'), 'the Depth slider shows outside the Depth slice view')
        self.assertNoProblems()

    def test_iso_value_controls_by_mouse(self):
        self.pill('v', 'temp')
        for mode in ('iso', 'iso3d'):
            with self.subTest(mode=mode):
                self.pill('m', mode)
                for sel in ('#isoSlider', '#isoPresets .preset[data-iso="20"]', '#isoPresets .preset[data-iso="26"]'):
                    self.assertTrue(self.visible(sel), f'{sel} is not shown in {mode}')
                self.press('#isoPresets .preset[data-iso="20"]')
                self.assertEqual(self.js(f'{STATE}.isoVal'), 20)
                self.assertEqual(self.js("document.getElementById('roVar').textContent"), 'Depth of 20.0 °C')
                x0, x1, y = self.track('#isoSlider')
                self.b.click_at(x1 - 2, y)
                self.js('__ol.flush()')
                self.assertGreater(self.js(f'{STATE}.isoVal'), 20, 'a click at the right end of the value slider')
                self.assertEqual(self.js(f'{STATE}.isoVal'), self.js("+document.getElementById('isoSlider').value"))
                self.press('#isoPresets .preset[data-iso="26"]')
        self.pill('m', 'surface')
        self.assertFalse(self.visible('#isoSlider'), 'the value slider shows outside the iso views')
        self.assertNoProblems()

    def test_key_follows_a_click_into_depth_of_a_value(self):
        self.pill('m', 'surface')
        self.pill('v', 'sal')
        self.pill('m', 'iso')
        self.assertEqual(self.js('colorbarSpec().palette'), 'depth')
        self.assertEqual(self.js(KEY_RANGE), self.js(SPEC_RANGE), 'the key right after a click into Depth of a value')
        self.pill('v', 'temp')          # the 35 PSU surface lies deeper than D26: a stale key shows its range
        self.assertEqual(self.js(KEY_RANGE), self.js(SPEC_RANGE), 'the key after a variable click in Depth of a value')
        self.assertEqual(self.js(KEY_TITLE), 'Depth of the D26 surface (m)')
        self.press('#isoPresets .preset[data-iso="20"]')
        self.assertEqual(self.js(KEY_RANGE), self.js(SPEC_RANGE), 'the key after the D20 preset')
        self.assertEqual(self.js(KEY_TITLE), 'Depth of the D20 surface (m)')
        self.press('#isoPresets .preset[data-iso="26"]')
        self.pill('m', 'surface')
        self.assertNoProblems()

    def test_isosurface_key_names_and_marks_the_value(self):
        for v, title, mark in (('temp', 'Temperature (°C) · 26.0 °C isosurface', '26.0 °C'),
                               ('sal', 'Salinity (PSU) · 35.00 PSU isosurface', '35.00 PSU')):
            with self.subTest(variable=v):
                self.pill('v', v)
                self.pill('m', 'iso3d')
                self.assertEqual(self.js(KEY_TITLE), title)
                marks = [t[0].replace('\u00a0', ' ') for t in self.js(KEY_LABELS) if t[4]]
                self.assertEqual(marks, [mark], 'the key marks the isosurface value')
        self.pill('v', 'temp')
        self.press('#isoPresets .preset[data-iso="20"]')
        self.assertEqual(self.js(KEY_TITLE), 'Temperature (°C) · 20.0 °C isosurface')
        self.assertIn('marked: 20.0', self.js(KEY_RANGE))
        # a value near an end of the bar: its bold label stays over the bar and the end label gives way
        bar = self.js("(()=>{const r=document.getElementById('keyBar').getBoundingClientRect(); return [r.left, r.right];})()")
        lo, hi = self.js('[colorbarSpec().min, colorbarSpec().max]')
        for at in (0.05, 0.95):
            with self.subTest(at=at):
                self.js(f'__ol.actions.setIso({round(lo + at * (hi - lo), 1)})')
                if self.js('colorbarSpec().empty'):
                    continue
                labels = self.js(KEY_LABELS)
                self.assertEqual(sum(1 for t in labels if t[4]), 1, f'the value is not marked: {labels}')
                spans = sorted(t[1:3] for t in labels)
                self.assertTrue(all(a[1] <= b[0] for a, b in zip(spans, spans[1:])), f'key labels overlap: {labels}')
                self.assertTrue(all(bar[0] - 0.5 <= t[1] and t[2] <= bar[1] + 0.5 for t in labels),
                                f'a key label stands past the bar {bar}: {labels}')
        self.press('#isoPresets .preset[data-iso="26"]')
        self.pill('m', 'surface')
        self.assertNoProblems()

    def test_exported_key_matches_the_screen(self):
        for v, m in (('temp', 'iso3d'), ('chl', 'iso3d'), ('chl', 'surface'), ('chl', 'iso'), ('tchp', 'surface')):
            with self.subTest(variable=v, view=m):
                self.pill('v', v)
                if v != 'tchp':
                    self.pill('m', m)
                k = self.js(EXPORT_KEY)
                # nothing past the box's 10 px padding: titles and notes wrap
                self.assertTrue(all(20 - 0.5 <= t[1] and t[2] <= 280 + 0.5 for t in k['text']),
                                f'text outside the exported key: {[t for t in k["text"] if t[1] < 19.5 or t[2] > 280.5]}')
                self.assertEqual(k['empty'], self.js("document.getElementById('keyBar').parentNode.hidden"))
                if k['empty']:
                    self.assertEqual(k['bar'], 0, 'an empty key paints a colour bar')
                    self.assertFalse([t for t in k['text'] if t[0] in [mk[0] for mk in k['marks']]], 'an empty key paints a mark')
                    continue
                self.assertEqual(k['bar'], 260)
                # marks and ticks where the screen puts them: on a log key too
                for text, x in k['marks']:
                    drawn = [t for t in k['text'] if t[0] == text and t[4]]
                    self.assertTrue(drawn and drawn[0][1] - 1 <= x <= drawn[0][2] + 1, f'mark {text} belongs at x {x:.0f}: {drawn}')
                for text, x in k['ticks']:
                    for t in k['text']:
                        if t[0] == text and t[3] == 'center' and not t[4]:
                            self.assertAlmostEqual((t[1] + t[2]) / 2, x, delta=1, msg=f'tick {text} belongs at x {x:.0f}')
                names = {*k['ends'], *(mk[0] for mk in k['marks']), *(tk[0] for tk in k['ticks'])}
                row = sorted(t[1:3] for t in k['text'] if t[0] in names)
                self.assertTrue(all(a[1] <= b[0] for a, b in zip(row, row[1:])), f'exported key labels overlap: {row}')
        self.pill('v', 'temp')
        self.pill('m', 'surface')
        self.assertNoProblems()

    def test_key_labels_show_their_real_values(self):
        self.pill('m', 'surface')
        for v in ('sal', 'cur'):
            with self.subTest(variable=v):
                self.pill('v', v)
                lo, hi = self.js('[colorbarSpec().min, colorbarSpec().max]')
                labels = self.js(KEY_LABELS)
                self.assertEqual(float(labels[0][0]), lo, 'the low end of the key')
                self.assertEqual(float(labels[1][0]), hi, 'the high end of the key')
                for text, _, _, at, mark in labels[2:]:
                    if not mark:
                        self.assertAlmostEqual(float(text), lo + at * (hi - lo), delta=(hi - lo) * 1e-3,
                                               msg=f'tick {text} sits at {at:.3f} of {lo}–{hi}')
                spans = sorted(t[1:3] for t in labels)
                self.assertTrue(all(a[1] <= b[0] for a, b in zip(spans, spans[1:])), f'key labels overlap: {labels}')
                title = self.js("document.getElementById('keyTitle').textContent")
                self.assertNotRegex(title, r'\d (m|°C|PSU|m/s)\b', 'a number and its unit can wrap apart')
        self.pill('v', 'temp')
        self.assertNoProblems()

    def test_every_ramp_is_in_the_ramp_menu(self):
        for v in ('temp', 'sal', 'cur', 'tchp', 'chl'):
            with self.subTest(variable=v):
                self.pill('v', v)
                sel = self.js("[document.getElementById('paletteSel').selectedIndex, document.getElementById('paletteSel').value]")
                self.assertGreaterEqual(sel[0], 0, f'the Ramp menu is blank for {v}')
                self.assertEqual(sel[1], self.js(f'{STATE}.palette'))
        hidden = self.js("[...document.getElementById('paletteSel').options].filter(o=>o.hidden).map(o=>o.value)")
        self.assertEqual(hidden, ['depth'])
        self.pill('v', 'temp')
        self.assertNoProblems()

    def test_heat_note_and_track_legend(self):
        self.pill('v', 'tchp')
        self.assertTrue(self.visible('#tchpNote'), 'no "What the map shows" note for cyclone heat potential')
        self.b.click('#trackDetailsBtn')
        self.b.pump(0.4)
        self.assertTrue(self.js("document.getElementById('trackPanel').classList.contains('show')"))
        self.b.key('Escape')
        self.pill('v', 'temp')
        self.assertFalse(self.visible('#tchpNote'))
        rows = "[...document.getElementById('keyLegend').children].some(e=>/track/i.test(e.textContent))"
        self.press('#swTrack')
        self.assertFalse(self.js(rows), 'the legend lists the track after it was switched off')
        self.press('#swTrack')
        self.assertTrue(self.js(rows), 'the legend leaves out the track after it was switched on')
        self.assertNoProblems()

    def test_tour_shows_the_controls_it_names(self):
        self.b.click('#presentBtn')
        self.b.pump(0.5)
        self.b.click('#tourNext')
        self.b.pump(0.5)
        self.assertIn('depth slider', self.js("document.getElementById('tourText').textContent"))
        self.assertTrue(self.visible('#depthSlider'), 'the tour names the depth slider, which is not shown')
        self.b.click('#tourNext')
        self.b.pump(0.5)
        self.assertEqual(self.js(KEY_RANGE), self.js(SPEC_RANGE), 'the key on the D26 step')
        self.b.click('#tourNext')
        self.b.pump(0.5)
        self.assertIn('arrows and streaks show direction', self.js("document.getElementById('tourText').textContent"))
        self.b.click('#tourStop')
        self.b.pump(0.5)
        self.pill('v', 'temp')
        self.pill('m', 'surface')
        self.assertNoProblems()

    def test_header_readout_is_never_cut(self):
        cut = """(()=>{ const ro=document.getElementById('readout').getBoundingClientRect();
          return [...document.querySelectorAll('#readout > span')].filter(e=>getComputedStyle(e).display!=='none')
            .filter(e=>{ const r=e.getBoundingClientRect(); return r.left < ro.left-0.5 || r.right > ro.right+0.5 ||
              e.scrollWidth > e.clientWidth+1; }).map(e=>e.id+': '+e.textContent); })()"""
        depth = self.js(f'{STATE}.depthIdx')
        self.js('__ol.actions.setDepth(DEPTHS.length-1)')         # the slice readout at 1000 m is the longest
        try:
            for w, h in ((1280, 720), (1366, 768), (1600, 900)):
                self.b.resize(w, h)
                self.b.pump(0.3)
                for v in ('temp', 'sal', 'cur', 'tchp', 'chl'):
                    self.pill('v', v)
                    for m in (('surface',) if v == 'tchp' else ('surface', 'slice', 'iso', 'iso3d')):
                        with self.subTest(width=w, variable=v, view=m):
                            if v != 'tchp':
                                self.pill('m', m)
                            self.assertEqual(self.js(cut), [], f'header readout cut off at {w}x{h}')
                            self.assertTrue(self.visible('#roRange'))
        finally:
            self.b.resize(self.width, self.height)
            self.b.pump(0.3)
            self.js(f'__ol.actions.setDepth({depth})')
        self.pill('v', 'temp')
        self.pill('m', 'surface')
        self.assertNoProblems()

    def test_data_mode(self):
        self.assertEqual(self.js(DATA_MODE), self.data_mode)

    def test_every_variable_and_display_mode(self):
        for vk, v in VARIABLES:
            for mk, m in MODES:
                with self.subTest(variable=v, mode=m):
                    self.b.key(vk)
                    self.b.key(mk)
                    self.b.pump(0.2)
                    self.assertEqual(self.js(f'{STATE}.variable'), v)
                    if v != 'tchp':
                        self.assertEqual(self.js(f'{STATE}.mode'), m)
                    readout = self.js("document.getElementById('roRange').textContent.trim()")
                    self.assertTrue(readout and readout != '–', f'empty readout for {v}/{m}')
        self.b.key('1')
        self.b.key('l')
        self.assertNoProblems()

    def test_depth_keys_reach_both_ends(self):
        self.b.key('1')
        self.b.key('d')
        for _ in range(12):
            self.b.key(']')
        self.assertEqual(self.js(f'{STATE}.depthIdx'), 10)
        for _ in range(12):
            self.b.key('[')
        self.assertEqual(self.js(f'{STATE}.depthIdx'), 0)
        self.b.key('l')
        self.assertNoProblems()

    def test_section_presets_and_drawing(self):
        for key in ('as15', 'bob15', 'track', 'n8'):
            with self.subTest(preset=key):
                self.click_control(f'#sectionPresets .preset[data-sec="{key}"]')
                self.b.pump(1.1)
                self.assertTrue(self.js("document.getElementById('sectionPanel').classList.contains('show')"))
        self.b.click('#closeSection')
        self.b.pump(1.0)
        self.b.key('s')
        cx, cy = self.b.center('#viewport')
        self.b.drag(cx - 250, cy - 20, cx + 150, cy + 30, steps=16)
        self.b.pump(0.6)
        self.assertTrue(self.js("document.getElementById('sectionPanel').classList.contains('show')"),
                        'a dragged section did not open the section panel')
        self.b.key('Escape')
        self.b.pump(1.0)
        self.assertNoProblems()

    def test_float_click_opens_profile(self):
        self.b.key('1')
        self.b.key('l')
        self.b.click('#resetViewBtn')
        self.b.pump(0.3)
        opened = False
        for pos in self.mark_points('argo')[:15]:
            if pos:
                self.b.click_at(*pos)
                self.b.pump(0.3)
                if self.js("document.getElementById('profile').classList.contains('show')"):
                    opened = True
                    break
        self.assertTrue(opened, 'no float marker opened the profile panel')
        # chlorophyll and oxygen tabs are hidden when the profile has no such data
        tabs = self.js("[...document.querySelectorAll('.tabbtn')].filter(b=>!b.hidden).map(b=>b.dataset.p)")
        self.assertIn('temp', tabs)
        for tab in [t for t in tabs if t != 'temp'] + ['temp']:
            self.b.click(f'.tabbtn[data-p="{tab}"]')
        self.b.key('Escape')
        self.b.pump(0.3)
        self.assertNoProblems()

    def test_track_fix_click_opens_track_panel(self):
        self.b.key('4')
        self.b.click('#resetViewBtn')
        self.b.pump(0.3)
        opened = False
        for pos in self.mark_points('track')[:30]:
            if pos:
                self.b.click_at(*pos)
                self.b.pump(0.3)
                if self.js("document.getElementById('trackPanel').classList.contains('show')"):
                    opened = True
                    break
        self.assertTrue(opened, 'no storm fix opened the track panel')
        self.b.key('Escape')
        self.b.key('1')
        self.b.pump(0.3)
        self.assertNoProblems()

    def test_timeline_and_play(self):
        self.js("(()=>{const t=document.getElementById('timeline'); t.value=7; t.dispatchEvent(new Event('input'));})()")
        self.assertEqual(self.js(f'{STATE}.time'), 7)
        self.b.key(' ')
        self.b.pump(2.4)
        self.b.key(' ')
        self.assertFalse(self.js(f'{STATE}.playing'))
        self.assertNotEqual(self.js(f'{STATE}.time'), 7, 'playback did not advance the time step')
        self.js("(()=>{const t=document.getElementById('timeline'); t.value=0; t.dispatchEvent(new Event('input'));})()")
        self.assertNoProblems()

    def test_overlay_switches(self):
        for sw in ('swArgo', 'swMisfit', 'swGlider', 'swFlow', 'swGrid'):
            self.b.click('#' + sw)
            self.b.click('#' + sw)
        self.assertNoProblems()

    def test_png_export(self):
        folder = output_dir('downloads', self.data_mode)
        for f in folder.glob('*.png'):
            f.unlink()
        self.b.allow_downloads(folder)
        self.b.click('#snapBtn')
        for _ in range(100):
            files = list(folder.glob('*.png'))
            if files:
                break
            self.b.pump(0.1)
        self.assertTrue(files, 'no PNG was saved')
        self.b.pump(0.3)
        w, h, _, _ = decode(files[0])
        cw, ch = self.js('[renderer.domElement.width, renderer.domElement.height]')
        self.assertEqual((w, h), (cw, ch))
        self.assertNoProblems()

    @pending('Ctrl/Alt/Meta guard in the hotkey handlers (architect, stage 3.1)')
    def test_ctrl_chords_are_left_to_the_browser(self):
        self.js("window.addEventListener('keydown', e=>{ window.__lastKey = e.defaultPrevented; })")
        look = (f"[{STATE}.variable, {STATE}.mode, !!{STATE}.sectionDraw,"
                f" {TOUR_ON}].join()")
        reset = ("if (isTourRunning()) __ol.actions.stopTour({restore:true});"
                 "if (typeof setSectionDraw === 'function') setSectionDraw(false);")
        wrong = []
        for k in ('p', 's', 'd', 'l', '2'):
            self.b.key('1')
            self.b.key('i' if k == 'l' else 'l')
            before = self.js(look)
            self.b.key(k, modifiers=2)
            self.b.pump(0.2)
            after, prevented = self.js(look), self.js('__lastKey')
            if prevented or after != before:
                wrong.append(f'Ctrl+{k.upper()}: ' + ', '.join(
                    (['browser default prevented'] if prevented else []) +
                    ([f'app state {before} -> {after} (variable, mode, drawing, tour)'] if after != before else [])))
            self.js(reset)
        self.b.key('1')
        self.b.key('l')
        self.assertFalse(wrong, 'app hotkeys fire on Ctrl chords:\n' + '\n'.join(wrong))

    def test_help_and_about(self):
        self.b.click('#helpBtn')
        self.assertTrue(self.js("document.getElementById('helpOverlay').classList.contains('show')"))
        self.b.key('Escape')
        self.b.click('#aboutBtn')
        self.assertTrue(self.js("document.getElementById('aboutOverlay').classList.contains('show')"))
        self.b.key('Escape')
        self.assertFalse(self.js("document.getElementById('aboutOverlay').classList.contains('show')"))
        self.assertNoProblems()


class RealMode(Controls, AppTestCase):
    data_mode = 'real'

    def test_chlorophyll_iso_views_have_an_empty_key(self):
        self.pill('v', 'chl')
        self.pill('m', 'iso')
        self.assertEqual(self.js(KEY_TITLE), 'Depth of the 0.20 mg/m³ surface (m)')
        self.assertFalse(self.visible('#keyBar'), 'a colour bar for a view with nothing coloured')
        self.assertEqual(self.js(KEY_LABELS), [])
        self.assertIn('no model field', self.js("document.getElementById('keyNote').textContent"))
        self.assertEqual(self.js("document.getElementById('roRange').textContent"), 'No model field')
        fields = "['cbMin', 'cbMax', 'paletteSel'].map(id=>[document.getElementById(id).value, document.getElementById(id).disabled])"
        self.assertEqual([f[1] for f in self.js(fields)], [True] * 3, 'the colour scale can be edited with nothing coloured')
        self.pill('m', 'iso3d')
        self.assertEqual(self.js(KEY_TITLE), 'Chlorophyll (mg/m³) · 0.20 mg/m³ isosurface')
        self.assertFalse(self.visible('#keyBar'), 'a colour bar for an isosurface that is not drawn')
        note = self.js("document.getElementById('keyNote').textContent")
        self.assertIn('no model field', note)
        self.assertNotIn('Log colour scale', note, 'the note describes a colour bar that is not drawn')
        self.assertEqual(self.js(fields)[:2], [['', True], ['', True]], 'the colour range of an isosurface that is not drawn')
        self.pill('v', 'temp')
        self.pill('m', 'surface')
        self.assertTrue(self.visible('#keyBar'))
        self.assertEqual([f[1] for f in self.js(fields)], [False] * 3)
        self.assertNoProblems()

    def test_bgc_profile_tabs_can_be_clicked(self):
        # the time step whose snapshot holds BGC float ARGO-2902174, and its marker on screen
        find = """(()=>{ for(let t=0; t<META.times.length; t++){ __ol.actions.setTime(t);
          const m=__ol.render.marks('argo').find(m=>m.id==='ARGO-2902174'); if(m) return [m.x, m.y]; } return null; })()"""
        tab_hit = """(()=>{ const e=document.querySelector('.tabbtn[data-p="%s"]'), r=e.getBoundingClientRect();
          return document.elementFromPoint(r.left+r.width/2, r.top+r.height/2)===e && r.right<=innerWidth; })()"""
        self.pill('v', 'temp')
        self.pill('m', 'surface')
        try:
            for w, h in ((1366, 768), (1600, 900)):
                with self.subTest(width=w):
                    self.b.resize(w, h)
                    self.b.pump(0.3)
                    self.b.click('#resetViewBtn')
                    self.b.pump(0.3)
                    at = self.js(find)
                    self.assertTrue(at, 'BGC float ARGO-2902174 is not on screen in any snapshot')
                    self.assertTrue(self.js("document.elementFromPoint(%f, %f)===renderer.domElement" % tuple(at)),
                                    'the float marker is covered')
                    self.b.click_at(*at)
                    self.b.pump(0.4)
                    self.assertIn('2902174', self.js("document.getElementById('profName').textContent"),
                                  'a click on the marker did not open its profile')
                    for tab in ('chl', 'oxy', 'temp'):
                        self.assertTrue(self.js(tab_hit % tab), f'the {tab} tab is off screen or covered at {w} px')
                        self.b.click(f'.tabbtn[data-p="{tab}"]')
                        self.assertEqual(self.js(f"document.querySelector('.tabbtn[data-p={tab}]').getAttribute('aria-selected')"),
                                         'true', f'a click on the {tab} tab')
                    self.b.key('Escape')
                    self.b.pump(0.3)
        finally:
            self.b.resize(self.width, self.height)
            self.js("__ol.actions.setTime(0)")
            self.b.pump(0.3)
        self.assertNoProblems()

    def test_chlorophyll_hover_has_no_model_value(self):
        self.pill('v', 'chl')
        self.b.click('#resetViewBtn')
        self.b.pump(0.3)
        for m in ('surface', 'iso', 'iso3d'):
            with self.subTest(view=m):
                self.pill('m', m)
                tips = []
                for lat, lon in ((8, 86), (12, 66), (16, 88)):
                    self.b.mouse('mouseMoved', *self.js(SEA_POINT % (lon, lat)))
                    self.b.pump(0.2)
                    tips.append(self.js("document.getElementById('hoverTip').innerText"))
                self.assertTrue(any('No model field' in t for t in tips), f'hover over the sea: {tips}')
                self.assertFalse([t for t in tips if 'NaN' in t or 'crossing' in t], f'hover over the sea: {tips}')
        self.b.mouse('mouseMoved', 5, 300)
        self.pill('v', 'temp')
        self.pill('m', 'surface')
        self.assertNoProblems()


class SyntheticMode(Controls, AppTestCase):
    data_mode = 'synth'


if __name__ == '__main__':
    unittest.main()
