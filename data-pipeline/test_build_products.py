"""Unit tests for build_products.py (no network). The golden test rebuilds data/products-2020-05.json from the raw
cache and compares bytes when the cache holds every satellite file it read."""
import base64, json, os, sys, tempfile, unittest

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import build_products as P  # noqa: E402
import build_snapshot as B  # noqa: E402

DATA = os.path.join(HERE, '..', 'data', 'products-2020-05.json')


class Science(unittest.TestCase):
    def test_cells_need_a_quarter_of_their_pixels(self):
        a = np.arange(16, dtype=float).reshape(4, 4)
        rows, cols = [np.array([0, 1]), np.array([2, 3])], [np.array([0, 1]), np.array([2, 3])]
        a[0:2, 0:2] = [[np.nan, np.nan], [np.nan, 5]]                   # one of four: kept
        a[2:4, 2:4] = np.nan                                           # none: NaN
        out = P.cell_stat(a, rows, cols, np.mean)
        self.assertEqual(out[0, 0], 5)
        self.assertTrue(np.isnan(out[1, 1]))
        self.assertEqual(out[0, 1], np.mean([2, 3, 6, 7]))

    def test_geometric_mean_and_box_mean(self):
        self.assertAlmostEqual(P.geometric_mean(np.array([0.1, 1.0, 10.0])), 1.0)
        self.assertTrue(np.isnan(P.geometric_mean(np.array([0.0, -1.0]))))
        f = np.full((len(B.LATS), len(B.LONS)), np.nan)
        f[B.LATS == 10.0, :] = 2.0
        f[B.LATS == 21.0, :] = 4.0
        self.assertEqual(P.box_mean(f, P.BOXES['bay']), 3.0)

    def test_pack_round_trip_and_missing(self):
        a = np.array([0.0, 0.123456, np.nan, 99.0])                   # 99 mg/m3 clips to the packing's top
        q = np.frombuffer(base64.b64decode(P.pack(a, 'chl')), '<i2')
        off, sc = P.QUANT['chl']
        self.assertEqual(q[2], P.MISSING)
        self.assertAlmostEqual(off + q[1] * sc, 0.123, places=6)
        self.assertEqual(q[3], 32767)

    def test_the_days_are_the_analysis_days(self):
        self.assertEqual([(t.month, t.day) for t in P.days(1995)], [(t.month, t.day) for t in B.SNAPS])
        self.assertEqual(sum(P.CLIM[0] <= y <= P.CLIM[1] for y in P.YEARS), 30)


class Committed(unittest.TestCase):
    def setUp(self):
        if not os.path.exists(DATA):
            self.skipTest('data/products-2020-05.json missing')
        with open(DATA, 'rb') as f:
            self.raw = f.read()
        self.d = json.loads(self.raw)

    def test_shape(self):
        d, m = self.d, self.d['meta']
        n = len(m['times']) * m['nlat'] * m['nlon']
        self.assertEqual(m['times'], [t.strftime('%Y-%m-%dT%H:%MZ') for t in B.SNAPS])
        for k in ('sst', 'clim', 'chl'):
            q = np.frombuffer(base64.b64decode(d[k]), '<i2')
            self.assertEqual(q.size, n, k)
            self.assertGreater((q != m['missing']).mean(), 0.5, k)
        self.assertEqual(d['series']['years'], P.YEARS)
        for box in P.BOXES:
            self.assertEqual(len(d['series'][box]), len(P.YEARS))
            self.assertTrue(all(24 < v < 33 for v in d['series'][box]), box)
        self.assertEqual(B.serialise(d), self.raw)

    def test_rebuild_from_the_cache_is_byte_identical(self):
        cache = os.environ.get('OCEANLENS_CACHE') or os.path.join(HERE, 'cache')
        if not os.path.isdir(os.path.join(cache, 'ostia')) or not os.path.isdir(os.path.join(cache, 'globcolour')):
            self.skipTest('the satellite files are not in the raw cache')
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, 'p.json')
            try:
                code = P.main(['--cache', cache, '--out', out])
            except B.CacheMiss:
                self.skipTest('the raw cache lacks some satellite files')
            if code == 2:
                self.skipTest('the raw cache lacks some satellite files')
            with open(out, 'rb') as f:
                self.assertEqual(f.read(), self.raw)


if __name__ == '__main__':
    unittest.main()
