/* ============================================================
   SYNTHETIC OCEAN DATASET
   Structured like the real thing: lat/lon/depth/time grid with
   temperature, salinity, current (u,v); plus Argo float and
   glider profiles. Deterministic procedural generation.
   ============================================================ */

function synthTemp(lat, lon, depth, t){
  const latN=(lat-LAT0)/(LAT1-LAT0), lonN=(lon-LON0)/(LON1-LON0);
  // the Bay of Bengal belongs to the Indo-Pacific warm pool: warmer surface and a deeper warm
  // layer (river freshwater forms a barrier layer), which is why its cyclones find so much heat
  const bay = 1/(1+Math.exp(-(lon-81)/1.5));
  let surf = 29.7 - 2.6*latN + 0.7*Math.sin(lonN*6.283 + t*0.32) + 1.2*bay;
  surf += 1.3*Math.sin(lat*0.85 + t*0.4)*Math.cos(lon*0.65 - t*0.22);
  const thermoDepth = 85 + 45*Math.sin(lat*0.5 + lon*0.28 + t*0.2) + 35*bay;
  const deepT = 4.2 + 2*Math.exp(-depth/1800);
  const k = 1/(1+Math.exp((depth-thermoDepth)/26));
  return deepT + (surf-deepT)*k;
}
function synthSal(lat, lon, depth, t){
  const lonN=(lon-LON0)/(LON1-LON0);
  // Arabian Sea is saltier; Bay of Bengal freshened by river runoff
  const surf = 36.3 - 3.1*lonN + 0.35*Math.sin(lat*0.6+t*0.25) - (lonN>0.55? 0.6*Math.sin(t*0.3):0);
  // deep salinity rises slightly with depth; the halocline sits above the
  // thermocline so the resulting water column is statically stable everywhere
  // (verified: zero density inversions across the full lat/lon/depth/time grid)
  const deepS = 34.9 + Math.min(depth,1200)*0.0006;
  const k = 1/(1+Math.exp((depth-70)/25));
  return deepS + (surf-deepS)*k;
}
function synthCurrentUV(lat, lon, depth, t){
  const x=(lon-LON0), y=(lat-LAT0), e=0.01;
  const psi=(xx,yy)=>Math.sin(xx*0.5+t*0.15)*Math.cos(yy*0.6)+0.5*Math.sin(xx*0.32 - yy*0.42 + t*0.12);
  const p0=psi(x,y);
  const u=-(psi(x,y+e)-p0)/e, v=(psi(x+e,y)-p0)/e;
  const decay=Math.exp(-depth/160);
  return {u:u*decay*0.42, v:v*decay*0.42};
}

function synthChl(lat, lon, depth, t){
  const surf = 0.15 + 0.35*Math.max(0, Math.sin((lon-LON0)*0.3+t*0.2));
  const dcm = 60 + 20*Math.sin(lat*0.4+t*0.15); // deep chlorophyll max
  const bump = Math.exp(-Math.pow(depth-dcm,2)/1400)*0.55;
  const base = surf*Math.exp(-depth/200);
  return Math.max(0.02, base+bump);
}

// Argo floats — fixed positions, WMO-style ids
const ARGO_IDS = [2902871,2902934,2902967,2903011,2903058,2903102,2903147,2903190,2903221,2903266,2903305,2903341,2903378,2903412];
const argoFloats = ARGO_IDS.map((id,idx)=>{
  const lat = lerp(LAT0+1, LAT1-1, (Math.sin(idx*12.9898)*0.5+0.5));
  const lon = lerp(LON0+1, LON1-1, (Math.sin(idx*78.233+4)*0.5+0.5));
  return {id:'ARGO-'+id, kind:'argo', lat, lon,
    cycle: 40+idx*3, lastSurfacing: `2026-08-${(28-idx%20).toString().padStart(2,'0')}`};
});

// Gliders — tracks of waypoints, id INCOIS-SG-0x
const gliderTracks = [0,1,2].map(g=>{
  const baseLat = lerp(LAT0+2, LAT1-2, [0.25,0.55,0.8][g]);
  const baseLon = lerp(LON0+2, LON1-2, [0.2,0.5,0.75][g]);
  const wps = [];
  for(let w=0; w<6; w++){
    wps.push({
      lat: baseLat + Math.sin(w*1.3+g)*1.4,
      lon: baseLon + Math.cos(w*1.1+g*2)*1.6,
      depth: 20 + (w%2)*350,
      time: `Day ${w+1}`
    });
  }
  return {id:'INCOIS-SG-0'+(g+1), kind:'glider', waypoints: wps};
});

