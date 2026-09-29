/* Facts: every number quoted in captions and panels, computed once from the data.
   data-pipeline/facts_reference.py recomputes the same numbers independently (Python, straight from the
   JSON), and the tests compare the two. Captions and panels read facts(), never their own arithmetic.

   Definitions
   - Nodes are the 0.5° grid points; land and seabed at a node come from ETOPO1 (gridLand, gridSeabed).
   - Bay of Bengal: lon > 80.5°E, 8–21°N, seabed > 200 m. Arabian Sea: lon < 77°E, lat ≥ 8°N, seabed > 200 m.
   - Model columns: node values, or bilinear between nodes and linear between the 11 levels (realAt).
   - Percentiles interpolate linearly between order statistics (numpy's default).
   - Path: Amphan's in-map fixes over the sea; the interpolated coast crossing is left out of the heat mean.
   - Before / after: the last analysis at least 12 h before the first fix, the first at least 12 h after
     landfall. */
// a property computed on first read and then kept (so an unused mode or variable costs nothing)
function lazyProp(obj, key, fn){
  Object.defineProperty(obj, key, {enumerable:true, configurable:true, get(){
    const v = fn();
    Object.defineProperty(obj, key, {value:v, enumerable:true});
    return v;
  }});
  return obj;
}
let FACTS_CACHE = null;
function facts(){
  if(!FACTS_CACHE) FACTS_CACHE = lazyProp(lazyProp({}, 'real', realFacts), 'synth', synthFacts);
  return FACTS_CACHE;
}

function percentile(values, p){
  const a = Float64Array.from(values.filter(Number.isFinite)).sort();
  if(!a.length) return NaN;
  const h = (a.length-1)*p/100, lo = Math.floor(h);
  return lo+1 < a.length ? a[lo] + (h-lo)*(a[lo+1]-a[lo]) : a[lo];
}
const mean = a=>a.reduce((s,v)=>s+v, 0)/a.length;
// depth where a column first crosses `target`, walking the model levels down to the seabed
function crossDepth(col, seabed, target){
  let v0 = col[0];
  for(let k=0;k<DEPTHS.length-1;k++){
    const z0 = DEPTHS[k];
    if(z0 >= seabed) return null;
    const z1 = Math.min(DEPTHS[k+1], seabed), v1 = lerp(col[k], col[k+1], (z1-z0)/(DEPTHS[k+1]-z0));
    if((v0-target)*(v1-target) <= 0 && v0!==v1) return lerp(z0, z1, (target-v0)/(v1-v0));
    v0 = v1;
  }
  return null;
}
function nodeIndex(i, j){ return i*LON_N + j; }

