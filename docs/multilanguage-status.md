# Multi-language implementation status

Published source boundary: `d194a664f1a1f4b5e8dcec78262d73a3e49356b3` (2026-10-31). Unpublished work below is **not included in this published boundary**. A later master needs a fresh source/evidence reconciliation; none of these counts certifies an untested merged tree.

This fixed, source-oriented entrypoint distinguishes published code, candidates, failures, unrun stages and next dependencies. Read [AGENTS.md](../AGENTS.md) and [the roadmap](multilanguage-roadmap.md), then the actual implementation and tests. It is not a running log, recovery inventory or language-completion score.

## Current rules and evidence boundary

- All four mainlines are **source -> owned parse/Sema -> PCC IR**, reusing owned emission/linking. C++, Rust, Java and Go plus required shared infrastructure are first priority; the owned NumPy extension chain is second, Linux first. Java class-file/JAR compatibility is frozen historical work, excluded from source-language acceptance.
- Before every future push, finish all tests for each affected language on the final candidate. Report ordinary, integration and reference lanes separately, including known failures, missing prerequisites and unfinished nodes. Keep the original 90-second/1-GiB guards and default six workers. Only complete bounded whole-language enumeration may use `-x --maxfail=0` to retain all failure outcomes; other pytest invocations remain fail-fast. Focused examples alone are not the gate.
- Small source implementation and tests continue independently of cleanup. Only heavy gates and shared runtime/backend publication are serialized. The first common raw-storage batch is published; another shared runtime/backend batch, including CFI/C-API candidates, waits for the user's relayed Mac QPS assessment. Do not change workflows or take over old C/legacy-CI cleanup.
- Do not access or run on the user's Mac. Existing user-reported Mac results are external evidence. Separately authorized cloud macOS arm64 CI is a distinct source-labeled lane, currently UNRUN here; its authorization is not user-device access and does not permit workflow changes. Linux native execution and Darwin cross-objects cannot replace Mac execution. External results with unknown HEAD do not qualify newer source.
- Host Python invoking PCC and executing owned native output is not native pcc1. No whole upstream project, owned NumPy build/import, five-GC extension chain or complete language standard has passed here. External G++ executing TinyXML2 is reference evidence, not PCC project support.

## Published implementation

### C++

Source: `pcc/frontends/cpp/`; tests: `tests/cpp/`. Public translation-unit selection enters `CEvaluator.compile_translation_units` for `.cpp` inputs. The C++ frontend owns its semantic checks and reuses owned preprocessing, C AST/lowering where semantics agree, and the existing backend; `cpp/driver.py` is not a separate compiler executable.

Published bounded domains include namespaces/name lookup, nominal classes/data members, enums, aggregate initialization, fixed local arrays and parameters, linkage/static storage, for/continue/increment, and scalar/operator/function-template subsets. They do not constitute complete C++17.

- `822ef7c1`: fixed local arrays, explicit multidimensional initialization, decay/subscript evaluation and adjusted parameters; 79 focused ordinary and 41 integration cases passed, with 82 owned/reference executions.
- `72996eb4` / `450d5e73` / `24578650`: owned cctype declarations, target-derived climits and bounded C-linkage object-pointer signatures.
- `deffe4ae`: C-object identity/linkage and resolved lookup projection. Two stale array-rejection expectations were fixed while retaining the original formerly rejected array source as a positive typed/native obligation. Its whole-domain ordinary result was 1,721 PASS plus 49 subtests; integration was 515 PASS / 4 external-reference FAIL / 2 missing-prerequisite ERROR.
- `91ae83e1`: compound assignments with RHS-before-LHS sequencing and single lvalue evaluation, plus bounded arithmetic casts. Its whole-domain ordinary result was **1,813 PASS plus 49 subtests**; integration was **579 PASS / 4 FAIL / 2 ERROR**, with no unfinished nodes. This is not combined green.
- `1b01a5cf` publishes C++ candidate `d62f68ad89a165e4beb6ecadd2b5336c542652fe` together with the Rust consumer and the same two shared-core paths: do/switch and actual shared instantiation. Its final ordinary gate passed **1,914 = 1,904 C++ + 10 shared-core**, plus 49 subtests. Integration: **635 selected = 629 PASS / the same 4 reference FAIL / 2 missing-native ERROR**, zero unfinished. Fifty new native observations = 18 do + 26 switch + 6 registry cases. This is not combined green or whole-project qualification.

