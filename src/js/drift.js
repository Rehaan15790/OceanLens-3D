/* ============================================================
   SURFACE DRIFT (a search-and-rescue aid)
   Click the sea, or a NOAA drifting buoy, to release an object at the analysis on screen: the model's surface
   currents carry it forward (or back in time, to ask where something found there came from) for up to 72 h. One drift
   with no spread draws the line. 200 more, released within about a kilometre and given a random walk for the eddies
   the model cannot hold, show how far apart the same start can end; the ellipse holding 90 % of those still afloat is
   the search area at each day. With two models in the page the other model's drift from the same point is drawn
   too, dashed. A drift released from a buoy also draws where the buoy really went (amber).
   Real data use each model's surface currents from the extras (HYCOM every 6 h, GLORYS daily means), and the
   random walk's K for each model is the one at which 90 % of real buoys ended inside the ellipse
   (data-pipeline/drift_validation.py). Current only: there is no wind, so no leeway and no Stokes drift.
   ============================================================ */
const DRIFT = {
  n: 200,                // ensemble members besides the central drift
  stepH: 1,              // integration step in hours (midpoint rule)
  K: 1000,               // m²/s: the random walk's diffusivity where no checked value exists (synthetic demo)
  r0Km: 1,               // spread of the release positions, one standard deviation, km
  hours: [24, 48, 72],
  p90: Math.sqrt(-2*Math.log(0.1))   // a 2-D normal holds 90 % within 2.146 standard deviations
};
const M_PER_DEG = 111320;
const DRIFT_Y = 0.05;    // just above the sea surface, under the track
// {lat, lon, hours, dir, buoy, t0, start, used, K, cur, run, others:[{words, run}], buoyPath, key}; null when none
let drift = null;
const driftState = {open:false};
const driftGroup = new THREE.Group();
scene.add(Render.register('drift', driftGroup));

// the currents a drift uses for a model: {clock (ms of its steps), uv(lat, lon, depth, k), every (hours apart)}.
// Real data: the model's surface currents from the extras when the page has them, else its fields at the analyses.
function driftCurrents(id){
  if(dataMode!=='real') return {clock:Array.from({length:NT}, (_, k)=>k*3*864e5), uv:fieldCurrentUV, every:72};
  const S = surfCurrents(id);
  if(S) return {clock:S.clock, uv:(la, lo, d, k)=>surfAt(S, k, la, lo), every:(S.clock[1]-S.clock[0])/3.6e6, surf:true};
  const m = REAL_MODELS.find(x=>x.id===id);
  if(!m.decoded) m.decoded = decodeModel(m.data());
  const R = id===realModel ? REAL : m.decoded;
  return {clock:META.times.map(t=>Date.parse(t)), every:72,
          uv:(la, lo, d, t)=>({u:realAtIn(R, 2, la, lo, d, t), v:realAtIn(R, 3, la, lo, d, t)})};
}
// the drift clock of the active model (the tests use it)
function driftClock(){ return driftCurrents(realModel).clock; }
// the time of analysis t in ms: real analysis times, or the demo's three-day steps
function driftStart(t){ return dataMode==='real' ? Date.parse(META.times[t]) : t*3*864e5; }
// hours of currents there are from start (ms) in direction dir (1 forward, −1 back)
function driftHoursAvailable(clock, start, dir){
  return (dir > 0 ? clock[clock.length-1] - start : start - clock[0])/3.6e6;
}
// the random walk's K for a model: checked against real buoys when the validation is in the page, else DRIFT.K
function driftK(id){
  const v = dataMode==='real' ? DRIFT_VALIDATION() : null, k = v && v.meta && v.meta.best_k_m2_s && v.meta.best_k_m2_s[id];
  return Number.isFinite(k) && k >= 0 ? k : DRIFT.K;
}
// the surface current uv(lat, lon, depth, k) at time ms, linear in time between the two steps around it
function uvAtTime(uv, clock, lat, lon, ms){
  if(clock.length < 2) return uv(lat, lon, 0, 0);
  let k = 0;
  while(k < clock.length-2 && ms > clock[k+1]) k++;
  const w = Math.max(0, Math.min(1, (ms-clock[k])/(clock[k+1]-clock[k])));
  const a = uv(lat, lon, 0, k);
  if(w===0) return a;
  const b = uv(lat, lon, 0, k+1);
  return {u:a.u + (b.u-a.u)*w, v:a.v + (b.v-a.v)*w};
}
// position, share ashore or off the map, and the 90 % ellipse (km) of ensemble members; no ellipse under 10 afloat
function driftSpread(members){
  const n = members.length, afloat = members.filter(p=>!p.end);
  const out = {afloat:afloat.length/n, ashore:members.filter(p=>p.end==='ashore').length/n,
               edge:members.filter(p=>p.end==='edge').length/n, mean:null, ellipse:null};
  if(afloat.length < 10) return out;
  let mla = 0, mlo = 0;
  afloat.forEach(p=>{ mla += p.lat; mlo += p.lon; });
  mla /= afloat.length; mlo /= afloat.length;
  const kx = M_PER_DEG/1000*Math.cos(mla*Math.PI/180), ky = M_PER_DEG/1000;
  let sxx = 0, syy = 0, sxy = 0;
  afloat.forEach(p=>{ const x = (p.lon-mlo)*kx, y = (p.lat-mla)*ky; sxx += x*x; syy += y*y; sxy += x*y; });
  sxx /= afloat.length - 1; syy /= afloat.length - 1; sxy /= afloat.length - 1;
  const mid = (sxx+syy)/2, r = Math.sqrt((sxx-syy)*(sxx-syy)/4 + sxy*sxy);
  const a = DRIFT.p90*Math.sqrt(mid+r), b = DRIFT.p90*Math.sqrt(Math.max(0, mid-r));
  out.mean = [mla, mlo];
  out.ellipse = {a, b, angle:0.5*Math.atan2(2*sxy, sxx-syy), area:Math.PI*a*b};   // angle: major axis, from east
  return out;
}
// whether [lat, lon] lies inside a mark's 90 % ellipse
function insideEllipse(m, p){
  if(!m.ellipse || !(m.ellipse.b > 0)) return false;
  const {a, b, angle} = m.ellipse;
  const x = (p[1]-m.mean[1])*M_PER_DEG/1000*Math.cos(m.mean[0]*Math.PI/180), y = (p[0]-m.mean[0])*M_PER_DEG/1000;
  const xr = x*Math.cos(angle) + y*Math.sin(angle), yr = -x*Math.sin(angle) + y*Math.cos(angle);
  return (xr/a)*(xr/a) + (yr/b)*(yr/b) <= 1;
}
/* The drift from (lat, lon) at time start (ms; or step t0 of clock) for `hours`, dir 1 forward or −1 back, with
   currents uv(lat, lon, depth, k) at the steps of `clock` (ms). Member 0 is the central drift (no spread, no random
   walk); n more start within r0Km and take a random walk of diffusivity K (σ = √(2K·dt) per step and axis), from a
   seeded generator, so the same start always gives the same drift. A member stops at the coast (ashore) or at the
   map's edge. Returns the central path (every step), its end, and at every 24 h and at the end the members and their
   spread. */
