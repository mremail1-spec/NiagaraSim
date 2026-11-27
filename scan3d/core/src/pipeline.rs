//! End-to-end reconstruction pipeline driven stage by stage.

use crate::features::{detect_and_describe, match_descriptors, Features};
use crate::geometry::{fundamental_ransac, Intrinsics};

use crate::image::Gray;
use crate::mesh::{build_mesh, build_mesh_poisson, Mesh};
use crate::mvs::{compute_depth_maps, filter_depth_maps, fuse, Cloud, DenseOptions, DepthMap};
use crate::sfm::{align_to_gps, apply_similarity, reconstruct_from, reconstruct_gps, refine_with_gps, PairMatch, Reconstruction};
use crate::features::Features as Feats;

pub type Report<'a> = &'a mut dyn FnMut(f32, &str);

pub struct Options {
    pub max_features: usize,
    pub match_window: usize,
    pub fov_deg: f64,
    pub dense: DenseOptions,
    pub mesh_res: usize,
    pub voxel: f64,
    /// field of view is known (photo EXIF or user setting): skip the lens search
    pub fov_known: bool,
    /// mesh with depth-map fusion (TSDF) instead of Poisson reconstruction
    pub mesh_tsdf: bool,
}

impl Default for Options {
    fn default() -> Self {
        Options { max_features: 1500, match_window: 6, fov_deg: 65.0, dense: DenseOptions::default(), mesh_res: 200, voxel: 0.008, fov_known: false, mesh_tsdf: false }
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
    /// field of view found by the lens search, and at how many frames
    pub fov_found: Option<(f64, usize)>,
    /// per-frame GPS position in local metres (x east, y up, z south) and accuracy (m)
    pub gps: Vec<Option<(crate::geometry::V3, f64)>>,
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
                // Orbit scans end where they started, so also match around the wrap.
                let near = j - i <= self.opts.match_window || n - (j - i) <= self.opts.match_window;
                // sparse skeleton of long-range pairs for loop closure / unordered input
                // Small scans: match everything (long-range pairs bridge weak stretches).
                let skel = n <= 50 || (i % 3 == 0 && j % 3 == 0);
                if near || skel {
                    v.push((i, j));
                }
            }
        }
        v
    }

    fn match_pair(&self, i: usize, j: usize) -> Option<PairMatch> {
        let thr = 2.0 * self.px_scale();
        let (fa, fb) = (&self.feats[i], &self.feats[j]);
        let m = match_descriptors(&fa.desc, &fb.desc, 0.8, 80);
        if m.len() < 20 {
            return None;
        }
        let pts = |m: &[(u32, u32)]| -> (Vec<[f64; 2]>, Vec<[f64; 2]>) {
            m.iter().map(|&(a, b)| { let (p, q) = (fa.kps[a as usize], fb.kps[b as usize]); ([p.x as f64, p.y as f64], [q.x as f64, q.y as f64]) }).unzip()
        };
        let (p1, p2) = pts(&m);
        let (_, mask) = fundamental_ransac(&p1, &p2, thr, (i * 7919 + j) as u64)?;
        if mask.iter().filter(|&&b| b).count() < 15 {
            return None;
        }
        // A guided second pass (re-matching along epipolar lines) was evaluated and
        // dropped: on soft texture it mostly added near-miss matches.
        let inl: Vec<(u32, u32)> = m.iter().zip(&mask).filter(|(_, ok)| **ok).map(|(x, _)| *x).collect();
        (inl.len() >= 20).then_some(PairMatch { i, j, matches: inl })
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

    fn intr_for(&self, fov_deg: f64) -> Intrinsics {
        let f = 0.5 * self.w.max(self.h) as f64 / (fov_deg.to_radians() * 0.5).tan();
        Intrinsics { f, cx: self.w as f64 / 2.0 - 0.5, cy: self.h as f64 / 2.0 - 0.5, k1: 0.0 }
    }

    /// Lens search: reconstruct a block of frames with the lens held fixed at
    /// several fields of view and keep the one with the lowest reprojection
    /// error. Letting bundle adjustment free the focal length is unreliable
    /// (it drifts on forward motion), so the lens stays fixed afterwards.
    fn search_fov(&self, rep: Report) -> Option<f64> {
        let n = self.feats.len();
        let len = n.min(40);
        // the block of consecutive photos with the most verified pairs inside it
        let a = (0..=n - len)
            .max_by_key(|&a| self.pairs.iter().filter(|p| p.i >= a && p.j < a + len).map(|p| p.matches.len()).sum::<usize>())
            .unwrap_or(0);
        let b = a + len;
        let feats: Vec<Feats> = self.feats[a..b].to_vec();
        let pairs: Vec<PairMatch> = self
            .pairs
            .iter()
            .filter(|p| p.i >= a && p.j < b)
            .map(|p| PairMatch { i: p.i - a, j: p.j - a, matches: p.matches.clone() })
            .collect();
        let thr = 4.0 * self.px_scale();
        let eval = |fov: f64| -> (f64, usize) {
            match reconstruct_from(&feats, &pairs, self.intr_for(fov), thr, None, false, &mut |_, _| {}) {
                Some(r) => (r.rms, r.registered()),
                None => (f64::INFINITY, 0),
            }
        };
        let mut tried: Vec<(f64, f64, usize)> = Vec::new();
        for fov in [40.0, 50.0, 60.0, 70.0, 80.0, 90.0, 100.0, 110.0] {
            let (rms, reg) = eval(fov);
            tried.push((fov, rms, reg));
            rep(tried.len() as f32 / 14.0, "");
        }
        let pick = |t: &[(f64, f64, usize)]| -> Option<f64> {
            let max_reg = t.iter().map(|x| x.2).max().unwrap_or(0);
            t.iter()
                .filter(|x| x.2 >= 3 && x.2 as f64 >= 0.8 * max_reg as f64 && x.1.is_finite())
                .min_by(|x, y| x.1.partial_cmp(&y.1).unwrap())
                .map(|x| x.0)
        };
        let coarse = pick(&tried)?;
        // A flat error curve, or a best value at the edge of the range, means the
        // photos do not determine the lens (e.g. pure sideways motion): give up.
        let finite: Vec<f64> = tried.iter().map(|x| x.1).filter(|v| v.is_finite()).collect();
        let (mn, mx) = finite.iter().fold((f64::MAX, f64::MIN), |a, &v| (a.0.min(v), a.1.max(v)));
        if coarse <= 40.0 || coarse >= 110.0 || (mx - mn) / mn.max(1e-9) < 0.03 {
            return None;
        }
        // golden-section refinement around the coarse minimum
        let (mut lo, mut hi) = (coarse - 7.0, coarse + 7.0);
        let g = 0.618_033_988_75;
        let mut x1 = hi - g * (hi - lo);
        let mut x2 = lo + g * (hi - lo);
        let mut f1 = eval(x1);
        let mut f2 = eval(x2);
        tried.push((x1, f1.0, f1.1));
        tried.push((x2, f2.0, f2.1));
        for _ in 0..4 {
            if f1.0 < f2.0 {
                hi = x2; x2 = x1; f2 = f1; x1 = hi - g * (hi - lo); f1 = eval(x1); tried.push((x1, f1.0, f1.1));
            } else {
                lo = x1; x1 = x2; f1 = f2; x2 = lo + g * (hi - lo); f2 = eval(x2); tried.push((x2, f2.0, f2.1));
            }
            rep(tried.len() as f32 / 14.0, "");
        }
        pick(&tried)
    }

    pub fn sfm(&mut self, rep: Report) -> Result<usize, String> {
        let n_frames = self.feats.len();
        let fov = if self.opts.fov_known {
            self.opts.fov_deg
        } else {
            let stale = match self.fov_found { None => n_frames >= 10, Some((_, at)) => at < 25 && n_frames >= 25 };
            if stale {
                match self.search_fov(&mut *rep) {
                    Some(f) => {
                        self.fov_found = Some((f, n_frames));
                        rep(0.0, &format!("lens: estimated field of view {:.1}° (from {} photos)", f, n_frames.min(40)));
                    }
                    None => {
                        self.fov_found = Some((self.opts.fov_deg, n_frames));
                        rep(0.0, &format!("lens: could not be determined from these photos; using {:.0}°", self.opts.fov_deg));
                    }
                }
            }
            self.fov_found.map_or(self.opts.fov_deg, |x| x.0)
        };
        let intr = self.intr_for(fov);
        let thr = 4.0 * self.px_scale();
        let gps_pts: Vec<Option<crate::geometry::V3>> = (0..self.feats.len()).map(|i| self.gps.get(i).copied().flatten().map(|g| g.0)).collect();
        let n_gps = gps_pts.iter().filter(|g| g.is_some()).count();
        let acc = {
            let mut a: Vec<f64> = self.gps.iter().flatten().map(|g| g.1).collect();
            a.sort_by(|x, y| x.partial_cmp(y).unwrap());
            a.get(a.len() / 2).copied().unwrap_or(5.0).max(2.0)
        };
        let rec = if n_gps >= 3 {
            reconstruct_gps(&self.feats, &self.pairs, intr, thr, self.init_pair, false, Some((&gps_pts, acc)), &mut *rep)
        } else {
            reconstruct_from(&self.feats, &self.pairs, intr, thr, self.init_pair, false, &mut *rep)
        };
        // GPS: put the model in metres on the map, then refine with GPS priors.
        let rec = rec.map(|mut r| {
            if n_gps >= 3 {
                if let Some((sc, rot, t, inl, med)) = align_to_gps(&r, &gps_pts, 3.0 * acc) {
                    apply_similarity(&mut r, sc, &rot, &t);
                    let pri: Vec<Option<(crate::geometry::V3, crate::geometry::V3)>> = self
                        .gps
                        .iter()
                        .map(|g| g.map(|(p, a)| {
                            let a = a.max(2.0);
                            (p, crate::geometry::V3::new(1.0 / a, 0.2 / a, 1.0 / a))
                        }))
                        .collect();
                    let med_after_align = {
                        let mut v: Vec<f64> = r.poses.iter().zip(&gps_pts).filter_map(|(p, g)| { let (p, g) = (p.as_ref()?, (*g)?); Some(((p.center().x - g.x).powi(2) + (p.center().z - g.z).powi(2)).sqrt()) }).collect();
                        v.sort_by(|a, b| a.partial_cmp(b).unwrap());
                        v.get(v.len() / 2).copied().unwrap_or(f64::NAN)
                    };
                    rep(0.0, &format!("GPS debug: {} cams registered, median {:.1} m right after alignment", r.registered(), med_after_align));
                    let (r0, r1) = refine_with_gps(&mut r, &self.feats, &pri, thr, 30);
                    let mut res: Vec<f64> = r.poses.iter().zip(&gps_pts).filter_map(|(p, g)| {
                        let (p, g) = (p.as_ref()?, (*g)?);
                        Some(((p.center().x - g.x).powi(2) + (p.center().z - g.z).powi(2)).sqrt())
                    }).collect();
                    res.sort_by(|a, b| a.partial_cmp(b).unwrap());
                    rep(0.0, &format!(
                        "GPS: {} cameras aligned (median {:.1} m before refinement, {:.1} m after); scale is now metres; rms {:.2}->{:.2}px, k1 {:.3}",
                        inl, med, res.get(res.len() / 2).copied().unwrap_or(f64::NAN), r0, r1, r.intr.k1
                    ));
                } else {
                    rep(0.0, "GPS: could not align the reconstruction to the GPS track");
                }
            }
            r
        });
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
        let (mesh, mode) = if self.opts.mesh_tsdf {
            (build_mesh(&self.maps, rec, cloud, &self.rgba, self.w, self.h, self.opts.mesh_res, &mut prog), "TSDF".to_string())
        } else {
            build_mesh_poisson(&self.maps, rec, cloud, &self.rgba, self.w, self.h, self.opts.mesh_res, &mut prog)
        };
        let n = mesh.idx.len() / 3;
        rep(1.0, &format!("mesh: {} vertices, {} triangles, {}", mesh.pos.len(), n, mode));
        self.mesh = Some(mesh);
        Ok(n)
    }
}
