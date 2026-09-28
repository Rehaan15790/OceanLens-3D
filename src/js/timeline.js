/* ---- timeline ---- */
const SYNTH_DATE0 = new Date('2026-09-01');
function timeLabel(t){
  // time steps are 3 days apart, so label them as offsets that match the T+0d … T+21d axis
  const d0 = dataMode==='real' ? new Date(REAL.meta.times[0]) : SYNTH_DATE0;
  const d = new Date(d0.getTime() + t*3*86400000);
  return `T+${t*3}d · ${d.toLocaleDateString('en-GB',{day:'2-digit',month:'short',year:'numeric'})}`;
}
function setTime(t){
  state.time = t;
  document.getElementById('timeline').value = t;
  document.getElementById('timeNow').textContent = timeLabel(t);
}
document.getElementById('timeline').addEventListener('input', e=>{
  setTime(parseInt(e.target.value));
  fullUpdate();
});
let playTimer = null;
document.getElementById('playBtn').addEventListener('click', ()=>{
  state.playing = !state.playing;
  document.getElementById('playBtn').textContent = state.playing ? '❚❚' : '▶';
  if(state.playing){
    playTimer = setInterval(()=>{
      state.time = (state.time+1) % NT;
      document.getElementById('timeline').value = state.time;
      document.getElementById('timeNow').textContent = timeLabel(state.time);
      fullUpdate();
    }, 1100);
  } else clearInterval(playTimer);
});
