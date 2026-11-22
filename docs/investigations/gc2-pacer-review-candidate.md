# GC2 heap-goal review candidate

This branch proposes a GC2-only policy behind `PCC_GC2_PACER_PERCENT`
(0–10,000, unset disables). An explicit positive
`PCC_GC_DEBT_THRESHOLD` preserves the stock path. The absolute heap goal
uses last marked live bytes, a percentage and the 4 MiB growth floor.
The trigger reserves one quarter of growth for concurrent work. Pressure
comes from existing batched managed-byte telemetry, not work debt.
Discharging mark work therefore does not lower measured heap occupancy.
This approximation may lag on short-lived threads or allocation bursts,
does not measure roots separately, and is not the Go adaptive controller.

Assist is admitted only during an enabled mark. Work grows with a
batch and the pressure between trigger and goal, with a 512-unit cap.
The existing mark credit and sweep handoff stay in place. The candidate
does not create extra CMS workers, change any other collector's policy,
or become active on master.

Before a performance run: use an identical, unchanged control; the current
`pcc_perf` subprocess environment deliberately drops ambient variables.
Do not assume setting the candidate's opt-in in the shell reaches a
benchmark child. Use a tested sealed-arm opt-in and report the effective
mode before five-pair A/B. A native assist inside a controlled stop is
not evidence for concurrent workload speed or maximum pause.

Linux pcc0 focused validation: three native test nodes (candidate gate
and real assist under a controlled stopped world, stock debt arithmetic,
and stock one/four-mutator telemetry), plus 96 targeted scheduler, policy
and cross-object ABI checks, passed. The candidate pressure case sets
occupancy at the goal with a real active mark, issues one explicit
allocation batch and observes an assist plus a decrease in remaining
work debt, while occupancy stays unchanged. It uses raw counters:
`pcc_gc_telemetry` flushes the caller's pending allocation batch and
could otherwise create the assist being measured. The disabled, default,
positive-override and non-GC2 cases do not activate the candidate.

The wider runtime signature chunk check is already red on the unchanged
master preimage: 51 ordinary runtime signatures in a tranche whose bound
is 50. The new cross-object ABI entry's chunk and order checks pass.
No official A/B, macOS run, or pcc1 rebuild was performed.

## Real allocations, opt-in and stock in the same ELF

With no debt-threshold override, the next owned native check ran two
fresh processes for each of one/four mutators and off/on modes. Each
allocated 240,000 temporary lists, preserved the same four registered
roots, attested the effective mode, and reached automatic sweep. The
ordinary gate can drain the pending mark after join; record workload
STWs separately from that drain. No profiling read enters the timed
allocation loops.

| Mutators | Mode | Assist entries | Workload / total STW events | Max recorded STW (µs) |
|---|---|---|---|---|
| 1 | stock | 0 / 0 | 2/2, 2/2 | 18714 / 15075 |
| 1 | opt-in 100 | 0 / 0 | 2/2, 2/2 | 25222 / 10537 |
| 4 | stock | 0 / 0 | 0/1, 0/1 | 42094 / 38603 |
| 4 | opt-in 100 | 2 / 19 | 3/3, 4/4 | 29877 / 30269 |

This demonstrates unforced entry into the candidate's assist path at
four mutators, not that every assist did work or that one arm is faster.
At four mutators stock max includes the post-join drain and opt-in max
does not; comparing those as a pause improvement would be invalid.
Neither this diagnostic nor the controlled stopped assist qualifies A/B.

## Qualified tool identity and macOS commands

The review branch includes the byte-identical pcc_perf tool and tests
from master `8330c7d`. The native attestation linked against this review
runtime and returned 100 / -1 / -1. No handler arm was built on Linux:
the matching gateway source is not available in that checkout.

From a clean authorized checkout of `review/gc2-pacer-20261122` on macOS,
with the benchmark's fixed gateway package available, use:

```sh
uv run python scripts/pcc_perf.py arm gc2-stock-same-source
uv run python scripts/pcc_perf.py arm gc2-goal-100 --gc2-pacer-percent 100
uv run python scripts/pcc_perf.py ab gc2-stock-same-source gc2-goal-100 --pairs 5 --gc 0,2 --workers 1,4 --control 0
uv run python scripts/pcc_perf.py table gc2-goal-100
```

Keep the same gateway source, process and memory guards. Build both from
this same branch so the intended difference is the sealed parameter;
do not set an ambient threshold override. Report maximum pause, peak
RSS/tail latency and all five pairs, not just aggregate throughput.
If either arm fails native qualification, preserve that receipt and
stop the comparison.

The current screen reports total stopped time, not per-pair maximum
pause, RSS and p99. Those fields need to be recorded by the next
measurement-tool slice before treating this command as the merge gate.
