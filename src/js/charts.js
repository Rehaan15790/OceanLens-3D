/* ---- the distance–depth plot in the right panel ---- */
let sectionPlot = null;   // what the chart last drew, for the hover readout
function drawSectionPanel(){
  if(!section) return;
  const spec = sectionSpec(), km = haversineKm(section.a, section.b);
  const c = document.getElementById('sectionChart');
  const {ctx, w, h} = hidpiCanvas(c, 280);
  const pad = {l:40, r:12, t:16, b:50};
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
      const rgb = col.land ? [0.34,0.30,0.21] : z > col.seabed ? [0.035,0.06,0.09]
        : paletteColor(spec.palette, (col.at(z)-spec.min)/(spec.max-spec.min));
      const o = (py*iw+px)*4;
      img.data[o] = rgb[0]*255; img.data[o+1] = rgb[1]*255; img.data[o+2] = rgb[2]*255; img.data[o+3] = 255;
    }
  }
  ctx.putImageData(img, Math.round(pad.l*dpr), Math.round(pad.t*dpr));   // device pixels: ignores the transform

  ctx.font = CHART_FONT;
  // depth grid and labels
  ctx.textAlign = 'right';
  [0,50,100,200,500,1000].forEach(z=>{
    const y = yOf(z);
    ctx.strokeStyle = 'rgba(255,255,255,.08)'; ctx.lineWidth = 1;
    ctx.beginPath(); ctx.moveTo(pad.l, y); ctx.lineTo(pad.l+pw, y); ctx.stroke();
    ctx.fillStyle = '#6e8ba0'; ctx.fillText(z+'m', pad.l-4, y+3);
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
    ctx.strokeStyle = '#ffffff'; ctx.lineWidth = 1.3; ctx.setLineDash(ct.dash);
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
      let ly = last[1]-4;
      while(usedY.some(u=>Math.abs(u-ly) < 11)) ly += 11;
      usedY.push(ly);
      ctx.fillStyle = '#ffffff'; ctx.textAlign = 'right'; ctx.fillText(ct.label, last[0]-3, ly);
    }
  });
  // observed profiles near the line: where this section can be checked against the ocean
  const near = sectionNearby(40);
  near.forEach(o=>{
    const x = pad.l + o.f*pw, argo = o.inst.kind==='argo';
    ctx.strokeStyle = argo ? 'rgba(255,128,88,.7)' : 'rgba(111,168,255,.7)';
    ctx.setLineDash([2,3]);
    ctx.beginPath(); ctx.moveTo(x, pad.t); ctx.lineTo(x, yOf(o.inst.maxDepth)); ctx.stroke();
    ctx.setLineDash([]);
    ctx.fillStyle = argo ? '#ff8058' : '#6fa8ff';
    ctx.beginPath();
    if(argo){ ctx.moveTo(x, pad.t-6); ctx.lineTo(x+4, pad.t-2); ctx.lineTo(x, pad.t+2); ctx.lineTo(x-4, pad.t-2); }
    else ctx.arc(x, pad.t-2, 3, 0, 7);
    ctx.fill();
  });
  // A / B ends and the distance axis
  ctx.fillStyle = '#ffffff'; ctx.textAlign = 'left'; ctx.fillText('A', pad.l, 10);
  ctx.textAlign = 'right'; ctx.fillText('B', pad.l+pw, 10);
  const step = [50,100,200,250,500,1000].find(s=>km/s <= 5) || 1000;
  ctx.fillStyle = '#6e8ba0'; ctx.textAlign = 'center';
  for(let d=0; d<=km; d+=step) ctx.fillText(String(d), pad.l+d/km*pw, pad.t+ph+12);
  ctx.textAlign = 'right'; ctx.fillText('km', pad.l+pw, pad.t+ph+24);
  // colour key, so the plot explains itself whatever the left panel shows
  const by = h-20, dp = spec.v==='cur' ? 2 : 1;
  for(let x=0; x<pw; x++){
    const col = paletteColor(spec.palette, x/pw);
    ctx.fillStyle = `rgb(${col[0]*255},${col[1]*255},${col[2]*255})`;
    ctx.fillRect(pad.l+x, by, 1, 6);
  }
  ctx.fillStyle = '#6e8ba0';
  ctx.textAlign = 'left'; ctx.fillText(spec.min.toFixed(dp), pad.l, h-4);
  ctx.textAlign = 'center'; ctx.fillText(spec.label+' · '+spec.unit, pad.l+pw/2, h-4);
  ctx.textAlign = 'right'; ctx.fillText(spec.max.toFixed(dp), pad.l+pw, h-4);
  ctx.textAlign = 'left';
  sectionPlot = {cols, pad, pw, ph, maxD, spec};

  // header, summary and the nearby instruments
  const fmt = p => `${p[0].toFixed(2)}°N ${p[1].toFixed(2)}°E`;
  document.getElementById('sectionName').textContent = section.name;
  document.getElementById('sectionKind').textContent = `${Math.round(km)} KM · ${spec.label.toUpperCase()}`;
  const wet = cols.filter(col=>!col.land);
  const d26 = spec.v==='temp' ? wet.map(col=>firstCrossing(col.at, col.seabed, 26)).filter(d=>d!==null) : [];
  const landKm = (cols.length-wet.length)/cols.length*km;
  document.getElementById('sectionMeta').innerHTML =
    `A ${fmt(section.a)}<br>B ${fmt(section.b)}<br>${timeLabel(state.time)}` +
    (d26.length ? `<br><span style="color:var(--accent)">D26 along the section: ${Math.round(Math.min(...d26))}–${Math.round(Math.max(...d26))} m</span>` : '') +
    (landKm > 5 ? `<br>crosses ${Math.round(landKm)} km of land` : '');
  const argoNear = near.filter(o=>o.inst.kind==='argo'), glNear = near.length-argoNear.length;
  document.getElementById('sectionFoot').innerHTML =
    (near.length
      ? `<div class="grp-label" style="margin-top:4px">OBSERVED PROFILES WITHIN 40 KM</div>` +
        argoNear.map(o=>`<button class="misfit" data-k="${near.indexOf(o)}">${o.inst.id}<span>${Math.round(o.f*km)} km<small>along</small></span></button>`).join('') +
        (glNear ? `<div>${glNear} glider profile${glNear>1?'s':''} (blue dots on the plot)</div>` : '')
      : 'No floats or gliders within 40 km of this line.') +
    `<div style="margin-top:8px">Depth axis is stretched so the upper ocean is readable. The 3D volume is
    dimmed while a section is open; close the section to restore it.</div>`;
  document.querySelectorAll('#sectionFoot .misfit').forEach(b=>{
    b.addEventListener('click', ()=>openProfile(near[+b.dataset.k].inst.userData));
  });
}
const sectionChartEl = document.getElementById('sectionChart');
sectionChartEl.addEventListener('mousemove', e=>{
  const out = document.getElementById('sectionReadout');
  if(!sectionPlot) return;
  const P = sectionPlot, r = sectionChartEl.getBoundingClientRect();
  const x = e.clientX-r.left-P.pad.l, y = e.clientY-r.top-P.pad.t;
  if(x < 0 || y < 0 || x > P.pw || y > P.ph){ out.textContent = 'hover the section for values'; return; }
  const col = P.cols[Math.min(P.cols.length-1, Math.floor(x/P.pw*P.cols.length))];
  const z = P.maxD*Math.pow(y/P.ph, 2);
  const where = `<b>${Math.round(col.km)} km</b> · ${col.lat.toFixed(2)}°N ${col.lon.toFixed(2)}°E · <b>${Math.round(z)} m</b>`;
  out.innerHTML = col.land ? `${where} · land`
    : z > col.seabed ? `${where} · below the sea floor (${Math.round(col.seabed)} m)`
    : `${where} · <b>${col.at(z).toFixed(P.spec.v==='cur' ? 3 : 2)} ${P.spec.unit}</b>`;
});
sectionChartEl.addEventListener('mouseleave', ()=>{ document.getElementById('sectionReadout').textContent = 'hover the section for values'; });

