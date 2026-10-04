# PCC current status

Updated 2026-10-04 at 15:25 UTC. None of the ten requested goals is fully qualified. Results below belong to their stated source; the new joined source still requires its own matched runtime and original execution gates.

## Verified synchronization and recovery

The latest completed local synchronization is PCC `e1e360ae325a84233ba88be11350499c3fb4eeea`, gateway `05fe10b57897dfcff19a75b41b7c9630436b32d8`, and GUI `6c61696c2ca54f4afd3f53c60f4e8a2e8bd7e3a3`. It applied the 13:01:19 capture: 54 PCC paths, with all 2,950 packet checksums, before/after hashes, modes and diff-check verified. No local development, tests or push occurred.

The applied packet is Library `libfile_e19354430f148191990d6fbfcdbc55ca`, version 1, SHA256 `de5e773c3b11d177f50c45590d5c40912482e84a002772ff16121f83838dc66f`. Source manifest: `61aa09dd148c6ebbba835b50dd2e6b02e19f0437b254d38f2e0f5cb0c3cb5579`. Restore the self-contained 09:18 backup `libfile_0b5da8dbb25c81919b7de213f74fac3c` version 1, then the 10:52 packet `libfile_1fd3f36fa2508191a9982ea45b42f7f7` version 1 and this packet in order.

Cloud development and recoverable source backups continue. Local synchronization is every two hours, with one change-based commit per changed repository. Push and the later CI repair loop require both the ten-goal prerequisite and an explicit future instruction to start.

## Current joined source awaiting qualification

The next candidate joins managed stored-method/interception results, coroutine throw/close protocol state, C frame lifetime accounting and owned C test execution, nine Linux POSIX exports, ordinary managed TemporaryDirectory objects, and streaming UTF-8/ASCII text decoding with structured UnicodeDecodeError payloads. Strict root validation and native archive provenance remain enabled. The text-file and exception payload layouts changed; this candidate requires a complete matched runtime rebuild.

The first four joined batches passed 325 bounded host tests. The later TempDir and codec batches have component model, owned IR and object receipts; those are not native behavior proof. A TempDir Path-containing single-file compile initially emitted a capability stub, so its successful compiler exit is explicitly excluded as a semantic pass. The unchanged whole-compiler class-schema test, original async file, gateway execution, original coroutine failures, stored-method controls and five-GC cases remain required on the final source.

The original string-subclass format failure remains unresolved. General dynamic interception, coroutine warning/traceback parity, multithreaded fork lifecycle, non-Linux file/codec behavior and the whole compiler graph remain unqualified. Method-selection/factory alternatives with unsafe ownership remain outside the candidate with actual source bytes preserved.

## Exact 13:01 validation and compiler graph

Precommit host/object validation attempted 2,144 nodes: 2,123 PASS, 18 FAIL, two original SKIP and one resource-incomplete node. Another 404 native/heavy/integration nodes were deferred. Fourteen failures reproduced on 10:52; four expanded extern-string assertions included one introduced managed-result alias-spelling failure. The source-bound phase-reconciled terminal receipt is authoritative; the raw interrupted JUnit aggregation missed one earlier PASS. Full C/Python collection selected 27,062 nodes and gate-deselected 225, with zero errors/skips. Integration collection selected 5,602 nodes. Collection is not execution.

Fresh V12 executed all 443 original compiler modules: 421 PASS and 22 frontend failures, with no timeout, crash or resource stop. It recovered yacc, tarfile and zipfile, but regressed pipeline_runtime_archive, pipeline_self_backend_cache, driver.project and pathlib at the newly enforced open options boundary. Eighteen previous failures remained. The diagnostic took 1,697.7 seconds and peaked at 3.13 GB. Its 22 failures still block pcc1. Terminal source/input proof is Library `libfile_c4b82c2e986c81919afbffc1a7dff58e`, version 0; complete recovery index is `libfile_e3ddc2793cb88191abd7008e50d375bf`, version 0.

The recurring resource-incomplete node was `tests/python/test_py_class_export_schema.py::test_pcc_cross_module_class_schema_matches_local_layout`. Its unchanged full-compiler workload exceeded the 512 MiB host envelope. A diagnostic replay under the existing 4 GiB compiler budget terminated normally after 194.10 seconds with an actual codegen failure: `slot-call CPython name requires an output-slot bridge: temp_dir`, in `pcc.package.install::_build_requirement_tool_wrappers`. Peak process-tree RSS was 2,220,138,496 bytes: 2,046,103,552 in the worker and 174,034,944 in the parent. Schema assertions were not reached. Raising the envelope exposed the defect; it did not fix product memory or certify the test. Proof: Library `libfile_6d7e9274b2ac8191862c3acc9a3b2e05`, version 0.

## Exact 13:01 native execution

The same compiler built and strictly admitted an owned Linux x86_64 runtime with 186 members, threads enabled and atomic refcount. Archive SHA256: `00ea9db17f218bd2565ff4a8953d665b604672c93378296a563dad44f555c607`. All seven preflights passed. Build time was 443.82 seconds with 458,530,816 bytes peak tree RSS during other bounded work; this is not performance acceptance. Runtime recovery is Library `libfile_1ffafd433ea081919b0880157d4f006a`, version 0.

