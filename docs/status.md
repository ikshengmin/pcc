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

**Integrated validation, 2026-11-27 14:31 +0800.** Run #647 (45937924805, source `26d6fb08`) completed with 21 successful, 30 failed, two skipped and one canceled jobs. All four Stage1 builds produced 629/629 modules; all four Stage2 attempts reached compilation deadlines, not memory exhaustion. The entire remote queue was verified empty across all active/pending states before this single integrated push. This snapshot follows source `dd956956`: accepted managed allocation classes, scoped opt-in MIR/CSR and generator suffix changes, plus the exact research-test collection fix for Linux x64/ARM and Windows. macOS separate not-integration RSS failure and Stage2 completion remain open. No second dispatch, rerun, or non-skip push while any CI is active or pending. Subsequent work remains local and `[skip ci]`.

**Integrated validation, 2026-11-27 16:46 +0800.** Run #648 (45952368942, source `e8e9875d`) terminal 22 success, 30 failed, two skipped; all 54 jobs ended, entire repository active/pending/queued/waiting/requested zero. All four Stage1 owners produced 629/629 modules, four Stage2 owners still compilation-deadline failures. This single integrated snapshot follows `d3df26c2`, including no-local Mach-O placement allocation reduction `d61b5bb6` (unchanged budget), and exact disjoint export timing diagnostic (default timing flag only; strict context zero fallback; no performance claim). Its purpose is qualification and actual native phase attribution. All further source/status pushes `[skip ci]` while any run is active or queued. No duplicate CI dispatch or rerun.

**Integrated validation, 2026-11-27 19:08 +0800.** Run #649 (45968267063, source `f042ab23`) terminal all54 jobs: 22 success, 28 failed, two canceled, two skipped. All four Stage1 owners produced artifacts, all Stage2 owners compilation deadlines; Linux x64/ARM/Windows native prior and Stage1 wheel/install/execute completed. Entire repository active/pending/queued/waiting/requested verified zero before this single integrated push. Snapshot follows `d3fd0f35`, including exact ABI chunk correction `3c65da0d` and maintainer-selected DEFAULT strict native AST JSON path `0648fe67`. Local exact native decode −54.56%, whole CPU −32.47%, RSS +6.33% scoped adverse explicitly accepted for this candidate; real complete summary fixture not qualified, next hosted Stage2 phase/memory evidence essential. No extra dispatch or rerun; all subsequent pushes `[skip ci]` while any run active or queued.

