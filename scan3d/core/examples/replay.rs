//! Replay dumped frames (PPM) through guided updates.
use scan3d::pipeline::Pipeline;
fn main() {
    let dir = std::env::args().nth(1).unwrap();
    let mut files: Vec<_> = std::fs::read_dir(&dir).unwrap().map(|e| e.unwrap().path()).collect();
    files.sort();
    let mut p = Pipeline::new();
    if let Some(v) = std::env::args().nth(2) { p.opts.fov_deg = v.parse().unwrap(); }
    for (i, f) in files.iter().enumerate() {
        let b = std::fs::read(f).unwrap();
        let hdr = String::from_utf8_lossy(&b[..20]).to_string();
        let parts: Vec<&str> = hdr.split_whitespace().collect();
        let (w, h): (usize, usize) = (parts[1].parse().unwrap(), parts[2].parse().unwrap());
        let off = b.len() - w * h * 3;
        let rgba: Vec<u8> = b[off..].chunks(3).flat_map(|c| [c[0], c[1], c[2], 255]).collect();
        p.add_frame(&rgba, w, h).unwrap();
        let mut msgs = Vec::new();
        let n = p.update(&mut |_, m| if !m.is_empty() { msgs.push(m.to_string()) });
        if i + 1 == files.len() {
            let mut v: Vec<_> = p.pairs.iter().filter(|q| q.j - q.i <= 2).map(|q| format!("{}-{}:{}", q.i, q.j, q.matches.len())).collect();
            v.sort(); println!("pairs {}", v.join(" "));
            for m in &msgs { println!("   {m}"); }
            println!("placed mask {:?}", (0..files.len()).map(|k| p.rec.as_ref().map_or(0, |r| r.poses[k].is_some() as u8)).collect::<Vec<_>>());
        }
        println!("frame {:2}: {:2} placed  {}", i + 1, n, msgs.join(" ; ").chars().take(4000).collect::<String>());
    }
}
