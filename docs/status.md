# PCC current status

Updated 2026-10-04 at 10:48 UTC. None of the ten requested goals is fully qualified. Results below are bound to their exact source; later edits do not inherit earlier passes.

## Verified synchronization and recovery

The latest completed local synchronization is PCC `eefb35ac3c097a583b7928b0b557e4a5703bf49e`, gateway `33856e0cf8194977ab4203cb02bfa2aee6a8d88f`, and GUI `6c61696c2ca54f4afd3f53c60f4e8a2e8bd7e3a3`. PCC changed 17 paths from the 09:18:18 source capture. Local application, hashes, modes and diff-check passed; no local development, tests or push occurred.

The exact source packet is Library `libfile_50c06ad69a7081919339005874bfe765`, version 1, SHA256 `36f7ce3bede8bf23cad82830f234ca614fb94882fffe72357df466a8d4274267`. Its source manifest is `dd43e331c7804145de749d5bfab4868ac51be59f4f54d365b32e23e6391b7960`. A self-contained backup of all 30,442 inventoried repository entries is `libfile_0b5da8dbb25c81919b7de213f74fac3c`, version 1. It preserves historical vendored artifacts unchanged and includes restoration instructions.

Cloud work and durable source backups continue. Local synchronization is every two hours, with one change-based commit per changed repository. Push and the later CI repair loop require both the ten-goal prerequisite and an explicit future instruction to start.

## Qualified 09:18 source boundary

The exact precommit host/object run reported 1,357 distinct passes and three failures across all 1,360 attempted nodes, with no resource stop. Failures were an unchanged Darwin-only private C linker helper used on Linux and two native-provisioning refusals. Full C/Python collection selected 26,548 nodes and gate-deselected 225; integration collection selected 5,569. Both collections had zero errors or skips. Collection is not execution.

A fresh owned Linux x86_64 runtime was built and strictly admitted for this same compiler: 186 members, threads enabled, atomic refcount, archive SHA256 `cb81c2da53409534c67c20e73cac9396467330f8213ace693f569059d28c4bca`. Compiler closure is `d851f353d20a7eb49abdacb5daa0103946eaeca1397f47c609784f2c22fbbfaa`; codegen checksum is `7de1a6d76bb577d93e975b0cf2eec6e8baa3a1e8a14c1ccece71e391b35bfbd7`. Build time was 427.30 seconds and peak tree RSS 458,567,680 bytes during other bounded work; these are diagnostic measurements, not performance acceptance. Runtime recovery is Library `libfile_cd5e8edd527c8191a0b596a4dc408f9b`, version 0.

All 16 unchanged original async tests compiled and executed successfully: 16 PASS, zero failure, skip, unrun or resource stop. The nested-closure case passed both standalone and in the complete maintained controller. Its former slicing and module-parent publication failures are resolved at this source boundary. Complete source/binary/receipt archives are `libfile_9c4da14528588191a86d46c63027b1fa` and `libfile_2b99ade7a1888191ae2d1c1895f8803f`, both version 0.

All 410 gateway nodes were attempted: 389 PASS and 21 FAIL, with no skipped/unrun nodes or resource stops. Two original examples fail the persistent generator-frame output contract; 16 require a qualified pcc1 that is not available. Three socket probes fail because they invoke missing clang to compile PCC runtime IR and their C programs. Those three are also owned-workflow migration gaps, not pure reference-oracle failures. Gateway proof is `libfile_85a4296138a081919f25c4f7ab8232ea`, version 0.

The maintained slice regression passed with actual GC0–4 witnesses. Environment-copy, sequence-index and system-result programs passed their unchanged five-setting tests plus 15 separate actual collector-witness replays. Corrected D3 passed. The original package fixture passed under five requested settings; its source has no collection witness, so actual selection is unobservable. Three pcc1 variants remain explicit setup failures. These scoped proofs are in `libfile_659710dd36fc8191919330fad5e35e08`, version 0; they do not qualify all collector behavior.

## Full C execution and accounting

The unchanged full C plan contains 7,559 selected nodes across 251 files, plus 53 original platform gates. It reached a source-stable pause with 1,599 raw pytest passes, 60 failures and 5,900 unreported nodes. All 154 completed attempts reconcile with JUnit; two healthy aggregate file attempts reached their 300-second protection limit and have preserved interrupted nodes. A signal-free orchestration barrier stopped the parent only after the active child completed; it added no test failure. The exact remaining-node plan uses batches of at most 40 and retains the original source/runtime and caps. Execution will resume after the next compiler/runtime qualification phase.

These raw pytest passes are not all native-execution proofs. One comparison helper converts PCC compile/link exceptions to return code 1, allowing equality with a reference program that exits 1. That accounting defect is preserved and needs explicit phase/execution assertions. Some compatibility tests deliberately use system assembly/linking; the public `--backend=self --freestanding-libc` route also reaches a host linker implicitly, which is a production ownership gap. Reference-tool/platform mismatches are classified separately from PCC defects.

