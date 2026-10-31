# Multi-language implementation status

Published source boundary: `117476b8206a1f99356da415e4692432fab7278f` (2026-10-31). Unpublished work below is **not included in this published boundary**. A later master needs a fresh source/evidence reconciliation; none of these counts certifies an untested merged tree.

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

`117476b8` publishes the pointer/implicit-this member-function slice, including capture-safe lowered names. The final ordinary gate passed **2,005 cases plus 49 subtests**; integration was **655 PASS / four retained reference FAIL / two missing-native ERROR**. This is not combined green. Thirty-two valid dot-receiver method sources remain deferred; constructors/destructors, ordinary dot-receiver calls and heap/object-lifetime support are not implemented by this pointer-only slice.

The four integration failures occur in external G++ 14.2 compilation: two internal C-object-linkage disagreements and the same CWG2394 case in class/platform reference nodes. Their cause is not asserted to be a confirmed GCC bug. The two errors lack `PCC_PLATFORM_TEST_COMPILER` for native project validation; no replacement compiler was silently selected.

The actual public-frontend attempt on the unchanged original TinyXML2 upstream-tests closure now stops at **missing `cstdio`**. Earlier `cctype` evidence is historical, not the current first failure. Constructors/destructors and unsupported member forms are source-inspected successor dependencies; the bounded pointer-member slice does not establish that this original project passed its header frontier. No PCC project object/run or original upstream-test success follows from the bounded fixtures.

Next: the original owned-header frontier, construction/destruction and lifetimes, references/value categories, richer overload/templates, inheritance/virtual dispatch, lambdas, RTTI/EH and owned standard-library closure. Keep original source inputs and object-expression side effects intact.

### Go

Source: `pcc/frontends/go/`; tests: `tests/go/`. Published source covers scalar/control-flow and package graphs, fixed-width signed/unsigned integers, constants/conversions, fixed-array value/storage semantics, multiple/named results, grouped declarations/iota and bounded static strings. Objects use the public IR and owned linker, not an external Go production compiler.

- `253f35a3`: fixed arrays and initialized package storage; 298 whole-Go PASS, including eight official same-source/rejection nodes; 51 full-width owned observations.
- `c62d752f`: named and multiple function results.
- `c9ae58b4`: grouped declarations, lexical constants and immutable static-string backing. **509 whole-Go PASS**, including **33 official-Go nodes**; 116 native receipts, including all 1,024 bytes from the unchanged original table component. The focused selection separately passed 116 tests; tests and raw observations are different counts.
- `c162f2ab` publishes candidate `3b6534c3ac5552c536e5ce143a1e7e9a14ac6c40`: defined/alias type identity, value records/nested fields, package-private fields and copies including 24-byte arguments/results. **594 whole-Go PASS**, including **39 official-reference nodes**; **141 native receipts**, including 25 new record observations. Its focused selection separately passed 85 tests. The earlier **554 PASS / 1 FAIL / 39 unfinished** remains historical; the final fix changed only the expected generic resolved-type diagnostic, not the rejected source. Darwin is cross-only; no standard Go/C aggregate ABI or whole math/bits pass.

- `bbd803bf37ef4b80f47c7fa9369a3cedc928fe7b` publishes direct value-receiver methods on local defined scalar/string/record types, defining-type alias method sets, direct method expressions and sole multiple-result arguments. Receiver snapshots, evaluation order, 24-byte copies, recursion and package visibility are preserved. **642 whole-Go PASS**, including **46 official-reference nodes**; 49 focused PASS separately. **157 full-width native receipts** include 16 new method observations; Darwin remains cross-object only. Neither methods gate failed. Stored method values, pointer/interface/generic receivers and standard Go/C aggregate ABI remain outside this slice.

`117476b8` also publishes ordered expression switches and fixed value-array range. **776 whole-Go PASS**, including **61 official-reference nodes**, with **200 full-width native observations**. The original invalid switch source remains a rejection obligation; the valid observation program is separate. Array snapshots, iteration assignment and constant-length evaluation rules are bounded source semantics, not slices, general range, runtime bounds panics or managed allocation.

