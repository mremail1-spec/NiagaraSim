//! Multi-view geometry: camera model, fundamental/essential matrices,
//! triangulation and PnP, all with RANSAC.

use nalgebra::{Matrix3, Matrix4, Rotation3, SMatrix, Vector2, Vector3};

pub type M3 = Matrix3<f64>;
pub type V3 = Vector3<f64>;

/// Shared pinhole camera with one radial distortion coefficient.
#[derive(Clone, Copy, Debug)]
pub struct Intrinsics {
    pub f: f64,
    pub cx: f64,
    pub cy: f64,
    pub k1: f64,
}

#[derive(Clone, Copy, Debug)]
pub struct Pose {
    pub r: M3,
    pub t: V3,
}

impl Pose {
    pub fn identity() -> Self {
        Pose { r: M3::identity(), t: V3::zeros() }
    }
    pub fn center(&self) -> V3 {
        -(self.r.transpose() * self.t)
    }
    #[inline]
    pub fn to_cam(&self, x: &V3) -> V3 {
        self.r * x + self.t
    }
}

impl Intrinsics {
    #[inline]
    pub fn project_cam(&self, xc: &V3) -> Option<Vector2<f64>> {
        if xc.z <= 1e-9 {
            return None;
        }
        let xn = xc.x / xc.z;
        let yn = xc.y / xc.z;
        let d = 1.0 + self.k1 * (xn * xn + yn * yn);
        Some(Vector2::new(self.f * d * xn + self.cx, self.f * d * yn + self.cy))
    }
    #[inline]
    pub fn project(&self, pose: &Pose, x: &V3) -> Option<Vector2<f64>> {
        self.project_cam(&pose.to_cam(x))
    }
    /// Pixel -> undistorted normalised image coordinates.
    pub fn normalize(&self, u: f64, v: f64) -> [f64; 2] {
        let xd = (u - self.cx) / self.f;
        let yd = (v - self.cy) / self.f;
        let (mut x, mut y) = (xd, yd);
        for _ in 0..6 {
            let d = 1.0 + self.k1 * (x * x + y * y);
            x = xd / d;
            y = yd / d;
        }
        [x, y]
    }
}

pub struct Rng(u64);
impl Rng {
    pub fn new(seed: u64) -> Self {
        Rng(seed.max(1))
    }
    pub fn next_u64(&mut self) -> u64 {
        self.0 ^= self.0 << 13;
        self.0 ^= self.0 >> 7;
        self.0 ^= self.0 << 17;
        self.0
    }
    pub fn below(&mut self, n: usize) -> usize {
        (self.next_u64() % n as u64) as usize
    }
    pub fn sample(&mut self, n: usize, k: usize) -> Vec<usize> {
        let mut out: Vec<usize> = Vec::with_capacity(k);
        while out.len() < k {
            let c = self.below(n);
            if !out.contains(&c) {
                out.push(c);
            }
        }
        out
    }
}

fn ransac_iters(inlier_ratio: f64, sample: i32, cap: usize) -> usize {
    let p = inlier_ratio.powi(sample).clamp(1e-9, 1.0 - 1e-12);
    let n = ((1.0f64 - 0.999).ln() / (1.0 - p).ln()).ceil();
    if n.is_finite() { (n as usize).clamp(30, cap) } else { cap }
}

fn hartley(pts: &[[f64; 2]]) -> M3 {
    let n = pts.len() as f64;
    let (mx, my) = pts.iter().fold((0.0, 0.0), |a, p| (a.0 + p[0] / n, a.1 + p[1] / n));
    let md = pts.iter().map(|p| ((p[0] - mx).powi(2) + (p[1] - my).powi(2)).sqrt()).sum::<f64>() / n;
    let s = if md > 1e-12 { std::f64::consts::SQRT_2 / md } else { 1.0 };
    M3::new(s, 0.0, -s * mx, 0.0, s, -s * my, 0.0, 0.0, 1.0)
}

fn apply(t: &M3, p: &[f64; 2]) -> [f64; 2] {
    [t[(0, 0)] * p[0] + t[(0, 2)], t[(1, 1)] * p[1] + t[(1, 2)]]
}