function driftRun(o){
  const {lat, lon, hours, dir, uv, clock} = o;
  const n = o.n ?? DRIFT.n, K = o.K ?? DRIFT.K, r0 = (o.r0Km ?? DRIFT.r0Km)*1000;
  const rng = mulberry32(o.seed ?? 1), dt = DRIFT.stepH*3600, steps = Math.round(hours/DRIFT.stepH);
  const cosd = la=>Math.cos(la*Math.PI/180), sig = Math.sqrt(2*K*dt), start = o.start ?? clock[o.t0];
  const P = [];
  for(let i=0;i<=n;i++){
    let la = lat, lo = lon;
    if(i){
      la += gauss(rng)*r0/M_PER_DEG; lo += gauss(rng)*r0/(M_PER_DEG*cosd(lat));
      if(isLand(la, lo)){ la = lat; lo = lon; }
    }
    P.push({lat:la, lon:lo, end:null, h:hours});
  }
  // one midpoint step from time ms
  const move = (p, ms)=>{
    const a = uvAtTime(uv, clock, p.lat, p.lon, ms), k = dir*dt/M_PER_DEG;
    const mla = p.lat + 0.5*a.v*k, mlo = p.lon + 0.5*a.u*k/cosd(p.lat);
    const b = uvAtTime(uv, clock, mla, mlo, ms + dir*dt*500);
    return [p.lat + b.v*k, p.lon + b.u*k/cosd(mla)];
  };
  const path = [{h:0, lat:P[0].lat, lon:P[0].lon}], marks = [];
  for(let s=1;s<=steps;s++){
    const ms = start + dir*(s-1)*dt*1000, h = s*DRIFT.stepH;
    P.forEach((p, i)=>{
      if(p.end) return;
      let [la, lo] = move(p, ms);
      if(i && K > 0){ la += gauss(rng)*sig/M_PER_DEG; lo += gauss(rng)*sig/(M_PER_DEG*cosd(la)); }
      if(la < LAT0 || la > LAT1 || lo < LON0 || lo > LON1){ p.end = 'edge'; p.h = h; return; }
      p.lat = la; p.lon = lo;
      if(isLand(la, lo)){ p.end = 'ashore'; p.h = h; }
    });
    if(!P[0].end || (P[0].end==='ashore' && P[0].h===h)) path.push({h, lat:P[0].lat, lon:P[0].lon});
    if(h % 24 === 0 || s===steps){
      const members = P.slice(1);
      marks.push(Object.assign({h, pts:members.map(p=>[p.lat, p.lon, p.end])}, driftSpread(members)));
    }
  }
  return {path, end:P[0].end, endH:P[0].h, marks};
}

