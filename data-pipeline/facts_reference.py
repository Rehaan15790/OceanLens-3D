#!/usr/bin/env python3
"""Independent recomputation of the numbers OceanLens 3D shows in captions and panels.

Reads data/amphan-2020.json directly (numpy only, no app code) and prints the facts as JSON. The app's
facts.js must agree with these within each fact's tolerance; tests compare the two.

Definitions (shared with facts.js):
- node land and seabed: median of the 0.1 deg ETOPO samples in the node's 0.5 deg cell (5 x 5, clipped at
  the edges), land where it is above 0; elsewhere (track, section) ETOPO bilinear, as the app
- Bay: lon > 80.5, 8-21 N, seabed > 200 m;  Arabian Sea: lon < 77, lat >= 8, seabed > 200 m
- model columns: bilinear in the horizontal, linear between the 11 levels (as the app)
"""
import base64, json, math, os, sys
from datetime import datetime, timezone
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
KM_PER_DEG = 111.195
SCALE_XZ, DEPTH_SCALE, DEEP_COMPRESSION = 0.55, 0.002, 0.085


class Snapshot:
    def __init__(self, path):
        with open(path, encoding='utf-8') as f:
            self.d = d = json.load(f)
        m = d['meta']
        self.m, self.depths = m, list(m['depths'])
        self.nt, self.nlat, self.nlon = len(m['times']), m['nlat'], m['nlon']
        q = np.frombuffer(base64.b64decode(d['model']), '<i2').astype(float)
        q = q.reshape(self.nt, len(m['vars']), len(self.depths), self.nlat, self.nlon)
        self.f = np.empty_like(q)
        for vi, v in enumerate(m['vars']):
            off, sc = m['quant'][v]
            self.f[:, vi] = off + q[:, vi] * sc
        g = d['altGrid']
        self.g = g
        self.alt = np.frombuffer(base64.b64decode(d['alt']), '<i2').astype(float).reshape(g['nlat'], g['nlon'])
        A = d['argo']; nL = len(A['levels'])
        self.levels = np.array(A['levels'], float)

        def dec(b64, key):
            off, sc = m['quant'][key]
            qq = np.frombuffer(base64.b64decode(b64), '<i2').astype(float)
            return np.where(qq == m['missing'], np.nan, off + qq * sc).reshape(-1, nL)
        self.T, self.S = dec(A['T'], 'temp'), dec(A['S'], 'sal')
        nP = len(A['profiles'])
        self.C = dec(A['C'], 'chl') if A.get('C') else np.full((nP, nL), np.nan)
        self.O = dec(A['O'], 'oxy') if A.get('O') and 'oxy' in m['quant'] else np.full((nP, nL), np.nan)
        self.profiles = A['profiles']
        mod = A.get('model')
        self.mT = dec(mod['T'], 'temp') if mod else None
        self.mS = dec(mod['S'], 'sal') if mod else None
        self.times = [datetime.strptime(t, '%Y-%m-%dT%H:%MZ').replace(tzinfo=timezone.utc) for t in m['times']]

    # ---- the app's samplers, re-implemented ----
    def altitude(self, lat, lon):
        g = self.g
        y = max(0, min(g['nlat'] - 1.000001, (lat - g['lat0']) / g['step']))
        x = max(0, min(g['nlon'] - 1.000001, (lon - g['lon0']) / g['step']))
        i, j = int(y), int(x); fy, fx = y - i, x - j
        a = self.alt
        return (a[i, j] * (1 - fx) + a[i, j + 1] * fx) * (1 - fy) + (a[i + 1, j] * (1 - fx) + a[i + 1, j + 1] * fx) * fy

    def cell_median(self, i, j):
        g = self.g
        ci, cj = round(i * self.m['step'] / g['step']), round(j * self.m['step'] / g['step'])
        blk = self.alt[max(0, ci - 2):min(g['nlat'], ci + 3), max(0, cj - 2):min(g['nlon'], cj + 3)]
        return float(np.median(blk))

    def land(self, lat, lon):
        return self.altitude(lat, lon) > 0

    def seabed(self, lat, lon):
        return max(5.0, -self.altitude(lat, lon))

    def at(self, vi, lat, lon, depth, t):
        m = self.m
        y = max(0, min(self.nlat - 1.000001, (lat - m['lat0']) / m['step']))
        x = max(0, min(self.nlon - 1.000001, (lon - m['lon0']) / m['step']))
        i, j = int(y), int(x); fy, fx = y - i, x - j
        D = self.depths; z = max(0, min(D[-1], depth)); k = 0
        while k < len(D) - 2 and z > D[k + 1]:
            k += 1
        fz = (z - D[k]) / (D[k + 1] - D[k])

        def lv(kk):
            a = self.f[t, vi, kk]
            return (a[i, j] * (1 - fx) + a[i, j + 1] * fx) * (1 - fy) + (a[i + 1, j] * (1 - fx) + a[i + 1, j + 1] * fx) * fy
        return lv(k) if fz == 0 else lv(k) * (1 - fz) + lv(k + 1) * fz

    def column(self, vi, lat, lon, t):
        return np.array([self.at(vi, lat, lon, d, t) for d in self.depths])


