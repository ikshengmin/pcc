# Current status

Updated October 4, 2026, 08:23 UTC. Maintain this page in place.
[Project intent](project-intent.md) and [compiler contracts](compiler-contract.md)
remain authoritative. No complete acceptance goal is qualified yet.

## Source and synchronization boundary

The verified local baselines are PCC `f4cdc3170ba701d452b7eb664ed40d128f441088`,
gateway `33856e0cf8194977ab4203cb02bfa2aee6a8d88f`, and GUI
`6c61696c2ca54f4afd3f53c60f4e8a2e8bd7e3a3`. They contain the immutable October 4
07:14:15 source capture: 30 PCC, 11 gateway and six GUI paths. The complete
before/after packet is Library `libfile_8db8f80c0d40819199565b1cb0aac0c8`,
version 1, SHA256
`aee054dee45e10f014a0157bed3fa064232b3e7dd5cf6b28565c7fbb709479e8`.
All source hashes, executable modes and local staging scope were verified.

Cloud development and validation continue. Every two hours, legitimate source,
tests and documentation are synchronized and committed locally, including
unfinished work with its exact limitations. The next run is October 4 at
09:40 UTC. Local development, tests, CI work and push are not part of this loop.
The future push/CI repair loop requires completion of the ten goals and a separate
explicit instruction to start. No such start instruction is active.

Post-capture changes currently include the generic C preprocessing repair,
mandatory-gate continuation and the small slice-dispatch repair. Experimental
suspension, method-selection and module-import changes remain separately sealed
until their concrete integration contracts are resolved. Every coherent code
batch is backed up with real before/after bytes; a remembered hash is not recovery.

## Latest exact-source checks

The 07:14 snapshot has **2,033 distinct passing nodes, 27 failure reports and
14 setup errors**, plus one resource-incomplete original context test. Its raw
receipts also retain 73 repeated passing attempts, excluded from the headline.
GUI contributes 204 host cases and 14 separately reported subtests; nine GUI
integration cases were explicitly deselected.

All 16 original async tests and all 410 gateway nodes received terminal test
reports. The 41 unsuccessful reports comprise two actual gateway compiler
ownership failures, 33 strict stale-runtime admissions, three missing-qualified-
pcc1 cases and three missing external-clang oracle cases. A terminal setup or
compilation failure does not mean the native program ran. The gateway file runs
were continued after first failures so untouched nodes were not hidden.

The original 47-target Stage1-context test retains 47 complete IR outputs but
hits its unchanged 1.5 GiB cap after 263.622 seconds at 1,626,411,008 bytes.
The assertion is not passed. Its IR is separately preserved in Library
`libfile_11964a0308288191ba9414addad4d76a`; this large artifact is unnecessary
for applying the source delta. Earlier post-emission diagnostics also expose
preexisting dynamic attribute edges in `_define_label`.

C/Python collection accounts for 26,676 nodes, with 26,452 selected and 224
deselected, zero collection errors and zero collection skips. The separate
5,563-node integration inventory is collection only. Full C/Python execution
has not been completed on this snapshot.

## Matched native and compiler boundaries

The immutable October 4 06:49 source has a fully admitted Linux x86-64 runtime:
186 members, threads enabled, atomic refcounts and unchanged source/configuration.
Archive SHA256 is
`aa9a115cc114016a15178fa5399d87f0eac18b0b4bb87510c2f4f35730c796d0`.
The build took 400.29 seconds at a 460,505,088-byte sampled peak. The source,
archive and admission evidence are Library
`libfile_d7793579316881918634c229bc93f2cd`, version 0.
These measurements are diagnostic observations, not final performance acceptance.

On that exact pair, the whole original async file records **15 native passes
and one native semantic failure**. The failing nested-closure program reaches
`lines[:-4].decode().split(...)` and raises an incorrect bytes IndexError.
The corrected D3 oracle passes separately: both CPython 3.15 and PCC require
`StopIteration.value == 42`, rather than returning 42 directly from `send()`.
The first seven native cases are preserved in Library
`libfile_3170d229ace481919c8bbd26e431940e`; the remaining cases and complete
matrix are `libfile_751be938168c8191a24c191017807335`.

