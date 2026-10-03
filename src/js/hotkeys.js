/* Keyboard shortcuts and the shortcuts card. */
ui.helpBtn.addEventListener('click', ()=>ui.helpOverlay.classList.add('show'));
ui.helpClose.addEventListener('click', ()=>ui.helpOverlay.classList.remove('show'));
ui.helpOverlay.addEventListener('click', e=>{ if(e.target===ui.helpOverlay) ui.helpOverlay.classList.remove('show'); });

// a mouse click leaves focus on the button, which would swallow the next shortcut; drop that
// focus (keyboard activation keeps it, detail === 0)
document.addEventListener('click', e=>{
  const b = e.target.closest('button');
  if(b && e.detail > 0) b.blur();
});

const VARIABLE_KEYS = {'1':'temp', '2':'sal', '3':'cur', '4':'tchp', '5':'chl'};
const MODE_KEYS = {u:'surface', l:'layers', d:'slice', i:'iso', o:'iso3d'};
window.addEventListener('keydown', e=>{
  if(e.ctrlKey || e.metaKey || e.altKey) return;   // leave browser shortcuts (print, save, reload) alone
  const k = e.key.toLowerCase();
  if(e.target.matches && e.target.matches('input[type=number], input[type=text], select, textarea')) return;
  if(e.target.tagName==='BUTTON' && (k===' ' || k==='enter')) return;   // let a focused button activate
  if(e.target.type==='range' && k.startsWith('arrow')) return;           // arrows move the slider
  if(VARIABLE_KEYS[k]){ stopTour(); setVariable(VARIABLE_KEYS[k]); }
  else if(MODE_KEYS[k] && !is2D(state.variable)){ stopTour(); setMode(MODE_KEYS[k]); }
  else if(k==='s') setSectionDraw(!state.sectionDraw);
  else if(k==='p'){ e.preventDefault(); if(isTourRunning()) stopTour({restore:true}); else startTour(); }
  else if(k==='r') resetView();
  else if(k===' '){ e.preventDefault(); setPlaying(!state.playing); }
  else if(k==='?') ui.helpOverlay.classList.add('show');
  else if((k==='[' || k===']') && !is2D(state.variable)){
    stopTour();
    setMode('slice');
    setDepth(state.depthIdx + (k===']' ? 1 : -1));
  }
  else if(k==='escape'){
    ui.helpOverlay.classList.remove('show');
    ui.aboutOverlay.classList.remove('show');
    if(state.sectionDraw){ setSectionDraw(false); sectionDrag = null; }
    else if(state.panel==='profile' || state.panel==='track') closeSidePanels();
    else if(section) clearSection();
  }
});
