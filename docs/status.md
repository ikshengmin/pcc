# PCC current status

Updated 2026-10-04 at 17:30 UTC. None of the ten requested goals is fully qualified. Every result below is bound to its stated source; the latest integrated runtime and coroutine repairs still need a complete source-matched rebuild and execution.

## Verified synchronization and recovery

The latest completed local synchronization is PCC `fd54ab297e7e21e7c170225de97a862ea402e845`, gateway `05fe10b57897dfcff19a75b41b7c9630436b32d8`, and GUI `6c61696c2ca54f4afd3f53c60f4e8a2e8bd7e3a3`. The 16:32:54 capture applied 29 PCC paths, with all 352 packet checksums, before/after hashes, modes and diff-check verified. No local development, tests or push occurred. Its packet is Library `libfile_21a7f1582470819181daa1f0a2e53b79`, version 1, SHA256 `a5298a5a47b20441739645b4ef229b2de6aff3ecb8760a8da1490eb228f006fe`.

That exact capture passed 280 bounded host/model/object checks. Thirty-four native/integration nodes and the full C corpus were explicitly deferred, not counted as passes. Full C/Python collection selected 27,613 nodes and gate-deselected 225, with no collection errors or skips; 5,635 integration nodes were inventoried. Collection is not execution.

A self-contained full source restoration point is Library `libfile_39cf854663248191b9cf64c4193c41c5`, version 0, SHA256 `a00147fd809afeb7bc3ddfa9fe79c8211002f7e2ddf7391d2ecf51bba60e31ec`. All 30,483 source entries, types, modes and hashes were verified. Apply the 16:32 packet afterward to recover the current synchronized source. Cloud development and recoverable source backups continue. Local synchronization is every two hours, with one change-based commit per changed repository. Push and the later CI repair loop require both the ten-goal prerequisite and an explicit future instruction to start.

## Latest integrated changes awaiting matched qualification

The candidate now uses the callable-aware rooted attribute callback path for ordinary custom `__getattribute__` lookup; the old path could call a managed function object as a native code pointer and crash the original setdefault control. A named single-object diagnostic overlay passed the original control and callback/lifetime probes across GC0–4, but this is mixed-build causal evidence. Proof: Library `libfile_96763a9f69f48191844cb8ce8e80af23`, version 0.

Coroutine close now uses the shared owned unary-call protocol. It roots the receiver, publishes the actual return value and routes errors before cleanup. The original failing repeated-close case, non-None returns, assignment/return/argument/default/list/discarded-result positions and later-argument exceptions passed 51 host/IR/ABI checks and eight native pytest variants in a named compiler overlay. All 40 GC executions had collection witnesses. The runtime ABI and strict ownership validator are unchanged; final combined-source replay remains required.

C manifest refreshers distinguish actual program exit 124 from an incomplete timed-out build/worker stage and classify other infrastructure/build failures separately. No corpus manifests were regenerated. The existing manifest consumers still need coverage for the new build-or-execution-failure category before regenerated inventories are adopted. Imported-constructor and virtual-thread tests now verify the maintained LIFO root/CFG ownership contract; old failures and negative mutation checks are preserved. Existing behavior assertions were retained.

String-subclass formatting, the broad TempDir capability-stub failure, bare two-argument getattr error routing, and public mutable sys.modules/import-registry semantics remain open. A proposed TempDir test narrowing remains outside the candidate because it removes original Path and recreated-directory behavior checks.

## Exact 16:32 native execution

The exact synchronized compiler built and strictly admitted all 187 production runtime members for Linux x86_64, threads enabled and atomic refcount. Archive SHA256: `1facbca01e689728e63ab0e0555470931b671a4ffbd4fb0adf9685a6912f7cff`. Seven owned-object/ABI preflights passed. Runtime/source proof: Library `libfile_2cf531273cd48191b59557fd7b8e5785`, version 0.

