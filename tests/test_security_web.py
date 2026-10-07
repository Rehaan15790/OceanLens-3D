"""Browser-app security: hostile data stays data, parsers stay bounded, and the page stays offline under its policy.

Each test below was written against an attack that worked on the build before its fix; the comment on the test says
what the attack was. Tests that feed a parser input that used to hang or exhaust memory run on a throw-away page with a
deadline, so a regression fails the test instead of freezing the run. Not part of the quick set: it opens several
browsers. Run it with `python tools/run.py --suite security`.
"""
import base64
import hashlib
import json
import re
import struct
import sys
import tempfile
import unittest
from pathlib import Path

from helpers import AppTestCase, REPO, Browser, default_build, open_app
from cdp import file_url

sys.path.insert(0, str(REPO))          # for `import build` in the embedding tests

IMG = '<img src=x onerror=window.__pwn=1>'
SVG = '"><svg onload=window.__pwn=1>'
DEADLINE = 25          # seconds a page may take over one hostile input before the test calls it a hang
PROTO_NAMES = ['__proto__', 'constructor', 'toString', 'hasOwnProperty', 'valueOf']

# page helpers: a baseline of the DOM, a scan for anything dangerous that appeared since, and readers for hostile input
HELPERS = r"""
window.__base = new Set(document.querySelectorAll('*'));
window.__scan = () => { const bad = [];
  for (const e of document.querySelectorAll('*')) { if (window.__base.has(e)) continue;
    const tag = e.tagName.toLowerCase();
    if ([...e.attributes].some(a => /^on/i.test(a.name))
        || ['img','iframe','script','object','embed','form','style','link','meta','base','video','audio','source','a'].includes(tag)
        || (tag === 'svg' && !e.classList.contains('icon'))) bad.push(e.outerHTML.slice(0, 100)); }
  return bad; };
const __shape = r => ({ms: Math.round(performance.now() - window.__t0), error: r.error, profiles: r.profiles, warnings: r.warnings,
                       needsMapping: r.needsMapping, format: r.format});
window.__readB64 = async (name, b64) => { const u8 = Uint8Array.from(atob(b64), c => c.charCodeAt(0));
  window.__t0 = performance.now(); return __shape(await OceanLens.readData(name, u8.buffer)); };
window.__readText = async (name, text) => { window.__t0 = performance.now(); return __shape(await OceanLens.readData(name, text)); };
window.__add = async (obj, name) => { const p = await OceanLens.readData('x.json', JSON.stringify(obj));
  if (p.error) throw new Error(p.error); return OceanLens.importParsed(p, {name}); };
window.__clean = () => { OceanLens.datasets().forEach(d => OceanLens.removeDataset(d.id)); };
"""
HOOK = ("window.__csp = []; document.addEventListener('securitypolicyviolation', e => window.__csp.push("
        "e.violatedDirective + ' ' + (e.blockedURI || '') + ' ' + (e.sample || '').slice(0, 60)), true);")


# ---- NetCDF classic (CDF-1) files, crafted ----
def _pad4(b):
    return b + b'\0' * (-len(b) % 4)


def _name(s):
    b = s.encode()
    return struct.pack('>I', len(b)) + _pad4(b)


def _attrs(d):
    if not d:
        return struct.pack('>II', 0, 0)
    out = struct.pack('>II', 0x0C, len(d))
    for k, v in d.items():
        b = v.encode()
        out += _name(k) + struct.pack('>II', 2, len(b)) + _pad4(b)
    return out


def netcdf(dims, variables, gattrs=None, numrecs=0, pad_to=0):
    """Bytes of a NetCDF classic file. dims: [(name, size)], size 0 = the record dimension. variables: dicts with name,
    dims (ids), type ('double' or 'char'), attrs (text values), vsize, and either alias (the name of a variable whose
    data this one shares), begin (an exact offset) or neither (the next free space, zero filled)."""
    def head(begins):
        out = b'CDF\x01' + struct.pack('>I', numrecs)
        out += struct.pack('>II', 0x0A, len(dims)) + b''.join(_name(n) + struct.pack('>I', s) for n, s in dims)
        out += _attrs(gattrs) + struct.pack('>II', 0x0B, len(variables))
        for v, begin in zip(variables, begins):
            ids = v.get('dims', [])
            out += _name(v['name']) + struct.pack('>I', len(ids)) + b''.join(struct.pack('>I', i) for i in ids)
            out += _attrs(v.get('attrs')) + struct.pack('>III', 2 if v.get('type') == 'char' else 6, v.get('vsize', 0), begin)
        return out
    pos, begins, where = len(head([0] * len(variables))), [], {}
    for v in variables:
        if 'alias' in v:
            begin = where[v['alias']]
        elif 'begin' in v:
            begin = v['begin']
        else:
            n = 1
            for i in v.get('dims', []):
                n *= dims[i][1] or 1
            begin, pos = pos, pos + (n * (1 if v.get('type') == 'char' else 8) + 3) // 4 * 4
        where[v['name']] = begin
        begins.append(begin)
    body = head(begins)
    return body + b'\0' * (max(pos, pad_to) - len(body))


def ego_file(n, shared=False):
    """A glider time series of n samples; with shared, PRES, TEMP, PSAL, LATITUDE and LONGITUDE reuse TIME's bytes."""
    extra = {'alias': 'TIME'} if shared else {}
    vs = [dict(name='TIME', dims=[0], attrs={'units': 'seconds since 1970-01-01 00:00:00'})] + \
         [dict(name=k, dims=[0], **extra) for k in ('LATITUDE', 'LONGITUDE', 'PRES', 'TEMP', 'PSAL')]
    return netcdf([('N', n)], vs, gattrs={'data_type': 'EGO glider time series data'})


