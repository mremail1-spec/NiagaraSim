import { Viewer } from './viewer.js';
import * as exp from './export.js';
import { guide, ringSvg } from './guide.js';

const $ = (id) => document.getElementById(id);
const MAX = 120, LONG = 800;
const state = { thumbs: [], w: 0, h: 0, results: {}, mask: [], busy: 0, seq: 0, building: false, sampleRun: 0, targetSet: false };
const viewer = new Viewer($('view'));
const worker = new Worker('worker.js', { type: 'module' });
const waiting = new Map();

function options() {
  return { fov_deg: +$('optFov').value || 65, dense_size: +$('optDense').value, mesh_res: +$('optMesh').value };
}
function send(cmd, extra = {}, transfer = []) {
  const seq = ++state.seq;
  worker.postMessage({ cmd, seq, ...extra }, transfer);
  return new Promise((res) => waiting.set(seq, res));
}
function msg(t, warn = false) { $('msg').textContent = t; $('msg').className = 'msg' + (warn ? ' warn' : ''); }
function log(t) { $('log').textContent += t + '\n'; $('log').scrollTop = 1e9; }
function overlay(title, sub) {
  const o = $('overlay');
  if (!title) { o.hidden = true; return; }
  o.hidden = false;
  const s = document.createElement('strong'); s.textContent = title;
  const d = document.createElement('div'); d.append(s, sub || '');
  o.replaceChildren(d);
}

// ---------- arrow cue ----------
const ICONS = {
  right: '<path d="M8 24h28M26 13l11 11-11 11" fill="none" stroke="currentColor" stroke-width="4" stroke-linecap="round" stroke-linejoin="round"/>',
  left: '<path d="M40 24H12M22 13 11 24l11 11" fill="none" stroke="currentColor" stroke-width="4" stroke-linecap="round" stroke-linejoin="round"/>',
  up: '<path d="M24 40V12M13 22l11-11 11 11" fill="none" stroke="currentColor" stroke-width="4" stroke-linecap="round" stroke-linejoin="round"/>',
  back: '<path d="M14 18h16a10 10 0 0 1 0 20H18M20 10l-8 8 8 8" fill="none" stroke="currentColor" stroke-width="4" stroke-linecap="round" stroke-linejoin="round"/>',
  done: '<path d="M10 25l9 9 19-20" fill="none" stroke="currentColor" stroke-width="4.5" stroke-linecap="round" stroke-linejoin="round"/>',
  shoot: '<rect x="6" y="14" width="36" height="25" rx="5" fill="none" stroke="currentColor" stroke-width="3.5"/><path d="M17 14l3-5h8l3 5" fill="none" stroke="currentColor" stroke-width="3.5" stroke-linejoin="round"/><circle cx="24" cy="26.5" r="7" fill="none" stroke="currentColor" stroke-width="3.5"/>',
  wait: '<circle cx="24" cy="24" r="15" fill="none" stroke="currentColor" stroke-width="4" stroke-dasharray="70 30" stroke-linecap="round"/>',
};
function showCue(g) {
  $('arrow').innerHTML = ICONS[g.arrow] || ICONS.shoot;
  $('gTitle').textContent = g.title;
  $('gDetail').textContent = g.detail;
  $('ring').innerHTML = ringSvg(g, 132);
  document.querySelector('.cue').classList.remove('busy');
}
function showBusy() {
  $('arrow').innerHTML = ICONS.wait;
  document.querySelector('.cue').classList.add('busy');
  $('gTitle').textContent = 'Working out where that photo was taken…';
  $('gDetail').textContent = 'You can line up the next shot meanwhile.';
}

// ---------- frames ----------
const work = document.createElement('canvas');
const wctx = work.getContext('2d', { willReadFrequently: true });