The four integration failures occur in external G++ 14.2 compilation: two internal C-object-linkage disagreements and the same CWG2394 case in class/platform reference nodes. Their cause is not asserted to be a confirmed GCC bug. The two errors lack `PCC_PLATFORM_TEST_COMPILER` for native project validation; no replacement compiler was silently selected.

The actual public-frontend attempt on the unchanged original TinyXML2 upstream-tests closure now stops at **missing `cstdio`**. Earlier `cctype` evidence is historical, not the current first failure. Constructors/destructors and member bodies are source-inspected successor dependencies, not evidence that execution passed the header frontier. No PCC project object/run or original upstream-test success follows from the bounded fixtures.

Next: the original owned-header frontier, construction/destruction and lifetimes, references/value categories, richer overload/templates, inheritance/virtual dispatch, lambdas, RTTI/EH and owned standard-library closure. Keep original source inputs and object-expression side effects intact.

### Go

Source: `pcc/frontends/go/`; tests: `tests/go/`. Published source covers scalar/control-flow and package graphs, fixed-width signed/unsigned integers, constants/conversions, fixed-array value/storage semantics, multiple/named results, grouped declarations/iota and bounded static strings. Objects use the public IR and owned linker, not an external Go production compiler.

- `253f35a3`: fixed arrays and initialized package storage; 298 whole-Go PASS, including eight official same-source/rejection nodes; 51 full-width owned observations.
- `c62d752f`: named and multiple function results.
- `c9ae58b4`: grouped declarations, lexical constants and immutable static-string backing. **509 whole-Go PASS**, including **33 official-Go nodes**; 116 native receipts, including all 1,024 bytes from the unchanged original table component. The focused selection separately passed 116 tests; tests and raw observations are different counts.
- `c162f2ab` publishes candidate `3b6534c3ac5552c536e5ce143a1e7e9a14ac6c40`: defined/alias type identity, value records/nested fields, package-private fields and copies including 24-byte arguments/results. **594 whole-Go PASS**, including **39 official-reference nodes**; **141 native receipts**, including 25 new record observations. Its focused selection separately passed 85 tests. The earlier **554 PASS / 1 FAIL / 39 unfinished** remains historical; the final fix changed only the expected generic resolved-type diagnostic, not the rejected source. Darwin is cross-only; no standard Go/C aggregate ABI or whole math/bits pass.

The table component plus authored observer is not the whole original `math/bits` package or its tests. The unchanged `bits_errors.go` still fails with `unsupported type error`; error/panic/linkname semantics remain required. Earlier failed declaration/string runs remain historical failures, not retroactively green results.

Next: methods and source control flow, followed by the original package dependencies and runtime-backed strings/slices/interfaces/maps/channels/goroutines. No heap-string, shared-GC, standard external Go aggregate ABI, Mac or native-pcc1 qualification is established.

### Java

Source: `pcc/frontends/java/`; source tests: `tests/java/test_source_*.py`. Source is parsed and semantically checked directly into PCC IR; javac, bytecode and JVM operand-stack IR are not required production stages. Reuse verified integer semantics without reopening the frozen class/JAR track.

Published source includes static methods/calls, supplied multi-unit closures/import resolution, scopes/definite assignment, primitive bool/int/long operations and overloads, constants/characters, initialized final locals/parameters, proven-nonzero integral division and primitive conditional expressions.

