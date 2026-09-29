/* Guided tour: runs the steps defined in tour-steps.js.
   Manual by default (Back / Next / Stop); Autoplay advances after a reading time based on the
   caption's length. Starting the tour pauses playback and remembers the view; Stop, Esc and
   Finish put it back. The delayed parts of a step (a section, a float, the track) carry the
   step's token and do nothing once another step or a stop has replaced it. */
const tour = {running:false, idx:0, token:0, autoplay:false, timer:null, saved:null};
let tourRotating = false;   // the camera may turn slowly during this step (read by the render loop)
let tourPaused = false;     // autoplay was switched off part-way (read by the render loop)
const TOUR_KEYS = ['variable', 'mode', 'depthIdx', 'isoVal', 'opacity', 'exagg', 'cbMin', 'cbMax', 'palette', 'time',
                   'colorMisfit', 'showArgo', 'showGlider', 'showGrid', 'showFlow', 'showTrack', 'playing'];

function isTourRunning(){ return tour.running; }

function tourLater(fn, ms){
  const token = tour.token;
  setTimeout(()=>{ if(tour.running && token===tour.token) fn(); }, ms);
}
// reading time for autoplay: about 3.5 words a second plus 3 s to look at the view, at least 8 s
function readingTime(text){
  const words = String(text).trim().split(/\s+/).length;
  return Math.max(8000, (words/3.5 + 3)*1000);
}

function snapshotApp(){
  const s = {};
  TOUR_KEYS.forEach(k=>{ s[k] = state[k]; });
  return {state:s, camera:{az:camAz, el:camEl, r:camR, target:camTarget.clone()},
          section: section ? {a:section.a.slice(), b:section.b.slice(), name:section.name} : null,
          panel: state.panel, profile: profileState.current};
}
function restoreApp(saved){
  const {playing, ...rest} = saved.state;
  Object.assign(state, rest);
  section = saved.section;
  buildSection();
  camTween = null;
  camAz = saved.camera.az; camEl = saved.camera.el; camR = saved.camera.r; camTarget.copy(saved.camera.target);
  updateCamera();
  requestUpdate('full');
  requestUpdate('overlay');
  flushUpdate();
  if(saved.panel==='profile' && saved.profile) openProfile(saved.profile);
  else if(saved.panel==='track') openTrack();
  else closeSidePanels();
  if(playing) setPlaying(true);
}

function applyTourStep(){
  const raw = tourSteps[tour.idx];
  // legacy steps carry real-mode overrides; ux's steps use getters, which the spread evaluates now
  const step = dataMode==='real' && raw.real ? {...raw, ...raw.real} : {...raw};
  tour.token++;
  clearTimeout(tour.timer);
  section = null;           // each step starts without a section; a step can ask for one
  buildSection();
  if(!step.section) homeCamera();
  setSectionDraw(false);
  state.variable = step.variable;
  resetRange();
  state.colorMisfit = !!step.openFloat;
  if(step.mode) state.mode = step.mode;
  if(step.depthIdx!==undefined) state.depthIdx = step.depthIdx;
  if(step.time!==undefined) state.time = step.time;
  if(ISO_META[state.variable]) state.isoVal = step.isoVal!==undefined ? step.isoVal : ISO_META[state.variable].def;
  requestUpdate('full');
  flushUpdate();
  showPanel('empty');

  const n = tourSteps.length;
  ui.tourStep.textContent = `Step ${tour.idx+1} of ${n} · ${step.title}`;
  ui.tourText.textContent = step.caption;
  ui.tourBack.disabled = tour.idx===0;
  ui.tourNext.textContent = tour.idx===n-1 ? 'Finish' : 'Next';
  tourRotating = !step.section;   // hold the camera still while it faces a section
  if(step.section) tourLater(()=>showSectionPreset(step.section), 900);
  // open the float that disagrees most with the model, so the comparison has something to show
  if(step.openFloat) tourLater(()=>{ if(openWorstFloat()) document.getElementById('profChart')?.scrollIntoView({block:'nearest'}); }, 1400);
  if(step.openTrack) tourLater(openTrack, 1400);
  if(tour.autoplay && !tourPaused) tour.timer = setTimeout(()=>nextTourStep(true), readingTime(step.caption));
}

/* ---- actions ---- */
function startTour(){
  if(tour.running) return;
  setSectionDraw(false);
  tour.saved = snapshotApp();
  setPlaying(false);
  tour.running = true; tour.idx = 0; tour.autoplay = false;
  tourPaused = false;
  ui.tourStrip.hidden = false;
  ui.tourPaused.hidden = true;
  setPressed(ui.tourAuto, false);
  setPressed(ui.presentBtn, true);
  applyTourStep();
}
// restore: put back the view from before the tour (Stop, Esc, Finish); a control the user
// touches during the tour stops it without undoing their change
function stopTour({restore = false} = {}){
  if(!tour.running) return;
  tour.running = false;
  tour.token++;
  clearTimeout(tour.timer);
  tourRotating = false; tourPaused = false;
  ui.tourStrip.hidden = true;
  setPressed(ui.presentBtn, false);
  const saved = tour.saved;
  tour.saved = null;
  if(restore && saved) restoreApp(saved);
}
function goToTourStep(i){
  if(!tour.running) return;
  tour.idx = Math.max(0, Math.min(tourSteps.length-1, i));
  applyTourStep();
}
function nextTourStep(fromAutoplay){
  if(tour.idx < tourSteps.length-1) return goToTourStep(tour.idx+1);
  if(fromAutoplay){ setTourAutoplay(false); return; }   // autoplay stops on the last step
  stopTour({restore:true});                            // "Finish"
}
function setTourAutoplay(on){
  tour.autoplay = !!on;
  tourPaused = false;
  setPressed(ui.tourAuto, tour.autoplay);
  ui.tourPaused.hidden = true;
  clearTimeout(tour.timer);
  if(tour.running && tour.autoplay) tour.timer = setTimeout(()=>nextTourStep(true), readingTime(ui.tourText.textContent));
}
function pauseTourAutoplay(){
  clearTimeout(tour.timer);
  tourPaused = true;
  ui.tourPaused.hidden = false;
}

ui.presentBtn.addEventListener('click', ()=>{ if(tour.running) stopTour({restore:true}); else startTour(); });
ui.tourBack.addEventListener('click', ()=>goToTourStep(tour.idx-1));
ui.tourNext.addEventListener('click', ()=>nextTourStep(false));
ui.tourStop.addEventListener('click', ()=>stopTour({restore:true}));
ui.tourAuto.addEventListener('click', ()=>{
  if(!tour.autoplay || tourPaused) setTourAutoplay(true);
  else pauseTourAutoplay();
});
window.addEventListener('keydown', e=>{
  if(!tour.running || e.ctrlKey || e.metaKey || e.altKey) return;
  if(e.target.matches && e.target.matches('input, select, textarea')) return;
  const k = e.key;
  if(k==='ArrowRight'){ nextTourStep(false); }
  else if(k==='ArrowLeft'){ goToTourStep(tour.idx-1); }
  else if(k==='Escape'){ stopTour({restore:true}); }
  else return;
  e.preventDefault();
  e.stopImmediatePropagation();   // the global shortcuts must not act on the same key
});
// orbiting by hand stops the slow rotation for this step; changing a control ends the tour
renderer.domElement.addEventListener('pointerdown', ()=>{ tourRotating = false; });
document.querySelectorAll('#varPills .pill, #modePills .pill, #dataToggle button').forEach(b=>{
  b.addEventListener('click', ()=>stopTour());
});
