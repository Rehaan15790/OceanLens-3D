#!/usr/bin/env python3
"""Build data/amphan-2020-igora.json: INCOIS's IGORA ocean reanalysis for the Amphan case, from the INCOIS LAS.

The INCOIS Live Access Server (las.incois.gov.in) serves IGORA, the INCOIS Global Ocean ReAnalysis: monthly means of
temperature, salinity and currents, 1979-2023, on a 0.5 deg global grid with 40 levels. This script asks the LAS
product server for a NetCDF extract of each variable over the map for May 2020 (the LAS 'Data_Extract_netCDF'
operation, as its Save As button does), puts the currents from the U cells onto the T cells, grids everything with the
pipeline's model_from_dataset, and writes a snapshot on the main snapshot's grid and times: the May 2020 monthly mean
stands for every analysis day. Argo, relief and track come from the GLORYS snapshot (the same case), so the facts and
the floats work as for the other models. The app shows it as a third model (build.py embeds one time of it).

    python data-pipeline/build_las.py [--fetch] [--cache DIR] [--out FILE] [--check]

The LAS leaves an intermediate certificate out of its HTTPS chain; data-pipeline/certs holds that GlobalSign
intermediate (checked by its SHA-256 fingerprint), so verification stays on. Same cache and manifest as the snapshot.
"""
import argparse, base64, hashlib, json, os, ssl, sys, urllib.parse, urllib.request

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import build_snapshot as B                      # noqa: E402

LAS = 'https://las.incois.gov.in/las/ProductServer.do'
CERT = os.path.join(HERE, 'certs', 'globalsign-rsa-ov-ssl-ca-2018.pem')
CERT_SHA256 = 'b676ffa3179e8812093a1b5eafee876ae7a6aaf231078dad1bfb21cd2893764a'
MONTH = '15-May-2020 12:00'                    # IGORA's May 2020 monthly mean
# snapshot key -> (LAS dataset id, LAS variable id, NetCDF variable, CF standard name for model_from_dataset)
IGORA = {'temp': ('id-aed8c62062', 'TEMPERATURE-id-aed8c62062', 'TEMPERATURE', 'sea_water_potential_temperature'),
         'sal': ('id-ca44f96e8c', 'SALINITY-id-ca44f96e8c', 'SALINITY', 'sea_water_salinity'),
         'u': ('id-eb2ca46dd1', 'U_VEL-id-eb2ca46dd1', 'U_VEL', 'eastward_sea_water_velocity'),
         'v': ('id-2247fc81b6', 'V_VEL-id-2247fc81b6', 'V_VEL', 'northward_sea_water_velocity')}
BOX = {'x': (B.LON0 - 0.75, B.LON1 + 0.75), 'y': (B.LAT0 - 0.75, B.LAT1 + 0.75), 'z': (0, 1250)}
CITATION = {'id': 'igora',
            'text': 'IGORA, the INCOIS Global Ocean ReAnalysis (monthly means, 1979-2023), Indian National Centre for Ocean '
                    'Information Services (INCOIS), Ministry of Earth Sciences, Government of India; served by the INCOIS '
                    'Live Access Server, las.incois.gov.in.'}


def intermediate():
    pem = open(CERT, encoding='ascii').read()
    der = ssl.PEM_cert_to_DER_cert(pem)
    B.require(hashlib.sha256(der).hexdigest() == CERT_SHA256, f'{CERT} is not the GlobalSign intermediate it should be')
    return pem


def request_xml(key):
    ds, var, _, _ = IGORA[key]
    r = lambda t, lo, hi: f'<range type="{t}" low="{lo}" high="{hi}"></range>'
    return (f'<?xml version="1.0"?><lasRequest href="file:las.xml"><link match="/lasdata/operations/operation'
            f'[@ID=\'Data_Extract_netCDF\']"></link><properties><ferret><format>netcdf</format><view>xyz</view></ferret>'
            f'</properties><args><link match="/lasdata/datasets/{ds}/variables/{var}"></link><region>'
            f'{r("x", *BOX["x"])}{r("y", *BOX["y"])}{r("z", *BOX["z"])}{r("t", MONTH, MONTH)}</region></args></lasRequest>')


def product_url(key):
    return LAS + '?xml=' + urllib.parse.quote(request_xml(key))


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **kw):
        return None


