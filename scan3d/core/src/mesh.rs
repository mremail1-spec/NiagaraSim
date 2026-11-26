//! Depth-map fusion into a truncated signed distance volume, surface
//! extraction with naive Surface Nets, cleanup and per-vertex colouring.

use crate::geometry::V3;
use crate::image::sample_rgb;
use crate::mvs::{Cloud, DepthMap};
use crate::sfm::Reconstruction;

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
            if let Some((_, _, d)) = dm.at(u, v) {
                if (d as f64 - zc).abs() > tol.max(0.02 * zc) {
                    continue;
                }
            } else {
                continue;
            }
            let view = (c - p).normalize();
            let cosang = nrm[vi].dot(&view);
            if cosang <= 0.1 {
                continue;
            }
            let w = cosang * cosang / (zc * zc);
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
