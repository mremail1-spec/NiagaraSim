//! Synthetic test scene: a ray-traced, procedurally textured tabletop
//! (ground plane, sphere, two boxes) seen from an orbiting camera.

use crate::geometry::{Intrinsics, Pose, M3, V3};

fn hash(x: i32, y: i32, z: i32, s: u32) -> f64 {
    let mut h = (x as u32).wrapping_mul(0x8da6b343) ^ (y as u32).wrapping_mul(0xd8163841) ^ (z as u32).wrapping_mul(0xcb1ab31f) ^ s.wrapping_mul(0x165667b1);
    h ^= h >> 13;
    h = h.wrapping_mul(0x5bd1e995);
    h ^= h >> 15;
    (h & 0xffffff) as f64 / 0xffffff as f64
}

fn noise(p: V3, s: u32) -> f64 {
    let (xi, yi, zi) = (p.x.floor() as i32, p.y.floor() as i32, p.z.floor() as i32);
    let f = |t: f64| t * t * (3.0 - 2.0 * t);
    let (fx, fy, fz) = (f(p.x - xi as f64), f(p.y - yi as f64), f(p.z - zi as f64));
    let mut acc = 0.0;
    for dz in 0..2 {
        for dy in 0..2 {
            for dx in 0..2 {
                let w = (if dx == 1 { fx } else { 1.0 - fx }) * (if dy == 1 { fy } else { 1.0 - fy }) * (if dz == 1 { fz } else { 1.0 - fz });
                acc += w * hash(xi + dx, yi + dy, zi + dz, s);
            }
        }
    }
    acc
}

fn fbm(p: V3, s: u32) -> f64 {
    let (mut a, mut f, mut sum, mut norm) = (1.0, 1.0, 0.0, 0.0);
    for o in 0..5 {
        sum += a * noise(p * f, s + o);
        norm += a;
        a *= 0.55;
        f *= 2.1;
    }
    sum / norm
}

pub struct Hit {
    pub t: f64,
    pub n: V3,
    pub obj: u32,
}

const SPHERE_C: V3 = V3::new(0.0, 0.6, 0.0);
const SPHERE_R: f64 = 0.6;
const BOXES: [([f64; 3], [f64; 3]); 2] = [([0.8, 0.0, -1.0], [1.4, 0.7, -0.4]), ([-1.5, 0.0, 0.3], [-0.9, 1.0, 0.9])];
const GROUND: f64 = 2.5;

fn intersect(o: &V3, d: &V3) -> Option<Hit> {
    let mut best: Option<Hit> = None;
    let mut take = |h: Hit| {
        if h.t > 1e-6 && best.as_ref().map_or(true, |b| h.t < b.t) {
            best = Some(h);
        }
    };
    if d.y.abs() > 1e-12 {
        let t = -o.y / d.y;
        let p = o + d * t;
        if p.x.abs() <= GROUND && p.z.abs() <= GROUND {
            take(Hit { t, n: V3::y(), obj: 0 });
        }
    }
    let oc = o - SPHERE_C;
    let b = oc.dot(d);
    let c = oc.dot(&oc) - SPHERE_R * SPHERE_R;
    let disc = b * b - c;
    if disc > 0.0 {
        let t = -b - disc.sqrt();
        take(Hit { t, n: (o + d * t - SPHERE_C).normalize(), obj: 1 });
    }
    for (bi, (lo, hi)) in BOXES.iter().enumerate() {
        let (mut tmin, mut tmax, mut axis) = (f64::MIN, f64::MAX, 0);
        let mut sign = 1.0;
        let mut miss = false;
        for a in 0..3 {
            if d[a].abs() < 1e-12 {
                if o[a] < lo[a] || o[a] > hi[a] {
                    miss = true;
                }
                continue;
            }
            let t1 = (lo[a] - o[a]) / d[a];
            let t2 = (hi[a] - o[a]) / d[a];
            let (tn, tf) = if t1 < t2 { (t1, t2) } else { (t2, t1) };
            if tn > tmin {
                tmin = tn;
                axis = a;
                sign = if t1 < t2 { -1.0 } else { 1.0 };
            }
            tmax = tmax.min(tf);
        }
        if !miss && tmin <= tmax && tmin > 0.0 {
            let mut n = V3::zeros();
            n[axis] = sign;
            take(Hit { t: tmin, n, obj: 2 + bi as u32 });
        }
    }
    best
}

