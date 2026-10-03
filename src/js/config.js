/* Configuration: the embedded snapshot, grid constants, variable metadata and the UI state. */
const REAL_DATA = JSON.parse(document.getElementById('snapshot').textContent);

// The snapshot must be the layout this build reads; if not, say so on the splash instead of
// drawing a misread ocean.
(function checkSnapshot(d){
  const m = d.meta, bad = [];
  const bytes = s => typeof s === 'string' ? s.length*3/4 - (s.endsWith('==') ? 2 : s.endsWith('=') ? 1 : 0) : -1;
  if(d.schema !== 1) bad.push(`schema ${d.schema ?? 'missing'}, this build reads schema 1`);
  if(!(m.step > 0) || !(m.nlat > 1) || !(m.nlon > 1) || !m.times.length) bad.push('grid size, step or times missing');
  if(!m.depths.every((z, i)=>i===0 || z > m.depths[i-1])) bad.push('depths do not increase');
  if(bytes(d.model) !== m.times.length*m.vars.length*m.depths.length*m.nlat*m.nlon*2) bad.push('model size does not match the grid');
  if(bytes(d.alt) !== d.altGrid.nlat*d.altGrid.nlon*2) bad.push('relief size does not match its grid');
  ['T', 'S', 'C'].filter(k=>k in d.argo).forEach(k=>{
    if(bytes(d.argo[k]) !== d.argo.profiles.length*d.argo.levels.length*2) bad.push(`argo.${k} size does not match the profiles`);
  });
  if(bad.length){
    const err = new Error('The embedded data does not match this version of the app: ' + bad.join('; ') + '.');
    window.__ol?._fail(err, 'data', {schema: d.schema});
    throw err;
  }
})(REAL_DATA);

// the data source on screen: 'real' (the embedded snapshot) or 'synth' (the analytic demo ocean)
let dataMode = 'real';

// the model grid comes from the snapshot; the synthetic ocean is sampled on the same grid
const META = REAL_DATA.meta;
const LAT_N = META.nlat, LON_N = META.nlon;
const LAT0 = META.lat0, LON0 = META.lon0;
const LAT1 = LAT0 + (LAT_N-1)*META.step, LON1 = LON0 + (LON_N-1)*META.step;
const DEPTHS = META.depths.slice();
const NT = META.times.length;
const SCALE_XZ = 0.55;         // degrees -> scene units
// 1000 m (deepest data layer) maps to 2 units per unit of exaggeration,
// so at the default 3x it fills the 6-unit domain box exactly
const DEPTH_SCALE = 0.002;

function lerp(a,b,t){return a+(b-a)*t;}

function b64Int16(s){
  const bin = atob(s), u8 = new Uint8Array(bin.length);
  for(let i=0;i<bin.length;i++) u8[i] = bin.charCodeAt(i);
  return new Int16Array(u8.buffer);
}

// Variable metadata comes from the registry (plugins.js): VAR_META[key] is the registered spec, with
// defMin/defMax set for the current data source; ISO_META[key] exists only for variables with an iso slider.
const VAR_META = {};
const ISO_META = {};
// fallback colour ranges and iso-slider limits for the current data source; the colour defaults per view come from facts
function applyModeDefaults(){
  Object.entries(VAR_META).forEach(([k, v])=>{
    const r = v.ranges[dataMode] || v.ranges.real || [0, 1];
    v.defMin = r[0]; v.defMax = r[1];
    if(!v.iso) return;
    ISO_META[k] = {min: v.iso.min ?? r[0], max: v.iso.max ?? r[1], step: v.iso.step, dp: v.iso.dp,
                   def: typeof v.iso.def === 'function' ? v.iso.def(r) : v.iso.def};
  });
}

/* ============================================================
   STATE
   Only the actions (controls.js, timeline.js, data-source.js, panels.js, tour.js) write it.
   ============================================================ */
const state = {
  // 'surface': opaque sea surface with faint deeper layers; 'layers': all depth levels;
  // 'slice': one depth; 'iso': the depth of a value; 'iso3d': the isosurface of a value
  variable:'temp', mode:'surface', depthIdx:4, isoVal:26, opacity:0.25,
  exagg:3.0, showArgo:true, showGlider:true, showGrid:true, showFlow:true, colorMisfit:false, showTrack:true,
  cbMin:0, cbMax:1, palette:'temperature', scale:'linear', customRange:false,   // boot runs resetRange()
  showOther:true, showAllImported:false,   // imported non-Argo/glider instruments; imported data outside ±1.5 days
  time:0, playing:false, sectionDraw:false,
  panel:'empty'   // which right-hand panel is open: 'empty' | 'profile' | 'track' | 'section'
};

// every element the architect-owned scripts drive; a missing one stops start-up with its id named
function bindIds(ids){
  const out = {}, missing = [];
  ids.forEach(id=>{ const el = document.getElementById(id); if(el) out[id] = el; else missing.push(id); });
  if(missing.length) throw new Error('The page is missing elements this script needs: ' + missing.join(', '));
  return out;
}
const ui = bindIds([
  'splash', 'enterBtn', 'roVar', 'roRange', 'roMean', 'dataToggle', 'presentBtn', 'aboutBtn', 'aboutOverlay',
  'aboutClose', 'helpBtn', 'helpOverlay', 'helpClose', 'varPills', 'modeGrp', 'modePills', 'tchpNote', 'depthGrp',
  'depthVal', 'depthSlider', 'isoGrp', 'isoVal', 'isoSlider', 'isoPresets', 'swArgo', 'swMisfit', 'swGlider',
  'rowGlider', 'swTrack', 'swFlow', 'swGrid', 'opVal', 'opSlider', 'exVal', 'exSlider', 'paletteSel', 'cbMin', 'cbMax',
  'cbReset', 'secTrackBtn', 'sectionMenu', 'tourStrip', 'tourStep', 'tourText', 'tourBack',
  'tourNext', 'tourAuto', 'tourStop', 'tourPaused', 'rightEmpty', 'valPanel', 'profile', 'profName', 'profKind',
  'closeProf', 'profMeta', 'qcBadge', 'profDepthVal', 'profDepth', 'derivGrid', 'sectionPanel', 'trackPanel',
  'trackName', 'trackKind', 'closeTrack', 'trackMeta', 'trackChart', 'trackTable', 'playBtn', 'timeline', 'timeNow'
]);
