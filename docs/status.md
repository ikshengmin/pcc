# Current status

Updated October 3, 2026, 21:15 UTC. Maintain this page in place.
[Project intent](project-intent.md) and [compiler contracts](compiler-contract.md)
remain authoritative. Focused repairs do not replace the original ten goals.

## Current boundary

There is no qualified current pcc1, pcc2 or pcc3, and no Stage2/Stage3 fixed point.
The latest complete compiler-context census, V8, uses the immutable 20:15 capture,
Stage1 source `90bb894ee2b5ee9dc0b4cbed2543679dfb0dde85cd79e1bda64d21e8b83d4ecb`.
All 441 freshly exported contexts are accounted: 383 pass, 57 fail in the
frontend, and one reaches its worker timeout. Source, graph, original indices,
receipts and output hashes were independently reverified. Graph SHA256 is
`4ed613cc0ad947858ad8642bfe864517a8abe3f37fafdec18c23c409dbdce43a`.
Elapsed time is 1,705.50 seconds and peak process-tree RSS is 3,160,305,664 bytes.
Other diagnostics overlapped; these are diagnostic observations, not build
performance or native acceptance.

Compared with V7, 361 passes remain passes, 22 failures pass, 56 failures remain,
one pass becomes a failure and one prior failure reaches its 120-second worker
cap. The single new regression is native_text_modules: the added regex flag
helper's lhs.value | rhs.value reaches the unchanged arithmetic ownership gate.
It is the first regression target for the shared bitwise producer/callee repair.
The timed-out cli_bootstrap module advances beyond its old frontend error into
__nested_read_labeled_report; its partial output is preserved, not classified
as a semantic failure. Both prior V7 regressions, macho_spec and
integer_fold_contract, now pass in the fresh V8 graph.

Later sum, valueclass, receiver, cleanup and runtime-algebra repairs are outside
V8 and require another integrated census. The original classgen field-contract
file passes unchanged on the 20:15 source plus its one-file receiver repair;
175 distinct focused and adjacent host/IR cases pass. Sum has stable paired
fail-before/pass-after results for both original affected module contexts.
These scoped results do not qualify the current full compiler.

The earlier quadratic cleanup expansion is repaired with lexical/LIFO owners,
while generator-resume roots stay in their frames. Original module 242 passes
in both V7 and V8. The generic pending-exception cleanup repair must preserve
that linear-growth contract. Strict ownership validators remain enabled; the
three principal source/result admission gates are unchanged in V8.

Remaining families include bitwise and other managed-result publication,
iterator/call producers, source bindings and ownership across compatibility
bridges. No earlier source's passing result qualifies later bytes. Complete
C/Python execution, native bootstrap, all-five-GC production/performance and
actual four-platform qualification remain open.

## Matched runtime and native evidence

The latest completed native control boundary uses Linux x86-64 source
`addad00416e5a8e9f35382212ea54c1c75c9f7fb8276a074ddc616546a44d63c`
and compiler `74eeb69090b34c658d65db675c392d15342ec086f510f050c09c5ead9704240b`.
All 186 fresh runtime members are admitted, with threads enabled and atomic
refcounts. Archive SHA256 is
`5a3ef5ba0e2198e3aa9feb387bd1a7b0192e500717891ab994f4fd040559a17e`.
Source, target, configuration, inventory and compiler checks passed.

The four unchanged original Python programs (failed-class cleanup, mixed-numeric
addition, division and module-loop binding) plus the unpack lifetime and
weakref/finalizer exception-identity controls pass all 30 program/backend pairs.
A separate sidecar verifies the actual GC identities 0 through 4 for every pair.
Both unchanged C controls pass. There are no failed, skipped or missing variants
in this bounded run. Its complete source, runtime, executables, raw GC logs and
receipts are Library `libfile_35d6618ab21c8191bf05200582840b16`, archive SHA256
`e26831b8514a2d7d8d921614ee8e4dee6d07ea60ffca42875f750f542080b54a`.

