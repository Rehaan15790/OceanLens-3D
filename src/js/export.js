/* ---- PNG export: the 3D view with what a reader needs to interpret it ----
   Title and context (real or synthetic, analysis time), the same colour key and legend as the screen, map
   labels with halos, a north arrow, the exaggeration ratio and the data caveat. Graphics owns the render and
   read step (Render.renderForExport, labelsForExport, northAngle); this file only composes. */
// label styles in the image mirror viewport.css (.geoLabel and its classes)
const EXPORT_LABEL_FONT = {sea:'italic 400 13px', land:'600 13px', small:'600 12px', tick:'400 12px', current:'italic 400 12px', secEnd:'600 12px'};
// the map labels on screen, with the font and colour each gets in the image
function exportLabels(){
  const family = CHART_FONT.split('px ')[1];
  return labelsForExport().map(L=>{
    const cls = (L.cls||'').split(' '), key = cls.includes('small') ? 'small' : cls.find(c=>EXPORT_LABEL_FONT[c]) || 'tick';
    return {text:L.text, x:L.x, y:L.y, font:`${EXPORT_LABEL_FONT[key]} ${family}`,
      color: key==='tick' ? theme().text2 : theme().text, alpha: L.occluded ? 0.35 : 1};
  });
}

// the colour key into the export image, laid out like renderKey (charts.js) on screen
function paintKey(g, spec, x, yBottom, w){
  const th = theme(), lut = Render.rampLUT(spec.palette);
  // the title wraps as the note does: after the ·, never between a number and its unit (non-breaking spaces)
  const head = wrapText(g, spec.title, w-20, CHART_FONT_BOLD), dt = (head.length-1)*15;
  const lines = spec.note ? wrapText(g, spec.note, w-20, CHART_FONT) : [];
  const legendH = spec.legend.length ? spec.legend.length*16 + 12 : 0;
  // an empty key (nothing on screen has a colour) has no bar, ends or marks, as on screen
  const h = dt + (spec.empty ? 31 : 62) + legendH + lines.length*15 + 8, y = yBottom-h;
  g.fillStyle = '#12181e'; g.fillRect(x, y, w, h);
  g.strokeStyle = th.grid; g.lineWidth = 1; g.strokeRect(x+.5, y+.5, w-1, h-1);
  g.textBaseline = 'alphabetic'; g.textAlign = 'left';
  g.fillStyle = th.text; g.font = CHART_FONT_BOLD; head.forEach((l,i)=>g.fillText(l, x+10, y+18+i*15));
  const bx = x+10, bw = w-20, by = y+26+dt, ty = spec.empty ? y+22+dt : by+27;
  if(!spec.empty){
    for(let i=0;i<bw;i++){
      const k = Math.min(255, Math.round(i/(bw-1)*255))*3;
      g.fillStyle = `rgb(${lut[k]},${lut[k+1]},${lut[k+2]})`; g.fillRect(bx+i, by, 1, 12);
    }
    // the screen's mapping (renderKey): a log key places its ticks and marks by the log of the value
    const px = v=>bx + Render.rampPos(v, spec.min, spec.max, spec.scale || 'linear')*bw, markX = spec.marks.map(m=>px(m.v));
    // as on screen: a bold mark label is centred on its mark unless it would stand past an end of the bar, when it
    // starts (or ends) at the mark; marks come first, so an end or tick label that would touch one is left out
    g.font = CHART_FONT_BOLD;
    const markL = spec.marks.map((m,i)=>{ const tx = markX[i], mw = g.measureText(m.text).width, c = tx-mw/2;
      const l = c < bx ? tx : c+mw > bx+bw ? tx-mw : c; return {l, r:l+mw}; });
    g.font = CHART_FONT; g.fillStyle = th.text2;
    const free = (l, r)=>markL.every(q=>r < q.l-4 || l > q.r+4), tw = s=>g.measureText(s).width;
    g.textAlign = 'left'; if(free(bx, bx+tw(spec.ends.min))) g.fillText(spec.ends.min, bx, ty);
    g.textAlign = 'right'; if(free(bx+bw-tw(spec.ends.max), bx+bw)) g.fillText(spec.ends.max, bx+bw, ty);
    g.textAlign = 'center';
    spec.ticks.forEach(t=>{ const tx = px(t.v);
      if([bx, bx+bw, ...markX].every(o=>Math.abs(o-tx) >= bw*0.12) && free(tx-tw(t.text)/2, tx+tw(t.text)/2)) g.fillText(t.text, tx, ty); });
    g.font = CHART_FONT_BOLD; g.textAlign = 'left';
    spec.marks.forEach((m,i)=>{
      const tx = markX[i];
      g.fillStyle = th.bg; g.fillRect(tx-2, by-3, 4, 22); g.fillStyle = th.text; g.fillRect(tx-1, by-3, 2, 22);
      g.fillText(m.text, markL[i].l, ty);
    });
  }
  g.font = CHART_FONT;
  let cy = ty + 10;
  if(legendH){
    g.strokeStyle = th.grid; g.beginPath(); g.moveTo(x+10, cy+.5); g.lineTo(x+w-10, cy+.5); g.stroke();
    cy += 16; g.textAlign = 'left';
    spec.legend.forEach(r=>{ legendSwatch(g, r.sw, x+17, cy-4); g.fillStyle = th.text2; g.fillText(r.text, x+32, cy); cy += 16; });
  }
  g.fillStyle = th.tick; g.textAlign = 'left';
  lines.forEach(l=>{ g.fillText(l, x+10, cy+2); cy += 15; });
}
// one legend symbol; sw is a keySpec legend row's sw ('float good', 'track', …)
function legendSwatch(g, sw, cx, cy){
  const [kind, cls] = sw.split(' ');
  g.lineWidth = 1.5; g.setLineDash([]);
  if(kind==='float'){
    const hex = {fair:MISFIT_CLASSES[1].color, poor:MISFIT_CLASSES[2].color, none:NO_MISFIT}[cls] ?? FLOAT_DEFAULT;
    const fill = '#' + hex.toString(16).padStart(6, '0');   // the marker colours from markers.js
    g.beginPath(); g.moveTo(cx, cy-5); g.lineTo(cx+5, cy); g.lineTo(cx, cy+5); g.lineTo(cx-5, cy); g.closePath();
    g.fillStyle = fill; g.fill();
    g.strokeStyle = cls==='poor' ? '#f4f4f4' : '#0b0f13'; g.stroke();
  } else if(kind==='glider'){
    g.beginPath(); g.arc(cx, cy, 4, 0, 7); g.fillStyle = '#f4f4f4'; g.fill(); g.strokeStyle = '#0b0f13'; g.stroke();
  } else if(kind==='track'){
    g.strokeStyle = '#e8ecef'; g.lineWidth = 2; g.setLineDash([4,3]);
    g.beginPath(); g.moveTo(cx-7, cy); g.lineTo(cx+7, cy); g.stroke(); g.setLineDash([]);
  } else if(kind==='storm'){
    g.strokeStyle = '#e8ecef'; g.lineWidth = 2; g.beginPath(); g.arc(cx, cy, 5, 0, 7); g.stroke();
  } else if(kind==='contour'){
    g.fillStyle = '#e8ecef'; g.fillRect(cx-7, cy-1, 14, 2);
  }
}
// split text into lines no wider than maxW px
function wrapText(g, text, maxW, font){
  g.font = font;
  const lines = []; let line = '';
  text.split(' ').forEach(wd=>{ const t = line ? line+' '+wd : wd;
    if(g.measureText(t).width > maxW && line){ lines.push(line); line = wd; } else line = t; });
  if(line) lines.push(line);
  return lines;
}
function paintNorth(g, x, y, deg){
  g.save(); g.translate(x, y);
  g.fillStyle = '#12181e'; g.beginPath(); g.arc(0, 0, 20, 0, 7); g.fill();
  g.strokeStyle = '#2c3640'; g.lineWidth = 1; g.stroke();
  g.rotate(deg*Math.PI/180);
  g.fillStyle = '#e8ecef'; g.beginPath(); g.moveTo(0, -13); g.lineTo(5, 3); g.lineTo(0, 0); g.lineTo(-5, 3); g.closePath(); g.fill();
  g.fillStyle = '#6b7680'; g.beginPath(); g.moveTo(0, 13); g.lineTo(5, 3); g.lineTo(0, 0); g.lineTo(-5, 3); g.closePath(); g.fill();
  g.restore();
  g.fillStyle = '#e8ecef'; g.font = CHART_FONT_BOLD; g.textAlign = 'center'; g.fillText('N', x, y+34);
}