// a canvas sized in CSS pixels, drawn at device resolution so text stays sharp
function hidpiCanvas(c, cssH){
  const dpr = Math.min(2, window.devicePixelRatio || 1);
  const w = c.clientWidth || 258;
  c.width = Math.round(w*dpr); c.height = Math.round(cssH*dpr); c.style.height = cssH+'px';
  const ctx = c.getContext('2d');
  ctx.setTransform(dpr,0,0,dpr,0,0);
  ctx.clearRect(0,0,w,cssH);
  return {ctx, w, h:cssH};
}
const CHART_FONT = "9.5px 'JetBrains Mono', Consolas, monospace";

/* ---- observed profile (solid) over the co-located model column (dashed) ---- */
function drawProfileChart(){
  const inst = profileState.inst;
  if(!inst) return;
  const {ctx, w, h} = hidpiCanvas(document.getElementById('profChart'), 300);
  const {lat, lon} = inst, t = state.time, tab = profileState.tab;
  const p = observeProfile(inst, t);
  const colors = {temp:'#ff8058', sal:'#2fe0c4', chl:'#8fd15b', dens:'#c9a0ff'};
  const units  = {temp:'°C', sal:'PSU', chl:'mg/m³', dens:'kg/m³'};
  const obs = obsArray(p, tab);
  const hasObs = !(tab==='chl' && !inst.bgc);

  // the chart spans the observed profile: 1000 m for Argo, a few hundred metres for gliders
  const maxDepth = inst.kind==='argo' ? 1000 : Math.ceil(inst.maxDepth/100)*100;
  const modelZ = DEPTHS.filter(z=>z<maxDepth).concat([maxDepth]);
  const modelV = modelZ.map(z=>modelAt(tab, lat, lon, z, t));
  const hasModel = modelV.every(v=>v!==null);   // false for chlorophyll in real mode (HYCOM has none)
  const vals = hasModel ? modelV.slice() : [];
  if(hasObs) obs.forEach(v=>{ if(Number.isFinite(v)) vals.push(v); });
  if(!vals.length) vals.push(0, 1);
  let vMin = Math.min(...vals), vMax = Math.max(...vals);
  const padV = (vMax-vMin)*0.06 || 0.1; vMin -= padV; vMax += padV;

  const pad = {l:40, r:12, t:12, b:24};
  const plotW = w-pad.l-pad.r, plotH = h-pad.t-pad.b;
  // square-root depth axis: the upper ocean, where the thermocline sits, gets most of the height
  const yOf = z => pad.t + Math.sqrt(z/maxDepth)*plotH;
  const xOf = v => pad.l + (v-vMin)/(vMax-vMin)*plotW;

  ctx.strokeStyle = '#1b3247'; ctx.lineWidth = 1;
  ctx.fillStyle = '#4b6577'; ctx.font = CHART_FONT; ctx.textAlign = 'left';
  const ticks = [0,25,50,100,200,300,500,750,1000].filter(z=>z<=maxDepth);
  if(ticks[ticks.length-1]!==maxDepth) ticks.push(maxDepth);
  ticks.forEach(z=>{
    const y = yOf(z);
    ctx.beginPath(); ctx.moveTo(pad.l,y); ctx.lineTo(w-pad.r,y); ctx.stroke();
    ctx.fillText(z+'m', 2, y+3);
  });
  ctx.beginPath(); ctx.moveTo(pad.l,pad.t); ctx.lineTo(pad.l,pad.t+plotH); ctx.stroke();

  // model column: dashed, with its native levels marked
  if(hasModel){
  ctx.strokeStyle = '#8fa8c0'; ctx.lineWidth = 1.5; ctx.setLineDash([4,3]);
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
    ctx.strokeStyle = colors[tab]; ctx.lineWidth = 2;
    ctx.beginPath();
    let pen = false;
    p.z.forEach((z,k)=>{
      if(!Number.isFinite(obs[k])){ pen = false; return; }
      if(p.flag[k]) return;
      const x = xOf(obs[k]), y = yOf(z);
      if(pen) ctx.lineTo(x,y); else { ctx.moveTo(x,y); pen = true; }
    });
    ctx.stroke();
    ctx.fillStyle = colors[tab];
    p.z.forEach((z,k)=>{
      if(!Number.isFinite(obs[k]) || p.flag[k]) return;
      ctx.beginPath(); ctx.arc(xOf(obs[k]), yOf(z), 1.7, 0, 7); ctx.fill();
    });
    ctx.strokeStyle = '#ff5c6c'; ctx.lineWidth = 1.5;
    p.z.forEach((z,k)=>{
      if(!p.flag[k] || !Number.isFinite(obs[k])) return;
      ctx.beginPath(); ctx.arc(xOf(obs[k]), yOf(z), 4.5, 0, 7); ctx.stroke();
    });
    if(!hasModel){ ctx.fillStyle = '#6e8ba0'; ctx.textAlign = 'right'; ctx.fillText('no model chlorophyll (HYCOM)', w-pad.r-2, pad.t+plotH-6); ctx.textAlign = 'left'; }
  } else {
    ctx.fillStyle = '#6e8ba0'; ctx.textAlign = 'center';
    ctx.fillText('no chlorophyll sensor on this float', pad.l+plotW/2, pad.t+plotH*0.55);
    ctx.fillText(hasModel ? 'model column only' : 'and HYCOM has no chlorophyll', pad.l+plotW/2, pad.t+plotH*0.55+14);
    ctx.textAlign = 'left';
  }

  // the depth currently shown in the cards
  const dSel = DEPTHS[profileState.depthIdx];
  if(dSel <= maxDepth){
    const ySel = yOf(dSel);
    ctx.strokeStyle = 'rgba(47,224,196,.55)'; ctx.lineWidth = 1; ctx.setLineDash([3,3]);
    ctx.beginPath(); ctx.moveTo(pad.l,ySel); ctx.lineTo(w-pad.r,ySel); ctx.stroke();
    ctx.setLineDash([]);
  }

  ctx.fillStyle = '#6e8ba0';
  ctx.fillText(vMin.toFixed(2), pad.l, h-8);
  ctx.textAlign = 'right';
  ctx.fillText(vMax.toFixed(2)+' '+units[tab], w-pad.r, h-8);
  ctx.textAlign = 'left';

  const label = inst.kind==='argo' ? 'Argo obs' : 'glider obs';
  document.getElementById('obsLegend').innerHTML =
    `<span><i class="o" style="background:${colors[tab]}"></i>${label}</span>`+
    (hasModel ? `<span><i class="m"></i>model</span>` : '')+
    (p.flag.some(f=>f) ? `<span><i class="q"></i>QC flag</span>` : '');

  const s = hasObs ? summarize(misfit(inst, t, tab)) : null;
  const dp = VAR_DP[tab];
  const good = p.z.filter((z,k)=>Number.isFinite(obs[k]) && !p.flag[k]).length;
  document.getElementById('profStats').innerHTML = s ? `
    <div><span>BIAS (OBS − MODEL)</span><b>${fmtSigned(s.bias,dp)} ${units[tab]}</b></div>
    <div><span>RMSE</span><b class="${s.rmse>RMSE_WARN[tab]?'bad':'good'}">${s.rmse.toFixed(dp)}</b></div>
    <div><span>LEVELS</span><b>${good}/${p.z.length}</b></div>` :
    `<div><span>BIAS / RMSE</span><b>—</b></div><div><span>LEVELS</span><b>0/${p.z.length}</b></div>`;
}

function updateLegend(){
  const rows = [];
  if(state.variable==='tchp'){
    rows.push('<div class="row"><span class="sw thr"></span> 50 kJ/cm² contour</div>');
    rows.push('<div class="row"><span class="sw d26"></span> D26 surface (26 °C)</div>');
    rows.push(`<div class="row"><span class="sw trk"></span> ${dataMode==='real' ? 'Amphan track (IBTrACS)' : 'sample cyclone track'}</div>`);
  }
  if(state.colorMisfit && state.showArgo){
    MISFIT_CLASSES.forEach((c,i)=>rows.push(`<div class="row"><span class="sw argo m${i}"></span> float ${c.label}</div>`));
  } else rows.push('<div class="row"><span class="sw argo"></span> Argo float</div>');
  if(dataMode==='synth') rows.push('<div class="row"><span class="sw gld"></span> Glider waypoint</div>');
  rows.push('<div class="row"><span class="sw shelf"></span> shelf → abyssal</div>');
  document.getElementById('legend').innerHTML = rows.join('');
}
