#!/usr/bin/env python3
"""OceanLens 3D data server: the built app, a REST API, OGC WMS 1.3.0 and OGC WCS 2.0.1.
Python standard library and numpy only; NetCDF through data-pipeline/ncio.py.

    python server/serve.py                                   app + data/*.json on http://127.0.0.1:8080/
    python server/serve.py --netcdf model.nc --port 8000     add a CF NetCDF-3 model file as a dataset
    python server/serve.py --host 0.0.0.0 --cors '*'         listen on every interface, allow any origin

Routes: / (the app), /api/v1/..., /wms, /wcs, /ows, /healthz. docs/API.md has the details and example URLs.
"""
import argparse, base64, contextlib, gzip, json, math, re, socket, struct, sys, threading, time, zlib
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from xml.sax.saxutils import escape, quoteattr

import numpy as np

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / 'data-pipeline'))
try:
    import ncio
except ImportError:          # WCS NetCDF output and --netcdf input need it; everything else works without
    ncio = None

VERSION = (REPO / 'VERSION').read_text().strip() if (REPO / 'VERSION').exists() else 'dev'
MAX_PIXELS, MAX_SIDE, MAX_CELLS = 4_194_304, 2048, 5_000_000
HEAVY_SLOTS = 4                   # GetMap and GetCoverage requests served at once; more get 503
MAP_BLOCK_ROWS = 256              # GetMap samples this many rows at a time, to bound memory
LOOPBACK = ('127.0.0.1', '::1', 'localhost')
DAY = 86400.0

# Known variables. Keys match the app; units are CF/UDUNITS strings, `uom` is the UCUM code WCS wants.
VARS = {
    'temp':  dict(label='Temperature', units='degC', uom='Cel', palette='temperature', range=(6, 32),
                  standard_name='sea_water_temperature'),
    'sal':   dict(label='Salinity', units='1e-3', uom='1e-3', palette='salinity', range=(32.6, 36.8),
                  standard_name='sea_water_salinity'),
    'u':     dict(label='Eastward current', units='m s-1', uom='m/s', palette='speed', range=(-1, 1),
                  standard_name='eastward_sea_water_velocity'),
    'v':     dict(label='Northward current', units='m s-1', uom='m/s', palette='speed', range=(-1, 1),
                  standard_name='northward_sea_water_velocity'),
    'speed': dict(label='Current speed', units='m s-1', uom='m/s', palette='speed', range=(0, 1),
                  standard_name='sea_water_speed'),
    'chl':   dict(label='Chlorophyll', units='mg m-3', uom='mg/m3', palette='chlorophyll', range=(0.05, 10),
                  scale='log', standard_name='mass_concentration_of_chlorophyll_a_in_sea_water'),
    'oxy':   dict(label='Dissolved oxygen', units='umol kg-1', uom='umol/kg', palette='speed', range=(0, 250),
                  standard_name='moles_of_oxygen_per_unit_mass_in_sea_water'),
}
STANDARD = {v['standard_name']: k for k, v in VARS.items()}
STANDARD.update({'sea_water_potential_temperature': 'temp', 'sea_water_conservative_temperature': 'temp',
                 'sea_water_practical_salinity': 'sal', 'baroclinic_eastward_sea_water_velocity': 'u',
                 'baroclinic_northward_sea_water_velocity': 'v',
                 'mole_concentration_of_dissolved_molecular_oxygen_in_sea_water': 'oxy'})
ARGO_KEYS = {'T': 'temp', 'S': 'sal', 'C': 'chl', 'O': 'oxy'}     # snapshot argo arrays -> meta.quant keys


class ApiError(Exception):
    def __init__(self, status, code, message, locator=None):
        super().__init__(message)
        self.status, self.code, self.message, self.locator = status, code, message, locator


def load_palettes():
    """The app's palettes (src/js/palettes.js): name -> (256, 3) uint8, entry i is the colour at t = i/255."""
    src = (REPO / 'src' / 'js' / 'palettes.js').read_text(encoding='utf-8')
    out = {}
    for m in re.finditer(r"(\w+):\s*\{label:'[^']*',\s*lut:'([0-9a-fA-F]+)'", src):
        out[m.group(1)] = np.frombuffer(bytes.fromhex(m.group(2)), np.uint8).reshape(-1, 3)
    if not out:
        raise SystemExit('no palettes found in src/js/palettes.js')
    return out


PALETTES = load_palettes()


