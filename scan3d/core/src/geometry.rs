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

/// Real roots of a quartic c4 x^4 + c3 x^3 + c2 x^2 + c1 x + c0 (Durand–Kerner + Newton polish).
fn quartic_roots(c: [f64; 5]) -> Vec<f64> {
    if c[0].abs() < 1e-14 {
        return vec![];
    }
    let a: Vec<f64> = c.iter().map(|v| v / c[0]).collect();
    type C = (f64, f64);
    let mul = |x: C, y: C| (x.0 * y.0 - x.1 * y.1, x.0 * y.1 + x.1 * y.0);
    let sub = |x: C, y: C| (x.0 - y.0, x.1 - y.1);
    let div = |x: C, y: C| { let d = y.0 * y.0 + y.1 * y.1; ((x.0 * y.0 + x.1 * y.1) / d, (x.1 * y.0 - x.0 * y.1) / d) };
    let f = |z: C| { let mut r = (1.0, 0.0); for k in 1..5 { r = mul(r, z); r.0 += a[k]; } r };
    let scale = 1.0 + a[1..].iter().fold(0.0f64, |m, v| m.max(v.abs()));
    let mut z: Vec<C> = (0..4).map(|k| { let t = 0.4 + 0.9 * k as f64; (scale * 0.5 * t.cos(), scale * 0.5 * t.sin()) }).collect();
    for _ in 0..200 {
        let mut moved = 0.0f64;
        for i in 0..4 {
            let mut den = (1.0, 0.0);
            for j in 0..4 {
                if i != j {
                    den = mul(den, sub(z[i], z[j]));
                }
            }
            if den.0 == 0.0 && den.1 == 0.0 {
                den = (1e-12, 0.0);
            }
            let step = div(f(z[i]), den);
            z[i] = sub(z[i], step);
            moved = moved.max(step.0.abs() + step.1.abs());
        }
        if moved < 1e-14 {
            break;
        }
    }
    let p = |x: f64| (((x + a[1]) * x + a[2]) * x + a[3]) * x + a[4];
    let dp = |x: f64| ((4.0 * x + 3.0 * a[1]) * x + 2.0 * a[2]) * x + a[3];
    z.iter()
        .filter(|r| r.1.abs() < 1e-6 * (1.0 + r.0.abs()))
        .map(|r| {
            let mut x = r.0;
            for _ in 0..3 {
                let d = dp(x);
                if d.abs() > 1e-14 { x -= p(x) / d; }
            }
            x
        })
        .collect()
}

/// Rigid transform (R, t) with dst ≈ R src + t (Kabsch).
fn kabsch(src: &[V3], dst: &[V3]) -> Option<Pose> {
    let n = src.len() as f64;
    let ms = src.iter().fold(V3::zeros(), |a, b| a + b) / n;
    let md = dst.iter().fold(V3::zeros(), |a, b| a + b) / n;
    let mut h = M3::zeros();
    for (s, d) in src.iter().zip(dst) {
        h += (d - md) * (s - ms).transpose();
    }
    let svd = h.svd(true, true);
    let (u, vt) = (svd.u?, svd.v_t?);
    let mut dm = M3::identity();
    if (u * vt).determinant() < 0.0 {
        dm[(2, 2)] = -1.0;
    }
    let r = u * dm * vt;
    Some(Pose { r, t: md - r * ms })
}

