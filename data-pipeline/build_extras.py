#!/usr/bin/env python3
"""Build data/amphan-2020-extras.json: the real data behind the drift tool and the satellite products.

    python data-pipeline/build_extras.py                 # rebuild from the raw cache
    python data-pipeline/build_extras.py --fetch         # download what the cache lacks
    python data-pipeline/build_extras.py --check         # rebuild and compare with the committed file

It holds, on the main snapshot's 0.5 deg grid unless said otherwise:
- surf.hycom: HYCOM GOFS 3.1 surface currents every 6 hours, 4-25 May 2020 (85 times; the main snapshot has only the
  8 analyses, 3 days apart, which cannot follow currents that turn in about two days).
- surf.glorys: GLORYS12V1 surface currents, the 22 daily means 4-25 May (native NetCDF-4 in byte ranges, as for the
  main GLORYS snapshot).
- drifters: the NOAA Global Drifter Program's hourly positions inside the map, 4-25 May, with drogue status.
- sat.sst: NOAA OISST v2.1 (AVHRR only) daily sea-surface temperature at 0.25 deg on the 8 analysis dates, extracted
  as NetCDF from the INCOIS Live Access Server (las.incois.gov.in).

Like build_snapshot.py it needs Python 3 and numpy only, reads every raw file through the same cache (SHA-256 in
manifest.json) and never touches the network without --fetch.
"""
import argparse, base64, csv, io, json, os, ssl, sys, urllib.error, urllib.parse, urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import numpy as np

import build_snapshot as bs
from build_snapshot import CacheMiss, FetchError, InvariantError, require

SURF_TIMES = [bs.T0 + timedelta(hours=6 * i) for i in range(85)]          # 4 May 12Z .. 25 May 12Z
DAYS = [bs.T0 + timedelta(days=i) for i in range(22)]                     # daily means centred at 12Z
END = DAYS[-1]
GDP = 'https://erddap.aoml.noaa.gov/gdp/erddap/tabledap/drifter_hourly_qc.csv'
GDP_COLS = 'ID,time,latitude,longitude,drogue_lost_date'
LAS = 'https://las.incois.gov.in/las'
OISST = {'dataset': 'id-a153fb198f', 'variable': 'SST-id-a153fb198f',
         'name': 'NOAA High Resolution SST AVHRR only (data provided by the NOAA OAR ESRL)'}
OISST_GRID = {'lat0': 6.125, 'lon0': 61.125, 'step': 0.25, 'nlat': 64, 'nlon': 124}
SST_QUANT = (0.0, 0.01)
HOSTS = bs.DEFAULT_HOSTS | {'erddap.aoml.noaa.gov', 'las.incois.gov.in'}
MIN_DRIFTER_HOURS = 24

CITATIONS = [
    {'id': 'gdp', 'doi': '10.25921/x46c-3620', 'text': 'Elipot, S., Sykulski, A., Lumpkin, R., Centurioni, L. and Pazos, M. (2022). Hourly location, '
                           'current velocity, and temperature collected from Global Drifter Program drifters world-wide. '
                           'NOAA National Centers for Environmental Information, dataset, doi:10.25921/x46c-3620. '
                           'Served by NOAA AOML ERDDAP (drifter_hourly_qc); CC BY 4.0.'},
    {'id': 'oisst', 'doi': '10.1175/JCLI-D-20-0166.1', 'text': 'Huang, B., Liu, C., Banzon, V., Freeman, E., Graham, G., Hankins, B., Smith, T. and Zhang, '
                             'H.-M. (2021). Improvements of the Daily Optimum Interpolation Sea Surface Temperature '
                             '(DOISST) Version 2.1. Journal of Climate, 34, 2923-2939. NOAA OISST v2.1 (AVHRR only), '
                             'provided by NOAA PSL and extracted from the INCOIS Live Access Server (las.incois.gov.in).'},
]


