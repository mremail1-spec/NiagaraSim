// Runs the Rust/WASM reconstruction off the main thread.
import init, { Scanner } from './pkg/scan3d.js';

const ready = init();

// ---- guided capture: one persistent scanner, re-solved after every change ----
let g = null;
let chain = Promise.resolve();
let pending = 0; // guided messages received but not yet handled
const opts = (s, options = {}) => { for (const [k, v] of Object.entries(options)) s.set_option(k, v); };

async function guided(m) {
  await ready;
  const post = (msg, transfer = []) => self.postMessage(msg, transfer);
  const logcb = (p, msg) => { if (msg) post({ type: 'log', msg }); };
  try {
    if (m.cmd === 'reset') { g?.free(); g = new Scanner(); opts(g, m.options); return; }
    if (!g) { g = new Scanner(); opts(g, m.options); }
    if (m.cmd === 'add') g.add_frame(new Uint8Array(m.frame), m.w, m.h);
    if (m.cmd === 'remove') g.remove_frame(m.i);
    if (m.cmd === 'target') g.set_target(m.u, m.v);
    // Let queued messages arrive, then skip this solve if newer frames are waiting:
    // the next one re-solves everything anyway.
    await new Promise((r) => setTimeout(r, 0));
    const newer = pending > 0;
    if (!newer && ((m.cmd === 'add' && m.solve !== false) || m.cmd === 'remove' || m.cmd === 'target')) {
      const t0 = performance.now();
      const solved = g.frame_count() ? g.update(() => {}) : 0;
      const pos = g.sparse_positions(), col = g.sparse_colors(), cams = g.cameras(), mask = g.registered();
      post({ type: 'guide', seq: m.seq, n: g.frame_count(), solved, mask, cams, target: Array.from(g.target()),
        intr: Array.from(g.intrinsics()), pos, col, ms: performance.now() - t0 }, [pos.buffer, col.buffer, cams.buffer]);
    }
    if (m.cmd === 'finish') {
      opts(g, m.options);
      const stage = (name) => (p, msg) => post({ type: 'progress', stage: name, p, msg });
      const t0 = performance.now();
      // Final pose solve over every frame (live guidance may have skipped solves).
      g.update((p, msg) => { if (msg && /^sfm:|final BA/.test(msg)) post({ type: 'progress', stage: 'sfm', p: 1, msg }); });
      {
        const pos = g.sparse_positions(), col = g.sparse_colors(), cams = g.cameras();
        post({ type: 'guide', seq: m.seq, n: g.frame_count(), solved: Array.from(g.registered()).reduce((a, b) => a + b, 0),
          mask: g.registered(), cams, target: Array.from(g.target()), intr: Array.from(g.intrinsics()), pos, col, ms: 0, final: true },
          [pos.buffer, col.buffer, cams.buffer]);
      }
      g.run_dense(stage('dense'));
      { const pos = g.dense_positions(), col = g.dense_colors(), nrm = g.dense_normals();
        post({ type: 'dense', pos, col, nrm }, [pos.buffer, col.buffer, nrm.buffer]); }
      g.run_mesh(stage('mesh'));
      { const pos = g.mesh_positions(), col = g.mesh_colors(), nrm = g.mesh_normals(), idx = g.mesh_indices();
        post({ type: 'mesh', pos, col, nrm, idx }, [pos.buffer, col.buffer, nrm.buffer, idx.buffer]); }
      post({ type: 'done', seconds: (performance.now() - t0) / 1000 });
    }
  } catch (err) {
    post({ type: 'error', msg: String(err && err.message ? err.message : err) });
  }
}

self.onmessage = async (e) => {
  if (e.data.cmd) {
    pending++;
    chain = chain.then(() => { pending--; return guided(e.data); });
    return;
  }
  const { frames, w, h, options } = e.data;
  await ready;
  const s = new Scanner();
  const post = (msg, transfer = []) => self.postMessage(msg, transfer);
  const stage = (name) => {
    let last = 0;
    return (p, msg) => {
      const now = performance.now();
      if (msg || now - last > 100 || p >= 1) {
        last = now;
        post({ type: 'progress', stage: name, p, msg });
      }
    };
  };
  try {
    for (const [k, v] of Object.entries(options)) s.set_option(k, v);
    for (const f of frames) s.add_frame(new Uint8Array(f), w, h);
    const t0 = performance.now();
    s.run_features(stage('features'));
    s.run_matching(stage('matching'));
    s.run_sfm(stage('sfm'));
    {
      const pos = s.sparse_positions(), col = s.sparse_colors(), cams = s.cameras();
      post({ type: 'sparse', pos, col, cams, intr: Array.from(s.intrinsics()) }, [pos.buffer, col.buffer, cams.buffer]);
    }
    s.run_dense(stage('dense'));
    {
      const pos = s.dense_positions(), col = s.dense_colors(), nrm = s.dense_normals();
      post({ type: 'dense', pos, col, nrm }, [pos.buffer, col.buffer, nrm.buffer]);
    }
    s.run_mesh(stage('mesh'));
    {
      const pos = s.mesh_positions(), col = s.mesh_colors(), nrm = s.mesh_normals(), idx = s.mesh_indices();
      post({ type: 'mesh', pos, col, nrm, idx }, [pos.buffer, col.buffer, nrm.buffer, idx.buffer]);
    }
    post({ type: 'done', seconds: (performance.now() - t0) / 1000 });
  } catch (err) {
    post({ type: 'error', msg: String(err && err.message ? err.message : err) });
  } finally {
    s.free();
  }
};
