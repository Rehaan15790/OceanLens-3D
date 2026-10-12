/* ============================================================
   IMPORT AND SERVER MODE
   A file -> detect the format -> parser -> dataset -> addDataset. Imported profiles behave like
   Argo (marker, profile chart against the model column, misfit, timeline filtering) and show in
   the real-data mode. Server mode: when the page comes from server/serve.py, OceanLens.server
   lists its datasets; on file:// and static hosts nothing changes.
   ============================================================ */
const IMPORT_MAX_BYTES = 50e6;
let importSeq = 0;

// the file's first bytes -> 'netcdf3' | 'hdf5' | 'text'
function sniffFormat(u8){
  if(u8[0]===0x43 && u8[1]===0x44 && u8[2]===0x46) return 'netcdf3';
  if(u8[0]===0x89 && u8[1]===0x48 && u8[2]===0x44 && u8[3]===0x46) return 'hdf5';
  return 'text';
}
// the registered reader of this kind whose detect() scores highest; null when none scores
function bestFormat(kind, src, name){
  let best = null, top = 0;
  Object.values(IMPORT_FORMATS).filter(f=>f.kind===kind).forEach(f=>{
    let s = 0;
    try { s = +f.detect(src, name) || 0; } catch(err){ s = 0; }
    if(s > top){ top = s; best = f; }
  });
  return best;
}
// never rejects: a problem comes back as parsed.error, in words a user can act on
async function readFile(file){
  if(file.size > IMPORT_MAX_BYTES){
    return {name:file.name, error:`The file is ${(file.size/1e6).toFixed(0)} MB; the browser limit is 50 MB. Subset it or use the pipeline.`, warnings:[]};
  }
  try { return await readData(file.name, await file.arrayBuffer()); }
  catch(err){ return {name:file.name, error:'The file could not be read: ' + err.message, warnings:[]}; }
}
// bytes or text -> the parsed object the import dialog reviews; errors come back in out.error, never thrown
async function readData(name, data){
  const out = {name, format:null, formatLabel:'', error:null, warnings:[], needsMapping:false, columns:null, units:null,
               mapping:null, preview:null, type:null, profiles:null};
  try {
    const u8 = typeof data==='string' ? null : new Uint8Array(data);
    const kind = u8 ? sniffFormat(u8) : 'text';
    if(kind==='hdf5') throw new Error('NetCDF-4 file: convert it to NetCDF-3 first (nccopy -k classic) or export it as CSV.');
    if(kind==='netcdf3'){
      const nc = NetCDF3.parse(u8.buffer.slice(u8.byteOffset, u8.byteOffset + u8.byteLength));
      const f = bestFormat('netcdf', nc, name);
      if(!f) throw new Error('This NetCDF file is not a layout the app reads (Argo profile, EGO glider, ERDDAP table or CF profiles). Export it as CSV.');
      return finishParsed(out, 'netcdf3', f.label, f.read(nc, {name}));
    }
    const text = typeof data==='string' ? data : new TextDecoder('utf-8').decode(u8);
    if(/^\s*[{[]/.test(text)){
      const j = JSON.parse(text);
      if(j && j.schema!==undefined && j.meta && j.model) throw new Error('This is an OceanLens snapshot (model data). It is built into the app with build.py, not imported.');
      const profiles = Array.isArray(j) ? j : j && j.profiles;
      if(!Array.isArray(profiles)) throw new Error('This JSON file has no "profiles" list.');
      const meta = Array.isArray(j) ? {} : j;
      return finishParsed(out, 'json', 'OceanLens dataset (JSON)', {profiles, type:typeof meta.type==='string' ? meta.type : 'other',
        name:typeof meta.name==='string' ? meta.name : undefined, notes:Array.isArray(meta.notes) ? meta.notes.map(String) : []});
    }
    const table = parseDelimited(text);
    const f = bestFormat('csv', table, name);
    if(f) return finishParsed(out, 'csv', f.label, f.read(table, {name}));
    const g = guessMapping(table);
    Object.assign(out, {format:'csv', formatLabel:`Delimited text (${{',':'comma', '\t':'tab', ';':'semicolon'}[table.delimiter]})`,
      columns:table.header, units:table.units, mapping:g.mapping, preview:table.rows.slice(0, 5),
      needsMapping:g.needsMapping, _table:table});
    if(g.missing.length) out.warnings.push('Not found from the headers: ' + g.missing.join(', ') + '.');
    out.type = typeFromText(table.header.join(' ') + ' ' + name) || 'other';
    if(!g.needsMapping){
      try {
        const ds = tableToDataset(table, g.mapping, {name});
        out._dataset = ds; out.profiles = ds.profiles.length; out.type = ds.type; out.warnings.push(...ds.warnings);
      } catch(err){ out.needsMapping = true; out.warnings.push(err.message); }
    }
    return out;
  } catch(err){
    out.error = err instanceof SyntaxError ? 'The JSON is not valid: ' + err.message : err.message;
    return out;
  }
}
function finishParsed(out, format, label, ds){
  if(!ds || !Array.isArray(ds.profiles)) throw new Error('The reader returned no profiles.');
  return Object.assign(out, {format, formatLabel:label, type:ds.type || 'other', profiles:ds.profiles.length,
                             warnings:out.warnings.concat(ds.warnings || []), _dataset:ds});
}
// add a read file as a dataset; opts.mapping (csv), opts.type (instrument type), opts.name
function importParsed(parsed, opts = {}){
  if(!parsed || parsed.error) throw new Error(parsed ? parsed.error : 'Nothing to import.');
  let ds = parsed._dataset;
  if(parsed._table && (opts.mapping || !ds)) ds = tableToDataset(parsed._table, opts.mapping || parsed.mapping, {name:parsed.name, type:opts.type});
  if(!ds) throw new Error('Choose the columns first.');
  const d = Object.assign({}, ds, {id:`imp-${++importSeq}`, name:opts.name || parsed.name, source:'import',
                                   format:parsed.format, type:opts.type || ds.type || parsed.type || 'other'});
  const r = addDataset(d);                     // throws when nothing can be shown: the data mode stays as it was
  if(dataMode!=='real') setDataMode('real');   // imported observations are real data
  r.warnings = (ds.warnings || []).concat(r.warnings);
  return r;
}

/* ---- server mode ---- */
const serverMode = {live:false, datasets:[], current:null, load:loadServerDataset};
// GET a server API path as JSON; throws the server's own error message
async function serverJSON(path){
  const r = await fetch(path, {headers:{Accept:'application/json'}});
  const j = await r.json().catch(()=>null);
  if(!r.ok) throw new Error(j && j.error ? j.error.message : `The server answered ${r.status}.`);
  return j;
}
// on load: if serve.py answers api/v1/datasets, fill OceanLens.server and tell the plugins; otherwise stay offline
async function startServerMode(){
  if(!/^https?:$/.test(location.protocol)) return;
  let j;
  try { j = await serverJSON('api/v1/datasets'); } catch(err){ return; }   // a static host: stay offline
  if(!j || !Array.isArray(j.datasets)) return;
  const want = new URLSearchParams(location.search).get('dataset');
  const def = j.datasets.find(d=>d.default) || j.datasets.find(d=>d.source==='snapshot');
  Object.assign(serverMode, {live:true, datasets:j.datasets,
    current: want && j.datasets.some(d=>d.id===want) ? want : def ? def.id : null});
  pluginEmit('server', {server:serverMode});
}
// another snapshot reloads the page with it embedded (serve.py ?dataset=); profiles load into this page
async function loadServerDataset(id){
  const d = serverMode.datasets.find(x=>x.id===id);
  if(!d) throw new Error(`The server has no dataset ${id}.`);
  if(d.source==='snapshot' && id!==serverMode.current){
    location.assign('?dataset=' + encodeURIComponent(id));
    return {id, navigating:true, profiles:0, warnings:[]};
  }
  if(id===serverMode.current) return {id, current:true, profiles:0, warnings:[]};
  if(!(d.profiles > 0)) return {id, profiles:0, warnings:['This dataset has no profiles; its fields are served through the REST API, WMS, WCS and OPeNDAP, not drawn in 3D.']};
  const bbox = [LON0, LAT0, LON1, LAT1].join(',');
  const j = await serverJSON(`api/v1/datasets/${encodeURIComponent(id)}/profiles?bbox=${bbox}`);
  const r = addDataset({id:'srv-' + id, name:d.title || id, source:'server', format:'json', type:'other', profiles:(j && j.profiles) || []});
  if(dataMode!=='real') setDataMode('real');
  return r;
}

Object.assign(OceanLens, {readFile, readData, importParsed, server:serverMode});
startServerMode();
