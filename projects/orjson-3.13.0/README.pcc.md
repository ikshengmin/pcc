# orjson: locked Rust/Python-extension source frontier

**Target:** the original orjson Python-extension chain, alongside NumPy's C/C++
extension chain. uv remains a separate future Rust executable target.

This bounded fixture is official **orjson 3.13.0**, pinned to
[bd00937c5c46ab20eb5d7fe1cfc034b906c4578f](https://github.com/ijl/orjson/tree/bd00937c5c46ab20eb5d7fe1cfc034b906c4578f).
The official [release](https://github.com/ijl/orjson/releases/tag/3.13.0) was
published on 2026-10-07. Files were read through the official GitHub contents API
at the immutable commit, not moving master. This is not a downgrade to fit an
older reference compiler and is not an ongoing "latest release" claim.

## Original identities and license boundaries

pcc-source-lock.json preserves 15 complete original files, 810,604 bytes, with
original relative paths, Git blob IDs, SHA-256, byte counts, immutable URLs and
per-file license evidence. It includes:

- Original Cargo.toml, Cargo.lock, pyproject.toml, build.rs and src/lib.rs.
- Complete vendored yyjson.c and yyjson.h; the header declares **0.12.0**.
  Their identity is the copy in this orjson commit, not an assumed yyjson tag.
- Original Python wrapper, test_api.py, test_roundtrip.py, test/util.py and
  test requirements. These are acceptance inputs, not an executed test subset.
- All three original license texts. Package license is **MPL-2.0 AND
  (Apache-2.0 OR MIT)**, not merely MIT/Apache. Rust roots and the Python wrapper
  are MPL-2.0 by their own SPDX headers; test_api.py is Apache-2.0 OR MIT;
  yyjson carries its original MIT notices. No license headers were removed.

Files without their own SPDX notice retain the project-declaration provenance
rather than an invented per-file SPDX statement. Cargo.lock dependency hashes
do not establish dependency source availability or dependency license clearance.

This is **not** the complete Rust module/crate/dependency/test-data closure.
Original upstream Python tests live under projects/orjson-3.13.0, outside
tests/rust discovery and the repository's pytest testpaths. In particular,
test_roundtrip.py depends on omitted data fixtures and its own needs_data
marker; skipped missing-data tests must never become a passing upstream suite.

## Actual upstream requirements

The pinned release itself requests Rust **1.95**, edition **2024**, resolver 3
and a cdylib. Cargo.lock pins **pyo3-ffi 0.29.3** and
**pyo3-build-config 0.29.3**. pyproject.toml selects maturin>=1,<2.

The earlier PCC reference **rustc 1.91.1 is below this MSRV**. The report labels
that historical mismatch without probing or claiming which toolchain is
currently installed. This runner never provisions or invokes a reference
compiler, silently downgrades orjson, or supplies fake certification.

orjson uses the low-level PyO3 FFI/configuration components, not the high-level
pyo3 dependency. No full PyO3 family, macro ecosystem or ABI qualification can
be inferred from this target.

Original build.rs reads real interpreter/ABI configuration through
pyo3_build_config::get(), selects CPython/Python-version/limited-ABI cfg, emits
target-specific configuration and invokes cc::Build for yyjson. The six C
defines in the source lock are copied from those exact source declarations;
the depth limit remains 1024. Reading those declarations is **not executing or
replacing build.rs**, implementing Cargo, or proving a correct PCC ABI config.

## Source-inspected PCC boundary

The original frontier's relevant source files were inspected at
46b65d239d8df28989925f9f19df5d0da3d8d8bd; their Rust/C/guard source was unchanged
from 990156399c22bb93ba54a1dba9accc648f198c39. This edition-bound successor
requires the public Rust API's explicit `edition=` option; it does not silently
retry against an older API or fall back to the default edition. Each CI run
still records its actual PCC source identity rather than reusing that baseline.

- pcc/frontends/rust/crate_context.py provides bounded supplied-source parsing,
  explicitly not Cargo, feature/cfg selection, external crates, macros,
  prelude synthesis or owned language-item registration.
- Public compile_rust accepts a source string and explicit edition identity,
  not a complete Cargo crate context. The runner passes `edition=2024` at its
  actual admission gate, without a checking flag or constructed token;
  unchecked input remains rejected. Selecting an edition does not establish
  its complete semantics, genuine borrow admission, or project qualification.
- Original core root tests retain the no_core/owned-context frontier. An
  original core/alloc/std closure and owned Cargo/build-script/procedural-macro
  execution are not established. A lock containing proc-macro dependencies
  does not prove they are active under the default feature selection.
- The C lane uses PCC's owned Preprocessor and normal native parser factory,
  the original complete yyjson pair, and the existing PCC-owned platform
  headers. It checks that no alternate system-header root was selected.
  Include search uses a fresh snapshot containing only the two frozen C files;
  missing, added, modified or symlinked snapshot inputs fail closed, so an
  unpinned math.h/stdio.h cannot shadow the claimed owned platform headers.
  C preprocessing is normal language processing, not editing the originals.
  No generated package headers, source extraction, fake C API or external
  compiler is introduced.

No compiler/runtime/backend implementation is changed by this fixture/runner.

## Stages and CI-only contract

All new code execution, compiler/import checks and tests are **UNRUN until CI**.
Only source inspection, immutable-file materialization and metadata hashing were
performed while authoring.

The standalone command for the authorized unified CI action is:

    CI=true python scripts/ci_orjson_source_frontier.py --output /tmp/orjson-source-frontier

The output must be a fresh directory. Shared helpers reuse the existing
90-second / 1-GiB process-tree watchdog and preserve a 1-GiB free-disk floor.
The worker records PCC revision, host identity, source/header/driver hashes,
original input hashes, exact invocation and raw resource/stdout/stderr evidence.
Compiler and source identities are checked again after all attempted stages.

The measured source frontiers are deliberately narrow:

1. Original src/lib.rs and build.rs parsing with explicit edition 2024.
2. Their public compile_rust admission attempts with explicit `edition=2024`,
   no attestation and no external checker. The API's explicit
   overflow-checks-off option is recorded
   as an admission-only diagnostic configuration, not acceptance of Cargo's
   dev/release arithmetic or panic profile.
3. Whole original yyjson C preprocessing/parsing using the six source-declared
   build.rs defines and owned platform headers. Preprocessed output, when
   produced, is separately hashed; original source bytes remain unchanged.

UNSUPPORTED means an explicit capability diagnostic; other diagnostics remain
FAIL. Unexpected exceptions are harness failures, not expected capability
successes. Object emission is separately UNRUN: this lane stops at C parsing,
and its explicit Rust edition option establishes neither a complete crate
context nor genuine checked-source admission. Report schema 2 records both
parser and public-API edition options as integers; it does not mark either
attempt successful before the guarded worker actually executes it.

The report keeps each later requirement separate and **UNRUN**:

- Owned Cargo graph, real build.rs execution, procedural-macro reachability and
  execution, core/alloc/std, complete Rust/C objects and full build.
- Shared extension link and Python C-API/load. CPython ABI cfg must not be
  fabricated to label an incompatible PCC runtime as CPython.
- Actual PCC-native import, original dumps and loads behavior, and the complete
  upstream test suite with dependencies and data.
- Five separate GC backends 0–4, including real extension lifecycle/roots and
  ownership evidence; five launches are not a lifecycle qualification.
- External reference compilation/import/tests, which cannot satisfy owned PCC
  production acceptance even when an adequate toolchain becomes available.

frontier.json is an evidence report, not a success waiver. Exit 1 means complete
orjson acceptance is not established, even when report generation completes.
Exit 2 means integrity/resource/harness failure. There is intentionally no
whole-project green result while the complete owned chain is unavailable.
Metadata/harness tests passing only establish their own contracts. No workflow
is added, no original test is run, and no wheel/binary is downloaded or executed.
