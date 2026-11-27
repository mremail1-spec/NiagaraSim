//! Poisson surface reconstruction on a regular grid: oriented points ->
//! indicator function (solve ∇²χ = ∇·V by conjugate gradients, coarse to
//! fine) -> iso-surface (Surface Nets). Closes gaps between observed parts,
//! e.g. a plain cup body between its rim and base.

use crate::geometry::V3;

pub struct Grid {
    pub lo: V3,
    pub h: f64,
    pub n: [usize; 3],
}

impl Grid {
    #[inline]
    pub fn id(&self, x: usize, y: usize, z: usize) -> usize {
        (z * self.n[1] + y) * self.n[0] + x
    }
    pub fn len(&self) -> usize {
        self.n[0] * self.n[1] * self.n[2]
    }
    pub fn is_empty(&self) -> bool {
        self.len() == 0
    }
    fn pos(&self, x: f64, y: f64, z: f64) -> V3 {
        self.lo + V3::new(x, y, z) * self.h
    }
}

/// Trilinear splat of a value into a grid.
fn splat(g: &Grid, p: &V3, mut f: impl FnMut(usize, f64)) {
    let q = (p - g.lo) / g.h;
    let (x0, y0, z0) = (q.x.floor(), q.y.floor(), q.z.floor());
    if x0 < 0.0 || y0 < 0.0 || z0 < 0.0 {
        return;
    }
    let (xi, yi, zi) = (x0 as usize, y0 as usize, z0 as usize);
    if xi + 1 >= g.n[0] || yi + 1 >= g.n[1] || zi + 1 >= g.n[2] {
        return;
    }
    let (fx, fy, fz) = (q.x - x0, q.y - y0, q.z - z0);
    for dz in 0..2 {
        for dy in 0..2 {
            for dx in 0..2 {
                let w = (if dx == 1 { fx } else { 1.0 - fx }) * (if dy == 1 { fy } else { 1.0 - fy }) * (if dz == 1 { fz } else { 1.0 - fz });
                f(g.id(xi + dx, yi + dy, zi + dz), w);
            }
        }
    }
}

fn sample(g: &Grid, v: &[f32], p: &V3) -> Option<f64> {
    let mut acc = 0.0;
    let mut any = false;
    splat(g, p, |i, w| {
        acc += w * v[i] as f64;
        any = true;
    });
    any.then_some(acc)
}

/// Right-hand side: divergence of the splatted normal field.
fn rhs(g: &Grid, pts: &[V3], nrm: &[V3]) -> Vec<f32> {
    let n = g.len();
    let mut vx = vec![0.0f32; n];
    let mut vy = vec![0.0f32; n];
    let mut vz = vec![0.0f32; n];
    // Normal density per cell area so the field is resolution independent.
    let s = 1.0 / (g.h * g.h);
    for (p, m) in pts.iter().zip(nrm) {
        splat(g, p, |i, w| {
            vx[i] += (w * m.x * s) as f32;
            vy[i] += (w * m.y * s) as f32;
            vz[i] += (w * m.z * s) as f32;
        });
    }
    let mut b = vec![0.0f32; n];
    let inv = (1.0 / (2.0 * g.h)) as f32;
    for z in 1..g.n[2] - 1 {
        for y in 1..g.n[1] - 1 {
            for x in 1..g.n[0] - 1 {
                let i = g.id(x, y, z);
                b[i] = (vx[i + 1] - vx[i - 1]) * inv
                    + (vy[g.id(x, y + 1, z)] - vy[g.id(x, y - 1, z)]) * inv
                    + (vz[g.id(x, y, z + 1)] - vz[g.id(x, y, z - 1)]) * inv;
            }
        }
    }
    b
}

/// y = -L x (positive definite form of the 7-point Laplacian), Dirichlet 0 boundary.
fn apply(g: &Grid, x: &[f32], y: &mut [f32]) {
    let ih2 = (1.0 / (g.h * g.h)) as f32;
    let (nx, ny, nz) = (g.n[0], g.n[1], g.n[2]);
    let sy = nx;
    let sz = nx * ny;
    for z in 0..nz {
        for yy in 0..ny {
            for xx in 0..nx {
                let i = g.id(xx, yy, z);
                if xx == 0 || yy == 0 || z == 0 || xx == nx - 1 || yy == ny - 1 || z == nz - 1 {
                    y[i] = x[i];
                    continue;
                }
                let s = x[i - 1] + x[i + 1] + x[i - sy] + x[i + sy] + x[i - sz] + x[i + sz];
                y[i] = (6.0 * x[i] - s) * ih2;
            }
        }
    }
}