def argo_file(n_prof, n_levels, record_bomb=0, records=0):
    """An Argo profile file that states n_prof x n_levels but holds no measurements; with record_bomb it also has five
    record variables whose stride is 0, so each claims record_bomb values in each of `records` records at the same bytes."""
    dims = [('N_PROF', n_prof), ('N_LEVELS', n_levels), ('STR', 8)]
    vs = [dict(name='PLATFORM_NUMBER', dims=[0, 2], type='char'), dict(name='JULD', dims=[0], attrs={'units': 'days since 1950-01-01 00:00:00'}),
          dict(name='LATITUDE', dims=[0]), dict(name='LONGITUDE', dims=[0])]
    if record_bomb:
        dims += [('REC', 0), ('BIG', record_bomb)]
        vs += [dict(name='PRES', dims=[3, 4], vsize=0)] + [dict(name=k, dims=[3, 4], vsize=0, alias='PRES') for k in ('TEMP', 'PSAL', 'CHLA', 'DOXY')]
    return netcdf(dims, vs, numrecs=records, pad_to=records + 16)


def b64(data):
    return base64.b64encode(data).decode()


def profile(pid, lat, lon, time, **extra):
    return dict({'id': pid, 'lat': lat, 'lon': lon, 'time': time, 'z': [5, 20, 50, 100], 'temp': [28, 27, 26, 20],
                 'sal': [33, 33.5, 34, 34.5]}, **extra)


class Throwaway(unittest.TestCase):
    """One fresh headless page per test, for input that may hang it."""
    def setUp(self):
        self.b = Browser()
        self.addCleanup(self.b.close)
        self.assertEqual(open_app(self.b, default_build()), 'ready')
        self.b.eval(HELPERS)

    def js(self, expr, **kw):
        kw.setdefault('timeout', DEADLINE)
        return self.b.eval(expr, **kw)

    def read(self, name, data):
        return self.js(f'__readB64({json.dumps(name)}, {json.dumps(b64(data))})', await_promise=True)

    def read_text(self, name, text):
        return self.js(f'__readText({json.dumps(name)}, {json.dumps(text)})', await_promise=True)

    def bounds(self):
        return self.js('[LAT0, LAT1, LON0, LON1]')


