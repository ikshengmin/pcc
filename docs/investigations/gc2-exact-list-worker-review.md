# GC2 short exact-list mark kernel (review branch only)

This candidate is separate from the GC2 pacer comparison branch. It
adds one test-invoked native entry and does not alter ordinary dispatch,
worker count, scheduling, mark ownership, or other collectors' hot paths.

The caller passes an unchanged traced root slot for the duration of the
call, not an unrooted object address. The kernel takes the same
container lock used by built-in list writers and checks the active
epoch while holding that lock. It admits at most 32 positions on an
exact built-in list. The callback uses stable membership and the
existing atomic shade/to-gray publication path. It does not hold the
global graph lock, save raw slots, mutate the owner color, or retire its
gray credit. It returns zero on fallback, including stale/inactive marks,
graph-held callers, unsupported layouts, and failed stable child
membership. A fallback may leave already shaded children gray; their
credits and published work are preserved, and the caller must still
trace the owner with the ordinary path.

This is not an integration entry with a claim of its own. A future
coordinator must provide owner lifetime and per-claim identity, prevent
finish while the scan or producer batch is outstanding, and revalidate
the epoch before retiring any successful work. Existing atomic shade is
allowed to allocate a token batch; its fallback is existing gray-credit
accounting plus the cursor walk, not loss of a marked child. The test
does not inject allocation failure. The small length limit is a review
boundary, not a policy or a measured pause guarantee.

Validation command, with a source-matched owned archive:
`PCC_RUNTIME_ARCHIVE=<archive> uv run pytest -m integration tests/python/test_gc2_short_list_review_native.py`.
The controlled mark fixture checks the original child in two slots
produces one gray credit and a later public append supplies the other.
It also checks rejection while inactive, with the graph lock held,
with the wrong epoch, for a tuple, and for an oversized list. The
other four GC backends are controls. No bootstrap, full suite,
QPS, or macOS execution was attempted on this branch.

Linux x86-64: the owned archive built under the resource guard; three
fresh GC2 subprocesses and GC0/1/3/4 negative controls passed. The ABI
chunk suite passed eight host cases. This does not cover final remark,
full-cycle liveness, helper abort, C extensions, or measured scaling.

## One dormant in-flight task

The test coordinator tracks one already-dequeued, exact gray owner
under the graph lock. It accepts only an explicitly rooted owner and
assigns a monotonically increasing task token. Its readiness predicate
cannot request final remark while a task is in flight or gray credit
is outstanding; it does not itself request remark or change the
production finish path. A matching successful close keeps the scanner's
already flushed child work and retires the original gray credit. A
matching abandon publishes the still-gray owner back to the worklist.
A stale close cannot release either task credit or identity.

The owned native test uses two real stopped cuts on a worker: after the
dequeue but before the container lock, and after it unlocks and flushes
but before it retires the task. Normal retire and same-epoch abandon
are both exercised. The fixture controls the current epoch and keeps
the registered owner slot stable. This initial unit does not handle a thread
dying mid-task, cross-epoch reset, coordinator restart, multiple
in-flight tasks, or integration with the existing concurrent drain
or stopped-world finish. It makes no throughput claim and does not
select a second marker worker.

Linux x86-64: three normal retirements and three abandons passed both
native stop cuts. A stopped negative control temporarily sets gray
credit to zero and still cannot request remark with a live token;
the real count is restored before resume. No GC2 source used by
ordinary mark or finish calls the review APIs. The prior short-list
test and eight ABI chunk checks passed on this revision.

## Stopped cross-epoch reclamation of a review slot

Only the stopped-world owner holding the graph lock may discard a
matched claim from an older epoch. This clears the review slot without
touching the new cycle's credit or tokens; it cannot discard a current
claim. The owner slot remains rooted until the original worker returns.
After the old helper has published child work and parked, the native
fixture advances the actual epoch under a stopped world, clears the
old worklist, and reclaims the review slot. The new cycle claims the
same list with a different token. The prior worker's real late return
cannot retire it or mutate its credit. Old epoch coloring and roots in
the fixture are limited to the isolated test graph; the ordinary
scheduler does not call this helper.

Linux x86-64: three fresh processes per path (retire, same-epoch
abandon, cross-epoch reset), the prior short-list checks, and eight
ABI tests passed. The reset fixture uses the real page-bitmap rotation
and worklist clear; changing only header color would leave the old
bitmap bit set and suppress re-shading. No ordinary scheduler, macOS,
or A/B ran.

## Default-off bridge in the automatic marker

This separate review branch wires the exact-list trial at the front
of the actual GC2 mark slice, before starting the shared trace stack.
The default is off; an owned test sets the selector only at startup.
No option or environment variable exposes it in user builds, no
additional worker is created, and master does not carry the branch.
The disabled build is a correctness control, not an untouched
performance control.

