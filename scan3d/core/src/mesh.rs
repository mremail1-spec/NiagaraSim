//! Depth-map fusion into a truncated signed distance volume, surface
//! extraction with naive Surface Nets, cleanup and per-vertex colouring.

use crate::geometry::V3;
use crate::image::sample_rgb;
use crate::mvs::{Cloud, DepthMap};
use crate::sfm::Reconstruction;
use crate::geometry::M3;
use crate::poisson;

#[derive(Default, Clone)]
pub struct Mesh {
    pub pos: Vec<[f32; 3]>,
    pub col: Vec<[u8; 3]>,
    pub nrm: Vec<[f32; 3]>,
    pub idx: Vec<u32>,
}

fn percentile(mut v: Vec<f32>, q: f32) -> f32 {
    if v.is_empty() {
        return 0.0;
    }
    v.sort_by(|a, b| a.partial_cmp(b).unwrap());
    v[((v.len() - 1) as f32 * q) as usize]
}

pub fn build_mesh(
    maps: &[Option<DepthMap>],
    rec: &Reconstruction,
    cloud: &Cloud,
    rgba: &[Vec<u8>],
    fw: usize,
    fh: usize,
    res: usize,
    progress: &mut dyn FnMut(f32),
) -> Mesh {
    if cloud.pos.len() < 100 {
        return Mesh::default();
    }
    let k = rec.intr;
    let mut lo = [0.0f64; 3];
    let mut hi = [0.0f64; 3];
    for a in 0..3 {
        let v: Vec<f32> = cloud.pos.iter().map(|p| p[a]).collect();
        let l = percentile(v.clone(), 0.005) as f64;
        let h = percentile(v, 0.995) as f64;
        let m = (h - l) * 0.05;
        lo[a] = l - m;
        hi[a] = h + m;
    }
    let ext = (0..3).map(|a| hi[a] - lo[a]).fold(0.0, f64::max);
    let vs = ext / res as f64;
    let dims: Vec<usize> = (0..3).map(|a| (((hi[a] - lo[a]) / vs).ceil() as usize + 1).max(2)).collect();
    let (nx, ny, nz) = (dims[0], dims[1], dims[2]);
    let nvox = nx * ny * nz;
    let trunc = 3.0 * vs;
    let mut tsdf = vec![1.0f32; nvox];
    let mut wgt = vec![0.0f32; nvox];
    let id = |x: usize, y: usize, z: usize| (z * ny + y) * nx + x;
    let views: Vec<usize> = (0..maps.len()).filter(|&i| maps[i].is_some()).collect();
    for (vi, &i) in views.iter().enumerate() {
        progress(0.8 * vi as f32 / views.len() as f32);
        let dm = maps[i].as_ref().unwrap();
        let pose = rec.poses[i].unwrap();
        for z in 0..nz {
            for y in 0..ny {
                for x in 0..nx {
                    let p = V3::new(lo[0] + x as f64 * vs, lo[1] + y as f64 * vs, lo[2] + z as f64 * vs);
                    let Some((u, v, zc)) = dm.project(&k, &pose, &p) else { continue };
                    let Some((_, _, d)) = dm.at(u, v) else { continue };
                    let sdf = d as f64 - zc;
                    if sdf < -trunc {
                        continue;
                    }
                    let val = (sdf / trunc).min(1.0) as f32;
                    let j = id(x, y, z);
                    tsdf[j] = (tsdf[j] * wgt[j] + val) / (wgt[j] + 1.0);
                    wgt[j] += 1.0;
                }
            }
        }
    }
    // --- Surface Nets ---
    let min_w = if views.len() >= 6 { 2.0 } else { 1.0 };
    let ok = |j: usize| wgt[j] >= min_w;
    let (cx, cy, cz) = (nx - 1, ny - 1, nz - 1);
    let cid = |x: usize, y: usize, z: usize| (z * cy + y) * cx + x;
    let mut cell_vert = vec![-1i32; cx * cy * cz];
    let mut pos: Vec<V3> = Vec::new();
    const CORNERS: [(usize, usize, usize); 8] =
        [(0, 0, 0), (1, 0, 0), (0, 1, 0), (1, 1, 0), (0, 0, 1), (1, 0, 1), (0, 1, 1), (1, 1, 1)];
    const EDGES: [(usize, usize); 12] =
        [(0, 1), (2, 3), (4, 5), (6, 7), (0, 2), (1, 3), (4, 6), (5, 7), (0, 4), (1, 5), (2, 6), (3, 7)];
    for z in 0..cz {
        for y in 0..cy {
            for x in 0..cx {
                let mut vals = [0.0f32; 8];
                let mut valid = true;
                let (mut neg, mut posn) = (false, false);
                for (c, &(dx, dy, dz)) in CORNERS.iter().enumerate() {
                    let j = id(x + dx, y + dy, z + dz);
                    if !ok(j) {
                        valid = false;
                        break;
                    }
                    vals[c] = tsdf[j];
                    if tsdf[j] < 0.0 { neg = true } else { posn = true }
                }
                if !valid || !neg || !posn {
                    continue;
                }
                let mut acc = V3::zeros();
                let mut n = 0.0;
                for &(a, b) in &EDGES {
                    let (va, vb) = (vals[a], vals[b]);
                    if (va < 0.0) != (vb < 0.0) {
                        let t = (va / (va - vb)) as f64;
                        let pa = CORNERS[a];
                        let pb = CORNERS[b];
                        acc += V3::new(
                            pa.0 as f64 + t * (pb.0 as f64 - pa.0 as f64),
                            pa.1 as f64 + t * (pb.1 as f64 - pa.1 as f64),
                            pa.2 as f64 + t * (pb.2 as f64 - pa.2 as f64),
                        );
                        n += 1.0;
                    }
                }
                let local = acc / n;
                cell_vert[cid(x, y, z)] = pos.len() as i32;
                pos.push(V3::new(
                    lo[0] + (x as f64 + local.x) * vs,
                    lo[1] + (y as f64 + local.y) * vs,
                    lo[2] + (z as f64 + local.z) * vs,
                ));
            }
        }
    }
    let mut idx: Vec<u32> = Vec::new();
    let quad = |c: [Option<(usize, usize, usize)>; 4], flip: bool, idx: &mut Vec<u32>| {
        let mut v = [0u32; 4];
        for (k, cc) in c.iter().enumerate() {
            let Some((x, y, z)) = cc else { return };
            let vi = cell_vert[cid(*x, *y, *z)];
            if vi < 0 {
                return;
            }
            v[k] = vi as u32;
        }
        if flip {
            v.reverse();
        }
        idx.extend_from_slice(&[v[0], v[1], v[2], v[0], v[2], v[3]]);
    };
    let cell = |x: isize, y: isize, z: isize| -> Option<(usize, usize, usize)> {
        (x >= 0 && y >= 0 && z >= 0 && (x as usize) < cx && (y as usize) < cy && (z as usize) < cz)
            .then_some((x as usize, y as usize, z as usize))
    };
    for z in 0..nz {
        for y in 0..ny {
            for x in 0..nx {
                let j = id(x, y, z);
                if !ok(j) {
                    continue;
                }
                let inside = tsdf[j] < 0.0;
                let (xi, yi, zi) = (x as isize, y as isize, z as isize);
                // edge along +x: cells around it in the (y, z) plane
                if x + 1 < nx && ok(id(x + 1, y, z)) && inside != (tsdf[id(x + 1, y, z)] < 0.0) {
                    quad([cell(xi, yi - 1, zi - 1), cell(xi, yi, zi - 1), cell(xi, yi, zi), cell(xi, yi - 1, zi)], !inside, &mut idx);
                }
                if y + 1 < ny && ok(id(x, y + 1, z)) && inside != (tsdf[id(x, y + 1, z)] < 0.0) {
                    quad([cell(xi - 1, yi, zi - 1), cell(xi - 1, yi, zi), cell(xi, yi, zi), cell(xi, yi, zi - 1)], !inside, &mut idx);
                }
                if z + 1 < nz && ok(id(x, y, z + 1)) && inside != (tsdf[id(x, y, z + 1)] < 0.0) {
                    quad([cell(xi - 1, yi - 1, zi), cell(xi, yi - 1, zi), cell(xi, yi, zi), cell(xi - 1, yi, zi)], !inside, &mut idx);
                }
            }
        }
    }
    drop(cell_vert);
    progress(0.85);
    let (pos, idx) = keep_large_components(pos, idx, 0.02);
    let pos = smooth(pos, &idx, 2);
    let nrm = vertex_normals(&pos, &idx);
    progress(0.9);
    let col = colorize(&pos, &nrm, maps, rec, rgba, fw, fh, trunc);
    progress(1.0);
    Mesh {
        pos: pos.iter().map(|p| [p.x as f32, p.y as f32, p.z as f32]).collect(),
        col,
        nrm: nrm.iter().map(|n| [n.x as f32, n.y as f32, n.z as f32]).collect(),
        idx,
    }
}

