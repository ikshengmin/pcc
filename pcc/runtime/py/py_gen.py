"""pcc-Python port of py_gen.c."""

__pcc_runtime_port__ = True

from pcc import i64
from pcc.runtime.py.py_abi_constants import (
    C_POINTER_SIZE,
    PYLISTOBJECT_CAPACITY_OFFSET,
    PYLISTOBJECT_ITEMS_OFFSET,
    PYLISTOBJECT_LENGTH_OFFSET,
    PYLISTOBJECT_SIZE,
    PYINLINEGENOBJECT_LENGTH_OFFSET,
    PYINLINEGENOBJECT_ITEMS_OFFSET,
    PYOBJECTHEADER_FLAGS_OFFSET,
    PY_FLAG_GC_PINNED,
    PY_FLAG_GEN_SOURCE,
    PY_FLAG_GEN_INLINE_FRAME,
    PY_TYPE_COROUTINE,
    PY_TYPE_GEN,
    PY_TYPE_LIST,
)


from pcc.extern import extern, c_abi_export, c_int32, c_ptr, c_int64, c_void
from pcc.unsafe import (
    atomic_rmw_i32,
    define_global_i32,
    define_thread_local_ptr_null,
    memset,
    call_ptr2,
    cstr,
    global_load_ptr,
    global_addr,
    global_store_ptr,
    function_addr,
    is_tagged_int,
    load_i32,
    load_i64,
    load_ptr,
    malloc,
    null,
    ptr_add,
    ptr_eq,
    ptr_is_null,
    stack_alloc,
    store_i32,
    store_i64,
    store_ptr,
)

py_incref   = extern("py_incref",   (c_ptr,),         c_void)
py_decref   = extern("py_decref",   (c_ptr,),         c_void)
py_exc_new  = extern("py_exc_new",  (c_int64, c_ptr), c_ptr)
py_exc_new_with_value = extern("py_exc_new_with_value", (c_int64, c_ptr), c_ptr)
py_raise    = extern("py_raise",    (c_ptr,),         c_void)
# py_raise increfs the exception it stores, so a caller that created it still
# owns a reference.  py_raise_owned raises and releases that reference.
py_raise_owned = extern("py_raise_owned", (c_ptr,), c_void)
py_coroutine_run = extern("py_coroutine_run", (c_ptr,), c_ptr)
py_coroutine_send = extern("py_coroutine_send", (c_ptr, c_ptr, c_ptr), c_ptr)
py_current_exception = extern("py_current_exception", (), c_ptr)
py_clear_exception   = extern("py_clear_exception",   (), c_void)
py_exc_builtin_class = extern("py_exc_builtin_class", (c_int64,), c_ptr)
py_exc_matches       = extern("py_exc_matches",       (c_ptr, c_ptr), c_int64)
py_exc_get_message   = extern("py_exc_get_message",   (c_ptr,),       c_ptr)
py_gc_track          = extern("py_gc_track",          (c_ptr,),         c_void)
py_weakref_invalidate = extern("py_weakref_invalidate", (c_ptr,), c_void)
pcc_gc_pin = extern("pcc_gc_pin", (c_ptr,), c_void)
pcc_gc_unpin = extern("pcc_gc_unpin", (c_ptr,), c_void)
pcc_py_gc_minor_graph_lock = extern("pcc_py_gc_minor_graph_lock", (), c_void)
pcc_py_gc_minor_graph_unlock = extern("pcc_py_gc_minor_graph_unlock", (), c_void)
pcc_refcount_incref = extern("pcc_refcount_incref", (c_ptr,), c_int64)
pcc_refcount_decref = extern("pcc_refcount_decref", (c_ptr,), c_int64)
pcc_gc_publish_initialized = extern(
    "pcc_gc_publish_initialized", (c_ptr,), c_void
)
pcc_gc_alloc         = extern("pcc_gc_alloc",         (c_int64, c_int32, c_int32), c_ptr)
pcc_gc_load_ptr      = extern("pcc_gc_load_ptr",      (c_ptr, c_ptr), c_ptr)
pcc_gc_backend = extern("pcc_gc_backend", (), c_int64)
pcc_gc_pointer_is_managed = extern("pcc_gc_pointer_is_managed", (c_ptr,), c_int64)
pcc_gc_store_ptr     = extern("pcc_gc_store_ptr",     (c_ptr, c_ptr, c_ptr), c_void)
pcc_gc_store_root    = extern("pcc_gc_store_root",    (c_ptr, c_ptr), c_void)
pcc_gc_scheduler_root_register_handle = extern(
    "pcc_gc_scheduler_root_register_handle", (c_ptr,), c_ptr
)
pcc_gc_scheduler_root_unregister_handle = extern(
    "pcc_gc_scheduler_root_unregister_handle", (c_ptr,), c_void
)
py_exc_set_context = extern("py_exc_set_context", (c_ptr, c_ptr), c_void)
pcc_gc_free_object_memory = extern("pcc_gc_free_object_memory", (c_ptr,), c_void)
py_runtime_error_if_unset = extern(
    "py_runtime_error_if_unset", (c_ptr, c_ptr), c_ptr
)
pcc_gc_backend4_zpage_register_owner_payload_span = extern(
    "pcc_gc_backend4_zpage_register_owner_payload_span",
    (c_ptr, c_ptr, c_int64), c_int64,
)

py_list_get = extern("py_list_get", (c_ptr, c_int64), c_ptr)
py_list_len = extern("py_list_len", (c_ptr,), c_int64)
py_list_set = extern("py_list_set", (c_ptr, c_int64, c_ptr), c_void)
pcc_gc_retain_known = extern("pcc_gc_retain_known", (c_ptr,), c_ptr)
pcc_gc_release_known = extern("pcc_gc_release_known", (c_ptr,), c_void)
pcc_diagnostics_runtime_log_event_code = extern("pcc_diagnostics_runtime_log_event_code", (c_int32, c_int32, c_int64, c_int64, c_ptr), c_void)


pcc_gc_frame_enter = extern("pcc_gc_frame_enter", (c_ptr, c_ptr), c_void)
pcc_gc_frame_leave = extern("pcc_gc_frame_leave", (c_ptr,), c_void)
pcc_gc_take_pinned_slot = extern("pcc_gc_take_pinned_slot", (c_ptr, c_int64), c_ptr)
pcc_gc_note_write_barrier = extern("pcc_gc_note_write_barrier", (c_ptr, c_ptr), c_void)
define_global_i32("pcc_gen_sync_frame_map", 5)

