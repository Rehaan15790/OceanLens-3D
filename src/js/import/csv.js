/* ============================================================
   DELIMITED TEXT IMPORT (comma, tab or semicolon)
   parseDelimited(text) -> table; guessMapping(table) -> column mapping; tableToDataset(table,
   mapping) -> dataset (one row per sample, grouped into profiles by id + time; a trajectory, such as a
   glider's, split into dives and climbs).
   ============================================================ */
const CSV_FIELDS = ['lat', 'lon', 'time', 'depth', 'pres', 'temp', 'sal', 'chl', 'oxy', 'u', 'v', 'id', 'type'];
const CSV_MEASURED = ['temp', 'sal', 'chl', 'oxy', 'u', 'v'];
// header names per field, compared after lower-casing and dropping units; first match wins, so
// adjusted (delayed-mode) columns come before raw ones
const CSV_NAMES = {
  lat:['latitude', 'lat', 'lat_deg'], lon:['longitude', 'lon', 'long', 'lng', 'lon_deg'],
  time:['time', 'datetime', 'date_time', 'timestamp', 'juld', 'date', 'time_utc'],
  depth:['depth', 'z', 'depth_below_surface'], pres:['pres_adjusted', 'pres', 'pressure', 'press'],
  temp:['temp_adjusted', 'temp', 'temperature', 'sea_water_temperature', 'water_temp', 't'],
  sal:['psal_adjusted', 'psal', 'sal', 'salinity', 'sea_water_salinity', 's'],
  chl:['chla_adjusted', 'chla', 'chl', 'chl_a', 'chlorophyll', 'chlorophyll_a'],
  oxy:['doxy_adjusted', 'doxy', 'oxy', 'oxygen', 'o2', 'dissolved_oxygen'],
  u:['u', 'ucur', 'water_u', 'east_vel', 'vel_east', 'eastward_velocity', 'eastward_sea_water_velocity'],
  v:['v', 'vcur', 'water_v', 'north_vel', 'vel_north', 'northward_velocity', 'northward_sea_water_velocity'],
  id:['platform_number', 'platform', 'platform_code', 'platform_deployment', 'wmo', 'float', 'float_id', 'trajectory', 'glider',
      'station', 'station_id', 'cast', 'cast_id', 'id', 'site'],
  type:['instrument_type', 'platform_type', 'instrument', 'type']
};
const CSV_UNIT_TOKENS = /^(c|degc|°c|deg_c|degree_c|degrees_c|degree_celsius|degrees_celsius|celsius|f|degf|°f|k|kelvin|m|meters|metres|ft|feet|km|dbar|decibar|db|bar|kpa|psu|pss|pss78|pss-78|g\/kg|1|1e-3|mg\/m3|mg_m3|mg_m-3|mg m-3|ug\/l|µg\/l|ugl|ug l-1|umol\/kg|µmol\/kg|umol_kg|umol kg-1|micromole\/kg|micromol\/kg|umol\/l|µmol\/l|umol l-1|micromole\/l|micromol\/l|mmol m-3|ml\/l|ml_l|mg\/l|mg_l|degrees_north|degrees_east|degree_north|degree_east|deg|utc|iso8601|m\/s|cm\/s|mm\/s|m_s|cm_s|m s-1|cm s-1|kn|knots)$/i;

