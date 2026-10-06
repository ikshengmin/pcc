# PCC current status

Updated 2026-10-06 at 03:51 UTC. All ten requested goals remain open. No pcc1 binary or Stage2/Stage3 fixed point exists.

## Current source

The verified synchronized baseline is PCC `5e23577b27b68754c714c140296fd4b4a3e0e47f`, gateway `05fe10b57897dfcff19a75b41b7c9630436b32d8`, and GUI `16c6ecfca3e576b5bef302071325c0f8dd8bc549`. The 03:30 synchronization committed six PCC paths from the immutable 02:20 capture. Actual source, baseline ancestry and strict restoration instructions are Library `libfile_2d9fa3cc91388191b73d28acafdbff1e` v0. All 30,608 captured entries were verified; twenty untouched vendor CRLF files retained their raw bytes. The next authorized synchronization is 05:30 UTC. Local work remains synchronization and commits only.

The synchronized compiler is `53174309d378fe2ae8a660596e3c55963c1a05c0edcd63cd596f66c6d29cd8c4`. The candidate now adds owned clock calls and typed floating error returns, four regression/fixture paths, and this status update: seven paths after the baseline. Its compiler checksum is `5b0fdedc29d3bd279f4855355651c62c4ea0381f28abb42f7dcec4f003953f3c`. Actual repair bytes and evidence are Library `libfile_96a1a0c03b4c8191b2b9bc08821dc38a` v0. The local-priority 50889ccd design is preserved; private HTTP, generic multi-argument min/max and runtime ordering/cleanup proposals remain unadmitted.

The next boundary is a fresh matching runtime, the direct clock/error native regressions, and the complete original async suite on a stable source. The subsequent whole compiler build needs measured profiling or a sound continuation decision: another identical cold 40-minute run cannot cover the measured graph. Guards, source identities and validation remain strict; a diagnostic budget is not an acceptance target.

## Latest maintained Stage1 result

The immutable 53174309 source produced a strictly admitted 189-member Linux x86_64 runtime with threads enabled and atomic reference counting. The runtime build took 555.70 seconds. Archive SHA256 is `a5469d663fd5dd4fc5c91df27c024766fb9555581e635fba09aae8411eeb0cb2`; archive and admission receipts are Library `libfile_137216b930708191bfacca25f63a350b` v0.

The maintained Stage1 verified its actual 453-module graph and 452 sibling initializers. It crossed the former CLI memory failure and module57 file-result ownership failure. The CLI object was 174,341,656 bytes, SHA256 `cfbde7b2e6ce6638132d0dd01d4c78625a0bebe98272a150147da76415a6f519`. Its exact object/context capsule is Library `libfile_546e70ece10c8191a4d4596db701f332` v0.

The unchanged 2,400-second external watchdog stopped healthy compilation at 2,400.159 seconds during module242, `attr_load_lowering`. There was no compiler error. It completed 63 objects totaling 944,146,952 bytes. Sampled peak process-tree RSS, including the real parents, was 4,130,275,328 bytes, below the 4 GiB diagnostic guard. Source/runtime postchecks passed. Worker0 had not completed its result TSV; module91 was scheduled in the unstarted worker1, so this run does not establish its whole-run success. No link, pcc1 or native smoke occurred.

All 63 objects and the original worker/AST/export context are preserved in Library `libfile_28f25ed36cbc8191bdd9f46af33fa7a5` v0. The maintained route has no supported partial-PCO resume; its reuse option requires an already completed pcc1. The completed objects must not be reused across changed compiler identities or counted as a complete bootstrap. The immediate build blocker is duration and missing continuation, rather than a compiler error in this attempt.

The scalar-import repair still has its independent, exact original module91 before/after proof: the old i64-as-object cleanup error was reproduced with the full export reader, then the repair emitted a verified owned object. Its evidence is `libfile_0bd6f8a9ac44819183eb62ae6f2554c4` v0. That component proof is separate from the unfinished maintained run.

## Actual native execution and the clock repair

The admitted 53174309 runtime executed seven causal nodes: three passed and four failed, with no infrastructure failures. Scalar-import readback and both strict negative struct-sequence controls passed under all five requested collector settings. No collection events were observed, so this is not five-collector qualification. The three positive tuple/time cases crossed class initialization but failed because native type objects lack `__bases__`. The unchanged original async-for program compiled, linked and ran, then exited with SIGILL and empty stdout/stderr. All original assertions remain unchanged. Complete programs, ELF binaries and receipts are Library `libfile_9f04a9ae90d88191a490083b360fe9a1` v0.