All 410 gateway nodes were attempted against the matched pair: **389 pass and
21 fail**, with no skipped or untouched nodes. The failures are 16 unavailable-
pcc1 prerequisites, three missing-clang reference prerequisites and two real
host-pcc0 compiler failures in dashboard/local HTTP. These programs advance beyond
the repaired `len()` result and require a persistent generator-frame output owner.
The structured-scope native example passes. The provider ABI control also passes,
but uses external GCC and CPython ctypes; it is not PCC-owned or live TLS evidence.
Full receipts are Library `libfile_e56db4a033a48191a93995fdef47d347`.

A separate interpreter correction reran the three host examples using qualified
CPython 3.15. The same two compiler failures remain and structured-scope passes;
unrelated forward-annotation errors from PATH Python 3.12 disappear. The integrated host-only correction defaults discovery to the invoking interpreter
while preserving native pcc1's existing no-probe guards. Its 16 targeted checks and
full original module lowering pass; joined native qualification remains pending.

The original 768 MiB async diagnostic stops remain retained. An unchanged
round-trip replay passes within the same 90-second timeout at 877,539,328 bytes,
explaining the lower cap. The complete native file uses the maintained
1.5 GiB/300-second diagnostic envelope. Resource stops are not semantic passes.

The completed V10 compiler census covers **442 modules** on the 07:14 source:
**411 pass and 31 fail frontend lowering**, with no resource stops or regressions
among the 398 V9 passes. Twelve prior contexts recover and one new module passes.
It adds only `pcc.driver.native_provisioning`; all 441 V9 names retain their
relative order and are compared by name. Fresh AST/export inputs are Library
`libfile_698f2cefc184819199302d5b03ce3d6d`, version 0, graph SHA256
`bfbe347eb1f72b0bed94a368e764ca0f7049df670b8720ac8b8129889c3bc247`.
The old `cli_bootstrap` timeout now passes within the unchanged 120-second cap
at 74.667 seconds, producing a complete 547,522,725-byte PIDX artifact. The
terminal receipts are Library `libfile_930c3e0211ac8191bc4497dc0c8bb2b7`.
This is host frontend-to-PIDX evidence, not native compiler qualification.

Historical V9 accounts for 398 passes, 42 frontend failures and one timeout on
its 441-module 04:49 source. Its source/input/terminal receipts are Library
`libfile_31c74178f38c8191a4dca4b594738d3a`, with successful output restored via
`libfile_41ebd6cbd5208191a3fbc5a696b9afa2`, version 1. The authoritative-root
index repair has same-input causality and preserves exact output prefixes;
its source/proof is `libfile_246dd4e059e0819185be6e085e5c8c5c`. It does not
reduce the remaining large compiler output or establish Stage2 performance.

## Current repairs and remaining failures

Strict root, publication, no-build and provenance checks remain enabled. The
reviewed undefined-symbol linker defect and forbidden runtime provisioning were
reproduced and repaired in earlier synchronized code. Three explicit-prebuilt
helper controls execute with both no-build flags on the admitted 05:27 boundary;
that proof is not silently attributed to later source.

The small slice repair selects the existing slice ABI for actual built-in
sequences and preserves slice-key `__getitem__` for user objects. It passes 39
component checks and a paired five-GC compatibility control with every backend
identity observed. The unchanged full original program advances to a different
failure: `urllib.parse` is loaded but not published as `urllib.parse` on its
parent module. The slice code and a maintained native regression are integrated;
current-source native qualification remains open. The unchanged original full
program subsequently passes with the paired module-import repair below. The complete causal evidence is Library
`libfile_1f63a44dd2f08191842efbdaea10b6e2`; maintained code is
`libfile_f85c050cddb4819183f9591fa0b86737`.

The integrated module-import repair handles parent publication, one object during
cycles and failed-cache cleanup while preserving the original exception. Its 24
actual-body checks pass. The unchanged original async program passes under five
requested GC settings in a configuration-checked replacement-member experiment;
separate native import/cycle/exception controls witness and pass all five collectors.
These are compatibility proofs, pending the new joined source/runtime build. Older
failed-import retry semantics still require namespace, initializer-guard and
surviving-global ownership work. The suspension proposal remains off-tree pending
valid original gateway context and native lifetime/exception replay.