// the Save PNG button: compose the view, labels, key and caveat, then download the PNG
function exportPNG(){
  const src = Render.renderForExport(), cssW = renderer.domElement.clientWidth, cssH = renderer.domElement.clientHeight;
  if(!src) return;                                     // the WebGL context is lost; boot-guard shows the notice
  const out = document.createElement('canvas');
  out.width = src.width; out.height = src.height;
  const g = out.getContext('2d');
  g.drawImage(src, 0, 0);
  g.scale(src.width/cssW, src.height/cssH);
  const th = theme(), real = dataMode==='real';

  // map labels are HTML over the canvas, so paint them in, with the same dark halo
  g.textAlign = 'center'; g.textBaseline = 'middle'; g.lineJoin = 'round';
  exportLabels().forEach(L=>{
    g.globalAlpha = L.alpha ?? 1; g.font = L.font || CHART_FONT;
    g.lineWidth = 3; g.strokeStyle = '#0b0f13'; g.strokeText(L.text, L.x, L.y);
    g.fillStyle = L.color || th.text; g.fillText(L.text, L.x, L.y);
  });
  g.globalAlpha = 1; g.textBaseline = 'alphabetic'; g.textAlign = 'left';

  // title block: what, where from, when
  const spec = keySpec();
  const context = document.getElementById('ctxText').textContent;
  const badge = real ? 'Real data' : 'Synthetic demo';
  g.font = CHART_FONT;
  const tw = Math.max(g.measureText(context).width + g.measureText(badge).width + 22, g.measureText(spec.title).width, 160);
  g.fillStyle = '#12181e'; g.fillRect(12, 12, tw+24, 64);
  g.strokeStyle = th.grid; g.lineWidth = 1; g.strokeRect(12.5, 12.5, tw+23, 63);
  g.fillStyle = th.text; g.font = "600 15px " + CHART_FONT.split('px ')[1]; g.fillText('OceanLens 3D', 24, 33);
  g.font = CHART_FONT_BOLD; const bw = g.measureText(badge).width + 12;
  g.fillStyle = real ? '#1c242c' : '#e3a82b'; g.fillRect(24, 41, bw, 16);
  g.fillStyle = real ? th.text : '#1a1300'; g.fillText(badge, 30, 53);
  g.font = CHART_FONT; g.fillStyle = th.text; g.fillText(context, 24+bw+8, 53);
  g.fillStyle = th.text2; g.fillText(spec.title, 24, 70);

  paintNorth(g, cssW-36, 36, northAngle());
  paintKey(g, spec, 12, cssH-40, 280);

  // caveat along the bottom
  const added = dataMode==='real' ? OceanLens.datasets().length : 0;
  const cav = dataSource().export.caveat + (added ? ` Includes ${added===1 ? 'one added data set' : added+' added data sets'}.` : '');
  g.font = CHART_FONT; const cw = g.measureText(cav).width;
  g.fillStyle = '#12181e'; g.fillRect(12, cssH-32, cw+20, 22);
  g.fillStyle = th.text2; g.fillText(cav, 22, cssH-17);

  const shown = currentView();         // a 2D product is a map whatever state.mode was left at
  const view = shown==='tchp' || shown==='field2d' ? 'map' : shown==='slice' ? DEPTHS[state.depthIdx]+'m' : shown;
  const link = document.createElement('a');
  link.download = dataSource().export.file(state.time, state.variable, view);
  link.href = out.toDataURL('image/png');
  link.click();
}
document.getElementById('snapBtn').addEventListener('click', exportPNG);
// no export while the WebGL context is lost: the canvas is blank until it is restored
document.addEventListener('webglcontextlost', ()=>{ const b = document.getElementById('snapBtn');
  b.disabled = true; b.title = 'The 3D view is restoring; try again in a moment.'; }, true);
document.addEventListener('webglcontextrestored', ()=>{ const b = document.getElementById('snapBtn');
  b.disabled = false; b.title = ''; }, true);
