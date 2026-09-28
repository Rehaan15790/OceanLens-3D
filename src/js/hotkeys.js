/* Keyboard shortcuts and the shortcuts card. */
document.getElementById('helpBtn').addEventListener('click', ()=>document.getElementById('helpOverlay').classList.add('show'));
document.getElementById('helpClose').addEventListener('click', ()=>document.getElementById('helpOverlay').classList.remove('show'));
document.getElementById('helpOverlay').addEventListener('click', e=>{ if(e.target.id==='helpOverlay') e.currentTarget.classList.remove('show'); });

function clickPill(sel){ const b=document.querySelector(sel); if(b) b.click(); }
// a mouse click leaves focus on the button, which used to swallow every shortcut until the
// user clicked the 3D view; drop that focus (keyboard activation keeps it, detail === 0)
document.addEventListener('click', e=>{
  const b = e.target.closest('button');
  if(b && e.detail > 0) b.blur();
});
window.addEventListener('keydown', e=>{
  const k = e.key.toLowerCase();
  if(e.target.matches && e.target.matches('input[type=number], input[type=text], select, textarea')) return;
  if(e.target.tagName==='BUTTON' && (k===' ' || k==='enter')) return;   // let a focused button activate
  if(e.target.type==='range' && k.startsWith('arrow')) return;           // arrows move the slider
  if(k==='1') clickPill('#varPills .pill[data-v="temp"]');
  else if(k==='2') clickPill('#varPills .pill[data-v="sal"]');
  else if(k==='3') clickPill('#varPills .pill[data-v="cur"]');
  else if(k==='4') clickPill('#varPills .pill[data-v="tchp"]');
  else if(k==='s') setSectionDraw(!state.sectionDraw);
  else if(k==='l') clickPill('#modePills .pill[data-m="layers"]');
  else if(k==='d') clickPill('#modePills .pill[data-m="slice"]');
  else if(k==='i') clickPill('#modePills .pill[data-m="iso"]');
  else if(k==='p'){ e.preventDefault(); document.getElementById('presentBtn').click(); }
  else if(k==='r') resetView();
  else if(k===' '){ e.preventDefault(); document.getElementById('playBtn').click(); }
  else if((k==='['||k===']') && state.variable!=='tchp'){
    if(state.mode!=='slice') clickPill('#modePills .pill[data-m="slice"]');
    const dir = k===']' ? 1 : -1;
    const next = Math.max(0, Math.min(DEPTHS.length-1, state.depthIdx+dir));
    const sl = document.getElementById('depthSlider');
    sl.value = next; sl.dispatchEvent(new Event('input'));
  }
  else if(k==='escape'){
    document.getElementById('helpOverlay').classList.remove('show');
    document.getElementById('aboutOverlay').classList.remove('show');
    if(state.sectionDraw){ setSectionDraw(false); sectionDrag = null; }
    else if(profileState.open || trackState.open) closeSidePanels();
    else if(section) clearSection();
  }
});
