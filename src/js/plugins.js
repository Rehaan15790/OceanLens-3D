/* ============================================================
   PLUGIN API: window.OceanLens
   Registries for variables, derived products, instrument types and import formats, and the
   datasets added at run time. The built-in variables and the Argo and glider types below are
   registered through the same calls a plugin makes. The test surface (window.__ol) is separate.
   ============================================================ */
const INSTRUMENT_TYPES = {};
const IMPORT_FORMATS = {};
const DATASETS = new Map();          // id -> {dataset, insts}
let importedInsts = [];              // the instruments of every added dataset
let datasetRev = 0;                  // bumps on every add/remove, so the markers rebuild
let datasetSeq = 0;
const pluginListeners = {};
const OBS_ARRAYS = {temp:'T', sal:'S', chl:'C', oxy:'O'};   // the short names ocean.js reads

function pluginEmit(name, detail){
  (pluginListeners[name] || []).slice().forEach(fn=>{ try { fn(detail); } catch(err){ console.error(err); } });
}
function pluginKey(k, what){
  if(typeof k !== 'string' || !/^[a-z][a-z0-9_-]{0,31}$/i.test(k)) throw new Error(`${what} key must be a short identifier (letters, digits, _ or -): ${k}`);
}
// a registration after start-up redraws; during page load main.js has not run yet
function pluginChanged(){ if(typeof requestUpdate === 'function') requestUpdate('full'); }

/* ---- variables and products ---- */
function registerVariable(key, spec){
  pluginKey(key, 'Variable');
  if(!spec || (typeof spec.sample !== 'function' && typeof spec.grid !== 'function')) throw new Error(`Variable ${key} needs a sample(lat, lon, depth, t) function.`);
  const range = spec.range || [0, 1];
  const v = Object.assign({label:key, unit:'', palette:'speed', scale:'linear', dims:3, listed:true, product:false,
                           ranges:{real:range, synth:range}}, spec, {key});
  v.model = Object.assign({real:true, synth:true}, spec.model);
  if(typeof v.sample !== 'function') v.sample = (lat, lon)=>NaN;
  if(!PALETTES[v.palette]) v.palette = 'speed';
  if(v.scale !== 'log') v.scale = 'linear';
  VAR_META[key] = v;
  if(!v.iso) delete ISO_META[key];
  field2dCache.clear();
  applyModeDefaults();
  pluginEmit('variable', {key});
  pluginChanged();
  return v;
}
function registerProduct(key, spec){
  const v = registerVariable(key, Object.assign({dims:2}, spec, {product:true}));
  pluginEmit('product', {key});
  return v;
}
// false when the current data source has no model field for the variable (chlorophyll in real mode)
function hasModel(key, mode = dataMode){ const v = VAR_META[key]; return !!v && v.model[mode] !== false; }
function is2D(key){ return !!VAR_META[key] && VAR_META[key].dims === 2; }
// one value per map point (gridPts order, null on land) for a 2D variable
const field2dCache = new Map();
function field2dGrid(key, t){
  const v = VAR_META[key], ck = key+'|'+dataMode+'|'+t+'|'+datasetRev+'|'+state.showAllImported;
  if(!v || v.dims !== 2) return null;
  if(!field2dCache.has(ck)){
    const g = v.grid ? v.grid(t) : gridPts.map((p, i)=>{
      if(gridLand[i]) return null;
      const x = v.sample(p.lat, p.lon, 0, t);
      return Number.isFinite(x) ? x : null;
    });
    field2dCache.set(ck, g);
  }
  return field2dCache.get(ck);
}

/* ---- instrument types ---- */
function registerInstrument(type, spec){
  pluginKey(type, 'Instrument');
  const label = (spec && spec.label) || type;
  INSTRUMENT_TYPES[type] = Object.assign({label, marker:'cross', variables:['temp', 'sal'], track:false, stem:true,
                                          kindLabel:()=>label}, spec, {type});
  pluginEmit('instrument', {type});
  if(importedInsts.length) pluginChanged();
  return INSTRUMENT_TYPES[type];
}
function instrumentType(kind){ return INSTRUMENT_TYPES[kind] || INSTRUMENT_TYPES.other; }
function instrumentVisible(inst){
  return inst.kind==='argo' ? state.showArgo : inst.kind==='glider' ? state.showGlider : state.showOther;
}

