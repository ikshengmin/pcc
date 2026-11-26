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
That run is a red baseline. Pending push-triggered exact-source validation:
[run 45663526933](https://github.com/ikshengmin/pcc/actions/runs/45663526933),
head `1f26ddde4c812c6774c5e457e912fe705828afd2`, observed pending after
the authorized non-skip result. It includes the worker, reference and
Stage2 improvements; the earlier run 45643044565 retains its independent
evidence. Do not dispatch a duplicate; compare actual terminal results. The latest completed
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

- Item 3 (c) measurement rule (asked by Claude, 2026-11-26): may a change
  whose request-level gain is below the host's floor be merged on
  mechanism evidence, that is a `prim --against` gain on its yardstick
  primitive beyond that primitive's control, plus a `top --against` drop of
  the targeted functions, with the handler `ab` run once per bundle? On
  2026-11-26 a loaded host (load 7.1) gave a 4.2% floor, which no single
  1-3% change can pass. Claude's recommendation: yes.

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
   #20 newest hosted run 45663526933: the same native reader import-closure
   assertion failed the separate Windows RSS publication job and all three
   completed worker-prior default-xdist jobs (Linux x86 artifact
   15047631327, 1262 passed / 1 failed). The reader must still exclude the
   host-only WinAPI/ctypes descriptor adapter, but the separately owned
   Windows pool lock legitimately imports msvcrt. Scoped that assertion to
   the actual reader and verified the owned locking ABI with a real
   x86-64 Windows COFF import. Exact preimage red; 33 reader/pool tests
   passed on Linux. No production change; hosted Windows execution is
   pending a later CI head.
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

   #20 cross-target indexed witnesses: six local Windows x86 red nodes
   generated Linux IR first, then asked the placeholder-only target binder
   to change its concrete ABI. All four fixture callers now select and
   assert the target before lowering, including actual PE text/streamed
   object, inline-edge and two-pass object parity and the Darwin slot-reuse
   and x86 lifecycle witnesses. 17 affected tests pass (7.13 s,
   119,037,952-byte peak, original 90 s / 1 GiB guard). The expanded
   84-node batch retains six pre-existing lifecycle negative-test failures
   (the unchanged file alone gives 62 pass / the same six fail); the
   earlier verifier rejection is a separate witness task. Next: stale
   SymbolMeta controls (weak field), remaining negative verifier witnesses,
   and other independent CI failures. No production target or verifier
   behavior was changed.

   #20 x86 assembler symbol metadata: two old positional test constructors
   no longer matched the weak field that the real metadata carries. Tests
   now name every field, include weak equality/replace and exercise both
   global and weak declarations with 4,096 temporary labels (one metadata
   record, unchanged ELF binding and serialized round trip). Source-matched
   192-member runtime: all 182 related non-integration tests pass,
   including execution of the C weak-definition fixture (3.87 s,
   101,351,424-byte peak); eight integration nodes not run. Next: remaining
   lifecycle negative-witness target IDs and independent CI failures.
   No assembler/symbol behavior changed.
   #20 indexed lifecycle negative controls: unique SSA names now include
   suffixes; the old mutation guessed bare names and could target value -1.
   Condition and cleanup-root controls follow actual edge/call IDs and
   assert the observed mutations (including the precise alloca type and
   landing slot). All six original failures now exercise the real intended
   semantic rejection after ordinary typed verification passes; the
   independent encoded witness also rejects them. All 97 related indexed
   and textual lifecycle, x86 inline-edge and slot-reuse tests pass under
   90 s / 1 GiB (4.59 s, 110,600,192-byte peak). Next: stale constant-lane
   selector negative witnesses and remaining backend correctness red.
   Production verifier/GC semantics unchanged.
   #20 indexed bridge selector negatives: constant vector lane extraction
   is now implemented, so the old unsupported-instruction fixture no longer
   reached an unsupported selector. The new valid dynamic-lane witness
   retains the real selector rejection, earlier SSA verifier ordering,
   later corrupt-wire rejection and emission state restoration. All 87
   affected tests pass (2.82 s, 85,504,000-byte peak). The same whole
   ordinary x86 batch now gives 1,418 pass / one pre-existing failure
   (register homes across the pointer-retirement marker), with 15 passing
   subtests and four deselected; 26.61 s, 142,790,656-byte peak. Eleven of
   the former twelve x86 baseline failures are repaired. The remaining
   optimization witness is still open; no selector behavior was changed.

   Complete pytest overlap fixture (run 45643044565, artifact 15044643830):
   the fixed 330 MiB allowance rejected a real child whose inherited host
   high-water was 773,967,872 B (padded reservation 1,101,677,568 B).
   The two-worker trim test now shares the measured cold-child pressure
   fixture: one fixed scenario budget, verified small-pair fit, actual
   bounded growth, one cancel/backoff/retire/exclusive retry, and the original
   local accounting trim receipt. The added 256 MiB retired-history witness
   executes the exact node in an isolated coordinator. Linux: the original
   high-water reproduction failed before, passed after (381,505,536 B real
   tree peak); whole affected file 85 ordinary passed, and all seven focused
   nodes passed with six workers (884,301,824 B peak, unchanged 1 GiB guard).
   No worker production policy, outer guard or CI worker count changed.
   Windows/macOS execution remains hosted evidence.

   Complete pytest runtime oracle (run 45643044565, artifact 15044643830):
   the exact checkout CLI now runs as `sys.executable -m pcc` from the
   repository, rather than requiring an installed console shim on PATH.
   Linux source 92392bb4 plus test change, matched 192-member runtime:
   all 15 tests passed (13 native programs byte-for-byte against CPython,
   empty-PATH CLI and corpus inventory), 89.33 s, 272,707,584 B peak
   under the unchanged 90 s / 1 GiB guard. Hosted verification pending.

   Claims:
   - #20 x86 constant GEP register-home witness: Codex, Linux, 2026-11-26T03:02Z, area backend (x86 register homes and its tests only; separate from AArch64 selection)

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
   (b) weakref pending exception: the old conservative-root scenario
   caused eight failures under the single collector. Retained 15 current
   source-body cases (success, raised error, allocation/registration
   failure, nested callbacks and TLS restoration), removed only duplicate
   conservative scenarios and retired relocation witnesses. The two owned
   C/PCC native tests now assert immediate nested error disposal
   unconditionally, with both provenance probes and no collector selector.
   Linux at `cf372804`, fresh verified 192-member runtime: all 17 passed, including actual
   native output and clean TLS. The prune report no longer marks this
   model file. Adjacent instance-finalizer tests still depend on removed
   lease-freeze/thaw models; migrate those independently, not by skipping.
   (b) finalizer/instance weakref ownership: the current body witnesses
   no longer import the removed lease-freeze/thaw or count-free root
   seeding model. They run the live finalizer, refcount tail and instance
   deallocator with the stable default owner and the optional checked
   provenance probe; temporary counted ownership, TLS, reentry,
   resurrection, weakref ordering and payload/class release remain tested.
   Linux at `3e4e3f55`, source-matched 192-member runtime: 28 source-body
   controls and two pcc0 native/CPython comparisons passed, including both
   provenance settings. pcc1 nodes remain in hosted CI. Both test files
   no longer appear in the prune report; no production runtime change.
   Next: migrate other independent (b) lifetime tests; investigate the
   parent-cycle order and may_park frontend boundary separately. Do not
   wait for the local Mac.

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
   (d), step 4b, second batch (Claude, local Mac, `86b321f68`, tests to
   `9675bed27`): 9 runtime modules (user finalizer dispatch, os.mkdir,
   sys.modules and the threaded name-key cache, raise/raise_owned, list
   factory and getter, buffer and bytes-decode finishes, int-bytes results,
   set constructor/binary/pop/snapshot results, tuple conversion and
   concat) take through `pcc_gc_take_slot`; the prior reads, pins, unpins
   and reasserts go with os.mkdir's pins array, `_set_constructor_pin` and
   the tuple pin/restore helpers. Each module compiles alone. The shared
   exception-constructor model now moves nothing too; os.mkdir leaves the
   pin-bit metadata inventory (nine sites). Host pcc0, by node id against
   HEAD, the 105 files naming those modules match (the renamed inventory
   node fails for the same pre-existing `_path_clear_root_prefix` reason).
   Not run: the heavy files named above, integration, pcc1 and Linux.
   Remaining 4b: 14 modules, from `py_obj` (7 sites) to `py_class` (93).
   (d) U1, 4b third batch (Claude, local Mac, `b9dfc1996`, tests
   `b079efeab`): py_obj, py_coroutine, py_obj_ops_compare and py_tempfile
   drop their pins. The coroutine finish takes its result through
   `pcc_gc_take_slot`; its legacy-pin words stay unused until 4c removes
   the lease tokens beside them. `pcc_gc_temp_root_enter_fresh`/`leave_fresh`
   keep only the counted root, `pcc_gc_immortalize` no longer pins,
   `sorted()` and borrowed `in` drop pin/unpin, and tempfile's prior-pin
   array goes (its finishes take through `pcc_gc_take_slot`). Each module
   compiles alone. Models: the coroutine protocol model takes through
   `pcc_gc_take_slot`; the metadata-flag inventory loses the five tempfile
   pin-restore sites (four left) and the native fixture expects no
   immortalize pin; the tempfile IR checks look for the `_td_hold` and
   `_td_register` calls in the raw IR, since an optimized body may inline
   them (an inlined registration is its single root-handle registration;
   HEAD already failed these three nodes for that reason). Host pcc0, by
   node id against HEAD `8887e4917`, the 101 files naming those modules or
   models have no new failing node. Not run:
   `test_py_multi_file_bootstrap_shim.py` (over the 2.5 GiB tree cap),
   integration (including the native metadata fixture), pcc1 and Linux.
   Remaining 4b: 10 modules, from `py_exc_traceback` to `py_class` (93).
   (d) U2, U3 and U4's `py_func` (Claude, local Mac, `589ea2efb`, `739c6d2e4`, `36b28ff03`, tests
   `3f8e9f9d0`, `42fb95b5b`): py_exc_traceback, py_format_runtime, py_threading,
   py_protocol_runtime, py_exc_objects, py_gen, py_os_path and py_func drop
   their pins and take through `pcc_gc_take_slot`. Gone with them: the pin
   helpers (`_unicode_format_pin`, `_condition_pin`/`_unpin`, the protocol
   `_pin_result` helpers, `_unicode_pin_slot`, `_builtin_value_pin_slot`/
   `_unpin_slot`), the prior-pin arrays of the generator sync driver,
   os.path, thread invoke, divmod and the py_func call protocol (whose
   array keeps only its lease tokens, now `tokens`), the result-alias
   prior searches, the generator close reload under the graph lock, and
   the uncalled `_func_call_copy_captures`. The Thread word at 96 (the
   former handoff pin) stays unused. Each module compiles alone. Models:
   the traceback, percent-format, protocol unary/binary, tuple consumer
   and generator frame-factory models reach their collection points
   without moving anything; models take through `pcc_gc_take_slot`; the
   metadata-flag inventory keeps the three IMMORTAL sites; the dataclass
   snapshot release takes no pin. Removed as relocation-only (the user's
   rule "改写或删并列出"): the moving negative control
   `test_receiver_finalizer_relocation_exposes_previous_raw_result[True]`
   (rewritten as `test_receiver_finalizer_collection_keeps_current_result`)
   and `test_alias_pin_restoration_keeps_reverse_release_order`;
   `test_generator_close_result_reload_is_locked_before_pinning` is
   rewritten as `test_generator_close_result_handoff_takes_slot_without_lock_or_pin`.
   Host pcc0, by node id against HEAD `89ecf978a`, the 111 files naming
   those modules or models and the importers of the edited models have no
   new failing node; after integrating the generator re-entrance change
   (`5c4d10927`, py_gen merged by hand), the 24 generator test files have
   none against `94f786b16`. Not run: the heavy
   files over the 2.5 GiB cap, integration, pcc1 and Linux.
   Stability check after (c) J1 and P0 landed (Claude, local Mac,
   2026-11-26): `pcc_perf.py arm stable-e7c` at `e7c3d4947` (2.5 GiB tree
   cap, peak 1.68 GiB) built the runtime and both handler programs, and all
   59 smoke runs (28 primitives at one and four workers, and the two
   programs) gave CPython's results. `table stable-e7c`, load 3.0 on 10
   cores: 59,799 req/s with one worker and 153,313 with four, 20.5 and
   27.8 us of processor time a request. One screening run without a
   control, so not an A/B result; the 2026-11-25 arm measured 55,124 and
   139,821 under load 7.3.
   (d) U4's `py_obj_ops_dispatch` (Claude, local Mac, `7b5c95f95`, tests
   `d5d2d57f2`): truth of a root slot, bitwise dispatch, the sync and
   bound-method constructors, the bound list/method/property/descriptor
   result handoffs drop their pins and take through `pcc_gc_take_slot`;
   `_bitwise_pin_result`, `_py_sync_pin` and `_py_sync_unpin` are gone, and
   no graph-lock call is left in the module. It compiles alone (158 calls
   to `pcc_gc_take_slot`, none to `pcc_gc_take_pinned_slot`, `pcc_gc_pin`
   or `pcc_gc_unpin`). Removed as pin-only (the user's rule
   "改写或删并列出"): `test_result_alias_preserves_an_existing_external_pin`.
   The string slot model binds `pcc_gc_take_slot` (the find adapters reuse
   it). Host pcc0, by node id against HEAD `e7c3d4947`, the 35 files naming
   the module or its models and the string model's importers have no new
   failing node
   (`test_binary_repeat_owners.py::test_fresh_results_run_the_root_barrier_while_address_leased`
   and `test_detached_str_search_methods.py::test_string_descriptor_runtime_strict_ir`
   fail on HEAD as well). Not run: `test_py_multi_file_bootstrap_shim.py`
   (over the 2.5 GiB cap), integration, pcc1 and Linux.
   (d) U5, `py_class` (Claude, local Mac, `d5667304c`, tests `04c9501d5`):
   the metaclass call, the class, str-subtype and tuple-subtype
   constructors, the instance lookup family, bound-method function loads
   and the getattr fallbacks take through `pcc_gc_take_slot`, with no pin,
   prior-pin record, pin reassertion or graph-locked reload; the lookup
   family keeps its per-slot owned markers. It compiles alone (303 calls to
   `pcc_gc_take_slot`, none to `pcc_gc_take_pinned_slot`, `pcc_gc_pin`,
   `pcc_gc_unpin` or `pcc_gc_reassert_pin`; its 23 remaining graph-lock
   calls belong to leases and root copies, 4c). The getter roots model
   binds `pcc_gc_take_slot` (seven of its nodes that failed on HEAD pass
   now). Host pcc0, by node id against HEAD `48e97a442`, the 64 files
   naming `py_class` or its models and their importers have no new failing
   node. `test_class_lookup_error_cleanup.py` still asserts the old pin
   bit, but on HEAD as here it stops earlier, in its loader, because
   `pcc_gc_mode_leases_freeze` went with collectors 1-4 (`2498f19ff`); its
   model is rewritten when that loader is. Not run:
   `test_py_multi_file_bootstrap_shim.py` (over the 2.5 GiB cap),
   integration, pcc1 and Linux.
   (d) U6, the 4c design (Claude, local Mac, runtime `24ae31a11`, code
   generation `1f03d26a3`, tests `c87877bc2`): the one collector's root
   copies are `pcc_gc_root_copy(destination, source)` and
   `pcc_gc_root_copy_borrowed(destination, source)` (py_obj, declared in
   `py_runtime.h` and `runtime_abi.py`). They retain the source's value
   into an empty registered destination and return a status only (0; -1
   invalid slots or a terminal object; -4 a nonempty destination or a
   failed store, owners unchanged); no lease token exists, so nothing is
   released afterwards. Their fast paths are the lease forms' without the
   address-lease acquisition; every other case takes the token-free graph
   transaction of `pcc_gc_root_copy_local`. Code generation's last lease
   form, the shared-source operand copy of a slot call
   (`_slot_call_copy_source`), emits them; every other code-generation
   lease was already the constant token 0. The lease forms stay for the
   runtime's own callers, which U7-U10 convert. New test:
   `test_root_copy_status.py` runs both copies natively against the
   single-thread and threaded archives (status codes, reference counts, no
   lease). The root-copy IR parser and 24 code-generation IR tests name the
   new copies. Host pcc0, by node id against HEAD `1214ce627`, the 75 files
   naming a root copy, the parser's importers and the edited files have no
   new failing node, and seven nodes that failed on HEAD pass; the native
   callback/finalizer program of `test_private_root_copy.py`, whose global
   source takes the new copy, passes. Not run: integration, pcc1 and Linux.
   4b is complete: no runtime module pins on its own paths or takes
   through `pcc_gc_take_pinned_slot`; what still pins is the old take
   protocol's ABI itself (`pcc_gc_pin_slot_for_take` in py_obj and the
   root operations), which U11 deletes. (c) J1 merged and released `py_class` and
   `py_obj_ops_dispatch` (no J1 claim line on 2026-11-26T01:46Z), which
   settled the waiting-list question about the overlap.
   Correction: `pcc_gc_backend4_auto_step_pending` is the one collector's
   live automatic-collection request (young and full collections are
   scheduled through it), not a leftover; it may only be renamed, before
   the header/ABI freeze.
   Next for (d): the plan Claude runs without asking between units
   (the user, 2026-11-26: "你这么继续继续的有点需要我参与。能plan一下吗").
   Done: steps 1, 2 (pins outside the take protocol), 4a, 4b batches 1-2,
   U1-U5 (4b) and U6. U7, the first 4c batch, is next.
   Units, in order, each one claim, commit and push (runtime first, then
   its test models, then the status result):
   (U1) 4b batch 3: py_obj, py_coroutine, py_obj_ops_compare, py_tempfile
        (30 sites).
   (U2) 4b batch 4: py_exc_traceback, py_format_runtime, py_threading,
        py_protocol_runtime (49).
   (U3) 4b batch 5: py_exc_objects, py_gen, py_os_path (76).
   (U4) 4b batch 6: py_func, py_obj_ops_dispatch (80).
   (U5) 4b batch 7: py_class (93); then the runtime no longer calls
        pin, unpin, reassert, take_pinned_slot or pin_slot_for_take.
   (U6) 4c design: a token-free root copy (`pcc_gc_root_copy*` returning
        a status only) beside the lease forms, emitted by code generation.
   (U7-U10) 4c batches: the runtime's 193 foreign/borrowed lease calls
        (38 files) and 101 root-copy calls, smallest files first, the same
        way as 4b; their test models drop lease tokens.
   (U11) 4d: delete the then-uncalled ABI functions from the runtime,
        `runtime_abi.py`, the effect contracts and (after the user's
        answer) `py_runtime.h`.
   (U12) step 3: delete the fused-protocol pass and
        `tests/python/test_fused_protocol_pass.py` (after the user's answer).
   (U13) step 5: `pcc_gc_backend()` and `PCC_GC_BACKEND` (8 references).
   (U14) step 6: the closed-young analysis and the per-collector options
        of `scripts/pcc_perf.py`.
   Per unit: rewrite (codemod, then by hand); compile each edited runtime
   module alone; run the test files naming the edited modules or models,
   one stream under `scripts/run_process_tree_sample.py` (2.5 GiB tree
   cap), with HEAD's failing nodes as the baseline; models follow the one
   collector (no movement, `pcc_gc_take_slot`, no pin); no file has a new
   failing node. Not run per the user: integration suites; also pcc1,
   Linux and the heavy files that exceed the cap. Claude stops and asks
   only for: a behavior change it cannot attribute to a model, or a file
   another agent claims. The user's answers of 2026-11-26 (selected
   options): run the units back to back ("连续做"); U12 may delete
   `tests/python/test_fused_protocol_pass.py` with the pass ("可以删");
   U11 deletes the matching `py_runtime.h` exports ("一起删"); a test or
   case that only exercises a removed protocol function is rewritten to
   the one collector, or deleted and listed in its commit ("改写或删并列出").
   The user, 2026-11-26, on claims: "plan也可以自己认领啊"; one claim
   line covers the whole plan, renewed every two hours.

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
   - (d) plan U1-U14 (the units under "Next for (d)", in order): Claude, macOS, 2026-11-26T03:28Z, area runtime-codegen

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
   Measured 2026-11-26 (local Mac, w1, arms of 6e54efdb3 with and without
   the A2 patch). The handler request costs 17.56 us of processor time;
   asyncio's is 11.5 us (87k req/s). `gap`: program 12.01 us (68.4%), roots
   3.64 (20.7%), allocator 1.35 (7.7%), barrier 0.55 (3.1%). Inclusive:
   the request body 13.40 us, `json.dumps` 5.63, `pcc_gc_store_root` 3.47
   (most of it the releases and deallocations of cleared slots). `prim`
   against CPython, ns: json 6279/1719 (3.65x, 16 allocations), TaskScope
   2394/484, perf_counter 395/57, str.encode 308/60, closure 2648/137,
   raise 511/98, generator 119/33, method 97/38; spawn and sleep cost less
   than asyncio's. json is about two thirds of the w1 gap. Its 6.8 us per
   call: json's own code and string building about 1.3 us (CPython's C
   encoder takes 1.7 us in all); reference counts, root stores and
   deallocation about 1.1; allocation 0.55; the container lock of the
   buffer list only this thread uses 0.40; the provenance checks
   `pcc_gc_granule_is_object_start`, `pcc_gc_pointer_is_managed` and
   `list_is_sane` 0.39; the TLV resolver 0.19; error and recursion checks
   0.17 (self time; the pieces are estimates until counted).
   A2 result: `pcc_gc_store_root` runs 304 times per request, counted with
   an lldb breakpoint over two run lengths with startup subtracted: an
   empty slot cleared 34.2%, a counted value cleared 26.8%, an immortal
   value cleared 15.6%, a counted value stored in an empty slot 9.7%, a
   tagged value cleared 7.7%, the rest below 3% each. The backend short
   path for NULL or tagged values on both sides (42% of the calls) cut its
   self time from 1.27 to 1.03 us, and its callers' self time grew about
   0.1 us; `ab` w1 56650 -> 56469 req/s (-0.3%, 1/5 pairs, floor 4.2% at
   load 7.1). Not merged; the patch is kept outside the repository. The
   plan's 3-4% was the profile share times a guessed half; a ceiling is
   now the counted calls per case times the per-case saving. A3-A5 of the
   earlier order are folded into J1 below.
   Order, Claude's recommendation of 2026-11-26, after the user asked to
   find where the time goes before anything else; json is the yardstick,
   `prim ARM --only json --against BASE` per change and one handler `ab`
   per bundle:
   Correctness comes first (the user's requirement of 2026-11-26:
   correctness, concurrency correctness, virtual-thread correctness). A
   check leaves a hot path only when it decides nothing, that is it only
   audits or debugs; a check that tells a foreign or CPython-handle object
   from an owned one stays. A lock fast path keeps mutual exclusion and
   ordering for every thread and carrier: a virtual thread can resume on
   another carrier, so no thread or carrier identity makes a container
   private. Before its measurement each piece runs, besides its own tests,
   the container-lock races (`test_list_container_lock_races.py`,
   `test_dict_container_lock_races.py`, `test_concurrent_list_append.py`,
   `test_threaded_gc0_shared_containers.py`) and the virtual-thread carrier
   and park tests (`test_virtual_thread_pcc_py_carriers.py`,
   `test_virtual_thread_park_effect.py`, `test_virtual_thread_lock_mechanisms.py`).
   J1. Off json's hot path: the provenance checks (from `list_is_sane`,
       `ptr_is_list`, instance getattr and set_field, the freeing note),
       a fast path for an uncontended list lock, the allocator's magazine
       calls, and the diagnostics-log call `py_obj_getattr` makes while the
       log is off; about 1.2 us of json's 6.8 (self-time guess). Parts in
       `py_list.py` wait for the (d) 4b claim.
   J1 result (Claude, 2026-11-26, local Mac, arms of 9de68e502 with and
   without the change): the container lock takes a free stripe with one
   CAS and spins reading only when it is held; `py_obj_getattr` tests the
   log word before calling the log; the magazine limit answers 256 up to
   128 bytes without a division. Instructions per handler request, from
   `/usr/bin/time -l` over 1000 minus 200 requests, three runs each: base
   272672, control 272530 (-0.05%), J1 268808 (-1.4%; runs within 0.2%);
   cycles 62301, 61176 and 57919. `prim --against`: list_append -5.3% at
   w1 and w4, attr -4.1% at w1, both beyond their control; json -2.1%
   within noise. The handler rate `ab` was invalid (host load 8.6, control
   floor 14.6%), so the evidence is the instruction count and the
   primitives. Merged on the user's instruction to do J1. Measurement
   from now on: check the load before a run; report instructions and
   cycles per request (load moves cycles, hardly instructions) with a
   control arm; a rate `ab` only on a quiet host. Correctness: the
   container-lock races, shared containers and virtual-thread park and
   carrier tests (47), allocator and magazine tests (31), the stripe-lock
   model (17, which failed on master on the removed
   `pcc_container_lock_held` and now pins the CAS-first acquire) and a new
   getattr log test pass. Unrun: nodes that need a current pcc1, and three
   that fail on master because they call the removed `pcc_gc_set_backend`
   or `pcc_container_lock_held`:
   `test_list_container_lock_races.py::test_list_lock_is_the_container_lock_without_the_probe`,
   `test_virtual_thread_pcc_py_carriers.py::test_threaded_pcc_python_archive_runs_persistent_carriers_and_pin_metrics`,
   `test_gc_object_cell_fast_paths.py::test_object_cell_fast_paths_publish_and_retire_exact_cells`.
   Measurement tool (Claude, 2026-11-26): `pcc_perf.py instr BASE ARM...`
   reports instructions and cycles per handler request from two run
   lengths; on J1's arms it gives base 272705, control +0.05%, J1 -1.27%
   instructions (cycles -2.23% and -4.12%) at load 5.3. Arm builds already
   reused program objects through the object cache; the frontend IR cache,
   though, fell back to the fixed identity `pcc-perf` and keyed the
   compiler by the Python interpreter, so an arm that changed code
   generation would have measured the IR of an arm built before it. Its
   identity is now the digest of the sealed `pcc/` files outside
   `pcc/runtime/py`. Earlier arms changed the runtime, the backend (keyed
   by its sources) or IR passes (after the cache boundary), so their
   results stand.
   J2 checks (Claude, 2026-11-26; details in
   [the design note](design/python-borrowed-local-operands.md)): slot calls
   consume no input; the exact-list guard memoizes on the borrowed registry,
   so borrowed locals need their own; pcc has no frame write-back (locals,
   vars, exec, settrace, _getframe all fail closed); a may-park dynamic call
   in a spawned function fails to compile. Found: pcc does not raise
   "generator already executing" (self re-entry recurses into
   RecursionError; four threads drive one generator without a ValueError),
   a correctness bug independent of J2 that blocks J2's step 2.
   J2 next: step 1 (ordinary functions) with its own borrowed-local registry, after the generator re-entrance fix.
   Generator re-entrance fixed (Claude, 2026-11-26): word 40 of a
   generator holds bit 0 (done) and bit 1 (running); `py_gen_next`,
   `py_gen_send`, `py_gen_throw` and `py_gen_close` claim the running bit
   with one CAS and clear it when the frame returns, so a self re-entry or a
   second thread raises ValueError "generator already executing" (CPython's
   message) instead of entering the frame twice; done is set with an atomic
   or, `py_gen_is_done` reads bit 0, `py_gen_take_completed` claims with a
   CAS. Virtual threads resume through `py_gen_next_direct`, so their frames
   are covered too. `tests/python/test_generator_already_executing.py`
   compares with CPython's output (self next/send/close/throw, three rounds
   of another thread's next/send/close); it failed before the fix
   (RecursionError) and passes after. 339 generator, coroutine and
   virtual-thread tests passed on the working tree. Not run: pcc1 nodes,
   gc3/gc4/relocation nodes, `test_gc_cleared_gen_sweep_native.py` and
   `test_generator_finalizer_context_restore.py` (they need an admitted
   `PCC_RUNTIME_ARCHIVE`). Open, to fix next:
   (1) failing on the working tree and not yet checked on HEAD:
   `test_generator_frame_take_group.py` (two native tests),
   `test_generator_frame_take_protocol.py::test_native_take_null_clear_matches_split_mark_and_remembered_state`,
   `test_generator_method_owned_result.py::test_generator_method_owned_result_ir`,
   `test_generator_result_lexical_root.py::test_result_handoff_uses_lexical_root_without_changing_child_or_sink`,
   `test_generator_root_groups_native.py::test_generator_root_groups_repository_native`,
   `test_generator_terminal_clear_activation.py::test_terminal_native_reference_and_actual_optimized_linker_input`,
   `test_virtual_thread_lowering_ownership.py` (callback roots and the
   callback exception owner contract);
   (2) failing on HEAD as well (removed APIs, for (d)'s rewrite): the
   C fixtures of `test_gc_observer_generator_native.py` and
   `test_generator_frame_clear_pending_stop.py` (tracing and relocation
   symbols), `test_generator_finalizer_accounting.py`
   (`py_gen_finalize_tracing_candidate`), `test_generator_frame_root_initialization.py`
   (all 20 cases),
   `data_model/test_generator_state_runtime.py::test_generator_resume_boundaries_attribute_silent_null_results`
   (a removed coroutine message), and the files that call
   `pcc_gc_set_backend` or `pcc_gc_telemetry_reset`: test_vthread_timer_heap_scheduler,
   test_vthread_io_ready_order, test_virtual_thread_ready_entry_pool,
   test_tuple_generator_conversion, test_vthread_direct_generator,
   test_generator_inline_frame, test_gc_coroutine_roots,
   test_generator_frame_initialization, test_generator_frame_owner_transfer,
   test_virtual_thread_pcc_py_carriers, test_generator_bulk_frame_save,
   test_gc_coroutine_scheduler_roots_production, test_generator_moving_frame_take;
   Open item (1), Claude, 2026-11-26: all nine functions fail on HEAD too.
   Fixed: `test_generator_terminal_clear_activation.py` (10 pass); it
   required a `pcc_gc_load_ptr` call, which the root-read lowering of
   887c56b7d turned into icmp/select/load. `RootSlotContract.root_read`
   now names the slot of either form. `test_virtual_thread_lowering_ownership.py`
   now also accepts both read forms and the `py_gen_frame_move` saves of
   dc7f4b578, and stops at its lease assertions. Still failing, and why:
   `test_virtual_thread_lowering_ownership.py`,
   `test_generator_method_owned_result.py::test_generator_method_owned_result_ir`
   and `test_generator_result_lexical_root.py` pin the
   `pcc_gc_foreign_lease_*`/`pcc_gc_root_copy_lease` calls the one collector no
   longer emits ((d) U7-U10 rewrites lease tests);
   `test_generator_frame_take_group.py::test_no_log_generator_group_finalization_and_cleanup_matches_reference`
   fails in `tests/python/promoted_restore_contract.py`, which accepts a
   promoted owner flag only in a select or a branch, while dc7f4b578 passes
   it as `py_gen_frame_move`'s `owned` argument (the contract must prove
   the caller clears the flag after the move); the native tests of
   `test_generator_frame_take_group.py` and `test_generator_frame_take_protocol.py`
   link removed tracing functions (`pcc_gc_gray_count_load_acquire`,
   `pcc_gc_set_mark_color`, store-buffer functions);
   `test_generator_root_groups_native.py` needs `PCC_THREADED_RUNTIME_ARCHIVE`.
   The 19 files that use `RootSlotContract` fail on 82 nodes on the tree and
   87 on HEAD, none new.
   (3) coroutines check "already executing" and then store the state with
   a plain store, so two threads may both pass; a probe decides.
   Where the gap is (Claude, 2026-11-26, local Mac, `/usr/bin/time -l` over
   1000 minus 200 requests): asyncio on CPython 3.15 executes about 155k
   instructions and 30k cycles per handler request, pcc about 269k
   instructions and 58k cycles; instructions per cycle are alike, so the gap
   is the instruction count. In the j1 arm's profile, sampled time mapped to
   instructions: callee-saved register saves and restores 10.2%, returns
   12.5%, pointer authentication 3.2% (frame entry and exit at least 26%),
   and about half of all samples fall in a function's first 12 instructions
   or its last 8 before a return. The hot runtime helpers spend most of their
   time there: `pcc_gc_store_root` (7.9% of the time, 51% at entry or exit,
   saves 10 registers), `pcc_gc_release_known` 74%, `py_gen_frame_take` 75%,
   `pcc_gc_granule_is_object_start` 62%, `py_decref` 58%, `free` 55%; json's
   large functions under 3%. Next for (c): shrink-wrapping in the self
   backend (save callee-saved registers only on the paths that use them),
   checked first on one helper's fast path with `instr`; a design note
   before the change, which needs the native and bootstrap gates.
   Self backend against LLVM on the same runtime IR (Claude, 2026-11-26,
   local Mac, a labeled reference experiment, not a product route): the
   current runtime's 183 archive modules (`build_owned/.../*.ll`) compiled
   with Homebrew clang 20.1.8 `-O2`, plus a 12-instruction stub for the
   `llvm.pcc.capture.gc.registers` intrinsic, linked by the owned linker into
   the same self-compiled handler program (the in-process provenance check
   skipped for the experiment only; the linker drops `__eh_frame`; the
   handler checks every response body). Per request, three runs each,
   1000 minus 200 requests: self runtime 268261 instructions, 57421 cycles,
   59105 req/s; clang runtime 174947 instructions (-35%), 37554 cycles
   (-35%), 85631 req/s (+45%); asyncio about 155k instructions and 30k
   cycles. The runtime's code generation alone accounts for about 93k of the
   115k extra instructions. Next for (c): rank the self AArch64 backend's
   gaps by comparing each hot runtime function's machine code with clang's
   for the same IR (entry/exit saves, call-result spills, address
   materialization, register copies; see the self-aarch64-* investigations)
   and measure each fix with `pcc_perf.py instr` against this bound.
   Where the self code is longer (same experiment, static machine code of
   the 20 hottest runtime functions): self bodies are 1.5-2.5x clang's
   (`py_incref` 55/27, `pcc_gc_retain_known` 139/68,
   `pcc_gc_try_init_ptr_locked` 130/51, `pcc_gc_store_root` 510/279), while
   both save about as many callee-saved registers, so shrink-wrapping those
   is not the lever. In `py_incref`'s counted fast path (about 38 self
   instructions against 22): (1) a frame record and pointer authentication
   on every entry and exit where clang builds no frame (fast path calls
   nothing, the slow path is a tail call); (2) `sxtw` after every 32-bit
   load; (3) unfused tests: `and`+`cmp`+`b.ne` for bit 0 (clang `tbnz`),
   `cmp #0`+`b.lt` for a sign (`tbnz #63`), `ldr`+`sxtw`+`and`+`cbz` for a
   flag (`ldrb`+`tbnz`); (4) a tag range test as several compare-branches
   (clang `sub`+one unsigned compare); (5) `nop`s on executed paths and
   branches to blocks that only branch; (6) `adrp`+`add`+`ldr` where the
   offset folds into the load. Proposed order: frameless leaves and tail
   calls, then compare/branch fusion and extension removal, then nops,
   branch chains and address folding, each measured with `instr`.
   P0 (the user made this P0 on 2026-11-26): the self AArch64 backend's
   code generation. The runtime IR given to clang is already after pcc's own
   passes (lowered root reads, phis). Three runtime builds of that IR, same
   self-compiled handler, three runs each: self 269956 instructions per
   request, 58944 cycles, 58394 req/s; LLVM code generation only (clang -O2
   with `-Xclang -disable-llvm-optzns`) 186681, 42513, 78370 req/s; LLVM IR
   optimization and code generation 174341, 40267, 89047 req/s (asyncio
   about 87k). Of the 95.6k instructions, about 83k (87%) come from code
   generation and 12k (13%) from IR optimization: fix the backend's
   instruction selection, frame lowering and machine-level peephole, not
   the IR. The handler program itself is still self-compiled in all three.
   P0 ranking (estimate: each hot function's static machine-code mix times
   its profile share; a time-weighted per-instruction count under-counts
   cheap instructions, and the default xctrace CPU Counters template records
   no call stacks): register-to-register `mov` 10.4% of executed
   instructions, frame record and pointer authentication 7.9%, immediates
   materialized with `mov` before use 6.5%, callee-saved saves and restores
   5.3% (clang saves about as many), `nop` 2.0%, `adrp`+`add` foldable into
   the load 2.0%, `sxtw` after a 32-bit load 1.7%, frame-slot spills 1.1%,
   unfused sign and bit tests 0.9%, branches to branches 0.1%; 38% in all,
   against the measured 31% of code generation alone. Order: register
   copies (coalescing) and immediate operands, then frameless functions,
   then the peepholes; each change measured with `instr` and gated by the
   native and bootstrap checks.
   P0 technique log (each entry: mechanism, platforms, measured effect).
   (a) Frameless atomic leaves (Claude, 2026-11-26): `_is_frameless_leaf`
   (shared by the AArch64 Darwin and Linux backends) also admits
   atomicrmw, store atomic, fence and a cmpxchg read through its one
   following extractvalue, all of which lower to registers only, so such a
   leaf drops its frame record and pointer authentication. Measured with
   `instr` (load 7.3): 268344 -> 267730 instructions per request (-0.23%,
   inside the 0.86% run spread), cycles +1.75%: few hot helpers are leaves,
   because most call another function or reach a thread-local through
   `_tlv_get_addr`. Not merged; the patch and its test are kept outside the
   repository for A1, after which thread-local users such as the container
   lock become leaves. The x86-64 backend (Linux, Windows) keeps its own
   frame rules and needs the same check. The frame category's weight sits
   in non-leaf functions: clang builds the frame only on the paths that
   call (shrink-wrapping the frame record) and signs no non-leaf return;
   that is the next frame item.
   (b) Return-address signing, measured on LLVM as a lab (Claude,
   2026-11-26): the same runtime IR built by clang -O2 with every function
   marked "sign-return-address"="non-leaf" (module flags alone, and
   `-mbranch-protection` on IR input, emit no `paciasp`) runs 178242
   instructions per request against 174771 unsigned (+3471, +2.0%; cycles
   +1.3%). On the self backend that is about 1.3% of a request. The self
   backend signs every non-leaf function (S-track SEC-P1-CFI, branch
   protection module); dropping it for plain arm64 executables, as clang
   does by default there, is the maintainer's decision. Together with (a),
   the frame category is worth about 1.5%, so the static-mix estimate (7.9%)
   overstated it: it counts every duplicated epilogue. The 83k-instruction
   gap sits in function bodies: register copies, immediates moved before
   use, `sxtw`, `nop`, unfused compares and unfolded addresses.
   (c) Sign-extending loads (Claude, 2026-11-26): pcc-Python's `load_i32`
   returns a 64-bit integer, so runtime IR is full of `load i32` + `sext`
   pairs, emitted as `ldr wN` + `sxtw xM, wN`. The typed peephole
   `fold_sign_extending_load` (outside the frozen `_run`, phase
   `typed_fold_sign_extending_load`) and its regex-free text counterpart
   `_fold_sign_extending_load` turn the pair into one `ldrsw xM` (`ldursw`
   for an unscaled offset) when the base is an x register with a plain
   immediate and either M == N or wN is a dead scratch; the encoder gains
   `ldrsw`/`ldursw` (words checked against clang 20). Platforms: AArch64
   Darwin and Linux (the Linux emitter calls the Darwin one). x86-64 has
   the same pair (`mov r10d, [m]` + `movsxd r10, r10d`, foldable into
   `movsxd r10, dword [m]`) in its own emitter; not changed yet. Measured
   with `instr` (load 3.9 on 12 cores, three runs each, arms p1-base at
   e7374ffc0 and p1-sxtw): 267981 -> 262566 instructions per request
   (-2.02%, run spread at most 0.51%), cycles -8.68%. Test:
   `tests/python/test_aarch64_sign_extending_load.py`. Rebuilt as a
   standalone pass (arm p1-sxtw2) and measured again with (d): 268555 ->
   262712 (-2.18%).
   (d) Loop-separator `nop` and branches over empty labels (Claude,
   2026-11-26). The stack-map planner emits `nop` before every loop
   safepoint label so the label never shares a PC with an earlier record
   (a call's return address); it ran on every backedge, 1.5% of the self
   runtime's executed instructions. `_drop_loop_separator_nops` (text) and
   `drop_loop_separator_nops` (typed, phase `typed_drop_loop_separator_nops`,
   the last pass) drop it when the previous instruction is not a call and
   not a label or directive; the stack-map encoder still rejects two records
   at one PC. The fallthrough-branch drop (text, typed records and the
   native fragment rewrite) now also crosses plain `L_` labels between
   `b L_x` and `L_x:`; stack-map labels, function labels, directives and
   instructions still block. Platforms: AArch64 Darwin and Linux; the
   x86-64 planner emits its one-byte separator under the same rule and
   needs the same check. Measured with `instr` (load 3.2, three runs each):
   262712 (p1-sxtw2) -> 259131 (p1-sep), -1.36%; with (c), 268555 ->
   259131, -3.51% instructions per request against e7374ffc0. Cycles moved
   +1.5%, inside the loaded host's noise. Test:
   `tests/python/test_aarch64_label_run_fallthrough_and_loop_separator.py`
   (text, typed and native agree; native bytes equal the assembled text;
   a loop program runs).
   (e) Phi coalescing in the register allocator (Claude, 2026-11-26).
   Of the 19392 register copies in the step trace, block-local copy
   propagation could remove at most 1073 (5.5%); the rest come from
   allocation: phi edge copies, call operands forced into x19-x28 so the
   argument setup copies them, call results moved out of x0. The callee-
   saved allocator now merges a phi and an incoming value into one
   register group when no member interval of one overlaps one of the other
   (`_phi_coalescing_pairs`, `_merge_alias_groups`); the edge copy then has
   equal source and destination and is skipped. Platforms: AArch64 Darwin
   and Linux (shared allocator); the x86-64 allocator is separate. Measured
   with `instr` (load 4.6): 259091 (p1-sep) -> 258168 (p1-phi), -0.36%,
   run spread at most 0.17%. Test: `tests/python/test_aarch64_phi_coalescing.py`
   (merge rules, a swap cycle and a value used after its loop stay apart,
   native execution in both modes). With (c) to (e) self runs 258168
   instructions per request against 186681 for clang's code generation of
   the same IR (1.38x) and 155k for asyncio (1.67x). Three peephole-sized
   changes measured 2.2%, 1.4% and 0.4%: the remaining gap is the
   allocation model (every value keeps a stack slot, call operands live in
   callee-saved registers, no parallel-move argument setup, no live-range
   splitting, no shrink-wrapped callee-saved saves), which needs a design
   decision before more work; see the waiting list.
   (f) Return-address signing off by default (maintainer decision,
   2026-11-26, answering waiting-list P0 (b): plain arm64 matches clang; the
   goal is at least LLVM parity, then asyncio). `branch_protection_enabled`
   now defaults to off, so self-compiled AArch64 functions carry no
   `paciasp`/`autiasp` and frameless leaves no `bti c`;
   `PCC_SELF_BRANCH_PROTECTION=1` restores both. Signing was 14.0% of the
   self backend's excess over clang in the exact ranking and about 2% of a
   request on the LLVM lab; the self-backend effect is measured with the
   next arm. Tests of the signed form use the `branch_protection_on`
   fixture (tests/conftest.py); `test_self_backend_default_matches_clang_plain_arm64`
   pins the default.
   (g) Call-site register rules in the allocator (Claude, 2026-11-26;
   the maintainer's answer to waiting-list P0 (c): at least LLVM parity,
   then asyncio). `call_operands_may_use_caller_registers` admits a direct,
   non-variadic call whose arguments are all scalar register arguments and
   whose result is not returned in memory; a value whose last use is such
   a call's operand no longer needs x19-x28, and `emit_scalar_register_arguments`
   writes the argument registers as one parallel move (register sources
   first, a cycle parked in x16, then slot and constant sources). Call
   results and returned values hold x0 when no call crosses them and no
   other interval holds it; a cast whose operand dies there shares its
   operand's register (the disjoint-interval merge of (e)). The allocator
   and the call emitter share the one predicate. Measured with `instr` (load
   5.7, three runs each), instructions per request: p1-phi 258074, signing
   off (f) 246285 (-4.6%), call-site rule 241548 (-6.4%), x0 and cast hints
   237065 (-8.1%; -11.7% against this morning's 268555; clang's code
   generation of the same IR is 186681, now 1.27x). Cycles per request did
   not follow (53631 -> 53267, inside noise): the removed instructions are
   cheap; the cycle gap is elsewhere and is the next thing to locate. Test:
   `tests/python/test_aarch64_call_operand_registers.py` (swap, rotation,
   duplicate, constant and call-crossing operands, native execution).
   P0 exact ranking (Claude, 2026-11-26; supersedes the static-mix
   estimate): lldb single-stepped 200000 instructions of the handler's
   main thread (one worker) for the self runtime and for the clang -O2
   runtime of the same IR, every executed instruction classified, the
   clang run scaled to equal work by the instructions both spend in the
   shared self-compiled program code (ratio 1.388). At equal work self runs
   200000 and clang 144062; self's excess by class: register copies +10709
   (19.1% of the gap), other ALU +8078 (14.4%), pointer authentication and
   bti +7826 (14.0%; self signs every framed function), `sxtw` +5423 (9.7%),
   callee-saved saves and restores +5389 (9.6%), frame records +4771 (8.5%),
   compares +3352 (6.0%), unconditional branches +3023 (5.4%), `nop` +2726
   (4.9%), stores, immediates, conditional branches, global addresses and
   stack slots 3-4% each; clang executes more loads and libSystem code.
   Order: `sxtw` folding and the loop-separator `nop` (done, (c) and (d):
   -3.51% instructions), then register copies, then frames; signing waits
   on the maintainer.
   J2. Fewer root stores and release chains in code generation: 104 of the
       304 calls per request clear a slot that is already empty.
   J3. TaskScope (5x CPython), `time.perf_counter` (7x), `str.encode` (5x).
   A1. Darwin thread-local access inline (TSD slot through `tpidrro_el0`,
       startup check, fallback to the call): the resolver is 0.65 us per
       request, the inline read is not free; waits on the maintainer.
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

   Failing on master on 2026-11-26, not caused by item 3 (checked on HEAD
   and on `shenming/master` snapshots):
   `tests/python/test_local_frame_arena.py`, 8 nodes (models of the removed
   frame protocol). `tests/python/test_typed_list_iteration_ownership.py` (4
   nodes) counted the collector's young log as a leak: one entry per newly
   tracked container in 8 KiB chunks, reset only by a young collection,
   capped at 1 MiB. A loop that frees everything by reference count triggers
   no collection, so 8000 iterations of three containers grew the log by 23
   chunks (188416 bytes) while every object was freed; the test now
   subtracts the log. Open, unmeasured: in a steady server the log saturates
   after about 130k tracked allocations, and a saturated log sends the next
   young collection to the exact-flag path. Stale expectations in the
   backend test set (record lanes, worker phases, codec preflight, link
   lifetime, typed C ABI, structured inventory, suffix scratch) were updated
   on 2026-11-26.

   Claims:
   - P0 unit 1: self AArch64 register copies and immediate operands: Claude, macOS, 2026-11-25T21:10Z, area backend (AArch64 selection, register allocation and their tests; native and bootstrap gates authorized by the user on 2026-11-26)
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
   Hosted run 45663526933 (artifact 15046868002) confirmed Clang 18.1.3's
   normative capability, but six simultaneous compiler driver/cc1 pairs
   crossed 1 GiB (1,281,925,120 B). The same six pytest workers now admit
   one external platform compiler at a time, retaining all 86 node identities,
   original executions, timeout and cap. Linux G++ 14.2: all 86 admission
   intervals disjoint, guard completed in 6.64 s at 440,233,984 B; 85/86
   native references, sole unchanged CWG2394 rejection. Whole ordinary C++
   plus CI contracts: 6290 tests and 49 subtests passed. Next: hosted Clang
   86-node gate; independent Source / cpp / reference remains at its 9/1207
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
   Java: generic blank instance final fields now use the bound DA/DU graph
   through initializer order, constructor delegation and every normal exit.
   Linux host pcc0: 4436 whole ordinary tests passed; 52 exact new originals
   matched OpenJDK 21 (23 executed, 3 valid-only, 26 rejected); old array-field
   and constructor-reference controls passed. Three stale static-init tests
   retain their original source in real class-guard ELF/IR and Java-error
   checks. The unchanged class-model frontiers advance from BigInteger:134:11
   to 159:5 (annotation) and GenericGF:42:17 to GenericGFPoly:200:16 (reference
   arrays). Strict production still requires #13 nominal static roots and
   construction (`SmallPrimes.java:90:18`, `GenericGF.java:33:23`). Run
   45643044565 reference (207 nodes, 4 failing aggregates/6 leaf differences)
   and integration (1259 nodes, 4 failures including 2 JDK-only) receipts
   reproduced all eight JDK-only source differences locally on 21.0.13. The
   applicable JLS 21/25 obligations remain strict; no qualified replacement
   JDK is known. Next: #13 backing/roots and native constructor lowering, not
   another v1 storage shortcut. The v1 envelope and runtime ABI are unchanged.
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
   all 12 existing x86 reds unchanged in that comparison. ARM Stage1
   3993.340 s vs x86 1466.275 s (same 621 modules, four workers): typed
   backend finalize accounts for 96.8% of the extra codegen worker time.
   First owner fix indexes conditional-fallthrough proofs once: actual
   202,676-event frozen compiler module, five paired host cross-target
   timings 1.430→1.213 s (-15.16%, control-normalized -14.90%, 5/5),
   identical AArch64 PCO; exact changed body executed as x86 ELF.
   All 168 existing red timing-fixture nodes repaired: real scope/capture
   cleanup, exact empty-object rejection and unchanged valid PCO checks.
   Also repaired the independent native receipt's missing source provider;
   1216 adjacent host tests plus one source-matched pcc0 executable receipt
   pass, five platform/integration nodes not run. Native x86 frame
   promotion now solves the same liveness equations with a predecessor
   worklist: actual 66-function graph evaluations 527,724→99,440, native
   owner 4.902→2.665 s (-45.63%, normalized -46.96%, 5/5), exact frame
   text and packed objects. Helper median peak +1.58 MB under unchanged
   1 GiB guard; 126 focused and 1,421 ordinary x86 tests pass, one known
   pointer-retirement register-home witness still red. Next: remaining
   pairing/trampoline and frontend/backend owners, actual equal-ceiling
   Stage2/3 CI. No full Stage2/3 speedup or ARM execution claim.
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
