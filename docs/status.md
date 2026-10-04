# PCC current status

Updated 2026-10-04 at 13:00 UTC. None of the ten requested goals is fully qualified. Every result below belongs to the stated source; later repairs require their own validation.

## Verified synchronization and recovery

The latest completed local synchronization is PCC `d2ffd8e5e37b6ab0b460b0dd3fc28bec9ba04b73`, gateway `05fe10b57897dfcff19a75b41b7c9630436b32d8`, and GUI `6c61696c2ca54f4afd3f53c60f4e8a2e8bd7e3a3`. It applied the 10:52:08 source capture: 32 PCC paths and one gateway path. All 2,433 packet checksums, after hashes, modes and diff-check passed. No local development, tests or push occurred.

The applied packet is Library `libfile_1fd3f36fa2508191a9982ea45b42f7f7`, version 1, SHA256 `6229991b5b1b427ac7dc8817a88cbef8b91033a436c36f0b60a74a8f46ef324c`. Source manifest: `161d1c612b23db5fe84b7dda756dabac0b6f4fecb488ea02645e4e6caf221554`. The self-contained 09:18 three-repository backup is `libfile_0b5da8dbb25c81919b7de213f74fac3c`, version 1; apply the later packet to recover this boundary. It preserves historical vendored artifacts and restoration instructions.

Cloud work and durable source backups continue. Local synchronization is every two hours, with one change-based commit per changed repository. Push and the later CI repair loop require both the ten-goal prerequisite and an explicit future instruction to start.

## Exact 10:52 host and compiler-graph results

Precommit host/object validation attempted all 1,805 intended nodes: 1,787 PASS, 15 FAIL, two original SKIP and one resource-incomplete node. Fourteen failures reproduce on 09:18; the virtual-thread callback source-spelling assertion was introduced on 10:52. Other failures include old generator-frame and imported-constructor assertions and a mixed-target C3 fixture. The resource stop was the cross-module class schema test spawning nine frontend workers under the 512 MiB host cap. These are preserved outcomes, not dismissed as passing checks.

Full C/Python collection selected 26,721 nodes and gate-deselected 225, with zero errors or skips. Integration collection selected 5,581 nodes. Collection is not execution. Exact failure comparison is Library `libfile_bab649c7a2bc81919fb65365c05016e3`, version 0.

Fresh V11 completed all 443 compiler modules: 422 PASS and 21 frontend failures, zero resource stops, and no loss of a V10 pass. Ten prior contexts recovered and the new C character module passed. Compiler closure: `77a32ad2821220fae1d8f37bdeb10e7f0931f8223be8c8b4f5d01f2a25752e9f`; graph: `8112b687ad0102266e88377ffec9bb15ad4d32d2b436f32256c63a4312a284db`. The 1,578.1-second diagnostic peaked at 3.240 GB. This is host frontend-to-PIDX evidence, not pcc1 or a fixed point. Complete recovery is Library `libfile_04f243e61b808191941d2afcb74d996c`, version 1; terminal summary is `libfile_47718e52a8c081919a10147ddc3475434`, version 0.

## Exact 10:52 native execution

The same compiler built and strictly admitted an owned Linux x86_64 runtime with 186 members, threads enabled and atomic refcount. Archive SHA256: `a0eb10ce20dda4f1a7647e75b2624c0ccd06302407cfc4ae4b8e59b2fc47a4ec`. All seven object preflights passed. Build time was 431.75 seconds with 457,539,584 bytes peak tree RSS during other bounded work; this is not performance acceptance. Runtime recovery: Library `libfile_c2436b3b77c0819194ae6c77a33e798c`, version 0.

All 16 unchanged original async tests compiled and executed: 16 PASS, zero failure, skip, unrun or resource stop. All 410 gateway nodes were attempted: 389 PASS and 21 FAIL, zero skip, unrun or resource stop. Sixteen failures need the missing qualified pcc1. Three socket tests now use the owned product route and fail on missing ordinary POSIX C exports. Two original examples advance beyond the former frame/TLS failure to the DNS managed-result publication boundary. Async proof archives are `libfile_e4ced37ee4fc8191b6ad5bb14fee997d` and `libfile_ccd2e869f4e0819196d49a3916eb3223`; gateway proof is `libfile_7ed72adc8db88191ae537eb3d9b26b63`, all version 0.

Forty additional native cases completed under their watchdogs: 23 PASS, 11 FAIL and six setup ERROR. Failures comprise six native behavior/assertion failures, four C-frame probes that still depend on missing clang, and one fixture that intentionally deletes the supplied runtime archive and is refused admission. Six pcc1 setups remain unavailable. The clang cases are also product-test ownership migration gaps. Actual GC0–4 witnesses passed for cancellation/context identity, ordinary may-park, virtual-thread may-park and slicing. Both changed C fixtures passed.

The six behavior failures are the public virtual_thread import in the nullable cleanup test, two missing coroutine.throw cases, list.append extraction in format, string-subclass format output, and setdefault custom-receiver order. Preserved CPython reference executions for the three format/setdefault cases all exit successfully; these are real PCC failures. Later source repairs do not erase these receipts. Supplement archives: `libfile_e519b42ba7d48191916dfce78f5cf00a` and `libfile_c5d9b01a284881919c79f6d4d32b28ee`; terminal handover: `libfile_3dd7868f605c819191a056b53c1eb4b4`, all version 0.

