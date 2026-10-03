#!/usr/bin/env python3
"""Generate the colour ramps for OceanLens 3D as 256-entry sRGB lookup tables (src/js/palettes.js).

Temperature, salinity and speed are designed with CIELAB L* rising linearly from the low end to the
high end (hue and chroma set in OKLab), so the order of values survives on a projector, in greyscale and under colour-vision
deficiency. Heat and depth keep the app's earlier stops (both already monotone in lightness). Every ramp
is checked: CIELAB L* must be strictly monotone, and the report lists L*, perceptual step uniformity and
the lightness order under simulated deuteranopia, protanopia and tritanopia.

    python make_palettes.py [--out ../src/js/palettes.js] [--report palettes-report.json]
"""
import argparse, json, os
import numpy as np

N = 256


# ---- colour science: sRGB (D65) <-> linear <-> OKLab, and CIELAB for checking ----
def srgb_to_linear(c):
    c = np.asarray(c, float)
    return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)


def linear_to_srgb(c):
    c = np.asarray(c, float)
    return np.where(c <= 0.0031308, 12.92 * c, 1.055 * np.abs(c) ** (1 / 2.4) - 0.055)


M1 = np.array([[0.4122214708, 0.5363325363, 0.0514459929],
               [0.2119034982, 0.6806995451, 0.1073969566],
               [0.0883024619, 0.2817188376, 0.6299787005]])
M2 = np.array([[0.2104542553, 0.7936177850, -0.0040720468],
               [1.9779984951, -2.4285922050, 0.4505937099],
               [0.0259040371, 0.7827717662, -0.8086757660]])


def linear_to_oklab(rgb):
    lms = np.cbrt(rgb @ M1.T)
    return lms @ M2.T


def oklab_to_linear(lab):
    lms = (lab @ np.linalg.inv(M2).T) ** 3
    return lms @ np.linalg.inv(M1).T


XYZ = np.array([[0.4124564, 0.3575761, 0.1804375],
                [0.2126729, 0.7151522, 0.0721750],
                [0.0193339, 0.1191920, 0.9503041]])
WHITE = np.array([0.95047, 1.0, 1.08883])


def srgb_to_cielab(srgb):
    xyz = srgb_to_linear(srgb) @ XYZ.T / WHITE
    f = np.where(xyz > (6 / 29) ** 3, np.cbrt(xyz), xyz / (3 * (6 / 29) ** 2) + 4 / 29)
    return np.stack([116 * f[..., 1] - 16, 500 * (f[..., 0] - f[..., 1]), 200 * (f[..., 1] - f[..., 2])], -1)


# Machado, Oliveira and Fernandes (2009), severity 1.0, applied to linear RGB
CVD = {
    'deuteranopia': np.array([[0.367322, 0.860646, -0.227968], [0.280085, 0.672501, 0.047413],
                              [-0.011820, 0.042940, 0.968881]]),
    'protanopia': np.array([[0.152286, 1.052583, -0.204868], [0.114503, 0.786281, 0.099216],
                            [-0.003882, -0.048116, 1.051998]]),
    'tritanopia': np.array([[1.255528, -0.076749, -0.178779], [-0.078411, 0.930809, 0.147602],
                            [0.004733, 0.691367, 0.303900]]),
}


def in_gamut(lab):
    rgb = oklab_to_linear(lab)
    return np.all((rgb >= -1e-6) & (rgb <= 1 + 1e-6), axis=-1)


def oklch_colour(L, C, h):
    """One colour from OKLCh; chroma is reduced where needed to stay inside sRGB (lightness and hue kept)."""
    lab = np.array([L, C * np.cos(h), C * np.sin(h)])
    if not in_gamut(lab):
        lo, hi = 0.0, C
        for _ in range(40):
            mid = (lo + hi) / 2
            if in_gamut(np.array([L, mid * np.cos(h), mid * np.sin(h)])):
                lo = mid
            else:
                hi = mid
        lab = np.array([L, lo * np.cos(h), lo * np.sin(h)])
    return np.clip(linear_to_srgb(np.clip(oklab_to_linear(lab), 0, 1)), 0, 1)


