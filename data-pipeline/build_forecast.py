#!/usr/bin/env python3
"""Build data/forecast-latest.json: the latest Copernicus Marine ocean forecast over the map, as a case for the app.

Copernicus Marine's global analysis and forecast (GLOBAL_ANALYSISFORECAST_PHY_001_024, 1/12 deg, daily means) is run
every day: the day before the run and the next 9 days. This script finds the latest run on the public object store,
reads each day's temperature, salinity and currents over the map in HTTPS byte ranges (nc4.py, as the GLORYS route
does), puts them on the app grid (model_from_dataset) and writes a schema-1 snapshot. The sea floor comes from the main
snapshot (the same grid). There is no storm track and no float in it: the app shows it as its own case, served by
server/serve.py (/?dataset=forecast-latest).

    python data-pipeline/build_forecast.py --fetch            # the latest run, today
    python data-pipeline/build_forecast.py --run 20261104     # a given run, from the cache

Run it once a day (docs/DEPLOY.md) to keep a server's forecast current. Without --fetch nothing is downloaded.
"""
import argparse, json, os, re, sys, urllib.parse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import build_snapshot as B                      # noqa: E402

ROOT = 'https://s3.waw3-1.cloudferro.com/mdl-native-14'
PRODUCT = 'native/GLOBAL_ANALYSISFORECAST_PHY_001_024'
DATASETS = {'thetao': 'cmems_mod_glo_phy-thetao_anfc_0.083deg_P1D-m_202406',
            'so': 'cmems_mod_glo_phy-so_anfc_0.083deg_P1D-m_202406',
            'uovo': 'cmems_mod_glo_phy-cur_anfc_0.083deg_P1D-m_202406'}
VARS = {'temp': ('thetao', 'thetao'), 'sal': ('so', 'so'), 'u': ('uovo', 'uo'), 'v': ('uovo', 'vo')}
NAME = re.compile(r'glo12_rg_1d-m_(\d{8})-\1_3D-(thetao|so|uovo)_fcst_R(\d{8})\.nc$')
MAX_DAYS = 11
CITATION = {'id': 'glo12', 'doi': '10.48670/moi-00016',
            'text': 'E.U. Copernicus Marine Service Information: Global Ocean Physics Analysis and Forecast '
                    '(GLOBAL_ANALYSISFORECAST_PHY_001_024), daily means, doi:10.48670/moi-00016. Lellouche, J.-M. et '
                    'al., Mercator Ocean International.'}


def listing(F, key, month):
    p = urllib.parse.quote(f'{PRODUCT}/{DATASETS[key]}/{month:%Y/%m}/', safe='/')
    return F.get(f'{ROOT}?list-type=2&prefix={p}', f'forecast/listing-{key}-{month:%Y-%m}.xml', B.parse_s3_listing)


def run_files(F, run=None, today=None):
    """{key: [(day, s3 key, size)]} of one run (the latest found when run is None) and the run date; every day must
    have its three files."""
    today = today or datetime.now(timezone.utc)
    months = sorted({datetime(d.year, d.month, 1) for d in (today - timedelta(days=12), today, today + timedelta(days=12))})
    found = {k: {} for k in DATASETS}
    for k in DATASETS:
        for m in months:
            try:
                L = listing(F, k, m)
            except (B.FetchError, B.InvariantError):
                continue                                  # a month with no files yet
            for key, size in L.items():
                g = NAME.search(key)
                if g and g.group(2) == k:
                    found[k][(g.group(3), g.group(1))] = (key, size)
    runs = sorted({r for r, _ in found['thetao']})
    B.require(runs, 'no forecast files found')
    run = run or runs[-1]
    days = sorted(d for r, d in found['thetao'] if r == run)
    days = [d for d in days if all((run, d) in found[k] for k in DATASETS)][:MAX_DAYS]
    B.require(len(days) >= 2, f'run {run}: fewer than 2 days with temperature, salinity and currents')
    return run, days, {k: [(d,) + found[k][(run, d)] for d in days] for k in DATASETS}


