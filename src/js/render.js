/* ============================================================
   RENDERING RULES
   The one place that creates three.js materials. It also owns colour conversion,
   palette ramps, draw order, the fog and tone-mapping policy, and the test hooks.
   - Palette colours are authored in sRGB (palettes.js). Data surfaces get them through a
     ramp texture: the vertex carries the value (uv.x), the texture turns it into a
     colour, so what is drawn between grid nodes is the colour of the interpolated
     value, and colour-range changes never touch geometry.
   - Data, categorical marks and lines are unlit, untoned and unfogged, so they match
     the colour bar and legend. Only terrain (land, walls, floor) is lit, tone mapped
     and fogged.
   - Transparent materials never write depth. Draw order between them is set
     explicitly by band (ORDER), because most geometry here is built in world
     coordinates and three.js cannot sort it by object position.
   ============================================================ */
const Render = (() => {
  const RAMP_N = 256;

  // test mode (?test or window.OL_TEST) is decided by boot-guard, which also seeds Math.random
  const testMode = !!(window.__ol && window.__ol.test);

  /* ---- colour conversion ---- */
  const toLinear = c => c <= 0.04045 ? c/12.92 : Math.pow((c + 0.055)/1.055, 2.4);
  // [r,g,b] in sRGB 0–1 (what paletteColor and CSS use) → linear, for vertex colours
  function gpuColor(rgb){ return [toLinear(rgb[0]), toLinear(rgb[1]), toLinear(rgb[2])]; }
  // an sRGB hex such as 0xff8058 or '#ff8058' → a linear THREE.Color, for material colours
  function gpuHex(hex){ return new THREE.Color(hex).convertSRGBToLinear(); }

  /* ---- palettes ---- */
  // old names stay valid until every caller uses the plain keys
  const ALIAS = {thermal:'temperature', haline:'salinity'};
  const OLD = {temperature:'thermal', salinity:'haline'};
  const lutCache = {};
  function paletteKey(name){ return ALIAS[name] || name; }
  // PALETTES entries are {label, lut:'<256 sRGB hex entries>'}; a [[t,[r,g,b]],…] stop list is
  // still accepted while palettes.js moves to the generated format
  function lut(name){
    const key = paletteKey(name);
    if(!lutCache[key]){
      const p = PALETTES[key] || PALETTES[OLD[key]], a = new Uint8Array(RAMP_N*3);
      if(p && typeof p.lut === 'string' && p.lut.length === RAMP_N*6){
        for(let i=0; i<a.length; i++) a[i] = parseInt(p.lut.substr(i*2, 2), 16);
      } else if(Array.isArray(p)){
        for(let i=0; i<RAMP_N; i++){
          const t = i/(RAMP_N - 1), k = Math.max(0, p.findIndex((s, n) => n < p.length-1 && t <= p[n+1][0]));
          const [t0, c0] = p[k], [t1, c1] = p[Math.min(k+1, p.length-1)], f = t1 > t0 ? (t - t0)/(t1 - t0) : 0;
          for(let c=0; c<3; c++) a[i*3+c] = Math.round(c0[c] + (c1[c] - c0[c])*f);
        }
      } else throw new Error(`palette "${name}" is missing or malformed`);
      lutCache[key] = a;
    }
    return lutCache[key];
  }
  // colour at t in [0,1], sRGB 0–1, linear between entries in sRGB: the texture filter does the same
  function paletteColor(name, t){
    const a = lut(name), x = Math.min(1, Math.max(0, Number.isFinite(t) ? t : 0))*(RAMP_N - 1);
    const i = Math.min(RAMP_N - 2, Math.floor(x)), f = x - i, o = i*3;
    return [(a[o] + (a[o+3] - a[o])*f)/255, (a[o+1] + (a[o+4] - a[o+1])*f)/255, (a[o+2] + (a[o+5] - a[o+2])*f)/255];
  }

  /* ---- ramp textures ---- */
  const rampImages = {};
  function rampTexture(name){
    const key = paletteKey(name);
    if(!rampImages[key]){
      const a = lut(key), data = new Uint8Array(RAMP_N*4);
      for(let i=0; i<RAMP_N; i++){ data[i*4] = a[i*3]; data[i*4+1] = a[i*3+1]; data[i*4+2] = a[i*3+2]; data[i*4+3] = 255; }
      rampImages[key] = data;
    }
    // one texture per material: the range lives in the texture's uv transform
    const tex = new THREE.DataTexture(rampImages[key], RAMP_N, 1, THREE.RGBAFormat);
    tex.encoding = THREE.sRGBEncoding;
    tex.magFilter = tex.minFilter = THREE.LinearFilter;
    tex.wrapS = tex.wrapT = THREE.ClampToEdgeWrapping;
    tex.generateMipmaps = false;
    tex.needsUpdate = true;
    tex.name = key;                 // r128 textures have no userData
    return tex;
  }
  // value → texel centre: min lands on entry 0, max on entry 255, anything outside clamps
  function setRange(material, min, max){
    const tex = material.map, span = (max - min) || 1, s = (RAMP_N - 1)/(RAMP_N*span);
    tex.repeat.set(s, 1);
    tex.offset.set(0.5/RAMP_N - min*s, 0);
    material.userData.range = [min, max];
  }
  function setPalette(material, name){
    if(paletteKey(name) === material.map.name) return;
    const [min, max] = material.userData.range, old = material.map;
    material.map = rampTexture(name);
    old.dispose();
    setRange(material, min, max);
  }

  /* ---- draw order bands (transparent pass; the opaque pass needs none) ---- */
  const ORDER = {
    opaque: 0,
    underLines: 10,     // stems, glider tracks, section isolines and outlines: drawn first, tinted by what is in front
    layers: 20,         // + (count-1-index): the deepest layer first, the surface last (camera is always above the sea)
    translucent: 34,    // translucent D26 or isosurface
    surfaceLines: 40,   // graticule, coast, contours, track line
    particles: 42,
    markers: 44,
    selection: 46
  };
  function layerOrder(index, count){ return ORDER.layers + (count - 1 - index); }

  /* ---- render on demand: anything that changes what the canvas shows calls invalidate() ---- */
  let dirty = true;
  function invalidate(){ dirty = true; }
  function takeDirty(){ const d = dirty; dirty = false; return d; }

  // Switch a data surface between opaque use (writes depth; masked edges cut at the wet/dry
  // mid-point) and translucent use (no depth writes; masked edges fade).
  function setSurfaceRole(m, opaque, opacity){
    const alphaTest = opaque && m.vertexColors ? 0.5 : 0;
    if(m.alphaTest !== alphaTest){ m.alphaTest = alphaTest; m.needsUpdate = true; }
    m.transparent = !opaque;
    m.depthWrite = opaque;
    m.opacity = opaque ? 1 : opacity;
    dirty = true;
  }

  /* ---- material factory ----
     Every material in the app comes from one of these, so the policy above holds. */
  const common = {toneMapped:false, fog:false};

  // A surface coloured by a data value: layers, slices, the TCHP map, the section curtain, isosurfaces.
  // opts: palette, min, max; opaque (default true); opacity (translucent only);
  //       masked: geometry carries RGBA vertex colours whose alpha is the land/seabed mask;
  //       underLines: lines are drawn on this surface, so push it back a little in depth.
  function dataSurface(opts){
    const o = Object.assign({opaque:true, opacity:1, masked:false, underLines:false}, opts);
    const m = new THREE.MeshBasicMaterial(Object.assign({}, common, {
      map: rampTexture(o.palette),
      vertexColors: o.masked,
      side: THREE.DoubleSide,
      transparent: !o.opaque,
      opacity: o.opaque ? 1 : o.opacity,
      depthWrite: o.opaque,
      alphaTest: o.opaque && o.masked ? 0.5 : 0,   // opaque masks cut at the mid-point between wet and dry nodes
      polygonOffset: o.underLines, polygonOffsetFactor: 1, polygonOffsetUnits: 1
    }));
    m.userData.kind = 'data';
    setRange(m, o.min, o.max);
    return m;
  }
  // Solid categorical colour (rings, fixes, occluder): sRGB hex in, unlit.
  function flat(hex, opts){
    const o = Object.assign({opacity:1, side:THREE.FrontSide, visible:true}, opts);
    const transparent = o.opacity < 1;
    const m = new THREE.MeshBasicMaterial(Object.assign({}, common, {color:gpuHex(hex), opacity:o.opacity,
      transparent, depthWrite:!transparent, side:o.side, visible:o.visible}));
    m.userData.kind = 'flat';
    return m;
  }
  // Lines: sRGB hex, optional opacity and dashes.
  function line(hex, opts){
    const o = Object.assign({opacity:1, dash:null}, opts);
    const transparent = o.opacity < 1;
    const params = Object.assign({}, common, {color:gpuHex(hex), opacity:o.opacity, transparent, depthWrite:!transparent});
    const m = o.dash ? new THREE.LineDashedMaterial(Object.assign(params, {dashSize:o.dash[0], gapSize:o.dash[1]}))
                     : new THREE.LineBasicMaterial(params);
    m.userData.kind = 'line';
    return m;
  }
  // Per-vertex coloured triangles or lines (particles, arrows). Colours must come from gpuColor().
  function vertexColoured(opts){
    const o = Object.assign({opacity:1, additive:false, lines:false}, opts);
    const transparent = o.opacity < 1 || o.additive;
    const params = Object.assign({}, common, {vertexColors:true, opacity:o.opacity, transparent, depthWrite:!transparent,
      blending: o.additive ? THREE.AdditiveBlending : THREE.NormalBlending});
    const m = o.lines ? new THREE.LineBasicMaterial(params)
                      : new THREE.MeshBasicMaterial(Object.assign(params, {side:THREE.DoubleSide}));
    m.userData.kind = 'vertex';
    return m;
  }
  // Lit terrain: land top, coastal wall, sea floor. Colours are given as rendered today (linear),
  // so terrain keeps its look; only terrain is tone mapped and fogged.
  function terrain(opts){
    const o = Object.assign({roughness:0.95, metalness:0.02, side:THREE.FrontSide}, opts);
    const m = new THREE.MeshStandardMaterial({color:o.color !== undefined ? o.color : 0xffffff,
      vertexColors:!!o.vertexColors, roughness:o.roughness, metalness:o.metalness, side:o.side});
    m.userData.kind = 'terrain';
    return m;
  }
  // Screen-space marks with a constant pixel size and per-point colours (gpuColor). The fill is white in
  // the texture and takes the point colour; the outline keeps its own colour ('dark', or 'light' for dark fills).
  const markTextures = {};
  function markTexture(shape, outline){
    const key = shape + outline;
    if(markTextures[key]) return markTextures[key];
    const s = 64, c = document.createElement('canvas'); c.width = c.height = s;
    const g = c.getContext('2d');
    g.beginPath();
    if(shape === 'ring'){             // the selection ring: white with a dark halo, no fill
      g.arc(s/2, s/2, s/2 - 8, 0, Math.PI*2);
      g.lineWidth = 12; g.strokeStyle = '#0b0f13'; g.stroke();
      g.lineWidth = 5; g.strokeStyle = '#ffffff'; g.stroke();
      const t = new THREE.CanvasTexture(c);
      t.encoding = THREE.sRGBEncoding;
      return (markTextures[key] = t);
    }
    if(shape === 'diamond'){ g.moveTo(s/2, 3); g.lineTo(s-3, s/2); g.lineTo(s/2, s-3); g.lineTo(3, s/2); g.closePath(); }
    else g.arc(s/2, s/2, s/2 - 4, 0, Math.PI*2);
    g.fillStyle = '#ffffff'; g.fill();
    g.lineWidth = 6; g.strokeStyle = outline === 'light' ? '#f4f4f4' : '#0b0f13'; g.stroke();
    const t = new THREE.CanvasTexture(c);
    t.encoding = THREE.sRGBEncoding;
    return (markTextures[key] = t);
  }
  function marks(opts){
    const o = Object.assign({shape:'diamond', sizePx:13, outline:'dark'}, opts);
    const m = new THREE.PointsMaterial(Object.assign({}, common, {map:markTexture(o.shape, o.outline), size:o.sizePx,
      sizeAttenuation:false, vertexColors:true, transparent:true, depthWrite:false, alphaTest:0.1}));
    m.userData.kind = 'marks';
    return m;
  }
  // The backdrop gradient: a ShaderMaterial writes its uniforms straight to the screen (no encoding).
  function backdrop(topHex, bottomHex){
    const m = new THREE.ShaderMaterial({
      uniforms:{topColor:{value:new THREE.Color(topHex)}, bottomColor:{value:new THREE.Color(bottomHex)},
        offset:{value:6}, exponent:{value:0.75}},
      vertexShader:`varying vec3 vWorldPosition; void main(){ vec4 wp = modelMatrix*vec4(position,1.0); vWorldPosition = wp.xyz; gl_Position = projectionMatrix*viewMatrix*wp; }`,
      fragmentShader:`uniform vec3 topColor; uniform vec3 bottomColor; uniform float offset; uniform float exponent;
        varying vec3 vWorldPosition;
        void main(){ float h = normalize(vWorldPosition + vec3(0.0, offset, 0.0)).y;
        gl_FragColor = vec4(mix(bottomColor, topColor, max(pow(max(h,0.0), exponent), 0.0)), 1.0); }`,
      side:THREE.BackSide, toneMapped:false, depthWrite:false
    });
    m.userData.kind = 'backdrop';
    return m;
  }

  /* ---- data values on geometry ---- */
  // values[i] goes to vertex i (uv.x); mask[i] (0/1, optional) to the alpha of an RGBA colour attribute
  function setValues(geo, values, mask){
    const n = geo.attributes.position.count;
    let uv = geo.getAttribute('uv');
    if(!uv || uv.count !== n){ uv = new THREE.Float32BufferAttribute(new Float32Array(n*2), 2); geo.setAttribute('uv', uv); }
    for(let i=0; i<n; i++){ uv.array[i*2] = values[i]; uv.array[i*2+1] = 0.5; }
    uv.needsUpdate = true;
    if(mask){
      let col = geo.getAttribute('color');
      if(!col || col.itemSize !== 4 || col.count !== n){ col = new THREE.Float32BufferAttribute(new Float32Array(n*4).fill(1), 4); geo.setAttribute('color', col); }
      for(let i=0; i<n; i++) col.array[i*4+3] = mask[i];
      col.needsUpdate = true;
    }
  }
  // On an nx × ny grid, give masked or missing nodes the mean of their valid neighbours (repeated until full),
  // so a surface fades or cuts towards land in the colour of the nearby water, not of a filler value.
  function fillMasked(values, mask, nx, ny, passes){
    const v = Float32Array.from(values, (x, i) => mask[i] && Number.isFinite(x) ? x : NaN);
    for(let p=0; p<(passes || 64); p++){
      if(v.every(Number.isFinite)) break;
      const src = v.slice();
      for(let j=0; j<ny; j++) for(let i=0; i<nx; i++){
        const k = j*nx + i;
        if(Number.isFinite(src[k])) continue;
        let s = 0, c = 0;
        for(let dj=-1; dj<=1; dj++) for(let di=-1; di<=1; di++){
          const jj = j + dj, ii = i + di;
          if(jj<0 || jj>=ny || ii<0 || ii>=nx) continue;
          const w = src[jj*nx + ii];
          if(Number.isFinite(w)){ s += w; c++; }
        }
        if(c) v[k] = s/c;
      }
    }
    for(let k=0; k<v.length; k++) if(!Number.isFinite(v[k])) v[k] = 0;   // isolated: fully masked anyway
    return v;
  }

  /* ---- renderer policy ---- */
  function createRenderer(){
    const r = new THREE.WebGLRenderer({antialias:true, alpha:false, preserveDrawingBuffer:false});
    r.outputEncoding = THREE.sRGBEncoding;
    r.toneMapping = THREE.ACESFilmicToneMapping;   // reaches terrain only: everything else is toneMapped:false
    r.toneMappingExposure = 1.15;
    r.physicallyCorrectLights = true;
    return r;
  }
  // device pixel ratio, capped at 2 and at about 4.2 million drawing-buffer pixels; 1 in test mode
  function pixelRatio(cssW, cssH){
    if(testMode) return 1;
    let pr = Math.min(window.devicePixelRatio || 1, 2);
    const budget = 4.2e6;
    if(cssW*cssH*pr*pr > budget) pr = Math.max(1, Math.sqrt(budget/(cssW*cssH)));
    return pr;
  }
  // resize the drawing buffer; call from resize handling and on DPR changes (e.g. a move to another monitor)
  function applySize(renderer, camera, cssW, cssH){
    const pr = pixelRatio(cssW, cssH);
    if(renderer.getPixelRatio() !== pr) renderer.setPixelRatio(pr);
    renderer.setSize(cssW, cssH);
    camera.aspect = cssW/cssH; camera.updateProjectionMatrix();
  }

  /* ---- named groups and test hooks ---- */
  let ctx = null;                 // {renderer, scene, camera, toWorld(lat,lon,depth) → THREE.Vector3, beforeRender()}
  const groups = new Map();       // name → Object3D
  function attach(c){ ctx = c; }
  function register(name, obj){ groups.set(name, obj); return obj; }
  const ISOLATE_KEEP = new Set(['surface','layers','slice','tchp','iso','d26','section','land','floor','backdrop']);

  function renderNow(){
    if(ctx.beforeRender) ctx.beforeRender();
    ctx.renderer.render(ctx.scene, ctx.camera);
    return renderInfo();
  }
  function renderInfo(){
    const r = ctx.renderer, i = r.info, gl = r.getContext();
    return {calls:i.render.calls, triangles:i.render.triangles, lines:i.render.lines, points:i.render.points,
      geometries:i.memory.geometries, textures:i.memory.textures, programs:i.programs ? i.programs.length : 0,
      canvas:[r.domElement.width, r.domElement.height], pixelRatio:r.getPixelRatio(), contextLost:gl.isContextLost()};
  }
  // Read device pixels at CSS-px points, all from one fresh render, in the same task
  // (the drawing buffer is not preserved). points: [[x, y], …]; returns one result per point.
  function readMany(points, opts){
    const r = ctx.renderer, gl = r.getContext(), cv = r.domElement;
    const cssW = cv.clientWidth, cssH = cv.clientHeight, lost = gl.isContextLost();
    const out = points.map(([x, y]) => ({x, y, inView: Number.isFinite(x) && Number.isFinite(y) && x >= 0 && y >= 0 && x < cssW && y < cssH,
      contextLost: lost, rgb: null}));
    if(lost || !out.some(o => o.inView)) return out;
    const hidden = [];
    const hide = obj => { if(obj && obj.visible){ obj.visible = false; hidden.push(obj); } };
    if(opts && opts.isolate) groups.forEach((obj, name) => { if(!ISOLATE_KEEP.has(name)) hide(obj); });
    if(opts && opts.hide) opts.hide.forEach(name => hide(groups.get(name)));
    try {
      renderNow();
      const pr = r.getPixelRatio(), w = cv.width, h = cv.height, buf = new Uint8Array(w*h*4);
      gl.readPixels(0, 0, w, h, gl.RGBA, gl.UNSIGNED_BYTE, buf);
      out.forEach(o => {
        if(!o.inView) return;
        const k = ((h - 1 - Math.floor(o.y*pr))*w + Math.floor(o.x*pr))*4;
        o.rgb = [buf[k], buf[k+1], buf[k+2]];
      });
    } finally { hidden.forEach(obj => { obj.visible = true; }); }
    return out;
  }
  function screenOf(lat, lon, depth){
    ctx.camera.updateMatrixWorld();
    const p = ctx.toWorld(lat, lon, depth || 0).project(ctx.camera), cv = ctx.renderer.domElement;
    return p.z > 1 || p.z < -1 ? [NaN, NaN] : [(p.x*0.5 + 0.5)*cv.clientWidth, (-p.y*0.5 + 0.5)*cv.clientHeight];
  }
  // also report where the read pixel's centre actually hits the horizontal plane at that depth:
  // tests compare against the value there, not at the requested point half a pixel away
  const _ray = new THREE.Raycaster(), _hit = new THREE.Vector3(), _plane = new THREE.Plane(new THREE.Vector3(0, 1, 0), 0);
  function withHit(res, lat, lon, depth){
    if(!res.inView) return res;
    const cv = ctx.renderer.domElement, pr = ctx.renderer.getPixelRatio();
    const cx = (Math.floor(res.x*pr) + 0.5)/pr, cy = (Math.floor(res.y*pr) + 0.5)/pr;
    _plane.constant = -ctx.toWorld(lat, lon, depth || 0).y;
    _ray.setFromCamera({x:cx/cv.clientWidth*2 - 1, y:-(cy/cv.clientHeight*2 - 1)}, ctx.camera);
    if(_ray.ray.intersectPlane(_plane, _hit)){
      res.world = [_hit.x, _hit.y, _hit.z];
      if(ctx.fromWorld) res.at = ctx.fromWorld(_hit);
    }
    return res;
  }
  const pixelAt = (lat, lon, depth, opts) => withHit(readMany([screenOf(lat, lon, depth)], opts)[0], lat, lon, depth);
  // pts: [[lat, lon, depthM], …], one render for all
  const pixelsAt = (pts, opts) => readMany(pts.map(p => screenOf(p[0], p[1], p[2])), opts).map((res, k) => withHit(res, pts[k][0], pts[k][1], pts[k][2]));
  const readAt = (x, y, opts) => readMany([[x, y]], opts)[0];
  function expectedColour(name, value, min, max){
    return paletteColor(name, (value - min)/(max - min)).map(c => Math.round(c*255));
  }
  function toggle(name, visible){ const o = groups.get(name); if(o) o.visible = visible; return !!o; }

  /* ---- for the colour key and the PNG export ---- */
  // the exact 256 sRGB entries the ramp texture holds, so the 2D key paints the same colours
  const rampLUT = name => lut(name).slice();
  // the scene at device pixels, without HTML overlays; read in the same task as the render
  function renderForExport(){
    if(ctx.renderer.getContext().isContextLost()) return null;   // the export button explains why
    renderNow();
    const src = ctx.renderer.domElement, c = document.createElement('canvas');
    c.width = src.width; c.height = src.height;
    c.getContext('2d').drawImage(src, 0, 0);
    return c;
  }

  const test = {renderNow, renderInfo, pixelAt, pixelsAt, pixelAtScreen:readAt, pixelsAtScreen:readMany,
    expectedColour, groups:() => [...groups.keys()], toggle,
    marks:kind => typeof pickableMarks === 'function' ? pickableMarks(kind) : []};

  return {testMode, gpuColor, gpuHex, paletteKey, paletteColor, rampLUT, renderForExport, rampTexture, setRange, setPalette,
    ORDER, layerOrder, invalidate, takeDirty, setSurfaceRole, dataSurface, flat, line, vertexColoured, terrain, marks, backdrop,
    setValues, fillMasked, createRenderer, pixelRatio, applySize, attach, register, test};
})();
// the 2D charts, colour bar and export call paletteColor(key, t); palettes.js holds data only
if(typeof paletteColor !== 'function') window.paletteColor = Render.paletteColor;
