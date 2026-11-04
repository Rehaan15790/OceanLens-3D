#!/usr/bin/env python3
"""Build data/moorings-2020-05.json: the RAMA moorings inside the map during the Amphan window.

RAMA (the Research Moored Array for African-Asian-Australian Monsoon Analysis and Prediction) keeps buoys in the
Bay of Bengal and the Arabian Sea that measure temperature and salinity at fixed depths, and currents near the
surface. The daily means of the NOAA PMEL GTMBA data sets (pmelTaoDyT, pmelTaoDyS, pmelTaoDyCur, via NOAA CoastWatch's ERDDAP),
12:00 UTC, 4-25 May 2020, with the GTMBA quality codes 1, 2 and 3 kept (highest, default, adjusted).

    python data-pipeline/build_moorings.py [--fetch] [--cache DIR] [--out FILE] [--check]

Same cache and manifest as build_snapshot.py (cache names moorings/...). The same cache gives a byte-identical file.
"""
import argparse, csv, io, os, sys, urllib.parse

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import build_snapshot as B                      # noqa: E402

# NOAA CoastWatch's ERDDAP serves PMEL's GTMBA sets over https (PMEL's own ERDDAP redirects there over http)
ERDDAP = 'https://coastwatch.pfeg.noaa.gov/erddap/tabledap'
# data set -> (value columns, quality column, scale to SI): temperature degC, salinity PSU, currents cm/s -> m/s
SETS = {'temp': ('pmelTaoDyT', ['T_20'], 'QT_5020', 1.0),
        'sal': ('pmelTaoDyS', ['S_41'], 'QS_5041', 1.0),
        'cur': ('pmelTaoDyCur', ['U_320', 'V_321'], 'QCS_5300', 0.01)}
GOOD = {'1', '2', '3'}                          # GTMBA quality: highest, default, adjusted
SCHEMA = 1
MIN_MOORINGS = 3
CITATION = {'id': 'rama', 'doi': '10.1175/2008BAMS2608.1',
            'text': 'RAMA mooring data from the Global Tropical Moored Buoy Array, provided by the GTMBA Project Office '
                    'of NOAA/PMEL. McPhaden, M. J. et al. (2009), RAMA: The Research Moored Array for African-Asian-'
                    'Australian Monsoon Analysis and Prediction, Bull. Amer. Meteor. Soc. 90, 459-480, '
                    'doi:10.1175/2008BAMS2608.1. RAMA is supported by NOAA, MoES/INCOIS (India) and other partners.'}


def query(key):
    ds, cols, q, _ = SETS[key]
    t0, t1 = B.SNAPS[0], B.SNAPS[-1]
    return (','.join(['station', 'latitude', 'longitude', 'depth', 'time'] + cols + [q]) +
            f'&array="RAMA"&latitude>={B.LAT0}&latitude<={B.LAT1}&longitude>={B.LON0}&longitude<={B.LON1}'
            f'&time>={t0:%Y-%m-%dT00:00:00Z}&time<={t1:%Y-%m-%dT23:59:59Z}')


def url(key):
    return f'{ERDDAP}/{SETS[key][0]}.csv?' + urllib.parse.quote(query(key), safe='&=,()')


def parser(key):
    ds, cols, q, _ = SETS[key]
    head = ['station', 'latitude', 'longitude', 'depth', 'time'] + cols + [q]

    def parse(data):
        rows = list(csv.reader(io.StringIO(data.decode('utf-8'))))
        B.require(len(rows) >= 2 and rows[0] == head, f'{ds}: the response does not start with {",".join(head)}')
        out = []
        for r in rows[2:]:
            if not r:
                continue
            B.require(len(r) == len(head), f'{ds}: a row has {len(r)} fields')
            B.require(len(r[0]) <= 16 and r[0].replace('.', '').isalnum(), f'{ds}: bad station name {r[0][:20]!r}')
            out.append(r)
        return out
    return parse