# ------------------------------------------------------------------ INCOIS LAS
def las_xml(ds, var, t, box=(bs.LON0, bs.LON1, bs.LAT0, bs.LAT1)):
    """A LAS product request: one variable as a NetCDF file over the map box, for one day at 12:00."""
    when = t.strftime('%d-%b-%Y %H:%M')
    return ('<?xml version="1.0"?><lasRequest href="file:las.xml">'
            "<link match=\"/lasdata/operations/operation[@ID='Data_Extract_netCDF']\"></link>"
            '<properties><ferret><view>xyt</view></ferret></properties>'
            f'<args><link match="/lasdata/datasets/{ds}/variables/{var}"></link><region>'
            f'<range type="x" low="{box[0]:g}" high="{box[1]:g}"/><range type="y" low="{box[2]:g}" high="{box[3]:g}"/>'
            f'<range type="t" low="{when}" high="{when}"/></region></args></lasRequest>')


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def las_output_url(F, url):
    """LAS answers a product request with a redirect to the file it wrote, as http://; the same server serves it over
    https. The redirect must stay on the LAS host and point into its output folder."""
    ctx = ssl.create_default_context(cafile=F.cafile) if F.cafile else ssl.create_default_context()
    opener = urllib.request.build_opener(urllib.request.HTTPSHandler(context=ctx), _NoRedirect())
    req = urllib.request.Request(url, headers={'User-Agent': 'OceanLens3D-extras/1'})
    try:
        with opener.open(req, timeout=300) as r:
            body = r.read(4096).decode('latin-1', 'replace')
            raise FetchError(f'LAS did not write a file ({r.status}): {" ".join(body.split())[:200]}')
    except urllib.error.HTTPError as e:
        loc = e.headers.get('Location', '')
        if e.code not in (301, 302, 303) or not loc:
            raise FetchError(f'LAS answered HTTP {e.code} for {url[:120]}') from e
    u = urllib.parse.urlsplit(loc)
    require(u.hostname == 'las.incois.gov.in' and u.path.startswith('/las/output/') and u.path.endswith('.nc'),
            f'LAS redirected to an unexpected place: {loc}')
    return urllib.parse.urlunsplit(('https', u.netloc, u.path, '', ''))


def las_get(F, xml, name, parse):
    """A LAS NetCDF extract through the cache: the cache records the product request (which says what was asked
    for), and the file is downloaded from where LAS wrote it."""
    url = f'{LAS}/ProductServer.do?xml=' + urllib.parse.quote(xml, safe='')
    if os.path.exists(F.path(name)) or not F.fetch:
        return F.get(url, name, parse)
    require(urllib.parse.urlsplit(url).hostname in F.allow_hosts, 'las.incois.gov.in is not in --allow-host')
    data = F.download(las_output_url(F, url), timeout=300)
    out = parse(data)
    F.store(name, data, url)
    with F._lock:
        F.used[name] = bs.sha256(data)
    return out


def parse_oisst(day):
    """A LAS OISST extract -> SST (64 x 124, NaN on land) after checking its axes and its day."""
    import ncio

    def parse(data):
        d = ncio.read_bytes(data)
        v = d['vars']
        lat, lon = np.asarray(v['LAT81_480']['data'], 'f8'), np.asarray(v['LON81_560']['data'], 'f8')
        g = OISST_GRID
        require(len(lat) == g['nlat'] and len(lon) == g['nlon'] and abs(lat[0] - g['lat0']) < 1e-4
                and abs(lon[0] - g['lon0']) < 1e-4 and np.allclose(np.diff(lat), g['step']) and np.allclose(np.diff(lon), g['step']),
                'OISST from LAS is on another grid')
        when = ncio.cf_times(v['TIME'])
        require(len(when) == 1 and when[0] == day, f'OISST from LAS is for {when}, not {day:%d %b %Y} 12Z')
        sst = np.asarray(v['SST']['data'], 'f8')[0, 0]
        require(str(v['SST']['attrs'].get('units')) == 'Celsius', 'OISST units are not Celsius')
        # LAS writes land as 0 and gives the variable no missing_value or _FillValue; 0 C is never sea water here
        sst = np.where((sst > -3) & (sst < 40) & (sst != 0), sst, np.nan)
        require(0.6 < np.isfinite(sst).mean() < 0.9, f'OISST: {np.isfinite(sst).mean():.0%} of the map is sea')
        return sst
    return parse


def fetch_oisst(F):
    out = []
    for day in bs.SNAPS:
        out.append(las_get(F, las_xml(OISST['dataset'], OISST['variable'], day),
                           f'extras/las/oisst_{day:%Y%m%d}.nc', parse_oisst(day)))
    return np.stack(out)


