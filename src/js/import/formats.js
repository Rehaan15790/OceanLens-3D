/* NetCDF format mappings for "Add data" (science): Argo profile files (core, BGC and synthetic), Ifremer EGO
   glider files, ERDDAP tabledap .nc tables and generic CF profiles. Each reader applies QC and units and
   returns a dataset (see plugins.js): °C, PSU, mg/m³, µmol/kg, depth in m.
   QC as in the snapshot: Argo flags 1, 2, 5, 8 kept; adjusted values wherever a file has them (raw values
   only for real-time data); chlorophyll and oxygen from adjusted values only when adjusted columns exist. */
(function(){
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
  // a CF depth axis with positive="up" holds heights (negative below the surface)
  const isUp = (nc, n)=>has(nc, n) && String(nc.vars[n].attrs.positive || '').trim().toLowerCase()==='up';
  const lowerMap = nc=>{ const m = {}; Object.keys(nc.vars).forEach(k=>{ m[k.toLowerCase()] = k; }); return m; };
  // the variable holding a quantity (a CF key): by standard_name first, then by usual names; null when absent
  function find(nc, key, extraNames){
    const [sns, names] = CF[key];
    for(const n of Object.keys(nc.vars)) if(sns.includes(String(nc.vars[n].attrs.standard_name || ''))
      && !/_qc$|_error$/i.test(n)) return n;
    const low = lowerMap(nc);
    for(const n of (extraNames || []).concat(names)) if(low[n]) return low[n];
    return null;
  }
  // len flags, one character each, from char variables (any shape; the first byte of each flag's share of
  // the bytes) or numeric flag variables; null when there is none or not one is set (ERDDAP
  // glider tables carry flag columns that are all missing)
  function flags(nc, n, len){
    if(!has(nc, n)) return null;
    let f = null;
    if(nc.vars[n].type==='char'){
      // the raw bytes, not read(): its strings are trimmed, and a blank flag before the first set one (a level
      // a sensor did not sample) would shift every later flag onto the wrong level
      const r = nc.raw(n), w = r.length / len;
      if(!r.length || !Number.isInteger(w) || w < 1) return null;
      f = Array.from({length:len}, (_, i)=>r[i*w] ? String.fromCharCode(r[i*w]) : ' ');
    } else {
      const r = nc.read(n);
      if(r.length===len) f = Array.from(r, v=>Number.isFinite(v) ? String(Math.round(v)) : ' ');
    }
    return f && f.some(c=>c!==' ') ? f : null;
  }
  const strs = (nc, n)=>has(nc, n) ? Array.from(nc.read(n), s=>typeof s==='string' ? s.trim() : String(s)) : null;
  // a CF time variable -> ms since 1970 (NaN where missing); throws on units it cannot read
  function timeMs(nc, n){
    const v = num(nc, n); if(!v) return null;
    const u = String(nc.vars[n].attrs.units || '');
    const m = u.match(/^\s*(\w+?)s?\s+since\s+(\d{1,4})-(\d{1,2})-(\d{1,2})(?:[ T](\d{1,2}):(\d{1,2})(?::(\d{1,2}(?:\.\d*)?))?)?/i);
    const unit = m && {second:1e3, sec:1e3, s:1e3, minute:6e4, min:6e4, hour:36e5, hr:36e5, h:36e5, day:864e5, d:864e5}[m[1].toLowerCase()];
    if(!unit) throw new Error(`Can't read the times: ${n} has units "${u}". Expected something like "days since 1950-01-01".`);
    const cal = String(nc.vars[n].attrs.calendar || 'standard').trim().toLowerCase();
    if(!['standard', 'gregorian', 'proleptic_gregorian', 'julian', ''].includes(cal)) throw new Error(`Can't read the times: ${n} uses the "${cal}" calendar.`);
    const y = +m[2], mo = +m[3], d = +m[4], hms = 36e5*(+(m[5]||0)) + 6e4*(+(m[6]||0)) + 1000*(+(m[7]||0));
    let base;
    if(cal==='julian' || (cal!=='proleptic_gregorian' && y*1e4 + mo*100 + d < 15821015)){
      // a Julian calendar date (the standard calendar is Julian before 15 October 1582, e.g. "hours since 1-1-1"),
      // through its Julian day number; read as Gregorian it would be 2 days off in year 1
      const a = Math.floor((14 - mo)/12), yy = y + 4800 - a, mm = mo + 12*a - 3;
      base = (d + Math.floor((153*mm + 2)/5) + 365*yy + Math.floor(yy/4) - 32083 - 2440588)*864e5 + hms;
    } else {
      const epoch = new Date(Date.UTC(2000, 0, 1));
      epoch.setUTCFullYear(y, mo-1, d);              // Date.UTC would read years 0–99 as 1900–1999
      base = epoch.getTime() + hms;
    }
    return Array.from(v, x=>Number.isFinite(x) ? base + x*unit : NaN);
  }
  const iso = ms=>new Date(ms).toISOString().slice(0, 16)+'Z';
  // one dive or climb from pressures (z: dbar) on 1 m depth bins at the segment's mean latitude (a few hundred levels)
  const dives = (ks, s)=>diveProfile(ks, s, (p, la)=>Math.round(depthFromPressure(p, la)));
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
      // N_PROF x N_LEVELS must be what the data variables hold: dimensions that claim more would only make the loop spin
      const held = Math.max(0, ...Object.values(A).map(a=>a ? a.length : 0));
      if(nP*nL > held) throw new Error(`The file says ${nP} profiles of ${nL} levels but its variables hold only ${held} values.`);
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
      // each variable is read once (not once per sample, which made a long file quadratic)
      const v = (name, key, adjOnly)=>{
        const f = pick.bind(null, key, num(nc, name), flags(nc, name+'_QC', n), num(nc, name+'_ADJUSTED'), flags(nc, name+'_ADJUSTED_QC', n));
        return Array.from({length:n}, (_,k)=>f(k, adjOnly));
      };
      const P = v('PRES', null, false);
      const T = v('TEMP', 'temp', false);
      const S = v('PSAL', 'sal', false);
      const C = has(nc, 'CHLA') ? v('CHLA', 'chl', has(nc, 'CHLA_ADJUSTED')) : null;
      let O = null;
      if(has(nc, 'DOXY')) O = v('DOXY', 'oxy', has(nc, 'DOXY_ADJUSTED'));
      else if(has(nc, 'MOLAR_DOXY')){          // µmol/L -> µmol/kg with the sample's density
        const M = v('MOLAR_DOXY', 'oxy', false);
        O = M.map((x,k)=>Number.isFinite(x) && Number.isFinite(T[k]) && Number.isFinite(S[k]) ? x/(1+sigmaT(S[k], T[k])/1000) : NaN);
      }
      const raw = {chl:!has(nc, 'CHLA_ADJUSTED') && anyFinite(C), oxy:!has(nc, 'DOXY_ADJUSTED') && anyFinite(O)};
      // segments: PHASE_NUMBER when present, else reversals of the pressure direction (diveSegments, csv.js)
      const phaseNo = num(nc, 'PHASE_NUMBER'), phase = num(nc, 'PHASE');
      let groups = new Map();
      if(phaseNo) for(let k=0;k<n;k++){
        const s = phase && ![1, 4].includes(phase[k]) ? -1 : phaseNo[k];
        if(s >= 0 && Number.isFinite(s)){ if(!groups.has(s)) groups.set(s, []); groups.get(s).push(k); }
      }
      else groups = diveSegments(P);
      const name = String(nc.attrs.platform_code || nc.attrs.deployment_label || nc.attrs.id || 'GLIDER').trim();
      const profiles = []; let dropped = 0;
      const smp = {ms:t, lat, lon, z:P, vals:{temp:T, sal:S, chl:C, oxy:O}};
      for(const [s, all] of groups){
        const ks = all.filter(k=>Number.isFinite(t[k]));
        if(!ks.length) continue;
        const d = dives(ks, smp);
        const pr = d && profile({id:name, lat:d.lat, lon:d.lon, time:iso(d.ms), meta:{platform:name, cycle:s}}, d.z, d.vars);
        if(pr) profiles.push(pr); else dropped++;
      }
      const warnings = dropped ? [left(dropped, 'segment', 'segments', 'too short or without a position')] : [];
      warnings.push(...rawWarnings(raw));
      return {type:'glider', format:'ego_glider', profiles, warnings, notes:[NOTE]};
    }});

  // ---- tables: ERDDAP tabledap .nc ("row" dimension) and CF tables with a profile id; a trajectory with no
  // profile id (OceanGliders ERDDAP) is split into dives and climbs per platform, as an EGO file is
  function readTable(nc, fmt){
    const low = lowerMap(nc), rowDim = nc.vars[find(nc, 'lat') || Object.keys(nc.vars)[0]].dims[0];
    const n = (nc.dims.find(d=>d.name===rowDim) || {size:0}).size;
    const col = key=>find(nc, key);
    const latN = col('lat'), lonN = col('lon'), timeN = col('time');
    if(!latN || !lonN || !timeN) throw new Error('This table has no latitude, longitude or time column.');
    const lat = num(nc, latN), lon = num(nc, lonN), t = timeMs(nc, timeN);
    const role = k=>String(nc.vars[k].attrs.cf_role || '');
    const idN = Object.keys(nc.vars).find(k=>k!==timeN && /profile_id|trajectory_id|timeseries_id/.test(role(k)))
      || low.platform_number || low.platform_code || low.platform_deployment || low.station || low.id || low.cast;
    const ids = idN ? strs(nc, idN) : null, cyc = low.cycle_number ? num(nc, low.cycle_number) : null;
    const byProfile = Object.keys(nc.vars).some(k=>role(k)==='profile_id');
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
    const c = Pc || Dc, dUp = !!Dc && isUp(nc, dN);
    const zAt = (k, la)=>{ const v = pick(null, c.raw, c.rawQc, c.adj, c.adjQc, k, false);
      return !Number.isFinite(v) ? v : Pc ? depthFromPressure(v, la) : dUp ? 0 - v : v; };
    const raw = {}, valsAt = ks=>{
      const vals = {};
      for(const [key, c] of Object.entries(C)){
        const bgc = key==='chl' || key==='oxy';
        vals[key] = ks.map(k=>pick(key, c.raw, c.rawQc, c.adj, c.adjQc, k, bgc && c.adjExists));
        if(bgc && !c.adjExists && anyFinite(vals[key])) raw[key] = true;
      }
      return vals;
    };
    const groups = new Map(), plats = new Map();
    for(let k=0;k<n;k++){
      const key = (ids ? ids[k] : '') + '|' + (cyc ? cyc[k] : '') + '|' + t[k];
      if(!groups.has(key)){
        groups.set(key, []);
        const p = ids ? ids[k] : '';
        if(!plats.has(p)) plats.set(p, []);
        plats.get(p).push(key);
      }
      groups.get(key).push(k);
    }
    const cdm = String(nc.attrs.cdm_data_type || nc.attrs.featureType || '').toLowerCase();
    const profiles = []; let dropped = 0, cut = 0, dived = 0;
    for(const [p, keys] of plats){
      // a trajectory: nearly every sample has its own time, and the depth changes (not a mooring's time series)
      const ks = [];
      keys.forEach(g=>groups.get(g).forEach(k=>{ if(Number.isFinite(t[k]) && Number.isFinite(lat[k]) && Number.isFinite(lon[k])) ks.push(k); }));
      const z = byProfile || /timeseries/.test(cdm) || keys.length < 5 || keys.length < 0.5*ks.length ? null
        : ks.sort((a, b)=>t[a]-t[b]).map(k=>zAt(k, lat[k]));
      let z0 = Infinity, z1 = -Infinity;
      if(z) z.forEach(v=>{ if(Number.isFinite(v)){ z0 = Math.min(z0, v); z1 = Math.max(z1, v); } });
      if(z && z1 - z0 >= 10){
        const id = p || String(nc.attrs.platform_code || nc.attrs.id || 'GLIDER').trim(), segs = [...diveSegments(z)];
        const smp = {ms:ks.map(k=>t[k]), lat:ks.map(k=>lat[k]), lon:ks.map(k=>lon[k]), z, vals:valsAt(ks)};
        const ds = segs.map(([s, is])=>{ const d = diveProfile(is, smp);
          return d && profile({id, lat:d.lat, lon:d.lon, time:iso(d.ms), meta:{platform:p || null, cycle:s}}, d.z, d.vars); }).filter(Boolean);
        if(ds.length){ profiles.push(...ds); dived += ds.length; cut += segs.length - ds.length; continue; }
      }
      for(const g of keys){
        const ks = groups.get(g), k0 = ks[0];
        if(!Number.isFinite(lat[k0]) || !Number.isFinite(lon[k0]) || !Number.isFinite(t[k0])){ dropped++; continue; }
        const id = ids ? (/^\d{5,7}$/.test(ids[k0]) ? 'ARGO-'+ids[k0] : ids[k0]) : 'P'+(profiles.length+1);
        const pr = profile({id, lat:lat[k0], lon:lon[k0], time:iso(t[k0]), meta:{platform:ids ? ids[k0] : null,
          cycle:cyc ? cyc[k0] : null}}, ks.map(k=>zAt(k, lat[k0])), valsAt(ks));
        if(pr) profiles.push(pr); else dropped++;
      }
    }
    const type = low.platform_number ? 'argo' : /trajectory/.test(cdm) || dived ? 'glider' : 'other';
    const warnings = dropped ? [left(dropped, 'profile', 'profiles', 'no good position, time or samples')] : [];
    if(cut) warnings.push(left(cut, 'dive or climb', 'dives and climbs', 'under 5 samples or 10 m'));
    warnings.push(...rawWarnings(raw));
    const notes = dived ? [NOTE, `Trajectory split into ${dived===1 ? 'one dive or climb' : dived+' dives and climbs'} where the pressure turns round; 1 m bins.`] : [NOTE];
    return {type, format:fmt, profiles, warnings, notes};
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
      const isP = !!find(nc, 'pres') && zN===find(nc, 'pres'), zUp = !isP && isUp(nc, zN);
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
        const z = Array.from(zPer(p), x=>!Number.isFinite(x) ? x : isP ? depthFromPressure(x, la) : zUp ? 0 - x : x), vals = {};
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
})();
