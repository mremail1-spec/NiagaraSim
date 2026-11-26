//! Incremental structure-from-motion: tracks, two-view initialisation,
//! PnP registration, triangulation and repeated bundle adjustment.

use crate::ba::{bundle_adjust, BaOptions, Obs};
use crate::features::Features;
use crate::geometry::*;
use nalgebra::{Rotation3, Vector3};

pub struct PairMatch {
    pub i: usize,
    pub j: usize,
    pub matches: Vec<(u32, u32)>,
}

#[derive(Clone)]
pub struct Point3D {
    pub x: V3,
    pub color: [u8; 3],
    pub track: usize,
    pub obs: Vec<(usize, usize)>,
}

#[derive(Clone)]
pub struct Reconstruction {
    pub poses: Vec<Option<Pose>>,
    pub intr: Intrinsics,
    pub points: Vec<Point3D>,
    pub init_pair: Option<(usize, usize)>,
    /// final RMS reprojection error in pixels
    pub rms: f64,
}

impl Reconstruction {
    pub fn registered(&self) -> usize {
        self.poses.iter().filter(|p| p.is_some()).count()
    }
}

struct Ctx<'a> {
    feats: &'a [Features],
    tracks: Vec<Vec<(usize, usize)>>,
    feat_track: Vec<Vec<i32>>,
    track_point: Vec<i32>,
    rec: Reconstruction,
    thr: f64,
    refine_k: bool,
}

fn find(p: &mut [usize], mut x: usize) -> usize {
    while p[x] != x {
        p[x] = p[p[x]];
        x = p[x];
    }
    x
}

fn uv(feats: &[Features], img: usize, f: usize) -> [f64; 2] {
    let k = &feats[img].kps[f];
    [k.x as f64, k.y as f64]
}

impl<'a> Ctx<'a> {
    fn new(feats: &'a [Features], pairs: &[PairMatch], intr: Intrinsics, thr: f64) -> Self {
        let mut off = vec![0usize; feats.len() + 1];
        for i in 0..feats.len() {
            off[i + 1] = off[i] + feats[i].kps.len();
        }
        let total = off[feats.len()];
        let mut parent: Vec<usize> = (0..total).collect();
        // Images each set already contains (bitmask; up to 128 images). A union that
        // would put two features of one image into a track is refused, instead of
        // poisoning (and later discarding) the whole track.
        let use_mask = feats.len() <= 128;
        let mut imgs: Vec<u128> = Vec::new();
        if use_mask {
            imgs = vec![0; total];
            for i in 0..feats.len() {
                for f in off[i]..off[i + 1] {
                    imgs[f] = 1u128 << i;
                }
            }
        }
        // Strongest pairs first, so the best evidence wins conflicts.
        let mut order: Vec<&PairMatch> = pairs.iter().collect();
        order.sort_by(|a, b| b.matches.len().cmp(&a.matches.len()));
        for pm in order {
            for &(a, b) in &pm.matches {
                let ra = find(&mut parent, off[pm.i] + a as usize);
                let rb = find(&mut parent, off[pm.j] + b as usize);
                if ra == rb {
                    continue;
                }
                if use_mask {
                    if imgs[ra] & imgs[rb] != 0 {
                        continue;
                    }
                    imgs[rb] |= imgs[ra];
                }
                parent[ra] = rb;
            }
        }
        let mut groups: std::collections::HashMap<usize, Vec<(usize, usize)>> = Default::default();
        for img in 0..feats.len() {
            for f in 0..feats[img].kps.len() {
                let r = find(&mut parent, off[img] + f);
                groups.entry(r).or_default().push((img, f));
            }
        }
        let mut tracks = Vec::new();
        let mut feat_track: Vec<Vec<i32>> = feats.iter().map(|f| vec![-1; f.kps.len()]).collect();
        let mut keys: Vec<usize> = groups.keys().copied().collect();
        keys.sort_unstable();
        for key in keys {
            let g = &groups[&key];
            if g.len() < 2 {
                continue;
            }
            let mut imgs: Vec<usize> = g.iter().map(|o| o.0).collect();
            imgs.sort_unstable();
            imgs.dedup();
            if imgs.len() != g.len() {
                continue; // inconsistent track (two features in one image)
            }
            for &(img, f) in g {
                feat_track[img][f] = tracks.len() as i32;
            }
            tracks.push(g.clone());
        }
        let nt = tracks.len();
        Ctx {
            feats,
            tracks,
            feat_track,
            track_point: vec![-1; nt],
            rec: Reconstruction { poses: vec![None; feats.len()], intr, points: Vec::new(), init_pair: None, rms: f64::NAN },
            refine_k: false,
            thr,
        }
    }

