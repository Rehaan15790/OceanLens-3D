/* ---- hover readout: the value of whatever is drawn under the pointer ----
   A float, waypoint or track fix within PICK_PX wins; then the section curtain or the isosurface
   if the pointer is on it; otherwise the displayed level (surface, slice) or the TCHP map. */
const hoverPlane = new THREE.Plane(new THREE.Vector3(0,1,0), 0);
const hoverRay = new THREE.Raycaster();
const hoverPt = new THREE.Vector3();
const hoverTip = document.getElementById('hoverTip');
const fmtDeg = fmt.latLon2;
// no value (no model water, a gap in the field) reads as a dash, never NaN
const fmtVal = (key, v) => Number.isFinite(v) ? fmt.value(key, v, VAR_META[key].unit) : '–';
function worldLatLon(p){ return {lat: -p.z/SCALE_XZ + (LAT0+LAT1)/2, lon: p.x/SCALE_XZ + (LON0+LON1)/2}; }
function hoverText(e){
  const rect = renderer.domElement.getBoundingClientRect();
  hoverRay.setFromCamera({x:((e.clientX-rect.left)/rect.width)*2-1, y:-((e.clientY-rect.top)/rect.height)*2+1}, camera);
  const pick = pickAt(e.clientX, e.clientY);
  renderer.domElement.style.cursor = pick ? 'pointer' : '';
  if(pick && pick.kind==='track'){
    const f = currentTrack()[pick.index], t = f.t ? fmt.fixTime(f.t) : `+${f.h} h`;
    const wind = [f.nd!=null ? `${f.nd} kt (IMD)` : '', f.usa!=null ? `${f.usa} kt (JTWC)` : ''].filter(Boolean).join(' · ');
    return `${fmtDeg(f.lat, f.lon)}<br>${t}${wind ? ' · '+wind : ''}`;
  }
  if(pick){
    const inst = pick.userData.wp ? pick.userData.wp.inst : pick.userData.data.inst;
    // an imported profile far from the analyses (another year, with "show all") gets its year
    const when = !inst.real ? '' : inst.imported && Math.abs(inst.gapH) > 36 ? fmt.analysisTime(inst.time) : fmt.floatTime(inst.time);
    return `${fmtDeg(inst.lat, inst.lon)}<br>${fmt.esc(inst.id)}${when ? ' · '+when : ''} (click to compare)`;
  }
  // the section curtain, when the pointer is on it
  const curtain = section && sectionGroup.children[0];
  if(curtain){
    const hit = hoverRay.intersectObject(curtain, false)[0];
    if(hit){
      const {lat, lon} = worldLatLon(hit.point), z = Math.max(0, -hit.point.y/(DEPTH_SCALE*state.exagg));
      const spec = sectionSpec(), v = getVal(spec.v, lat, lon, z, state.time);
      return `${fmtDeg(lat, lon)}<br>${Math.round(z)} m · ${fmtVal(spec.v, v)}`;
    }
  }
  // TCHP and other 2D products are a map at the surface, whatever state.mode was left at
  const view = currentView(), tchp = view==='tchp', map2d = tchp || view==='field2d';
  const isoObj = map2d ? null : view==='iso' ? isoMesh : view==='iso3d' ? iso3dMesh : null;
  if(isoObj && isoObj.visible){
    const hit = hoverRay.intersectObject(isoObj, false)[0];
    if(hit){
      const {lat, lon} = worldLatLon(hit.point), z = -hit.point.y/(DEPTH_SCALE*state.exagg);
      return `${fmtDeg(lat, lon)}<br>${isoLabel()} at ${Math.round(z)} m`;
    }
  }
  const depth = view==='slice' ? DEPTHS[state.depthIdx] : 0;
  hoverPlane.constant = depth*DEPTH_SCALE*state.exagg;
  if(!hoverRay.ray.intersectPlane(hoverPlane, hoverPt)) return null;
  const {lat, lon} = worldLatLon(hoverPt);
  if(lat<LAT0||lat>LAT1||lon<LON0||lon>LON1) return null;
  const where = fmtDeg(lat, lon);
  if(isLand(lat, lon)) return `${where}<br>Land`;
  // no model field to read (real-data chlorophyll): say so, as the header and the key do
  if(!hasModel(state.variable)) return `${where}<br>No model field: click a float for its profile`;
  const seabed = bathyDepth(lat, lon);
  if(tchp){
    const r = modelTchp(lat, lon, state.time, seabed);
    const d26 = r.q===0 ? 'D26: surface below 26 °C' : r.d26===null ? 'D26: warm to the sea floor' : `D26 ${r.d26.toFixed(0)} m`;
    return `${where}<br>TCHP ${fmt.value('tchp', r.q, 'kJ/cm²')} · ${d26} · seabed ${Math.round(seabed)} m`;
  }
  if(map2d) return `${where}<br>${VAR_META[state.variable].label} · ${fmtVal(state.variable, getVal(state.variable, lat, lon, 0, state.time))}`;
  if(view==='iso') return `${where}<br>No crossing above the sea floor (seabed ${Math.round(seabed)} m)`;
  if(depth > seabed) return `${where}<br>Below the sea floor (seabed ${Math.round(seabed)} m)`;
  const label = view==='slice' ? `${depth} m` : 'Surface (0 m)';
  return `${where}<br>${label} · ${fmtVal(state.variable, getVal(state.variable, lat, lon, depth, state.time))}`;
}
// what the readout depends on besides the pointer: a shortcut or playback changes it while the pointer rests
const hoverStateKey = ()=>[dataMode, state.variable, state.mode, state.depthIdx, state.time, state.isoVal, state.exagg,
  datasetRev, state.showAllImported, section ? section.a.concat(section.b).join() : ''].join('|');
let hoverAt = null, hoverKey = '';
function showHover(e){
  const text = dragging ? null : hoverText(e);
  hoverAt = text ? {clientX:e.clientX, clientY:e.clientY} : null;
  hoverKey = hoverStateKey();
  if(!text){ hoverTip.style.display='none'; return; }
  const rect = renderer.domElement.getBoundingClientRect();
  hoverTip.innerHTML = text;
  hoverTip.style.display = 'block';
  hoverTip.style.left = (e.clientX-rect.left)+'px';
  hoverTip.style.top = (e.clientY-rect.top)+'px';
}
// read the point again once the view under a resting pointer has changed
(function followHoverState(){
  requestAnimationFrame(followHoverState);
  if(!hoverAt || hoverTip.style.display!=='block' || hoverStateKey()===hoverKey) return;
  flushUpdate();                       // the markers and surfaces the readout picks must be the new ones
  showHover(hoverAt);
})();
renderer.domElement.addEventListener('pointermove', showHover);
renderer.domElement.addEventListener('pointerleave', ()=>{ hoverAt = null; hoverTip.style.display='none'; renderer.domElement.style.cursor=''; });
