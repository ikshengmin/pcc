# GC2 stock pacing arithmetic

The current threaded GC2 is on the shared tracing scheduler. Its start
threshold is the greater of 4 MiB and the last marked live bytes multiplied
by (pause−100)/100; an explicit positive override takes precedence.
This is allocation **debt** above the last completion, not an absolute
current heap goal. The separate step multiplier turns debt into work;
only completed marking work pays down allocated bytes.

The isolated Linux pcc0 test stops the actual GC2 world with automatic
GC disabled, supplies fixed last-mark and allocation snapshots, and calls
the production routines for threshold, budget, and payment. It covers
minimum threshold, a growing live-set target, fractional rounding,
positive override, budget minimum and no-credit zero work. No new
pacer was installed. The following native diagnostic uses one frozen input and reports raw
counters, not a throughput or latency comparison.

## Actual one- and four-mutator workload

The owned Linux pcc0 harness allocates exactly 48,000 temporary lists per
process, partitioned over one or four actual registered threads. All four
existing roots survive in both cases. Threshold override: 131,072 bytes.
Each of three fresh processes per worker count uses the ordinary automatic
collection gate, with no explicit gc.collect call.

| Mutators | Assists | CMS traces | STW events | Max STW (microseconds) |
|---|---|---|---|---|
| 1 | 0 / 0 / 0 | 65 / 46 / 48 | 13 / 9 / 10 | 739 / 706 / 828 |
| 4 | 0 / 0 / 1 | 22664 / 23744 / 21852 | 1 / 2 / 1 | 738 / 6199 / 2789 |

All processes observed one CMS startup and an automatic candidate sweep.
A first attempt that required a sweep before join sometimes found none at
four mutators even though the CMS worker had traced objects. The harness now
records workload STWs separately and permits a bounded 500 ms ordinary
post-join poll to receive a pending automatic sweep. The final runs needed
no post-join sweep to meet the criterion. These scheduler-dependent rows do
not qualify the assist mechanism under heavy pressure or indicate a
scaling/latency gain. No pacer or runtime behavior changed.
