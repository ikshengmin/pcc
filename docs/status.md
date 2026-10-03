# Current status

Updated October 3, 2026. Maintain this page in place. [Project Intent](project-intent.md)
and [compiler contracts](compiler-contract.md) remain the requirements; focused
review repairs do not replace the original ten goals.

## Source and recovery boundary

A cloud filesystem reset lost work after the October 2, 19:20 UTC backup,
including generated compiler/runtime artifacts and unuploaded test receipts.
The three source repositories were restored from verified Library bytes matching
the pre-reset synchronized commits: PCC `351c617eb27979ecaad6c96bc94a38b98fbbd444`,
gateway `83577bc41dc79d3ead2e217bfa04f0f500799012`, and GUI
`3843a75a5634dfb3107890a2d628517713782080`.

The first authorized hourly sync completed October 3 at 04:46 UTC from the
immutable 04:33 source capture: PCC `1a891b352b6d6c53b28d7ae2b846ebf7771388ed`
(39 paths), GUI `45b84f1d1b112c4a88cc9d5a6772077a2f78bb7c` (10 paths), and gateway
unchanged. All 49 before/after hashes and modes were verified locally, with no
push. Subsequent changes remain unsynchronized until the next capture.

The exact PCC base `1a59d09df54cc0b7b46219395d3419cab8543675` was verified
across 29,601 Git blob bytes and modes, followed by all 154 paths in the backed-up
forward delta. This restores source content, not Git history or qualification.
The candidate includes reconstructed later changes, each with actual before/after
bytes, modes and hashes outside the repositories. Historical passing results
cannot qualify these recreated changes.

Hourly local synchronization is authorized from October 3, 04:40 UTC. Include all
legitimate source work, including failing or unqualified work, in one commit per
changed repository, preserve unrelated local edits and do not push. The accidental
`work-in-progress` directory is removed with its bytes preserved outside source.
Scratch files, backups and duplicate handoffs must stay outside the repositories.

## Fresh evidence and active repairs

CPython 3.15.0rc1 and locked pytest dependencies are restored in an isolated
environment. Fresh post-reset results are tied to their recorded source hashes;
later changes require the affected checks again:

- Compiler/capture/producer matrix: 107 passed, 1 failed, 28 deselected. The
  original integer-constructor case exposed an unowned repr(self.value) result
  in a generated dataclass __repr__. A shared repr/ascii producer repair is now
  reconstructed and its 13-case causal gate passed, including the unchanged
  original program and strict rejection checks. Two native cases are pending.
- Callable metadata: 20 host/source-model checks passed, covering rooted
  construction, default lifetimes, callback representation and helper contracts.
  The corrected py_func and py_class components emitted strict owned objects
  in 38.98s / 339 MB with the codegen checksum unchanged. A whole matched archive
  and native execution remain pending.
- Installed-wheel isolation/provenance: 15 checks passed, including a real
  isolated interpreter import fixture. This does not qualify a native wheel.
- Runtime receipt producers: 25 focused/controlled Make cases passed, including
  missing/null/configuration/stale-source/codegen rejection. The producer files
  were unchanged; an early broad preflight snapshot preceded a separate metadata
  annotation correction, so no whole-candidate stability is claimed for this gate.
- Explicit default/threaded/integration runtime fixtures: 61 host cases passed
  with 873 production/fixture hashes and modes unchanged, including actual
  archive-byte tampering and MZ-prefix handling. This is no native Windows proof.
- GUI Harness: 147 host tests passed with all 32 input hashes unchanged. Native,
  TLS, reference application, interaction and pixel gates remain open against
  DeepSeekHarness `47f943859bef60e4160492346772ded9b24f765a`.

Reconstructed compiler changes include transitive closure captures, hidden
capture operands, persistent default roots, pooled-name ownership, and shared
Call/Subscript/BinOp result publication. Runtime list/tuple concatenation and
mixed numeric/reflected-add dispatch have code and formal tests, but no fresh
native qualification. Live class namespace and bound-method delegation remain
partially unreconstructed. Runtime configuration/inventory producers and explicit
fixture provenance are freshly checked at their host/component boundaries.
Darwin implicit stream symbols passed 25 host/owned-object checks. Five narrow
atomic instruction forms passed 41 encoding checks; narrow atomic lowering and
cmpxchg layout passed 91 focused/codec cases. These are not actual Darwin or
AArch64 execution. C parser/abort and thread logging remain active reconstruction
or qualification work.

The fresh complete compiler graph audit finished at 04:49 UTC: all 441 unique
modules completed, with 120 production indexed-lowering passes and 321 strict
frontend failures, no timeouts or crashes. Source and graph identities were
reverified unchanged: source 3c4c5fd022b650c3f12a857ecdd2805a5ac9dcbf35c637cef0b8aa14a14cb302,
graph 0d4c6c15d62a197acd3cfe8e1865f392078407bbc51139890f8491dd464186e1.
The watchdog recorded 830.310 seconds and 828,022,784 bytes peak tree RSS.

