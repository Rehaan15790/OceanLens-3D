/* ============================================================
   SATELLITE PRODUCTS (real data): a fishing-zone indicator and climate monitoring
   #snapshot-products (data-pipeline/build_products.py) holds, on the app grid and the 8 analysis days: OSTIA
   sea-surface temperature and its front strength, the 1991–2020 normal of the same days, Copernicus-GlobColour
   chlorophyll, and each year's mean SST 1982–2025 in a Bay of Bengal and an Arabian Sea box.
   They are registered through the plugin API as 2D products, shown in real-data mode only:
   - Satellite chlorophyll, and SST fronts (the strongest tenth of the 0.05° gradients in each 0.5° cell).
   - Fishing-zone indicator: how many of two signs fish gather by a cell shows on the day, an SST front and water
     rich in chlorophyll. The idea behind INCOIS's potential fishing zone advisories, simplified; not an advisory.
   - SST anomaly: the day against its 1991–2020 normal. The panel adds the yearly series and its trend.
   productFacts() computes every number shown; data-pipeline/facts_reference.py product_facts recomputes them.
   ============================================================ */
const FRONT_MIN = 0.03;      // °C per km: a front on the 0.05° OSTIA field
const CHL_MIN = 0.3;         // mg/m³: water richer in plankton than the open Bay and Arabian Sea in May
const PRODUCTS = (()=>{
  const el = document.getElementById('snapshot-products');
  if(!el) return null;
  let d;
  try { d = JSON.parse(el.textContent); } catch(e){ console.warn('the satellite products do not parse; they are left out'); return null; }
  const m = d && d.meta;
  if(!m || d.schema!==1 || !['lat0', 'lon0', 'step', 'nlat', 'nlon'].every(k=>m[k]===META[k]) || m.times.join()!==META.times.join()){
    console.warn('the satellite products are on another grid or other days; they are left out');
    return null;
  }
  const dec = key=>{
    const q = b64Int16(d[key]), [off, sc] = m.quant[key], f = new Float32Array(q.length);
    for(let n=0;n<q.length;n++) f[n] = q[n]===m.missing ? NaN : off + q[n]*sc;
    return f;
  };
  return {meta:m, sst:dec('sst'), clim:dec('clim'), chl:dec('chl'), front:dec('front'), series:d.series, layer:m.nlat*m.nlon};
})();
// a product's value at analysis t and map point n (gridPts order), null where it has none
function productAt(key, t, n){
  const v = PRODUCTS[key][t*PRODUCTS.layer + n];
  return Number.isFinite(v) ? v : null;
}
function sstAnomalyAt(t, n){
  const a = productAt('sst', t, n), b = productAt('clim', t, n);
  return a===null || b===null ? null : a - b;
}
// 0, 1 or 2: an SST front, and water rich in chlorophyll; null where either is unknown
function fishingScore(t, n){
  const f = productAt('front', t, n), c = productAt('chl', t, n);
  return f===null || c===null ? null : (f >= FRONT_MIN ? 1 : 0) + (c >= CHL_MIN ? 1 : 0);
}
// map points inside a box {lat:[s, n], lon:[w, e]} (the pipeline's boxes, edges included)
function boxPoints(box){ return gridPts.map((p, n)=>n).filter(n=>{ const p = gridPts[n];
  return p.lat >= box.lat[0]-1e-9 && p.lat <= box.lat[1]+1e-9 && p.lon >= box.lon[0]-1e-9 && p.lon <= box.lon[1]+1e-9; }); }
// least-squares slope of y on x
function slope(x, y){
  const mx = mean(x), my = mean(y);
  let sxy = 0, sxx = 0;
  x.forEach((v, i)=>{ sxy += (v-mx)*(y[i]-my); sxx += (v-mx)*(v-mx); });
  return sxy/sxx;
}
/* The numbers the panel and captions quote: per analysis, the cells showing both fishing signs and the mean anomaly
   in each box; for the series, the trend (°C per decade), the 1991–2020 mean and the case year against it. */
