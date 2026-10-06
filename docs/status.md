# PCC current status

Updated 2026-10-06 at 01:06 UTC. All ten requested goals remain open. No pcc1 binary or Stage2/Stage3 fixed point exists.

## Current source

The verified synchronized baseline is PCC `21e13f07ab85d77b6a6bae7fed3d6469b200ff96`, gateway `05fe10b57897dfcff19a75b41b7c9630436b32d8`, and GUI `16c6ecfca3e576b5bef302071325c0f8dd8bc549`. The 23:30 synchronization committed 29 PCC paths from the immutable 23:01:54 capture, compiler `3176a826e231861cfcc2f9880629d524d7c25bc1825080762afd6952e2dfcf2d`. Actual source and restoration ancestry are Library `libfile_baa423a645388191a9d6591f9d450064` v0. Twenty untouched vendor CRLF files retained their raw bytes. The next authorized synchronization is 01:30 UTC. Local work remains synchronization and commits only.

The mutable candidate now has compiler checksum `9fbdc665bbe3af996a36271f99eec9f5797b1b2336c733c8ceb8b32a67913947`, with 11 changed paths after the baseline. These comprise four test-fixture repairs, the iterable min/max producer and its test, the stack-map packing lifetime repair and its test, the direct file-method producer repair and its test, and this status document. The local-priority 50889ccd design is preserved. No private HTTP, generic multi-argument min/max or runtime ordering/cleanup proposal has been admitted.

The immediate path is this coherent core source through a fresh admitted runtime, the maintained Stage1, repair of any actual failing original input, then linking and pcc1 smoke. Later feature/review work is paused with actual bytes preserved separately.

## Latest actual Stage1 result

The immutable 41bc source was restored and verified from Library `libfile_b9142e4b7c308191af4af10a88425477` v0. Its compiler checksum is `41bc99590c8c47169afe994dc0d13dd737a2c42ca7ae8f69fa3d9e0a559750be`, and its 29,910-entry PCC manifest is `7bd4fcf90a29cf6fe35bdebca9592dcc4026d92d0cd2dea434cdf019970f7f21`. Its fresh 189-member Linux x86_64 runtime passed strict source/codegen, threads=1, atomic-reference-count and inventory admission. Runtime archive SHA256 is `10b4b262c1b3ace16ed5f4c9d55f060878a472c069dd59eef1842aaeb7b84131`; actual archive and receipts are Library `libfile_8cd2312dafc08191b4e75fe655edd205` v0.

The original maintained Stage1 ran with the unchanged 4 GiB process-tree and 2,400-second guards. It passed the former CLI memory boundary and completed 16 owned objects, totaling 445,544,568 bytes. The CLI object is 174,341,656 bytes, SHA256 `e779416c085a68e1919f8111423f92a38673d780c5d5f320831dcdc52b87ffb3`. Peak actual tree RSS was 4,113,272,832 bytes, including the real parents. The actual graph contains 453 modules and 452 sibling initializers. The rejected host urllib.request discovery probe is not part of that graph.

At 1,126.30 seconds, Stage1 stopped on a compiler error in `module_action_dag.publish_graph_state_file`: `stream.fileno()` passed to `os.fsync` lacked an authoritative owned-result handoff. No link or pcc1 was produced. Although original cleanup removed the live object directory, all 16 completed objects and exact failed inputs survived in external capture. Full proof is Library `libfile_4570814cd8e48191874b53354474ac0f` v0; its ledger is `libfile_4c6c52456b848191af55b1a936f42fae` v0. The maintained path currently cannot resume partial PCOs; its reuse option requires an already completed pcc1.

The narrow 9fbd repair routes fileno, tell and flush through the existing receiver-rooting and immediate-result-publication helper. Receiver evaluation remains single, and strict consumer checks are unchanged. Forty-seven focused checks pass. The complete original module, using the original multi-assignment export-reader context, emits a verified 12,743,968-byte object with zero fallback records, SHA256 `f5b294e0df2eb7871ec918e037e4705adb2469c712e68f2c5c9cb3e8d9e9129c`. Source, tests, inputs and object are Library `libfile_bf66081b31a48191a38bf49541fee221` v0. The new compiler still needs its fresh matching runtime and whole maintained Stage1; component object proof is not native execution.

## Memory evidence and scope correction

Earlier maintained 3176 failed during CLI assembly at 4,325,101,568 bytes. The worker used 4,105,875,456 bytes; parent overhead was below the prior diagnostic reserve. Its exact failure is Library `libfile_34882fd46e748191876c796b7a74d6a7` v0. The packing repair compacts records per function and retires finished lookup caches, removing 61,275,660 shallow bytes while preserving packed payload and complete object bytes. Its 128 scoped checks, source review and diagnostic object are in `libfile_6cbdb408d9308191b7a8eed64dc5c1b2` v0 and `libfile_51b0347283088191b5c58a04ee1feb14` v0. The later actual 41bc run above, rather than those instrumented diagnostics, establishes that the CLI memory boundary was crossed.

Correction: the earlier yacc/lex/datetime preflight retained full source names and the export file but selected one assigned module. That selects the worker's filtered export reader, unlike the original multi-assignment reader. Its strict frontend results apply to that singleton export view, not an identical original effective context. The module57 reproduction and repaired object above preserve the full original reader. Earlier receipts remain intact with an explicit correction; no prior pass was relabeled as a whole-compiler pass.

## Validation and remaining native work

The synced 3176 capture's 24 affected files had 938 passing nodes and 41 native deferrals: 16 complete files and eight incomplete. Full C/Python collection found 30,738 nodes in 1,767 files with 225 explicit deselections and no errors. Collection supplies no execution credit.

Later fixed-scope host evidence is Library `libfile_a7926470ebd481918b413679984426e4` v2. Cumulative PCC outcomes on 3176 are 3,927 passes, 36 failures, 15 provisioning-guard infrastructure errors, seven resource blocks, 41 native deferrals, three skips and 4,466 unrun. The 36 failures comprise 15 IR/count assertions and 21 incomplete model namespaces. Four later test repairs preserve original semantic cases and pass 141 host checks; their manifest is `libfile_522a3e9482c48191ab25d194ee0d5b8f` v0. Three recursive-stdlib assertions remain queued. These later changes do not alter the sealed counts.

The exact 3176 gateway host lane passed 379 eligible cases. The remaining 31 of 410 comprise eight requiring an external C observer, three owned C, three owned Python, one OpenSSL reference and 16 requiring pcc1. The 16 mandatory async tests and the original native causal/C controls remain queued on that source. Host receipts and ready commands are Library `libfile_4b7684ec8cec8191a8aaa0c8cf926a2c` v0. They do not qualify the newer 41bc or 9fbd source.

Historical 19:35 source e119 passed all 16 original async tests and both original C controls through owned compilation, linking and native execution. Its gateway results included no Python product-execution pass. Those earlier artifacts are retained in Library `libfile_875177d5463c819192a4f907b78246e5` v0 and provide no current-source credit. Full original C/Python, native integration, collector/platform/performance and fixed-point acceptance remains open.

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
