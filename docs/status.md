# PCC current status

Updated 2026-11-24. Source: `ikshengmin/pcc` master (`shenming` remote).
Durable receipts for this period (patches, profiles, A/B scripts, repros):
`/Users/jiamo/pcc-recovery-archive/2026-10-13/README.md`; the 2026-10-21
handoff is `/Users/jiamo/pcc-recovery-archive/2026-10-21/README.md`. The 2026-10-07 status
(source preservation, the remote agent's backups) is in git history at
`38357808:docs/status.md`.

This page holds the work order and the current state of its items. The
dated records that used to follow it are in
[status-archive/2026-11.md](status-archive/2026-11.md) and
[status-archive/2026-10.md](status-archive/2026-10.md): history that
describes the runs it records, not the current tree, and not a task queue.
Search them by symbol when a result's provenance is needed. A result that
is no longer the current state of its item moves there with its text
unchanged.

## Work order

The queue an agent takes when the maintainer says only "推进"; see
[AGENTS.md](../AGENTS.md#continuing-on-推进). Platform "any" means Linux or
macOS.

Who set the order: item 2 is the one-collector work the maintainer ordered
on 2026-11-24, when the maintainer also told Claude to rewrite that item,
rules 13 to 15 and the collector measurement rules for one collector;
self-host last and its completion bar, "推进" authorizing
commit and push, and the round and claim rules below are the maintainer's
decisions of 2026-11-22. Item 3 (pcc-gateway throughput) is the
maintainer's addition of 2026-11-23, placed after the GC item. The relative
order of items 4 to 7 is the agent's recommendation of 2026-11-22, which the
maintainer adjusted only by moving self-host last; ask before trading one of
them against another. Targeted
rounds, rules 13 to 16, the GC measurement rules and the inert-slice rule
were the agent's recommendations after a risk review on 2026-11-22; the
maintainer approved them and asked for targeted rounds the same day.

**Targeted round.** The advance message followed by a target skips the
queue order: the target names one item by its number, an issue, or words
naming an item or project. For example "advance 3" means item 3,
"advance gateway" item 3, "advance Go" the Go project in item 4,
"advance #6" issue #6 in item 1, and "advance numpy" item 5.
Take the first unit of that target that rule 1 allows; if none is free,
report that and stop rather than fall back to another item. Everything else
still applies: claims, areas, a regression's stopped area (rule 13),
design-review waits (rule 15), the circuit breaker and the waiting list.
Without a target, take the queue in order.

**One round.** One "推进" is one round on one unit. A unit is the single thing
an item's "Next" names: one issue, one project's first blocker, or one
sub-step of a milestone. When that is too large to validate and commit on its
own, the unit is its first slice that can be, and the rest goes back into
"Next". The round ends when the unit is validated and pushed, or is blocked
with its reason recorded. The agent then rewrites the item's "Next" with the
new next step and its source identity, releases its claim, reports and stops;
it does not move on to another unit in the same round.

**Claims.** Concurrent agents, on any host, coordinate through the claim lines
under each item and through Git alone.

1. Choose the first unit that this host can execute, that waits on no
   maintainer decision, that no active claim names, and whose area no other
   active claim holds. Areas are coarse: `runtime-gc`, `runtime-codegen`,
   `backend`, `frontend-<language>`, `numpy`, `value-model`, `gpu`, `proof`,
   `selfhost`, `checkpoint`. Two units may share an area only if they touch
   no common file.
2. Claim before any other work: fetch `shenming/master`, replace the item's
   `- none` line, or add a line under the existing claims, with
   `- <unit>: <Codex|Claude>, <host>, <UTC start>, area <area>`; commit only
   that change as `claim: <unit> [skip ci]` and push. The claim holds only
   once that push succeeds.
3. If the push is rejected, pull with rebase. A conflict on claim lines means
   another agent claimed at the same time: keep its line. If it names the same
   unit or area, drop your own claim commit and choose another unit;
   otherwise keep both lines.
4. Finish in one commit: the result, the item's new "Next", and the removal of
   your claim line (back to `- none` when it was the last one).
5. Renew a claim at least every two hours, by pushing progress or by updating
   only the time on its claim line. A claim with no pushed progress or renewal
   for four hours may be taken over: replace its line and write "taken over
   from <previous claim>" in the commit message. Work the previous agent did
   not push cannot be recovered from another host. Before pushing any result,
   fetch and check that your claim line still stands; if it was taken over,
   push nothing for that unit and report.
6. On one machine every agent works in its own worktree or clone, never in
   another agent's checkout. Heavy builds and performance runs still queue on
   `build/.pcc-performance.lock`.
7. An agent that cannot push to `shenming/master` claims nothing and takes no
   unit.
8. An agent holds at most one active claim. After a round it fetches again and
   chooses again by these rules; it never reserves its next unit.
9. Circuit breaker: when the same unit ends blocked or fails validation in two
   consecutive rounds, move it to "Waiting on maintainer" with the reason and
   do not retry it. An agent whose last three rounds all ended blocked stops
   and waits for the maintainer.
10. Anything that needs the maintainer goes to "Waiting on maintainer" below:
    the unit, the question, the options and the agent's recommendation,
    labeled as such. Then choose another unit. Do not wait idle on it.
11. Resources: when `build/.pcc-performance.lock` is held, take a unit that
    does not need it rather than queueing for it. Delete your own run outputs
    at the end of each round, and keep this repository's memory and time
    guards.
12. Pacing: an agent runs one round at a time. A continuous agent, including
    one running around the clock, starts its next round only after the
    previous one ended, and reads this work order again each time.
13. Checkpoint. Focused tests alone do not catch drift across many rounds.
    A checkpoint is due when ten or more commits since the last checkpoint
    below touch the compiler or runtime
    (`git rev-list --count <checkpoint>..shenming/master -- pcc/runtime pcc/frontends pcc/ir pcc/backend pcc/driver`),
    or when a result of item 2 lands.
    - Dispatch: any round, targeted or not, that finds a checkpoint due and
      none pending first runs
      `gh workflow run "PCC validation" --repo ikshengmin/pcc --ref master -f base_sha=<checkpoint> -f expected_sha=<current master SHA>`,
      records the run under "Pending" below in its own `checkpoint:` commit,
      and continues with its unit. Nobody waits for the run.
    - Comparison: once the pending run has finished, comparing it is the
      first unit of the next untargeted round (area `checkpoint`). Compare
      it with the last checkpoint's run by job and by test node id. A
      regression is a job or node that passed there and fails now, a
      platform where pcc1 was produced and now is not, or a Stage1 wall time
      more than 25% above the same lane there. A lane cancelled or missing
      in either run gives no comparison; record it.
    - Outcome: with no regression, the dispatched commit becomes the last
      checkpoint. With one, open an issue naming the run, lane, node and
      commit range, put it first in item 1's "Next", and record its area as
      stopped: until the fix lands, no agent pushes another result in that
      area except the fix.
    - The maintainer's approval of 2026-11-22 covers this dispatch at this
      cadence only. `full_chain`, performance A/B dispatches and local
      full-suite or bootstrap runs still need the maintainer's explicit OK.
14. macOS verification. Each result of item 2 that changes the collector,
    the allocator or the code they generate, and that was not measured on
    the maintainer's Mac, adds a unit "macOS verification of <unit>" to item
    2, platform macOS. A local agent on the maintainer's Mac takes it:
    `scripts/pcc_perf.py arm` for the result and its parent, `ab` with
    `--control-arm`, `table`, and the unit's focused native tests, reported
    as local Mac evidence. Until one does, the result's report lists those
    commands for the maintainer.
15. Design review. The first code round of two architecture units is
    preceded by a design round: object relocation, and biased reference
    counting once its ceiling qualifies. The design round writes a note in
    the unit's issue: the data structures; every place that holds an
    object's address without a count (stack words, runtime caches) or as a
    value (`id()`, the default hash, weak references, C-API handles);
    finalizers; and the measurement that decides the unit. The unit then
    waits under "Waiting on maintainer" until the maintainer, or a reviewer
    the maintainer names, approves the note. Agents take other units
    meanwhile.
16. Rules. Agents change AGENTS.md's continuation section and these rules
    only on the maintainer's instruction; a proposed change goes to the
    waiting list. Rewriting an item's "Next", claim lines, the checkpoint lines and
    the waiting list is ordinary round work.

**Checkpoint.** Last checkpoint: `7ce5cf663`, validated by
[run 44358778171](https://github.com/ikshengmin/pcc/actions/runs/44358778171).
That run is a red baseline. No formal run pending. The latest completed
[checkpoint comparison](investigations/checkpoint-validation-45427972345.md)
keeps that baseline: candidate `7ec14d388` has exact test regressions
[#19](https://github.com/ikshengmin/pcc/issues/19) and
[#20](https://github.com/ikshengmin/pcc/issues/20). #19's fix landed at
`6c1c280f`; matched [macOS ARM64 job 138802215009](https://github.com/ikshengmin/pcc/actions/runs/45447422239/job/138802215009)
passed. Artifact 14968387872 confirms the exact callback node (1 passed,
0 skipped), with direct and threaded native output equal to CPython and
the matching runtime identity. This does not qualify the entire run.
Stopped areas: the affected single-collector test and CI paths, except fixes
for #20. The two Go diagnostics and Rust integration node from #10/#11 passed;
the revised Go index native integration witness was not reached. #12 still
blocks Windows ordinary collection. This checkpoint did not produce ARM64
Stage1 on either platform; Linux x86-64 and Windows did. No fixed point proved.

**Waiting on maintainer.** Questions agents could not settle. The maintainer
answers here or in chat; the agent that next takes the unit records the answer
in the relevant section or issue and removes the entry.

- Item 1, issues #9, #4 and #8 (asked by Claude, 2026-11-24). They describe
  collectors 1-4, which no longer exist: #9 weak callbacks and finalizers
  under acyclic resurrection on GC1-4, #4 a GC3 timeout with four carriers,
  #8 the GC3 nursery design review. Options: close all three; or keep #9
  open until its two native test files are rewritten for the one collector
  and pass. Claude's recommendation: close #4 and #8, and close #9 after
  that rewrite, which belongs to item 2 (b).

- Item 3 (c) A1 (asked by Claude, 2026-11-26): may compiled code on Darwin
  read thread-locals through the pthread TSD array (`tpidrro_el0`) as Go
  does on darwin/arm64, instead of calling the TLV resolver at every
  access? The layout is not a documented Apple ABI. Claude's
  recommendation: yes, with a check at process start that compares the
  inline read with the resolver's answer and falls back to the call for
  the whole process when they differ.

1. **GC correctness.** Open issues:
   [#7](https://github.com/ikshengmin/pcc/issues/7) (ownership of managed
   boxed-int direct returns) waits for a new reported native owner witness,
   not an annotation or a project-specific exception.
   [#9](https://github.com/ikshengmin/pcc/issues/9),
   [#4](https://github.com/ikshengmin/pcc/issues/4) and
   [#8](https://github.com/ikshengmin/pcc/issues/8) describe collectors 1-4,
   which were removed on 2026-11-24; what to do with them is under "Waiting
   on maintainer". The checkpoint regressions
   [#10](https://github.com/ikshengmin/pcc/issues/10) and
   [#11](https://github.com/ikshengmin/pcc/issues/11) have locally validated
   fixes and partial hosted confirmation in checkpoint 45427972345;
   [#12](https://github.com/ikshengmin/pcc/issues/12) (Windows collection)
   is open. This item's earlier text, the results for #9 and for collectors
   1-4, is in the
   [archive](status-archive/2026-11.md#work-order-item-1-gc-correctness-as-of-2026-11-24).
   [#16](https://github.com/ikshengmin/pcc/issues/16) is fixed: every
   instance death walked every live weakref in the process (O(n²)). The
   target now carries `PY_FLAG_WEAK_REFERENCED` (0x8000, the unused former
   `PY_FLAG_GC_LARGE_DEFERRED`) from its first weakref, and the existing
   target directory runs on the one collector, so a death visits only its
   own refs and an unmarked death none. Local Mac, host pcc0: 100k deaths
   with 16k unrelated live weakrefs 20.8 s -> 0.033 s (CPython 0.008 s);
   weakly referenced cycles at 2k/8k/32k 0.10/1.55/26.8 s ->
   0.009/0.028/0.105 s; a weakref/finalizer/resurrection probe matched
   CPython (129.5 s -> 0.97 s). Tests: `tests/python/test_weakref_target_directory_native.py`
   and the directory model cases of `tests/python/test_gc0_weakref_batch.py`.
   Not a measured handler-throughput change (no `pcc_perf ab`). Linux,
   pcc1 and the whole Python suite (blocked by item 2 (b)) are unrun.
   The collector's early weak pass, `py_weakref_queue_gc0_batch`, now visits
   only the batch members' own refs through the directory instead of every
   live weakref on each collection. Local Mac: 200k garbage cycles with 64k
   live weakrefs elsewhere 0.545 s -> 0.284 s (0.27 s with none); the probe
   above still matches CPython. A full collection with 64k weakly referenced
   live objects is 36 ms against CPython's 4.9 ms: collector traversal cost,
   item 2's throughput work, not this pass.
   [#20](https://github.com/ikshengmin/pcc/issues/20), sweep CI:
   the Darwin focused gate selects three current native cases (retained
   roots and subset resurrection, cyclic weakref/finalizer synchronization,
   and post-finalizer weakref clearing) and six weakref model cases,
   replacing removed GC1–4 verdict paths. The C subset probe now matches
   CPython 3.15: a callback-free ref survives resurrection and clears on
   eventual death; the native late-weakref case tests an early callback ref
   separately. Kept six-worker defaults, exact execution receipts, and all
   job guards. Linux: 106 focused checks passed and the actual gate executed
   all 9 nodes with six workers (663 MB peak tree RSS). No compiler/runtime
   code changed. The hosted run and whole-language qualification are pending.
   The separate late-weakref host model needs current collector primitives
   (`pcc_gc_tracked_count_flush`, user-code window) before it can run again.
   #20 checked frame registration: five red model cases now execute the
   current registry and unlink bodies, preserving an unrelated live frame.
   With node/capacity/allocation/insertion failures they verify no park,
   cleanup before the inner guard exits, and no published frame or cycle
   request. The raw status ABI check remains. Linux: 19 focused tests pass;
   no runtime code changed. No new CI run for this test-only slice while
   the sweep CI run is queued; whole-language qualification is pending.
   #20 frame-rooted operands: seven obsolete lease expectations now
   verify the actual call operands trace to the original frame slots,
   in argument order, without accidental root copies. The native
   comparisons and operations still match CPython through reentrant
   collection, rebinding and exception cases. Linux: 82 focused checks
   passed, including both native CPython comparisons. No compiler/runtime
   changes; hosted and whole-language qualification are pending.
   #20 Stage2/3 artifact transport: the finite contract tests now use
   the six actual log/resource paths on each CI route, not the 30
   paths of the retired collectors. They still reject missing,
   duplicate, unrelated and binary paths, and now also reject adding
   retired collector logs. Linux: 11 focused checks passed; no bootstrap
   was run and no workflow budget or actual artifact path was changed.
   #20 same-attempt Stage1 admission: the test finds the bootstrap
   owner by step ID, not by an old comment. It still checks legacy-only
   bootstrap, no cross-run download or implicit provisioning, and the
   exact same-attempt producer/consumer bundle. Linux: 208 focused
   admission/export checks passed; no hosted bootstrap was run.
   #20 Win64 callback: the native executable checks home-space,
   result 616 and preserved caller roots without a collector selector.
   The v2 receipt requires exactly one execution and rejects duplicate,
   stale, nonzero/boolean status, wrong image or selector evidence.
   Linux: 34 focused host controls and Win64 IR/COFF encoding checks
   passed. Windows native execution is pending hosted CI. The combined
   gate still fails its common two-root slot receipt (old five-run
   requirement), not the independent Win64 receipt.
   #20 two-root slot receipt: the intact owned helper checks both slots
   are empty and the original TLS exception survives, with exactly one
   native execution in the v2 receipt. The verifier rejects expired,
   duplicate, selector-bearing or malformed execution evidence. Linux:
   329 host receipt/model checks passed, including the combined Win64
   admission control. Actual native slot execution remains unverified
   (the restored Linux environment lost its runtime archive).
   #20 cyclic lifetime witness: the native and CPython source now both
   create a self-cycle and disable automatic collection, require no
   callbacks after the producer joins, then require weakref/finalizer
   order and no repetition on the next collection. The separate immediate
   death and slot ABI witnesses are intact. Linux: 332 host controls,
   reference executions (including rejection of removed self-edge), and
   emitted IR checks passed. Native cyclic execution remains pending CI.
   #20 common native ownership receipts: the six programs use the v2
   one-run helper without a selector, plus the unchanged slot probe and
   Win64 callback. The gate requires current run and binary identity,
   exact CPython/native commands, integer status, timing and the existing
   IR/runtime/linked-image checks. It rejects legacy evidence. Linux:
   503 host controls passed, including inherited-selector removal and
   negative native status. Actual native executions pending hosted CI.
   #20 native find gate: both compiler modes and both programs now use
   one native execution each without a selector. Shared v2 admission
   checks bind the current run, absolute binary, typed status and timing;
   the seven find/length modules, Stage1 provenance and budget are intact.
   Linux: 340 host/reference/IR checks passed, four native nodes pending.
   #20 shared ownership helper: all 133 direct call sites now run the
   native program once, without a backend parameter or repeated five-GC
   loop. macOS receipt retention includes native.json without dropping
   independent evidence. Linux: 620 focused host checks passed; this is
   test migration, not native runtime qualification.
   Linux native verification at `a3831872`: owned threaded atomic archive
   (192 verified members) built in 61.1 s, peak tree RSS 529 MiB. Actual
   pcc0-compiled C handle retention/release (2 nodes), cross-thread final
   release, bound find/str length and container bitsets all passed with
   their reference checks. Two-root cleanup failed twice before execution:
   indexed codec preflight for module 2 estimates 2,105,543 bytes against
   the existing 2,097,152-byte limit; missing link manifest is downstream.
   No limit changed. These results predate Claude’s subsequent codegen
   edits; no pcc1 or latest-master native qualification is claimed.
   At `9fbd2648`, a fresh source-matched archive after the codegen lease
   slice confirms the two-root immediate executable really prints
   TWO_ROOT_CLEANUP_EXCEPTION_OK and exits 0. A separate direct native
   cyclic witness also matches CPython. The full indexed gate still fails:
   module 2 conservative prefix is now 2,148,240 bytes against 2 MiB.
   Do not promote either direct result to indexed-gate PASS.
   Next: retain the indexed gate failure as a separate backend issue;
   advance (b) on independent obsolete test migrations while the collector
   codegen slice remains claimed. The two-root evidence needs a generic
   bounded representation change, not another replay or relaxed guard.
   #20 Worker prior inventory at `3b5598d0`: the exact 87-module
   ordinary collection now has 3789 nodes. Cleanup IR 21 -> 18,
   exception cleanup 43 -> 44, float callback 10 -> 5 reflect the
   already-landed single-collector migrations. No file or marker removed;
   the three guarded process tests remain included. Reconcile the fixed
   inventory and its controls (including stale and missing-node rejection).
   All 394 affected ordinary tests passed on Linux; full six-worker
   execution remains for CI, not qualified by collection.
   #20 Worker prior execution at `487f31f7`: default six workers,
   3789 nodes collected, 3099 passed before three tuple-factory failures.
   The model required obsolete initialized-publication callbacks, although
   the live factory publishes its registered owning output directly and
   tracks only cycle-capable tuples. Removed that obsolete model hook;
   ownership conservation, tracking, error preservation and transaction
   counts remain checked. All 14 model tests and both native pcc0 tests
   (lifetime and concurrent collection) pass with the rebuilt matched
   runtime; both programs also pass CPython.
   #20 at `6f7fd8a4`: six-worker gate now reaches 3267 passed
   before failing the operand call model's deleted mode-lease functions.
   Replace the obsolete 42 synthetic lease-token/retry permutations with
   12 actual constant-token emitter cases for one/three owning and borrowed
   operands, scalar/object/iteration results and exact pending exceptions.
   Keep real emitted IR checks and update the fixed inventory (3759 nodes,
   87 files). Linux: 381 affected ordinary checks passed, no skipped cases.
   #20 at `3e99de40`: six-worker gate reaches 3457 passed,
   then three Mach-O link timing stub failures. The production executable
   link now explicitly selects runtime-main archive ownership; model
   callbacks did not accept that argument. Test both true and false
   forwarding through the real preparation body, retaining timing,
   output, input ownership and injected-error checks. Linux: all 34
   affected Mach-O timing tests passed; no count change.
   #20 at `ceaab313`: 3502 passed in the six-worker gate, then
   five find-model failures from undefined live runtime dependencies.
   The model now executes the actual bytearray length accessor and
   supplies bounded memchr/memcmp and pointer arithmetic adapters to
   the current string search body; no CPython find shortcut or
   assertion removal. Linux: 157 find/string model tests and the
   pcc0 native bound-find/str-length oracle passed on a verified
   matching archive. No inventory change.
   #20 Linux ordinary worker-prior gate at `192e35e3`: all
   3759 nodes in 87 files passed in 76.85 s with default six xdist
   workers. The real verify_pytest consumer accepts all six collection
   witnesses, 3759 selected/passed and zero platform-unrun; watchdog
   completed at 78.57 s, peak process-tree RSS 1,460,609,024 bytes.
   Local cap 4 GiB (stricter than hosted 16 GiB), so this is the
   ordinary subgate, not the entire hosted prior/stage1 qualification.
   #20 native object gate: prior failure was real wire oversize, not
   false budget accounting. Receipt v4 uses canonical signed LEB128 and
   fixed-order arena inventories, with a separate 16 MiB decoded-wire
   bound; the 2 MiB wire/aggregate/output/JSON limits remain unchanged.
   Host retention v2 losslessly compresses bounded artifacts; independent
   replay still binds every original byte, digest and native execution.
   Marker selection recognizes the actual immortal static str object
   layout and requires a real local operand use. Linux pcc0 with a fresh
   verified 192-member archive: 5/5 native nodes, six workers, 21.79 s;
   outer verifier admits all six executions and two-root slot evidence;
   838 focused host controls, signed i64 native arena roundtrip PASS.
   Four previously rejected full captures roundtrip byte-identically to
   the ordinary representation (16 modules), compressed input totals
   1,549,152–2,036,400 bytes; final evidence JSON stays below 955 KiB.
   Not a hosted Stage1 or native-pcc1 gate qualification.
   #20 worker prior strict-closure: hosted ARM artifact
   `15029055172` (run `45619704293`) failed at the actual Windows-platform
   owned method binding oracle, not memory or timeout:
   `_declare_external_function: no bound owned attribute call`.
   The current generated IR uses private root copy, loaded-method call
   and an out-self null-test. The oracle now tracks those exact paths,
   rejects detached callable/receiver/flag/copy source and still checks
   the actual symbol, not a substring. Linux strict closure at
   `e6e59a52` produced and whole-verified 796,825,537 bytes of IR.
   Replayed the exact failing Windows-platform section through the fixed
   oracle, owned parser/verifier, ordinary PIDX and real ELF emission
   (123,304 bytes, required native symbols verified). 115 oracle tests
   pass; full strict closure on the next hosted SHA remains unqualified.
   The subsequent full local closure retained 29 successful module
   emissions and reproduced the original Windows-platform assertion.
   Independently replayed all remaining five sections from that exact
   compiled closure, with source digests checked and the fixed oracle;
   all 34 original target objects now have local emission evidence.
   Run `45643044565` exposed a stale ordinary inventory count after six
   new oracle controls (109 -> 115); corrected all static receipt checks.
   Actual Linux six-worker ordinary gate: 3765/3765 passed, 76.04 s,
   peak 1.54 GB under a tightened 3 GiB local limit.
   #20 shared ordinary Darwin ownership: 51 red tests in two files
   directly invoke the Apple AArch64 ABI/assembler on Linux; three
   additional existing Darwin gates had no owner. All 54 now have
   explicit macos-arm64 campaign obligations, never counted as Linux
   passes. Pure cross-target IR checks and three portable C native
   controls stay Linux. Actual inventory 330 nodes (276 Linux, 54
   transfer); Linux 276/276 with six workers, 11.53 s, under 1 GiB after
   grouping the 29 host compiler-launching tests to avoid duplicate
   build trees; prior ungrouped local run hit 1 GiB. 311 CI ownership/
   coverage/plan controls pass (including the new group contract).
   Hosted Darwin execution remains pending, not skipped qualification.
   #20 direct-indexed Source failures: the cross-target tests were
   intercepting the retired public generate entry instead of the live
   fresh worker entry. Supply the explicit target at that boundary;
   observed AArch64 ELF machine 183 and Darwin cmpxchg records. The
   real worker also failed to apply the freestanding source directive
   and injected managed safepoint dependencies; it now honors the same
   source-mode contract as serial compile. Raw write/loop programs
   link without runtime symbols and execute with exit 22; ordinary
   managed programs retain the safepoint. Host direct worker and owned
   pass tests now inspect and link the actual host emission ABI
   (Linux ELF, Darwin Mach-O, Windows COFF/PE), not the Darwin linker
   on Linux. Linux fresh verified 192-member runtime; 44 direct tests
   (including actual linked program 41 42 before/after passes),
   282 adjacent tests pass. Other hosted platforms not run.
   #20 single-collector frame registry witnesses: removed three retired
   exported symbol expectations, reconciled the exact TLS/storage and
   raw import contracts. Owned object emission actually verified x86-64
   ELF, AArch64 ELF, Mach-O and COFF symbol sets (including Windows TLS
   index and x86 inline zero fill). Replaced retired GC4 pool/global
   registry C harnesses with real PCC native single-collector probes:
   verify unique archive owner, frame registration inactive, pool drain,
   and four threads held inside frame scopes while a fifth observes
   roots, then 512 mutations each. Fresh verified 192-member Linux
   runtime: 30 focused tests pass; external LLVM/clang reference
   test remains in the hosted job (clang unavailable on this host).
   #20 cross-module None ABI witness: the actual run 45619704293 fixture
   emits external linkage on definitions and declarations; the old regex
   skipped it. The test now uses the owned header parser, retains repeated
   declarations, and checks synchronous void/async handle and actual call
   types. The emitted IR had 2,189 headers, 1,086 unique declarations, 23
   definitions and five cross-module symbols, with zero return mismatches.
   Linux focused: 6/6 pass in 23.39 s; guarded peak 264,605,696 bytes under
   the original 1 GiB cap. Test-only, no compiler ABI change; hosted rerun pending.
   #20 multi-module valueclass emission witness: the diagnostic IR bundle
   contains separate modules with distinct local frame-map constants of the
   same name (the original failure used counts -5/-4/-5). The test now emits
   every module independently, like the real object pipeline, retaining the
   original function-symbol check. Linux: all five focused valueclass ABI
   tests passed after the latest fused-protocol change (20.36 s, peak
   207,953,920 bytes); the actual 20-module
   failure artifact separately emitted all modules for Darwin, including
   consumer and functools (43.90 s, peak 144,818,176 bytes), under 90 s/1 GiB.
   No stack-map bounds check or compiler behavior changed.
   Next: worker-prior/changed-module and remaining Source; the 216-node
   source shared batch still requires a hosted rerun, and the active
   checkpoint's independent wheel evidence is pending.
   #20 source worker admission: in run 45619704293, independent compiler
   pools overlapped and exhausted the existing 128-MiB headroom. The source
   guard now binds one peer admission lease per exact state path. The two
   original native ABI witnesses pass with all six workers, 90 s and 1 GiB
   (42.37 s, 808,480,768 bytes peak); the complete original 216-node batch
   gave 214 passing calls, two independent failures, and a 90-s TIMEOUT,
   not whole-batch acceptance. See [shared compiler pool investigation](investigations/shared-compiler-admission-45619704293.md).
   #20 on the local Mac at `25a309945`: the named CI-contract files and the
   shared frame tests pass. `tests/test_ci_native_object_regressions.py`'s
   host-only model controls failed only on macOS, from the gate's
   Linux/Windows host assertion (since `0df9b0b6a`); they now give the gate
   a supported host (test-only; 307 passed). Linux hosted lanes unrun.
   #12 Windows ordinary collection: the Linux profiling engine imports
   fcntl only while enabling/disabling the perf event; synthetic record
   decoding remains importable without it. Linux: 35 non-integration
   tests passed, including missing-fcntl import/decoder and ioctl cleanup
   on exception. Windows collection still needs hosted verification.
   [#19](https://github.com/ikshengmin/pcc/issues/19) has matching hosted
   native callback evidence as recorded in Checkpoint above.

   Claims:
   - none
2. **One collector: finish.** Maintainer decisions of 2026-11-24: pcc has
   one collector, the features of the former collectors 1-4 enter it, and
   the redundant collectors and the selector go, with no option left. State
   and evidence are in
   [One collector](#one-collector-gc0-is-the-fused-collector-maintainer-decision-2026-11-24)
   below. This item's earlier text, the per-collector results of
   collectors 1-4, is in this file at `cba826a93`. Platform: any for code;
   macOS for the measurements (rule 14). Measure with
   `scripts/pcc_perf.py`. While this item has a ready unit, an untargeted
   round takes it before item 3.
   > 维护者指示（2026-11-24）：item 2 先做稳定化，暂停 (0b) 的停顿优化。
   >
   > 原因：(0b) 最近两个候选（64-cell 批量分配、e74be5c94 的 exact node batch）都没有测出收益。按 AGENTS.md「No optimization that cannot pay」，先把 (0b) 现状写进 item 2 的 Next，然后停下 (0b)，等下面几步完成后再继续。
   >
   > 新顺序，每一步是一个单元，照常按 work order 的规则 claim、验证、推送：
   > 1. (d) 生成协议定型：编译器不再生成 pin/lease/relocation-read/write-barrier 调用；删掉 fused-protocol pass、pcc_gc_backend() 和所有 PCC_GC_BACKEND 读取（当前还有 54 处，分布在 17 个文件）；删掉没人用的 closed-young 分析和 pcc_perf.py 的 --gc / pacer 选项。area runtime-codegen。
   > 2. (c) 用 scripts/one_collector/sweep_one_collector.py entries 清掉只给 collectors 1-4 用的函数，把还在用的函数挪到使用方旁边，然后删除旧模块。
   > 3. (b) 用 scripts/one_collector/prune_tests.py report 清到 0：测这个收集器的测试改写后必须通过；只测 collectors 1-4 的测试删除。
   >    如果实际依赖关系要求调整 1-3 的顺序，可以调，但要把原因写进 Next。
   > 4. #13 的收集器侧第一道门槛（只做运行时这一侧；Go/Java 的切片降级留给前端 agent）：
   >    - 现有对象头不变，只加一个 described layout tag，payload 的第一个字指向编译器生成的不可变描述符；
   >    - 列出这个 tag 必须覆盖的每一个 tag 分派点（slot visitor/traverse、销毁时的 decref、size/extent、young 跟踪），并加一个测试，漏掉任何一个就失败；
   >    - 一个不含指针的字节数组对照和一个含引用的对照，分别在 young 回收、full 回收、显式 gc.collect() 下通过，覆盖根的生命周期、环、回收，以及对象只在寄存器里时触发回收的那个窗口；
   >    - 跑 pcc_perf.py ab --control-arm（5 对，w1 和 w4），证明现有 Python 对象的 tag 不因此变慢。
   >
   > 完成第 4 步后，写一个"收集器接口稳定"检查，五条全部满足才算稳定：
   > (1) 生成的 IR 里没有 pin/lease/barrier/relocation 调用，代码里没有 PCC_GC_BACKEND；
   > (2) sweep_one_collector.py entries 为空；
   > (3) prune_tests.py report 为 0；
   > (4) 对象头布局（count@0、tag@8、flags@12）和 described tag 的 ABI 不再改；
   > (5) 一次检查点 CI 没有新回归，#10/#11 的托管 lane 已重跑。
   > 全部满足后，在 item 2 和 item 4 的 Next 里写明「collector interface stable at <sha>」，再回去做 (0b) 和吞吐。
   >
   > 记录要求：在 work order 里用原话引用这条维护者指示。其他规则照旧：每个结果推送前跑受影响语言的整套测试，遵守检查点规则，macOS 的数字归 rule 14 的 macOS 验证单元。

   Next: (b) and (d) run in parallel at the maintainer's request ("派发并做
   (d)", 2026-11-24): (b) changes tests, (d) code generation; the Python
   suite collects again, so (d) compares by node id against the prune list.
   Then (c) and the collector-side first gate of #13. Some (c) exports
   still have (d) callers. Generic Go/Java lowering stays with item 4. Do
   not resume (0b) or throughput until all five stability conditions hold.

   (b), C weakref lifetime: retained the native ownership contract as
   a typed C probe linked by PCC, without the retired selector or the
   system linker. A live weakref dereference owns its result; after the
   final release the reference clears. Linux pcc0: the probe and the
   existing CPython weakref comparison passed under the usual guard.
   The native whole-language gate is unqualified locally; run it in
   GitHub CI. This is test-only and changes no compiler or runtime.
   Linux worker preflight [run 45427972345, job 138565316142](https://github.com/ikshengmin/pcc/actions/runs/45427972345/job/138565316142):
   the cleanup helper check required a lease release already absent from
   generated IR. Removed lease-only expectations; actual one- and two-root
   release ordering, exception save/restore, and stack maps are tested.
   Linux: 18 IR tests and both native/CPython callback witnesses passed.
   Whole-language qualification is pending GitHub CI.
   The float-callback CI node now checks the single collector: both
   direct and threaded native pcc0 runs match CPython event-by-event for
   nested success, invalid-result destruction before receiver destruction,
   and restoration of the outer exception. The receipt retains both
   results. The original GC4 relocation symbols are no longer required.
   Linux: 102 passing focused checks; the existing legacy sweep-trigger
   assertion still fails on both this patch and its unmodified parent.
   The hosted float gate and whole-language qualification are pending CI.
   (b) populated cyclic-list public collection: 777 self-referential
   lists made by a joined creator release their payloads exactly once;
   callbacks clear all weakrefs, a rooted self-referential sentinel stays
   live, and another collection does not repeat finalization. Retired
   backend-specific sweep internals and their unreferenced C fixture are
   removed. CPython and actual Linux pcc0 native executions passed at
   `ff41b4af` with its verified runtime; pcc1 remains pending CI.
   #9 public migration probe at `65a949cf`: using the unchanged
   constructor/weakref_resurrection_boundary.py fixture and matched
   Linux pcc0 runtime, five modes match CPython exactly (plain, self-cycle,
   downstream, child-first, parent-first). Parent-cycle differs: CPython
   child-weak, parent-finalize, child-finalize; PCC child-weak,
   child-finalize, parent-finalize. The six-thread-mode singleton oracle
   cannot compile as written: Subject.__del__ may_park implicit dunder
   is not statically resumable. Neither failed check was suppressed,
   and the original tests remain intact. #9 stays open.
   (b) public generator lifetime at `49e13664`: 777 unstarted and
   777 suspended generators release their frame payloads, suspended cleanup
   runs exactly once, weakrefs clear, a rooted suspended sentinel survives
   collection and later closes normally. CPython and Linux pcc0 passed
   (2 tests); native pcc1 remains pending CI. The removed controlled
   sweep modes exercised retired collector-specific candidate and worker
   protocols; existing generator finalization, pending-error and resurrection
   checks remain untouched.
   Next: migrate other independent (b) lifetime tests; investigate the
   parent-cycle order in runtime-gc and the may_park frontend boundary
   separately, respecting active claims. Do not wait for the local Mac.

   (d), first slice: the fused-protocol pass scans its lines without `re`.
   Native pcc1 runs the pass, and its owned regex engine rejected the
   patterns ("pattern outside the native regex subset"), which blocked the
   full Python gate. On ASCII IR the scanner gives exactly the former
   expressions' output, also on the pass's actual inputs for a program and
   two runtime modules; it accepts ASCII names only, so host pcc0 and pcc1
   decide alike. A pcc-compiled program running the pass matches CPython
   (local Mac; the same program with the former module failed as pcc1 did).
   pcc1 was not rebuilt: bootstrap needs the maintainer's OK.
   (d), second slice: the compiler stops reading `PCC_GC_BACKEND` and
   calling `pcc_gc_backend()`. `pipeline_frontend_workers.py`
   (`_worker_collector`, recorded class keys), `deferred_frontend_schedule.py`
   (frontend and PCO floors, class keys), `indexed_phase_timing.py` (the
   snapshot's collector field) and `scripts/run_pcc_deferred_link.py` (the
   PCO emit floor) take the one collector, 0; recorded keys keep `|0`, so
   existing priors keep their keys. Tests that varied the variable now set
   a stale value, or none, and expect the one collector's costs and keys;
   the deferred-link floor test no longer holds the one collector's PCO
   floor to the PCO peaks of collectors 1-4 (its own 392-worker corpus
   check stays). Local Mac, host pcc0: the ordinary nodes of the 61 files
   that cover these modules, 2606 passed, with 62 nodes deselected that
   fail identically at HEAD (stale stubs, host resource admission, and the
   collector 1-4 cases of the prune list). A native program built from the
   two modules gave collector 0 and an available 17-value snapshot with the
   variable unset, `0` and `3`. Integration nodes and Linux were not run.
   (d), lease slice (Claude, local Mac, `22d737106`): code generation takes
   no address lease. The fused pass already answered every emitted
   foreign/borrowed lease acquire and release with 0; the 47 sites now
   produce that token 0 themselves, a known non-negative status emits no
   branch, a token-0 lease needs no cleanup and the extern c_str/c_obj
   projection takes none. Emitted IR has no `pcc_gc_leases_inert` load, lease
   call or `py_cleanup_one_lease_preserving_exception`; a sample program's
   IR fell from 6972 to 4737 lines and the test runtime archive from 17.2 to
   16.4 MB. Tests of the lease protocol now assert its absence (the
   message-owner contract checks the owning root instead, with a negative
   control). Host pcc0: the 129 files naming the protocol and 25 other
   IR-shape files show no new failure by node id against HEAD; one native
   program (fresh results, identity, exceptions, finalizers) matches
   CPython. Native integration programs of those 47 files (pcc0, each tree
   with its own source-matched runtime archive, `-m integration -k "not
   pcc1"`): 236 pass, and the 19 failing nodes are the same 19 that fail at
   `a38318725`. pcc1 and Linux were not run.
   (d), step 1 (Claude, local Mac, `4b2cc8f04` and test follow-ups to
   `e21d8c14b`): code generation emits no `pcc_gc_pin`, `pcc_gc_unpin`,
   `pcc_gc_note_slot_write_barrier` or `pcc_gc_note_write_barrier`. The
   fused pass already dropped each of them, so the post-pass IR keeps its
   blocks (358 on the sample program) and loses only the 28 dead loads that
   fed them. `_gc_pin`/`_gc_unpin`, about 280 call sites and the extern
   repin are gone. Tests that asserted the emitted pin protocol now assert
   its absence or the owner that replaces it (cleared shared-return slots,
   the staging-root clear, release pairing on scaffold error edges). Host
   pcc0, by node id against HEAD (`97014d7d4`): in the 239 files naming
   pin, unpin, barrier or lease, every file that fails on either side was
   enumerated; after rewriting 12 test files no failing node differs.
   Native integration programs of the
   lease slice's 47 files (pcc0, source-matched archive, `-m integration -k
   "not pcc1"`): 236 pass and the same 19 nodes fail. pcc1 and Linux were
   not run. Left in code generation for step 4: the graph lock around a
   root reload (`_emit_root_pin_lock`), the extern prior-pin read and
   `pcc_gc_take_pinned_slot`, and pin-named bookkeeping (`pinned_arg_temps`,
   `_pinned_gc_rooted_local_names`, the `ret.shared.unpin` label).
   (d), step 2, first part (Claude, local Mac, `1e892971b`): the runtime
   sources no longer call `pcc_gc_pin`, `pcc_gc_unpin` or a store barrier
   outside the pin/prior/take protocol: 166 pin/unpin calls and the barrier
   calls in 32 modules, 38 extern bindings, and the code that only fed them
   (the C-API buffer release lease scan, the module-state stability query).
   The fused pass already dropped each call, so behavior is unchanged. The
   249 pin/unpin calls in functions that read a prior pin bit, keep a pin
   array or hand off through `pcc_gc_take_pinned_slot` stay for step (4):
   75 test files model take_pinned_slot and assert that pin. Each edited
   module compiles alone. Host pcc0, by node id against HEAD, all 326 test
   files naming a changed module or pin/unpin: one test file rewritten
   (module-state registration takes no pin), no other difference (three
   compile-heavy nodes failed once and pass alone). Native integration of
   the 47 lease-slice files with the source-matched archive: 236 pass and
   the same 19 nodes fail. Runs were single-stream under
   `scripts/run_process_tree_sample.py` with a 2.75 GiB tree cap, after an
   earlier oversubscribed local run (six streams of six workers) pushed the
   Mac into swap. pcc1 and Linux were not run.
   (d), step 4a (Claude, local Mac, `d4e33e666`): code generation takes
   through the new `pcc_gc_take_slot(slot)` (load, clear, return) and no
   longer reads a prior pin bit, calls `pcc_gc_pin_slot_for_take` or takes
   the graph lock around a root reload: call results, returns, valueclass
   payload roots, extern roots (now `(slot, owned)` pairs), generator frames
   and yields, and module attribute stores. A sample program's IR fell from
   4709 to 4639 lines. Tests reading the emitted take were rewritten (51
   files with the code); host pcc0, by node id against HEAD, the 130 files
   naming the take, its labels or the changed helpers show no other
   difference. Not run: four heavy qualification files
   (`test_fallback_baseline.py` and three infrastructure gates), the
   integration suites (the user, 2026-11-25: "算了你不要跑集成测试了。机器资源太慢了。"),
   pcc1 and Linux. One native program (object returns,
   generator yields, extern text, a finalizer, an exception) matches
   CPython.
   (d), step 4b, first batch (Claude, local Mac, `6986377bb`, `03ce9e54c`):
   15 runtime modules (the C-API method and regex entries, dict
   materialization, file, listdir, os.walk, tuple iteration and import
   results; the str UTF-8, builtins cache, file lock, buffer and
   virtual-thread GC pins) take through `pcc_gc_take_slot` with no prior
   read, pin or graph lock; each compiles alone. Test models follow the one
   collector: the shared slot model (`test_set_call_slot_roots.Memory`) and
   the buffer model reach their collection points without moving objects,
   their users assert no movement, models provide `pcc_gc_take_slot`, and
   owner models expect no finish pin. Host pcc0, by node id against HEAD,
   the 100-odd test files naming those modules or models match. Not run:
   two compile-heavy files that exceed a 2.5 GiB tree cap
   (`test_native_container_builtin_error_paths.py`,
   `test_py_multi_file_bootstrap_shim.py`), the four heavy qualification
   files, integration, pcc1 and Linux. Remaining 4b: 23 modules, from
   `py_os_mkdir` (4 sites) to `py_class` (93).
   Correction: `pcc_gc_backend4_auto_step_pending` is the one collector's
   live automatic-collection request (young and full collections are
   scheduled through it), not a leftover; it may only be renamed, before
   the header/ABI freeze.
   Next for (d), in order, each with its tests rewritten in the same
   change: (1) done above; (2) the runtime sources: pin/unpin and barrier
   calls outside the take protocol done above; their lease calls move into
   (4) with the take-protocol pins, because root-copy tokens are released
   through the same `pcc_gc_foreign_lease_release`;
   (3) delete the fused-protocol pass after (4) (its test file
   named to the maintainer first); (4) `pcc_gc_root_copy_lease`,
   `pcc_gc_pin_slot_for_take` and `pcc_gc_take_pinned_slot` become
   lease-free forms, in four committed parts: (4a) a `pcc_gc_take_slot`
   (load, clear, return; the one collector never sets the pin bit, so a
   prior is always 0) and code generation emits it with no prior read,
   `pin_slot_for_take` or root-pin lock; (4b) the runtime's take callers
   (108 takes, 249 pins, 35 reasserts in 28 files) switch to it in file
   batches with their test models; (4c) the runtime's leases (about 190
   foreign-lease and 68 root-copy calls) become token-free in file batches
   with their models; (4d) the then-unused ABI functions go, the C header's
   exports only after the maintainer's answer; (5) remove the runtime's `pcc_gc_backend()` with its
   lowering (`unsafe_lowering.py`, `extern_lowering.py`, `runtime_abi.py`,
   `pipeline_freestanding.py`) and the worker env prefix's
   `PCC_GC_BACKEND=0`; (6) the closed-young analysis (a GC3 frontend
   analysis, unrelated to the one collector's generations) and the
   per-collector options of `scripts/pcc_perf.py`.

   The first tests-only (b) slice is admitted unqualified: the maintainer
   said “同意合入” and asked Codex to continue. The exact test patch of
   `c1b34833` collected 48,150 ordinary Python nodes with zero collection
   errors on its frozen Linux base; 42 focused native-program cases passed
   with pcc0 under the original guard. The full Python gate did not pass:
   the matching Linux pcc1 rejected the default fused-protocol regex.
   This slice changes no compiler or runtime files. On the frozen
   candidate, `prune_tests.py report` still listed 65 whole files and
   856 cases in 172 mixed files; audit them, rewrite tests of the live
   collector and remove only tests exclusive to retired collectors.
   Checkpoint (rule 13): Claude dispatches the due checkpoint from Mac
   after this slice lands, including reruns of #10 and #11 hosted lanes.
   The Linux Stage1 producer in [run 45356074767](https://github.com/ikshengmin/pcc/actions/runs/45356074767)
   reached compilation. The native worker and bootstrap selectors now require
   the single collector. The new bootstrap selector was collected on Linux,
   but Stage2/Stage3, native pcc1 tests and the stability checkpoint remain
   unqualified. Finish the Python test migration before treating this as a gate.
   (0a) Done on Linux: the young log is process-wide bounded without
       changing collection admission; see the regression evidence below.
   (0b) Slot slicing and allocation-free gray links landed. The 320k
       live-container probe improves on Linux; the complete pause target
       is not met.
       Exact reclaim now uses the existing terminal free protocol for
       already-cleared built-in containers; the 320k Linux pause fell from
       a median 226 to 173 ms. The full-to-young handover now also yields
       between batches, without admitting a young view over a partial batch.
       Sweep packs candidates into retired node blocks, with no growing
       buffer. Exact collection now frees detached raw tracking nodes
       after resuming, with safe-point polls between batches. Object
       destruction and synchronous collection semantics are unchanged.
       Linux native 320k exact-pause medians over ten rounds: 185 ms base,
       171 ms result (8/10 lower), 174 ms repeated base control, similar
       peak RSS; one result run reached 294 ms. No bound or throughput
       improvement is established. At `ba3ebafe`, the Linux 320k
       exact view spent about 7 ms enumerating and 24 ms creating nodes.
       A 64-cell raw allocation batch reduced instrumented node creation
       from 24.4 to 21.2 ms (seven pairs), but the uninstrumented exact
       stop had near-identical medians (166.0 vs 165.5 ms, 4/12 shorter,
       repeated-base control 174.7 ms). The candidate was not kept.
       Paused at `e74be5c94`: neither batch candidate established a
       gain; do not start another (0b) candidate before the collector
       interface gate. Exact synchronous reclaim remains unbounded.
   macOS verification of (0b) detached-node release: run
   `uv run pytest -x -v tests/python/test_gc0_high_fanout_pause.py tests/python/test_gc0_incremental_full_collection.py tests/python/test_gc0_generational_collection.py tests/python/test_gc0_young_log_bound.py`.
   On the parent seal `pcc_perf.py arm retire-base` and
   `pcc_perf.py arm retire-control`, and on the result seal
   `pcc_perf.py arm retire-new`. Run `pcc_perf.py ab retire-base retire-new
   --control-arm retire-control` and `pcc_perf.py table retire-new`.
   Platform macOS.
   macOS verification of (0b) candidate blocks: run the high-fanout and
   incremental full tests named below. On the parent build `pcc_perf.py arm
   blocks-base` and `pcc_perf.py arm blocks-control`, and on the result
   `pcc_perf.py arm blocks-new`. Run `pcc_perf.py ab blocks-base blocks-new
   --control-arm blocks-control` and `pcc_perf.py table blocks-new`.
   Platform macOS.
   macOS verification of (0b) handover: run the high-fanout and
   incremental full tests named below. On the parent seal `pcc_perf.py arm
   handover-base` and `pcc_perf.py arm handover-control`; on the result seal
   `pcc_perf.py arm handover-new`. Run `pcc_perf.py ab handover-base
   handover-new --control-arm handover-control` and `pcc_perf.py table
   handover-new`. Platform macOS.
   macOS verification of (0b) gray links: focused high-fanout and incremental
   full tests, plus a sealed A/B against its parent with a duplicate parent
   control arm and `table`. Platform macOS.
   macOS verification of (0b) slot slicing: run
   `uv run pytest -x -v tests/python/test_gc0_high_fanout_pause.py tests/python/test_gc0_incremental_full_collection.py`,
   seal the result, its parent and a second parent arm, then run
   `pcc_perf.py ab --control-arm` and `table`. Platform macOS.
   macOS verification of (0a) young-log cap: run
   `uv run pytest -x -v tests/python/test_gc0_young_log_bound.py` and
   seal the (0a) commit, its parent, and a second parent arm for
   `pcc_perf.py ab --control-arm` followed by `table`. Platform macOS.
   (a) Throughput of what landed on 2026-11-24. Measured by Claude on the
       local Mac at `4792d314d` against `90f4345db`, with a second build of
       the base as control, five pairs: w1 51,791 to 51,491 req/s (-0.6%,
       noise floor 26.5%), w4 120,000 to 121,972 (+1.6%, floor 11.7%):
       inconclusive, the host's load was 6.8 on 12 cores. Processor time a
       request, which load barely moves: 24.1 to 23.8 us at w1, 35.8 to
       35.2 us at w4, so no added cost shows. `table` gave 52,094 (60% of
       asyncio) at w1 and 86,098 at w4 under load 7.3, while the A/B median
       of the same arm was 121,972: the w4 table figure is not a result.
       Again on 2026-11-25 (Claude, local Mac, at the user's request), arm
       `gc0-latest` = `0b71d1214` against `gc0-head` with control
       `gc0-head-b`, five pairs, load 7.3 on 12 cores: w1 52,923 to 55,124
       req/s (+4.2%, 5/5 pairs, floor 7.3%), w4 137,646 to 139,821 (+1.6%,
       3/5, floor 8.1%), both inconclusive by the rule. Processor time a
       request: 23.5 to 21.9 us at w1 (-6.8%) and 31.9 to 30.7 us at w4
       (-3.8%), the direction of the emitted-protocol removal in (d). Against
       the recorded asyncio figures (taken on 2026-11-24 under other load):
       63% at w1 and 53% at w4.
       Next: the same `ab` with `--pairs 10` on an idle host (arms
       `gc0-head`, `gc0-head-b`, `gc0-master`, `gc0-latest` exist under
       `PCC_PERF_HOME`). Platform macOS.
   (b) Tests that still name removed entry points.
       `scripts/one_collector/prune_tests.py report` lists them from the
       tracked `removed.json` (what the removal took, against `90f4345db`),
       on any host: 75 files in which every test does and 192 files in
       which 951 tests do, at `4f0efe6db`. A test of
       the one collector is rewritten without the removed setter or loop
       and must pass; a test of collectors 1-4 is deleted. They fail until
       then, so compare a suite by node id against this list.
   (c) The functions left in twelve of the former modules.
       `scripts/one_collector/sweep_one_collector.py entries` names their
       callers. Remove what only collectors 1-4 used; move what the
       collector uses beside its users, then delete the module.
   (d) Emitted protocol. Stop emitting the pin, lease, relocation-read and
       write-barrier calls and retire the fused-protocol pass; remove
       `pcc_gc_backend()` and the compiler's reads of `PCC_GC_BACKEND`;
       remove the frontend's closed-young analysis, which has no consumer;
       remove the per-collector options of `scripts/pcc_perf.py` (`--gc`,
       the pacer option). Area `runtime-codegen`.
   (e) Collector thread default. A program in which threads change old
       lists and dictionaries while a cycle scans them; a join of the
       thread at process exit; an A/B at four workers. The maintainer then
       decides the default.
   (f) Replaced by (0b).
   (g) Linux: done at `cba826a9` (the slab-return programs, which call
       `page_discard`, and five shared-managed integration cases). Left:
       the external Go and JDK oracle cases, and Windows.
   (h) Memory still pinned after the occupancy-ordered slab lists, measured
       on the gateway. It decides whether object relocation becomes a unit;
       that unit starts with a design review (rule 15).
   Then the collector's throughput work, each candidate with its ceiling
   recorded first: borrowed temporaries in code generation, generator frame
   locals, the owner-thread fast path for counts and container locks.

   Claims:
   - (d) 4b runtime takes, second batch (py_dunder, py_os_mkdir, py_module_attrs_runtime, py_exc_tls, py_list, py_obj_stubs, py_int_convert, py_set, py_tuple): Claude, macOS, 2026-11-25T16:24Z, area runtime-codegen
   - (b) weakref pending-exception single-collector witnesses: Codex, Linux, 2026-11-25T17:08Z, area runtime-gc (test files and existing native fixture only)
3. **pcc-gateway throughput: handler against asyncio, server against
   aiohttp.** Maintainer addition of 2026-11-23: the native request path as
   a whole, beyond what one collector owns, at two levels. Collector-specific
   work stays in item 2. Every change keeps the five collectors' CPython
   results (finalizers, weakrefs, resurrection) and the gateway's HTTP,
   server and streaming checks. Gains come from generic compiled-code and
   runtime costs (call paths, ownership protocol, allocation, syscalls),
   never from route- or module-specific paths. Platform: any for code;
   numbers come from the local Mac (labeled local Mac evidence) or Linux.
   - Lower level, the in-process handler against its CPython asyncio
     counterpart (`benchmarks/handler/`): beat asyncio on every collector
     with one worker and with four; four workers are never slower than one.
     Measure with `scripts/pcc_perf.py`: `asyncio` records the reference,
     `arm` seals each build, `table` reports GC0–4 at one and four workers
     against it, `ab --control` decides a change, and `prim`, `gap`, `top`
     and `locks` localize. Current (2026-11-22, `fd567f664`, asyncio 88,674
     req/s): GC0 49,168 / 103,898 (one / four workers; 55% / 117%), GC1
     34,855 / 69,097, GC2 36,382 / 49,208, GC3 34,411 / 69,706, GC4
     27,045 / 37,985.
   - Upper level, the native `pcc-gateway` server against unchanged
     upstream aiohttp on the same host: beat it with one carrier against one
     aiohttp process and with four carriers against four processes. Build
     with `uv run pcc --backend self ../pcc-gateway/web_frameworks_app.py
     -o <bin>` and run `uv run python benchmarks/webserver.py --output <dir>
     --native <bin> --build-receipt <receipt> --python <aiohttp python>
     --zrk <zrk> --threads 3` with the RSpec options in the gateway's
     `benchmarks/web-frameworks.md`: GET /, GET /user/<id> and POST /user
     at C512, five-second warmup, 15 seconds per route; `--screen-route
     root` screens one route. The entry holds the performance lock,
     process-tree watchdog and RSS cap. For a change, add
     `--reference-native <base binary> --reference-build-receipt <base
     receipt>` (both arms built by the same command), an even `--repeats`
     giving at least five alternating pairs, and an unchanged control (a
     collector the change cannot reach, or a second build of the base);
     report medians, pair wins and the control's deviation. As in item 2, a
     change within a control floor of 5% or more is inconclusive. Current
     (local Mac, GET / C512, one carrier, 2026-11-16, `b7e97763`): GC0 6,182
     and GC3 4,167 QPS against aiohttp ~27,500 (22% and 15%). Since the
     handler already passes asyncio at four workers, most of the gap is in
     the server path; GC0 request CPU by mechanism (2026-11-15 census):
     reference counting, allocation, free, root stores, pins and leases
     about 53%, syscalls 8.7%, calls and frames 8.7%, attribute lookup 6.4%.
   User instruction of 2026-11-26 to Claude, while other agents do item 2's
   stabilization: "他们搞稳定性。你能推进GC优化吗。特别是融合之后还没有达到预期的。没有超过asyncio 的部分。"
   ("They do the stability work. Can you push the GC optimization forward,
   especially where the fused collector has not met expectations: the
   parts that do not beat asyncio yet.") This lets Claude run (c) beside
   item 2's stabilization, which the instruction of 2026-11-24 had put
   first; it changes no other agent's order. Each candidate still needs
   its ceiling recorded first and an `ab --control-arm` result, and it
   avoids the files of active item 2 claims.
   (c) profile at `0b71d1214` (local Mac, load 4-7): w1 21.7 us of
   processor time a request, of which the collector's root operations
   20%, allocation 6.6%. The profile is flat. The cache-line padding the
   one-collector cleanup removed is restored (`b234eea64`): 46 layout
   conflicts to 13, no measurable change. Candidate in progress, ceiling
   recorded first: `pcc_gc_load_ptr`, `pcc_gc_load_borrowed_ptr` and
   `pcc_gc_resolve_owned_ptr` are plain loads (or the identity) since the
   collectors that move objects were removed, but stay calls, with an
   entry poll that only a moving collector needed. Their self time is
   about 1.0 us of 21.7 us at w1 (4.6%) and 1.8 of 31.8 us at w4 (5.8%),
   plus the two flag loads of the AArch64 short path at each program load.
   Result: the fused-protocol pass, which runs for every program and
   runtime module, lowers them to a load of the slot (through a NULL word
   when the slot may be NULL) and to the identity. `ab` against a second
   build of the base (local Mac, load 9.2, five pairs): w1 53,685 to
   57,262 req/s, +6.7%, 5/5 pairs, floor 1.5%: a gain; w4 124,685 to
   129,614, +4.0%, 5/5, floor 4.1%: within noise. Processor time a request
   22.5 to 21.2 us and 34.0 to 32.7 us. The arm's primitives all matched
   CPython. Of the 149 test files naming the helpers, run file by file
   fail-fast with and without the change, four IR-shape tests trace the
   root read in either form now; every other failing file stops at the
   same node without the change, or names removed collectors (item 2
   (b)). Deleting the pass in item 2 (d)(3) moves this lowering into code
   generation; the AArch64 short path for the two loads then has no call
   left to shorten.
   Measured and not to be pursued (reference experiment, system clang on
   the local Mac, 2026-11-26): an uncontended atomic count update costs
   1.26-1.37 ns against 1.03-1.17 ns plain, and a biased update 1.23-1.58
   ns (owner compared through a thread-local or the thread pointer), so
   biased reference counting has no ceiling here; the per-request cost
   is the number of runtime operations, not their atomicity.
   Profile after it (arm `load-new`, w1, 17.4 us a request): `json.dumps`
   is 5.6 us inclusive (32%), object release (`_py_decref_finish` and the
   deallocators) 2.2 us, allocation 1.2 us. The json primitive (6.4 us
   against CPython's 1.7 us) spends 0.8 us in the list-append path's
   container lock and checks (`pcc_container_lock` and unlock,
   `pcc_gc_try_init_ptr_locked`, `_list_is_sane`, and
   `_container_fast_still_admitted`, which rechecks a collector-switch word
   no code sets to -1 any more), 0.2 us in Darwin TLV calls, 0.24 us in
   provenance checks. Arguments pass borrowed (no per-argument root or
   count). Containers taking a literal's reference was estimated at about
   0.2% and is not pursued. Candidates of 3% or more, each structural:
   (1) the list-append path's lock and checks, about 4% of a request
   (`py_list.py`, claimed by item 2 (d) 4b while that batch runs);
   (2) Darwin thread-local access: every access calls the TLV resolver
   through its descriptor (`_materialize_thread_local_address`), about 4%;
   inlining the resolver's fast path reads the pthread TSD array through
   `tpidrro_el0`, an undocumented layout Go also relies on, so it waits
   for the maintainer's decision; (3) borrowed temporaries in code
   generation (the root operations are 20% of a request). Below the floor
   alone and together (about 2.5%): the allocator's per-call statistics
   atomics, the magazine limit's division, the dict deallocator's debug
   slot check.
   Candidate (3) examined (Claude, 2026-11-26, emitted IR of small
   programs at `31a803cf0`): in an ordinary function an assignment from a
   call already takes the result's reference (`store ptr` into the local's
   slot) and arguments pass borrowed, so there is no retain-and-release
   pair to remove there. Generator and parking frames keep them: a
   literal's container root is counted (`pcc_gc_store_root` in and out)
   where ordinary functions use `pcc_gc_temp_root_enter_fresh`, by design
   for a literal whose element suspends (literal_lowering.py,
   `_enter_fresh_container_temp_root`); a delegated call's result is
   retained into its slot and the original released, then copied and
   cleared again; each resume stores and clears the handled-exception
   slot. Their ceiling on the handler is below 1%. The root operations'
   20% is mostly the ownership every Python local carries, done by a
   runtime call where CPython uses inline code; a backend short path for
   `pcc_gc_store_root` when neither value needs a count (NULL, tagged or
   immortal; a third of the calls clear an empty slot), like the existing
   AArch64 short path of the root reads, is the remaining form of (3),
   ceiling about 3% (not measured).
   Order for (c), Claude's recommendation that the user accepted on
   2026-11-26 ("按你的建议", then "完整的顺序包括你的启示"); each step records
   its ceiling first, keeps only a gain beyond an `ab --control-arm`
   floor, and leaves the object header layout alone:
   A1. Darwin thread-local access inline: read the pthread TSD slot through
       `tpidrro_el0` instead of calling the TLV resolver, with a startup
       check against the resolver and a fallback to the call (about 4%;
       waits on the maintainer, see "Waiting on maintainer").
   A2. Backend short paths of `pcc_gc_store_root`, the releases and the
       retains when neither value needs a count (NULL, tagged, immortal; a
       clear of an empty slot), as for the root reads (about 3-4%).
   A3. Short paths of the error check and the recursion-depth check (needs
       A1; about 2-3%).
   A4. The list-append path: container-lock fast path, without the
       recheck of the retired collector-switch word and with the sanity
       checks off the hot path (after the (d) 4b claim on `py_list.py`;
       about 4%).
   A5. Allocation and release bundle: per-thread batched statistics, the
       magazine limit by table, the dict deallocator's debug check behind
       a switch (with A1 or A4, together above the floor).
   Checkpoint after A1 and A2: if the trend does not support w1 at 75-80k
   req/s, report and reassess.
   B1. On an idle host, why a w4 request costs about 50% more processor
       time than a w1 request. B2. Scheduler: the global ready-node pool
       lock and the shared counters become per-carrier caches and batched
       statistics. B3. Class reference-count contention (an instance
       increments its class's count; a w4 instance costs 785 ns against
       231 ns at w1): module-level classes immortal or deferred, as CPython
       treats types, after stating the lifetime boundary.
   C1. Only if A4 leaves container locks at 3% or more of a request:
       Go-style per-function parameter escape summaries with Java-style lock
       elision for containers that stay in one thread, conservative at
       getattr, globals, closures and thread hand-off; a design note first.
   Not pursued: biased reference counting or locking (measured above; Java
   removed biased locking in JDK 18), whole-program analysis (neither Go nor
   Java does it), module-specific accelerators such as a native json.
   Next, in order: (a) four-carrier measurement: `web_frameworks_app.py`
   fixes `carrier_count=1` and the runner records one carrier, so add a
   carrier count to the app and runner and a four-process aiohttp
   reference (pcc-gateway only); (b) the largest owner of the C512 request
   path from a fresh busy-carrier profile (`scripts/run_process_tree_sample.py`),
   starting with the GC0 ownership protocol (reference-count chains,
   allocation and free, root stores, pins and leases); (c) the handler's
   one-worker gap to asyncio on GC0 (55%), localized with `prim` and `gap`;
   (d) per-site attribute lookup caches.
   Area: the pcc area of the unit's files (`runtime-codegen`, `runtime-gc`,
   `backend`); a unit that changes only pcc-gateway holds none.
   Mac attribution for (b) and (c): until
   [#17](https://github.com/ikshengmin/pcc/issues/17) every owned Mach-O
   executable carried the constant LC_UUID `11111111-…`, and `sample` (also
   behind `pcc_perf.py top` and `run_process_tree_sample.py`) named functions
   from another binary's indexed debug symbols. The UUID now comes from the
   image's load commands, fixups, symbol and string tables and export trie;
   a local Mac `sample` of a probe names its own functions. Re-check a Mac
   function profile taken before this fix against `nm` before using it.

   Claims:
   - none
4. **Source-language frontends**, in parallel with item 2 (different files;
   only heavy gates and shared runtime publication are serialized). Targets
   and each project's current first blocker:
   [multi-language status](multilanguage-status.md). Platform: any.
   Before pushing a frontend result, run the affected language's whole
   ordinary suite (AGENTS.md: whole affected-language validation).
   Checkpoint regression #10 came from two Go commits (`d9735bfdd`,
   `feef5185c`) that changed diagnostics without it. Next,
   in order. Go/Java: the first gate of the
   [shared managed storage](design/go-shared-managed-storage.md) contract
   that Claude approved at the maintainer's request on 2026-11-22
   ([#13](https://github.com/ikshengmin/pcc/issues/13)): one described
   layout tag on the existing header, a pointer-free and a reference-bearing
   control on the one collector (young collection, full collection, explicit
   collection), then generic slices. The gate named GC0–4 until the
   one-collector decision of 2026-11-24; Claude revised it and the design
   document the same day, at the maintainer's request to resolve the
   contradiction. New Go source-shape admissions stay frozen. The v1
   envelope is not promoted (Claude's recommendation; the maintainer has not
   decided promotion): it is a tuple-shaped owner, so a later tuple-specific
   step of the collector, such as no longer tracking a tuple of immutable
   items, would apply to mutable Go and Java objects. C++
   [#14](https://github.com/ikshengmin/pcc/issues/14):
   implicit/explicit destructor exception specifications and the non-throwing
   override rule now select ordinary versus effect-gated D1 destruction.
   Linux pcc0: cross-TU D1 controls match g++ 14.2. Generic TU-local
   static function linkage now preserves ordinary declarations, qualified
   definitions and internal inline semantics independent of specifier order.
   6033 C++ ordinary tests and 49 subtests passed; 11 new owned ELF
   programs match g++ 14.2. Unchanged TinyXML2 advances past `1199:9`
   and `2310:1` to `tinyxml2.cpp:2580:1`. The generic user-provided C1
   exception-specification owner now admits matching non-throwing ordinary
   and class-template constructor declarations, including separately defined
   member C1s; definitions with unowned escaping effects stay rejected.
   Linux host pcc0: 6060 ordinary tests and 49 subtests passed; five native
   ELF/C++17 G++ reference programs agree byte for byte. Unchanged TinyXML2
   still stops at `2580:1`, `cpp.unsupported.destructor-subobjects`:
   XMLPrinter constructs two DynArray members, then its Push call reaches a
   potentially allocating EnsureCapacity. No declared non-throwing contract
   qualifies this edge. Next C++: shared owned exception propagation,
   partial-subobject cleanup and matching deallocation; not an additional
   package-specific effect proof. See the
   [C1 frontier investigation](investigations/tinyxml2-explicit-virtual-destructor-effects.md).
   Native pcc1 still stops at the parser's no-libpython stub; no upstream
   executable or full C++ integration pass.
   C++ platform CI (45643044565, artifact 15035827358): all 86 owned
   ELF cases passed; G++ 13.3's sole reference failure is accepted DR
   CWG2394. The mandatory platform comparator now selects Clang 18 on
   Linux, with strict positive/negative capability and identity probes
   before all unchanged 86 cases; G++ mismatch remains separate evidence.
   Local Linux: 441 CI contracts, 6060 ordinary C++ + 49 subtests and 86
   owned cases passed; G++ 14.2 still 85/86. Next: run the actual hosted
   Clang gate; independent Source / cpp / reference remains at its 9/1207
   G++ failures, not waived. See [reference qualification](cpp-frontend.md#cpp17-platform-reference-qualification).
   The opt-in Go source probe reached `internal/bytealg/bytealg.go:9:2`; no dependency-closure executable exists.
   See the
   [closure audit](investigations/go-math-bits-original-closure-frontier.md).
   No full test execution or owned map/goroutine storage.
   Go CI next: rerun all source lanes, starting with the cold-cache six-worker
   integration batch from run 45619704293. Four simultaneous Go reference
   builds crossed the 1 GiB tree cap; builds sharing a cache now take turns.
   The new overlap regression failed before and passed after; 48 focused
   protocol tests and two native owned ELF controls passed on Linux. The
   hosted Go 1.27.3 integration and whole-language verdicts are pending.
   Java CI next: native scalar static blocks and blank-final initialization of
   already admitted primitive-array owners use the existing class guard, shared
   DA/DU flow, and lexical root retirement. Linux host: 706 focused ordinary
   tests and 2 lane-registry checks passed; JDK and managed native execution
   remain CI obligations. The v1 envelope, runtime ABI and #13 gate are unchanged.
   Run 45619704293's Commons Numbers and ZXing gates still require nominal static
   reference storage and class construction after #13; the six JDK-only failed
   nodes retain the normative conformance obligations, not a changed oracle.
   Rust CI next: the original fallible-streaming-iterator still fails at
   lib.rs:22:1 (core::cmp) in run 45619704293, artifact 15033286334. Closed
   source dependencies now admit edition-2015 explicit extern crates and
   nested module aliases with per-file pins; 552 focused tests and two native
   ELF link controls pass on Linux. This is not a core implementation. Next
   supply real pinned core source closure and owned no_core/lang-item semantics
   (the retained Rust 1.90.0 core root stops at line 75), then actual prelude
   trait/type identities. The strict original project remains a failure;
   neither an external token nor a caller-provided crate named core waives it.

   Claims:
   - none
5. **NumPy extension chain (Linux first) and the value model.** NumPy: the
   "numpy through uv locks" section below and allstoalls#153, #155. Value
   model (project-intent pillar 3): allstoalls#172, #178, #182. Platform:
   Linux first for NumPy, any for the value model. Next: not yet assessed in
   this order; read those sections and issues and fix the first observed
   blocker.

   Claims:
   - none
6. **Maintainer-selected branches**, after the items above unless the
   maintainer names one:
   - Local-model mode and GPU
     ([architecture](design/pcc-local-model-architecture.md), milestones M0
     to M6). Platform: macOS on Apple silicon for anything that executes
     Metal; Linux can only do static and Kernel IR work. Next: the M4
     remainder — fence-driven buffer release, no-copy buffers, more than one
     queue per context, a wait the collectors see as blocked — then growing
     `@gpu.kernel` beyond loop-free kernels (loops, reductions, threadgroup
     memory) before M5. M3, the Harness route, waits for the TAG-translated
     Harness in its own repositories.
     Maintainer decision of 2026-11-23, approving Claude's recommendation in
     chat: the operating system's Metal framework, called in-process through
     the Objective-C runtime (`newLibraryWithSource:` / `newLibraryWithData:`,
     as `pcc/gpu_runtime/metal_device.py` does), is an accepted platform
     boundary under the pcc1 ownership contract. Xcode tooling is not:
     `xcrun metal`/`metallib` (`pcc/kernel_ir/metal_finalize.py`) and the
     `xcrun clang` builds of Objective-C in `pcc/backend/metal.py`
     (`gpu_metal_runtime.m` and the generated runtime bridge) remain labeled
     reference routes and cannot satisfy a pcc1 claim. The MSL handed to the
     framework must be generated by pcc; results name the OS shader compiler
     and record the macOS build. Open unit: move those `xcrun clang` routes
     onto the owned Objective-C runtime path, or retire them by name with the
     maintainer's OK.
   - Agda-class proof checker
     ([exploration and implementation state](research/agda-class-proof-checker-exploration.md)).
     Platform: any (host CPython). Next: stage 2 (`with`, `rewrite`, records,
     `Fin`, `Vec`, `Dec`), continuing the church-turing edition.

   Claims:
   - none
7. **Long-standing CPython-replacement and standard-library issues**
   (allstoalls#157 to #170): advanced through the items above; take one
   directly only when the maintainer names it.

   Claims:
   - none
8. **Last: self-host fixed point and public CLI parity** (allstoalls#199,
   #200; section "Self-host fixed point: the final convergence step" below),
   together with every result that needs a current pcc1 (GPU M2, proof-checker
   stage 3). Bootstrap runs need the maintainer's explicit OK.
   Next: validate matching worker ceilings and bounded native export
   batching in CI; optimize repeated preload and indexed generation.
   Run 45643044565: Linux 2/621 codegen modules retired at Stage2 timeout;
   Windows not yet through class preload. The original 1,800 s / 16 GiB
   guards remain. Worker ceiling: 334 related tests passed. Actual prior
   native export binary, five pairs each: 31.8 KiB eight-module batch
   -33.5%, 62.5 KiB -7.9%, both 5/5 with identical merged metadata and AST
   bytes; 159 worker tests passed. Only guarded batches <=64 KiB / 8
   modules are admitted. Preload workers now receive the entire class
   graph without non-class exports, preserving root semantics and correcting
   the existing serial key order. Actual old native worker on 102 original
   compiler modules, five alternating pairs: wire 4.45→1.82 MB, worker
   3.107→1.582 s (-49.1%, 5/5), exact delta bytes and lower peak RSS.
   343 focused tests passed; exact projection body executed as native ELF.
   The shared x86 retarget/forward scan on 66 frozen real functions has a
   17.0% raw native owner gain (9.86% control-normalized median, 5/5 wins),
   identical packed object and assembly outputs; 14 new tests pass with
   all 12 existing x86 reds unchanged. Next: CI full equal-ceiling Stage2/3,
   fix the existing x86 correctness/diagnostic witnesses, then measure
   frame-register/strong-liveness and remaining per-root reconstruction
   independently. No full Stage2/3 speedup claim.
   [Phase investigation](investigations/stage2-worker-budget-parity-45643044565.md).

   Claims:
   - none
9. **Then: review this mechanism.** Maintainer decision of 2026-11-22: once
   pcc's kernel is stable, the "推进" mechanism in AGENTS.md and this work
   order are reviewed again. The trigger is the fixed point meeting its
   completion bar (Stage2 and Stage3 pass, Stage2 wall time not above
   Stage1), even if the pcc1-dependent results of item 8 (CLI parity, GPU M2,
   proof-checker stage 3) are still open. From then on an agent told "推进"
   starts no new item; it reports that the review is due and waits.

## One collector: GC0 is the fused collector (maintainer decision, 2026-11-24)

Maintainer decision, in a question Claude put on 2026-11-24 (option chosen:
"GC0, keep the other code"): the fused collector is built on GC0, reference
counting with the cycle collector, and GC1-4 stay in the repository and
keep building. The maintainer added the same day that the characteristic
feature of each of GC1-4 must enter the fused collector; they are not
dropped. Tests and performance acceptance target the fused collector. This
superseded the order of 2026-11-21 ("optimize each collector before
unifying"): the GC1-4 identities are not built as separate collectors.

Maintainer decisions later on 2026-11-24: slab evacuation is not built into
the fused collector (it needs the pin, lease and read-barrier protocol that
the fused protocol removes; replaced the same day, see "Relocation" below); the fused collector's pause must be very short
and independent of heap size, as ZGC's is; and the five-collector structure
is to be deleted quickly so that one collector remains, which then gets the
optimization work ("做完删除 5GC这种。然后主力优化融合GC。 只留一个gC").

One collector, stage 1 (Claude, 2026-11-24, **not executed**: the maintainer
assigned testing to Codex, "不测。我交给codex去测了"). The runtime selects
only collector 0: `PCC_GC_BACKEND` other than 0 aborts with a message and
`pcc_gc_set_backend(n != 0)` returns -1. The fused-protocol pass runs for
every program and runtime module (no `PCC_GC_FUSED_PROTOCOL` switch).
`pcc_perf` measures one collector. A script rewrote the collector loops of
698 test files to one collector (`for backend|gc|collector|gc_backend|col|
requested_backend in range(5)` and the equivalent tuples and
`parametrize` lists; other `range(5)` loops are untouched). AGENTS.md states
the one-collector contract.

For Codex to execute: the runtime archive build; the focused suites of the
fused collector (`test_gc0_generational_collection.py`,
`test_fused_protocol_pass.py`, `test_gc_tracking_*.py`,
`test_freestanding_gc_tracking.py`, `test_freestanding_gc_control.py`,
`test_slot_call_inert_leases.py`, `test_pcc_perf.py`); then the Python
language gate. Expected to fail until stage 2 deletes them: tests that
select collectors 1-4 by name or by `(1, 2, 3, 4)`/`range(1, 5)` loops
(about 368 files name a tracing or relocating collector), and
`test_slot_call_inert_leases.py`'s collector-switch case and
`test_pcc_perf.py`'s five-collector expectations, which need the same
rewrite. Stage 2: delete the runtime of collectors 1-4 and the protocol
entries only they used. Stage 3: full collection in bounded stops (below).

One collector, stage 2 (deletion; in progress, Claude, local Mac,
2026-11-24, on `90f4345db`). The maintainer asked the same day for the
redundant collectors and the selector to go, with no option left. Done so
far, mechanically, by `scripts/one_collector/` (its `iterate.py` describes
the procedure): every runtime module was folded under "the selected
collector is 0" and under the words no remaining function writes, the
functions nothing reaches were removed, and this repeated to a fixed point.
`pcc_gc_set_backend` is deleted; nothing reads `PCC_GC_BACKEND` in the
runtime any more; code generation no longer emits the closed-young-domain
call. Result: 27,740 lines removed from `pcc/runtime/py`, 641 exported
symbols gone, 696 ABI table entries, 176 header prototypes and 179 state
words with them; fifteen of the 27 modules below are deleted
(callback_layers, common_mark_cycle, conservative_roots,
forwarding_retirement, generational_scheduler, object_root_seeding,
relocation_copy/drain/payload/remap/selector, scalar_sweep,
tracing_sweep_collector, zpage_allocation, zpage_mechanics);
`py_gc_backend.py` went from 277 functions to 35. 107 test files that
existed only for collectors 1-4 are deleted.

Evidence: every changed module compiles alone (102 on this Mac;
`freestanding_linux_elf_image.py` needs a Linux target); the collector's
tests named under stage 3 and `test_gc0_object_slab_return.py` pass on the
reduced runtime.

Still open in stage 2:

- Twelve of the 27 modules keep about 89 functions: object_nodes (27),
  mapped_roots (21), generational_promotion (11), forwarding_identity (9),
  page_marks (5), incremental_concurrent_scheduler (4), sweep_slots (3),
  barrier_dispatcher (3), alloc_stage (2), zpage_lifecycle (2),
  gray_worklist (1), generational_oldification (1). Some are what the one
  collector uses (pause records, root visitors, `clear_referents`); the
  rest is still called through `pcc_gc_shade_gray_atomic`, the
  owner-payload-span calls of `py_class.py` and the telemetry.
  `scripts/one_collector/sweep_one_collector.py entries` lists the callers.
- `pcc_gc_backend()` still exists and answers 0 (it also initialises the
  configuration); `PCC_GC_BACKEND` is still read on the compiler side for
  cache keys and labels (`deferred_frontend_schedule.py`,
  `pipeline_frontend_workers.py`, `package/runtime_profile.py`); the
  closed-young analysis of the frontend (`closed_young_domain.py`,
  `closed_young_closure.py`) has no consumer left.
- Tests: `scripts/one_collector/prune_tests.py report` finds 75 more test
  files in which every test names something removed, and 192 files in
  which 951 tests do (at `4f0efe6db`). Many of them test the one collector and only select
  it through the removed setter or loop over removed entry points, so they
  need a rewrite, not deletion; none was touched.
- The pin, lease, relocation-read and write-barrier calls are still
  emitted and dropped by the fused-protocol pass.
- Comments and docstrings of folded functions still describe the removed
  arms in places.
- `PCC_RUNTIME_GC_STRESS_EVERY` (`runtime_gc_pressure.py`) matches source
  text; its tracing hooks were removed with their targets, the remaining
  ones were not run.

Lesson recorded in the tool: a word counts as constant only when no
function still in the sources writes it, reached or not. The first version
looked at reached functions only and folded the collector thread's start
away (its switch is written by a setter that only a test names); the
`concurrent` case of the stage 3 test caught it.

The map the deletion started from: after stage 1 no code of collectors 1-4
could run, so this is dead-code removal with no behaviour change. The 27
modules below were 18,207 lines, referenced about 830 times from 40
surviving files; `py_gc_backend.py` (7,000 lines) was the hub.

    freestanding_gc_*: alloc_stage barrier_dispatcher callback_layers
    common_mark_cycle conservative_roots forwarding_identity
    forwarding_retirement generational_oldification generational_promotion
    generational_scheduler gray_worklist incremental_concurrent_scheduler
    mapped_roots object_nodes object_root_seeding page_marks relocation_copy
    relocation_drain relocation_payload relocation_remap relocation_selector
    scalar_sweep sweep_slots tracing_sweep_collector zpage_allocation
    zpage_lifecycle zpage_mechanics

`py_shared_managed.py` (Go and Java managed storage) is outside the default
runtime archive: `pcc/runtime/shared_managed_build.py` builds it separately.
It calls `pcc_gc_backend`, `pcc_gc_publish_initialized` (work only on the
former collector 4), `pcc_gc_store_ptr` and the root handle registration
(`pcc_gc_scheduler_root_register_handle`). The archive build and the fused
collector's suites do not reach it, so after each deleted group also run
`tests/go/test_managed_contracts.py`,
`tests/java/test_source_native_arrays.py`,
`tests/runtime_shared/test_managed_owned.py` and
`tests/runtime_shared/test_java_reference_abi.py`. Their cases that build
and run `py_shared_managed` are integration cases gated on Linux x86_64 and
`PCC_RUNTIME_ARCHIVE`; a Mac run executes only their source-level cases, so
the native path is a Linux check:

    PCC_RUNTIME_ARCHIVE=<owned archive> gtimeout 1800s uv run pytest -x -v -n0 -m integration tests/runtime_shared/test_managed_owned.py tests/runtime_shared/test_java_reference_abi.py tests/go/test_managed_contracts.py tests/java/test_source_native_arrays.py

Linux x86-64 at `cba826a9`, host pcc0 emitting owned native programs:
17 focused collector and ABI cases passed (five bounded-stop cases,
generations, three slab-return programs and eight ABI checks). The four
shared-storage files ran 173 ordinary cases; five integration cases passed
with the source-matched runtime archive, including the managed graph, scalar
slice ownership, Go managed allocation and the Java reference/roots client.
The Java client uses a separately built `PCC_JAVA_MANAGED_OBJECT`. External
Go/JDK oracle cases, pcc1, Windows and post-removal QPS were not run.
The explicit-collection pause telemetry and self-assignment ownership
harnesses now run without the deleted selector, using the owned C fixture
linker. Both passed; remaining selector-dependent tests are not qualified.

Stage 3 (full collection in bounded stops; written by Claude on the local
Mac, 2026-11-24, uncommitted on `90f4345db`).
`freestanding_gc_backend0_incremental.py` replaces the stopped-world full
collection of the automatic path. A cycle gives every old (not young)
tracked container a node through a cursor over the allocator's cells
(`pcc_allocator_object_cells_enumerate_slice`), subtracts internal
references, marks what outside referrers reach and sweeps its view, a
bounded part in every stop (`pcc_gc_full_slice_us`, 200 us of work by
default), with the program running in between. The unreached objects become
young again in one stop and the next young collection decides among them,
so nothing is freed on stale counts and `pcc_gc_backend0_collect_candidates`
is not used. A stop is requested by every automatic-collection request and
by every 1020 containers a thread tracks. `py_gc_untrack` removes an
object's node while the view exists; a complete stopped-world view
(`gc.collect()`, `gc.get_objects()`) makes the cycle give up its nodes; with
automatic collection off the cycle releases its view without candidates.

Evidence (local Mac, 2026-11-24, working tree on `90f4345db`):
`tests/python/test_gc0_incremental_full_collection.py` passed 5 of 5 and
`test_gc0_generational_collection.py` passed, one run each: old cyclic
garbage reclaimed in hundreds of stops; the object graph changed during
every phase; snapshot, `gc.disable()` and `gc.collect()` during a cycle;
weak references and resurrection; and the collector thread below with four
allocating threads. The acceptance probe (maximum stop at 20,000 / 80,000 /
320,000 long-lived containers) has not been run.

    PCC_NO_AUTO_PCC1=1 gtimeout 900s uv run pytest -x -v -n0 tests/python/test_gc0_incremental_full_collection.py tests/python/test_gc0_generational_collection.py

Handler throughput, same day and host (12 cores, load 5.1;
`scripts/pcc_perf.py`, arm `gc0-head` = `90f4345db`, arm `gc0-stage3` = that
tree with this section's runtime files): CPython asyncio 87,011 and 263,203
req/s at one and four workers; `table gc0-stage3` 51,178 (59% of asyncio)
and 121,566 (46%), 23.8 and 35.7 us of processor time a request, 1 ms
stopped; `ab gc0-head gc0-stage3 --control-arm gc0-head-b`, five pairs:
+0.2% (4/5 pairs, floor 0.5%) and +0.1% (4/5, floor 2.4%), both within
noise. The handler seldom reaches a full collection: this shows no cost on
the request path, not the pause bound.

Young-log growth (0a), fixed: Linux x86-64 at `cba826a9` grew to
99 / 197 / 295 / 393 chunks in four batches of 100,000 track/release
pairs, with no net growth or collection. The log now reserves at most
128 chunks of 8 KiB (1 MiB process-wide). Saturation does not trigger a
collection; the next already-requested young collection uses the
existing exact allocator/index flag enumeration (which can scan the
heap). Exited carriers' logs remain visible until reset, then their
chunks are freed. The native churn and exit regressions in
`tests/python/test_gc0_young_log_bound.py` check the cap, no extra
collection, reclamation of the young cycle without the old cycle, and
reclamation of 16 departed carriers' cycles and chunks. No throughput
claim; macOS A/B is pending.

The 200 us budget is not a pause bound. A Linux x86-64 screening run at
the same revision, with one old list retaining 20k / 80k / 320k lists,
observed per-step maxima of 1.18 / 7.98 / 27.76 ms; nesting the same
children in lists of at most 256 gave 0.47 / 0.83 / 0.59 ms. The full
collector checks the budget between objects, not between their slots.
The next bound must include a high-fanout container as well as heap size.

The (0b) slot slice resumes by ordinal, not a pointer into container
storage. Candidate discovery is still approximate across slices and the
existing young collection decides reclamation. A native regression resizes
and shifts the old list during subtract and mark and checks both live
references and collection of an old, unreachable, high-fanout self-cycle.
It failed on the parent without slot slicing; 17 focused Linux cases passed
with the change. Three runs of the live high-fanout probe gave median
per-run maxima 0.50 / 0.52 / 6.50 ms at 20k / 80k / 320k (320k maximum
7.29 ms); this is not a strict pause bound or a throughput A/B. A further
run using `tests/python/fixtures/gc_full_high_fanout_scaling.c`, linked
via `tests.owned_runtime_c_fixture.link_c_harness`, gave 0.25 / 1.82 /
5.59 ms. Pass the heap size as argv[1] and optionally `grouped` to use
256-slot intermediate lists. This excludes the initial aging collection,
and attributes a step by its starting phase (it may cross phases).
The largest mark steps in three 320k traces began before root scanning
and coincided with gray-array growth from 262,144 to 524,288 entries.
Newly allocated 4 KiB gray chunks lost all five initial pairs and were
discarded. The landed version instead links the full-view nodes already
owned by the collector, with no separate queue allocation or copying.
Linux native: 10 focused tests passed, including a wide two-level graph
that checks descendants are not lost. Ten CPU-pinned alternating 320k
pairs against `c9162e9c`: median per-run longest step 6.52 → 0.69 ms;
mark-start step 6.52 → 0.44 ms (10/10 lower for both); candidate worst
1.44 ms. The unchanged grouped-list control was noisy. This is a
targeted pause probe, not a 200 us end-to-end bound or a throughput
verdict. Candidate buffer growth and handover, root and extension
traversal, and explicit collection remain separate.

The unreachable control uses `tests/python/fixtures/gc_full_unreachable_scaling.c`
with the same link helper and argv. It verifies that a whole unreachable
component is handed over, still tracked until the exact young decision, and
then reclaimed. The exact collector now publishes terminal ownership only
for cleared built-in lists, tuples, dicts and sets, enabling the existing
metadata-free route when its provenance, identity and free-watch checks
allow it. The mixed built-in regression also checks that a live referent
keeps its ownership. Linux native: 13 distinct focused cases passed.
Ten CPU-pinned, alternating 320k pairs against `a2e7bc36`: median exact
young pause 226.2 to 173.3 ms (9/10 lower, one 284.7 ms candidate);
median handover essentially unchanged at about 10 ms. This is a targeted
pause probe, not a throughput claim. macOS was not run.

The handover now checks its deadline every 64 candidate addresses. Automatic
collection defers a young view until completion; admission racing the
handover takes an exact full view rather than exposing a partial batch.
Disabling automatic collection mid-handover never initiates reclamation.
The native test observes multiple handover slices at zero work budget
(previously none), exercises both admission paths and checks the disabled case.
Ten pinned 320k Linux pairs: one old handover pause had median 9.51 ms;
the new maximum per run of the handover-start slices had median 0.32 ms
(worst 0.46 ms, 52–56 slices). Including sweep-start slices the candidate's
worst from those two phases was 1.33 ms. Exact young reclaim remained at
about 166 ms median; this is not a bound on the complete collection or
a throughput result. 16 focused native and 7 ABI-chunk checks passed.
The extra port ABI suite stopped on a baseline test that opens removed
`freestanding_gc_relocation_payload.py`. macOS and hosted CI were not run.

Sweep no longer allocates or resizes an array of candidate addresses. The
first retired words in each full-view node block hold the packed addresses;
handover frees the retained blocks as it goes. An exact collection that
interrupts sweep releases the packed prefix separately from the indexed
suffix. Linux native: 17 focused cases passed, including that abort path,
resume/disable, races, and exact reclamation. Ten pinned 320k pairs
against `d94ccf0c`: median of each run's longest sweep-start pause 1.076
to 0.390 ms, 10/10 lower (candidate worst 0.604 ms). The handover medians
were 0.370 and 0.367 ms; peak RSS was about 119.5 MiB on both arms; exact
reclaim varied, medians 179.6 and 177.3 ms. This is a pause probe, not a
throughput claim; macOS and hosted CI were not run.

Collector thread, the concurrent feature of the former GC2 (off by default:
`pcc_gc_full_concurrent`, set through
`pcc_gc_backend0_full_set_concurrent(1)`; the program of its test case
passed 30 consecutive runs on the local Mac). It stays off (Claude's
choice, not a maintainer decision) until three things exist: a program in
which threads change old lists and dictionaries while a cycle scans them,
a join of the thread at process exit, and an A/B at four workers. When a cycle begins in a threaded
program with atomic counts and container locks, a detached thread does the
passes with the world running. It holds the object-graph lock for a batch,
scans a list or an instance under that object's container lock, reaches an
object only from its node under the node's shard lock and keeps it alive
with a reference while it scans it, never dereferences a child, and skips C
extension objects. The large-object part of the build, the runtime's roots
and the hand-over to the young generation take a stop. Mutators assist with
a stop of their own when the work done is behind sixteen node visits per
tracked container (`pcc_gc_backend0_full_step`). A reference the thread ends
up owning is released by a mutator. The fifth case of the test file above
(`concurrent`) exercises it with four threads. Not handled: the thread is
not joined at process exit.

Memory return, the purpose of the former GC4 (done without relocation,
Claude, local Mac, 2026-11-24). The maintainer said in this session that the features of
GC2 and GC4 are both to be done, which replaces the decision above not to
build slab evacuation. What it buys is memory, not throughput: a 64 KiB
object slab stayed resident for the life of the process once any object
had lived in it.

Step 1, done: empty object slabs go back to the system.
`pcc_allocator_trim_objects` discards the pages of fully free slabs through
the new `pcc.unsafe.page_discard` intrinsic; the mapping and the span stay,
because lock-free provenance readers read the state word of any candidate
address, and the size class carves the slab again when it next needs cells
(`_recarve_object_slab_locked`). A full collection, explicit or the end of
one in bounded stops, ends with this trim and with the raw-slab trim, which
nothing called before. `page_discard` maps fresh anonymous pages over the
range on Darwin (`MADV_FREE_REUSABLE` left 6 of 9 MB resident), is
`madvise(MADV_DONTNEED)` on Linux and `VirtualAlloc(MEM_RESET)` on Windows;
only Darwin was executed.

Steps 2 and 3, done differently: nothing is moved. Moving an object means
rewriting every pointer to it, and this runtime holds uncounted raw
pointers (borrowed temporaries across calls, internal caches) with no
protocol left to find them in a stop, while `id()` and the default hash
are the address; a missed pointer would be silent corruption (Claude's
judgment). The allocator keeps sparse slabs from forming instead. The
central free cells of the object family are no longer one list per size
class: every slab keeps its own (span words 64 to 88), and a size class
keeps its slabs in lists by occupancy (dense: fewer than half of the cells
free; sparse; empty; discarded). A cell comes from a dense slab first, so
new objects fill the slabs that are nearly full and a sparse slab empties
as its objects die. The trim then takes the empty list, all but the slab
emptied last, so its work follows the number of empty slabs, not of free
cells. What stays: a slab that holds one long-lived object.

Evidence (local Mac): `tests/python/test_gc0_object_slab_return.py`, three
programs: a peak of 200,000 objects with one in a thousand kept (more than
100 slabs returned, the resident size falls by more than half of their
bytes, the same size classes carve the slabs again); five rounds over
several size classes; and a heap of sparse slabs (one object in ten left
in every slab), two million short-lived objects of which 10,000 stay, then
the first survivors freed: 284 slabs (17 MB) returned, resident size 51 MB
before and 33 MB after. Not run: the same program on the previous
last-in-first-out lists, and an A/B of the handler (the central lists are
on the magazines' refill and drain paths, not on the allocation fast path).

Claude's proposed mapping of the remaining features onto the counting base
(a proposal; GC4's line is superseded by the decision above):

- GC1, incremental: the cycle collector runs in bounded mutator-paced steps
  instead of one stop per collection (bounded pause).
- GC2, concurrent with assists and a pacer: cycle detection marks in the
  background, paced by allocation, with mutator assists.
- GC3, generational: cycle candidates are aged, and a young pass examines
  only recent containers (as CPython's generations do).
- GC4, relocating: sparse object slabs are evacuated in a stopped world to
  return memory (compaction), with the existing slab evacuation.

Evidence at the decision (local Mac): GC0 w1 48.8k req/s against 33k for
GC1-3 and 26k for GC4; only GC0 runs finalizers when CPython does (on
GC1-4 an object dropped in the current frame is finalized one collection
late, `test_bulk_nonterminal_retirement.py`'s native order case).

Work, in order, each step with an A/B and its ceiling recorded first:

1. Borrowed temporaries in code generation: a value with a stable holder
   for the duration of a call is neither rooted nor counted. One request
   makes about 1,000 root-store and reference-count calls for 38 objects
   (327 `pcc_gc_store_root`, of which 102 clear an empty slot and 178
   release a temporary); that protocol is 24% of GC0's request CPU.
2. Generator frame locals: fewer frame-slot round trips per suspension
   (about 10% of request CPU).
3. Owner-thread fast path for counts and container locks (biased
   reference counting); atomics, locks and thread-locals are about 20%.

Measured and rejected on 2026-11-24, not to be retried as is: inlining the
count tests at every call site (IR grows 3.3x and the benchmark build
exceeds its memory cap) and flattening the allocator's helper calls
(handler +0.6%, 11/15 pairs; a call costs about 1 ns here).

## Collector measurement rules

These rules came with the five-collector plan of 2026-11-21 and hold for
the one collector. That plan ended with the one-collector decision of
2026-11-24; its text is in this file at `cba826a93`.

Each performance candidate needs five same-input A/B pairs and an unchanged
control: `scripts/pcc_perf.py ab --control-arm NAME` adds a second build of
the base arm. The control's largest deviation from 1.0 is the noise floor,
and `ab` gives each row a verdict against it. A change counts as a gain or
a loss only beyond that floor. When the change is within the floor and the
floor is 5% or more, the A/B is inconclusive. It then counts neither as a
gain nor toward freezing; rerun it with more pairs or on a quieter host.
Freeze the collector for optimization when three consecutive conclusive
candidates each have measured gains below 5%, or an Amdahl ceiling below
10%. Correctness fixes continue.

Code merged before its candidate can be measured must stay inert: no added
call, load or store on the common path, shown by an `ab` against a control
arm with no loss. Otherwise keep the slices on a review branch and merge
them with the first measurable candidate. Commit or report after each step,
report any hour without output, move to the next ready item when blocked,
and give the maintainer the macOS commands to run.

[#4](https://github.com/ikshengmin/pcc/issues/4) remains open/intermittent;
no independent macOS acceptance is claimed here.

## Self-host fixed point: the final convergence step

Maintainer decision of 2026-11-22, translated: the self-host fixed point comes
last. pcc keeps changing, round after round; the fixed point is the
convergence that closes the work, after which the project has a stable kernel.
It is ordered after GC correctness, the GC0–4 optimization and unification
above, the source-language frontends, the NumPy extension chain and the value
model. Evidence that needs a current pcc1 (GPU milestone M2 in
[the local-model architecture](design/pcc-local-model-architecture.md), stage 3
of the [proof checker](research/agda-class-proof-checker-exploration.md)) moves
to that step with it. Self-host stays a required outcome; this decision orders
the work and does not change the contract.

Completion bar, set by the maintainer the same day: Stage2 and Stage3 pass,
and Stage2 wall time does not exceed Stage1, because Stage2 is the native pcc1
compiling the same source that host pcc0 compiles under CPython in Stage1.
Compare the two stages with the same source, worker count and cache state.

State against that bar: not met. In CI run 45643044565 at `5f101c61`,
Stage1 compiled 621/621 modules with four workers in 1,466.275 s on
Linux x86-64 and 2,377.649 s on Windows x86-64; Stage2 timed out at the
unchanged 1,800 s whole-owner watchdog on both. The legacy single-chain
two-worker ceiling is now corrected to match its Stage1 receipt.
Stage2 native work is substantially slower even within matching module
phases. Details and focused validation:
[worker-budget and native phase attribution](investigations/stage2-worker-budget-parity-45643044565.md).
No equal-condition completed-stage comparison or fixed point yet;
[allstoalls/pcc#200](https://github.com/allstoalls/pcc/issues/200).
