#!/usr/bin/env python3
"""NetCDF classic (CDF-1, CDF-2, CDF-5) reader and writer in numpy, with CF helpers.

    ds = read(path)                  # or read_bytes(data)
    ds == {'dims': {name: size}, 'unlimited': name or None, 'attrs': {...},
           'vars': {name: {'dims': (dim, ...), 'attrs': {...}, 'data': ndarray}}}

Reading unpacks: scale_factor / add_offset are applied; _FillValue, missing_value, values outside
valid_min / valid_max / valid_range, and the default fill of float variables become NaN in a float
array. Char variables come back as str arrays joined along their last dimension (data.shape is
dims[:-1]). raw=True returns the stored values untouched.

    write(path, dims, vars, attrs=None, fmt='CDF-1')       # dims[name] = None marks the record dimension
    data = write_bytes(dims, vars, attrs=None, fmt='CDF-1')

On write a variable may carry 'dtype' ('i1', 'S1', 'i2', 'i4', 'f4', 'f8'; CDF-5 adds 'u1', 'u2', 'u4',
'i8', 'u8'); otherwise the type follows the data. With an integer dtype and scale_factor or add_offset in
attrs, float data is packed; NaN becomes _FillValue. NaN in float data is written as _FillValue when
the variable has one. NetCDF-4 (HDF5) files are refused with a message.
"""
import math, os, re, struct
from datetime import datetime, timezone

import numpy as np

TYPES = {1: 'i1', 2: 'S1', 3: 'i2', 4: 'i4', 5: 'f4', 6: 'f8', 7: 'u1', 8: 'u2', 9: 'u4', 10: 'i8', 11: 'u8'}
CODES = {v: k for k, v in TYPES.items()}
CLASSIC = {'i1', 'S1', 'i2', 'i4', 'f4', 'f8'}
FMT_VERSION = {'CDF-1': 1, 'CDF-2': 2, 'CDF-5': 5}
DEFAULT_FILL = {'f4': np.float32(9.9692099683868690e+36), 'f8': 9.9692099683868690e+36}
TAG_DIM, TAG_VAR, TAG_ATT = 10, 11, 12
TYPED_ATTRS = ('_FillValue', 'missing_value', 'valid_min', 'valid_max', 'valid_range')


class NetCDFError(ValueError):
    """The bytes are not a NetCDF classic file this module can read, or the data cannot be written."""


def pad4(n):
    """Bytes of padding that bring n up to a multiple of 4."""
    return (4 - n % 4) % 4


# ------------------------------------------------------------------ reading
class _Reader:
    """A big-endian cursor over the header bytes."""
    def __init__(self, data):
        self.b, self.o = data, 0

    def take(self, n):
        if self.o + n > len(self.b):
            raise NetCDFError('file ends inside the header')
        s = self.b[self.o:self.o + n]
        self.o += n
        return s

    def i4(self):
        return struct.unpack('>i', self.take(4))[0]

    def u(self, size):
        return struct.unpack('>Q' if size == 8 else '>I', self.take(size))[0]

    def name(self, nsz):
        n = self.u(nsz)
        s = self.take(n).decode('utf-8', 'replace')
        self.take(pad4(n))
        return s


def _attr_value(t, raw):
    """Raw attribute bytes -> str, a scalar or an array."""
    if t == 'S1':
        return raw.decode('utf-8', 'replace').rstrip('\x00')
    a = np.frombuffer(raw, '>' + t).astype(t)
    return a[0].item() if len(a) == 1 else a


def _read_attrs(r, nsz):
    """One attribute list from the header -> dict."""
    tag, n = r.i4(), r.u(nsz)
    if tag == 0 and n == 0:
        return {}
    if tag != TAG_ATT:
        raise NetCDFError(f'bad attribute list tag {tag}')
    out = {}
    for _ in range(n):
        name = r.name(nsz)
        code = r.i4()
        if code not in TYPES:
            raise NetCDFError(f'unknown attribute type {code} for {name}')
        t = TYPES[code]
        cnt = r.u(nsz)
        size = cnt * np.dtype(t).itemsize
        out[name] = _attr_value(t, r.take(size))
        r.take(pad4(size))
    return out


