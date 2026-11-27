//! Harder synthetic test scenes built from primitives: a glossy, nearly
//! texture-less cup on a table in a room, and a street lined with buildings.
//! Rendering adds sensor noise and a moving specular highlight, the things
//! that make real photos harder than the clean tabletop scene.

use crate::geometry::{Intrinsics, Pose, V3};
use crate::synth::look_at;

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

#[derive(Clone, Copy)]
pub enum Tex {
    /// base colour modulated by fbm noise (freq, amplitude 0..1)
    Noise { freq: f64, amp: f64 },
    /// facade: wall with a grid of windows (spacing, window size), on the x=const or z=const face
    Windows { spacing: f64 },
    /// printed label band with stripes and blocky "text"
    Label,
}

#[derive(Clone, Copy)]
pub struct Mat {
    pub base: [f64; 3],
    pub tex: Tex,
    pub spec: f64,
    pub seed: u32,
}

#[derive(Clone, Copy)]
pub enum Shape {
    /// horizontal plane at height y, bounded in x and z
    Ground { y: f64, x0: f64, x1: f64, z0: f64, z1: f64 },
    Boxx { lo: [f64; 3], hi: [f64; 3] },
    /// vertical cylinder with caps
    Cyl { cx: f64, cz: f64, r: f64, y0: f64, y1: f64 },
    /// inward-facing room walls (an axis-aligned box seen from inside)
    Room { lo: [f64; 3], hi: [f64; 3] },
}

pub struct Prim {
    pub shape: Shape,
    pub mat: Mat,
}

pub struct Scene {
    pub prims: Vec<Prim>,
    pub light: V3,
    pub sky: [f64; 3],
    /// sensor noise standard deviation in 0..255 units
    pub noise: f64,
}

struct Hit {
    t: f64,
    n: V3,
    m: usize,
}

fn box_hit(o: &V3, d: &V3, lo: &[f64; 3], hi: &[f64; 3], inside: bool) -> Option<(f64, V3)> {
    let (mut tmin, mut tmax) = (f64::MIN, f64::MAX);
    let (mut an, mut sn, mut af, mut sf) = (0, 1.0, 0, 1.0);
    for a in 0..3 {
        if d[a].abs() < 1e-12 {
            if o[a] < lo[a] || o[a] > hi[a] {
                return None;
            }
            continue;
        }
        let t1 = (lo[a] - o[a]) / d[a];
        let t2 = (hi[a] - o[a]) / d[a];
        let (tn, tf, s_near) = if t1 < t2 { (t1, t2, -1.0) } else { (t2, t1, 1.0) };
        if tn > tmin {
            tmin = tn;
            an = a;
            sn = s_near;
        }
        if tf < tmax {
            tmax = tf;
            af = a;
            sf = -s_near;
        }
    }
    if tmin > tmax {
        return None;
    }
    let mut n = V3::zeros();
    if inside {
        if tmax <= 0.0 {
            return None;
        }
        n[af] = -sf;
        Some((tmax, n))
    } else {
        if tmin <= 1e-9 {
            return None;
        }
        n[an] = sn;
        Some((tmin, n))
    }
}

impl Scene {
    fn intersect(&self, o: &V3, d: &V3) -> Option<Hit> {
        let mut best: Option<Hit> = None;
        for (m, p) in self.prims.iter().enumerate() {
            let hit: Option<(f64, V3)> = match p.shape {
                Shape::Ground { y, x0, x1, z0, z1 } => {
                    if d.y.abs() < 1e-12 { None } else {
                        let t = (y - o.y) / d.y;
                        let q = o + d * t;
                        (t > 1e-9 && q.x >= x0 && q.x <= x1 && q.z >= z0 && q.z <= z1).then(|| (t, V3::y()))
                    }
                }
                Shape::Boxx { lo, hi } => box_hit(o, d, &lo, &hi, false),
                Shape::Room { lo, hi } => box_hit(o, d, &lo, &hi, true),
                Shape::Cyl { cx, cz, r, y0, y1 } => {
                    let mut h: Option<(f64, V3)> = None;
                    let (ox, oz) = (o.x - cx, o.z - cz);
                    let a = d.x * d.x + d.z * d.z;
                    if a > 1e-12 {
                        let b = ox * d.x + oz * d.z;
                        let c = ox * ox + oz * oz - r * r;
                        let disc = b * b - a * c;
                        if disc > 0.0 {
                            let t = (-b - disc.sqrt()) / a;
                            let y = o.y + d.y * t;
                            if t > 1e-9 && y >= y0 && y <= y1 {
                                h = Some((t, V3::new(ox + d.x * t, 0.0, oz + d.z * t).normalize()));
                            }
                        }
                    }
                    for (yc, ny) in [(y1, 1.0), (y0, -1.0)] {
                        if d.y.abs() > 1e-12 {
                            let t = (yc - o.y) / d.y;
                            let q = o + d * t;
                            if t > 1e-9 && (q.x - cx).powi(2) + (q.z - cz).powi(2) <= r * r && h.map_or(true, |hh| t < hh.0) {
                                h = Some((t, V3::new(0.0, ny, 0.0)));
                            }
                        }
                    }
                    h
                }
            };
            if let Some((t, n)) = hit {
                if best.as_ref().map_or(true, |b| t < b.t) {
                    best = Some(Hit { t, n, m });
                }
            }
        }
        best
    }