function realFacts(){
  const m = REAL.meta, NL = DEPTHS.length, times = m.times.map(Date.parse);
  const node = (t, vi, k, i, j)=>REAL.f[(t*m.vars.length+vi)*REAL.per + k*REAL.layer + i*m.nlon + j];
  const col = (vi, lat, lon, t)=>DEPTHS.map(d=>realAt(vi, lat, lon, d, t));
  const tchpAt = (lat, lon, t, seabed)=>tchpColumn(columnInterp(col(0, lat, lon, t)), columnInterp(col(1, lat, lon, t)),
    Math.min(seabed, DEPTHS[NL-1])).q;
  const F = {analysis:{count:times.length, times:m.times}};

  const P = realProfiles;
  F.argo = {profiles:P.length, floats:new Set(P.map(p=>p.id)).size,
    perAnalysis:times.map((_,t)=>P.filter(p=>p.snap===t).length), tempOnly:P.filter(p=>!p.salOk).length};

  const T = REAL_TRACK;
  if(T.length) Object.assign(F, trackFacts(T));
  else Object.assign(F, {track:null, path:null, section:null});

  // basins
  const bay = [], ars = [];
  gridPts.forEach((p,idx)=>{
    if(gridLand[idx] || gridSeabed[idx] <= 200) return;
    const lat = LAT0 + p.i*m.step, lon = LON0 + p.j*m.step;       // exact, unlike an interpolated position
    if(lon > 80.5 && lat >= 8 && lat <= 21) bay.push(p);
    if(lon < 77 && lat >= 8) ars.push(p);
  });
  const nodeCol = (t, p)=>DEPTHS.map((_,k)=>node(t, 0, k, p.i, p.j));
  F.bay = {nodes:bay.length, sstP25:[], t1000Mean:[], d26P10:[], d26P90:[], salMedian:[]};
  F.arabian = {nodes:ars.length, salMedian:[]};
  times.forEach((_,t)=>{
    F.bay.sstP25.push(percentile(bay.map(p=>node(t,0,0,p.i,p.j)), 25));
    F.bay.t1000Mean.push(mean(bay.filter(p=>gridSeabed[nodeIndex(p.i,p.j)] >= 1000).map(p=>node(t,0,NL-1,p.i,p.j))));
    const d26 = bay.map(p=>crossDepth(nodeCol(t,p), gridSeabed[nodeIndex(p.i,p.j)], 26)).filter(d=>d!==null);
    F.bay.d26P10.push(percentile(d26, 10)); F.bay.d26P90.push(percentile(d26, 90));
    F.bay.salMedian.push(percentile(bay.map(p=>node(t,1,0,p.i,p.j)), 50));
    F.arabian.salMedian.push(percentile(ars.map(p=>node(t,1,0,p.i,p.j)), 50));
  });

  // East India Coastal Current: the first node east of the coast (from 78°E) that holds model water, 10–16°N
  F.currents = {eiccMaxNorth:times.map((_,t)=>{
    let best = {v:-Infinity, lat:null};
    for(let i=0;i<LAT_N;i++){
      const lat = LAT0 + i*m.step;
      if(lat < 10 || lat > 16) continue;
      for(let j=0;j<LON_N;j++){
        const lon = LON0 + j*m.step;
        if(lon < 78 || gridLand[nodeIndex(i,j)] || (REAL.wet && !REAL.wet[nodeIndex(i,j)])) continue;
        const v = node(t, 3, 0, i, j);
        if(v > best.v) best = {v, lat};
        break;
      }
    }
    return best;
  })};

  // model-Argo comparison, exactly what the comparison panel shows
  const bands = [[0,100],[100,300],[300,1000]];
  const comp = times.map((_,t)=>{
    const insts = P.filter(p=>p.snap===t), row = {};
    ['temp','sal'].forEach(v=>{
      const all = insts.flatMap(inst=>misfit(inst, t, v));
      row[v] = bands.map(([z0,z1])=>summarize(all.filter(o=>o.z>z0 && o.z<=z1)));
    });
    let worst = null;
    insts.forEach(inst=>{
      const s = summarize(misfit(inst, t, 'temp'));
      if(s && (!worst || s.rmse > worst.rmse)){
        worst = {id:inst.id, rmse:s.rmse, gapHours:Math.round((times[t]-Date.parse(inst.time))/3.6e6),
          modelGapHours:modelGapHours(inst)};
      }
    });
    row.worst = worst;
    return row;
  });
  const deep = comp.map(r=>r.temp[2] && r.temp[2].rmse).filter(Number.isFinite);
  F.comparison = {matchedInTime:P.every(p=>!!p.model), bands:comp.map(({temp, sal})=>({temp, sal})),
    worst:comp.map(r=>r.worst),
    deepRmse:deep.length ? {median:percentile(deep, 50), min:Math.min(...deep), max:Math.max(...deep)} : null};

  F.view = viewFacts();
  F.ranges = rangeFacts((vi, t, k, i, j)=>node(t, vi, k, i, j),
    (t, k, i, j)=>Math.hypot(node(t,2,k,i,j), node(t,3,k,i,j)),
    (p, t, seabed)=>tchpAt(p.lat, p.lon, t, seabed));
  return F;
}

// Amphan's track, the heat under its path and the genesis-to-landfall section (needs at least one fix)
function trackFacts(T){
  const m = REAL.meta, NL = DEPTHS.length, times = m.times.map(Date.parse), F = {};
  const col = (vi, lat, lon, t)=>DEPTHS.map(d=>realAt(vi, lat, lon, d, t));
  const tchpAt = (lat, lon, t, seabed)=>tchpColumn(columnInterp(col(0, lat, lon, t)), columnInterp(col(1, lat, lon, t)),
    Math.min(seabed, DEPTHS[NL-1])).q;
  const sea = T.filter(p=>!p.landfall), lf = T[T.length-1];
  const peak = key=>T.reduce((a,b)=>(b[key]||0) > (a[key]||0) ? b : a);
  const ri = key=>{
    let best = null;
    T.forEach((a,i)=>T.slice(i+1).forEach(b=>{
      if(Date.parse(b.t)-Date.parse(a.t)!==864e5 || a[key]==null || b[key]==null) return;
      // ties go to the later window (17 May 06Z 70->140 kt beats 00Z 60->130 kt)
      if(!best || b[key]-a[key] >= best.to-best.from) best = {from:a[key], to:b[key], start:a.t, end:b.t};
    }));
    return best;
  };
  const pj = peak('usa'), pi = peak('nd');
  F.track = {firstFix:T[0].t, inMapFixes:sea.length,
    leadDays:times.map(x=>(Date.parse(T[0].t)-x)/864e5),
    landfall:{time:lf.t, lat:lf.lat, lon:lf.lon, windJtwc:lf.usa, windImd:lf.nd},
    peakJtwc:{kt:pj.usa, time:pj.t}, peakImd:{kt:pi.nd, time:pi.t, grade:pi.grade},
    ri24Jtwc:ri('usa'), ri24Imd:ri('nd')};

  const before = Math.max(0, times.filter(x=>x <= Date.parse(T[0].t)-12*3.6e6).length-1);
  const after = Math.max(before+1, times.findIndex(x=>x >= Date.parse(lf.t)+12*3.6e6));
  const heat = t=>mean(sea.map(p=>tchpAt(p.lat, p.lon, t, bathyDepth(p.lat, p.lon))));
  F.path = {beforeIndex:before, afterIndex:after, heatBefore:heat(before), heatAfter:heat(after)};

  // the straight line from the first fix to landfall (the "track" section preset), 200 columns
  const chord = t=>{
    const sst = [], d26 = [];
    for(let k=0;k<200;k++){
      const u = k/199, lat = lerp(T[0].lat, lf.lat, u), lon = lerp(T[0].lon, lf.lon, u);
      if(isLand(lat, lon)) continue;
      const c = col(0, lat, lon, t);
      sst.push(c[0]);
      const d = crossDepth(c, Math.min(bathyDepth(lat, lon), DEPTHS[NL-1]), 26);
      if(d!==null) d26.push(d);
    }
    return [mean(sst), mean(d26)];
  };
  const [sb, db] = chord(before), [sa, da] = chord(after);
  F.section = {track:{columns:200, sstBefore:sb, sstAfter:sa, cooling:sb-sa, d26Before:db, d26After:da}};

  return F;
}

