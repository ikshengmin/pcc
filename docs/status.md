# Current status

Updated October 3, 2026, 17:20 UTC. Maintain this page in place.
[Project intent](project-intent.md) and [compiler contracts](compiler-contract.md)
remain authoritative. Focused repairs do not replace the original ten goals.

## Current boundary

There is no qualified current pcc1, pcc2 or pcc3, and no Stage2/Stage3 fixed point.
The latest complete compiler-context diagnostic finished at 15:01 on source
`b8aee32d012ae42c5ef87fef7ac20916f9bd39a00535158f5b7a4c0dafb91767`.
It regenerated all 441 contexts: 278 lowered successfully and 163 stopped at
their first frontend failure, with no timeouts or crashes. Source and context
identities reverified. These are shared producer-contract failures, not 163
independent bugs. The run took 1,388.22 seconds with 1.65 GB peak process-tree
RSS while other bounded diagnostics and archival work overlapped; it is not a
build-performance acceptance result.

Compared with the previous 214/227 diagnostic, 65 failures became passes,
213 stayed passing, 162 stayed failing and one previously passing context
failed. All 17 regressions from the earlier 190/251-to-214/227 transition and
all 41 gains from that transition now pass on this single combined source.
The one new regression was pipeline_import_scan: a dynamic count() call
returned an unowned branch result before strict subtraction. Its producer is
now repaired, with eight focused checks and an unchanged original-context PIDX
pass. The validator remains unchanged. Remaining families
include multiplication/repetition, OS/path result publication, imported
constructors, namespace bindings and string-method result handoff.

The earlier 41-context preservation run used source `333f9be7`: 40 passed under
a 512 MiB diagnostic cap, while py_parse passed a separately authorized 1.5 GiB
recheck after its initial memory-limit stop. Its 1.35 GB peak and byte-identical
v4 PIDX are retained; this is not memory-target acceptance.

The preceding complete diagnostic used source
`c2b078540e55363d779beba964b8430460184cfb02844e50cf7addadbdaa6108`,
frozen at 12:27, and produced 214 passes and 227 failures. Later mutable-source
repairs have separate focused evidence. The next complete source was frozen at
16:18 as `d396b3e1c42f84a313f3f099036bfb90f308b706a7cb3c5b8a51abf1ba2dc633`;
its retained-source diagnostic accounts for all 441 modules across two phases:
350 pass, 90 fail in the frontend, and one (call_expression_lowering) reaches
the original 120-second worker limit. The initial 30-minute outer stop and all
partial receipts are retained. A single-worker diagnostic recheck of that one
module is pending. V5-to-V6 has 75 new passes and three frontend regressions:
dataclass-factory callable, bytes.decode handoff and floor-division feeding
repetition. The first two have separate repaired original-context proofs;
floor-division repair is active. None of these counts proves native bootstrap. Neither complete diagnostic emits or
executes a native compiler. The next compiler boundary requires a green fresh
complete diagnostic and actual native bootstrap on the same final source.

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
candidate. Class mortality and full class/base/MRO/registry retirement remain
open. The newer candidate also adds two repetition runtime modules and requires
its own complete matching archive.

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
preserved; 25 focused route/SSA checks pass. Their native executions await the
new matching runtime and are not included in the older six executions above.

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

## Thread diagnostics

Lifecycle, scheduler-lock, safepoint and stop-the-world events, plus an independent
lock-held suspend tripwire, are integrated. Raw sink component checks exclude
allocation, registration, implicit safepoints and indirect calls; explicit safe
wait polls are retained. Actual per-module frontend settings were recorded in
the complete runtime build.

The first native attempt stopped before execution at an ambiguous raw getenv
return declaration. After the fixture declared c_rawptr explicitly, compilation
passed. Five GC0 mask cases passed; the sixth, thread+gc, produced the correct
program output but failed the preserved suspension/resumption assertion. Its
log contained four suspend records, zero resume records and 49 reported drops.
The remaining 54 cases were not run.

