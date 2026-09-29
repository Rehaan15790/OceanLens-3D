"""The splash and the boot guard's status surface: the loading state, ready, the version stamp."""
import unittest

from helpers import REPO, AppTestCase, Browser, default_build, open_app

# Recorded just after DOMContentLoaded, before the app can have marked itself ready.
AT_DOM = r"""document.addEventListener('DOMContentLoaded', () => setTimeout(() => {
  const b = document.getElementById('enterBtn');
  window.__splashAtDom = {state: document.documentElement.dataset.state, disabled: !!(b && b.disabled),
                          label: b ? b.textContent.trim() : null};
}, 0));"""


class Splash(AppTestCase):
    enter = False

    def test_ready_state_and_buttons(self):
        self.assertEqual(self.js('document.documentElement.dataset.state'), 'ready')
        self.assertEqual(self.js("window.__ol && __ol.status"), 'ready')
        b = self.js("(()=>{const b=document.getElementById('enterBtn'); return [b.disabled, b.textContent.trim()];})()")
        self.assertEqual(b, [False, 'Open the viewer'])

    def test_version_is_stamped(self):
        version = (REPO / 'VERSION').read_text(encoding='utf-8').strip()
        self.assertEqual(self.js("document.querySelector('meta[name=version]').content"), version)
        self.assertEqual(self.js('__ol.version'), version)
        if not self.js("!!document.getElementById('aboutBuild')"):
            self.skipTest('this build has no #aboutBuild line in the About panel')
        about = self.js("(document.getElementById('aboutVersion')||{}).textContent || ''")
        self.assertIn(version, about, 'the About panel does not show the version')

    def test_ready_time_recorded(self):
        self.assertIsInstance(self.js('__ol.readyAt'), (int, float))


class SplashWhileLoading(unittest.TestCase):
    def test_buttons_wait_for_the_data(self):
        with Browser() as b:
            b.send('Page.addScriptToEvaluateOnNewDocument', source=AT_DOM)
            b.send('Emulation.setCPUThrottlingRate', rate=4)
            open_app(b, default_build(), enter=False)
            seen = b.eval('window.__splashAtDom || null')
        self.assertIsNotNone(seen, 'DOMContentLoaded probe did not run')
        if seen['state'] != 'loading':
            self.skipTest(f"the app was already {seen['state']} at DOMContentLoaded; the loading state was not observable")
        self.assertTrue(seen['disabled'], 'ENTER was clickable before the app was ready')
        self.assertEqual(seen['label'], 'Loading data…')


if __name__ == '__main__':
    unittest.main()