`3a5a97b2` adds typed, condition-sensitive ternaries with only the selected arm executed. **782 whole `tests/java` PASS** includes 670 source-prefixed and 112 frozen compatibility nodes (35 reader + 77 bytecode). These are classifications of the same gate, not independently rerun subtotals; compatibility does not count toward source acceptance. The delta has 78 new full-width source-native observations, separately from 156 prior observations. A separate JDK21 lane matched 78 positive originals and 12 invalid-source rejections.

`5268403f` publishes candidate `2ae814acebe8ded73995675c0e50f241dbc2e0b8`: source switch/do and a real case-label definite-assignment false-admission fix. **936 whole-Java PASS** classifies as 824 source-prefixed + 112 frozen compatibility nodes; 60 new full-width source-native originals, plus a separate JDK lane with 60 positive / 37 invalid-source matches. The unchanged offending source remains a regression obligation. The earlier 934 local PASS did not override its failed reference comparison; retain those distinct observations rather than relabeling them. No objects/heap, whole JDK/project, Mac or pcc1 qualification follows.

The older strict supplementary-character reference divergence remains FAIL; the narrow later reference lane does not supersede it. The original Commons Numbers primes production closure previously stopped at an unsupported string token, emitted no object and did not run its own tests; no successful later whole-project gate is established.

Historical class-file AOT (`00529a78`) and label handling (`ac5a9136`) remain preserved, with unpublished JAR experiments frozen. A synthetic JAR execution or structural PDFBox read is not Java source or ecosystem qualification.

Next: real class/member/object behavior, then strings/arrays and shared runtime contracts. Model/typechecking is not native allocated-object execution. Runtime-bound JDK operations need owned implementations; OpenJDK is behavioral/specification reference, not implementation code to translate into PCC.

### Rust

Source: `pcc/frontends/rust/`; tests: `tests/rust/`. The independent source parser/Sema lowers typed scopes, functions/control flow, records/tuples and bounded enum/match forms into public IR. `experimental_rustc_checked=True` remains caller attestation, not proof of an actual rustc run; PCC has no complete borrow checker.

- `d2a01555`: explicit targets and real Darwin cross-object checks, not Mac execution.
- `69155020` / `f69ea411`: value aggregates/move-state checks and bounded unsafe extern-C/readonly repr(C) references. Original Meson adder plus two original C functions executed together through owned objects with a full 12-byte observation; this is not the complete Meson shared-library project.
- `5cec7de7`: unit enums and binding-free match; 360 whole-Rust PASS, with 21 rustc references unavailable at that time.
- `6ea12479`: tuple enum payloads and by-value match bindings; 63 focused and **420 whole-Rust PASS**. Twenty-seven rustc references were unavailable, not passed. Original Option/Result frontier files still failed; no core/alloc success follows.
- `58380044`: constructor early-return/conditional-join regression and explicit unsupported-Drop diagnostic. Only its three new focused tests ran; this was **not** a 423-case whole-domain rerun and cannot stand in for the newer pre-push requirement.
- `1b01a5cf` jointly publishes Rust candidate `5cad0a203808e417938eb9c5e4963a1aeca1be85` and the C++ consumer: **503 PASS = 493 Rust ordinary/integration + 10 shared-core**. Five new generic cases produced full eight-byte Linux native observations and five Darwin cross-objects. Thirty-three rustc reference inputs were unavailable in that run; the historical 503 PASS / 33 reference UNRUN classification remains unchanged.

**Known semantic failure:** a subsequent actual rustc 1.91.1 reference run exposed `tests/rust/programs/payload_partial_moves.rs`: after moving a field, PCC admits matching the whole enum again, while rustc rejects it with E0382. The original source is preserved. The actual reference lane ran 39 original positive inputs: 38 PASS / this one FAIL. A newer generic-functions candidate separately passed 75 focused cases and reached 598 PASS / 1 FAIL / zero unfinished in its 599-node whole selection; the failed selection is not relabeled by later fixes. Local correction candidate `689843f9106c93b56266fcd1f4e4ab4f29e44098` subsequently passed its complete 607-case gate, including 39 actual positive rustc metadata/typecheck comparisons and two E0382 negative comparisons; independent evidence-binding review remains pending. That correction is **unpublished** at this page's boundary: the published false admission remains open until the reviewed fix lands. This is newly observed in the published payload behavior, not an attributed new regression without a parent comparison; installing the oracle does not retroactively pass the 33 earlier unrun references.

