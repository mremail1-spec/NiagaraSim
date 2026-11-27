//! Multi-view stereo: per-view plane-sweep depth maps scored with windowed
//! ZNCC, geometric consistency filtering, and fusion into a coloured cloud.

use crate::geometry::{Intrinsics, Pose, V3};
use crate::image::{sample_rgb, Gray};
use crate::sfm::Reconstruction;

#[derive(Clone)]
pub struct DepthMap {
    pub w: usize,
    pub h: usize,
    /// low-res pixels per full-res pixel
    pub s: f64,
    pub depth: Vec<f32>,
    pub sources: Vec<usize>,
}

pub struct DenseOptions {
    pub size: usize,
    pub depths: usize,
    pub sources: usize,
    pub min_zncc: f32,
    pub radius: usize,
    /// at most this many reference views get a depth map (evenly spaced)
    pub max_views: usize,
}

impl Default for DenseOptions {
    fn default() -> Self {
        DenseOptions { size: 320, depths: 64, sources: 3, min_zncc: 0.5, radius: 3, max_views: 32 }
    }
}

impl DepthMap {
    #[inline]
    pub fn ray(&self, k: &Intrinsics, x: usize, y: usize) -> V3 {
        let u = (x as f64 + 0.5) / self.s - 0.5;
        let v = (y as f64 + 0.5) / self.s - 0.5;
        let n = k.normalize(u, v);
        V3::new(n[0], n[1], 1.0)
    }
    /// Project a world point to low-res pixel coordinates and camera depth.
    #[inline]
    pub fn project(&self, k: &Intrinsics, pose: &Pose, x: &V3) -> Option<(f64, f64, f64)> {
        let xc = pose.to_cam(x);
        let q = k.project_cam(&xc)?;
        Some(((q.x + 0.5) * self.s - 0.5, (q.y + 0.5) * self.s - 0.5, xc.z))
    }
    #[inline]
    pub fn at(&self, x: f64, y: f64) -> Option<(usize, usize, f32)> {
        let xi = x.round();
        let yi = y.round();
        if xi < 0.0 || yi < 0.0 || xi >= self.w as f64 || yi >= self.h as f64 {
            return None;
        }
        let (xi, yi) = (xi as usize, yi as usize);
        let d = self.depth[yi * self.w + xi];
        if d.is_finite() { Some((xi, yi, d)) } else { None }
    }
}

fn box_sum(src: &[f32], w: usize, h: usize, r: usize, tmp: &mut [f32], out: &mut [f32]) {
    for y in 0..h {
        let row = &src[y * w..(y + 1) * w];
        let mut acc = 0.0f32;
        for x in 0..=r.min(w - 1) {
            acc += row[x];
        }
        for x in 0..w {
            tmp[y * w + x] = acc;
            if x + r + 1 < w {
                acc += row[x + r + 1];
            }
            if x >= r {
                acc -= row[x - r];
            }
        }
    }
    for x in 0..w {
        let mut acc = 0.0f32;
        for y in 0..=r.min(h - 1) {
            acc += tmp[y * w + x];
        }
        for y in 0..h {
            out[y * w + x] = acc;
            if y + r + 1 < h {
                acc += tmp[(y + r + 1) * w + x];
            }
            if y >= r {
                acc -= tmp[(y - r) * w + x];
            }
        }
    }
}

/// Pick neighbouring views with good overlap and baseline for each image.
fn select_sources(rec: &Reconstruction, n: usize, refs: &[bool]) -> Vec<Vec<usize>> {
    let m = rec.poses.len();
    let mut cnt = vec![0usize; m * m];
    let mut ang = vec![0.0f64; m * m];
    for p in &rec.points {
        for a in 0..p.obs.len() {
            for b in 0..p.obs.len() {
                if a == b {
                    continue;
                }
                let (i, j) = (p.obs[a].0, p.obs[b].0);
                let ci = rec.poses[i].unwrap().center();
                let cj = rec.poses[j].unwrap().center();
                let t = (p.x - ci).normalize().dot(&(p.x - cj).normalize()).clamp(-1.0, 1.0).acos();
                cnt[i * m + j] += 1;
                ang[i * m + j] += t;
            }
        }
    }
    (0..m)
        .map(|i| {
            let mut c: Vec<(usize, f64)> = (0..m)
                .filter(|&j| j != i && refs[j] && cnt[i * m + j] >= 10)
                .map(|j| {
                    let a = (ang[i * m + j] / cnt[i * m + j] as f64).to_degrees();
                    let wa = if a < 4.0 { a / 4.0 } else if a > 35.0 { (35.0 / a).powi(2) } else { 1.0 };
                    (j, cnt[i * m + j] as f64 * wa)
                })
                .collect();
            c.sort_by(|a, b| b.1.partial_cmp(&a.1).unwrap());
            c.into_iter().take(n).map(|x| x.0).collect()
        })
        .collect()
}

