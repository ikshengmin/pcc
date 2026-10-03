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

The second hourly sync completed at 05:42 UTC from the immutable 05:35:57
capture: PCC `208f806c40e5b3b79deb926b761bf038e44d711a` (23 paths), with GUI and
gateway unchanged. All before/after hashes and modes were verified; there was no
push. Later candidate changes remain unsynchronized until the next capture.

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

Two complete, fresh compiler graph audits are preserved. V1 finished with
120 indexed-lowering passes and 321 first failures. V2 finished with 167 passes
and 274 first failures across all 441 unique modules, with no timeouts or crashes.
V2 source is 4c655275e6d5d25f56e78fc43e10a037e3a6d938c9bf2f8dd6d05f20bb3a7a14;
graph is 01bd87ba833da60059cc41988d9666f0c6461683fa6d379444660ca03ced9125.
The source/context were reverified unchanged; watchdog time was 942.838 seconds
and peak tree RSS 908,316,672 bytes. Matching module names gives 59 fail-to-pass,
108 pass-to-pass, 262 fail-to-fail and 12 newly exposed strict failures. These
are first failures per module, not independent bug counts or native proof.

V2 first failures concentrate on Call handoff (118), Call ownership (43),
BoolExpr (31), arithmetic proof (24), BinOp handoff (18), Name ownership (14),
set receiver (7), valueclass attributes (3), and 16 smaller root/bridge/base-class
cases. Producer migration and unchanged strict validation continue together.
The earlier historical 237/204 counts came from lost source and are not a matched
comparison.

After V2, the real selected-owner BoolExpr path passed 21 focused checks; two
unchanged original contexts advanced to separate os.uname/join producer failures.
Ordinary class-namespace lookup passed nine focused cases and fully generated
LLVM text for original py_ast module116. That replay did not enable the audit's
PIDX environment; it establishes host IR lowering only. Prepared custom namespaces
remain unchanged and unqualified. The one-argument type producer is integrated
with its focused/component gate still pending.

The managed binary-call result owner repair passes 33 host models. Component
lowering exposed a map-literal and then managed-versus-raw helper-signature error;
those narrow corrections are integrated and its current component retry is active.
Incoming method lookup/replacement ownership remains a separate known gap, so
native overloaded arithmetic is not qualified by these models. Public Linux abort
is integrated after two syscall models and two owned cross-object checks; six
native signal-state cases remain pending. Four standalone owned-linked x86_64
varargs programs passed; these do not qualify a complete runtime or C suite.

The next approved execution boundary is one immutable compiler/runtime freeze,
a fresh threaded atomic Linux runtime, and native format, BoolExpr, type,
ordinary class-default lifetime and original C controls. Source-matched CPython
oracles for the three producer programs pass. No current pcc1 or pcc2 exists;
there is no Stage2 to Stage3 fixed point. Further complete PIDX censuses and the
full native/compiler gates remain required on the final combined candidate.

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
`libfile_a5f7c11281008191be4ea89ea9611cbd`; version 18 contains the October 3,
06:30 code checkpoint. Archives contain recoverable code bytes and a verified
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

V2 inputs are Library `libfile_b94df145020c819185824ff43dc3e8b6`; the complete
4,035-file terminal evidence is `libfile_7bec6b7a854c8191a540cb39c13c0c0b`.
The verified second hourly delta is `libfile_ef1cd16a09148191b6898141b26678db`.
Current backups also contain an exact integrated-source delta separately from
unapplied proposals, so an off-tree proposal cannot be mistaken for candidate code.
