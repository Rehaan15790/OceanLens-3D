/* ============================================================
   NETCDF-4 (HDF5) READER: the HDF5 structures netCDF-C writes, as data-pipeline/nc4.py reads them
   await NetCDF4.parse(arrayBuffer) -> the same object as NetCDF3.parse: {version, numrecs, dims, attrs, vars, read(name),
   raw(name), attr(var, name)}. read() unpacks with NetCDF3.unpack, so both formats follow the same rules.
   Read: superblocks 0-3, object headers 1 and 2, groups by symbol table or by link messages (compact or dense),
   attributes (compact or dense), fixed-point, float, string and variable-length string types, compact, contiguous and
   chunked layouts with every chunk index (v1 B-tree, single, implicit, fixed array, extensible array, v2 B-tree), the
   deflate (the browser's DecompressionStream), shuffle and fletcher32 filters, and netCDF dimension scales.
   Every variable is decoded during parse(), so read() stays synchronous. Offsets and sizes are checked against the
   file, inflation against each chunk's size, and loops in the file's structures refused: a damaged or hostile file
   ends in an Error with a message, never a hang or a huge allocation.
   ============================================================ */
const NetCDF4 = (()=>{
  const SIG = [0x89, 0x48, 0x44, 0x46, 0x0d, 0x0a, 0x1a, 0x0a];
  const UNDEF = -1;
  const MAX_DEPTH = 64, MAX_MESSAGES = 65536, MAX_CHUNKS = 1 << 20;
  const MAX_VALUES = 5e7;                        // per variable, as the NetCDF-3 reader
  const HIDDEN = new Set(['DIMENSION_LIST', 'REFERENCE_LIST', 'CLASS', 'NAME', '_Netcdf4Dimid', '_Netcdf4Coordinates',
                          '_NCProperties', '_nc3_strict', '_IsNetcdf4', '_SuperblockVersion']);
  const NOT_A_VARIABLE = 'This is a netCDF dimension but not a netCDF variable';
  const NON_COORD = '_nc4_non_coord_';
  const fail = msg=>{ throw new Error(msg); };

  // little-endian integer of up to 8 bytes; addresses past 2^53 are refused
  function le(s){
    let v = 0;
    for(let i=s.length-1; i>=0; i--) v = v*256 + s[i];
    if(v > Number.MAX_SAFE_INTEGER) fail('an offset in the file is too large');
    return v;
  }
  class Cur {
    constructor(b, pos, osz, lsz, what){ Object.assign(this, {b, p:pos || 0, osz, lsz, what}); }
    take(n){
      if(!(n >= 0) || this.p + n > this.b.length) fail(`${this.what} is cut short`);
      const s = this.b.subarray(this.p, this.p + n);
      this.p += n;
      return s;
    }
    u(n){ return n ? le(this.take(n)) : 0; }
    u8(){ return this.u(1); }
    u16(){ return this.u(2); }
    u32(){ return this.u(4); }
    addr(){ const s = this.take(this.osz); return s.every(b=>b===255) ? UNDEF : le(s); }
    length(){ const s = this.take(this.lsz); return s.every(b=>b===255) ? UNDEF : le(s); }
    sig(t){
      const s = this.take(4);
      if(String.fromCharCode(...s)!==t) fail(`expected ${t} in ${this.what}`);
    }
    align(base){ this.p += (8 - (this.p - base) % 8) % 8; }
    cstr(){
      const end = this.b.indexOf(0, this.p);
      if(end < 0) fail(`unterminated name in ${this.what}`);
      const s = new TextDecoder('utf-8').decode(this.b.subarray(this.p, end));
      this.p = end + 1;
      return s;
    }
  }
  const encSize = n=>Math.floor(Math.log2(Math.max(1, n))/8) + 1;     // H5VM_limit_enc_size

  // ---------------------------------------------------------------- datatypes and dataspaces
  // {cls, size, kind: 'int'|'uint'|'float'|'string'|'char'|'vlenstr'|'vlen'|'ref'|'other', be, base}
  function datatype(c){
    const b0 = c.u8(), cls = b0 & 15, ver = b0 >> 4, bits = c.u(3), size = c.u32();
    if(size > 1 << 20) fail(`datatype size ${size} is not plausible`);
    const t = {cls, size, kind:'other', be:!!(bits & 1)};
    if(cls===0){
      c.take(4);
      if(![1, 2, 4, 8].includes(size)) fail(`integers of ${size} bytes are not supported`);
      t.kind = bits & 8 ? 'int' : 'uint';
    } else if(cls===1){
      c.take(12);
      if(![4, 8].includes(size) || bits & 0x40) fail(`floats of ${size} bytes are not supported`);
      t.kind = 'float';
    } else if(cls===3){
      t.kind = size===1 ? 'char' : 'string';
    } else if(cls===2 || cls===4){
      if(cls===4) c.take(4);
    } else if(cls===5){
      c.take(bits & 255);
    } else if(cls===6){                          // compound: parsed to be skipped (REFERENCE_LIST)
      const n = bits & 0xffff;
      for(let i=0; i<n; i++){
        const p0 = c.p;
        c.cstr();
        if(ver < 3) c.align(p0);
        if(ver===1){ c.u32(); c.take(28); }
        else if(ver===2) c.u32();
        else c.u(encSize(size));
        datatype(c);
      }
    } else if(cls===7){
      if(ver >= 4) fail('new-style references are not supported');
      t.kind = 'ref';
    } else if(cls===8){
      const n = bits & 0xffff, base = datatype(c);
      for(let i=0; i<n; i++){ const p0 = c.p; c.cstr(); if(ver < 3) c.align(p0); }
      c.take(n*base.size);
      Object.assign(t, {kind:base.kind, be:base.be});
    } else if(cls===9){
      t.kind = (bits & 15)===1 ? 'vlenstr' : 'vlen';
      t.base = datatype(c);
    } else if(cls===10){
      const rank = c.u8();
      if(ver < 3) c.take(3);
      c.take(4*rank);
      if(ver < 3) c.take(4*rank);
      datatype(c);
    } else fail(`datatype class ${cls} is not supported`);
    return t;
  }
  function dataspace(c){
    const ver = c.u8(), rank = c.u8(), flags = c.u8();
    let kind = 1;
    if(ver===1) c.take(5);
    else if(ver===2) kind = c.u8();
    else fail(`dataspace version ${ver} is not supported`);
    if(rank > 32) fail(`${rank} dimensions is not plausible`);
    const dims = [], maxd = [];
    for(let i=0; i<rank; i++){ const d = c.length(); if(d===UNDEF) fail('a dimension size is undefined'); dims.push(d); }
    for(let i=0; i<rank; i++) maxd.push(flags & 1 ? c.length() : dims[i]);
    return kind===2 ? {dims:null, maxd:null} : {dims, maxd:maxd.map(m=>m===UNDEF ? null : m)};
  }
  // the NetCDF-3 reader's type name for a datatype
  function typeName(t){
    if(t.kind==='char') return 'char';
    if(t.kind==='int') return {1:'byte', 2:'short', 4:'int', 8:'int64'}[t.size];
    if(t.kind==='uint') return {1:'ubyte', 2:'ushort', 4:'uint', 8:'uint64'}[t.size];
    if(t.kind==='float') return t.size===4 ? 'float' : 'double';
    if(t.kind==='string' || t.kind==='vlenstr') return 'string';
    return null;
  }
  // element i of a numeric type from bytes
  function readNum(dv, t, at){
    const little = !t.be;
    switch(t.kind + t.size){
      case 'int1': return dv.getInt8(at);
      case 'uint1': return dv.getUint8(at);
      case 'int2': return dv.getInt16(at, little);
      case 'uint2': return dv.getUint16(at, little);
      case 'int4': return dv.getInt32(at, little);
      case 'uint4': return dv.getUint32(at, little);
      case 'int8': return Number(dv.getBigInt64(at, little));
      case 'uint8': return Number(dv.getBigUint64(at, little));
      case 'ref8': return Number(dv.getBigUint64(at, true));
      case 'float4': return dv.getFloat32(at, little);
      case 'float8': return dv.getFloat64(at, little);
    }
    return NaN;
  }

  // ---------------------------------------------------------------- the file
  function open(buffer){
    const u8 = new Uint8Array(buffer);
    const F = {u8, objs:new Map(), gheaps:new Map()};
    const rd = (off, n, what)=>{
      if(!(off >= 0) || !(n >= 0) || off + n > u8.length) fail(`The file ends inside ${what} (truncated?).`);
      return u8.subarray(off, off + n);
    };
    const cur = (b, pos, what)=>new Cur(b, pos, F.osz, F.lsz, what);
    const at = a=>a===UNDEF ? UNDEF : a + F.base;

    // superblock
    let off = -1;
    for(const o of [0, 512, 1024, 2048, 4096, 8192]){
      if(o + 8 <= u8.length && SIG.every((b, i)=>u8[o+i]===b)){ off = o; break; }
    }
    if(off < 0) fail('Not an HDF5 file.');
    {
      const c = new Cur(rd(off, Math.min(256, u8.length - off), 'the superblock'), 8, 8, 8, 'the superblock');
      const ver = c.u8();
      let base, root;
      if(ver===0 || ver===1){
        c.take(4);
        F.osz = c.u8(); F.lsz = c.u8();
        c.take(9);
        if(ver===1) c.take(4);
        if(![2, 4, 8].includes(F.osz) || ![2, 4, 8].includes(F.lsz)) fail('HDF5 offset or length size is not supported');
        c.osz = F.osz; c.lsz = F.lsz;
        base = c.addr(); c.addr(); c.addr(); c.addr(); c.addr();
        root = c.addr();
      } else if(ver===2 || ver===3){
        F.osz = c.u8(); F.lsz = c.u8();
        if(![2, 4, 8].includes(F.osz) || ![2, 4, 8].includes(F.lsz)) fail('HDF5 offset or length size is not supported');
        c.osz = F.osz; c.lsz = F.lsz;
        c.u8();
        base = c.addr(); c.addr(); c.addr();
        root = c.addr();
      } else fail(`HDF5 superblock version ${ver} is not supported`);
      if(root===UNDEF) fail('the root group address is undefined');
      F.base = base===UNDEF ? off : base;
      F.root = root;
    }

    // ---------------------------------------------------------------- object headers
    function object(addr){
      if(F.objs.has(addr)) return F.objs.get(addr);
      if(F.objs.size > 1 << 18) fail('too many objects');
      const o = {addr, msgs:[], attrs:{}, dims:null, maxd:null, dtype:null, layout:null, filters:[], fill:null, links:null};
      F.objs.set(addr, o);
      const a = at(addr);
      if(!(a >= 0 && a < u8.length)) fail(`an object header address is outside the file`);
      const head = rd(a, Math.min(16, u8.length - a), 'an object header');
      if(String.fromCharCode(...head.subarray(0, 4))==='OHDR') headerV2(o, a);
      else if(head[0]===1) headerV1(o, a);
      else fail('no object header where the file says one is');
      interpret(o);
      return o;
    }
    function continuation(data, blocks){
      const c = cur(data, 0, 'a continuation message');
      blocks.push([at(c.addr()), c.length()]);
    }
    function headerV1(o, a){
      const c = cur(rd(a, 16, 'an object header'), 0, 'an object header');
      c.take(4); c.u32();
      const blocks = [[a + 16, c.u32()]], seen = new Set();
      while(blocks.length){
        const [start, size] = blocks.shift(), key = start + ':' + size;
        if(seen.has(key) || seen.size > 4096) fail('object header continuation loop');
        seen.add(key);
        const b = rd(start, size, 'an object header'), m = cur(b, 0, 'an object header');
        while(m.p + 8 <= b.length){
          const type = m.u16(), msize = m.u16(), flags = m.u8();
          m.take(3);
          const data = m.take(msize);
          if(flags & 2 && type!==0x10) fail('shared header messages are not supported');
          if(type===0x10) continuation(data, blocks); else o.msgs.push([type, data]);
          if(o.msgs.length > MAX_MESSAGES) fail('too many header messages');
        }
      }
    }
    function headerV2(o, a){
      const c = cur(rd(a, Math.min(48, u8.length - a), 'an object header'), 0, 'an object header');
      c.sig('OHDR');
      if(c.u8()!==2) fail('object header version is not 2');
      const flags = c.u8();
      if(flags & 0x20) c.take(16);
      if(flags & 0x10) c.take(4);
      const size = c.u(1 << (flags & 3)), order = !!(flags & 4);
      const blocks = [[a + c.p, size]], seen = new Set();
      let first = true;
      while(blocks.length){
        const [start, sz] = blocks.shift(), key = start + ':' + sz;
        if(seen.has(key) || seen.size > 4096) fail('object header continuation loop');
        seen.add(key);
        let b = rd(start, sz, 'an object header');
        if(!first){
          if(String.fromCharCode(...b.subarray(0, 4))!=='OCHK') fail('object header continuation without OCHK');
          b = b.subarray(4, Math.max(4, b.length - 4));
        }
        first = false;
        const m = cur(b, 0, 'an object header'), hdr = order ? 6 : 4;
        while(m.p + hdr <= b.length){
          const type = m.u8(), msize = m.u16(), mflags = m.u8();
          if(order) m.u16();
          const data = m.take(msize);
          if(mflags & 2 && type!==0x10) fail('shared header messages are not supported');
          if(type===0x10) continuation(data, blocks); else o.msgs.push([type, data]);
          if(o.msgs.length > MAX_MESSAGES) fail('too many header messages');
        }
      }
    }
    function interpret(o){
      let attrInfo = null, linkInfo = null, symtab = null;
      for(const [type, data] of o.msgs){
        const c = cur(data, 0, `header message ${type}`);
        if(type===0x01){ const s = dataspace(c); o.dims = s.dims; o.maxd = s.maxd; }
        else if(type===0x03) o.dtype = datatype(c);
        else if(type===0x05) o.fill = fillNew(c);
        else if(type===0x04 && !o.fill){ const n = c.u32(); o.fill = n ? c.take(n) : null; }
        else if(type===0x08) o.layout = layout(c);
        else if(type===0x0b) o.filters = filters(c);
        else if(type===0x0c) attribute(o, c);
        else if(type===0x15) attrInfo = c;
        else if(type===0x02) linkInfo = c;
        else if(type===0x06){ o.links = o.links || {}; const [n, t] = link(c); if(t!==null) o.links[n] = t; }
        else if(type===0x11) symtab = [c.addr(), c.addr()];
      }
      if(symtab){ o.links = o.links || {}; symbolTable(o, ...symtab); }
      if(linkInfo){ o.links = o.links || {}; denseLinks(o, linkInfo); }
      if(attrInfo) denseAttrs(o, attrInfo);
    }
    function fillNew(c){
      const ver = c.u8();
      if(ver===1 || ver===2){
        c.u8(); c.u8();
        const defined = c.u8();
        if(ver===2 && !defined) return null;
        const n = c.u32();
        return n ? c.take(n) : null;
      }
      if(ver===3){
        const flags = c.u8();
        if(flags & 0x20){ const n = c.u32(); return n ? c.take(n) : null; }
        return null;
      }
      fail(`fill value message version ${ver} is not supported`);
    }
    function layout(c){
      const ver = c.u8(), L = {ver};
      if(ver===1 || ver===2){
        const nd = c.u8();
        L.cls = c.u8();
        c.take(5);
        if(L.cls!==0) L.addr = c.addr();
        L.chunk = []; for(let i=0; i<nd; i++) L.chunk.push(c.u32());
        if(L.cls===0) L.data = c.take(c.u32());
        if(L.cls===2) L.index = 'btree1';
        return L;
      }
      if(ver!==3 && ver!==4) fail(`layout message version ${ver} is not supported`);
      L.cls = c.u8();
      if(L.cls===0) L.data = c.take(c.u16());
      else if(L.cls===1){ L.addr = c.addr(); L.size = c.length(); }
      else if(L.cls===2){
        if(ver===3){
          const nd = c.u8();
          L.addr = c.addr();
          L.chunk = []; for(let i=0; i<nd; i++) L.chunk.push(c.u32());
          L.index = 'btree1';
        } else {
          const flags = c.u8(), nd = c.u8(), enc = c.u8();
          if(enc < 1 || enc > 8) fail('chunk sizes in a layout message are not plausible');
          L.chunk = []; for(let i=0; i<nd; i++) L.chunk.push(c.u(enc));
          const kind = c.u8();
          if(kind===1){ L.index = 'single'; if(flags & 2){ L.singleSize = c.length(); L.singleMask = c.u32(); } }
          else if(kind===2) L.index = 'implicit';
          else if(kind===3){ L.index = 'farray'; c.u8(); }
          else if(kind===4){ L.index = 'earray'; c.take(5); }
          else if(kind===5){ L.index = 'btree2'; c.take(6); }
          else fail(`chunk index type ${kind} is not supported`);
          L.addr = c.addr();
        }
      } else fail(L.cls===3 ? 'virtual datasets are not supported' : `layout class ${L.cls} is not supported`);
      return L;
    }
    function filters(c){
      const ver = c.u8(), n = c.u8(), out = [];
      if(ver===1) c.take(6);
      else if(ver!==2) fail(`filter pipeline version ${ver} is not supported`);
      for(let i=0; i<n; i++){
        const id = c.u16(), nlen = ver===1 || id >= 256 ? c.u16() : 0;
        c.u16();
        const nval = c.u16();
        let name = '';
        if(nlen){ const s = c.take(nlen); name = new TextDecoder().decode(s.subarray(0, Math.max(0, s.indexOf(0)) || s.length)); }
        const vals = [];
        for(let k=0; k<nval; k++) vals.push(c.u32());
        if(ver===1 && nval % 2) c.take(4);
        out.push({id, name, vals});
      }
      return out;
    }
    function attribute(o, c){
      const ver = c.u8(), flags = c.u8(), nsz = c.u16(), tsz = c.u16(), ssz = c.u16();
      if(ver===3) c.u8();
      if(flags & 3) fail('shared datatypes in attributes are not supported');
      const pad = ver===1 ? 8 : 1, skip = n=>c.take((pad - n % pad) % pad);
      const nb = c.take(nsz); skip(nsz);
      const z = nb.indexOf(0), name = new TextDecoder('utf-8').decode(z >= 0 ? nb.subarray(0, z) : nb);
      const tb = c.take(tsz); skip(tsz);
      const sb = c.take(ssz); skip(ssz);
      const t = datatype(cur(tb, 0, `attribute ${name}`)), s = dataspace(cur(sb, 0, `attribute ${name}`));
      if(!s.dims){ o.attrs[name] = {t, dims:[], raw:new Uint8Array(0), empty:true}; return; }
      const n = s.dims.reduce((a, b)=>a*b, 1), esz = t.cls===9 ? 4 + F.osz + 4 : t.size;
      if(n*esz > c.b.length - c.p) fail(`attribute ${name} is cut short`);
      o.attrs[name] = {t, dims:s.dims, raw:c.take(n*esz)};
    }
    // an attribute's value as the NetCDF-3 reader gives it: a string, a number, or an array of numbers
    function attrValue(a){
      if(a.empty) return '';
      const {t, raw} = a, n = a.dims.reduce((x, y)=>x*y, 1), dec = new TextDecoder('utf-8');
      if(t.cls===3){
        if(t.size===1 || n===1) return dec.decode(raw).replace(/\0+$/, '');
        const out = [];
        for(let i=0; i<n; i++) out.push(dec.decode(raw.subarray(i*t.size, (i+1)*t.size)).replace(/[\0 ]+$/, ''));
        return out;
      }
      if(t.cls===9){
        const items = vlen(t, raw, n);
        return t.kind==='vlenstr' ? (n===1 ? items[0] : items) : items;
      }
      if(!['int', 'uint', 'float'].includes(t.kind)) return undefined;
      const dv = new DataView(raw.buffer, raw.byteOffset, raw.byteLength), out = [];
      for(let i=0; i<n; i++) out.push(readNum(dv, t, i*t.size));
      return n===1 ? out[0] : out;
    }
    function vlen(t, raw, n){
      const c = cur(raw, 0, 'a variable-length value'), out = [];
      for(let i=0; i<n; i++){
        const len = c.u32(), coll = c.addr(), idx = c.u32();
        if(coll===UNDEF || len===0){ out.push(t.kind==='vlenstr' ? '' : []); continue; }
        const data = gheap(coll, idx);
        if(t.kind==='vlenstr') out.push(new TextDecoder('utf-8').decode(data.subarray(0, len)).replace(/\0+$/, ''));
        else {
          const bt = t.base, sz = bt ? bt.size : 1;
          if(len*sz > data.length) fail('a variable-length value is longer than its heap object');
          const dv = new DataView(data.buffer, data.byteOffset, data.byteLength), vals = [];
          for(let k=0; k<len; k++) vals.push(bt ? readNum(dv, bt, k*sz) : data[k]);
          out.push(vals);
        }
      }
      return out;
    }
    function gheap(addr, idx){
      if(!F.gheaps.has(addr)){
        const a = at(addr), c = cur(rd(a, 8 + F.lsz, 'a global heap'), 0, 'a global heap');
        c.sig('GCOL'); c.take(4);
        const size = c.length();
        if(size===UNDEF || size > 1 << 28 || size < 8 + F.lsz) fail('a global heap collection is not plausible');
        const b = rd(a, size, 'a global heap'), objs = new Map(), m = cur(b, 8 + F.lsz, 'a global heap');
        while(m.p + 8 + F.lsz <= b.length){
          const i = m.u16(); m.u16(); m.take(4);
          const sz = m.length();
          if(i===0) break;
          if(sz===UNDEF || sz > b.length - m.p) fail('a global heap object runs past its collection');
          objs.set(i, m.take(sz));
          m.align(0);
        }
        F.gheaps.set(addr, objs);
      }
      const objs = F.gheaps.get(addr);
      if(!objs.has(idx)) fail('a global heap object is missing');
      return objs.get(idx);
    }
    function link(c){
      if(c.u8()!==1) fail('link message version is not 1');
      const flags = c.u8(), type = flags & 8 ? c.u8() : 0;
      if(flags & 4) c.take(8);
      if(flags & 16) c.u8();
      const n = c.u(1 << (flags & 3)), name = new TextDecoder('utf-8').decode(c.take(n));
      return [name, type===0 ? c.addr() : null];
    }
    function symbolTable(o, btree, heap){
      const hc = cur(rd(at(heap), 8 + 2*F.lsz + F.osz, 'a local heap'), 0, 'a local heap');
      hc.sig('HEAP'); hc.take(4);
      const hsize = hc.length(); hc.length();
      const hdata = hc.addr();
      if(hsize===UNDEF || hsize > 1 << 28) fail('a local heap size is not plausible');
      const names = rd(at(hdata), hsize, 'a local heap'), esz = 2*F.osz + 24;
      for(const child of btree1(btree, 0, 0)){
        const c = cur(rd(at(child), 8, 'a symbol table node'), 0, 'a symbol table node');
        c.sig('SNOD'); c.take(2);
        const n = c.u16();
        const e = cur(rd(at(child) + 8, n*esz, 'a symbol table node'), 0, 'a symbol table node');
        for(let i=0; i<n; i++){
          const noff = e.u(F.osz), oaddr = e.addr();
          e.take(24);
          if(noff >= names.length) fail('a link name lies outside its local heap');
          const end = names.indexOf(0, noff);
          const name = new TextDecoder('utf-8').decode(names.subarray(noff, end >= 0 ? end : names.length));
          if(oaddr!==UNDEF) o.links[name] = oaddr;
        }
      }
    }
    // the leaves of a v1 B-tree: child addresses (groups) or [key bytes, child] (chunks)
    function btree1(addr, type, keylen){
      const out = [], seen = new Set(), stack = [[addr, 0]];
      const klen = type===0 ? F.lsz : keylen;
      while(stack.length){
        const [a, depth] = stack.pop();
        if(seen.has(a) || depth > MAX_DEPTH) fail('B-tree loop or too deep');
        seen.add(a);
        const c = cur(rd(at(a), 8 + 2*F.osz, 'a B-tree node'), 0, 'a B-tree node');
        c.sig('TREE');
        const t = c.u8(), level = c.u8(), n = c.u16();
        if(t!==type) fail('a B-tree node has the wrong type');
        const body = cur(rd(at(a) + 8 + 2*F.osz, n*(klen + F.osz) + klen, 'a B-tree node'), 0, 'a B-tree node');
        const kids = [];
        for(let i=0; i<n; i++){
          const key = body.take(klen), child = body.addr();
          if(level===0) out.push(type===1 ? [key, child] : child);
          else kids.push([child, depth + 1]);
        }
        for(let i=kids.length-1; i>=0; i--) stack.push(kids[i]);     // keep the tree's order
        if(out.length > MAX_CHUNKS) fail('too many B-tree entries');
      }
      return out;
    }
    function fheap(addr){
      const a = at(addr), c = cur(rd(a, Math.min(512, u8.length - a), 'a fractal heap header'), 0, 'a fractal heap header');
      c.sig('FRHP'); c.u8();
      const idlen = c.u16(), filt = c.u16();
      c.u8();
      const maxManaged = c.u32();
      c.length(); c.addr(); c.length(); c.addr(); c.length(); c.length(); c.length();
      for(let i=0; i<5; i++) c.length();
      const width = c.u16(), start = c.length(), maxDirect = c.length(), maxBits = c.u16();
      c.u16();
      const root = c.addr(), rows = c.u16();
      if(filt) fail('filtered fractal heaps are not supported');
      if(!width || start===UNDEF || !start || maxDirect===UNDEF || !maxDirect || maxBits > 64) fail('fractal heap parameters are not plausible');
      const offBytes = Math.ceil(maxBits/8);
      const lenBytes = Math.min(Math.ceil(Math.log2(maxDirect)/8), encSize(maxManaged));
      const rowSize = r=>r < 2 ? start : start*2**(r - 1);
      const directRows = Math.log2(maxDirect/start) + 2;
      const blocks = [];
      const indirect = (baddr, nrows, depth)=>{
        if(depth > MAX_DEPTH) fail('fractal heap too deep');
        const nd = Math.min(nrows, directRows)*width, ni = Math.max(0, nrows - directRows)*width;
        const ic = cur(rd(at(baddr), 5 + F.osz + offBytes + (nd + ni)*F.osz, 'a fractal heap block'), 0, 'a fractal heap block');
        ic.sig('FHIB'); ic.u8(); ic.addr();
        let pos = ic.u(offBytes);
        for(let r=0; r<nrows; r++){
          const s = rowSize(r);
          for(let w=0; w<width; w++){
            const x = ic.addr();
            if(x!==UNDEF){
              if(r < directRows) blocks.push([pos, s, x]);
              else indirect(x, Math.log2(s/(start*width)) + 1, depth + 1);
            }
            pos += s;
            if(blocks.length > 65536) fail('fractal heap has too many blocks');
          }
        }
      };
      if(root!==UNDEF){ if(rows===0) blocks.push([0, start, root]); else indirect(root, rows, 0); }
      const cache = new Map();
      const get = id=>{
        const kind = (id[0] >> 4) & 3;
        if(kind===2) return id.subarray(1, 1 + (id[0] & 15) + 1);
        if(kind!==0) fail('huge fractal heap objects are not supported');
        const hc = cur(id, 1, 'a heap ID'), o = hc.u(offBytes), len = hc.u(lenBytes);
        for(const [boff, size, baddr] of blocks){
          if(o >= boff && o < boff + size){
            if(!cache.has(baddr)){
              const b = rd(at(baddr), size, 'a fractal heap block');
              if(String.fromCharCode(...b.subarray(0, 4))!=='FHDB') fail('fractal heap direct block without FHDB');
              cache.set(baddr, b);
            }
            const b = cache.get(baddr), rel = o - boff;
            if(rel + len > b.length) fail('a fractal heap object runs past its block');
            return b.subarray(rel, rel + len);
          }
        }
        fail('a fractal heap object lies outside every block');
      };
      return {idlen, get};
    }
    function btree2(addr){
      const c = cur(rd(at(addr), 18 + F.osz + 2 + F.lsz + 4, 'a v2 B-tree header'), 0, 'a v2 B-tree header');
      c.sig('BTHD'); c.u8();
      const type = c.u8(), node = c.u32(), rec = c.u16(), depth = c.u16();
      c.u8(); c.u8();
      const root = c.addr(), nroot = c.u16();
      if(!rec || node < 10 || depth > MAX_DEPTH) fail('v2 B-tree parameters are not plausible');
      const maxN = [Math.floor((node - 10)/rec)], cum = [maxN[0]], cumSize = [0], nrecSize = encSize(maxN[0]);
      for(let d=1; d<=depth; d++){
        const ptr = F.osz + nrecSize + (d > 1 ? cumSize[d-1] : 0), m = Math.floor((node - (10 + ptr))/(rec + ptr));
        maxN.push(m); cum.push((m + 1)*cum[d-1] + m); cumSize.push(encSize(cum[d]));
      }
      const out = [], seen = new Set();
      const walk = (a, n, d)=>{
        if(a===UNDEF || !n) return;
        if(seen.has(a) || seen.size > 65536) fail('v2 B-tree loop or too large');
        seen.add(a);
        if(d===0){
          const b = rd(at(a), 6 + n*rec, 'a v2 B-tree leaf');
          if(String.fromCharCode(...b.subarray(0, 4))!=='BTLF') fail('v2 B-tree leaf without BTLF');
          for(let i=0; i<n; i++) out.push(b.subarray(6 + i*rec, 6 + (i+1)*rec));
          return;
        }
        const ptr = F.osz + nrecSize + (d > 1 ? cumSize[d-1] : 0);
        const b = rd(at(a), 6 + n*rec + (n + 1)*ptr, 'a v2 B-tree node');
        if(String.fromCharCode(...b.subarray(0, 4))!=='BTIN') fail('v2 B-tree node without BTIN');
        const pc = cur(b, 6 + n*rec, 'a v2 B-tree node'), kids = [];
        for(let i=0; i<=n; i++){ const ca = pc.addr(), cn = pc.u(nrecSize); if(d > 1) pc.u(cumSize[d-1]); kids.push([ca, cn]); }
        kids.forEach(([ca, cn], i)=>{ walk(ca, cn, d - 1); if(i < n) out.push(b.subarray(6 + i*rec, 6 + (i+1)*rec)); });
      };
      walk(root, nroot, depth);
      return {type, recs:out};
    }
    function denseAttrs(o, c){
      if(c.u8()!==0) fail('attribute info message version is not 0');
      if(c.u8() & 1) c.u16();
      const heap = c.addr(), names = c.addr();
      if(heap===UNDEF || names===UNDEF) return;
      const h = fheap(heap);
      for(const r of btree2(names).recs) attribute(o, cur(h.get(r.subarray(0, h.idlen)), 0, 'a dense attribute'));
    }
    function denseLinks(o, c){
      if(c.u8()!==0) fail('link info message version is not 0');
      if(c.u8() & 1) c.take(8);
      const heap = c.addr(), names = c.addr();
      if(heap===UNDEF || names===UNDEF) return;
      const h = fheap(heap);
      for(const r of btree2(names).recs){
        const [n, t] = link(cur(h.get(r.subarray(4, 4 + h.idlen)), 0, 'a dense link'));
        if(t!==null) o.links[n] = t;
      }
    }

    // ---------------------------------------------------------------- groups -> netCDF
    const datasets = new Map();                 // path -> object
    const walk = (g, path, stack)=>{
      if(stack.has(g.addr) || stack.size > MAX_DEPTH) fail('group loop');
      for(const name of Object.keys(g.links || {}).sort()){
        const o = object(g.links[name]);
        if(o.layout) datasets.set(path + name, o);
        else if(o.links) walk(o, path + name + '/', new Set([...stack, g.addr]));
      }
    };
    const root = object(F.root);
    walk(root, '', new Set());
    return {F, rd, cur, at, datasets, root, attrValue, vlen, btree1, btree2};
  }

  // ---------------------------------------------------------------- data
  async function inflate(src, max){
    if(typeof DecompressionStream!=='function') fail('This browser cannot decompress NetCDF-4 data (no DecompressionStream).');
    const ds = new DecompressionStream('deflate'), w = ds.writable.getWriter(), r = ds.readable.getReader();
    w.write(src).catch(()=>{});
    w.close().catch(()=>{});
    const parts = [];
    let n = 0;
    try {
      for(;;){
        const {done, value} = await r.read();
        if(done) break;
        n += value.length;
        if(n > max){ r.cancel().catch(()=>{}); fail('a compressed chunk inflates past its size'); }
        parts.push(value);
      }
    } catch(err){
      if(/inflates past/.test(err.message)) throw err;
      fail('a compressed chunk is damaged');
    }
    const out = new Uint8Array(n);
    let p = 0;
    for(const s of parts){ out.set(s, p); p += s.length; }
    return out;
  }
  async function decodeChunk(o, raw, mask, nbytes){
    let b = raw;
    for(let k=o.filters.length-1; k>=0; k--){
      if(mask & (1 << k)) continue;
      const f = o.filters[k];
      if(f.id===1) b = await inflate(b, nbytes);
      else if(f.id===2){
        const size = f.vals[0] || o.dtype.size;
        if(size > 1 && b.length % size===0){
          const n = b.length/size, out = new Uint8Array(b.length);
          for(let j=0; j<size; j++) for(let i=0; i<n; i++) out[i*size + j] = b[j*n + i];
          b = out;
        }
      } else if(f.id===3) b = b.subarray(0, Math.max(0, b.length - 4));
      else fail(`HDF5 filter ${f.id} (${f.name || 'unnamed'}) is not supported`);
    }
    if(b.length < nbytes) fail(`a chunk holds ${b.length} bytes, ${nbytes} expected`);
    return b.subarray(0, nbytes);
  }
  // {grid coordinates joined by ',': [offset, stored bytes, filter mask]}
  function chunkIndex(H, o, dims, chunk, esz){
    const {F, rd, cur, at} = H, L = o.layout, rank = dims.length, idx = new Map();
    if(L.addr===UNDEF || L.addr===undefined) return idx;
    const grid = dims.map((d, k)=>Math.ceil(d/chunk[k]));
    const nchunks = grid.reduce((a, b)=>a*b, 1);
    if(nchunks > MAX_CHUNKS) fail('too many chunks');
    const full = chunk.reduce((a, b)=>a*b, 1)*esz, filtered = o.filters.length > 0;
    const maxd = (o.maxd || dims).map((m, k)=>m===null ? dims[k] : m);
    const each = fn=>{
      const cc = new Array(rank).fill(0);
      for(let n=0; n<nchunks; n++){
        fn(cc.slice());
        for(let k=rank-1; k>=0; k--){ if(++cc[k] < grid[k]) break; cc[k] = 0; }
      }
    };
    const entry = (b, esize)=>{
      const c = cur(b, 0, 'a chunk index entry'), a = c.addr();
      if(a===UNDEF) return null;
      return filtered ? [a, c.u(esize - F.osz - 4), c.u32()] : [a, null, 0];
    };
    if(L.index==='btree1'){
      for(const [key, child] of H.btree1(L.addr, 1, 8 + 8*(rank + 1))){
        const c = cur(key, 0, 'a chunk key'), size = c.u32(), mask = c.u32(), cc = [];
        for(let k=0; k<rank; k++){
          const v = c.u(8);
          if(!chunk[k] || v % chunk[k]) fail('a chunk offset is not on the chunk grid');
          cc.push(v/chunk[k]);
        }
        idx.set(cc.join(), [at(child), size, mask]);
      }
    } else if(L.index==='single'){
      idx.set(new Array(rank).fill(0).join(), [at(L.addr), L.singleSize ?? full, L.singleMask ?? 0]);
    } else if(L.index==='implicit'){
      let i = 0;
      each(cc=>{ idx.set(cc.join(), [at(L.addr) + i*full, full, 0]); i++; });
    } else if(L.index==='farray' || L.index==='earray'){
      const get = L.index==='farray' ? farray(H, L.addr, entry) : earray(H, L.addr, entry);
      const unlim = L.index==='earray' ? (o.maxd || []).map((m, k)=>m===null ? k : -1).filter(k=>k >= 0).slice(0, 1) : [];
      const order = unlim.concat([...Array(rank).keys()].filter(k=>!unlim.includes(k)));
      const mcount = maxd.map((m, k)=>Math.ceil(m/chunk[k])), down = new Array(rank).fill(1);
      for(let j=rank-2; j>=0; j--) down[order[j]] = down[order[j+1]]*mcount[order[j+1]];
      each(cc=>{
        const e = get(cc.reduce((s, v, k)=>s + v*down[k], 0));
        if(e) idx.set(cc.join(), [at(e[0]), e[1] ?? full, e[2]]);
      });
    } else if(L.index==='btree2'){
      for(const r of H.btree2(L.addr).recs){
        const c = cur(r, 0, 'a chunk record'), a = c.addr();
        let size = full, mask = 0;
        if(filtered){ size = c.u(r.length - F.osz - 4 - 8*rank); mask = c.u32(); }
        const cc = [];
        for(let k=0; k<rank; k++) cc.push(c.u(8));
        idx.set(cc.join(), [at(a), size, mask]);
      }
    }
    if(idx.size > MAX_CHUNKS) fail('too many chunks');
    return idx;
  }
  function farray(H, addr, entry){
    const {F, rd, cur, at} = H;
    const c = cur(rd(at(addr), 8 + F.lsz + F.osz + 4, 'a fixed array header'), 0, 'a fixed array header');
    c.sig('FAHD'); c.u8(); c.u8();
    const esz = c.u8(), pageBits = c.u8(), n = c.length(), dblk = c.addr();
    if(n===UNDEF || n > MAX_CHUNKS || !esz) fail('fixed array parameters are not plausible');
    if(dblk===UNDEF) return ()=>null;
    const page = 2**pageBits, prefix = 6 + F.osz;
    if(n > page){
      const npages = Math.ceil(n/page), bitmap = rd(at(dblk) + prefix, Math.ceil(npages/8), 'a fixed array page bitmap');
      const first = at(dblk) + prefix + bitmap.length + 4;
      return i=>{
        if(i >= n) return null;
        const p = Math.floor(i/page), k = i % page;
        if(!(bitmap[p >> 3] & (0x80 >> (p & 7)))) return null;
        return entry(rd(first + p*(page*esz + 4) + k*esz, esz, 'a fixed array page'), esz);
      };
    }
    const body = rd(at(dblk) + prefix, n*esz, 'a fixed array data block');
    return i=>i < n ? entry(body.subarray(i*esz, (i+1)*esz), esz) : null;
  }
  function earray(H, addr, entry){
    const {F, rd, cur, at} = H;
    const c = cur(rd(at(addr), 12 + 6*F.lsz + F.osz + 4, 'an extensible array header'), 0, 'an extensible array header');
    c.sig('EAHD'); c.u8(); c.u8();
    const esz = c.u8(), maxBits = c.u8(), idxN = c.u8(), minEl = c.u8(), minPtrs = c.u8(), pageBits = c.u8();
    for(let i=0; i<6; i++) c.length();
    const iblk = c.addr();
    if(!esz || maxBits > 64 || !minEl || !minPtrs) fail('extensible array parameters are not plausible');
    if(iblk===UNDEF) return ()=>null;
    const nsblks = 1 + maxBits - Math.log2(minEl), sinfo = [];
    let start = 0, sd = 0;
    for(let u=0; u<nsblks; u++){
      const nd = 2**Math.floor(u/2), ne = 2**Math.floor((u + 1)/2)*minEl;
      sinfo.push([nd, ne, start, sd]); start += nd*ne; sd += nd;
    }
    const ibS = 2*Math.log2(minPtrs), nD = 2*(minPtrs - 1), nS = Math.max(0, nsblks - ibS), arrOff = Math.ceil(maxBits/8);
    const ic = cur(rd(at(iblk), 6 + F.osz + idxN*esz + (nD + nS)*F.osz, 'an extensible array index block'), 0,
                   'an extensible array index block');
    ic.sig('EAIB'); ic.u8(); ic.u8(); ic.addr();
    const direct = []; for(let i=0; i<idxN; i++) direct.push(ic.take(esz));
    const dblks = []; for(let i=0; i<nD; i++) dblks.push(ic.addr());
    const sblks = []; for(let i=0; i<nS; i++) sblks.push(ic.addr());
    const page = 2**pageBits, cache = new Map();
    const dblock = (a, ne)=>{
      if(a===UNDEF) return null;
      if(ne > page) fail('paged extensible array data blocks are not supported');
      if(!cache.has(a)){
        const b = rd(at(a), 6 + F.osz + arrOff + ne*esz, 'an extensible array data block');
        if(String.fromCharCode(...b.subarray(0, 4))!=='EADB') fail('extensible array data block without EADB');
        cache.set(a, b.subarray(6 + F.osz + arrOff));
      }
      return cache.get(a);
    };
    const superAddrs = (a, nd)=>{
      if(a===UNDEF) return null;
      const key = 's' + a;
      if(!cache.has(key)){
        const b = rd(at(a), 6 + F.osz + arrOff + nd*F.osz, 'an extensible array super block');
        if(String.fromCharCode(...b.subarray(0, 4))!=='EASB') fail('extensible array super block without EASB');
        const sc = cur(b, 6 + F.osz + arrOff, 'an extensible array super block'), list = [];
        for(let i=0; i<nd; i++) list.push(sc.addr());
        cache.set(key, list);
      }
      return cache.get(key);
    };
    return i=>{
      if(i < idxN) return entry(direct[i], esz);
      const j = i - idxN;
      for(let u=0; u<sinfo.length; u++){
        const [nd, ne, s0, d0] = sinfo[u];
        if(j >= s0 && j < s0 + nd*ne){
          const d = Math.floor((j - s0)/ne), k = (j - s0) % ne;
          let a = UNDEF;
          if(u < ibS) a = d0 + d < dblks.length ? dblks[d0 + d] : UNDEF;
          else { const sa = u - ibS < sblks.length ? superAddrs(sblks[u - ibS], nd) : null; a = sa ? sa[d] : UNDEF; }
          const b = dblock(a, ne);
          return b ? entry(b.subarray(k*esz, (k+1)*esz), esz) : null;
        }
      }
      return null;
    };
  }
  // a variable's elements, flattened row-major: bytes (esz each), fill where nothing is stored
  async function elements(H, o, budget){
    const {rd, at} = H, t = o.dtype, dims = o.dims, esz = t.cls===9 ? 4 + H.F.osz + 4 : t.size;
    const total = dims.reduce((a, b)=>a*b, 1);
    if(total > MAX_VALUES) fail('a variable is too large for the browser');
    budget.values -= total;
    if(budget.values < 0) fail('the file decodes to more values than the browser allows for its size');
    // each stored byte belongs to one variable in a real file: variables that share bytes would multiply a small file
    const claim = n=>{ budget.stored -= n; if(budget.stored < 0) fail('NetCDF file whose variables claim more data than the file holds (corrupt, or sharing the same bytes)'); };
    const out = new Uint8Array(total*esz);
    const fill = o.fill && o.fill.length===esz ? o.fill : null;
    if(fill) for(let i=0; i<total; i++) out.set(fill, i*esz);
    const L = o.layout;
    if(L.cls===0){
      if(L.data.length < out.length) fail('compact data is cut short');
      out.set(L.data.subarray(0, out.length));
    } else if(L.cls===1){
      if(L.addr!==UNDEF && total){ claim(out.length); out.set(rd(at(L.addr), out.length, 'contiguous data')); }
    } else {
      let chunk = L.chunk.slice();
      if(chunk.length===dims.length + 1) chunk = chunk.slice(0, dims.length);
      if(chunk.length!==dims.length || chunk.some(c=>!(c > 0))) fail('chunk dimensions do not match the variable');
      const nbytes = chunk.reduce((a, b)=>a*b, 1)*esz;
      if(nbytes > 1 << 28) fail('chunks too large for the browser');
      const idx = chunkIndex(H, o, dims, chunk, esz), rank = dims.length;
      const strides = new Array(rank).fill(1), cstr = new Array(rank).fill(1);
      for(let k=rank-2; k>=0; k--){ strides[k] = strides[k+1]*dims[k+1]; cstr[k] = cstr[k+1]*chunk[k+1]; }
      for(const [key, [a, size, mask]] of idx){
        claim(size);
        const cc = key ? key.split(',').map(Number) : [];
        const data = await decodeChunk(o, rd(a, size, 'a chunk'), mask, nbytes);
        const c0 = cc.map((v, k)=>v*chunk[k]);
        if(c0.some((v, k)=>v >= dims[k])) continue;
        const ext = c0.map((v, k)=>Math.min(chunk[k], dims[k] - v));
        if(!rank){ out.set(data.subarray(0, esz)); continue; }
        // copy row by row along the last dimension
        const rows = ext.slice(0, -1).reduce((x, y)=>x*y, 1), last = ext[rank-1]*esz, pos = new Array(rank - 1).fill(0);
        for(let r=0; r<rows; r++){
          let src = 0, dst = 0;
          for(let k=0; k<rank-1; k++){ src += pos[k]*cstr[k]; dst += (c0[k] + pos[k])*strides[k]; }
          dst += c0[rank-1];
          out.set(data.subarray(src*esz, src*esz + last), dst*esz);
          for(let k=rank-2; k>=0; k--){ if(++pos[k] < ext[k]) break; pos[k] = 0; }
        }
      }
    }
    return out;
  }

  // ---------------------------------------------------------------- the NetCDF3-shaped result
  async function parse(buffer){
    try {
      return await build(open(buffer), buffer.byteLength);
    } catch(err){
      if(/^This browser|^Not an HDF5/.test(err.message)) throw err;
      throw new Error('This NetCDF-4 file could not be read: ' + err.message.replace(/\.$/, '') + '.');
    }
  }
  async function build(H, fileSize){
    const {datasets, root, attrValue} = H;
    const attrsOf = o=>{
      const out = {};
      for(const [k, a] of Object.entries(o.attrs)){
        if(HIDDEN.has(k)) continue;
        const v = attrValue(a);
        if(v!==undefined) out[k] = v;
      }
      return out;
    };
    const byAddr = new Map([...datasets].map(([p, o])=>[o.addr, p]));
    const scales = [...datasets].filter(([, o])=>o.attrs.CLASS && attrValue(o.attrs.CLASS)==='DIMENSION_SCALE');
    const dimId = o=>o.attrs._Netcdf4Dimid ? attrValue(o.attrs._Netcdf4Dimid) : Infinity;
    scales.sort((a, b)=>dimId(a[1]) - dimId(b[1]) || (a[0] < b[0] ? -1 : 1));
    const dims = [], dimSize = {};
    for(const [p, o] of scales){
      const name = p.replace(NON_COORD, ''), size = o.dims && o.dims.length ? o.dims[0] : 0;
      dims.push({name, size, unlimited:!!(o.maxd && o.maxd[0]===null)});
      dimSize[name] = size;
    }
    // decoded values are capped by the file's size (deflated data rarely grows more than tenfold), and stored bytes by
    // the file itself
    const vars = {}, phony = new Map(), budget = {values:Math.min(MAX_VALUES, Math.max(1e6, 16*fileSize)), stored:fileSize};
    for(const [p, o] of [...datasets].sort((a, b)=>a[0] < b[0] ? -1 : 1)){
      const name = p.replace(NON_COORD, '');
      const isScale = scales.some(([q])=>q===p);
      if(isScale && o.attrs.NAME && String(attrValue(o.attrs.NAME)).startsWith(NOT_A_VARIABLE)) continue;
      const type = o.dtype ? typeName(o.dtype) : null;
      if(!type || !o.dims) continue;                // a type the app has no use for (compound, opaque, ...)
      let vd = [];
      if(isScale && o.dims.length===1 && !p.includes(NON_COORD)) vd = [name];
      else if(o.attrs.DIMENSION_LIST){
        const refs = attrValue(o.attrs.DIMENSION_LIST) || [];
        vd = refs.map((r, k)=>{
          const target = byAddr.get(r[0]);
          if(target===undefined) fail(`${name}: dimension ${k} refers to an unknown dimension scale`);
          return target.replace(NON_COORD, '');
        });
      }
      if(vd.length!==o.dims.length){
        vd = o.dims.map((size, k)=>{
          const key = k + ':' + size;
          if(!phony.has(key)){ const d = `phony_dim_${phony.size}`; phony.set(key, d); dims.push({name:d, size, unlimited:false}); }
          return phony.get(key);
        });
      }
      const bytes = await elements(H, o, budget);
      const t = o.dtype, n = o.dims.reduce((a, b)=>a*b, 1);
      let raw;
      if(type==='char') raw = bytes;
      else if(type==='string'){
        if(t.cls===9) raw = H.vlen(t, bytes, n);
        else { const dec = new TextDecoder('utf-8'); raw = []; for(let i=0; i<n; i++) raw.push(dec.decode(bytes.subarray(i*t.size, (i+1)*t.size)).replace(/[\0 ]+$/, '')); }
      } else {
        raw = new Float64Array(n);
        const dv = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
        for(let i=0; i<n; i++) raw[i] = readNum(dv, t, i*t.size);
      }
      vars[name] = {name, dims:vd, shape:o.dims.slice(), type, size:t.cls===9 ? 16 : t.size, attrs:attrsOf(o), record:false,
                    _raw:raw};
    }
    const unl = dims.find(d=>d.unlimited);
    const attrs = attrsOf(root);
    const get = k=>{ const v = vars[k]; if(!v) throw new Error(`No variable ${k} in this file.`); return v; };
    const raw = k=>get(k)._raw.slice();
    const read = k=>{
      const v = get(k), a = raw(k);
      if(v.type==='char') return NetCDF3.chars(v, a);
      if(v.type==='string') return a;
      return NetCDF3.unpack(v, a, k);
    };
    const attr = (k, nm)=>k ? get(k).attrs[nm] : attrs[nm];
    return {version:4, numrecs:unl ? unl.size : 0, dims, attrs, vars, read, raw, attr};
  }
  return {parse};
})();
