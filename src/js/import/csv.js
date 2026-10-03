/* ============================================================
   DELIMITED TEXT IMPORT (comma, tab or semicolon)
   parseDelimited(text) -> table; guessMapping(table) -> column mapping; tableToDataset(table,
   mapping) -> dataset (one row per sample, grouped into profiles by id + time).
   ============================================================ */
const CSV_FIELDS = ['lat', 'lon', 'time', 'depth', 'pres', 'temp', 'sal', 'chl', 'oxy', 'id', 'type'];
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
  id:['platform_number', 'platform', 'platform_code', 'wmo', 'float', 'float_id', 'station', 'station_id', 'cast', 'cast_id', 'id', 'site'],
  type:['instrument_type', 'platform_type', 'instrument', 'type']
};
const CSV_UNIT_TOKENS = /^(c|degc|°c|deg_c|degree_c|degrees_c|celsius|f|degf|°f|k|kelvin|m|meters|metres|ft|feet|km|dbar|decibar|db|bar|kpa|psu|pss|pss78|pss-78|g\/kg|1|mg\/m3|mg_m3|mg_m-3|mg m-3|ug\/l|µg\/l|ugl|umol\/kg|µmol\/kg|umol_kg|micromole\/kg|umol\/l|µmol\/l|ml\/l|ml_l|mg\/l|mg_l|degrees_north|degrees_east|degree_north|degree_east|deg|utc|iso8601)$/i;

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
    const n = lines.slice(0, 10).map(l=>splitLine(l, d).length);
    if(n[0] > best && n.every(k=>k===n[0])){ best = n[0]; delimiter = d; }
  });
  if(!delimiter) throw new Error('No consistent comma, tab or semicolon columns found.');
  let rows = lines.map(l=>splitLine(l, delimiter));
  const width = rows[0].length;
  rows = rows.filter(r=>r.length===width);
  // a header row has a text cell above a number
  const headerDetected = rows[0].some((c, j)=>!isNumCell(c) && isNumCell(rows[1][j]));
  const header = headerDetected ? rows.shift() : rows[0].map((_, j)=>`Column ${j+1}`);
  // a units row (ERDDAP): no numbers, unit-like words, and numbers below it
  let units = header.map(()=>'' );
  if(headerDetected && rows.length > 1 && !rows[0].some(isNumCell) &&
     rows[0].some((c, j)=>c!=='' && isNumCell(rows[1][j]) && CSV_UNIT_TOKENS.test(c.replace(/\s+/g, ' ')))){
    units = rows.shift();
  }
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
  return {delimiter, headerDetected, header, names, units, rows};
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
  if(mapping.depth===undefined && mapping.pres===undefined) missing.push('depth or pressure');
  if(!['temp', 'sal', 'chl', 'oxy'].some(f=>mapping[f]!==undefined)) missing.push('a measured variable');
  return {mapping, missing, needsMapping: !table.headerDetected || missing.length > 0};
}

