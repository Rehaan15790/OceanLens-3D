/* Real data: HYCOM fields, Argo profiles and the Amphan track, decoded from the embedded snapshot. */
// HYCOM on the app's own 0.5° grid and levels, laid out [time][variable][level][lat][lon]
const REAL = HAS_REAL ? (()=>{
  const m = REAL_DATA.meta, q = b64Int16(REAL_DATA.model);
  const per = m.depths.length*m.nlat*m.nlon, f = new Float32Array(q.length);
  for(let n=0;n<q.length;n++){
    const [off, sc] = m.quant[m.vars[Math.floor(n/per) % m.vars.length]];
    f[n] = off + q[n]*sc;
  }
  return {meta:m, f, per, layer:m.nlat*m.nlon};
})() : null;
// bilinear in latitude/longitude, linear between model levels
function realAt(vi, lat, lon, depth, t){
  const m = REAL.meta, f = REAL.f;
  const y = Math.max(0, Math.min(m.nlat-1.000001, (lat-m.lat0)/m.step));
  const x = Math.max(0, Math.min(m.nlon-1.000001, (lon-m.lon0)/m.step));
  const i = Math.floor(y), j = Math.floor(x), fy = y-i, fx = x-j;
  const D = m.depths, z = Math.max(0, Math.min(D[D.length-1], depth));
  let k = 0;
  while(k < D.length-2 && z > D[k+1]) k++;
  const fz = (z-D[k])/(D[k+1]-D[k]);
  const at = kk=>{
    const o = (t*m.vars.length+vi)*REAL.per + kk*REAL.layer + i*m.nlon + j;
    return (f[o]*(1-fx) + f[o+1]*fx)*(1-fy) + (f[o+m.nlon]*(1-fx) + f[o+m.nlon+1]*fx)*fy;
  };
  return fz===0 ? at(k) : at(k)*(1-fz) + at(k+1)*fz;
}

/* ---- real Argo profiles (REAL_DATA.argo), one set per snapshot ---- */
const ARGO_MODE = {D:'delayed mode (QC\'d)', A:'adjusted', R:'real time'};
const realProfiles = HAS_REAL ? (()=>{
  const A = REAL_DATA.argo, nL = A.levels.length, miss = REAL_DATA.meta.missing, Q = REAL_DATA.meta.quant;
  const dec = (b64, key)=>{ const q = b64Int16(b64), [off, sc] = Q[key]; return Array.from(q, v=>v===miss ? NaN : off+v*sc); };
  const T = dec(A.T, 'temp'), S = dec(A.S, 'sal'), C = dec(A.C, 'chl');
  return A.profiles.map((p,n)=>{
    const cut = arr=>arr.slice(n*nL, (n+1)*nL);
    const Tn = cut(T), Sn = cut(S), Cn = cut(C);
    const ok = Tn.map((v,k)=>Number.isFinite(v) && Number.isFinite(Sn[k]));
    const first = ok.indexOf(true), last = ok.lastIndexOf(true);
    const inst = {id:'ARGO-'+p.w, kind:'argo', real:true, lat:p.la, lon:p.lo, snap:p.s, time:p.t, mode:p.m,
      cycle:p.c, levels:A.levels, maxDepth:A.levels[last], bgc:Cn.some(Number.isFinite)};
    // the same density-inversion check the synthetic profiles get
    const D = Tn.map((v,k)=>ok[k] ? sigmaT(Sn[k], v) : NaN);
    const flag = new Uint8Array(nL);
    let prev = -1;
    for(let k=0;k<nL;k++){
      if(!Number.isFinite(D[k])) continue;
      if(prev>=0 && D[k] < D[prev]-0.03){ flag[k] = 1; continue; }
      prev = k;
    }
    const missing = new Uint8Array(nL);
    for(let k=first;k<=last;k++) if(!ok[k]) missing[k] = 1;
    inst.obs = {z:A.levels, T:Tn.map((v,k)=>ok[k]?v:NaN), S:Sn.map((v,k)=>ok[k]?v:NaN), C:Cn, D, flag, missing};
    inst.userData = {type:'argo', data:{id:inst.id, cycle:p.c, lastSurfacing:p.t, inst}};
    return inst;
  });
})() : [];

// how far a real profile is from the model analysis it is compared with (up to 1.5 days)
function analysisGap(inst){
  const h = Math.round((Date.parse(REAL.meta.times[inst.snap]) - Date.parse(inst.time))/3.6e6);
  return h===0 ? 'same time as the analysis' : `${Math.abs(h)} h ${h>0 ? 'before' : 'after'} the analysis`;
}

// Amphan's fixes over the sea inside the map, ending at the coast crossing interpolated between
// the last fix at sea and the first over land (IBTrACS is six-hourly; landfall fell in between)
const REAL_TRACK = HAS_REAL ? (()=>{
  const all = REAL_DATA.track.fixes.map(([t, lat, lon, usa, nd, grade])=>({t, lat, lon, usa, nd, grade}));
  const T = [], mix = (x, y, u)=> x==null || y==null ? (x ?? y) : Math.round(lerp(x, y, u));
  for(const f of all){
    if(f.lat>=LAT0 && f.lat<=LAT1 && f.lon>=LON0 && f.lon<=LON1 && !isLand(f.lat, f.lon)){ T.push(f); continue; }
    if(!T.length) continue;
    const a = T[T.length-1];
    for(let s=1;s<=60;s++){
      const u = s/60, lat = lerp(a.lat, f.lat, u), lon = lerp(a.lon, f.lon, u);
      if(isLand(lat, lon) || lat > LAT1){
        T.push({t:new Date(lerp(Date.parse(a.t), Date.parse(f.t), u)).toISOString().slice(0,16)+'Z', lat, lon,
          usa:mix(a.usa, f.usa, u), nd:mix(a.nd, f.nd, u), grade:a.grade, landfall:true});
        break;
      }
    }
    break;
  }
  return T;
})() : [];
REAL_TRACK.forEach(f=>{ f.h = (Date.parse(f.t)-Date.parse(REAL_TRACK[0].t))/3.6e6; });