const isNumCell = c => c!=='' && Number.isFinite(+c);
function splitLine(line, d){
  const out = []; let cur = '', q = false;
  for(let i=0; i<line.length; i++){
    const ch = line[i];
    if(q){
      if(ch==='"'){ if(line[i+1]==='"'){ cur += '"'; i++; } else q = false; }
      else cur += ch;
    } else if(ch==='"' && cur.trim()==='') { q = true; cur = ''; }
    else if(ch===d){ out.push(cur.trim()); cur = ''; }
    else cur += ch;
  }
  out.push(cur.trim());
  return out;
}
function parseDelimited(text){
  const lines = String(text).replace(/^﻿/, '').split(/\r\n|\n|\r/).filter(l=>l.trim()!=='' && !/^\s*#/.test(l));
  if(lines.length < 2) throw new Error('The file has fewer than two lines of data.');
  // the delimiter that splits the first lines into the same number (> 1) of cells
  let delimiter = null, best = 1;
  [',', '\t', ';'].forEach(d=>{
    // most of the first lines (a stray ragged row is skipped later) split like the header
    const n = lines.slice(0, 20).map(l=>splitLine(l, d).length), same = n.filter(k=>k===n[0]).length;
    if(n[0] > best && same >= Math.max(2, 0.7*n.length)){ best = n[0]; delimiter = d; }
  });
  if(!delimiter) throw new Error('No consistent comma, tab or semicolon columns found.');
  let rows = lines.map(l=>splitLine(l, delimiter));
  const width = rows[0].length;
  const all = rows.length;
  rows = rows.filter(r=>r.length===width);
  const ragged = all - rows.length;
  // a units row (ERDDAP): no numbers other than units (1, 1e-3), unit-like words, and numbers below it
  const unitsRow = (r, below)=>!!r && !!below && !r.some(c=>isNumCell(c) && !CSV_UNIT_TOKENS.test(c)) &&
    r.some((c, j)=>c!=='' && isNumCell(below[j]) && CSV_UNIT_TOKENS.test(c.replace(/\s+/g, ' ')));
  // a header row has a text cell above a number, or above a units row
  const headerDetected = rows[0].some((c, j)=>!isNumCell(c) && isNumCell(rows[1][j])) ||
    (rows[0].some(c=>c!=='' && !isNumCell(c)) && unitsRow(rows[1], rows[2]));
  const header = headerDetected ? rows.shift() : rows[0].map((_, j)=>`Column ${j+1}`);
  let units = header.map(()=>'' );
  if(headerDetected && unitsRow(rows[0], rows[1])) units = rows.shift();
  // units written into headers: temp_C, temp (degC), pres[dbar]
  const names = header.map((h, j)=>{
    let name = h, unit = units[j];
    const br = h.match(/^(.*?)\s*[([]\s*([^)\]]+)\s*[)\]]\s*$/);
    if(br){ name = br[1]; unit = unit || br[2]; }
    else {
      const us = h.match(/^(.+?)[_ ]([^_ ]+)$/);
      if(us && CSV_UNIT_TOKENS.test(us[2]) && !/^(degrees_(north|east))$/i.test(h)){ name = us[1]; unit = unit || us[2]; }
    }
    units[j] = unit || '';
    return name.trim().toLowerCase().replace(/[\s-]+/g, '_');
  });
  return {delimiter, headerDetected, header, names, units, rows, ragged};
}

function guessMapping(table){
  const mapping = {}, used = new Set();
  CSV_FIELDS.forEach(f=>{
    for(const n of CSV_NAMES[f]){
      const j = table.names.findIndex((x, k)=>x===n && !used.has(k));
      if(j >= 0){ mapping[f] = j; used.add(j); break; }
    }
  });
  const missing = ['lat', 'lon', 'time'].filter(f=>mapping[f]===undefined);
  // surface current maps (HF radar) have u and v but no depth
  const surface = mapping.u!==undefined && mapping.v!==undefined;
  if(mapping.depth===undefined && mapping.pres===undefined && !surface) missing.push('depth or pressure');
  if(!CSV_MEASURED.some(f=>mapping[f]!==undefined)) missing.push('a measured variable');
  return {mapping, missing, needsMapping: !table.headerDetected || missing.length > 0};
}

