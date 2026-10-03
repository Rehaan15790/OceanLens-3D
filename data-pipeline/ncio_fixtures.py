"""Small synthetic NetCDF-3 files for import tests of src/js/import/formats.js (made up, not measurements).

    from ncio_fixtures import ego_glider_bytes, cf_profiles_bytes
    fx = ego_glider_bytes()      # {'data', 'filename', 'format', 'type', 'profiles', 'first_id', 'first_z',
                                 #  'first_temp', 'warnings'}

first_z / first_temp are what the reader should hand over for the first profile: depth in m and temperature in
°C, None where the value is missing (NaN in JS). Compare temperatures within 1e-4. 'warnings' lists substrings
expected among the dataset warnings. Everything lies at 14.5–15.5° N, 87.5–88.5° E on 15–16 May 2020.
"""
import math
from datetime import datetime, timezone

import numpy as np

import ncio


def depth_from_pressure(p, lat):
    """Saunders (1981), as depthFromPressure in src/js/plugins.js."""
    s = math.sin(math.radians(lat))
    return (1 - (5.92 + 5.25 * s * s) * 1e-3) * p - 2.21e-6 * p * p


def _f4(v):
    return None if v is None or not np.isfinite(v) else float(np.float32(v))


def _temp(z, shift=0.0):
    return 29.5 - 15.0 / (1 + math.exp(-(z - 90 - shift) / 25))


def ego_glider_bytes():
    """An EGO glider time series: a surface interval, then descent, ascent and descent (PHASE 1/4, PHASE_NUMBER
    0/1/2), a sample every 2 m to 200 m. TEMP, PSAL and CHLA (no CHLA_ADJUSTED, so a chlorophyll warning) with
    byte QC flags; one TEMP sample in the second segment is flagged 4 and must be dropped."""
    t0 = datetime(2020, 5, 15, 6, tzinfo=timezone.utc).timestamp()
    pres, phase, phno = [], [], []
    for p in (0.4, 0.5, 0.4):                                 # surface: PHASE 0, never a profile
        pres.append(p); phase.append(0); phno.append(-1)
    # pressures whose depths fall on whole metres (2, 4 ... 200 m), so each 1 m bin holds one sample
    c1 = (5.92 + 5.25 * math.sin(math.radians(15.03)) ** 2) * 1e-3
    p_at = lambda d: round(((1 - c1) - math.sqrt((1 - c1) ** 2 - 4 * 2.21e-6 * d)) / (2 * 2.21e-6), 3)
    down = [p_at(d) for d in range(2, 201, 2)]
    up = down[::-1]
    for n, seg in enumerate((down, up, down)):
        for p in seg:
            pres.append(float(p)); phase.append(1 if n != 1 else 4); phno.append(n)
    n = len(pres)
    k = np.arange(n)
    time = t0 + 20.0 * k
    lat = 15.0 + 0.0005 * k
    lon = 88.0 + 0.001 * k
    temp = np.array([_temp(p, 8 * max(ph, 0)) for p, ph in zip(pres, phno)])
    psal = 33.2 + 1.8 * (1 - np.exp(-np.array(pres) / 40))
    chla = 0.08 + 0.5 * np.exp(-((np.array(pres) - 60) / 20) ** 2)
    tqc = np.ones(n, 'i1')
    bad = 3 + len(down) + 10                                  # segment 1 (ascent), 11th sample
    temp[bad], tqc[bad] = 45.0, 4
    ones = np.ones(n, 'i1')
    f4 = lambda units, sn, data, fill=99999.0: {'dims': ('TIME',), 'dtype': 'f4', 'data': data,
                                                'attrs': {'units': units, 'standard_name': sn,
                                                          '_FillValue': np.float32(fill)}}
    V = {
        'TIME': {'dims': ('TIME',), 'dtype': 'f8', 'data': time,
                 'attrs': {'standard_name': 'time', 'units': 'seconds since 1970-01-01T00:00:00Z', 'axis': 'T'}},
        'LATITUDE': {'dims': ('TIME',), 'dtype': 'f8', 'data': lat,
                     'attrs': {'standard_name': 'latitude', 'units': 'degree_north', '_FillValue': 99999.0}},
        'LONGITUDE': {'dims': ('TIME',), 'dtype': 'f8', 'data': lon,
                      'attrs': {'standard_name': 'longitude', 'units': 'degree_east', '_FillValue': 99999.0}},
        'PRES': f4('decibar', 'sea_water_pressure', np.array(pres)),
        'TEMP': f4('degree_Celsius', 'sea_water_temperature', temp),
        'PSAL': f4('psu', 'sea_water_practical_salinity', psal),
        'CHLA': f4('mg/m3', 'mass_concentration_of_chlorophyll_a_in_sea_water', chla),
        'PHASE': {'dims': ('TIME',), 'dtype': 'i1', 'data': np.array(phase, 'i1'), 'attrs': {'_FillValue': np.int8(-128)}},
        'PHASE_NUMBER': {'dims': ('TIME',), 'dtype': 'i4', 'data': np.array(phno, 'i4'), 'attrs': {}},
    }
    for nm, q in (('PRES', ones), ('TEMP', tqc), ('PSAL', ones), ('CHLA', ones)):
        V[nm + '_QC'] = {'dims': ('TIME',), 'dtype': 'i1', 'data': q, 'attrs': {'_FillValue': np.int8(-128)}}
    attrs = {'data_type': 'EGO glider time-series data', 'format_version': '1.4', 'platform_code': 'SYN-GLIDER-01',
             'Conventions': 'CF-1.6 EGO-1.4', 'comment': 'Synthetic test file made by data-pipeline/ncio_fixtures.py'}
    data = ncio.write_bytes({'TIME': n}, V, attrs, 'CDF-1')

    # what the reader should produce for segment 0: 1 m depth bins at the segment's mean latitude
    ks = [i for i in range(n) if phno[i] == 0]
    la = float(np.mean(lat[ks]))
    z = [depth_from_pressure(pres[i], la) for i in ks]
    bins = [round(x) for x in z]
    assert len(set(bins)) == len(bins) and max(abs(x - round(x)) for x in z) < 0.45, 'bins must hold one sample each'
    return {'data': data, 'filename': 'syn-ego-glider.nc', 'format': 'ego_glider', 'type': 'glider', 'profiles': 3,
            'first_id': 'SYN-GLIDER-01', 'first_z': [float(b) for b in bins],
            'first_temp': [_f4(temp[i]) for i in ks], 'warnings': ['Chlorophyll values are unadjusted']}