def rho_eos80(S, T):
    rw = (999.842594 + 6.793952e-2 * T - 9.095290e-3 * T ** 2 + 1.001685e-4 * T ** 3 - 1.120083e-6 * T ** 4
          + 6.536332e-9 * T ** 5)
    A = 0.824493 - 4.0899e-3 * T + 7.6438e-5 * T ** 2 - 8.2467e-7 * T ** 3 + 5.3875e-9 * T ** 4
    B = -5.72466e-3 + 1.0227e-4 * T - 1.6546e-6 * T ** 2
    return rw + A * S + B * S ** 1.5 + 4.8314e-4 * S * S


def tchp(depths, Tc, Sc, bottom, cp=4000.0):
    """cp * integral of rho (T - 26) dz from the surface to D26 (or the bottom), by fine quadrature."""
    if Tc[0] <= 26:
        return 0.0
    z = np.arange(0.0, bottom + 1e-9, 0.05)
    T = np.interp(z, depths, Tc); S = np.interp(z, depths, Sc)
    w = np.clip(T - 26, 0, None)
    cross = np.where(T <= 26)[0]
    if len(cross):
        w[cross[0]:] = 0
    return float(np.trapezoid(rho_eos80(S, T) * w, z) * cp / 1e7)


def inversion_flags(D):
    """Argo RTQC test 14 on consecutive valid levels, both directions (0.03 kg/m3)."""
    idx = [k for k in range(len(D)) if np.isfinite(D[k])]
    flag = np.zeros(len(D), bool)
    for a, b in zip(idx, idx[1:]):
        if D[b] < D[a] - 0.03:
            flag[b] = True
        if D[a] > D[b] + 0.03:
            flag[a] = True
    return flag


def first_crossing(depths, col, seabed, target):
    v0 = col[0]
    for k in range(len(depths) - 1):
        z0 = depths[k]
        if z0 >= seabed:
            return None
        z1 = min(depths[k + 1], seabed); v1 = np.interp(z1, depths, col)
        if (v0 - target) * (v1 - target) <= 0 and v0 != v1:
            return z0 + (z1 - z0) * (target - v0) / (v1 - v0)
        v0 = v1
    return None


def track(s):
    """In-map sea fixes and the interpolated coast crossing, as the app builds REAL_TRACK."""
    m = s.m
    lat1, lon1 = m['lat0'] + (s.nlat - 1) * m['step'], m['lon0'] + (s.nlon - 1) * m['step']
    fixes = [dict(t=f[0], lat=f[1], lon=f[2], usa=f[3], nd=f[4], grade=f[5]) for f in s.d['track']['fixes']]
    ts = lambda x: datetime.strptime(x[:16], '%Y-%m-%dT%H:%M').replace(tzinfo=timezone.utc).timestamp()
    T = []
    for f in fixes:
        if m['lat0'] <= f['lat'] <= lat1 and m['lon0'] <= f['lon'] <= lon1 and not s.land(f['lat'], f['lon']):
            T.append(f); continue
        if not T:
            continue
        a = T[-1]
        for k in range(1, 61):
            u = k / 60; la = a['lat'] + (f['lat'] - a['lat']) * u; lo = a['lon'] + (f['lon'] - a['lon']) * u
            if s.land(la, lo) or la > lat1:
                mix = lambda x, y: (x if y is None else y) if (x is None or y is None) else round(x + (y - x) * u)
                tt = ts(a['t']) + (ts(f['t']) - ts(a['t'])) * u
                T.append(dict(t=datetime.fromtimestamp(tt, timezone.utc).strftime('%Y-%m-%dT%H:%MZ'), lat=la, lon=lo,
                              usa=mix(a['usa'], f['usa']), nd=mix(a['nd'], f['nd']), grade=a['grade'], landfall=True))
                break
        break
    return T, ts


