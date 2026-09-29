// true lat/lon graticule (2.5° minor, 5° major) drawn just above the sea surface: two line sets
const graticule = new THREE.Group();
(function buildGraticule(){
  const major = [], minor = [];
  const seg = (list, pts)=>{ for(let k=0;k<pts.length-1;k++) list.push(pts[k], pts[k+1]); };
  for(let lon=Math.ceil(LON0/2.5)*2.5; lon<=LON1; lon+=2.5){
    const pts=[]; for(let lat=LAT0; lat<=LAT1; lat+=1){ const [x,z]=toXZ(lat,lon); pts.push(new THREE.Vector3(x,0.02,z)); }
    seg(Math.abs(lon % 5) < 0.01 ? major : minor, pts);
  }
  for(let lat=Math.ceil(LAT0/2.5)*2.5; lat<=LAT1; lat+=2.5){
    const pts=[]; for(let lon=LON0; lon<=LON1; lon+=1){ const [x,z]=toXZ(lat,lon); pts.push(new THREE.Vector3(x,0.02,z)); }
    seg(Math.abs(lat % 5) < 0.01 ? major : minor, pts);
  }
  [[major, 0x2e5c78, 0.55], [minor, 0x1a3346, 0.3]].forEach(([pts, hex, opacity])=>{
    const lines = new THREE.LineSegments(new THREE.BufferGeometry().setFromPoints(pts), Render.line(hex, {opacity}));
    lines.renderOrder = Render.ORDER.surfaceLines;
    graticule.add(lines);
  });
})();
scene.add(Render.register('graticule', graticule));

// domain bounding box (subtle)
const boxGeo = new THREE.BoxGeometry(
  (LON1-LON0)*SCALE_XZ, 6, (LAT1-LAT0)*SCALE_XZ);
const boxEdges = new THREE.EdgesGeometry(boxGeo);
const boxLines = new THREE.LineSegments(boxEdges, Render.line(0x1b3a4f, {opacity:0.35}));
boxLines.renderOrder = Render.ORDER.underLines;
boxLines.position.y = -3;
scene.add(Render.register('box', boxLines));
// box was authored for the default 3x exaggeration; rescale it when that changes (updateDepthGeometry)
function positionBox(){
  const k = state.exagg/3;
  boxLines.scale.y = k;
  boxLines.position.y = -3*k;
}

const floorGeo = new THREE.PlaneGeometry(          // 0.25° facets: real ridges and the shelf break show
  (LON1-LON0)*SCALE_XZ, (LAT1-LAT0)*SCALE_XZ, 124, 64);
floorGeo.rotateX(-Math.PI/2);
const floorPos = floorGeo.attributes.position;
const floorLatLon = [];
for(let i=0;i<floorPos.count;i++){
  const lon = floorPos.getX(i)/SCALE_XZ + (LON0+LON1)/2;
  const lat = -floorPos.getZ(i)/SCALE_XZ + (LAT0+LAT1)/2;   // inverse of toXZ
  floorLatLon.push([lat, lon, bathyDepth(lat, lon)]);
}
const floorColors = new Float32Array(floorPos.count*3);
function shadeFloor(){
  for(let i=0;i<floorPos.count;i++){
    const dep = floorLatLon[i][2];
    // shallow shelf reads sandy-olive, deep basin reads cold blue-grey
    const t = Math.min(1, dep/3200);
    const r = lerp(0.30, 0.055, t), g = lerp(0.30, 0.075, t), b = lerp(0.20, 0.105, t);
    floorColors[i*3]=r; floorColors[i*3+1]=g; floorColors[i*3+2]=b;
  }
  floorGeo.setAttribute('color', new THREE.Float32BufferAttribute(floorColors,3));
}
shadeFloor();
const floorMesh = new THREE.Mesh(floorGeo, Render.terrain({vertexColors:true, roughness:0.97, metalness:0.03}));
scene.add(Render.register('floor', floorMesh));
// called from updateDepthGeometry() when the vertical exaggeration changes
function positionFloor(){
  for(let i=0;i<floorPos.count;i++){
    floorPos.setY(i, -displayDepth(floorLatLon[i][2])*DEPTH_SCALE*state.exagg);
  }
  floorPos.needsUpdate = true;
  floorGeo.computeVertexNormals();
}

/* ---- land from ETOPO1: marching squares at sea level, so the coast is smooth ----
   Each 0.1° cell contributes its land part (corners above sea level plus the points
   where its edges cross 0 m). A low wall and a sandy line run along the coast, and
   relief is exaggerated gently so the Western Ghats read. */