/* ---- NOAA drifting buoys (extras, real data): drawn while the drift tool is in use ---- */
function buoyData(){ const e = dataMode==='real' ? EXTRAS() : null; return e && e.drifters ? e.drifters : null; }
// the buoys with a position at the analysis on screen: [{index, id, lat, lon, h, drogued}]
function buoysNow(){
  const D = buoyData();
  if(!D) return [];
  const h = Math.round((driftStart(state.time) - Date.parse(D.t0))/3.6e6);
  return D.tracks.map((tr, index)=>{
    const p = tr.find(q=>q[0]===h), lost = D.drifters[index].drogue_lost_h;
    return p ? {index, id:D.drifters[index].id, lat:p[1], lon:p[2], h, drogued:lost===null || lost > h} : null;
  }).filter(Boolean);
}
// a buoy's hourly positions from hour h0 for up to `hours` (dir 1 on, −1 back), stopping at the first gap
function buoyTrack(index, h0, hours, dir){
  const D = buoyData(), at = new Map(D.tracks[index].map(q=>[q[0], q])), out = [];
  for(let s=0;s<=hours;s++){
    const q = at.get(h0 + dir*s);
    if(!q) break;
    out.push({h:s, lat:q[1], lon:q[2]});
  }
  return out;
}

/* ---- the drift on screen ---- */
// the drift's start must be on the sea inside the map
function driftStartOk(lat, lon){
  return Number.isFinite(lat) && Number.isFinite(lon) && lat>=LAT0 && lat<=LAT1 && lon>=LON0 && lon<=LON1 && !isLand(lat, lon);
}
// run the drift for the data source, model and analysis on screen (kept while none of them changes)
function computeDrift(){
  if(!drift) return;
  const t0 = state.time;
  // a drift released from a buoy starts where the buoy is at this analysis; one with no position here lets go of it
  if(drift.buoy!==null){
    const b = buoysNow().find(x=>x.index===drift.buoy);
    if(b){ drift.lat = b.lat; drift.lon = b.lon; } else drift.buoy = null;
  }
  const key = [drift.lat, drift.lon, drift.hours, drift.dir, drift.buoy, t0, dataMode, realModel].join('|');
  if(drift.key===key) return;
  drift.key = key;
  const cur = driftCurrents(realModel), start = driftStart(t0);
  Object.assign(drift, {t0, start, K:driftK(realModel), cur:{every:cur.every, surf:!!cur.surf}, others:[], run:null,
                        buoyPath:null, buoyInfo:null});
  drift.used = Math.min(drift.hours, Math.floor(driftHoursAvailable(cur.clock, start, drift.dir)));
  if(drift.used < 1) return;
  const seed = hashStr(`${drift.lat.toFixed(4)},${drift.lon.toFixed(4)},${t0},${drift.dir}`);
  const base = {lat:drift.lat, lon:drift.lon, start, hours:drift.used, dir:drift.dir, seed};
  drift.run = driftRun(Object.assign({clock:cur.clock, uv:cur.uv, K:drift.K}, base));
  // every other model in the page, from the same point and time: the central drift only
  if(dataMode==='real') REAL_MODELS.filter(m=>m.id!==realModel).forEach(m=>{
    const c = driftCurrents(m.id), hours = Math.min(drift.used, Math.floor(driftHoursAvailable(c.clock, start, drift.dir)));
    if(hours >= 1) drift.others.push({words:m.words, run:driftRun(Object.assign({}, base, {clock:c.clock, uv:c.uv, n:0, hours}))});
  });
  if(drift.buoy!==null){
    const b = buoysNow().find(x=>x.index===drift.buoy);
    drift.buoyInfo = b;
    drift.buoyPath = buoyTrack(drift.buoy, b.h, drift.used, drift.dir);
  }
}

