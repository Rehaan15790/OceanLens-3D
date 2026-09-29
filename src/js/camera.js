/* ---- orbit camera: mouse, touch and keyboard ---- */
const CAM_HOME = {az:0.35, el:0.68};                 // from the south-south-east: north up, east right
const CAM_TARGET_HOME = new THREE.Vector3(0, -2.4, 0);
const CAM_MIN_Y = 0.8;                               // never below the sea surface or inside the relief
let camTarget = CAM_TARGET_HOME.clone();
let camAz = CAM_HOME.az, camEl = CAM_HOME.el, camR = 19;
let camAtHome = true;                                // refit on resize only while nobody has moved the view

// distance at which the whole domain box fits the view from the home direction, with a margin
function fitDistance(){
  const hx = (LON1-LON0)*SCALE_XZ/2, hz = (LAT1-LAT0)*SCALE_XZ/2, bottom = -6*state.exagg/3;
  const corners = [];
  for(const x of [-hx, hx]) for(const z of [-hz, hz]) for(const y of [0, bottom]) corners.push(new THREE.Vector3(x, y, z));
  const probe = camera.clone(), v = new THREE.Vector3();
  const fits = r => {
    probe.position.set(CAM_TARGET_HOME.x + r*Math.cos(CAM_HOME.el)*Math.sin(CAM_HOME.az),
      CAM_TARGET_HOME.y + r*Math.sin(CAM_HOME.el), CAM_TARGET_HOME.z + r*Math.cos(CAM_HOME.el)*Math.cos(CAM_HOME.az));
    probe.lookAt(CAM_TARGET_HOME); probe.updateMatrixWorld();
    return corners.every(c => { v.copy(c).project(probe); return Math.abs(v.x) < 0.94 && Math.abs(v.y) < 0.9; });
  };
  let lo = 8, hi = 60;
  for(let k=0; k<24; k++){ const mid = (lo + hi)/2; if(fits(mid)) hi = mid; else lo = mid; }
  return hi;
}
function maxDistance(){ return Math.max(24, fitDistance()*1.25); }
function updateCamera(){
  // keep the camera above the sea: raise the elevation if this distance would take it under
  const minEl = Math.asin(Math.min(1, Math.max(0, (CAM_MIN_Y - camTarget.y)/camR)));
  camEl = Math.max(camEl, minEl);
  camera.position.set(camTarget.x + camR*Math.cos(camEl)*Math.sin(camAz),
    camTarget.y + camR*Math.sin(camEl), camTarget.z + camR*Math.cos(camEl)*Math.cos(camAz));
  camera.lookAt(camTarget);
  Render.invalidate();
}
updateCamera();

let dragging=false, dragMoved=false, lastX=0, lastY=0, drawGesture=false;
let camTween = null;   // eased camera move (see faceSection); any drag cancels it
function userMovedCamera(){ camTween = null; camAtHome = false; }
function orbitBy(dAz, dEl){ userMovedCamera(); camAz += dAz; camEl = Math.max(0.1, Math.min(1.45, camEl + dEl)); updateCamera(); }
function zoomBy(dR){ userMovedCamera(); camR = Math.max(4, Math.min(maxDistance(), camR + dR)); updateCamera(); }

// eased moves and the guided tour's slow turn; true when the camera moved this frame
function stepCamera(now, dt){
  if(camTween){
    const k = Math.min(1, (now - camTween.t0)/camTween.dur), e = k*k*(3 - 2*k);
    camAz = lerp(camTween.az0, camTween.az1, e); camEl = lerp(camTween.el0, camTween.el1, e);
    if(camTween.r1 !== undefined){
      camR = lerp(camTween.r0, camTween.r1, e);
      camTarget.x = lerp(camTween.t0x, camTween.t1x, e); camTarget.z = lerp(camTween.t0z, camTween.t1z, e);
    }
    updateCamera();
    if(k >= 1){ camAtHome = !!camTween.home; camTween = null; }
    return true;
  }
  if(typeof tourRotating !== 'undefined' && tourRotating && !(typeof tourPaused !== 'undefined' && tourPaused)){
    camAz += 0.054*dt;                               // rad/s, the same on any display refresh rate
    updateCamera();
    return true;
  }
  return false;
}