function productFacts(){
  if(!PRODUCTS) return null;
  const S = PRODUCTS.series, boxes = S.boxes, NT = META.times.length;
  const out = {fishing:[], anomaly:[], series:{}};
  for(let t=0;t<NT;t++){
    let both = 0, known = 0;
    gridPts.forEach((p, n)=>{ const s = gridLand[n] ? null : fishingScore(t, n); if(s!==null){ known++; if(s===2) both++; } });   // as the map
    out.fishing.push({both, known});
    const row = {};
    Object.entries(boxes).forEach(([name, box])=>{
      const a = boxPoints(box).map(n=>sstAnomalyAt(t, n)).filter(v=>v!==null);
      row[name] = a.length ? mean(a) : null;
    });
    out.anomaly.push(row);
  }
  Object.keys(boxes).forEach(name=>{
    const y = S[name], yrs = S.years, normal = mean(y.filter((_, i)=>yrs[i] >= 1991 && yrs[i] <= 2020));
    const i20 = yrs.indexOf(2020), rank = y.filter(v=>v > y[i20]).length + 1;
    out.series[name] = {trendPerDecade:10*slope(yrs, y), normal, case:y[i20], caseAnomaly:y[i20]-normal, caseRank:rank, years:yrs.length};
  });
  return out;
}

if(PRODUCTS){
  const grid = fn=>t=>gridPts.map((p, n)=>dataMode!=='real' || gridLand[n] ? null : fn(t, n));
  registerProduct('schl', {label:'Satellite chlorophyll', unit:'mg/m³', palette:PALETTES.chlorophyll ? 'chlorophyll' : 'speed',
    scale:'log', range:[0.05, 5], modes:['real'], grid:grid((t, n)=>productAt('chl', t, n)),
    note:'Copernicus-GlobColour gap-free satellite chlorophyll at the surface (clouds filled by interpolation), each cell the geometric mean of its 4 km pixels.'});
  registerProduct('front', {label:'SST fronts', unit:'°C/10 km', palette:'heat', range:[0, 0.8], modes:['real'],
    grid:grid((t, n)=>{ const v = productAt('front', t, n); return v===null ? null : 10*v; }),
    note:'How fast the satellite sea-surface temperature (OSTIA, 0.05°) changes across a front: the strongest tenth in each cell.'});
  registerProduct('pfz', {label:'Fishing-zone indicator', unit:'signs', palette:'heat', range:[0, 2], ticks:[0, 1, 2], modes:['real'],
    grid:grid(fishingScore),
    note:`Fish gather where warm and cold water meet in water rich in plankton. 2 = an SST front (at least ${fmt.dp(10*FRONT_MIN, 1)} °C/10 km) `+
      `and chlorophyll of at least ${CHL_MIN} mg/m³; 1 = one of them. A teaching version of the idea, not a potential fishing zone advisory (INCOIS issues those).`});
  registerProduct('ssta', {label:'SST anomaly', unit:'°C', palette:'temperature', range:[-2, 2], modes:['real'],
    grid:grid(sstAnomalyAt),
    note:'Satellite sea-surface temperature (OSTIA) against the 1991–2020 mean of the same day: dark is cooler than normal, bright warmer.'});
}
// true when variable key can be shown in data mode `mode` (satellite products: real data only)
function varInMode(key, mode = dataMode){ const v = VAR_META[key]; return !!v && (!v.modes || v.modes.includes(mode)); }