# ------------------------------------------------------------------ HYCOM surface currents, every 6 hours
def fetch_hycom_surface(F):
    das, lat, lon, dep, tim = bs.hycom_axes(F)
    require(dep[0] == 0, 'the first HYCOM level is not the surface')
    tidx = []
    for t in SURF_TIMES:
        k = bs.nearest_time(tim, t, tol_h=0.01)
        require(k is not None, f'HYCOM has no output at {t:%Y-%m-%d %H}Z')
        tidx.append(k)
    SI, SJ = 6, 3                                                    # as fetch_hycom: 0.24 degree sampling
    i0, i1 = int(np.searchsorted(lat, bs.LAT0 - 0.4)), int(np.searchsorted(lat, bs.LAT1 + 0.4))
    j0, j1 = int(np.searchsorted(lon, bs.LON0 - 0.4)), int(np.searchsorted(lon, bs.LON1 + 0.4))
    sub_lat, sub_lon = lat[i0:i1 + 1:SI], lon[j0:j1 + 1:SJ]
    i1, j1 = i0 + (len(sub_lat) - 1) * SI, j0 + (len(sub_lon) - 1) * SJ
    rows, cols = bs.cell_members(sub_lat, bs.LATS), bs.cell_members(sub_lon, bs.LONS)
    shape = [1, 1, len(sub_lat), len(sub_lon)]

    def job(j):
        n, hv = j
        url = f'{bs.HYCOM}.ascii?{hv}.{hv}[{tidx[n]}][0:1:0][{i0}:{SI}:{i1}][{j0}:{SJ}:{j1}]'

        def parse(data):
            arr = bs.parse_ascii(data)
            if arr.get(hv + '_shape') != shape:
                raise ValueError(f'{hv}: shape {arr.get(hv + "_shape")}, expected {shape}')
            return arr[hv].reshape(shape)[0, 0]
        return j, F.get(url, f'extras/hycom_surf/{hv}_{SURF_TIMES[n]:%Y%m%d%H}.txt', parse)

    out = np.zeros((len(SURF_TIMES), 2, len(bs.LATS), len(bs.LONS)))
    jobs = [(n, hv) for n in range(len(SURF_TIMES)) for hv in ('water_u', 'water_v')]
    with ThreadPoolExecutor(6) as ex:
        for (n, hv), a in ex.map(job, jobs):
            sc, off, fill = bs.das_packing(das, hv)
            a = np.where(a == fill, np.nan, a * sc + off)
            out[n, 0 if hv == 'water_u' else 1] = bs.fill_nans(bs.cell_mean(a, rows, cols))
    times = [bs.hours_to_time(tim[k]) for k in tidx]
    require(times == SURF_TIMES, 'HYCOM surface times are not every 6 hours')
    return out, {'url': bs.HYCOM, 'variables': ['water_u', 'water_v'], 'level_m': 0.0, 'time_index': [tidx[0], tidx[-1]],
                 'every_hours': 6, 'sample_step_deg': [float(round(sub_lat[1] - sub_lat[0], 3)),
                                                       float(round(sub_lon[1] - sub_lon[0], 3))]}


