/* ============================================================
   QUALITY OF LIFE: hover readout, reset view, PNG export, hotkeys
   ============================================================ */
const hoverPlane = new THREE.Plane(new THREE.Vector3(0,1,0), 0);
const hoverRay = new THREE.Raycaster();
const hoverPt = new THREE.Vector3();
const hoverTip = document.getElementById('hoverTip');
renderer.domElement.addEventListener('pointermove', e=>{
  if(dragging){ hoverTip.style.display='none'; return; }
  const rect = renderer.domElement.getBoundingClientRect();
  const ndc = new THREE.Vector2(
    ((e.clientX-rect.left)/rect.width)*2-1,
    -((e.clientY-rect.top)/rect.height)*2+1);
  const depth = state.variable!=='tchp' && state.mode==='slice' ? DEPTHS[state.depthIdx] : 0;
  hoverPlane.constant = depth*DEPTH_SCALE*state.exagg;
  hoverRay.setFromCamera(ndc, camera);
  if(!hoverRay.ray.intersectPlane(hoverPlane, hoverPt)){ hoverTip.style.display='none'; return; }
  const lon = hoverPt.x/SCALE_XZ + (LON0+LON1)/2;
  const lat = -hoverPt.z/SCALE_XZ + (LAT0+LAT1)/2;   // inverse of toXZ
  if(lat<LAT0||lat>LAT1||lon<LON0||lon>LON1){ hoverTip.style.display='none'; return; }
  const meta = VAR_META[state.variable];
  if(isLand(lat, lon)){
    hoverTip.innerHTML =
      `${Math.abs(lat).toFixed(2)}°${lat>=0?'N':'S'} &nbsp; ${lon.toFixed(2)}°E<br>`+
      `<span class="v">land — no ocean data</span>`;
    hoverTip.style.display='block';
    hoverTip.style.left = (e.clientX-rect.left)+'px';
    hoverTip.style.top = (e.clientY-rect.top)+'px';
    return;
  }
  const seabed = bathyDepth(lat, lon);
  if(state.variable==='tchp'){
    const r = modelTchp(lat, lon, state.time, seabed);
    const d26 = r.q===0 ? 'none (surface below 26 °C)' : r.d26===null ? 'at or below the seabed' : r.d26.toFixed(0)+' m';
    hoverTip.innerHTML =
      `${Math.abs(lat).toFixed(2)}°${lat>=0?'N':'S'} &nbsp; ${lon.toFixed(2)}°E<br>`+
      `TCHP <span class="v">${r.q.toFixed(0)} kJ/cm²</span><br>`+
      `D26 ${d26} · seabed ${Math.round(seabed)} m`;
    hoverTip.style.display='block';
    hoverTip.style.left = (e.clientX-rect.left)+'px';
    hoverTip.style.top = (e.clientY-rect.top)+'px';
    return;
  }
  if(depth > seabed){
    hoverTip.innerHTML =
      `${Math.abs(lat).toFixed(2)}°${lat>=0?'N':'S'} &nbsp; ${lon.toFixed(2)}°E<br>`+
      `seabed <span class="v">${Math.round(seabed)} m</span> — below sea floor`;
    hoverTip.style.display='block';
    hoverTip.style.left = (e.clientX-rect.left)+'px';
    hoverTip.style.top = (e.clientY-rect.top)+'px';
    return;
  }
  const val = getVal(state.variable, lat, lon, depth, state.time);
  hoverTip.innerHTML =
    `${Math.abs(lat).toFixed(2)}°${lat>=0?'N':'S'} &nbsp; ${lon.toFixed(2)}°E<br>`+
    `depth <span class="v">${depth} m</span> · seabed ${Math.round(seabed)} m<br>`+
    `${meta.label} <span class="v">${val.toFixed(2)} ${meta.unit}</span>`;
  hoverTip.style.display='block';
  hoverTip.style.left = (e.clientX-rect.left)+'px';
  hoverTip.style.top = (e.clientY-rect.top)+'px';
});
renderer.domElement.addEventListener('pointerleave', ()=>{ hoverTip.style.display='none'; });