fn eight_point(p1: &[[f64; 2]], p2: &[[f64; 2]], idx: &[usize]) -> Option<M3> {
    let mut ata = SMatrix::<f64, 9, 9>::zeros();
    for &i in idx {
        let (x1, y1) = (p1[i][0], p1[i][1]);
        let (x2, y2) = (p2[i][0], p2[i][1]);
        let a = SMatrix::<f64, 9, 1>::from_column_slice(&[x2 * x1, x2 * y1, x2, y2 * x1, y2 * y1, y2, x1, y1, 1.0]);
        ata += a * a.transpose();
    }
    let eig = ata.symmetric_eigen();
    let (mut k, mut best) = (0, f64::MAX);
    for i in 0..9 {
        if eig.eigenvalues[i] < best {
            best = eig.eigenvalues[i];
            k = i;
        }
    }
    let v = eig.eigenvectors.column(k);
    let f = M3::new(v[0], v[1], v[2], v[3], v[4], v[5], v[6], v[7], v[8]);
    let svd = f.svd(true, true);
    let (u, vt) = (svd.u?, svd.v_t?);
    let mut s = svd.singular_values;
    // singular values are sorted descending
    s[2] = 0.0;
    Some(u * M3::from_diagonal(&s) * vt)
}

#[inline]
pub fn sampson(f: &M3, a: &[f64; 2], b: &[f64; 2]) -> f64 {
    let x1 = V3::new(a[0], a[1], 1.0);
    let x2 = V3::new(b[0], b[1], 1.0);
    let fx1 = f * x1;
    let ftx2 = f.transpose() * x2;
    let e = x2.dot(&fx1);
    e * e / (fx1.x * fx1.x + fx1.y * fx1.y + ftx2.x * ftx2.x + ftx2.y * ftx2.y + 1e-30)
}

/// Robust fundamental matrix in pixel coordinates. Returns (F, inlier mask).
pub fn fundamental_ransac(p1: &[[f64; 2]], p2: &[[f64; 2]], thr_px: f64, seed: u64) -> Option<(M3, Vec<bool>)> {
    let n = p1.len();
    if n < 12 {
        return None;
    }
    let t1 = hartley(p1);
    let t2 = hartley(p2);
    let q1: Vec<[f64; 2]> = p1.iter().map(|p| apply(&t1, p)).collect();
    let q2: Vec<[f64; 2]> = p2.iter().map(|p| apply(&t2, p)).collect();
    let s = 0.5 * (t1[(0, 0)] + t2[(0, 0)]);
    let thr = (thr_px * s).powi(2);
    let mut rng = Rng::new(seed);
    let mut best: Option<(M3, usize)> = None;
    let mut iters = 2000;
    let mut it = 0;
    while it < iters {
        it += 1;
        let idx = rng.sample(n, 8);
        let Some(f) = eight_point(&q1, &q2, &idx) else { continue };
        let cnt = (0..n).filter(|&i| sampson(&f, &q1[i], &q2[i]) < thr).count();
        if best.as_ref().map_or(true, |b| cnt > b.1) {
            best = Some((f, cnt));
            iters = ransac_iters(cnt as f64 / n as f64, 8, 2000);
        }
    }
    let (mut f, _) = best?;
    for _ in 0..2 {
        let inl: Vec<usize> = (0..n).filter(|&i| sampson(&f, &q1[i], &q2[i]) < thr).collect();
        if inl.len() < 8 {
            break;
        }
        f = eight_point(&q1, &q2, &inl)?;
    }
    let mask: Vec<bool> = (0..n).map(|i| sampson(&f, &q1[i], &q2[i]) < thr).collect();
    Some((t2.transpose() * f * t1, mask))
}

pub fn skew(v: &V3) -> M3 {
    M3::new(0.0, -v.z, v.y, v.z, 0.0, -v.x, -v.y, v.x, 0.0)
}

pub fn exp_so3(w: &V3) -> M3 {
    Rotation3::new(*w).into_inner()
}

/// Linear triangulation from (pose, normalised coordinate) pairs.
pub fn triangulate(views: &[(&Pose, [f64; 2])]) -> Option<V3> {
    let mut ata = Matrix4::<f64>::zeros();
    for (p, x) in views {
        let r = &p.r;
        let t = &p.t;
        let row = |i: usize| nalgebra::RowVector4::new(r[(i, 0)], r[(i, 1)], r[(i, 2)], t[i]);
        let a1 = row(0) - row(2) * x[0];
        let a2 = row(1) - row(2) * x[1];
        ata += a1.transpose() * a1 + a2.transpose() * a2;
    }
    let eig = ata.symmetric_eigen();
    let mut k = 0;
    for i in 1..4 {
        if eig.eigenvalues[i] < eig.eigenvalues[k] {
            k = i;
        }
    }
    let v = eig.eigenvectors.column(k);
    if v[3].abs() < 1e-12 {
        return None;
    }
    Some(V3::new(v[0] / v[3], v[1] / v[3], v[2] / v[3]))
}

/// Largest angle (radians) between viewing rays of a point.
pub fn triangulation_angle(centers: &[V3], x: &V3) -> f64 {
    let mut best: f64 = 0.0;
    for i in 0..centers.len() {
        let a = (x - centers[i]).normalize();
        for j in i + 1..centers.len() {
            let b = (x - centers[j]).normalize();
            best = best.max(a.dot(&b).clamp(-1.0, 1.0).acos());
        }
    }
    best
}

