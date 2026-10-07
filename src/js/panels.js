/* ============================================================
   PROFILE, VALIDATION AND TRACK PANELS (right-hand side)
   Logic here; ux owns the templates (3.1b). Strings that depend on the data source come from
   dataSource(); anything from the data goes through fmt.esc before it reaches innerHTML.
   ============================================================ */
// what the profile panel shows: inst is null while the followed float has no profile at this time
const profileState = {open:false, inst:null, current:null, floatId:null, tab:'temp', depthIdx:undefined};
const trackState = {open:false};
const fmtSigned = fmt.signed;
const VAR_DP = {temp:2, sal:2, dens:2, chl:2, oxy:1, cur:2};
// RMSE above which a profile's misfit is highlighted (per variable)
const RMSE_WARN = {temp:0.5, sal:0.15, dens:0.15, chl:0.1, oxy:10, cur:0.1};

function showPanel(which){   // 'empty' | 'profile' | 'track' | 'section'
  state.panel = which;
  profileState.open = which==='profile'; trackState.open = which==='track'; sectionState.open = which==='section';
  ui.profile.classList.toggle('show', profileState.open);
  ui.trackPanel.classList.toggle('show', trackState.open);
  ui.sectionPanel.classList.toggle('show', sectionState.open);
  ui.rightEmpty.hidden = which!=='empty';
  if(which!=='profile') showSelection(null);
}
// closing a profile or the track goes back to the section if one is drawn, else to the summary
function closeSidePanels(){
  if(section){ showPanel('section'); drawSectionPanel(); }
  else showPanel('empty');
}
function worstArgo(){
  let worst = null, top = -1;
  instrumentsNow().filter(i=>i.kind==='argo' && !i.imported).forEach(inst=>{
    const s = summarize(misfit(inst, state.time, 'temp'));
    if(s && s.rmse > top){ top = s.rmse; worst = inst; }
  });
  return worst;
}

/* ---- actions ---- */
function openPanel(name){
  if(name==='track') return openTrack();
  if(name==='section'){ if(section){ showPanel('section'); drawSectionPanel(); } return; }
  closeSidePanels();
}
function closePanels(){ closeSidePanels(); }
// the float that disagrees most with the model; false when there is none to show
function openWorstFloat(){
  const w = worstArgo();
  if(!w) return false;
  openProfile(w.userData);
  return true;
}

/* ---- regional validation summary: shown when no instrument is selected ---- */
let validationFor = null;
function renderValidation(){
  const key = dataMode+state.time;
  if(validationFor===key) return;   // depends on the source and time step only
  validationFor = key;
  const src = dataSource(), t = state.time, bands = [[0,100],[100,300],[300,1000]];
  const insts = instrumentsNow().filter(i=>!i.imported);   // the summary is the built-in comparison (facts)
  const table = (v, title) => {
    const all = insts.flatMap(inst=>misfit(inst, t, v));
    const rows = bands.map(([z0,z1])=>{
      const s = summarize(all.filter(o=>o.z>z0 && o.z<=z1));
      return `<tr><td>${z0}–${z1} m</td><td>${s?fmtSigned(s.bias,VAR_DP[v]):'—'}</td>`+
        `<td>${s?s.rmse.toFixed(VAR_DP[v]):'—'}</td><td>${s?s.n:0}</td></tr>`;
    }).join('');
    return `<table class="val-table"><caption>${title}</caption>`+
      `<tr><th>Depth</th><th>Bias</th><th>RMSE</th><th>Levels</th></tr>${rows}</table>`;
  };
  const ranked = insts.map(inst=>({inst, s:summarize(misfit(inst, t, 'temp'))}))
    .filter(o=>o.s).sort((a,b)=>b.s.rmse-a.s.rmse).slice(0,4);
  const flagged = insts.filter(inst=>observeProfile(inst, t).flag.some(f=>f));
  const nArgo = insts.filter(i=>i.kind==='argo').length;
  ui.valPanel.innerHTML = `
    <h2 class="val-h">${fmt.esc(src.val.heading)}</h2>
    <p class="val-sub">${fmt.esc(src.val.sub(t, nArgo, insts.length-nArgo))}</p>
    ${table('temp', 'Temperature (°C), observed − model')}
    ${table('sal', 'Salinity (PSU), observed − model')}
    <h3 class="grp-label">Largest temperature misfit</h3>
    ${ranked.map(o=>`<button type="button" class="misfit" data-k="${insts.indexOf(o.inst)}">${fmt.esc(o.inst.id)}`+
      `<span>RMSE ${o.s.rmse.toFixed(2)} °C · ${misfitWord('temp', o.s.rmse)}</span></button>`).join('')}
    <p class="val-foot">${flagged.length
      ? `QC: density inversion flagged in ${fmt.esc(flagged.map(i=>i.id).join(', '))}; flagged levels are left out of the statistics.`
      : 'QC: no density inversions.'}</p>
    <p class="val-foot">${fmt.esc(src.val.foot)}</p>`;
  ui.valPanel.querySelectorAll('.misfit').forEach(b=>{
    b.addEventListener('click', ()=>openProfile(insts[+b.dataset.k].userData));
  });
}