# =============================================================================================================
class HostileText(AppTestCase):
    """Names, ids and metadata from a file are text wherever the app shows them."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.b.eval(HELPERS)
        cls.lat0, cls.lat1, cls.lon0, cls.lon1 = cls.b.eval('[LAT0, LAT1, LON0, LON1]')
        cls.mid = ((cls.lat0 + cls.lat1) / 2, (cls.lon0 + cls.lon1) / 2)
        cls.t = cls.b.eval('META.times[2]')
        cls.proto0 = cls.b.eval('Object.getOwnPropertyNames(Object.prototype).length')

    def tearDown(self):
        self.js('__clean(); clearSection(); closeSidePanels();')

    def assertClean(self):
        self.assertEqual(self.js('__scan()'), [], 'markup from the file became elements')
        self.assertIsNone(self.js('window.__pwn'), 'a script from the file ran')

    # attack: ids, dataset names, cycle and mode from a JSON file reached innerHTML in the profile panel, the hover tip,
    # the validation panel or the section panel unescaped
    def test_markup_in_ids_names_and_metadata_is_shown_as_text(self):
        la, lo = self.mid
        ds = {'type': 'argo', 'name': IMG, 'notes': [IMG], 'profiles': [
            profile(IMG, la, lo, self.t, meta={'cycle': SVG, 'mode': IMG, 'platform': SVG}),
            profile(SVG, la + 0.5, lo, self.t), profile("'-window.__pwn=1-'", la + 1, lo, self.t),
            profile('javascript:window.__pwn=1', la + 1.5, lo, self.t), profile('<b>bold</b>', la - 0.5, lo, self.t)]}
        res = self.js(f'__add({json.dumps(ds)}, {json.dumps(IMG)})', await_promise=True)
        self.assertEqual(res['profiles'], 5)
        self.js('setShowAllImported(true)')
        self.b.pump(0.6)
        self.js("""importedInsts.forEach(i => { openProfile(i.userData);
            for (const tab of ['temp', 'sal']) { profileState.tab = tab; syncProfileTabs(); drawProfileChart(); renderDerived(); } })""")
        self.js('renderValidation()')
        self.js(f"""(() => {{ const m = importedInsts[0]; section = {{a: [m.lat - 0.2, m.lon - 0.2], b: [m.lat + 0.2, m.lon + 0.2],
            name: {json.dumps(IMG)}}}; showPanel('section'); drawSectionPanel(); }})()""")
        tips = self.js("""(() => { const rect = renderer.domElement.getBoundingClientRect(), out = [];
            for (const it of markerItems) { if (!instrumentVisible(it.vis)) continue;
              const v = it.pos.clone().project(camera); if (v.z > 1) continue;
              const text = hoverText({clientX: rect.left + (v.x * 0.5 + 0.5) * rect.width, clientY: rect.top + (-v.y * 0.5 + 0.5) * rect.height});
              if (text) { hoverTip.innerHTML = text; out.push(text); } }
            return out; })()""")
        self.assertTrue(any('&lt;img src=x onerror' in t for t in tips), 'no hover tip showed the hostile id (is it on the map?)')
        self.assertFalse(any('<img' in t or '<svg' in t for t in tips), 'a hover tip carries live markup')
        self.assertClean()
        self.assertNoProblems()

    # attack: CSV header cells, a cell in the id column and the file name reached the import dialog
    def test_markup_in_csv_headers_cells_and_file_name_is_shown_as_text(self):
        la, lo = self.mid
        rows = [f'{la},{lo},{self.t},{z},{T},x{SVG},1' for z, T in ((5, 28), (20, 27), (50, 26), (100, 20))]
        csv = f'latitude,longitude,time,depth,temp,station,{IMG}\n' + '\n'.join(rows) + '\n'
        name = IMG + '.csv'
        self.js("document.getElementById('addDataBtn').click()")
        self.b.pump(0.2)
        self.js(f"importUI.readFile(new File([{json.dumps(csv)}], {json.dumps(name)}))", await_promise=True)
        self.b.pump(0.4)
        self.assertEqual(self.js("document.getElementById('importFile').textContent"), name)
        options = self.js("[...document.querySelectorAll('#importMapGrid option')].map(o => o.textContent)")
        self.assertTrue(any(IMG in o for o in options), 'the hostile header should be listed as plain text')
        self.assertClean()
        self.js("document.getElementById('importAdd').click()")
        self.b.pump(0.8)
        self.assertEqual(self.js("OceanLens.datasets().length"), 1)
        self.assertIn(IMG, self.js("document.getElementById('importAnnounce').textContent"))
        self.assertClean()

    # attack: an instrument type, unit or key named like a member of Object.prototype ("constructor", "__proto__")
    # resolved to inherited properties; a click on such a profile threw "type.kindLabel is not a function"
    def test_names_of_object_prototype_members_are_ordinary_values(self):
        la, lo = self.mid
        for key in PROTO_NAMES:
            with self.subTest(type=key):
                ds = {'type': key, 'name': 'p-' + key, 'profiles': [profile('A', la, lo, self.t), profile('B', la + 1, lo, self.t, type=key)]}
                self.js(f'__add({json.dumps(ds)}, "p-{key}")', await_promise=True)
                r = self.js("""(() => { const out = {kinds: importedInsts.map(i => i.kind), err: null};
                    try { importedInsts.forEach(i => openProfile(i.userData)); } catch (e) { out.err = String(e); } return out; })()""")
                self.assertEqual(r, {'kinds': ['other', 'other'], 'err': None})
                self.assertIsNone(self.js(f'OceanLens.instrument({json.dumps(key)})'))
                self.js('__clean()')
        for key in PROTO_NAMES:
            with self.subTest(unit=key):
                la, lo = self.mid
                csv = f'latitude,longitude,time,depth,temp [{key}]\n{la},{lo},{self.t},5,28\n{la},{lo},{self.t},50,26\n'
                r = self.js(f'__readText("u.csv", {json.dumps(csv)})', await_promise=True)
                self.assertIsNone(r['error'])
                self.assertEqual(r['profiles'], 1, r)
                self.assertTrue(any('not recognised' in w for w in r['warnings']), r['warnings'])

    # attack: "__proto__" and "constructor" keys in JSON and CSV headers aimed at Object.prototype
    def test_prototype_pollution_attempts_change_nothing(self):
        la, lo = self.mid
        pollute = ('{"__proto__": {"polluted": 1}, "profiles": [{"__proto__": {"polluted2": 1}, "constructor": {"prototype": {"polluted3": 1}},'
                   f' "id": "x", "lat": {la}, "lon": {lo}, "time": "{self.t}", "z": [5, 50], "vars": {{"__proto__": {{"polluted": 1}}, "temp": [28, 26]}},'
                   ' "meta": {"__proto__": {"polluted2": 1}}}]}')
        csv = (f'__proto__,constructor,prototype,toString,latitude,longitude,time,depth,temp\n1,2,3,4,{la},{lo},{self.t},5,28\n'
               f'1,2,3,4,{la},{lo},{self.t},50,26\n')
        for name, text in (('p.json', pollute), ('p.csv', csv)):
            r = self.js(f'__readText({json.dumps(name)}, {json.dumps(text)})', await_promise=True)
            self.assertIsNone(r['error'], r)
        self.js(f"OceanLens.readData('p.json', {json.dumps(pollute)}).then(p => OceanLens.importParsed(p, {{name: 'p'}}))", await_promise=True)
        self.assertEqual(self.js('[({}).polluted, ({}).polluted2, ({}).polluted3, [].polluted]'), [None] * 4)
        self.assertEqual(self.js('Object.getOwnPropertyNames(Object.prototype).length'), self.proto0)


# =============================================================================================================
class ParserLimits(AppTestCase):
    """Legitimate files keep working (the fixes below are only worth having if they do)."""
    SAMPLES = {'argo-5907328_prof.nc': ('netcdf3', 3), 'argo-amphan-samples.csv': ('csv', 3), 'argo-erddap-2020-05-18.csv': ('csv', 3),
               'bgc-argo-2902120-c227.nc': ('netcdf3', 1), 'ctd-synthetic-cast.csv': ('csv', 1),
               'glider-humpback504-2016-07-05.nc': ('netcdf3', 4), 'glider-marlin505-2016-07-06.csv': ('csv', 6)}

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.b.eval(HELPERS)

    def test_shipped_samples_still_import(self):
        for name, (fmt, n) in self.SAMPLES.items():
            with self.subTest(sample=name):
                data = (REPO / 'samples' / name).read_bytes()
                r = self.js(f'__readB64({json.dumps(name)}, {json.dumps(b64(data))})', await_promise=True)
                self.assertIsNone(r['error'])
                self.assertEqual((r['format'], r['profiles'], r['needsMapping']), (fmt, n, False))



# =============================================================================================================
class DamagedFiles(Throwaway):
    """Real files cut short or with flipped bytes."""

    # truncated and bit-flipped real files must give a result or a message, quickly, never an exception
    def test_damaged_netcdf_files_end_in_a_message(self):
        files = {n: b64((REPO / 'samples' / n).read_bytes()) for n in ParserLimits.SAMPLES if n.endswith('.nc')}
        r = self.js("""(async files => { let seed = 12345; const rnd = () => (seed = (seed * 1103515245 + 12345) & 0x7fffffff) / 0x7fffffff;
            const out = {cases: 0, threw: [], slowest: 0};
            for (const [name, b] of Object.entries(files)) {
              const src = Uint8Array.from(atob(b), c => c.charCodeAt(0));
              for (let i = 0; i < 60; i++) {
                const u = i < 20 ? src.slice(0, Math.floor(rnd() * src.length)) : src.slice();
                if (i >= 20) for (let k = 0; k < 1 + (i % 4); k++) u[Math.floor(rnd() * Math.min(u.length, i % 2 ? 1500 : u.length))] = Math.floor(rnd() * 256);
                const t0 = performance.now();
                try { await OceanLens.readData(name, u.buffer); } catch (e) { out.threw.push(name + ': ' + e.message); }
                out.slowest = Math.max(out.slowest, performance.now() - t0); out.cases++; } }
            return out; })(FILES)""".replace('FILES', json.dumps(files)), await_promise=True, timeout=120)
        self.assertEqual(r['threw'], [])
        self.assertLess(r['slowest'], 5000, 'one damaged file took %.0f ms' % r['slowest'])


# =============================================================================================================
class Deadlines(Throwaway):
    """Inputs that used to hang the page or exhaust its memory now end quickly, with a message."""

    # attack: a header cell "(" + spaces made the unit regex backtrack cubically: 3200 spaces took 2 s, 25 000 about 20 min
    def test_csv_header_text_cannot_stall_the_unit_parser(self):
        r = self.read_text('h.csv', '(' + ' ' * 6000 + 'x,lat\n1,2\n1,2\n')
        self.assertLess(r['ms'], 1000, 'parsing a 6000-space header took %d ms' % r['ms'])

    # attack: a line of a"a"a"... made the splitter trim its growing cell at every quote: 100 000 pairs took 5.5 s, 200 000
    # about 20 s, a megabyte of them minutes
    def test_a_line_full_of_quotes_is_split_in_linear_time(self):
        r = self.read_text('q.csv', 'a"' * 100000 + ',lat\n1,2\n1,2\n')
        self.assertLess(r['ms'], 1000, 'splitting 100 000 quote pairs took %d ms' % r['ms'])
        # the quoting rules themselves are unchanged
        t = self.js("""[splitLine('a,"b,c",d', ','), splitLine('  "x ""y"" z" , w', ','), splitLine('p"q,"r', ','), splitLine('"",x', ','),
                       splitLine('"a"b"c",d', ','), splitLine(' ,"  ","k"', ',')]""")
        self.assertEqual(t, [['a', 'b,c', 'd'], ['x "y" z', 'w'], ['p"q', 'r'], ['', 'x'], ['ab"c"', 'd'], ['', '', 'k']])

    # attack: a table of tens of thousands of columns made the mapping step build thirteen menus of that many options
    # (60 000 columns: 780 000 options, 6.5 s; 500 000 columns: minutes and gigabytes)
    def test_a_csv_with_tens_of_thousands_of_columns_is_refused(self):
        n = 60000
        text = ','.join(f'c{i}' for i in range(n)) + '\n' + ','.join(['1'] * n) + '\n' + ','.join(['2'] * n) + '\n'
        r = self.read_text('wide.csv', text)
        self.assertTrue(r['error'] and 'columns' in r['error'], r)
        self.js("document.getElementById('addDataBtn').click()")
        self.b.pump(0.2)
        self.js(f"importUI.readFile(new File([{json.dumps(text)}], 'wide.csv'))", await_promise=True)
        self.b.pump(0.3)
        self.assertEqual(self.js("document.querySelectorAll('#importMapGrid option').length"), 0)
        self.assertIn('columns', self.js("document.getElementById('importError').textContent"))

    # attack: the glider reader re-read each variable for every sample, so n samples cost n*n reads
    # (4000 samples 0.6 s, 30 000 about 35 s)
    def test_glider_file_is_read_in_linear_time(self):
        r = self.read('glider.nc', ego_file(12000))
        self.assertLess(r['ms'], 1500, 'reading 12 000 samples took %d ms' % r['ms'])

    # attack: N_PROF x N_LEVELS stated in the header with no data behind it: the loop ran for every pair
    # (a 32 KB file with 2000 x 8000 took 0.4 s, with 100 KB and 12 000 x 100 000 about a minute)
    def test_argo_dimensions_without_matching_data_are_refused(self):
        r = self.read('argo.nc', argo_file(2000, 30000))
        self.assertTrue(r['error'] and 'hold only' in r['error'], r)
        self.assertLess(r['ms'], 1000)

    # attack: five record variables with a stride of 0 over the same bytes: a 60 KB file unpacked to five 400 MB
    # Float64Arrays (50 000 records x 1000 values each)
    def test_variables_that_claim_more_data_than_the_file_holds_are_refused(self):
        data = argo_file(1, 2, record_bomb=1000, records=8000)
        self.assertLess(len(data), 70000)
        r = self.read('bomb.nc', data)
        self.assertTrue(r['error'] and 'claim more data' in r['error'], r)
        self.assertLess(r['ms'], 1000)
        r = self.read('shared.nc', ego_file(4000, shared=True))          # variables that share bytes are not a real file either
        self.assertTrue(r['error'] and 'claim more data' in r['error'], r)

    # attack: two profile values one step apart at a size where "v += step" changes nothing made the axis-tick loop
    # run until the array was full (8 s, then "Invalid array length"); 1e17 and 1e17 + 16 never returned
    def test_axis_ticks_terminate_for_values_one_step_apart(self):
        for lo, hi in ((28.0, 28.000000000000004), (1e17, 1e17 + 16), (1e300, 1.0000000000000002e300)):
            with self.subTest(lo=lo):
                n = self.js(f'niceTicks({lo!r}, {hi!r}, 4).length')
                self.assertLessEqual(n, 12)
        self.assertEqual(self.js('niceTicks(0, 30, 4)'), [0, 10, 20, 30])         # ordinary ranges are untouched
        self.assertEqual(self.js('niceTicks(26, 31, 4)'), [26, 28, 30])

    def test_a_chlorophyll_profile_with_nearly_equal_values_draws(self):
        mid = self.js('[(LAT0 + LAT1) / 2, (LON0 + LON1) / 2, META.times[2]]')
        ds = {'type': 'argo', 'name': 'flat', 'profiles': [profile('F', mid[0], mid[1], mid[2], chl=[0.1, 0.10000000000000002, 0.1, 0.10000000000000002])]}
        r = self.js(f"""(async () => {{ await __add({json.dumps(ds)}, 'flat'); setShowAllImported(true);
            importedInsts.forEach(i => {{ openProfile(i.userData); profileState.tab = 'chl'; syncProfileTabs(); drawProfileChart(); }}); return 'drawn'; }})()""",
                    await_promise=True)
        self.assertEqual(r, 'drawn')

    # attack: an "array" written as {"length": 300000000} made the ADCP and mooring readers allocate and walk that many
    # elements (3.6 s, then "Invalid array length"; a few hundred million more and the tab goes)
    def test_arrays_with_a_huge_length_do_not_stall_the_plugin_readers(self):
        mid = self.js('[(LAT0 + LAT1) / 2, (LON0 + LON1) / 2, META.times[2]]')
        huge = {'length': 300000000}
        for ds in ({'type': 'adcp', 'profiles': [dict(profile('A', *mid), u=huge, v=[0.1, 0.2, 0.3, 0.4])]},
                   {'type': 'mooring', 'profiles': [dict(profile('M', *mid), z=huge)]},
                   {'type': 'mooring', 'profiles': [dict(profile('M', *mid), temp=huge)]}):
            with self.subTest(type=ds['type'], keys=sorted(ds['profiles'][0])[:3]):
                r = self.js(f"""(async () => {{ const t0 = performance.now(); const p = await OceanLens.readData('x.json', {json.dumps(json.dumps(ds))});
                    let res = null; try {{ OceanLens.importParsed(p, {{name: 'x'}}); }} catch (e) {{ res = e.message; }}
                    return [Math.round(performance.now() - t0), p.error, res]; }})()""", await_promise=True)
                self.assertLess(r[0], 1000, r)
                self.assertNotIn('Invalid array length', str(r))

    # attack: a depth of 1e300 m in a warm profile made the heat-content loop (2 m steps down to the deepest level)
    # run for ever when the profile was opened
    def test_depths_below_any_ocean_are_dropped(self):
        mid = self.js('[(LAT0 + LAT1) / 2, (LON0 + LON1) / 2, META.times[2]]')
        ds = {'type': 'argo', 'name': 'deep', 'profiles': [profile('D', mid[0], mid[1], mid[2], z=[5, 20, 11999, 1e300, 1e308], temp=[28, 27, 26.5, 26.2, 26.1],
                                                                  sal=[34, 34, 34, 34, 34])]}
        r = self.js(f"""(async () => {{ await __add({json.dumps(ds)}, 'deep'); setShowAllImported(true);
            const i = importedInsts[0]; openProfile(i.userData); return [i.maxDepth, i.levels.length]; }})()""", await_promise=True)
        self.assertEqual(r, [11999, 3])

    # attack: a fuzzed header (flipped bytes, cut files) could claim huge counts; every outcome is a result or a message
    def test_fuzzed_netcdf_headers_neither_hang_nor_throw(self):
        base = argo_file(3, 4)
        r = self.js("""(async b64 => { let seed = 99; const rnd = () => (seed = (seed * 1103515245 + 12345) & 0x7fffffff) / 0x7fffffff;
            const src = Uint8Array.from(atob(b64), c => c.charCodeAt(0)), out = {n: 0, threw: [], slowest: 0, outcomes: {}};
            for (let i = 0; i < 300; i++) { const u = src.slice();
              for (let k = 0; k < 1 + (i % 3); k++) u[Math.floor(rnd() * Math.min(u.length, 400))] = rnd() < 0.5 ? 255 : Math.floor(rnd() * 256);
              const t0 = performance.now();
              try { const p = await OceanLens.readData('f.nc', u.buffer); out.outcomes[p.error ? 'error' : 'ok'] = (out.outcomes[p.error ? 'error' : 'ok'] || 0) + 1; }
              catch (e) { out.threw.push(e.message); }
              out.slowest = Math.max(out.slowest, performance.now() - t0); out.n++; }
            return out; })(DATA)""".replace('DATA', json.dumps(b64(base))), await_promise=True)
        self.assertEqual(r['threw'], [])
        self.assertLess(r['slowest'], 3000)


# =============================================================================================================
class UrlAndStorage(unittest.TestCase):
    """The address and the page's storage are not trusted either."""

    def test_hostile_query_hash_and_storage_do_nothing(self):
        build = default_build()
        urls = ['?dataset=%3Cimg%20src%3Dx%20onerror%3Dwindow.__pwn%3D1%3E', '?dataset=__proto__&test', '#%3Cimg%20src%3Dx%20onerror%3Dwindow.__pwn%3D1%3E',
                '?%00%ff=' + 'A' * 60000 + '#' + 'B' * 60000, '?test&seed=%27%3Balert(1)//']
        with Browser() as b:
            self.assertEqual(open_app(b, build, enter=False), 'ready')
            b.eval("sessionStorage.setItem('ol-small-ok', '<img src=x onerror=window.__pwn=1>'); localStorage.setItem('ol-small-ok', '{\"__proto__\":{\"x\":1}}')")
            for q in urls:
                with self.subTest(url=q[:50]):
                    mark = b.mark()
                    b.goto(file_url(build) + q)
                    b.wait("document.documentElement.dataset.state === 'ready' || document.documentElement.dataset.state === 'error'", timeout=30)
                    self.assertEqual(b.eval("document.documentElement.dataset.state"), 'ready')
                    self.assertIsNone(b.eval('window.__pwn'))
                    self.assertEqual([p['text'][:100] for p in b.problems(since=mark) if p['kind'] == 'exception'], [])


