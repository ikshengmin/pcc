"""Persistent gray batches for the count-free collectors GC1, GC2 and GC4.

Successful color transitions append to an owning thread's raw batch. A full
batch, a safepoint or thread exit publishes it to the shared MPSC list. The
graph lock serializes consumers and protects the partially consumed list;
the producer never takes that lock. Entries are addresses, not lifetime
leases: a consumer must check current object-index membership before reading
a header. Healthy GC1/GC2 CMS and auto-enabled mutator slices request
publication of another running thread's tail instead of searching the object
list. Paused-automatic explicit steps, stopped drains and a cycle which lost
a token to allocation failure retain that fallback.

GC4 queues its shades as well. It marks in a stopped world, and its drain
takes a queued address first and walks the object list only once the queue
is empty: a root seeded gray used to be found by walking from the list
head, past every younger object, to wherever the root sat. The walk stays
the authority for a gray object without a usable token: one the collector
moved since it was shaded (the token names the old address, which is no
longer a known object), or one colored without this queue. GC3 marks on the
collector thread while mutators run and keeps the walk alone: queued there,
every shade paid for a token and the measured throughput did not move.
"""

from pcc import i64
from pcc.extern import c_abi_export
from pcc.unsafe import (
    atomic_cas_i64,
    atomic_load_i64,
    atomic_rmw_i64,
    atomic_store_i32,
    define_global_i32,
    define_global_i64,
    define_global_ptr_null,
    define_thread_local_ptr_null,
    free,
    global_addr,
    global_load_ptr,
    global_store_ptr,
    int_to_ptr,
    load_i32,
    load_i64,
    load_ptr,
    malloc,
    null,
    ptr_is_null,
    ptr_to_int,
    store_i64,
    store_ptr,
)


__pcc_freestanding__ = True

_BATCH_NEXT = 0
_BATCH_EPOCH = 8
_BATCH_COUNT = 16
_BATCH_READ = 24
_BATCH_ITEMS = 32
_BATCH_CAPACITY = 64
_BATCH_BYTES = 544

# Only this module accesses its TLS symbol, including from the thread kernel.
define_thread_local_ptr_null("pcc_gc_gray_local_batch")
define_global_ptr_null("pcc_gc_gray_published_batches")
define_global_ptr_null("pcc_gc_gray_pending_batches")
define_global_i64("pcc_gc_gray_worklist_allocation_failures", 0)
# An eligible mark consumer requests publication. A producer flush never
# clears this level: its own tail says nothing about another running producer.
define_global_i32("pcc_gc_gray_worklist_flush_requested", 0)
# A successful later allocation cannot recover a token already lost to OOM.
# Only stopped whitening/finished cleanup may clear this cycle-local latch.
define_global_i32("pcc_gc_gray_worklist_fallback_required", 0)


@c_abi_export("pcc_gc_gray_worklist_flush")
def pcc_gc_gray_worklist_flush() -> None:
    """Publish this thread's nonempty batch without allocation or polling."""
    batch = global_load_ptr("pcc_gc_gray_local_batch")
    if ptr_is_null(batch) != 0 or load_i64(batch, _BATCH_COUNT) == 0:
        return
    global_store_ptr("pcc_gc_gray_local_batch", null())
    head = global_addr("pcc_gc_gray_published_batches")
    previous: i64 = atomic_load_i64(head, 0, "relaxed")
    while True:
        store_ptr(batch, _BATCH_NEXT, int_to_ptr(previous))
        observed: i64 = atomic_cas_i64(
            head, 0, previous, ptr_to_int(batch), "release", "relaxed"
        )
        if observed == previous:
            return
        previous = observed


@c_abi_export("pcc_gc_gray_worklist_note")
def pcc_gc_gray_worklist_note(obj) -> None:
    """Record a successful shade, including roots seeded before mark_active."""
    if load_i32(global_addr("pcc_gc_backend4_no_refcounts"), 0) == 0:
        return
    backend: i64 = load_i32(global_addr("pcc_gc_backend_selected"), 0)
    if backend != 1 and backend != 2 and backend != 4:
        return
    epoch: i64 = atomic_load_i64(
        global_addr("pcc_gc_tracing_cycle_epoch"), 0, "acquire"
    )
    batch = global_load_ptr("pcc_gc_gray_local_batch")
    if ptr_is_null(batch) == 0:
        if load_i64(batch, _BATCH_EPOCH) != epoch:
            # Never mix epochs, including delayed TLS work from an old cycle.
            pcc_gc_gray_worklist_flush()
            batch = global_load_ptr("pcc_gc_gray_local_batch")
    if ptr_is_null(batch) != 0:
        batch = malloc(_BATCH_BYTES)
        if ptr_is_null(batch) != 0:
            # The color/count transition is NOT undone. The original walk
            # will still find this gray object, including while stopped.
            atomic_store_i32(
                global_addr("pcc_gc_gray_worklist_fallback_required"),
                0, 1, "release",
            )
            atomic_rmw_i64(
                "add", global_addr("pcc_gc_gray_worklist_allocation_failures"),
                0, 1, "relaxed",
            )
            return
        store_i64(batch, _BATCH_COUNT, 0)
        store_i64(batch, _BATCH_READ, 0)
        store_ptr(batch, _BATCH_NEXT, null())
        global_store_ptr("pcc_gc_gray_local_batch", batch)
    count: i64 = load_i64(batch, _BATCH_COUNT)
    if count == 0:
        store_i64(batch, _BATCH_EPOCH, epoch)
    store_ptr(batch, _BATCH_ITEMS + count * 8, obj)
    count = count + 1
    store_i64(batch, _BATCH_COUNT, count)
    if count == _BATCH_CAPACITY:
        pcc_gc_gray_worklist_flush()