Confirmed product failures include `_Pragma` parsing, missing ordinary C symbols such as `exit`, `atoi` and `puts`, int128 cast lowering, and file-scope static assertions that incorrectly try to emit runtime IR. Varargs and long-double tests have additional target/fixture ambiguity that is not silently assigned to product semantics. The user's historical 57-failure inventory, including 42 regressions, remains attributed evidence rather than a current cloud total.

## Later joined source, not yet native-qualified

The candidate now contains bounded text/byte result publication, dictionary setdefault output ownership, a shared target-aware C character decoder, nullable constructor-field inference/export joining, generator-frame result ownership and corrected dispatch placement, target socket-header constants, an exact-expression SSA GVN repair, and owned C/gateway test-route corrections. Component proofs and original failure bytes are durably preserved before heavy runs.

A new format cleanup regression was found before whole-graph qualification: 32 repeated fields produced 7.93 MB of probe IR and 18,605 cleanup-root visits. Reusable scratch owners reduce this to 1.31 MB, 15 root registrations and 24 cleanup-root visits, while preserving eager argument order and observed transient lifetimes. The repair has 94 dedicated and 88 existing host checks passing; native lifetime tests remain pending.

The generator repair's earlier SwitchInstr placement claim was withdrawn after a counterexample. Corrected restores now precede dispatch and dominate tested resume/error/cleanup paths. Two original gateway executable-closure IR diagnostics advance beyond the frame refusal and reach a separate TLS-field provenance error. That error is traced to the constructor's final None cleanup assignment erasing an earlier native instance field type; local and exported field schemas now join those states as dynamic native object storage. The joined behavior still requires native execution on a new matching runtime. Strict ownership checks remain enabled.

The C GVN repair fixes a demonstrated wrong result: a nested same-line unary expression had been substituted for an entire boolean return. The unchanged volatile program now returns the expected result under owned Linux execution. The gateway socket migration preserves all original C program bytes, but actual linking/execution awaits a matching runtime and missing ordinary POSIX C exports. The old runtime is correctly rejected after the optimizer edit.

A fresh complete V11 compiler audit is active on frozen source closure `77a32ad2821220fae1d8f37bdeb10e7f0931f8223be8c8b4f5d01f2a25752e9f`. Fresh graph `8112b687ad0102266e88377ffec9bb15ad4d32d2b436f32256c63a4312a284db` contains all 442 V10 names plus the C character decoder. Two old relative positions change through new dependencies; transitions are compared by name. Inputs are Library `libfile_6edfcb3728cc8191887e930fa8e20a74` and `libfile_32db4811c990819196e23550da200f3e`, version 0. No terminal V11 result is claimed yet.

Historical V10 completed 411 PASS and 31 frontend failures across 442 modules, with no resource stop and no lost V9 pass. It recovered 12 prior contexts and added one passing module. That is host frontend-to-PIDX evidence, not a native pcc1 or fixed point.

## Ten-goal acceptance remains open

1. Native pcc0 → pcc1 → pcc2 → pcc3, byte-identical Stage2/Stage3, original C/Python controls and no libpython: no current qualified pcc1; Stage2/Stage3 are not built.
2. All five collectors: scoped witnessed programs pass; production concurrency, relocation, GC4 capacity, FRESH_ALLOC, long-term memory, throughput and stability remain open.
3. Four platforms: Linux x86_64 executes scoped tests. Cross-target objects do not qualify macOS ARM64, Windows or Linux aarch64. The macOS 15 standard 3-core M1 / 7 GB / 45-minute job and wheel/version parity remain unexecuted.
4. Owned compiler/runtime/ABI/provenance: strict archive admission, undefined-symbol rejection and no-build guards remain active. Remaining stubs, ordinary C exports, external-tool routes, bindings, uv-lock and CLI/API consistency still need qualification.
5. EDG semantics and compilation memory: concrete C/Python fixes are advancing; original full contexts, remaining syntax/ABI gaps and memory targets are not complete.
6. Build performance: Stage2 ≤ Stage1 and all-five-GC shared build under six hours are unqualified. Overlapping diagnostic timings are not acceptance benchmarks.
7. Complete C/Python tests and cleanup: full C is resumable; full Python and remaining integration execution are incomplete. External TLS, archive/freestanding and asynchronous timeout boundaries remain open.
8. Gateway: original async 16 is green on the 09:18 boundary; owned gateway compilation, pcc1 integrations, TLS/network semantics and performance beyond asyncio remain open.
9. Tail-call, MADD, peephole, scaffold identity and thread logging: individual historical controls exist; complete optimization and performance qualification remains open.
10. GUI: pinned DeepSeekHarness reference `47f943859bef60e4160492346772ded9b24f765a`; historical host coverage is 204 cases plus 14 subtests. Native app, five-GC, HTTP/TLS, Loader/HMR, typed FFI, UI interactions and pixel equivalence remain open.

Method-selection/factory work remains separate and unsafe to integrate: saved IR shows a boxed cleanup index retiring after a raw result is taken; descriptor comparison, adapter retirement, method-array synchronization and provider lifetime also remain unresolved. New source and native proof are not substituted for each other.
