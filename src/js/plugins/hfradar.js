/* Example plugin: HF radar surface currents as a product, drawn like a variable. Data comes from an
   import or the server: one record per radar cell and time (lat, lon, time, u, v in m/s, no depth).
   The cells are observations on their own grid, not profiles, so they get no markers; the product
   maps the radar time nearest the analysis onto the model grid (inverse-distance within one grid step). */
(()=>{
  const KEY = 'hfr';
  OceanLens.registerInstrument('hfradar', {
    label:'HF radar', marker:'cross', variables:['cur'], gridded:true, stem:false,
    readProfile(p){
      const src = p.vars || p, num = x=>x==null || x==='' ? NaN : +x;
      const at = a=>Array.isArray(a) || ArrayBuffer.isView(a) ? a[0] : a;
      const speed = src.cur!==undefined ? num(at(src.cur)) : Math.hypot(num(at(src.u)), num(at(src.v)));
      return Object.assign({}, p, {z:[0], depth:undefined, pres:undefined, vars:{cur:[speed]}});
    }
  });
  // the cells of the radar time nearest analysis t (within 1.5 days, or any with "show all")
  function cellsAt(t){
    const all = OceanLens.observations('hfradar'), at = Date.parse(META.times[t]);
    let best = null, gap = Infinity;
    all.forEach(c=>{ const d = Math.abs(c.ms - at); if(d < gap){ gap = d; best = c.ms; } });
    if(best===null || (!state.showAllImported && gap > 1.5*864e5)) return [];
    return all.filter(c=>c.ms===best && Number.isFinite(c.obs.vars.cur[0]));
  }
  const spec = {
    label:'HF radar surface current', unit:'m/s', palette:'speed', range:[0, 1], listed:false,
    grid(t){
      const out = new Array(gridPts.length).fill(null), bins = new Map(), step = META.step;
      const node = (lat, lon)=>[Math.round((lat-LAT0)/step), Math.round((lon-LON0)/step)];
      cellsAt(t).forEach(c=>{
        const k = node(c.lat, c.lon).join();
        if(!bins.has(k)) bins.set(k, []);
        bins.get(k).push(c);
      });
      if(!bins.size) return out;
      gridPts.forEach((p, n)=>{
        if(gridLand[n]) return;
        const cos = Math.cos(p.lat*Math.PI/180);
        let sw = 0, sv = 0;
        for(let di=-1; di<=1; di++) for(let dj=-1; dj<=1; dj++){
          (bins.get(`${p.i+di},${p.j+dj}`) || []).forEach(c=>{
            const d = Math.hypot(c.lat-p.lat, (c.lon-p.lon)*cos);
            if(d > step) return;
            const w = 1/Math.pow(d + 0.05*step, 2);
            sw += w; sv += w*c.obs.vars.cur[0];
          });
        }
        if(sw) out[n] = sv/sw;
      });
      return out;
    },
    sample(lat, lon, z, t){
      const i = Math.round((lat-LAT0)/META.step), j = Math.round((lon-LON0)/META.step);
      const g = i>=0 && i<LAT_N && j>=0 && j<LON_N ? field2dGrid(KEY, t)[i*LON_N + j] : null;
      return g===null ? NaN : g;
    }
  };
  OceanLens.registerProduct(KEY, spec);
  // the variable pill appears once radar data is loaded
  OceanLens.on('dataset', ()=>{
    if(!OceanLens.variable(KEY).listed && OceanLens.observations('hfradar').length){
      OceanLens.registerProduct(KEY, Object.assign({}, spec, {listed:true}));
    }
  });
})();