# ---- time ----------------------------------------------------------------------------------------------------
def iso(sec):
    return datetime.fromtimestamp(float(sec), timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def parse_iso(s):
    s = s.strip().strip('"\'')
    m = re.fullmatch(r'(\d{4})-(\d{2})-(\d{2})(?:[T ](\d{2}):(\d{2})(?::(\d{2})(?:\.\d+)?)?)?\s*(?:Z|[+-]00:?00)?', s)
    if not m:
        raise ValueError(f'not an ISO 8601 UTC time: {s!r}')
    y, mo, d, h, mi, se = (int(g) if g else 0 for g in m.groups())
    return datetime(y, mo, d, h, mi, se, tzinfo=timezone.utc).timestamp()


def cf_times(values, units):
    """CF 'X since Y' numbers -> epoch seconds."""
    m = re.match(r'\s*(\w+)\s+since\s+(\d{1,4})-(\d{1,2})-(\d{1,2})(?:[ T](\d{1,2}):(\d{1,2})(?::(\d{1,2}))?)?', units or '')
    if not m:
        raise ValueError(f'time units {units!r} are not "<unit> since <date>"')
    per = {'second': 1, 'minute': 60, 'hour': 3600, 'day': DAY}
    unit = m.group(1).lower().rstrip('s')
    if unit not in per:
        raise ValueError(f'time unit {m.group(1)!r} is not supported')
    base = datetime(*(int(g) if g else 0 for g in m.groups()[1:]), tzinfo=timezone.utc).timestamp()
    return base + np.asarray(values, float) * per[unit]


# ---- datasets ------------------------------------------------------------------------------------------------
class Dataset:
    """A gridded ocean state on a rectilinear grid, with optional profiles and a storm track.
    fields[key] is float32 [time][depth][lat][lon], NaN where there is no water or no data."""

    def __init__(self, id, title, source, lat, lon, depths, tsec, fields, info, profiles=(), track=None,
                 attrs=None, raw=None):
        self.id, self.title, self.source = id, title, source
        self.lat, self.lon = np.asarray(lat, float), np.asarray(lon, float)
        self.depths, self.tsec = np.asarray(depths, float), np.asarray(tsec, float)
        self.times = [iso(t) for t in self.tsec]
        self.fields, self.info = fields, info
        self.profiles, self.track, self.attrs, self.raw = list(profiles), track, attrs or {}, raw
        if len(self.lat) < 2 or len(self.lon) < 2:
            raise ValueError(f'{id}: the grid needs at least 2 latitudes and 2 longitudes')

    @property
    def bbox(self):
        return [float(self.lon[0]), float(self.lat[0]), float(self.lon[-1]), float(self.lat[-1])]

    def summary(self, base=''):
        u = f'{base}/api/v1/datasets/{self.id}'
        links = {'meta': u + '/meta', 'field': u + '/field', 'column': u + '/column', 'profiles': u + '/profiles'}
        if self.raw is not None:
            links['snapshot'] = u + '/snapshot'
        if self.track:
            links['track'] = u + '/track'
        return {'id': self.id, 'title': self.title, 'source': self.source, 'times': self.times,
                'depths': [num(z) for z in self.depths], 'bbox': self.bbox, 'profiles': len(self.profiles),
                'variables': [self.var_summary(k) for k in self.fields], 'links': links}

    def var_summary(self, key):
        i = self.info[key]
        return {'key': key, 'label': i['label'], 'units': i['units'], 'standard_name': i.get('standard_name'),
                'range': [num(x) for x in i['range']], 'scale': i.get('scale', 'linear'),
                'palette': palette_name(i), 'layer': f'{self.id}.{key}'}

    @property
    def time_tolerance(self):
        """How far (s) a requested time may be from the nearest analysis: 1.5 days, or half the step if larger."""
        return max(1.5 * DAY, float(np.median(np.diff(self.tsec))) / 2 if len(self.tsec) > 1 else 0)

    def time_index(self, value):
        if value is None or str(value).strip().lower() in ('', 'current', 'default'):
            return 0
        value = str(value).strip()
        if value.isdigit():
            i = int(value)
            if i >= len(self.times):
                raise ApiError(400, 'InvalidDimensionValue', f'time index {i} is past the last of {len(self.times)} times', 'time')
            return i
        try:
            sec = parse_iso(value)
        except ValueError as e:
            raise ApiError(400, 'InvalidDimensionValue', str(e), 'time')
        i = int(np.argmin(np.abs(self.tsec - sec)))
        tol = self.time_tolerance
        if abs(self.tsec[i] - sec) > tol:
            raise ApiError(400, 'InvalidDimensionValue', f'no time within {tol / DAY:.1f} days of {value}; '
                           f'times run {self.times[0]} to {self.times[-1]}', 'time')
        return i

    def layer(self, key, ti, depth):
        """2D field at one depth (m, positive down), linear between levels."""
        f, D = self.fields[key][ti], self.depths
        if not np.isfinite(depth) or depth > D[-1] + 1e-6:
            raise ApiError(400, 'InvalidDimensionValue', f'depth {depth:g} m is below the deepest level ({D[-1]:g} m)', 'depth')
        if len(D) == 1 or depth <= D[0]:
            return f[0]
        k = int(min(max(np.searchsorted(D, depth, 'right') - 1, 0), len(D) - 2))
        fz = min(max((depth - D[k]) / (D[k + 1] - D[k]), 0.0), 1.0)
        return blend([f[k], f[k + 1]], [1 - fz, fz])

    def sample(self, grid2d, lats, lons):
        """Bilinear over the grid nodes that hold data; NaN where under half the weight has data or outside."""
        lats, lons = np.asarray(lats, float), np.asarray(lons, float)
        fy = np.interp(lats, self.lat, np.arange(len(self.lat)), left=np.nan, right=np.nan)
        fx = np.interp(lons, self.lon, np.arange(len(self.lon)), left=np.nan, right=np.nan)
        ok = np.isfinite(fy) & np.isfinite(fx)
        fy, fx = np.where(ok, fy, 0), np.where(ok, fx, 0)
        i = np.clip(np.floor(fy).astype(int), 0, len(self.lat) - 2)
        j = np.clip(np.floor(fx).astype(int), 0, len(self.lon) - 2)
        wy, wx = fy - i, fx - j
        out = blend([grid2d[i, j], grid2d[i, j + 1], grid2d[i + 1, j], grid2d[i + 1, j + 1]],
                    [(1 - wy) * (1 - wx), (1 - wy) * wx, wy * (1 - wx), wy * wx])
        return np.where(ok, out, np.nan)


def blend(arrays, weights):
    num_, den = 0.0, 0.0
    for a, w in zip(arrays, weights):
        good = np.isfinite(a)
        num_ = num_ + np.where(good, a, 0) * w
        den = den + good * w
    with np.errstate(invalid='ignore', divide='ignore'):
        return np.where(np.asarray(den) >= 0.5 - 1e-9, num_ / np.where(den > 0, den, 1), np.nan)


def num(x, nd=4):
    """JSON-safe number: NaN -> None, rounded."""
    x = float(x)
    return None if not math.isfinite(x) else round(x, nd)


def palette_name(info):
    p = info.get('palette', 'temperature')
    return p if p in PALETTES else 'temperature'


def safe_id(stem):
    s = re.sub(r'[^A-Za-z0-9_-]+', '-', stem).strip('-') or 'dataset'
    return s if s[0].isalpha() else 'd' + s


def var_info(key, attrs=None, data=None):
    attrs = attrs or {}
    base = dict(VARS.get(key, {}))
    info = {'label': base.get('label') or attrs.get('long_name') or key,
            'units': str(attrs.get('units') or base.get('units') or '1'),
            'standard_name': attrs.get('standard_name') or base.get('standard_name'),
            'palette': base.get('palette', 'temperature'), 'scale': base.get('scale', 'linear'),
            'uom': base.get('uom') or str(attrs.get('units') or '1')}
    finite = data[np.isfinite(data)] if data is not None else np.array([])
    info['data_range'] = [float(finite.min()), float(finite.max())] if finite.size else [None, None]
    if 'range' in base:
        info['range'] = base['range']
    elif finite.size:
        lo, hi = np.percentile(finite, [2, 98])
        info['range'] = (float(lo), float(hi if hi > lo else lo + 1))
    else:
        info['range'] = (0, 1)
    return info


def load_snapshot(path):
    """A schema-1 snapshot (data/*.json), decoded the way src/js/data-real.js decodes it."""
    raw = Path(path).read_bytes()
    d = json.loads(raw)
    if d.get('schema') != 1:
        raise ValueError(f'{path}: schema {d.get("schema")}, this server reads schema 1')
    m = d['meta']
    nt, nv, nk, ny, nx = len(m['times']), len(m['vars']), len(m['depths']), m['nlat'], m['nlon']
    q = np.frombuffer(base64.b64decode(d['model']), '<i2').reshape(nt, nv, nk, ny, nx)
    wet = None
    if d.get('mask'):
        bits = np.unpackbits(np.frombuffer(base64.b64decode(d['mask']['wet']), np.uint8))
        wet = bits[:nk * ny * nx].reshape(nk, ny, nx).astype(bool)
    fields, info = {}, {}
    for vi, key in enumerate(m['vars']):
        off, sc = m['quant'][key]
        f = (off + q[:, vi].astype(np.float64) * sc).astype(np.float32)
        f[q[:, vi] == m.get('missing', -32768)] = np.nan
        if wet is not None:             # cells without model water carry neighbour fills; the API reports no value
            f[:, ~wet] = np.nan
        fields[key] = f
    if 'u' in fields and 'v' in fields:
        fields['speed'] = np.hypot(fields['u'], fields['v'])
    for key, f in fields.items():
        info[key] = var_info(key, data=f[0])
    lat = m['lat0'] + m['step'] * np.arange(ny)
    lon = m['lon0'] + m['step'] * np.arange(nx)
    tsec = [parse_iso(t) for t in m['times']]
    profiles = snapshot_profiles(d)
    track = None
    if d.get('track'):
        T = d['track']
        track = {'name': T.get('name'), 'fields': T['fields'],
                 'fixes': [[iso(parse_iso(f[0]))] + list(f[1:]) for f in T['fixes']]}
    attrs = {k: m[k] for k in ('sources', 'citations', 'provenance') if k in m}
    return Dataset(safe_id(Path(path).stem), m.get('title', Path(path).stem), 'snapshot', lat, lon, m['depths'],
                   tsec, fields, info, profiles, track, attrs, raw)


def snapshot_profiles(d):
    A, m = d.get('argo'), d['meta']
    if not A:
        return []
    levels, nL, miss = A['levels'], len(A['levels']), m.get('missing', -32768)
    arrays = {}
    for k, key in ARGO_KEYS.items():
        if isinstance(A.get(k), str) and key in m['quant']:
            q = np.frombuffer(base64.b64decode(A[k]), '<i2').reshape(-1, nL)
            off, sc = m['quant'][key]
            arrays[key] = np.where(q == miss, np.nan, off + q.astype(np.float64) * sc)
    out = []
    for n, p in enumerate(A['profiles']):
        prof = {'id': f"{p['w']}_{int(p['c']):03d}", 'platform': str(p['w']), 'type': 'argo', 'cycle': p['c'],
                'lat': p['la'], 'lon': p['lo'], 'time': iso(parse_iso(p['t'])), 'mode': p.get('m')}
        cols = {key: a[n].copy() for key, a in arrays.items()}
        if p.get('sq') == 0 and 'sal' in cols:      # salinity failed delayed-mode QC: report none
            cols['sal'][:] = np.nan
            prof['sal_qc'] = 'failed'
        have = np.zeros(nL, bool)
        for a in cols.values():
            have |= np.isfinite(a)
        last = int(np.nonzero(have)[0][-1]) + 1 if have.any() else 0
        prof['depth'] = levels[:last]
        for key, a in cols.items():
            if np.isfinite(a[:last]).any():
                prof[key] = [num(x) for x in a[:last]]
        out.append(prof)
    return out


def load_netcdf(path):
    """A CF NetCDF-3 file on a rectilinear lat/lon grid (optional depth and time axes)."""
    if ncio is None:
        raise SystemExit('--netcdf needs data-pipeline/ncio.py')
    nc = ncio.read(str(path))
    V = nc['vars']

    def coord(names, std, axis, units=()):
        one = {n: v for n, v in V.items() if len(v['dims']) == 1}
        for test in (lambda n, a: a.get('standard_name') in std, lambda n, a: str(a.get('axis', '')).upper() == axis,
                     lambda n, a: str(a.get('units', '')) in units, lambda n, a: n.lower() in names):
            for n, v in one.items():
                if test(n, v['attrs']):
                    return n
        return None

    la = coord(('lat', 'latitude', 'nav_lat'), ('latitude',), 'Y', ('degrees_north', 'degree_north', 'degree_N'))
    lo = coord(('lon', 'longitude', 'nav_lon'), ('longitude',), 'X', ('degrees_east', 'degree_east', 'degree_E'))
    zn = coord(('depth', 'lev', 'deptht', 'z', 'level'), ('depth',), 'Z')
    tn = coord(('time', 't'), ('time',), 'T')
    if not la or not lo:
        raise ValueError(f'{path}: no latitude/longitude coordinate variables found')
    lat, lon = np.asarray(V[la]['data'], float), np.asarray(V[lo]['data'], float)
    depth = np.asarray(V[zn]['data'], float) if zn else np.array([0.0])
    if zn and (str(V[zn]['attrs'].get('positive', '')).lower() == 'up' or (depth <= 0).all() and (depth < 0).any()):
        depth = -depth
    tsec = cf_times(V[tn]['data'], V[tn]['attrs'].get('units')) if tn else np.array([0.0])
    axes = {V[la]['dims'][0]: 'y', V[lo]['dims'][0]: 'x'}
    if zn:
        axes[V[zn]['dims'][0]] = 'z'
    if tn:
        axes[V[tn]['dims'][0]] = 't'
    fields, info = {}, {}
    for name, v in V.items():
        dims = tuple(v['dims'])
        if name in (la, lo, zn, tn) or 'y' not in [axes.get(x) for x in dims] or 'x' not in [axes.get(x) for x in dims]:
            continue
        a = np.asarray(v['data'], np.float32)
        if a.dtype.kind not in 'fiu':
            continue
        extra = [i for i, x in enumerate(dims) if x not in axes]
        if any(a.shape[i] != 1 for i in extra):
            continue
        a = a.squeeze(axis=tuple(extra)) if extra else a
        dims = [axes[x] for x in dims if x in axes]
        for ax in ('t', 'z'):
            if ax not in dims:
                a, dims = a[np.newaxis], [ax] + dims
        a = np.transpose(a, [dims.index(x) for x in 'tzyx'])
        key = STANDARD.get(v['attrs'].get('standard_name')) or re.sub(r'\W', '_', name)
        if key in fields:
            key = re.sub(r'\W', '_', name)
        fields[key] = np.ascontiguousarray(a, np.float32)
        info[key] = var_info(key, v['attrs'], a[0])
    if not fields:
        raise ValueError(f'{path}: no variables on the latitude/longitude grid')
    order = [np.argsort(x) for x in (tsec, depth, lat, lon)]
    tsec, depth, lat, lon = (x[o] for x, o in zip((tsec, depth, lat, lon), order))
    for key in fields:
        fields[key] = fields[key][order[0]][:, order[1]][:, :, order[2]][:, :, :, order[3]]
    if 'u' in fields and 'v' in fields and 'speed' not in fields:
        fields['speed'] = np.hypot(fields['u'], fields['v'])
        info['speed'] = var_info('speed', data=fields['speed'][0])
    g = nc.get('attrs', {})
    attrs = {k: str(g[k]) for k in ('title', 'institution', 'source', 'history', 'references', 'Conventions') if k in g}
    return Dataset(safe_id(Path(path).stem), str(g.get('title') or Path(path).stem), 'netcdf', lat, lon, depth,
                   tsec, fields, info, attrs=attrs)


# ---- images --------------------------------------------------------------------------------------------------
def png_rgba(rgba):
    """RGBA uint8 (h, w, 4) -> PNG bytes (filter 0, zlib)."""
    h, w = rgba.shape[:2]
    rows = np.zeros((h, w * 4 + 1), np.uint8)
    rows[:, 1:] = rgba.reshape(h, w * 4)

    def chunk(tag, data):
        return struct.pack('>I', len(data)) + tag + data + struct.pack('>I', zlib.crc32(tag + data) & 0xffffffff)
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', w, h, 8, 6, 0, 0, 0)) +
            chunk(b'IDAT', zlib.compress(rows.tobytes(), 6)) + chunk(b'IEND', b''))