The 17-target native matrix completed with 12 PASS and five real failures: two repeated-coroutine-close variants, string-subclass format output, a setdefault custom-receiver SIGSEGV, and the broad TempDir capability stub. Copy/deepcopy, dynamic padding, mixed-scalar ownership, original await lifetime, generator C controls, Unicode payload and open lifetime controls passed. The two await variants witnessed GC0–4. The unchanged full codec program passed five requested collector settings but emitted no collection events, so collector selection was not observable.

The unchanged original gateway C socket probe now passes on the exact compiler/runtime, closing the earlier LVN causal diagnostic. The compiler invalidates cached C expressions at calls and indirect writes, preventing a mutated `ready` variable from being reused for the constant zero in `sent = 0`. The combined targeted/codec/socket proof is Library `libfile_ef71e5a6166c8191a3539abcdf29842c`, version 0. The original full async/gateway controller is running separately on this exact source; earlier 15:26 results below are not substituted for it.

The prepared current C corpus has 7,845 selected nodes and 32 original platform/reference gates, including 21 Csmith nodes. Official Csmith 2.3.0, commit `30dccd73b78652c4719f36572994778a5b233a4e`, and its matching headers are installed in the cloud; setup proof is Library `libfile_69c48c017f148191952fc158e9111ba7`, version 0. Current C execution is queued behind the active native controller and its disk reserve. Clang reference installation remains pending approval. No current full-C success is claimed.

## Exact 15:26 execution and selected compiler evidence

The frozen 15:26 compiler built all 187 production runtime members with threads enabled and atomic refcount and passed strict archive/configuration/ABI admission. Archive SHA256: `d3384fd50da1a883f9c9e04d2bb81ed2c34d7e80133838da0024f56de0535b71`. Build time was 452.07 seconds during bounded diagnostic work; it is not a performance acceptance result. Runtime proof is Library `libfile_1e8c9946ac948191a65921243e1e3d06`, version 0.

All 16 unchanged original async tests passed through actual native execution. Their artifact proofs are `libfile_776199f15e0c8191b90743146bdf8d6e` and `libfile_4f46957430a08191a1e4bdb388292fc5`, version 0. Eighty same-binary GC0–4 replays matched their baseline output/exit, but emitted no collection witnesses; this is not five-GC qualification. The original gateway controller and individual continuation finished at 391 PASS, 17 FAIL, two resource-incomplete compilations and zero never-attempted nodes. The passes comprise 388 host/reference checks, two owned C executions and one native Python structured-scope example. Sixteen failures require missing qualified pcc1; one is the C socket LVN defect, subsequently closed on the 16:32 source. Dashboard and local HTTP compilation reached the 300-second envelope. No completed PCC Python live-network example is claimed. Two earlier continuation launches were rejected by a diagnostic performance lock before target execution and remain infrastructure receipts. Final continuation proof is Library `libfile_f0fd20eb13148191b5b126bf2b795af1`, version 0.

Three complete original C files passed all 77 tests: 21 actual native executions and 56 owned-object/rejection controls, including signed/unsigned 8/16-bit atomics and overflow results. Two earlier missing-archive/configuration attempts are preserved as infrastructure failures. Exact proof is Library `libfile_00b3e0b3e97c8191b4b3c27e03162465`, version 0.

Fresh selected replay closes all four V12 regressions: pipeline_runtime_archive, pipeline_self_backend_cache, driver.project and pathlib. Source/export capture took 76.36 seconds and the four-module replay 18.03 seconds at 722 MB peak RSS. The other 18 V12 failures were not covered. Proof: Library `libfile_1ec00f079e1881918a017fbb22682dcf`, version 0. The unchanged whole-compiler class-schema gate remains queued under its justified 4 GiB compiler envelope.

## Exact 13:01 validation and compiler graph

Precommit host/object validation attempted 2,144 nodes: 2,123 PASS, 18 FAIL, two original SKIP and one resource-incomplete node. Another 404 native/heavy/integration nodes were deferred. Fourteen failures reproduced on 10:52; four expanded extern-string assertions included one introduced managed-result alias-spelling failure. The source-bound phase-reconciled terminal receipt is authoritative; the raw interrupted JUnit aggregation missed one earlier PASS. Full C/Python collection selected 27,062 nodes and gate-deselected 225, with zero errors/skips. Integration collection selected 5,602 nodes. Collection is not execution.