    fn reproj_err(&self, img: usize, f: usize, x: &V3) -> f64 {
        let p = self.rec.poses[img].as_ref().unwrap();
        match self.rec.intr.project(p, x) {
            Some(q) => {
                let o = uv(self.feats, img, f);
                ((q.x - o[0]).powi(2) + (q.y - o[1]).powi(2)).sqrt()
            }
            None => f64::MAX,
        }
    }

    fn triangulate_track(&mut self, t: usize, min_angle: f64) -> bool {
        if self.track_point[t] >= 0 {
            return false;
        }
        let mut obs: Vec<(usize, usize)> =
            self.tracks[t].iter().copied().filter(|&(img, _)| self.rec.poses[img].is_some()).collect();
        for _round in 0..2 {
            if obs.len() < 2 {
                return false;
            }
            let views: Vec<(&Pose, [f64; 2])> = obs
                .iter()
                .map(|&(img, f)| {
                    let u = uv(self.feats, img, f);
                    (self.rec.poses[img].as_ref().unwrap(), self.rec.intr.normalize(u[0], u[1]))
                })
                .collect();
            let Some(x) = triangulate(&views) else { return false };
            let good: Vec<(usize, usize)> =
                obs.iter().copied().filter(|&(img, f)| self.reproj_err(img, f, &x) < self.thr).collect();
            if good.len() == obs.len() {
                let centers: Vec<V3> = obs.iter().map(|&(img, _)| self.rec.poses[img].unwrap().center()).collect();
                if triangulation_angle(&centers, &x) < min_angle {
                    return false;
                }
                self.track_point[t] = self.rec.points.len() as i32;
                self.rec.points.push(Point3D { x, color: [200, 200, 200], track: t, obs });
                return true;
            }
            obs = good;
        }
        false
    }

    fn triangulate_all(&mut self, min_angle: f64) -> usize {
        (0..self.tracks.len()).filter(|&t| self.triangulate_track(t, min_angle)).count()
    }

    fn bundle(&mut self, iterations: usize, fixed: usize) -> (f64, f64) {
        let refine = self.refine_k && self.rec.registered() >= 4;
        let mut xs: Vec<V3> = self.rec.points.iter().map(|p| p.x).collect();
        let mut obs = Vec::new();
        for (pi, p) in self.rec.points.iter().enumerate() {
            for &(img, f) in &p.obs {
                obs.push(Obs { cam: img, point: pi, uv: uv(self.feats, img, f) });
            }
        }
        let opt = BaOptions { iterations, huber_px: self.thr * 0.5, refine_intrinsics: refine, refine_k1: self.rec.registered() >= 12, fix_focal: false, fixed_cam: fixed, center_prior: None };
        let r = bundle_adjust(&mut self.rec.poses, &mut self.rec.intr, &mut xs, &obs, &opt);
        for (p, x) in self.rec.points.iter_mut().zip(xs) {
            p.x = x;
        }
        r
    }

    /// Drop bad observations and weak points; returns number of removed points.
    fn filter(&mut self, min_angle: f64) -> usize {
        let before = self.rec.points.len();
        let mut kept = Vec::with_capacity(before);
        let points = std::mem::take(&mut self.rec.points);
        for mut p in points {
            p.obs.retain(|&(img, f)| self.reproj_err(img, f, &p.x) < self.thr);
            let centers: Vec<V3> = p.obs.iter().map(|&(img, _)| self.rec.poses[img].unwrap().center()).collect();
            if p.obs.len() >= 2 && triangulation_angle(&centers, &p.x) >= min_angle {
                kept.push(p);
            }
        }
        // Cameras left with too few consistent observations were placed wrongly:
        // un-place them (they may be registered again later).
        let mut per_cam = vec![0usize; self.rec.poses.len()];
        for p in &kept {
            for &(img, _) in &p.obs {
                per_cam[img] += 1;
            }
        }
        let drop: Vec<bool> = (0..self.rec.poses.len())
            .map(|i| self.rec.poses[i].is_some() && per_cam[i] < 12 && Some(i) != self.rec.init_pair.map(|p| p.0) && Some(i) != self.rec.init_pair.map(|p| p.1))
            .collect();
        if drop.iter().any(|&d| d) {
            for (i, d) in drop.iter().enumerate() {
                if *d {
                    self.rec.poses[i] = None;
                }
            }
            let mut again = Vec::with_capacity(kept.len());
            for mut p in kept {
                p.obs.retain(|o| !drop[o.0]);
                if p.obs.len() >= 2 {
                    again.push(p);
                }
            }
            kept = again;
        }
        self.track_point.iter_mut().for_each(|v| *v = -1);
        for (i, p) in kept.iter().enumerate() {
            self.track_point[p.track] = i as i32;
        }
        self.rec.points = kept;
        before - self.rec.points.len()
    }