_GEN_FRAME_FACTORY_VALUE = 0
_GEN_FRAME_FACTORY_ERROR = 1
_GEN_FRAME_FACTORY_SLOT_COUNT = 2
define_global_i32("pcc_gen_frame_factory_map", _GEN_FRAME_FACTORY_SLOT_COUNT)
pcc_gc_foreign_lease_acquire = extern("pcc_gc_foreign_lease_acquire", (c_ptr,), c_int64)
pcc_gc_foreign_lease_release = extern("pcc_gc_foreign_lease_release", (c_ptr, c_int64), c_int64)
pcc_gc_note_slot_write_barrier = extern(
    "pcc_gc_note_slot_write_barrier", (c_ptr, c_ptr, c_ptr), c_void
)
py_tls_exc_swap_slot = extern("py_tls_exc_swap_slot", (c_ptr,), c_void)
pcc_platform_abort = extern("pcc_platform_abort", (), c_void)
pcc_gc_retain_plan_prepare_locked = extern(
    "pcc_gc_retain_plan_prepare_locked", (c_ptr, c_ptr), c_ptr
)
pcc_gc_retain_plan_finish = extern("pcc_gc_retain_plan_finish", (c_ptr,), c_void)
pcc_gc_store_ptr_plan_init = extern(
    "pcc_gc_store_ptr_plan_init", (c_ptr, c_ptr, c_int64), c_void
)
pcc_gc_store_ptr_plan_commit_locked = extern(
    "pcc_gc_store_ptr_plan_commit_locked", (c_ptr, c_ptr, c_ptr, c_ptr), c_int64
)
pcc_gc_store_ptr_plan_finish = extern("pcc_gc_store_ptr_plan_finish", (c_ptr,), c_void)
define_global_i32("pcc_gen_inline_borrowed_map", -2)
define_global_i32("pcc_gen_inline_result_map", 1)
_GEN_INLINE_OWNER = 0
_GEN_INLINE_VALUE = 8
_GEN_INLINE_BORROWED_BYTES = 16


def _inline_frame_access(frame, index: int, value, operation: int):
    """Preserve an existing inline frame across a collector-mode switch.

    Operation 0 copies, 1 takes and 2 replaces. Frame allocation selects GC0,
    but changing collectors must keep existing objects usable and traceable.
    The graph transaction covers every derived address; the result stays rooted
    through deferred reference-count work and terminal root teardown.
    """
    borrowed = stack_alloc(_GEN_INLINE_BORROWED_BYTES)
    owner_slot = ptr_add(borrowed, _GEN_INLINE_OWNER)
    value_slot = ptr_add(borrowed, _GEN_INLINE_VALUE)
    store_ptr(owner_slot, 0, frame)
    store_ptr(value_slot, 0, value)
    result_slot = stack_alloc(C_POINTER_SIZE)
    store_ptr(result_slot, 0, null())
    pcc_gc_frame_enter(global_addr("pcc_gen_inline_borrowed_map"), borrowed)
    pcc_gc_frame_enter(global_addr("pcc_gen_inline_result_map"), result_slot)
    retain_plan = stack_alloc(56)
    store_plan = stack_alloc(128)
    if operation != 0:
        pcc_gc_store_ptr_plan_init(store_plan, load_ptr(owner_slot, 0), pcc_gc_backend())
    pcc_py_gc_minor_graph_lock()
    frame = pcc_gc_load_ptr(null(), owner_slot)
    valid: int = 0
    if index >= 0 and index < load_i64(frame, PYINLINEGENOBJECT_LENGTH_OFFSET):
        valid = 1
        slot = ptr_add(frame, PYINLINEGENOBJECT_ITEMS_OFFSET + index * C_POINTER_SIZE)
        if operation != 2:
            current = pcc_gc_load_ptr(frame, slot)
            current = pcc_gc_retain_plan_prepare_locked(retain_plan, current)
            store_ptr(result_slot, 0, current)
        if operation != 0:
            replacement = null()
            if operation == 2:
                replacement = pcc_gc_load_ptr(null(), value_slot)
            pcc_gc_store_ptr_plan_commit_locked(store_plan, frame, slot, replacement)
    pcc_py_gc_minor_graph_unlock()
    if valid != 0 and operation != 2:
        pcc_gc_retain_plan_finish(retain_plan)
    if operation != 0:
        pcc_gc_store_ptr_plan_finish(store_plan)
    pcc_py_gc_minor_graph_lock()
    result = pcc_gc_load_ptr(null(), result_slot)
    prior: i64 = 0
    if ptr_is_null(result) == 0 and is_tagged_int(result) == 0:
        prior = i64(load_i32(result, PYOBJECTHEADER_FLAGS_OFFSET) & PY_FLAG_GC_PINNED)
        pcc_gc_pin(result)
    pcc_py_gc_minor_graph_unlock()
    pcc_gc_frame_leave(result_slot)
    pcc_gc_frame_leave(borrowed)
    return pcc_gc_take_pinned_slot(result_slot, prior)


@c_abi_export("py_gen_frame_get")
def py_gen_frame_get(frame, index: int):
    """NEW ref from a compiler-private, fixed-size managed-object frame."""
    if ptr_is_null(frame) != 0:
        return py_list_get(frame, index)
    if load_i32(frame, 8) == PY_TYPE_GEN and (load_i32(frame, PYOBJECTHEADER_FLAGS_OFFSET) & PY_FLAG_GEN_INLINE_FRAME) != 0:
        if pcc_gc_backend() != 0:
            return _inline_frame_access(frame, index, null(), 0)
        if index < 0 or index >= load_i64(frame, PYINLINEGENOBJECT_LENGTH_OFFSET):
            return null()
        return pcc_gc_retain_known(load_ptr(frame, PYINLINEGENOBJECT_ITEMS_OFFSET + index * C_POINTER_SIZE))
    if pcc_gc_backend() != 0:
        return py_list_get(frame, index)
    if index < 0 or index >= load_i64(frame, PYLISTOBJECT_LENGTH_OFFSET):
        return py_list_get(frame, index)
    items = load_ptr(frame, PYLISTOBJECT_ITEMS_OFFSET)
    return pcc_gc_retain_known(load_ptr(items, index * C_POINTER_SIZE))


@c_abi_export("py_gen_frame_take")
def py_gen_frame_take(frame, index: int):
    """Move the owner out of a compiler-private frame cell, emptying it.

    A running generator owns its locals and operand roots in its own rooted
    slots; the frame holds them only while it is suspended. Copying them out
    on resume kept every saved value alive until the next suspension or
    completion, after the function had already rebound or dropped it.
    """
    if ptr_is_null(frame) != 0:
        return py_list_get(frame, index)
    if load_i32(frame, 8) == PY_TYPE_GEN and (load_i32(frame, PYOBJECTHEADER_FLAGS_OFFSET) & PY_FLAG_GEN_INLINE_FRAME) != 0:
        if pcc_gc_backend() != 0:
            return _inline_frame_access(frame, index, null(), 1)
        if index < 0 or index >= load_i64(frame, PYINLINEGENOBJECT_LENGTH_OFFSET):
            return null()
        slot = ptr_add(frame, PYINLINEGENOBJECT_ITEMS_OFFSET + index * C_POINTER_SIZE)
        value = load_ptr(slot, 0)
        store_ptr(slot, 0, null())
        return value
    if pcc_gc_backend() != 0:
        value = py_list_get(frame, index)
        py_list_set(frame, index, null())
        return value
    if index < 0 or index >= load_i64(frame, PYLISTOBJECT_LENGTH_OFFSET):
        return py_list_get(frame, index)
    if load_i32(global_addr("pcc_diagnostics_runtime_log_fast_state"), 0) != 0:
        pcc_diagnostics_runtime_log_event_code(2, 3, 0, 0, frame)
    items = load_ptr(frame, PYLISTOBJECT_ITEMS_OFFSET)
    slot = ptr_add(items, index * C_POINTER_SIZE)
    value = load_ptr(slot, 0)
    store_ptr(slot, 0, null())
    return value


