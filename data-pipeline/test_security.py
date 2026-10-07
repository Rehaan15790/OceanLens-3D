"""Security regression tests: hostile NetCDF files, the download path, the data writer and the repository's own guards.

    python -m unittest discover -s data-pipeline -p "test_security.py" -v

Offline: nothing here uses the real network (downloads are mocked; the redirect test talks to a throwaway server
on 127.0.0.1), and no cache is needed. Each attack test fails without the fix it guards; the "still reads" tests
check that the fixes did not break legitimate files.
"""
import ast, http.server, importlib.util, json, os, random, re, ssl, struct, subprocess, sys, tempfile, threading
import types, unittest, urllib.error, urllib.request
from pathlib import Path
from unittest import mock

import numpy as np

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
sys.path.insert(0, str(HERE))
import build_snapshot as B  # noqa: E402
import ncio  # noqa: E402
import ncio_fixtures as FX  # noqa: E402


def load_check():
    """tools/check.py as a module of its own (it is not a package)."""
    spec = importlib.util.spec_from_file_location('ol_tools_check', REPO / 'tools' / 'check.py')
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ------------------------------------------------------------------ hand-built NetCDF
Z = bytes(1)


class Cdf:
    """NetCDF classic header pieces, so tests can write headers that no real writer would."""
    def __init__(self, ver=1):
        self.ver, self.w = ver, 8 if ver == 5 else 4

    def u(self, v):
        return struct.pack('>Q' if self.w == 8 else '>I', v)

    def name(self, s):
        b = s.encode()
        return self.u(len(b)) + b + Z * ((4 - len(b) % 4) % 4)

    def absent(self):
        return struct.pack('>i', 0) + self.u(0)

    def attrs(self, items):                       # (name, nc_type, raw bytes, count)
        out = struct.pack('>i', 12) + self.u(len(items))
        for n, t, raw, c in items:
            out += self.name(n) + struct.pack('>i', t) + self.u(c) + raw + Z * ((4 - len(raw) % 4) % 4)
        return out

    def var(self, name, ids, typ, vsize, begin, atts=None):
        out = self.name(name) + self.u(len(ids)) + b''.join(self.u(i) for i in ids) + (atts or self.absent())
        return out + struct.pack('>i', typ) + self.u(vsize) + struct.pack('>I' if self.ver == 1 else '>Q', begin)


def build(ver, numrecs, dims, specs, data=b''):
    """specs: (name, dim ids, nc_type, vsize, attribute bytes or None, offset into data). Returns the whole file."""
    c = Cdf(ver)

    def head(base):
        d = struct.pack('>i', 10) + c.u(len(dims)) + b''.join(c.name(n) + c.u(s) for n, s in dims) if dims else c.absent()
        v = struct.pack('>i', 11) + c.u(len(specs)) + b''.join(
            c.var(n, ids, t, vs, base + off, at) for n, ids, t, vs, at, off in specs)
        return b'CDF' + bytes([ver]) + c.u(numrecs) + d + c.absent() + v
    return head(len(head(0))) + data


F8, CHAR = 6, 2