/** Resize a source to the scan's frame size; returns RGBA bytes. */
function grab(src, sw0, sh0) {
  let w = state.w, h = state.h;
  if (!state.thumbs.length) {
    const s = Math.min(1, LONG / Math.max(sw0, sh0));
    w = Math.round(sw0 * s) & ~1; h = Math.round(sh0 * s) & ~1;
    state.w = w; state.h = h;
  }
  work.width = w; work.height = h;
  const sa = sw0 / sh0, ta = w / h;
  let sx = 0, sy = 0, sw = sw0, sh = sh0;
  if (sa > ta) { sw = sh0 * ta; sx = (sw0 - sw) / 2; } else if (sa < ta) { sh = sw0 / ta; sy = (sh0 - sh) / 2; }
  wctx.drawImage(src, sx, sy, sw, sh, 0, 0, w, h);
  return wctx.getImageData(0, 0, w, h).data;
}

async function addPhoto(src, sw, sh, { thumbUrl, solve = true } = {}) {
  if (state.thumbs.length >= MAX) { msg(`Limit of ${MAX} photos reached. Press Finish.`, true); return null; }
  const first = !state.thumbs.length;
  const data = grab(src, sw, sh);
  const img = new Image();
  img.src = thumbUrl || work.toDataURL('image/jpeg', 0.55);
  img.alt = `photo ${state.thumbs.length + 1}`;
  img.title = 'Remove this photo';
  img.onclick = () => removePhoto(state.thumbs.indexOf(img));
  state.thumbs.push(img);
  $('strip').appendChild(img);
  img.scrollIntoView({ inline: 'end', block: 'nearest' });
  updateButtons();
  const buf = data.buffer;
  const p = send('add', { frame: buf, w: state.w, h: state.h, solve, options: options() }, [buf]);
  if (solve) { state.busy++; showBusy(); }
  if (first && !state.sampleRun) askTarget(img.src);
  return p;
}

function removePhoto(i) {
  if (i < 0 || state.building) return;
  state.thumbs[i].remove();
  state.thumbs.splice(i, 1);
  if (!state.thumbs.length) { state.w = state.h = 0; state.targetSet = false; }
  updateButtons();
  state.busy++; showBusy();
  send('remove', { i });
}

function updateButtons() {
  const n = state.thumbs.length;
  const reg = state.mask.reduce((a, b) => a + b, 0);
  $('frameInfo').textContent = n ? `${n} photo${n === 1 ? '' : 's'}${state.w ? ` at ${state.w}×${state.h}` : ''}, ${reg} placed in 3D` : 'No photos yet';
  $('btnUndo').disabled = !n || state.building;
  $('btnFinish').disabled = reg < 3 || state.building;
}

// ---------- object marking ----------
function askTarget(src) {
  $('pickImg').src = src;
  $('pickDot').hidden = true;
  $('picker').hidden = false;
}
$('pickImg').onclick = (e) => {
  const r = e.target.getBoundingClientRect();
  const fx = (e.clientX - r.left) / r.width, fy = (e.clientY - r.top) / r.height;
  const dot = $('pickDot');
  dot.style.left = `${fx * 100}%`; dot.style.top = `${fy * 100}%`; dot.hidden = false;
  state.targetSet = true;
  send('target', { u: fx * state.w, v: fy * state.h });
  setTimeout(() => ($('picker').hidden = true), 350);
};
$('pickSkip').onclick = () => { $('picker').hidden = true; };