@c_abi_export("py_gen_frame_set")
def py_gen_frame_set(frame, index: int, value) -> None:
    """Retaining frame store; caller keeps the private frame alive.

    Its slots contain only Python objects, tagged values or NULL. GC0 needs
    no tracing barrier; moving/tracing collectors use the original setter.
    Publication precedes old-value finalization exactly as in py_list_set.
    """
    if ptr_is_null(frame) != 0:
        py_list_set(frame, index, value)
        return
    if load_i32(frame, 8) == PY_TYPE_GEN and (load_i32(frame, PYOBJECTHEADER_FLAGS_OFFSET) & PY_FLAG_GEN_INLINE_FRAME) != 0:
        if pcc_gc_backend() != 0:
            _inline_frame_access(frame, index, value, 2)
            return
        if index < 0 or index >= load_i64(frame, PYINLINEGENOBJECT_LENGTH_OFFSET):
            return
        slot = ptr_add(frame, PYINLINEGENOBJECT_ITEMS_OFFSET + index * C_POINTER_SIZE)
        old = load_ptr(slot, 0)
        if ptr_eq(old, value) != 0:
            return
        pcc_gc_retain_known(value)
        store_ptr(slot, 0, value)
        pcc_gc_release_known(old)
        return
    if pcc_gc_backend() != 0:
        py_list_set(frame, index, value)
        return
    if index < 0 or index >= load_i64(frame, PYLISTOBJECT_LENGTH_OFFSET):
        py_list_set(frame, index, value)
        return
    if load_i32(global_addr("pcc_diagnostics_runtime_log_fast_state"), 0) != 0:
        pcc_diagnostics_runtime_log_event_code(2, 3, 0, 0, frame)
    items = load_ptr(frame, PYLISTOBJECT_ITEMS_OFFSET)
    slot = ptr_add(items, index * C_POINTER_SIZE)
    old = load_ptr(slot, 0)
    if ptr_eq(old, value) != 0:
        return
    pcc_gc_retain_known(value)
    store_ptr(slot, 0, value)
    pcc_gc_release_known(old)


@c_abi_export("py_gen_frame_len")
def py_gen_frame_len(frame) -> int:
    if ptr_is_null(frame) == 0 and is_tagged_int(frame) == 0:
        if load_i32(frame, 8) == PY_TYPE_GEN and (load_i32(frame, PYOBJECTHEADER_FLAGS_OFFSET) & PY_FLAG_GEN_INLINE_FRAME) != 0:
            return load_i64(frame, PYINLINEGENOBJECT_LENGTH_OFFSET)
    return py_list_len(frame)


def _require_result(result, helper_name, message):
    if ptr_is_null(result):
        py_runtime_error_if_unset(helper_name, message)
    return result


def _gen_frame_factory_finish(slots, token: i64, success: i64):
    value_slot = ptr_add(slots, _GEN_FRAME_FACTORY_VALUE * C_POINTER_SIZE)
    error_slot = ptr_add(slots, _GEN_FRAME_FACTORY_ERROR * C_POINTER_SIZE)
    if success == 0:
        py_tls_exc_swap_slot(error_slot)
        if token >= 0:
            if pcc_gc_foreign_lease_release(value_slot, token) != 0:
                pcc_platform_abort()
                return null()
        pcc_gc_store_root(value_slot, null())
        py_clear_exception()
        py_tls_exc_swap_slot(error_slot)
        pcc_gc_frame_leave(slots)
        return null()
    # Bridge the terminal raw ABI with the same pin/slot transfer protocol
    # as ordinary list construction. The counted lease remains live through
    # every allocation, poll and initialization operation above this point.
    # That lease already protects the private root's address through pinning;
    # taking the global graph lock again only serializes unrelated factories.
    value = pcc_gc_load_ptr(null(), value_slot)
    prior: i64 = i64(load_i32(value, PYOBJECTHEADER_FLAGS_OFFSET) & PY_FLAG_GC_PINNED)
    pcc_gc_pin(value)
    if pcc_gc_foreign_lease_release(value_slot, token) != 0:
        pcc_platform_abort()
        return null()
    pcc_gc_frame_leave(slots)
    return pcc_gc_take_pinned_slot(value_slot, prior)


def _gen_frame_factory_oom(slots, token: i64, message):
    if ptr_is_null(py_current_exception()):
        py_raise_owned(py_exc_new(19, message))
    return _gen_frame_factory_finish(slots, token, 0)


@c_abi_export("py_gen_frame_new")
def py_gen_frame_new(slot_count: i64):
    """Construct the compiler's fixed-size list frame before publishing it."""
    if slot_count < 0 or slot_count > 134217728:
        return _require_result(null(), cstr("py_gen_frame_new"), cstr("invalid generator frame size"))
    slots = stack_alloc(_GEN_FRAME_FACTORY_SLOT_COUNT * C_POINTER_SIZE)
    memset(slots, 0, _GEN_FRAME_FACTORY_SLOT_COUNT * C_POINTER_SIZE)
    pcc_gc_frame_enter(global_addr("pcc_gen_frame_factory_map"), slots)
    value_slot = ptr_add(slots, _GEN_FRAME_FACTORY_VALUE * C_POINTER_SIZE)
    store_ptr(value_slot, 0, pcc_gc_alloc(PYLISTOBJECT_SIZE, PY_TYPE_LIST, 0))
    if ptr_is_null(load_ptr(value_slot, 0)):
        return _gen_frame_factory_oom(slots, -1, cstr("generator frame allocation failed"))
    frame = load_ptr(value_slot, 0)
    capacity: i64 = slot_count
    if capacity < 4:
        capacity = 4
    store_i64(frame, PYLISTOBJECT_LENGTH_OFFSET, 0)
    store_i64(frame, PYLISTOBJECT_CAPACITY_OFFSET, capacity)
    store_ptr(frame, PYLISTOBJECT_ITEMS_OFFSET, null())
    token: i64 = i64(pcc_gc_foreign_lease_acquire(value_slot))
    if token < 0:
        _require_result(null(), cstr("py_gen_frame_new"), cstr("generator frame address lease failed"))
        return _gen_frame_factory_finish(slots, token, 0)
    frame = load_ptr(value_slot, 0)
    pcc_gc_note_slot_write_barrier(null(), value_slot, frame)
    slot_bytes: i64 = i64(C_POINTER_SIZE)
    items = malloc(capacity * slot_bytes)
    if ptr_is_null(items):
        return _gen_frame_factory_oom(slots, token, cstr("generator frame slots allocation failed"))
    frame = load_ptr(value_slot, 0)
    store_ptr(frame, PYLISTOBJECT_ITEMS_OFFSET, items)
    none = global_load_ptr("py_None")
    # This index describes bounded ABI storage, not a Python integer value.
    index: i64 = 0
    # None is immortal. Fill the unpublished frame without retain/release or
    # growth; subsequent argument/local stores use the ordinary list barriers.
    while index < slot_count:
        store_ptr(items, index * slot_bytes, none)
        index += 1
    store_i64(frame, PYLISTOBJECT_LENGTH_OFFSET, slot_count)
    pcc_gc_backend4_zpage_register_owner_payload_span(frame, items, capacity * slot_bytes)
    py_gc_track(frame)
    pcc_gc_publish_initialized(frame)
    return _gen_frame_factory_finish(slots, token, 1)


