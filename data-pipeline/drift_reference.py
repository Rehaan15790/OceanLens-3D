"""The drift tool's trajectory model in numpy, written from its description and not from the app's code.

It follows src/js/drift.js step for step, for many particles at once:
- currents bilinear on the 0.5 deg grid (indices clamped as data-real.js realAt does), linear in time between the
  steps of their clock;
- one-hour midpoint steps: the current at the start moves a particle half a step, the current there half an hour on
  moves it the whole step; 1 m is 1/111 320 deg of latitude, and of longitude divided by cos(latitude);
- an optional random walk after each step, sigma = sqrt(2 K dt) per axis, and a release spread of r0;
- a particle stops when it leaves the map (position kept) or reaches land (ETOPO1 above 0 m, position kept there).

The tests compare the app's central drift with `run` (no random walk) hour by hour; drift_validation.py runs it
against the NOAA drifters, with and without the random walk.
"""
import base64
import math

import numpy as np

M_PER_DEG = 111320.0
P90 = math.sqrt(-2 * math.log(0.1))          # a 2-D normal holds 90 % within 2.146 standard deviations


class Currents:
    """Surface u, v [time][lat][lon] on a regular grid, with the clock of the times (ms since 1970)."""

    def __init__(self, clock_ms, u, v, grid):
        self.clock = np.asarray(clock_ms, 'f8')
        self.u, self.v = np.asarray(u, 'f8'), np.asarray(v, 'f8')
        self.g = grid

    @staticmethod
    def _ms(iso):
        from datetime import datetime, timezone
        return datetime.strptime(iso, '%Y-%m-%dT%H:%MZ').replace(tzinfo=timezone.utc).timestamp() * 1000

    @classmethod
    def from_extras(cls, extras, model):
        """surf.<model> of data/amphan-2020-extras.json (HYCOM every 6 h, GLORYS daily)."""
        s, g = extras['surf'][model], extras['meta']['grid']
        q = np.frombuffer(base64.b64decode(s['data']), '<i2').astype('f8')
        q = q.reshape(len(s['times']), 2, g['nlat'], g['nlon'])
        off, sc = s['quant']
        # the app stores the decoded values as float32: so does this, so both start from the same numbers
        f = (off + q * sc).astype('f4').astype('f8')
        return cls([cls._ms(t) for t in s['times']], f[:, 0], f[:, 1], g)

    @classmethod
    def from_snapshot(cls, snap):
        """The surface u, v of a snapshot's 8 analyses (facts_reference.Snapshot), the fields the app had before."""
        m = snap.m
        f = snap.f.astype('f4').astype('f8')
        return cls([t.timestamp() * 1000 for t in snap.times], f[:, 2, 0], f[:, 3, 0],
                   {'lat0': m['lat0'], 'lon0': m['lon0'], 'step': m['step'], 'nlat': m['nlat'], 'nlon': m['nlon']})

    def _space(self, field, lat, lon):
        g = self.g
        y = np.clip((lat - g['lat0']) / g['step'], 0, g['nlat'] - 1.000001)
        x = np.clip((lon - g['lon0']) / g['step'], 0, g['nlon'] - 1.000001)
        i, j = np.floor(y).astype(int), np.floor(x).astype(int)
        fy, fx = y - i, x - j
        return ((field[i, j] * (1 - fx) + field[i, j + 1] * fx) * (1 - fy)
                + (field[i + 1, j] * (1 - fx) + field[i + 1, j + 1] * fx) * fy)

    def uv(self, lat, lon, ms):
        c = self.clock
        if len(c) < 2:
            return self._space(self.u[0], lat, lon), self._space(self.v[0], lat, lon)
        k = np.clip(np.searchsorted(c[1:-1], ms, side='left'), 0, len(c) - 2)
        w = np.clip((ms - c[k]) / (c[k + 1] - c[k]), 0, 1)
        out = []
        for F in (self.u, self.v):
            a = self._bilinear_at(F, k, lat, lon)
            b = self._bilinear_at(F, np.minimum(k + 1, len(c) - 1), lat, lon)
            out.append(a + (b - a) * w)
        return out

    def _bilinear_at(self, F, k, lat, lon):
        g = self.g
        y = np.clip((lat - g['lat0']) / g['step'], 0, g['nlat'] - 1.000001)
        x = np.clip((lon - g['lon0']) / g['step'], 0, g['nlon'] - 1.000001)
        i, j = np.floor(y).astype(int), np.floor(x).astype(int)
        fy, fx = y - i, x - j
        return ((F[k, i, j] * (1 - fx) + F[k, i, j + 1] * fx) * (1 - fy)
                + (F[k, i + 1, j] * (1 - fx) + F[k, i + 1, j + 1] * fx) * fy)


