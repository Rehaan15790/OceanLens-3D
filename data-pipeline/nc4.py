#!/usr/bin/env python3
"""NetCDF-4 (HDF5) reader in numpy and zlib: the subset of HDF5 that netCDF-C writes.

    ds = read_bytes(data)            # or read(path): the same dict as ncio.read_bytes()
    f = open_source(source)          # bytes, a path, or an object with .size and .read(offset, n)
    f.dims, f.unlimited, f.attrs     # {name: size}, name or None, {name: value}
    f.vars[name].dims, .shape, .attrs
    f.vars[name].read((slice(0, 1), 3, slice(None)), raw=False)   # only the chunks the selection touches

Values unpack as in ncio: _FillValue, missing_value and the valid range become NaN, then scale_factor and
add_offset apply; raw=True returns the stored values. NC_CHAR arrays come back as str arrays joined along their last
dimension, NC_STRING arrays as str arrays.

What is read: superblocks 0-3; object headers 1 and 2 with continuation blocks; groups by symbol table (v1 B-tree and
local heap) or by link messages, compact or dense (fractal heap with a v2 B-tree name index); attributes, compact or
dense; dataspaces; fixed-point, floating-point, string, variable-length string and sequence, reference and compound
datatypes; fill values; compact, contiguous and chunked layouts, the chunks indexed by a v1 B-tree, a single chunk, an
implicit index, a fixed array, an extensible array or a v2 B-tree; the deflate, shuffle and fletcher32 filters.
Dimensions come from the dimension scales netCDF-C writes (DIMENSION_LIST, CLASS, NAME); a file without them gets
phony_dim_<n> dimensions, as netCDF-C names them.

Not read, refused with NetCDFError: other filters (szip, zstd, ...), shared (committed) datatypes in attributes, huge
fractal-heap objects, paged extensible-array data blocks, external links and external storage. Every offset and size
is checked against the file, decoded bytes are capped (max_bytes), and loops in the file's structures are refused, so
hostile input raises NetCDFError and nothing else.
"""
import os, struct, zlib

import numpy as np

from ncio import NetCDFError, _unpack

SIGNATURE = b'\x89HDF\r\n\x1a\n'
UNDEF = object()                    # an undefined address (all bits set)
MAX_BYTES = 1 << 30                 # decoded bytes one read may produce, by default
MAX_DEPTH = 64                      # B-tree and heap levels
MAX_MESSAGES = 1 << 16
MAX_CHUNKS = 1 << 22                # chunks one variable may index
HIDDEN_ATTRS = {'DIMENSION_LIST', 'REFERENCE_LIST', 'CLASS', 'NAME', '_Netcdf4Dimid', '_Netcdf4Coordinates',
                '_NCProperties', '_nc3_strict', '_IsNetcdf4', '_SuperblockVersion'}
NOT_A_VARIABLE = 'This is a netCDF dimension but not a netCDF variable'
NON_COORD = '_nc4_non_coord_'


# ------------------------------------------------------------------ the bytes
class BytesSource:
    """Random access to bytes in memory."""
    def __init__(self, data):
        self.data = memoryview(bytes(data))
        self.size = len(self.data)

    def read(self, offset, n):
        return bytes(self.data[offset:offset + n])


class FileSource:
    """Random access to a local file, without reading all of it."""
    def __init__(self, path):
        self.f = open(path, 'rb')
        self.size = os.fstat(self.f.fileno()).st_size

    def read(self, offset, n):
        self.f.seek(offset)
        return self.f.read(n)

    def close(self):
        self.f.close()


class _Src:
    """A source with bounds checks: a read past the end raises NetCDFError, not a short result."""
    def __init__(self, src):
        self.src, self.size = src, int(src.size)

    def read(self, offset, n, what='a structure'):
        if offset is UNDEF or offset < 0 or n < 0 or offset + n > self.size:
            raise NetCDFError(f'file ends inside {what} (offset {offset if offset is not UNDEF else "undefined"}, '
                              f'{n} bytes, file {self.size} bytes)')
        b = self.src.read(offset, n)
        if len(b) != n:
            raise NetCDFError(f'short read inside {what}')
        return b


class _Cur:
    """A little-endian cursor over one structure's bytes."""
    def __init__(self, b, pos=0, osz=8, lsz=8, what='a structure'):
        self.b, self.p, self.osz, self.lsz, self.what = b, pos, osz, lsz, what

    def take(self, n):
        if n < 0 or self.p + n > len(self.b):
            raise NetCDFError(f'{self.what} is cut short')
        s = self.b[self.p:self.p + n]
        self.p += n
        return s

    def u(self, n):
        return int.from_bytes(self.take(n), 'little') if n else 0

    def u8(self):
        return self.u(1)

    def u16(self):
        return self.u(2)

    def u32(self):
        return self.u(4)

    def u64(self):
        return self.u(8)

    def addr(self):
        v = self.take(self.osz)
        return UNDEF if v == b'\xff' * self.osz else int.from_bytes(v, 'little')

    def length(self):
        v = self.take(self.lsz)
        return UNDEF if v == b'\xff' * self.lsz else int.from_bytes(v, 'little')

    def sig(self, s):
        got = self.take(4)
        if got != s:
            raise NetCDFError(f'expected {s.decode()} in {self.what}, found {got!r}')

    def align(self, base, n=8):
        self.p += (n - (self.p - base) % n) % n

    def cstr(self):
        end = self.b.find(b'\x00', self.p)
        if end < 0:
            raise NetCDFError(f'unterminated name in {self.what}')
        s = self.b[self.p:end].decode('utf-8', 'replace')
        self.p = end + 1
        return s


def lookup3(data, initval=0):
    """Bob Jenkins' lookup3 hashlittle, the checksum of HDF5's newer metadata."""
    M = 0xffffffff

    def rot(x, k):
        return ((x << k) | (x >> (32 - k))) & M
    n = len(data)
    a = b = c = (0xdeadbeef + n + initval) & M
    k = 0
    while n > 12:
        a = (a + int.from_bytes(data[k:k + 4], 'little')) & M
        b = (b + int.from_bytes(data[k + 4:k + 8], 'little')) & M
        c = (c + int.from_bytes(data[k + 8:k + 12], 'little')) & M
        a = (a - c) & M; a ^= rot(c, 4); c = (c + b) & M
        b = (b - a) & M; b ^= rot(a, 6); a = (a + c) & M
        c = (c - b) & M; c ^= rot(b, 8); b = (b + a) & M
        a = (a - c) & M; a ^= rot(c, 16); c = (c + b) & M
        b = (b - a) & M; b ^= rot(a, 19); a = (a + c) & M
        c = (c - b) & M; c ^= rot(b, 4); b = (b + a) & M
        n -= 12
        k += 12
    if n == 0:
        return c
    tail = data[k:k + n] + b'\x00' * (12 - n)
    a = (a + int.from_bytes(tail[0:4], 'little')) & M
    b = (b + int.from_bytes(tail[4:8], 'little')) & M
    c = (c + int.from_bytes(tail[8:12], 'little')) & M
    c ^= b; c = (c - rot(b, 14)) & M
    a ^= c; a = (a - rot(c, 11)) & M
    b ^= a; b = (b - rot(a, 25)) & M
    c ^= b; c = (c - rot(b, 16)) & M
    a ^= c; a = (a - rot(c, 4)) & M
    b ^= a; b = (b - rot(a, 14)) & M
    c ^= b; c = (c - rot(b, 24)) & M
    return c


