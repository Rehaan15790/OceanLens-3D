/* ---- layer meshes: one plane per model level, coloured by value through a palette ramp ---- */
function gridIndices(){
  const indices = [];
  for(let i=0;i<LAT_N-1;i++){
    for(let j=0;j<LON_N-1;j++){
      const a=i*LON_N+j, b=a+1, c=a+LON_N, e=c+1;
      indices.push(a,c,b, b,c,e);
    }
  }
  return indices;
}
function gridGeometry(y){
  const geo = new THREE.BufferGeometry();
  geo.setAttribute('position', new THREE.Float32BufferAttribute(gridPts.flatMap(p=>{ const [x,z]=toXZ(p.lat,p.lon); return [x,y,z]; }), 3));
  geo.setIndex(gridIndices());
  return geo;
}
const layerMeshes = [];
const layerGroup = new THREE.Group();
DEPTHS.forEach((depth, k)=>{
  const mesh = new THREE.Mesh(gridGeometry(0),
    Render.dataSurface({palette:state.palette, min:state.cbMin, max:state.cbMax, scale:state.scale, masked:true, opaque:false, opacity:state.opacity}));
  mesh.renderOrder = Render.layerOrder(k, DEPTHS.length);   // deepest first; the camera is always above the sea
  mesh.userData.depth = depth;
  layerGroup.add(mesh);
  layerMeshes.push(mesh);
});
scene.add(Render.register('layers', layerGroup));

/* ---- isosurface: the depth where each water column first crosses the threshold ----
   One surface point per column (a 2.5-D isosurface, which is exactly what isotherm
   depths such as D20/D26 are). A column that never crosses above the seabed is left
   as a gap rather than guessed, and the surface never dips below the sea floor. */
// `at(z)` gives the column's value at depth z; walks the model levels down to the seabed
function firstCrossing(at, seabed, target){
  let v0 = at(DEPTHS[0]);
  for(let k=0; k<DEPTHS.length-1; k++){
    const z0 = DEPTHS[k];
    if(z0 >= seabed) return null;
    const z1 = Math.min(DEPTHS[k+1], seabed);
    const v1 = at(z1);
    if((v0-target)*(v1-target) <= 0 && v0!==v1) return lerp(z0, z1, (target-v0)/(v1-v0));
    v0 = v1;
  }
  return null;
}
function isoCrossDepth(varKey, lat, lon, seabed, target, t){
  return firstCrossing(z=>getVal(varKey, lat, lon, z, t), seabed, target);
}
// asNeutral: the D26 surface under the TCHP map, drawn in one translucent colour so the key stays about heat
function buildIsoMesh(varKey, target, asNeutral){
  const cross = gridPts.map((p,idx)=> gridLand[idx] ? null
    : isoCrossDepth(varKey, p.lat, p.lon, gridSeabed[idx], target, state.time));
  const valid = cross.filter(z=>z!==null);
  const info = {n:valid.length, lo:0, hi:1000, min:NaN, max:NaN, mean:NaN};
  if(valid.length){
    info.min = Math.min(...valid); info.max = Math.max(...valid);
    info.mean = valid.reduce((a,b)=>a+b,0)/valid.length;
    // colour range fitted to the surface itself, on round numbers, so the depth colour bar reads cleanly
    info.lo = Math.floor(info.min/10)*10;
    info.hi = Math.max(info.lo+10, Math.ceil(info.max/10)*10);
  }
  const geo = new THREE.BufferGeometry();
  geo.setAttribute('position', new THREE.Float32BufferAttribute(gridPts.flatMap((p,idx)=>{
    const [x,z] = toXZ(p.lat, p.lon);
    return [x, -(cross[idx]===null ? 0 : cross[idx])*DEPTH_SCALE*state.exagg, z];
  }), 3));
  const indices = [];
  for(let i=0;i<LAT_N-1;i++){
    for(let j=0;j<LON_N-1;j++){
      const a=i*LON_N+j, b=a+1, c=a+LON_N, e=c+1;
      // only quads whose four corners all cross: land, shelf and no-crossing columns stay open
      if(cross[a]===null||cross[b]===null||cross[c]===null||cross[e]===null) continue;
      indices.push(a,c,b, b,c,e);
    }
  }
  geo.setIndex(indices);
  let mesh;
  if(asNeutral){
    mesh = new THREE.Mesh(geo, Render.flat(0x78bed7, {opacity:0.4, side:THREE.DoubleSide}));
    mesh.renderOrder = Render.ORDER.translucent;
  } else {
    Render.setValues(geo, cross.map(z=>z===null ? 0 : z));
    mesh = new THREE.Mesh(geo, Render.dataSurface({palette:'depth', min:info.lo, max:info.hi}));
  }
  return {mesh, info};
}
function disposeMesh(m){
  if(!m) return;
  scene.remove(m); m.geometry.dispose();
  if(m.material.map) m.material.map.dispose();
  m.material.dispose();
}
let isoMesh = null, isoInfo = null;
function buildIsoGeometry(){
  disposeMesh(isoMesh);
  const built = buildIsoMesh(state.variable, state.isoVal, false);
  isoMesh = built.mesh; isoInfo = built.info;
  scene.add(Render.register('iso', isoMesh));
}

