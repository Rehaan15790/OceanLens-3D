/* Formatting: dates in UTC, coordinates, units, precision per variable and HTML escaping.
   The rules are ux's copy table (formats section): a space before every unit, U+2212 for minus,
   English month abbreviations, and times always in UTC so every viewer sees the same labels. */
const fmt = (()=>{
  const MONTHS = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
  const WORDS = ['zero','one','two','three','four','five','six','seven','eight','nine','ten'];
  const pad = n => String(n).padStart(2, '0');
  const date = t => t instanceof Date ? t : new Date(t);
  const minus = s => s.replace(/^-/, '−');
  // decimals per variable in readouts; bias and RMSE always use 2
  const DP = {temp:1, sal:2, cur:2, tchp:0, dens:2, chl:2, depth:0};

  const dp = (v, n) => minus((Math.abs(v) < 0.5*Math.pow(10, -n) ? 0 : v).toFixed(n));
  const int = v => dp(v, 0);
  const lat = (v, n) => `${Math.abs(v).toFixed(n)}°${v < 0 ? 'S' : 'N'}`;
  const lon = (v, n) => `${Math.abs(v).toFixed(n)}°${v < 0 ? 'W' : 'E'}`;

  return Object.freeze({
    esc: s => String(s).replace(/[&<>"']/g, c => ({'&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;'}[c])),
    dp, int,
    // a value of a variable at its readout precision, with the unit: "28.4 °C"
    value: (key, v, unit) => `${dp(v, DP[key] ?? 2)}${unit ? ' ' + unit : ''}`,
    decimals: key => DP[key] ?? 2,
    signed: (v, n) => (v >= 0 ? '+' : '−') + Math.abs(v).toFixed(n),
    sig2: v => { if(!v) return '0'; const p = Math.pow(10, Math.floor(Math.log10(Math.abs(v))) - 1); return int(Math.round(v/p)*p); },
    countWord: n => (Number.isInteger(n) && n >= 0 && n <= 10) ? WORDS[n] : String(n),
    round: (v, step) => Math.round(v/step)*step,
    roundDown: (v, step) => Math.floor(v/step)*step,
    roundUp: (v, step) => Math.ceil(v/step)*step,
    depthLabel: m => `${int(m)} m`,
    latLon2: (la, lo) => `${lat(la, 2)}, ${lon(lo, 2)}`,
    latLon1: (la, lo) => `${lat(la, 1)}, ${lon(lo, 1)}`,
    // 13 May 2020, 12:00 UTC
    analysisTime: t => { const d = date(t); return `${d.getUTCDate()} ${MONTHS[d.getUTCMonth()]} ${d.getUTCFullYear()}, ${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())} UTC`; },
    // 13 May
    dayMonth: t => { const d = date(t); return `${d.getUTCDate()} ${MONTHS[d.getUTCMonth()]}`; },
    // 18 May, 05:35 UTC
    floatTime: t => { const d = date(t); return `${d.getUTCDate()} ${MONTHS[d.getUTCMonth()]}, ${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())} UTC`; },
    // 15 May 06Z, or 20 May 10:18Z off the synoptic hours
    fixTime: t => { const d = date(t), m = d.getUTCMinutes(); return `${d.getUTCDate()} ${MONTHS[d.getUTCMonth()]} ${pad(d.getUTCHours())}${m ? ':' + pad(m) : ''}Z`; },
    // synthetic time steps are three days apart and have no calendar date
    demoDay: t => 1 + 3*t
  });
})();
