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
That run is a red baseline. Pending: none. The 2026-11-23 checkpoint
is due; dispatch was not admitted because the Linux GitHub CLI has no
authenticated host. No run ID was issued.
[Run 45020986775](https://github.com/ikshengmin/pcc/actions/runs/45020986775)
at `0f638af5b` completed red and did not replace it. The
[checkpoint comparison](investigations/checkpoint-validation-45020986775.md)
identifies three Go ordinary node regressions
([#10](https://github.com/ikshengmin/pcc/issues/10)), one Rust integration
node regression ([#11](https://github.com/ikshengmin/pcc/issues/11)),
and a separate Windows collection blocker
([#12](https://github.com/ikshengmin/pcc/issues/12)).
Stopped areas: none. The #10 and #11 fixes are locally validated;
the affected hosted lanes have not been rerun. #12 remains open.
Linux ARM now produced Stage1 (6,794 s); Stage2→Stage3 is not green.

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
   fixes whose hosted lanes were not rerun;
   [#12](https://github.com/ikshengmin/pcc/issues/12) (Windows collection)
   is open. This item's earlier text, the results for #9 and for collectors
   1-4, is in the
   [archive](status-archive/2026-11.md#work-order-item-1-gc-correctness-as-of-2026-11-24).
   Next: no unit is ready. A correctness failure of the one collector
   becomes this item's first unit when it is reported.

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
   Next, in order. Each letter is one unit, a group of files of it when it
   is too large for a round; area `runtime-gc` unless it names another.
   Two defects found on Linux at `cba826a9` come first (details in the
   section below):
   (0a) Done on Linux: the young log is process-wide bounded without
       changing collection admission; see the regression evidence below.
   (0b) Slot slicing and allocation-free gray links landed. The 320k
       live-container probe improves on Linux; the complete pause target
       is not met.
       Exact reclaim now uses the existing terminal free protocol for
       already-cleared built-in containers; the 320k Linux pause fell from
       a median 226 to 173 ms. The full-to-young handover now also yields
       between batches, without admitting a young view over a partial batch.
       Next: remove candidate-buffer growth copies, then split the exact
       reclaim phases; the complete pause is still not bounded.
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
       Next: the same `ab` with `--pairs 10` on an idle host (arms
       `gc0-head`, `gc0-head-b`, `gc0-master` exist under `PCC_PERF_HOME`).
       Platform macOS.
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
   - none
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
   items, would apply to mutable Go and Java objects. C++ (TinyXML2
   `1199:9`): destructor exception specifications and the non-throwing
   override rule instead of effect proofs, as approved in
   [#14](https://github.com/ikshengmin/pcc/issues/14); the project-only
   effect domain is rejected. Existing probes
   are not native upstream acceptance. The opt-in source probe reached
   `internal/bytealg/bytealg.go:9:2`; no dependency-closure executable exists.
   See the
   [closure audit](investigations/go-math-bits-original-closure-frontier.md).
   No full test execution or owned map/goroutine storage.

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

State against that bar: not met. In CI run 44358778171 (Linux x86-64, below)
Stage1 took 2,202.313 s with four workers; Stage2, with two workers, was
stopped by the 1,800 s watchdog before linking, its process tree already 22.5%
above the Stage1 RSS sample. The worker counts differ, so those times are not
an equal-condition comparison. Stage2 completion blockers are tracked in
[allstoalls/pcc#200](https://github.com/allstoalls/pcc/issues/200).