def facts(path):
    s = Snapshot(path)
    D = s.depths
    F = {'analysis': {'count': s.nt, 'times': s.m['times']}}
    P = s.profiles
    F['argo'] = {'profiles': len(P), 'floats': len({p['w'] for p in P}),
                 'perAnalysis': [sum(1 for p in P if p['s'] == t) for t in range(s.nt)],
                 'tempOnly': sum(1 for p in P if p.get('sq', 1) == 0)}
    hasC, hasO = np.isfinite(s.C).any(1), np.isfinite(s.O).any(1)
    B = [p for n, p in enumerate(P) if hasC[n] or hasO[n]]
    F['argo']['bgc'] = {'floats': len({p['w'] for p in B}), 'profiles': len(B),
                        'chl': int(hasC.sum()), 'oxy': int(hasO.sum())}

    T, ts = track(s)
    if not T:
        F.update(track=None, path=None, section=None)
        return comparison_and_rest(s, F)
    sea = [p for p in T if not p.get('landfall')]
    lf = T[-1]
    peak = lambda key: max(T, key=lambda p: p[key] or 0)

    def ri(key):
        best = None
        for i, a in enumerate(T):
            for b in T[i + 1:]:
                if abs(ts(b['t']) - ts(a['t']) - 86400) < 1 and a[key] and b[key]:
                    # ties go to the later window (17 May 06Z 70->140 kt beats 00Z 60->130 kt)
                    if best is None or b[key] - a[key] >= best['to'] - best['from']:
                        best = {'from': a[key], 'to': b[key], 'start': a['t'], 'end': b['t']}
        return best
    times = [t.timestamp() for t in s.times]
    before = max(0, sum(1 for x in times if x <= ts(T[0]['t']) - 12 * 3600) - 1)
    after = max(before + 1, next(i for i, x in enumerate(times) if x >= ts(lf['t']) + 12 * 3600))
    F['track'] = {'firstFix': T[0]['t'], 'inMapFixes': len(sea),
                  'leadDays': [round((ts(T[0]['t']) - x) / 86400, 2) for x in times],
                  'landfall': {'time': lf['t'], 'lat': round(lf['lat'], 3), 'lon': round(lf['lon'], 3),
                               'windJtwc': lf['usa'], 'windImd': lf['nd']},
                  'peakJtwc': {'kt': peak('usa')['usa'], 'time': peak('usa')['t']},
                  'peakImd': {'kt': peak('nd')['nd'], 'time': peak('nd')['t'], 'grade': peak('nd')['grade']},
                  'ri24Jtwc': ri('usa'), 'ri24Imd': ri('nd')}

    def heat(t):
        out = []
        for p in sea:
            sb = s.seabed(p['lat'], p['lon'])
            out.append(tchp(D, s.column(0, p['lat'], p['lon'], t), s.column(1, p['lat'], p['lon'], t), min(sb, D[-1])))
        return out
    hb, ha = heat(before), heat(after)
    F['path'] = {'beforeIndex': before, 'afterIndex': after, 'heatBefore': round(float(np.mean(hb)), 2),
                 'heatAfter': round(float(np.mean(ha)), 2)}

    # regions over grid nodes
    nodes = [(s.m['lat0'] + i * s.m['step'], s.m['lon0'] + j * s.m['step'], i, j)
             for i in range(s.nlat) for j in range(s.nlon)]
    med = {(i, j): s.cell_median(i, j) for la, lo, i, j in nodes}
    node = {(i, j): (la, lo, med[(i, j)] > 0, max(5.0, -med[(i, j)])) for la, lo, i, j in nodes}
    bay = [(i, j) for (i, j), (la, lo, ld, sb) in node.items() if not ld and sb > 200 and lo > 80.5 and 8 <= la <= 21]
    ars = [(i, j) for (i, j), (la, lo, ld, sb) in node.items() if not ld and sb > 200 and lo < 77 and la >= 8]
    F['bay'] = {'nodes': len(bay), 'sstP25': [], 't1000Mean': [], 'd26P10': [], 'd26P90': [], 'salMedian': []}
    F['arabian'] = {'nodes': len(ars), 'salMedian': []}
    for t in range(s.nt):
        sst = [s.f[t, 0, 0, i, j] for i, j in bay]
        deep = [s.f[t, 0, len(D) - 1, i, j] for i, j in bay if node[(i, j)][3] >= 1000]
        d26 = [first_crossing(D, s.f[t, 0, :, i, j], node[(i, j)][3], 26) for i, j in bay]
        d26 = [x for x in d26 if x is not None]
        F['bay']['sstP25'].append(round(float(np.percentile(sst, 25)), 3))
        F['bay']['t1000Mean'].append(round(float(np.mean(deep)), 3))
        F['bay']['d26P10'].append(round(float(np.percentile(d26, 10)), 1))
        F['bay']['d26P90'].append(round(float(np.percentile(d26, 90)), 1))
        F['bay']['salMedian'].append(round(float(np.median([s.f[t, 1, 0, i, j] for i, j in bay])), 3))
        F['arabian']['salMedian'].append(round(float(np.median([s.f[t, 1, 0, i, j] for i, j in ars])), 3))

    # East India Coastal Current: first water node east of the coast, 10-16 N, holding model water
    wet = None
    if 'mask' in s.d:
        bits = np.unpackbits(np.frombuffer(base64.b64decode(s.d['mask']['wet']), np.uint8))
        wet = bits[:len(D) * s.nlat * s.nlon].reshape(len(D), s.nlat, s.nlon).astype(bool)
    eicc = []
    for t in range(s.nt):
        best = (-1e9, None)
        for i in range(s.nlat):
            la = s.m['lat0'] + i * s.m['step']
            if not 10 <= la <= 16:
                continue
            for j in range(s.nlon):
                lo = s.m['lon0'] + j * s.m['step']
                if lo < 78 or node[(i, j)][2] or (wet is not None and not wet[0, i, j]):
                    continue
                v = s.f[t, 3, 0, i, j]
                if v > best[0]:
                    best = (v, la)
                break
        eicc.append({'v': round(float(best[0]), 3), 'lat': best[1]})
    F['currents'] = {'eiccMaxNorth': eicc}

    # the genesis-to-landfall chord, 200 columns
    a, b = (T[0]['lat'], T[0]['lon']), (lf['lat'], lf['lon'])

    def chord(t):
        sst, d26 = [], []
        for k in range(200):
            u = k / 199; la = a[0] + (b[0] - a[0]) * u; lo = a[1] + (b[1] - a[1]) * u
            if s.land(la, lo):
                continue
            sb = min(s.seabed(la, lo), D[-1]); col = s.column(0, la, lo, t)
            sst.append(col[0])
            x = first_crossing(D, col, sb, 26)
            if x is not None:
                d26.append(x)
        return float(np.mean(sst)), float(np.mean(d26))
    sb_, db_ = chord(before); sa_, da_ = chord(after)
    F['section'] = {'track': {'columns': 200, 'sstBefore': round(sb_, 3), 'sstAfter': round(sa_, 3),
                              'cooling': round(sb_ - sa_, 3), 'd26Before': round(db_, 1), 'd26After': round(da_, 1)}}

    return comparison_and_rest(s, F)