def read_days(F, files, log=print):
    """The map box of every day and variable, on the levels the app depths use, as an ncio-style dict."""
    import nc4

    def open_one(k, day, key, size):
        src = B.RangeSource(F, f'{ROOT}/{key}', 'forecast/' + key.rsplit('/', 1)[1], size)
        return nc4.open_source(src, max_bytes=1 << 28)
    f0 = open_one('thetao', *files['thetao'][0])
    lat, lon, dep = (np.asarray(f0.vars[n].read(), 'f8') for n in ('latitude', 'longitude', 'depth'))
    i0, i1 = int(np.searchsorted(lat, B.LAT0 - 0.4)), int(np.searchsorted(lat, B.LAT1 + 0.4))
    j0, j1 = int(np.searchsorted(lon, B.LON0 - 0.4)), int(np.searchsorted(lon, B.LON1 + 0.4))
    levels = [[(0, 1.0)] if z <= dep[0] else B.level_weights(dep, [z])[0] for z in B.DEPTHS]
    need = sorted({k for lv in levels for k, _ in lv})
    top = need[-1] + 1

    def one(job):
        key, n = job
        stem, var = VARS[key]
        day, s3key, size = files[stem][n]
        f = open_one(stem, day, s3key, size)
        for name, a in (('latitude', lat), ('longitude', lon), ('depth', dep)):
            B.require(np.array_equal(np.asarray(f.vars[name].read(), 'f8'), a), f'{s3key}: another {name} axis')
        t = float(np.asarray(f.vars['time'].read()).ravel()[0])
        a = f.vars[var].read((0, slice(0, top), slice(i0, i1), slice(j0, j1)))
        return key, n, t, dict(f.vars[var].attrs), dict(f.vars['time'].attrs), np.asarray(a, 'f8')[need].astype('f4')
    jobs = [(key, n) for key in VARS for n in range(len(files['thetao']))]
    with ThreadPoolExecutor(6) as ex:
        got = {(key, n): r for key, n, *r in ex.map(one, jobs)}
    nd = len(files['thetao'])
    times = [got[('temp', n)][0] for n in range(nd)]
    for key in VARS:
        B.require([got[(key, n)][0] for n in range(nd)] == times, f'{key}: the files disagree on the days')
    ds = {'dims': {'time': nd, 'depth': len(need), 'latitude': i1 - i0, 'longitude': j1 - j0}, 'unlimited': 'time',
          'attrs': {}, 'vars': {
              'time': {'dims': ('time',), 'attrs': got[('temp', 0)][2], 'data': np.array(times)},
              'depth': {'dims': ('depth',), 'attrs': {'standard_name': 'depth', 'units': 'm', 'positive': 'down'},
                        'data': dep[need]},
              'latitude': {'dims': ('latitude',), 'attrs': {'standard_name': 'latitude', 'units': 'degrees_north'},
                           'data': lat[i0:i1]},
              'longitude': {'dims': ('longitude',), 'attrs': {'standard_name': 'longitude', 'units': 'degrees_east'},
                            'data': lon[j0:j1]}}}
    for key, (stem, var) in VARS.items():
        ds['vars'][var] = {'dims': ('time', 'depth', 'latitude', 'longitude'), 'attrs': got[(key, 0)][1],
                           'data': np.stack([got[(key, n)][3] for n in range(nd)])}
    log(f'  {nd} days x 4 variables read, levels {dep[need][0]:.1f}-{dep[need][-1]:.0f} m')
    return ds, {'box': {'lat': [float(lat[i0]), float(lat[i1 - 1])], 'lon': [float(lon[j0]), float(lon[j1 - 1])]},
                'levels_read_m': [float(dep[k]) for k in need]}


