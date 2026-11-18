# Offline orjson Cargo source store

This directory is separate from the [original bounded 15-file fixture](../orjson-3.13.0/README.pcc.md).
Nothing here changes its 32-file / 2 MiB limits or the CI worker’s 90-second / 1 GiB limits.

* `upstream.tar.gz`: unmodified [official orjson commit archive](https://codeload.github.com/ijl/orjson/tar.gz/bd00937c5c46ab20eb5d7fe1cfc034b906c4578f).
  [Git commit](https://github.com/ijl/orjson/commit/bd00937c5c46ab20eb5d7fe1cfc034b906c4578f) `bd00937c5c46ab20eb5d7fe1cfc034b906c4578f`
  points to tree `48b5e5df86fbfddd828ced45d309069ce985ea46`.
  The archive SHA-256 is `00145316d1931ddfe41d22512c90a96a4533722bf0effc562259af0aa75d0568`.
  All 585 regular-file bytes and Git modes reproduce the independently pinned tree; the commit/tree association
  is a trusted input, not a claim that a tarball contains a Git commit object.
* `registry/<name>-<version>.crate`: all 22 original [crates.io registry](https://crates.io/) packages from
  the original root Cargo.lock, downloaded unchanged from the registry’s static distribution endpoint
  `https://static.crates.io/crates/<name>/<name>-<version>.crate`.
  Each is authenticated against its own Cargo.lock SHA-256 before decompression. Their 1,144 regular files,
  normalized/original manifests, original notices and license texts are retained, not rewritten.
  Package declarations are not a project-wide license. No registry programs execute when reading the store.

The 23 archives total 3,279,116 compressed bytes; their 1,729 files total 14,930,773 bytes. They cover the
upstream Git distribution including test data, plus the packages in its Rust lock, **not** a compiler,
Rust standard-library/sysroot, a Python test environment, a wheel, Maturin, or an authenticated interpreter ABI.

The prerequisite planner uses default features and the explicitly named host and target, not the ambient machine.
For that release library selection, 16 packages and 26 planned units are reachable. Optional packages present in
the lock are not automatically compiled. Host build dependencies, package libraries and host build-script executables
are distinct. Only an actual successful producer can provide generated files, cfg or direct links metadata.
The selected `pyo3-ffi` script has `links = "python"` and calls the real config serializer; its
`cargo:PYO3_CONFIG=...` output would be forwarded to the orjson build script as `DEP_PYTHON_PYO3_CONFIG`.
The library `pyo3-build-config` itself has `build = false`. `target-lexicon` first needs its real generated
`OUT_DIR/host.rs`. The archive/plan provides neither file nor env value.

Remaining obligations include actually executing the original scripts with PCC, a standard library, genuine
owned objects and C/Python ABI, linking and loading the extension, upstream tests, and all five GC modes.
The CI source gate still fails while these remain unqualified.