def comparison_and_rest(s, F):
    """The model-Argo comparison and the view constants (independent of the track)."""
    D, P = s.depths, s.profiles
    # model-Argo comparison, temperature and salinity, by band
    bands = [(0, 100), (100, 300), (300, 1000)]
    comp = []
    worst = []
    matched = s.mT is not None
    for t in range(s.nt):
        recs = {'temp': [], 'sal': []}
        wt = (-1, None)
        for n, p in enumerate(P):
            if p['s'] != t:
                continue
            salok = p.get('sq', 1) != 0
            ok = np.isfinite(s.T[n]) & (np.isfinite(s.S[n]) | (not salok))
            dens = np.where(ok & np.isfinite(s.S[n]), rho_eos80(s.S[n], s.T[n]) - 1000, np.nan)
            ok &= ~inversion_flags(dens)
            if matched:
                mt, ms = s.mT[n], s.mS[n]
            else:
                mt = np.interp(s.levels, D, s.column(0, p['la'], p['lo'], t))
                ms = np.interp(s.levels, D, s.column(1, p['la'], p['lo'], t))
            dT = (s.T[n] - mt)[ok & np.isfinite(mt)]
            zT = s.levels[ok & np.isfinite(mt)]
            recs['temp'] += list(zip(zT, dT))
            okS = ok & np.isfinite(s.S[n]) & np.isfinite(ms)
            recs['sal'] += list(zip(s.levels[okS], (s.S[n] - ms)[okS]))
            if len(dT):
                r = math.sqrt(float(np.mean(dT ** 2)))
                if r > wt[0]:
                    gap = (s.times[t] - datetime.strptime(p['t'], '%Y-%m-%dT%H:%MZ').replace(tzinfo=timezone.utc))
                    wt = (r, {'id': 'ARGO-' + p['w'], 'rmse': round(r, 3), 'gapHours': round(gap.total_seconds() / 3600)})
        row = {}
        for v, rr in recs.items():
            arr = np.array(rr)
            row[v] = []
            for z0, z1 in bands:
                d = arr[(arr[:, 0] > z0) & (arr[:, 0] <= z1), 1] if len(arr) else np.array([])
                row[v].append({'bias': round(float(d.mean()), 3), 'rmse': round(float(np.sqrt((d ** 2).mean())), 3),
                               'n': int(d.size)} if d.size else None)
        comp.append(row); worst.append(wt[1])
    deep = [r['temp'][2]['rmse'] for r in comp if r['temp'][2]]
    F['comparison'] = {'matchedInTime': matched, 'bands': comp, 'worst': worst,
                       'deepRmse': {'median': round(float(np.median(deep)), 3), 'min': min(deep), 'max': max(deep)}
                       if deep else None}
    F['view'] = {'exaggerationPerSlider': round(KM_PER_DEG * 1000 / SCALE_XZ * DEPTH_SCALE, 2),
                 'deepCompression': round(1 / DEEP_COMPRESSION, 2)}
    return F


