// orbit controls (custom, minimal)
const CAM_TARGET_HOME = new THREE.Vector3(0,-2.4,0);
let camTarget = CAM_TARGET_HOME.clone();
let camAz = 0.35, camEl = 0.68, camR = 19;   // from the south-south-east: north up, east right
// distance at which the whole domain (≈17 × 9 units plus depth) fits the narrower field of view
function fitDistance(){
  const halfV = THREE.MathUtils.degToRad(camera.fov/2);
  const halfH = Math.atan(Math.tan(halfV)*camera.aspect);
  return Math.max(12, Math.min(24, 7.4/Math.sin(Math.min(halfV, halfH))));
}
function updateCamera(){
  const x = camTarget.x + camR*Math.cos(camEl)*Math.sin(camAz);
  const y = camTarget.y + camR*Math.sin(camEl);
  const z = camTarget.z + camR*Math.cos(camEl)*Math.cos(camAz);
  camera.position.set(x,y,z);
  camera.lookAt(camTarget);
}
updateCamera();

let dragging=false, dragMoved=false, lastX=0,lastY=0, drawGesture=false;
let camTween = null;   // eased camera move (see faceSection); any drag cancels it
renderer.domElement.addEventListener('pointerdown', e=>{
  camTween = null;
  if(state.sectionDraw){ drawGesture = true; return; }   // drawing a section, not orbiting
  dragging=true; dragMoved=false; lastX=e.clientX; lastY=e.clientY;
});
window.addEventListener('pointerup', e=>{
  if(drawGesture){ drawGesture = false; return; }
  // only a press that started on the 3D view counts as a click on it — not one on a side panel
  const wasDragging = dragging;
  dragging=false;
  if(wasDragging && !dragMoved) handleClick(e);
});
window.addEventListener('pointermove', e=>{
  if(!dragging) return;
  const dx=e.clientX-lastX, dy=e.clientY-lastY;
  if(Math.abs(dx)>3||Math.abs(dy)>3) dragMoved=true;
  camAz -= dx*0.006; camEl = Math.max(0.1,Math.min(1.45, camEl - dy*0.006));
  lastX=e.clientX; lastY=e.clientY; updateCamera();
});
renderer.domElement.addEventListener('wheel', e=>{
  e.preventDefault();
  camR = Math.max(4, Math.min(24, camR + e.deltaY*0.012));
  updateCamera();
}, {passive:false});

function resize(){
  const w = viewport.clientWidth, h = viewport.clientHeight;
  if(w===0 || h===0) return;
  renderer.setSize(w,h);
  camera.aspect = w/h; camera.updateProjectionMatrix();
  if(typeof drawColorbarCanvas === 'function') drawColorbarCanvas();
}
window.addEventListener('resize', resize);
if(window.ResizeObserver) new ResizeObserver(resize).observe(viewport);

function resetView(){ camTween=null; camTarget.copy(CAM_TARGET_HOME); camAz=0.35; camEl=0.68; camR=fitDistance(); updateCamera(); }
document.getElementById('resetViewBtn').addEventListener('click', resetView);
