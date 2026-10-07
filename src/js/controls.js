/* ============================================================
   UI WIRING
   Controls call actions. The actions below (and in timeline.js, data-source.js, panels.js and
   tour.js) are the only writers of `state`; each ends in requestUpdate(), which also runs
   syncControls() to write the state back into every control, aria states included.
   ============================================================ */
const MODES = ['surface', 'layers', 'slice', 'iso', 'iso3d'];
const OVERLAYS = {swArgo:'showArgo', swMisfit:'colorMisfit', swGlider:'showGlider', swTrack:'showTrack',
                  swFlow:'showFlow', swGrid:'showGrid'};
// switches ux may add; wired only when present
const OPTIONAL_OVERLAYS = {swOther:'showOther', swAllImported:'showAllImported'};
const optUi = Object.fromEntries(['scaleToggle', ...Object.keys(OPTIONAL_OVERLAYS)].map(id=>[id, document.getElementById(id)]));
const ISO_MODES = ['iso', 'iso3d'];

function isoMode(){ return !is2D(state.variable) && ISO_MODES.includes(state.mode) && !!ISO_META[state.variable]; }
function isoLabel(){
  const meta = VAR_META[state.variable], im = ISO_META[state.variable];
  return state.variable==='temp' ? `D${+state.isoVal.toFixed(im.dp)} depth`
    : `${state.isoVal.toFixed(im.dp)}\u00a0${meta.unit} depth`;
}
// the view on screen: 'surface' | 'layers' | 'slice' | 'iso' | 'iso3d' | 'tchp' | 'field2d' (other 2D variables)
function currentView(){ return state.variable==='tchp' ? 'tchp' : is2D(state.variable) ? 'field2d' : state.mode; }
// the level the header statistics describe: the slice depth, otherwise the surface
function readoutDepth(){ return state.mode==='slice' ? DEPTHS[state.depthIdx] : 0; }

/* ---- header readout: the surface or the slice, never the whole column ---- */
function updateReadout(){
  const meta = VAR_META[state.variable], s = volumeStats;
  if(isoMode()){
    const im = ISO_META[state.variable];
    ui.roVar.textContent = `Depth of ${fmt.dp(state.isoVal, im.dp)} ${meta.unit}`;
    const any = Number.isFinite(s.min);
    ui.roRange.textContent = any ? `Range ${fmt.int(s.min)}–${fmt.int(s.max)} m`
      : hasModel(state.variable) ? 'No crossing' : 'No model field';
    ui.roMean.textContent = any ? `Mean ${fmt.int(s.mean)} m` : '';
    return fitReadout();
  }
  const n = fmt.decimals(state.variable);
  ui.roVar.textContent = is2D(state.variable) ? meta.label : `${meta.label} at ${fmt.depthLabel(readoutDepth())}` +
    (hasModel(state.variable) ? '' : ' (floats)');
  ui.roRange.textContent = Number.isFinite(s.min) ? `Range ${fmt.dp(s.min, n)}–${fmt.dp(s.max, n)} ${meta.unit}` : 'No data';
  ui.roMean.textContent = Number.isFinite(s.mean) ? `Mean ${fmt.dp(s.mean, n)} ${meta.unit}` : '';
  fitReadout();
}
// the mean gives way when the line would cut the variable's name (narrow screens, long names such as chlorophyll's)
function fitReadout(){
  ui.roMean.hidden = false;
  ui.roMean.hidden = ui.roVar.scrollWidth > ui.roVar.clientWidth;
}
window.addEventListener('resize', fitReadout);

