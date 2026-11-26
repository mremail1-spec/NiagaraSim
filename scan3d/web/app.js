import { Viewer } from './viewer.js';
import * as exp from './export.js';

const $ = (id) => document.getElementById(id);
const MAX_FRAMES = 120;

const state = {
  frames: [],          // { data: Uint8ClampedArray (RGBA), url }
  w: 0, h: 0,
  stream: null,
  auto: true,
  lastKey: null,       // small grayscale of last captured frame
  lastTick: null,
  lastShot: 0,
  sharpHist: [],
  results: {},
  worker: null,
};

// ---------- tabs ----------
function showTab(name) {
  document.querySelectorAll('[role=tab]').forEach((b) => b.setAttribute('aria-selected', String(b.dataset.tab === name)));
  document.querySelectorAll('main section').forEach((s) => s.classList.toggle('active', s.id === name));
  if (name === 'model') viewer.draw();
}
document.querySelectorAll('[role=tab]').forEach((b) => b.addEventListener('click', () => showTab(b.dataset.tab)));

// ---------- frame helpers ----------
const work = document.createElement('canvas');
const wctx = work.getContext('2d', { willReadFrequently: true });
const small = document.createElement('canvas');
small.width = 160; small.height = 120;
const sctx = small.getContext('2d', { willReadFrequently: true });

function targetSize(srcW, srcH) {
  if (state.frames.length) return [state.w, state.h];
  const long = +$('optRes').value;
  const s = Math.min(1, long / Math.max(srcW, srcH));
  return [Math.round(srcW * s) & ~1, Math.round(srcH * s) & ~1];
}

/** Draw a source into the frame size (centre-crop to the session's aspect ratio). */
function grab(src, srcW, srcH) {
  const [w, h] = targetSize(srcW, srcH);
  work.width = w; work.height = h;
  const sa = srcW / srcH, ta = w / h;
  let sx = 0, sy = 0, sw = srcW, sh = srcH;
  if (sa > ta) { sw = srcH * ta; sx = (srcW - sw) / 2; } else if (sa < ta) { sh = srcW / ta; sy = (srcH - sh) / 2; }
  wctx.drawImage(src, sx, sy, sw, sh, 0, 0, w, h);
  return { w, h, data: wctx.getImageData(0, 0, w, h).data };
}

function addFrame(src, srcW, srcH) {
  if (state.frames.length >= MAX_FRAMES) { hint(`limit of ${MAX_FRAMES} frames reached`, 'warn'); return false; }
  const f = grab(src, srcW, srcH);
  state.w = f.w; state.h = f.h;
  const url = work.toDataURL('image/jpeg', 0.6);
  state.frames.push({ data: f.data, url });
  const img = new Image();
  img.src = url;
  img.title = 'Remove this frame';
  img.onclick = () => {
    const i = [...$('strip').children].indexOf(img);
    state.frames.splice(i, 1);
    img.remove();
    updateCount();
  };
  $('strip').appendChild(img);
  img.scrollIntoView({ inline: 'end', block: 'nearest' });
  updateCount();
  return true;
}

function updateCount() {
  const n = state.frames.length;
  $('hudCount').textContent = `${n} frame${n === 1 ? '' : 's'}`;
  $('btnProcess').disabled = n < 3;
  if (!n) { state.w = state.h = 0; state.lastKey = null; }
}

function hint(text, cls = '') {
  const el = $('hudHint');
  el.textContent = text;
  el.className = 'pill ' + cls;
  $('hud').hidden = false;
}

// small grayscale + sharpness (variance of Laplacian)
function analyse(src, srcW, srcH) {
  sctx.drawImage(src, 0, 0, srcW, srcH, 0, 0, 160, 120);
  const d = sctx.getImageData(0, 0, 160, 120).data;
  const g = new Float32Array(160 * 120);
  for (let i = 0; i < g.length; i++) g[i] = (0.299 * d[4 * i] + 0.587 * d[4 * i + 1] + 0.114 * d[4 * i + 2]) / 255;
  let s = 0, s2 = 0, n = 0;
  for (let y = 1; y < 119; y++) for (let x = 1; x < 159; x++) {
    const i = y * 160 + x;
    const l = 4 * g[i] - g[i - 1] - g[i + 1] - g[i - 160] - g[i + 160];
    s += l; s2 += l * l; n++;
  }
  return { g, sharp: s2 / n - (s / n) ** 2 };
}
function diff(a, b) {
  let s = 0;
  for (let i = 0; i < a.length; i++) s += Math.abs(a[i] - b[i]);
  return s / a.length;
}