def lstar_ramp(Lstar, C, h_deg):
    """Colours whose CIELAB L* follows `Lstar` exactly, with the given OKLab chroma and hue."""
    out = np.zeros((len(Lstar), 3))
    for i, (target, c, h) in enumerate(zip(Lstar, C, np.radians(h_deg))):
        lo, hi = 0.0, 1.0
        for _ in range(40):
            mid = (lo + hi) / 2
            if srgb_to_cielab(oklch_colour(mid, c, h))[0] < target:
                lo = mid
            else:
                hi = mid
        out[i] = oklch_colour((lo + hi) / 2, c, h)
    return out


def smooth(t, knots, values):
    """Piecewise-linear then lightly smoothed curve through (knot, value) pairs. Hues are given unwrapped
    (e.g. 305 -> 380 for violet to red), so interpolation never swings through green."""
    v = np.interp(t, knots, values)
    k = np.ones(15) / 15
    pad = np.concatenate([np.full(7, v[0]), v, np.full(7, v[-1])])
    return np.convolve(pad, k, mode='valid')


def designed(L0, L1, c_knots, c_vals, h_knots, h_vals):
    """L* rises linearly from L0 to L1; chroma and hue follow smoothed knots."""
    t = np.linspace(0, 1, N)
    return lstar_ramp(L0 + (L1 - L0) * t, smooth(t, c_knots, c_vals), smooth(t, h_knots, h_vals))


def from_stops(stops):
    """The app's earlier ramps: linear in sRGB between stops (what paletteColor did), sampled 256 times."""
    t = np.linspace(0, 1, N)
    p = np.array([s[0] for s in stops]); c = np.array([s[1] for s in stops], float) / 255
    return np.stack([np.interp(t, p, c[:, k]) for k in range(3)], -1)


def ramps():
    return {
        # dark blue -> violet -> red -> orange -> pale yellow; warm water is the light end
        'temperature': ('Temperature', designed(18, 95, [0, 0.35, 0.7, 1], [0.12, 0.17, 0.19, 0.13],
                                                [0, 0.3, 0.55, 0.8, 1], [262, 305, 380, 415, 455])),
        # navy -> blue -> cyan -> pale cyan; fresh water is the dark end
        'salinity': ('Salinity', designed(16, 93, [0, 0.4, 0.8, 1], [0.09, 0.12, 0.10, 0.06],
                                          [0, 0.4, 0.8, 1], [265, 245, 215, 195])),
        # dark green -> green -> yellow-green -> pale yellow; fast is light
        'speed': ('Speed', designed(12, 92, [0, 0.4, 0.8, 1], [0.06, 0.12, 0.15, 0.13],
                                    [0, 0.4, 0.8, 1], [165, 145, 125, 105])),
        # dark violet-blue -> teal -> green -> pale yellow-green; high chlorophyll is light (meant for log scale)
        'chlorophyll': ('Chlorophyll', designed(14, 93, [0, 0.4, 0.8, 1], [0.08, 0.11, 0.15, 0.14],
                                                [0, 0.35, 0.7, 1], [290, 225, 150, 112])),
        'heat': ('Heat', from_stops([[0, [22, 18, 44]], [0.3, [104, 32, 96]], [0.6, [214, 78, 52]],
                                     [0.85, [248, 176, 62]], [1, [255, 244, 204]]])),
        # index 0 = shallow (pale), 255 = deep (dark)
        'depth': ('Depth', from_stops([[0, [222, 246, 236]], [0.3, [112, 204, 190]], [0.65, [42, 122, 170]],
                                       [1, [32, 42, 112]]])),
    }


