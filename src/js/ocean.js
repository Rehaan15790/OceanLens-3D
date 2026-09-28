/* Ocean science: equation of state, model at the observations, misfit statistics, cyclone heat. */
/* ---- derived oceanographic quantities ----
   UNESCO / EOS-80 one-atmosphere equation of state. Verified against the
   published check values (S=35,T=0 -> 1028.1063; S=35,T=25 -> 1023.3431). */
function seawaterDensity(S, T){
  const rw = 999.842594 + 6.793952e-2*T - 9.095290e-3*T*T + 1.001685e-4*Math.pow(T,3)
           - 1.120083e-6*Math.pow(T,4) + 6.536332e-9*Math.pow(T,5);
  const A = 0.824493 - 4.0899e-3*T + 7.6438e-5*T*T - 8.2467e-7*Math.pow(T,3) + 5.3875e-9*Math.pow(T,4);
  const B = -5.72466e-3 + 1.0227e-4*T - 1.6546e-6*T*T;
  return rw + A*S + B*Math.pow(S,1.5) + 4.8314e-4*S*S;
}
function sigmaT(S,T){ return seawaterDensity(S,T) - 1000; }
function pressureDbar(z){ return 1.0052*z + 2.3e-6*z*z; }
// sound speed, Mackenzie (1981) nine-term equation
function soundSpeed(T,S,z){
  return 1448.96 + 4.591*T - 5.304e-2*T*T + 2.374e-4*Math.pow(T,3)
       + 1.340*(S-35) + 1.630e-2*z + 1.675e-7*z*z
       - 1.025e-2*T*(S-35) - 7.139e-13*T*Math.pow(z,3);
}

const MODEL_FN = {temp:fieldTemp, sal:fieldSal, chl:fieldChl, cur:currentSpeed};
// the model is known on its levels only; in between it is linearly interpolated,
// which is how a Class 4 comparison reads gridded output
function modelAt(varKey, lat, lon, z, t){
  if(varKey==='chl' && dataMode==='real') return null;   // HYCOM has no chlorophyll
  if(varKey==='dens'){
    const T = modelAt('temp',lat,lon,z,t), S = modelAt('sal',lat,lon,z,t);
    return T===null || S===null ? null : sigmaT(S,T);
  }
  const fn = MODEL_FN[varKey];
  if(z <= 0) return fn(lat,lon,0,t);
  for(let k=0;k<DEPTHS.length-1;k++){
    if(z <= DEPTHS[k+1]){
      return lerp(fn(lat,lon,DEPTHS[k],t), fn(lat,lon,DEPTHS[k+1],t), (z-DEPTHS[k])/(DEPTHS[k+1]-DEPTHS[k]));
    }
  }
  return null; // below the deepest model level
}

const obsCache = new Map();
function observeProfile(inst, t){
  if(inst.real) return inst.obs;
  const key = inst.id+'|'+t;
  if(obsCache.has(key)) return obsCache.get(key);
  const tr = inst.traits, n = inst.levels.length;
  const r = mulberry32(hashStr(key));
  // telemetry dropouts: short runs of missing levels, shown as gaps and never filled in
  const missing = new Uint8Array(n);
  const nGaps = r()<0.5 ? 0 : (r()<0.7 ? 1 : 2);
  for(let g=0; g<nGaps; g++){
    const start = 3 + Math.floor(r()*Math.max(1, n-8)), len = 2 + Math.floor(r()*3);
    for(let k=start; k<Math.min(n, start+len); k++) missing[k] = 1;
  }
  const T=[], S=[], C=[];
  inst.levels.forEach((z,k)=>{
    // displaced thermocline: the real column at z looks like the model at a shifted depth
    // (ramped in over the top 60 m so the profile stays monotonic near the surface)
    const shift = tr.thermoShift*Math.min(1, z/60);
    const tv = fieldTemp(inst.lat, inst.lon, z-shift, t) + tr.mlBias*Math.exp(-z/50) + gauss(r)*0.01;
    let sv = fieldSal(inst.lat, inst.lon, z-0.6*shift, t) + tr.freshCap*Math.exp(-z/22) + gauss(r)*0.005;
    if(z===inst.spikeAt) sv -= 0.32;                      // bad sample (conductivity cell glitch)
    const cv = inst.bgc ? Math.max(0.01, fieldChl(inst.lat, inst.lon, Math.max(0, z-0.5*shift), t)
                                         *tr.chlScale*(1+0.12*gauss(r))) : NaN;
    const gone = missing[k]===1;
    T.push(gone?NaN:tv); S.push(gone?NaN:sv); C.push(gone?NaN:cv);
  });
  // QC: density inversion test between successive good levels (Argo real-time QC uses 0.03 kg/m³)
  const D = T.map((tv,k)=>Number.isFinite(tv) ? sigmaT(S[k],tv) : NaN);
  const flag = new Uint8Array(n);
  let prev = -1;
  for(let k=0;k<n;k++){
    if(!Number.isFinite(D[k])) continue;
    if(prev>=0 && D[k] < D[prev]-0.03){ flag[k] = 1; continue; } // keep comparing against the last good level
    prev = k;
  }
  const p = {z:inst.levels, T, S, C, D, flag, missing};
  obsCache.set(key, p);
  return p;
}
function obsArray(p, varKey){ return {temp:p.T, sal:p.S, chl:p.C, dens:p.D}[varKey]; }
// value of an observed profile at depth z; null inside a gap, on a flagged level or below the
// profile. Above the first level (5 m) the first value stands in for the mixed layer.
function obsInterp(p, arr){
  const good = k => Number.isFinite(arr[k]) && !p.flag[k];
  return z=>{
    for(let k=0;k<p.z.length;k++){
      if(p.z[k] < z) continue;
      if(!good(k)) return null;
      if(k===0 || p.z[k]===z) return arr[k];
      if(!good(k-1)) return null;
      return lerp(arr[k-1], arr[k], (z-p.z[k-1])/(p.z[k]-p.z[k-1]));
    }
    return null;
  };
}