class HostileNetCDF(unittest.TestCase):
    def test_huge_record_count_with_empty_records_does_not_spin(self):
        # ~100 bytes: 'z' has size 0, 't' is the record dimension and claims billions of records of 0 bytes each
        child = ("import sys; sys.path.insert(0, sys.argv[1]); import ncio\n"
                 "try: ncio.read_bytes(bytes.fromhex(sys.argv[2]))\n"
                 "except ncio.NetCDFError: pass\n")
        for ver, n in ((1, 0xFFFFFFFE), (5, 1 << 62)):
            data = build(ver, n, [('z', 0), ('t', 0)], [('v', [1, 0], F8, 0, None, 0)])
            r = subprocess.run([sys.executable, '-c', child, str(HERE), data.hex()], timeout=30, capture_output=True)
            self.assertEqual(r.returncode, 0, r.stderr.decode()[-300:])

    def test_overlapping_variables_cannot_multiply_a_small_file(self):
        # 40 variables all pointing at the same 64 KiB: 2.5 MB decoded from a file under 70 KB
        region = 1 << 16
        specs = [(f'v{i}', [0], F8, region, None, 0) for i in range(40)]
        data = build(1, 0, [('x', region // 8)], specs, b'\x01' * region)
        self.assertLess(len(data), region + 4096)
        with self.assertRaisesRegex(ncio.NetCDFError, 'overlap'):
            ncio.read_bytes(data)

    def test_dimension_sizes_from_the_file_cannot_wrap_or_overflow(self):
        for dims in ([('x', 1 << 32), ('y', 1 << 32)], [('x', 1 << 63)], [('x', (1 << 64) - 1)]):
            ids = list(range(len(dims)))
            data = build(5, 0, dims, [('v', ids, 1, 0, None, 0)])
            with self.assertRaises(ncio.NetCDFError):          # not a bare ValueError / OverflowError from numpy
                ncio.read_bytes(data)

    def test_odd_attribute_values_raise_netcdferror_not_a_traceback(self):
        c = Cdf(1)
        for att in (('valid_min', CHAR, b'abc', 3), ('scale_factor', CHAR, b'abc', 3), ('scale_factor', F8, b'', 0),
                    ('add_offset', CHAR, b'zz', 2)):
            data = build(1, 0, [('x', 2)], [('v', [0], F8, 16, c.attrs([att]), 0)], Z * 16)
            with self.assertRaises(ncio.NetCDFError, msg=att[0]):
                ncio.read_bytes(data)

    def test_time_far_outside_the_calendar_raises_netcdferror(self):
        var = {'attrs': {'units': 'seconds since 1970-01-01'}, 'data': np.array([1e30, 0.0])}
        with self.assertRaises(ncio.NetCDFError):
            ncio.cf_times(var)

    def test_truncated_and_corrupted_files_raise_only_netcdferror(self):
        seeds = [FX.ego_glider_bytes()['data'], FX.cf_profiles_bytes()['data']]
        seeds += [p.read_bytes() for p in sorted((REPO / 'samples').glob('*.nc'))[:2]]
        rnd = random.Random(2026)
        for _ in range(400):
            b = bytearray(rnd.choice(seeds))
            for _ in range(rnd.choice([1, 2, 3, 8])):
                b[rnd.randrange(min(len(b), 700))] = rnd.choice([0, 1, 0x7F, 0x80, 0xFF, rnd.randrange(256)])
            if rnd.random() < 0.25:
                del b[rnd.randrange(len(b)):]
            try:
                ncio.read_bytes(bytes(b))
            except ncio.NetCDFError:
                pass

    def test_real_files_and_round_trips_still_read(self):
        files = sorted((REPO / 'samples').glob('*.nc'))
        for p in files:
            if p.read_bytes()[:3] == b'CDF':
                self.assertTrue(ncio.read_bytes(p.read_bytes())['vars'], p.name)
        dims = {'t': None, 'z': 3}
        V = {'t': {'dims': ('t',), 'attrs': {}, 'data': np.arange(5.0)},
             'v': {'dims': ('t', 'z'), 'attrs': {}, 'data': np.arange(15.0).reshape(5, 3)},
             'w': {'dims': ('t',), 'attrs': {}, 'data': np.arange(5, dtype='i2')}}
        for fmt in ('CDF-1', 'CDF-2', 'CDF-5'):
            d = ncio.read_bytes(ncio.write_bytes(dims, V, None, fmt))
            self.assertEqual(d['vars']['v']['data'].tolist(), V['v']['data'].tolist(), fmt)
            self.assertEqual(d['vars']['w']['data'].tolist(), [0, 1, 2, 3, 4], fmt)


# ------------------------------------------------------------------ download path
class Resp:
    """A response: read(n) in pieces, optional headers."""
    def __init__(self, data, headers=None):
        self.data, self.pos, self.headers, self.reads = data, 0, headers or {}, 0

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self, n=-1):
        self.reads += 1
        end = len(self.data) if n is None or n < 0 else self.pos + n
        chunk, self.pos = self.data[self.pos:end], min(end, len(self.data))
        return chunk


def no_network():
    return mock.patch.object(urllib.request.OpenerDirector, 'open', side_effect=AssertionError('network used'))


class DownloadPath(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cache = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def fetcher(self, **k):
        return B.Fetcher(self.cache, fetch=True, sleep=lambda s: None, **k)

    def test_only_https_urls_are_downloaded(self):
        for url in ('http://tds.hycom.org/x', 'HTTP://tds.hycom.org/x', 'file:///C:/Windows/win.ini', 'ftp://tds.hycom.org/x',
                    'data:text/plain,hi', '//tds.hycom.org/x', 'tds.hycom.org/x'):
            with no_network() as op:
                with self.assertRaises(B.FetchError, msg=url):
                    self.fetcher().download(url, retries=1)
            op.assert_not_called()

    def test_hosts_outside_the_default_list_are_refused(self):
        for url in ('https://evil.example/x', 'https://tds.hycom.org.evil.example/x', 'https://evil.example/tds.hycom.org',
                    'https://tds.hycom.org@evil.example/x', 'https://evil.example:443@tds.hycom.org.evil.example/'):
            with no_network() as op:
                with self.assertRaises(B.FetchError, msg=url):
                    self.fetcher().download(url, retries=1)
            op.assert_not_called()
        with mock.patch.object(urllib.request.OpenerDirector, 'open', return_value=Resp(b'ok')):
            self.assertEqual(self.fetcher().download('https://tds.hycom.org/x', retries=1), b'ok')

    def test_allow_host_replaces_the_default_list(self):
        f = self.fetcher(allow_hosts=['Example.TEST'])
        with mock.patch.object(urllib.request.OpenerDirector, 'open', return_value=Resp(b'ok')):
            self.assertEqual(f.download('https://example.test/x', retries=1), b'ok')
        with no_network():
            with self.assertRaises(B.FetchError):
                f.download('https://tds.hycom.org/x', retries=1)

    def test_every_source_url_is_https_on_a_default_host(self):
        for url in (B.HYCOM, B.ARGO, B.ETOPO, B.IBTRACS, B.BGC):
            u = urllib.parse.urlsplit(url)
            self.assertEqual(u.scheme, 'https', url)
            self.assertIn(u.hostname, B.DEFAULT_HOSTS, url)

    def test_redirects_only_to_https_on_allowed_hosts(self):
        h = B._ConfinedRedirects({'tds.hycom.org'})
        req = urllib.request.Request('https://tds.hycom.org/a')
        for new in ('http://tds.hycom.org/x', 'https://evil.example/x', 'ftp://tds.hycom.org/x', 'file:///C:/Windows/win.ini',
                    'https://tds.hycom.org.evil.example/x'):
            with self.assertRaises(B.FetchError, msg=new):
                h.redirect_request(req, None, 302, 'Found', {}, new)
        again = h.redirect_request(req, None, 302, 'Found', {}, 'https://tds.hycom.org/b')
        self.assertEqual(again.full_url, 'https://tds.hycom.org/b')

    def test_a_real_redirect_is_not_followed_off_https(self):
        hits = []

        class Server(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                if self.path == '/start':
                    self.send_response(302)
                    self.send_header('Location', f'http://127.0.0.1:{self.server.server_port}/foreign')
                    self.send_header('Content-Length', '0')
                    self.end_headers()
                else:
                    hits.append(self.path)
                    self.send_response(200)
                    self.send_header('Content-Length', '2')
                    self.end_headers()
                    self.wfile.write(b'hi')
        srv = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Server)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            start = f'http://127.0.0.1:{srv.server_port}/start'
            direct = urllib.request.ProxyHandler({})                                   # never via a system proxy
            with urllib.request.build_opener(direct).open(start, timeout=10) as r:    # control: urllib follows it
                self.assertEqual(r.read(), b'hi')
            self.assertEqual(hits, ['/foreign'])
            hits.clear()
            with self.assertRaises(B.FetchError):
                urllib.request.build_opener(direct, B._ConfinedRedirects({'127.0.0.1'})).open(start, timeout=10)
            self.assertEqual(hits, [])
        finally:
            srv.shutdown()
            srv.server_close()

    def test_response_size_is_capped(self):
        f = self.fetcher()
        with mock.patch.object(B, 'MAX_DOWNLOAD', 1000):
            with self.assertRaises(B.FetchError):
                f._read_body(Resp(b'x' * 3000), 'u', 10)
            self.assertEqual(f._read_body(Resp(b'x' * 1000), 'u', 10), b'x' * 1000)
            announced = Resp(b'x' * 10, {'Content-Length': '99999'})
            with self.assertRaises(B.FetchError):
                f._read_body(announced, 'u', 10)
            self.assertEqual(announced.reads, 0)                  # refused before reading a byte

    def test_a_response_that_drips_in_forever_times_out(self):
        clock = iter([0.0, 1e9, 2e9, 3e9])
        with mock.patch.object(B, 'time', types.SimpleNamespace(monotonic=lambda: next(clock))):
            with self.assertRaises(TimeoutError):
                self.fetcher()._read_body(Resp(b'x' * (3 << 20)), 'u', 10)

    def test_tls_is_verified_and_a_custom_ca_bundle_is_still_honoured(self):
        seen = []
        real = ssl.create_default_context

        def spy(**k):
            seen.append(k)
            return real()
        f = self.fetcher(cafile='bundle.pem')
        with mock.patch.object(B.ssl, 'create_default_context', spy), \
                mock.patch.object(urllib.request.OpenerDirector, 'open', return_value=Resp(b'ok')):
            f.download('https://tds.hycom.org/x', retries=1)
        self.assertEqual(seen, [{'cafile': 'bundle.pem'}])
        self.assertEqual(f._ctx.verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(f._ctx.check_hostname)
        https = [h for h in f._opener.handlers if isinstance(h, urllib.request.HTTPSHandler)]
        self.assertTrue(https and all(getattr(h, '_context', None) is f._ctx for h in https))

    def test_cache_names_cannot_leave_the_cache(self):
        outer = tempfile.mkdtemp()
        cache = os.path.join(outer, 'cache')
        F = B.Fetcher(cache)
        for name in ('../evil.txt', 'a/../../evil.txt', 'hycom_obs/../../evil.txt', '/evil.txt', 'C:/evil.txt', 'a\\..\\evil.txt',
                     '..\\..\\evil.txt', '', 'a//b', '.', './evil.txt', 'a/./b', 'evil\0.txt'):
            with self.assertRaises(ValueError, msg=repr(name)):
                F.path(name)
            with self.assertRaises(ValueError, msg=repr(name)):
                F.store(name, b'pwned', 'https://tds.hycom.org/x')
        self.assertEqual(os.listdir(outer), [])
        for name in ('x.txt', 'hycom_obs/2901337_307_t4196.txt', 'hycom_salinity_t0_k0-0.txt', 'bgc_sample_2902120_227.nc'):
            self.assertTrue(os.path.abspath(F.path(name)).startswith(os.path.abspath(cache) + os.sep), name)

    def test_cached_files_are_checked_against_the_manifest(self):
        good = b'-' * 45 + b'\nx[2]\n1.0, 2.0\n'
        tampered = b'-' * 45 + b'\nx[2]\n9.0, 9.0\n'
        path = os.path.join(self.cache, 'x.txt')
        with open(path, 'wb') as f:
            f.write(good)
        # no manifest entry (a cache from before manifests existed): accepted
        self.assertEqual(list(B.Fetcher(self.cache).get('u', 'x.txt', B.parse_ascii)['x']), [1.0, 2.0])
        with open(os.path.join(self.cache, 'manifest.json'), 'w', encoding='utf-8') as f:
            json.dump({'x.txt': {'url': 'u', 'sha256': B.sha256(good), 'bytes': len(good)}}, f)
        self.assertEqual(list(B.Fetcher(self.cache).get('u', 'x.txt', B.parse_ascii)['x']), [1.0, 2.0])
        with open(path, 'wb') as f:
            f.write(tampered)                                     # changed after it was downloaded
        with self.assertRaisesRegex(ValueError, 'manifest'):
            B.Fetcher(self.cache).get('u', 'x.txt', B.parse_ascii)

    def test_a_fresh_download_is_recorded_and_then_accepted(self):
        good = b'-' * 45 + b'\nx[2]\n1.0, 2.0\n'
        F = self.fetcher(allow_hosts=['example.test'])
        with mock.patch.object(urllib.request.OpenerDirector, 'open', return_value=Resp(good)):
            F.get('https://example.test/x', 'x.txt', B.parse_ascii)
        self.assertEqual(list(F.get('https://example.test/x', 'x.txt', B.parse_ascii)['x']), [1.0, 2.0])
        self.assertEqual(list(B.Fetcher(self.cache).get('u', 'x.txt', B.parse_ascii)['x']), [1.0, 2.0])


# ------------------------------------------------------------------ what the pipeline writes and parses
class HostileData(unittest.TestCase):
    def test_script_breakouts_never_reach_the_json_unescaped(self):
        nasty = '</script><!--<script>alert(1)</script>\u2028\u2029<!-- -->'
        out = B.serialise({'name': nasty, 'k</script>': 1})
        text = out.decode('ascii')
        for bad in ('</', '<!--', '\u2028', '\u2029'):
            self.assertNotIn(bad, text)
        self.assertEqual(json.loads(text)['name'], nasty)

    def test_a_declared_shape_cannot_wrap_to_zero(self):
        body = ('-' * 45 + '\nx[4294967296][4294967296]\n').encode()
        with self.assertRaises(ValueError):
            B.parse_ascii(body)

    def test_platform_number_must_be_ascii_digits(self):
        names = B.ARGO_COLS.split(',')
        row = [''] * len(names)
        row[names.index('platform_number')] = '\u0662\u0669\u0660\u0661\u0663\u0663\u0667'      # Arabic-Indic digits
        row[names.index('cycle_number')] = '1'
        row[names.index('time')] = '2020-05-04T12:00:00Z'
        with self.assertRaisesRegex(ValueError, 'platform number'):
            B.select_argo(names, [row])

    def test_errors_from_hostile_downloads_end_the_build_cleanly(self):
        with mock.patch.object(B, 'build', side_effect=OverflowError('cannot convert float infinity to integer')):
            with mock.patch('sys.stderr'):
                self.assertEqual(B.main(['--cache', tempfile.gettempdir(), '--out', os.devnull]), 1)


# ------------------------------------------------------------------ tools/pngdiff.py
def make_png(w, h, ctype=6, rows=None, extra=b''):
    """A PNG; rows default to all-zero filtered scanlines. `extra` is appended to the compressed image data."""
    import zlib
    bpp = 4 if ctype == 6 else 3
    raw = rows if rows is not None else (b'\0' + bytes(w * bpp)) * h

    def chunk(t, body):
        return struct.pack('>I', len(body)) + t + body + struct.pack('>I', zlib.crc32(t + body))
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', w, h, 8, ctype, 0, 0, 0)) +
            chunk(b'IDAT', zlib.compress(raw) + extra) + chunk(b'IEND', b''))


class HostilePNG(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location('ol_tools_pngdiff', REPO / 'tools' / 'pngdiff.py')
        cls.P = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.P)

    def decode(self, data):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, 'x.png')
            with open(p, 'wb') as f:
                f.write(data)
            return self.P.decode(p)

    def test_legit_png_still_decodes(self):
        w, h, bpp, rows = self.decode(make_png(3, 2, 6, rows=b'\0' + bytes(range(12)) + b'\0' + bytes(range(12, 24))))
        self.assertEqual((w, h, bpp), (3, 2, 4))
        self.assertEqual(rows[1], bytes(range(12, 24)))
        self.assertEqual(self.decode(make_png(5, 4, 2))[:3], (5, 4, 3))

    def test_a_decompression_bomb_is_refused_without_inflating_it(self):
        import tracemalloc
        bomb = make_png(4, 4, rows=bytes(64 << 20))                  # 4 x 4 pixels, but 64 MiB of image data
        tracemalloc.start()
        try:
            with self.assertRaises(ValueError):
                self.decode(bomb)
            peak = tracemalloc.get_traced_memory()[1]
        finally:
            tracemalloc.stop()
        self.assertLess(peak, 16 << 20)

    def test_header_dimensions_cannot_demand_gigabytes(self):
        for w, h in ((100000, 100000), (1 << 20, 1 << 20), (0, 5)):
            with self.assertRaises(ValueError, msg=f'{w}x{h}'):
                self.decode(make_png(w, h, rows=b'\0' * 64))

    def test_truncated_files_raise_valueerror(self):
        good = make_png(2, 2)
        for cut in (8, 12, 20, 33, len(good) - 30):
            with self.assertRaises(ValueError, msg=str(cut)):
                self.decode(good[:cut])


# ------------------------------------------------------------------ repository guards
class RepoGuards(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.check = load_check()

    def test_vendored_three_js_is_the_pinned_official_release(self):
        import hashlib
        self.assertEqual(set(self.check.VENDORED), {'vendor/three.r128.min.js', 'vendor/three.LICENSE'})
        for rel, want in self.check.VENDORED.items():
            self.assertEqual(hashlib.sha256((REPO / rel).read_bytes()).hexdigest(), want, rel)

    def test_secret_patterns_catch_tokens_and_ignore_prose(self):
        pat = self.check.SECRETS
        tokens = ['AK' + 'IA' + 'ABCDEFGHIJKLMNOP', 'gh' + 'p_' + 'a1' * 18, 'github' + '_pat_' + 'A1' * 11,
                  'xo' + 'xb-1234567890-abcdef', 'AI' + 'za' + 'Sy' * 17 + 'x', 'sk' + '-' + 'a1' * 16,
                  '-----BEGIN ' + 'RSA PRIVATE KEY-----', 'glpat' + '-' + 'a1' * 10, 'npm' + '_' + 'a1' * 18]
        for t in tokens:
            self.assertTrue(pat.search(f'token = "{t}"'), t[:6])
        for ok in ('task-based-workflow-description-in-detail-for-everyone', 'desk-top-notes-for-the-dev-team-and-more',
                   'sha256 ' + 'ab12' * 16, 'ghp_short', 'AKIA lower', '-----BEGIN CERTIFICATE-----'):
            self.assertFalse(pat.search(ok), ok)
        for line in (REPO / 'vendor' / 'three.r128.min.js').read_text(encoding='utf-8').splitlines():
            self.assertFalse(pat.search(line))

    def test_workflow_checker_flags_weak_workflows(self):
        c = self.check
        good = ('on: push\npermissions:\n  contents: read\njobs:\n  a:\n    steps:\n      - uses: actions/checkout@' + 'a' * 40 +
                '\n        with:\n          persist-credentials: false\n')
        weak = {'no_permissions': good.replace('permissions:\n  contents: read\n', ''),
                'write_permissions': good.replace('contents: read', 'contents: write'),
                'target': good.replace('on: push', 'on:\n  pull_request_target:'),
                'workflow_run': good.replace('on: push', 'on:\n  workflow_run:\n    workflows: [x]'),
                'kept_credentials': good.replace('          persist-credentials: false\n', '').replace('        with:\n', '')}
        with tempfile.TemporaryDirectory() as d:
            wf = Path(d) / '.github' / 'workflows'
            wf.mkdir(parents=True)
            with mock.patch.object(c, 'REPO', Path(d)):
                (wf / 'ok.yml').write_text(good, encoding='utf-8')
                self.assertEqual(c.workflow_problems(), [])
                for name, text in weak.items():
                    (wf / 'ok.yml').write_text(text, encoding='utf-8')
                    self.assertTrue(c.workflow_problems(), name)

    def test_ci_workflow_is_locked_down(self):
        self.assertEqual(self.check.workflow_problems(), [])
        text = (REPO / '.github' / 'workflows' / 'ci.yml').read_text(encoding='utf-8')
        self.assertRegex(text, r'(?m)^permissions:\n  contents: read\n')
        self.assertIn('timeout-minutes:', text)
        uses = re.findall(r'(?m)^\s*(?:-\s*)?uses:\s*(\S+)', text)
        self.assertTrue(uses)
        for u in uses:
            self.assertRegex(u, r'^[\w.-]+/[\w./-]+@[0-9a-f]{40}$', f'{u} must be pinned to a full commit SHA')

    def test_gitignore_blocks_secret_files(self):
        def ignored(name):
            try:
                return subprocess.run(['git', 'check-ignore', '-q', name], cwd=REPO).returncode == 0
            except FileNotFoundError:
                self.skipTest('git is not installed')
        for name in ('.env', '.env.local', 'server.pem', 'ca-bundle.pem', 'private.key', 'cert.pfx', 'id_rsa', '.npmrc', '.pypirc',
                     'secrets.json', 'gcp-credentials.json', 'service-account-prod.json', '.ssh/config', '.aws/credentials'):
            self.assertTrue(ignored(name), name)
        for name in ('.env.example', 'README.md', 'src/js/main.js'):
            self.assertFalse(ignored(name), name)

    def test_no_dangerous_python_constructs_anywhere_in_the_repo(self):
        skip = {'cache', 'dist', '.git', '__pycache__', 'output', 'graphify-out', 'backup', 'node_modules'}
        found = []
        for p in REPO.rglob('*.py'):
            if skip & set(p.relative_to(REPO).parts):
                continue
            tree = ast.parse(p.read_text(encoding='utf-8'), str(p))
            for n in ast.walk(tree):
                what = None
                if isinstance(n, ast.Call):
                    f = n.func
                    dotted = f.id if isinstance(f, ast.Name) else (
                        f'{f.value.id}.{f.attr}' if isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name) else '')
                    if dotted in ('eval', 'exec', 'os.system', 'os.popen', 'tempfile.mktemp', 'pickle.load', 'pickle.loads',
                                  'marshal.load', 'marshal.loads', 'yaml.load', 'ssl._create_unverified_context'):
                        what = dotted
                    if isinstance(f, ast.Attribute) and f.attr == 'extractall':
                        what = 'extractall'
                    for kw in n.keywords:
                        if kw.arg in ('shell', 'verify') and isinstance(kw.value, ast.Constant) and kw.value.value is (kw.arg == 'shell'):
                            what = f'{kw.arg}={kw.value.value}'
                elif isinstance(n, ast.Attribute) and n.attr == 'CERT_NONE':
                    what = 'CERT_NONE'
                elif isinstance(n, ast.Assign) and isinstance(n.value, ast.Constant) and n.value.value is False and any(
                        isinstance(t, ast.Attribute) and t.attr == 'check_hostname' for t in n.targets):
                    what = 'check_hostname = False'
                if what:
                    found.append(f'{p.relative_to(REPO).as_posix()}:{n.lineno}: {what}')
        self.assertEqual(found, [])


if __name__ == '__main__':
    unittest.main()