def colour_index(values, lo, hi, log=False):
    """Value -> palette entry 0..255, clamped at both ends; log10 positions when log (values <= 0 take entry 0)."""
    with np.errstate(invalid='ignore', divide='ignore'):
        if log:
            t = (np.log10(np.maximum(values, lo * 1e-6)) - math.log10(lo)) / (math.log10(hi) - math.log10(lo))
        else:
            t = (values - lo) / (hi - lo)
    return np.clip(np.rint(np.nan_to_num(t, nan=0.0) * 255), 0, 255).astype(np.intp)


# ---- the server ----------------------------------------------------------------------------------------------
class OceanServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, addr, datasets, app=None, cors=(), public_url=None, quiet=False):
        super().__init__(addr, Handler)
        self.datasets = {d.id: d for d in datasets}
        self.app, self.cors, self.public_url, self.quiet = app, tuple(cors), public_url, quiet
        self.gz_cache, self.pages = {}, {}
        self.default_id = embedded_id(self.datasets, app)
        self.heavy = threading.BoundedSemaphore(HEAVY_SLOTS)
        # on a loopback bind, only loopback Host names: a page on another site cannot reach the API through a
        # DNS name rebound to 127.0.0.1
        self.check_host = addr[0] in LOOPBACK and not public_url

    def shutdown_request(self, request):
        # Lingering close: send FIN, then wait (up to 5 s) for the client to finish reading and close. Closing at
        # once can reset the connection on Windows and cut off the tail of a large response.
        try:
            request.shutdown(socket.SHUT_WR)
            request.settimeout(5)
            while request.recv(65536):
                pass
        except OSError:
            pass
        self.close_request(request)

    def layer_ref(self, name):
        """'dataset.var' (or 'dataset/var') -> (Dataset, key)."""
        name = name or ''
        for sep in ('.', '/', ':'):
            if sep in name:
                ds_id, key = name.rsplit(sep, 1)
                ds = self.datasets.get(ds_id)
                if ds and key in ds.fields:
                    return ds, key
        return None, None


