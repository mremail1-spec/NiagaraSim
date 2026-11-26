import { Viewer } from './viewer.js';
import * as exp from './export.js';
import { guide, ringSvg } from './guide.js';

const $ = (id) => document.getElementById(id);
const MAX_FRAMES = 120;
const MOVE = 0.045;          // image change that triggers the next auto-capture

const state = {
  thumbs: [], w: 0, h: 0,
  stream: null, auto: true,
  lastKey: null, lastTick: null, lastShot: 0, sharpHist: [],
  seq: 0, wanted: 0, replied: 0, mask: [], guide: null, complete: false,
  results: {}, building: false,
};

// ---------- tabs ----------
function showTab(name) {
  document.querySelectorAll('[role=tab]').forEach((b) => b.setAttribute('aria-selected', String(b.dataset.tab === name)));
  document.querySelectorAll('main section').forEach((s) => s.classList.toggle('active', s.id === name));
  if (name === 'model') viewer.draw();
}
document.querySelectorAll('[role=tab]').forEach((b) => b.addEventListener('click', () => showTab(b.dataset.tab)));

// ---------- engine (persistent guided session) ----------
const worker = new Worker('worker.js', { type: 'module' });
function options() {
  return { fov_deg: +$('optFov').value || 65, dense_size: +$('optDense').value, mesh_res: +$('optMesh').value };
}
function send(cmd, extra = {}, transfer = []) {
  worker.postMessage({ cmd, seq: ++state.seq, ...extra }, transfer);
  return state.seq;
}
function setBusy() { $('cue').classList.toggle('busy', state.replied < state.wanted); }

// ---------- guidance overlay ----------
const ICONS = {
  right: '<path d="M8 24h28M26 13l11 11-11 11" fill="none" stroke="currentColor" stroke-width="4" stroke-linecap="round" stroke-linejoin="round"/>',
  left: '<path d="M40 24H12M22 13 11 24l11 11" fill="none" stroke="currentColor" stroke-width="4" stroke-linecap="round" stroke-linejoin="round"/>',
  up: '<path d="M24 40V12M13 22l11-11 11 11" fill="none" stroke="currentColor" stroke-width="4" stroke-linecap="round" stroke-linejoin="round"/>',
  back: '<path d="M14 18h16a10 10 0 0 1 0 20H18M20 10l-8 8 8 8" fill="none" stroke="currentColor" stroke-width="4" stroke-linecap="round" stroke-linejoin="round"/>',
  done: '<path d="M10 25l9 9 19-20" fill="none" stroke="currentColor" stroke-width="4.5" stroke-linecap="round" stroke-linejoin="round"/>',
  tap: '<circle cx="24" cy="24" r="14" fill="none" stroke="currentColor" stroke-width="3.5"/><circle cx="24" cy="24" r="4" fill="currentColor"/>',
  shoot: '<rect x="6" y="14" width="36" height="25" rx="5" fill="none" stroke="currentColor" stroke-width="3.5"/><circle cx="24" cy="26.5" r="7" fill="none" stroke="currentColor" stroke-width="3.5"/>',
};
function cue(arrow, title, detail) {
  $('cue').hidden = false;
  $('arrow').innerHTML = ICONS[arrow] || ICONS.shoot;
  $('gTitle').textContent = title;
  $('gDetail').textContent = detail || '';
}
function showGuide(g) {
  state.guide = g;
  state.complete = g.complete;
  // With auto-capture the user only walks; photos are taken for them.
  const title = state.stream && state.auto ? g.title.replace(' and take the next photo', '') : g.title;
  cue(g.arrow, title, g.detail);
  $('ringbox').hidden = false;
  $('ringbox').innerHTML = ringSvg(g, 96);
}

// ---------- frame helpers ----------
const work = document.createElement('canvas');
const wctx = work.getContext('2d', { willReadFrequently: true });
const small = document.createElement('canvas');
small.width = 160; small.height = 120;
const sctx = small.getContext('2d', { willReadFrequently: true });

function grab(src, srcW, srcH) {
  if (!state.thumbs.length) {
    const s = Math.min(1, +$('optRes').value / Math.max(srcW, srcH));
    state.w = Math.round(srcW * s) & ~1; state.h = Math.round(srcH * s) & ~1;
  }
  const w = state.w, h = state.h;
  work.width = w; work.height = h;
  const sa = srcW / srcH, ta = w / h;
  let sx = 0, sy = 0, sw = srcW, sh = srcH;
  if (sa > ta) { sw = srcH * ta; sx = (srcW - sw) / 2; } else if (sa < ta) { sh = srcW / ta; sy = (srcH - sh) / 2; }
  wctx.drawImage(src, sx, sy, sw, sh, 0, 0, w, h);
  return wctx.getImageData(0, 0, w, h).data;
}

