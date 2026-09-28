/* ============================================================
   PROFILE PANEL
   ============================================================ */

const profileState = {open:false, current:null, inst:null, tab:'temp'};
const trackState = {open:false};
const fmtSigned = (v, dp) => (v>=0?'+':'−')+Math.abs(v).toFixed(dp);
const VAR_DP = {temp:2, sal:2, dens:2, chl:2};
// RMSE above which a profile's misfit is highlighted (per variable)
const RMSE_WARN = {temp:0.5, sal:0.15, dens:0.15, chl:0.1};

function showPanel(which){   // 'empty' | 'profile' | 'track' | 'section'
  profileState.open = which==='profile'; trackState.open = which==='track'; sectionState.open = which==='section';
  document.getElementById('profile').classList.toggle('show', profileState.open);
  document.getElementById('trackPanel').classList.toggle('show', trackState.open);
  document.getElementById('sectionPanel').classList.toggle('show', sectionState.open);
  document.getElementById('rightEmpty').style.display = which==='empty' ? 'block' : 'none';
}
// closing a profile or the track goes back to the section if one is drawn, else to the summary
function closeSidePanels(){
  if(section){ showPanel('section'); drawSectionPanel(); }
  else showPanel('empty');
}
function worstArgo(){
  let worst = null, top = -1;
  instrumentsNow().filter(i=>i.kind==='argo').forEach(inst=>{
    const s = summarize(misfit(inst, state.time, 'temp'));
    if(s && s.rmse > top){ top = s.rmse; worst = inst; }
  });
  return worst;
}