fn shade(o: &V3, d: &V3) -> [f64; 3] {
    let Some(h) = intersect(o, d) else {
        let t = 0.5 + 0.5 * d.y;
        return [180.0 + 50.0 * t, 200.0 + 40.0 * t, 230.0];
    };
    let p = o + d * h.t;
    let base: [f64; 3] = match h.obj {
        0 => [170.0, 150.0, 120.0],
        1 => [200.0, 90.0, 70.0],
        2 => [80.0, 140.0, 200.0],
        _ => [110.0, 190.0, 100.0],
    };
    let n1 = fbm(p * 7.0, h.obj * 10);
    let n2 = fbm(p * 2.5 + V3::new(5.0, 1.0, 3.0), h.obj * 10 + 5);
    let spots = if n2 > 0.55 { 0.55 } else { 1.0 };
    let light = V3::new(0.4, 1.0, 0.3).normalize();
    let lam = 0.4 + 0.6 * h.n.dot(&light).max(0.0);
    let k = lam * (0.35 + 1.1 * n1) * spots;
    [(base[0] * k).min(255.0), (base[1] * k).min(255.0), (base[2] * k).min(255.0)]
}

pub fn look_at(eye: V3, target: V3) -> Pose {
    let f = (target - eye).normalize();
    let r = f.cross(&V3::y()).normalize();
    let d = f.cross(&r);
    let rot = M3::from_rows(&[r.transpose(), d.transpose(), f.transpose()]);
    Pose { r: rot, t: -(rot * eye) }
}

pub fn render(pose: &Pose, k: &Intrinsics, w: usize, h: usize) -> Vec<u8> {
    let mut img = vec![255u8; w * h * 4];
    let c = pose.center();
    let rt = pose.r.transpose();
    for y in 0..h {
        for x in 0..w {
            let mut acc = [0.0; 3];
            for (sx, sy) in [(0.25, 0.25), (0.75, 0.25), (0.25, 0.75), (0.75, 0.75)] {
                let u = x as f64 + sx - 0.5;
                let v = y as f64 + sy - 0.5;
                let dir = (rt * V3::new((u - k.cx) / k.f, (v - k.cy) / k.f, 1.0)).normalize();
                let s = shade(&c, &dir);
                for a in 0..3 {
                    acc[a] += s[a] * 0.25;
                }
            }
            for a in 0..3 {
                img[4 * (y * w + x) + a] = acc[a].round().clamp(0.0, 255.0) as u8;
            }
        }
    }
    img
}

/// Camera positions on an orbit around the scene.
pub fn orbit(n: usize, radius: f64) -> Vec<Pose> {
    (0..n)
        .map(|i| {
            let a = i as f64 / n as f64 * std::f64::consts::TAU;
            let hgt = if i % 2 == 0 { 1.3 } else { 1.9 };
            look_at(V3::new(radius * a.cos(), hgt, radius * a.sin()), V3::new(0.0, 0.35, 0.0))
        })
        .collect()
}

pub fn intrinsics(w: usize, h: usize) -> Intrinsics {
    Intrinsics { f: 0.866 * w as f64, cx: w as f64 / 2.0 - 0.5, cy: h as f64 / 2.0 - 0.5, k1: 0.0 }
}

/// Distance from a point to the nearest scene surface.
pub fn surface_distance(p: &V3) -> f64 {
    let mut d = if p.x.abs() <= GROUND && p.z.abs() <= GROUND { p.y.abs() } else { f64::MAX };
    d = d.min(((p - SPHERE_C).norm() - SPHERE_R).abs());
    for (lo, hi) in BOXES.iter() {
        let c = V3::new(lo[0] + hi[0], lo[1] + hi[1], lo[2] + hi[2]) * 0.5;
        let e = V3::new(hi[0] - lo[0], hi[1] - lo[1], hi[2] - lo[2]) * 0.5;
        let q = (p - c).abs() - e;
        let outside = q.map(|v| v.max(0.0)).norm();
        let inside = q.x.max(q.y).max(q.z).min(0.0);
        d = d.min((outside + inside).abs());
    }
    d
}

/// Similarity (s, R, t) mapping `src` onto `dst` (Umeyama).
pub fn umeyama(src: &[V3], dst: &[V3]) -> (f64, M3, V3) {
    let n = src.len() as f64;
    let ms = src.iter().fold(V3::zeros(), |a, b| a + b) / n;
    let md = dst.iter().fold(V3::zeros(), |a, b| a + b) / n;
    let mut cov = M3::zeros();
    let mut var = 0.0;
    for (s, d) in src.iter().zip(dst) {
        cov += (d - md) * (s - ms).transpose();
        var += (s - ms).norm_squared();
    }
    cov /= n;
    var /= n;
    let svd = cov.svd(true, true);
    let (u, vt) = (svd.u.unwrap(), svd.v_t.unwrap());
    let mut sm = M3::identity();
    if u.determinant() * vt.determinant() < 0.0 {
        sm[(2, 2)] = -1.0;
    }
    let r = u * sm * vt;
    let scale = (svd.singular_values[0] * sm[(0, 0)] + svd.singular_values[1] * sm[(1, 1)] + svd.singular_values[2] * sm[(2, 2)]) / var;
    (scale, r, md - r * ms * scale)
}
