"""Bounded terminal GC1/GC2 frees, handed to the existing CMS worker after resume.

These blocks have already left every managed-object index and completed their
typed destruction. Only the allocator's physical free remains. The queue borrows
payload word zero; word eight and the allocator prefix keep their original data.
This is not concurrent object sweeping or a finalizer queue.
"""

from pcc import i64
from pcc.extern import c_abi_export, c_int64, c_ptr, c_void, extern
from pcc.unsafe import (
    atomic_cas_i64,
    atomic_load_i32,
    atomic_load_i64,
    atomic_store_i64,
    atomic_rmw_i64,
    define_global_i64,
    define_global_ptr_null,
    define_thread_local_i32,
    define_thread_local_ptr_null,
    free,
    global_addr,
    global_load_ptr,
    global_store_ptr,
    load_i32,
    load_i64,
    load_ptr,
    null,
    ptr_is_null,
    store_i32,
    store_i64,
    store_ptr,
)


__pcc_freestanding__ = True

_MAX_BLOCKS: i64 = 4096
_MAX_USABLE_BYTES: i64 = 1048576
_NEXT: i64 = 0
_CANDIDATE: i64 = 1024
_DEALLOCATING: i64 = 524288
_EXCLUDED: i64 = 86081  # IMMORTAL | PINNED | MINOR | FRESH | ZPAGE

# All accesses stay in this compilation owner. The native TLS word primitive
# also holds integer words, as in the thread kernel's TLS state/epoch storage.
define_thread_local_i32("pcc_gc_deferred_free_active", 0)
define_thread_local_ptr_null("pcc_gc_deferred_free_head")
define_thread_local_ptr_null("pcc_gc_deferred_free_tail")
define_thread_local_ptr_null("pcc_gc_deferred_free_pending_count")
define_thread_local_ptr_null("pcc_gc_deferred_free_pending_bytes")
define_thread_local_ptr_null("pcc_gc_deferred_free_local_attempts")
define_thread_local_ptr_null("pcc_gc_deferred_free_local_fallbacks")

# Closed scopes publish once per field, not atomically on every free. Physical
# completion is asynchronous; these are not atomic multi-field snapshots.
define_global_i64("pcc_gc_deferred_free_attempts", 0)
define_global_i64("pcc_gc_deferred_free_enqueued", 0)
define_global_i64("pcc_gc_deferred_free_drained", 0)
define_global_i64("pcc_gc_deferred_free_cap_fallbacks", 0)
define_global_i64("pcc_gc_deferred_free_peak_bytes", 0)
define_global_i64("pcc_gc_deferred_free_active_drainers", 0)
define_global_i64("pcc_gc_deferred_free_handed_off", 0)
define_global_i64("pcc_gc_deferred_free_worker_drained", 0)
define_global_i64("pcc_gc_deferred_free_local_drained", 0)
define_global_i64("pcc_gc_deferred_free_worker_thread", 0)

# One bounded mailbox, including the worker's detached batch. The graph lock
# protects the head and state transitions; atomic count/bytes also serve raw
# diagnostic reads and the worker's empty fast path. count stays nonzero until
# physical release completes: READY has a head, CLAIMED has no shared head.
define_global_ptr_null("pcc_gc_deferred_free_worker_head")
define_global_i64("pcc_gc_deferred_free_worker_count", 0)
define_global_i64("pcc_gc_deferred_free_worker_bytes", 0)

pcc_thread_owns_stopped_world = extern("pcc_thread_owns_stopped_world", (), c_int64)
pcc_thread_no_park_depth = extern("pcc_thread_no_park_depth", (), c_int64)
pcc_py_gc_minor_graph_lock_depth = extern("pcc_py_gc_minor_graph_lock_depth", (), c_int64)
pcc_capi_is_cext_type_tag = extern("pcc_capi_is_cext_type_tag", (c_int64,), c_int64)
pcc_py_gc_minor_graph_lock = extern("pcc_py_gc_minor_graph_lock", (), c_void)
pcc_py_gc_minor_graph_unlock = extern("pcc_py_gc_minor_graph_unlock", (), c_void)
pcc_gc_is_cms_worker = extern("pcc_gc_is_cms_worker", (), c_int64)
pcc_current_thread_id = extern("pcc_current_thread_id", (), c_int64)
malloc_usable_size = extern("malloc_usable_size", (c_ptr,), c_int64)
pcc_platform_abort = extern("pcc_platform_abort", (), c_void)


