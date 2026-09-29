/* ---- hover readout: the value of whatever is drawn under the pointer ----
   A float, waypoint or track fix within PICK_PX wins; then the section curtain or the isosurface
   if the pointer is on it; otherwise the displayed level (surface, slice) or the TCHP map. */
const hoverPlane = new THREE.Plane(new THREE.Vector3(0,1,0), 0);
const hoverRay = new THREE.Raycaster();
const hoverPt = new THREE.Vector3();
const hoverTip = document.getElementById('hoverTip');
const fmtDeg = (lat, lon) => `${Math.abs(lat).toFixed(2)}° ${lat>=0?'N':'S'}, ${lon.toFixed(2)}° E`;
const fmtVal = (key, v) => {
  const m = VAR_META[key], dp = key==='temp' ? 1 : key==='tchp' ? 0 : 2;
  return `${v.toFixed(dp)} ${m.unit}`;
};
function worldLatLon(p){ return {lat: -p.z/SCALE_XZ + (LAT0+LAT1)/2, lon: p.x/SCALE_XZ + (LON0+LON1)/2}; }
function hoverText(e){
  const rect = renderer.domElement.getBoundingClientRect();
  hoverRay.setFromCamera({x:((e.clientX-rect.left)/rect.width)*2-1, y:-((e.clientY-rect.top)/rect.height)*2+1}, camera);
  const pick = pickAt(e.clientX, e.clientY);
  renderer.domElement.style.cursor = pick ? 'pointer' : '';
  if(pick && pick.kind==='track'){
    const f = currentTrack()[pick.index], t = f.t ? f.t.replace('T',' ').replace(':00Z',' UTC') : `+${f.h} h`;
    const wind = [f.nd!=null ? `${f.nd} kt (IMD)` : '', f.usa!=null ? `${f.usa} kt (JTWC)` : ''].filter(Boolean).join(' · ');
    return `${fmtDeg(f.lat, f.lon)}<br>${t}${wind ? ' · '+wind : ''}`;
  }
  if(pick){
    const inst = pick.kind==='argo' ? pick.userData.data.inst : pick.userData.wp.inst;
    const when = inst.real ? inst.time.replace('T',' ').replace('Z',' UTC') : '';
    return `${fmtDeg(inst.lat, inst.lon)}<br>${inst.id}${when ? ' · '+when : ''} (click to compare)`;
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
  const tchp = state.variable==='tchp';
  if(!tchp && state.mode==='iso' && isoMesh){
    const hit = hoverRay.intersectObject(isoMesh, false)[0];
    if(hit){
      const {lat, lon} = worldLatLon(hit.point), z = -hit.point.y/(DEPTH_SCALE*state.exagg);
      return `${fmtDeg(lat, lon)}<br>${isoLabel()} at ${Math.round(z)} m`;
    }
  }
  const depth = !tchp && state.mode==='slice' ? DEPTHS[state.depthIdx] : 0;
  hoverPlane.constant = depth*DEPTH_SCALE*state.exagg;
  if(!hoverRay.ray.intersectPlane(hoverPlane, hoverPt)) return null;
  const {lat, lon} = worldLatLon(hoverPt);
  if(lat<LAT0||lat>LAT1||lon<LON0||lon>LON1) return null;
  const where = fmtDeg(lat, lon);
  if(isLand(lat, lon)) return `${where}<br>Land`;
  const seabed = bathyDepth(lat, lon);
  if(tchp){
    const r = modelTchp(lat, lon, state.time, seabed);
    const d26 = r.q===0 ? 'D26: surface below 26 °C' : r.d26===null ? 'D26: warm to the sea floor' : `D26 ${r.d26.toFixed(0)} m`;
    return `${where}<br>TCHP ${r.q.toFixed(0)} kJ/cm² · ${d26} · seabed ${Math.round(seabed)} m`;
  }
  if(state.mode==='iso') return `${where}<br>No crossing above the sea floor (seabed ${Math.round(seabed)} m)`;
  if(depth > seabed) return `${where}<br>Below the sea floor (seabed ${Math.round(seabed)} m)`;
  const label = state.mode==='slice' ? `${depth} m` : 'Surface (0 m)';
  return `${where}<br>${label} · ${fmtVal(state.variable, getVal(state.variable, lat, lon, depth, state.time))}`;
}
renderer.domElement.addEventListener('pointermove', e=>{
  const text = dragging ? null : hoverText(e);
  if(!text){ hoverTip.style.display='none'; return; }
  const rect = renderer.domElement.getBoundingClientRect();
  hoverTip.innerHTML = text;
  hoverTip.style.display = 'block';
  hoverTip.style.left = (e.clientX-rect.left)+'px';
  hoverTip.style.top = (e.clientY-rect.top)+'px';
});
renderer.domElement.addEventListener('pointerleave', ()=>{ hoverTip.style.display='none'; renderer.domElement.style.cursor=''; });