class Handler(BaseHTTPRequestHandler):
    server_version = 'OceanLens/' + VERSION
    sys_version = ''
    protocol_version = 'HTTP/1.1'
    timeout = 30              # idle keep-alive connections are closed after this many seconds
    _head = False

    def log_message(self, fmt, *args):
        if not self.server.quiet:
            sys.stderr.write('%s %s\n' % (self.log_date_time_string(), fmt % args))

    def do_HEAD(self):
        self.do_GET(head=True)

    def do_OPTIONS(self):
        if not self.host_ok():
            return self.misdirected()
        self.send_response(204)
        self._common_headers()
        self.send_header('Access-Control-Allow-Methods', 'GET, HEAD, OPTIONS')
        self.send_header('Access-Control-Max-Age', '600')
        self.send_header('Content-Length', '0')
        self.end_headers()

    def do_POST(self):
        self.send_json({'error': {'code': 'MethodNotAllowed', 'message': 'only GET, HEAD and OPTIONS are served'}}, 405)

    do_PUT = do_DELETE = do_PATCH = do_POST

    # -- responses
    def _common_headers(self):
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'no-referrer')
        origin = self.headers.get('Origin')
        allowed = self.server.cors
        if allowed and origin and ('*' in allowed or origin in allowed):
            self.send_header('Access-Control-Allow-Origin', '*' if '*' in allowed else origin)
            self.send_header('Access-Control-Expose-Headers', 'X-OceanLens-Shape, X-OceanLens-Offset, '
                             'X-OceanLens-Scale, X-OceanLens-Missing, X-OceanLens-Time, X-OceanLens-Depth')
            self.send_header('Vary', 'Origin')

    def send(self, body, ctype, status=200, headers=None, cache_key=None):
        if isinstance(body, str):
            body = body.encode('utf-8')
        gz = 'gzip' in self.headers.get('Accept-Encoding', '') and len(body) > 1400 and \
            any(t in ctype for t in ('json', 'xml', 'html', 'csv', 'text'))
        if gz:
            hit = self.server.gz_cache.get(cache_key) if cache_key else None
            if hit is None or hit[0] != len(body):
                hit = (len(body), gzip.compress(body, 6))
                if cache_key:
                    self.server.gz_cache[cache_key] = hit
            body = hit[1]
        self.send_response(status)
        self._common_headers()
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-cache' if 'html' in ctype else 'public, max-age=300')
        if gz:
            self.send_header('Content-Encoding', 'gzip')
            self.send_header('Vary', 'Accept-Encoding')
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if not self._head:
            self.wfile.write(body)

    def send_json(self, obj, status=200, cache_key=None):
        self.send(json.dumps(obj, separators=(',', ':'), allow_nan=False), 'application/json; charset=utf-8',
                  status, cache_key=cache_key)

    def base_url(self):
        if self.server.public_url:
            return self.server.public_url.rstrip('/')
        host = self.headers.get('Host') or ''
        if not re.fullmatch(r'[A-Za-z0-9.\-]+(:\d{1,5})?|\[[0-9A-Fa-f:.]+\](:\d{1,5})?', host):
            host = '%s:%d' % self.server.server_address[:2]
        return f'http://{host}'

    def host_ok(self):
        if not self.server.check_host:
            return True
        host = (self.headers.get('Host') or '').lower()
        name = host[1:host.index(']')] if host.startswith('[') and ']' in host else host.rsplit(':', 1)[0]
        return name in LOOPBACK

    def misdirected(self):
        self.send_json({'error': {'code': 'MisdirectedRequest',
                                  'message': 'this server answers only to localhost, 127.0.0.1 or [::1]'}}, 421)

    @contextlib.contextmanager
    def busy(self):
        """Hold a slot for a memory-heavy request; ApiError 503 when all are taken."""
        if not self.server.heavy.acquire(blocking=False):
            raise ApiError(503, 'ServerBusy', f'{HEAVY_SLOTS} map or coverage requests are already running; retry shortly')
        try:
            yield
        finally:
            self.server.heavy.release()

    # -- routing
    def do_GET(self, head=False):
        self._head = head
        if not self.host_ok():
            return self.misdirected()
        url = urlsplit(self.path)
        path = re.sub(r'/+', '/', url.path).rstrip('/') or '/'
        try:
            q = {k.lower(): v for k, v in parse_qs(url.query, keep_blank_values=True).items()}
        except ValueError:
            q = {}
        service = (q.get('service', [''])[-1]).upper()
        try:
            if path in ('/', '/index.html'):
                return self.serve_app(q)
            if path == '/healthz':
                return self.send_json({'status': 'ok', 'version': VERSION, 'datasets': len(self.server.datasets),
                                       'netcdf': ncio is not None})
            if path in ('/wms', '/ows') and (service == 'WMS' or path == '/wms'):
                return self.wms(q)
            if path in ('/wcs', '/ows') and (service == 'WCS' or path == '/wcs'):
                return self.wcs(q)
            if path == '/ows':
                raise ApiError(400, 'MissingParameterValue', 'SERVICE must be WMS or WCS', 'service')
            if path.startswith('/api'):
                return self.api(path, q)
            raise ApiError(404, 'NotFound', f'no route {path}')
        except ApiError as e:
            if path in ('/wms', '/ows') and (service == 'WMS' or path == '/wms'):
                return self.wms_error(e)
            if path in ('/wcs', '/ows') and (service == 'WCS' or path == '/wcs'):
                return self.wcs_error(e)
            return self.send_json({'error': {'code': e.code, 'message': e.message}}, e.status)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as e:             # a bug, not a bad request: say so without a traceback to the client
            self.log_error('internal error on %s: %r', self.path, e)
            try:
                self.send_json({'error': {'code': 'InternalError', 'message': f'{type(e).__name__} while serving the request'}}, 500)
            except Exception:
                pass

    def serve_app(self, q):
        """The built page; with ?dataset=<id>, the same page with that dataset's snapshot in place of the embedded one."""
        app = self.server.app
        if not app or not Path(app).exists():
            return self.send('<!doctype html><meta charset="utf-8"><title>OceanLens 3D server</title>'
                             '<p>No built app found. Run <code>python build.py --out dist/OceanLens3D.html</code> '
                             'or start the server with <code>--app FILE</code>. The API is at <a href="/api/v1">/api/v1</a>.',
                             'text/html; charset=utf-8')
        mtime, ds_id = Path(app).stat().st_mtime, self.param(q, 'dataset')
        if ds_id in (None, self.server.default_id):
            return self.send(Path(app).read_bytes(), 'text/html; charset=utf-8', cache_key=('app', mtime))
        ds = self.server.datasets.get(ds_id)
        if ds is None or ds.raw is None:
            raise ApiError(404, 'NoSnapshot', f'no app snapshot for dataset {ds_id!r}; see /api/v1/datasets')
        key = ('page', mtime, ds_id)
        page = self.server.pages.get(key)
        if page is None:
            html = Path(app).read_text(encoding='utf-8')
            tag = '<script type="application/json" id="snapshot">'
            a = html.find(tag)
            b = html.find('</script>', a)
            if a < 0 or b < 0:
                raise ApiError(500, 'InternalError', 'the built page has no snapshot tag to replace')
            # build.py writes every "<" in embedded JSON as <, so the data cannot close the tag early
            page = (html[:a + len(tag)] + ds.raw.decode('utf-8').replace('<', '\\u003c') + html[b:]).encode('utf-8')
            self.server.pages = {key: page}         # keep only the latest substituted page
        self.send(page, 'text/html; charset=utf-8', cache_key=key)

    # -- REST
    def param(self, q, name, required=False, default=None):
        v = q.get(name, [None])[-1]
        if v is None or v == '':
            if required:
                raise ApiError(400, 'MissingParameterValue', f'parameter {name} is required', name)
            return default
        return v

    def number(self, q, name, required=True, default=None):
        v = self.param(q, name, required, default)
        try:
            x = float(v)
        except (TypeError, ValueError):
            raise ApiError(400, 'InvalidParameterValue', f'{name} must be a number, not {v!r}', name)
        if not math.isfinite(x):
            raise ApiError(400, 'InvalidParameterValue', f'{name} must be finite', name)
        return x

    def api(self, path, q):
        parts = path.split('/')[1:]        # ['api', 'v1', 'datasets', id, op]
        base = self.base_url()
        if parts in (['api'], ['api', 'v1']):
            return self.send_json({'service': 'OceanLens 3D', 'version': VERSION, 'endpoints': [
                '/api/v1/datasets', '/api/v1/datasets/{id}/meta', '/api/v1/datasets/{id}/snapshot',
                '/api/v1/datasets/{id}/field?var=&time=&depth=[&format=bin]', '/api/v1/datasets/{id}/column?lat=&lon=&time=',
                '/api/v1/datasets/{id}/profiles?bbox=w,s,e,n&start=&end=', '/api/v1/datasets/{id}/track',
                '/wms?SERVICE=WMS&REQUEST=GetCapabilities', '/wcs?SERVICE=WCS&REQUEST=GetCapabilities']})
        if parts[:3] != ['api', 'v1', 'datasets'] or len(parts) > 5:
            raise ApiError(404, 'NotFound', f'no route {path}')
        if len(parts) == 3:
            return self.send_json({'datasets': [dict(d.summary(base), default=d.id == self.server.default_id)
                                                for d in self.server.datasets.values()]})
        ds = self.server.datasets.get(parts[3])
        if ds is None:
            raise ApiError(404, 'NoSuchDataset', f'no dataset {parts[3]!r}; see /api/v1/datasets')
        op = parts[4] if len(parts) == 5 else 'meta'
        handler = {'meta': self.api_meta, 'snapshot': self.api_snapshot, 'field': self.api_field,
                   'column': self.api_column, 'profiles': self.api_profiles, 'track': self.api_track}.get(op)
        if handler is None:
            raise ApiError(404, 'NotFound', f'no route {path}')
        return handler(ds, q)

    def api_meta(self, ds, q):
        out = ds.summary(self.base_url())
        out['grid'] = {'lat': [num(x) for x in ds.lat], 'lon': [num(x) for x in ds.lon],
                       'depth_units': 'm, positive down', 'time_format': 'ISO 8601 UTC'}
        for v in out['variables']:
            v['data_range'] = [num(x) if x is not None else None for x in ds.info[v['key']]['data_range']]
        out['track'] = bool(ds.track)
        out['attributes'] = ds.attrs
        return self.send_json(out)

    def api_snapshot(self, ds, q):
        if ds.raw is None:
            raise ApiError(404, 'NoSnapshot', f'{ds.id} comes from NetCDF and has no app snapshot; use field, column or WCS')
        self.send(ds.raw, 'application/json; charset=utf-8', cache_key=('snap', ds.id))

    def pick_var(self, ds, q):
        key = self.param(q, 'var', True)
        if key not in ds.fields:
            raise ApiError(400, 'InvalidParameterValue', f'var must be one of {", ".join(ds.fields)}', 'var')
        return key

    def api_field(self, ds, q):
        key = self.pick_var(ds, q)
        ti = ds.time_index(self.param(q, 'time'))
        depth = self.number(q, 'depth', False, 0)
        grid = ds.layer(key, ti, depth)
        fmt = (self.param(q, 'format') or 'json').lower()
        if fmt == 'bin':
            finite = grid[np.isfinite(grid)]
            lo, hi = (float(finite.min()), float(finite.max())) if finite.size else (0.0, 0.0)
            scale = (hi - lo) / 65000 or 1.0
            qv = np.where(np.isfinite(grid), np.rint((grid - lo) / scale) - 32500, -32768).astype('<i2')
            return self.send(qv.tobytes(), 'application/octet-stream', headers={
                'X-OceanLens-Shape': f'{grid.shape[0]},{grid.shape[1]}', 'X-OceanLens-Offset': repr(lo + 32500 * scale),
                'X-OceanLens-Scale': repr(scale), 'X-OceanLens-Missing': '-32768',
                'X-OceanLens-Time': ds.times[ti], 'X-OceanLens-Depth': f'{depth:g}'})
        if fmt != 'json':
            raise ApiError(400, 'InvalidParameterValue', 'format must be json or bin', 'format')
        return self.send_json({'dataset': ds.id, 'var': key, 'units': ds.info[key]['units'], 'time': ds.times[ti],
                               'depth': depth, 'lat': [num(x) for x in ds.lat], 'lon': [num(x) for x in ds.lon],
                               'order': 'values[lat][lon], south to north, west to east; null = no data',
                               'values': [[num(x) for x in row] for row in grid]})

    def api_column(self, ds, q):
        lat, lon = self.number(q, 'lat'), self.number(q, 'lon')
        ti = ds.time_index(self.param(q, 'time'))
        if not (ds.lat[0] <= lat <= ds.lat[-1] and ds.lon[0] <= lon <= ds.lon[-1]):
            raise ApiError(400, 'InvalidParameterValue', f'({lat}, {lon}) is outside the grid {ds.bbox}', 'lat')
        out = {'dataset': ds.id, 'lat': lat, 'lon': lon, 'time': ds.times[ti], 'depth': [num(z) for z in ds.depths],
               'units': {k: ds.info[k]['units'] for k in ds.fields}, 'values': {}}
        for key, f in ds.fields.items():
            out['values'][key] = [num(ds.sample(f[ti, k], [lat], [lon])[0]) for k in range(len(ds.depths))]
        return self.send_json(out)

    def api_profiles(self, ds, q):
        bbox = self.param(q, 'bbox')
        if bbox:
            try:
                w, s, e, n = (float(x) for x in bbox.split(','))
                assert all(map(math.isfinite, (w, s, e, n))) and w <= e and s <= n
            except (ValueError, AssertionError):
                raise ApiError(400, 'InvalidParameterValue', 'bbox must be west,south,east,north in degrees, '
                               'finite, with west <= east and south <= north', 'bbox')
        try:
            start = parse_iso(self.param(q, 'start')) if self.param(q, 'start') else -math.inf
            end = parse_iso(self.param(q, 'end')) if self.param(q, 'end') else math.inf
        except ValueError as e:
            raise ApiError(400, 'InvalidParameterValue', str(e), 'start')
        kind = self.param(q, 'type')
        out = [p for p in ds.profiles
               if (not bbox or (w <= p['lon'] <= e and s <= p['lat'] <= n)) and start <= parse_iso(p['time']) <= end
               and (not kind or p['type'] == kind)]
        return self.send_json({'dataset': ds.id, 'count': len(out), 'depth_units': 'm',
                               'units': {k: VARS[k]['units'] for k in ARGO_KEYS.values()}, 'profiles': out})

    def api_track(self, ds, q):
        if not ds.track:
            raise ApiError(404, 'NoTrack', f'{ds.id} has no storm track')
        return self.send_json(ds.track)

    # -- OGC common
    def ogc_request(self, q, service, versions):
        req = self.param(q, 'request', True)
        ver = self.param(q, 'version') or self.param(q, 'acceptversions')
        if ver and req.lower() != 'getcapabilities' and ver not in versions:
            raise ApiError(400, 'InvalidParameterValue', f'{service} version {ver} is not served; use {versions[0]}', 'version')
        return req.lower(), ver

    def bbox_of(self, q, crs, version):
        try:
            a = [float(x) for x in self.param(q, 'bbox', True).split(',')[:4]]
            assert len(a) == 4 and all(map(math.isfinite, a))
        except (ValueError, AssertionError):
            raise ApiError(400, 'InvalidParameterValue', 'BBOX must be four numbers', 'bbox')
        if crs == 'EPSG:4326' and version == '1.3.0':        # 1.3.0 EPSG:4326 axis order is latitude, longitude
            a = [a[1], a[0], a[3], a[2]]
        if a[0] >= a[2] or a[1] >= a[3]:
            raise ApiError(400, 'InvalidParameterValue', 'BBOX minimum must be below maximum', 'bbox')
        return a                                               # lon0, lat0, lon1, lat1

    # -- WMS 1.3.0
    def wms(self, q):
        req, ver = self.ogc_request(q, 'WMS', ('1.3.0', '1.1.1'))
        if req == 'getcapabilities':
            return self.send(self.wms_capabilities(), 'text/xml; charset=utf-8')
        if req in ('getmap', 'getfeatureinfo'):
            ver = ver or '1.3.0'
            names = self.param(q, 'query_layers' if req == 'getfeatureinfo' else 'layers', True).split(',')
            if len(names) > 1 and req == 'getmap':
                raise ApiError(400, 'InvalidParameterValue', 'GetMap draws one layer per request', 'layers')
            layers = []
            for n in names:
                ds, key = self.server.layer_ref(n)
                if ds is None:
                    raise ApiError(400, 'LayerNotDefined', f'no layer {n!r}; see GetCapabilities', 'layers')
                layers.append((ds, key))
            crs = (self.param(q, 'crs') or self.param(q, 'srs') or 'CRS:84').upper()
            if crs not in ('CRS:84', 'EPSG:4326'):
                raise ApiError(400, 'InvalidCRS', f'CRS {crs} is not served; use CRS:84 or EPSG:4326', 'crs')
            box = self.bbox_of(q, crs, ver)
            try:
                w, h = int(self.param(q, 'width', True)), int(self.param(q, 'height', True))
            except ValueError:
                raise ApiError(400, 'InvalidParameterValue', 'WIDTH and HEIGHT must be integers', 'width')
            if not (1 <= w <= MAX_SIDE and 1 <= h <= MAX_SIDE and w * h <= MAX_PIXELS):
                raise ApiError(400, 'InvalidParameterValue', f'WIDTH and HEIGHT must be 1 to {MAX_SIDE}', 'width')
            ds = layers[0][0]
            ti = ds.time_index(self.param(q, 'time'))
            elev = self.param(q, 'elevation')
            try:
                depth = abs(float(elev)) if elev not in (None, '') else float(ds.depths[0])
            except ValueError:
                raise ApiError(400, 'InvalidDimensionValue', 'ELEVATION must be a number of metres', 'elevation')
            if req == 'getmap':
                return self.wms_map(q, ds, layers[0][1], box, w, h, ti, depth)
            return self.wms_info(q, layers, box, w, h, depth, ver)
        raise ApiError(400, 'OperationNotSupported', f'REQUEST {req} is not served', 'request')

    def wms_map(self, q, ds, key, box, w, h, ti, depth):
        fmt = (self.param(q, 'format') or 'image/png').lower()
        if fmt != 'image/png':
            raise ApiError(400, 'InvalidFormat', 'FORMAT must be image/png', 'format')
        info = ds.info[key]
        style = (self.param(q, 'styles') or '').split(',')[0].strip()
        style = style.split('/')[-1] if style and style.lower() != 'default' else palette_name(info)
        if style not in PALETTES:
            raise ApiError(400, 'StyleNotDefined', f'style {style!r}; styles are {", ".join(PALETTES)}', 'styles')
        log = (self.param(q, 'logscale') or ('true' if info.get('scale') == 'log' else 'false')).lower() == 'true'
        rng = self.param(q, 'colorscalerange')
        try:
            lo, hi = (float(x) for x in rng.split(',')) if rng else info['range']
        except ValueError:
            raise ApiError(400, 'InvalidParameterValue', 'COLORSCALERANGE must be min,max', 'colorscalerange')
        if not (hi > lo) or (log and lo <= 0):
            raise ApiError(400, 'InvalidParameterValue', 'COLORSCALERANGE needs min < max, and min > 0 for a log scale',
                           'colorscalerange')
        rgb = None
        if (self.param(q, 'transparent') or 'false').lower() != 'true':
            bg = (self.param(q, 'bgcolor') or '0xFFFFFF').lower().replace('0x', '').replace('#', '')
            try:
                rgb = np.frombuffer(bytes.fromhex(bg[-6:].rjust(6, 'f')), np.uint8)
            except ValueError:
                raise ApiError(400, 'InvalidParameterValue', 'BGCOLOR must be 0xRRGGBB', 'bgcolor')
        lons = box[0] + (np.arange(w) + 0.5) * (box[2] - box[0]) / w
        lats = box[3] - (np.arange(h) + 0.5) * (box[3] - box[1]) / h
        with self.busy():
            grid = ds.layer(key, ti, depth)
            rgba = np.zeros((h, w, 4), np.uint8)
            for r0 in range(0, h, MAP_BLOCK_ROWS):          # in blocks: the float temporaries stay a few MB
                LON, LAT = np.meshgrid(lons, lats[r0:r0 + MAP_BLOCK_ROWS])
                vals = ds.sample(grid, LAT, LON)
                block = rgba[r0:r0 + MAP_BLOCK_ROWS]
                block[..., :3] = PALETTES[style][colour_index(vals, lo, hi, log)]
                block[..., 3] = np.where(np.isfinite(vals), 255, 0)
                if rgb is not None:
                    dry = block[..., 3] == 0
                    block[dry, :3], block[..., 3] = rgb, 255
            png = png_rgba(rgba)
        self.send(png, 'image/png', headers={'X-OceanLens-Time': ds.times[ti], 'X-OceanLens-Depth': f'{depth:g}'})

    def wms_info(self, q, layers, box, w, h, depth, ver):
        try:
            i = int(self.param(q, 'i' if ver == '1.3.0' else 'x', True))
            j = int(self.param(q, 'j' if ver == '1.3.0' else 'y', True))
        except ValueError:
            raise ApiError(400, 'InvalidPoint', 'I and J must be integer pixel positions', 'i')
        if not (0 <= i < w and 0 <= j < h):
            raise ApiError(400, 'InvalidPoint', 'I and J must lie inside WIDTH and HEIGHT', 'i')
        lon = box[0] + (i + 0.5) * (box[2] - box[0]) / w
        lat = box[3] - (j + 0.5) * (box[3] - box[1]) / h
        rows = []
        for ds, key in layers:                       # each dataset has its own times
            t = ds.time_index(self.param(q, 'time'))
            v = ds.sample(ds.layer(key, t, depth), [lat], [lon])[0]
            rows.append({'layer': f'{ds.id}.{key}', 'label': ds.info[key]['label'], 'value': num(v),
                         'units': ds.info[key]['units'], 'time': ds.times[t]})
        fmt = (self.param(q, 'info_format') or 'application/json').lower()
        if fmt == 'application/json':
            return self.send_json({'lat': round(lat, 5), 'lon': round(lon, 5), 'time': rows[0]['time'],
                                   'depth': depth, 'features': rows})
        if fmt == 'text/plain':
            lines = [f'lat {lat:.4f}  lon {lon:.4f}  depth {depth:g} m'] + \
                    [f"{r['layer']}: {'no data' if r['value'] is None else r['value']} {r['units']} at {r['time']}" for r in rows]
            return self.send('\n'.join(lines) + '\n', 'text/plain; charset=utf-8')
        raise ApiError(400, 'InvalidFormat', 'INFO_FORMAT must be application/json or text/plain', 'info_format')

    def wms_capabilities(self):
        url = escape(self.base_url() + '/wms?', {'"': '&quot;'})
        res = f'<OnlineResource xlink:type="simple" xlink:href="{url}"/>'
        dcp = f'<DCPType><HTTP><Get>{res}</Get></HTTP></DCPType>'
        sets = list(self.server.datasets.values())
        if sets:
            w = min(d.bbox[0] for d in sets); s = min(d.bbox[1] for d in sets)
            e = max(d.bbox[2] for d in sets); n = max(d.bbox[3] for d in sets)
        else:
            w, s, e, n = -180, -90, 180, 90
        x = ['<?xml version="1.0" encoding="UTF-8"?>',
             '<WMS_Capabilities version="1.3.0" xmlns="http://www.opengis.net/wms" xmlns:xlink="http://www.w3.org/1999/xlink"'
             ' xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xsi:schemaLocation="http://www.opengis.net/wms'
             ' http://schemas.opengis.net/wms/1.3.0/capabilities_1_3_0.xsd">',
             '<Service><Name>WMS</Name><Title>OceanLens 3D</Title>',
             '<Abstract>Ocean model fields (temperature, salinity, currents) by time and depth.</Abstract>',
             f'{res}<Fees>none</Fees><AccessConstraints>none</AccessConstraints><LayerLimit>1</LayerLimit>'
             f'<MaxWidth>{MAX_SIDE}</MaxWidth><MaxHeight>{MAX_SIDE}</MaxHeight></Service>',
             '<Capability><Request>',
             f'<GetCapabilities><Format>text/xml</Format>{dcp}</GetCapabilities>',
             f'<GetMap><Format>image/png</Format>{dcp}</GetMap>',
             f'<GetFeatureInfo><Format>application/json</Format><Format>text/plain</Format>{dcp}</GetFeatureInfo>',
             '</Request><Exception><Format>XML</Format></Exception>',
             f'<Layer><Title>OceanLens 3D</Title><CRS>CRS:84</CRS><CRS>EPSG:4326</CRS>{geo_box(w, s, e, n)}']
        for ds in sets:
            times = ','.join(ds.times)
            elev = ','.join(f'{-z:g}' for z in ds.depths)
            x.append(f'<Layer><Title>{escape(ds.title)}</Title>{geo_box(*ds.bbox)}')
            for key, info in ds.info.items():
                pal = palette_name(info)
                styles = [pal] + [p for p in PALETTES if p != pal]
                x.append(f'<Layer queryable="1" opaque="0"><Name>{escape(ds.id)}.{key}</Name>'
                         f'<Title>{escape(info["label"])} ({escape(info["units"])})</Title>'
                         f'<Abstract>{escape(info.get("standard_name") or key)}; default colour range '
                         f'{info["range"][0]:g} to {info["range"][1]:g}, {info.get("scale", "linear")} scale</Abstract>'
                         f'{geo_box(*ds.bbox)}'
                         f'<BoundingBox CRS="CRS:84" minx="{ds.bbox[0]:g}" miny="{ds.bbox[1]:g}" maxx="{ds.bbox[2]:g}" maxy="{ds.bbox[3]:g}"/>'
                         f'<BoundingBox CRS="EPSG:4326" minx="{ds.bbox[1]:g}" miny="{ds.bbox[0]:g}" maxx="{ds.bbox[3]:g}" maxy="{ds.bbox[2]:g}"/>'
                         f'<Dimension name="time" units="ISO8601" default="{ds.times[0]}" nearestValue="1">{times}</Dimension>'
                         f'<Dimension name="elevation" units="m" unitSymbol="m" default="{-ds.depths[0]:g}">{elev}</Dimension>'
                         + ''.join(f'<Style><Name>{p}</Name><Title>{p} palette</Title></Style>' for p in styles)
                         + '</Layer>')
            x.append('</Layer>')
        x.append('</Layer></Capability></WMS_Capabilities>')
        return '\n'.join(x)

    def wms_error(self, e):
        body = ('<?xml version="1.0" encoding="UTF-8"?>\n<ServiceExceptionReport version="1.3.0" '
                'xmlns="http://www.opengis.net/ogc">'
                f'<ServiceException code={quoteattr(e.code)}>{escape(e.message)}</ServiceException></ServiceExceptionReport>')
        self.send(body, 'text/xml; charset=utf-8', e.status)

    # -- WCS 2.0.1
    def wcs(self, q):
        req, ver = self.ogc_request(q, 'WCS', ('2.0.1', '2.0.0'))
        if req == 'getcapabilities':
            return self.send(self.wcs_capabilities(), 'application/xml; charset=utf-8')
        if req == 'describecoverage':
            ids = self.param(q, 'coverageid', True).split(',')
            return self.send(self.wcs_describe([self.coverage(i) for i in ids]), 'application/xml; charset=utf-8')
        if req == 'getcoverage':
            return self.wcs_get(q)
        raise ApiError(400, 'OperationNotSupported', f'REQUEST {req} is not served', 'request')

    def coverage(self, cid):
        ds, key = self.server.layer_ref(cid)
        if ds is None:
            raise ApiError(404, 'NoSuchCoverage', f'no coverage {cid!r}; see GetCapabilities', 'coverageId')
        return ds, key

    def wcs_capabilities(self):
        url = escape(self.base_url() + '/wcs?', {'"': '&quot;'})
        ops = ''.join(f'<ows:Operation name="{op}"><ows:DCP><ows:HTTP><ows:Get xlink:href="{url}"/></ows:HTTP></ows:DCP>'
                      '</ows:Operation>' for op in ('GetCapabilities', 'DescribeCoverage', 'GetCoverage'))
        cov = []
        for ds in self.server.datasets.values():
            w, s, e, n = ds.bbox
            for key, info in ds.info.items():
                cov.append(f'<wcs:CoverageSummary><ows:Title>{escape(ds.title)}: {escape(info["label"])}</ows:Title>'
                           f'<ows:WGS84BoundingBox><ows:LowerCorner>{w:g} {s:g}</ows:LowerCorner>'
                           f'<ows:UpperCorner>{e:g} {n:g}</ows:UpperCorner></ows:WGS84BoundingBox>'
                           f'<wcs:CoverageId>{escape(ds.id)}.{key}</wcs:CoverageId>'
                           '<wcs:CoverageSubtype>GridCoverage</wcs:CoverageSubtype></wcs:CoverageSummary>')
        return ('<?xml version="1.0" encoding="UTF-8"?>\n<wcs:Capabilities version="2.0.1" '
                'xmlns:wcs="http://www.opengis.net/wcs/2.0" xmlns:ows="http://www.opengis.net/ows/2.0" '
                'xmlns:xlink="http://www.w3.org/1999/xlink">'
                '<ows:ServiceIdentification><ows:Title>OceanLens 3D</ows:Title>'
                '<ows:Abstract>Ocean model fields as CF NetCDF-3 or CSV.</ows:Abstract>'
                '<ows:ServiceType>OGC WCS</ows:ServiceType><ows:ServiceTypeVersion>2.0.1</ows:ServiceTypeVersion>'
                '<ows:Profile>http://www.opengis.net/spec/WCS/2.0/conf/core</ows:Profile>'
                '<ows:Profile>http://www.opengis.net/spec/WCS_protocol-binding_get-kvp/1.0/conf/get-kvp</ows:Profile>'
                f'</ows:ServiceIdentification><ows:OperationsMetadata>{ops}</ows:OperationsMetadata>'
                '<wcs:ServiceMetadata><wcs:formatSupported>application/netcdf</wcs:formatSupported>'
                '<wcs:formatSupported>text/csv</wcs:formatSupported></wcs:ServiceMetadata>'
                f'<wcs:Contents>{"".join(cov)}</wcs:Contents></wcs:Capabilities>')

    def wcs_describe(self, covs):
        out = []
        crs = escape('http://www.opengis.net/def/crs-compound?1=http://www.opengis.net/def/crs/OGC/0/AnsiDate'
                     '&2=http://www.opengis.net/def/crs/EPSG/0/5831&3=http://www.opengis.net/def/crs/EPSG/0/4326')
        for ds, key in covs:
            info, cid = ds.info[key], f'{ds.id}.{key}'
            lo = f'"{ds.times[0]}" {ds.depths[0]:g} {ds.lat[0]:g} {ds.lon[0]:g}'
            hi = f'"{ds.times[-1]}" {ds.depths[-1]:g} {ds.lat[-1]:g} {ds.lon[-1]:g}'
            size = f'{len(ds.times) - 1} {len(ds.depths) - 1} {len(ds.lat) - 1} {len(ds.lon) - 1}'
            std = info.get('standard_name')
            definition = f' definition="http://vocab.nerc.ac.uk/standard_name/{escape(std)}/"' if std else ''
            out.append(f'<wcs:CoverageDescription gml:id="{escape(cid)}">'
                       f'<gml:boundedBy><gml:Envelope srsName="{crs}" axisLabels="time depth Lat Long" '
                       f'uomLabels="d m deg deg" srsDimension="4"><gml:lowerCorner>{lo}</gml:lowerCorner>'
                       f'<gml:upperCorner>{hi}</gml:upperCorner></gml:Envelope></gml:boundedBy>'
                       f'<wcs:CoverageId>{escape(cid)}</wcs:CoverageId>'
                       f'<gml:domainSet><gml:Grid gml:id="grid-{escape(cid)}" dimension="4"><gml:limits><gml:GridEnvelope>'
                       f'<gml:low>0 0 0 0</gml:low><gml:high>{size}</gml:high></gml:GridEnvelope></gml:limits>'
                       '<gml:axisLabels>time depth Lat Long</gml:axisLabels></gml:Grid></gml:domainSet>'
                       f'<gmlcov:rangeType><swe:DataRecord><swe:field name="{escape(key)}"><swe:Quantity{definition}>'
                       f'<swe:description>{escape(info["label"])}</swe:description>'
                       f'<swe:uom code={quoteattr(info["uom"])}/></swe:Quantity></swe:field></swe:DataRecord></gmlcov:rangeType>'
                       '<wcs:ServiceParameters><wcs:CoverageSubtype>GridCoverage</wcs:CoverageSubtype>'
                       '<wcs:nativeFormat>application/netcdf</wcs:nativeFormat></wcs:ServiceParameters>'
                       '</wcs:CoverageDescription>')
        return ('<?xml version="1.0" encoding="UTF-8"?>\n<wcs:CoverageDescriptions xmlns:wcs="http://www.opengis.net/wcs/2.0" '
                'xmlns:gml="http://www.opengis.net/gml/3.2" xmlns:gmlcov="http://www.opengis.net/gmlcov/1.0" '
                f'xmlns:swe="http://www.opengis.net/swe/2.0">{"".join(out)}</wcs:CoverageDescriptions>')

    def wcs_get(self, q):
        ds, key = self.coverage(self.param(q, 'coverageid', True))
        axes = {'time': ds.tsec, 'depth': ds.depths, 'lat': ds.lat, 'lon': ds.lon}
        names = {'time': 'time', 'ansi': 'time', 't': 'time', 'depth': 'depth', 'z': 'depth', 'elevation': 'elevation',
                 'lat': 'lat', 'latitude': 'lat', 'y': 'lat', 'long': 'lon', 'lon': 'lon', 'longitude': 'lon', 'x': 'lon'}
        sel = {a: np.arange(len(v)) for a, v in axes.items()}
        for s in q.get('subset', []):
            m = re.fullmatch(r'\s*(\w+)\s*\(\s*([^,()]*?)\s*(?:,\s*([^,()]*?)\s*)?\)\s*', s)
            axis = names.get(m.group(1).lower()) if m else None
            if not axis:
                raise ApiError(400, 'InvalidAxisLabel' if m else 'InvalidSubsetting',
                               f'subset {s!r}: use time, depth, Lat or Long, e.g. subset=Lat(10,15)', 'subset')
            sign = -1 if axis == 'elevation' else 1
            axis = 'depth' if axis == 'elevation' else axis

            def val(v):
                v = v.strip('"\'')
                if v in ('*', ''):
                    return None
                try:
                    return parse_iso(v) if axis == 'time' else sign * float(v)
                except ValueError:
                    raise ApiError(400, 'InvalidSubsetting', f'subset {s!r}: {v!r} is not a valid {axis} value', 'subset')
            c = axes[axis]
            if m.group(3) is None:                              # slice: the nearest value, kept as an axis of one
                v = val(m.group(2))
                if v is None:
                    continue
                i = int(np.argmin(np.abs(c - v)))
                if axis == 'time':                              # as close as TIME must be in WMS and REST
                    ok = abs(c[i] - v) <= ds.time_tolerance
                else:                                           # inside the axis, or within half a step of an end
                    half = abs(c[-1] - c[0]) / (len(c) - 1) / 2 if len(c) > 1 else 0
                    ok = c.min() - half - 1e-9 <= v <= c.max() + half + 1e-9
                if not ok:
                    raise ApiError(400, 'InvalidSubsetting', f'subset {s!r} is outside the coverage '
                                   f'({axis} runs {fmt_axis(axis, c[0])} to {fmt_axis(axis, c[-1])})', 'subset')
                sel[axis] = np.array([i])
            else:
                lo, hi = val(m.group(2)), val(m.group(3))
                if sign < 0:
                    lo, hi = hi, lo
                lo = -math.inf if lo is None else lo
                hi = math.inf if hi is None else hi
                if lo > hi:
                    raise ApiError(400, 'InvalidSubsetting', f'subset {s!r}: low is above high', 'subset')
                sel[axis] = np.nonzero((c >= lo - 1e-9) & (c <= hi + 1e-9))[0]
            if not len(sel[axis]):
                raise ApiError(400, 'InvalidSubsetting', f'subset {s!r} selects nothing', 'subset')
        it, ik, iy, ix = sel['time'], sel['depth'], sel['lat'], sel['lon']
        cells = len(it) * len(ik) * len(iy) * len(ix)
        if cells > MAX_CELLS:
            raise ApiError(400, 'InvalidSubsetting', f'{cells} cells requested; the limit is {MAX_CELLS}', 'subset')
        with self.busy():
            return self.coverage_out(q, ds, key, it, ik, iy, ix, cells)

    def coverage_out(self, q, ds, key, it, ik, iy, ix, cells):
        data = ds.fields[key][np.ix_(it, ik, iy, ix)]
        fmt = (self.param(q, 'format') or 'application/netcdf').lower()
        name = f'{ds.id}.{key}'
        if fmt in ('text/csv', 'csv'):
            if cells > MAX_CELLS // 5:
                raise ApiError(400, 'InvalidSubsetting', f'{cells} cells requested; CSV is limited to {MAX_CELLS // 5}', 'subset')
            T, K, Y, X = np.meshgrid(it, ik, iy, ix, indexing='ij')
            lines = [f'time,depth_m,lat,lon,{key}_{ds.info[key]["units"].replace(" ", "")}']
            flat = data.ravel()
            for t, k, y, x_, v in zip(T.ravel(), K.ravel(), Y.ravel(), X.ravel(), flat):
                lines.append(f'{ds.times[t]},{ds.depths[k]:g},{ds.lat[y]:g},{ds.lon[x_]:g},'
                             + ('' if not np.isfinite(v) else f'{v:.5g}'))
            return self.send('\n'.join(lines) + '\n', 'text/csv; charset=utf-8',
                             headers={'Content-Disposition': f'attachment; filename="{name}.csv"'})
        if fmt not in ('application/netcdf', 'application/x-netcdf', 'netcdf', 'nc'):
            raise ApiError(400, 'InvalidParameterValue', 'format must be application/netcdf or text/csv', 'format')
        if ncio is None:
            raise ApiError(501, 'NoApplicableCode', 'NetCDF output needs data-pipeline/ncio.py; use format=text/csv')
        self.send(coverage_netcdf(ds, key, it, ik, iy, ix, data), 'application/netcdf',
                  headers={'Content-Disposition': f'attachment; filename="{name}.nc"'})

    def wcs_error(self, e):
        loc = f' locator={quoteattr(e.locator)}' if e.locator else ''
        body = ('<?xml version="1.0" encoding="UTF-8"?>\n<ows:ExceptionReport xmlns:ows="http://www.opengis.net/ows/2.0" '
                f'version="2.0.1"><ows:Exception exceptionCode={quoteattr(e.code)}{loc}>'
                f'<ows:ExceptionText>{escape(e.message)}</ows:ExceptionText></ows:Exception></ows:ExceptionReport>')
        self.send(body, 'application/xml; charset=utf-8', e.status)