@c_abi_export("pcc_gc_deferred_free_begin")
def pcc_gc_deferred_free_begin(prior_held: i64) -> i64:
    backend: i64 = load_i32(global_addr("pcc_gc_backend_selected"), 0)
    if (
        (backend != 1 and backend != 2)
        or load_i32(global_addr("pcc_gc_backend4_no_refcounts"), 0) == 0
        or prior_held != 0
        or pcc_thread_owns_stopped_world() == 0
        or pcc_thread_no_park_depth() != 0
        or pcc_py_gc_minor_graph_lock_depth() != 0
        or load_i32(global_addr("pcc_gc_deferred_free_active"), 0) != 0
    ):
        return 0
    if (
        ptr_is_null(global_load_ptr("pcc_gc_deferred_free_head")) == 0
        or ptr_is_null(global_load_ptr("pcc_gc_deferred_free_tail")) == 0
        or load_i64(global_addr("pcc_gc_deferred_free_pending_count"), 0) != 0
        or load_i64(global_addr("pcc_gc_deferred_free_pending_bytes"), 0) != 0
    ):
        pcc_platform_abort()
        return 0
    store_i64(global_addr("pcc_gc_deferred_free_local_attempts"), 0, 0)
    store_i64(global_addr("pcc_gc_deferred_free_local_fallbacks"), 0, 0)
    store_i32(global_addr("pcc_gc_deferred_free_active"), 0, 1)
    return 1


@c_abi_export("pcc_gc_deferred_free_try_enqueue")
def pcc_gc_deferred_free_try_enqueue(obj, flags: i64) -> i64:
    # The sole production caller has successfully retired managed provenance
    # and is about to call free. Do not admit finalizer-created fresh garbage,
    # pins, literals, arena/zpage allocations or C-extension lifecycle owners.
    if load_i32(global_addr("pcc_gc_deferred_free_active"), 0) == 0:
        return 0
    if (flags & (_CANDIDATE | _DEALLOCATING)) != (_CANDIDATE | _DEALLOCATING):
        return 0
    if (flags & _EXCLUDED) != 0:
        return 0
    backend: i64 = load_i32(global_addr("pcc_gc_backend_selected"), 0)
    if (backend != 1 and backend != 2) or pcc_thread_owns_stopped_world() == 0:
        return 0
    if pcc_capi_is_cext_type_tag(load_i32(obj, 8)) != 0:
        return 0
    attempts = global_addr("pcc_gc_deferred_free_local_attempts")
    store_i64(attempts, 0, load_i64(attempts, 0) + 1)
    count_slot = global_addr("pcc_gc_deferred_free_pending_count")
    bytes_slot = global_addr("pcc_gc_deferred_free_pending_bytes")
    count: i64 = load_i64(count_slot, 0)
    pending_bytes: i64 = load_i64(bytes_slot, 0)
    usable: i64 = malloc_usable_size(obj)
    if count >= _MAX_BLOCKS or usable <= 0 or usable > _MAX_USABLE_BYTES - pending_bytes:
        fallbacks = global_addr("pcc_gc_deferred_free_local_fallbacks")
        store_i64(fallbacks, 0, load_i64(fallbacks, 0) + 1)
        return 0
    store_ptr(obj, _NEXT, null())
    tail = global_load_ptr("pcc_gc_deferred_free_tail")
    if ptr_is_null(tail) != 0:
        global_store_ptr("pcc_gc_deferred_free_head", obj)
    else:
        store_ptr(tail, _NEXT, obj)
    global_store_ptr("pcc_gc_deferred_free_tail", obj)
    store_i64(count_slot, 0, count + 1)
    store_i64(bytes_slot, 0, pending_bytes + usable)
    return 1