/* ---- import formats (readers live in import/) ---- */
function registerFormat(id, spec){
  pluginKey(id, 'Format');
  if(!spec || typeof spec.detect !== 'function' || typeof spec.read !== 'function') throw new Error(`Format ${id} needs detect() and read().`);
  IMPORT_FORMATS[id] = Object.assign({label:id, kind:'netcdf'}, spec, {id});
  pluginEmit('format', {id});
}

/* ---- datasets: profiles become instruments that behave like Argo ---- */
// Saunders (1981) inverse: depth (m) from pressure (dbar), within 0.1 m of UNESCO 1983 to 1000 m
function depthFromPressure(p, lat){
  const s = Math.sin(lat*Math.PI/180), c1 = (5.92 + 5.25*s*s)*1e-3;
  return (1-c1)*p - 2.21e-6*p*p;
}
const asNum = v => v===null || v===undefined || v==='' ? NaN : +v;
// text from a file shown in the UI: no control characters, bounded length
const cleanText = (v, max) => String(v).replace(/[\u0000-\u001f\u007f-\u009f]/g, ' ').trim().slice(0, max);
const MAX_PROFILES = 20000;
function nearestAnalysis(ms){
  let best = 0, bestD = Infinity;
  META.times.forEach((iso, t)=>{ const d = Math.abs(Date.parse(iso) - ms); if(d < bestD){ bestD = d; best = t; } });
  return best;
}
// one profile of a dataset -> an instrument, or a short reason it was skipped
function datasetInstrument(raw, ds, n){
  const type0 = raw.type || ds.type || 'other';
  const p = INSTRUMENT_TYPES[type0] && INSTRUMENT_TYPES[type0].readProfile ? INSTRUMENT_TYPES[type0].readProfile(raw, ds) : raw;
  if(!p) return 'not a profile';
  const lat = asNum(p.lat), lon = asNum(p.lon), ms = Date.parse(p.time);
  if(!Number.isFinite(lat) || !Number.isFinite(lon)) return 'no position';
  if(!Number.isFinite(ms)) return 'no valid time';
  if(lat < LAT0 || lat > LAT1 || lon < LON0 || lon > LON1) return 'outside the map';
  let z = p.z || p.depth, fromPres = false;
  if(!z && p.pres){ z = p.pres; fromPres = true; }
  if(!Array.isArray(z) && !ArrayBuffer.isView(z)) return 'no depths';
  const src = p.vars || p, keys = [...new Set(Object.keys(OBS_ARRAYS).concat(Object.keys(VAR_META)))];
  // levels with a finite depth, sorted shallow to deep
  const order = Array.from(z, (d, k)=>[fromPres ? depthFromPressure(asNum(d), lat) : asNum(d), k])
    .filter(([d])=>Number.isFinite(d) && d >= 0).sort((a, b)=>a[0]-b[0]);
  const levels = order.map(([d])=>+d.toFixed(2)), vars = {};
  keys.forEach(k=>{
    const a = src[k];
    if(a && typeof a !== 'string' && a.length !== undefined) vars[k] = order.map(([, i])=>asNum(a[i]));
  });
  const nan = ()=>levels.map(()=>NaN);
  const T = vars.temp || nan(), S = vars.sal || nan(), C = vars.chl || nan(), O = vars.oxy || nan();
  const has = levels.map((_, k)=>Object.values(vars).some(a=>Number.isFinite(a[k])));
  const first = has.indexOf(true), last = has.lastIndexOf(true);
  if(first < 0) return 'no values';
  const D = T.map((t, k)=>Number.isFinite(t) && Number.isFinite(S[k]) ? sigmaT(S[k], t) : NaN);
  const missing = new Uint8Array(levels.length);
  for(let k=first; k<=last; k++) if(!has[k]) missing[k] = 1;
  const kind = INSTRUMENT_TYPES[p.type || type0] ? (p.type || type0) : 'other', meta = p.meta || {};
  const snap = nearestAnalysis(ms);
  const inst = {id:cleanText(p.id ?? `${ds.name} ${n+1}`, 64) || `${ds.name} ${n+1}`, key:`${ds.id}:${n}`, kind, imported:true, dataset:ds.id,
    datasetName:ds.name, real:true, snap, gapH:(ms - Date.parse(META.times[snap]))/3.6e6, ms,
    time:new Date(ms).toISOString().slice(0, 16)+'Z', lat, lon, levels, maxDepth:levels[last],
    bgc:C.some(Number.isFinite), hasOxy:O.some(Number.isFinite), salOk:S.some(Number.isFinite),
    mode:meta.mode, cycle:meta.cycle ?? '', meta,
    obs:{z:levels, T, S, C, O, D, flag:inversionFlags(D), missing, vars}};
  inst.userData = {type:kind, data:{id:inst.id, cycle:inst.cycle, inst}};
  return inst;
}
function datasetSummary(id){
  const e = DATASETS.get(id);
  if(!e) return null;
  const d = e.dataset, types = {};
  let t0 = Infinity, t1 = -Infinity;
  e.insts.forEach(i=>{ types[i.kind] = (types[i.kind] || 0) + 1; if(i.ms < t0) t0 = i.ms; if(i.ms > t1) t1 = i.ms; });
  return {id, name:d.name, source:d.source, format:d.format, type:d.type, profiles:e.insts.length, skipped:e.skipped,
          start:new Date(t0).toISOString(), end:new Date(t1).toISOString(), types,
          notes:d.notes || []};
}
function datasetsChanged(){
  importedInsts = [].concat(...[...DATASETS.values()].map(e=>e.insts));
  datasetRev++;
  pluginChanged();
}
function addDataset(ds){
  if(!ds || !Array.isArray(ds.profiles)) throw new Error('A dataset needs a list of profiles.');
  const id = String(ds.id || `data-${++datasetSeq}`);
  const d = Object.assign({source:'import', type:'other'}, ds, {id});
  d.name = cleanText(ds.name || id, 120) || id;
  const insts = [], why = {};
  d.profiles.forEach((p, n)=>{
    const r = insts.length >= MAX_PROFILES ? 'more than the 20 000 a dataset can hold'
      : p && typeof p === 'object' ? datasetInstrument(p, d, n) : 'not a profile';
    if(typeof r === 'string') why[r] = (why[r] || 0) + 1; else insts.push(r);
  });
  const warnings = Object.entries(why).map(([r, k])=>`${k} ${k===1 ? 'profile' : 'profiles'} skipped: ${r}`);
  if(why['outside the map']) warnings.push(`The map covers ${LAT0}–${LAT1}°N, ${LON0}–${LON1}°E.`);
  if(!insts.length) throw new Error(d.profiles.length ? `None of the ${d.profiles.length} profiles can be shown. ${warnings.join('. ')}.` : 'The file has no profiles.');
  if(DATASETS.has(id)) removeDataset(id, true);
  const skipped = d.profiles.length - insts.length;
  DATASETS.set(id, {dataset:d, insts, skipped});
  datasetsChanged();
  pluginEmit('dataset', {action:'add', dataset:datasetSummary(id)});
  return {id, name:d.name, profiles:insts.length, skipped, warnings};
}
function removeDataset(id, quiet){
  const e = DATASETS.get(id);
  if(!e) return false;
  const summary = datasetSummary(id);
  DATASETS.delete(id);
  if(typeof profileState !== 'undefined' && profileState.inst && profileState.inst.dataset===id) closeSidePanels();
  if(!quiet){ datasetsChanged(); pluginEmit('dataset', {action:'remove', dataset:summary}); }
  return true;
}
// imported profiles within ±1.5 days of analysis t (all of them with "show all"); gridded types
// (spec.gridded, e.g. HF radar cells) are fields, not profiles: products read them, markers do not
const IMPORT_WINDOW_MS = 1.5*864e5;
function importedNow(t){
  if(!importedInsts.length) return [];
  const at = Date.parse(META.times[t]);
  return importedInsts.filter(i=>!instrumentType(i.kind).gridded && (state.showAllImported || Math.abs(i.ms - at) <= IMPORT_WINDOW_MS));
}

