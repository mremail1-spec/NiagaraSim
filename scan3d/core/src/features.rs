//! ORB-style features: multi-scale FAST-9 corners ranked by Harris response,
//! intensity-centroid orientation and rotated BRIEF 256-bit descriptors.

use crate::image::Gray;
use std::sync::OnceLock;

#[derive(Clone, Copy, Debug)]
pub struct Keypoint {
    pub x: f32,
    pub y: f32,
    pub angle: f32,
    pub score: f32,
    pub level: u8,
}

pub type Desc = [u64; 4];

#[derive(Clone, Default)]
pub struct Features {
    pub kps: Vec<Keypoint>,
    pub desc: Vec<Desc>,
}

const CIRCLE: [(i32, i32); 16] = [
    (0, -3), (1, -3), (2, -2), (3, -1), (3, 0), (3, 1), (2, 2), (1, 3),
    (0, 3), (-1, 3), (-2, 2), (-3, 1), (-3, 0), (-3, -1), (-2, -2), (-1, -3),
];
const MARGIN: usize = 20;
const LEVEL_SCALE: f32 = 1.5;
const LEVELS: usize = 4;

fn pattern() -> &'static Vec<[i8; 4]> {
    static P: OnceLock<Vec<[i8; 4]>> = OnceLock::new();
    P.get_or_init(|| {
        let mut s: u64 = 0x9E3779B97F4A7C15;
        let mut next = || {
            s ^= s << 13;
            s ^= s >> 7;
            s ^= s << 17;
            (s >> 11) as f64 / (1u64 << 53) as f64
        };
        let mut gauss = || {
            let u1 = next().max(1e-12);
            let u2 = next();
            (-2.0 * u1.ln()).sqrt() * (2.0 * std::f64::consts::PI * u2).cos()
        };
        (0..256)
            .map(|_| {
                let mut v = [0i8; 4];
                for c in v.iter_mut() {
                    *c = (gauss() * 31.0 / 5.0).round().clamp(-13.0, 13.0) as i8;
                }
                v
            })
            .collect()
    })
}

fn fast_corners(img: &Gray, thr: f32) -> Vec<(usize, usize)> {
    let w = img.w;
    let offs: Vec<isize> = CIRCLE.iter().map(|&(dx, dy)| dy as isize * w as isize + dx as isize).collect();
    let mut out = Vec::new();
    if img.w <= 2 * MARGIN || img.h <= 2 * MARGIN {
        return out;
    }
    let d = &img.data;
    for y in MARGIN..img.h - MARGIN {
        for x in MARGIN..img.w - MARGIN {
            let i = (y * w + x) as isize;
            let p = d[i as usize];
            let hi = p + thr;
            let lo = p - thr;
            let mut nb = 0;
            let mut nd = 0;
            for k in [0usize, 4, 8, 12] {
                let v = d[(i + offs[k]) as usize];
                if v > hi { nb += 1 } else if v < lo { nd += 1 }
            }
            if nb < 2 && nd < 2 {
                continue;
            }
            let mut state = [0i8; 16];
            for k in 0..16 {
                let v = d[(i + offs[k]) as usize];
                state[k] = if v > hi { 1 } else if v < lo { -1 } else { 0 };
            }
            let mut found = false;
            for s in [1i8, -1] {
                let mut run = 0;
                for k in 0..25 {
                    if state[k % 16] == s {
                        run += 1;
                        if run >= 9 {
                            found = true;
                            break;
                        }
                    } else {
                        run = 0;
                    }
                }
                if found {
                    break;
                }
            }
            if found {
                out.push((x, y));
            }
        }
    }
    out
}

fn harris(img: &Gray, x: usize, y: usize) -> f32 {
    let (mut a, mut b, mut c) = (0.0f32, 0.0f32, 0.0f32);
    for yy in y - 3..=y + 3 {
        for xx in x - 3..=x + 3 {
            let gx = img.at(xx + 1, yy) - img.at(xx - 1, yy);
            let gy = img.at(xx, yy + 1) - img.at(xx, yy - 1);
            a += gx * gx;
            b += gy * gy;
            c += gx * gy;
        }
    }
    a * b - c * c - 0.04 * (a + b) * (a + b)
}

fn orientation(img: &Gray, x: usize, y: usize) -> f32 {
    let (mut m01, mut m10) = (0.0f32, 0.0f32);
    for dy in -15i32..=15 {
        let span = ((225 - dy * dy) as f32).sqrt() as i32;
        for dx in -span..=span {
            let v = img.at((x as i32 + dx) as usize, (y as i32 + dy) as usize);
            m10 += dx as f32 * v;
            m01 += dy as f32 * v;
        }
    }
    m01.atan2(m10)
}

