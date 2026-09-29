/* ---- current arrows (slice mode, variable=cur): one instanced cone set and one line set ---- */
const ARROW_MAX = 400;
const arrowGroup = new THREE.Group(); scene.add(Render.register('arrows', arrowGroup));
const arrowCones = new THREE.InstancedMesh(new THREE.CylinderGeometry(0, 0.5, 1, 6, 1).translate(0, -0.5, 0), Render.flat(0xffffff), ARROW_MAX);
arrowCones.instanceColor = new THREE.InstancedBufferAttribute(new Float32Array(ARROW_MAX*3), 3);
arrowCones.count = 0;
arrowCones.frustumCulled = false;
const arrowShafts = new THREE.LineSegments(new THREE.BufferGeometry(), Render.vertexColoured({lines:true}));
arrowGroup.add(arrowCones, arrowShafts);
const _q = new THREE.Quaternion(), _s = new THREE.Vector3(), _m = new THREE.Matrix4(), _yUp = new THREE.Vector3(0,1,0);
function buildArrows(depth){
  const shaft = [], shaftCol = [];
  let n = 0;
  gridPts.forEach((p,idx)=>{
    if(p.i % 3 || p.j % 4 || n >= ARROW_MAX) return;   // subsample to roughly 1.5° × 2°
    if(gridLand[idx] || depth > gridSeabed[idx]) return; // water only
    const {u,v} = fieldCurrentUV(p.lat, p.lon, depth, state.time);
    const speed = Math.sqrt(u*u+v*v);
    if(speed === 0) return;
    const dir = new THREE.Vector3(u, 0, -v).normalize();  // northward flow points to −z
    const sN = Math.min(1, speed/VAR_META.cur.defMax), len = 0.25 + sN*1.15, head = len*0.35;
    const [x,z] = toXZ(p.lat, p.lon);
    const y = -depth * DEPTH_SCALE * state.exagg + 0.03;
    const c = Render.gpuColor(paletteColor(state.palette, sN));
    shaft.push(x, y, z, x + dir.x*(len-head), y, z + dir.z*(len-head));
    shaftCol.push(...c, ...c);
    _q.setFromUnitVectors(_yUp, dir);
    _m.compose(new THREE.Vector3(x + dir.x*len, y, z + dir.z*len), _q, _s.set(len*0.22, head, len*0.22));
    arrowCones.setMatrixAt(n, _m);
    arrowCones.instanceColor.setXYZ(n, c[0], c[1], c[2]);
    n++;
  });
  arrowCones.count = n;
  arrowCones.instanceMatrix.needsUpdate = true;
  arrowCones.instanceColor.needsUpdate = true;
  arrowShafts.geometry.dispose();
  arrowShafts.geometry = new THREE.BufferGeometry();
  arrowShafts.geometry.setAttribute('position', new THREE.Float32BufferAttribute(shaft, 3));
  arrowShafts.geometry.setAttribute('color', new THREE.Float32BufferAttribute(shaftCol, 3));
}

/* ---- current streaks: directional particles advected by the model's velocity field ----
   Current-speed view only (plan e), in the speed colours of the key. */
const FLOW_N = 340;
const FLOW_SPEED = 2.6; // visual speed multiplier, illustrative, not physically scaled
const flowLat = new Float32Array(FLOW_N);
const flowLon = new Float32Array(FLOW_N);
function respawnParticle(i){
  flowLat[i] = LAT0 + Math.random()*(LAT1-LAT0);
  flowLon[i] = LON0 + Math.random()*(LON1-LON0);
}
for(let i=0;i<FLOW_N;i++) respawnParticle(i);
// 3 vertices (one triangle) per particle: a comet-style streak pointing downstream; RGBA colours
const flowGeo = new THREE.BufferGeometry();
flowGeo.setAttribute('position', new THREE.BufferAttribute(new Float32Array(FLOW_N*9), 3));
flowGeo.setAttribute('color', new THREE.BufferAttribute(new Float32Array(FLOW_N*12), 4));
const flowMesh = new THREE.Mesh(flowGeo, Render.vertexColoured({opacity:0.999}));
flowMesh.renderOrder = Render.ORDER.particles;
flowMesh.frustumCulled = false;
scene.add(Render.register('particles', flowMesh));

function currentFlowDepth(){
  return state.mode==='slice' ? DEPTHS[state.depthIdx] : 0;
}
function applyFlowVisibility(){
  flowMesh.visible = state.showFlow && state.variable==='cur';
  if(flowMesh.visible) updateFlowParticles(0);
}
const _dir = new THREE.Vector3(), _perp = new THREE.Vector3(), _up = new THREE.Vector3(0,1,0);
function updateFlowParticles(dt){
  if(!flowMesh.visible) return;
  const depth = currentFlowDepth();
  const kFlow = FLOW_SPEED*0.36/VAR_META.cur.defMax;   // same on-screen pace whatever the source's current speeds
  const pos = flowGeo.getAttribute('position').array, col = flowGeo.getAttribute('color').array;
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
    const speedN = Math.min(1, Math.sqrt(u*u+v*v)/VAR_META.cur.defMax);  // matches the current-speed colour bar

    _dir.set(u, 0, -v);   // northward flow points to −z, same as the arrows
    const dl = _dir.length();
    if(dl < 1e-5) _dir.set(1,0,0); else _dir.divideScalar(dl);
    _perp.crossVectors(_dir, _up).normalize();
    const len = 0.16 + speedN*0.5, halfW = 0.028 + speedN*0.03;
    const noseX = x + _dir.x*len*0.6, noseZ = z + _dir.z*len*0.6;
    const tailX = x - _dir.x*len*0.4, tailZ = z - _dir.z*len*0.4;
    const p = i*9;
    pos[p]=noseX; pos[p+1]=y; pos[p+2]=noseZ;
    pos[p+3]=tailX+_perp.x*halfW; pos[p+4]=y; pos[p+5]=tailZ+_perp.z*halfW;
    pos[p+6]=tailX-_perp.x*halfW; pos[p+7]=y; pos[p+8]=tailZ-_perp.z*halfW;

    const c = Render.gpuColor(paletteColor(state.palette, speedN)), aNose = 0.95, aTail = 0.12;
    const q = i*12;
    col[q]=c[0]; col[q+1]=c[1]; col[q+2]=c[2]; col[q+3]=aNose;
    for(const o of [4, 8]){ col[q+o]=c[0]; col[q+o+1]=c[1]; col[q+o+2]=c[2]; col[q+o+3]=aTail; }
  }
  flowGeo.getAttribute('position').needsUpdate = true;
  flowGeo.getAttribute('color').needsUpdate = true;
}
