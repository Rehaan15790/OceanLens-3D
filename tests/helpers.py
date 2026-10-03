"""Shared fixtures for the browser tests: one headless browser per test class, the app opened in test mode.

The build under test is $OCEANLENS_BUILD, else dist/OceanLens3D.html, else the root OceanLens3D.html
(tools/run.py sets the variable from --build). Tests reach the app through window.__ol when the build provides it
and fall back to the older globals otherwise, so they run against every build since the baseline.
"""
import functools
import json
import os
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
OUTPUT = REPO / 'tests' / 'output'
sys.path.insert(0, str(REPO / 'tools'))

from cdp import Browser, CDPError, default_build, open_app  # noqa: E402

# JS expressions that work on both the contract (window.__ol) and older builds
STATE = '((window.__ol && __ol.state) || state)'
DATA_MODE = '((window.__ol && __ol.dataMode) || dataMode)'
TOUR_ON = "isTourRunning()"


def fn(name):
    """JS expression for a pure function: __ol.fn.<name> if exposed, else the global."""
    return f'((window.__ol && __ol.fn && __ol.fn.{name}) || {name})'


class Pending(unittest.SkipTest):
    """A known gap in this build: reported as an expected failure by tools/run.py, not as a pass or a failure."""


PRE_ACTIONS = '!(window.__ol && __ol.actions)'     # the build predates architect's action layer


def pending(reason, probe=PRE_ACTIONS):
    """Mark a test whose feature is scheduled but may not be in the build yet.

    While `probe` (JS) is true in the page, a failing assertion is reported as Pending(reason) instead of a
    failure. Once the feature lands the probe turns false and the test counts normally. The default probe is
    "this build predates architect's action layer" (window.__ol.actions, PLAN 1q)."""
    def deco(f):
        @functools.wraps(f)
        def wrapper(self, *a, **kw):
            try:
                gap = self.js(f'(()=>{{try{{return !!({probe})}}catch(e){{return true}}}})()')
            except CDPError:
                gap = True
            if not gap:
                return f(self, *a, **kw)
            try:
                f(self, *a, **kw)
            except (AssertionError, CDPError) as e:
                raise Pending(f'pending: {reason} [{str(e).strip().splitlines()[0][:140]}]')
        return wrapper
    return deco


class AppTestCase(unittest.TestCase):
    longMessage = False       # show our failure messages without the repr of the compared values
    width, height = 1600, 900
    enter = True
    data_mode = None          # 'real' | 'synth' to switch before the tests
    browser_args = ()

    @classmethod
    def setUpClass(cls):
        cls.b = Browser(width=cls.width, height=cls.height, args=cls.browser_args)
        try:
            cls.boot_state = open_app(cls.b, default_build(), enter=cls.enter)
            if cls.boot_state != 'ready' and cls.enter:
                raise unittest.SkipTest(f'app did not become ready (state {cls.boot_state})')
            if cls.data_mode:
                cls.b.click(f'#dataToggle button[data-mode="{cls.data_mode}"]')
                cls.b.pump(0.5)
        except Exception:
            cls.b.close()
            raise

    @classmethod
    def tearDownClass(cls):
        cls.b.close()

    def setUp(self):
        self.mark = self.b.mark()

    def js(self, expr, **kw):
        return self.b.eval(expr, **kw)

    def problems(self):
        """Console errors, warnings and exceptions since this test began, minus the offline-font noise
        that the network test reports on its own."""
        return [f"[{p['kind']}] {p['text'][:200]} @{p['where']}" for p in self.b.problems(since=self.mark)
                if not (p['kind'] == 'log:network' and 'ERR_NAME_NOT_RESOLVED' in p['text'])
                and 'GPU stall due to ReadPixels' not in p['text']]     # driver note when tests read pixels back

    def assertNoProblems(self):
        self.b.pump(0.15)
        probs = self.problems()
        self.assertFalse(probs, 'console problems:\n  ' + '\n  '.join(probs))

    def click_control(self, selector):
        """A real click on a control, opening the <details> menu that holds it first if it is closed."""
        closed = self.js(f"(()=>{{const e=document.querySelector({json.dumps(selector)}); const d=e && e.closest('details');"
                         f" return !!d && !d.open;}})()")
        if closed:
            self.b.click(f"details:has({selector}) > summary")
            self.b.pump(0.1)
        self.b.click(selector)

    def mark_points(self, kind):
        """Page coordinates of the visible pickable marks of one kind ('argo', 'glider', 'track')."""
        return self.js(f"__ol.render.marks('{kind}').map(m=>[m.x, m.y])")

    def screen_of(self, obj_expr):
        """Client coordinates of a three.js object, or None when it is off screen."""
        return self.js(f"""(()=>{{ const o={obj_expr}; if(!o) return null; const v=new THREE.Vector3();
            o.getWorldPosition(v); v.project(camera); const r=renderer.domElement.getBoundingClientRect();
            if(v.z>1||Math.abs(v.x)>1||Math.abs(v.y)>1) return null;
            return [r.left+(v.x*0.5+0.5)*r.width, r.top+(-v.y*0.5+0.5)*r.height]; }})()""")

    def visible_text_matching(self, pattern):
        """Text of the first visible element whose own text matches the regex, or None."""
        return self.js(f"""(()=>{{ const re=new RegExp({json.dumps(pattern)}, 'i');
            for (const e of document.querySelectorAll('body *')) {{
              if (e.children.length > 3) continue;
              const r = e.getBoundingClientRect(), cs = getComputedStyle(e);
              if (!r.width || !r.height || cs.visibility === 'hidden' || +cs.opacity === 0) continue;
              if (r.bottom < 0 || r.right < 0 || r.top > innerHeight || r.left > innerWidth) continue;
              if (re.test(e.textContent || '')) return (e.textContent || '').trim().slice(0, 160);
            }} return null; }})()""")


def output_dir(*parts):
    p = OUTPUT.joinpath(*parts)
    p.mkdir(parents=True, exist_ok=True)
    return p