fn keep_large_components(pos: Vec<V3>, idx: Vec<u32>, min_frac: f64) -> (Vec<V3>, Vec<u32>) {
    let n = pos.len();
    let mut parent: Vec<usize> = (0..n).collect();
    fn find(p: &mut [usize], mut x: usize) -> usize {
        while p[x] != x {
            p[x] = p[p[x]];
            x = p[x];
        }
        x
    }
    for t in idx.chunks(3) {
        for e in [(t[0], t[1]), (t[1], t[2])] {
            let a = find(&mut parent, e.0 as usize);
            let b = find(&mut parent, e.1 as usize);
            if a != b {
                parent[a] = b;
            }
        }
    }
    let mut size = vec![0usize; n];
    for t in idx.chunks(3) {
        let r = find(&mut parent, t[0] as usize);
        size[r] += 1;
    }
    let total = idx.len() / 3;
    let mut remap = vec![-1i64; n];
    let mut npos = Vec::new();
    let mut nidx = Vec::new();
    for t in idx.chunks(3) {
        let r = find(&mut parent, t[0] as usize);
        if (size[r] as f64) < min_frac * total as f64 {
            continue;
        }
        for &v in t {
            if remap[v as usize] < 0 {
                remap[v as usize] = npos.len() as i64;
                npos.push(pos[v as usize]);
            }
            nidx.push(remap[v as usize] as u32);
        }
    }
    (npos, nidx)
}