The integrated environment-copy, shlex/check-output and sequence-index producer
repairs recover all five retained original contexts (build_exec, compile_cache,
pipeline_libpython, module_action_dag and driver.project), with 29 focused checks
passing. The three maintained native behavioral programs await the joined runtime.
Code and full before/after context receipts are Library
`libfile_3607c4ea464c8191b7d3b39d2847b4aa`.

Method-selection work remains off-tree. Concrete unresolved contracts include
class-bound rich comparison and fresh reflected lookup, legacy managed-method
publication, raw-adapter retirement after deletion, bound-function construction,
and method-array reader/writer exclusion. Isolated selector/model success does
not establish safe concurrent publication or raw provider lifetime.

C repairs independently execute 19 original/new programs through the owned Linux
backend/linker and emit 76 target-object controls; 136 focused checks pass. They
cover unsigned narrow atomic results, pointer exchange, AArch64 unsigned
subtraction overflow and x86 signed i32 multiplication overflow. The C99 header,
constant-GEP and optional AArch64 type-context repairs have their own scoped
receipts. Exact original Csmith seeds remain unavailable here, so no 16-seed
success is claimed. Public runtime-backed C gates and non-Linux device execution
remain open.

The later Lua/LZ4 preprocessing repair preserves original vendor bytes and passes
112 selected checks plus four owned native programs. Character-literal #if
handling and macro stringification are repaired for those cases. A subsequent
review exposes a broader shared character-decoding/type-policy gap for signed
byte escapes, UTF-8 multichar constants and AArch64 Linux; that gap remains open.
The user's 026ad210 report of 57 C failures, including 42 regressions, is attributed
historical evidence, not a current cloud total. Its complete node/family inventory
is retained in the recovery ledger; overlapping categories are not added together.

The GUI reference is pinned to `47f943859bef60e4160492346772ded9b24f765a`;
all 7,412 restored Git blob hashes and modes were verified. Session observer
containment and the native-pump call ABI have host coverage. The upstream TS
suite, actual native GUI, Loader/HMR lifecycle, interactions and pixels remain
unqualified. Reference restoration does not establish parity.

## The ten acceptance goals

1. Native host pcc0 → pcc1 → pcc2 → pcc3, byte-identical Stage2/Stage3, original
   C/Python controls and no libpython. No current pcc1 or fixed point is qualified.
2. Equal production correctness, concurrency, relocation, GC4 capacity, long-run
   memory and throughput for all five collectors. Earlier source-bound controls,
   held-lock/STW probes and telemetry are partial evidence, not full acceptance.
3. Actual macOS ARM64, Linux x86-64, Windows and Linux AArch64 qualification;
   macOS-15 must use the standard 3-core M1/7 GB/45-minute job. Cross-object
   emission does not replace platform execution or wheel/version parity.
4. Owned compiler/runtime/ABI/provenance throughout, including runtime self-build,
   bindgen, lockfile, function stubs and consistent CLI/API/diagnostics. External
   compiler/interpreter reference lanes remain explicitly separate.
5. Complete EDG semantics and compilation-memory targets. Current semantic gaps
   and the resource-incomplete context test remain blockers.
6. Stage2 no slower than Stage1 and the five-GC shared build under six hours.
   No final-source performance comparison is qualified.
7. Complete C/Python and real integration execution, plus remaining TLS/BSD archive,
   freestanding-assertion and asynchronous-timeout cleanup. Collection is inventory.
8. Gateway semantic and performance goals beyond the asyncio reference. Current
   native compiler failures and unavailable pcc1 lanes remain blockers.
9. Deferred tail-call, multiply-add, peephole, scaffold identity and thread logging
   goals. Prior scoped optimizer and thread/STW results still need final-source
   native and performance qualification.
10. Complete native GUI across five GCs, pinned Harness HTTP/TLS, Loader/HMR,
    typed FFI/UI bridges, interaction and pixel equivalence.

## Recovery and evidence preservation

Detailed historical receipts remain outside the repositories in durable Library
archives. The latest source packet includes the prior status text and exact
restoration chain. Completed immutable source views may share only verified
identical bytes, modes, types and mtimes; any future editing requires a private
copy. Historical generated IR is stored losslessly with tested restoration
helpers. Active inputs, source, successful runtime archives and all archive
copies are preserved. No permission to remove old archive copies has been given.