A small replay of that exact ELF logged an unhandled RecursionError immediately before SIGILL. Static emitted code shows owned `time.monotonic` calling its own published module through `_native_time`, and its floating error exit contains UD2. The exact fault address remains unobserved; no privileged tracing or core-dump inspection was used. The earlier tuple-base omission is repaired, but this later failure remains a failed original integration result.

The new candidate calls the existing owned `py_time_*` ABI for all five affected wrappers. The established `sys.implementation.name` guard preserves their working plain-CPython path and removes the host branch in the tested PCC context. Floating error exits now return typed zero sentinels after existing cleanup, allowing callers to inspect the preserved TLS exception. Eighty-two host/model/IR checks and CPython references pass. Identical focused export fingerprints demonstrate old dynamic provider calls versus the new owned calls. An earlier standalone test selected builtin shortcuts and is retained as insufficient provider-context evidence. New-source native execution, full async and bootstrap remain pending.

Other known native gaps are unchanged: `type.__bases__`, missing native `os.unlink` during the file-method control's cleanup, and min/max comparison-error loss. The latter two were observed on the older 9fbd source, where original assertions stopped at GC0. Those binaries/programs are preserved in `libfile_b52bb4e0b3588191bf8600ac7ba43e9b` v0 and do not supply newer-source credit.

## Exact synchronized validation scope

The 03:30 packet preserves its explicit 02:44:50 checkpoint, before the later Stage1 launch. On that captured source, affected nodes were 222 PASS, three FAIL, thirteen native-deferred and one object-deferred across ten whole files: six PASS, one FAIL and three INCOMPLETE. The separate mandatory async scope was one FAIL and fifteen UNRUN; all 410 gateway nodes were UNRUN. These overlap the seven-node native block and must not be added as disjoint totals.

Fixed coverage contains 317 PCC files and 28 gateway files. PCC has 8,576 total nodes: 8,556 selected and twenty explicit default deselections. Full C/Python collection found 30,819 nodes in 1,773 files, including 225 default deselections. Collection is inventory, not execution. Full original C/Python and native integration acceptance remain open.

Historical source bands remain separate. Earlier 3176 failed the CLI memory guard; the packing lifetime repair preserved payload/object bytes, and later maintained runs crossed that boundary. Earlier e119 passed sixteen original async tests and two C controls, but those results do not qualify current code. Older singleton yacc/lex/datetime preflights selected a filtered export reader; only their effective singleton context was tested. The later original module57 run and module91 component replay preserved the full reader. Historical receipts were not relabeled as current or whole-compiler passes.

## Ten-goal acceptance remains open

1. Bootstrap: produce pcc1, run the original C/Python controls, then establish a no-libpython Stage2/Stage3 byte fixed point.
2. Five collectors: finish correctness, concurrency, relocation, GC4 capacity, FRESH_ALLOC, long-term memory, throughput and stability.
3. Platforms: execute macOS ARM64, Linux x86_64, Windows and Linux aarch64 qualification, including the standard macOS 15 3-core M1 / 7 GB / 45-minute job and wheel parity.
4. Ownership: complete compiler/runtime/ABI/provenance, stubs, bindgen, uv-lock and CLI/API consistency without product cc/LLVM/libpython fallbacks.
5. EDG-derived design: complete frontend semantic representation, a single layout authority and per-function compilation-memory management; qualify layered validation and centralized ABI across platforms. Diagnostic RSS guards are not invented acceptance thresholds. External i128 register-edge ABI, x86 dynamic alloca and two-dimensional VLA remain gaps.
6. Build performance: prove Stage2 no slower than Stage1 and a shared five-GC build under six hours. Diagnostic timings do not establish these targets.
7. Tests and cleanup: execute the full original C/Python and integration inventories; finish TLS, archive, freestanding and asynchronous timeout work.
8. Gateway: qualify actual native network/TLS and structured concurrency, then establish performance beyond asyncio.
9. Optimization: qualify tail-call, MADD, peephole, scaffold identity and thread-logging behavior and performance.
10. GUI: complete native app, five-GC, HTTP/TLS, Loader/HMR, typed FFI, interaction and pixel equivalence against Harness 47f943859bef60e4160492346772ded9b24f765a. The existing 160 host passes and 7,412 verified reference blobs do not establish native or pixel completion.

Push and the future CI repair loop require both the ten-goal prerequisite and an explicit instruction to start. Detailed historical runs, failed attempts and restoration instructions stay in durable archives outside the repositories.
