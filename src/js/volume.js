/* ---- layer meshes (one flat plane per depth level) ---- */
const layerMeshes = [];
function buildLayers(){
  DEPTHS.forEach((depth)=>{
    const geo = new THREE.BufferGeometry();
    const positions = [];
    gridPts.forEach(p=>{
      const [x,z] = toXZ(p.lat, p.lon);
      positions.push(x, 0, z);
    });
    const indices = [];
    for(let i=0;i<LAT_N-1;i++){
      for(let j=0;j<LON_N-1;j++){
        const a=i*LON_N+j, b=a+1, c=a+LON_N, e=c+1;
        indices.push(a,c,b, b,c,e);
      }
    }
    geo.setAttribute('position', new THREE.Float32BufferAttribute(positions,3));
    geo.setIndex(indices);
    geo.setAttribute('color', new THREE.Float32BufferAttribute(new Array(LAT_N*LON_N*4).fill(0.5),4));
    geo.computeVertexNormals();
    const mat = new THREE.MeshBasicMaterial({vertexColors:true, transparent:true, opacity:0.5, side:THREE.DoubleSide, toneMapped:false});
    const mesh = new THREE.Mesh(geo, mat);
    mesh.userData.depth = depth;
    scene.add(mesh);
    layerMeshes.push(mesh);
  });
}
buildLayers();

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
function buildIsoMesh(varKey, target, colourByDepth){
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
  const positions = [], colors = [];
  gridPts.forEach((p,idx)=>{
    const [x,z] = toXZ(p.lat, p.lon);
    const d = cross[idx]===null ? 0 : cross[idx];
    positions.push(x, -d*DEPTH_SCALE*state.exagg, z);
    colors.push(...paletteColor('depth', (d-info.lo)/(info.hi-info.lo)));
  });
  const indices = [];
  for(let i=0;i<LAT_N-1;i++){
    for(let j=0;j<LON_N-1;j++){
      const a=i*LON_N+j, b=a+1, c=a+LON_N, e=c+1;
      // only quads whose four corners all cross: land, shelf and no-crossing columns stay open
      if(cross[a]===null||cross[b]===null||cross[c]===null||cross[e]===null) continue;
      indices.push(a,c,b, b,c,e);
    }
  }
  const geo = new THREE.BufferGeometry();
  geo.setAttribute('position', new THREE.Float32BufferAttribute(positions,3));
  geo.setAttribute('color', new THREE.Float32BufferAttribute(colors,3));
  geo.setIndex(indices);
  geo.computeVertexNormals();
  const mat = colourByDepth
    ? new THREE.MeshStandardMaterial({vertexColors:true, side:THREE.DoubleSide, transparent:true, opacity:0.9, metalness:0.1, roughness:0.6, toneMapped:false})
    : new THREE.MeshStandardMaterial({color:0x78bed7, side:THREE.DoubleSide, transparent:true, opacity:0.4, metalness:0.1, roughness:0.7, depthWrite:false});
  return {mesh:new THREE.Mesh(geo, mat), info};
}
function disposeMesh(m){ if(!m) return; scene.remove(m); m.geometry.dispose(); m.material.dispose(); }
let isoMesh = null, isoInfo = null;
function buildIsoGeometry(){
  disposeMesh(isoMesh);
  const built = buildIsoMesh(state.variable, state.isoVal, true);
  isoMesh = built.mesh; isoInfo = built.info;
  scene.add(isoMesh);
}

/* ---- cyclone heat potential: TCHP map at the surface, D26 surface beneath ---- */
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
const tchpGeo = new THREE.BufferGeometry();
tchpGeo.setAttribute('position', new THREE.Float32BufferAttribute(
  gridPts.flatMap(p=>{ const [x,z]=toXZ(p.lat,p.lon); return [x,0.04,z]; }), 3));
tchpGeo.setAttribute('color', new THREE.Float32BufferAttribute(new Array(gridPts.length*4).fill(0), 4));
tchpGeo.setIndex(gridIndices());
const tchpMesh = new THREE.Mesh(tchpGeo, new THREE.MeshBasicMaterial({vertexColors:true, transparent:true,
  opacity:0.92, side:THREE.DoubleSide, toneMapped:false, depthWrite:false}));
tchpMesh.visible = false;
scene.add(tchpMesh);
let d26Mesh = null, tchpContour = null;

// marching squares on the model grid: line segments where `vals` crosses `level`
function contourSegments(vals, level){
  const pts = [];
  const edge = (a,b)=>{
    const va = vals[a], vb = vals[b];
    if((va-level)*(vb-level) >= 0) return null;
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
  const col = tchpGeo.getAttribute('color');
  g.forEach((c,idx)=>{
    const rgb = paletteColor(state.palette, ((c?c.q:0)-state.cbMin)/(state.cbMax-state.cbMin));
    col.setXYZW(idx, rgb[0], rgb[1], rgb[2], c ? 1 : 0);
  });
  col.needsUpdate = true;
  // D26 underneath, drawn neutral so the colour bar stays about heat
  disposeMesh(d26Mesh);
  d26Mesh = buildIsoMesh('temp', 26, false).mesh;
  scene.add(d26Mesh);
  disposeMesh(tchpContour);
  const segs = contourSegments(g.map(c=>c?c.q:null), TCHP_THRESHOLD);
  const cgeo = new THREE.BufferGeometry().setFromPoints(segs.map(([x,z])=>new THREE.Vector3(x,0.07,z)));
  tchpContour = new THREE.LineSegments(cgeo, new THREE.LineBasicMaterial({color:0xffffff, transparent:true, opacity:0.95, toneMapped:false}));
  scene.add(tchpContour);
}

/* ============================================================
   FIELD -> COLOR UPDATE
   ============================================================ */
function updateLayerColors(){
  if(state.variable==='tchp') return;   // 2D field, drawn by updateTchpView()
  layerMeshes.forEach(mesh=>{
    const depth = mesh.userData.depth;
    const colAttr = mesh.geometry.getAttribute('color');
    gridPts.forEach((p,idx)=>{
      const v = getVal(state.variable, p.lat, p.lon, depth, state.time);
      const norm = (v - state.cbMin)/(state.cbMax - state.cbMin);
      const c = paletteColor(state.palette, norm);
      // hide the field over land and anywhere below the seabed
      const a = (gridLand[idx] || depth > gridSeabed[idx]) ? 0 : 1;
      colAttr.setXYZW(idx, c[0], c[1], c[2], a);
    });
    colAttr.needsUpdate = true;
    mesh.position.y = -depth * DEPTH_SCALE * state.exagg;
  });
}

function updateSurfaceRipple(elapsed){
  const surfMesh = layerMeshes[0]; // DEPTHS[0] === 0, the sea-surface layer
  if(!surfMesh || !surfMesh.visible) return;
  const posAttr = surfMesh.geometry.getAttribute('position');
  for(let idx=0; idx<gridPts.length; idx++){
    const x = posAttr.getX(idx), z = posAttr.getZ(idx);
    const wave = Math.sin(x*1.8 + elapsed*1.1)*0.012 + Math.cos(z*1.4 - elapsed*0.9)*0.012;
    posAttr.setY(idx, wave);
  }
  posAttr.needsUpdate = true;
  // surface layer uses an unlit vertex-color material, so normals aren't needed here
}
