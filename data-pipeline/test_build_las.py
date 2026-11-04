"""Unit tests for build_las.py (no network): the LAS request, the GlobalSign intermediate, moving currents from the U
cells to the T cells, and a byte-identical rebuild of data/amphan-2020-igora.json when the cache holds the extracts."""
import base64, json, os, sys, tempfile, unittest, urllib.parse

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import build_las as L  # noqa: E402
import build_snapshot as B  # noqa: E402

DATA = os.path.join(HERE, '..', 'data', 'amphan-2020-igora.json')


class Request(unittest.TestCase):
    def test_netcdf_extract_of_the_map_for_may_2020(self):
        x = L.request_xml('temp')
        self.assertIn("operation[@ID='Data_Extract_netCDF']", x)
        self.assertIn('<view>xyz</view>', x)
        self.assertIn('/lasdata/datasets/id-aed8c62062/variables/TEMPERATURE-id-aed8c62062', x)
        self.assertIn(f'<range type="t" low="{L.MONTH}" high="{L.MONTH}">', x)
        self.assertTrue(L.BOX['x'][0] < B.LON0 and L.BOX['x'][1] > B.LON1 and L.BOX['y'][0] < B.LAT0 and L.BOX['y'][1] > B.LAT1)
        u = urllib.parse.urlsplit(L.product_url('v'))
        self.assertEqual((u.scheme, u.hostname), ('https', 'las.incois.gov.in'))
        self.assertIn('las.incois.gov.in', B.DEFAULT_HOSTS)

    def test_the_intermediate_is_checked(self):
        self.assertIn('BEGIN CERTIFICATE', L.intermediate())
        with tempfile.TemporaryDirectory() as d:
            bad = os.path.join(d, 'x.pem')
            with open(L.CERT) as f:
                pem = f.read()
            with open(bad, 'w') as f:                  # another certificate (here: the same with one byte changed)
                lines = pem.splitlines()
                lines[5] = ('A' if lines[5][0] != 'A' else 'B') + lines[5][1:]
                f.write('\n'.join(lines) + '\n')
            old, L.CERT = L.CERT, bad
            try:
                with self.assertRaises(Exception):
                    L.intermediate()
            finally:
                L.CERT = old


class Grid(unittest.TestCase):
    def test_u_cells_to_t_cells(self):
        xu, yu = np.array([80.0, 80.5, 81.0, 81.5]), np.array([10.0, 10.5, 11.0])
        xt, yt = xu[:-1] + 0.25, yu[:-1] + 0.25
        a = (0.1 * xu[None, :] + 0.2 * yu[:, None])[None, None]          # linear, so the move is exact
        out = L.to_t_points(a, xu, yu, xt, yt)
        np.testing.assert_allclose(out[0, 0], 0.1 * xt[None, :] + 0.2 * yt[:, None])
        a2 = a.copy()
        a2[0, 0, 0, 0] = np.nan                                           # land at a corner stays out of its cells
        self.assertTrue(np.isnan(L.to_t_points(a2, xu, yu, xt, yt)[0, 0, 0, 0]))


class Committed(unittest.TestCase):
    def setUp(self):
        if not os.path.exists(DATA):
            self.skipTest('data/amphan-2020-igora.json missing')
        with open(DATA, 'rb') as f:
            self.raw = f.read()

    def test_one_month_at_every_time_on_the_main_grid(self):
        d = json.loads(self.raw)
        m = d['meta']
        with open(os.path.join(HERE, '..', 'data', 'amphan-2020.json'), encoding='utf-8') as f:
            M = json.load(f)['meta']
        for k in ('times', 'lat0', 'lon0', 'step', 'nlat', 'nlon', 'depths', 'vars'):
            self.assertEqual(m[k], M[k], k)
        self.assertEqual((m['model']['short'], m['model']['temperature'], m['model']['monthly']), ('IGORA', 'potential', '2020-05'))
        q = base64.b64decode(d['model'])
        one = len(q) // len(m['times'])
        self.assertTrue(all(q[k * one:(k + 1) * one] == q[:one] for k in range(len(m['times']))))
        self.assertEqual(B.serialise(d), self.raw)

    def test_rebuild_from_the_cache_is_byte_identical(self):
        cache = os.environ.get('OCEANLENS_CACHE') or os.path.join(HERE, 'cache')
        if not all(os.path.exists(os.path.join(cache, 'las', f'igora-{k}-2020-05.nc')) for k in L.IGORA):
            self.skipTest('the LAS extracts are not in the raw cache')
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, 'i.json')
            self.assertEqual(L.main(['--cache', cache, '--out', out]), 0)
            with open(out, 'rb') as f:
                self.assertEqual(f.read(), self.raw)


if __name__ == '__main__':
    unittest.main()