def parse_nc(data):
    import ncio
    B.require(data[:3] == b'CDF' or data[:4] == b'\x89HDF', 'the LAS did not return NetCDF')
    return ncio.read_bytes(data)


def las_get(F, key):
    """The LAS extract of one variable: from the cache, or (with --fetch) a product request, whose 302 names the output
    file, then that file over https. Cached under a stable name with the request recorded as its URL."""
    name = f'las/igora-{key}-2020-05.nc'
    if os.path.exists(F.path(name)) or not F.fetch:
        return F.get(product_url(key), name, parse_nc)
    ctx = ssl.create_default_context()
    ctx.load_verify_locations(cadata=F.extra_cadata)
    op = urllib.request.build_opener(urllib.request.HTTPSHandler(context=ctx), _NoRedirect)
    try:
        op.open(urllib.request.Request(product_url(key), headers={'User-Agent': 'OceanLens3D-snapshot/2'}), timeout=300)
        raise B.FetchError(f'the LAS made no {key} product (no redirect to an output file)')
    except urllib.error.HTTPError as e:
        loc = e.headers.get('Location', '') if e.code in (301, 302, 303) else ''
        if not loc:
            raise B.FetchError(f'the LAS answered {e.code} for {key}') from e
    u = urllib.parse.urlsplit(loc)
    B.require(u.hostname == 'las.incois.gov.in' and u.path.startswith('/las/output/') and u.path.endswith('.nc'),
              f'unexpected LAS output location {loc[:120]}')
    data = F.download('https://las.incois.gov.in' + u.path)       # the same file over https
    out = parse_nc(data)
    F.store(name, data, product_url(key))
    with F._lock:
        F.used[name] = B.sha256(data)
    return out


def to_t_points(a, xu, yu, xt, yt):
    """A field on the U cells (corners of the T cells on IGORA's B grid) -> the T cells, linear in x then y."""
    out = np.empty(a.shape[:-2] + (len(yt), len(xt)))
    for idx in np.ndindex(a.shape[:-2]):
        rows = np.array([np.interp(xt, xu, r, left=np.nan, right=np.nan) for r in a[idx]])
        out[idx] = np.array([np.interp(yt, yu, c, left=np.nan, right=np.nan) for c in rows.T]).T
    return out