@c_abi_export("py_gen_inline_frame_new")
def py_gen_inline_frame_new(slot_count: i64):
    """Coallocate a GC0 generator and its compiler-private fixed frame.

    The frame view is the generator itself, never an interior pointer or an
    owning self edge. Other collectors retain their established list layout.
    py_gen_from_inline_frame installs the resume function and acquires the caller's
    result owner, preserving the existing factory construction lease.
    """
    if pcc_gc_backend() != 0:
        return py_gen_frame_new(slot_count)
    if slot_count < 0 or slot_count > 134217728:
        return _require_result(null(), cstr("py_gen_inline_frame_new"), cstr("invalid generator frame size"))
    slots = stack_alloc(_GEN_FRAME_FACTORY_SLOT_COUNT * C_POINTER_SIZE)
    memset(slots, 0, _GEN_FRAME_FACTORY_SLOT_COUNT * C_POINTER_SIZE)
    pcc_gc_frame_enter(global_addr("pcc_gen_frame_factory_map"), slots)
    value_slot = ptr_add(slots, _GEN_FRAME_FACTORY_VALUE * C_POINTER_SIZE)
    items_offset: i64 = i64(PYINLINEGENOBJECT_ITEMS_OFFSET)
    slot_stride: i64 = i64(C_POINTER_SIZE)
    size: i64 = items_offset + slot_count * slot_stride
    store_ptr(value_slot, 0, pcc_gc_alloc(size, PY_TYPE_GEN, PY_FLAG_GEN_INLINE_FRAME))
    if ptr_is_null(load_ptr(value_slot, 0)):
        return _gen_frame_factory_oom(slots, -1, cstr("inline generator allocation failed"))
    gen = load_ptr(value_slot, 0)
    store_ptr(gen, 16, null())
    store_ptr(gen, 24, null())
    store_i64(gen, 32, 0)
    store_i64(gen, 40, 0)
    store_ptr(gen, 48, global_load_ptr("py_None"))
    store_i64(gen, PYINLINEGENOBJECT_LENGTH_OFFSET, 0)
    token: i64 = i64(pcc_gc_foreign_lease_acquire(value_slot))
    if token < 0:
        _require_result(null(), cstr("py_gen_inline_frame_new"), cstr("generator frame address lease failed"))
        return _gen_frame_factory_finish(slots, token, 0)
    gen = load_ptr(value_slot, 0)
    none = global_load_ptr("py_None")
    index: i64 = 0
    while index < slot_count:
        store_ptr(gen, items_offset + index * slot_stride, none)
        index += 1
    store_i64(gen, PYINLINEGENOBJECT_LENGTH_OFFSET, slot_count)
    py_gc_track(gen)
    pcc_gc_publish_initialized(gen)
    return _gen_frame_factory_finish(slots, token, 1)

@c_abi_export("py_gen_frame_save")
def py_gen_frame_save(frame, slot_addresses, slot_count: int) -> int:
    """Batch one GC0 save; other collectors keep the ordinary setter path."""
    if pcc_gc_backend() != 0 or ptr_is_null(frame) or ptr_is_null(slot_addresses):
        return 0
    if slot_count < 0 or pcc_gc_pointer_is_managed(frame) == 0:
        return 0
    if load_i32(frame, 8) != PY_TYPE_LIST:
        return 0
    capacity: int = load_i64(frame, PYLISTOBJECT_CAPACITY_OFFSET)
    if load_i64(frame, PYLISTOBJECT_LENGTH_OFFSET) != slot_count:
        return 0
    if capacity < slot_count or capacity > 134217728:
        return 0
    items = load_ptr(frame, PYLISTOBJECT_ITEMS_OFFSET)
    if ptr_is_null(items):
        return 0
    index: int = 0
    while index < slot_count:
        source_slot = load_ptr(slot_addresses, index * C_POINTER_SIZE)
        value = pcc_gc_load_ptr(null(), source_slot)
        pcc_gc_store_ptr(frame, ptr_add(items, index * C_POINTER_SIZE), value)
        index += 1
    return 1


@c_abi_export("py_gen_from_inline_frame")
def py_gen_from_inline_frame(resume, frame):
    """Finish the compiler's private construction lease, never arbitrary data."""
    if ptr_is_null(resume) == 0 and ptr_is_null(frame) == 0 and is_tagged_int(frame) == 0:
        if load_i32(frame, 8) == PY_TYPE_GEN and (load_i32(frame, PYOBJECTHEADER_FLAGS_OFFSET) & PY_FLAG_GEN_INLINE_FRAME) != 0:
            if ptr_is_null(load_ptr(frame, 16)) != 0:
                store_ptr(frame, 16, resume)
                return pcc_gc_retain_known(frame)
    return py_gen_new(resume, frame)


@c_abi_export("py_gen_new")
def py_gen_new(resume, frame):
    if ptr_is_null(resume) or ptr_is_null(frame):
        return _require_result(
            null(),
            cstr("py_gen_new"),
            cstr("generator construction received a NULL resume thunk or frame"),
        )
    g = pcc_gc_alloc(56, PY_TYPE_GEN, 0)
    if ptr_is_null(g):
        return _require_result(
            null(),
            cstr("pcc_gc_alloc"),
            cstr("generator construction could not allocate generator state"),
        )
    store_ptr(g, 16, resume)
    store_ptr(g, 24, null())
    store_i64(g, 32, 0)       # state
    store_i64(g, 40, 0)       # done
    store_ptr(g, 48, null())   # pending send value
    pcc_gc_store_ptr(g, ptr_add(g, 24), frame)
    pcc_gc_store_ptr(g, ptr_add(g, 48), global_load_ptr("py_None"))
    py_gc_track(g)
    pcc_gc_publish_initialized(g)
    return g


