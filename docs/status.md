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

The third hourly sync completed at 06:45 UTC from the immutable 06:36:46
capture: PCC `62c0bdad8fc4fabf9da795759dde1dec1a31a144` (17 paths), with GUI and
gateway unchanged. Before/after bytes and modes were verified locally. This exact
capture remains preserved. The fourth sync used the 07:35 capture and produced
PCC `8ed4eabec0ddc2505570ba6b413f88de0964ff76` (9 paths). The fifth completed
at 08:51 from the exact 08:31 capture: PCC
`9c834d952d4b8b582b7cdfef818ba91478132295` (18 paths), with GUI and gateway
unchanged. That fifth capture remains preserved. The sixth sync completed at09:56 from
the exact09:46 capture: PCC `f4d02d9dc01372889c7e40d0f52d6315970d58cc`
(45 paths), with GUI and gateway unchanged. It recorded385 fast passes and one
failed frozenset producer case, with17,753 selected and5,726 gate-deselected
collection nodes and no errors. The09:46 capture is the current verified baseline. Its fast checks
recorded 169 passes and two failures; collection had no errors. Local work only
applies and commits the source delta; validation runs in the cloud.

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
  in 38.98s / 339 MB with the codegen checksum unchanged. The subsequent matched
  whole archive passed strict admission; native execution remains pending.
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
remain unchanged and unqualified. The one-argument type producer passed 19
focused checks, with four native cases still pending. Original type contexts
advanced to separate dict.get and constructor-handoff producer failures.

The managed binary-call result owner repair passes 33 host models. Component
lowering exposed a map-literal and then managed-versus-raw helper-signature error;
those narrow corrections are integrated. The exact optimized IR emitted an
owned 1,881,160-byte object with SHA256
e86035918c8d21f4aef023780807dd01cc70139dc3d3e1384b847c2d32acada6.
Incoming method lookup/replacement ownership remains a separate known gap, so
native overloaded arithmetic is not qualified by these models. Public Linux abort
is integrated after two syscall models and two owned cross-object checks; six
native signal-state cases remain pending. Four standalone owned-linked x86_64
varargs programs passed; these do not qualify a complete runtime or C suite.

A fresh threaded atomic Linux x86_64 runtime finished at 07:09 UTC from frozen
source c3ade842a23fa0863027d7cb7d3f56b223d8b155296366c9965274e04bd47fe2.
All 183 ordered members and the real threads=true/refcount=atomic configuration
passed strict source, policy, codegen and archive admission. Build watchdog time
was 431.33 seconds and peak tree RSS 428,994,560 bytes. Archive SHA256 is
61ff10c984181c233b92e5eb224ff3dbe5d23df39af5cfc73de6df4bd571da58.
The first staging attempt failed because its directory was not recognized as a
runtime library source; a byte-identical recognized staging path passed. Both
receipts are preserved and the compiler/runtime inputs remained unchanged.

All six prepared Python controls compiled and linked against the frozen runtime.
Format, type, ordinary class-default identity/lifetime and the exact restored
indexed_payload.py each passed GC0 through GC4 with exact output and empty
stderr: 20 native passes. BoolExpr temporary disposal order failed GC0 at the
unchanged event-order assertion; class-construction failure cleanup failed GC0
at the unchanged weakref-release assertion. Their other eight collector runs
remain unobserved. Retained original IR is being traced for the owners that keep
these objects alive. Assertions and original source programs remain unchanged.
The original GCC 20000223-1.c also compiled, linked and executed with exit 0 and
empty output. Other original C and abort signal-state controls remain pending.

Earlier format attempts stopped at test-harness guards rejecting host PCC export
and object worker subprocesses. Exact-target dispatch preserves the full source
closure. Eight narrow guard checks passed before allowing only verified frozen
PCC object workers, rejecting arbitrary subprocesses and external tool fallback.
All results here are host-pcc0-produced native programs. No current pcc1 or pcc2
exists and there is no Stage2 to Stage3 fixed point. Further complete PIDX
censuses and native/compiler gates remain required on the combined candidate.

PCC_LOG=thread instrumentation has been reconstructed in the logger and pthread
kernel, with 22 fresh host-body checks passing and all three tested file hashes
unchanged. Tests exercise filtering, disabled fast path, event codes, allocation
and platform errors, lock/TLS order, join and both detach disposal orders. Native
component/thread/five-GC execution and overhead remain unqualified. Property
accessor construction owners are also integrated with their separate focused
gate pending; these later candidate changes are outside the frozen runtime above.

The October 3 review reproduced a real exception-binding regression: all four
unchanged module-handler object-emission cases pass on the 04:33 synchronized
source and fail on the 05:35 source. The module except-as binding repair now
uses its registered module-global slot and clears it on handler exits; its fresh
checks are pending. The two ordinary native lifetime failures above are separate
preexisting behaviors and are not attributed to that regression.

