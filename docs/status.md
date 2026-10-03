# Current status

Updated October 3, 2026, 19:06 UTC. Maintain this page in place.
[Project intent](project-intent.md) and [compiler contracts](compiler-contract.md)
remain authoritative. Focused repairs do not replace the original ten goals.

## Current boundary

There is no qualified current pcc1, pcc2 or pcc3, and no Stage2/Stage3 fixed point.
The latest complete compiler-context census uses source
`28828d463503cc4cac9bbdf5e525ed8f30cceb9846b8e852324b5a099ba5de1f`.
All 441 freshly exported original contexts are accounted: 362 pass and 79 fail
in the frontend, with no timeout or memory stop. Source and generated input
identities reverified. The run took 1,746.84 seconds with 1,715,986,432-byte peak
process-tree RSS while other diagnostics overlapped; this is not build-performance
acceptance or native execution.

Compared with V6 (350 passes, 90 frontend failures, one resource-incomplete),
V7 preserves 348 passes, restores 13 failures and the resource-limited module,
and introduces two frontend regressions. Those are bytes.split feeding decode
in macho_spec and abs feeding floor division in integer_fold_contract. Their
shared producers now have separate causal passes on both original V6 and V7
contexts. Current macho_spec encounters a newer earlier dict(zip(...)) producer
gap, so those causal passes do not qualify the mutable whole module.

The earlier resource failure exposed quadratic IR expansion: short-lived call
roots were enrolled in every function return cleanup. Lexical/LIFO lifetime
repair preserves exceptional cleanup and keeps generator-resume roots in their
frame. The final original-module replay passes under the same 2 GiB/120-second
guards (35.04 seconds in the worker), and module 242 passes in V7. Six growth/root
balance checks and 232 related host/IR/model cases pass; native qualification is
separate. Strict ownership validation remains enabled.

Remaining families include managed Call/BinOp publication, iterator producers,
source bindings and CPython/native bridges. Newer dict/file/regex and Constant
repairs have bounded original-context evidence but are outside V7. The next
complete census and native bootstrap require another stable integrated source.
No earlier source's passing result qualifies later bytes.

## Matched runtime and native evidence

The Linux x86-64 runtime for source
`b8aee32d012ae42c5ef87fef7ac20916f9bd39a00535158f5b7a4c0dafb91767`
is admitted with all 184 members, threads enabled and atomic refcounts. Its
compiler checksum is `2da4f0c36d8bd6402630a86aed3ea9965a2beb61b92f2d8cb38001dbf241d0b4`
and archive hash is `126a3c35f178b1e74fc1d99d59e628e23dcc4f469365f25639efd1867eb18d3d`.
Input identities remained stable. Construction took 510.41 seconds with
439.8 MB observed process-tree peak; overlapping diagnostics prevent a build
performance claim.

Its native controller is terminal. Twenty-seven of 31 Python groups pass all
five requested GC settings. The four failing groups are retained: failed-class
mortality under GC0, a zero-iteration module-loop target that fails to raise
NameError under GC0, mixed-numeric addition blocked by ComplexLit publication
at compile time, and division's zero-divisor assertion under GC0. The later four
GC variants in each runtime-failing group remain unrun. Original C controls (2),
abort cases (6) and thread cases (60) pass. Separate collector observations
record 112 verified selections, 26 unobservable cases, 12 skipped variants and
five compile-failed variants. These observations do not replace program outcomes
or establish all-GC production, concurrency or performance qualification.

Two additional unchanged SSA short-circuit C tests pass through their migrated
owned helper with optimize=True and the same admitted source/runtime. Their
executables exit zero with empty stdout/stderr. The wrapper's two existing
TYPEOF parser warnings are recorded separately.

The newer ComplexLit repair passes 88 host checks and makes the unchanged
mixed-numeric source emit an owned ELF object. The module-loop bound check
passes 14 host/reference cases; its native fixture remains unchanged. A bool
layout repair removes reads beyond header-only singleton globals. An isolated
two-member runtime comparison reproduces the old division failure and passes
all five requested settings with the correction, using unchanged original IR.
That comparison has no production receipt and does not qualify the combined
candidate. A failed-class construction repair now detaches the unpublished definition's
namespace owner while preserving the shell, MRO and escaped/metaclass behavior.
Its 42 focused checks pass; the unchanged native Token-finalization assertion
still requires the new archive. Full class/base/MRO/registry retirement remains
open. The candidate also adds two repetition runtime modules and therefore
requires its own complete matching 186-member archive.

A newer diagnostic freeze, source `243185b2` with compiler `2be03662`, emitted
all seven changed/new runtime components. Its preflight then rejected an injected
safepoint poll in the new locked heap-copy helper; no full 186-member archive or
native controls ran. Counted no-park handling may suppress parking, but source-body
witnesses also reach GC3/GC4 heap-barrier allocation under nested graph lock.
A staged scratch-root snapshot now moves tuple heap stores and retirement after
unlock. The retry source `ffece4b8`, compiler `312d6631`, contains exactly five
changes from the preceding freeze. Its seven-object preflight passes unchanged
logical lock/ownership requirements, including restricted kernel calls, scratch
clearing, no poll/allocation/indirect call and ordinary wrapper polls. The full
186-member archive is building; original native controls and a real forced-STW
held-lock probe are pending. The original failed gate remains retained.

These binaries were produced by host pcc0. They do not establish native pcc1,
Stage2/Stage3 fixed point, complete C/Python execution or final-source acceptance.

Earlier replacement-object experiments are retained as mixed diagnostics.
Their compiler/source closure differed from the original archive; one early
finalizer experiment also used the wrong threading setting. Observed passes
from those experiments are not source-only causal or matched-runtime proof.

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
`3f995a3dd73d901370c7cc5efed0bf6738ce40b0` (18:25 capture), GUI
`45b84f1d1b112c4a88cc9d5a6772077a2f78bb7c`, and gateway
`83577bc41dc79d3ead2e217bfa04f0f500799012`. Promote a later capture only after its
actual local application and commit are verified.

The latest synchronized immutable capture is October 3 at 18:25:16 UTC,
containing 31 PCC paths against `9290368`. Exact-source execution records 1,544
distinct passes and six failures, with no resource-incomplete group. The original
47-target Stage1 context test completes with 37 IR outputs and ten codegen errors
under the unchanged 1.5 GiB safety cap (275.41 seconds, 1,537,331,200-byte peak).
It still fails its original assertion.

The other failures are the attributed class_gen Call producer, boxed-int ABI
expectation, pointer native test blocked by the missing matching runtime,
incomplete re import alias and newly introduced ValueBox keyword-projection
regression. Later alias/ValueBox repairs are outside this captured source.
Tests remaining after each first failure and native cases remain unqualified.

Its full inventory contains 25,669 nodes: 25,444 selected and 225 capability or
environment deselections. The independent integration inventory selects 5,547
from the same union. Both have zero collection errors/skips. Collection does
not execute test bodies. The host phase explicitly excludes archive-dependent
native cases, and the mixed backend file selects only its original AArch64
peephole helpers.

Historical captures remain separate: 17:21 recorded 1,780 passes, one completed
failure and one resource-incomplete context node; 16:20 recorded 1,426 passes
and a resource-incomplete context node. The earlier `71e15ea6` capture recorded
1,189 passes and three failures. Later scoped repairs do not rewrite those
receipts.

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
