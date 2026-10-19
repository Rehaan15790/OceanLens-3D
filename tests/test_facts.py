"""Every number the UI states comes from the runtime `facts` module; this checks each one against an independent
Python recomputation by science (data-pipeline/facts_reference.py).

tests/facts_expected.json (HYCOM, the main snapshot) and tests/facts_expected_glorys.json (GLORYS, recomputed from
data/amphan-2020-glorys.json) map a dotted path under window.__ol.facts to its expected value, for example
    "real.path.heatBefore": {"value": 85.1, "tol": 0.05, ...}
    "real.comparison.worst[5].id": {"value": "ARGO-2902769"}
Numbers compare within `tol`; everything else compares exactly. The test skips until the build exposes
window.__ol.facts and the expected file exists.
"""
import json
import math
import re
import sys
import unittest

from helpers import REPO, AppTestCase

EXPECTED = REPO / 'tests' / 'facts_expected.json'
EXPECTED_GLORYS = REPO / 'tests' / 'facts_expected_glorys.json'
MISSING = object()


def resolve(obj, path):
    for name, index in re.findall(r'([^.\[\]]+)|\[(\d+)\]', path):
        if name:
            if not isinstance(obj, dict) or name not in obj:
                return MISSING
            obj = obj[name]
        else:
            i = int(index)
            if not isinstance(obj, list) or i >= len(obj):
                return MISSING
            obj = obj[i]
    return obj


class Facts(AppTestCase):
    enter = False

    def setUp(self):
        super().setUp()
        if not self.js('!!(window.__ol && __ol.facts)'):
            self.skipTest('this build does not expose window.__ol.facts yet')
        if not EXPECTED.exists():
            self.skipTest('tests/facts_expected.json does not exist yet')
        self.expected = json.loads(EXPECTED.read_text(encoding='utf-8'))

    def facts_now(self):
        return self.js("JSON.parse(JSON.stringify(typeof __ol.facts === 'function' ? __ol.facts() : __ol.facts))")

    def test_facts_match_the_independent_recomputation(self):
        self.compare(self.expected, self.facts_now())

    def test_glorys_facts_match_their_recomputation(self):
        if 'glorys' not in (self.js('__ol.models || []') or []):
            self.skipTest('this build has no GLORYS model')
        try:
            self.js("__ol.actions.setRealModel('glorys')")
            self.compare(json.loads(EXPECTED_GLORYS.read_text(encoding='utf-8')), self.facts_now())
        finally:
            self.js("__ol.actions.setRealModel(__ol.models[0])")

    def compare(self, expected, facts):
        missing, wrong = [], []
        for path, spec in expected.items():
            got, want = resolve(facts, path), spec['value']
            if got is MISSING:
                missing.append(path)
            elif isinstance(want, (int, float)) and not isinstance(want, bool):
                if not isinstance(got, (int, float)) or abs(got - want) > spec.get('tol', 0):
                    wrong.append(f"{path}: app {got}, recomputed {want} (tol {spec.get('tol', 0)})")
            elif got != want:
                wrong.append(f'{path}: app {got!r}, recomputed {want!r}')
        self.assertFalse(missing or wrong, f'{len(missing)} facts missing, {len(wrong)} differ '
                         f'(of {len(expected)}):\n' + '\n'.join(wrong[:30] + [f'missing: {p}' for p in missing[:20]]))


