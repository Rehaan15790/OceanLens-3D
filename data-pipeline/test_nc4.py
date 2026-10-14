"""Tests of the NetCDF-4 reader (nc4.py) and the Blosc decoder (blosc.py), offline.

    python -m unittest discover -s data-pipeline -p "test_nc4.py" -v

The fixture files come from nc4_fixtures.py, which shares nc4.py's reading of the HDF5 specification, so they show
the reader is consistent and safe, not that it is right. RealGlorys reads a Copernicus Marine native file written by
netCDF-C and HDF5 (from the raw cache) and checks it against the same values in the Copernicus ARCO Zarr store,
decoded independently by blosc.py; it skips when the cache is absent.
"""
import os, random, sys, tempfile, unittest, zlib

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import blosc  # noqa: E402
import build_snapshot as B  # noqa: E402
import nc4  # noqa: E402
import nc4_fixtures as F  # noqa: E402
import ncio  # noqa: E402

STYLES = ('classic', 'latest')


class Counting(nc4.BytesSource):
    """A source that counts what is read."""
    def __init__(self, data):
        super().__init__(data)
        self.reads, self.bytes = 0, 0

    def read(self, offset, n):
        self.reads += 1
        self.bytes += n
        return super().read(offset, n)


class Checksum(unittest.TestCase):
    def test_lookup3_matches_the_reference_vectors(self):
        # the self-test values of Bob Jenkins' lookup3.c
        self.assertEqual(nc4.lookup3(b''), 0xdeadbeef)
        self.assertEqual(nc4.lookup3(b'Four score and seven years ago'), 0x17770551)
        self.assertEqual(nc4.lookup3(b'Four score and seven years ago', 1), 0xcd628161)