const OceanLens = window.OceanLens = {
  version:1,
  registerVariable, registerProduct, registerInstrument, registerFormat, addDataset, removeDataset,
  datasets: ()=>[...DATASETS.keys()].map(datasetSummary),
  dataset: datasetSummary,
  variable: k=>VAR_META[k] || null, variables: ()=>Object.keys(VAR_META),
  instrument: t=>INSTRUMENT_TYPES[t] || null, instruments: ()=>Object.keys(INSTRUMENT_TYPES),
  formats: ()=>Object.keys(IMPORT_FORMATS),
  observations: type=>importedInsts.filter(i=>i.kind===type),   // every imported record of a type, all times
  on(name, fn){ (pluginListeners[name] = pluginListeners[name] || []).push(fn); return fn; },
  off(name, fn){ const l = pluginListeners[name]; if(l && l.includes(fn)) l.splice(l.indexOf(fn), 1); }
};

/* ---- built-ins, through the API ---- */
registerVariable('temp', {label:'Temperature', unit:'°C', palette:'temperature',
  sample:(lat, lon, z, t)=>fieldTemp(lat, lon, z, t), ranges:{real:[6, 32], synth:[5, 32]}, iso:{step:0.1, dp:1, def:26}});
registerVariable('sal', {label:'Salinity', unit:'PSU', palette:'salinity',
  sample:(lat, lon, z, t)=>fieldSal(lat, lon, z, t), ranges:{real:[32.6, 36.8], synth:[32.4, 36.6]}, iso:{step:0.05, dp:2, def:35}});
