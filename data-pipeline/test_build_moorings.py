"""Unit tests for build_moorings.py (no network). The golden test rebuilds data/moorings-2020-05.json from the raw
cache and compares bytes when the cache holds the three responses."""
import json, os, sys, tempfile, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import build_moorings as M  # noqa: E402
import build_snapshot as B  # noqa: E402

DATA = os.path.join(HERE, '..', 'data', 'moorings-2020-05.json')


def csv_for(key, rows):
    ds, cols, q, _ = M.SETS[key]
    head = ','.join(['station', 'latitude', 'longitude', 'depth', 'time'] + cols + [q])
    return (head + '\n,degrees_north,degrees_east,m,UTC' + ',x' * (len(cols) + 1) + '\n' + '\n'.join(rows) + '\n').encode()


class FakeFetcher:
    def __init__(self, bodies):
        self.bodies, self.used = bodies, {}

    def get(self, url, name, parse, **kw):
        self.used[name] = '0' * 64
        return parse(self.bodies[name])

    def manifest(self):
        return {}


class Building(unittest.TestCase):
    def test_quality_codes_units_and_layout(self):
        F = FakeFetcher({
            'moorings/pmelTaoDyT-2020-05.csv': csv_for('temp', [
                '12n90e,12.0,90.0,1.0,2020-05-04T12:00:00Z,31.33,2.0',
                '12n90e,12.0,90.0,10.0,2020-05-04T12:00:00Z,30.92,1.0',
                '12n90e,12.0,90.0,13.0,2020-05-04T12:00:00Z,NaN,0.0',      # no value: dropped
                '12n90e,12.0,90.0,20.0,2020-05-04T12:00:00Z,25.0,4.0',     # lower quality: dropped
                '12n90e,12.0,90.0,1.0,2020-05-05T12:00:00Z,31.31,3.0',
                '15n65e,15.0,65.0,5.0,2020-05-04T12:00:00Z,30.0,2.0',
                '8n67e,8.0,67.0,1.0,2020-05-04T12:00:00Z,30.5,2.0']),
            'moorings/pmelTaoDyS-2020-05.csv': csv_for('sal', ['12n90e,12.0,90.0,10.0,2020-05-04T12:00:00Z,32.6,2.0']),
            'moorings/pmelTaoDyCur-2020-05.csv': csv_for('cur', ['12n90e,12.0,90.0,12.0,2020-05-04T12:00:00Z,-6.0,3.7,2.0']),
        })
        d = M.build(F)
        m = {x['station']: x for x in d['moorings']}
        self.assertEqual(sorted(m), ['12n90e', '15n65e', '8n67e'])
        a = m['12n90e']
        self.assertEqual(a['days'], ['2020-05-04', '2020-05-05'])
        self.assertEqual(a['depth'], {'temp': [1.0, 10.0], 'sal': [10.0], 'cur': [12.0]})
        self.assertEqual(a['temp'], [[31.33, 30.92], [31.31, None]])
        self.assertEqual((a['u'][0], a['v'][0]), ([-0.06], [0.037]))         # cm/s -> m/s
        self.assertEqual(d['meta']['provenance']['values_dropped'], 2)

    def test_refuses_another_table_and_other_hours(self):
        with self.assertRaises(B.InvariantError):
            M.parser('temp')(b'station,time\n,UTC\n')
        F = FakeFetcher({'moorings/pmelTaoDyT-2020-05.csv': csv_for('temp', ['12n90e,12.0,90.0,1.0,2020-05-04T00:00:00Z,31.3,2.0']),
                         'moorings/pmelTaoDyS-2020-05.csv': csv_for('sal', []), 'moorings/pmelTaoDyCur-2020-05.csv': csv_for('cur', [])})
        with self.assertRaises(B.InvariantError):
            M.build(F)

    def test_https_and_an_allowed_host(self):
        for key in M.SETS:
            u = M.url(key)
            self.assertTrue(u.startswith('https://coastwatch.pfeg.noaa.gov/'), u)
            self.assertIn('coastwatch.pfeg.noaa.gov', B.DEFAULT_HOSTS)


class Committed(unittest.TestCase):
    def setUp(self):
        if not os.path.exists(DATA):
            self.skipTest('data/moorings-2020-05.json missing')
        with open(DATA, 'rb') as f:
            self.raw = f.read()

    def test_shape(self):
        d = json.loads(self.raw)
        self.assertGreaterEqual(len(d['moorings']), M.MIN_MOORINGS)
        for m in d['moorings']:
            self.assertTrue(B.LAT0 <= m['lat'] <= B.LAT1 and B.LON0 <= m['lon'] <= B.LON1, m['station'])
            for key, n in (('temp', 'temp'), ('sal', 'sal'), ('u', 'cur'), ('v', 'cur')):
                self.assertEqual(len(m[key]), len(m['days']))
                self.assertTrue(all(len(row) == len(m['depth'][n]) for row in m[key]), (m['station'], key))
        self.assertEqual(B.serialise(d), self.raw)

    def test_rebuild_from_the_cache_is_byte_identical(self):
        cache = os.environ.get('OCEANLENS_CACHE') or os.path.join(HERE, 'cache')
        if not all(os.path.exists(os.path.join(cache, 'moorings', f'{M.SETS[k][0]}-2020-05.csv')) for k in M.SETS):
            self.skipTest('the RAMA responses are not in the raw cache')
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, 'm.json')
            self.assertEqual(M.main(['--cache', cache, '--out', out]), 0)
            with open(out, 'rb') as f:
                self.assertEqual(f.read(), self.raw)


if __name__ == '__main__':
    unittest.main()
