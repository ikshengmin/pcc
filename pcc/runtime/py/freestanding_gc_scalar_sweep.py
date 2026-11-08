"""Bounded, committed GC1/2 scalar deaths retired by the existing CMS worker.

The ordinary sweep has already invalidated weakrefs, run PASS0/rechecked
resurrection and cleared slots. Only exact, untracked INT/FLOAT/STR object-family
allocations enter this mailbox. Their typed destructors have no work other than
free_object_memory. No typed dispatcher, user callback or managed allocation is
called here. This is not full-chain concurrent sweep or a finalizer queue.

ObjectNode.freeing == -batch_token is a distinct pending owner, not retired=1.
The node and index remain intact; live bytes are debited once at death claim.
All existing nonzero-freeing walks skip it; DEALLOCATING also rejects lock-free
shading. Batch tokens are independent of mark epochs: a committed death survives
a same-GC reset, which does not clear these nodes. Backend freeze prohibits a
collector change. Failed entries remain durably owned in QUARANTINED storage.
"""
from pcc import i64
from pcc.extern import c_abi_export, c_int64, c_ptr, c_void, extern
from pcc.unsafe import (
    abi_constant,
    atomic_load_i32,
    atomic_load_i64,
    atomic_rmw_i64,
    atomic_store_i64,
    define_global_i64,
    define_global_null_ptr_array,
    define_thread_local_ptr_null,
    free,
    global_addr,
    load_i32,
    load_i64,
    load_ptr,
    null,
    ptr_eq,
    ptr_is_null,
    store_i32,
    store_i64,
    store_ptr,
)

__pcc_freestanding__ = True

_MAX_TASKS: i64 = 256
_MAX_BYTES: i64 = 65536
_TASK_BYTES: i64 = 40
_OBJECT: i64 = 0
_NODE: i64 = 8
_USABLE: i64 = 16
_TAG: i64 = 24
_RESULT: i64 = 32  # 0 pending, 1 retired/raw owner, -1 quarantined
_EMPTY: i64 = 0
_BUILDING: i64 = 1
_READY: i64 = 2
_DRAINING: i64 = 3
_QUARANTINED: i64 = 4
_CANDIDATE: i64 = 1024
_DEALLOCATING: i64 = 524288
_EXCLUDED: i64 = 610419  # IMMORTAL|TRACKED|GRAY|BLACK|PINNED|MINOR|FRESH|ZPAGE|DEALLOCATING

define_global_null_ptr_array("pcc_gc_scalar_sweep_tasks", 1280)
define_global_i64("pcc_gc_scalar_sweep_state", 0)
define_global_i64("pcc_gc_scalar_sweep_token", 0)
define_global_i64("pcc_gc_scalar_sweep_count", 0)
define_global_i64("pcc_gc_scalar_sweep_bytes", 0)
define_thread_local_ptr_null("pcc_gc_scalar_sweep_producer_token")
define_global_i64("pcc_gc_scalar_sweep_enqueued", 0)
define_global_i64("pcc_gc_scalar_sweep_retired", 0)
define_global_i64("pcc_gc_scalar_sweep_released", 0)
define_global_i64("pcc_gc_scalar_sweep_worker_retired", 0)
define_global_i64("pcc_gc_scalar_sweep_local_retired", 0)
define_global_i64("pcc_gc_scalar_sweep_worker_thread", 0)
define_global_i64("pcc_gc_scalar_sweep_quarantined", 0)
define_global_i64("pcc_gc_scalar_sweep_cap_rejections", 0)
define_global_i64("pcc_gc_scalar_sweep_sealed", 0)