/// Recover relative pose (R, t) of camera 2 from an essential matrix using cheirality.
pub fn pose_from_essential(e: &M3, n1: &[[f64; 2]], n2: &[[f64; 2]]) -> Option<(Pose, usize)> {
    let svd = e.svd(true, true);
    let mut u = svd.u?;
    let mut vt = svd.v_t?;
    if u.determinant() < 0.0 {
        u = -u;
    }
    if vt.determinant() < 0.0 {
        vt = -vt;
    }
    let w = M3::new(0.0, -1.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0);
    let t: V3 = u.column(2).into();
    let p0 = Pose::identity();
    let mut best: Option<(Pose, usize)> = None;
    for r in [u * w * vt, u * w.transpose() * vt] {
        for tt in [t, -t] {
            let p = Pose { r, t: tt };
            let mut cnt = 0;
            for i in 0..n1.len() {
                if let Some(x) = triangulate(&[(&p0, n1[i]), (&p, n2[i])]) {
                    if x.z > 0.0 && p.to_cam(&x).z > 0.0 {
                        cnt += 1;
                    }
                }
            }
            if best.as_ref().map_or(true, |b| cnt > b.1) {
                best = Some((p, cnt));
            }
        }
    }
    best
}

fn pnp_dlt(xs: &[V3], ns: &[[f64; 2]], idx: &[usize]) -> Option<Pose> {
    let n = idx.len() as f64;
    let c = idx.iter().fold(V3::zeros(), |a, &i| a + xs[i]) / n;
    let sd = idx.iter().map(|&i| (xs[i] - c).norm()).sum::<f64>() / n;
    if sd < 1e-12 {
        return None;
    }
    let mut ata = SMatrix::<f64, 12, 12>::zeros();
    for &i in idx {
        let p = (xs[i] - c) / sd;
        let (x, y) = (ns[i][0], ns[i][1]);
        let r1 = SMatrix::<f64, 12, 1>::from_column_slice(&[p.x, p.y, p.z, 1.0, 0.0, 0.0, 0.0, 0.0, -x * p.x, -x * p.y, -x * p.z, -x]);
        let r2 = SMatrix::<f64, 12, 1>::from_column_slice(&[0.0, 0.0, 0.0, 0.0, p.x, p.y, p.z, 1.0, -y * p.x, -y * p.y, -y * p.z, -y]);
        ata += r1 * r1.transpose() + r2 * r2.transpose();
    }
    let eig = ata.symmetric_eigen();
    let mut k = 0;
    for i in 1..12 {
        if eig.eigenvalues[i] < eig.eigenvalues[k] {
            k = i;
        }
    }
    let v = eig.eigenvectors.column(k);
    let mut m = M3::new(v[0], v[1], v[2], v[4], v[5], v[6], v[8], v[9], v[10]);
    let mut tt = V3::new(v[3], v[7], v[11]);
    if m.determinant() < 0.0 {
        m = -m;
        tt = -tt;
    }
    let svd = m.svd(true, true);
    let r = svd.u? * svd.v_t?;
    let s = svd.singular_values.mean();
    if s < 1e-12 {
        return None;
    }
    // P' = [sR | tt] applies to (X - c)/sd  =>  x ~ R X + (tt*sd/s - R c)... scale consistently:
    // sR (X - c)/sd + tt  ∝  R X - R c + tt*sd/s
    let t = tt * sd / s - r * c;
    Some(Pose { r, t })
}

/// Robust absolute pose from 3D-2D correspondences (pixels). Returns pose and inlier mask.
pub fn pnp_ransac(xs: &[V3], uv: &[[f64; 2]], k: &Intrinsics, thr_px: f64, seed: u64) -> Option<(Pose, Vec<bool>)> {
    let n = xs.len();
    if n < 8 {
        return None;
    }
    let ns: Vec<[f64; 2]> = uv.iter().map(|p| k.normalize(p[0], p[1])).collect();
    let thr2 = thr_px * thr_px;
    let err = |p: &Pose, i: usize| -> f64 {
        match k.project(p, &xs[i]) {
            Some(q) => (q.x - uv[i][0]).powi(2) + (q.y - uv[i][1]).powi(2),
            None => f64::MAX,
        }
    };
    let mut rng = Rng::new(seed);
    let mut best: Option<(Pose, usize)> = None;
    let mut iters = 1000;
    let mut it = 0;
    while it < iters {
        it += 1;
        let idx = rng.sample(n, 6);
        let Some(p) = pnp_dlt(xs, &ns, &idx) else { continue };
        let cnt = (0..n).filter(|&i| err(&p, i) < thr2).count();
        if best.as_ref().map_or(true, |b| cnt > b.1) {
            best = Some((p, cnt));
            iters = ransac_iters(cnt as f64 / n as f64, 6, 1000);
        }
    }
    let (mut pose, _) = best?;
    for _ in 0..3 {
        let inl: Vec<usize> = (0..n).filter(|&i| err(&pose, i) < thr2 * 4.0).collect();
        if inl.len() < 6 {
            break;
        }
        pose = refine_pose(&pose, xs, uv, &inl, k, thr_px);
    }
    let mask: Vec<bool> = (0..n).map(|i| err(&pose, i) < thr2).collect();
    Some((pose, mask))
}

