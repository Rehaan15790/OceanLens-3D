"""Unit tests for build_forecast.py (no network): picking the latest run and its days from the object-store listings,
and the committed file's shape when there is one."""
import json, os, re, sys, unittest
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import build_forecast as FC  # noqa: E402
import build_snapshot as B  # noqa: E402

DATA = os.path.join(HERE, '..', 'data', 'forecast-latest.json')


class Listings:
    """A Fetcher that answers the month listings from a set of file names."""
    def __init__(self, names):
        self.names = names

    def get(self, url, name, parse, **kw):
        self.names_asked = getattr(self, 'names_asked', []) + [name]
        key, y, mo, _stamp = re.search(r'listing-(\w+)-(\d{4})-(\d{2})-(\d{8})\.xml$', name).groups()
        month = f'{y}/{mo}'
        out = {f'{FC.PRODUCT}/{FC.DATASETS[key]}/{month}/{n}': 100 for n in self.names
               if f'-{key}_fcst_' in n and n.split('_')[3][:6] == month.replace('/', '')}
        if not out:
            raise B.InvariantError('the bucket listing is empty')
        return out


def names(run, days, keys=('thetao', 'so', 'uovo')):
    return [f'glo12_rg_1d-m_{d}-{d}_3D-{k}_fcst_R{run}.nc' for d in days for k in keys]


class Runs(unittest.TestCase):
    def test_the_latest_run_and_its_days(self):
        old = names('20261103', ['20261102', '20261103', '20261104'])
        new = names('20261104', [f'202611{d:02d}' for d in range(3, 14)])
        run, days, files = FC.run_files(Listings(old + new), today=datetime(2026, 11, 4, tzinfo=timezone.utc))
        self.assertEqual(run, '20261104')
        self.assertEqual(days, [f'202611{d:02d}' for d in range(3, 14)])
        self.assertEqual(len(files['uovo']), 11)

    def test_a_day_missing_a_variable_is_left_out_and_runs_can_be_chosen(self):
        L = names('20261104', ['20261103', '20261104', '20261105']) + names('20261104', ['20261106'], ('thetao', 'so'))
        L += names('20261103', ['20261102', '20261103'])
        run, days, _ = FC.run_files(Listings(L), today=datetime(2026, 11, 4, tzinfo=timezone.utc))
        self.assertEqual(days, ['20261103', '20261104', '20261105'])
        run, days, _ = FC.run_files(Listings(L), run='20261103', today=datetime(2026, 11, 4, tzinfo=timezone.utc))
        self.assertEqual((run, days), ('20261103', ['20261102', '20261103']))

    def test_across_a_month_boundary(self):
        L = names('20261130', ['20261129', '20261130', '20261201', '20261202'])
        run, days, _ = FC.run_files(Listings(L), today=datetime(2026, 11, 30, tzinfo=timezone.utc))
        self.assertEqual(days, ['20261129', '20261130', '20261201', '20261202'])

    def test_each_day_lists_afresh(self):
        L = Listings(names('20261105', ['20261104', '20261105']))
        FC.run_files(L, today=datetime(2026, 11, 5, tzinfo=timezone.utc))
        self.assertTrue(all(n.endswith('-20261105.xml') for n in L.names_asked), L.names_asked)
        L2 = Listings(names('20261104', ['20261103', '20261104']))
        FC.run_files(L2, run='20261104')                  # a named run reads the listings of its own day
        self.assertTrue(all(n.endswith('-20261104.xml') for n in L2.names_asked), L2.names_asked)

    def test_no_files(self):
        with self.assertRaises(B.InvariantError):
            FC.run_files(Listings([]), today=datetime(2026, 11, 4, tzinfo=timezone.utc))


class Committed(unittest.TestCase):
    def test_shape(self):
        if not os.path.exists(DATA):
            self.skipTest('data/forecast-latest.json missing')
        with open(DATA, 'rb') as f:
            raw = f.read()
        d = json.loads(raw)
        m = d['meta']
        self.assertEqual(m['case']['kind'], 'forecast')
        self.assertEqual(m['model']['short'], 'Forecast')
        self.assertEqual([m['lat0'], m['lon0'], m['step'], m['nlat'], m['nlon'], m['depths']],
                         [B.LAT0, B.LON0, B.STEP, len(B.LATS), len(B.LONS), B.DEPTHS])
        self.assertTrue(2 <= len(m['times']) <= FC.MAX_DAYS)
        self.assertEqual(d['argo']['profiles'], [])
        self.assertNotIn('track', d)
        self.assertEqual(B.serialise(d), raw)


if __name__ == '__main__':
    unittest.main()
