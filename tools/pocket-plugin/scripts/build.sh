#!/bin/sh
# SPDX-License-Identifier: MIT
# Release build of plugin.wasm. Rust keeps source paths (for panic messages)
# in the binary; these remaps keep the build machine's folders out of it.
set -e
cd "$(dirname "$0")/.."
cargo_home="${CARGO_HOME:-$HOME/.cargo}"
rustup_home="${RUSTUP_HOME:-$HOME/.rustup}"
# When several remaps match, rustc uses the last one: most specific last.
export RUSTFLAGS="$RUSTFLAGS --remap-path-prefix=$HOME=/home --remap-path-prefix=$rustup_home=/rustup --remap-path-prefix=$cargo_home=/cargo --remap-path-prefix=$cargo_home/registry/src=/crates --remap-path-prefix=$PWD=/plugin"
exec cargo build --release --locked "$@"