def cf_profiles_bytes():
    """Two CF profiles on a shared depth axis (profile x depth). Temperature is Int16 with scale_factor and
    add_offset and a _FillValue gap at 30 m in the first profile; salinity float. The second profile ends at
    300 m (fill below), so its deeper levels are dropped."""
    depth = np.array([0, 5, 10, 20, 30, 50, 75, 100, 150, 200, 300, 500], 'f8')
    tm = np.array([(datetime(2020, 5, 16, h, tzinfo=timezone.utc) - datetime(1950, 1, 1, tzinfo=timezone.utc))
                   .total_seconds() / 86400 for h in (0, 12)])
    temp = np.array([[_temp(z) for z in depth], [_temp(z, -10) for z in depth]])
    sal = np.array([33.0 + 2 * (1 - np.exp(-depth / 50)), 33.4 + 1.6 * (1 - np.exp(-depth / 60))])
    temp[0, 4] = np.nan
    temp[1, 11] = sal[1, 11] = np.nan
    V = {
        'time': {'dims': ('profile',), 'data': tm,
                 'attrs': {'standard_name': 'time', 'units': 'days since 1950-01-01 00:00:00', 'calendar': 'standard'}},
        'lat': {'dims': ('profile',), 'data': np.array([14.5, 15.5]),
                'attrs': {'standard_name': 'latitude', 'units': 'degrees_north'}},
        'lon': {'dims': ('profile',), 'data': np.array([87.5, 88.5]),
                'attrs': {'standard_name': 'longitude', 'units': 'degrees_east'}},
        'depth': {'dims': ('depth',), 'data': depth,
                  'attrs': {'standard_name': 'depth', 'units': 'm', 'positive': 'down', 'axis': 'Z'}},
        'temp': {'dims': ('profile', 'depth'), 'dtype': 'i2', 'data': temp,
                 'attrs': {'standard_name': 'sea_water_temperature', 'units': 'degree_Celsius', 'scale_factor': 0.001,
                           'add_offset': 20.0, '_FillValue': np.int16(-32767)}},
        'sal': {'dims': ('profile', 'depth'), 'dtype': 'f4', 'data': sal,
                'attrs': {'standard_name': 'sea_water_practical_salinity', 'units': '1', '_FillValue': np.float32(-999.0)}},
    }
    attrs = {'Conventions': 'CF-1.8', 'featureType': 'profile', 'id': 'SYN-CF',
             'comment': 'Synthetic test file made by data-pipeline/ncio_fixtures.py'}
    data = ncio.write_bytes({'profile': 2, 'depth': len(depth)}, V, attrs, 'CDF-2')
    q = np.round((temp[0] - 20.0) / 0.001)
    first = [None if not np.isfinite(x) else float(20.0 + x * 0.001) for x in q]
    return {'data': data, 'filename': 'syn-cf-profiles.nc', 'format': 'cf_generic', 'type': 'other', 'profiles': 2,
            'first_id': 'SYN-CF-1', 'first_z': [float(z) for z in depth], 'first_temp': first, 'warnings': []}


if __name__ == '__main__':
    for f in (ego_glider_bytes, cf_profiles_bytes):
        fx = f()
        d = ncio.read_bytes(fx['data'])
        print(fx['filename'], len(fx['data']), 'bytes,', fx['profiles'], 'profiles, first temps',
              fx['first_temp'][:3], '...', len(fx['first_temp']), 'levels; vars', len(d['vars']))
