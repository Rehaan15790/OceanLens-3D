/* Real data: the model fields, Argo profiles and the Amphan track, decoded from the embedded snapshots.
   The main snapshot holds a model (HYCOM) with the floats, the track and the relief. A second, model-only snapshot
   (#snapshot-glorys, written by build.py from the GLORYS snapshot) holds another model on the same grid and times.
   One model is on screen at a time (realModel); setRealModel() switches. */
// one snapshot's model on the app's own 0.5° grid and levels, laid out [time][variable][level][lat][lon]
function decodeModel(d){
  const m = d.meta, q = b64Int16(d.model);
  const per = m.depths.length*m.nlat*m.nlon, one = new Float32Array(q.length);
  for(let n=0;n<q.length;n++){
    const [off, sc] = m.quant[m.vars[Math.floor(n/per) % m.vars.length]];
    one[n] = off + q[n]*sc;
  }
  // build.py embeds a field repeated at every time once (meta.timesStored 1, IGORA's monthly mean): repeat it here
  let f = one;
  if(m.timesStored===1 && m.times.length > 1){
    f = new Float32Array(one.length*m.times.length);
    for(let t=0;t<m.times.length;t++) f.set(one, t*one.length);
  }
  // mask.wet: one bit per cell, [level][lat][lon], most significant bit first; 1 = the cell holds model
  // water. Cells without it carry values filled from their neighbours.
  let wet = null;
  if(d.mask){
    const bits = atob(d.mask.wet);
    wet = new Uint8Array(per);
    for(let n=0;n<per;n++) wet[n] = (bits.charCodeAt(n>>3) >> (7-(n&7))) & 1;
  }
  return {meta:m, f, per, layer:m.nlat*m.nlon, wet};
}
// the case the main snapshot holds: Cyclone Amphan (a snapshot without meta.case), or another the pipeline wrote, such as
// a forecast (build_forecast.py): its name, its label on the Real data button, and whether the Amphan tour applies
const CASE = (()=>{
  const c = REAL_DATA.meta.case;
  if(!c) return {kind:'cyclone', name:'Cyclone Amphan', button:'Amphan 2020', storm:'Amphan', tour:true};
  const forecast = c.kind==='forecast', issued = forecast && c.issued ? Date.parse(c.issued + 'T00:00Z') : NaN;
  return {kind:String(c.kind || 'case'), name:String(c.name || REAL_DATA.meta.title || 'Real data'),
          button:forecast && Number.isFinite(issued) ? `forecast ${fmt.dayMonth(new Date(issued).toISOString())}` : String(c.name || 'case'),
          storm:null, tour:false, forecast, issued};
})();
// the Real data button names the case
document.querySelectorAll('#dataToggle button[data-mode="real"]').forEach(b=>{ b.textContent = `Real data: ${CASE.button}`; });