/* ---- colour scale: what the key describes right now ---- */
function colorbarSpec(){
  const view = currentView(), meta = VAR_META[state.variable], iso = isoMode();
  // in both iso views, no surface (no crossing, no model field) gives an empty key: nothing on screen has a colour
  const any = !!(isoInfo && isoInfo.n);
  if(view==='iso' && iso){
    // the surface's own depth range, never the variable's ramp
    return {palette:'depth', min:any ? isoInfo.lo : NaN, max:any ? isoInfo.hi : NaN, scale:'linear', unit:'m', label:isoLabel(),
            view, depth:null, isoValue:state.isoVal, editable:false, empty:!any};
  }
  // an isosurface with nothing to draw has no colour scale to edit either
  const empty = view==='iso3d' && iso && !any;
  return {palette:state.palette, min:state.cbMin, max:state.cbMax, scale:state.scale, unit:meta.unit, label:meta.label,
          view, depth: view==='tchp' || view==='field2d' || view==='iso3d' ? null : readoutDepth(),
          isoValue: view==='iso3d' && iso ? state.isoVal : null, editable:!empty, empty};
}
function updateColorbarUI(){
  const spec = colorbarSpec(), meta = VAR_META[state.variable];
  // an empty key (no surface) leaves the fields blank, in both iso views
  const shown = v=>Number.isFinite(v) && !spec.empty ? v : '';
  ui.cbMin.value = shown(spec.min); ui.cbMax.value = shown(spec.max);
  ui.cbMin.disabled = ui.cbMax.disabled = ui.paletteSel.disabled = !spec.editable;
  ui.paletteSel.value = spec.palette;
  ui.cbReset.hidden = !spec.editable || (!state.customRange && state.palette===meta.palette && state.scale===meta.scale);
  if(optUi.scaleToggle) optUi.scaleToggle.querySelectorAll('[data-scale]').forEach(b=>{
    setPressed(b, b.dataset.scale===spec.scale);
    b.disabled = !spec.editable;
  });
}

/* ---- actions ---- */
// the default colour range of the view on screen (science's facts): the level shown for the
// surface and a slice, the whole column for all depth levels and the depth of a value, the heat
// range for TCHP
// variables without facts ranges (plugins, products) use the registry's range for the data source
function viewRange(){
  const r = facts()[dataMode].ranges[state.variable], meta = VAR_META[state.variable], own = [meta.defMin, meta.defMax];
  if(!r) return own;
  if(is2D(state.variable) || state.mode==='layers' || ISO_MODES.includes(state.mode)) return r.column || own;
  return (r.levels && r.levels[state.mode==='slice' ? state.depthIdx : 0]) || r.column || own;
}
// a log scale needs a positive minimum
function logSafeMin(){
  if(state.scale==='log' && !(state.cbMin > 0)) state.cbMin = state.cbMax > 0 ? +(state.cbMax/100).toPrecision(2) : 0.01;
}
// follow the view's default range unless the user has set their own
function applyViewRange(){
  if(state.customRange) return;
  [state.cbMin, state.cbMax] = viewRange();
  logSafeMin();
}
function resetRange(){
  state.customRange = false;
  state.palette = VAR_META[state.variable].palette;
  state.scale = VAR_META[state.variable].scale;
  applyViewRange();
}
function setVariable(v){
  if(!VAR_META[v]) return;
  state.variable = v;
  if(ISO_MODES.includes(state.mode) && !ISO_META[v]) state.mode = 'surface';
  resetRange();
  if(ISO_META[v]) state.isoVal = ISO_META[v].def;
  requestUpdate('full');
}
function setMode(m){
  if(!MODES.includes(m) || (ISO_MODES.includes(m) && !ISO_META[state.variable])) return;
  state.mode = m;
  applyViewRange();
  requestUpdate('full');
}
function setDepth(i){
  state.depthIdx = Math.max(0, Math.min(DEPTHS.length-1, Math.round(i)));
  applyViewRange();
  requestUpdate('full');
}
function setIso(v){
  const im = ISO_META[state.variable];
  if(!im || !Number.isFinite(v)) return;
  state.isoVal = Math.max(im.min, Math.min(im.max, v));
  requestUpdate('full');
}
function setOpacity(x){
  if(!Number.isFinite(x)) return;
  state.opacity = Math.max(0.05, Math.min(0.95, x));
  requestUpdate('opacity');
}
function setExaggeration(e){
  if(!Number.isFinite(e)) return;
  state.exagg = Math.max(1, Math.min(8, e));
  requestUpdate('depth');
}
// false (and nothing changes) unless min < max
function setRange(min, max){
  if(!(Number.isFinite(min) && Number.isFinite(max) && min < max) || (state.scale==='log' && min <= 0)) return false;
  state.cbMin = min; state.cbMax = max; state.customRange = true;
  requestUpdate('full');
  return true;
}
function setScale(s){
  if(s!=='linear' && s!=='log') return;
  state.scale = s;
  logSafeMin();
  requestUpdate('full');
}
function setPalette(name){
  if(typeof name !== 'string' || !name) return;
  state.palette = name;
  requestUpdate('full');
}
function setOverlay(key, on){
  if(!Object.values(OVERLAYS).includes(key) && !Object.values(OPTIONAL_OVERLAYS).includes(key)) return;
  state[key] = !!on;
  if(key==='colorMisfit' && on) state.showArgo = true;   // misfit colours need the floats on
  requestUpdate(['colorMisfit', 'showTrack', 'showAllImported'].includes(key) ? 'full' : 'overlay');
}
// the overlays that change visibility only
function applyOverlays(){
  applyMarkerVisibility();
  updateLegend();
  applyFlowVisibility();
  graticule.visible = state.showGrid; boxLines.visible = state.showGrid;
}

