#!/usr/bin/env python3
"""Build the real-data snapshot for OceanLens 3D (Cyclone Amphan, May 2020).

Sources, all open and needing no account:
  model       HYCOM + NCODA GOFS 3.1 analysis, GLBy0.08/expt_93.0 (tds.hycom.org, OPeNDAP ASCII)
  in situ     Argo GDAC profiles via Ifremer ERDDAP (ArgoFloats)
  bathymetry  ETOPO1 via NOAA ERDDAP (etopo180), every 0.1 degree
  track       IBTrACS v04r01, North Indian basin (NOAA NCEI)

Only numpy is needed. Raw responses live in a cache directory. By default the script reads the cache only
and stops, naming the file, if something is missing; --fetch allows downloads (add --ca-bundle behind a
TLS-inspecting proxy or antivirus). Nothing is written to the cache in offline runs.

    python build_snapshot.py [--cache DIR] [--out FILE] [--check]
    python build_snapshot.py --fetch [--ca-bundle PEM]

The result is a JSON file (default ../data/amphan-2020.json) that build.py inlines into OceanLens3D.html.
The same cache always gives byte-identical output.
"""
import argparse, base64, csv, hashlib, http.client, io, json, math, os, re, ssl, sys, threading, time
import urllib.error, urllib.parse, urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import numpy as np

SCHEMA = 1
PIPELINE_VERSION = '2'

# ---- what the app expects: the grid is checked against meta at boot ----
LAT0, LAT1, LON0, LON1, STEP = 6.0, 22.0, 61.0, 92.0, 0.5
LATS = np.round(np.arange(LAT0, LAT1 + 1e-9, STEP), 3)          # 33
LONS = np.round(np.arange(LON0, LON1 + 1e-9, STEP), 3)          # 63
DEPTHS = [0, 20, 50, 75, 100, 150, 200, 300, 500, 750, 1000]
ARGO_LEVELS = list(range(5, 101, 5)) + list(range(110, 301, 10)) + list(range(325, 1001, 25))
T0 = datetime(2020, 5, 4, 12, tzinfo=timezone.utc)               # 8 analyses, 3 days apart: 4-25 May
SNAPS = [T0 + timedelta(days=3 * i) for i in range(8)]
STORM, STORM_YEAR = 'AMPHAN', '2020'

HYCOM = 'https://tds.hycom.org/thredds/dodsC/GLBy0.08/expt_93.0'
ARGO = 'https://erddap.ifremer.fr/erddap/tabledap/ArgoFloats.csv'
ETOPO = 'https://upwell.pfeg.noaa.gov/erddap/griddap/etopo180.csv'
IBTRACS = ('https://www.ncei.noaa.gov/data/international-best-track-archive-for-climate-stewardship-ibtracs/'
           'v04r01/access/csv/ibtracs.NI.list.v04r01.csv')
# the files in the original cache were downloaded on this date; later downloads record their own in manifest.json
DEFAULT_ACCESSED = '2026-09-27'

# Int16 packing: value = offset + q * scale. Salinity uses HYCOM's own packing, so estuary water fits.
QUANT = {'temp': (15.0, 0.001), 'sal': (20.0, 0.001), 'u': (0.0, 0.0001), 'v': (0.0, 0.0001), 'chl': (0.0, 0.001)}
MISSING = -32768

# Argo: data-centre flags kept, then a re-run of the real-time tests the app relies on
ARGO_GOOD = {'1', '2', '5', '8'}                                   # good, probably good, changed, estimated
GLOBAL_RANGE = {'temp': (-2.5, 40.0), 'psal': (2.0, 41.0)}         # Argo QC manual, global range test
REGIONAL_TMIN, REGIONAL_PMAX = 4.0, 1100.0   # coldest QC-good value in this domain above 1100 dbar is 5.9 C
SPIKE = {'temp': (6.0, 2.0), 'psal': (0.9, 0.3)}                    # threshold above / below 500 dbar
MIN_LEVELS = 10
IMD_GRADES = {'', 'D', 'DD', 'CS', 'SCS', 'VSCS', 'ESCS', 'SuCS'}

# sanity bounds for the output; a violation stops the build
MIN_PROFILES, MIN_PER_SNAPSHOT = 100, 10

CITATIONS = [
    {'id': 'hycom', 'text': 'HYCOM + NCODA Global 1/12° Analysis (GOFS 3.1, GLBy0.08/expt_93.0), provided by the '
                           'HYCOM consortium and the U.S. Naval Research Laboratory (hycom.org). Chassignet et al. '
                           '(2007), J. Mar. Syst. 65, 60–83; Cummings (2005), Q. J. R. Meteorol. Soc. 131, '
                           '3583–3604.'},
    {'id': 'argo', 'doi': '10.17882/42182',
     'text': 'These data were collected and made freely available by the International Argo Program and the national '
             'programs that contribute to it (https://argo.ucsd.edu, https://www.ocean-ops.org). The Argo Program is '
             'part of the Global Ocean Observing System. Argo (2000), Argo float data and metadata from Global Data '
             'Assembly Centre (Argo GDAC), SEANOE, doi:10.17882/42182; accessed through the Ifremer ERDDAP server.'},
    {'id': 'etopo1', 'doi': '10.7289/V5C8276M',
     'text': 'Amante, C. and B. W. Eakins (2009), ETOPO1 1 Arc-Minute Global Relief Model: Procedures, Data Sources '
             'and Analysis, NOAA Technical Memorandum NESDIS NGDC-24, doi:10.7289/V5C8276M. (ETOPO1 is superseded '
             'by ETOPO 2022.)'},
    {'id': 'ibtracs', 'doi': '10.25921/82ty-9e16',
     'text': 'Knapp, K. R., M. C. Kruk, D. H. Levinson, H. J. Diamond and C. J. Neumann (2010), The International Best '
             'Track Archive for Climate Stewardship (IBTrACS), Bull. Amer. Meteor. Soc. 91, 363–376, '
             'doi:10.1175/2009BAMS2755.1; IBTrACS v04r01, NOAA NCEI, doi:10.25921/82ty-9e16. Positions and winds '
             'from RSMC New Delhi (IMD) and the Joint Typhoon Warning Center.'},
]