def fmt_axis(axis, v):
    return iso(v) if axis == 'time' else f'{v:g}'


def geo_box(w, s, e, n):
    return (f'<EX_GeographicBoundingBox><westBoundLongitude>{w:g}</westBoundLongitude><eastBoundLongitude>{e:g}'
            f'</eastBoundLongitude><southBoundLatitude>{s:g}</southBoundLatitude><northBoundLatitude>{n:g}'
            '</northBoundLatitude></EX_GeographicBoundingBox>')


FILL = np.float32(9.96921e36)      # the CF/NetCDF default fill for float


def coverage_netcdf(ds, key, it, ik, iy, ix, data):
    """One variable on (time, depth, lat, lon) as CF-1.8 NetCDF-3 bytes."""
    info = ds.info[key]
    attrs = {'units': info['units'], 'long_name': info['label'], '_FillValue': FILL}
    if info.get('standard_name'):
        attrs['standard_name'] = info['standard_name']
    out = np.where(np.isfinite(data), data, FILL).astype(np.float32)
    g = {'Conventions': 'CF-1.8', 'title': f'{ds.title}: {info["label"]}',
         'source': ds.attrs.get('source') or (ds.attrs.get('sources', {}) or {}).get('model', ds.source),
         'history': f'{iso(time.time())} subset by OceanLens 3D server {VERSION} (WCS 2.0.1 GetCoverage)'}
    cites = ds.attrs.get('citations')
    if cites:
        g['references'] = '; '.join(c.get('text', '') for c in cites if isinstance(c, dict))
    dims = {'time': len(it), 'depth': len(ik), 'lat': len(iy), 'lon': len(ix)}
    variables = {
        'time': {'dims': ('time',), 'dtype': 'f8', 'data': ds.tsec[it] / 3600.0,
                 'attrs': {'standard_name': 'time', 'units': 'hours since 1970-01-01 00:00:00', 'calendar': 'standard', 'axis': 'T'}},
        'depth': {'dims': ('depth',), 'dtype': 'f4', 'data': ds.depths[ik].astype(np.float32),
                  'attrs': {'standard_name': 'depth', 'units': 'm', 'positive': 'down', 'axis': 'Z'}},
        'lat': {'dims': ('lat',), 'dtype': 'f4', 'data': ds.lat[iy].astype(np.float32),
                'attrs': {'standard_name': 'latitude', 'units': 'degrees_north', 'axis': 'Y'}},
        'lon': {'dims': ('lon',), 'dtype': 'f4', 'data': ds.lon[ix].astype(np.float32),
                'attrs': {'standard_name': 'longitude', 'units': 'degrees_east', 'axis': 'X'}},
        key: {'dims': ('time', 'depth', 'lat', 'lon'), 'dtype': 'f4', 'data': out, 'attrs': attrs},
    }
    return ncio.write_bytes(dims, variables, g)