    pub fn debug_hit(&self, o: &V3, d: &V3) -> Option<f64> { self.intersect(o, d).map(|h| h.t) }

    fn albedo(&self, m: &Mat, p: &V3, n: &V3) -> [f64; 3] {
        match m.tex {
            Tex::Noise { freq, amp } => {
                let k = 1.0 - amp + 2.0 * amp * fbm(p * freq, m.seed);
                m.base.map(|c| c * k)
            }
            Tex::Windows { spacing } => {
                // facade coordinates: horizontal along the wall, vertical = y
                let u = if n.x.abs() > 0.5 { p.z } else { p.x };
                let fu = (u / spacing).rem_euclid(1.0);
                let fv = (p.y / (spacing * 1.1)).rem_euclid(1.0);
                let wall = 0.75 + 0.5 * fbm(p * 3.0, m.seed);
                let floor_band = p.y > 0.8;
                if floor_band && fu > 0.25 && fu < 0.75 && fv > 0.3 && fv < 0.8 {
                    let refl = 0.35 + 0.3 * fbm(V3::new(u * 0.7, p.y * 0.7, 0.0), m.seed + 9);
                    [60.0 * refl + 20.0, 75.0 * refl + 25.0, 95.0 * refl + 35.0]
                } else {
                    m.base.map(|c| c * wall)
                }
            }
            Tex::Label => {
                let ang = p.z.atan2(p.x);
                if ang > 0.3 && ang < 1.9 && p.y > 0.12 && p.y < 0.32 {
                    let cell = ((ang * 30.0) as i32, (p.y * 60.0) as i32);
                    let ink = hash(cell.0, cell.1, 0, 77) > 0.55 && p.y > 0.17 && p.y < 0.27;
                    if ink { [30.0, 30.0, 40.0] } else if p.y < 0.15 || p.y > 0.29 { [200.0, 40.0, 40.0] } else { [235.0, 230.0, 220.0] }
                } else {
                    let k = 0.98 + 0.04 * fbm(p * 20.0, m.seed);
                    m.base.map(|c| c * k)
                }
            }
        }
    }

    fn shade(&self, o: &V3, d: &V3) -> [f64; 3] {
        let Some(h) = self.intersect(o, d) else { return self.sky };
        let p = o + d * h.t;
        let pr = &self.prims[h.m];
        let a = self.albedo(&pr.mat, &p, &h.n);
        let l = self.light;
        let lam = 0.45 + 0.55 * h.n.dot(&l).max(0.0);
        let half = (l - d).normalize();
        let spec = pr.mat.spec * h.n.dot(&half).max(0.0).powi(60) * 255.0;
        a.map(|c| (c * lam + spec).min(255.0))
    }

