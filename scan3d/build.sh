#!/usr/bin/env bash
# Build the Rust core to WebAssembly and place the JS bindings in web/pkg.
# Needs: rustup target add wasm32-unknown-unknown; cargo install wasm-bindgen-cli --version 0.2.100
set -euo pipefail
cd "$(dirname "$0")/core"
cargo build --release --lib --target wasm32-unknown-unknown
wasm-bindgen --target web --no-typescript --out-dir ../web/pkg \
  target/wasm32-unknown-unknown/release/scan3d.wasm
if command -v wasm-opt >/dev/null; then wasm-opt -O3 ../web/pkg/scan3d_bg.wasm -o ../web/pkg/scan3d_bg.wasm; fi
ls -l ../web/pkg