// the words the app uses for a model; meta.model names it, and a snapshot without meta.model is HYCOM.
// temperature: 'in-situ' or 'potential' (meta.model.temperature when the snapshot says); argo: 'yes' when the model's
// documentation says it assimilates Argo, else 'likely'; monthly: one monthly mean stands for every analysis day
const MODEL_WORDS = {
  HYCOM: {product:'HYCOM analysis', products:'analyses', label:'HYCOM', kind:'analysis',
          data:'HYCOM GOFS 3.1', note:'HYCOM + NCODA analysis (GOFS 3.1)', temperature:'in-situ', argo:'yes'},
  GLORYS:{product:'GLORYS daily mean', products:'daily means', label:'GLORYS', kind:'reanalysis',
          data:'Copernicus Marine GLORYS12V1', note:'Copernicus Marine GLORYS12V1 reanalysis, daily means',
          temperature:'potential', argo:'yes'},
  IGORA: {product:'IGORA May 2020 mean', products:'monthly mean', label:'IGORA', kind:'reanalysis',
          data:'INCOIS IGORA reanalysis (INCOIS LAS)', temperature:'potential', argo:'likely', monthly:true,
          note:'INCOIS Global Ocean ReAnalysis from the INCOIS Live Access Server: the May 2020 monthly mean, the same at every analysis day'},
  Forecast:{product:'daily mean', products:'daily means', label:'Forecast', kind:'forecast',
          data:'Copernicus Marine global analysis and forecast', note:'Copernicus Marine global ocean analysis and forecast (1/12°), daily means',
          temperature:'potential', argo:'yes'},
};
function modelWords(meta){
  const mm = meta.model || {}, short = mm.short || 'HYCOM';
  const w = Object.assign({short}, MODEL_WORDS[short] || {product:`${short} model output`, products:'model times',
    label:short, kind:'model', data:short, note:short, temperature:'in-situ', argo:'likely'});
  if(mm.temperature==='potential' || mm.temperature==='in-situ') w.temperature = mm.temperature;
  if(mm.monthly) w.monthly = true;
  return w;
}
// the comparison with floats is a consistency check: said as firmly as the model's documentation allows
function assimilationWords(short){
  const M = MODEL();
  return M.argo==='yes' ? `${M.short} assimilates Argo${short ? ':' : ', so this is'} a consistency check, not independent validation.`
    : `${M.short} likely assimilates Argo${short ? ':' : ', so read this as'} a consistency check, not independent validation.`;
}
// another model must sit on the main snapshot's grid, levels and times to be drawn and compared on it
function sameGridAsMain(m){
  const M = REAL_DATA.meta;
  return !!m && ['lat0', 'lon0', 'step', 'nlat', 'nlon'].every(k=>m[k]===M[k]) && Array.isArray(m.depths) &&
    m.depths.join()===M.depths.join() && m.times.join()===M.times.join() && m.vars.join()===M.vars.join();
}
// the real-data models in the page, main snapshot first; the others are decoded the first time they are shown.
// A data server can add more (addServerModel, called from import.js).
const REAL_MODELS = (()=>{
  const list = [{id:modelWords(REAL_DATA.meta).short.toLowerCase(), words:modelWords(REAL_DATA.meta),
                 atObs:!!(REAL_DATA.argo && REAL_DATA.argo.model), decoded:null, data:()=>REAL_DATA}];
  ['snapshot-glorys', 'snapshot-igora'].forEach(sid=>{
    const el = document.getElementById(sid);
    if(!el) return;
    let d = null;
    const data = ()=>d || (d = JSON.parse(el.textContent));
    const words = modelWords(data().meta), id = words.short.toLowerCase();
    // a main snapshot of another case (a forecast) has other times: the second model is left out, as expected there
    if(!sameGridAsMain(data().meta)){ if(!REAL_DATA.meta.case) console.warn(`the ${words.short} snapshot is on another grid or other times; it is left out`); }
    else if(!list.some(x=>x.id===id)) list.push({id, words, atObs:false, decoded:null, data});
  });
  return list;
})();
let realModel = REAL_MODELS[0].id;
// REAL is the model on screen; setRealModel() copies another decoded model's fields into it, so the main model keeps
// a copy of its own (sharing REAL would let a switch overwrite it, and switching back would keep the other model)
const REAL = decodeModel(REAL_DATA);
REAL_MODELS[0].decoded = Object.assign({}, REAL);
// the model on screen: its words (MODEL.short, MODEL.product, ...)
function activeModel(){ return REAL_MODELS.find(x=>x.id===realModel) || REAL_MODELS[0]; }
function MODEL(){ return activeModel().words; }
// the Model switch in the left panel: one button per model (setRealModel is in data-source.js)
function modelButton(id, label, title, onClick){
  const b = document.createElement('button');
  b.type = 'button';
  b.dataset.model = id;
  b.textContent = label;
  b.title = title;
  b.addEventListener('click', onClick);
  ui.modelToggle.appendChild(b);
  return b;
}
REAL_MODELS.forEach(m=>modelButton(m.id, m.words.label, m.words.note, ()=>setRealModel(m.id)));
// A model a data server offers (serve.py /api/v1/datasets/<id>/model, a NetCDF file put on the page's grid and times):
// a button now; its fields load on the first click, then it is a model like the others. id must be new.
function addServerModel(d){
  const id = 'srv-' + d.id, label = String(d.title || d.id).slice(0, 18);
  if(REAL_MODELS.some(m=>m.id===id)) return null;
  let loading = null;
  const b = modelButton(id, label, `${d.title || d.id}, from the data server (loads when chosen)`, ()=>{
    if(REAL_MODELS.some(m=>m.id===id)) return setRealModel(id);
    if(loading) return;
    b.disabled = true; b.textContent = 'Loading…';
    loading = serverJSON(d.links.model).then(j=>{
      if(!j || j.schema!==1 || !sameGridAsMain(j.meta)) throw new Error(`${label} is not on this page's grid and times.`);
      const words = Object.assign(modelWords(j.meta), {label, note:`${d.title || d.id}, from the data server`,
        product:`${label} model output`, products:'model outputs', data:d.title || d.id});
      REAL_MODELS.push({id, words, atObs:false, decoded:null, data:()=>j, server:true});
      setRealModel(id);
    }).catch(err=>{
      ui.modelNote.textContent = err.message;
    }).finally(()=>{ b.disabled = false; b.textContent = label; loading = null; });
  });
  return b;
}
// bilinear in latitude/longitude, linear between model levels; R is a decodeModel() result (realAt: the model on screen)
function realAtIn(R, vi, lat, lon, depth, t){
  const m = R.meta, f = R.f;
  const y = Math.max(0, Math.min(m.nlat-1.000001, (lat-m.lat0)/m.step));
  const x = Math.max(0, Math.min(m.nlon-1.000001, (lon-m.lon0)/m.step));
  const i = Math.floor(y), j = Math.floor(x), fy = y-i, fx = x-j;
  const D = m.depths, z = Math.max(0, Math.min(D[D.length-1], depth));
  let k = 0;
  while(k < D.length-2 && z > D[k+1]) k++;
  const fz = (z-D[k])/(D[k+1]-D[k]);
  const at = kk=>{
    const o = (t*m.vars.length+vi)*R.per + kk*R.layer + i*m.nlon + j;
    return (f[o]*(1-fx) + f[o+1]*fx)*(1-fy) + (f[o+m.nlon]*(1-fx) + f[o+m.nlon+1]*fx)*fy;
  };
  return fz===0 ? at(k) : at(k)*(1-fz) + at(k+1)*fz;
}
function realAt(vi, lat, lon, depth, t){ return realAtIn(REAL, vi, lat, lon, depth, t); }