pcc_py_gc_minor_graph_lock = extern("pcc_py_gc_minor_graph_lock", (), c_void)
pcc_py_gc_minor_graph_unlock = extern("pcc_py_gc_minor_graph_unlock", (), c_void)
pcc_thread_owns_stopped_world = extern("pcc_thread_owns_stopped_world", (), c_int64)
pcc_gc_is_cms_worker = extern("pcc_gc_is_cms_worker", (), c_int64)
pcc_current_thread_id = extern("pcc_current_thread_id", (), c_int64)
pcc_gc_object_index_find = extern("pcc_gc_object_index_find", (c_ptr,), c_ptr)
pcc_gc_object_index_remove = extern("pcc_gc_object_index_remove", (c_ptr,), c_ptr)
pcc_gc_identity_index_find = extern("pcc_gc_identity_index_find", (c_ptr,), c_ptr)
pcc_gc_managed_pointer_index_contains = extern("pcc_gc_managed_pointer_index_contains", (c_ptr,), c_int64)
pcc_gc_granule_is_object_start = extern("pcc_gc_granule_is_object_start", (c_ptr,), c_int64)
pcc_gc_granule_object_retire = extern("pcc_gc_granule_object_retire", (c_ptr,), c_int64)
pcc_gc_object_node_is_address_pinned = extern("pcc_gc_object_node_is_address_pinned", (c_ptr, c_ptr), c_int64)
pcc_gc_object_node_unlink = extern("pcc_gc_object_node_unlink", (c_ptr,), c_void)
pcc_gc_object_node_release = extern("pcc_gc_object_node_release", (c_ptr,), c_void)
pcc_gc_note_scalar_sweep_claim = extern("pcc_gc_note_scalar_sweep_claim", (c_ptr,), c_void)
pcc_gc_live_bytes_subtract = extern("pcc_gc_live_bytes_subtract", (c_int64,), c_void)
malloc_usable_size = extern("malloc_usable_size", (c_ptr,), c_int64)
pcc_platform_abort = extern("pcc_platform_abort", (), c_void)


@c_abi_export("pcc_gc_scalar_sweep_begin")
def pcc_gc_scalar_sweep_begin() -> None:
    # Called only after the existing outer GC1/2 scope admitted its owner.
    store_i64(global_addr("pcc_gc_scalar_sweep_producer_token"), 0, 0)
    pcc_py_gc_minor_graph_lock()
    backend: i64 = load_i32(global_addr("pcc_gc_backend_selected"), 0)
    if (
        (backend == 1 or backend == 2)
        and pcc_thread_owns_stopped_world() != 0
        and load_i32(global_addr("pcc_gc_backend4_no_refcounts"), 0) != 0
        and atomic_load_i32(global_addr("pcc_gc_backend_frozen"), 0, "acquire") != 0
        and atomic_load_i32(global_addr("pcc_gc_cms_worker_started"), 0, "acquire") == 1
        and atomic_load_i32(global_addr("pcc_gc_cms_worker_stop_requested"), 0, "acquire") == 0
        and load_i64(global_addr("pcc_gc_scalar_sweep_state"), 0) == _EMPTY
    ):
        token: i64 = load_i64(global_addr("pcc_gc_scalar_sweep_token"), 0)
        if token < 0 or token == 9223372036854775807:
            pcc_platform_abort()
        else:
            token = token + 1
            atomic_store_i64(global_addr("pcc_gc_scalar_sweep_token"), 0, token, "release")
            store_i64(global_addr("pcc_gc_scalar_sweep_producer_token"), 0, token)
            atomic_store_i64(global_addr("pcc_gc_scalar_sweep_state"), 0, _BUILDING, "release")
    pcc_py_gc_minor_graph_unlock()


