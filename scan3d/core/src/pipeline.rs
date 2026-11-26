//! End-to-end reconstruction pipeline driven stage by stage.

use crate::features::{detect_and_describe, match_descriptors, Features};
use crate::geometry::{fundamental_ransac, Intrinsics};

use crate::image::Gray;
use crate::mesh::{build_mesh, Mesh};
use crate::mvs::{compute_depth_maps, filter_depth_maps, fuse, Cloud, DenseOptions, DepthMap};
use crate::sfm::{reconstruct_from, PairMatch, Reconstruction};

pub type Report<'a> = &'a mut dyn FnMut(f32, &str);

pub struct Options {
    pub max_features: usize,
    pub match_window: usize,
    pub fov_deg: f64,
    pub dense: DenseOptions,
    pub mesh_res: usize,
    pub voxel: f64,
}

impl Default for Options {
    fn default() -> Self {
        Options { max_features: 1500, match_window: 6, fov_deg: 65.0, dense: DenseOptions::default(), mesh_res: 200, voxel: 0.008 }
    }
}

#[derive(Default)]
pub struct Pipeline {
    pub opts: Options,
    pub w: usize,
    pub h: usize,
    pub rgba: Vec<Vec<u8>>,
    pub grays: Vec<Gray>,
    pub feats: Vec<Features>,
    pub pairs: Vec<PairMatch>,
    pub rec: Option<Reconstruction>,
    pub maps: Vec<Option<DepthMap>>,
    pub cloud: Option<Cloud>,
    pub mesh: Option<Mesh>,
    /// frames [0, matched_upto) have been matched against each other
    pub matched_upto: usize,
    /// pixel in frame 0 that marks the object being scanned
    pub target_uv: Option<(f64, f64)>,
    /// starting pair of the last successful solve (reused while capturing)
    pub init_pair: Option<(usize, usize)>,
}

impl Pipeline {
    pub fn new() -> Self {
        Self::default()
    }

    pub fn add_frame(&mut self, rgba: &[u8], w: usize, h: usize) -> Result<(), String> {
        if rgba.len() != w * h * 4 {
            return Err("frame buffer size does not match width*height*4".into());
        }
        if !self.rgba.is_empty() && (w != self.w || h != self.h) {
            return Err(format!("all frames must be {}x{}", self.w, self.h));
        }
        self.w = w;
        self.h = h;
        self.rgba.push(rgba.to_vec());
        self.grays.push(Gray::from_rgba(rgba, w, h));
        Ok(())
    }

    fn px_scale(&self) -> f64 {
        (self.w.max(self.h) as f64 / 720.0).max(0.5)
    }

    pub fn focal_guess(&self) -> f64 {
        0.5 * self.w.max(self.h) as f64 / (self.opts.fov_deg.to_radians() * 0.5).tan()
    }

    pub fn features(&mut self, rep: Report) {
        let n = self.grays.len();
        self.feats.truncate(n);
        for i in self.feats.len()..n {
            self.feats.push(detect_and_describe(&self.grays[i], self.opts.max_features));
            rep((i + 1) as f32 / n as f32, "");
        }
        let total: usize = self.feats.iter().map(|f| f.kps.len()).sum();
        rep(1.0, &format!("features: {} images, {:.0} keypoints/image", n, total as f64 / n.max(1) as f64));
    }

    fn pair_list(&self) -> Vec<(usize, usize)> {
        let n = self.feats.len();
        let mut v = Vec::new();
        for i in 0..n {
            for j in i + 1..n {
                let near = j - i <= self.opts.match_window;
                // sparse skeleton of long-range pairs for loop closure / unordered input
                let skel = n <= 24 || (i % 3 == 0 && j % 3 == 0);
                if near || skel {
                    v.push((i, j));
                }
            }
        }
        v
    }

    fn match_pair(&self, i: usize, j: usize) -> Option<PairMatch> {
        let thr = 3.0 * self.px_scale();
        let m = match_descriptors(&self.feats[i].desc, &self.feats[j].desc, 0.8, 80);
        if m.len() < 30 {
            return None;
        }
        let p1: Vec<[f64; 2]> = m.iter().map(|&(a, _)| { let q = self.feats[i].kps[a as usize]; [q.x as f64, q.y as f64] }).collect();
        let p2: Vec<[f64; 2]> = m.iter().map(|&(_, b)| { let q = self.feats[j].kps[b as usize]; [q.x as f64, q.y as f64] }).collect();
        let (_, mask) = fundamental_ransac(&p1, &p2, thr, (i * 7919 + j) as u64)?;
        let inl: Vec<(u32, u32)> = m.iter().zip(mask).filter(|(_, ok)| *ok).map(|(x, _)| *x).collect();
        (inl.len() >= 30).then_some(PairMatch { i, j, matches: inl })
    }

    /// Batch matching of all frames (ordered capture: neighbours + skeleton).
    pub fn matching(&mut self, rep: Report) {
        let list = self.pair_list();
        self.pairs.clear();
        for (k, &(i, j)) in list.iter().enumerate() {
            if let Some(pm) = self.match_pair(i, j) {
                self.pairs.push(pm);
            }
            if k % 4 == 0 {
                rep(k as f32 / list.len() as f32, "");
            }
        }
        self.matched_upto = self.feats.len();
        rep(1.0, &format!("matching: {} of {} image pairs verified", self.pairs.len(), list.len()));
    }

