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
That run is a red baseline. Prior push-triggered exact-source validation
[run 45663526933](https://github.com/ikshengmin/pcc/actions/runs/45663526933)
completed failure (31 red, 18 green, 3 canceled, 2 skipped). Its successor
[run 45844665220](https://github.com/ikshengmin/pcc/actions/runs/45844665220),
head `b346a48a`, produced Linux x64, ARM64 and Windows Stage1; all three
Stage2 owners completed failure (1800s timeout), Mac still active. Latest
maintenance fixes and performance units through `73fd3fe0` were queued in
[run 45856871699](https://github.com/ikshengmin/pcc/actions/runs/45856871699),
head `6cbafc4a`, pending behind the prior useful run. Since that exact
checkpoint, substantial Stage2 lazy summary (`9f101283`) and sparse
class preload (`7d172a8a`), shared generator save (`7e3d8d1e`), and C++
stdlib surface (`e3b0440f`) have landed with focused evidence. This
non-skip receipt queued the new integrated source in
[run 45862787448](https://github.com/ikshengmin/pcc/actions/runs/45862787448),
head `c20c2c01`, alongside the older comparison (maintainer allows
accumulating CI; no current useful run canceled). At 17:31 UTC on
2026-11-26, run 45856871699 is in progress (23 green, 10 red, 4 active).
All three native Stage1 bundles have now been downloaded and exact
receipt/log hashes checked against their manifests (artifact IDs
15140867848 Linux x64, 15140084999 Linux ARM64, 15140984244 Windows x64).
Same four workers, actual shared stage receipt compile seconds and peak
tree bytes, prior run 45844665220→45856871699:
Linux x64 1571.926864→1206.445611 (-23.25%), RSS2936516608→2877579264
(-2.01%); Linux ARM64 1736.298260→1644.478621 (-5.29%),
RSS4598730752→4676341760 (+1.69%); Windows x64 1181.080718→1738.978787
(+47.24%), RSS3627479040→3730415616 (+2.84%). Windows wall regression
requires diagnosis: 604/606 matched codegen modules slower, median
1.757x (p10 1.618/p90 1.880); infer median1.553x, all major phases
including export/link slower. That run's Windows observer has no own CPU or
affinity/quota measurement, so no causal source vs machine conclusion.
Future Windows Stage1 observer now reads optional GetProcessTimes own
user/kernel FILETIME 100ns durations and creation identity from the same
already-open RSS process handle, with IsProcessInJob on that exact handle
before reading to reject a reused foreign PID. Failed membership/time
queries are explicit nulls, never zero/estimated CPU. No extra inventory,
no host quota/affinity claims, no summed complete-tree CPU, no guard/
deadline/RSS/native-path change; disabled observation still does not query.
Test-first seven failures; Linux proper-gated affected observer/export/
Windows guard/Linux admission modules 393 passed, two actual Windows
nodes UNRUN (including new real Job CPU smoke). Wider direct host ordinary
CI contract module inventory was not a hosted equivalent: 3,212 passed,
34 failures and six deselections, six subtests passed. Two Darwin-owned
nodes, nine CPython3.13 platform uname calls inside subprocess mocks,
21 child launches lacking pytest after PYTHONPATH reset, one pinned
Python3.15 input inventory, one pytest9.2 subtest representation. Explicitly
not green; exact Windows hosted CPU receipts still needed.
Linux x64 runner image also changed; do not attribute all speedup to
source. Actual codegen worker wall1096.943s (91% of Linux x64 compile),
export62.352, link34.983. 625 module records sum infer149.1s,
codegen3752.082s; indexed receipts backend2728.8 worker-seconds,
function emission1546.7, stackmap plan640.36, prepare verification351.2,
layout128.95. Prioritize actual codegen worker common owners rather than
minor link changes; stage1 target 5m six-worker not yet met.
All three Stage2 owners in run 45856871699 FAILED their existing
1800s deadlines (not complete Stage2 or Stage3). Linux x64 owner
artifact15143077466 elapsed1800.1168s/outer tree peak3715461120B;
full bootstrap artifact15143435682 sampled native compiler tree through
1783.600s peak3202990080B (+11.31% against same-scope Stage1).
ARM64 owner artifact15143715322 1800.2300s/outer peak4783247360B;
full artifact15142914311 sampled compiler tree through1781.758s
peak4278870016B (below Stage1 4676341760B, but incomplete).
Windows owner artifact15143139129 existing watchdog timed out
1800.074s. Full bootstrap artifact15144830770 now retrieved: exact
source6cbafc4a, Stage1 PE shared1738.978787s/3730415616B (imported
Stage1 progress carries zero compile cost, canary compile21.0919s
passed); no Stage2 terminal receipt and no Stage3. Stage2 8000 samples
through1769.9650857s partial peak2660990976B, last1403752448B/two
processes. Complete export247/247 batches, summary79/79, preload43
retire including calibration; only two codegen starts, one retired
(c_parsetab), c_codegen still underway (indexed capture476958 records,
95041 blocks, 358849 instructions, no fallback). Partial memory is
below Stage1 but incomplete; no Stage2<=Stage1 acceptance.
Latest x64 Stage2 actual export659.022s (prior789.855), summary202.441,
preload220.162; ARM export356.308s, summary99.855, preload117.749.
Both Linux lanes only completed three frontend codegen modules before
timeout: x64 c_parsetab23.672s/c_codegen323.536/cli_bootstrap258.986;
ARM 23.556/581.023/406.817. Actual terminal singleton class_gen index209
on both. x64 compiler tree samples show one child at a time during the
oversized codegen sequence; codegen worker_3 still active at timeout.
Class_gen had reached direct indexed capture (332713 instructions,
86393 blocks, 727 functions, zero fallback) but not final emission.
Production oversized handoff intentionally serializes oversized workers
and forces indexed assembly rather than direct native sections to
respect historical heap risk. Do not merely widen worker limits or
concurrency: reduce actual single-worker CPU/RSS, measure safe admission
within the existing resource envelope and Stage2<=Stage1 memory target.
Authentic Linux x64 Stage1 ELF from same source receipt c88e3ba1...,
272120580 bytes, manifest verified, canary executed successfully; use
focused old-source singleton native worker replay for exact attribution
without a full local bootstrap. Neither latest compact class preload
nor future AST v2 is included in this run. Run 45862787448 was pending
at last verified check. Since c20c2c01 a substantial additional independent batch
landed: actual native class-preload private compact wire `16f2386b`
(80.08% worker CPU and 81.20% RSS reduction across 5/5 pairs), real
compiler typed CFG `81af95e8`, exact four-platform worker-prior test
fix `bd763672`, and unchanged NumPy C++ frontier header fixes
`b53d7e49`, `54ba01e6`, `9ea658c7`, `fdbe7161`. This non-skip receipt
queued [run 45868668374](https://github.com/ikshengmin/pcc/actions/runs/45868668374),
exact head `9223aca1`, observed pending; existing runs remain intact.
A further distinct integrated checkpoint includes production private AST v2
`14c70ccef` (32 real sidecars 66.43 -> 16.406 MB, 15/15 native paired
CPU/RSS wins, all downstream summaries exact), x64 planner `f345bf154`
and register-copy `f0b17ed3` (actual large owner CPU -8.29% raw,
-10.913% normalized, 5/5 exact outputs; full small worker neutral),
internal #13 described-managed lifetime and mapping slices `1191c19d`
and `534b4e9e` (frontends remain frozen; ordinary QPS inconclusive),
and truthful Windows own CPU-time observation `d62115a0`. No guard or
deadline loosened; actual full Stage2 and six-worker Stage1 targets remain
unmet. This status-only non-skip commit queues the new immutable snapshot
without cancelling or rerunning earlier runs. Verified exact head
`4efcdda1473df9f234640751ed3d65bc42d1d4fd` queued
[run 45876460150](https://github.com/ikshengmin/pcc/actions/runs/45876460150),
observed pending. Do not claim any hosted lane
green from focused tests. The latest completed
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

- Item 3 (empty-root clear, Codex 2026-11-26): two actual native A/B
  rounds removed eight confirmed redundant operations or proved both
  sequence-unpack clear sites redundant, but no reproducible CPU/QPS
  gain beyond duplicate control. Do not merge either as a speed unit.
  Options: keep them rejected, or admit exact count reduction as a
  correctness-checked mechanical simplification in a measured larger
  bundle. Recommendation: defer this slice and attack RC/TLS first.

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
   #20 worker-prior latest hosted run 45844665220 (b346a48a), x86-64
   artifact 15130297778: default-xdist stopped on stale static inventory
   (tuple-root model 14 expected, 11 actual). All six collection witnesses
   agree on 3773 ordinary nodes: worker priors 106 -> 115, RSS readers
   24 -> 26, and tuple roots 14 -> 11. The independent Linux host collection
   of all 87 files at 605ddc2f confirms every per-file count; the three
   changed files' 152 node identities match exactly. The receipt now
   rejects the old counts as well as omitted nodes; all original gates,
   six-worker requirement and strict report evidence remain unchanged.
   The actual failing assertion reproduced before the fix; 22 focused
   contract tests pass after it on host Python 3.13. Hosted execution is
   pending; collection is not a pass of the whole gate.
   #20 Source CI-contracts ordinary Linux (coverage 15131867000) had
   only the same stale inventory assertion; the preceding fix covers it.
   Source shared ordinary macOS (coverage 15131222277) stopped before
   collection: the platform owner erroneously required a Linux Stage1
   bundle. The exact tracked 54 Darwin nodes remain mandatory. Scope
   Linux native fixture admission and aggregate receipt checks to the
   default Linux campaign; the Darwin host retains its exact ownership
   and cannot admit or claim a foreign Linux bundle. The Linux shared
   campaigns still require and authenticate their Stage1 inputs. The
   new regression reproduced the old behavior before the fix; 216 Linux
   host-Python CI native-input/owner/coverage/workflow tests passed after
   it. No workflow lane or guard relaxed; hosted Darwin execution pending.
   #20 remaining Source/frontier failures in 45844665220 are substantive,
   not another harness regression. The C++ Linux reference artifact
   15131428562 had 1417 passing and 9 failing nodes: independent G++
   reference differences already kept strict by item 4 (the separate
   Clang++ platform gate passed). Java reference 15132176381 had 204/4
   passes/failures; integration 15130199036 had 1255/4, the existing JDK
   disagreements and unimplemented nominal reference field storage (#13).
   Go integration 15130029592 had 878/2, unchanged go-enry's unsafe
   import frontier (errors/join.go) and whole math/bits dependency closure.
   NumPy 15130207555 is missing actual generated `_numpyconfig.h` and
   an owned C++ `<assert.h>` (C fake-libc assert is a no-op, not a C++
   implementation); orjson 15131090955 retains real pyo3/cfg_attr/Cargo
   source frontiers. No oracle substitution, fabricated generated input,
   disabled assertion, deleted node or relaxed acceptance is appropriate.
   Continue these actual frontend/runtime owners as item 4/5 units; the
   present host has no javac or Clang++ to requalify their strict oracles.
   #20 worker-prior run 45856871699 (6cbafc4a): all four platform
   artifacts (Linux x86 15140375466, ARM 15139367323, Windows 15139163406,
   macOS 15139124581) now pass the former inventory check but stop at
   `test_scalar_type_reads_use_authoritative_arena_and_live_mutations`
   after 1371-1376 passes. The direct arena `_length` optimization leaves
   the previous six `len()` dispatch observations obsolete, not the full
   four-cell proof. The exact-class model now independently observes one
   authoritative slot read and zero `__len__` dispatch per scalar call,
   all three type checks, and the original short-window IndexError followed
   by the same live arena's real append making the fourth cell readable.
   Exact preimage failed locally, changed node passes, all 100 finite
   source-extracted scalar tests pass (47.10 s on host Python 3.13). Test
   inventory, production backend, runtime and CI budgets unchanged; hosted
   execution pending. No heavy build or additional CI dispatch.
   Next: re-run worker-prior/changed-module and remaining Source; the 216-node
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
   optimization witness now separates live-pointer GEP selection from
   last-read pointer retirement. Both live GEPs use one LEA and load/extend
   directly into their register homes; the last-read fixture requires the
   pointer-home clear between its copy and address computation. Two owned
   native ELF programs execute and return the expected 42 and 49. Linux:
   132 register-home, register-copy and frame-register checks pass at
   564bac71 with the test change. No production code or GC lifetime
   semantics changed; hosted validation pending. Next: other independent
   #20 CI failures and exact-source checkpoint comparison.

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

   Hosted source frontier, run 45856871699: ci-contracts integration
   artifact15145256321 (six nodes) 3 PASS, 1 actual weakset_order pcc1
   WorkerMemoryError (available174772224 B; measured213622784 B), 2
   interrupted; 90.2558s TIMEOUT, peak978374656 B, unchanged 90s/1GiB.
   Shared reference artifact15144478141: 3 PASS, JSON pcc1 one true
   worker error available154288128 B vs actual215617536 B; completed
   23.8976s/1000853504 B. Shared integration artifact15144448317
   lane271: 152 PASS, 7 true test failures, 7 interrupted, 6 prerequisite
   UNRUN, 99 not attempted. Batch001 four actual WorkerMemoryError;
   batch002 two CPython scratch reference imports fail
   `ModuleNotFoundError: No module named 'pcc'`, one finalizer worker
   admission error, guard MEMORY_LIMIT1108127744 B vs1GiB.
   Scratch fixture only: real copied production writer's CPython child
   starts outside checkout with inherited PYTHONPATH absent. Local
   test-first exact same error; now bind checkout root explicitly only
   for CPython reference, same as sibling native batch fixture. Unchanged
   actual extracted source returns all nine semantic rows/empty stderr
   from external tmp cwd with no inherited PYTHONPATH; four affected
   ordinary scratch/batch modules 188 PASS, four native integration
   nodes deselected/UNRUN (3.32s). No native compiler/admission path,
   resource/worker/timeout/guard/expected-output change. Other memory
   failures remain unresolved. Hosted verification pending.

   Weakset exact memory follow-up (no policy change): clean source6cbafc4a
   and actual hosted pcc1/runtime admitted using original manifest
   9254520b6c746e7a9c2f804552797f196328553a346d456d5fcf076d5792313e.
   Local automatic guard rejected before launch (cgroup unavailable);
   documented explicit same 90s/1GiB plus stricter 2GiB Linux physical
   reserve. Whole six selected nodes were attempted twice with six
   workers/loadgroup, but local CPython3.13/pytest9.2/xdist3.8 did not
   produce stock @worker_resource_fixtures scheduling suffix; concurrent
   bodies hit MEMORY_LIMIT1076617216 B at12.91s and1083281408 B at5.46s.
   Invalid serial-group comparison; do not infer hosted reproduction.
   Diagnostic same exact weakset_order-pcc1 node alone under both original
   collection modules vs weakset-only module; both retained six controller
   workers, same admitted pcc1 and original cap/deadline. Both timed out:
   90.52s peak912191488 B vs90.32s peak822894592 B. Sampled controller
   plus six direct pytest worker RSS medians485322752→361226240 B
   (-124096512 B, -25.57%; 330/337 active samples). Pre-child last sample
   481419264→352882688 B; native child subtree peak426868736→461668352 B,
   largest individual native worker237613056→232562688 B. Parent
   import overhead is real, but hosted exact WorkerMemoryError and
   complete success not reproduced. Neither result is a passing weakset
   node, nor authority to split scopes for extra time or weaken resource
   constraints. Next: reduce unnecessary collection imports while
   preserving all nodes/groups/workers; qualify on exact pinned hosted
   CPython/pytest with unchanged resource envelope.

   Claims:
   - worker-resource test-only collection import footprint, preserve group and execution owners: Codex, Linux, 2026-11-26T18:47Z, area ci-resource-test-collection-heap

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
   (d) U7, 4c batch 1 (Claude, local Mac, runtime `f4889035b`, tests
   `f1e1e0169`): sixteen modules whose lease tokens stay inside the module
   drop their leases (py_call_splat_runtime, py_capi_capsule_runtime,
   py_capi_import_runtime, py_coroutine, py_dunder, py_exc_traceback,
   py_gen, py_int_convert, py_obj_ops_compare, py_os_mkdir, py_os_substrate,
   py_os_walk, py_process_substrate, py_tempfile, py_virtual_thread_runtime,
   py_weakref). Lease acquisitions, releases and their failure branches,
   the token arrays and the lease-only adopt helpers go; root copies take
   the status-only `pcc_gc_root_copy`/`_borrowed`. Under the one collector
   every lease was the no-op token 0 apart from the provenance probe, and
   only movement checks and diagnostics read a lease count, so no
   behavior changes. Each module compiles alone and none of their IR calls
   a lease function. Test models bind the status-only copies and move
   nothing; the lease-only cases are deleted or rewritten and listed in
   the tests commit. Host pcc0, by node id against HEAD `0d01e13b0`, the
   103 files naming the modules or their models and the 40 edited files
   and importers have no new failing node. Not run: integration, pcc1,
   Linux and `test_py_multi_file_bootstrap_shim.py`. Left for U8: the
   cross-module token ABIs (freestanding_class_namespace,
   freestanding_sequence_snapshot, py_re_bridge_entry, py_tuple's
   `pcc_tuple_from_owned_roots_lease`) with their callers, and
   py_capi_buffer_runtime's uncalled GC3 exporter path with its tests.
   (d) U8, 4c batch 2 (Claude, local Mac, runtime `19bdfacc5`, tests
   `ab5d7fb1d`): the ABIs that handed a lease token to another module are
   status-only, with their callers. py_obj adds
   `pcc_gc_root_copy_prepare_locked` (the split copy without a lease,
   128-byte plan finished by `pcc_gc_store_root_plan_finish`). The class
   namespace acquire/install slots and `pcc_list_snapshot_commit_slots`
   lose their token parameters; py_binary_repeat_runtime drops all its
   leases; `pcc_tuple_from_owned_roots_lease` is folded into
   `py_tuple_from_owned_roots` (its `py_runtime.h` declaration goes); the re
   chain's publication and class factories are `pcc_re_publish_copy`,
   `pcc_re_match_class_copy` and `pcc_re_pattern_class_copy`, and
   py_re_match_runtime drops its tokens array; py_capi_buffer_runtime's
   uncalled GC3 exporter path goes. Each module compiles alone; the fully
   converted modules' IR calls no lease function. Models follow; the
   lease-only and GC3-only tests are deleted or rewritten and listed in the
   tests commit. Host pcc0, by node id against HEAD `7b7be3456`, the 162
   files naming the changed ABIs, modules or models and the 18 edited files
   have no new failing node (two renamed tests fail on HEAD under their old
   names for the same reasons). Not run: integration, pcc1, Linux and
   `test_py_multi_file_bootstrap_shim.py`. Left: the own lease protocols of
   py_class, py_dict and the other larger modules (U9-U10).
   (d) U9, 4c batch 3 (Claude, local Mac, runtime `4702039fe`, tests
   `31aeaf185`): py_file, py_os_path, py_obj_stubs (buffer conversion),
   py_iter (tuple iterator), py_format_runtime, py_capi_method_call_runtime
   (its view loses the token array), py_module_attrs_runtime with its
   callers py_compiled_module_runtime and py_extension_loader_runtime
   (`py_sys_modules_owner_adopt` goes; `_owner_drop` is (slots, index),
   `_owner_finish` is (slots)) and py_list take no lease. Each module
   compiles alone and its IR calls no lease function. Models follow;
   lease-only cases are deleted or rewritten and listed in the tests
   commit. Host pcc0, by node id against HEAD `b75187cf1`, the 225 files
   naming the modules, exports or models and their importers have no new
   failing node. Not run: integration, pcc1, Linux and
   `test_py_multi_file_bootstrap_shim.py`. Left for U10: py_class,
   py_obj_ops_dispatch, py_set, py_protocol_runtime, py_dict and py_func
   (127 lease calls).
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
   U1-U5 (4b), U6, U7, U8 and U9. U10, the last 4c batch, is next.
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
   (e) The maintainer chose “多线程默认开启，保留显式关闭。补上退出等待，测一下扫描中退出。”
       The worker lifecycle and its validation are described below under
       “Concurrent old-generation scanning”.
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
   - (d) plan U1-U14 (the units under "Next for (d)", in order): Claude, macOS, 2026-11-26T12:39Z, area runtime-codegen

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
   Actual known-RC source attribution (Codex, Linux, same native handler
   and gateway identities as below): diagnostic site-tagged wrappers
   preserve original retain/release category gates; 13 sites x7 counters,
   5 C20 10/50 alternating delta800 pairs. Site totals exactly equal
   global known entries every run, w1 270.80125 all five pairs.
   `py_gen_frame_set` inline retain 92.5/request: NULL27.5, tagged5.2,
   immortal58.75, actual1.05; inline old release92.5: NULL58.75,
   immortal33.75, actual0. `py_gen_frame_move` old release10.5:
   NULL5.35, immortal5.15, actual0; same-value release19.85 all immortal.
   Factory retain4.1 all actual; other compiler/unclassified51.35125
   (tagged5.00125, immortal16.15, actual30.2, terminal1). All audited
   get/list-frame and known root-store/take sites zero in this workload.
   Thus inline set+move: 215.35 total, 214.3 no-op (91.0% of all
   235.45 known no-op entries); w4 set medians91.76 each, old move28.00
   (NULL22.92), same-value move19.40 all immortal; actual set retain
   1.05, all set releases and moves no count changes. Runtime diagnostic
   binary SHA4e2d2bde745c729bac18f0fad19148ec4433452b47bce11f1cca176ad324c111.
   Source instrumentation restored, no diagnostic timing or code published.
   Inline frame known-RC candidate (Codex, Linux, clean baseline
   ab8696fc5). Measured actual self-compiled native indirect-call
   primitive, same binary trivial callable control, 10M iterations and
   five control/candidate/control batches per category. Quiet medians of
   net retain NULL/tagged/immortal 0.68/1.26/2.56ns and release
   NULL/tagged/immortal 2.57/2.92/2.49ns. Exact above w1 counts weight these
   to 0.487us/request over callable noop; entire arm's two calls and loop
   give a deliberately loose 1.272us/request upper envelope (2.64% of
   48.185us baseline). Candidate's guard is actually inlined at all three
   frame sites, avoids NULL/tagged/admitted immortal known calls, keeps
   original checks for -1 or enabled and publication before terminal
   release; same-value move drops exactly one old owner. 21 focused
   tests passed including audit-mode direct C owner/alias/move/take,
   known object lifetime, 4-carrier list/dict races and append/shared
   containers, generator finalization and virtual-thread park/resume
   exceptions. Actual native cross-carrier lock program also passed.
   Both binaries passed exact gateway response checks with provenance=3
   and known checks=1 on w1 and w4. Uninstrumented source-matched static
   ELF SHA6204ced20453c2755163051afa7ebdf164798e9d6d26e762621da4cec8ce7aa3
   and candidate SHA f314ddbc7b00c35fd3d8f2e4675422e2d5f116486ca5539a1b1166f5461065af.
   5 alternating pairs plus identical baseline control, C100 x300
   rounds, all exact response payloads passed and allocation counts
   exact w1, +/-3 scheduling-only w4. w1 21,248->20,203 QPS (-4.91%,
   1/5; control -4.61%), CPU 48.185->50.730us (control 50.742);
   RSS identical median20,791,296B. w4 43,260->41,548 QPS (-3.96%,
   3/5; control -2.66%), CPU86.682->90.154us (control89.525),
   RSS25,370,624->25,464,832B. Equivalent control drift, no validated
   gain; not merged, no candidate source/diagnostic code published.
   Native managed allocation/free attribution (Codex, Linux, exact
   source 2cc0e6fb, gateway b1490486). Diagnostic-only atomics at actual
   pcc_gc_alloc attempts/success and both pcc_gc_free_object_memory
   release-to-allocator paths; type tag 0..255, overflow and separate
   inline GEN flag bucket, requested header/body bytes and size bands.
   Stop after carrier pool shutdown before result formatting and compare
   attempt sum and histogram to authoritative snapshot telemetry.
   Native sanity exact list and inline-GEN allocate/free increments, both
   sums equal. Instrumented static ELF
   f9a1f60e93dc4030906190a271395d8a0c1ecc0dd983f81ae99ede63534e744c.
   All 10 alternating C20 rounds10/50 pairs delta800 reconciled exact
   snapshot, successful==attempts, exact gateway payload. w1 all five
   repeats 38.45 allocations, 2754.4 requested managed bytes, 37.45
   terminal fast frees, 0 other free/request. w4 medians equal, observed
   allocation scheduling variation <=0.0025/request. Not raw malloc
   payload or throughput. The benchmark retains one latency float per
   request through the snapshot; all other median managed types free
   at the same rate as allocated.
   Per-request type (count, requested bytes): list (7.10,284.0), tuple
   (6.10,235.6), float (6,144), inline GEN frames (4.10,732.4), non-inline
   GEN (4.05,226.8), iterator (3.05,97.6), virtual thread (3,600), dict
   (2,112), dynamic class tag248 (1.05,84), str (1,127), bytes (1,111).
   List+tuple 34.3% of allocation count; GEN+inline GEN+virtual thread
   29.0% of count and 56.6% of requested bytes. <=64-byte objects
   28.3/38.45 (73.6%). No slow free path in this workload. Diagnostic
   instrumentation restored; no timing or production change claimed.
   Completed continuation allocation attribution (Codex, Linux,
   source 5be4d177, exact gateway b1490486). Diagnostic counters in
   py_gen_completed, py_gen_new, py_gen_from_inline_frame, take and
   synchronous take, disabled after carrier shutdown before report.
   Source-matched static self ELF SHA
   748ca8d56150c6a9df790183e9dae6445f4bf4b815814630c8ad9cd955b7e3e2.
   All five alternating C20 10/50 delta800 pairs on both w1/w4: 4.05
   completed enters/successes, 4.05 py_gen_new enters/successes, 4.10
   inline from enters/successes, ZERO inline fallback, 4.05 successful
   take_completed, zero synchronous take successes. Total take attempts
   w1 6.25, w4 median6.745 (scheduling variation). Thus all ordinary GEN
   allocations in this workload are temporary completed wrappers, and
   all are immediately consumed by delegated caller, not escaped.
   Separate uninstrumented native owned C primitive on exact same clean
   source: two registered roots, create+take borrowed+retaining replace
   releasing wrapper+clear vs direct rooted value+clear, 2M iterations,
   five alternating base/wrapper/identical-base control triples, both
   modes exact identity/output and exactly 2M vs zero allocations.
   None CPU base19.7055/wrapper302.5025/control17.1675ns, paired net
   283.207ns (absolute paired control drift3.255ns); counted list
   base36.1645/wrapper360.778/control35.631ns, paired net324.5955ns
   (control drift0.916ns). Static ELF SHA
   42e3f95f4bd6923bc2fb5157983958209ac89852258c9d351e4a712f1388bd70.
   Weighted 4.05 actual wrappers cost 1.147--1.315us/request on these
   reference modes (2.38--2.73% of earlier w1 48.185us; estimated source
   mix 3 counted fork and 1.05 None close, ~1.277us). This is a primitive
   ceiling, not whole-handler speed or actual counted/None timing
   attribution. Runtime diagnostic restored; no production change.
   Next: inspect exact closed-world factory return and direct delegated
   call owner. Prototype only source-proven immediate-result/deferred
   handoff using an explicit status/result slot (no pointer tagging,
   public generator/ordinary factory ABI stays valid; no implicit TLS
   side channel). Admit only proven tail completed/continuation choices;
   arbitrary or escaped factory results keep original ABI. Preserve
   result ownership before argument/receiver release, pending exceptions,
   deferred suspension, finally, cancellation, and cross-carrier tests.
   This removes 10.5% of allocation count plus wrapper protocol, but the
   observed local CPU ceiling is modest: measure actual emitted call and
   allocation reduction and 5 native paired A/B with unchanged control
   before merge, do not undertake a broad ABI redesign to chase <3%.
   If no narrow safe route, reject and move to measured list/tuple/iter
   allocation owner attribution.
   Exact proven retain candidate (Codex, Linux): symmetrical early return
   in `_gc_retain` for the same SSA proof used by `_gc_release`,
   preserving exact identity and ptr type, with unknown same-spelling
   value still going to ordinary/known retain. 16 focused identity and
   None-result publication tests pass. Broader 75 green/23 red included
   22 retired-collector runtime models (unchanged runtime code) and one
   host CPython 3.13 truth-of-NotImplemented mismatch with PCC's target
   Python 3.15; no full gate claim.
   Actual diagnostic native handler 5 paired w1 counts equal in every
   one of 29 categories (known retain96.6, release174.20125, checked
   inc118.2525, dec230.75 both arms), w4 scheduling-only variation.
   Exact-proof elision removes zero per-request calls in this workload;
   not merged, no timing claim. Important source audit: known RC calls
   are predominantly direct runtime `py_gen_frame_get/move/set`, plus
   known root stores; only a few direct frontend `_gc_retain` sites.
   Next attribute the actual known no-op counts by these generator
   frame paths and root move/restore, then remove redundant copy/retain
   or release for proven empty frame cells without crossing a park owner
   or losing callback/weakref/GC semantics. Do not keep screening tiny
   compiler literal proof without measuring actual source origin.
   Actual RC category attribution (Codex, Linux x86_64, same native handler
   source e5a2ca85 plus facade fix `1820d5fd`, gateway b1490486):
   diagnostic-only 29 atomic entry/branch counters in py_obj, C20
   (50-10) rounds delta800 requests, 5 alternating pairs, w1 identical.
   `pcc_gc_retain_known` 96.60/request: NULL27.50, tagged5.20, immortal
   58.75, actual increment5.15 (94.7% no-op). `pcc_gc_release_known`
   174.20125: NULL64.10, tagged5.00125, immortal74.90, decrement30.20
   (82.7% no-op), 29.20 nonterminal/1.00 terminal. No verify, negative
   or nonpositive fallback. Known combined 270.80 entries, 235.45 no-op
   (86.9%); do not add to checked APIs as independent cost.
   `py_incref`118.2525: NULL7, tagged1.0025, fast immortal26.4, atomic
   46.75, checked tail37.1. `py_decref`230.75: NULL21.1, tagged0,
   fast immortal99.5, CAS nonterminal38.6, checked tail71.55.
   w4 medians known retain96.05 (27.5 NULL,5.10 tagged,58.3 immortal,
   actual5.15); known release189.90 (80.34 NULL,5.06 tagged,74.45
   immortal, actual30.2, terminal1). Checked inc119.42 and dec232.17.
   Native probe binary SHA564bc14fa0512c7ef4505221cc6e8c152ec1dacea44ef96b6c2d075aa09dbc36;
   input/output handler verified, diagnostic timings not used, no source
   instrumentation published. Next high ROI: quantify statically provable
   tagged/null/immortal SSA and proven immortal root loads. `_gc_retain`
   presently emits a call even when `_value_is_never_gc_object` is true
   (the same exact emitter-owned proof already suppresses `_gc_release`).
   Test symmetric retain-elision as a narrow slice, but only measure
   actual call-count reduction, cost and 5 A/B+control; do not assume all
   runtime immortal/NULL cases have compile-time proof.
   Second empty-root experiment (Codex, Linux): narrower real JSON
   sequence-unpack candidate, `_store_unpack_root_target` returns true
   only after checked move into local; immediate clear omitted only then,
   other target kinds still release, final dedicated item frames retire
   flags/LIFO without second clear. Exact native mixed local/subscript
   assignment, finalizer and short-iterable exception witness passes,
   CPython parity, new IR failure/leave proof; 42 focused host pass with
   exact two existing red and nine deselected. Handler smoke passes.
   Static proof removes both redundant calls for checked local moves;
   no fresh dynamic count for this second candidate. Equalized native
   files with fsync/fadvise, warm both. JSON fixed-count 5 pairs CPU
   18.252 ->18.087 us/op, duplicate base17.991, 3/5; unchanged dict
   control0.841 ->0.907, duplicate0.856. Actual handler C100x300 w1
   QPS21465 ->21187, duplicate19083; CPU47.79 ->48.09 dupe53.81.
   w4 first: QPS38034 ->43302, dupe38608 (4/5), CPU98.22 ->86.62
   dupe95.40. Longer repeat C100x1000 did not reproduce: QPS40323
   ->38895 dupe38611, CPU92.17 ->95.45 dupe96.55. Allocation counts
   and output equal, equalized RSS within control. No stable speed gain,
   not merged. After two screened empty-root elimination units stop
   this sub-area until the maintainer decides whether to bundle exact
   work removal below timing floor. Next independent runtime ownership
   target: quantify RC and TLS accesses, not redo these two candidates.
   Empty root-clear experiment (Codex, Linux, same actual handler source
   and diagnostic arms as below): retire private operand roots on checked
   success after `pcc_gc_root_move` or after each unpack item has already
   been explicitly cleared, preserving flag and frame leave order. Static
   actual gateway baseline had 56 adjacent redundant clear pairs, mostly
   sequence unpack. Native instrumented handler C20 10/50 delta800,
   five exact w1 pairs: store_root 308.05125 -> 300.05125, empty clear
   108.30 -> 100.30, all other counters exactly equal. w4 medians
   315.46 -> 307.325, empty 114.74125 ->106.71. Primitive fixed-count
   screen localized all eight to JSON (58 ->50 roots, 16 ->8 empty clears;
   allocation and refcount entries equal); other 27 primitives unchanged
   except noise in multithread scheduler counters. Native own-root
   finalizer/exception witness passes; 47 focused host passes and the same
   eight failures as exact base, one new IR success/failed-move test passes.
   Uninstrumented C100x300 5 A/B+control: w1 QPS 17850 ->19336,
   duplicate base control19481, CPU us 56.93 ->52.26 control52.49 (4/5);
   w4 38964 ->41747 control39877, CPU96.51 ->89.06 control94.23 (3/5).
   JSON fixed 30k timed + 30k warm operations, pinned process CPU us/op
   21.252 ->18.648, duplicate base18.361, 2/5, exact checksum & allocations.
   Thus no stable incremental speed beyond control; not merged.
   RSS apparent +6 MiB was file residency, not heap: smaps Pss_File
   10544 ->16452 KiB while anonymous8888 ->8896. After fsync and
   POSIX_FADV_DONTNEED on both owned artifacts, two pairs w1 total
   19290 ->19356 KiB, anonymous8790 ->8788; w4 total26164 ->25548.
   Preserve this control for future native A/B. Next: JSON `_encode_object`
   and `_encode_array` use sequence unpack; `_store_unpack_root_target`
   returns no proof even when its checked successful move empties source,
   so both the caller's immediate clear and final release repeat work.
   Make successful ownership transfer explicit, combine both eliminations
   with other actual high-count sites, then measure five paired real JSON
   and handler CPU/QPS and memory with an equalized file-residency control.
   Linux handler measurement (Codex, 2026-11-26, base e5a2ca85 plus
   facade dispatch fix now `1820d5fd`, gateway `b1490486`, host CPython
   3.13.5 self native ELF; full handler smoke passes 1/4 carriers):
   C100 x100 rounds, 5 rotating PCC/asyncio/duplicate-PCC controls,
   contemporaneous other builds: w1 21664 QPS / 48.19 us CPU/request,
   asyncio 61771 /25.61, control 21435 /48.85; w4 39245 /97.25,
   asyncio 125992 /57.88, control 39465 /97.02. Native binary SHA
   a8cf4ebe5f55c8b1c36a0f36027e27457b2df843b80915267c5b07d4a79e002b.
   Independent diagnostic-only runtime instrumented 10 entries and
   counted API calls over C20 x (50-10) rounds = 800 requests, 5
   alternating pairs. w1: store_root 308.05, NULL->NULL 108.30 (35.2%),
   take 19.25, known store/take 0, py_incref 118.25, py_decref 230.75,
   retain_known 96.60, release_known 174.20, allocations 38.45. All
   five pairs identical. w4 medians: store_root 316.10, NULL->NULL
   115.40 (36.5%), take 19.26, known store/take 0, py_incref 119.65,
   py_decref 232.51, retain_known 96.16, release_known 193.34, alloc
   38.45. Entry counts overlap (do not sum as independent costs).
   Diagnostic counts match uninstrumented allocation counter and confirm
   Mac attribution; instrumented binary not used for timing, no source
   instrumentation published. Native image is stripped; precise hardware
   cycle preflight unavailable on Linux, software task-clock preflight
   passes but cannot bind sample IPs to function symbols in this image.
   Next: statically prove and eliminate redundant empty-root clear calls
   without skipping trace visibility, terminal release, debug or alias
   semantics; target the real request path, 5 paired A/B plus control.
   Linux handler correctness blocker (Codex, source `e5a2ca85`, gateway
   `b1490486`): a fresh self-compiled native handler linked successfully
   (runtime 85.65s, total 219.22s) but trapped in public host-only
   `pcc.virtual_thread.spawn`. A minimal import of `pcc.virtual_thread as
   vt` plus `sys` reproduced the same error; single-source IR correctly
   lowered the primitive, explicit closure including the compiled public
   facade incorrectly used generic imported method dispatch (the facade
   deliberately raises). Give lexically proven native vthread calls
   precedence over generic compiled module alias dispatch; shadowed local
   aliases continue through the existing proof and generic method path.
   New real-facade multi-IR regression and eight adjacent host analysis
   cases pass; candidate runtime rebuilt with matching codegen provenance,
   native minimal executable ran with empty environment/PATH nonexistent
   and printed 7 (base exited 1 with trap). No full handler/QPS claim yet:
   next compile the actual handler with this fix; compare Linux allocations
   and root/refcount operations per request once smoke passes.
   Lifecycle allocation unit (Codex, Linux x86_64, source `605ddc2f`):
   on the cached object-family take/put path, fold the intrusive head/count
   updates into the callers and reuse the incremented scalar for the drain
   threshold. Public helper symbols, refill/drain locking, poison and LIVE
   publication/retirement ordering are unchanged. Five alternating pairs
   of 3M managed-cell cycles (32..144 bytes, allocate, initialize, publish,
   exact check, retire, free), self-generated allocator assembly with GCC
   reference harness `benchmarks/c/allocator_object_lifecycle_churn.c`:
   process CPU median 170.87 -> 157.54 ns/cell (-7.8%, 5/5), cache-disabled
   control 215.95 -> 213.32 ns/cell (2/5, noisy). Independent initial run
   170.77 -> 158.72 ns/cell (5/5), control 180.16 -> 186.96. All checksums
   and live requested/usable deltas match. Standalone pthread remote-free
   4 workers x 25 x 600 cells, each arm 3/3 passes with identical accounting.
   Native magazine fixture extends burst to 600 cells and reuses it twice;
   syntax checked, full native integration and handler QPS unqualified.
   Repro: compile allocator using `compile_python(..., emit_llvm_only=True,
   python_library=True, target_triple='x86_64-unknown-linux-gnu')`, then
   `emit_self_asm` for the same triple and `gcc -c` as test assembler.
   Link the benchmark with GCC `-O2 -fno-builtin`; execute 3000000 0 and
   3000000 1 in alternating pairs. Next: count actual request allocations
   and refcount calls on a current native handler before estimating QPS;
   prioritize redundant empty-root clears and TLS access count, not ABI deletion.
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
   (h) Not merged, no request-level gain (Claude, 2026-11-26): callee-saved
   pairs with the higher register at the lower address (`stp x20, x19`, as
   clang) and one SP adjustment for the frame record and the callee saves
   (`stp x20, x19, [sp, #-(n+16)]!` ... `stp x29, x30, [sp, #n]`). A call
   loop microbenchmark on the Mac measured 2.15 ns per call for the old
   layout, 1.45 with the pair order and 1.04 with both (clang 0.95). The
   handler request did not follow: `instr` with a control arm (a second
   build of the base; load 11.8, five runs) gave cycles -1.59% (pair order)
   and -0.07% (both; instructions -0.60%) against a control floor of
   +2.05%, and two earlier screens were +2.07% and +1.14%. Backend test set
   7248/7250 passed (the two pcc1-gated nodes were not run). The patch is
   kept outside the repository as `p0-callee-pair-order-and-folded-frame.diff`
   beside `p0-frameless-atomic-leaves.diff` (frameless atomic leaves: -2.51%
   cycles in the same run, also inside the floor). The same run's
   instruction counts varied 32% between runs at load 11.8, against 0.3% at
   load 3: some request path executes more instructions when it waits
   longer, so `instr` is not load-independent; not yet located.
   P0 Next (unit 1 closed 2026-11-26 with (c), (d), (e), (g) merged and
   (h) parked): self executes 1.27x clang's instructions on the same
   runtime IR, but no instruction-removing change since (c) moved cycles
   per request beyond noise. Before another backend change, compare
   sampled cycles per function of the self and clang builds under the same
   conditions (`pcc_perf.py top` on both arms, then the hot function's code
   in both) and rank by cycles; measure on an idle host with a control arm.
   P0 unit 2 (next, unclaimed): the per-function cycle comparison of the same
   runtime IR (10:34, self 52807 against clang 43004 cycles per request) is
   flat, the largest excess `pcc_gc_store_root` +802; by leaf address about
   70% of the gap is stack traffic. Frame layout is (h); next are value
   stores to stack slots and small constant-size `memset`/`memmove`
   (+560 and +361 cycles; clang expands them inline).
   P0 allocation model design (Claude, 2026-11-26; the user said to do it,
   answering waiting-list P0 (c) with "at least LLVM parity"). Evidence:
   `pcc_gc_store_root` (arm qps-ea1ea73) saves x19-x24 and the frame record
   on every entry and restores all of them on every exit, the
   `slot == NULL` return included, because its arguments are copied into
   callee-saved registers at entry: they are live across the calls of its
   slow paths (`py_incref_checked_tail`, `py_decref`). Not the cause:
   post-safepoint reloads (447 reload pairs after 328551 calls in the whole
   benchmark binary; the one collector does not move objects). After a
   call the next instruction is most often `mov` (84707) or `sub` (63166,
   mostly a root-slot address for the next call). Current model
   (`self_backend_aarch64_darwin_regalloc.py`): one linear function-wide
   interval per value; any call position inside it forces x19-x28;
   every value keeps its slot; the prologue saves every assigned
   callee-saved register and every epilogue restores them. Plan, each step
   measured alone with `pcc_perf.py instr` and `table` against a control
   arm and gated by the native and bootstrap checks:
   (1) Liveness with holes: per-block live-in/live-out by a backward
   dataflow over the CFG (bitsets per block), and "crosses a call" means
   live after that call instruction, not a call anywhere inside the
   linear hull, which today also counts calls on other paths.
   (2) Shrink-wrapping: save the used callee-saved registers and the frame
   record at the nearest common dominator of the blocks that use them and
   restore them on the exits those blocks reach; paths that use neither
   (early returns, call-free fast paths) run neither. Arguments stay in
   x0-x7 until the wrapped region copies them. Safepoints are calls, so
   every stack-map record lies inside the wrapped region and frame-relative
   slot offsets keep their meaning there.
   (3) Cold-call splitting, only if (1)+(2) leave the fast paths of hot
   helpers saving registers: a value live across calls that lie only on
   paths into an error landing or a `*_checked_tail` slow helper keeps a
   caller-saved register and is stored to its existing slot before such a
   call and reloaded after it; the coldness rule is structural and tested,
   never a per-function list.
   (4) Then the instruction-selection items of the exact ranking: address
   folding (`adrp`+`add`+`ldr` -> `adrp`+`ldr`), bit tests as `tbz`/`tbnz`,
   tag ranges as one unsigned compare, no `nop` on executed paths, small
   constant `memset`/`memmove` inline. Compile-time budget: the dataflow is
   linear in blocks times live words; Stage1 worker replays (array_core,
   hoist_predicates) must not slow down beyond their run spread.
   P0 allocation model results (Claude, 2026-11-26, local Mac; base
   11c88fb4e; arms alloc-base, alloc-base2 = two builds of the base,
   alloc-precise, alloc-split..alloc-split4, alloc-pack; nothing merged,
   code on the local branch `exp/aarch64-call-spills` d371cc81c with its
   tests). (1) Liveness with holes (`_precise_call_crossers`): -0.70% to
   -1.51% instructions per request in four `instr` runs (w1), but
   `ab alloc-base2 alloc-precise --control-arm alloc-base` gave 61441 ->
   61418 QPS (-0.0%, 2/5 pairs, floor 0.7%): fewer callee-saved saves do not
   show in throughput. (3) Cold-call spills: with equal branch
   probabilities (the IR carries no branch weights or `expect`) a helper's
   main path behind a few early-return guards looked cold
   (`py_decref_finish` 180 -> 243 instructions); first build +1.98%
   instructions and 28.8 -> 31.9 MB, mostly byte slots made physical whose
   padding the prologue clears one byte at a time. A second build crashed
   (alias members sharing a register spilled through different slots;
   fixed per register, with a native test). Restricted to structurally
   cold blocks (reached only through inline error edges, or no return
   reachable) it changes no function of the benchmark binary. Packing the
   physical frame by alignment cut prologue zero stores 29155 -> 26648 but
   no measurable instructions per request. Reassessment (rule: three
   candidates under 5%): A1 TLV (~2% ceiling), the immortal-object and
   class-refcount items and these allocation steps all measured at or
   below the noise floor, so callee-saved and stack traffic is not the owner
   of the +45% clang gap on this core; next is task "locate the cycle gap":
   per-function sampled cycles of the self build against the clang build
   of the same runtime IR, idle host, then the mechanism of the top
   excess function in both, before any further backend change.
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
       304 calls per request clear a slot that is already empty. Compile
       side (2026-11-26, `scripts/pcc_perf.py compile` on 40216ff43): one
       function, `pcc_gateway.server._proxy_exchange_attempt` (263 source
       lines, 73 park points), lowers to a 327865-line resume function, 20%
       of the program's 1.66M IR lines, and its emit worker (67 s) is the
       longest serial step of a 131 s cold compile. It has 51456 blocks and
       21935 release-if-flagged sequences (`_emit_release_owned_local_if_flagged`:
       flag load, branch, `pcc_gc_store_root`): each hidden selection slot
       (`handled.resume.pending.owner`, `except.class.owner`,
       `except.class.replacement.owner`, one fresh slot per site from
       `_exception_selection_owner_slot`) is released about 40 times, and
       each of the 73 park points saves 90 to 142 slots with
       `py_gen_frame_move` (9852 calls), which is also per-park run time.
       Attributed by wrapping the emitter during a full compile: all 21935
       come from `_emit_owned_local_cleanup`, 16494 from
       `_emit_generator_yield_value` (every park releases every owned local
       inline) and 5296 from `_emit_generator_finish` (every generator
       return does the same). Ordinary returns already share one exit
       (`shared_exit_leaves`); generator parks and returns do not.
       (a) done (Claude, 2026-11-26): each park and each return now
       branches to one shared exit per resume function (one for parks,
       one for returns) that releases the union of the exits' owned
       locals once, each by its runtime flag, after the body has
       registered every slot; the yielded owner's flag is cleared
       before the branch and the slot is taken after the cleanup, as
       before. `_owned_local_cleanup_plan` is the one selection both
       ordinary exits and the shared exits use. A 16-park generator went
       from 693 to 108 release sequences and now grows linearly
       (`tests/python/test_generator_shared_suspend_cleanup.py`, which
       also runs parks, returns, close and throw natively against
       CPython's finalizer output). The runtime's 8264 functions are
       byte-identical (no runtime generator), and the handler arms' smoke
       cases pass. Found while testing, not changed: where several
       locals die at one exit pcc releases them in name order, not
       CPython's; CPython's traceback keeps a thrown-into generator's
       frame alive until its except block ends, pcc releases earlier.
       The first version joined the exits through phis; the self IR
       verifier rejected a shared exit's phi with excess predecessor
       edges in Stage1, so ca5d198e6 turned it off. dcc443bcf carries
       the value, next state and handoff slot through three entry
       allocas instead and turns it on by default
       (`PCC_GENERATOR_SHARED_EXIT=0` turns it off); a macOS Stage1 of
       it passed, smoke included. (c) done in the same commit: the
       hidden owners of `handled.resume.pending`, `except.class` and
       `except.class.replacement` reuse one slot per function and label
       (`tests/python/test_exception_selection_slot_reuse.py`). (d) one
       traceback-append block per function: with slots it added 2.6% IR
       lines on the measured module, not merged. Next: (b) a park saves
       only the slots live across it (73 parks x 90-142
       `py_gen_frame_move` in the gateway function, also per park at run
       time); (e) shared call-site error cleanup; (f) fewer
       `py_err_occurred` checks (a `__repr__` of a three-field dataclass
       checks it 15 times, once after each call that already returns
       NULL on error); then the QPS measurement and a systematic review
       of the frontend for the same exits-times-locals shape.
       Codex conservative static save-prefix screen (Linux, source
       `702bb226`, actual gateway `b1490486`, 25-module closure plus stdlib):
       1,172,323 -> 1,172,178 total IR lines, 19,759 -> 19,649 save moves;
       the dominant `_proxy_exchange_attempt` is unchanged at 95,620 lines
       and 9,852 `py_gen_frame_move` calls. Its 73 shared-exit value inputs
       are all raw `vthread.delegate.value` SSA results, excluded by the
       proof that admitted only permanent words or registered handoff slots.
       A 32-park fixture did shrink 704 -> 22 moves and x86 object
       144,616 -> 101,688 bytes; five alternating compile+object CPU pairs
       1.236 -> 0.900 s, 4/5 raw but only 3/5 control-normalized wins;
       unchanged control 0.441 -> 0.476 s, byte-identical within/across arms.
       This is not evidence of a Stage1 or gateway QPS gain. Source-matched
       Linux native text and direct indexed arms matched CPython's output
       for yield/send/throw/close and finalization. Related host gates: 19
       passing, 29 failing, same 29 on the exact base (17 passing before
       the new two host nodes); the failures are unchanged borrowed-owner
       retirement assertions and Linux-to-Darwin target mismatch.
       Rejected, no production code published. Next first prove the delegated
       return's owned handoff to an always-empty registered activation slot
       before existing frame saves, and verify that publication, callback
       order, stack-map identity and resumed child ownership stay exact;
       then reconsider shared save-prefix or park-live-slot reduction.
       J2 delegated park handoff (Codex, Linux, 2026-11-26): one
       registered activation-only owner cell receives the freshly yielded
       child result after scalar return/null checks; the persistent child
       slot is not changed. No intervening call before publication; the
       existing shared park exit takes the result after its unchanged
       cleanup. A common exact entry slot/flag save prefix dispatches to
       each original suffix only after the primary resume switch's case
       span is complete (direct indexed capture). Broad admission rejected:
       it affected hot small scheduler functions. Admit only >=64 static
       slots and >=16 lexically proven delegate sites, not ordinary yield
       or await. In the actual gateway b1490486a closure, only the large
       proxy resume qualifies: 95,620 -> 84,228 IR lines (-11.9%),
       9,852 -> 4,044 static frame moves (-59%); whole closure
       1,172,323 -> 1,160,931 lines. Native_stats, TaskScope and primitive
       modules remain byte-identical. The isolated actual x86 proxy object
       shrinks 3,428,824 -> 3,144,576 bytes (-8.3%). Toggle
       PCC_GENERATOR_SHARED_FRAME_SAVE=0 is in the frontend cache key;
       disabled fixture IR and actual executable are byte-identical to
       their original baseline. Linux source-matched native text/direct
       20-site fixtures on one/four carriers preserve child ownership,
       aliases, error and finalizer output; ordinary generator/coroutine
       send/throw/close matches CPython. Host adjacent gates 98 passes vs
       baseline 90, same 34 pre-existing failures; cross-target owned
       Darwin emission passes (not a Mac runtime test). Compiler closure
       contextual probe (624 sources) changed generator module fallback
       count zero; not a complete Stage1 bootstrap.
       Real proxy owner backend emission, five alternating quiet Linux
       CPU-pinned pairs, 19.984 -> 18.038 s (-9.74%, 5/5); unchanged
       control 2.001 -> 2.053 s (2.68% median absolute paired drift),
       control-normalized median -12.53%, 5/5. Both owner arms and the
       control are byte-stable within arm. A first loaded five-pair run
       was noisy and cannot establish speed. Single cold native gateway
       build 117.76 -> 114.59 s wall, 287.09 -> 270.61 s CPU, not a
       five-pair full-build result. Handler C100 x300 five pairs w1
       19,899 -> 20,789 QPS, 50.80 -> 49.29 us CPU/request, control
       20,308/50.28; w4 38,622 -> 36,712, 96.74 -> 101.08, control
       37,917/97.25. The longer isolated w4 C100 x1000 five-pair repeat
       after equal file-cache treatment went 36,637 -> 38,769 QPS,
       98.08 -> 95.19 us, control 38,314/96.19 (4/5 raw wins for both).
       Opposite noisy throughput effects are not a reproducible handler
       speedup/regression; no QPS benefit claimed. The admitted result
       is the measured backend-owner compile CPU/object reduction only.
       Next: park-live-slot reduction by proof, or a single error landing
       per function without adding hot-path ownership work.
   J2s. Stage1 compile time (the user, 2026-11-26: target five minutes,
       six workers). A macOS Stage1 on dcc443bcf's tree took 2163 s wall
       under host load and 8795 s CPU; its 622 codegen workers took about
       8600 worker-seconds, 91% of a large worker in the self backend
       (54% in the record-level passes after selection, about 20% in
       constructing frozen-dataclass records on CPython), and produced
       objects at about 3.4 s per MB (pcc1 `__text` 225.4 MiB;
       `pcc.frontends.python.py_ast`, 10 KB of source, is a 10 MB object
       and a 60 s worker). Six perfectly scheduled workers would still
       need about 1440 s, so five minutes needs about a fifth of the CPU:
       smaller generated code first, then the backend's per-record cost.
       Done: 781c088d1 (module memory prior: 3-4 workers ran where 6
       fit; replayed admission 1823 -> 1506 s), 6b3df67a0 (host workers
       decode their root's export closure, not the 30.7 MB graph),
       564bac71a (the pool stops re-deciding an unchanged declined
       admission), a35adce4c (six Stage1 frontend workers, six backend
       jobs). Unmeasured as a whole: no Stage1 has run with all four.
       Measured without a Stage1 (codegen workers replayed from that run's
       manifests with the direct-indexed capture off): the self backend
       takes about 200 us per IR line, linear in module size (class_gen:
       706k lines, 10 s frontend, about 125 s backend), so Stage1 is about
       43M IR lines, 65 per source line. Replays run `python -m pcc`,
       which puts the working directory first on `sys.path`: run them
       from the tree under test (an earlier pair of "no gain" results ran
       the main checkout's code). Measured from the worktree, alternating
       arms, objects byte-identical: reading `CompilerIntArena._length`
       directly instead of `len()` in the hot backend readers saves 4-8%
       of a worker (array_core, hoist_predicates; two runs of five pairs,
       10/10 wins each); tuple-based CompilerInt records on the host saved
       4.5-4.9% but were rejected, because a tuple subclass changes every
       `isinstance(x, tuple)` test a record reaches (a test helper tells
       address groups from records that way). Across
       class_gen, c_codegen, cli_bootstrap, type_infer and four smaller
       modules (`pcc_perf.py ir`), per-call-site error paths are 25-30% of
       IR lines: a cleanup chain of `py_cleanup_one_root_preserving_exception`
       + `pcc_gc_frame_leave_lifo_local_mapped` per live temporary (5.2%),
       `py_current_exception` + `py_exc_append_frame_source` per site
       (5.0%), the exception-preserving call-slot cleanup (6.7%),
       `py_err_occurred` checks (3.0%), raise sequences (2.6%). The chains
       exist because each temporary root slot enters and leaves its own
       LIFO frame, so error edges from different sites see different
       frame stacks and cannot share a landing.
       Later the same day, all pushed and replay-measured (five
       alternating pairs, objects byte-identical unless noted):
       520501236 direct arena lengths (-4 to -8%), 734b1152f drops the
       never-called `_pcc_py_module_init_<mod>` (py_ast -26% CPU, object
       -24%), ffc64f469 three-to-five-root error cleanup helpers (-3% to
       -6.5%, objects -4.5% and -17%), 188619260 and 8b35b70a5 typed
       passes share decoded opcodes, labels, branches and tokens across
       passes (-15% and -2.5%), 06272c490 a Stage1 codegen worker reuses
       its Darwin object when the captured kernel, emitter sources, target
       and PCC_ settings are unchanged (a hit: array_core 8.5 -> 1.6 s,
       py_ast 25.2 -> 3.0 s). Stage1 on 06272c490 (host load 15-20, both
       passed with smoke): cold 1887 s wall, 6911 s CPU (-21% against the
       morning's 8795 s); warm, no source change, 607 s wall, 1468 s CPU;
       `__text` 221.4 MiB. In the warm run the codegen workers take about
       180 s and the last ~300 s run on 2-5 processes ending in
       `pcc link`; that tail is the next thing to attribute. The user,
       2026-11-26: the five-minute target is for a cold Stage1 too.
       Rewrites per typed pass over array_core, hoist_predicates and
       pipeline_frontend_parallel (~580k records each pass):
       thread_trampoline_branches removes 11609 and adds 2921,
       fold_scratch_def_into_move 3598/1799, the adjacent stack
       store/load forward 2265/2259; the other sixteen together under 300,
       ten of them none. Instruction selection already emits what most
       passes look for, yet each pass still copies and sweeps every
       record. The typed peepholes now find their candidate windows on
       the host from the header opcodes in one bytes scan and bulk-copy
       the IDs between them (peephole passes on array_core 1.81 -> 0.83 s;
       worker CPU array_core -14.1%, 297 -12.9%, five pairs, 5/5 wins,
       objects byte-identical). Stage1 on that commit (d2089f80c, local Mac,
       host busy with tests): cold 1269 s wall, 5248 s CPU (-24% against
       06272c490), codegen done at about 1100 s, link 162.9 s single-threaded;
       warm rerun 581 s wall, rc 0, and its pcc1 compiled and ran a class /
       exception / dict probe with CPython's output. 9848d7812 copies each
       direct instruction into the module as integers (array_core -6.1%,
       297 -6.0%). Set aside, CPU inside the noise (array_core -2.3% 4/5,
       base spread 10%): consecutive span appends growing one tail run.
       The link (replayed in process from a stashed Stage1 input set, output
       byte-identical to the Stage1 pcc1; replays need the process-tree
       guard's budget environment or the stack-map merge refuses 8M+
       records): about 15.6M relocations, each folded into a dict and a
       Relocation object about three times; folding is 37% of the link,
       symbol-name resolution 64M calls, source-relocation validation 18%,
       stack maps about 15%. Open failing nodes on HEAD before today's
       commits: test_llvm_capi_direct_indexed_kernel.py::
       test_direct_publication_uses_exact_static_abi_in_stage1_context
       (dynamic getattr in arm64_asm_driver._define_label); the GC4 copy
       fixture tests (fixture files removed with GC4). Sampled (SIGPROF,
       not cProfile, which inflated the relocation folding several times),
       the link was about 43% stack maps (runtime-table emission alone 20%,
       its validation 9%), 25% final image preparation, 16% the compact
       view's revalidation, 10% input decode, 9% relocation packing.
       9bb17d907 converts stack-map locations by byte column, writes record
       rows per block and validates block maxima from the packed words
       (link 163.8 -> 148.0 s CPU, five pairs, output byte-identical).
       Against five minutes cold the gap is still about 4x: the link stays
       single-threaded over 15.6M relocations, 9M safepoint records and 28M
       stack-map locations, so it needs per-object preparation moved into
       the codegen workers or fewer records (safepoints at calls that cannot
       reach a collection need a GC-protocol proof first); the workers need
       the allocation model change below and J2's single error landing.
       The typed CFG pass input is an immutable producer-owned record stream
       with one aligned opcode header per event. Stage1 host replay
       (Codex, Linux, 2026-11-26, ab8696fc): retain exact event indices and
       public callback/dense/native paths; in compact CPython owner lists
       find only branch and zero-header event candidates for trampoline,
       indexed cold fold and empty local-label cleanup. Copy retained runs
       in bulk, never return the mutable input root. The private host span
       reader copies validated IDs by list, still checks each ID's range.
       The debug verifier, another owner and external metadata iterables
       retain the original dense checks; native storage keeps the original
       route. Real 625-source compiler closure diagnostic with explicit
       arm64-apple-darwin target froze worker_resource_plan (5.70 MB),
       pipeline_frontend_parallel (8.29 MB) and pipeline_exports (9.99 MB),
       each zero py_cpy_* fallback calls. Five quiet alternating CPU-pinned
       full owned Mach-O emission pairs on actual worker_resource_plan
       (same parsed module IR, not a whole Stage1/direct capture replay):
       backend+object CPU 15.365 -> 14.688 s (-4.41%, 5/5), wall 15.568
       -> 14.891 s (-4.35%, 5/5), unchanged parse control 2.434 -> 2.413 s
       (1.52% median paired absolute drift); normalized owner CPU median
       -4.61%, 5/5. Full replay parse+backend+object CPU 17.778 -> 17.157 s
       (-3.50%), wall 17.982 -> 17.399 s (-3.24%), peak RSS 193840
       -> 187684 KiB (-3.18%, 5/5). Exact same 2,319,208-byte object
       in all ten arms (sha256 d320f3fde7b785009423ca67ab006adcfd8edd6db41f704e209cc6297fb9a1ec).
       The targeted phase walls: trampoline 0.826 -> 0.375 s (-54.6%),
       indexed fold 0.284 -> 0.131 s (-53.9%), empty labels
       0.114 -> 0.071 s (-38.2%), prepare 4.847 -> 4.321 s (-10.85%),
       each 5/5. No whole Stage1 time claim; actual native Mac execution
       not run.
       Exact final candidate contextual fallback count zero for both changed
       backend modules (625-source Darwin target closure); 207 focused host
       tests pass and one native Mac execution test is deselected on Linux
       (the same execution-format failure on base). Second real module
       pipeline_frontend_parallel has exact 9,175,128-byte Mach-O object
       parity (sha256 5f41f79443f533132770104d2390e3370177b5e8e697d76dafd4da71574ec054);
       this one loaded pair is correctness only.
       Staged relocation replay proof candidate (Codex, Linux 2026-11-26,
       base 81af95e8): retain the complete canonical raw-row count fold
       (flags/companions/names before payload or proof admission). Bind
       exact immutable bytes and private canonical table identities; the
       two validation replays then materialize one Relocation at a time
       directly from six scalar fields, without dictionaries or a folded
       256-row batch. Public/injected proof or validator owners retain
       the old route, and original scratch plus 512 bytes is admitted.
       Three actual compiler PCO artifacts from the 625-source Darwin
       compiler closure (pipeline_exports 6,977,832 bytes,
       pipeline_frontend_parallel 11,114,559, worker_resource_plan 3,727,075)
       provide 163,178 merged expanded rows, 4,788 symbols, five sections
       and 5,556,936 bytes of merged payload. Actual staged preparation
       under the existing 2.5 GiB tree guard: identical complete metadata,
       payload and row digest 96969a7563115e60a1ce0bb236d104856e9a9e2d39e537e394fa580d52125863,
       zero retained budget at return. 264 focused tests vs base228 pass;
       263 additional pass on both, same four Mac-only execution failures
       (Linux exec-format); exact cross-target executable parity covered.
       First five quiet CPU-pinned pairs: source-view owner CPU
       0.5665 -> 0.5208 s (-8.07%, 5/5), wall -8.71%, but full
       preparation CPU 1.992 -> 2.024 s (+1.62%, 2/5), wall +3.55%,
       RSS 41,264 -> 43,572 KiB (+5.59%, 1/5). Unchanged input-scan
       control diverged +15.07% (15.63% median paired absolute drift),
       normalized source-view CPU -18.38% (4/5), full -12.73% (3/5);
       insufficient to admit a full preparation gain.
       Prewarmed exact-input quiet A/B/A retest (five alternating triples,
       same-base full-prepare control): full CPU 2.153 -> 2.061 s, control
       2.052; normalized median -1.61% (3/5), control drift 4.06%.
       Wall normalized -1.97% (3/5), drift 4.87%. Peak RSS 41,404 ->
       43,596 KiB, control 41,256, normalized +5.49% (1/5), control drift
       0.02%. Highwater already diverges during unchanged stack-map merge
       before source-view replay; no basis to credit it back. Source-view
       CPU 0.595 -> 0.563, control 0.593, normalized -7.41% (3/5).
       Exact output and budget-retirement parity remain, but global CPU
       benefit not reproduced beyond control and memory not noninferior.
       Rejected: no production code published. Next investigate direct
       validated PCO symbol-row projection/packing in input-detach, avoiding
       per-relocation object and repeated symbol-name resolution while
       preserving the exact final row wire and source error order; measure
       its own actual phase and full preparation against fresh base.
       Actual Linux x64 CI 6cbafc4a Stage1 (artifact 15140867848,
       2026-11-26, four workers): compile 1206.446 s, peak RSS
       2,877,579,264 B, 625 completed modules; coordinator workers
       1096.943 s (91%), exports 62.352 s, link 34.983 s (2.9%).
       Sum of all 625 indexed-native-sections receipts: frontend generation
       923.499 worker-s, backend 2728.814 (function emission 1546.725,
       stack-map planning 640.363, verification 351.200, layout 128.953),
       object assembly 51.127, encoding 11.897. Largest c_codegen
       backend62.35 s (35.98 function,14.21 stack), class_gen58.33
       (32.92,14.49), type_infer48.85 (29.58,10.13). These are inclusive
       original worker wall durations, not inferred CPU totals.
       Codex Linux replay against 9790f3f1, whose planner/kernel/x86 emitter
       and tested module sources match CI 6cbafc4a: exact direct-indexed
       capture from current 625-source closed world, passes off, zero
       fallback, no text render. c_codegen 1039 functions, 95,041 blocks,
       358,843 instructions (CI same blocks, 358,849 instructions);
       class_gen727/86,393/332,713; worker_resource_plan105/13,050/47,696.
       c_codegen CPU sampling (11,572 samples, 2,381 inside planner)
       attributed planner block-entry states457, managed origins454,
       record construction318, liveness276, repeat final frame protocol225;
       frozen value constructors896 (37.6%), stable text hash267 (11.2%).
       Although 725/1039 functions track no managed SSA, they account
       for just 13,564/358,843 instructions (3.8%); skip-empty is not
       the dominant owner.
       Host-only direct x86 frame-protocol facts: reuse each successful
       reachable call's checked enter group or leave key from entry-state
       analysis in final emission, while independently applying duplicate/
       unmatched active-state checks. Registered-root derivation reuses
       only nonempty compiler local groups and resolves the original slots
       again; uncached/empty/registry paths retain canonical validation.
       Exact native arena/public nonexact or replaced helper paths remain
       uncached. First candidate large actual c_codegen planner-only
       five quiet alternating triples (same verified/layout preparation
       as the indexed streamed owner, same-base complete planner control):
       CPU 9.988 -> 9.074 s (-9.15%, 5/5), control10.159,
       normalized median -11.51%, 5/5, control drift3.38%;
       wall10.035 -> 9.128, normalized -11.28%, 5/5;
       RSS324,292 -> 323,564 KiB, control324,300, 5/5 lower.
       All 15 arms have identical 205,755-record plan digest
       4e023b53c79c55047906ff71c6bd6d460347077f3d8db3417f14e0ee976766bb.
       Decode control normalized +4.46% within6.10% drift, preparation
       +0.91%. This isolated planner result is not a full module or
       Stage1 timing.
       Final hardened source keeps the cleanup wrapper host-only (native
       arena path outside the exception handler), rejects nonexact global
       initializers, and clears the private facts even with a saved error
       traceback. 239 focused tests pass vs233 base (including exact
       streamed object, active duplicate/leave errors, missing-fact
       validation and retirement; the older frozen global-index oracle
       keeps its original SHA). Final actual changed planner direct-indexed
       capture in 625-source compiler closure: 625 functions, 57,034 blocks,
       229,371 instructions, zero fallback and no py_cpy_* edge. Final
       c_codegen full streamed ELF exact 13,508,968 bytes
       (sha256 04d4276f7a85b5e7d3c1fe76a60967ba1d2af582355c2419f0c2a2ca74e3e584)
       and exact 205,755-record plan digest in a loaded correctness replay.
       Final exact hardened source five quiet alternating full streamed
       worker_resource_plan triples: backend+object CPU5.697 -> 5.538 s
       (-2.79%,4/5), same-base control5.744; normalized -4.217%4/5,
       control drift1.577%. Including indexed codec decode, CPU6.172 ->
       6.021 s, control6.192, normalized -3.954%4/5; wall normalized
       -4.072%4/5; peak RSS81,084 -> 81,076 KiB, control81,388,
       normalized -0.152%5/5. Actual stack-map phase wall1.108 -> 0.956
       s (-13.69%,5/5), control1.159, normalized -15.466%5/5.
       All15 full streamed ELF objects byte-identical (1,739,192 bytes,
       sha256 11b507bf67fd12674abef4d977a093bc7261cd6e58b052a695727383f77eac63).
       No whole Stage1 bootstrap timing or native compiler speed claim.
       Register-copy sparse active state (Codex, Linux, 2026-11-26, base
       ca08b6b9): the actual c_codegen x64 function emission profile found
       562 samples in copy forwarding and 156 in instruction effects.
       Replace repeated 16-register copy/immediate invalidation and
       address-presence scans with live bitmasks: clear written destinations
       and constants in a mask operation, examine only live source
       dependencies, never consult a stale array row. Preserve partial
       writes, label/unknown-instruction barriers and address/index rules.
       Current strict contextual 625-source direct indexed capture
       (no fallback or py_cpy edge) c_codegen 1,039 functions, 95,041
       blocks, 358,843 instructions, worker_resource_plan 105/13,050/47,696.
       Capture actual base streamed function input: 1,632,673 assembly
       lines across 1,039 functions, 1,519,962 output lines. Five quiet
       base/candidate/same-base control triples (alternating order) replay
       identical inputs: owner CPU 5.799 -> 5.318 s (-8.294%,5/5), control
       7.056; normalized -10.913%5/5, control drift5.591%; normalized wall
       -10.339%5/5. Replay peak RSS174,448 -> 174,624 KiB, control174,448,
       normalized +0.079% (tiny replay variation, no memory win claimed).
       All15 owner output digests exact (sha256
       ba54e21e1ab6b7d926c1e85dd3792cc755ea1acbe068515974dfd7c524025a81).
       Actual full c_codegen ELF exact 13,508,968 bytes, sha256
       8b6b5c7627edd2d289dd507d7d6522cabc7aaf26f421bf184c08a962cbba2277.
       Five quiet full streamed worker_resource_plan triples: backend+object
       CPU5.992 -> 5.971 s (-0.364%,4/5), control5.909, normalized -0.827%
       3/5 within control drift1.692%; full CPU normalized -1.321%3/5,
       full wall -0.676%3/5; peak RSS81,024 -> 80,756 KiB, control81,052,
       normalized -0.039%3/5 within control drift0.650%. All15 objects
       exact (1,739,176 bytes, sha256
       42d60bb810624c8f1b6ac655d6a554ee98a09c32b709acebeb5c974a1b2b58cc).
       Full worker neutral, not a whole Stage1 or native-compiler speed
       claim. 387 focused tests including five added state-expiry cases,
       plus 3,000 scratch seeded exact comparisons against the old two
       forwarding entrypoints. Final changed source strict 625-source
       direct capture 40 functions/6,950 blocks/25,329 instructions, zero
       fallback/no libpython edge, native ELF emitted (873,432 bytes,
       sha256 6a58aa90d0abcb2df299967fda68736ba8af312271e0883df560b6e4de06aee0).
       Sparse x64 block ownership (Codex, Linux, 2026-11-26, base
       f0b17ed3): replace frame-promotion binary searches for every
       access/branch and dense loop depth line sweep with exact block-start
       coordinate maps, sorted loop-boundary events and a monotone access
       cursor. Remove dead-move liveness's per-line block-owner array:
       every terminator precedes the next block start (or EOF), and labels
       are starts (frame prefix labels map to entry). Retire new private
       maps and events before liveness allocations. Preserve overlapping
       backedges, duplicate/adjacent labels, partial pointer retirements,
       unknown destinations and existing ordering. Current 625-source
       strict direct contextual snapshot (zero fallback/no py_cpy edge):
       c_codegen and worker_resource_plan indexed input hashes unchanged
       from prior unit. Actual streamed c_codegen base capture across
       1,039 functions: frame input 1,732,758 lines -> 1,696,653, copy
       input 1,632,673 -> 1,519,962. Five quiet alternating identical
       input replay triples plus same-base control: combined owner CPU
       9.268 -> 8.739 s (-5.708%,5/5), control9.505, normalized -6.587%
       5/5 vs control drift3.208%; wall normalized -7.933%5/5.
       Frame alone CPU4.221 -> 3.665 s (-13.166%,5/5), control4.339,
       normalized -13.782%5/5 vs drift4.258%; copy owner alone neutral
       (normalized -0.464%4/5 within drift3.480%). Replay peak RSS
       63,720 -> 63,656 KiB, control63,736, normalized -0.129%4/5.
       All15 exact line digests for both owners (frame sha256
       578e41d42737077195833624461f4892ad01b4c1b000e795be48c401585fcfb6;
       copy ba54e21e1ab6b7d926c1e85dd3792cc755ea1acbe068515974dfd7c524025a81).
       Actual large c_codegen streamed ELF byte-identical (13,508,968 bytes,
       sha256 8b6b5c7627edd2d289dd507d7d6522cabc7aaf26f421bf184c08a962cbba2277).
       Five quiet full streamed worker_resource_plan triples: backend+obj
       CPU5.830 -> 5.532 s (-5.112%,5/5), control5.627, normalized -1.975%
       5/5 within control drift3.823%; full CPU normalized -2.832%5/5,
       wall -3.372%5/5; peak RSS81,156 -> 81,100 KiB, control81,360,
       normalized -0.212%4/5. All15 ELF objects exact (1,739,176 bytes,
       sha256 42d60bb810624c8f1b6ac655d6a554ee98a09c32b709acebeb5c974a1b2b58cc).
       Full worker directional improvement within drift, not a whole
       Stage1 or native-compiler speed claim. 389 focused tests pass,
       including dense-reference sparse labels/terminators and overlapping
       prefix/backedges with pointer-lifetime checks. Exact changed source
       625-closure direct capture frame43 functions/8,475 blocks/30,178
       instructions and copy40/7,078/25,918, zero fallback/no libpython;
       actual native ELF objects emitted: frame1,054,088 bytes sha256
       978a5e7d045ed7555baaff841499fc5795d4c0caba9def75a2376e0253b14ae4,
       copy893,824 bytes sha256
       543f5edee108d37661c050a68c974a2457075a71b2625f2875808729d6ca4298.
       Next (latest actual Linux CI codegen priority): (1) the remaining
       1,546.7 worker-s function-emission owner, particularly x64 packed
       stack-map emission routes currently recreating stable text hash
       buckets for every block instead of using the existing integer route
       spans (AArch64 indexed owner already uses these); (2) J2 (e) one error
       landing per function after registering temporaries once, measured
       against the actual CI block inventory; (3) the allocation model
       toward LLVM parity, then remove passes that no longer fire;
       (4) remaining dense loop NOP/pair scans and no-rewrite publication
       preserving public snapshot contracts. Per-object link preparation
       remains useful on the measured Darwin tail, but is only 2.9% of
       this Linux Stage1 report and is not the next Linux priority.
   J3. TaskScope (5x CPython), `time.perf_counter` (7x), `str.encode` (5x).
   A1. Darwin thread-local access inline (TSD slot through `tpidrro_el0`,
       startup check, fallback to the call): the resolver is 0.65 us per
       request, the inline read is not free. Approved by the user on
       2026-11-26 (answer to a question naming this design). Starts after
       the Stage2 TLV resolver-save unit (Codex, backend) releases its claim:
       the inline fast path changes the call sequence that unit's
       `self_backend_aarch64_typed_tlv.py` matches.
   B1. On an idle host, why a w4 request costs about 50% more processor
       time than a w1 request. B2. Scheduler: the global ready-node pool
       lock and the shared counters become per-carrier caches and batched
       Measured 2026-11-26 (Claude, local Mac, arm qps-ea1ea73 at ea1ea73f5,
       load 5-9 from other sessions): table w1 62959 req/s (72% of asyncio),
       w4 151474 (58%); per request w1 236856 instructions and 53477 cycles,
       w4 260014 (+9.8%) and 94934 (+77.5%): w4's excess is mostly the same
       instructions running slower, not spinning. prim w4 scaling: instance
       0.82, sleep 0.64, clock 1.39, spawn 1.96, scope 1.98, raise 2.00,
       request 2.58, json 3.15; loop/call/method/tuple 3.8-3.96. Ceiling
       experiments (screens, not merged): an instance that does not count
       its class makes `instance` w4 -76.5% but `request` unchanged (the
       handler creates few user instances); static definitions and classes
       made immortal moved w4 cycles -2.35%, inside the noise. By sampled
       time the scheduler lock, its spinning and the ready-node pool are
       about 6% of a w4 request: `pcc_mutex_lock` from join, join_result,
       sleep, run_ready_thread and complete (trylock 0.63 us, swtch_pri
       0.28, safepoint 0.33), `_ready_alloc` 0.37 and `_ready_node_take`'s
       queue lock 0.33. At w1 json is 5.94 us of a 16.3 us request: about
       20% allocation and free, container locks 5.8%, store_root 5%,
       `_tlv_get_addr` 3.6%.
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
   Public virtual-thread TCP, local work based on `ca5d198e6`: sequential
   operations in an inline generator reuse its traced argument slots and
   three private integer words. On the Mac, six alternating trials with a
   separately built control measured 1.73x throughput for one-byte reads
   of buffered loopback data (control floor 2.1%). A 16-byte echo exchange
   used 6.2% fewer instructions; its throughput fell within control noise.
   The gateway transport calls the platform socket operations directly.
   Its request rate is a separate measurement. Focused host/self replay:
   `uv run pytest -x -vv tests/python/test_virtual_thread_network.py::test_sequential_tcp_gc_matrix`
   (one and four carriers). Native pcc1 qualification remains open.
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

   Closed-world immediate factory handoff (exact source 7763efd4; Linux,
   2026-11-26): diagnostic measured 4.05 completed result wrappers/request,
   all immediately consumed; net wrapper primitive 283–325 ns. Narrow
   generic compiler proof tested direct lexical module-qualified factory,
   explicit private discriminator, public dynamic ABI, ordinary generator
   value and exception semantics. Both candidate iterations are REJECTED,
   not production changes. Final single-body thin public wrapper+direct
   root take reduced RX growth from 14,240 to 6,272 bytes; RW/TLS unchanged.
   15 host tests, 24 scoped native tests incl finalization and four-carrier
   shared-container races, native audits w1/w4, 625-source contextual six
   modules all fallback zero. Exact 5 repeats allocations/request
   38.45 -> 34.40 (w1/w4). Final C100 * 1000, alternating 5 triples
   (identical baseline control, CPU8 w1 / CPUs4–8 w4):
   w1 QPS 18,790.83 -> 19,802.68, CPU 53.8534 -> 51.1004 us/request,
   5/5 each; paired normalized QPS +4.038%, CPU -3.443% vs max control
   floor 2.242%/2.932%, but peak RSS 27,348,992 -> 27,471,872 B
   (normalized +0.434% vs max control 0.240%). w4 QPS 40,469.22 ->
   41,220.78, CPU 91.5529 -> 89.6980 us, 5/5 raw; paired normalized
   +1.612%/-2.079% within control floors 11.557%/11.318%, inconclusive.
   Peak RSS 31,875,072 -> 31,981,568 B; max STW median w1 2140 ->
   2428 us, w4 1935 -> 1962 us, both within max unchanged control.
   First duplicate-body candidate also had peak RSS regression, with
   smaps attribution primarily file-mapped pages, not anonymous heap.
   Final exact binary SHA 8f7be47324951252a66a54b21af68f6ce4c7b98186ab737a24dc4c5c247ccea4;
   baseline SHA 135ed295823122b28d04ae87395da63e7e3b2a02cd47fa3f6383f1765d78f504.
   Keep public protocol unchanged; don't retry the same factory patch.
   Next: measure allocation count and cost attributable to ordinary
   list/tuple/iterator ownership, prove a generic non-escaping candidate
   before attempting removal. No benchmark-specific shortcuts.

   Claims:
   - Stage1 x64 direct packed integer stack-map emission routes: Codex, Linux, 2026-11-26T18:51Z, area backend-x64-emit-routes
   - Ordinary list/tuple/iterator ownership allocation attribution and generic non-escape proof: Codex, Linux, 2026-11-26T18:31Z, area runtime-codegen-allocation
   - Runtime QPS through the self AArch64 backend: A1 (Darwin TLV inline, approved), then code generation toward LLVM parity (allocation model); the user, 2026-11-26, chose to proceed although Codex's backend claim covers the area; it avoids files that claim's commits are changing and integrates on rebase: Claude, macOS, 2026-11-26T14:08Z, area backend
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
   items, would apply to mutable Go and Java objects. #13 first independent
   runtime geometry slice: header/descriptor contract now fixes the future
   object descriptor@16, repeat count@24, fields@32 with unchanged existing
   header; immutable compiler descriptor records prefix extent, element
   stride, and sorted reference slots (fixed and repeated). Checked raw
   extent/count/ordinal helpers are in production module source, not an
   allocation/tag or frontend admission. Native x86 C-to-owned runtime
   ELF probe returned 42 with empty streams, zero external symbols; owned
   AArch64 Linux, Darwin ARM64, Windows x64 object emissions passed.
   40 scoped tests passed under unchanged 90s/1GiB guard (2.21s, 96,915,456
   B peak). No whole archive/current native collector or QPS certification.
   The [shared storage design](design/go-shared-managed-storage.md#first-geometry-slice-checked-fixed-and-repeated-slots-not-a-tag-admission)
   records allocation/extent, four refcount tag checks, bounded visitor,
   both dealloc dispatchers, sweep/clear, young/full tracking and owning
   root handoff. Second independent runtime slice: private initializer
   binds validated immutable metadata to the exact allocation request,
   rejecting mismatches and second initialization without mutation.
   Bounded precise visitor visits only selected offsets/role=1 (constant
   metadata work plus one table read per slot) and resumes on the common
   state cursor. Raw native mixed backing fixture visited seven owned
   addresses in four calls, cleared only those and preserved four actual
   pointer-shaped scalar words, header/metadata, and zero-capacity case.
   48 scoped tests passed, guarded 3.02s/100,052,992 B; same self-owned
   cross-target exports. No managed tag, registered owner, actual collect,
   root or concurrency claim. The existing pcc_gc_alloc publishes the
   type tag/cell before returning, so private geometry must be initialized
   before that publication, not by an unqualified after-return store.
   Third atomic managed-lifecycle slice now admits internal tag 35 only
   through dedicated constructor into a trusted registered empty root.
   It restricts first admission to object-family size classes <=16,384
   (no raw-mapping publication shortcut), zeroes RESERVED cells and binds
   validated geometry/header before release-CAS LIVE; transfers initial
   count to root before no-park exit; reloads rooted owner before tracking.
   All four refcount checks, bounded visitor, ordinary/cycle dealloc,
   terminal flags and shared sweep/young/full walker consume one layout.
   Linux current-source self-owned 193-member archive built 149.89s/
   282,763,264 B peak and strictly readmitted after two exact runtime
   member rebuilds (unchanged compiler checksum). Native C witness exit
   42/empty streams +10 repetitions: actual scheduler roots, +105/-105
   allocator requested bytes, pointer-free/mixed references, exact counts,
   scalar pointer-shaped bits, terminal retirement, rooted cycles,
   explicit/young/bounded full, two registered carriers contending on
   field and 100 allocations by worker across concurrent collection.
   Existing native candidate/resurrection, pending zero-count roots and
   ordinary list cycle agree; final combined guard 9.79s/172,388,352 B.
   Ten source ABI checks pass; stale deleted relocation-module numeric
   domain audit is strengthened against four actual slot-role users.
   This is a bounded Linux internal lifecycle gate, not cross-platform
   native, whole-suite, Python QPS A/B, general backing or stable collector
   certification. Fourth atomic raw-mapping lifecycle slice now uses
   allocator raw extent above 16 KiB with private zero/init outside
   no-park; exact managed index insertion + root transfer share the
   graph-lock transaction BEFORE unlock/park. Failure frees only an
   unregistered private mapping; duplicate fresh key fails closed.
   Small-cell path and ordinary Python allocator remain unchanged.
   Same compiler checksum; actual py_obj/py_gc_backend modules rebuilt,
   strict exact 193-member archive readmitted. Native large scalar
   20,032-byte requested debit/credit and one index key, fixed 20,000
   bytes, mixed repeated 24,048 bytes/2,001 precise edges and bounded
   tail cursor; rooted raw/raw cycle with small child, scalar pointer
   bits ignored, terminal/explicit/young/bounded full free, 100 raw
   allocations on second registered carrier during collection. Small
   and raw native each exit42/empty streams +10 repeats, 3 existing
   native collector controls pass; guard 17.77s/182,132,736 B. Eight
   source lifecycle models and exact four live graph-lock callsite
   classification passed. No cross-platform native or pause/QPS claim.
   Final small/raw cross-target object ABI: actual six production runtime
   modules and C caller compiled self on AArch64 Linux, Darwin ARM64,
   Windows x64 MSVC; all expected exact exported signatures, 64-bit C
   arguments/results (LLP64 included), and unique constructor/visitor/
   dealloc cross-object relocation owners. 3 focused checks passed under
   90s/1GiB guard (33.91s,130,854,912 B). Native execution there UNRUN.
   Actual exclusive Linux QPS/CPU/RSS A/B frozen pre-tag 7763efd4 vs
   combined lifecycle 534b4e9e, same gateway b1490486 and handler bytes;
   five alternating base/candidate/unchanged-base-control triples each
   w1/w4, 100,000 timed requests per arm. w1 median QPS 19,336→19,016
   (-1.66%, 1/5), control 19,033, paired noise 1.69%; CPU 52.002→52.888
   us/req (+1.70%), control 52.685, noise 2.08%: within noise. w4 QPS
   39,205→37,642 (-3.99%, 2/5), control 37,420, noise 7.72%; CPU
   95.073→99.958 (+5.14%), control 100.180, noise 9.25%: inconclusive.
   RSS medians all 42,332,160 B; allocation medians exactly equal;
   max-STW median w1 2081→1999 us/control1938, w4 1804→1916/control2028.
   No clear penalty relative to control, but w4 performance non-regression
   is NOT established; no collector-interface stability or frontend
   admission. See shared storage design for frozen hashes, receipt and
   control details. Next: quiet stable repeat + required platform native
   and macOS A/B before generic Go/Java lowering or unchanged upstream
   acceptance. Independent same-source ordinary float allocate/read/release
   primitive 4M iterations x15, exact live requested bytes unchanged and
   checksum7M, CPU212.284→214.385 ns/control218.946 (+0.99%,2/5),
   wall212.529→218.441/control226.004 (+2.78%,2/5); maximum control
   floors7.87%/8.73%, inconclusive. Independent exclusive longer w4
   C100x2000 five alternating triples same handler/affinity, QPS
   39,056.756→40,261.474/control39,790.891 (+3.08%,2/5), CPU
   93.3445→91.5924/control92.7252 us (-1.88%,2/5); allocation medians
   7,642,847 each; RSS41,463,808→41,512,960/control41,504,768 B;
   STW1952→1996/control1966 us. Median control deviations1.88%/0.66%
   but formal max floor8.30%/6.28% exceeds 5%, still inconclusive.
   Earlier w1 max floors3.52%/3.76% within noise; w4 no release.
   Both receipts preserved in shared storage design. No language admission.
   C++
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
   NumPy C++ source first-blocker unit: an owned C++17 `<assert.h>` and
   `<cassert>` now rebind on every inclusion to the current NDEBUG state.
   Active assertion is contextual-bool conditional evaluation, with a
   source expression/file/line/enclosing function diagnostic on stderr
   followed by the existing target C write/abort ABI, not fake-libc's
   no-op. Source templates retain failure metadata; unrelated arbitrary
   noexcept/noreturn calls remain rejected. Focused Linux host: 238 tests
   passed, including x86-64/AArch64 Linux and Darwin AArch64 object emission.
   Three real x86-64 ELF runs using a bounded runtime archive whose
   write/abort source and object SHA receipts matched the current two
   owners passed: successful side effect once, NDEBUG no evaluation and
   reinclusion, and failed template emits expected diagnostic and SIGABRT.
   G++ 14.2 independently confirms those outcomes (format differs by
   implementation). Strict current-whole-runtime fixture added, hosted
   execution pending; active Windows assertion and other unimplemented
   C++ expression capabilities remain explicit frontiers. Actual unchanged
   NumPy 2.4.4 halffloat.cpp (cef1d814...cd26) moves from missing assert.h
   to missing stdlib.h, not an emitted object or NumPy build. Next NumPy
   C++ follow-up: owned `<stdlib.h>` acquires the same `<cstdlib>`
   declarations and explicitly imports its std-only callback, abs and div
   overload sets into global scope, retaining canonical symbol identities,
   language linkage and unsupported value/function/locale contracts. Both
   inclusion orders and repeated header control pass. The wide `::abs(long)`
   emits its genuine i64 std overload relocation, not a narrowed C `abs` or
   fake implementation. 84 focused host tests passed. Real Linux ELF
   `::abs(-::atoi("25"))+std::atoi("17")` returned 42 with empty streams
   using a bounded owned runtime archive with current `atoi` source/object
   hashes; independent G++ 14.2 agrees. Strict whole-current-runtime fixture
   is added, hosted execution pending. Unchanged NumPy halffloat.cpp now
   passes stdlib.h acquisition and stops on string.h (same original SHA).
   C++ follow-up: owned `<string.h>` now selects the existing `<cstring>`
   canonical declarations and exact global/std search overload identities;
   the former explicit missing-header test now verifies both include orders,
   repeated inclusion, no fake-libc fallback, and const/linkage identity.
   The mutable search still emits its real unresolved C++ provider, not
   a const-erasure or substitute C call. 63 focused host tests passed.
   Actual Linux ELF const `strchr`, `memcmp`, and `strlen` returned 42
   with empty streams using a bounded owned runtime archive whose current
   `freestanding_mem_str` source/object hashes matched; independent G++
   14.2 gives the same result. Strict complete runtime fixture added,
   hosted execution pending. Unchanged NumPy halffloat.cpp now advances
   from missing string.h to missing errno.h (same original SHA), not an
   emitted object. C++ follow-up: owned `<errno.h>` selects `<cerrno>`'s
   exact target error-number and location-lvalue ABI. Full macro inventory,
   both include orders and repeated inclusion match; Linux x86-64/AArch64
   and Darwin project only their correct C accessor, and Windows still
   fails explicitly rather than borrowing Linux numbers. 90 focused host
   tests passed. Actual Linux ELF sets errno through its address and then
   observes the same slot after a real overflowing strtol returns LONG_MAX
   and publishes ERANGE; result 42 and empty streams match G++ 14.2. The
   bounded owned archive's libc/errno/strtoint source and object receipts
   match current owners. Strict complete runtime fixture added; hosted
   execution pending. Unchanged NumPy halffloat.cpp now stops at missing
   limits.h, not an emitted object. C++ follow-up: owned `<limits.h>`
   now shares `<climits>`' exact target integer limits and promoted types.
   Full constant/overload checks on Linux x86-64/AArch64, Darwin AArch64,
   and both Windows LLP64 targets preserve platform char signedness and
   widths in both inclusion orders/repeated inclusion, without fake C
   typedefs or unrelated macros. `MB_LEN_MAX` remains the existing
   compile-time capacity, not a locale/runtime qualification. 113 focused
   host tests passed; actual self-emitted/static-owned-linked x86 ELF
   overload/limit program returned 42 with empty streams, matching G++
   14.2 and byte-identical IR/object to the existing `<climits>` spelling.
   Unchanged NumPy halffloat.cpp now stops at missing ctype.h (same SHA),
   still no emitted object. Owned C++ <ctype.h> now shares the exact
   <cctype> C-linkage int(int) declarations; ten target/include-order checks
   across Linux x86-64/AArch64, Darwin AArch64 and Windows LLP64, all 14
   identical external-call IR/object artifacts, and no fake macro/provider.
   65 focused host source tests passed, native static owned-linked x86 ELF
   no-call program passed. Existing owned toupper (fixed C locale) matched
   G++ 14.2 for all valid int arguments -1..255 through both global/std
   spellings, exit 42 and empty streams; selected archive source/object
   hashes matched current runtime owner, strict full archive fixture added.
   Other classification/locale runtime providers are not qualified.
   Unchanged NumPy halffloat.cpp (same SHA) now reaches missing generated
   _numpyconfig.h, the shared C/C++ prerequisite, no object emitted.
   Next NumPy: actual generated configuration; no fabricated inputs or
   production acceptance. The new 45856871699 Java and Go receipts reproduce the
   existing strict source frontiers: Java reference 15140386201 204/4 and
   integration 15139682668 1255/4, Go integration 15141110841 878/2;
   no oracle waiver or project-specific substitution.
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
   Linux first for NumPy, any for the value model. NumPy generated-input
   frontier (after item 4 ctype.h): unmodified NumPy _core/meson.build lines
   550–564 owns config.h and _numpyconfig.h through stock configure_file;
   cdata is determined by actual target sizeof (including complex, wchar_t,
   off_t and Python C-ABI types), mandatory math/complex/stdlib symbol
   probes, long-double object-format, endian, visibility, selected threading
   and source ABI/API versions. No static guessed header qualifies.
   The host-only PCC Meson adapter explicitly has C/C++/ar providers only;
   stock NumPy project declares Cython >=3.0.6 before these probes.
   The adapter also rejects implicit named-library discovery (-lm), debug
   mode and Ninja dependency generation. Native package build declares
   OWNED-CYTHON-UNAVAILABLE and OWNED-MESON-GRAPH-REQUIRED; Cython is not
   vendored or installed on this probe host. The existing CI NumPy lane is
   explicitly host-PCC source-only, with unchanged C/C++ TUs and no native
   Stage1 dependency, and must remain red on missing generated inputs.
   Next independent native work: source-pin and qualify Cython and Meson
   execution using the admitted owned compiler, bind real Python C-API and
   native math/ABI checks, and consume the generated out-of-source header
   overlay with authenticated provenance. Do not insert untracked headers
   into the locked source or convert the existing host failure to UNRUN.

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
   pointer-retirement register-home witness was subsequently repaired by
   `698937ea`. The AArch64 typed adjacent-memory pairing pass now rejects
   non-transfer first opcodes before probing the next event. On Linux host
   CPython 3.13, actual `worker_resource_plan.py` compiled to AArch64 IR
   (SHA256 `adff74021697f54cdd66549de703c9729a2bed4520837ebefda5f572f318d57b`),
   indexed transport supplied 202,882 pre-pair events. Five alternating
   same-input CPU pairs: median 0.531 -> 0.305 s (-42.6%, 5/5 wins),
   unchanged disabled-pair control 0.173 -> 0.170 s; output record fields,
   opcodes, operand tokens and encoded words identical, sections assembled.
   144 focused typed pair/CFG/pipeline nodes pass (host, six workers,
   standalone pytest); the new work-count test fails against the old pass.
   This is a cross-target pass-level measurement, not native ARM, Stage2,
   or whole-language acceptance. CI qualification pending. Next: the trampoline pass now stops scanning a local
   block after the second nonempty event and collects the label index in
   its first pass, preserving all branch-distance and metadata checks.
   On the same frozen `worker_resource_plan.py` IR, actual indexed emitter
   input 203,963 events: five alternating CPU pairs, median 0.244 -> 0.171 s
   (-29.6%, 5/5; first baseline arm had a cold decode cache); unchanged
   fallthrough control 0.0568 -> 0.0558 s. Exact output record fields,
   opcodes, operands and encoded words agree, sections assemble. 145 host
   focused typed pair/CFG/pipeline nodes passed on six workers. The new
   count witness reduces nontrivial-block body probes 1001 -> 3; it fails
   on the old pass. Cross-target pass-only evidence, not native ARM or
   full Stage2; CI pending. Empty-local-label pass screen (Linux, same frozen actual 203,088-event
   compiler module): candidate compacting label-only ranges was 0.06685 ->
   0.07321 s (0/5); a cached-record variant 0.06947 -> 0.06696 s (2/5),
   controls stable and exact output equal. Neither candidate is admitted;
   no production change. TLV resolver-save screening now constructs invariant operand lists once
   per module probe/function scan, not per record; exact match and liveness
   semantics unchanged. Linux host CPython 3.13, real indexed
   `worker_resource_plan.py` AArch64 Linux input 202,882 events: five
   alternating pairs CPU median 0.04734 -> 0.03214 s (-32.1%, 5/5), control
   0.05641 -> 0.05684 s. Same source Darwin-target IR SHA256
   `3988b84c88373bcb45b47b9e344277aa0a3169ad3e1e6561cc9df2aca2959cfa`,
   295,768 events: 0.06752 -> 0.05574 s (4/5; control 0.09129 -> 0.10218 s,
   noisy, no native Mac claim). Both exact output records match and assemble;
   615 focused typed tests pass, changed module emits x86 Linux IR. Work-count
   witness reduces 1000 invariant operand constructions to 1. Pass-only
   host cross-target gains, whole-stage CI pending. Current source `bc8f04f6` host cross-target indexed
   worker_resource_plan IR SHA256
   `c1b90e8aea46568b070c7957789825c6fb7d7c8fe74b7afd356d633df521c36e`:
   parse 2.51 CPU s, emit 14.91 CPU s; inclusive prepare 5.10 wall s,
   function emit 5.67 wall s; largest typed children trampoline 0.898,
   publish 0.660, adjacent store/load forward 0.573, validation 0.489,
   memory pairing 0.412 s. No additive wall/CPU or stage-speed claim.
   A cached-opcode prefilter was rejected (2/5; control drift). The shared
   immutable context can read only the family lane to reject non-labels
   before decoding full records; debug/foreign context retains checks.
   Actual 203,963 pre-trampoline events, restore all four decode-cache
   snapshots before every arm (35,880 preexisting opcode entries; other
   three empty): 0.72547 -> 0.61191 CPU s (-15.7%, 5/5), control 0.05689 ->
   0.05479 s. Exact record/word/token outputs agree; assembled. 616
   focused typed tests passed; old implementation fails new work-count
   witness; changed module emits x86 Linux IR. Whole-stage CI pending.
   Typed publish screen on actual 200,634 post-pass records from the same
   frozen current compiler IR, fresh builder/identical metadata per arm:
   caching ordinary relocation symbol validation 0.52782 -> 0.53579 CPU s
   (3/5 wins; copy control noisy); scalar-lane fast path 0.50127 -> 0.48655 s
   (4/5; copy control also faster). Exact sections, symbols and counters
   agreed but neither candidate has compelling normalized benefit; neither
   is admitted. Next: higher-share instruction selection and stack-map/link
   owners, equal-ceiling Stage2/3 CI. This status/release commit intentionally
   triggers current master validation of the accumulated reviewed changes;
   no new production change in this unit, no CI acceptance implied.
   Stage1 Mach-O host runtime-table validation: partial location blocks now
   compute the same signed packed maximum with bounded byte slices, rather
   than one scalar Python decode per root, preserving one visit per word.
   Full-block low-byte scratch is also bounded per block. Linux host CPython
   3.13 cross-target real Darwin worker_resource_plan IR SHA256
   `3988b84c88373bcb45b47b9e344277aa0a3169ad3e1e6561cc9df2aca2959cfa`
   emitted a version 3 table SHA256
   `1d4442207b0e04e2381381ba36e171dd89b970d66079a462b066b92d32f90865`
   with 105 functions, 19,194 safepoints, 1,314 live sets, 37,423 locations.
   Five alternating CPU pairs, 15 validations per arm: median 0.02342 ->
   0.01349 s (-42.4%, 5/5); unchanged generic validator 0.01958 -> 0.01999 s.
   Exact summary bytes and remaining work counter agree. 170 focused host
   runtime/stack-map validation and spool tests pass. This is an owner-level
   cross-target measurement, not native macOS, full link or Stage1 timing.
   Next link slice: measure per-object prep moved to the codegen workers;
   confirm full-stage timing and Stage2 resource parity in CI.
   [Phase investigation](investigations/stage2-worker-budget-parity-45643044565.md).

   Stage2 coordinator indexed export writer: the seven metadata rows now
   convert and write one wire value at a time instead of retaining every
   converted tree through all module writes. Host CPython 3.12.14, real closed-
   world export class surface from 668 compiler frontend/backend/IR modules,
   825 common types and 379 sensitive roots, final index 1,193 descriptors:
   five alternating fresh-process pairs, peak RSS median 92,636 -> 79,188 KiB
   (-14.5%, 5/5), equal pre-write RSS about 41,780 KiB; unchanged legacy
   writer control 157,784 KiB median. All 29,513,459-byte indexed wires had
   SHA256 8d3dfa41075b17d52016af866f8288fc28fb70297c4c4bcbb0e4b6a2813e0705.
   CPU median 0.994 -> 0.960 s (2/5 wins), no speed claim. 39 host wire
   contracts pass; a new weakref lifetime witness fails the old eager batch;
   changed module emits owned x86 Linux IR. This measures only the indexed
   writer on a real compiler class projection, not whole Stage2 RSS or native
   timing. Root-rebuild profile: 0.508/0.803 s inside class reconstruction
   over 16 selected roots, encoding 0.206 s. Dependency-set, singleton-name
   table, static-schema cache and fused delta key-scan candidates did not
   have compelling controlled CPU gains; none admitted. Next: equal-resource
   hosted Stage2 terminal receipts and eliminate repeated class reconstruction
   only with a provably unchanged root-dependent binding/order witness.

   Stage2 native indexed export reader now retains complete module/preload
   rows and slices only the selected closure's payloads and its root delta.
   Header/inventory validation and other raw-payload users keep their original
   contract. The offset-tuple variant saved RSS but lost CPU (warm 0/5);
   rejected. Intact strings avoid that cost. Real 29,513,459-byte indexed wire
   from the preceding fixture, host CPython 3.13.5, five alternating fresh
   pairs per root: worker_resource_plan (1 selected module) peak 181,296 ->
   159,024 KiB, class_gen (10) 181,420 -> 159,144 KiB, AArch64 backend (16)
   181,428 -> 159,148 KiB; each 5/5, equal initial RSS about 82,068 KiB.
   CPU medians 0.744 -> 0.746 (2/5), 0.880 -> 0.849 (5/5), 0.938 -> 0.893
   (4/5) seconds. Aggregate of the three roots 2.654 -> 2.460 (4/5); independent
   same-process aggregate 1.666 -> 1.552 (4/5, unchanged raw-index control
   0.0267 -> 0.0279 s). Fresh raw-index control peak 110,640 KiB. Exact
   semantic wire output hashes agree in every root/arm; 234 host contracts
   pass, 2 native integration nodes not run. New payload-slice-count witness
   fails old (unrelated module and all preload payloads copied); changed
   reader emits owned x86 Linux IR. No whole-stage/native timing claim.
   Windows run 45844665220 owner artifact 15134536420: Stage2/3 owner timeout
   1800.174 s (not RSS limit), whole-owner peak 2,792,398,848 B. Stage1 step
   duration 1283 s; wall parity remains false. No exact Stage2 phase attribution
   until full diagnostics arrive. Next: descriptor DAG transport design:
   actual T row 5,046,730 bytes, 350,795 nested tuple occurrences but 5,693
   distinct tuples. Intern immutable descriptor nodes in a versioned
   postorder transport, preserving old-wire reading, malformed-data rejection,
   type equality and exact root binding/order. Do not adopt without
   controlled reader/writer CPU and RSS evidence.

   Correction before descriptor DAG work: the streaming metadata writer had
   opened the destination before metadata conversion completed, violating the
   prior destination-preservation contract (42 failure-path regressions).
   It now converts/render rows separately into a list of strings before
   opening, releases each wire tree immediately, and clears rendered lines
   after writing metadata; original metadata-failure boundary restored.
   Against original eager wire trees on the same frozen 29,513,459-byte
   indexed-wire fixture, host CPython 3.13.5, five alternating fresh-process
   pairs: peak RSS median 90,544 -> 80,088 KiB (-11.5%, 5/5); exact SHA256
   8d3dfa41075b17d52016af866f8288fc28fb70297c4c4bcbb0e4b6a2813e0705.
   CPU median 1.067 -> 0.779 s (4/5), noisy legacy writer control, no speed
   claim. All 121 host tests across indexed-owned-wire and pipeline-export
   contracts pass (one native integration not run), including 42 failure
   witnesses and prior tree-lifetime / closure-slice witnesses; changed
   writer emits owned x86 Linux IR. This correction supersedes the earlier
   streaming writer's RSS receipt. DAG transport remains Next.

   Versioned immutable preload tuple DAG now published: indexed.v2 writes
   only proven immutable descriptor tuple/scalar graphs (at least 64 types),
   with canonical type-tagged postorder nodes; immutable sharing never changes
   descriptor values or root order. v1 remains readable and the writer falls
   back for small/unsupported/beyond-bound graphs. v2 validates backward refs,
   exact scalar types, all nodes, root indices, total logical expansion <=16M
   items and depth <=128. Metadata preflight destination contract remains.
   Actual 668-module fixture: T row 350,795 nested tuple occurrences / 726,468
   logical items / max depth39 interned to 5,693 nodes and 1,193 type roots;
   T JSON 5,046,730 -> 268,846 bytes (-94.7%); complete wire 29,513,459 ->
   24,735,575 bytes. Legacy/v2 other rows unchanged.
   Final guarded-code host CPython3.13.5 reader, five alternating fresh
   pairs each: worker_resource_plan CPU .67314 -> .04718 s, peak156,092 ->
   74,492 KiB; class_gen .79149 -> .15266 s, peak156,228 ->81,028 KiB;
   AArch64 backend .66586 ->.11449 s, peak156,216 ->77,448 KiB. 15/15 CPU and
   RSS wins; exact root semantic output hashes; unchanged legacy raw-index
   control .05812 s /107,576 KiB. Corrected v1 writer -> v2 writer median
   1.02618 -> .63389 s, 81,084 ->56,248 KiB (5/5 both); unchanged legacy
   writer control noisy .795 s/159,672 KiB. No whole-stage speed inference.
   Exact owned Linux x86 runtime built from candidate source under 3-job
   3GiB guard in127.06s, peak383,053,824B, source/codegen provenance verified.
   Actual pcc0+self+owned linker fixture admitted exact repo source closure
   (55 modules, 167.7MB IR), compiled206.81s/peak1,318,576,128B, static ELF;
   v2 read/write and eight GC loops print INDEXED_EXPORT_OWNERS_OK and output
   bytes equal CPython SHA256
   7f08d047935761b0a2db5f47f923d05fd8f92cf1c3bb03af0fc8b2fe88cc3e59.
   The scratch fixture's explicit PCC_PACKAGE_SITE and owned linker are now
   set in regression test. Separate exact codec body native ELF read the
   actual v1/v2 T rows, identical expanded JSON; native canonical encoder
   emitted exactly the host v2 T SHA256
   c6bbad4c058c84f56313e401f9a6b028ae594639eba2dac85d1508f1ca3334ae.
   297 focused host contracts pass, one native test deselected from that
   host suite (actual pcc0 fixture executed separately); v1/v2 failure,
   malformed and expansion/transport validation all tested. Broader host
   checkpoint suite had 636 passes, 5 reds (old libpython-route and system
   numpy symlink import inventory reproduced on prior source; 3 CPU guard
   budget tests timed out under competing load); no whole-CI claim.
   Full latest CI artifact attribution: Windows 15136410279 Stage1 compile
   1180.880s/3,627,479,040B peak, Stage2 last sample1781.463s/2,653,753,344B
   peak, 3 codegen modules done, class_gen active; Stage2 export608.662 vs
   Stage1 63.797s, summary188.872 vs17.544s, preload193.750 vs20.364s.
   Linux x64 15134943149 Stage1 compile1571.474s/2,936,516,608B peak,
   Stage2 last sample1781.131s/3,198,173,184B peak (+8.91%), two codegen
   modules done, cli_bootstrap active; export789.855 vs77.118s, summary
   242.045 vs18.791s, preload263.820 vs24.797s. ARM owner15135581178
   timeout1800.199s/4,753,969,152B whole-owner peak, class_gen active.
   Stage2 <= Stage1 bar unmet. Next: actual native reader A/B follow-up,
   then tackle summary-worker repeated effect export decode and provably
   root-independent class reconstruction. Maintain same guards.

   Native reader replay follow-up: same candidate static x86 Linux ELF
   (actual 55-module production reader fixture from the preceding round),
   same frozen real worker_resource_plan root, v1 vs v2 only. Five alternating
   fresh-process CPU/RSS pairs: median CPU7.00259 ->2.13842 s (-69.5%, 5/5),
   wall7.03230 ->2.13915 s; peak RSS339,544 ->288,212 KiB (-15.1%, 5/5).
   Unchanged small-v2 control .03680 CPU s/23,412 KiB, some competing host
   compilation noise; exact selected root counts 1 module, 1,193 types,
   1,453 base keys. Guard COMPLETE51.0s/max344,498,176B. Separate same-ELF
   full semantic JSON output comparison byte-identical SHA256
   57bbd37a2210f9f4663d8c81dcc535aa0927095a03c6042f4f610567792cde5c.
   Native owner-level evidence, not whole Stage2 timing; CI checkpoint
   6cbafc4a covers the production DAG.
   ARM full CI artifact15135712330: Stage1 actual1735.907s/4,598,730,752B
   peak, Stage2 sampled1781.508s/4,295,376,896B peak then timeout; three
   codegen modules done, class_gen active. Export360.751 vs51.847s,
   summary103.486 vs12.861s, preload117.674 vs15.835s. Stage2 wall parity
   failed all non-Mac platforms. Next: profile actual summary-worker effect
   graph accesses and remove repeated whole-graph decode where exact
   effect-summary bytes and resource guards agree.

   Stage2 summary-worker private effect export transport (next owner):
   actual 625-source pcc.__main__ recursive stdlib graph frozen under guard.
   Legacy compact effect wire 2,547,062B, near hosted 2,545,590B; four
   real 8-module shards (pipeline, class_gen, parser/backend, stdlib tail)
   access 63/120/34/17 of 625 module tables, respectively 213,916/480,392/
   135,005/51,895 of 2,522,096B row payload; no full-table scans during
   actual worker summary. Replace eager repeated whole-graph decode with
   versioned indexed effect export rows; complete inventory/header/duplicate
   validation is eager, module payload decoded once on access and cached,
   unused payload remains private raw string. Legacy wire and custom
   coordinator/worker callbacks retain eager transport. Producer preflight
   still completes metadata render before destination open. All 625 decoded
   tables equal, and 32 summary files (four shards) match byte-for-byte.
   Host 5 alternating pairs, exact analyzer worker and same AST/wire paths:
   class_gen shard median CPU1.45076 ->1.24721s (-14.0%,4/5), RSS102,764
   ->93,652 KiB (5/5); stdlib tail CPU.25205 ->.15917s (-36.8%,5/5),
   RSS37,420 ->32,012 KiB (5/5). Eager candidate control CPU1.5159/.21739s
   and RSS102,784/37,344 KiB; host competing build noise, no wall claim.
   Producer wire size2,547,062 ->2,544,475B, host writer CPU.05272 ->.03982s,
   RSS39,848 ->30,124 KiB (5/5 both), unchanged eager reader control
   .04437s/40,548 KiB. 190 focused host tests pass, native integration
   reuses its existing owned-binary summary regression for eager/indexed
   output equality. No whole-stage or CI parity claim.
   Exact new runtime: 93.99s, peak390,979,584B, guard COMPLETE;
   192-member archive provenance validates with current codegen stamp.
   Native pcc0+self+owned linker actual worker-body fixture, 58 source
   modules/183,798,071B IR, built188.06s, peak1,352,876,032B, static ELF.
   Native writer on full 625-table actual graph equals host indexed wire
   SHA256 66c3ce467a83e5b71228b1bf1f13e5293348fb2dd2e3952552331a87e1be66f7.
   Native lazy class_gen shard (24.74s, sampled240,164,864B) and stdlib tail
   (2.59s, sampled48,394,240B) complete, exact CPython summary SHA256
   32561a1b9c228f1a058e7d3a94875f74b3644d3bba02b119d812dadb204062b3 and
   1ea4a6a319beff1aadeb2cfdd810718570094a3a5496d2718545e8eeacd6d16c.
   Native timing pairs follow separately when host quiet; this source is
   not in integrated CI checkpoint 6cbafc4a. Stage2 <= Stage1 remains unmet.
   Next: investigate repeated unique-class preload reconstruction.

   Stage2 sparse class preload root delta: actual frozen 668-module
   compiler graph /4,658 exported class rows /379 sensitive roots;
   global 825 descriptors/1,453 keys. Eight-root profile 11,366 recursive
   class lookups (22,732 discarded module/owner list membership checks),
   6,565 dense descriptor IDs encoded. Each root previously built the
   complete dense index and dependency inventory, then discarded both
   except sparse changes. Fresh root-local context and exact resolution
   order are unchanged; collect drop keys from class_types, compare
   encode_type_memo descriptors directly against global_by_key, intern
   only emitted changed rows. Global sensitivity/dependency contract
   unchanged, no cross-root mutable/placeholder sharing.
   Ten alternating quiet paired wins on actual eight-root owner:
   initial five median CPU .169800 ->.127944s (-24.65%); independent
   five 4x repeated real shards .840936 ->.635463s (-24.43%,5/5).
   Unchanged global preload control (8 runs) .166326s, peak106,804KiB;
   measured process peak106,808 ->106,796KiB, initial graph HWM masks
   owner-only RSS (neutral, no memory improvement claim). All 22,274B
   compact delta payloads byte-identical SHA256
   9654b6ef37aa6b2a5c1330155db9c1c11b05e0465491077ece834aa1cf78aca7.
   77 focused host tests pass, including dense oracle for aliases,
   recursive fields, duplicated bare names and bases with exact order.
   Actual full worker host old/new output byte-identical. Source-matched
   owned Linux runtime built100.10s/400,736,256B; 192 members and current
   codegen provenance valid. Actual pcc0+self+owned linker static ELF
   fixture compiled68 modules/226,670,898B IR in188.92s, peak1,635,930,112B.
   Full 22MB class graph and eight actual roots execute the production
   preload_delta_worker.run in17.81s, sampled449,363,968B, prints
   PRELOAD_DELTA_WORKER_OK. Native and both host worker outputs exact
   SHA256 1a984f6a4e875793febbe097c32528708930c1a5c8f30bd4fa1270d34fb5111c.
   No native A/B or whole-stage parity claim; integrated CI checkpoint
   6cbafc4a does not include this production change.
   Next: measure remaining repeated class reconstruction with exact
   dependency/order semantics, and verify hosted Stage2 owner receipts.

   Native summary transport A/B follow-up (9f101283): same prior
   source-owned 58-module static x64 ELF, exact actual 8-module stdlib
   tail shard and manifest, legacy eager vs new indexed lazy reader.
   Both arms share the candidate analyzer accessor (transport-only
   A/B, host A/B previously compared old/new analyzer). Five alternating
   fresh processes in exclusive quiet window: median CPU3.514695 ->
   2.232185s (-36.49%,5/5), wall3.556249 ->2.279282s; peak RSS86,936 ->
   51,488KiB (-40.77%,5/5). Unchanged one-module eager control CPU1.961691s,
   RSS78,112KiB. All summary bytes exact CPython SHA256
   1ea4a6a319beff1aadeb2cfdd810718570094a3a5496d2718545e8eeacd6d16c.
   Guard COMPLETE38.62s/100,167,680B. Native worker-owner evidence only,
   not whole Stage2 timing.

   Native sparse class preload delta A/B follow-up (7d172a8a): latest
   source-owned runtime at 7a0912e2 built114.11s/403,599,360B, current
   192-member provenance valid. Same static x64 pcc0+self ELF, 68 actual
   source modules/227,398,471B IR, built255.16s/1,674,592,256B, exact
   legacy root function body vs production current function, shared
   production worker body with branch and root-loop wall timer.
   Five alternating fresh processes in exclusive quiet window,
   full frozen actual22,772,804B class wire, same eight sensitive roots:
   median full CPU14.976491 ->14.705130s (-1.81%,4/5), full wall15.053773
   ->14.765579s (-1.91%,4/5), peak RSS440,016 ->439,908KiB (2/5, neutral).
   Root-loop wall1.757219 ->1.540174s (-12.35%,4/5); unchanged same
   graph zero-root control CPU13.308307s (range13.235272–14.062969s),
   RSS439,532KiB. Full CPU change lies within control drift; no robust
   whole-worker speedup claim. Native B/C exact host result bytes
   SHA256 1a984f6a4e875793febbe097c32528708930c1a5c8f30bd4fa1270d34fb5111c
   in all ten runs. Guard COMPLETE218.29s/463,347,712B. The dominant
   remaining cost is repeated whole class-export decode/global prep,
   not the sparse delta loop. No Stage2 parity or full CI qualification.

   Next independent preload transport target (source/byte accounting,
   not yet a production optimization): private worker class resolver
   reads kind/class_name/owning_module/valueclass/base_names/field_types/
   field_names plus complete module/class inventory. Preserve existing
   public preload_class_export_surface full-record contract; restrict any
   field projection to the owned private worker wire and prove exact
   per-root order/aliases/cycles. The frozen actual 668-module class wire
   22,772,804B ->1,988,766B (-91.27%); method metadata alone20,049,466B.
   Compact fixture and full graph global preload/sensitivity/index,
   11 real selected root deltas byte-identical in host check. Next unit
   should verify all sensitive roots, native full/compact worker pairs
   and real integration tests before production push; no current speed
   claim from these bytes alone.

   Stage2 compact owned private class-preload worker transport:
   separate seven-field projection for the actual class resolver; retains
   all module/class binding inventory, aliases, original field and class
   order, missing-vs-present values. Public preload_class_export_surface
   complete-record API and alternate writer callbacks unchanged;
   ordinary native-export wire schema remains unchanged. Only exact
   owned coordinator writer selects compact projection. Both Stage1
   pcc0 and Stage2 native pcc1 use this path when preload workers are
   admitted; serial fallback retains original metadata.
   Actual frozen 668-module/4,658-class graph transport22,772,804 ->
   1,988,766B (-91.27%); method records alone20,049,466B. Full global
   preload/sensitivity and ALL379 actual root deltas equal, aggregate
   SHA256 d3483650f33226ef96ef385f8bdc3b0d16474c2b1cd410bfb3067dac060d0eb7.
   Guard COMPLETE16.92s/113,881,088B. 79 focused host tests pass,
   including actual owned host worker parallel index parity, full public
   callback compatibility, exact alias/base/missing field/order behavior.
   Source-owned Linux runtime117.05s/387,944,448B, current192-member
   provenance valid. Actual pcc0+self+owned linker static x64 ELF
   68 modules/227,599,669B IR, built206.17s/1,618,841,600B. Native
   production projector on entire real graph emits host-identical
   compact wire SHA256
   f3d207ed090c1b074b72b4998b8d3f721daf6be8de3448c14f0755f3176c67b6.
   Same source-owned actual worker body with phase timers, full vs
   compact wire only, same 8-root manifest. Five alternating exclusive
   quiet fresh process pairs: median full CPU14.708106 ->2.930488s
   (-80.08%,5/5), wall14.767891 ->2.959692s (-79.96%,5/5), peak RSS
   430,256 ->80,868KiB (-81.20%,5/5). Decoder wall12.608076 ->
   1.325711s (-89.49%,5/5), global preload .247041 ->.181294s,
   root delta loop1.367024 ->1.399022s (neutral; 2/5). Unchanged
   full-wire zero-root control CPU13.517278s (range13.274594–13.664461),
   decode12.704087s; confirms decode dominates full worker. All output
   bytes exact native/host SHA256
   1a984f6a4e875793febbe097c32528708930c1a5c8f30bd4fa1270d34fb5111c.
   Guard COMPLETE156.88s/463,724,544B. Stage1 same worker route but
   host A/B not yet measured. Old Linux x64 hosted preload phase
   Stage2 263.82s vs Stage1 24.80s; export phase789.86 vs77.12s,
   so preload is only one part, no end-to-end Stage2 parity or CI
   qualification claimed. Next: host worker A/B and hosted checkpoint
   receipts; then profile remaining full export/AST worker owners.

   Compact class preload host worker A/B follow-up (16f2386b):
   source-matched actual pcc0 host worker body with phase timers,
   same full22,772,804B vs compact1,988,766B graph/8-root manifest as
   native A/B. Exclusive quiet five alternating fresh process pairs:
   median full CPU.879472 ->.331257s (-62.33%,5/5); wall.879994 ->
   .331633s (-62.31%,5/5), peak RSS148,448 ->33,684KiB (-77.31%,5/5).
   Decoder wall.476158 ->.056555s (-88.12%,5/5), global preload
   .136883 ->.027568s, root loop.105920 ->.110014s neutral (2/5).
   Unchanged full zero-root control CPU1.087209s (range.905555–
   1.169006s; noisy but primary gain far beyond control drift), RSS
   148,444KiB. All output bytes exact SHA256
   1a984f6a4e875793febbe097c32528708930c1a5c8f30bd4fa1270d34fb5111c.
   Guard COMPLETE11.86s/164,339,712B. Both host and native worker
   improvements established; whole Stage1/Stage2 remains unqualified
   until hosted receipts include production16f2386b.
   Next: profile remaining actual export/AST worker owners; use CI
   phase receipts to choose the next structural target.

   Stage2 remaining export/AST owner attribution (no source change):
   old Linux x64 hosted artifact15134943149 directly logged export
   manifest-end -> worker collect-start260.309s. Admission events:
   245 complete export workers/624 modules; worker-span256.346s,
   sum of per-worker admission wall956.168s, nested module phase
   244.344s (parse112.335/lift108.041), imports2.294s, remaining
   709.530s aggregate contains AST write/metadata/exports/admission,
   not a separately instrumented phase. Old Stage1 same624 modules
   78 tasks/30.835s span/sum122.477s; latest 6cbafc4a hosted Stage1
   79 tasks/625 modules span25.694s/sum102.344s, module32.501s,
   remaining68.228s aggregate. Latest Stage1 export62.352s,
   summary15.780s, preload18.709s. Old Stage2 c_parsetab export
   worker32.231s (module10.698, remainder21.522s), class_gen17.020s
   (module4.725, remainder12.289s).
   Direct actual AST codec attribution, source-owned runtime at
   4e95bb7f built111.12s/412,794,880B, current193-member provenance.
   Native pcc0+self static x64 ELF26 modules/40,827,401B IR, built
   34.77s/588,972,032B. Same exact production v1 AST codec bodies
   with phase timers, two fresh runs in exclusive quiet window each
   of frozen actual 625-source graph's c_parsetab/class_gen/pipeline
   sidecars. All six roundtrips byte-identical to originals.
   c_parsetab17,852,685B (149,944 nodes): reader component medians
   text1.375511 + JSON decode7.058334 + AST reconstruction2.023277
   =10.457122s; writer AST projection1.747628 + JSON encode2.810122
   + write .072156 =4.629906s. Whole process CPU15.760982s,
   RSS419,678KiB.
   class_gen11,209,510B (89,142 nodes): reader .744313+3.978986+
   1.238580=5.961879s; writer .945821+1.886369+.053093=2.885283s;
   CPU9.260428s/RSS263,308KiB.
   pipeline3,591,026B (28,816 nodes): reader .205294+1.666853+
   .435316=2.307464s; writer .280810+.633064+.016783=.930658s;
   CPU3.394190s/RSS100,046KiB. Guard COMPLETE57.38s/445,071,360B.
   AST writer is only part of old export worker remainder; JSON
   decode dominates independently measured reader used by summary
   and codegen workers. Prototype byte-accounting (not production
   performance): positional tagged nodes plus interned SourceSpan
   file table gives same raw values with payload c_parsetab5,408,703B
   (-69.70%), class_gen3,338,419B (-70.22%), pipeline1,143,892B
   (-68.15%); 49,982/30,995/9,892 repeated source paths respectively.
   Next independent owner: versioned compact AST writer/reader with
   strict tag/arity/location validation, legacy v1 compatibility,
   generic opaque fields preserved, exact AST/downstream output and
   actual native five-pair read/write validation. No whole Stage2
   inference from these isolated attribution phases.

   Stage2 private versioned positional AST sidecar transport:
   ordinary exact-owned export worker writes v2 tagged positional nodes,
   interned SourceSpan file table, strict location index/coordinate and
   node arity/tag validation, explicit bytes/opaque map records. All
   encoded fields decoded/validated; shared AST constructors retain
   defaults, None normalization and original values. Positional layout
   fingerprint pinned to v2; future field-order changes require version
   bump. Public raw/v1 writer and Stage1 checkpoint-stage=1 sidecars
   unchanged. Legacy v1 decoder unchanged, v2 exact envelope supported;
   closed_young token validates exact members per version and decodes
   same hashed bytes. Worker budgets/concurrency unchanged. Hosted
   Stage1 direct python -m pcc and Stage2 effective worker env have no
   checkpoint flag, so ordinary route configured in both; next hosted
   receipt must verify actual use.
   Frozen actual 32-module sidecars from625-source graph: all v2 decode
   semantic AST and canonical v1 reencode exact originals; total
   66,430,186 ->16,405,999B (-75.3%), aggregate original SHA256
   2abf89280bd39fc3a85f54058e3a44dbf727470e811c2a710883b01700c2a703.
   Actual c_parsetab17,852,685 ->4,359,081B; class_gen11,209,510 ->
   2,687,524B; pipeline3,591,026 ->936,160B.
   190 final focused host tests pass (native integration node deselected
   but direct native fixture below), actual parallel two-module v1/v2
   emitted IR byte-identical, checkpoint selector and closed_young
   strict corruption/hash tests. Broader checkpoint suite298 pass,
   7 reds reproduced on exact base56064cab (6 stale link mock signatures,
   1 tracked Node body). No candidate regression claim for those.
   Quiet source-matched host five pairs per real module, same codec
   read/write roundtrip and small v1 control: c_parsetab CPU1.023089 ->
   .958744s (-6.29%,5/5), RSS145,820 ->102,284KiB (-29.86%,5/5);
   class_gen .772595 ->.651827s (-15.63%,5/5), RSS96,240 ->68,236KiB
   (-29.10%,5/5); pipeline .308510 ->.243362s (-21.12%,4/5), RSS
   startup floor neutral. Control .126–.130s, exact bytes, guard23.01s.
   Source-owned runtime118.93s/386,174,976B, current193-member
   provenance valid. Actual pcc0+self static x64 ELF codec fixture
   52 modules/168,642,561B IR built149.70s/1,338,155,008B.
   Native strict contract prints AST_V2_CONTRACT_OK (Unicode source
   locations, opaque dict/bytes, 7 malformed tag/arity/location cases).
   Same binary 3 actual modules x5 alternating exclusive quiet pairs
   + unchanged small v1 control, full codec CPU/RSS all15/15 wins:
   c_parsetab CPU15.154631 ->9.457991s (-37.59%), RSS415,252 ->
   246,220KiB (-40.71%), reader component medians10.003575 ->
   6.005407s, writer4.505764 ->3.021704; class_gen CPU9.704605 ->
   6.052958s (-37.63%), RSS256,932 ->148,252KiB (-42.30%),
   reader6.175384 ->3.902141, writer3.138149 ->1.839769; pipeline
   CPU3.022381 ->1.919901s (-36.48%), RSS102,524 ->57,008KiB
   (-44.40%), reader1.930198 ->1.271359, writer.913157 ->.572798.
   Small v1 control .649–.731s, all45 output bytes exact respective
   inputs, guard COMPLETE238.17s/452,395,008B. Same source-owned
   corrected native downstream fixture58 modules/184,508,180B IR
   built188.25s/1,373,794,304B; explicit pipeline_exports local-import
   owner resolves initial isolated scratch omission. Actual class_gen
   v1/v2 AST through real summary builder and lazy effect exports
   produces exact prior native/host summary SHA256
   add924f6487f4a56b59948ffacddf2d8d2895556605a5dd2ebce9ba5df06056a.
   Both guarded native summary runs complete; single correctness runs,
   not an additional A/B. No whole Stage2 parity claim.
   Latest hosted x64 source6cbafc4a (not compact/V2) artifact15143435682:
   export659.022s, summary202.441, preload220.162. Stage2 compiler tree
   peak3,202,990,080B vs Stage1 2,877,579,264; owner outer3.715GB.
   Only three codegen modules complete at timeout; terminal singleton
   class_gen index209 (also ARM64). Exact latest log has class_gen
   infer5.243s, prepare12.478s, completed frontend direct capture
   17.729s/440,208 records/zero fallback, no completed backend emit.
   c_codegen Stage2 frontend145.417/backend177.213s vs Stage1
   23.242/62.348; cli_bootstrap121.096/137.198 vs18.061/47.021.
   Oversized assembly handoff is intentional memory guard, not an
   unexplained concurrency bug. Next: replay actual retained CI
   pcc1 exact source6cbafc4a singleton class_gen/c_codegen with
   source-matched context and actual phase/RSS instrumentation; no
   full bootstrap or relaxed guard, then choose measured owner fix.

   Authentic hosted singleton replay (2026-11-26). CI run 45856871699,
   Linux x64 source 6cbafc4a14b20d38ae831d168133c6ebd85568de,
   Stage1 artifact 15140867848, retained native pcc1 SHA256
   c88e3ba155f98cef716e2f5b75009ba6cc3721d4b6abb92dd37b8bbbe5cdae08.
   The exact terminal worker_3 manifest (SHA256
   1ceb7de640a3b6e5005a734571fc63f9d8b3bb13683c620d75ec205cb14961f3)
   gives singleton class_gen index 209, 625 modules, 624 sibling initializers,
   442 indexed export dependencies, and the 245 admitted export chunks.
   Reconstructed old-source context matches hosted indexed exports size
   32,747,067 bytes and dependency count; hosted payload hash unavailable.
   Native target export singleton agreed byte-for-byte with the local host
   AST (11,271,500 bytes, SHA256 b248d4510926ae8d117800144cc0e8aed91793d7bea7e9b803ef122d25a00240),
   shard exports (220,417 bytes) and dependency sidecar (12,505 bytes).
   Local source path differs from hosted path. Actual pcc1 source identity
   b0c2890c1ddceda8b43d6e3262589870b6f0c54eae3829a0c6e08717e8245322
   is the exact hosted 1587-file tree, and the original native runtime
   provenance is validated. The retained native compiler replay uses the
   same oversized assembly route (direct native object disabled), one
   worker, no fallback, unchanged 400-second / 1.8-GiB isolated guard.
   Result: TIMEOUT 400.240535 s, isolated process-tree peak 1,671,671,808 B,
   terminal native process RSS 1,661,886,464 B; CPU censored by guard kill,
   no final assembly/object/result. Frontend inference 7.354 s, prepare
   17.378 s, ClassLowering class body 129.594 s, direct capture 22.631 s;
   supported records 440,208, blocks 86,393, call continuations 13,617,
   error cleanups 2,317, error frames 7,145, instructions 332,713,
   functions 727 and zero fallbacks exactly match hosted replay evidence.
   Frontend/capture completes, backend emit does not finish before the
   guard. These are isolated actual owner observations, not completed
   Stage2 measurements or an uncensored backend timer. Hosted class
   body was 99.022 s versus Stage1 11.246 s. Next: same-route host
   comparator to observe phase shape, rank actual ClassLowering method
   costs and instrument backend subphases independently, with exact input
   and fixed guards. No increased limits/concurrency, no extra full bootstrap.
   
   Same-route host comparator (diagnostic, concurrent independent builds;
   not a formal causal A/B against the exclusive native timeout): old
   exact source 6cbafc4a, same reconstructed context and native target AST,
   same one-worker oversized indexed-assembly route and unchanged guard.
   COMPLETE 89.110456 s, wait4 CPU 83.877012 s, peak isolated process
   tree 845,185,024 B; published 57,304,557-byte assembly SHA256
   7165f5ed623388294453b9a74154db3e9562185daf3d9f446b03c35811777e16.
   Exact same indexed CFG counts as authentic native replay. Host infer
   3.427 s, prepare 0.742 s, ClassLowering class body 12.977 s,
   capture 3.963 s; backend inclusive 56.522973 s, with existing
   aggregates verify 4.413454, layout 2.201835, stack-map 15.328429,
   function emit inclusive 34.088760, finalization 0.076371 s.
   These are completed host owner phase observations, not uncensored
   native backend phase durations. Native 400-second timeout was
   described above. This confirms the exact phase shape and motivates
   bounded real backend progress receipts and actual class method traces.

   Authentic retained native pcc1 method trace (diagnostic only): same
   old-source singleton/manifest, 400-second / 1.8-GiB guard, existing
   PCC_DEBUG_CODEGEN_PHASES enables statement/body logging, filtered
   method inter-event wall timestamps while independent builds ran.
   All 155 ClassLowering methods completed, no skips/open method;
   final methods-end marker at 275.162475 s from invocation. Intentional
   scope stop after observing marker (exit 130; guard JSON remained a
   RUNNING checkpoint; no worker completion, no CPU result). Last sample
   at 293.518 s; sampled peak tree 1,285,758,976 B. Trace SHA256
   670a61b808faf278ca219a5853c0f6b7f10ed1ebbb49cefd8e617c1bbc8e693d.
   Observed completed method compilation spans total 163.406172 s,
   top _emit_class_init 21.647770, _emit_method_body 14.573946,
   _emit_class_init_outline_body 10.706633 (top three 28.72% of sum),
   _maybe_expand_dataclass 6.751469, declare_extern_class 5.309528,
   _emit_class_method_publication 5.169204, emit_instantiate 5.119972,
   _normalize_native_class_bases 5.048629 s. The values identify which
   method bodies the retained compiler was compiling, not execution
   costs of those routines or an uninstrumented benchmark. Preserve
   the earlier exact uninstrumented timeout as the owner evidence.
   Next: bounded x86 backend phase events (no timer schema change);
   quantify actual method function-table snapshot visits and mutations
   before selecting a structural optimization.

   Bounded x86 backend progress diagnostics (not a performance claim).
   Existing worker timing produces a fixed twelve flushed begin/complete
   receipts for verify, layout, target memory passes, module setup,
   function loop and finalization, under scope x86_backend / parent
   backend_call. Incomplete/killed work leaves open begin marker, does
   not fabricate elapsed completion. Existing sixteen inclusive aggregate
   indices remain unchanged; custom aggregate-only observer protocol and
   disabled path untouched. Linux and Windows emitted assembly identical
   with/without diagnostics; original verifier exception pass-through,
   clock failure best effort. Host 554 focused suite tests + 3 new clock
   failure cases pass. Exact candidate source contextual direct capture
   across full 625-source closure, all three modified compiler modules
   fallback zero (timing 69 funcs/16,106 instructions, prepare 26/5,676,
   x86 backend 325/236,939), complete 64.076 s, peak 540,274,688 B.
   Fresh owned runtime provenance validated, compile native helper 22
   modules (25.498 s, peak 468,062,208 B), static ELF no dynamic libpython,
   SHA256 39ca3dd7fed8b8e8d62da0ccb15907ffb557f4bb4d06759b7260b25c15cb63,
   all 12 native event records exact order and clock_ok=1, aggregate
   totals zero, unknown label rejected; NATIVE_X86_PROGRESS_OK, rc0.
   This patch instruments future actual hosted native long-tail backend
   phases; does not claim old retained native binary contains the patch.
   No full bootstrap, no limit change. Next independent measured
   structural method owner to count function-table snapshot visits vs
   mutations and choose safe sparse alternative only if justified.

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

**Concurrent old-generation scanning.** The November 26 change on
`8f756a029b` starts a worker at the beginning of a full cycle when more
than one mutator is registered, reference counts are atomic, and the
container-lock mode is available. `pcc_gc_backend0_full_set_concurrent(0)`
disables subsequent worker starts. Explicit 1 also allows a worker in a
one-mutator threaded runtime. A mutator assists when allocation outpaces
scanning. Roots, large-object index enumeration, handover, and the exact
young verdict still require stops. Worker startup registers a normal-exit
callback, reaps the previous handle, and publishes the new joinable handle.
Normal exit closes admission, joins the worker, and drains its held releases.
Immediate process termination retains its platform semantics.

Local Darwin arm64 verification uses `uv run pytest -x -vv
tests/python/test_gc0_worker_exit.py`. A native exit callback observes an
active scan; an earlier registered callback runs after shutdown and checks
that the worker has exited and its handle is clear. The explicit opt-out
starts no worker. A complete young log and retired handover blocks now feed
the exact view in one validated walk; ordinary mutator-log saturation still
uses exact allocator/index enumeration. Run `uv run pytest -x -vv
tests/python/test_gc0_high_fanout_pause.py tests/python/test_gc0_young_log_bound.py`.
The final synchronous verdict still has no hard pause bound. Five alternating
Darwin arm64 pairs against the sealed parent, including an independently
linked baseline control, measured the exact young verdict for 160,001
unreachable containers. With 640,000 unrelated old lists retained the
median fell from 35.79 to 32.40 ms (5/5). With 1,280,000 it fell from 42.86
to 31.78 ms (5/5). With 320,001 candidates and no unrelated retained
lists, medians were 62.55 and 64.54 ms (0/5 shorter; control floor 6.5%).
The ordinary handler's five-pair median QPS changed +1.75% at one worker
and -5.30% at four, inside control floors of 6.95% and 13.09%.
The host-pcc0 weakref executables at both provenance settings matched the
CPython 3.15 reference. Native-pcc1 qualification needs a current compiler.

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
cells. The subsequent partial-page trim also considers slabs newly crossing
sparse occupancy thresholds. It returns wholly central-free 16 KiB ranges,
including their allocator prefixes, and reconstructs dormant cells on reuse.
Magazine cells and live cells remain in place. Counter 3 of
`pcc_allocator_object_slab_counter` counts currently discarded partial
bytes. On Darwin arm64, 20,000 lists on 35 slabs with one survivor per slab
returned 1,720,320 bytes while preserving survivor identity and contents.
The native regression fills the slabs again before releasing the survivors:
`uv run pytest -x -vv tests/python/test_gc0_object_slab_return.py`.

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