Before dropping the graph lock the marker enters a no-park region;
it never carries the graph-owned trace stack or fallback cursor
across unlock. One dequeued exact-list task can scan under its
container lock and publish with the existing atomic protocol. The
marker reacquires the graph lock and closes the task before leaving
no-park, so a real stopped-world finish cannot bisect the handoff.
A layout/index miss uses the ordinary visitor on the same task and
cannot loop through the shortcut on requeue. Other tokens use the
ordinary loop; a stock fallback walker that already blackened the
same owner cannot cause a second gray debit. A marked but non-gray
object is not accepted into the bridge.

This first bridge returns after one trial per slice. It is a liveness
and work-accounting prototype, not an effective parallel pool or a
throughput candidate. Before any worker expansion, review the
no-park/graph-lock order and finish admission, verify forced fallback
and allocation failure, and test whether the extra polls dominate.

Linux x86-64, source-matched owned ELF: twelve fresh automatic runs,
one/four allocating mutators × test selector off/on × three. Every run
finished marking, swept, and preserved the registered owners and their
children. Off reported no bridge work; on reported 34–81 shortcut
visits and 10–24 ordinary fallbacks with one mutator, and 9,630–43,661
visits and 2–4 fallbacks with four mutators. Every four-mutator run
completed one ordinary mark; one-mutator runs completed four to
twelve. The two earlier focused native suites also passed on this
archive. These are coverage counters, not sampled timing, a guarantee
of progress for arbitrary programs, or measured speedup.

A deterministic owned probe feeds one actual mark step a queued exact
list of 32 slots, a 33-slot list, or a tuple. With the bridge on, the
32-slot shape takes the shortcut exactly once, the 33-slot shape takes
ordinary fallback exactly once, and the tuple bypasses the shortcut.
The disabled control registers none of those events. All six combinations
blacken the original owner, debit its credit once, and enqueue exactly
one child, even with repeated incoming slots. Three fresh processes per
combination passed on Linux; no automatic finish or speed claim is
added by this controlled single slice.

## Contested owner: retirement is not task closure

A second controlled native probe forces the runtime's fallback latch
(not an injected malloc failure). One thread holds the owner's list
lock while the alternate marker dequeues that owner and drops the graph
lock. A third thread takes the graph and runs the real ordinary fallback
cursor on the same object before the alternate can retire it. Only then
does the test release the list lock. In the original bridge both calls
returned one completed owner even though its gray credit went down only
once and the child was queued once. Both callers can spend returned
progress against shared allocation debt, so that is a real double
credit, not a memory-liveness failure.

The close operation now distinguishes matching closure without gray
debit from actual retirement. The bridge returns completed work only
for the latter. It does not infer the debit from a separate color read:
a concurrent producer can re-shade a black object between observations.
Both paths may still visit the object in this forced interleave; this
change does not claim to eliminate that duplicate scanning cost.

The previous owned archive failed with `ordinary=1 alternate=1` and
the new archive passed all three fresh processes with
`ordinary=1 alternate=0`, still one child token and one remaining
gray credit. Four other focused native suites (automatic finish,
forced width, stopped claim gaps, short-list supported layout) also
passed using the exact rebuilt archive.

## Reserve ownership in the bounded cursor pass

The owned test selects mode 2 to add a second experiment: while holding
the graph lock, the ordinary cursor will advance past the single
reserved owner without scanning it or debiting its gray credit. This
still costs one unit of the slice's bounded search allowance. It does
not mutate the reservation, and the pending owner's credit continues
to prevent an empty-queue handoff. With mode 1, the earlier duplicate-
credit defense remains directly testable. Mode 0 remains the bridge-
off correctness control. All selectors are native fixture-only.

The deterministic contention probe expects mode 1's ordinary scanner
to retire once and its alternate scanner to return zero. It expects
mode 2 to exchange those results, still leaving one gray child and
one child token. This check covers the ordinary cursor pass, not the
separate depth-first trace stack or every producer handoff.

The previous binary returned one cursor retirement for both selector
values. The new source-matched Linux binary passed 3 forced interleaves
per value, and the automatic liveness harness passed 18 executions
(1/4 allocating mutators, modes 0/1/2, three repeats). The other three
owned probe nodes passed; no wall-time comparison was attempted.

## Concurrent depth-first stack respects the reservation

The cursor-only preimage still followed another owner's strong edge to
the reserved list via the legacy graph-owned trace stack. With the held
list/graph interleave, the ordinary worker retired two objects and the
bridge zero. The running-world, budgeted trace stack now ignores a
snapshot of the same reserved owner in mode 2. It drops only the
duplicate stack hint, not the task, its gray credit, or its children.
The stopped-world drain is unchanged.

