/* ---- cyclone tracks ----
   synthetic mode: a made-up Bay of Bengal storm, for illustration only;
   real mode: Cyclone Amphan's six-hourly IBTrACS fixes inside the map. */

const trackGroup = new THREE.Group();
const trackMeshes = [];
const trackLabel = labelEls.find(L=>L.el.textContent==='Sample cyclone track');
const landfallLabel = labelEls.find(L=>L.el.textContent==='landfall');
// where the real storm was at the snapshot on screen
const stormNow = new THREE.Mesh(new THREE.RingGeometry(0.2, 0.29, 40),
  new THREE.MeshBasicMaterial({color:0xffcf5c, side:THREE.DoubleSide, toneMapped:false, transparent:true, opacity:0.95}));
stormNow.rotation.x = -Math.PI/2;
stormNow.visible = false;
trackGroup.add(stormNow);
const stormLabel = (()=>{
  const el = document.createElement('div');
  el.className = 'geoLabel current';
  el.style.color = '#ffcf5c';
  labelLayerEl.appendChild(el);
  const L = {el, pos:new THREE.Vector3(), visibleIf:()=>stormNow.visible && trackGroup.visible};
  labelEls.push(L);
  return L;
})();
function buildTrack(){
  trackGroup.children.slice().forEach(o=>{
    if(o===stormNow) return;
    trackGroup.remove(o); o.geometry.dispose(); o.material.dispose();
  });
  trackMeshes.length = 0;
  const T = currentTrack(), real = dataMode==='real';
  const pts = T.map(p=>{ const [x,z]=toXZ(p.lat,p.lon); return new THREE.Vector3(x,0.09,z); });
  const line = new THREE.Line(new THREE.BufferGeometry().setFromPoints(pts),
    new THREE.LineDashedMaterial({color:0xffffff, dashSize:0.14, gapSize:0.08, transparent:true, opacity:0.9, toneMapped:false}));
  line.computeLineDistances();
  trackGroup.add(line);
  const mat = new THREE.MeshBasicMaterial({color:0xffffff, toneMapped:false});
  T.forEach((p,k)=>{
    // real fixes grow with the storm's wind, so the rapid intensification is visible on the map
    const r = real ? 0.045 + Math.min(1, (p.usa||p.nd||20)/145)*0.075 : 0.075;
    const m = new THREE.Mesh(new THREE.SphereGeometry(r, 12, 12), mat.clone());
    m.position.copy(pts[k]);
    m.userData = {type:'track', index:k};
    trackGroup.add(m); trackMeshes.push(m);
  });
  mat.dispose();
  const ring = new THREE.Mesh(new THREE.RingGeometry(0.14, 0.19, 32),
    new THREE.MeshBasicMaterial({color:0xffffff, side:THREE.DoubleSide, toneMapped:false}));
  ring.rotation.x = -Math.PI/2;
  ring.position.copy(pts[pts.length-1]);
  trackGroup.add(ring);
  trackLabel.el.textContent = real ? 'Cyclone Amphan · IBTrACS' : 'Sample cyclone track';
  const [x0,z0] = toXZ(T[0].lat-0.8, T[0].lon+0.4);
  trackLabel.pos.set(x0, 0.12, z0);
  const last = T[T.length-1], [x1,z1] = toXZ(last.lat+0.5, last.lon-0.6);
  landfallLabel.pos.set(x1, 0.34, z1);
}
function updateStormNow(){
  stormNow.visible = false;
  if(dataMode!=='real' || !REAL_TRACK.length) return;
  const now = Date.parse(REAL.meta.times[state.time]);
  const k = REAL_TRACK.findIndex(f=>Date.parse(f.t) >= now);
  if(k<=0) return;                                     // before the first fix, or after it left the map
  const a = REAL_TRACK[k-1], b = REAL_TRACK[k], f = (now-Date.parse(a.t))/(Date.parse(b.t)-Date.parse(a.t));
  const lat = lerp(a.lat, b.lat, f), lon = lerp(a.lon, b.lon, f);
  const wind = Math.round(lerp(a.usa||a.nd||0, b.usa||b.nd||0, f));
  const [x,z] = toXZ(lat, lon);
  stormNow.position.set(x, 0.1, z);
  stormNow.visible = true;
  stormLabel.el.textContent = `Amphan now · ${wind} kt`;
  stormLabel.pos.set(x, 0.12, z + 0.45);
}
buildTrack();
scene.add(trackGroup);
trackGroup.visible = false;