/* ---- state -> controls ---- */
function setPressed(el, on){
  el.classList.toggle('active', on);
  el.setAttribute('aria-pressed', on ? 'true' : 'false');
}
function syncIsoSlider(){
  const im = ISO_META[state.variable];
  if(!im) return;
  const s = ui.isoSlider;
  s.min = im.min; s.max = im.max; s.step = im.step; s.value = state.isoVal;
  ui.isoVal.textContent = `${fmt.dp(state.isoVal, im.dp)} ${VAR_META[state.variable].unit}`;
  ui.isoPresets.hidden = state.variable!=='temp';
  ui.isoPresets.querySelectorAll('.preset').forEach(b=>{
    setPressed(b, state.variable==='temp' && Math.abs(parseFloat(b.dataset.iso)-state.isoVal) < 1e-6);
  });
}
function setShowAllImported(on){ setOverlay('showAllImported', on); }
function syncControls(){
  const tchp = is2D(state.variable), src = dataSource();
  ui.varPills.querySelectorAll('.pill').forEach(b=>setPressed(b, b.dataset.v===state.variable));
  ui.modePills.querySelectorAll('.pill').forEach(b=>{
    setPressed(b, b.dataset.m===state.mode);
    b.disabled = ISO_MODES.includes(b.dataset.m) && !ISO_META[state.variable];
  });
  ui.modeGrp.hidden = tchp;
  ui.tchpNote.hidden = !tchp;
  ui.depthGrp.hidden = tchp || state.mode!=='slice';
  ui.isoGrp.hidden = !isoMode();
  ui.depthSlider.max = DEPTHS.length-1;
  ui.depthSlider.value = state.depthIdx;
  ui.depthVal.textContent = fmt.depthLabel(DEPTHS[state.depthIdx]);
  syncIsoSlider();
  ui.opSlider.value = Math.round(state.opacity*100);
  ui.opVal.textContent = `${Math.round(state.opacity*100)}%`;
  ui.exSlider.value = Math.round(state.exagg*10);
  ui.exVal.textContent = state.exagg.toFixed(1);
  Object.entries(OVERLAYS).forEach(([id, key])=>{
    ui[id].classList.toggle('on', state[key]);
    ui[id].setAttribute('aria-checked', state[key] ? 'true' : 'false');
  });
  Object.entries(OPTIONAL_OVERLAYS).forEach(([id, key])=>{
    if(!optUi[id]) return;
    optUi[id].classList.toggle('on', state[key]);
    optUi[id].setAttribute('aria-checked', state[key] ? 'true' : 'false');
  });
  const gliders = src.hasGliders || (dataMode==='real' && importedInsts.some(i=>i.kind==='glider'));
  ui.swGlider.disabled = !gliders;
  ui.rowGlider.title = gliders ? '' : 'No glider data in this data set';
  updateColorbarUI();
  ui.dataToggle.querySelectorAll('button').forEach(b=>setPressed(b, b.dataset.mode===dataMode));
  ui.secTrackBtn.textContent = src.section.trackPreset;
  ui.secTrackBtn.disabled = currentTrack().length===0;
  syncTimeline();
}

