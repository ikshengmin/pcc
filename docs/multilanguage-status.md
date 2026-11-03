# Multi-language implementation status

Current source inspected: `a49230d812d91678e9f4fcc33d01fd476698c7fd`.
Implementation is driven by the first failure of each unchanged upstream project,
not by feature-test counts. No target below has passed its complete PCC build and
upstream tests. Mainlines compile source directly to PCC IR.

## Project targets

| Language | Target | Current frontier | Next work |
| --- | --- | --- | --- |
| C++17 | TinyXML2 11.0.0, original `xmltest` | Owned `<cstdio>` exists. Current source rejects the template destructor in `DynArray`; this is source inspection, not a fresh project run. The last CI project nodes lacked the native compiler prerequisite. | Establish the current whole-project host-PCC failure, then fix that failure through the existing template/lifetime owners. |
| Java | Commons Numbers Primes 1.2 | Current source rejects the string literal in `Primes.java:31`; a historical experimental frontend's deeper array failure does not qualify master. | Preserve all production and original test classes, pin test dependencies, and connect required objects/strings through the shared managed model. |
| Go | Go 1.27.3 `math/bits`, whole package and tests | Retained CI confirms rejection of predeclared `error` in `bits_errors.go`. The table component is not a package pass. | Complete the unchanged package/test target, then implement its first interface/runtime requirement. Do not substitute the bootstrap variant or omit panic tests. |
| Rust | `fallible-streaming-iterator` 0.1.9 | Selected dependency-free crate uses `Option`, `Result`, traits and references. Current-source inspection predicts the crate lint attribute as the first parser boundary; no PCC project run yet. | Pin the complete original crate and licenses, establish the first PCC failure, and retain its original tests plus a separate API consumer. |

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
  owned-linked process. The next gate is compiled Python calling all four via
  `pcc.extern` in one executable, followed by buffers, GC handles, then C-API
  extension modules. Java bytecode compatibility remains a separate frozen
  experiment.
- Shared runtime/backend changes retain the single-batch Mac/QPS review gate.
  Do not access the user's Mac or edit the separate Python IR/RC performance line.

## Validation

[CI run 41550908488](https://github.com/ikshengmin/pcc/actions/runs/41550908488)
tested `531f2b0b` and failed overall. Its
[bounded passed-test summary](../specs/conformance/summaries/ci-41550908488-passed.json)
records exact test IDs, versions, platform and evidence hashes; it does not
qualify later commits. Subsequent source-reviewed changes remain execution-unrun.

NumPy/orjson source-frontier jobs are wired into the existing unified workflow.
Their complete owned builds/imports, native pcc1 and full five-GC acceptance
remain open. Latest user direction puts execution in CI, not local test runs.
Keep original failures and reference disagreements; do not weaken assertions,
resource limits or prerequisites. Interim commits use `[skip ci]`; validation
batches are deliberate, not automatically repeated after every commit or failure.
