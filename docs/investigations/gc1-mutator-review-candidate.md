# Worker-free GC1: first Linux candidate, not merged

Runtime base: master `531e9ec8d4fc0edc274799a40944936549cca394`.
For the performance baseline, use master `98a932017f494dcbdfd996e9ca614dc8c0d8f153`:
it has the same compiler/runtime and benchmark sources as the runtime
base, with the same tested receipt tool now on this branch.
This branch changes only the proposed GC1 scheduling policy. GC1 does
not start a CMS worker; allocation publishes debt without tracing
from inside an allocation; an already-rooted application gate owns one
incremental slice and its eventual sweep. GC2–4 retain their background
worker and allocation-assist policy. A dedicated GC1 gate claim is needed:
using the existing GC4 auto-step flag made the native zero-worker test
fail with no completed mark because that flag excludes regular mark
seeding while the world is running.

On Linux pcc0, seven native cases passed with a source-matched owned
archive; the automatic GC1 program passed with zero worker starts and no
concurrent allocation assists, made no explicit collection call, and
observed mark commit, candidate sweep and survival of a registered root.
The program also checks that disabled auto collection does not commit a mark,
then re-enables it and checks progress. The five startup/lifetime cases
and the five-collector native weakref+finalizer program passed. The
scheduler/CPython reference set passed 88 host checks.
The focused four-mutator test then passed in three fresh processes. Each
process completed 24,000 allocations, kept all four distinct roots,
started no worker and reported 7/6/7 mark commits. Maximum recorded
collection STW was 2,961/3,371/3,414 microseconds in this synthetic
diagnostic. This is not an A/B maximum-pause result, a no-loss control,
macOS behavior, or pcc1. No production/master runtime has changed.

The automatic-sweep finalizer checks passed in two Linux pcc0 native
processes. A producer thread exits after creating a terminal cycle.
Allocation while GC is disabled leaves both callbacks unrun; after
reenabling, allocation alone delivers the weakref callback and then
the finalizer exactly once. A separate native mode reenters
`gc.collect()` only from inside the callback and does not redeliver
or prematurely run the finalizer. CPython matches the ordinary
non-reentrant case; its reentrant semantics are not assumed.

Next measurement, from an authorized
macOS checkout with the same gateway source: seal base and review HEAD
under the same `PCC_PERF_HOME` from their separate worktrees, then run:

```sh
# At master 98a93201 in a separate clean worktree:
uv run python scripts/pcc_perf.py arm gc1-stock-98a93201
# At review/gc1-mutator-20261122:
uv run python scripts/pcc_perf.py arm gc1-mutator-review
uv run python scripts/pcc_perf.py ab gc1-stock-98a93201 gc1-mutator-review --pairs 5 --gc 0,1 --workers 1,4 --control 0 --latency-receipts
uv run python scripts/pcc_perf.py table gc1-mutator-review
```

Use the same `PCC_PERF_HOME`, gateway package and existing memory limits
for both worktrees. The five paired raw QPS, maximum collection STW,
process peak RSS and timed p99 values are in `run_receipts` in
`ledger.jsonl` under that home. CPU/RSS include output formatting.
Report maximum pause separately from throughput. Do not merge before the controls and
native multi-mutator checks qualify this candidate.