/* ---- controls -> actions ---- */
// pills by delegation, so pills added later (runtime variables, new views) work too
ui.varPills.addEventListener('click', e=>{ const b = e.target.closest('.pill[data-v]'); if(b) setVariable(b.dataset.v); });
ui.modePills.addEventListener('click', e=>{ const b = e.target.closest('.pill[data-m]'); if(b) setMode(b.dataset.m); });
// a pill for every listed variable the page does not already have
function addVariablePills(){
  Object.values(VAR_META).forEach(v=>{
    if(!v.listed || ui.varPills.querySelector(`.pill[data-v="${v.key}"]`)) return;
    const b = document.createElement('button'), u = document.createElement('span');
    b.type = 'button'; b.className = 'pill'; b.dataset.v = v.key; b.setAttribute('aria-pressed', 'false');
    b.textContent = v.label + ' ';
    u.className = 'unit'; u.textContent = v.unit;
    b.appendChild(u);
    ui.varPills.appendChild(b);
  });
}
addVariablePills();
OceanLens.on('variable', addVariablePills);
if(optUi.scaleToggle) optUi.scaleToggle.addEventListener('click', e=>{ const b = e.target.closest('[data-scale]'); if(b) setScale(b.dataset.scale); });
Object.entries(OPTIONAL_OVERLAYS).forEach(([id, key])=>{ if(optUi[id]) optUi[id].addEventListener('click', ()=>setOverlay(key, !state[key])); });
ui.isoPresets.querySelectorAll('.preset').forEach(b=>b.addEventListener('click', ()=>setIso(parseFloat(b.dataset.iso))));
ui.depthSlider.addEventListener('input', e=>setDepth(parseInt(e.target.value, 10)));
ui.isoSlider.addEventListener('input', e=>setIso(parseFloat(e.target.value)));
ui.opSlider.addEventListener('input', e=>setOpacity(parseInt(e.target.value, 10)/100));
ui.exSlider.addEventListener('input', e=>setExaggeration(parseInt(e.target.value, 10)/10));
ui.cbMin.addEventListener('change', e=>{ if(!setRange(parseFloat(e.target.value), state.cbMax)) e.target.value = state.cbMin; });
ui.cbMax.addEventListener('change', e=>{ if(!setRange(state.cbMin, parseFloat(e.target.value))) e.target.value = state.cbMax; });
// one ramp option per palette; 'depth' belongs to the depth-of-a-value view and is never offered
Object.entries(PALETTES).forEach(([k, p])=>{ const o = new Option(p.label, k); o.hidden = k==='depth'; ui.paletteSel.appendChild(o); });
ui.paletteSel.addEventListener('change', e=>setPalette(e.target.value));
ui.cbReset.addEventListener('click', ()=>{ resetRange(); requestUpdate('full'); });
Object.entries(OVERLAYS).forEach(([id, key])=>ui[id].addEventListener('click', ()=>setOverlay(key, !state[key])));
// the section tools sit in a menu, which closes once a tool is chosen
ui.sectionMenu.querySelectorAll('.preset, #sectionDrawBtn').forEach(b=>b.addEventListener('click', ()=>{ ui.sectionMenu.open = false; }));

/* ---- about overlay ---- */
ui.aboutBtn.addEventListener('click', ()=>ui.aboutOverlay.classList.add('show'));
ui.aboutClose.addEventListener('click', ()=>ui.aboutOverlay.classList.remove('show'));
ui.aboutOverlay.addEventListener('click', e=>{ if(e.target===ui.aboutOverlay) ui.aboutOverlay.classList.remove('show'); });

/* ---- splash ---- */
ui.enterBtn.addEventListener('click', ()=>ui.splash.classList.add('hide'));
