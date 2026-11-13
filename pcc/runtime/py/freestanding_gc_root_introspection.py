"""Read-only root-registry introspection for the freestanding GC kernel."""

from pcc import i64
from pcc.extern import c_abi_export, c_int64, c_ptr, c_void, extern
from pcc.unsafe import (
    gc_backend_current,
    global_load_ptr,
    load_i64,
    load_ptr,
    logical_shift_right_i64,
    ptr_add,
    ptr_diff,
    ptr_eq,
    ptr_is_null,
)


__pcc_freestanding__ = True


pcc_thread_native_root_contains = extern(
    "pcc_thread_native_root_contains", (c_ptr,), c_int64
)
pcc_py_gc_minor_graph_lock = extern("pcc_py_gc_minor_graph_lock", (), c_void)
pcc_py_gc_minor_graph_unlock = extern("pcc_py_gc_minor_graph_unlock", (), c_void)
# GC0 registrations change the scheduler-root list under its own word only.
pcc_gc_scheduler_root_list_acquire = extern(
    "pcc_gc_scheduler_root_list_acquire", (), c_void
)
pcc_gc_scheduler_root_list_release = extern(
    "pcc_gc_scheduler_root_list_release", (), c_void
)


pcc_gc_continuation_root_list_acquire = extern(
    "pcc_gc_continuation_root_list_acquire", (), c_void
)
pcc_gc_continuation_root_list_release = extern(
    "pcc_gc_continuation_root_list_release", (), c_void
)
pcc_thread_owns_stopped_world = extern("pcc_thread_owns_stopped_world", (), c_int64)
pcc_stop_the_world = extern("pcc_stop_the_world", (), c_int64)
pcc_resume_world = extern("pcc_resume_world", (), c_int64)
pcc_thread_local_root_slots_stopped = extern("pcc_thread_local_root_slots_stopped", (), c_int64)
pcc_platform_abort = extern("pcc_platform_abort", (), c_void)


@c_abi_export("pcc_gc_scheduler_root_count")
def pcc_gc_scheduler_root_count() -> i64:
    gc_backend_current()
    pcc_py_gc_minor_graph_lock()
    pcc_gc_scheduler_root_list_acquire()
    node = global_load_ptr("pcc_gc_scheduler_root_head")
    count: i64 = 0
    while ptr_is_null(node) == 0:
        # A node a thread keeps for reuse has an empty slot and roots
        # nothing (pcc_gc_scheduler_root_tls_cache).
        if ptr_is_null(load_ptr(node, 0)) == 0:
            count = count + 1
        node = load_ptr(node, 8)
    pcc_gc_scheduler_root_list_release()
    pcc_py_gc_minor_graph_unlock()
    return count


@c_abi_export("pcc_gc_frame_root_slot_count")
def pcc_gc_frame_root_slot_count() -> i64:
    gc_backend_current()
    stopped_here: i64 = 0
    if pcc_thread_owns_stopped_world() == 0:
        if pcc_stop_the_world() != 0:
            pcc_platform_abort()
            return 0
        stopped_here = 1
    pcc_py_gc_minor_graph_lock()
    node = global_load_ptr("pcc_gc_frame_head")
    count: i64 = pcc_thread_local_root_slots_stopped()
    while ptr_is_null(node) == 0:
        count = count + load_i64(node, 40)
        node = load_ptr(node, 16)
    pcc_py_gc_minor_graph_unlock()
    if stopped_here != 0:
        pcc_resume_world()
    return count


@c_abi_export("pcc_gc_continuation_root_slot_count")
def pcc_gc_continuation_root_slot_count() -> i64:
    gc_backend_current()
    pcc_py_gc_minor_graph_lock()
    pcc_gc_continuation_root_list_acquire()
    node = global_load_ptr("pcc_gc_continuation_root_head")
    count: i64 = 0
    while ptr_is_null(node) == 0:
        count = count + load_i64(node, 24)
        node = load_ptr(node, 16)
    pcc_gc_continuation_root_list_release()
    pcc_py_gc_minor_graph_unlock()
    return count


@c_abi_export("pcc_gc_coroutine_root_score")
def pcc_gc_coroutine_root_score() -> i64:
    return (
        pcc_gc_scheduler_root_count()
        + pcc_gc_frame_root_slot_count()
        + pcc_gc_continuation_root_slot_count()
    )


@c_abi_export("pcc_gc_root_slot_in_span")
def pcc_gc_root_slot_in_span(slot, slots, count: i64) -> i64:
    if ptr_is_null(slot) != 0 or ptr_is_null(slots) != 0 or count <= 0:
        return 0
    if count <= 100000:
        # Registered maps admit at most 100000 slots. Compare the slot's
        # byte offset modulo the raw 64-bit address space, not pointer order:
        # a span can cross the signed-address boundary or wrap at 2**64.
        # Alignment is relative to slots, which need not be 8-byte aligned.
        # Larger direct-call counts retain the original loop unchanged.
        delta: i64 = ptr_diff(slot, slots)
        if (delta & 7) != 0:
            return 0
        return 1 if logical_shift_right_i64(delta, 3) < count else 0
    i: i64 = 0
    while i < count:
        if ptr_eq(ptr_add(slots, i * 8), slot) != 0:
            return 1
        i = i + 1
    return 0


@c_abi_export("pcc_gc_slot_is_runtime_root")
def pcc_gc_slot_is_runtime_root(slot) -> i64:
    gc_backend_current()
    if ptr_is_null(slot) != 0:
        return 0
    # Owner-local handles need no graph/root-list lock. This reads only the
    # calling thread's chain; cross-thread walks remain stopped-world only.
    if pcc_thread_native_root_contains(slot) != 0:
        return 1
    pcc_py_gc_minor_graph_lock()
    node = global_load_ptr("pcc_gc_frame_head")
    while ptr_is_null(node) == 0:
        if pcc_gc_root_slot_in_span(slot, load_ptr(node, 8), load_i64(node, 40)) != 0:
            pcc_py_gc_minor_graph_unlock()
            return 1
        node = load_ptr(node, 16)
    pcc_gc_continuation_root_list_acquire()
    node = global_load_ptr("pcc_gc_continuation_root_head")
    while ptr_is_null(node) == 0:
        if pcc_gc_root_slot_in_span(slot, load_ptr(node, 8), load_i64(node, 24)) != 0:
            pcc_gc_continuation_root_list_release()
            pcc_py_gc_minor_graph_unlock()
            return 1
        node = load_ptr(node, 16)
    pcc_gc_continuation_root_list_release()
    found: i64 = 0
    pcc_gc_scheduler_root_list_acquire()
    node = global_load_ptr("pcc_gc_scheduler_root_head")
    while ptr_is_null(node) == 0 and found == 0:
        if ptr_eq(load_ptr(node, 0), slot) != 0:
            found = 1
        node = load_ptr(node, 8)
    pcc_gc_scheduler_root_list_release()
    pcc_py_gc_minor_graph_unlock()
    return found
