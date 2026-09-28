/* ---- geographic + graticule labels (screen-space projected) ---- */
const labelDefs = [
  {text:'India', lat:16.5, lon:78, y:0.2, cls:'land'},
  {text:'Sri Lanka', lat:7.6, lon:80.8, y:0.14, cls:'land small'},
  {text:'Arabian Sea', lat:14, lon:65.5, y:0.06, cls:'sea'},
  {text:'Bay of Bengal', lat:12.5, lon:88.5, y:0.06, cls:'sea'},
];
[65,70,75,80,85,90].forEach(lon=>labelDefs.push({text:lon+'°E', lat:LAT0-1.1, lon, y:0.06, cls:'tick'}));
[10,15,20].forEach(lat=>labelDefs.push({text:lat+'°N', lat, lon:LON0-1.9, y:0.06, cls:'tick'}));
// depth ruler down the western edge of the domain
const DEPTH_TICKS = [0,100,200,500,1000];
DEPTH_TICKS.forEach(d=>labelDefs.push({text:d+' m', lat:LAT1+0.9, lon:LON0-1.9, depth:d, cls:'tick'}));
// real named currents in this region — shown only while Current speed is the active variable
const currentLabelDefs = [
  {text:'West India Coastal Current', lat:13.5, lon:72.5, y:0.09, cls:'current'},
  {text:'East India Coastal Current', lat:13.5, lon:83.5, y:0.09, cls:'current'},
  {text:'Summer Monsoon Current', lat:6.3, lon:78.5, y:0.09, cls:'current'},
  {text:'Bay of Bengal Gyre', lat:18, lon:88, y:0.09, cls:'current'},
  {text:'Arabian Sea Eddy Field', lat:18.5, lon:65.5, y:0.09, cls:'current'},
];
currentLabelDefs.forEach(d=>{ d.onlyWhen='cur'; labelDefs.push(d); });
labelDefs.push({text:'Sample cyclone track', lat:9.6, lon:89.6, y:0.12, cls:'current', onlyWhen:'tchp'});
labelDefs.push({text:'landfall', lat:18.3, lon:83.1, y:0.34, cls:'current', onlyWhen:'tchp'});
const labelLayerEl = document.getElementById('labelLayer');
const labelEls = labelDefs.map(d=>{
  const el = document.createElement('div');
  el.className = 'geoLabel '+d.cls;
  el.textContent = d.text;
  labelLayerEl.appendChild(el);
  const [x,z] = toXZ(d.lat, d.lon);
  return {el, pos:new THREE.Vector3(x, d.y!==undefined?d.y:0, z), onlyWhen:d.onlyWhen, depth:d.depth};
});
// depth-anchored labels must follow the vertical exaggeration slider
function updateDepthLabelPositions(){
  labelEls.forEach(L=>{
    if(L.depth!==undefined) L.pos.y = -L.depth*DEPTH_SCALE*state.exagg;
  });
}
const labelRay = new THREE.Raycaster();
let labelFrame = 0;
function updateLabels(){
  const rect = viewport.getBoundingClientRect();
  labelFrame++;
  const doOcclusion = (labelFrame % 4 === 0);
  labelEls.forEach(L=>{
    if((L.onlyWhen && L.onlyWhen!==state.variable) || (L.visibleIf && !L.visibleIf())){ L.el.style.display='none'; return; }
    const p = L.pos.clone().project(camera);
    const inView = p.z < 1 && p.x>-1.1 && p.x<1.1 && p.y>-1.1 && p.y<1.1;
    L.el.style.display = inView ? 'block' : 'none';
    if(inView){
      L.el.style.left = ((p.x*0.5+0.5)*rect.width)+'px';
      L.el.style.top = ((-p.y*0.5+0.5)*rect.height)+'px';
      if(doOcclusion && occluders.length){
        const dir = L.pos.clone().sub(camera.position);
        const dist = dir.length();
        labelRay.set(camera.position, dir.normalize());
        const hit = labelRay.intersectObjects(occluders, false);
        L.el.classList.toggle('occluded', hit.length>0 && hit[0].distance < dist-0.05);
      }
    }
  });
  const compassSvg = document.querySelector('#compass svg');
  if(compassSvg) compassSvg.style.transform = 'rotate('+(camAz*180/Math.PI)+'deg)';
  // orientation hint flips when the camera orbits behind the coastline, so
  // "which side is which" is never ambiguous to a viewer
  const hint = document.getElementById('orientHint');
  if(hint){
    const rightIsEast = Math.cos(camAz) > 0;
    hint.innerHTML = rightIsEast
      ? 'ARABIAN SEA <b>◀ W</b><br>BAY OF BENGAL <b>E ▶</b>'
      : 'BAY OF BENGAL <b>◀ E</b><br>ARABIAN SEA <b>W ▶</b>';
  }
}
