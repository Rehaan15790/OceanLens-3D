/* ============================================================
   SURFACE DRIFT (a search-and-rescue aid)
   Click the sea to release an object at the analysis on screen: the model's surface currents carry it forward
   (or back in time, to ask where something found there came from) for up to 72 h. One drift with no spread draws
   the line. 200 more, released within about a kilometre and given a random walk for the eddies a 0.5° grid cannot
   hold, show how far apart the same start can end; the ellipse holding 90 % of those still afloat is the search
   area at each day. With two models in the page the other model's drift from the same point is drawn too, dashed:
   where the two part is the model uncertainty a forecaster would weigh.
   Current only: the snapshots have no wind, so there is no leeway and no Stokes drift (the panel says so).
   drifters.js checks the drift against real drifting buoys and draws a drifter's own path when a drift starts from one.
   ============================================================ */
const DRIFT = {
  n: 200,                // ensemble members besides the central drift
  stepH: 1,              // integration step in hours (midpoint rule)
  K: 1000,               // m²/s: horizontal eddy diffusivity of the random walk, an assumed sub-grid value
  r0Km: 1,               // spread of the release positions, one standard deviation, km
  hours: [24, 48, 72],
  p90: Math.sqrt(-2*Math.log(0.1))   // a 2-D normal holds 90 % within 2.146 standard deviations
};
const M_PER_DEG = 111320;
const DRIFT_Y = 0.05;    // just above the sea surface, under the track
let drift = null;        // {lat, lon, hours, dir, t0, used, run, others:[{words, run}], key}; null when none
const driftState = {open:false};
const driftGroup = new THREE.Group();
scene.add(Render.register('drift', driftGroup));

// ms of each time step: the analysis times (real data) or the demo's three-day steps
function driftClock(){
  return dataMode==='real' ? META.times.map(t=>Date.parse(t)) : Array.from({length:NT}, (_, k)=>k*3*864e5);
}
// hours of currents there are from step t0 in direction dir (1 forward, −1 back)
function driftHoursAvailable(clock, t0, dir){
  return (dir > 0 ? clock[clock.length-1] - clock[t0] : clock[t0] - clock[0])/3.6e6;
}
// the surface current uv(lat, lon, depth, t) at time ms, linear in time between the two steps around it
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
/* The drift from (lat, lon) at step t0 for `hours`, dir 1 forward or −1 back, with currents uv(lat, lon, depth, t)
   at the steps of `clock` (ms). Member 0 is the central drift (no spread, no random walk); n more start within r0Km
   and take a random walk of diffusivity K (σ = √(2K·dt) per step and axis), from a seeded generator, so the same start
   always gives the same drift. A member stops at the coast (ashore) or at the map's edge.
   Returns the central path (every step), its end, and at every 24 h and at the end the members and their spread. */