/* ---- the right panel's card for a satellite product: what it shows today, and the climate series ---- */
// an element with text; parts are strings (text) or [string] (bold), so nothing from the data is ever read as HTML
function el$(tag, cls, parts){
  const e = document.createElement(tag);
  if(cls) e.className = cls;
  (parts || []).forEach(x=>{
    if(Array.isArray(x)){ const b = document.createElement('b'); b.textContent = x.join(''); e.appendChild(b); }
    else e.appendChild(document.createTextNode(String(x)));
  });
  return e;
}
function renderProductCard(){
  const el = document.getElementById('productCard');
  if(!el) return;
  const key = state.variable, on = !!PRODUCTS && dataMode==='real' && ['schl', 'front', 'pfz', 'ssta'].includes(key);
  el.hidden = !on;
  if(!on){ el.replaceChildren(); return; }
  const F = facts().real.products, t = state.time, day = timeLabel(t);
  if(key==='ssta'){
    const a = F.anomaly[t], bay = F.series.bay, ar = F.series.arabian, sg = v=>fmt.signed(v, 2);
    const chart = el$('canvas', 'series-chart');
    chart.id = 'seriesChart';
    chart.setAttribute('role', 'img');
    chart.setAttribute('aria-label', `Mean sea-surface temperature on the 8 analysis days of May, each year from ${Number(PRODUCTS.series.years[0])}`);
    el.replaceChildren(
      el$('h3', 'grp-label', ['Climate: sea-surface temperature']),
      el$('p', 'meta', [`${day}: the Bay of Bengal box is `, [`${sg(a.bay)} °C`], ' and the Arabian Sea box ',
        [`${sg(a.arabian)} °C`], ' against the 1991–2020 normal.']),
      chart,
      el$('p', 'meta', ['The same 8 days of May, every year since 1982. Bay of Bengal: ', [`${sg(bay.trendPerDecade)} °C per decade`],
        `; 2020 was ${sg(bay.caseAnomaly)} °C against its normal, ${bay.caseRank===1 ? 'the warmest' : `the ${ordinal(bay.caseRank)} warmest`}`+
        ` of ${bay.years} years. Arabian Sea: ${sg(ar.trendPerDecade)} °C per decade.`]));
    drawSeriesChart(chart);
  } else {
    const f = F.fishing[t];
    el.replaceChildren(
      el$('h3', 'grp-label', ['Fishing-zone signs']),
      el$('p', 'meta', [`${day}: `, [String(f.both)], ` of ${f.known} sea cells (0.5°) show both an SST front and chlorophyll-rich water.`]),
      el$('p', 'hint', ['Satellite data of the day (gap-free, so clouds are filled in). Not an advisory: INCOIS issues potential '+
        'fishing zone advisories from its own analysis.']));
  }
}
const ordinal = n=>n + (n%100 >= 11 && n%100 <= 13 ? 'th' : ['th', 'st', 'nd', 'rd'][n%10] || 'th');
// the yearly series of both boxes, 2020 marked
function drawSeriesChart(c){
  if(!c) return;
  const th = theme(), {ctx, w, h} = hidpiCanvas(c, 170), S = PRODUCTS.series, yrs = S.years;
  const pad = {l:38, r:10, t:10, b:24}, pw = w-pad.l-pad.r, ph = h-pad.t-pad.b;
  const all = [...S.bay, ...S.arabian], lo = Math.floor(Math.min(...all)*2)/2, hi = Math.ceil(Math.max(...all)*2)/2;
  const X = y=>pad.l + (y-yrs[0])/(yrs[yrs.length-1]-yrs[0])*pw, Y = v=>pad.t + (hi-v)/(hi-lo)*ph;
  ctx.font = CHART_FONT; ctx.lineWidth = 1;
  niceTicks(lo, hi, 4).forEach(v=>{
    ctx.strokeStyle = th.grid; ctx.beginPath(); ctx.moveTo(pad.l, Y(v)); ctx.lineTo(w-pad.r, Y(v)); ctx.stroke();
    ctx.fillStyle = th.tick; ctx.textAlign = 'right'; ctx.fillText(v.toFixed(1), pad.l-4, Y(v)+4);
  });
  ctx.textAlign = 'center';
  [1990, 2000, 2010, 2020].forEach(y=>{ ctx.fillStyle = th.tick; ctx.fillText(String(y), X(y), h-6); });
  ctx.fillText('°C', pad.l-20, pad.t+4);
  const line = (vals, color, dash)=>{
    ctx.strokeStyle = color; ctx.lineWidth = 1.6; ctx.setLineDash(dash || []);
    ctx.beginPath(); vals.forEach((v, i)=>{ const x = X(yrs[i]), y = Y(v); if(i) ctx.lineTo(x, y); else ctx.moveTo(x, y); }); ctx.stroke();
    ctx.setLineDash([]);
  };
  line(S.bay, th.obs); line(S.arabian, th.model, [5, 3]);
  const i20 = yrs.indexOf(2020);
  [[S.bay, th.obs], [S.arabian, th.model]].forEach(([v, col])=>{
    ctx.beginPath(); ctx.arc(X(2020), Y(v[i20]), 3.5, 0, 7); ctx.fillStyle = col; ctx.fill();
  });
  ctx.fillStyle = th.text2; ctx.textAlign = 'left';
  ctx.fillText('Bay of Bengal (solid)', pad.l+4, pad.t+10); ctx.fillText('Arabian Sea (dashed)', pad.l+4, pad.t+24);
}
