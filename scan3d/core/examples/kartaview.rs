//! Reconstruct a KartaView street sequence prepared as PPM frames + meta.json.
//! cargo run --release --example kartaview -- <dir> [--fov deg]
use scan3d::geometry::V3;
use scan3d::pipeline::Pipeline;
use std::time::Instant;

fn main() {
    let args: Vec<String> = std::env::args().collect();
    let dir = &args[1];
    let meta: Vec<(String, f64, f64, f64)> = {
        // tiny parser for [{"file":..,"lat":..,"lng":..,"acc":..}, ...]
        let txt = std::fs::read_to_string(format!("{dir}/frames.txt")).unwrap();
        txt.lines().map(|l| { let v: Vec<&str> = l.split_whitespace().collect(); (v[0].to_string(), v[1].parse().unwrap(), v[2].parse().unwrap(), v[3].parse().unwrap()) }).collect()
    };
    let (lat0, lng0) = (meta[0].1, meta[0].2);
    let mut p = Pipeline::new();
    if let Some(i) = args.iter().position(|a| a == "--fov") { p.opts.fov_deg = args[i + 1].parse().unwrap(); p.opts.fov_known = true; }
    p.opts.mesh_res = 280;
    p.opts.dense.size = 448;
    for (k, (f, lat, lng, acc)) in meta.iter().enumerate() {
        let b = std::fs::read(format!("{dir}/{f}")).unwrap();
        let hdr = String::from_utf8_lossy(&b[..20]).to_string();
        let parts: Vec<&str> = hdr.split_whitespace().collect();
        let (w, h): (usize, usize) = (parts[1].parse().unwrap(), parts[2].parse().unwrap());
        let off = b.len() - w * h * 3;
        let rgba: Vec<u8> = b[off..].chunks(3).flat_map(|c| [c[0], c[1], c[2], 255]).collect();
        p.add_frame(&rgba, w, h).unwrap();
        // local metres: x east, y up, z south
        let x = (lng - lng0) * 111_320.0 * lat0.to_radians().cos();
        let z = -(lat - lat0) * 110_540.0;
        p.gps.resize(k + 1, None);
        p.gps[k] = Some((V3::new(x, 0.0, z), *acc));
    }
    println!("{} frames {}x{}", p.rgba.len(), p.w, p.h);
    let t0 = Instant::now();
    let mut rep = |_: f32, m: &str| if !m.is_empty() && !m.starts_with("registered image") { println!("  {m}") };
    p.features(&mut rep);
    p.matching(&mut rep);
    println!("matching done {:.0}s", t0.elapsed().as_secs_f64());
    p.sfm(&mut rep).expect("sfm");
    println!("sfm done {:.0}s", t0.elapsed().as_secs_f64());
    p.dense(&mut rep).unwrap();
    p.mesh(&mut rep).unwrap();
    println!("total {:.0}s", t0.elapsed().as_secs_f64());
    let m = p.mesh.as_ref().unwrap();
    let c = p.cloud.as_ref().unwrap();
    let rec = p.rec.as_ref().unwrap();
    let cams: Vec<f32> = rec.poses.iter().flatten().flat_map(|q| { let c = q.center(); [c.x as f32, c.y as f32, c.z as f32] }).collect();
    let j = format!("{{\"pos\":{:?},\"col\":{:?},\"idx\":{:?},\"dpos\":{:?},\"dcol\":{:?},\"cams\":{:?},\"spos\":{:?},\"scol\":{:?}}}",
        m.pos.iter().flatten().collect::<Vec<_>>(), m.col.iter().flatten().collect::<Vec<_>>(), m.idx,
        c.pos.iter().flatten().collect::<Vec<_>>(), c.col.iter().flatten().collect::<Vec<_>>(), cams,
        rec.points.iter().flat_map(|q| [q.x.x as f32, q.x.y as f32, q.x.z as f32]).collect::<Vec<_>>(),
        rec.points.iter().flat_map(|q| q.color).collect::<Vec<_>>());
    std::fs::write(format!("{dir}/result.json"), j).unwrap();
}
