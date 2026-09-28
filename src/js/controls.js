/* ============================================================
   UI WIRING
   ============================================================ */
function isoMode(){ return state.variable!=='tchp' && state.mode==='iso'; }
function isoLabel(){
  const meta = VAR_META[state.variable], im = ISO_META[state.variable];
  return state.variable==='temp' ? `D${+state.isoVal.toFixed(im.dp)} depth`
    : `${state.isoVal.toFixed(im.dp)} ${meta.unit} depth`;
}
function updateReadout(){
  const meta = VAR_META[state.variable], iso = isoMode();
  const unit = iso ? 'm' : meta.unit, dp = iso || state.variable==='tchp' ? 0 : 2;
  const f = v => Number.isFinite(v) ? v.toFixed(dp) : '—';
  document.getElementById('roVar').textContent = iso ? isoLabel() : meta.label;
  document.getElementById('roRange').textContent = Number.isFinite(volumeStats.min)
    ? `${f(volumeStats.min)} – ${f(volumeStats.max)} ${unit}` : 'no crossing';
  document.getElementById('roMean').textContent = Number.isFinite(volumeStats.mean) ? `${f(volumeStats.mean)} ${unit}` : '—';
}

// what the colour bar describes right now: the field, or (isosurface mode) the surface's depth
function colorbarSpec(){
  if(isoMode() && isoInfo) return {palette:'depth', min:isoInfo.lo, max:isoInfo.hi, unit:'m', editable:false};
  return {palette:state.palette, min:state.cbMin, max:state.cbMax, unit:VAR_META[state.variable].unit, editable:true};
}
function drawColorbarCanvas(){
  const c = document.getElementById('colorbarCanvas');
  c.width = c.clientWidth || 200;
  const ctx = c.getContext('2d'), spec = colorbarSpec();
  for(let x=0;x<c.width;x++){
    const col = paletteColor(spec.palette, x/c.width);
    ctx.fillStyle = `rgb(${col[0]*255},${col[1]*255},${col[2]*255})`;
    ctx.fillRect(x,0,1,c.height);
  }
}
function updateColorbarUI(){
  const spec = colorbarSpec(), dp = spec.unit==='m' || state.variable==='tchp' ? 0 : 1;
  document.getElementById('cbMinLbl').textContent = spec.min.toFixed(dp)+' '+spec.unit;
  document.getElementById('cbMaxLbl').textContent = spec.max.toFixed(dp)+' '+spec.unit;
  document.getElementById('paletteName').textContent = spec.editable ? state.palette : 'isosurface depth';
  const mn = document.getElementById('cbMin'), mx = document.getElementById('cbMax'), sel = document.getElementById('paletteSel');
  mn.value = spec.min; mx.value = spec.max;
  mn.disabled = mx.disabled = sel.disabled = !spec.editable;
  sel.value = spec.palette;
  drawColorbarCanvas();
}

document.querySelectorAll('#varPills .pill').forEach(btn=>{
  btn.addEventListener('click', ()=>{
    document.querySelectorAll('#varPills .pill').forEach(b=>b.classList.remove('active'));
    btn.classList.add('active');
    state.variable = btn.dataset.v;
    const meta = VAR_META[state.variable];
    state.cbMin = meta.defMin; state.cbMax = meta.defMax; state.palette = meta.palette;
    document.getElementById('cbMin').value = meta.defMin;
    document.getElementById('cbMax').value = meta.defMax;
    document.getElementById('paletteSel').value = meta.palette;
    if(ISO_META[state.variable]) state.isoVal = ISO_META[state.variable].def;
    syncIsoSlider();
    fullUpdate();
  });
});

function syncIsoSlider(){
  const im = ISO_META[state.variable];
  if(!im) return;
  const s = document.getElementById('isoSlider');
  s.min = im.min; s.max = im.max; s.step = im.step; s.value = state.isoVal;
  document.getElementById('isoVal').textContent = state.isoVal.toFixed(im.dp)+' '+VAR_META[state.variable].unit;
  document.getElementById('isoPresets').style.display = state.variable==='temp' ? 'flex' : 'none';
  document.querySelectorAll('#isoPresets .preset').forEach(b=>{
    b.classList.toggle('active', state.variable==='temp' && Math.abs(parseFloat(b.dataset.iso)-state.isoVal) < 1e-6);
  });
}
document.querySelectorAll('#isoPresets .preset').forEach(b=>{
  b.addEventListener('click', ()=>{ state.isoVal = parseFloat(b.dataset.iso); syncIsoSlider(); fullUpdate(); });
});