# tolerance by leaf name: counts, ids and winds exact; the rest well inside what the UI rounds to
TOL = {'lat': 0.001, 'lon': 0.001, 'heatBefore': 0.05, 'heatAfter': 0.05, 'sstP25': 0.005, 't1000Mean': 0.005,
       'd26P10': 0.2, 'd26P90': 0.2, 'salMedian': 0.005, 'v': 0.005, 'sstBefore': 0.01, 'sstAfter': 0.01,
       'cooling': 0.01, 'd26Before': 0.2, 'd26After': 0.2, 'bias': 0.005, 'rmse': 0.005, 'median': 0.005,
       'min': 0.005, 'max': 0.005, 'leadDays': 0.01, 'exaggerationPerSlider': 0.1, 'deepCompression': 0.01}


def expected(F):
    """Flat map for tests/facts_expected.json: dotted path under facts().real -> value and tolerance."""
    out = {}

    def walk(x, path, leaf):
        if isinstance(x, dict):
            for k, v in x.items():
                walk(v, f'{path}.{k}', k)
        elif isinstance(x, list):
            for i, v in enumerate(x):
                walk(v, f'{path}[{i}]', leaf)
        elif x is not None and not isinstance(x, bool):
            out['real' + path] = {'value': x, 'tol': TOL.get(leaf, 0) if not isinstance(x, str) else 0,
                                  'how': 'data-pipeline/facts_reference.py'}
    for k in ('argo', 'track', 'path', 'bay', 'arabian', 'currents', 'section', 'comparison', 'view'):
        walk(F[k], '.' + k, k)
    return out


if __name__ == '__main__':
    import argparse
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('data', nargs='?', default=os.path.join(HERE, '..', 'data', 'amphan-2020.json'))
    ap.add_argument('--expected', help='write the flat expected-values file for tests/test_facts.py here')
    ap.add_argument('--check', action='store_true', help='with --expected, compare instead of writing')
    args = ap.parse_args()
    F = facts(args.data)
    if args.expected:
        text = json.dumps(expected(F), indent=1, sort_keys=True)
        if args.check:
            try:
                with open(args.expected, encoding='utf-8') as f:
                    same = f.read() == text
            except OSError:
                same = False
            print(f"check {'ok' if same else 'failed'}: {args.expected} {'matches' if same else 'differs from'} the data")
            sys.exit(0 if same else 1)
        with open(args.expected, 'w', encoding='utf-8', newline='\n') as f:
            f.write(text)
        print(f'wrote {len(expected(F))} expected facts to {args.expected}')
    else:
        print(json.dumps(F, indent=1))