# =============================================================================================================
class PageFile(unittest.TestCase):
    """The built file as shipped: its policy, its references, its links. No browser needed."""

    @classmethod
    def setUpClass(cls):
        cls.html = default_build().read_text(encoding='utf-8')
        m = re.search(r'<meta http-equiv="Content-Security-Policy" content="([^"]*)">', cls.html)
        cls.csp = {}
        for part in (m.group(1) if m else '').split(';'):
            toks = part.split()
            if toks:
                cls.csp[toks[0]] = toks[1:]

    def inline_scripts(self):
        out = []
        for m in re.finditer(r'<!--.*?-->|<script\b([^>]*)>(.*?)</script>', self.html, re.S | re.I):
            if m.group(0).startswith('<!--'):
                continue
            if 'src=' in m.group(1) or ('type=' in m.group(1) and 'javascript' not in m.group(1)):
                continue
            out.append(m.group(2))
        return out

    def test_the_policy_allows_no_network_eval_or_inline_script(self):
        c = self.csp
        self.assertTrue(c, 'the built page has no Content-Security-Policy meta tag')
        self.assertEqual(c.get('default-src'), ["'none'"])
        for d in ('object-src', 'base-uri', 'form-action'):
            self.assertEqual(c.get(d), ["'none'"], d)
        for d in ('script-src', 'style-src'):
            self.assertTrue(c[d] and all(t.startswith("'sha256-") for t in c[d]), f'{d} should list only hashes: {c[d][:3]}')
        self.assertEqual(c.get('connect-src'), ["'self'"])
        for d, toks in c.items():
            for t in toks:
                self.assertNotIn(t, ('*', 'http:', 'https:', "'unsafe-eval'", "'wasm-unsafe-eval'", "'unsafe-hashes'", "'unsafe-inline'"), f'{d} allows {t}')
        self.assertTrue(set(c.get('img-src', [])) <= {'data:', 'blob:'})
        self.assertNotIn('worker-src', c)        # workers fall back to default-src 'none'

    def test_every_inline_script_and_style_has_its_hash_and_no_hash_is_stale(self):
        def h(text):
            return "'sha256-" + base64.b64encode(hashlib.sha256(text.encode('utf-8')).digest()).decode('ascii') + "'"
        self.assertEqual({h(body) for body in self.inline_scripts()}, set(self.csp['script-src']))
        styles = re.findall(r'<style\b[^>]*>(.*?)</style>', self.html, re.S | re.I)
        self.assertEqual({h(s) for s in styles}, set(self.csp['style-src']))
        self.assertNotRegex(re.sub(r'<(script|style)\b[^>]*>.*?</\1>', '', self.html, flags=re.S | re.I), r'\sstyle\s*=', 'a style="" attribute needs unsafe-inline')

    def test_nothing_is_fetched_from_outside_the_file(self):
        tags = re.sub(r'(<(script|style)\b[^>]*>).*?(</\2>)', r'\1\3', self.html, flags=re.S | re.I)
        tags = re.sub(r'<!--.*?-->', '', tags, flags=re.S)
        refs = re.findall(r'\b(?:src|href|action|poster|srcset|data|formaction|xlink:href)\s*=\s*["\']([^"\']*)["\']', tags, re.I)
        self.assertEqual([r for r in refs if not r.startswith(('#', 'data:'))], [])
        style = ''.join(re.findall(r'<style\b[^>]*>(.*?)</style>', self.html, re.S | re.I))
        self.assertEqual([u for u in re.findall(r'url\(\s*["\']?([^"\')]+)', style) if not u.startswith('data:')], [])
        self.assertNotRegex(style, r'@import')

    def test_links_and_windows_are_safe(self):
        for a in re.findall(r'<a\b[^>]*>', self.html, re.I):
            if re.search(r'target\s*=\s*["\']?_blank', a, re.I):
                self.assertTrue(re.search(r'rel\s*=\s*["\'][^"\']*noopener', a) and re.search(r'rel\s*=\s*["\'][^"\']*noreferrer', a), a)
        for path in sorted((REPO / 'src' / 'js').rglob('*.js')):
            text = path.read_text(encoding='utf-8')
            self.assertNotRegex(text, r'window\.open\(|\.target\s*=\s*[\'"]_blank|document\.write\(|\beval\(|new Function\(|insertAdjacentHTML|srcdoc|outerHTML\s*=',
                                path.name)
            if re.search(r'addEventListener\(\s*[\'"]message[\'"]|onmessage|postMessage\(', text):      # a handler must check who sent it
                self.assertRegex(text, r'\.origin\b[^;]*;|\.source\b', f'{path.name}: a message handler must check origin and source')

    def test_every_inner_html_in_the_sources_goes_through_escaping(self):
        # the only strings assigned to innerHTML are templates whose data parts pass fmt.esc, numbers or constants;
        # a new unescaped sink is caught by the hostile-id tests above, this keeps the list of files that have any short
        files = sorted(p.name for p in (REPO / 'src' / 'js').rglob('*.js') if 'innerHTML' in p.read_text(encoding='utf-8'))
        self.assertEqual(files, ['charts.js', 'hover.js', 'import-ui.js', 'panels.js'],
                         'a file with an innerHTML sink was added: escape its data with fmt.esc and add a hostile-input test')