// ISO 8601 or 'YYYY-MM-DD hh:mm[:ss]' (also with '/'); UTC unless an offset is given
function parseTimeCell(s){
  const m = String(s).trim().match(/^(\d{4})[-/](\d{1,2})[-/](\d{1,2})(?:[T ](\d{1,2}):(\d{2})(?::(\d{2}(?:\.\d+)?))?)?\s*(Z|UTC|GMT|[+-]\d{2}:?\d{2})?$/i);
  if(!m) return NaN;
  const [y, mo, d, h, mi, sec] = [+m[1], +m[2], +m[3], +(m[4] || 0), +(m[5] || 0), +(m[6] || 0)];
  if(mo < 1 || mo > 12 || d < 1 || h > 23 || mi > 59 || sec >= 61) return NaN;
  if(new Date(Date.UTC(y, mo-1, d)).getUTCDate() !== d) return NaN;   // 31 April and the like
  let ms = Date.UTC(y, mo-1, d, h, mi, 0) + Math.round(sec*1000);
  const tz = m[7] && m[7].match(/^([+-])(\d{2}):?(\d{2})$/);
  if(tz) ms -= (tz[1]==='+' ? 1 : -1)*(+tz[2]*60 + +tz[3])*6e4;
  return ms;
}
// unit conversions to °C, m, dbar, PSU, mg/m³, µmol/kg; null = unit not recognised
function unitConverter(field, unit){
  const u = String(unit || '').trim().toLowerCase().replace(/\s+/g, ' ');
  const same = x=>x;
  if(!u) return same;
  const table = {
    temp:{c:same, degc:same, '°c':same, deg_c:same, degree_c:same, degrees_c:same, degree_celsius:same, degrees_celsius:same, celsius:same,
          f:x=>(x-32)*5/9, degf:x=>(x-32)*5/9, '°f':x=>(x-32)*5/9, k:x=>x-273.15, kelvin:x=>x-273.15},
    depth:{m:same, meters:same, metres:same, ft:x=>x*0.3048, feet:x=>x*0.3048, km:x=>x*1000},
    pres:{dbar:same, decibar:same, db:same, bar:x=>x*10, kpa:x=>x/10},
    sal:{psu:same, pss:same, pss78:same, 'pss-78':same, 1:same, '1e-3':same, 'g/kg':same},
    chl:{'mg/m3':same, mg_m3:same, 'mg_m-3':same, 'mg m-3':same, 'ug/l':same, 'µg/l':same, ugl:same, 'ug l-1':same},
    // µmol/L to µmol/kg with a seawater density of 1.025 kg/L; 1 mL/L O2 = 44.661 µmol/L
    u:{'m/s':same, m_s:same, 'm s-1':same, 'cm/s':x=>x/100, cm_s:x=>x/100, 'cm s-1':x=>x/100, 'mm/s':x=>x/1000, kn:x=>x*0.514444, knots:x=>x*0.514444},
    v:{'m/s':same, m_s:same, 'm s-1':same, 'cm/s':x=>x/100, cm_s:x=>x/100, 'cm s-1':x=>x/100, 'mm/s':x=>x/1000, kn:x=>x*0.514444, knots:x=>x*0.514444},
    oxy:{'umol/kg':same, 'µmol/kg':same, umol_kg:same, 'umol kg-1':same, 'micromole/kg':same, 'micromol/kg':same,
         'umol/l':x=>x/1.025, 'µmol/l':x=>x/1.025, 'umol l-1':x=>x/1.025, 'micromole/l':x=>x/1.025, 'micromol/l':x=>x/1.025, 'mmol m-3':x=>x/1.025,
         'ml/l':x=>x*44.661/1.025, ml_l:x=>x*44.661/1.025, 'mg/l':x=>x*31.252/1.025, mg_l:x=>x*31.252/1.025}
  }[field];
  if(!table) return same;
  return table[u] || null;
}
const TYPE_WORDS = {adcp:'adcp', radar:'hfradar', hfr:'hfradar', argo:'argo', float:'argo', 'bgc-argo':'argo', glider:'glider',
                    ctd:'ctd', cast:'ctd', mooring:'mooring', buoy:'mooring'};
function typeFromText(s){
  const w = String(s || '').toLowerCase();
  for(const k of Object.keys(TYPE_WORDS)) if(w.includes(k)) return TYPE_WORDS[k];
  return null;
}