@c_abi_export("pcc_gc_scalar_sweep_try_enqueue")
def pcc_gc_scalar_sweep_try_enqueue(obj, node) -> i64:
    token: i64 = load_i64(global_addr("pcc_gc_scalar_sweep_producer_token"), 0)
    if token == 0 or pcc_thread_owns_stopped_world() == 0:
        return 0
    # BUILDING is changed only by this stopped-world producer. Once full,
    # avoid a lock and index/pin/provenance probes for every remaining object.
    # This counter records capacity rejections, not proven-eligible objects.
    if (load_i64(global_addr("pcc_gc_scalar_sweep_state"), 0) == _BUILDING
            and load_i64(global_addr("pcc_gc_scalar_sweep_token"), 0) == token
            and (load_i64(global_addr("pcc_gc_scalar_sweep_count"), 0) >= _MAX_TASKS
                 or load_i64(global_addr("pcc_gc_scalar_sweep_bytes"), 0) > _MAX_BYTES - 16)):
        atomic_rmw_i64("add", global_addr("pcc_gc_scalar_sweep_cap_rejections"), 0, 1, "relaxed")
        return 0
    # All other PASS2 types keep the old path without a new graph transaction.
    # STW ownership protects these initial scalar/header reads. GC1/2 do
    # not set the moving collectors' MALLOC_ALLOC bit; the LIVE object-family
    # granule checked below is the exact allocator-origin authority.
    tag: i64 = load_i32(obj, 8)
    flags: i64 = load_i32(obj, 12)
    if ((tag != abi_constant("object.type.int")
         and tag != abi_constant("object.type.float")
         and tag != abi_constant("object.type.str"))
            or (flags & (_CANDIDATE | 8)) != (_CANDIDATE | 8)
            or (flags & _EXCLUDED) != 0):
        return 0
    pcc_py_gc_minor_graph_lock()
    if (
        load_i64(global_addr("pcc_gc_scalar_sweep_state"), 0) != _BUILDING
        or load_i64(global_addr("pcc_gc_scalar_sweep_token"), 0) != token
        or ptr_is_null(node) != 0
        or ptr_eq(pcc_gc_object_index_find(obj), node) == 0
        or ptr_eq(load_ptr(node, 0), obj) == 0
        or load_i64(node, 32) != 0
    ):
        pcc_py_gc_minor_graph_unlock()
        return 0
    flags: i64 = load_i32(obj, 12)
    tag: i64 = load_i32(obj, 8)
    if (
        (flags & (_CANDIDATE | 8)) != (_CANDIDATE | 8)
        or (flags & _EXCLUDED) != 0
        or (tag != abi_constant("object.type.int")
            and tag != abi_constant("object.type.float")
            and tag != abi_constant("object.type.str"))
        or pcc_gc_object_node_is_address_pinned(node, obj) != 0
        or ptr_is_null(load_ptr(node, 24)) == 0
        or ptr_is_null(load_ptr(node, 48)) == 0
        or load_i64(node, 80) != 0
        or pcc_gc_granule_is_object_start(obj) != 1
        or pcc_gc_managed_pointer_index_contains(obj) != 0
        or ptr_is_null(pcc_gc_identity_index_find(obj)) == 0
    ):
        pcc_py_gc_minor_graph_unlock()
        return 0
    count: i64 = load_i64(global_addr("pcc_gc_scalar_sweep_count"), 0)
    pending_bytes: i64 = load_i64(global_addr("pcc_gc_scalar_sweep_bytes"), 0)
    usable: i64 = malloc_usable_size(obj)
    if count >= _MAX_TASKS or usable <= 0 or usable > _MAX_BYTES - pending_bytes:
        atomic_rmw_i64("add", global_addr("pcc_gc_scalar_sweep_cap_rejections"), 0, 1, "relaxed")
        pcc_py_gc_minor_graph_unlock()
        return 0
    tasks = global_addr("pcc_gc_scalar_sweep_tasks")
    offset: i64 = count * _TASK_BYTES
    store_ptr(tasks, offset + _OBJECT, obj)
    store_ptr(tasks, offset + _NODE, node)
    store_i64(tasks, offset + _USABLE, usable)
    store_i64(tasks, offset + _TAG, tag)
    store_i64(tasks, offset + _RESULT, 0)
    # Commit first-death accounting once, without retiring its index/node
    # or overwriting the reference-count word. Pending physical bytes have
    # a separate counter; quarantine never restores a dead object to live.
    store_i64(node, 32, -token)
    store_i32(obj, 12, flags | 4 | _DEALLOCATING)
    pcc_gc_live_bytes_subtract(load_i64(node, 8))
    pcc_gc_note_scalar_sweep_claim(obj)
    atomic_store_i64(global_addr("pcc_gc_scalar_sweep_count"), 0, count + 1, "release")
    atomic_store_i64(global_addr("pcc_gc_scalar_sweep_bytes"), 0, pending_bytes + usable, "release")
    atomic_rmw_i64("add", global_addr("pcc_gc_scalar_sweep_enqueued"), 0, 1, "relaxed")
    pcc_py_gc_minor_graph_unlock()
    return 1