/* ---- model vs observation misfit (GODAE OceanView Class 4 style) ----
   obs − model at every good observed level, the model interpolated to the
   observation's position and depth. */
function misfit(inst, t, varKey){
  if(inst.real) t = inst.snap;   // a real profile is compared with the model at its own snapshot
  const p = observeProfile(inst, t), arr = obsArray(p, varKey), out = [];
  p.z.forEach((z,k)=>{
    if(!Number.isFinite(arr[k]) || p.flag[k]) return;
    const m = modelAt(varKey, inst.lat, inst.lon, z, t);
    if(m!==null) out.push({z, d:arr[k]-m});
  });
  return out;
}
function summarize(diffs){
  if(!diffs.length) return null;
  let s=0, s2=0;
  diffs.forEach(o=>{ s+=o.d; s2+=o.d*o.d; });
  return {bias:s/diffs.length, rmse:Math.sqrt(s2/diffs.length), n:diffs.length};
}

/* ---- tropical cyclone heat potential ----
   Leipper & Volgenau (1972); Goni et al. (2009): heat held in water warmer than
   26 °C, Q = cp ∫ ρ (T − 26) dz from the surface down to D26. Integrated in 2 m
   steps with ρ from EOS-80. Returns kJ/cm² and D26 in metres; d26 is null when the
   column stays above 26 °C all the way to `bottom`, q is null when a data gap hides
   the crossing. */
const CP_SEAWATER = 4000;     // J kg⁻¹ K⁻¹, seawater near 35 PSU and 25 °C
const TCHP_THRESHOLD = 50;    // kJ/cm², commonly associated with cyclone intensification
function tchpColumn(tempAt, salAt, bottom){
  const T0 = tempAt(0), S0 = salAt(0);
  if(T0===null || S0===null) return {q:null, d26:null};
  if(T0 <= 26) return {q:0, d26:0};
  const dz = 2;
  let q = 0, prevT = T0, prevS = S0;
  for(let z=dz; z<=bottom; z+=dz){
    const T = tempAt(z), S = salAt(z);
    if(T===null || S===null) return {q:null, d26:null};
    if(T <= 26){
      const f = (prevT-26)/(prevT-T);                                 // part of this step still above 26 °C
      q += seawaterDensity(prevS, prevT)*(prevT-26)*0.5*f*dz;         // triangle down to the crossing
      return {q:q*CP_SEAWATER/1e7, d26:z-dz+f*dz};
    }
    q += seawaterDensity(0.5*(S+prevS), 0.5*(T+prevT))*(0.5*(T+prevT)-26)*dz;
    prevT = T; prevS = S;
  }
  return {q:q*CP_SEAWATER/1e7, d26:null};
}
function columnInterp(vals){
  return z=>{
    if(z <= 0) return vals[0];
    for(let k=0;k<DEPTHS.length-1;k++){
      if(z <= DEPTHS[k+1]) return lerp(vals[k], vals[k+1], (z-DEPTHS[k])/(DEPTHS[k+1]-DEPTHS[k]));
    }
    return null;
  };
}
function modelTchp(lat, lon, t, seabed){
  const Tv = DEPTHS.map(d=>fieldTemp(lat,lon,d,t)), Sv = DEPTHS.map(d=>fieldSal(lat,lon,d,t));
  return tchpColumn(columnInterp(Tv), columnInterp(Sv), Math.min(seabed, DEPTHS[DEPTHS.length-1]));
}
function obsTchp(inst, p, seabed){
  const r = tchpColumn(obsInterp(p,p.T), obsInterp(p,p.S), inst.maxDepth);
  // a profile that stops above the 26 °C crossing can't give the heat content
  if(r.d26===null && r.q!==null && inst.maxDepth < seabed-20) return {q:null, d26:null};
  return r;
}
const tchpCache = new Map();
function tchpGrid(t){
  const key = dataMode+t;
  if(!tchpCache.has(key)){
    tchpCache.set(key, gridPts.map((p,idx)=> gridLand[idx] ? null : modelTchp(p.lat, p.lon, t, gridSeabed[idx])));
  }
  return tchpCache.get(key);
}