// synthetic mode quotes no data numbers, but the colour bar needs its default ranges
function synthFacts(){
  return {view:viewFacts(), ranges:rangeFacts(
    (vi, t, k, i, j)=>{ const p = gridPts[nodeIndex(i,j)]; return (vi ? synthSal : synthTemp)(p.lat, p.lon, DEPTHS[k], t); },
    (t, k, i, j)=>{ const p = gridPts[nodeIndex(i,j)], c = synthCurrentUV(p.lat, p.lon, DEPTHS[k], t); return Math.hypot(c.u, c.v); },
    (p, t, seabed)=>tchpColumn(columnInterp(DEPTHS.map(d=>synthTemp(p.lat, p.lon, d, t))),
      columnInterp(DEPTHS.map(d=>synthSal(p.lat, p.lon, d, t))), Math.min(seabed, DEPTHS[DEPTHS.length-1])).q)};
}

function viewFacts(){
  // metres per scene unit horizontally (111.195 km per degree of latitude) over metres per unit vertically
  return {exaggerationPerSlider:111195/SCALE_XZ*DEPTH_SCALE,
    deepCompression:1/0.085};                        // displayDepth compresses the sea floor below 1000 m
}

// Default colour ranges: p1–p99 over the visible nodes of all analyses, per level and for the whole
// column, rounded outward (temperature 1 °C, salinity 0.1, speed from 0 up to 0.1 m/s). At the surface
// and down to 50 m the temperature range always includes 26 °C. Heat: 0 to p99 rounded up to 25 kJ/cm².
function rangeFacts(val, speed, heat){
  const down = (x, s)=>Math.floor(x/s+1e-9)*s, up = (x, s)=>Math.ceil(x/s-1e-9)*s, r3 = x=>Math.round(x*1000)/1000;
  const visible = (k, fn)=>{
    const v = [];
    gridPts.forEach((p,idx)=>{
      if(gridLand[idx] || DEPTHS[k] > gridSeabed[idx]) return;
      for(let t=0;t<NT;t++) v.push(fn(t, k, p.i, p.j));
    });
    return v;
  };
  const scalar = (fn, range)=>()=>{
    const levels = [], all = [];
    DEPTHS.forEach((d,k)=>{ const v = visible(k, fn); all.push(...v); levels.push(range(v, d)); });
    return {levels, column:range(all, Infinity)};
  };
  const R = {};
  lazyProp(R, 'temp', scalar((t,k,i,j)=>val(0,t,k,i,j), (v, d)=>{
    let lo = down(percentile(v, 1), 1), hi = up(percentile(v, 99), 1);
    if(d <= 50){ lo = Math.min(lo, 26); hi = Math.max(hi, 27); }
    return [lo, hi];
  }));
  lazyProp(R, 'sal', scalar((t,k,i,j)=>val(1,t,k,i,j), v=>[r3(down(percentile(v, 1), 0.1)), r3(up(percentile(v, 99), 0.1))]));
  lazyProp(R, 'cur', scalar(speed, v=>[0, r3(up(percentile(v, 99), 0.1))]));
  lazyProp(R, 'tchp', ()=>{
    const q = [];
    gridPts.forEach((p,idx)=>{ if(!gridLand[idx]) for(let t=0;t<NT;t++) q.push(heat(p, t, gridSeabed[idx])); });
    return {column:[0, up(percentile(q, 99), 25)]};
  });
  return R;
}