const driftVisible = ()=>!!(drift && drift.run && driftGroup.visible);
const driftLabels = {
  start: makeLabel('', 'drift', new THREE.Vector3(), {visibleIf:driftVisible}),
  end: makeLabel('', 'drift', new THREE.Vector3(), {visibleIf:driftVisible}),
  other: makeLabel('', 'drift', new THREE.Vector3(), {visibleIf:()=>driftVisible() && drift.others.length > 0}),
  buoy: makeLabel('', 'drift buoy', new THREE.Vector3(), {visibleIf:()=>driftVisible() && !!drift.buoyPath && drift.buoyPath.length > 1})
};
const BUOY_HEX = 0xffcf5c;   // real buoys and their paths: amber, the colour of real things moving on the map (the storm)
let driftBuoyMarks = [];      // the buoys drawn now (buildDrift), for clicks
const _dv = (lat, lon, lift = 0)=>{ const [x, z] = toXZ(lat, lon); return new THREE.Vector3(x, DRIFT_Y + lift, z); };
// a flat ribbon along a path (a 1 px line is lost on the bright end of the palette); dash: on/off length, or null
function driftRibbon(path, half, lift, dash){
  const out = [], v = path.map(p=>_dv(p.lat, p.lon, lift));
  let along = 0;
  for(let i=1;i<v.length;i++){
    const a = v[i-1], b = v[i], dx = b.x-a.x, dz = b.z-a.z, len = Math.hypot(dx, dz);
    along += len;
    if(len < 1e-6 || (dash && Math.floor((along - len/2)/dash) % 2)) continue;
    const px = -dz/len*half, pz = dx/len*half, ex = dx/len*half*0.5, ez = dz/len*half*0.5;   // ends overlap a little
    out.push(a.x-ex+px, a.y, a.z-ez+pz, a.x-ex-px, a.y, a.z-ez-pz, b.x+ex+px, b.y, b.z+ez+pz,
             b.x+ex+px, b.y, b.z+ez+pz, a.x-ex-px, a.y, a.z-ez-pz, b.x+ex-px, b.y, b.z+ez-pz);
  }
  const g = new THREE.BufferGeometry();
  g.setAttribute('position', new THREE.Float32BufferAttribute(out, 3));
  return g;
}
// rebuild the buoys (while the drift tool is in use) and the drift's lines, ellipses and members; clears them when unused
function buildDrift(){
  driftGroup.children.slice().forEach(o=>{ driftGroup.remove(o); o.geometry.dispose(); o.material.dispose(); });
  const add = (obj, order)=>{ obj.renderOrder = order; obj.frustumCulled = false; driftGroup.add(obj); return obj; };
  const points = (list, shape, size, rgb)=>{
    const g = new THREE.BufferGeometry(), c = Render.gpuColor(rgb);
    g.setAttribute('position', new THREE.Float32BufferAttribute(list.flatMap(([la, lo, lift])=>{ const v = _dv(la, lo, lift); return [v.x, v.y, v.z]; }), 3));
    g.setAttribute('color', new THREE.Float32BufferAttribute(list.flatMap(()=>c), 3));
    return add(new THREE.Points(g, Render.marks({shape, sizePx:size})), Render.ORDER.markers);
  };
  driftBuoyMarks = [];
  if(drift || state.driftPlace){
    driftBuoyMarks = buoysNow();
    if(driftBuoyMarks.length) points(driftBuoyMarks.map(b=>[b.lat, b.lon, 0.02]), 'square', 10, [1, 0.81, 0.36]);
  }
  if(!drift || !drift.run) return;
  const run = drift.run, sign = drift.dir > 0 ? '+' : '−';
  // the 90 % ellipse at each day, the last one strongest
  run.marks.forEach((m, k)=>{
    if(!m.ellipse) return;
    const {a, b, angle} = m.ellipse, kx = 1000/(M_PER_DEG*Math.cos(m.mean[0]*Math.PI/180)), ky = 1000/M_PER_DEG, pts = [];
    for(let s=0;s<64;s++){
      const th = s/64*2*Math.PI, x = a*Math.cos(th), y = b*Math.sin(th);
      const ex = x*Math.cos(angle) - y*Math.sin(angle), ey = x*Math.sin(angle) + y*Math.cos(angle);
      pts.push(_dv(m.mean[0] + ey*ky, m.mean[1] + ex*kx));
    }
    const last = k===run.marks.length-1;
    add(new THREE.LineLoop(new THREE.BufferGeometry().setFromPoints(pts), Render.line(0xffffff, {opacity:last ? 0.95 : 0.45})),
        Render.ORDER.surfaceLines);
  });
  // where the members are at the end: white dots afloat, white crosses ashore
  const end = run.marks[run.marks.length-1];
  const afloat = end.pts.filter(p=>!p[2]).map(([la, lo])=>[la, lo, 0.005]);
  const ashore = end.pts.filter(p=>p[2]==='ashore').map(([la, lo])=>[la, lo, 0.005]);
  if(afloat.length) points(afloat, 'circle', 6, [1, 1, 1]).renderOrder = Render.ORDER.particles;
  if(ashore.length) points(ashore, 'cross', 9, [1, 1, 1]).renderOrder = Render.ORDER.particles;
  // the central drift as a white ribbon with a dark edge, the other model's dashed, the buoy's real path in amber
  const lines = [[run.path, null, 0xffffff], ...drift.others.map(o=>[o.run.path, 0.06, 0xffffff])];
  if(drift.buoyPath && drift.buoyPath.length > 1) lines.push([drift.buoyPath, null, BUOY_HEX]);
  // over the dots (particles), under the markers, so the paths read through the cloud
  lines.forEach(([path, dash, hex])=>{
    add(new THREE.Mesh(driftRibbon(path, 0.036, 0.008, dash), Render.flat(0x0b0f13, {side:THREE.DoubleSide})), Render.ORDER.particles + 1);
    add(new THREE.Mesh(driftRibbon(path, 0.02, 0.011, dash), Render.flat(hex, {side:THREE.DoubleSide})), Render.ORDER.particles + 1);
  });
  // the release point
  const ring = add(new THREE.Mesh(new THREE.RingGeometry(0.1, 0.15, 32), Render.flat(0xffffff, {side:THREE.DoubleSide})),
                   Render.ORDER.markers);
  ring.rotation.x = -Math.PI/2;
  ring.position.copy(_dv(drift.lat, drift.lon, 0.015));
  // labels: the release behind the drift, each end beyond itself; a label that would sit within 0.55 deg of one already
  // placed moves to the side, then further
  const first = run.path[0], last = run.path[run.path.length-1];
  const cl = Math.cos(first.lat*Math.PI/180);
  const unit = q=>{ const x = (q.lon-first.lon)*cl, y = q.lat-first.lat, d = Math.hypot(x, y); return d < 0.1 ? [0, 1] : [x/d, y/d]; };
  const at = (q, [x, y], k)=>[q.lat + y*k, q.lon + x*k/cl];
  const placed = [];
  const place = (L, text, q, k)=>{
    const u = unit(q), base = at(q, u, k), perp = [-u[1], u[0]];
    const tries = [0, 1, -1, 2, -2].map(n=>at({lat:base[0], lon:base[1]}, perp, 0.5*n));
    const p = tries.find(c=>placed.every(d=>Math.hypot((c[1]-d[1])*cl, c[0]-d[0]) >= 0.55)) || tries[0];
    placed.push(p);
    L.el.textContent = text;
    L.pos.copy(_dv(p[0], p[1], 0.03));
  };
  const startAt = at(first, unit(last), -0.45);
  placed.push(startAt);
  driftLabels.start.el.textContent = drift.dir > 0 ? 'Release' : 'Found here';
  driftLabels.start.pos.copy(_dv(startAt[0], startAt[1], 0.03));
  place(driftLabels.end, `${driftModelName()} ${sign}${last.h} h`, last, 0.45);
  if(drift.others.length){
    const o = drift.others[0];
    place(driftLabels.other, `${o.words.short} ${sign}${o.run.path[o.run.path.length-1].h} h`, o.run.path[o.run.path.length-1], 0.45);
  }
  if(drift.buoyPath && drift.buoyPath.length > 1){
    const q = drift.buoyPath[drift.buoyPath.length-1];
    place(driftLabels.buoy, `Buoy ${sign}${q.h} h`, q, 0.45);
  }
}
// the drawn buoy under a click (within 12 px), or null
function buoyAt(e){
  if(!driftBuoyMarks.length) return null;
  const rect = renderer.domElement.getBoundingClientRect(), v = new THREE.Vector3();
  let best = null, bestD = 12;
  driftBuoyMarks.forEach(b=>{
    v.copy(_dv(b.lat, b.lon, 0.02)).project(camera);
    if(v.z > 1) return;
    const d = Math.hypot(rect.left + (v.x*0.5+0.5)*rect.width - e.clientX, rect.top + (-v.y*0.5+0.5)*rect.height - e.clientY);
    if(d < bestD){ bestD = d; best = b; }
  });
  return best;
}
// ease the camera in over the drift, keeping its heading; the view's centre lands on the sea surface
function faceDrift(){
  if(!drift || !drift.run) return;
  const pts = drift.run.path.slice();
  drift.others.forEach(o=>pts.push(...o.run.path));
  if(drift.buoyPath) pts.push(...drift.buoyPath);
  const end = drift.run.marks[drift.run.marks.length-1];
  if(end.ellipse) pts.push({lat:end.mean[0] + end.ellipse.a/111, lon:end.mean[1]}, {lat:end.mean[0] - end.ellipse.a/111, lon:end.mean[1]});
  const xz = pts.map(p=>toXZ(p.lat, p.lon)), xs = xz.map(p=>p[0]), zs = xz.map(p=>p[1]);
  const cx = (Math.min(...xs)+Math.max(...xs))/2, cz = (Math.min(...zs)+Math.max(...zs))/2;
  const span = Math.max(Math.max(...xs)-Math.min(...xs), Math.max(...zs)-Math.min(...zs));
  const el1 = Math.max(camEl, 0.85), r1 = Math.max(6, Math.min(camR, span*2.6 + 4)), d = -camTarget.y/Math.tan(el1);
  camTween = {az0:camAz, el0:camEl, r0:camR, t0x:camTarget.x, t0z:camTarget.z,
    az1:camAz, el1, r1, t1x:cx - d*Math.sin(camAz), t1z:cz - d*Math.cos(camAz), t0:performance.now(), dur:900};
}
// the name of the currents on screen
function driftModelName(){ return dataMode==='real' ? MODEL().short : 'Demo'; }

