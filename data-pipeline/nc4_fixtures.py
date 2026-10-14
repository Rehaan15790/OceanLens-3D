"""Small NetCDF-4 (HDF5) files for tests of nc4.py and the browser reader: a writer for the layouts netCDF-C produces.

    data = write_bytes(dims, vars, attrs=None, style='classic')      # the same dims / vars / attrs as ncio.write_bytes

style='classic' is what netCDF-C writes by default and what Copernicus Marine's native files use: superblock 0,
version 1 object headers, groups as symbol tables (v1 B-tree and local heap), chunks in a v1 B-tree, version 1
filter pipelines and attributes. style='latest' is what HDF5 writes with the newest format bounds: superblock 2,
version 2 object headers, link messages, version 4 layouts (single chunk, implicit, fixed array, extensible array
and v2 B-tree chunk indexes), dense attribute and link storage in fractal heaps with v2 B-tree name indexes.

Per variable, besides ncio's 'dims', 'attrs', 'data' and 'dtype':
    'layout': 'contiguous' (default without unlimited dimensions), 'compact' or 'chunked'
    'chunks': chunk shape (default: the whole variable, 1 along an unlimited dimension)
    'deflate': level 1-9 (shuffle is added unless 'shuffle': False)
    'index': latest style only: 'single', 'implicit', 'farray', 'earray' or 'btree2' (default from the dimensions)
    'skip_fill_chunks': True leaves chunks that hold only the fill value unallocated
    'continuation': True puts the attributes in an object header continuation block
For hostile-input tests only: '_filters' replaces the filter list [(id, name, values)], '_raw_chunks' {grid coords:
bytes} replaces stored chunks, and '_dims' replaces the dimension sizes the dataspace declares.
groups={'name': {'vars': {...}, 'attrs': {...}}} adds subgroups that share the root's dimensions; dense_links=True
stores the root's links in a fractal heap (latest style); dense_attrs=N moves objects with more than N attributes
to dense storage (latest style, default 8 as in HDF5).

Because this writer and nc4.py come from the same reading of the HDF5 specification, tests built on it alone cannot
prove the reader right; the GLORYS test (test_nc4.RealGlorys) checks the reader on a file netCDF-C and HDF5 wrote.
"""
import zlib

import numpy as np

from nc4 import lookup3

UNDEF8 = b'\xff' * 8
NOT_A_VARIABLE = 'This is a netCDF dimension but not a netCDF variable.'


class String(str):
    """An NC_STRING attribute value (a plain str is written as NC_CHAR, as netCDF-C does)."""


def _pad8(b):
    return b + b'\x00' * ((8 - len(b) % 8) % 8)


def _u(v, n):
    return int(v).to_bytes(n, 'little')


class _W:
    """An append-only HDF5 image with 8-byte offsets and lengths."""
    def __init__(self, style):
        self.style = style
        self.b = bytearray()

    def alloc(self, data):
        while len(self.b) % 8:
            self.b.append(0)
        a = len(self.b)
        self.b += data
        return a

    def reserve(self, n):
        return self.alloc(b'\x00' * n)

    def put(self, addr, data):
        self.b[addr:addr + len(data)] = data

    @staticmethod
    def a(addr):
        return UNDEF8 if addr is None else _u(addr, 8)

    @staticmethod
    def checked(body):
        return body + _u(lookup3(bytes(body)), 4)


# ------------------------------------------------------------------ datatypes and dataspaces
def _dt_numeric(dt):
    dt = np.dtype(dt)
    if dt.kind in 'iu':
        bits = 0x08 if dt.kind == 'i' else 0
        return bytes([0x10]) + _u(bits, 3) + _u(dt.itemsize, 4) + _u(0, 2) + _u(dt.itemsize * 8, 2)
    if dt.kind == 'f':
        f8 = dt.itemsize == 8
        bits = 0x20 | ((63 if f8 else 31) << 8)
        props = (_u(0, 2) + _u(64 if f8 else 32, 2) + bytes([52 if f8 else 23, 11 if f8 else 8, 0, 52 if f8 else 23]) +
                 _u(1023 if f8 else 127, 4))
        return bytes([0x11]) + _u(bits, 3) + _u(dt.itemsize, 4) + props
    raise ValueError(f'no HDF5 type for {dt}')


def _dt_string(size):
    return bytes([0x13]) + _u(0x00, 3) + _u(size, 4)


def _dt_vlen_string():
    return bytes([0x19]) + _u(0x01, 3) + _u(16, 4) + bytes([0x10]) + _u(0, 3) + _u(1, 4) + _u(0, 2) + _u(8, 2)


def _dt_vlen_refs():
    return bytes([0x19]) + _u(0x00, 3) + _u(16, 4) + bytes([0x17]) + _u(0, 3) + _u(8, 4)


def _space(dims, maxdims, style):
    rank = len(dims)
    flags = 1 if maxdims is not None else 0
    if style == 'classic':
        b = bytes([1, rank, flags, 0]) + b'\x00' * 4
    else:
        b = bytes([2, rank, flags, 0 if rank == 0 else 1])
    b += b''.join(_u(d, 8) for d in dims)
    if maxdims is not None:
        b += b''.join(UNDEF8 if m is None else _u(m, 8) for m in maxdims)
    return b


