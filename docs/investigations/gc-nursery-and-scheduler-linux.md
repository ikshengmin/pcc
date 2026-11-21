# Linux GC prototype, scheduler and pause measurements

Linux x86-64 measurements from 2026-11-21. The nursery and scheduler results
are in-process C100 handler observations; the C512 measurements below are
separate HTTP diagnostics. No independent macOS acceptance was run.

- [Nursery review branch](https://github.com/ikshengmin/pcc/tree/review/gc-exp2-20261121).
- [Scheduler review branch](https://github.com/ikshengmin/pcc/tree/review/gc-scheduler-20261121).

| Original checkpoint | Review snapshot (identical source tree) |
|---|---|
| `21bbc16c` | [`0fa25b71`](https://github.com/ikshengmin/pcc/commit/0fa25b71ad96190e06b639889b8bdf76cb34a9ea) |
| `35b90c64` | [`dec8fc63`](https://github.com/ikshengmin/pcc/commit/dec8fc635e4e540655c665257d7475af548b274d) |
| `e3d846b4` | [`5a4eda73`](https://github.com/ikshengmin/pcc/commit/5a4eda73847d9501697213767680c7f02c7c64ff) |
| `d3c8fa8c` | [`1c65c535`](https://github.com/ikshengmin/pcc/commit/1c65c5356fbaf9c77dbd0342fdf5683b86fb223e) |

Original commit history and the ELF hashes below can be verified against
the separately delivered Git bundles and native replay kit. No experimental
changes are merged into master.

## Maintainer direction and engineering recommendation

The maintainer set the condition: “达不到就转 GC0 + 偏向引用计数。”
The 90% gate also required no worse longest pause, peak RSS or tail latency.
That instruction governs what to work on after a prototype misses the gate;
it is not proof against count-free collection. Engineering recommendation:
continue measuring the GC0 fallback, and do not call the unified architecture
settled by this experiment. Preserve all five compatibility controls and the
runtime selector.

## Added stock GC3 control

Five interleaved matched sets at both worker counts comprised the exact
frozen binary with GC0 and stock GC3, the final candidate with GC3, plus the
same frozen GC0 binary repeated as an unchanged control. Each process checked
200 warmups and 10,000 timed responses (C100, no injected delay). All 40
completed on Linux x86-64 (CPU affinity 0–5), under the unchanged diagnostic
guard. These are paired medians; the prior table shows ratios of throughput
medians, so the summaries need not have the same values.

| Workers | Stock GC0 median req/s | Stock GC3 median req/s | Candidate GC3 median req/s | Paired candidate / stock GC3 | Candidate wins |
|---|---:|---:|---:|---:|---:|
| 1 | 18,472 | 10,099 | 9,108 | 85.5% | 0/5 |
| 4 | 42,329 | 21,205 | 16,725 | 85.1% | 0/5 |

Paired candidate/GC0 medians were 49.5% and 35.2%; duplicate-GC0 ratios
had medians 93.1% and 98.7%. Worst per-process p99 for stock GC3 vs candidate
was 44.52 vs 50.18 ms at w1, and 8.88 vs 23.31 ms at w4. The respective
maximum recorded collection windows were 34.15 vs 25.32 ms and 22.19 vs
54.57 ms. These counters are not full application-pause measurements.

The stock arm answers how the combined prototype compares with stock GC3,
not what the nursery alone contributed. The prototype combines changes to
the compiler and collector, retains the old-heap walk, admits mostly float
leaves, and exhausted FLOAT pages at w4. A true nursery-only experiment needs
otherwise identical builds, including the same codegen, and a nursery switch
with proof that only the admission/reclamation mechanism changes.

## Same-candidate-ELF admission ablation

A tighter interleaved run used the final measured candidate ELF in both
GC3 candidate arms. The enabled arm retains the exact, sealed native inputs.
The disabled arm adds one inert environment entry, causing the experiment's
exact two-entry environment check to deny admission. The driver required
zero nursery allocations and minor cycles with admission denied, and actual
reclamation with admission enabled. Both arms used the same source and ELF;
stock GC3 and duplicate GC0 were run alongside. All 50 timed processes
completed under the unchanged 90-second / 1-GiB diagnostic guard.

| Workers | Paired enabled / same-ELF disabled throughput | Enabled wins | Paired enabled / stock GC3 throughput |
|---|---:|---:|---:|
| 1 | 87.0% | 0/5 | 87.4% |
| 4 | 85.8% | 0/5 | 81.3% |

This toggles the experimentally admitted window and its dependent codegen
paths as a group, not the allocator alone. The enabled minor still walks
the old heap; this does not evaluate a complete dirty-only design. The
companion replay supplement records the driver, source-independent command
and the 50 raw observations; the main replay kit provides both exact ELFs.

## Experiment 2: final combined prototype

The last measured experiment 2 arm routed proven escaping float values
through the old allocator without adding calls to ordinary allocation.
Its nursery still reclaimed pages, but it never qualified as a
dirty-only collector: its old-heap reference walk remained enabled, and
cross-suspension float publication exhausted the bounded FLOAT pages
on the four-worker handler.

Five alternating Linux x86-64 in-process handler pairs per worker count
(including the identical frozen GC0 control) used the same inputs as the
first prototype below. All 30 processes returned all 10,000 timed
results. The final candidate is original checkpoint `35b90c64`; frozen
GC0 is again from `076fa37e`.

| Workers | GC0 req/s, median | Candidate req/s, median | Ratio | Pairs at 90% |
|---|---:|---:|---:|---:|
| 1 | 16,528 | 8,243 | 49.9% | 0/5 |
| 4 | 41,640 | 17,745 | 42.6% | 0/5 |

Candidate peak RSS was 44.0/50.6 MiB against 22.7 MiB in both
GC0 cases. The worst per-process p99 was 52.29/26.45 ms against
17.77/1.49 ms; the candidate's longest recorded collection window
was 48.50/28.02 ms. The frozen counter reported no event in these
processes, which is not evidence of zero application pause. A separate
qualified-for-admission but unpaired run recorded 189.34 ms with one
worker. The identical frozen control varied substantially, especially
at four workers (paired ratios 0.82–1.31), so this is not evidence of
a small performance delta; all ten candidate pairs were nevertheless
only 36.8–56.2% of their matches, far below the 90% gate.

Reproduce with the native arguments below, using the preserved
candidate ELF SHA-256
`c7474f5003901b6bce8b58477d6d2a9dad2f4bcdbada7c8274446c82fbe285b7`
and the same frozen ELF. Collect raw latencies with `--samples` in
place of `--summary` for each arm. No independent macOS qualification
was run; this is not a claim about all possible tracing collectors.

The tested prototype missed the gate. This is evidence about the measured
prototype only. It is not a demonstration that a nursery with a verified dirty-only
minor, broader object coverage, and no FLOAT page exhaustion cannot meet the gate.
The old terminal-shape experiments and scheduler trials are not mainline changes.

## GC0 ownership follow-up: scheduler transfer trials (2026-11-21)

The native [foreign-last-release check](https://github.com/ikshengmin/pcc/commit/6eca5235d7ee0af466198d172b6033a62ad73e12)
requires last-release finalization while the original owner is still blocked.
The [cycle/weakref check](https://github.com/ikshengmin/pcc/commit/d5dc5ba5d99e3097841c432a7c237adaad05c604)
requires both callbacks before explicit collection returns. The latter was
run on all five backends against CPython. These tests do not enable a new
collector. A [separate float ownership correction](https://github.com/ikshengmin/pcc/commit/8fe76edcacc5ab17c95004d6dd9d342417b59ba9)
made an annotated object local consume its fresh float allocation instead of
retaining the same value a second time.

The Linux in-process GC0 handler log recorded 39,027 virtual-thread
incref/decref events out of 168,431 at four carriers. Of 1,201 virtual-thread
frees, 626 were logged on a different carrier from allocation. This is a
reference-event census, not time attribution or proof of confinement.

Two temporary trials transferred the detached ready node's existing owner to
the running claim, preserving the queue and recycle protocols. Native tests
checked a sole queue owner survives the transfer with count 1 and is freed
once; four-carrier spawn/join also passed. Each performance screen used five
alternating same-input Linux handler pairs at each width with a duplicate
stock arm, 200 warmups and 10,000 validated timed responses per process.

| Trial | w1 median throughput vs stock; wins | w4 median throughput vs stock; wins |
|---|---:|---:|
| Helper, `e3d846b` | 95.9%; 2/5 | 95.6%; 1/5 |
| Inline, `d3c8fa8` | 94.4%; 1/5 | 106.6%; 2/5 |

Peak RSS was unchanged. The inline trial also worsened w1 worst
per-process p99 (14.92 vs 4.64 ms). Its w4 median looked higher, but it
lost three matched pairs and the unchanged duplicate stock ratios
spanned 0.86–1.20. Neither version showed a stable improvement; do not
merge them. These temporary screens do not claim an HTTP or macOS result.
The exact frozen ELF SHA-256 was
`a479a90d7a61a01cf5378d4163ca846bd53860c4f1041f7d6fc04ab746d8f48b`.
Use its corresponding native handler with `PCC_GC_BACKEND=0` and
`100 0 100 --samples 1` or `100 0 100 --samples 4` in each arm.

## GC migration experiment 2: first measurable nursery (2026-11-21)

The temporary Linux x86-64 prototype combines the experiment 1 GC3
code-generation changes with a leaf bump nursery. It actually reclaims
pages, but still performs an independent full scan of old strong slots.
This receipt is an early performance screen, not a dirty-only collector,
an HTTP result, a macOS result, or permission to remove any backend.

Five alternating same-input pairs per worker count (plus an identical
GC0 control arm) used the native in-process handler: concurrency 100,
zero injected delay, 200 warmups and 10,000 validated timed requests per
process. The frozen GC0 input was compiled from `076fa37e`; the test arm
was the original GC3 experiment checkpoint `21bbc16c`.

| Workers | Median GC0 req/s | Median GC3 candidate req/s | Ratio | Pairs at 90% |
|---|---:|---:|---:|---:|
| 1 | 17,631 | 8,622 | 48.9% | 0/5 |
| 4 | 43,433 | 18,639 | 42.9% | 0/5 |

Peak process RSS was about 22.7 MiB for GC0 and 43.5/51.7 MiB for
the candidate (one/four workers). The worst per-process request p99
was 8.23/1.31 ms for GC0 and 37.87/26.12 ms for the candidate. The
candidate's longest recorded GC window was 27.1/27.8 ms; frozen GC0's
counter did not report an event, which is not evidence of zero latency
or a directly equivalent GC window.

With the preserved native artifacts, run the frozen and candidate
handler with their respective `PCC_GC_BACKEND` set to 0 and 3, passing
`100 0 100 --summary 1` or `100 0 100 --summary 4`. Retain the raw
latencies, exact process peak RSS, and both runtime and nursery
pause counters, and reverse arm order each pair. SHA-256: frozen ELF
`6456816ca5dca1f4bdcf7c79107c50d892b4d4ae7467950eef2723440a212530`;
candidate ELF
`602cadeb510074036421dcb0264833bc2cd8c0ee406cedd75cf62be9fcc6c139`.
The experiment code is in the review branch above, not in master. The ELF
files supplied separately are the measured artifacts; source snapshot alone is
not a bit-identical build recipe.

## Linux C512: new GC3 collection phase measurement (2026-11-21)

A diagnostic rebuild of the same native HTTP program (PCC `076fa37e`,
gateway `3f91c0e5`) added exclusive collection-window clocks and read
them at shutdown. With GC3 and four carriers, the exact loopback
zrk 3.0.0 C50 warmup and C512 measurement commands below returned
28,182/28,182 successful main requests, zero client errors. The old
gateway still exited 1 when draining after SIGTERM; this does not
resolve issue #4 or measure an uninstrumented performance arm.

| Exclusive phase | All 96 windows (µs) | The same longest window (µs) |
|---|---:|---:|
| Marking and unseparated mark work | 926,667 | 7,648 |
| Heap lookup of pin owners | 788,435 | 7,471 |
| Clear/reclaim | 763,070 | 77,414 |
| Finalizer-target stage | 377,131 | 17,285 |
| Capture/sort stack roots | 34,014 | 368 |
| Registered roots | 26,079 | 365 |
| Other | 2,306 | 25 |
| **Total** | **2,917,702** | **110,576** |

Both columns agree with counters 119–121 under this build's old
1 µs floor; the archived vector does not identify same-tick windows.
Clear/reclaim accounted for 70% of the longest event. The clocks start after the
initial stop rendezvous; no temporary callback-release interval
was measured in this diagnostic. The historical 2,714,738 µs total
and 122,783 µs max did not carry phase data and cannot be backfilled.

Use the instrumented gateway ELF and `PCC_GC_BACKEND=3` with the
load commands below; capture gateway stdout on shutdown and validate
it using the parser at the recorded revision. The historical 32-slot report
uses earlier labels and the old same-tick convention; do not feed it to the
corrected parser. For new runs see
[`pcc_perf.py pause prepare`](../developer-reference.md#diagnose-a-tracing-collectors-pause-phases)
and use `python scripts/pcc_perf.py pause report server.stdout` with the
canonical 33-slot rows.
The verified report SHA-256 is
`9e6265f56f9b95a875f914bef1eaf824a9e95a578e25aa318d3509215042ef38`.

## Linux C512 request timeout: intermittent, investigation paused (2026-11-21)

[PCC issue #4](https://github.com/ikshengmin/pcc/issues/4) remains open and
marked intermittent. The original frozen GC3 preflight (ELF `27e64792fdbd…`)
observed 510 HTTP 2xx and 508 timeouts. The later reconstruction used PCC
`076fa37e` and gateway `3f91c0e5`, not a byte-identical copy of that original
ELF. On the reconstructed stock server the in-flight timeouts did not recur
in five GC3 runs. No stalled-thread trace was captured; do not interpret
0/5 as fixed. No further historical timeout replay is planned.
The separate stock shutdown race produced drain exit 1 in four of five GC3
runs and two of three GC0 control runs. The gateway fix is
[`b1490486`](https://github.com/ikshengmin/pcc-gateway/commit/b1490486a993c9528dcbfeecf499daba18ae0ef1);
it does not resolve or close the historical request-timeout issue.

The GC0 control reused the exact reconstructed stock ELF measured with GC3
(SHA-256 `da0b9f19bfdb1179b219583e39d6081c2347212e2df78c3d296b2afb9c22c0d7`).
The C512 main runs returned 62,941 / 62,957 / 59,087 HTTP 2xx with zero
client errors. Process-wide STW counters (count / total µs / max µs) were
2 / 4,943 / 2,474; 4 / 10,841 / 4,117; and 3 / 9,737 / 4,091.
The previous [five GC3 runs and counters](https://github.com/ikshengmin/pcc/issues/4#issuecomment-7045802751)
on this ELF had median 29,192 HTTP 2xx versus 62,941 on GC0 (about 46%).
These separate, non-interleaved batches with stock drain failures are
diagnostics, not a five-pair performance A/B. Do not use the earlier
30% ratio across different artifacts. Counters 119–121 do not attribute
the 2,714,738 µs total or the 122,783 µs maximum to a collection phase.

Historical workload command, for reference only. The ELF is the pinned
native gateway GET `/` benchmark with four carriers; use official zrk 3.0.0.
Keep the same running server for warmup and measurement, then send
`SIGTERM`, record its exit, and capture process counters 119–121 if the
server has the shutdown reporting hook.

```sh
PCC_GC_BACKEND=0 "$GATEWAY_ELF" 18080 & # GC3 selects PCC_GC_BACKEND=3
server_pid=$!
# Wait for the loopback listener before warmup.
zrk --plain --closed -t 3 --disable-keepalive --connections 50 \
    --duration 5s --method GET --timeout 8s --format json \
    --output warmup.json http://127.0.0.1:18080/
zrk --plain --closed -t 3 --disable-keepalive --connections 512 \
    --duration 15s --method GET --timeout 8s --format json \
    --output main.json http://127.0.0.1:18080/
kill -TERM "$server_pid"
wait "$server_pid"
```
