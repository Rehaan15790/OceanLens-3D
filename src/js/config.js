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

// defaults span the actual data range so nothing clips and no range is wasted
const VAR_META = {
  temp:{label:'Temperature', unit:'°C', defMin:5, defMax:32, palette:'temperature'},
  sal: {label:'Salinity', unit:'PSU', defMin:32.4, defMax:36.6, palette:'salinity'},
  cur: {label:'Current speed', unit:'m/s', defMin:0, defMax:0.36, palette:'speed'},
  // 2D field derived from the temperature/salinity columns — see tchpColumn()
  // 0–200 matches operational TCHP maps; the rare warm-eddy column above 200 saturates
  tchp:{label:'Cyclone heat potential', unit:'kJ/cm²', defMin:0, defMax:200, palette:'heat'}
};
// isosurface threshold slider, in the variable's own units
const ISO_META = {
  temp:{min:5, max:32, step:0.1, def:26, dp:1},
  sal: {min:32.4, max:36.6, step:0.05, def:35, dp:2},
  cur: {min:0.02, max:0.36, step:0.01, def:0.1, dp:2}
};
// ranges that depend on the data source (real ones: 0.5–99.5th percentiles of the snapshot)
const MODE_DEFAULTS = {
  synth:{temp:[5,32], sal:[32.4,36.6], cur:[0,0.36]},
  real: {temp:[6,32], sal:[32.6,36.8], cur:[0,1.0]}
};
function applyModeDefaults(){
  const d = MODE_DEFAULTS[dataMode];
  Object.keys(d).forEach(k=>{ VAR_META[k].defMin = d[k][0]; VAR_META[k].defMax = d[k][1]; });
  ISO_META.temp.min = d.temp[0]; ISO_META.temp.max = d.temp[1];
  ISO_META.sal.min = d.sal[0];   ISO_META.sal.max = d.sal[1];
  ISO_META.cur.max = d.cur[1];   ISO_META.cur.def = +(d.cur[1]*0.3).toFixed(2);
}
applyModeDefaults();

/* ============================================================
   STATE
   Only the actions (controls.js, timeline.js, data-source.js, panels.js, tour.js) write it.
   ============================================================ */
const state = {
  // 'surface': opaque sea surface with faint deeper layers; 'layers': all depth levels;
  // 'slice': one depth; 'iso': the depth of a value
  variable:'temp', mode:'surface', depthIdx:4, isoVal:ISO_META.temp.def, opacity:0.25,
  exagg:3.0, showArgo:true, showGlider:true, showGrid:true, showFlow:true, colorMisfit:false, showTrack:true,
  cbMin:VAR_META.temp.defMin, cbMax:VAR_META.temp.defMax, palette:'temperature',
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
