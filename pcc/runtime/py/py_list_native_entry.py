"""No-poll capture and activation ownership for native list operations.

The public ABI still transfers direct PyObject pointers with its existing
pin/lease rules. These wrappers publish borrowed inputs before the managed
body's first poll. A frame and its range table live in the enclosing wrapper;
the body may bind/clear scratch groups in any order without global nodes.
Neither native blocking admission nor relocation-safe gates change here.
"""
__pcc_runtime_port__ = True

from pcc.extern import c_abi_export, c_int64, c_ptr, c_void, extern
from pcc.unsafe import (
    global_addr,
    is_tagged_int,
    load_i32,
    load_ptr,
    memset,
    null,
    ptr_is_null,
    stack_alloc,
    store_ptr,
)

pcc_thread_no_park_enter = extern("pcc_thread_no_park_enter", (), c_void)
pcc_thread_no_park_exit = extern("pcc_thread_no_park_exit", (), c_void)
pcc_gc_native_frame_enter = extern(
    "pcc_gc_native_frame_enter", (c_ptr, c_ptr, c_int64), c_int64
)
pcc_gc_native_frame_leave = extern("pcc_gc_native_frame_leave", (c_ptr,), c_int64)
pcc_gc_native_root_bind_range = extern(
    "pcc_gc_native_root_bind_range", (c_ptr, c_int64), c_ptr
)

# Each wrapper defines its scratch layout as local typed literals. Runtime
# archive linking need not retain this module's initialization for 40/128/8.
# Six equality/pop/remove entries keep their frame on GC0: their candidate
# and result cells were unconditional scheduler roots before this port.
# pcc_gc_nonmoving_count_free (fixed with the collector) skips the activation
# protocol for the nonmoving count-free GC1/GC2: their stopped-world seed and
# remark scan every thread's stack conservatively, which covers these inputs.

pcc_list_body_pcc_list_grow_for_mutation = extern(
    "pcc_list_body_pcc_list_grow_for_mutation", (c_ptr, c_int64), c_ptr
)

@c_abi_export("pcc_list_grow_for_mutation")
def pcc_list_grow_for_mutation(l: c_ptr, want: int) -> c_ptr:
    # GC0 disabled frame tracking needs no native activation protocol here.
    # Its original input owners/counts still protect the body's raw inputs.
    if (load_i32(global_addr("pcc_gc_nonmoving_count_free"), 0) != 0
            or (load_i32(global_addr("pcc_gc_config_initialized"), 0) != 0
                and load_i32(global_addr("pcc_gc_backend_selected"), 0) == 0
                and load_i32(global_addr("pcc_gc_backend0_frame_roots_enabled"), 0) == 0)):
        inputs = stack_alloc(1 * 8)
        store_ptr(inputs, 0 * 8, l)
        return pcc_list_body_pcc_list_grow_for_mutation(inputs, want)
    native_frame_bytes: int = 40
    native_range_capacity: int = 8
    native_ranges_bytes: int = 128
    pcc_thread_no_park_enter()
    frame = stack_alloc(40)  # native_frame_bytes: fixed descriptor layout
    memset(frame, 0, native_frame_bytes)
    ranges = stack_alloc(128)  # native_ranges_bytes: eight local ranges
    inputs = stack_alloc(1 * 8)
    store_ptr(inputs, 0 * 8, l)
    pcc_gc_native_frame_enter(frame, ranges, native_range_capacity)
    if (load_i32(global_addr("pcc_gc_config_initialized"), 0) == 0
            or load_i32(global_addr("pcc_gc_backend_selected"), 0) != 0
            or load_i32(global_addr("pcc_gc_backend0_frame_roots_enabled"), 0) != 0):
        pcc_gc_native_root_bind_range(inputs, -1)
    pcc_thread_no_park_exit()
    result = pcc_list_body_pcc_list_grow_for_mutation(inputs, want)
    pcc_gc_native_frame_leave(frame)
    return result

pcc_list_body_py_list_new = extern(
    "pcc_list_body_py_list_new", (c_int64,), c_ptr
)

@c_abi_export("py_list_new")
def py_list_new(initial_capacity: int) -> c_ptr:
    # GC0 disabled frame tracking needs no native activation protocol here.
    # Its original input owners/counts still protect the body's raw inputs.
    if (load_i32(global_addr("pcc_gc_nonmoving_count_free"), 0) != 0
            or (load_i32(global_addr("pcc_gc_config_initialized"), 0) != 0
                and load_i32(global_addr("pcc_gc_backend_selected"), 0) == 0
                and load_i32(global_addr("pcc_gc_backend0_frame_roots_enabled"), 0) == 0)):
        return pcc_list_body_py_list_new(initial_capacity)
    native_frame_bytes: int = 40
    native_range_capacity: int = 8
    native_ranges_bytes: int = 128
    pcc_thread_no_park_enter()
    frame = stack_alloc(40)  # native_frame_bytes: fixed descriptor layout
    memset(frame, 0, native_frame_bytes)
    ranges = stack_alloc(128)  # native_ranges_bytes: eight local ranges
    pcc_gc_native_frame_enter(frame, ranges, native_range_capacity)
    pcc_thread_no_park_exit()
    result = pcc_list_body_py_list_new(initial_capacity)
    pcc_gc_native_frame_leave(frame)
    return result

pcc_list_body_py_list_from_static_items = extern(
    "pcc_list_body_py_list_from_static_items", (c_ptr, c_int64), c_ptr
)

@c_abi_export("py_list_from_static_items")
def py_list_from_static_items(items: c_ptr, count: int) -> c_ptr:
    # GC0 disabled frame tracking needs no native activation protocol here.
    # Its original input owners/counts still protect the body's raw inputs.
    if (load_i32(global_addr("pcc_gc_nonmoving_count_free"), 0) != 0
            or (load_i32(global_addr("pcc_gc_config_initialized"), 0) != 0
                and load_i32(global_addr("pcc_gc_backend_selected"), 0) == 0
                and load_i32(global_addr("pcc_gc_backend0_frame_roots_enabled"), 0) == 0)):
        return pcc_list_body_py_list_from_static_items(items, count)
    native_frame_bytes: int = 40
    native_range_capacity: int = 8
    native_ranges_bytes: int = 128
    pcc_thread_no_park_enter()
    frame = stack_alloc(40)  # native_frame_bytes: fixed descriptor layout
    memset(frame, 0, native_frame_bytes)
    ranges = stack_alloc(128)  # native_ranges_bytes: eight local ranges
    pcc_gc_native_frame_enter(frame, ranges, native_range_capacity)
    pcc_thread_no_park_exit()
    result = pcc_list_body_py_list_from_static_items(items, count)
    pcc_gc_native_frame_leave(frame)
    return result

pcc_list_body_py_list_from_owned_roots = extern(
    "pcc_list_body_py_list_from_owned_roots", (c_ptr, c_int64, c_ptr), c_int64
)

