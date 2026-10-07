/* ---- geographic and graticule labels (screen-space projected) ---- */
const labelDefs = [
  {text:'India', lat:16.5, lon:78, y:0.2, cls:'land'},
  {text:'Sri Lanka', lat:7.6, lon:80.8, y:0.14, cls:'land small'},
  {text:'Arabian Sea', lat:14, lon:65.5, y:0.06, cls:'sea'},
  {text:'Bay of Bengal', lat:12.5, lon:88.5, y:0.06, cls:'sea'},
];
[65,70,75,80,85,90].forEach(lon=>labelDefs.push({text:lon+'°E', lat:LAT0-1.1, lon, y:0.06, cls:'tick'}));
[10,15,20].forEach(lat=>labelDefs.push({text:lat+'°N', lat, lon:LON0-1.9, y:0.06, cls:'tick'}));
// named currents in this region, shown only while Current speed is the active variable
const currentLabelDefs = [
  {text:'West India Coastal Current', lat:13.5, lon:72.5, y:0.09, cls:'current'},
  {text:'East India Coastal Current', lat:13.5, lon:83.5, y:0.09, cls:'current'},
  {text:'Monsoon Current', lat:6.3, lon:78.5, y:0.09, cls:'current'},
  {text:'Bay of Bengal Gyre', lat:18, lon:88, y:0.09, cls:'current'},
];
currentLabelDefs.forEach(d=>{ d.onlyWhen='cur'; labelDefs.push(d); });
const labelLayerEl = document.getElementById('labelLayer');
// an HTML label pinned to a 3D point; updateLabels places it each frame. extra: onlyWhen (a variable), visibleIf()
function makeLabel(text, cls, pos, extra){
  const el = document.createElement('div');
  el.className = 'geoLabel '+cls;
  el.textContent = text;
  el.style.display = 'none';
  labelLayerEl.appendChild(el);
  const L = Object.assign({el, pos, shown:false, x:NaN, y:NaN}, extra);
  labelEls.push(L);
  return L;
}
const labelEls = [];
labelDefs.forEach(d=>{
  const [x,z] = toXZ(d.lat, d.lon);
  makeLabel(d.text, d.cls, new THREE.Vector3(x, d.y, z), {onlyWhen:d.onlyWhen});
});

/* ---- depth ruler on the vertical edge of the domain nearest the camera ---- */
const DEPTH_TICKS = [0,100,200,500,1000];
const rulerLabels = DEPTH_TICKS.map(d=>makeLabel(d+' m', 'tick', new THREE.Vector3(), {depth:d}));
const ruler = new THREE.LineSegments(new THREE.BufferGeometry(), Render.line(0x8c97a1, {opacity:0.8}));
ruler.renderOrder = Render.ORDER.surfaceLines;
scene.add(Render.register('ruler', ruler));
let rulerCorner = null;
// the depth ruler on the box corner nearest the camera; redrawn only when that corner or the exaggeration changes
function placeRuler(){
  const hx = (LON1-LON0)*SCALE_XZ/2, hz = (LAT1-LAT0)*SCALE_XZ/2;
  // the corner nearest the camera, pushed slightly outside the box so no layer sits in front of it
  const sx = camera.position.x >= camTarget.x ? 1 : -1, sz = camera.position.z >= camTarget.z ? 1 : -1;
  const key = sx+','+sz+','+state.exagg;
  if(key === rulerCorner) return;
  rulerCorner = key;
  const x = sx*(hx + 0.25), z = sz*(hz + 0.25), tick = 0.18, pts = [];
  const yOf = d => -d*DEPTH_SCALE*state.exagg;
  pts.push(new THREE.Vector3(x, 0, z), new THREE.Vector3(x, yOf(1000), z));
  DEPTH_TICKS.forEach(d=>{ pts.push(new THREE.Vector3(x, yOf(d), z), new THREE.Vector3(x + sx*tick, yOf(d), z)); });
  ruler.geometry.dispose();
  ruler.geometry = new THREE.BufferGeometry().setFromPoints(pts);
  rulerLabels.forEach(L=>{ L.pos.set(x + sx*(tick + 0.35), yOf(L.depth), z); });
}
// depth-anchored labels follow the vertical exaggeration slider (updateDepthGeometry)
function updateDepthLabelPositions(){ rulerCorner = null; placeRuler(); }

/* ---- per rendered frame: project, place, and dim labels that land hides ---- */
const labelRay = new THREE.Raycaster();
const _lp = new THREE.Vector3(), _ld = new THREE.Vector3();
let lastLabelCamera = '', compassDeg = NaN;
// every rendered frame: project the labels, hide those out of view and dim those behind land
function updateLabels(){
  placeRuler();
  const rect = viewport.getBoundingClientRect();
  const camKey = camera.position.toArray().map(v=>v.toFixed(3)).join(',') + rect.width + 'x' + rect.height;
  const cameraMoved = camKey !== lastLabelCamera;
  lastLabelCamera = camKey;
  labelEls.forEach(L=>{
    const wanted = !(L.onlyWhen && L.onlyWhen!==state.variable) && !(L.visibleIf && !L.visibleIf());
    const p = _lp.copy(L.pos).project(camera);
    const inView = wanted && p.z < 1 && p.x>-1.1 && p.x<1.1 && p.y>-1.1 && p.y<1.1;
    if(inView !== L.shown){ L.el.style.display = inView ? 'block' : 'none'; L.shown = inView; }
    if(!inView) return;
    const x = Math.round((p.x*0.5+0.5)*rect.width), y = Math.round((-p.y*0.5+0.5)*rect.height);
    if(x !== L.x || y !== L.y){
      L.el.style.transform = `translate(${x}px,${y}px) translate(-50%,-50%)`;
      L.x = x; L.y = y;
    }
    if(cameraMoved && occluders.length){
      const dist = _ld.copy(L.pos).sub(camera.position).length();
      labelRay.set(camera.position, _ld.normalize());
      const hit = labelRay.intersectObjects(occluders, false);
      L.el.classList.toggle('occluded', hit.length>0 && hit[0].distance < dist-0.05);
    }
  });
  const deg = Math.round(northAngle()*10)/10;
  if(deg !== compassDeg){
    const compassSvg = document.querySelector('#compass svg');
    if(compassSvg) compassSvg.style.transform = 'rotate('+deg+'deg)';
    compassDeg = deg;
  }
}

// the visible map labels for the PNG export: text, CSS-px position in the view, class list
function labelsForExport(){
  return labelEls.filter(L=>L.shown).map(L=>({text:L.el.textContent, x:L.x, y:L.y, cls:L.el.className,
    occluded:L.el.classList.contains('occluded')}));
}