# ---- start-up ------------------------------------------------------------------------------------------------
def default_app():
    for p in (REPO / 'dist' / 'OceanLens3D.html', REPO / 'OceanLens3D.html'):
        if p.exists():
            return p
    return None


def embedded_id(datasets, app=None):
    """The dataset whose snapshot the served page embeds; else the one src/index.html names; else the first snapshot."""
    snaps = {i: d for i, d in datasets.items() if d.raw is not None}
    try:
        html = Path(app).read_text(encoding='utf-8') if app else ''
    except OSError:
        html = ''
    tag = '<script type="application/json" id="snapshot">'
    a = html.find(tag)
    if a >= 0:
        text = html[a + len(tag):html.find('</script>', a)].replace('\\u003c', '<')   # build.py escapes "<"
        for i, d in snaps.items():          # build.py embeds the file stripped; compare by content
            if d.raw.decode('utf-8').strip() == text:
                return i
        try:
            obj = json.loads(text)
            match = next((i for i, d in snaps.items() if json.loads(d.raw) == obj), None)
        except ValueError:
            match = None
        if match:
            return match
    try:
        m = re.search(r'id="snapshot" src="[^"]*?([\w-]+)\.json"', (REPO / 'src' / 'index.html').read_text(encoding='utf-8'))
    except OSError:
        m = None
    if m and safe_id(m.group(1)) in snaps:
        return safe_id(m.group(1))
    return next(iter(snaps), None)


