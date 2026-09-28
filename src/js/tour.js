/* Guided tour: runs the steps defined in tour-steps.js. */
let tourIdx = 0, tourTimer = null, tourRotating = false, tourPaused = false;
const TOUR_STEP_MS = 9500;
function setActivePill(container, attr, val){
  document.querySelectorAll(container+' .pill').forEach(b=>{
    b.classList.toggle('active', b.dataset[attr]===val);
  });
}
function buildTourDots(){
  const dotsEl = document.getElementById('tourDots');
  dotsEl.innerHTML = '';
  tourSteps.forEach((_,i)=>{
    const d = document.createElement('div');
    d.className = 'd'+(i===tourIdx?' active':'');
    d.addEventListener('click', ()=>goToTourStep(i));
    dotsEl.appendChild(d);
  });
}
function applyTourStep(step){
  if(dataMode==='real' && step.real) step = Object.assign({}, step, step.real);
  section = null;          // each step starts without a section; a step can ask for one
  buildSection();
  if(!step.section) homeCamera();
  setSectionDraw(false);
  state.variable = step.variable;
  const meta = VAR_META[state.variable];
  state.cbMin = meta.defMin; state.cbMax = meta.defMax; state.palette = meta.palette;
  document.getElementById('cbMin').value = meta.defMin;
  document.getElementById('cbMax').value = meta.defMax;
  document.getElementById('paletteSel').value = meta.palette;
  setActivePill('#varPills','v', step.variable);
  state.colorMisfit = !!step.openFloat;
  document.getElementById('swMisfit').classList.toggle('on', state.colorMisfit);

  state.mode = step.mode;
  setActivePill('#modePills','m', step.mode);
  document.getElementById('depthGrp').style.display = step.mode==='slice' ? 'block' : 'none';
  document.getElementById('isoGrp').style.display = step.mode==='iso' ? 'block' : 'none';

  if(step.depthIdx!==undefined){
    state.depthIdx = step.depthIdx;
    document.getElementById('depthSlider').value = step.depthIdx;
    document.getElementById('depthVal').textContent = DEPTHS[step.depthIdx]+' m';
  }
  if(step.time!==undefined) setTime(step.time);
  if(ISO_META[state.variable]) state.isoVal = step.isoVal!==undefined ? step.isoVal : ISO_META[state.variable].def;
  syncIsoSlider();
  fullUpdate();

  const cap = document.getElementById('tourCaption');
  cap.classList.remove('visible');
  setTimeout(()=>{
    cap.innerHTML = `<span class="step">STEP ${tourIdx+1} / ${tourSteps.length} · ${step.title.toUpperCase()}</span>`+
      `<span class="body">${step.caption}</span>`+
      `<span class="navhint">← → to navigate · hover to pause · click to stop</span>`;
    cap.classList.add('show');
    requestAnimationFrame(()=>cap.classList.add('visible'));
  }, 200);
  buildTourDots();

  showPanel('empty');
  tourRotating = !step.section;   // hold the camera still while it faces a section
  if(step.section) setTimeout(()=>showSectionPreset(step.section), 900);
  // open the float that disagrees most with the model, so the comparison has something to show
  if(step.openFloat) setTimeout(()=>openProfile(worstArgo().userData), 1400);
  if(step.openTrack) setTimeout(openTrack, 1400);
}
function goToTourStep(i){
  tourIdx = ((i % tourSteps.length) + tourSteps.length) % tourSteps.length;
  applyTourStep(tourSteps[tourIdx]);
  restartTourTimer();
}
function restartTourTimer(){
  clearInterval(tourTimer);
  tourTimer = setInterval(()=>{ if(!tourPaused) goToTourStep(tourIdx+1); }, TOUR_STEP_MS);
}
function stopTour(){
  tourRotating = false; tourPaused = false;
  clearInterval(tourTimer); tourTimer = null;
  document.getElementById('presentBtn').classList.remove('active');
  document.getElementById('presentBtn').textContent = '▶ PRESENT MODE';
  document.getElementById('tourCaption').classList.remove('show','visible');
  document.getElementById('tourDots').classList.remove('show');
}
function startTour(){
  tourIdx = 0; tourRotating = true; tourPaused = false;
  document.getElementById('presentBtn').classList.add('active');
  document.getElementById('presentBtn').textContent = '■ STOP';
  document.getElementById('tourDots').classList.add('show');
  applyTourStep(tourSteps[0]);
  restartTourTimer();
}
document.getElementById('presentBtn').addEventListener('click', ()=>{
  if(tourTimer) stopTour(); else startTour();
});
document.getElementById('tourCaption').addEventListener('mouseenter', ()=>{ tourPaused = true; });
document.getElementById('tourCaption').addEventListener('mouseleave', ()=>{ tourPaused = false; });
window.addEventListener('keydown', e=>{
  if(!tourTimer) return;
  if(e.key==='ArrowRight'){ e.preventDefault(); goToTourStep(tourIdx+1); }
  if(e.key==='ArrowLeft'){ e.preventDefault(); goToTourStep(tourIdx-1); }
  if(e.key==='Escape'){ stopTour(); }
});
// any manual interaction on the 3D view or side controls ends the guided tour
renderer.domElement.addEventListener('pointerdown', ()=>{ if(tourTimer) stopTour(); });
document.querySelectorAll('#varPills .pill, #modePills .pill').forEach(b=>{
  b.addEventListener('click', ()=>{ if(tourTimer) stopTour(); });
});