// ISO 8601 or 'YYYY-MM-DD hh:mm[:ss]' (also with '/'); UTC unless an offset is given
function parseTimeCell(s){
  const m = String(s).trim().match(/^(\d{4})[-/](\d{1,2})[-/](\d{1,2})(?:[T ](\d{1,2}):(\d{2})(?::(\d{2}(?:\.\d+)?))?)?\s*(Z|UTC|GMT|[+-]\d{2}:?\d{2})?$/i);
  if(!m) return NaN;
  let ms = Date.UTC(+m[1], +m[2]-1, +m[3], +(m[4] || 0), +(m[5] || 0), 0) + Math.round(+(m[6] || 0)*1000);
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
    temp:{c:same, degc:same, '°c':same, deg_c:same, degree_c:same, degrees_c:same, celsius:same,
          f:x=>(x-32)*5/9, degf:x=>(x-32)*5/9, '°f':x=>(x-32)*5/9, k:x=>x-273.15, kelvin:x=>x-273.15},
    depth:{m:same, meters:same, metres:same, ft:x=>x*0.3048, feet:x=>x*0.3048, km:x=>x*1000},
    pres:{dbar:same, decibar:same, db:same, bar:x=>x*10, kpa:x=>x/10},
    sal:{psu:same, pss:same, pss78:same, 'pss-78':same, 1:same, 'g/kg':same},
    chl:{'mg/m3':same, mg_m3:same, 'mg_m-3':same, 'mg m-3':same, 'ug/l':same, 'µg/l':same, ugl:same},
    // µmol/L to µmol/kg with a seawater density of 1.025 kg/L; 1 mL/L O2 = 44.661 µmol/L
    oxy:{'umol/kg':same, 'µmol/kg':same, umol_kg:same, 'micromole/kg':same, 'umol/l':x=>x/1.025, 'µmol/l':x=>x/1.025,
         'ml/l':x=>x*44.661/1.025, ml_l:x=>x*44.661/1.025, 'mg/l':x=>x*31.252/1.025, mg_l:x=>x*31.252/1.025}
  }[field];
  if(!table) return same;
  return table[u] || null;
}
const TYPE_WORDS = {argo:'argo', float:'argo', 'bgc-argo':'argo', glider:'glider', ctd:'ctd', cast:'ctd', mooring:'mooring', buoy:'mooring'};
function typeFromText(s){
  const w = String(s || '').toLowerCase();
  for(const k of Object.keys(TYPE_WORDS)) if(w.includes(k)) return TYPE_WORDS[k];
  return null;
}

function tableToDataset(table, mapping, opts = {}){
  const col = f=>mapping[f]===undefined || mapping[f]===null || mapping[f]==='' ? -1 : +mapping[f];
  ['lat', 'lon', 'time'].forEach(f=>{ if(col(f) < 0) throw new Error(`Choose the column that holds ${f==='lat' ? 'latitude' : f==='lon' ? 'longitude' : 'time'}.`); });
  const zf = col('depth') >= 0 ? 'depth' : col('pres') >= 0 ? 'pres' : null;
  if(!zf) throw new Error('Choose the column that holds depth or pressure.');
  const vars = ['temp', 'sal', 'chl', 'oxy'].filter(f=>col(f) >= 0);
  if(!vars.length) throw new Error('Choose at least one measured column (temperature, salinity, chlorophyll or oxygen).');
  const warnings = [], conv = {};
  [zf, ...vars].forEach(f=>{
    const unit = table.units[col(f)], c = unitConverter(f, unit);
    if(c) conv[f] = c;
    else { conv[f] = x=>x; warnings.push(`Unit "${unit}" for ${f} not recognised; values used as they are.`); }
  });
  // Argo-style QC columns (NAME_QC next to a mapped column): flags 1, 2, 5 and 8 are kept
  const qcCol = {};
  [zf, ...vars].forEach(f=>{
    const name = table.names[col(f)], j = table.names.indexOf(name + '_qc');
    if(j >= 0) qcCol[f] = j;
  });
  if(Object.keys(qcCol).length) warnings.push('QC columns found: only flags 1, 2, 5 and 8 are kept.');
  const goodQc = c => c==='' || /^[1258]$/.test(String(c).trim());
  const groups = new Map();
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
      groups.set(key, g);
    }
    const g = groups.get(key), zr = r[col(zf)];
    if(!isNumCell(zr) || (qcCol[zf]!==undefined && !goodQc(r[qcCol[zf]]))) return;
    const zv = conv[zf](+zr);
    g.z.push(zf==='pres' ? depthFromPressure(zv, lat) : zv);
    vars.forEach(f=>{
      const c = r[col(f)], ok = isNumCell(c) && (qcCol[f]===undefined || goodQc(r[qcCol[f]]));
      g.vars[f].push(ok ? conv[f](+c) : NaN);
    });
  });
  if(badTime) warnings.push(`${badTime} ${badTime===1 ? 'row has' : 'rows have'} a time that is not ISO 8601 or YYYY-MM-DD hh:mm; skipped.`);
  if(!groups.size) throw new Error('No rows with a readable time, position and depth.');
  const type = opts.type || typeFromText(table.header.join(' ') + ' ' + (opts.name || '')) || 'other';
  return {name:opts.name || 'Imported CSV', format:'csv', type, profiles:[...groups.values()], warnings,
          notes: zf==='pres' ? ['Depth from pressure (Saunders 1981).'] : []};
}