    /// Match frames added since the last call against every earlier frame.
    pub fn match_new(&mut self, rep: Report) {
        let n = self.feats.len();
        for j in self.matched_upto..n {
            for i in 0..j {
                if let Some(pm) = self.match_pair(i, j) {
                    self.pairs.push(pm);
                }
            }
            rep(1.0, "");
        }
        self.matched_upto = n;
    }

    /// Features + matching for new frames, then a fresh pose solve.
    /// Returns the number of registered frames (0 when no solution yet).
    pub fn update(&mut self, rep: Report) -> usize {
        self.features(&mut |_, _| {});
        self.match_new(&mut |_, _| {});
        self.rec = None;
        self.cloud = None;
        self.mesh = None;
        self.sfm(rep).unwrap_or(0)
    }

    pub fn remove_frame(&mut self, i: usize) {
        if i >= self.rgba.len() {
            return;
        }
        self.rgba.remove(i);
        self.grays.remove(i);
        if i < self.feats.len() {
            self.feats.remove(i);
        }
        self.pairs.retain(|p| p.i != i && p.j != i);
        for p in self.pairs.iter_mut() {
            if p.i > i { p.i -= 1; }
            if p.j > i { p.j -= 1; }
        }
        if i < self.matched_upto {
            self.matched_upto -= 1;
        }
        if i == 0 {
            self.target_uv = None;
        }
        self.init_pair = None;
        self.rec = None;
        self.cloud = None;
        self.mesh = None;
        self.maps.clear();
        if self.rgba.is_empty() {
            self.w = 0;
            self.h = 0;
        }
    }

    /// 3D centre of the scanned object: sparse points seen near the marked
    /// pixel of frame 0 (image centre when unmarked), else the scene centre.
    pub fn target_point(&self) -> Option<crate::geometry::V3> {
        let rec = self.rec.as_ref()?;
        let (u, v) = self.target_uv.unwrap_or((self.w as f64 / 2.0, self.h as f64 / 2.0));
        let dim = self.w.max(self.h) as f64;
        for rad in [0.15, 0.3, 0.5] {
            let mut xs: Vec<crate::geometry::V3> = Vec::new();
            for p in &rec.points {
                if let Some(&(_, f)) = p.obs.iter().find(|o| o.0 == 0) {
                    let k = self.feats[0].kps[f];
                    if ((k.x as f64 - u).powi(2) + (k.y as f64 - v).powi(2)).sqrt() < rad * dim {
                        xs.push(p.x);
                    }
                }
            }
            if xs.len() >= 8 {
                let med = |mut a: Vec<f64>| { a.sort_by(|x, y| x.partial_cmp(y).unwrap()); a[a.len() / 2] };
                return Some(crate::geometry::V3::new(
                    med(xs.iter().map(|p| p.x).collect()),
                    med(xs.iter().map(|p| p.y).collect()),
                    med(xs.iter().map(|p| p.z).collect()),
                ));
            }
        }
        Some(crate::geometry::V3::zeros())
    }

    pub fn sfm(&mut self, rep: Report) -> Result<usize, String> {
        let f = self.focal_guess();
        let intr = Intrinsics { f, cx: self.w as f64 / 2.0 - 0.5, cy: self.h as f64 / 2.0 - 0.5, k1: 0.0 };
        let thr = 4.0 * self.px_scale();
        let rec = reconstruct_from(&self.feats, &self.pairs, intr, thr, self.init_pair, &mut *rep);
        let Some(mut rec) = rec else {
            return Err("could not initialise: need more overlap / texture / parallax between photos".into());
        };
        for p in rec.points.iter_mut() {
            let (img, f) = p.obs[0];
            let k = self.feats[img].kps[f];
            let c = crate::image::sample_rgb(&self.rgba[img], self.w, self.h, k.x, k.y);
            p.color = [c[0] as u8, c[1] as u8, c[2] as u8];
        }
        self.init_pair = rec.init_pair;
        let n = rec.registered();
        rep(1.0, &format!("sfm: {} / {} cameras registered, {} sparse points", n, self.rgba.len(), rec.points.len()));
        self.rec = Some(rec);
        Ok(n)
    }

    pub fn dense(&mut self, rep: Report) -> Result<usize, String> {
        let rec = self.rec.as_ref().ok_or("run sfm first")?;
        let mut prog = |p: f32| rep(p * 0.9, "");
        let mut maps = compute_depth_maps(&self.grays, rec, &self.opts.dense, &mut prog);
        let raw: usize = maps.iter().flatten().map(|m| m.depth.iter().filter(|d| d.is_finite()).count()).sum();
        let kept = filter_depth_maps(&mut maps, rec);
        let cloud = fuse(&maps, rec, &self.rgba, self.w, self.h, self.opts.voxel);
        rep(1.0, &format!("dense: {} depth maps, {} raw / {} consistent depths, {} fused points",
            maps.iter().flatten().count(), raw, kept, cloud.pos.len()));
        let n = cloud.pos.len();
        self.maps = maps;
        self.cloud = Some(cloud);
        Ok(n)
    }

    pub fn mesh(&mut self, rep: Report) -> Result<usize, String> {
        let rec = self.rec.as_ref().ok_or("run sfm first")?;
        let cloud = self.cloud.as_ref().ok_or("run dense first")?;
        let mut prog = |p: f32| rep(p, "");
        let mesh = build_mesh(&self.maps, rec, cloud, &self.rgba, self.w, self.h, self.opts.mesh_res, &mut prog);
        let n = mesh.idx.len() / 3;
        rep(1.0, &format!("mesh: {} vertices, {} triangles", mesh.pos.len(), n));
        self.mesh = Some(mesh);
        Ok(n)
    }
}
