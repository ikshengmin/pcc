# Multi-language implementation status

Latest executed project baseline: `1e0083ea7e5cbb41f462517714b619c70335b81d`.
Implementation is driven by the first failure of each unchanged upstream project,
not by feature-test counts. No target below has passed its complete PCC build and
upstream tests. Mainlines compile source directly to PCC IR.

## Project targets

| Language | Target | Current frontier | Next work |
| --- | --- | --- | --- |
| C++17 | TinyXML2 11.0.0, original `xmltest` | CI rejects class-template members; source inspection locates the first unsupported declaration at `DynArray::~DynArray`. Owned `<cstdio>` exists. No executable or upstream test execution. | Template D1 source is reviewed but execution-unrun; retain the whole-project gate and continue through its next actual failure. |
| Java | Commons Numbers Primes 1.2 | CI rejects the string literal in `Primes.java:31`. All 17 original inputs are intact; production and test dependency closure remains incomplete. | Connect objects/strings through the shared managed model, preserving all original test classes. |
| Go | Go 1.27.3 `math/bits`, whole package and tests | CI rejects predeclared `error` in `bits_errors.go:12`. Whole test execution is unrun; matching reference toolchain is missing. | Implement the shared storage/interface requirements without substituting the bootstrap variant or omitting panic tests. |
| Rust | `fallible-streaming-iterator` 0.1.9 | Actual same-input rustc certification passes; PCC rejects `warn(missing_docs)` at `lib.rs:16`. External reference builds the original crate and its two compile-only test functions; the separate API consumer passes. No PCC project object. | Certified lint transition is source-reviewed, execution-unrun; keep the original project gate strict. |

Project inputs and tests belong with their runners and source locks. Detailed
feature obligations belong in `specs/features/` and language tests, not this page.

## Shared prerequisites

- The shared managed ABI is an experimental opt-in **GC0-only tuple envelope**,
  not a general type-bitmap object model. Reuse the allocator, roots, slot visitor,
  barriers and relocation owners to design one common object header and type
  descriptor for Go/Java, coordinated with the count-free tracing work. Current
  count-free GC1/2/4 experiments do not establish GC3 or five-GC acceptance.
- Reuse existing virtual threads, carrier threads and channel mechanisms.
  Shared language exceptions/panics need one owned unwinding design; CFI tables
  alone do not establish that design or cross-platform exception support.
- Existing scalar integration is C++ calling Java-source, Go and Rust in one
  owned-linked process. The compiled-Python four-source gate now exists: owned
  provider object checks pass, but all four execution nodes stop at the missing
  verified runtime archive. Same-executable execution remains unrun. Buffers,
  GC handles and C-API extension modules follow this gate. Java bytecode compatibility remains a separate frozen
  experiment.
- Shared runtime/backend changes retain the single-batch Mac/QPS review gate.
  Do not access the user's Mac or edit the separate Python IR/RC performance line.

## Validation

[CI run 41570071093](https://github.com/ikshengmin/pcc/actions/runs/41570071093)
tested `1e0083ea` and completed with 13 successful and 31 failed jobs. No target
above passed its complete PCC build and upstream tests. Later source-reviewed
changes remain execution-unrun. The earlier [bounded passed-test summary](../specs/conformance/summaries/ci-41550908488-passed.json)
is tied only to its recorded revision, test IDs, versions, platform and hashes.

Published CI-diagnostics checkpoint [`80526e2`](https://github.com/ikshengmin/pcc/commit/80526e2a8d58b16d6ebed98490d6ebfd0b50c96e)
aligns the macOS timeout test with the existing 7200-second limit and supplies
checkout imports and early output capture for temporary worker children. Its
original workload, retry, concurrency and cleanup assertions remain intact.
These source-reviewed repairs are execution-unrun; CI 41570071093 does not
qualify them. The next validation must execute the changed source.

NumPy source preprocessing stops at generated `_numpyconfig.h` and `stdarg.h`.
orjson yyjson preprocessing/parsing passes; Rust build/import remains blocked.
Their complete owned builds/imports, native pcc1 and full five-GC acceptance
remain open. Latest user direction puts execution in CI, not local test runs.
Keep original failures and reference disagreements; do not weaken assertions,
resource limits or prerequisites. Interim commits use `[skip ci]`; validation
batches are deliberate, not automatically repeated after every commit or failure.
