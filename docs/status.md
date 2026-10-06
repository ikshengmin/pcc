# PCC current status

Updated 2026-10-06 at 02:20 UTC. All ten requested goals remain open. No pcc1 binary or Stage2/Stage3 fixed point exists.

## Current source

The verified synchronized baseline is PCC `e93238faed92917b61126158a5f1669be5bf68f7`, gateway `05fe10b57897dfcff19a75b41b7c9630436b32d8`, and GUI `16c6ecfca3e576b5bef302071325c0f8dd8bc549`. The 01:30 synchronization committed all 11 PCC paths from the immutable 01:08:26 capture. Actual source, baseline ancestry and restoration instructions are Library `libfile_6750d9b1fa7881919f1876290402c5de` v1. All 30,605 captured entries were verified, and twenty untouched vendor CRLF files retained their raw bytes. The next authorized synchronization is 03:30 UTC. Local work remains synchronization and commits only.

The candidate now adds two direct repairs: scalar-import cleanup and builtin tuple base publication, with three regression test files and this status update. These are six changed paths after the baseline. Its compiler checksum is `53174309d378fe2ae8a660596e3c55963c1a05c0edcd63cd596f66c6d29cd8c4`. The synchronized compiler is `9fbdc665bbe3af996a36271f99eec9f5797b1b2336c733c8ceb8b32a67913947`. The local-priority 50889ccd design is preserved. Private HTTP, generic multi-argument min/max and runtime ordering/cleanup proposals remain unadmitted, with their actual bytes preserved separately.

The immediate path remains a coherent source through its matching runtime and the maintained Stage1, repair of actual original failures, then linking and pcc1 smoke. Other feature work remains paused. A prior compiler's objects or native passes do not qualify this new source.

## Latest actual Stage1 result

The synchronized 9fbd source produced a fresh, strictly admitted 189-member Linux x86_64 runtime with threads enabled and atomic reference counting. Its build took 546.17 seconds. Archive SHA256 is `6d30242a718d1ed9fe8349d798c4e0db704a8ca814a58ee2f643f6864152bc37`; the actual archive and admission receipts are Library `libfile_66c8cd1ded308191a2e8b2006088bcb5` v0.

The maintained Stage1 used the unchanged 4 GiB process-tree and 2,400-second diagnostic guards. Its complete discovered graph had 453 modules and 452 sibling initializers. It passed the former CLI memory failure and the original module57 file-result ownership failure. The CLI object was 174,341,672 bytes. Module57, `module_action_dag`, produced a 12,743,984-byte object using the full original multi-assignment export reader; its exact context is Library `libfile_ff5cb9183e8481918304e50e8bb32780` v0.

At 1,369.10 seconds, Stage1 stopped on an operand-type error in module91, `pcc.backend.self_backend_aarch64_darwin_tail_calls`. Its generated module initializer passed an i64 `import.publish.current` value to an operation requiring void*. The run completed 30 objects totaling 534,292,800 bytes. Peak actual tree RSS, including the real parents, was 4,123,992,064 bytes. Source and runtime identities passed post-run checks. No link or pcc1 was produced. All 30 objects and the exact failed inputs are retained in Library `libfile_1c14263b721c81918835160af1d7d202` v0. The maintained path still cannot resume partial objects; its reuse option requires a completed pcc1.

The new repair distinguishes scalar ABI storage from GC-owned object storage during import staging retirement. Scalar projections were never pinned and must retain their ABI contents. Object slots retain their existing unpin and root-barrier cleanup. Strict type and ownership validators are unchanged. The before replay reproduced the exact original error with all 453 exports and all 113 original assignments preserved. The after replay produced a verified 530,360-byte owned object in 4.317 seconds at 233 MB peak, with zero fallback records. That is component compilation proof. Actual code and evidence are Library `libfile_0bd6f8a9ac44819183eb62ae6f2554c4` v0. The intermediate a2e9 capture is preserved as `libfile_7d942ae2533c8191996c90e2dd61d7a2` v0; its runtime was not built because the following native startup defect was then identified. The combined compiler still needs its matching runtime, whole Stage1 and native readback.

## Actual native execution and test scope

Two causal programs executed on the admitted 9fbd runtime and both failed on GC0. The file-method control reached cleanup, then failed because native `os` has no `unlink` attribute. The min/max control failed its original assertion, `max comparison error lost`. These were semantic failures, not resource stops: 40.34 seconds / 207 MB and 24.84 seconds / 186 MB respectively. Original assertions stopped execution, so GC1–4 remain unrun. Neither entire program is counted as passed. Those complete failed programs, ELF binaries and GC logs are Library `libfile_b52bb4e0b3588191bf8600ac7ba43e9b` v0. The full async/gateway controller then executed four original async nodes on the same unchanged source: one passed and three failed with `TypeError: struct sequence requires a tuple subtype`. It is paused at a supported node boundary, with twelve async nodes and all 410 gateway nodes still pending.

The retained async ELF identifies the shared failure: its initializer registered as `time` creates `struct_time` with zero bases, then invokes the unchanged struct-sequence validator before user main. Class lowering handled builtin `str` but omitted builtin `tuple`. The actual Stage1 graph includes the same owned time provider, exposing subsequent pcc1 startup. This conclusion comes from emitted constructor arguments and source; dynamic first-site/MRO state was not observed, and no exact earlier-source regression classification is claimed.

The combined repair routes builtin tuple through the existing owned type provider, after resolved user classes and builtin-shadowing checks. It preserves the runtime validator. Unresolved rebound tuple bases fail explicitly; general dynamic base support is not claimed. Sixty-six focused compiler/model checks passed, with six object-heavy cases deselected; a separate owned-object gate verifies the canonical tuple provider and one base. Five new native cases remain unrun. Exact source, tests, machine-code evidence and original diagnosis are Library `libfile_84dd56aa45148191bb7765b4c0368f8e` v0. The next matched runtime will run the direct scalar-import and tuple/time causal checks before continuing the maintained Stage1.

The sealed 01:08 snapshot's affected execution had 544 passing nodes and eight native deferrals across 16 whole files: twelve complete files and four incomplete. Its fixed PCC scope has 8,548 nodes, of which 7,996 were unrun at sealing. The mandatory 16 async and 410 gateway cases were also unrun at sealing; later execution receipts remain separate. Full C/Python collection found 30,791 nodes in 1,770 files with 225 explicit default-gate deselections. Collection is inventory, not execution.

Historical evidence is preserved without current-source credit. The earlier 3176 maintained run exceeded the memory guard; the subsequent packing repair retired 61,275,660 shallow bytes while preserving payload and object bytes, and both later 41bc and 9fbd maintained runs crossed the CLI boundary. Earlier 3176 fixed-scope host failures, resource blocks and unrun nodes remain in Library `libfile_a7926470ebd481918b413679984426e4` v2. Historical e119 passed the original 16 async and two C controls; its artifacts are `libfile_875177d5463c819192a4f907b78246e5` v0. None of these establish full current C/Python, native integration, collector, platform, performance or fixed-point acceptance.

An earlier scope correction also remains in force: singleton yacc/lex/datetime preflights selected a filtered export reader even though they retained the full export file. Those results apply to that effective view. The actual module57 run and module91 before/after replay preserve the original full reader; earlier receipts were not relabeled as whole-compiler passes.

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
