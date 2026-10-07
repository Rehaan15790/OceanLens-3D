/* Update orchestration, start-up and the animation loop. */
let volumeStats = {min:NaN, max:NaN, mean:NaN};
// header statistics: water nodes at the level the readout names (the surface or the slice);
// in isosurface mode they describe the surface's depths, like the colour key
function computeStats(){
  if(isoMode()){
    volumeStats = isoInfo ? {min:isoInfo.min, max:isoInfo.max, mean:isoInfo.mean} : {min:NaN, max:NaN, mean:NaN};
    return;
  }
  const vals = [];
  if(is2D(state.variable)){
    field2dGrid(state.variable, state.time).forEach(q=>{ if(q!==null && Number.isFinite(q)) vals.push(q); });
  } else if(!hasModel(state.variable)){
    // no model field (chlorophyll in real mode): the profiles on screen at the readout depth
    const d = readoutDepth();
    instrumentsNow().forEach(inst=>{
      const p = observeProfile(inst, state.time), arr = obsArray(p, state.variable);
      const v = arr && d <= inst.maxDepth ? obsInterp(p, arr)(Math.max(d, p.z[0])) : null;
      if(v!==null && Number.isFinite(v)) vals.push(v);
    });
  } else {
    const d = readoutDepth();
    gridPts.forEach((p,idx)=>{
      if(!gridLand[idx] && d <= gridSeabed[idx]) vals.push(getVal(state.variable, p.lat, p.lon, d, state.time));
    });
  }
  let min=Infinity, max=-Infinity, sum=0;
  vals.forEach(v=>{ if(v<min) min=v; if(v>max) max=v; sum+=v; });
  volumeStats = vals.length ? {min, max, mean:sum/vals.length} : {min:NaN, max:NaN, mean:NaN};
}

// the view first: the key reads what it builds (the iso surface's depth range, the track's visibility)
function applyMode(){
  applyVolumeView();
  updateLegend();
}

// real floats belong to a snapshot: rebuild the markers when the snapshot, the source, the
// vertical scale or the misfit colouring changes
let markersKey = null;
function refreshMarkers(){
  const mk = dataMode+'|'+(dataMode==='real' || state.colorMisfit ? state.time : '')+'|'+state.exagg+'|'+state.colorMisfit+
    '|'+datasetRev+'|'+state.showAllImported;
  if(mk===markersKey) return;
  markersKey = mk;
  buildMarkers();
  applyMarkerVisibility();
}

function fullUpdate(){
  refreshMarkers();
  followProfile();
  updateStormNow();
  updateLayerColors();
  updateDepthGeometry();
  applyMode();
  computeStats();
  updateReadout();
  updateColorbarUI();
  if(state.panel==='profile'){ renderDerived(); drawProfileChart(); }
  if(state.panel==='track') renderTrack();
  if(section){ buildSection(); if(state.panel==='section') drawSectionPanel(); }
  renderValidation();
}
// the exaggeration fast path: geometry that depends on the vertical scale, no recolouring
function applyDepthScale(){
  updateDepthGeometry();
  refreshMarkers();
  applyVolumeView();
  if(section) buildSection();
  updateLegend();
}

/* ---- updates: every action ends here; input events coalesce to one update per frame ----
   kind: 'full', 'depth' (vertical exaggeration), 'opacity' or 'overlay' (visibility only).
   This is the one place app changes reach the renderer (Render.invalidate). */
const pendingUpdates = new Set();
let updateFrame = 0;
function requestUpdate(kind = 'full'){
  pendingUpdates.add(kind);
  syncControls();
  if(!updateFrame) updateFrame = requestAnimationFrame(flushUpdate);
}
// run a pending update now (start-up, the tour, tests)
function flushUpdate(){
  if(updateFrame){ cancelAnimationFrame(updateFrame); updateFrame = 0; }
  if(!pendingUpdates.size) return;
  const kinds = new Set(pendingUpdates);
  pendingUpdates.clear();
  if(kinds.has('full')) fullUpdate();
  else {
    if(kinds.has('depth')) applyDepthScale();
    if(kinds.has('opacity')) setLayerOpacity(state.opacity);
  }
  if(kinds.has('overlay')) applyOverlays();
  Render.invalidate();
}

/* ---- the test and status surface (window.__ol, created by boot-guard.js) ---- */
function exposeTestSurface(){
  const ol = window.__ol || (window.__ol = {});
  const now = f => (...args)=>{ const r = f(...args); flushUpdate(); return r; };   // actions apply at once
  ol.state = state;
  Object.defineProperty(ol, 'dataMode', {get: ()=>dataMode, configurable:true});
  ol.actions = Object.fromEntries(Object.entries({
    setVariable, setMode, setDepth, setIso, setTime, setPlaying, setDataMode, setOverlay, setOpacity, setExaggeration,
    setRange, setPalette, setScale, setShowAllImported, resetRange: ()=>{ resetRange(); requestUpdate('full'); },
    openPanel, closePanels:closeSidePanels, openProfile, openWorstFloat, startTour, stopTour, goToTourStep, nextTourStep, setTourAutoplay,
    showSectionPreset, clearSection
  }).map(([k, f])=>[k, now(f)]));
  ol.flush = flushUpdate;
  ol.plugins = OceanLens;
  ol.facts = facts;
  ol.fn = {seawaterDensity, sigmaT, tchpColumn, firstCrossing, contourSegments, summarize, misfit, realAt, obsInterp,
           modelTchp, b64Int16, fmt, parseDelimited, NetCDF3, depthFromPressure, field2dGrid, instrumentsNow};
  ol.render = Render.test;
}

/* ============================================================
   BOOT
   ============================================================ */
let readyMarked = false;
function animate(){
  requestAnimationFrame(animate);
  renderFrame();
  if(!readyMarked){ readyMarked = true; window.__ol?._markReady(); }
}
function boot(){
  try {
    resize();
    resetView();
    resetRange();
    requestUpdate('full');
    flushUpdate();
    exposeTestSurface();
    animate();
  } catch(err){
    window.__ol?._fail(err);
    throw err;
  }
}
boot();