/// Minimal absolute pose from three 3D points and their normalised image
/// coordinates (Grunert's solution). Up to four candidate poses.
pub fn p3p(xw: [&V3; 3], ns: [[f64; 2]; 3]) -> Vec<Pose> {
    let j: Vec<V3> = ns.iter().map(|n| V3::new(n[0], n[1], 1.0).normalize()).collect();
    let a2 = (xw[1] - xw[2]).norm_squared();
    let b2 = (xw[0] - xw[2]).norm_squared();
    let c2 = (xw[0] - xw[1]).norm_squared();
    if a2 < 1e-18 || b2 < 1e-18 || c2 < 1e-18 {
        return vec![];
    }
    let ca = j[1].dot(&j[2]);
    let cb = j[0].dot(&j[2]);
    let cg = j[0].dot(&j[1]);
    let amc = (a2 - c2) / b2;
    let apc = (a2 + c2) / b2;
    let c4 = (amc - 1.0).powi(2) - 4.0 * c2 / b2 * ca * ca;
    let c3 = 4.0 * (amc * (1.0 - amc) * cb - (1.0 - apc) * ca * cg + 2.0 * c2 / b2 * ca * ca * cb);
    let c2c = 2.0 * (amc * amc - 1.0 + 2.0 * amc * amc * cb * cb + 2.0 * ((b2 - c2) / b2) * ca * ca
        - 4.0 * apc * ca * cb * cg + 2.0 * ((b2 - a2) / b2) * cg * cg);
    let c1 = 4.0 * (-amc * (1.0 + amc) * cb + 2.0 * a2 / b2 * cg * cg * cb - (1.0 - apc) * ca * cg);
    let c0 = (1.0 + amc).powi(2) - 4.0 * a2 / b2 * cg * cg;
    let mut out = Vec::new();
    for v in quartic_roots([c4, c3, c2c, c1, c0]) {
        if v <= 0.0 {
            continue;
        }
        let den = 2.0 * (cg - v * ca);
        if den.abs() < 1e-12 {
            continue;
        }
        let u = ((-1.0 + amc) * v * v - 2.0 * amc * cb * v + 1.0 + amc) / den;
        if u <= 0.0 {
            continue;
        }
        let s1sq = b2 / (1.0 + v * v - 2.0 * v * cb);
        if s1sq <= 0.0 {
            continue;
        }
        let s1 = s1sq.sqrt();
        let pc = [j[0] * s1, j[1] * (u * s1), j[2] * (v * s1)];
        if let Some(p) = kabsch(&[*xw[0], *xw[1], *xw[2]], &pc) {
            out.push(p);
        }
    }
    out
}

#[allow(dead_code)]
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
        let idx = rng.sample(n, 3);
        for p in p3p([&xs[idx[0]], &xs[idx[1]], &xs[idx[2]]], [ns[idx[0]], ns[idx[1]], ns[idx[2]]]) {
            let cnt = (0..n).filter(|&i| err(&p, i) < thr2).count();
            if best.as_ref().map_or(true, |b| cnt > b.1) {
                best = Some((p, cnt));
                iters = ransac_iters(cnt as f64 / n as f64, 3, 1000);
            }
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

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn p3p_recovers_pose() {
        let mut rng = Rng::new(7);
        let mut r01 = || (rng.next_u64() % 10000) as f64 / 10000.0;
        for trial in 0..200 {
            let truth = Pose { r: exp_so3(&V3::new(r01() - 0.5, r01() - 0.5, r01() - 0.5)), t: V3::new(r01() - 0.5, r01() - 0.5, 4.0 + r01()) };
            // planar-ish and general configurations
            let pts: Vec<V3> = (0..3).map(|_| V3::new(2.0 * r01() - 1.0, if trial % 2 == 0 { 0.0 } else { 2.0 * r01() - 1.0 }, 2.0 * r01() - 1.0)).collect();
            let ns: Vec<[f64; 2]> = pts.iter().map(|x| { let c = truth.to_cam(x); [c.x / c.z, c.y / c.z] }).collect();
            let sols = p3p([&pts[0], &pts[1], &pts[2]], [ns[0], ns[1], ns[2]]);
            let best = sols.iter().map(|p| (p.r - truth.r).norm() + (p.t - truth.t).norm()).fold(f64::MAX, f64::min);
            assert!(best < 1e-5, "trial {trial}: {} solutions, best error {best}", sols.len());
        }
    }
}

fn homography_4pt(p1: &[[f64; 2]], p2: &[[f64; 2]], idx: &[usize]) -> Option<M3> {
    let mut ata = SMatrix::<f64, 9, 9>::zeros();
    for &i in idx {
        let (x1, y1, x2, y2) = (p1[i][0], p1[i][1], p2[i][0], p2[i][1]);
        let a = SMatrix::<f64, 9, 1>::from_column_slice(&[-x1, -y1, -1.0, 0.0, 0.0, 0.0, x2 * x1, x2 * y1, x2]);
        let b = SMatrix::<f64, 9, 1>::from_column_slice(&[0.0, 0.0, 0.0, -x1, -y1, -1.0, y2 * x1, y2 * y1, y2]);
        ata += a * a.transpose() + b * b.transpose();
    }
    let eig = ata.symmetric_eigen();
    let mut k = 0;
    for i in 1..9 {
        if eig.eigenvalues[i] < eig.eigenvalues[k] {
            k = i;
        }
    }
    let v = eig.eigenvectors.column(k);
    let h = M3::new(v[0], v[1], v[2], v[3], v[4], v[5], v[6], v[7], v[8]);
    (h.norm() > 1e-12).then_some(h)
}