/* ---- derived quantities at the selected depth: observed, with the model beside it ---- */
function renderDerived(){
  const inst = profileState.inst;
  if(!inst) return;
  const {lat, lon} = inst, t = state.time, d = DEPTHS[profileState.depthIdx];
  const p = observeProfile(inst, t);
  const below = d > inst.maxDepth;
  const at = arr => below ? null : obsInterp(p, arr)(d);
  const oT = at(p.T), oS = at(p.S), oC = inst.bgc ? at(p.C) : null;
  const mT = modelAtObs(inst,'temp',d), mS = modelAtObs(inst,'sal',d), mC = modelAtObs(inst,'chl',d);
  const mOK = mT!==null && mS!==null;
  const why = below ? 'below the profile' : 'gap in the profile';
  const f = (v,dp) => v===null ? null : v.toFixed(dp);
  const card = (label, obs, model, unit, note) => `
    <div class="deriv"><div class="lb">${label}</div>
      <div class="vl${obs===null?' na':''}">${obs===null?'—':obs}<small>${obs===null?'':unit}</small></div>
      <div class="md">${obs===null && note ? note+' · ' : ''}model ${model===null?'—':model}</div></div>`;
  const oOK = oT!==null && oS!==null;
  const seabed = bathyDepth(lat, lon);
  const oH = obsTchp(inst, p, seabed);
  const mH = inst.model ? tchpColumn(z=>modelAtObs(inst,'temp',z), z=>modelAtObs(inst,'sal',z), Math.min(seabed, DEPTHS[DEPTHS.length-1]))
                        : modelTchp(lat, lon, t, seabed);
  // D26 is a depth, or a reason there is none: surface already below 26 °C, or warm to the sea floor
  const d26 = r => r.q===null || r.q===0 || r.d26===null ? null : r.d26.toFixed(0);
  const d26Note = r => r.q===null ? 'gap at D26' : r.q===0 ? 'surface below 26 °C' : r.d26===null ? 'warm to the sea floor' : '';
  const d26Model = r => r.q===null ? null : r.q===0 ? 'none' : r.d26===null ? 'floor' : r.d26.toFixed(0);
  const salNote = inst.salOk===false ? 'failed delayed-mode QC' : why;
  ui.profDepthVal.textContent = fmt.depthLabel(d);
  ui.derivGrid.innerHTML =
    card('Temperature', f(oT,1), f(mT,1), '°C', why) +
    card('Salinity', f(oS,2), f(mS,2), 'PSU', salNote) +
    card('D26', d26(oH), d26Model(mH), 'm', d26Note(oH)) +
    card('Cyclone heat potential', f(oH.q,0), f(mH.q,0), 'kJ/cm²', 'gap at D26');
  const more = document.getElementById('derivMore');
  if(more) more.innerHTML =
    card('Density σ<sub>t</sub>', oOK ? sigmaT(oS,oT).toFixed(2) : null, mOK ? sigmaT(mS,mT).toFixed(2) : null, 'kg/m³', salNote) +
    card('Sound speed', oOK ? soundSpeed(oT,oS,d).toFixed(0) : null, mOK ? soundSpeed(mT,mS,d).toFixed(0) : null, 'm/s', salNote) +
    card('Chlorophyll', f(oC,2), f(mC,2), 'mg/m³', inst.bgc ? why : 'no sensor') +
    `<div class="deriv"><div class="lb">Pressure</div>
      <div class="vl">${pressureDbar(d, lat).toFixed(0)}<small>dbar</small></div><div class="md">from depth</div></div>`;

  // QC summary for this profile
  const flagged = p.z.filter((z,k)=>p.flag[k]);
  const nMissing = p.missing.reduce((a,b)=>a+b, 0);
  ui.qcBadge.innerHTML = flagged.length
    ? `<div class="qc flag">QC: density inversion at ${flagged.join(', ')} m; left out of bias and RMSE.</div>`
    : `<div class="qc pass">QC: no density inversions${nMissing ? `; ${nMissing} missing levels, shown as gaps` : ''}.</div>`;
}