    /// Fallback registration for an image that sees few existing 3D points but
    /// matches a registered image well: relative pose from the essential matrix,
    /// with only the scale taken from the shared 3D points.
    fn register_relative(&mut self, img: usize, pairs: &[PairMatch]) -> Result<usize, String> {
        let k = self.rec.intr;
        let best = pairs
            .iter()
            .filter(|p| (p.i == img && self.rec.poses[p.j].is_some()) || (p.j == img && self.rec.poses[p.i].is_some()))
            .max_by_key(|p| p.matches.len())
            .ok_or("no matched registered neighbour")?;
        let (r, flip) = if best.i == img { (best.j, true) } else { (best.i, false) };
        let pairs_rf: Vec<(usize, usize)> =
            best.matches.iter().map(|&(a, b)| if flip { (b as usize, a as usize) } else { (a as usize, b as usize) }).collect();
        let n1: Vec<[f64; 2]> = pairs_rf.iter().map(|&(fr, _)| { let u = uv(self.feats, r, fr); k.normalize(u[0], u[1]) }).collect();
        let n2: Vec<[f64; 2]> = pairs_rf.iter().map(|&(_, fi)| { let u = uv(self.feats, img, fi); k.normalize(u[0], u[1]) }).collect();
        let (rel, good2) = relative_pose(&n1, &n2, self.thr / k.f, 4242 + img as u64).ok_or("relative pose failed")?;
        if good2 < 30 {
            return Err(format!("only {} two-view inliers", good2));
        }
        let pr = self.rec.poses[r].unwrap();
        // Scale from 3D points already seen in this image.
        let mut scales = Vec::new();
        let mut ids = Vec::new();
        for (f, &t) in self.feat_track[img].iter().enumerate() {
            if t < 0 || self.track_point[t as usize] < 0 {
                continue;
            }
            let pi = self.track_point[t as usize] as usize;
            let a = rel.r * pr.to_cam(&self.rec.points[pi].x);
            let u = uv(self.feats, img, f);
            let n = k.normalize(u[0], u[1]);
            let xh = V3::new(n[0], n[1], 1.0);
            let c_t = xh.cross(&rel.t);
            let den = c_t.norm_squared();
            if den < 1e-12 {
                continue;
            }
            let sc = -xh.cross(&a).dot(&c_t) / den;
            if sc > 0.0 {
                scales.push(sc);
                ids.push((f, pi));
            }
        }
        // Scale: from shared 3D points when there are enough; otherwise assume a
        // typical step length (the median distance between consecutive placed
        // cameras). Later loop closure and bundle adjustment correct it.
        let assumed = scales.len() < 3;
        let sc = if !assumed {
            let mut sorted = scales.clone();
            sorted.sort_by(|a, b| a.partial_cmp(b).unwrap());
            sorted[sorted.len() / 2]
        } else {
            if good2 < 40 {
                return Err(format!("{} shared 3D points and a weak pair ({} inliers)", scales.len(), good2));
            }
            let centers: Vec<(usize, V3)> = self.rec.poses.iter().enumerate().filter_map(|(i, p)| p.map(|p| (i, p.center()))).collect();
            let mut steps: Vec<f64> = centers.windows(2).map(|w| (w[1].1 - w[0].1).norm() / (w[1].0 - w[0].0) as f64).collect();
            if steps.is_empty() {
                return Err("no step length to assume".into());
            }
            steps.sort_by(|a, b| a.partial_cmp(b).unwrap());
            steps[steps.len() / 2] * (img as f64 - r as f64).abs().max(1.0)
        };
        let pose = Pose { r: rel.r * pr.r, t: rel.r * pr.t + rel.t * sc };
        self.rec.poses[img] = Some(pose);
        let good: Vec<(usize, usize)> =
            ids.into_iter().filter(|&(f, pi)| self.reproj_err(img, f, &self.rec.points[pi].x) < self.thr * 1.5).collect();
        if !assumed && (good.len() < 3 || (good.len() as f64) < 0.5 * scales.len() as f64) {
            self.rec.poses[img] = None;
            return Err(format!("scale inconsistent ({} of {} points agree)", good.len(), scales.len()));
        }
        let n = good.len();
        for (f, pi) in good {
            self.rec.points[pi].obs.push((img, f));
        }
        Ok(n)
    }

    fn register(&mut self, img: usize) -> Result<usize, String> {
        let mut xs = Vec::new();
        let mut uvs = Vec::new();
        let mut ids = Vec::new();
        for (f, &t) in self.feat_track[img].iter().enumerate() {
            if t >= 0 && self.track_point[t as usize] >= 0 {
                let pi = self.track_point[t as usize] as usize;
                xs.push(self.rec.points[pi].x);
                uvs.push(uv(self.feats, img, f));
                ids.push((f, pi));
            }
        }
        let Some((pose, mask)) = pnp_ransac(&xs, &uvs, &self.rec.intr, self.thr * 0.75, 1234 + img as u64) else {
            return Err(format!("{} correspondences, PnP failed", xs.len()));
        };
        let n_in = mask.iter().filter(|&&m| m).count();
        if n_in < 12 || ((n_in as f64) < 0.25 * xs.len() as f64 && n_in < 30) {
            return Err(format!("{} correspondences, {} PnP inliers", xs.len(), n_in));
        }
        self.rec.poses[img] = Some(pose);
        for (k, &(f, pi)) in ids.iter().enumerate() {
            if mask[k] {
                self.rec.points[pi].obs.push((img, f));
            }
        }
        Ok(n_in)
    }
}


