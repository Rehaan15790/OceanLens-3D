"""The guided tour: every step in both data modes, and the timer races found in round 1."""
import unittest

from helpers import STATE, TOUR_ON, AppTestCase, pending

CAPTION_SHOWN = "!document.getElementById('tourStrip').hidden"
STEP_TEXT = "document.getElementById('tourStep').textContent"
SECTION_ON = "document.getElementById('sectionPanel').classList.contains('show') || (typeof section!=='undefined' && section!==null)"


class TourSteps:
    def test_every_step(self):
        self.b.key('p')
        self.b.pump(0.4)
        self.assertTrue(self.js(TOUR_ON), 'P did not start the tour')
        seen = []
        for i in range(8):
            self.b.pump(2.0)
            seen.append(self.js(STEP_TEXT))
            self.b.key('ArrowRight')
        self.b.key('Escape')
        self.b.pump(0.5)
        self.assertFalse(self.js(TOUR_ON), 'Esc did not stop the tour')
        self.assertEqual(len(set(seen)), 8, f'expected 8 distinct steps, saw {seen}')
        self.assertNoProblems()


class RealTour(TourSteps, AppTestCase):
    data_mode = 'real'


class SyntheticTour(TourSteps, AppTestCase):
    data_mode = 'synth'


class Captions(AppTestCase):
    @pending('tour captions of 35 words or fewer (ux, tour-steps.js, stage 3.1)',
             probe="!document.getElementById('tourStrip')")
    def test_captions_are_35_words_or_fewer(self):
        long = []
        for mode in ('real', 'synth'):
            self.b.click(f'#dataToggle button[data-mode="{mode}"]')
            self.b.pump(0.3)
            words = self.js(r"tourSteps.map(s => String(s.caption).trim().split(/\s+/).length)")
            long += [f'{mode} step {i + 1}: {n} words' for i, n in enumerate(words) if n > 35]
        self.assertFalse(long, 'captions over 35 words:\n' + '\n'.join(long))


class TourRaces(AppTestCase):
    """Delayed work scheduled by a step must not run after the step (or the tour) has ended."""
    data_mode = 'real'

    def tearDown(self):
        self.b.key('Escape')
        self.b.pump(0.3)
        self.b.key('Escape')
        self.b.key('Escape')
        self.b.pump(1.0)

    @pending('tour timer tokens, playback pause on start (architect, tour.js, stage 3.1)')
    def test_stopping_at_once_hides_the_caption(self):
        self.b.key('p')
        self.b.key('Escape')
        self.b.pump(0.8)
        self.assertFalse(self.js(TOUR_ON))
        self.assertFalse(self.js(CAPTION_SHOWN), 'the caption came back after the tour stopped')

    @pending('tour timer tokens, playback pause on start (architect, tour.js, stage 3.1)')
    def test_skipping_the_section_step_leaves_no_section(self):
        self.b.key('p')
        self.b.pump(0.3)
        for _ in range(7):                   # to step 7 (the section) and straight on to step 8
            self.b.key('ArrowRight')
        self.b.pump(1.8)
        self.assertFalse(self.js(SECTION_ON), 'the section from step 7 was drawn during step 8')

    @pending('tour timer tokens, playback pause on start (architect, tour.js, stage 3.1)')
    def test_stopping_before_a_panel_opens_keeps_it_closed(self):
        self.b.key('p')
        self.b.pump(0.3)
        for _ in range(7):
            self.b.key('ArrowRight')         # step 8 opens a float profile after a delay
        self.b.key('Escape')
        self.b.pump(1.8)
        self.assertFalse(self.js("document.getElementById('profile').classList.contains('show')"),
                         'the profile panel opened after the tour was stopped')

    @pending('tour timer tokens, playback pause on start (architect, tour.js, stage 3.1)')
    def test_tour_pauses_playback(self):
        self.b.key(' ')
        self.b.pump(0.2)
        self.b.key('p')
        self.b.pump(0.6)
        self.assertFalse(self.js(f'{STATE}.playing'), 'the timeline kept playing during the tour')


if __name__ == '__main__':
    unittest.main()