# =============================================================================================================
class PolicyInForce(unittest.TestCase):
    """The policy is not just present: the browser enforces it, so an injection that slipped through would still be inert."""

    def test_inline_handlers_eval_and_remote_images_are_blocked(self):
        with Browser() as b:
            b.send('Page.addScriptToEvaluateOnNewDocument', source=HOOK)
            self.assertEqual(open_app(b, default_build(), enter=False), 'ready')
            # eval is tried from a timer: code the debugger evaluates is exempt from the policy, the page's own code is not
            b.eval("""(() => { window.__out = {};
                setTimeout(() => { try { eval('window.__ev = 1'); __out.eval = 'ran'; } catch (e) { __out.eval = e.name; }
                                   try { __out.fn = new Function('return 1')(); } catch (e) { __out.fn = e.name; } }, 0);
                document.body.insertAdjacentHTML('beforeend', '<img src="http://127.0.0.1:9/x.png" onerror="window.__ev2 = 1">');
                const s = document.createElement('script'); s.textContent = 'window.__ev3 = 1'; document.body.appendChild(s); })()""")
            b.pump(0.6)
            self.assertEqual(b.eval('window.__out'), {'eval': 'EvalError', 'fn': 'EvalError'})
            self.assertEqual(b.eval('[window.__ev, window.__ev2, window.__ev3]'), [None, None, None])
            seen = ' | '.join(b.eval('window.__csp'))
            self.assertIn('script-src', seen)
            self.assertIn('img-src', seen)
            # the image was asked for, the browser reported the refusal, and no response ever came back
            self.assertTrue(any(v.startswith('img-src http://127.0.0.1:9/x.png') for v in b.eval('window.__csp')), b.eval('window.__csp'))
            self.assertEqual([r['url'] for r in b.external_requests() if isinstance(r['status'], int)], [])