@c_abi_export("pcc_gc_scalar_sweep_seal")
def pcc_gc_scalar_sweep_seal() -> None:
    token: i64 = load_i64(global_addr("pcc_gc_scalar_sweep_producer_token"), 0)
    if token == 0:
        return
    if pcc_thread_owns_stopped_world() == 0:
        pcc_platform_abort()
        return
    pcc_py_gc_minor_graph_lock()
    if (load_i64(global_addr("pcc_gc_scalar_sweep_token"), 0) == token
            and load_i64(global_addr("pcc_gc_scalar_sweep_state"), 0) == _BUILDING):
        count: i64 = load_i64(global_addr("pcc_gc_scalar_sweep_count"), 0)
        atomic_rmw_i64("add", global_addr("pcc_gc_scalar_sweep_sealed"), 0, count, "relaxed")
        atomic_store_i64(global_addr("pcc_gc_scalar_sweep_state"), 0,
                         _READY if count != 0 else _EMPTY, "release")
    pcc_py_gc_minor_graph_unlock()


@c_abi_export("pcc_gc_scalar_sweep_owns")
def pcc_gc_scalar_sweep_owns(obj) -> i64:
    # The normally empty fast path avoids adding an index lookup to every
    # pointer_unregister. Under a reservation, negative node ownership is
    # authoritative, including a failed granule/index retirement quarantine.
    if atomic_load_i64(global_addr("pcc_gc_scalar_sweep_state"), 0, "acquire") == _EMPTY:
        return 0
    pcc_py_gc_minor_graph_lock()
    node = pcc_gc_object_index_find(obj)
    owned: i64 = 0
    if ptr_is_null(node) == 0 and load_i64(node, 32) < 0:
        owned = 1
    pcc_py_gc_minor_graph_unlock()
    return owned