function driftRun(o){
  const {lat, lon, t0, hours, dir, uv, clock} = o;
  const n = o.n ?? DRIFT.n, K = o.K ?? DRIFT.K, r0 = (o.r0Km ?? DRIFT.r0Km)*1000;
  const rng = mulberry32(o.seed ?? 1), dt = DRIFT.stepH*3600, steps = Math.round(hours/DRIFT.stepH);
  const cosd = la=>Math.cos(la*Math.PI/180), sig = Math.sqrt(2*K*dt), start = clock[t0];
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

/* ---- the drift on screen ---- */
// the drift's start must be on the sea inside the map
function driftStartOk(lat, lon){
  return Number.isFinite(lat) && Number.isFinite(lon) && lat>=LAT0 && lat<=LAT1 && lon>=LON0 && lon<=LON1 && !isLand(lat, lon);
}
// run the drift for the data source, model and analysis on screen (kept while none of them changes)
function computeDrift(){
  if(!drift) return;
  const clock = driftClock(), t0 = state.time;
  const key = [drift.lat, drift.lon, drift.hours, drift.dir, t0, dataMode, realModel].join('|');
  if(drift.key===key) return;
  drift.key = key;
  drift.t0 = t0;
  drift.used = Math.min(drift.hours, Math.floor(driftHoursAvailable(clock, t0, drift.dir)));
  drift.others = [];
  drift.run = null;
  drift.real = null;
  if(drift.used < 1) return;
  const seed = hashStr(`${drift.lat.toFixed(4)},${drift.lon.toFixed(4)},${t0},${drift.dir}`);
  const base = {lat:drift.lat, lon:drift.lon, t0, hours:drift.used, dir:drift.dir, clock, seed};
  drift.run = driftRun(Object.assign({uv:fieldCurrentUV}, base));
  // every other model in the page, from the same point and time: the central drift only
  if(dataMode==='real') REAL_MODELS.filter(m=>m.id!==realModel).forEach(m=>{
    if(!m.decoded) m.decoded = decodeModel(m.data());
    const R = m.decoded;
    const uv = (la, lo, d, t)=>({u:realAtIn(R, 2, la, lo, d, t), v:realAtIn(R, 3, la, lo, d, t)});
    drift.others.push({words:m.words, run:driftRun(Object.assign({uv, n:0}, base))});
  });
  drift.real = drifterTruth(drift, clock);
}

const driftVisible = ()=>!!(drift && drift.run && driftGroup.visible);
const driftLabels = {
  start: makeLabel('', 'drift', new THREE.Vector3(), {visibleIf:driftVisible}),
  end: makeLabel('', 'drift', new THREE.Vector3(), {visibleIf:driftVisible}),
  other: makeLabel('', 'drift', new THREE.Vector3(), {visibleIf:()=>driftVisible() && drift.others.length > 0})
};
const _dv = (lat, lon, lift = 0)=>{ const [x, z] = toXZ(lat, lon); return new THREE.Vector3(x, DRIFT_Y + lift, z); };
// a flat ribbon on the sea surface along path [{lat, lon}], half-width `half`, `lift` above DRIFT_Y, dashed every `dash`
function surfaceRibbon(path, half, lift, dash){
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
// rebuild the drift's lines, ellipses and members for `drift`; clears them when null
function buildDrift(){
  driftGroup.children.slice().forEach(o=>{ driftGroup.remove(o); o.geometry.dispose(); o.material.dispose(); });
  if(!drift || !drift.run) return;
  const run = drift.run, sign = drift.dir > 0 ? '+' : '−';
  const add = (obj, order)=>{ obj.renderOrder = order; obj.frustumCulled = false; driftGroup.add(obj); return obj; };
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
  // where the members are at the end: white afloat, amber ashore
  const end = run.marks[run.marks.length-1], pos = [], col = [];
  const WHITE = Render.gpuColor([1, 1, 1]), AMBER = Render.gpuColor([1, 0.81, 0.36]);
  end.pts.forEach(([la, lo, how])=>{
    if(how==='edge') return;
    const v = _dv(la, lo, 0.005);
    pos.push(v.x, v.y, v.z);
    col.push(...(how==='ashore' ? AMBER : WHITE));
  });
  const g = new THREE.BufferGeometry();
  g.setAttribute('position', new THREE.Float32BufferAttribute(pos, 3));
  g.setAttribute('color', new THREE.Float32BufferAttribute(col, 3));
  add(new THREE.Points(g, Render.marks({shape:'circle', sizePx:6})), Render.ORDER.particles);
  // the central drift as a white ribbon with a dark edge (a 1 px line is lost on the bright end of the palette),
  // the other model's dashed, and a real drifter's own path (drifters.js) in its colour
  [[run.path, null], ...drift.others.map(o=>[o.run.path, 0.06])].forEach(([path, dash])=>{
    add(new THREE.Mesh(surfaceRibbon(path, 0.036, 0.008, dash), Render.flat(0x0b0f13, {side:THREE.DoubleSide})), Render.ORDER.surfaceLines);
    add(new THREE.Mesh(surfaceRibbon(path, 0.02, 0.011, dash), Render.flat(0xffffff, {side:THREE.DoubleSide})), Render.ORDER.surfaceLines);
  });
  if(drift.real && drift.real.path.length > 1){
    add(new THREE.Mesh(surfaceRibbon(drift.real.path, 0.036, 0.0085), Render.flat(0x0b0f13, {side:THREE.DoubleSide})), Render.ORDER.surfaceLines);
    add(new THREE.Mesh(surfaceRibbon(drift.real.path, 0.02, 0.0115), Render.flat(DRIFTER_HEX, {side:THREE.DoubleSide})), Render.ORDER.surfaceLines);
  }
  // the release point
  const ring = add(new THREE.Mesh(new THREE.RingGeometry(0.1, 0.15, 32), Render.flat(0xffffff, {side:THREE.DoubleSide})),
                   Render.ORDER.markers);
  ring.rotation.x = -Math.PI/2;
  ring.position.copy(_dv(drift.lat, drift.lon, 0.015));
  // labels: the release behind the drift, each model's end beyond its own end (and apart when the ends are close)
  const first = run.path[0], last = run.path[run.path.length-1];
  const cl = Math.cos(first.lat*Math.PI/180);
  const unit = q=>{ const x = (q.lon-first.lon)*cl, y = q.lat-first.lat, d = Math.hypot(x, y); return d < 0.1 ? [0, 1] : [x/d, y/d]; };
  const at = (q, [x, y], k)=>[q.lat + y*k, q.lon + x*k/cl];
  const u = unit(last), endAt = at(last, u, 0.45), startAt = at(first, u, -0.45);
  driftLabels.start.el.textContent = drift.dir > 0 ? 'Release' : 'Found here';
  driftLabels.start.pos.copy(_dv(startAt[0], startAt[1], 0.03));
  driftLabels.end.el.textContent = `${driftModelName()} ${sign}${last.h} h`;
  driftLabels.end.pos.copy(_dv(endAt[0], endAt[1], 0.03));
  if(drift.others.length){
    const o = drift.others[0], q = o.run.path[o.run.path.length-1];
    let p = at(q, unit(q), 0.45);
    if(Math.hypot((p[1]-endAt[1])*cl, p[0]-endAt[0]) < 0.6){
      const side = Math.sign((q.lon-last.lon)*cl*u[1] - (q.lat-last.lat)*u[0]) || 1;   // the side of the line q is on
      p = [p[0] - side*u[0]*0.45, p[1] + side*u[1]*0.45/cl];
    }
    driftLabels.other.el.textContent = `${o.words.short} ${sign}${q.h} h`;
    driftLabels.other.pos.copy(_dv(p[0], p[1], 0.03));
  }
}
// ease the camera in over the drift, keeping its heading; the view's centre lands on the sea surface
function faceDrift(){
  if(!drift || !drift.run) return;
  const pts = drift.run.path.slice();
  drift.others.forEach(o=>pts.push(...o.run.path));
  const end = drift.run.marks[drift.run.marks.length-1];
  if(end.ellipse) pts.push({lat:end.mean[0] + end.ellipse.a/111, lon:end.mean[1]}, {lat:end.mean[0] - end.ellipse.a/111, lon:end.mean[1]});
  const xz = pts.map(p=>toXZ(p.lat, p.lon)), xs = xz.map(p=>p[0]), zs = xz.map(p=>p[1]);
  const cx = (Math.min(...xs)+Math.max(...xs))/2, cz = (Math.min(...zs)+Math.max(...zs))/2;
  const span = Math.max(Math.max(...xs)-Math.min(...xs), Math.max(...zs)-Math.min(...zs));
  const el1 = Math.max(camEl, 0.85), r1 = Math.max(8, Math.min(camR, span*2.6 + 6)), d = -camTarget.y/Math.tan(el1);
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
function renderDriftPanel(){
  if(!drift) return;
  const back = drift.dir < 0, M = dataMode==='real' ? MODEL() : null, clock = driftClock();
  document.getElementById('driftName').textContent = back ? 'Drift back in time' : 'Surface drift';
  document.getElementById('driftKind').textContent = M ? `${M.short} currents` : 'Synthetic currents';
  document.getElementById('driftMeta').innerHTML = `${back ? 'Found at' : 'Released at'} <b>${fmt.latLon2(drift.lat, drift.lon)}</b> · `+
    fmt.esc(timeLabel(drift.t0));
  document.querySelectorAll('#driftDir button').forEach(b=>setPressed(b, +b.dataset.dir===drift.dir));
  document.querySelectorAll('#driftHours button').forEach(b=>setPressed(b, +b.dataset.h===drift.hours));
  const body = document.getElementById('driftBody');
  if(!drift.run){
    body.innerHTML = `<p class="hint">${back ? 'This is the first' : 'This is the last'} ${M ? fmt.esc(M.product) : 'demo day'}: `+
      `there are no currents ${back ? 'before' : 'after'} it. Move the timeline ${back ? 'forward' : 'back'}, or drift `+
      `${back ? 'forward' : 'back in time'}.</p>`;
    document.getElementById('driftFoot').innerHTML = '';
    return;
  }
  const run = drift.run, sign = back ? '−' : '+', p0 = [drift.lat, drift.lon];
  const short = drift.used < drift.hours
    ? `<p class="hint">Only ${drift.used} h of currents ${back ? 'before' : 'after'} this time: the drift stops there.</p>` : '';
  const rows = run.marks.map(m=>{
    const c = run.path.find(p=>p.h===m.h) || run.path[run.path.length-1];
    return `<tr><td>${sign}${m.h} h</td><td>${fmt.int(haversineKm(p0, [c.lat, c.lon]))} km ${bearingWord(p0, [c.lat, c.lon])}</td>`+
      `<td>${m.ellipse ? fmt.sig2(m.ellipse.area)+' km²' : '—'}</td><td>${fmt.int(100*m.ashore)} %</td></tr>`;
  }).join('');
  const last = run.path[run.path.length-1];
  let km = 0;
  for(let i=1;i<run.path.length;i++) km += haversineKm([run.path[i-1].lat, run.path[i-1].lon], [run.path[i].lat, run.path[i].lon]);
  const fate = run.end==='ashore' ? `It reaches the coast after ${run.endH} h, near ${fmt.latLon1(last.lat, last.lon)}.`
             : run.end==='edge' ? `It leaves the map after ${run.endH} h.`
             : `It ${back ? 'started' : 'ends'} near <b>${fmt.latLon2(last.lat, last.lon)}</b>.`;
  const others = drift.others.map(o=>{
    const q = o.run.path[o.run.path.length-1], gap = haversineKm([last.lat, last.lon], [q.lat, q.lon]);
    return `<p class="meta">The ${fmt.esc(o.words.short)} drift (dashed) from the same point ends <b>${fmt.int(gap)} km</b> from the `+
      `${fmt.esc(M.short)} one${o.run.end==='ashore' ? `, ashore after ${o.run.endH} h` : ''}: the two models’ currents differ by that much here.</p>`;
  }).join('');
  body.innerHTML = short +
    `<p class="meta">The line ${back ? 'traces back' : 'moves'} ${fmt.int(km)} km in ${run.endH} h `+
    `(${fmt.dp(km/run.endH/3.6, 2)} m/s on average). ${fate}</p>` + others + drifterPanelHtml(drift) +
    `<table class="val-table"><caption>The ${DRIFT.n} spread drifts</caption>`+
    `<tr><th>${back ? 'Before' : 'After'}</th><th>Line from ${back ? 'find' : 'release'}</th><th>90 % area</th><th>Ashore</th></tr>${rows}</table>`;
  const days = Math.round((clock[1]-clock[0])/864e5);
  document.getElementById('driftFoot').innerHTML =
    `<p>${M ? `${fmt.esc(M.short)} surface currents from its ${fmt.esc(M.products)}, ${days} days apart` : `The demo’s made-up currents, ${days} days apart`}, `+
    `linear in time between them, in ${DRIFT.stepH} h steps. The line is one drift with no spread. The dots are ${DRIFT.n} more, `+
    `released within about ${DRIFT.r0Km} km and given a random walk (K = ${DRIFT.K} m²/s, an assumed value for eddies smaller `+
    `than the 0.5° grid); amber ones reached the coast. Each ellipse holds 90 % of those still afloat that day.</p>`+
    `<p>Only the current moves them: the data have no wind, so there is no leeway (a life raft or a person in the water also `+
    `moves with the wind, at a few per cent of its speed) and no Stokes drift. ${drifterCheckSentence()} A teaching aid, `+
    `not a search plan: INCOIS runs SARAT for that.</p>`;
}

/* ---- actions ---- */
// release a drift at (lat, lon) from the analysis on screen; false when the point is not on the sea in the map
function startDrift(lat, lon, opts = {}){
  if(!driftStartOk(lat, lon)) return false;
  if(isTourRunning()) stopTour();
  setDriftPlace(false);
  drift = {lat, lon, hours:opts.hours ?? (drift ? drift.hours : 72), dir:opts.dir ?? (drift ? drift.dir : 1),
           drifter:opts.drifter || null};
  computeDrift();
  faceDrift();
  showPanel('drift');
  requestUpdate('full');
  return true;
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
// click-to-release mode on or off (the T key, the Drift button)
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
  hint.firstChild.textContent = 'Click the sea to release a drift. ';
}
// a click on the view while placing (markers.js handleClick sends it here)
function placeDriftAt(e){
  const p = surfaceLatLon(e);
  if(!p) return;
  if(!startDrift(p[0], p[1])) document.getElementById('driftHint').firstChild.textContent = 'That is land: click the sea. ';
}
document.getElementById('driftBtn').addEventListener('click', ()=>setDriftPlace(!state.driftPlace));
document.getElementById('closeDrift').addEventListener('click', clearDrift);
document.querySelectorAll('#driftDir button').forEach(b=>b.addEventListener('click', ()=>setDriftOptions({dir:+b.dataset.dir})));
document.querySelectorAll('#driftHours button').forEach(b=>b.addEventListener('click', ()=>setDriftOptions({hours:+b.dataset.h})));