/* ---- cyclone heat potential: TCHP map at the surface, D26 surface beneath ----
   The map lies on the sea surface like the 0 m layer, so the graticule (0.02) and the floats (0.05) stay above it. */
const tchpMesh = new THREE.Mesh(gridGeometry(0),
  Render.dataSurface({palette:VAR_META.tchp.palette, min:VAR_META.tchp.defMin, max:VAR_META.tchp.defMax, masked:true}));
tchpMesh.visible = false;
scene.add(Render.register('tchp', tchpMesh));
let d26Mesh = null, tchpContour = null;

// marching squares on the model grid: line segments where `vals` crosses `level`
function contourSegments(vals, level){
  const pts = [];
  const edge = (a,b)=>{
    const va = vals[a], vb = vals[b];
    if((va >= level) === (vb >= level)) return null;
    const f = (level-va)/(vb-va);
    const [x0,z0] = toXZ(gridPts[a].lat, gridPts[a].lon), [x1,z1] = toXZ(gridPts[b].lat, gridPts[b].lon);
    return [lerp(x0,x1,f), lerp(z0,z1,f)];
  };
  for(let i=0;i<LAT_N-1;i++){
    for(let j=0;j<LON_N-1;j++){
      const a=i*LON_N+j, b=a+1, c=a+LON_N, e=c+1;
      if(vals[a]===null||vals[b]===null||vals[c]===null||vals[e]===null) continue;
      const hits = [edge(a,b), edge(b,e), edge(e,c), edge(c,a)].filter(Boolean);
      for(let k=0;k+1<hits.length;k+=2) pts.push(hits[k], hits[k+1]);
    }
  }
  return pts;
}
function updateTchpView(){
  const g = tchpGrid(state.time);
  const mask = g.map(c=>c && c.q!==null ? 1 : 0);
  Render.setValues(tchpGeo(), Render.fillMasked(g.map(c=>c ? c.q : NaN), mask, LON_N, LAT_N), mask);
  Render.setPalette(tchpMesh.material, state.palette);
  Render.setRange(tchpMesh.material, state.cbMin, state.cbMax, state.scale);
  // D26 underneath, drawn neutral so the colour bar stays about heat
  disposeMesh(d26Mesh);
  d26Mesh = buildIsoMesh('temp', 26, true).mesh;
  scene.add(Render.register('d26', d26Mesh));
  disposeMesh(tchpContour);
  const segs = contourSegments(g.map(c=>c && c.q!==null ? c.q : null), TCHP_THRESHOLD);
  tchpContour = new THREE.LineSegments(new THREE.BufferGeometry().setFromPoints(segs.map(([x,z])=>new THREE.Vector3(x,0.07,z))),
    Render.line(0xffffff, {opacity:0.95}));
  tchpContour.renderOrder = Render.ORDER.surfaceLines;
  scene.add(Render.register('tchpContour', tchpContour));
}
function tchpGeo(){ return tchpMesh.geometry; }
// other map-only fields (2D products): the same surface, no D26 and no threshold line
function updateField2dView(){
  const g = field2dGrid(state.variable, state.time);
  const mask = g.map(v=>v!==null && Number.isFinite(v) ? 1 : 0);
  Render.setValues(tchpGeo(), Render.fillMasked(g.map(v=>v===null ? NaN : v), mask, LON_N, LAT_N), mask);
  Render.setPalette(tchpMesh.material, state.palette);
  Render.setRange(tchpMesh.material, state.cbMin, state.cbMax, state.scale);
}

/* ============================================================
   FIELD VALUES -> LAYERS
   ============================================================ */
