//! Niagara Falls economy simulation engine.
//!
//! One codebase, two builds: a native program for calibration and backtests, and
//! WebAssembly for the browser game. Nothing in the library touches files or clocks.
pub mod city;
pub mod engine;
pub mod rng;
pub mod tourists;
#[cfg(target_arch = "wasm32")]
pub mod wasm;

pub use city::City;
pub use engine::{Engine, Mode, Snapshot, Trip};
