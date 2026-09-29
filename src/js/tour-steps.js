/* ---- guided tour: the steps and their captions ----
   Captions are built from the runtime facts (facts.js) and formatters (fmt.js), so no data-derived number is
   typed here. Each is at most 35 words and keeps its qualifiers ("in HYCOM", "mostly", "consistency check");
   detail lives in the panel the step opens. Literal numbers are definitions (26 °C, 50 kJ/cm², 300 m,
   1000 m) or grid constants. Until facts exist, real-mode steps show a caption without numbers.
   tour.js reads title, caption and time as plain values: they are getters for the current data mode. */
const TOUR_DEFAULT_VIEW = 'surface';   // decision f: opaque surface, faint layers below
const TOUR_STEP_DEFS = [
  {id:'temperature', variable:'temp', mode:TOUR_DEFAULT_VIEW, time:0,
   title:{real:'Temperature', synth:'Temperature (synthetic)'},
   caption:{
     real:(f,fmt,t)=>`HYCOM analysis, ${fmt.dayMonth(f.analysis.times[t])}. The Bay of Bengal surface is mostly above ` +
       `${fmt.int(Math.floor(f.bay.sstP25[t]))} °C; the sides of the block show it cooling to about ` +
       `${fmt.int(f.bay.t1000Mean[t])} °C by 1000 m.`,
     plain:'HYCOM analysis: warm surface water above a sharp thermocline. The sides of the block show the whole ' +
       'column down to 1000 m.',
     synth:'Synthetic demo data, not a real ocean state. Warm surface water cools through the thermocline towards ' +
       'cold deep water; the sides of the block show the whole column.'}},

  {id:'slice', variable:'temp', mode:'slice', depthIdx:4, time:0,
   title:{real:'One depth at a time', synth:'One depth at a time'},
   caption:{
     plain:`One model level at a time, here ${DEPTHS[4]} m, with its own colour range. Use the depth slider or the ` +
       `[ and ] keys to step through the ${DEPTHS.length} levels from 0 to 1000 m.`,
     synth:`One level of the synthetic field at a time, here ${DEPTHS[4]} m. Use the depth slider or the [ and ] keys ` +
       `to step through the ${DEPTHS.length} levels.`}},

  {id:'d26', variable:'temp', mode:'iso', isoVal:26, time:0,
   title:{real:'D26, the 26 °C isotherm', synth:'D26, the 26 °C isotherm'},
   caption:{
     real:(f,fmt,t)=>`D26, the 26 °C isotherm: water above it can feed a cyclone. In HYCOM it lies at ` +
       `${fmt.int(fmt.roundDown(f.bay.d26P10[t],10))}–${fmt.int(fmt.roundUp(f.bay.d26P90[t],10))} m across most of ` +
       `the Bay. Shelf columns warm to the sea floor stay open.`,
     plain:'D26, the 26 °C isotherm: water above it can feed a cyclone. Shelf columns that are warm to the sea floor ' +
       'stay open rather than being guessed.',
     synth:'D26, the 26 °C isotherm, in the synthetic field: water above it can feed a cyclone. Columns that never ' +
       'cool to 26 °C above the sea floor stay open.'}},

  {id:'currents', variable:'cur', mode:'slice', depthIdx:0, time:0,
   title:{real:'Surface currents', synth:'Surface currents (synthetic)'},
   caption:{
     real:(f,fmt,t)=>`Surface currents on ${fmt.dayMonth(f.analysis.times[t])}: arrows show direction, colour shows ` +
       `speed. In HYCOM the East India Coastal Current runs north along the coast at up to about ` +
       `${fmt.dp(f.currents.eiccMaxNorth[t].v,1)} m/s.`,
     plain:'Surface currents in HYCOM: arrows show direction, colour shows speed. The labels mark where the named ' +
       'currents usually flow.',
     synth:'Synthetic currents: arrows show direction, colour shows speed, and the streaks move along the same field. ' +
       'The labels mark where real currents usually flow; this field does not reproduce them.'}},

  {id:'salinity', variable:'sal', mode:TOUR_DEFAULT_VIEW, time:0,
   title:{real:'Salinity', synth:'Salinity (synthetic)'},
   caption:{
     real:(f,fmt,t)=>`In HYCOM the Bay of Bengal surface is far fresher than the Arabian Sea: about ` +
       `${fmt.int(f.bay.salMedian[t])} against ${fmt.int(f.arabian.salMedian[t])} PSU, fed mainly by the ` +
       `Ganges–Brahmaputra and other rivers. The fresh layer sits on saltier water.`,
     plain:'In HYCOM the Bay of Bengal surface is far fresher than the Arabian Sea, fed mainly by the ' +
       'Ganges–Brahmaputra and other rivers. The fresh layer sits on saltier water.',
     synth:'The synthetic field makes the Bay of Bengal fresher than the Arabian Sea, as river runoff does in the ' +
       'real ocean.'}},

  {id:'heat', variable:'tchp', mode:TOUR_DEFAULT_VIEW, time:f=>f ? f.path.beforeIndex : 3, openTrack:true,
   title:{real:'Heat before Amphan', synth:'Cyclone heat potential (synthetic)'},
   caption:{
     real:(f,fmt,t)=>`${fmt.dayMonth(f.analysis.times[t])}, ${fmt.countWord(Math.round(f.track.leadDays[t]))} days before ` +
       `Amphan's first best-track position. In HYCOM, heat under its future path averaged ` +
       `${fmt.int(f.path.heatBefore)} kJ/cm², well above the 50 kJ/cm² mark. Right panel: before and after.`,
     plain:'Heat stored above 26 °C before Amphan formed. The white contour marks 50 kJ/cm²; the right panel ' +
       'compares heat under the path before and after the storm.',
     synth:'Heat stored in water warmer than 26 °C, with a made-up storm track. The right panel samples the heat ' +
       'along it; the storm is illustrative, not a real cyclone.'}},

  {id:'section', variable:'temp', mode:TOUR_DEFAULT_VIEW, time:f=>f ? f.path.afterIndex : 6, section:'track',
   title:{real:'Amphan’s cold wake', synth:'A vertical section'},
   caption:{
     real:(f,fmt,t)=>`${fmt.dayMonth(f.analysis.times[t])}, ${fmt.countWord(tourDaysAfterLandfall(f, t))} days after ` +
       `landfall, on the genesis-to-landfall line. In HYCOM the surface is about ${fmt.dp(f.section.track.cooling,1)} °C ` +
       `cooler than on ${fmt.dayMonth(f.analysis.times[f.path.beforeIndex])}, while D26 stays near ` +
       `${fmt.int(fmt.round(f.section.track.d26After,5))} m: a cold wake.`,
     plain:'After landfall, on the genesis-to-landfall line: in HYCOM the surface along the path has cooled while D26 ' +
       'stays at a similar depth, a cold wake.',
     synth:'A section along the sample track, from the surface to the sea floor. The solid white line is D26, the ' +
       'dashed one D20. Drag across the sea, or press S, to cut your own.'}},

  {id:'argo', variable:'temp', mode:TOUR_DEFAULT_VIEW, time:f=>f ? tourOverBay(f) : 5, openFloat:'worst',
   title:{real:'Argo against HYCOM', synth:'Floats against the model (synthetic)'},
   caption:{
     real:(f,fmt,t)=>`${fmt.dayMonth(f.analysis.times[t])}, Amphan over the Bay. Solid: Argo; dashed: HYCOM ` +
       `${f.comparison.matchedInTime ? 'matched in place and time' : 'at the nearest analysis'}. Below 300 m the RMS ` +
       `difference is ${fmt.dp(fmt.roundDown(f.comparison.deepRmse.min,0.05),2)}–` +
       `${fmt.dp(fmt.roundUp(f.comparison.deepRmse.max,0.05),2)} °C. HYCOM assimilates Argo: a consistency check, ` +
       `not independent validation.`,
     plain:'Argo profiles (solid) against HYCOM (dashed) while Amphan was over the Bay. HYCOM assimilates Argo, so ' +
       'this is a consistency check, not independent validation.',
     synth:'Synthetic floats sample a slightly different ocean from the model, with noise and gaps. Solid: observed; ' +
       'dashed: model. One float has a bad salinity sample that the QC check flags.'}},
];

