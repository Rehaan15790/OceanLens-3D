// Behaviour fingerprint (adopted from architect's round-1 tooling). Drives the app through its views via its
// globals and records only deterministic outputs: panel text, colour-buffer hashes and counts. Two builds that
// return the same fingerprint behave the same for everything listed here.
// Evaluated through CDP by tools/run.py (suite "fingerprint"); the expression's value is the fingerprint object.
(function(){
  const F = {};
  const txt = id => { const e = document.getElementById(id); return e ? (e.textContent || '').replace(/\s+/g, ' ').trim() : null; };
  const hashArr = a => { let h = 0; for(let i=0;i<a.length;i++) h = (Math.imul(h, 31) + Math.round(a[i]*1e4))|0; return h; };
  const hashStr = s => { let h = 0; for(let i=0;i<s.length;i++) h = (Math.imul(h, 31) + s.charCodeAt(i))|0; return h; };
  const setView = (v, m) => {
    state.variable = v; state.mode = m;
    const meta = VAR_META[v];
    state.cbMin = meta.defMin; state.cbMax = meta.defMax; state.palette = meta.palette;
    if(ISO_META[v]) state.isoVal = ISO_META[v].def;
  };
  try {
    for(const dm of ['real','synth']){
      setDataMode(dm);
      for(const t of [0, 3, 5]){
        setTime(t);
        for(const v of ['temp','sal','cur','tchp']){
          for(const m of (v==='tchp' ? ['layers'] : ['layers','slice','iso'])){
            setView(v, m); fullUpdate();
            const key = `${dm}/t${t}/${v}/${m}`;
            F[key] = {
              readout: [txt('roVar'), txt('roRange'), txt('roMean')].join(' | '),
              colourbar: [txt('cbMinLbl'), txt('cbMaxLbl'), txt('paletteName')].join(' | '),
              legend: txt('legend'),
              layers: hashArr(layerMeshes.map(l=>hashArr(l.geometry.getAttribute('color').array))),
              iso: m==='iso' ? [isoInfo.n, isoInfo.min.toFixed(3), isoInfo.max.toFixed(3), hashArr(isoMesh.geometry.getAttribute('position').array)] : null,
              tchp: v==='tchp' ? hashArr(tchpGrid(t).map(c=>c ? c.q : -1)) : null,
              markers: (window.__ol && __ol.render && __ol.render.marks) ? __ol.render.marks().length : markerMeshes.length
            };
          }
        }
        setView('temp','layers'); fullUpdate();
        F[`${dm}/t${t}/validation`] = hashStr(txt('valPanel')) + ' ' + txt('valPanel').slice(0, 120);
        const worst = worstArgo();
        if(worst){
          openProfile(worst.userData);
          const tabs = {};
          for(const tab of ['temp','sal','dens','chl']){
            profileState.tab = tab; drawProfileChart();
            tabs[tab] = txt('profStats') + ' / ' + txt('obsLegend');
          }
          F[`${dm}/t${t}/profile`] = {name: txt('profName'), meta: txt('profMeta'), qc: txt('qcBadge'), derived: txt('derivGrid'), tabs};
          closeSidePanels();
        }
      }
      setView('tchp','layers'); setTime(3); fullUpdate(); openTrack();
      F[`${dm}/track`] = {meta: txt('trackMeta'), table: hashStr(txt('trackTable')) + ' ' + txt('trackTable').slice(0, 160)};
      closeSidePanels();
      setView('temp','layers'); fullUpdate();
      for(const p of ['as15','bob15','n8','track']){
        showSectionPreset(p);
        const mesh = sectionGroup.children[0];
        F[`${dm}/section/${p}`] = {kind: txt('sectionKind'), meta: txt('sectionMeta'), foot: txt('sectionFoot').slice(0, 200),
          curtain: mesh ? [mesh.geometry.getAttribute('position').count, hashArr(mesh.geometry.getAttribute('color').array)] : null};
        clearSection();
      }
    }
    F.labels = labelLayerEl.children.length;
    F.geometries = renderer.info.memory.geometries;
  } catch(err){ F.error = String(err && err.stack || err); }
  return F;
})()
