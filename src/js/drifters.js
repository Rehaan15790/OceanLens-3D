/* ============================================================
   DRIFTING BUOYS (the drift tool's check against the real ocean)
   NOAA Global Drifter Program buoys inside the map in the real-data window, every 6 h (#snapshot-drifters, written by
   data-pipeline/build_drifters.py). The Drifting buoys overlay draws their tracks and a mark where each one is at the
   analysis on screen; a click on a mark releases a drift there, and the drift panel then shows where the buoy really
   went next to the model's line. drifterCheck() does that from every buoy at every analysis, for the numbers the
   panel quotes; data-pipeline/facts_reference.py drifter_check recomputes them.
   A drogued SVP buoy follows the current at 15 m; one that has lost its drogue also moves with the wind.
   ============================================================ */
const DRIFTER_HEX = 0x7ee0ff;
const DRIFTERS = (()=>{
  const el = document.getElementById('snapshot-drifters');
  if(!el) return null;
  let d;
  try { d = JSON.parse(el.textContent); } catch(e){ console.warn('the drifter data does not parse; it is left out'); return null; }
  if(!d || d.schema!==1 || !Array.isArray(d.drifters)){ console.warn('the drifter data has another schema; it is left out'); return null; }
  const t0 = Date.parse(d.meta.t0), step = d.meta.stepH*3.6e6;
  const list = d.drifters.map(b=>{
    const fixes = new Map(b.fixes.map(([k, lat, lon])=>[t0 + k*step, {lat, lon}]));
    return {id:b.id, type:b.type, drogueLost:b.drogueLost ? Date.parse(b.drogueLost) : null, fixes,
            path:b.fixes.map(([, lat, lon])=>({lat, lon}))};
  });
  return {meta:d.meta, stepH:d.meta.stepH, list, byId:new Map(list.map(b=>[b.id, b]))};
})();
// a buoy's position at time ms (a 6-hourly fix), null when it has none then
function drifterAt(b, ms){ return b.fixes.get(ms) || null; }
// true while the buoy still has its drogue at time ms
function drifterDrogued(b, ms){ return b.drogueLost===null || ms < b.drogueLost; }

/* From every buoy position at an analysis with 72 h of currents after it, on the sea: the central drift (no spread)
   with currents uv at the times of clock; at 24, 48 and 72 h the distance (km) from where the buoy really was, and the
   distance had the object stayed put (stillKm). Medians, and the count of pairs at each time. */
function drifterCheck(uv, clock){
  const pairs = {24:[], 48:[], 72:[]}, used = new Set();
  let releases = 0;
  DRIFTERS.list.forEach(b=>clock.forEach((ms, t0)=>{
    const f = drifterAt(b, ms);
    if(!f || isLand(f.lat, f.lon) || driftHoursAvailable(clock, t0, 1) < 72) return;
    const run = driftRun({lat:f.lat, lon:f.lon, t0, hours:72, dir:1, uv, clock, n:0});
    releases++;
    used.add(b.id);
    DRIFT.hours.forEach(h=>{
      const g = drifterAt(b, ms + h*3.6e6), p = run.path.find(q=>q.h===h);
      if(g && p) pairs[h].push([haversineKm([p.lat, p.lon], [g.lat, g.lon]), haversineKm([f.lat, f.lon], [g.lat, g.lon])]);
    });
  }));
  return {drifters:used.size, releases, byHours:DRIFT.hours.map(h=>{
    const a = pairs[h];
    return a.length ? {h, n:a.length, km:percentile(a.map(x=>x[0]), 50), stillKm:percentile(a.map(x=>x[1]), 50)} : {h, n:0};
  })};
}
// the check for the real-data model on screen (facts.js keeps it per model)
function drifterFacts(){
  if(!DRIFTERS) return null;
  const uv = (la, lo, d, t)=>({u:realAt(2, la, lo, d, t), v:realAt(3, la, lo, d, t)});
  return drifterCheck(uv, REAL.meta.times.map(t=>Date.parse(t)));
}