pub fn compute_depth_maps(
    grays: &[Gray],
    rec: &Reconstruction,
    opt: &DenseOptions,
    progress: &mut dyn FnMut(f32),
) -> Vec<Option<DepthMap>> {
    let k = rec.intr;
    let m = rec.poses.len();
    let full_w = grays[0].w;
    let full_h = grays[0].h;
    let s = (opt.size as f64 / full_w.max(full_h) as f64).min(1.0);
    let w = ((full_w as f64 * s).round() as usize).max(8);
    let h = ((full_h as f64 * s).round() as usize).max(8);
    let s = w as f64 / full_w as f64;
    let reg: Vec<usize> = (0..m).filter(|&i| rec.poses[i].is_some()).collect();
    let mut refs = vec![false; m];
    let nv = reg.len().min(opt.max_views.max(2));
    for k in 0..nv {
        refs[reg[k * reg.len() / nv]] = true;
    }
    let lo: Vec<Option<Gray>> = (0..m)
        .map(|i| rec.poses[i].filter(|_| refs[i]).map(|_| grays[i].downscale(1.0 / s as f32)).map(|g| if g.w == w && g.h == h { g } else { g.resize(w, h) }))
        .collect();
    let sources = select_sources(rec, opt.sources, &refs);
    let n = w * h;
    let r = opt.radius;
    let area = ((2 * r + 1) * (2 * r + 1)) as f32;
    let mut out: Vec<Option<DepthMap>> = vec![None; m];
    let mut tmp = vec![0.0f32; n];
    for i in 0..m {
        progress(i as f32 / m as f32);
        let (Some(pose), Some(rimg)) = (rec.poses[i], lo[i].as_ref()) else { continue };
        let srcs: Vec<usize> = sources[i].clone();
        if srcs.is_empty() {
            continue;
        }
        let mut zs: Vec<f64> = rec
            .points
            .iter()
            .filter(|p| p.obs.iter().any(|o| o.0 == i))
            .map(|p| pose.to_cam(&p.x).z)
            .filter(|z| *z > 0.0)
            .collect();
        if zs.len() < 10 {
            continue;
        }
        zs.sort_by(|a, b| a.partial_cmp(b).unwrap());
        let z_near = zs[zs.len() * 2 / 100] * 0.75;
        let z_far = zs[zs.len() * 98 / 100] * 1.3;
        let (inv_far, inv_near) = (1.0 / z_far, 1.0 / z_near);
        let dm0 = DepthMap { w, h, s, depth: vec![], sources: srcs.clone() };
        let rays: Vec<V3> = (0..n).map(|p| dm0.ray(&k, p % w, p / w)).collect();
        // Reference window statistics.
        let rr: Vec<f32> = rimg.data.iter().map(|v| v * v).collect();
        let mut sr = vec![0.0f32; n];
        let mut srr = vec![0.0f32; n];
        box_sum(&rimg.data, w, h, r, &mut tmp, &mut sr);
        box_sum(&rr, w, h, r, &mut tmp, &mut srr);
        let rel: Vec<(Vec<V3>, V3, Pose)> = srcs
            .iter()
            .map(|&j| {
                let pj = rec.poses[j].unwrap();
                let r_rel = pj.r * pose.r.transpose();
                let t_rel = pj.t - r_rel * pose.t;
                (rays.iter().map(|ray| r_rel * ray).collect(), t_rel, pj)
            })
            .collect();
        let d = opt.depths.max(3);
        let mut best = vec![-2.0f32; n];
        let mut best_k = vec![0usize; n];
        let mut before = vec![f32::NAN; n];
        let mut after = vec![f32::NAN; n];
        let mut prev = vec![f32::NAN; n];
        let mut scores: Vec<Vec<f32>> = vec![vec![-1.0; n]; srcs.len()];
        let (mut sw, mut ssw, mut rsw, mut msk) = (vec![0.0f32; n], vec![0.0f32; n], vec![0.0f32; n], vec![0.0f32; n]);
        let (mut bs, mut bss, mut brs, mut bm) = (vec![0.0f32; n], vec![0.0f32; n], vec![0.0f32; n], vec![0.0f32; n]);
        for kk in 0..d {
            let invd = inv_far + (inv_near - inv_far) * kk as f64 / (d - 1) as f64;
            let z = 1.0 / invd;
            for (si, &j) in srcs.iter().enumerate() {
                let simg = lo[j].as_ref().unwrap();
                let (a, t_rel, _) = &rel[si];
                for p in 0..n {
                    let xc = a[p] * z + t_rel;
                    let v = k.project_cam(&xc).and_then(|q| simg.sample(((q.x + 0.5) * s - 0.5) as f32, ((q.y + 0.5) * s - 0.5) as f32));
                    match v {
                        Some(v) => {
                            sw[p] = v;
                            ssw[p] = v * v;
                            rsw[p] = v * rimg.data[p];
                            msk[p] = 1.0;
                        }
                        None => {
                            sw[p] = 0.0;
                            ssw[p] = 0.0;
                            rsw[p] = 0.0;
                            msk[p] = 0.0;
                        }
                    }
                }
                box_sum(&sw, w, h, r, &mut tmp, &mut bs);
                box_sum(&ssw, w, h, r, &mut tmp, &mut bss);
                box_sum(&rsw, w, h, r, &mut tmp, &mut brs);
                box_sum(&msk, w, h, r, &mut tmp, &mut bm);
                let sc = &mut scores[si];
                for p in 0..n {
                    if bm[p] < area - 0.5 {
                        sc[p] = -1.0;
                        continue;
                    }
                    let mr = sr[p] / area;
                    let ms = bs[p] / area;
                    let vr = srr[p] / area - mr * mr;
                    let vs = bss[p] / area - ms * ms;
                    if vr < 4e-5 || vs < 4e-5 {
                        sc[p] = -1.0;
                        continue;
                    }
                    sc[p] = (brs[p] / area - mr * ms) / (vr * vs).sqrt();
                }
            }
            for p in 0..n {
                // mean of the best two source scores (robust to occlusion in one view)
                let (mut b1, mut b2) = (-1.0f32, -1.0f32);
                for sc in &scores {
                    let v = sc[p];
                    if v > b1 {
                        b2 = b1;
                        b1 = v;
                    } else if v > b2 {
                        b2 = v;
                    }
                }
                let v = if scores.len() >= 2 { 0.5 * (b1 + b2) } else { b1 };
                if v > best[p] {
                    best[p] = v;
                    best_k[p] = kk;
                    before[p] = prev[p];
                    after[p] = f32::NAN;
                } else if best_k[p] + 1 == kk {
                    after[p] = v;
                }
                prev[p] = v;
            }
        }
        let mut depth = vec![f32::NAN; n];
        for p in 0..n {
            if best[p] < opt.min_zncc {
                continue;
            }
            let mut kf = best_k[p] as f64;
            if before[p].is_finite() && after[p].is_finite() {
                let den = before[p] - 2.0 * best[p] + after[p];
                if den < -1e-6 {
                    kf += (0.5 * (before[p] - after[p]) / den).clamp(-0.5, 0.5) as f64;
                }
            } else if best_k[p] == 0 || best_k[p] == d - 1 {
                continue; // at the edge of the sweep range: unreliable
            }
            let invd = inv_far + (inv_near - inv_far) * kf / (d - 1) as f64;
            depth[p] = (1.0 / invd) as f32;
        }
        out[i] = Some(DepthMap { depth, ..dm0 });
    }
    progress(1.0);
    out
}

