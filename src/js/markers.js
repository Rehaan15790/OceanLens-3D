/* ---- markers: floats + gliders ---- */
const markerGroup = new THREE.Group(); scene.add(markerGroup);
const markerMeshes = [];

// float colour: coral by default; with "colour floats by misfit" on, the temperature RMSE
// against the model sorts it into good / fair / poor (grey when nothing is comparable)
const MISFIT_CLASSES = [
  {max:0.5, color:0x4fd18b, emissive:0x0f4a2a, label:'RMSE < 0.5 °C'},
  {max:1.0, color:0xffc043, emissive:0x5a3c05, label:'RMSE 0.5–1 °C'},
  {max:Infinity, color:0xff4d4d, emissive:0x5a0f0f, label:'RMSE > 1 °C'},
];
const argoMats = [...MISFIT_CLASSES, {color:0x7d8a96, emissive:0x1e252c}, {color:0xff8058, emissive:0x5a2210}]
  .map(c=>new THREE.MeshStandardMaterial({color:c.color, emissive:c.emissive, emissiveIntensity:0.55, roughness:0.35, metalness:0.25}));
function misfitClass(inst){
  const s = summarize(misfit(inst, state.time, 'temp'));
  return s ? MISFIT_CLASSES.findIndex(c=>s.rmse < c.max) : MISFIT_CLASSES.length;
}
function argoMaterial(inst){
  return argoMats[state.colorMisfit ? misfitClass(inst) : argoMats.length-1];
}
function buildMarkers(){
  markerGroup.children.forEach(o=>{
    if(o.geometry) o.geometry.dispose();
    if(o.material && !argoMats.includes(o.material)) o.material.dispose();
  });
  markerGroup.clear(); markerMeshes.length = 0;
  const argoGeo = new THREE.OctahedronGeometry(0.11);
  const floats = dataMode==='real' ? instrumentsNow() : argoFloats.map(f=>f.inst);
  floats.forEach(inst=>{
    const [x,z] = toXZ(inst.lat, inst.lon);
    const m = new THREE.Mesh(argoGeo, argoMaterial(inst));
    m.position.set(x, 0.05, z);
    m.userData = {type:'argo', data:inst.userData.data, pulsePhase:Math.random()*6.28};
    markerGroup.add(m); markerMeshes.push(m);
    // subtle vertical stem showing profiling range
    const stemGeo = new THREE.BufferGeometry().setFromPoints([
      new THREE.Vector3(x,0.05,z), new THREE.Vector3(x, -(inst.real ? inst.maxDepth : 1000)*DEPTH_SCALE*state.exagg, z)
    ]);
    const stem = new THREE.Line(stemGeo, new THREE.LineBasicMaterial({color:0xff8058, transparent:true, opacity:0.25, toneMapped:false}));
    stem.userData.type = 'argo';
    markerGroup.add(stem);
  });
  if(dataMode!=='synth') return;   // no public glider data in the real window
  const gliderGeo = new THREE.SphereGeometry(0.08,10,10);
  const gliderMat = new THREE.MeshStandardMaterial({color:0x6fa8ff, emissive:0x0f2c55, emissiveIntensity:0.5, roughness:0.3, metalness:0.3});
  gliderTracks.forEach(track=>{
    const pts = track.waypoints.map(w=>{
      const [x,z] = toXZ(w.lat, w.lon);
      const y = -w.depth*DEPTH_SCALE*state.exagg;
      return new THREE.Vector3(x,y,z);
    });
    const lineGeo = new THREE.BufferGeometry().setFromPoints(pts);
    const line = new THREE.Line(lineGeo, new THREE.LineBasicMaterial({color:0x6fa8ff, transparent:true, opacity:0.6, toneMapped:false}));
    line.userData.type = 'glider';
    markerGroup.add(line);
    track.waypoints.forEach((w,wi)=>{
      const [x,z] = toXZ(w.lat, w.lon);
      const y = -w.depth*DEPTH_SCALE*state.exagg;
      const m = new THREE.Mesh(gliderGeo, gliderMat);
      m.position.set(x,y,z);
      m.userData = {type:'glider', data:track, wp:w, wpIndex:wi, pulsePhase:Math.random()*6.28};
      markerGroup.add(m); markerMeshes.push(m);
    });
  });
}
function applyMarkerVisibility(){
  markerGroup.children.forEach(o=>{
    if(o.userData?.type==='argo') o.visible = state.showArgo;
    if(o.userData?.type==='glider') o.visible = state.showGlider;
  });
}
buildMarkers();
function pulseMarkers(elapsed){
  markerMeshes.forEach(m=>{
    const s = 1 + Math.sin(elapsed*1.8 + m.userData.pulsePhase)*0.1;
    m.scale.setScalar(s);
  });
}

const raycaster = new THREE.Raycaster();
const mouseNDC = new THREE.Vector2();
function handleClick(e){
  const rect = renderer.domElement.getBoundingClientRect();
  mouseNDC.x = ((e.clientX-rect.left)/rect.width)*2-1;
  mouseNDC.y = -((e.clientY-rect.top)/rect.height)*2+1;
  raycaster.setFromCamera(mouseNDC, camera);
  // raycasting ignores visibility, so leave out markers whose overlay is switched off
  const targets = markerMeshes.filter(m=>m.visible).concat(trackGroup.visible ? trackMeshes : []);
  const hits = raycaster.intersectObjects(targets);
  if(!hits.length) return;
  const ud = hits[0].object.userData;
  if(ud.type==='track') openTrack();
  else openProfile(ud.type==='argo' ? ud.data.inst.userData : ud.wp.inst.userData);
}
