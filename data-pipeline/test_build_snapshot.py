"""Unit tests for build_snapshot.py (stdlib unittest + numpy; no network, no cache needed).

    python -m unittest discover -s data-pipeline -v

The golden test at the end rebuilds data/amphan-2020.json from the raw cache and compares bytes; it runs only
when a cache is available ($OCEANLENS_CACHE or data-pipeline/cache) and the data file carries schema 1.
"""
import base64, json, math, os, sys, tempfile, unittest, urllib.error
from datetime import datetime, timezone
from unittest import mock

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import build_snapshot as B  # noqa: E402

SEP = '-' * 45


def ascii_response(body):
    return ('Dataset {\n    Float64 x;\n} test;\n' + SEP + '\n' + body).encode('ascii')


class Equations(unittest.TestCase):
    def test_p2z_matches_unesco_1983_check_value(self):
        # Fofonoff and Millard (1983): 10000 dbar at 30 degrees latitude is 9712.653 m
        self.assertAlmostEqual(B.p2z(10000, 30), 9712.653, places=3)

    def test_p2z_is_zero_at_the_surface_and_below_pressure_at_depth(self):
        self.assertEqual(B.p2z(0, 15), 0)
        self.assertTrue(990 < B.p2z(1000, 15) < 1000)


class OpendapParsing(unittest.TestCase):
    GRID = ('water_temp.water_temp[1][2][2][3]\n'
            '[0][0][0], 1, 2, 3\n[0][0][1], 4, 5, 6\n[0][1][0], 7, 8, 9\n[0][1][1], 10, 11, 12\n'
            'water_temp.time[1]\n165900.0\n\nwater_temp.depth[2]\n0.0, 2.0\n')

    def test_grid_and_maps(self):
        out = B.parse_ascii(ascii_response(self.GRID))
        self.assertEqual(out['water_temp_shape'], [1, 2, 2, 3])
        self.assertEqual(out['water_temp'].reshape(1, 2, 2, 3)[0, 1, 1, 2], 12)
        self.assertEqual(list(out['depth']), [0.0, 2.0])
        self.assertEqual(out['time'][0], 165900.0)

    def test_truncated_response_is_rejected(self):
        with self.assertRaises(ValueError):
            B.parse_ascii(ascii_response(self.GRID.replace('[0][1][1], 10, 11, 12\n', '')))

    def test_error_page_is_rejected(self):
        with self.assertRaises(ValueError):
            B.parse_ascii(b'Error {\n    code = 400;\n    message = "bad constraint";\n};')

    def test_das_packing(self):
        das = ('Attributes {\n    water_temp {\n        Int16 _FillValue -30000;\n'
               '        Float32 scale_factor 0.001;\n        Float32 add_offset 20.0;\n    }\n}\n')
        self.assertEqual(B.das_packing(B.parse_das(das.encode()), 'water_temp'), (0.001, 20.0, -30000.0))
        with self.assertRaises(ValueError):
            B.parse_das(b'<html>Service unavailable</html>')


class Gridding(unittest.TestCase):
    def test_fill_nans_fills_holes_and_keeps_finite_values(self):
        a = np.array([[1.0, 2.0, 3.0], [4.0, np.nan, 6.0], [7.0, 8.0, 9.0]])
        f = B.fill_nans(a)
        self.assertEqual(f[1, 1], (2 + 4 + 6 + 8) / 4)
        np.testing.assert_array_equal(f[~np.isnan(a)], a[~np.isnan(a)])

    def test_fill_nans_edge_cases(self):
        self.assertTrue(np.isnan(B.fill_nans(np.full((3, 3), np.nan))).all())
        a = np.arange(9.0).reshape(3, 3)
        np.testing.assert_array_equal(B.fill_nans(a), a)
        b = np.full((1, 5), np.nan); b[0, 0] = 2.0
        np.testing.assert_array_equal(B.fill_nans(b), np.full((1, 5), 2.0))

    def test_level_weights(self):
        dep = np.array([0, 20, 50, 70, 80, 100, 700, 800, 1000], float)
        lv = B.level_weights(dep, [0, 50, 75, 750, 1000])
        self.assertEqual(lv[0], [(0, 1.0)])
        self.assertEqual(lv[1], [(2, 1.0)])
        self.assertEqual(lv[2], [(3, 0.5), (4, 0.5)])
        self.assertEqual(lv[3], [(6, 0.5), (7, 0.5)])
        with self.assertRaises(B.InvariantError):
            B.level_weights(dep, [1200])

    def test_cell_members_cover_each_cell_without_sharing(self):
        lat = np.round(np.arange(-80, 90.0001, 0.04), 4)
        i0 = int(np.searchsorted(lat, B.LAT0 - 0.4))
        i1 = int(np.searchsorted(lat, B.LAT1 + 0.4))
        sub = lat[i0:i1 + 1:6]
        rows = B.cell_members(sub, B.LATS)
        self.assertTrue(all(2 <= len(r) <= 3 for r in rows))
        flat = np.concatenate(rows)
        self.assertEqual(len(flat), len(set(flat)))

    def test_contiguous_ranges(self):
        self.assertEqual(B.contiguous_ranges([0, 8, 14, 16, 17, 19]), [(0, 0), (8, 8), (14, 14), (16, 17), (19, 19)])


