/* ---- cyclone tracks ----
   synthetic mode: a made-up Bay of Bengal storm, for illustration only;
   real mode: Cyclone Amphan's six-hourly IBTrACS fixes inside the map. */

const TRACK_Y = 0.13;                    // above the coastal relief, so the landfall end is never inside the land
const trackGroup = new THREE.Group();
let trackFixes = [];                     // [{pos:Vector3, index}] for picking and hover
// mode-dependent strings come from the data source (some are functions of the fix or the wind)
function trackText(key, ...args){
  const v = dataSource().track[key];
  return typeof v === 'function' ? v(...args) : v || '';
}
const trackLabel = makeLabel('', 'current', new THREE.Vector3(), {visibleIf:()=>trackGroup.visible && trackFixes.length>0});
const landfallLabel = makeLabel('', 'current', new THREE.Vector3(), {visibleIf:()=>trackGroup.visible && trackFixes.length>0});
// where the real storm was at the analysis on screen
const stormNow = new THREE.Mesh(new THREE.RingGeometry(0.2, 0.29, 40), Render.flat(0xffcf5c, {opacity:0.95, side:THREE.DoubleSide}));
stormNow.rotation.x = -Math.PI/2;
stormNow.renderOrder = Render.ORDER.markers;
stormNow.visible = false;
const stormLabel = makeLabel('', 'current', new THREE.Vector3(), {visibleIf:()=>stormNow.visible && trackGroup.visible});
stormLabel.el.style.color = '#ffcf5c';
const fixGeo = new THREE.SphereGeometry(1, 12, 12), fixMat = Render.flat(0xffffff);
function buildTrack(){
  trackGroup.children.slice().forEach(o=>{
    trackGroup.remove(o);
    if(o.geometry !== fixGeo) o.geometry.dispose();
    if(o.material !== fixMat) o.material.dispose();
  });
  trackGroup.add(stormNow);
  trackFixes = [];
  const T = currentTrack(), real = dataMode==='real';
  if(!T.length){ trackLabel.el.textContent = landfallLabel.el.textContent = ''; return; }
  const pts = T.map(p=>{ const [x,z]=toXZ(p.lat,p.lon); return new THREE.Vector3(x,TRACK_Y,z); });
  const line = new THREE.Line(new THREE.BufferGeometry().setFromPoints(pts), Render.line(0xffffff, {opacity:0.9, dash:[0.14,0.08]}));
  line.computeLineDistances();
  line.renderOrder = Render.ORDER.surfaceLines;
  trackGroup.add(line);
  T.forEach((p,k)=>{
    // real fixes grow with IMD's wind (the basin's RSMC), so the rapid intensification is visible on the map
    const r = real ? 0.045 + Math.min(1, (p.nd ?? 20)/130)*0.075 : 0.075;
    const m = new THREE.Mesh(fixGeo, fixMat);
    m.scale.setScalar(r);
    m.position.copy(pts[k]);
    trackGroup.add(m);
    trackFixes.push({pos:pts[k], index:k});
  });
  const ring = new THREE.Mesh(new THREE.RingGeometry(0.14, 0.19, 32), Render.flat(0xffffff, {side:THREE.DoubleSide}));
  ring.rotation.x = -Math.PI/2;
  ring.position.copy(pts[pts.length-1]);
  trackGroup.add(ring);
  trackLabel.el.textContent = trackText('mapLabel');
  landfallLabel.el.textContent = trackText('landfallLabel', T[T.length-1]);
  const [x0,z0] = toXZ(T[0].lat-0.8, T[0].lon+0.4);
  trackLabel.pos.set(x0, TRACK_Y+0.03, z0);
  const last = T[T.length-1], [x1,z1] = toXZ(last.lat+0.5, last.lon-0.6);
  landfallLabel.pos.set(x1, 0.34, z1);
}
function updateStormNow(){
  stormNow.visible = false;
  if(dataMode!=='real' || !REAL_TRACK.length)return;
  const now = Date.parse(REAL.meta.times[state.time]);
  const k = REAL_TRACK.findIndex(f=>Date.parse(f.t) >= now);
  if(k<=0)return;             // before the first fix, or after it left the map
  const a = REAL_TRACK[k-1], b = REAL_TRACK[k], f = (now-Date.parse(a.t))/(Date.parse(b.t)-Date.parse(a.t));
  const lat = lerp(a.lat, b.lat, f), lon = lerp(a.lon, b.lon, f);
  const imd = a.nd!=null && b.nd!=null, wind = Math.round(imd ? lerp(a.nd, b.nd, f) : lerp(a.usa||0, b.usa||0, f));
  const [x,z] = toXZ(lat, lon);
  stormNow.position.set(x, TRACK_Y+0.01, z);
  stormNow.visible = true;
  stormLabel.el.textContent = trackText('nowLabel', wind, imd ? 'IMD' : 'JTWC');
  stormLabel.pos.set(x, TRACK_Y+0.03, z + 0.45);
}
// the "Cyclone track" overlay: every variable, whenever it is switched on
function applyTrackVisibility(){
  trackGroup.visible = state.showTrack !== false;
}
buildTrack();
scene.add(Render.register('track', trackGroup));
applyTrackVisibility();
