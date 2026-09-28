/* ============================================================
   VERTICAL SECTION
   Drag a line across the sea surface (or pick a preset) to cut a curtain
   through the model, from the surface down to the sea floor or the model's
   1000 m bottom. Drawn in 3D here and as a distance–depth plot in the
   right panel.
   ============================================================ */
const SECTION_PRESETS = {
  as15:  {name:'15°N across the Arabian Sea',       a:[15.0,61.5], b:[15.0,73.4]},
  bob15: {name:'15°N across the Bay of Bengal',     a:[15.0,80.8], b:[15.0,91.5]},
  n8:    {name:'8°N, Arabian Sea to Bay of Bengal', a:[8.0,62.0],  b:[8.0,91.5]}
};
let section = null;                  // {a:[lat,lon], b:[lat,lon], name}
const sectionState = {open:false};
const sectionGroup = new THREE.Group();
scene.add(sectionGroup);
// curtain rows: 10 m steps through the upper ocean, 25 m below 200 m
const SECTION_Z = [];
for(let z=0; z<200; z+=10) SECTION_Z.push(z);
for(let z=200; z<=1000; z+=25) SECTION_Z.push(z);

// what the section is coloured by. TCHP is a 2-D field, so in that view the section shows
// temperature, whose 26 °C line is the bottom of the heat the map measures
function sectionSpec(){
  if(state.variable==='tchp'){
    return {v:'temp', palette:'thermal', min:VAR_META.temp.defMin, max:VAR_META.temp.defMax, unit:'°C', label:'Temperature'};
  }
  const m = VAR_META[state.variable];
  return {v:state.variable, palette:state.palette, min:state.cbMin, max:state.cbMax, unit:m.unit, label:m.label};
}
function sectionContours(v){
  if(v==='temp') return [{val:26, label:'D26', dash:[]}, {val:20, label:'D20', dash:[4,3]}];
  if(v==='sal') return [{val:35, label:'35 PSU', dash:[]}];
  return [];
}
// n columns from A to B: position, distance, land / seabed, and the model column
// (interpolated between model levels, like every other model readout here)
function sectionColumns(n){
  const {a, b} = section, fn = MODEL_FN[sectionSpec().v], total = haversineKm(a, b);
  const cols = [];
  for(let i=0;i<n;i++){
    const f = i/(n-1);
    const lat = lerp(a[0], b[0], f), lon = lerp(a[1], b[1], f);
    const land = isLand(lat, lon);
    const seabed = land ? 0 : Math.min(bathyDepth(lat, lon), DEPTHS[DEPTHS.length-1]);
    const at = land ? null : columnInterp(DEPTHS.map(d=>fn(lat, lon, d, state.time)));
    cols.push({f, km:f*total, lat, lon, land, seabed, at});
  }
  return cols;
}
// observed profiles within maxKm of the section line, with their position along it (0–1)
function sectionNearby(maxKm){
  const {a, b} = section, k = 111.2, cl = Math.cos((a[0]+b[0])/2*Math.PI/180);
  const ax = a[1]*k*cl, ay = a[0]*k, dx = b[1]*k*cl-ax, dy = b[0]*k-ay, L2 = dx*dx+dy*dy || 1;
  const out = [];
  instrumentsNow().forEach(inst=>{
    const px = inst.lon*k*cl, py = inst.lat*k;
    const f = ((px-ax)*dx + (py-ay)*dy)/L2;
    if(f < 0 || f > 1) return;
    const off = Math.hypot(ax+f*dx-px, ay+f*dy-py);
    if(off <= maxKm) out.push({inst, f, off});
  });
  return out.sort((p,q)=>p.f-q.f);
}

const secEndLabels = ['A','B'].map(t=>{
  const el = document.createElement('div');
  el.className = 'geoLabel secEnd';
  el.textContent = t;
  labelLayerEl.appendChild(el);
  const L = {el, pos:new THREE.Vector3(), visibleIf:()=>section!==null};
  labelEls.push(L);
  return L;
});