Next: close this move-state false admission, then generic functions, traits/impls/inference, full moves/borrows, macros, core/alloc and owned intrinsics. Private enum storage does not promise stable Rust/C aggregate ABI. No Drop, heap/destructor, shared-GC or native-pcc1 qualification follows from these fixtures.

## Unpublished work

Later generic-functions, class/member-model and method/control-flow batches remain separate local candidates, not published features at the boundary above. Their focused or model-only results do not certify source semantics outside their corpus, allocated-object execution, whole projects or this published tree. The newly observed Rust false admission and its locally validated but unpublished correction are described above; do not broaden the older 503-pass claim into rustc certification.

The C++/Rust shared-core files were integrated once with both real consumers in `1b01a5cf`. Preserve source-only follow-on work, but review and complete final affected-language gates before moving any candidate into the published section.

## Common runtime and interoperability

**Published `5c4ddd2b`: first experimental checked raw allocation/resize/free component.** The exact six-path change passed **two tests**, including **one actual owned-linked Linux x86-64 native executable** and a signature-admission/rejection test. A separate related regression selection passed **60 ordinary tests**; **eight Windows-native cases were unrun on Linux**. Both retained the original limits and six workers. Earlier failed helper-export/CLI-option runs did not reach native execution.

This is host-PCC compilation of current source with owned emission/linking, without a runtime archive. It does **not** qualify production archive integration, native pcc1, Mac/arm64 execution, full allocator coverage, C++ void** consumers/new/delete, Rust Drop, concurrency, managed objects, five-GC/handles or exceptions. Existing allocator/backend providers are unchanged. Further shared runtime/backend batches are **HOLD for the user's Mac QPS assessment**.

CFI candidate `64893c10` remains unpublished/held: opt-in Linux unwind-table emission and external throw-through-owned-frames evidence is not an owned unwinder/personality/LSDA or language EH. Its three parent-equal indexed-bridge failures remain visible; the later ABI/errno expansion ended at 184 PASS / 6 FAIL / 411 unfinished under the original resource floor. The 2,264-pass Mach-O-related selection is separate, not a cure for that incomplete gate.

C-API candidate `81d5dec5` also remains held. Its 37 host contract passes do not establish changed-runtime native five-GC behavior. Existing archives cannot certify new runtime sources.

Published `9ef9610f` source interoperability was renewed against exact `822ef7c1` inputs: C++ called Java-source, Go and Rust exports in one owned Linux executable with full 32-byte observations. The source and historical-bytecode nodes each passed separately, with two byte-identical builds/two runs. Only the source node is source-language acceptance. This is scalar interoperability, not shared objects, stable aggregate ABI, callbacks, GC handles, exceptions, suspension or whole projects.

Published test-only commit `d194a664f1a1f4b5e8dcec78262d73a3e49356b3` carries reviewed ABI candidate `ad6dbdbe492035e798397a5cb4c149e7852ceba0`, tested against exact published production `c162f2ab4e213bb17315ba3d390282e9db7fadf4` with four new test/fixture files: `tests/multilanguage/fixtures/record_pointer/caller.cpp`, `tests/multilanguage/fixtures/record_pointer/read_record.rs`, `tests/multilanguage/record_pointer_execution.py` and `tests/multilanguage/test_record_pointer_c_abi.py`. Its complete multilanguage selection passed **4/4**: three Linux-native nodes (Java-source scalar, Java-bytecode compatibility scalar, C++ calling Rust with a readonly stack-record pointer), each executed twice, plus one Darwin arm64 **cross-object-only** node. Six native runs are not six tests; the bytecode node remains excluded from source acceptance. The record observation preserves all 48 bytes, including unchanged before/after field snapshots and full u64 result `18146886964504632207`. This renews only those boundaries; it does not establish Mac execution, by-value aggregate ABI, shared allocation, managed handles/GC, EH or whole projects. The new tests are now published; production files remain identical to the exact tested `c162f2ab` boundary.

