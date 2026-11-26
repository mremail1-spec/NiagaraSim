use scan3d::geometry::V3;
use scan3d::pipeline::Pipeline;
use scan3d::synth::*;
fn main() {
    let (w, h) = (480usize, 360usize);
    let k = intrinsics(w, h);
    let mut gt = Vec::new();
    let mut p = Pipeline::new();
    if let Some(v) = std::env::args().nth(1) { p.opts.max_features = v.parse().unwrap(); }
    // like the fake webcam: 3.2 radius, height 1.6 + 0.3 sin(3a), 15° apart
    for i in 0..16 {
        let a = (i as f64 * 15.0).to_radians();
        let pose = look_at(V3::new(3.2 * a.cos(), 1.6 + 0.3 * (3.0 * a).sin(), 3.2 * a.sin()), V3::new(0.0, 0.35, 0.0));
        gt.push(pose.center());
        p.add_frame(&render(&pose, &k, w, h), w, h).unwrap();
        let mut msgs = Vec::new();
        let n = p.update(&mut |_, m| if !m.is_empty() { msgs.push(m.to_string()) });
        if i == 12 {
            let rec = p.rec.as_ref().unwrap();
            let (src, dst): (Vec<V3>, Vec<V3>) = rec.poses.iter().enumerate().filter_map(|(j, q)| q.map(|q| (q.center(), gt[j]))).unzip();
            let (s, r, t) = umeyama(&src, &dst);
            let idx: Vec<usize> = rec.poses.iter().enumerate().filter(|(_, q)| q.is_some()).map(|(j, _)| j).collect();
            for (k2, (a, b)) in src.iter().zip(&dst).enumerate() { print!("{}:{:.2} ", idx[k2], (r * a * s + t - b).norm()); }
            println!(" | focal {:.0}", rec.intr.f);
        }
        if i == 15 { for pm in &p.pairs { if pm.j >= 10 && pm.j - pm.i == 1 { print!("{}-{}:{} ", pm.i, pm.j, pm.matches.len()); } } println!(); }
        println!("photo {:2}: {:2} registered   {}", i + 1, n, msgs.iter().filter(|m| m.starts_with("registered image") && m.contains("neighbour") || m.starts_with("image")).cloned().collect::<Vec<_>>().join(""));
    }
}