def build(F, log=print):
    got = {k: las_get(F, k) for k in IGORA}
    T = got['temp']
    tv = T['vars']
    xt = (np.asarray(tv['XT_OCEAN']['data'], 'f8') + 180) % 360 - 180
    yt = np.asarray(tv['YT_OCEAN']['data'], 'f8')
    z = np.asarray(tv['ST_OCEAN']['data'], 'f8')
    time = tv['TAX']
    V = {'time': {'dims': ('time',), 'attrs': dict(time['attrs']), 'data': np.asarray(time['data'], 'f8')},
         'depth': {'dims': ('depth',), 'attrs': {'standard_name': 'depth', 'units': 'm', 'positive': 'down'}, 'data': z},
         'lat': {'dims': ('lat',), 'attrs': {'standard_name': 'latitude', 'units': 'degrees_north'}, 'data': yt},
         'lon': {'dims': ('lon',), 'attrs': {'standard_name': 'longitude', 'units': 'degrees_east'}, 'data': xt}}
    for key, (_, _, nm, sn) in IGORA.items():
        d = got[key]['vars']
        a = np.asarray(d[nm]['data'], 'f8')
        if key in ('u', 'v'):                         # the U cells' own axes, onto the T cells
            xu = (np.asarray(d['XU_OCEAN']['data'], 'f8') + 180) % 360 - 180
            yu = np.asarray(d['YU_OCEAN']['data'], 'f8')
            B.require(np.array_equal(np.asarray(d['ST_OCEAN']['data'], 'f8'), z), f'{key}: other levels')
            a = to_t_points(a, xu, yu, xt, yt)
        else:
            B.require(a.shape[-2:] == (len(yt), len(xt)), f'{key}: another grid')
        units = {'temp': 'degC', 'sal': '1e-3', 'u': 'm s-1', 'v': 'm s-1'}[key]
        V[{'temp': 'thetao', 'sal': 'so', 'u': 'uo', 'v': 'vo'}[key]] = {
            'dims': ('time', 'depth', 'lat', 'lon'), 'attrs': {'standard_name': sn, 'units': units}, 'data': a}
    ds = {'dims': {'time': 1, 'depth': len(z), 'lat': len(yt), 'lon': len(xt)}, 'unlimited': 'time', 'attrs': {}, 'vars': V}
    model, wet, when, info = B.model_from_dataset(ds, 'IGORA May 2020', log)
    B.require(np.isfinite(model).all(), 'IGORA has gaps after filling')
    sal, speed = model[0, 1, 0], np.hypot(model[0, 2, 0], model[0, 3, 0])
    B.require(28 < np.median(sal) < 38 and np.median(speed) < 1.5, 'IGORA values out of the expected ranges (units?)')
    # the same case as GLORYS: its floats, relief and track, and the main grid and times
    with open(os.path.join(HERE, '..', 'data', 'amphan-2020-glorys.json'), encoding='utf-8') as f:
        base = json.load(f)
    m = base['meta']
    nt = len(m['times'])
    man = F.manifest()
    names = sorted(n for n in F.used if n.startswith('las/'))
    meta = {k: m[k] for k in ('title', 'times', 'lat0', 'lon0', 'step', 'nlat', 'nlon', 'depths', 'vars', 'quant', 'missing')}
    meta['sources'] = dict(m['sources'], model='INCOIS IGORA ocean reanalysis, May 2020 monthly mean, from the INCOIS Live '
                       'Access Server (Data_Extract_netCDF); each 0.5 deg cell the mean of the native points inside it, '
                       'levels interpolated linearly to the app depths, currents moved from the U cells to the T cells. '
                       'The monthly mean stands for every analysis day.')
    meta['citations'] = [CITATION] + [c for c in m['citations'] if c.get('id') != 'glorys']
    meta['provenance'] = dict(m['provenance'], pipeline={'script': 'data-pipeline/build_las.py', 'version': B.PIPELINE_VERSION},
                              model={'url': LAS, 'requests': {k: request_xml(k) for k in IGORA},
                                     'native_levels_m': info['native_levels_m'], 'native_step_deg': info['native_step_deg'],
                                     'month': '2020-05', 'note': 'one monthly mean, the same at every analysis time'})
    meta['inputs'] = {n: {'sha256': F.used[n], 'accessed': man.get(n, {}).get('fetched', '')[:10]} for n in names}
    meta['model'] = {'name': 'INCOIS Global Ocean ReAnalysis (IGORA)', 'short': 'IGORA',
                     'kind': 'reanalysis, monthly mean', 'product': 'IGORA monthly means 1979-2023 (INCOIS LAS)',
                     'temperature': 'potential', 'monthly': '2020-05'}
    one = b''.join(base64.b64decode(B.pack(model[0, vi], k)) for vi, k in enumerate(['temp', 'sal', 'u', 'v']))
    return {
        'schema': B.SCHEMA, 'meta': meta,
        'model': base64.b64encode(one * nt).decode('ascii'),       # [time][variable][level][lat][lon]: one month, nt times
        'mask': {'wet': base64.b64encode(np.packbits(wet.astype(np.uint8).ravel()).tobytes()).decode('ascii'),
                 'layout': '[level][lat][lon]', 'bitorder': 'big'},
        'alt': base['alt'], 'altGrid': base['altGrid'], 'argo': base['argo'], 'track': base['track'],
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--cache', default=os.environ.get('OCEANLENS_CACHE') or os.path.join(HERE, 'cache'))
    ap.add_argument('--out', default=os.path.join(HERE, '..', 'data', 'amphan-2020-igora.json'))
    ap.add_argument('--fetch', action='store_true', help='ask the LAS for what the cache lacks')
    ap.add_argument('--check', action='store_true', help='rebuild and compare with --out instead of writing it')
    a = ap.parse_args(argv)
    F = B.Fetcher(a.cache, fetch=a.fetch, extra_cadata=intermediate())
    try:
        text = B.serialise(build(F))
    except B.CacheMiss as e:
        print(f'missing from the cache: {e} (run with --fetch)', file=sys.stderr)
        return 2
    if a.check:
        with open(a.out, 'rb') as f:
            same = f.read() == text
        print(f"check {'ok' if same else 'failed'}: {a.out}")
        return 0 if same else 1
    B.write_atomic(a.out, text)
    print(f'wrote {a.out} ({len(text) / 1e6:.1f} MB)')
    return 0


if __name__ == '__main__':
    sys.exit(main())