# ------------------------------------------------------------------ GLORYS surface currents, daily means
def fetch_glorys_surface(F):
    import nc4, ncio
    found = {}
    for m in sorted({t.strftime('%Y/%m') for t in DAYS}):
        prefix = urllib.parse.quote(f'{bs.GLORYS_PREFIX}/{m}/', safe='/')
        found.update(F.get(f'{bs.GLORYS_ROOT}?list-type=2&prefix={prefix}', f'glorys/listing-{m.replace("/", "-")}.xml',
                           bs.parse_s3_listing))
    opened = []
    for t in DAYS:
        pat = f'{bs.GLORYS_PREFIX}/{t:%Y/%m}/mercatorglorys12v1_gl12_mean_{t:%Y%m%d}_R'
        keys = sorted(k for k in found if k.startswith(pat) and k.endswith('.nc'))
        require(len(keys) == 1, f'GLORYS: {len(keys)} native files for {t:%d %b %Y}')
        src = bs.RangeSource(F, f'{bs.GLORYS_ROOT}/{keys[0]}', 'glorys/' + keys[0].rsplit('/', 1)[1], found[keys[0]])
        f = nc4.open_source(src, max_bytes=1 << 28)
        tv = f.vars['time']
        require(ncio.cf_times({'attrs': tv.attrs, 'data': tv.read()}) == [t], f'GLORYS: {keys[0]} is not the mean of {t:%d %b}')
        opened.append((keys[0], f))
    f0 = opened[0][1]
    lat, lon, dep = (np.asarray(f0.vars[n].read(), 'f8') for n in ('latitude', 'longitude', 'depth'))
    i0, i1 = int(np.searchsorted(lat, bs.LAT0 - 0.4)), int(np.searchsorted(lat, bs.LAT1 + 0.4))
    j0, j1 = int(np.searchsorted(lon, bs.LON0 - 0.4)), int(np.searchsorted(lon, bs.LON1 + 0.4))
    half = bs.STEP / 2 + 1e-6
    rows, cols = bs.cell_members(lat[i0:i1], bs.LATS, half), bs.cell_members(lon[j0:j1], bs.LONS, half)

    def one(job):
        d, vi, nm = job
        a = opened[d][1].vars[nm].read((0, slice(0, 1), slice(i0, i1), slice(j0, j1)))
        return d, vi, bs.fill_nans(bs.cell_mean(np.asarray(a, 'f8')[0], rows, cols))
    out = np.zeros((len(DAYS), 2, len(bs.LATS), len(bs.LONS)))
    with ThreadPoolExecutor(6) as ex:
        for d, vi, a in ex.map(one, [(d, vi, nm) for d in range(len(DAYS)) for vi, nm in enumerate(('uo', 'vo'))]):
            out[d, vi] = a
    return out, {'url': bs.GLORYS_ROOT, 'variables': ['uo', 'vo'], 'level_m': float(round(dep[0], 4)),
                 'files': [k.rsplit('/', 1)[1] for k, _ in opened],
                 'read': 'NetCDF-4 (HDF5) chunks in HTTPS byte ranges by data-pipeline/nc4.py'}


# ------------------------------------------------------------------ NOAA Global Drifter Program
def gdp_query():
    cons = [f'latitude>={bs.LAT0:g}', f'latitude<={bs.LAT1:g}', f'longitude>={bs.LON0:g}', f'longitude<={bs.LON1:g}',
            f'time>={bs.T0:%Y-%m-%dT%H:%M:%SZ}', f'time<={END:%Y-%m-%dT%H:%M:%SZ}']
    return GDP_COLS + ''.join('&' + c for c in cons)


def gdp_url():
    first, *rest = gdp_query().split('&')
    return GDP + '?' + urllib.parse.quote(first, safe=',') + ''.join('&' + urllib.parse.quote(c, safe='') for c in rest)


