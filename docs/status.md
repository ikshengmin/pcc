# PCC current status

Updated 2026-10-05 at 17:06 UTC. All ten requested goals remain open. The candidate contains a coherent 56-file source/test increment; this status refresh is the 57th changed path. Host evidence is partial. Matching-runtime native execution, the original failed-module replays, a complete pcc1 and the Stage2/Stage3 fixed point remain pending.

## Current source and synchronization

The verified local baseline is PCC `70bf5ca0772e53eb9c43170bb434f648d20a2ae9`, gateway `05fe10b57897dfcff19a75b41b7c9630436b32d8`, and GUI `16c6ecfca3e576b5bef302071325c0f8dd8bc549`. The preceding synchronization applied exactly 15 PCC paths with raw bytes and modes checked; it ran no local tests and performed no push. Twenty untouched vendor checkout files differ from canonical Git blobs only by line endings: preserve their raw local bytes and modes. Changed-path preimages still require exact raw identity. Automatic source synchronization is authorized every two hours from 17:30 UTC. A prepared packet is not a verified local commit; promotion requires its actual local receipt.

The 56 integrated paths include 14 new files. They add owned os.walk and scalar-error ownership, bin/hex/oct NEW-result handoff and shadowing, an owned html parser, POSIX immutable-bytes positional writer/providers, compact stack-map physical bounds and merge handling, ELF packed relocations/zero-initialization/chunked publication, prepared-namespace roots, and two justified assertion repairs. The global FRAME-abort proposal and later dir, host-adapter, lexer and warning proposals remain excluded. No scratch outputs or runtime binaries belong in the source delta.

The integration at 17:04:50 UTC verified every sealed postimage against the candidate. Source, preimages, postimages and gate receipts are retained outside the repository in `pcc-next-qualification-merge-20261005T1645Z`; `integration-receipt.json` records the actual integration. The durable coherent source batch is Library `libfile_22b4deab597881918b3776ca0d35292f` v0, archive SHA256 `3c1a33843d96ddc789d38085371d2cbf08431da2d98451641252a74cd83c1f86`. The forthcoming immutable source capture will preserve this status and the complete source identities before validation; later validation receipts are additive.

The local-priority merge remains intact: local `50889ccd` and compatible cloud work were combined in `fb37c530`; conflicting cloud Make and generic dynamic-constructor changes were excluded. Local `88fb2256` and `50889ccd` are siblings. Recovery archives are Library `libfile_f3b7c1eb94708191ab5b60fa10b5af30`, `libfile_67b3259a0e648191b8f6772bf0dd3245`, and `libfile_9a7cdd9d55c48191a00dfc8ba169b404`, each v0. The verified `70bf5ca0` baseline source capture is `libfile_75966379d8c481918e0190e51e088b7d` v0.

## Evidence and current limits

The private merged source recorded 434 host passes, two preserved assertion failures, 22 later selected nodes unrun because their invocations stopped at the first failure, and 29 integration deselections. The authoritative counts are `gate-counts.json` and `gate-summary.json` in the merge archive. The failures are `test_prepared_namespace_reuses_method_signatures` and `test_pcc_stdlib_from_import_constant_stays_native`. Their original assertions remain visible. These are bounded CPython 3.15 host/frontend gates (60 seconds and 256 MiB per group), not native runtime qualification. Current-capture collection and whole-file host execution must retain every affected node and report failures, deferred native cases and unrun nodes separately. Subsequent -x continuations must identify exactly which previously unrun nodes they execute.

The prior 15:37 source capture ultimately recorded 227 passes, 11 failures and 22 native deferrals among 260 affected nodes. All 11 failures reproduced on the preceding S3 source; this does not classify them as introduced by the prior 15-file delta. That evidence does not qualify the 56-file increment.

The preceding S3 assembler-updated source (compiler checksum `e9ec97c0744677343b8c27862a68b93b8aef0cbf9bfd3ba131843a2d4f1567b1`) built all 188 owned runtime objects in 453.62 seconds at 344,928,256 bytes peak tree RSS. Its strictly admitted 75,499,128-byte archive has SHA256 `60f6d1c47fd6e74f5946676bcdd8f3c6e0d4c58852501e28d732706f2a6e461a`. The complete mixed-local and range/reversed changed-shape files passed 14 and 11 tests, including actual GC0–4 witnesses. Exact source/runtime/native receipts are Library `libfile_e767ccda665081919e92ac221d2b3738` v0. These results precede both the inherited-default/URL increment and this new batch.

All 16 original async/await tests passed only on the earlier v2 source, before the assembler increment. Its full run used an explicit continuation for the two nodes unfinished at the initial timeout. Source, native programs and outcomes are Library `libfile_6e306b1a5ad081918c891ff6202c9736` v0; its owned runtime/provenance are `libfile_8ed5ffa75a508191a7c5c2cee2d41cd2` v0. These are host pcc0/self-backend compilations followed by native ELF execution without libpython. They are not native pcc1 compiler execution, current-source async qualification, general five-GC qualification or gateway integration.

