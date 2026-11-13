# uv 0.13.7: locked Rust executable source frontier

**Target:** the uv executable (astral-sh/uv, the Python package manager written
in Rust), as a Rust package project alongside orjson (Rust extension), NumPy
(C/C++ extension), ZXing (Java) and go-enry (Go).

This bounded fixture is official **uv 0.13.7**, pinned to
[316c827e058c16b71d5c507a20729c2995a7bcce](https://github.com/astral-sh/uv/tree/316c827e058c16b71d5c507a20729c2995a7bcce)
(tag `0.13.7`, committed 2026-11-03; the same build hash the locally installed
`uv 0.13.7 (316c827e0 2026-11-03)` reports). Files were fetched from
`raw.githubusercontent.com` at that commit, not from a moving branch.

## Selection and identities

`pcc-source-lock.json` pins 12 complete original files (829,301 bytes) with
their paths, Git blob IDs, SHA-256, sizes and immutable URLs:

- Workspace `Cargo.toml`, `Cargo.lock`, `pyproject.toml` (maturin backend) and
  `rust-toolchain.toml` (channel 1.99.0; workspace `rust-version` 1.97.0,
  edition 2024).
- Both license texts. The workspace license is **MIT OR Apache-2.0**.
- Four Rust roots: the executable entry `crates/uv/src/bin/uv.rs`, the library
  root `crates/uv/src/lib.rs`, the dependency-free `crates/uv-version/src/lib.rs`
  and the lock-format owner `crates/uv-lock/src/lock/mod.rs` (whose revision
  history `pcc sync --locked` follows).

This is NOT the 87-crate workspace or its crates.io closure: no Cargo graph,
build script, procedural macro, dependency, or upstream test is executed or
claimed. Cross-check: the PyPI sdist `uv-0.13.7.tar.gz`
(sha256 `5e766791cb825d7a07e2ef9e622d307b48754fb682a31ba7bc5f272fab9ea395`)
has 9 of these files byte-identical; maturin's sdist packaging adds `readme`
fields to the two crate manifests and prunes dev-only entries from `Cargo.lock`,
so the GitHub originals are the pinned identities.

## Current frontier (host pcc, Rust parser, edition 2024)

`tests/rust/test_uv_source_frontier.py` verifies the identities through the
shared source-lock loader and records each root's first blocker:

| Root | First blocker |
|---|---|
| `crates/uv/src/bin/uv.rs` | `3:3` `#[cfg(feature = ...)]` needs a checked Cargo configuration |
| `crates/uv/src/lib.rs` | `1515:34` `@` binding pattern (`run_variant @ (...)`) |
| `crates/uv-version/src/lib.rs` | `5:5` `env!` macro invocation |
| `crates/uv-lock/src/lock/mod.rs` | `10666:22` `@` inside a `macro_rules!` matcher |

```sh
uv run pytest -x -q tests/rust/test_uv_source_frontier.py
```

Native pcc1, owned Cargo/build orchestration, core/alloc/std and the
dependency closure, linking and running uv remain open.