Fresh V12 executed all 443 original compiler modules: 421 PASS and 22 frontend failures, with no timeout, crash or resource stop. It recovered yacc, tarfile and zipfile, but regressed pipeline_runtime_archive, pipeline_self_backend_cache, driver.project and pathlib at the newly enforced open options boundary. Eighteen previous failures remained. The diagnostic took 1,697.7 seconds and peaked at 3.13 GB. Its 22 failures still block pcc1. Terminal source/input proof is Library `libfile_c4b82c2e986c81919afbffc1a7dff58e`, version 0; complete recovery index is `libfile_e3ddc2793cb88191abd7008e50d375bf`, version 0.

The recurring resource-incomplete node was `tests/python/test_py_class_export_schema.py::test_pcc_cross_module_class_schema_matches_local_layout`. Its unchanged full-compiler workload exceeded the 512 MiB host envelope. A diagnostic replay under the existing 4 GiB compiler budget terminated normally after 194.10 seconds with an actual codegen failure: `slot-call CPython name requires an output-slot bridge: temp_dir`, in `pcc.package.install::_build_requirement_tool_wrappers`. Peak process-tree RSS was 2,220,138,496 bytes: 2,046,103,552 in the worker and 174,034,944 in the parent. Schema assertions were not reached. Raising the envelope exposed the defect; it did not fix product memory or certify the test. Proof: Library `libfile_6d7e9274b2ac8191862c3acc9a3b2e05`, version 0.

## Exact 13:01 native execution

The same compiler built and strictly admitted an owned Linux x86_64 runtime with 186 members, threads enabled and atomic refcount. Archive SHA256: `00ea9db17f218bd2565ff4a8953d665b604672c93378296a563dad44f555c607`. All seven preflights passed. Build time was 443.82 seconds with 458,530,816 bytes peak tree RSS during other bounded work; this is not performance acceptance. Runtime recovery is Library `libfile_1ffafd433ea081919b0880157d4f006a`, version 0.

All 16 unchanged original async tests compiled and executed: 16 PASS. Three original repaired controls also passed: nullable import cleanup, builtin-open/context lifetime, and context-entry rebinding. Open/context variants witnessed actual GC0–4 collections; nullable requested settings without collections are not equivalent witnesses. Proof archives are `libfile_f4b29264ecf4819182eb6a7cc013442d`, `libfile_8680352418048191865f8419e5702c1e`, and `libfile_9da433f7c3ec8191ae82b12aceca7533`, all version 0.

Gateway outcomes were 390 PASS, 15 FAIL, one attempted resource-incomplete dashboard compile and four later nodes not admitted. Fourteen failures require missing qualified pcc1; one fails owned linking on nine POSIX exports. The dashboard advanced beyond DNS but exceeded its 300-second compilation envelope. Of the 390 passes, 388 are host/reference checks and two are owned C socket executions. No PCC Python live-network gateway example completed in this run. The earlier 10:52 total of 389 passes similarly included 388 host/reference checks and one compiled structured-scope example. Neither count is a native gateway qualification. Terminal proof: Library `libfile_9a42a450af548191b286a9c897c79ded`, version 0.

## Storage cleanup

User-authorized cleanup retired obsolete completed builds, successful generated IR and their older local archive copies, while preserving current source, the current runtime, C continuation inputs, needed failing reproductions and compact provenance. Further cleanup retired completed V9/V10/V12 generated products, old successful executables and redundant local output archives. Compact replacements retain unique source, commands, metadata, receipts and failed binaries; only proven regenerable successful products were removed. Sources, input contexts and rebuild commands remain; Library versions are untouched. Older local-archive restoration instructions for those generated outputs are superseded by the retirement receipts. Disk capacity remains an explicit build-admission check.

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