@c_abi_export("py_dealloc_gen")
def py_dealloc_gen(o) -> None:
    if (load_i32(o, PYOBJECTHEADER_FLAGS_OFFSET) & PY_FLAG_GEN_INLINE_FRAME) != 0:
        count: i64 = i64(load_i64(o, PYINLINEGENOBJECT_LENGTH_OFFSET))
        index: i64 = 0
        items_offset: i64 = i64(PYINLINEGENOBJECT_ITEMS_OFFSET)
        slot_stride: i64 = i64(C_POINTER_SIZE)
        while index < count:
            slot = ptr_add(o, items_offset + index * slot_stride)
            value = load_ptr(slot, 0)
            store_ptr(slot, 0, null())
            py_decref(value)
            index += 1
    frame = pcc_gc_load_ptr(o, ptr_add(o, 24))
    if not ptr_is_null(frame):
        py_decref(frame)
    send_value = pcc_gc_load_ptr(o, ptr_add(o, 48))
    if not ptr_is_null(send_value):
        py_decref(send_value)
    pcc_gc_free_object_memory(o)


@c_abi_export("py_gen_finalize")
def py_gen_finalize(gen) -> None:
    # An unstarted generator has not entered its try/finally body.
    if ptr_is_null(gen) or load_i64(gen, 40) != 0 or load_i64(gen, 32) == 0:
        return
    flags: int = load_i32(gen, 12)
    if (flags & 4) != 0:
        return
    store_i32(gen, 12, flags | 4)
    py_incref(gen)
    pcc_gc_pin(gen)
    py_weakref_invalidate(gen)
    saved = py_current_exception()
    if ptr_is_null(saved) == 0:
        py_incref(saved)
        pcc_gc_pin(saved)
    py_clear_exception()
    closed = py_gen_close(gen)
    if ptr_is_null(closed) == 0:
        py_decref(closed)
    # As with __del__, a cleanup error must not replace the caller's error.
    # Reporting finalizer errors through the unraisable channel remains a
    # shared diagnostics boundary.
    py_clear_exception()
    if ptr_is_null(saved) == 0:
        py_raise(saved)
        pcc_gc_unpin(saved)
        py_decref(saved)
    pcc_gc_unpin(gen)
    py_decref(gen)


@c_abi_export("py_gen_finalize_from_dealloc")
def py_gen_finalize_from_dealloc(gen) -> int:
    if load_i64(gen, 40) != 0 or load_i64(gen, 32) == 0 or (load_i32(gen, 12) & 4) != 0:
        return 0
    # Keep a guard owner while close() borrows the zero-refcount generator.
    # Drop the guard with the shared counter primitive, avoiding recursive
    # deallocation before the outer terminal-refcount path has resumed.
    store_i32(gen, 12, load_i32(gen, 12) & ~524288)
    pcc_refcount_incref(gen)
    py_gen_finalize(gen)
    remaining: int = pcc_refcount_decref(gen)
    if remaining > 0:
        py_gc_track(gen)
        return 1
    store_i32(gen, 12, load_i32(gen, 12) | 524288)
    return 0


def _checked_gen(gen):
    if ptr_is_null(gen):
        exc = py_exc_new(3, null())
        py_raise_owned(exc)
        return null()
    if is_tagged_int(gen):
        exc = py_exc_new(3, null())
        py_raise_owned(exc)
        return null()
    if load_i32(gen, 8) != PY_TYPE_GEN:
        exc = py_exc_new(3, null())
        py_raise_owned(exc)
        return null()
    return gen


@c_abi_export("py_gen_set_may_park")
def py_gen_set_may_park(gen) -> None:
    gen = _checked_gen(gen)
    if ptr_is_null(gen):
        return
    store_i32(gen, 12, load_i32(gen, 12) | 1048576)


@c_abi_export("py_gen_is_may_park")
def py_gen_is_may_park(gen) -> int:
    if ptr_is_null(gen) or is_tagged_int(gen):
        return 0
    if load_i32(gen, 8) != PY_TYPE_GEN:
        return 0
    return 1 if (load_i32(gen, 12) & 1048576) != 0 else 0


@c_abi_export("py_gen_is_continuation")
def py_gen_is_continuation(gen) -> int:
    if ptr_is_null(gen) or is_tagged_int(gen):
        return 0
    if load_i32(gen, 8) != PY_TYPE_GEN:
        return 0
    flags: int = load_i32(gen, 12)
    return 1 if (flags & 1048576) != 0 and (flags & PY_FLAG_GEN_SOURCE) == 0 else 0


def _set_send_value(gen, value) -> None:
    if ptr_is_null(value):
        value = global_load_ptr("py_None")
    pcc_gc_store_ptr(gen, ptr_add(gen, 48), value)


@c_abi_export("py_gen_state")
def py_gen_state(gen) -> int:
    gen = _checked_gen(gen)
    if ptr_is_null(gen):
        return -1
    state: int = load_i64(gen, 32)
    # A suspended coroutine may temporarily own raw throw arguments in its
    # traced send slot. Dispatch still targets the same positive resume index.
    return -state if state < 0 else state


@c_abi_export("py_gen_set_state")
def py_gen_set_state(gen, state: int) -> None:
    gen = _checked_gen(gen)
    if ptr_is_null(gen):
        return
    store_i64(gen, 32, state)


@c_abi_export("py_gen_set_done")
def py_gen_set_done(gen) -> None:
    gen = _checked_gen(gen)
    if ptr_is_null(gen):
        return
    store_i64(gen, 40, 1)


@c_abi_export("py_gen_is_done")
def py_gen_is_done(gen) -> int:
    gen = _checked_gen(gen)
    if ptr_is_null(gen):
        return 1
    if load_i64(gen, 40) != 0:
        return 1
    return 0


# A driver that consumes a generator's return (py_gen_next_direct) names it
# here for the duration of one resume. Record: the pinned target generator,
# then whether its return was delivered.
define_thread_local_ptr_null("pcc_tls_gen_direct_request")
_DIRECT_TARGET = 0
_DIRECT_DELIVERED = 8
_DIRECT_BYTES = 16


def _direct_request_for(gen):
    request = global_load_ptr("pcc_tls_gen_direct_request")
    if ptr_is_null(request) == 0 and ptr_eq(load_ptr(request, _DIRECT_TARGET), gen) != 0:
        return request
    return null()


@c_abi_export("py_gen_finish")
def py_gen_finish(gen, value):
    gen = _checked_gen(gen)
    if ptr_is_null(gen):
        return null()
    store_i64(gen, 40, 1)
    request = _direct_request_for(gen)
    if ptr_is_null(request) == 0:
        # The driving py_gen_next_direct takes the value from the finished
        # generator's send slot; no StopIteration is built for it.
        _set_send_value(gen, value)
        store_i64(request, _DIRECT_DELIVERED, 1)
        return null()
    if ptr_is_null(value) or ptr_eq(value, global_load_ptr("py_None")):
        stop = py_exc_new(8, null())
    else:
        stop = py_exc_new_with_value(8, value)       # PY_EXC_STOPITERATION
    if ptr_is_null(stop):
        return _require_result(
            null(),
            cstr("py_exc_new_with_value"),
            cstr("generator finish could not allocate StopIteration"),
        )
    py_raise(stop)
    py_decref(stop)
    return null()


