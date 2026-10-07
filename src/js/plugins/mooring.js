/* Example plugin: moored buoys (for example the RAMA and OMNI moorings in the Bay of Bengal).
   Types only; data comes from an import or the server. A mooring samples fixed depths over time,
   so each time step of a mooring is one profile; the reader drops the levels the mooring has no
   sensor at and keeps the buoy's nominal position. */
OceanLens.registerInstrument('mooring', {
  label:'Mooring',
  marker:'square',
  variables:['temp', 'sal', 'oxy', 'chl'],
  stem:true,
  kindLabel:inst=>inst.meta && inst.meta.site ? `Mooring ${inst.meta.site}` : 'Mooring',
  readProfile(p){
    const z = p.z || p.depth || p.pres, vars = p.vars || p;
    const list = a=>Array.isArray(a) || ArrayBuffer.isView(a);      // not {length: 3e8}, which Array.from would allocate
    if(!list(z)) return p;
    const keep = Array.from(z, (_, k)=>['temp', 'sal', 'oxy', 'chl'].some(v=>vars[v] && Number.isFinite(+(vars[v][k] ?? NaN))));
    const pick = a=>list(a) && Array.from(a).filter((_, k)=>keep[k]);
    const out = Object.assign({}, p, {vars:{}});
    ['temp', 'sal', 'oxy', 'chl'].forEach(v=>{ if(vars[v]) out.vars[v] = pick(vars[v]); });
    if(p.z) out.z = pick(p.z); else if(p.depth) out.depth = pick(p.depth); else out.pres = pick(p.pres);
    return out;
  }
});
