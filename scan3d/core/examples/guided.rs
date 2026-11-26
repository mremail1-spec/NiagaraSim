//! Simulate guided capture: frames arrive one at a time; re-solve after each.
use scan3d::pipeline::Pipeline;
use scan3d::synth::*;
use std::time::Instant;
fn main() {
    let (w, h) = (480usize, 360usize);
    let k = intrinsics(w, h);
    let mut p = Pipeline::new();
    let laps: usize = std::env::args().nth(1).and_then(|a| a.parse().ok()).unwrap_or(1);
    let poses: Vec<_> = (0..laps).flat_map(|_| orbit(40, 3.2)).collect();
    for (i, pose) in poses.iter().enumerate() {
        p.add_frame(&render(pose, &k, w, h), w, h).unwrap();
        let t = Instant::now();
        let n = p.update(&mut |_, _| {});
        let tp = p.target_point().map(|t| format!("{:.2},{:.2},{:.2}", t.x, t.y, t.z)).unwrap_or_default();
        if i % 10 != 9 { continue; }
        println!("photo {:2}: {:2} registered in {:.2}s target [{}]", i + 1, n, t.elapsed().as_secs_f64(), tp);
    }
}
