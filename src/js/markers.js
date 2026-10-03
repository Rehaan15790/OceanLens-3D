/* ---- markers: every instrument type in the registry (Argo, glider, CTD, mooring, imported) ----
   Constant-size screen marks with a dark outline (render.js), achromatic so no palette can hide
   them; the type's marker style sets the shape. Stems show each profile's depth range; types with
   track:true join one platform's profiles in time order. Picking is done on screen, within PICK_PX. */
const markerGroup = new THREE.Group(); scene.add(Render.register('markers', markerGroup));
const stemGroup = new THREE.Group(); scene.add(Render.register('stems', stemGroup));
const PICK_PX = 12, MARK_PX = 13;

// "colour floats by misfit": the temperature RMSE against the model sorts a float into good /
// fair / poor, lighter to darker; grey when nothing is comparable
const MISFIT_CLASSES = [
  {max:0.5, color:0xf4f4f4, css:'#f4f4f4', label:'RMSE < 0.5 °C'},
  {max:1.0, color:0x9aa3ab, css:'#9aa3ab', label:'RMSE 0.5–1 °C'},
  {max:Infinity, color:0x15191d, css:'#15191d', label:'RMSE > 1 °C'},
];
const NO_MISFIT = {color:0x5f6b76, css:'#5f6b76'}, FLOAT_DEFAULT = {color:0xf4f4f4, css:'#f4f4f4'};
function misfitClass(inst){
  const s = summarize(misfit(inst, state.time, 'temp'));
  return s ? MISFIT_CLASSES.findIndex(c=>s.rmse < c.max) : MISFIT_CLASSES.length;
}
let markerItems = [];      // [{pos:Vector3, kind:instrument type, vis, userData}] for picking and hover
const instType = kind => typeof instrumentType === 'function' ? instrumentType(kind) : null;
function markStyle(kind){ const t = instType(kind); return (t && t.marker) || (kind === 'glider' ? 'circle' : 'diamond'); }
// vis: the instrument (or {kind}) that instrumentVisible() decides on
function isShown(vis){
  if(typeof instrumentVisible === 'function') return instrumentVisible(vis);
  return vis.kind === 'glider' ? state.showGlider : state.showArgo;
}
const timeOf = inst => typeof inst.time === 'number' ? inst.time : Date.parse(inst.time);
function marksObject(items, opts){
  const geo = new THREE.BufferGeometry().setFromPoints(items.map(it=>it.pos));
  geo.setAttribute('color', new THREE.Float32BufferAttribute(items.flatMap(it=>Render.gpuColor(new THREE.Color(it.hex).toArray())), 3));
  const pts = new THREE.Points(geo, Render.marks(opts));
  pts.renderOrder = Render.ORDER.markers;
  return pts;
}
function clearGroup(g){
  g.children.slice().forEach(o=>{ g.remove(o); o.geometry.dispose(); o.material.dispose(); });
}
function buildMarkers(){
  clearGroup(markerGroup); clearGroup(stemGroup);
  markerItems = [];
  const floats = dataMode==='real' ? instrumentsNow() : argoFloats.map(f=>f.inst);
  const marks = new Map(), stems = new Map(), tracks = new Map();   // keyed by type (marks: and outline)
  const push = (map, key, v) => { if(!map.has(key)) map.set(key, []); map.get(key).push(v); };
  floats.forEach(inst=>{
    const kind = inst.kind || 'argo', t = instType(kind);
    const [x,z] = toXZ(inst.lat, inst.lon);
    const cls = state.colorMisfit ? misfitClass(inst) : -1;
    const hex = cls < 0 ? FLOAT_DEFAULT.color : cls < MISFIT_CLASSES.length ? MISFIT_CLASSES[cls].color : NO_MISFIT.color;
    const userData = inst.userData || {type:kind, data:{id:inst.id, inst}};
    const item = {pos:new THREE.Vector3(x, 0.05, z), kind, vis:inst, userData, hex};
    push(marks, kind + (cls === 2 ? '|dark' : '|light'), item);
    markerItems.push(item);
    if(t ? t.stem !== false : kind !== 'glider')
      push(stems, kind, [new THREE.Vector3(x,0.05,z), new THREE.Vector3(x, -(inst.real ? inst.maxDepth : 1000)*DEPTH_SCALE*state.exagg, z)]);
    if(t && t.track) push(tracks, kind + '|' + inst.id, item);
  });
  // the darkest misfit class gets a light outline and a size step, so it never reads as a hole
  marks.forEach((items, key)=>{
    const [kind, tone] = key.split('|'), dark = tone === 'dark';
    const o = marksObject(items, {shape:markStyle(kind), sizePx:dark ? Math.round(MARK_PX*1.25) : MARK_PX, outline:dark ? 'light' : 'dark'});
    markerGroup.add(Object.assign(o, {userData:{type:kind, vis:items[0].vis}}));
  });
  stems.forEach((pairs, kind)=>{
    const s = new THREE.LineSegments(new THREE.BufferGeometry().setFromPoints(pairs.flat()), Render.line(0xdfe5ea, {opacity:0.7}));
    s.renderOrder = Render.ORDER.underLines;
    s.userData = {type:kind, vis:floats.find(f=>(f.kind || 'argo')===kind)};
    stemGroup.add(s);
  });
  tracks.forEach((items, key)=>{
    if(items.length < 2) return;
    items.sort((a, b)=>timeOf(a.vis) - timeOf(b.vis));
    const line = new THREE.Line(new THREE.BufferGeometry().setFromPoints(items.map(it=>it.pos)), Render.line(0xaeb8c1, {opacity:0.6}));
    line.renderOrder = Render.ORDER.underLines;
    line.userData = {type:items[0].kind, vis:items[0].vis};
    stemGroup.add(line);
  });
  if(dataMode==='synth'){   // no public glider data in the real window
    const wps = [], vis = {kind:'glider'};
    gliderTracks.forEach(track=>{
      const pts = track.waypoints.map(w=>{ const [x,z] = toXZ(w.lat, w.lon); return new THREE.Vector3(x, -w.depth*DEPTH_SCALE*state.exagg, z); });
      const line = new THREE.Line(new THREE.BufferGeometry().setFromPoints(pts), Render.line(0xaeb8c1, {opacity:0.6}));
      line.renderOrder = Render.ORDER.underLines;
      line.userData = {type:'glider', vis};
      stemGroup.add(line);
      track.waypoints.forEach((w,wi)=>{
        const item = {pos:pts[wi], kind:'glider', vis, userData:{type:'glider', data:track, wp:w, wpIndex:wi}, hex:0xf4f4f4};
        wps.push(item); markerItems.push(item);
      });
    });
    if(wps.length) markerGroup.add(Object.assign(marksObject(wps, {shape:markStyle('glider'), sizePx:MARK_PX}), {userData:{type:'glider', vis}}));
  }
  applyMarkerVisibility();
  placeSelection();
}
function applyMarkerVisibility(){
  [markerGroup, stemGroup].forEach(g=>g.children.forEach(o=>{ o.visible = !!o.userData.vis && isShown(o.userData.vis); }));
}