class ArgoLevels(unittest.TestCase):
    def test_exact_hits_and_interpolation(self):
        z = np.arange(2.0, 402.0, 2.0); v = 30 - z / 100
        out = B.to_levels(z, v)
        L = np.array(B.ARGO_LEVELS)
        np.testing.assert_allclose(out[L <= 400], 30 - L[L <= 400] / 100)
        self.assertTrue(np.isnan(out[L > 400]).all())

    def test_surface_fill_only_from_a_shallow_first_sample(self):
        z = np.arange(11.0, 300.0, 1.0)
        self.assertEqual(B.to_levels(z, np.full(z.size, 29.0))[0], 29.0)          # 5 m from the 11 m sample
        z = np.arange(13.0, 300.0, 1.0)
        self.assertTrue(np.isnan(B.to_levels(z, np.full(z.size, 29.0))[:2]).all())

    def test_wide_gaps_stay_gaps(self):
        z = np.array([5.0, 10, 15, 20, 60, 65, 70]); v = np.arange(7.0)
        out = B.to_levels(z, v)
        L = B.ARGO_LEVELS
        self.assertTrue(np.isnan(out[L.index(40)]))                               # 40 m gap > 20 m allowed
        self.assertEqual(out[L.index(60)], 4.0)

    def test_too_few_samples(self):
        self.assertTrue(np.isnan(B.to_levels(np.array([5.0, 10.0]), np.array([1.0, 2.0]))).all())


class RealTimeQC(unittest.TestCase):
    P = np.arange(5.0, 1005.0, 5.0)
    T = 30 * np.exp(-P / 300) + 5

    def test_clean_profile_passes(self):
        ok, n = B.rtqc(self.P, self.T.copy(), 'temp')
        self.assertTrue(ok.all())
        self.assertEqual(sum(n.values()), 0)

    def test_spike_is_removed(self):
        t = self.T.copy(); t[20] += 8
        ok, n = B.rtqc(self.P, t, 'temp')
        self.assertFalse(ok[20]); self.assertEqual(n['spike'], 1); self.assertEqual(ok.sum(), len(t) - 1)

    def test_cold_values_flagged_good_are_removed(self):
        # float 2902266, cycle 48: -1.9 C at the surface flagged good in delayed mode
        t = self.T.copy(); t[:5] = [-1.88, -1.9, -1.87, -2.0, -2.0]
        ok, n = B.rtqc(self.P, t, 'temp')
        self.assertFalse(ok[:5].any()); self.assertEqual(n['regional_range'], 5)

    def test_global_range_and_stuck_value(self):
        s = np.full(self.P.size, 35.0); s[3] = 45.0
        ok, n = B.rtqc(self.P, s, 'psal')
        self.assertEqual(n['global_range'], 1)
        self.assertEqual(n['stuck'], self.P.size - 1)                             # the rest is one repeated value
        self.assertFalse(ok.any())


def argo_rows(wmo, cyc, when, lat, lon, mode, t_of_p, s_of_p, pqc='1', tqc='1', sqc='1', raw_offset=0.0):
    rows = []
    for p in np.arange(5.0, 505.0, 5.0):
        t, s = t_of_p(p), s_of_p(p)
        adj = mode in ('A', 'D')
        rows.append([wmo, str(cyc), when, str(lat), str(lon), '1', mode,
                     f'{p}', pqc, f'{t + raw_offset}', tqc, f'{s}', sqc,
                     f'{p}' if adj else 'NaN', pqc if adj else ' ',
                     f'{t}' if adj else 'NaN', tqc if adj else ' ',
                     f'{s}' if adj else 'NaN', sqc if adj else ' ', 'NaN', ''])
    return rows


