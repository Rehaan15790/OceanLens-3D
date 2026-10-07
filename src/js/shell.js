/* ---- layout chrome: collapsible side panels, the small-screen notice, dialog focus, facts in markup ----
   Changes no app state; it shows, hides and focuses chrome, fills [data-fact] / [data-cite] elements, and
   forwards a few buttons to the app's own functions. */

const shell = (()=>{
  const app = document.getElementById('app');
  const NARROW = 1100, SMALL = 900;       // CSS px: right panel starts collapsed below NARROW; notice below SMALL

  function setPanel(side, open){
    app.classList.toggle(side+'-collapsed', !open);
    const btn = document.getElementById(side+'Toggle');
    btn.setAttribute('aria-expanded', String(open));
    const label = side==='left' ? (open ? 'Hide controls' : 'Show controls')
                                : (open ? 'Hide analysis panel' : 'Show analysis panel');
    btn.setAttribute('aria-label', label); btn.title = label;
    // the canvas is sized by a ResizeObserver on #viewport (graphics), so nothing else to call here
  }
  let userToggled = false;
  ['left','right'].forEach(side=>document.getElementById(side+'Toggle')
    .addEventListener('click', ()=>{ userToggled = true; setPanel(side, app.classList.contains(side+'-collapsed')); }));
  let widthTimer = 0;
  window.addEventListener('resize', ()=>{ clearTimeout(widthTimer);
    widthTimer = setTimeout(()=>{ if(!userToggled) applyWidth(); }, 200); });

  // panels.js calls this when a float, the track or a section is opened, so a collapsed panel comes back
  function revealRight(){ if(app.classList.contains('right-collapsed')) setPanel('right', true); }

  function applyWidth(){
    const w = window.innerWidth;
    setPanel('right', w >= NARROW);
    setPanel('left', w >= SMALL);
  }

  // small screens: an honest notice with a way through (owner decision 4)
  let smallDismissed = false;
  function smallScreenCheck(){
    const dlg = document.getElementById('smallScreen');
    try{ if(sessionStorage.getItem('ol-small-ok')==='1') smallDismissed = true; }catch(e){}
    if(window.innerWidth >= SMALL || smallDismissed || !dlg.hidden) return;
    dlg.hidden = false;
    openDialog(dlg.querySelector('.dialog'), ()=>{
      dlg.hidden = true;
      smallDismissed = true;
      try{ sessionStorage.setItem('ol-small-ok','1'); }catch(e){}
    });
  }

  // dialogs: focus moves in on open, Tab stays inside, Esc closes, focus returns to the opener
  let current = null, opener = null, onClose = null;
  const FOCUSABLE = 'button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex="-1"])';
  // dialogs with steps hide whole blocks, so only rendered controls count
  const focusables = el=>[...el.querySelectorAll(FOCUSABLE)].filter(f=>f.getClientRects().length > 0);
  function openDialog(dialogEl, closeFn){
    opener = document.activeElement; current = dialogEl; onClose = closeFn;
    const first = focusables(dialogEl)[0];
    if(!first && !dialogEl.hasAttribute('tabindex')) dialogEl.tabIndex = -1;
    (first || dialogEl).focus();
  }
  function closeDialog(){
    if(!current) return;
    const fn = onClose; current = null; onClose = null;
    if(fn) fn();
    if(opener && document.contains(opener)) opener.focus();
    opener = null;
  }
  document.addEventListener('keydown', e=>{
    if(!current) return;
    if(e.key==='Escape'){ e.preventDefault(); e.stopPropagation(); closeDialog(); return; }
    if(e.key!=='Tab') return;
    const f = focusables(current);
    if(!f.length){ e.preventDefault(); return; }
    const i = f.indexOf(document.activeElement);
    if(e.shiftKey && i<=0){ e.preventDefault(); f[f.length-1].focus(); }
    else if(!e.shiftKey && i===f.length-1){ e.preventDefault(); f[0].focus(); }
  }, true);

  // About and Shortcuts keep their existing .show toggles (controls.js); shell only adds focus handling
  function wireOverlay(overlayId, openBtnId, closeBtnId){
    const ov = document.getElementById(overlayId), card = ov.querySelector('.dialog');
    const close = ()=>ov.classList.remove('show');
    document.getElementById(openBtnId).addEventListener('click', ()=>openDialog(card, close));
    document.getElementById(closeBtnId).addEventListener('click', closeDialog);
    ov.addEventListener('click', e=>{ if(e.target===ov) closeDialog(); });
  }
  wireOverlay('aboutOverlay', 'aboutBtn', 'aboutClose');
  wireOverlay('helpOverlay', 'helpBtn', 'helpClose');

  // [data-fact] and [data-copy]: numbers and mode strings in static markup
  function fillFacts(facts, fmt, copy){
    const get = path=>path.split('.').reduce((o,k)=>o==null ? o : k==='last' ? o[o.length-1] : o[k], facts);
    document.querySelectorAll('[data-fact]').forEach(el=>{
      const v = get(el.dataset.fact), f = fmt[el.dataset.fmt || 'int'];
      el.textContent = v==null ? '' : f ? f(v) : String(v);
    });
    if(copy) document.querySelectorAll('[data-copy]').forEach(el=>{
      const v = copy[el.dataset.copy];
      el.textContent = typeof v==='function' ? v(facts, fmt) : (v || '');
    });
  }


  // buttons that start something the app already does (moves to architect's actions once they exist)
  const on = (id, fn)=>{ const el = document.getElementById(id); if(el) el.addEventListener('click', fn); };
  const leaveSplash = ()=>document.getElementById('enterBtn').click();
  const tour = ()=>startTour();
  on('enterTourBtn', ()=>{ leaveSplash(); tour(); });
  on('startTourBtn', tour);
  on('openTrackBtn', ()=>{ if(state.variable!=='tchp') setVariable('tchp'); openTrack(); revealRight(); });
  on('trackDetailsBtn', ()=>{ openTrack(); revealRight(); });
  on('worstFloatBtn', ()=>{ if(openWorstFloat()) revealRight(); });
  on('smallScreenContinue', ()=>closeDialog());
  on('enterBtn', ()=>{ smallScreenCheck(); window.addEventListener('resize', smallScreenCheck); });

  // citations come with the data (meta.citations, written by the pipeline)
  function fillCitations(){
    const list = REAL_DATA.meta.citations || [];
    document.querySelectorAll('[data-cite]').forEach(el=>{
      const c = list.find(x=>x.id===el.dataset.cite);
      el.textContent = c ? c.text : '';
    });
  }
  fillCitations();
  fillFacts(facts().real, Object.assign({exagDefault:v=>fmt.sig2(v*state.exagg)}, fmt));
  document.querySelectorAll('[data-needs-shell]').forEach(el=>{ el.hidden = false; });

  applyWidth();
  // called when the splash is dismissed (the splash sits above every dialog)
  return {setPanel, revealRight, openDialog, closeDialog, fillFacts, smallScreenCheck};
})();
