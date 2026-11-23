"""Persistent gray batches for the count-free tracing collectors GC1-GC4.

Successful color transitions append to an owning thread's raw batch. A full
batch, a safepoint or thread exit publishes it to the shared MPSC list. The
graph lock serializes consumers and protects the partially consumed list;
the producer never takes that lock. Entries are addresses, not lifetime
leases: a consumer must check current object-index membership before reading
a header. Healthy GC1/GC2 CMS and auto-enabled mutator slices request
publication of another running thread's tail instead of searching the object
list. Paused-automatic explicit steps, stopped drains and a cycle which lost
a token to allocation failure retain that fallback.

GC3 shares GC1/GC2's mutator and marking protocol, this queue included.

GC4 queues its shades as well. It marks in a stopped world, and its drain
takes a queued address first and walks the object list only once the queue
is empty: a root seeded gray used to be found by walking from the list
head, past every younger object, to wherever the root sat. The walk stays
the authority for a gray object without a usable token: one the collector
moved since it was shaded (the token names the old address, which is no
longer a known object), or one colored without this queue.
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


@c_abi_export("pcc_gc_gray_worklist_unregister")
def pcc_gc_gray_worklist_unregister() -> None:
    """Publish remaining work before the registered thread/TLS disappears."""
    empty = global_load_ptr("pcc_gc_gray_local_batch")
    if ptr_is_null(empty) == 0:
        global_store_ptr("pcc_gc_gray_local_batch", null())
        free(empty)