This causal source differs from its admitted predecessor by five compiler files
that retire synthetic tuple-unpack owners and preserve pending exceptions during
reentrant cleanup. The unchanged original module-loop assertion fails on that
predecessor and passes here. The predecessor source `ffece4b8` independently
passes the real two-thread forced-STW/held-lock probe on all five observed
backends, with nested no-park depth, balanced leases, invalid/successful snapshot
paths and eventual parking/resume. That separate proof is Library
`libfile_d97ddc8325488191b995be439665a167`. It does not establish full collector
stability or throughput, and is not silently attributed to later source.

Historical V5 source `b8aee32d` admitted 184 runtime members and recorded 27 of
31 Python groups passing all five requested settings, with four original failures
and explicit witness limits. It also passed two original C controls, six abort
cases, 60 thread cases and two migrated SSA short-circuit C controls. Detailed
outcomes and collector observations remain in its dated recovery archive;
these historical totals do not qualify the current candidate. Earlier mixed
replacement-object experiments likewise remain diagnostics, not matched-source
causal proof.

All these programs were emitted by host pcc0. There is still no qualified
current pcc1, pcc2 or pcc3, fixed point, complete C/Python execution or final-source
acceptance. Later unary, sum, valueclass, receiver and generic cleanup changes
require their own integrated runtime/native qualification. Strict archive
provenance must reject the older archive for those newer compiler bytes.

## Active repairs

The strict ownership validator remains enabled. Shared producers must publish
managed results to authoritative output roots before parking cleanup and reload
through those roots after relocation boundaries.

- The original module except-as regression is reproduced across the old source
  boundary and repaired through its registered module-global binding. The four
  unchanged target object cases and the matched native lifetime control pass.
- Set construction now has coherent compiler/runtime result-owner changes and
  30 focused checks. An original macho_link context advances past its frozenset
  constructor to a separate sum producer. Actual immutable frozenset semantics,
  new runtime components and native qualification remain open.
- Environment lookup now evaluates and roots key before default, publishes the
  result before cleanup, and returns an owned default on a miss. Its 32 focused
  checks and original pipeline-context advancement pass. This changes the
  ownership ABI despite an unchanged symbol signature: an old borrowed-default
  runtime must be rejected. Native proof requires a rebuilt matching runtime.
- List/tuple constructors, iterator ownership, bytes caller publication, sorting
  and factory-allocation failure handling now have 250 host/model/reference
  checks. Sorting preserves stable reverse order and roots comparator inputs.
  A later buffer-owner repair has 134 host/model/reference passes and production
  library IR proof. It repairs memoryview base ownership and publishes sequence
  results before temporary-buffer disposal; native five-GC proof remains open.
  Dict.get now publishes through caller output roots and has 87 integrated
  checks. These source batches are newer than the last matched runtime.
- The later exception-class Name regression is reproduced on unchanged Linux
  matrix sources: five baseline passes became four passes and one failure.
  Copying from the authoritative exception cache slot restores all five. A
  separately recorded target-plumbing fixture correction allows the 07:35
  compiler and repaired compiler to pass all 20 four-target expression cases. An
  exact reconstruction of the retained pre-fix closure reproduces 16 passes and
  four matching Name failures with that same corrected fixture. The repaired
  whole host/object boundary has 108 passes, including all 14 exact class-attribute
  cases; 41 archive-dependent native cases remain unexecuted.
- Remaining shared families include dynamic branch merges, native OS wrappers,
  valueclass boxing, arithmetic value-kind proof and authoritative Name bindings.
  The complete diagnostic retains original AST/callee failures for causal replay.
- Metaclass ownership has 10 host/IR checks. Class tag allocation has a shared
  CAS owner, reserved-tag exclusion and exhaustion cleanup with 14 host checks.
  The old bodies reproduced a collision with reserved tag 200 on allocation 97,
  C-extension-range overflow and duplicate allocation under controlled
  interleaving. Four source-matched runtime components now emit the required i32
  tag ABI and metaclass/namespace operations. Later metaclass retirement changes
  have 27 body-model checks. Their two changed runtime components now emit the
  required lock, retirement-plan and unlock sequence. Native contention,
  exhaustion and lifetime qualification remain pending.
