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
shared source-lock loader and records each root's first blocker. cfg is
evaluated by PCC's owned configuration for `x86_64-unknown-linux-gnu` with the
inputs `cargo build --release` gives each package, computed from the pinned
manifests by `pcc/frontends/rust/cargo_config.py`: uv's default features
(`performance`, `performance-memory-allocator` and the `test-*` set), no
`test`, and the workspace release profile (no debug assertions,
`panic = "abort"`). `crates/uv-lock/Cargo.toml` is not pinned; that root's only
predicate is `cfg(test)`, which the test asserts.

| Root | First blocker |
|---|---|
| `crates/uv/src/bin/uv.rs` | `4:14` `extern crate uv_performance_memory_allocator;` (active under default features) needs external crate loading |
| `crates/uv/src/lib.rs` | `121:36` `format!(..)` (core formatting and an owned `String`) |
| `crates/uv-version/src/lib.rs` | none in the frontend: the whole root compiles, and with the value-only attestation an owned native build prints `0.13.7`; without it, the global gate (no owned borrow checker) remains |
| `crates/uv-lock/src/lock/mod.rs` | `105:24` named-field enum variant `UnsupportedVersion { supported: u32, version: u32 }` |

Both roots formerly stopped in the lexer at `@` (`lib.rs:1515:34`, the
`run_variant @ (ToolCommand::Uvx(_) | ToolCommand::Run(_))` binding, and
`lock/mod.rs:10666:22` inside a `macro_rules!` matcher). Identifier `@`
subpatterns are now owned (`tests/rust/programs/at_bindings.rs`); macro
matchers remain a macro-by-example gap. Grouped use trees
(`use std::collections::{BTreeMap, BTreeSet, VecDeque};`, formerly `3:23`)
are now parsed into one checked import per leaf
(`tests/rust/programs/use_grouped_tree.rs`), and `pub use` re-exports
(`pub use crate::lock::deserialize::Error as CanonicalLockError;`, formerly
`74:5`) resolve with rustc's E0364/E0365 visibility checks
(`tests/rust/programs/use_public_reexport.rs`). Restricted visibility
(`pub(crate) mod export;`, formerly `87:4`) is owned too
(`tests/rust/programs/visibility_restricted.rs`). Tool-scoped crate lints
(`#![deny(clippy::print_stdout, clippy::print_stderr)]`, formerly `lib.rs:1:15`)
are retained with no compile effect, as rustc evaluates none of them
(`tests/rust/programs/tool_lints.rs`). The three former `cfg` blockers
(`uv.rs:3:3`, `lib.rs:7:3`, `lock/mod.rs:94:3`) are evaluated by the owned
configuration (`tests/rust/test_rust_owned_cfg.py`), and the integer const
item `const VERSION: u32 = 1;` (formerly `lock/mod.rs:98:1`) is owned
(`tests/rust/programs/const_items.rs`). `env!` is evaluated against the crate
environment Cargo sets, derived from the pinned manifests
(`tests/rust/test_rust_builtin_env.py`). With string literals as `&'static str`
values and disabled items skipped by delimiter structure (its `#[cfg(test)] mod
tests`), `uv-version` is the first uv root PCC compiles in full: on Apple
silicon, PCC's own compiler, assembler and Mach-O linker produce a program
whose `version()` prints `0.13.7`, as rustc's build of the same root does
(`test_uv_version_compiles_and_runs_natively`, local Mac evidence; the
x86-64 Linux run of the same test is unrun in this change). The lifetime
argument of `BaseClientBuilder<'a>` (formerly `lib.rs:70:75`) now parses
(`tests/rust/test_rust_lifetime_arguments.py`); explicit lifetimes still need
an actual rustc check, as PCC has no owned lifetime analysis. Built-in derives
(`#[derive(Debug, Copy, Clone, Eq, PartialEq)]`, formerly `lib.rs:94:3`) are owned
(`tests/rust/programs/derives.rs`); `thiserror::Error` is a procedural derive
from another crate and stays a semantic gap. `const fn` items and inherent
methods (formerly `lib.rs:104:5`) are ordinary run-time functions
(`tests/rust/programs/const_fn.rs`), and core's `matches!` (formerly `lib.rs:105:9`)
expands to its defining match (`tests/rust/programs/matches.rs`). Helper attributes of
procedural derives (`#[error(..)]`, `#[from]`, formerly `lib.rs:112:3` and
`lock/mod.rs:104:5`) are retained uninterpreted; the `thiserror::Error` derive
itself stays an explicit semantic gap.

```sh
uv run pytest -x -q tests/rust/test_uv_source_frontier.py
```

Native pcc1, owned Cargo/build orchestration, core/alloc/std and the
dependency closure, linking and running uv remain open.
