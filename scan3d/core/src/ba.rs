//! Sparse bundle adjustment: Levenberg–Marquardt with a Schur complement on
//! points, Huber loss, shared intrinsics (focal + radial k1).

use crate::geometry::{exp_so3, project_jac, Intrinsics, Pose, V3};
use nalgebra::{DMatrix, DVector, Matrix3, SMatrix, Vector2, Vector3};

pub struct Obs {
    pub cam: usize,
    pub point: usize,
    pub uv: [f64; 2],
}

pub struct BaOptions {
    pub iterations: usize,
    pub huber_px: f64,
    pub refine_intrinsics: bool,
    /// also refine radial distortion (needs many well-spread cameras)
    pub refine_k1: bool,
    pub fixed_cam: usize,
}

#[inline]
fn robust(e: f64, h: f64) -> f64 {
    if e < h { e * e } else { 2.0 * h * e - h * h }
}

fn total_cost(poses: &[Option<Pose>], k: &Intrinsics, pts: &[V3], obs: &[Obs], h: f64) -> f64 {
    obs.iter()
        .map(|o| match poses[o.cam].as_ref().and_then(|p| k.project(p, &pts[o.point])) {
            Some(q) => robust(((q.x - o.uv[0]).powi(2) + (q.y - o.uv[1]).powi(2)).sqrt(), h),
            None => 4.0 * h * h,
        })
        .sum()
}

