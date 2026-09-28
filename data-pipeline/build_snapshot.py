#!/usr/bin/env python3
"""Build the real-data snapshot that OceanLens3D.html embeds (Cyclone Amphan, May 2020).

Sources, all open and needing no account:
  model       HYCOM GOFS 3.1 global analysis, GLBy0.08/expt_93.0 (tds.hycom.org, OPeNDAP ASCII)
  in situ     Argo GDAC profiles via Ifremer ERDDAP (ArgoFloats), QC flags applied
  bathymetry  ETOPO1 via NOAA ERDDAP (etopo180), 0.1 degree
  track       IBTrACS v04r01, North Indian basin (NOAA NCEI)

Only numpy is needed. Raw responses are cached in --cache, so a rerun does not download again.

    python build_snapshot.py --cache DIR [--ca-bundle PEM]

The result is written into ../OceanLens3D.html between the REAL-DATA markers.
"""
import argparse, base64, csv, io, json, math, os, re, ssl, sys, time, urllib.parse, urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import numpy as np

# ---- what the app expects (must match OceanLens3D.html) ----
LAT0, LAT1, LON0, LON1, STEP = 6.0, 22.0, 61.0, 92.0, 0.5
LATS = np.round(np.arange(LAT0, LAT1 + 1e-9, STEP), 3)          # 33
LONS = np.round(np.arange(LON0, LON1 + 1e-9, STEP), 3)          # 63
DEPTHS = [0, 20, 50, 75, 100, 150, 200, 300, 500, 750, 1000]
ARGO_LEVELS = list(range(5, 101, 5)) + list(range(110, 301, 10)) + list(range(325, 1001, 25))
T0 = datetime(2020, 5, 4, 12, tzinfo=timezone.utc)               # 8 snapshots, 3 days apart: 4-25 May
SNAPS = [T0 + timedelta(days=3 * i) for i in range(8)]
STORM, STORM_YEAR = 'AMPHAN', '2020'

HYCOM = 'https://tds.hycom.org/thredds/dodsC/GLBy0.08/expt_93.0'
ARGO = 'https://erddap.ifremer.fr/erddap/tabledap/ArgoFloats.csv'
ETOPO = 'https://upwell.pfeg.noaa.gov/erddap/griddap/etopo180.csv'
IBTRACS = ('https://www.ncei.noaa.gov/data/international-best-track-archive-for-climate-stewardship-ibtracs/'
           'v04r01/access/csv/ibtracs.NI.list.v04r01.csv')

# Int16 packing: value = offset + q * scale
QUANT = {'temp': (15.0, 0.001), 'sal': (34.0, 0.0005), 'u': (0.0, 0.0001), 'v': (0.0, 0.0001), 'chl': (0.0, 0.001)}
MISSING = -32768


class Fetcher:
    """GET with an on-disk cache and a few retries."""

    def __init__(self, cache, cafile):
        self.cache = cache
        os.makedirs(cache, exist_ok=True)
        self.ctx = ssl.create_default_context(cafile=cafile) if cafile else ssl.create_default_context()
        self.downloaded = 0

    def get(self, url, name, headers=None, timeout=180, retries=4):
        path = os.path.join(self.cache, name)
        if os.path.exists(path):
            with open(path, 'rb') as f:
                return f.read()
        for attempt in range(retries):
            try:
                req = urllib.request.Request(url, headers={'User-Agent': 'OceanLens3D-snapshot/1.0', **(headers or {})})
                with urllib.request.urlopen(req, context=self.ctx, timeout=timeout) as r:
                    data = r.read()
                with open(path, 'wb') as f:
                    f.write(data)
                self.downloaded += len(data)
                return data
            except Exception:
                if attempt == retries - 1:
                    raise
                time.sleep(2 + 4 * attempt)


def pack(values, key):
    """float array -> base64 little-endian Int16 (NaN -> MISSING)."""
    off, sc = QUANT[key]
    a = np.asarray(values, dtype=np.float64)
    q = np.where(np.isnan(a), MISSING, np.clip(np.round((a - off) / sc), -32767, 32767)).astype('<i2')
    return base64.b64encode(q.tobytes()).decode('ascii')