/* ---- the selected float or waypoint: a static ring ---- */
const selectionRing = new THREE.Points(new THREE.BufferGeometry().setFromPoints([new THREE.Vector3()]),
  Render.marks({shape:'ring', sizePx:30}));
selectionRing.geometry.setAttribute('color', new THREE.Float32BufferAttribute([1,1,1], 3));
selectionRing.renderOrder = Render.ORDER.selection;
selectionRing.visible = false;
scene.add(Render.register('selection', selectionRing));
let selectedInst = null;
// panels call this when a profile opens (inst) or closes (null)
function showSelection(inst){ selectedInst = inst || null; placeSelection(); }
function placeSelection(){
  const it = selectedInst && markerItems.find(m=>(m.userData.data && m.userData.data.inst===selectedInst) || (m.userData.wp && m.userData.wp.inst===selectedInst));
  selectionRing.visible = !!it;
  if(it){ selectionRing.geometry.attributes.position.setXYZ(0, it.pos.x, it.pos.y, it.pos.z); selectionRing.geometry.attributes.position.needsUpdate = true; }
}

/* ---- picking on screen: the nearest float, waypoint or track fix within PICK_PX ---- */
const _pp = new THREE.Vector3();
function pickAt(clientX, clientY){
  const rect = renderer.domElement.getBoundingClientRect(), x = clientX - rect.left, y = clientY - rect.top;
  let best = null, bestD = PICK_PX;
  const consider = (pos, hit)=>{
    _pp.copy(pos).project(camera);
    if(_pp.z > 1) return;
    const d = Math.hypot((_pp.x*0.5+0.5)*rect.width - x, (-_pp.y*0.5+0.5)*rect.height - y);
    if(d < bestD){ bestD = d; best = hit; }
  };
  markerItems.forEach(it=>{ if(isShown(it.vis)) consider(it.pos, it); });
  if(trackGroup.visible) trackFixes.forEach(f=>consider(f.pos, {kind:'track', index:f.index}));
  return best;
}
function handleClick(e){
  const hit = pickAt(e.clientX, e.clientY);
  if(!hit) return;
  if(hit.kind==='track') openTrack();
  else openProfile(hit.userData.wp ? hit.userData.wp.inst.userData : hit.userData.data.inst.userData || hit.userData.data.inst);
}
buildMarkers();

// for tests: every visible pickable mark with its page position (clientX/Y) and identity
function pickableMarks(kind){
  const rect = renderer.domElement.getBoundingClientRect(), out = [];
  const add = (pos, k, id) => {
    _pp.copy(pos).project(camera);
    if(_pp.z > 1 || Math.abs(_pp.x) > 1 || Math.abs(_pp.y) > 1) return;
    out.push({kind:k, id, x:rect.left + (_pp.x*0.5+0.5)*rect.width, y:rect.top + (-_pp.y*0.5+0.5)*rect.height});
  };
  markerItems.forEach(it=>{ if(isShown(it.vis)) add(it.pos, it.kind, it.userData.wp ? it.userData.wp.inst.id : it.userData.data.inst.id); });
  if(trackGroup.visible) trackFixes.forEach(f=>add(f.pos, 'track', f.index));
  return kind ? out.filter(m=>m.kind===kind) : out;
}
