"""End-to-end smoke tests through real mouse and keyboard input, in both data modes, with a console gate:
any console error, warning or uncaught exception during a test fails it."""
import unittest

from helpers import DATA_MODE, STATE, TOUR_ON, AppTestCase, output_dir, pending
from pngdiff import decode

VARIABLES = (('1', 'temp'), ('2', 'sal'), ('3', 'cur'), ('4', 'tchp'))
MODES = (('l', 'layers'), ('d', 'slice'), ('i', 'iso'))


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
        for tab in ('sal', 'dens', 'chl', 'temp'):
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


class SyntheticMode(Controls, AppTestCase):
    data_mode = 'synth'


if __name__ == '__main__':
    unittest.main()