class ModelFixture(unittest.TestCase):
    """The CF model fixture in both HDF5 styles: every value, attribute and dimension as written."""

    @classmethod
    def setUpClass(cls):
        cls.dims, cls.vars, cls.attrs, cls.groups = F.model_fixture()
        cls.files = {s: F.write_bytes(cls.dims, cls.vars, cls.attrs, style=s, groups=cls.groups) for s in STYLES}

    def test_dimensions_and_variables(self):
        for s in STYLES:
            ds = ncio.read_bytes(self.files[s])
            self.assertEqual(ds['dims'], {'time': 2, 'depth': 3, 'lat': 4, 'lon': 5, 'nv': 2, 'strlen': 8}, s)
            self.assertEqual(ds['unlimited'], 'time', s)
            self.assertEqual(sorted(ds['vars']), sorted(list(self.vars) + ['extra/flag']), s)
            for name, v in self.vars.items():
                self.assertEqual(ds['vars'][name]['dims'], tuple(v['dims']), f'{s} {name}')
            self.assertEqual(ds['vars']['extra/flag']['dims'], ('lat',))

    def test_values_unpack_as_ncio_does(self):
        for s in STYLES:
            ds = ncio.read_bytes(self.files[s])
            want = self.vars['thetao']['data']
            got = ds['vars']['thetao']['data']
            np.testing.assert_array_equal(np.isnan(got), np.isnan(want), s)
            np.testing.assert_allclose(got[~np.isnan(got)], want[~np.isnan(want)], atol=0.0006, err_msg=s)
            sal = ds['vars']['so']['data']
            np.testing.assert_array_equal(np.isnan(sal), np.isnan(self.vars['so']['data']), s)
            np.testing.assert_allclose(sal[~np.isnan(sal)], self.vars['so']['data'][~np.isnan(sal)], err_msg=s)
            for c in ('time', 'depth', 'lat', 'lon', 'lat_bnds'):
                np.testing.assert_allclose(ds['vars'][c]['data'], self.vars[c]['data'], err_msg=f'{s} {c}')
            self.assertEqual(ds['vars']['platform']['data'].tolist(), ['GLORYS', 'HYCOM-X'], s)
            self.assertEqual(ds['vars']['note']['data'].tolist(), ['first day', 'second, ünïcode'], s)
            self.assertEqual(ds['vars']['extra/flag']['data'].tolist(), [1, 0, 1, 1], s)

    def test_raw_values_and_the_unallocated_chunk(self):
        for s in STYLES:
            raw = ncio.read_bytes(self.files[s], raw=True)['vars']['thetao']['data']
            self.assertEqual(raw.dtype, np.int16, s)
            self.assertTrue((raw[1, 2, 2:, 3:] == -32767).all(), s)        # never written: the fill value
            self.assertEqual(int(raw[0, 0, 1, 0]), int(round((self.vars['thetao']['data'][0, 0, 1, 0] - 20) / 0.001)))

    def test_attributes(self):
        for s in STYLES:
            ds = ncio.read_bytes(self.files[s])
            g = ds['attrs']
            self.assertEqual(g['title'], 'nc4 fixture')
            self.assertEqual(g['history'], 'made by nc4_fixtures.py')
            self.assertEqual(g['keywords'].tolist(), ['ocean', 'test'])
            self.assertEqual(g['level_count'], 3)
            np.testing.assert_array_equal(g['resolution'], [2.0, 2.0])
            self.assertNotIn('_NCProperties', g)
            a = ds['vars']['thetao']['attrs']
            self.assertEqual(a['scale_factor'], 0.001)
            self.assertEqual(a['_FillValue'], -32767)
            self.assertEqual(a['units'], 'degrees_C')
            for hidden in ('CLASS', 'NAME', 'DIMENSION_LIST', '_Netcdf4Dimid', 'REFERENCE_LIST'):
                self.assertNotIn(hidden, a)
                self.assertNotIn(hidden, ds['vars']['lat']['attrs'])
            self.assertEqual(ds['vars']['lat']['attrs']['bounds'], 'lat_bnds')

    def test_cf_helpers_work_on_netcdf4(self):
        ds = ncio.read_bytes(self.files['classic'])
        self.assertEqual(ncio.cf_find(ds, ['sea_water_potential_temperature']), 'thetao')
        self.assertEqual(ncio.cf_axis(ds, 'T', 'thetao'), 'time')
        self.assertEqual([t.strftime('%d %H') for t in ncio.cf_times(ds['vars']['time'])], ['13 12', '14 12'])

    def test_a_selection_reads_only_the_chunks_it_needs(self):
        for s in STYLES:
            whole = ncio.read_bytes(self.files[s])['vars']['thetao']['data']
            src = Counting(self.files[s])
            f = nc4.open_source(src)
            f.vars['thetao'].read((0, 0, 0, 0))                          # reads the chunk index once
            before = src.bytes
            part = f.vars['thetao'].read((1, slice(0, 2), slice(0, 2), slice(0, 3)))
            np.testing.assert_array_equal(part, whole[1, 0:2, 0:2, 0:3], s)
            one = src.bytes - before
            f.vars['thetao'].read()
            self.assertLess(one * 5, src.bytes - before - one, f'{s}: one chunk of the 20 stored')
            np.testing.assert_array_equal(f.vars['thetao'].read((slice(None), 2, 3)), whole[:, 2, 3], s)

    def test_a_v1_btree_with_internal_nodes(self):
        a = np.arange(30 * 40, dtype='f8').reshape(30, 40)
        data = F.write_bytes({'y': 30, 'x': 40}, {'v': {'dims': ('y', 'x'), 'data': a, 'chunks': (3, 4), 'deflate': 1,
                                                       'btree_leaf': 4, 'attrs': {}}})
        self.assertGreater(data.count(b'TREE'), 20)                       # 100 chunks in a tree three levels deep
        np.testing.assert_array_equal(ncio.read_bytes(data)['vars']['v']['data'], a)

    def test_files_on_disk(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, 'f.nc')
            with open(p, 'wb') as fh:
                fh.write(self.files['latest'])
            self.assertEqual(ncio.read(p)['dims']['lon'], 5)
            f = nc4.open_source(p)
            self.assertEqual(f.vars['lon'].read().tolist(), [80, 82, 84, 86, 88])
            f.s.src.close()


class LatestFormat(unittest.TestCase):
    """Every version 4 chunk index, dense attributes and links, compact data, a continuation block."""

    def test_every_variable_reads_back(self):
        dims, vars, attrs, kw = F.latest_fixture()
        data = F.write_bytes(dims, vars, attrs, **kw)
        for marker in (b'FAHD', b'EAHD', b'EADB', b'BTHD', b'FRHP', b'FHIB', b'OCHK'):
            self.assertIn(marker, data)
        ds = ncio.read_bytes(data)
        for name, v in vars.items():
            got = ds['vars'][name]['data']
            np.testing.assert_array_equal(np.asarray(got, np.asarray(v['data']).dtype), v['data'], err_msg=name)
            want = {k: x for k, x in v['attrs'].items()}
            self.assertEqual(set(ds['vars'][name]['attrs']), set(want), name)
            for k, x in want.items():
                self.assertEqual(ds['vars'][name]['attrs'][k], x, f'{name}.{k}')
        self.assertEqual(ds['attrs']['title'], 'latest-format fixture')
        self.assertEqual(ds['unlimited'], 't')
        self.assertEqual(ds['dims']['t'], 10)


