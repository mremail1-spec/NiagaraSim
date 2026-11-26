//! Render the synthetic scene, reconstruct it, and report accuracy.
//! `cargo run --release --example synthetic [views] [--y4m out.y4m frames]`

use scan3d::geometry::V3;
use scan3d::pipeline::Pipeline;
use scan3d::synth::*;
use std::io::Write;
use std::time::Instant;

fn main() {
    let args: Vec<String> = std::env::args().collect();
    let (w, h) = (480usize, 360usize);
    let k = intrinsics(w, h);
    if args.len() >= 4 && args[1] == "--y4m" {
        // Smooth orbit video for the browser's fake-camera test.
        let frames: usize = args[3].parse().unwrap();
        let mut out = std::fs::File::create(&args[2]).unwrap();
        writeln!(out, "YUV4MPEG2 W{w} H{h} F30:1 Ip A1:1 C420jpeg").unwrap();
        for i in 0..frames {
            let a = i as f64 / frames as f64 * std::f64::consts::TAU;
            let hgt = 1.6 + 0.3 * (a * 3.0).sin();
            let pose = look_at(V3::new(3.2 * a.cos(), hgt, 3.2 * a.sin()), V3::new(0.0, 0.35, 0.0));
            let img = render(&pose, &k, w, h);
            let mut yp = vec![0u8; w * h];
            let mut up = vec![0u8; w * h / 4];
            let mut vp = vec![0u8; w * h / 4];
            for y in 0..h {
                for x in 0..w {
                    let i = 4 * (y * w + x);
                    let (r, g, b) = (img[i] as f64, img[i + 1] as f64, img[i + 2] as f64);
                    yp[y * w + x] = (0.299 * r + 0.587 * g + 0.114 * b).round() as u8;
                    if x % 2 == 0 && y % 2 == 0 {
                        let j = (y / 2) * (w / 2) + x / 2;
                        up[j] = (128.0 - 0.168736 * r - 0.331264 * g + 0.5 * b).round().clamp(0.0, 255.0) as u8;
                        vp[j] = (128.0 + 0.5 * r - 0.418688 * g - 0.081312 * b).round().clamp(0.0, 255.0) as u8;
                    }
                }
            }
            out.write_all(b"FRAME\n").unwrap();
            out.write_all(&yp).unwrap();
            out.write_all(&up).unwrap();
            out.write_all(&vp).unwrap();
        }
        return;
    }
    if args.len() >= 4 && args[1] == "--ppm" {
        // Still frames of the orbit, for a demo sample set.
        let n: usize = args[3].parse().unwrap();
        std::fs::create_dir_all(&args[2]).unwrap();
        for (i, pose) in orbit(n, 3.2).iter().enumerate() {
            let img = render(pose, &k, w, h);
            let mut out = format!("P6\n{w} {h}\n255\n").into_bytes();
            for px in img.chunks(4) { out.extend_from_slice(&px[..3]); }
            std::fs::write(format!("{}/frame_{:02}.ppm", args[2], i), out).unwrap();
        }
        return;
    }
    let n: usize = args.get(1).and_then(|s| s.parse().ok()).unwrap_or(30);
    let poses = orbit(n, 3.2);
    let t0 = Instant::now();
    let mut p = Pipeline::new();
    for pose in &poses {
        p.add_frame(&render(pose, &k, w, h), w, h).unwrap();
    }
    println!("rendered {n} views in {:.1}s", t0.elapsed().as_secs_f64());
    let mut rep = |_: f32, m: &str| if !m.is_empty() { println!("  {m}") };
    let t = Instant::now();
    p.features(&mut rep);
    p.matching(&mut rep);
    println!("features+matching {:.1}s", t.elapsed().as_secs_f64());
    let t = Instant::now();
    p.sfm(&mut rep).expect("sfm failed");
    println!("sfm {:.1}s", t.elapsed().as_secs_f64());
    let rec = p.rec.as_ref().unwrap();
    let (src, dst): (Vec<V3>, Vec<V3>) = rec
        .poses
        .iter()
        .enumerate()
        .filter_map(|(i, q)| q.map(|q| (q.center(), poses[i].center())))
        .unzip();
    let (s, r, tt) = umeyama(&src, &dst);
    let cam_err: Vec<f64> = src.iter().zip(&dst).map(|(a, b)| (r * a * s + tt - b).norm()).collect();
    let mean_cam = cam_err.iter().sum::<f64>() / cam_err.len() as f64;
    println!("camera centre error: mean {:.4} (orbit radius 3.2), focal {:.1} (true {:.1})", mean_cam, rec.intr.f, k.f);
    let to_gt = |q: &V3| r * q * s + tt;
    let sparse: Vec<f64> = rec.points.iter().map(|q| surface_distance(&to_gt(&q.x))).collect();
    println!("sparse: median dist to surface {:.4}", median(sparse));
    let t = Instant::now();
    p.dense(&mut rep).unwrap();
    println!("dense {:.1}s", t.elapsed().as_secs_f64());
    let c = p.cloud.as_ref().unwrap();
    let d: Vec<f64> = c.pos.iter().map(|q| surface_distance(&to_gt(&V3::new(q[0] as f64, q[1] as f64, q[2] as f64)))).collect();
    let frac = d.iter().filter(|&&x| x < 0.02).count() as f64 / d.len() as f64;
    println!("dense: median dist {:.4}, {:.1}% within 2cm", median(d), 100.0 * frac);
    let t = Instant::now();
    p.mesh(&mut rep).unwrap();
    println!("mesh {:.1}s", t.elapsed().as_secs_f64());
    let m = p.mesh.as_ref().unwrap();
    let d: Vec<f64> = m.pos.iter().map(|q| surface_distance(&to_gt(&V3::new(q[0] as f64, q[1] as f64, q[2] as f64)))).collect();
    let frac = d.iter().filter(|&&x| x < 0.03).count() as f64 / d.len().max(1) as f64;
    // orientation: normals should point toward free space (sample a step along the normal)
    let mut outward = 0usize;
    for (q, nn) in m.pos.iter().zip(&m.nrm) {
        let a = to_gt(&V3::new(q[0] as f64, q[1] as f64, q[2] as f64));
        let n = r * V3::new(nn[0] as f64, nn[1] as f64, nn[2] as f64);
        let sky = a + n * 0.05;
        if sky.y > a.y - 0.01 || surface_distance(&sky) > 0.02 { outward += 1; }
    }
    println!("mesh: median dist {:.4}, {:.1}% within 3cm, total {:.1}s, normals plausibly outward {:.0}%",
        median(d), 100.0 * frac, t0.elapsed().as_secs_f64(), 100.0 * outward as f64 / m.pos.len().max(1) as f64);
    std::fs::write("/tmp/scan3d_mesh.obj", {
        let mut s = String::new();
        for (q, c) in m.pos.iter().zip(&m.col) { s += &format!("v {} {} {} {} {} {}\n", q[0], q[1], q[2], c[0] as f32/255.0, c[1] as f32/255.0, c[2] as f32/255.0); }
        for t in m.idx.chunks(3) { s += &format!("f {} {} {}\n", t[0]+1, t[1]+1, t[2]+1); }
        s
    }).ok();
}

fn median(mut v: Vec<f64>) -> f64 {
    if v.is_empty() { return f64::NAN; }
    v.sort_by(|a, b| a.partial_cmp(b).unwrap());
    v[v.len() / 2]
}