Independent try-lock delivery can separate the two records of a completed
suspension. A paired-delivery repair after world unlock, with zero no-park depth
and reentrancy checks, has a separately rebuilt complete 184-member archive.
All 18 GC0/GC1 mask/format cases pass, including the previously failing pair check.
The first GC2 case, with logging disabled, timed out at the unchanged 20-second
limit; 41 later cases were not run in that original matrix. Separate baseline and
repaired binaries both pass a GC2 replay, so the intermittent timeout remains
unattributed. Independent slices pass all 15 tripwire cases and all nine mask
cases under each of GC3 and GC4. GC labels here are requested settings without
an actual collector-event witness. The original stopped receipt is preserved.
Potentially locked observation paths remain nonwaiting. Native concurrency,
complete five-GC coverage and logging overhead remain unqualified.

A later alternating GC2 stress stopped after seven passes at another 20-second
timeout. Main-only phase markers then localized a baseline-pass/repaired-timeout
comparison to the first join, after stop/resume completed. The observed futex/TID
states match a source hazard where done is published before unregister/teardown
finishes. A narrow completion-handoff repair has 72 host cases, including an
old-source failure at the blocked-teardown interleaving. Its complete 184-member
archive is now strictly admitted, including transitive proof that the raw tail
after unregister cannot register, safepoint, allocate or make indirect calls.
The original native matrix passed 22 cases, then failed strict parsing of a
truncated final GC2/thread resume record after normal program completion; 37
cases remain unrun in that receipt. A separate original uninstrumented stress
slice passed 20/20 with requested GC2 and logging disabled at the unchanged
20-second bound. Complete-record buffering has 93 host cases and a separately admitted complete
184-member runtime. On that exact Linux threaded/atomic source, the unchanged
native matrix passes all 60 cases and a separate original-program GC2 stress
passes 20/20. All 25 thread-enabled matrix cases contain the 24 application
worker identities and at least 24 matched suspension pairs. The earlier failed
GC2/thread case now has complete JSON and 47 matched pairs. Both transitive raw
tail and sink/batch IR checks pass. GC labels remain requested settings because
no actual collector-event witness appeared. These results do not qualify the
newer combined candidate, all-GC production behavior, performance or pcc1.
Buffering does not promise transactional durability if forced termination occurs
between actual partial OS writes. All predecessor failures are retained.

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

The latest verified local synchronization used the immutable 15:19:46 capture:
PCC `71e15ea62ceadbfeb716824ba0e7d5d8cdf95b10` (15 paths), GUI
`45b84f1d1b112c4a88cc9d5a6772077a2f78bb7c`, and gateway
`83577bc41dc79d3ead2e217bfa04f0f500799012`. Exact hashes, modes and diff checks
passed. Its scoped execution records 1,189 passes and three failures: the
original Stage1-context test (22 IR outputs, 25 codegen exceptions), sys.prefix
passed to path join, and the first original C short-circuit native test blocked
by the explicit no-provisioning guard. Its second native test was unrun. A later
frozen V5 control proves those C programs through the owned route; it does not
supply this capture's missing matching runtime.

The all-test inventory has 24,706 nodes, 24,481 selected and 225 capability or
environment deselections. A separate integration inventory selects 5,529 from
the same union. Both have zero collection errors/skips and retain per-node
reasons. Neither inventory establishes test execution. Later namespace and
fixture corrections pass all 224 affected OS host checks, with three native
cases explicitly excluded. Those results belong to newer source.

The preceding 14:20:58 capture associated with `816fb03f` recorded 1,152 passes
and one contextual failure, with 22 IR outputs and 25 exceptions. Its inventories
contained 24,540 nodes, 24,315 selected by the all-test command and 5,521 selected
by the integration command. The earlier 13:26 capture retained a default
non-integration marker expression; its 5,742 deselections therefore combined
marker selection and capability gates. Historical counts are not interchangeable.

The 16:20 source capture contains 37 PCC paths and records 1,426 passing
checks with one resource-incomplete contextual test. Its 47-target assertion
stopped at the 1.5 GiB diagnostic cap, retaining 19 IR outputs and six error
records. The recorded synchronized baseline remains `71e15ea6`; later source
changes have separate manifests and scoped results.

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
memory cap. Preserve failures and
continue other independent authorized work.