#[inline]
fn h_err(h: &M3, a: &[f64; 2], b: &[f64; 2]) -> f64 {
    let q = h * V3::new(a[0], a[1], 1.0);
    if q.z.abs() < 1e-12 {
        return f64::MAX;
    }
    (q.x / q.z - b[0]).powi(2) + (q.y / q.z - b[1]).powi(2)
}

/// Robust homography between normalised coordinates; returns (H, inlier count).
pub fn homography_ransac(p1: &[[f64; 2]], p2: &[[f64; 2]], thr: f64, seed: u64) -> Option<(M3, usize)> {
    let n = p1.len();
    if n < 8 {
        return None;
    }
    let thr2 = thr * thr;
    let mut rng = Rng::new(seed);
    let mut best: Option<(M3, usize)> = None;
    let mut iters = 1000;
    let mut it = 0;
    while it < iters {
        it += 1;
        let idx = rng.sample(n, 4);
        let Some(h) = homography_4pt(p1, p2, &idx) else { continue };
        let cnt = (0..n).filter(|&i| h_err(&h, &p1[i], &p2[i]) < thr2).count();
        if best.as_ref().map_or(true, |b| cnt > b.1) {
            best = Some((h, cnt));
            iters = ransac_iters(cnt as f64 / n as f64, 4, 1000);
        }
    }
    let (mut h, _) = best?;
    let inl: Vec<usize> = (0..n).filter(|&i| h_err(&h, &p1[i], &p2[i]) < thr2).collect();
    if inl.len() >= 8 {
        if let Some(h2) = homography_4pt(p1, p2, &inl) {
            h = h2;
        }
    }
    let cnt = (0..n).filter(|&i| h_err(&h, &p1[i], &p2[i]) < thr2).count();
    Some((h, cnt))
}

/// Motion hypotheses from a homography in normalised coordinates
/// (Faugeras' decomposition, as used in ORB-SLAM).
pub fn decompose_homography(h: &M3) -> Vec<Pose> {
    let svd = h.svd(true, true);
    let (Some(u), Some(vt)) = (svd.u, svd.v_t) else { return vec![] };
    let s = u.determinant() * vt.determinant();
    let (d1, d2, d3) = (svd.singular_values[0], svd.singular_values[1], svd.singular_values[2]);
    if d1 / d2 < 1.00001 || d2 / d3 < 1.00001 {
        return vec![];
    }
    let x1 = ((d1 * d1 - d2 * d2) / (d1 * d1 - d3 * d3)).sqrt();
    let x3 = ((d2 * d2 - d3 * d3) / (d1 * d1 - d3 * d3)).sqrt();
    let x1s = [x1, x1, -x1, -x1];
    let x3s = [x3, -x3, x3, -x3];
    let mut out = Vec::new();
    // d' = d2
    let aux = ((d1 * d1 - d2 * d2) * (d2 * d2 - d3 * d3)).sqrt() / ((d1 + d3) * d2);
    let ct = (d2 * d2 + d1 * d3) / ((d1 + d3) * d2);
    let st = [aux, -aux, -aux, aux];
    for i in 0..4 {
        let rp = M3::new(ct, 0.0, -st[i], 0.0, 1.0, 0.0, st[i], 0.0, ct);
        let r = u * rp * vt * s;
        let tp = V3::new(x1s[i], 0.0, -x3s[i]) * (d1 - d3);
        let t = (u * tp).normalize();
        out.push(Pose { r, t });
    }
    // d' = -d2
    let aux = ((d1 * d1 - d2 * d2) * (d2 * d2 - d3 * d3)).sqrt() / ((d1 - d3) * d2);
    let cp = (d1 * d3 - d2 * d2) / ((d1 - d3) * d2);
    let sp = [aux, -aux, -aux, aux];
    for i in 0..4 {
        let rp = M3::new(cp, 0.0, sp[i], 0.0, -1.0, 0.0, sp[i], 0.0, -cp);
        let r = u * rp * vt * s;
        let tp = V3::new(x1s[i], 0.0, x3s[i]) * (d1 + d3);
        let t = (u * tp).normalize();
        out.push(Pose { r, t });
    }
    out.retain(|p| p.r.determinant() > 0.0);
    out
}

