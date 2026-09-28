/* ============================================================
   DATA SOURCES
   'real'  : HYCOM analysis, Argo profiles and the IBTrACS track around Cyclone
             Amphan (4–25 May 2020), embedded above as REAL_DATA by
             data-pipeline/build_snapshot.py
   'synth' : the procedural demo ocean defined just above
   Everything else reads the ocean through fieldTemp / fieldSal / fieldCurrentUV /
   fieldChl, so switching source is one variable.
   ============================================================ */

function fieldTemp(lat, lon, depth, t){ return dataMode==='real' ? realAt(0, lat, lon, depth, t) : synthTemp(lat, lon, depth, t); }
function fieldSal(lat, lon, depth, t){ return dataMode==='real' ? realAt(1, lat, lon, depth, t) : synthSal(lat, lon, depth, t); }
function fieldCurrentUV(lat, lon, depth, t){
  return dataMode==='real' ? {u:realAt(2, lat, lon, depth, t), v:realAt(3, lat, lon, depth, t)} : synthCurrentUV(lat, lon, depth, t);
}
// HYCOM is a physics-only model, so the real model column has no chlorophyll
function fieldChl(lat, lon, depth, t){ return dataMode==='real' ? NaN : synthChl(lat, lon, depth, t); }

function currentSpeed(lat,lon,depth,t){const {u,v}=fieldCurrentUV(lat,lon,depth,t);return Math.sqrt(u*u+v*v);}

function getVal(varKey, lat, lon, depth, t){
  if(varKey==='temp') return fieldTemp(lat,lon,depth,t);
  if(varKey==='sal') return fieldSal(lat,lon,depth,t);
  return currentSpeed(lat,lon,depth,t);
}

// the profiles on screen: real ones belong to one snapshot, synthetic instruments are fixed

function instrumentsNow(){ return dataMode==='real' ? realProfiles.filter(p=>p.snap===state.time) : synthInstruments; }

function currentTrack(){ return dataMode==='real' ? REAL_TRACK : SAMPLE_TRACK; }

/* ---- data source switch (header) ---- */
function caveatText(){
  return dataMode==='real'
    ? 'HYCOM ANALYSIS · ARGO GDAC · ETOPO1 · IBTRACS · NOT FOR NAVIGATION'
    : 'SYNTHETIC DEMO DATA · NOT FOR NAVIGATION';
}
function syncDataModeUI(){
  document.querySelectorAll('#dataToggle button').forEach(b=>{
    b.classList.toggle('active', b.dataset.mode===dataMode);
    if(b.dataset.mode==='real') b.disabled = !HAS_REAL;
  });
  document.getElementById('scaleNote').textContent = caveatText();
  const gRow = document.getElementById('swGlider').parentElement;   // no public glider data in the real window
  gRow.style.opacity = dataMode==='real' ? 0.4 : 1;
  gRow.title = dataMode==='real' ? 'No public glider data in the Amphan window' : '';
}
function setDataMode(mode){
  if(mode===dataMode || (mode==='real' && !HAS_REAL)) return;
  if(tourTimer) stopTour();
  dataMode = mode;
  applyModeDefaults();
  const meta = VAR_META[state.variable];
  state.cbMin = meta.defMin; state.cbMax = meta.defMax;
  document.getElementById('cbMin').value = meta.defMin;
  document.getElementById('cbMax').value = meta.defMax;
  if(ISO_META[state.variable]) state.isoVal = ISO_META[state.variable].def;
  syncIsoSlider();
  setTime(state.time);          // relabels the timeline with this source's dates
  validationFor = null;
  buildTrack();
  if(profileState.open || trackState.open) closeSidePanels();
  syncDataModeUI();
  fullUpdate();
}
document.querySelectorAll('#dataToggle button').forEach(b=>b.addEventListener('click', ()=>setDataMode(b.dataset.mode)));
