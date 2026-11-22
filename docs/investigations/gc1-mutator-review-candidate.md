# Worker-free GC1: first Linux candidate, not merged

Base: master `531e9ec8d4fc0edc274799a40944936549cca394`.
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

Next Linux gate: check GC1 stop/repair under paused and reentered
collection, including a terminal weakref/finalizer on the actual
automatic sweep path. Next measurement, from an authorized
macOS checkout with the same gateway source: seal base and review HEAD
under the same `PCC_PERF_HOME` from their separate worktrees, then run:

```sh
# At base 531e9ec8:
uv run python scripts/pcc_perf.py arm gc1-stock-531e9ec8
# At review/gc1-mutator-20261122:
uv run python scripts/pcc_perf.py arm gc1-mutator-review
uv run python scripts/pcc_perf.py ab gc1-stock-531e9ec8 gc1-mutator-review --pairs 5 --gc 0,1 --workers 1,4 --control 0
uv run python scripts/pcc_perf.py table gc1-mutator-review
```

Keep the same gateway inputs and existing memory limits. Report maximum
pause separately from throughput. Do not merge before the controls and
native multi-mutator checks qualify this candidate.