document.querySelectorAll('#modePills .pill').forEach(btn=>{
  btn.addEventListener('click', ()=>{
    document.querySelectorAll('#modePills .pill').forEach(b=>b.classList.remove('active'));
    btn.classList.add('active');
    state.mode = btn.dataset.m;
    document.getElementById('depthGrp').style.display = state.mode==='slice' ? 'block' : 'none';
    document.getElementById('isoGrp').style.display = state.mode==='iso' ? 'block' : 'none';
    fullUpdate();
  });
});

document.getElementById('depthSlider').addEventListener('input', e=>{
  state.depthIdx = parseInt(e.target.value);
  document.getElementById('depthVal').textContent = DEPTHS[state.depthIdx]+' m';
  fullUpdate();
});
document.getElementById('isoSlider').addEventListener('input', e=>{
  state.isoVal = parseFloat(e.target.value);
  syncIsoSlider();
  fullUpdate();
});
document.getElementById('opSlider').addEventListener('input', e=>{
  state.opacity = parseInt(e.target.value)/100;
  document.getElementById('opVal').textContent = e.target.value+'%';
  fullUpdate();
});
document.getElementById('exSlider').addEventListener('input', e=>{
  state.exagg = parseInt(e.target.value)/10;
  document.getElementById('exVal').textContent = state.exagg.toFixed(1)+'×';
  buildMarkers();
  applyMarkerVisibility();
  fullUpdate();
});
document.getElementById('cbMin').addEventListener('change', e=>{
  const v = parseFloat(e.target.value);
  if(!Number.isFinite(v)) { e.target.value = state.cbMin; return; }
  if(v >= state.cbMax){ e.target.value = state.cbMin; return; }  // keep min below max
  state.cbMin = v; fullUpdate();
});
document.getElementById('cbMax').addEventListener('change', e=>{
  const v = parseFloat(e.target.value);
  if(!Number.isFinite(v)) { e.target.value = state.cbMax; return; }
  if(v <= state.cbMin){ e.target.value = state.cbMax; return; }
  state.cbMax = v; fullUpdate();
});
document.getElementById('paletteSel').addEventListener('change', e=>{ state.palette = e.target.value; fullUpdate(); });

function wireSwitch(id, stateKey, onToggle){
  const el = document.getElementById(id);
  el.addEventListener('click', ()=>{
    state[stateKey] = !state[stateKey];
    el.classList.toggle('on', state[stateKey]);
    onToggle();
  });
}
wireSwitch('swArgo','showArgo', ()=>{ applyMarkerVisibility(); updateLegend(); });
wireSwitch('swGlider','showGlider', applyMarkerVisibility);
wireSwitch('swMisfit','colorMisfit', ()=>{ if(!state.showArgo) document.getElementById('swArgo').click(); fullUpdate(); });
wireSwitch('swFlow','showFlow', ()=>{ flowMesh.visible = state.showFlow && state.variable!=='tchp'; });
wireSwitch('swGrid','showGrid', ()=>{ graticule.visible = state.showGrid; boxLines.visible = state.showGrid; });

document.getElementById('cbMin').value = VAR_META.temp.defMin;
document.getElementById('cbMax').value = VAR_META.temp.defMax;

/* ---- about overlay ---- */
document.getElementById('aboutBtn').addEventListener('click', ()=>document.getElementById('aboutOverlay').classList.add('show'));
document.getElementById('aboutClose').addEventListener('click', ()=>document.getElementById('aboutOverlay').classList.remove('show'));
document.getElementById('aboutOverlay').addEventListener('click', e=>{ if(e.target.id==='aboutOverlay') e.currentTarget.classList.remove('show'); });

/* ---- splash ---- */
document.getElementById('enterBtn').addEventListener('click', ()=>{
  document.getElementById('splash').classList.add('hide');
});