@c_abi_export("pcc_gc_scalar_sweep_drain")
def pcc_gc_scalar_sweep_drain(worker: i64, closing: i64, expected_token: i64) -> i64:
    if worker != 0 and pcc_gc_is_cms_worker() == 0:
        return 0
    if closing == 0 and atomic_load_i64(global_addr("pcc_gc_scalar_sweep_state"), 0, "acquire") != _READY:
        return 0
    # Closing must take the lock even after an empty read, ordering behind a
    # producer that was admitted before stop's state transition.
    pcc_py_gc_minor_graph_lock()
    if (load_i64(global_addr("pcc_gc_scalar_sweep_state"), 0) != _READY
            or (expected_token != 0 and load_i64(global_addr("pcc_gc_scalar_sweep_token"), 0) != expected_token)
            or (worker == 0 and atomic_load_i32(global_addr("pcc_gc_cms_worker_started"), 0, "acquire") == 1
                and atomic_load_i32(global_addr("pcc_gc_cms_worker_stop_requested"), 0, "acquire") == 0)):
        pcc_py_gc_minor_graph_unlock()
        return 0
    atomic_store_i64(global_addr("pcc_gc_scalar_sweep_state"), 0, _DRAINING, "release")
    token: i64 = load_i64(global_addr("pcc_gc_scalar_sweep_token"), 0)
    count: i64 = load_i64(global_addr("pcc_gc_scalar_sweep_count"), 0)
    tasks = global_addr("pcc_gc_scalar_sweep_tasks")
    retired: i64 = 0
    failed: i64 = 0
    failed_bytes: i64 = 0
    i: i64 = 0
    while i < count:
        offset: i64 = i * _TASK_BYTES
        obj = load_ptr(tasks, offset + _OBJECT)
        node = load_ptr(tasks, offset + _NODE)
        # Structural ownership cannot legitimately change while pending.
        # Do not dereference an unverified stale node or call it quarantined.
        if (ptr_is_null(node) != 0
                or ptr_eq(pcc_gc_object_index_find(obj), node) == 0
                or ptr_eq(load_ptr(node, 0), obj) == 0
                or load_i64(node, 32) != -token):
            pcc_platform_abort()
            return 0
        valid: i64 = 0
        flags: i64 = load_i32(obj, 12)
        if (load_i32(obj, 8) == load_i64(tasks, offset + _TAG)
                and (flags & (_CANDIDATE | 8 | 4 | _DEALLOCATING)) == (_CANDIDATE | 8 | 4 | _DEALLOCATING)
                and (flags & (_EXCLUDED & ~_DEALLOCATING)) == 0
                and pcc_gc_object_node_is_address_pinned(node, obj) == 0
                and ptr_is_null(load_ptr(node, 24)) != 0
                and ptr_is_null(load_ptr(node, 48)) != 0
                and load_i64(node, 80) == 0
                and pcc_gc_granule_is_object_start(obj) == 1
                and pcc_gc_managed_pointer_index_contains(obj) == 0
                and ptr_is_null(pcc_gc_identity_index_find(obj)) != 0):
            # No allocation or callback. Retire provenance before removing
            # the index: a failure leaves the negative owner discoverable.
            if pcc_gc_granule_object_retire(obj) == 1:
                removed = pcc_gc_object_index_remove(obj)
                if ptr_eq(removed, node) != 0:
                    store_i64(node, 32, 1)
                    pcc_gc_object_node_unlink(node)
                    pcc_gc_object_node_release(node)
                    store_ptr(tasks, offset + _NODE, null())
                    store_i64(obj, 0, 0)
                    store_i64(tasks, offset + _RESULT, 1)
                    retired = retired + 1
                    valid = 1
                else:
                    # Exact node identity was checked under this same lock.
                    # An unexpected remove result is structural corruption,
                    # not a recoverable quarantine with a possibly lost index.
                    pcc_platform_abort()
                    return 0
        if valid == 0:
            # Do not recycle the failed task or retry its uncertain transition.
            # Its exact negative-token node and index stay durably owned.
            store_i64(tasks, offset + _RESULT, -1)
            failed = failed + 1
            failed_bytes = failed_bytes + load_i64(tasks, offset + _USABLE)
        i = i + 1
    atomic_rmw_i64("add", global_addr("pcc_gc_scalar_sweep_retired"), 0, retired, "relaxed")
    if worker != 0:
        atomic_rmw_i64("add", global_addr("pcc_gc_scalar_sweep_worker_retired"), 0, retired, "relaxed")
        atomic_store_i64(global_addr("pcc_gc_scalar_sweep_worker_thread"), 0, pcc_current_thread_id(), "release")
    else:
        atomic_rmw_i64("add", global_addr("pcc_gc_scalar_sweep_local_retired"), 0, retired, "relaxed")
    atomic_rmw_i64("add", global_addr("pcc_gc_scalar_sweep_quarantined"), 0, failed, "relaxed")
    # Every surviving managed node is now a durable quarantine entry. There
    # are no private unretired nodes across this outer unlock, which may park.
    pcc_py_gc_minor_graph_unlock()
    i = 0
    while i < count:
        offset = i * _TASK_BYTES
        if load_i64(tasks, offset + _RESULT) == 1:
            obj = load_ptr(tasks, offset + _OBJECT)
            free(obj)
            store_ptr(tasks, offset + _OBJECT, null())
        i = i + 1
    atomic_rmw_i64("add", global_addr("pcc_gc_scalar_sweep_released"), 0, retired, "relaxed")
    pcc_py_gc_minor_graph_lock()
    atomic_store_i64(global_addr("pcc_gc_scalar_sweep_count"), 0, failed, "release")
    atomic_store_i64(global_addr("pcc_gc_scalar_sweep_bytes"), 0, failed_bytes, "release")
    atomic_store_i64(global_addr("pcc_gc_scalar_sweep_state"), 0,
                     _QUARANTINED if failed != 0 else _EMPTY, "release")
    pcc_py_gc_minor_graph_unlock()
    return retired