@c_abi_export("py_list_from_owned_roots")
def py_list_from_owned_roots(sources: c_ptr, count: int, output: c_ptr) -> int:
    # GC0 disabled frame tracking needs no native activation protocol here.
    # Its original input owners/counts still protect the body's raw inputs.
    if (load_i32(global_addr("pcc_gc_nonmoving_count_free"), 0) != 0
            or (load_i32(global_addr("pcc_gc_config_initialized"), 0) != 0
                and load_i32(global_addr("pcc_gc_backend_selected"), 0) == 0
                and load_i32(global_addr("pcc_gc_backend0_frame_roots_enabled"), 0) == 0)):
        return pcc_list_body_py_list_from_owned_roots(sources, count, output)
    native_frame_bytes: int = 40
    native_range_capacity: int = 8
    native_ranges_bytes: int = 128
    pcc_thread_no_park_enter()
    frame = stack_alloc(40)  # native_frame_bytes: fixed descriptor layout
    memset(frame, 0, native_frame_bytes)
    ranges = stack_alloc(128)  # native_ranges_bytes: eight local ranges
    pcc_gc_native_frame_enter(frame, ranges, native_range_capacity)
    pcc_thread_no_park_exit()
    result = pcc_list_body_py_list_from_owned_roots(sources, count, output)
    pcc_gc_native_frame_leave(frame)
    return result

pcc_list_body_py_list_append_from_slots = extern(
    "pcc_list_body_py_list_append_from_slots", (c_ptr, c_ptr, c_int64), c_void
)

@c_abi_export("py_list_append_from_slots")
def py_list_append_from_slots(list_slot: c_ptr, item_slot: c_ptr, borrowed_mask: int) -> None:
    # GC0 disabled frame tracking needs no native activation protocol here.
    # Its original input owners/counts still protect the body's raw inputs.
    if (load_i32(global_addr("pcc_gc_nonmoving_count_free"), 0) != 0
            or (load_i32(global_addr("pcc_gc_config_initialized"), 0) != 0
                and load_i32(global_addr("pcc_gc_backend_selected"), 0) == 0
                and load_i32(global_addr("pcc_gc_backend0_frame_roots_enabled"), 0) == 0)):
        pcc_list_body_py_list_append_from_slots(list_slot, item_slot, borrowed_mask)
        return
    native_frame_bytes: int = 40
    native_range_capacity: int = 8
    native_ranges_bytes: int = 128
    pcc_thread_no_park_enter()
    frame = stack_alloc(40)  # native_frame_bytes: fixed descriptor layout
    memset(frame, 0, native_frame_bytes)
    ranges = stack_alloc(128)  # native_ranges_bytes: eight local ranges
    pcc_gc_native_frame_enter(frame, ranges, native_range_capacity)
    pcc_thread_no_park_exit()
    pcc_list_body_py_list_append_from_slots(list_slot, item_slot, borrowed_mask)
    pcc_gc_native_frame_leave(frame)
    return

pcc_list_body_py_list_append = extern(
    "pcc_list_body_py_list_append", (c_ptr,), c_void
)

@c_abi_export("py_list_append")
def py_list_append(lst: c_ptr, item: c_ptr) -> None:
    # GC0 disabled frame tracking needs no native activation protocol here.
    # Its original input owners/counts still protect the body's raw inputs.
    if (load_i32(global_addr("pcc_gc_nonmoving_count_free"), 0) != 0
            or (load_i32(global_addr("pcc_gc_config_initialized"), 0) != 0
                and load_i32(global_addr("pcc_gc_backend_selected"), 0) == 0
                and load_i32(global_addr("pcc_gc_backend0_frame_roots_enabled"), 0) == 0)):
        inputs = stack_alloc(2 * 8)
        store_ptr(inputs, 0 * 8, lst)
        store_ptr(inputs, 1 * 8, item)
        pcc_list_body_py_list_append(inputs)
        return
    native_frame_bytes: int = 40
    native_range_capacity: int = 8
    native_ranges_bytes: int = 128
    pcc_thread_no_park_enter()
    frame = stack_alloc(40)  # native_frame_bytes: fixed descriptor layout
    memset(frame, 0, native_frame_bytes)
    ranges = stack_alloc(128)  # native_ranges_bytes: eight local ranges
    inputs = stack_alloc(2 * 8)
    store_ptr(inputs, 0 * 8, lst)
    store_ptr(inputs, 1 * 8, item)
    pcc_gc_native_frame_enter(frame, ranges, native_range_capacity)
    if (load_i32(global_addr("pcc_gc_config_initialized"), 0) == 0
            or load_i32(global_addr("pcc_gc_backend_selected"), 0) != 0
            or load_i32(global_addr("pcc_gc_backend0_frame_roots_enabled"), 0) != 0):
        pcc_gc_native_root_bind_range(inputs, -2)
    pcc_thread_no_park_exit()
    pcc_list_body_py_list_append(inputs)
    pcc_gc_native_frame_leave(frame)
    return

pcc_list_body_py_list_append_fresh_native_instance = extern(
    "pcc_list_body_py_list_append_fresh_native_instance", (c_ptr,), c_void
)

@c_abi_export("py_list_append_fresh_native_instance")
def py_list_append_fresh_native_instance(lst: c_ptr, item: c_ptr) -> None:
    # GC0 disabled frame tracking needs no native activation protocol here.
    # Its original input owners/counts still protect the body's raw inputs.
    if (load_i32(global_addr("pcc_gc_nonmoving_count_free"), 0) != 0
            or (load_i32(global_addr("pcc_gc_config_initialized"), 0) != 0
                and load_i32(global_addr("pcc_gc_backend_selected"), 0) == 0
                and load_i32(global_addr("pcc_gc_backend0_frame_roots_enabled"), 0) == 0)):
        inputs = stack_alloc(2 * 8)
        store_ptr(inputs, 0 * 8, lst)
        store_ptr(inputs, 1 * 8, item)
        pcc_list_body_py_list_append_fresh_native_instance(inputs)
        return
    native_frame_bytes: int = 40
    native_range_capacity: int = 8
    native_ranges_bytes: int = 128
    pcc_thread_no_park_enter()
    frame = stack_alloc(40)  # native_frame_bytes: fixed descriptor layout
    memset(frame, 0, native_frame_bytes)
    ranges = stack_alloc(128)  # native_ranges_bytes: eight local ranges
    inputs = stack_alloc(2 * 8)
    store_ptr(inputs, 0 * 8, lst)
    store_ptr(inputs, 1 * 8, item)
    pcc_gc_native_frame_enter(frame, ranges, native_range_capacity)
    if (load_i32(global_addr("pcc_gc_config_initialized"), 0) == 0
            or load_i32(global_addr("pcc_gc_backend_selected"), 0) != 0
            or load_i32(global_addr("pcc_gc_backend0_frame_roots_enabled"), 0) != 0):
        pcc_gc_native_root_bind_range(inputs, -2)
    pcc_thread_no_park_exit()
    pcc_list_body_py_list_append_fresh_native_instance(inputs)
    pcc_gc_native_frame_leave(frame)
    return

pcc_list_body_py_list_get = extern(
    "pcc_list_body_py_list_get", (c_ptr, c_int64), c_ptr
)

@c_abi_export("py_list_get")
def py_list_get(lst: c_ptr, i: int) -> c_ptr:
    # GC0 disabled frame tracking needs no native activation protocol here.
    # Its original input owners/counts still protect the body's raw inputs.
    if (load_i32(global_addr("pcc_gc_nonmoving_count_free"), 0) != 0
            or (load_i32(global_addr("pcc_gc_config_initialized"), 0) != 0
                and load_i32(global_addr("pcc_gc_backend_selected"), 0) == 0
                and load_i32(global_addr("pcc_gc_backend0_frame_roots_enabled"), 0) == 0)):
        inputs = stack_alloc(1 * 8)
        store_ptr(inputs, 0 * 8, lst)
        return pcc_list_body_py_list_get(inputs, i)
    native_frame_bytes: int = 40
    native_range_capacity: int = 8
    native_ranges_bytes: int = 128
    pcc_thread_no_park_enter()
    frame = stack_alloc(40)  # native_frame_bytes: fixed descriptor layout
    memset(frame, 0, native_frame_bytes)
    ranges = stack_alloc(128)  # native_ranges_bytes: eight local ranges
    inputs = stack_alloc(1 * 8)
    store_ptr(inputs, 0 * 8, lst)
    pcc_gc_native_frame_enter(frame, ranges, native_range_capacity)
    if (load_i32(global_addr("pcc_gc_config_initialized"), 0) == 0
            or load_i32(global_addr("pcc_gc_backend_selected"), 0) != 0
            or load_i32(global_addr("pcc_gc_backend0_frame_roots_enabled"), 0) != 0):
        pcc_gc_native_root_bind_range(inputs, -1)
    pcc_thread_no_park_exit()
    result = pcc_list_body_py_list_get(inputs, i)
    pcc_gc_native_frame_leave(frame)
    return result