class CacheMiss(Exception):
    """A raw file is not in the cache and downloads are off."""


class FetchError(Exception):
    """A download failed for good."""


class InvariantError(Exception):
    """The output breaks one of the snapshot's sanity rules."""


def require(cond, msg):
    if not cond:
        raise InvariantError(msg)


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def read_json(path, default=None):
    if not os.path.exists(path):
        return default
    with open(path, encoding='utf-8') as f:
        return json.load(f)


def write_atomic(path, data):
    """Write bytes so that a crash leaves either the old file or the new one, never half of one."""
    d = os.path.dirname(os.path.abspath(path))
    os.makedirs(d, exist_ok=True)
    tmp = path + '.part'
    with open(tmp, 'wb') as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


class Fetcher:
    """Reads raw responses from the cache; downloads a missing one only when fetching is on.

    Every response goes through `parse` before it is cached, so an error page or a truncated body never
    lands in the cache. Client errors (4xx) fail at once; timeouts, resets and 5xx are retried."""

    def __init__(self, cache, fetch=False, cafile=None, sleep=time.sleep, allow_hosts=None):
        self.cache, self.fetch, self.cafile, self.sleep = cache, fetch, cafile, sleep
        self.allow_hosts = set(allow_hosts or [])
        self.used = {}              # cache name -> sha256, for provenance
        self.downloaded = 0
        self._lock = threading.Lock()
        self._ctx = None

    def path(self, name):
        return os.path.join(self.cache, *name.split('/'))

    def get(self, url, name, parse, headers=None, timeout=180, retries=4):
        path = self.path(name)
        if os.path.exists(path):
            with open(path, 'rb') as f:
                data = f.read()
            try:
                out = parse(data)
            except Exception as e:
                raise ValueError(f'cached file {name} does not parse ({e}); delete it and rerun with --fetch') from e
        else:
            if not self.fetch:
                raise CacheMiss(name)
            data = self.download(url, headers, timeout, retries)
            out = parse(data)                       # raises on garbage: nothing is cached
            self.store(name, data, url)
        with self._lock:
            self.used[name] = sha256(data)
        return out

    def download(self, url, headers=None, timeout=180, retries=4):
        host = urllib.parse.urlsplit(url).hostname
        if self.allow_hosts and host not in self.allow_hosts:
            raise FetchError(f'{host} is not in --allow-host; not downloading {url}')
        if self._ctx is None:
            self._ctx = ssl.create_default_context(cafile=self.cafile) if self.cafile else ssl.create_default_context()
        last = None
        for attempt in range(retries):
            try:
                req = urllib.request.Request(url, headers={'User-Agent': 'OceanLens3D-snapshot/2', **(headers or {})})
                with urllib.request.urlopen(req, context=self._ctx, timeout=timeout) as r:
                    data = r.read()
                with self._lock:
                    self.downloaded += len(data)
                return data
            except urllib.error.HTTPError as e:
                if 400 <= e.code < 500 and e.code not in (408, 429):
                    raise FetchError(f'HTTP {e.code} for {url}') from e
                last = e
            except (urllib.error.URLError, TimeoutError, ConnectionError, http.client.HTTPException) as e:
                last = e
            if attempt < retries - 1:
                self.sleep(2 + 4 * attempt)
        raise FetchError(f'giving up on {url} after {retries} attempts: {last}') from last

    def store(self, name, data, url):
        write_atomic(self.path(name), data)
        with self._lock:
            mpath = os.path.join(self.cache, 'manifest.json')
            man = read_json(mpath, {})
            man[name] = {'url': url, 'sha256': sha256(data), 'bytes': len(data),
                         'fetched': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}
            write_atomic(mpath, json.dumps(man, indent=1, sort_keys=True).encode())

    def manifest(self):
        return read_json(os.path.join(self.cache, 'manifest.json'), {})


def pack(values, key):
    """float array -> base64 little-endian Int16 (NaN -> MISSING). Refuses values the packing cannot hold."""
    off, sc = QUANT[key]
    a = np.asarray(values, dtype=np.float64)
    q = np.round((a - off) / sc)
    fin = np.isfinite(a)
    if fin.any() and (np.abs(q[fin]) > 32767).any():
        bad = a[fin][np.abs(q[fin]) > 32767]
        raise InvariantError(f'{key}: {bad.size} values outside the Int16 packing range '
                             f'{off - 32767 * sc:g}..{off + 32767 * sc:g} (e.g. {bad[0]:g})')
    q = np.where(fin, q, MISSING).astype('<i2')
    return base64.b64encode(q.tobytes()).decode('ascii')


def unpack(b64, key):
    off, sc = QUANT[key]
    q = np.frombuffer(base64.b64decode(b64), dtype='<i2').astype(np.float64)
    return np.where(q == MISSING, np.nan, off + q * sc)


# ------------------------------------------------------------------ HYCOM
def parse_ascii(data):
    """Arrays in an OPeNDAP .ascii response -> {name: flat float array, name+'_shape': shape}."""
    text = data.decode('ascii') if isinstance(data, bytes) else data
    if '-' * 45 not in text:
        raise ValueError('not an OPeNDAP ASCII response')
    body = text.split('-' * 45, 1)[1]
    out, name, vals = {}, None, []

    def finish():
        if name is not None:
            out[name] = np.array(vals, dtype=np.float64)

    for line in body.splitlines():
        line = line.strip()
        if not line:
            continue
        m = re.match(r'^([\w.]+)((?:\[\d+\])+)$', line)
        if m:
            finish()
            name, vals = m.group(1).split('.')[-1], []
            out[name + '_shape'] = [int(x) for x in re.findall(r'\[(\d+)\]', m.group(2))]
        elif line.startswith('['):                       # "[0][1][2], v, v, v"
            vals.extend(float(v) for v in line.split(',')[1:])
        else:                                            # 1-D map: "v, v, v"
            vals.extend(float(v) for v in line.split(','))
    finish()
    if not out:
        raise ValueError('no arrays in the OPeNDAP response')
    for k in [k for k in out if not k.endswith('_shape')]:
        n = int(np.prod(out[k + '_shape']))
        if out[k].size != n:
            raise ValueError(f'{k}: {out[k].size} values for shape {out[k + "_shape"]} (truncated response?)')
    return out