The table component plus authored observer is not the whole original `math/bits` package or its tests. The unchanged `bits_errors.go` still fails with `unsupported type error`; error/panic/linkname semantics remain required. Earlier failed declaration/string runs remain historical failures, not retroactively green results.

Next: remaining source control flow and method/type forms, followed by the original package dependencies and runtime-backed strings/slices/interfaces/maps/channels/goroutines. No heap-string, shared-GC, standard external Go aggregate ABI, Mac or native-pcc1 qualification is established.

### Java

Source: `pcc/frontends/java/`; source tests: `tests/java/test_source_*.py`. Source is parsed and semantically checked directly into PCC IR; javac, bytecode and JVM operand-stack IR are not required production stages. Reuse verified integer semantics without reopening the frozen class/JAR track.

Published source includes static methods/calls, supplied multi-unit closures/import resolution, scopes/definite assignment, primitive bool/int/long operations and overloads, constants/characters, initialized final locals/parameters, proven-nonzero integral division and primitive conditional expressions.

`3a5a97b2` adds typed, condition-sensitive ternaries with only the selected arm executed. **782 whole `tests/java` PASS** includes 670 source-prefixed and 112 frozen compatibility nodes (35 reader + 77 bytecode). These are classifications of the same gate, not independently rerun subtotals; compatibility does not count toward source acceptance. The delta has 78 new full-width source-native observations, separately from 156 prior observations. A separate JDK21 lane matched 78 positive originals and 12 invalid-source rejections.

`5268403f` publishes candidate `2ae814acebe8ded73995675c0e50f241dbc2e0b8`: source switch/do and a real case-label definite-assignment false-admission fix. **936 whole-Java PASS** classifies as 824 source-prefixed + 112 frozen compatibility nodes; 60 new full-width source-native originals, plus a separate JDK lane with 60 positive / 37 invalid-source matches. The unchanged offending source remains a regression obligation. The earlier 934 local PASS did not override its failed reference comparison; retain those distinct observations rather than relabeling them. No objects/heap, whole JDK/project, Mac or pcc1 qualification follows.

Published `bb5df18811bc85d5aedaec236f5cd5c432062d7d` adds class/member and initializer semantic models, not allocated instances: 106 focused and **1,042 whole-Java PASS** (930 source-prefixed + 112 frozen compatibility nodes). Separate JDK21 source21/target21 TYPECHECK comparisons matched 40 positives and 49 invalid-source rejections; there are **zero new instance-native observations**. Preserve the earlier 1,039 local passes followed by a real forward-reference false-admission failure; the unchanged offending source is retained as a negative regression.

`117476b8` adds test-only actual raw-process comparisons of six unchanged source programs with PCC and JDK21. The final **1,046 whole-Java PASS** includes frozen compatibility; **six actual raw PCC/JDK pairs** compare stdout, stderr and exit separately. These are existing static-source paths, not instance allocation, constructor execution or heap/GC qualification. Historical normalized-value/typecheck comparisons are not relabeled as raw-stream execution.

The older strict supplementary-character reference divergence remains FAIL; the narrow later reference lane does not supersede it. The original Commons Numbers primes production closure previously stopped at an unsupported string token, emitted no object and did not run its own tests; no successful later whole-project gate is established.

Historical class-file AOT (`00529a78`) and label handling (`ac5a9136`) remain preserved, with unpublished JAR experiments frozen. A synthetic JAR execution or structural PDFBox read is not Java source or ecosystem qualification.

Next: actual allocated-object/member execution beyond the published semantic model, then strings/arrays and shared runtime contracts. Model/typechecking is not native allocated-object execution. Runtime-bound JDK operations need owned implementations; OpenJDK is behavioral/specification reference, not implementation code to translate into PCC.

### Rust

Source: `pcc/frontends/rust/`; tests: `tests/rust/`. The independent source parser/Sema lowers typed scopes, functions/control flow, records/tuples and bounded enum/match forms into public IR. `experimental_rustc_checked=True` remains caller attestation, not proof of an actual rustc run; PCC has no complete borrow checker.