# =============================================================================================================
class FlowsUnderPolicy(unittest.TestCase):
    """Boot, import (dialog, CSV, NetCDF, plugin types), PNG export and the tour: no policy violation, no request."""

    @classmethod
    def setUpClass(cls):
        cls.b = Browser()
        try:
            cls.b.send('Page.addScriptToEvaluateOnNewDocument', source=HOOK)
            cls.tmp = tempfile.TemporaryDirectory()
            cls.b.allow_downloads(cls.tmp.name)
            build = default_build()
            if open_app(cls.b, build, enter=True) != 'ready':
                raise unittest.SkipTest('app did not become ready')
            cls.b.eval(HELPERS)
            lat, lon = cls.b.eval('[(LAT0 + LAT1) / 2, (LON0 + LON1) / 2]')
            t = cls.b.eval('META.times[2]')
            cls.steps = {}
            samples = REPO / 'samples'

            def dialog(name, data):
                cls.b.eval("document.getElementById('addDataBtn').click()")
                cls.b.pump(0.15)
                cls.b.eval(f"importUI.readFile(new File([Uint8Array.from(atob({json.dumps(b64(data))}), c => c.charCodeAt(0))], {json.dumps(name)}))", await_promise=True)
                cls.b.pump(0.3)
                cls.b.eval("document.getElementById('importAdd').click()")
                cls.b.pump(0.6)
            dialog('argo-amphan-samples.csv', (samples / 'argo-amphan-samples.csv').read_bytes())
            dialog('argo-5907328_prof.nc', (samples / 'argo-5907328_prof.nc').read_bytes())
            adcp = {'type': 'adcp', 'name': 'adcp', 'profiles': [dict(profile('A1', lat, lon, t), u=[0.2, 0.1, 0.1, 0.0], v=[0.1, 0.1, 0.0, 0.0])]}
            radar = {'type': 'hfradar', 'name': 'radar', 'profiles': [{'id': f'c{i}', 'lat': lat + i * 0.2, 'lon': lon, 'time': t, 'u': [0.3], 'v': [0.1]} for i in range(6)]}
            for ds in (adcp, radar):
                cls.b.eval(f'__add({json.dumps(ds)}, {json.dumps(ds["name"])})', await_promise=True)
            cls.b.pump(0.6)
            cls.b.eval("(()=>{ const p = document.querySelector('#varPills .pill[data-v=\"hfr\"]'); if (p) p.click(); })()")
            cls.b.pump(0.4)
            cls.b.eval("importedInsts.forEach(i => openProfile(i.userData))")
            cls.b.eval("document.getElementById('snapBtn').click()")
            cls.b.pump(1.0)
            cls.steps['page1'] = cls.b.eval('window.__csp')
            # the tour on a fresh page
            cls.b.goto(file_url(build, 'test'))
            cls.b.wait("document.documentElement.dataset.state === 'ready'", timeout=30)
            cls.b.pump(0.5)
            cls.b.click('#enterTourBtn')
            for _ in range(60):
                cls.b.pump(0.25)
                if not cls.b.eval('isTourRunning()'):
                    break
                cls.b.eval("(()=>{ const n = document.getElementById('tourNext'); if (n && !n.disabled) n.click(); })()")
            cls.steps['tour'] = cls.b.eval('window.__csp')
        except BaseException:
            cls.b.close()
            raise

    @classmethod
    def tearDownClass(cls):
        cls.b.close()
        cls.tmp.cleanup()

    def test_no_policy_violation_while_importing_exporting_and_touring(self):
        self.assertEqual(self.steps, {'page1': [], 'tour': []})

    def test_no_request_leaves_the_file(self):
        self.assertEqual([r['url'][:80] for r in self.b.external_requests()], [])

    def test_the_tour_ran_to_its_end(self):
        self.assertFalse(self.b.eval('isTourRunning()'))