/* ---- the drift panel ---- */
const COMPASS = ['N','NNE','NE','ENE','E','ESE','SE','SSE','S','SSW','SW','WSW','W','WNW','NW','NNW'];
function bearingWord(a, b){
  const dx = (b[1]-a[1])*Math.cos((a[0]+b[0])/2*Math.PI/180), dy = b[0]-a[0];
  return COMPASS[Math.round(((Math.atan2(dx, dy)*180/Math.PI + 360) % 360)/22.5) % 16];
}
// a model's drift checked against the drogued buoys (drift-validation), or null
function driftCheck(id){
  const v = dataMode==='real' ? DRIFT_VALIDATION() : null, m = v && v.models && v.models[id];
  return m && m.drogued ? {v, d:m.drogued} : null;
}
function renderDriftPanel(){
  if(!drift) return;
  const back = drift.dir < 0, M = dataMode==='real' ? MODEL() : null, B = drift.buoyInfo;
  document.getElementById('driftName').textContent = back ? 'Drift back in time' : 'Surface drift';
  document.getElementById('driftKind').textContent = M ? `${M.short} currents` : 'Synthetic currents';
  document.getElementById('driftMeta').innerHTML = (B
    ? `${back ? 'Back from' : 'Released from'} NOAA drifting buoy <b>${fmt.esc(B.id)}</b> (${B.drogued ? 'drogue at 15 m' : 'no drogue'}) at `
    : `${back ? 'Found at' : 'Released at'} `) + `<b>${fmt.latLon2(drift.lat, drift.lon)}</b> · ${fmt.esc(timeLabel(drift.t0))}`;
  document.querySelectorAll('#driftDir button').forEach(b=>setPressed(b, +b.dataset.dir===drift.dir));
  document.querySelectorAll('#driftHours button').forEach(b=>setPressed(b, +b.dataset.h===drift.hours));
  const body = document.getElementById('driftBody'), foot = document.getElementById('driftFoot');
  if(!drift.run){
    body.innerHTML = `<p class="hint">There are no currents ${back ? 'before' : 'after'} ${back ? 'the first' : 'the last'} `+
      `${M ? fmt.esc(M.product) : 'demo day'}. Move the timeline ${back ? 'forward' : 'back'}, or drift ${back ? 'forward' : 'back in time'}.</p>`;
    foot.innerHTML = '';
    return;
  }
  const run = drift.run, sign = back ? '−' : '+', p0 = [drift.lat, drift.lon], bp = drift.buoyPath;
  const short = drift.used < drift.hours
    ? `<p class="hint">Only ${drift.used} h of currents ${back ? 'before' : 'after'} this time: the drift stops there.</p>` : '';
  const buoyAtH = h=>bp && bp.find(q=>q.h===h);
  const rows = run.marks.map(m=>{
    const c = run.path.find(p=>p.h===m.h) || run.path[run.path.length-1], q = buoyAtH(m.h);
    const nb = ' ';   // a number stays with its unit
    return `<tr><td>${sign}${m.h}${nb}h</td><td>${fmt.int(haversineKm(p0, [c.lat, c.lon]))}${nb}km ${bearingWord(p0, [c.lat, c.lon])}</td>`+
      `<td>${m.ellipse ? fmt.sig2(m.ellipse.area)+nb+'km²' : '—'}</td><td>${fmt.int(100*m.ashore)}${nb}%</td>`+
      (bp ? `<td>${q ? `${fmt.int(haversineKm([q.lat, q.lon], [c.lat, c.lon]))}${nb}km ${insideEllipse(m, [q.lat, q.lon]) ? 'in' : 'out'}` : '—'}</td>` : '')+
      '</tr>';
  }).join('');
  const last = run.path[run.path.length-1];
  let km = 0;
  for(let i=1;i<run.path.length;i++) km += haversineKm([run.path[i-1].lat, run.path[i-1].lon], [run.path[i].lat, run.path[i].lon]);
  const fate = run.end==='ashore' ? `It reaches the coast after ${run.endH} h, near ${fmt.latLon1(last.lat, last.lon)}.`
             : run.end==='edge' ? `It leaves the map after ${run.endH} h.`
             : `It ${back ? 'started' : 'ends'} near <b>${fmt.latLon2(last.lat, last.lon)}</b>.`;
  const mine = driftCheck(realModel);
  const others = drift.others.map(o=>{
    const q = o.run.path[o.run.path.length-1], gap = haversineKm([last.lat, last.lon], [q.lat, q.lon]);
    const theirs = driftCheck(o.words.short.toLowerCase());
    let vs = '';
    if(mine && theirs){
      const a = mine.d.separation_km['72'].median, b = theirs.d.separation_km['72'].median;
      vs = ` Against real buoys this month the ${fmt.esc(a <= b ? M.short : o.words.short)} drift came closer `+
           `(a median ${fmt.int(Math.min(a, b))} km from the buoy after 72 h, against ${fmt.int(Math.max(a, b))} km).`;
    }
    return `<p class="meta">The ${fmt.esc(o.words.short)} drift (dashed) from the same point ends <b>${fmt.int(gap)} km</b> from the `+
      `${fmt.esc(M.short)} one${o.run.end==='ashore' ? `, ashore after ${o.run.endH} h` : ''}.${vs}</p>`;
  }).join('');
  let buoy = '';
  if(bp && bp.length > 1){
    const q = bp[bp.length-1], c = run.path.find(p=>p.h===q.h) || last, m = run.marks.find(x=>x.h===q.h);
    const vsOthers = drift.others.map(o=>{ const r = o.run.path.find(p=>p.h===q.h) || o.run.path[o.run.path.length-1];
      return ` and ${fmt.int(haversineKm([q.lat, q.lon], [r.lat, r.lon]))} km from the ${fmt.esc(o.words.short)} one`; }).join('');
    buoy = `<p class="meta">Where the buoy really went (amber): ${back ? `${q.h} h earlier it was` : `after ${q.h} h it was`} `+
      `<b>${fmt.int(haversineKm([q.lat, q.lon], [c.lat, c.lon]))} km</b> from the ${fmt.esc(M.short)} line${vsOthers}`+
      `${m ? `, ${insideEllipse(m, [q.lat, q.lon]) ? 'inside' : 'outside'} the 90 % ellipse` : ''}.`+
      `${q.h < drift.used ? ` Its record stops there.` : ''}</p>`;
  } else if(B){
    buoy = `<p class="meta">The buoy has no record ${back ? 'before' : 'after'} this time, so there is no real path to compare.</p>`;
  }
  body.innerHTML = short +
    `<p class="meta">The line ${back ? 'traces back' : 'moves'} ${fmt.int(km)} km in ${run.endH} h `+
    `(${fmt.dp(km/run.endH/3.6, 2)} m/s on average). ${fate}</p>` + buoy + others +
    `<table class="val-table"><caption>The ${DRIFT.n} spread drifts</caption>`+
    `<tr><th>${back ? 'Before' : 'After'}</th><th>Line from ${back ? 'find' : 'release'}</th><th>90 % area</th><th>Ashore</th>`+
    `${bp ? '<th>Buoy: off the line, in the ellipse?</th>' : ''}</tr>${rows}</table>`;
  const every = drift.cur.every, days = Math.round(every/24);
  const curText = M ? `${fmt.esc(M.short)} surface currents ${drift.cur.surf ? (every===24 ? 'as daily means' : `every ${every} hours`)
                                                           : `at its ${fmt.esc(M.products)}, ${days} days apart`}`
                    : `The demo’s made-up currents, ${days} days apart`;
  const kText = mine ? `K = ${drift.K} m²/s, the tested value at which the share of real buoys inside the 90 % ellipse came closest to 90 %`
                     : `K = ${drift.K} m²/s, an assumed value`;
  let check = '';
  if(mine){
    const d = mine.d, s72 = d.separation_km['72'], e = d.ensemble && d.ensemble[String(drift.K)];
    check = `<p>Checked against ${mine.v.meta.drifters.drogued} NOAA drifting buoys with drogues (${s72.n} three-day releases, `+
      `4–22 May 2020): after 72 h the ${fmt.esc(M.short)} line was a median ${fmt.int(s72.median)} km from the buoy, closer than `+
      `“it stays where it was” (${fmt.int(d.stay_put_km['72'].median)} km) in ${fmt.int(d.beats_stay_put_pct['72'])} % of releases; `+
      `skill ${d.skill_72h.median.toFixed(2)} on Liu and Weisberg’s scale (1 is perfect)`+
      `${e ? `; ${fmt.int(e.inside_pct['72'])} % of the buoys ended inside the 90 % ellipse` : ''}. The drogues hang at 15 m, `+
      `below the surface currents used here.</p>`;
  }
  foot.innerHTML =
    `<p>${curText}, linear in time between them, in ${DRIFT.stepH} h steps. The line is one drift with no spread. The dots are `+
    `${DRIFT.n} more, released within about ${DRIFT.r0Km} km and given a random walk for eddies the model cannot hold `+
    `(${kText}); crosses reached the coast. Each ellipse holds 90 % of those still afloat that day.</p>` + check +
    `<p>Only the current moves them: there is no wind, so no leeway (a life raft or a person in the water also moves with `+
    `the wind, at a few per cent of its speed) and no Stokes drift. A teaching aid, not a search plan: INCOIS runs SARAT for that.</p>`;
}