@c_abi_export("pcc_gc_deferred_free_drain_raw_chain")
def _drain_raw_chain(obj, count: i64, pending_bytes: i64) -> None:
    # No managed operations, user callbacks, graph lock or safepoint in this
    # bounded loop. The registered caller stays running until its next poll.
    remaining: i64 = count
    remaining_bytes: i64 = pending_bytes
    atomic_rmw_i64("add", global_addr("pcc_gc_deferred_free_active_drainers"), 0, 1, "release")
    while ptr_is_null(obj) == 0:
        if remaining <= 0:
            pcc_platform_abort()
            return
        nxt = load_ptr(obj, _NEXT)
        usable: i64 = malloc_usable_size(obj)
        free(obj)
        # Neither the block nor its allocator prefix is read after free.
        remaining = remaining - 1
        remaining_bytes = remaining_bytes - usable
        obj = nxt
    if remaining != 0 or remaining_bytes != 0:
        pcc_platform_abort()
        return
    atomic_rmw_i64("add", global_addr("pcc_gc_deferred_free_drained"), 0, count, "relaxed")
    atomic_rmw_i64("sub", global_addr("pcc_gc_deferred_free_active_drainers"), 0, 1, "release")


@c_abi_export("pcc_gc_deferred_free_close")
def pcc_gc_deferred_free_close() -> i64:
    if load_i32(global_addr("pcc_gc_deferred_free_active"), 0) == 0:
        return 0
    # Nested public collection's resume only decrements stop depth. Only the
    # normal outer owner calls here, after actually releasing its world stop.
    store_i32(global_addr("pcc_gc_deferred_free_active"), 0, 0)
    if (
        pcc_thread_owns_stopped_world() != 0
        or pcc_thread_no_park_depth() != 0
        or pcc_py_gc_minor_graph_lock_depth() != 0
    ):
        pcc_platform_abort()
        return 0
    count: i64 = load_i64(global_addr("pcc_gc_deferred_free_pending_count"), 0)
    pending_bytes: i64 = load_i64(global_addr("pcc_gc_deferred_free_pending_bytes"), 0)
    obj = global_load_ptr("pcc_gc_deferred_free_head")
    # Retire producer ownership before a consumer can observe the chain.
    global_store_ptr("pcc_gc_deferred_free_head", null())
    global_store_ptr("pcc_gc_deferred_free_tail", null())
    store_i64(global_addr("pcc_gc_deferred_free_pending_count"), 0, 0)
    store_i64(global_addr("pcc_gc_deferred_free_pending_bytes"), 0, 0)
    atomic_rmw_i64("add", global_addr("pcc_gc_deferred_free_attempts"), 0,
                   load_i64(global_addr("pcc_gc_deferred_free_local_attempts"), 0), "relaxed")
    atomic_rmw_i64("add", global_addr("pcc_gc_deferred_free_enqueued"), 0, count, "relaxed")
    atomic_rmw_i64("add", global_addr("pcc_gc_deferred_free_cap_fallbacks"), 0,
                   load_i64(global_addr("pcc_gc_deferred_free_local_fallbacks"), 0), "relaxed")
    peak = global_addr("pcc_gc_deferred_free_peak_bytes")
    previous: i64 = atomic_load_i64(peak, 0, "relaxed")
    while pending_bytes > previous:
        observed: i64 = atomic_cas_i64(peak, 0, previous, pending_bytes, "relaxed", "relaxed")
        if observed == previous:
            break
        previous = observed
    if count == 0:
        return 0
    handed_off: i64 = 0
    pcc_py_gc_minor_graph_lock()
    if (
        atomic_load_i32(global_addr("pcc_gc_cms_worker_started"), 0, "acquire") == 1
        and atomic_load_i32(global_addr("pcc_gc_cms_worker_stop_requested"), 0, "acquire") == 0
        and atomic_load_i64(global_addr("pcc_gc_deferred_free_worker_count"), 0, "relaxed") == 0
    ):
        global_store_ptr("pcc_gc_deferred_free_worker_head", obj)
        atomic_store_i64(global_addr("pcc_gc_deferred_free_worker_bytes"), 0, pending_bytes, "release")
        atomic_rmw_i64("add", global_addr("pcc_gc_deferred_free_handed_off"), 0, count, "relaxed")
        atomic_store_i64(global_addr("pcc_gc_deferred_free_worker_count"), 0, count, "release")
        handed_off = 1
    pcc_py_gc_minor_graph_unlock()
    if handed_off == 0:
        # No worker, shutdown/start in progress, or the single mailbox busy:
        # finish this finite batch here rather than waiting or accumulating.
        _drain_raw_chain(obj, count, pending_bytes)
        atomic_rmw_i64("add", global_addr("pcc_gc_deferred_free_local_drained"), 0, count, "relaxed")
    return count