class ArgoSelection(unittest.TestCase):
    names = B.ARGO_COLS.split(',')
    tp = staticmethod(lambda p: 30 * math.exp(-p / 300) + 5)
    sp = staticmethod(lambda p: 34 + p / 1000)

    def test_adjusted_values_for_delayed_mode(self):
        rows = argo_rows('2902233', 305, '2020-05-13T04:10:57Z', 16.15, 87.22, 'D', self.tp, self.sp, raw_offset=5.0)
        profs, _ = B.select_argo(self.names, rows)
        self.assertEqual(len(profs), 1)
        self.assertLess(abs(profs[0]['T'][0] - self.tp(5.0)), 0.01)              # adjusted, not raw + 5
        self.assertEqual(profs[0]['s'], 3)

    def test_salinity_failure_drops_or_keeps_temperature(self):
        rows = argo_rows('2902233', 305, '2020-05-13T04:10:57Z', 16.15, 87.22, 'D', self.tp, self.sp, sqc='4')
        profs, counts = B.select_argo(self.names, rows)
        self.assertEqual(profs, []); self.assertEqual(counts['salinity_failed'], 1)
        profs, _ = B.select_argo(self.names, rows, temperature_only=True)
        self.assertEqual(profs[0]['sq'], 0)
        self.assertTrue(np.isnan(profs[0]['S']).all()); self.assertTrue(np.isfinite(profs[0]['T']).sum() >= 10)

    def test_real_time_flag_three_is_dropped(self):
        rows = argo_rows('2902200', 155, '2020-05-25T00:48:29Z', 12.8, 64.1, 'R', self.tp, self.sp, tqc='3')
        profs, counts = B.select_argo(self.names, rows)
        self.assertEqual(profs, []); self.assertEqual(counts['too_few_levels'], 1)

    def test_one_profile_per_float_per_analysis_closest_in_time(self):
        rows = (argo_rows('6902943', 180, '2020-05-18T14:37:30Z', 13.9, 62.0, 'D', self.tp, self.sp)
                + argo_rows('6902943', 181, '2020-05-20T14:50:30Z', 13.9, 62.0, 'D', self.tp, self.sp))
        profs, counts = B.select_argo(self.names, rows)
        self.assertEqual([p['c'] for p in profs], [180])                          # 21 h vs 27 h from 19 May 12Z
        self.assertEqual(counts['not_closest_in_time'], 1)

    def test_outside_the_window(self):
        rows = argo_rows('2902233', 300, '2020-04-28T04:00:00Z', 16.15, 87.22, 'D', self.tp, self.sp)
        profs, counts = B.select_argo(self.names, rows)
        self.assertEqual(profs, []); self.assertEqual(counts['outside_window'], 1)

    def test_unexpected_values_stop_the_build(self):
        with self.assertRaises(ValueError):
            B.select_argo(self.names, argo_rows('29<img>', 1, '2020-05-13T04:10:57Z', 16, 87, 'D', self.tp, self.sp))
        with self.assertRaises(ValueError):
            B.select_argo(self.names, argo_rows('2902233', 1, '2020-05-13T04:10:57Z', 16, 87, 'X', self.tp, self.sp))

    def test_header_is_checked(self):
        with self.assertRaises(ValueError):
            B.parse_argo_csv(b'Error {\n  code=404;\n}\n')


