# fallible-streaming-iterator 0.1.9

The complete seven-file upstream tree is unchanged at
[`9217bc5e381b54b4ef4c38959488a5dc993b7b81`](https://github.com/sfackler/fallible-streaming-iterator/tree/9217bc5e381b54b4ef4c38959488a5dc993b7b81)
(`v0.1.9`). `pcc-source-lock.json` pins every original Git blob and SHA-256.
Original MIT and Apache-2.0 licenses are included. `.circleci/config.yml` is
inert upstream source, not PCC workflow configuration and is never executed.

The default crate is edition 2015, no_std, has no Cargo dependencies or build
script, and does not enable its optional `std` feature. Its single 21,146-byte
library uses real Option/Result, traits and references, including associated
types, default methods and closure-based adapters. Owned core and cross-crate
linking are still required; no library is extracted or replaced with a stub.

All new execution is **UNRUN pending CI**. No native PCC project pass is claimed.
The actual first parser failure is recorded by this existing-limit entry:

```sh
CI=true uv run --frozen --no-sync python scripts/ci_fallible_streaming_iterator.py \
  --output "$RUNNER_TEMP/fallible-streaming-iterator-pcc"
```

It retains the 90-second process-tree and 1-GiB limits and returns nonzero while
the project is incomplete. Missing audited rustc prerequisites in the following
tests are UNRUN, never a substitute caller flag or a passed project:

- `tests/rust/test_fallible_streaming_iterator_project.py::test_actual_same_original_certification_then_pcc_object`
  actually checks the same original library, then requires PCC object emission.
  A current frontend rejection fails the test rather than satisfying it.
- `tests/rust/test_fallible_streaming_iterator_project.py::test_original_package_self_checks_and_real_api_native_reference`
  separately uses the audited rustc to compile the unchanged library and its
  original cfg(test) object-safety checks, then executes the separate authored
  API consumer in `tests/rust/project_consumers/fallible_streaming_iterator.rs`.
  Upstream has no runtime `#[test]` cases. The consumer checks values, errors,
  exhaustion, mutable borrowing and adapters, with an eight-byte result.

The existing guarded Rust CI integration/reference lanes discover these tests.
The reference commands retained in the test are:

```text
<audited-rustc> --edition=2015 --target=x86_64-unknown-linux-gnu -Coverflow-checks=off --emit=metadata --crate-type=lib --crate-name=certified_input <unchanged-original.rs> -o <checked.rmeta>
<audited-rustc> --edition=2015 --target=x86_64-unknown-linux-gnu -Coverflow-checks=off -Cpanic=abort --crate-name=fallible_streaming_iterator --crate-type=rlib <unchanged-src/lib.rs> -o <library.rlib>
<audited-rustc> --edition=2015 --target=x86_64-unknown-linux-gnu -Coverflow-checks=off -Clinker=<reference-cc> --crate-name=fallible_streaming_iterator --test <unchanged-src/lib.rs> -o <upstream-self-test>
<upstream-self-test>
<audited-rustc> --edition=2015 --target=x86_64-unknown-linux-gnu -Coverflow-checks=off -Cpanic=abort -Clinker=<reference-cc> --extern fallible_streaming_iterator=<library.rlib> <consumer.rs> -o <reference-consumer>
<reference-consumer>
```

Those reference artifacts never enter PCC. PCC consumes the actual
`CheckedRustSource` for the unchanged library and calls `compile_rust` with
edition 2015, the same target and overflow policy, followed by `emit_object`.
Logs and receipts are written only to the selected external output directories.