fn smooth(mut pos: Vec<V3>, idx: &[u32], iters: usize) -> Vec<V3> {
    let n = pos.len();
    for _ in 0..iters {
        let mut acc = vec![V3::zeros(); n];
        let mut cnt = vec![0.0f64; n];
        for t in idx.chunks(3) {
            for (a, b) in [(t[0], t[1]), (t[1], t[2]), (t[2], t[0])] {
                acc[a as usize] += pos[b as usize];
                acc[b as usize] += pos[a as usize];
                cnt[a as usize] += 1.0;
                cnt[b as usize] += 1.0;
            }
        }
        for i in 0..n {
            if cnt[i] > 0.0 {
                pos[i] = pos[i] * 0.5 + acc[i] / cnt[i] * 0.5;
            }
        }
    }
    pos
}

fn vertex_normals(pos: &[V3], idx: &[u32]) -> Vec<V3> {
    let mut n = vec![V3::zeros(); pos.len()];
    for t in idx.chunks(3) {
        let (a, b, c) = (pos[t[0] as usize], pos[t[1] as usize], pos[t[2] as usize]);
        let fnrm = (b - a).cross(&(c - a));
        for &v in t {
            n[v as usize] += fnrm;
        }
    }
    n.into_iter().map(|v| if v.norm() > 1e-15 { v.normalize() } else { V3::y() }).collect()
}