class Track(unittest.TestCase):
    def row(self, t, lat, lon, usa, nd, grade, name='AMPHAN', sid='2020136N10088'):
        return {'SID': sid, 'NAME': name, 'ISO_TIME': t, 'LAT': str(lat), 'LON': str(lon),
                'USA_WIND': str(usa) if usa is not None else ' ', 'NEWDELHI_WIND': str(nd) if nd is not None else ' ',
                'NEWDELHI_GRADE': grade}

    def rows(self):
        return [self.row('2020-05-18 06:00:00', 13.4, 86.2, 140, 120, 'SuCS'),
                self.row('2020-05-18 09:00:00', 13.7, 86.3, 142, 122, 'SuCS'),          # 3-hourly: skipped
                self.row('2020-05-18 12:00:00', 14.1, 86.4, 145, 125, 'SuCS'),
                self.row('2020-05-18 12:00:00', 20.0, 60.0, 50, 40, 'CS', name='OTHER'),
                self.row('2020-05-18 18:00:00', 14.9, 86.6, 140, 130, 'SuCS'),
                self.row('2020-05-21 12:00:00', 25.4, 89.6, None, 20, 'D')]

    def test_six_hourly_fixes_of_one_storm(self):
        fx = B.track_fixes(self.rows())
        self.assertEqual([f[0] for f in fx], ['2020-05-18T06:00Z', '2020-05-18T12:00Z', '2020-05-18T18:00Z',
                                              '2020-05-21T12:00Z'])
        self.assertEqual(fx[1][3:], [145, 125, 'SuCS'])
        self.assertIsNone(fx[3][3])

    def test_unknown_grade_stops_the_build(self):
        r = self.rows(); r[0]['NEWDELHI_GRADE'] = '<script>'
        with self.assertRaises(ValueError):
            B.track_fixes(r)

    def test_needs_fixes_inside_the_domain(self):
        r = [self.row(f'2020-05-1{d} 00:00:00', 30.0, 50.0, 30, 20, 'D') for d in range(5)]
        with self.assertRaises(B.InvariantError):
            B.track_fixes(r)


class Packing(unittest.TestCase):
    def test_round_trip_within_half_a_step(self):
        v = np.array([5.9, 26.0, 33.647, np.nan])
        back = B.unpack(B.pack(v, 'temp'), 'temp')
        np.testing.assert_allclose(back[:3], v[:3], atol=0.0005 + 1e-12)
        self.assertTrue(np.isnan(back[3]))

    def test_estuary_salinity_fits_and_nonsense_is_refused(self):
        self.assertAlmostEqual(B.unpack(B.pack([6.01], 'sal'), 'sal')[0], 6.01, places=6)
        with self.assertRaises(B.InvariantError):
            B.pack([60.0], 'sal')
        with self.assertRaises(B.InvariantError):
            B.pack([4.0], 'u')

    def test_missing_is_the_int16_minimum(self):
        q = np.frombuffer(base64.b64decode(B.pack([np.nan], 'temp')), '<i2')
        self.assertEqual(q[0], B.MISSING)

    def test_serialise_escapes_script_end_and_refuses_nan(self):
        text = B.serialise({'x': 'a</script>b', 'deg': '°'})
        self.assertNotIn(b'</', text)
        self.assertEqual(json.loads(text), {'x': 'a</script>b', 'deg': '°'})
        self.assertTrue(all(c < 128 for c in text))
        with self.assertRaises(ValueError):
            B.serialise({'x': float('nan')})


class FakeResponse:
    def __init__(self, data):
        self.data = data

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return self.data


def http_error(code):
    return urllib.error.HTTPError('https://example.test/x', code, 'error', None, None)


