/* ---- current arrows (slice mode, variable=cur) ---- */
let arrowGroup = new THREE.Group(); scene.add(arrowGroup);
function buildArrows(depth){
  // dispose before clearing: buildArrows runs on every time step during playback
  arrowGroup.children.forEach(a=>{
    if(a.line){ a.line.geometry.dispose(); a.line.material.dispose(); }
    if(a.cone){ a.cone.geometry.dispose(); a.cone.material.dispose(); }
  });
  arrowGroup.clear();
  gridPts.forEach((p,idx)=>{
    if(p.i % 3 || p.j % 4) return; // subsample to roughly 1.5° × 2°
    if(gridLand[idx] || depth > gridSeabed[idx]) return; // water only
    const {u,v} = fieldCurrentUV(p.lat, p.lon, depth, state.time);
    const speed = Math.sqrt(u*u+v*v);
    const dir = new THREE.Vector3(u, 0, -v).normalize();
    if(dir.lengthSq()===0) return;
    const sN = Math.min(1, speed/VAR_META.cur.defMax), len = 0.25 + sN*1.15;
    const [x,z] = toXZ(p.lat, p.lon);
    const y = -depth * DEPTH_SCALE * state.exagg + 0.03;
    const col = paletteColor('speed', sN);
    const arrow = new THREE.ArrowHelper(dir, new THREE.Vector3(x,y,z), len, new THREE.Color(col[0],col[1],col[2]).getHex(), len*0.35, len*0.22);
    arrow.line.material.toneMapped = false;
    arrow.cone.material.toneMapped = false;
    arrowGroup.add(arrow);
  });
}

/* ---- live current particles: directional streaks advected by the real velocity field ---- */
const FLOW_N = 340;
const FLOW_SPEED = 2.6; // visual speed multiplier — illustrative, not physically scaled
const flowLat = new Float32Array(FLOW_N);
const flowLon = new Float32Array(FLOW_N);
function respawnParticle(i){
  flowLat[i] = LAT0 + Math.random()*(LAT1-LAT0);
  flowLon[i] = LON0 + Math.random()*(LON1-LON0);
}
for(let i=0;i<FLOW_N;i++) respawnParticle(i);
// 3 vertices (one triangle) per particle: a comet-style streak pointing downstream
const flowGeo = new THREE.BufferGeometry();
const flowPositions = new Float32Array(FLOW_N*9);
const flowColors = new Float32Array(FLOW_N*9);
flowGeo.setAttribute('position', new THREE.BufferAttribute(flowPositions,3));
flowGeo.setAttribute('color', new THREE.BufferAttribute(flowColors,3));
const flowMat = new THREE.MeshBasicMaterial({vertexColors:true, transparent:true, opacity:0.92,
  blending:THREE.AdditiveBlending, depthWrite:false, side:THREE.DoubleSide, toneMapped:false});
const flowMesh = new THREE.Mesh(flowGeo, flowMat);
scene.add(flowMesh);

function currentFlowDepth(){
  return state.mode==='slice' ? DEPTHS[state.depthIdx] : 0;
}
const _dir = new THREE.Vector3(), _perp = new THREE.Vector3(), _up = new THREE.Vector3(0,1,0);
function updateFlowParticles(dt){
  if(!state.showFlow || state.variable==='tchp') return;
  const depth = currentFlowDepth();
  const lightAtten = Math.max(0.32, 1 - depth/1300);
  const kFlow = FLOW_SPEED*0.36/VAR_META.cur.defMax;   // same on-screen pace whatever the source's current speeds
  const posAttr = flowGeo.getAttribute('position');
  const colAttr = flowGeo.getAttribute('color');
  for(let i=0;i<FLOW_N;i++){
    const {u,v} = fieldCurrentUV(flowLat[i], flowLon[i], depth, state.time);
    flowLon[i] += u*kFlow*dt;
    flowLat[i] += v*kFlow*dt;
    if(flowLon[i]<LON0 || flowLon[i]>LON1 || flowLat[i]<LAT0 || flowLat[i]>LAT1
       || isLand(flowLat[i], flowLon[i]) || depth > bathyDepth(flowLat[i], flowLon[i])){
      respawnParticle(i);
    }
    const [x,z] = toXZ(flowLat[i], flowLon[i]);
    const y = -depth*DEPTH_SCALE*state.exagg + 0.035;
    const speed = Math.sqrt(u*u+v*v);
    const speedN = Math.min(1, speed/VAR_META.cur.defMax);  // matches the current-speed colour bar

    _dir.set(u, 0, -v);   // northward flow points to −z, same as the arrows
    const dl = _dir.length();
    if(dl < 1e-5) _dir.set(1,0,0); else _dir.divideScalar(dl);
    _perp.crossVectors(_dir, _up).normalize();

    const len = 0.16 + speedN*0.5;
    const halfW = 0.028 + speedN*0.03;
    const noseX = x + _dir.x*len*0.6, noseZ = z + _dir.z*len*0.6;
    const tailBaseX = x - _dir.x*len*0.4, tailBaseZ = z - _dir.z*len*0.4;
    const t0 = i*9, t1 = t0+3, t2 = t0+6;
    posAttr.array[t0]=noseX; posAttr.array[t0+1]=y; posAttr.array[t0+2]=noseZ;
    posAttr.array[t1]=tailBaseX+_perp.x*halfW; posAttr.array[t1+1]=y; posAttr.array[t1+2]=tailBaseZ+_perp.z*halfW;
    posAttr.array[t2]=tailBaseX-_perp.x*halfW; posAttr.array[t2+1]=y; posAttr.array[t2+2]=tailBaseZ-_perp.z*halfW;

    const c = paletteColor('speed', speedN);
    const noseR=c[0]*lightAtten, noseG=c[1]*lightAtten, noseB=c[2]*lightAtten;
    const tailR=noseR*0.12, tailG=noseG*0.12, tailB=noseB*0.12;
    colAttr.array[t0]=noseR; colAttr.array[t0+1]=noseG; colAttr.array[t0+2]=noseB;
    colAttr.array[t1]=tailR; colAttr.array[t1+1]=tailG; colAttr.array[t1+2]=tailB;
    colAttr.array[t2]=tailR; colAttr.array[t2+1]=tailG; colAttr.array[t2+2]=tailB;
  }
  posAttr.needsUpdate = true;
  colAttr.needsUpdate = true;
}