# ------------------------------------------------------------------ HYCOM
def parse_ascii(text):
    """Arrays in an OPeNDAP .ascii response -> {name: flat float array, name+'_shape': shape}."""
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
    return out


def das_packing(das, var):
    i = das.index('\n    ' + var + ' {')
    block = das[i: das.index('}', i)]
    num = lambda key: float(re.search(key + r'\s+(-?[\d.eE+-]+)', block).group(1))
    return num('scale_factor'), num('add_offset'), num('_FillValue')


def fill_nans(a):
    """Fill NaNs from their neighbours (land and below-seabed cells), so interpolation near the
    coast never reads a hole. The app masks land and seabed itself."""
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


def fetch_hycom(F):
    das = F.get(HYCOM + '.das', 'hycom.das').decode()
    ax = parse_ascii(F.get(HYCOM + '.ascii?lat,lon,depth', 'hycom_axes.txt').decode())
    tim = parse_ascii(F.get(HYCOM + '.ascii?time', 'hycom_time.txt', timeout=300).decode())['time']
    lat, lon, dep = ax['lat'], ax['lon'], ax['depth']

    epoch = datetime(2000, 1, 1, tzinfo=timezone.utc)
    tidx = []
    for s in SNAPS:
        h = (s - epoch).total_seconds() / 3600
        k = int(np.argmin(np.abs(tim - h)))
        if abs(tim[k] - h) > 1.5:
            sys.exit(f'HYCOM has no analysis within 1.5 h of {s:%Y-%m-%d %H}Z')
        tidx.append(k)

    SI, SJ = 6, 3                                                    # 0.24 degree sampling
    i0, i1 = int(np.searchsorted(lat, LAT0 - 0.4)), int(np.searchsorted(lat, LAT1 + 0.4))
    j0, j1 = int(np.searchsorted(lon, LON0 - 0.4)), int(np.searchsorted(lon, LON1 + 0.4))
    sub_lat, sub_lon = lat[i0:i1 + 1:SI], lon[j0:j1 + 1:SJ]
    i1, j1 = i0 + (len(sub_lat) - 1) * SI, j0 + (len(sub_lon) - 1) * SJ

    # each app level from one HYCOM level, or linearly between the two around it (75 m, 750 m)
    levels = []
    for d in DEPTHS:
        k = int(np.searchsorted(dep, d))
        levels.append([(k, 1.0)] if dep[k] == d else [(k - 1, (dep[k] - d) / (dep[k] - dep[k - 1])),
                                                      (k, (d - dep[k - 1]) / (dep[k] - dep[k - 1]))])
    need = sorted({k for lv in levels for k, _ in lv})
    ranges, start = [], need[0]
    for a, b in zip(need, need[1:] + [None]):
        if b != a + 1:
            ranges.append((start, a))
            start = b

    hvars = {'temp': 'water_temp', 'sal': 'salinity', 'u': 'water_u', 'v': 'water_v'}
    jobs = [(t, key, hv, r) for t in range(len(SNAPS)) for key, hv in hvars.items() for r in ranges]

    def job(j):
        t, key, hv, (k0, k1) = j
        url = f'{HYCOM}.ascii?{hv}.{hv}[{tidx[t]}][{k0}:1:{k1}][{i0}:{SI}:{i1}][{j0}:{SJ}:{j1}]'
        arr = parse_ascii(F.get(url, f'hycom_{hv}_t{t}_k{k0}-{k1}.txt').decode())
        return j, arr[hv].reshape(arr[hv + '_shape'])[0]            # (nk, nlat, nlon)

    print(f'HYCOM: {len(jobs)} requests ({len(SNAPS)} times x 4 variables x {len(ranges)} level groups)')
    raw = {}
    with ThreadPoolExecutor(6) as ex:
        for n, (j, a) in enumerate(ex.map(job, jobs), 1):
            t, key, hv, (k0, k1) = j
            sc, off, fill = das_packing(das, hv)
            a = np.where(a == fill, np.nan, a * sc + off)
            for kk in range(k0, k1 + 1):
                raw[(t, key, kk)] = a[kk - k0]
            if n % 40 == 0:
                print(f'  {n}/{len(jobs)}')

    # 0.5 degree cells: average the 0.24 degree samples inside each cell
    rows = [np.where(np.abs(sub_lat - la) <= 0.2501)[0] for la in LATS]
    cols = [np.where(np.abs(sub_lon - lo) <= 0.2501)[0] for lo in LONS]

    def regrid(a):
        out = np.full((len(LATS), len(LONS)), np.nan)
        for i, r in enumerate(rows):
            for j, c in enumerate(cols):
                blk = a[np.ix_(r, c)]
                if np.isfinite(blk).any():
                    out[i, j] = np.nanmean(blk)
        return out

    model = np.zeros((len(SNAPS), 4, len(DEPTHS), len(LATS), len(LONS)))
    wet = np.zeros((len(DEPTHS), len(LATS), len(LONS)), bool)
    for t in range(len(SNAPS)):
        for vi, key in enumerate(hvars):
            for di, lv in enumerate(levels):
                a = sum(w * regrid(raw[(t, key, k)]) for k, w in lv)
                if t == 0 and vi == 0:
                    wet[di] = np.isfinite(a)
                model[t, vi, di] = fill_nans(a)
    return model, wet, [datetime.fromtimestamp((epoch.timestamp() + tim[k] * 3600), timezone.utc) for k in tidx]


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