pcc_list_body_py_list_getitem = extern(
    "pcc_list_body_py_list_getitem", (c_ptr, c_int64), c_ptr
)

@c_abi_export("py_list_getitem")
def py_list_getitem(lst: c_ptr, i: int) -> c_ptr:
    # GC0 disabled frame tracking needs no native activation protocol here.
    # Its original input owners/counts still protect the body's raw inputs.
    if (load_i32(global_addr("pcc_gc_nonmoving_count_free"), 0) != 0
            or (load_i32(global_addr("pcc_gc_config_initialized"), 0) != 0
                and load_i32(global_addr("pcc_gc_backend_selected"), 0) == 0
                and load_i32(global_addr("pcc_gc_backend0_frame_roots_enabled"), 0) == 0)):
        inputs = stack_alloc(1 * 8)
        store_ptr(inputs, 0 * 8, lst)
        return pcc_list_body_py_list_getitem(inputs, i)
    native_frame_bytes: int = 40
    native_range_capacity: int = 8
    native_ranges_bytes: int = 128
    pcc_thread_no_park_enter()
    frame = stack_alloc(40)  # native_frame_bytes: fixed descriptor layout
    memset(frame, 0, native_frame_bytes)
    ranges = stack_alloc(128)  # native_ranges_bytes: eight local ranges
    inputs = stack_alloc(1 * 8)
    store_ptr(inputs, 0 * 8, lst)
    pcc_gc_native_frame_enter(frame, ranges, native_range_capacity)
    if (load_i32(global_addr("pcc_gc_config_initialized"), 0) == 0
            or load_i32(global_addr("pcc_gc_backend_selected"), 0) != 0
            or load_i32(global_addr("pcc_gc_backend0_frame_roots_enabled"), 0) != 0):
        pcc_gc_native_root_bind_range(inputs, -1)
    pcc_thread_no_park_exit()
    result = pcc_list_body_py_list_getitem(inputs, i)
    pcc_gc_native_frame_leave(frame)
    return result

pcc_list_body_py_list_get_i64 = extern(
    "pcc_list_body_py_list_get_i64", (c_ptr, c_int64), c_int64
)

@c_abi_export("py_list_get_i64")
def py_list_get_i64(lst: c_ptr, i: int) -> int:
    # GC0 disabled frame tracking needs no native activation protocol here.
    # Its original input owners/counts still protect the body's raw inputs.
    if (load_i32(global_addr("pcc_gc_nonmoving_count_free"), 0) != 0
            or (load_i32(global_addr("pcc_gc_config_initialized"), 0) != 0
                and load_i32(global_addr("pcc_gc_backend_selected"), 0) == 0
                and load_i32(global_addr("pcc_gc_backend0_frame_roots_enabled"), 0) == 0)):
        inputs = stack_alloc(1 * 8)
        store_ptr(inputs, 0 * 8, lst)
        return pcc_list_body_py_list_get_i64(inputs, i)
    native_frame_bytes: int = 40
    native_range_capacity: int = 8
    native_ranges_bytes: int = 128
    pcc_thread_no_park_enter()
    frame = stack_alloc(40)  # native_frame_bytes: fixed descriptor layout
    memset(frame, 0, native_frame_bytes)
    ranges = stack_alloc(128)  # native_ranges_bytes: eight local ranges
    inputs = stack_alloc(1 * 8)
    store_ptr(inputs, 0 * 8, lst)
    pcc_gc_native_frame_enter(frame, ranges, native_range_capacity)
    if (load_i32(global_addr("pcc_gc_config_initialized"), 0) == 0
            or load_i32(global_addr("pcc_gc_backend_selected"), 0) != 0
            or load_i32(global_addr("pcc_gc_backend0_frame_roots_enabled"), 0) != 0):
        pcc_gc_native_root_bind_range(inputs, -1)
    pcc_thread_no_park_exit()
    result = pcc_list_body_py_list_get_i64(inputs, i)
    pcc_gc_native_frame_leave(frame)
    return result

pcc_list_body_py_list_get_i64_nonnegative = extern(
    "pcc_list_body_py_list_get_i64_nonnegative", (c_ptr, c_int64), c_int64
)

@c_abi_export("py_list_get_i64_nonnegative")
def py_list_get_i64_nonnegative(lst: c_ptr, i: int) -> int:
    # GC0 disabled frame tracking needs no native activation protocol here.
    # Its original input owners/counts still protect the body's raw inputs.
    if (load_i32(global_addr("pcc_gc_nonmoving_count_free"), 0) != 0
            or (load_i32(global_addr("pcc_gc_config_initialized"), 0) != 0
                and load_i32(global_addr("pcc_gc_backend_selected"), 0) == 0
                and load_i32(global_addr("pcc_gc_backend0_frame_roots_enabled"), 0) == 0)):
        inputs = stack_alloc(1 * 8)
        store_ptr(inputs, 0 * 8, lst)
        return pcc_list_body_py_list_get_i64_nonnegative(inputs, i)
    native_frame_bytes: int = 40
    native_range_capacity: int = 8
    native_ranges_bytes: int = 128
    pcc_thread_no_park_enter()
    frame = stack_alloc(40)  # native_frame_bytes: fixed descriptor layout
    memset(frame, 0, native_frame_bytes)
    ranges = stack_alloc(128)  # native_ranges_bytes: eight local ranges
    inputs = stack_alloc(1 * 8)
    store_ptr(inputs, 0 * 8, lst)
    pcc_gc_native_frame_enter(frame, ranges, native_range_capacity)
    if (load_i32(global_addr("pcc_gc_config_initialized"), 0) == 0
            or load_i32(global_addr("pcc_gc_backend_selected"), 0) != 0
            or load_i32(global_addr("pcc_gc_backend0_frame_roots_enabled"), 0) != 0):
        pcc_gc_native_root_bind_range(inputs, -1)
    pcc_thread_no_park_exit()
    result = pcc_list_body_py_list_get_i64_nonnegative(inputs, i)
    pcc_gc_native_frame_leave(frame)
    return result

pcc_list_body_py_list_set = extern(
    "pcc_list_body_py_list_set", (c_ptr, c_int64), c_void
)