#[allow(clippy::too_many_arguments)]
fn colorize(
    pos: &[V3],
    nrm: &[V3],
    maps: &[Option<DepthMap>],
    rec: &Reconstruction,
    rgba: &[Vec<u8>],
    fw: usize,
    fh: usize,
    tol: f64,
) -> Vec<[u8; 3]> {
    let k = rec.intr;
    let mut acc = vec![([0.0f64; 3], 0.0f64); pos.len()];
    for (i, dm) in maps.iter().enumerate() {
        let Some(dm) = dm else { continue };
        let pose = rec.poses[i].unwrap();
        let c = pose.center();
        for (vi, p) in pos.iter().enumerate() {
            let Some((u, v, zc)) = dm.project(&k, &pose, p) else { continue };
            if u < 0.0 || v < 0.0 || u > (dm.w - 1) as f64 || v > (dm.h - 1) as f64 {
                continue;
            }
            // visibility: must agree with this view's depth map when it has a value
            let mut wv = 1.0;
            if let Some((_, _, d)) = dm.at(u, v) {
                if (d as f64 - zc).abs() > tol.max(0.02 * zc) {
                    continue;
                }
            } else {
                wv = 0.25; // no depth measured there (plain surface): trust less
            }
            let view = (c - p).normalize();
            let cosang = nrm[vi].dot(&view);
            if cosang <= 0.1 {
                continue;
            }
            let w = wv * cosang * cosang / (zc * zc);
            let fu = (u + 0.5) / dm.s - 0.5;
            let fv = (v + 0.5) / dm.s - 0.5;
            let col = sample_rgb(&rgba[i], fw, fh, fu as f32, fv as f32);
            for a in 0..3 {
                acc[vi].0[a] += w * col[a] as f64;
            }
            acc[vi].1 += w;
        }
    }
    acc.iter()
        .map(|(c, w)| {
            if *w > 0.0 {
                [(c[0] / w) as u8, (c[1] / w) as u8, (c[2] / w) as u8]
            } else {
                [160, 160, 160]
            }
        })
        .collect()
}

/// Where the cameras look (least-squares meeting point of their optical axes)
/// and how far around it they go (degrees of azimuth, Y is up).
pub fn camera_focus(rec: &Reconstruction) -> Option<(V3, f64, f64)> {
    let cams: Vec<(V3, V3)> = rec.poses.iter().flatten().map(|p| (p.center(), p.r.transpose() * V3::z())).collect();
    if cams.len() < 3 {
        return None;
    }
    let mut a = M3::zeros();
    let mut b = V3::zeros();
    for (c, d) in &cams {
        let m = M3::identity() - d * d.transpose();
        a += m;
        b += m * c;
    }
    let focus = a.try_inverse()? * b;
    // An object scan keeps the focus near the middle of nearly every photo.
    let centred = cams
        .iter()
        .filter(|(c, d)| {
            let v = focus - c;
            v.norm() > 1e-9 && d.dot(&v.normalize()) > 25f64.to_radians().cos()
        })
        .count();
    if centred * 10 < cams.len() * 8 {
        return None;
    }
    let mut az: Vec<f64> = cams.iter().map(|(c, _)| (c.z - focus.z).atan2(c.x - focus.x).to_degrees()).collect();
    az.sort_by(|x, y| x.partial_cmp(y).unwrap());
    let mut gap = 360.0 - (az[az.len() - 1] - az[0]);
    for w in az.windows(2) {
        gap = f64::max(gap, w[1] - w[0]);
    }
    let mut dist: Vec<f64> = cams.iter().map(|(c, _)| (c - focus).norm()).collect();
    dist.sort_by(|x, y| x.partial_cmp(y).unwrap());
    Some((focus, 360.0 - gap, dist[dist.len() / 2]))
}