/// Projection and its Jacobians w.r.t. pose (left rotation increment, translation),
/// intrinsics (f, k1) and the 3D point.
#[allow(clippy::type_complexity)]
#[inline]
pub fn project_jac(
    pose: &Pose,
    k: &Intrinsics,
    x: &V3,
) -> Option<(Vector2<f64>, SMatrix<f64, 2, 6>, SMatrix<f64, 2, 2>, SMatrix<f64, 2, 3>)> {
    let xr = pose.r * x;
    let xc = xr + pose.t;
    let z = xc.z;
    if z <= 1e-9 {
        return None;
    }
    let xn = xc.x / z;
    let yn = xc.y / z;
    let r2 = xn * xn + yn * yn;
    let d = 1.0 + k.k1 * r2;
    let uv = Vector2::new(k.f * d * xn + k.cx, k.f * d * yn + k.cy);
    let dd = SMatrix::<f64, 2, 2>::new(
        k.f * (d + 2.0 * k.k1 * xn * xn), k.f * 2.0 * k.k1 * xn * yn,
        k.f * 2.0 * k.k1 * xn * yn, k.f * (d + 2.0 * k.k1 * yn * yn),
    );
    let nn = SMatrix::<f64, 2, 3>::new(1.0 / z, 0.0, -xn / z, 0.0, 1.0 / z, -yn / z);
    let a = dd * nn;
    let jp = a * pose.r;
    let jw = a * (-skew(&xr));
    let mut jc = SMatrix::<f64, 2, 6>::zeros();
    jc.fixed_view_mut::<2, 3>(0, 0).copy_from(&jw);
    jc.fixed_view_mut::<2, 3>(0, 3).copy_from(&a);
    let jk = SMatrix::<f64, 2, 2>::new(d * xn, k.f * xn * r2, d * yn, k.f * yn * r2);
    Some((uv, jc, jk, jp))
}

/// Gauss-Newton (Huber-weighted) pose refinement with fixed points.
pub fn refine_pose(pose: &Pose, xs: &[V3], uv: &[[f64; 2]], idx: &[usize], k: &Intrinsics, huber: f64) -> Pose {
    let mut p = *pose;
    let mut lambda = 1e-3;
    let cost = |p: &Pose| -> f64 {
        idx.iter()
            .map(|&i| match k.project(p, &xs[i]) {
                Some(q) => {
                    let e = ((q.x - uv[i][0]).powi(2) + (q.y - uv[i][1]).powi(2)).sqrt();
                    if e < huber { e * e } else { 2.0 * huber * e - huber * huber }
                }
                None => 1e6,
            })
            .sum()
    };
    let mut c0 = cost(&p);
    for _ in 0..15 {
        let mut h = SMatrix::<f64, 6, 6>::zeros();
        let mut g = SMatrix::<f64, 6, 1>::zeros();
        for &i in idx {
            if let Some((q, jc, _, _)) = project_jac(&p, k, &xs[i]) {
                let r = Vector2::new(q.x - uv[i][0], q.y - uv[i][1]);
                let e = r.norm();
                let w = if e < huber { 1.0 } else { huber / e };
                h += jc.transpose() * jc * w;
                g -= jc.transpose() * r * w;
            }
        }
        let mut improved = false;
        for _ in 0..5 {
            let mut hd = h;
            for d in 0..6 {
                hd[(d, d)] *= 1.0 + lambda;
                hd[(d, d)] += 1e-12;
            }
            let Some(ch) = hd.cholesky() else {
                lambda *= 10.0;
                continue;
            };
            let dx = ch.solve(&g);
            let np = Pose {
                r: exp_so3(&V3::new(dx[0], dx[1], dx[2])) * p.r,
                t: p.t + V3::new(dx[3], dx[4], dx[5]),
            };
            let c1 = cost(&np);
            if c1 < c0 {
                p = np;
                let rel = (c0 - c1) / c0.max(1e-12);
                c0 = c1;
                lambda = (lambda * 0.3).max(1e-7);
                improved = rel > 1e-6;
                break;
            }
            lambda *= 10.0;
        }
        if !improved {
            break;
        }
    }
    p
}