A two-owner graph with a strong edge to the reserved list now reports
one ordinary and one alternate retirement, with exactly one gray child
and one visible token. Both modes and the direct-cursor and depth-first
shapes passed three times each (12 fresh processes). The automatic
one/four-mutator harness and three other focused Linux native suites also
passed. This does not enable another CMS worker or establish a speedup.

## Startup selector for measuring the review candidate

This revision accepts the exact startup environment value
`PCC_GC2_EXACT_LIST_BRIDGE=1` only for GC2. It selects the reservation-
aware bridge (native mode 2). The first GC2 query caches the choice using
an atomic compare/exchange, and the read-only
`pcc_gc2_exact_list_bridge_mode()` reports it. Unset, blank, or any
other spelling stays off. Other collectors report off even if the
environment contains the flag. Mode 1 is still fixture-only.

The setting is for a sealed comparison, not a merge, agent worker
expansion, or a claim of better throughput. Until the arm tooling
attests it, do not use an inherited shell flag as proof that an A/B
case actually selected the candidate.

Linux owned native: 12 strict spelling/backend probes and 18 automatic
one/four-mutator runs passed. Mode 2 in the automatic run was selected
by startup environment, not by a direct runtime global write. Mode 0
was unset; mode 1 remained a separate fixture control. Eight ABI checks
passed. Native macOS and wall-time A/B were not run.

## Same-input Mac handoff: sealed arms

Use the exact commit containing this section as `REVIEW_SHA`. Run in
the maintainer's existing Mac checkout with the same gateway source for
both arms. Do not run another benchmark or compiler build concurrently.
The baseline below is the unchanged master runtime of this review series.

```sh
# Set REVIEW_SHA to the full commit SHA from the linked review receipt.
# Set PCC_GATEWAY_PACKAGE to your checkout's pcc_gateway package directory.
(
set -e
test -n "$REVIEW_SHA" && test -f "$PCC_GATEWAY_PACKAGE/structured.py" || exit 1
export PCC_GATEWAY_PACKAGE
git fetch shenming master review/gc2-exact-list-20261122
root="$(mktemp -d "${TMPDIR:-/tmp}/pcc-gc2-list-pair.XXXXXX")"
export PCC_PERF_HOME="$root/perf"
git worktree add --detach "$root/base" 55a90b4ad55389f4c2fb3a0d2f28ee29ddc8896b
git worktree add --detach "$root/review" "$REVIEW_SHA"
(cd "$root/base" && uv run python scripts/pcc_perf.py arm gc2-list-base)
(cd "$root/review" && uv run python scripts/pcc_perf.py arm gc2-list-review --gc2-exact-list-bridge)
(cd "$root/review" && uv run python scripts/pcc_perf.py ab gc2-list-base gc2-list-review \
  --pairs 5 --gc 0,2 --workers 1,4 --rounds 100 --control 0 --latency-receipts)
(cd "$root/review" && uv run python scripts/pcc_perf.py table gc2-list-review --rounds 100)
printf 'Receipts: %s\n' "$PCC_PERF_HOME"
)
```

Keep both `build.json` receipts and `$PCC_PERF_HOME/ledger.jsonl`.
The candidate arm requires exact mode attestation `on=2, off=0, gc0=0`
and the matching program hash; GC0 is deliberately tested with the
flag present during attestation. The tool passes the setting only for
GC2 and records it per arm. Read paired throughput, maximum runtime
STW, peak process RSS, and timed request p99 together against the GC0
noise floor. The workload is the owned handler, not the C512 network
server. The bridge and heap-goal pacer are separate experiments.

Linux ran 82 focused host tool tests and compiled and executed the
actual three-mode `pcc_perf` attestation using the source-matched owned
archive. It returned `on=2, off=0, gc0=0`. Linux did not build the
gateway arms or run A/B: the gateway package is absent and the host
cannot provide the default 6 GiB tree cap plus 8 GiB reserve. No
resource limit was reduced and no Mac execution is claimed here.

## Automatic lifecycle smoke on the measured runtime

A separately compiled lifecycle program creates a cyclic target on a
thread that exits, retains its weakref, and drives actual automatic
GC2 after disable/enable. It keeps a different short list reachable.
The same binary, with the selector off and on, delivered the weak
callback before the finalizer once, invalidated the target weakref,
and kept the unrelated list and its value alive. The on variant reads
a positive actual shortcut visit counter, not just the mode bit.
Both the ordinary and a PCC weak-callback `gc.collect()` reentry
case passed three times per mode (12 Linux owned runs). The ordinary
isolated reference uses CPython and passed. CPython reentry semantics,
C-extension ownership, and resurrection are not established here.
The sealed performance revision's runtime source is unchanged.