def fetch_argo(F):
    cols = ('platform_number,cycle_number,time,latitude,longitude,position_qc,data_mode,'
            'pres,pres_qc,temp,temp_qc,psal,psal_qc,pres_adjusted,pres_adjusted_qc,'
            'temp_adjusted,temp_adjusted_qc,psal_adjusted,psal_adjusted_qc,chla,chla_qc')
    t_lo = (SNAPS[0] - timedelta(days=1.5)).strftime('%Y-%m-%dT%H:%M:%SZ')
    t_hi = (SNAPS[-1] + timedelta(days=1.5)).strftime('%Y-%m-%dT%H:%M:%SZ')
    parts = [cols, f'time>={t_lo}', f'time<={t_hi}', f'latitude>={LAT0}', f'latitude<={LAT1}',
             f'longitude>={LON0}', f'longitude<={LON1}', 'pres<=1100']
    url = ARGO + '?' + '&'.join(urllib.parse.quote(p, safe='=,') for p in parts)
    rows = list(csv.reader(io.StringIO(F.get(url, 'argo.csv', timeout=900).decode())))
    names, rows = rows[0], rows[2:]                                    # row 1 holds units
    ix = {n: i for i, n in enumerate(names)}
    good = {'1', '2', '5', '8'}                                         # good / probably good / changed / estimated

    profs = {}
    for r in rows:
        key = (r[ix['platform_number']].strip(), int(float(r[ix['cycle_number']])), r[ix['time']])
        profs.setdefault(key, []).append(r)

    out = []
    for (wmo, cyc, tstr), rs in profs.items():
        r0 = rs[0]
        if r0[ix['position_qc']] not in good:
            continue
        lat, lon = float(r0[ix['latitude']]), float(r0[ix['longitude']])
        when = datetime.strptime(tstr, '%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=timezone.utc)
        snap = int(np.argmin([abs((when - s).total_seconds()) for s in SNAPS]))
        if abs((when - SNAPS[snap]).total_seconds()) > 1.5 * 86400:
            continue
        mode = r0[ix['data_mode']].strip()
        adj = '_adjusted' if mode in ('A', 'D') else ''      # delayed/adjusted values where they exist

        def column(var, qc_var):
            z, v = [], []
            for r in rs:
                p, x = r[ix['pres' + adj]], r[ix[var]]
                if p in ('', 'NaN') or x in ('', 'NaN'):
                    continue
                if r[ix['pres' + adj + '_qc']] not in good or r[ix[qc_var]] not in good:
                    continue
                z.append(p2z(float(p), lat)); v.append(float(x))
            if not z:
                return np.full(len(ARGO_LEVELS), np.nan)
            o = np.argsort(z)
            z, v = np.array(z)[o], np.array(v)[o]
            keep = np.concatenate([[True], np.diff(z) > 0.05])
            return to_levels(z[keep], v[keep])

        T = column('temp' + adj, 'temp' + adj + '_qc')
        S = column('psal' + adj, 'psal' + adj + '_qc')
        C = column('chla', 'chla_qc')        # this dataset has no adjusted chla; only good-flagged values pass
        if np.isfinite(T).sum() < 10 or np.isfinite(S).sum() < 10:
            continue
        out.append({'w': wmo, 'c': cyc, 't': tstr[:16] + 'Z', 'la': round(lat, 3), 'lo': round(lon, 3),
                    's': snap, 'm': mode, 'dt': abs((when - SNAPS[snap]).total_seconds()), 'T': T, 'S': S, 'C': C})

    # one profile per float per snapshot: the closest in time
    best = {}
    for p in out:
        k = (p['w'], p['s'])
        if k not in best or p['dt'] < best[k]['dt']:
            best[k] = p
    return sorted(best.values(), key=lambda p: (p['s'], p['w']))


# ------------------------------------------------------------------ ETOPO1 and IBTrACS
def fetch_etopo(F):
    q = urllib.parse.quote(f'altitude[({LAT0}):6:({LAT1})][({LON0}):6:({LON1})]', safe='')
    rows = list(csv.reader(io.StringIO(F.get(ETOPO + '?' + q, 'etopo_0.1deg.csv').decode())))[2:]
    lats = sorted({float(r[0]) for r in rows}); lons = sorted({float(r[1]) for r in rows})
    alt = np.zeros((len(lats), len(lons)))
    li, lj = {v: i for i, v in enumerate(lats)}, {v: j for j, v in enumerate(lons)}
    for la, lo, a in rows:
        alt[li[float(la)], lj[float(lo)]] = float(a)
    return alt, lats[0], lons[0], round(lats[1] - lats[0], 4)


def fetch_track(F):
    path = os.path.join(F.cache, 'ibtracs_tail.csv')
    if not os.path.exists(path):                     # header plus the most recent seasons, via range requests
        head = F.get(IBTRACS, 'ibtracs_head.bin', headers={'Range': 'bytes=0-4000'}).decode().splitlines()[0]
        tail = F.get(IBTRACS, 'ibtracs_tail.bin', headers={'Range': 'bytes=-6500000'}, timeout=300).decode('utf-8', 'replace')
        with open(path, 'w', encoding='utf-8') as f:
            f.write(head + '\n' + '\n'.join(tail.splitlines()[1:]))
    fixes = []
    for d in csv.DictReader(open(path, encoding='utf-8')):
        if d['NAME'] != STORM or not d['SID'].startswith(STORM_YEAR) or d['ISO_TIME'][11:13] not in ('00', '06', '12', '18'):
            continue
        num = lambda k: int(float(d[k])) if d.get(k, '').strip() not in ('', ' ') else None
        fixes.append([d['ISO_TIME'][:16].replace(' ', 'T') + 'Z', float(d['LAT']), float(d['LON']),
                      num('USA_WIND'), num('NEWDELHI_WIND'), d.get('NEWDELHI_GRADE', '').strip()])
    return fixes


# ------------------------------------------------------------------ assemble and embed
def embed(html_path, payload):
    with open(html_path, encoding='utf-8') as f:
        s = f.read()
    block = ('<!-- REAL-DATA:BEGIN generated by data-pipeline/build_snapshot.py, do not edit by hand -->\n<script>\n'
             'const REAL_DATA = ' + json.dumps(payload, separators=(',', ':')) + ';\n</script>\n<!-- REAL-DATA:END -->')
    b, e = s.find('<!-- REAL-DATA:BEGIN'), s.find('<!-- REAL-DATA:END -->')
    if b >= 0 and e > b:
        s = s[:b] + block + s[e + len('<!-- REAL-DATA:END -->'):]
    else:
        anchor = '<script>\n/* ============================================================\n   SYNTHETIC OCEAN DATASET'
        if anchor not in s:
            sys.exit('could not find where to insert the data block')
        s = s.replace(anchor, block + '\n' + anchor, 1)
    with open(html_path, 'w', encoding='utf-8', newline='') as f:
        f.write(s)


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--cache', default=os.path.join(here, 'cache'))
    ap.add_argument('--ca-bundle', default=None, help='PEM bundle for HTTPS (e.g. with a TLS-scanning antivirus)')
    ap.add_argument('--html', default=os.path.join(here, '..', 'OceanLens3D.html'))
    args = ap.parse_args()
    F = Fetcher(args.cache, args.ca_bundle)

    model, wet, times = fetch_hycom(F)
    speed = np.hypot(model[:, 2], model[:, 3])
    print('HYCOM analysis times:', ', '.join(f'{t:%d %b %H}Z' for t in times))
    print(f'  temperature {np.nanmin(model[:, 0]):.2f}..{np.nanmax(model[:, 0]):.2f} C, '
          f'salinity {np.nanmin(model[:, 1][:, :, wet[0]]):.2f}..{np.nanmax(model[:, 1][:, :, wet[0]]):.2f}, '
          f'surface speed p99 {np.nanpercentile(speed[:, 0][:, wet[0]], 99):.2f} m/s')

    argo = fetch_argo(F)
    print(f'Argo: {len(argo)} profiles from {len({p["w"] for p in argo})} floats; per snapshot',
          [sum(1 for p in argo if p['s'] == s) for s in range(len(SNAPS))])

    alt, alat0, alon0, astep = fetch_etopo(F)
    print(f'ETOPO1: {alt.shape[0]} x {alt.shape[1]} at {astep} deg, {100 * (alt > 0).mean():.0f}% land')

    track = fetch_track(F)
    print(f'{STORM}: {len(track)} six-hourly fixes, {track[0][0]} to {track[-1][0]}, peak {max(f[3] or 0 for f in track)} kt (JTWC)')

    payload = {
        'meta': {
            'title': 'Cyclone Amphan, May 2020',
            'times': [t.strftime('%Y-%m-%dT%H:%MZ') for t in times],
            'lat0': LAT0, 'lon0': LON0, 'step': STEP, 'nlat': len(LATS), 'nlon': len(LONS), 'depths': DEPTHS,
            'vars': ['temp', 'sal', 'u', 'v'], 'quant': QUANT, 'missing': MISSING,
            'sources': {
                'model': 'HYCOM GOFS 3.1 global analysis (GLBy0.08/expt_93.0), tds.hycom.org; 1/12 deg, '
                         'averaged into 0.5 deg cells; 75 m and 750 m interpolated between model levels',
                'argo': 'Argo GDAC via erddap.ifremer.fr (ArgoFloats); adjusted values for delayed/adjusted '
                        'profiles, QC flags 1, 2, 5, 8 kept; nearest profile within 1.5 days of each snapshot',
                'bathymetry': 'ETOPO1 via upwell.pfeg.noaa.gov ERDDAP (etopo180), sampled every 0.1 deg',
                'track': 'IBTrACS v04r01 North Indian basin (NOAA NCEI); JTWC and IMD winds'},
            'built': datetime.now(timezone.utc).strftime('%Y-%m-%d'),
        },
        # Int16, laid out [time][variable][level][lat][lon]
        'model': base64.b64encode(np.concatenate([
            np.clip(np.round((model[:, vi] - QUANT[k][0]) / QUANT[k][1]), -32767, 32767).astype('<i2')[:, None]
            for vi, k in enumerate(['temp', 'sal', 'u', 'v'])], axis=1).tobytes()).decode('ascii'),
        'alt': base64.b64encode(np.clip(np.round(alt), -32767, 32767).astype('<i2').tobytes()).decode('ascii'),
        'altGrid': {'lat0': alat0, 'lon0': alon0, 'step': astep, 'nlat': alt.shape[0], 'nlon': alt.shape[1]},
        'argo': {
            'levels': ARGO_LEVELS,
            'profiles': [{k: p[k] for k in ('w', 'c', 't', 'la', 'lo', 's', 'm')} for p in argo],
            'T': pack(np.concatenate([p['T'] for p in argo]), 'temp'),
            'S': pack(np.concatenate([p['S'] for p in argo]), 'sal'),
            'C': pack(np.concatenate([p['C'] for p in argo]), 'chl'),
        },
        'track': {'name': STORM, 'fixes': track},
    }
    embed(args.html, payload)
    size = len(json.dumps(payload, separators=(',', ':')))
    print(f'embedded {size / 1e6:.2f} MB into {os.path.normpath(args.html)}; downloaded {F.downloaded / 1e6:.1f} MB this run')


if __name__ == '__main__':
    main()
