# PCC current status

Updated 2026-10-07 at 02:26 UTC. Captured compiler: `3962870a26df58cbfd84429b8908f593a401672fc5022d5c7eee2127ca3641a9`. The next source delta adds the exact getcwd machine ABI, canonical Windows errno typing and maintained regression coverage. Source synchronization is not a pass gate; all ten goals remain open.

## Verified baseline and recovery

The verified local baseline is PCC `3941e05d1be26cd8d18678b9a63e37a17cc106e1`, on parent `81c06463c6e2a5e625022ae39aaa37e064210aa3`. Its fifteen changed paths, including four new files, passed raw, staged and committed byte/mode verification with a clean worktree. All twenty untouched CRLF identities, Git configuration and comparison.json were preserved. Gateway remains `05fe10b57897dfcff19a75b41b7c9630436b32d8`; GUI remains `16c6ecfca3e576b5bef302071325c0f8dd8bc549`.

The 3941 source packet is Library `libfile_3e5ca88857408191a06cf3155e4c362b` v0. Full recovery starts at self-contained source entry `libfile_ec04a54cfe2c819196f3c58b55b02eaf` v0: reconstruct clean 9007, then apply the verified e111, 81c and 3941 packets before this delta, checking every full inventory. The exact chain, changed-file preimages, postimages and modes are in `pcc-getcwd-forward-20261007T0330Z/pcc-forward-delta/receipts/`. Source recovery does not recreate lost execution outcomes.

## Getcwd closure correction and qualification boundary

The maintained ABI registry now admits only the exact `getcwd(c_ptr, c_size_t) -> c_ptr` external declaration, including its caller-owned buffer and borrowed return contract. The existing Windows `pcc_errno_set` declaration uses canonical `c_int32`. All prior 121 ABI entries and Windows function bodies remain unchanged. The added regression rejects incorrect signatures, owners and name-only lookalikes.

The integration's two dedicated complete files passed all forty host/source controls. Across five original complete-file selections, it recorded 73 PASS, two call failures, one native setup error and twelve UNRUN under `-x`. The original platform filesystem test now crosses the former getcwd closure rejection and emits the relevant IR before failing because clang is absent. That is an external-reference infrastructure prerequisite. The original makedirs textual assertion still fails; its typed call was already present in historical comparison evidence. The unchanged Linux raw-syscall assertion still forbids a getcwd declaration and was not reached after the earlier failure. No assertion was weakened.

Integration source and receipts are Library `libfile_a6c2f878969481918c98d96c3ba0a6c0` v0 and inventory `libfile_d0d9c12f7338819198b2b73d91a516e1` v0, with all 121 archive members read back. Exact source changes, original failures and phase records are under `pcc-getcwd-integration-20261007T0217Z/`. These counts describe that component capture; the new packet records its own frozen-source attempts.

The packet retains the previous 382 whole files in the additive inventory and adds the getcwd contract file. Its requested execution scope retains all previous 37 affected whole files plus that file; the heavy scaffold file remains inventoried and queued while Stage1 owns the heavy lane. Original complete-file attempts use bounded host/IR checks, explicit no-provisioning controls, complete phase logs and fail-fast UNRUN records. Full C/Python and gateway collection is inventory only. Exact results and the execution continuation queue remain in the packet receipts.

There is no admitted runtime matched to compiler 396 and no current pcc1. Actual getcwd target execution, Darwin/Windows behavior, object/link qualification, all sixteen original async nodes and all 410 original gateway nodes remain required on matching inputs. Historical 70a async passes and gateway outcomes are retained in the preceding packet and confer no qualification on this source. The canonical module vars, owned-provider and builder corrections synchronized at 3941 retain their own unfinished native boundaries.

## Separate Stage1 interruption and recovery

As of 02:22 UTC, the necessary pool-retry lane remains frozen at compiler `4663933b53b4f2ee68b798403c7f6b7c88cd88d361a2964dafa11c54d9e87bfe` with its strictly matched 379c runtime. It has seven verified objects from a graph of 456 modules and no pcc1. Its source and build identity are separate from this getcwd source.

The original 01:11 attempt lost its executor transport with its guard still recorded as RUNNING. Its final raw sample supplies only a 1,153.156881-second observed wall lower bound and a 2,990,796,800-byte observed RSS peak. The first normal recovery was admitted at 02:15:33 into a private checkpoint store, then lost the executor key/transport after 74 samples and an 18.559229-second observed interval. It added no objects and did not reach runtime cache-hit confirmation. Both original interrupted receipts remain unchanged; neither is rewritten as a timeout or success.

Locks and exclusive log leases established that the interrupted writers were gone before recovery. A managed sixty-second continuity probe then completed. Recovery continues through the normal entry with the same 4 GiB tree cap, 2,400-second attempt guard and 1 GiB reserve, preserving the seven verified objects. Exact cumulative CPU, cumulative wall time and interruption endpoints remain unknown. Segmented recovery does not establish cold-build, macOS 45-minute or full Stage1 performance. Latest immutable boundary receipts are under `pcc-stage1-retry-launch-20261007T0103Z/recovery-20261007T0208Z/`; later progress belongs in a separate supplement.

## Retained evidence and open semantic boundaries

Previous source packets preserve the exact worker-retry progressive-growth A/B, canonical module vars controls, owned OS provider checks, verified builder-count correction, packed-span object identity proofs, sizing-memo comparisons and historical native attempts. Their source, runtime and execution boundaries stay explicit; component results and older source bands do not establish a current full-toolchain pass. Detailed historical records remain outside the repositories in the verified restoration chain instead of being repeated here.

Current native namespace rebinding and override behavior, native type bases, full CPython OS coverage, source-matched five-GC execution, current pcc1 and Stage2/Stage3 fixed point remain open. Local synchronization permits exact verification and commit only. Parent coordination owns dispatch and baseline promotion; local development, local tests and push remain outside this sync.

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
