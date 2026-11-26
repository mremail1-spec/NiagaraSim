//! End-to-end check on a rendered scene with known geometry and cameras.

use scan3d::geometry::V3;
use scan3d::pipeline::Pipeline;
use scan3d::synth::*;

#[test]
fn reconstructs_synthetic_orbit() {
    let (w, h) = (400usize, 300usize);
    let k = intrinsics(w, h);
    let poses = orbit(40, 3.2);
    let mut p = Pipeline::new();
    p.opts.dense.max_views = 16;
    p.opts.mesh_res = 128;
    for pose in &poses {
        p.add_frame(&render(pose, &k, w, h), w, h).unwrap();
    }
    let mut rep = |_: f32, _: &str| {};
    p.features(&mut rep);
    p.matching(&mut rep);
    let n = p.sfm(&mut rep).expect("sfm");
    assert!(n >= 36, "only {n}/40 cameras registered");
    let rec = p.rec.as_ref().unwrap();
    assert!((rec.intr.f / k.f - 1.0).abs() < 0.03, "focal {} vs {}", rec.intr.f, k.f);
    let (src, dst): (Vec<V3>, Vec<V3>) =
        rec.poses.iter().enumerate().filter_map(|(i, q)| q.map(|q| (q.center(), poses[i].center()))).unzip();
    let (s, r, t) = umeyama(&src, &dst);
    let cam_err = src.iter().zip(&dst).map(|(a, b)| (r * a * s + t - b).norm()).fold(0.0, f64::max);
    assert!(cam_err < 0.05, "camera error {cam_err}");
    p.dense(&mut rep).unwrap();
    p.mesh(&mut rep).unwrap();
    let m = p.mesh.as_ref().unwrap();
    assert!(m.idx.len() / 3 > 2000, "mesh too small: {} triangles", m.idx.len() / 3);
    let mut d: Vec<f64> = m
        .pos
        .iter()
        .map(|q| surface_distance(&(r * V3::new(q[0] as f64, q[1] as f64, q[2] as f64) * s + t)))
        .collect();
    d.sort_by(|a, b| a.partial_cmp(b).unwrap());
    let med = d[d.len() / 2];
    let p90 = d[d.len() * 9 / 10];
    assert!(med < 0.025 && p90 < 0.12, "mesh error median {med} p90 {p90}");
}

/// Harder scenes (slow): run with `cargo test --release -- --ignored`.
#[test]
#[ignore]
fn glossy_cup_and_street() {
    use scan3d::scenes::*;
    let (w, h) = (640usize, 480usize);
    let k = intrinsics(w, h);
    for (name, scene, poses, min_cams) in [("cup", Scene::cup(), cup_orbit(40), 18usize), ("street", Scene::street(), street_walk(40), 38)] {
        let mut p = Pipeline::new();
        for (i, pose) in poses.iter().enumerate() {
            p.add_frame(&scene.render(pose, &k, w, h, i as u32 + 1), w, h).unwrap();
        }
        let mut rep = |_: f32, _: &str| {};
        p.features(&mut rep);
        p.matching(&mut rep);
        let n = p.sfm(&mut rep).expect("sfm");
        assert!(n >= min_cams, "{name}: only {n} cameras");
        let rec = p.rec.as_ref().unwrap();
        let (src, dst): (Vec<V3>, Vec<V3>) =
            rec.poses.iter().enumerate().filter_map(|(i, q)| q.map(|q| (q.center(), poses[i].center()))).unzip();
        let (s, r, t) = umeyama(&src, &dst);
        let mut e: Vec<f64> = src.iter().zip(&dst).map(|(a, b)| (r * a * s + t - b).norm()).collect();
        e.sort_by(|a, b| a.partial_cmp(b).unwrap());
        let limit = if name == "cup" { 0.05 } else { 1.0 };
        assert!(e[e.len() / 2] < limit, "{name}: median camera error {}", e[e.len() / 2]);
        p.dense(&mut rep).unwrap();
        p.mesh(&mut rep).unwrap();
        assert!(p.mesh.as_ref().unwrap().idx.len() > 3000, "{name}: mesh too small");
    }
}