@c_abi_export("py_list_set")
def py_list_set(lst: c_ptr, i: int, item: c_ptr) -> None:
    # GC0 disabled frame tracking needs no native activation protocol here.
    # Its original input owners/counts still protect the body's raw inputs.
    if (load_i32(global_addr("pcc_gc_nonmoving_count_free"), 0) != 0
            or (load_i32(global_addr("pcc_gc_config_initialized"), 0) != 0
                and load_i32(global_addr("pcc_gc_backend_selected"), 0) == 0
                and load_i32(global_addr("pcc_gc_backend0_frame_roots_enabled"), 0) == 0)):
        inputs = stack_alloc(2 * 8)
        store_ptr(inputs, 0 * 8, lst)
        store_ptr(inputs, 1 * 8, item)
        pcc_list_body_py_list_set(inputs, i)
        return
    native_frame_bytes: int = 40
    native_range_capacity: int = 8
    native_ranges_bytes: int = 128
    pcc_thread_no_park_enter()
    frame = stack_alloc(40)  # native_frame_bytes: fixed descriptor layout
    memset(frame, 0, native_frame_bytes)
    ranges = stack_alloc(128)  # native_ranges_bytes: eight local ranges
    inputs = stack_alloc(2 * 8)
    store_ptr(inputs, 0 * 8, lst)
    store_ptr(inputs, 1 * 8, item)
    pcc_gc_native_frame_enter(frame, ranges, native_range_capacity)
    if (load_i32(global_addr("pcc_gc_config_initialized"), 0) == 0
            or load_i32(global_addr("pcc_gc_backend_selected"), 0) != 0
            or load_i32(global_addr("pcc_gc_backend0_frame_roots_enabled"), 0) != 0):
        pcc_gc_native_root_bind_range(inputs, -2)
    pcc_thread_no_park_exit()
    pcc_list_body_py_list_set(inputs, i)
    pcc_gc_native_frame_leave(frame)
    return

pcc_list_body_py_list_get_for_frame = extern(
    "pcc_list_body_py_list_get_for_frame", (c_ptr, c_int64), c_ptr
)

@c_abi_export("py_list_get_for_frame")
def py_list_get_for_frame(lst: c_ptr, i: int) -> c_ptr:
    # GC0 disabled frame tracking needs no native activation protocol here.
    # Its original input owners/counts still protect the body's raw inputs.
    if (load_i32(global_addr("pcc_gc_nonmoving_count_free"), 0) != 0
            or (load_i32(global_addr("pcc_gc_config_initialized"), 0) != 0
                and load_i32(global_addr("pcc_gc_backend_selected"), 0) == 0
                and load_i32(global_addr("pcc_gc_backend0_frame_roots_enabled"), 0) == 0)):
        inputs = stack_alloc(1 * 8)
        store_ptr(inputs, 0 * 8, lst)
        return pcc_list_body_py_list_get_for_frame(inputs, i)
    native_frame_bytes: int = 40
    native_range_capacity: int = 8
    native_ranges_bytes: int = 128
    pcc_thread_no_park_enter()
    frame = stack_alloc(40)  # native_frame_bytes: fixed descriptor layout
    memset(frame, 0, native_frame_bytes)
    ranges = stack_alloc(128)  # native_ranges_bytes: eight local ranges
    inputs = stack_alloc(1 * 8)
    store_ptr(inputs, 0 * 8, lst)
    pcc_gc_native_frame_enter(frame, ranges, native_range_capacity)
    if (load_i32(global_addr("pcc_gc_config_initialized"), 0) == 0
            or load_i32(global_addr("pcc_gc_backend_selected"), 0) != 0
            or load_i32(global_addr("pcc_gc_backend0_frame_roots_enabled"), 0) != 0):
        pcc_gc_native_root_bind_range(inputs, -1)
    pcc_thread_no_park_exit()
    result = pcc_list_body_py_list_get_for_frame(inputs, i)
    pcc_gc_native_frame_leave(frame)
    return result

pcc_list_body_py_list_set_from_owned_root = extern(
    "pcc_list_body_py_list_set_from_owned_root", (c_ptr, c_int64, c_ptr, c_ptr), c_void
)

@c_abi_export("py_list_set_from_owned_root")
def py_list_set_from_owned_root(lst: c_ptr, i: int, source_slot: c_ptr, owned_flag: c_ptr) -> None:
    # GC0 disabled frame tracking needs no native activation protocol here.
    # Its original input owners/counts still protect the body's raw inputs.
    if (load_i32(global_addr("pcc_gc_nonmoving_count_free"), 0) != 0
            or (load_i32(global_addr("pcc_gc_config_initialized"), 0) != 0
                and load_i32(global_addr("pcc_gc_backend_selected"), 0) == 0
                and load_i32(global_addr("pcc_gc_backend0_frame_roots_enabled"), 0) == 0)):
        inputs = stack_alloc(1 * 8)
        store_ptr(inputs, 0 * 8, lst)
        pcc_list_body_py_list_set_from_owned_root(inputs, i, source_slot, owned_flag)
        return
    native_frame_bytes: int = 40
    native_range_capacity: int = 8
    native_ranges_bytes: int = 128
    pcc_thread_no_park_enter()
    frame = stack_alloc(40)  # native_frame_bytes: fixed descriptor layout
    memset(frame, 0, native_frame_bytes)
    ranges = stack_alloc(128)  # native_ranges_bytes: eight local ranges
    inputs = stack_alloc(1 * 8)
    store_ptr(inputs, 0 * 8, lst)
    pcc_gc_native_frame_enter(frame, ranges, native_range_capacity)
    if (load_i32(global_addr("pcc_gc_config_initialized"), 0) == 0
            or load_i32(global_addr("pcc_gc_backend_selected"), 0) != 0
            or load_i32(global_addr("pcc_gc_backend0_frame_roots_enabled"), 0) != 0):
        pcc_gc_native_root_bind_range(inputs, -1)
    pcc_thread_no_park_exit()
    pcc_list_body_py_list_set_from_owned_root(inputs, i, source_slot, owned_flag)
    pcc_gc_native_frame_leave(frame)
    return

pcc_list_body_py_list_setitem = extern(
    "pcc_list_body_py_list_setitem", (c_ptr, c_int64), c_int64
)

@c_abi_export("py_list_setitem")
def py_list_setitem(lst: c_ptr, i: int, item: c_ptr) -> int:
    # GC0 disabled frame tracking needs no native activation protocol here.
    # Its original input owners/counts still protect the body's raw inputs.
    if (load_i32(global_addr("pcc_gc_nonmoving_count_free"), 0) != 0
            or (load_i32(global_addr("pcc_gc_config_initialized"), 0) != 0
                and load_i32(global_addr("pcc_gc_backend_selected"), 0) == 0
                and load_i32(global_addr("pcc_gc_backend0_frame_roots_enabled"), 0) == 0)):
        inputs = stack_alloc(2 * 8)
        store_ptr(inputs, 0 * 8, lst)
        store_ptr(inputs, 1 * 8, item)
        return pcc_list_body_py_list_setitem(inputs, i)
    native_frame_bytes: int = 40
    native_range_capacity: int = 8
    native_ranges_bytes: int = 128
    pcc_thread_no_park_enter()
    frame = stack_alloc(40)  # native_frame_bytes: fixed descriptor layout
    memset(frame, 0, native_frame_bytes)
    ranges = stack_alloc(128)  # native_ranges_bytes: eight local ranges
    inputs = stack_alloc(2 * 8)
    store_ptr(inputs, 0 * 8, lst)
    store_ptr(inputs, 1 * 8, item)
    pcc_gc_native_frame_enter(frame, ranges, native_range_capacity)
    if (load_i32(global_addr("pcc_gc_config_initialized"), 0) == 0
            or load_i32(global_addr("pcc_gc_backend_selected"), 0) != 0
            or load_i32(global_addr("pcc_gc_backend0_frame_roots_enabled"), 0) != 0):
        pcc_gc_native_root_bind_range(inputs, -2)
    pcc_thread_no_park_exit()
    result = pcc_list_body_py_list_setitem(inputs, i)
    pcc_gc_native_frame_leave(frame)
    return result