/** Add a frame to the scan and (by default) re-solve poses for guidance. */
function addFrame(src, srcW, srcH, { solve = true, target = null } = {}) {
  if (state.thumbs.length >= MAX_FRAMES) { hint(`limit of ${MAX_FRAMES} frames reached`, 'warn'); return false; }
  const data = grab(src, srcW, srcH);
  const img = new Image();
  img.src = work.toDataURL('image/jpeg', 0.6);
  img.title = 'Remove this frame';
  img.onclick = () => removeFrame(state.thumbs.indexOf(img));
  state.thumbs.push(img);
  $('strip').appendChild(img);
  img.scrollIntoView({ inline: 'end', block: 'nearest' });
  const seq = send('add', { frame: data.buffer, w: state.w, h: state.h, solve: solve && !target, options: options() }, [data.buffer]);
  if (solve) state.wanted = seq;
  if (target) state.wanted = send('target', { u: target[0] * state.w, v: target[1] * state.h });
  setBusy();
  updateCount();
  return true;
}
function removeFrame(i) {
  if (i < 0 || state.building) return;
  state.thumbs[i].remove();
  state.thumbs.splice(i, 1);
  state.wanted = send('remove', { i });
  setBusy();
  updateCount();
}
function updateCount() {
  const n = state.thumbs.length;
  const placed = state.mask.reduce((a, b) => a + b, 0);
  $('hudCount').textContent = `${n} frame${n === 1 ? '' : 's'}${n ? ` · ${placed} placed` : ''}`;
  $('btnProcess').disabled = placed < 3 || state.building;
  if (!n) { state.w = state.h = 0; state.lastKey = null; }
}
function hint(text, cls = '') {
  const el = $('hudHint');
  el.textContent = text;
  el.className = 'pill ' + cls;
  $('hud').hidden = false;
}

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

// ---------- worker replies ----------
let current = null;
worker.onmessage = ({ data: m }) => {
  if (m.type === 'guide') {
    state.replied = Math.max(state.replied, m.seq);
    setBusy();
    state.mask = Array.from(m.mask);
    state.thumbs.forEach((img, i) => img.classList.toggle('bad', !!m.final && !state.mask[i]));
    const g = guide({ n: m.n, solved: m.solved, mask: m.mask, cams: m.cams, target: m.target });
    if (state.stream || m.n) showGuide(g);
    if (g.complete) hint('coverage complete – press Build 3D model', 'ok');
    state.results.sparse = m;
    if (m.solved) {
      viewer.setPoints('sparse', m.pos, m.col);
      const [f, , w, h] = m.intr;
      viewer.setCameras(m.cams, w / h, f / Math.max(w, h));
      if (!state.fitted) { viewer.fit(m.pos); state.fitted = true; }
      if (!state.building) setMode('sparse');
    }
    updateCount();
  } else if (m.type === 'progress') {
    if (current !== m.stage) { if (current) $('st-' + current)?.classList.add('done'); current = m.stage; }
    const li = $('st-' + m.stage);
    if (li) li.querySelector('i').style.width = `${Math.round(m.p * 100)}%`;
    if (m.msg) log(m.msg);
  } else if (m.type === 'dense') {
    state.results.dense = m;
    viewer.setPoints('dense', m.pos, m.col);
    setMode('dense');
  } else if (m.type === 'mesh') {
    state.results.mesh = m;
    if (m.idx.length) { viewer.setMesh(m.pos, m.col, m.nrm, m.idx); setMode('mesh'); }
  } else if (m.type === 'done') {
    if (current) $('st-' + current)?.classList.add('done');
    log(`done in ${m.seconds.toFixed(1)} s`);
    state.building = false;
    updateCount();
  } else if (m.type === 'error') {
    log('⚠ ' + m.msg);
    state.building = false;
    state.replied = state.wanted;
    setBusy();
    updateCount();
  }
};
worker.onerror = (e) => log('⚠ engine failed to start: ' + (e.message || 'unknown error'));

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
  if (state.thumbs.length) { if (state.guide) showGuide(state.guide); }
  else cue('tap', 'Tap the object you want to scan', 'Hold the phone so the whole object is in view with some space around it.');
  hint('move slowly around the object');
  requestAnimationFrame(tick);
}
function stopCamera() {
  state.stream?.getTracks().forEach((t) => t.stop());
  state.stream = null;
  video.hidden = true;
  $('cue').hidden = true;
  $('ringbox').hidden = true;
  $('btnCam').textContent = 'Start camera';
  $('btnAuto').disabled = true;
  $('btnShot').disabled = true;
}

