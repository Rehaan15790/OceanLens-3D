/* ============================================================
   ISOSURFACE (3D): where the field equals the chosen value, through the whole volume
   Marching cubes over the model grid (DEPTHS × lat × lon). The case table is generated at load
   from the cube's faces: on a face with two crossings either way of joining them, the one that
   cuts off the corners at or above the value is always taken, from both cubes that share the
   face, so the surface has no cracks.
   Masks: cells touching land are skipped; model values below the sea floor still steer the
   interpolation, but triangles whose centre lies below the sea floor (the grid's or the drawn
   terrain's, whichever is shallower) are dropped. A missing value
   (below the model's deepest level) repeats the one above it.
   Vertical exaggeration is the mesh's y scale (the normal matrix keeps the shading right), so the
   slider costs nothing; value, time and variable changes rebuild the mesh.
   ============================================================ */
const MC = (() => {
  // corner c = x + 2y + 4z: x along longitude (j), y along latitude (i), z down the levels (k)
  const faces = [[0,4,6,2], [1,3,7,5], [0,1,5,4], [2,6,7,3], [0,2,3,1], [4,5,7,6]];   // anticlockwise seen from outside
  const edges = [], edgeOf = {};
  for(let c=0; c<8; c++) for(const bit of [1, 2, 4]) if(!(c & bit)){
    edgeOf[c+','+(c|bit)] = edgeOf[(c|bit)+','+c] = edges.length;
    edges.push([c, c|bit, bit === 1 ? 0 : bit === 2 ? 1 : 2]);   // [corner a, corner b, axis]
  }
  const cases = [];
  for(let m=0; m<256; m++){
    const inside = c => (m >> c) & 1, next = {};
    faces.forEach(f => {
      const xs = [];
      for(let a=0; a<4; a++){
        const c0 = f[a], c1 = f[(a+1)%4];
        if(inside(c0) !== inside(c1)) xs.push({e:edgeOf[c0+','+c1], leaving:inside(c0)});
      }
      // every crossing that leaves the inside joins the crossing before it: a shared edge is
      // walked the other way round by the neighbouring face, so the segments close into loops
      xs.forEach((x, p) => { if(x.leaving) next[x.e] = xs[(p - 1 + xs.length) % xs.length].e; });
    });
    const tris = [], seen = new Set();
    Object.keys(next).forEach(k => {
      const start = +k;
      if(seen.has(start)) return;
      const loop = [];
      let e = start;
      do { seen.add(e); loop.push(e); e = next[e]; } while(e !== start);
      // fan from a vertex whose diagonals do not lie on a cube face, where the neighbouring cube could
      // draw the same diagonal and the surface would pinch
      const L = loop.length, onFace = (x, y) => faces.some(f => [x, y].every(e => f.includes(edges[e][0]) && f.includes(edges[e][1])));
      let s0 = 0;
      for(let s=0; s<L; s++){
        let ok = true;
        for(let i=2; i<L-1 && ok; i++) ok = !onFace(loop[s], loop[(s + i) % L]);
        if(ok){ s0 = s; break; }
      }
      for(let i=1; i+1<L; i++) tris.push(loop[s0], loop[(s0 + i) % L], loop[(s0 + i + 1) % L]);
    });
    cases.push(tris);
  }
  return {edges, cases};
})();

const iso3dMesh = new THREE.Mesh(new THREE.BufferGeometry(), Render.isoShaded([1, 1, 1]));
iso3dMesh.renderOrder = Render.ORDER.translucent;
iso3dMesh.visible = false;
scene.add(Render.register('iso3d', iso3dMesh));
let iso3dKey = null, iso3dSpec = null, iso3dInfo = null;
const iso3dStats = {ms:NaN, triangles:0};   // the last rebuild, for tests and the perf budget (~50 ms)

// node values over (k, i, j), missing ones repeated from above; colOK = the column has water and a surface value
function iso3dField(){
  const plane = LAT_N*LON_N, NK = DEPTHS.length, key = fieldKey(state.variable);
  const levels = layerField && layerField.key === key ? layerField.levels
    : DEPTHS.map(d => gridPts.map(p => getVal(state.variable, p.lat, p.lon, d, state.time)));
  const V = new Float32Array(plane*NK), colOK = new Uint8Array(plane);
  for(let c=0; c<plane; c++){
    if(gridLand[c] || !Number.isFinite(levels[0][c])) continue;
    colOK[c] = 1;
    for(let k=0; k<NK; k++){
      const v = levels[k][c];
      V[k*plane + c] = Number.isFinite(v) ? v : V[(k - 1)*plane + c];
    }
  }
  return {V, colOK};
}

