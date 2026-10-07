/* ---- Add data: the import dialog, drop-anywhere, the "Your data" list and the server data source ----
   UI only. Reading and adding go through architect's OceanLens.readFile / importParsed / removeDataset;
   focus handling is shell.openDialog / closeDialog. */

const importUI = (()=>{
  const $ = id=>document.getElementById(id);
  const ov = $('importOverlay'), card = $('importCard'), input = $('importInput');
  const pick = $('importPick'), review = $('importReview');
  const statusEl = $('importStatus'), errorEl = $('importError');
  const addBtn = $('importAdd'), backBtn = $('importBack'), cancelBtn = $('importCancel');
  const TYPES = ['argo', 'glider', 'ctd', 'mooring', 'other'];
  const FIELDS = [
    ['lat', 'Latitude', 'required'], ['lon', 'Longitude', 'required'], ['time', 'Time', 'required'],
    ['depth', 'Depth (m)', 'depth or pressure'], ['pres', 'Pressure (dbar)', 'depth or pressure'],
    ['temp', 'Temperature'], ['sal', 'Salinity'], ['chl', 'Chlorophyll'], ['oxy', 'Oxygen'],
    ['u', 'Eastward current'], ['v', 'Northward current'],
    ['id', 'Profile or platform id'], ['type', 'Instrument type']
  ];
  const api = ()=>window.OceanLens || null;
  const plural = (n, one, many)=>`${n} ${n===1 ? one : many}`;
  let parsed = null, busy = false, isOpen = false;
  const autoAll = new Set();       // datasets whose import turned Show all times on

  /* ---- dialog steps ---- */
  function setStatus(text){ statusEl.textContent = text || ''; }
  function setError(text){ errorEl.textContent = text || ''; errorEl.hidden = !text; }
  function showStep(step){
    pick.hidden = step!=='pick';
    review.hidden = step!=='review';
    addBtn.hidden = step!=='review';
    backBtn.hidden = step==='pick';
  }
  function reset(){
    parsed = null; busy = false; input.value = '';
    setStatus(''); setError(''); showStep('pick');
  }
  function open(){
    if(isOpen) return;
    reset();
    $('importAnnounce').textContent = '';
    ov.classList.add('show'); isOpen = true;
    shell.openDialog(card, ()=>{ ov.classList.remove('show'); isOpen = false; reset(); });
    $('importChoose').focus();                 // the step's main action, not the close button
  }
  const close = ()=>shell.closeDialog();

  async function readFile(file){
    if(busy) return;
    if(!api() || !api().readFile){ setError('The import module did not load, so files cannot be read.'); return; }
    busy = true; setError(''); showStep('read');
    setStatus(`Reading ${file.name}…`);
    backBtn.hidden = true;
    card.tabIndex = -1; card.focus();          // the focused control was in a step that is now hidden
    const p = await api().readFile(file);
    busy = false;
    if(!isOpen) return;
    if(p.error){
      parsed = null; showStep('error'); setStatus('');
      setError(`${file.name}: ${p.error}`);
      backBtn.focus();
      return;
    }
    parsed = p;
    renderReview();
    showStep('review');
    setStatus(`Read ${p.name}. ${p.needsMapping ? 'Match the columns, then add it.' : 'Check the details, then add it.'}`);
    const firstMap = $('importMapGrid').querySelector('select');
    (p.needsMapping && firstMap ? firstMap : addBtn).focus();
  }

  function renderReview(){
    const p = parsed, rows = [];
    $('importFile').textContent = p.name;
    rows.push(['Format', p.formatLabel || p.format]);
    rows.push(['Profiles', p.profiles==null ? 'Counted once the columns are matched'
      : p.profiles===0 ? 'None found' : String(p.profiles)]);
    if(p.format==='csv' && p.columns) rows.push(['Columns', String(p.columns.length)]);
    (p.warnings || []).forEach(w=>rows.push(['Note', w, 'warn']));
    const dl = $('importSummary'); dl.textContent = '';
    rows.forEach(([k, v, cls])=>{
      const dt = document.createElement('dt'), dd = document.createElement('dd');
      dt.textContent = k; dd.textContent = v; if(cls) dd.className = cls;
      dl.append(dt, dd);
    });
    renderMapping();
    // built-ins first, then any type a plugin registered at runtime
    const sel = $('importType'), reg = api().instruments();
    const types = TYPES.concat(reg.filter(t=>!TYPES.includes(t)));
    sel.textContent = '';
    types.forEach(t=>sel.add(new Option(instrumentLabel(t), t)));
    sel.value = types.includes(p.type) ? p.type : 'other';
    $('importName').value = p.name;
  }

  // CSV only: one select per quantity, each listing the file's columns with a sample value
  function renderMapping(){
    const p = parsed, fs = $('importMapping'), grid = $('importMapGrid');
    grid.textContent = '';
    fs.hidden = !(p.format==='csv' && p.columns && p.columns.length);
    if(fs.hidden) return;
    fs.querySelector('legend').textContent = p.needsMapping ? 'Match the columns' : 'Columns';
    fs.querySelector('.field-note').textContent = p.needsMapping
      ? "Some column names weren't recognised. Pick the column that holds each quantity. Latitude, longitude and time are required, and depth or pressure unless the file holds surface currents (eastward and northward)."
      : 'Matched from the header. Change any that are wrong.';
    const sample = i=>p.preview && p.preview[0] && p.preview[0][i]!=null && p.preview[0][i]!=='' ? `: ${p.preview[0][i]}` : '';
    FIELDS.forEach(([key, label, req])=>{
      const id = 'map-'+key, lab = document.createElement('label'), sel = document.createElement('select');
      lab.htmlFor = id; lab.textContent = label;
      if(req){ const r = document.createElement('span'); r.className = 'req'; r.textContent = ` (${req})`; lab.appendChild(r); }
      sel.id = id; sel.dataset.key = key;
      sel.add(new Option('Not in file', ''));
      p.columns.forEach((c, i)=>sel.add(new Option(`${c}${p.units && p.units[i] ? ' ['+p.units[i]+']' : ''}${sample(i)}`, String(i))));
      const m = p.mapping && p.mapping[key];
      sel.value = Number.isInteger(m) ? String(m) : '';
      sel.addEventListener('change', ()=>sel.removeAttribute('aria-invalid'));
      grid.append(lab, sel);
    });
  }

  function currentMapping(){
    const m = {};
    $('importMapGrid').querySelectorAll('select').forEach(s=>{ if(s.value!=='') m[s.dataset.key] = parseInt(s.value, 10); });
    return m;
  }
  // the required quantities that have no column, as select elements
  function missingColumns(m){
    const miss = ['lat', 'lon', 'time'].filter(k=>!(k in m));
    // surface currents (HF radar) need no depth: u and v alone are enough
    if(!('depth' in m) && !('pres' in m) && !('u' in m && 'v' in m)) miss.push('depth');
    return miss;
  }
  const MISSING_NAMES = {lat:'latitude', lon:'longitude', time:'time', depth:'depth or pressure'};

  function add(){
    if(!parsed || busy) return;
    const opts = {type:$('importType').value, name:$('importName').value.trim() || parsed.name};
    if(!$('importMapping').hidden){
      opts.mapping = currentMapping();
      const miss = missingColumns(opts.mapping);
      $('importMapGrid').querySelectorAll('select').forEach(s=>s.removeAttribute('aria-invalid'));
      if(miss.length){
        miss.forEach(k=>$('map-'+k).setAttribute('aria-invalid', 'true'));
        if(miss.includes('depth')) $('map-pres').setAttribute('aria-invalid', 'true');
        const names = miss.map(k=>MISSING_NAMES[k]);
        setError(`Choose a column for ${names.join(', ').replace(/, ([^,]*)$/, ' and $1')}.`);
        $('map-'+miss[0]).focus();
        return;
      }
    }
    let res;
    try{ res = api().importParsed(parsed, opts); }
    catch(err){ setError(err && err.message ? err.message : 'This file could not be added.'); return; }
    const shown = reveal(res), msg = addedMessage(opts.name, res, shown);
    close();
    announce(msg, true);
    if(shown.inst){ openProfile(shown.inst.userData); shell.revealRight(); }   // a narrow window starts with the panel collapsed
  }
  // profiles more than 1.5 days from the analysis are hidden: when none of the new ones shows, go to the analysis
  // nearest the first that has one, else turn on Show all times; then the first one on the map opens
  function reveal(res){
    const out = {moved:false, all:false, date:'', inst:null};
    if(dataMode!=='real') return out;
    const d = api().dataset(res.id), at = i=>+String(i.key).split(':').pop();
    const mine = d ? [].concat(...Object.keys(d.types).map(t=>api().observations(t)))
      .filter(i=>i.dataset===res.id && !(api().instrument(i.kind) || {}).gridded).sort((a, b)=>at(a) - at(b)) : [];
    const now = ()=>instrumentsNow().filter(i=>i.dataset===res.id);
    if(mine.length && !now().length){
      const near = mine.find(i=>Math.abs(i.gapH) <= 36);
      if(near){ setTime(near.snap); out.moved = true; }
      else { setShowAllImported(true); autoAll.add(res.id); out.all = true; out.date = fmt.analysisTime(mine[0].ms); }
    }
    out.inst = now()[0] || null;
    return out;
  }
  function addedMessage(name, res, shown = {}){
    let s = `Added ${plural(res.profiles, 'profile', 'profiles')} from ${name}.`;
    if(res.skipped) s += ` ${plural(res.skipped, 'profile was', 'profiles were')} outside the map and left out.`;
    if(shown.moved) s += ` The timeline moved to ${timeLabel(state.time)}, the analysis nearest the new profiles.`;
    if(shown.all) s += ` No profile is within 1.5 days of an analysis (the first is from ${shown.date}), so Show all times is now on.`;
    else if(!state.showAllImported) s += ' Profiles within 1.5 days of the analysis time are shown; turn on Show all times to see every one.';
    if(shown.inst) s += ` Profile ${shown.inst.id} is open.`;
    return s;
  }
  // "Added" stays on screen (it says how to see every profile), scrolled into view under Your data with the Show
  // all times switch below it when both fit; other notices are for screen readers only
  function announce(text, visible){
    const vis = $('importAnnounce'), sr = $('importAnnounceSr'), el = visible ? vis : sr;
    vis.textContent = ''; sr.textContent = '';
    setTimeout(()=>{
      el.textContent = text;
      if(!visible || getComputedStyle(el).visibility!=='visible') return;
      el.scrollIntoView({block:'nearest'});
      const sw = $('userDataOpts');
      if(!sw.hidden && sw.getBoundingClientRect().bottom - el.getBoundingClientRect().top <= $('left').clientHeight) sw.scrollIntoView({block:'nearest'});
    }, 50);
  }
  // charts.js updateLegend() calls this on every update: the "Real data only" note follows the data mode
  function syncMode(){
    $('userDataMode').hidden = !$('userDataEmpty').hidden || dataMode==='real';
  }

  /* ---- "Your data" list ---- */
  function renderList(){
    const list = $('userDataList'), sets = api().datasets().filter(d=>d.source!=='builtin');
    list.textContent = '';
    sets.forEach(ds=>{
      const li = document.createElement('li'), text = document.createElement('div');
      const name = document.createElement('span'), meta = document.createElement('span');
      text.className = 'data-item'; name.className = 'data-name'; meta.className = 'data-meta';
      const n = ds.profiles || 0;
      name.textContent = ds.name || ds.id; name.title = ds.name || ds.id;
      meta.textContent = [instrumentLabel(ds.type), plural(n, 'profile', 'profiles'),
        ds.source==='server' ? 'from the server' : (ds.format || '').toUpperCase()].filter(Boolean).join(' · ');
      text.append(name, meta);
      const rm = document.createElement('button');
      rm.type = 'button'; rm.className = 'icon-btn'; rm.dataset.id = ds.id;
      rm.setAttribute('aria-label', `Remove ${ds.name || ds.id}`); rm.title = 'Remove';
      rm.innerHTML = '<svg class="icon" viewBox="0 0 16 16" aria-hidden="true"><path d="M4 4l8 8M12 4l-8 8"/></svg>';
      rm.addEventListener('click', ()=>remove(ds, rm));
      li.append(text, rm); list.appendChild(li);
    });
    const any = sets.length > 0;
    $('userDataEmpty').hidden = any;
    $('userDataOpts').hidden = !any;
    $('userDataMode').hidden = !any || dataMode==='real';
    const other = sets.some(ds=>Object.keys(ds.types || {}).some(t=>!['argo', 'glider'].includes(t)));
    $('rowOther').hidden = !other;
    // Show all times that imports turned on goes off with the last of those datasets
    if(!autoAll.size) return;
    autoAll.forEach(id=>{ if(!sets.some(d=>d.id===id)) autoAll.delete(id); });
    if(!autoAll.size && state.showAllImported) setShowAllImported(false);
  }
  function remove(ds, btn){
    const buttons = [...$('userDataList').querySelectorAll('button')], i = buttons.indexOf(btn), all = state.showAllImported;
    api().removeDataset(ds.id);
    renderList();
    const left = [...$('userDataList').querySelectorAll('button')];
    (left[Math.min(i, left.length-1)] || $('addDataBtn')).focus();
    announce(`Removed ${ds.name || ds.id}.${all && !state.showAllImported ? ' Show all times is off again.' : ''}`);
  }

  /* ---- server data source (only when the page is served by server/serve.py) ---- */
  function renderServer(){
    const srv = api() && api().server, grp = $('sourceGrp');
    if(!srv || (!srv.live && !(srv.datasets && srv.datasets.length))){ grp.hidden = true; return; }
    grp.hidden = false;
    const sel = $('sourceSel'), cur = srv.current;
    sel.textContent = '';
    (srv.datasets || []).forEach(d=>sel.add(new Option(d.title || d.name || d.id, d.id)));
    if(cur!=null) sel.value = typeof cur==='object' ? cur.id : cur;
    setLive(srv.live ? 'live' : 'offline');
  }
  function setLive(st, text){
    $('sourceStatus').dataset.state = st;
    $('sourceStatusText').textContent = text || (st==='live' ? 'Live: connected to the OceanLens server'
      : st==='loading' ? 'Loading from the server…'
      : "Offline: the server isn't responding. Showing the data already loaded.");
  }
  $('sourceSel').addEventListener('change', e=>{
    const srv = api() && api().server;
    if(!srv || !srv.load) return;
    setLive('loading');
    Promise.resolve(srv.load(e.target.value)).then(()=>setLive('live'), err=>setLive('offline',
      `Offline: ${err && err.message ? err.message : "the server isn't responding"}. Showing the data already loaded.`));
  });

  /* ---- drag and drop: the whole page takes the file, the 3D view shows the target ---- */
  const zone = $('dropZone');
  let depth = 0;
  const hasFiles = e=>e.dataTransfer && [...(e.dataTransfer.types || [])].includes('Files');
  const splashUp = ()=>{ const s = $('splash'); return s && !s.classList.contains('hide'); };
  window.addEventListener('dragenter', e=>{ if(!hasFiles(e) || splashUp()) return; depth++; zone.hidden = false; });
  window.addEventListener('dragleave', e=>{ if(!hasFiles(e)) return; depth = Math.max(0, depth-1); if(!depth) zone.hidden = true; });
  window.addEventListener('dragover', e=>{ if(hasFiles(e)){ e.preventDefault(); e.dataTransfer.dropEffect = splashUp() ? 'none' : 'copy'; } });
  window.addEventListener('drop', e=>{
    if(!hasFiles(e)) return;
    e.preventDefault(); depth = 0; zone.hidden = true;
    const files = e.dataTransfer.files;
    if(!files || !files.length || splashUp()) return;
    if(!isOpen){ shell.closeDialog(); open(); }
    readFile(files[0]).then(()=>{
      if(files.length > 1 && isOpen) setStatus(`${statusEl.textContent} Only ${files[0].name} was read; add the others one at a time.`);
    });
  });

  /* ---- wiring ---- */
  $('addDataBtn').addEventListener('click', open);
  $('importChoose').addEventListener('click', ()=>input.click());
  input.addEventListener('change', ()=>{ if(input.files && input.files[0]) readFile(input.files[0]); });
  $('importClose').addEventListener('click', close);
  cancelBtn.addEventListener('click', close);
  backBtn.addEventListener('click', ()=>{ reset(); $('importChoose').focus(); });
  addBtn.addEventListener('click', add);
  $('swAllImported').addEventListener('click', ()=>autoAll.clear());     // the user's choice from now on
  card.addEventListener('keydown', e=>{
    if(e.key==='Enter' && e.target.id==='importName'){ e.preventDefault(); add(); }
  });
  ov.addEventListener('click', e=>{ if(e.target===ov && !busy) close(); });

  api().on('dataset', renderList);
  api().on('server', renderServer);
  renderList();
  renderServer();
  return {open, renderList, renderServer, readFile, syncMode};
})();