// takes an instrument or its userData (a glider waypoint's userData carries wp)
function openProfile(x){
  const userData = x.userData && !x.data && !x.wp ? x.userData : x;
  const inst = userData.wp ? userData.wp.inst : userData.data.inst;
  const type = instrumentType(inst.kind);
  showPanel('profile');
  showSelection(inst);
  profileState.current = userData;
  profileState.inst = inst;
  profileState.floatId = inst.id;
  profileState.tab = type.profileTab || 'temp';
  syncProfileTabs();
  if(profileState.depthIdx===undefined) profileState.depthIdx = state.mode==='slice' ? state.depthIdx : 4;
  const seabed = Math.round(bathyDepth(inst.lat, inst.lon));
  const where = `<span class="region">${fmt.esc(regionName(inst.lat, inst.lon))}</span><br>`+
    `${fmt.latLon2(inst.lat, inst.lon)}<br>`;
  const sampling = `${inst.imported ? Math.round(inst.levels[0]) : 5}–${Math.round(inst.maxDepth)} m, ${inst.levels.length} levels · seabed ${seabed} m`;
  const MODE = {D:'Delayed mode (quality-controlled)', A:'Adjusted', R:'Real time'};
  const salNote = inst.salOk===false ? '<br>Salinity failed delayed-mode QC; density uses model salinity.' : '';
  ui.profName.textContent = userData.data.id;
  ui.profKind.textContent = type.kindLabel(inst, userData.wpIndex);
  ui.profKind.className = 'kind kind-' + (inst.imported ? type.marker : userData.type==='argo' ? 'argo' : 'glider');
  if(inst.imported){
    const gap = inst.gapH, far = Math.abs(gap) > 36;
    ui.profMeta.innerHTML = where + `${fmt.esc(inst.datasetName)} · ${far ? fmt.analysisTime(inst.time) : fmt.floatTime(inst.time)}<br>` +
      `${far ? 'Compared with the nearest analysis, ' : 'Model: the analysis '}${fmt.dp(Math.abs(gap)/24, 1)} days ` +
      `${gap >= 0 ? 'before' : 'after'} the profile<br>${sampling}`;
  } else if(userData.type==='argo'){
    ui.profMeta.innerHTML = where + (inst.real
      ? `Cycle ${fmt.esc(inst.cycle)} · ${fmt.floatTime(inst.time)} · ${fmt.esc(modelGap(inst))}<br>`+
        `${fmt.esc(MODE[inst.mode]||inst.mode)} · ${sampling}${salNote}`
      : `Cycle ${fmt.esc(userData.data.cycle)}<br>${sampling}`);
  } else {
    ui.profMeta.innerHTML = where + `${fmt.esc(userData.wp.time)}<br>${sampling}`;
  }
  ui.profDepth.value = profileState.depthIdx;
  renderDerived();
  drawProfileChart();
}
// how far the model used for the comparison is from the profile: the matched output (P3), or the analysis
function modelGap(inst){
  const h = modelGapHours(inst);
  if(h===null) return analysisGap(inst);
  return `model matched at ${fmt.floatTime(inst.model.time)}, ${fmt.dp(Math.abs(h), 1)} h ${h >= 0 ? 'before' : 'after'} the profile`;
}
// tabs beyond temperature, salinity and density (chlorophyll, oxygen, current speed) only for profiles that
// carry them; a hidden current tab falls back to temperature
function syncProfileTabs(){
  const inst = profileState.inst, p = inst ? observeProfile(inst, state.time) : null;
  const tabs = document.querySelectorAll('.tabbtn');
  tabs.forEach(b=>{
    const k = b.dataset.p, core = ['temp', 'sal', 'dens'].includes(k), arr = p && !core ? obsArray(p, k) : null;
    b.hidden = !core && !(arr && arr.some(Number.isFinite));
  });
  if([...tabs].some(b=>b.hidden && b.dataset.p===profileState.tab)) profileState.tab = 'temp';
  tabs.forEach(b=>setSelected(b, b.dataset.p===profileState.tab));
}
function setSelected(b, on){
  b.classList.toggle('active', on);
  b.setAttribute('aria-selected', on ? 'true' : 'false');
}