- Namespace transaction support has a freestanding component with zero calls in
  its locked helpers. The later namespace writer API has 36 body-model checks;
  legacy writers/readers, raw callbacks and managed method-selection ownership
  remain incompletely migrated. No production safety conclusion follows from
  support-only tests.

The completed dynamic set/update tranche has 196 focused checks. Replaying all
14 original affected V6 contexts gives nine PIDX passes and five distinct later
producer failures, with no remaining original receiver-slot diagnostic. Native
qualification remains open. Dict constructors/fromkeys now have 128 focused
checks; an introduced ValueBox dispatch regression was independently reproduced
and repaired. Eight original contexts give two passes, four later failures and
two resource stops; that replay precedes only the final compatibility guard.
The runtime fromkeys helper's internal rooting is still unqualified.

Scaffold owner dispatch now uses declared semantic capabilities and native export
metadata. Its affected-file matrix records 115 passes, two attributed preexisting
failures and one resource-incomplete case. The remaining failures are a class_gen
Call-source producer and a boxed-integer ABI expectation. Field inventory checks
now name the audited helpers/callers and preserve provenance assertions.

Off-tree proposals and integrated but unqualified work remain distinguishable
in the retained code batches. Lambda adapters and generic unary operands now have 35 strict scope/CFG checks.
The existing operand-root file is corrected to distinguish generic dispatch from
exact-int proof, and all 24 cases pass, including the unchanged constructor
pipeline. Runtime callback cleanup and unsupported callback semantics remain open.

## C and ABI work

Named declarations, layout/pointer/signature caches and deferred backend
consumers now retain module-owned type context. The repair has 226 scoped checks
and two owned Linux late/nested-layout executions. A broader stage1-context node
previously timed out and a native codec node was deselected; neither is passed.
The full original Stage1-context test now has terminal results. The reviewed
f3a0430 snapshot generates IR for 9 of its 47 targets and records 38 codegen
exceptions (113.3 seconds, 755 MB peak). Source frozen at 12:13 generates IR for
13 and records 34 exceptions (126.2 seconds, 834 MB peak), with four newly passing
targets and no new failing target. Both tests fail. Negative fallback counts are
existing exception sentinels; they and the strict ownership checks are unchanged.
These contextual tests do not execute a native compiler.

Darwin stream resolution now covers implicit use, explicit file/block externs,
owned-header macros and local/translation-unit definitions. Its 114 focused
checks include actual owned Mach-O undefined-symbol inspection. Actual Darwin
execution remains unrun. Narrow AArch64 atomics have encoder/lowering evidence,
not native AArch64 qualification.

Six C corpus product adapters previously forced host assembly/linking through
`run_translation_units_with_system_cc`. Their old passes cannot prove owned
product execution. A test-only migration now routes product programs through
public owned compile/emit APIs while preserving original C sources/assertions
and the external reference oracle. Eighteen host route checks pass. Six native
entries using unchanged sources across five product adapters also pass against
that oracle, with the product guard permitting only PCC-owned executables.
Complete corpus execution is pending. Csmith generation is unavailable here.
Its product adapter now feeds the original generated source and include directory
to PCC's own preprocessor; the external compiler remains the separate oracle.
Twenty route checks pass. Two original SSA short-circuit programs also now use
the owned helper, with their source, optimization, jobs, timeout and assertions
preserved; 25 focused route/SSA checks pass. Both later execute successfully
against the explicitly admitted V5 archive, as recorded above; they are not
current-candidate native proof. The default and explicit-self pointer-initializer
cases now use the same owned product route. Their exact C source, constructors,
assertions, O2/jobs=1 and timeout are preserved. Two new route guards and 22
existing guards pass; these pointer programs' native executions remain unrun.

