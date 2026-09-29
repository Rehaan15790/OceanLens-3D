"""Window sizes: screenshots into tests/output/sizes/ plus layout checks at each size.

Sizes from the plan: common laptops and projectors, 1536x864 (a 1920x1080 panel at 125 %), a DPR 1.5 case, and
a phone, where the "built for laptop and desktop" notice must show.
"""
import unittest

from helpers import AppTestCase, output_dir, pending

SIZES = [(1024, 768, 1), (1280, 720, 1), (1366, 768, 1), (1536, 864, 1), (1920, 1080, 1), (1280, 720, 1.5)]
LAYOUT = """(()=>{ const c=renderer.domElement, v=document.getElementById('viewport'), hr=document.getElementById('headRight');
  const key = document.querySelector('[data-test="colour-key"]') || document.getElementById('colorbarCanvas');
  const kr = key ? key.getBoundingClientRect() : null;
  return {viewport:[v.clientWidth, v.clientHeight], canvas:[c.clientWidth, c.clientHeight],
    header: hr ? hr.getBoundingClientRect().right <= innerWidth + 1 : true,
    overflow: document.documentElement.scrollWidth > innerWidth || document.documentElement.scrollHeight > innerHeight,
    keyVisible: !!kr && kr.width>0 && kr.top >= 0 && kr.bottom <= innerHeight && kr.right <= innerWidth}; })()"""


class Sizes(AppTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        folder = output_dir('sizes')
        cls.layout = {}
        for w, h, dpr in SIZES:
            cls.b.resize(w, h, dpr)
            cls.b.pump(0.6)
            cls.b.screenshot(folder / f'{w}x{h}@{dpr}.png')
            cls.layout[f'{w}x{h}@{dpr}'] = cls.b.eval(LAYOUT)
        cls.b.resize(1600, 900)

    def test_canvas_header_and_scrolling(self):
        bad = []
        for size, r in self.layout.items():
            if r['viewport'] != r['canvas']:
                bad.append(f"{size}: canvas {r['canvas']} does not fill the viewport {r['viewport']}")
            if not r['header']:
                bad.append(f'{size}: header controls run off the right edge')
            if r['overflow']:
                bad.append(f'{size}: the page scrolls')
        self.assertFalse(bad, '\n'.join(bad))

    @pending('colour key in the viewport (ux, stage 3.1)', probe="!document.querySelector('[data-test=colour-key]')")
    def test_colour_key_visible_without_scrolling(self):
        hidden = [size for size, r in self.layout.items() if not r['keyVisible']]
        self.assertFalse(hidden, 'colour key not fully visible at ' + ', '.join(hidden))

    @pending('small-screen notice (ux, stage 3.1)', probe="!document.getElementById('smallScreen')")
    def test_phone_shows_the_small_screen_notice(self):
        shown = "(()=>{const e=document.getElementById('smallScreen'); return !!e && !e.hidden && e.getBoundingClientRect().width>0;})()"
        self.b.resize(390, 844, 1)
        self.b.pump(0.6)
        self.b.screenshot(output_dir('sizes') / '390x844@1.png')
        self.assertTrue(self.visible_text_matching(r'laptop and desktop'), 'no "built for laptop and desktop" notice at 390x844')
        self.b.click('#smallScreenContinue')
        self.b.pump(0.3)
        self.assertFalse(self.js(shown), '"Continue anyway" did not close the notice')
        self.b.resize(800, 600, 1)
        self.b.pump(0.3)
        self.b.resize(390, 844, 1)
        self.b.pump(0.3)
        self.assertFalse(self.js(shown), 'the notice came back after "Continue anyway"')
        self.b.resize(1600, 900)


if __name__ == '__main__':
    unittest.main()
