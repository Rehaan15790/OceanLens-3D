/* Update orchestration, start-up and the animation loop. */
let volumeStats = {min:0,max:0,mean:0};
// header readout: water only — nothing over land or below the sea floor.
// In isosurface mode it describes the surface (depths), matching the colour bar.
function computeStats(){
  if(state.variable!=='tchp' && state.mode==='iso'){
    volumeStats = {min:isoInfo.min, max:isoInfo.max, mean:isoInfo.mean};
    return;
  }
  const vals = [];
  if(state.variable==='tchp'){
    tchpGrid(state.time).forEach(c=>{ if(c) vals.push(c.q); });
  } else {
    const depths = state.mode==='slice' ? [DEPTHS[state.depthIdx]] : DEPTHS;
    gridPts.forEach((p,idx)=>{
      if(gridLand[idx]) return;
      depths.forEach(d=>{ if(d <= gridSeabed[idx]) vals.push(getVal(state.variable,p.lat,p.lon,d,state.time)); });
    });
  }
  let min=Infinity, max=-Infinity, sum=0;
  vals.forEach(v=>{ if(v<min) min=v; if(v>max) max=v; sum+=v; });
  volumeStats = vals.length ? {min, max, mean:sum/vals.length} : {min:NaN, max:NaN, mean:NaN};
}

function applyMode(){
  const tchp = state.variable==='tchp';
  layerMeshes.forEach(mesh=>{ mesh.visible = false; });
  arrowGroup.visible = false;
  if(isoMesh) isoMesh.visible = false;
  tchpMesh.visible = tchp;
  tchpMesh.material.opacity = section ? 0.5 : 0.92;
  trackGroup.visible = tchp;
  if(d26Mesh) d26Mesh.visible = tchp;
  if(tchpContour) tchpContour.visible = tchp;
  flowMesh.visible = state.showFlow && !tchp;
  document.getElementById('modeGrp').style.display = tchp ? 'none' : 'block';
  document.getElementById('tchpNote').style.display = tchp ? 'block' : 'none';
  document.getElementById('depthGrp').style.display = !tchp && state.mode==='slice' ? 'block' : 'none';
  document.getElementById('isoGrp').style.display = !tchp && state.mode==='iso' ? 'block' : 'none';
  updateLegend();

  if(tchp){
    updateTchpView();
  } else if(state.mode==='layers'){
    layerMeshes.forEach(mesh=>{
      mesh.visible = true;
      mesh.material.opacity = section ? Math.min(state.opacity, 0.15) : state.opacity;
    });
  } else if(state.mode==='slice'){
    const depth = DEPTHS[state.depthIdx];
    layerMeshes.forEach(mesh=>{
      if(mesh.userData.depth === depth){ mesh.visible = true; mesh.material.opacity = Math.min(0.95, state.opacity+0.3); }
    });
    if(state.variable==='cur'){
      arrowGroup.visible = true;
      buildArrows(depth);
    }
  } else if(state.mode==='iso'){
    buildIsoGeometry();
    isoMesh.visible = true;
  }
}

let markersKey = null;
function fullUpdate(){
  // real floats belong to a snapshot: rebuild the markers when the snapshot or the source changes
  const mk = dataMode+'|'+(dataMode==='real' || state.colorMisfit ? state.time : '')+'|'+state.exagg+'|'+state.colorMisfit;
  if(mk!==markersKey){ markersKey = mk; buildMarkers(); applyMarkerVisibility(); }
  if(profileState.open && profileState.inst.real && profileState.inst.snap!==state.time) closeSidePanels();
  updateStormNow();
  updateLayerColors();
  applyMode();
  computeStats();
  updateReadout();
  updateColorbarUI();
  updateFloorPosition();
  updateDomainBox();
  updateDepthLabelPositions();
  if(profileState.open){ renderDerived(); drawProfileChart(); }
  if(trackState.open) renderTrack();
  if(section){ buildSection(); if(sectionState.open) drawSectionPanel(); }
  renderValidation();
}

/* ============================================================
   BOOT
   ============================================================ */

let lastFrameTime = performance.now();
let clockElapsed = 0;
function animate(){
  requestAnimationFrame(animate);
  const now = performance.now();
  const dt = Math.min(0.05, (now-lastFrameTime)/1000);
  lastFrameTime = now;
  clockElapsed += dt;
  if(camTween){
    const k = Math.min(1, (now-camTween.t0)/camTween.dur), e = k*k*(3-2*k);
    camAz = lerp(camTween.az0, camTween.az1, e); camEl = lerp(camTween.el0, camTween.el1, e);
    if(camTween.r1!==undefined){
      camR = lerp(camTween.r0, camTween.r1, e);
      camTarget.x = lerp(camTween.t0x, camTween.t1x, e); camTarget.z = lerp(camTween.t0z, camTween.t1z, e);
    }
    updateCamera();
    if(k >= 1) camTween = null;
  } else if(tourRotating && !tourPaused){ camAz += 0.0009; updateCamera(); }
  updateFlowParticles(dt);
  updateSurfaceRipple(clockElapsed);
  pulseMarkers(clockElapsed);
  updateLabels();
  renderer.render(scene, camera);
}
resize();
resetView();
syncDataModeUI();
document.getElementById('timeNow').textContent = timeLabel(0);
document.getElementById('depthVal').textContent = DEPTHS[state.depthIdx]+' m';
syncIsoSlider();
updateFloorPosition();
fullUpdate();
animate();
