// Runs the Rust/WASM reconstruction off the main thread.
import init, { Scanner } from './pkg/scan3d.js';

const ready = init();

self.onmessage = async (e) => {
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