- `d2a01555`: explicit targets and real Darwin cross-object checks, not Mac execution.
- `69155020` / `f69ea411`: value aggregates/move-state checks and bounded unsafe extern-C/readonly repr(C) references. Original Meson adder plus two original C functions executed together through owned objects with a full 12-byte observation; this is not the complete Meson shared-library project.
- `5cec7de7`: unit enums and binding-free match; 360 whole-Rust PASS, with 21 rustc references unavailable at that time.
- `6ea12479`: tuple enum payloads and by-value match bindings; 63 focused and **420 whole-Rust PASS**. Twenty-seven rustc references were unavailable, not passed. Original Option/Result frontier files still failed; no core/alloc success follows.
- `58380044`: constructor early-return/conditional-join regression and explicit unsupported-Drop diagnostic. Only its three new focused tests ran; this was **not** a 423-case whole-domain rerun and cannot stand in for the newer pre-push requirement.
- `1b01a5cf` jointly publishes Rust candidate `5cad0a203808e417938eb9c5e4963a1aeca1be85` and the C++ consumer: **503 PASS = 493 Rust ordinary/integration + 10 shared-core**. Five new generic cases produced full eight-byte Linux native observations and five Darwin cross-objects. Thirty-three rustc reference inputs were unavailable in that run; the historical 503 PASS / 33 reference UNRUN classification remains unchanged.

**Previously observed semantic failure, now fixed in published `42876363c43d6fcd4a8a04ce4a3bcc5172ebe087`:** rustc 1.91.1 exposed `tests/rust/programs/payload_partial_moves.rs`, where PCC admitted matching a whole enum after a field move while rustc rejected it with E0382. The original failing source remains unchanged as a negative regression; its earlier native execution is not Rust qualification. Preserve the old 38 PASS / one FAIL reference lane and 75 focused PASS plus **598 PASS / one FAIL / zero unfinished** whole selection; they are not relabeled by the fix.

The published correction and generic-function batch passed **607 tests = 556 nonreference Rust + 39 actual original-source rustc 1.91.1 metadata/typecheck positives + two exact E0382 rejection comparisons + 10 shared-core**. Independent source/evidence binding verified candidate `689843f9106c93b56266fcd1f4e4ab4f29e44098` before publication. Twenty-five full-width native receipt sets include six new generic-function programs and a separate valid tuple-partial-move program; Darwin evidence is cross-object only. The batch requires an initialized owning enum before discriminant inspection and adds abstract generic-function body checks/concrete recursive instantiation through the unchanged shared registry. Earlier unavailable reference epochs remain UNRUN, not retroactively passed.

`117476b8` publishes bounded inherent `impl` methods and associated functions for local nominal records/enums, `self`/`mut self`, `Self` and direct/value-receiver calls. Consuming receivers move Rust values rather than copying them; bounded generic impls reuse the shared registry. **669 whole-Rust PASS** includes **six actual unchanged-source native PCC/rustc comparison pairs**, distinct from the historical 668-case metadata-only gate. References (`&self`/`&mut self`), traits, autoderef/autoborrow, general inference and complete generics remain outside this slice.

Next priority remains the user's basic source forms: references, arrays, for loops and broader generics, extending the bounded impl-method support rather than claiming it complete. Private enum storage does not promise stable Rust/C aggregate ABI; full borrow checking, core/alloc, Drop, heap/destructors, shared GC, Mac execution and native pcc1 remain open.

## Unpublished work

The bounded C++ pointer members, Rust inherent methods, Go switch/array range and Java semantic-model/raw-reference work named above are published. Later successors remain separate local candidates; no focused/model-only result certifies unexecuted source forms, allocated objects or whole projects. In particular, do not turn the older 503-pass Rust gate into retroactive rustc certification or the Java model into native-instance support.

The C++/Rust shared-core files were integrated once with both real consumers in `1b01a5cf`. Preserve source-only follow-on work, but review and complete final affected-language gates before moving any candidate into the published section.