// ---------- camera ----------
const video = $('video');
async function startCamera() {
  if (!navigator.mediaDevices?.getUserMedia) {
    hint('camera API unavailable: open this page over https or on localhost', 'warn');
    return;
  }
  try {
    state.stream = await navigator.mediaDevices.getUserMedia({
      audio: false,
      video: { facingMode: { ideal: 'environment' }, width: { ideal: 1920 }, height: { ideal: 1080 } },
    });
  } catch (e) {
    hint('camera permission denied or no camera: ' + e.name, 'warn');
    return;
  }
  video.srcObject = state.stream;
  await video.play();
  video.hidden = false;
  $('intro').hidden = true;
  $('hud').hidden = false;
  $('btnCam').textContent = 'Stop camera';
  $('btnAuto').disabled = false;
  $('btnShot').disabled = false;
  hint('move slowly around the object');
  requestAnimationFrame(tick);
}
function stopCamera() {
  state.stream?.getTracks().forEach((t) => t.stop());
  state.stream = null;
  video.hidden = true;
  $('btnCam').textContent = 'Start camera';
  $('btnAuto').disabled = true;
  $('btnShot').disabled = true;
}

let lastAnalysis = 0;
function tick(t) {
  if (!state.stream) return;
  requestAnimationFrame(tick);
  if (t - lastAnalysis < 100 || video.readyState < 2) return;
  lastAnalysis = t;
  const vw = video.videoWidth, vh = video.videoHeight;
  const a = analyse(video, vw, vh);
  state.sharpHist.push(a.sharp);
  if (state.sharpHist.length > 30) state.sharpHist.shift();
  const sorted = [...state.sharpHist].sort((x, y) => x - y);
  const ref = sorted[Math.floor(sorted.length * 0.75)] || a.sharp;
  const rel = a.sharp / (ref || 1);
  const sharpEl = $('hudSharp');
  sharpEl.textContent = rel > 0.8 ? 'sharp' : rel > 0.55 ? 'slightly blurry' : 'blurry – hold steady';
  sharpEl.className = 'pill ' + (rel > 0.8 ? 'ok' : rel > 0.55 ? '' : 'warn');
  const speed = state.lastTick ? diff(a.g, state.lastTick) : 0;
  state.lastTick = a.g;
  if (!state.auto) return;
  if (speed > 0.09) { hint('too fast – slow down', 'warn'); return; }
  const change = state.lastKey ? diff(a.g, state.lastKey) : 1;
  if (change > 0.25 && state.lastKey) hint('big jump since last frame – go back a little', 'warn');
  if (change > 0.045 && rel > 0.7 && t - state.lastShot > 300) {
    if (addFrame(video, vw, vh)) {
      state.lastKey = a.g;
      state.lastShot = t;
      flash();
      hint(state.frames.length < 20 ? 'keep going around the object' : 'good – cover the top and bottom too', 'ok');
    }
  }
}
function flash() {
  video.animate([{ opacity: 0.4 }, { opacity: 1 }], { duration: 160 });
}

$('btnCam').onclick = () => (state.stream ? stopCamera() : startCamera());
$('btnAuto').onclick = () => {
  state.auto = !state.auto;
  $('btnAuto').textContent = `Auto-capture: ${state.auto ? 'on' : 'off'}`;
  $('btnAuto').setAttribute('aria-pressed', String(state.auto));
  $('btnShot').classList.toggle('auto', state.auto);
};
$('btnShot').onclick = () => {
  if (!state.stream) return;
  const a = analyse(video, video.videoWidth, video.videoHeight);
  if (addFrame(video, video.videoWidth, video.videoHeight)) { state.lastKey = a.g; flash(); }
};
$('btnClear').onclick = () => {
  state.frames = [];
  $('strip').innerHTML = '';
  updateCount();
};

// ---------- import ----------
$('file').onchange = async (e) => {
  const files = [...e.target.files];
  e.target.value = '';
  $('intro').hidden = true;
  for (const f of files) {
    if (f.type.startsWith('video/')) await importVideo(f);
    else {
      try {
        const bmp = await createImageBitmap(f, { imageOrientation: 'from-image' });
        addFrame(bmp, bmp.width, bmp.height);
        bmp.close();
      } catch { hint(`could not read ${f.name}`, 'warn'); }
    }
  }
  if (!state.stream) $('intro').hidden = state.frames.length > 0;
};

async function importVideo(file) {
  const v = document.createElement('video');
  v.muted = true; v.playsInline = true; v.preload = 'auto';
  v.src = URL.createObjectURL(file);
  await new Promise((r, j) => { v.onloadeddata = r; v.onerror = () => j(new Error('cannot decode video')); }).catch((e) => hint(e.message, 'warn'));
  const dur = v.duration;
  if (!isFinite(dur) || dur <= 0) return;
  const want = Math.min(MAX_FRAMES - state.frames.length, Math.max(10, Math.round(dur * 2.5)));
  const seek = (t) => new Promise((r) => { v.onseeked = r; v.currentTime = t; });
  $('hud').hidden = false;
  for (let i = 0; i < want; i++) {
    // take the sharper of two nearby moments
    let best = null;
    for (const dt of [0, 0.12]) {
      const t = Math.min(dur - 0.01, ((i + 0.5) / want) * dur + dt);
      await seek(t);
      const a = analyse(v, v.videoWidth, v.videoHeight);
      if (!best || a.sharp > best.sharp) best = { t, sharp: a.sharp };
    }
    if (best.t !== v.currentTime) await seek(best.t);
    addFrame(v, v.videoWidth, v.videoHeight);
    hint(`extracting video frames ${i + 1}/${want}`);
  }
  URL.revokeObjectURL(v.src);
  hint('video imported');
}