/// Keep depths confirmed by neighbouring depth maps.
pub fn filter_depth_maps(maps: &mut [Option<DepthMap>], rec: &Reconstruction) -> usize {
    let k = rec.intr;
    let snapshot: Vec<Option<DepthMap>> = maps.to_vec();
    let mut kept = 0;
    for (i, mo) in maps.iter_mut().enumerate() {
        let Some(dm) = mo else { continue };
        let pose = rec.poses[i].unwrap();
        let others: Vec<(usize, &DepthMap)> =
            dm.sources.iter().filter_map(|&j| snapshot[j].as_ref().map(|d| (j, d))).collect();
        let need = if others.len() >= 2 { 2 } else { 1 };
        let mut out = vec![f32::NAN; dm.w * dm.h];
        for y in 0..dm.h {
            for x in 0..dm.w {
                let z = dm.depth[y * dm.w + x];
                if !z.is_finite() {
                    continue;
                }
                let xw = pose.r.transpose() * (dm.ray(&k, x, y) * z as f64 - pose.t);
                let mut good = 0;
                for &(j, dj) in &others {
                    let pj = rec.poses[j].unwrap();
                    let Some((u, v, _)) = dj.project(&k, &pj, &xw) else { continue };
                    let Some((xj, yj, zj)) = dj.at(u, v) else { continue };
                    let xw2 = pj.r.transpose() * (dj.ray(&k, xj, yj) * zj as f64 - pj.t);
                    let Some((u2, v2, z2)) = dm.project(&k, &pose, &xw2) else { continue };
                    let pix = ((u2 - x as f64).powi(2) + (v2 - y as f64).powi(2)).sqrt();
                    if pix < 1.0 && ((z2 - z as f64) / z as f64).abs() < 0.01 {
                        good += 1;
                    }
                }
                if good >= need {
                    out[y * dm.w + x] = z;
                    kept += 1;
                }
            }
        }
        dm.depth = out;
    }
    kept
}

