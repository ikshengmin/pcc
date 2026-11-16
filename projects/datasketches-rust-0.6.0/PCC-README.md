# Apache DataSketches Rust 0.6.0: unchanged Rust project

Part of the Apache DataSketches set pinned for PCC (scripts/datasketches_sources.py):
the same sketch library in C++, Rust, Go and Java plus its Python package, so
each PCC frontend compiles one algorithm suite and the results can be compared
for performance against each language's own toolchain.

`pcc-source-lock.json` pins the release tag archive (https://codeload.github.com/apache/datasketches-rust/tar.gz/refs/tags/0.6.0) at commit
`675e3e123fda6da19657d345038252b16adc2ebb`: every member is unchanged and its Git blob ID was checked
against the commit tree (scripts/pin_datasketches.py reproduces the lock byte
for byte). Paths upstream marks export-ignore are listed, not fetched.
License: Apache-2.0 (LICENSE, NOTICE and the original source headers).

Dependencies are recorded in the lock and are not present: the library has none; its tests need googletest 0.14.3 and insta 1.48.0 (crates.io); the workspace's benchmarks, tests-integration and xtask crates need further crates.io packages.
No package manager, network fetch, generated source or substitute exists.
projects/.gitattributes keeps the upstream bytes (no text or whitespace rewriting).

`tests/test_datasketches_projects.py` verifies every byte and drives the work.

First observed blocker (local Mac): the 103-file crate, parsed with every sketch feature
enabled, stops at `datasketches/src/lib.rs:63:4`, `#![deny(missing_docs)]`: lint semantics
need an actual rustc check, and multi-file crates have no actual-check path yet.
