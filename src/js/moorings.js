/* ============================================================
   RAMA MOORINGS (real data): moored buoys in the Bay of Bengal and the Arabian Sea during Amphan
   #snapshot-moorings (data-pipeline/build_moorings.py): NOAA PMEL GTMBA daily means at 12:00 UTC, temperature and
   salinity at fixed depths and currents near the surface. Each mooring on each analysis day is one profile, through
   the mooring plugin's own reader, so it draws, opens and compares with the model like an imported mooring; it is
   built-in real data, so it joins the real source's instruments (data-source.js) beside the Argo floats rather than
   the Your data list. mooringFacts() computes what the docs quote; facts_reference.py mooring_facts recomputes it.
   ============================================================ */
const MOORINGS = (()=>{
  const el = document.getElementById('snapshot-moorings');
  if(!el) return null;
  let d;
  try { d = JSON.parse(el.textContent); } catch(e){ console.warn('the mooring data do not parse; they are left out'); return null; }
  if(!d || d.schema!==1 || !Array.isArray(d.moorings)){ console.warn('the mooring data have another schema; they are left out'); return null; }
  return d;
})();
// "12n90e" -> "12°N 90°E"
function stationName(st){
  const m = /^(\d+(?:\.\d+)?)([ns])(\d+(?:\.\d+)?)([ew])$/i.exec(st);
  return m ? `${m[1]}°${m[2].toUpperCase()} ${m[3]}°${m[4].toUpperCase()}` : st;
}
// one profile per mooring and analysis day: the union of the sensor depths, NaN where a sensor has no value
const MOORING_INSTS = (()=>{
  if(!MOORINGS) return [];
  const ds = {id:'rama', name:'RAMA mooring (NOAA PMEL)', type:'mooring', source:'builtin'}, out = [];
  MOORINGS.moorings.forEach(m=>{
    const depth = m.depth, z = [...new Set([...depth.temp, ...depth.sal, ...depth.cur])].sort((a, b)=>a-b);
    const at = (arr, ds2, k)=>{ const i = ds2.indexOf(z[k]); return i < 0 || arr[i]===null ? NaN : arr[i]; };
    META.times.forEach((iso, t)=>{
      const day = m.days.indexOf(iso.slice(0, 10));
      if(day < 0) return;
      const cur = z.map((_, k)=>{ const u = at(m.u[day], depth.cur, k), v = at(m.v[day], depth.cur, k); return Math.hypot(u, v); });
      const raw = {id:`RAMA ${stationName(m.station)}`, type:'mooring', lat:m.lat, lon:m.lon, time:`${iso.slice(0, 10)}T12:00Z`, z,
        vars:{temp:z.map((_, k)=>at(m.temp[day], depth.temp, k)), sal:z.map((_, k)=>at(m.sal[day], depth.sal, k)), cur},
        meta:{site:stationName(m.station), station:m.station}};
      const inst = datasetInstrument(raw, ds, out.length);
      if(typeof inst !== 'string'){ inst.builtIn = true; out.push(inst); }
    });
  });
  return out;
})();
// the moorings at analysis t (real source, data-source.js)
function mooringsAt(t){ return MOORING_INSTS.filter(i=>i.snap===t); }

/* Per mooring: the analyses it has, temperature bias and RMSE against the model on screen over all of them (observed −
   model, as the profile panel), and its shallowest temperature from the last analysis before Amphan passed (13 May) to
   the first after (19 May), observed and in the model (in-situ, as measured). */
function mooringFacts(){
  if(!MOORING_INSTS.length) return null;
  const before = META.times.findIndex(x=>x.startsWith('2020-05-13')), after = META.times.findIndex(x=>x.startsWith('2020-05-19'));
  return MOORINGS.moorings.map(m=>{
    const insts = MOORING_INSTS.filter(i=>i.meta.station===m.station);
    const s = summarize(insts.flatMap(i=>misfit(i, i.snap, 'temp')));
    const top = t=>{
      const i = insts.find(x=>x.snap===t);
      if(!i) return null;
      const k = i.obs.T.findIndex(Number.isFinite);
      return k < 0 ? null : {z:i.levels[k], v:i.obs.T[k]};
    };
    const a = before >= 0 ? top(before) : null, b = after >= 0 ? top(after) : null;
    const change = a && b && a.z===b.z ? {depth:a.z, observed:b.v - a.v,
      model:modelAsMeasured('temp', m.lat, m.lon, a.z, after) - modelAsMeasured('temp', m.lat, m.lon, a.z, before)} : null;
    return {station:m.station, analyses:insts.length, tempBias:s ? s.bias : null, tempRmse:s ? s.rmse : null, n:s ? s.n : 0, change};
  });
}
