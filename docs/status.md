# PCC current status

Updated 2026-10-05 at 23:01 UTC. All ten requested goals remain open. No pcc1 binary or Stage2/Stage3 fixed point exists.

## Current source and synchronization

The verified synchronized baseline is PCC `168e4d069e51ec4a6df58a8f1bf90ec7ecde7ce5`, gateway `05fe10b57897dfcff19a75b41b7c9630436b32d8`, and GUI `16c6ecfca3e576b5bef302071325c0f8dd8bc549`. Its immutable 21:01:34 UTC source capture has compiler checksum `87bddb86f9b21374a5945ebfc7c5e337526e8ee8c4f2f9b924fbef8aac65b46a`. Source and restoration ancestry are Library `libfile_251e91e1bef08191b21ea2f94a210311` v0. The next authorized synchronization is 23:30 UTC. Twenty untouched vendor CRLF files retain their raw bytes; changed paths require exact preimages. Local work is limited to synchronization and commits.

The candidate now includes coherent changes after that baseline: static model declaration resolution; dataclass target and fixture corrections; virtual-thread result publication and callback ownership checks; live imported-call return classification; rooted bound-signature copying; an owned fsync export; and compiler/assembler lifetime reductions. The local-priority 50889ccd design remains intact. The conflicting generic dynamic-constructor and Make alternatives were not restored. The pending capture's exact validation belongs to its own receipt; earlier results below do not certify these combined bytes.

The memory changes emit and retire plans one function at a time, clear consumed lookup owners, and represent assembler instructions as spans in the existing assembly string. An explicit consuming API releases stack-map plans after packing their independent bytes. Ordinary callers retain the non-consuming default. An opted-in caller's plans may remain consumed if a later encoding or validation step fails. Strict root, relocation and packed-map validation remains enabled.

## Actual Stage1 boundary

The unchanged 21:01 source built and admitted a fresh 189-member runtime for Linux x86_64, threads=1 and atomic reference counting. Its archive SHA256 is `2e6bea2ab07915240fbc9fcc2f8d16723cadfc52e7bac437e106ea178f974c75`; the archive is Library `libfile_a2ca404060388191bfa112cccc3d989b` v0.

The maintained Stage1 command then exported all 453 modules. Its first CLI object stopped after 334.32 seconds at a 4,319,125,504-byte process-tree peak under the unchanged 4 GiB diagnostic guard. It completed zero objects and never linked. The worker accounted for 4,098,277,376 bytes and its parents for 220,848,128 bytes. The captured module contains 3,379,765 supported records, 544 functions and 2,803,774 instructions, with zero fallback records and no LLVM text rendering. All exported inputs, invocation and failure receipts are Library `libfile_75e38a6016048191b612e4f609a6cd5f` v0.

The V3 lifetime repair emitted the actual retained CLI object: 174,341,672 bytes, SHA256 `783580af35ab7a23dc315c05e6a558ab7894e6d747afb696172ab159c46ad225`. Every function assembly hash and plan digest matched the earlier attempt. This standalone run's worker peak plus the original parent overhead still exceeds the original process-tree limit. It is object proof, not an original Stage1 memory pass. Source, tests, object and failed attempts are Library `libfile_187fe5e0d91c8191bd6cbcb526163623` v0.

V4's consuming-plan change has 139 scoped passing checks and an independent source review. Its same-input replay includes an actual resident 220,848,128-byte reserve to model the observed parent overhead. At this update, all 544 functions and the identical 431,596,717-byte assembly have completed parsing; encoding remains active. The observed 4,115,963,904-byte peak includes the reserve. This modeled diagnostic is not the full original Stage1 invocation. Actual V3-to-V4 source and tests are already durable in Library `libfile_606523e4930081919abe015e69f57b27` v0. Original Stage1 replay, linking and pcc1 smoke remain required.

## Causal repairs and remaining native work

On the unchanged 21:01 compiler and its fresh runtime, the prepared constructor passed all five requested collector settings without observed collection events. Acquisition advanced beyond its old time boundary and failed compiling page_url ownership. HTML produced the correct inherited-private value and passed GC0 through GC3, then failed keyword dispatch under GC4. The writer advanced beyond optional-parent construction and failed because ordinary os.fsync was absent. These original inputs remain unchanged.

The candidate repairs each shared producer. Virtual-thread spawn now publishes an immediately rooted result; live imported-call analysis follows the emitted managed binding; bound-signature copying retains each value across moving allocations; os.fsync uses the existing owned syscall, errno and warning paths. These changes have causal host/reference/IR evidence, with actual native replays pending a matching runtime. Acquisition still imports urllib.request, which has no owned provider; that remaining capability gap must not be described as working HTTP acquisition. Native fsync integer-subclass behavior and GC4 signature relocation remain unqualified. Source capsules are `libfile_dab9a50d11348191a0bf3d1105c542cd` v1, `libfile_adacec8c52488191b6ef8030d78630ce` v0, `libfile_6e61aeac71f88191975e79b5153dd8bb` v0, and `libfile_a4b7d8fda91881919d73e9d12d48e1a8` v0.

## Source-bound validation history

The 21:01 capture's affected inventory contained 1,451 nodes: 1,212 passed, 98 failed, five were resource-blocked, 60 native nodes were deferred and 76 were unrun. Its 98 failures comprised 74 named-constant model-loader failures, 16 target mismatches, six host-binder setup errors and two call-IR assertions. The postcapture model repair passed 222 checks on the candidate. The separate dataclass repair passed all 24 formerly failing cases; its complete inventory has 96 passes and 38 native deferrals. Those source changes do not retroactively change the sealed counts.

The unchanged 21:01 source subsequently passed all four original constructor object-emission targets under a justified 1 GiB guard, with peaks of 428–507 MB; its full-class string IR check and two companions also passed. These are cross-target objects and host IR checks, not execution on foreign platforms. Full C/Python collection found 30,510 nodes in 1,758 files, with 30,285 selected and 225 explicit deselections and no errors. Fixed coverage retains 302 PCC and all 28 gateway files. Collection is not execution. All 16 async and 410 gateway nodes remain unrun on the newer combined candidate.

The earlier 19:35 compiler `e11949301e50d0ccdf710f961118f7e22c0b351a72af1c5bcd9ee94ac740a2ef`, with runtime archive `c8b430d001ef2330ba5e636443a757e5c29765018ed04b454e365c3c8dd419e5`, passed all 16 original async tests and both original C programs through owned compilation, linking and native execution. Its 410 gateway nodes ended with 391 passes, 16 missing-pcc1 prerequisites and three compilation failures. The passes were 387 host/model checks, three owned C socket probes and one external OpenSSL reference. No gateway Python product execution passed. Actual outputs and ledger are Library `libfile_875177d5463c819192a4f907b78246e5` v0. These are historical, source-bound results.

The historical full 446-module diagnostic emitted 432 objects and had 14 compiler failures without linking. A later retained dashboard module passed the shared stack-map accounting repair while preserving strict caps and root validation. Neither establishes a current whole-compiler or application pass. Detailed run catalogs remain outside the repositories.

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