// ---------- worker replies ----------
let current = null;
worker.onmessage = ({ data: m }) => {
  if (m.type === 'guide') {
    state.busy = Math.max(0, state.busy - 1);
    waiting.get(m.seq)?.(m); waiting.delete(m.seq);
    if (state.busy) return; // a newer update is queued
    state.mask = Array.from(m.mask);
    state.thumbs.forEach((img, i) => img.classList.toggle('bad', i < state.mask.length && !state.mask[i] && m.solved > 0));
    const g = guide({ n: m.n, solved: m.solved, mask: m.mask, cams: m.cams, target: m.target });
    showCue(g);
    $('gStatus').textContent = m.n ? `${m.solved} of ${m.n} photos placed · ${Math.round(m.ms)} ms` : '';
    updateButtons();
    if (m.solved) {
      overlay(null);
      viewer.setPoints('sparse', m.pos, m.col);
      const [f, , w, h] = m.intr;
      viewer.setCameras(m.cams, w / h, f / Math.max(w, h));
      if (viewer.mode !== 'sparse') setMode('sparse');
      if (!state.fitted) { viewer.fit(m.pos); state.fitted = true; }
    } else {
      viewer.clear();
      state.fitted = false;
      overlay(m.n ? 'No 3D yet' : 'Take a photo to start', m.n ? 'The first 3D points appear once two photos overlap and were taken from different spots.' : 'Then follow the arrow.');
    }
    if (g.complete && !state.sampleRun) msg('Coverage complete. Press Finish & build model.');
  } else if (m.type === 'log') {
    log(m.msg);
  } else if (m.type === 'progress') {
    if (current !== m.stage) { if (current) $('st-' + current)?.classList.add('done'); current = m.stage; }
    const li = $('st-' + m.stage);
    if (li) { li.querySelector('i').style.width = `${Math.round(m.p * 100)}%`; }
    if (m.msg) log(m.msg);
  } else if (m.type === 'dense') {
    state.results.dense = m;
    viewer.setPoints('dense', m.pos, m.col);
    setMode('dense');
  } else if (m.type === 'mesh') {
    state.results.mesh = m;
    if (m.idx.length) { viewer.setMesh(m.pos, m.col, m.nrm, m.idx); setMode('mesh'); $('btnExport').disabled = false; }
  } else if (m.type === 'done') {
    if (current) $('st-' + current)?.classList.add('done');
    log(`Model built in ${m.seconds.toFixed(1)} s`);
    state.building = false; updateButtons();
    msg('Model ready. Orbit it on the left, or download it below. Taking another photo continues this scan.');
    waiting.get('build')?.(); waiting.delete('build');
  } else if (m.type === 'error') {
    log('Stopped: ' + m.msg);
    msg(m.msg, true);
    state.building = false; state.busy = 0; updateButtons();
    waiting.get('build')?.(); waiting.delete('build');
  }
};
worker.onerror = (e) => {
  overlay('The 3D engine could not start', 'WebAssembly or module workers may be blocked in this view');
  msg('The 3D engine could not start here: ' + (e.message || 'blocked'), true);
};

// ---------- actions ----------
const STEPS = [['dense', 'Depth maps'], ['mesh', 'Build mesh']];
function resetSteps() {
  $('steps').replaceChildren(...STEPS.map(([k, t]) => {
    const li = document.createElement('li'); li.id = 'st-' + k;
    li.innerHTML = `${t}<span></span><div class="bar"><i></i></div>`;
    return li;
  }));
  current = null;
}
function finish() {
  if (state.building) return Promise.resolve();
  state.building = true; updateButtons();
  resetSteps();
  log('Building the dense model from the placed photos…');
  msg('Building the model. This takes about half a minute for 40 photos.');
  worker.postMessage({ cmd: 'finish', options: options() });
  return new Promise((res) => waiting.set('build', res));
}
$('btnFinish').onclick = finish;
$('btnUndo').onclick = () => removePhoto(state.thumbs.length - 1);

function stopSample() { state.sampleRun = 0; }
async function reset() {
  stopSample();
  state.thumbs.forEach((t) => t.remove());
  Object.assign(state, { thumbs: [], w: 0, h: 0, results: {}, mask: [], busy: 0, targetSet: false, fitted: false });
  $('btnExport').disabled = true;
  $('log').textContent = '';
  resetSteps();
  viewer.clear();
  worker.postMessage({ cmd: 'reset', options: options() });
  showCue(guide({ n: 0 }));
  $('gStatus').textContent = '';
  overlay('Take a photo to start', 'Then follow the arrow.');
  updateButtons();
}
$('btnReset').onclick = () => { reset(); msg(''); };

