/* Configuration: the embedded snapshot, grid constants, variable metadata and the UI state. */
const REAL_DATA = JSON.parse(document.getElementById('snapshot').textContent);
const HAS_REAL = typeof REAL_DATA !== 'undefined';
let dataMode = HAS_REAL ? 'real' : 'synth';

// 0.5° grid: fine enough that land/seabed masking and isosurface gaps follow the coast
const LAT_N = 33, LON_N = 63;
const LAT0 = 6, LAT1 = 22, LON0 = 61, LON1 = 92;
const DEPTHS = [0, 20, 50, 75, 100, 150, 200, 300, 500, 750, 1000];
const NT = 8;
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
  temp:{label:'Temperature', unit:'°C', defMin:5, defMax:32, palette:'thermal'},
  sal: {label:'Salinity', unit:'PSU', defMin:32.4, defMax:36.6, palette:'haline'},
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
   ============================================================ */
const state = {
  variable:'temp', mode:'layers', depthIdx:4, isoVal:ISO_META.temp.def, opacity:0.55,
  exagg:3.0, showArgo:true, showGlider:true, showGrid:true, showFlow:true, colorMisfit:false,
  cbMin:VAR_META.temp.defMin, cbMax:VAR_META.temp.defMax, palette:'thermal',
  time:0, playing:false, sectionDraw:false
};