pcc_list_body_py_list_len = extern(
    "pcc_list_body_py_list_len", (c_ptr,), c_int64
)

@c_abi_export("py_list_len")
def py_list_len(lst: c_ptr) -> int:
    # GC0 disabled frame tracking needs no native activation protocol here.
    # Its original input owners/counts still protect the body's raw inputs.
    if (load_i32(global_addr("pcc_gc_nonmoving_count_free"), 0) != 0
            or (load_i32(global_addr("pcc_gc_config_initialized"), 0) != 0
                and load_i32(global_addr("pcc_gc_backend_selected"), 0) == 0
                and load_i32(global_addr("pcc_gc_backend0_frame_roots_enabled"), 0) == 0)):
        inputs = stack_alloc(1 * 8)
        store_ptr(inputs, 0 * 8, lst)
        return pcc_list_body_py_list_len(inputs)
    native_frame_bytes: int = 40
    native_range_capacity: int = 8
    native_ranges_bytes: int = 128
    pcc_thread_no_park_enter()
    frame = stack_alloc(40)  # native_frame_bytes: fixed descriptor layout
    memset(frame, 0, native_frame_bytes)
    ranges = stack_alloc(128)  # native_ranges_bytes: eight local ranges
    inputs = stack_alloc(1 * 8)
    store_ptr(inputs, 0 * 8, lst)
    pcc_gc_native_frame_enter(frame, ranges, native_range_capacity)
    if (load_i32(global_addr("pcc_gc_config_initialized"), 0) == 0
            or load_i32(global_addr("pcc_gc_backend_selected"), 0) != 0
            or load_i32(global_addr("pcc_gc_backend0_frame_roots_enabled"), 0) != 0):
        pcc_gc_native_root_bind_range(inputs, -1)
    pcc_thread_no_park_exit()
    result = pcc_list_body_py_list_len(inputs)
    pcc_gc_native_frame_leave(frame)
    return result

pcc_list_body_py_list_concat = extern(
    "pcc_list_body_py_list_concat", (c_ptr,), c_ptr
)

@c_abi_export("py_list_concat")
def py_list_concat(a: c_ptr, b: c_ptr) -> c_ptr:
    # GC0 disabled frame tracking needs no native activation protocol here.
    # Its original input owners/counts still protect the body's raw inputs.
    if (load_i32(global_addr("pcc_gc_nonmoving_count_free"), 0) != 0
            or (load_i32(global_addr("pcc_gc_config_initialized"), 0) != 0
                and load_i32(global_addr("pcc_gc_backend_selected"), 0) == 0
                and load_i32(global_addr("pcc_gc_backend0_frame_roots_enabled"), 0) == 0)):
        inputs = stack_alloc(2 * 8)
        store_ptr(inputs, 0 * 8, a)
        store_ptr(inputs, 1 * 8, b)
        return pcc_list_body_py_list_concat(inputs)
    native_frame_bytes: int = 40
    native_range_capacity: int = 8
    native_ranges_bytes: int = 128
    pcc_thread_no_park_enter()
    frame = stack_alloc(40)  # native_frame_bytes: fixed descriptor layout
    memset(frame, 0, native_frame_bytes)
    ranges = stack_alloc(128)  # native_ranges_bytes: eight local ranges
    inputs = stack_alloc(2 * 8)
    store_ptr(inputs, 0 * 8, a)
    store_ptr(inputs, 1 * 8, b)
    pcc_gc_native_frame_enter(frame, ranges, native_range_capacity)
    if (load_i32(global_addr("pcc_gc_config_initialized"), 0) == 0
            or load_i32(global_addr("pcc_gc_backend_selected"), 0) != 0
            or load_i32(global_addr("pcc_gc_backend0_frame_roots_enabled"), 0) != 0):
        pcc_gc_native_root_bind_range(inputs, -2)
    pcc_thread_no_park_exit()
    result = pcc_list_body_py_list_concat(inputs)
    pcc_gc_native_frame_leave(frame)
    return result

pcc_list_body_py_list_copy = extern(
    "pcc_list_body_py_list_copy", (c_ptr,), c_ptr
)

@c_abi_export("py_list_copy")
def py_list_copy(src: c_ptr) -> c_ptr:
    # GC0 disabled frame tracking needs no native activation protocol here.
    # Its original input owners/counts still protect the body's raw inputs.
    if (load_i32(global_addr("pcc_gc_nonmoving_count_free"), 0) != 0
            or (load_i32(global_addr("pcc_gc_config_initialized"), 0) != 0
                and load_i32(global_addr("pcc_gc_backend_selected"), 0) == 0
                and load_i32(global_addr("pcc_gc_backend0_frame_roots_enabled"), 0) == 0)):
        inputs = stack_alloc(1 * 8)
        store_ptr(inputs, 0 * 8, src)
        return pcc_list_body_py_list_copy(inputs)
    native_frame_bytes: int = 40
    native_range_capacity: int = 8
    native_ranges_bytes: int = 128
    pcc_thread_no_park_enter()
    frame = stack_alloc(40)  # native_frame_bytes: fixed descriptor layout
    memset(frame, 0, native_frame_bytes)
    ranges = stack_alloc(128)  # native_ranges_bytes: eight local ranges
    inputs = stack_alloc(1 * 8)
    store_ptr(inputs, 0 * 8, src)
    pcc_gc_native_frame_enter(frame, ranges, native_range_capacity)
    if (load_i32(global_addr("pcc_gc_config_initialized"), 0) == 0
            or load_i32(global_addr("pcc_gc_backend_selected"), 0) != 0
            or load_i32(global_addr("pcc_gc_backend0_frame_roots_enabled"), 0) != 0):
        pcc_gc_native_root_bind_range(inputs, -1)
    pcc_thread_no_park_exit()
    result = pcc_list_body_py_list_copy(inputs)
    pcc_gc_native_frame_leave(frame)
    return result

pcc_list_body_py_list_repeat = extern(
    "pcc_list_body_py_list_repeat", (c_ptr, c_int64), c_ptr
)

@c_abi_export("py_list_repeat")
def py_list_repeat(src: c_ptr, count: int) -> c_ptr:
    # GC0 disabled frame tracking needs no native activation protocol here.
    # Its original input owners/counts still protect the body's raw inputs.
    if (load_i32(global_addr("pcc_gc_nonmoving_count_free"), 0) != 0
            or (load_i32(global_addr("pcc_gc_config_initialized"), 0) != 0
                and load_i32(global_addr("pcc_gc_backend_selected"), 0) == 0
                and load_i32(global_addr("pcc_gc_backend0_frame_roots_enabled"), 0) == 0)):
        inputs = stack_alloc(1 * 8)
        store_ptr(inputs, 0 * 8, src)
        return pcc_list_body_py_list_repeat(inputs, count)
    native_frame_bytes: int = 40
    native_range_capacity: int = 8
    native_ranges_bytes: int = 128
    pcc_thread_no_park_enter()
    frame = stack_alloc(40)  # native_frame_bytes: fixed descriptor layout
    memset(frame, 0, native_frame_bytes)
    ranges = stack_alloc(128)  # native_ranges_bytes: eight local ranges
    inputs = stack_alloc(1 * 8)
    store_ptr(inputs, 0 * 8, src)
    pcc_gc_native_frame_enter(frame, ranges, native_range_capacity)
    if (load_i32(global_addr("pcc_gc_config_initialized"), 0) == 0
            or load_i32(global_addr("pcc_gc_backend_selected"), 0) != 0
            or load_i32(global_addr("pcc_gc_backend0_frame_roots_enabled"), 0) != 0):
        pcc_gc_native_root_bind_range(inputs, -1)
    pcc_thread_no_park_exit()
    result = pcc_list_body_py_list_repeat(inputs, count)
    pcc_gc_native_frame_leave(frame)
    return result

