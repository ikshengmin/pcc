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

## Isolated heap-goal and assist policy

The [Go GC guide](https://go.dev/doc/gc-guide#GOGC) separates the total
heap goal from work done during marking. The current Go goal includes GC
roots in the growth allowance; this first PCC candidate deliberately uses
only the last marked live heap, because a reliable separate root-size input
is not yet part of this scheduler. It is not a port of Go's adaptive pacer.

The model uses a goal of live plus the larger of 4 MiB or the configured
percentage of live, with checked/saturating arithmetic. It starts before
the goal by reserving one quarter of growth as marking headroom. Assist
requires an active mark and a nonempty allocation batch, scales with the
batch and pressure from trigger to goal, reaches 2× the batch-derived
work at the goal, and caps a single assist at 512 work units. The floor,
headroom fraction and scale are test parameters, not measured choices.
An explicit threshold override must keep the existing diagnostic path.

Seven host cases cover the policy, including saturation and zero work
when inactive. Next, a review-only implementation needs separate
decision inputs for heap progress and remaining mark debt: subtracting
mark-work credit must never move the heap backward. An assist-pressure
harness must demonstrate actual assist calls and progress, not just
an arithmetic change. Dedicated-worker scaling and A/B are separate.

## Measurement identity

`pcc_perf arm --gc2-pacer-percent N` seals the knob, rather than relying
on a shell environment variable that the runner intentionally discards.
Before any opted-in arm qualifies, it compiles a tiny program against
the same owned runtime archive and verifies on=N, off=-1, and GC0=-1.
Ordinary launches pass the knob only to GC2. A missing/mismatched
attestation or changed binary rejects the arm, and GC2 results name each
arm's sealed percentage. Stock arms need no new runtime symbol.

The exact tool probe ran against the Linux review archive and reported
100 / -1 / -1; the pcc_perf source command suite passed 74 tests. No
gateway arm or performance A/B ran in this Linux workspace.

For a merge-gate comparison, add `--latency-receipts` to the five-pair
`ab` command. It asks each owned handler for the timed request samples,
checks the expected request count, computes p99 on the host, and keeps
each pair's QPS, runtime maximum collection STW and process peak RSS in
`run_receipts` in the ledger (including a duplicate control arm, if
used). CPU and RSS include sample serialization; elapsed QPS and p99
are from the timed request window. Maximum STW is a runtime counter,
not an end-to-end request pause. Linux wait4 RSS is normalized from KiB
and macOS RSS is already bytes. Missing measurements fail receipt mode.
The pcc_perf focused suite passed 79 checks, including a real local
wait4 child; no gateway A/B or macOS execution was performed.

The pre-existing runtime ABI bound failure (51 entries in part 16)
was reproduced on master and fixed there by moving only its final entry,
`py_obj_call_sync`, to the beginning of the next chunk. The flattened
ordered sequence of all 1,202 signature AST pairs is unchanged. The focused
ABI chunk and declaration/IR suite passed 10 checks; full native compiler
bootstrap and CI were not run for this partition-only change.

### Admission and ownership before adding parallel marker workers

Stock GC2 does not have a worker pool: the CMS start/stop state and handle
in `freestanding_gc_public_collection.py` own one worker. The generic
`pcc_gc_tracing_step_cycle` takes the global graph lock before advancing
the shared fallback cursor, claiming gray work, or modifying the shared
trace stack. The owner-stack guard is thread-local, but the underlying
stack count and buffer are not. C-extension scanning has a distinct
unlock/relock handoff with one pending owner, backend, and epoch.

The focused owned-C native case parks a registered helper behind the
graph lock while the main thread publishes one real gray list with
automatic collection disabled. The helper makes no progress while the
lock is held and processes the node after release; the registered root
keeps its identity. This isolates stock admission, not scheduler speed,
fairness, or the duration of a scan. It does not measure four marker
workers on the production workload.

A parallel-scan candidate needs a single coordinator for cycle start,
root seeding, C-extension handoffs, finish and sweep; per-helper scratch;
and a bounded pool of whole-owner claims with epoch validation. Keep
objects stable and retain the supported container slot protocol when
scanning outside the graph lock. Termination must account for private
producer tails and in-flight claims as well as the visible queue before
final remark. Adding callers to the current mark slice does not create
parallel scans for its lock-held paths. No pool size, speedup, or
collector selection follows from this probe.

The test-only claim model describes the next candidate's linearization
points: reserve credit before making work visible; transfer credit to
an in-flight claim on dequeue; reserve child credit before retiring the
parent. A token includes the epoch and a per-claim serial, including when
the same worker retries the same object. A failed scan requeues the
owner. An empty visible queue cannot request final remark while there
are unpublished reservations or in-flight claims. Zero outstanding
credit only requests stopped-world remark, which must check for late
roots and work again before sweep. Four model cases cover both completion
orders, capacity, duplicate shading, late work, retry and stale results.
The model does not establish memory ordering, object stability, lock
ordering, or runtime performance.

A separate Linux owned-C test initializes stock GC2's count-free list
container mode (observe it after first allocation) and chooses two real
rooted built-in lists on distinct lock stripes. Two registered threads
each take the same list lock used for mutations and hold inside the
strong-slot visitor. Both callbacks overlap while a third thread owns
the global graph lock. Three subprocesses passed with exactly the
expected child in each slot. This uses the real slot visitor without the
graph-locked mark callback. Same-stripe owners may still serialize.
No concurrent writer, collection, mark publication, extra runtime
worker, or A/B was exercised by this test.

The following owned-C guard runs with automatic GC disabled and stages
a controlled active mark on stock GC2. The locked slot visitor sees a
pre-existing child in two slots and uses the actual atomic shade API:
one gray credit and one queue entry, not two. A second registered
thread attempts a public append to the same list while the visitor owns
its container lock. The visitor does not see the late child; after
unlock the ordinary append barrier shades it. Both producer batches
then expose exactly the two expected children. Three fresh Linux
processes passed. This is a mark-publication test, not full-cycle
termination or a throughput claim. The atomic shade path can allocate
a gray batch; an integration candidate must qualify bounded failure
handling and must not assume that captured slots remain valid
after container unlock.