@c_abi_export("pcc_gc_gray_worklist_take_locked")
def pcc_gc_gray_worklist_take_locked():
    """Take one raw address; caller holds graph lock or owns stopped world.

    Local children are consumed first without publishing a tiny batch for
    each parent. Shared partial batches live in graph-owned global state,
    not CMS TLS, so a mutator assist or stopped-world drain can resume them.
    Stale epoch blocks are discarded without dereferencing object addresses.
    """
    if load_i32(global_addr("pcc_gc_backend4_no_refcounts"), 0) == 0:
        return null()
    backend: i64 = load_i32(global_addr("pcc_gc_backend_selected"), 0)
    if backend != 1 and backend != 2 and backend != 4:
        return null()
    epoch: i64 = atomic_load_i64(
        global_addr("pcc_gc_tracing_cycle_epoch"), 0, "acquire"
    )
    local = global_load_ptr("pcc_gc_gray_local_batch")
    if ptr_is_null(local) == 0:
        count: i64 = load_i64(local, _BATCH_COUNT)
        if load_i64(local, _BATCH_EPOCH) != epoch:
            store_i64(local, _BATCH_COUNT, 0)
        elif count > 0:
            count = count - 1
            store_i64(local, _BATCH_COUNT, count)
            return load_ptr(local, _BATCH_ITEMS + count * 8)
    batch = global_load_ptr("pcc_gc_gray_pending_batches")
    while True:
        if ptr_is_null(batch) != 0:
            bits: i64 = atomic_rmw_i64(
                "xchg", global_addr("pcc_gc_gray_published_batches"),
                0, 0, "acquire",
            )
            batch = int_to_ptr(bits)
            global_store_ptr("pcc_gc_gray_pending_batches", batch)
            if ptr_is_null(batch) != 0:
                return null()
        index: i64 = load_i64(batch, _BATCH_READ)
        if (load_i64(batch, _BATCH_EPOCH) == epoch
                and index < load_i64(batch, _BATCH_COUNT)):
            obj = load_ptr(batch, _BATCH_ITEMS + index * 8)
            store_i64(batch, _BATCH_READ, index + 1)
            return obj
        nxt = load_ptr(batch, _BATCH_NEXT)
        global_store_ptr("pcc_gc_gray_pending_batches", nxt)
        free(batch)
        batch = nxt


@c_abi_export("pcc_gc_gray_worklist_unregister")
def pcc_gc_gray_worklist_unregister() -> None:
    """Publish remaining work before the registered thread/TLS disappears."""
    pcc_gc_gray_worklist_flush()
    empty = global_load_ptr("pcc_gc_gray_local_batch")
    if ptr_is_null(empty) == 0:
        global_store_ptr("pcc_gc_gray_local_batch", null())
        free(empty)


@c_abi_export("pcc_gc_gray_worklist_clear_stopped")
def pcc_gc_gray_worklist_clear_stopped() -> None:
    """Drop address tokens at the stopped-world whitening/finished boundary.

    The caller owns the stopped world and serializes collector consumers.
    Every other parked/exited thread has already published its tail. This
    also handles a finalizer reachability recheck which reuses a mark epoch.
    Never inspect an object through a token being discarded here.
    """
    local = global_load_ptr("pcc_gc_gray_local_batch")
    if ptr_is_null(local) == 0:
        store_i64(local, _BATCH_COUNT, 0)
        store_i64(local, _BATCH_READ, 0)
    batch = global_load_ptr("pcc_gc_gray_pending_batches")
    global_store_ptr("pcc_gc_gray_pending_batches", null())
    while ptr_is_null(batch) == 0:
        nxt = load_ptr(batch, _BATCH_NEXT)
        free(batch)
        batch = nxt
    bits: i64 = atomic_rmw_i64(
        "xchg", global_addr("pcc_gc_gray_published_batches"), 0, 0, "acquire"
    )
    batch = int_to_ptr(bits)
    while ptr_is_null(batch) == 0:
        nxt = load_ptr(batch, _BATCH_NEXT)
        free(batch)
        batch = nxt
    # Every other producer is parked/exited and has published its tail.
    # Same-backend epoch advances alone do not provide this exclusion.
    atomic_store_i32(
        global_addr("pcc_gc_gray_worklist_fallback_required"), 0, 0, "release"
    )
    atomic_store_i32(
        global_addr("pcc_gc_gray_worklist_flush_requested"), 0, 0, "release"
    )