fn cg(g: &Grid, b: &[f32], x: &mut [f32], iters: usize) {
    let n = g.len();
    // -L x = -b
    let rhs: Vec<f32> = (0..n).map(|i| {
        let (xx, yy, zz) = (i % g.n[0], (i / g.n[0]) % g.n[1], i / (g.n[0] * g.n[1]));
        if xx == 0 || yy == 0 || zz == 0 || xx == g.n[0] - 1 || yy == g.n[1] - 1 || zz == g.n[2] - 1 { 0.0 } else { -b[i] }
    }).collect();
    let mut ax = vec![0.0f32; n];
    apply(g, x, &mut ax);
    let mut r: Vec<f32> = (0..n).map(|i| rhs[i] - ax[i]).collect();
    let mut p = r.clone();
    let mut ap = vec![0.0f32; n];
    let mut rr: f64 = r.iter().map(|v| (*v as f64) * (*v as f64)).sum();
    let r0 = rr.max(1e-30);
    for _ in 0..iters {
        apply(g, &p, &mut ap);
        let pap: f64 = p.iter().zip(&ap).map(|(a, b)| (*a as f64) * (*b as f64)).sum();
        if pap.abs() < 1e-30 {
            break;
        }
        let alpha = (rr / pap) as f32;
        for i in 0..n {
            x[i] += alpha * p[i];
            r[i] -= alpha * ap[i];
        }
        let rr_new: f64 = r.iter().map(|v| (*v as f64) * (*v as f64)).sum();
        if rr_new < 1e-10 * r0 {
            break;
        }
        let beta = (rr_new / rr) as f32;
        for i in 0..n {
            p[i] = r[i] + beta * p[i];
        }
        rr = rr_new;
    }
}

/// Trilinear upsampling of a coarse solution onto a finer grid with the same bounds.
fn prolong(c: &Grid, xc: &[f32], f: &Grid) -> Vec<f32> {
    let mut out = vec![0.0f32; f.len()];
    for z in 0..f.n[2] {
        for y in 0..f.n[1] {
            for x in 0..f.n[0] {
                let p = f.pos(x as f64, y as f64, z as f64);
                out[f.id(x, y, z)] = sample(c, xc, &p).unwrap_or(0.0) as f32;
            }
        }
    }
    out
}

pub struct PoissonResult {
    pub grid: Grid,
    /// field whose zero level set is the surface (negative inside)
    pub field: Vec<f32>,
    /// distance (in voxels) from each node to the nearest input point, capped
    pub dist: Vec<u16>,
}

/// Solve on a grid covering [lo, lo + ext] with `res` nodes along the longest side.
pub fn solve(pts: &[V3], nrm: &[V3], lo: V3, ext: V3, res: usize, progress: &mut dyn FnMut(f32)) -> PoissonResult {
    let levels = [res / 4, res / 2, res];
    let mut prev: Option<(Grid, Vec<f32>)> = None;
    for (li, &r) in levels.iter().enumerate() {
        let r = r.max(8);
        let h = ext.max() / (r - 1) as f64;
        let n = [0, 1, 2].map(|a| ((ext[a] / h).ceil() as usize + 1).max(4));
        let g = Grid { lo, h, n };
        let b = rhs(&g, pts, nrm);
        let mut x = match &prev {
            Some((pg, px)) => prolong(pg, px, &g),
            None => vec![0.0f32; g.len()],
        };
        let iters = [400, 120, 60][li];
        cg(&g, &b, &mut x, iters);
        progress((li + 1) as f32 / 4.0);
        prev = Some((g, x));
    }
    let (g, x) = prev.unwrap();
    // iso-value: average indicator at the samples
    let vals: Vec<f64> = pts.iter().filter_map(|p| sample(&g, &x, p)).collect();
    let iso = if vals.is_empty() { 0.0 } else { vals.iter().sum::<f64>() / vals.len() as f64 };
    let field: Vec<f32> = x.iter().map(|v| (*v as f64 - iso) as f32).collect();
    // distance to data (BFS over the grid from cells holding points)
    let mut dist = vec![u16::MAX; g.len()];
    let mut queue = std::collections::VecDeque::new();
    for p in pts {
        let q = (p - g.lo) / g.h;
        let (x, y, z) = (q.x.round(), q.y.round(), q.z.round());
        if x < 0.0 || y < 0.0 || z < 0.0 {
            continue;
        }
        let (x, y, z) = (x as usize, y as usize, z as usize);
        if x >= g.n[0] || y >= g.n[1] || z >= g.n[2] {
            continue;
        }
        let i = g.id(x, y, z);
        if dist[i] != 0 {
            dist[i] = 0;
            queue.push_back((x, y, z));
        }
    }
    while let Some((x, y, z)) = queue.pop_front() {
        let d = dist[g.id(x, y, z)];
        if d >= 4000 {
            continue;
        }
        let nb = [
            (x.wrapping_sub(1), y, z), (x + 1, y, z), (x, y.wrapping_sub(1), z),
            (x, y + 1, z), (x, y, z.wrapping_sub(1)), (x, y, z + 1),
        ];
        for (a, b, c) in nb {
            if a < g.n[0] && b < g.n[1] && c < g.n[2] {
                let j = g.id(a, b, c);
                if dist[j] == u16::MAX {
                    dist[j] = d + 1;
                    queue.push_back((a, b, c));
                }
            }
        }
    }
    progress(1.0);
    PoissonResult { grid: g, field, dist }
}