/* pointer: one pointer orbits (or draws a section), two pinch to zoom */
const pointers = new Map();
let pinchDist = 0;
renderer.domElement.addEventListener('pointerdown', e=>{
  camTween = null;
  pointers.set(e.pointerId, {x:e.clientX, y:e.clientY});
  if(pointers.size === 2){
    const [a, b] = [...pointers.values()];
    pinchDist = Math.hypot(a.x - b.x, a.y - b.y);
    dragging = false; dragMoved = true;              // a pinch is never a click
    return;
  }
  if(state.sectionDraw){ drawGesture = true; return; }   // drawing a section, not orbiting
  dragging=true; dragMoved=false; lastX=e.clientX; lastY=e.clientY;
});
window.addEventListener('pointerup', e=>{
  pointers.delete(e.pointerId);
  if(drawGesture){ drawGesture = false; return; }
  // only a press that started on the 3D view counts as a click on it — not one on a side panel
  const wasDragging = dragging;
  dragging=false;
  if(wasDragging && !dragMoved) handleClick(e);
});
window.addEventListener('pointercancel', e=>{ pointers.delete(e.pointerId); dragging = false; });
window.addEventListener('pointermove', e=>{
  if(pointers.has(e.pointerId)) pointers.set(e.pointerId, {x:e.clientX, y:e.clientY});
  if(pointers.size === 2){
    const [a, b] = [...pointers.values()], d = Math.hypot(a.x - b.x, a.y - b.y);
    if(pinchDist > 0 && d > 0) zoomBy(camR*(pinchDist/d - 1));
    pinchDist = d;
    return;
  }
  if(!dragging) return;
  const dx=e.clientX-lastX, dy=e.clientY-lastY;
  if(Math.abs(dx)>3||Math.abs(dy)>3) dragMoved=true;
  lastX=e.clientX; lastY=e.clientY;
  orbitBy(-dx*0.006, -dy*0.006);
});
renderer.domElement.addEventListener('wheel', e=>{
  e.preventDefault();
  const px = e.deltaMode === 1 ? e.deltaY*16 : e.deltaMode === 2 ? e.deltaY*400 : e.deltaY;   // lines, pages
  zoomBy(px*0.012);
}, {passive:false});

/* keyboard, while the 3D view has focus: arrows orbit, +/− zoom, Home resets */
renderer.domElement.addEventListener('keydown', e=>{
  if(e.ctrlKey || e.metaKey || e.altKey) return;
  const step = e.shiftKey ? 0.25 : 0.08;
  const act = {ArrowLeft:()=>orbitBy(step, 0), ArrowRight:()=>orbitBy(-step, 0),
    ArrowUp:()=>orbitBy(0, step*0.6), ArrowDown:()=>orbitBy(0, -step*0.6),
    '+':()=>zoomBy(-1.2), '=':()=>zoomBy(-1.2), '-':()=>zoomBy(1.2), Home:()=>resetView()}[e.key];
  if(!act) return;
  e.preventDefault(); e.stopPropagation();      // keep the page-level shortcuts out of it
  act();
});

/* size and device pixel ratio */
function resize(){
  const w = viewport.clientWidth, h = viewport.clientHeight;
  if(w===0 || h===0) return;
  Render.applySize(renderer, camera, w, h);
  if(camAtHome && !camTween){ camR = fitDistance(); updateCamera(); }
  Render.invalidate();
}
window.addEventListener('resize', resize);
if(window.ResizeObserver) new ResizeObserver(resize).observe(viewport);
// moving the window to a screen with another pixel ratio (or zooming the page) changes devicePixelRatio
(function watchDpr(){
  if(!window.matchMedia) return;
  const mq = matchMedia(`(resolution: ${window.devicePixelRatio}dppx)`);
  const onChange = ()=>{ if(!mq.addEventListener) mq.removeListener(onChange); resize(); watchDpr(); };
  if(mq.addEventListener) mq.addEventListener('change', onChange, {once:true}); else mq.addListener(onChange);
})();

function resetView(){
  camTween=null; camTarget.copy(CAM_TARGET_HOME); camAz=CAM_HOME.az; camEl=CAM_HOME.el; camR=fitDistance();
  camAtHome = true; updateCamera();
}
document.getElementById('resetViewBtn').addEventListener('click', resetView);

// screen angle of north in degrees, clockwise from up, for the compass and the PNG export's north arrow
function northAngle(){ return camAz*180/Math.PI; }