class ModelThroughNetcdf4(unittest.TestCase):
    """--model-netcdf gives the same snapshot fields from NetCDF-4 as from NetCDF-3."""

    def test_same_fields_from_both_formats(self):
        from test_build_snapshot import ModelNetcdf
        with tempfile.TemporaryDirectory() as d:
            p3 = os.path.join(d, 'model3.nc')
            ModelNetcdf().write(p3)
            ds = ncio.read(p3, raw=True)
            vars = {}
            for name, v in ds['vars'].items():
                spec = {'dims': v['dims'], 'attrs': v['attrs'], 'data': v['data']}
                if len(v['dims']) == 4:
                    spec.update(chunks=(1, 4, 20, 40), deflate=5)
                vars[name] = spec
            dims = {k: (None if k == ds['unlimited'] else n) for k, n in ds['dims'].items()}
            p4 = os.path.join(d, 'model4.nc')
            for style in STYLES:
                with open(p4, 'wb') as fh:
                    fh.write(F.write_bytes(dims, vars, ds['attrs'], style=style))
                m3, w3, t3, _ = B.model_from_netcdf(p3, log=lambda *a: None)
                m4, w4, t4, info = B.model_from_netcdf(p4, log=lambda *a: None)
                np.testing.assert_array_equal(m4, m3, style)
                np.testing.assert_array_equal(w4, w3, style)
                self.assertEqual(t4, t3, style)
                self.assertEqual(info['variables'], {'temp': 'thetao', 'sal': 'so', 'u': 'uo', 'v': 'vo'})


class Hostile(unittest.TestCase):
    """Damaged and hostile files raise NetCDFError and nothing else, quickly, without large allocations."""

    def assert_only_netcdf_errors(self, data):
        try:
            ncio.read_bytes(data)
        except ncio.NetCDFError:
            pass

    def test_truncated_files(self):
        for s in STYLES:
            dims, vars, attrs, groups = F.model_fixture()
            data = F.write_bytes(dims, vars, attrs, style=s, groups=groups)
            for cut in list(range(0, 200, 7)) + list(range(200, len(data), 389)):
                with self.subTest(style=s, cut=cut):
                    self.assert_only_netcdf_errors(data[:cut])

    def test_flipped_bytes(self):
        rng = random.Random(11)
        for s in STYLES:
            dims, vars, attrs, kw = F.latest_fixture() if s == 'latest' else F.model_fixture()[:3] + ({},)
            data = bytearray(F.write_bytes(dims, vars, attrs, **kw))
            for k in range(300):
                bad = bytearray(data)
                for _ in range(3):
                    bad[rng.randrange(len(bad))] = rng.randrange(256)
                with self.subTest(style=s, k=k):
                    self.assert_only_netcdf_errors(bytes(bad))

    def test_a_decompression_bomb_is_refused_without_inflating_it(self):
        bomb = zlib.compress(b'\x00' * (64 << 20), 9)                      # 64 MiB of zeros in about 64 KB
        data = F.write_bytes({'x': 1000}, {'v': {'dims': ('x',), 'dtype': 'i1', 'data': np.zeros(1000, 'i1'),
                                                 'chunks': (1000,), 'deflate': 1, '_raw_chunks': {(0,): bomb},
                                                 'attrs': {}}})
        with self.assertRaisesRegex(ncio.NetCDFError, 'inflates past'):
            ncio.read_bytes(data)

    def test_declared_sizes_cannot_demand_gigabytes(self):
        for dims in ([1 << 20, 1 << 20], [1 << 40], [(1 << 62) + 3]):
            data = F.write_bytes({'x': 4}, {'v': {'dims': ('x',), 'data': np.zeros(4), 'attrs': {}, '_dims': dims}})
            with self.assertRaises(ncio.NetCDFError, msg=str(dims)):
                ncio.read_bytes(data)

    def test_unsupported_filters_are_named(self):
        data = F.write_bytes({'x': 8}, {'v': {'dims': ('x',), 'data': np.zeros(8), 'chunks': (8,), 'attrs': {},
                                              '_filters': [(32015, 'zstd', [3])]}})
        with self.assertRaisesRegex(ncio.NetCDFError, 'filter 32015'):
            ncio.read_bytes(data)

    def test_a_btree_loop_is_refused(self):
        a = np.arange(30 * 40, dtype='f8').reshape(30, 40)
        data = bytearray(F.write_bytes({'y': 30, 'x': 40}, {'v': {'dims': ('y', 'x'), 'data': a, 'chunks': (3, 4),
                                                                  'btree_leaf': 4, 'attrs': {}}}))
        # an internal node (level 1 or more) whose first child is the node itself
        k = 0
        while True:
            k = data.index(b'TREE', k + 1)
            if data[k + 4] == 1 and data[k + 5] >= 1:
                break
        first_child = k + 8 + 16 + 8 + 8 * 3                              # after the header and the first key
        data[first_child:first_child + 8] = k.to_bytes(8, 'little')
        with self.assertRaisesRegex(ncio.NetCDFError, 'loop'):
            ncio.read_bytes(bytes(data))

    def test_a_continuation_loop_is_refused(self):
        data = bytearray(F.write_bytes({'x': 3}, {'v': {'dims': ('x',), 'data': np.arange(3.0), 'continuation': True,
                                                        'attrs': {'units': 'm', 'long_name': 'x'}}}))
        # the continuation message (type 0x10, 16 bytes) in v's header points at a block; make that block's first
        # message a continuation back to itself
        pat = b'\x10\x00\x10\x00\x00\x00\x00\x00'
        p = data.index(pat)
        addr = int.from_bytes(data[p + 8:p + 16], 'little')
        size = int.from_bytes(data[p + 16:p + 24], 'little')
        first = int.from_bytes(data[addr + 2:addr + 4], 'little')         # the block's first message size
        data[addr:addr + 8] = (0x10).to_bytes(2, 'little') + first.to_bytes(2, 'little') + b'\x00' * 4
        data[addr + 8:addr + 24] = addr.to_bytes(8, 'little') + size.to_bytes(8, 'little')
        with self.assertRaisesRegex(ncio.NetCDFError, 'loop'):
            ncio.read_bytes(bytes(data))

    def test_not_hdf5(self):
        for data in (b'\x89HDF\r\n\x1a\n', b'\x89HDF\r\n\x1a\n' + b'\x00' * 32, b'\x89HDF\r\n\x1a\n' + b'\xff' * 200):
            with self.assertRaises(ncio.NetCDFError):
                ncio.read_bytes(data)