**Integrated validation, 2026-11-27 21:05 +0800.** Run #650 (45987874137, source `f2a870a4`) terminal all54 jobs: 22 success, 29 failed, two skipped, one provider canceled. All four Stage1 built629/629; x64 1083.793s/3057623040B, ARM1654.395s/5187276800B, Windows1000.883s/3579244544B, Mac2556.274s/2062729216B. All four Stage2 failed deadlines; x64 export350.839/summary97.449/preload45.727s, no pcc2. Whole repository queue verified in_progress/queued/pending/waiting/requested zero before this single integrated push, maintainer informed. Latest exact source parent `2b8866e2` includes root leave ledger and errno test fix, scoped standard ELF STT_FILE admission and relocation rejection `184f51f0` with 77 focused passes/strict629 contextual fallback0, plus previously landed AArch64 optimization. Compact tape diagnostic is scratch-only, not in this source. No additional dispatch/rerun/non-skip push while any run active or pending.

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
observed pending. As of 2026-11-26T19:13Z run 45856871699
(6cbafc4a) is complete and failed; Linux x64/ARM64/Windows complete
integration partitions report downstream BUDGET_EXHAUSTED UNRUN
remainder, not independent test regressions. Native Stage2 owner failures
remain. Run 45862787448 (c20c2c01) is now active; its four worker-prior
artifacts reproduce the stale scalar-arena expectation already fixed
in later `bd763672` (not included in c20c2c01), so do not duplicate it.
Run 45868668374 and 45876460150 remain pending. A new distinct
integrated source snapshot adds actual x64 sparse frame owner
`c570f27e` (normalized -13.782% frame owner, 5/5 exact outputs) and
integer stack-map route `4d9089be` (205755 real routes, owner CPU
2.274->0.480s, normalized -79.08%; actual full worker normalized
CPU -5.531%, 5/5, exact ELF, RSS noninferior). Current overlapping
x64 progress diagnostics preserved and integrated parity tested.
It also includes production bounded Stage2 x86 owner progress records
`d3e132d6` (557 host tests, actual strict native fixture, no speed
claim), actual CPython scratch reference source-path fix `cd6d906c`,
one-collector sorted ownership model correction `a1dc9332`
(exact baseline 20 fail/48 pass ->70 pass), strict live single
collector resource-case receipt cardinality `3014994e` (92 focused
pass; real native prerequisites remain unrun), and no resource,
worker-count or deadline relaxation. This non-skip status-only commit
queues this newly distinct snapshot; existing runs stay intact.
Verified exact run [45882128844](https://github.com/ikshengmin/pcc/actions/runs/45882128844)
head `15f6332dd3a1629efc4699cb8f3aa0b201a0179c`, observed pending.
Current c20c2c01 actual Stage1 artifacts15147497153 (Linux x64)
and15148417046 (Linux ARM64), exact manifests/hashes verified:
4 workers,625 modules, x64 1190.927323s/2,851,999,744B
(vs1206.445611s/2,877,579,264B), ARM64 1614.150228s/
4,911,534,080B (vs1644.478621s/4,676,341,760B; RSS +5.03%).
Both precede later AST v2/frame/integer route etc; six-worker
300s target unmet and no comparable completed Stage2.
As of 2026-11-26T19:58Z the next distinct cumulative immutable snapshot
at production head 761b7d0b includes private nonsuspending list iterator
7a099ce3 (actual same-handler w1 throughput +4.56%, CPU paired normalized
-4.0%, RSS noninferior; w4 throughput neutral; no stable w4 gain claim),
native global result index b81247f7 (actual repeated owner -99.845%, exact
ELF, whole worker neutral), host verifier successful definition plane
761b7d0b (actual c_codegen owner CPU normalized -13.065%, 5/5, full worker
verification phase -13.471%, total neutral, exact ELF and RSS),
native annotation index 1eef7a57 (actual 2025 scans eliminated,
isolated native CPU -33.592%, exact assembly/CFG, RSS neutral), and
actual lambda iterator root-frame retirement fix 10f25029 (18 model,
68 adjacent, three source-matched native cases passed). Existing
resource and correctness gates unchanged. c20c2c01 latest actual
Windows Stage1 1804.973591s/3,753,308,160B; x64 Stage2 timeout
1800.146s, actual partial compiler-tree peak 3,207,159,808B (+12.45%
above Stage1), 8405 samples, last 1783.857s, class_gen backend
unfinished. ARM64 Stage2 owner timeout 1800.203s; outer pytest tree
4,771,790,848B is not a comparable compiler-tree value. Stage1 six-worker
300s and completed Stage2 wall/RSS <= Stage1 remain unmet. This non-skip
status-only commit queues this distinct snapshot without cancelling
or rerunning prior workflow runs.
As of 2026-11-26T20:35Z the next distinct cumulative immutable
snapshot includes production MRO lazy traversal 61fc4ea0 (actual
151998 projected leaf queries, native inner -37.15%, CPU -24.18%
5/5, RSS neutral, native BFS/cycle/mutable positive tests, original
full class_gen assembly exact; existing baseline direct-method
failure byte-identical and separately owned). Also integrated:
Linux errno raw closure target-specific actual fix 5d113086 (13
native/IR/cross and 8 adjacent tests, strict625 fallback0;
general NULL trap preserved, LLVM reference local unrun), exact
scaffold result ownership model66c6d15d (20/20 actual ELF, three
negative mutations), Linux stackwalk existing policy100c29d8
(9/9 actual, Darwin linked-table unrun), Darwin prerequisite
cb23b48a and exact same-run Mac source owner transfere1ac8f39
(95/95 contract models, missing/wrong-source/host/phase fail closed,
current Mac source execution unrun), missing receipt validator
fae0dcd2 (50pass/48 subtests, actual baseline1 fail/18 pass,
request/output and existing seal preserved). Stage1 ARM trampoline
candidate not included: owner CPU normalized -18.465%5/5 exact
PCO but actual small-worker RSS normalized +2.903% beyond .165%
control, full CPU neutral; not authorized for production.
Current c20c2c01 Stage1 Mac actual all625 compile3480.394s +
publish31.123s =3511.517s, guard peak1,971,683,328B,
average1.773705 workers. All four Stage2 owners uncompleted:
x64 partial compiler tree3,207,159,808B, ARM4,278,214,656B,
Windows2,654,302,208B before 1800s timeouts; Mac inner timeout
600.430s partial1,929,265,152B (outer621.276s). No Stage2 wall or
peak parity verdict; no six-worker cold300s acceptance.
Mac separate marker integration pressure-lease schema mismatch
is under exact investigation, pressure tests unrun; not-integration
collection hit memory limit2,885,287,936B vs2,880,356,352B,
no test node outcomes. No deadline, worker-count or resource
gate relaxation. This non-skip status-only commit queues the new
distinct source snapshot, prior workflow runs preserved.

As of 2026-11-26T21:46Z the next distinct cumulative immutable
snapshot at production head d3b3d354 includes nonmoving inline four-slot
list storage a5b2bb00 (actual raw allocations17.57625->10.47625/request,
managed35.45 unchanged, requested4965.12B unchanged, usable+227.2B;
measured native count1/2 owner CPU normalized -12.393%/-12.213%5/5,
same-handler w1 CPU -1.639%4/5 beyond max control1.152%, RSS
-0.357%5/5; QPS directional only, w4 neutral, p99/STW within controls,
87 focused tests and exact native ownership/cycle/weakref audits).
Stage2 direct-only entry-hoist positioning 811125a0 (actual 6465
queries/1,788,109 records, verified final direct owner; two exact
source-matched native projection binaries CPU -23.543%5/5, inner
-98.522%5/5, RSS -380KiB5/5, exact assembly/positions; independent
Function allocation-only fixture usable unchanged but process RSS
+68KiB at both counts, not a blanket memory guarantee; focused
131 pass/18 unchanged baseline fail/1 deselect).
CI/correctness fixes a91caaaf ordinary boxed external annotation live
dispatch (44/44 focused incl actual Parent/Duck/None and constructor
controls, strict625 fallback0), 0846c755 exact compact Stage1 source
lease pressure admission (31/31 focused, full marker/pressure/bundle
123 pass/2 same local baseline fail, Mac pressure execution unrun),
cd15c218 ordinary external void exact captured live method binding
(19 host and67 adjacent pass, six actual ELF integration cases,
three strict contextual625 fallback0). No unaccepted raw machine
ABI change or rejected ARM scalar validation included: threshold
candidate actual owner -78.644%5/5 exact but qualifying full worker
RSS +2.520% vs median control .596%, rejected. No whole Stage1/Stage2
speed/RSS parity or hosted pass claim.
Public Actions API observed at21:46Z: prior run
[45868668374 (#638)](https://github.com/ikshengmin/pcc/actions/runs/45868668374)
head9223aca1cd69 in_progress; #639–642 pending, existing runs left
intact. This non-skip status-only commit queues the next distinct
source snapshot, no worker/deadline/resource gate changes, no
cancellation or rerun.

As of 2026-11-26T22:27Z a newly distinct cumulative exact source
snapshot at production head de4eb2b1 follows the pending prior
non-skip run [45900488816 (#643)](https://github.com/ikshengmin/pcc/actions/runs/45900488816),
head 7fcbb3948fa161dd2e3672d82eaeef0bfde188fe. First-parent ancestry
is verified through all six subsequent accepted units:
raw machine method ABI 7d3c7540 (actual baseline37->75, candidate37 exact,
native signed/unsigned boundaries and ordinary control, host110,
three contextual625/zero fallback);
retired direct libpython package test c963ef85 (focused2, no route
restoration/cache admission), explicit legacy ABI label model4a6f8f30
(focused8), atomic unsupported package mode rejection3c33b911
(host/native-source54 pass, no invalid acquisition/build/install/cache
or native claim, four strict contextual625/zero fallback, current actual
pcc1 fixture UNRUN); real four-platform worker-prior stale bounded
root-cleanup model5f49dbca (baseline1F17P->24/24, actual runtime helper1–5
and reverse caller cleanup/stackmaps, six-root inline, adjacent exact
baseline4F108P->3F115P; no hosted acceptance yet). Stage1 ARM typed
single-run host snapshot de4eb2b1 accepted only structural memory:
actual captured read_span projected transient24,338,752->12,169,320B,
108 focused, changed contextual626/zero fallback, exact large/small
PCO; formal small CPU neutral and RSS noninferior, no whole-stage speed
or high-water improvement claim. No rejected Stage2 signature candidate
or current unaccepted GC dictionary owner included.

Latest #638 run45868668374 clean9223aca1 actual Stage1 identity all
625/625: Linux x64 compile1189.280s / peak2,799,013,888B (artifact
15153817780), Linux ARM64 1588.248s / 4,607,479,808B (15153818646),
Windows 1238.509s / 3,655,790,592B (15155040498), Mac3314.551s /
entry3349.806s, peak RSS unavailable in profile (15156391872, cap is
not peak). All exact bundle and receipt hashes checked. Full-bootstrap
owners x64/ARM/Win hit unchanged1800s timeout; comparable partial
native Stage2 x64 peak2,778,996,736B at sample1783.604s, four codegen
modules complete, unsafe_lowering still running; Windows partial peak
1,910,099,968B at1775.078s, three modules complete, class_gen started.
No completed Stage2 wall/RSS <= Stage1 or six-worker300s acceptance.
All four #638 worker-prior maxfail artifacts reproduce one stale
three-root test, now fixed5f49dbca; no resource relaxation. Prior
#643 exact pending run count one; production parent de4eb2b had no
workflow run before this distinct normal non-skip checkpoint. Existing
useful queued/active runs preserved, no rerun or cancellation.

As of 2026-11-26T23:12Z a distinct cumulative immutable source snapshot
at production head 5f2ba802 follows #644 run
[45904354249](https://github.com/ikshengmin/pcc/actions/runs/45904354249),
exact head e5b706922147e7798305268f004e7eaf1f318288, observed pending;
verified first-parent ancestry through accepted changes:
99cb997f opt-in exact live coordinator dictionary storage histogram
diagnostic only (actual native explicit counts/owner/bytes tests and
four concurrent workers, flag-off empty stderr; no coallocation,
no performance or whole Stage2 peak claim);
c2c65d99 explicit cross-module raw i64/u64 free-function descriptor
(actual isolated native baseline75 vs reference37, candidate37;
signed/unsigned boundaries and boxed42 exact, host2/2+adjacent102,
strict contextual626 both fallback0; no broad field ABI migration);
5f2ba802 exact AMD64 read-only unwind section type (target/name/flags
specific, preserves payload and relocations,8/8 focused, actual self
raw closure1/1, source preimage controls50/50, adjacent165/165,
context626 fallback0). The unchanged hosted LLVM frame-registry
object advances from unsupported .eh_frame type to genuine unsupported
TLSGD relocation19 and external __tls_get_addr. Full reference
remains red; no relocation allowance, no oracle suppression.
Target-qualified freestanding x64 Linux initial-exec metadata is
next separate source unit, not included. Rejected Stage2 rebound
candidates and unaccepted Stage1/GC next claims are not production.
No worker, resource or deadline gate relaxed. Existing queued #644
has exactly one run; production parent 5f2ba802 had zero runs before
this normal non-skip source-distinct checkpoint. No cancel/rerun.

Latest exact #638 native bootstrap all four incomplete, Mac actual
inner timeout600.350s partial peak1,780,563,968B (artifact15157120792),
outer623.415s/2,296,070,144B (15156203987); ARM partial Stage2 peak
4,302,229,504B at1781.966s/three complete codegen, class_gen rendered
not completed (15155144445). Mac current native-prior process guard
also independently timeout124; actual package/wheel later unrun.
Mac marker pressure identity mismatch fixed0846c755 after that
snapshot; nonintegration collection actual memory limit98.457s
2,836,889,600B over unchanged2,831,859,712B. Linux/ARM/Win marker
integration remainder budget exhausted UNRUN. #638 aggregate exact
14 completed source campaigns / 21908 PASS, excludes11 unresolved
producers, not global green. No complete Stage2 <= Stage1 or
six-worker Stage1 300s acceptance.

As of 2026-11-27T00:13Z a distinct cumulative immutable source snapshot
at production head 0409a468 follows #645 run
[45909263179](https://github.com/ikshengmin/pcc/actions/runs/45909263179),
exact head bc1094c305384e6c1dcc742fb180fa5b962b9491, observed pending.
Verified first-parent ancestry includes:
cf7762a6 target-qualified freestanding Linux x64 initial-exec TLS
(existing self emitted assembly/object byte-identical, five focused and
33 adjacent tests, contextual626 fallback0; hosted LLVM reference
still UNRUN, no allowance of TLSGD or external resolver);
6aab9ce7 validated coordinator lexical-cache retirement after indexed
initialization ordering (Stage2 memory-only exact acceptance, no full
fixed-point qualification);
0409a468 opt-in managed live slab size distribution at manifests-ready
(native owner and diagnostics verification, no allocation layout or
performance claim). Rejected candidates are not production. No changes
to resource caps, worker counts, deadlines or source/reference tests.

Latest #639 source 4efcdda1473df9f234640751ed3d65bc42d1d4fd verified
all four Stage1 manifest-bound actual receipts: 625/625 modules each.
Compile wall/peak bytes: Linux x64 1163.566s/3018366976 (artifact
15160016178), ARM1573.257s/5043924992 (15160156396), Windows x64
1738.770s/3664633856 (15160695690), macOS ARM3342.503s with observed
peak unavailable (15160584849); entry is separate, macOS avg1.913
fails target avg2 and1800s. Stage2 owner partial/timeouts x64
15161420535 peak3258445824, ARM15161147939 peak4682493952, Win
15160784369 1800.221s deadline/RSS not observed. No complete
Stage2 <= Stage1 qualification. Four worker prior failures still
the historical stale three-root node fixed5f49dbca later.
Java reference actual four red selected-JDK-only normative obligations,
not a PCC output regression; preserve unchanged oracle. Java integration
two additional selected-JDK-only disagreements and two actual unchanged
Commons/ZXing reference field/class-init gaps, #13 frontend remains
frozen until existing runtime/platform/nonregression gate. No waiver.

Existing queued/active runs preserved; exact production parent had
zero workflow runs prior to this normal cumulative source-distinct
checkpoint, no duplicate/cancel/rerun.

Do not claim any hosted lane green from focused tests. The latest completed
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
   Candidate test-only deferred direct execution imports in 45 actual
   function bodies (no proxy), preserving fixture registration and
   exact AST apart from imports. Fresh-import regression failed before
   on all four imports and passed after; affected ordinary resource and
   three external-dependent test files 156 PASS, four integration nodes
   UNRUN, guard34.29s/469204992 B. Actual exclusive paired collection
   at source8139a33d, same two modules, five alternating base/candidate/
   unchanged-base-control triples, exactly identical raw115 nodes/
   six integration, 111 selected without native gate, canonical IDs,
   lane/group/gate and selection digest
   2ff94c2307bcac5e0f767837d96ce5d7218e724d660f86d0c8dc2c750571f496.
   Three imports deferred (pool already imported by actual conftest),
   but median actual process RSS62799872→65208320 B/control62808064
   (+3.84%,0/5) and peak62689280→64954368/control62730240 (+3.61%,0/5).
   Same-base max drift0.04%/0.23%. Existing pycache state across local
   checkouts is not fully matched, no causal loss claim; no meaningful
   measured reduction. Reject/revert candidate and regression source;
   no scheduling, gate or test inventory change. Raw receipt SHA256
   bb5b64a6044d66efffe0fc5cb2bef34fa5d6709fbe9a1c7e22c0e5f9c3bc2c2b.
   Larger collection module parser/assertion-rewrite footprint remains
   a hypothesis, not another authorized scope split.

   Stale sorted-slot ownership model correction (one collector):
   actual unchanged production function at baseline720111d2 and current
   e0628130 no longer retargets/registers GC4 payloads. Independently
   reproduced original 20 failures/48 PASS locally (15.28s). The model
   now witnesses actual publication under graph lock: old and new exact
   owned pointer multisets, poisoned raw allocation tail must be cleared,
   publication before freeing old backing after unlock, new remains
   owned until result owner disposal. Failed result-root transfer must
   free both raw backings in order; replace unreachable obsolete retarget
   fault with two live validation errors (changed work-list shape and
   missing payload). All prior ordering/key/compare/alias/reverse,
   collections, root/disposal and primary TLS assertions retained; same
   production source, 70/70 PASS (16.24s), two additional live error
   witnesses, no test deletion or gate change. Native execution not
   inferred from source model; no optimization claim.

   Native resource-driver stale cardinality: existing loop executes
   only collector0 and all six cases, but old final assertion demanded
   5×6 receipts. Test-first exact six rows failed (1 fail/5 negative
   passes). Shared single-collector tuple now drives the unchanged
   complete case loop and exact ordered (collector,case) inventory
   verification. Seven focused inventory cases pass, including missing,
   duplicate, old collector, Boolean collector, extra and reordered
   rejection; original native node name and all six real driver
   owner/child/diagnostic/return checks preserved. Whole affected
   ordinary file 92 PASS including six real CPython scenarios, two
   native integration nodes UNRUN, guard28.12s/403972096 B under
   unchanged90s/1GiB. No native/host-oracle acceptance inferred.
   Next exact run45862787448/c20c2c01 first-page 30 jobs observed
   20 green/9 red/1 active. All four worker-prior artifacts
   15147002533 Linux x64,15145543606 ARM64,15145659838 Windows,
   15146574584 macOS fail the same stale scalar arena length model
   already fixed by later bd763672 (not in this checkpoint).
   NumPy15146792931: original C missing _numpyconfig.h, C++ string.h
   (later fixed b53d7e49; current generated config frontier remains).
   orjson15145689415 original C yyjson object326912 B PASS; Rust
   pyo3_build_config and cfg_attr remain real unsupported gates.
   Go coverage15145834503 878 PASS/2 FAIL: unchanged unsafe import
   src/errors/join.go:8:2 and math/bits whole dependency closure.
   Java integration15146744195 1255/4 (two JDK-only, two shared
   nominal reference-storage gaps), reference15146948372 204/4
   JDK-only. C++ reference15147168994 1417/9 explicit G++ oracle
   failures, including array-padding oracle returned1 expected42.
   No waiver or claim of current integrated hosted pass.

   C++ array-padding exact external attribution: actual hosted source
   artifact15146969262 unchanged TU370 bytes SHA25620d0492d4bcb71df5012bcd7a8bbc490f1bf3b6a120ff82da97f087f520b0410
   equals current fixture. Hosted owned ELF SHA10c35dcc... receipt42/
   stdout2a000000, original G++13 ELF SHA258e35dc... failed1; original
   hosted oracle binary happens to return42 locally three times. Its
   actual emitted member zero stores cover offsets0,4–11,12 of the
   16-byte object, leaving padding bytes1–3,13–15 untouched, hence
   accidental stack-history-dependent success. Diagnostic-only exact
   same placement construction after explicit 0xff storage prefill
   SHA6110e9c5...: local stock G++14.2 exits1 three times; actual local
   owned self ELF SHAe225eb1a... exits42/stdout2a000000 three times.
   [WG21 N4659](https://www.open-std.org/JTC1/SC22/WG21/docs/papers/2017/n4659.pdf)
   [dcl.init]6.2/8.2 requires class padding zeroing on this
   non-user-provided default constructor's value-initialization.
   [GCC's own 2024 RFC](https://gcc.gnu.org/pipermail/gcc-patches/2024-September/664054.html)
   attributes old automatic struct padding to compiler optimization;
   GCC15.1 documents a standard-guaranteed mode, but the
   [Ubuntu24.04 installed image inventory](https://github.com/actions/runner-images/blob/main/images/ubuntu/Ubuntu2404-Readme.md)
   lists GNU C++12.4,13.3,14.2 only, and
   [GCC DR status](https://gcc.gnu.org/projects/cxx-dr-status.html)
   still has no confirmed CWG2394 implementation release. No qualified
   GCC replacement available or proved for all nine gaps. The same
   c20 checkpoint's dedicated normative platform artifact15147142116
   is genuine Clang++18.1.3 /usr/bin/clang++-18 SHA8ef402d4...
   CAPABILITY_CONFIRMED and all86 original reference/86 owned native
   nodes pass, G++ diagnostic CAPABILITY_MISMATCH. Its positive
   capability is not whole-source reference qualification; current
   explicit independent G++ Source lane policy unchanged. A broad
   normative source provider change needs maintainer choice, real
   whole unchanged source oracle validation including deterministic
   padding probe, and preserved independent G++ evidence. No fixture,
   expectations, compiler policy or CI guard weakened.

   Exact previously failed shared ordinary coverage receipt15145043938
   (run45856871699 source6cbafc4a): lane10489, 5411 PASS,68 true
   failures,2 interrupted,170 prerequisites UNRUN,4838 unattempted
   after guard anomaly. Cross-module None-return ABI fixture one actual
   failure: uninstalled checkout script scripts/pcc_multi.py could not
   import pcc. Reproduced exact original node after clearing inherited
   PYTHONPATH locally (1 fail/1 PASS); bind checkout root explicitly
   only to existing script subprocess, same command and cwd. Actual
   returned combined IR still verifies every declare/define return
   match, sync None void, async None coroutine ptr, and direct call
   ABI, unchanged assertions. Whole affected file 2/2 PASS; guard
   COMPLETE4.118s/110710784 B under unchanged90s/1GiB. No compiler
   production, test expectation or resource policy change.
   Remaining actual failures grouped by source file: Darwin TLS default
   signing16; freestanding errno LLVM2; frame registry LLVM1 and flags1;
   generator bulk2/init1/owner transfer2 removed selector; generator
   frame root initialization25; handoff receipt1; imported constructor
   pinned ABI6; Darwin closed-world ownership1; industrial worker
   memory1; IR scaffold integer8. Independent corrections pending;
   no hosted lane acceptance inferred.

   Darwin TLS protection model (Linux object proof, 2026-11-26):
   baseline default-off compiler reproduced 16 stale PAC expectations
   (3 PASS, 2 real Darwin execution UNRUN). Explicit opt-in restores
   all 16 signed direct/indexed internal/external TLS cases, preserving
   PACIASP-before-frame-save and AUTIASP-after-restore around real BLR.
   New 16 default plain cases require exact balanced aligned SP16/LR
   save and restore around hidden BLR (no PAC/AUT/BTI/full frame pair).
   True leaf retains BTI-only opt-in; explicit off also verified.
   All 35 model/object cases PASS; four actual native Darwin driver
   cases UNRUN on Linux, not waived. No production or policy change.

   Generator C fixtures (Linux native, 2026-11-26): strict-admitted
   current source/codegen threaded atomic runtime archive SHA256
   a853269963429ae3d6c193c5d86c0226878d3cee354881c6ed58c3e439f901ac.
   Baseline four exact C probes all FAIL undefined static ELF
   pcc_gc_set_backend (guard COMPLETE19.921s/104722432 B). Removed
   retired selector and unreachable multi-collector branches only.
   Native probes now enforce successful bulk stores (including aliases),
   invalid save nonmutation, fixed sizes 0/1/33 initialized to None,
   traced child survival, 10 take/save round trips, retaining-getter
   negative exit2, invalid destination rejection, owned/same-value
   transfer, borrowed retention, and terminal allocation-count baseline.
   Four probes PASS under same90s/1GiB guard COMPLETE10.321s/107581440 B.
   Whole three files 8 PASS/1 pre-existing IR model failure (bulk
   rejected experiment expects old frame_set but current resume emits
   frame_move), guard COMPLETE22.064s/144093184 B. No production,
   native test or resource policy change; remaining model independent.

   Generator rejected-bulk-save IR control (2026-11-26): prior
   whole-file run showed the sole remaining stale frame_set save
   expectation failing while the native probes passed. Current owner
   moves are checked for both disabled/enabled rejected experiment
   settings: zero bulk calls, exactly six private frame moves, each
   yield ordered slot0 seed/1 first/2 second, distinct true owned SSA
   inputs, excluding terminal frame_set(None). Focused 1/1 PASS on
   unchanged production source, replacing the old stale model rather
   than removing the assertion. Prior native 8 PASS unaffected.

   Imported constructor integer ABI (Linux owned ELF, 2026-11-26):
   hosted artifact15145043938 six exact old pinned-slot assertions
   reproduced locally (6 FAIL/4 PASS). The sole collector uses the
   ordinary owned result slot. Test now proves concrete constructor
   result local registered before allocation, allocation published to
   that slot, receiver reloaded into initializer, exception cleanup,
   local leave and same-slot take, shared return-root store/register,
   reload/clear/retire and actual returned SSA. All original ABI
   define/declare/call physical ptr/i64 lane checks, owned ELF emits
   and no CPython retained, plus absence of retired pin. 10/10 PASS
   on unchanged production source (3.73s); no hosted acceptance
   inferred.

   Generator frame root initialization (real IR, 2026-11-26):
   hosted coverage artifact15145043938 all25 FAIL obsolete "borrowed
   owner overwritten" model reproduced on current unchanged codegen
   locally (4.91s). Production emit_generator_borrowed_owner_retirement
   deliberately clears both private nonowning ABI copies at completed
   resume return, after last use; activation failure never publishes.
   Model still requires original borrowed values before registration,
   full owning NULL initialization, exact restore/reload/publication,
   and now exact local/mapped ABI CFG retirement. Each reachable rooted
   return must clear both borrowed physical slots once, only after
   frame retirement with no following read/callback/effect except
   recursion cleanup; activation-failure return must stay uninitialized.
   All original20 real IR shape/toggle/target cases and five mutation
   controls preserved; three new controls reject absent/early/read
   after terminal clear. All28 PASS (5.23s) on unchanged production.
   No native GC coverage claim or gate waiver.

   Iterator root models and genuine native lambda retirement fix
   (2026-11-26, frozen source7a099ce3): exact former14 module/2 dynamic
   model failures reproduced (16 FAIL1.68s). Current root APIs require
   actual local registration, concrete owner publication and same
   physical iterator read, exact normal/error clears while active and
   reachable matching frame exits. Private loop cursor is scalar, not
   a GC root; its owned slot is passed to py_obj_private_for_next,
   whose runtime source reads pcc_gc_load_ptr initially, after length
   and on fallback. Dynamic target consumes the exact next result;
   store_root_take runtime publishes before releasing previous owner,
   and current target reads and guarded error+normal clears verified.
   Existing target preparation and x64 stackmap proof preserved.
   Stronger model exposed REAL native lambda successful return leaving
   its regular comprehension root registered: old source14 PASS/2
   FAIL exact CFG (call.slot.ready.206), error already retired.
   Actual contextual native valueclass legacy-capture comprehension
   reproduces the same missing leave on both targets (2 FAIL1.34s).
   Fixed only both lambda success epilogues, retiring registered
   expression frames after argument cleanup while separate lexical
   result root still owns value, before caller result handoff. Error
   path unchanged. All original16 plus two legacy-target models PASS,
   18/18; adjacent68 models PASS. Changed module exact 625-source
   context fallback0, guarded COMPLETE95.270s/676814848 B under
   600s/1.5GiB cap/reserve2GiB. Fresh source/codegen-admitted threaded
   atomic runtime SHA256
   a86281a54cb58ab1bce638e0ff456f4e9d5c5bf4225818db49970c928560c697
   guard COMPLETE102.500s/387371008 B. Actual new two native lambda
   adapters, three repeats, callback error and 15 finalizers with
   nested gc.collect compare exact CPython stdout, emitted full frame
   ledger and no CPython calls; plus two existing native lambda
   tests, 3/3 PASS, unchanged90s/1GiB guard COMPLETE6.654s/110702592 B.
   No gate weakening or hosted acceptance claim.

   Frame registry flags / stackmap policy (2026-11-26, frozen
   source10f25029 strict archive a86281a5): hosted artifact15145043938
   stale expected walk_state1 on Linux reproduced locally1 FAIL/2 PASS,
   actual [1,0,0,1,0], guard COMPLETE4.886s/103460864 B. Runtime mode
   lazily caches only a validated published table; current target
   contract Mach-O owned link publishes, ELF/PE main does not (including
   experimental object tables). Existing application retains all
   40 rooted chain/GC semantic results (sum517140) and configuration
   flag conjunction, checks actual table pointer and explicitly calls
   native mode. Linux observed flags [1,0,0,1,0,0,0,0,1,0]:
   no table/mode0/walk_state0/index0, policy setting1; ignored requested
   "0" gives identical result. For actual linked Mach-O table requires
   publication, mode1/state1 and validated index>0; native Darwin UNRUN.
   Independent owned C native control uses backend-encoded one-function
   table resolved to actual native probe's address, validates no-table
   mode0 before publishing, valid mode1 default/ignored0, report2, state1
   and index1, malformed magic fails closed with native abort reason.
   This tests mode/index admission, not a claim of ELF program publication
   or actual stack traversal. All4 affected plus5 explicit platform
   policy controls PASS, 9/9 under unchanged90s/1GiB guard
   COMPLETE5.630s/95928320 B. No production or gate change.

   Scaffold integer ABI result ownership (real owned ELF, 2026-11-26):
   hosted artifact15145043938 eight old pin/publication-failure-label
   assertions reproduced on current frozen5a0d90f4 source (8 FAIL/12
   PASS,90s/1GiB guard COMPLETE31.918s/90099712 B). The sole collector
   publishes result by an infallible direct store to the registered
   activation local; dead old publication failure labels cannot prove
   lifetime. All original provider ABI, one len/one box, strict native
   no CPython and raw/manual rejection controls retained. New physical
   result proof checks exact registration/bounds, borrowed first arg
   group, actual pending-error CFG, one boxed owner release on EVERY
   reachable post-producer exit (no release of borrowed arg), exact
   exception-preserving result cleanup before leave, success fresh
   same-cell load for caller sink or owner take after argument cleanup,
   and full local/mapped/LIFO CFG frame ledger. Three mutations of
   actual emitted IR reject missing publication, double release and
   missing error cleanup. All20 PASS, guard COMPLETE29.719s/88203264 B
   unchanged production and resource policy; host ELF model, not a
   native runtime/hosted CI acceptance claim.

   Darwin-only indexed handoff prerequisite (2026-11-26): hosted
   Linux ordinary artifact15145043938 selected the explicitly
   Darwin-arm64-only closure node and failed its unchanged host
   assertion. Local exact file reproduced1 FAIL/1 PASS; inventory
   raw2 selected2, no prerequisite. Actual dedicated Mac indexed
   profile receipt15146648514 (c20c2c01 run45862787448) ran same exact
   node setup/call/teardown PASS,17.93s/JUnit1, no skips; 5-provider
   closure/543 definitions/29 actual helpers/6 body-negative controls.
   This is host-pcc0 compile-only, not native program execution.
   Current pre-marker test/provider SHA match that receipt; current
   compiler different, so current Mac gate still UNRUN.
   Added the exact declarative Darwin AND arm64 pcc_gate, retaining
   original in-body native host requirement and dedicated profile.
   New contract test failed before marker; after change actual Linux
   source inventory raw2 canonical nodes intact, selected1, exact
   closure node retained as ordinary UNRUN_PREREQUISITE (not PASS);
   source campaign remains INCOMPLETE_UNRUN absent separate transfer.
   Dedicated execution verifier rejects actual Linux collection;
   negative host gate controls linux x64/arm, Darwin x64/Windows and
   positive Darwin arm64 admission model retain exact node policy.
   Guarded13 PASS/1 genuine Mac prerequisite deselected,
   COMPLETE1.289s/45268992 B unchanged90s/1GiB. No ownership transfer,
   test-body, resource limit or coverage acceptance change.

   Indexed handoff exact source owner transfer (2026-11-26):
   one manifest override only: shared/ordinary canonical
   test_handoff_provider_closed_world_aarch64 -> macos-arm64, no
   external tools. Prior real dedicated c20 Mac node exact receipt
   15146648514 had17.93s call/57samples peak tree315916288 B,
   largest process268271616 B; no claim this proves six-worker source
   budget. Separate dedicated gate remains unchanged. Existing
   same-run source-platform job already schedules shared ordinary
   macos-15, exact github.sha, no Linux Stage1 input on Mac; owner
   modules now includes the one test file. Actual Linux raw inventory
   from prior unit passed production partition: ordinary census
   remains local, exact blocked Darwin node stays raw and becomes
   TRANSFER_PENDING, Linux status PLATFORM_COVERAGE_PENDING (not
   PASS). Aggregate independently requires actual same-run Mac
   source coverage artifact and full phases; dedicated receipt cannot
   substitute. Candidate manifest SHA256
   4d2c4cecf8ff19c1ffeed1e4a165fe3a151a87d9459ba99ab1a1ea403718564f.
   Test-first10 exact transfer/aggregate models failed without rule;
   final adds stale provider identity control; all95 existing owner,
   coverage and focused dedicated gate models PASS. Negative exact
   node controls reject missing owner even with green dedicated
   artifact, stale test/provider source/SHA/run/attempt, wrong host,
   absent call and owner deselection. Linux native input lease
   shape modeled, not real native admission. Initial physical reserve
   refusal retained; eventual unchanged90s/1GiB guard COMPLETE
   1.813s/50298880 B. No coverage/workflow/resource code change.
   Current same-run Mac source execution remains UNRUN; no CI green.

   Freestanding errno target-honest callback (2026-11-26, frozen
   e1ac8f39): hosted artifact 15145043938 self raw closure
   pcc_null_callee_trap reproduced by owned ELF/native test; old
   8 FAIL/3 PASS guard 5.650s/116920320B. Darwin-only library symbol
   returns NULL on non-Darwin; existing general raw NULL trap remains
   fatal. Optional supported literal target applies only to exact
   int32(ptr/int32/ptr/uint64) raw callback; nonmatch -1 before
   any operands, same/unguarded unchanged, rejects invalid literal,
   dynamic, empty, arity and kwargs. Actual Linux owned ELF no
   undefined; GCC C semantic harness confirms independent libc/PCC
   errno and three thread TLS addresses, all 134 glibc strerror
   messages and native matched/unguarded signed32/uint64 ABI, guarded
   mismatch. Darwin cross owned Mach-O named callbacks and exact
   legitimate TLV descriptor bootstrap relocation; true Darwin
   execution UNRUN. Actual 13 PASS/1 LLVM reference local UNRUN
   (clang absent); unchanged 90s/1GiB guard COMPLETE 3.089s/90554368B.
   Hosted LLVM reference tls_get_addr unresolved; exact assertion
   unchanged. Adjacent general trap/unsafe target tests 8 PASS,
   guard 1.540s/83902464B. Exact changed unsafe_lowering 625-source
   contextual fallback 0, guard 600s/1.5GiB COMPLETE
   74.619s/772186112B. No production toolchain, resource/gating or
   general trap weakening. Latest integrated hosted CI UNRUN.

   Indexed handoff missing receipt strict retirement failure
   (2026-11-26 frozen 5d113086): exact hosted artifact 15145043938
   missing_receipt node reproduced old expected OSError vs actual
   HandoffError, 1 FAIL/18 PASS guard COMPLETE 1.555s/62300160B.
   The required receipt is rejected as a nonregular file before
   unlink, unlike OS failures of actual validated-input unlink.
   Test now expects exact HandoffError and exact coordinator receipt
   path, checks entered retirement but no successful return, missing
   receipt stays absent and request/output preserved. All existing
   seal/sidecar/companion/decoy preservation, no successor, no
   observation release and other failure cases unchanged. Whole
   retirement plus indexed handoff suite 50 PASS/48 subtests,
   guard COMPLETE 2.064s/72445952B under unchanged 90s/1GiB.
   No production or gate change; integrated hosted CI UNRUN.

   External class boxed-result formal dispatch correctness
   (2026-11-26 frozen fae0dcd2): old Stage2 host witness direct
   user_owner_Parent_ping assertion fails, exact repeat reproduced.
   Global unique preload retains owner.Parent type but indexed
   dependency closure intentionally has no owner shard without an
   import. Restoring it unguarded is WRONG: actual erased Duck/None
   source compiles with imported/qualified formal Parent and calls
   fixed Parent_ping even on Duck and None. Strict frozen archive
   770606d6b0e3d653bc52b14295275dc47b74eb989eab9df69ee64aead101c775;
   CPython and unique-unimported owned ELF b2bf6e35... output
   7/42/attribute-error, imported owned ELF2011936d... outputs7/7/7,
   guard9.946s/96694272B. Test-first actual imported and qualified
   both IR/native fail, 4 FAIL/4 PASS, guard27.395s/116420608B.
   No global preload closure expansion. Only an ordinary boxed
   pointer result method on a cross-module class formal whose live
   receiver annotation matches its declared class moves through
   existing live binding classifier/result owner; concrete
   constructor, raw/valueclass, explicit C ABI and may-park
   routes unchanged. Broad void/scalar guard rejected due existing
   optional arena ABI evidence. New narrow candidate exact strict
   runtime COMPLETE111.271s/387551232B, archive bytes identical
   770606d6... with updated codegen provenance; contextual625
   fallback0 COMPLETE97.022s/710217728B. Final44/44 PASS under
   unchanged90s/1GiB guard34.518s/126259200B: all four real owned
   ELF integration cases preserve int7/42, boxed string parent/duck,
   None AttributeError, exact ctor direct7, subclass19, property31
   and class mutation27, no CPython fallback in the host IR models.
   Duplicate unqualified owner remains unresolved, qualified and
   unique inference keep exact owner identity, existing aggregate
   ABI and optional Arena void direct contract retained. Separate
   remaining genuine void Optional annotation bug: diagnostic
   three-module erased Duck native ELF99131119150e98836e575acdec46bd942ffb0582560d1de525015faccb3de4ee
   output parent/parent vs CPython parent/duck with None guard,
   guard4.355s/74022912B; no fix/admission yet. Next owner must
   preserve actual live dispatch or exact guarded internal ABI,
   not assume annotation/non-null is a runtime type proof. No full
   Stage1/2, Darwin native or integrated hosted acceptance claimed.

   Pressure Stage1 compact source lease (2026-11-26, frozen
   a91caaaf): actual hosted c20c2c01 Mac integration artifact
   15151930783 pressure admission FAIL before build/tests/ordinary,
   guard COMPLETE2.020s/65716224B, residual1178s after owner621.39s.
   Full source receipt has commit/hash/clean/count/scope, admitted
   compact bundle has commit/hash/clean only; actual source commit
   and hash, producer and Mac platform match. Whole-dict comparison
   in execute and final summarize therefore rejects valid same-run
   input. Common strict projection requires both clean True, exact
   valid 40-char commit and 64-char hash, never missing values.
   Full source receipt count/scope and before/after equality remain;
   same producer/platform/manifest hash/artifact checks unchanged,
   summary also rechecks actual current manifest bytes against
   admitted checksum. Test-first real schema 6 FAIL/25 PASS guard
   0.780s/45359104B (valid execute and summary rejected, actual
   lock/build/provenance/test/mutate phases never reached).
   Candidate focused31/31 PASS guard0.529s/36503552B with stale
   commit/hash/missing/dirty/lease/platform/manifest bytes/full-count
   mutation and exact subsequent phase/lock/inventory controls.
   Full marker/pressure/bundle suites123 PASS/2 FAIL under local
   installed pytest9.2.0/xdist3.8 (hosted pinned pytest9.0.3);
   two existing real six-worker loadgroup nodeid suffix checks
   fail unchanged source too (actual2FAIL guard2.841s/241172480B).
   No worker/group/test inventory or resource relaxation. Mac
   pressure build/execution still UNRUN; current hosted CI UNRUN.
   Separate actual Mac not-integration artifact15151916012 reached
   collection only, MEMORY_LIMIT124.954s/2885287936B vs cap
   2880356352B, six workers, largest472285184B, no test outcomes.
   Do not infer pass or increase cap.

   Ordinary external void live receiver guard (2026-11-26,
   frozen0846c755): original Optional Parent formal/typed nested
   alias still called Parent body for erased Duck and None; stronger
   source/IR/native test-first4 FAIL/1 PASS guard16.348s/114995200B.
   New direct branch is admitted only for actual source-owned
   void_managed_pointer_abi exact Boolean true on provider method,
   explicit normal instance void, untyped/definite managed object
   arguments or actual ordinary boxed int; explicit raw machine,
   scalar, valueclass, unresolved source type aliases, old/malformed
   proof and low-level/resumable ABI excluded. Actual canonical
   source method row/body symbol/native adapter, unique source
   symbol owner and no adapter collision, complete positional
   pointer parameters, no closure or speculative alias declaration.
   Live py_obj_load_method resolves before args; receiver/method/args
   evaluate once, stay rooted, exact bound receiver and captured
   native entry check choose body, otherwise invoke same captured
   callable on same arguments. Direct Python None/result owner
   and exception cleanup preserved. Concrete ctor unchanged;
   noncanonical but proven-managed source uses ordinary live
   fallback. Existing optional Arena and nested direct body
   assertion retained and strengthened to require actual live
   guard/fallback, not annotation/non-null proof.
   Host19 PASS/6 integration not selected, guard51.075s/356630528B.
   Final source metadata/exports/type-wire adjacent67 PASS
   guard6.903s/120119296B. Fresh strict candidate runtime
   COMPLETE111.634s/390270976B under tighter1GiB+2GiB reserve;
   archive770606d6... identical bytes with current source/codegen
   proof. Actual final uninstrumented native6/6 PASS guard
   30.966s/121155584B: boxed previous controls plus Parent/Duck/
   None, typed nested alias, exact ctor, subclass, descriptor lookup
   before argument, class rebind during argument preserving captured
   old method, later replacement, argument exception/recovery.
   Diagnostic exact original final IR SHA4246d5e4... only branch
   write markers, owned static ELF a5ae78d9... same exact semantic
   bytes, stderr FDF = cold Parent fallback, warm Parent exact
   native adapter direct, Duck fallback, guard2.831s/56737792B.
   Three actual contextual625 fallback0: method_call_expression
   80.184s/751398912B, pipeline_exports99.084s/674033664B,
   pipeline_context83.420s/664403968B under tighter1GiB.
   Separate genuine raw integer export loss discovered: source
   pcc.i64 encoded identical to ordinary int, untouched0846 IR
   ce8d3ad8... caller void(ptr,ptr) vs actual provider void(ptr,i64).
   Three-module raw native ELFbbb961df... output151 vs CPython37,
   guard60.543s/373342208B; includes an independent analogous
   free-function import edge. Raw source flag false/no new guard,
   not claimed correct. Next isolate two-module method-only exact
   named raw ABI descriptor and signed/unsigned boundaries; the
   free-function route remains independently open. No integrated
   hosted/native-pcc1/Stage1/2 or performance acceptance claimed.

   - 2026-11-26 exact cross-module raw machine integer method ABI: isolated exact baseline `cd15c218` owned ELF printed 75 rather than CPython 37 because the method metadata erased the explicit machine lane to boxed int; candidate preserves source-owned `pcc.i64`/`pcc.u64` only for method parameter/call-signature/return descriptors, validates exact named 64-bit signedness before the generic tuple cache (rejects non-Boolean alias), legacy ordinary int remains ordinary and free-function wire unchanged. Test-first nine failures → nine passes, exact indexed and legacy wire round trips, native provider and consumer signatures agree. Fresh source-provenance runtime archive SHA `770606d6b0e3d653bc52b14295275dc47b74eb989eab9df69ee64aead101c775` re-admitted in guard 88.785s / 384,147,456B. Isolated native output 37 exact (candidate ELF `39fa245486b333f2da473dc141ae1d955bb7d661b13c9b1a8b0da2a2b4b753bf`, guard 33.752s / 356,909,056B); full owned ELF `88736f7462ad50c029cd12563440871275acfef9009a317a796bc88ffdcc9e10` signed minimum/maximum, unsigned 0/2^63/2^64−1 and ordinary boxed 42 exact against CPython (33.313s / 355,508,224B). Adjacent host 110 pass / six integration deselected (25.707s / 120,111,104B); strict contextual closure 625 modules with zero fallback for `pipeline_exports` (62.722s / 674,816,000B), `pipeline_context` (69.813s / 662,159,360B), `export_meta` (63.464s / 612,593,664B), each under 1 GiB cap / 2 GiB reserve, unchanged 600s contextual/90s native guards. No broad free-function or field ABI migration; independent hosted native pcc1/platform, Stage2 full and performance acceptance remain UNRUN for this change.
   - 2026-11-26 exact package parity retired direct libpython contract: hosted c20 run 45862787448 Mac job 140115980296 private overlay PASS, package gate 47 PASS / one failed obsolete positive assertion line 485 (`test_install_blocks_libpython_linked_artifact_in_pcc_native_mode`), after original native rejection passed. Direct route intentionally retired in `15b7a2a3`; current native linkage scanner rejects direct libpython even in `cpython-compat`. Exact local baseline failed original positive, candidate test-only retains all original `pcc-native` rejection assertions and additionally checks legacy `libpython` and `cpython-compat` direct edge rejection, existing PCC-PKG-003 diagnostic, failure manifest and no successful cache record. Neighbor distinct CPython extension ABI without direct edge remains positive in `cpython-compat`. Focused 2/2 PASS; full file exact baseline 9 failures / 16 passes / 1 deselected to candidate 8 failures / 17 passes / 1 deselected, identical eight unrelated local CLI uv / no current exact pcc1 failures. Adjacent linkage/ABI model 22 passes / 3 failures (two local toolchain prerequisites; separate stale positive legacy label at `test_package_abi_mode_labels.py:64` still pending separate contract). No production route restored, no test skipped, no cap change, native Mac current full gate UNRUN.
   - 2026-11-26 exact retired legacy package linkage ABI labels fixture: baseline `2de9831d` host model 1 failure / 4 passes, stale positive at line 64 was a direct libpython edge plus CPython extension ABI and cannot earn compatibility SUCCESS after the documented `15b7a2a3` removal. Candidate test-only preserves direct edge recognition and PCC-PKG-003 rejection in all three labels, top/per-scan/capability no native claim; separately an identical CPython extension ABI without direct edge can pass only the supported `cpython-compat` mode, not retired `libpython` or `pcc-native`, with PCC-PKG-004 and no native claim preserved. Existing native rejection, empty scan and exact host/native capability model unaffected. All six ABI label tests plus two adjacent install/cache tests PASS under unchanged 90s/1 GiB/2 GiB reserve. No production route restored. Independent native pcc1 ABI label fixture still has retired positive expectation and needs current artifact; generic invalid label pure no-edge linkage report appears to return ok/native claim (actual `libpython` with clean ELF fixture), a separate contract issue not disguised as fixed here.
   - 2026-11-26 invalid package ABI admission production contract: exact baseline `8e608588` host report admitted a clean artifact with retired `libpython` as native claim; native source shim reported ok and inconsistent nested capability, both installer owners could reuse an old invalid success receipt/acquire invalid mode. Shared schema now only supports exact `pcc-native` and `cpython-compat`, labels all others unsupported; both report owners fail closed at top/per-scan/capability, append PCC-PKG-ABI-MODE-INVALID after real edge diagnostics. Both install owners reject before acquisition/build/extraction/reinstall/cache; only explicitly named local artifact/root scanned diagnostically, isolated `.pcc-invalid-abi/pcc-package.json` failure receipt preserves existing site/cache and actual direct PCC-PKG-003. Exact new test-first 22 fails / 2 supported controls pass → focused 32 PASS; expanded host/native-source tests 54 PASS, one unchanged actual current pcc1 binary prerequisite unavailable, not skipped or claimed green. Includes old matching invalid installed and cache receipts/bytes unchanged, explicit no-acquisition checks, valid clean install/reinstall/cache and compat/non-claim controls. Actual pcc1 stale ABI-label test replaced with stronger direct-edge rejection and no-edge compat control, plus unsupported clean/empty negative; all four actual nodes collect, remain UNRUN without current binary (host native-source shim is not binary qualification). Strict current closure 625, zero fallback for `schema` 72.869s/611,409,920B, `linkage` 61.415s/626,356,224B, `install` 71.314s/664,338,432B, `cli_bootstrap` 72.726s/865,423,360B each under unchanged 600s/1GiB guard and 2GiB reserve. No libpython route restored; no performance or full Stage2/native Mac claim.
   - 2026-11-26 four-platform hosted worker-prior default-xdist failure exact root: run45868668374 clean `9223aca1` artifacts x64 15153242696, ARM 15152144841, Win 15153212753 (each 2649 PASS / one FAIL), Mac 15153706554 (2656 PASS / one FAIL), outer digests and JUnit checked independently. All same stale `test_owned_cleanup_helper_ir.py::test_ineligible_shapes_keep_inline_tls_protocol[three-roots]` AttributeError Recorder module has no real `_functions`, because current production cleanup helper accepts 1–5 simple LIFO root owners, not only 1–2. No resource failure. Exact current baseline `3fd18d37` 1F17P same stack; test-only candidate full module24/24 PASS with explicit 3–5 recorder helper positives, actual emitted caller signature/owner registration/reverse retirement and stack maps, actual runtime helper 1–5 ABI and single TLS exception protection, reverse clear, no borrowed load or hidden frame transition; six-root inline retains two swaps and exact reverse disposal/retirement; all flagged/non-LIFO old controls retained. Adjacent host nonintegration slice baseline4F108P/5 deselected → candidate3F115P/5 deselected, exact unchanged three preexisting `test_virtual_thread_spawn_slot_ownership` indirect mapped-name and `test_slot_call_lexical_roots` explicit Linux target frame model failures. No production change, no worker/cap/inventory waiver; full new hosted worker-prior remains UNRUN, adjacent independent models pending.
   - 2026-11-26 exact cross-module raw machine integer free-function ABI: isolated current baseline `89a57547` actual owned ELF `6f8d19070554ac42566465b643a7d0696ccff9a7776a993aa6313b94d9edcbea` printed 75 instead of CPython 37 (guard 29.618s/357,036,032B), host two fails including lost explicit pcc.i64 return type. Extend already-admitted exact raw descriptor (7d3c7540 method-only) to source top-level function return/parameter/call-signature, no generic encode_type, ordinary boxed int or field ABI migration. Exact indexed/legacy wire and actual provider/consumer IR signatures, host 2/2; adjacent 102 pass/4 integration deselected. Strict baseline/current runtime archive bytes `4af42eebe7a7fb091d76e7cafb55db029e4a74f23d387281c74a586dd8184580`, separately verified source provenance (83.078s/382,898,176B and 81.068s/384,270,336B). Candidate isolated ELF `9e46bacc86f0cdf69572e4a8ca2a4ba84910dcc453ef6948bd74694c830b3333` exact37 (30.031s/357,138,432B); full ELF `f3e4a43d71c24ea55ecbed9e69b21555f739773d7156aa762e0a00ef06f26732` signed min/max, unsigned0/2^63/2^64−1 and ordinary42 exact against CPython (28.375s/358,268,928B). Both changed compiler modules strict contextual closure626 (one additional de4eb2b helper) zero fallback: exports61.662s/675,311,616B, context66.780s/666,554,368B, unchanged1GiB cap/2GiB reserve and guards90s native/600s context. Whole Stage2/native current pcc1 performance and hosted acceptance UNRUN; no broad ABI migration.
   - 2026-11-26 independently correct AMD64 typed unwind section admission: actual #638 source ordinary failure artifact15157321838 `test_frame_registry_object_has_exact_raw_closure[llvm]`, exact unchanged hosted object SHA `b108932117af47802f3ed3b0531c52f695314a7eb7b7fe6696652b9503598c8a` (IR SHA83035a00) baseline current `f3813638` rejected `.eh_frame` type1879048193. Independent readelf shows X86_64_UNWIND 0x70000001, read-only alloc, align8, link/info/entsize0, 36 PC32 `.rela.eh_frame` records. Owner adds only this exact machine/name/flags type branch in parser and model, preserves file-backed payload/type/relocations and all generic validation; unknown machine/name/non-alloc/write/exec/TLS flags rejected. Test-first2F6P→8/8; owned typed section roundtrip ordinary/compact and static image byte-identical to same PROGBITS frame receiving relocation. Exact hosted object now advances to **still BLOCKED** six unsupported TLSGD relocations type19 and external `__tls_get_addr`; do not broaden link relocation contract or claim LLVM source gate green. Strict contextual626 fallback0 66.004s / 718,180,352B. Existing frozen source reference adapters initially61 hash failures; exact new whole owner da574987→old a99b05a0 three-hunk inverse authenticates previous digest and preserves old ODR/host/packed source hashes, missing/damaged/duplicated/unrelated controls. Source control suite50/50, same adjacent scope165/165 after inverse (prior104 pass/61 fail hash), original actual self closure node1/1; one original LLVM reference replay remains blocked, not skipped from hosted. No performance claim. Next distinct target-qualified freestanding x64 Linux explicit initialexec TLS metadata (owned backend already maps default and initialexec to GOTTPOFF), preserve TLS address values and cross-platform defaults, actual reference hosted verification required; no generic TLS model change.
   - 2026-11-26 target-qualified x86 Linux freestanding TLS contract: actual #638 exact LLVM frame registry source artifact bare `thread_local global` on three own pool globals emitted six R_X86_64_TLSGD and external `__tls_get_addr` (next actual blocker after typed unwind 5f2ba802); owned backend already accepts `default` and `initialexec` and uses GOTTPOFF/static TLS, rejects unsupported general-dynamic/localexec IR. Candidate makes that existing freestanding x64 Linux source ABI explicit `thread_local(initialexec)` for ptr/i32 definitions, preserves TLS value/address intrinsic detection, all other target/default/nonfreestanding definitions unchanged. Exact baseline ae2be257 test-first2F3P→5/5 target/actual source model tests, actual same frame registry baseline/candidate emitted IR byte-identical after only explicit spelling normalization; actual owned asm exact SHA `d9a74777bee04244b2562b4879a91caed08de765061bc9be8cb2718b5a30bcd1`, actual object byte-identical SHA `4c2939177e1448265002ff1027b501efab5c77c930d33d42ec02b09398534346` and original strict self RAW symbol closure. Adjacent33/33 guard6.897s/108,158,976B incl actual four owned target frame-registry objects, actual Linux self errno thread isolation via existing separate cc reference harness, all TLS address/invalid controls. Strict contextual626 zero fallback 73.821s/830,853,120B under unchanged1GiB/2GiB reserve. Existing tests source expectations adjust only x64 freestanding model, actual LLVM reference test unchanged and UNRUN locally (clang absent); hosted reference verification required, do not claim entire CI or TLSGD support. No generic TLS model or linker relaxation, no performance claim.
   - 2026-11-27 exact current private lexical root models: prior adjacent three baseline failures reproduced on clean adaabfa1, 3 FAIL / 2 PASS (same-name indirect mutation StopIteration for old mapped spellings; explicit Linux target old LIFO symbol absent). Positive actual generated spawn default and explicit Linux target use local enter/leave; explicit table-on generated spawn uses local-mapped enter/leave. Test-only two files now explicitly exercise table off/on for each of four same-name indirect negative controls (eight total), retain real dominating function pointer, parsed verifier, one exact indirect call, and actual owning registration/retirement rejection. Linux-on-Darwin target exact local enter/leave with no mapped calls, independent complete stackmap planning. Whole affected/adjacent ordinary scope 126 PASS, two native integration nodes deselected/UNRUN, unchanged90s/1GiB guard +2GiB reserve; baseline1.817s/80,666,624B, candidate17.879s/116,789,248B. No production, resource, test deletion or gate change; native spawn retired selector remains separate.
   - 2026-11-27 user-priority Linux instruction metric: `pcc_perf.py instr BASE CONTROL --counter-backend callgrind` explicit optional Linux Valgrind, auto retains Darwin time / Linux perf. Exact one process, all threads combined, no child exec tracing, one complete final profile; validates actual native_stats timed and two warm-up batch request counts before differentiating 10 vs50 rounds (800 requests at C20). Official [Callgrind format](https://valgrind.org/docs/manual/cl-format.html) whole-run summary Ir retained, optional exclusive totals sanity <=summary, no inclusive edge sum, malformed/duplicates/multi-path fail closed. Simulated user instruction reads explicitly labeled, cycles/IPC unavailable (not estimated), no function attribution or hardware-equivalence claim. Test-first15 FAIL missing API, candidate all16 new parser/runner/availability/CLI controls PASS (mock runner, not actual Valgrind); full original+new 102 PASS /1 existing CPU profiler hard-exit sample threshold 100>100 (prior101/1), exact alternating baseline and candidate two pairs all same actual100 sample failure; earlier baseline isolated once PASS. No sampler change/waiver. Actual CLI exit1 diagnostics for Valgrind and perf missing. Linux host has neither tool/apt cache, actual user-only perf_event_open hardware count denied ENOENT; no actual dynamic count, cycles or IPC. No whole test suite pass claim or new CI dispatch. Existing queue preserved per user.
   - 2026-11-27 exact native Unicode splitlines content correctness: Stage2 marker baseline source b3dc0990, actual ELF180a0aef and runtime owner source blob3ddb468d exact current; eight real Unicode content differences (not identity). Fresh strict independent baseline source0368a95b archive16b320d2, 87.730s/385,118,208B; actual ELF314783f9 returned0 empty stderr but 455/704 rows differ against CPython over88 inputs, including 60 bare/positionalFalse each and44 positionalTrue per repetition. Separately exposed dynamic keepends False incorrectly treated True in existing lowering (83/88; full original diagnostic retained, not waived). Current runtime scope only: exact ASCII VT/FF/FS/GS/RS and UTF8 C2 85 / E2 80 A8,A9 with bounds, existing CRLF coalescing/append ownership/terminal semantics preserved; no generic whitespace or identity change. Candidate source426ed2c7 strict self-owned runtime archive8b0dd1b51c9f77f0dbfc322f67406b74db65967c9203c6e6adeb8eb698fafb2a 89.783s/386,019,328B, provenance4b64f291, changed actual member ce43476118a643f4f2a19839eaf361e611296ed2b15aac3dd779c0ffffaa1cf9 self emitter no host cc, IR46a2f80d no stub/fallback. Exact current native ELF ddc604f4c35bc53e91f3bb3967d5aba3590039e50cfe17208183de42eac6f828 matches CPython numeric bytes for88 inputs × five constant forms including all boundaries, multibyte near misses, NUL, CRLF, empty/trailing/repeated. Adjacent split maxsplit controls too, 3/3 under unchanged90s/1GiB guard +2GiB reserve (4.914s/88,248,320B). Current pcc1 integration node remains UNRUN, not a native compiler fixed point or performance claim. Dynamic keepends argument evaluation remains separate actual compiler defect. No new CI dispatch.
   - 2026-11-27 user-priority optional Callgrind per-function self Ir attribution: `pcc_perf.py instr BASE CONTROL --counter-backend callgrind --functions 20` (same measured short/long runs, no additional workload). Stream official format one part/event order and multiple/compressed subpositions; compressed fn/cfn and ob/cob alias views, inline file metadata does not change enclosing symbol; skip exactly cost row after calls= (inclusive), never add call costs. Group by reported object+symbol (not unique physical function), validate represented exclusive vector against optional totals and <=summary; explicit unknown symbols and unrepresented summary difference. Each run raw group differential reconciles exact total before division, signed differences retained (including negative), separate per-symbol medians need not sum to total median. Malformed/duplicate/undefined/missing association and totals mismatch fail closed; no symbol attribution invented. Test-first14 failures missing API, focused+adjacent32 PASS/85 deselected (1.288s/54,411,264B; baseline1.848s/61,739,008B), synthetic format/mock runner only, actual CLI fail closed Valgrind absent exit1. Existing independent CPU sampler threshold previously reproduced both baseline/candidate; whole suite not rerun or claimed green. No actual counts, cycles, IPC or actual hardware function attribution; no CI dispatch.
   - 2026-11-27 exact native dynamic splitlines keepends semantics: baseline496ee457 re-admitted strict current runtime, actual owned ELF1848b80132f05c32b7c25b10e4003bd02e9dfd482664969124415b44cf1087b5 returns0/empty stderr but CPython output diverges; six receiver calls, ZERO operand/truth/length or argument/truth exception observations, because prior lowering treats a nonconstant keepends as True without evaluation. Baseline guarded4.640s/92,209,152B; actual stdout SHA757ce7fc vs expected37d57aed. Candidate valid single native nonconstant positional/keyword argument only: existing owned slot call captures receiver then arg once, checked Python truthiness with both authoritative roots, i32 flag, actual runtime result published before reverse cleanup; exception preserves selecting TLS. Constant/noarg and invalid/foreign route behavior unchanged. Fresh strict current archive bytes identical 8b0dd1b5 with separately verified candidate provenance8d99f373, 91.907s/387,100,672B. Actual owned ELF d54f398f473b33c45abcf2bbaa6e43ce06ad82f8875ef337f9c0cb8cfc573369 matches CPython exactly: typed and dynamic receiver, bool/int/float/None/str/list truth, expression order, custom __bool__/__len__, gc.collect inside callback, operand and truth exceptions caught. Existing Unicode88x5 and split maxsplit adjacent pass; guarded4 PASS/2 pcc1 integration UNRUN 6.670s/112,619,520B. Actual strict ordinary contextual closure626 changed string_method_lowering fallback0 (67.241s/681,672,704B). Baseline/candidate generated IR for bare/constant positional/keyword splitlines and unrelated split(maxsplit), strip, upper, replace, find byte-identical SHA63aafcc6327aec960c0635b4082d9b01279d936152f49506c25858dffc1d9937. No performance or fixed-point qualification; no new CI dispatch.
   - 2026-11-27 #646 worker-prior exact first failure all four platforms: x64 artifact15164724545 1323 PASS/1 FAIL (guard75.571s/1,684,684,800B), ARM15164793605 1300/1 (53.254s/1,283,170,304B), Windows15164993600 1359/1 (123.20s pytest), Mac15165112800 1335/1 (105.323s/1,658,028,032B); identical JUnit test_default_command_retains_project_xdist line498 assert24==18, maxfail early, not whole gate. Independent all six witnesses each platform agree exact raw ordinary universe3786 SHA ffece077c62f6ef7c7854366bda6825015053f844924a06d874a748699ff28c7, selected x64 3786, other three3785 plus same genuine platform-UNRUN x86 Linux text-index node. Compared #639 exact witness set+14/-1: +7 indexed_phase_timing x86 progress controls (447→454), +6 cleanup helper actual/recorder eligible 3–5 controls plus six-root inline replacing obsolete three-root ineligible (18→24). No other per-file/raw/source divergence; all87 host input file blobs unchanged current vs #646 (before count mirror change). Exact current baseline ff6f4ba1 same line498 failure guard9.973s/79,982,592B. Reconcile only HOST_COUNTS two entries and exact mirrored prefix/universe/AST counts; strengthen stale-count, omitted added nodes and equal-total per-file movement negatives for both. Same 87 files, six workers and all gate/deadline/resource requirements. Actual focused full test_ci_worker_size_prior_gate.py 327/327 PASS guard18.606s/104,558,592B under unchanged90s/1GiB +2GiB reserve; hosted execution remains unqualified. #646 Linux CI ordinary additionally reports diagnostic bridge and Darwin owner55vs54 nodes; separate evidence, do not waive. No new CI dispatch.
   - 2026-11-27 exact #646 shared Darwin source-owner mirror: previously approved ARM64 handoff closed-world node is the only addition to 54 rules, exact current manifest contains55. Baseline3c65f70b same hosted test fails55==54 (0.785s/44,003,328B). Test-only correction asserts exact55 and unique rule domain/shared ordinary, macos-arm64 only, no additional tools. Existing every Linux Stage1 input requirement, Darwin absent native input and reject imported Linux bundle checks retained, dedicated Mac same-run owner gates unchanged. Full native-input/owner/Mac regression gate host models 262 PASS, guarded1.795s/58,171,392B. No native Mac execution claim or CI dispatch.
   - 2026-11-27 genuine #646 diagnostic native CPython bridge defect: exact baseline490f063c original test_snapshot_native_lowering_calls_owned_runtime_directly fails (2.324s/88,518,656B); five actual py_cpy_ calls all module-top/fini (ensure_init, import time, getattr perf_counter_ns, two decrefs), first-class clock capture for private x86 worker observer stability. Narrow host-only CPython capture and identity comparison guarded by existing folded implementation predicate; native clock callsites are statically owned, no native optimization disable, all other observer guards unchanged. Standalone candidate zero bridge calls, exactly same six py_time_perf_counter_ns callsites, original assertion retained and strengthened with per-function direct call checks. Fresh strict runtime current archive8b0dd1b5 bytes unchanged, provenance b2ffaf63, guarded88.375s/387,231,744B. Actual closed self x86 ELF a035ff2773c197a7d5550abe8d74d3b1ebd6dbde83219b71b33f800486230c3c returns exact marked private stable True/public False, direct clock positive and elapsed nonnegative/no failure True, invalid scalar False, empty stderr; guard24.973s/435,990,528B. Strict actual ordinary contextual closure626 fallback0 61.585s/626,442,240B; full two affected host model files63/63 PASS5.214s/104,214,528B including all host mutable observers and original no-bridge. No actual native pcc1 fixed point, performance or hosted green claim. Existing CI queue preserved; no new dispatch.
   - 2026-11-27 actual #646 Mac Stage1 clean500e1ab6 all626 modules finished; entry3562.448s/compile3561.879s, observed2,010,939,392B, auto cap2,845,884,416B, failure before detached merged placement: metadata modeled raw2,591,546,240 vs limit2,578,743,296 (+12,802,944B), retained descriptor ledger380,347,152, no pcc1 output. Exact older #639 same link owner code finished full219s placement/detach/sign/write, not a source-only causal verdict. #646 PCO corpus unavailable. Structural correction only: external spellings directly become used-name set, no duplicate external set; explicitly retire rename scratch after each input and return; unresolved/weak outputs filter membership preserving sorted unique order instead of allocating difference/intersection sets. Exact source occurrence census bounds reserved N+L, used E+L, one-input max local detector; disjoint placement sets bounded N and weak/strong undefined occurrence counts. Maximum of these actual disjoint lifetimes, all old mapping/sequence/string/per-symbol/section/plan charges and production cap unchanged; pathological local expansion remains charged. Baseline actual synthetic four external-only PCO detached admission raw3,627,720B; original5M byte-exact output preserved, added threshold fails. Candidate placement below3.45M; first full tighter synthetic3.45M refused independent later row validation raw3,450,168, no waiver. Full synthetic3.6M (below old placement raw) emits byte-identical Mach-O, original5M path retained. Local collision corpus N512/L511 charges set1023 and max local256, exact negative one-byte-under refusal and full eager parity. Windowed prepare/parity73 PASS guarded3.344s/57,602,048B. Ordinary contextual closure629 source2629aaa6 with candidate, macho_link fallback0 65.520s/683,671,552B, host windowed owner excluded as intended. No native Mac rerun/qualification or full-corpus budget outcome claimed. No CI dispatch.
   - 2026-11-27 maintainer single consolidated CI checkpoint after complete prior queue drained: #646 (45915165347, clean source500e1ab6) terminal54 jobs21 success/31 failure/2 skipped, prior #640–645 provider cancelled, verified repository active0/queued0/pending0 and no later run. Exact accepted master81b2edd4 is 37 commits ahead/0 behind #646; includes post-snapshot worker-prior inventory9f36bd72, Darwin membership490f063c, real native diagnostic bridge014a6b37, Unicode/dynamic splitlines363af723/73460cdd, optional Linux Callgrind426ed2c7/baee923f, Stage2 marker ad880b4f, native GC48 slab73ae31f4, Mac detached owner81b2edd4, x64 typed MIR opt-in2629aaa6 (default OFF, existing Stage1/Stage2 unchanged unless explicit opt-in; no automatic LLVM parity or native fixed-point claim). GC80 rejected/held, excluded; active independent source claims remain. Exactly one normal cumulative run for this verified latest accepted source, no further snapshot/dispatch until this run terminal and maintainer authorization. No guard/deadline/worker changes or CI green claim.
   - 2026-11-27 #647 all-four worker-prior new exact first failure `test_arm_packed_width_cache_source_and_observation_contract`, x64767 PASS/1 FAIL, ARM756/1, Win779/1, Mac811/1 (artifact15171362520/15171970251/15171407722/15171851598); Linux CI ordinary compact15171980983 same sole failure3268 PASS/1 FAIL, prior #646 fixes passed there. Eleven new MIR classes unclassified by fail-closed broad backend data-plane inventory (not missing slots: nine ordinary classes already explicit, two exception controls). Current exact baseline34fa11bf same failure guarded2.054s/83,849,216B. Add exact eleven truthful semantic-record, phase-shell, native_u32 arena and target-control categories; existing broad unclassified/stale/invalid emptiness unchanged, AST existing slots/exception and missing-one negative for every new class. Adjacent tool run first14 PASS/2 FAIL revealed two actual stack-map projection sites (latest remap helper owner, originally same constructors in #647 remap_plain_stack_map) and two actual inline aggregate lane reads, not gate waiver. Exact two direct constructor count1 diagnostic_adapter entries for changed offsets of existing plain plan, preserve ownership/derived fields and identity memoization, no raw arena claim; added missing/wrong count/moved/invalid policy controls. Separate Stage1 actual aggregate source fix3f465adb (exact planner blob1cd47b98) resolves original inline-read invariant, unchanged all58/full8 objects per its receipt; no MIR source touched here. Combined exact latest candidate focused CI node + full inventory tool16/16 PASS guarded22.799s/86,528,000B. Host model/actual inventory only, #647 already red immutable snapshot; no new CI trigger. Mac shared ordinary #647 compact15172145634 54PASS/1 UNRUN_MEMORY_LIMIT (sample1,078,263,808B vs1GiB), no semantic claim.
   - 2026-11-27 actual #647 Stage2 cold closure attribution (no production): exact terminal x64 class_gen function_loop begin (335663IR/87118blocks), previous completed c_parsetab26.954s/cli_bootstrap302.758s/c_codegen379.795s; ARM class_gen frontend finalize (prior23.094/467.600/663.031s); Win c_codegen function_loop begin (358849IR/95041blocks, prior34.342/392.375s); Mac inner600.123s/1,697,611,776B deadline, first c_parsetab calibration block_bodies no complete. Coordinator actual initial closure x64152.569/ARM115.087/Win193.898s; export parallel472.160/235.050/609.933/Mac426.684s includes summary145.876/65.158/191.192/122.863 and preload60.397/30.997/73.491/50.676. Exact source f4da0803 actual fresh ordinary host closure plain/observe 617->629 modules, ordered file paths+content SHA identical first d4c302bc/final f623126e, plain11.184s vs instrumented13.614s (not native speed). Actual single/multi import resolutions20570/18742, repeats17321/16088, misses14807/13572; second init probes16474/15291, exactly31765 redundant pure path joins within calls; project-root scans28379, marker exists56838; package-site fingerprint calls74451 including outside phases, 967863 environment gets. No stale path or environment caching allowed; current native stat/listdir direct, old subprocess comment not current. Source-matched native per-call diagnostic exact63a53bc0 path/environment owner blobs identical, strict shared archive d4520c69/provenance8a58, self ELF8bdf0b60 built21.309s/432MB, actual eight path outputs/root/fingerprint byte-identical host. All30 native runs exact (5 alternating old/new/control join triples and5 root/fingerprint/empty triples, each10000); median CPU ns/op old24724,new18439,root17651,fingerprint111227,empty698; paired join deltas10341/6135/6673/4880/5178ns all5 positive vs old-old max2809. Local CPU ceilings duplicate join ~0.20s; all root scans ~0.48s; all fingerprint operations ~8.23s (entire cost, not removable amount), not a hosted wall prediction. Reject path optimization below meaningful Stage2 ceiling; no persistent cache, no production or CI. Next independent actual export summary/preload source attribution only, avoid already rejected candidates.
   - Stage2 summary native phase attribution (CI worker): completed host evidence and stopped diagnostic (no production change). Actual frozen 32 modules V1/V2 fully decoded wire equality 32/32, 66,430,186 / 16,405,999 bytes; five no-callable modules, with c_parsetab alone 4,359,081B V2, host decode 0.650s and function/import scans 0.095s, all32 decode 2.068s and scans 0.102s. Not a native ceiling. Native probe on exact 63a53bc0 source and independently verified d4520c65 archive (unchanged summary owner) hit original 1GiB guard at 53.994s / 1,145,262,080B (nine host owned-pass workers); sole authorized serial pass retry hit same guard at 114.830s / 1,079,742,464B (eight host backend emit workers). No binary or native phase timings, no third attempt, no cap/deadline/production worker changes, no skipped validation or persistent cache. Hosted CI remains untouched. [skip ci]
   - 2026-11-27 #647 complete pytest not-integration x64/ARM/Windows exact JUnit collection error (artifacts15174459622/15174029204/15174273712): `test_x86_frame_owner_search_experiment` imports deleted private `_block_of` from frame-register owner; Mac artifact15174363494 instead MEMORY_LIMIT before collection at113.077s, 2,835,267,584B >2,825,322,496B, no JUnit, independent and unrequalified. Baseline clean03a7dac7 same collection error rc2/0 tests. Restore exact historical binary search copied from c570f27e parent7d771a77 commit diff (explicit historical comparator), separately model current ordered selected-access owner cursor without modifying backend; v2 diagnostic output names historical binary and cursor model read counts, preserves old prototype costs and all original input/order/bounds/malformed tests, independent bisect owner oracle. Candidate collects all six original nodes and full six + actual production sparse owner test 7 passed, 15 subtests passed in3.24s. No skipped tests, production modification, native speed or complete hosted green claim; no CI trigger. [skip ci]
   - 2026-11-27 #647 actual Mac complete pytest ordinary collection guard: six xdist workers460–468MB each plus parent53.76MB terminal, observed2,835,267,584B >2,825,322,496 limit by9,945,088B, 113.077s, no JUnit/test outcomes, source error on other platforms separate. Static current source census 3,634 test/conftest modules, no AST syntax errors, no substantial proven duplicate top-level fixture owner. One diagnostic-only Linux clean314fe83b source unchanged production/tests from #648, local single-process collect-only (no native provisioning or fake Stage1, no test execution), original1GiB guard terminal COMPLETE54.913s/543,711,232B rc1, partial2,754 observed modules and 68,645 selected-before-error; host Python3.13 lacks profiling.tracing used by test_py_stdlib_devtool_closure (CI3.15). No complete qualification or re-run. Largest immediate RSS rises metal_5gc_lifetime_real43.4MB (487KB source/38,206 AST nodes/936 asserts plus imported 563KB test), c/test_self_backend37.7MB (322KB/34,872 nodes/1,070 asserts), package_array_core29.5MB (126KB/19,321 nodes/925 asserts). Their next-ten-module minima remain roughly35/34/24MiB, but arena retention is not proof of removable object bytes; largest allocated-block rises instead test_os_error_constructors2,989 items/+106,565 blocks, GCC self/execute each ~1,900/+77K. No giant retained top-level data proven, existing AST model retirement intact; no unsafe test removal, factoring, worker/cap/deadline or selection changes. Stop attribution; Mac remains UNQUALIFIED, wait #648 exact results. No new CI. [skip ci]
   - 2026-11-27 #648 four-platform worker-prior new exact failure test_macho_windowed_prepare.py:328 `test_symbol_phase_retirement_admits_and_emits_identical_image`: actual synthetic placement raw3,467,992 vs original3,450,000 limit x64/ARM/Mac, Windows3,467,928; artifacts15177557725/15176714602/15177616927/15177463276; x64 2531PASS/1FAIL, ARM2533/1, Mac2530/1, Win2518/1, frontend environment66PASS all. Hosted CPython3.15 host `getsizeof` component breakdown unavailable, local3.13 model differs. Current production census already checks exact nonexternal N_SECT predicate as rename helper, including source names/types. Structural fix only when no local definitions: avoid helper's two actual full spelling sets, produce independent empty map/input, retain all existing mapping/name/plan/placement charges and validations. Any local definition retains original helper, collision/duplicate/error semantics and pathological charge. New spy baseline205895cc fails actual helper call256x4 (raw3,418,520), candidate exact same frozen PCOs and append list capacity raw3,349,016, reduction69,504B exactly modeled omitted set difference; sorted temporary list alternative +32B accounted separately. Full candidate image equals eager SHAfb16f4eaff9c31370c94bbb274c7f9b304b4b657ed21ac3c39bed108440ad8a2. Existing 3.45M placement and 3.6/5M full synthetic limits unchanged, actual collision independent map and one-byte-under refusal retained; 177 PASS windowed prepare, packed-symbol resolution, staged relocation count (2.54s). Strict ordinary contextual closure629, macho_link fallback0, changed host-only macho_host_prepare explicitly excluded as intended, guarded68.311s/673,132,544B. No hosted3.15 or native Mac qualification, production cap unchanged; no new CI. [skip ci]
   - 2026-11-27 #649 four-platform worker-prior exact first failures `test_runtime_signature_literals_are_bounded_function_chunks` and `test_every_abi_chunk_family_obeys_native_literal_bound[_runtime_signatures_part_]`, max52 >50, actual artifacts15183264097/15184276212/15183753323/15183219573. x64 2712PASS/2FAIL, ARM2710/1, Mac2731/2, Win2710/2, frontend environment66PASS each. Runtime ABI blob60b62754 unchanged since #647/#648 (latent earlier gate not reached). Baseline clean0ebbbe2c host2FAIL/6PASS exact. Move only unchanged final two native-function ABI entries py_func_init_metadata_slots and py_func_new_static_def from part20 to immediately adjacent part21, 52/14→50/16; no new parts, no change to declaration/assembly order, signatures or tests. Independently actual imported ordered runtime1085 values fingerprint9c477505 and cross392 cb79e0b8 identical; flattened AST runtime1085 b41dc6b6/cross395 1dcc734c identical including duplicate cross declarations. Original ABI test suite8/8 PASS0.84s, actual ordinary strict closure629 changed runtime_abi fallback0, guard81.206s/687,407,104B. Original <=50 bound preserved, no hosted or Stage2 qualification; no new CI. [skip ci]
   - 2026-11-27 #649 Linux complete not-integration exact first recorded failure `test_layer1_constants_cross_module_static_imports_stay_native` (artifact15190599689, source f042ab23; overall guard TIMEOUT2400.466s/8,500,690,944B, no JUnit). Clean f2a870a4 baseline reproduced failed literal-in-consumer assertion after no-bridge PASS, guarded1.814s/69,791,744B. Actual emitted consumer IR SHA16221715, provider IR SHA2bcbfe65. Provider exact `pcc.ir.compat` static literal is published into registered module root and exact native module table; consumer exact native compiled-module import/getattr into registered roots, exact module/key publication, live table lookup, class initializer registration/reload, exact L1CodeGen receiver and class-key setter. Correct the obsolete literal-in-consumer test model with this full producer→consumer native value-chain witness, retaining original fallback check and adding five mutated provider/root/value/class-key negatives. Focused original + contextual host state 2 PASS in3.11s; host actual generated IR only, native pcc1/whole hosted lane not run, no production or gate changes. [skip ci]
   - 2026-11-27 #650 four-platform worker-prior exact failure `test_root_leave_identity_ledger.py::test_identity_order_and_host_operation_counts[0-owned]` and neighboring owned size cases, artifacts15192970829/15192456822/15193236088/15193690786. Original AST-reference loader targets `_emit_owned_local_cleanup` after actual identity ledger moved to `_emit_exit_frame_leaves`; inversion finds (0,0) instead of (1,1) and actual wrapper needs `_owned_local_cleanup_plan`. Current exact source f2a870a4 test blobf9c25b10/ownership blobf49ae20d unchanged latest; local baseline actual original node FAIL0.54s. Model now executes the real extracted wrapper, plan, root-clear and frame-leave bodies, only transforms actual ledger body into old linear identity reference; no expected results, test inventory, production code, guards or CI limits changed. Entire existing test file 36 PASS in11.21s including counts, duplicate identity, payload/group/error/mutation, reentrant, weakref and reset controls. Host recording model only; immutable #650 still red, no new CI. [skip ci]
   - 2026-11-27 #650 actual independent shared ordinary batch20 `test_linux_errno_ir_has_pcc_tls_and_no_libc_or_loader_imports` stale generic TLS IR assertion (artifact15196739310 source f2a870a4). Exact latest preimage testb1a79a82 and unsafelowering866e872d match frozen f2a baseline, original node actual FAIL1.26s. Earlier accepted cf7762a6 specifically made freestanding x64 Linux TLS initial-exec; actual named errno symbol now spells `@pcc_errno_storage = thread_local(initialexec) global i32 0`. Fix only test model for explicit exact x64 target, reject generic errno spelling, assert native TLS address intrinsic, retain every original forbidden import and add `__tls_get_addr` prohibition. Candidate focused node + actual GCC-linked self threaded isolation + existing four-target fixture model and object emission controls 6 PASS1.66s. Clang/hosted suite not qualified; independent genuine ELF file-symbol parser failure and resource failures remain. No production, guard, or CI changes. [skip ci]
   - 2026-11-27 #650 actual shared ordinary batch20 LLVM raw frame registry test (artifact15196739310) rejects standard Clang ELF file symbol `freestanding_gc_frame_registry_llvm.ll` at owned ELF parser unsupported type. Exact f2a/latest source f4e53d56 and test ffd5cf56 unchanged. New test-first x64/ARM synthetic four cases fail as expected0.84s; real GCC/as object with FILE LOCAL DEFAULT ABS name `file-meta.S` SHA b6548495 reproduces same owned parser failure. Admit only exact file metadata rows (local/absolute/default visibility, zero value/size, nonempty name), preserving original table index and local/global order; reject any relocation referencing a file symbol. Ordinary no-FILE route does not allocate file-index set or walk relocation rows a second time; all previous checks remain. Candidate exact x64/ARM metadata, compact/eager roundtrip, multiple files, malformed metadata/refusal and image parity, full existing ELF test suite, native self frame registry raw closure, five TLS model/control tests: 77 PASS2.98s. Actual GCC/as object parse/emit and unchanged global closure, owned linked static executable exit42 SHA4dfb8b36. Strict current180c6f49+candidate contextual closure629, elf_x86_64 fallback0, COMPLETE83.364s/750,657,536B with1GiB cap/2GiB reserve/120s, emitted IR SHAe71afdf2. LLVM Clang unavailable locally, exact hosted LLVM reference remains UNQUALIFIED, no broader gate relaxation or CI trigger. [skip ci]
   - 2026-11-27 #651 actual four-platform worker-prior first failure `test_elf_input_sources_strict_modules_and_workflow_are_bound`: expected old67 format tests, actual71 after the four STT_FILE cases in 184f51f0; artifacts15200120304/15198959637/15199443431/15200065855, x64350PASS/1FAIL, ARM352/1, Win349/1, Mac581/2 (frontend66PASS all). Mac second actual `test_arm_packed_width_cache_source_and_observation_contract` because exact historical owner hash rejected new later STT_FILE production delta. Local exact baseline both nodes2FAIL0.56s. Strengthen only linux-elf-format required exact71 in real gate and all three expectations; staging67/69, native_count0, workers/deadlines/skip/selection unchanged. Independently authenticate complete new FILE owner SHA1d3ae5dc and reverse exactly three hunks to prior unwind owner SHAda574987, then existing ODR/unwind/host-export/older packed reference chain unchanged; all historical hashes and existing tests preserved, new 3x3 missing/damaged/duplicate plus unrelated hash tamper controls fail closed. Candidate124PASS10.81s +86PASS5.08s covering actual format71, exact route/staging and negative guard contracts, older source controls and x86 packed object output comparisons. No ELF production change, no CI trigger or hosted qualification. [skip ci]
   - 2026-11-27 #651 final Linux nonintegration actual failure (artifact15207203468, SHA35a63255, exact run46004099923): guard COMPLETE1840.187s rc1 peak8544591872B under unchanged16GiB/2400s; stale-state live forecast probe read an empty started3 PID between file creation and payload write (ValueError int(empty), not forecast policy). Test-only same-directory atomic PID publication with hidden pending file; persistent deterministic witness executes the exact shared generated publication statements and observes real open/truncate before payload, baseline red1.32s, corrected all five existing real-resource scenarios PASS8.88s, no extra test node/coverage/worker/cap/deadline change. zlib same adler32 native NULL-without-exception, strict native multi-module route absent and selfhost oracle warmup1500s timeout remain independent actual failures; no native/hosted qualification or CI trigger. [skip ci]
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
   Mac assessment of the 2026-11-27 shared batch (Claude, local Mac,
   2026-11-27; base 11c88fb4e as arms alloc-base2 and control alloc-base,
   candidate arm head-9d51a6a at 9d51a6a74, 288 commits later: allocator
   48- and 80-byte managed classes, shared generator frame save suffixes;
   the x86-64 typed MIR is opt-in and does not reach AArch64). Host load
   6-11 on 12 cores. `ab` five pairs: w1 63062 -> 63469 req/s (+0.6%, 4/5,
   control floor 7.5%), w4 145391 -> 151213 (+4.0%, 4/5, floor 9.2%):
   both inconclusive; CPU per request 18.6 -> 18.2 us (w1), 28.6 -> 27.4
   us (w4). `instr` five runs: w1 236399 -> 230899 instructions per request
   (-2.33%, control +0.03%, run spread at most 0.6%), cycles -2.83% inside
   a 6.6-11.8% spread; w4 256085 -> 249249 (-2.67%, control -0.44%, spread
   up to 4%), cycles +0.93%. `table`: w1 61818 req/s (71% of the recorded
   asyncio), w4 148164 (56%), level with 2026-11-26 (62959 / 151474).
   No Mac regression from the batch; no throughput gain beyond noise. The
   goal (at least LLVM parity, then asyncio) is not met: the clang runtime
   of the same IR ran about 85-89k req/s at w1 on this host.
   LLVM bound at the same source (Claude, local Mac, 2026-11-27, labeled
   reference experiment): `pcc_perf.py arm NAME --reference-runtime
   clang-O2` now builds it (each owned archive member's own IR through
   Homebrew clang 20.1.8, the capture stub, the self-compiled program,
   `PCC_RUNTIME_BUILD=make` route; `clang-codegen` adds
   `-disable-llvm-optzns`). Arms head-9d51a6a (self), head-self2 (second
   self build, control), head-clang; load 7.7. `ab` five pairs: w1 62464
   -> 92859 req/s (+48.7%, 5/5, floor 3.3%; 107% of the recorded asyncio),
   w4 139561 -> 163303 (+17.0%, 5/5, floor 5.1%; 62% of asyncio). `instr`:
   231355 -> 169501 instructions per request (-26.7%), 54607 -> 35789
   cycles (-34.5%). So w1 parity with LLVM would pass asyncio; w4 needs
   scaling work beyond code generation (asyncio w4/w1 3.0x, clang runtime
   1.76x). `top` w1: 16.04 -> 10.79 us per request, spread over 40+
   functions (largest: `memset` 0.48 -> 0 with `bzero` 0.20, store_root
   -0.37, str_join_list -0.19); seven small helpers clang inlines (0.57 us,
   about 11%). Static mix of 20 hot functions: self 4008 instructions,
   clang 2347; excess frame loads +480 (values kept in slots and reloaded
   after calls, duplicated epilogue restores), branches/calls +365,
   register copies +276, immediates +154, compares +114, frame stores only
   +57. Same pattern as the x86-64 retrofits Codex rejected before the
   typed MIR (Linux w1 +36%): the AArch64 lever is whole-function
   allocation, not more local passes.
   Split of the bound (arm head-clangcg, `clang-codegen`, same day): w1
   62325 -> 81374 req/s (+30.6%, 5/5, floor 1.9%), 180707 instructions
   (-21.7%), 41461 cycles (-21.6%); full clang -O2 169231 / 36156. LLVM
   code generation is about two thirds of the gap and LLVM IR optimization
   one third; code generation alone reaches 94% of asyncio.
   (i) Nested runtime inlining (Claude, 2026-11-27): the definition inliner
   clones each callee as it was before the pass, so a call inside an
   inlined helper (for example `tls_i64` inside thread-state readers,
   `append_mode_admitted_locked`, allocator magazine helpers) is copied
   into the caller and stays a call; rerunning the inliner on the shipped
   runtime IR removes them, a third run changes almost nothing. The runtime
   pass list (`pcc/runtime/Makefile` PCC_RUNTIME_IR_PASSES) now runs
   `inline-defined-wide` twice with `instsimplify` between. Arm inl2 against
   head-9d51a6a, control head-self2, load 7.6-7.9: w1 62032 -> 64214 req/s
   (+3.5%, 5/5, floor 2.0%: a gain), CPU 18.6 -> 18.0 us; w4 137627 ->
   140663 (+2.2%, 4/5, floor 3.7%: within noise); `instr` -1.46%
   instructions, cycles -5.23% (control -2.99%). Test:
   `tests/python/test_runtime_nested_inlining.py` (a three-deep helper nest
   stays a call after one pass, is fully inlined by the configured list,
   and the emitted program runs). Focused inliner/runtime-pass files 132
   passed; arm smoke and primitives exact against CPython. Not run: whole
   Python suite, GC integration gate, bootstrap/self-host.
   Which LLVM step the self passes lack (Claude, local Mac, 2026-11-27,
   labeled reference experiments built with `pcc_perf.py arm
   --reference-runtime opt:PIPELINE|llc:OPTIONS`, source 52096e63e, each
   `ab` five pairs at w1 against arm abl-cg = LLVM code generation alone,
   control abl-cg2 = a second build of it, load 4.8-8). Self against abl-cg:
   65005 -> 80974 req/s (+24.6%). Removing one LLVM code generation step:
   register allocation replaced by `regalloc=fast` -44.7% (0/5; cycles
   +36%, below self; measured on head-clangcg, control mislabeled, verdict
   from the pairs and cycles), block placement off -6.3% (0/5, floor 1.3%),
   shrink-wrapping off +4.5% (5/5, floor 3.7%: LLVM is not faster with it
   here), machine CSE/sink/LICM off +2.3%, peephole off +1.9%
   (inconclusive), branch folding and tail duplication off +1.5% (noise);
   FastISel's runtime failed its smoke (json.dumps), not measured. Adding
   one LLVM IR pipeline: `cgscc(inline)` +5.2%, `instcombine` +3.7%,
   `sroa,early-cse,gvn` +1.5% (noise), `simplifycfg` +0.9%, `default<O2>`
   +13.4%. So register allocation is the step that carries the gap, then
   block placement and inlining; shrink-wrapping is not. The self allocator
   already places most scalar values (four hot modules: 15845 assigned, 280
   lost to pool pressure, about 1200 kept in slots by rule), but its linear
   scan evicts the furthest-ending interval (a function argument used on the
   hot entry path loses first, as in `py_obj_eq_value`), has no spill
   weights, no live-range splitting and no holes. Greedy's parts ranked the
   same way (source a96e2c9a3, arms ra-cg/ra-cg2): `regalloc=basic` (no
   splitting or eviction) +0.5% (noise), coalescing off
   (`join-liveintervals=false`) +2.6%: neither splitting nor coalescing
   carries the gap here; `regalloc=fast` loses by spilling every value at
   block boundaries, which the self allocator does not do. Per-function
   time against abl-cg is flat (15.26 -> 12.03 us; largest
   `pcc_container_unlock` 0.30 -> 0.11, then `memset`, the granule test,
   `free`).
   (j) Not merged, no request-level gain (Claude, 2026-11-27): acquire
   loads as RCpc `ldapr` on Darwin (LLVM's lowering; `ldar` is RCsc and
   waits for an earlier `stlr`; 1406 acquire loads in the runtime, none
   seq_cst; Linux keeps `ldar`). Arms rc-base/rc-base2 (a96e2c9a3) and
   rc-new, all 2553 `ldar` became `ldapr`: w1 63540 -> 64250 (+1.1%, 5/5,
   floor 3.2%: noise), w4 -1.4% (floor 7.0%), cycles -1.8% against control
   -2.3%. Patch, encodings (clang 20 words) and native test kept outside
   the repository as `p0-rcpc-acquire-loads.diff`. Also not merged, same
   arms: the runtime pass list on `freestanding_thread_kernel_pthread`
   (`owned_runtime_build` skips it, mirroring the Makefile rule; no recorded
   reason besides implicit safepoints): w1 +0.9%, w4 -3.2%, both
   inconclusive, instructions -0.06%
   (`p0-thread-kernel-runtime-passes.diff`).
   Where the executed instructions differ (Claude, 2026-11-27): self and
   LLVM code generation have the same IPC on this workload (228k/53k and
   179k/42k per request), so the cycle gap is the instruction gap, and
   instructions per request (run spread under 1%) are the per-change
   measure; request rates resolve only accumulated changes on this host.
   `pcc_perf.py steps A B` single-steps the request thread under lldb,
   charges each PC to its function (linker stubs, veneers and system
   libraries separately), keeps the raw record in the arm, and scales the
   second arm by the shared self-compiled program code to equal work.
   rc-base against ra-cg, 600000 steps each: runtime 506845 against 381651
   executed instructions at equal work (+33%), spread over 40+ functions
   (`str_join_list`, `granule_is_object_start`, container lock/unlock,
   `py_obj_eq_value`, `free` lead). By class: ALU +37k (`and #imm` +15.7k,
   mostly a bit or mask test before cbz/cbnz/cmp that LLVM folds into
   tbz/tbnz/tst; `add` reg+reg +12.5k, address arithmetic LLVM folds into
   the access; `lsl` +4k, `ubfiz`/shifted operands), value stack slots
   +23k (`stur`/`ldur [x29]` +12k, alignment-hole `sturb` zeroing +5k,
   `[sp]` +6k), register copies +22k, unconditional `b` +12k (layout),
   `cmp #imm` +8.5k (LLVM uses `ccmp` chains, -2.9k), `sxtb`/`sxtw` +6k,
   immediates +5k; frames entered and calls are not higher (self makes
   fewer calls). The gap is instruction selection and local placement of
   values across many patterns, not one pass. Copies split further: moves
   between argument and callee-saved registers are close to LLVM's; the
   excess is scratch traffic through x9-x17 (self about 14k, LLVM under
   1k), concentrated in the container lock/unlock compare-exchange and
   release-store emitters and `str_join_list`.
   (k) Merged (Claude, 2026-11-27), measured together as arm bundle2
   against rc-base, control rc-base2: thread-local address results are
   pool candidates (`_ALLOCATED_RESULT_INTRINSICS`; the TLV result was
   stored to its slot and reloaded at every use, 0.92% of instructions
   alone); frame alignment holes cleared with the widest aligned store
   (one `stur wzr` per 4-byte hole instead of four `sturb`); instcombine
   narrows `and (sext iN x), 2^N-1` to `zext` and `icmp eq/ne (ext iN x), C`
   to an N-bit compare (target-neutral, so x86-64 runtimes see it too).
   Instructions per request 228066 -> 224377 (-1.6% to -1.9% across runs;
   control -0.1%). `ab` five pairs: w1 66799 -> 67925 (+1.7%, 4/5, floor
   4.5%: within noise), w4 -1.5% (floor 16.2%, inconclusive). Tests:
   `test_aarch64_tls_address_registers.py` (no slot traffic, two threads),
   `test_aarch64_padding_zero_stores.py`, `test_instcombine_narrow_extensions.py`
   (boundary values executed before and after). AArch64, TLS and IR pass
   files 4474 passed; four nodes excluded that fail identically on HEAD or
   need `PCC_RUNTIME_ARCHIVE` (constructor cleanup native, exception TLS
   source text, two optimizer native-export shapes). Not run: whole suite,
   GC integration gate, bootstrap.
   Assessment: each targeted item is 0.5-1% of instructions; the
   remaining gap is the emission model (one IR instruction at a time,
   operands through fixed scratch registers, repaired by peepholes). Next:
   compare-exchange and atomic-store emitters using operand registers and
   `wzr` directly, `and`-test fusion (`tst`, then `tbz`/`tbnz` with branch
   relaxation), register-offset addressing; the structural alternative is
   an AArch64 machine IR with selection and allocation, as the typed x86-64
   MIR did (-22% static instructions, Linux w1 +36%).
   (l) Not merged, no gain (Claude, 2026-11-27): LLVM's inliner remarks on
   ten hot runtime modules list 663 call sites it inlines that pcc's
   leaves; 380 are refused only because a multi-block callee returns or
   merges a pointer (a rule for frame-registering callers, 14 of the 429
   callers) and 210 sit in the pthread kernel, which skipped the runtime
   pass list. Gating that rule on callers with `pcc_gc_frame_enter*` and
   running the pass list on the kernel cut the LLVM-inlinable sites to 213,
   but arms h3-base/h3-base2 (8e5e7906e) and h3-inl measured instructions
   -0.17% (control +0.18%) and w1 +0.6% (floor 1.4%). The same inlining is
   worth +5.2% under LLVM code generation and nothing under the self
   backend: inlined bodies are emitted with the same per-instruction
   selection, so IR-level gains wait on code generation. Patch and test
   kept outside the repository (`p0-inline-pointer-merge-and-kernel-passes.diff`).
   Next: the AArch64 instruction-selection layer (machine IR with
   pattern-based selection after LLVM's AArch64 patterns, then block
   placement), measured by executed instructions against arm ra-cg.
   (m) Merged (Claude, 2026-11-27): masked tests select as `tst`. A fused
   `icmp eq/ne iN (and x, C), 0` (or `, C` for a single-bit C) whose `and`
   directly precedes it, has no other use and whose C is a logical
   immediate emits `tst x, #C` with the branch on Z; the `and` is not
   emitted and takes no register (`_masked_test_fusions`; text and producer
   encoders gained `tst` with an immediate, words checked against clang 20).
   Arm h4-tst against h3-base, control h3-base2 (no `pcc/` change between
   8e5e7906e and 27d455f2a): instructions -0.61% (control +0.10%), w1
   +1.2% (5/5, floor 9.7%), w4 +1.4% (floor 2.7%): within noise. Test:
   `test_aarch64_and_test_fusion.py` (shapes, four refusals, encodings,
   native execution of every branch in both allocator modes). AArch64, TLS
   and encoder files 4683 passed (three nodes excluded that fail on HEAD
   or need `PCC_RUNTIME_ARCHIVE`). Each selection pattern added this way
   is worth 0.5-1% and touches three encoders, the allocator and the
   emitters; a selection framework that matches single-use operand trees
   once would make each further pattern a table entry.
   (n) Merged (Claude, 2026-11-27): the pthread kernel runs the runtime
   pass list without the inliners (`owned_runtime_build._without_inlining`,
   Makefile `PCC_THREAD_KERNEL_IR_PASSES`). Its lock, TLS and stop-the-world
   helpers kept constant truncations, a multiply by 128, sign extensions
   before zero tests and branches on constant PHIs; inlining into them had
   grown those paths (l). Arm h5-kscalar against h4-tst, control h3-base2:
   instructions -0.51%; the arm's smoke and primitives exact at w1/w4.
   (o) Merged (Claude, 2026-11-27): instcombine folds `trunc`/`zext`/`sext`
   of an integer constant (the kernel's compare-exchange operands were
   `trunc i64 0 to i32` and `trunc i64 1 to i32`, each built in a scratch
   register and copied). Arm h6-castfold against h5-kscalar (same `pcc/`
   otherwise): instructions -0.39%. Tests execute the folded casts natively
   before and after.
   (p) Merged as LLVM-parity lowering, not a performance claim (Claude,
   2026-11-27): atomic stores use their operands' registers and ZR for a
   zero value (`stlr wzr, [x1]`); 32/64-bit LSE compare-exchange takes a
   new value already in a register as Rt (ZR for zero) and seeds a small
   constant expectation directly with an immediate compare. Arm h7-atomics
   against h6-castfold: instructions -0.08% (the constant fold had already
   removed most of these copies). The first draft emitted the compare
   before the CAS on the native path only: direct instruction capture
   records each line when its helper runs, so helpers must run in output
   order; `test_aarch64_cmpxchg_operands.py` executes success and failure
   paths natively and caught it. Legacy tuple payloads make the same choices
   so both payload forms emit the same code.
   (q) Merged (Claude, 2026-11-27): single-bit masked tests select as
   `tbz`/`tbnz` (LLVM's AArch64 selection). The encoders gained the 14-bit
   test branch (`STRUCTURED_FIXUP_BRANCH14`, text, direct capture and
   semantic re-encoding; words checked against clang 20), branch relaxation
   per branch kind (+/-32 KiB for tbz: the opposite test over `b`), and the
   typed and text CFG passes treat tbz/tbnz as conditional branches (label
   references, retargeting, inversion). Arm h8-tbz against a clean HEAD
   build h8-base: instructions 220176 -> 216147 (-1.83%). Tests: the fusion
   file executes every branch natively, a relaxed native tbz across a
   44 KiB block, and far/near relaxation words. AArch64, arm64 encoder,
   atomic, Mach-O and TLS files 7348 passed. Two regressions found and
   fixed before merge: CFG passes dropped labels only tbz referenced (the
   runtime failed to build), and the relaxation threshold must use the
   smaller reach so patched limits still relax.
   (r) Merged (Claude, 2026-11-27): register-offset addressing, LLVM's
   AArch64 selection of `gep T, ptr %p, i64 %i` feeding the next scalar
   integer/pointer load or store as its only use: `[Xp, Xi]` for a byte
   element, `[Xp, Xi, lsl #log2(size)]` when the element size equals the
   access size, both operands already in registers; the GEP emits nothing.
   The rc-base step record executed 8628 `add` + access pairs per 600000
   instructions (1.44%; memchr/memcmp/memset byte loops, `py_obj_eq_value`,
   allocator granule walks). Encoders gained LDR/STR (register) in text,
   direct capture (`ADDRESS_REGISTER` tokens) and semantic re-encoding,
   words checked against clang 20. Atomic (`ldar`) and volatile accesses,
   i32 indices (`sxtw` form) and the block-local
   `PCC_SELF_CALLEE_SAVED_REGISTERS=0` allocator (arguments in slots) keep
   the add. Arm h9-regoff against a clean HEAD build h9-base: instructions
   216901 -> 213867 (-1.40%); cycles were taken under load 17 and are not
   a result. `test_aarch64_constant_and_gep_lowering.py` pinned the old
   `add ..., lsl #3` for a GEP that feeds a load; it now checks the folded
   load and keeps the shifted add covered by a returned address.
   Standalone `pcc --python-library --emit-llvm` of
   `self_backend_aarch64_darwin_memory.py` failed on HEAD
   (`slot-call ... lacks an immediate owned-result handoff`): that compile
   classifies this package's imports as CPython, and `"x" + str(call
   result)` in the large emitters converted the CPython result after the
   call. Four such lines (three from 2026-10-30, one from the atomic-store
   change) now call typed `_x_register`/`_w_register`; the module compiles
   standalone again. The closure (Stage1) compile was not run. Tests: the
   new file encodes against clang, re-encodes records, and executes every
   folded form natively under both allocators; AArch64, arm64 encoder,
   Mach-O, TLS, native-fragment and self-backend files 8134 passed with
   `PCC_RUNTIME_ARCHIVE` set to the h9-regoff2 runtime (source-matched).
   Not passing, all independent of this change: two fail identically on a
   clean 05ff5a481 snapshot,
   `test_native_emission_fragments.py::test_native_terminator_fragment_keeps_nop_between_distinct_safepoints`
   (the unoptimized object's stack map decodes only the entry record,
   although the call and loop labels and the separator `nop` are emitted)
   and
   `test_threaded_exception_tls_isolation.py::test_pcc_python_exception_slot_is_compiler_owned_tls_and_registered_root`
   (expects `pcc_gc_note_slot_write_barrier` in `py_tls_exc_set`, which
   has none); `test_self_backend_no_host_gate.py::test_self_backend_no_host_no_silent_fallback`
   needs a pcc1 binary (run with `PCC_NO_AUTO_PCC1=1`).
   (s) Merged (Claude, 2026-11-27): loop rotation in the AArch64 block
   layout (LLVM MachineBlockPlacement's top-tested loop rotation,
   `_rotate_top_tested_loops` before the canonical error fallthroughs):
   the latch falls into the header and the header's one conditional branch
   returns to the loop. pcc-Python loops poll a thread safepoint on the
   backedge (`br %need, %slow, %head`); the polling block is the latch and
   the slow block moves before the loop. The rc-base step record executed
   7927 backedge `b` per 600000 instructions; a loop entered k times per
   iteration count n saves n-1 per entry, so short loops gain little. Arm
   h10-rot2 against clean HEAD h10-base (five repeats each, runs within
   0.47%): instructions 213800 -> 212841 (-0.45%); an earlier draft that
   skipped polled loops (h10-rot) gave -0.32%. Cycles varied 5-11% under
   load 10 and are not a result. Tests: `test_aarch64_loop_rotation.py`
   checks while, early-return, nested, exit-first and polled loops,
   bottom-tested and unoptimized ones unchanged, typed/text agreement and
   native runs under both allocators; the backend files 8225 passed with
   the four nodes named in (r) deselected (the archive one passed alone
   with the h10-rot2 runtime). Remaining hot backward branches: loops
   whose latch has other backedges (`continue`), slow-path `b` in rotated
   loops (cold), and loops with a header outside the rotation shape.
   (t) Merged as LLVM-parity lowering, not a performance claim (Claude,
   2026-11-28): a constant `shl`/`lshr`/`ashr` whose only use is the
   directly following i32/i64 `add`/`sub`/`and`/`or`/`xor` (the
   subtrahend for `sub`) is that operation's shifted second source
   (`eor x1, x1, x0, asr #13` in `pcc_container_unlock`);
   `adjacent_shift_operand` decides for both instructions and leaves
   consumers fused into `madd` or `tst` alone. Producer encoding, direct
   capture and semantic re-encoding gained lsr/asr kinds (clang 20
   words); `test_arm64_encode_producer.py` had listed a shifted `and` as
   unproven and now encodes the shifted forms. Arm h11-shift against
   h10-rot2 (same source otherwise; five repeats, runs within 0.93%):
   211880 -> 211774 instructions (-0.05%, inside the noise). Most hot
   shifts feed address computations, `gep i8, @stripes, (shl %h, 7)` in
   the container locks; folding them into the GEP's `add` and into
   register-offset loads (`[Xn, Xm, lsl #s]`) is the next step. Five
   `"x" + str(...)` register spellings in the compute module (2026-11-01,
   2026-11-13) now call a typed `_x_register`, so the module compiles
   standalone with `pcc --python-library` again (it failed on HEAD like
   the memory module in (r)). Tests: the new file executes every folded
   and kept form natively under both allocators; backend files 8240
   passed with (r)'s four nodes deselected (the archive one passed alone
   with the h11-shift runtime).
   (u) Merged (Claude, 2026-11-28): a value takes x19-x28 only where it is
   live across a call. The function-wide allocator's single linear
   interval counted every call between definition and last use, including
   calls in blocks where the value is dead (the `abort` path laid out
   between the thread-state load and its use in `pcc_container_unlock`).
   `_PreciseCallCrossing` keeps the linear rule for calls at the interval's
   ends and counts an interior call only if the value is used later in
   that block or is live out of it (per-value backward walk; fused `madd`
   operands count as read at the consumer). Functions above 2000 blocks
   keep the linear rule. Callee-saved saves per entry in the h11-shift ->
   h12-cross objects: `pcc_container_lock` 2 -> 1, `pcc_container_unlock`
   1 -> 0, `pcc_gc_retain_known` 2 -> 1, `pcc_gc_release_known` 5 -> 4.
   The new h10-rot2 step record put callee-saved saves and restores at
   7.0% of executed instructions (`pcc_gc_store_root` 6 per entry, `free`
   10, `pcc_thread_safepoint` 10). Instructions per request, median of
   five runs, two separate measurements under load (run spread up to 31%,
   so these are indications, not ab results): -1.18% and -1.17%.
   (v) Merged (Claude, 2026-11-28): `zext` of an i8/i16/i32 load result to
   i64 is the loaded register (LDRB/LDRH/LDR W and their unscaled,
   acquire and reload forms zero the upper bits); the backend emitted
   `mov w4, w4` after the byte loads of memchr, memcmp and
   `py_obj_eq_value` (about 3.1k per 600000 stepped instructions). A zext
   of an argument or call result keeps `mov w, w` (unspecified upper bits
   under AAPCS64; the test returns a truncated 0x100000005). Together with
   (u), median of five runs in the same two measurements: -2.03% and
   -1.77%. Tests for (u) and (v): new files execute natively under both
   allocators and check typed/text agreement; backend files 8259 passed
   with (r)'s four nodes deselected (the archive one passed alone with
   the h12-zext runtime).
   Measurement note: `instr` medians moved by 1-2% between repeated runs
   of the same arm while an lldb step job ran; per-change instruction
   claims above therefore rely on the medians agreeing across separate
   measurements, and a deterministic counter (stepped instructions per
   request, or Linux Callgrind) is the reference for small changes.
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
       Direct x64 integer stack-map emission routes (Codex, Linux,
       2026-11-26, isolated base c570f27e): source trace correction to the
       previous next-step wording: actual x64 plans are UNPACKED records
       and textual route tuples; AArch64 owns packed spans. Keep all public
       plan/record storage and eager rendering order. The exact direct x64
       block emitter opts into sparse canonical block-ID routes using the
       already indexed kernel (reuse one ID over contiguous equal identity
       rows), rather than building and rehashing three textual buckets for
       every block. Keep original text route for custom plan/kernel/helper,
       callbacks, packed plans or supplied layouts. Preserve numeric block
       aliases, repeated row order and eager missing reload-label failure.
       Exact current 625-source contextual direct capture c_codegen and
       worker_resource_plan PIDX hashes unchanged from previous unit; zero
       fallback/no libpython. Actual c_codegen streamed owner capture:
       1,039 functions, 95,041 blocks, 205,755 records/routes (1,039 entry,
       158,887 suffix, 45,829 terminator), 26,395,512-byte exact input.
       Five quiet alternating input replay triples vs same-base control:
       route construction+consumption CPU2.274 -> 0.480 s (-78.893%,5/5),
       control2.277, normalized -79.080%5/5 vs control drift1.158%;
       construction alone normalized -75.712%5/5, wall normalized
       -78.839%5/5. Replay peak RSS32,532 -> 30,996 KiB, control32,444,
       normalized -4.575%5/5 vs drift0.259%. All15 route output digests
       exact (sha256
       113f329a9a750c3950f565d91dde0d9f1ff20778bdf1be33b4d66c44905b3d76).
       Actual c_codegen complete streamed ELF exact (13,508,968 bytes,
       sha256 8b6b5c7627edd2d289dd507d7d6522cabc7aaf26f421bf184c08a962cbba2277).
       Five quiet actual full streamed worker_resource_plan triples:
       backend+obj CPU5.693 -> 5.429 s (-4.627%,5/5), control5.656,
       normalized -6.367%5/5 vs control drift3.252%; full CPU6.181 ->
       5.881 (-4.852%,5/5), normalized -5.531%5/5 vs drift2.190%;
       full wall normalized -5.518%5/5; function emit wall3.456 ->
       3.179 (-8.030%,5/5), normalized -8.627%5/5 vs drift4.499%;
       peak RSS81,340 -> 81,220 KiB, control81,416, normalized
       -0.155%5/5 vs drift0.093%. All15 exact ELF objects (1,739,176
       bytes, sha256 42d60bb810624c8f1b6ac655d6a554ee98a09c32b709acebeb5c974a1b2b58cc).
       No whole Stage1 or native-compiler speed claim. 148 focused tests,
       including five new route/alias/failure/compatibility tests. Exact
       changed source direct contextual capture self_backend_emit
       17 funcs/4,922 blocks/19,177 instructions and self_backend_x86_64_linux
       325/63,044/235,561, zero fallback/no libpython; native ELF emitted
       719,656 bytes sha256 5bc7c5fcb6ab64293476e072133bc6f9b522a471622ac83e00fc9ea3bdc438e5
       and 8,587,672 bytes sha256
       c992aa1b0a2fdb964d15cae83cb4ffade718c23e7f3657b60afa4c0fc361a141.
       Publication integrated with the peer Stage2 progress trace in
       3fb598ed (disjoint caller hunk, original trace preserved). Re-ran
       all148 focused tests and exact small streamed ELF on the integrated
       source with trace opt-in disabled; the five-pair timing above is
       isolated pretrace source, not a fresh integrated speed run.
       Timed worker global index (Codex, Linux, 2026-11-26, base
       f1aac35b): the existing x64 globals index was deliberately disabled
       for any timing observer, including exact public timers, because their
       instance/class methods or clock may mutate prepared globals between
       functions. Only the isolated worker's private timer creation marks
       ownership; test unchanged exact class, start/add, clock, instance
       method shadows, scalar/list storage after coarse progress callbacks
       and before function iteration. Ordinary public timer instance shape,
       aggregate/receipt schema and callback order unchanged; custom and
       direct prepared callers remain per-function. Native guard fixture
       compiled and executed from exact candidate source with strict matched
       owned runtime (193 members), public unmarked false, worker true before
       and after native clock/add, all16 aggregates retained. Existing
       global-index and timing tests including marked cross-function mutation
       oracle 503 passed. Actual c_codegen PIDX globals7,764 ×1,039 funcs =
       8,066,796 repeated name reads/inserts; pre-edit isolated CPython
       diagnostic repeated dictionary construction CPU0.513 s vs one build
       0.000458 s (host ceiling about1% of this module backend, not a stage
       speed claim). Source-matched owned native focused fixture imports
       actual GlobalDef class, all7,764 actual global names, exact production
       repeated expression vs one guarded holder, verifies every key and
       ordered digest. Five quiet alternating triples, same binary/control:
       native owner wall5.184989 -> 0.008057 s (-99.845%,5/5), control
       5.229341, normalized -99.845%5/5 vs1.09% drift. Full process
       CPU5.275418 -> 0.148393 s (-97.187%,5/5), control5.349459,
       normalized -97.208%5/5; RSS16,660 -> 16,148 KiB, control16,660,
       -3.073%5/5. All15 exact inventory and digest282919542712
       (8,066,796 total entries); statically linked native fixture,
       no dynamic/libpython dependency. This is the actual repeated-work
       owner, not a whole Stage2 compiler benchmark. Current strict
       625-source direct contextual capture for all three changed modules
       zero fallback/no libpython: timing75 funcs/4,191 blocks/17,313 insns,
       x64 emitter325/63,394/237,351, worker61/12,862/51,534; actual native
       objects emitted. Actual marked c_codegen full streamed proof observed
       every1,039 function sharing one 7,764-global holder, retired after
       module, ELF exact 13,508,968 bytes sha256
       8b6b5c7627edd2d289dd507d7d6522cabc7aaf26f421bf184c08a962cbba2277.
       Five quiet actual full worker_resource_plan triples with private
       worker marker on candidate: backend+obj CPU5.152 ->5.179 s
       (+0.534% median,4/5 paired wins), control5.200, normalized -1.293%
       4/5 within drift2.235%; full CPU normalized -1.694%5/5 within
       drift2.987%, full wall -1.585%5/5; RSS81,180 ->81,248 KiB,
       control81,180, normalized -0.049%3/5 within drift0.256%.
       All15 exact ELF (1,739,176 bytes sha256
       42d60bb810624c8f1b6ac655d6a554ee98a09c32b709acebeb5c974a1b2b58cc).
       Full host worker neutral, no whole Stage1/Stage2 speed claim.
       Latest verified Linux x64 Stage1 CI source c20c2c01, 625 modules/4 workers:
       1,190.927 s /2,851,999,744 bytes (previous1,206.446s);
       all625 indexed-native-sections phase receipts valid. Safe workers
       1,086.151 s (91.2%), export58.174, link34.760; worker infer149.185
       worker-s, codegen3,722.687; backend2,709.438, function emit1,519.736,
       stack-map644.087, verify355.922, layout127.910, frontend generate919.276.
       Largest c_codegen backend61.162s, class_gen58.074s, type_infer50.051,
       cli_bootstrap46.520, cpp.parser44.524. This snapshot predates the
       recent major route optimizations.
       Host verifier successful scalar facts (Codex, Linux, 2026-11-26,
       base b81247f7): current c_codegen direct-indexed verifier-only
       profile (1,039 funcs,95,041 blocks,358,843 instructions,176,450
       values,6,059 types) CPU5.726 s diagnostic,1,401 samples: frozen
       record constructors662 (47.3%), ordinary uses838 (59.8%).
       Definition/type checks repeatedly create two frozen records and
       retrieve value names on success only for errors; storage-only type
       header constructors111 samples. CPython exact owned scalar planes
       now prove successful type/dominance checks without snapshots or
       successful diagnostic name reads; complete exact list/window and
       original accessors required. Every unsuccessful/ambiguous/malformed
       proof falls to original diagnostics, including valid pointer alias
       matching and x86_fp80 storage rules. Native unboxed valueclass route
       unchanged; no verifier thinning or new acceptance. Seven focused
       positive/negative exact diagnostic/guard tests plus existing frozen
       scalar reference unchanged,280 total focused tests pass. Current
       strict625-source contextual capture unchanged c_codegen/worker
       PIDX hashes; changed verifier126 funcs/15,277 blocks/63,173 insns,
       zero fallback/no libpython, actual native object emitted
       2,253,888 bytes sha256
       c6abadae525e3a7fa47945b42cc5566555be823f6dabb759d783d01b1d3c3d42.
       Actual fresh-decode c_codegen verifier owner five quiet alternating
       triples/same-base control: CPU5.178 ->4.483s (-13.428%,5/5),
       control5.184, normalized -13.065%5/5 vs drift0.985%; wall
       normalized -12.785%5/5 vs drift0.578%; RSS266,168 ->266,184 KiB,
       control266,168, normalized -0.003%, neutral. All15 exact
       authoritative value/type/definition/CFG plane digests sha256
       dbbe9100f642800e64edd3fd816ca4d7d502fafbb64cd3f03448dc88839e2e5d.
       Actual full c_codegen ELF exact 13,508,968 bytes sha256
       8b6b5c7627edd2d289dd507d7d6522cabc7aaf26f421bf184c08a962cbba2277.
       Five quiet actual full worker_resource_plan triples: verifier phase
       wall0.67648 ->0.58982s (-12.811%,5/5), control0.67328,
       normalized -13.471%5/5 vs drift7.148%. Total backend+obj
       CPU5.228 ->5.191s (-0.702%,3/5), control5.273, normalized
       -1.211%3/5 vs drift1.115%; full CPU normalized -1.043%3/5,
       wall -0.897%3/5; unchanged function emit normalized +2.087%
       within drift2.242%; RSS normalized -0.015%3/5. All15 exact ELF
       1,739,176 bytes sha256
       42d60bb810624c8f1b6ac655d6a554ee98a09c32b709acebeb5c974a1b2b58cc.
       Full worker neutral, no whole Stage1/native compiler speed claim.
       Latest verified c20c2c01 ARM Stage1 1,614.150s/4.912GB, Windows
       1,804.974s/3.753GB; both625 modules/4 workers. ARM safe workers
       1,464.626s, backend4,578.613 worker-s, dominant backend finalize
       1,766.932 (prepare1,652.249), function emit1,281.335, stack-map
       422.797, verify250.379. ARM prepare coarse children: trampoline
       branches254.303, typed publish207.005, typed validate188.361,
       adjacent stack forwarding167.018, pair64bit131.431 worker-s.
       Windows safe workers1,633.741s, backend4,097.461, function
       emit2,427.939, stack-map857.304, verify478.381. These snapshots
       predate recent optimizations; no causal platform comparison.
       Actual ARM typed publication attribution/rejected candidates
       (Codex/Linux, base7f8a3bda, diagnostic only). Full contextual
       625-source direct indexed no-libpython/fallback0 capture of real
       ARM Linux c_codegen (1039 funcs/95,041 blocks/358,843 instructions)
       and worker_resource_plan (105/13,050/47,696). c_codegen final
       publication one contiguous ordinary run (-2,18182085,1493150),
       1,493,150 events, 558,137 no-reloc words (37.4%), 265,150
       contiguous no-reloc runs (mean2.105), 32,210 runs >=4 totaling
       230,299 words, 494,150 fixups. Full actual PCO22,204,134B sha256
       88554c1a84e58c21ae6def1a6a56049726bdfc5ed8c6c726feb94e427264a7b0;
       exact same PCO hash from independent recapture, frozen publication
       replay with actual metadata directives and both redesigned arms.
       Diagnostic publication CPU4.787s,1,188 samples: next_record_id,
       get_unchecked, set_unchecked, next_record 372 (31.3%);
       frozen CompilerInt4 constructors226 (19.0%). Small actual PCO
       2,777,336B sha256
       cbff0094db13b8b62e2abb637264d6c9902579c1c1d09e7f36f46cc5f2307717.
       First private marked-worker borrowed final run bypassed cursor
       with no copy, 77 host tests pass, strict contextual capture
       fallback0 but native publication gained a valuebox_new edge
       (actual unchanged closure has existing call_slots19,
       valuebox_get_field2; focused kernel contract is stricter).
       Reject this candidate before timing; no production changes.
       Second design left publish_fragment byte-identical and accelerated
       exact host middle-of-run cursor positions with canonical fallback
       for malformed/overridden accessors and final element; 101 host
       tests pass, one stock Darwin native invocation not run on Linux.
       Contextual value_arena199 funcs/11,586 blocks/41,035 instructions,
       emitter324/34,162/143,487, both fallback0/no libpython; native
       next_record no py_obj_call/valuebox edge, but new native cursor
       address/length condition per event. Exact full PCO and all event
       counters match, preliminary non-quiet owner CPU5.162 vs4.706s
       (+9.7% one-off, NOT formal A/B). Reject as unpromising rather
       than consume formal quiet slot or risk Stage2 overhead.
       Both changes restored; no Stage1 or Stage2 speed claim.
       Actual ARM typed trampoline attribution/rejected candidate
       (Codex/Linux, base6238b7e3; source same7f8a3bda, changes tests/docs).
       c_codegen real prepass1,521,122 events,453,460 event candidates,
       113,702 branches,313,692 local labels,4,718 trampoline labels;
       4,695 targets hit (4.13%), all distinct, hops4691 one/4 two;
       memoization has no measured reuse. Diagnostic owner CPU2.935s,
       706 samples, frozen CompilerInt4 constructors223 (31.6%),
       repeated ordinary instruction boundary/empty and label facts.
       Exact host raw scalar positive family body proof and direct
       nontrampoline target skip, canonical caches and public observer
       fallback; fixed initial native outer import-shadow regression
       with distinct private aliases. Strict contextual625 closure
       base76 funcs/29,543 instr; candidate79/32,912, fallback0/no
       libpython. Five native hot function instruction counts and full
       call multisets exactly unchanged: dense trampoline2595/1308,
       outer286/148, CFG driver2955/1690, candidate scanner601/274,
       metadata129/50. 229 host focused tests passed; final alias source
       full actual ARM PCO22,204,134B sha256
       88554c1a84e58c21ae6def1a6a56049726bdfc5ed8c6c726feb94e427264a7b0,
       exact independent base/candidate, guard peak1,154,682,880B.
       Five quiet actual frozen c_codegen owner alternating triples:
       CPU2.877867->2.388236s (-17.014%,5/5), control2.814487,
       normalized -18.465%5/5 vs drift2.363%; wall normalized
       -18.900%5/5, RSS657588->657616KiB (+0.004%, neutral). All15
       exact output/append planes sha256
       37b99072e4762cabc00e1025a0f63ed6bcdd94d22d1493af2c996fe0601c837d,
       1,507,697 output IDs,4,682 appended records,8,316 tokens.
       Five quiet actual small full worker_resource_plan triples:
       trampoline phase0.347060->0.296402s (-14.596%,4/5),
       normalized -17.888%4/5 vs drift3.934%; full CPU normalized
       -1.365%4/5 vs drift2.950%, neutral; full wall -1.206%.
       All15 exact PCO2,777,336B sha256
       cbff0094db13b8b62e2abb637264d6c9902579c1c1d09e7f36f46cc5f2307717.
       REJECT: full worker RSS167080->172072KiB normalized +2.903%
       (4/5 higher) vs same-base drift0.165%. Guarded non-timed
       smaps samples identify no changed owner cache/retained increase:
       cache lengths+sizes all5 planes exact, allocated-block delta+86;
       import/decode RSS0/28KiB difference, candidate ~2.3MB lower at
       trampoline end. First divergence next unchanged conditional-fold
       completion RSS139188->162920KiB (+23.7MB anonymous, file neutral,
       allocated blocks still+86); later peak +6160KiB. Likely allocator
       large-buffer release layout, not a proven live-object regression,
       but real measured peak gate still fails. No allocator tuning.
       Follow-up private exact forward in-place compaction reuses
       rewritten list (source list untouched, no writes reach unread
       events),97 host focused tests pass, exact small PCO; directional
       single smoke RSS172076KiB, still +4996KiB vs base formal median.
       Reject correction too, no formal timing or production changes.
       Actual ARM typed validation attribution/rejected scalar proof
       (Codex/Linux, base2b583f88, real frozen ARM Linux PIDX from
       same backend source7f8a3bda). Initial c_codegen1,525,851 records
       (1,072,391 instructions/453,460 events),2,364,993 tokens,
       498,726 symbol tokens,322,969 symbols, completed prefix0.
       Diagnostic require_complete CPU4.646s,1,145 samples: frozen
       CompilerInt4 constructors735 (64.2%), get4_unchecked165
       (14.4%), token_count67, require_header33. Exact host scalar
       successful proof checks every original header opcode/form/token
       range/event-zero/symbol condition; malformed/custom/debug fall
       to canonical diagnostics/order, no validator thinning.
       Unrestricted candidate focused233 pass and frozen owner smoke
       CPU4.218->.814s exact digest; small full exact PCO but three
       non-timed RSS prescreen triples paired -1.73%,+3.77%,+3.04%,
       median+3.04% vs control1.42%; reject unrestricted.
       Narrow initial-only host threshold >=500,000 records (observed
       c_codegen1.526M, small worker ~200k), small/incremental paths
       canonical;234 focused tests pass, boundary/incremental,
       malformed/debug/custom exact fallback. Actual small canonical
       smoke exact PCO, RSS166144KiB within control. Strict contextual
       625 closure base73 funcs/23,844 instr; candidate76/26,817,
       fallback0/no libpython. Five unchanged native methods full
       call multisets/instruction counts exact; require_complete
       843->929 instructions,399->444 static calls solely host gate,
       no new py_obj_call/valuebox edges, per-record loop exact.
       Actual native CFG raw i1 flag gate block96, true101->helper119,
       false124 helper unreachable; native sys.implementation=pcc,
       only one raw load/branch per call, no host helper execution.
       Clean actual full c_codegen matched screens (not formal)
       exact PCO22,204,134B sha256
       88554c1a84e58c21ae6def1a6a56049726bdfc5ed8c6c726feb94e427264a7b0,
       RSS1,139,324->1,130,544KiB directional lower, validation
       wall4.082->1.138s. Five quiet actual frozen c_codegen owner
       triples: CPU4.208698->.892289s (-78.799%,5/5), control4.238471,
       norm -78.644%5/5 vs drift1.311%; wall norm -78.135%5/5;
       RSS299932->299872KiB norm -0.023%, neutral. All15 exact
       authoritative provenance planes sha256
       8bc489ae9c08df8f800cbff86742dc026acfdd29dc6804c791171f1f560de3a5,
      1,525,851 records/322,969 symbols.
       Actual qualifying ARM emitter own module (143,487 direct
       indexed instructions, initial582,912 records/416,086
       instructions/166,826 events,931,084 tokens,116,110 symbols)
       five independently guarded full backend+object alternating
       triples, same input/controls, all15 exact PCO8,355,115B sha256
       de7b1b8e30a98d36437f9f83ea0b69a119c2afcdfb408f102be5bf7b03d6db06.
       typed_validate wall1.648540->.385848s (-76.595%,5/5),
       norm -76.579%5/5 vs drift5.781%; finalize norm -7.257%5/5
       vs drift1.989%; backend+object CPU51.759876->49.090141s
       (-5.158%,5/5), norm -2.665%5/5 vs drift2.705%; full decode+
       backend CPU norm -2.619%5/5 vs drift2.474%.
       REJECT: qualifying full worker RSS437488->458120KiB,
       control435876, raw+4.716%, paired median+2.520% (3/5 higher)
       vs median control drift.596%; paired -1.157,+5.204,+2.520,
       -.032,+5.807%; max same-base drift6.93% but no stable memory
       noninferiority. Strong actual owner/phase gain alone cannot
       override full RSS gate. No allocator tuning or another
       threshold retry, no production source change/whole Stage1 claim.
       ARM typed exact single-run host snapshot (Codex/Linux,
       base7fcbb394). Actual full c_codegen diagnostic 7 read_span
       calls, first checked -3/six ordinary -2 runs,10,553,762 IDs,
       CPU.933s diagnostic; each close observes distinct equal-length
       temporary and returned list,11.97–12.17MB duplicate per call,
       total84.43MB avoidable second-copy allocation. 176 samples
       161 bounds validation,7 source extend,7 close; high-water
       already set earlier, no big CPU claim. Ownership transfer
       alone rejected: actual projected first-run transient peak
       unchanged24,338,752B because source slice+extend already
       duplicates list, directional CPU slower.
       Exact canonical single-run source shape/method/owner and
       exact scalar bounds proof now creates one independent list
       snapshot, no temporary source slice+arena duplicate and
       second copy. Public/custom/debug/concat/malformed paths use
       original copy/validation/close ordering; returned list not
       aliased to source. Actual1,521,122 captured IDs projected
       owner sha256
       6540f8d9cc232e0eb1096ff231835cc3d36e9454b52589b57ec5c9dbccb6d536,
       direct slice diagnostic traced peak24,338,752->12,169,320B
       (-50.0%, -12.17MB), retained list12,169,088B unchanged;
       one-off CPU.090->.121s slower, not a speed claim.
       Initial in-module helper native hot edge regression rejected;
       final isolated CPython-only helper captures canonical method
       identities at import, preserves custom callback fallback.
       Focused108 tests pass (ordinary/checked exact independence,
       observed copy/extend/close clears temporary, malformed/custom
       comparison/concat fallback). Final strict contextual closure
       626 modules (+1 helper), context49 funcs/9000 instr, helper
       7/7462, both fallback0/no libpython. Six unchanged native hot
       methods exact instruction counts/full call multisets incl
       record1371/821 calls and operands743/399. read_span raw i1
       gate block18 true20->helper40, false21 helper unreachable
       by actual CFG; added dynamic calls only host branch, native
       sys=pcc false, canonical native loaded-method4/call_slots2.
       Clean full large c_codegen same-input screen exact PCO
       22,204,134B sha256
       88554c1a84e58c21ae6def1a6a56049726bdfc5ed8c6c726feb94e427264a7b0,
       CPU128.504->127.827s, RSS1,133,020->1,132,252KiB neutral,
       not formal high-water gain. Five actual small full
       worker_resource_plan alternating triples, exact same-base
       controls, guard COMPLETE246.038s (transport reattached,
       all15 arms present and lock released). Backend+obj CPU
       15.46386->15.40708s raw-.367%, paired norm+.526% within
       control1.086%; full decode+backend CPU norm+1.091% within
       control1.321%, wall neutral. RSS168180->168024KiB,
       paired norm-.095%4/5 vs control.005%, slight lower.
       Unrelated stackmap phase norm+3.783%1/5 vs control.282%;
       finalize norm-1.449%4/5 within control1.922%, total CPU
       neutral. All15 exact PCO2,777,336B sha256
       cbff0094db13b8b62e2abb637264d6c9902579c1c1d09e7f36f46cc5f2307717.
       Admit scoped structural temporary memory allocation reduction
       only, no large high-water/whole Stage1 speed or target claim.
       Hosted inline-edge route screen, Codex Linux 2026-11-27:
       exact #638 artifact15153817780 ZIP SHA256
       62538fcc9ff6462db66c16a074539e90ed57ec58bd6781943fec68167bc120e7,
       retained Stage1 log SHA256
       c2c627b1a313694c0f2fb74a6f160d7f1f1544265e48ce71cb2cf08418fbd511.
       All625 CFG rows inline errors=0: 4,239,220 blocks,
       712,477 call.cont+361,228 err.frame=25.33% theoretical
       affected block families, 132,229 explicit cleanup blocks remain,
       16,506,609 instructions. Actual admission all825 complete,
       codegen625 worker-wall-s4369.692 (max singleton92.483s),
       export100.718, summary53.722, preload23.672; codegen
       1094.022s wall of1189.280s total. With unchanged workload
       ideal six-worker codegen alone728.282s, >=58.8% work reduction
       even ignoring serial phases needed for300s; if existing
       non-codegen95.258s unchanged, ~71.9% codegen work reduction
       needed. Theoretical lower bounds, not achieved or predicted
       whole-stage speed. Hosted harness explicit capture/emit skips
       automatic default inline edge unlike bootstrap.py. On exact
       current source ff7b555f current indexed626-module/79-chunk
       context export SHA70be22dc0c0dcd41d88ba7a347134a586f486a24cb89532936db6865773df8e3,
       candidate would preserve explicit off and bind effective route
       to shared receipt identity. Test-first config5 fail, actual
       worker default across all3 targets3 fail with explicit-off3
       green -> candidate expanded134 pass; four exact baseline same
       Linux-to-Darwin executable format failures. Exact large class_gen
       CFG87118->72293 blocks (−17.02%), call.cont13713->0,
       err.frame7268->6156, instructions335663->347182, funcs727
       unchanged. c_codegen exact #638 OFF shape95041->78984
       blocks (−16.9%), call.cont15265->0, instructions358849->369582,
       funcs1039 unchanged; both strict zero fallback. One-off CPU
       class72.737->70.746s, c_codegen75.217->74.234s, RSS
       784740->769164 and839280->812188KiB, directional only.
       ELF external definition ABI exact943/1220 symbols, sole new
       undefined symbol py_exc_append_frame_indexed; exact default
       ELF registered roots, explicit packed-stackmap focused tests
       pass. Fresh exact owned archive SHA085c8c95bd954882a2d791158a3c5f7fa9400804c0a04dd8b002fe39fc07642b,
       two native Linux traceback fixture ELFs (OFF/ON) exit1,
       exact stdout SHA5e3f85de9e09e60f0c7656d6fddd767d9073aa410af6704d79330fff76760f25,
       stderr SHA6dad2f3118b13d2fbb58d5e0af237dc9c7d11f3d8f086354ad8cf32b4aa09fb7,
       distinct exception sites, caught/reraised/uncaught traceback
       source and two finalizers exact. Full class five alternating
       controlled isolated triples all15 strict native ELF hashes
       stable (OFF 12,661,992B sha4d5cfd9eabb6aea5b4d460eb078fde01282f29e8cc5a3f012462477bc06fc014,
       ON12,962,904B sha8aaa34b73e7b8357372d72839820db568f911e4ad9ffba27bf17be957e59dcff).
       CPU80.252->80.055s control79.438, norm+.263%1/5 vs
       control1.014% median/2.247% max; wall norm+.446%0/5
       vs median control1.003%; peak RSS785360->768204KiB,
       norm−2.359%5/5 vs control .381% median/.831% max.
       Backend−2.089%5/5, stackmap−17.716%5/5, but verify
       +12.261%0/5, frontend+2.947%1/5, object assemble
       +105.948%0/5 (assembly emit_finalize+166.720%0/5),
       ELF grows2.376% exact. Reject broad default despite robust
       worker RSS reduction: all five wall directions adverse,
       end-to-end CPU no stable benefit and deterministic
       object/verify/finalize growth. No medium formal or source
       production, no resource/gate changes, no whole Stage1/2 claim.
       Follow-up actual inline finalize/verify attribution (680df6b3,
       default remains OFF): #639 exact x64 625 modules, all inline0;
       4,242,421 blocks/712,904 call.cont/361,488 err.frame/
       16,518,217 instructions, worker-codegen 4267.136s (ideal
       six-core lower bound 711.189s, not measured), backend2615.803s,
       emit1522.359/stackmap551.643/verify351.697/finalize37.123s.
       Exact class phase15 OFF/ON labels321508->262893, relocs280563
       ->284320, leaf .483/.478s profiled; GC gen2 both zero collected,
       instrumented1.039/.964s. Frozen owner plain .4874/.4827s;
       no causal proof of prior finalize increase. ON verifier actual
       landing scans13713 queries/1,704,204 row comparisons, 106
       functions sorted with threshold256 cover99.666% comparisons.
       Dense private index five exact retained-result triples CPU
       norm−95.952%5/5 but RSS+740KiB0/5, reject. Zero-allocation
       sorted lower-bound candidate retained-result CPU−94.577%5/5,
       RSS+136KiB0/5 within max control. Exact smaps/tracemalloc peak
       +32B, no table allocation; production-shaped immediate-consume
       independent 5 triples CPU−94.508%5/5 RSS−8KiB4/5, and
       after valueclass dynamic getattr hardening repeat−94.583%5/5
       RSS−4KiB4/5 (one tie, unchanged controls). Actual per-slot
       checksum 88888563/41139 exact; dense full class screen exact
       object verify−10.97% directional only. Strict native direct
       valueclass observer attribute first RED, dynamic getter fixed
       zero fallback/no libpython; new host-only proof isolation
       test-first1 red->141 focused passes, actual context627
       modules (added one helper), verifier129 funcs/65589 instr,
       helper10/3468, zero fallback/no libpython. **Reject sorted
       candidate**: three unchanged native verifier hot methods
       reproducibly differ even after host isolation (_build_definitions
       3266->3670, _verify_ordinary_uses2688->2866,
       _verify_inline_error_edges1085->1080 with generic len);
       calls/GC roots change adversely on OFF. Do not proceed formal
       full worker or enable default despite isolated CPU wins; no
       source production, no gate change, no Stage1/2 claim. Actual
       general OFF profile _host_definition_planes343735 calls
       1.204s, _host_definition_type_matches2.508s inclusive profiled
       (not uninstrumented formal).
       Exact current d222ab42 class_gen OFF strict capture closure626,
       same 727 funcs/87118 blocks/335663 instr/inline0, PIDX
       SHA cf4113ccce175998c3aeab8d47b2d1ddc0a8961a9e96d163156012e5354ea4b0.
       Actual diagnostic 343735 plane checks all successful, 343008
       per-operand successful type checks (141383 same ID, 201625
       canonical aliases, no fallback), instrumented plane inclusive CPU
       .3949s (~1.15us/call), type proof1.1289s (~3.29us/call),
       verify6.162s incl observers, not formal; largest method33437
       checks/38ms and class init25992/30ms. Standalone plane proof
       even eliminated entirely ceilings at ~0.5% whole class worker,
       complete type proof~1.4%; no standalone source change, no
       global cache and no native inference risk incurred. Release
       verifier proof unit.
       Actual same class OFF PIDX cf4113cc (d222ab42) prepared through
       exact verifier/layout/target passes, one shared worker globals
       index as production, all727/727 functions admitted host protocol
       facts: 7003 globals x727 =5,091,181 repeated exactness checks,
       CPU .7501s inclusive of 9.6304s planner (7.79%), ~0.94% full
       class worker ceiling; 184919 records exact plan digest
       522a76008e3e8a292aae649c7c8a4721973d65670f98c2c753c60f945a06c02b.
       Diagnostic only, native raw skips proof. Existing globals-index
       holder can be populated after ineligible/text/custom-first
       function without initializer exactness; presence cannot certify
       later direct functions, duplicate-last map may hide earlier invalid
       rows. New holder marker/arg or tightening native global builder
       risks semantics/hot parity for bounded ceiling. Reject standalone,
       no source change or cache.
       Current exact d222ab42 class OFF PIDX cf4113cc marked full
       streamed backend sampling: CPU44.068s, ELF12,661,992B SHA
       122f82e57da42757dbdb6d9f6c07ed14c2f4460c1e81ff89011f596dfabb7a8f,
       10231 samples/4446 function emit: block emit1588, register
       copies1168 (dead moves368), frame promotion983 (root offsets136).
       Exact 727 plans/184919 records, 11,322,013 root location visits;
       adjacent tuple identity128363 records, retained3,381,583
       visits (−70.13%), distinct19123 tuples/1,130,439 location
       visits; all exact dataclass/int, reloads941 untouched. Canonical
       owner CPU.564s, offset digest da47de4eb2cf125c4423e4842d9ad3092592f8c6d81cb8cf1195b4ffd27dad1c.
       Adjacent local identity candidate only after exact immutable
       tuple/record/location/reload guards; any custom callback disables
       reuse for remainder, packed route untouched. Test-first1 intended
       24 vs3 loop reads red+2 observer controls green ->167 focused
       pass. Native strict contextual module626-source closure both,
       43 symbols, zero fallback/no libpython; 40 unchanged hot methods
       exact call/instruction parity, target914->2374 instr and module
       init+20/static+2. Exact frozen all727 plans pickle25.74MB
       SHA39b04cf3c05892c6d61955b5a8550ac2497b72e1551cad2afb50212795c0f9f7.
       Host five alternating triples all15 exact sorted and ordered
       offsets (ordered SHA66a932abe5dae0d49b29a2e2a3e66384205ad4ada95d7582a70ecb2a09ccc0d1);
       CPU .554843->.351331s, control.537560, norm−34.613%5/5
       vs control2.379% median/5.635% max; RSS173148->173144KiB
       control173144, norm0%,2 wins/3 ties. Directional whole class
       ceiling small (.176s screen), no full-worker gain claimed.
       Actual native focused projection preserves 1050 root identities,
       1218 shared tuples,14848 largest class-init record slots, exact
       every expected key1050/checksum7772008, TSV1.114MB; fresh
       source-matched owned archive SHAaafe3f12e2dc790a0f4b1a8df7f4b77214ad53564c9fa2a07a6e7ae05778198a.
       Full actual native source-closure fixture guard **TIMEOUT**
       400s, peak715MB, no binary, active safe worker63/65; no
       resource/deadline changes or retry. Reject current candidate
       because native actual semantics/perf unqualified and small full
       worker ceiling, despite robust host owner and unchanged native
       hot parity. No source production/whole Stage1/2 claim.
       Actual current c912 post-landed sparse x64 dead-move attribution
       same exact d222 class OFF PIDX cf4113cc, emitted ELF SHA122f82e5
       exact; all727 liveness calls followable, independent instrumented
       replay exact masks. 1,491,222 lines,925090 effectful(62.03%),
       554271 neutral(37.17%),11861 already-dead; dense3 arrays
       35.91MB aggregate list storage. Worklist877648 pops/421778
       blocks,2,909,457 line steps incl1,216,094 skip/neutral(41.80%),
       final1491222 incl554271 neutral; canonical owner CPU1.9845s
       (~4.5% backend44s, directional). Frozen exact727 input masks
       48.25MB SHA85dd1a745527558b3906536172bfce333980d3c13a336239a3c1743712beaf3f.
       Packed sparse event prototype all727 exact, CPU1.426->1.548s
       (+8.57%), estimated list/scalar bytes62.43->37.89MB (not
       measured RSS), reject CPU. No-array boundary trim prototype
       removes554159/554271 neutral (99.98%) from existing block
       ranges after CFG ownership, CPU1.511->1.343s directional.
       Candidate private parser only, original observer8-arg path,
       kind2/zero-write kind1/unknown target preserved; test-first3
       intended red->168 focused pass incl512 padded random CFGs.
       Strict native current closure626 both, fallback0/no libpython;
       33 unchanged non-target hot methods exact, target dead4300
       ->4648 instr/forward5833->5915; native adapter17->21 and
       optional vector removed, module init/fini literals only.
       Actual frozen727 owner5 alternating triples all15 exact
       mask SHA2106422e311ac3619efcbcc82ef00ab4b0bd6f7ba817fe428cab9e97a352f807,
       CPU1.430475->1.300189s control1.442300, norm−10.967%5/5
       vs control1.206% median/2.482% max. **Reject stable RSS
       regression** median49912->50484KiB (control49912), norm
       +1.146%0/5 vs max unchanged .016%, despite no new array
       (new boundary scalar objects plausible; not causal proof).
       No source production/full worker claim, no further native
       build. Maintainer 2026-11-27 new priority same-post-pass-IR
       cross-backend static hot-function codegen analysis (no external
       backend, actual instruction/stack/spill/scratch count) before
       any new allocator design; local [skip ci], no new complete CI
       snapshot/dispatch/rerun while queue/running.
       Same-IR owned backend diagnostic (current c9120f18, Linux,
       no source changes): actual optimized Linux runtime module
       receipts (atomic/threads, original archive aafe3f12, generated
       before backend emission) source+IR SHA exact current source.
       py_obj IR b8abe0da9c75e45aca09f52c0ba66ca95db61a52922aa684676be1066ca0d864,
       allocator IR d867dbd55fad1f5afe0646b2536f330cad91057391ff4ee0225f782ba0427a20,
       JSON IR e6c60a9f6c262801b729fa90b55db281d979c3dfb736219ecac0870b2e6cc419,
       vthread IR 8f4aa722c9f60397f1c5917aee8c3703854b38e817782b1b2584c62b12421084.
       Input bytes identical except target triple x86_64 Linux ->
       aarch64 Linux, empty datalayout; each hot body hash exact.
       Full py_obj145/JSON63 defs x64 owned ELF byte-exact original
       receipts198915d3/8c862586; allocator8/vthread6 selected
       bodies same projection both arches with all original globals/type
       context; exclude free/unrelated x86 inline syscall bodies, no
       Darwin TLS/platform frontend differences. Owned AArch64 default
       optimize=False and explicit optimize=True (callee-saved regalloc
       default ON both); all ARM encoded instruction words equal final
       instruction lines. 29 functions/87 exact arms: x64 11702 static
       instructions vs ARM default7956/opt7393 (1.58x); x64 local rbp
       loads592/stores861 + rsp721/165 =2339 direct stack accesses,
       ARM opt x29 238/172 + sp1146/202 =1758 paired slot accesses,
       frame address materializations87 (direct syntax lower bound,
       computed aliases excluded). x64 r10/r11 scratch mov2694
       (1324 register transfers) vs ARM x9-17 mov537(263 transfers),
       scratch stack537/267; calls531/519, branches1421/1240.
       All direct stack instructions classified; not dynamic req/s.
       x64/ARM opt static store_root871/453,take215/113,known131/67,
       retain101/47,release156/73,incref116/46,decref157/73,
       gc_alloc207/138,load_ptr57/30,allocator alloc_object197/148,
       malloc193/135,object magazine take313/186,raw take327/199,
       granule start408/233,JSON loads175/97,dumps_ex108/41,
       parse_string1729/1303,parse_value672/388,dump_value1398/942,
       vthread join645/440,complete929/631,complete_unlocked380/234.
       Controlled same-IR static isolation (no source edit; x64 only
       late frame promotion bypass, other peepholes retained; ARM
       optimize=True callee/whole-func/call-result flags OFF):
       x64 no-promotion12923->11702 current(-9.45%), direct stack
       6085->2339(-61.6%), scratch mov4720->2694; ARM block-local
       13689->7393 function-wide pools(-46.0%), stack3791->1758,
       scratch mov2204->537. Store_root x64 1103->871 vs ARM
       1185->453; parse_string1964->1729 vs2188->1303;
       complete992->929 vs1118->631. ARM-local even exceeds
       x64 current: major difference is whole-function allocation
       plus operand integration, not ISA alone. Static only.
       GC root contract: x64 late destructive promotion excludes
       mapped root/reload-source slots; reload destination plain
       stores can themselves be promoted (explicit native test),
       but pointer retirement/zero-home proof also excludes reload
       destination, alloca/sret/overlap. ARM allocator pre-selection
       excludes reload destination because slot refreshed only after
       moving safepoint. Active GC nonmoving/read-only stack scan
       does not justify deleting mapped physical stores: mutator
       alias/root APIs/callbacks still write. Next (latest actual
       Stage1 codegen priority): (0) opt-in generic x64 pre-selection
       register/operand prototype, first narrow exact indexed
       block-local integer SSA, non-addressable/nonmapped and
       write-through homes, no call/unknown/PHI/inline-edge hazards,
       flags/width/ABI and conservative roots preserved; use existing
       late promotion unchanged. Main target extend whole-function
       saved-register pool/PHI and direct operand model after safe
       subset, not local-only. Same IR static counts + source-matched
       native semantic/handler CPU/req w1 five alternating control
       pairs and RSS before default change, w4 final if noise permits;
       no new CI snapshots, retain inline OFF;
       2026-11-27 source-local opt-in prototype results, **all
       rejected, no production**. Same 29 actual receipt-verified IR
       body hashes/default four ELF hashes unchanged: local r8/r9
       integer-only11702->11699 instructions (tiny); competing
       whole-function integer r12-r15/write-through with exact CFG
       and PHI/ABI native tests11702->12892(+10.17%); register-only
       all-use/private pointer/direct memory/parallel-edge variant
       11702->12802(+9.40%), 17 focused incl native call/loop/ABI pass,
       not qualified full source. Existing late physical-slot plan
       oracle exact29:486/652 ranked offsets assigned,2742 SSA
       generations incl844 ptr,817 i1,901 i64;1880 reads/2947 stores,
       1805 dead stores,553 retirement decisions, explaining competing
       SSA pool regression. Same-plan origin-group direct lowering
       195 intact groups,377 raw instructions avoided, existing
       copy pass already removes293; final11702->11618(-.72%),
       bytes53929->53664, all owned encoded, no runtime speed claim.
       Whole-slot caller eligibility union24/166 unassigned, maximum
       40 loads/23 stores; same machine CFG per-generation proof
       only12 local unassigned generations/28 accesses(16 loads,
       12 stores), 4 pointer gens, baseline exact, no allocation
       implementation. Stop retrofits. Raw guarded diagnostic
       receipts/scripts under local stage1-cross-static, old
       prototypes retained, source restored clean.
       2026-11-27 **accepted explicit host-compiler x64 Linux
       application opt-in** (maintainer directed merge of demonstrated QPS
       improvement separately from compilation cost). Set both
       `PCC_SELF_X86_TYPED_MIR=1` and
       `PCC_SELF_X86_TYPED_MIR_COMPACT=1` for the qualified mode.
       Default, native self-host/Bootstrap Stage1/2 and Windows routes
       remain unchanged/OFF. Native `pcc1` executing the allocator is
       not qualified: explicit MIR requests fail before emission/runtime
       selection/worker cache, rather than pretending to support it.
       One per-function typed MIR allocation owner: exact CFG live sets,
       call/flags/fixed-register constraints, parallel PHI/ABI copies,
       pinned mapped/reload/alloca homes, compact spill frame, exact
       root/derived reload remap and pointer retirement; whole-function
       legacy fallback for unsupported shape or proven packed graph bound.
       Standard worker-object and runtime archive provenance/cache keys
       bind both opt-in flags; unset/OFF keys remain unchanged and orphan
       compact is OFF. Direct object cache already keys PCC settings and
       is host-only. Actual same receipt-verified IR 58 hot functions
       15435->12005 static instructions (-22.22%), 71078->53822 code
       bytes (-24.28%), 52 improvements, six exact fallback, no static
       regression; this is not a dynamic instruction measurement.
       Actual giant proxy-resume function 41629 indexed values,8383
       blocks,44007 operations: sparse pointer-death snapshots and exact
       uint32 CSR replace dense graph storage; old dense vs packed
       owned PCO byte-identical, peak1365192704->982167552B (-383MB),
       one-off76.25->70.57s under unchanged cap. Full8 runtime modules
       (1150 functions), five alternating base/candidate/unchanged
       controls, 15 exact deterministic owned ELF: normalized CPU
       -3.05%, wall -3.20%, RSS -3.53%, each5/5 beyond max control.
       Actual source-matched native handler archives on source014a plus
       measured packed MIR codegen0d1bf7d5: all193 post-pass IR/source
       hashes equal,185 changed owned objects, no host cc; corrected
       complete30-file gateway fixture. 60 exact formal handler runs
       (five triples per worker count and separate latency runs),12
       exact semantic runs (777 synchronous cycle finalizers/weakrefs,
       four carriers/12000 unique shared list entries), plus targeted
       ABI/CAS/PHI/root-reload callback/exception tests. w1 C100x2000:
       QPS16460->23924, control17541, norm+36.39%5/5; CPU61.53->42.10us
       control57.15, norm-26.35%5/5; RSS36.778->35.541MB norm-3.36%5/5;
       max STW2175->1647us norm-24.38%5/5. Separate w1 C100x500 p99
       5.689->3.686ms control5.744, norm-35.83%5/5. w4 main QPS
       41150->51955 control41777, norm+24.36%5/5 within max control
       (27.68%, directional); CPU90.18->70.81us control88.95,
       norm-20.39%5/5 beyond controls; RSS norm-3.25%5/5. Separate
       w4 latency QPS norm+33.94%5/5 beyond control, p99
       1.383->1.032ms control1.432 norm-27.93%5/5. w4 STW directional
       only, high control drift. w1 allocations exact; w4 scheduling
       counter jitter within controls.
       **Compile cost remains RED on large real app:** corrected
       application-only build104.56->168.39s/784531456->1310507008B,
       one-off +61.0% wall/+67.0% peak, but both under original
       1.5GiB+2GiB reserve (old dense candidate hit cap). Full Stage1/
       Stage2 targets not claimed. Final source-only native-local
       renames/cache-policy guard preserve exact same IR/ELF/PCO;
       strict closed-world capture629 modules, seven changed production
       modules all zero fallback/no libpython, 82 focused tests pass;
       broader151 pass and identical17 preexisting baseline failures.
       No dedicated CI dispatch. 2026-11-27 scoped large compile
       follow-up: preserve the original immutable stackmap location/
       reload tuple and element identity sharing during compact MIR
       frame remap (private exact layout only, per-function identity
       checks retaining sources, no distinct-equal conflation or
       global cache; subclass/patched layout remains uncached).
       Actual giant same-IR worker on source81b2: OFF15.35 CPU/
       16.08s wall, peak286.3MB; old MIR70.64s/979.4MB. Its original
       plan has21175 records/4996907 location refs, only1316 tuple
       identities and1109 objects. Naive remap destroys sharing:
       13.414 CPU seconds and current RSS289.3->929.6MB. New
       full giant worker53.62s/334.4MB (-24.09% wall/-65.86%
       peak, one-off), remap0.262s, lowering4.866s vs7.719;
       owned PCO byte-identical to prior qualified MIR output.
       Actual frozen plan/layout owner five alternating triples:
       15/15 exact semantic digest (all21175 records/4996907
       locations/4721 reloads, original identity roundtrip checked),
       CPU15.5067->0.1823s/control15.0253, norm-98.787%5/5;
       peak707044->43488KiB/control707040, norm-93.849%5/5;
       wall norm-98.794%5/5, all beyond unchanged controls.
       Full8 same frozen actual runtime IR/source receipts (1150
       functions, old MIR vs new, both compact ON) five triples:
       all15 exact previous ELF hashes; CPU/wall norm-1.25/-1.29%
       5/5 within control drift (noninferior), RSS52244->52012KiB,
       norm-0.436%5/5 beyond max0.0842%. All58 static assembly/object
       and admission exact; 19 focused MIR tests incl identity,
       offsets/reloads, malformed/custom observers, native root
       semantics; strict closed-world629 frame module35 functions,
       zero fallback/no libpython. No new runtime QPS claim from
       identical bytes, no whole Stage1/2 claim; #647 snapshot
       precedes this source. Defaults and resource caps unchanged.
       2026-11-27 scoped packed MIR clobber-union follow-up:
       actual giant20999 machine clobber ops (one mask967),3102493
       repeated allowed-register intersections vs16105 union IDs.
       Accumulate exact affected live virtual IDs per signature,
       apply commutative intersections once before coloring; dense
       small route unchanged, no early allowed-mask reader. Exact
       test-first repeated24 call mask visits25->2 and fixed shift/
       division/call allowed/interference/register/saved parity,
       21 focused MIR tests. Actual same-current giant clean one
       pair (old allocator source function vs new): wall59.23->55.98s
       (-5.48%), peak335.48->330.63MB, allocation39.685->36.105s
       (-9.02%, CSR phase noise+1.44s); actual register/saved digest
       and complete owned PCO byte-identical. Confounded initial
       capture screen retained separately, not used for total gain.
       Actual frozen allowed-update owner five alternating triples,
       all15 exact41629 allowed masks and same MIR/CFG: CPU
       8.4583->0.20137s/control8.3368 norm-97.5845%5/5 beyond
       max7.67%; wall norm-97.59%5/5; peak+12KiB within controls,
       all15 pre-owner peak == final peak (zero incremental RSS).
       Full8 frozen actual IR/source receipts (1150 functions,
       both MIR+compact ON) all15 exact prior ELF: CPU/wall norm
       +0.39/+0.36%4/5 within high controls, noninferior; RSS
       52544->52292KiB norm-0.480%5/5 beyond max0.1445%.
       All58 static outputs/admission exact; strict contextual
       planner629 closure/77 functions/41165 IR inst zero fallback,
       no libpython. No new runtime QPS or whole Stage1/2 claim;
       #647 snapshot excludes this source, no new CI.
       2026-11-27 exact inline aggregate source-policy correction:
       bind CompilerInt2 phi incoming and CompilerInt4 type layout
       to distinct typed temporaries before reading first lane;
       no call-order or lane-change, no gate weakening. Existing
       source inventory node red (exact two violations lines299/355)
       ->22 PASS with 21 MIR tests. Strict context exact source
       8704c1a7/PIDX79e432f5:629 closure,77 funcs/41271 inst, zero
       fallback/no libpython. All58 frozen actual hot functions,
       both routes116 rows,34 assembly/object/admission artifacts
       byte-identical; full8 1150 functions all prior ELF exact.
       No performance or full-CI qualification claim; inventory
       class/projection classification independently owned by CI.
       #647 snapshot unchanged, no new CI.
       2026-11-27 scoped packed CSR sparse-wide enumeration:
       actual frozen giant MIR41629 values/8383 blocks,32216
       nonempty masks/5034145 ordered undirected edge events per
       pass,20.10M u64 words but only288119 nonzero. Private
       nonnegative packed-only 256-bit skip-empty iterator in
       degree reservation and CSR fill; small masks/dense path
       unchanged, no extra retained adjacency. Test-first2 reds
       ->24 pass incl strict ascending/random/high ordinal and
       wide packed-vs-dense edge/color/allowed parity. Attribution
       exact ordered digest d9afcaa8; baseline12.738s,64 chunks
       3.824s,256 byte chunks1.986s,selected skip256 2.089s.
       Clean same-source giant batch pair (old iterator restored
       only at two new call sites): complete PCO5f0ed888 exact
       and register/saved digest76dcdb3e exact; guard wall
       52.801->31.894s (-39.60%), allocator CPU34.054->10.501
       (-69.16%), CSR18.183->5.481 (-69.86%). Sampled tree peak
       330.55->334.50MB directional+1.20%; actual allocator-end
       process HWM322572->322452KiB, whole process HWM
       329560->330092KiB (+0.16%), one pair not formal.
       Actual frozen degree-owner5 alternating triples all15 exact
       5034145 events and degree digest22f87dca: CPU median
       13.2778->2.0491s/control14.2948, paired norm-84.83%5/5
       beyond max13.83%; wall norm-84.82%5/5. RSS187420->
       187364KiB noninferior, all15 pre-peak=final.
       Full8 1150 funcs all15 exact previous ELF; original RSS
       norm+0.354%0/5 within max0.379% prompted hold. Actual
       invocation count0 (all1150 funcs; no packed path used).
       Symmetric initialization keeps old/new callable objects
       and preimage read identical both arms, repeat15 exact:
       RSS52204->52140/control52256KiB norm-0.222% mixed2/5
       within max0.523%, original adverse direction absent;
       CPU/wall norm-4.63/-4.67%3/5 within ~13% controls, no
       small-module speed claim. All58 static116 rows/34 artifacts
       exact; strict629 closure/81 funcs/44204 inst fallback0,
       no libpython. No new runtime QPS/whole Stage1/2 claim;
       #647 excludes this source, no new CI.
       Default MIR-OFF x64 stackmap pointer-alias attribution
       (2026-11-27; independent scope, no production): exact
       #647 Stage1 629 worker receipts function_emit1562.305,
       stackmap649.037,layout147.837 worker-s. Frozen actual
       class_gen direct PIDXcf4113cc/source2b1ada66,727 funcs,
       87118 blocks/335663 inst, no fallback. Current planner
       exact source4a28d71a; guarded18.87s/299.79MB, owner
       diagnostic8.154s/184919 records, plan digest522a7600.
       Resolver137330 calls,120282 exact same-object/content
       repeats87.6%;136612 zero-hop99.48%,718 single-hop.
       Instrumented inclusive resolve CPU0.619s, repeated subset
       0.533s (includes observation overhead), ideal ceiling
       ~6.5% planner, ~1% old complete backend, before extra
       cache lookup/retention; no native benefit inferred.
       Reject standalone cache, preserve original hash/cycle
       validation; no source change. Next actual general OFF
       function-emission structural attribution, no gate relax.
       Default MIR-OFF x64 CFG shell/PHI emission attribution
       (2026-11-27; no production): same frozen actual class_gen
       PIDXcf4113cc/current owner9f01a770/bfd99a92, complete
       streamed ELF12661992B SHA122f82e5 exact prior. Guard
       45.67s/439.83MB; 727 functions/87118 projected block
       shells+terminators,851 PHIs/1810 incoming;122547 phi
       edge requests,120737 empty targets98.52%,2871 incoming
       comparisons. Shell CPU0.7766s, terminator0.5532s inclusive
       PHI0.1032s; ideal eliminating both only1.33s/40.05s
       whole streamed backend3.32%, shell alone1.94%; actual
       direct route still must emit branches/parallel copies.
       Reject isolated CFG shell rewrite; retain canonical
       block order, explicit diagnostic/custom/public routes
       and stackmap label semantics. No native benefit inferred.
       Next seek dominant indexed instruction/emit structural
       owner, coordinate separate Stage2 tuple projection.
       Default OFF post-selection machine-event attribution
       (2026-11-27, no production): frozen class_gen PIDXcf4113cc,
       current x64/frame/copy source9f01a770/e2f2fa84/a038f9cb,
       exact complete ELF122f82e5. 727 funcs/1,675,103 initial
       lines; total pass CPU10.908s /43.918s instrumented backend
       (24.8%), frame promotion4.365s, copy forwarding4.755s,
       all other1.788s; unchanged line identity88.74-97.76%
       across phases (includes labels/directives, not parse hits).
       Actual frozen c_codegen phase inputs SHA086ec069 (1039
       each,1.733M frame/1.633M copy), detail replay11.82s/
       65.7MB, exact prior complete text frame578e41d4/copy
       ba54e21e. Slot parse432514/noncandidate accesses129546,
       copy effects1264919/droppable839351/address457353/
       two-op parse72655. Diagnostic count/sample owner CPU
       frame3.681+copy6.313; per-call SIGPROF reset undersamples
       tiny functions, observation overhead explicit. Frame574
       samples: inline/slot/access parse74 (~.48s), pool refs64
       (~.41s), liveness84. Copy1125: split/two-op57 (~.32s),
       effects133/address29 (semantic decode not previously
       available), dead_moves384 (~2.15s). Pure lexical parse
       ceiling~.8s (~1.8% actual class backend); even including
       pool/address share ~1.37s (~3%) before metadata expense.
       Reject second machine-event stream architecture from
       this evidence; no source change, no native inference.
       Dominant combined owner is dataflow/analysis semantics.
       Next actual dead-move/dataflow structure beyond rejected
       trim/sparse owner, or native profile with proof.
       Default OFF strong-liveness unchanged live_out attribution
       (2026-11-27; exact frozen actual class_gen dead inputs
       SHA85dd1a74/current copy ownera038f9cb; no production):
       guard2.09s/51.35MB,727 complete dead masks exact
       digest0266a8b0. 421778 blocks/877648 worklist pops,
       2909457 line steps,30154 already-evaluated unchanged
       live_out revisits (3.44% pops)/146148 line steps
       (5.02%). 425716 changed revisits/1272087 steps;
       final1491222 line selection steps unaffected.
       Even perfect skip<.1s of ~2s owner/<.25% complete
       backend, before sentinel/branch cost. Reject standalone
       change, no repeat of prior trim/sparse prototypes.
       Stop minor host-only dataflow points; next structural
       production Stage1 wall owner or actual native profile.
       Stage1 wall scheduling/startup residual attribution
       (2026-11-27; no production): #647 x64 exact629 singleton
       admissions total4713.451 worker-s, actual span1179.307s
       vs ideal4 1178.363s (gap.944s); scheduler near optimal.
       Sum infer+codegen4108.613 worker-s; residual604.838
       worker-s includes manifest/import/sparse exports/AST/
       contexts/cleanup, not all startup. All629 indexed_exports=1;
       79 one-export residual median.573s,125 exports2-10 .619,
       188 exports11-50 .783,199 exports51-150 .861,34>400
       median2.777s. Existing opt-in host batch2 requires legacy
       full graph, cannot serve current sparse root semantics;
       no default switch. Existing source200KB/AST6MB thresholds
       eligible619/629, ideal max309 pairs, ≥.503s reusable work
       per avoided process required for3% whole Stage1 1295.102s
       before per-root decode/reset/admission/scheduling cost.
       Actual historical626 sparse graph exports SHA70be22dc,
       v4 standard worker (current owner SHA1735f6be), selected
       six real module roots with actual exports1/3/20/97/150/443.
       First coarse poll missed a rapid atomic phase (native PCO
       success, retained observation failure). Corrected external
       .2ms poll guard12.20s/213MB,12/12 exact paired PCOs,
       all12 manifest reports, spawn->manifest median.2247s/
       max.3685; manifest->exports varies.16-1.47s; two exports
       snapshots overwritten, explicitly unknown. Four extra
       standard importtime diagnostic process runs exact in2.90s;
       nonnested total imports .306/.374/.364/.405s incl CLI,
       post-manifest layer1 .0665-.0886 and later backend imports.
       Even ideal all-import reuse max.405s x309 /4=31.3s
       (~2.4% whole stage), below threshold before reset; cannot
       add spawn->manifest (overlapping imports). Reject sparse
       batch2 current proposal; no source/default/guard changes,
       no new CI. Pause small host-only directions.
       Next measure current combined generator/ownership IR
       reduction and packed allocator on the actual giant, then
       sparse live planes/streamed lowering only if measured
       remaining owner warrants; native allocator qualification
       before pcc1 mode. No gate relax.
       (1) ARM typed
       prepare memory: quantify retained prior run history and
       private CFG owner retirement with public snapshot/callback
       contracts, then common indexed fact/emission
       owner; (2) J2 (e) one error
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

   Follow-up ordinary container allocation attribution, exact source
   `720111d2` (Linux, 2026-11-26): actual five 10/50 round differences
   (C20, 800 requests) in each of w1/w4. Total38.45 allocations/request:
   list7.10, tuple6.10 (arity0=0,1=1.05,2=5.05), iterator3.05 (all
   ordinary list). Dict items materializes 2 lists plus 4 two-item
   tuples/request, and plain sorted copies 2 lists. Direct primitive
   with same sorted two-key output and allocation counts 3 versus4
   measures whole extra sorted path plus necessary comparison
   495–501ns/occurrence (2/request, upper bound ~1us/request).
   Candidate generic direct `sorted(stable_parameter.items())` exact
   native dict tag branch marks an unexposed intermediate; ordinary
   user method/subclass and public sorted ABI unchanged. Runtime
   admits unique bounded exact list (<=16), nonmoving atomic mode;
   callback-free sort borrowed scratch before root transfer/permutation,
   fallback leaves original unmodified. Actual C baseline/candidate
   1M operations x5 alternating triples/mode (identical control):
   string-value CPU2146.388 ->1751.051ns, normalized -19.302% vs
   max control7.843%; counted-list-value CPU2160.350 ->1844.038ns,
   normalized -15.753% vs9.531%; 5/5 each, exact allocation4->3.
   Actual handler allocations/request 38.45->36.45, both carriers
   and known/provenance audits pass. 44 host tests, 65 scoped tests
   pass; 20 pre-existing exact baseline failures in old GC4
   retarget/register model reproduced with exact original runtime
   source, not caused by candidate; pcc1 native test unrun. 625-source
   contextual five changed compiler modules all zero fallback.
   Final candidate SHA9d759e33f38706783e117c71c284a0b5a1dbe2c73a6a41049dbd874a23b97e38,
   baseline SHA2b0255f7b524f4a595d5ae44c6c7cb7b35aa3ca0872eeb2eeda20fcf1f136412.
   RX +2512 bytes, same mapped RW start/size.
   Actual complete C100*2000 5 triples/worker: w1 QPS18168.25->18049.38
   (3/5), CPU55.1492->55.1187us (4/5), paired normalized+5.543%/
   -5.024% within max control14.611%/12.066%; RSS36,798,464->
   36,667,392B (norm -0.345% vs control0.045%). w4 QPS37635.02->
   38166.37 (4/5), CPU99.0337->97.6979 (4/5), normalized+2.467%/
   -2.129% within max control8.208%/11.682%; RSS41,517,056->
   41,553,920B within control0.867%. Max STW both within paired
   max control. Whole-handler result INCONCLUSIVE, no merge and no
   speed/regression claim despite exact primitive reduction. Preserve
   focused patch+receipts for a genuinely quieter host; do not weaken
   threshold or keep retrying the same micro change.
   Next: attribute all3.05 ordinary list iterator allocations to
   actual consumer, measure owner cost, consider generic nonescaping
   loop cursor preserving live lengths, mutation, dynamic dispatch,
   finalizer and suspension semantics. No benchmark-specific bypass.

   Ordinary private for-loop cursor, exact Linux source `3fb598ed`:
   actual 5 matched 10/50 round count pairs each worker account for all
   3.05 list iterators/request: ordinary JSON _encode_object2.0,
   _encode_array1.0, generator0.05, other runtime/compiled consumers0.
   Actual next attempts9.05, exhaustion3.05, successful items6.0.
   5 alternating 1M-iteration primitive triples (each len1/2/4)
   measure public iterator excess over direct owned list loop
   363.734/375.792/452.515ns, weighted ordinary ceiling ~1.12us/request.
   Production compiler-private nonsuspending loop: raw activation index
   with independent exact-list owning root; all ordinary other values
   retain actual public iterator; live len/get and exception order;
   if type/mode changes materialize original sequence iterator at SAME
   index and transfer registered owner, not a fresh __iter__ dispatch.
   Public iter/next, generator frame and dynamic override ABI unchanged.
   Exact native primitive candidate len1 CPU473.956->136.747ns,
   norm -69.773% (max control12.566%); len2 537.286->210.818ns,
   norm -61.031% (max control3.274%), all 10/10 wins, exact item
   checksum and one iterator allocation eliminated. Actual handler
   38.45->35.45 allocations/request w1/w4, five counts, known/provenance
   audits both pass. Native baseline/new actual program parity and 60
   focused tests incl shared four-carrier list/dict/append, park/resume,
   dynamic override, rebind, errors, finalizers; pcc1 native nodes
   unrun. 11 focused host/nullable tests incl rare type-change handoff,
   quiet exhaustion with zero exception alloc; other14 unchanged
   baseline stale root IR models independently reproduced. Contextual
   closed-world actual 625-source changed two modules fallback0.
   Actual candidate ELF SHA9ba3013c4efff15266977a7a9eaaa47dcf3de8965ec10a2a23925088fd2be128,
   base SHA9475a9c4af4d84abac9a2fcb46fe8099f80e5ae055c4b82abe3c4aea6c32d305,
   RX+1296 bytes, same mapped RW start/size/TLS.
   Main C100*2000 five alternating triples, identical controls:
   w1 QPS18334.40->19169.59 (4/5), CPU53.5471->52.4825us (4/5),
   paired normalized+5.147%/-4.000% exceed max control3.112%/2.609%;
   RSS36,794,368->36,728,832B, norm -0.189%. w4 QPS40675.83->
   40847.38 (4/5), CPU91.3861->90.7458us (4/5), normalized
   +3.334%/-3.347% vs max control4.504%/2.457%; QPS neutral,
   paired CPU improvement, RSS41,558,016->41,414,656B within
   control. Independent w4 C100*2000 five further triples QPS
   34580.83->36931.86 (4/5), CPU108.9752->100.8364 (4/5)
   normalized+4.836%/-5.338% within high max control17.179%/
   15.534% (inconclusive, not a second gain claim); RSS neutral.
   Separate C100*500 p99 receipts five triples w1 5.141->5.159ms,
   w4 1.443->1.466ms; paired normalized -1.677%/+11.568% within
   control32.652%/14.140%, no measured p99 regression. Max STW
   paired within controls, no reproducible RSS or pause regression.
   Admit actual w1 handler improvement and exact native owner
   reduction; w4 throughput remains neutral/inconclusive, do not
   claim whole Stage1/Stage2 gain. Next: actual provenance check
   and container lock count/cost by hot owned path before proposing
   proof of any lock/check removal, preserve shared-thread semantics.

   Actual managed provenance and container-lock count/cost (source
   62e17cf2, diagnostic only). All five C20 10/50 round pairs delta800
   each w1/w4 reconcile exact owner counters. w1 public managed checks
   124.75375/request: 117.70375 live slab and 7.05 managed-index positive;
   no null/tagged/singleton/locked fallback. Owners list54.5, class49.10125,
   dict11.0525, tuple10.1, residual0. w4 124.875 (117.825 slab/7.05 index).
   w1 container lock/unlock96.955 pairs, all uncontended first CAS;
   list62.3525, class field10.1, dict read13.0525/write2, obj field9.45,
   pair0.05, residual0. w4 97.0275 pairs, 96.13625 first CAS and
   .875 contended in perturbed diagnostic (not actual timing).
   Clean exact source rebuild190.386s peak831664128B, static native
   primitive all seven modes and both controls correct. Five quiet
   alternating triples each, pinned CPU, same indirect baseline and
   unchanged control: paired incremental ns per call slab4.6338,
   index36.7405, uncontended lock+unlock15.988, singleton35.4274,
   tagged0.8607, null0.6200, raw negative183.1735. Max control drift
   respectively .4055,2.588,1.7645,.5576,.1065,.1745,3.0785ns.
   Weighted observed w1 isolated removable ceiling 0.545us slab +
   .259us index +1.550us lock =2.355us/request (~4.4% earlier
   measured 53.547us CPU/request); this is NOT measured whole-handler
   gain or permission to remove safety checks/locks. Public raw input
   provenance and nonmoving thread registration/publication preserved.
   Diagnostic instrumentation restored; no production runtime change.
   Next quantify actual TLS access owners and same-source cost before
   any thread-state caching design. Preserve virtual thread exception,
   root, worker/carrier migration and external thread semantics.

   Actual runtime TLS source-position attribution (Codex/Linux,
   exact source decef72c, diagnostic only). All 27 actually linked runtime
   modules with TLS references, 289 literal global_addr/load/store sites,
   native per-site relaxed counters, disable after carrier shutdown before
   report. Every five alternating C20 10/50 round difference (800 requests)
   has exact payload and nonnegative raw counts. Full guarded native ELF
   3e7bc75b983fe9d7d1901735ac4dba697c6626c92a10510a044a0365c6cc93d0
   compile235.731s peak780730368B. w1 all five totals945.24375
   evaluated source positions/request; w4 median1004.41875 (990.90375-
   1072.9; instrumentation may perturb scheduling). w1 thread-state352.365,
   allocator magazine104.90125, recursion88.9, thread-id88.60125,
   tracked delta48.8, dealloc depth34.45, exception storage33.95,
   generator direct32.95, current vthread29.15, handled context28.35,
   young-log24.42375, trash head24.4. Thread kernel aggregate440.96625.
   Lock and unlock96.955 each independently match provenance diagnostic;
   no-park enter/exit39.80125 each, safepoint35.45, scheduler markers
   21.65125 each, paired lock .05 each. w4 thread-state384.435,
   magazine104.91375, thread-id101.9675, recursion90.65. These are
   evaluated source-level accesses, not emitted machine TLS resolver
   count, and Linux does not model Darwin _tlv_get_addr cost.
   Probe sources restored; no production change. Next quiet exact
   clean-source owner-level primitive cost (not TLS-only cost) and
   only an independently validated safe reuse design if worthwhile.

   Exact clean-source native TLS owner cost (decef72c): source runtime
   rebuilt112.792s peak395235328B, ELF2ef5126bc9fd7fffd2180d28b59af21bc9bc77d5939886e9c7cb92f8c04cfade;
   seven modes, five quiet alternating triples, 10M iterations/case,
   exact unchanged indirect controls. Paired extra CPU ns per operation:
   registered current ID5.5258 (control drift.2955), magazine1.2168
   (.8764), recursion enter+leave4.5801 (.3925), no-park enter+exit
   4.6208 (.2433), safepoint3.1283 (.6975), tracked delta +1/-1 pair
   6.3974 (.7743), empty exception get -.0214 (.2577; no measured cost).
   Weighted w1 measured owner ceilings .490+.128+.204+.184+.111+
   .156=1.272us/request (~2.4% earlier handler CPU); this includes
   full owner functions, not a TLV-only cost, and the lock cost above
   is separate. No broadly safe removal and no Linux case for a complex
   cross-thread cache; Darwin remains a separate measurement. Native
   x64 ID hot path has a176-byte frame, helper call and cold registration
   spills, but entire measured owner only .49us/request. No TLS source
   change accepted. Next attribute actual raw allocator requests/bytes,
   object-vs-raw free paths and owner functions; seek structural lifetime
   reduction independent of previously rejected sorted/factory patch.

   Actual raw/managed allocator request and free attribution (source
   23892fb0, diagnostic only): 72 linked source modules,288 literal
   malloc/calloc/realloc sites with count and size argument; allocator
   26 path-event classes, count/requested/usable bytes. Guarded native
   ELF5b41d0a6302997feef5d131dd17f3137c8f068642796f412314a8587c6ef2858
   compile234.807s peak772014080B. Five alternating C20 round10/50
   pairs delta800 each w1/w4 exact output, free count/byte sums and
   magazine route=hit+refill. w1 all five exact raw malloc17.57625
   (2306.72 requested/2664.32 usable B), managed35.45 (2658.4/3600 B),
   frees52.0025 (raw17.5525/2104.48 requested B; object34.45/
   2634.4). No large mapping, calloc or realloc. Raw magazine
  17.5 hits/0 refill, object35.44125 hits/.00875 refill. Source
   owner residual zero for every pair: list11.4525/1375.36B (initial
   7.1/227.2B, append growth4.3/1004.8B, extend.05/128B, other
   grow.0025/15.36B), dict4/384B (two48 and two144 allocations),
   continuation node2.1/352.8B (168 each), young log.02375/194.56B.
   w4 median raw17.57375, managed35.45, free52.0025, identical
   list/dict counts, raw refill.00125, object refill.02875. This
   indicates almost all allocator work is cache hit, not slow
   refill; prioritize reducing actual raw buffer creation/growth.
   Diagnostic instrumentation restored, no production change.
   Next exact quiet native raw pair and same-output list growth
   costs, then shape/ownership proof before choosing a layout or
   pre-sizing candidate; no unsafe blanket lock/provenance removal.

   Nonmoving list inline four-slot storage (Codex/Linux, exact source
   23892fb0). Actual list initial capacity4 owner7.1/request,227.2
   requested raw bytes; measured shape-driven nonmoving container
   can own zeroed initial four pointers in its object tail. Public
   header/length/capacity/items-pointer ABI unchanged, future moving
   mode retains separate storage. Growth/append/slice/sorted owner
   transitions retarget and clear retired inline tail under existing
   transaction; never free self-relative storage; ordinary external
   payload release unchanged, terminal dealloc retires owner once.
   Exact actual count diagnostic raw17.57625->10.47625/request,
   managed35.45 unchanged, total requested4965.12B unchanged,
   total usable +227.2B (object class tradeoff); no blanket allocator
   bypass. Diagnostic sources restored before native production build.
   Exact native C inline storage/retarget/C contract, 4-carrier
   concurrent list programs, 777 cycle collection, terminal weak
   callbacks both probe0/2, known/provenance audits exact, including
   w4 1000 requests metric28=0. Baseline C contract expected non-inline
   succeeds both probes and expected-inline fails layout rc4;
   all native cost sanity counts1/2/4/5/9 n100 exact. 87 focused
   tests pass (1 deselected); broad old allocation/source models same
   34 failures/17 passed/1 deselected on exact baseline and candidate,
   no false broad pass. Candidate/base ELF SHA256 respectively
   f9a0b4dae24226abe281f6c922e28a339c0c2503423c797225f822ce87a19451
   9eacb5967461e443714c7df1272a89cc9efeedbc1310f93246bd2b2016545d27.
   Five quiet native owner 300k-loop alternating triples per count,
   unchanged control, median base->candidate ns and paired
   normalized reduction/wins/max control: count1 533.95->459.50
   (12.393%,5/5,4.949%), count2 620.25->547.18
   (12.213%,5/5,4.825%), count4 809.19->762.30
   (5.979%,4/5,2.289%), count5 1049.93->1010.56
   (5.379%,4/5,4.169%), count9 1608.25->1551.91
   (1.229%,3/5,6.458%, neutral). All75 exact checksum.
   Actual handler C100*2000 five alternating triples per worker,
   same-base controls, exact requests. w1 QPS17621.07->17776.39
   (+0.88%,4/5), paired norm+0.951% vs max control1.357%, directional
   only; CPU56.8773->56.3993us normalized -1.639%4/5 beyond max
   control1.152%; allocations exact, RSS36,728,832->36,597,760B,
   norm -0.357%5/5 beyond control0.011%; STW norm+4.947%
   within control8.186% median/45.46% max. w4 QPS40054.27->
   40174.26 norm -1.428%2/5 vs max control6.79%; CPU92.226->
   91.925us norm+0.824%2/5 vs max8.93%, neutral/inconclusive;
   RSS41,431,040->41,517,056B norm+0.124% within control
   0.168% median/0.355% max; STW norm -3.818% within control.
   Separate exact C100*500 latency five triples: w1 p99
   6.016->5.377ms control4.989, norm -9.387%4/5 vs control
   median/max17.071/40.65%; RSS norm -0.338%5/5, STW norm
   -10.324% within max65.64%; w4 p99 1.262->1.359ms control
   1.376, norm+2.296% within control9.033/11.308%; RSS norm
   +0.118% within control0.456/0.648%, STW norm+1.544%
   within controls. No reproducible RSS/p99/STW regression beyond
   controls. Admit measured small-list native owner and modest w1
   CPU/RSS reduction, throughput directional only, w4 neutral.
   No whole Stage1/Stage2 or hosted CI pass claim.

   2026-11-26 exact dictionary raw storage attribution: Linux C20 five
   existing 10/50-round pairs (source 23892fb0, same actual handler) each
   attribute 4 _alloc_tables raw calls, 384 requested B per request.
   Capacities are 4 and 8 (presized dynamic and static-pair constructor);
   48/144 B are averages, not a six-slot table. A small-table coallocation
   would preserve requested bytes and remove 96 user-usable B plus two
   48 B raw allocation headers per request; the measured raw pair cost
   implies only about 0.14 us/request (`~0.25%` CPU ceiling). No
   throughput candidate or RSS prediction admitted. In Stage2 run
   45862787448 the coordinator at manifests-ready had 459,764,192 usable
   and 825,470,976 mapped B but no live dict ownership attribution.
   Add cold diagnostic only: `PCC_PROFILE_COORDINATOR_MEMORY=1` at
   manifests-ready prints a separate exact tracked live dictionary storage
   histogram, capacity:dicts:raw_blocks:requested:usable bytes. Same STW
   over disjoint allocator slab and outside-pointer-index walks, 2 KiB
   pointer batch and 32 B cursor, no object-list materialization, root
   retention or heap allocation during the walk. Exclude incomplete and
   terminal owners; reject inconsistent/interior raw base before header
   access. Reports unavailable rather than partial results, resumes only
   a stop it acquired. Flag-off native execution remains silent.
   Exact source baseline 5f49dbca: test-first absent ABI/caller each
   failed; 42 focused six-worker host tests pass (2.03s), isolated
   runtime IR guard 0.779s / 49,299,456 B; emitted snapshot has no
   GC-frame/root/heap calls. Fresh owned runtime archive
   fa4ef0581e8c77a9f7d29550f4484c0e07bc15b9f06e48c1c1ce10524413ed85,
   native build 159.857s / 404,561,920 B within 1 GiB tree cap and
   2 GiB reserve. Exact native C ELF 90d19acf2d10991c652decf8e94356e4a713d3c9af68b5429918a4dd89440c27
   default and provenance 2 pass capacity 4/8/1024, actual raw allocator
   byte deltas, terminal/cycle release and four concurrent native workers
   constructing/growing/clearing/retiring across 20 snapshots. Exact
   application caller ELF ae06b159d027c7b3809b9c5572f867e5f23ec2166c3a1d54593688670c13846a
   flag-off empty stderr, flag-on actual cap4 count 1->2 and cap8
   317->318 with exact raw blocks/requested/usable bytes. This is a
   diagnostic at the coordinator phase, not whole-stage peak or RSS and
   not performance evidence. No coallocation change. Next read the
   naturally cumulative hosted Stage2 histogram to quantify actual
   live small-table footprint before any candidate; continue analyzing
   independent lifetime owners.

   Actual allocator live-byte accounting owner screen (Linux, Codex):
   after inline-list (a5b2bb00), five actual C20 pairs yield 45.92625
   allocations and 44.9025 non-null frees/request. Magazine route covers
   90.70125 of 90.82875 accounting operations/request. Each existing
   allocation/free computes the stack-shard address and performs two
   atomic RMWs; structural 181.6575 RMWs/request is not a cycle estimate.
   Exact allocator source b3f78849107559fcc4fb94a61963ff51be2224804c82eb4787f1c63fbd4f30a8
   is byte-identical current b73eec84 and owned archive fa4ef058
   (built 5f49dbca plus exact dictionary diagnostic source, not current
   full compiler artifact). Native owned C ELF b4d020ebcfc88132990d20f85ca5ad10df82c098867414b9a536af557914fa28,
   10M pairs/arm, 5 alternating triples per 32/32,72/128,168/256 B
   size pair and control; all 90 runs exact checksum and quiescent
   allocator counter balances. Actual account allocate+free excess over
   same indirect noops 38.709/38.073/38.246 ns/pair (max unchanged
   drift .163/.527/.284 ns); a same-binary single-writer relaxed
   load/store reference removes 33.140/33.037/33.017 ns/pair, 5/5
   each (max control 1.256/1.639/4.804 ns). Entire owner upper envelope
   1.727–1.756 us/request (3.06–3.11% of measured 56.4 us/request);
   plausible local counter reference only 1.497–1.503 us (2.65–2.67%),
   before registering/retiring per-thread stats, foreign fallback and
   exact concurrent read costs. No memory reduction. Guard build
   2.315s / 76,566,528B; quiet measurement 21.771s / 17,391,616B,
   unchanged 1 GiB tree guard / 2 GiB reserve. REJECT this complex
   per-thread accounting proposal below the 3% ceiling; no production
   source change and no actual-handler speed claim.

   Actual terminal decref owner screen (Linux, Codex). Existing exact
   managed terminal free attribution after a5b2bb00 is 34.45/request,
   the full upper count for all direct and prepared finish paths. Owned
   C native ELF e5c80cacaaf6047b3282fbdc4ddf0b34899f17b62f380d7015139203aea6613a,
   archive fa4ef058 (py_obj/dealloc/GC backend/thread-kernel source
   byte-identical to current ae2be257, not current whole compiler).
   Three exact sole-owner float, empty list, two-tagged-item tuple
   shapes; 1M complete allocate+release operations per arm, five
   alternating blocks with same-binary unchanged public decref
   control. All 75 runs exact allocation telemetry, no double frees,
   identical requested/usable balance after subtracting actual 8 KiB
   young-log chunks. Existing proven release_known relative to ordinary
   py_decref saves 2.901/11.685/7.167 ns, manually identical prepared
   finish record 3.181/-9.587/8.492 ns. A controlled callback-free
   reference keeps count-zero mark, freeing note, untrack and actual
   trash/deallocation owner in order, but omits general finish
   dispatch and record plumbing ONLY on these three exact no-weakref
   fixture objects; its savings 15.251/8.433/17.248 ns each 5/5,
   max unchanged control drift 14.440/12.297/7.433 ns. It cannot
   substitute a general terminal ABI. Even generously weighting
   largest controlled difference by every terminal free gives
   0.594 us/request (1.05% of ~56.4 us), before mandatory callback,
   weakref, audit, resurrection paths and real direct-owner fraction.
   Guarded build2.845s / 76,115,968 B, measurement19.689s /
   20,430,848 B, unchanged 1 GiB tree cap / 2 GiB reserve.
   REJECT terminal refactor; no production code or handler speed claim.
   Next seek structural compiler ownership/allocation elimination on
   actual remaining high-count sites rather than repeat tiny terminal,
   accounting, TLS, empty-root or factory candidates.

   Actual native flat owning sorted dictionary pair snapshot screen
   (Linux, Codex). Exact handler attribution is 2 items-list, 4 pair
   tuple and 2 sorted-output-list managed allocations/request, every
   one- and four-carrier repeat. Actual pcc/stdlib/json.py assigns
   items = sorted(obj.items()) if sort_keys else list(obj.items()),
   then consumes its private local; native handler uses both n=2
   selections (one exact string-valued, one counted-container-valued).
   Controlled native C reference on archive fa4ef058 (not current
   entire compiler), private flat owning tuple versus ordinary
   materialize/sort/consume, 300k complete iterations, five alternating
   blocks including unchanged controls, 60 exact-identity/telemetry
   runs, ELF d2b1597ff56a887e892afafffa02c5e32cb1eecaf8d6f16cdc7c19df8ae8f996.
   n=2 strings baseline 2078 ns -> 923.883 ns, paired saving
   1164.123 ns (5/5, max control 140.390 ns); n=2 counted values
   2148.417 -> 927.580 ns, saving 1220.837 ns (5/5, control
   26.240 ns); n=1 and n=4 savings 876.923 and 1954.523 ns,
   both 5/5. Measured primitive 4 -> 1 managed allocations/operation,
   generous actual weighted reference envelope 2.385 us/request
   (4.23% of ~56.4 us), not handler performance. Guard build 2.892s /
   76.4MB, measure 34.763s / 18.8MB; same 1 GiB cap / 2 GiB reserve.
   REJECT: the original output list and counted pair tuples are
   tracked objects. A tracked flat tuple would become observable
   by gc.get_objects/get_referrers while JSON's arbitrary _encode
   calls run; another carrier can obtain and retain these owners.
   A deliberately untracked tuple or raw root array would remove
   the original observable graph and may change finalizer/callback
   lifetime; lexical single-consumer alone is insufficient proof.
   Do not hide GC metadata or introduce global virtualization for
   this small primitive envelope. Source-only draft not published or
   timed; no production or actual-handler speed claim. Continue
   representation-preserving structural lifecycle attribution.

   Actual class special-call temporary root-group owner ceiling (Linux,
   Codex). Existing actual raw allocator attribution 2.1 continuation
   registry nodes/request, 352.8 requested B (168 B average, consistent
   with 56 B header +14 roots; class _special_open uses14, dict groups7/16).
   Raw allocation alone measured only ~0.15us/request, not a structural
   candidate. Existing C ABI archive fa4ef058 py_class, root registry and
   root operations exact current blobs 5b49f0c2/e4e84fb1/b487c637.
   Native C screen calls the archive's actual private _special_open/close
   functions, 500k iterations, five alternating blocks including exact
   unchanged controls per shape; ELF
   4910b273de8fe4e2f4ce9ac55facf7b307115af662e693394cc4011264bdf294.
   50 formal runs +7 smoke all passed, exact root-head restoration,
   requested/usable accounting, retained outer-list count, no managed
   allocation/double frees or error. All-empty base367.642ns, same registry
   live-only cleanup reference295.368ns, paired saving60.148ns (4/5,
   max control drift36.342ns); whole no-callback all-empty reference
   without registration188.444ns, paired174.482ns (5/5). One tagged
   binding live-only saving51.874ns (5/5, control22.822ns), three-slot
   two-counted+tagged saving77.202ns (5/5, control105.992ns, inconclusive).
   The whole-empty reference is not valid for a general callback-bearing
   special call. Even weighting every actual registry node by this
   generous whole-empty saving gives 0.366us/request (~0.65% of56.4us);
   weighting by largest entire measured baseline incl. live publication
   and required releases (463.482ns) gives 0.973us (~1.73%), below3%.
   Complete guard11.774s/73,793,536B, unchanged1GiB cap/2GiB reserve.
   REJECT costly source occupancy probe and stack/cleanup specialization;
   no production source or whole-handler performance claim. Next use
   cold opt-in live object-family slab size histogram to identify actual
   Stage2 allocator internal padding before any size-class redesign.

   Cold LIVE managed object-family slab size attribution (Linux, Codex,
   exact production base cf7762a6 plus three diagnostic source files).
   Previous actual allocation churn requested2658.4/usable3600 B per
   request is not live memory; prior hosted Stage2 manifests-ready
   usable459,764,192/mapped825,470,976 B had no live size attribution.
   Extend existing PCC_PROFILE_COORDINATOR_MEMORY=1 only at manifests-ready:
   a separate independent stopped-world managed slab histogram (not
   atomically combined with the prior dictionary-table receipt).
   37 bins: rounded 16-byte request upper16..512, then current classes
   1024..16384; count:requested:user-usable:hypothetical min16-aligned
   payload bytes. Exact LIVE slab enumeration visits one span base once,
   validates object-family prefix, state, size class and signed aggregation,
   excludes RESERVED/free/raw/large/index-only/static owners. Includes
   still-physically-owned terminal LIVE cells. No layout/stat/root mutation,
   managed allocation, object-list materialization or hot-path work.
   Explicit aligned output address/exact1184B ABI; on malformed state,
   overflow or STW admission failure report unavailable, resume only a
   stop acquired. This is not total heap, mapped bytes, RSS or a proposed
   new class; adding classes could increase partially occupied slabs.
   Baseline test-first exact two reds (missing native helper and absent
   coordinator call). Final 30 six-worker host tests green including
   previous dict diagnostic, boundaries, multiple batches, empty, null,
   wrong ABI/prefix, counter overflow, failed and borrowed stop. Actual
   allocator/GC IR no managed/heap/root operations; add helper only
   calls exact size class. Fresh exact owned archive
   e129de088b2f60baa007b328deb9154d644a14d3e19a981c38c92c28b15624bc.
   Native C ELF cf785b84f119eb45e1a73ee04a5cd71b0a2fffeac5857abcc76a3a1466a56c05
   default/provenance2 both pass exact tuple class-boundary deltas,
   canaries, raw/RESERVED/large exclusion, no allocation/stat changes
   during isolated snapshot, borrowed stop, release and20 snapshots
   while four native carriers churn. Prior dict C ELF9a3b6046c97b3afd5925f00c8d9dac25e9d0462740fa7f5d31ad0b6a2b50907c both modes pass.
   Actual native app ELFf2589912d980ee370ada6cb617c1c05fc8136f5c3870ef6b63e3d0653087f4a9
   flag OFF exact stdout15/stderr empty; ON same stdout, two old dict
   and two exact managed histogram receipts. Complete guard155.512s
   peak413,868,032B with unchanged1GiB cap/2GiB reserve.
   Admit diagnostic only; no speed/RSS gain, no allocator redesign,
   no hosted Stage2 result yet. Existing workflow already profiles
   coordinator memory; use natural cumulative CI receipts, no new
   independent dispatch. Only genuine hosted live histogram and actual
   same-input RSS can drive a size-class candidate.

   Exact NULL-to-NULL root-store owner ceiling (Linux, Codex).
   Existing actual native GC0 handler prior attribution ~108.30 empty
   clears/request; two previous compiler-local candidates removed eight
   but no reproducible handler speed. Clean owned archive e129de08
   (current root implementation py_obj blob d10b88ae, not entire current
   compiler), exact registered empty root, ten million calls per arm,
   five alternating actual/noop/unchanged-actual control triples. ELF
   203382291bc9566571eff424a64f3adead2fb6d473d1298f49cf2d4f168807e3.
   All15 runs exact NULL/allocator/telemetry balance, no errors or
   double frees. CPU median full root9.1007ns, indirect no-op2.5077ns,
   unchanged actual9.2811ns; paired incremental6.6565ns 5/5,
   max control drift2.664ns. Weighting every prior observed empty
   clear by incremental cost gives ~0.721us/request (~1.28% of56.4us);
   even the entire root call plus loop9.1007ns gives ~0.986us (~1.75%),
   below3%. Complete guard3.610s/77,828,096B, unchanged1GiB/2GiB.
   Diagnostic preparation (not production): common L1 IR direct-call
   tagging with stable module/function/block/callsite labels, exact
   authoritative fast/direct old-root load classification, bounded raw
   atomic hash recorder in freestanding allocator (no safepoint,
   managed/heap/root calls), original ABI residual, opt-in begin and
   stop before report. Four rewrite tests pass; actual py_obj and
   stdlib JSON IR 1083 static sites, fully tagged direct JSON calls
   and original ABI wrapper. These are static sites, NOT current
   per-request frequencies or executed source attribution. Final IR
   guard5.390s/186,818,560B after fixing diagnostic-only imports.
   REJECT standalone empty-clear optimization and defer expensive full
   diagnostic handler build: even all previous empty clears below
   threshold on Linux. No production source or actual-handler speed
   claim. Keep prepared site/counter harness for a later combined
   whole-function structural ownership proof only if evidence warrants;
   do not repeat tiny local clears or weaken audit/root semantics.

   Ordinary-object atomic RC single-op cost and nonmoving-root audit
   (Linux x86_64, Codex, 2026-11-27). User redirected from isolated
   protocol patches to measured atomic count tax. Exact relevant current
   source c9120f18: py_obj d10b88ae and native kernel74ef7882 match the
   verified owned e129de08 archive (not entire current compiler).
   Exclusive ordinary nonimmortal float, one real outer counted owner;
   5M increments or nonterminal decrements separately timed with Linux
   process CPU clock (owned syscall228 clock-id2), reciprocal synthetic
   owners balanced outside timer. Source-copied atomic reference uses
   exact ordinary admitted checks/atomic RMW or release CAS, identical
   plain reference replaces only the count operation for this unshared
   fixture; unsupported checked/terminal paths are not represented.
   Self-owned reference objects20362a1a/4f3f35b2, native ELF
   49bcc0158da72e0ab94f763ebf724b4d4276df0de8a5eaaa90bb51515396ad4d.
   Machine audit: one lock instruction per atomic reference, zero per
   plain reference; native timer syscall228/id2. Five alternating
   actual/atomic-reference/plain/unchanged-actual blocks per mode,
   80 formal +12 smoke all exact counts, allocator/telemetry balance,
   no errors/double frees.
   CPU ns/op median actual, matched atomic reference, plain, unchanged
   control; paired reference atomic tax, wins and max control drift:
   public incref13.2408/13.3011/5.9692/13.4792;7.3921ns,5/5,0.620ns.
   public nonterminal decref CAS13.7164/13.7436/6.0115/13.9194;
   7.6897ns,5/5,1.670ns. Kernel incref RMW11.3086/11.3250/3.6687/
   11.3495;7.8349ns,5/5,1.242ns. Kernel decref RMW11.8022/11.1102/
   3.5039/11.3350;7.6078ns,5/5,4.824ns. Final guard13.045s/
   99,942,400B, unchanged1GiB/2GiB. Earlier diagnostic-only standalone
   reference compile failures were typed literal/import boundary and
   clock intrinsic only admits realtime/monotonic; corrected without
   production changes. Prior actual handler public entries118.2525
   inc/230.75 dec include NULL/tagged/immortal and checked tails.
   Confirmed nonoverlapping ordinary fast atomic inc46.75 + CAS dec38.6,
   known direct kernel inc5.15 + nonterminal dec29.2 (default known
   checks off, no public fallback). Weighted matched tax0.905us/req
   (~1.60% of56.4us). Deliberately generous all checked-tail entries
   37.1/71.55 and known terminal1 as one extra RMW each gives
   1.748us (~3.10%), NOT an eligible biased/local RC proof: includes
   terminal, nonordinary, uncertain/no-op counts and no exclusive
   ownership proof. Inline root/field atomic updates not attributed by
   these prior API counters. No whole-handler speed or total atomic-tax
   claim; no biased/non-atomic production RC.
   Exact root-slot audit on same current source (11 relevant runtime
   blobs matched): single collector config hardcodes backend0 and
   frame-registry-inactive=1 after initialization. pcc_gc_load_ptr,
   load_borrowed_ptr and resolve_owned_ptr are identity reads;
   oldify_copy returns NULL. Active GC0 root mark calls
   pcc_gc_visit_native_root_frames(6,0), mode6 and registered/global/
   continuation/scheduler visitors only load root then mark reachable.
   No active relocation or forwarding producer; stale checked resolver
   mode3 could write only if forwarding existed, mode2 oldify stub,
   forwarding_index_insert has no definition/caller (stale extern).
   Safepoint parks/updates thread metadata, not live stack root values.
   Cycle clear walks heap referents, not root slots; queued weakrefs and
   __del__ run with world resumed/table unlocked, then re-stop and
   recompute reachability; explicit start/stop callbacks also mutator
   code. Thus GC itself does not rewrite live stack-root pointers, but
   ordinary explicit root move/store/take, escaped address, C callback,
   global rebinding/reentrant mutation CAN change program slots. A
   compiler reload-elision proof must preserve actual owning lifetime,
   alias/non-rebinding and callback/audit/error semantics; never infer
   all roots immutable or all references safe merely from nonmoving GC.
   No source/runtime changes; support backend/Stage1 alias proof only.

   2026-11-27 managed-only 48-byte object-slab class (Linux, Codex;
   memory unit approved, not handler throughput or full Stage2 acceptance).
   Actual hosted #646 source 500e1ab6, artifact15167443571, x64 Stage2
   manifests-ready log SHA256 9b999dd38fd3c5426aa150022bdd3aac1feb024564fa9ecd708ed035ed3220e0:
   4,346,829 LIVE object slab cells, requested250,295,285B,
   usable362,013,376B, hypothetical16-aligned282,694,880B. The largest
   waste bin B48 was 2,228,549 cells (requested91,680,172B,
   usable142,627,136B). B64 733,115 cells (requested40,939,941B).
   48-byte managed class only, old raw malloc selector/ABI unchanged;
   existing object indexes 0..10 stable, appended index11, stride96
   and 682 cells per 64KiB slab; separate raw/object magazine views,
   explicit reverse mapping for trim/thread exit, exact registered
   provenance/recarve. On failed pre-publication granule registration
   re-carve the same private mapping into existing raw64 geometry and
   account actual usable64; no duplicate mapping or new raw class.
   Baseline 014a6b37 exact owned archive 8b0dd1b51c9f77f0dbfc322f67406b74db65967c9203c6e6adeb8eb698fafb2a;
   rebuilt allocator original byte-identical. Candidate archive
   7be0997503b449afdfe31050bcf71198513c378923a1f2c880ad503737ca6ef7,
   only allocator member changed (f38a031b), CAPI inventory unchanged,
   actual optimized allocation IR selector fully inlined. Host 28 PASS,
   two integration tests deselected; separate exact native boundary/raw ABI,
   1400-cell exhaustion, actual discard/recarve, 12 class cross-thread
   free, drain and duplicate audit red baseline rc31 twice, candidate
   exact OBJECT_MAGAZINES_OK twice; both arms exact raw magazines,
   four-carrier managed histogram and production cycle weakref/finalizer
   sync under provenance2 passed. No external LLVM/host-cc or runtime
   provisioning fallback.
   Exact synthetic LIVE replay matches #646 B48/B64 counts/requested sums
   (2,961,664 cells, requested132,620,113B every arm), not actual Stage2
   arrival order or object graph. Five alternating triples plus2 smoke,
   guarded16.583s/415.7MB; base/candidate/unchanged usable
   189,546,496/153,889,712/189,546,496B (-18.81%,5/5), mapped
   341,966,848/306,413,568/341,966,848B (-10.40%,5/5), peakRSS
   372,477,952/336,801,792/372,477,952B (-9.58%,34.02MiB,5/5,
   unchanged 0). Paired whole process CPU -8.25%,4/5, unchanged median
   abs3.57%; free phase noisy; no production CPU gain claim.
   Actual gateway handler once-compiled same25 digest-receipted app IR
   modules linked twice; base ELF c7f14595, candidate6466fc13, exact
   response bytes checked for every 100k-request arm. w1 five triples:
   QPS17789.7->17936.1, paired -0.203%,2/5, max unchanged2.808%;
   CPU71.607->70.968us, paired -0.281%,4/5, max unchanged2.642%;
   RSS61,923,328->61,927,424B (+1 page within max unchanged0.013%),
   maxSTW2049->1848us, p99 5.014->5.094ms within controls. w4 five
   triples QPS39473.8->39162.6, paired+1.179%,3/5; CPU105.308->105.054us,
   paired-0.242%,3/5; RSS66,818,048->66,813,952B; STW2172->2177us,
   p99 1.483->1.463ms. w4 unchanged max QPS21.499%/CPU13.29%,
   INCONCLUSIVE (no reproducible regression; no throughput claim).
   All30 native handler arms complete, no exception/body mismatch.
   This is a scoped live-density memory improvement, not full Stage2
   RSS or Stage2<=Stage1 qualification. Next retain actual hosted
   per-phase histogram/peak evidence; do not add further size classes
   or repeat noisy throughput-only tests without a new owner rationale.
   No dedicated CI dispatch; all intervening claims preserved.

   2026-11-27 managed-only 80-byte object-slab class (Linux, Codex;
   independent scoped memory unit after 48-byte production73ae31f4).
   Actual #646 manifests-ready source500e1ab6 x64 B80=484,225 LIVE
   cells, requested33,487,978B, usable61,980,800B, aligned38,738,000B;
   B96/112/128 respectively266,374/254,368/152,377 cells. ARM B80
   476,208 and Windows444,429 (observed diagnostic counts, not platform
   qualification). New managed80 only, index12 preserving 0..11,
   stride128 and512 cells per64KiB slab. Old raw malloc classes and
   ABI stay unchanged; separate object/raw magazine views, 13-class
   reverse drain and exact span geometry. On failed private granule
   registration re-carve the same mapping to existing raw128 and account
   actual usable; 48/raw64 fallback unchanged. No 96B class in this unit.
   Test-first intended host red (65->128), then34 PASS/2 integration
   deselected; separate native baseline intended rc41 twice, candidate
   OBJECT_MAGAZINES_OK twice, 64..81 raw/managed boundaries, exact LIVE
   provenance, >2 slabs1100-cell exhaustion/discard/recarve, cross-thread
   13-class drain and duplicate audit. Both arms raw magazines,
   four-carrier managed histogram and cycle weakref/finalizer sync under
   provenance2 green. Exact compiler closure014a6b37 cd5ca8ae; base GC48
   archive7be09975 allocator recompiled byte-identical f38a031b, candidate
   archive cc685464115b0264b461345cfb62176796825f738a67e9990acb16afc66731e2
   only allocator member changed (59b91dd0), CAPI unchanged; optimized
   alloc_object hot class selector fully inlined. Initial changed peer
   compiler checksum correctly rejected; exact closure restored.
   Actual #646 B80/96/112/128 synthetic LIVE replay preserves all four
   bins, total1,157,344 cells and requested102,389,039B, not actual full
   Stage2 graph or arrival order. Five alternating triples+2 smoke:
   usable148,140,032->124,897,232B (-15.69%,5/5), mapped209,354,752->
   185,958,400B (-11.18%,5/5, control0.016%), peakRSS225,349,632->
   202,018,816B (-10.35%,22.25MiB,5/5, unchanged0). Process CPU paired
   -2.714%,4/5, control median abs13.02%, inconclusive. No CPU gain claim.
   Actual gateway exact25 previously hashed IR modules re-linked;
   baseline ELF byte-identical to accepted48 6466fc13, candidate8ff6a1e4.
   Every 100k-request arm asserts expected JSON; w1 five triples
   QPS16711.1->16581.3 paired+0.041%,3/5 (max control2.153%), CPU
   76.007->76.593us paired+0.275%,2/5 (max control2.167%), RSS
   61,923,328->61,800,448B (-120KiB,5/5; max control0.013%), STW
   2292->2129us, p99 6.191->6.517ms within noisy controls.
   w4 first five triples inconclusive: paired QPS+0.375%, CPU+0.316%,
   RSS+0.037%, STW+9.928%, p99+0.768%, max controls QPS11.212%/CPU14.914%.
   One adverse correlated pair QPS-28.99%, CPU+21.85%, STW+91.38%,
   p99+94.21% while unchanged control mild; exact allocations and zero
   graph lock waits. STW difference1.739ms alone cannot explain the
   wall loss; no proven scheduling cause. Held and ran ONE independent
   repeat at load0.46->1.77: no correlated extreme reproduced, paired
   QPS-0.503%, CPU-0.815%4/5, RSS-0.363%, STW+6.021%1/5 (one +57.4%
   without correlated CPU/QPS/p99 loss), p99-3.854%. Max unchanged QPS
   9.618%/CPU6.096%, strictly inconclusive AGAIN. Preserve both rounds,
   all10 combined paired medians QPS-0.064%5/10, CPU-0.736%6/10, RSS
   +0.031%4/10, STW+7.027%2/10, p99-1.810%6/10; unchanged median abs
   2.565/1.994/0.156/15.704/4.547% respectively. STW direction/noise
   remains disclosed; no reproducible correlated collapse, no w4
   performance qualification or gain. Maintainer approved scoped memory
   based on exact live replay and real w1 RSS, no further pure-noise retry.
   No claim of whole Stage2 RSS or Stage2<=Stage1; #647 source snapshot
   26d6fb08 is already running and excludes this change. All pushes
   [skip ci], no dedicated CI dispatch; all peer claims preserved.

   - 2026-11-27 shared generator save-suffix prefix spine: exact source slot/flag object identity and ordered contiguous all-owned plans; mismatches/nonowned/skipped/empty retain original path, per-state cutoff keeps park cleanup. Actual corrected 30-file handler/39-module closure source74cdff68, giant post IR91,650→82,879 lines, calls22,387→18,895, moves4,706→1,214, PHIs18,055 and root stores3525/leaves3299 unchanged. Fresh strict626-module compiler context fallback0; original/revised direct/text native OFF/ON 1/4 carriers16 exact outputs4218 with GC/finalizer/exception. Prior wrapper explicit-site failure independently proved provider shadowing; ordinary documented site-free native passes. Production sharder owned one giant ELF2,866,568→2,589,568B (−9.66%), same export, runtime relocation delta exclusively3492 removed frame moves. Exclusive 5 alternating A/B/control triples all15 exact per-arm ELF: CPU16.9837→15.3325s−9.72%4/5, control+0.48%, normalized−10.73%; last adverse pair19.279vs16.984/control17.065 disclosed. Wall−8.61%4/5 ctrl+0.535%; RSS300908→290932KiB−3.315%5/5, unchanged control+4KiB,max drift3392, normalized−3.402%. Formal stdout SHA d8b2cec38d3f239187bb2b525f966241cd3ab90f57fea6db6314d8a585c62af9, guard550375df32651ff7281548b724d8d441e8240b88faa691ed21e61fc91a78a94d. Scoped giant backend only; single frontend capture CPU neutral/mixed sampled RSS, no full handler/Stage2 claim. Next target actual huge ownership-flag PHI expansion, independently attributed before edits. No CI dispatch.

   - 2026-11-27 rejected cost-aware exact nonescaping i1 mem2reg cutoff (floor 256, ratio >8; claim 0d1d4b55). 19 focused passes, strict 629 closure fallback 0; actual giant PHIs 18,055→281, body 14.39→5.04 MB, single pass/backend CPU −26.56/−50.07%, full same-source handler compile CPU −12.98%, peak RSS −10.82%. Independent w4 fixed-CPU 5 triples: candidate 51,708–52,166 QPS vs unchanged control 53,004–53,490; normalized QPS −1.60%, CPU +1.45%. Latency repeat normalized RSS +0.336% > max control 0.275%; smaps file code RSS +72 KiB (w4), +8 KiB (w1), anonymous/dirty unchanged. Not a GC heap regression, but default runtime gate not met. Candidate source/tests not published; original and repeat raw receipts retained. Hosted direct passes-off bootstrap unaffected; no whole-stage improvement claim. Next: actual native indexed large-module codegen attribution.

   - 2026-11-27 rejected native default verifier cold-path allocation unit (747c1dd1/63a53bc0). Actual class_gen OFF PIDX cf4113cc: 86,267 phi-free, 49,055 unconditional, no switch/inline. Candidate only avoids predecessor allocation on phi_count==0 and delays diagnostic repr after mandatory same header/span reads. Source ea575668/test d445451a, intended 2 red then 31 green. Exact native production-body projection with real arena c0bb2701 and 135,322 ordered eligible rows, checksum188041627; ordinary site missing arena failure retained, corrected explicit source and same strict runtime d4520c69. CPU0 20-repeat A/B/control 10.754→4.053→10.467s (normalized −61.80%), peak57728→55072→56072KiB, exact output. Scoped saving ~0.328s/class replay, entire projected baseline ~0.531s/repeat including load, not full native verifier bound. Stage-level payoff too low; no formal native full owner/ELF qualification; no source/test merged. Release claim. No CI.

   - 2026-11-27 rejected direct indexed packed operand projection unit (claim443c1fad). Actual class_gen default OFF 727 functions/335,663 records, 2,137,826 fields, 591,182 strings, 277,763 identity replacements, 148,125 new intern keys, only 75,498 tuples unchanged (22.49%); copy-on-write not pursued. Actual 199,033 call records/323,694 argument rows duplicate record reads, 110,401 fixed packed nested helpers unqualified. Same-ELF scoped native replay exact methods/arena and actual 9,420,426B data SHAa07a3d16 checksum361334046, strict runtime d4520c69. One CPU0 20-repeat A/B/control 14.578→9.508→14.066s CPU (norm −33.615%), RSS54972→54976→54976KiB, exact output. Scoped saving ~0.241s/class corpus, too small vs actual #647 132.701s emit; no full native/5-pair qualification. No source/test merged, release claim. No CI.

   - 2026-11-27 native compiler self-PC profile feasibility blocked (claim ceae7394). Existing Linux profile-preflight passed: 80ms requested, counted79,998,206ns/running79,990,193ns, 86 samples/52 distinct IPs/kernel_lost0, stdout60d44a26. Retained authentic source6cb pcc1 SHA c88e3ba is 272,120,580B static ELF, above existing sealed 128MiB limit, and old worker lacks READY/G/DONE markers. Existing supported capture cannot sample it. Do not change cap or add alternate capture; no actual native compiler samples, hardware instructions/IPC, production or CI. Release claim; use actual phase receipts and same-IR static evidence without representing host samples as native.

   - 2026-11-27 actual compiler-function same-PIDX MIR/legacy evidence (a143947b). Clean d1df09ea both arms; frame/copies sources SHA523f6764/857dc94a exact #647 source26d6fb08; historical strict contextual closure626 PIDX d818fdb2/1b2e1403 fallback0. Eight functions, seven admitted compact MIR: static instructions58,388→53,431 (−8.49%), total104,829→99,872 (−4.73%), bytes548144→523432, rbp-local loads5688→4798/stores10932→9386, calls17048 exact. Largest keep_frame_slots_in_registers 46441 static instructions exact fallback; admission width−1 non-CAS aggregate value4724, not resource cap. Exact inventory only two aggregate values, direct llvm.smul.with.overflow.i64 {i64,i1} immediate extracts0/1/no other uses. Historical selected host sample weight1406/1920 admitted, not native PC/time share. No native execution or production/default change. Close attribution claim; next distinct exact arithmetic pair unit. No CI.

   - 2026-11-27 rejected opt-in exact ephemeral signed i64 overflow MIR pair expansion (715ab1ae), no source/test merge. Actual largest #647-source keep_frame_slots had exactly 2 nonescaping llvm.smul.with.overflow.i64 {i64,i1}, adjacent extracts; text flags16/fixed0 vs contextual16/2. Test-first 2 red, initial incorrect guard 2 red/23 pass preserved, corrected typed MIR25/25 incl 8 native signed cases and negative controls. Static largest instructions46441→43437 (−6.47%), frame15936→11296, bytes246743→235462, other15/16 bodies exact; host emit2.035→3.355s adverse. Actual89-module strict context fallback0/no-libpython with target PIDX dcd0e8c8, 3 actual root/reload-bearing c_codegen frame rows, exact reconstructed and native output. Fresh strict runtime3bb8f1cd; full89 baseline deferred329.878s/704335872B, actual target PCO864b0828 exact independent legacy. Official stripped baseline/candidate ELF b5dd9925/516437ee, same89 ordered inputs only target PCO6442e0e9 replaced, exact unique image target nonreloc proof205711/194430 bytes. Initial nm red (strip) and raw non-target parity red retained; all42 other funcs209082B, 208874 bytes equal, 208 diff bytes exactly92 direct E8 calls decode to same LOCAL FUNC due layout, all other bytes/relocations exact. Native3 outputs/roots/reloads identical. Formal CPU0 rounds100×5 triples all15 exact CPU4.589746→4.467294 ctrl4.586142 norm−2.582%5/5 maxctrl1.775%, wall−2.560%5/5; RSS43824→54784KiB ctrl43828 norm+24.992% adverse 5/5 maxctrl16KiB(.0365%), initial screen+25.30%. Sampled smaps program file +7040KiB, program Anonymous868 exact, anon/heap+4KiB control-scale, not evidenced GC heap growth; does not override actual RSS gate. Formal stdout5bd0d0ab guard3e14a25e smapsf3363379. Reject and release; no Stage2 whole speed or CI claim.

   - 2026-11-27 actual #648 x64 export677.783s nonoverlap: initial249-worker pool358.614s, summary206.210, preload83.355, other29.604. Initial pool aggregate worker elapsed1339.569s, context/post-context909.395s outside traced context end; current batch metadata still repeats structural AST walk for498 modules. Exact historical6cb native batch replay tasks127/78/213 all21 wire/AST outputs exact, worker11.839s/context3.076s/outside8.763s (old V1 codec, not pure metadata or current source speed). Add seven disjoint completed post-context phase timers to existing worker timing flag (AST write, reexport scan, owned metadata, dependencies, function objects, native wire, reexport wire), disabled path no extra clock/helper/IO, original errors/order/wires preserved. Test-first 5 intended red/2 old controls pass, 51 focused pass, actual ordinary two-module V2 worker outputs AST/export/reexport/dependency byte-exact on vs off, row protocol unchanged. Strict real629 native context changed module index48,61funcs/13332blocks/53610inst, fallback0/no-libpython, PIDX a17558de, guard47.175s/296148992B. Source6da8598c/tests a58ad22b. Diagnostic only; actual hosted native timings await next single normal CI, no speed claim or extra run.

   - #648 Stage1 regression attribution: all four629/629 pass, compiler x64 1925.472s/3.043GB, ARM1640.311s/5.100GB, Win1760.030s/3.751GB, Mac3524.862s/1.917GB. On x64 all629 slower, including all626 same CFG shape +49.8% cumulative worker time; IR count +0.060%, frontend/backend/verify/layout/stackmap/function emit all +48–58%. #648 long-task sampled own CPU alone exceeds old #647 wall for all178, broad per-CPU processing difference not only queue. Same x64 runner image but region changed, no causal hardware proof. ARM equal-shape −2.5%, Win +11.8%. Default backend source unchanged; no rollback or gate change. Four Stage2 owners still deadline, x64/ARM/Win peak2.748/4.263/1.575GB, Mac inner600.166s/1.643GB. x64 initial closure197.601/export677.783 and same-shape cli399.825/c_parsetab35.304, broad increase; ARM slightly progressed, Windows broad increase. Compare cross-run, not controlled A/B.

   - 2026-11-27 Stage1 direct capture attribution paused by maintainer's urgent pcc1 summary root-cause investigation. Exact #648 clean e8 source7b1601d8 selected c_codegen guard76.441s/732508160B, captureCPU4.814953/wall4.853825, fallback0, local PIDX b4d5e097 97,579,479B, 1039 funcs/95041 blocks/358843 instructions vs hosted358849 (six-instruction difference). No internal phase attribution arm, source or performance claim; release, reopen after root-cause priority and exact shape.

   - 2026-11-27 urgent native pcc1 summary root-cause diagnostic. Same-source current #648 e8 and identical historical626 V2 input output exact: actual summary task57 host2.075989→native18.364708 CPU (8.846x), task53 .494372→3.885961 (7.86x), no-callable c_parsetab1.19213→12.213146 (10.24x), empty no slowdown. Actual current pcc1 stripped machine image d6a39f41 proves `_decode_py_ast_wire` actual E8 call target user_json_loads, caller/callee unique exact nonreloc bytes8767/3452 and exact target VA260292624, selected contextual source d00df871/PIDX90f5f2b9 exact #648 74func/7071blocks/27000. Same valid4.359MB AST JSON-only canonical SHA exact: CPython parse wall.271801, compiled Python provider4.277266, raw ABI .874358 (raw4.892x faster provider, still3.217x CPython). Raw actual entry relocation py_json_loads, runtime exact eace79a7, formal own ELF5971830f, raw process RSS191540→266972KiB +39.38%, full CPU includes validation. Single screen, no production replacement or compatibility claim (raw trailing/exception/Unicode differences). Provider misroute root confirmed, further AST reconstruction/semantic-preserving provider optimization needed. No new CI; release diagnostic claim.

   - 2026-11-27 native summary AST reconstruction diagnostic completed (ad9347fd). Exact current #648 e8 runtime eace79a7, 26 official modules fallback0/no py_cpy, actual entry provider+V2 decode/encode PLT32; same raw c_parsetab4,359,078B canonical SHA f777240c both. CPython→native phase wall read .042896→.299731s, parse .193574→4.209238, rebuild .395045→2.648466, raw release .008380→.155305, AST release .007853→.059678. Native read+parse+rebuild+raw release7.313 vs host.640, excludes validation. Native total CPU10.223179 vs host1.075928 includes validation; process RSS101412→212456KiB. Single scoped screen not additive whole summary attribution, no production/CI. Actual V2 top5 canonical typed n kinds IntLit/IntType/StrLit/StrType/DynType comprise87648/99962 nodes (87.68%),201284/248790 field visits; possible direct constructor path after full validation, no performance claim yet. Receipts stage2-summary-ast-probe; release diagnostic claim.

   - 2026-11-27 rejected no-hook direct dict JSON provider (8934dfca). Exact provider preimage26060536 candidate65012221/test45ff65f0, intended red→33 host pass. Exact e8 current runtimeeace79a7 strict, 19 equal closure/other sources, all direct PCO fallback0, entry user_json_loads direct unchanged relocation7076, actual native hook-none and type tag4 gate/direct dict method. Baseline ELF374b1a04 candidatee1b1b6df, full actual c_parsetab JSON canonical SHA f777240c. Screen control drift retained. Formal all15 exact, guard97.672s/216.23MB: parse4.611→3.995s paired−14.798%4/5 maxctrl6.874%, whole CPU6.838→6.032 paired−11.063%4/5 maxctrl6.065%, wall−11.125%; RSS196182016→209326080B +13,144,064/+6.700%, adverse5/5, maxctrl.048%. Stable RSS adverse, no production source/tests merged. No smaps or causal memory attribution; raw receipts retained, guard36c06304/stdoute0f67226/samples80e3a0c3. No CI; release claim.

   - 2026-11-27 raw/provider post-parse live-memory diagnostic (34e7af5b) blocked before compilation by strict exact-source admission: e8 HEAD but tracked AST candidate source/test in Stage2 checkout, guard COMPLETE rc1 .788s/29,601,792B. No compile/parse/snapshot/result. Independent existing GC worktrees containing e8 have tracked modifications, none eligible. Preserve probe and refusal; no retry in dirty source, no production/CI, release. Future only by actual Stage2 owner after candidate preserved and exact clean e8 restored.

   - 2026-11-27 AST V2 top5 direct constructors candidate paused for urgent raw/provider live memory attribution. Source5dc5e470/testb50f509b, intended baseline1 red/6 pass, candidate43 host tests pass, 4 native/parallel not run. Exact patch9e7fdd75 preserved, owner restored ONLY two tracked files original blobs2ece30c1/7213e9b4, clean e8 HEAD. Needs fresh strict source-matched runtime and native A/B/RSS, no production or speed claim. Release temporary claim; reopen after diagnostic.

   - 2026-11-27 actual raw/provider post-parse memory diagnostic completed in clean exact e8 owner checkout (8a0ae4e7). Strict20 real modules/fallback0, exact runtimeeace79a7, official ELFf5f08536; canonical actual c_parsetab f777240c both. Build36.466s/231903232B, whole diag47.14s under60; provider/RAW tree peaks199831552/267534336B, not formal timings. Live dict storage provider149,944 capacity4 (requested19,192,832/usable23,991,040), raw149,977 capacity8 (baseline32, new149945; raw requested38,394,112/usable47,992,640), +19.201MB req/+24.002MB usable major actual storage. Whole allocator postparse raw requested83,504,488 vs provider62,518,568, usable97,135,904 vs70,355,920, mapped190,693,376 vs118,558,720. Raw class2 count318407 vs157921 (+160486), payload14,060,549 vs7,320,154; after release mapped219,631,616 vs155,283,456/reclaimable104,595,456 vs44,892,160. Snapshot counters unchanged during each. No causal full RSS equivalence or timing/production claim; receipts provider08d2fabc/raw7c148b17, no logging/source/CI. Release diagnostic.

   - 2026-11-27 rejected Stage1 common x64 default stackmap multi-scan sparse event diagnostic (1a80d826). Exact frozen real class_gen PIDX cf4113cc (727 functions/87,118 blocks/335,663 instructions), same planner source SHA0ab61bfd default MIR OFF. Four independent metadata scans each335,663, only5,469 alias events; 199,033 call records/58,516 frame protocol/15,269 enter. In-memory diagnostic sparse event pass and three sparse consumers include construction and discard; no production edit. Same 184,919 complete plan records/locations/reloads digest522a76008e3e8a292aae649c7c8a4721973d65670f98c2c753c60f945a06c02b both arms, owner CPU9.699179→7.705296s (-20.56%, below 24% predeclared planner threshold), event build .223935s included, process RSS293092→294240KiB (+.392%). Unchanged decode4.733→3.808s (-19.55%) and prepare7.943→6.590s (-17.03%) show large run drift. This one direction screen is not a speed result, no c_codegen second arm/formal, no native bypass or production/CI. Preserve exact raw receipts build/stage1-stackscan/class-{base,sparse}; release claim.

   - 2026-11-27 maintainer explicitly selected DEFAULT native private strict owned AST JSON parser despite disclosed scoped RSS tradeoff (user: '默认用新的。' / '先提交。' / '提交之后继续解决为什么内存有问题。'). Strict canonical subset only, original provider fallback and CPython/public JSON unchanged; compact dict cap4, ASCII cached keys, checked registered root group, fatal/OOM exception shelter. Cold guarded high-water trim before recognized V1/V2 reconstruction (>=8MiB and >=mapped/8, no pending/no-park/graph lock), no forced GC. 91 host tests pass; fresh exact strict runtime193 members codegen e3e5f6cc archive43e5624a, actual25-module native closure fallback0, actual private/trim/scheduler/GC relocations. Complete 4.36MB c_parsetab wire output f777240c exact, 1/4 carriers probe2 exact. Five alternate CPU4 triples15/15 exact: decode5.486637→2.473342s −54.56% (5/5), whole CPU8.007139→5.468905 −32.47% (5/5), normalized−54.15/−31.94%; peak RSS216524→230184KiB +6.33% adverse5/5 max control drift .009%, guard +6.82%. Ordinary RSS noninferiority NOT met; maintainer explicitly accepted default tradeoff and requested continue memory fix after commit. Earlier trim diagnostic 543 slabs/35.586MB returned10.347ms, no STW count change, not whole peak proof. Actual68 summary fixture blocked pre-codegen missing contextual host exports; no complete summary/Stage2 qualification. Exact source eeb97451/header66de0d49/wireb61d0a8c/testbc2a1d25; all original adverse receipts preserved. No CI result claimed; continue real worker peak/root cause and Stage1-first.

   - 2026-11-27 post-merge native AST JSON memory attribution (929953fa), exact same candidate-runtime ELF cold phase diagnostic: provider/private postparse RSS118.18→155.87MiB, after production trim121.93 (+3.75 vs provider), AST/raw coexist152.39→156.57, projection154.93→151.29; highest cumulative peak occurred in full validation JSON reencode224.88→229.96 (+5.07MiB), not actual summary worker step. Raw reserve similar after drop; no complete worker conclusion. Actual68 parallel admission failed contextual host exports; distinct official serial explicit-target path passed all68 direct captures fallback0 but TIMEOUT360s/919756800B, 194524808B IR and211 object_write completions, no ELF/output; no retry/closure widening/cap changes. Exact receipt guard0cd82078, stderr372415bf. Lossless 465 own timeout .ll/.pco artifacts266312181→33467948B verified per-file compression (manifest86705df8), original manifests/results/receipts preserved. Release; actual hosted Stage2 memory qualification awaits next normal CI containing default0648fe67. No CI triggered.

   - 2026-11-27 top5 constructor formal native scope held: exact rebased wire60611c09/testb50f509b, 82 host passes, all 15 exact native output; five triples decode −13.864%, whole CPU −7.921%, peak RSS +1.014% adverse (1/5 wins), not merged. Preserve source/receipts and release this unit. Next test existing guarded trim after raw reconstruction owner release with exact merged baseline, no broad RSS exception.

   - 2026-11-27 exact same-ELF 26-module post-release trim diagnostic: 3 outputs canonical f777240c; raw-release RSS157.12→113.87MiB, free raw43.06→0MiB, live requested34.42MiB unchanged, STW680 unchanged; whole diagnostic RSS235660→217512KiB (−7.7%), control234680, CPU6.275552→6.531293 (+4.08%), control6.274442. Artificial canonical dumps own peak; actual summary does not dump AST. HOLD, no production edit. A narrower summary-only threshold >=32MiB and >=1/4 mapped could avoid small batches; needs authentic actual worker five triples, not another unsupported local full build. No Stage2-wide claim, release.

   - 2026-11-27 #650 actual class_gen frontend owner diagnostic: exact f2a870a4 clean full629 context, two guarded arms both rc0, identical CFG727/87118/335663, fallback0 and 90,228,084B serialized digest09438222. CPU21.213→19.870s (host drift, not speed), RSS640660→643796KiB, samples4388 frontend3240/capture1148. Scope correction: old context wrapper omitted hosted PCC_DIRECT_INDEXED_KERNEL_FUSE_USES=1, so capture attribution is FUSE0 and cannot infer hosted duplicate-use cost. IRBuilder.call 813 inclusive, args helper765, publish493; no removable fraction proven. HOLD no production or CI; receipts retained and claim released, no retry.

   - 2026-11-27 compact V2 tape scratch host model proof: test-first red→green, 5 methods incl all known kind arities and 24+ malformed/unsupported cases; guarded corpus10.809s/142585856B. Actual frozen c_parsetab f777240c accepted, 656868 eight-byte records +32 header =5254976B (5.01MiB) vs observed raw live overlap46.46MiB, format arithmetic only; exact raw value oracle. Real historical task57 7/8 (only depth64 fallback), task53 8/8 and minimal1/1, total16/17, 98.7% bytes. Host model intentionally slow, no native CPU/RSS claim. Preserve scratch and receipts, release; next independent native strict scanner diagnostic before ABI/production.

   - 2026-11-27 local exact native compact tape scanner diagnostic successful. Temporary scanner runtime db877e8c (80 host pass), strict193 archive32fc800f/prov2363e7ed build120.707s/399450112B, actual22 closure ELF c010b245 fallback0/no py_cpy, native1/4 carrier probe2 eight jobs exact. c_parsetab tape5254976B SHAa55e82f5 exact model and raw oracle f777240c. Actual postparse raw vs scanner+semantic validation live requested59,412,333→11,171,587B (−81.2%), usable67,412,080→11,259,232, mapped153,997,312→12,713,984, RSS161,693,696→20,914,176, alloc_count504404→104913, STW1→0. Lexical raw .847838 vs tape+semantic .957869s unequal scope; whole output modes different, no whole CPU/RSS claim. No production ABI/reader change or CI; preserve scratch receipts and release for next materializer design.

   - 2026-11-27 scratch native compact materializer completed, same exact diagnostic archive32fc800f, 25 modules fallback0, ELF073e5381, native1/4 carrier probe2 exact NATIVE_TAPE_MATERIALIZER_OK36, full canonical f777240c both arms. One fixed CPU4 same-ELF full screen decode3.700432→4.248125s (+14.8%), whole CPU7.457241→7.385336 (−.96%), peak RSS234284→217256KiB (−7.27%); decode-return current RSS164188160→53854208B (−67.2%), mapped155136000→44511232, requested35770591→25743982. Actual worker no dumps, whole full screen no formal speed claim; hold due decode CPU adverse. Preserve exact receipts and source-only remove duplicate validation/token accesses; no production or CI. Release.

   - 2026-11-27 AArch64 cross-target typed finalizer retention diagnostic blocked: clean184f51f0 plus exact historical625/inlineOFF ARM c_codegen PIDX48c535cf, CFG1039/95041/358843. Baseline TIMEOUT150.2366s rc124, peak1508163584B within1.5GiB, preflight valid, last typed_fold_cond_branch_to_fallthrough begin; no PCO or census, no retry. Historical prior actual latest1,493,150 vs cumulative>=19,675,235 span IDs (>=145MB obsolete pointer positions) remains directional older evidence, not current Mac memory attribution. Preserve exact receipts, release; no production or CI/cap change.

   - 2026-11-27 bounded actual smaller historical ARM worker_resource_plan cross-Darwin exact clean184f51f0 diagnostic COMPLETE both arms, final PCO6149696B SHA75affd65 both. Baseline23.638s/221720576B, census22.660/221757440 (not speed). Final latest295556 IDs vs retained3874300, obsolete3578744 (92.37%, min28629952B pointer refs); allocated run list32208792B (14.5% measured peak), latest list ~2.36MB. First transfer299286 latest/retained; six changed peepholes retained1794850 then seven CFG passes append runs. Actual RSS167→221.50MB at final CFG→194.79 after publish semantics retirement. Cross-target Linux-host evidence not hosted Mac measurement; no production/CI, release; next narrow private pass ownership renewal test-first without public persistence change.

   - 2026-11-27 packed compact tape native formal HOLD: host8 pass, actual record reads1313743 vs prior3241463 source count; strict25 ELF469f10af, 1/4 native exact, 5 triples all15 canonical f777240c. Decode median3.036267→3.240482s paired+7.202%, normalized+15.027%; wholeCPU6.552245→7.074864 paired+5.241%, norm+11.681%, 0/5 wins; whole RSS233888→230984KiB paired−1.592/norm−1.486%, guard +.252/+0.530%. Decode-return RSS164163584→53395456 −67.499% all5, mapped−71.61%, requested−29.45%, STW680→597. Preserve exact formal receipts, no merge or retry. Next distinct side-plan to remove149944 repeated label/file-id token calls only after source/ownership proof; no ABI or CI. Release.

   - 2026-11-27 validated POD side-plan HOLD: host11 pass, actual149944 repeated token calls removed, exact2627472B plan freed on all paths. Strict25 ELF41c48fae, 1/4 native exact; one same-ELF full canonical f777240c screen decode3.007686→3.957636s(+31.58%), whole CPU6.434076→7.094325(+10.26%), RSS232560→217868KiB(−6.32%), decode-return RSS −67.02%. CPU adverse stop, no formal/retry, no production/ABI/CI. Preserve receipts, release; next structural typed construction or converted scalar only after independent ceiling.

   - 2026-11-27 Stage1 AArch64 private typed peephole-to-CFG history retirement (memory-only): #650 Mac actual finalize1309.751 worker-s with 1.428–1.574GB giant backend peaks; earlier giant cross-target diagnostic timed out at150.237s/1.508GB with no PCO, no retry. Independent historical625 ARM worker_resource_plan PIDX55b2bf2f/source23bfa297, inlineOFF, cross-Darwin structured host replay (not hosted Mac source/runner); current a6bc18e1 exact three-file source overlay. Actual same PCO6,149,624B SHAed92ccb29b44451dc2440e2600a423eebcce85a34afed2ef6b60583b022274d5 across corrected screen and formal15 arms. Exact latest298,465/retained1,794,742→298,465 run IDs, obsolete1,496,277→0, list14,358,008→2,387,784B; boundary RSS222.085→164.680MB paired−25.323%5/5; process peak247664→237176KiB paired−3.080%4/5, control-normalized−3.571%4/5/max control1.019%; sampled guard peak251.601→228.700MB paired−9.683%4/5, normalized−9.659%3/5, max control3.226%. CPU mixed paired−.342%3/5/normalized−2.027%3/5/max control3.602%; first two adverse +9.60/+16.94% with pre-boundary function emission +.772/+1.925s vs mean controls; new prepare residual median+.053s, no speed claim. Private factory-only one-shot canonical authority, revoke on observer changes/failure, copy latest exact checked run once, old owner never closed, public/custom/malformed fallback and all CFG/pass/order/output unchanged. Intended test-first red, focused26/26 PASS; combined span+full timing suite resource-limited at original.75GiB, no whole suite claim. Exact629 native contextual two modules 204/324 functions, fallback0/no libpython, guard53.915s/402,464,768B; not native execution. Original60s/.75GiB formal and120s/1GiB context with2GiB reserve, no limits/default/CI change. Preserve all raw receipts and release claim. [skip ci]

   - 2026-11-27 scratch packed+direct top5 formal qualified scope: host10 pass actual87648 direct/12314 generic c_parsetab, strict25 ELF0b16a285, native1/4 exact; 5 triples all15 canonical f777240c. Decode median2.784734→2.350376s paired−15.908%, normalized−14.932%, wholeCPU6.193965→5.768743 paired−5.897/norm−4.657%, both5/5; peakRSS234148→225196KiB −3.823/−3.132%,5/5; guard−4.130/−4.078% 4/5 base/3/5 control. Decode-return RSS norm−70.546%, mapped−75.044, live requested−44.058, cumulative decode peak−63.331, alloc−42.434%, STW680→448−34.118%; exact receipts retained. Scope scratch only, no production/ABI/CI, release. Next integrate exact runtime+reader after remote identities, strict native actual reader and formal same oracle/controls; no broad stage claim.

   - 2026-11-27 production compact V2 AST native reader qualified: private strict canonical lexical tape (bounded immutable 8-byte records, no managed graph), full schema/files/kind/arity/span preflight then packed reads + direct five exact constructors; unsupported returns to original owned JSON/highwater/public-provider fallback before constructors, no retry after construction, CPython/public JSON unchanged. No header/runtime ABI table edits (preserve Mac U11). Test-first3 intended red, focused42 PASS2.32s incl checked heap failures, all constructor contracts, malformed/V1/Unicode/error precedence and shared spans. Exact base0fe6f0e6 source runtime69702f55 / reader2feff8be / tests5e363abd,a619e56c; fresh strict193 runtime266775d3 manifestd2b3cc3d codegen4c25e35a built110.060s/399372288B, actual25 closure fallback0/no py_cpy 45.589s/357101568B ELF3362fb48, actual reader range scanner/legacy/materializer and runtime token/data, top5 constructor relocations, native 1/4 carriers GC probe2 eight jobs each exact. Same production ELF5 alternating CPU4 legacy/default/legacy triples15/15 full frozen c_parsetab canonical f777240c output, decode2.956324→2.346275s paired−20.347%/control-normalized−16.044%, whole CPU6.125858→5.553617 −9.341/−7.099%, wall−9.028/−6.729%, peakRSS234868→231032KiB −1.633/−1.261% (all5/5 both). Guard sampled peak−1.367/−2.505%,5/5 base/4/5 control (one +.873%); control CPU/decode/RSS −2.414/−4.843/−1.062%, max CPU/decode control drift8.373/9.516%, all normalized candidate favorable. Decode-return RSS164360192→51286016B norm−68.776%, cumulative decode peak−63.360%, mapped−73.101%, live requested−35.695%, alloc−44.052%, STW event count680→457 norm−32.794% (not GC cycle count), raw reclaimable45154304→65536. Existing guard30s/1.5GiB/2 reserve each, campaign lock; initial duplicate-lock refusal before any process preserved, corrected launcher uses documented child lock-off while parent holds lock. Raw receipts retained. Full process peak includes canonical output reencode; actual summary worker does not reencode, previous real68 fixture blocked/timed out, no whole summary/Stage1/Stage2 or cross-platform gain claim. Publication base4bc21146 differs since tested0fe6 only ARM Darwin memory emitter + ARM unit tests/status; four exact preimages/runtime header/ABI unchanged, Linux x64 proof unchanged, latest global codegen provenance not falsely relabeled. Release claim, no CI. [skip ci]

   - 2026-11-27 postmerge small actual CFG census clean25f61: COMPLETE22.102s/229675008B guard, exact PCO6149624B ed92ccb29 unchanged. At accepted boundary gen3 latest298465; after fourth CFG pass drop_labels latest297466 vs retained1488668, obsolete1191202 refs min9529616B, allocated11909400B. Current RSS204677120 before subsequent TLV229449728/pair229675008/final loop248700928 (process highwater242424KiB); final retained2377789 vs latest295538. Actual peak follows midpoint, final-only retirement too late. Cross-Darwin Linux-host historical frozen input, no hosted Mac claim. Preserve receipts, release; new independent private midpoint candidate only with unchecked marker preserved and observer/old-root consistency. No CI.

   - 2026-11-27 native pcc1 class-method phase attribution diagnostic only: #650 clean same-source x64 Stage1/Stage2 and Win exact IR CFG counts for CLI/c_codegen/class_gen/type_infer; actual CCodeGenerator12.946→89.710s (Win90.844), ClassLowering11.295→82.215s (Win82.327), frontend generation6–7× despite identical output counts; prior older6cb native method trace156 methods163.601s partial and host profile77.85M calls are not native callee CPU evidence. Extend only existing PCC_PY_FRONTEND_WORKER_TIMING at actual ClassLowering._emit_method_body coarse boundaries: snapshot/binding/boundness-entry/body/default-return/finalize/restore, one completed row with PID/module/class/method/symbol/absolute same-clock monotonic endpoints, continuation/deferred-stub distinct, nested inclusive intervals can be subtracted offline, unhandled original failure no completed row, disabled no clock or output; no per-statement or per-hot-call timer, no change to debug lowering route, no ABI/header or resource gate. Test-first intended missing rows1 FAIL/5 controls; initial hoisted-local nesting assumption wrong, kept failure and corrected assertion to actual deferred order, independent genuine reentrant nested interval test. Final9 PASS3.14s incl exact normal and direct PIDX on/off, actual deferred strict stub and original error/cleanup, no new source semantics. Exact tested d0a89d04 class_gen a3b165da / new test b0d0db52; full actual629 context class_gen index210, strict fallback0/no libpython, ordinary x64 host OFF/ON/OFF all3 identical complete standard encoded PIDX92048738B SHA1c438a260 (727 funcs/88788 blocks/342739 IR; candidate source contains added diagnostics, not old #650 shape). Digest-only POSIX FIFO consumed full original codec stream, tiny real regular-vs-FIFO89913B exact SHA6e269e68, no large duplicate files. Guard137.068s/704655360B under180s/1.5GiB/2 reserve+lock. Host generation CPU22.3600/22.2971/20.6641s, wall22.3639/22.4440/20.7802, ON CPU−.28% vs first OFF/+7.90% vs control, OFF drift−7.58%; diagnostic not formal overhead or speed proof. ON156 valid rows (88.5KB total log), OFF none. Inclusive actual host sum11.931s: snapshot .001/binding .310/boundness .398/body11.086 (92.9%)/return .036/finalize .097/restore .003; no nested overlap in this owner. Native split unknown until next usual consolidated hosted CI; no native execution or whole bootstrap/performance claim. Preserve receipts, release claim, no CI. [skip ci]

   - 2026-11-27 Stage1 AArch64 private CFG midpoint span-history retirement (memory-only): accepted peepholes-to-CFG owner336baa1e kept298465 IDs, subsequent actual postmerge25f61ccc census on historical625/inlineOFF ARM worker_resource_plan PIDX55b2bf2f cross-Darwin host structured emission showed fourth CFG pass latest297466/retained1,488,668, obsolete1,191,202 refs (9.53MB minimum), process RSS rose after midpoint204.677→248.701MB; retiring only after seventh pass would be too late for this peak. Single derived private canonical owner permission only after successful prior retirement, revoke on timer/custom/failure, exact owner-type before generation read, preserve unchecked -2 run marker, old owner/cursors untouched, same CFG/matcher order and public fallback. Test-first intended red, focused33 PASS; original e95 source screen+formal15 exact6,149,624B PCOed92ccb29, process HWM norm−9.477%5/5, guard norm−2.766%5/5, CPU norm−2.223%3/5 within10.100% control, strict629 three modules fallback0. Fresh remote preimage conflict with Claude9e9a0e17 branch14 selection/relaxation caused publication STOP; exact conflict-free three-way rebase on d0a89d04 preserved both changed hunk lists byte-for-byte, no overwrite. Current rebased focused36 PASS (old33+three peer encoding/relaxation/selection host controls; Darwin native3 UNRUN). Same historical frozen input, current clean d0a source c1dc817a/candidate diagnostic-only exact four-file source12143f1d, new PCO6,149,220B SHAec92f4511e9832693b425a3e91160c92ec5815eca78b3ac31c6cf01c8a43ce5c exact all current screen A/B/C and formal15; previous PCO legitimately differs with peer branch14. Current exact CFG entry298364, midpoint latest297365/retained1,488,163→297,365, obsolete1,190,798→0, unchecked marker -2 unchanged. Process HWM med240104→216128KiB paired−10.177%5/5/norm−10.322%5/5 beyond max control6.534%; guard peak245.809→221.528MB paired−9.083%5/5/norm−5.785%5/5 within max control7.452%; immediate midpoint RSS mixed paired+.047%2/5/norm+.054% (allocator retention). CPU med24.296→24.831s paired−.969%3/5/norm+1.737%2/5 within max control15.417%, adverse fifth pair+16.126%; transfer residual excess median+.036s, fifth normalized whole+2.866s incl pre-boundary emit+1.429s, no speed claim. Current exact629 strict contextual3 modules 204/76/327 functions, fallback0/no libpython, 67.355s/407,535,616B; source lowering only, not native pcc1 execution. Original per-arm60s/.75GiB+2 reserve and strict120s/1GiB+2 reserve, exact checkout restoration, no resource/default/CI change. Preserve old and rebased receipts and release claim. [skip ci]

   Claims:
   - Runtime QPS through the self AArch64 backend toward LLVM parity: locate the LLVM step the self passes lack (pcc_perf reference ablations, scripts/pcc_perf.py), then implement it in pcc's own AArch64 passes (shrink-wrapping, allocation or selection, whichever the ablation ranks first); IR-pass changes are target-neutral and reach x64; avoids the typed x64 MIR files: Claude, macOS, 2026-11-27T15:16Z, area backend











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

   Actual class_gen snapshot attribution (diagnostic only, old exact source
   6cbafc4a, same retained native AST/context, same one-worker indexed
   assembly route; only manifest output destinations differ). Host trace
   enabled only inside method body, no map mutation: before existing restore
   loop capture original snapshot size, current identities/new/deleted keys;
   direct writer and alias-restoration site observations count actual map
   mutations, plus paired local-binding inventory visit counters.
   COMPLETE 107.810384 s, wait4 CPU 107.356188 s, peak isolated tree
   844,271,616 B. Exact byte-identical 57,304,557-byte assembly SHA256
   7165f5ed623388294453b9a74154db3e9562185daf3d9f446b03c35811777e16,
   exact 727 funcs/332,713 instructions/86,393 blocks. 156 restored method
   scopes (155 ClassLowering +1 ClassInfo): 18,843 entries copied and
   18,843 restore writes, table size 118–123; exactly five direct binding
   mutation attempts/effective, all new helpers preserved; zero changed
   preexisting identities, deletes, or preexisting effective writes.
   Duplicate forced-vs-mixed local binding inventory scans each involved
   2,079 recursive calls and 3,697 statement visits (ClassLowering alone
   2,078/3,660 each). Trace rows SHA256
   2bfb98bb5a58c2109304f7d8e138a299cdf9b58528a1d002cd07f12d9c08293c.
   Decision: real redundant work, but low absolute count versus 129.594 s
   native class owner; no isolated native cost evidence. Do not replace
   broad restore semantics with risky sparse journal as primary fix.
   No speed claim or production changes. Next: actual host nested
   method-generation call profile (not methods-being-compiled trace),
   then source-matched native focused experiment on measured owner.

   Actual nested ClassLowering method-generation cProfile (diagnostic only).
   Old exact source 6cbafc4a, same actual native AST/context and host
   one-worker indexed-assembly route; profile enabled only within 156
   outer method-emission calls, including nested calls. COMPLETE
   109.100990 s, wait4 CPU 105.649357 s, peak isolated tree 790,835,200 B,
   byte-identical 57,304,557-byte assembly SHA256 7165f5ed623388294453b9a74154db3e9562185daf3d9f446b03c35811777e16,
   exact CFG counts. Profile SHA256
   ad9dacfde28883687330ee47d2e42864790781b615fa2799261305132325ae9c.
   77,852,099 calls / 76,613,209 primitive calls, 46.654 s profiled
   interval (instrumented host, concurrent independent build; not native
   CPU or causal A/B). Existing IR builder call 155,077 calls/10.175 s
   inclusive, publish_call 155,077/6.686 s; _resolve_method_mro
   245,699/3.091 s, method_overridden_by_subclass 1,438/3.615 s,
   annotation_module_bindings 2,025/2.992 s, _closed_world_is_node
   1,543,050 total/959,850 primitive calls, _name_is_stored_in
   124,783 total/509 primitive/1.938 s. These nested times overlap.
   First candidate: eliminate repeated source-verified annotation
   namespace scans within the exact frozen current AST owner; no
   global/public cache, identity-based invalidation on AST replacement,
   unchanged import/rebinding/scaffold proof. Require exact output and
   native focused source-matched evidence before production.

   Immutable codegen annotation namespace reuse (measured structural fix).
   Private per-L1 cache holds one exact frozen Module/tuple-body identity
   and source-verified binding map, invalidates on native import AST
   replacement even equal value, custom/mutable adapters and public
   analysis still fresh. Constructor-owned fixed layout/host contract;
   both scaffold nonprovider and tuple-base consumers share only source
   facts, never relax independent receiver/provider proof. Host tests
   97 passed/6 existing selection deselected, including rebinding/star,
   exact identity, public mutable result, real consumer replacement,
   tuple-base and scaffold proof. Contextual direct capture full
   625-source graph complete63.524 s / 370,790,400 B, all five changed
   compiler modules zero fallback. Fresh candidate owned runtime
   135.177 s / 389,312,512 B; strict older archive codegen stamp rejected,
   not bypassed. Native fixture explicit actual pipeline_closed_world
   helper dependency added after incomplete fixture failure; full
   58-module build179.745 s / 1,356,324,864 B, static ELF no libpython,
   SHA256 d69cba4363fbe7d72453a9410ef78954debf78f929bbeaced0fa43685f2074da.
   Actual old retained native class_gen v1 AST SHA256
   b248d4510926ae8d117800144cc0e8aed91793d7bea7e9b803ef122d25a00240;
   native contract rc0 NATIVE_ANNOTATION_CACHE_OK 95, exact binding
   map/hit identity/equal distinct and empty replacement/original restoration.
   Old 6cbafc4a source + exact five candidate blobs (all production
   preimages match) separate host compiler actual same-route replay
   COMPLETE68.377293 s, peak823,328,768 B; all 156 methods, annotation
   scans 2 total (inference+first codegen), zero within methods versus
   baseline cProfile2,025. Exact 57,304,557-byte assembly SHA256
   7165f5ed623388294453b9a74154db3e9562185daf3d9f446b03c35811777e16,
   exact 727 functions/332,713 instructions/86,393 blocks. Host timings
   concurrent diagnostic only, no A/B speed claim.
   Exclusive native 5 alternating baseline/candidate/control triples,
   same static binary, actual AST, actual 2,025 scan count per arm,
   pinned CPU, all15 exact map95/checksum210600. Median inner scan
   3.123073436 -> 0.046051551 s (-98.525%,5/5, control+0.321%);
   full process CPU9.048502 -> 6.008944 s (-33.592%,5/5,
   control-0.269%), wall9.202035 -> 6.110530 s (-33.596%,5/5,
   control-0.859%). Median RSS223,180 -> 223,708 KiB (+0.237%,
   1/5 wins), unchanged control223,620 (+0.197%), residual+88 KiB
   (+0.039%) within control; no RSS reduction claim. Harness COMPLETE
   123.060 s, peak242,233,344 B, stdout SHA256
   44a8bd5d3994b873f475bf0f9b9a8daa32677804da85f70dbcbd25183c07d1a8.
   Isolated actual owner benefit, not whole Stage2 completion or
   extrapolation. No relaxed limits/full bootstrap. Next larger measured
   method owner: repeated MRO/subclass override scans; require actual
   key/walk counts and cache invalidation proof.

   Actual nested MRO/subclass owner attribution (diagnostic only):
   same old source/native AST/context, host singleton indexed assembly,
   read-only wrappers only inside 156 method scopes. COMPLETE74.298459 s,
   CPU74.061089 s, peak833,843,200 B; exact original 57,304,557-byte
   assembly SHA256 7165f5ed623388294453b9a74154db3e9562185daf3d9f446b03c35811777e16.
   Counts SHA256 232da0266a82ff74408ab50232ed4bd9d5e1d51aed0679e53a64b6755d141875.
   245,699 MRO calls, all initial exact local registry; 244,237 negative
   (172 distinct negative keys), 1,462 positive incl 1,245 cache hits.
   243,470 calls originate in subclass override and all are negative.
   151,998 exact local leaf-absent queries (107 unique keys, no eligible
   Name base) verified every one returned None. 1,438 override calls
   all false, 745 __getattribute__. Registry grows313->331 throughout
   generation, ruling out broad negative caching without generation
   proof; many class/method/base mutations exist. Existing source
   allocates visited set/queue and two nested closures on non-hit,
   even leaf miss. Next narrow no-cache change: lazy graph-walk storage
   only on discovery of a base edge, remove per-query closures and
   keep current positive cache publication/order and mutable miss
   semantics. Require test-first, actual same-route exact output,
   source-matched native measurement and no fallback before production.

   Lazy local MRO walk allocation (structural, no negative cache).
   Remove two per-query nested publication/miss closures; publish positive
   cache inline, re-read cache after external declaration, no miss caching.
   Initial class inspection precedes graph-walk allocation; visited/queue
   created only on actual non-object base edge, preserve breadth-first
   order, qualified import lookup, local/remote priority and live changes.
   Test-first leaf allocation guard red then 13 class-model tests green
   (mutable miss, BFS/cycle, external cache publication). Exact contextual
   625-source graph native capture41.894 s / 270,667,776 B, changed
   class_model_lowering 67 functions/31,962 instructions, fallback0.
   Fresh exact source-owned runtime99.949 s / 394,907,648 B.
   Actual old-source host projection replay COMPLETE66.265 s /
   790,360,064 B, exact original 57,304,557-byte assembly. Projection
   SHA256 ee24cb03ad499a1c6b8b536207472bebf06e2524e69e019a1a5c2c744defa91f:
   151,998 ordered actual leaf queries,107 descriptors/103 classes, all
   base tuples empty, exact method names1–155/field names0–230, actual
   positive-key union220 no leaf overlap (cache grew4–220; 452 export
   modules). Focused native projection uses these actual descriptor/key
   facts and exact baseline/candidate method bodies (only method names
   changed), not the full native compiler. Function SHA256 baseline
   c8c80347639f791f3b8ef62b949793b3f0babacea9dcb07c37ffa6b35231353c,
   candidate79273a9238b853394ced1278c73f8684b266c2822035ea9b9aaf6ce69c72c465.
   23-module native build50.224 s / 575,664,128 B, static no libpython,
   binary SHA256 e62bed7c0fdea2df1f000affee373cc5ed76b70f2c5f92f738a9c5ff3f5f661e;
   contract exact miss/then-live registration rc0. Additional same-body
   native BFS/cycle contract rebuild49.234 s / 534,970,368 B,
   SHA256 498bc58adfed6900284e086e1e6889da7060a2be955603cdafa5d74c3210f3a1,
   NATIVE_MRO_PROJECTION_AND_BFS_OK, rc0.
   Old6cbafc4a plus exact candidate production file blob (identical
   preimage) actual host singleton same-route COMPLETE112.922 s /
   775,352,320 B; byte-identical assembly SHA256
   7165f5ed623388294453b9a74154db3e9562185daf3d9f446b03c35811777e16,
   exact all245,699 MRO counts/results/151,998 leaf and CFG. Host
   concurrently measured diagnostic only, no timing A/B.
   Exclusive native projected actual leaf query sequence 151,998 x
   five alternating baseline/candidate/control triples, pinned CPU,
   same static binary, all15 exact negative results/107 descriptors/
   220 positive keys. Inner median1.206446041 -> 0.758256592 s
   (-37.150%,5/5, control+6.263%); full process CPU1.824287 ->
   1.383180 s (-24.180%,5/5, control+3.909%); wall1.828317 ->
   1.414990 s (-22.607%,5/5, control+5.353%); RSS20,664 ->
   20,600 KiB (-0.310%,3/5, control0). Harness COMPLETE26.214 s,
   peak31,174,656 B; stdout SHA256
   894ca64c6384cf62add0c1998497f286a6b7c5a9f88b9694145ae79fd3924120.
   Broad class/subclass/cross-module suite98 passed,1 xfailed,1 failed:
   test_native_cross_module_class_annotation_dispatch.py::
   test_unique_external_class_annotation_dispatches_method_natively also
   fails exact untouched HEAD100c29d8 preimage, same direct-dispatch
   assertion; separate candidate rerun baseline/candidate user_user_run
   body4233 bytes byte-identical SHA256
   b29bb2e32ff99a9fabd8850c0634737aefe3e3c871015c7a921f9113d4b67caf.
   Known baseline failure untouched, not masked; no new regression.
   Narrow projected native leaf benefit, not full native owner or
   completed Stage2 speed. No relaxed guards/full bootstrap.
   Next measured repeated method-generation owner: source binding
   traversal/call argument and IR call publication costs.

   Actual entry-hoist terminator scan attribution (diagnostic only).
   Nested method profile showed 561,229 _instruction_opname_text calls,
   of which480,922 came from _position_at_entry_hoist_point via
   _instruction_is_terminator. Same old exact source/native AST/context
   read-only host wrapper COMPLETE70.195403 s / 840,278,016 B, original
   byte-identical 57,304,557-byte assembly SHA256
   7165f5ed623388294453b9a74154db3e9562185daf3d9f446b03c35811777e16.
   Count receipt SHA256 08972cb6d0f106fdef232192841884fbbac49b0612295b4e82c4d81f24c7fa1c.
   All156 methods,6,465 entry hoists exact owned Block/direct builder,
   no-text/fallback0, no inline anchor. 4,932 flags false scanned
   479,389 exact empty-text records, no terminator;1,533 flags true
   found terminal record at final position, one scan each, no trailing
   records. Largest reverse scan895; total480,922. Native metadata
   cannot be blindly trusted for raw text; Block.append/insert and
   _replace_record_text are distinct from direct compact API.
   Production candidate only with exact owned Block/IRBuilder/Module
   guard and monotonic module-level raw-text mutation marker (one field
   per Module, no per-Block/per-direct-instruction overhead). Any text
   anywhere conservatively falls back to existing textual scan. For
   proven direct-only false flag end placement, true flag verify last
   terminator then numeric position, custom/mixed/trailing fallback.
   Require mutation coverage, actual ordered projection, native
   source-matched A/B, contextual proof and exact old output.

   Verified direct-only entry-hoist positioning (production implementation,
   Linux) rejects module-wide marker: actual temporal raw intersection
   all6,465 hoists had prior raw text in module; function-clean5,298
   visits479,755 of480,922 (99.76%), function-prior-raw1,167 only1,167
   visits, entry itself clean all. Ordered projection SHA256
   22d5d866472c67248e5aa494d24752098a8c4206e8463fc89c2cbe806865ac66:
   4,151 unique snapshots/1,737,269 records,6,465 queries/1,788,109
   records visited;1,533 true-tail identity scans1,308,720 visits.
   Function monotonic marker, only raw append/insert/record text
   replacement; no direct emitter mutation/per-record or per-Block
   field. Exact Function/Module/Block/IRBuilder ownership/route guard;
   false termination flag/function clean -> end; true flag verifies
   exact final Record owner and terminator -> numeric insertion point.
   Inline anchor precedence, text/mixed/custom/trailing/wrong owner
   fallback unchanged. Actual old-source host singleton candidate
   COMPLETE80.914s/847,556,608B (concurrent diagnostic only; no timing
   A/B): same727 functions/332,713 inst/86,393 blocks, fallback0,
   exact57,304,557-byte assembly SHA256 7165f5ed623388294453b9a74154db3e9562185daf3d9f446b03c35811777e16;
   false reverse visits479,389->0,total480,922->1,533; no old forward
   identity scan on verified final records. Exact current candidate
   full625-module context capture both changed production modules:
   ir.py963 functions/129,269 inst; ownership_lowering.py190/55,605;
   zero fallback/no libpython edges, COMPLETE72.327s/397,111,296B.
   Real native42-module static fixture actual IR classes and exact
   old/new method/helper bodies; contract passes raw mutations,
   trailing compact record, inline anchor and clean/terminated paths.
   Five exclusive alternating baseline/candidate/control triples,
   pinned CPU, same candidate binary and exact ordered projected
   entries: inner2.127783->.031632s (-98.513%,5/5,control-.851%);
   full process CPU9.059856->6.695150s (-26.101%,5/5,control-3.200%);
   RSS386,472->386,472KiB median,control0. All15 exact positions/checksum,
   receipt SHA256 2f15a87ccc4ac204fbd8617bdbece1e77c1a77119250783f8a86710f3ecd6d8f.
   Stronger source-matched separate static owner binaries, same fixture
   path, sole exact old/new hoist body difference, actual preimage and
   candidate IR classes and independently source-matched owned runtime
   archives; 5 alternating exclusive triples, all15 exact:
   inner2.184601->.032293s (-98.522%,5/5,control+.705%), full process
   CPU9.183733->7.021626s (-23.543%,5/5,control+.598%);
   wall9.340329->7.187359s (-23.050%,5/5,control+.939%);
   RSS386,272->385,892KiB (-380KiB/-0.0984%,5/5,control-8KiB).
   Harness COMPLETE130.674s, receipt SHA256
   5c94d2fb98f43b3ec444de5634c587ef778ab5db4cc9ea5991cc39f7242f99e4.
   Independent identical-source/path native Function allocation fixture
   actual preimage/candidate runtime and class layouts, 5 triples each
   count727/4096: count727 requested787,372->793,188B (+5,816B,
   exactly+8/Function), usable961,616->961,616B (5/5 ties); count4096
   median requested5,340,586->5,373,354B (+32,768B), usable6,310,656
   unchanged (individual large-run GC variation). Independent tiny
   allocation-only process RSS +68KiB at both counts (727 +.317%,
   4096 +.226%, control0); do not hide this synthetic footprint.
   smaps short-process sampling inconclusive for causal attribution:
   file mapping median-60KiB; anon/heap727 +348KiB vs control-72,
   4096 +4KiB vs control-4. Actual source-matched projected owner
   process RSS -380KiB all5 beyond control, so qualify this measured
   owner, not a blanket memory guarantee/whole bootstrap result.
   Allocation fixture receipt SHA256
   d582f12d9dc0dfd96b4a4020bdb46135d89ff29943cc47c0477064a71f5c6575.
   Focused regression guard: 131 pass/18 fail/1 deselect vs untouched
   exact8fc6331e preimage119 pass/18 fail/1 deselect; all18 identical
   failed node IDs and error lines (existing stale shared-pinned model
   missing cbranch and root-leave model missing cleanup-plan helper).
   Test-only explicit distinct Function discriminator in injected fake
   ir namespace preserves custom owner path and baseline failure; no
   hidden failure change. Twelve new placement/mutation contracts pass.
   Existing unrelated whole-stage ABI contextual test intentionally
   stopped with33 green dots, not claimed; excluded only that named
   test from focused replay; both changed production modules exact
   full contextual proof completed above. Next actual class method
   owner: repeated IR builder call publication/argument lowering;
   retain exact source/current native phase evidence. No full Stage2
   bootstrap or limits change claimed.

   Next actual nested method-generation owner (source-only plan):
   _irbuilder_call_from_args_list155,077 calls, self2.001s/cum9.918s;
   publish_call155,077/cum6.686s in previous actual host cProfile.
   Exact direct/no-text Function signature currently materializes
   expected_arg_types list and empty arg_type_parts list even when
   FunctionType.args is an immutable tuple; publish_call read-only
   indexes expected types. Measure actual exact Function/FunctionType/
   tuple/no-text eligible counts and copied signature entries on
   authentic old source/class_gen route with byte-exact output before
   any candidate. No signature memoization (known denied), no broad
   dynamic type assumptions; preserve text/duck/custom/mutable fallback,
   source-matched native CPU/RSS and exact contextual/actual output gates.

   Direct signature candidate DENIED (RSS gate), no production delta.
   Actual old-source nested method diagnostic COMPLETE65.391s/
   838,983,680B, exact original57,304,557-byte assembly SHA256
   7165f5ed623388294453b9a74154db3e9562185daf3d9f446b03c35811777e16.
   All155,077 calls exact owned Function/FunctionType/tuple/IRBuilder,
   no-text, fixed/actual args245,700, nonvararg, void106,117. Potential
   310,154 redundant lists and245,700 copied type entries. Ordered
   projection479 exact distinct callee signatures,156 methods,
   canonical void/ptr/i1/i32/i64; projection SHA256
   1609952c877d66b6b703807fd950934f5373d520dd0c0f8d6760203b20bbdf29.
   Test-first exact preimage3 red/6 pass; candidate42 pass/1 unrelated
   whole-stage context deselected. Exact625-module contextual ir.py
   candidate capture966 functions/129,453 inst,zero fallback/no
   libpython edges, COMPLETE50.138s/391,811,072B. Same actual old
   source candidate singleton COMPLETE64.363s/850,341,888B (concurrent
   diagnostic only), byte-identical assembly and all155,077 expected
   signature collections list->tuple, all fixed/actual args unchanged.
   Real native independently source-matched static baseline/candidate
   production IRBuilder/publish_call fixture, same source/path, exact
   observed ordered signatures and method boundaries; representative
   SSA operands only, not original complete SSA graph. Actual packed
   publication columns, metadata, uses, names/types/value IDs all
   byte-identical4,031,822B SHA256
   da215ed741397aba77ef12b7671ec8569959196e93e1f221a6184afa714cf017.
   Exclusive five alternating paired/control triples, pinned CPU,
   15/15 exact outputs, inner6.088106->5.720917s (-6.031%,5/5,
   control-2.728%); full CPU10.236052->9.854029s (-3.732%,5/5,
   control-1.457%); wall10.351100->9.970194s (-3.680%,5/5,
   control-1.248%); RSS103,236->104,140KiB (+904KiB/+0.876%,
   0/5 wins, control-4.630%). Reject despite CPU, memory gate
   noninferiority not proven. Harness COMPLETE154.345s, raw receipt
   SHA256 93ab1eb4f43fc5d91246029ceeeb782d64f7e02ad5d3f12b613925cda2bdf681.
   No new persistent class field/cache; suspected GC pacing or widened
   union/local root lowering, not measured attribution. Do not retry
   unchanged; any narrower alternative needs new actual memory proof.
   Next measured nested owner _name_is_stored_in:124,783 recursive
   calls/509 outer all prepare_rebound_object_parameters, host profile
   cum1.938s,2,875,180 getattr and3,039,137 len calls across37 audited
   field names. First measure actual node kinds/field hits/fallback
   on same authentic old route; exact owned frozen AST leaf/type
   dispatch may remove absent dynamic field probes without any cache
   or retained AST. Preserve original field order, subclasses/custom
   and malformed fallback, exact output/native CPU/RSS; no change
   before actual counts, no unqualified full Stage2 claim.

   Rebound-name no-cache exact type dispatch, streaming fixture DENIED
   under peak RSS gate (no production delta).
   Actual old source read-only method owner COMPLETE80.502s/
   841,580,544B, same original57,304,557B assembly SHA256
   7165f5ed623388294453b9a74154db3e9562185daf3d9f446b03c35811777e16.
   509 distinct(body,ident) pairs across156 exact bodies, no repeated
   pair,16 true,124,783 nodes;2,875,180 generic field probes,75,241
   hits. Exact Name29,302/1,084,174 absent probes (37.7%);
   Attr11,868/439,116 probes(11,868 hits); Call9,792/362,304
   (29,376 hits); StrLit5,373/198,801; If2,773/102,363;
   Compare/BinOp3,647/134,939. No custom nodes in actual projection,
   but preserve generic subclass/custom and malformed known-field
   handling. No result cache (zero repeat pairs), no retained AST.
   Original414,110,018B typed V1 snapshots preserved exact raw
   gzip23,815,514B one-by-one SHA-verified; compact structural V2
   1,988,429B neutralizes only unscanned ty/span/annotation/return_ty,
   NOT full typed metadata equivalence. All509 exact legacy results
   and original per-query node/probe counts checked before/after
   compact V2 roundtrip. Receipt SHA256
   99e893409fd845df0a56c4801fd7a5cdca154b719fca20f3dd6350c24a7c75cf.
   Test-first exact preimage2 red/5 pass, candidate seven new pass;
   existing recursive parameter tests six preexisting stale
   GC-frame symbol regex fails, exact untouched preimage and all six
   baseline/candidate emitted function bodies byte-identical. Candidate
  28 pass/6 fail vs existing baseline21 pass/6 fail. Exact current
   626-module contextual changed cpy_call_lowering capture74 funcs/
   45,786 inst, zero fallback/no libpython edge. Actual same old
   route candidate COMPLETE62.432s/846,622,720B concurrent diagnostic,
   identical assembly, all509 results/124,783 nodes/75,241 hits
   unchanged, probes2,875,180->380,777 (-86.756% actual call count,
   not time). Real exact old/new extracted scanner bodies, same
   fixture path and independent source-matched static native
   binaries, actual py_ast classes and V2 reader, custom Name subclass
   fallback and actual Assign target contract pass both. Streaming
   per-body decode/scan, exclusive five alternating control triples:
   all15 exact509 ordered results; inner4.592118->.799263s
   (-82.595%,5/5,control+2.801%); full CPU7.594873->3.701872s
   (-51.258%,5/5,control+3.064%), wall7.644570->3.747059s
   (-50.984%,5/5,control+3.028%). RSS38,612->39,136KiB
   (+524KiB/+1.357%,0/5 wins, control+324KiB/+0.839%).
   Harness COMPLETE98.012s, raw receipt SHA256
   1c4d5ed00be71a371cd5cd9c48a9f473a53d39eef1349009f441e470aafa4a78.
   Memory gate fails despite strong CPU; no production push.
   Separate approved actual-lifetime owner experiment may preload
   all compact2MB bodies before scanning because real codegen retains
   typed AST graph before method queries (stream fixture artificially
   interleaves decode allocations and scans). Same exact source
   candidate, exact input/output/order, separate RSS mapping
   attribution and five paired/control triples required; disclose
   this failed streaming gate regardless. No cherry-picking or
   blanket Stage2 claim. CI raw free-function validation takes next
   heavy slot; source-only prep until release.

   Separate preloaded rebound owner experiment also DENIED, no
   production. Actual structural compact bodies all156 decoded and
   retained before ordered509 scans, no explicit GC/lifetime-shortening,
   exact same source-matched scanner bodies and fixture path, no
   candidate source changes. Exclusive five triples all15 results
   exact: inner4.291265->.688666s (-83.952%,5/5,control+.075%);
   process CPU7.040099->3.454030s (-50.938%,5/5,control+.539%);
   wall7.089797->3.503689s (-50.581%,5/5,control+.953%).
   RSS median39,084->38,964KiB (-120KiB/-.307%,control+16KiB),
   but only1/5 paired wins: raw A/B/C KiB
   39204/39372/39100,37104/38936/38380,39084/38964/39144,
   38780/38940/39156,39108/39132/37088.
   Paired B−C +272,+556,-180,-216,+2044KiB; normalized
   +.696%,+1.449%,-.460%,-.552%,+5.511%, median+.696%.
   Max unchanged A↔C absolute drift2020KiB; largest normalized
   adverse2044 exceeds by24KiB, candidate max39372 vs base39204.
   Separate read-only smaps3 triples, full /proc/pid/smaps samples
   every20ms during child; group attribution at max sampled total
   (not exact wait4 HWM or same formal run), program map +4KiB
   control0, anon/heap +308KiB control+304KiB, stack0 median;
   raw sampled A/B/C totals38612/39040/38912,
   39000/38920/38976,38300/37280/38648KiB.
   No matched explanation for adverse formal pair; retain failed
   streaming gate above, reject multi-type candidate even with
   strong CPU. Formal receipt SHA256
   a319926dfaf60df24a93c2e3735b5d6d5b9e689fb740ed760208d54a23b057fe;
   mapping receipt SHA256
   2fbb68eb036eb876c40980729c630e206914cebc225a6ad44977f506933936ef.
   Next narrow exact Name-only leaf fast path:29,302 exact Names,
   1,084,174 absent probes (37.7% of total), one exact predicate,
   no persistent state, subclasses/custom fallback and target-store
   special logic unchanged. Test-first, exact actual old output,
   current native strict context, preloaded primary and streaming
   secondary source-matched controls; no stable RSS adverse accepted.
   CI raw context has next heavy slot, source-only preparation.

   Narrow exact Name-only leaf fast path also DENIED; stop this
   owner, no production. Test-first exact preimage1 red/4 pass;
   candidate5 new pass and existing six baseline stale regex failures
   unchanged, all six emitted function body SHA256 match baseline.
   Candidate26 pass/6 fail vs existing baseline21/6. Only one exact
   Name predicate (5 source lines incl comment), no new state.
   Current exact626-module contextual changed module capture74 funcs/
   44,311 inst, zero fallback/no libpython. Same old actual singleton
   COMPLETE62.793s/843,071,488B concurrent diagnostic only, identical
   original57,304,557B assembly; all509/16/124,783/75,241 exact,
   probes2,875,180->1,791,006 (-1,084,174/-37.708% calls only),
   Name absent probes0. Independent source-matched static native
   exact Name/actual Assign/custom Name subclass contracts pass
   both preloaded and stream harnesses. Exclusive 5 triples each,
   all30 exact ordered509 results:
   Preloaded primary inner4.412047->2.815542s (-36.185%,5/5,
   control-.801%); CPU7.285112->5.657720s (-22.339%,5/5,
   control-1.414%); wall-22.552%; RSS39,100->39,016KiB
   (-84KiB/-.215%,control0,3/5), median paired normalized-.368%.
   Raw A/B/C KiB:38760/38756/39256,39112/39016/39160,
   38748/38396/38720,39100/39144/38604,39104/39164/39100.
   Streaming secondary inner4.432806->2.798026s (-36.879%,5/5,
   control+.181%); CPU7.349824->5.566639s (-24.262%,5/5,
   control-.592%); wall-24.285%; RSS38,800->39,168KiB
   (+368KiB/+.948%,control-80KiB/-.206%,0/5 wins),
   median paired normalized+.968%, largest adverse B−C812KiB
   vs max unchanged drift444KiB.
   Raw A/B/C KiB:38700/38804/38444,38800/39168/38356,
   38816/39336/38720,38736/39236/38860,38808/39132/38800.
   Actual independent stable secondary RSS adverse; reject rather
   than cherry-pick favorable preloaded result. Prior broad streaming
   and preloaded failed gates retained above. No more retries of
   this AST owner without a distinct measured memory cause.
   Preloaded formal receipt SHA256
   a6607250b65edb4d8a5df2b36c268f5272b128704e21ab0b420fcc7627f6a932;
   streaming SHA256 cb84d192d9ade40a6e9ff9e8fe8c8df1be9680b7a8e2d563049b608b57dc9581.
   Next actual hosted #638 x64 Stage2 owner unsafe_lowering (after
   completed class_gen), source-only phase attribution first, or
   measured coordinator memory-specific owner; no full bootstrap,
   no relaxed limits or unqualified Stage2 parity. CI ELF contexts
   receive next heavy slot.

   Actual #638 x64 Stage2 source9223aca1 phase attribution (read
   only, partial timeout). Exact bootstrap artifact15155193219:
   class_gen frontend148.631s/backend177.498s (function emit105.496,
   stackmap33.514,verify26.098,layout10.306), completed worker
   codegen326.890s. Child PID13038 observed elapsed1332.777->1681.627s,
   child peak1,799,413,760B, parent979,582,976B,
   partial tree2,778,996,736B (Stage1 peak2,799,013,888B, partial
   not completed acceptance). Next unsafe_lowering PID14420 observed
   elapsed1682.043->1783.604s (only101.561s before outer timeout),
   peak child742,371,328B, tree1,721,954,304B. Actual infer6.351,
   prepare8.373,class65.696,module_init1.866,capture8.899s, render0,
   CFG184 funcs/164,226 inst/45,461 blocks,fallback0. Only about10s
   observed time remained for backend after frontend, so no evidence
   unsafe/render stalled; serial oversized predecessors/deadline
   dominate. Never raise cap/add speculative concurrency.
   Actual coordinator collect_relative_module_closure98.756s plus
   collect_multi_source_relative_closure34.150s (total132.906s)
   before export476.078s. Exact #638/current dependency closure and
   lexical scan owner blobs identical9441a032/30792d5f. Source
   currently calls relative and absolute _package_import_targets
   separately per source, each rereads/normalizes/masks/scans from
   specs, plus _top_level_import_targets. Quantify actual duplicate
   source bytes/counts/call sites first on exact source graph; local
   within-iteration snapshot/spec reuse only if exact source and
   original hooks, preserve relative/absolute root ordering,
   top-level-only flags, source change/error/monkeypatch behavior.
   No persistent source/AST cache; require exact closure order/
   sources, focused native strict context and CPU/RSS controls.
   Stage1 route harness disjoint. CI ELF context heavy first;
   source-only attribution prep until release.

   Stage2 owned selfhost coordinator lexical cache last-use release:
   exact #638 source9223aca1 uninstrumented closure613->625 modules,
   624 siblings, actual ABI validation included, exact order against
   prior attribution. Three pure retained caches D643/T623/I32,
   1,298 entries/1,924 string identities/671 distinct same-content
   pairs, 51,737,771 UTF8 bytes and host getsizeof71,811,192B (not
   native RSS). Exact content manifest SHA66e0187727e1e034387654753c2b0726dcbe86eed9b1977966f8991ab8970140;
   identity rows SHA3354f61d2751bebc3691d8bd93fb83c5709686d76326bf25e7bd02b6b754c26a.
   Release only all three original dictionary objects after last
   init order, owned pcc.__main__ exact realpath, scaffold on/self,
   non-emit/no explicit target/direct indexed route. Generic action
   cache dependency scan may legitimately use caches later, so
   non-direct routes unchanged; worker processes own separate caches.
   Five test-first helper-missing failures->5 passes, current
   source e1a0fc9e uninstrumented graph614->626/625 siblings exact
   SHA060ffe8d685400ae340bb44529bf12d7fe0a6324a63b568ea58804b996ec0af2,
   cache644/624/34->0/0/0 unchanged identities. Strict context
   626-module changed scan59 funcs/24,541 inst, pipeline329/195,168,
   zero fallback/no libpython.
   Actual native lexical transform smoke (12.3KB T +14.3KB I +12B D)
   exact output/cache D3/T1/I1; candidate release0, subsequent
   exact memoized reuse both arms. Prior large380KB transform smoke
   deliberately stopped at30s for peer slot, unqualified; full
   transform replay not used as performance evidence. Separate
   source-matched static native full retained-owner reconstruction
   verifies each1,924 content SHA256,671 distinct same-content pairs,
   actual ordered dictionary key/value aliases before loader returns.
   Baseline/candidate exact archive byte-identical, source provenance
   separately rebuilt/verified; native ELF SHA3647e209/23db87d0.
   Exclusive five alternating A/B/control triples, all15 exact.
   After loader return and actual production clear, baseline vs
   candidate post-release requested53,992,871->2,176,460B
   (-51,816,411), usable58,630,368->2,433,280B (-56,197,088),
   mapped64,233,472->17,997,824B (-46,235,648), RSS67,664->22,572KiB
   (-45,092,5/5, unchanged control0/max8KiB). Full process peak
   67,868->67,664KiB (-204,5/5, control+4/max68). Loader drop
   separately requested85,666B/usable116,896B/RSS0 all arms.
   CPU4.575338->4.594864s (+.427%,2/5, control-.603%; paired
   normalized+.05698s vs max unchanged drift.332109s), wall
   4.659034->4.675822s (+.360%,1/5, control-1.043%; paired
   normalized+.03663s vs max unchanged drift.47926s). No proven
   speed improvement, observed cost within unchanged control.
   Scope is exact retained-owner memory, not original lexical
   phase performance, parent coordinator whole-process RSS, or
   completed Stage2 time/peak. Raw 15 receipt SHA256
   510aa0b3c8c3814588b25afe48f6ce76d96a3cc4ea276d1092a631141c0508fa,
   summary SHA256 acf5b1f106b4486173d1f6ff28a9f59cb70169b69dd1f3234c974170bb5a8525.
   No limits/concurrency relaxed; latest complete fixed point
   still unqualified. Next real lexical duplicate/large-source
   cost attribution and test-first local snapshot reuse, no
   persistent cache or broad route changes.

   Next independent Stage2 lexical pair scan reuse: existing exact old
   source attribution shows single local duplicate attribute mask565
   calls/17.23MB and multi587/17.51MB; single same-flag from-spec
   duplicates565/13.02MB. Large native transform smoke 380,087B source
   had10,878 ordinary lines max320, still scanning at30s (unqualified).
   Test-first local relative/absolute pair only, preserve both original
   file opens and second post-first-resolution read; compare exact raw
   snapshot, reuse masked source and same-flag specs only with original
   hooks, otherwise original calls. Changed source or hooks must force
   fresh scan. Distinct root resolution/order and top-level-only flags,
   unsafe imports and error semantics unchanged; no persistent cache.
   Require exact current graph/order, strict native context and actual
   scoped CPU/RSS paired controls, no full bootstrap/relaxed limits.
   GC diagnostic and Stage1 formal priority; source-only prep.

   Stage2 local lexical pair snapshot reuse (rejected, no production):
   exact current source43b88f74 test-first six intended helper-missing
   reds->49/49 green including adjacent, current actual graph
   614->626/625 siblings byte-identical SHA060ffe8d. Preserve both
   original relative and absolute file opens, raw text equality,
   source mutation/error, original hook flags/order and multi
   accumulator side effects. Local two-string snapshot only, no
   persistent state; downstream lexical readers unchanged.
   Actual non-hook-mutating frame attribution single attribute mask
   1696->1131 (-565), bytes51,909,395->34,612,804; multi1790->1213
   (-577), bytes52,910,552->35,468,659; order579 unchanged. Package
   and from-spec call counts unchanged, slight own source length
   difference. Strict changed module152 funcs/65,556 inst, zero
   fallback/no libpython; pidx SHA c57814e9.
   Full actual native fixture import117 modules default owned-pass
   pool9 mem2reg,sroa MEMORY_LIMIT101.624s/1,624,805,376B (cap
   1,610,612,736); receipt SHA141c27e1. Actual hosted passes-off
   retry TIMEOUT150.312s/1,145,475,072B during self backend, receipt
   SHA905bf9d0. No full import binary, no qualified full route.
   Narrow exact extracted production bodies plus actual scanner,
   pipeline_packages real resolver and driver paths, exact old
   #638 source tree8 files215,870B. Host full production and both
   projections identical ordered26 targets/3975B SHA
   8879290a3ac65e7376beb1091681b921a58fc9aecaa916ce2efa5d5cd4d5af14.
   Exact package target body baseline SHA0c32c2b4/candidate5600f604,
   pair3d9bac38; real resolver never replaced. Projection closure76
   modules first bounded90s TIMEOUT/703,807,488B receipt3759a96d,
   final dedicated150s candidate COMPLETE131.130s/702,566,400B,
   baseline131.917s/665,882,624B, both static native exact source
   archives byte-identical, distinct source provenance; ELF
   b83582b1/0d1a821d. Both native sanity exact host SHA.
   Exclusive5 alternating A/B/control triples, each arm5 fresh
   processes, all75 exact ordered outputs. CPU aggregate
   .684680->.656929s (-4.053%,5/5, unchanged control+.869%;
   paired normalized-5.932%), wall-3.981% (4/5, control+1.031%).
   Peak RSS17,364->17,760KiB (+396/+2.281%,0/5, unchanged
   control0/max12KiB); median process peaks17,360->17,756KiB
   (+396,0/5, unchanged control0/max8KiB). Raw max A/B/C KiB
   17364/17760/17364,17364/17760/17364,17364/17760/17364,
   17364/17756/17376,17364/17780/17364.
   Stable actual scoped RSS adverse beyond control: reject
   snapshot variant despite CPU improvement. No production,
   no repeated same-version retry, no whole Stage2 claim.
   Raw formal receipt SHA256
   5a857090122d3de0882666bcfbe8159ca1cffedcc39d12495adce62fc1e471f4;
   guard SHA d6de58d55edf69ec583e6f2b1974114dae2f794b5c2ef80acdec5f7255ee009a.
   Next distinct no-retention attribution: exact old graph586/625
   source files,15,085,973/17,906,133B lack literal
   except AttributeError. Existing pure mask scans every line even
   if impossible to match. Prove actual call-weighted scope and
   exact text/line endings/identity/hook contract before candidate;
   if no exact safe predicate, reject. No persistent state.

   Independent marker-absent attribute mask attribution:
   exact #638 graph586/625 files,15,085,973/17,906,133B do not
   contain literal except AttributeError. Existing tests already
   cover a return-original shortcut contract, so do not repeat it.
   Distinct exact-str candidate only if measured/proven: direct
   join(splitlines(keepends=True)) when literal marker absent,
   preserving original split/join content and identity semantics
   while skipping every strip/lstrip/indent and second out list.
   Exact custom subclass/duck callbacks and marker-present full
   scanner unchanged. Actual call-weighted source/line count,
   explicit identity/separator/handler tests, strict actual context,
   source-matched native scanner 5 paired controls CPU/RSS required.
   No retained state or broad cache. Stage1 attribution priority;
   source-only prep until direct release.

   Stage2 exact-str marker-absent attribute mask split/join:
   actual current call-weighted closure 614->626 modules/625 siblings,
   exact graph SHA060ffe8d. Single eligible1591/1696 calls,
   43,554,332/51,909,395B and1,009,156 lines; multi1678/1790,
   44,409,464/52,910,552B and1,036,676 lines; order543/579,
   14,681,504/17,492,075B and341,615 lines. Total3812/4065
   calls,102.645/122.312MB,2,387,447 eligible lines; no claim
   these counts equal elapsed-time gain. Five source lines only:
   exact str and literal marker absent means original handler
   cannot match; direct join(splitlines(keepends=True)) preserves
   original list elements, line endings, trailing newline and
   one-line/multiline result identity behavior, skips per-line
   strip/lstrip/indent and second out list. No retained state,
   no return-original shortcut; subclasses/duck and marker-present
   full scanner unchanged. Test-first one intended 128+128
   strip/lstrip failure/16 pass ->61/61 including adjacent,
   explicit all11 separators/dynamic string identity, custom
   hooks/errors, nonliteral spacing and literal prefix variants.
   Actual graph/order byte-identical, same cache counts.
   Strict current scanner59 funcs/24,585 inst zero fallback/no
   libpython, pidx SHA f5cff77d. Exact native scanner fixture
   24 actual old #638 files4,018,552B (18 absent2,910,744B) plus
   44 separator/identity rows. Host base/candidate69 rows exact
   SHAe14b4f96. Baseline native source archive strict explicit
   provenance verification, candidate fresh source-matched archive
   byte-identical, distinct provenance; static native ELF
   180a0aef/3198a19b. Native baseline real24 file rows exact
   host; known baseline native vs CPython 22 one-line/trailing
   identity differences and8 marker-present exotic separator
   content gaps unchanged, all native base/candidate69 rows
   exact SHA c7f6f2e8f33742bf7aab80046e1ccaad2f9d7fcf4be4a50a2789e46f465529c3.
   Separate CI runtime splitlines CR/LF-only correctness owner,
   not broadened or masked by this optimization.
   Exclusive five alternating A/B/control triples all15 exact:
   CPU.481931->.418190s (-13.226%,5/5, control-3.481%;
   paired normalized-12.208%); wall-13.475%5/5 control-2.728%.
   Peak RSS18,712->18,696KiB (-16,5/5, control0/max4KiB,
   paired normalized-.096%). Raw RSS A/B/C KiB:
   18716/18700/18716,18712/18700/18712,18712/18692/18716,
   18716/18696/18712,18712/18692/18712. No whole Stage2 or
   broad CPython semantics qualification, no limit change.
   Formal raw SHA256
   456df134b15a0a4386b6df9d5c80dbe79a5f19d16687fd68522e0239f25f3d10;
   guard SHA256
   0b9def58cc6548ab54b7760c5ca78cdfc35d378905c20c78717c3be50ae4243f.

   Independent Stage2 uncached TYPE_CHECKING negative attribution:
   exact old graph618/625 files lack raw TYPE_CHECKING, but original
   parenthesized import scanner concatenates stripped continuation
   fragments without separators and can synthesize a flag alias.
   Do not assume contiguous marker absence in alias text suffices.
   Stronger exact predicate: exact source and discovery alias text
   strings; raw TYPE_CHECKING absent and literal from typing import
   prefix absent in alias text, so flag_aliases cannot be populated
   and raw alias.TYPE_CHECKING condition cannot match. Keep original
   discovery hook/cache call before predicate, direct original
   split/join identity/separators, custom/source/marker paths intact.
   Actual call-weighted uncached source/alias counts/bytes first
   (existing cache high hits); explicit fragmented alias and custom
   lexer regressions; no persistent state. Strict context and exact
   native scanner CPU/RSS 5-pair controls only if credible.
   CI/Stage1 heavy priority, source-only prep.

   Stage2 uncached TYPE_CHECKING negative lexical shortcut
   (rejected, no production): actual current public9605 calls,
   uncached624, eligible502/624 misses (80.45%, only5.23% public),
   raw12,044,219B/alias8,870,578B/282,113 alias lines. All
   discovery prehit0; actual eligible original second scan0,
   other122 misses only1 second scan338 lines. Exact corrected
   predicate raw TYPE_CHECKING absent AND discovery alias text
   lacks literal from typing import prefix, both exact str.
   Fragmented parenthesized aliases can synthesize flag text,
   so marker absence alone unsafe. Original discovery hook/cache,
   custom callbacks, direct split/join identity and fallback
   retained. Test-first intended256 strip count red+14 pass
   ->81/81+56 subtests, exact current graph SHA060ffe8d,
   strict scanner59 funcs/24,650 inst zero fallback/no libpython.
   Actual cold scanner fixture12 exact old files262,761B
   +25 alias/separator controls, host38 rows exact SHA bac06307,
   native baseline all content/length/cache identity exact host,
   11 known one-line join identity differences unchanged.
   Exact baseline archive strict verified, candidate fresh
   archive byte-identical distinct provenance; static native
   ELF dd9a813a/361ce4e6, all38 native rows exact SHAedb64215.
   Exclusive five alternating A/B/control triples all15 exact:
   CPU.074757->.071370s (-4.531%,4/5, unchanged control
   -3.875%; paired normalized-2.428%, max unchanged drift
   .035013s), wall-4.414%4/5 ctrl-3.857%, RSS median13,312
   unchanged4 ties/one+512KiB within control512. Gain within
   unchanged control, no reliable native effect: reject,
   no expensive retry or production. Raw formal SHA256
   4eefb8435663d8d8c2f261c38c09058fb574c396612e10d9fe6e2b388fc40a80;
   guard SHA e6074855c05c6ee61a9905605c1e54603f93b5ce33a0594ec1ea436062979350.
   User latest all pushes skip CI; no new complete CI while
   any queued/running, no cancellation. Pause low-impact lexical.

   Independent high-priority complementary same-IR backend
   static comparison (source/read-only attribution): Stage1
   owns actual py_obj/allocator/JSON/virtual thread corpus.
   Stage2 owns exact archived Linux post-pass IR container
   list/dict lock/get/set/append and TLS exception/state helpers,
   per-object IR and current runtime source SHA verified.
   Same selected function bodies and globals/types, triple-only
   x64 Linux vs AArch64 Linux default and optimized. Count
   static instruction/code bytes/direct stack accesses/scratch
   moves/branches/calls, not dynamic instruction count or IPC.
   AArch64 Darwin ABI projection optional only admissible exact
   TLS bodies, not actual Mac profile. Audit register allocation
   gates/design independently; no duplicate candidate edits.
   Stage1 script smoke first, GC/CI priority heavy; no CI.

   Claims:
   - Stage2 complementary same-IR container/TLS backend static attribution: Codex, Linux, 2026-11-27T01:41Z, area selfhost-cross-backend
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