// A real profile belongs to one analysis. When the time changes, show the same float's profile at
// the new analysis, or say that it has none within 1.5 days. An imported profile still on screen (within
// 1.5 days, or "show all") stays: the cycles of a float and the dives of a glider share one id.
function followProfile(){
  if(state.panel!=='profile' || dataMode!=='real' || !profileState.floatId) return;
  const now = instrumentsNow(), cur = profileState.inst;
  if(cur && (cur.snap===state.time || now.includes(cur))) return;
  const match = now.find(p=>p.id===profileState.floatId);
  if(match){
    const tab = profileState.tab;
    openProfile(match.userData);
    profileState.tab = tab;
    syncProfileTabs();
    drawProfileChart();
    return;
  }
  profileState.inst = null;
  showSelection(null);
  ui.profMeta.innerHTML = `${fmt.esc(dataSource().profile.none(profileState.floatId, state.time))}<br>`+
    `<button type="button" class="btn btn-small" id="profBack">Back to comparison</button>`;
  document.getElementById('profBack').addEventListener('click', closePanels);
  ui.qcBadge.textContent = ''; ui.derivGrid.textContent = '';
  const more = document.getElementById('derivMore');
  if(more) more.textContent = '';
  ['profStats', 'obsLegend'].forEach(id=>{ const el = document.getElementById(id); if(el) el.textContent = ''; });
  const c = document.getElementById('profChart');
  if(c) c.getContext('2d').clearRect(0, 0, c.width, c.height);
}

ui.profDepth.addEventListener('input', e=>{
  profileState.depthIdx = parseInt(e.target.value, 10);
  renderDerived();
  drawProfileChart();
});
ui.closeProf.addEventListener('click', closeSidePanels);
ui.closeTrack.addEventListener('click', closeSidePanels);
document.querySelectorAll('.tabbtn').forEach(btn=>{
  btn.addEventListener('click', ()=>{
    document.querySelectorAll('.tabbtn').forEach(b=>setSelected(b, b===btn));
    profileState.tab = btn.dataset.p;
    drawProfileChart();
  });
});

