"""server/serve.py: REST shapes, WMS 1.3.0 (valid PNG, colour-correct at a known node, transparent land),
WCS 2.0.1 (CF NetCDF-3 round trip through data-pipeline/ncio.py, CSV) and a CF NetCDF model file as a dataset.

No browser. Expected values come from the snapshot and src/js/palettes.js decoded here, independently of the server.
"""
import base64, gzip, http.client, json, re, shutil, struct, sys, threading, unittest, zlib
import xml.etree.ElementTree as ET
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / 'server'))
sys.path.insert(0, str(REPO / 'data-pipeline'))
import ncio                     # noqa: E402
import serve                    # noqa: E402

SNAP = REPO / 'data' / 'amphan-2020.json'
WORK = REPO / 'tests' / 'output' / 'server'
NS = {'wms': 'http://www.opengis.net/wms', 'ogc': 'http://www.opengis.net/ogc', 'wcs': 'http://www.opengis.net/wcs/2.0',
      'ows': 'http://www.opengis.net/ows/2.0', 'gml': 'http://www.opengis.net/gml/3.2'}


def read_png(b):
    """PNG -> (h, w, 4) uint8, checking the signature, every chunk CRC, 8-bit RGBA and filter 0."""
    assert b[:8] == b'\x89PNG\r\n\x1a\n', 'not a PNG signature'
    pos, idat, ihdr = 8, b'', None
    while pos < len(b):
        n = struct.unpack('>I', b[pos:pos + 4])[0]
        tag, data = b[pos + 4:pos + 8], b[pos + 8:pos + 8 + n]
        assert zlib.crc32(tag + data) & 0xffffffff == struct.unpack('>I', b[pos + 8 + n:pos + 12 + n])[0], f'bad CRC in {tag}'
        if tag == b'IHDR':
            ihdr = struct.unpack('>IIBBBBB', data)
        elif tag == b'IDAT':
            idat += data
        pos += 12 + n
    w, h, depth, ctype = ihdr[:4]
    assert (depth, ctype) == (8, 6), 'not 8-bit RGBA'
    rows = np.frombuffer(zlib.decompress(idat), np.uint8).reshape(h, w * 4 + 1)
    assert (rows[:, 0] == 0).all()
    return rows[:, 1:].reshape(h, w, 4)


def palette(name):
    src = (REPO / 'src' / 'js' / 'palettes.js').read_text(encoding='utf-8')
    m = re.search(name + r":\s*\{label:'[^']*',\s*lut:'([0-9a-f]+)'", src)
    return np.frombuffer(bytes.fromhex(m.group(1)), np.uint8).reshape(-1, 3)


def make_cf_file(path):
    """A small CF model file the way an external producer might write it: latitude descending, potential
    temperature, days since an epoch, one land cell."""
    lat = np.array([12.0, 11.0, 10.0, 9.0])
    lon = np.array([80.0, 81.0, 82.0, 83.0, 84.0])
    depth = np.array([0.5, 10.0, 30.0])
    t = np.array([16.5, 17.5])                                   # 2020-05-17T12:00Z, 2020-05-18T12:00Z
    temp = (25 + np.arange(2 * 3 * 4 * 5).reshape(2, 3, 4, 5) * 0.01).astype(np.float32)
    temp[:, :, 0, 0] = np.nan
    ncio.write(str(path), {'time': None, 'depth': 3, 'lat': 4, 'lon': 5}, {
        'time': {'dims': ('time',), 'data': t, 'attrs': {'standard_name': 'time', 'units': 'days since 2020-05-01 00:00:00'}},
        'depth': {'dims': ('depth',), 'data': depth, 'attrs': {'standard_name': 'depth', 'units': 'm', 'positive': 'down'}},
        'lat': {'dims': ('lat',), 'data': lat, 'attrs': {'standard_name': 'latitude', 'units': 'degrees_north'}},
        'lon': {'dims': ('lon',), 'data': lon, 'attrs': {'standard_name': 'longitude', 'units': 'degrees_east'}},
        'thetao': {'dims': ('time', 'depth', 'lat', 'lon'), 'dtype': 'f4', 'data': temp,
                   'attrs': {'standard_name': 'sea_water_potential_temperature', 'units': 'degC', '_FillValue': np.float32(-999)}},
    }, {'Conventions': 'CF-1.8', 'title': 'Test model'})
    return lat, lon, depth, temp