    pub fn render(&self, pose: &Pose, k: &Intrinsics, w: usize, h: usize, seed: u32) -> Vec<u8> {
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
                    let s = self.shade(&c, &dir);
                    for a in 0..3 {
                        acc[a] += s[a] * 0.25;
                    }
                }
                for a in 0..3 {
                    let n = if self.noise > 0.0 {
                        let u1 = hash(x as i32, y as i32, a as i32, seed).max(1e-9);
                        let u2 = hash(x as i32, y as i32, a as i32 + 7, seed ^ 0x9e37);
                        self.noise * (-2.0 * u1.ln()).sqrt() * (std::f64::consts::TAU * u2).cos()
                    } else { 0.0 };
                    img[4 * (y * w + x) + a] = (acc[a] + n).round().clamp(0.0, 255.0) as u8;
                }
            }
        }
        img
    }

    pub fn surface_distance(&self, p: &V3) -> f64 {
        let mut d = f64::MAX;
        for pr in &self.prims {
            let dd = match pr.shape {
                Shape::Ground { y, x0, x1, z0, z1 } => {
                    let ox = (x0 - p.x).max(p.x - x1).max(0.0);
                    let oz = (z0 - p.z).max(p.z - z1).max(0.0);
                    ((p.y - y).powi(2) + ox * ox + oz * oz).sqrt()
                }
                Shape::Boxx { lo, hi } | Shape::Room { lo, hi } => {
                    let c = V3::new(lo[0] + hi[0], lo[1] + hi[1], lo[2] + hi[2]) * 0.5;
                    let e = V3::new(hi[0] - lo[0], hi[1] - lo[1], hi[2] - lo[2]) * 0.5;
                    let q = (p - c).abs() - e;
                    (q.map(|v| v.max(0.0)).norm() + q.x.max(q.y).max(q.z).min(0.0)).abs()
                }
                Shape::Cyl { cx, cz, r, y0, y1 } => {
                    let dr = ((p.x - cx).powi(2) + (p.z - cz).powi(2)).sqrt() - r;
                    let dy = (y0 - p.y).max(p.y - y1);
                    (V3::new(dr.max(0.0), dy.max(0.0), 0.0).norm() + dr.max(dy).min(0.0)).abs()
                }
            };
            d = d.min(dd);
        }
        d
    }

    /// A glossy, nearly uniform cup with a printed label, on a wooden table in a room.
    pub fn cup() -> Scene {
        let m = |base: [f64; 3], tex: Tex, spec: f64, seed: u32| Mat { base, tex, spec, seed };
        Scene {
            prims: vec![
                Prim { shape: Shape::Cyl { cx: 0.0, cz: 0.0, r: 0.25, y0: 0.0, y1: 0.42 }, mat: m([225.0, 120.0, 45.0], Tex::Label, 0.6, 3) },
                Prim { shape: Shape::Ground { y: 0.0, x0: -1.2, x1: 1.2, z0: -0.9, z1: 0.9 }, mat: m([150.0, 105.0, 70.0], Tex::Noise { freq: 6.0, amp: 0.35 }, 0.05, 11) },
                Prim { shape: Shape::Boxx { lo: [-1.2, -0.8, -0.9], hi: [1.2, -0.001, 0.9] }, mat: m([110.0, 80.0, 55.0], Tex::Noise { freq: 4.0, amp: 0.3 }, 0.0, 12) },
                Prim { shape: Shape::Room { lo: [-3.0, -0.8, -3.0], hi: [3.0, 2.2, 3.0] }, mat: m([190.0, 185.0, 175.0], Tex::Noise { freq: 1.5, amp: 0.25 }, 0.0, 13) },
            ],
            light: V3::new(0.3, 1.0, 0.4).normalize(),
            sky: [200.0, 200.0, 200.0],
            noise: 2.5,
        }
    }

    /// A street between two rows of buildings with windowed facades.
    pub fn street() -> Scene {
        let m = |base: [f64; 3], tex: Tex, seed: u32| Mat { base, tex, spec: 0.0, seed };
        let mut prims = vec![Prim {
            shape: Shape::Ground { y: 0.0, x0: -8.0, x1: 8.0, z0: -10.0, z1: 60.0 },
            mat: m([95.0, 95.0, 100.0], Tex::Noise { freq: 2.0, amp: 0.4 }, 21),
        }];
        let colors = [[200.0, 170.0, 140.0], [170.0, 120.0, 100.0], [215.0, 210.0, 190.0], [150.0, 160.0, 175.0]];
        for i in 0..10 {
            let z0 = -10.0 + i as f64 * 7.0;
            let hl = 7.0 + 4.0 * hash(i, 1, 0, 5);
            let hr = 7.0 + 4.0 * hash(i, 2, 0, 5);
            prims.push(Prim { shape: Shape::Boxx { lo: [-12.0, 0.0, z0], hi: [-5.0, hl, z0 + 6.8] }, mat: m(colors[i as usize % 4], Tex::Windows { spacing: 1.6 }, 30 + i as u32) });
            prims.push(Prim { shape: Shape::Boxx { lo: [5.0, 0.0, z0], hi: [12.0, hr, z0 + 6.8] }, mat: m(colors[(i as usize + 2) % 4], Tex::Windows { spacing: 1.6 }, 50 + i as u32) });
        }
        Scene { prims, light: V3::new(0.5, 1.0, -0.3).normalize(), sky: [175.0, 205.0, 235.0], noise: 2.0 }
    }
}

/// Handheld orbit around a small object, with height changes.
pub fn cup_orbit(n: usize) -> Vec<Pose> {
    (0..n)
        .map(|i| {
            let a = i as f64 / n as f64 * std::f64::consts::TAU;
            let h = 0.45 + 0.25 * (2.0 * a).sin();
            look_at(V3::new(0.85 * a.cos(), h, 0.85 * a.sin()), V3::new(0.0, 0.18, 0.0))
        })
        .collect()
}

/// Walking down the middle of the street, looking ahead and swaying left/right.
pub fn street_walk(n: usize) -> Vec<Pose> {
    (0..n)
        .map(|i| {
            let z = i as f64 * 0.5;
            let yaw = 0.5 * (i as f64 * 0.35).sin();
            let eye = V3::new(0.6 * (i as f64 * 0.2).sin(), 1.6, z);
            look_at(eye, eye + V3::new(yaw.sin(), 0.05, yaw.cos()))
        })
        .collect()
}

/// Facade capture: walk down the street with the camera turned towards the
/// left-hand buildings, then come back filming the right-hand side.
pub fn street_sideways(n: usize) -> Vec<Pose> {
    (0..n)
        .map(|i| {
            let half = n / 2;
            let (z, side) = if i < half { (i as f64 * 0.6, -1.0) } else { ((n - 1 - i) as f64 * 0.6, 1.0) };
            let eye = V3::new(-0.5 * side, 1.6, z);
            // mostly sideways, slightly forward so neighbouring shots overlap well
            let dir = V3::new(side, 0.1, 0.35).normalize();
            look_at(eye, eye + dir)
        })
        .collect()
}