# =============================================================================================================
class Embedding(unittest.TestCase):
    """build.py puts data and code into <script> elements; hostile text in either cannot end the element or run."""
    PAGE = ('<!DOCTYPE html>\n<html>\n<head>\n<meta charset="UTF-8">\n<link rel="stylesheet" href="css/a.css">\n</head>\n'
            '<body>\n<script type="application/json" id="d" src="data.json"></script>\n<script src="js/a.js"></script>\n</body>\n</html>\n')
    DATA = {'close': '</script><script>window.__pwn=1</script>', 'comment': '<!-- <script> -->', 'separators': 'a\u2028b\u2029c',
            'escaped': '\\u003c/script\\u003e', 'img': IMG, 'style': '</style><img src=x onerror=window.__pwn=1>'}

    def tree(self, js='window.__d = JSON.parse(document.getElementById("d").textContent);\n', css='body{margin:0}\n', data=None):
        import build
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        (root / 'src' / 'css').mkdir(parents=True)
        (root / 'src' / 'js').mkdir()
        (root / 'VERSION').write_text('0.0.0\n')
        (root / 'src' / 'index.html').write_text(self.PAGE, encoding='utf-8')
        (root / 'src' / 'css' / 'a.css').write_text(css, encoding='utf-8')
        (root / 'src' / 'js' / 'a.js').write_text(js, encoding='utf-8')
        (root / 'src' / 'data.json').write_text(json.dumps(data or self.DATA, ensure_ascii=False), encoding='utf-8')
        saved = build.ROOT, build.SRC
        build.ROOT, build.SRC = str(root), str(root / 'src')
        self.addCleanup(lambda: setattr(build, 'ROOT', saved[0]) or setattr(build, 'SRC', saved[1]))
        return build, root

    def test_hostile_data_round_trips_without_ending_its_element(self):
        build, root = self.tree()
        data, *_ = build.build()
        out = root / 'page.html'
        out.write_bytes(data)
        with Browser() as b:
            b.send('Page.addScriptToEvaluateOnNewDocument', source=HOOK)
            b.goto(file_url(out))
            self.assertEqual(b.eval('document.scripts.length'), 2)
            self.assertEqual(b.eval('window.__d'), self.DATA)
            self.assertIsNone(b.eval('window.__pwn'))
            self.assertEqual(b.eval('window.__csp'), [])
            self.assertEqual(b.eval('document.querySelectorAll("img").length'), 0)

    def test_code_that_could_end_or_confuse_its_element_is_refused(self):
        for js in ('var a = "</script>";\n', 'var a = "</SCRIPT >";\n', 'var a = "<!--"; var b = "<script>";\n', 'var a = "<!--";\n// <Script\n'):
            with self.subTest(js=js[:30]):
                build, _ = self.tree(js=js)
                with self.assertRaises(build.BuildError):
                    build.build()

    def test_a_stylesheet_that_could_end_its_element_is_refused(self):
        for css in ('a{content:"</style><script>x</script>"}\n', 'a{content:"<!--"}\n'):
            with self.subTest(css=css[:30]):
                build, _ = self.tree(css=css)
                with self.assertRaises(build.BuildError):
                    build.build()

    def test_data_that_is_not_json_is_refused(self):
        build, root = self.tree()
        (root / 'src' / 'data.json').write_text('{"a": </script>}', encoding='utf-8')
        with self.assertRaises(build.BuildError):
            build.build()


if __name__ == '__main__':
    sys.path.insert(0, str(REPO))
    unittest.main()