function buildSection(){
  sectionGroup.children.slice().forEach(o=>{ sectionGroup.remove(o); o.geometry.dispose(); o.material.dispose(); });
  if(!section) return;
  const spec = sectionSpec();
  const n = Math.max(2, Math.min(160, Math.round(haversineKm(section.a, section.b)/15)+1));  // ~15 km columns
  const cols = sectionColumns(n), R = SECTION_Z.length;
  const yOf = d => -d*DEPTH_SCALE*state.exagg;
  const at3 = (c, d, lift) => { const [x,z] = toXZ(c.lat, c.lon); return new THREE.Vector3(x, yOf(d)+lift, z); };

  const positions = [], colors = [];
  cols.forEach(c=>{
    SECTION_Z.forEach(zd=>{
      const d = Math.min(zd, c.seabed);   // rows below the sea floor fold onto it, so the curtain follows the bottom
      const p = at3(c, d, 0);
      positions.push(p.x, p.y, p.z);
      colors.push(...(c.land ? [0.56,0.49,0.32] : paletteColor(spec.palette, (c.at(d)-spec.min)/(spec.max-spec.min))));
    });
  });
  const indices = [];
  for(let i=0;i<n-1;i++){
    if(cols[i].land && cols[i+1].land) continue;
    for(let r=0;r<R-1;r++){
      const a=i*R+r, b=a+1, c=a+R, e=c+1;
      indices.push(a,b,c, b,e,c);
    }
  }
  const geo = new THREE.BufferGeometry();
  geo.setAttribute('position', new THREE.Float32BufferAttribute(positions, 3));
  geo.setAttribute('color', new THREE.Float32BufferAttribute(colors, 3));
  geo.setIndex(indices);
  sectionGroup.add(new THREE.Mesh(geo, new THREE.MeshBasicMaterial({vertexColors:true, side:THREE.DoubleSide, toneMapped:false})));

  const line = (pts, color, opacity) => sectionGroup.add(new THREE.Line(new THREE.BufferGeometry().setFromPoints(pts),
    new THREE.LineBasicMaterial({color, transparent:true, opacity, toneMapped:false})));
  line(cols.map(c=>at3(c, 0, 0.06)), 0xffffff, 0.95);                 // along the sea surface
  line(cols.map(c=>at3(c, c.seabed, -0.004)), 0xdfcd94, 0.75);        // sea floor, or the model bottom
  [cols[0], cols[n-1]].forEach(c=>line([at3(c, 0, 0.06), at3(c, c.seabed, 0)], 0xffffff, 0.6));
  // isolines on the curtain, broken wherever a column doesn't cross
  sectionContours(spec.v).forEach(ct=>{
    let run = [];
    const flush = ()=>{ if(run.length > 1) line(run, 0xffffff, ct.dash.length ? 0.5 : 0.95); run = []; };
    cols.forEach(c=>{
      const d = c.land ? null : firstCrossing(c.at, c.seabed, ct.val);
      if(d===null) flush(); else run.push(at3(c, d, 0));
    });
    flush();
  });
  secEndLabels[0].pos.copy(at3(cols[0], 0, 0.3));
  secEndLabels[1].pos.copy(at3(cols[n-1], 0, 0.3));
}

/* ---- drawing a section by dragging across the sea surface ---- */
const drawRay = new THREE.Raycaster(), drawPlane = new THREE.Plane(new THREE.Vector3(0,1,0), 0), drawPt = new THREE.Vector3();
function surfaceLatLon(e){
  const rect = renderer.domElement.getBoundingClientRect();
  drawRay.setFromCamera(new THREE.Vector2(((e.clientX-rect.left)/rect.width)*2-1, -((e.clientY-rect.top)/rect.height)*2+1), camera);
  if(!drawRay.ray.intersectPlane(drawPlane, drawPt)) return null;
  const lat = -drawPt.z/SCALE_XZ + (LAT0+LAT1)/2, lon = drawPt.x/SCALE_XZ + (LON0+LON1)/2;   // inverse of toXZ
  return [Math.max(LAT0, Math.min(LAT1, lat)), Math.max(LON0, Math.min(LON1, lon))];
}
function setSectionDraw(on){
  state.sectionDraw = on;
  document.getElementById('sectionDrawBtn').classList.toggle('active', on);
  viewport.classList.toggle('drawing', on);
  document.getElementById('drawHint').classList.toggle('show', on);
  if(on && tourTimer) stopTour();
}
let sectionDrag = null, sectionBuildQueued = false;
function requestSectionBuild(){
  if(sectionBuildQueued) return;
  sectionBuildQueued = true;
  requestAnimationFrame(()=>{ sectionBuildQueued = false; buildSection(); });
}
renderer.domElement.addEventListener('pointerdown', e=>{
  if(!state.sectionDraw) return;
  const p = surfaceLatLon(e);
  if(!p) return;
  sectionDrag = {a:p};
  renderer.domElement.setPointerCapture(e.pointerId);
});
renderer.domElement.addEventListener('pointermove', e=>{
  if(!sectionDrag) return;
  const p = surfaceLatLon(e);
  if(!p) return;
  section = {a:sectionDrag.a, b:p, name:'Custom section'};
  requestSectionBuild();   // the curtain follows the pointer while dragging
});
renderer.domElement.addEventListener('pointerup', ()=>{
  if(!sectionDrag) return;
  sectionDrag = null;
  setSectionDraw(false);
  // a click or a tiny drag is not a section
  if(!section || haversineKm(section.a, section.b) < 60){ clearSection(); return; }
  showSection();
});

