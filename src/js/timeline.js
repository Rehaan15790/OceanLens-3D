/* ---- timeline: the analyses (real) or demo days (synthetic), and playback ---- */
// the label of time step t in the active data mode
function timeLabel(t){ return dataSource().time.now(t); }

// state -> the timeline slider, time label and play button
function syncTimeline(){
  ui.timeline.max = NT-1;
  ui.timeline.value = state.time;
  ui.timeNow.textContent = timeLabel(state.time);
  ui.playBtn.setAttribute('aria-pressed', state.playing ? 'true' : 'false');
  ui.playBtn.setAttribute('aria-label', state.playing ? 'Pause' : 'Play through the analyses');
  ui.playBtn.classList.toggle('playing', state.playing);
}

/* ---- actions ---- */
function setTime(t){
  if(!Number.isFinite(t)) return;
  state.time = Math.max(0, Math.min(NT-1, Math.round(t)));
  requestUpdate('full');
}
const PLAY_STEP_MS = 1100;
let playTimer = null;
// start or stop playback: one analysis every PLAY_STEP_MS, looping
function setPlaying(on){
  on = !!on;
  if(on===state.playing) return;
  state.playing = on;
  clearInterval(playTimer);
  playTimer = on ? setInterval(()=>setTime((state.time+1) % NT), PLAY_STEP_MS) : null;
  syncTimeline();
}

ui.timeline.addEventListener('input', e=>setTime(parseInt(e.target.value, 10)));
ui.playBtn.addEventListener('click', ()=>setPlaying(!state.playing));