/// Naive Surface Nets on a field (negative inside); nodes failing `ok` are skipped.
pub fn surface_nets(g: &Grid, field: &[f32], ok: &dyn Fn(usize) -> bool) -> (Vec<V3>, Vec<u32>) {
    let (nx, ny, nz) = (g.n[0], g.n[1], g.n[2]);
    let (cx, cy, cz) = (nx - 1, ny - 1, nz - 1);
    let cid = |x: usize, y: usize, z: usize| (z * cy + y) * cx + x;
    let mut cell_vert = vec![-1i32; cx * cy * cz];
    let mut pos: Vec<V3> = Vec::new();
    const CORNERS: [(usize, usize, usize); 8] = [(0, 0, 0), (1, 0, 0), (0, 1, 0), (1, 1, 0), (0, 0, 1), (1, 0, 1), (0, 1, 1), (1, 1, 1)];
    const EDGES: [(usize, usize); 12] = [(0, 1), (2, 3), (4, 5), (6, 7), (0, 2), (1, 3), (4, 6), (5, 7), (0, 4), (1, 5), (2, 6), (3, 7)];
    for z in 0..cz {
        for y in 0..cy {
            for x in 0..cx {
                let mut vals = [0.0f32; 8];
                let (mut neg, mut posn, mut valid) = (false, false, true);
                for (c, &(dx, dy, dz)) in CORNERS.iter().enumerate() {
                    let j = g.id(x + dx, y + dy, z + dz);
                    if !ok(j) {
                        valid = false;
                        break;
                    }
                    vals[c] = field[j];
                    if field[j] < 0.0 { neg = true } else { posn = true }
                }
                if !valid || !neg || !posn {
                    continue;
                }
                let mut acc = V3::zeros();
                let mut cnt = 0.0;
                for &(a, b) in &EDGES {
                    let (va, vb) = (vals[a], vals[b]);
                    if (va < 0.0) != (vb < 0.0) {
                        let t = (va / (va - vb)) as f64;
                        let (pa, pb) = (CORNERS[a], CORNERS[b]);
                        acc += V3::new(
                            pa.0 as f64 + t * (pb.0 as f64 - pa.0 as f64),
                            pa.1 as f64 + t * (pb.1 as f64 - pa.1 as f64),
                            pa.2 as f64 + t * (pb.2 as f64 - pa.2 as f64),
                        );
                        cnt += 1.0;
                    }
                }
                let l = acc / cnt;
                cell_vert[cid(x, y, z)] = pos.len() as i32;
                pos.push(g.pos(x as f64 + l.x, y as f64 + l.y, z as f64 + l.z));
            }
        }
    }
    let mut idx = Vec::new();
    let cell = |x: isize, y: isize, z: isize| -> i32 {
        if x < 0 || y < 0 || z < 0 || x as usize >= cx || y as usize >= cy || z as usize >= cz { -1 } else { cell_vert[cid(x as usize, y as usize, z as usize)] }
    };
    let mut quad = |c: [i32; 4], flip: bool| {
        if c.iter().any(|&v| v < 0) {
            return;
        }
        let mut v = c.map(|x| x as u32);
        if flip {
            v.reverse();
        }
        idx.extend_from_slice(&[v[0], v[1], v[2], v[0], v[2], v[3]]);
    };
    for z in 0..nz {
        for y in 0..ny {
            for x in 0..nx {
                let j = g.id(x, y, z);
                if !ok(j) {
                    continue;
                }
                let inside = field[j] < 0.0;
                let (xi, yi, zi) = (x as isize, y as isize, z as isize);
                if x + 1 < nx && ok(g.id(x + 1, y, z)) && inside != (field[g.id(x + 1, y, z)] < 0.0) {
                    quad([cell(xi, yi - 1, zi - 1), cell(xi, yi, zi - 1), cell(xi, yi, zi), cell(xi, yi - 1, zi)], !inside);
                }
                if y + 1 < ny && ok(g.id(x, y + 1, z)) && inside != (field[g.id(x, y + 1, z)] < 0.0) {
                    quad([cell(xi - 1, yi, zi - 1), cell(xi - 1, yi, zi), cell(xi, yi, zi), cell(xi, yi, zi - 1)], !inside);
                }
                if z + 1 < nz && ok(g.id(x, y, z + 1)) && inside != (field[g.id(x, y, z + 1)] < 0.0) {
                    quad([cell(xi - 1, yi - 1, zi), cell(xi, yi - 1, zi), cell(xi, yi, zi), cell(xi - 1, yi, zi)], !inside);
                }
            }
        }
    }
    (pos, idx)
}
