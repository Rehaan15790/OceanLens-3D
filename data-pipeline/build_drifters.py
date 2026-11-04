#!/usr/bin/env python3
"""Build data/drifters-2020-05.json: real drifting buoys in the map during the Amphan window, for the drift check.

The NOAA Global Drifter Program hourly product (quality-controlled, interpolated; Elipot et al. 2016), read from the
GDP ERDDAP at AOML, every 6 hours (00, 06, 12 and 18 UTC) from the first analysis to the last. The app draws the tracks
and runs its surface drift from each drifter's position at each analysis, to compare with where the drifter went.

    python data-pipeline/build_drifters.py [--fetch] [--cache DIR] [--out FILE] [--check]

Raw responses go through the same cache and manifest as build_snapshot.py (cache name drifters/...). Without --fetch
nothing is downloaded. The same cache always gives a byte-identical file.
"""
import argparse, csv, io, os, sys, urllib.parse
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import build_snapshot as B                                   # noqa: E402  (the shared fetcher, limits and writer)

GDP = 'https://erddap.aoml.noaa.gov/gdp/erddap/tabledap/drifter_hourly_qc.csv'
COLUMNS = ['ID', 'time', 'latitude', 'longitude', 'drogue_lost_date', 'typebuoy', 'location_type']
STEP_H = 6
SCHEMA = 1
CACHE_NAME = 'drifters/gdp_hourly_2020-05.csv'
CITATION = {'id': 'gdp', 'doi': '10.25921/x46c-3620',
            'text': 'Elipot, S., A. Sykulski, R. Lumpkin, L. Centurioni and M. Pazos (2022), Hourly location, current '
                    'velocity, and temperature collected from Global Drifter Program drifters world-wide, version '
                    'v2.01, NOAA NCEI, doi:10.25921/x46c-3620; Elipot, S. et al. (2016), A global surface drifter data '
                    'set at hourly resolution, J. Geophys. Res. Oceans 121, 2937-2966, doi:10.1002/2016JC011716. Data '
                    'collected and made freely available by the NOAA Global Drifter Program (CC BY 4.0), accessed from '
                    'erddap.aoml.noaa.gov.'}
MIN_DRIFTERS = 5


def window():
    """The analysis window of the snapshots: the first to the last analysis time."""
    return B.SNAPS[0], B.SNAPS[-1]


def query():
    t0, t1 = window()
    return (','.join(COLUMNS) + f'&time>={t0:%Y-%m-%dT%H:%M:%SZ}&time<={t1:%Y-%m-%dT%H:%M:%SZ}'
            f'&latitude>={B.LAT0}&latitude<={B.LAT1}&longitude>={B.LON0}&longitude<={B.LON1}')


def url():
    return GDP + '?' + urllib.parse.quote(query(), safe='&=,()')


def utc(s):
    return datetime.strptime(s, '%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=timezone.utc)


def parse(data):
    """ERDDAP CSV (a header row, a units row, then data) -> rows of dicts; refuses anything else."""
    text = data.decode('utf-8')
    rows = list(csv.reader(io.StringIO(text)))
    B.require(len(rows) >= 2 and rows[0] == COLUMNS, f'the GDP response does not start with {",".join(COLUMNS)}')
    out = []
    for r in rows[2:]:
        if not r:
            continue
        B.require(len(r) == len(COLUMNS), f'a GDP row has {len(r)} fields, not {len(COLUMNS)}')
        rec = dict(zip(COLUMNS, r))
        B.require(rec['ID'].isdigit() and len(rec['ID']) <= 20, f'bad drifter id {rec["ID"][:30]!r}')
        lat, lon = float(rec['latitude']), float(rec['longitude'])
        B.require(-90 <= lat <= 90 and -180 <= lon <= 360, f'drifter {rec["ID"]}: position {lat}, {lon} out of range')
        out.append({'id': rec['ID'], 't': utc(rec['time']), 'lat': lat, 'lon': lon,
                    'lost': utc(rec['drogue_lost_date']) if rec['drogue_lost_date'] else None,
                    'type': rec['typebuoy'], 'fix': rec['location_type']})
    return out


def drifters_from_rows(rows):
    """Every STEP_H hours from the first analysis, inside the map: [{id, type, drogueLost, fixes [[step, lat, lon]]}]."""
    t0, t1 = window()
    by = {}
    for r in rows:
        k, rem = divmod((r['t'] - t0).total_seconds(), STEP_H * 3600)
        if rem or not (t0 <= r['t'] <= t1):
            continue
        if not (B.LAT0 <= r['lat'] <= B.LAT1 and B.LON0 <= r['lon'] <= B.LON1):
            continue
        d = by.setdefault(r['id'], {'id': r['id'], 'type': r['type'], 'lost': r['lost'], 'fixes': {}})
        d['fixes'][int(k)] = (round(r['lat'], 4), round(r['lon'], 4))
    out = []
    for i in sorted(by):
        d = by[i]
        if len(d['fixes']) < 2:
            continue
        out.append({'id': d['id'], 'type': d['type'],
                    'drogueLost': d['lost'].strftime('%Y-%m-%dT%H:%MZ') if d['lost'] else None,
                    'fixes': [[k, *d['fixes'][k]] for k in sorted(d['fixes'])]})
    return out


def build(F):
    rows = F.get(url(), CACHE_NAME, parse)
    drifters = drifters_from_rows(rows)
    B.require(len(drifters) >= MIN_DRIFTERS, f'only {len(drifters)} drifters in the map')
    t0, t1 = window()
    man = F.manifest()
    entry = man.get(CACHE_NAME, {})
    gps = sum(r['fix'] == 'GPS' for r in rows)
    return {
        'schema': SCHEMA,
        'meta': {
            'title': 'Drifting buoys, Cyclone Amphan window, May 2020',
            't0': t0.strftime('%Y-%m-%dT%H:%MZ'), 't1': t1.strftime('%Y-%m-%dT%H:%MZ'), 'stepH': STEP_H,
            'source': 'NOAA Global Drifter Program, hourly quality-controlled interpolated positions '
                      '(drifter_hourly_qc v2.01) via erddap.aoml.noaa.gov, every 6 h inside the map. A drogued SVP '
                      'drifter follows the current at 15 m; one that has lost its drogue also moves with the wind.',
            'citation': CITATION,
            'provenance': {'url': GDP, 'query': query(), 'rows': len(rows), 'gps_rows': gps,
                           'kept': 'positions at 00, 06, 12 and 18 UTC inside the map, from the first analysis to '
                                   'the last; a drifter with fewer than 2 such positions is left out',
                           'counts': {'drifters': len(drifters), 'fixes': sum(len(d['fixes']) for d in drifters)}},
            'inputs': {CACHE_NAME: {'sha256': F.used[CACHE_NAME], 'accessed': entry.get('fetched', '')[:10]}},
        },
        'drifters': drifters,
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--cache', default=os.environ.get('OCEANLENS_CACHE') or os.path.join(HERE, 'cache'))
    ap.add_argument('--out', default=os.path.join(HERE, '..', 'data', 'drifters-2020-05.json'))
    ap.add_argument('--fetch', action='store_true', help='download the file if the cache lacks it')
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