/* ---- regional validation summary: shown when no instrument is selected ---- */
let validationFor = null;
function renderValidation(){
  const key = dataMode+state.time;
  if(validationFor===key) return;   // depends on the source and time step only
  validationFor = key;
  const t = state.time, bands = [[0,100],[100,300],[300,1000]], insts = instrumentsNow();
  const table = (v, title) => {
    const all = insts.flatMap(inst=>misfit(inst, t, v));
    const rows = bands.map(([z0,z1])=>{
      const s = summarize(all.filter(o=>o.z>z0 && o.z<=z1));
      return `<tr><td>${z0}–${z1} m</td><td>${s?fmtSigned(s.bias,VAR_DP[v]):'—'}</td>`+
        `<td>${s?s.rmse.toFixed(VAR_DP[v]):'—'}</td><td>${s?s.n:0}</td></tr>`;
    }).join('');
    return `<table class="val-table"><caption>${title}</caption>`+
      `<tr><th>depth</th><th>bias</th><th>rmse</th><th>levels</th></tr>${rows}</table>`;
  };
  const ranked = insts.map(inst=>({inst, s:summarize(misfit(inst, t, 'temp'))}))
    .filter(o=>o.s).sort((a,b)=>b.s.rmse-a.s.rmse).slice(0,4);
  const flagged = insts.filter(inst=>observeProfile(inst, t).flag.some(f=>f));
  const nArgo = insts.filter(i=>i.kind==='argo').length, real = dataMode==='real';
  const el = document.getElementById('valPanel');
  el.innerHTML = `
    <div class="val-h">Validation summary</div>
    <div class="val-sub">${timeLabel(t)} · ${nArgo} ${real ? 'real Argo profiles' : 'Argo + '+(insts.length-nArgo)+' glider profiles'}</div>
    ${table('temp', 'TEMPERATURE °C · OBS − MODEL')}
    ${table('sal', 'SALINITY PSU · OBS − MODEL')}
    <div class="grp-label">LARGEST TEMPERATURE MISFIT</div>
    ${ranked.map(o=>`<button class="misfit" data-k="${insts.indexOf(o.inst)}">${o.inst.id}`+
      `<span>${o.s.rmse.toFixed(2)} °C<small>rmse</small></span></button>`).join('')}
    <div class="val-foot">QC: ${flagged.length
      ? `density inversion flagged in ${flagged.map(i=>i.id).join(', ')}; bad levels left out of the statistics.`
      : 'no density inversions.'}</div>
    <div class="val-foot">${real
      ? `Real Argo profiles (GDAC, QC flags applied) against HYCOM at the same snapshot, interpolated to each
         profile's position and depth (GODAE Class 4 style). HYCOM assimilates Argo, so this checks
         consistency rather than validating independently.`
      : `The model is interpolated to each observation's position and depth (GODAE Class 4 style).
         Observations in synthetic mode are synthetic — see Data &amp; Sources.`}</div>`;
  el.querySelectorAll('.misfit').forEach(b=>{
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
  const mT = modelAt('temp',lat,lon,d,t), mS = modelAt('sal',lat,lon,d,t), mC = modelAt('chl',lat,lon,d,t);
  const why = below ? 'below profile' : 'gap';
  const f = (v,dp) => v===null ? null : v.toFixed(dp);
  const card = (color, label, obs, model, unit, note) => `
    <div class="deriv"><div class="lb"><i style="background:${color}"></i>${label}</div>
      <div class="vl${obs===null?' na':''}">${obs===null?'—':obs}<small>${obs==='none'?'':unit}</small></div>
      <div class="md">${obs===null && note ? note+' · ' : ''}model ${model===null?'—':model}</div></div>`;
  const oOK = oT!==null && oS!==null;
  const seabed = bathyDepth(lat, lon);
  const oH = obsTchp(inst, p, seabed), mH = modelTchp(lat, lon, t, seabed);
  // D26: 'none' when the surface is already below 26 °C, ≥seabed when the column is warm to the floor
  const d26 = r => r.q===null ? null : r.q===0 ? 'none' : r.d26===null ? '≥'+Math.round(Math.min(seabed, inst.maxDepth)) : r.d26.toFixed(0);
  document.getElementById('profDepthVal').textContent = d+' m';
  document.getElementById('derivGrid').innerHTML =
    card('#ff8058', 'TEMPERATURE', f(oT,2), f(mT,2), '°C', why) +
    card('#2fe0c4', 'SALINITY', f(oS,2), f(mS,2), 'PSU', why) +
    card('#c9a0ff', 'DENSITY σ<sub>t</sub>', oOK ? sigmaT(oS,oT).toFixed(2) : null, sigmaT(mS,mT).toFixed(2), 'kg/m³', why) +
    card('#ffcf5c', 'SOUND SPEED', oOK ? soundSpeed(oT,oS,d).toFixed(0) : null, soundSpeed(mT,mS,d).toFixed(0), 'm/s', why) +
    card('#8fd15b', 'CHLOROPHYLL', f(oC,2), f(mC,2), 'mg/m³', inst.bgc ? why : 'no sensor') +
    `<div class="deriv"><div class="lb"><i style="background:#8fa8c0"></i>PRESSURE</div>
      <div class="vl">${pressureDbar(d).toFixed(0)}<small>dbar</small></div><div class="md">from depth</div></div>` +
    card('#ff5c9a', 'D26 · 26 °C DEPTH', d26(oH), d26(mH), 'm', 'gap at D26') +
    card('#ff9a3c', 'CYCLONE HEAT', f(oH.q,0), f(mH.q,0), 'kJ/cm²', 'gap at D26');

  // QC summary for this profile
  const flagged = p.z.filter((z,k)=>p.flag[k]);
  const nMissing = p.missing.reduce((a,b)=>a+b, 0);
  document.getElementById('qcBadge').innerHTML = flagged.length
    ? `<div class="qc flag">QC · density inversion at ${flagged.join(', ')} m<br>flagged level left out of bias / RMSE</div>`
    : `<div class="qc pass">QC · no density inversions${nMissing ? ` · ${nMissing} missing levels, shown as gaps` : ''}</div>`;
}

function openProfile(userData){
  const inst = userData.type==='argo' ? userData.data.inst : userData.wp.inst;
  showPanel('profile');
  profileState.current = userData;
  profileState.inst = inst;
  profileState.tab = 'temp';
  if(profileState.depthIdx===undefined) profileState.depthIdx = state.mode==='slice' ? state.depthIdx : 4;
  document.querySelectorAll('.tabbtn').forEach(b=>b.classList.toggle('active', b.dataset.p==='temp'));
  const seabed = Math.round(bathyDepth(inst.lat, inst.lon));
  const where = `<span style="color:var(--accent)">${regionName(inst.lat, inst.lon)}</span><br>`+
    `lat ${inst.lat.toFixed(3)}°N · lon ${inst.lon.toFixed(3)}°E<br>`;
  const sampling = `profile 5–${inst.maxDepth} m · ${inst.levels.length} levels · seabed ${seabed} m`;
  if(userData.type==='argo'){
    document.getElementById('profName').textContent = userData.data.id;
    document.getElementById('profKind').textContent = inst.bgc ? 'BGC-ARGO FLOAT' : 'ARGO FLOAT';
    document.getElementById('profKind').className = 'kind kind-argo';
    document.getElementById('profMeta').innerHTML = where + (inst.real
      ? `cycle #${inst.cycle} · ${inst.time.replace('T',' ').replace('Z',' UTC')}, ${analysisGap(inst)}<br>${ARGO_MODE[inst.mode]||inst.mode} · ${sampling}`
      : `cycle #${userData.data.cycle} · last surfacing ${userData.data.lastSurfacing}<br>${sampling}`);
  } else {
    document.getElementById('profName').textContent = userData.data.id;
    document.getElementById('profKind').textContent = 'GLIDER WAYPOINT '+(userData.wpIndex+1);
    document.getElementById('profKind').className = 'kind kind-glider';
    document.getElementById('profMeta').innerHTML = where + `${userData.wp.time}<br>${sampling}`;
  }
  document.getElementById('profDepth').value = profileState.depthIdx;
  renderDerived();
  drawProfileChart();
}
document.getElementById('profDepth').addEventListener('input', e=>{
  profileState.depthIdx = parseInt(e.target.value);
  renderDerived();
  drawProfileChart();
});
document.getElementById('closeProf').addEventListener('click', closeSidePanels);
document.getElementById('closeTrack').addEventListener('click', closeSidePanels);
document.querySelectorAll('.tabbtn').forEach(btn=>{
  btn.addEventListener('click', ()=>{
    document.querySelectorAll('.tabbtn').forEach(b=>b.classList.remove('active'));
    btn.classList.add('active');
    profileState.tab = btn.dataset.p;
    drawProfileChart();
  });
});

/* ---- sample cyclone track: ocean heat along the path ---- */
function openTrack(){
  showPanel('track');
  renderTrack();
}
function renderTrack(){
  const real = dataMode==='real', T = currentTrack();
  document.getElementById('trackName').textContent = real ? 'Cyclone Amphan' : 'Sample cyclone track';
  document.getElementById('trackKind').textContent = real ? 'REAL · IBTRACS V04R01' : 'ILLUSTRATIVE · NOT A REAL STORM';
  const heat = t => T.map(p=>modelTchp(p.lat, p.lon, t, bathyDepth(p.lat, p.lon)));
  const tm = VAR_META.tchp, heatCol = q=>{ const c = paletteColor('heat', (q-tm.defMin)/(tm.defMax-tm.defMin)); return `rgb(${c[0]*255},${c[1]*255},${c[2]*255})`; };
  const {ctx, w, h} = hidpiCanvas(document.getElementById('trackChart'), 190);
  const pad = {l:32, r:real ? 30 : 10, t:22, b:22}, pw = w-pad.l-pad.r, ph = h-pad.t-pad.b;
  const fmtDay = iso => { const d = new Date(iso); return d.toLocaleDateString('en-GB',{day:'2-digit',month:'short'})+' '+String(d.getUTCHours()).padStart(2,'0')+'Z'; };
  let series, vMax;
  if(real){
    // ocean heat under the path before the storm and after it: the difference is its cold wake
    const times = REAL.meta.times.map(Date.parse), t0 = Date.parse(T[0].t), t1 = Date.parse(T[T.length-1].t);
    const before = Math.max(0, times.filter(x=>x <= t0-12*3.6e6).length-1);
    const after = Math.max(before+1, times.findIndex(x=>x >= t1+12*3.6e6));
    const qb = heat(before), qa = heat(after);
    const mean = a=>a.reduce((x,r)=>x+r.q, 0)/a.length;
    const peak = T.reduce((a,b)=>(b.usa||0)>(a.usa||0)?b:a);
    document.getElementById('trackMeta').innerHTML =
      `${fmtDay(T[0].t)} → landfall ${fmtDay(T[T.length-1].t)} near ${T[T.length-1].lat.toFixed(1)}°N ${T[T.length-1].lon.toFixed(1)}°E<br>`+
      `peak ${peak.usa} kt (JTWC) on ${fmtDay(peak.t)}<br>`+
      `<span style="color:var(--accent)">heat under the path: ${mean(qb).toFixed(0)} kJ/cm² on ${fmtDay(REAL.meta.times[before])}, `+
      `${mean(qa).toFixed(0)} on ${fmtDay(REAL.meta.times[after])}</span>`;
    series = {qb, qa, before, after};
    vMax = Math.max(TCHP_THRESHOLD*1.6, ...qb.map(r=>r.q), ...qa.map(r=>r.q))*1.08;
  } else {
    const qs = heat(state.time);
    const over = qs.filter(r=>r.q>=TCHP_THRESHOLD).length;
    let pk = 0; qs.forEach((r,k)=>{ if(r.q > qs[pk].q) pk = k; });
    document.getElementById('trackMeta').innerHTML =
      `Bay of Bengal → Andhra coast · 12-hourly · ${T[T.length-1].h} h<br>`+
      `ocean state: ${timeLabel(state.time)}<br>`+
      `<span style="color:var(--accent)">${over} of ${T.length} positions ≥ ${TCHP_THRESHOLD} kJ/cm² · peak ${qs[pk].q.toFixed(0)} at +${T[pk].h} h</span>`;
    series = {qs};
    vMax = Math.max(TCHP_THRESHOLD*1.6, ...qs.map(r=>r.q))*1.08;
  }
  const y = v => pad.t + ph - v/vMax*ph;
  ctx.font = CHART_FONT;
  ctx.fillStyle = '#6e8ba0';
  ctx.fillText(real ? 'TCHP · kJ/cm²  ■ before  □ after' : 'TCHP along track · kJ/cm²', pad.l, 12);
  ctx.strokeStyle = '#1b3247'; ctx.lineWidth = 1; ctx.fillStyle = '#4b6577';
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
      ctx.strokeStyle = '#dcebf1'; ctx.lineWidth = 1; ctx.strokeRect(x+slot*0.5+0.5, y(qa)+0.5, bw-1, y(0)-y(qa)-1);
    } else {
      const bw = slot*0.62, q = series.qs[k].q;
      ctx.fillStyle = heatCol(q); ctx.fillRect(x+(slot-bw)/2, y(q), bw, y(0)-y(q));
    }
    const every = real ? 4 : 2;
    if(k%every===0){
      ctx.fillStyle = '#6e8ba0'; ctx.textAlign = 'center';
      ctx.fillText(real ? new Date(p.t).getUTCDate()+'' : '+'+p.h+'h', x+slot/2, h-7);
      ctx.textAlign = 'left';
    }
  });
  ctx.strokeStyle = '#ffffff'; ctx.setLineDash([4,3]);
  ctx.beginPath(); ctx.moveTo(pad.l, y(TCHP_THRESHOLD)); ctx.lineTo(w-pad.r, y(TCHP_THRESHOLD)); ctx.stroke();
  ctx.setLineDash([]);
  if(real){
    // JTWC wind on its own axis: the intensification lines up with the heat underneath
    const yw = v => pad.t + ph - v/160*ph;
    ctx.strokeStyle = '#ffcf5c'; ctx.lineWidth = 1.6; ctx.beginPath();
    let pen = false;
    T.forEach((p,k)=>{ if(p.usa==null){ pen = false; return; } const x = pad.l+(k+0.5)*slot; if(pen) ctx.lineTo(x, yw(p.usa)); else { ctx.moveTo(x, yw(p.usa)); pen = true; } });
    ctx.stroke();
    ctx.fillStyle = '#ffcf5c'; ctx.textAlign = 'left';
    [0,80,160].forEach(v=>ctx.fillText(String(v), w-pad.r+3, yw(v)+3));
    ctx.fillText('kt', w-pad.r+3, pad.t-8);
  }

  document.getElementById('trackTable').innerHTML = real
    ? `<tr><th>time</th><th>position</th><th>wind</th><th>before</th><th>after</th></tr>`+
      T.map((p,k)=>`<tr><td>${fmtDay(p.t)}${p.landfall?' ⚑':''}</td><td>${p.lat.toFixed(1)}°N ${p.lon.toFixed(1)}°E</td>`+
        `<td style="color:#ffcf5c">${p.usa==null?'—':p.usa+' kt'}</td>`+
        `<td style="color:${series.qb[k].q>=TCHP_THRESHOLD?'var(--warn)':'var(--text)'}">${series.qb[k].q.toFixed(0)}</td>`+
        `<td>${series.qa[k].q.toFixed(0)}</td></tr>`).join('')
    : `<tr><th>time</th><th>position</th><th>TCHP</th><th>D26</th></tr>`+
      T.map((p,k)=>`<tr><td>+${p.h} h</td><td>${p.lat.toFixed(1)}°N ${p.lon.toFixed(1)}°E</td>`+
        `<td style="color:${series.qs[k].q>=TCHP_THRESHOLD?'var(--warn)':'var(--text)'}">${series.qs[k].q.toFixed(0)}</td>`+
        `<td>${series.qs[k].d26===null?'—':series.qs[k].d26.toFixed(0)+' m'}</td></tr>`).join('');
}