// a trajectory in time order (a glider, a towed or profiling CTD) -> its dives and climbs, split where the depth
// or pressure turns round by more than 5 m (dbar) from its extreme: Map(segment -> sample indices); samples
// without z join none. Until it has moved 5 m the start is the reference, so dense sampling still turns.
function diveSegments(z){
  const out = new Map();
  let s = 0, dir = 0, ext = NaN;
  for(let k=0;k<z.length;k++){
    if(!Number.isFinite(z[k])) continue;
    if(Number.isFinite(ext)){
      const d = z[k] - ext;
      if(dir===0){ if(Math.abs(d) > 5){ dir = Math.sign(d); ext = z[k]; } }
      else if(d*dir < -5){ s++; dir = -dir; ext = z[k]; }
      else if(d*dir > 0) ext = z[k];
    } else ext = z[k];
    if(!out.has(s)) out.set(s, []);
    out.get(s).push(k);
  }
  return out;
}
// one dive or climb (indices ks into s = {ms, lat, lon, z, vals}) -> {lat, lon, ms, z, vars} on 1 m bins (bin(z, lat)),
// each the mean of its samples, at the mean position and middle time; null under 5 samples or 10 m
function diveProfile(ks, s, bin = z=>Math.round(z)){
  const mean = a=>{ let t = 0, c = 0; a.forEach(x=>{ if(Number.isFinite(x)){ t += x; c++; } }); return c ? t/c : NaN; };
  const pk = ks.filter(k=>Number.isFinite(s.z[k])), la = mean(ks.map(k=>s.lat[k])), lo = mean(ks.map(k=>s.lon[k]));
  if(pk.length < 5 || !Number.isFinite(la) || !Number.isFinite(lo)) return null;
  let z0 = Infinity, z1 = -Infinity;
  pk.forEach(k=>{ z0 = Math.min(z0, s.z[k]); z1 = Math.max(z1, s.z[k]); });
  if(z1 - z0 < 10) return null;
  const bins = new Map();
  pk.forEach(k=>{ const b = bin(s.z[k], la); if(!bins.has(b)) bins.set(b, []); bins.get(b).push(k); });
  const z = [...bins.keys()].sort((a, b)=>a-b), vars = {};
  Object.entries(s.vals).forEach(([key, a])=>{ if(a) vars[key] = z.map(b=>mean(bins.get(b).map(k=>a[k]))); });
  const times = ks.map(k=>s.ms[k]).filter(Number.isFinite).sort((a, b)=>a-b);
  return {lat:la, lon:lo, ms:times[times.length>>1], z, vars};
}