async function loadBitmap(file) {
  return createImageBitmap(file, { imageOrientation: 'from-image' });
}
$('shot').onchange = async (e) => {
  const f = e.target.files[0];
  e.target.value = '';
  if (!f) return;
  if (state.sampleRun) { await reset(); }
  try { const b = await loadBitmap(f); await addPhoto(b, b.width, b.height); b.close(); }
  catch { msg('Could not read that photo. Try again.', true); }
};
$('file').onchange = async (e) => {
  const files = [...e.target.files];
  e.target.value = '';
  if (!files.length) return;
  if (state.sampleRun) { await reset(); }
  const images = files.filter((f) => !f.type.startsWith('video/'));
  const videos = files.filter((f) => f.type.startsWith('video/'));
  let k = 0;
  for (const f of images) {
    k++;
    try { const b = await loadBitmap(f); addPhoto(b, b.width, b.height, { solve: k === images.length && !videos.length }); b.close(); }
    catch { msg(`Could not read ${f.name}. Use JPEG or PNG photos.`, true); }
  }
  for (const v of videos) await importVideo(v, v === videos[videos.length - 1]);
  msg(`Added ${files.length} file${files.length === 1 ? '' : 's'}. Keep going with Take photo, or press Finish.`);
};

function sharpness(src, w, h) {
  const c = document.createElement('canvas'); c.width = 160; c.height = 120;
  const x = c.getContext('2d', { willReadFrequently: true });
  x.drawImage(src, 0, 0, w, h, 0, 0, 160, 120);
  const d = x.getImageData(0, 0, 160, 120).data;
  let s = 0, s2 = 0, n = 0;
  const g = (i) => d[4 * i] * 0.299 + d[4 * i + 1] * 0.587 + d[4 * i + 2] * 0.114;
  for (let y = 1; y < 119; y++) for (let xx = 1; xx < 159; xx++) {
    const i = y * 160 + xx, l = 4 * g(i) - g(i - 1) - g(i + 1) - g(i - 160) - g(i + 160);
    s += l; s2 += l * l; n++;
  }
  return s2 / n - (s / n) ** 2;
}
async function importVideo(file, solveAtEnd) {
  const v = document.createElement('video');
  v.muted = true; v.playsInline = true; v.preload = 'auto';
  v.src = URL.createObjectURL(file);
  try { await new Promise((r, j) => { v.onloadeddata = r; v.onerror = j; }); }
  catch { msg(`This browser cannot decode ${file.name}. Try an MP4, or photos.`, true); return; }
  const dur = v.duration;
  const want = Math.min(MAX - state.thumbs.length, Math.max(10, Math.round(dur * 2.5)));
  const seek = (t) => new Promise((r) => { v.onseeked = r; v.currentTime = t; });
  for (let i = 0; i < want; i++) {
    let best = null;
    for (const dt of [0, 0.12]) {
      const t = Math.min(dur - 0.01, ((i + 0.5) / want) * dur + dt);
      await seek(t);
      const s = sharpness(v, v.videoWidth, v.videoHeight);
      if (!best || s > best.s) best = { t, s };
    }
    if (best.t !== v.currentTime) await seek(best.t);
    addPhoto(v, v.videoWidth, v.videoHeight, { solve: solveAtEnd && i === want - 1 });
    msg(`Taking frames from the video: ${i + 1}/${want}`);
  }
  URL.revokeObjectURL(v.src);
}

// Sample: replay a 40-photo walk through the same guided loop.
async function playSample() {
  await reset();
  const run = (state.sampleRun = Date.now());
  msg('Sample scan: photos arrive one by one, as if someone were walking around the scene.');
  for (let i = 0; i < 40; i++) {
    if (state.sampleRun !== run) return;
    const url = `sample/frame_${String(i).padStart(2, '0')}.jpg`;
    const b = await createImageBitmap(await (await fetch(url)).blob());
    if (state.sampleRun !== run) return;
    await addPhoto(b, b.width, b.height, { thumbUrl: url });
    b.close();
    await new Promise((r) => setTimeout(r, 250));
  }
  if (state.sampleRun !== run) return;
  await finish();
  if (state.sampleRun === run) state.sampleRun = 0;
}
$('btnSample').onclick = playSample;

