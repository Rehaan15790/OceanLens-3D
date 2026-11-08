"""Tests for build_extras.py, drift_reference.py and drift_validation.py (stdlib unittest + numpy; no network).

The golden test rebuilds data/amphan-2020-extras.json from the raw cache when one is present; the others need only
the committed data files.
"""
import base64, json, math, os, sys, unittest
from datetime import datetime, timezone

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, '..')
sys.path.insert(0, HERE)
import build_extras as X              # noqa: E402
import build_snapshot as B            # noqa: E402
import drift_reference as dr          # noqa: E402
import facts_reference as fr          # noqa: E402
import ncio                           # noqa: E402

DATA = os.path.join(ROOT, 'data')


def load(name):
    with open(os.path.join(DATA, name), encoding='utf-8') as f:
        return json.load(f)


def unpack(b64, shape, off, sc):
    q = np.frombuffer(base64.b64decode(b64), '<i2').astype('f8').reshape(shape)
    return np.where(q == B.MISSING, np.nan, off + q * sc)


def oisst_bytes(day, sst):
    """A LAS-style OISST extract: int16 SST scaled by 0.01, land written as 0, no missing value (as LAS writes it)."""
    g = X.OISST_GRID
    lat = (g['lat0'] + g['step'] * np.arange(g['nlat'])).astype('f4')
    lon = (g['lon0'] + g['step'] * np.arange(g['nlon'])).astype('f4')
    days = (day - datetime(1978, 1, 1, 12, tzinfo=timezone.utc)).days
    q = np.where(np.isfinite(sst), np.round(sst / 0.01), 0).astype('i2')[None, None]
    return ncio.write_bytes(
        {'LON81_560': g['nlon'], 'LAT81_480': g['nlat'], 'ZLEV': 1, 'TIME': 1},
        {'LON81_560': {'dims': ('LON81_560',), 'attrs': {'units': 'degrees_east'}, 'data': lon},
         'LAT81_480': {'dims': ('LAT81_480',), 'attrs': {'units': 'degrees_north'}, 'data': lat},
         'ZLEV': {'dims': ('ZLEV',), 'attrs': {'units': 'meters'}, 'data': np.zeros(1, 'f4')},
         'TIME': {'dims': ('TIME',), 'attrs': {'units': 'days since 1978-01-01 12:00:00', 'axis': 'T'},
                  'data': np.array([days], 'f4')},
         'SST': {'dims': ('TIME', 'ZLEV', 'LAT81_480', 'LON81_560'),
                 'attrs': {'units': 'Celsius', 'scale_factor': np.float32(0.01), 'add_offset': np.float32(0)}, 'data': q}},
        {'Conventions': 'CF-1.6'})


class LasExtracts(unittest.TestCase):
    DAY = datetime(2020, 5, 16, 12, tzinfo=timezone.utc)

    def sst(self):
        g = X.OISST_GRID
        a = np.full((g['nlat'], g['nlon']), 29.5)
        a[30:, :50] = np.nan                                         # land, about a fifth of the map
        return a

    def test_land_written_as_zero_becomes_nan(self):
        got = X.parse_oisst(self.DAY)(oisst_bytes(self.DAY, self.sst()))
        self.assertTrue(np.isnan(got[45, 10]))
        self.assertAlmostEqual(got[0, 0], 29.5, places=5)                 # a float32 scale factor, as LAS writes it

    def test_another_day_is_refused(self):
        with self.assertRaises(B.InvariantError):
            X.parse_oisst(self.DAY)(oisst_bytes(datetime(2020, 5, 17, 12, tzinfo=timezone.utc), self.sst()))

    def test_the_request_names_the_dataset_box_and_day(self):
        x = X.las_xml('id-a153fb198f', 'SST-id-a153fb198f', self.DAY)
        for part in ("@ID='Data_Extract_netCDF'", '/lasdata/datasets/id-a153fb198f/variables/SST-id-a153fb198f',
                     'low="61" high="92"', 'low="6" high="22"', 'low="16-May-2020 12:00" high="16-May-2020 12:00"'):
            self.assertIn(part, x)


class DrifterParsing(unittest.TestCase):
    HEAD = 'ID,time,latitude,longitude,drogue_lost_date\n,UTC,degrees_north,degrees_east,UTC\n'

    def test_tracks_and_drogue(self):
        body = (self.HEAD + '7,2020-05-04T12:00:00Z,10.0,80.5,2020-05-10T00:00:00Z\n'
                            '7,2020-05-04T13:00:00Z,10.1,80.6,2020-05-10T00:00:00Z\n')
        out = X.parse_gdp(body.encode())
        self.assertEqual(out['7']['h'], [0, 1])
        self.assertEqual(out['7']['drogue_lost'], '2020-05-10T00:00:00Z')

    def test_off_grid_times_and_positions_outside_the_map_are_refused(self):
        for row in ('7,2020-05-04T12:30:00Z,10.0,80.5,\n', '7,2020-05-04T12:00:00Z,25.0,80.5,\n'):
            with self.subTest(row=row), self.assertRaises(B.InvariantError):
                X.parse_gdp((self.HEAD + row).encode())

    def test_other_columns_are_refused(self):
        with self.assertRaises(B.InvariantError):
            X.parse_gdp(b'ID,time,lat,lon\n,,,\n')