/* ---- a drift released from a buoy: where the buoy really went ---- */
// the buoy's own path and distances for drift `d` (computeDrift calls it with the times of its currents); null unless
// d started from a buoy at the analysis on screen, on that buoy's position then
function drifterTruth(d, clock){
  if(!d.drifter || dataMode!=='real' || !DRIFTERS || !d.run) return null;
  const b = DRIFTERS.byId.get(d.drifter), ms0 = clock[d.t0], f = b && drifterAt(b, ms0);
  if(!f || Math.abs(f.lat-d.lat) > 1e-6 || Math.abs(f.lon-d.lon) > 1e-6) return null;
  const at = h=>drifterAt(b, ms0 + d.dir*h*3.6e6), path = [];
  for(let h=0; h<=d.used; h+=DRIFTERS.stepH){
    const g = at(h);
    if(!g) break;
    path.push({h, lat:g.lat, lon:g.lon});
  }
  const near = (run, h, g)=>{ const p = run.path.find(q=>q.h===h); return p ? haversineKm([p.lat, p.lon], [g.lat, g.lon]) : null; };
  const rows = DRIFT.hours.filter(h=>h<=d.used).map(h=>{
    const g = at(h);
    return g ? {h, km:near(d.run, h, g), others:d.others.map(o=>near(o.run, h, g)), stillKm:haversineKm([f.lat, f.lon], [g.lat, g.lon])} : null;
  }).filter(Boolean);
  return {b, path, rows, drogued:drifterDrogued(b, ms0)};
}
// the drift panel's paragraph and table for a buoy (or how to start from one)
function drifterPanelHtml(d){
  if(dataMode!=='real' || !DRIFTERS) return '';
  if(!d.real){
    return state.showDrifters ? '' : `<p class="hint">Turn on <b>Drifting buoys</b> under Overlays and click a buoy to `+
      `release a drift where a real buoy was, and see where it really went.</p>`;
  }
  const r = d.real, back = d.dir < 0, sign = back ? '−' : '+', M = MODEL();
  const km = v=>v===null ? 'ashore or off the map' : `${fmt.int(v)} km`;
  const head = `<tr><th>${back ? 'Before' : 'After'}</th><th>${fmt.esc(M.short)} line</th>` +
    d.others.map(o=>`<th>${fmt.esc(o.words.short)} line</th>`).join('') + `<th>Had it stayed put</th></tr>`;
  const rows = r.rows.map(x=>`<tr><td>${sign}${x.h} h</td><td>${km(x.km)}</td>${x.others.map(v=>`<td>${km(v)}</td>`).join('')}`+
    `<td>${fmt.int(x.stillKm)} km</td></tr>`).join('');
  const lost = r.b.drogueLost && !r.drogued ? new Date(r.b.drogueLost).toISOString().slice(0, 10) : null;
  return `<table class="val-table drifter-table"><caption>Distance from the real buoy ${fmt.esc(r.b.id)} (blue line)</caption>${head}${rows}</table>`+
    `<p class="meta">${r.drogued ? 'It still had its drogue, so it followed the current at about 15 m.'
      : `It had lost its drogue${lost ? ` (${lost})` : ''}, so the wind moved it too; the model line has no wind.`}`+
    `${r.rows.length < DRIFT.hours.filter(h=>h<=d.used).length ? ' The buoy has no position at some of these times.' : ''}</p>`;
}
// the drift panel's sentence on how the drift compares with real buoys (from facts, so the tests check it)
function drifterCheckSentence(){
  if(dataMode!=='real' || !DRIFTERS) return 'Real-data mode checks the drift against real drifting buoys.';
  const c = facts().real.drifters, r = c && c.byHours.find(x=>x.h===72);
  if(!r || !r.n) return 'No drifting buoy in the map could check it.';
  return `Checked against ${c.drifters} real drifting buoys (NOAA Global Drifter Program): from ${c.releases} releases, `+
    `the ${fmt.esc(MODEL().short)} line ends a median ${fmt.int(r.km)} km from the buoy after 72 h, against `+
    `${fmt.int(r.stillKm)} km had the object stayed put.`;
}

/* ---- the overlay: every buoy's track, and a mark where it is at the analysis on screen ---- */
const drifterGroup = new THREE.Group();
scene.add(Render.register('drifters', drifterGroup));
let drifterMarks = [];      // [{pos, id}] for picking
let driftersKey = null;
const DRIFTER_GPU = Render.gpuColor([0x7e/255, 0xe0/255, 1]);
function drifterLayerOn(){ return dataMode==='real' && !!DRIFTERS && !!state.showDrifters; }
// rebuild the tracks and marks when the analysis on screen or the data source changes
function refreshDrifters(){
  const key = drifterLayerOn() ? state.time : 'off';
  if(key===driftersKey) return;
  driftersKey = key;
  drifterGroup.children.slice().forEach(o=>{ drifterGroup.remove(o); o.geometry.dispose(); o.material.dispose(); });
  drifterMarks = [];
  if(key==='off') return;
  const add = (obj, order)=>{ obj.renderOrder = order; obj.frustumCulled = false; drifterGroup.add(obj); };
  const ms = Date.parse(META.times[state.time]), pos = [], col = [];
  DRIFTERS.list.forEach(b=>{
    add(new THREE.Mesh(surfaceRibbon(b.path, 0.009, 0.004), Render.flat(DRIFTER_HEX, {opacity:0.55, side:THREE.DoubleSide})),
        Render.ORDER.surfaceLines);
    const f = drifterAt(b, ms);
    if(!f) return;
    const v = _dv(f.lat, f.lon, 0.012);
    pos.push(v.x, v.y, v.z); col.push(...DRIFTER_GPU);
    drifterMarks.push({pos:v, id:b.id});
  });
  if(pos.length){
    const g = new THREE.BufferGeometry();
    g.setAttribute('position', new THREE.Float32BufferAttribute(pos, 3));
    g.setAttribute('color', new THREE.Float32BufferAttribute(col, 3));
    add(new THREE.Points(g, Render.marks({shape:'circle', sizePx:11})), Render.ORDER.markers);
  }
}
function applyDrifterVisibility(){ drifterGroup.visible = drifterLayerOn(); }
// a click on a buoy's mark: release a drift where it is at the analysis on screen
function startDriftFromDrifter(id){
  const b = DRIFTERS && DRIFTERS.byId.get(id), f = b && drifterAt(b, Date.parse(META.times[state.time]));
  return f ? startDrift(f.lat, f.lon, {drifter:id}) : false;
}
