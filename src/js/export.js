// briefing-ready PNG: the 3D view plus what a reader needs to interpret it — title,
// variable, view, date, the colour bar with units, the map labels and the data caveat
function exportPNG(){
  renderer.render(scene, camera); // ensure the buffer is current before reading it
  const src = renderer.domElement, cssW = src.clientWidth, cssH = src.clientHeight;
  const out = document.createElement('canvas');
  out.width = src.width; out.height = src.height;
  const g = out.getContext('2d');
  g.drawImage(src, 0, 0);
  g.scale(src.width/cssW, src.height/cssH);

  // geographic labels are HTML overlays, so paint them into the image too
  g.textAlign = 'center'; g.textBaseline = 'middle';
  labelEls.forEach(L=>{
    if(L.el.style.display==='none') return;
    const cs = getComputedStyle(L.el);
    g.globalAlpha = parseFloat(cs.opacity);
    g.font = `${cs.fontStyle} ${cs.fontWeight} ${cs.fontSize} ${cs.fontFamily}`;
    g.fillStyle = cs.color;
    g.fillText(L.el.textContent, parseFloat(L.el.style.left), parseFloat(L.el.style.top));
  });
  g.globalAlpha = 1; g.textBaseline = 'alphabetic'; g.textAlign = 'left';

  const mono = "'JetBrains Mono', Consolas, monospace";
  const meta = VAR_META[state.variable], spec = colorbarSpec();
  const view = state.variable==='tchp' ? 'surface map, D26 surface beneath'
    : state.mode==='slice' ? `depth slice at ${DEPTHS[state.depthIdx]} m`
    : state.mode==='iso' ? `isosurface, ${isoLabel()}` : 'volumetric layers';
  const box = (x,y,w,h)=>{ g.fillStyle = 'rgba(6,13,22,.84)'; g.fillRect(x,y,w,h);
    g.strokeStyle = '#1b3247'; g.lineWidth = 1; g.strokeRect(x+.5,y+.5,w-1,h-1); };

  // title block, top left
  const l1 = `${meta.label} · ${view}`, l2 = timeLabel(state.time);
  g.font = `11px ${mono}`;
  const tw = Math.max(g.measureText(l1).width, g.measureText(l2).width, 120);
  box(12, 12, tw+28, 66);
  g.fillStyle = '#dcebf1'; g.font = "600 15px 'Space Grotesk', 'Segoe UI', sans-serif"; g.fillText('OceanLens 3D', 26, 35);
  g.fillStyle = '#9fb8c8'; g.font = `11px ${mono}`; g.fillText(l1, 26, 53); g.fillText(l2, 26, 68);

  // colour bar, bottom right
  const bw = 230, bh = 12, bx = cssW-bw-26, by = cssH-44;
  box(bx-12, by-30, bw+24, 64);
  g.fillStyle = '#9fb8c8'; g.font = `10.5px ${mono}`;
  g.fillText(!spec.editable ? `Isosurface depth (m) · ${isoLabel()}`
    : state.variable==='tchp' ? `TCHP (${meta.unit}) · white line = ${TCHP_THRESHOLD}`
    : `${meta.label} (${meta.unit})`, bx, by-12);
  for(let x=0; x<bw; x++){
    const c = paletteColor(spec.palette, x/bw);
    g.fillStyle = `rgb(${c[0]*255},${c[1]*255},${c[2]*255})`;
    g.fillRect(bx+x, by, 1, bh);
  }
  const dp = spec.unit==='m' || state.variable==='tchp' ? 0 : 1;
  g.fillStyle = '#dcebf1'; g.fillText(spec.min.toFixed(dp), bx, by+bh+14);
  g.textAlign = 'right'; g.fillText(spec.max.toFixed(dp)+' '+spec.unit, bx+bw, by+bh+14);
  g.textAlign = 'left';

  // data caveat, bottom left
  const cav = caveatText();
  g.font = `9.5px ${mono}`;
  box(12, cssH-32, g.measureText(cav).width+20, 20);
  g.fillStyle = '#6e8ba0'; g.fillText(cav, 22, cssH-18);

  const link = document.createElement('a');
  link.download = `oceanlens3d-${state.variable}-T+${state.time*3}d.png`;
  link.href = out.toDataURL('image/png');
  link.click();
}
document.getElementById('snapBtn').addEventListener('click', exportPNG);