# ------------------------------------------------------------------ the writer
class _File:
    def __init__(self, dims, vars, attrs, style, groups, dense_links, dense_attrs):
        self.w = _W(style)
        self.style, self.dims, self.vars, self.attrs = style, dims, vars, attrs or {}
        self.groups = groups or {}
        self.dense_links, self.dense_attrs = dense_links, dense_attrs
        self.heap = []                     # (index, bytes) of the global heap collections being filled
        self.heap_addr = None

    # ---------------------------------------------------------- global heap
    def heap_collection(self, objects):
        """Write a global heap collection holding objects (list of bytes) -> its address; ids are 1..n."""
        body = b''
        for i, o in enumerate(objects, 1):
            body += _u(i, 2) + _u(1, 2) + b'\x00' * 4 + _u(len(o), 8) + _pad8(o)
        size = max(4096, 16 + len(body) + 16)
        free = size - 16 - len(body)
        body += _u(0, 2) + _u(0, 2) + b'\x00' * 4 + _u(free, 8)
        img = b'GCOL' + bytes([1, 0, 0, 0]) + _u(size, 8) + body
        return self.w.alloc(img + b'\x00' * (size - len(img)))

    # ---------------------------------------------------------- attributes
    def attr_parts(self, name, value, heap_ids):
        """-> (datatype bytes, dataspace bytes, data bytes) for one attribute value."""
        if isinstance(value, String) or (isinstance(value, (list, tuple)) and value and isinstance(value[0], String)):
            vals = [value] if isinstance(value, String) else list(value)
            data = b''.join(_u(len(v.encode()), 4) + self.w.a(heap_ids[0]) + _u(heap_ids[1][(name, k)], 4)
                            for k, v in enumerate(vals))
            dims = [] if isinstance(value, String) else [len(vals)]
            return _dt_vlen_string(), _space(dims, None, self.style), data
        if isinstance(value, str):
            raw = value.encode('utf-8') or b'\x00'
            return _dt_string(len(raw)), _space([], None, self.style), raw
        if isinstance(value, bytes):                                  # a ready-made (dtype, space, data) triple
            raise ValueError('bytes attributes are not supported')
        if isinstance(value, tuple) and len(value) == 3 and isinstance(value[0], bytes):
            return value
        a = np.asarray(value)
        if a.dtype.kind == 'f' and a.dtype.itemsize == 8 and not isinstance(value, (np.floating, np.ndarray)):
            a = a.astype('f8')
        elif a.dtype.kind == 'i' and not isinstance(value, (np.integer, np.ndarray)):
            a = a.astype('i4')
        a = a.astype(a.dtype.newbyteorder('<'))
        dims = [] if a.ndim == 0 else [a.size]
        return _dt_numeric(a.dtype), _space(dims, None, self.style), a.tobytes()

    def attr_message(self, name, parts):
        t, s, d = parts
        nm = name.encode('utf-8') + b'\x00'
        if self.style == 'classic':
            return (bytes([1, 0]) + _u(len(nm), 2) + _u(len(t), 2) + _u(len(s), 2) + _pad8(nm) + _pad8(t) + _pad8(s) + d)
        return bytes([3, 0]) + _u(len(nm), 2) + _u(len(t), 2) + _u(len(s), 2) + b'\x00' + nm + t + s + d

    def string_objects(self, attrs, tag):
        """Collect NC_STRING attribute values for the strings heap."""
        out = []
        for k, v in attrs.items():
            if isinstance(v, String):
                out.append(((tag, k), 0, v))
            elif isinstance(v, (list, tuple)) and v and isinstance(v[0], String):
                out += [((tag, k), i, s) for i, s in enumerate(v)]
        return out

    # ---------------------------------------------------------- object headers
    def header(self, msgs, cont_msgs=()):
        """Object header with msgs [(type, data)] and optionally a continuation block -> address."""
        w, style = self.w, self.style
        if style == 'classic':
            def enc(ms):
                return b''.join(_u(t, 2) + _u(len(_pad8(d)), 2) + b'\x00' * 4 + _pad8(d) for t, d in ms)
            cont_addr = None
            if cont_msgs:
                body = enc(cont_msgs)
                cont_addr = w.alloc(body)
                msgs = list(msgs) + [(0x10, w.a(cont_addr) + _u(len(body), 8))]
            body = enc(msgs)
            n = len(msgs) + len(cont_msgs)
            return w.alloc(bytes([1, 0]) + _u(n, 2) + _u(1, 4) + _u(len(body), 4) + b'\x00' * 4 + body)

        def enc2(ms):
            return b''.join(bytes([t]) + _u(len(d), 2) + b'\x00' + d for t, d in ms)
        if cont_msgs:
            blk = w.checked(b'OCHK' + enc2(cont_msgs))
            ca = w.alloc(blk)
            msgs = list(msgs) + [(0x10, w.a(ca) + _u(len(blk), 8))]
        body = enc2(msgs)
        return w.alloc(w.checked(b'OHDR' + bytes([2, 0x02]) + _u(len(body), 4) + body))

    # ---------------------------------------------------------- data and layouts
    def prepare(self, name, v):
        """The array to store, its numpy dtype and its fill value bytes."""
        a = v['data']
        attrs = v.get('attrs', {})
        dt = v.get('dtype')
        if dt == 'S1' or (dt is None and np.asarray(a).dtype.kind == 'U' and v['dims'] and
                          v['dims'][-1] in self.dims and not v.get('string')):
            n = self.dims[v['dims'][-1]]
            strs = np.asarray(a, dtype=str)
            flat = [s.encode('utf-8')[:n].ljust(n, b'\x00') for s in strs.ravel()]
            arr = np.frombuffer(b''.join(flat), 'S1').reshape(strs.shape + (n,))
            return arr, 'char', b'\x00'
        if v.get('string') or dt == 'str':
            return np.asarray(a, dtype=object), 'vlen', bytes(16)
        a = np.asarray(a)
        dt = np.dtype(dt) if dt else a.dtype
        fillv = attrs.get('_FillValue')
        if dt.kind in 'iu' and a.dtype.kind == 'f':
            sc, off = attrs.get('scale_factor', 1.0), attrs.get('add_offset', 0.0)
            q = np.round((a - off) / sc)
            q = np.where(np.isfinite(a), q, fillv if fillv is not None else 0)
            a = q.astype(dt)
        elif dt.kind == 'f' and fillv is not None:
            a = np.where(np.isnan(a), fillv, a).astype(dt)
        else:
            a = a.astype(dt)
        a = np.ascontiguousarray(a.astype(dt.newbyteorder('<')))
        fill = np.asarray(fillv if fillv is not None else 0, dt.newbyteorder('<')).tobytes()
        return a, dt.newbyteorder('<'), fill

    def chunk_bytes(self, arr, cc, chunk, fill, esz):
        """The bytes of chunk cc, padded with the fill value past the array's edge."""
        sl = tuple(slice(c * k, c * k + k) for c, k in zip(cc, chunk))
        part = arr[sl]
        if part.shape == tuple(chunk):
            return np.ascontiguousarray(part).tobytes(), np.all(part.view(np.uint8).reshape(-1, esz) ==
                                                                  np.frombuffer(fill, np.uint8)) if esz else False
        full = np.empty(chunk, arr.dtype)
        full.view(np.uint8).reshape(-1, esz)[:] = np.frombuffer(fill, np.uint8)
        full[tuple(slice(0, s) for s in part.shape)] = part
        allfill = bool(np.all(full.view(np.uint8).reshape(-1, esz) == np.frombuffer(fill, np.uint8)))
        return full.tobytes(), allfill

    def layout_and_data(self, name, v, arr, kind, dtb, fill, unlimited_dims):
        """Write the data -> header messages for layout, filters and fill value."""
        w = self.w
        rank = arr.ndim if kind != 'char' else arr.ndim
        shape = arr.shape
        esz = 16 if kind == 'vlen' else arr.dtype.itemsize
        if kind == 'vlen':
            raw_elems = arr
        layout = v.get('layout') or ('chunked' if unlimited_dims or 'chunks' in v or v.get('deflate') else 'contiguous')
        msgs = []
        if kind == 'vlen':
            payload = [str(s).encode('utf-8') for s in raw_elems.ravel()]
            hid = self.heap_collection(payload) if payload else None
            data = b''.join(_u(len(p), 4) + w.a(hid) + _u(i, 4) for i, p in enumerate(payload, 1))
        else:
            data = arr.tobytes()
        fill_msg = None
        if v.get('attrs', {}).get('_FillValue') is not None or kind in ('vlen', 'char'):
            if self.style == 'classic':
                fill_msg = (0x05, bytes([2, 2, 2, 1]) + _u(len(fill), 4) + fill)
            else:
                fill_msg = (0x05, bytes([3, 0x02 | (0x02 << 2) | 0x20]) + _u(len(fill), 4) + fill)
        if layout == 'compact':
            msgs.append((0x08, bytes([3 if self.style == 'classic' else 4, 0]) + _u(len(data), 2) + data))
        elif layout == 'contiguous':
            addr = w.alloc(data) if len(data) and not v.get('unallocated') else None
            msgs.append((0x08, bytes([3 if self.style == 'classic' else 4, 1]) + w.a(addr) + _u(len(data), 8)))
        else:
            chunk = list(v.get('chunks') or [1 if d in unlimited_dims else max(1, s) for d, s in zip(v['dims'], shape)])
            if kind == 'char' and len(chunk) == rank - 1:
                chunk.append(shape[-1])
            filters = []
            if v.get('deflate'):
                if v.get('shuffle', True) and esz > 1:
                    filters.append((2, 'shuffle', [esz]))
                filters.append((1, 'deflate', [int(v['deflate'])]))
            if '_filters' in v:
                filters = list(v['_filters'])
            grid = [-(-s // c) for s, c in zip(shape, chunk)]
            if kind == 'vlen':
                velems = np.frombuffer(data, np.uint8).reshape(shape + (16,)) if data else np.zeros(shape + (16,), np.uint8)
                varr = velems.view(np.dtype('V16')).reshape(shape)
            else:
                varr = arr
            entries = []
            idx_kind = v.get('index')
            for cc in np.ndindex(*grid):
                cb, allfill = self.chunk_bytes(varr, cc, chunk, fill, esz)
                if allfill and v.get('skip_fill_chunks'):
                    continue
                stored = cb
                for fid, _, vals in filters:
                    if fid == 2:
                        stored = np.frombuffer(stored, np.uint8).reshape(-1, esz).T.tobytes()
                    elif fid == 1:
                        stored = zlib.compress(stored, vals[0])
                stored = v.get('_raw_chunks', {}).get(cc, stored)
                entries.append((cc, stored))
            if self.style == 'classic' or idx_kind == 'btree1':
                if self.style != 'classic':
                    raise ValueError('a v1 B-tree chunk index needs the classic style here')
                addrs = [(cc, w.alloc(s), len(s)) for cc, s in entries]
                bt = self.btree1_chunks(addrs, chunk, esz, v.get('btree_leaf', 64))
                msgs.append((0x08, bytes([3, 2, rank + 1]) + w.a(bt) + b''.join(_u(c, 4) for c in chunk) + _u(esz, 4)))
            else:
                msgs.append((0x08, self.layout4(entries, chunk, esz, shape, v, unlimited_dims, bool(filters))))
            if filters:
                msgs.append((0x0b, self.pipeline(filters)))
        if fill_msg:
            msgs.append(fill_msg)
        return msgs

    def pipeline(self, filters):
        if self.style == 'classic':
            b = bytes([1, len(filters)]) + b'\x00' * 6
            for fid, name, vals in filters:
                nm = _pad8(name.encode() + b'\x00')
                b += _u(fid, 2) + _u(len(nm), 2) + _u(1, 2) + _u(len(vals), 2) + nm + b''.join(_u(x, 4) for x in vals)
                if len(vals) % 2:
                    b += b'\x00' * 4
            return b
        b = bytes([2, len(filters)])
        for fid, _, vals in filters:
            b += _u(fid, 2) + _u(1, 2) + _u(len(vals), 2) + b''.join(_u(x, 4) for x in vals)
        return b

    def btree1_chunks(self, addrs, chunk, esz, leaf_max):
        """v1 B-tree of chunks (type 1); several levels when there are more than leaf_max chunks."""
        w = self.w
        rank = len(chunk)

        def key(cc, size):
            return _u(size, 4) + _u(0, 4) + b''.join(_u(c * k, 8) for c, k in zip(cc, chunk)) + _u(0, 8)
        last_key = key([g for g in (max((cc[d] for cc, _, _ in addrs), default=0) + 1 for d in range(rank))], 0)
        nodes = [(key(cc, size), a) for cc, a, size in addrs]           # (first key, child)
        level = 0
        if not nodes:
            return None
        while True:
            groups = [nodes[i:i + leaf_max] for i in range(0, len(nodes), leaf_max)]
            made = []
            for k, g in enumerate(groups):
                nxt = groups[k + 1][0][0] if k + 1 < len(groups) else last_key
                body = b'TREE' + bytes([1, level]) + _u(len(g), 2) + UNDEF8 + UNDEF8
                for kk, child in g:
                    body += kk + w.a(child)
                body += nxt
                made.append((g[0][0], w.alloc(body)))
            if len(made) == 1:
                return made[0][1]
            nodes, level = made, level + 1

    def layout4(self, entries, chunk, esz, shape, v, unlimited_dims, filtered):
        """Version 4 layout message and its chunk index."""
        w = self.w
        rank = len(chunk)
        kind = v.get('index') or ('earray' if len(unlimited_dims) == 1 else 'btree2' if unlimited_dims else
                                  'single' if len(entries) == 1 and all(c >= s for c, s in zip(chunk, shape)) else 'farray')
        dims_b = bytes([4]) + b''.join(_u(c, 4) for c in chunk) + _u(esz, 4)
        head = bytes([4, 2])
        grid = [-(-s // c) for s, c in zip(shape, chunk)]
        nchunks = int(np.prod(grid))

        def entry(addr, size):
            return w.a(addr) + ((_u(size, 4) + _u(0, 4)) if filtered else b'')
        if kind == 'single':
            cc, stored = entries[0]
            a = w.alloc(stored)
            flags = 0x02 if filtered else 0
            body = head + bytes([flags, rank + 1]) + dims_b + bytes([1])
            if filtered:
                body += _u(len(stored), 8) + _u(0, 4)
            return body + w.a(a)
        if kind == 'implicit':
            if filtered or len(entries) != nchunks:
                raise ValueError('an implicit index needs every chunk, unfiltered')
            first = None
            for cc, stored in entries:
                a = w.alloc(stored)
                first = a if first is None else first
            return head + bytes([0, rank + 1]) + dims_b + bytes([2]) + w.a(first)
        lin = {cc: i for i, cc in enumerate(np.ndindex(*grid))}
        if kind == 'earray':
            u = [k for k, d in enumerate(v['dims']) if d in unlimited_dims][0]
            order = [u] + [k for k in range(rank) if k != u]
            g2 = [grid[k] for k in order]
            lin = {cc: int(np.ravel_multi_index(tuple(cc[k] for k in order), g2)) for cc in np.ndindex(*grid)}
        placed = {lin[cc]: (w.alloc(stored), len(stored)) for cc, stored in entries}
        esz_e = 8 + (8 if filtered else 0)
        client = 1 if filtered else 0
        if kind == 'farray':
            n = nchunks
            elems = b''.join(entry(*placed[i]) if i in placed else UNDEF8 + (b'\x00' * 8 if filtered else b'')
                             for i in range(n))
            hdr_addr = w.reserve(4 + 4 + 8 + 8 + 4)
            dblk = w.alloc(w.checked(b'FADB' + bytes([0, client]) + w.a(hdr_addr) + elems))
            w.put(hdr_addr, w.checked(b'FAHD' + bytes([0, client, esz_e, 10]) + _u(n, 8) + w.a(dblk)))
            return head + bytes([0, rank + 1]) + dims_b + bytes([3, 10]) + w.a(hdr_addr)
        if kind == 'earray':
            max_bits, idx_n, min_ptrs, min_el, page_bits = 32, 4, 4, 16, 10
            n = (max(placed) + 1) if placed else 0
            nsblks = 1 + max_bits - (min_el.bit_length() - 1)
            ib_nsblks = 2 * (min_ptrs.bit_length() - 1)
            n_dblk, n_sblk = 2 * (min_ptrs - 1), nsblks - ib_nsblks

            def el(i):
                return entry(*placed[i]) if i in placed else UNDEF8 + (b'\x00' * 8 if filtered else b'')
            hdr_addr = w.reserve(12 + 6 * 8 + 8 + 4)
            dblk_addrs = []
            start, k = idx_n, 0
            while start < n:                                     # data blocks of super blocks 0, 1, 2, ...
                u = len(dblk_addrs)
                sb = 0
                cnt, acc = 0, 0
                while True:                                      # which super block holds data block number u
                    nd = 1 << (sb // 2)
                    if acc + nd > u:
                        break
                    acc += nd
                    sb += 1
                ne = (1 << ((sb + 1) // 2)) * min_el
                elems = b''.join(el(i) for i in range(start, start + ne))
                dblk_addrs.append(w.alloc(w.checked(b'EADB' + bytes([0, client]) + w.a(hdr_addr) + _u(start - idx_n, 4)
                                                     + elems)))
                start += ne
                k += 1
                if len(dblk_addrs) > n_dblk:
                    raise ValueError('extensible array fixture too large for its index block')
            iblk = w.alloc(w.checked(b'EAIB' + bytes([0, client]) + w.a(hdr_addr) + b''.join(el(i) for i in range(idx_n)) +
                                     b''.join(w.a(dblk_addrs[i] if i < len(dblk_addrs) else None) for i in range(n_dblk)) +
                                     UNDEF8 * n_sblk))
            w.put(hdr_addr, w.checked(b'EAHD' + bytes([0, client, esz_e, max_bits, idx_n, min_el, min_ptrs, page_bits]) +
                                      _u(0, 8) * 2 + _u(len(dblk_addrs), 8) + _u(0, 8) + _u(n, 8) + _u(n, 8) + w.a(iblk)))
            return head + bytes([0, rank + 1]) + dims_b + bytes([4, max_bits, idx_n, min_ptrs, min_el, page_bits]) + \
                w.a(hdr_addr)
        if kind == 'btree2':
            btype = 11 if filtered else 10
            recs = []
            for cc, stored in entries:
                a = w.alloc(stored)
                r = w.a(a) + ((_u(len(stored), 4) + _u(0, 4)) if filtered else b'') + b''.join(_u(c, 8) for c in cc)
                recs.append(r)
            rsz = 8 + (8 if filtered else 0) + 8 * rank
            node = max(512, 10 + len(recs) * rsz)
            leaf = w.alloc(w.checked(b'BTLF' + bytes([0, btype]) + b''.join(recs)) + b'\x00' * (node - 10 - len(recs) * rsz))
            hdr = w.alloc(w.checked(b'BTHD' + bytes([0, btype]) + _u(node, 4) + _u(rsz, 2) + _u(0, 2) + bytes([100, 40]) +
                                    w.a(leaf) + _u(len(recs), 2) + _u(len(recs), 8)))
            return head + bytes([0, rank + 1]) + dims_b + bytes([5]) + _u(node, 4) + bytes([100, 40]) + w.a(hdr)
        raise ValueError(f'unknown chunk index {kind}')

    # ---------------------------------------------------------- dense storage (latest style)
    def fractal_heap(self, objects, start=512, width=4, indirect=False, idlen=8):
        """A fractal heap holding objects -> (header address, heap ids)."""
        w = self.w
        max_direct, max_bits = 65536, 32
        off_b, len_b = 4, 2
        hdr_size = 4 + 1 + 8 + off_b
        ids, blocks = [], []
        if not indirect:
            need = hdr_size + sum(len(o) for o in objects)
            while start < need:
                start *= 2
            body, pos = b'', hdr_size
            for o in objects:
                ids.append((bytes([0]) + _u(pos, off_b) + _u(len(o), len_b) + b'\x00')[:idlen])
                body += o
                pos += len(o)
            hdr_addr = w.reserve(256)
            root = w.alloc(b'FHDB' + bytes([0]) + w.a(hdr_addr) + _u(0, off_b) + body + b'\x00' * (start - hdr_size - len(body)))
            rows = 0
        else:
            hdr_addr = w.reserve(256)
            # one object per direct block, every other entry left unallocated: offsets come from the doubling table
            entries, heap_off, row = [], 0, 0
            k = 0
            for o in objects:
                if hdr_size + len(o) > start:
                    raise ValueError('object larger than a heap block')
            rows = 0
            objs = list(objects)
            while objs:
                size = start if row < 2 else start << (row - 1)
                for _ in range(width):
                    if k % 2 == 0 and objs:
                        o = objs.pop(0)
                        a = w.alloc(b'FHDB' + bytes([0]) + w.a(hdr_addr) + _u(heap_off, off_b) + o +
                                    b'\x00' * (size - hdr_size - len(o)))
                        ids.append((bytes([0]) + _u(heap_off + hdr_size, off_b) + _u(len(o), len_b) + b'\x00')[:idlen])
                        entries.append(a)
                    else:
                        entries.append(None)
                    heap_off += size
                    k += 1
                row += 1
            rows = row
            root = w.alloc(w.checked(b'FHIB' + bytes([0]) + w.a(hdr_addr) + _u(0, off_b) +
                                     b''.join(w.a(e) for e in entries)))
        hdr = (b'FRHP' + bytes([0]) + _u(idlen, 2) + _u(0, 2) + bytes([0]) + _u(4096, 4) + _u(0, 8) + UNDEF8 + _u(0, 8) +
               UNDEF8 + _u(0, 8) * 3 + _u(len(objects), 8) + _u(0, 8) * 4 + _u(width, 2) + _u(start, 8) +
               _u(max_direct, 8) + _u(max_bits, 2) + _u(rows, 2) + w.a(root) + _u(rows, 2))
        w.put(hdr_addr, w.checked(hdr))
        return hdr_addr, ids

    def btree2(self, btype, records):
        w = self.w
        rsz = len(records[0])
        node = max(512, 10 + len(records) * rsz)
        leaf = w.alloc(w.checked(b'BTLF' + bytes([0, btype]) + b''.join(records)) + b'\x00' * (node - 10 - len(records) * rsz))
        return w.alloc(w.checked(b'BTHD' + bytes([0, btype]) + _u(node, 4) + _u(rsz, 2) + _u(0, 2) + bytes([100, 40]) +
                                 w.a(leaf) + _u(len(records), 2) + _u(len(records), 8)))

    def attr_msgs(self, attrs, heap_ids, tag, indirect=False):
        msgs = [self.attr_message(k, self.attr_parts(k, val, (heap_ids[0], {key[1]: i for key, i in heap_ids[1].items()
                                                                              if key[0] == tag})))
                for k, val in attrs.items()]
        if self.style == 'latest' and len(msgs) > self.dense_attrs:
            hdr, ids = self.fractal_heap(msgs, indirect=indirect)
            recs = [i + b'\x00' + _u(n, 4) + _u(lookup3(k.encode()), 4) for n, (i, k) in enumerate(zip(ids, attrs))]
            bt = self.btree2(8, recs)
            return [(0x15, bytes([0, 0]) + self.w.a(hdr) + self.w.a(bt))]
        return [(0x0c, m) for m in msgs]

    # ---------------------------------------------------------- groups
    def group(self, links, attr_msgs, dense=False):
        """A group header holding links {name: address} -> address."""
        w = self.w
        if self.style == 'classic':
            names = sorted(links)
            heap = b'\x00' * 8
            offs = {}
            for n in names:
                offs[n] = len(heap)
                heap += _pad8(n.encode('utf-8') + b'\x00')
            heap_data = w.alloc(heap)
            heap_hdr = w.alloc(b'HEAP' + bytes([0, 0, 0, 0]) + _u(len(heap), 8) + UNDEF8 + w.a(heap_data))
            snods = []
            for i in range(0, max(1, len(names)), 8):
                part = names[i:i + 8]
                body = b'SNOD' + bytes([1, 0]) + _u(len(part), 2)
                for n in part:
                    body += _u(offs[n], 8) + w.a(links[n]) + _u(0, 4) + b'\x00' * 4 + b'\x00' * 16
                body += b'\x00' * ((8 - len(part)) * 40)
                snods.append((part, w.alloc(body)))
            tree = b'TREE' + bytes([0, 0]) + _u(len(snods), 2) + UNDEF8 + UNDEF8 + _u(0, 8)
            for part, a in snods:
                tree += w.a(a) + _u(offs[part[-1]] if part else 0, 8)
            bt = w.alloc(tree)
            addr = self.header([(0x11, w.a(bt) + w.a(heap_hdr))] + attr_msgs)
            return addr, bt, heap_hdr
        if dense:
            msgs = [self.link_msg(n, a) for n, a in links.items()]
            hdr, ids = self.fractal_heap(msgs, idlen=7)
            recs = [_u(lookup3(n.encode()), 4) + i for n, i in zip(links, ids)]
            bt = self.btree2(5, recs)
            info = (0x02, bytes([0, 0]) + w.a(hdr) + w.a(bt))
            return self.header([info, (0x0a, bytes([0, 0]))] + attr_msgs), None, None
        info = (0x02, bytes([0, 0]) + UNDEF8 + UNDEF8)
        lm = [(0x06, self.link_msg(n, a)) for n, a in links.items()]
        return self.header([info, (0x0a, bytes([0, 0]))] + lm + attr_msgs), None, None

    @staticmethod
    def link_msg(name, addr):
        nm = name.encode('utf-8')
        return bytes([1, 0x00]) + bytes([len(nm)]) + nm + _u(addr, 8)

    # ---------------------------------------------------------- everything
    def build(self):
        w = self.w
        sb_size = 96 if self.style == 'classic' else 48
        w.reserve(sb_size)
        unlimited = {d for d, s in self.dims.items() if s is None}
        sizes = {}
        allvars = [('', n, v) for n, v in self.vars.items()]
        for g, spec in self.groups.items():
            allvars += [(g, n, v) for n, v in spec.get('vars', {}).items()]
        prepared = {}
        for g, n, v in allvars:
            arr, kind, fill = self.prepare(n, v)
            prepared[(g, n)] = (arr, kind, fill)
            for d, s in zip(v['dims'], arr.shape):
                sizes[d] = max(sizes.get(d, 0), s)
        dsize = {d: (sizes.get(d, 0) if s is None else s) for d, s in self.dims.items()}
        # NC_STRING attribute values live in one global heap collection, written first
        strings = []
        tags = [('', None, self.attrs)] + [(g, None, spec.get('attrs', {})) for g, spec in self.groups.items()]
        tags += [(g, n, v.get('attrs', {})) for g, n, v in allvars]
        for g, n, attrs in tags:
            strings += self.string_objects(attrs, (g, n))
        heap_addr = self.heap_collection([s.encode('utf-8') for _, _, s in strings]) if strings else None
        heap_index = {(key[0], (key[1], i)): k for k, (key, i, _) in enumerate(strings, 1)}

        def ids_for(tag):
            return heap_addr, {(t, ki): k for (t, ki), k in heap_index.items() if t == tag}

        dimid = {d: i for i, d in enumerate(self.dims)}
        scale_addr, links = {}, {}
        # dimension scales: coordinate variables, or dimension-only datasets
        for d in self.dims:
            v = self.vars.get(d)
            maxd = [None] if d in unlimited else None
            if v is not None and tuple(v['dims']) == (d,):
                arr, kind, fill = prepared[('', d)]
                extra = {'CLASS': 'DIMENSION_SCALE', 'NAME': d, '_Netcdf4Dimid': np.int32(dimid[d])}
                msgs = [(0x01, _space([arr.shape[0]], maxd, self.style)), (0x03, self.dtype_bytes(arr, kind))]
                msgs += self.layout_and_data(d, v, arr, kind, None, fill, unlimited & {d})
                amsgs = self.attr_msgs({**v.get('attrs', {}), **extra}, ids_for(('', d)), ('', d))
                scale_addr[d] = self.header(msgs + amsgs if not v.get('continuation') else msgs,
                                            amsgs if v.get('continuation') else ())
            else:
                n = dsize[d]
                extra = {'CLASS': 'DIMENSION_SCALE', 'NAME': f'{NOT_A_VARIABLE}{n:10d}', '_Netcdf4Dimid': np.int32(dimid[d])}
                lay = (bytes([3 if self.style == 'classic' else 4, 1]) + UNDEF8 + _u(n * 4, 8)) if d not in unlimited else \
                    (bytes([3, 2, 2]) + UNDEF8 + _u(1, 4) + _u(4, 4) if self.style == 'classic' else
                     bytes([4, 2, 0, 2, 4]) + _u(1, 4) + _u(4, 4) + bytes([4, 32, 4, 4, 16, 10]) + UNDEF8)
                msgs = [(0x01, _space([n], maxd, self.style)), (0x03, _dt_numeric('<f4')), (0x08, lay)]
                msgs += self.attr_msgs(extra, ids_for(None), None)
                scale_addr[d] = self.header(msgs)
            links[d] = scale_addr[d]
        # DIMENSION_LIST of every other variable: references in a second global heap collection
        lists = {}
        refs = []
        for g, n, v in allvars:
            if g == '' and n in self.dims and tuple(v['dims']) == (n,):
                continue
            arr, kind, _ = prepared[(g, n)]
            vd = list(v['dims']) if kind != 'char' else list(v['dims'])
            if vd:
                lists[(g, n)] = len(refs)
                refs += [_u(scale_addr[d], 8) for d in vd]
        ref_heap = self.heap_collection(refs) if refs else None
        group_links = {g: {} for g in self.groups}
        for g, n, v in allvars:
            if g == '' and n in self.dims and tuple(v['dims']) == (n,):
                continue
            arr, kind, fill = prepared[(g, n)]
            dims = list(arr.shape)
            maxd = [None if d in unlimited else None for d in v['dims']] if any(d in unlimited for d in v['dims']) else None
            if maxd is not None:
                maxd = [None if d in unlimited else s for d, s in zip(v['dims'], dims)]
            msgs = [(0x01, _space(list(v.get('_dims', dims)), maxd, self.style)), (0x03, self.dtype_bytes(arr, kind))]
            msgs += self.layout_and_data(n, v, arr, kind, None, fill, unlimited & set(v['dims']))
            attrs = dict(v.get('attrs', {}))
            if (g, n) in lists:
                first = lists[(g, n)]
                dl = b''.join(_u(1, 4) + w.a(ref_heap) + _u(first + k + 1, 4) for k in range(len(v['dims'])))
                attrs['DIMENSION_LIST'] = (_dt_vlen_refs(), _space([len(v['dims'])], None, self.style), dl)
            amsgs = self.attr_msgs(attrs, ids_for((g, n)), (g, n), indirect=v.get('heap_indirect', False))
            addr = self.header(msgs + amsgs if not v.get('continuation') else msgs, amsgs if v.get('continuation') else ())
            (links if g == '' else group_links[g])[n] = addr
        for g, spec in self.groups.items():
            ga, _, _ = self.group(group_links[g], self.attr_msgs(spec.get('attrs', {}), ids_for((g, None)), (g, None)))
            links[g] = ga
        gattrs = dict(self.attrs)
        gattrs.setdefault('_NCProperties', 'version=2,netcdf=4.9.2,hdf5=1.14.3')
        root, bt, heap = self.group(links, self.attr_msgs(gattrs, ids_for(('', None)), ('', None)), self.dense_links)
        eof = len(w.b)
        if self.style == 'classic':
            sb = (b'\x89HDF\r\n\x1a\n' + bytes([0, 0, 0, 0, 0, 8, 8, 0]) + _u(4, 2) + _u(16, 2) + _u(0, 4) + _u(0, 8) +
                  UNDEF8 + _u(eof, 8) + UNDEF8 + _u(0, 8) + _u(root, 8) + _u(1, 4) + _u(0, 4) + _u(bt, 8) + _u(heap, 8))
        else:
            sb = w.checked(b'\x89HDF\r\n\x1a\n' + bytes([2, 8, 8, 0]) + _u(0, 8) + UNDEF8 + _u(eof, 8) + _u(root, 8))
        w.put(0, sb)
        return bytes(w.b)

    def dtype_bytes(self, arr, kind):
        if kind == 'char':
            return _dt_string(1)
        if kind == 'vlen':
            return _dt_vlen_string()
        return _dt_numeric(arr.dtype)


def write_bytes(dims, vars, attrs=None, style='classic', groups=None, dense_links=False, dense_attrs=8):
    """dims {name: size or None}, vars and global attrs -> NetCDF-4 bytes (see the top of this module)."""
    if style not in ('classic', 'latest'):
        raise ValueError(style)
    return _File(dims, vars, attrs, style, groups, dense_links, dense_attrs).build()


# ------------------------------------------------------------------ ready-made fixtures
def model_fixture():
    """A small CF model file: packed int16 temperature in deflated, shuffled chunks with partial edge chunks and one
    unallocated chunk, float salinity, an unlimited time axis, a bounds dimension without a coordinate variable, NC_CHAR
    and NC_STRING variables, a subgroup, and attributes of each kind. Returns (dims, vars, attrs, groups)."""
    t = np.array([0.0, 24.0])
    z = np.array([0.5, 50.0, 500.0])
    y = np.array([10.0, 12.0, 14.0, 16.0])
    x = np.array([80.0, 82.0, 84.0, 86.0, 88.0])
    T, Z, Y, X = np.meshgrid(t, z, y, x, indexing='ij')
    temp = 29 - Z / 60 + 0.05 * Y + 0.01 * X + T / 24
    temp[:, :, 0, 0] = np.nan                                   # a land column
    temp[1, 2, 2:, 3:] = np.nan                                 # a whole chunk of fill: left unallocated
    sal = (34 + Z / 2000 - 0.02 * Y).astype('f4')
    sal[0, 0, 3, 4] = np.nan
    dims = {'time': None, 'depth': 3, 'lat': 4, 'lon': 5, 'nv': 2, 'strlen': 8}
    vars = {
        'time': {'dims': ('time',), 'attrs': {'units': 'hours since 2020-05-13 12:00:00', 'standard_name': 'time',
                                              'calendar': 'gregorian', 'axis': 'T'}, 'data': t},
        'depth': {'dims': ('depth',), 'attrs': {'units': 'm', 'positive': 'down', 'standard_name': 'depth'},
                  'data': z.astype('f4')},
        'lat': {'dims': ('lat',), 'attrs': {'units': 'degrees_north', 'standard_name': 'latitude',
                                            'bounds': 'lat_bnds'}, 'data': y.astype('f4')},
        'lon': {'dims': ('lon',), 'attrs': {'units': 'degrees_east', 'standard_name': 'longitude'},
                'data': x.astype('f4')},
        'lat_bnds': {'dims': ('lat', 'nv'), 'data': np.stack([y - 1, y + 1], 1).astype('f4'), 'attrs': {}},
        'thetao': {'dims': ('time', 'depth', 'lat', 'lon'), 'dtype': 'i2', 'chunks': (1, 2, 2, 3), 'deflate': 4,
                   'skip_fill_chunks': True,
                   'attrs': {'standard_name': 'sea_water_potential_temperature', 'units': 'degrees_C',
                             '_FillValue': np.int16(-32767), 'scale_factor': 0.001, 'add_offset': 20.0,
                             'valid_min': np.int16(-20000), 'valid_max': np.int16(20000), 'long_name': 'Temperature'},
                   'data': temp},
        'so': {'dims': ('time', 'depth', 'lat', 'lon'), 'dtype': 'f4',
               'attrs': {'standard_name': 'sea_water_salinity', 'units': '1e-3', '_FillValue': np.float32(1e20)},
               'data': sal},
        'platform': {'dims': ('time', 'strlen'), 'dtype': 'S1', 'data': np.array(['GLORYS', 'HYCOM-X']),
                     'attrs': {'long_name': 'source tag'}},
        'note': {'dims': ('time',), 'string': True, 'data': np.array(['first day', 'second, ünïcode']), 'attrs': {}},
    }
    attrs = {'title': 'nc4 fixture', 'Conventions': 'CF-1.8', 'history': String('made by nc4_fixtures.py'),
             'keywords': [String('ocean'), String('test')], 'level_count': np.int32(3),
             'resolution': np.array([2.0, 2.0])}
    groups = {'extra': {'vars': {'flag': {'dims': ('lat',), 'dtype': 'i1', 'data': np.array([1, 0, 1, 1], 'i1'),
                                          'attrs': {'flag_meanings': 'ok bad'}}},
                        'attrs': {'purpose': 'subgroup'}}}
    return dims, vars, attrs, groups


def latest_fixture():
    """Every version 4 chunk index, dense attributes (direct and indirect fractal heap roots), a compact variable,
    an attribute continuation block and dense links. Returns (dims, vars, attrs, kwargs for write_bytes)."""
    rng = np.random.default_rng(7)
    a2 = rng.normal(size=(6, 7)).astype('f4')
    many = {f'attr_{k:02d}': (k if k % 3 else f'text {k}') for k in range(12)}
    dims = {'t': None, 'y': 6, 'x': 7, 's': 1}
    vars = {
        'single': {'dims': ('y', 'x'), 'data': a2, 'layout': 'chunked', 'chunks': (6, 7), 'deflate': 6, 'index': 'single',
                   'attrs': many},
        'implicit': {'dims': ('y', 'x'), 'data': a2 * 2, 'layout': 'chunked', 'chunks': (3, 4), 'index': 'implicit',
                     'attrs': {'units': 'm'}},
        'farray': {'dims': ('y', 'x'), 'data': np.arange(42, dtype='i4').reshape(6, 7), 'chunks': (2, 3), 'deflate': 1,
                   'index': 'farray', 'attrs': {**many, 'heap': 'indirect'}, 'heap_indirect': True},
        'earray': {'dims': ('t', 'x'), 'data': np.arange(10 * 7, dtype='f8').reshape(10, 7), 'chunks': (1, 7),
                   'deflate': 2, 'index': 'earray', 'attrs': {'units': '1'}},
        'btree2': {'dims': ('t', 'y'), 'data': np.arange(60, dtype='i2').reshape(10, 6), 'chunks': (3, 4), 'deflate': 3,
                   'index': 'btree2', 'attrs': {}},
        'compact': {'dims': ('s',), 'data': np.array([42.5]), 'layout': 'compact', 'attrs': {'units': 'K'}},
        'cont': {'dims': ('x',), 'data': np.arange(7, dtype='u2'), 'continuation': True,
                 'attrs': {'units': 'count', 'long_name': 'in a continuation block'}},
    }
    return dims, vars, {'title': 'latest-format fixture'}, {'style': 'latest', 'dense_links': True}