def quantise(rgb):
    """8-bit colours that keep CIELAB L* strictly monotone: of the 8 floor/ceil roundings of each entry,
    take the one nearest in OKLab among those that continue the lightness order."""
    rising = srgb_to_cielab(rgb[-1])[0] > srgb_to_cielab(rgb[0])[0]
    src = rgb if rising else rgb[::-1]
    corners = np.array([[a, b, c] for a in (0, 1) for b in (0, 1) for c in (0, 1)])
    out, prev = [], -1.0
    for c in src:
        cand = np.clip(np.floor(c * 255)[None] + corners, 0, 255)
        L = srgb_to_cielab(cand / 255)[:, 0]
        d = np.linalg.norm(linear_to_oklab(srgb_to_linear(cand / 255)) - linear_to_oklab(srgb_to_linear(c)), axis=1)
        ok = L > prev + 1e-9
        k = int(np.argmin(np.where(ok, d, np.inf))) if ok.any() else int(np.argmax(L))
        out.append(cand[k]); prev = L[k]
    q = np.array(out, int)
    return q if rising else q[::-1]


def report(name, q):
    srgb = q / 255
    lab = srgb_to_cielab(srgb)
    L = lab[:, 0]
    dL = np.diff(L)
    ok = bool(np.all(dL > 0) or np.all(dL < 0))
    step = np.linalg.norm(np.diff(linear_to_oklab(srgb_to_linear(srgb)), axis=0), axis=1)
    out = {'monotone_L': ok, 'L_min': round(float(L.min()), 1), 'L_max': round(float(L.max()), 1),
           'L_at_sixteenths': [round(float(x), 1) for x in L[::17]],
           'min_abs_L_step': round(float(np.abs(dL).min()), 3),
           'oklab_step_cv': round(float(step.std() / step.mean()), 3)}
    for k, m in CVD.items():
        sim = linear_to_srgb(np.clip(srgb_to_linear(srgb) @ m.T, 0, 1))
        Ls = srgb_to_cielab(sim)[:, 0]
        Ls = Ls if Ls[-1] > Ls[0] else -Ls
        reversal = float(np.max(np.maximum.accumulate(Ls) - Ls))
        out[k] = {'largest_L_reversal': round(reversal, 2), 'monotone_L': reversal < 1.0,
                  'end_to_end_dE76': round(float(np.linalg.norm(srgb_to_cielab(sim)[-1] - srgb_to_cielab(sim)[0])), 1)}
    return out


def main(argv=None):
    here = os.path.dirname(os.path.abspath(__file__))
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--out', default=os.path.join(here, '..', 'src', 'js', 'palettes.js'))
    ap.add_argument('--report', default=None, help='also write the checks as JSON')
    ap.add_argument('--check', action='store_true', help='compare with --out instead of writing it')
    args = ap.parse_args(argv)
    lines, rep = [], {}
    for key, (label, rgb) in ramps().items():
        q = quantise(rgb)
        rep[key] = report(key, q)
        if not rep[key]['monotone_L']:
            raise SystemExit(f'{key}: CIELAB L* is not monotone')
        lut = ''.join(f'{r:02x}{g:02x}{b:02x}' for r, g, b in q)
        lines.append(f"  {key}: {{label:'{label}', lut:'{lut}'}}")
    js = ('// Generated by data-pipeline/make_palettes.py; do not edit by hand.\n'
          '// Each lut: 256 sRGB entries, 6 hex digits each; entry i is the colour at t = i/255, low value first.\n'
          'const PALETTES = {\n' + ',\n'.join(lines) + '\n};\n')
    if args.check:
        try:
            with open(args.out, encoding='ascii', newline='') as f:
                same = f.read() == js
        except OSError:
            same = False
        print(f"check {'ok' if same else 'failed'}: {args.out} {'matches' if same else 'differs from'} the generator")
        return 0 if same else 1
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, 'w', encoding='ascii', newline='\n') as f:
        f.write(js)
    if args.report:
        with open(args.report, 'w', encoding='utf-8', newline='\n') as f:
            json.dump(rep, f, indent=1)
    for k, r in rep.items():
        print(f"{k:12s} L* {r['L_min']:5.1f}..{r['L_max']:5.1f} monotone {r['monotone_L']}  step CV {r['oklab_step_cv']:.2f}"
              f"  deutan/protan/tritan monotone {r['deuteranopia']['monotone_L']}/{r['protanopia']['monotone_L']}/"
              f"{r['tritanopia']['monotone_L']}")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