def read_bytes(data, raw=False):
    """NetCDF classic bytes -> the dict described at the top of this module; NetCDFError for anything else."""
    data = bytes(data)
    if data[:4] == b'\x89HDF' or data[:8] == b'\x89HDF\r\n\x1a\n':
        raise NetCDFError('NetCDF-4 file: convert it to NetCDF-3 first (e.g. nccopy -k classic, or '
                          'copernicusmarine subset --netcdf3-compatible)')
    if data[:3] != b'CDF' or len(data) < 8 or data[3] not in (1, 2, 5):
        raise NetCDFError('not a NetCDF classic file')
    ver = data[3]
    nsz = 8 if ver == 5 else 4                       # NON_NEG and dimension ids
    osz = 4 if ver == 1 else 8                       # variable begin offsets
    r = _Reader(data)
    r.o = 4
    numrecs = r.u(nsz)
    streaming = numrecs == (1 << (8 * nsz)) - 1

    tag, n = r.i4(), r.u(nsz)
    dims, unlimited = [], None
    if not (tag == 0 and n == 0):
        if tag != TAG_DIM:
            raise NetCDFError(f'bad dimension list tag {tag}')
        for _ in range(n):
            nm, size = r.name(nsz), r.u(nsz)
            if size == 0:
                unlimited = nm
            dims.append([nm, size])
    gattrs = _read_attrs(r, nsz)

    tag, n = r.i4(), r.u(nsz)
    specs = []
    if not (tag == 0 and n == 0):
        if tag != TAG_VAR:
            raise NetCDFError(f'bad variable list tag {tag}')
        for _ in range(n):
            nm = r.name(nsz)
            ids = [r.u(nsz) for _ in range(r.u(nsz))]
            attrs = _read_attrs(r, nsz)
            code = r.i4()
            if code not in TYPES:
                raise NetCDFError(f'unknown type {code} for variable {nm}')
            vsize, begin = r.u(nsz), r.u(osz)
            if any(i >= len(dims) for i in ids):
                raise NetCDFError(f'variable {nm} uses an undefined dimension')
            specs.append((nm, ids, attrs, TYPES[code], vsize, begin))

    rec = [s for s in specs if s[1] and dims[s[1][0]][0] == unlimited]

    def slab(s):
        shape = [dims[i][1] for i in s[1][1:]] if s in rec else [dims[i][1] for i in s[1]]
        return math.prod(shape) * np.dtype(s[3]).itemsize       # Python ints: sizes from the file cannot wrap

    recsize = slab(rec[0]) if len(rec) == 1 else sum(slab(s) + pad4(slab(s)) for s in rec)
    if rec and streaming:
        numrecs = max(0, (len(data) - min(s[5] for s in rec)) // recsize) if recsize else 0
    if unlimited is not None:
        for d in dims:
            if d[0] == unlimited:
                d[1] = numrecs

    out = {'dims': {d[0]: d[1] for d in dims}, 'unlimited': unlimited, 'attrs': gattrs, 'vars': {}}
    claimed = 0                                      # bytes the variables claim; in a real file they never overlap
    for s in specs:
        nm, ids, attrs, t, vsize, begin = s
        shape = [dims[i][1] for i in ids]
        dt = np.dtype('>' + t) if t != 'S1' else np.dtype('S1')
        per = slab(s)
        if s in rec:
            if begin + (numrecs - 1) * recsize + per > len(data) and numrecs:
                raise NetCDFError(f'file ends inside record variable {nm}')
        elif begin + per > len(data):
            raise NetCDFError(f'file ends inside variable {nm}')
        claimed += per * (numrecs if s in rec else 1)
        if claimed > len(data):                      # overlapping variables would multiply a small file into gigabytes
            raise NetCDFError(f'variables overlap or claim more data than the file holds (at {nm})')
        if s in rec:
            # no bytes per record (a zero-size inner dimension): nothing to copy, and numrecs may be absurd
            buf = b''.join(data[begin + k * recsize: begin + k * recsize + per] for k in range(numrecs)) if per else b''
        else:
            buf = data[begin:begin + per]
        try:
            a = np.frombuffer(buf, dt).reshape(shape)
            a = a.astype(t) if t != 'S1' else a
            value = a.copy() if raw else _unpack(a, t, attrs)
        except (TypeError, ValueError, IndexError, OverflowError) as e:      # e.g. a text scale_factor
            raise NetCDFError(f'variable {nm} cannot be decoded ({type(e).__name__}: {e})') from e
        out['vars'][nm] = {'dims': tuple(dims[i][0] for i in ids), 'attrs': attrs, 'data': value}
    return out


def read(path, raw=False):
    """read_bytes of a file."""
    with open(path, 'rb') as f:
        return read_bytes(f.read(), raw)


def _unpack(a, t, attrs):
    """Stored values -> what read() returns: fill and out-of-range values become NaN, then scale_factor
    and add_offset apply. Char arrays become strings."""
    if t == 'S1':
        if a.ndim == 0:
            return np.array(a.tobytes().decode('utf-8', 'replace').rstrip('\x00 '))
        flat = a.reshape(-1, a.shape[-1]) if a.shape[-1] else a.reshape(-1, 1)
        strs = [row.tobytes().decode('utf-8', 'replace').rstrip('\x00 ') for row in flat]
        return np.array(strs, dtype=str).reshape(a.shape[:-1])
    if str(attrs.get('_Unsigned', '')).lower() == 'true' and t in ('i1', 'i2', 'i4', 'i8'):
        a = a.view('u' + t[1:])
    bad = np.zeros(a.shape, bool)
    fills = [attrs[k] for k in ('_FillValue', 'missing_value') if k in attrs]
    for f in fills:
        for v in np.atleast_1d(f):
            bad |= a == v
    if not fills and t in DEFAULT_FILL:
        bad |= a == DEFAULT_FILL[t]
    vr = attrs.get('valid_range')
    lo = vr[0] if vr is not None and np.size(vr) == 2 else attrs.get('valid_min')
    hi = vr[1] if vr is not None and np.size(vr) == 2 else attrs.get('valid_max')
    with np.errstate(invalid='ignore'):
        if lo is not None:
            bad |= a < lo
        if hi is not None:
            bad |= a > hi
    sc, off = attrs.get('scale_factor'), attrs.get('add_offset')
    if sc is None and off is None and not bad.any():
        return a
    out = a.astype('f4' if t == 'f4' and sc is None and off is None else 'f8')
    if sc is not None:
        out = out * float(np.atleast_1d(sc)[0])
    if off is not None:
        out = out + float(np.atleast_1d(off)[0])
    out[bad] = np.nan
    return out


# ------------------------------------------------------------------ writing
def _attr_bytes(name, value, vtype=None):
    """An attribute -> (type, count, bytes); _FillValue and valid_* take the variable's type."""
    if isinstance(value, (str, bytes)):
        b = value.encode('utf-8') if isinstance(value, str) else value
        return 'S1', len(b), b
    a = np.asarray(value)
    if name in TYPED_ATTRS and vtype and vtype != 'S1':
        t = vtype
    elif a.dtype.kind == 'b':
        t = 'i4'
    elif a.dtype.kind in 'iu':
        t = a.dtype.str[1:] if a.dtype.str[1:] in CODES else 'i4'
        if t == 'i4' and a.size and (a.min() < -2**31 or a.max() >= 2**31):
            t = 'f8'
    elif a.dtype.kind == 'f':
        t = 'f4' if a.dtype == np.float32 else 'f8'
    else:
        raise NetCDFError(f'attribute {name}: cannot store {type(value).__name__}')
    a = np.atleast_1d(a).astype('>' + t)
    return t, a.size, a.tobytes()


def _var_type(v, fmt):
    """The type a variable is written as: its 'dtype', else from the data; NetCDFError if fmt lacks it."""
    t = v.get('dtype')
    a = v['data']
    if t is None:
        a = np.asarray(a)
        if a.dtype.kind in 'US':
            t = 'S1'
        elif a.dtype.kind == 'b':
            t = 'i1'
        elif a.dtype.kind in 'iu':
            t = a.dtype.str[1:]
            if fmt != 'CDF-5' and t not in CLASSIC:
                t = 'i4' if t in ('u1', 'u2') or (a.size == 0 or (a.min() >= -2**31 and a.max() < 2**31)) else 'f8'
        elif a.dtype.kind == 'f':
            t = 'f4' if a.dtype == np.float32 else 'f8'
        else:
            raise NetCDFError(f'cannot store a {a.dtype} array')
    t = np.dtype(t).str[1:] if t != 'S1' else t
    if t not in CODES or (fmt != 'CDF-5' and t not in CLASSIC):
        raise NetCDFError(f'type {t} is not available in {fmt}')
    return t


def _encode(name, v, t, shape):
    """A variable's data as big-endian bytes, packed with scale_factor / add_offset for an integer type."""
    a, attrs = v['data'], v.get('attrs') or {}
    if t == 'S1':
        a = np.asarray(a)
        n = shape[-1] if shape else 1
        if a.dtype.kind == 'U':
            a = np.char.encode(a, 'utf-8')
        if a.dtype.kind == 'S' and a.dtype.itemsize != 1 or list(a.shape) != list(shape):
            flat = a.reshape(-1)
            buf = bytearray()
            for s in flat:
                s = bytes(s)[:n]
                buf += s + b'\x00' * (n - len(s))
            a = np.frombuffer(bytes(buf), 'S1')
        out = np.ascontiguousarray(a, 'S1')
        if out.size != int(np.prod(shape, dtype=np.int64)):
            raise NetCDFError(f'{name}: data does not fit dims {shape}')
        return out.tobytes()
    a = np.asarray(a)
    if t[0] in 'iu' and a.dtype.kind == 'f':
        sc = float(np.atleast_1d(attrs.get('scale_factor', 1.0))[0])
        off = float(np.atleast_1d(attrs.get('add_offset', 0.0))[0])
        fill = attrs.get('_FillValue')
        q = (a - off) / sc
        nan = ~np.isfinite(q)
        if nan.any():
            if fill is None:
                raise NetCDFError(f'{name}: NaN in data packed to {t} without _FillValue')
            q = np.where(nan, fill, q)
        info = np.iinfo(t)
        q = np.round(q)
        if q.size and (q.min() < info.min or q.max() > info.max):
            raise NetCDFError(f'{name}: values do not fit {t} with this scale_factor/add_offset')
        a = q
    elif t[0] == 'f' and '_FillValue' in attrs:
        a = np.where(np.isnan(a), attrs['_FillValue'], a)
    if a.size != int(np.prod(shape, dtype=np.int64)):
        raise NetCDFError(f'{name}: {a.size} values do not fit dims {shape}')
    return np.ascontiguousarray(a.reshape(shape), '>' + t).tobytes()


def write_bytes(dims, vars, attrs=None, fmt='CDF-1'):
    """dims, vars and global attrs -> NetCDF classic bytes (see the top of this module)."""
    if fmt not in FMT_VERSION:
        raise NetCDFError(f'unknown format {fmt}; use CDF-1, CDF-2 or CDF-5')
    ver = FMT_VERSION[fmt]
    nsz, osz = (8 if ver == 5 else 4), (4 if ver == 1 else 8)
    names = list(dims)
    recdims = [d for d in names if dims[d] is None]
    if len(recdims) > 1:
        raise NetCDFError('only one record dimension is allowed')
    recdim = recdims[0] if recdims else None
    numrecs = 0
    for v in vars.values():
        if v['dims'] and v['dims'][0] == recdim:
            numrecs = max(numrecs, np.shape(v['data'])[0] if np.ndim(v['data']) else 1)
    size = {d: (numrecs if dims[d] is None else int(dims[d])) for d in names}
    for nm, v in vars.items():
        for i, d in enumerate(v['dims']):
            if d not in size:
                raise NetCDFError(f'variable {nm} uses undefined dimension {d}')
            if d == recdim and i:
                raise NetCDFError(f'variable {nm}: the record dimension must come first')

    def u(n, s):
        return struct.pack('>Q' if s == 8 else '>I', n)

    def name(s):
        b = s.encode('utf-8')
        return u(len(b), nsz) + b + b'\x00' * pad4(len(b))

    def attr_list(at, vtype=None):
        if not at:
            return b'\x00' * 4 + u(0, nsz)
        out = struct.pack('>i', TAG_ATT) + u(len(at), nsz)
        for k, val in at.items():
            t, n, b = _attr_bytes(k, val, vtype)
            out += name(k) + struct.pack('>i', CODES[t]) + u(n, nsz) + b + b'\x00' * pad4(len(b))
        return out

    # payloads first, so the header knows every size
    items = []
    for nm, v in vars.items():
        t = _var_type(v, fmt)
        shape = [size[d] for d in v['dims']]
        isrec = bool(v['dims']) and v['dims'][0] == recdim
        body = _encode(nm, v, t, shape)
        per = len(body) // numrecs if isrec and numrecs else len(body)
        items.append([nm, v, t, isrec, body, per])
    recs = [it for it in items if it[3]]
    single = len(recs) == 1

    head = b'CDF' + bytes([ver]) + u(numrecs, nsz)
    head += (struct.pack('>i', TAG_DIM) + u(len(names), nsz) + b''.join(
        name(d) + u(0 if dims[d] is None else size[d], nsz) for d in names)) if names else b'\x00' * 4 + u(0, nsz)
    head += attr_list(attrs or {})

    def var_entries(begins):
        if not items:
            return b'\x00' * 4 + u(0, nsz)
        out = struct.pack('>i', TAG_VAR) + u(len(items), nsz)
        for (nm, v, t, isrec, body, per), b in zip(items, begins):
            vsize = per + (0 if isrec and single else pad4(per))
            out += name(nm) + u(len(v['dims']), nsz) + b''.join(u(names.index(d), nsz) for d in v['dims'])
            out += attr_list(v.get('attrs') or {}, t) + struct.pack('>i', CODES[t])
            out += u(min(vsize, (1 << 32) - 1) if nsz == 4 else vsize, nsz) + u(b, osz)
        return out

    hlen = len(head) + len(var_entries([0] * len(items)))
    begins, pos = [], hlen
    for it in items:
        if not it[3]:
            begins.append(pos)
            pos += len(it[4]) + pad4(len(it[4]))
        else:
            begins.append(None)
    recstart = pos
    for i, it in enumerate(items):
        if it[3]:
            begins[i] = pos
            pos += it[5] + (0 if single else pad4(it[5]))
    if ver == 1 and pos >= 2**31:
        raise NetCDFError('data too large for CDF-1; use fmt="CDF-2"')
    out = bytearray(head + var_entries(begins))
    for it in items:
        if not it[3]:
            out += it[4] + b'\x00' * pad4(len(it[4]))
    assert len(out) == recstart
    for k in range(numrecs):
        for it in recs:
            chunk = it[4][k * it[5]:(k + 1) * it[5]]
            out += chunk + (b'' if single else b'\x00' * pad4(len(chunk)))
    return bytes(out)


def write(path, dims, vars, attrs=None, fmt='CDF-1'):
    """write_bytes to path through a temporary file; returns the size in bytes."""
    data = write_bytes(dims, vars, attrs, fmt)
    tmp = path + '.part'
    with open(tmp, 'wb') as f:
        f.write(data)
    os.replace(tmp, path)
    return len(data)


# ------------------------------------------------------------------ CF helpers
AXIS_HINTS = {
    'X': ({'longitude', 'grid_longitude', 'projection_x_coordinate'}, ('degrees_east', 'degree_east', 'degree_e',
          'degrees_e'), ('lon', 'longitude', 'x', 'nav_lon', 'long')),
    'Y': ({'latitude', 'grid_latitude', 'projection_y_coordinate'}, ('degrees_north', 'degree_north', 'degree_n',
          'degrees_n'), ('lat', 'latitude', 'y', 'nav_lat')),
    'Z': ({'depth', 'altitude', 'height', 'sea_water_pressure', 'depth_below_sea_surface'}, (),
          ('depth', 'lev', 'z', 'deptht', 'level', 'pres', 'pressure')),
    'T': ({'time'}, (), ('time', 't', 'juld')),
}


def cf_find(ds, standard_names, names=()):
    """Name of the first variable whose standard_name is in standard_names, else the first of names
    present (case-insensitive), else None."""
    sn = set(standard_names)
    for nm, v in ds['vars'].items():
        if str(v['attrs'].get('standard_name', '')) in sn:
            return nm
    low = {nm.lower(): nm for nm in ds['vars']}
    for n in names:
        if n.lower() in low:
            return low[n.lower()]
    return None


def cf_axis(ds, axis, var=None):
    """Coordinate variable for axis 'X', 'Y', 'Z' or 'T', preferring one of var's dimensions."""
    sns, units, names = AXIS_HINTS[axis]
    cands = [d for d in ds['vars'][var]['dims'] if d in ds['vars']] if var else []
    cands += [n for n in ds['vars'] if n not in cands]
    for test in (lambda a, n: str(a.get('axis', '')).upper() == axis,
                 lambda a, n: str(a.get('standard_name', '')) in sns,
                 lambda a, n: str(a.get('units', '')).lower() in units if units else
                 (axis == 'T' and ' since ' in str(a.get('units', '')).lower()),
                 lambda a, n: n.lower() in names):
        for n in cands:
            v = ds['vars'][n]
            if (len(v['dims']) <= 1 or axis in 'XY') and test(v['attrs'], n):
                return n
    return None


_UNIT_SECONDS = {'second': 1, 'sec': 1, 's': 1, 'minute': 60, 'min': 60, 'hour': 3600, 'hr': 3600, 'h': 3600,
                 'day': 86400, 'd': 86400}


def cf_time_epoch(values, units, calendar='standard'):
    """CF time values ('<unit> since <date>') -> seconds since 1970-01-01 UTC as float64."""
    if str(calendar).lower() not in ('standard', 'gregorian', 'proleptic_gregorian', 'julian', ''):
        raise NetCDFError(f'calendar {calendar} is not supported')
    m = re.match(r'\s*(\w+?)s?\s+since\s+(\d{1,4})-(\d{1,2})-(\d{1,2})(?:[ T](\d{1,2}):(\d{1,2})(?::(\d{1,2}(?:\.\d*)?))?)?'
                 r'\s*(Z|UTC|[+-]00:?00)?\s*$', str(units), re.I)
    if not m or m.group(1).lower() not in _UNIT_SECONDS:
        raise NetCDFError(f'time units {units!r} are not "<unit> since <date>"')
    y, mo, d, hh, mi, ss = m.group(2, 3, 4, 5, 6, 7)
    sec = float(ss or 0)
    base = datetime(int(y), int(mo), int(d), int(hh or 0), int(mi or 0), int(sec), tzinfo=timezone.utc)
    return base.timestamp() + (sec - int(sec)) + np.asarray(values, 'f8') * _UNIT_SECONDS[m.group(1).lower()]


def cf_times(var):
    """A time variable from read() -> list of timezone-aware datetimes (NaN -> None)."""
    a = var['attrs']
    secs = cf_time_epoch(var['data'], a.get('units', ''), a.get('calendar', 'standard'))
    try:
        return [None if not np.isfinite(s) else datetime.fromtimestamp(float(s), timezone.utc) for s in np.ravel(secs)]
    except (OverflowError, OSError, ValueError) as e:                 # a time far outside the calendar
        raise NetCDFError(f'time values outside the supported calendar range ({type(e).__name__}: {e})') from e


def cf_depth(var):
    """A vertical coordinate as positive-down metres (altitude / positive="up" negated, km scaled)."""
    a, z = var['attrs'], np.asarray(var['data'], 'f8')
    if str(a.get('units', '')).lower() in ('km', 'kilometers', 'kilometres'):
        z = z * 1000
    if str(a.get('positive', '')).lower() == 'up' or a.get('standard_name') in ('altitude', 'height'):
        z = -z
    return z