class Blosc(unittest.TestCase):
    """blosc.py on hand-made frames (the real ones are in RealGlorys)."""

    @staticmethod
    def lz4_literals(b):
        """An LZ4 block of literals only (valid, if not compressed)."""
        n = len(b)
        if n < 15:
            return bytes([n << 4]) + b
        rest, ext = n - 15, b''
        while rest >= 255:
            ext += b'\xff'
            rest -= 255
        return bytes([0xf0]) + ext + bytes([rest]) + b

    def frame(self, payload, typesize=2, shuffle=True):
        data = np.frombuffer(payload, np.uint8)
        sh = data.reshape(-1, typesize).T.tobytes() if shuffle else payload
        c = self.lz4_literals(sh)
        flags = (1 << 5) | (0x01 if shuffle else 0)
        body = len(c).to_bytes(4, 'little') + c
        return (bytes([2, 1, flags, typesize]) + len(payload).to_bytes(4, 'little') + len(payload).to_bytes(4, 'little') +
                (16 + 4 + len(body)).to_bytes(4, 'little') + (20).to_bytes(4, 'little') + body)

    def test_literals_matches_and_shuffle(self):
        payload = np.arange(500, dtype='<i2').tobytes()
        self.assertEqual(blosc.decompress(self.frame(payload)), payload)
        # a match that overlaps its own output: 'ab' then copy 10 bytes from offset 2 -> 'abababababab'
        block = bytes([0x26]) + b'ab' + (2).to_bytes(2, 'little')
        self.assertEqual(blosc.lz4_block(block, 12), b'ab' * 6)

    def test_hostile_frames(self):
        good = self.frame(np.arange(100, dtype='<i2').tobytes())
        for bad in (good[:10], good[:-3], good[:4] + (0x7fffffff).to_bytes(4, 'little') + good[8:],
                    good[:2] + bytes([good[2] & 0x1f]) + good[3:]):
            with self.assertRaises(blosc.BloscError):
                blosc.decompress(bad)
        with self.assertRaises(blosc.BloscError):
            blosc.lz4_block(bytes([0x0f]) + (9).to_bytes(2, 'little'), 100)   # a match before the start


class RealGlorys(unittest.TestCase):
    """A Copernicus Marine GLORYS12V1 native file (NetCDF-4 as netCDF-C and HDF5 wrote it), read from the raw cache,
    against the same values in the Copernicus ARCO Zarr store decoded by blosc.py."""

    @classmethod
    def setUpClass(cls):
        cls.cache = os.environ.get('OCEANLENS_CACHE') or os.path.join(HERE, 'cache')
        if not hasattr(B, 'glorys_check_files'):
            raise unittest.SkipTest('the pipeline has no GLORYS step yet')
        cls.files = B.glorys_check_files(cls.cache)
        if cls.files is None:
            raise unittest.SkipTest('no GLORYS files in the raw cache')

    def test_native_netcdf4_equals_the_zarr_store(self):
        native, zarr_chunks = self.files
        for (var, level), want in zarr_chunks.items():
            got = native(var, level)
            np.testing.assert_array_equal(got, want, f'{var} level {level}')


if __name__ == '__main__':
    unittest.main()