## Published specification ledger — partial inventory

`117476b8` publishes `scripts/language_feature_ledger.py`, `scripts/conformance_ledger.py`, 59 checker tests, four language feature maps under `specs/features/` and their README. This is a **partial specification inventory**, not a complete language specification, complete official suite or new suite execution. C++ has 101 diagnostic codes mapped by actual frontend AST audit; other stable-code inventories still have explicit gaps.

Each new feature needs specification-chapter-linked unit tests, an actual executable program and an actual same-original-source reference comparison before its ledger status is updated. The only states are `supported`, `explicitly_unimplemented` and `missing`; every unimplemented diagnostic code maps to a feature. The published ledger requires matching revision/mode/platform/backend/GC evidence and raw stdout/stderr/exit for supported runtime behavior; typecheck or normalized-value comparisons do not substitute. Host pcc0 and native pcc1 stay separate, and missing reference tools remain visibly unrun.

Verified baseline exports remain local; the baseline files are **not published**. Metadata/checker passes do not establish full-spec coverage or official-suite success, and aggregate PASS counts must not be expanded into invented test identities.

## Common runtime and interoperability

**Published `5c4ddd2b`: first experimental checked raw allocation/resize/free component.** The exact six-path change passed **two tests**, including **one actual owned-linked Linux x86-64 native executable** and a signature-admission/rejection test. A separate related regression selection passed **60 ordinary tests**; **eight Windows-native cases were unrun on Linux**. Both retained the original limits and six workers. Earlier failed helper-export/CLI-option runs did not reach native execution.

This is host-PCC compilation of current source with owned emission/linking, without a runtime archive. It does **not** qualify production archive integration, native pcc1, Mac/arm64 execution, full allocator coverage, C++ void** consumers/new/delete, Rust Drop, concurrency, managed objects, five-GC/handles or exceptions. Existing allocator/backend providers are unchanged. Further shared runtime/backend batches are **HOLD for the user's Mac QPS assessment**. Production integration of this raw component additionally requires **user-run Mac arm64 validation under all five GC modes and multithreading**; that acceptance is not established here and does not authorize cloud access to the user's Mac. Next local work is the managed-allocation/GC design needed by Go slices/strings and Java objects/arrays; those managed capabilities are **not implemented or qualified** by this raw-storage component.

CFI candidate `64893c10` remains unpublished/held: opt-in Linux unwind-table emission and external throw-through-owned-frames evidence is not an owned unwinder/personality/LSDA or language EH. Its three parent-equal indexed-bridge failures remain visible; the later ABI/errno expansion ended at 184 PASS / 6 FAIL / 411 unfinished under the original resource floor. The 2,264-pass Mach-O-related selection is separate, not a cure for that incomplete gate.

C-API candidate `81d5dec5` also remains held. Its 37 host contract passes do not establish changed-runtime native five-GC behavior. Existing archives cannot certify new runtime sources.

Published `9ef9610f` source interoperability was renewed against exact `822ef7c1` inputs: C++ called Java-source, Go and Rust exports in one owned Linux executable with full 32-byte observations. The source and historical-bytecode nodes each passed separately, with two byte-identical builds/two runs. Only the source node is source-language acceptance. This is scalar interoperability, not shared objects, stable aggregate ABI, callbacks, GC handles, exceptions, suspension or whole projects.

Published test-only commit `d194a664f1a1f4b5e8dcec78262d73a3e49356b3` carries reviewed ABI candidate `ad6dbdbe492035e798397a5cb4c149e7852ceba0`, tested against exact published production `c162f2ab4e213bb17315ba3d390282e9db7fadf4` with four new test/fixture files: `tests/multilanguage/fixtures/record_pointer/caller.cpp`, `tests/multilanguage/fixtures/record_pointer/read_record.rs`, `tests/multilanguage/record_pointer_execution.py` and `tests/multilanguage/test_record_pointer_c_abi.py`. Its complete multilanguage selection passed **4/4**: three Linux-native nodes (Java-source scalar, Java-bytecode compatibility scalar, C++ calling Rust with a readonly stack-record pointer), each executed twice, plus one Darwin arm64 **cross-object-only** node. Six native runs are not six tests; the bytecode node remains excluded from source acceptance. The record observation preserves all 48 bytes, including unchanged before/after field snapshots and full u64 result `18146886964504632207`. This renews only those boundaries; it does not establish Mac execution, by-value aggregate ABI, shared allocation, managed handles/GC, EH or whole projects. At the `d194a664` test publication, production files were identical to the exact tested `c162f2ab` boundary. This four-node gate was not rerun here on the later `42876363`/`bbd803bf` frontend changes.