def parse_gdp(data):
    """GDP hourly CSV -> {id: {'h': hours since T0, 'lat', 'lon', 'drogue_lost': iso or None}}."""
    rows = list(csv.reader(io.StringIO(data.decode('utf-8'))))
    require(rows and rows[0] == GDP_COLS.split(','), f'unexpected GDP columns {rows[:1]}')
    out = {}
    for r in rows[2:]:                                               # row 1 holds the units
        if len(r) != 5:
            continue
        t = datetime.strptime(r[1], '%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=timezone.utc)
        h = (t - bs.T0).total_seconds() / 3600
        la, lo = float(r[2]), float(r[3])
        require(h == int(h) and 0 <= h <= (END - bs.T0).total_seconds() / 3600, f'GDP time {r[1]} off the hourly grid')
        require(bs.LAT0 <= la <= bs.LAT1 and bs.LON0 <= lo <= bs.LON1, f'GDP position {la}, {lo} outside the map')
        d = out.setdefault(r[0], {'h': [], 'lat': [], 'lon': [], 'drogue_lost': r[4] or None})
        d['h'].append(int(h)); d['lat'].append(la); d['lon'].append(lo)
    require(out, 'no drifters in the map')
    return out


def fetch_drifters(F):
    raw = F.get(gdp_url(), 'extras/gdp_hourly.csv', parse_gdp, timeout=300)
    ids, tracks, counts = [], [], {'drifters': 0, 'dropped_short': 0}
    for did in sorted(raw):
        d = raw[did]
        order = np.argsort(d['h'])
        h = np.asarray(d['h'])[order]
        require((np.diff(h) > 0).all(), f'drifter {did} repeats a time')
        if len(h) < MIN_DRIFTER_HOURS:
            counts['dropped_short'] += 1
            continue
        lost = d['drogue_lost']
        lost_h = None
        if lost:
            lt = datetime.strptime(lost, '%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=timezone.utc)
            lost_h = round((lt - bs.T0).total_seconds() / 3600, 2)
        # drogued at hour h when h < drogue_lost_h; no date means the drogue was still on
        ids.append({'id': did, 'drogue_lost': lost, 'drogue_lost_h': lost_h})
        la, lo = np.asarray(d['lat'])[order], np.asarray(d['lon'])[order]
        tracks.append([[int(h[k]), round(float(la[k]), 4), round(float(lo[k]), 4)] for k in range(len(h))])
        counts['drifters'] += 1
    return {'t0': bs.T0.strftime('%Y-%m-%dT%H:%MZ'), 'step_h': 1, 'fields': ['hour', 'lat', 'lon'],
            'drifters': ids, 'tracks': tracks}, counts


# ------------------------------------------------------------------ assemble
def pack16(a, off, sc):
    q = np.round((np.asarray(a, 'f8') - off) / sc)
    q = np.where(np.isfinite(q), q, bs.MISSING)
    require(np.abs(q[q != bs.MISSING]).max(initial=0) < 32767, 'value outside the int16 packing')
    return base64.b64encode(q.astype('<i2').tobytes()).decode('ascii')


def build(cache, fetch=False, cafile=None, allow_hosts=None, log=print):
    F = bs.Fetcher(cache, fetch, cafile, allow_hosts=allow_hosts or HOSTS)
    hy, hinfo = fetch_hycom_surface(F)
    log(f'HYCOM surface currents: {len(SURF_TIMES)} times every 6 h, speed p99 {np.percentile(np.hypot(hy[:, 0], hy[:, 1]), 99):.2f} m/s')
    gl, ginfo = fetch_glorys_surface(F)
    log(f'GLORYS surface currents: {len(DAYS)} daily means, speed p99 {np.percentile(np.hypot(gl[:, 0], gl[:, 1]), 99):.2f} m/s')
    for a, nm in ((hy, 'HYCOM'), (gl, 'GLORYS')):
        require(np.isfinite(a).all() and np.abs(a).max() < 3, f'{nm} surface currents have gaps or exceed 3 m/s')
    drift, dcounts = fetch_drifters(F)
    log(f'GDP drifters: {dcounts["drifters"]} in the map, {sum(len(t) for t in drift["tracks"])} hourly positions')
    sst = fetch_oisst(F)
    log(f'OISST from INCOIS LAS: {len(sst)} days, {np.nanmin(sst):.2f}..{np.nanmax(sst):.2f} C')
    uq = bs.QUANT['u']
    payload = {
        'schema': 1,
        'meta': {
            'title': 'Cyclone Amphan, May 2020: currents for the drift tool, drifters and satellite SST',
            'grid': {'lat0': bs.LAT0, 'lon0': bs.LON0, 'step': bs.STEP, 'nlat': len(bs.LATS), 'nlon': len(bs.LONS)},
            'missing': bs.MISSING,
            'sources': {
                'surf_hycom': 'HYCOM GOFS 3.1 analysis (GLBy0.08/expt_93.0) surface currents every 6 hours, sampled and '
                              'averaged onto the 0.5 deg grid like the main snapshot',
                'surf_glorys': 'Copernicus Marine GLORYS12V1 daily-mean surface currents (0.49 m), native NetCDF-4 read '
                               'in byte ranges, averaged onto the 0.5 deg grid',
                'drifters': 'NOAA Global Drifter Program hourly positions (drifter_hourly_qc, AOML ERDDAP) inside the map',
                'sst': 'NOAA OISST v2.1 (AVHRR only) daily SST at 0.25 deg, extracted as NetCDF from the INCOIS Live '
                       'Access Server'},
            'citations': CITATIONS,
            'provenance': {
                'pipeline': {'script': 'data-pipeline/build_extras.py', 'version': '1'},
                'surf_hycom': hinfo, 'surf_glorys': ginfo,
                'drifters': {'url': GDP, 'query': gdp_query(), 'counts': dcounts,
                             'drogue': 'drifters carry a drogue centred at 15 m; drogue_lost_date says when it came off'},
                'sst': {'server': LAS, 'dataset': OISST, 'operation': 'Data_Extract_netCDF',
                        'request': las_xml(OISST['dataset'], OISST['variable'], bs.SNAPS[0]).replace(
                            bs.SNAPS[0].strftime('%d-%b-%Y %H:%M'), '{date} 12:00')},
            },
            'inputs': extras_inputs(F),
        },
        'surf': {
            'hycom': {'times': [t.strftime('%Y-%m-%dT%H:%MZ') for t in SURF_TIMES], 'vars': ['u', 'v'], 'quant': list(uq),
                      'data': pack16(hy, *uq)},
            'glorys': {'times': [t.strftime('%Y-%m-%dT%H:%MZ') for t in DAYS], 'vars': ['u', 'v'], 'quant': list(uq),
                       'data': pack16(gl, *uq)},
        },
        'drifters': drift,
        'sat': {'sst': {'grid': OISST_GRID, 'times': [t.strftime('%Y-%m-%dT%H:%MZ') for t in bs.SNAPS],
                        'quant': list(SST_QUANT), 'data': pack16(sst, *SST_QUANT)}},
    }
    return payload, F


def extras_inputs(F):
    """Hash and access date of every raw file, the HYCOM and GLORYS ranges in groups."""
    man = F.manifest()
    groups = {'hycom_surf': [n for n in F.used if n.startswith('extras/hycom_surf/')],
              'glorys': [n for n in F.used if n.startswith('glorys/')],
              'hycom_axes': [n for n in F.used if n.startswith('hycom')]}
    out = {}
    for n in sorted(n for n in F.used if not any(n in g for g in groups.values())):
        out[n] = {'sha256': F.used[n], 'accessed': man.get(n, {}).get('fetched', bs.DEFAULT_ACCESSED)[:10]}
    for g, names in groups.items():
        if names:
            dates = sorted(man.get(n, {}).get('fetched', bs.DEFAULT_ACCESSED)[:10] for n in names)
            out[g] = {'files': len(names), 'sha256': bs.group_hash(F, names),
                      'accessed': dates[0] if dates[0] == dates[-1] else f'{dates[0]}..{dates[-1]}'}
    return out


def main(argv=None):
    here = os.path.dirname(os.path.abspath(__file__))
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--cache', default=os.environ.get('OCEANLENS_CACHE') or os.path.join(here, 'cache'))
    ap.add_argument('--out', default=os.path.join(here, '..', 'data', 'amphan-2020-extras.json'))
    ap.add_argument('--fetch', action='store_true', help='download files missing from the cache')
    ap.add_argument('--ca-bundle', default=os.environ.get('OCEANLENS_CA_BUNDLE'))
    ap.add_argument('--allow-host', action='append', metavar='HOST')
    ap.add_argument('--check', action='store_true', help='rebuild and compare with --out instead of writing it')
    ap.add_argument('--cache-manifest', metavar='FILE', help='also list every raw file read (name, sha256, bytes)')
    args = ap.parse_args(argv)
    try:
        payload, F = build(args.cache, args.fetch, args.ca_bundle, args.allow_host)
    except CacheMiss as e:
        print(f'error: {e} is not in the cache ({args.cache}); rerun with --fetch to download it', file=sys.stderr)
        return 2
    except (InvariantError, FetchError, ValueError, OSError, KeyError, IndexError) as e:
        print(f'error: {type(e).__name__}: {e}', file=sys.stderr)
        return 1
    data = bs.serialise(payload)
    out = os.path.normpath(args.out)
    if args.cache_manifest:
        lines = [f'{n} {F.used[n]} {os.path.getsize(F.path(n))}' for n in sorted(F.used)]
        bs.write_atomic(args.cache_manifest, ('\n'.join(lines) + '\n').encode('ascii'))
    if args.check:
        old = open(out, 'rb').read() if os.path.exists(out) else b''
        if old == data:
            print(f'check ok: {out} matches a rebuild from the cache')
            return 0
        print(f'check failed: {out} differs from a rebuild', file=sys.stderr)
        return 1
    bs.write_atomic(out, data)
    print(f'wrote {len(data) / 1e6:.2f} MB to {out}; downloaded {F.downloaded / 1e6:.1f} MB this run')
    return 0


if __name__ == '__main__':
    sys.exit(main())