// Tap on the video: the first tap marks the object and takes the first frame.
video.addEventListener('click', (e) => {
  if (!state.stream) return;
  const r = video.getBoundingClientRect();
  const fx = (e.clientX - r.left) / r.width, fy = (e.clientY - r.top) / r.height;
  if (!state.thumbs.length) {
    const a = analyse(video, video.videoWidth, video.videoHeight);
    if (addFrame(video, video.videoWidth, video.videoHeight, { target: [fx, fy] })) {
      state.lastKey = a.g; state.lastShot = performance.now(); flash();
      const dot = $('tapdot');
      const sr = video.parentElement.getBoundingClientRect();
      dot.style.left = `${e.clientX - sr.left}px`; dot.style.top = `${e.clientY - sr.top}px`;
      $('tapmark').hidden = false;
      setTimeout(() => ($('tapmark').hidden = true), 1200);
      cue('right', 'Object marked. Now walk slowly to your right', 'Keep it centred. Photos are taken automatically as you move.');
    }
  } else {
    $('btnShot').click();
  }
});

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
  // live meter: how far you have moved toward the next automatic photo
  const change = state.lastKey ? diff(a.g, state.lastKey) : 0;
  const meter = $('meter');
  meter.style.width = state.lastKey ? `${Math.min(100, (change / MOVE) * 100)}%` : '0%';
  meter.classList.toggle('ready', change >= MOVE);
  if (!state.auto || !state.thumbs.length || state.complete || state.building) return;
  if (speed > 0.09) { hint('too fast – slow down', 'warn'); return; }
  if (change > 0.25) hint('big jump since last frame – go back a little', 'warn');
  if (change > MOVE && rel > 0.7 && t - state.lastShot > 500) {
    if (addFrame(video, vw, vh)) {
      state.lastKey = a.g;
      state.lastShot = t;
      flash();
      hint('photo taken', 'ok');
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
  if (addFrame(video, video.videoWidth, video.videoHeight)) { state.lastKey = a.g; state.lastShot = performance.now(); flash(); }
};
$('btnClear').onclick = () => {
  state.thumbs.forEach((t) => t.remove());
  Object.assign(state, { thumbs: [], mask: [], guide: null, complete: false, results: {}, fitted: false, lastKey: null });
  send('reset', { options: options() });
  viewer.clear();
  $('ringbox').hidden = true;
  if (state.stream) cue('tap', 'Tap the object you want to scan', 'Hold the phone so the whole object is in view.');
  else $('cue').hidden = true;
  updateCount();
};

// ---------- import ----------
$('file').onchange = async (e) => {
  const files = [...e.target.files];
  e.target.value = '';
  $('intro').hidden = true;
  const imgs = files.filter((f) => !f.type.startsWith('video/'));
  const vids = files.filter((f) => f.type.startsWith('video/'));
  let k = 0;
  for (const f of imgs) {
    k++;
    try {
      const bmp = await createImageBitmap(f, { imageOrientation: 'from-image' });
      addFrame(bmp, bmp.width, bmp.height, { solve: k === imgs.length && !vids.length });
      bmp.close();
    } catch { hint(`could not read ${f.name}`, 'warn'); }
  }
  for (const v of vids) await importVideo(v, v === vids[vids.length - 1]);
  if (!state.stream) $('intro').hidden = state.thumbs.length > 0;
};

async function importVideo(file, solveAtEnd) {
  const v = document.createElement('video');
  v.muted = true; v.playsInline = true; v.preload = 'auto';
  v.src = URL.createObjectURL(file);
  try { await new Promise((r, j) => { v.onloadeddata = r; v.onerror = () => j(new Error('cannot decode video')); }); }
  catch (e) { hint(e.message, 'warn'); return; }
  const dur = v.duration;
  if (!isFinite(dur) || dur <= 0) return;
  const want = Math.min(MAX_FRAMES - state.thumbs.length, Math.max(10, Math.round(dur * 2.5)));
  const seek = (t) => new Promise((r) => { v.onseeked = r; v.currentTime = t; });
  $('hud').hidden = false;
  for (let i = 0; i < want; i++) {
    let best = null;
    for (const dt of [0, 0.12]) {
      const t = Math.min(dur - 0.01, ((i + 0.5) / want) * dur + dt);
      await seek(t);
      const a = analyse(v, v.videoWidth, v.videoHeight);
      if (!best || a.sharp > best.sharp) best = { t, sharp: a.sharp };
    }
    if (best.t !== v.currentTime) await seek(best.t);
    addFrame(v, v.videoWidth, v.videoHeight, { solve: solveAtEnd && i === want - 1 });
    hint(`extracting video frames ${i + 1}/${want}`);
  }
  URL.revokeObjectURL(v.src);
  hint('video imported');
}

// ---------- build ----------
const STEPS = [['sfm', 'Camera poses (final solve)'], ['dense', 'Depth maps'], ['mesh', 'Build mesh']];
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
  if (state.building) return;
  stopCamera();
  resetSteps();
  current = null;
  state.building = true;
  updateCount();
  showTab('model');
  const placed = state.mask.reduce((a, b) => a + b, 0);
  log(`${state.thumbs.length} frames; final pose solve, then the dense model…`);
  state.wanted = send('finish', { options: options() });
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

send('reset', { options: options() });
window.scan3d = { state, viewer };