class TrajectoryModel(unittest.TestCase):
    """drift_reference against currents with known answers (the same cases as the app's test_facts.Drift)."""

    @classmethod
    def setUpClass(cls):
        cls.snap = fr.Snapshot(os.path.join(DATA, 'amphan-2020.json'))
        cls.land = dr.Land(cls.snap)

    def cur(self, u):
        g = {'lat0': 6.0, 'lon0': 61.0, 'step': 0.5, 'nlat': 33, 'nlon': 63}
        uu = np.stack([np.full((33, 63), x) for x in u])
        return dr.Currents([0, 72 * 3.6e6][:len(u)], uu, np.zeros_like(uu), g)

    def east_km(self, la, lo):
        return (lo - 65) * dr.M_PER_DEG / 1000 * math.cos(math.radians(12))

    def test_uniform_and_rising_currents(self):
        la, lo, end, _ = dr.run(self.cur([0.5, 0.5]), self.land, [12], [65], 0, 24)
        self.assertAlmostEqual(self.east_km(la[-1, 0], lo[-1, 0]), 43.2, places=6)
        la, lo, end, _ = dr.run(self.cur([0.0, 1.0]), self.land, [12], [65], 0, 72)
        self.assertAlmostEqual(self.east_km(la[-1, 0], lo[-1, 0]), 129.6, places=6)

    def test_random_walk_spread(self):
        rng = np.random.default_rng(1)
        la, lo, end, _ = dr.run(self.cur([0.0, 0.0]), self.land, np.full(4000, 12.0), np.full(4000, 65.0), 0, 72,
                                K=1000, rng=rng)
        sp = dr.spread(la[-1], lo[-1], end == 0)
        sigma2 = 2 * 1000 * 72 * 3600 / 1e6                          # km2
        self.assertLess(abs(np.trace(sp[2]) / 2 / sigma2 - 1), 0.06)

    def test_stops_at_the_coast_and_the_edge(self):
        _, _, end, _ = dr.run(self.cur([1.0, 1.0]), self.land, [15.0], [72.5], 0, 72)     # east onto the Goa coast
        self.assertEqual(int(end[0]), 1)
        _, _, end, _ = dr.run(self.cur([-0.5, -0.5]), self.land, [15.0], [62.0], 0, 72)
        self.assertEqual(int(end[0]), 2)


class ExtrasFile(unittest.TestCase):
    """The committed extras agree with the snapshots where both hold the same field."""

    @classmethod
    def setUpClass(cls):
        cls.e = load('amphan-2020-extras.json')

    def model_surface(self, name):
        s = load(name)
        m = s['meta']
        q = np.frombuffer(base64.b64decode(s['model']), '<i2').astype('f8').reshape(8, 4, 11, m['nlat'], m['nlon'])
        return np.stack([m['quant'][v][0] + q[:, i, 0] * m['quant'][v][1] for i, v in enumerate(m['vars'])], 1)[:, 2:4]

    def test_currents_at_the_analyses_equal_the_snapshots(self):
        g = self.e['meta']['grid']
        for model, every, name in (('hycom', 12, 'amphan-2020.json'), ('glorys', 3, 'amphan-2020-glorys.json')):
            with self.subTest(model=model):
                s = self.e['surf'][model]
                f = unpack(s['data'], (len(s['times']), 2, g['nlat'], g['nlon']), *s['quant'])
                self.assertEqual(s['times'][::every], load(name)['meta']['times'])
                np.testing.assert_array_equal(f[::every], self.model_surface(name))

    def test_drifters_stay_inside_the_map_on_whole_hours(self):
        d = self.e['drifters']
        self.assertGreaterEqual(len(d['tracks']), 10)
        for tr in d['tracks']:
            h = [p[0] for p in tr]
            self.assertEqual(h, sorted(set(h)))
            for _, la, lo in tr:
                self.assertTrue(B.LAT0 <= la <= B.LAT1 and B.LON0 <= lo <= B.LON1)

    def test_satellite_sst_is_plausible(self):
        s, g = self.e['sat']['sst'], self.e['sat']['sst']['grid']
        f = unpack(s['data'], (len(s['times']), g['nlat'], g['nlon']), *s['quant'])
        self.assertEqual(s['times'], load('amphan-2020.json')['meta']['times'])
        sea = np.isfinite(f)
        self.assertTrue(0.7 < sea.mean() < 0.85)
        self.assertTrue(25 < np.nanmin(f) and np.nanmax(f) < 34)


class Validation(unittest.TestCase):
    def test_the_committed_validation_is_what_the_script_computes(self):
        import drift_validation as V
        res = V.validate(ROOT)
        self.assertEqual(json.dumps(res, indent=1, ensure_ascii=True) + '\n',
                         open(os.path.join(DATA, 'drift-validation.json'), encoding='ascii').read())


class Golden(unittest.TestCase):
    def test_rebuild_matches_the_committed_extras(self):
        cache = os.environ.get('OCEANLENS_CACHE') or os.path.join(HERE, 'cache')
        if not os.path.exists(os.path.join(cache, 'extras')):
            self.skipTest('no raw cache with the extras ($OCEANLENS_CACHE or data-pipeline/cache)')
        payload, _ = X.build(cache, log=lambda *a: None)
        with open(os.path.join(DATA, 'amphan-2020-extras.json'), 'rb') as f:
            self.assertEqual(B.serialise(payload), f.read())


if __name__ == '__main__':
    unittest.main()
