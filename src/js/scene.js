/* ============================================================
   THREE.JS SCENE
   Renderer, backdrop, lights and fog (render.js sets the policy), and the frame loop.
   ============================================================ */
const viewport = document.getElementById('viewport');
const scene = new THREE.Scene();
// the camera stays at least 0.8 units above the sea (camera.js) and the overlays sit 0.003–0.02 apart (over the
// sea: grid 0.02, floats 0.05, heat contour 0.07; over a slice: arrow edges +0.027, arrows +0.03, streaks +0.035),
// so the near plane is as far out as that allows
const camera = new THREE.PerspectiveCamera(42, 1, 0.3, 100);
const renderer = Render.createRenderer();
viewport.appendChild(renderer.domElement);
renderer.domElement.setAttribute('role', 'img');
renderer.domElement.setAttribute('aria-label', '3D view of the ocean model');
renderer.domElement.tabIndex = 0;                 // keyboard orbit and zoom (camera.js)
renderer.domElement.style.touchAction = 'none';   // touch drags orbit and pinches zoom instead of scrolling

scene.add(Render.register('backdrop', new THREE.Mesh(new THREE.SphereGeometry(42, 20, 20), Render.backdrop(0x1d4a66, 0x060e1a))));
scene.add(new THREE.AmbientLight(0xffffff, 0.7));
scene.add(new THREE.HemisphereLight(0x9fd8ff, 0x0a1c14, 0.85));
const dl = new THREE.DirectionalLight(0xeaf6ff, 0.7); dl.position.set(3, 8, 4); scene.add(dl);
// depth cue for the terrain only: data, marks and lines ignore fog (render.js); updateCamera (camera.js) sets
// near and far from the camera distance
scene.fog = new THREE.Fog(0x081525);

Render.attach({
  renderer, scene, camera,
  toWorld: (lat, lon, depth) => { const [x, z] = toXZ(lat, lon); return new THREE.Vector3(x, -depth*DEPTH_SCALE*state.exagg, z); },
  fromWorld: v => ({lat: -v.z/SCALE_XZ + (LAT0+LAT1)/2, lon: v.x/SCALE_XZ + (LON0+LON1)/2}),
  beforeRender: () => updateLabels()
});

/* ---- the frame loop ----
   Render on demand: a frame is drawn only when something changed (Render.invalidate), while the
   camera moves, and at 30 fps while current streaks move. Nothing is drawn while the splash or a
   dialog covers the view. A one-second refresh keeps any missed change from lingering. */
const splashEl = document.getElementById('splash');
function viewCovered(){
  return (splashEl && !splashEl.classList.contains('hide')) ||
    !!document.querySelector('.overlay.show, #smallScreen:not([hidden])');
}
let lastTick = performance.now(), lastFlowTick = 0, lastDrawn = 0, drawnOnce = false, wasCovered = false;
function renderFrame(){
  const now = performance.now(), dt = Math.min(0.05, (now - lastTick)/1000);
  lastTick = now;
  if(stepCamera(now, dt)) Render.invalidate();
  const covered = viewCovered();
  if(covered && drawnOnce){ wasCovered = true; return; }
  if(wasCovered){ wasCovered = false; Render.invalidate(); }
  if(flowMesh.visible && !Render.testMode && now - lastFlowTick >= 33){
    updateFlowParticles(Math.min(0.1, (now - lastFlowTick)/1000));
    lastFlowTick = now;
    Render.invalidate();
  }
  if(!Render.takeDirty() && now - lastDrawn < 1000) return;
  updateLabels();
  renderer.render(scene, camera);
  lastDrawn = now; drawnOnce = true;
}