## Original compiler boundary and memory

The immutable S3 446-module owned-object diagnostic terminated at 17:02:14.397 UTC after starting at 14:02:54.881 UTC. It produced 432 owned ELF objects and 14 ordinary compiler failures in 10,759.516 seconds, with 3,989,438,464 bytes peak process-tree RSS under the unchanged 4 GiB cap. There were no resource-limit or timeout/harness failures. Its successful objects total 3,481,697,144 bytes. Linking was disabled: no pcc1, original cold Stage1, or fixed point was produced. Exact inputs, outcomes and receipts remain in `pcc-cloud-runs/final-owned-object-cohort-elf-20261005T1401Z` outside the repository.

The 14 failures comprise shared walk ownership (2), URL (1), inherited defaults (2), html (1), base formatting (1), writer (1), stack maps (2), dir reflection (1), host adapter (1), mixed-domain lexer lookup (1), and foreign-super `__new__` in datetime (1). This next source batch does not claim those 14 failures closed. Each repair still needs the unchanged original module/context replay, followed by the matching native/runtime boundary.

The original cold Stage1 previously stopped at the preprocessor Boolean/list storage mismatch after 1,665.64 seconds and peaked at 4,292,632,576 bytes, only 2,334,720 bytes below 4 GiB. The generic repair now emits that original object with the strict verifier and zero fallback. The new local wide-integer, half-precision and dynamic-allocation modules also emitted real owned ELF objects in full context with zero fallback. This remains object evidence.

The assembler lifetime repair passes 95 small controls with identical Linux/Windows object bytes and owned execution. The full original CLI same-input replay passed in 381.47 seconds at 3,879,014,400 bytes peak tree RSS; its 361,209,059-byte assembly and 133,489,920-byte object exactly match the controls. Small code/proof is Library `libfile_96fbc1b13b80819197cb206ca6348808` v0, with full receipts in `pcc-cloud-runs/x86-symbol-metadata-20261005T1240Z`. The new ELF representation/publication changes still need full-link qualification; earlier headers-only relocation counts were a representation risk, not an observed full-link OOM.

## Other retained work

Inventory classification passed its complete 14-test file. Owned-builder settings passed all 46 checks. Earlier combined Make timeout groups passed separately (86 and 18 checks). Warning/import publication remains an unintegrated proposal with 28 focused host/frontend passes and no qualified original native/lifetime matrix; recovery code is Library `libfile_615be611b3a08191a7b3818f7270adf6` v2.

The synchronized GUI repair preserves disabled Harness group realms and consumer identity through re-enabling and releases owners on removal. All 160 Harness host tests passed. The reference is pinned to `47f943859bef60e4160492346772ded9b24f765a`, with 7,412 blob identities/modes verified. Upstream TypeScript, native GUI, five-GC GUI and pixel equivalence remain unrun. Code/proof is Library `libfile_45f62b905b008191b0cfdea91f6d6211` v2; reference source is `libfile_58a31a6af50c8191ab5a07f142ffc3aa` v0.

The fixed validation inventory preserves the original complete async file, every gateway file and the stable affected-file baseline. Collection is not execution. Current-source full async and actual gateway execution remain pending, as do the full original C/Python suites. Historical frontend censuses and the user's earlier C-suite results do not qualify this source.

## Ten-goal acceptance remains open

1. Bootstrap: produce pcc1, run original C/Python controls, then establish a no-libpython Stage2/Stage3 byte fixed point.
2. Five collectors: finish correctness, concurrency, relocation, GC4 capacity, FRESH_ALLOC, long-term memory, throughput and stability.
3. Platforms: execute macOS ARM64, Linux x86_64, Windows and Linux aarch64 qualification, including the standard macOS 15 3-core M1 / 7 GB / 45-minute job and wheel parity.
4. Ownership: complete compiler/runtime/ABI/provenance, stubs, bindgen, uv-lock and CLI/API consistency without product cc/LLVM/libpython fallbacks.
5. Semantics and memory: complete the stated EDG semantics and compilation-memory targets. External i128 register-edge ABI, x86 dynamic alloca and two-dimensional VLA remain explicit gaps.
6. Build performance: prove Stage2 no slower than Stage1 and a shared five-GC build under six hours. Current diagnostic timings do not establish these targets.
7. Tests and cleanup: execute the full original C/Python and integration inventories; finish TLS, archive, freestanding and asynchronous timeout work.
8. Gateway: qualify real native network/TLS and structured concurrency, then establish performance beyond asyncio. The completed async file is one component.
9. Optimization: qualify tail-call, MADD, peephole, scaffold identity and thread-logging behavior and performance.
10. GUI: complete the native app, five-GC, HTTP/TLS, Loader/HMR, typed FFI, interaction and pixel requirements against the pinned Harness reference.

Push and the future CI repair loop require both the ten-goal prerequisite and an explicit instruction to start.