class ServerTest(unittest.TestCase):
    longMessage = False

    @classmethod
    def setUpClass(cls):
        WORK.mkdir(parents=True, exist_ok=True)
        cls.cf = make_cf_file(WORK / 'cf-model.nc')
        shutil.copyfile(SNAP, WORK / 'copy-2020.json')          # a second snapshot, for the ?dataset= page
        app = next((p for p in (REPO / 'dist' / 'platform.html', REPO / 'dist' / 'OceanLens3D.html',
                                REPO / 'OceanLens3D.html') if p.exists()), None)
        cls.srv = serve.make_server('127.0.0.1', 0, [SNAP, WORK / 'copy-2020.json'], [WORK / 'cf-model.nc'],
                                    app=app or '', quiet=True)
        cls.base = f'http://127.0.0.1:{cls.srv.server_address[1]}'
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        d = json.loads(SNAP.read_bytes())
        m = d['meta']
        cls.meta = m
        cls.q = np.frombuffer(base64.b64decode(d['model']), '<i2').reshape(
            len(m['times']), len(m['vars']), len(m['depths']), m['nlat'], m['nlon'])
        bits = np.unpackbits(np.frombuffer(base64.b64decode(d['mask']['wet']), np.uint8))
        cls.wet = bits[:len(m['depths']) * m['nlat'] * m['nlon']].reshape(len(m['depths']), m['nlat'], m['nlon']).astype(bool)
        # a node wet in its whole 3x3 neighbourhood at 0, 20 and 50 m, and an inland node dry in its neighbourhood
        W = cls.wet[0] & cls.wet[1] & cls.wet[2]
        core = W[:-2, :-2] & W[:-2, 1:-1] & W[:-2, 2:] & W[1:-1, :-2] & W[1:-1, 1:-1] & W[1:-1, 2:] & W[2:, :-2] & W[2:, 1:-1] & W[2:, 2:]
        i, j = np.argwhere(core)[len(np.argwhere(core)) // 2] + 1
        cls.node = int(i), int(j)
        D = ~cls.wet[0]
        dry = D[:-2, :-2] & D[:-2, 2:] & D[2:, :-2] & D[2:, 2:] & D[1:-1, 1:-1]
        cls.land = tuple(int(x) + 1 for x in np.argwhere(dry)[0])
        cls.app = app

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()

    def get(self, path, headers=None, method='GET'):
        """(status, headers, body). gzip and keep-alive, as browsers do: some Windows antivirus proxies stall the
        tail of a large response on a loopback connection the server closes ("Connection: close")."""
        c = http.client.HTTPConnection('127.0.0.1', self.srv.server_address[1], timeout=60)
        try:
            c.request(method, path, headers={'Accept-Encoding': 'gzip', **(headers or {})})
            r = c.getresponse()
            s, h, b = r.status, r.headers, r.read()
        finally:
            c.close()
        return s, h, gzip.decompress(b) if h.get('Content-Encoding') == 'gzip' else b

    def json(self, path, status=200):
        s, h, b = self.get(path)
        self.assertEqual(s, status, f'{path}: HTTP {s}: {b[:300]!r}')
        self.assertIn('application/json', h['Content-Type'])
        return json.loads(b)

    def value(self, var, t, k, i, j):
        off, sc = self.meta['quant'][var]
        return off + float(self.q[t, self.meta['vars'].index(var), k, i, j]) * sc

    def lonlat(self, i, j):
        return self.meta['lon0'] + j * self.meta['step'], self.meta['lat0'] + i * self.meta['step']

    # ---- REST
    def test_datasets_list(self):
        d = {x['id']: x for x in self.json('/api/v1/datasets')['datasets']}
        self.assertEqual(set(d), {'amphan-2020', 'copy-2020', 'cf-model'})
        a = d['amphan-2020']
        self.assertTrue(a['default'])
        self.assertFalse(d['copy-2020']['default'])
        self.assertEqual(a['source'], 'snapshot')
        self.assertEqual(len(a['times']), len(self.meta['times']))
        self.assertEqual({v['key'] for v in a['variables']}, {'temp', 'sal', 'u', 'v', 'speed'})
        self.assertEqual(a['bbox'], [61.0, 6.0, 92.0, 22.0])
        self.assertGreater(a['profiles'], 100)
        self.assertIn('snapshot', a['links'])
        self.assertEqual(d['cf-model']['source'], 'netcdf')
        self.assertNotIn('snapshot', d['cf-model']['links'])

    def test_meta(self):
        m = self.json('/api/v1/datasets/amphan-2020/meta')
        self.assertEqual(len(m['grid']['lat']), self.meta['nlat'])
        self.assertEqual(len(m['grid']['lon']), self.meta['nlon'])
        self.assertTrue(m['attributes']['citations'])
        temp = next(v for v in m['variables'] if v['key'] == 'temp')
        self.assertEqual(temp['standard_name'], 'sea_water_temperature')
        self.assertLess(temp['data_range'][0], temp['data_range'][1])

    def test_field_json_matches_snapshot(self):
        i, j = self.node
        f = self.json('/api/v1/datasets/amphan-2020/field?var=temp&time=0&depth=0')
        self.assertEqual((len(f['values']), len(f['values'][0])), (self.meta['nlat'], self.meta['nlon']))
        self.assertAlmostEqual(f['values'][i][j], self.value('temp', 0, 0, i, j), places=3)
        li, lj = self.land
        self.assertIsNone(f['values'][li][lj], 'a cell without model water must be null')
        # ISO time and a depth between levels (20 and 50 m: halfway at 35 m)
        f = self.json('/api/v1/datasets/amphan-2020/field?var=sal&time=2020-05-16T12:00Z&depth=35')
        self.assertEqual(f['time'], '2020-05-16T12:00:00Z')
        want = (self.value('sal', 4, 1, i, j) + self.value('sal', 4, 2, i, j)) / 2
        self.assertAlmostEqual(f['values'][i][j], want, places=3)

    def test_field_bin(self):
        i, j = self.node
        s, h, b = self.get('/api/v1/datasets/amphan-2020/field?var=temp&time=1&depth=20&format=bin')
        self.assertEqual(s, 200)
        ny, nx = (int(x) for x in h['X-OceanLens-Shape'].split(','))
        self.assertEqual(len(b), ny * nx * 2)
        q = np.frombuffer(b, '<i2').reshape(ny, nx)
        v = float(h['X-OceanLens-Offset']) + q[i, j] * float(h['X-OceanLens-Scale'])
        self.assertAlmostEqual(v, self.value('temp', 1, 1, i, j), delta=float(h['X-OceanLens-Scale']))
        self.assertEqual(q[self.land], int(h['X-OceanLens-Missing']))

    def test_column(self):
        i, j = self.node
        lon, lat = self.lonlat(i, j)
        c = self.json(f'/api/v1/datasets/amphan-2020/column?lat={lat}&lon={lon}&time=2')
        self.assertEqual(c['depth'], self.meta['depths'])
        self.assertEqual(len(c['values']['temp']), len(self.meta['depths']))
        self.assertAlmostEqual(c['values']['temp'][0], self.value('temp', 2, 0, i, j), places=3)
        u, v = self.value('u', 2, 0, i, j), self.value('v', 2, 0, i, j)
        self.assertAlmostEqual(c['values']['speed'][0], (u * u + v * v) ** 0.5, places=3)

    def test_profiles_filter(self):
        every = self.json('/api/v1/datasets/amphan-2020/profiles')['profiles']
        p = self.json('/api/v1/datasets/amphan-2020/profiles?bbox=80,10,92,22&start=2020-05-10T00:00Z&end=2020-05-20T00:00Z')
        self.assertTrue(0 < p['count'] < len(every))
        for x in p['profiles']:
            self.assertTrue(80 <= x['lon'] <= 92 and 10 <= x['lat'] <= 22, x['id'])
            self.assertTrue('2020-05-10' <= x['time'] <= '2020-05-20', x['time'])
            self.assertEqual(len(x['depth']), len(x['temp']), x['id'])
            self.assertEqual(x['type'], 'argo')
        self.assertTrue(any('chl' in x for x in every), 'BGC floats carry chlorophyll')

    def test_track(self):
        t = self.json('/api/v1/datasets/amphan-2020/track')
        self.assertEqual(t['fields'][:3], ['time', 'lat', 'lon'])
        self.assertGreater(len(t['fixes']), 10)
        self.json('/api/v1/datasets/cf-model/track', 404)

    def test_errors_are_json(self):
        for path, status, code in [('/api/v1/datasets/nope', 404, 'NoSuchDataset'),
                                   ('/api/v1/datasets/amphan-2020/field?var=xyz', 400, 'InvalidParameterValue'),
                                   ('/api/v1/datasets/amphan-2020/field?var=temp&depth=5000', 400, 'InvalidDimensionValue'),
                                   ('/api/v1/datasets/amphan-2020/field?var=temp&time=2021-01-01', 400, 'InvalidDimensionValue'),
                                   ('/api/v1/datasets/amphan-2020/column?lat=40&lon=80', 400, 'InvalidParameterValue'),
                                   ('/api/v1/datasets/cf-model/snapshot', 404, 'NoSnapshot'),
                                   ('/api/v1/datasets/amphan-2020/profiles?bbox=nan,6,92,22', 400, 'InvalidParameterValue'),
                                   ('/api/v1/datasets/amphan-2020/profiles?bbox=92,6,61,22', 400, 'InvalidParameterValue'),
                                   ('/nothing', 404, 'NotFound')]:
            e = self.json(path, status)['error']
            self.assertEqual(e['code'], code, path)
            self.assertTrue(e['message'])
        self.assertEqual(self.get('/api/v1/datasets', method='POST')[0], 405)
        # DNS rebinding: on a loopback bind only loopback Host names are answered
        self.assertEqual(self.get('/api/v1/datasets', {'Host': 'attacker.example:80'})[0], 421)
        self.assertEqual(self.get('/api/v1/datasets', {'Host': f'localhost:{self.srv.server_address[1]}'})[0], 200)

    def test_heavy_requests_are_capped(self):
        held = 0
        while self.srv.heavy.acquire(blocking=False):
            held += 1
        try:
            s, h, b = self.get('/wms?SERVICE=WMS&VERSION=1.3.0&REQUEST=GetMap&LAYERS=amphan-2020.temp&STYLES=&CRS=CRS:84'
                               '&BBOX=61,6,92,22&WIDTH=10&HEIGHT=10&FORMAT=image/png')
            self.assertEqual(s, 503)
            self.assertEqual(ET.fromstring(b).find('ogc:ServiceException', NS).get('code'), 'ServerBusy')
        finally:
            for _ in range(held):
                self.srv.heavy.release()
        self.assertEqual(held, serve.HEAVY_SLOTS)

    def test_snapshot_and_page(self):
        s, h, b = self.get('/api/v1/datasets/amphan-2020/snapshot')
        self.assertEqual(h['Content-Encoding'], 'gzip')
        self.assertEqual(b, SNAP.read_bytes())
        if not self.app:
            self.skipTest('no built app to serve')
        page = self.get('/')[2]
        self.assertEqual(page, Path(self.app).read_bytes())
        self.assertEqual(self.get('/?dataset=amphan-2020')[2], page)
        swapped = self.get('/?dataset=copy-2020')[2].decode('utf-8')
        tag = '<script type="application/json" id="snapshot">'
        a = swapped.index(tag) + len(tag)
        embedded = json.loads(swapped[a:swapped.index('</script>', a)])
        self.assertEqual(embedded, json.loads(SNAP.read_bytes()))
        self.assertEqual(self.get('/?dataset=cf-model')[0], 404)

    def test_cors(self):
        self.assertIsNone(self.get('/api/v1/datasets', {'Origin': 'https://example.org'})[1]['Access-Control-Allow-Origin'])
        self.srv.cors = ('https://example.org',)
        try:
            h = self.get('/api/v1/datasets', {'Origin': 'https://example.org'})[1]
            self.assertEqual(h['Access-Control-Allow-Origin'], 'https://example.org')
            self.assertIsNone(self.get('/api/v1/datasets', {'Origin': 'https://other.org'})[1]['Access-Control-Allow-Origin'])
        finally:
            self.srv.cors = ()

    # ---- WMS
    def test_wms_capabilities(self):
        s, h, b = self.get('/wms?SERVICE=WMS&REQUEST=GetCapabilities&VERSION=1.3.0')
        self.assertEqual(s, 200)
        root = ET.fromstring(b)
        self.assertEqual(root.get('version'), '1.3.0')
        crs = {e.text for e in root.iterfind('.//wms:Capability/wms:Layer/wms:CRS', NS)}
        self.assertTrue({'CRS:84', 'EPSG:4326'} <= crs)
        layer = next(l for l in root.iterfind('.//wms:Layer', NS) if l.findtext('wms:Name', None, NS) == 'amphan-2020.temp')
        dims = {d.get('name'): d.text.split(',') for d in layer.iterfind('wms:Dimension', NS)}
        self.assertEqual(len(dims['time']), len(self.meta['times']))
        self.assertEqual([abs(float(x)) for x in dims['elevation']], [float(z) for z in self.meta['depths']])
        self.assertEqual(layer.get('queryable'), '1')

    def getmap(self, extra, lon, lat, size=3, d=0.01, layer='amphan-2020.temp'):
        bbox = f'{lon - size * d / 2},{lat - size * d / 2},{lon + size * d / 2},{lat + size * d / 2}'
        s, h, b = self.get(f'/wms?SERVICE=WMS&VERSION=1.3.0&REQUEST=GetMap&LAYERS={layer}&STYLES=&CRS=CRS:84'
                           f'&BBOX={bbox}&WIDTH={size}&HEIGHT={size}&FORMAT=image/png&TRANSPARENT=TRUE{extra}')
        self.assertEqual(s, 200, b[:300])
        self.assertEqual(h['Content-Type'], 'image/png')
        return read_png(b)

    def test_wms_getmap_colour_at_node(self):
        i, j = self.node
        lon, lat = self.lonlat(i, j)
        v = self.value('temp', 3, 0, i, j)
        lut = palette('temperature')
        img = self.getmap('&TIME=2020-05-13T12:00:00Z&ELEVATION=0', lon, lat)
        want = lut[int(round((v - 6) / (32 - 6) * 255))]
        self.assertTrue((np.abs(img[1, 1, :3].astype(int) - want) <= 2).all(), f'{img[1, 1]} vs {want} for {v:.3f} degC')
        self.assertEqual(img[1, 1, 3], 255)
        # COLORSCALERANGE centred on the value lands mid-palette; a log scale puts it at log10 position
        img = self.getmap(f'&TIME=2020-05-13T12:00:00Z&COLORSCALERANGE={v - 1},{v + 1}', lon, lat)
        self.assertTrue((np.abs(img[1, 1, :3].astype(int) - lut[128]) <= 2).all())
        img = self.getmap('&TIME=2020-05-13T12:00:00Z&COLORSCALERANGE=1,100&LOGSCALE=true', lon, lat)
        want = lut[int(round(np.log10(v) / 2 * 255))]
        self.assertTrue((np.abs(img[1, 1, :3].astype(int) - want) <= 2).all(), 'log scale')
        # EPSG:4326 in WMS 1.3.0 takes BBOX as lat,lon
        s, h, b = self.get(f'/wms?SERVICE=WMS&VERSION=1.3.0&REQUEST=GetMap&LAYERS=amphan-2020.temp&STYLES=&CRS=EPSG:4326'
                           f'&BBOX={lat - .015},{lon - .015},{lat + .015},{lon + .015}&WIDTH=3&HEIGHT=3&FORMAT=image/png'
                           f'&TIME=2020-05-13T12:00:00Z')
        self.assertTrue((read_png(b)[1, 1] == self.getmap('&TIME=2020-05-13T12:00:00Z', lon, lat)[1, 1]).all())

    def test_wms_land_transparent(self):
        lon, lat = self.lonlat(*self.land)
        self.assertEqual(self.getmap('', lon, lat)[1, 1, 3], 0)
        full = self.get('/wms?SERVICE=WMS&VERSION=1.3.0&REQUEST=GetMap&LAYERS=amphan-2020.sal&STYLES=salinity&CRS=CRS:84'
                        '&BBOX=61,6,92,22&WIDTH=310&HEIGHT=160&FORMAT=image/png&TRANSPARENT=TRUE')[2]
        clear = (read_png(full)[..., 3] == 0).mean()
        self.assertTrue(0.05 < clear < 0.6, f'{clear:.2f} of the domain transparent')

    def test_wms_featureinfo(self):
        i, j = self.node
        lon, lat = self.lonlat(i, j)
        s, h, b = self.get(f'/wms?SERVICE=WMS&VERSION=1.3.0&REQUEST=GetFeatureInfo&LAYERS=amphan-2020.temp'
                           f'&QUERY_LAYERS=amphan-2020.temp,amphan-2020.sal&STYLES=&CRS=CRS:84'
                           f'&BBOX={lon - .015},{lat - .015},{lon + .015},{lat + .015}&WIDTH=3&HEIGHT=3&I=1&J=1'
                           f'&INFO_FORMAT=application/json&TIME=0')
        f = json.loads(b)['features']
        self.assertAlmostEqual(f[0]['value'], self.value('temp', 0, 0, i, j), places=3)
        self.assertAlmostEqual(f[1]['value'], self.value('sal', 0, 0, i, j), places=3)
        # each dataset resolves TIME against its own times
        b = self.get('/wms?SERVICE=WMS&VERSION=1.3.0&REQUEST=GetFeatureInfo&LAYERS=amphan-2020.temp'
                     '&QUERY_LAYERS=amphan-2020.temp,cf-model.temp&STYLES=&CRS=CRS:84&BBOX=80,9,84,12&WIDTH=4&HEIGHT=3'
                     '&I=1&J=1&INFO_FORMAT=application/json&TIME=2020-05-17T12:00Z')[2]
        self.assertEqual([r['time'] for r in json.loads(b)['features']], ['2020-05-16T12:00:00Z', '2020-05-17T12:00:00Z'])

    def test_wms_exceptions(self):
        for query, code in [('LAYERS=nope.temp', 'LayerNotDefined'), ('LAYERS=amphan-2020.temp&CRS=EPSG:3857', 'InvalidCRS'),
                            ('LAYERS=amphan-2020.temp&STYLES=rainbow', 'StyleNotDefined'),
                            ('LAYERS=amphan-2020.temp&FORMAT=image/gif', 'InvalidFormat')]:
            base = 'SERVICE=WMS&VERSION=1.3.0&REQUEST=GetMap&BBOX=61,6,92,22&WIDTH=10&HEIGHT=10&FORMAT=image/png&CRS=CRS:84'
            s, h, b = self.get(f'/wms?{base}&{query}')
            self.assertEqual(s, 400, query)
            ex = ET.fromstring(b).find('ogc:ServiceException', NS)
            self.assertEqual(ex.get('code'), code, query)

    # ---- WCS
    def test_wcs_capabilities_and_describe(self):
        root = ET.fromstring(self.get('/wcs?SERVICE=WCS&REQUEST=GetCapabilities')[2])
        ids = {e.text for e in root.iterfind('.//wcs:CoverageId', NS)}
        self.assertTrue({'amphan-2020.temp', 'amphan-2020.sal', 'amphan-2020.u', 'cf-model.temp'} <= ids)
        root = ET.fromstring(self.get('/wcs?SERVICE=WCS&VERSION=2.0.1&REQUEST=DescribeCoverage&COVERAGEID=amphan-2020.temp')[2])
        env = root.find('.//gml:Envelope', NS)
        self.assertEqual(env.get('axisLabels'), 'time depth Lat Long')
        high = root.findtext('.//gml:high', None, NS).split()
        self.assertEqual([int(x) + 1 for x in high], [len(self.meta['times']), len(self.meta['depths']),
                                                      self.meta['nlat'], self.meta['nlon']])

    def test_wcs_netcdf_round_trip(self):
        s, h, b = self.get('/wcs?SERVICE=WCS&VERSION=2.0.1&REQUEST=GetCoverage&COVERAGEID=amphan-2020.temp'
                           '&SUBSET=Lat(10,12)&SUBSET=Long(80,82)&SUBSET=depth(0,50)&SUBSET=time("2020-05-16T12:00Z")'
                           '&FORMAT=application/netcdf')
        self.assertEqual(s, 200, b[:300])
        nc = ncio.read_bytes(b)
        self.assertEqual(nc['attrs']['Conventions'], 'CF-1.8')
        self.assertEqual(nc['dims'], {'time': 1, 'depth': 3, 'lat': 5, 'lon': 5})
        V = nc['vars']
        self.assertEqual(V['temp']['attrs']['standard_name'], 'sea_water_temperature')
        self.assertEqual(list(V['lat']['data']), [10, 10.5, 11, 11.5, 12])
        self.assertEqual(V['time']['data'][0] * 3600, serve.parse_iso('2020-05-16T12:00Z'))
        m = self.meta
        i0, j0 = int((10 - m['lat0']) / m['step']), int((80 - m['lon0']) / m['step'])
        for k in range(3):
            for a in range(5):
                for c in range(5):
                    got = V['temp']['data'][0, k, a, c]
                    if self.wet[k, i0 + a, j0 + c]:
                        self.assertAlmostEqual(float(got), self.value('temp', 4, k, i0 + a, j0 + c), places=4)
                    else:
                        self.assertTrue(np.isnan(got), 'no model water -> _FillValue -> NaN')

    def test_wcs_csv_and_errors(self):
        s, h, b = self.get('/wcs?SERVICE=WCS&VERSION=2.0.1&REQUEST=GetCoverage&COVERAGEID=amphan-2020.sal'
                           '&SUBSET=Lat(10,11)&SUBSET=Long(80,81)&SUBSET=time("2020-05-04T12:00Z")&FORMAT=text/csv')
        lines = b.decode().strip().splitlines()
        self.assertEqual(lines[0], 'time,depth_m,lat,lon,sal_1e-3')
        self.assertEqual(len(lines) - 1, 1 * len(self.meta['depths']) * 3 * 3)
        s, h, b = self.get('/wcs?SERVICE=WCS&VERSION=2.0.1&REQUEST=GetCoverage&COVERAGEID=nope')
        self.assertEqual(s, 404)
        self.assertEqual(ET.fromstring(b).find('ows:Exception', NS).get('exceptionCode'), 'NoSuchCoverage')
        for sub in ('Lat(40,50)', 'time("1999-01-01")', 'depth(5000)', 'Long(100)'):
            s, h, b = self.get(f'/wcs?SERVICE=WCS&VERSION=2.0.1&REQUEST=GetCoverage&COVERAGEID=amphan-2020.sal&SUBSET={sub}')
            self.assertEqual(s, 400, sub)
            self.assertEqual(ET.fromstring(b).find('ows:Exception', NS).get('exceptionCode'), 'InvalidSubsetting', sub)

    # ---- OPeNDAP DAP2
    def test_opendap_dds_and_das(self):
        import build_snapshot as B
        dds = self.get('/opendap/amphan-2020.dds?temp[0][0:1][10:11][20:22],time,sal.sal[0][0][10][20]')[2].decode()
        for line in ('Grid {', 'Float32 temp[time = 1][depth = 2][lat = 2][lon = 3];', 'Float64 lon[lon = 3];',
                     'Float64 time[time = 8];', 'Structure {', 'Float32 sal[time = 1][depth = 1][lat = 1][lon = 1];',
                     '} amphan-2020;'):
            self.assertIn(line, dds)
        full = self.get('/opendap/amphan-2020.dds')[2].decode()
        self.assertEqual(full.count('Grid {'), 5, 'one Grid per variable (temp, sal, u, v, speed)')
        das = B.parse_das(self.get('/opendap/amphan-2020.das')[2])
        for line in ('Float32 _FillValue 9.96921e+36;', 'String standard_name "sea_water_temperature";',
                     'String units "hours since 1970-01-01 00:00:00";', 'NC_GLOBAL {'):
            self.assertIn(line, das)

    def test_opendap_ascii_read_by_the_pipeline_parser(self):
        """build_snapshot.parse_ascii, the reader the pipeline uses on HYCOM's THREDDS, reads our own .ascii."""
        import build_snapshot as B
        m = self.meta
        # the request form the pipeline sends HYCOM: var.var[t][k0:1:k1][i0:SI:i1][j0:SJ:j1]
        out = B.parse_ascii(self.get('/opendap/amphan-2020.ascii?temp.temp[3][0:1:2][10:2:14][20:1:22],'
                                     'sal.sal[3][0:1:2][10:2:14][20:1:22]')[2])
        self.assertEqual(out['temp_shape'], [1, 3, 3, 3])
        for var in ('temp', 'sal'):
            got = out[var].reshape(3, 3, 3)
            for k in range(3):
                for a, i in enumerate((10, 12, 14)):
                    for c, j in enumerate((20, 21, 22)):
                        if self.wet[k, i, j]:
                            self.assertAlmostEqual(got[k, a, c], self.value(var, 3, k, i, j), places=3)
                        else:
                            self.assertAlmostEqual(got[k, a, c] / 9.96921e36, 1, places=5, msg='no water -> _FillValue')
        ax = B.parse_ascii(self.get('/opendap/amphan-2020.ascii?lat,lon,depth')[2])
        self.assertEqual(list(ax['depth']), m['depths'])
        np.testing.assert_allclose(ax['lat'], m['lat0'] + m['step'] * np.arange(m['nlat']))
        t = B.parse_ascii(self.get('/opendap/amphan-2020.ascii?time')[2])['time']
        self.assertEqual(t[4] * 3600, serve.parse_iso(m['times'][4]))
        # a whole Grid brings its maps
        g = B.parse_ascii(self.get('/opendap/amphan-2020.ascii?temp[3][1][10:11][20:21]')[2])
        self.assertEqual((list(g['depth']), list(g['lat']), list(g['lon'])), ([20.0], [11.0, 11.5], [71.0, 71.5]))
        # a NetCDF dataset (latitude stored north to south, served south to north)
        lat, lon, depth, temp = self.cf
        v = B.parse_ascii(self.get('/opendap/cf-model.ascii?temp.temp[1][1][0][2]')[2])['temp']
        self.assertAlmostEqual(v[0], float(temp[1, 1, 3, 2]), places=4)

    def test_opendap_dods_byte_layout(self):
        i, j = self.node
        s, h, b = self.get(f'/opendap/amphan-2020.dods?temp[0][0][{i}:1:{i + 1}][{j}:1:{j + 2}]')
        self.assertEqual((s, h['Content-Type'], h['Content-Description']), (200, 'application/octet-stream', 'dods-data'))
        dds, sep, data = b.partition(b'\nData:\n')
        self.assertTrue(sep and dds.startswith(b'Dataset {'), 'DDS, then "Data:", then XDR')
        pos, parts = 0, []
        for kind, n in (('>f4', 6), ('>f8', 1), ('>f8', 1), ('>f8', 2), ('>f8', 3)):   # array, then the 4 maps
            self.assertEqual(struct.unpack('>ii', data[pos:pos + 8]), (n, n), 'XDR arrays start with the length twice')
            size = np.dtype(kind).itemsize * n
            parts.append(np.frombuffer(data[pos + 8:pos + 8 + size], kind))
            pos += 8 + size
        self.assertEqual(pos, len(data), 'nothing after the last map')
        want = [self.value('temp', 0, 0, i + a, j + c) if self.wet[0, i + a, j + c] else 9.96921e36
                for a in range(2) for c in range(3)]
        np.testing.assert_allclose(parts[0], want, rtol=1e-6)
        m = self.meta
        self.assertEqual(parts[1][0] * 3600, serve.parse_iso(m['times'][0]))
        np.testing.assert_allclose(parts[4], m['lon0'] + m['step'] * np.arange(j, j + 3))

    def test_opendap_errors(self):
        for path, status in [('/opendap/amphan-2020.ascii?nope', 404), ('/opendap/amphan-2020.ascii?temp[0][0:1]', 400),
                             ('/opendap/amphan-2020.dods?temp[9][0][0][0]', 400), ('/opendap/amphan-2020.ascii?temp[0:2:x][0][0][0]', 400),
                             ('/opendap/amphan-2020.ascii?temp&temp>3', 400), ('/opendap/nope.dds', 404),
                             ('/opendap/amphan-2020.nc', 404)]:
            s, h, b = self.get(path)
            self.assertEqual(s, status, path)
            self.assertTrue(b.startswith(b'Error {') and f'code = {status};'.encode() in b, path)

    # ---- a CF NetCDF model file as a dataset
    def test_netcdf_dataset(self):
        lat, lon, depth, temp = self.cf
        m = self.json('/api/v1/datasets/cf-model/meta')
        self.assertEqual(m['grid']['lat'], sorted(lat.tolist()), 'latitude is served south to north')
        self.assertEqual(m['times'], ['2020-05-17T12:00:00Z', '2020-05-18T12:00:00Z'])
        self.assertEqual([v['key'] for v in m['variables']], ['temp'], 'potential temperature maps to temp')
        f = self.json('/api/v1/datasets/cf-model/field?var=temp&time=1&depth=10')
        self.assertAlmostEqual(f['values'][0][2], float(temp[1, 1, 3, 2]), places=4)   # south row = last written row
        self.assertIsNone(f['values'][3][0], 'the fill cell stays missing')
        b = self.get('/wcs?SERVICE=WCS&VERSION=2.0.1&REQUEST=GetCoverage&COVERAGEID=cf-model.temp')[2]
        back = ncio.read_bytes(b)['vars']['temp']['data']
        np.testing.assert_allclose(back, temp[:, :, ::-1, :], rtol=0, atol=1e-6)


if __name__ == '__main__':
    unittest.main()
