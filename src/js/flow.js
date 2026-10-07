/* ---- current arrows (slice mode, variable=cur): one instanced cone set, flat shafts and a flat dark edge ----
   Achromatic, like the floats and the streaks, so the speed colours of the slice cannot hide them: white with a
   dark edge (for the light end of the palette); the length shows where the speed sits on the key. The edge is
   one flat outline of each arrow, a little larger all round, a hair below the shaft and the cone's widest
   section, so it shows only round them. */
const ARROW_MAX = 400;
const arrowGroup = new THREE.Group(); scene.add(Render.register('arrows', arrowGroup));
const arrowCones = new THREE.InstancedMesh(new THREE.CylinderGeometry(0, 0.5, 1, 6, 1).translate(0, -0.5, 0), Render.flat(0xf4f4f4), ARROW_MAX);
arrowCones.count = 0;
arrowCones.frustumCulled = false;
const arrowShafts = new THREE.Mesh(new THREE.BufferGeometry(), Render.flat(0xf4f4f4, {side:THREE.DoubleSide}));
const arrowEdges = new THREE.Mesh(new THREE.BufferGeometry(), Render.flat(0x0b0f13, {side:THREE.DoubleSide}));
arrowShafts.frustumCulled = arrowEdges.frustumCulled = false;
arrowGroup.add(arrowEdges, arrowCones, arrowShafts);
const _q = new THREE.Quaternion(), _s = new THREE.Vector3(), _m = new THREE.Matrix4(), _yUp = new THREE.Vector3(0,1,0);
// current arrows on the slice at this depth (slice mode, current speed only)
function buildArrows(depth){
  const shaft = [], edge = [], e = 0.03;
  let n = 0;
  gridPts.forEach((p,idx)=>{
    if(p.i % 3 || p.j % 4 || n >= ARROW_MAX) return;   // subsample to roughly 1.5° × 2°
    if(gridLand[idx] || depth > gridSeabed[idx]) return; // water only
    const {u,v} = fieldCurrentUV(p.lat, p.lon, depth, state.time);
    const speed = Math.sqrt(u*u+v*v);
    if(speed === 0) return;
    const dir = new THREE.Vector3(u, 0, -v).normalize();  // northward flow points to −z
    const sN = Render.rampPos(speed, state.cbMin, state.cbMax), len = 0.25 + sN*1.15, head = len*0.35;
    const [x,z] = toXZ(p.lat, p.lon);
    const y = -depth * DEPTH_SCALE * state.exagg + 0.03, w = 0.012 + 0.008*sN, r = len*0.11;
    // a point s along the flow and t to its side; a quad from s0 to s1, half-width h
    const at = (s, t, yy)=>[x + dir.x*s - dir.z*t, yy, z + dir.z*s + dir.x*t];
    const quad = (out, s0, s1, h, yy)=>out.push(...at(s0, h, yy), ...at(s0, -h, yy), ...at(s1, h, yy), ...at(s1, h, yy), ...at(s0, -h, yy), ...at(s1, -h, yy));
    quad(shaft, 0, len - head, w, y);
    quad(edge, -e, len - head, w + e, y - 0.003);
    edge.push(...at(len - head - e, r + 1.4*e, y - 0.003), ...at(len - head - e, -r - 1.4*e, y - 0.003), ...at(len + 2.5*e, 0, y - 0.003));
    _q.setFromUnitVectors(_yUp, dir);
    _m.compose(new THREE.Vector3(x + dir.x*len, y, z + dir.z*len), _q, _s.set(len*0.22, head, len*0.22));
    arrowCones.setMatrixAt(n, _m);
    n++;
  });
  arrowCones.count = n;
  arrowCones.instanceMatrix.needsUpdate = true;
  [[arrowShafts, shaft], [arrowEdges, edge]].forEach(([mesh, pos])=>{
    mesh.geometry.dispose();
    mesh.geometry = new THREE.BufferGeometry();
    mesh.geometry.setAttribute('position', new THREE.Float32BufferAttribute(pos, 3));
  });
}

