"""The optional xarray engine (ncio.read(path, engine='xarray'), --engine xarray in build_snapshot.py and serve.py)
gives what the project's own readers give. Skipped when xarray and its NetCDF backends are not installed:

    pip install xarray scipy h5netcdf     (h5netcdf brings h5py)
"""
import os, sys, tempfile, unittest

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import build_snapshot as B  # noqa: E402
import nc4_fixtures  # noqa: E402
import ncio  # noqa: E402
import test_build_snapshot  # noqa: E402  (its ModelNetcdf fixture; imported as a module so its tests do not run twice)

try:
    import xarray  # noqa: F401
    import scipy  # noqa: F401
    import h5netcdf  # noqa: F401
    import h5py  # noqa: F401
    HAVE = True
except ImportError:
    HAVE = False


def classic_fixture(path):
    """NetCDF-3 with what read() unpacks: packed int16 with scale, offset and fill, a float fill, a valid range,
    a record dimension, CF time and a char variable."""
    T = np.array([[20.0, 21.5, np.nan], [22.25, 23.0, 24.0]])
    ncio.write(path, {'time': None, 'x': 3, 'n': 4}, {
        'time': {'dims': ('time',), 'attrs': {'standard_name': 'time', 'units': 'days since 2020-05-01 00:00:00'},
                 'data': np.array([3.5, 6.5])},
        'temp': {'dims': ('time', 'x'), 'dtype': 'i2', 'data': T,
                 'attrs': {'scale_factor': 0.01, 'add_offset': 15.0, '_FillValue': np.int16(-32767), 'units': 'degC'}},
        'sal': {'dims': ('time', 'x'), 'dtype': 'f4', 'data': np.array([[33.0, 99.0, 34.0], [35.0, 36.0, -1.0]]),
                'attrs': {'valid_range': np.array([0.0, 45.0], 'f4'), '_FillValue': np.float32(-999)}},
        'name': {'dims': ('n',), 'dtype': 'S1', 'data': np.array(list('ab  '), 'S1')},
    }, {'title': 'xarray check'})


@unittest.skipUnless(HAVE, 'xarray, scipy and h5netcdf are not installed (pip install xarray scipy h5netcdf)')  # h5netcdf brings h5py
class XarrayEngine(unittest.TestCase):
    def same(self, path):
        a, b = ncio.read(path), ncio.read(path, engine='xarray')
        self.assertEqual(a['attrs'].get('title'), b['attrs'].get('title'))
        for name, v in a['vars'].items():
            w = b['vars'][name]
            x, y = np.asarray(v['data']), np.asarray(w['data'])
            if x.dtype.kind in 'SUO':
                self.assertEqual(x.tolist(), y.tolist(), name)
                continue
            self.assertEqual(tuple(v['dims']), tuple(w['dims']), name)
            np.testing.assert_allclose(y.astype('f8'), x.astype('f8'), rtol=1e-6, atol=1e-6, equal_nan=True, err_msg=name)
            for k in ('units', 'standard_name', 'scale_factor', 'add_offset'):
                if k in v['attrs']:
                    self.assertIn(k, w['attrs'], f'{name}: {k}')
        return a, b

    def test_netcdf3_unpacking_and_ranges(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, 'c.nc')
            classic_fixture(p)
            a, b = self.same(p)
        self.assertTrue(np.isnan(b['vars']['sal']['data'][0, 1]) and np.isnan(b['vars']['sal']['data'][1, 2]),
                        'values outside valid_range become NaN, as read() does')
        self.assertEqual(b['unlimited'], 'time')
        self.assertEqual(ncio.cf_times(b['vars']['time']), ncio.cf_times(a['vars']['time']))

    def test_netcdf4_model_file(self):
        dims, vars_, attrs, groups = nc4_fixtures.model_fixture()
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, 'm4.nc')
            with open(p, 'wb') as f:
                f.write(nc4_fixtures.write_bytes(dims, vars_, attrs, groups=groups))
            self.same(p)

    def test_the_pipeline_and_the_server_give_the_same_model(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, 'model.nc')
            test_build_snapshot.ModelNetcdf.write(test_build_snapshot.ModelNetcdf(), p)
            native = B.model_from_netcdf(p, log=lambda *a: None)
            viax = B.model_from_netcdf(p, log=lambda *a: None, engine='xarray')
            np.testing.assert_allclose(viax[0], native[0], rtol=1e-6, atol=1e-6)
            self.assertTrue(np.array_equal(viax[1], native[1]))
            self.assertEqual(viax[2], native[2])
            sys.path.insert(0, os.path.join(HERE, '..', 'server'))
            import serve
            a, b = serve.load_netcdf(p), serve.load_netcdf(p, engine='xarray')
        self.assertEqual(sorted(a.fields), sorted(b.fields))
        for k in a.fields:
            np.testing.assert_allclose(b.fields[k], a.fields[k], rtol=1e-6, atol=1e-6, equal_nan=True, err_msg=k)
        self.assertEqual(a.times, b.times)


class EngineChoice(unittest.TestCase):
    def test_unknown_engine_is_refused(self):
        with self.assertRaises(ValueError):
            ncio.read(__file__, engine='pynio')


if __name__ == '__main__':
    unittest.main()
