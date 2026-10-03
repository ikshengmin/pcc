# Current status

Updated October 3, 2026, 13:58 UTC. Maintain this page in place.
[Project intent](project-intent.md) and [compiler contracts](compiler-contract.md)
remain authoritative. Focused repairs do not replace the original ten goals.

## Current boundary

There is no qualified current pcc1, pcc2 or pcc3, and no Stage2/Stage3 fixed point.
The latest complete compiler-context diagnostic regenerated all 441 module
contexts: 214 lowered successfully and 227 stopped at their first frontend
failure. It had no timeouts or crashes, and reverified every source/context
identity. These are shared producer-contract failures, not 227 independent bugs.
Relative to the previous 190/251 diagnostic, 41 modules advanced to passing,
173 stayed passing, 210 stayed failing and 17 previously passing contexts now
fail. Those 17 integration regressions are the next producer gate, with exact
old/new module identities retained. Preserve the 41 gains while repairing the
newly exposed input-call, subtraction/division, set-difference, lambda-return and
module-loop ownership paths.
Sixteen of those 17 original contexts now reach production PIDX on repaired
frozen compilers. The remaining canonicalize module advances past division to
later modulo/shift producers; their runtime owner repair is still in progress.
The 41 previous gains also pass on frozen source `333f9be7`: 40 under a 512 MiB
diagnostic cap, and py_parse in a separately authorized 1.5 GiB recheck after
its initial memory-limit stop. Its 1.35 GB peak and byte-identical v4 PIDX are
recorded; this is not memory-target acceptance. Later loop changes and the
complete combined current-source audit remain outside that preservation proof.

That diagnostic used source
`c2b078540e55363d779beba964b8430460184cfb02844e50cf7addadbdaa6108`,
frozen at 12:27. Later mutable-source changes have separate focused evidence.
The next compiler boundary requires another complete fresh-context diagnostic
and then actual native bootstrap on the same final source.

## Matched runtime and native evidence

The complete Linux x86-64 threaded/atomic runtime frozen at 09:59 passed strict
admission for all 184 ordered, unique members. It took 437.79 seconds with about
439 MB peak RSS. The recorded build used the owned compiler/emitter/archive APIs;
no host assembler/linker or libpython supplied the generated runtime.

- Source: `3b1592b4aa8c7c409669e938740717ee96d3002718623cedfdf3a68d3ec94c59`
- Compiler: `78d785e3766a5a8b71999210400c582fcbecd1d8c73d2b3663dcec9f3c72af29`
- Archive: `e9f501eddc9984e63d04da3de5365a43fb5f8364332ec0e1f5cf15602e2599e4`

Eight unchanged Python controls passed all five requested GC settings: canonical
object/base-subclass behavior, finalizer callbacks and resurrection, module
except-as lifetime, short-circuit destructor order, format, type, namespace
identity and indexed-payload behavior. Those are 40 native executions. Separate
unchanged-binary GC sidecars confirmed actual backend selection for 35 runs.
Indexed-payload emitted no collection event in its five runs, so its receipts
prove requested settings only.

The original failed-class cleanup program still fails under GC0 at
`references[0]() is None`; its other four GC variants remain unexecuted. Classes
are still made immortal, and complete class/base/MRO/name/registry retirement
semantics remain open. Two original C controls and six public-abort signal-state
cases passed against the matched runtime.

These binaries were produced by host pcc0. They do not establish native pcc1
compilation, fixed-point bootstrap, complete C/Python coverage or production GC
performance. The current candidate is newer than this runtime and must be
rebuilt before claiming combined qualification.

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

The latest verified local synchronization used the immutable 13:26:05 capture:
PCC `1ef656e38dd4ca6a5d420bb8e1b029ab2cd3f9e1` (33 paths), GUI
`45b84f1d1b112c4a88cc9d5a6772077a2f78bb7c`, and gateway
`83577bc41dc79d3ead2e217bfa04f0f500799012`. Exact bytes, modes and diff checks
passed. That capture recorded 1,192 passes and one failure across 37 independent
affected-file groups. The failed original contextual test produced 22 IR outputs
and 25 codegen exceptions. Collection selected 18,593 nodes and gate-deselected
5,742, with no errors or skips; it executed no test bodies. These results qualify
the capture's scoped checks only. Later source changes remain separate.

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