// ---------- view ----------
function setMode(mode) {
  viewer.mode = mode;
  document.querySelectorAll('#modes button').forEach((b) => b.setAttribute('aria-pressed', String(b.dataset.mode === mode)));
  viewer.draw();
}
document.querySelectorAll('#modes button').forEach((b) => (b.onclick = () => setMode(b.dataset.mode)));
$('btnCams').onclick = () => { viewer.showCams = !viewer.showCams; $('btnCams').textContent = `Cameras: ${viewer.showCams ? 'on' : 'off'}`; viewer.draw(); };
$('btnTex').onclick = () => { viewer.textured = !viewer.textured; $('btnTex').textContent = `Colour: ${viewer.textured ? 'on' : 'off'}`; viewer.draw(); };

// ---------- export (zip, stored) ----------
const CRC = new Uint32Array(256).map((_, n) => { let c = n; for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1; return c >>> 0; });
function crc32(u8) { let c = 0xffffffff; for (let i = 0; i < u8.length; i++) c = CRC[(c ^ u8[i]) & 255] ^ (c >>> 8); return (c ^ 0xffffffff) >>> 0; }
function zip(entries) {
  const enc = new TextEncoder(), parts = [], central = [];
  let off = 0;
  for (const [name, data] of entries) {
    const d = typeof data === 'string' ? enc.encode(data) : new Uint8Array(data);
    const nm = enc.encode(name), crc = crc32(d);
    const h = new DataView(new ArrayBuffer(30));
    [[0, 0x04034b50, 4], [4, 20, 2], [8, 0, 2], [10, 0, 2], [12, 0x21, 2], [14, crc, 4], [18, d.length, 4], [22, d.length, 4], [26, nm.length, 2], [28, 0, 2]]
      .forEach(([o, v, s]) => (s === 4 ? h.setUint32(o, v, true) : h.setUint16(o, v, true)));
    const c = new DataView(new ArrayBuffer(46));
    [[0, 0x02014b50, 4], [4, 20, 2], [6, 20, 2], [8, 0, 2], [10, 0, 2], [12, 0, 2], [14, 0x21, 2], [16, crc, 4], [20, d.length, 4], [24, d.length, 4],
      [28, nm.length, 2], [30, 0, 2], [32, 0, 2], [34, 0, 2], [36, 0, 2], [38, 0, 4], [42, off, 4]]
      .forEach(([o, v, s]) => (s === 4 ? c.setUint32(o, v, true) : c.setUint16(o, v, true)));
    parts.push(new Uint8Array(h.buffer), nm, d);
    central.push(new Uint8Array(c.buffer), nm);
    off += 30 + nm.length + d.length;
  }
  const cdSize = central.reduce((a, b) => a + b.length, 0);
  const e = new DataView(new ArrayBuffer(22));
  e.setUint32(0, 0x06054b50, true); e.setUint16(8, entries.length, true); e.setUint16(10, entries.length, true);
  e.setUint32(12, cdSize, true); e.setUint32(16, off, true);
  return new Blob([...parts, ...central, new Uint8Array(e.buffer)], { type: 'application/zip' });
}
let downloads = null;
(async () => { try { downloads = await window.claude?.use?.('downloads'); } catch { downloads = null; } })();
$('btnExport').onclick = async () => {
  const { mesh, dense } = state.results;
  if (!mesh) return;
  const entries = [['scan.glb', exp.glb(mesh)], ['scan.obj', exp.obj(mesh)], ['scan-mesh.ply', exp.plyMesh(mesh)]];
  if (dense) entries.push(['scan-points.ply', exp.plyPoints(dense)]);
  entries.push(['README.txt', 'Scan3D export. scan.glb: mesh with vertex colours (Blender, web, AR viewers).\nscan.obj: same mesh, colours after each vertex.\nscan-mesh.ply / scan-points.ply: mesh and dense point cloud (MeshLab, CloudCompare).\nUnits are relative.\n']);
  const blob = zip(entries);
  const em = $('exportMsg');
  if (!downloads) { em.textContent = 'Saving files is not available in this view.'; return; }
  try {
    await downloads.save({ filename: 'scan3d-model.zip', data: blob });
    em.textContent = 'Saved scan3d-model.zip.';
  } catch (err) {
    em.textContent = err?.code === 'declined' ? 'Download cancelled.' : `Could not save the file (${err?.code || 'error'}).`;
  }
};

resetSteps();
reset().then(playSample);