/// Returns (initial RMS reprojection error, final RMS) in pixels.
pub fn bundle_adjust(
    poses: &mut [Option<Pose>],
    k: &mut Intrinsics,
    pts: &mut [V3],
    obs: &[Obs],
    opt: &BaOptions,
) -> (f64, f64) {
    let rms = |poses: &[Option<Pose>], k: &Intrinsics, pts: &[V3]| -> f64 {
        let mut s = 0.0;
        let mut n = 0usize;
        for o in obs {
            if let Some(q) = poses[o.cam].as_ref().and_then(|p| k.project(p, &pts[o.point])) {
                s += (q.x - o.uv[0]).powi(2) + (q.y - o.uv[1]).powi(2);
                n += 1;
            }
        }
        if n == 0 { 0.0 } else { (s / n as f64).sqrt() }
    };
    let rms0 = rms(poses, k, pts);
    if obs.is_empty() {
        return (rms0, rms0);
    }
    // Block layout of the reduced camera system.
    let nk = if opt.refine_intrinsics { 2 } else { 0 };
    let mut cam_off: Vec<Option<usize>> = vec![None; poses.len()];
    let mut nc = nk;
    for (i, p) in poses.iter().enumerate() {
        if p.is_some() && i != opt.fixed_cam {
            cam_off[i] = Some(nc);
            nc += 6;
        }
    }
    let mut by_point: Vec<Vec<usize>> = vec![Vec::new(); pts.len()];
    for (i, o) in obs.iter().enumerate() {
        if poses[o.cam].is_some() {
            by_point[o.point].push(i);
        }
    }
    let h = opt.huber_px;
    let mut cost = total_cost(poses, k, pts, obs, h);
    let mut lambda = 1e-4;

    struct PointBlock {
        vinv: Matrix3<f64>,
        gp: Vector3<f64>,
        ws: Vec<(usize, usize, SMatrix<f64, 6, 3>)>,
    }

    for _iter in 0..opt.iterations {
        let mut accepted = false;
        for _attempt in 0..6 {
            let mut s = DMatrix::<f64>::zeros(nc, nc);
            let mut rhs = DVector::<f64>::zeros(nc);
            let mut blocks: Vec<Option<PointBlock>> = Vec::with_capacity(pts.len());
            for (pi, list) in by_point.iter().enumerate() {
                if list.is_empty() {
                    blocks.push(None);
                    continue;
                }
                let mut v = Matrix3::<f64>::zeros();
                let mut gp = Vector3::<f64>::zeros();
                let mut ws: Vec<(usize, usize, SMatrix<f64, 6, 3>)> = Vec::new();
                let mut wk = SMatrix::<f64, 6, 3>::zeros();
                for &oi in list {
                    let o = &obs[oi];
                    let pose = poses[o.cam].as_ref().unwrap();
                    let Some((q, jc, mut jk, jp)) = project_jac(pose, k, &pts[pi]) else { continue };
                    if !opt.refine_k1 {
                        jk[(0, 1)] = 0.0;
                        jk[(1, 1)] = 0.0;
                    }
                    let r = Vector2::new(q.x - o.uv[0], q.y - o.uv[1]);
                    let e = r.norm();
                    let w = if e < h { 1.0 } else { h / e };
                    v += jp.transpose() * jp * w;
                    gp -= jp.transpose() * r * w;
                    if nk > 0 {
                        let kk = jk.transpose() * jk * w;
                        for a in 0..2 {
                            for b in 0..2 {
                                s[(a, b)] += kk[(a, b)];
                            }
                        }
                        let gk = jk.transpose() * r * w;
                        rhs[0] -= gk[0];
                        rhs[1] -= gk[1];
                        let wkp = jk.transpose() * jp * w;
                        for a in 0..2 {
                            for b in 0..3 {
                                wk[(a, b)] += wkp[(a, b)];
                            }
                        }
                    }
                    if let Some(off) = cam_off[o.cam] {
                        let cc = jc.transpose() * jc * w;
                        for a in 0..6 {
                            for b in 0..6 {
                                s[(off + a, off + b)] += cc[(a, b)];
                            }
                        }
                        let gc = jc.transpose() * r * w;
                        for a in 0..6 {
                            rhs[off + a] -= gc[a];
                        }
                        if nk > 0 {
                            let ck = jc.transpose() * jk * w;
                            for a in 0..6 {
                                for b in 0..2 {
                                    s[(off + a, b)] += ck[(a, b)];
                                    s[(b, off + a)] += ck[(a, b)];
                                }
                            }
                        }
                        ws.push((off, 6, jc.transpose() * jp * w));
                    }
                }
                if nk > 0 {
                    ws.push((0, 2, wk));
                }
                for d in 0..3 {
                    v[(d, d)] = v[(d, d)] * (1.0 + lambda) + 1e-9;
                }
                let Some(vinv) = v.try_inverse() else {
                    blocks.push(None);
                    continue;
                };
                blocks.push(Some(PointBlock { vinv, gp, ws }));
            }
            // Damping on the camera diagonal, then Schur reduction.
            for d in 0..nc {
                s[(d, d)] = s[(d, d)] * (1.0 + lambda) + 1e-9;
            }
            for b in blocks.iter().flatten() {
                let vg = b.vinv * b.gp;
                for (ao, asz, wa) in &b.ws {
                    let wav = wa * b.vinv;
                    let r = wa * vg;
                    for i in 0..*asz {
                        rhs[ao + i] -= r[i];
                    }
                    for (bo, bsz, wb) in &b.ws {
                        let m = wav * wb.transpose();
                        for i in 0..*asz {
                            for j in 0..*bsz {
                                s[(ao + i, bo + j)] -= m[(i, j)];
                            }
                        }
                    }
                }
            }
            let dc = if nc > 0 {
                match s.cholesky() {
                    Some(ch) => ch.solve(&rhs),
                    None => {
                        lambda *= 10.0;
                        continue;
                    }
                }
            } else {
                DVector::zeros(0)
            };
            // Back-substitute points and form candidate parameters.
            let mut np: Vec<Option<Pose>> = poses.to_vec();
            for (i, off) in cam_off.iter().enumerate() {
                if let Some(o) = off {
                    let p = np[i].as_mut().unwrap();
                    p.r = exp_so3(&V3::new(dc[*o], dc[o + 1], dc[o + 2])) * p.r;
                    p.t += V3::new(dc[o + 3], dc[o + 4], dc[o + 5]);
                }
            }
            let mut nk_ = *k;
            if nk > 0 {
                nk_.f += dc[0];
                nk_.k1 = (nk_.k1 + dc[1]).clamp(-1.0, 1.0);
            }
            let mut npts = pts.to_vec();
            for (pi, b) in blocks.iter().enumerate() {
                if let Some(b) = b {
                    let mut g = b.gp;
                    for (o, sz, w) in &b.ws {
                        for i in 0..*sz {
                            for j in 0..3 {
                                g[j] -= w[(i, j)] * dc[o + i];
                            }
                        }
                    }
                    npts[pi] += b.vinv * g;
                }
            }
            let c1 = total_cost(&np, &nk_, &npts, obs, h);
            if c1 < cost && nk_.f > 0.0 {
                let rel = (cost - c1) / cost.max(1e-12);
                poses.copy_from_slice(&np);
                *k = nk_;
                pts.copy_from_slice(&npts);
                cost = c1;
                lambda = (lambda / 3.0).max(1e-8);
                accepted = rel > 1e-7;
                break;
            }
            lambda *= 8.0;
        }
        if !accepted {
            break;
        }
    }
    (rms0, rms(poses, k, pts))
}