A separate fresh atomic-candidate gate passed the same **four ABI nodes** and was bound to published `117476b8`: the qualified `pcc/`, `tests/` and `scripts/` subtrees and **1,334 actual gate input identities** match. This is an input-binding verification, **not a post-publication rerun or full-tree-equality claim**; local-only baseline exports and the README caveat differ outside the gate inputs. The result remains three Linux-native nodes (source scalar, historical-bytecode scalar and readonly record pointer) with six actual runs, plus one Darwin arm64 cross-object-only node. No Mac, shared-GC/EH, pcc1, by-value aggregate or whole-project qualification follows; the earlier `c162f2ab` gate remains separate historical evidence.

## Externally supplied Mac results

The user reported native Mac results: Rust built for arm64 returned **33092**; direct Java-source compilation (no javac/bytecode) returned **328350**; C++ command-line Mach-O execution returned **51**, exercising for, initialized arrays, indexed pointers, integer static_cast, overloads, templates and enum comparison. Go had been validated in an earlier round. The user also reported **1,419 frontend host passes and two stale-test failures**.

These are user-supplied results, not Mac executions performed by the cloud worker. Their exact HEAD was not provided, so they do not qualify this page's latest published/candidate snapshots or satisfy the pending Mac QPS review.

## Reference compiler versions and prerequisites

Keep cloud and user-reported Mac reference owners distinct; they are not one interchangeable oracle environment:

| Language | Cloud reference | User-reported Mac reference |
|---|---|---|
| Rust | rustc 1.91.1 | rustc 1.91.1 |
| Go | Go 1.27.3 | Go 1.26.2 |
| Java | JDK 21, `-source 21 -target 21` | javac 17 |
| C++ | G++ 14.2 | clang 20 |

A version being available is not proof that a source was checked or executed. Record the actual compiler/options, original input and source revision for each comparison; version differences can affect diagnostics and accepted language features. Missing prerequisites remain visibly UNRUN/not selected under the existing collection policy, or ERROR when an unmet prerequisite was explicitly selected. Never quietly qualify an absent reference/native/platform lane or replace its owner.

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
- Published feature metadata: `uv run python scripts/language_feature_ledger.py specs/features/*.json --report`; diagnostics audit: `uv run python scripts/language_feature_ledger.py specs/features/cpp.json --audit-diagnostics .` against the exact intended source snapshot. These validate metadata/source mappings, not runtime semantics or a complete official suite.
- Original project fixtures live in `tests/cpp/project_cases.py` / `tests/cpp/test_cpp_projects.py`; TinyXML2 smoke and original upstream-tests are different obligations. The separate bounded upstream-conformance adapter's 11 original translation units failed explicitly; 11 unavailable Clang references were not selected. This is not a passing full compiler/conformance suite.
- One publisher integrates the latest complete remote tree and exact path preimages/modes. Verify published commits before moving candidate rows into the published section. Preserve peer changes; do not force-push, reset shared trees or repeat immutable heavy jobs merely because unrelated master paths advanced.
- Fixed next dependencies remain original project source frontiers, native pcc1 availability, NumPy build/import and five-GC extension end-to-end. Record real failures rather than quietly excluding them; test counts are not completion percentages.

Keep raw logs, credentials, rejected payloads and machine-specific recovery paths out of these fixed source documents. Public source/test identities and reproducible commands orient the next contributor; actual execution is required to renew a claim.