@c_abi_export("py_gen_completed_resume")
def py_gen_completed_resume(gen, value):
    if not ptr_is_null(py_current_exception()):
        py_gen_set_done(gen)
        return null()
    return py_gen_finish(gen, value)


@c_abi_export("py_gen_completed")
def py_gen_completed(value):
    # The resume ABI accepts arbitrary managed userdata. A completed call
    # needs its result owner, not a list of suspended Python locals.
    if ptr_is_null(value):
        value = global_load_ptr("py_None")
    gen = py_gen_new(function_addr("py_gen_completed_resume"), value)
    if not ptr_is_null(gen):
        py_gen_set_may_park(gen)
    return gen


@c_abi_export("py_gen_take_completed")
def py_gen_take_completed(gen):
    # NULL means "use the normal generator protocol". Preserve any pending
    # exception; only a fresh completed continuation can bypass StopIteration.
    if not ptr_is_null(py_current_exception()):
        return null()
    gen = _checked_gen(gen)
    if ptr_is_null(gen) or load_i64(gen, 40) != 0:
        return null()
    resume = load_ptr(gen, 16)
    if ptr_eq(resume, function_addr("py_gen_completed_resume")) == 0:
        return null()
    store_i64(gen, 40, 1)
    # Borrowed from gen. The caller captures this owner before releasing gen.
    return pcc_gc_load_ptr(gen, ptr_add(gen, 24))


def _gen_sync_clear(slots, pins, offset: int) -> None:
    value = load_ptr(slots, offset)
    if ptr_is_null(value) == 0 and is_tagged_int(value) == 0:
        pcc_gc_unpin(value)
        if load_i64(pins, offset) != 0:
            atomic_rmw_i32("or", value, 12, 64, "relaxed")
    pcc_gc_store_root(ptr_add(slots, offset), null())


def _gen_sync_build(slots, pins) -> None:
    # gen0, result8, error16, yielded24, close-result32. All returned and
    # TLS-borrowed owners are captured before another runtime-port entry.
    completed = py_gen_take_completed(load_ptr(slots, 0))
    if ptr_is_null(completed) == 0:
        if ptr_is_null(completed) == 0 and is_tagged_int(completed) == 0:
            store_i64(pins, 8, load_i32(completed, 12) & 64)
            pcc_gc_pin(completed)
        py_incref(completed)
        store_ptr(slots, 8, completed)
        pcc_gc_note_write_barrier(null(), completed)
        return
    if ptr_is_null(py_current_exception()) == 0:
        return
    yielded = py_gen_next(load_ptr(slots, 0))
    if ptr_is_null(yielded) == 0 and is_tagged_int(yielded) == 0:
        store_i64(pins, 24, load_i32(yielded, 12) & 64)
        pcc_gc_pin(yielded)
    store_ptr(slots, 24, yielded)
    pcc_gc_note_write_barrier(null(), yielded)
    if ptr_is_null(yielded) == 0:
        # Preserve the existing explicit boundary for an unresolved dynamic
        # parking call inside a virtual thread; do not block its carrier.
        _gen_sync_clear(slots, pins, 24)
        gen = load_ptr(slots, 0)
        atomic_rmw_i32("or", gen, 12, 64, "relaxed")
        closed = py_gen_close(gen)
        if ptr_is_null(closed) == 0 and is_tagged_int(closed) == 0:
            store_i64(pins, 32, load_i32(closed, 12) & 64)
            pcc_gc_pin(closed)
        store_ptr(slots, 32, closed)
        pcc_gc_note_write_barrier(null(), closed)
        _gen_sync_clear(slots, pins, 32)
        py_clear_exception()
        py_raise_owned(py_exc_new(7, cstr("a parking call suspended outside a resumable caller")))
        return
    error = py_current_exception()
    if ptr_is_null(error):
        return
    if ptr_is_null(error) == 0 and is_tagged_int(error) == 0:
        store_i64(pins, 16, load_i32(error, 12) & 64)
        pcc_gc_pin(error)
    py_incref(error)
    store_ptr(slots, 16, error)
    pcc_gc_note_write_barrier(null(), error)
    stop_type = py_exc_builtin_class(8)
    error = load_ptr(slots, 16)
    atomic_rmw_i32("or", error, 12, 64, "relaxed")
    if py_exc_matches(error, stop_type) == 0:
        return
    value = py_exc_get_message(load_ptr(slots, 16))
    if ptr_is_null(value):
        value = global_load_ptr("py_None")
    if ptr_is_null(value) == 0 and is_tagged_int(value) == 0:
        store_i64(pins, 8, load_i32(value, 12) & 64)
        pcc_gc_pin(value)
    py_incref(value)
    store_ptr(slots, 8, value)
    pcc_gc_note_write_barrier(null(), value)
    # The message is a borrowed field. Its own root and pin precede clearing
    # TLS or releasing the StopIteration; either operation can run cleanup.
    py_clear_exception()


@c_abi_export("py_gen_run_may_park_sync")
def py_gen_run_may_park_sync(gen):
    slots = stack_alloc(40)
    pins = stack_alloc(40)
    memset(slots, 0, 40)
    memset(pins, 0, 40)
    if ptr_is_null(gen) == 0 and is_tagged_int(gen) == 0:
        store_i64(pins, 0, load_i32(gen, 12) & 64)
        pcc_gc_pin(gen)
    pcc_gc_frame_enter(global_addr("pcc_gen_sync_frame_map"), slots)
    pcc_gc_store_root(slots, gen)
    _gen_sync_build(slots, pins)
    # On failure preserve the current exception across root cleanup. A
    # successfully consumed StopIteration is not restored to TLS.
    failed: int = 1 if ptr_is_null(load_ptr(slots, 8)) else 0
    if failed and ptr_is_null(load_ptr(slots, 16)):
        error = py_current_exception()
        if ptr_is_null(error) == 0:
            if ptr_is_null(error) == 0 and is_tagged_int(error) == 0:
                store_i64(pins, 16, load_i32(error, 12) & 64)
                pcc_gc_pin(error)
            py_incref(error)
            store_ptr(slots, 16, error)
            pcc_gc_note_write_barrier(null(), error)
    prior_result_pin: int = load_i64(pins, 8)
    if ptr_eq(load_ptr(slots, 8), load_ptr(slots, 16)):
        prior_result_pin = load_i64(pins, 16)
    if ptr_eq(load_ptr(slots, 8), load_ptr(slots, 0)):
        prior_result_pin = load_i64(pins, 0)
    _gen_sync_clear(slots, pins, 32)
    _gen_sync_clear(slots, pins, 24)
    _gen_sync_clear(slots, pins, 0)
    # Restoring TLS comes after every other owner release: a generator's
    # finalizer must not replace the callback's original pending exception.
    if failed and ptr_is_null(load_ptr(slots, 16)) == 0:
        py_raise(load_ptr(slots, 16))
    _gen_sync_clear(slots, pins, 16)
    result = load_ptr(slots, 8)
    if ptr_is_null(result) == 0 and is_tagged_int(result) == 0:
        atomic_rmw_i32("or", result, 12, 64, "relaxed")
    pcc_gc_frame_leave(slots)
    return pcc_gc_take_pinned_slot(ptr_add(slots, 8), prior_result_pin)