pcc_list_body_py_list_contains = extern(
    "pcc_list_body_py_list_contains", (c_ptr,), c_int64
)

@c_abi_export("py_list_contains")
def py_list_contains(lst: c_ptr, item: c_ptr) -> int:
    native_frame_bytes: int = 40
    native_range_capacity: int = 8
    native_ranges_bytes: int = 128
    pcc_thread_no_park_enter()
    frame = stack_alloc(40)  # native_frame_bytes: fixed descriptor layout
    memset(frame, 0, native_frame_bytes)
    ranges = stack_alloc(128)  # native_ranges_bytes: eight local ranges
    inputs = stack_alloc(2 * 8)
    store_ptr(inputs, 0 * 8, lst)
    store_ptr(inputs, 1 * 8, item)
    pcc_gc_native_frame_enter(frame, ranges, native_range_capacity)
    if (load_i32(global_addr("pcc_gc_config_initialized"), 0) == 0
            or load_i32(global_addr("pcc_gc_backend_selected"), 0) != 0
            or load_i32(global_addr("pcc_gc_backend0_frame_roots_enabled"), 0) != 0):
        pcc_gc_native_root_bind_range(inputs, -2)
    pcc_thread_no_park_exit()
    result = pcc_list_body_py_list_contains(inputs)
    pcc_gc_native_frame_leave(frame)
    return result

pcc_list_body_py_list_slice = extern(
    "pcc_list_body_py_list_slice", (c_ptr,), c_ptr
)

@c_abi_export("py_list_slice")
def py_list_slice(lst: c_ptr, lo: c_ptr, hi: c_ptr, step: c_ptr) -> c_ptr:
    # GC0 disabled frame tracking needs no native activation protocol here.
    # Its original input owners/counts still protect the body's raw inputs.
    if (load_i32(global_addr("pcc_gc_nonmoving_count_free"), 0) != 0
            or (load_i32(global_addr("pcc_gc_config_initialized"), 0) != 0
                and load_i32(global_addr("pcc_gc_backend_selected"), 0) == 0
                and load_i32(global_addr("pcc_gc_backend0_frame_roots_enabled"), 0) == 0)):
        inputs = stack_alloc(4 * 8)
        store_ptr(inputs, 0 * 8, lst)
        store_ptr(inputs, 1 * 8, lo)
        store_ptr(inputs, 2 * 8, hi)
        store_ptr(inputs, 3 * 8, step)
        return pcc_list_body_py_list_slice(inputs)
    native_frame_bytes: int = 40
    native_range_capacity: int = 8
    native_ranges_bytes: int = 128
    pcc_thread_no_park_enter()
    frame = stack_alloc(40)  # native_frame_bytes: fixed descriptor layout
    memset(frame, 0, native_frame_bytes)
    ranges = stack_alloc(128)  # native_ranges_bytes: eight local ranges
    inputs = stack_alloc(4 * 8)
    store_ptr(inputs, 0 * 8, lst)
    store_ptr(inputs, 1 * 8, lo)
    store_ptr(inputs, 2 * 8, hi)
    store_ptr(inputs, 3 * 8, step)
    pcc_gc_native_frame_enter(frame, ranges, native_range_capacity)
    if (load_i32(global_addr("pcc_gc_config_initialized"), 0) == 0
            or load_i32(global_addr("pcc_gc_backend_selected"), 0) != 0
            or load_i32(global_addr("pcc_gc_backend0_frame_roots_enabled"), 0) != 0):
        pcc_gc_native_root_bind_range(inputs, -4)
    pcc_thread_no_park_exit()
    result = pcc_list_body_py_list_slice(inputs)
    pcc_gc_native_frame_leave(frame)
    return result

pcc_list_body_py_list_del_slice = extern(
    "pcc_list_body_py_list_del_slice", (c_ptr,), c_int64
)

@c_abi_export("py_list_del_slice")
def py_list_del_slice(lst: c_ptr, lo: c_ptr, hi: c_ptr, step: c_ptr) -> int:
    # GC0 disabled frame tracking needs no native activation protocol here.
    # Its original input owners/counts still protect the body's raw inputs.
    if (load_i32(global_addr("pcc_gc_nonmoving_count_free"), 0) != 0
            or (load_i32(global_addr("pcc_gc_config_initialized"), 0) != 0
                and load_i32(global_addr("pcc_gc_backend_selected"), 0) == 0
                and load_i32(global_addr("pcc_gc_backend0_frame_roots_enabled"), 0) == 0)):
        inputs = stack_alloc(4 * 8)
        store_ptr(inputs, 0 * 8, lst)
        store_ptr(inputs, 1 * 8, lo)
        store_ptr(inputs, 2 * 8, hi)
        store_ptr(inputs, 3 * 8, step)
        return pcc_list_body_py_list_del_slice(inputs)
    native_frame_bytes: int = 40
    native_range_capacity: int = 8
    native_ranges_bytes: int = 128
    pcc_thread_no_park_enter()
    frame = stack_alloc(40)  # native_frame_bytes: fixed descriptor layout
    memset(frame, 0, native_frame_bytes)
    ranges = stack_alloc(128)  # native_ranges_bytes: eight local ranges
    inputs = stack_alloc(4 * 8)
    store_ptr(inputs, 0 * 8, lst)
    store_ptr(inputs, 1 * 8, lo)
    store_ptr(inputs, 2 * 8, hi)
    store_ptr(inputs, 3 * 8, step)
    pcc_gc_native_frame_enter(frame, ranges, native_range_capacity)
    if (load_i32(global_addr("pcc_gc_config_initialized"), 0) == 0
            or load_i32(global_addr("pcc_gc_backend_selected"), 0) != 0
            or load_i32(global_addr("pcc_gc_backend0_frame_roots_enabled"), 0) != 0):
        pcc_gc_native_root_bind_range(inputs, -4)
    pcc_thread_no_park_exit()
    result = pcc_list_body_py_list_del_slice(inputs)
    pcc_gc_native_frame_leave(frame)
    return result

pcc_list_body_py_list_extend = extern(
    "pcc_list_body_py_list_extend", (c_ptr,), c_void
)

@c_abi_export("py_list_extend")
def py_list_extend(a: c_ptr, b: c_ptr) -> None:
    # GC0 disabled frame tracking needs no native activation protocol here.
    # Its original input owners/counts still protect the body's raw inputs.
    if (load_i32(global_addr("pcc_gc_nonmoving_count_free"), 0) != 0
            or (load_i32(global_addr("pcc_gc_config_initialized"), 0) != 0
                and load_i32(global_addr("pcc_gc_backend_selected"), 0) == 0
                and load_i32(global_addr("pcc_gc_backend0_frame_roots_enabled"), 0) == 0)):
        inputs = stack_alloc(2 * 8)
        store_ptr(inputs, 0 * 8, a)
        store_ptr(inputs, 1 * 8, b)
        pcc_list_body_py_list_extend(inputs)
        return
    native_frame_bytes: int = 40
    native_range_capacity: int = 8
    native_ranges_bytes: int = 128
    pcc_thread_no_park_enter()
    frame = stack_alloc(40)  # native_frame_bytes: fixed descriptor layout
    memset(frame, 0, native_frame_bytes)
    ranges = stack_alloc(128)  # native_ranges_bytes: eight local ranges
    inputs = stack_alloc(2 * 8)
    store_ptr(inputs, 0 * 8, a)
    store_ptr(inputs, 1 * 8, b)
    pcc_gc_native_frame_enter(frame, ranges, native_range_capacity)
    if (load_i32(global_addr("pcc_gc_config_initialized"), 0) == 0
            or load_i32(global_addr("pcc_gc_backend_selected"), 0) != 0
            or load_i32(global_addr("pcc_gc_backend0_frame_roots_enabled"), 0) != 0):
        pcc_gc_native_root_bind_range(inputs, -2)
    pcc_thread_no_park_exit()
    pcc_list_body_py_list_extend(inputs)
    pcc_gc_native_frame_leave(frame)
    return