First-failure families are Call ownership/publication (254), arithmetic proof
(20), BinOp publication (14), Name ownership (14), set receiver (7), registered
roots (7), CPython dirpath (2), Lambda (1), BoolExpr (1), and foreign-base super
(1). These are first failures per module, not independent bugs. Shared format,
container, ordinary boxed-arithmetic and class-global publication contracts are
being migrated consistently; validators remain strict. The historical 237/204
counts came from lost earlier source and are not a matched comparison.

After these repairs, regenerate the graph and rerun all 441 contexts before the
next whole-runtime/bootstrap freeze. No current pcc1 or pcc2 exists, and no Stage2
to Stage3 fixed point is established. Later Darwin/atomic candidate edits were
outside the frozen graph and need final integrated qualification.

## Qualification order

Use the checked-in Python version, isolated outputs, durable node logs, the shared
heavy-run lock and a process-tree RSS watchdog. Disable surprise native builds
with PCC_NO_AUTO_PCC1=1 and PCC_TEST_NO_NATIVE_PROVISIONING=1. Every pytest
diagnostic uses -x -n0 -vv --tb=short. Timeouts guard runaway processes; they are
not acceptance budgets. Freeze source, configuration and artifact identities.

Finish causal producer checks and the fresh full graph audit, then build a matched
whole runtime. Execute changed-shape programs under all five GCs, original C/Python
controls, and complete tests/c and tests/python inventories. Report passed, failed,
deselected, unavailable and unobserved nodes separately. Run dependent bootstrap
stages in order through scripts/bootstrap.py; only successful Stage2 and Stage3
raw-byte equality establishes the fixed point. See [validation workflow](validation-workflow.md).

## Ten goals still open

1. Native pcc0 to pcc1 to pcc2 to pcc3, byte-identical Stage2/Stage3, original
   C/Python execution controls, and no libpython.
2. Equal semantics and production correctness/concurrency/performance for all
   five collectors. Include original GC4 getter/iterator/capacity failures,
   relocation, FRESH_ALLOC, finalizers, weakrefs, resurrection and long-run RSS,
   throughput and stability under the shared authoritative root/slot contract.
3. Actual macOS ARM64, Linux x86_64, Windows and Linux aarch64 jobs, including
   Linux aarch64 Stage1 through Stage3. Preserve macOS-15 standard three-core M1,
   7 GB and 45-minute qualification, plus installed wheel/version parity.
4. Owned compiler/runtime/ABI/provenance, native runtime self-build, bindgen,
   lockfile, CLI/API/diagnostics and real function implementations, without LLVM,
   host C compiler or hidden external-owner fallback.
5. Complete EDG-inspired semantic facts, one layout authority, layered native
   validation, compilation-memory targets and centralized target ABI coverage.
6. Successful comparable Stage2 no slower than Stage1, and shared builds for
   all five collectors within six hours.
7. Complete C and Python suites and authorized cleanup, including external TLS,
   BSD archives, freestanding assertions and partial-async timeout cases.
8. Gateway semantics and measured performance beyond asyncio with identical
   inputs, long-running behavior and traceable configuration.
9. Tail calls, multiply-add, peepholes, scaffold identity and PCC_LOG=thread,
   with native semantics and measured effects.
10. GUI/Harness native app/five-GC behavior, HTTP/TLS, Loader HMR, typed FFI/UI
    bridges, interaction and pixel equivalence against the pinned reference.
    Host models and cross-compilation do not prove device execution.

## Durable evidence

The recoverable base delta is Library `libfile_85beb2beb8f88191b47144ac4fa719ea`.
Reconstruction code and receipts are versioned under
`libfile_a5f7c11281008191be4ea89ea9611cbd`; version 10 contains the October 3,
04:54 code checkpoint. Archives contain recoverable code bytes and a verified
baseline/incremental chain, not only recipes or remembered hashes. The cloud
recovery directory is pcc-reset-recovery-20261003 outside the source repositories.
Save every coherent batch before heavy tests and preserve failure evidence.
Old generated binaries, runtimes and the 441-module graph must be rebuilt;
historical hashes do not recover bytes.

The fresh graph inputs are preserved as Library
`libfile_e4d21adda870819187f2218b0924cb1c`; all 3,975 terminal node/guard/artifact
files are in `libfile_ba4713127d888191996585e2a8a70dd3`. Both archives passed
hash/mode readback. The completed hourly delta is
`libfile_2b94b861f9048191a0f2c3272ccab2a9`; its exact captured source, not later
candidate changes, is the next synchronization baseline.