The Make fixture incorrectly paired Linux ELF probe objects with the host archive
utilities on every platform. A target-aware host fixture and explicit NM override
now pass all 31 cases: the original 25 strict Make/provenance cases and six owned
cross-format archive/tool checks. The old Make's ignored NM setting produces an
empty Mach-O symbol inventory with GNU tools; strict admission rejects it. Native
macOS execution has not been performed.

Historical reviewer counts of five C root errors and 45 failures/16 csmith
failures are attributed, unresolved counts. A precise five-node ledger was not
retained, and they are not current candidate totals. Full source-bound collection
and execution must establish the actual remaining failures.

## Thread diagnostics and optimization

Lifecycle, scheduler-lock, safepoint and stop-the-world events, plus an independent
lock-held suspend tripwire, are integrated. Completion handoff, paired suspension
records and complete-record buffering have source-bound causal tests. On the
admitted historical Linux threaded/atomic 184-member runtime, the unchanged
native matrix passes all 60 cases and an independent original-program GC2 stress
passes 20/20. All 25 thread-enabled matrix cases contain 24 worker identities
and at least 24 matched suspension pairs. The collector settings in that matrix
lack event witnesses. Earlier timeouts and truncated-log failures are retained;
the final source/configuration result does not qualify the newer candidate,
logging overhead, all-GC concurrency or long-run stability.

AArch64 peephole liveness now preserves MOVK/BFI/BFXIL destination inputs and
W/X aliases, release-store operands and indirect-branch uses. The exact helper
matrix changes from 81 mismatches in 84 cases to zero; 254 distinct host/helper,
encoder and precise-stackmap tests pass. Existing MADD/MSUB eligibility and
production routing are unchanged. Target execution remains open.

The bounded tail-call helpers now create a preheader and simultaneous parameter
PHIs, preserving changed arguments, swaps and non-entry labels. Frame/root,
exception and unsupported ABI shapes remain unchanged. All 77 focused host
checks pass, including the public opt-in route and independent semantic probes.
The accumulator helper remains unwired. Native constant-stack behavior,
production optimization performance and broader optimization goals remain open.

## GUI, gateway and remaining goals

The pinned Harness reference is restored at
`47f943859bef60e4160492346772ded9b24f765a`. All 7,412 Git blob hashes and modes
match tree `f904efab9ef435201d6ba4da88a34d6366568272`, with no missing/extra
files or submodules. Reference recovery is not behavioral or pixel parity.
The reconstructed GUI Loader/session tranche has 147 host checks; actual native
GUI, HTTP/TLS, HMR, typed FFI/bridges, interaction traces and pixels remain open.

All ten acceptance goals remain active:

1. Native bootstrap through byte-identical Stage2/Stage3, with original C/Python
   controls and no libpython.
2. Equal production correctness, concurrency, throughput and long-run memory
   behavior for all five GCs, including relocation and GC4 capacity contracts.
3. Actual macOS ARM64, Linux x86-64, Windows and Linux AArch64 qualification;
   the required macOS-15 job uses the standard 3-core M1/7 GB/45-minute envelope.
4. Owned compiler/runtime/ABI/provenance throughout, including runtime self-build,
   bindgen, lockfile and consistent CLI/API/diagnostics.
5. Complete EDG semantics and compilation-memory targets.
6. Stage2 no slower than Stage1 and the five-GC shared build under six hours.
7. Complete C and Python execution and the remaining cleanup goals.
8. Gateway semantic and performance goals beyond the asyncio reference.
9. Deferred tail-call, multiply-add, peephole and other planned optimizations,
   scaffold identity and complete thread diagnostics.
10. Complete native GUI/Harness behavior and visual equivalence to the pinned
    reference.

Cross-object emission does not establish execution on another platform. Missing
platform execution must remain explicit until a suitable environment is arranged.

## Source recovery and synchronization

The reset recovery restored verified source bytes from the last backed-up
October 2 boundary. Historical passing results never qualify reconstructed
bytes. Recoverable current code, before/after patches, source identities and
execution artifacts are retained outside the repositories and backed up durably.
No generated backups, scratch tests or work-in-progress directory belong here.