// ---------- processing ----------
const STEPS = [['features', 'Detect features'], ['matching', 'Match images'], ['sfm', 'Solve camera poses'],
  ['dense', 'Depth maps'], ['mesh', 'Build mesh']];
function resetSteps() {
  $('steps').innerHTML = STEPS.map(([k, t]) => `<li id="st-${k}">${t}<div class="bar"><i></i></div></li>`).join('');
  $('log').textContent = '';
}
resetSteps();
function log(msg) {
  $('log').textContent += msg + '\n';
  $('log').scrollTop = 1e9;
}

$('btnProcess').onclick = () => {
  if (state.worker) state.worker.terminate();
  stopCamera();
  resetSteps();
  viewer.clear();
  state.results = {};
  showTab('model');
  const worker = (state.worker = new Worker('worker.js', { type: 'module' }));
  const options = {
    fov_deg: +$('optFov').value || 65,
    dense_size: +$('optDense').value,
    mesh_res: +$('optMesh').value,
  };
  log(`processing ${state.frames.length} frames at ${state.w}×${state.h}…`);
  const frames = state.frames.map((f) => f.data.slice().buffer);
  worker.postMessage({ frames, w: state.w, h: state.h, options }, frames);
  let current = null;
  worker.onmessage = ({ data: m }) => {
    if (m.type === 'progress') {
      if (current !== m.stage) {
        if (current) $('st-' + current).classList.add('done');
        current = m.stage;
      }
      $('st-' + m.stage).querySelector('i').style.width = `${Math.round(m.p * 100)}%`;
      if (m.msg) log(m.msg);
    } else if (m.type === 'sparse') {
      state.results.sparse = m;
      viewer.setPoints('sparse', m.pos, m.col);
      const [f, , w, h] = m.intr;
      viewer.setCameras(m.cams, w / h, f / Math.max(w, h));
      setMode('sparse');
      viewer.fit(m.pos);
    } else if (m.type === 'dense') {
      state.results.dense = m;
      viewer.setPoints('dense', m.pos, m.col);
      setMode('dense');
    } else if (m.type === 'mesh') {
      state.results.mesh = m;
      if (m.idx.length) { viewer.setMesh(m.pos, m.col, m.nrm, m.idx); setMode('mesh'); }
    } else if (m.type === 'done') {
      $('st-' + current).classList.add('done');
      log(`done in ${m.seconds.toFixed(1)} s`);
      worker.terminate();
      state.worker = null;
    } else if (m.type === 'error') {
      log('⚠ ' + m.msg);
      worker.terminate();
      state.worker = null;
    }
  };
  worker.onerror = (e) => log('⚠ worker error: ' + e.message);
};

// ---------- viewer ----------
const viewer = new Viewer($('view'));
function setMode(mode) {
  viewer.mode = mode;
  document.querySelectorAll('#modes button').forEach((b) => b.setAttribute('aria-pressed', String(b.dataset.mode === mode)));
  viewer.draw();
}
document.querySelectorAll('#modes button').forEach((b) => (b.onclick = () => setMode(b.dataset.mode)));
$('btnCams').onclick = () => {
  viewer.showCams = !viewer.showCams;
  $('btnCams').textContent = `Cameras: ${viewer.showCams ? 'on' : 'off'}`;
  viewer.draw();
};
$('btnTex').onclick = () => {
  viewer.textured = !viewer.textured;
  $('btnTex').textContent = `Colour: ${viewer.textured ? 'on' : 'off'}`;
  viewer.draw();
};
document.querySelectorAll('[data-export]').forEach((b) => (b.onclick = () => {
  const { mesh, dense } = state.results;
  const kind = b.dataset.export;
  if (kind === 'plydense') {
    if (!dense) return log('no dense cloud yet');
    return exp.download('scan-points.ply', exp.plyPoints(dense));
  }
  if (!mesh || !mesh.idx.length) return log('no mesh yet');
  if (kind === 'glb') exp.download('scan.glb', exp.glb(mesh), 'model/gltf-binary');
  if (kind === 'obj') exp.download('scan.obj', exp.obj(mesh), 'text/plain');
  if (kind === 'plymesh') exp.download('scan-mesh.ply', exp.plyMesh(mesh));
}));

// test hook (headless checks)
window.scan3d = { state, viewer };
