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
