//! scan3d — photogrammetry in the browser.
//!
//! Frames (RGBA) go in; camera poses, a sparse and a dense coloured point
//! cloud, and a vertex-coloured triangle mesh come out.

pub mod ba;
pub mod features;
pub mod geometry;
pub mod image;
pub mod mesh;
pub mod mvs;
pub mod pipeline;
pub mod sfm;
#[cfg(not(target_arch = "wasm32"))]
pub mod synth;

use pipeline::Pipeline;
use wasm_bindgen::prelude::*;

#[wasm_bindgen]
extern "C" {
    #[wasm_bindgen(js_namespace = console)]
    fn error(s: &str);
}

fn flat3<T: Copy>(v: &[[T; 3]]) -> Vec<T> {
    v.iter().flat_map(|a| a.iter().copied()).collect()
}

#[wasm_bindgen]
pub struct Scanner {
    p: Pipeline,
}

#[wasm_bindgen]
impl Scanner {
    #[wasm_bindgen(constructor)]
    pub fn new() -> Scanner {
        std::panic::set_hook(Box::new(|info| error(&format!("scan3d panic: {info}"))));
        Scanner { p: Pipeline::new() }
    }

    /// Tunables: max_features, match_window, fov_deg, dense_size, dense_depths,
    /// dense_sources, dense_views, min_zncc, mesh_res, voxel.
    pub fn set_option(&mut self, key: &str, v: f64) -> Result<(), JsValue> {
        let o = &mut self.p.opts;
        match key {
            "max_features" => o.max_features = v as usize,
            "match_window" => o.match_window = v as usize,
            "fov_deg" => o.fov_deg = v,
            "dense_size" => o.dense.size = v as usize,
            "dense_depths" => o.dense.depths = v as usize,
            "dense_sources" => o.dense.sources = v as usize,
            "dense_views" => o.dense.max_views = v as usize,
            "min_zncc" => o.dense.min_zncc = v as f32,
            "mesh_res" => o.mesh_res = v as usize,
            "voxel" => o.voxel = v,
            _ => return Err(JsValue::from_str(&format!("unknown option {key}"))),
        }
        Ok(())
    }

    pub fn add_frame(&mut self, rgba: &[u8], w: u32, h: u32) -> Result<(), JsValue> {
        self.p.add_frame(rgba, w as usize, h as usize).map_err(|e| JsValue::from_str(&e))
    }

    pub fn frame_count(&self) -> u32 {
        self.p.rgba.len() as u32
    }

    fn report(cb: &js_sys::Function) -> impl FnMut(f32, &str) + '_ {
        move |p: f32, m: &str| {
            let _ = cb.call2(&JsValue::NULL, &JsValue::from_f64(p as f64), &JsValue::from_str(m));
        }
    }

    pub fn run_features(&mut self, cb: &js_sys::Function) {
        self.p.features(&mut Self::report(cb));
    }
    pub fn run_matching(&mut self, cb: &js_sys::Function) {
        self.p.matching(&mut Self::report(cb));
    }
    pub fn run_sfm(&mut self, cb: &js_sys::Function) -> Result<u32, JsValue> {
        self.p.sfm(&mut Self::report(cb)).map(|n| n as u32).map_err(|e| JsValue::from_str(&e))
    }
    pub fn run_dense(&mut self, cb: &js_sys::Function) -> Result<u32, JsValue> {
        self.p.dense(&mut Self::report(cb)).map(|n| n as u32).map_err(|e| JsValue::from_str(&e))
    }
    pub fn run_mesh(&mut self, cb: &js_sys::Function) -> Result<u32, JsValue> {
        self.p.mesh(&mut Self::report(cb)).map(|n| n as u32).map_err(|e| JsValue::from_str(&e))
    }

    pub fn sparse_positions(&self) -> Vec<f32> {
        self.p.rec.as_ref().map_or(vec![], |r| r.points.iter().flat_map(|p| [p.x.x as f32, p.x.y as f32, p.x.z as f32]).collect())
    }
    pub fn sparse_colors(&self) -> Vec<u8> {
        self.p.rec.as_ref().map_or(vec![], |r| r.points.iter().flat_map(|p| p.color).collect())
    }
    pub fn dense_positions(&self) -> Vec<f32> {
        self.p.cloud.as_ref().map_or(vec![], |c| flat3(&c.pos))
    }
    pub fn dense_colors(&self) -> Vec<u8> {
        self.p.cloud.as_ref().map_or(vec![], |c| flat3(&c.col))
    }
    pub fn dense_normals(&self) -> Vec<f32> {
        self.p.cloud.as_ref().map_or(vec![], |c| flat3(&c.nrm))
    }
    pub fn mesh_positions(&self) -> Vec<f32> {
        self.p.mesh.as_ref().map_or(vec![], |m| flat3(&m.pos))
    }
    pub fn mesh_colors(&self) -> Vec<u8> {
        self.p.mesh.as_ref().map_or(vec![], |m| flat3(&m.col))
    }
    pub fn mesh_normals(&self) -> Vec<f32> {
        self.p.mesh.as_ref().map_or(vec![], |m| flat3(&m.nrm))
    }
    pub fn mesh_indices(&self) -> Vec<u32> {
        self.p.mesh.as_ref().map_or(vec![], |m| m.idx.clone())
    }
    /// Per registered camera: [frame index, centre xyz, rotation (world->camera) row-major 9].
    pub fn cameras(&self) -> Vec<f32> {
        let mut out = Vec::new();
        if let Some(r) = &self.p.rec {
            for (i, p) in r.poses.iter().enumerate() {
                if let Some(p) = p {
                    let c = p.center();
                    out.extend_from_slice(&[i as f32, c.x as f32, c.y as f32, c.z as f32]);
                    out.extend(p.r.transpose().iter().map(|v| *v as f32)); // column-major of R^T = row-major of R
                }
            }
        }
        out
    }
    /// [focal px, k1, width, height]
    pub fn intrinsics(&self) -> Vec<f64> {
        self.p.rec.as_ref().map_or(vec![], |r| vec![r.intr.f, r.intr.k1, self.p.w as f64, self.p.h as f64])
    }
}

impl Default for Scanner {
    fn default() -> Self {
        Self::new()
    }
}
