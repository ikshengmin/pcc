# Multi-language implementation status

Latest executed project baseline: `2dc72b759676649eecea8057218c55ff54be4e31`.
Implementation is driven by the first failure of each unchanged upstream project,
not by feature-test counts. No target below has passed its complete PCC build and
upstream tests. Mainlines compile source directly to PCC IR.

## Project targets

| Language | Target | Current frontier | Next work |
| --- | --- | --- | --- |
| C++17 | TinyXML2 11.0.0, original `xmltest` | Local Linux x86-64 container (g++ 12.2) at the C++ header round: `tinyxml2.cpp` stops at `tinyxml2.h:343` `class MemPoolT : public MemPool`, the class-template base. Ordinary classes have no base-class support yet and virtual functions are base-free only. Owned `<cerrno>`/`<ctime>` move `xmltest.cpp` to the non-standard `<sys/stat.h>`. No executable or upstream test execution. | Single inheritance with polymorphic bases (layout, conversions, lookup, construction/destruction, overriding), then class-template bases; an owned POSIX `<sys/stat.h>`/`<sys/types.h>` surface. Keep the unchanged whole-project gate. |
| Java | Commons Numbers Primes 1.2 | CI rejects the string literal in `Primes.java:31`. All 17 original inputs are intact; production and test dependency closure remains incomplete. | Connect objects/strings through the shared managed model, preserving all original test classes. |
| Go | Go 1.27.3 `math/bits`, whole package and tests | CI rejects predeclared `error` in `bits_errors.go:12`. Whole test execution is unrun; matching reference toolchain is missing. | Implement the shared storage/interface requirements without substituting the bootstrap variant or omitting panic tests. |
| Rust | `fallible-streaming-iterator` 0.1.9 | Local Mac and Linux x86-64 container with an audited rustc 1.91.1 receipt at the closure-syntax round: same-input certification passes; the owned parser now retains every Fn-family bound, unqualified `Some(e)` pattern and closure through line 207 and stops at `lib.rs:208:19` (`for _ in 0..n`, range iterators). Those three constructs are explicit semantic gaps (closure traits, closure types/captures, core Option/Result). No PCC project object. | Range iteration, then the core Option/Result model and closure traits/types; keep the original project gate strict. |

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

[CI run 41592994473](https://github.com/ikshengmin/pcc/actions/runs/41592994473)
tested `2dc72b7` and ended with 44 jobs: 12 successful, 31 failed and one
cancelled. The C++ reference job was cancelled despite retained failing results;
it is not a passed reference lane. No target above passed its complete PCC build
and upstream tests. The older [CI run 41570071093](https://github.com/ikshengmin/pcc/actions/runs/41570071093)
tested `1e0083ea`; neither run validates later commits. The earlier
[bounded passed-test summary](../specs/conformance/summaries/ci-41550908488-passed.json)
is tied only to its recorded revision, test IDs, versions, platform and hashes.

Published CI-diagnostics checkpoint [`80526e2`](https://github.com/ikshengmin/pcc/commit/80526e2a8d58b16d6ebed98490d6ebfd0b50c96e)
aligns the macOS timeout test with the existing 7200-second limit and supplies
checkout imports and early output capture for temporary worker children. Its
original workload, retry, concurrency and cleanup assertions remain intact.
That checkpoint is included in `2dc72b7`; results remain bound to the executed
revision and do not qualify the newer source changes below.

Newer GC source-test pointer providers, C++ copy-reference/lookup repairs, Rust
configuration policy and reference-inventory repairs remain execution-unrun.
The two exact unused-template C++ cases are ill-formed, no diagnostic required:
a reference compiler may diagnose them or accept them without a diagnostic.
Source-only correction [`0b532d6`](https://github.com/ikshengmin/pcc/commit/0b532d6c80f5ac17c69286013b64e4144e91a332)
retains the original inputs and PCC eager rejections while recording either
reference outcome. Reference acceptance is not semantic acceptance. The next
deliberate non-skip validation must execute the changed source.

NumPy source preprocessing stops at generated `_numpyconfig.h` and `stdarg.h`.
orjson yyjson preprocessing/parsing passes; Rust build/import remains blocked.
Their complete owned builds/imports, native pcc1 and full five-GC acceptance
remain open. Latest user direction puts execution in CI, not local test runs.
Keep original failures and reference disagreements; do not weaken assertions,
resource limits or prerequisites. Interim commits use `[skip ci]`; validation
batches are deliberate, not automatically repeated after every commit or failure.