function tableToDataset(table, mapping, opts = {}){
  const col = f=>mapping[f]===undefined || mapping[f]===null || mapping[f]==='' ? -1 : +mapping[f];
  ['lat', 'lon', 'time'].forEach(f=>{ if(col(f) < 0) throw new Error(`Choose the column that holds ${f==='lat' ? 'latitude' : f==='lon' ? 'longitude' : 'time'}.`); });
  const zf = col('depth') >= 0 ? 'depth' : col('pres') >= 0 ? 'pres' : null;
  const surface = !zf && col('u') >= 0 && col('v') >= 0;          // a surface current map: every row at 0 m
  if(!zf && !surface) throw new Error('Choose the column that holds depth or pressure.');
  const vars = CSV_MEASURED.filter(f=>col(f) >= 0);
  if(!vars.length) throw new Error('Choose at least one measured column (temperature, salinity, chlorophyll, oxygen or current).');
  const warnings = table.ragged ? [`${table.ragged} ${table.ragged===1 ? 'row has' : 'rows have'} a different number of columns from the header; skipped.`] : [];
  const conv = {};
  const measured = zf ? [zf, ...vars] : vars;
  measured.forEach(f=>{
    const unit = table.units[col(f)], c = unitConverter(f, unit);
    if(c) conv[f] = c;
    else { conv[f] = x=>x; warnings.push(`Unit "${unit}" for ${f} not recognised; values used as they are.`); }
  });
  // Argo-style QC columns (NAME_QC next to a mapped column): flags 1, 2, 5 and 8 are kept; a column with no
  // flag at all (ERDDAP glider tables) is no QC
  const qcCol = {};
  measured.forEach(f=>{
    const name = table.names[col(f)], j = table.names.indexOf(name + '_qc');
    if(j >= 0 && table.rows.some(r=>/^\d$/.test(String(r[j]).trim()))) qcCol[f] = j;
  });
  if(Object.keys(qcCol).length) warnings.push('QC columns found: only flags 1, 2, 5 and 8 are kept.');
  const goodQc = c => c==='' || /^[1258]$/.test(String(c).trim());
  const groups = new Map(), tracks = new Map(), at = new Map();
  let badTime = 0;
  table.rows.forEach(r=>{
    const ms = parseTimeCell(r[col('time')]);
    if(!Number.isFinite(ms)){ badTime++; return; }
    const lat = +r[col('lat')], lon0 = +r[col('lon')], lon = lon0 > 180 ? lon0 - 360 : lon0;
    const id = col('id') >= 0 ? r[col('id')] : `${lat.toFixed(3)},${lon.toFixed(3)}`;
    const key = id + '|' + ms;
    if(!groups.has(key)){
      const g = {id: col('id') >= 0 ? id : `${opts.name || 'Profile'} ${groups.size+1}`, lat, lon,
                 time:new Date(ms).toISOString(), z:[], vars:Object.fromEntries(vars.map(f=>[f, []]))};
      const t = col('type') >= 0 ? typeFromText(r[col('type')]) : null;
      if(t) g.type = t;
      groups.set(key, g); at.set(g, ms);
      const tk = col('id') >= 0 ? id : '';      // the platform: without an id column the file is one
      if(!tracks.has(tk)) tracks.set(tk, []);
      tracks.get(tk).push(g);
    }
    const g = groups.get(key), zr = zf ? r[col(zf)] : '0';
    if(!isNumCell(zr) || (zf && qcCol[zf]!==undefined && !goodQc(r[qcCol[zf]]))) return;
    const zv = zf ? conv[zf](+zr) : 0;
    g.z.push(zf==='pres' ? depthFromPressure(zv, lat) : zv);
    vars.forEach(f=>{
      const c = r[col(f)], ok = isNumCell(c) && (qcCol[f]===undefined || goodQc(r[qcCol[f]]));
      g.vars[f].push(ok ? conv[f](+c) : NaN);
    });
  });
  if(badTime) warnings.push(`${badTime} ${badTime===1 ? 'row has' : 'rows have'} a time that is not ISO 8601 or YYYY-MM-DD hh:mm; skipped.`);
  if(!groups.size) throw new Error('No rows with a readable time, position and depth.');
  // a platform whose samples nearly all have their own time while the depth changes is a trajectory (glider):
  // its dives and climbs become the profiles, not thousands of one-sample ones
  const profiles = [];
  let dives = 0, dropped = 0;
  for(const [tk, gs] of tracks){
    let n = 0, z0 = Infinity, z1 = -Infinity;
    gs.forEach(g=>g.z.forEach(z=>{ n++; z0 = Math.min(z0, z); z1 = Math.max(z1, z); }));
    if(!zf || gs.length < 5 || gs.length < 0.5*n || z1 - z0 < 10){ gs.forEach(g=>profiles.push(g)); continue; }
    const s = {ms:[], lat:[], lon:[], z:[], vals:Object.fromEntries(vars.map(f=>[f, []]))};
    gs.sort((a, b)=>at.get(a) - at.get(b)).forEach(g=>g.z.forEach((z, k)=>{
      s.ms.push(at.get(g)); s.lat.push(g.lat); s.lon.push(g.lon); s.z.push(z); vars.forEach(f=>s.vals[f].push(g.vars[f][k]));
    }));
    const id = tk || opts.name || 'Trajectory', segs = [...diveSegments(s.z)], ds = [];
    segs.forEach(([seg, ks])=>{
      const d = diveProfile(ks, s);
      if(!d) return;
      const p = {id, lat:d.lat, lon:d.lon, time:new Date(d.ms).toISOString(), z:d.z, vars:d.vars, meta:{platform:id, cycle:seg}};
      if(gs[0].type) p.type = gs[0].type;
      ds.push(p);
    });
    if(!ds.length){ gs.forEach(g=>profiles.push(g)); continue; }      // no real dive: keep the samples as they are
    profiles.push(...ds); dives += ds.length; dropped += segs.length - ds.length;
  }
  if(dropped) warnings.push(`${dropped} ${dropped===1 ? 'dive or climb was' : 'dives and climbs were'} left out: under 5 samples or 10 m.`);
  const notes = zf==='pres' ? ['Depth from pressure (Saunders 1981).'] : surface ? ['No depth column: surface values (0 m).'] : [];
  if(dives) notes.push(`Trajectory split into ${dives===1 ? 'one dive or climb' : dives+' dives and climbs'} where the depth turns round; 1 m bins.`);
  const type = opts.type || typeFromText(table.header.join(' ') + ' ' + (opts.name || '')) || (dives ? 'glider' : 'other');
  return {name:opts.name || 'Imported CSV', format:'csv', type, profiles, warnings, notes};
}