class Fetching(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cache = self.tmp.name
        self.good = ascii_response('x[2]\n1.0, 2.0\n')

    def tearDown(self):
        self.tmp.cleanup()

    def test_offline_cache_miss_names_the_file(self):
        F = B.Fetcher(self.cache)
        with self.assertRaises(B.CacheMiss) as cm:
            F.get('https://example.test/x', 'x.txt', B.parse_ascii)
        self.assertIn('x.txt', str(cm.exception))

    def test_cached_file_is_parsed_and_hashed(self):
        with open(os.path.join(self.cache, 'x.txt'), 'wb') as f:
            f.write(self.good)
        F = B.Fetcher(self.cache)
        self.assertEqual(list(F.get('u', 'x.txt', B.parse_ascii)['x']), [1.0, 2.0])
        self.assertEqual(len(F.used['x.txt']), 64)

    def test_corrupt_cached_file_is_reported(self):
        with open(os.path.join(self.cache, 'x.txt'), 'wb') as f:
            f.write(b'<html>oops</html>')
        with self.assertRaises(ValueError):
            B.Fetcher(self.cache).get('u', 'x.txt', B.parse_ascii)

    def test_client_error_fails_at_once_and_caches_nothing(self):
        calls = []

        def urlopen(*a, **k):
            calls.append(1)
            raise http_error(404)
        F = B.Fetcher(self.cache, fetch=True, sleep=lambda s: None)
        with mock.patch.object(B.urllib.request, 'urlopen', urlopen):
            with self.assertRaises(B.FetchError):
                F.get('https://example.test/x', 'x.txt', B.parse_ascii)
        self.assertEqual(len(calls), 1)
        self.assertEqual(os.listdir(self.cache), [])

    def test_server_error_is_retried_and_the_result_cached_with_a_manifest(self):
        answers = [http_error(503), ConnectionResetError('reset'), FakeResponse(self.good)]

        def urlopen(*a, **k):
            r = answers.pop(0)
            if isinstance(r, Exception):
                raise r
            return r
        F = B.Fetcher(self.cache, fetch=True, sleep=lambda s: None)
        with mock.patch.object(B.urllib.request, 'urlopen', urlopen):
            out = F.get('https://example.test/x', 'sub/x.txt', B.parse_ascii)
        self.assertEqual(list(out['x']), [1.0, 2.0])
        with open(os.path.join(self.cache, 'sub', 'x.txt'), 'rb') as f:
            self.assertEqual(f.read(), self.good)
        with open(os.path.join(self.cache, 'manifest.json'), encoding='utf-8') as f:
            man = json.load(f)
        self.assertEqual(man['sub/x.txt']['url'], 'https://example.test/x')
        self.assertEqual(man['sub/x.txt']['bytes'], len(self.good))
        self.assertFalse(any(n.endswith('.part') for n in os.listdir(self.cache)))

    def test_hosts_outside_the_allow_list_are_never_contacted(self):
        F = B.Fetcher(self.cache, fetch=True, sleep=lambda s: None, allow_hosts=['tds.hycom.org'])
        with mock.patch.object(B.urllib.request, 'urlopen', side_effect=AssertionError('network used')):
            with self.assertRaises(B.FetchError):
                F.get('https://erddap.ifremer.fr/erddap/x.csv', 'x.csv', B.parse_argo_csv)

    def test_garbage_download_is_not_cached(self):
        F = B.Fetcher(self.cache, fetch=True, sleep=lambda s: None)
        with mock.patch.object(B.urllib.request, 'urlopen', lambda *a, **k: FakeResponse(b'<html>maintenance</html>')):
            with self.assertRaises(ValueError):
                F.get('https://example.test/x', 'x.txt', B.parse_ascii)
        self.assertEqual(os.listdir(self.cache), [])


class ModelAtObservations(unittest.TestCase):
    DAS = ('Attributes {\n    water_temp {\n        Int16 _FillValue -30000;\n        Float32 scale_factor 0.001;\n'
           '        Float32 add_offset 20.0;\n    }\n    salinity {\n        Int16 _FillValue -30000;\n'
           '        Float32 scale_factor 0.001;\n        Float32 add_offset 20.0;\n    }\n}\n')

    def arr(self, t_corners, s_corners, nlev=3):
        """Array response (as tds.hycom.org returns for var.var[...]): the same corner values at every level
        except the last, which is below the seabed."""
        def grid(name, corners):
            lines = [f'{name}.{name}[1][{nlev}][2][2]']
            for k in range(nlev):
                for i in range(2):
                    vals = [corners[i][j] if k < nlev - 1 else -30000 for j in range(2)]
                    lines.append(f'[0][{k}][{i}], ' + ', '.join(str(v) for v in vals))
            lines += [f'{name}.time[1]', '165900.0', f'{name}.depth[{nlev}]',
                      ', '.join(str(float(d)) for d in range(0, 10 * nlev, 10)),
                      f'{name}.lat[2]', '16.0, 16.04', f'{name}.lon[2]', '87.12, 87.2']
            return '\n'.join(lines) + '\n'
        return B.parse_ascii(ascii_response(grid('water_temp', t_corners) + grid('salinity', s_corners)))

    def test_bilinear_over_wet_corners(self):
        a = self.arr([[10000, 12000], [10000, 12000]], [[13000, 13000], [13000, 13000]])
        T, S = B.obs_column(a, 16.02, 87.16, B.parse_das(self.DAS.encode()), [16.0, 16.04], [87.12, 87.2])
        self.assertAlmostEqual(T[0], 31.0, places=6)                               # halfway between 30 and 32
        self.assertAlmostEqual(S[0], 33.0, places=6)
        self.assertTrue(np.isnan(T[-1]))                                          # all corners below the seabed

    def test_dry_corner_is_left_out(self):
        a = self.arr([[10000, -30000], [10000, -30000]], [[13000, -30000], [13000, -30000]])
        T, S = B.obs_column(a, 16.02, 87.16, B.parse_das(self.DAS.encode()), [16.0, 16.04], [87.12, 87.2])
        self.assertAlmostEqual(T[0], 30.0, places=6)

    def test_column_on_argo_levels(self):
        dep = np.array([0.0, 2, 4, 6, 8, 10, 12, 15, 20]); v = 30 - dep / 10
        v[-1] = np.nan
        out = B.column_on_levels(dep, v)
        self.assertAlmostEqual(out[0], 29.5)                                      # 5 m
        self.assertAlmostEqual(out[1], 29.0)                                      # 10 m
        self.assertTrue(np.isnan(out[3]))                                         # 20 m: below the last wet level

    def test_request_uses_the_nearest_three_hourly_output(self):
        lat = np.round(np.arange(15.0, 17.0, 0.04), 4); lon = np.round(np.arange(86.0, 88.0, 0.08), 4)
        tim = np.arange(176600.0, 176700.0, 3.0)
        when = datetime(2000, 1, 1, tzinfo=timezone.utc) + B.timedelta(hours=176641.2)
        url, name, t, (i, j) = B.obs_request({'w': '2902233', 'c': 305, 'la': 16.15, 'lo': 87.22, 'when': when},
                                             lat, lon, tim)
        self.assertEqual(tim[t], 176642.0)
        self.assertTrue(lat[i] <= 16.15 <= lat[i + 1] and lon[j] <= 87.22 <= lon[j + 1])
        self.assertEqual(name, f'hycom_obs/2902233_305_t{t}.txt')
        self.assertIn('water_temp.water_temp[', url); self.assertIn('salinity.salinity[', url)


class GeneratedFiles(unittest.TestCase):
    """The committed palettes.js and tests/facts_expected.json must be what their generators produce now."""

    def test_palettes_match_the_generator(self):
        import make_palettes
        out = os.path.join(HERE, '..', 'src', 'js', 'palettes.js')
        if not os.path.exists(out):
            self.skipTest('src/js/palettes.js does not exist')
        with mock.patch('builtins.print'):
            self.assertEqual(make_palettes.main(['--out', out, '--check']), 0)

    def test_expected_facts_match_the_data(self):
        import facts_reference as R
        data = os.path.join(HERE, '..', 'data', 'amphan-2020.json')
        exp = os.path.join(HERE, '..', 'tests', 'facts_expected.json')
        if not (os.path.exists(data) and os.path.exists(exp)):
            self.skipTest('data file or tests/facts_expected.json missing')
        with open(exp, encoding='utf-8') as f:
            self.assertEqual(f.read(), json.dumps(R.expected(R.facts(data)), indent=1, sort_keys=True),
                             'regenerate with: python data-pipeline/facts_reference.py --expected tests/facts_expected.json')

    def test_empty_track_gives_no_track_facts(self):
        import facts_reference as R
        data = os.path.join(HERE, '..', 'data', 'amphan-2020.json')
        if not os.path.exists(data):
            self.skipTest('data file missing')
        with open(data, encoding='utf-8') as f:
            d = json.load(f)
        d['track']['fixes'] = [[f'2020-05-1{k}T00:00Z', 30.0, 50.0, 30, 20, 'D'] for k in range(5)]
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, 'x.json')
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(d, f)
            F = R.facts(path)
        self.assertIsNone(F['track']); self.assertIsNone(F['path']); self.assertIsNone(F['section'])
        self.assertTrue(F['comparison']['matchedInTime'])


def cache_dir():
    c = os.environ.get('OCEANLENS_CACHE') or os.path.join(HERE, 'cache')
    return c if os.path.isdir(c) and os.path.exists(os.path.join(c, 'argo.csv')) else None


class Golden(unittest.TestCase):
    """The committed data file must be exactly what the pipeline builds from the cache."""

    def test_rebuild_matches_the_committed_data(self):
        cache = cache_dir()
        out = os.path.join(HERE, '..', 'data', 'amphan-2020.json')
        if not cache:
            self.skipTest('no raw cache ($OCEANLENS_CACHE or data-pipeline/cache)')
        if not os.path.exists(out):
            self.skipTest('data/amphan-2020.json does not exist yet')
        with open(out, encoding='utf-8') as f:
            data = json.load(f)
        if data.get('schema') != B.SCHEMA:
            self.skipTest('data/amphan-2020.json predates schema 1; regenerate it with build_snapshot.py')
        args = ['--cache', cache, '--out', out, '--check']
        if data['meta']['provenance']['argo']['qc'].get('temperature_only_profiles'):
            args.append('--temperature-only')
        if data['argo'].get('model'):
            args.append('--model-at-obs')
        self.assertEqual(B.main(args), 0)


if __name__ == '__main__':
    unittest.main()
