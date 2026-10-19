/* Plugin: satellite sea-surface temperature and SST fronts (real data), registered as two map products.
   The data are NOAA OISST v2.1 (AVHRR only) daily SST at 0.25°, extracted as NetCDF from the INCOIS Live Access Server
   (data/amphan-2020-extras.json, sat.sst; data-pipeline/build_extras.py), on the 8 analysis days.
   - Satellite SST: each 0.5° map point is the mean of the 0.25° cells around it (up to four).
   - SST fronts: a simplified Belkin and O'Reilly (2009) approach on the 0.25° field: one 3 × 3 median pass (they iterate a
     contextual median filter until it converges), then the Sobel gradient (°C per 100 km), at sea cells whose whole 3 × 3 neighbourhood is sea. Each map point shows the strongest
     front among its cells. Fronts are where fish gather; INCOIS's fishing-zone advisories pair them with satellite
     chlorophyll, which the LAS has only up to 1 May 2020, so this shows the temperature half only.
   Both need the extras; without them, and in the synthetic demo, they draw nothing and the key says why. */
(()=>{
  let decoded;
  // {g, days: [Float32Array per analysis, NaN on land]} or null
  function sat(){
    if(decoded!==undefined) return decoded;
    const e = typeof EXTRAS === 'function' ? EXTRAS() : null, s = e && e.sat && e.sat.sst;
    decoded = null;
    if(s && s.times.join()===META.times.join()){
      const g = s.grid, q = b64Int16(s.data), [off, sc] = s.quant, n = g.nlat*g.nlon, miss = e.meta.missing;
      if(q.length===s.times.length*n){
        decoded = {g, days:s.times.map((_, t)=>{
          const f = new Float32Array(n);
          for(let k=0;k<n;k++){ const v = q[t*n + k]; f[k] = v===miss ? NaN : off + v*sc; }
          return f;
        })};
      }
    }
    return decoded;
  }
  // the 0.25° cells within a quarter degree of a map point
  function cellsOf(S, lat, lon){
    const g = S.g, out = [];
    const i0 = Math.floor((lat - 0.25 - g.lat0)/g.step) + 1, j0 = Math.floor((lon - 0.25 - g.lon0)/g.step) + 1;
    for(let i=i0;i<i0+2;i++) for(let j=j0;j<j0+2;j++) if(i>=0 && i<g.nlat && j>=0 && j<g.nlon) out.push(i*g.nlon + j);
    return out;
  }
  // one value per map point (null on land or without data) from a 0.25° field and a reducer
  function toMap(field, reduce){
    const S = sat();
    return gridPts.map((p, n)=>{
      if(gridLand[n] || !S) return null;
      const v = cellsOf(S, p.lat, p.lon).map(k=>field[k]).filter(Number.isFinite);
      return v.length ? reduce(v) : null;
    });
  }
  const fronts = new Map();
  // |∇SST| in °C per 100 km on the 0.25° grid after a 3 × 3 median filter (NaN where any neighbour is land)
  function frontField(t){
    if(fronts.has(t)) return fronts.get(t);
    const S = sat(), g = S.g, T = S.days[t], nx = g.nlon, ny = g.nlat, M = new Float32Array(T.length).fill(NaN);
    const at = (a, i, j)=>a[i*nx + j];
    for(let i=1;i<ny-1;i++) for(let j=1;j<nx-1;j++){
      const w = [];
      for(let di=-1;di<=1;di++) for(let dj=-1;dj<=1;dj++) w.push(at(T, i+di, j+dj));
      if(w.every(Number.isFinite)){ w.sort((a, b)=>a-b); M[i*nx + j] = w[4]; }
    }
    const G = new Float32Array(T.length).fill(NaN), km = 111.32*g.step;
    for(let i=1;i<ny-1;i++) for(let j=1;j<nx-1;j++){
      const v = (di, dj)=>at(M, i+di, j+dj);
      const gx = (v(-1, 1) + 2*v(0, 1) + v(1, 1) - v(-1, -1) - 2*v(0, -1) - v(1, -1))/8;   // °C per cell, eastward
      const gy = (v(1, -1) + 2*v(1, 0) + v(1, 1) - v(-1, -1) - 2*v(-1, 0) - v(-1, 1))/8;   // northward (rows go north)
      const lat = g.lat0 + i*g.step, dx = km*Math.cos(lat*Math.PI/180), dy = km;
      const mag = Math.hypot(gx/dx, gy/dy)*100;
      if(Number.isFinite(mag)) G[i*nx + j] = mag;
    }
    fronts.set(t, G);
    return G;
  }
  const none = ()=>new Array(gridPts.length).fill(null);
  const note = what=>()=>{
    if(dataMode!=='real') return `${what} exists for the real Amphan case only.`;
    if(!sat()) return `${what} needs the satellite data (data/amphan-2020-extras.json), which this page does not hold.`;
    return '';
  };
  OceanLens.registerProduct('sstsat', {
    label:'Satellite SST', unit:'°C', palette:'temperature', range:[26, 33], model:{synth:false},
    about:'Sea-surface temperature measured from space: NOAA OISST v2.1, a daily blend of AVHRR satellite and in-situ '+
          'SST on a 0.25° grid, extracted from the INCOIS Live Access Server. Compare it with the models’ Temperature at 0 m.',
    grid:t=>dataMode==='real' && sat() ? toMap(sat().days[t], v=>v.reduce((a, b)=>a + b, 0)/v.length) : none(),
    keyNote:()=>note('Satellite SST')() || 'NOAA OISST v2.1 daily mean, from the INCOIS Live Access Server.'
  });
  OceanLens.registerProduct('front', {
    label:'SST fronts', unit:'°C/100 km', palette:'heat', range:[0, 1.5], model:{synth:false},
    about:'How fast satellite SST changes across the sea surface: bright bands are fronts, where warm and cool water meet and '+
          'fish often gather. A simplified Belkin and O’Reilly (2009) method: one 3 × 3 median pass, then the Sobel gradient. '+
          'INCOIS fishing-zone advisories pair fronts with satellite chlorophyll, which the LAS does not hold for May 2020.',
    grid:t=>dataMode==='real' && sat() ? toMap(frontField(t), v=>Math.max(...v)) : none(),
    keyNote:()=>note('The SST front map')() ||
      'Strength of satellite SST fronts (a simplified Belkin and O’Reilly 2009 method on NOAA OISST from the INCOIS LAS). '+
      'Fishing-zone advisories pair fronts with chlorophyll, which is not available for May 2020.'
  });
})();