def parse_das(data):
    text = data.decode('utf-8')
    if not text.lstrip().startswith('Attributes {'):
        raise ValueError('not an OPeNDAP DAS')
    return text


def das_packing(das, var):
    i = das.index('\n    ' + var + ' {')
    block = das[i: das.index('}', i)]
    num = lambda key: float(re.search(key + r'\s+(-?[\d.eE+-]+)', block).group(1))
    return num('scale_factor'), num('add_offset'), num('_FillValue')


def fill_nans(a):
    """Fill NaNs from their neighbours (land and below-seabed cells), so interpolation near the
    coast never reads a hole. The exported wet mask says which cells were filled."""
    a = a.copy()
    m = np.isnan(a)
    if m.all():
        return a
    while m.any():
        p = np.pad(a, 1, constant_values=np.nan)
        nb = np.stack([p[:-2, 1:-1], p[2:, 1:-1], p[1:-1, :-2], p[1:-1, 2:]])
        cnt = (~np.isnan(nb)).sum(0)
        with np.errstate(invalid='ignore'):
            s = np.nansum(nb, 0)
        upd = m & (cnt > 0)
        a[upd] = s[upd] / cnt[upd]
        m = np.isnan(a)
    return a


def level_weights(dep, targets):
    """Each target depth from one model level, or linearly between the two around it."""
    levels = []
    for d in targets:
        k = int(np.searchsorted(dep, d))
        require(k < len(dep), f'model levels stop above {d} m')
        if dep[k] == d:
            levels.append([(k, 1.0)])
        else:
            w = (d - dep[k - 1]) / (dep[k] - dep[k - 1])
            levels.append([(k - 1, 1.0 - w), (k, w)])
    return levels


def cell_members(samples, centres, half=0.2501):
    """Indices of the samples inside each cell (|sample - centre| <= half)."""
    return [np.where(np.abs(samples - c) <= half)[0] for c in centres]


def contiguous_ranges(ks):
    ks = sorted(set(ks))
    ranges, start = [], ks[0]
    for a, b in zip(ks, ks[1:] + [None]):
        if b != a + 1:
            ranges.append((start, a))
            start = b
    return ranges


def hycom_axes(F):
    das = F.get(HYCOM + '.das', 'hycom.das', parse_das)
    ax = F.get(HYCOM + '.ascii?lat,lon,depth', 'hycom_axes.txt', parse_ascii)
    tim = F.get(HYCOM + '.ascii?time', 'hycom_time.txt', parse_ascii, timeout=300)['time']
    return das, ax['lat'], ax['lon'], ax['depth'], tim


def nearest_time(tim, when, tol_h=1.5):
    epoch = datetime(2000, 1, 1, tzinfo=timezone.utc)
    h = (when - epoch).total_seconds() / 3600
    k = int(np.argmin(np.abs(tim - h)))
    if abs(tim[k] - h) > tol_h:
        return None
    return k


def hours_to_time(h):
    return datetime(2000, 1, 1, tzinfo=timezone.utc) + timedelta(hours=float(h))


def fetch_hycom(F):
    das, lat, lon, dep, tim = hycom_axes(F)
    tidx = []
    for s in SNAPS:
        k = nearest_time(tim, s)
        require(k is not None, f'HYCOM has no output within 1.5 h of {s:%Y-%m-%d %H}Z')
        tidx.append(k)

    SI, SJ = 6, 3                                                    # 0.24 degree sampling
    i0, i1 = int(np.searchsorted(lat, LAT0 - 0.4)), int(np.searchsorted(lat, LAT1 + 0.4))
    j0, j1 = int(np.searchsorted(lon, LON0 - 0.4)), int(np.searchsorted(lon, LON1 + 0.4))
    sub_lat, sub_lon = lat[i0:i1 + 1:SI], lon[j0:j1 + 1:SJ]
    i1, j1 = i0 + (len(sub_lat) - 1) * SI, j0 + (len(sub_lon) - 1) * SJ

    levels = level_weights(dep, DEPTHS)
    ranges = contiguous_ranges([k for lv in levels for k, _ in lv])
    hvars = {'temp': 'water_temp', 'sal': 'salinity', 'u': 'water_u', 'v': 'water_v'}
    jobs = [(t, key, hv, r) for t in range(len(SNAPS)) for key, hv in hvars.items() for r in ranges]

    def job(j):
        t, key, hv, (k0, k1) = j
        url = f'{HYCOM}.ascii?{hv}.{hv}[{tidx[t]}][{k0}:1:{k1}][{i0}:{SI}:{i1}][{j0}:{SJ}:{j1}]'
        shape = [1, k1 - k0 + 1, len(sub_lat), len(sub_lon)]

        def parse(data):
            arr = parse_ascii(data)
            if arr.get(hv + '_shape') != shape:
                raise ValueError(f'{hv}: shape {arr.get(hv + "_shape")}, expected {shape}')
            return arr[hv].reshape(shape)[0]
        return j, F.get(url, f'hycom_{hv}_t{t}_k{k0}-{k1}.txt', parse)

    raw = {}
    with ThreadPoolExecutor(6) as ex:
        for j, a in ex.map(job, jobs):
            t, key, hv, (k0, k1) = j
            sc, off, fill = das_packing(das, hv)
            a = np.where(a == fill, np.nan, a * sc + off)
            for kk in range(k0, k1 + 1):
                raw[(t, key, kk)] = a[kk - k0]

    rows, cols = cell_members(sub_lat, LATS), cell_members(sub_lon, LONS)

    def regrid(a):
        out = np.full((len(LATS), len(LONS)), np.nan)
        for i, r in enumerate(rows):
            for j, c in enumerate(cols):
                blk = a[np.ix_(r, c)]
                if np.isfinite(blk).any():
                    out[i, j] = np.nanmean(blk)
        return out

    model = np.zeros((len(SNAPS), 4, len(DEPTHS), len(LATS), len(LONS)))
    wet = None
    for t in range(len(SNAPS)):
        for vi, key in enumerate(hvars):
            layer_wet = np.zeros((len(DEPTHS), len(LATS), len(LONS)), bool)
            for di, lv in enumerate(levels):
                a = sum(w * regrid(raw[(t, key, k)]) for k, w in lv)
                layer_wet[di] = np.isfinite(a)
                model[t, vi, di] = fill_nans(a)
            if wet is None:
                wet = layer_wet
            require((layer_wet == wet).all(), f'HYCOM land mask differs for {key} at {SNAPS[t]:%d %b}')
    times = [hours_to_time(tim[k]) for k in tidx]
    info = {'time_index': tidx, 'native_levels_m': [float(dep[k]) for lv in levels for k, _ in lv],
            'sample_step_deg': [float(round(sub_lat[1] - sub_lat[0], 3)), float(round(sub_lon[1] - sub_lon[0], 3))],
            'samples_per_cell': [int(min(len(r) for r in rows)), int(max(len(r) for r in rows)),
                                 int(min(len(c) for c in cols)), int(max(len(c) for c in cols))]}
    return model, wet, times, info