@c_abi_export("py_gen_next")
def py_gen_next(gen):
    gen = _checked_gen(gen)
    if ptr_is_null(gen):
        return null()
    if load_i64(gen, 40) != 0:
        exc = py_exc_new(8, null())
        py_raise_owned(exc)
        return null()
    resume = load_ptr(gen, 16)
    frame = pcc_gc_load_ptr(gen, ptr_add(gen, 24))
    if (load_i32(gen, PYOBJECTHEADER_FLAGS_OFFSET) & PY_FLAG_GEN_INLINE_FRAME) != 0:
        frame = gen
    if ptr_is_null(resume):
        exc = py_exc_new(8, null())
        py_raise_owned(exc)
        return null()
    _set_send_value(gen, global_load_ptr("py_None"))
    result = call_ptr2(resume, gen, frame)
    # Do not pass a fresh managed result through a runtime-port helper whose
    # entry can poll before the caller captures this ownership transfer.
    if ptr_is_null(result):
        request = _direct_request_for(gen)
        if ptr_is_null(request) != 0 or load_i64(request, _DIRECT_DELIVERED) == 0:
            py_runtime_error_if_unset(cstr("py_gen_next"), cstr("generator resume returned NULL without StopIteration or an exception"))
    return result


@c_abi_export("py_gen_next_direct")
def py_gen_next_direct(gen, returned):
    """py_gen_next for a driver that consumes this generator's return.

    A return stores its value in the finished generator's send slot instead
    of raising StopIteration; ``returned`` then holds 1 and the result is a
    new reference to that value. Generators this one drives keep the ordinary
    protocol. It stays pinned while it runs, so py_gen_finish can recognize
    it by address: no other object can occupy that address meanwhile.
    """
    store_i64(returned, 0, 0)
    gen = _checked_gen(gen)
    if ptr_is_null(gen):
        return null()
    request = stack_alloc(_DIRECT_BYTES)
    store_ptr(request, _DIRECT_TARGET, gen)
    store_i64(request, _DIRECT_DELIVERED, 0)
    prior_pin: int = load_i32(gen, PYOBJECTHEADER_FLAGS_OFFSET) & PY_FLAG_GC_PINNED
    pcc_gc_pin(gen)
    outer = global_load_ptr("pcc_tls_gen_direct_request")
    global_store_ptr("pcc_tls_gen_direct_request", request)
    result = py_gen_next(gen)
    global_store_ptr("pcc_tls_gen_direct_request", outer)
    if load_i64(request, _DIRECT_DELIVERED) != 0:
        result = pcc_gc_load_ptr(gen, ptr_add(gen, 48))
        py_incref(result)
        store_i64(returned, 0, 1)
    pcc_gc_unpin(gen)
    if prior_pin != 0:
        atomic_rmw_i32("or", gen, PYOBJECTHEADER_FLAGS_OFFSET, PY_FLAG_GC_PINNED, "relaxed")
    return result


@c_abi_export("py_gen_send")
def py_gen_send(gen, value):
    if not ptr_is_null(gen):
        if is_tagged_int(gen) == 0:
            if load_i32(gen, 8) == PY_TYPE_COROUTINE:       # PY_TYPE_COROUTINE
                return py_coroutine_send(gen, value, null())
    gen = _checked_gen(gen)
    if ptr_is_null(gen):
        return null()
    if load_i64(gen, 40) != 0:
        exc = py_exc_new(8, null())
        py_raise_owned(exc)
        return null()
    resume = load_ptr(gen, 16)
    frame = pcc_gc_load_ptr(gen, ptr_add(gen, 24))
    if (load_i32(gen, PYOBJECTHEADER_FLAGS_OFFSET) & PY_FLAG_GEN_INLINE_FRAME) != 0:
        frame = gen
    if ptr_is_null(resume):
        exc = py_exc_new(8, null())
        py_raise_owned(exc)
        return null()
    none = global_load_ptr("py_None")
    if load_i64(gen, 32) == 0 and not ptr_is_null(value):
        if ptr_eq(value, none) == 0:
            exc = py_exc_new(3, null())
            py_raise_owned(exc)
            return null()
    _set_send_value(gen, value)
    result = call_ptr2(resume, gen, frame)
    # Do not pass a fresh managed result through a runtime-port helper whose
    # entry can poll before the caller captures this ownership transfer.
    if ptr_is_null(result):
        py_runtime_error_if_unset(cstr("py_gen_send"), cstr("generator send returned NULL without StopIteration or an exception"))
    return result


@c_abi_export("py_gen_throw")
def py_gen_throw(gen, exc):
    if not ptr_is_null(gen) and is_tagged_int(gen) == 0:
        if load_i32(gen, 8) == PY_TYPE_COROUTINE:
            return py_coroutine_send(gen, global_load_ptr("py_None"), exc)
    gen = _checked_gen(gen)
    if ptr_is_null(gen):
        return null()
    if load_i64(gen, 40) != 0:
        stop = py_exc_new(8, null())
        py_raise_owned(stop)
        return null()
    resume = load_ptr(gen, 16)
    frame = pcc_gc_load_ptr(gen, ptr_add(gen, 24))
    if (load_i32(gen, PYOBJECTHEADER_FLAGS_OFFSET) & PY_FLAG_GEN_INLINE_FRAME) != 0:
        frame = gen
    if ptr_is_null(resume):
        stop = py_exc_new(8, null())
        py_raise_owned(stop)
        return null()
    _set_send_value(gen, global_load_ptr("py_None"))
    py_raise(exc)
    result = call_ptr2(resume, gen, frame)
    # Do not pass a fresh managed result through a runtime-port helper whose
    # entry can poll before the caller captures this ownership transfer.
    if ptr_is_null(result):
        py_runtime_error_if_unset(cstr("py_gen_throw"), cstr("generator throw returned NULL without setting an exception"))
    return result


