"""Load budgets over file://, the pen-drive case. Timings are machine-dependent, so the budgets are loose and the
suite should run on its own (not in parallel with other browser work)."""
import statistics
import unittest

from helpers import Browser, Pending, default_build, open_app

TIMES = """(()=>{ const contract = !!window.__ol; const p=Object.fromEntries(performance.getEntriesByType('paint').map(e=>[e.name,e.startTime]));
  const n=performance.getEntriesByType('navigation')[0];
  const ready = (window.__ol && __ol.readyAt) || n.domInteractive;
  return {fp: p['first-paint'] ?? null, ready, load: n.loadEventEnd, contract}; })()"""


def measure(throttle=1):
    with Browser() as b:
        if throttle > 1:
            b.send('Emulation.setCPUThrottlingRate', rate=throttle)
        open_app(b, default_build(), enter=False)
        b.pump(0.5)
        return b.eval(TIMES)


class LoadBudget(unittest.TestCase):
    def test_splash_paints_before_boot_finishes(self):
        runs = [measure() for _ in range(3)]
        early = sum(1 for r in runs if r['fp'] is not None and r['fp'] < r['ready'])
        detail = ', '.join(f"paint {r['fp'] or 0:.0f} ms / ready {r['ready']:.0f} ms" for r in runs)
        if early < 2 and not runs[0]['contract']:
            raise Pending(f'pending: splash before boot, measured again after the split (PLAN 1p) [{detail}]')
        self.assertGreaterEqual(early, 2, 'first paint came after boot in most runs: ' + detail)

    def test_ready_within_2_5_s_at_4x_cpu(self):
        runs = [measure(4) for _ in range(2)]
        ready = statistics.median(r['ready'] for r in runs)
        self.assertLess(ready, 2500, f'ready after {ready:.0f} ms at 4x CPU throttling')


if __name__ == '__main__':
    unittest.main()