pub fn reconstruct(
    feats: &[Features],
    pairs: &[PairMatch],
    intr0: Intrinsics,
    thr_px: f64,
    log: &mut dyn FnMut(f32, &str),
) -> Option<Reconstruction> {
    reconstruct_from(feats, pairs, intr0, thr_px, None, true, log)
}

/// Like `reconstruct`, but tries `prefer` as the starting pair first (keeps
/// repeated solves during capture consistent with each other).
pub fn reconstruct_from(
    feats: &[Features],
    pairs: &[PairMatch],
    intr0: Intrinsics,
    thr_px: f64,
    prefer: Option<(usize, usize)>,
    refine_intrinsics: bool,
    log: &mut dyn FnMut(f32, &str),
) -> Option<Reconstruction> {
    reconstruct_gps(feats, pairs, intr0, thr_px, prefer, refine_intrinsics, None, log)
}

/// As `reconstruct_from`; with GPS, the start whose cameras fit the GPS track
/// best is kept (forward driving is ambiguous from images alone).
#[allow(clippy::too_many_arguments)]
pub fn reconstruct_gps(
    feats: &[Features],
    pairs: &[PairMatch],
    intr0: Intrinsics,
    thr_px: f64,
    prefer: Option<(usize, usize)>,
    refine_intrinsics: bool,
    gps: Option<(&[Option<V3>], f64)>,
    log: &mut dyn FnMut(f32, &str),
) -> Option<Reconstruction> {
    let cands = init_candidates(feats, pairs, intr0, thr_px, prefer);
    if cands.is_empty() {
        return None;
    }
    // Fast path: the previous starting pair is still sound.
    if let Some(pp) = prefer {
        if cands[0].0 == f64::MAX && (cands[0].1, cands[0].2) == pp {
            let c = &cands[0];
            if let Some(r) = grow(feats, pairs, intr0, thr_px, refine_intrinsics, (c.1, c.2, c.3), log) {
                return Some(r);
            }
        }
    }
    // Two-view starts can be ambiguous (forward motion, near-planar views), so
    // grow from a few well-separated candidates and keep the reconstruction
    // that agrees best with all pair matches.
    let mut chosen: Vec<&(f64, usize, usize, Pose)> = Vec::new();
    for c in &cands {
        if chosen.len() >= if gps.is_some() { 5 } else { 3 } {
            break;
        }
        if chosen.iter().all(|d| c.1.abs_diff(d.1) > 2 && c.2.abs_diff(d.2) > 2) {
            chosen.push(c);
        }
    }
    let mut best: Option<(f64, Reconstruction)> = None;
    for c in chosen {
        log(0.0, &format!("trying start {}-{} (score {:.0})", c.1, c.2, c.0.min(1e9)));
        let Some(r) = grow(feats, pairs, intr0, thr_px, refine_intrinsics, (c.1, c.2, c.3), &mut *log) else { continue };
        let mut gc = global_consistency(&r, feats, pairs, 1.5 * thr_px);
        log(0.0, &format!("start {}-{}: {} cameras, {:.1}% of all matches consistent", c.1, c.2, r.registered(), 100.0 * gc));
        if let Some((g, acc)) = gps {
            // Score by GPS agreement after GPS-weighted refinement (which removes
            // the drift of long forward-driving reconstructions): cameras within
            // 3x GPS accuracy, then a small residual.
            gc = -1.0;
            if let Some((sc, rot, t, _, _)) = align_to_gps(&r, g, 3.0 * acc) {
                let mut rr = r.clone();
                apply_similarity(&mut rr, sc, &rot, &t);
                let w = V3::new(1.0 / acc, 0.2 / acc, 1.0 / acc);
                let pri: Vec<Option<(V3, V3)>> = g.iter().map(|x| x.map(|p| (p, w))).collect();
                refine_with_gps(&mut rr, feats, &pri, thr_px, 15);
                let mut d: Vec<f64> = rr.poses.iter().zip(g).filter_map(|(p, q)| {
                    let (p, q) = (p.as_ref()?, (*q)?);
                    Some(((p.center().x - q.x).powi(2) + (p.center().z - q.z).powi(2)).sqrt())
                }).collect();
                d.sort_by(|a, b| a.partial_cmp(b).unwrap());
                let inl = d.iter().filter(|&&x| x < 3.0 * acc).count();
                let med = d.get(d.len() / 2).copied().unwrap_or(f64::INFINITY);
                log(0.0, &format!("  after GPS refinement: {} cameras within {:.0} m, median {:.1} m", inl, 3.0 * acc, med));
                gc = inl as f64 - med / (10.0 * acc);
            }
        }
        if best.as_ref().map_or(true, |b| gc > b.0) {
            best = Some((gc, r));
        }
    }
    best.map(|b| b.1)
}