def build(F, run=None, base=None, log=print, today=None):
    run, days, files = run_files(F, run, today)
    log(f'forecast run {run}: {days[0]} to {days[-1]}')
    ds, info = read_days(F, files, log)
    model, wet, when, minfo = B.model_from_dataset(ds, f'GLO12 run {run}', log)
    B.require(np.isfinite(model).all(), 'the forecast has gaps after filling')
    base = base or os.path.join(HERE, '..', 'data', 'amphan-2020.json')
    with open(base, encoding='utf-8') as f:
        main = json.load(f)                           # the sea floor: ETOPO1 on the same grid
    M = main['meta']
    B.require([M['lat0'], M['lon0'], M['step'], M['nlat'], M['nlon']] == [B.LAT0, B.LON0, B.STEP, len(B.LATS), len(B.LONS)],
              'the main snapshot is on another grid')
    issued = datetime.strptime(run, '%Y%m%d').strftime('%d %b %Y').lstrip('0')
    man, used = F.manifest(), sorted(F.used)
    return {
        'schema': B.SCHEMA,
        'meta': {
            'title': f'Ocean forecast issued {issued}',
            'times': [t.strftime('%Y-%m-%dT%H:%MZ') for t in when],
            'lat0': B.LAT0, 'lon0': B.LON0, 'step': B.STEP, 'nlat': len(B.LATS), 'nlon': len(B.LONS),
            'depths': B.DEPTHS, 'vars': ['temp', 'sal', 'u', 'v'], 'quant': B.QUANT, 'missing': B.MISSING,
            'case': {'kind': 'forecast', 'name': 'Ocean forecast', 'run': run, 'issued': f'{run[:4]}-{run[4:6]}-{run[6:]}',
                     'first': days[0], 'last': days[-1]},
            'sources': {'model': 'Copernicus Marine global analysis and forecast (GLOBAL_ANALYSISFORECAST_PHY_001_024), '
                                 f'run {run}, daily means; each 0.5 deg cell the mean of the native points inside it, '
                                 'levels interpolated linearly to the app depths.',
                        'bathymetry': M['sources']['bathymetry']},
            'citations': [CITATION] + [c for c in M['citations'] if c.get('id') == 'etopo1'],
            'provenance': {'pipeline': {'script': 'data-pipeline/build_forecast.py', 'version': B.PIPELINE_VERSION},
                           'model': {'url': ROOT, 'run': run, 'days': days, 'files': {k: [f[1] for f in v] for k, v in files.items()},
                                     'read': 'NetCDF-4 (HDF5) chunks in HTTPS byte ranges by data-pipeline/nc4.py',
                                     **info, 'native_step_deg': minfo['native_step_deg']},
                           'bathymetry': M['provenance']['bathymetry']},
            'inputs': {'forecast': {'files': len(used), 'sha256_of_hashes': B.sha256(''.join(F.used[n] for n in used).encode()),
                                    'accessed': max((man.get(n, {}).get('fetched', '')[:10] for n in used), default='')}},
            'model': {'name': 'Copernicus Marine global analysis and forecast', 'short': 'Forecast',
                      'kind': 'analysis and forecast, daily means', 'product': 'GLOBAL_ANALYSISFORECAST_PHY_001_024',
                      'doi': CITATION['doi'], 'temperature': 'potential', 'run': run},
        },
        'model': __import__('base64').b64encode(b''.join(
            __import__('base64').b64decode(B.pack(model[t, vi], k)) for t in range(len(when))
            for vi, k in enumerate(['temp', 'sal', 'u', 'v']))).decode('ascii'),
        'mask': {'wet': __import__('base64').b64encode(np.packbits(wet.astype(np.uint8).ravel()).tobytes()).decode('ascii'),
                 'layout': '[level][lat][lon]', 'bitorder': 'big'},
        'alt': main['alt'], 'altGrid': main['altGrid'],
        'argo': {'levels': main['argo']['levels'], 'profiles': [], 'T': '', 'S': ''},
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--cache', default=os.environ.get('OCEANLENS_CACHE') or os.path.join(HERE, 'cache'))
    ap.add_argument('--out', default=os.path.join(HERE, '..', 'data', 'forecast-latest.json'))
    ap.add_argument('--run', help='the run date (YYYYMMDD); default the latest on the object store')
    ap.add_argument('--fetch', action='store_true', help='download what the cache lacks')
    ap.add_argument('--ca-bundle', default=os.environ.get('OCEANLENS_CA_BUNDLE'))
    ap.add_argument('--check', action='store_true', help='rebuild and compare with --out instead of writing it')
    a = ap.parse_args(argv)
    F = B.Fetcher(a.cache, fetch=a.fetch, cafile=a.ca_bundle)
    try:
        text = B.serialise(build(F, a.run))
    except B.CacheMiss as e:
        print(f'missing from the cache: {e} (run with --fetch)', file=sys.stderr)
        return 2
    if a.check:
        with open(a.out, 'rb') as f:
            same = f.read() == text
        print(f"check {'ok' if same else 'failed'}: {a.out}")
        return 0 if same else 1
    B.write_atomic(a.out, text)
    print(f'wrote {a.out} ({len(text) / 1e6:.1f} MB, {F.downloaded / 1e6:.0f} MB downloaded)')
    return 0


if __name__ == '__main__':
    sys.exit(main())
