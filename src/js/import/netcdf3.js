/* ============================================================
   NETCDF CLASSIC READER (CDF-1, CDF-2 64-bit offset, CDF-5 64-bit data)
   NetCDF3.parse(arrayBuffer) -> {version, numrecs, dims, attrs, vars, read(name), raw(name), attr(var, name)}
   read() unpacks: _FillValue, missing_value, valid_min/max/range and the default fill become NaN,
   then scale_factor and add_offset apply (no valid range on times and coordinates). Char variables come back
   as strings.
   ============================================================ */
const NetCDF3 = (()=>{
  const TYPES = {1:['byte', 1], 2:['char', 1], 3:['short', 2], 4:['int', 4], 5:['float', 4], 6:['double', 8],
                 7:['ubyte', 1], 8:['ushort', 2], 9:['uint', 4], 10:['int64', 8], 11:['uint64', 8]};
  const DEFAULT_FILL = {byte:-127, short:-32767, int:-2147483647, float:9.969209968386869e36, double:9.969209968386869e36,
                        ubyte:255, ushort:65535, uint:4294967295};
  const HDF5 = [0x89, 0x48, 0x44, 0x46, 0x0d, 0x0a, 0x1a, 0x0a];

  // read the header and check every variable lies inside the file; data is read later, on request
  function parse(buffer){
    const u8 = new Uint8Array(buffer), dv = new DataView(buffer);
    if(HDF5.every((b, i)=>u8[i]===b)) throw new Error('NetCDF-4 file: convert it to NetCDF-3 first (nccopy -k classic) or export it as CSV.');
    if(!(u8[0]===0x43 && u8[1]===0x44 && u8[2]===0x46) || ![1, 2, 5].includes(u8[3])) throw new Error('Not a NetCDF classic file.');
    const version = u8[3];
    let pos = 4;
    try {
      const u32 = ()=>{ const v = dv.getUint32(pos); pos += 4; return v; };
      const u64 = ()=>{ const v = Number(dv.getBigUint64(pos)); pos += 8; return v; };
      const count = ()=>{ const n = version===5 ? u64() : u32(); if(n > u8.length) throw new Error('a count in the header is larger than the file'); return n; };
      const offset = version===1 ? u32 : u64;                  // begin
      const pad = n=>{ pos += (4 - n%4)%4; };
      const name = ()=>{ const n = count(); const s = new TextDecoder('utf-8').decode(u8.subarray(pos, pos+n)); pos += n; pad(n); return s; };
      const typeOf = code=>{ if(!TYPES[code]) throw new Error(`unknown data type ${code}`); return TYPES[code]; };
      const values = (code, n)=>{
        const [t, size] = typeOf(code), start = pos;
        pos += n*size; pad(n*size);
        if(t==='char') return new TextDecoder('utf-8').decode(u8.subarray(start, start+n)).replace(/\0+$/, '');
        const out = new Array(n);
        for(let i=0; i<n; i++) out[i] = readOne(dv, t, start + i*size);
        return n===1 ? out[0] : out;
      };
      const attList = ()=>{
        const tag = u32(), n = count(), out = {};
        if(tag===0 && n===0) return out;
        if(tag!==0x0C) throw new Error('attribute list expected');
        for(let i=0; i<n; i++){ const k = name(), code = u32(); out[k] = values(code, count()); }
        return out;
      };
      let numrecs = version===5 ? u64() : u32();
      if(numrecs > u8.length && numrecs < 0xFFFFFFFF) throw new Error('the record count is larger than the file');
      // dimensions
      const dims = [];
      let tag = u32(), n = count();
      if(tag===0x0A) for(let i=0; i<n; i++){ const k = name(), size = count(); dims.push({name:k, size, unlimited:size===0}); }
      else if(tag!==0 || n!==0) throw new Error('dimension list expected');
      const attrs = attList();
      // variables
      const vars = {};
      tag = u32(); n = count();
      if(tag!==0x0B && !(tag===0 && n===0)) throw new Error('variable list expected');
      for(let i=0; tag===0x0B && i<n; i++){
        const k = name(), nd = count(), ids = [];
        for(let d=0; d<nd; d++) ids.push(count());
        const vatts = attList(), code = u32(), vsize = count(), begin = offset();
        const [type, size] = typeOf(code);
        const vd = ids.map(id=>dims[id]);
        if(vd.some(d=>!d)) throw new Error(`variable ${k} uses a missing dimension`);
        const record = vd.length > 0 && vd[0].unlimited;
        vars[k] = {name:k, dims:vd.map(d=>d.name), shape:vd.map(d=>d.unlimited ? 0 : d.size), type, size, attrs:vatts,
                   record, vsize, begin};
      }
      const recVars = Object.values(vars).filter(v=>v.record);
      // the record size: padded vsizes, except a lone record variable, which is not padded
      const perRec = v=>v.shape.slice(1).reduce((a, b)=>a*b, 1)*v.size;
      const recsize = recVars.length===1 ? perRec(recVars[0]) : recVars.reduce((a, v)=>a + v.vsize, 0);
      const recStart = recVars.length ? Math.min(...recVars.map(v=>v.begin)) : 0;
      if(numrecs >= 0xFFFFFFFF && recVars.length){       // streaming: count the records that fit
        numrecs = recsize ? Math.floor((u8.length - recStart)/recsize) : 0;
      }
      recVars.forEach(v=>{ v.shape[0] = numrecs; });
      // every variable's data must lie inside the file; with no records (Argo _prof.nc files have N_HISTORY = 0)
      // the record variables hold nothing, so only the start of the record section is checked
      Object.values(vars).forEach(v=>{
        const per = v.shape.slice(v.record ? 1 : 0).reduce((a, b)=>a*b, 1)*v.size;
        const end = v.record ? (numrecs ? v.begin + (numrecs-1)*recsize + per : recStart) : v.begin + per;
        if(!(v.begin >= pos) || end > u8.length) throw new Error(`variable ${v.name} points past the end of the file (truncated?)`);
        if(per/v.size*(v.record ? Math.max(1, numrecs) : 1) > 5e7) throw new Error(`variable ${v.name} is too large for the browser`);
      });
      return makeFile(u8, dv, version, numrecs, dims, attrs, vars, recsize);
    } catch(err){
      if(err instanceof RangeError) throw new Error('The NetCDF file ends early (truncated or not NetCDF).');
      if(/^NetCDF|^Not a/.test(err.message)) throw err;
      throw new Error('This NetCDF file could not be read: ' + err.message + '.');
    }
  }
  // one value of type t at byte offset at (NetCDF is big-endian, as DataView reads by default)
  function readOne(dv, t, at){
    switch(t){
      case 'byte': return dv.getInt8(at);
      case 'ubyte': return dv.getUint8(at);
      case 'short': return dv.getInt16(at);
      case 'ushort': return dv.getUint16(at);
      case 'int': return dv.getInt32(at);
      case 'uint': return dv.getUint32(at);
      case 'float': return dv.getFloat32(at);
      case 'double': return dv.getFloat64(at);
      case 'int64': return Number(dv.getBigInt64(at));
      case 'uint64': return Number(dv.getBigUint64(at));
    }
    return NaN;
  }
  // the object parse() returns: readers over the file bytes
  function makeFile(u8, dv, version, numrecs, dims, attrs, vars, recsize){
    const get = k=>{ const v = vars[k]; if(!v) throw new Error(`No variable ${k} in this file.`); return v; };
    // the raw values, flattened row-major
    function raw(k){
      const v = get(k), n = v.shape.reduce((a, b)=>a*b, 1), out = v.type==='char' ? new Uint8Array(n) : new Float64Array(n);
      const put = (dst, src)=>{ out[dst] = v.type==='char' ? u8[src] : readOne(dv, v.type, src); };
      if(!v.record){
        for(let i=0; i<n; i++) put(i, v.begin + i*v.size);
      } else {
        const per = v.shape.slice(1).reduce((a, b)=>a*b, 1);
        for(let r=0; r<numrecs; r++) for(let i=0; i<per; i++) put(r*per + i, v.begin + r*recsize + i*v.size);
      }
      return out;
    }
    function read(k){
      const v = get(k), a = raw(k);
      if(v.type==='char'){
        const len = v.shape.length ? v.shape[v.shape.length-1] : a.length, out = [];
        const dec = new TextDecoder('utf-8');
        for(let i=0; i<a.length; i+=len) out.push(dec.decode(a.subarray(i, i+len)).replace(/\0+$/, '').trim());
        return out;
      }
      const at = v.attrs, num = x=>Array.isArray(x) ? x.map(Number) : x===undefined ? [] : [Number(x)];
      const fills = num(at._FillValue).concat(num(at.missing_value));
      if(!('_FillValue' in at) && DEFAULT_FILL[v.type]!==undefined) fills.push(DEFAULT_FILL[v.type]);
      const isFloat = v.type==='float' || v.type==='double';
      const fill = x=>fills.some(f=>isFloat ? Math.abs(x - f) <= Math.abs(f)*1e-6 : x===f) || (isFloat && Math.abs(x) >= 9.96e36) || Number.isNaN(x);
      // no valid range on times and coordinates, nor one that rejects every value: both are wrong metadata
      // (ERDDAP glider tables give time, in seconds since 1970, valid_max 90000)
      const coord = /\bsince\b/i.test(String(at.units || '')) || /^[TXY]$/i.test(String(at.axis || '')) || (v.dims.length===1 && v.dims[0]===k);
      const vr = num(at.valid_range);
      let lo = coord ? undefined : vr.length===2 ? vr[0] : num(at.valid_min)[0], hi = coord ? undefined : vr.length===2 ? vr[1] : num(at.valid_max)[0];
      const out = x=>(lo!==undefined && x < lo) || (hi!==undefined && x > hi);
      if((lo!==undefined || hi!==undefined) && a.some(x=>!fill(x)) && a.every(x=>fill(x) || out(x))) lo = hi = undefined;
      const sc = at.scale_factor!==undefined ? Number(at.scale_factor) : 1, off = at.add_offset!==undefined ? Number(at.add_offset) : 0;
      for(let i=0; i<a.length; i++){
        const x = a[i];
        if(fill(x) || out(x)) a[i] = NaN;
        else a[i] = x*sc + off;
      }
      return a;
    }
    const attr = (k, name)=>k ? (get(k).attrs[name]) : attrs[name];
    return {version, numrecs, dims, attrs, vars, read, raw, attr};
  }
  return {parse};
})();