def _enc_size(n):
    """Bytes HDF5 uses to encode counts up to n (H5VM_limit_enc_size)."""
    return (max(int(n), 1).bit_length() - 1) // 8 + 1


# ------------------------------------------------------------------ datatypes
class DType:
    """A decoded HDF5 datatype: cls, size, and per class np (numpy dtype), base, members, vlen_string, char."""
    def __init__(self, cls, size):
        self.cls, self.size = cls, size
        self.np = None
        self.base = None
        self.members = []
        self.vlen_string = False
        self.sign = False

    @property
    def code(self):
        """The ncio type name of a numeric or char type ('i2', 'f4', 'S1'), else None."""
        if self.cls == 3 and self.size == 1:
            return 'S1'
        if self.np is not None and self.np.kind in 'iuf':
            return f'{self.np.kind}{self.np.itemsize}'
        return None


def _datatype(c):
    """Parse a datatype message at the cursor."""
    b0 = c.u8()
    cls, ver = b0 & 0x0f, b0 >> 4
    bits = c.u(3)
    size = c.u32()
    if size > 1 << 20:
        raise NetCDFError(f'datatype size {size} is not plausible')
    t = DType(cls, size)
    order = '>' if bits & 1 else '<'
    if cls == 0:                                          # fixed-point
        c.take(4)
        if size not in (1, 2, 4, 8):
            raise NetCDFError(f'integers of {size} bytes are not supported')
        t.sign = bool(bits & 0x08)
        t.np = np.dtype(f'{order}{"i" if t.sign else "u"}{size}')
    elif cls == 1:                                        # floating-point
        c.take(12)
        if size not in (2, 4, 8) or bits & 0x40:
            raise NetCDFError(f'floats of {size} bytes (or VAX order) are not supported')
        t.np = np.dtype(f'{order}f{size}')
    elif cls == 3:                                        # fixed-length string
        t.np = np.dtype(f'S{size}')
    elif cls in (2, 4, 5):                                # time, bitfield, opaque: kept as bytes
        if cls == 4:
            c.take(4)
        elif cls == 5:
            tag_len = bits & 0xff
            c.take(tag_len)
        t.np = np.dtype(f'V{size}')
    elif cls == 6:                                        # compound
        n = bits & 0xffff
        names, formats, offsets = [], [], []
        start_pos = None
        for _ in range(n):
            start_pos = c.p
            name = c.cstr()
            if ver < 3:
                c.align(start_pos)
            if ver == 1:
                off = c.u32()
                c.take(1 + 3 + 4 + 4 + 16)               # dimensionality, reserved, permutation, reserved, sizes
            elif ver == 2:
                off = c.u32()
            else:
                off = c.u(_enc_size(size) if size > 0 else 1)
            m = _datatype(c)
            t.members.append((name, off, m))
            names.append(name)
            formats.append(m.np if m.np is not None else np.dtype(f'V{m.size}'))
            offsets.append(off)
        try:
            t.np = np.dtype({'names': names, 'formats': formats, 'offsets': offsets, 'itemsize': size})
        except (ValueError, TypeError) as e:
            raise NetCDFError(f'compound datatype cannot be decoded ({e})') from e
    elif cls == 7:                                        # reference
        if ver >= 4:
            raise NetCDFError('new-style (version 4) references are not supported')
        t.np = np.dtype(f'<u{size}') if size in (1, 2, 4, 8) else np.dtype(f'V{size}')
    elif cls == 8:                                        # enum: read as its base integer type
        n = bits & 0xffff
        base = _datatype(c)
        for _ in range(n):
            p0 = c.p
            c.cstr()
            if ver < 3:
                c.align(p0)
        c.take(n * base.size)
        t.np, t.sign = base.np, base.sign
    elif cls == 9:                                        # variable-length
        t.vlen_string = (bits & 0x0f) == 1
        t.base = _datatype(c)
    elif cls == 10:                                       # array
        rank = c.u8()
        if ver < 3:
            c.take(3)
        dims = [c.u32() for _ in range(rank)]
        if ver < 3:
            c.take(4 * rank)
        base = _datatype(c)
        if base.np is None:
            raise NetCDFError('arrays of this element type are not supported')
        t.np = np.dtype((base.np, tuple(dims)))
    else:
        raise NetCDFError(f'datatype class {cls} is not supported')
    return t


def _dataspace(c):
    """Parse a dataspace message -> (dims, maxdims) with None for an unlimited maximum; dims is None for null."""
    ver = c.u8()
    rank = c.u8()
    flags = c.u8()
    kind = 1
    if ver == 1:
        c.take(5)
    elif ver == 2:
        kind = c.u8()
    else:
        raise NetCDFError(f'dataspace version {ver} is not supported')
    if rank > 32:
        raise NetCDFError(f'{rank} dimensions is not plausible')
    dims = [c.length() for _ in range(rank)]
    maxd = [c.length() for _ in range(rank)] if flags & 1 else list(dims)
    if any(d is UNDEF for d in dims):
        raise NetCDFError('a dimension size is undefined')
    maxd = [None if m is UNDEF else m for m in maxd]
    if kind == 2:
        return None, None
    return dims, maxd


# ------------------------------------------------------------------ the file
class Var:
    """One netCDF variable: dims, shape, attrs, and read(selection)."""
    def __init__(self, f, name, obj):
        self.f, self.name, self.obj = f, name, obj
        self.dims = ()
        self.attrs = {}

    @property
    def shape(self):
        return tuple(self.obj.dims)

    def read(self, sel=None, raw=False, max_bytes=None):
        a = self.f._read_data(self.obj, sel, max_bytes)
        t = self.obj.dtype
        if t.cls == 3 and t.size == 1:
            return _unpack(a.view('S1') if a.dtype != np.dtype('S1') else a, 'S1', self.attrs)
        if t.cls == 3:
            return np.array([x.decode('utf-8', 'replace').rstrip('\x00 ') for x in a.ravel()],
                            dtype=str).reshape(a.shape)
        if t.cls == 9 and t.vlen_string:
            return a
        code = t.code
        if code is None or raw:
            return a
        a = a.astype(code)
        try:
            return _unpack(a, code, self.attrs)
        except (TypeError, ValueError, IndexError, OverflowError) as e:
            raise NetCDFError(f'variable {self.name} cannot be decoded ({type(e).__name__}: {e})') from e


class _Obj:
    """A parsed object header: its messages, and for a dataset its dataspace, datatype, layout and filters."""
    def __init__(self, addr):
        self.addr = addr
        self.msgs = []                 # (type, bytes)
        self.attrs = {}
        self.dims = self.maxdims = None
        self.dtype = None
        self.layout = None
        self.filters = []
        self.fill = None
        self.links = None              # a group: {name: address}

    @property
    def is_dataset(self):
        return self.layout is not None


