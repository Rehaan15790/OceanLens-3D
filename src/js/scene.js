/* ============================================================
   THREE.JS SCENE
   ============================================================ */
const viewport = document.getElementById('viewport');
const scene = new THREE.Scene();
const camera = new THREE.PerspectiveCamera(42, 1, 0.1, 100);
const renderer = new THREE.WebGLRenderer({antialias:true, alpha:false, preserveDrawingBuffer:true});
renderer.setPixelRatio(Math.min(window.devicePixelRatio,2));
renderer.outputEncoding = THREE.sRGBEncoding;
renderer.toneMapping = THREE.ACESFilmicToneMapping;
renderer.toneMappingExposure = 1.15;
renderer.physicallyCorrectLights = true;
viewport.appendChild(renderer.domElement);

/* ---- atmospheric gradient backdrop: lighter near the "surface", dark toward depth ---- */
const bgGeo = new THREE.SphereGeometry(42, 20, 20);
const bgMat = new THREE.ShaderMaterial({
  uniforms:{ topColor:{value:new THREE.Color(0x1d4a66)}, bottomColor:{value:new THREE.Color(0x060e1a)},
    offset:{value:6}, exponent:{value:0.75} },
  vertexShader:`varying vec3 vWorldPosition; void main(){ vec4 wp = modelMatrix*vec4(position,1.0); vWorldPosition = wp.xyz; gl_Position = projectionMatrix*viewMatrix*wp; }`,
  fragmentShader:`uniform vec3 topColor; uniform vec3 bottomColor; uniform float offset; uniform float exponent;
    varying vec3 vWorldPosition;
    void main(){ float h = normalize(vWorldPosition + vec3(0.0, offset, 0.0)).y;
    gl_FragColor = vec4(mix(bottomColor, topColor, max(pow(max(h,0.0), exponent), 0.0)), 1.0); }`,
  side:THREE.BackSide, toneMapped:false
});
scene.add(new THREE.Mesh(bgGeo, bgMat));

scene.add(new THREE.AmbientLight(0xffffff, 0.7));
const hemi = new THREE.HemisphereLight(0x9fd8ff, 0x0a1c14, 0.85); scene.add(hemi);
const dl = new THREE.DirectionalLight(0xeaf6ff,0.7); dl.position.set(3,8,4); scene.add(dl);
// domain corners sit ~20-24 units from the camera, so fog must reach well past that
scene.fog = new THREE.Fog(0x081525, 20, 62);