# ------------------------------------------------------------------ Argo
def p2z(p, lat):
    """Depth (m) from pressure (dbar): UNESCO 1983, Fofonoff and Millard."""
    x = math.sin(math.radians(lat)) ** 2
    g = 9.780318 * (1 + (5.2788e-3 + 2.36e-5 * x) * x) + 1.092e-6 * p
    return ((((-1.82e-15 * p + 2.279e-10) * p - 2.2512e-5) * p + 9.72659) * p) / g


def max_gap(z):
    return 20 if z <= 100 else 40 if z <= 300 else 80


def to_levels(z, v):
    """Profile onto ARGO_LEVELS; a real sampling gap wider than max_gap stays a gap."""
    out = np.full(len(ARGO_LEVELS), np.nan)
    if len(z) < 3:
        return out
    for n, L in enumerate(ARGO_LEVELS):
        if L < z[0]:
            if z[0] <= 12:
                out[n] = v[0]                    # shallowest sample stands in for the surface layer
            continue
        if L > z[-1]:
            break
        k = int(np.searchsorted(z, L))
        if z[k] == L:
            out[n] = v[k]
        elif z[k] - z[k - 1] <= max_gap(L):
            out[n] = v[k - 1] + (v[k] - v[k - 1]) * (L - z[k - 1]) / (z[k] - z[k - 1])
    return out


def rtqc(p, v, var):
    """Re-run Argo real-time tests on samples the data centre flagged good (pressure-sorted arrays).
    Returns the mask of samples that pass and the number each test removed. Delayed-mode flags can miss
    things: float 2902266 carries -2 C temperatures flagged good under salinity flagged bad."""
    n = {'global_range': 0, 'regional_range': 0, 'spike': 0, 'stuck': 0}
    ok = np.ones(len(v), bool)
    lo, hi = GLOBAL_RANGE[var]
    bad = (v < lo) | (v > hi)
    n['global_range'] = int(bad.sum())
    ok &= ~bad
    if var == 'temp':
        bad = ok & (p <= REGIONAL_PMAX) & (v < REGIONAL_TMIN)
        n['regional_range'] = int(bad.sum())
        ok &= ~bad
    idx = np.where(ok)[0]
    if len(idx) >= 3:
        hi_thr, lo_thr = SPIKE[var]
        vv, pp = v[idx], p[idx]
        s = np.abs(vv[1:-1] - (vv[2:] + vv[:-2]) / 2) - np.abs((vv[2:] - vv[:-2]) / 2)
        spk = s > np.where(pp[1:-1] < 500, hi_thr, lo_thr)
        n['spike'] = int(spk.sum())
        ok[idx[1:-1][spk]] = False
    idx = np.where(ok)[0]
    if len(idx) > 1 and np.all(v[idx] == v[idx[0]]):
        n['stuck'] = len(idx)
        ok[:] = False
    return ok, n


ARGO_COLS = ('platform_number,cycle_number,time,latitude,longitude,position_qc,data_mode,'
             'pres,pres_qc,temp,temp_qc,psal,psal_qc,pres_adjusted,pres_adjusted_qc,'
             'temp_adjusted,temp_adjusted_qc,psal_adjusted,psal_adjusted_qc,chla,chla_qc')


def argo_query():
    t_lo = (SNAPS[0] - timedelta(days=1.5)).strftime('%Y-%m-%dT%H:%M:%SZ')
    t_hi = (SNAPS[-1] + timedelta(days=1.5)).strftime('%Y-%m-%dT%H:%M:%SZ')
    return [ARGO_COLS, f'time>={t_lo}', f'time<={t_hi}', f'latitude>={LAT0}', f'latitude<={LAT1}',
            f'longitude>={LON0}', f'longitude<={LON1}', 'pres<=1100']


def parse_argo_csv(data):
    rows = list(csv.reader(io.StringIO(data.decode('utf-8'))))
    if not rows or rows[0] != ARGO_COLS.split(','):
        raise ValueError('unexpected Argo CSV header')
    return rows[0], rows[2:]                                           # row 1 holds units


