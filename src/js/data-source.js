/* ============================================================
   DATA SOURCES
   'real'  : the model on screen (HYCOM, or GLORYS: realModel), Argo profiles and the IBTrACS track around
             Cyclone Amphan (4–25 May 2020), from the embedded snapshots (data-real.js)
   'synth' : the analytic demo ocean (data-synthetic.js)
   Everything else reads the ocean, the instruments, the track and every mode-dependent
   string through dataSource(), so switching source is one variable (dataMode).
   ============================================================ */
// for a forecast case: whether day t is before the run (an analysis) or how many days ahead it is
function forecastDay(t){
  if(!CASE.forecast || !Number.isFinite(CASE.issued)) return '';
  const d = Math.round((Date.parse(META.times[t].slice(0, 10) + 'T00:00Z') - CASE.issued)/864e5);
  return d < 0 ? ' (analysis)' : d === 0 ? ' (day of issue)' : ` (+${d} ${d===1 ? 'day' : 'days'})`;
}
const SOURCES = {
  real: {
    temp: (lat, lon, depth, t)=>realAt(0, lat, lon, depth, t),
    sal:  (lat, lon, depth, t)=>realAt(1, lat, lon, depth, t),
    uv:   (lat, lon, depth, t)=>({u:realAt(2, lat, lon, depth, t), v:realAt(3, lat, lon, depth, t)}),
    chl:  ()=>NaN,                      // both models are physics only: no chlorophyll
    instruments: t=>realProfiles.filter(p=>p.snap===t).concat(mooringsAt(t)),   // Argo floats, then RAMA moorings
    fixes: ()=>REAL_TRACK,
    hasGliders: false,

    // ---- copy table (ux edits the strings in 3.1b) ----
    ctx: {badge:'Real data',
          text: t=>`${CASE.name} · ${MODEL().product} ${fmt.analysisTime(META.times[t])}${forecastDay(t)} · ${t+1} of ${NT}`},
    time: {now: t=>fmt.analysisTime(META.times[t]), tick: t=>fmt.dayMonth(META.times[t])},
    track: {get name(){ return CASE.name; }, badge:'IBTrACS v04r01', mapLabel:'Amphan (IBTrACS)',   // CASE: data-real.js, loaded later
            landfallLabel: fix=>`Landfall ${fmt.fixTime(fix.t)}`,
            nowLabel: (wind, agency)=>`Amphan · ${wind} kt (${agency})`,
            empty:'No IBTrACS fixes for Amphan fall inside the map.'},
    section: {trackPreset:"Along Amphan's track", trackName:'Amphan: genesis to landfall'},
    legend: {track:'Amphan track (IBTrACS)', now:'Amphan at this analysis'},
    val: {heading:'Model–Argo comparison',
          sub: (t, nArgo)=>`${fmt.analysisTime(META.times[t])} · ${nArgo} Argo profiles`,
          get foot(){ const F = facts().real;
            const M = MODEL();
            return (M.monthly
              ? `Each profile is compared with the ${M.short} monthly mean of its month, interpolated `
              : F.comparison && F.comparison.matchedInTime
              ? `The model is matched to each profile’s time and place (nearest 3-hourly ${M.short} output) and interpolated `
              : `Each profile is compared with the ${M.product} nearest in time (within 1.5 days), interpolated `) +
              `to its depths, as in GODAE Class 4 verification. ${assimilationWords(false)}`; }},
    profile: {kind: inst=>inst.bgc ? 'BGC-Argo float' : 'Argo float',
              none:(id, t)=>`${id} has no profile within 1.5 days of ${fmt.dayMonth(META.times[t])}.`},
    vars: {get chlNote(){ return `From BGC-Argo floats only. ${MODEL().short} has no chlorophyll, so the map has no model field: click a float to see its profile.`; },
           get chlKey(){ return `${MODEL().short} has no chlorophyll; only BGC-Argo floats carry it.`; }},
    export: {get caveat(){ return CASE.tour ? `Data: ${MODEL().data} · Argo GDAC incl. BGC-Argo · IBTrACS v04r01 · ETOPO1. Not for operational use.`
                                             : `Data: ${MODEL().data} · ETOPO1. Not for operational use.`; },
             file: (t, variable, view)=>`oceanlens3d_real_${META.times[t].slice(0, 10)}_${variable}_${view}.png`}
  },
  synth: {
    temp: (lat, lon, depth, t)=>synthTemp(lat, lon, depth, t),
    sal:  (lat, lon, depth, t)=>synthSal(lat, lon, depth, t),
    uv:   (lat, lon, depth, t)=>synthCurrentUV(lat, lon, depth, t),
    chl:  (lat, lon, depth, t)=>synthChl(lat, lon, depth, t),
    instruments: ()=>synthInstruments,
    fixes: ()=>SAMPLE_TRACK,
    hasGliders: true,

    ctx: {badge:'Synthetic demo', text: t=>`Demo day ${fmt.demoDay(t)} · not a real ocean state`},
    time: {now: t=>`Day ${fmt.demoDay(t)}`, tick: t=>`Day ${fmt.demoDay(t)}`},
    track: {name:'Sample track', badge:'Illustrative, not a real storm', mapLabel:'Sample track (illustrative)',
            landfallLabel: ()=>'Landfall', nowLabel: null,
            empty:'The sample track has no positions.'},
    section: {trackPreset:'Along the sample track', trackName:'Sample track: start to landfall'},
    legend: {track:'Sample track (illustrative)', now:null},
    val: {heading:'Model–observation comparison',
          sub: (t, nArgo, nGlider)=>`Day ${fmt.demoDay(t)} · ${nArgo} synthetic floats and ${nGlider} glider profiles`,
          foot:'Synthetic observations compared with the synthetic model in the same way. They are not real data.'},
    profile: {kind: (inst, wpIndex)=>inst.kind==='glider' ? `Synthetic glider, waypoint ${wpIndex+1}` : 'Synthetic float',
              none:()=>''},
    vars: {chlNote:'Synthetic field generated in the browser, shown on a log scale by default.', chlKey:''},
    export: {caveat:'Synthetic demo data, not a real ocean state. Not for operational use.',
             file: (t, variable, view)=>`oceanlens3d_synthetic_day${fmt.demoDay(t)}_${variable}_${view}.png`}
  }
};
// the active entry of SOURCES: the ocean, the instruments and every mode-dependent string
function dataSource(){ return SOURCES[dataMode]; }