pcc_list_body_py_list_insert = extern(
    "pcc_list_body_py_list_insert", (c_ptr, c_int64), c_void
)

@c_abi_export("py_list_insert")
def py_list_insert(lst: c_ptr, i: int, item: c_ptr) -> None:
    # GC0 disabled frame tracking needs no native activation protocol here.
    # Its original input owners/counts still protect the body's raw inputs.
    if (load_i32(global_addr("pcc_gc_nonmoving_count_free"), 0) != 0
            or (load_i32(global_addr("pcc_gc_config_initialized"), 0) != 0
                and load_i32(global_addr("pcc_gc_backend_selected"), 0) == 0
                and load_i32(global_addr("pcc_gc_backend0_frame_roots_enabled"), 0) == 0)):
        inputs = stack_alloc(2 * 8)
        store_ptr(inputs, 0 * 8, lst)
        store_ptr(inputs, 1 * 8, item)
        pcc_list_body_py_list_insert(inputs, i)
        return
    native_frame_bytes: int = 40
    native_range_capacity: int = 8
    native_ranges_bytes: int = 128
    pcc_thread_no_park_enter()
    frame = stack_alloc(40)  # native_frame_bytes: fixed descriptor layout
    memset(frame, 0, native_frame_bytes)
    ranges = stack_alloc(128)  # native_ranges_bytes: eight local ranges
    inputs = stack_alloc(2 * 8)
    store_ptr(inputs, 0 * 8, lst)
    store_ptr(inputs, 1 * 8, item)
    pcc_gc_native_frame_enter(frame, ranges, native_range_capacity)
    if (load_i32(global_addr("pcc_gc_config_initialized"), 0) == 0
            or load_i32(global_addr("pcc_gc_backend_selected"), 0) != 0
            or load_i32(global_addr("pcc_gc_backend0_frame_roots_enabled"), 0) != 0):
        pcc_gc_native_root_bind_range(inputs, -2)
    pcc_thread_no_park_exit()
    pcc_list_body_py_list_insert(inputs, i)
    pcc_gc_native_frame_leave(frame)
    return

pcc_list_body_py_list_pop = extern(
    "pcc_list_body_py_list_pop", (c_ptr, c_int64), c_ptr
)

@c_abi_export("py_list_pop")
def py_list_pop(lst: c_ptr, i: int) -> c_ptr:
    native_frame_bytes: int = 40
    native_range_capacity: int = 8
    native_ranges_bytes: int = 128
    pcc_thread_no_park_enter()
    frame = stack_alloc(40)  # native_frame_bytes: fixed descriptor layout
    memset(frame, 0, native_frame_bytes)
    ranges = stack_alloc(128)  # native_ranges_bytes: eight local ranges
    inputs = stack_alloc(1 * 8)
    store_ptr(inputs, 0 * 8, lst)
    pcc_gc_native_frame_enter(frame, ranges, native_range_capacity)
    if (load_i32(global_addr("pcc_gc_config_initialized"), 0) == 0
            or load_i32(global_addr("pcc_gc_backend_selected"), 0) != 0
            or load_i32(global_addr("pcc_gc_backend0_frame_roots_enabled"), 0) != 0):
        pcc_gc_native_root_bind_range(inputs, -1)
    pcc_thread_no_park_exit()
    result = pcc_list_body_py_list_pop(inputs, i)
    pcc_gc_native_frame_leave(frame)
    return result

pcc_list_body_py_list_remove = extern(
    "pcc_list_body_py_list_remove", (c_ptr,), c_void
)

@c_abi_export("py_list_remove")
def py_list_remove(lst: c_ptr, item: c_ptr) -> None:
    native_frame_bytes: int = 40
    native_range_capacity: int = 8
    native_ranges_bytes: int = 128
    pcc_thread_no_park_enter()
    frame = stack_alloc(40)  # native_frame_bytes: fixed descriptor layout
    memset(frame, 0, native_frame_bytes)
    ranges = stack_alloc(128)  # native_ranges_bytes: eight local ranges
    inputs = stack_alloc(2 * 8)
    store_ptr(inputs, 0 * 8, lst)
    store_ptr(inputs, 1 * 8, item)
    pcc_gc_native_frame_enter(frame, ranges, native_range_capacity)
    if (load_i32(global_addr("pcc_gc_config_initialized"), 0) == 0
            or load_i32(global_addr("pcc_gc_backend_selected"), 0) != 0
            or load_i32(global_addr("pcc_gc_backend0_frame_roots_enabled"), 0) != 0):
        pcc_gc_native_root_bind_range(inputs, -2)
    pcc_thread_no_park_exit()
    pcc_list_body_py_list_remove(inputs)
    pcc_gc_native_frame_leave(frame)
    return

pcc_list_body_py_list_clear = extern(
    "pcc_list_body_py_list_clear", (c_ptr,), c_void
)

@c_abi_export("py_list_clear")
def py_list_clear(lst: c_ptr) -> None:
    # GC0 disabled frame tracking needs no native activation protocol here.
    # Its original input owners/counts still protect the body's raw inputs.
    if (load_i32(global_addr("pcc_gc_nonmoving_count_free"), 0) != 0
            or (load_i32(global_addr("pcc_gc_config_initialized"), 0) != 0
                and load_i32(global_addr("pcc_gc_backend_selected"), 0) == 0
                and load_i32(global_addr("pcc_gc_backend0_frame_roots_enabled"), 0) == 0)):
        inputs = stack_alloc(1 * 8)
        store_ptr(inputs, 0 * 8, lst)
        pcc_list_body_py_list_clear(inputs)
        return
    native_frame_bytes: int = 40
    native_range_capacity: int = 8
    native_ranges_bytes: int = 128
    pcc_thread_no_park_enter()
    frame = stack_alloc(40)  # native_frame_bytes: fixed descriptor layout
    memset(frame, 0, native_frame_bytes)
    ranges = stack_alloc(128)  # native_ranges_bytes: eight local ranges
    inputs = stack_alloc(1 * 8)
    store_ptr(inputs, 0 * 8, lst)
    pcc_gc_native_frame_enter(frame, ranges, native_range_capacity)
    if (load_i32(global_addr("pcc_gc_config_initialized"), 0) == 0
            or load_i32(global_addr("pcc_gc_backend_selected"), 0) != 0
            or load_i32(global_addr("pcc_gc_backend0_frame_roots_enabled"), 0) != 0):
        pcc_gc_native_root_bind_range(inputs, -1)
    pcc_thread_no_park_exit()
    pcc_list_body_py_list_clear(inputs)
    pcc_gc_native_frame_leave(frame)
    return

pcc_list_body_py_list_index = extern(
    "pcc_list_body_py_list_index", (c_ptr,), c_int64
)

