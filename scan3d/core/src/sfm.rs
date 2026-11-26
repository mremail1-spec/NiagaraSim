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
        for pm in pairs {
            for &(a, b) in &pm.matches {
                let ra = find(&mut parent, off[pm.i] + a as usize);
                let rb = find(&mut parent, off[pm.j] + b as usize);
                if ra != rb {
                    parent[ra] = rb;
                }
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
            rec: Reconstruction { poses: vec![None; feats.len()], intr, points: Vec::new() },
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
        let refine = self.rec.registered() >= 4;
        let mut xs: Vec<V3> = self.rec.points.iter().map(|p| p.x).collect();
        let mut obs = Vec::new();
        for (pi, p) in self.rec.points.iter().enumerate() {
            for &(img, f) in &p.obs {
                obs.push(Obs { cam: img, point: pi, uv: uv(self.feats, img, f) });
            }
        }
        let opt = BaOptions { iterations, huber_px: self.thr * 0.5, refine_intrinsics: refine, fixed_cam: fixed };
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
        self.track_point.iter_mut().for_each(|v| *v = -1);
        for (i, p) in kept.iter().enumerate() {
            self.track_point[p.track] = i as i32;
        }
        self.rec.points = kept;
        before - self.rec.points.len()
    }

    fn register(&mut self, img: usize) -> Option<usize> {
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
        let (pose, mask) = pnp_ransac(&xs, &uvs, &self.rec.intr, self.thr * 0.75, 1234 + img as u64)?;
        let n_in = mask.iter().filter(|&&m| m).count();
        if n_in < 15 || (n_in as f64) < 0.2 * xs.len() as f64 {
            return None;
        }
        self.rec.poses[img] = Some(pose);
        for (k, &(f, pi)) in ids.iter().enumerate() {
            if mask[k] {
                self.rec.points[pi].obs.push((img, f));
            }
        }
        Some(n_in)
    }
}


pub fn reconstruct(
    feats: &[Features],
    pairs: &[PairMatch],
    intr0: Intrinsics,
    thr_px: f64,
    log: &mut dyn FnMut(f32, &str),
) -> Option<Reconstruction> {
    let min_angle = 1.5f64.to_radians();
    let mut ctx = Ctx::new(feats, pairs, intr0, thr_px);
    log(0.0, &format!("{} feature tracks", ctx.tracks.len()));

    // --- Two-view initialisation ---
    let mut cands: Vec<&PairMatch> = pairs.iter().filter(|p| p.matches.len() >= 40).collect();
    cands.sort_by(|a, b| b.matches.len().cmp(&a.matches.len()));
    cands.truncate(40);
    let mut best: Option<(f64, usize, usize, Pose)> = None;
    for pm in cands {
        let n1: Vec<[f64; 2]> =
            pm.matches.iter().map(|&(a, _)| { let u = uv(feats, pm.i, a as usize); intr0.normalize(u[0], u[1]) }).collect();
        let n2: Vec<[f64; 2]> =
            pm.matches.iter().map(|&(_, b)| { let u = uv(feats, pm.j, b as usize); intr0.normalize(u[0], u[1]) }).collect();
        let Some((e, mask)) = fundamental_ransac(&n1, &n2, thr_px / intr0.f, 99 + pm.i as u64 * 31 + pm.j as u64) else { continue };
        let svd = e.svd(true, true);
        let (Some(u), Some(vt)) = (svd.u, svd.v_t) else { continue };
        let e = u * M3::from_diagonal(&V3::new(1.0, 1.0, 0.0)) * vt;
        let in1: Vec<[f64; 2]> = (0..n1.len()).filter(|&i| mask[i]).map(|i| n1[i]).collect();
        let in2: Vec<[f64; 2]> = (0..n2.len()).filter(|&i| mask[i]).map(|i| n2[i]).collect();
        let Some((pose, _)) = pose_from_essential(&e, &in1, &in2) else { continue };
        let p0 = Pose::identity();
        let c = [p0.center(), pose.center()];
        let mut angles = Vec::new();
        for i in 0..in1.len() {
            if let Some(x) = triangulate(&[(&p0, in1[i]), (&pose, in2[i])]) {
                if x.z <= 0.0 || pose.to_cam(&x).z <= 0.0 {
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
        let score = good * (med / 5.0).min(1.0);
        if best.as_ref().map_or(true, |b| score > b.0) {
            best = Some((score, pm.i, pm.j, pose));
        }
    }
    let (score, a, b, pose) = best?;
    log(0.0, &format!("initial pair {}-{} (score {:.0})", a, b, score));
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
    loop {
        let mut counts: Vec<(usize, usize)> = (0..n_img)
            .filter(|&i| ctx.rec.poses[i].is_none())
            .map(|i| {
                let c = ctx.feat_track[i]
                    .iter()
                    .filter(|&&t| t >= 0 && ctx.track_point[t as usize] >= 0)
                    .count();
                (i, c)
            })
            .filter(|&(i, c)| c >= 15 && (failed[i] == 0 || c as f64 > failed[i] as f64 * 1.3))
            .collect();
        if counts.is_empty() {
            break;
        }
        counts.sort_by(|x, y| y.1.cmp(&x.1));
        let mut done = false;
        for &(img, c) in counts.iter().take(5) {
            if let Some(nin) = ctx.register(img) {
                let nt = ctx.triangulate_all(min_angle);
                let (r0, r1) = ctx.bundle(8, a);
                ctx.filter(min_angle);
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
            break;
        }
    }
    // --- Final refinement ---
    ctx.triangulate_all(min_angle);
    let (r0, r1) = ctx.bundle(40, a);
    ctx.filter(min_angle);
    ctx.triangulate_all(min_angle);
    let (_, r2) = ctx.bundle(15, a);
    ctx.filter(min_angle);
    log(0.0, &format!(
        "final BA: rms {:.2} -> {:.2} -> {:.2}px, focal {:.1}px, k1 {:.3}, {} cameras, {} points",
        r0, r1, r2, ctx.rec.intr.f, ctx.rec.intr.k1, ctx.rec.registered(), ctx.rec.points.len()
    ));
    let mut rec = ctx.rec;
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
