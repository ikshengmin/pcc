# Bounded post-resume terminal frees

This is a partial step toward concurrent GC1/GC2 sweeping. It moves only the
allocator's final physical release of eligible, already-dead object blocks out
of the collection owner's stopped-world window. It does not detach the object
list, run sweep on a CMS worker, or defer finalizers to a new queue.

## Ownership and admission

`freestanding_gc_deferred_free.py` owns a thread-local FIFO. The two normal
collection owners in `py_obj.py`, automatic tracing collection and explicit
`pcc_gc_collect`, open a scope after successfully stopping the world. The owner
records whether it already held the world after start callbacks, before the
stop attempt. A nested/already-held stop does not open another scope. GC0,
GC3/GC4, counted GC1/GC2, and graph-lock/no-park callers do not open one either.
Direct low-level collection entrypoints do not create a scope; outside an
existing owner scope their terminal releases remain synchronous.

The sole production enqueue call is the terminal `free` branch of
`pcc_gc_free_object_memory`. Admission requires all of the following:

- An active GC1/GC2 count-free collection scope on the current thread.
- Successful managed-pointer retirement after the existing logical death,
  index, lease, and allocation-origin checks.
- Both the sweep-candidate and deallocating flags.
- No immortal, pinned, fresh, minor-arena, or zpage allocation flag, and no
  C-extension type tag.
- The current thread still owns the stopped world.

The queue borrows payload word zero only after those checks. It preserves the
second payload word, including allocator double-free poison semantics, and
does not alter the allocator prefix. Size accounting uses the owned
`malloc_usable_size` entrypoint. An unregistered object is not on an allocator
free list merely because managed provenance has been retired.

## Bounded synchronous fallback

Each scope holds at most 4,096 blocks and 1 MiB of allocator usable bytes. An
invalid usable size, an oversized block, or an exhausted block/byte allowance
takes the original immediate-free path. These are bounds on this scope's raw
payload queue, not on total collector work or total process memory.

After the actual outer world resume, the same mutator closes its scope before
external-resource polling, stop callbacks, or return. Closing first disables
further admission, then reads each next pointer and size before calling the
owned allocator's `free`. It never reads that block afterward. No finalizer,
managed allocation, implicit safepoint, or user callback belongs to this raw
drain. Retaining the same owner also preserves the allocator's thread-local
magazine ownership.

Other carriers can run after resume while physical releases remain pending.
The draining mutator does not poll inside this bounded loop, so a subsequent
world-stop request may wait for that drain and the mutator's next safepoint.
This mechanism does not establish a hard whole-collection pause bound.

## Counters

`pcc_gc_deferred_free_counter(metric)` returns:

| Metric | Meaning |
| --- | --- |
| 0 | Current thread's pending block count. |
| 1 | Current thread's pending allocator usable bytes. |
| 2 | Eligible terminal-free attempts from completed scopes. |
| 3 | Blocks enqueued by completed scopes. |
| 4 | Blocks physically released by completed scopes. |
| 5 | Eligible attempts falling back for capacity or invalid usable size. |
| 6 | Largest pending usable-byte total of one completed scope. |
| 7 | Number of threads currently inside a nonempty raw drain. |

Metrics 2–6 publish once per completed scope; they are separate atomic fields,
not an atomic multi-field snapshot. Metrics 0/1 are TLS observations, not
process-wide pending totals. Metric 6 is not peak process memory or the sum of
simultaneous queues. Metric 7 brackets a short interval: another carrier may
observe zero despite genuine post-resume draining. Unknown metric IDs return
`-1`. There is no reset operation.

## Preserved work and validation limits

Mark finishing, candidate discovery, weakref handling, finalizers,
post-finalizer reachability rechecking, typed destruction, and object-index
retirement keep their existing owners and ordering. This change does not
complete PEP 442 migration, remove the object-list cursor, or make the entire
sweep concurrent. GC4 forwarding-shell retirement is not redirected here.

Focused checks are in `tests/python/test_gc_deferred_free.py` and
`tests/python/test_gc_deferred_free_native.py`. Their native fixtures use real
owned allocator/runtime objects and controlled terminal/candidate verdicts.
The allocator-isolation fixture stops and joins the CMS worker before its
controlled queue cases, so another thread's startup allocations cannot change
the global live-byte accounting oracle. Its separate native carrier still
witnesses progress after world resume. Public collection-owner fixtures retain
normal CMS operation. Queue-capacity and resume-order witnesses do not
substitute for an unmodified workload, full five-collector correctness,
native-pcc1 bootstrap, or throughput qualification. Keep same-input benchmark
results separate from instrumented counter observations and from older
revisions' measurements.