/// Score candidate starting pairs (best first).
fn init_candidates(
    feats: &[Features],
    pairs: &[PairMatch],
    intr0: Intrinsics,
    thr_px: f64,
    prefer: Option<(usize, usize)>,
) -> Vec<(f64, usize, usize, Pose)> {
    let min_angle = 1.5f64.to_radians();
    // --- Two-view initialisation ---
    // Candidate starting pairs: many matches, but skip near-duplicate views
    // (tiny image motion means no parallax to triangulate from).
    let dim = 2.0 * intr0.cx.max(intr0.cy);
    let moved = |pm: &PairMatch| -> bool {
        let mut d: Vec<f64> = pm.matches.iter().map(|&(a, b)| {
            let (p, q) = (uv(feats, pm.i, a as usize), uv(feats, pm.j, b as usize));
            ((p[0] - q[0]).powi(2) + (p[1] - q[1]).powi(2)).sqrt()
        }).collect();
        d.sort_by(|x, y| x.partial_cmp(y).unwrap());
        d[d.len() / 2] > 0.02 * dim
    };
    let mut cands: Vec<&PairMatch> = pairs.iter().filter(|p| p.matches.len() >= 40 && moved(p)).collect();
    cands.sort_by(|a, b| b.matches.len().cmp(&a.matches.len()));
    cands.truncate(150);
    if let Some((pi, pj)) = prefer {
        if let Some(pos) = cands.iter().position(|p| p.i == pi && p.j == pj) {
            let c = cands.remove(pos);
            cands.insert(0, c);
        }
    }
    let preferred = prefer;
    let mut out: Vec<(f64, usize, usize, Pose)> = Vec::new();
    for pm in cands {
        let n1: Vec<[f64; 2]> =
            pm.matches.iter().map(|&(a, _)| { let u = uv(feats, pm.i, a as usize); intr0.normalize(u[0], u[1]) }).collect();
        let n2: Vec<[f64; 2]> =
            pm.matches.iter().map(|&(_, b)| { let u = uv(feats, pm.j, b as usize); intr0.normalize(u[0], u[1]) }).collect();
        let Some((pose, fit)) = relative_pose(&n1, &n2, thr_px / intr0.f, 99 + pm.i as u64 * 31 + pm.j as u64) else { continue };
        // Parallax check (as in ORB-SLAM): if a homography (rotation and/or one plane)
        // explains nearly every match, the pair cannot pin down translation reliably.
        let h_in = homography_ransac(&n1, &n2, thr_px / intr0.f, 7 + pm.i as u64).map_or(0, |h| h.1);
        let parallax = (1.0 - h_in as f64 / fit.max(1) as f64).clamp(0.0, 1.0);
        let (in1, in2) = (n1, n2);
        let p0 = Pose::identity();
        let c = [p0.center(), pose.center()];
        let mut angles = Vec::new();
        for i in 0..in1.len() {
            if let Some(x) = triangulate(&[(&p0, in1[i]), (&pose, in2[i])]) {
                let c2 = pose.to_cam(&x);
                if x.z <= 0.0 || c2.z <= 0.0 {
                    continue;
                }
                let t2 = (thr_px / intr0.f).powi(2);
                if (x.x / x.z - in1[i][0]).powi(2) + (x.y / x.z - in1[i][1]).powi(2) > t2
                    || (c2.x / c2.z - in2[i][0]).powi(2) + (c2.y / c2.z - in2[i][1]).powi(2) > t2
                {
                    continue;
                }
                angles.push(triangulation_angle(&c, &x));
            }
        }
        if angles.len() < 30 {
            continue;
        }
        angles.sort_by(|a, b| a.partial_cmp(b).unwrap());
        let med = angles[angles.len() / 2].to_degrees();
        let good = angles.iter().filter(|&&a| a > min_angle).count() as f64;
        let mut score = good * (med / 12.0).min(1.0) * (0.05 + parallax);
        if preferred == Some((pm.i, pm.j)) && med >= 2.0 && good >= 40.0 {
            score = f64::MAX; // previous starting pair is still sound: keep it
        }
        out.push((score, pm.i, pm.j, pose));
    }
    out.sort_by(|a, b| b.0.partial_cmp(&a.0).unwrap());
    out
}