// derived from the landfall time rather than typed: the last analysis before landfall, and whole days since it
function tourLandfall(f){ return Date.parse(f.track && f.track.landfall ? f.track.landfall.time : REAL_TRACK[REAL_TRACK.length-1].t); }
function tourOverBay(f){ const lf = tourLandfall(f); return f.analysis.times.filter(x=>Date.parse(x) <= lf).length-1; }
function tourDaysAfterLandfall(f, t){ return Math.round((Date.parse(f.analysis.times[t]) - tourLandfall(f))/864e5); }
function tourDataMode(){ return (typeof state!=='undefined' && state.dataMode) || (typeof dataMode!=='undefined' ? dataMode : 'real'); }
function tourFacts(){
  const f = typeof facts==='function' ? facts().real : null;
  return f && f.analysis ? f : null;
}
function tourCaption(def){
  const mode = tourDataMode(), f = tourFacts(), c = def.caption;
  if(mode!=='real') return c.synth;
  if(c.real && f && typeof fmt!=='undefined' && fmt.dayMonth){
    try{ return c.real(f, fmt, typeof def.time==='function' ? def.time(f) : def.time); }
    catch(e){ console.warn('tour caption', def.id, e); }
  }
  return c.plain;
}
const tourSteps = TOUR_STEP_DEFS.map(def=>{
  const step = {};
  Object.keys(def).forEach(k=>{ if(!['title','caption','time'].includes(k)) step[k] = def[k]; });
  Object.defineProperties(step, {
    title:  {enumerable:true, get:()=>def.title[tourDataMode()==='real' ? 'real' : 'synth']},
    caption:{enumerable:true, get:()=>tourCaption(def)},
    time:   {enumerable:true, get:()=>typeof def.time==='function' ? def.time(tourFacts()) : def.time},
  });
  return step;
});