class Drift(AppTestCase):
    """The drift tool's central drift against data-pipeline/facts_reference.py drift_path (numpy, no app code), for the
    model on screen and for the other model the panel compares it with; and the spread against the random walk's own
    statistics."""
    enter = False
    # (lat, lon, analysis, direction): open sea forward and back, the head of the Bay, one off Mumbai that reaches the
    # coast (HYCOM after 64 h, GLORYS after 5 h), one that leaves the map's south-west edge (HYCOM, after 56 h)
    STARTS = [(15.0, 88.0, 4, 1), (15.0, 88.0, 4, -1), (21.0, 87.0, 4, 1), (19.0, 73.0, 4, 1), (7.0, 64.0, 4, 1)]

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        sys.path.insert(0, str(REPO / 'data-pipeline'))
        import facts_reference
        cls.ref = facts_reference
        cls.snaps = {'hycom': facts_reference.Snapshot(str(REPO / 'data' / 'amphan-2020.json'))}
        glorys = REPO / 'data' / 'amphan-2020-glorys.json'
        if glorys.exists():
            cls.snaps['glorys'] = facts_reference.Snapshot(str(glorys))
        # the drift uses each model's surface currents from the extras (HYCOM every 6 h, GLORYS daily)
        cls.extras = json.loads((REPO / 'data' / 'amphan-2020-extras.json').read_text(encoding='utf-8'))

    def ref_path(self, model, lat, lon, t, d):
        return self.ref.drift_path(self.snaps[model], lat, lon, t, 72, d, self.extras, model)

    def check(self, got, want, end, label):
        path, ref_end = want
        self.assertEqual(end, ref_end, f'{label}: ends {end!r}, recomputed {ref_end!r}')
        self.assertEqual([p['h'] for p in got], [p[0] for p in path], f'{label}: hours differ')
        worst = max(max(abs(p['lat'] - q[1]), abs(p['lon'] - q[2])) for p, q in zip(got, path))
        self.assertLess(worst, 1e-4, f'{label}: {worst:.2e} deg from the recomputation')

    def test_central_drift_matches_its_recomputation(self):
        models = self.js('__ol.models')
        try:
            for model in [m for m in models if m in self.snaps]:
                self.js(f"__ol.actions.setRealModel('{model}')")
                for lat, lon, t, d in self.STARTS:
                    with self.subTest(model=model, start=(lat, lon, t, d)):
                        self.js(f'__ol.actions.setTime({t})')
                        self.assertTrue(self.js(f'__ol.actions.startDrift({lat}, {lon}, {{hours:72, dir:{d}}})'))
                        run = self.js('(r=>({path:r.path, end:r.end}))(__ol.drift.run)')
                        want = self.ref_path(model, lat, lon, t, d)
                        self.check(run['path'], want, run['end'], model)
                        for other in self.js('__ol.drift.others.map(o=>({id:o.words.short.toLowerCase(), path:o.run.path, end:o.run.end}))'):
                            if other['id'] in self.snaps:
                                want = self.ref_path(other['id'], lat, lon, t, d)
                                self.check(other['path'], want, other['end'], f"{other['id']} beside {model}")
        finally:
            self.js('__ol.actions.clearDrift()')
            self.js(f"__ol.actions.setRealModel('{models[0]}')")

    def test_a_drift_from_a_buoy_follows_the_validation(self):
        # a drift released from a buoy at an analysis is one of drift_validation.py's cases: the same central drift,
        # and the separation from the buoy the app reports is the one the validation measures
        import drift_reference as dr
        snap = self.snaps['hycom']
        buoys = self.js('__ol.actions.setTime(4) || __ol.fn.buoysNow()')
        self.assertGreater(len(buoys), 3, 'no drifting buoys at the 16 May analysis')
        b = buoys[0]
        self.assertTrue(self.js(f"__ol.actions.startDriftFromBuoy({b['index']}, {{hours:72, dir:1}})"))
        got = self.js('(d=>({path:d.run.path, buoy:d.buoyPath}))(__ol.drift)')
        track = {h: (la, lo) for h, la, lo in self.extras['drifters']['tracks'][b['index']]}
        cur = dr.Currents.from_extras(self.extras, 'hycom')
        la, lo, _, _ = dr.run(cur, dr.Land(snap), [b['lat']], [b['lon']], snap.times[4].timestamp() * 1000, 72)
        for q in got['buoy']:
            self.assertEqual((q['lat'], q['lon']), track[b['h'] + q['h']])
        h = got['buoy'][-1]['h']
        p = got['path'][h]
        want = float(dr.km(la[h, 0], lo[h, 0], *track[b['h'] + h]))
        self.assertAlmostEqual(float(dr.km(p['lat'], p['lon'], got['buoy'][-1]['lat'], got['buoy'][-1]['lon'])), want, places=2)
        self.js('__ol.actions.clearDrift()')

    def test_ensemble_spread_is_the_random_walk(self):
        # no current: each axis spreads by sqrt(2 K t), so the 90 % ellipse is a circle of radius 2.146 sigma
        r = self.js("""(()=>{ const f = __ol.fn, clock = [0, 72*3.6e6];
            const run = f.driftRun({lat:12, lon:65, t0:0, hours:72, dir:1, clock, uv:()=>({u:0, v:0}), n:2000, r0Km:0, seed:7});
            const e = run.marks[run.marks.length-1].ellipse;
            return {a:e.a, b:e.b, area:e.area, path:run.path.at(-1)}; })()""")
        sigma = math.sqrt(2 * 1000 * 72 * 3600) / 1000
        want = math.pi * (2.146 * sigma) ** 2
        self.assertLess(abs(r['area'] / want - 1), 0.08, f"90 % area {r['area']:.0f} km², expected {want:.0f}")
        self.assertLess(r['a'] / r['b'], 1.15, 'a random walk without current spreads as a circle')
        self.assertEqual((r['path']['lat'], r['path']['lon']), (12, 65), 'the central drift moved without a current')

    def test_uniform_and_time_varying_currents(self):
        # 0.5 m/s east for 24 h at 12 N: 43.2 km east; a current rising linearly from 0 to 1 m/s over 72 h moves
        # 129.6 km (the midpoint rule is exact for both)
        js = """(uv, hours, dir)=>{ const r = __ol.fn.driftRun({lat:12, lon:65, t0:dir > 0 ? 0 : 1, hours, dir,
            clock:[0, 72*3.6e6], uv, n:0}); return r.path.at(-1); }"""
        km = lambda p: (p['lon'] - 65) * 111.32 * math.cos(math.radians(12))
        east = self.js(f"({js})(()=>({{u:0.5, v:0}}), 24, 1)")
        self.assertAlmostEqual(km(east), 43.2, places=6)
        self.assertEqual(east['lat'], 12)
        back = self.js(f"({js})(()=>({{u:0.5, v:0}}), 24, -1)")
        self.assertAlmostEqual(km(back), -43.2, places=6)
        ramp = self.js(f"({js})((la, lo, d, t)=>({{u:t, v:0}}), 72, 1)")
        self.assertAlmostEqual(km(ramp), 129.6, places=6)

    def test_same_start_same_drift(self):
        run = ("JSON.stringify((c=>__ol.fn.driftRun({lat:15, lon:88, start:__ol.fn.driftStart(4), hours:48, dir:1, clock:c.clock, "
               "uv:c.uv, seed:3}))(__ol.fn.driftCurrents(__ol.realModel)).marks)")
        a, b = self.js(run), self.js(run)
        self.assertEqual(a, b)