Named-type declarations, layout/pointer/signature caches and deferred backend
consumers now carry their owning module context. The integrated parser repair has
226 scoped host checks and two owned Linux late/nested-layout executables passing.
A broadly selected full-stage1-context node timed out and one native codec node
was deselected; neither is qualified. Explicit Darwin stdio declarations remain
an off-tree proposal awaiting validation.

Scheduler-lock, safepoint suspend/resume and stop-the-world events plus an
independent lock-held suspend tripwire are integrated. The logger uses a raw
syscall sink outside the scheduler/world locks. There are 127 unique focused host
checks and four owned component emissions passing. Their frozen source predates
later parser integration; a complete matching runtime and native five-GC run are
still required. The freestanding class-namespace callback split is integrated
with its new host/component checks pending.

The unchanged four-target module-handler regression now passes. Canonical object()
construction and weakref semantics are integrated; host checks pass but a native
component-to-executable attempt stopped at source-identity preflight after parser
files changed. It must be replayed against an immutable compiler. The finalizer
original-IR diagnostic passes GC0–4 with explicit matching threaded/atomic build
settings, but combines source/compiler revisions and cannot establish a complete
matched-runtime result. The earlier diagnostic also had a threading mismatch;
its observed passes are retained without attributing them solely to the fix.

Full C/Python collection on the immutable 08:08 capture completed without errors:
23,354 discovered nodes, 3,027 C and 14,605 Python selected, and 5,722 gate-deselected.
No test body executed in that collection. All 29,634 source identities stayed
unchanged. Every later hourly source capture must run its own collection and fast
affected regressions before the local commit. Preserve failing work in the sync,
report exact failures and unexecuted cases, and use commit subjects describing the
actual changes rather than synchronization workflow labels.

A new complete Linux x86-64 threaded/atomic runtime, frozen at09:59:56, passed
strict admission for all184 ordered members in437.79 seconds, with439MB peak.
Its source identity is `3b1592b4aa8c7c409669e938740717ee96d3002718623cedfdf3a68d3ec94c59`
and archive is `e9f501eddc9984e63d04da3de5365a43fb5f8364332ec0e1f5cf15602e2599e4`.
Eight unchanged Python controls passed all five requested GC settings (40 runs).
Separate unchanged-binary sidecars confirmed actual backend selection for35 of
those runs; indexed-payload emitted no collection event in its five runs. The
ordinary failed-class cleanup case still fails GC0, leaving its other four
variants unexecuted. Two original C programs and all six abort cases passed.
These are host-pcc0-produced native controls, not a native compiler fixed point.

The thread diagnostic passed archive/sink/settings admission but its program
stopped at a raw getenv return annotation before any of60 executions. The fixture
now declares c_rawptr explicitly; its native replay remains queued. A fresh441-
module diagnostic is running on separately frozen10:20 source. Later metaclass
owner changes passed10 host/IR cases but are outside the09:59 runtime. The owned
Darwin external-stream resolver passed114 host/object cases, including real
Mach-O symbol checks; actual Darwin execution remains unrun.

The pinned Harness reference was recovered from the official archive at
`47f943859bef60e4160492346772ded9b24f765a`. All7,412 blob hashes and modes match
Git tree `f904efab9ef435201d6ba4da88a34d6366568272`; this is reference recovery,
not behavioral or pixel parity. Corpus C helpers still route product assembly
through host cc in several suites; migration to the owned executable API is
underway, preserving external reference oracles and original C programs.

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
`libfile_a5f7c11281008191be4ea89ea9611cbd`; version 20 contains the October 3,
07:24 code checkpoint. Archives contain recoverable code bytes and a verified
baseline/incremental chain, not only recipes or remembered hashes. The cloud
recovery directory is pcc-reset-recovery-20261003 outside the source repositories.
Save every coherent batch before heavy tests and preserve failure evidence.
Old generated binaries and graphs were lost; newly regenerated graph and runtime
bytes are preserved below. Historical hashes alone do not recover bytes.

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

The verified third hourly delta is `libfile_c3a466e41e9c8191a95f9d2f2732be8b`.
The admitted matched runtime and its frozen compiler/runtime source, all 183
member objects and IR/provenance, and build receipts are recoverable from
`libfile_bc542c184fa48191a0d8bb964e443e3a`: 2,005 files, 55,383,935 archive bytes,
SHA256 77ee0582d6adeafdc2c55de729899710345c831eed136cdc26e0dfcc0e4951b3.
Archive readback verified every included file hash and mode. Native execution
receipts remain a separate boundary.
