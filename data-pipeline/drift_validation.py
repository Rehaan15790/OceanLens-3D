#!/usr/bin/env python3
"""Check the drift tool against real drifting buoys and write data/drift-validation.json.

    python data-pipeline/drift_validation.py            # write the file
    python data-pipeline/drift_validation.py --check    # recompute and compare with the committed file

Reads the snapshots and data/amphan-2020-extras.json only (no network). For every NOAA Global Drifter Program buoy
in the map with hourly positions for a whole 72 h, a drift is released at the buoy's position at 12:00 UTC each day
from 4 to 22 May 2020 and run with the drift tool's model (drift_reference.py, the same as src/js/drift.js):
- the separation between the drift and the buoy after 24, 48 and 72 h, against a baseline that assumes the buoy
  stays where it was;
- the Liu and Weisberg (2011) skill score over 72 h (1 - the summed separations / the summed distances the buoy
  travelled from its start; 0 when that is negative);
- for the random-walk ensemble (200 members within 1 km), how often the buoy ends inside the 90 % ellipse, for
  several diffusivities K: the one that comes closest to 90 % is the app's K.
Cases are split by drogue: a buoy whose 15 m drogue was on for the whole 72 h follows the upper ocean; one without
it is also pushed by the wind, which the model leaves out.
"""
import argparse, json, math, os, sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import drift_reference as dr                                        # noqa: E402
import facts_reference as fr                                        # noqa: E402

HORIZONS = (24, 48, 72)
K_VALUES = (0, 100, 300, 1000, 3000)
MEMBERS, R0_KM, SEED = 200, 1.0, 20200504
FIRST_START_H, LAST_START_H = 0, 432                                # 4 May 12Z .. 22 May 12Z, daily


def cases(extras):
    """(drifter index, start hour, positions [73, 2], drogued) for every whole 72 h window."""
    d = extras['drifters']
    out = []
    for n, (info, tr) in enumerate(zip(d['drifters'], d['tracks'])):
        pos = {h: (la, lo) for h, la, lo in tr}
        lost = info['drogue_lost_h']
        for h0 in range(FIRST_START_H, LAST_START_H + 1, 24):
            if all(h0 + s in pos for s in range(73)):
                p = np.array([pos[h0 + s] for s in range(73)])
                drogued = lost is None or lost > h0 + 72
                undrogued = lost is not None and lost <= h0
                out.append((n, h0, p, 'drogued' if drogued else 'undrogued' if undrogued else 'mixed'))
    return out


def stats(x):
    x = np.asarray(x, 'f8')
    return {'median': round(float(np.median(x)), 1), 'mean': round(float(np.mean(x)), 1),
            'p90': round(float(np.percentile(x, 90)), 1), 'n': int(x.size)} if x.size else None


def central(cur, land, cs, t0_ms):
    """The central drift of every case at once; separations and skill."""
    lat0 = np.array([c[2][0, 0] for c in cs]); lon0 = np.array([c[2][0, 1] for c in cs])
    starts = np.array([t0_ms + c[1] * 3.6e6 for c in cs])
    la, lo, end, _ = dr.run(cur, land, lat0, lon0, starts, 72)
    obs = np.stack([c[2] for c in cs], 1)                            # [73, cases, 2]
    sep = dr.km(la, lo, obs[:, :, 0], obs[:, :, 1])                  # [73, cases]
    travelled = np.vstack([np.zeros(len(cs)), np.cumsum(dr.km(obs[:-1, :, 0], obs[:-1, :, 1], obs[1:, :, 0], obs[1:, :, 1]), 0)])
    stay = dr.km(obs[0, :, 0][None], obs[0, :, 1][None], obs[:, :, 0], obs[:, :, 1])
    with np.errstate(invalid='ignore', divide='ignore'):
        ss = 1 - sep[1:].sum(0) / travelled[1:].sum(0)
    ss = np.where(np.isfinite(ss), np.maximum(ss, 0), 0)
    return sep, stay, ss, end


def coverage(cur, land, cs, t0_ms, K, rng):
    """For each case: whether the buoy is inside the 90 % ellipse at each horizon, and the ellipse area (km2)."""
    P = MEMBERS
    lat0 = np.repeat([c[2][0, 0] for c in cs], P); lon0 = np.repeat([c[2][0, 1] for c in cs], P)
    starts = np.repeat([t0_ms + c[1] * 3.6e6 for c in cs], P)
    la, lo, end, end_h = dr.run(cur, land, lat0, lon0, starts, 72, K=K, r0_km=R0_KM, rng=rng)
    inside, area = {h: [] for h in HORIZONS}, {h: [] for h in HORIZONS}
    for n, c in enumerate(cs):
        sl = slice(n * P, (n + 1) * P)
        for h in HORIZONS:
            keep = (end[sl] == 0) | (end_h[sl] > h)
            sp = dr.spread(la[h, sl], lo[h, sl], keep)
            if sp is None:
                continue
            inside[h].append(dr.inside_90(sp, c[2][h, 0], c[2][h, 1]))
            ev = np.clip(np.linalg.eigvalsh(sp[2]), 0, None)
            area[h].append(math.pi * dr.P90 ** 2 * math.sqrt(ev[0] * ev[1]))
    return ({str(h): round(100 * float(np.mean(inside[h])), 1) if inside[h] else None for h in HORIZONS},
            {str(h): round(float(np.median(area[h]))) if area[h] else None for h in HORIZONS})