class Satellite(AppTestCase):
    """The satellite SST and SST-front maps (src/js/plugins/satellite.js) and the model-satellite SST line of the
    comparison panel against data-pipeline/facts_reference.py satellite_maps."""
    enter = False

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        sys.path.insert(0, str(REPO / 'data-pipeline'))
        import facts_reference
        cls.ref = facts_reference
        cls.snap = facts_reference.Snapshot(str(REPO / 'data' / 'amphan-2020.json'))
        cls.extras = json.loads((REPO / 'data' / 'amphan-2020-extras.json').read_text(encoding='utf-8'))

    def test_maps_match_their_recomputation(self):
        nlon = self.snap.nlon
        for t in (0, 4, 7):
            ref = self.ref.satellite_maps(self.snap, self.extras, t)
            app = {k: self.js(f"__ol.fn.field2dGrid('{k}', {t})") for k in ('sstsat', 'front')}
            bad = []
            for (i, j), (sst, front) in ref.items():
                for key, want in (('sstsat', sst), ('front', front)):
                    got = app[key][i * nlon + j]
                    if (want is None) != (got is None) or (want is not None and abs(got - want) > 1e-5):
                        bad.append(f'{key} t{t} ({i},{j}): app {got}, recomputed {want}')
            self.assertFalse(bad, '\n'.join(bad[:10]))
            self.assertGreater(sum(f is not None for _, f in ref.values()), 1000)

    def test_model_minus_satellite_sst(self):
        for model in self.js('__ol.models'):
            self.js(f"__ol.actions.setRealModel('{model}')")
            snap = self.snap if model == 'hycom' else self.ref.Snapshot(str(REPO / 'data' / 'amphan-2020-glorys.json'))
            got = self.js('__ol.fn.satelliteSstStats(4)')
            ref = self.ref.satellite_maps(self.snap, self.extras, 4)
            d = [snap.at(0, self.snap.m['lat0'] + i * 0.5, self.snap.m['lon0'] + j * 0.5, 0, 4) - s
                 for (i, j), (s, _) in ref.items() if s is not None]
            with self.subTest(model=model):
                self.assertEqual(got['n'], len(d))
                self.assertAlmostEqual(got['bias'], sum(d) / len(d), places=4)
                self.assertAlmostEqual(got['rmse'], math.sqrt(sum(x * x for x in d) / len(d)), places=4)
        self.js('__ol.actions.setRealModel(__ol.models[0])')


if __name__ == '__main__':
    unittest.main()
