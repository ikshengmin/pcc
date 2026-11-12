# Multi-language implementation status

Latest executed project baseline: `2dc72b759676649eecea8057218c55ff54be4e31`.
Implementation is driven by the first failure of each unchanged upstream project,
not by feature-test counts. No target below has passed its complete PCC build and
upstream tests. Mainlines compile source directly to PCC IR.

## Project targets

| Language | Target | Current frontier | Next work |
| --- | --- | --- | --- |
| C++17 | TinyXML2 11.0.0, original `xmltest` | Local Mac and Linux x86-64 container: owned `<cerrno>`/`<ctime>` move `xmltest.cpp` to the non-standard `<sys/stat.h>`. Single non-virtual bases of non-polymorphic classes now work (layout, lookup, access, conversions); `tinyxml2.cpp` still stops at `tinyxml2.h:343`, `MemPoolT`, a class template whose base `MemPool` is polymorphic with declared special members. No executable or upstream test execution. | Base subobject construction/destruction, polymorphic bases and overriding, then class-template bases; an owned POSIX `<sys/stat.h>`/`<sys/types.h>` surface. Keep the unchanged whole-project gate. |
| Java | Commons Numbers Primes 1.2 | Local Mac and Linux x86-64 container: non-throwing primitive local arrays lower onto the shared GC0 managed envelope; throw statements and class creation parse with explicit sema gaps. The production compile now stops at the `List<Integer>` result type of `primeFactors`, `Primes.java:108:19`. PCC native execution of managed arrays is unrun (the runtime-free Java executable cannot link the owned runtime archive). All 17 original inputs are intact. | Generic reference types, exceptions, class initialization for `SmallPrimes.PRIMES`, and an owned runtime link path for managed Java programs. |
| Go | Go 1.27.3 `math/bits`, whole package and tests | Local Mac and Linux x86-64 container at the runtime-owned-linkname round: the `//go:linkname` error variables are references to runtime-owned storage (no package global); the package compile moves from `bits_errors.go:12:5` to the first value use, `panic(overflowError)` at `bits.go:507:9`. The Go 1.27.3 reference is installed locally; 7 reference tests expect diagnostic text older than Go 1.27.3. | Owned runtime error objects and interface values, then panic semantics; keep panic tests and the official reference strict. |
| Rust | `fallible-streaming-iterator` 0.1.9 | Local Mac and Linux x86-64 container with an audited rustc 1.91.1 receipt: certification passes; the owned parser retains Fn-family bounds, `Some(e)` patterns and closures (explicit semantic gaps) and iterates integer ranges, then stops at `lib.rs:306:6` (lifetime parameters on an impl). No PCC project object. | Impl lifetime parameters, then the core Option/Result model and closure traits/types; keep the original project gate strict. |

Package targets pinned for test-driven work (docs/python-native-extension-languages.md): `projects/zxing-3.5.4` (Java Reed-Solomon; first blocker `GenericGF.java:162:3`, an `@Override` annotation) and `projects/go-enry-2.9.6` (Go tokenizer slice; first blocker `tokenize.go:16:23`, slice storage), alongside the existing orjson (Rust `lib.rs:73:6`, `macro_rules!`) and NumPy targets.

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