@c_abi_export("py_list_index")
def py_list_index(lst: c_ptr, item: c_ptr) -> int:
    native_frame_bytes: int = 40
    native_range_capacity: int = 8
    native_ranges_bytes: int = 128
    pcc_thread_no_park_enter()
    frame = stack_alloc(40)  # native_frame_bytes: fixed descriptor layout
    memset(frame, 0, native_frame_bytes)
    ranges = stack_alloc(128)  # native_ranges_bytes: eight local ranges
    inputs = stack_alloc(2 * 8)
    store_ptr(inputs, 0 * 8, lst)
    store_ptr(inputs, 1 * 8, item)
    pcc_gc_native_frame_enter(frame, ranges, native_range_capacity)
    if (load_i32(global_addr("pcc_gc_config_initialized"), 0) == 0
            or load_i32(global_addr("pcc_gc_backend_selected"), 0) != 0
            or load_i32(global_addr("pcc_gc_backend0_frame_roots_enabled"), 0) != 0):
        pcc_gc_native_root_bind_range(inputs, -2)
    pcc_thread_no_park_exit()
    result = pcc_list_body_py_list_index(inputs)
    pcc_gc_native_frame_leave(frame)
    return result

pcc_list_body_py_list_index_range = extern(
    "pcc_list_body_py_list_index_range", (c_ptr, c_int64, c_int64), c_int64
)

@c_abi_export("py_list_index_range")
def py_list_index_range(lst: c_ptr, item: c_ptr, start: int, end: int) -> int:
    native_frame_bytes: int = 40
    native_range_capacity: int = 8
    native_ranges_bytes: int = 128
    pcc_thread_no_park_enter()
    frame = stack_alloc(40)  # native_frame_bytes: fixed descriptor layout
    memset(frame, 0, native_frame_bytes)
    ranges = stack_alloc(128)  # native_ranges_bytes: eight local ranges
    inputs = stack_alloc(2 * 8)
    store_ptr(inputs, 0 * 8, lst)
    store_ptr(inputs, 1 * 8, item)
    pcc_gc_native_frame_enter(frame, ranges, native_range_capacity)
    if (load_i32(global_addr("pcc_gc_config_initialized"), 0) == 0
            or load_i32(global_addr("pcc_gc_backend_selected"), 0) != 0
            or load_i32(global_addr("pcc_gc_backend0_frame_roots_enabled"), 0) != 0):
        pcc_gc_native_root_bind_range(inputs, -2)
    pcc_thread_no_park_exit()
    result = pcc_list_body_py_list_index_range(inputs, start, end)
    pcc_gc_native_frame_leave(frame)
    return result

pcc_list_body_py_list_count = extern(
    "pcc_list_body_py_list_count", (c_ptr,), c_int64
)

@c_abi_export("py_list_count")
def py_list_count(lst: c_ptr, item: c_ptr) -> int:
    native_frame_bytes: int = 40
    native_range_capacity: int = 8
    native_ranges_bytes: int = 128
    pcc_thread_no_park_enter()
    frame = stack_alloc(40)  # native_frame_bytes: fixed descriptor layout
    memset(frame, 0, native_frame_bytes)
    ranges = stack_alloc(128)  # native_ranges_bytes: eight local ranges
    inputs = stack_alloc(2 * 8)
    store_ptr(inputs, 0 * 8, lst)
    store_ptr(inputs, 1 * 8, item)
    pcc_gc_native_frame_enter(frame, ranges, native_range_capacity)
    if (load_i32(global_addr("pcc_gc_config_initialized"), 0) == 0
            or load_i32(global_addr("pcc_gc_backend_selected"), 0) != 0
            or load_i32(global_addr("pcc_gc_backend0_frame_roots_enabled"), 0) != 0):
        pcc_gc_native_root_bind_range(inputs, -2)
    pcc_thread_no_park_exit()
    result = pcc_list_body_py_list_count(inputs)
    pcc_gc_native_frame_leave(frame)
    return result

pcc_list_body_py_list_reverse = extern(
    "pcc_list_body_py_list_reverse", (c_ptr,), c_void
)

@c_abi_export("py_list_reverse")
def py_list_reverse(lst: c_ptr) -> None:
    # GC0 disabled frame tracking needs no native activation protocol here.
    # Its original input owners/counts still protect the body's raw inputs.
    if (load_i32(global_addr("pcc_gc_nonmoving_count_free"), 0) != 0
            or (load_i32(global_addr("pcc_gc_config_initialized"), 0) != 0
                and load_i32(global_addr("pcc_gc_backend_selected"), 0) == 0
                and load_i32(global_addr("pcc_gc_backend0_frame_roots_enabled"), 0) == 0)):
        inputs = stack_alloc(1 * 8)
        store_ptr(inputs, 0 * 8, lst)
        pcc_list_body_py_list_reverse(inputs)
        return
    native_frame_bytes: int = 40
    native_range_capacity: int = 8
    native_ranges_bytes: int = 128
    pcc_thread_no_park_enter()
    frame = stack_alloc(40)  # native_frame_bytes: fixed descriptor layout
    memset(frame, 0, native_frame_bytes)
    ranges = stack_alloc(128)  # native_ranges_bytes: eight local ranges
    inputs = stack_alloc(1 * 8)
    store_ptr(inputs, 0 * 8, lst)
    pcc_gc_native_frame_enter(frame, ranges, native_range_capacity)
    if (load_i32(global_addr("pcc_gc_config_initialized"), 0) == 0
            or load_i32(global_addr("pcc_gc_backend_selected"), 0) != 0
            or load_i32(global_addr("pcc_gc_backend0_frame_roots_enabled"), 0) != 0):
        pcc_gc_native_root_bind_range(inputs, -1)
    pcc_thread_no_park_exit()
    pcc_list_body_py_list_reverse(inputs)
    pcc_gc_native_frame_leave(frame)
    return


pcc_gc_load_ptr = extern("pcc_gc_load_ptr", (c_ptr, c_ptr), c_ptr)
pcc_gc_native_root_unbind = extern("pcc_gc_native_root_unbind", (c_ptr,), c_void)


@c_abi_export("pcc_list_native_prepare_root")
def pcc_list_native_prepare_root(slot: c_ptr, handle_slot: c_ptr, owned: int) -> int:
    """Publish a captured temporary before the first read-barrier poll.

    Managed allocation fixed the backend before this slot obtained a value.
    Config reads and the local bind are nonpolling; no wrapper can introduce
    an earlier implicit entry poll. Counted nonmoving modes keep their old
    no-registration path; so do the count-free nonmoving GC1/2, whose
    stopped-world seed and remark scan thread stacks conservatively. Only the
    relocating GC3/4 bind a temporary they may move.
    """
    store_ptr(handle_slot, 0, null())
    value = load_ptr(slot, 0)
    if ptr_is_null(value) != 0 or is_tagged_int(value) != 0:
        return 0
    backend: int = load_i32(global_addr("pcc_gc_backend_selected"), 0)
    if backend != 3 and backend != 4:
        # GC0 and the counted modes keep their owners' counts. The nonmoving
        # count-free GC1/GC2 rescan every thread's registers and stack
        # conservatively at each stopped-world seed and remark, which finds a
        # temporary held in this frame; neither moves it.
        return 0
    handle = pcc_gc_native_root_bind_range(slot, 1 if owned != 0 else -1)
    if ptr_is_null(handle) != 0:
        return -1
    store_ptr(handle_slot, 0, handle)
    value = pcc_gc_load_ptr(null(), slot)
    store_ptr(slot, 0, value)
    if ptr_is_null(value) != 0:
        pcc_gc_native_root_unbind(handle)
        store_ptr(handle_slot, 0, null())
        return -1
    return 0