/// Fraction of all verified pair matches that agree with the reconstruction's
/// camera poses (epipolar check). A wrong start can look fine locally but fails
/// for distant pairs, so this compares whole reconstructions.
pub fn global_consistency(rec: &Reconstruction, feats: &[Features], pairs: &[PairMatch], thr_px: f64) -> f64 {
    let k = rec.intr;
    let thr = (thr_px / k.f).powi(2);
    let (mut good, mut total) = (0usize, 0usize);
    for pm in pairs {
        total += pm.matches.len();
        let (Some(a), Some(b)) = (rec.poses.get(pm.i).copied().flatten(), rec.poses.get(pm.j).copied().flatten()) else { continue };
        let r = b.r * a.r.transpose();
        let t = b.t - r * a.t;
        if t.norm() < 1e-12 {
            continue;
        }
        let e = skew(&t.normalize()) * r;
        for &(fa, fb) in &pm.matches {
            let (ua, ub) = (uv(feats, pm.i, fa as usize), uv(feats, pm.j, fb as usize));
            if sampson(&e, &k.normalize(ua[0], ua[1]), &k.normalize(ub[0], ub[1])) < thr {
                good += 1;
            }
        }
    }
    if total == 0 { 0.0 } else { good as f64 / total as f64 }
}

/// Grow a reconstruction from a given starting pair.
#[allow(clippy::too_many_arguments)]
fn grow(
    feats: &[Features],
    pairs: &[PairMatch],
    intr0: Intrinsics,
    thr_px: f64,
    refine_intrinsics: bool,
    init: (usize, usize, Pose),
    log: &mut dyn FnMut(f32, &str),
) -> Option<Reconstruction> {
    let min_angle = 1.5f64.to_radians();
    let mut ctx = Ctx::new(feats, pairs, intr0, thr_px);
    ctx.refine_k = refine_intrinsics;
    let (a, b, pose) = init;
    let init_pair = (a, b);
    ctx.rec.init_pair = Some(init_pair);
    ctx.rec.poses[a] = Some(Pose::identity());
    ctx.rec.poses[b] = Some(pose);
    let n = ctx.triangulate_all(min_angle);
    log(0.0, &format!("triangulated {} initial points", n));
    if n < 20 {
        return None;
    }
    ctx.bundle(20, a);
    ctx.filter(min_angle);

    // --- Incremental registration ---
    let n_img = feats.len();
    let mut failed: Vec<usize> = vec![0; n_img];
    let mut last_ba = ctx.rec.registered();
    let mut tried_rel: Vec<bool> = vec![false; n_img];
    loop {
        let counts: Vec<(usize, usize)> = (0..n_img)
            .filter(|&i| ctx.rec.poses[i].is_none())
            .map(|i| {
                let c = ctx.feat_track[i]
                    .iter()
                    .filter(|&&t| t >= 0 && ctx.track_point[t as usize] >= 0)
                    .count();
                (i, c)
            })
            .collect();
        let weak: Vec<usize> = counts.iter().filter(|&&(i, _)| !tried_rel[i]).map(|x| x.0).collect();
        let mut counts: Vec<(usize, usize)> = counts
            .into_iter()
            .filter(|&(i, c)| c >= 12 && (failed[i] == 0 || c as f64 > failed[i] as f64 * 1.3))
            .collect();
        if counts.is_empty() && !weak.is_empty() {
            // No image sees enough 3D points: try two-view registration instead.
            let mut ok = false;
            for img in weak {
                tried_rel[img] = true;
                match ctx.register_relative(img, pairs) {
                    Ok(n) => {
                        let nt = ctx.triangulate_all(min_angle);
                        last_ba = 0; // force refinement next round
                        log(0.0, &format!("registered image {} from its neighbour ({} scale points, +{} points)", img, n, nt));
                        ok = true;
                        break;
                    }
                    Err(e) => log(0.0, &format!("image {img} not registered: {e}")),
                }
            }
            if ok {
                tried_rel.iter_mut().for_each(|t| *t = false);
                ctx.bundle(8, a);
                ctx.filter(min_angle);
                last_ba = ctx.rec.registered();
                continue;
            }
        }
        if counts.is_empty() {
            if last_ba != ctx.rec.registered() {
                // Stuck with un-refined cameras: refine, then retry the failures.
                last_ba = ctx.rec.registered();
                ctx.bundle(8, a);
                ctx.filter(min_angle);
                ctx.triangulate_all(min_angle);
                failed.iter_mut().for_each(|f| *f = 0);
                continue;
            }
            break;
        }
        counts.sort_by(|x, y| y.1.cmp(&x.1));
        let mut done = false;
        for &(img, c) in counts.iter().take(5) {
            let res = ctx.register(img);
            if let Err(e) = &res {
                log(0.0, &format!("image {img} not registered: {e}"));
            }
            if let Ok(nin) = res {
                let nt = ctx.triangulate_all(min_angle);
                // Global refinement when the model has grown by ~15% (always while small).
                let reg = ctx.rec.registered();
                let (r0, r1) = if reg <= 8 || reg as f64 >= last_ba as f64 * 1.15 {
                    last_ba = reg;
                    let r = ctx.bundle(8, a);
                    ctx.filter(min_angle);
                    r
                } else {
                    (f64::NAN, f64::NAN)
                };
                log(0.0, &format!(
                    "registered image {} ({} inliers, +{} points, rms {:.2}->{:.2}px, f={:.0})",
                    img, nin, nt, r0, r1, ctx.rec.intr.f
                ));
                done = true;
                break;
            } else {
                failed[img] = c;
            }
        }
        log(ctx.rec.registered() as f32 / n_img as f32, "");
        if !done {
            let weak: Vec<usize> = (0..n_img)
                .filter(|&i| ctx.rec.poses[i].is_none() && !tried_rel[i])
                .collect();
        if !weak.is_empty() {
            // No image sees enough 3D points: try two-view registration instead.
            let mut ok = false;
            for img in weak {
                tried_rel[img] = true;
                match ctx.register_relative(img, pairs) {
                    Ok(n) => {
                        let nt = ctx.triangulate_all(min_angle);
                        last_ba = 0; // force refinement next round
                        log(0.0, &format!("registered image {} from its neighbour ({} scale points, +{} points)", img, n, nt));
                        ok = true;
                        break;
                    }
                    Err(e) => log(0.0, &format!("image {img} not registered: {e}")),
                }
            }
            if ok {
                tried_rel.iter_mut().for_each(|t| *t = false);
                ctx.bundle(8, a);
                ctx.filter(min_angle);
                last_ba = ctx.rec.registered();
                continue;
            }
        }
            if last_ba != ctx.rec.registered() {
                last_ba = ctx.rec.registered();
                ctx.bundle(8, a);
                ctx.filter(min_angle);
                ctx.triangulate_all(min_angle);
                failed.iter_mut().for_each(|f| *f = 0);
                continue;
            }
            break;
        }
    }
    // --- Final refinement ---
    ctx.triangulate_all(min_angle);
    let (r0, r1) = ctx.bundle(40, a);
    ctx.filter(min_angle);
    ctx.triangulate_all(min_angle);
    let (_, r2) = ctx.bundle(15, a);
    ctx.rec.rms = r2;
    ctx.filter(min_angle);
    log(0.0, &format!(
        "final BA: rms {:.2} -> {:.2} -> {:.2}px, focal {:.1}px, k1 {:.3}, {} cameras, {} points",
        r0, r1, r2, ctx.rec.intr.f, ctx.rec.intr.k1, ctx.rec.registered(), ctx.rec.points.len()
    ));
    let mut rec = ctx.rec;
    rec.init_pair = Some(init_pair);
    normalize_frame(&mut rec);
    Some(rec)
}

