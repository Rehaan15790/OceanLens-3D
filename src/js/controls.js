/* ============================================================
   UI WIRING
   Controls call actions. The actions below (and in timeline.js, data-source.js, panels.js and
   tour.js) are the only writers of `state`; each ends in requestUpdate(), which also runs
   syncControls() to write the state back into every control, aria states included.
   ============================================================ */
const MODES = ['surface', 'layers', 'slice', 'iso'];
const OVERLAYS = {swArgo:'showArgo', swMisfit:'colorMisfit', swGlider:'showGlider', swTrack:'showTrack',
                  swFlow:'showFlow', swGrid:'showGrid'};

function isoMode(){ return state.variable!=='tchp' && state.mode==='iso'; }
function isoLabel(){
  const meta = VAR_META[state.variable], im = ISO_META[state.variable];
  return state.variable==='temp' ? `D${+state.isoVal.toFixed(im.dp)} depth`
    : `${state.isoVal.toFixed(im.dp)} ${meta.unit} depth`;
}
// the view on screen: 'surface' | 'layers' | 'slice' | 'iso' | 'tchp'
function currentView(){ return state.variable==='tchp' ? 'tchp' : state.mode; }
// the level the header statistics describe: the slice depth, otherwise the surface
function readoutDepth(){ return state.mode==='slice' ? DEPTHS[state.depthIdx] : 0; }

/* ---- header readout: the surface or the slice, never the whole column ---- */
function updateReadout(){
  const meta = VAR_META[state.variable], s = volumeStats;
  if(isoMode()){
    const im = ISO_META[state.variable];
    ui.roVar.textContent = `Depth of ${fmt.dp(state.isoVal, im.dp)} ${meta.unit}`;
    const any = Number.isFinite(s.min);
    ui.roRange.textContent = any ? `Range ${fmt.int(s.min)}–${fmt.int(s.max)} m` : 'No crossing';
    ui.roMean.textContent = any ? `Mean ${fmt.int(s.mean)} m` : '';
    return;
  }
  const n = fmt.decimals(state.variable);
  ui.roVar.textContent = state.variable==='tchp' ? meta.label : `${meta.label} at ${fmt.depthLabel(readoutDepth())}`;
  ui.roRange.textContent = Number.isFinite(s.min) ? `Range ${fmt.dp(s.min, n)}–${fmt.dp(s.max, n)} ${meta.unit}` : 'No data';
  ui.roMean.textContent = Number.isFinite(s.mean) ? `Mean ${fmt.dp(s.mean, n)} ${meta.unit}` : '';
}

/* ---- colour scale: what the key describes right now ---- */
function colorbarSpec(){
  const view = currentView(), meta = VAR_META[state.variable];
  if(view==='iso' && isoInfo){
    return {palette:'depth', min:isoInfo.lo, max:isoInfo.hi, unit:'m', label:isoLabel(), view,
            depth:null, isoValue:state.isoVal, editable:false};
  }
  return {palette:state.palette, min:state.cbMin, max:state.cbMax, unit:meta.unit, label:meta.label, view,
          depth: view==='tchp' ? null : readoutDepth(), isoValue:null, editable:true};
}
function updateColorbarUI(){
  const spec = colorbarSpec(), meta = VAR_META[state.variable];
  ui.cbMin.value = spec.min; ui.cbMax.value = spec.max;
  ui.cbMin.disabled = ui.cbMax.disabled = ui.paletteSel.disabled = !spec.editable;
  ui.paletteSel.value = spec.palette;
  ui.cbReset.hidden = !spec.editable ||
    (state.cbMin===meta.defMin && state.cbMax===meta.defMax && state.palette===meta.palette);
}

/* ---- actions ---- */
function resetRange(){
  const m = VAR_META[state.variable];
  state.cbMin = m.defMin; state.cbMax = m.defMax; state.palette = m.palette;
}
function setVariable(v){
  if(!VAR_META[v]) return;
  state.variable = v;
  resetRange();
  if(ISO_META[v]) state.isoVal = ISO_META[v].def;
  requestUpdate('full');
}
function setMode(m){
  if(!MODES.includes(m)) return;
  state.mode = m;
  requestUpdate('full');
}
function setDepth(i){
  state.depthIdx = Math.max(0, Math.min(DEPTHS.length-1, Math.round(i)));
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
  if(!(Number.isFinite(min) && Number.isFinite(max) && min < max)) return false;
  state.cbMin = min; state.cbMax = max;
  requestUpdate('full');
  return true;
}
function setPalette(name){
  if(typeof name !== 'string' || !name) return;
  state.palette = name;
  requestUpdate('full');
}
function setOverlay(key, on){
  if(!Object.values(OVERLAYS).includes(key)) return;
  state[key] = !!on;
  if(key==='colorMisfit' && on) state.showArgo = true;   // misfit colours need the floats on
  requestUpdate(key==='colorMisfit' || key==='showTrack' ? 'full' : 'overlay');
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
function syncControls(){
  const tchp = state.variable==='tchp', src = dataSource();
  ui.varPills.querySelectorAll('.pill').forEach(b=>setPressed(b, b.dataset.v===state.variable));
  ui.modePills.querySelectorAll('.pill').forEach(b=>setPressed(b, b.dataset.m===state.mode));
  ui.modeGrp.hidden = tchp;
  ui.tchpNote.hidden = !tchp;
  ui.depthGrp.hidden = tchp || state.mode!=='slice';
  ui.isoGrp.hidden = tchp || state.mode!=='iso';
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
  ui.swGlider.disabled = !src.hasGliders;
  ui.rowGlider.title = src.hasGliders ? '' : 'No glider data in this data set';
  updateColorbarUI();
  ui.dataToggle.querySelectorAll('button').forEach(b=>setPressed(b, b.dataset.mode===dataMode));
  ui.secTrackBtn.textContent = src.section.trackPreset;
  ui.secTrackBtn.disabled = currentTrack().length===0;
  syncTimeline();
}

/* ---- controls -> actions ---- */
ui.varPills.querySelectorAll('.pill').forEach(btn=>btn.addEventListener('click', ()=>setVariable(btn.dataset.v)));
ui.modePills.querySelectorAll('.pill').forEach(btn=>btn.addEventListener('click', ()=>setMode(btn.dataset.m)));
ui.isoPresets.querySelectorAll('.preset').forEach(b=>b.addEventListener('click', ()=>setIso(parseFloat(b.dataset.iso))));
ui.depthSlider.addEventListener('input', e=>setDepth(parseInt(e.target.value, 10)));
ui.isoSlider.addEventListener('input', e=>setIso(parseFloat(e.target.value)));
ui.opSlider.addEventListener('input', e=>setOpacity(parseInt(e.target.value, 10)/100));
ui.exSlider.addEventListener('input', e=>setExaggeration(parseInt(e.target.value, 10)/10));
ui.cbMin.addEventListener('change', e=>{ if(!setRange(parseFloat(e.target.value), state.cbMax)) e.target.value = state.cbMin; });
ui.cbMax.addEventListener('change', e=>{ if(!setRange(state.cbMin, parseFloat(e.target.value))) e.target.value = state.cbMax; });
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