function trackSectionPreset(){
  const T = currentTrack(), last = T[T.length-1];
  return {name: dataMode==='real' ? 'Along Amphan\'s track' : 'Along the sample cyclone track',
    a:[T[0].lat, T[0].lon], b:[last.lat, last.lon]};
}
function showSectionPreset(key){
  const p = key==='track' ? trackSectionPreset() : SECTION_PRESETS[key];
  section = {a:p.a.slice(), b:p.b.slice(), name:p.name};
  showSection();
  faceSection();
}
// swing the camera round to look at the curtain face-on. It stands on whichever side has open
// water between it and the curtain (land slabs in the foreground hide the section), and on the
// southern side when both are clear, so north stays up
function faceSection(){
  const [ax,az] = toXZ(section.a[0], section.a[1]), [bx,bz] = toXZ(section.b[0], section.b[1]);
  const len = Math.hypot(bx-ax, bz-az) || 1, r1 = Math.max(9, Math.min(24, len*1.1+6));
  let nx = -(bz-az)/len, nz = (bx-ax)/len;      // unit horizontal normal to the section
  if(nz < 0){ nx = -nx; nz = -nz; }            // +z is south
  const mx = (ax+bx)/2, mz = (az+bz)/2, reach = r1*Math.cos(0.42);
  const landHits = sgn => {
    let hits = 0;
    for(let k=1;k<=10;k++){
      const x = mx + sgn*nx*reach*k/10, z = mz + sgn*nz*reach*k/10;
      if(isLand(-z/SCALE_XZ + (LAT0+LAT1)/2, x/SCALE_XZ + (LON0+LON1)/2)) hits++;
    }
    return hits;
  };
  if(landHits(-1) < landHits(1)){ nx = -nx; nz = -nz; }
  let to = Math.atan2(nx, nz);
  while(to-camAz > Math.PI) to -= 2*Math.PI;   // turn the short way round
  while(camAz-to > Math.PI) to += 2*Math.PI;
  // orbit around the middle of the section, close enough that it fills the view
  camTween = {az0:camAz, el0:camEl, r0:camR, t0x:camTarget.x, t0z:camTarget.z,
    az1:to, el1:0.42, r1, t1x:mx, t1z:mz,
    t0:performance.now(), dur:900};
}
function showSection(){
  buildSection();
  applyMode();            // dims the volume so the curtain reads clearly
  showPanel('section');
  drawSectionPanel();
}
// after a section has pulled the camera to one corner, ease back to the home view
function homeCamera(){
  if(camTarget.distanceTo(CAM_TARGET_HOME) < 0.01) return;
  let to = 0.35;
  while(to-camAz > Math.PI) to -= 2*Math.PI;
  while(camAz-to > Math.PI) to += 2*Math.PI;
  camTween = {az0:camAz, el0:camEl, r0:camR, t0x:camTarget.x, t0z:camTarget.z,
    az1:to, el1:0.68, r1:fitDistance(), t1x:CAM_TARGET_HOME.x, t1z:CAM_TARGET_HOME.z, t0:performance.now(), dur:900};
}
function clearSection(){
  section = null;
  buildSection();
  applyMode();
  homeCamera();
  if(sectionState.open) showPanel('empty');
}
document.getElementById('sectionDrawBtn').addEventListener('click', ()=>setSectionDraw(!state.sectionDraw));
document.querySelectorAll('#sectionPresets .preset').forEach(b=>{
  b.addEventListener('click', ()=>{ setSectionDraw(false); if(tourTimer) stopTour(); showSectionPreset(b.dataset.sec); });
});
document.getElementById('closeSection').addEventListener('click', clearSection);