def validate(root):
    load = lambda name: json.load(open(os.path.join(root, 'data', name), encoding='utf-8'))
    extras = load('amphan-2020-extras.json')
    snap = fr.Snapshot(os.path.join(root, 'data', 'amphan-2020.json'))
    gsnap = fr.Snapshot(os.path.join(root, 'data', 'amphan-2020-glorys.json'))
    land = dr.Land(snap)
    t0_ms = dr.Currents._ms(extras['drifters']['t0'])
    cs = cases(extras)
    variants = {
        'hycom': ('HYCOM surface currents every 6 h (what the app uses)', dr.Currents.from_extras(extras, 'hycom')),
        'glorys': ('GLORYS daily-mean surface currents (what the app uses)', dr.Currents.from_extras(extras, 'glorys')),
        'hycom_analyses': ('HYCOM at the 8 analyses only, 3 days apart', dr.Currents.from_snapshot(snap)),
        'glorys_analyses': ('GLORYS at the 8 analysis days only, 3 days apart', dr.Currents.from_snapshot(gsnap)),
    }
    groups = {g: [i for i, c in enumerate(cs) if c[3] == g] for g in ('drogued', 'undrogued', 'mixed')}
    out = {'schema': 1, 'meta': {
        'title': 'The drift tool against NOAA Global Drifter Program buoys, 4-25 May 2020',
        'script': 'data-pipeline/drift_validation.py',
        'method': ('A drift released at each buoy\'s position at 12:00 UTC every day from 4 to 22 May, run for 72 h '
                   'with the drift tool\'s model (data-pipeline/drift_reference.py, as src/js/drift.js). Separation: '
                   'great-circle distance from the buoy. Baseline: the buoy assumed to stay at its start. Skill: Liu and '
                   'Weisberg (2011), hourly, n = 1. Coverage: the share of cases whose buoy ends inside the 90 % ellipse '
                   f'of {MEMBERS} random-walk members released within {R0_KM:g} km.'),
        'citation': 'Liu, Y. and Weisberg, R. H. (2011). Evaluation of trajectory modeling in different dynamic regions '
                    'using normalized cumulative Lagrangian separation. Journal of Geophysical Research, 116, C09013.',
        'drogue': 'GDP drogues are centred at 15 m; the model currents are at the surface (HYCOM 0 m, GLORYS 0.49 m).',
        'cases': {g: len(ix) for g, ix in groups.items()},
        'drifters': {g: len({cs[i][0] for i in ix}) for g, ix in groups.items()},
        'k_values_m2_s': list(K_VALUES)}, 'models': {}}
    for key, (label, cur) in variants.items():
        sep, stay, ss, end = central(cur, land, cs, t0_ms)
        m = {'label': label}
        for g, ix in groups.items():
            if not ix:
                continue
            m[g] = {'separation_km': {str(h): stats(sep[h, ix]) for h in HORIZONS},
                    'stay_put_km': {str(h): stats(stay[h, ix]) for h in HORIZONS},
                    'beats_stay_put_pct': {str(h): round(100 * float(np.mean(sep[h, ix] < stay[h, ix])), 1) for h in HORIZONS},
                    'skill_72h': {'median': round(float(np.median(ss[ix])), 2), 'mean': round(float(np.mean(ss[ix])), 2)},
                    'stopped': int((end[ix] != 0).sum())}
        out['models'][key] = m
    # the ensemble: coverage of the 90 % ellipse for each K, drogued buoys, the fields the app uses
    dro = [cs[i] for i in groups['drogued']]
    best = {}
    for key in ('hycom', 'glorys'):
        cov = {}
        for K in K_VALUES:
            c, a = coverage(variants[key][1], land, dro, t0_ms, K, np.random.default_rng(SEED + K))
            cov[str(K)] = {'inside_pct': c, 'area_km2': a}
        out['models'][key]['drogued']['ensemble'] = cov
        best[key] = min(K_VALUES, key=lambda K: (abs(cov[str(K)]['inside_pct']['72'] - 90), K))
    # one K for the app: the smallest of the two models' best, rounded to the tested values
    out['meta']['best_k_m2_s'] = best
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--root', default=os.path.join(HERE, '..'))
    ap.add_argument('--check', action='store_true')
    args = ap.parse_args(argv)
    res = validate(args.root)
    text = (json.dumps(res, indent=1, ensure_ascii=True) + '\n').encode('ascii')
    path = os.path.normpath(os.path.join(args.root, 'data', 'drift-validation.json'))
    if args.check:
        same = os.path.exists(path) and open(path, 'rb').read() == text
        print(('check ok: ' if same else 'check failed: ') + path + (' matches a recomputation' if same else ' differs'))
        return 0 if same else 1
    with open(path, 'wb') as f:
        f.write(text)
    print(f'wrote {path}')
    for key, m in res['models'].items():
        d = m.get('drogued')
        if d:
            print(f"{key:16s} drogued: separation 24/48/72 h median "
                  + ' / '.join(f"{d['separation_km'][str(h)]['median']}" for h in HORIZONS)
                  + f" km; stay put {d['stay_put_km']['72']['median']} km; beats stay-put {d['beats_stay_put_pct']['72']} %;"
                  + f" skill {d['skill_72h']['median']}" + (f"; n {d['separation_km']['72']['n']}"))
            if 'ensemble' in d:
                print('     coverage at 72 h by K: ' + ', '.join(f"{K}: {v['inside_pct']['72']} % ({v['area_km2']['72']} km2)"
                                                         for K, v in d['ensemble'].items()))
        u = m.get('undrogued')
        if u:
            print(f"{'':16s} undrogued: separation 72 h median {u['separation_km']['72']['median']} km, n {u['separation_km']['72']['n']}, skill {u['skill_72h']['median']}")
    print('best K by model:', res['meta']['best_k_m2_s'], 'cases', res['meta']['cases'], 'drifters', res['meta']['drifters'])
    return 0


if __name__ == '__main__':
    sys.exit(main())
