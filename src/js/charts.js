/* ---- 2D drawing for the panels and the viewport key ----
   Chart colours come from the CSS tokens (tokens.css), read once; data colours come only from the palettes. */
const CHART_FONT = '11px system-ui, "Segoe UI", Roboto, "Noto Sans", Arial, sans-serif';
const CHART_FONT_BOLD = '600 ' + CHART_FONT;
let chartTheme = null;
function theme(){
  if(chartTheme) return chartTheme;
  const cs = getComputedStyle(document.documentElement), v = n=>cs.getPropertyValue(n).trim();
  chartTheme = {text:v('--text'), text2:v('--text-2'), tick:v('--chart-tick'), grid:v('--chart-grid'), obs:v('--chart-obs'),
    model:v('--chart-model'), mark:v('--chart-mark'), flag:v('--flag'), accent:v('--accent'), panel:v('--panel-2'), bg:v('--bg')};
  return chartTheme;
}
// readout formats (until fmt.js has them): temperature 1 dp, salinity and speed 2 dp
const VALUE_DP = {temp:1, sal:2, cur:2, dens:2, chl:2, oxy:0};
const PROFILE_UNITS = {temp:'°C', sal:'PSU', chl:'mg/m³', oxy:'µmol/kg', dens:'kg/m³'};
const PROFILE_NAMES = {temp:'temperature', sal:'salinity', chl:'chlorophyll', oxy:'oxygen', dens:'density'};
const escapeHtml = s=>String(s).replace(/[&<>"']/g, c=>({'&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;'})[c]);
// an instrument type's label from the plugin registry, for imported and plugin data
function instrumentLabel(kind){
  const reg = window.OceanLens && OceanLens.instrument ? OceanLens.instrument(kind) : null;
  return reg && reg.label ? reg.label : kind ? kind.charAt(0).toUpperCase()+kind.slice(1) : 'Observed';
}
const ll2 = (lat, lon)=>`${lat.toFixed(2)}°N, ${lon.toFixed(2)}°E`;
const signed = (v, dp)=>(v >= 0 ? '+' : '−') + Math.abs(v).toFixed(dp);

// a canvas sized in CSS pixels, drawn at device resolution so text stays sharp
// value -> ramp position, linear or log10 as the GPU does it (graphics' Render.rampPos)
function scaleNow(){ return state.scale==='log' ? 'log' : 'linear'; }
function rampT(v, min, max, scale=scaleNow()){
  if(Render.rampPos) return Render.rampPos(v, min, max, scale);
  return Math.max(0, Math.min(1, (v-min)/(max-min)));
}
// round ticks for a log axis: 1, 2, 5 per decade, thinned to about `target`
function logTicks(min, max, target=5){
  const lo = min > 0 ? min : max*1e-3, out = [];
  for(let e=Math.floor(Math.log10(lo)); e<=Math.ceil(Math.log10(max)); e++)
    [1, 2, 5].forEach(m=>{ const v = +(m*Math.pow(10, e)).toPrecision(1); if(v >= lo*0.999 && v <= max*1.001) out.push(v); });
  if(out.length <= target) return out;
  const decades = out.filter(v=>{ const l = Math.log10(v); return Math.abs(l-Math.round(l)) < 1e-9; });
  return decades.length >= 2 ? decades : out.filter((v,i)=>i%2===0);
}
const logTickText = v=>v >= 1 ? String(Math.round(v)) : String(+v.toPrecision(1));
function hidpiCanvas(c, cssH){
  const dpr = Math.min(2, window.devicePixelRatio || 1);
  const w = c.clientWidth || 272;
  c.width = Math.round(w*dpr); c.height = Math.round(cssH*dpr); c.style.height = cssH+'px';
  const ctx = c.getContext('2d');
  ctx.setTransform(dpr,0,0,dpr,0,0);
  ctx.clearRect(0,0,w,cssH);
  return {ctx, w, h:cssH};
}

// "nice" tick values (1, 2, 2.5, 5 × 10^n), about `target` of them across [min, max]
function niceTicks(min, max, target=5){
  const span = max - min;
  if(!(span > 0)) return [min];
  const raw = span/(target-1), p = Math.pow(10, Math.floor(Math.log10(raw)));
  const step = [1, 2, 2.5, 5, 10].map(m=>m*p).find(s=>span/s <= target) || 10*p;
  const out = [], eps = step*1e-6;
  for(let v = Math.ceil((min-eps)/step)*step; v <= max+eps; v += step) out.push(+v.toFixed(10));
  return out;
}
const tickText = (v, step)=>v.toFixed(step >= 1 ? 0 : step >= 0.1 ? 1 : 2).replace('-', '−');

// markers on the 2D charts look like the ones in 3D: achromatic, with a dark outline
function drawFloatGlyph(ctx, x, y, r, glider){
  ctx.beginPath();
  if(glider) ctx.arc(x, y, r*0.8, 0, 7);
  else { ctx.moveTo(x, y-r); ctx.lineTo(x+r, y); ctx.lineTo(x, y+r); ctx.lineTo(x-r, y); ctx.closePath(); }
  ctx.fillStyle = '#f4f4f4'; ctx.fill(); ctx.lineWidth = 1.5; ctx.strokeStyle = '#0b0f13'; ctx.stroke();
}

/* ---- the distance–depth plot in the right panel ---- */
let sectionPlot = null;   // what the chart last drew, for the hover readout
function drawSectionPanel(){
  if(!section) return;
  const th = theme(), spec = sectionSpec(), km = haversineKm(section.a, section.b);
  const c = document.getElementById('sectionChart');
  const {ctx, w, h} = hidpiCanvas(c, 300);
  const pad = {l:46, r:12, t:20, b:58};
  const pw = w-pad.l-pad.r, ph = h-pad.t-pad.b, maxD = DEPTHS[DEPTHS.length-1];
  const cols = sectionColumns(Math.max(2, Math.round(pw)));   // one column per CSS pixel
  const yOf = z => pad.t + Math.sqrt(z/maxD)*ph;              // same stretched depth axis as the profile chart
  const xOf = i => pad.l + (i+0.5)*pw/cols.length;

  // colour raster at device resolution
  const dpr = c.width/w, iw = Math.round(pw*dpr), ih = Math.round(ph*dpr);
  const img = ctx.createImageData(iw, ih);
  for(let px=0; px<iw; px++){
    const col = cols[Math.min(cols.length-1, Math.floor(px/dpr))];
    for(let py=0; py<ih; py++){
      const z = maxD*Math.pow((py+0.5)/ih, 2);
      const rgb = col.land ? [0.34,0.30,0.21] : z > col.seabed ? [0.055,0.075,0.094]
        : paletteColor(spec.palette, rampT(col.at(z), spec.min, spec.max, spec.scale || scaleNow()));
      const o = (py*iw+px)*4;
      img.data[o] = rgb[0]*255; img.data[o+1] = rgb[1]*255; img.data[o+2] = rgb[2]*255; img.data[o+3] = 255;
    }
  }
  ctx.putImageData(img, Math.round(pad.l*dpr), Math.round(pad.t*dpr));   // device pixels: ignores the transform

  ctx.font = CHART_FONT;
  ctx.textAlign = 'right';
  [0,50,100,200,500,1000].forEach(z=>{
    const y = yOf(z);
    ctx.strokeStyle = 'rgba(255,255,255,.10)'; ctx.lineWidth = 1;
    ctx.beginPath(); ctx.moveTo(pad.l, y); ctx.lineTo(pad.l+pw, y); ctx.stroke();
    ctx.fillStyle = th.tick; ctx.fillText(z+' m', pad.l-5, y+4);
  });
  // sea floor where it is shallower than the model bottom
  ctx.strokeStyle = '#dfcd94'; ctx.lineWidth = 1.2;
  ctx.beginPath();
  let pen = false;
  cols.forEach((col,i)=>{
    if(col.land || col.seabed >= maxD){ pen = false; return; }
    const x = xOf(i), y = yOf(col.seabed);
    if(pen) ctx.lineTo(x,y); else { ctx.moveTo(x,y); pen = true; }
  });
  ctx.stroke();
  // isolines, labelled at their last point (nudged apart when two end together)
  const usedY = [];
  sectionContours(spec.v).forEach(ct=>{
    ctx.strokeStyle = th.mark; ctx.lineWidth = 1.4; ctx.setLineDash(ct.dash);
    ctx.beginPath();
    let pen = false, last = null;
    cols.forEach((col,i)=>{
      const d = col.land ? null : firstCrossing(col.at, col.seabed, ct.val);
      if(d===null){ pen = false; return; }
      const x = xOf(i), y = yOf(d);
      if(pen) ctx.lineTo(x,y); else { ctx.moveTo(x,y); pen = true; }
      last = [x,y];
    });
    ctx.stroke(); ctx.setLineDash([]);
    if(last){
      let ly = last[1]-5;
      while(usedY.some(u=>Math.abs(u-ly) < 13)) ly += 13;
      usedY.push(ly);
      ctx.font = CHART_FONT_BOLD; ctx.textAlign = 'right';
      ctx.lineWidth = 3; ctx.strokeStyle = th.bg; ctx.strokeText(ct.label, last[0]-4, ly);
      ctx.fillStyle = th.mark; ctx.fillText(ct.label, last[0]-4, ly);
      ctx.font = CHART_FONT;
    }
  });
  // observed profiles near the line: where this section can be checked against the ocean
  const near = sectionNearby(40);
  near.forEach(o=>{
    const x = pad.l + o.f*pw, glider = o.inst.kind!=='argo';
    ctx.strokeStyle = 'rgba(244,244,244,.7)'; ctx.lineWidth = 1; ctx.setLineDash([2,3]);
    ctx.beginPath(); ctx.moveTo(x, pad.t); ctx.lineTo(x, yOf(o.inst.maxDepth)); ctx.stroke();
    ctx.setLineDash([]);
    drawFloatGlyph(ctx, x, pad.t-7, 5, glider);
  });
  // A / B ends and the distance axis
  ctx.fillStyle = th.text; ctx.font = CHART_FONT_BOLD;
  ctx.textAlign = 'left'; ctx.fillText('A', 4, pad.t-3);
  ctx.textAlign = 'right'; ctx.fillText('B', w-2, pad.t-3);
  ctx.font = CHART_FONT;
  const step = [50,100,200,250,500,1000].find(s=>km/s <= 5) || 1000;
  ctx.fillStyle = th.tick; ctx.textAlign = 'center';
  for(let d=0; d<=km; d+=step) ctx.fillText(String(d), pad.l+d/km*pw, pad.t+ph+14);
  ctx.textAlign = 'right'; ctx.fillText('km', pad.l+pw, pad.t+ph+28);
  // colour key, so the plot explains itself whatever else is on screen
  const by = h-22, dp = spec.v==='temp' ? 0 : spec.v==='sal' ? 1 : 2;
  const lut = keyLUT(spec.palette);
  for(let x=0; x<pw; x++){
    const k = Math.min(255, Math.round(x/(pw-1)*255))*3;
    ctx.fillStyle = `rgb(${lut[k]},${lut[k+1]},${lut[k+2]})`;
    ctx.fillRect(pad.l+x, by, 1, 7);
  }
  ctx.fillStyle = th.tick;
  ctx.textAlign = 'left'; ctx.fillText(spec.min.toFixed(dp), pad.l, h-3);
  ctx.textAlign = 'center'; ctx.fillStyle = th.text2; ctx.fillText(`${spec.label} (${spec.unit})`, pad.l+pw/2, h-3);
  ctx.textAlign = 'right'; ctx.fillStyle = th.tick; ctx.fillText(spec.max.toFixed(dp), pad.l+pw, h-3);
  ctx.textAlign = 'left';
  sectionPlot = {cols, pad, pw, ph, maxD, spec};

  // header, summary and the nearby instruments
  document.getElementById('sectionName').textContent = section.name;
  document.getElementById('sectionKind').textContent = `${Math.round(km)} km · ${spec.label.toLowerCase()}`;
  const wet = cols.filter(col=>!col.land);
  const d26 = spec.v==='temp' ? wet.map(col=>firstCrossing(col.at, col.seabed, 26)).filter(d=>d!==null) : [];
  const landKm = (cols.length-wet.length)/cols.length*km;
  const meta = [`A ${ll2(...section.a)}`, `B ${ll2(...section.b)}`, timeLabel(state.time)];
  if(d26.length) meta.push(`D26 along the section: ${Math.round(Math.min(...d26))}–${Math.round(Math.max(...d26))} m`);
  if(landKm > 5) meta.push(`Crosses ${Math.round(landKm)} km of land.`);
  document.getElementById('sectionMeta').innerHTML = meta.join('<br>');
  const argoNear = near.filter(o=>o.inst.kind==='argo'), glNear = near.length-argoNear.length;
  const real = dataMode==='real';
  document.getElementById('sectionFoot').innerHTML =
    (near.length
      ? `<h3 class="grp-label">${real ? 'Argo profiles' : 'Profiles'} within 40 km</h3>` +
        argoNear.map(o=>`<button type="button" class="misfit" data-k="${near.indexOf(o)}">${escapeHtml(o.inst.id)}` +
          `<span>${Math.round(o.f*km)} km along</span></button>`).join('') +
        (glNear ? `<p>${glNear} glider profile${glNear>1 ? 's' : ''} (circles on the plot).</p>` : '')
      : `<p>${real ? 'No Argo profiles' : 'No floats or gliders'} within 40 km of this line.</p>`) +
    `<p>The depth axis is stretched so the upper ocean is readable. The 3D volume is dimmed while a section is open.</p>`;
  document.querySelectorAll('#sectionFoot .misfit').forEach(b=>{
    b.addEventListener('click', ()=>openProfile(near[+b.dataset.k].inst.userData));
  });
}
const SECTION_HINT = 'Hover over the section to read values.';
const sectionChartEl = document.getElementById('sectionChart');
sectionChartEl.addEventListener('mousemove', e=>{
  const out = document.getElementById('sectionReadout');
  if(!sectionPlot) return;
  const P = sectionPlot, r = sectionChartEl.getBoundingClientRect();
  const x = e.clientX-r.left-P.pad.l, y = e.clientY-r.top-P.pad.t;
  if(x < 0 || y < 0 || x > P.pw || y > P.ph){ out.textContent = SECTION_HINT; return; }
  const col = P.cols[Math.min(P.cols.length-1, Math.floor(x/P.pw*P.cols.length))];
  const z = P.maxD*Math.pow(y/P.ph, 2);
  const where = `<b>${Math.round(col.km)} km</b> · ${ll2(col.lat, col.lon)} · <b>${Math.round(z)} m</b>`;
  out.innerHTML = col.land ? `${where} · land`
    : z > col.seabed ? `${where} · below the sea floor (seabed ${Math.round(col.seabed)} m)`
    : `${where} · <b>${col.at(z).toFixed(VALUE_DP[P.spec.v] ?? 2)} ${P.spec.unit}</b>`;
});
sectionChartEl.addEventListener('mouseleave', ()=>{ document.getElementById('sectionReadout').textContent = SECTION_HINT; });

/* ---- observed profile (solid) over the co-located model column (dashed) ---- */
const MISFIT_BANDS = {temp:[0.5, 1.0], sal:[0.15, 0.30]};   // good below the first, poor above the second
function misfitWord(tab, rmse){
  const b = MISFIT_BANDS[tab];
  return b ? (rmse < b[0] ? 'good' : rmse <= b[1] ? 'fair' : 'poor') : '';
}
function drawProfileChart(){
  const inst = profileState.inst;
  if(!inst) return;
  const th = theme();
  const {ctx, w, h} = hidpiCanvas(document.getElementById('profChart'), 300);
  // imported profiles shown at other times ("show all") stay compared with their nearest analysis
  const {lat, lon} = inst, t = inst.real && Number.isInteger(inst.snap) ? inst.snap : state.time, tab = profileState.tab;
  const p = observeProfile(inst, t);
  const units = PROFILE_UNITS;
  const obs = obsArray(p, tab);
  const hasObs = obs.some(v=>Number.isFinite(v));
  const real = dataMode==='real', modelName = real ? 'HYCOM' : 'The model';

  // the chart spans the observed profile (model levels stop at 1000 m): 1000 m for Argo, less for shallow casts
  const maxDepth = inst.kind==='argo' || !(inst.maxDepth > 0) ? 1000 : Math.min(1000, Math.ceil(inst.maxDepth/100)*100);
  const modelZ = DEPTHS.filter(z=>z<maxDepth).concat([maxDepth]);
  const modelV = modelZ.map(z=>modelAt(tab, lat, lon, z, t));
  const hasModel = modelV.every(v=>v!==null);   // false for chlorophyll in real mode (HYCOM has none)
  const vals = hasModel ? modelV.slice() : [];
  if(hasObs) obs.forEach(v=>{ if(Number.isFinite(v)) vals.push(v); });
  if(!vals.length) vals.push(0, 1);
  let vMin = Math.min(...vals), vMax = Math.max(...vals);
  const padV = (vMax-vMin)*0.06 || 0.1; vMin -= padV; vMax += padV;

  const pad = {l:46, r:12, t:10, b:34};
  const plotW = w-pad.l-pad.r, plotH = h-pad.t-pad.b;
  // square-root depth axis: the upper ocean, where the thermocline sits, gets most of the height
  const yOf = z => pad.t + Math.sqrt(z/maxDepth)*plotH;
  const xOf = v => pad.l + (v-vMin)/(vMax-vMin)*plotW;

  ctx.lineWidth = 1; ctx.font = CHART_FONT;
  const ticks = [0,25,50,100,200,300,500,750,1000].filter(z=>z<=maxDepth);
  if(ticks[ticks.length-1]!==maxDepth) ticks.push(maxDepth);
  ctx.textAlign = 'right';
  ticks.forEach(z=>{
    const y = yOf(z);
    ctx.strokeStyle = th.grid; ctx.beginPath(); ctx.moveTo(pad.l,y); ctx.lineTo(w-pad.r,y); ctx.stroke();
    ctx.fillStyle = th.tick; ctx.fillText(z+' m', pad.l-5, y+4);
  });
  // value axis: a few round ticks, unit at the end
  const vt = niceTicks(vMin, vMax, 4), vstep = vt.length > 1 ? vt[1]-vt[0] : 1;
  ctx.textAlign = 'center';
  vt.forEach(v=>{
    const x = xOf(v);
    ctx.strokeStyle = th.grid; ctx.beginPath(); ctx.moveTo(x, pad.t); ctx.lineTo(x, pad.t+plotH); ctx.stroke();
    ctx.fillStyle = th.tick; ctx.fillText(tickText(v, vstep), x, pad.t+plotH+14);
  });
  ctx.textAlign = 'right'; ctx.fillText(units[tab], w-pad.r, pad.t+plotH+28);

  // model column: dashed, with its native levels marked
  if(hasModel){
    ctx.strokeStyle = th.model; ctx.lineWidth = 1.5; ctx.setLineDash([5,3]);
    ctx.beginPath();
    modelZ.forEach((z,i)=>{ const x=xOf(modelV[i]), y=yOf(z); if(i===0) ctx.moveTo(x,y); else ctx.lineTo(x,y); });
    ctx.stroke(); ctx.setLineDash([]);
    modelZ.forEach((z,i)=>{
      if(!DEPTHS.includes(z)) return;
      ctx.beginPath(); ctx.arc(xOf(modelV[i]), yOf(z), 2.2, 0, 7); ctx.stroke();
    });
  }

  if(hasObs){
    // observations: solid, broken at missing levels; flagged levels are ringed and skipped
    ctx.strokeStyle = th.obs; ctx.lineWidth = 2;
    ctx.beginPath();
    let pen = false;
    p.z.forEach((z,k)=>{
      if(!Number.isFinite(obs[k])){ pen = false; return; }
      if(p.flag[k]) return;
      const x = xOf(obs[k]), y = yOf(z);
      if(pen) ctx.lineTo(x,y); else { ctx.moveTo(x,y); pen = true; }
    });
    ctx.stroke();
    ctx.strokeStyle = th.flag; ctx.lineWidth = 1.5;
    p.z.forEach((z,k)=>{
      if(!p.flag[k] || !Number.isFinite(obs[k])) return;
      ctx.beginPath(); ctx.arc(xOf(obs[k]), yOf(z), 4.5, 0, 7); ctx.stroke();
    });
    if(!hasModel){ ctx.fillStyle = th.text2; ctx.textAlign = 'right'; ctx.fillText(`${modelName} has no ${PROFILE_NAMES[tab]}.`, w-pad.r-4, pad.t+plotH-8); }
  } else {
    ctx.fillStyle = th.text2; ctx.textAlign = 'center';
    ctx.fillText(`No ${PROFILE_NAMES[tab]} in this profile.`, pad.l+plotW/2, pad.t+plotH*0.5);
    if(!hasModel) ctx.fillText(`${modelName} has no ${PROFILE_NAMES[tab]}.`, pad.l+plotW/2, pad.t+plotH*0.5+16);
  }

  // the depth the value cards show
  const dSel = DEPTHS[profileState.depthIdx];
  if(dSel <= maxDepth){
    const ySel = yOf(dSel);
    ctx.strokeStyle = th.accent; ctx.lineWidth = 1; ctx.setLineDash([3,3]);
    ctx.beginPath(); ctx.moveTo(pad.l,ySel); ctx.lineTo(w-pad.r,ySel); ctx.stroke();
    ctx.setLineDash([]);
  }
  ctx.textAlign = 'left';

  const obsName = inst.imported ? instrumentLabel(inst.kind)
    : inst.kind==='argo' ? (real ? 'Argo' : 'Synthetic float') : 'Synthetic glider';
  document.getElementById('obsLegend').innerHTML =
    `<span><i class="lg lg-obs"></i>${escapeHtml(obsName)}</span>` +
    (hasModel ? `<span><i class="lg lg-model"></i>${real ? 'HYCOM' : 'Model'}</span>` : '') +
    (p.flag.some(f=>f) ? `<span><i class="lg lg-flag"></i>QC flag</span>` : '');

  const s = hasObs ? summarize(misfit(inst, t, tab)) : null;
  const dp = 2;
  const good = p.z.filter((z,k)=>Number.isFinite(obs[k]) && !p.flag[k]).length;
  const word = s ? misfitWord(tab, s.rmse) : '';
  document.getElementById('profStats').innerHTML = s ? `
    <div>Bias (observed − model)<b>${signed(s.bias,dp)} ${units[tab]}</b></div>
    <div>RMSE<b>${s.rmse.toFixed(dp)} ${units[tab]}${word ? ' · '+word : ''}</b></div>
    <div>Levels used<b>${good} of ${p.z.length}</b></div>` :
    `<div>Bias and RMSE<b>—</b></div><div>Levels used<b>0 of ${p.z.length}</b></div>`;
}

/* ---- the colour key, legend, context line and timeline labels in the viewport ----
   Everything here is drawn from state and colorbarSpec(); nothing decides a range or a colour. */
// the 256 sRGB entries of a palette: graphics' ramp texture data when it exists, else sampled from the palette
function keyLUT(palette){ return Render.rampLUT(palette); }
function exaggerationRatio(e){
  const k = typeof facts==='function' ? facts().real.view.exaggerationPerSlider : 404.4;
  const r = k*e, p = Math.pow(10, Math.floor(Math.log10(r))-1);
  return String(Math.round(r/p)*p);                       // two significant figures
}
function analysisTimes(){
  return META.times;
}
const demoDay = t=>1+3*t;
function dayMonth(iso){ return new Date(iso).toLocaleDateString('en-GB', {day:'numeric', month:'short', timeZone:'UTC'}); }

function keySpec(){
  const c = colorbarSpec(), meta = VAR_META[state.variable];
  const marks = [], legend = [], notes = [];
  let title;
  if(state.variable==='tchp'){
    title = 'Cyclone heat potential (kJ/cm²)';
    if(TCHP_THRESHOLD >= c.min && TCHP_THRESHOLD <= c.max) marks.push({v:TCHP_THRESHOLD, text:String(TCHP_THRESHOLD)});
    legend.push({sw:'contour', text:'50 kJ/cm² contour'});
  } else if(state.mode==='iso'){
    title = `Depth of the ${isoLabel().replace(/ depth$/, '')} surface (m)`;
  } else {
    const where = state.mode==='surface' ? 'surface (0 m)'
      : state.mode==='slice' ? `${DEPTHS[state.depthIdx]} m` : 'all depth levels';
    title = `${meta.label} (${meta.unit}) · ${where}`;
    if(state.variable==='temp' && 26 >= c.min && 26 <= c.max) marks.push({v:26, text:'26 °C'});
    if(state.mode==='surface') notes.push('Lower layers are faint: read values on the surface.');
    if(state.mode==='layers') notes.push('The layers blend: read values on a depth slice.');
  }
  if(state.showArgo){
    if(state.colorMisfit){
      legend.push({sw:'float good', text:'Float, temperature RMSE < 0.5 °C'});
      legend.push({sw:'float fair', text:'0.5–1.0 °C'});
      legend.push({sw:'float poor', text:'> 1.0 °C'});
      legend.push({sw:'float none', text:'No comparable levels'});
    } else legend.push({sw:'float', text: dataMode==='real' ? 'Argo float' : 'Synthetic float'});
  }
  if(state.showGlider && dataMode==='synth') legend.push({sw:'glider', text:'Synthetic glider waypoint'});
  if(trackGroup.visible){
    legend.push({sw:'track', text:dataSource().legend.track});
    if(stormNow.visible && dataSource().legend.now) legend.push({sw:'storm', text:dataSource().legend.now});
  }
  importedLegend().forEach(r=>legend.push(r));
  const scale = c.scale || (c.editable ? scaleNow() : 'linear');
  if(state.variable==='chl' && dataSource().vars && dataSource().vars.chlKey) notes.unshift(dataSource().vars.chlKey);
  if(scale==='log') notes.unshift('Log colour scale.');
  notes.push(`Vertical exaggeration ≈ ${exaggerationRatio(state.exagg)} : 1; below 1000 m the sea floor is compressed a further 12×.`);
  if(scale==='log'){
    const end = v=>String(+v.toPrecision(2));
    return {title, palette:c.palette, min:c.min, max:c.max, unit:c.unit, scale,
      ticks:logTicks(c.min, c.max).map(v=>({v, text:logTickText(v)})),
      ends:{min:end(c.min > 0 ? c.min : c.max*1e-3), max:end(c.max)}, marks, legend, note:notes.join(' ')};
  }
  const ticks = niceTicks(c.min, c.max), step = ticks.length > 1 ? ticks[1]-ticks[0] : 1;
  return {title, palette:c.palette, min:c.min, max:c.max, unit:c.unit, scale,
    ticks:ticks.map(v=>({v, text:tickText(v, step)})),
    ends:{min:tickText(c.min, step), max:tickText(c.max, step)}, marks, legend, note:notes.join(' ')};
}

function renderKey(spec){
  const $ = id=>document.getElementById(id);
  $('keyTitle').textContent = spec.title;
  const cv = $('keyBar'), dpr = Math.min(2, window.devicePixelRatio||1), bw = cv.clientWidth || 236;
  cv.width = Math.round(bw*dpr); cv.height = Math.round(12*dpr);
  const g = cv.getContext('2d'), lut = keyLUT(spec.palette);
  for(let i=0;i<cv.width;i++){
    const k = Math.min(255, Math.round(i/(cv.width-1)*255))*3;
    g.fillStyle = `rgb(${lut[k]},${lut[k+1]},${lut[k+2]})`; g.fillRect(i, 0, 1, cv.height);
  }
  const pos = v=>rampT(v, spec.min, spec.max, spec.scale || 'linear')*100;
  const row = $('keyTicks'); row.textContent = '';
  const add = (cls, left, text)=>{ const el = document.createElement('span'); el.className = cls; el.style.left = left+'%';
    if(text!==undefined) el.textContent = text; row.appendChild(el); };
  // marks (26 °C, 50 kJ/cm²) get bold labels; plain ticks too close to a mark or an end are left out
  const markPos = spec.marks.map(m=>pos(m.v));
  const endMark = e=>spec.marks.find(m=>Math.abs(pos(m.v)-e) < 3);
  const lo = endMark(0), hi = endMark(100);
  add('key-tick first'+(lo ? ' mark' : ''), 0, lo ? lo.text : spec.ends.min);
  add('key-tick last'+(hi ? ' mark' : ''), 100, hi ? hi.text : spec.ends.max);
  spec.marks.forEach((m,i)=>{ add('key-mark', markPos[i]); if(m!==lo && m!==hi) add('key-tick mark', markPos[i], m.text); });
  spec.ticks.forEach(t=>{ const p = pos(t.v); if([0, 100, ...markPos].every(o=>Math.abs(o-p) >= 12)) add('key-tick', p, t.text); });
  $('keyRange').textContent = `Colour range ${spec.ends.min} to ${spec.ends.max} ${spec.unit}` +
    (spec.scale==='log' ? ', log scale' : '') +
    (spec.marks.length ? `; marked: ${spec.marks.map(m=>m.text).join(', ')}` : '') + '.';
  const ul = $('keyLegend'); ul.textContent = '';
  spec.legend.forEach(r=>{
    const li = document.createElement('li'), sw = document.createElement('span'), [kind, cls] = r.sw.split(' ');
    sw.className = 'sw sw-'+kind+(cls ? ' '+cls : '');
    li.append(sw, document.createTextNode(r.text)); ul.appendChild(li);
  });
  $('keyNote').textContent = spec.note;
}

function renderContext(){
  const ctx = dataSource().ctx;
  document.getElementById('contextLine').classList.toggle('synth', dataMode!=='real');
  document.getElementById('ctxBadge').textContent = ctx.badge;
  document.getElementById('ctxText').textContent = ctx.text(state.time);
}

let timeLabelsFor = null;
function renderTimeLabels(){
  if(timeLabelsFor===dataMode) return;
  timeLabelsFor = dataMode;
  const el = document.getElementById('timeLabels'), times = analysisTimes();
  el.textContent = '';
  for(let t=0;t<NT;t++){
    const s = document.createElement('span');
    s.textContent = dataSource().time.tick(t);
    s.style.left = (t/(NT-1)*100)+'%';
    el.appendChild(s);
  }
}

// a one-line description of the 3D view, for the canvas's accessible name (graphics calls it)
function viewDescription(){
  const k = keySpec();
  const when = dataMode==='real' ? dayMonth(analysisTimes()[state.time]) : `demo day ${demoDay(state.time)}`;
  return `3D view: ${k.title}, ${when}`;
}

window.addEventListener('resize', ()=>{ if(document.getElementById('keyTitle').textContent) renderKey(keySpec()); });
// main.js calls this on every update
function updateLegend(){
  renderKey(keySpec());
  renderContext();
  renderTimeLabels();
  const r = document.getElementById('exRatioVal');
  if(r) r.textContent = exaggerationRatio(state.exagg);
  const cn = document.getElementById('chlNote'), vars = dataSource().vars;
  if(cn){ cn.textContent = state.variable==='chl' && vars ? vars.chlNote : ''; cn.hidden = !cn.textContent; }
  if(typeof importUI!=='undefined') importUI.syncMode();
}

// legend rows for imported instruments whose marker the built-in rows don't cover
const MARKER_SWATCH = {triangle:'ctd', square:'mooring', cross:'other', circle:'glider', diamond:'float'};
function importedLegend(){
  const OL = window.OceanLens;
  if(!OL || !OL.datasets || dataMode!=='real') return [];
  const types = new Set();
  OL.datasets().forEach(ds=>Object.keys(ds.types || {}).forEach(t=>types.add(t)));
  const rows = [];
  types.forEach(t=>{
    const spec = OL.instrument ? OL.instrument(t) : null, sw = MARKER_SWATCH[spec && spec.marker] || 'other';
    if(sw==='float') return;                                   // shares the Argo row
    const on = sw==='glider' ? state.showGlider : state.showOther!==false;
    if(on) rows.push({sw, text:`${instrumentLabel(t)} (your data)`});
  });
  return rows;
}
