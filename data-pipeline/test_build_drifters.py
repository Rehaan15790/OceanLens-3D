"""Unit tests for build_drifters.py (no network). The golden test rebuilds data/drifters-2020-05.json from the raw
cache and compares bytes when the cache holds the GDP response."""
import json, os, sys, tempfile, unittest
from datetime import timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import build_drifters as D  # noqa: E402
import build_snapshot as B  # noqa: E402

DATA = os.path.join(HERE, '..', 'data', 'drifters-2020-05.json')
HEAD = ','.join(D.COLUMNS) + '\n,UTC,degrees_north,degrees_east,UTC,,\n'


def row(i, t, lat, lon, lost='2021-01-01T00:00:00Z'):
    return f'{i},{t:%Y-%m-%dT%H:%M:%SZ},{lat},{lon},{lost},SVP,GPS\n'


class Parsing(unittest.TestCase):
    def test_refuses_another_table(self):
        with self.assertRaises(B.InvariantError):
            D.parse(b'ID,time\n,UTC\n1,2020-05-04T12:00:00Z\n')
        with self.assertRaises(B.InvariantError):
            D.parse((HEAD + '12x,2020-05-04T12:00:00Z,10,80,,SVP,GPS\n').encode())     # not a drifter id
        with self.assertRaises(B.InvariantError):
            D.parse((HEAD + '1,2020-05-04T12:00:00Z,95,80,,SVP,GPS\n').encode())       # latitude out of range

    def test_keeps_six_hourly_positions_inside_the_map_and_window(self):
        t0, t1 = D.window()
        text = HEAD + ''.join(row(7, t0 + timedelta(hours=h), 15 + h / 100, 88) for h in range(0, 13))   # hourly
        text += row(7, t0 - timedelta(hours=6), 15, 88)              # before the first analysis
        text += row(7, t1 + timedelta(hours=6), 15, 88)              # after the last
        text += row(8, t0, 15, 95) + row(8, t0 + timedelta(hours=6), 15, 95)    # east of the map
        text += row(9, t0, 12, 85, '')                               # one position only, and no drogue date
        out = D.drifters_from_rows(D.parse(text.encode()))
        self.assertEqual([d['id'] for d in out], ['7'])
        self.assertEqual([f[0] for f in out[0]['fixes']], [0, 1, 2])
        self.assertEqual(out[0]['fixes'][1], [1, 15.06, 88.0])
        self.assertEqual(out[0]['drogueLost'], '2021-01-01T00:00Z')


class Committed(unittest.TestCase):
    def setUp(self):
        if not os.path.exists(DATA):
            self.skipTest('data/drifters-2020-05.json missing')
        with open(DATA, 'rb') as f:
            self.raw = f.read()
        self.d = json.loads(self.raw)

    def test_shape(self):
        d = self.d
        self.assertEqual(d['schema'], D.SCHEMA)
        self.assertEqual(d['meta']['stepH'], D.STEP_H)
        self.assertGreaterEqual(len(d['drifters']), D.MIN_DRIFTERS)
        for b in d['drifters']:
            ks = [f[0] for f in b['fixes']]
            self.assertEqual(ks, sorted(set(ks)), b['id'])
            for _, lat, lon in b['fixes']:
                self.assertTrue(B.LAT0 <= lat <= B.LAT1 and B.LON0 <= lon <= B.LON1, b['id'])
        self.assertEqual(B.serialise(d), self.raw, 'the file is the pipeline\'s own serialisation')

    def test_rebuild_from_the_cache_is_byte_identical(self):
        cache = os.environ.get('OCEANLENS_CACHE') or os.path.join(HERE, 'cache')
        if not os.path.exists(os.path.join(cache, *D.CACHE_NAME.split('/'))):
            self.skipTest('the GDP response is not in the raw cache')
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, 'd.json')
            self.assertEqual(D.main(['--cache', cache, '--out', out]), 0)
            with open(out, 'rb') as f:
                self.assertEqual(f.read(), self.raw)


if __name__ == '__main__':
    unittest.main()