pub struct Cloud {
    pub pos: Vec<[f32; 3]>,
    pub col: Vec<[u8; 3]>,
    pub nrm: Vec<[f32; 3]>,
}

/// Back-project filtered depth maps into one voxel-downsampled cloud with normals.
pub fn fuse(maps: &[Option<DepthMap>], rec: &Reconstruction, rgba: &[Vec<u8>], fw: usize, fh: usize, voxel: f64) -> Cloud {
    let k = rec.intr;
    let mut acc: std::collections::HashMap<(i32, i32, i32), ([f64; 3], [f64; 3], [f64; 3], u32)> = Default::default();
    for (i, dm) in maps.iter().enumerate() {
        let Some(dm) = dm else { continue };
        let pose = rec.poses[i].unwrap();
        let c = pose.center();
        let world = |x: usize, y: usize| -> Option<V3> {
            let z = dm.depth[y * dm.w + x];
            z.is_finite().then(|| pose.r.transpose() * (dm.ray(&k, x, y) * z as f64 - pose.t))
        };
        for y in 1..dm.h - 1 {
            for x in 1..dm.w - 1 {
                let Some(p) = world(x, y) else { continue };
                let n = match (world(x + 1, y), world(x - 1, y), world(x, y + 1), world(x, y - 1)) {
                    (Some(a), Some(b), Some(cc), Some(d)) => {
                        let mut n = (a - b).cross(&(cc - d));
                        if n.norm() < 1e-12 {
                            continue;
                        }
                        n = n.normalize();
                        if n.dot(&(c - p)) < 0.0 {
                            n = -n;
                        }
                        n
                    }
                    _ => (c - p).normalize(),
                };
                let u = (x as f64 + 0.5) / dm.s - 0.5;
                let v = (y as f64 + 0.5) / dm.s - 0.5;
                let col = sample_rgb(&rgba[i], fw, fh, u as f32, v as f32);
                let key = ((p.x / voxel).floor() as i32, (p.y / voxel).floor() as i32, (p.z / voxel).floor() as i32);
                let e = acc.entry(key).or_insert(([0.0; 3], [0.0; 3], [0.0; 3], 0));
                for a in 0..3 {
                    e.0[a] += p[a];
                    e.1[a] += col[a] as f64;
                    e.2[a] += n[a];
                }
                e.3 += 1;
            }
        }
    }
    let mut cloud = Cloud { pos: vec![], col: vec![], nrm: vec![] };
    let mut keys: Vec<_> = acc.keys().copied().collect();
    keys.sort_unstable();
    for key in keys {
        let (p, c, n, cnt) = acc[&key];
        let f = cnt as f64;
        let nn = V3::new(n[0], n[1], n[2]);
        let nn = if nn.norm() > 1e-12 { nn.normalize() } else { V3::y() };
        cloud.pos.push([(p[0] / f) as f32, (p[1] / f) as f32, (p[2] / f) as f32]);
        cloud.col.push([(c[0] / f) as u8, (c[1] / f) as u8, (c[2] / f) as u8]);
        cloud.nrm.push([nn.x as f32, nn.y as f32, nn.z as f32]);
    }
    cloud
}