/// Centre the scene, scale to unit median radius and rotate so the average
/// camera "up" direction becomes +Y.
pub fn normalize_frame(rec: &mut Reconstruction) {
    if rec.points.is_empty() {
        return;
    }
    let med = |mut v: Vec<f64>| -> f64 {
        v.sort_by(|a, b| a.partial_cmp(b).unwrap());
        v[v.len() / 2]
    };
    let c = V3::new(
        med(rec.points.iter().map(|p| p.x.x).collect()),
        med(rec.points.iter().map(|p| p.x.y).collect()),
        med(rec.points.iter().map(|p| p.x.z).collect()),
    );
    let s = med(rec.points.iter().map(|p| (p.x - c).norm()).collect()).max(1e-9);
    let mut up = V3::zeros();
    for p in rec.poses.iter().flatten() {
        up -= V3::new(p.r[(1, 0)], p.r[(1, 1)], p.r[(1, 2)]);
    }
    let rw = if up.norm() > 1e-9 {
        Rotation3::rotation_between(&up.normalize(), &Vector3::y())
            .unwrap_or_else(|| Rotation3::from_axis_angle(&Vector3::x_axis(), std::f64::consts::PI))
            .into_inner()
    } else {
        M3::identity()
    };
    for p in rec.points.iter_mut() {
        p.x = rw * (p.x - c) / s;
    }
    for p in rec.poses.iter_mut().flatten() {
        let t = (p.r * c + p.t) / s;
        p.r *= rw.transpose();
        p.t = t;
    }
}

