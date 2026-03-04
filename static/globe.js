/**
 * PayBack — 3D Globe Background
 * Requires Three.js to be loaded before this script.
 */
(function () {

  // ── Canvas ───────────────────────────────────────────
  const canvas = document.createElement('canvas');
  canvas.id = 'globe-canvas';
  document.body.insertBefore(canvas, document.body.firstChild);

  // ── Renderer ─────────────────────────────────────────
  const renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: true });
  renderer.setSize(window.innerWidth, window.innerHeight);
  renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));

  // ── Scene + Camera ───────────────────────────────────
  const scene  = new THREE.Scene();
  const camera = new THREE.PerspectiveCamera(42, window.innerWidth / window.innerHeight, 0.1, 100);
  camera.position.z = 3.2;

  const R     = 1.0;
  const group = new THREE.Group();
  scene.add(group);

  // ── Core sphere (placeholder until texture loads) ────
  const sphereMat = new THREE.MeshPhongMaterial({
    color:     0x1a2a4a,
    emissive:  0x0c1a3a,
    shininess: 18,
    specular:  new THREE.Color(0x224488),
  });
  const sphere = new THREE.Mesh(new THREE.SphereGeometry(R, 64, 64), sphereMat);
  group.add(sphere);

  // ── Load Earth texture ───────────────────────────────
  const loader = new THREE.TextureLoader();
  loader.crossOrigin = 'anonymous';
  loader.load(
    'https://threejs.org/examples/textures/planets/earth_atmos_2048.jpg',
    function (texture) {
      sphereMat.map      = texture;
      sphereMat.color.set(0xffffff);
      sphereMat.emissive.set(0x000000);
      sphereMat.shininess = 18;
      sphereMat.specular  = new THREE.Color(0x336699);
      sphereMat.needsUpdate = true;
    },
    undefined,
    function () { console.warn('PayBack globe: earth texture failed to load'); }
  );

  // ── Latitude rings ───────────────────────────────────
  [-75, -60, -45, -30, -15, 0, 15, 30, 45, 60, 75].forEach(lat => {
    const phi  = THREE.MathUtils.degToRad(90 - lat);
    const y    = R * Math.cos(phi);
    const r    = R * Math.sin(phi);
    if (r < 0.05) return;
    const ring = new THREE.Mesh(
      new THREE.TorusGeometry(r, 0.0015, 6, 120),
      new THREE.MeshBasicMaterial({ color: 0x6366f1, transparent: true, opacity: 0.22 })
    );
    ring.position.y = y;
    ring.rotation.x = Math.PI / 2;
    group.add(ring);
  });

  // ── Longitude rings ──────────────────────────────────
  const meridianMat = new THREE.MeshBasicMaterial({ color: 0x6366f1, transparent: true, opacity: 0.14 });
  for (let i = 0; i < 18; i++) {
    const ring = new THREE.Mesh(new THREE.TorusGeometry(R, 0.0015, 6, 120), meridianMat);
    ring.rotation.y = (i / 18) * Math.PI;
    group.add(ring);
  }

  // ── Inner atmosphere ─────────────────────────────────
  group.add(new THREE.Mesh(
    new THREE.SphereGeometry(R * 1.10, 32, 32),
    new THREE.MeshBasicMaterial({ color: 0x4488ff, transparent: true, opacity: 0.07, side: THREE.BackSide })
  ));

  // ── Outer glow ───────────────────────────────────────
  group.add(new THREE.Mesh(
    new THREE.SphereGeometry(R * 1.22, 32, 32),
    new THREE.MeshBasicMaterial({ color: 0x6366f1, transparent: true, opacity: 0.025, side: THREE.BackSide })
  ));

  // ── Stars ────────────────────────────────────────────
  const starCount = 2000;
  const starPos   = new Float32Array(starCount * 3);
  for (let i = 0; i < starCount; i++) {
    const dist  = 8 + Math.random() * 14;
    const theta = Math.random() * Math.PI * 2;
    const phi   = Math.acos(2 * Math.random() - 1);
    starPos[i * 3]     = dist * Math.sin(phi) * Math.cos(theta);
    starPos[i * 3 + 1] = dist * Math.sin(phi) * Math.sin(theta);
    starPos[i * 3 + 2] = dist * Math.cos(phi);
  }
  const starGeo = new THREE.BufferGeometry();
  starGeo.setAttribute('position', new THREE.BufferAttribute(starPos, 3));
  scene.add(new THREE.Points(starGeo, new THREE.PointsMaterial({
    color: 0xffffff, size: 0.025, transparent: true, opacity: 0.45,
  })));

  // ── Lighting ─────────────────────────────────────────
  scene.add(new THREE.AmbientLight(0x334466, 1.4));

  // Sun-like directional light (warm, from upper-right)
  const sunLight = new THREE.DirectionalLight(0xfff5e0, 1.8);
  sunLight.position.set(5, 3, 4);
  scene.add(sunLight);

  // Cool fill light to simulate sky scatter
  const fillLight = new THREE.DirectionalLight(0x6699ff, 0.5);
  fillLight.position.set(-4, -1, -3);
  scene.add(fillLight);

  // ── Mouse tracking ───────────────────────────────────
  let mx = 0, my = 0, tx = 0, ty = 0;
  window.addEventListener('mousemove', e => {
    mx = (e.clientX / window.innerWidth  - 0.5) * 2;
    my = (e.clientY / window.innerHeight - 0.5) * 2;
  });

  // ── Animate ──────────────────────────────────────────
  let tick = 0;
  (function animate() {
    requestAnimationFrame(animate);
    tick += 0.0008;
    tx += (mx - tx) * 0.035;
    ty += (my - ty) * 0.035;
    group.rotation.y = tick + tx * 0.55;
    group.rotation.x = ty * 0.30;
    renderer.render(scene, camera);
  })();

  // ── Resize ───────────────────────────────────────────
  window.addEventListener('resize', () => {
    camera.aspect = window.innerWidth / window.innerHeight;
    camera.updateProjectionMatrix();
    renderer.setSize(window.innerWidth, window.innerHeight);
  });

})();