@c_abi_export("pcc_gc_scalar_sweep_close")
def pcc_gc_scalar_sweep_close() -> i64:
    token: i64 = load_i64(global_addr("pcc_gc_scalar_sweep_producer_token"), 0)
    store_i64(global_addr("pcc_gc_scalar_sweep_producer_token"), 0, 0)
    if token == 0:
        return 0
    if pcc_thread_owns_stopped_world() != 0:
        pcc_platform_abort()
        return 0
    # Existing raw-only callers have no scalar tasks and need not seal an
    # empty reservation. A nonempty unsealed scope is a broken owner contract.
    pcc_py_gc_minor_graph_lock()
    if (load_i64(global_addr("pcc_gc_scalar_sweep_token"), 0) == token
            and load_i64(global_addr("pcc_gc_scalar_sweep_state"), 0) == _BUILDING):
        if load_i64(global_addr("pcc_gc_scalar_sweep_count"), 0) != 0:
            pcc_platform_abort()
            return 0
        atomic_store_i64(global_addr("pcc_gc_scalar_sweep_state"), 0, _EMPTY, "release")
    pcc_py_gc_minor_graph_unlock()
    return pcc_gc_scalar_sweep_drain(0, 0, token)


@c_abi_export("pcc_gc_scalar_sweep_counter")
def pcc_gc_scalar_sweep_counter(metric: i64) -> i64:
    if metric == 0:
        return atomic_load_i64(global_addr("pcc_gc_scalar_sweep_state"), 0, "acquire")
    if metric == 1:
        return atomic_load_i64(global_addr("pcc_gc_scalar_sweep_count"), 0, "acquire")
    if metric == 2:
        return atomic_load_i64(global_addr("pcc_gc_scalar_sweep_bytes"), 0, "acquire")
    if metric == 3:
        return atomic_load_i64(global_addr("pcc_gc_scalar_sweep_enqueued"), 0, "acquire")
    if metric == 4:
        return atomic_load_i64(global_addr("pcc_gc_scalar_sweep_retired"), 0, "acquire")
    if metric == 5:
        return atomic_load_i64(global_addr("pcc_gc_scalar_sweep_released"), 0, "acquire")
    if metric == 6:
        return atomic_load_i64(global_addr("pcc_gc_scalar_sweep_worker_retired"), 0, "acquire")
    if metric == 7:
        return atomic_load_i64(global_addr("pcc_gc_scalar_sweep_local_retired"), 0, "acquire")
    if metric == 8:
        return atomic_load_i64(global_addr("pcc_gc_scalar_sweep_worker_thread"), 0, "acquire")
    if metric == 9:
        return atomic_load_i64(global_addr("pcc_gc_scalar_sweep_quarantined"), 0, "acquire")
    if metric == 10:
        return atomic_load_i64(global_addr("pcc_gc_scalar_sweep_cap_rejections"), 0, "acquire")
    if metric == 11:
        return atomic_load_i64(global_addr("pcc_gc_scalar_sweep_token"), 0, "acquire")
    if metric == 12:
        return atomic_load_i64(global_addr("pcc_gc_scalar_sweep_sealed"), 0, "acquire")
    return -1