/// Number of matches a relative pose explains: triangulated in front of both
/// cameras with small reprojection error (normalised units).
pub fn count_good(pose: &Pose, n1: &[[f64; 2]], n2: &[[f64; 2]], thr: f64) -> usize {
    let p0 = Pose::identity();
    let thr2 = thr * thr;
    (0..n1.len())
        .filter(|&i| {
            let Some(x) = triangulate(&[(&p0, n1[i]), (pose, n2[i])]) else { return false };
            let c2 = pose.to_cam(&x);
            if x.z <= 0.0 || c2.z <= 0.0 {
                return false;
            }
            let e1 = (x.x / x.z - n1[i][0]).powi(2) + (x.y / x.z - n1[i][1]).powi(2);
            let e2 = (c2.x / c2.z - n2[i][0]).powi(2) + (c2.y / c2.z - n2[i][1]).powi(2);
            e1 < thr2 && e2 < thr2
        })
        .count()
}

/// Robust relative pose of camera 2 w.r.t. camera 1 from normalised matches.
/// Considers both the essential-matrix and the homography (planar scene)
/// models and keeps the candidate that explains the most matches.
pub fn relative_pose(n1: &[[f64; 2]], n2: &[[f64; 2]], thr: f64, seed: u64) -> Option<(Pose, usize)> {
    let mut cands: Vec<Pose> = Vec::new();
    if let Some((e, mask)) = fundamental_ransac(n1, n2, thr, seed) {
        if let Some((u, vt)) = { let s = e.svd(true, true); s.u.zip(s.v_t) } {
            let e = u * M3::from_diagonal(&V3::new(1.0, 1.0, 0.0)) * vt;
            let in1: Vec<[f64; 2]> = (0..n1.len()).filter(|&i| mask[i]).map(|i| n1[i]).collect();
            let in2: Vec<[f64; 2]> = (0..n2.len()).filter(|&i| mask[i]).map(|i| n2[i]).collect();
            if let Some((p, _)) = pose_from_essential(&e, &in1, &in2) {
                cands.push(p);
            }
        }
    }
    if let Some((h, _)) = homography_ransac(n1, n2, thr, seed ^ 0x5bd1) {
        cands.extend(decompose_homography(&h));
    }
    let mut best: Option<(Pose, usize)> = None;
    for c in cands {
        // polish the candidate on the matches it explains
        let good = count_good(&c, n1, n2, 2.0 * thr);
        if best.as_ref().map_or(true, |b| good > b.1) {
            best = Some((c, good));
        }
    }
    best
}

#[cfg(test)]
mod tests_h {
    use super::*;
    #[test]
    fn relative_pose_on_planar_scene() {
        let mut rng = Rng::new(11);
        let mut r01 = || (rng.next_u64() % 100000) as f64 / 100000.0;
        let truth = Pose { r: exp_so3(&V3::new(0.1, 0.35, -0.05)), t: V3::new(-0.6, 0.05, 0.1).normalize() };
        // points on the plane y = 1 (a floor seen by a camera looking forward/down)
        let pts: Vec<V3> = (0..200).map(|_| V3::new(4.0 * r01() - 2.0, 1.0, 3.0 + 4.0 * r01())).collect();
        let n1: Vec<[f64; 2]> = pts.iter().map(|x| [x.x / x.z, x.y / x.z]).collect();
        let n2: Vec<[f64; 2]> = pts.iter().map(|x| { let c = truth.to_cam(x); [c.x / c.z, c.y / c.z] }).collect();
        let (p, good) = relative_pose(&n1, &n2, 0.002, 3).expect("pose");
        let err_r = (p.r - truth.r).norm();
        let err_t = (p.t.normalize() - truth.t).norm();
        assert!(good > 190 && err_r < 1e-3 && err_t < 1e-3, "good {good} err_r {err_r} err_t {err_t}");
    }
}