class File:
    """An open NetCDF-4 file: dims, unlimited, attrs, vars."""
    def __init__(self, source, max_bytes=MAX_BYTES):
        self.s = _Src(source)
        self.max_bytes = max_bytes
        self.objs = {}
        self.gheaps = {}
        self._superblock()
        root = self._object(self.root_addr)
        self.datasets = {}             # full path -> _Obj
        self._walk(root, '', set())
        self._netcdf(root)

    # ---------------------------------------------------------- structure
    def _superblock(self):
        off = None
        for o in [0] + [512 << k for k in range(40)]:
            if o + 8 > self.s.size:
                break
            if self.s.read(o, 8) == SIGNATURE:
                off = o
                break
        if off is None:
            raise NetCDFError('not an HDF5 file')
        c = _Cur(self.s.read(off, min(256, self.s.size - off)), 8, what='the superblock')
        ver = c.u8()
        if ver in (0, 1):
            c.take(4)
            self.osz, self.lsz = c.u8(), c.u8()
            c.take(1 + 4 + 4)
            if ver == 1:
                c.take(4)
            self._sizes()
            c.osz, c.lsz = self.osz, self.lsz
            base = c.addr()
            c.addr(); c.addr(); c.addr()                  # free space, end of file, driver
            c.addr()                                      # root entry: link name offset
            root = c.addr()
        elif ver in (2, 3):
            self.osz, self.lsz = c.u8(), c.u8()
            self._sizes()
            c.osz, c.lsz = self.osz, self.lsz
            c.u8()
            base = c.addr()
            c.addr(); c.addr()                            # superblock extension, end of file
            root = c.addr()
        else:
            raise NetCDFError(f'HDF5 superblock version {ver} is not supported')
        self.base = off if base is UNDEF else base
        if root is UNDEF:
            raise NetCDFError('the root group address is undefined')
        self.root_addr = root
        self.sb_version = ver

    def _sizes(self):
        if self.osz not in (2, 4, 8) or self.lsz not in (2, 4, 8):
            raise NetCDFError(f'offset size {self.osz} / length size {self.lsz} is not supported')

    def _cur(self, b, pos=0, what='a structure'):
        return _Cur(b, pos, self.osz, self.lsz, what)

    def _at(self, addr):
        """A file address -> an offset in the source."""
        if addr is UNDEF:
            return UNDEF
        return addr + self.base

    def _object(self, addr):
        """Parse the object header at addr (cached)."""
        if addr in self.objs:
            return self.objs[addr]
        if len(self.objs) > 1 << 20:
            raise NetCDFError('too many objects')
        o = _Obj(addr)
        self.objs[addr] = o
        at = self._at(addr)
        if at is UNDEF or not 0 <= at < self.s.size:
            raise NetCDFError(f'object header address {addr} is outside the file')
        head = self.s.read(at, min(64, self.s.size - at), 'an object header')
        if head[:4] == b'OHDR':
            self._ohdr_v2(o, at)
        elif head[:1] == b'\x01':
            self._ohdr_v1(o, at)
        else:
            raise NetCDFError(f'no object header at {addr}')
        self._interpret(o)
        return o

    def _ohdr_v1(self, o, at):
        c = self._cur(self.s.read(at, 16, 'an object header'), what='an object header')
        c.u8(); c.u8()
        nmsg = c.u16()
        c.u32()
        size = c.u32()
        blocks, seen = [(at + 16, size)], set()
        while blocks:
            start, size = blocks.pop(0)
            if (start, size) in seen or len(seen) > 4096:
                raise NetCDFError('object header continuation loop')
            seen.add((start, size))
            b = self.s.read(start, size, 'an object header')
            c = self._cur(b, what='an object header')
            while c.p + 8 <= len(b):
                mtype, msize, mflags = c.u16(), c.u16(), c.u8()
                c.take(3)
                data = c.take(msize)
                if mflags & 0x02 and mtype not in (0x10,):
                    raise NetCDFError('shared header messages are not supported')
                if mtype == 0x10:
                    cc = self._cur(data, what='a continuation message')
                    blocks.append((self._at(cc.addr()), cc.length()))
                else:
                    o.msgs.append((mtype, data))
                if len(o.msgs) > MAX_MESSAGES:
                    raise NetCDFError('too many header messages')

    def _ohdr_v2(self, o, at):
        pre = self.s.read(at, min(48, self.s.size - at), 'an object header')
        c = self._cur(pre, what='an object header')
        c.sig(b'OHDR')
        if c.u8() != 2:
            raise NetCDFError('object header version is not 2')
        flags = c.u8()
        if flags & 0x20:
            c.take(16)
        if flags & 0x10:
            c.take(4)
        size = c.u(1 << (flags & 3))
        track_order = bool(flags & 0x04)
        blocks, seen, first = [(at + c.p, size)], set(), True
        while blocks:
            start, size = blocks.pop(0)
            if (start, size) in seen or len(seen) > 4096:
                raise NetCDFError('object header continuation loop')
            seen.add((start, size))
            if first:
                b = self.s.read(start, size, 'an object header')
                first = False
            else:
                b = self.s.read(start, size, 'an object header continuation')
                if b[:4] != b'OCHK':
                    raise NetCDFError('object header continuation without OCHK')
                b = b[4:max(4, len(b) - 4)]
            c = self._cur(b, what='an object header')
            hdr = 4 + (2 if track_order else 0)
            while c.p + hdr <= len(b):
                mtype, msize, mflags = c.u8(), c.u16(), c.u8()
                if track_order:
                    c.u16()
                data = c.take(msize)
                if mflags & 0x02 and mtype != 0x10:
                    raise NetCDFError('shared header messages are not supported')
                if mtype == 0x10:
                    cc = self._cur(data, what='a continuation message')
                    blocks.append((self._at(cc.addr()), cc.length()))
                else:
                    o.msgs.append((mtype, data))
                if len(o.msgs) > MAX_MESSAGES:
                    raise NetCDFError('too many header messages')

    def _interpret(self, o):
        """Decode the messages a reader needs."""
        attr_info = link_info = symtab = None
        for mtype, data in o.msgs:
            c = self._cur(data, what=f'header message {mtype}')
            if mtype == 0x01:
                o.dims, o.maxdims = _dataspace(c)
            elif mtype == 0x03:
                o.dtype = _datatype(c)
            elif mtype == 0x05:
                o.fill = self._fill_new(c)
            elif mtype == 0x04 and o.fill is None:
                n = c.u32()
                o.fill = c.take(n) if n else None
            elif mtype == 0x08:
                o.layout = self._layout(c)
            elif mtype == 0x0b:
                o.filters = self._filters(c)
            elif mtype == 0x0c:
                self._attribute(o, c)
            elif mtype == 0x15:
                attr_info = c
            elif mtype == 0x02:
                link_info = c
            elif mtype == 0x06:
                if o.links is None:
                    o.links = {}
                name, target = self._link(c)
                if target is not None:
                    o.links[name] = target
            elif mtype == 0x11:
                symtab = (c.addr(), c.addr())
        if symtab:
            o.links = o.links or {}
            self._symbol_table(o, *symtab)
        if link_info is not None:
            o.links = o.links or {}
            self._dense_links(o, link_info)
        if attr_info is not None:
            self._dense_attrs(o, attr_info)

    def _fill_new(self, c):
        ver = c.u8()
        if ver in (1, 2):
            c.u8(); c.u8()
            defined = c.u8()
            if ver == 2 and not defined:
                return None
            n = c.u32()
            return c.take(n) if n else None
        if ver == 3:
            flags = c.u8()
            if flags & 0x20:
                n = c.u32()
                return c.take(n) if n else None
            return None
        raise NetCDFError(f'fill value message version {ver} is not supported')

    def _layout(self, c):
        ver = c.u8()
        L = {'version': ver}
        if ver in (1, 2):
            nd = c.u8()
            cls = c.u8()
            c.take(5)
            L['class'] = cls
            if cls != 0:
                L['addr'] = c.addr()
            L['chunk'] = [c.u32() for _ in range(nd)]
            if cls == 0:
                n = c.u32()
                L['data'] = c.take(n)
            if cls == 2:
                L['index'] = 'btree1'
            return L
        if ver not in (3, 4):
            raise NetCDFError(f'layout message version {ver} is not supported')
        cls = c.u8()
        L['class'] = cls
        if cls == 0:
            n = c.u16()
            L['data'] = c.take(n)
        elif cls == 1:
            L['addr'] = c.addr()
            L['size'] = c.length()
        elif cls == 2:
            if ver == 3:
                nd = c.u8()
                L['addr'] = c.addr()
                L['chunk'] = [c.u32() for _ in range(nd)]
                L['index'] = 'btree1'
            else:
                flags = c.u8()
                nd = c.u8()
                enc = c.u8()
                if not 1 <= enc <= 8:
                    raise NetCDFError(f'chunk dimension size encoded in {enc} bytes')
                L['chunk'] = [c.u(enc) for _ in range(nd)]
                kind = c.u8()
                L['flags'] = flags
                if kind == 1:
                    L['index'] = 'single'
                    if flags & 0x02:
                        L['single_size'] = c.length()
                        L['single_mask'] = c.u32()
                elif kind == 2:
                    L['index'] = 'implicit'
                elif kind == 3:
                    L['index'] = 'farray'
                    c.u8()
                elif kind == 4:
                    L['index'] = 'earray'
                    c.take(5)
                elif kind == 5:
                    L['index'] = 'btree2'
                    c.take(6)
                else:
                    raise NetCDFError(f'chunk index type {kind} is not supported')
                L['addr'] = c.addr()
        elif cls == 3:
            raise NetCDFError('virtual datasets are not supported')
        else:
            raise NetCDFError(f'layout class {cls} is not supported')
        return L

    def _filters(self, c):
        ver = c.u8()
        n = c.u8()
        if ver == 1:
            c.take(6)
        elif ver != 2:
            raise NetCDFError(f'filter pipeline version {ver} is not supported')
        out = []
        for _ in range(n):
            fid = c.u16()
            nlen = c.u16() if ver == 1 or fid >= 256 else 0
            flags = c.u16()
            nval = c.u16()
            name = ''
            if nlen:
                name = c.take(nlen).split(b'\x00')[0].decode('ascii', 'replace')
            vals = [c.u32() for _ in range(nval)]
            if ver == 1 and nval % 2:
                c.take(4)
            out.append((fid, name, flags, vals))
        return out

    def _attribute(self, o, c):
        """One attribute message -> o.attrs[name] (unreadable values are skipped, not fatal)."""
        ver = c.u8()
        flags = c.u8()
        nsz, tsz, ssz = c.u16(), c.u16(), c.u16()
        if ver == 3:
            c.u8()
        if flags & 0x03:
            raise NetCDFError('shared datatypes or dataspaces in attributes are not supported')
        pad = 8 if ver == 1 else 1
        name = c.take(nsz).split(b'\x00')[0].decode('utf-8', 'replace')
        c.take((pad - nsz % pad) % pad)
        tb = c.take(tsz); c.take((pad - tsz % pad) % pad)
        sb = c.take(ssz); c.take((pad - ssz % pad) % pad)
        t = _datatype(self._cur(tb, what=f'attribute {name}'))
        dims, _ = _dataspace(self._cur(sb, what=f'attribute {name}'))
        if dims is None:
            o.attrs[name] = ''
            return
        n = int(np.prod(dims, dtype=object)) if dims else 1
        esz = 4 + self.osz + 4 if t.cls == 9 else t.size
        if n * esz > len(c.b) - c.p:
            raise NetCDFError(f'attribute {name} is cut short')
        raw = c.take(n * esz)
        o.attrs[name] = (t, dims, raw)

    def _attr_value(self, name, t, dims, raw):
        """Raw attribute bytes -> str, a scalar or an array, as ncio returns them."""
        n = int(np.prod(dims, dtype=object)) if dims else 1
        if t.cls == 3:
            if t.size == 1 or n == 1:
                return raw.decode('utf-8', 'replace').rstrip('\x00')
            return np.array([raw[i * t.size:(i + 1) * t.size].decode('utf-8', 'replace').rstrip('\x00 ')
                             for i in range(n)], dtype=str)
        if t.cls == 9:
            items = self._vlen(t, raw, n)
            if t.vlen_string:
                return items[0] if n == 1 else np.array(items, dtype=str)
            return items
        if t.np is None:
            return None
        a = np.frombuffer(raw, t.np)
        if t.np.kind in 'iuf':
            a = a.astype(t.np.newbyteorder('='))
            return a[0].item() if len(a) == 1 else a
        return a

    def _vlen(self, t, raw, n):
        """n variable-length elements (length, heap collection, object index) -> str or arrays."""
        c = self._cur(raw, what='a variable-length value')
        out = []
        for _ in range(n):
            ln = c.u32()
            coll = c.addr()
            idx = c.u32()
            if coll is UNDEF or ln == 0:
                out.append('' if t.vlen_string else np.zeros(0, t.base.np if t.base and t.base.np is not None else 'u1'))
                continue
            data = self._gheap(coll, idx)
            if t.vlen_string:
                out.append(data[:ln].decode('utf-8', 'replace').rstrip('\x00'))
            else:
                bt = t.base.np if t.base is not None and t.base.np is not None else np.dtype('u1')
                need = ln * bt.itemsize
                if need > len(data):
                    raise NetCDFError('variable-length value longer than its heap object')
                out.append(np.frombuffer(data[:need], bt))
        return out

    def _gheap(self, addr, idx):
        """Object idx of the global heap collection at addr."""
        if addr not in self.gheaps:
            at = self._at(addr)
            c = self._cur(self.s.read(at, 8 + self.lsz, 'a global heap'), what='a global heap')
            c.sig(b'GCOL')
            c.u8(); c.take(3)
            size = c.length()
            if size is UNDEF or size > 1 << 28 or size < 8 + self.lsz:
                raise NetCDFError(f'global heap collection of {size} bytes')
            b = self.s.read(at, size, 'a global heap')
            objs = {}
            c = self._cur(b, 8 + self.lsz, what='a global heap')
            while c.p + 8 + self.lsz <= len(b):
                i, _rc = c.u16(), c.u16()
                c.take(4)
                sz = c.length()
                if i == 0:
                    break
                if sz is UNDEF or sz > len(b) - c.p:
                    raise NetCDFError('global heap object runs past its collection')
                objs[i] = c.take(sz)
                c.align(0)
            self.gheaps[addr] = objs
        objs = self.gheaps[addr]
        if idx not in objs:
            raise NetCDFError(f'global heap object {idx} is missing')
        return objs[idx]

    def _link(self, c):
        """A link message -> (name, address) for a hard link, (name, None) otherwise."""
        if c.u8() != 1:
            raise NetCDFError('link message version is not 1')
        flags = c.u8()
        ltype = c.u8() if flags & 0x08 else 0
        if flags & 0x04:
            c.u64()
        if flags & 0x10:
            c.u8()
        n = c.u(1 << (flags & 3))
        name = c.take(n).decode('utf-8', 'replace')
        if ltype == 0:
            return name, c.addr()
        return name, None

    def _symbol_table(self, o, btree, heap):
        """Old-style group: v1 B-tree of symbol table nodes, names in a local heap."""
        hc = self._cur(self.s.read(self._at(heap), 8 + 2 * self.lsz + self.osz, 'a local heap'), what='a local heap')
        hc.sig(b'HEAP')
        hc.u8(); hc.take(3)
        hsize = hc.length()
        hc.length()
        haddr = hc.addr()
        if hsize is UNDEF or hsize > 1 << 28:
            raise NetCDFError('local heap size is not plausible')
        names = self.s.read(self._at(haddr), hsize, 'a local heap')
        for child in self._btree1_leaves(btree, 0, 0):
            c = self._cur(self.s.read(self._at(child), 8, 'a symbol table node'), what='a symbol table node')
            c.sig(b'SNOD')
            c.u8(); c.u8()
            n = c.u16()
            esz = 2 * self.osz + 24
            if n > 1 << 16:
                raise NetCDFError('symbol table node too large')
            c = self._cur(self.s.read(self._at(child) + 8, n * esz, 'a symbol table node'), what='a symbol table node')
            for _ in range(n):
                noff = c.u(self.osz)
                oaddr = c.addr()
                c.take(24)
                if noff >= len(names):
                    raise NetCDFError('link name outside its local heap')
                end = names.find(b'\x00', noff)
                name = names[noff:end if end >= 0 else len(names)].decode('utf-8', 'replace')
                if oaddr is not UNDEF:
                    o.links[name] = oaddr

    def _btree1_leaves(self, addr, ntype, keylen, depth=0, seen=None):
        """Children of the leaves of a v1 B-tree; for chunk trees (ntype 1) yields (key bytes, child)."""
        seen = set() if seen is None else seen
        if addr in seen or depth > MAX_DEPTH:
            raise NetCDFError('B-tree loop or too deep')
        seen.add(addr)
        at = self._at(addr)
        c = self._cur(self.s.read(at, 8 + 2 * self.osz, 'a B-tree node'), what='a B-tree node')
        c.sig(b'TREE')
        t, level, n = c.u8(), c.u8(), c.u16()
        if t != ntype:
            raise NetCDFError(f'B-tree node type {t}, expected {ntype}')
        klen = self.lsz if ntype == 0 else keylen
        body = self.s.read(at + 8 + 2 * self.osz, n * (klen + self.osz) + klen, 'a B-tree node')
        c = self._cur(body, what='a B-tree node')
        for _ in range(n):
            key = c.take(klen)
            child = c.addr()
            if level == 0:
                yield (key, child) if ntype == 1 else child
            else:
                yield from self._btree1_leaves(child, ntype, keylen, depth + 1, seen)

    # ---------------------------------------------------------- fractal heaps and v2 B-trees
    def _fheap(self, addr):
        """Fractal heap header -> a function heap_id -> object bytes."""
        at = self._at(addr)
        b = self.s.read(at, min(512, self.s.size - at), 'a fractal heap header')
        c = self._cur(b, what='a fractal heap header')
        c.sig(b'FRHP')
        c.u8()
        idlen, filt_len, flags = c.u16(), c.u16(), c.u8()
        max_managed = c.u32()
        c.length(); c.addr(); c.length(); c.addr(); c.length(); c.length(); c.length()
        c.length(); c.length(); c.length(); c.length(); c.length()
        width = c.u16()
        start = c.length()
        max_direct = c.length()
        max_heap_bits = c.u16()
        c.u16()
        root = c.addr()
        rows = c.u16()
        if filt_len:
            raise NetCDFError('filtered fractal heaps are not supported')
        if width == 0 or start in (0, UNDEF) or max_direct in (0, UNDEF) or max_heap_bits > 64:
            raise NetCDFError('fractal heap parameters are not plausible')
        off_bytes = (max_heap_bits + 7) // 8
        # H5HF__hdr_finish_init_phase1: the length field holds the largest managed object
        len_bytes = min(((max_direct.bit_length() - 1) + 7) // 8, _enc_size(max_managed))
        checked = bool(flags & 0x02)
        blocks = []                                       # (heap offset, size, address)

        def row_size(r):
            return start if r < 2 else start << (r - 1)
        max_direct_rows = (max_direct // start).bit_length() + 1

        def indirect(baddr, nrows, depth):
            if depth > MAX_DEPTH:
                raise NetCDFError('fractal heap too deep')
            n_direct = min(nrows, max_direct_rows) * width
            n_indirect = max(0, nrows - max_direct_rows) * width
            ib = self.s.read(self._at(baddr), 5 + self.osz + off_bytes + (n_direct + n_indirect) * self.osz,
                             'a fractal heap block')
            ic = self._cur(ib, what='a fractal heap block')
            ic.sig(b'FHIB')
            ic.u8(); ic.addr()
            hoff = ic.u(off_bytes)
            pos = hoff
            for r in range(nrows):
                s = row_size(r)
                for _ in range(width):
                    a = ic.addr()
                    if r < max_direct_rows:
                        if a is not UNDEF:
                            blocks.append((pos, s, a))
                    else:
                        if a is not UNDEF:
                            sub_rows = (s // (start * width)).bit_length()
                            indirect(a, sub_rows, depth + 1)
                    pos += s
                    if len(blocks) > 1 << 16:
                        raise NetCDFError('fractal heap has too many blocks')

        if root is not UNDEF:
            if rows == 0:
                blocks.append((0, start, root))
            else:
                indirect(root, rows, 0)
        cache = {}

        def get(hid):
            kind = (hid[0] >> 4) & 3
            if kind == 2:                                 # tiny: the object is in the ID
                n = (hid[0] & 0x0f) + 1
                return hid[1:1 + n]
            if kind != 0:
                raise NetCDFError('huge fractal heap objects are not supported')
            hc = self._cur(hid, 1, what='a heap ID')
            off, ln = hc.u(off_bytes), hc.u(len_bytes)
            for boff, size, baddr in blocks:
                if boff <= off < boff + size:
                    if baddr not in cache:
                        cache[baddr] = self.s.read(self._at(baddr), size, 'a fractal heap block')
                        if cache[baddr][:4] != b'FHDB':
                            raise NetCDFError('fractal heap direct block without FHDB')
                    rel = off - boff
                    data = cache[baddr]
                    if rel + ln > len(data):
                        raise NetCDFError('fractal heap object runs past its block')
                    return data[rel:rel + ln]
            raise NetCDFError('fractal heap object outside every block')
        _ = checked
        return idlen, get

    def _btree2_records(self, addr):
        """Every record of a v2 B-tree, as bytes, plus the tree's record type."""
        at = self._at(addr)
        c = self._cur(self.s.read(at, 4 + 1 + 1 + 4 + 2 + 2 + 2 + self.osz + 2 + self.lsz + 4, 'a v2 B-tree header'),
                      what='a v2 B-tree header')
        c.sig(b'BTHD')
        c.u8()
        btype, node_size, rec_size, depth = c.u8(), c.u32(), c.u16(), c.u16()
        c.u8(); c.u8()
        root = c.addr()
        nroot = c.u16()
        if rec_size == 0 or node_size < 10 or depth > MAX_DEPTH:
            raise NetCDFError('v2 B-tree parameters are not plausible')
        # bytes of the child-pointer fields per level (H5B2__hdr_init)
        max_nrec = [(node_size - 10) // rec_size]
        cum = [max_nrec[0]]
        cum_size = [0]
        nrec_size = _enc_size(max_nrec[0])
        for d in range(1, depth + 1):
            ptr = self.osz + nrec_size + (cum_size[d - 1] if d > 1 else 0)
            m = (node_size - (10 + ptr)) // (rec_size + ptr)
            max_nrec.append(m)
            cum.append((m + 1) * cum[d - 1] + m)
            cum_size.append(_enc_size(cum[d]))
        out, seen = [], set()

        def node(a, n, d):
            if a is UNDEF or n == 0:
                return
            if a in seen or len(seen) > 1 << 16:
                raise NetCDFError('v2 B-tree loop or too large')
            seen.add(a)
            if d == 0:
                b = self.s.read(self._at(a), 6 + n * rec_size, 'a v2 B-tree leaf')
                if b[:4] != b'BTLF':
                    raise NetCDFError('v2 B-tree leaf without BTLF')
                out.extend(b[6 + i * rec_size:6 + (i + 1) * rec_size] for i in range(n))
                return
            ptr = self.osz + nrec_size + (cum_size[d - 1] if d > 1 else 0)
            b = self.s.read(self._at(a), 6 + n * rec_size + (n + 1) * ptr, 'a v2 B-tree node')
            if b[:4] != b'BTIN':
                raise NetCDFError('v2 B-tree node without BTIN')
            recs = [b[6 + i * rec_size:6 + (i + 1) * rec_size] for i in range(n)]
            pc = self._cur(b, 6 + n * rec_size, what='a v2 B-tree node')
            kids = []
            for _ in range(n + 1):
                ca = pc.addr()
                cn = pc.u(nrec_size)
                if d > 1:
                    pc.u(cum_size[d - 1])
                kids.append((ca, cn))
            for i, (ca, cn) in enumerate(kids):
                node(ca, cn, d - 1)
                if i < n:
                    out.append(recs[i])
        node(root, nroot, depth)
        return btype, out

    def _dense_attrs(self, o, c):
        if c.u8() != 0:
            raise NetCDFError('attribute info message version is not 0')
        flags = c.u8()
        if flags & 1:
            c.u16()
        heap, name_tree = c.addr(), c.addr()
        if heap is UNDEF or name_tree is UNDEF:
            return
        idlen, get = self._fheap(heap)
        _, recs = self._btree2_records(name_tree)
        for r in recs:
            data = get(r[:idlen])
            self._attribute(o, self._cur(data, what='a dense attribute'))

    def _dense_links(self, o, c):
        if c.u8() != 0:
            raise NetCDFError('link info message version is not 0')
        flags = c.u8()
        if flags & 1:
            c.u64()
        heap, name_tree = c.addr(), c.addr()
        if heap is UNDEF or name_tree is UNDEF:
            return
        idlen, get = self._fheap(heap)
        _, recs = self._btree2_records(name_tree)
        for r in recs:
            name, target = self._link(self._cur(get(r[4:4 + idlen]), what='a dense link'))
            if target is not None:
                o.links[name] = target

    # ---------------------------------------------------------- groups -> netCDF
    def _walk(self, g, path, stack):
        if g.addr in stack or len(stack) > MAX_DEPTH:
            raise NetCDFError('group loop')
        for name, addr in sorted((g.links or {}).items()):
            o = self._object(addr)
            full = path + name
            if o.is_dataset:
                self.datasets[full] = o
            elif o.links is not None:
                self._walk(o, full + '/', stack | {g.addr})

    def _netcdf(self, root):
        """Dimension scales -> dimensions; datasets -> variables; root attributes -> global attributes."""
        by_addr = {o.addr: p for p, o in self.datasets.items()}
        scales = {}
        for p, o in self.datasets.items():
            cls = o.attrs.get('CLASS')
            if cls and self._attr_value('CLASS', *cls) == 'DIMENSION_SCALE':
                scales[p] = o
        self.dims, self.unlimited = {}, None
        for p, o in sorted(scales.items()):
            name = p.replace(NON_COORD, '')
            size = o.dims[0] if o.dims else 0
            self.dims[name] = size
            if o.maxdims and o.maxdims[0] is None and self.unlimited is None:
                self.unlimited = name
        self.attrs = self._attrs(root)
        self.vars = {}
        phony = {}
        for p, o in sorted(self.datasets.items()):
            raw_name = p
            name = p.replace(NON_COORD, '')
            nm_attr = o.attrs.get('NAME')
            if p in scales and nm_attr and str(self._attr_value('NAME', *nm_attr)).startswith(NOT_A_VARIABLE):
                continue
            v = Var(self, name, o)
            v.attrs = self._attrs(o)
            dims = []
            dl = o.attrs.get('DIMENSION_LIST')
            if raw_name in scales and o.dims is not None and len(o.dims) == 1 and raw_name.find(NON_COORD) < 0:
                dims = [name]
            elif dl:
                refs = self._attr_value('DIMENSION_LIST', *dl)
                for k, r in enumerate(refs if isinstance(refs, list) else []):
                    target = int(np.asarray(r).ravel()[0]) if np.size(r) else None
                    dname = by_addr.get(target)
                    if dname is None:
                        raise NetCDFError(f'{name}: dimension {k} refers to an unknown dimension scale')
                    dims.append(dname.replace(NON_COORD, ''))
            if o.dims is not None and len(dims) != len(o.dims):
                dims = []
                for k, size in enumerate(o.dims):
                    key = (k, size)
                    if key not in phony:
                        phony[key] = f'phony_dim_{len(phony)}'
                        self.dims[phony[key]] = size
                    dims.append(phony[key])
            v.dims = tuple(dims)
            self.vars[name] = v

    def _attrs(self, o):
        out = {}
        for k, val in o.attrs.items():
            if k in HIDDEN_ATTRS or not isinstance(val, tuple):
                continue
            v = self._attr_value(k, *val)
            if v is not None:
                out[k] = v
        return out

    # ---------------------------------------------------------- data
    def _read_data(self, o, sel, max_bytes=None):
        limit = self.max_bytes if max_bytes is None else max_bytes
        t, dims = o.dtype, o.dims
        if t is None or dims is None:
            return np.zeros(0)
        rank = len(dims)
        esz = 4 + self.osz + 4 if t.cls == 9 else t.size
        if sel is None:
            sel = (slice(None),) * rank
        if not isinstance(sel, tuple):
            sel = (sel,)
        sel = tuple(sel) + (slice(None),) * (rank - len(sel))
        if len(sel) != rank:
            raise NetCDFError('selection has more dimensions than the variable')
        lo, hi, squeeze = [], [], []
        for k, (s, n) in enumerate(zip(sel, dims)):
            if isinstance(s, slice):
                a, b, step = s.indices(n)
                if step != 1:
                    raise NetCDFError('only contiguous selections are supported')
                lo.append(a); hi.append(max(a, b))
            else:
                i = int(s) + (n if int(s) < 0 else 0)
                if not 0 <= i < n:
                    raise NetCDFError(f'index {s} outside dimension of size {n}')
                lo.append(i); hi.append(i + 1); squeeze.append(k)
        shape = [h - l for l, h in zip(lo, hi)]
        total = int(np.prod(shape, dtype=object)) if shape else 1
        if total * esz > limit:
            raise NetCDFError(f'reading {total * esz} bytes exceeds the limit of {limit}')
        vt = np.dtype(f'V{esz}') if t.cls == 9 else (t.np if t.np is not None else np.dtype(f'V{esz}'))
        out = np.empty(shape, vt)
        fill = o.fill if o.fill is not None and len(o.fill) == esz else bytes(esz)
        out.view(np.uint8).reshape(-1, esz)[:] = np.frombuffer(fill, np.uint8) if total else 0
        L = o.layout
        if total:
            if L['class'] == 0:
                self._place_whole(out, L.get('data', b''), dims, lo, hi, vt, esz, 'compact data')
            elif L['class'] == 1:
                if L['addr'] is not UNDEF:
                    n = int(np.prod(dims, dtype=object)) * esz if dims else esz
                    if n > limit:
                        raise NetCDFError(f'variable of {n} bytes exceeds the limit of {limit}')
                    self._place_contiguous(out, o, dims, lo, hi, vt, esz)
            else:
                self._place_chunks(out, o, dims, lo, hi, vt, esz, limit)
        if t.cls == 9:
            raw = out.tobytes()
            items = self._vlen(t, raw, total)
            out = np.array(items, dtype=str if t.vlen_string else object).reshape(shape)
        elif vt.kind in 'iuf':
            out = out.astype(vt.newbyteorder('='))
        if squeeze:
            out = out.reshape([n for k, n in enumerate(shape) if k not in squeeze])
        return out

    def _place_whole(self, out, buf, dims, lo, hi, vt, esz, what):
        n = int(np.prod(dims, dtype=object)) if dims else 1
        if len(buf) < n * esz:
            raise NetCDFError(f'{what} is cut short')
        a = np.frombuffer(buf[:n * esz], vt).reshape(dims)
        out[...] = a[tuple(slice(l, h) for l, h in zip(lo, hi))]

    def _place_contiguous(self, out, o, dims, lo, hi, vt, esz):
        """Read only the rows of a contiguous dataset that the selection needs."""
        base = self._at(o.layout['addr'])
        if not dims:
            out[...] = np.frombuffer(self.s.read(base, esz, 'contiguous data'), vt)[0]
            return
        inner = int(np.prod(dims[1:], dtype=object)) * esz
        b = self.s.read(base + lo[0] * inner, (hi[0] - lo[0]) * inner, 'contiguous data')
        a = np.frombuffer(b, vt).reshape([hi[0] - lo[0]] + list(dims[1:]))
        out[...] = a[(slice(None),) + tuple(slice(l, h) for l, h in zip(lo[1:], hi[1:]))]

    def _chunk_index(self, o, dims, chunk, esz):
        """{chunk grid coordinates: (offset in source, stored bytes, filter mask)} for every allocated chunk."""
        if getattr(o, '_index', None) is not None:
            return o._index
        L, rank = o.layout, len(dims)
        grid = [-(-d // c) if c else 0 for d, c in zip(dims, chunk)]
        maxd = [m if m is not None else d for m, d in zip(o.maxdims or dims, dims)]
        full = int(np.prod(chunk, dtype=object)) * esz
        idx = {}
        kind = L['index']
        if L['addr'] is UNDEF:
            o._index = idx
            return idx
        if kind == 'btree1':
            klen = 8 + 8 * (rank + 1)
            for key, child in self._btree1_leaves(L['addr'], 1, klen):
                kc = self._cur(key, what='a chunk key')
                size, mask = kc.u32(), kc.u32()
                offs = [kc.u64() for _ in range(rank)]
                if any(c == 0 or off % c for off, c in zip(offs, chunk)):
                    raise NetCDFError('chunk offset is not on the chunk grid')
                idx[tuple(off // c for off, c in zip(offs, chunk))] = (self._at(child), size, mask)
                if len(idx) > MAX_CHUNKS:
                    raise NetCDFError('too many chunks')
        elif kind == 'single':
            idx[(0,) * rank] = (self._at(L['addr']), L.get('single_size', full), L.get('single_mask', 0))
        elif kind == 'implicit':
            n = int(np.prod(grid, dtype=object))
            if n > MAX_CHUNKS:
                raise NetCDFError('too many chunks')
            for i, cc in enumerate(np.ndindex(*grid)):
                idx[cc] = (self._at(L['addr']) + i * full, full, 0)
        elif kind in ('farray', 'earray'):
            filtered = bool(o.filters)
            entries = self._farray(L['addr'], filtered) if kind == 'farray' else self._earray(L['addr'], filtered)
            # the linear chunk index runs over the chunk grid of the maximum dimensions; an extensible array
            # moves its unlimited dimension to the front first (H5D__earray_idx: "swizzled" coordinates)
            unlim = [k for k, m in enumerate(o.maxdims or []) if m is None] if kind == 'earray' else []
            order = unlim[:1] + [k for k in range(rank) if k not in unlim[:1]]
            mcount = [-(-maxd[k] // chunk[k]) for k in range(rank)]
            down = [1] * rank
            for j in range(rank - 2, -1, -1):
                down[order[j]] = down[order[j + 1]] * mcount[order[j + 1]]
            for cc in np.ndindex(*grid):
                lin = sum(cc[k] * down[k] for k in range(rank))
                e = entries(lin)
                if e is not None:
                    a, size, mask = e
                    idx[cc] = (self._at(a), size if size is not None else full, mask)
                if len(idx) > MAX_CHUNKS:
                    raise NetCDFError('too many chunks')
        elif kind == 'btree2':
            _, recs = self._btree2_records(L['addr'])
            for r in recs:
                c = self._cur(r, what='a chunk record')
                a = c.addr()
                if o.filters:
                    nsz = len(r) - self.osz - 4 - 8 * rank
                    size, mask = c.u(nsz), c.u32()
                else:
                    size, mask = full, 0
                cc = tuple(c.u64() for _ in range(rank))
                idx[cc] = (self._at(a), size, mask)
                if len(idx) > MAX_CHUNKS:
                    raise NetCDFError('too many chunks')
        o._index = idx
        return idx

    def _entry_reader(self, filtered, esz_entry):
        def parse(b):
            c = self._cur(b, what='a chunk index entry')
            a = c.addr()
            if a is UNDEF:
                return None
            if filtered:
                size = c.u(esz_entry - self.osz - 4)
                return a, size, c.u32()
            return a, None, 0
        return parse

    def _farray(self, addr, filtered):
        at = self._at(addr)
        c = self._cur(self.s.read(at, 4 + 4 + self.lsz + self.osz + 4, 'a fixed array header'), what='a fixed array header')
        c.sig(b'FAHD')
        c.u8(); c.u8()
        esz, page_bits = c.u8(), c.u8()
        n = c.length()
        dblk = c.addr()
        if n is UNDEF or n > MAX_CHUNKS or esz == 0:
            raise NetCDFError('fixed array parameters are not plausible')
        parse = self._entry_reader(filtered, esz)
        if dblk is UNDEF:
            return lambda i: None
        page = 1 << page_bits
        prefix = 4 + 1 + 1 + self.osz
        if n > page:
            npages = -(-n // page)
            bitmap = self.s.read(self._at(dblk) + prefix, (npages + 7) // 8, 'a fixed array page bitmap')
            first = self._at(dblk) + prefix + len(bitmap) + 4

            def get(i):
                if i >= n:
                    return None
                p, k = divmod(i, page)
                if not bitmap[p // 8] & (0x80 >> (p % 8)):
                    return None
                return parse(self.s.read(first + p * (page * esz + 4) + k * esz, esz, 'a fixed array page'))
            return get
        body = self.s.read(self._at(dblk) + prefix, n * esz, 'a fixed array data block')
        return lambda i: parse(body[i * esz:(i + 1) * esz]) if i < n else None

    def _earray(self, addr, filtered):
        at = self._at(addr)
        c = self._cur(self.s.read(at, 12 + 6 * self.lsz + self.osz + 4, 'an extensible array header'),
                      what='an extensible array header')
        c.sig(b'EAHD')
        c.u8(); c.u8()
        esz, max_bits, idx_elmts, min_dblk_elmts, min_ptrs, page_bits = (c.u8() for _ in range(6))
        for _ in range(6):
            c.length()
        iblk = c.addr()
        if esz == 0 or max_bits > 64 or min_dblk_elmts == 0 or min_ptrs == 0:
            raise NetCDFError('extensible array parameters are not plausible')
        parse = self._entry_reader(filtered, esz)
        if iblk is UNDEF:
            return lambda i: None
        nsblks = 1 + max_bits - (min_dblk_elmts.bit_length() - 1)
        sinfo, start, sdblk = [], 0, 0
        for u in range(nsblks):
            nd, ne = 1 << (u // 2), (1 << ((u + 1) // 2)) * min_dblk_elmts
            sinfo.append((nd, ne, start, sdblk))
            start += nd * ne
            sdblk += nd
        ib_nsblks = 2 * (min_ptrs.bit_length() - 1)
        n_dblk_addrs = 2 * (min_ptrs - 1)
        n_sblk_addrs = max(0, nsblks - ib_nsblks)
        arr_off = (max_bits + 7) // 8
        ib = self.s.read(self._at(iblk), 4 + 2 + self.osz + idx_elmts * esz + (n_dblk_addrs + n_sblk_addrs) * self.osz,
                         'an extensible array index block')
        ic = self._cur(ib, what='an extensible array index block')
        ic.sig(b'EAIB')
        ic.u8(); ic.u8(); ic.addr()
        direct = [ic.take(esz) for _ in range(idx_elmts)]
        dblks = [ic.addr() for _ in range(n_dblk_addrs)]
        sblks = [ic.addr() for _ in range(n_sblk_addrs)]
        page = 1 << page_bits
        cache = {}

        def dblock(a, ne):
            if a is UNDEF:
                return None
            if ne > page:
                raise NetCDFError('paged extensible array data blocks are not supported')
            if a not in cache:
                b = self.s.read(self._at(a), 4 + 2 + self.osz + arr_off + ne * esz, 'an extensible array data block')
                if b[:4] != b'EADB':
                    raise NetCDFError('extensible array data block without EADB')
                cache[a] = b[4 + 2 + self.osz + arr_off:]
            return cache[a]

        def super_addrs(a, nd):
            if a is UNDEF:
                return None
            key = ('s', a)
            if key not in cache:
                b = self.s.read(self._at(a), 4 + 2 + self.osz + arr_off + nd * self.osz, 'an extensible array super block')
                if b[:4] != b'EASB':
                    raise NetCDFError('extensible array super block without EASB')
                sc = self._cur(b, 4 + 2 + self.osz + arr_off, what='an extensible array super block')
                cache[key] = [sc.addr() for _ in range(nd)]
            return cache[key]

        def get(i):
            if i < idx_elmts:
                return parse(direct[i])
            j = i - idx_elmts
            for u, (nd, ne, s0, d0) in enumerate(sinfo):
                if s0 <= j < s0 + nd * ne:
                    d, k = divmod(j - s0, ne)
                    if u < ib_nsblks:
                        a = dblks[d0 + d] if d0 + d < len(dblks) else UNDEF
                    else:
                        sa = super_addrs(sblks[u - ib_nsblks], nd) if u - ib_nsblks < len(sblks) else None
                        a = sa[d] if sa else UNDEF
                    b = dblock(a, ne)
                    return None if b is None else parse(b[k * esz:(k + 1) * esz])
            return None
        return get

    def _decode_chunk(self, o, raw, mask, nbytes):
        """Stored chunk bytes -> nbytes of element data, filters undone in reverse order."""
        for k, (fid, name, _flags, vals) in reversed(list(enumerate(o.filters))):
            if mask & (1 << k):
                continue
            if fid == 1:
                d = zlib.decompressobj()
                try:
                    out = d.decompress(raw, nbytes + 1)
                except zlib.error as e:
                    raise NetCDFError(f'a compressed chunk is damaged ({e})') from e
                if len(out) > nbytes or d.unconsumed_tail:
                    raise NetCDFError('a compressed chunk inflates past its size')
                raw = out
            elif fid == 2:
                size = vals[0] if vals else o.dtype.size
                if size > 1 and len(raw) % size == 0:
                    raw = np.frombuffer(raw, np.uint8).reshape(size, -1).T.tobytes()
            elif fid == 3:
                raw = raw[:-4]
            else:
                raise NetCDFError(f'HDF5 filter {fid} ({name or "unnamed"}) is not supported')
        if len(raw) < nbytes:
            raise NetCDFError(f'a chunk holds {len(raw)} bytes, {nbytes} expected')
        return raw[:nbytes]

    def _place_chunks(self, out, o, dims, lo, hi, vt, esz, limit):
        chunk = list(o.layout['chunk'])
        rank = len(dims)
        if len(chunk) == rank + 1:
            chunk = chunk[:rank]
        if len(chunk) != rank or any(c <= 0 for c in chunk):
            raise NetCDFError('chunk dimensions do not match the variable')
        nbytes = int(np.prod(chunk, dtype=object)) * esz
        if nbytes > min(limit, 1 << 31):
            raise NetCDFError(f'chunks of {nbytes} bytes exceed the limit')
        idx = self._chunk_index(o, dims, chunk, esz)
        first = [l // c for l, c in zip(lo, chunk)]
        last = [(h - 1) // c for h, c in zip(hi, chunk)]
        decoded = 0
        for cc in np.ndindex(*[b - a + 1 for a, b in zip(first, last)]):
            cc = tuple(a + k for a, k in zip(first, cc))
            e = idx.get(cc)
            if e is None:
                continue
            at, size, mask = e
            decoded += nbytes
            if decoded > limit:
                raise NetCDFError(f'decoding more than {limit} bytes')
            raw = self.s.read(at, size, 'a chunk')
            data = self._decode_chunk(o, raw, mask, nbytes)
            a = np.frombuffer(data, vt).reshape(chunk)
            c0 = [k * c for k, c in zip(cc, chunk)]
            src = tuple(slice(max(l, s) - s, min(h, s + c) - s) for l, h, s, c in zip(lo, hi, c0, chunk))
            dst = tuple(slice(max(l, s) - l, min(h, s + c) - l) for l, h, s, c in zip(lo, hi, c0, chunk))
            out[dst] = a[src]


# ------------------------------------------------------------------ the ncio-style entry points
def open_source(source, max_bytes=MAX_BYTES):
    """bytes, a path, or a source object (.size, .read(offset, n)) -> File."""
    if isinstance(source, (bytes, bytearray, memoryview)):
        source = BytesSource(source)
    elif isinstance(source, (str, os.PathLike)):
        source = FileSource(source)
    try:
        return File(source, max_bytes)
    except NetCDFError:
        raise
    except (struct.error, ValueError, TypeError, IndexError, KeyError, OverflowError, MemoryError, RecursionError) as e:
        raise NetCDFError(f'the HDF5 structures cannot be read ({type(e).__name__}: {e})') from e


def to_dict(f, raw=False):
    """A File -> the dict ncio.read_bytes() returns."""
    out = {'dims': dict(f.dims), 'unlimited': f.unlimited, 'attrs': dict(f.attrs), 'vars': {}}
    for name, v in f.vars.items():
        try:
            data = v.read(raw=raw)
        except NetCDFError:
            raise
        except (struct.error, ValueError, TypeError, IndexError, KeyError, OverflowError, MemoryError) as e:
            raise NetCDFError(f'variable {name} cannot be read ({type(e).__name__}: {e})') from e
        out['vars'][name] = {'dims': v.dims, 'attrs': v.attrs, 'data': data}
    return out


def read_bytes(data, raw=False, max_bytes=None):
    """NetCDF-4 bytes -> the dict ncio.read_bytes() returns. Decoded output is capped at max_bytes (default: 1 GiB,
    or 256 times the file size if smaller, and at least 64 MiB)."""
    data = bytes(data)
    if max_bytes is None:
        max_bytes = min(MAX_BYTES, max(1 << 26, 256 * len(data)))
    f = open_source(data, max_bytes)
    return to_dict(f, raw)


def read(path, raw=False):
    with open(path, 'rb') as fh:
        return read_bytes(fh.read(), raw)