/* ---- real Argo profiles (REAL_DATA.argo), one set per analysis ----
   Each carries the main model at its own position and time when the snapshot has argo.model (inst.atObs; it is
   inst.model only while that model is on screen). A profile whose
   salinity failed delayed-mode QC (sq 0) keeps its temperature; density then uses model salinity.
   C (chlorophyll, mg/m³) and O (oxygen, µmol/kg) come from BGC-Argo adjusted values; NaN = no sensor or failed QC. */
const realProfiles = (()=>{
  const A = REAL_DATA.argo, nL = A.levels.length, miss = REAL_DATA.meta.missing, Q = REAL_DATA.meta.quant;
  const dec = (b64, key)=>{ const q = b64Int16(b64), [off, sc] = Q[key]; return Array.from(q, v=>v===miss ? NaN : off+v*sc); };
  const T = dec(A.T, 'temp'), S = dec(A.S, 'sal'), C = A.C ? dec(A.C, 'chl') : [], O = A.O && Q.oxy ? dec(A.O, 'oxy') : [];
  const MT = A.model ? dec(A.model.T, 'temp') : null, MS = A.model ? dec(A.model.S, 'sal') : null;
  return A.profiles.map((p,n)=>{
    const cut = arr=>arr.length ? arr.slice(n*nL, (n+1)*nL) : new Array(nL).fill(NaN);
    const Tn = cut(T), Sn = cut(S), Cn = cut(C), On = cut(O), salOk = p.sq!==0;
    const ok = Tn.map((v,k)=>Number.isFinite(v) && (Number.isFinite(Sn[k]) || !salOk));
    const first = ok.indexOf(true), last = ok.lastIndexOf(true);
    const inst = {id:'ARGO-'+p.w, kind:'argo', real:true, lat:p.la, lon:p.lo, snap:p.s, time:p.t, mode:p.m,
      cycle:p.c, levels:A.levels, maxDepth:A.levels[last], bgc:Cn.some(Number.isFinite), hasOxy:On.some(Number.isFinite), salOk};
    if(MT) inst.model = inst.atObs = {T:cut(MT), S:cut(MS), time:A.model.times[n]};   // the main model at the float
    const D = Tn.map((v,k)=>ok[k] && Number.isFinite(Sn[k]) ? sigmaT(Sn[k], v) : NaN);
    const missing = new Uint8Array(nL);
    for(let k=first;k<=last;k++) if(!ok[k]) missing[k] = 1;
    inst.obs = {z:A.levels, T:Tn.map((v,k)=>ok[k]?v:NaN), S:Sn.map((v,k)=>ok[k]?v:NaN), C:Cn, O:On, D,
      flag:inversionFlags(D), missing};
    inst.userData = {type:'argo', data:{id:inst.id, cycle:p.c, inst}};
    return inst;
  });
})();

// how far a real profile is from the model analysis it is compared with (up to 1.5 days)
function analysisGap(inst){
  const h = Math.round((Date.parse(REAL.meta.times[inst.snap]) - Date.parse(inst.time))/3.6e6);
  return h===0 ? 'same time as the analysis' : `${Math.abs(h)} h ${h>0 ? 'before' : 'after'} the analysis`;
}
// hours from the matched model output to the profile (obs − model); null without argo.model
function modelGapHours(inst){
  return inst.model ? (Date.parse(inst.time) - Date.parse(inst.model.time))/3.6e6 : null;
}

// Amphan's fixes over the sea inside the map, ending at the coast crossing interpolated between
// the last fix at sea and the first over land (IBTrACS is six-hourly; landfall fell in between).
// usa = JTWC 1-minute wind (kt), nd = IMD 3-minute wind (kt), grade = IMD grade (D … SuCS).
const REAL_TRACK = (()=>{
  const all = (REAL_DATA.track && REAL_DATA.track.fixes || []).map(([t, lat, lon, usa, nd, grade])=>({t, lat, lon, usa, nd, grade}));
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
})();
REAL_TRACK.forEach(f=>{ f.h = (Date.parse(f.t)-Date.parse(REAL_TRACK[0].t))/3.6e6; });
