# PCC current status

Updated 2026-10-07 at 15:12 UTC. Captured compiler: `b0a661035abb5714b73081315f40ddf878f1f8c54deeefbd53d1033beb4b2cb0`. Canonical source now combines valueclass rebinding ownership, annotated variadic carriers, dynamic mapping construction, guarded live imported dictionary methods, Path text-helper newline defaults, and actual bytes/text-family strip dispatch. All ten goals remain open.

## Source preservation and local baseline

The verified local baseline remains PCC `dce4f3c7d3b8803b0ba1771e33103fdaad6fcb7d`, gateway `05fe10b57897dfcff19a75b41b7c9630436b32d8`, and GUI `16c6ecfca3e576b5bef302071325c0f8dd8bc549`. No source packet after that baseline is reported as locally synchronized. The original six-path Library item `libfile_1a32885e3c14819199bc4f026536d419` v0 remains blocked by official local GET 403 failures. The later nine-path e6 recovery backup `libfile_7d2c1b8d1bdc81919b5590ed0e321ea4` v0 remains immutable. No access recovery, local retry, commit or baseline promotion is implied by this new backup.

Exact cumulative changed bytes, preimages, modes and complete inventories are preserved outside the repositories in `pcc-protocol-source-backup-20261007T1530Z/pcc-forward-delta/receipts/`. Unchanged vendor files use verified immutable baseline references. Separate source/evidence backups cover mapping (`libfile_ef104b89b8f48191b794a59153fe0fb9` v1), imported dictionary guards (`libfile_3598ed9cb0708191b88cae48232a2f3e` v1), Path defaults (`libfile_7847a1c4d62881918af28f42379966db` v1), and bytes-strip dispatch (`libfile_dc186bcc00d88191b504e8281c428ad1` v0). Reservation, encoder and checkpoint optimizations remain private and unintegrated.

## Current repairs and exact execution limits

Live imported Dyn dictionary methods now enter the existing type guard before generic callable lookup. Path.read_text/write_text use the standard `newline=None` default and preserve explicitly supplied values. Dynamic strip/lstrip/rstrip choose the bytes or text helper from the actual receiver, with ordinary method lookup, argument order, exceptions and owning slots preserved. The corresponding formal regression files are captured without relaxing assertions.

The original d526/f37 Stage1 produced 456 objects and linked an owned pcc1, then failed the maintained native smoke compile. Subsequent mixed-codegen diagnostics crossed the mapping boundary using replacement 132, the imported-get boundary using consumers 17, 86, 89, 99 and 134, and the newline-default boundary using provider 413. These are dated causal witnesses rather than a fresh current-source compiler qualification.

The next original failure was archive bytes.rstrip returning str before decode. The repaired complete native binary-header/strip/decode chain and protocol passed, and original consumer 217 re-emitted successfully. The eight-object diagnostic image linked, but the original native smoke reached its outer diagnostic guard at 15:06:10 UTC after 240.110 seconds (return code 124; peak 639,938,560 bytes). Its worker completed 989 owned records with zero fallback, then the downstream main process published no ELF and reported no exception or ownership fault. Emitted smoke execution and Stage2 remain unrun. The 240-second outer guard is a diagnostic limit, not a performance acceptance threshold or evidence of deadlock; the maintained barrier has no per-smoke timeout. The fourth original smoke boundary is not established by this timeout. Changed compiler owners 21, 123, 267, 287, 297 and 311 are not replaced in that diagnostic. A successful consumer diagnostic cannot imply that its native compiler implements all new compiler-source changes.

No matching b0 runtime or fresh b0 Stage1 has been qualified. Standard admission of the older f37 runtime against changed codegen remains rejected; explicitly identified mixed diagnostics stay separate. Current native regression fixtures, complete sixteen async nodes and 410 gateway nodes execution, original smoke execution, Stage2/Stage3 and platform/performance acceptance remain open or unrun. Earlier annotation-reflection and explicit-newline limitations retain their original source identities. This backup records exact-source host execution and collection separately, preserves failures and unrun nodes, and does not treat source preservation as a pass gate.

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