// masks: land, and anywhere below the seabed (node masks from geo.js)
function layerMask(depth){ return gridPts.map((p,idx)=> gridLand[idx] || depth > gridSeabed[idx] ? 0 : 1); }
// the samples of the last recolouring, per level: the 3D isosurface reuses them
let layerField = null;
const fieldKey = v => dataMode+'|'+v+'|'+state.time;
const isMapOnly = v => v==='tchp' || (VAR_META[v] && VAR_META[v].dims===2);
const hasField = v => typeof hasModel!=='function' || hasModel(v);
function updateLayerColors(){
  if(isMapOnly(state.variable) || !hasField(state.variable)) return;   // 2D fields: updateTchpView / updateField2dView
  const levels = [];
  layerMeshes.forEach(mesh=>{
    const depth = mesh.userData.depth, mask = layerMask(depth);
    const vals = gridPts.map(p=>getVal(state.variable, p.lat, p.lon, depth, state.time));
    vals.forEach((v, idx)=>{ if(!Number.isFinite(v)) mask[idx] = 0; });   // no value: a gap, never a filled-in colour
    levels.push(vals);
    Render.setValues(mesh.geometry, Render.fillMasked(vals, mask, LON_N, LAT_N), mask);
    Render.setPalette(mesh.material, state.palette);
    Render.setRange(mesh.material, state.cbMin, state.cbMax, state.scale);
  });
  layerField = {key:fieldKey(state.variable), levels};
}

/* ---- what the view shows (decision f) ----
   surface: the 0 m layer opaque, the lower layers faint (opacity slider); layers: every level
   translucent; slice: one level, opaque; iso: the depth of a value, opaque; iso3d: the 3D isosurface,
   opaque. TCHP and 2D products: the map, opaque. A variable with no model field (chlorophyll in real
   mode) draws no field. While a section is open everything in front of the curtain turns translucent. */
function applyVolumeView(){
  if(typeof viewDescription === 'function') renderer.domElement.setAttribute('aria-label', viewDescription());
  const view = currentView(), tchp = view==='tchp', map2d = tchp || view==='field2d', mode = state.mode, sec = !!section;
  layerMeshes.forEach(m=>{ m.visible = false; });
  arrowGroup.visible = false;
  if(isoMesh) isoMesh.visible = false;
  iso3dMesh.visible = false;
  tchpMesh.visible = map2d;
  if(d26Mesh) d26Mesh.visible = tchp;
  if(tchpContour) tchpContour.visible = tchp;
  applyTrackVisibility();
  applyFlowVisibility();
  if(map2d){
    if(tchp) updateTchpView(); else updateField2dView();
    Render.setSurfaceRole(tchpMesh.material, !sec, 0.5);
  } else if(!hasField(state.variable)){
    if(isoMode()) isoInfo = null;
  } else if(mode==='surface' || mode==='layers'){
    layerMeshes.forEach((m, k)=>{
      m.visible = true;
      const opaqueTop = mode==='surface' && k===0 && !sec;
      Render.setSurfaceRole(m.material, opaqueTop, sec ? Math.min(state.opacity, 0.15) : state.opacity);
    });
  } else if(mode==='slice'){
    const depth = DEPTHS[state.depthIdx];
    layerMeshes.forEach(m=>{
      if(m.userData.depth !== depth) return;
      m.visible = true;
      Render.setSurfaceRole(m.material, !sec, 0.5);
    });
    if(state.variable==='cur'){
      arrowGroup.visible = true;
      buildArrows(depth);
    }
  } else if(mode==='iso'){
    buildIsoGeometry();
    isoMesh.visible = true;
    Render.setSurfaceRole(isoMesh.material, !sec, 0.5);
  } else if(mode==='iso3d'){
    updateIso3d();
    Render.setSurfaceRole(iso3dMesh.material, !sec, 0.5);
  }
}
// the opacity slider: the faint layers only (fast path, no recolouring)
function setLayerOpacity(v){
  layerMeshes.forEach(m=>{ if(m.material.transparent) m.material.opacity = section ? Math.min(v, 0.15) : v; });
}

/* ---- depth geometry: follows the vertical exaggeration slider (fast path) ---- */
let depthGeometryExagg = null;
function updateDepthGeometry(){
  if(state.exagg === depthGeometryExagg) return;
  depthGeometryExagg = state.exagg;
  layerMeshes.forEach(m=>{ m.position.y = -m.userData.depth*DEPTH_SCALE*state.exagg; });
  positionFloor();
  positionBox();
  updateDepthLabelPositions();
}