argoFloats.forEach(f=>{
  const [la,lo] = nearestWater(f.lat, f.lon, 1200); // Argo floats profile deep water
  f.lat = la; f.lon = lo;
});
gliderTracks.forEach(t=>{
  t.waypoints.forEach(w=>{
    const [la,lo] = nearestWater(w.lat, w.lon, 250); // gliders work the shelf/slope
    w.lat = la; w.lon = lo;
    w.depth = Math.min(w.depth, Math.round(bathyDepth(la,lo)*0.85));
  });
});

/* ============================================================
   SYNTHETIC IN-SITU OBSERVATIONS
   Each instrument samples a "true" ocean that differs from the model the
   way real ones do: a displaced thermocline, a warmer/cooler mixed layer,
   a fresher river-plume cap in the Bay that models under-resolve, sensor
   noise and missing levels. Deterministic per instrument, so a demo always
   shows the same numbers.
   ============================================================ */
function mulberry32(a){
  return function(){
    a|=0; a=a+0x6D2B79F5|0;
    let t=Math.imul(a^a>>>15, 1|a);
    t=t+Math.imul(t^t>>>7, 61|t)^t;
    return ((t^t>>>14)>>>0)/4294967296;
  };
}
function gauss(rng){ let u=0; while(u===0) u=rng(); return Math.sqrt(-2*Math.log(u))*Math.cos(6.2831853*rng()); }
function hashStr(s){ let h=2166136261; for(let i=0;i<s.length;i++){ h^=s.charCodeAt(i); h=Math.imul(h,16777619); } return h>>>0; }

// core-Argo style sampling: dense near the surface, coarser at depth
const ARGO_LEVELS = [];
for(let z=5; z<=100; z+=5) ARGO_LEVELS.push(z);
for(let z=110; z<=300; z+=10) ARGO_LEVELS.push(z);
for(let z=325; z<=1000; z+=25) ARGO_LEVELS.push(z);

// what the model gets wrong at this spot — fixed for the life of the instrument
function instrumentTraits(key, lon){
  const r = mulberry32(hashStr(key));
  return {
    thermoShift: Math.max(-28, Math.min(28, gauss(r)*12)), // m; + means the real thermocline is deeper
    mlBias: gauss(r)*0.25,                                  // °C, fades out below the mixed layer
    freshCap: lon>80.5 ? -(0.3+r()*0.6) : gauss(r)*0.04,    // PSU; the Bay river plume the model misses
    chlScale: 0.7 + r()*0.8
  };
}
function makeInstrument(id, kind, lat, lon, maxDepth, opts){
  const levels = kind==='argo' ? ARGO_LEVELS.filter(z=>z<=maxDepth)
    : Array.from({length:Math.floor(maxDepth/5)}, (_,k)=>5+k*5);   // gliders sample every ~5 m
  return Object.assign({id, kind, lat, lon, maxDepth, levels, bgc:false, spikeAt:null,
    traits:instrumentTraits(opts.traitKey || id, lon)}, opts);
}
const synthInstruments = [];
argoFloats.forEach((f,idx)=>{
  const maxDepth = Math.min(1000, Math.floor(bathyDepth(f.lat,f.lon)*0.95/25)*25);
  f.inst = makeInstrument(f.id, 'argo', f.lat, f.lon, maxDepth, {
    bgc: idx%3===0,                   // every third float carries a chlorophyll sensor (BGC-Argo)
    spikeAt: idx===5 ? 300 : null,    // one bad salinity sample, so the QC check has something to catch
    userData: {type:'argo', data:f}
  });
  synthInstruments.push(f.inst);
});
gliderTracks.forEach(track=>{
  track.waypoints.forEach((w,wi)=>{
    const maxDepth = Math.max(30, Math.min(370, Math.floor(bathyDepth(w.lat,w.lon)*0.9/5)*5));
    w.inst = makeInstrument(track.id+' · wp'+(wi+1), 'glider', w.lat, w.lon, maxDepth, {
      bgc:true, traitKey:track.id, userData:{type:'glider', data:track, wp:w, wpIndex:wi}
    });
    synthInstruments.push(w.inst);
  });
});

const SAMPLE_TRACK = [
  [10.4,89.3],[11.2,88.7],[12.1,88.0],[13.1,87.2],[14.1,86.4],[15.1,85.6],[16.1,84.8],[17.0,84.0],[17.8,83.5]
].map(([lat,lon],k)=>({lat, lon, h:k*12}));   // 12-hourly positions, landfall on the Andhra coast
