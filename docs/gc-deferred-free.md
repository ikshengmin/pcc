# Bounded CMS terminal frees

This is a partial step toward concurrent GC1/GC2 sweeping. After the collection
owner resumes the world, it can transfer eligible, already-dead object blocks
to the **existing CMS worker** for the allocator's final physical release. It
does not detach the managed object list, move logical sweep to a worker, or
introduce a new finalizer queue.

## Admission and retained semantics

`freestanding_gc_deferred_free.py` retains the thread-local admission scope.
The two normal owners in `py_obj.py`, automatic tracing collection and explicit
`pcc_gc_collect`, open it after successfully stopping the world. Nested or
already-held stops, GC0/GC3/GC4, counted GC1/GC2, graph-lock/no-park callers, and
standalone low-level collection entrypoints do not create a scope.

The sole production enqueue call remains the terminal `free` branch of
`pcc_gc_free_object_memory`. It requires:

- An active count-free GC1/GC2 scope owned by the stopped-world thread.
- Successful managed-pointer retirement after the original logical death,
  index, lease, and allocation-origin checks.
- Both sweep-candidate and deallocating flags.
- No immortal, pinned, fresh, minor-arena, zpage, or C-extension lifetime.

Only payload word zero becomes queue linkage. Payload word eight (allocator
double-free poison) and the allocator prefix remain untouched until `free`.
Each producer scope still admits at most **4,096 blocks and 1 MiB of usable
allocator bytes**. Invalid sizes and exhausted capacity use the original
immediate-free path. No cap was raised for worker consumption.

Mark finishing, weakref clearing, finalizers and resurrection, post-finalizer
reachability rechecking, typed destruction, and object-index retirement retain
their original owners and ordering. GC4 forwarding-shell retirement is not
redirected. This is not the full detached-object-list sweep or PEP 442 migration.

## Publication and physical ownership

After actual outer world resume, `close` disables the scope, clears its TLS
ownership, and publishes completed-scope counters. Under the existing graph
lock it rechecks both CMS lifecycle fields: `started == 1` and no stop request.
If the single mailbox is empty, it transfers the entire raw chain. Otherwise
it frees that bounded batch locally after releasing the lock. Worker absence,
startup, pause, quarantine, and shutdown therefore cannot strand a new batch.
No worker is started or waited on by `close`.

The mailbox has three states:

- Empty: count zero and head null.
- Ready: count positive and head names the published chain.
- Claimed: count positive and head null while CMS owns the detached raw chain.

The graph lock protects head changes and transitions. Count and bytes use
atomic access; release publication supports the normal worker's acquire empty
check. Capacity stays reserved until the worker finishes physical release.
Thus **mailbox plus worker-private batch together** never exceed 4,096 blocks
or 1 MiB. This is additional to each original bounded producer scope, not a
bound on all threads' pending memory, the heap, or RSS. A busy mailbox causes
finite local work rather than an accumulating backlog.

The CMS loop drains before checking automatic-marking enablement or threshold,
so `gc.disable()` and threshold zero do not defer already-terminal frees.
Freeing occurs outside the graph lock, without managed allocation, user
callbacks, or safepoints. The next successful STW waits for this registered
worker to finish its bounded raw loop and reach an existing poll. This does
not provide a hard wall-clock pause bound.

## Stop, join, and allocator handoff

The existing stop owner changes CMS state from 1 to 3 before publishing its stop
request. The worker's final drain **always acquires the graph lock**, even when
the mailbox initially appears empty. An earlier admitted producer may still
hold that lock and publish after the stop CAS; the final acquisition orders
behind it and consumes its batch. A later producer sees state 3 and falls back.
An empty fast path at final exit would break this ordering and is forbidden.
Only after final drain does the worker clear its identity and return. Successful
join leaves no worker batch pending; join failure retains the original state-3
quarantine. Process termination itself relies on ordinary OS address-space
reclamation, not a newly introduced runtime shutdown protocol.

The owned allocator supports cross-thread frees: object cells enter the freeing
thread's bounded magazine, with central returns under the allocator lock.
Worker thread unregister drains both allocator magazine families before leaving
the live set. Queue transfer does not change allocator metadata or route blocks
to their former allocating thread. Each drain reads next pointer and usable
size before `free`, and never reads that block afterward.

## Counters

`pcc_gc_deferred_free_counter(metric)` returns separate atomic observations,
except TLS metrics 0/1. There is no joint snapshot or reset operation.

| Metric | Meaning |
| --- | --- |
| 0 | Current thread's open-scope pending blocks. |
| 1 | Current thread's open-scope pending usable bytes. |
| 2 | Eligible terminal-free attempts from closed scopes. |
| 3 | Blocks enqueued by closed scopes. |
| 4 | Blocks physically released by either raw drainer. |
| 5 | Eligible attempts falling back for capacity or invalid size. |
| 6 | Largest usable-byte total of one closed producer scope. |
| 7 | Threads currently inside a nonempty raw drain. |
| 8 | Blocks transferred to the CMS mailbox. |
| 9 | Blocks physically released by the actual CMS worker. |
| 10 | Blocks physically released by `close`'s local fallback. |
| 11 | Block reservation for the mailbox or worker-private batch. |
| 12 | Usable-byte reservation for the mailbox or worker-private batch. |
| 13 | Thread ID of the most recent actual CMS raw drainer, initially zero. |

Metrics 4/9/10 publish after completing a physical batch, so they may lag metric
3, including during stop callbacks. At a quiescent successful join with no open
producer scope, 3 equals 4, 8 equals 9, 4 equals 9 + 10, and 7/11/12 are zero.
Metrics 11/12 retain the whole reservation until the batch completes, including
blocks already freed in that in-progress batch; they are not exact remaining
physical blocks/bytes. Metric 6 is not peak process memory. Unknown metric IDs
return `-1`.

## Focused verification boundaries

`tests/python/test_gc_deferred_free.py` executes production bodies with controlled
memory/thread operations, including admitted-but-unpublished stop interleaving,
busy private-batch fallback, exclusions, caps, and no post-free access.
`test_gc_cms_worker_lifecycle.py` retains the existing start/stop/reset checks.

`test_gc_deferred_free_native.py` uses an explicitly admitted fresh owned runtime.
Its worker fixture never calls a drain helper: real CMS entry performs the frees.
It checks a different worker thread ID, disabled/zero-threshold progress,
immediate stop/join, eventual accounting, and reuse of retired addresses by a
different thread after worker-exit magazine drain. Its separate allocator-only
fixture keeps the worker stopped to isolate synchronous byte accounting. Public
collection-owner fixtures retain their original semantic checks and compare
physical completion after successful join, not in the stop callback.

These are controlled terminal verdicts and focused runtime checks. They do not
establish unmodified-workload performance, full five-collector correctness,
native-pcc1 fixed point, or complete phase 1c. Preserve observed lifecycle
failures and same-input baselines separately from these controls.