registerVariable('cur', {label:'Current speed', unit:'m/s', palette:'speed',
  sample:(lat, lon, z, t)=>currentSpeed(lat, lon, z, t), ranges:{real:[0, 1.0], synth:[0, 0.36]},
  iso:{min:0.02, step:0.01, dp:2, def:r=>+(r[1]*0.3).toFixed(2)}});
// 2D field derived from the temperature/salinity columns (tchpColumn); 0–200 matches operational TCHP maps
registerVariable('tchp', {label:'Cyclone heat potential', unit:'kJ/cm²', palette:'heat', dims:2,
  sample:(lat, lon, z, t)=>{ const q = modelTchp(lat, lon, t, bathyDepth(lat, lon)).q; return q===null ? NaN : q; },
  grid:t=>tchpGrid(t).map(c=>c && c.q!==null ? c.q : null), range:[0, 200]});
// HYCOM is a physics-only model: in real mode chlorophyll comes from BGC-Argo floats only
registerVariable('chl', {label:'Chlorophyll', unit:'mg/m³', palette:PALETTES.chlorophyll ? 'chlorophyll' : 'speed', scale:'log',
  sample:(lat, lon, z, t)=>fieldChl(lat, lon, z, t), model:{real:false}, ranges:{real:[0.02, 5], synth:[0.02, 1.5]},
  iso:{step:0.01, dp:2, def:0.2}});
registerVariable('oxy', {label:'Dissolved oxygen', unit:'µmol/kg', palette:'speed', listed:false,
  sample:()=>NaN, model:{real:false, synth:false}, range:[0, 250]});
state.isoVal = ISO_META.temp.def;

registerInstrument('argo', {label:'Argo float', marker:'diamond', variables:['temp', 'sal', 'chl', 'oxy'],
  kindLabel:inst=>inst.imported ? (inst.bgc ? 'BGC-Argo float' : 'Argo float') : dataSource().profile.kind(inst)});
registerInstrument('glider', {label:'Glider', marker:'circle', variables:['temp', 'sal', 'chl', 'oxy'], track:true, stem:false,
  kindLabel:(inst, wpIndex)=>inst.imported ? 'Glider profile' : dataSource().profile.kind(inst, wpIndex)});
registerInstrument('other', {label:'Other instrument', marker:'cross', variables:['temp', 'sal', 'chl', 'oxy'],
  kindLabel:()=>'Imported profile'});