fn describe(smooth: &Gray, x: usize, y: usize, angle: f32) -> Desc {
    let (s, c) = angle.sin_cos();
    let mut d = [0u64; 4];
    for (k, p) in pattern().iter().enumerate() {
        let rot = |px: i8, py: i8| -> f32 {
            let rx = (c * px as f32 - s * py as f32).round() as isize;
            let ry = (s * px as f32 + c * py as f32).round() as isize;
            smooth.at((x as isize + rx) as usize, (y as isize + ry) as usize)
        };
        if rot(p[0], p[1]) < rot(p[2], p[3]) {
            d[k >> 6] |= 1u64 << (k & 63);
        }
    }
    d
}

pub fn detect_and_describe(img: &Gray, max_features: usize) -> Features {
    let mut feats = Features::default();
    // Feature budget per level proportional to area.
    let weights: Vec<f32> = (0..LEVELS).map(|l| LEVEL_SCALE.powi(-2 * l as i32)).collect();
    let wsum: f32 = weights.iter().sum();
    let mut level_img = img.clone();
    for l in 0..LEVELS {
        if l > 0 {
            level_img = img.downscale(LEVEL_SCALE.powi(l as i32));
        }
        if level_img.w <= 2 * MARGIN + 8 || level_img.h <= 2 * MARGIN + 8 {
            break;
        }
        let scale = img.w as f32 / level_img.w as f32;
        let budget = ((max_features as f32) * weights[l] / wsum).ceil() as usize;
        let mut corners = fast_corners(&level_img, 0.035);
        if corners.len() < budget * 2 {
            corners = fast_corners(&level_img, 0.015);
        }
        // Harris score + 3x3 non-maximum suppression.
        let mut score_map = vec![0.0f32; level_img.w * level_img.h];
        let scored: Vec<(usize, usize, f32)> = corners
            .iter()
            .map(|&(x, y)| {
                let s = harris(&level_img, x, y);
                score_map[y * level_img.w + x] = s;
                (x, y, s)
            })
            .filter(|t| t.2 > 0.0)
            .collect();
        let mut kept: Vec<(usize, usize, f32)> = scored
            .into_iter()
            .filter(|&(x, y, s)| {
                for dy in -1i32..=1 {
                    for dx in -1i32..=1 {
                        if dx == 0 && dy == 0 {
                            continue;
                        }
                        let n = score_map[(y as i32 + dy) as usize * level_img.w + (x as i32 + dx) as usize];
                        if n > s {
                            return false;
                        }
                    }
                }
                true
            })
            .collect();
        // Spatial bucketing so features cover the whole image.
        let cell = 32usize;
        let gw = level_img.w.div_ceil(cell);
        let gh = level_img.h.div_ceil(cell);
        let per_cell = ((budget as f32 / (gw * gh) as f32) * 2.0).ceil() as usize + 1;
        kept.sort_by(|a, b| b.2.partial_cmp(&a.2).unwrap());
        let mut counts = vec![0usize; gw * gh];
        let mut bucketed = Vec::new();
        for k in kept {
            let c = (k.1 / cell) * gw + k.0 / cell;
            if counts[c] < per_cell {
                counts[c] += 1;
                bucketed.push(k);
            }
        }
        bucketed.truncate(budget);
        let smooth = level_img.blur(2.0);
        for (x, y, s) in bucketed {
            let a = orientation(&level_img, x, y);
            feats.desc.push(describe(&smooth, x, y, a));
            feats.kps.push(Keypoint {
                x: (x as f32 + 0.5) * scale - 0.5,
                y: (y as f32 + 0.5) * scale - 0.5,
                angle: a,
                score: s,
                level: l as u8,
            });
        }
    }
    feats
}

#[inline]
pub fn hamming(a: &Desc, b: &Desc) -> u32 {
    (a[0] ^ b[0]).count_ones() + (a[1] ^ b[1]).count_ones() + (a[2] ^ b[2]).count_ones() + (a[3] ^ b[3]).count_ones()
}

/// Mutual nearest neighbours passing Lowe's ratio test.
pub fn match_descriptors(a: &[Desc], b: &[Desc], ratio: f32, max_dist: u32) -> Vec<(u32, u32)> {
    let mut col_best = vec![(u32::MAX, u32::MAX); b.len()];
    let mut row = Vec::with_capacity(a.len());
    for (i, da) in a.iter().enumerate() {
        let (mut b1, mut b2, mut j1) = (u32::MAX, u32::MAX, 0usize);
        for (j, db) in b.iter().enumerate() {
            let d = hamming(da, db);
            if d < b1 {
                b2 = b1;
                b1 = d;
                j1 = j;
            } else if d < b2 {
                b2 = d;
            }
            if d < col_best[j].0 {
                col_best[j] = (d, i as u32);
            }
        }
        row.push((b1, b2, j1));
    }
    let mut out = Vec::new();
    for (i, &(b1, b2, j1)) in row.iter().enumerate() {
        if b.is_empty() || b1 > max_dist {
            continue;
        }
        if (b1 as f32) >= ratio * b2 as f32 {
            continue;
        }
        if col_best[j1].1 == i as u32 {
            out.push((i as u32, j1 as u32));
        }
    }
    out
}
