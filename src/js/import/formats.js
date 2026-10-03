/* NetCDF format mappings for "Add data" (science): Argo profile files (core, BGC and synthetic), Ifremer EGO
   glider files, ERDDAP tabledap .nc tables and generic CF profiles. Each reader applies QC and units and
   returns a dataset (see plugins.js): °C, PSU, mg/m³, µmol/kg, depth in m.
   QC as in the snapshot: Argo flags 1, 2, 5, 8 kept; adjusted values wherever a file has them (raw values
   only for real-time data); chlorophyll and oxygen from adjusted values only when adjusted columns exist. */
(function(){
  if(typeof OceanLens==='undefined' || !OceanLens.registerFormat) return;
  const GOOD = new Set(['1', '2', '5', '8']);
  const RANGE = {temp:[-2.5, 40], sal:[2, 41], chl:[-0.1, 50], oxy:[-5, 600]};
  const VARS = ['temp', 'sal', 'chl', 'oxy'];
  const NOTE = 'QC: flags 1, 2, 5, 8 kept; adjusted values where the file has them, otherwise the sensor values '
    + '(chlorophyll and oxygen then carry a warning); range checks (°C −2.5–40, PSU 2–41, mg/m³ −0.1–50, µmol/kg −5–600)';
  const LABEL = {chl:'Chlorophyll', oxy:'Oxygen'};
  // warnings for chlorophyll or oxygen read from unadjusted sensor values (vals: key -> per-sample arrays used)
  function rawWarnings(used){
    return Object.keys(LABEL).filter(k=>used[k]).map(k=>`${LABEL[k]} values are unadjusted sensor values (the file has `
      + 'no adjusted column); treat them as approximate.');
  }
  const anyFinite = a=>!!a && Array.prototype.some.call(a, Number.isFinite);
  const left = (n, one, many, why)=>`${n} ${n===1 ? one+' was' : many+' were'} left out: ${why}.`;
  // CF standard names, then common variable names (lower case), per quantity
  const CF = {
    temp:[['sea_water_temperature', 'sea_water_potential_temperature', 'sea_water_conservative_temperature'],
      ['temp', 'temperature', 'thetao', 'water_temp', 'te']],
    sal:[['sea_water_practical_salinity', 'sea_water_salinity', 'sea_water_absolute_salinity'],
      ['psal', 'sal', 'salinity', 'so', 'salt']],
    chl:[['mass_concentration_of_chlorophyll_a_in_sea_water', 'mass_concentration_of_chlorophyll_in_sea_water'],
      ['chla', 'chl', 'chlorophyll', 'cphl', 'fluo_chla']],
    oxy:[['moles_of_oxygen_per_unit_mass_in_sea_water', 'mole_concentration_of_dissolved_molecular_oxygen_in_sea_water'],
      ['doxy', 'oxygen', 'molar_doxy', 'o2', 'dox2']],
    pres:[['sea_water_pressure', 'sea_water_pressure_due_to_sea_water'], ['pres', 'pressure', 'prs']],
    depth:[['depth', 'depth_below_sea_surface'], ['depth', 'deph', 'z']],
    lat:[['latitude'], ['latitude', 'lat']], lon:[['longitude'], ['longitude', 'lon']],
    time:[['time'], ['time', 'juld', 'date_time']],
  };

  const has = (nc, n)=>!!(n && nc.vars[n]);
  const num = (nc, n)=>has(nc, n) ? nc.read(n) : null;
  const lowerMap = nc=>{ const m = {}; Object.keys(nc.vars).forEach(k=>{ m[k.toLowerCase()] = k; }); return m; };
  function find(nc, key, extraNames){
    const [sns, names] = CF[key];
    for(const n of Object.keys(nc.vars)) if(sns.includes(String(nc.vars[n].attrs.standard_name || ''))
      && !/_qc$|_error$/i.test(n)) return n;
    const low = lowerMap(nc);
    for(const n of (extraNames || []).concat(names)) if(low[n]) return low[n];
    return null;
  }
  // len flags, one character each, from char variables (any shape; the reader collapses the last
  // dimension into strings) or numeric flag variables
  function flags(nc, n, len){
    if(!has(nc, n)) return null;
    const r = nc.read(n);
    if(r.length && typeof r[0]==='string'){
      const w = len / r.length;
      if(!Number.isInteger(w) || w < 1) return null;
      return Array.from(r, x=>x.padEnd(w, ' ').slice(0, w)).join('').split('');
    }
    return r.length===len ? Array.from(r, v=>Number.isFinite(v) ? String(Math.round(v)) : ' ') : null;
  }
  const strs = (nc, n)=>has(nc, n) ? Array.from(nc.read(n), s=>typeof s==='string' ? s.trim() : String(s)) : null;
  function timeMs(nc, n){
    const v = num(nc, n); if(!v) return null;
    const u = String(nc.vars[n].attrs.units || '');
    const m = u.match(/^\s*(\w+?)s?\s+since\s+(\d{1,4})-(\d{1,2})-(\d{1,2})(?:[ T](\d{1,2}):(\d{1,2})(?::(\d{1,2}(?:\.\d*)?))?)?/i);
    const unit = m && {second:1e3, sec:1e3, s:1e3, minute:6e4, min:6e4, hour:36e5, hr:36e5, h:36e5, day:864e5, d:864e5}[m[1].toLowerCase()];
    if(!unit) throw new Error(`Can't read the times: ${n} has units "${u}". Expected something like "days since 1950-01-01".`);
    const base = Date.UTC(+m[2], +m[3]-1, +m[4], +(m[5]||0), +(m[6]||0), 0) + 1000*(+(m[7]||0));
    return Array.from(v, x=>Number.isFinite(x) ? base + x*unit : NaN);
  }
  const iso = ms=>new Date(ms).toISOString().slice(0, 16)+'Z';
  const inRange = (key, v)=>Number.isFinite(v) && (!key || (v >= RANGE[key][0] && v <= RANGE[key][1]));

  // one profile from per-sample arrays: z (m), vars; sorted by depth, repeated depths dropped
  function profile(base, z, vals){
    const idx = [];
    for(let k=0;k<z.length;k++) if(Number.isFinite(z[k]) && VARS.some(v=>vals[v] && Number.isFinite(vals[v][k]))) idx.push(k);
    idx.sort((a,b)=>z[a]-z[b]);
    const keep = idx.filter((k,i)=>i===0 || z[k] - z[idx[i-1]] > 0.05);
    if(keep.length < 2) return null;
    const vars = {};
    VARS.forEach(v=>{ if(vals[v] && keep.some(k=>Number.isFinite(vals[v][k]))) vars[v] = keep.map(k=>vals[v][k]); });
    return Object.assign(base, {z:keep.map(k=>+z[k].toFixed(2)), vars});
  }
  // the value at one sample under the QC rules: adjusted first; raw only when there is no adjusted value
  // (key null: no range test, for pressure and depth)
  function pick(key, raw, rawQc, adj, adjQc, k, adjustedOnly){
    if(adj && Number.isFinite(adj[k])) return (!adjQc || GOOD.has(adjQc[k])) && inRange(key, adj[k]) ? adj[k] : NaN;
    if(adjustedOnly || !raw || !Number.isFinite(raw[k])) return NaN;
    return (!rawQc || GOOD.has(rawQc[k])) && inRange(key, raw[k]) ? raw[k] : NaN;
  }

  // ---- Argo profile files (R/D core, B and S files): N_PROF x N_LEVELS, upper-case names
  const ARGO_NAMES = {temp:'TEMP', sal:'PSAL', chl:'CHLA', oxy:'DOXY'};
  OceanLens.registerFormat('argo_profile', {label:'Argo profile NetCDF', kind:'netcdf',
    detect:nc=>has(nc, 'PLATFORM_NUMBER') && nc.dims.some(d=>d.name==='N_LEVELS') ? 0.95 : 0,
    read(nc){
      const nP = nc.dims.find(d=>d.name==='N_PROF').size, nL = nc.dims.find(d=>d.name==='N_LEVELS').size;
      const wmo = strs(nc, 'PLATFORM_NUMBER'), cyc = num(nc, 'CYCLE_NUMBER'), t = timeMs(nc, 'JULD');
      const lat = num(nc, 'LATITUDE'), lon = num(nc, 'LONGITUDE');
      const posQc = flags(nc, 'POSITION_QC', nP), tQc = flags(nc, 'JULD_QC', nP), mode = flags(nc, 'DATA_MODE', nP);
      const dir = flags(nc, 'DIRECTION', nP);
      const A = {}, Q = {};
      ['PRES', ...Object.values(ARGO_NAMES)].forEach(n=>{
        A[n] = num(nc, n); A[n+'_A'] = num(nc, n+'_ADJUSTED');
        Q[n] = flags(nc, n+'_QC', nP*nL); Q[n+'_A'] = flags(nc, n+'_ADJUSTED_QC', nP*nL);
      });
      const profiles = [], warnings = [], raw = {}; let dropped = 0;
      for(let p=0;p<nP;p++){
        if((posQc && !GOOD.has(posQc[p])) || (tQc && !GOOD.has(tQc[p])) || !Number.isFinite(t[p])
          || !Number.isFinite(lat[p]) || !Number.isFinite(lon[p])){ dropped++; continue; }
        const cut = a=>a ? a.slice(p*nL, (p+1)*nL) : null;
        const m = mode ? mode[p] : 'R', delayed = m==='A' || m==='D';
        const col = (key, n, adjOnly)=>{
          const r = cut(A[n]), rq = cut(Q[n]), a = cut(A[n+'_A']), aq = cut(Q[n+'_A']);
          return Array.from({length:nL}, (_,k)=>pick(key, r, rq, a, aq, k, adjOnly && !!a));
        };
        const pres = col(null, 'PRES', delayed), vals = {};
        for(const [key, n] of Object.entries(ARGO_NAMES)){
          if(A[n] || A[n+'_A']) vals[key] = col(key, n, key==='chl' || key==='oxy' || delayed);
          if(LABEL[key] && !A[n+'_A'] && anyFinite(vals[key])) raw[key] = true;
        }
        const z = pres.map(x=>Number.isFinite(x) ? depthFromPressure(x, lat[p]) : NaN);
        const pr = profile({id:'ARGO-'+wmo[p], lat:lat[p], lon:lon[p], time:iso(t[p]),
          meta:{platform:wmo[p], cycle:cyc ? cyc[p] : null, mode:m, direction:dir ? dir[p] : 'A'}}, z, vals);
        if(pr) profiles.push(pr); else dropped++;
      }
      if(dropped) warnings.push(left(dropped, 'profile', 'profiles', 'no good position, time or samples'));
      warnings.push(...rawWarnings(raw));
      return {type:'argo', format:'argo_profile', profiles, warnings, notes:[NOTE]};
    }});

  // ---- Ifremer EGO glider files: one time series, split into dives and climbs
  OceanLens.registerFormat('ego_glider', {label:'EGO glider NetCDF (Ifremer)', kind:'netcdf',
    detect:nc=>/ego|glider/i.test(String(nc.attrs.data_type || '') + ' ' + String(nc.attrs.format_version ? 'ego' : ''))
      && has(nc, 'TIME') ? 0.95 : (has(nc, 'PHASE') && has(nc, 'TIME') && has(nc, 'PRES') ? 0.7 : 0),
    read(nc){
      const t = timeMs(nc, 'TIME'), n = t.length, lat = num(nc, 'LATITUDE'), lon = num(nc, 'LONGITUDE');
      const v = (name, key)=>pick.bind(null, key, num(nc, name), flags(nc, name+'_QC', n),
        num(nc, name+'_ADJUSTED'), flags(nc, name+'_ADJUSTED_QC', n));
      const P = Array.from({length:n}, (_,k)=>v('PRES', null)(k, false));
      const T = Array.from({length:n}, (_,k)=>v('TEMP', 'temp')(k, false));
      const S = Array.from({length:n}, (_,k)=>v('PSAL', 'sal')(k, false));
      const C = has(nc, 'CHLA') ? Array.from({length:n}, (_,k)=>v('CHLA', 'chl')(k, has(nc, 'CHLA_ADJUSTED'))) : null;
      let O = null;
      if(has(nc, 'DOXY')) O = Array.from({length:n}, (_,k)=>v('DOXY', 'oxy')(k, has(nc, 'DOXY_ADJUSTED')));
      else if(has(nc, 'MOLAR_DOXY')){          // µmol/L -> µmol/kg with the sample's density
        const M = Array.from({length:n}, (_,k)=>v('MOLAR_DOXY', 'oxy')(k, false));
        O = M.map((x,k)=>Number.isFinite(x) && Number.isFinite(T[k]) && Number.isFinite(S[k]) ? x/(1+sigmaT(S[k], T[k])/1000) : NaN);
      }
      const raw = {chl:!has(nc, 'CHLA_ADJUSTED') && anyFinite(C), oxy:!has(nc, 'DOXY_ADJUSTED') && anyFinite(O)};
      // segments: PHASE_NUMBER when present, else reversals of the pressure direction (5 dbar hysteresis)
      const seg = new Array(n).fill(-1), phaseNo = num(nc, 'PHASE_NUMBER'), phase = num(nc, 'PHASE');
      if(phaseNo) for(let k=0;k<n;k++) seg[k] = phase && ![1, 4].includes(phase[k]) ? -1 : phaseNo[k];
      else {
        let s = 0, dir = 0, ext = NaN;
        for(let k=0;k<n;k++){
          if(!Number.isFinite(P[k])) continue;
          if(!Number.isFinite(ext)){ ext = P[k]; seg[k] = s; continue; }
          const d = P[k] - ext;
          if(dir===0 && Math.abs(d) > 5) dir = Math.sign(d);
          if(dir!==0 && Math.sign(d)===-dir && Math.abs(d) > 5){ s++; dir = -dir; }
          if((dir >= 0 && P[k] > ext) || (dir < 0 && P[k] < ext) || dir===0) ext = P[k];
          seg[k] = s;
        }
      }
      const groups = new Map();
      seg.forEach((s,k)=>{ if(s >= 0 && Number.isFinite(s) && Number.isFinite(t[k])){ if(!groups.has(s)) groups.set(s, []); groups.get(s).push(k); } });
      const name = String(nc.attrs.platform_code || nc.attrs.deployment_label || nc.attrs.id || 'GLIDER').trim();
      const profiles = []; let dropped = 0;
      for(const [s, ks] of groups){
        const pk = ks.filter(k=>Number.isFinite(P[k]));
        const la = mean(ks.map(k=>lat[k])), lo = mean(ks.map(k=>lon[k]));
        if(pk.length < 5 || !Number.isFinite(la) || !Number.isFinite(lo)
          || Math.max(...pk.map(k=>P[k])) - Math.min(...pk.map(k=>P[k])) < 10){ dropped++; continue; }
        // 1 m bins keep a dive to a few hundred levels
        const bins = new Map();
        pk.forEach(k=>{ const b = Math.round(depthFromPressure(P[k], la)); if(!bins.has(b)) bins.set(b, []); bins.get(b).push(k); });
        const zs = [...bins.keys()].sort((a,b)=>a-b), vals = {};
        [['temp', T], ['sal', S], ['chl', C], ['oxy', O]].forEach(([key, arr])=>{
          if(arr) vals[key] = zs.map(b=>mean(bins.get(b).map(k=>arr[k])));
        });
        const times = ks.map(k=>t[k]).sort((a,b)=>a-b);
        const pr = profile({id:name, lat:la, lon:lo, time:iso(times[times.length>>1]), meta:{platform:name, cycle:s}}, zs, vals);
        if(pr) profiles.push(pr); else dropped++;
      }
      const warnings = dropped ? [left(dropped, 'segment', 'segments', 'too short or without a position')] : [];
      warnings.push(...rawWarnings(raw));
      return {type:'glider', format:'ego_glider', profiles, warnings, notes:[NOTE]};
    }});

  // ---- tables: ERDDAP tabledap .nc ("row" dimension) and CF tables with a profile id
  function readTable(nc, fmt){
    const low = lowerMap(nc), rowDim = nc.vars[find(nc, 'lat') || Object.keys(nc.vars)[0]].dims[0];
    const n = (nc.dims.find(d=>d.name===rowDim) || {size:0}).size;
    const col = key=>find(nc, key);
    const latN = col('lat'), lonN = col('lon'), timeN = col('time');
    if(!latN || !lonN || !timeN) throw new Error('This table has no latitude, longitude or time column.');
    const lat = num(nc, latN), lon = num(nc, lonN), t = timeMs(nc, timeN);
    const idN = Object.keys(nc.vars).find(k=>/profile_id|trajectory_id|timeseries_id/.test(String(nc.vars[k].attrs.cf_role || '')))
      || low.platform_number || low.platform_code || low.station || low.id || low.cast;
    const ids = idN ? strs(nc, idN) : null, cyc = low.cycle_number ? num(nc, low.cycle_number) : null;
    const base = (key, name)=>{
      const raw = name, adj = name && low[name.toLowerCase()+'_adjusted'];
      const qc = n2=>n2 && (low[n2.toLowerCase()+'_qc'] || null);
      return {raw:num(nc, raw), rawQc:flags(nc, qc(raw), n), adj:num(nc, adj), adjQc:flags(nc, qc(adj), n), adjExists:!!adj};
    };
    const pN = col('pres'), dN = col('depth');
    const Pc = pN ? base('pres', pN) : null, Dc = !pN && dN ? base('depth', dN) : null;
    const C = {}; VARS.forEach(key=>{ const nm = col(key); if(nm && !/_adjusted$/i.test(nm)) C[key] = base(key, nm);
      else if(nm) C[key] = {raw:null, rawQc:null, adj:num(nc, nm), adjQc:flags(nc, low[nm.toLowerCase()+'_qc'], n), adjExists:true}; });
    if(!Pc && !Dc) throw new Error('This table has no depth or pressure column.');
    const groups = new Map();
    for(let k=0;k<n;k++){
      const key = (ids ? ids[k] : '') + '|' + (cyc ? cyc[k] : '') + '|' + t[k];
      if(!groups.has(key)) groups.set(key, []); groups.get(key).push(k);
    }
    const profiles = [], raw = {}; let dropped = 0;
    for(const ks of groups.values()){
      const k0 = ks[0];
      if(!Number.isFinite(lat[k0]) || !Number.isFinite(lon[k0]) || !Number.isFinite(t[k0])){ dropped++; continue; }
      const c = Pc || Dc;
      const z = ks.map(k=>{ const v = pick(null, c.raw, c.rawQc, c.adj, c.adjQc, k, false);
        return Pc && Number.isFinite(v) ? depthFromPressure(v, lat[k0]) : v; });
      const vals = {};
      for(const [key, c] of Object.entries(C)){
        const bgc = key==='chl' || key==='oxy';
        vals[key] = ks.map(k=>pick(key, c.raw, c.rawQc, c.adj, c.adjQc, k, bgc && c.adjExists));
        if(bgc && !c.adjExists && anyFinite(vals[key])) raw[key] = true;
      }
      const id = ids ? (/^\d{5,7}$/.test(ids[k0]) ? 'ARGO-'+ids[k0] : ids[k0]) : 'P'+(profiles.length+1);
      const pr = profile({id, lat:lat[k0], lon:lon[k0], time:iso(t[k0]), meta:{platform:ids ? ids[k0] : null,
        cycle:cyc ? cyc[k0] : null}}, z, vals);
      if(pr) profiles.push(pr); else dropped++;
    }
    const cdm = String(nc.attrs.cdm_data_type || nc.attrs.featureType || '').toLowerCase();
    const type = low.platform_number ? 'argo' : /trajectory/.test(cdm) ? 'glider' : 'other';
    const warnings = dropped ? [left(dropped, 'profile', 'profiles', 'no good position, time or samples')] : [];
    warnings.push(...rawWarnings(raw));
    return {type, format:fmt, profiles, warnings, notes:[NOTE]};
  }
  OceanLens.registerFormat('erddap_table', {label:'ERDDAP tabledap NetCDF', kind:'netcdf',
    detect:nc=>nc.dims.some(d=>d.name==='row') ? (nc.attrs.cdm_data_type || /erddap/i.test(String(nc.attrs.infoUrl || nc.attrs.history || '')) ? 0.9 : 0.6) : 0,
    read:nc=>readTable(nc, 'erddap_table')});

  // ---- generic CF: one profile (data on a depth axis), profiles x levels, or a table
  OceanLens.registerFormat('cf_generic', {label:'CF NetCDF profiles', kind:'netcdf',
    detect:nc=>(find(nc, 'lat') && find(nc, 'lon') && VARS.some(k=>find(nc, k)) ? 0.4 : 0) + (/CF/.test(String(nc.attrs.Conventions || '')) ? 0.1 : 0),
    read(nc){
      const latN = find(nc, 'lat'), lonN = find(nc, 'lon'), timeN = find(nc, 'time');
      const zN = find(nc, 'pres') || find(nc, 'depth');
      const dataN = VARS.map(k=>find(nc, k)).find(Boolean);
      if(!zN) throw new Error('No depth or pressure variable was found.');
      if(!dataN) throw new Error('No temperature, salinity, chlorophyll or oxygen variable was found.');
      const dv = nc.vars[dataN], lv = nc.vars[latN];
      if(lv.dims.length && dv.dims.length >= 3) throw new Error('This looks like gridded model output. Build a snapshot from it with data-pipeline/build_snapshot.py --model-netcdf.');
      if(lv.dims.length===1 && dv.dims.length===1 && lv.dims[0]===dv.dims[0]) return readTable(nc, 'cf_generic');
      const isP = !!find(nc, 'pres') && zN===find(nc, 'pres');
      const lat = num(nc, latN), lon = num(nc, lonN), t = timeN ? timeMs(nc, timeN) : null;
      if(!t) throw new Error('No time variable was found.');
      const nP = dv.dims.length===2 ? nc.vars[dataN].shape[0] : 1, nL = dv.shape[dv.shape.length-1];
      const zA = num(nc, zN), zPer = zA.length===nL ? (p=>zA) : (p=>zA.slice(p*nL, (p+1)*nL));
      const cols = {}; VARS.forEach(k=>{ const nm = find(nc, k); if(nm) cols[k] = {a:num(nc, nm), q:flags(nc, lowerMap(nc)[nm.toLowerCase()+'_qc'], nP*nL)}; });
      const per = (a, p)=>a && a.length===nP*nL && nP > 1 ? a.slice(p*nL, (p+1)*nL) : a;
      const profiles = [], raw = {};
      for(let p=0;p<nP;p++){
        const la = lat[Math.min(p, lat.length-1)], lo = lon[Math.min(p, lon.length-1)], tm = t[Math.min(p, t.length-1)];
        if(!Number.isFinite(la) || !Number.isFinite(lo) || !Number.isFinite(tm)) continue;
        const z = Array.from(zPer(p), x=>isP && Number.isFinite(x) ? depthFromPressure(x, la) : x), vals = {};
        for(const [k, c] of Object.entries(cols)){
          const a = per(c.a, p), q = per(c.q, p);
          vals[k] = Array.from({length:nL}, (_,i)=>pick(k, a, q, null, null, i, false));
          if(LABEL[k] && anyFinite(vals[k])) raw[k] = true;
        }
        const pr = profile({id:String(nc.attrs.id || nc.attrs.station || 'CF')+(nP > 1 ? '-'+(p+1) : ''), lat:la, lon:lo,
          time:iso(tm), meta:{}}, z, vals);
        if(pr) profiles.push(pr);
      }
      return {type:'other', format:'cf_generic', profiles, warnings:rawWarnings(raw), notes:[NOTE]};
    }});

  function mean(a){ let s = 0, c = 0; a.forEach(x=>{ if(Number.isFinite(x)){ s += x; c++; } }); return c ? s/c : NaN; }
})();