The last confirmed local source baseline is PCC
`5c72b625c927205d2d868bfbd479a10744b852fd` (20:15 capture), GUI
`45b84f1d1b112c4a88cc9d5a6772077a2f78bb7c`, and gateway
`83577bc41dc79d3ead2e217bfa04f0f500799012`. All 14 PCC paths passed local
before/after hash, mode and diff checks. Later cloud repairs remain unsynchronized.

The latest synchronized immutable capture is October 3 at 20:15:06 UTC,
containing 14 PCC paths against `f4d83e8`. Exact-source execution records 1,413
distinct passes, four completed failures and one separate resource-incomplete
attempt. The original 47-target Stage1 context test completes with 37 IR outputs
and ten codegen errors under the unchanged 1.5 GiB safety cap (298.032 seconds,
1,539,309,568-byte peak). No targets newly fail or recover compared with 19:17;
all ten error texts are byte-identical. The original assertion still fails.

The other completed failures are class_gen's direct append_basic_block dispatch
assertion, the imported valueclass aggregate ABI assertion, and an existing
dict-copy cleanup IR assertion reached by a separately selected host subset.
Class_gen now completes lowering beyond both repaired bridge/format failures.
The valueclass failure has a later separate repair for constructor payloads and
exported class identity, with 38 focused checks; one larger original case remains
resource-limited and native qualification remains open.

The changed container test module contains unmarked native cases. Its first
native case unexpectedly started owned runtime construction despite the test
provisioning flags, then reached its 300-second guard without a native outcome.
The remaining 17 cases were unexecuted in that attempt. The separate five-case
host subset passed one case and failed the next cleanup assertion; three remained
unexecuted. All original captured source bytes and modes stayed unchanged.
Generated runtime artifacts and its stopped lock owner were retained outside the
source snapshot, and the complete source inventory was reverified. The provisioning
bypass is being repaired; no partial runtime admission or native pass is inferred.

The complete inventory contains 25,962 nodes: 25,737 selected and 225 capability
or environment deselections. The independent integration inventory selects 5,553
from the same union. Both have zero collection errors/skips. Collection does not
execute test bodies. All 37 contextual IR files are separately backed up as
Library `libfile_9d741b07b8a08191b7bf92ffd9c95fc3`; the synchronization packet
records their hashes and source identity.

Historical captures remain separate: 19:17 recorded 1,685 passes and four failures;
18:25 recorded 1,544 passes and six failures; 17:21 recorded 1,780 passes, one
completed failure and one resource-incomplete context node. Later scoped repairs
do not rewrite those receipts, and differing selected groups prevent a simple
comparison of passing totals.

Recoverable code bytes, before/after patches and detailed historical receipts
are retained in the external PCC recovery archives in Library. Code archive
`libfile_a5f7c11281008191be4ea89ea9611cbd`, version 41, contains the 17:35 code
capture. The V6 census input/receipt archive
`libfile_b31d99b80ce88191bb44580e4307ddcd` includes complete original generated
AST/exports and all phase/failure/resource receipts; successful PIDX payloads
are separately retained and are not included in that archive.

Hourly synchronization includes legitimate integrated work even when failing or
unqualified. Run collection and affected fast tests on the exact capture first;
report failures and unexecuted cases instead of treating sync as acceptance.
The local executor only applies and commits one change-based commit per changed
repository, preserves user edits, and does not develop, test or push. Promote a
capture to the next baseline only after its actual local commit is verified.
Later mutable changes remain separate until the next capture.

Use the qualified CPython 3.15.0rc1 environment, isolated outputs, source/config
identities and resource watchdogs. Coordinate heavy runs, forbid surprise
compiler/runtime provisioning, and stop each independent diagnostic at its first
real failure. Safety timeouts are not acceptance budgets. A pre-launch lock rejection now
records a terminal receipt instead of leaving a stale RUNNING state; all 17
watchdog tests pass, including owned-child reaping under the unchanged one-byte
memory cap. Preserve failures and continue other independent authorized work.