def load_datasets(snapshots=(), netcdfs=()):
    out, seen = [], set()
    for p in list(snapshots) + list(netcdfs):
        ds = load_snapshot(p) if p in snapshots else load_netcdf(p)
        if ds.id in seen:
            raise SystemExit(f'two datasets would share the id {ds.id!r} ({p}); rename one file')
        seen.add(ds.id)
        out.append(ds)
    return out


def make_server(host='127.0.0.1', port=8080, snapshots=None, netcdfs=(), app=None, cors=(), public_url=None, quiet=False):
    snapshots = sorted((REPO / 'data').glob('*.json')) if snapshots is None else [Path(p) for p in snapshots]
    datasets = load_datasets(snapshots, [Path(p) for p in netcdfs])
    return OceanServer((host, port), datasets, app if app is not None else default_app(), cors, public_url, quiet)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0], formatter_class=argparse.RawDescriptionHelpFormatter,
                                 epilog=__doc__.split('\n', 2)[2])
    ap.add_argument('--host', default='127.0.0.1', help='interface to listen on (0.0.0.0 for every interface)')
    ap.add_argument('--port', type=int, default=8080)
    ap.add_argument('--app', help='built single-file app to serve at / (default dist/OceanLens3D.html, then OceanLens3D.html)')
    ap.add_argument('--data', nargs='*', help='schema-1 snapshot JSON files (default data/*.json)')
    ap.add_argument('--netcdf', nargs='*', default=[], help='CF NetCDF-3 model files to serve as datasets')
    ap.add_argument('--cors', action='append', default=[], help="allowed origin for cross-origin requests ('*' for any); repeatable")
    ap.add_argument('--public-url', help='base URL clients see, when behind a reverse proxy (e.g. https://host/oceanlens)')
    ap.add_argument('--quiet', action='store_true', help='no request log')
    a = ap.parse_args(argv)
    srv = make_server(a.host, a.port, a.data, a.netcdf, a.app, a.cors, a.public_url, a.quiet)
    print(f'OceanLens 3D server {VERSION} on http://{a.host}:{srv.server_address[1]}/  '
          f'({len(srv.datasets)} datasets: {", ".join(srv.datasets)}; app {srv.app or "not built"})', flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()


if __name__ == '__main__':
    main()