/// Mesh by Poisson reconstruction of the dense oriented points.
/// Object scans (cameras around a point) give a closed surface around the
/// object; scenes keep only surface near measured points.
#[allow(clippy::too_many_arguments)]
pub fn build_mesh_poisson(
    maps: &[Option<DepthMap>],
    rec: &Reconstruction,
    cloud: &Cloud,
    rgba: &[Vec<u8>],
    fw: usize,
    fh: usize,
    res: usize,
    progress: &mut dyn FnMut(f32),
) -> (Mesh, String) {
    if cloud.pos.len() < 100 {
        return (Mesh::default(), "too few points".into());
    }
    let all: Vec<(V3, V3)> = cloud.pos.iter().zip(&cloud.nrm)
        .map(|(p, n)| (V3::new(p[0] as f64, p[1] as f64, p[2] as f64), V3::new(n[0] as f64, n[1] as f64, n[2] as f64)))
        .collect();
    let focus = camera_focus(rec);
    let object = matches!(focus, Some((_, cov, _)) if cov >= 150.0);
    let sel: Vec<(V3, V3)> = match focus {
        Some((f, _, d)) if object => all.iter().copied().filter(|(p, _)| (p - f).norm() < 0.7 * d).collect(),
        _ => all.clone(),
    };
    let sel = if sel.len() >= 100 { sel } else { all };
    let mut lo = V3::zeros();
    let mut hi = V3::zeros();
    for a in 0..3 {
        let v: Vec<f32> = sel.iter().map(|(p, _)| p[a] as f32).collect();
        let (l, h) = (percentile(v.clone(), 0.005) as f64, percentile(v, 0.995) as f64);
        let m = (h - l) * 0.08 + 1e-6;
        lo[a] = l - m;
        hi[a] = h + m;
    }
    let pts: Vec<V3> = sel.iter().map(|x| x.0).filter(|p| (0..3).all(|a| p[a] >= lo[a] && p[a] <= hi[a])).collect();
    let nrm: Vec<V3> = sel.iter().filter(|(p, _)| (0..3).all(|a| p[a] >= lo[a] && p[a] <= hi[a])).map(|x| x.1).collect();
    let mut prog = |p: f32| progress(0.7 * p);
    let pr = poisson::solve(&pts, &nrm, lo, hi - lo, res, &mut prog);
    // Keep surface within reach of the data: generous for objects (fills
    // texture-less patches), tight for scenes (no invented walls).
    let reach: u16 = if object { ((res as f64 * 0.05) as u16).max(4) } else { 4 };
    let ok = |j: usize| pr.dist[j] <= reach;
    let (pos, idx) = poisson::surface_nets(&pr.grid, &pr.field, &ok);
    // Trim invented surface: drop faces lying in space the cameras saw through
    // to a farther measured surface (e.g. a lid Poisson puts over an open scene).
    let near_data = |p: &V3| -> bool {
        let q = (p - pr.grid.lo) / pr.grid.h;
        let (x, y, z) = (q.x.round() as isize, q.y.round() as isize, q.z.round() as isize);
        if x < 0 || y < 0 || z < 0 || x as usize >= pr.grid.n[0] || y as usize >= pr.grid.n[1] || z as usize >= pr.grid.n[2] {
            return false;
        }
        pr.dist[pr.grid.id(x as usize, y as usize, z as usize)] <= 4
    };
    let tmp_n = vertex_normals(&pos, &idx);
    let tol = 4.0 * pr.grid.h;
    let keep_v: Vec<bool> = pos
        .iter()
        .zip(&tmp_n)
        .map(|(p, _)| {
            // In space cameras saw through (a surface was measured behind it): invented.
            near_data(p) || free_space_votes(p, maps, rec, tol) < 2
        })
        .collect();
    let idx: Vec<u32> = idx.chunks(3).filter(|t| t.iter().any(|&v| keep_v[v as usize])).flatten().copied().collect();
    progress(0.8);
    let (pos, idx) = keep_large_components(pos, idx, if object { 0.1 } else { 0.01 });
    let pos = smooth(pos, &idx, 2);
    let nrm = vertex_normals(&pos, &idx);
    let col = colorize(&pos, &nrm, maps, rec, rgba, fw, fh, 3.0 * pr.grid.h);
    progress(1.0);
    let mode = if object {
        let (f, cov, _) = focus.unwrap();
        format!("object mode (cameras cover {:.0}° around [{:.2}, {:.2}, {:.2}])", cov, f.x, f.y, f.z)
    } else {
        "scene mode".to_string()
    };
    (
        Mesh {
            pos: pos.iter().map(|p| [p.x as f32, p.y as f32, p.z as f32]).collect(),
            col,
            nrm: nrm.iter().map(|n| [n.x as f32, n.y as f32, n.z as f32]).collect(),
            idx,
        },
        mode,
    )
}

/// Number of views whose measured depth lies clearly behind this point, i.e.
/// the camera saw through the point to a farther surface.
fn free_space_votes(p: &V3, maps: &[Option<DepthMap>], rec: &Reconstruction, tol: f64) -> usize {
    let k = rec.intr;
    let mut votes = 0;
    for (i, dm) in maps.iter().enumerate() {
        let Some(dm) = dm else { continue };
        let pose = rec.poses[i].unwrap();
        let Some((u, v, zc)) = dm.project(&k, &pose, p) else { continue };
        if let Some((_, _, d)) = dm.at(u, v) {
            if d as f64 > zc + tol.max(0.03 * zc) {
                votes += 1;
            }
        }
    }
    votes
}