function buildIso3d(target){
  const t0 = performance.now();
  const {V, colOK} = iso3dField();
  const NI = LAT_N, NJ = LON_N, NK = DEPTHS.length, plane = NI*NJ;
  const X = new Float32Array(plane), Z = new Float32Array(plane);
  gridPts.forEach((p, c) => { const [x, z] = toXZ(p.lat, p.lon); X[c] = x; Z[c] = z; });
  const vertOf = new Int32Array(plane*NK*3).fill(-1);
  const pos = [], depth = [], floor = [], index = [], corner = new Int32Array(8), tri = [0, 0, 0];
  const step = [1, NJ, plane];
  // log-scale variables (chlorophyll) vary roughly exponentially, so the crossing is found in log10
  const logVar = VAR_META[state.variable] && VAR_META[state.variable].scale === 'log' && target > 0;
  const tv = v => logVar ? Math.log10(Math.max(v, target*1e-6)) : v, tt = tv(target);
  const vertex = (e) => {
    const [a, , axis] = MC.edges[e], na = corner[a], key = na*3 + axis;
    if(vertOf[key] >= 0) return vertOf[key];
    const nb = na + step[axis], va = tv(V[na]), f = (tt - va)/(tv(V[nb]) - va);
    const ca = na % plane, cb = nb % plane, ka = (na - ca)/plane, kb = (nb - cb)/plane;
    const d = lerp(DEPTHS[ka], DEPTHS[kb], f);
    pos.push(lerp(X[ca], X[cb], f), -d*DEPTH_SCALE, lerp(Z[ca], Z[cb], f));
    // the drawn terrain comes from ETOPO at full resolution: never go below it either
    const lat = lerp(gridPts[ca].lat, gridPts[cb].lat, f), lon = lerp(gridPts[ca].lon, gridPts[cb].lon, f);
    depth.push(d); floor.push(Math.min(lerp(gridSeabed[ca], gridSeabed[cb], f), bathyDepth(lat, lon)));
    return (vertOf[key] = depth.length - 1);
  };
  for(let k=0; k<NK-1; k++) for(let i=0; i<NI-1; i++) for(let j=0; j<NJ-1; j++){
    const c0 = i*NJ + j;
    if(!colOK[c0] || !colOK[c0 + 1] || !colOK[c0 + NJ] || !colOK[c0 + NJ + 1]) continue;
    const n0 = k*plane + c0;
    let m = 0;
    for(let c=0; c<8; c++){
      const n = n0 + (c & 1) + (c >> 1 & 1)*NJ + (c >> 2 & 1)*plane;
      corner[c] = n;
      if(V[n] >= target) m |= 1 << c;
    }
    const tris = MC.cases[m];
    for(let t=0; t<tris.length; t+=3){
      for(let q=0; q<3; q++) tri[q] = vertex(tris[t + q]);
      // below the sea floor: dropped
      if(depth[tri[0]] + depth[tri[1]] + depth[tri[2]] > floor[tri[0]] + floor[tri[1]] + floor[tri[2]]) continue;
      index.push(tri[0], tri[1], tri[2]);
    }
  }
  const geo = new THREE.BufferGeometry();
  geo.setAttribute('position', new THREE.Float32BufferAttribute(pos, 3));
  geo.setIndex(new THREE.BufferAttribute(pos.length/3 > 65535 ? new Uint32Array(index) : new Uint16Array(index), 1));
  geo.computeVertexNormals();
  iso3dMesh.geometry.dispose();
  iso3dMesh.geometry = geo;
  // the header statistics describe the surface's depths, as in "depth of a value"
  let n = 0, min = Infinity, max = -Infinity, sum = 0;
  const used = new Uint8Array(depth.length);
  index.forEach(v => { if(!used[v]){ used[v] = 1; const d = depth[v]; n++; sum += d; if(d < min) min = d; if(d > max) max = d; } });
  iso3dInfo = n ? {n, lo:Math.floor(min/10)*10, hi:Math.max(Math.floor(min/10)*10 + 10, Math.ceil(max/10)*10), min, max, mean:sum/n}
              : {n:0, lo:0, hi:1000, min:NaN, max:NaN, mean:NaN};
  iso3dStats.ms = performance.now() - t0;
  iso3dStats.triangles = index.length/3;
}

// called by applyVolumeView in 'iso3d': rebuild only when the field or the value changed
// (a variable registered again under the same key is a new field)
function updateIso3d(){
  const key = fieldKey(state.variable) + '|' + state.isoVal, spec = VAR_META[state.variable];
  if(key !== iso3dKey || spec !== iso3dSpec){ iso3dKey = key; iso3dSpec = spec; buildIso3d(state.isoVal); }
  isoInfo = iso3dInfo;
  iso3dMesh.scale.y = state.exagg;
  Render.setIsoColour(iso3dMesh.material, paletteColor(state.palette, Render.rampPos(state.isoVal, state.cbMin, state.cbMax, state.scale)));
  iso3dMesh.visible = true;
}