## Externally supplied Mac results

The user reported native Mac results: Rust built for arm64 returned **33092**; direct Java-source compilation (no javac/bytecode) returned **328350**; C++ command-line Mach-O execution returned **51**, exercising for, initialized arrays, indexed pointers, integer static_cast, overloads, templates and enum comparison. Go had been validated in an earlier round. The user also reported **1,419 frontend host passes and two stale-test failures**.

These are user-supplied results, not Mac executions performed by the cloud worker. Their exact HEAD was not provided, so they do not qualify this page's latest published/candidate snapshots or satisfy the pending Mac QPS review.

## Reproduction and takeover

Commands select tests only; run them under the existing watchdog/resource policy and any required shared performance lock. Do not remove default xdist or raise the original limits. Run both selections for each affected language before push:

```bash
uv run pytest -x --maxfail=0 tests/cpp -m 'not integration'
uv run pytest -x --maxfail=0 tests/cpp -m integration
uv run pytest -x --maxfail=0 tests/go -m 'not integration'
uv run pytest -x --maxfail=0 tests/go -m integration
uv run pytest -x --maxfail=0 tests/java -m 'not integration'
uv run pytest -x --maxfail=0 tests/java -m integration
uv run pytest -x --maxfail=0 tests/rust -m 'not integration'
uv run pytest -x --maxfail=0 tests/rust -m integration
```

- Inspect collected nodes and prerequisites first. Explicit C++ reference selection uses `PCC_CPP_ORACLE`; native project tests require `PCC_PLATFORM_TEST_COMPILER`. Official Go references use their existing explicit compiler/cache prerequisites; Rust references require actual rustc availability. Never silently replace an execution owner.
- Ordinary/integration markers and execution ownership are distinct: an ordinary node can execute host-PCC-owned native output. Report reference positives, invalid-source rejection comparisons, IR/model checks, cross-objects, owned native and pcc1 separately. Java's frozen compatibility nodes may remain regression checks but are excluded from source acceptance.
- Only the complete bounded whole-language selections above use `-x --maxfail=0`, retaining every failure without changing original guards or default workers. Other invocations remain fail-fast with `-x`; a first failure is not completion of a remaining domain. Do not create new exclusions or call unfinished nodes passes. Existing collection-time prerequisite deselection is UNRUN/not selected, not a semantic success; an unmet selected prerequisite remains ERROR.
- Source scalar-interop selection: `uv run pytest -x tests/multilanguage/test_scalar_c_abi.py::test_cpp_calls_java_source_go_rust_in_one_owned_process -m integration`. The bytecode node is historical compatibility, not an alternate source path.
- Full published multilanguage selection: `uv run pytest -x tests/multilanguage -m integration`. Record the production revision and the reviewed test-input identity separately; the Darwin record node checks objects only, not Mac execution.
- Original project fixtures live in `tests/cpp/project_cases.py` / `tests/cpp/test_cpp_projects.py`; TinyXML2 smoke and original upstream-tests are different obligations. The separate bounded upstream-conformance adapter's 11 original translation units failed explicitly; 11 unavailable Clang references were not selected. This is not a passing full compiler/conformance suite.
- One publisher integrates the latest complete remote tree and exact path preimages/modes. Verify published commits before moving candidate rows into the published section. Preserve peer changes; do not force-push, reset shared trees or repeat immutable heavy jobs merely because unrelated master paths advanced.
- Fixed next dependencies remain original project source frontiers, native pcc1 availability, NumPy build/import and five-GC extension end-to-end. Record real failures rather than quietly excluding them; test counts are not completion percentages.

Keep raw logs, credentials, rejected payloads and machine-specific recovery paths out of these fixed source documents. Public source/test identities and reproducible commands orient the next contributor; actual execution is required to renew a claim.