@c_abi_export("pcc_gc_deferred_free_worker_drain")
def pcc_gc_deferred_free_worker_drain(closing: i64) -> i64:
    if pcc_gc_is_cms_worker() == 0:
        return 0
    count_slot = global_addr("pcc_gc_deferred_free_worker_count")
    if closing == 0 and atomic_load_i64(count_slot, 0, "acquire") == 0:
        return 0
    # Exit MUST acquire the lock even if the mailbox first appears empty.
    # An admitted producer may hold it across stop's 1->3 CAS and publish
    # afterward. This acquisition waits for that publication; all subsequent
    # producers observe state 3 and drain locally. No free occurs under lock.
    pcc_py_gc_minor_graph_lock()
    count: i64 = atomic_load_i64(count_slot, 0, "relaxed")
    pending_bytes: i64 = atomic_load_i64(global_addr("pcc_gc_deferred_free_worker_bytes"), 0, "relaxed")
    obj = global_load_ptr("pcc_gc_deferred_free_worker_head")
    global_store_ptr("pcc_gc_deferred_free_worker_head", null())
    pcc_py_gc_minor_graph_unlock()
    if count == 0:
        return 0
    _drain_raw_chain(obj, count, pending_bytes)
    atomic_rmw_i64("add", global_addr("pcc_gc_deferred_free_worker_drained"), 0, count, "relaxed")
    atomic_store_i64(global_addr("pcc_gc_deferred_free_worker_thread"), 0, pcc_current_thread_id(), "release")
    # Keep the mailbox reserved during the entire private drain. A producer
    # racing this completion either falls back or installs the next batch.
    pcc_py_gc_minor_graph_lock()
    atomic_store_i64(global_addr("pcc_gc_deferred_free_worker_bytes"), 0, 0, "release")
    atomic_store_i64(count_slot, 0, 0, "release")
    pcc_py_gc_minor_graph_unlock()
    return count


@c_abi_export("pcc_gc_deferred_free_counter")
def pcc_gc_deferred_free_counter(metric: i64) -> i64:
    # 0/1 are this thread's open scope; 2/3/5/6 describe closed scopes.
    # Physical completion (4/9/10) may lag publication. Fields are not a snapshot.
    if metric == 0:
        return load_i64(global_addr("pcc_gc_deferred_free_pending_count"), 0)
    if metric == 1:
        return load_i64(global_addr("pcc_gc_deferred_free_pending_bytes"), 0)
    if metric == 2:
        return atomic_load_i64(global_addr("pcc_gc_deferred_free_attempts"), 0, "relaxed")
    if metric == 3:
        return atomic_load_i64(global_addr("pcc_gc_deferred_free_enqueued"), 0, "relaxed")
    if metric == 4:
        return atomic_load_i64(global_addr("pcc_gc_deferred_free_drained"), 0, "relaxed")
    if metric == 5:
        return atomic_load_i64(global_addr("pcc_gc_deferred_free_cap_fallbacks"), 0, "relaxed")
    if metric == 6:
        return atomic_load_i64(global_addr("pcc_gc_deferred_free_peak_bytes"), 0, "relaxed")
    if metric == 7:
        return atomic_load_i64(global_addr("pcc_gc_deferred_free_active_drainers"), 0, "acquire")
    if metric == 8:
        return atomic_load_i64(global_addr("pcc_gc_deferred_free_handed_off"), 0, "relaxed")
    if metric == 9:
        return atomic_load_i64(global_addr("pcc_gc_deferred_free_worker_drained"), 0, "relaxed")
    if metric == 10:
        return atomic_load_i64(global_addr("pcc_gc_deferred_free_local_drained"), 0, "relaxed")
    if metric == 11:
        return atomic_load_i64(global_addr("pcc_gc_deferred_free_worker_count"), 0, "acquire")
    if metric == 12:
        return atomic_load_i64(global_addr("pcc_gc_deferred_free_worker_bytes"), 0, "acquire")
    if metric == 13:
        return atomic_load_i64(global_addr("pcc_gc_deferred_free_worker_thread"), 0, "acquire")
    return -1
