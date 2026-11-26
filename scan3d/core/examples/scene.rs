//! Reconstruct a harder synthetic scene and report accuracy/completeness.
//! cargo run --release --example scene -- cup|street [views] [--save dir]
use scan3d::geometry::V3;
use scan3d::pipeline::Pipeline;
use scan3d::scenes::*;
use scan3d::synth::{intrinsics, umeyama};
use std::time::Instant;

fn main() {
    let args: Vec<String> = std::env::args().collect();
    let which = args.get(1).map(String::as_str).unwrap_or("cup");
    let n: usize = args.get(2).and_then(|s| s.parse().ok()).unwrap_or(40);
    let save = args.iter().position(|a| a == "--save").map(|i| args[i + 1].clone());
    let (scene, poses) = match which {
        "street" => (Scene::street(), street_walk(n)),
        "facades" => (Scene::street(), street_sideways(n)),
        _ => (Scene::cup(), cup_orbit(n)),
    };
    let (w, h) = (640usize, 480usize);
    let k = intrinsics(w, h);
    let mut p = Pipeline::new();
    if let Ok(v) = std::env::var("FOV") { p.opts.fov_deg = v.parse().unwrap(); p.opts.fov_known = true; }
    for (i, pose) in poses.iter().enumerate() {
        let img = scene.render(pose, &k, w, h, i as u32 + 1);
        if let Some(dir) = &save {
            std::fs::create_dir_all(dir).unwrap();
            let mut out = format!("P6\n{w} {h}\n255\n").into_bytes();
            for px in img.chunks(4) { out.extend_from_slice(&px[..3]); }
            std::fs::write(format!("{dir}/f{i:03}.ppm"), out).unwrap();
        }
        p.add_frame(&img, w, h).unwrap();
    }
    let t0 = Instant::now();
    let verbose = std::env::var("V").is_ok();
    let mut rep = |_: f32, m: &str| if verbose && !m.is_empty() || m.starts_with("sfm:") || m.starts_with("dense:") || m.starts_with("mesh:") || m.starts_with("final") { println!("  {m}") };
    p.features(&mut rep);
    p.matching(&mut rep);
    if p.sfm(&mut rep).is_err() { println!("SFM FAILED"); return; }
    let rec = p.rec.as_ref().unwrap();
    let (src, dst): (Vec<V3>, Vec<V3>) = rec.poses.iter().enumerate().filter_map(|(i, q)| q.map(|q| (q.center(), poses[i].center()))).unzip();
    let (s, r, t) = umeyama(&src, &dst);
    let err = src.iter().zip(&dst).map(|(a, b)| (r * a * s + t - b).norm()).sum::<f64>() / src.len() as f64;
    if let Ok(core) = std::env::var("ALIGN") {
        let (a0, a1): (usize, usize) = { let v: Vec<usize> = core.split('-').map(|x| x.parse().unwrap()).collect(); (v[0], v[1]) };
        let (cs, cd): (Vec<V3>, Vec<V3>) = (a0..=a1).filter_map(|i| rec.poses[i].map(|q| (q.center(), poses[i].center()))).unzip();
        let (s2, r2, t2) = umeyama(&cs, &cd);
        for (i, q) in rec.poses.iter().enumerate() { if let Some(q) = q { let e = (r2 * q.center() * s2 + t2 - poses[i].center()).norm(); print!("{i}:{e:.2} "); } }
        println!();
    }
    if std::env::var("PERCAM").is_ok() {
        let idx: Vec<usize> = rec.poses.iter().enumerate().filter(|(_, q)| q.is_some()).map(|(i, _)| i).collect();
        for (k2, (a, b)) in src.iter().zip(&dst).enumerate() { let q = r * a * s + t; println!("  cam {:2}: gt ({:5.2},{:5.2},{:5.2}) rec ({:5.2},{:5.2},{:5.2})", idx[k2], b.x, b.y, b.z, q.x, q.y, q.z); }
    }
    let mut errs: Vec<f64> = src.iter().zip(&dst).map(|(a, b)| (r * a * s + t - b).norm()).collect();
    errs.sort_by(|a, b| a.partial_cmp(b).unwrap());
    let _ = err;
    println!("cameras {}/{} centre error median {:.4} max {:.4}, focal {:.1} (true {:.1})", src.len(), n, errs[errs.len() / 2], errs[errs.len() - 1], rec.intr.f, k.f);
    let to_gt = |q: &[f32; 3]| r * V3::new(q[0] as f64, q[1] as f64, q[2] as f64) * s + t;
    p.dense(&mut rep).unwrap();
    p.mesh(&mut rep).unwrap();
    let m = p.mesh.as_ref().unwrap();
    let mut d: Vec<f64> = m.pos.iter().map(|q| scene.surface_distance(&to_gt(q))).collect();
    d.sort_by(|a, b| a.partial_cmp(b).unwrap());
    let unit = if which == "street" || which == "facades" { 0.10 } else { 0.01 };
    if !d.is_empty() {
        println!("mesh accuracy: median {:.4}, p90 {:.4}, within {}: {:.1}%", d[d.len() / 2], d[d.len() * 9 / 10], unit,
            100.0 * d.iter().filter(|&&x| x < unit).count() as f64 / d.len() as f64);
    }
    // completeness of the main subject: sample its surface, find nearest mesh vertex
    let pts: Vec<V3> = m.pos.iter().map(to_gt).collect();
    let samples: Vec<V3> = if which == "street" || which == "facades" {
        // building facades facing the street, up to 6 m high, along the walked stretch
        (0..400).map(|i| { let z = (i % 40) as f64 * 0.5; let y = 0.5 + (i / 40) as f64 * 0.55; V3::new(if i % 2 == 0 { -5.0 } else { 5.0 }, y, z) }).collect()
    } else {
        // cup side wall
        (0..400).map(|i| { let a = (i % 40) as f64 / 40.0 * std::f64::consts::TAU; let y = 0.02 + (i / 40) as f64 * 0.04; V3::new(0.25 * a.cos(), y, 0.25 * a.sin()) }).collect()
    };
    let covered = samples.iter().filter(|q| pts.iter().any(|p| (p - *q).norm() < unit * 3.0)).count();
    println!("subject completeness: {:.0}% of {} surface samples have mesh within {}", 100.0 * covered as f64 / samples.len() as f64, which, unit * 3.0);
    println!("time {:.1}s", t0.elapsed().as_secs_f64());
    if let Some(dir) = &save {
        let mut s = String::new();
        for (q, c) in m.pos.iter().zip(&m.col) { s += &format!("v {} {} {} {} {} {}\n", q[0], q[1], q[2], c[0] as f32 / 255.0, c[1] as f32 / 255.0, c[2] as f32 / 255.0); }
        for tr in m.idx.chunks(3) { s += &format!("f {} {} {}\n", tr[0] + 1, tr[1] + 1, tr[2] + 1); }
        std::fs::write(format!("{dir}/mesh.obj"), s).unwrap();
        let c = p.cloud.as_ref().unwrap();
        let j = format!("{{\"pos\":{:?},\"col\":{:?},\"idx\":{:?},\"dpos\":{:?},\"dcol\":{:?}}}",
            m.pos.iter().flatten().collect::<Vec<_>>(), m.col.iter().flatten().collect::<Vec<_>>(), m.idx,
            c.pos.iter().step_by(3).flatten().collect::<Vec<_>>(), c.col.iter().step_by(3).flatten().collect::<Vec<_>>());
        std::fs::write(format!("{dir}/mesh.json"), j).unwrap();
    }
}