All 16 unchanged original async tests compiled and executed: 16 PASS. Three original repaired controls also passed: nullable import cleanup, builtin-open/context lifetime, and context-entry rebinding. Open/context variants witnessed actual GC0–4 collections; nullable requested settings without collections are not equivalent witnesses. Proof archives are `libfile_f4b29264ecf4819182eb6a7cc013442d`, `libfile_8680352418048191865f8419e5702c1e`, and `libfile_9da433f7c3ec8191ae82b12aceca7533`, all version 0.

Gateway outcomes were 390 PASS, 15 FAIL, one attempted resource-incomplete dashboard compile and four later nodes not admitted. Fourteen failures require missing qualified pcc1; one fails owned linking on nine POSIX exports. The dashboard advanced beyond DNS but exceeded its 300-second compilation envelope. Of the 390 passes, 388 are host/reference checks and two are owned C socket executions. No PCC Python live-network gateway example completed in this run. The earlier 10:52 total of 389 passes similarly included 388 host/reference checks and one compiled structured-scope example. Neither count is a native gateway qualification. Terminal proof: Library `libfile_9a42a450af548191b286a9c897c79ded`, version 0.

## Storage cleanup

User-authorized cleanup retired obsolete completed builds, successful generated IR and their older local archive copies, while preserving current source, the current runtime, C continuation inputs, needed failing reproductions and compact provenance. The latest batch removed 2,184,773,632 allocated bytes of V9/V10 generated-output archive parts after verifying hashes and retaining all archive metadata. Sources, input contexts and rebuild commands remain; Library versions are untouched. Older local-archive restoration instructions for those generated outputs are superseded by the retirement receipts. Disk capacity remains an explicit build-admission check.

## Full C continuation and accounting

The original 09:18 source/runtime plan has 7,559 selected nodes across 251 files plus 53 original platform gates. At the signal-free pause on 2026-10-04 at 12:59 UTC, cumulative raw outcomes were 1,874 PASS, 74 FAIL and 5,611 without terminal outcomes. The latest continuation completed all 289 previously stranded C-testsuite/sibling nodes: 275 PASS and 14 FAIL, with all 22 watchdogs complete and no new resource stop. Source stability passed. Earlier aggregate-file protection stops remain in the history; no current failure was manufactured by pausing.

Raw pytest passes are not all native-execution proof. The old Clang comparison helper can collapse compile/link failure to return code 1 and accidentally match a reference program that exits 1. The new candidate records compile, link and run separately and requires actual execution for runtime comparison. A real before/after control demonstrates the old false pass; an owned program that legitimately exits 1 remains accepted. This repair still needs the affected full-corpus rerun. Twenty-four historical runtime-comparison nodes are flagged for reassessment.

Remaining observed C failures include unsupported int128 conversion, missing ordinary C exports, x86_fp80 global initializers, and target/reference ambiguities. C89 fall-through-main cases whose stdout matches but exit status differs require language-mode analysis. The user's earlier 57-failure inventory and 42-regression classification remain attributed historical evidence, not the current cloud total.

## Ten-goal acceptance remains open

1. Native pcc0 → pcc1 → pcc2 → pcc3, byte-identical Stage2/Stage3, original C/Python controls and no libpython: no current qualified pcc1; Stage2/Stage3 are not built.
2. All five collectors: scoped witnessed programs pass; production concurrency, relocation, GC4 capacity, FRESH_ALLOC, long-term memory, throughput and stability remain open.
3. Four platforms: Linux x86_64 executes scoped tests. Cross-target objects do not qualify macOS ARM64, Windows or Linux aarch64. The macOS 15 standard 3-core M1 / 7 GB / 45-minute job and wheel/version parity remain unexecuted.
4. Owned compiler/runtime/ABI/provenance: strict archive admission, undefined-symbol rejection and no-build guards remain active. Remaining stubs, ordinary C exports, external-tool routes, bindings, uv-lock and CLI/API consistency still need qualification.
5. EDG semantics and compilation memory: concrete C/Python fixes are advancing; original full contexts, remaining syntax/ABI gaps and memory targets are not complete.
6. Build performance: Stage2 ≤ Stage1 and all-five-GC shared build under six hours are unqualified. Overlapping diagnostic timings are not acceptance benchmarks.
7. Complete C/Python tests and cleanup: full C is resumable; full Python and remaining integration execution are incomplete. External TLS, archive/freestanding and asynchronous timeout boundaries remain open.
8. Gateway: all 16 original async tests pass on the 13:01 boundary; owned gateway compilation, pcc1 integrations, TLS/network semantics and performance beyond asyncio remain open.
9. Tail-call, MADD, peephole, scaffold identity and thread logging: individual historical controls exist; complete optimization and performance qualification remains open.
10. GUI: pinned DeepSeekHarness reference `47f943859bef60e4160492346772ded9b24f765a`; historical host coverage is 204 cases plus 14 subtests. Native app, five-GC, HTTP/TLS, Loader/HMR, typed FFI, UI interactions and pixel equivalence remain open.

Method-selection/factory work remains separate and unsafe to integrate: saved IR shows a boxed cleanup index retiring after a raw result is taken; descriptor comparison, adapter retirement, method-array synchronization and provider lifetime also remain unresolved. New source and native proof are not substituted for each other.