@c_abi_export("py_gen_close")
def py_gen_close(gen):
    gen = _checked_gen(gen)
    if ptr_is_null(gen):
        return null()
    if load_i64(gen, 40) == 0:
        resume = load_ptr(gen, 16)
        if not ptr_is_null(resume):
            gen_slot = stack_alloc(8)
            exc_slot = stack_alloc(8)
            store_ptr(gen_slot, 0, null())
            store_ptr(exc_slot, 0, null())
            gen_root = pcc_gc_scheduler_root_register_handle(gen_slot)
            if ptr_is_null(gen_root):
                root_error = py_exc_new(19, null())
                py_raise(root_error)
                py_decref(root_error)
                return null()
            pcc_gc_store_root(gen_slot, gen)
            exc_root = pcc_gc_scheduler_root_register_handle(exc_slot)
            if ptr_is_null(exc_root):
                pcc_gc_store_root(gen_slot, null())
                pcc_gc_scheduler_root_unregister_handle(gen_root)
                root_error = py_exc_new(19, null())
                py_raise(root_error)
                py_decref(root_error)
                return null()
            exc = py_exc_new(55, null())      # PY_EXC_GENERATOREXIT
            if ptr_is_null(exc):
                pcc_gc_store_root(exc_slot, null())
                pcc_gc_scheduler_root_unregister_handle(exc_root)
                pcc_gc_store_root(gen_slot, null())
                pcc_gc_scheduler_root_unregister_handle(gen_root)
                return null()
            pcc_gc_store_root(exc_slot, exc)
            gen = load_ptr(gen_slot, 0)
            _set_send_value(gen, global_load_ptr("py_None"))
            py_raise(load_ptr(exc_slot, 0))
            py_decref(load_ptr(exc_slot, 0))  # TLS and root own the exception
            # Raising/decref may allocate or safepoint.  Reload the moving
            # generator from its updateable root before reading its frame.
            gen = load_ptr(gen_slot, 0)
            frame = pcc_gc_load_ptr(gen, ptr_add(gen, 24))
            if (load_i32(gen, PYOBJECTHEADER_FLAGS_OFFSET) & PY_FLAG_GEN_INLINE_FRAME) != 0:
                frame = gen
            resume = load_ptr(gen, 16)
            result = call_ptr2(resume, gen, frame)
            if not ptr_is_null(result):
                py_decref(result)
                pcc_gc_store_root(exc_slot, null())
                pcc_gc_scheduler_root_unregister_handle(exc_root)
                pcc_gc_store_root(gen_slot, null())
                pcc_gc_scheduler_root_unregister_handle(gen_root)
                runtime_error = py_exc_new(7, cstr("generator ignored GeneratorExit"))
                py_raise(runtime_error)
                py_decref(runtime_error)
                return null()
            if ptr_is_null(py_current_exception()):
                _require_result(
                    null(),
                    cstr("py_gen_close"),
                    cstr(
                        "generator close resume returned NULL without setting an exception"
                    ),
                )
            stop_cls = py_exc_builtin_class(8)
            stopped = py_exc_matches(py_current_exception(), stop_cls)
            exit_cls = py_exc_builtin_class(55)
            exiting = py_exc_matches(py_current_exception(), exit_cls)
            gen = load_ptr(gen_slot, 0)
            if stopped != 0:
                store_i64(gen, 40, 1)
                # Python 3.13+: close() returns the generator's return value.
                # Reuse the registered injected-exception slot for that owner
                # before clearing the StopIteration that lends its field.
                value = py_exc_get_message(py_current_exception())
                if ptr_is_null(value):
                    value = global_load_ptr("py_None")
                pcc_gc_store_root(exc_slot, value)
                py_clear_exception()
                # Acquiring the graph lease can park and move the rooted
                # result. Reload only under that lease, then pin before any
                # cleanup operation can expose another safepoint.
                pcc_py_gc_minor_graph_lock()
                value = pcc_gc_load_ptr(null(), exc_slot)
                store_ptr(exc_slot, 0, value)
                prior_pin: int = 0
                if is_tagged_int(value) == 0:
                    prior_pin = load_i32(value, 12) & 64
                    pcc_gc_pin(value)
                pcc_py_gc_minor_graph_unlock()
                pcc_gc_store_root(gen_slot, null())
                pcc_gc_scheduler_root_unregister_handle(gen_root)
                pcc_gc_scheduler_root_unregister_handle(exc_root)
                return pcc_gc_take_pinned_slot(exc_slot, prior_pin)
            elif exiting != 0:
                # Any GeneratorExit is a successful close, including one
                # explicitly raised by the body. Other BaseExceptions escape.
                store_i64(gen, 40, 1)
                py_clear_exception()
            else:
                store_i64(gen, 40, 1)
                pcc_gc_store_root(exc_slot, null())
                pcc_gc_scheduler_root_unregister_handle(exc_root)
                pcc_gc_store_root(gen_slot, null())
                pcc_gc_scheduler_root_unregister_handle(gen_root)
                return null()
            pcc_gc_store_root(exc_slot, null())
            pcc_gc_scheduler_root_unregister_handle(exc_root)
            pcc_gc_store_root(gen_slot, null())
            pcc_gc_scheduler_root_unregister_handle(gen_root)
    none = global_load_ptr("py_None")
    py_incref(none)
    return none


@c_abi_export("py_gen_close_preserving_exception")
def py_gen_close_preserving_exception(gen) -> int:
    gen_slot = stack_alloc(8)
    store_ptr(gen_slot, 0, null())
    gen_root = pcc_gc_scheduler_root_register_handle(gen_slot)
    if ptr_is_null(gen_root):
        return -1
    pcc_gc_store_root(gen_slot, gen)
    borrowed = py_current_exception()
    saved_slot = stack_alloc(8)
    store_ptr(saved_slot, 0, null())
    saved_root = null()
    if ptr_is_null(borrowed) == 0:
        saved_root = pcc_gc_scheduler_root_register_handle(saved_slot)
        if ptr_is_null(saved_root):
            pcc_gc_store_root(gen_slot, null())
            pcc_gc_scheduler_root_unregister_handle(gen_root)
            return -1
        pcc_gc_store_root(saved_slot, borrowed)
        py_clear_exception()

    closed = py_gen_close(load_ptr(gen_slot, 0))
    if ptr_is_null(closed) == 0:
        py_decref(closed)
        saved = load_ptr(saved_slot, 0)
        if ptr_is_null(saved) == 0:
            py_raise(saved)
        if ptr_is_null(saved_root) == 0:
            pcc_gc_store_root(saved_slot, null())
            pcc_gc_scheduler_root_unregister_handle(saved_root)
        pcc_gc_store_root(gen_slot, null())
        pcc_gc_scheduler_root_unregister_handle(gen_root)
        return 0

    cleanup_error = py_current_exception()
    saved = load_ptr(saved_slot, 0)
    if (
        ptr_is_null(cleanup_error) == 0
        and ptr_is_null(saved) == 0
        and ptr_eq(cleanup_error, saved) == 0
    ):
        py_exc_set_context(cleanup_error, saved)
    if ptr_is_null(saved_root) == 0:
        pcc_gc_store_root(saved_slot, null())
        pcc_gc_scheduler_root_unregister_handle(saved_root)
    pcc_gc_store_root(gen_slot, null())
    pcc_gc_scheduler_root_unregister_handle(gen_root)
    return -1


@c_abi_export("py_gen_take_send")
def py_gen_take_send(gen):
    gen = _checked_gen(gen)
    if ptr_is_null(gen):
        return null()
    none = global_load_ptr("py_None")
    value = pcc_gc_load_ptr(gen, ptr_add(gen, 48))
    if ptr_is_null(value):
        value = none
    py_incref(value)
    pcc_gc_store_ptr(gen, ptr_add(gen, 48), none)
    return value