/* ---- actions ---- */
// release a drift at (lat, lon) from the analysis on screen (opts.buoy: from that buoy); false when not on the sea
function startDrift(lat, lon, opts = {}){
  if(!driftStartOk(lat, lon)) return false;
  if(isTourRunning() && !opts.fromTour) stopTour();
  setDriftPlace(false);
  drift = {lat, lon, hours:opts.hours ?? (drift ? drift.hours : 72), dir:opts.dir ?? (drift ? drift.dir : 1),
           buoy:Number.isInteger(opts.buoy) ? opts.buoy : null};
  computeDrift();
  faceDrift();
  showPanel('drift');
  requestUpdate('full');
  return true;
}
// release a drift from buoy `index` (of the extras' drifters) at the analysis on screen; false when it has no position then
function startDriftFromBuoy(index, opts = {}){
  const b = buoysNow().find(x=>x.index===index);
  return b ? startDrift(b.lat, b.lon, Object.assign({}, opts, {buoy:index})) : false;
}
// change how long, or which way in time, the drift on screen runs
function setDriftOptions(opts){
  if(!drift) return;
  if(DRIFT.hours.includes(opts.hours)) drift.hours = opts.hours;
  if(opts.dir===1 || opts.dir===-1) drift.dir = opts.dir;
  requestUpdate('full');
}
// remove the drift and ease the camera back home; the panel goes back to the section if there is one
function clearDrift(){
  drift = null;
  buildDrift();
  if(!section) homeCamera();
  if(driftState.open) closeSidePanels();
  requestUpdate('full');
}
// click-to-release mode on or off (the T key, the Drift button); the buoys show while it is on
function setDriftPlace(on){
  if(on && state.sectionDraw) setSectionDraw(false);
  if(on && isTourRunning()) stopTour();
  state.driftPlace = on;
  const btn = document.getElementById('driftBtn');
  btn.classList.toggle('active', on);
  btn.setAttribute('aria-pressed', on ? 'true' : 'false');
  viewport.classList.toggle('drawing', on || state.sectionDraw);
  const hint = document.getElementById('driftHint');
  hint.classList.toggle('show', on);
  hint.firstChild.textContent = buoyData() ? 'Click the sea, or an amber buoy, to release a drift. ' : 'Click the sea to release a drift. ';
  buildDrift();
  Render.invalidate();
}
// a click on the view while placing (markers.js handleClick sends it here): a buoy, else the sea
function placeDriftAt(e){
  const b = buoyAt(e);
  if(b){ startDriftFromBuoy(b.index); return; }
  const p = surfaceLatLon(e);
  if(!p) return;
  if(!startDrift(p[0], p[1])) document.getElementById('driftHint').firstChild.textContent = 'That is land: click the sea. ';
}
// a click on a drawn buoy while a drift is shown releases a drift from it; true when it did
function clickDriftBuoy(e){
  const b = drift ? buoyAt(e) : null;
  return b ? startDriftFromBuoy(b.index) : false;
}
document.getElementById('driftBtn').addEventListener('click', ()=>setDriftPlace(!state.driftPlace));
document.getElementById('closeDrift').addEventListener('click', clearDrift);
document.querySelectorAll('#driftDir button').forEach(b=>b.addEventListener('click', ()=>setDriftOptions({dir:+b.dataset.dir})));
document.querySelectorAll('#driftHours button').forEach(b=>b.addEventListener('click', ()=>setDriftOptions({hours:+b.dataset.h})));