class Land:
    """ETOPO1 from a snapshot: land where the bilinear altitude is above 0 m, as geo.js isLand."""

    def __init__(self, snap):
        self.g, self.a = snap.g, snap.alt
        m = snap.m
        self.box = (m['lat0'], m['lat0'] + (m['nlat'] - 1) * m['step'], m['lon0'], m['lon0'] + (m['nlon'] - 1) * m['step'])

    def __call__(self, lat, lon):
        g, a = self.g, self.a
        y = np.clip((lat - g['lat0']) / g['step'], 0, g['nlat'] - 1.000001)
        x = np.clip((lon - g['lon0']) / g['step'], 0, g['nlon'] - 1.000001)
        i, j = np.floor(y).astype(int), np.floor(x).astype(int)
        fy, fx = y - i, x - j
        alt = (a[i, j] * (1 - fx) + a[i, j + 1] * fx) * (1 - fy) + (a[i + 1, j] * (1 - fx) + a[i + 1, j + 1] * fx) * fy
        return alt > 0


def run(cur, land, lat, lon, t0_ms, hours, direction=1, K=0.0, r0_km=0.0, rng=None):
    """Particles from (lat, lon) at t0_ms (arrays, one per particle) for `hours` one-hour steps.
    Returns (lat[steps+1, P], lon[steps+1, P], end[P]: 0 afloat, 1 ashore, 2 off the map, end_h[P]).
    After a particle stops its later rows repeat its last position."""
    lat = np.array(lat, 'f8', ndmin=1)
    lon = np.array(lon, 'f8', ndmin=1)
    t0 = np.broadcast_to(np.asarray(t0_ms, 'f8'), lat.shape).copy()
    P, dt = lat.size, 3600.0
    cosd = lambda a: np.cos(np.radians(a))
    if r0_km > 0:
        la = lat + rng.standard_normal(P) * r0_km * 1000 / M_PER_DEG
        lo = lon + rng.standard_normal(P) * r0_km * 1000 / (M_PER_DEG * cosd(lat))
        bad = land(la, lo)
        lat, lon = np.where(bad, lat, la), np.where(bad, lon, lo)
    lat0, lat1, lon0, lon1 = land.box
    steps = int(round(hours))
    out_la, out_lo = np.empty((steps + 1, P)), np.empty((steps + 1, P))
    out_la[0], out_lo[0] = lat, lon
    end, end_h = np.zeros(P, int), np.full(P, float(steps))
    sig = math.sqrt(2 * K * dt)
    k = direction * dt / M_PER_DEG
    for s in range(1, steps + 1):
        live = end == 0
        ms = t0 + direction * (s - 1) * dt * 1000
        a_u, a_v = cur.uv(lat, lon, ms)
        mla, mlo = lat + 0.5 * a_v * k, lon + 0.5 * a_u * k / cosd(lat)
        b_u, b_v = cur.uv(mla, mlo, ms + direction * dt * 500)
        nla, nlo = lat + b_v * k, lon + b_u * k / cosd(mla)
        if K > 0:
            nla = nla + rng.standard_normal(P) * sig / M_PER_DEG
            nlo = nlo + rng.standard_normal(P) * sig / (M_PER_DEG * cosd(nla))
        off = live & ((nla < lat0) | (nla > lat1) | (nlo < lon0) | (nlo > lon1))
        move = live & ~off
        lat, lon = np.where(move, nla, lat), np.where(move, nlo, lon)
        ashore = move & land(lat, lon)
        end[off], end[ashore] = 2, 1
        end_h[off | ashore] = s
        out_la[s], out_lo[s] = lat, lon
    return out_la, out_lo, end, end_h


def spread(lat, lon, keep):
    """Mean and covariance (km) of the kept members of one ensemble; None under 10 members."""
    la, lo = lat[keep], lon[keep]
    if la.size < 10:
        return None
    mla, mlo = la.mean(), lo.mean()
    x = (lo - mlo) * M_PER_DEG / 1000 * math.cos(math.radians(mla))
    y = (la - mla) * M_PER_DEG / 1000
    C = np.cov(np.vstack([x, y]))                                    # n - 1 in the denominator, as drift.js
    return mla, mlo, C


def inside_90(sp, lat, lon):
    """Whether (lat, lon) lies inside the 90 % ellipse of a spread()."""
    mla, mlo, C = sp
    d = np.array([(lon - mlo) * M_PER_DEG / 1000 * math.cos(math.radians(mla)), (lat - mla) * M_PER_DEG / 1000])
    try:
        return float(d @ np.linalg.solve(C, d)) <= P90 ** 2
    except np.linalg.LinAlgError:
        return False


def km(la1, lo1, la2, lo2):
    """Great-circle distance in km (as geo.js haversineKm)."""
    r = np.radians
    h = np.sin(r(la2 - la1) / 2) ** 2 + np.cos(r(la1)) * np.cos(r(la2)) * np.sin(r(lo2 - lo1) / 2) ** 2
    return 2 * 6371 * np.arcsin(np.sqrt(h))