/* ---- cyclone track: ocean heat along the path ---- */
function openTrack(){
  showPanel('track');
  renderTrack();
}
function renderTrack(){
  const src = dataSource(), real = dataMode==='real', T = currentTrack();
  ui.trackName.textContent = src.track.name;
  ui.trackKind.textContent = src.track.badge;
  if(!T.length){
    ui.trackMeta.textContent = src.track.empty;
    ui.trackTable.textContent = '';
    const c = ui.trackChart; c.getContext('2d').clearRect(0, 0, c.width, c.height);
    return;
  }
  const heat = t => T.map(p=>modelTchp(p.lat, p.lon, t, bathyDepth(p.lat, p.lon)));
  const [hMin, hMax] = state.variable==='tchp' ? [state.cbMin, state.cbMax] : facts()[dataMode].ranges.tchp.column;
  const heatCol = q=>{ const c = paletteColor('heat', (q-hMin)/(hMax-hMin)); return `rgb(${c[0]*255},${c[1]*255},${c[2]*255})`; };
  const {ctx, w, h} = hidpiCanvas(ui.trackChart, 190);
  const pad = {l:32, r:real ? 30 : 10, t:22, b:22}, pw = w-pad.l-pad.r, ph = h-pad.t-pad.b;
  let series, vMax;
  if(real){
    // ocean heat under the path before the storm and after it: the difference is its cold wake
    // facts: the analyses before and after the storm, heat under the path (landfall point excluded), peaks
    const F = facts().real, ri = (r, who)=>r ? `${fmt.esc(r.from)}→${fmt.esc(r.to)} kt (${who})` : '';
    const before = F.path.beforeIndex, after = F.path.afterIndex, heatB = F.path.heatBefore, heatA = F.path.heatAfter;
    const qb = heat(before), qa = heat(after);
    const pj = {usa:F.track.peakJtwc.kt, t:F.track.peakJtwc.time}, pi = {nd:F.track.peakImd.kt, t:F.track.peakImd.time};
    const last = F.track.landfall;
    ui.trackMeta.innerHTML =
      `First fix ${fmt.fixTime(F.track.firstFix)}; landfall ${fmt.fixTime(last.time)} near ${fmt.latLon1(last.lat, last.lon)}<br>`+
      `Peak ${pi.nd!=null ? `${fmt.esc(pi.nd)} kt (IMD, ${fmt.fixTime(pi.t)}) · ` : ''}${fmt.esc(pj.usa)} kt (JTWC, ${fmt.fixTime(pj.t)})<br>`+
      (F.track && F.track.ri24Jtwc ? `Fastest 24-hour intensification: ${[ri(F.track.ri24Imd,'IMD'), ri(F.track.ri24Jtwc,'JTWC')].filter(Boolean).join(', ')}<br>` : '')+
      `<span class="accent">Heat under the path, landfall excluded: ${fmt.int(heatB)} kJ/cm² on ${fmt.dayMonth(META.times[before])}, `+
      `${fmt.int(heatA)} on ${fmt.dayMonth(META.times[after])}</span><br>`+
      `<span class="chart-key"><i class="lg lg-bar"></i>${fmt.dayMonth(META.times[before])} <i class="lg lg-outline"></i>${fmt.dayMonth(META.times[after])} `+
      `<i class="lg lg-obs"></i>wind, IMD <i class="lg lg-model"></i>JTWC</span>`;
    series = {qb, qa, before, after};
    vMax = Math.max(TCHP_THRESHOLD*1.6, ...qb.map(r=>r.q), ...qa.map(r=>r.q))*1.08;
  } else {
    const qs = heat(state.time);
    const over = qs.filter(r=>r.q>=TCHP_THRESHOLD).length;
    let pk = 0; qs.forEach((r,k)=>{ if(r.q > qs[pk].q) pk = k; });
    ui.trackMeta.innerHTML =
      `Bay of Bengal to the Andhra coast, 12-hourly, ${T[T.length-1].h} h<br>`+
      `Ocean state: ${fmt.esc(timeLabel(state.time))}<br>`+
      `<span class="accent">${over} of ${T.length} positions at or above ${TCHP_THRESHOLD} kJ/cm²; peak ${qs[pk].q.toFixed(0)} at +${T[pk].h} h</span>`;
    series = {qs};
    vMax = Math.max(TCHP_THRESHOLD*1.6, ...qs.map(r=>r.q))*1.08;
  }
  const y = v => pad.t + ph - v/vMax*ph;
  ctx.font = CHART_FONT;
  const th = theme();
  ctx.fillStyle = th.tick;
  ctx.fillText('kJ/cm²', 4, 12);
  ctx.strokeStyle = th.grid; ctx.lineWidth = 1; ctx.fillStyle = th.tick;
  for(let v=0; v<=vMax; v+=50){
    ctx.beginPath(); ctx.moveTo(pad.l, y(v)); ctx.lineTo(w-pad.r, y(v)); ctx.stroke();
    ctx.fillText(String(v), 4, y(v)+3);
  }
  const slot = pw/T.length;
  T.forEach((p,k)=>{
    const x = pad.l + k*slot;
    if(real){
      const bw = slot*0.4, qb = series.qb[k].q, qa = series.qa[k].q;
      ctx.fillStyle = heatCol(qb); ctx.fillRect(x+slot*0.1, y(qb), bw, y(0)-y(qb));
      ctx.strokeStyle = th.text; ctx.lineWidth = 1; ctx.strokeRect(x+slot*0.5+0.5, y(qa)+0.5, bw-1, y(0)-y(qa)-1);
    } else {
      const bw = slot*0.62, q = series.qs[k].q;
      ctx.fillStyle = heatCol(q); ctx.fillRect(x+(slot-bw)/2, y(q), bw, y(0)-y(q));
    }
    const every = real ? 4 : 2;
    if(k%every===0){
      ctx.fillStyle = th.tick; ctx.textAlign = 'center';
      ctx.fillText(real ? new Date(p.t).getUTCDate()+'' : '+'+p.h+'h', x+slot/2, h-7);
      ctx.textAlign = 'left';
    }
  });
  ctx.strokeStyle = th.tick; ctx.setLineDash([1,3]);
  ctx.beginPath(); ctx.moveTo(pad.l, y(TCHP_THRESHOLD)); ctx.lineTo(w-pad.r, y(TCHP_THRESHOLD)); ctx.stroke();
  ctx.setLineDash([]);
  if(real){
    // winds on their own axis (IMD solid, JTWC dashed): the intensification lines up with the heat underneath
    const yw = v => pad.t + ph - v/160*ph;
    [['nd', [], th.text], ['usa', [4,3], th.model]].forEach(([key, dash, colour])=>{
      ctx.strokeStyle = colour; ctx.lineWidth = 1.6; ctx.setLineDash(dash); ctx.beginPath();
      let pen = false;
      T.forEach((p,k)=>{ if(p[key]==null){ pen = false; return; } const x = pad.l+(k+0.5)*slot; if(pen) ctx.lineTo(x, yw(p[key])); else { ctx.moveTo(x, yw(p[key])); pen = true; } });
      ctx.stroke(); ctx.setLineDash([]);
    });
    ctx.fillStyle = th.tick; ctx.textAlign = 'left';
    [0,80,160].forEach(v=>ctx.fillText(String(v), w-pad.r+3, yw(v)+3));
    ctx.fillText('kt', w-pad.r+3, pad.t-8);
  }

  const hot = q => q>=TCHP_THRESHOLD ? ' class="hot"' : '';
  ui.trackTable.innerHTML = real
    ? `<caption>Winds in kt; heat potential in kJ/cm²</caption>`+
      `<tr><th>Time (UTC)</th><th>Position</th><th>IMD</th><th>JTWC</th>`+
      `<th>${fmt.dayMonth(META.times[series.before])}</th><th>${fmt.dayMonth(META.times[series.after])}</th></tr>`+
      T.map((p,k)=>`<tr><td>${fmt.fixTime(p.t)}${p.landfall?', landfall':''}</td><td>${fmt.latLon1(p.lat, p.lon)}</td>`+
        `<td class="wind">${p.nd==null?'—':fmt.esc(p.nd)}</td><td class="wind">${p.usa==null?'—':fmt.esc(p.usa)}</td>`+
        (p.landfall ? `<td>coast</td><td>coast</td></tr>` :
        `<td${hot(series.qb[k].q)}>${series.qb[k].q.toFixed(0)}</td><td>${series.qa[k].q.toFixed(0)}</td></tr>`)).join('')
    : `<tr><th>Hour</th><th>Position</th><th>Heat potential</th><th>D26</th></tr>`+
      T.map((p,k)=>`<tr><td>+${p.h} h</td><td>${fmt.latLon1(p.lat, p.lon)}</td>`+
        `<td${hot(series.qs[k].q)}>${series.qs[k].q.toFixed(0)}</td>`+
        `<td>${series.qs[k].d26===null?'—':series.qs[k].d26.toFixed(0)+' m'}</td></tr>`).join('');
}