function fieldTemp(lat, lon, depth, t){ return dataSource().temp(lat, lon, depth, t); }
function fieldSal(lat, lon, depth, t){ return dataSource().sal(lat, lon, depth, t); }
function fieldCurrentUV(lat, lon, depth, t){ return dataSource().uv(lat, lon, depth, t); }
function fieldChl(lat, lon, depth, t){ return dataSource().chl(lat, lon, depth, t); }
function currentSpeed(lat,lon,depth,t){const {u,v}=fieldCurrentUV(lat,lon,depth,t);return Math.sqrt(u*u+v*v);}

// any registered variable at a point; a 2D variable ignores the depth
function getVal(varKey, lat, lon, depth, t){
  const v = VAR_META[varKey];
  if(!v) throw new Error(`getVal: no variable ${varKey}`);
  return v.sample(lat, lon, v.dims===2 ? 0 : depth, t);
}
// the profiles on screen: real ones belong to one snapshot, synthetic instruments are fixed;
// imported ones (real mode only) show within ±1.5 days of the analysis, or all with "show all"
function instrumentsNow(){
  const own = dataSource().instruments(state.time);
  return dataMode==='real' && importedInsts.length ? own.concat(importedNow(state.time)) : own;
}
// the storm track of the active source: Amphan (real) or SAMPLE_TRACK (synthetic)
function currentTrack(){ return dataSource().fixes(); }

/* ---- action: switch the data source ---- */
function setDataMode(mode){
  if(mode===dataMode || !SOURCES[mode]) return;
  if(isTourRunning()) stopTour({restore:false});
  dataMode = mode;
  if(!varInMode(state.variable)) state.variable = 'temp';   // satellite products are real data only
  applyModeDefaults();
  resetRange();
  if(ISO_META[state.variable]) state.isoVal = ISO_META[state.variable].def;
  validationFor = null;
  buildTrack();
  if(state.panel==='profile' || state.panel==='track') closeSidePanels();
  requestUpdate('full');
}
ui.dataToggle.querySelectorAll('button').forEach(b=>b.addEventListener('click', ()=>setDataMode(b.dataset.mode)));

/* ---- action: switch the real-data model (HYCOM / GLORYS) ----
   The floats, track and relief stay; the fields, the comparison and every number in captions and panels follow the
   model. An open profile or track panel is redrawn for the new model rather than closed, to compare the two. */
function setRealModel(id){
  const m = REAL_MODELS.find(x=>x.id===id);
  if(!m || id===realModel) return;
  if(isTourRunning()) stopTour({restore:false});
  if(!m.decoded) m.decoded = decodeModel(m.data());
  Object.assign(REAL, m.decoded);
  realModel = id;
  realProfiles.forEach(p=>{ if(p.atObs){ if(m.atObs) p.model = p.atObs; else delete p.model; } });
  datasetRev++;                        // markers, hover readouts and 2D fields key on it
  validationFor = null;
  if(dataMode==='real'){
    if(state.panel==='profile' && profileState.current && profileState.inst){
      const tab = profileState.tab;
      openProfile(profileState.current);
      profileState.tab = tab;
      syncProfileTabs();
      drawProfileChart();
    } else if(state.panel==='track'){
      openTrack();
    }
  }
  requestUpdate('full');
}
// the next model in REAL_MODELS (the M key)
function nextRealModel(){
  const i = REAL_MODELS.findIndex(x=>x.id===realModel);
  return REAL_MODELS[(i+1) % REAL_MODELS.length].id;
}