## Full C continuation and accounting

The original 09:18 source/runtime plan has 7,559 selected nodes across 251 files plus 53 original platform gates. At the signal-free pause on 2026-10-04 at 12:59 UTC, cumulative raw outcomes were 1,874 PASS, 74 FAIL and 5,611 without terminal outcomes. The latest continuation completed all 289 previously stranded C-testsuite/sibling nodes: 275 PASS and 14 FAIL, with all 22 watchdogs complete and no new resource stop. Source stability passed. Earlier aggregate-file protection stops remain in the history; no current failure was manufactured by pausing.

Raw pytest passes are not all native-execution proof. The old Clang comparison helper can collapse compile/link failure to return code 1 and accidentally match a reference program that exits 1. The new candidate records compile, link and run separately and requires actual execution for runtime comparison. A real before/after control demonstrates the old false pass; an owned program that legitimately exits 1 remains accepted. This repair still needs the affected full-corpus rerun. Twenty-four historical runtime-comparison nodes are flagged for reassessment.

Remaining observed C failures include unsupported int128 conversion, missing ordinary C exports, x86_fp80 global initializers, and target/reference ambiguities. C89 fall-through-main cases whose stdout matches but exit status differs require language-mode analysis. The user's earlier 57-failure inventory and 42-regression classification remain attributed historical evidence, not the current cloud total.

## Later candidate repairs awaiting matched native qualification

The candidate now joins typed C static-assert and initializer evaluation, Linux C lifecycle/stdio exports, iterator result/default ownership, method-local foreign provenance isolation, DNS extern-result publication, public virtual_thread imports, and corrected builtin-open/context ownership. The static dispatch table was regenerated and all 458 signatures checked. Six initial batches passed 108 bounded host controls; the corrected open/context follow-on passed 38 more. These counts describe scoped component checks before the new full snapshot validation.

The C static-assert repair also passed 19 unchanged standalone C programs using owned ELF emission and syscall startup, plus 26 original stdio object cases across two targets. The open repair advances the unchanged tarfile and zipfile compiler contexts to PIDX; method-scope isolation advances yacc. Iterator repair clears the old next() failure and reaches a later C evaluator ownership failure. The DNS repair advances the original gateway module contexts through owned IR verification. None of these component results establishes whole native examples or bootstrap completion.

The target-aware C3 fixture now uses the same explicit ARM64 Darwin target for compilation and object emission and checks the actual Mach-O CPU/magic; device execution remains unrun. The cumulative source recovery archive is Library `libfile_7e1a094bdfb08191b19a05a3b4a04214`; corrected open/context source is also separately preserved in `libfile_9b87a123e6808191af7ae45c06e45988`. Exact versions and hashes are recorded in their recovery manifests. Unsafe method-selection/factory work remains outside the candidate with real source bytes preserved.

Storage exhaustion interrupted new tool calls after the completed native suite. Lossless compression of completed passing binaries and exact-content/mode/mtime deduplication of completed immutable source views restored bounded execution. Restore manifests preserve paths and artifact identities. No old archive copies, failing native repros or Library versions were deleted. Space remains a build-admission constraint.

## Ten-goal acceptance remains open

1. Native pcc0 → pcc1 → pcc2 → pcc3, byte-identical Stage2/Stage3, original C/Python controls and no libpython: no current qualified pcc1; Stage2/Stage3 are not built.
2. All five collectors: scoped witnessed programs pass; production concurrency, relocation, GC4 capacity, FRESH_ALLOC, long-term memory, throughput and stability remain open.
3. Four platforms: Linux x86_64 executes scoped tests. Cross-target objects do not qualify macOS ARM64, Windows or Linux aarch64. The macOS 15 standard 3-core M1 / 7 GB / 45-minute job and wheel/version parity remain unexecuted.
4. Owned compiler/runtime/ABI/provenance: strict archive admission, undefined-symbol rejection and no-build guards remain active. Remaining stubs, ordinary C exports, external-tool routes, bindings, uv-lock and CLI/API consistency still need qualification.
5. EDG semantics and compilation memory: concrete C/Python fixes are advancing; original full contexts, remaining syntax/ABI gaps and memory targets are not complete.
6. Build performance: Stage2 ≤ Stage1 and all-five-GC shared build under six hours are unqualified. Overlapping diagnostic timings are not acceptance benchmarks.
7. Complete C/Python tests and cleanup: full C is resumable; full Python and remaining integration execution are incomplete. External TLS, archive/freestanding and asynchronous timeout boundaries remain open.
8. Gateway: all 16 original async tests pass on the 10:52 boundary; owned gateway compilation, pcc1 integrations, TLS/network semantics and performance beyond asyncio remain open.
9. Tail-call, MADD, peephole, scaffold identity and thread logging: individual historical controls exist; complete optimization and performance qualification remains open.
10. GUI: pinned DeepSeekHarness reference `47f943859bef60e4160492346772ded9b24f765a`; historical host coverage is 204 cases plus 14 subtests. Native app, five-GC, HTTP/TLS, Loader/HMR, typed FFI, UI interactions and pixel equivalence remain open.

Method-selection/factory work remains separate and unsafe to integrate: saved IR shows a boxed cleanup index retiring after a raw result is taken; descriptor comparison, adapter retirement, method-array synchronization and provider lifetime also remain unresolved. New source and native proof are not substituted for each other.
