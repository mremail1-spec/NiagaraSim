import { Viewer } from './viewer.js';
import * as exp from './export.js';

const $ = (id) => document.getElementById(id);
const MAX = 120, LONG = 800;
const state = { frames: [], w: 0, h: 0, results: {}, worker: null };
const viewer = new Viewer($('view'));

// ---------- frames ----------
const work = document.createElement('canvas');
const wctx = work.getContext('2d', { willReadFrequently: true });

function addFrame(src, sw0, sh0, thumbUrl) {
  if (state.frames.length >= MAX) { msg(`Limit of ${MAX} photos reached`, true); return; }
  let w = state.w, h = state.h;
  if (!state.frames.length) {
    const s = Math.min(1, LONG / Math.max(sw0, sh0));
    w = Math.round(sw0 * s) & ~1; h = Math.round(sh0 * s) & ~1;
  }
  work.width = w; work.height = h;
  const sa = sw0 / sh0, ta = w / h;
  let sx = 0, sy = 0, sw = sw0, sh = sh0;
  if (sa > ta) { sw = sh0 * ta; sx = (sw0 - sw) / 2; } else if (sa < ta) { sh = sw0 / ta; sy = (sh0 - sh) / 2; }
  wctx.drawImage(src, sx, sy, sw, sh, 0, 0, w, h);
  state.w = w; state.h = h;
  state.frames.push(wctx.getImageData(0, 0, w, h).data);
  const img = new Image();
  img.alt = `photo ${state.frames.length}`;
  img.src = thumbUrl || work.toDataURL('image/jpeg', 0.5);
  $('strip').appendChild(img);
  updateInfo();
}
function clearFrames() { state.frames = []; state.w = state.h = 0; $('strip').replaceChildren(); updateInfo(); }
function updateInfo() {
  const n = state.frames.length;
  $('frameInfo').textContent = n ? `${n} photo${n === 1 ? '' : 's'} at ${state.w}×${state.h}` : 'No photos yet';
  $('btnBuild').disabled = n < 3 || !!state.worker;
}
function msg(t, warn = false) { $('msg').textContent = t; $('msg').className = 'msg' + (warn ? ' warn' : ''); }

async function loadSample() {
  clearFrames();
  overlay('Loading the sample scan…', '40 photos taken around a small tabletop scene');
  for (let i = 0; i < 40; i++) {
    const url = `sample/frame_${String(i).padStart(2, '0')}.jpg`;
    const blob = await (await fetch(url)).blob();
    const bmp = await createImageBitmap(blob);
    addFrame(bmp, bmp.width, bmp.height, url);
    bmp.close();
  }
  msg('Sample loaded: a ray-traced scene with known geometry, so results can be checked.');
}

$('file').onchange = async (e) => {
  const files = [...e.target.files];
  e.target.value = '';
  if (!files.length) return;
  if (state.worker) { state.worker.terminate(); state.worker = null; }
  clearFrames();
  for (const f of files) {
    if (f.type.startsWith('video/')) await importVideo(f);
    else {
      try {
        const bmp = await createImageBitmap(f, { imageOrientation: 'from-image' });
        addFrame(bmp, bmp.width, bmp.height);
        bmp.close();
      } catch { msg(`Could not read ${f.name}. Use JPEG or PNG photos.`, true); }
    }
  }
  if (state.frames.length >= 3) msg('Ready. Press Build 3D model.');
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
async function importVideo(file) {
  const v = document.createElement('video');
  v.muted = true; v.playsInline = true; v.preload = 'auto';
  v.src = URL.createObjectURL(file);
  try { await new Promise((r, j) => { v.onloadeddata = r; v.onerror = j; }); }
  catch { msg(`This browser cannot decode ${file.name}. Try an MP4 or extract photos.`, true); return; }
  const dur = v.duration;
  const want = Math.min(MAX - state.frames.length, Math.max(10, Math.round(dur * 2.5)));
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
    addFrame(v, v.videoWidth, v.videoHeight);
    msg(`Extracting video frames ${i + 1}/${want}`);
  }
  URL.revokeObjectURL(v.src);
}

// ---------- build ----------
const STEPS = [['features', 'Detect features'], ['matching', 'Match photos'], ['sfm', 'Solve camera poses'], ['dense', 'Depth maps'], ['mesh', 'Build mesh']];
function resetSteps() {
  $('steps').replaceChildren(...STEPS.map(([k, t]) => {
    const li = document.createElement('li'); li.id = 'st-' + k;
    li.innerHTML = `${t}<span></span><div class="bar"><i></i></div>`;
    return li;
  }));
  $('log').textContent = '';
}
function log(t) { $('log').textContent += t + '\n'; $('log').scrollTop = 1e9; }
function overlay(title, sub) {
  const o = $('overlay');
  if (!title) { o.hidden = true; return; }
  o.hidden = false;
  const s = document.createElement('strong'); s.textContent = title;
  const d = document.createElement('div'); d.append(s, sub || '');
  o.replaceChildren(d);
}

function build() {
  if (state.frames.length < 3) return;
  state.worker?.terminate();
  resetSteps();
  viewer.clear();
  state.results = {};
  $('btnExport').disabled = true;
  overlay('Reconstructing…', 'camera positions appear first, then the point cloud and the mesh');
  let worker;
  try { worker = state.worker = new Worker('worker.js', { type: 'module' }); }
  catch (e) { log('Could not start the background worker: ' + e.message); return; }
  updateInfo();
  const t0 = performance.now();
  const options = { fov_deg: +$('optFov').value || 65, dense_size: +$('optDense').value, mesh_res: +$('optMesh').value };
  log(`${state.frames.length} photos at ${state.w}×${state.h}`);
  const frames = state.frames.map((f) => f.slice().buffer);
  worker.postMessage({ frames, w: state.w, h: state.h, options }, frames);
  let current = null;
  const finish = () => { worker.terminate(); if (state.worker === worker) state.worker = null; updateInfo(); };
  worker.onmessage = ({ data: m }) => {
    if (m.type === 'progress') {
      if (current !== m.stage) { if (current) $('st-' + current).classList.add('done'); current = m.stage; }
      const li = $('st-' + m.stage);
      li.querySelector('i').style.width = `${Math.round(m.p * 100)}%`;
      li.querySelector('span').textContent = `${((performance.now() - t0) / 1000).toFixed(0)} s`;
      if (m.msg) log(m.msg);
    } else if (m.type === 'sparse') {
      state.results.sparse = m;
      overlay(null);
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
      if (m.idx.length) { viewer.setMesh(m.pos, m.col, m.nrm, m.idx); setMode('mesh'); $('btnExport').disabled = false; }
    } else if (m.type === 'done') {
      $('st-' + current).classList.add('done');
      log(`Finished in ${m.seconds.toFixed(1)} s`);
      finish();
    } else if (m.type === 'error') {
      log('Stopped: ' + m.msg);
      overlay('Could not reconstruct these photos', m.msg);
      finish();
    }
  };
  worker.onerror = (e) => {
    log('The engine failed to load: ' + (e.message || 'WebAssembly may be blocked in this view'));
    overlay('The 3D engine could not start', 'WebAssembly or module workers may be blocked here');
    finish();
  };
}
$('btnBuild').onclick = build;
$('btnSample').onclick = async () => { state.worker?.terminate(); state.worker = null; await loadSample(); build(); };

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
loadSample().then(build).catch((e) => { overlay('Could not load the sample photos', String(e)); });