def build(F):
    per = {}                                    # station -> {lat, lon, days: {date: {key: {depth: value}}}}
    kept = dropped = 0
    for key in SETS:
        ds, cols, q, scale = SETS[key]
        for r in F.get(url(key), f'moorings/{ds}-2020-05.csv', parser(key), retries=6):
            st, lat, lon, z, t = r[0], float(r[1]), float(r[2]), float(r[3]), r[4]
            vals, flag = r[5:5 + len(cols)], r[-1].split('.')[0]              # codes come as 2.0
            # one quality code for a current pair; temperature or salinity: its own
            if flag not in GOOD or any(v in ('', 'NaN') for v in vals):
                dropped += 1
                continue
            day = t[:10]
            B.require(t[10:] == 'T12:00:00Z', f'{ds}: {st} {t} is not a 12:00 UTC daily mean')
            m = per.setdefault(st, {'lat': lat, 'lon': lon if lon <= 180 else lon - 360, 'days': {}})
            v = [float(x) * scale for x in vals]
            m['days'].setdefault(day, {}).setdefault(key, {})[z] = v if key == 'cur' else v[0]
            kept += 1
    moorings = []
    for st in sorted(per):
        m = per[st]
        days = sorted(m['days'])
        depth = {k: sorted({z for d in days for z in m['days'][d].get(k, {})}) for k in SETS}

        def column(key, d, z, part=None):
            v = m['days'][d].get(key, {}).get(z)
            if v is None:
                return None
            return round(v[part], 4) if part is not None else round(v, 4)
        moorings.append({
            'station': st, 'lat': m['lat'], 'lon': m['lon'], 'days': days,
            'depth': {k: depth[k] for k in SETS},
            'temp': [[column('temp', d, z) for z in depth['temp']] for d in days],
            'sal': [[column('sal', d, z) for z in depth['sal']] for d in days],
            'u': [[column('cur', d, z, 0) for z in depth['cur']] for d in days],
            'v': [[column('cur', d, z, 1) for z in depth['cur']] for d in days],
        })
    B.require(len(moorings) >= MIN_MOORINGS, f'only {len(moorings)} RAMA moorings in the map')
    man = F.manifest()
    return {
        'schema': SCHEMA,
        'meta': {
            'title': 'RAMA moorings, Cyclone Amphan window, May 2020',
            'source': 'NOAA PMEL GTMBA daily means (pmelTaoDyT, pmelTaoDyS, pmelTaoDyCur) via coastwatch.pfeg.noaa.gov ERDDAP, '
                      'array RAMA, inside the map, 4-25 May 2020; quality codes 1, 2 and 3 kept. Temperature degC at '
                      'the sensor depths, salinity PSU, currents m/s (eastward u, northward v) from the current meters.',
            'citation': CITATION,
            'provenance': {'url': ERDDAP, 'queries': {k: query(k) for k in SETS}, 'quality_kept': sorted(GOOD),
                           'values_kept': kept, 'values_dropped': dropped},
            'inputs': {n: {'sha256': F.used[n], 'accessed': man.get(n, {}).get('fetched', '')[:10]}
                       for n in sorted(F.used)},
        },
        'moorings': moorings,
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--cache', default=os.environ.get('OCEANLENS_CACHE') or os.path.join(HERE, 'cache'))
    ap.add_argument('--out', default=os.path.join(HERE, '..', 'data', 'moorings-2020-05.json'))
    ap.add_argument('--fetch', action='store_true', help='download what the cache lacks')
    ap.add_argument('--ca-bundle', default=os.environ.get('OCEANLENS_CA_BUNDLE'))
    ap.add_argument('--check', action='store_true', help='rebuild and compare with --out instead of writing it')
    a = ap.parse_args(argv)
    F = B.Fetcher(a.cache, fetch=a.fetch, cafile=a.ca_bundle)
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
    print(f'wrote {a.out} ({len(text) / 1000:.0f} kB)')
    return 0


if __name__ == '__main__':
    sys.exit(main())