const occluders = [];   // meshes that can hide screen-space labels
(function buildLand(){
  const {g, a} = ETOPO, N = g.nlat, M = g.nlon;
  const alt = (i,j)=>a[i*M+j];
  const landY = h => 0.1 + Math.max(0,h)*0.00005;
  const shade = h => { const t = Math.min(1, Math.max(0,h)/1800); return [lerp(0.56,0.46,t), lerp(0.49,0.40,t), lerp(0.32,0.34,t)]; };
  const vert = (lat, lon, h)=>{ const [x,z] = toXZ(lat, lon); return {x, y:landY(h), z, h}; };
  const corner = (i,j)=>vert(g.lat0+i*g.step, g.lon0+j*g.step, alt(i,j));
  const cross = (i0,j0,i1,j1)=>{                 // where altitude crosses 0 m along a cell edge
    const h0 = alt(i0,j0), h1 = alt(i1,j1), f = h0/(h0-h1);
    return vert(g.lat0+(i0+(i1-i0)*f)*g.step, g.lon0+(j0+(j1-j0)*f)*g.step, 0);
  };
  const top = [], topCol = [], wall = [], coast = [];
  const tri = (p,q,r)=>[p,q,r].forEach(v=>{ top.push(v.x, v.y, v.z); topCol.push(...shade(v.h)); });
  const poly = P=>{ for(let k=1;k<P.length-1;k++) tri(P[0], P[k], P[k+1]); };
  const edge = (p,q)=>{
    coast.push(p.x, p.y+0.002, p.z, q.x, q.y+0.002, q.z);
    wall.push(p.x,-0.05,p.z, q.x,-0.05,q.z, q.x,q.y,q.z,  p.x,-0.05,p.z, q.x,q.y,q.z, p.x,p.y,p.z);
  };
  for(let i=0;i<N-1;i++){
    for(let j=0;j<M-1;j++){
      const C = [[i,j],[i,j+1],[i+1,j+1],[i+1,j]];
      const L = C.map(([ci,cj])=>alt(ci,cj) > 0);
      const nLand = L.filter(Boolean).length;
      if(nLand===0) continue;
      const cv = C.map(([ci,cj])=>corner(ci,cj));
      if(nLand===4){ tri(cv[0],cv[1],cv[2]); tri(cv[0],cv[2],cv[3]); continue; }
      if(nLand===2 && L[0]===L[2]){
        // saddle: two opposite corners are land; join them only if the cell centre is land too
        const joined = (alt(i,j)+alt(i,j+1)+alt(i+1,j+1)+alt(i+1,j))/4 > 0;
        const x01 = cross(...C[0],...C[1]), x12 = cross(...C[1],...C[2]), x23 = cross(...C[2],...C[3]), x30 = cross(...C[3],...C[0]);
        if(L[0]){
          if(joined){ poly([cv[0],x01,x12,cv[2],x23,x30]); edge(x01,x12); edge(x23,x30); }
          else { tri(cv[0],x01,x30); tri(cv[2],x23,x12); edge(x01,x30); edge(x23,x12); }
        } else {
          if(joined){ poly([x01,cv[1],x12,x23,cv[3],x30]); edge(x01,x30); edge(x12,x23); }
          else { tri(x01,cv[1],x12); tri(x23,cv[3],x30); edge(x01,x12); edge(x23,x30); }
        }
        continue;
      }
      // one or three land corners, or two adjacent ones: walk round adding the crossings
      const P = [], X = [];
      for(let k=0;k<4;k++){
        const n = (k+1)%4;
        if(L[k]) P.push(cv[k]);
        if(L[k]!==L[n]){ const x = cross(...C[k],...C[n]); P.push(x); X.push(x); }
      }
      poly(P);
      if(X.length===2) edge(X[0], X[1]);
    }
  }
  const geom = (arr, cols)=>{
    const geo = new THREE.BufferGeometry();
    geo.setAttribute('position', new THREE.Float32BufferAttribute(arr, 3));
    if(cols) geo.setAttribute('color', new THREE.Float32BufferAttribute(cols, 3));
    geo.computeVertexNormals();
    return geo;
  };
  const land = new THREE.Group();
  land.add(new THREE.Mesh(geom(top, topCol), Render.terrain({vertexColors:true, roughness:0.9, side:THREE.DoubleSide})));
  land.add(new THREE.Mesh(geom(wall), Render.terrain({color:0x6f6142, side:THREE.DoubleSide})));
  scene.add(Render.register('land', land));
  const coastLine = new THREE.LineSegments(new THREE.BufferGeometry().setAttribute('position', new THREE.Float32BufferAttribute(coast, 3)),
    Render.line(0xdfcd94, {opacity:0.7}));
  coastLine.renderOrder = Render.ORDER.surfaceLines;
  scene.add(Render.register('coast', coastLine));

  // labels test occlusion against a coarse, invisible copy (every 0.4°), which is far cheaper to raycast
  const occ = [];
  for(let i=0;i<N-4;i+=4){
    for(let j=0;j<M-4;j+=4){
      if(alt(i,j)>0 && alt(i,j+4)>0 && alt(i+4,j+4)>0 && alt(i+4,j)>0){
        const q = [[i,j],[i,j+4],[i+4,j+4],[i+4,j]].map(([ci,cj])=>{ const v = corner(ci,cj); return [v.x, v.y, v.z]; });
        occ.push(...q[0], ...q[1], ...q[2], ...q[0], ...q[2], ...q[3]);
      }
    }
  }
  const occMesh = new THREE.Mesh(geom(occ), Render.flat(0x000000, {side:THREE.DoubleSide}));
  occMesh.visible = false;
  occluders.push(occMesh);
})();