/* ---- current streaks: directional particles advected by the model's velocity field ----
   Current-speed view only (plan e). Achromatic, like the floats, so the speed colours of the field under them
   cannot hide them: a white comet with a thin dark edge (for the light end of the palette), longer and more
   opaque the higher the speed sits on the key. */
const FLOW_N = 340;
const FLOW_SPEED = 2.6; // visual speed multiplier, illustrative, not physically scaled
const flowLat = new Float32Array(FLOW_N);
const flowLon = new Float32Array(FLOW_N);
// particle i to a random point; one on land or out of the water respawns on the next update
function respawnParticle(i){
  flowLat[i] = LAT0 + Math.random()*(LAT1-LAT0);
  flowLon[i] = LON0 + Math.random()*(LON1-LON0);
}
for(let i=0;i<FLOW_N;i++) respawnParticle(i);
// a comet-style streak pointing downstream: two triangles per particle, every dark edge first and then every
// white core over them (one draw, in order); RGBA colours
const flowGeo = new THREE.BufferGeometry();
flowGeo.setAttribute('position', new THREE.BufferAttribute(new Float32Array(FLOW_N*18), 3));
flowGeo.setAttribute('color', new THREE.BufferAttribute(new Float32Array(FLOW_N*24), 4));
const FLOW_RGB = [Render.gpuColor([0x0b/255, 0x0f/255, 0x13/255]), [1, 1, 1]];   // the dark edge, the white core
const flowMesh = new THREE.Mesh(flowGeo, Render.vertexColoured({opacity:0.999}));
flowMesh.renderOrder = Render.ORDER.particles;
flowMesh.frustumCulled = false;
scene.add(Render.register('particles', flowMesh));

function currentFlowDepth(){
  return state.mode==='slice' ? DEPTHS[state.depthIdx] : 0;
}
// streaks show for current speed only, with the overlay on
function applyFlowVisibility(){
  flowMesh.visible = state.showFlow && state.variable==='cur';
  if(flowMesh.visible) updateFlowParticles(0);
}
const _dir = new THREE.Vector3(), _perp = new THREE.Vector3(), _up = new THREE.Vector3(0,1,0);
// advect every particle by dt seconds and rewrite the streak geometry (scene.js calls this at 30 fps)
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
    const speedN = Render.rampPos(Math.sqrt(u*u+v*v), state.cbMin, state.cbMax);  // where the speed sits on the key

    _dir.set(u, 0, -v);   // northward flow points to −z, same as the arrows
    const dl = _dir.length();
    if(dl < 1e-5) _dir.set(1,0,0); else _dir.divideScalar(dl);
    _perp.crossVectors(_dir, _up).normalize();
    const len = 0.16 + speedN*0.5, halfW = 0.03 + speedN*0.035, aNose = 0.7 + 0.3*speedN;
    // k 0: the dark edge, a little larger all round; k 1: the white core
    for(let k=0;k<2;k++){
      const e = k ? 0 : 0.02, front = len*0.6 + 1.5*e, back = len*0.4 + e, w = halfW + e;
      const p = (k*FLOW_N + i)*9, q = (k*FLOW_N + i)*12, c = FLOW_RGB[k], a = k ? aNose : 0.6*aNose;
      pos[p]=x + _dir.x*front; pos[p+1]=y; pos[p+2]=z + _dir.z*front;
      pos[p+3]=x - _dir.x*back + _perp.x*w; pos[p+4]=y; pos[p+5]=z - _dir.z*back + _perp.z*w;
      pos[p+6]=x - _dir.x*back - _perp.x*w; pos[p+7]=y; pos[p+8]=z - _dir.z*back - _perp.z*w;
      col[q]=c[0]; col[q+1]=c[1]; col[q+2]=c[2]; col[q+3]=a;
      for(const o of [4, 8]){ col[q+o]=c[0]; col[q+o+1]=c[1]; col[q+o+2]=c[2]; col[q+o+3]=k ? 0.25 : 0.1; }
    }
  }
  flowGeo.getAttribute('position').needsUpdate = true;
  flowGeo.getAttribute('color').needsUpdate = true;
}
