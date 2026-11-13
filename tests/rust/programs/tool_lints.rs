// Original PCC specification fixture: tool-scoped crate lint attributes, as in
// uv 0.13.7's crates/uv/src/lib.rs. rustc evaluates none of these lints.
#![deny(clippy::print_stdout, clippy::print_stderr)]
#![warn(rustdoc::broken_intra_doc_links, clippy::pedantic::nested)]
#![forbid(clippy::all)]
#![allow(clippy::all)]
pub fn answer()->u64 { 0xfedcba9876543210u64 }