/// Similarity (scale, yaw about +Y, translation) mapping reconstruction camera
/// centres onto GPS positions, keeping the reconstruction's "up". 2-point
/// RANSAC on the horizontal (x, z) plane; returns (s, yaw rotation, t, inliers).
pub fn align_to_gps(rec: &Reconstruction, gps: &[Option<V3>], thr_m: f64) -> Option<(f64, M3, V3, usize, f64)> {
    let pairs: Vec<(V3, V3)> = rec
        .poses
        .iter()
        .zip(gps)
        .filter_map(|(p, g)| Some((p.as_ref()?.center(), (*g)?)))
        .collect();
    if pairs.len() < 3 {
        return None;
    }
    // complex-number form of a 2D similarity: w = a z + b, z = x + i*zcoord
    let fit = |idx: &[usize]| -> Option<((f64, f64), (f64, f64))> {
        let n = idx.len() as f64;
        let (mut mzx, mut mzy, mut mwx, mut mwy) = (0.0, 0.0, 0.0, 0.0);
        for &i in idx {
            mzx += pairs[i].0.x / n; mzy += pairs[i].0.z / n;
            mwx += pairs[i].1.x / n; mwy += pairs[i].1.z / n;
        }
        let (mut num_r, mut num_i, mut den) = (0.0, 0.0, 0.0);
        for &i in idx {
            let (zx, zy) = (pairs[i].0.x - mzx, pairs[i].0.z - mzy);
            let (wx, wy) = (pairs[i].1.x - mwx, pairs[i].1.z - mwy);
            // a = sum(w * conj(z)) / sum(|z|^2)
            num_r += wx * zx + wy * zy;
            num_i += wy * zx - wx * zy;
            den += zx * zx + zy * zy;
        }
        if den < 1e-12 {
            return None;
        }
        let a = (num_r / den, num_i / den);
        let b = (mwx - (a.0 * mzx - a.1 * mzy), mwy - (a.0 * mzy + a.1 * mzx));
        Some((a, b))
    };
    let resid = |a: (f64, f64), b: (f64, f64), i: usize| -> f64 {
        let (z, w) = (&pairs[i].0, &pairs[i].1);
        let px = a.0 * z.x - a.1 * z.z + b.0;
        let py = a.0 * z.z + a.1 * z.x + b.1;
        ((px - w.x).powi(2) + (py - w.z).powi(2)).sqrt()
    };
    let n = pairs.len();
    let mut rng = crate::geometry::Rng::new(77);
    let mut best: Option<Vec<usize>> = None;
    for _ in 0..300 {
        let s = rng.sample(n, 2);
        let Some((a, b)) = fit(&s) else { continue };
        let inl: Vec<usize> = (0..n).filter(|&i| resid(a, b, i) < thr_m).collect();
        if best.as_ref().map_or(true, |bb| inl.len() > bb.len()) {
            best = Some(inl);
        }
    }
    let inl = best?;
    if inl.len() < 3 {
        return None;
    }
    let (a, b) = fit(&inl)?;
    let scale = (a.0 * a.0 + a.1 * a.1).sqrt();
    let th = a.1.atan2(a.0);
    // map (x, z) -> rotate by th in the x-z plane; as a 3D rotation about +Y
    let (c, sn) = (th.cos(), th.sin());
    let r = M3::new(c, 0.0, -sn, 0.0, 1.0, 0.0, sn, 0.0, c);
    let mut ys: Vec<f64> = pairs.iter().map(|(z, w)| w.y - scale * z.y).collect();
    ys.sort_by(|x, y| x.partial_cmp(y).unwrap());
    let t = V3::new(b.0, ys[ys.len() / 2], b.1);
    let mut res: Vec<f64> = inl.iter().map(|&i| resid(a, b, i)).collect();
    res.sort_by(|x, y| x.partial_cmp(y).unwrap());
    Some((scale, r, t, inl.len(), res[res.len() / 2]))
}

/// Move a reconstruction by X' = s R X + t.
pub fn apply_similarity(rec: &mut Reconstruction, s: f64, r: &M3, t: &V3) {
    for p in rec.points.iter_mut() {
        p.x = r * p.x * s + t;
    }
    for p in rec.poses.iter_mut().flatten() {
        let r2 = p.r * r.transpose();
        p.t = p.t * s - r2 * t;
        p.r = r2;
    }
}

/// Bundle adjustment with GPS priors on the camera centres (and radial
/// distortion refined, focal kept). Returns (rms before, rms after) in pixels.
pub fn refine_with_gps(rec: &mut Reconstruction, feats: &[Features], gps: &[Option<(V3, V3)>], thr_px: f64, iterations: usize) -> (f64, f64) {
    let mut xs: Vec<V3> = rec.points.iter().map(|p| p.x).collect();
    let mut obs = Vec::new();
    for (pi, p) in rec.points.iter().enumerate() {
        for &(img, f) in &p.obs {
            obs.push(Obs { cam: img, point: pi, uv: uv(feats, img, f) });
        }
    }
    let mut pri: Vec<Option<(V3, V3)>> = gps.to_vec();
    pri.resize(rec.poses.len(), None);
    let opt = BaOptions {
        iterations,
        huber_px: thr_px * 0.5,
        refine_intrinsics: false,
        refine_k1: false,
        fix_focal: true,
        fixed_cam: usize::MAX,
        center_prior: Some(pri),
    };
    let r = bundle_adjust(&mut rec.poses, &mut rec.intr, &mut xs, &obs, &opt);
    for (p, x) in rec.points.iter_mut().zip(xs) {
        p.x = x;
    }
    rec.rms = r.1;
    r
}