def select_argo(names, rows, temperature_only=False):
    """Group rows into profiles, apply QC, grid onto ARGO_LEVELS and keep one profile per float per
    analysis (the closest in time). Returns (profiles, counts)."""
    ix = {n: i for i, n in enumerate(names)}
    profs = {}
    for r in rows:
        key = (r[ix['platform_number']].strip(), int(float(r[ix['cycle_number']])), r[ix['time']])
        profs.setdefault(key, []).append(r)

    counts = {'profiles_in_query': len(profs), 'position_qc': 0, 'outside_window': 0, 'too_few_levels': 0,
              'salinity_failed': 0, 'rtqc_samples_removed': {'temp': {}, 'psal': {}}}
    out = []
    for (wmo, cyc, tstr), rs in sorted(profs.items()):
        r0 = rs[0]
        if not re.fullmatch(r'\d{5,7}', wmo):
            raise ValueError(f'unexpected Argo platform number {wmo!r}')
        mode = r0[ix['data_mode']].strip()
        if mode not in ('R', 'A', 'D'):
            raise ValueError(f'unexpected Argo data mode {mode!r} for {wmo}')
        if r0[ix['position_qc']] not in ARGO_GOOD:
            counts['position_qc'] += 1
            continue
        lat, lon = float(r0[ix['latitude']]), float(r0[ix['longitude']])
        when = datetime.strptime(tstr, '%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=timezone.utc)
        snap = int(np.argmin([abs((when - s).total_seconds()) for s in SNAPS]))
        dt = abs((when - SNAPS[snap]).total_seconds())
        if dt > 1.5 * 86400:
            counts['outside_window'] += 1
            continue
        adj = '_adjusted' if mode in ('A', 'D') else ''      # delayed/adjusted values where they exist

        def column(var):
            p, v = [], []
            for r in rs:
                pr, x = r[ix['pres' + adj]], r[ix[var + adj]]
                if pr in ('', 'NaN') or x in ('', 'NaN'):
                    continue
                if r[ix['pres' + adj + '_qc']] not in ARGO_GOOD or r[ix[var + adj + '_qc']] not in ARGO_GOOD:
                    continue
                p.append(float(pr)); v.append(float(x))
            if not p:
                return np.full(len(ARGO_LEVELS), np.nan)
            o = np.argsort(p, kind='stable')
            p, v = np.array(p)[o], np.array(v)[o]
            ok, n = rtqc(p, v, var)
            tally = counts['rtqc_samples_removed'][var]
            for k, c in n.items():
                if c:
                    tally[k] = tally.get(k, 0) + c
            p, v = p[ok], v[ok]
            z = np.array([p2z(x, lat) for x in p])
            keep = np.concatenate([[True], np.diff(z) > 0.05])
            return to_levels(z[keep], v[keep])

        T, S = column('temp'), column('psal')
        C = chla_column(rs, ix, lat)
        nT, nS = int(np.isfinite(T).sum()), int(np.isfinite(S).sum())
        if nT < MIN_LEVELS:
            counts['too_few_levels'] += 1
            continue
        sq = 1
        if nS < MIN_LEVELS:
            if not temperature_only:
                counts['salinity_failed'] += 1
                continue
            S, sq = np.full(len(ARGO_LEVELS), np.nan), 0
        out.append({'w': wmo, 'c': cyc, 't': tstr[:16] + 'Z', 'la': round(lat, 3), 'lo': round(lon, 3),
                    's': snap, 'm': mode, 'sq': sq, 'dt': dt, 'when': when, 'T': T, 'S': S, 'C': C})

    best = {}
    for p in out:
        k = (p['w'], p['s'])
        if k not in best or p['dt'] < best[k]['dt']:
            best[k] = p
    counts['not_closest_in_time'] = len(out) - len(best)
    return sorted(best.values(), key=lambda p: (p['s'], p['w'])), counts


def chla_column(rs, ix, lat):
    """Chlorophyll where a float has good-flagged values. This dataset has no adjusted chla, and real-time
    chla is flagged 3 by rule, so in practice the column stays empty."""
    pts = sorted((float(r[ix['pres']]), float(r[ix['chla']])) for r in rs
                 if r[ix['chla_qc']] in ARGO_GOOD and r[ix['pres']] not in ('', 'NaN') and r[ix['chla']] not in ('', 'NaN'))
    if not pts:
        return np.full(len(ARGO_LEVELS), np.nan)
    z = np.array([p2z(a, lat) for a, _ in pts]); v = np.array([b for _, b in pts])
    keep = np.concatenate([[True], np.diff(z) > 0.05])
    return to_levels(z[keep], v[keep])


def fetch_argo(F, temperature_only=False):
    q = argo_query()
    url = ARGO + '?' + '&'.join(urllib.parse.quote(p, safe='=,') for p in q)
    names, rows = F.get(url, 'argo.csv', parse_argo_csv, timeout=900)
    profiles, counts = select_argo(names, rows, temperature_only)
    return profiles, counts, url


# ------------------------------------------------------------------ ETOPO1 and IBTrACS
def parse_etopo(data):
    rows = list(csv.reader(io.StringIO(data.decode('utf-8'))))
    if not rows or rows[0] != ['latitude', 'longitude', 'altitude']:
        raise ValueError('unexpected ETOPO CSV header')
    rows = rows[2:]
    lats = sorted({float(r[0]) for r in rows}); lons = sorted({float(r[1]) for r in rows})
    require(len(rows) == len(lats) * len(lons), 'ETOPO grid is not complete')
    alt = np.zeros((len(lats), len(lons)))
    li, lj = {v: i for i, v in enumerate(lats)}, {v: j for j, v in enumerate(lons)}
    for la, lo, a in rows:
        alt[li[float(la)], lj[float(lo)]] = float(a)
    return alt, lats, lons


def fetch_etopo(F):
    q = urllib.parse.quote(f'altitude[({LAT0}):6:({LAT1})][({LON0}):6:({LON1})]', safe='')
    alt, lats, lons = F.get(ETOPO + '?' + q, 'etopo_0.1deg.csv', parse_etopo)
    step = round(lats[1] - lats[0], 4)
    require(step == round(lons[1] - lons[0], 4) == 0.1, f'ETOPO spacing {step}, expected 0.1')
    require(abs(lats[0] - LAT0) < 1e-6 and abs(lons[0] - LON0) < 1e-6, 'ETOPO grid does not start at the domain corner')
    require(abs(lats[-1] - LAT1) < 1e-6 and abs(lons[-1] - LON1) < 1e-6, 'ETOPO grid does not end at the domain corner')
    land = (alt > 0).mean()
    require(0.05 < land < 0.6, f'ETOPO land fraction {land:.2f} is implausible for this domain')
    return alt, round(lats[0], 4), round(lons[0], 4), step, q


TRACK_COLS = ('SID', 'NAME', 'ISO_TIME', 'LAT', 'LON', 'USA_WIND', 'NEWDELHI_WIND', 'NEWDELHI_GRADE')


def parse_ibtracs(data):
    text = data.decode('utf-8', 'replace')
    reader = csv.DictReader(io.StringIO(text))
    missing = [c for c in TRACK_COLS if c not in (reader.fieldnames or [])]
    if missing:
        raise ValueError(f'IBTrACS header lacks {missing}')
    return list(reader)


def track_fixes(rows, name=STORM, year=STORM_YEAR):
    """Six-hourly fixes of one storm: [time, lat, lon, JTWC 1-min wind kt, IMD 3-min wind kt, IMD grade]."""
    num = lambda s: int(float(s)) if s.strip() else None
    fixes = []
    for d in rows:
        if d['NAME'] != name or not d['SID'].startswith(year) or d['ISO_TIME'][11:13] not in ('00', '06', '12', '18') \
                or d['ISO_TIME'][14:16] != '00':
            continue
        grade = d['NEWDELHI_GRADE'].strip()
        if grade not in IMD_GRADES:
            raise ValueError(f'unexpected IMD grade {grade!r} at {d["ISO_TIME"]}')
        fixes.append([d['ISO_TIME'][:16].replace(' ', 'T') + 'Z', float(d['LAT']), float(d['LON']),
                      num(d['USA_WIND']), num(d['NEWDELHI_WIND']), grade])
    require(len(fixes) >= 4, f'only {len(fixes)} fixes found for {name} {year}')
    require(all(a[0] < b[0] for a, b in zip(fixes, fixes[1:])), 'track fixes are not in time order')
    require(any(LAT0 <= f[1] <= LAT1 and LON0 <= f[2] <= LON1 for f in fixes), 'no track fix inside the domain')
    return fixes


def fetch_track(F):
    name = 'ibtracs_tail.csv'
    if not os.path.exists(F.path(name)):
        if not F.fetch:
            raise CacheMiss(name)
        # header plus the most recent seasons, via range requests (the full file is large)
        head = F.download(IBTRACS, {'Range': 'bytes=0-4000'}).decode().splitlines()[0]
        tail = F.download(IBTRACS, {'Range': 'bytes=-6500000'}, timeout=300).decode('utf-8', 'replace')
        data = (head + '\n' + '\n'.join(tail.splitlines()[1:]) + '\n').encode('utf-8')
        parse_ibtracs(data)
        F.store(name, data, IBTRACS + ' (header + last 6.5 MB)')
    return track_fixes(F.get(IBTRACS, name, parse_ibtracs))


# ------------------------------------------------------------------ HYCOM at each profile's time (P3)
def obs_request(prof, lat, lon, tim, nlev=33):
    """The OPeNDAP request for the 2x2 native columns around one profile at the nearest 3-hourly output."""
    t = nearest_time(tim, prof['when'])
    require(t is not None, f'no HYCOM output within 1.5 h of {prof["w"]} cycle {prof["c"]}')
    i = int(np.searchsorted(lat, prof['la'])) - 1
    j = int(np.searchsorted(lon, prof['lo'])) - 1
    sel = f'[{t}][0:1:{nlev - 1}][{i}:1:{i + 1}][{j}:1:{j + 1}]'
    url = f'{HYCOM}.ascii?water_temp.water_temp{sel},salinity.salinity{sel}'
    return url, f'hycom_obs/{prof["w"]}_{prof["c"]:03d}_t{t}.txt', t, (i, j)


def obs_column(arr, la, lo, das, lat2, lon2):
    """Bilinear in the horizontal over the wet corners, per native level -> (T, S). The response holds the
    arrays only, so the corner coordinates come from the axes."""
    fy = (la - lat2[0]) / (lat2[1] - lat2[0]); fx = (lo - lon2[0]) / (lon2[1] - lon2[0])
    w = np.array([[(1 - fy) * (1 - fx), (1 - fy) * fx], [fy * (1 - fx), fy * fx]])
    out = []
    for hv in ('water_temp', 'salinity'):
        sc, off, fill = das_packing(das, hv)
        a = arr[hv].reshape(arr[hv + '_shape'])[0]                   # (level, 2, 2)
        a = np.where(a == fill, np.nan, a * sc + off)
        ww = np.where(np.isfinite(a), w[None], 0.0)
        tot = ww.sum(axis=(1, 2))
        with np.errstate(invalid='ignore'):
            out.append(np.where(tot > 0, np.nansum(np.nan_to_num(a) * ww, axis=(1, 2)) / tot, np.nan))
    return out[0], out[1]


def column_on_levels(dep, v):
    fin = np.isfinite(v)
    res = np.full(len(ARGO_LEVELS), np.nan)
    if fin.sum() < 2:
        return res
    d, x = dep[fin], v[fin]
    L = np.array(ARGO_LEVELS, float)
    inside = L <= d[-1]
    res[inside] = np.interp(L[inside], d, x)
    return res


def fetch_model_at_obs(F, profiles, plan_only=False):
    das, lat, lon, dep, tim = hycom_axes(F)
    reqs = [obs_request(p, lat, lon, tim) for p in profiles]
    if plan_only:
        return reqs
    shape = [1, 33, 2, 2]

    def parse(data):
        arr = parse_ascii(data)
        for hv in ('water_temp', 'salinity'):
            if arr.get(hv + '_shape') != shape:
                raise ValueError(f'{hv}: shape {arr.get(hv + "_shape")}, expected {shape}')
        return arr

    def one(pr):
        p, (url, name, t, (i, j)) = pr
        arr = F.get(url, name, parse)
        T, S = obs_column(arr, p['la'], p['lo'], das, lat[i:i + 2], lon[j:j + 2])
        return column_on_levels(dep[:33], T), column_on_levels(dep[:33], S), hours_to_time(tim[t])

    with ThreadPoolExecutor(4) as ex:
        cols = list(ex.map(one, zip(profiles, reqs)))
    return cols


# ------------------------------------------------------------------ assemble
def group_hash(F, names):
    h = hashlib.sha256()
    for n in sorted(names):
        h.update(f'{n} {F.used[n]}\n'.encode())
    return h.hexdigest()


def inputs_block(F):
    man = F.manifest()
    groups = {'hycom': [n for n in F.used if n.startswith('hycom_') and not n.startswith('hycom_obs/')],
              'hycom_obs': [n for n in F.used if n.startswith('hycom_obs/')]}
    single = sorted(n for n in F.used if not any(n in g for g in groups.values()))
    out = {}
    for n in single:
        out[n] = {'sha256': F.used[n], 'accessed': man.get(n, {}).get('fetched', DEFAULT_ACCESSED)[:10]}
    for g, names in groups.items():
        if names:
            dates = sorted(man.get(n, {}).get('fetched', DEFAULT_ACCESSED)[:10] for n in names)
            out[g] = {'files': len(names), 'sha256': group_hash(F, names), 'accessed': dates[0] if dates[0] == dates[-1]
                      else f'{dates[0]}..{dates[-1]}'}
    return out


def build(cache, fetch=False, cafile=None, temperature_only=False, model_at_obs=False, log=print, allow_hosts=None):
    F = Fetcher(cache, fetch, cafile, allow_hosts=allow_hosts)

    model, wet, times, hinfo = fetch_hycom(F)
    require(np.isfinite(model).all(), 'model has gaps after filling')
    t_w = model[:, 0][:, wet]; s_w = model[:, 1][:, wet]
    require(-2 < t_w.min() and t_w.max() < 40, f'model temperature {t_w.min():.2f}..{t_w.max():.2f} C is implausible')
    require(0 <= s_w.min() and s_w.max() < 45, f'model salinity {s_w.min():.2f}..{s_w.max():.2f} is implausible')
    require(np.abs(model[:, 2:]).max() < 3, 'model current above 3 m/s')
    require(1000 < wet[0].sum() < len(LATS) * len(LONS), f'{wet[0].sum()} wet surface cells is implausible')
    speed = np.hypot(model[:, 2], model[:, 3])
    log('HYCOM analysis times:', ', '.join(f'{t:%d %b %H}Z' for t in times))
    log(f'  temperature {t_w.min():.2f}..{t_w.max():.2f} C, salinity {s_w.min():.2f}..{s_w.max():.2f}, '
        f'surface speed p99 {np.percentile(speed[:, 0][:, wet[0]], 99):.2f} m/s (model water only)')

    argo, acounts, argo_url = fetch_argo(F, temperature_only)
    per = [sum(1 for p in argo if p['s'] == s) for s in range(len(SNAPS))]
    require(len(argo) >= MIN_PROFILES and min(per) >= MIN_PER_SNAPSHOT,
            f'too few Argo profiles: {len(argo)} in total, per analysis {per}')
    log(f'Argo: {len(argo)} profiles from {len({p["w"] for p in argo})} floats; per analysis {per}'
        + (f'; {sum(1 for p in argo if not p["sq"])} temperature-only' if temperature_only else ''))
    removed = {k: v for k, v in acounts['rtqc_samples_removed'].items() if v}
    if removed:
        log('  RTQC re-check removed samples:', removed)

    alt, alat0, alon0, astep, etopo_q = fetch_etopo(F)
    log(f'ETOPO1: {alt.shape[0]} x {alt.shape[1]} at {astep} deg, {100 * (alt > 0).mean():.0f}% land')

    track = fetch_track(F)
    log(f'{STORM}: {len(track)} six-hourly fixes, {track[0][0]} to {track[-1][0]}, '
        f'peak {max(f[3] or 0 for f in track)} kt (JTWC), {max(f[4] or 0 for f in track)} kt (IMD)')

    argo_block = {
        'levels': ARGO_LEVELS,
        'profiles': [{k: p[k] for k in ('w', 'c', 't', 'la', 'lo', 's', 'm', 'sq')} for p in argo],
        'T': pack(np.concatenate([p['T'] for p in argo]), 'temp'),
        'S': pack(np.concatenate([p['S'] for p in argo]), 'sal'),
        'C': pack(np.concatenate([p['C'] for p in argo]), 'chl'),
    }
    obs_info = None
    if model_at_obs:
        cols = fetch_model_at_obs(F, argo)
        argo_block['model'] = {
            'T': pack(np.concatenate([c[0] for c in cols]), 'temp'),
            'S': pack(np.concatenate([c[1] for c in cols]), 'sal'),
            'times': [c[2].strftime('%Y-%m-%dT%H:%MZ') for c in cols],
        }
        obs_info = 'HYCOM at each profile\'s position (bilinear over the wet corners of the native grid) and nearest ' \
                   '3-hourly output, native levels interpolated linearly to the Argo levels'
        log(f'HYCOM at observation times: {len(cols)} columns')

    model_note = ('HYCOM GOFS 3.1 analysis (GLBy0.08/expt_93.0), tds.hycom.org. Native grid sampled every '
                  f'{hinfo["sample_step_deg"][0]} deg and averaged over the samples inside each 0.5 deg cell; 75 m and '
                  '750 m interpolated between model levels.')
    payload = {
        'schema': SCHEMA,
        'meta': {
            'title': 'Cyclone Amphan, May 2020',
            'times': [t.strftime('%Y-%m-%dT%H:%MZ') for t in times],
            'lat0': LAT0, 'lon0': LON0, 'step': STEP, 'nlat': len(LATS), 'nlon': len(LONS), 'depths': DEPTHS,
            'vars': ['temp', 'sal', 'u', 'v'], 'quant': QUANT, 'missing': MISSING,
            'sources': {
                'model': model_note,
                'argo': 'Argo GDAC via erddap.ifremer.fr (ArgoFloats); adjusted values for delayed-mode and adjusted '
                        'profiles, data-centre QC flags 1, 2, 5, 8 kept, then the global-range, regional-range, spike '
                        'and stuck-value tests re-run; the closest profile to each analysis within 1.5 days',
                'bathymetry': 'ETOPO1 via upwell.pfeg.noaa.gov ERDDAP (etopo180), sampled every 0.1 deg',
                'track': 'IBTrACS v04r01 North Indian basin (NOAA NCEI); JTWC 1-minute and IMD 3-minute winds'},
            'citations': CITATIONS,
            'provenance': {
                'pipeline': {'script': 'data-pipeline/build_snapshot.py', 'version': PIPELINE_VERSION},
                'model': {'url': HYCOM, 'variables': ['water_temp', 'salinity', 'water_u', 'water_v'],
                          'time_index': hinfo['time_index'], 'native_levels_m': hinfo['native_levels_m'],
                          'sample_step_deg': hinfo['sample_step_deg'],
                          'samples_per_cell': hinfo['samples_per_cell'],
                          'filled_cells': 'model values in cells without model water are filled from neighbours; '
                                          'mask.wet marks the cells that hold model water'},
                'model_at_obs': obs_info,
                'argo': {'url': ARGO, 'query': argo_query(),
                         'qc': {'flags_kept': sorted(ARGO_GOOD), 'adjusted_for_modes': ['A', 'D'],
                                'global_range': GLOBAL_RANGE,
                                'regional_range': f'temperature >= {REGIONAL_TMIN} C above {REGIONAL_PMAX:g} dbar',
                                'spike_thresholds': SPIKE, 'min_levels': MIN_LEVELS,
                                'temperature_only_profiles': temperature_only,
                                'window_days': 1.5, 'one_per_float_per_analysis': True},
                         'counts': acounts},
                'bathymetry': {'url': ETOPO, 'query': urllib.parse.unquote(etopo_q)},
                'track': {'url': IBTRACS, 'storm': f'{STORM} {STORM_YEAR}', 'hours': ['00', '06', '12', '18']},
            },
            'inputs': inputs_block(F),
        },
        # Int16, laid out [time][variable][level][lat][lon]
        'model': base64.b64encode(b''.join(
            base64.b64decode(pack(model[t, vi], k)) for t in range(len(SNAPS))
            for vi, k in enumerate(['temp', 'sal', 'u', 'v']))).decode('ascii'),
        # one bit per cell, [level][lat][lon], most significant bit first; 1 = the cell holds model water
        'mask': {'wet': base64.b64encode(np.packbits(wet.astype(np.uint8).ravel()).tobytes()).decode('ascii'),
                 'layout': '[level][lat][lon]', 'bitorder': 'big'},
        'alt': base64.b64encode(np.clip(np.round(alt), -32767, 32767).astype('<i2').tobytes()).decode('ascii'),
        'altGrid': {'lat0': alat0, 'lon0': alon0, 'step': astep, 'nlat': alt.shape[0], 'nlon': alt.shape[1]},
        'argo': argo_block,
        'track': {'name': STORM, 'fields': ['time', 'lat', 'lon', 'jtwc_wind_kt', 'imd_wind_kt', 'imd_grade'],
                  'fixes': track},
    }
    return payload, F


def serialise(payload):
    """Compact, ASCII-only JSON with '</' escaped, so the text can sit inside a <script> element."""
    text = json.dumps(payload, separators=(',', ':'), ensure_ascii=True, allow_nan=False)
    return (text.replace('</', '<\\/') + '\n').encode('ascii')


def main(argv=None):
    here = os.path.dirname(os.path.abspath(__file__))
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--cache', default=os.environ.get('OCEANLENS_CACHE') or os.path.join(here, 'cache'),
                    help='raw download cache (default: $OCEANLENS_CACHE or data-pipeline/cache)')
    ap.add_argument('--out', default=os.path.join(here, '..', 'data', 'amphan-2020.json'))
    ap.add_argument('--fetch', action='store_true', help='download files missing from the cache')
    ap.add_argument('--ca-bundle', default=os.environ.get('OCEANLENS_CA_BUNDLE'),
                    help='PEM bundle for HTTPS (e.g. behind a TLS-scanning antivirus)')
    ap.add_argument('--allow-host', action='append', metavar='HOST',
                    help='with --fetch, download only from these hosts (repeatable)')
    ap.add_argument('--check', action='store_true', help='rebuild and compare with --out instead of writing it')
    ap.add_argument('--temperature-only', action='store_true',
                    help='keep Argo profiles whose salinity failed QC (temperature comparisons only)')
    ap.add_argument('--model-at-obs', action='store_true',
                    help='add HYCOM columns at each profile\'s position and time (argo.model)')
    ap.add_argument('--plan-model-at-obs', action='store_true',
                    help='print the requests --model-at-obs would make and stop')
    args = ap.parse_args(argv)

    try:
        if args.plan_model_at_obs:
            F = Fetcher(args.cache)
            argo, _, _ = fetch_argo(F, args.temperature_only)
            reqs = fetch_model_at_obs(F, argo, plan_only=True)
            todo = [r for r in reqs if not os.path.exists(F.path(r[1]))]
            print(f'{len(reqs)} profiles -> {len(reqs)} requests ({len(todo)} not cached yet)')
            for url, name, t, ij in reqs[:3]:
                print(' ', name, url)
            return 0
        payload, F = build(args.cache, args.fetch, args.ca_bundle, args.temperature_only, args.model_at_obs,
                           allow_hosts=args.allow_host)
    except CacheMiss as e:
        print(f'error: {e} is not in the cache ({args.cache}); rerun with --fetch to download it', file=sys.stderr)
        return 2
    except (InvariantError, FetchError, ValueError) as e:
        print(f'error: {e}', file=sys.stderr)
        return 1
    data = serialise(payload)
    out = os.path.normpath(args.out)
    if args.check:
        if not os.path.exists(out):
            print(f'check failed: {out} does not exist', file=sys.stderr)
            return 1
        with open(out, 'rb') as f:
            old = f.read()
        if old == data:
            print(f'check ok: {out} matches a rebuild from the cache')
            return 0
        try:
            prev = json.loads(old)
            diff = [k for k in payload if prev.get(k) != json.loads(data)[k]]
        except ValueError:
            diff = ['(not JSON)']
        print(f'check failed: {out} differs from a rebuild in {diff}', file=sys.stderr)
        return 1
    write_atomic(out, data)
    print(f'wrote {len(data) / 1e6:.2f} MB to {out}; downloaded {F.downloaded / 1e6:.1f} MB this run')
    return 0


if __name__ == '__main__':
    sys.exit(main())
