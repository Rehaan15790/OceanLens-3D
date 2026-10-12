/* ============================================================
   RENDERING RULES
   The one place that creates three.js materials. It also owns colour conversion,
   palette ramps, draw order, the fog and tone-mapping policy, and the test hooks.
   - Palette colours are authored in sRGB (palettes.js). Data surfaces get them through a
     ramp texture: the vertex carries the value (uv.x), the texture turns it into a
     colour, so what is drawn between grid nodes is the colour of the interpolated
     value, and colour-range changes never touch geometry.
   - The colour scale is linear or log10 (state.scale). Data surfaces apply it per pixel to the
     interpolated value; CPU-coloured things use rampPos(), so both agree.
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
  const lutCache = {};
  // PALETTES entries are {label, lut:'<256 sRGB hex entries>'} (palettes.js, generated)
  function lut(name){
    if(!lutCache[name]){
      const p = PALETTES[name], a = new Uint8Array(RAMP_N*3);
      if(!p || typeof p.lut !== 'string' || p.lut.length !== RAMP_N*6) throw new Error(`palette "${name}" is missing or malformed`);
      for(let i=0; i<a.length; i++) a[i] = parseInt(p.lut.substr(i*2, 2), 16);
      lutCache[name] = a;
    }
    return lutCache[name];
  }
  // colour at t in [0,1], sRGB 0–1, linear between entries in sRGB: the texture filter does the same
  function paletteColor(name, t){
    const a = lut(name), x = Math.min(1, Math.max(0, Number.isFinite(t) ? t : 0))*(RAMP_N - 1);
    const i = Math.min(RAMP_N - 2, Math.floor(x)), f = x - i, o = i*3;
    return [(a[o] + (a[o+3] - a[o])*f)/255, (a[o+1] + (a[o+4] - a[o+1])*f)/255, (a[o+2] + (a[o+5] - a[o+2])*f)/255];
  }

  /* ---- colour scale: value -> ramp position, linear or log10 ----
     In log mode anything at or below the floor (min, or max/1000 when min <= 0) takes the colour of min. */
  const logFloor = (min, max) => min > 0 ? min : max > 0 ? max*1e-3 : 1e-6;
  // [a, b]: the range in the scale's own units (log10 of the values in log mode)
  function scaleDomain(min, max, scale){
    if(scale !== 'log') return [min, max];
    const lo = logFloor(min, max);
    return [Math.log10(lo), Math.log10(max > lo ? max : lo*10)];
  }
  // t in [0, 1] for value v on the key min..max; scale defaults to state.scale
  function rampPos(v, min, max, scale){
    const sc = scale || state.scale, [a, b] = scaleDomain(min, max, sc);
    const x = sc === 'log' ? Math.log10(Math.max(v, logFloor(min, max))) : v, t = (x - a)/((b - a) || 1);
    return Math.min(1, Math.max(0, Number.isFinite(t) ? t : 0));
  }

  /* ---- ramp textures ---- */
  const rampImages = {};
  // a 256 x 1 texture of the palette, sampled by the data-surface shader (patchRamp)
  function rampTexture(name){
    if(!rampImages[name]){
      const a = lut(name), data = new Uint8Array(RAMP_N*4);
      for(let i=0; i<RAMP_N; i++){ data[i*4] = a[i*3]; data[i*4+1] = a[i*3+1]; data[i*4+2] = a[i*3+2]; data[i*4+3] = 255; }
      rampImages[name] = data;
    }
    // one texture per material (setPalette swaps it); the range lives in the material's ramp uniforms
    const tex = new THREE.DataTexture(rampImages[name], RAMP_N, 1, THREE.RGBAFormat);
    tex.encoding = THREE.sRGBEncoding;
    tex.magFilter = tex.minFilter = THREE.LinearFilter;
    tex.wrapS = tex.wrapT = THREE.ClampToEdgeWrapping;
    tex.generateMipmaps = false;
    tex.needsUpdate = true;
    tex.name = name;                 // r128 textures have no userData
    return tex;
  }
  // value -> texel centre: min lands on entry 0, max on entry 255, anything outside clamps.
  // scale: 'linear' (default) or 'log'; a uniform change, no recompile, no geometry change
  function setRange(material, min, max, scale){
    const sc = scale === 'log' ? 'log' : 'linear', [a, b] = scaleDomain(min, max, sc);
    const s = (RAMP_N - 1)/(RAMP_N*((b - a) || 1)), u = material.userData.ramp;
    u.st.value.set(s, 0.5/RAMP_N - a*s);
    u.log.value = sc === 'log' ? 1 : 0;
    u.floor.value = logFloor(min, max);
    material.userData.range = [min, max];
    material.userData.scale = sc;
  }
  // swap a data surface's palette; its value range and scale stay
  function setPalette(material, name){
    if(name === material.map.name) return;
    const [min, max] = material.userData.range, old = material.map;
    material.map = rampTexture(name);
    old.dispose();
    setRange(material, min, max, material.userData.scale);
  }
  // Data surfaces sample the ramp in the fragment shader from the interpolated raw value (uv.x), so the
  // log is taken per pixel: what is drawn between nodes is the colour of the interpolated value.
  function patchRamp(shader){
    const u = this.userData.ramp;
    shader.uniforms.olRampST = u.st; shader.uniforms.olRampLog = u.log; shader.uniforms.olRampFloor = u.floor;
    shader.vertexShader = 'varying float vOlRaw;\n' +
      shader.vertexShader.replace('#include <uv_vertex>', '#include <uv_vertex>\n\tvOlRaw = uv.x;');
    shader.fragmentShader = 'uniform vec2 olRampST;\nuniform float olRampLog;\nuniform float olRampFloor;\nvarying float vOlRaw;\n' +
      shader.fragmentShader.replace('#include <map_fragment>', `#ifdef USE_MAP
	float olV = olRampLog > 0.5 ? log2(max(vOlRaw, olRampFloor))*0.30102999566398 : vOlRaw;
	vec4 texelColor = mapTexelToLinear(texture2D(map, vec2(olV*olRampST.x + olRampST.y, 0.5)));
	diffuseColor *= texelColor;
#endif`);
  }

  /* ---- draw order bands (transparent pass; the opaque pass needs none) ---- */
  const ORDER = {
    opaque: 0,
    underLines: 10,     // stems, glider tracks, section isolines and outlines: drawn first, tinted by what is in front
    layers: 20,         // + (count-1-index): the deepest layer first, the surface last (camera is always above the sea)
    translucent: 34,    // translucent D26 or isosurface
    map: 36,            // the TCHP / 2D map at the sea surface, when translucent: over the D26 surface below it
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

  // A surface coloured by a data value: layers, slices, the TCHP map, the section curtain, depth of a value.
  // opts: palette, min, max, scale ('linear' | 'log'); opaque (default true); opacity (translucent only);
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
    m.userData.ramp = {st:{value:new THREE.Vector2(1, 0)}, log:{value:0}, floor:{value:1e-6}};
    m.onBeforeCompile = patchRamp;   // one shared function: one program cache key for all data surfaces
    setRange(m, o.min, o.max, o.scale);
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
    // diamond: Argo; circle: glider; triangle (point down): CTD cast; square: mooring; cross: anything else
    if(shape === 'diamond'){ g.moveTo(s/2, 3); g.lineTo(s-3, s/2); g.lineTo(s/2, s-3); g.lineTo(3, s/2); g.closePath(); }
    else if(shape === 'triangle'){ g.moveTo(4, 8); g.lineTo(s-4, 8); g.lineTo(s/2, s-3); g.closePath(); }
    else if(shape === 'square') g.rect(10, 10, s-20, s-20);
    else if(shape === 'cross'){
      g.translate(s/2, s/2); g.rotate(Math.PI/4);
      const a = 8, b = 27;
      [[-a,-b],[a,-b],[a,-a],[b,-a],[b,a],[a,a],[a,b],[-a,b],[-a,a],[-b,a],[-b,-a],[-a,-a]].forEach(([x, y], i) => i ? g.lineTo(x, y) : g.moveTo(x, y));
      g.closePath();
    }
    else g.arc(s/2, s/2, s/2 - 4, 0, Math.PI*2);
    g.fillStyle = '#ffffff'; g.fill();
    g.lineWidth = 6; g.strokeStyle = outline === 'light' ? '#f4f4f4' : '#0b0f13'; g.stroke();
    const t = new THREE.CanvasTexture(c);
    t.encoding = THREE.sRGBEncoding;
    return (markTextures[key] = t);
  }
  // A point sprite takes its centre's depth, so a level surface just under a mark (the sea, a map at y = 0) would cut
  // off its lower half. Each fragment of a mark above the sea takes instead the nearer of the mark's depth and the
  // depth where the fragment's own view ray meets y = 0: the whole sprite clears the surfaces it sits on at any
  // angle, and land in front of it still hides it. (gl_FragDepth: WebGL 2, or WebGL 1 with EXT_frag_depth.)
  const _markUp = new THREE.Vector3();
  function liftMarks(shader){
    // three sets viewMatrix and cameraPosition for few material types, so the camera comes in uniforms of its own
    shader.uniforms.olSize = {value:this.size};
    shader.uniforms.olUp = {get value(){ return _markUp.set(0, 1, 0).transformDirection(ctx.camera.matrixWorldInverse); }};
    shader.uniforms.olSea = {get value(){ return -ctx.camera.matrixWorld.elements[13]; }};
    shader.vertexShader = 'uniform float olSize;\nuniform vec3 olUp;\nuniform float olSea;\nvarying vec4 vOlRay;\nvarying vec4 vOlDepth;\n' +
      shader.vertexShader.replace('#include <project_vertex>', `#include <project_vertex>
	// heights relative to the camera are dot(v, olUp) in view space, olSea that of y = 0. The view ray through a point of
	// the sprite is the centre's plus olT*(gl_PointCoord - 0.5), y flipped; each fragment uses the ray a pixel lower, so
	// all of its samples clear the sea even where the sea's depth changes fast down the screen (grazing views)
	float olPx = 1.0/(scale*projectionMatrix[1][1]), olT = olSize*olPx;
	vOlRay = vec4(dot(vec3(mvPosition.xy/-mvPosition.z - vec2(0.0, olPx), -1.0), olUp), olT*olUp.x, -olT*olUp.y, olSea);
	vOlDepth = vec4(-mvPosition.z, projectionMatrix[2][2], projectionMatrix[3][2], dot(mvPosition.xyz, olUp) > olSea ? 1.0 : 0.0);`);
    shader.fragmentShader = 'varying vec4 vOlRay;\nvarying vec4 vOlDepth;\n' + shader.fragmentShader.replace('#include <logdepthbuf_fragment>', `#include <logdepthbuf_fragment>
#if defined(GL_EXT_frag_depth) || __VERSION__ >= 300
	float olDown = vOlRay.x + (gl_PointCoord.x - 0.5)*vOlRay.y + (gl_PointCoord.y - 0.5)*vOlRay.z;
	float olD = olDown < 0.0 ? min(vOlDepth.x, 0.999*vOlRay.w/olDown) : vOlDepth.x;
	gl_FragDepthEXT = vOlDepth.w > 0.5 ? 0.5 + 0.5*(vOlDepth.z/olD - vOlDepth.y) : gl_FragCoord.z;
#endif`);
  }
  function marks(opts){
    const o = Object.assign({shape:'diamond', sizePx:13, outline:'dark'}, opts);
    const m = new THREE.PointsMaterial(Object.assign({}, common, {map:markTexture(o.shape, o.outline), size:o.sizePx,
      sizeAttenuation:false, vertexColors:true, transparent:true, depthWrite:false, alphaTest:0.1}));
    m.userData.kind = 'marks';
    m.extensions = {fragDepth:true};
    m.onBeforeCompile = liftMarks;
    return m;
  }
  // The 3D isosurface: one colour (sRGB 0-1, from paletteColor), shaded by a light fixed in the world (high, from the
  // north-west) so its relief reads from any side. Faces turned to that light show the key's colour; the rest darken to 45 %.
  // Written straight to the screen like the backdrop, so the colour stays sRGB. Opacity follows m.opacity.
  function isoShaded(rgb){
    const m = new THREE.ShaderMaterial({
      uniforms:{olColor:{value:new THREE.Color().setRGB(rgb[0], rgb[1], rgb[2])}, olOpacity:{get value(){ return m.opacity; }}},
      vertexShader:`varying vec3 vN; void main(){ vN = (vec4(normalMatrix*normal, 0.0)*viewMatrix).xyz; gl_Position = projectionMatrix*modelViewMatrix*vec4(position, 1.0); }`,
      fragmentShader:`uniform vec3 olColor; uniform float olOpacity; varying vec3 vN;
        void main(){ float d = abs(dot(normalize(vN), vec3(-0.477670, 0.796117, -0.371521)));
        gl_FragColor = vec4(olColor*(0.45 + 0.55*d), olOpacity); }`,
      side:THREE.DoubleSide, toneMapped:false
    });
    m.userData.kind = 'iso3d';
    return m;
  }
  function setIsoColour(m, rgb){ m.uniforms.olColor.value.setRGB(rgb[0], rgb[1], rgb[2]); dirty = true; }
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
  // name a scene object so tests can find, hide or isolate it (Render.test.groups, toggle, pixelAt isolate)
  function register(name, obj){ groups.set(name, obj); return obj; }
  const ISOLATE_KEEP = new Set(['surface','layers','slice','tchp','iso','iso3d','d26','section','land','floor','backdrop']);

  function renderNow(){
    if(ctx.beforeRender) ctx.beforeRender();
    ctx.renderer.render(ctx.scene, ctx.camera);
    return renderInfo();
  }
  // draw calls, memory and canvas size of the last frame (tests, and handy in the console: __ol.render.renderInfo())
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
      const pr = r.getPixelRatio(), h = cv.height, px = new Uint8Array(4);
      out.forEach(o => {
        if(!o.inView) return;
        gl.readPixels(Math.floor(o.x*pr), h - 1 - Math.floor(o.y*pr), 1, 1, gl.RGBA, gl.UNSIGNED_BYTE, px);
        o.rgb = [px[0], px[1], px[2]];
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
  // the colour the key gives a value; scale defaults to state.scale
  function expectedColour(name, value, min, max, scale){
    return paletteColor(name, rampPos(value, min, max, scale)).map(c => Math.round(c*255));
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
    marks:kind => pickableMarks(kind)};

  return {testMode, gpuColor, paletteColor, rampPos, rampLUT, renderForExport, setRange, setPalette,
    ORDER, layerOrder, invalidate, takeDirty, setSurfaceRole, dataSurface, flat, line, vertexColoured, terrain, marks, isoShaded, setIsoColour, backdrop,
    setValues, fillMasked, createRenderer, applySize, attach, register, test};
})();
// the 2D charts, colour bar and export call paletteColor(key, t); palettes.js holds data only
const paletteColor = Render.paletteColor;
