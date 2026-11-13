"""pcc-Python port of weakref.ref runtime support.

A weakref object is 56 bytes: header, target @16 (a weak edge), callback @24
(a strong edge), prev @32 / next @40 links and the referent's cached hash @48
(-1 until first hashed, as CPython's ``wr_hash``).

Live weakrefs sit on one of two lists. ``py_weakref_head`` holds them
ordinarily. A collection's weak window (its early pass to its late clear)
holds ``py_weakref_late_head``: the refs without callbacks whose target the
early pass found unreachable, and every ref created inside the window. As in
CPython 3.15 (Python/gc.c handle_weakrefs, then the post-finalizer clear),
a ref with a callback is cleared before finalizers and a ref without one
keeps its target through them. The late clear visits only the late list, so
the window adds no pass over every weakref.
"""

__pcc_runtime_port__ = True

from pcc.runtime.py.py_abi_constants import (
    PYINSTANCEOBJECT_CLS_OFFSET,
    PYOBJECTHEADER_TYPE_TAG_OFFSET,
    PY_TYPE_BOOL,
    PY_TYPE_BYTEARRAY,
    PY_TYPE_BYTES,
    PY_TYPE_COMPLEX,
    PY_TYPE_DICT,
    PY_TYPE_FLOAT,
    PY_TYPE_INSTANCE,
    PY_TYPE_INT,
    PY_TYPE_LIST,
    PY_TYPE_NONE,
    PY_TYPE_STR,
    PY_TYPE_TUPLE,
    PY_TYPE_USER_CLASS_START,
    PY_TYPE_VALUEBOX,
    PY_TYPE_WEAKREF,
)

from pcc.extern import extern, c_abi_export, c_ptr, c_int32, c_int64, c_void
from pcc.unsafe import (
    calloc,
    cstr,
    define_global_i32,
    free,
    global_addr,
    global_load_ptr,
    global_store_ptr,
    is_tagged_int,
    load_i32,
    load_i64,
    load_i8,
    load_ptr,
    malloc,
    null,
    ptr_add,
    ptr_eq,
    ptr_is_null,
    stack_alloc,
    store_i32,
    store_i64,
    store_i8,
    store_ptr,
    strlen,
)


py_incref = extern("py_incref", (c_ptr,), c_void)
py_decref = extern("py_decref", (c_ptr,), c_void)
py_gc_track = extern("py_gc_track", (c_ptr,), c_void)
pcc_gc_backend = extern("pcc_gc_backend", (), c_int64)
pcc_thread_owns_stopped_world = extern("pcc_thread_owns_stopped_world", (), c_int64)
pcc_gc_backend0_is_weak_candidate = extern(
    "pcc_gc_backend0_is_weak_candidate", (c_ptr,), c_int64
)
py_dict_del = extern("py_dict_del", (c_ptr, c_ptr), c_int64)
py_dict_get = extern("py_dict_get", (c_ptr, c_ptr), c_ptr)
py_dict_keys = extern("py_dict_keys", (c_ptr,), c_ptr)
py_dict_new = extern("py_dict_new", (), c_ptr)
py_dict_set = extern("py_dict_set", (c_ptr, c_ptr, c_ptr), c_void)
py_list_append = extern("py_list_append", (c_ptr, c_ptr), c_void)
py_list_get = extern("py_list_get", (c_ptr, c_int64), c_ptr)
py_list_len = extern("py_list_len", (c_ptr,), c_int64)
py_list_new = extern("py_list_new", (c_int64,), c_ptr)
py_list_pop = extern("py_list_pop", (c_ptr, c_int64), c_ptr)
py_list_set = extern("py_list_set", (c_ptr, c_int64, c_ptr), c_void)
py_tuple_new = extern("py_tuple_new", (c_int64,), c_ptr)
py_tuple_get = extern("py_tuple_get", (c_ptr, c_int64), c_ptr)
py_tuple_set_item = extern("py_tuple_set_item", (c_ptr, c_int64, c_ptr), c_void)
py_obj_call = extern("py_obj_call", (c_ptr, c_ptr, c_ptr), c_ptr)
py_clear_exception = extern("py_clear_exception", (), c_void)
py_current_exception = extern("py_current_exception", (), c_ptr)
py_tls_exc_swap_slot = extern("py_tls_exc_swap_slot", (c_ptr,), c_void)
pcc_gc_stack_slot_root_is_conservative = extern(
    "pcc_gc_stack_slot_root_is_conservative", (c_ptr,), c_int64
)
pcc_gc_note_frame_enter_local_checked = extern(
    "pcc_gc_note_frame_enter_local_checked", (c_ptr, c_ptr), c_int64
)
pcc_gc_note_frame_leave_local = extern(
    "pcc_gc_note_frame_leave_local", (c_ptr,), c_void
)
pcc_thread_no_park_enter = extern("pcc_thread_no_park_enter", (), c_void)
pcc_thread_no_park_exit = extern("pcc_thread_no_park_exit", (), c_void)
py_exc_new = extern("py_exc_new", (c_int64, c_ptr), c_ptr)
py_raise = extern("py_raise", (c_ptr,), c_void)
# py_raise increfs; a caller that created the exception must release it.
py_raise_owned = extern("py_raise_owned", (c_ptr,), c_void)
py_err_occurred = extern("py_err_occurred", (), c_int64)
py_obj_hash = extern("py_obj_hash", (c_ptr,), c_int64)
py_obj_eq_value = extern("py_obj_eq_value", (c_ptr, c_ptr), c_int64)
py_obj_type_name = extern("py_obj_type_name", (c_ptr,), c_ptr)
py_str_byte_len = extern("py_str_byte_len", (c_ptr,), c_int64)
py_str_utf8 = extern("py_str_utf8", (c_ptr,), c_ptr)
pcc_diagnostics_runtime_log_event_code = extern("pcc_diagnostics_runtime_log_event_code", (c_int32, c_int32, c_int64, c_int64, c_ptr), c_void)
pcc_gc_alloc = extern("pcc_gc_alloc", (c_int64, c_int32, c_int32), c_ptr)
pcc_gc_publish_initialized = extern(
    "pcc_gc_publish_initialized", (c_ptr,), c_void
)
pcc_gc_pin = extern("pcc_gc_pin", (c_ptr,), c_void)
pcc_gc_note_relocation_read = extern(
    "pcc_gc_note_relocation_read", (c_ptr,), c_ptr,
)
pcc_gc_load_ptr = extern("pcc_gc_load_ptr", (c_ptr, c_ptr), c_ptr)
pcc_gc_store_ptr = extern("pcc_gc_store_ptr", (c_ptr, c_ptr, c_ptr), c_void)
pcc_gc_free_object_memory = extern("pcc_gc_free_object_memory", (c_ptr,), c_void)
pcc_py_gc_minor_graph_lock = extern("pcc_py_gc_minor_graph_lock", (), c_void)
pcc_py_gc_minor_graph_unlock = extern("pcc_py_gc_minor_graph_unlock", (), c_void)
pcc_capi_is_cext_type_tag = extern("pcc_capi_is_cext_type_tag", (c_int64,), c_int64)
pcc_gc_tracing_is_sweep_candidate = extern("pcc_gc_tracing_is_sweep_candidate", (c_ptr,), c_int64)
pcc_gc_foreign_lease_acquire = extern("pcc_gc_foreign_lease_acquire", (c_ptr,), c_int64)
pcc_gc_foreign_lease_release = extern("pcc_gc_foreign_lease_release", (c_ptr, c_int64), c_int64)
pcc_platform_abort = extern("pcc_platform_abort", (), c_void)

_PENDING_WEAK_NEXT: int = 0
_PENDING_WEAK_ROOT: int = 8
_PENDING_WEAK_LEASE: int = 16
_PENDING_WEAK_BYTES: int = 24

_WEAK_CALLBACK_EXCEPTION_ROOT: int = 0
_WEAK_CALLBACK_EXCEPTION_BYTES: int = 8
define_global_i32("pcc_weakref_callback_exception_map", 1)

# Weakref object layout (bytes).
_WEAKREF_TARGET = 16
_WEAKREF_CALLBACK = 24
_WEAKREF_PREV = 32
_WEAKREF_NEXT = 40
_WEAKREF_HASH = 48
_WEAKREF_BYTES = 56
# Open weak windows: early passes not yet matched by their late clear. A
# finalizer's nested collection opens its own window inside its caller's.
define_global_i32("pcc_weakref_late_windows", 0)


def _weakref_target_is_base_object(target) -> int:
    tag: int = load_i32(target, PYOBJECTHEADER_TYPE_TAG_OFFSET)
    if tag != PY_TYPE_INSTANCE and tag < PY_TYPE_USER_CLASS_START:
        return 0
    if tag == PY_TYPE_VALUEBOX or pcc_capi_is_cext_type_tag(tag) != 0:
        return 0
    # Compare exact classes in one graph transaction. Slots-only subclasses
    # and ordinary subclasses have their own weak-reference policy; this is
    # only the canonical builtin object's unconditional exclusion.
    pcc_py_gc_minor_graph_lock()
    current = pcc_gc_note_relocation_read(target)
    cls = pcc_gc_load_ptr(current, ptr_add(current, PYINSTANCEOBJECT_CLS_OFFSET))
    base = pcc_gc_load_ptr(null(), global_addr("py_object_root_cache"))
    result: int = 0
    if ptr_is_null(base) == 0:
        result = ptr_eq(cls, base)
    pcc_py_gc_minor_graph_unlock()
    return result


def _py_none():
    return global_load_ptr("py_None")


def _unlink(wr) -> None:
    prev = load_ptr(wr, 32)
    nxt = load_ptr(wr, 40)
    if ptr_eq(prev, wr) != 0:
        store_ptr(wr, 32, null())
        store_ptr(wr, 40, null())
        return
    if ptr_is_null(prev) != 0:
        if ptr_eq(global_load_ptr("py_weakref_head"), wr) != 0:
            global_store_ptr("py_weakref_head", nxt)
        elif ptr_eq(global_load_ptr("py_weakref_late_head"), wr) != 0:
            global_store_ptr("py_weakref_late_head", nxt)
        else:
            store_ptr(wr, 40, null())
            return
    else:
        if ptr_eq(load_ptr(prev, 40), wr) == 0:
            store_ptr(wr, 32, null())
            store_ptr(wr, 40, null())
            return
        store_ptr(prev, 40, nxt)
    if ptr_is_null(nxt) == 0:
        store_ptr(nxt, 32, prev)
    store_ptr(wr, 32, null())
    store_ptr(wr, 40, null())


def _link_main(wr) -> None:
    head = global_load_ptr("py_weakref_head")
    store_ptr(wr, _WEAKREF_PREV, null())
    store_ptr(wr, _WEAKREF_NEXT, head)
    if ptr_is_null(head) == 0:
        store_ptr(head, _WEAKREF_PREV, wr)
    global_store_ptr("py_weakref_head", wr)


def _link_late(wr) -> None:
    head = global_load_ptr("py_weakref_late_head")
    store_ptr(wr, _WEAKREF_PREV, null())
    store_ptr(wr, _WEAKREF_NEXT, head)
    if ptr_is_null(head) == 0:
        store_ptr(head, _WEAKREF_PREV, wr)
    global_store_ptr("py_weakref_late_head", wr)


def _message_append(message, length: int, text, count: int) -> int:
    index: int = 0
    while index < count:
        store_i8(message, length, load_i8(text, index))
        length = length + 1
        index = index + 1
    return length


def _weakref_refuse(target) -> None:
    """TypeError: cannot create weak reference to '<type>' object."""
    message = stack_alloc(160)
    prefix = cstr("cannot create weak reference to '")
    length: int = _message_append(message, 0, prefix, strlen(prefix))
    if is_tagged_int(target) != 0:
        name_int = cstr("int")
        length = _message_append(message, length, name_int, strlen(name_int))
    else:
        name = py_obj_type_name(target)
        if ptr_is_null(name) == 0:
            count: int = py_str_byte_len(name)
            if count > 100:
                count = 100
            length = _message_append(message, length, py_str_utf8(name), count)
            py_decref(name)
    suffix = cstr("' object")
    length = _message_append(message, length, suffix, strlen(suffix))
    store_i8(message, length, 0)
    py_raise_owned(py_exc_new(3, message))


def _weakref_tag_refused(tag: int) -> int:
    """CPython's builtin types without a weak-reference list."""
    if (tag == PY_TYPE_NONE or tag == PY_TYPE_BOOL or tag == PY_TYPE_INT
            or tag == PY_TYPE_FLOAT or tag == PY_TYPE_STR or tag == PY_TYPE_LIST
            or tag == PY_TYPE_DICT or tag == PY_TYPE_TUPLE or tag == PY_TYPE_COMPLEX
            or tag == PY_TYPE_BYTES or tag == PY_TYPE_BYTEARRAY):
        return 1
    return 0


@c_abi_export("py_weakref_new")
def py_weakref_new(target, callback):
    if ptr_is_null(target) != 0:
        return null()
    if is_tagged_int(target) != 0:
        _weakref_refuse(target)
        return null()
    if _weakref_tag_refused(load_i32(target, PYOBJECTHEADER_TYPE_TAG_OFFSET)) != 0:
        _weakref_refuse(target)
        return null()
    # Value projections are identity-free; a ValueBox's lifetime is
    # unpredictable (every boxing makes a new box) — mirror CPython's
    # analogue (weakref.ref(3) -> TypeError). 200 == PY_TYPE_VALUEBOX,
    # 3 == PY_EXC_TYPEERROR. The compile-time diagnostic catches the
    # static form; this covers Dyn-path boxes.
    if load_i32(target, 8) == PY_TYPE_VALUEBOX:
        py_raise_owned(
            py_exc_new(
                3,
                cstr("cannot create weak reference to a valueclass payload"),
            )
        )
        return null()
    if _weakref_target_is_base_object(target) != 0:
        py_raise_owned(py_exc_new(3, cstr("cannot create weak reference to 'object' object")))
        return null()
    if ptr_eq(callback, _py_none()) != 0:
        callback = null()
    target = pcc_gc_note_relocation_read(target)

    wr = pcc_gc_alloc(_WEAKREF_BYTES, PY_TYPE_WEAKREF, 0)
    if ptr_is_null(wr) != 0:
        return null()
    store_i64(wr, 0, 1)
    store_i32(wr, 8, PY_TYPE_WEAKREF)        # PY_TYPE_WEAKREF
    store_ptr(wr, 16, target)
    store_ptr(wr, 24, null())
    store_ptr(wr, 32, null())
    store_ptr(wr, 40, null())
    store_i64(wr, _WEAKREF_HASH, -1)
    if ptr_is_null(callback) == 0:
        pcc_gc_store_ptr(wr, ptr_add(wr, 24), callback)

    if pcc_gc_backend() == 0:
        # The weak target is not a strong edge, but the callback is. GC0
        # must see this owner to distinguish a live callback from a weakref
        # participating in the same unreachable cycle as its target.
        py_gc_track(wr)
        if (load_i32(wr, 12) & 2) == 0:
            # Tracking can allocate while GC0's table is materialized. The
            # object is still private: release its callback without ever
            # leaving an untracked node in the weak-reference chain.
            py_decref(wr)
            py_raise_owned(py_exc_new(19, cstr("weak reference tracking failed")))
            return null()
    if load_i32(global_addr("pcc_weakref_late_windows"), 0) != 0:
        # Created by a weak callback or finalizer: the open window's late
        # clear decides it with the refs that kept their targets.
        _link_late(wr)
    else:
        _link_main(wr)
    pcc_diagnostics_runtime_log_event_code(4, 1, load_i32(target, 8), 0 if ptr_is_null(callback) != 0 else 1, target)
    pcc_gc_publish_initialized(wr)
    return wr


@c_abi_export("py_weakref_call")
def py_weakref_call(ref):
    if ptr_is_null(ref) != 0:
        return null()
    if is_tagged_int(ref) != 0:
        return null()
    if load_i32(ref, 8) != PY_TYPE_WEAKREF:
        return null()
    target = load_ptr(ref, 16)
    if ptr_is_null(target) != 0:
        none_obj = _py_none()
        py_incref(none_obj)
        return none_obj
    resolved = pcc_gc_note_relocation_read(target)
    if ptr_eq(resolved, target) == 0:
        store_ptr(ref, 16, resolved)
        target = resolved
    py_incref(target)
    return target


@c_abi_export("py_weakref_hash")
def py_weakref_hash(ref) -> int:
    """CPython weakref_hash: the referent's hash, cached on first use."""
    cached: int = load_i64(ref, _WEAKREF_HASH)
    if cached != -1:
        return cached
    target = py_weakref_call(ref)
    if ptr_is_null(target) != 0:
        return -1
    if ptr_eq(target, _py_none()) != 0:
        py_decref(target)
        py_raise_owned(py_exc_new(3, cstr("weak object has gone away")))
        return -1
    hashed: int = py_obj_hash(target)
    py_decref(target)
    if py_err_occurred() != 0:
        return -1
    store_i64(ref, _WEAKREF_HASH, hashed)
    return hashed


@c_abi_export("py_weakref_eq")
def py_weakref_eq(a, b) -> int:
    """CPython weakref_richcompare for ==: live referents compare, else identity."""
    first = py_weakref_call(a)
    if ptr_is_null(first) != 0:
        return ptr_eq(a, b)
    second = py_weakref_call(b)
    if ptr_is_null(second) != 0:
        py_decref(first)
        return ptr_eq(a, b)
    none_obj = _py_none()
    result: int = 0
    if ptr_eq(first, none_obj) != 0 or ptr_eq(second, none_obj) != 0:
        result = ptr_eq(a, b)
    else:
        result = py_obj_eq_value(first, second)
    py_decref(first)
    py_decref(second)
    return result


@c_abi_export("py_weak_value_dict_new")
def py_weak_value_dict_new():
    return py_dict_new()


@c_abi_export("py_weak_value_dict_set")
def py_weak_value_dict_set(d, key, value) -> int:
    if ptr_is_null(d) != 0:
        return -1
    if ptr_is_null(key) != 0:
        return -1
    if ptr_is_null(value) != 0:
        return -1
    wr = py_weakref_new(value, _py_none())
    if ptr_is_null(wr) != 0:
        return -1
    py_dict_set(d, key, wr)
    py_decref(wr)
    return 0


@c_abi_export("py_weak_value_dict_contains")
def py_weak_value_dict_contains(d, key) -> int:
    if ptr_is_null(d) != 0:
        return 0
    if ptr_is_null(key) != 0:
        return 0
    wr = py_dict_get(d, key)
    if ptr_is_null(wr) != 0:
        return 0
    target = py_weakref_call(wr)
    py_decref(wr)
    if ptr_is_null(target) != 0:
        return 0
    if ptr_eq(target, _py_none()) != 0:
        py_decref(target)
        py_dict_del(d, key)
        return 0
    py_decref(target)
    return 1


@c_abi_export("py_weak_value_dict_len")
def py_weak_value_dict_len(d) -> int:
    if ptr_is_null(d) != 0:
        return 0
    keys = py_dict_keys(d)
    if ptr_is_null(keys) != 0:
        return 0
    n: int = py_list_len(keys)
    i: int = 0
    count: int = 0
    while i < n:
        key = py_list_get(keys, i)
        if ptr_is_null(key) == 0:
            if py_weak_value_dict_contains(d, key) != 0:
                count += 1
            py_decref(key)
        i += 1
    py_decref(keys)
    return count


@c_abi_export("py_weak_key_dict_new")
def py_weak_key_dict_new():
    return py_list_new(0)


def _weak_key_entry_new(key, value):
    wr = py_weakref_new(key, _py_none())
    if ptr_is_null(wr) != 0:
        return null()
    entry = py_tuple_new(2)
    if ptr_is_null(entry) != 0:
        py_decref(wr)
        return null()
    py_tuple_set_item(entry, 0, wr)
    py_tuple_set_item(entry, 1, value)
    py_decref(wr)
    return entry


@c_abi_export("py_weak_key_dict_set")
def py_weak_key_dict_set(d, key, value) -> int:
    if ptr_is_null(d) != 0:
        return -1
    if ptr_is_null(key) != 0:
        return -1
    if ptr_is_null(value) != 0:
        return -1
    n: int = py_list_len(d)
    i: int = 0
    while i < n:
        entry = py_list_get(d, i)
        if ptr_is_null(entry) == 0:
            wr = py_tuple_get(entry, 0)
            live = py_weakref_call(wr)
            same: int = 0
            if ptr_is_null(live) == 0:
                if ptr_eq(live, _py_none()) == 0:
                    if ptr_eq(live, key) != 0:
                        same = 1
                py_decref(live)
            if ptr_is_null(wr) == 0:
                py_decref(wr)
            if same != 0:
                replacement = _weak_key_entry_new(key, value)
                if ptr_is_null(replacement) != 0:
                    py_decref(entry)
                    return -1
                py_list_set(d, i, replacement)
                py_decref(replacement)
                py_decref(entry)
                return 0
            py_decref(entry)
        i += 1
    entry2 = _weak_key_entry_new(key, value)
    if ptr_is_null(entry2) != 0:
        return -1
    py_list_append(d, entry2)
    py_decref(entry2)
    return 0


@c_abi_export("py_weak_key_dict_len")
def py_weak_key_dict_len(d) -> int:
    if ptr_is_null(d) != 0:
        return 0
    count: int = 0
    i: int = 0
    while i < py_list_len(d):
        entry = py_list_get(d, i)
        if ptr_is_null(entry) != 0:
            i += 1
        else:
            wr = py_tuple_get(entry, 0)
            live = py_weakref_call(wr)
            is_live: int = 0
            if ptr_is_null(live) == 0:
                if ptr_eq(live, _py_none()) == 0:
                    is_live = 1
                py_decref(live)
            if ptr_is_null(wr) == 0:
                py_decref(wr)
            if is_live != 0:
                count += 1
                i += 1
            else:
                popped = py_list_pop(d, i)
                if ptr_is_null(popped) == 0:
                    py_decref(popped)
            py_decref(entry)
    return count


def _weakref_run_callback(wr) -> None:
    """Discard callback errors, preserving the caller's pending exception."""
    callback = pcc_gc_load_ptr(wr, ptr_add(wr, 24))
    if ptr_is_null(callback) == 0:
        saved = stack_alloc(_WEAK_CALLBACK_EXCEPTION_BYTES)
        store_ptr(saved, _WEAK_CALLBACK_EXCEPTION_ROOT, null())
        registered: int = 0
        saved_owner: int = 0
        if ptr_is_null(py_current_exception()) == 0:
            # TLS remains the only owner until the empty activation-private
            # slot is known to be traced. Keep no borrowed address across the
            # registration guard's exit, which permits relocation.
            if pcc_gc_stack_slot_root_is_conservative(saved) == 0:
                pcc_thread_no_park_enter()
                status: int = pcc_gc_note_frame_enter_local_checked(
                    global_addr("pcc_weakref_callback_exception_map"), saved
                )
                pcc_thread_no_park_exit()
                if status != 1:
                    pcc_platform_abort()
                    return
                registered = 1
            py_tls_exc_swap_slot(saved)
            saved_owner = 1
        args = py_tuple_new(1)
        if ptr_is_null(args) == 0:
            pcc_diagnostics_runtime_log_event_code(4, 3, 0, 0, wr)
            py_tuple_set_item(args, 0, wr)
            # The held weakref owns its callback. Reload after allocations or
            # checked registration may have relocated that owned reference.
            callback = pcc_gc_load_ptr(wr, ptr_add(wr, 24))
            # A weakref callback is an unraisable boundary: release its owned
            # result and arguments, then discard its error.
            result = py_obj_call(callback, args, _py_none())
            if ptr_is_null(result) == 0:
                py_decref(result)
            py_decref(args)
        py_clear_exception()
        if saved_owner != 0:
            py_tls_exc_swap_slot(saved)
        if registered != 0:
            pcc_gc_note_frame_leave_local(saved)


def _drop_callback_queue(pending, gc0: int) -> None:
    """Release every hold of an unrun queue; no callback runs."""
    while ptr_is_null(pending) == 0:
        entry = pending
        pending = load_ptr(entry, _PENDING_WEAK_NEXT)
        if gc0 != 0:
            py_decref(load_ptr(entry, _PENDING_WEAK_ROOT))
        else:
            pcc_gc_foreign_lease_release(
                ptr_add(entry, _PENDING_WEAK_ROOT), load_i64(entry, _PENDING_WEAK_LEASE))
        free(entry)


def _queue_callback_hold(wr, pending_cell, gc0: int) -> int:
    """Append a counted (GC0) or leased hold on ``wr`` to a callback queue.

    ``pending_cell`` holds the queue's head @0 and tail @8. Returns 0, or -1
    after aborting on allocation or lease failure. Every earlier hold is
    released first: a pass that prepares its holds before clearing any target
    then leaves neither a partial invalidation nor a retained owner.
    """
    entry = malloc(_PENDING_WEAK_BYTES)
    if ptr_is_null(entry) != 0:
        _drop_callback_queue(load_ptr(pending_cell, 0), gc0)
        store_ptr(pending_cell, 0, null())
        store_ptr(pending_cell, 8, null())
        pcc_platform_abort()
        return -1
    store_ptr(entry, _PENDING_WEAK_NEXT, null())
    store_ptr(entry, _PENDING_WEAK_ROOT, wr)
    store_i64(entry, _PENDING_WEAK_LEASE, 0)
    if gc0 != 0:
        py_incref(wr)
    else:
        token: int = pcc_gc_foreign_lease_acquire(ptr_add(entry, _PENDING_WEAK_ROOT))
        if token < 0:
            free(entry)
            _drop_callback_queue(load_ptr(pending_cell, 0), gc0)
            store_ptr(pending_cell, 0, null())
            store_ptr(pending_cell, 8, null())
            pcc_platform_abort()
            return -1
        store_i64(entry, _PENDING_WEAK_LEASE, token)
    tail = load_ptr(pending_cell, 8)
    if ptr_is_null(tail) != 0:
        store_ptr(pending_cell, 0, entry)
    else:
        store_ptr(tail, _PENDING_WEAK_NEXT, entry)
    store_ptr(pending_cell, 8, entry)
    return 0


def _run_callback_queue(pending, gc0: int) -> None:
    """Run each held callback once, then drop its hold."""
    while ptr_is_null(pending) == 0:
        entry = pending
        pending = load_ptr(entry, _PENDING_WEAK_NEXT)
        held = load_ptr(entry, _PENDING_WEAK_ROOT)
        _weakref_run_callback(held)
        if gc0 != 0:
            py_decref(held)
        elif pcc_gc_foreign_lease_release(
            ptr_add(entry, _PENDING_WEAK_ROOT), load_i64(entry, _PENDING_WEAK_LEASE)
        ) != 0:
            pcc_platform_abort()
            return
        free(entry)


def _open_late_window() -> None:
    windows: int = load_i32(global_addr("pcc_weakref_late_windows"), 0)
    store_i32(global_addr("pcc_weakref_late_windows"), 0, windows + 1)


def _close_late_window() -> None:
    windows: int = load_i32(global_addr("pcc_weakref_late_windows"), 0)
    if windows > 0:
        store_i32(global_addr("pcc_weakref_late_windows"), 0, windows - 1)


@c_abi_export("py_weakref_invalidate_gc0_candidates")
def py_weakref_invalidate_gc0_candidates() -> int:
    """GC0's early pass over its unreachable batch (CPython handle_weakrefs).

    The collector owns the stopped world and the materialized tracking table.
    A ref with a callback loses its target now; a live one (not itself in
    the batch) runs its callback after every such target is cleared. Every
    hold is prepared before any target is cleared, so allocation failure
    leaves no partial invalidation. A ref without a callback keeps its target
    through finalizers: it moves to the late list for
    py_weakref_clear_gc0_candidates. This opens a weak window, which that
    late clear closes.
    """
    if pcc_gc_backend() != 0:
        return 0
    if pcc_thread_owns_stopped_world() == 0:
        pcc_platform_abort()
        return -1
    _open_late_window()
    pending_cell = stack_alloc(16)
    store_ptr(pending_cell, 0, null())
    store_ptr(pending_cell, 8, null())
    count: int = 0
    wr = global_load_ptr("py_weakref_head")
    while ptr_is_null(wr) == 0:
        nxt = load_ptr(wr, _WEAKREF_NEXT)
        target = load_ptr(wr, _WEAKREF_TARGET)
        if (ptr_is_null(target) == 0 and is_tagged_int(target) == 0
                and pcc_gc_backend0_is_weak_candidate(target) != 0):
            if ptr_is_null(load_ptr(wr, _WEAKREF_CALLBACK)) != 0:
                _unlink(wr)
                _link_late(wr)
            elif (pcc_gc_backend0_is_weak_candidate(wr) == 0
                    and (load_i32(wr, 12) & 524288) == 0
                    and load_i64(wr, 0) > 0):
                if _queue_callback_hold(wr, pending_cell, 1) != 0:
                    return -1
                count += 1
        wr = nxt
    # Only refs with callbacks to batch targets remain to clear here.
    wr = global_load_ptr("py_weakref_head")
    while ptr_is_null(wr) == 0:
        target = load_ptr(wr, _WEAKREF_TARGET)
        if (ptr_is_null(target) == 0 and is_tagged_int(target) == 0
                and pcc_gc_backend0_is_weak_candidate(target) != 0):
            store_ptr(wr, _WEAKREF_TARGET, null())
            pcc_diagnostics_runtime_log_event_code(4, 2, 0, load_i32(target, 8), target)
        wr = load_ptr(wr, _WEAKREF_NEXT)
    _run_callback_queue(load_ptr(pending_cell, 0), 1)
    return count


@c_abi_export("py_weakref_clear_gc0_candidates")
def py_weakref_clear_gc0_candidates() -> int:
    """GC0's late clear: settle the late list and close the weak window.

    After finalizers the collector re-arms the still-dead part of its batch.
    A late-list ref to a re-armed target is cleared silently, as CPython 3.15
    clears refs to final-unreachable objects; every late ref then returns to
    the ordinary list. This visits the late list only.
    """
    if pcc_gc_backend() != 0:
        return 0
    if pcc_thread_owns_stopped_world() == 0:
        pcc_platform_abort()
        return -1
    count: int = 0
    wr = global_load_ptr("py_weakref_late_head")
    while ptr_is_null(wr) == 0:
        nxt = load_ptr(wr, _WEAKREF_NEXT)
        target = load_ptr(wr, _WEAKREF_TARGET)
        if (ptr_is_null(target) == 0 and is_tagged_int(target) == 0
                and pcc_gc_backend0_is_weak_candidate(target) != 0):
            store_ptr(wr, _WEAKREF_TARGET, null())
            pcc_diagnostics_runtime_log_event_code(4, 2, 0, load_i32(target, 8), target)
            count += 1
        _unlink(wr)
        _link_main(wr)
        wr = nxt
    _close_late_window()
    return count


def _tracing_target_dies(target) -> int:
    if (ptr_is_null(target) == 0 and is_tagged_int(target) == 0
            and pcc_gc_tracing_is_sweep_candidate(target) != 0
            and (load_i32(target, 12) & 16384) == 0
            and pcc_capi_is_cext_type_tag(load_i32(target, 8)) == 0):
        return 1
    return 0


@c_abi_export("py_weakref_invalidate_tracing_candidates")
def py_weakref_invalidate_tracing_candidates() -> int:
    """The tracing sweep's early pass (CPython handle_weakrefs).

    The tracing collector owns the stopped world. A ref with a callback to an
    unreachable target loses it now; one which is itself unreachable must
    not run its callback. Independent counted address leases retain queued
    refs across callback allocation, collection and mutation. A ref without
    a callback keeps its target through finalizers on the late list. This
    opens a weak window, which py_weakref_clear_tracing_candidates closes.
    """
    _open_late_window()
    pending_cell = stack_alloc(16)
    store_ptr(pending_cell, 0, null())
    store_ptr(pending_cell, 8, null())
    count: int = 0
    wr = global_load_ptr("py_weakref_head")
    while ptr_is_null(wr) == 0:
        nxt = load_ptr(wr, _WEAKREF_NEXT)
        target = pcc_gc_note_relocation_read(load_ptr(wr, _WEAKREF_TARGET))
        if _tracing_target_dies(target) != 0:
            if ptr_is_null(load_ptr(wr, _WEAKREF_CALLBACK)) != 0:
                _unlink(wr)
                _link_late(wr)
            else:
                store_ptr(wr, _WEAKREF_TARGET, null())
                pcc_diagnostics_runtime_log_event_code(4, 2, 0, load_i32(target, 8), target)
                if (load_i32(wr, 12) & (1024 | 524288)) == 0:
                    if _queue_callback_hold(wr, pending_cell, 0) != 0:
                        return -1
                    count = count + 1
        wr = nxt
    _run_callback_queue(load_ptr(pending_cell, 0), 0)
    return count


@c_abi_export("py_weakref_clear_tracing_candidates")
def py_weakref_clear_tracing_candidates() -> int:
    """The tracing sweep's late clear: settle the late list, close the window.

    Runs after finalizers and the resurrection recheck. A late-list ref whose
    target is still a sweep candidate is cleared silently; late callbacks are
    never delivered. Every late ref then returns to the ordinary list. This
    visits the late list only.
    """
    if pcc_gc_backend() == 0:
        return 0
    if pcc_thread_owns_stopped_world() == 0:
        pcc_platform_abort()
        return -1
    count: int = 0
    wr = global_load_ptr("py_weakref_late_head")
    while ptr_is_null(wr) == 0:
        nxt = load_ptr(wr, _WEAKREF_NEXT)
        target = pcc_gc_note_relocation_read(load_ptr(wr, _WEAKREF_TARGET))
        if _tracing_target_dies(target) != 0:
            store_ptr(wr, _WEAKREF_TARGET, null())
            pcc_diagnostics_runtime_log_event_code(4, 2, 0, load_i32(target, 8), target)
            count += 1
        _unlink(wr)
        _link_main(wr)
        wr = nxt
    _close_late_window()
    return count


def _gc0_hold_target_callbacks(wr, target, pending_cell) -> None:
    while ptr_is_null(wr) == 0:
        if (ptr_eq(load_ptr(wr, _WEAKREF_TARGET), target) != 0
                and (load_i32(wr, 12) & 524288) == 0
                and load_i64(wr, 0) > 0
                and ptr_is_null(load_ptr(wr, _WEAKREF_CALLBACK)) == 0):
            if _queue_callback_hold(wr, pending_cell, 1) != 0:
                return
        wr = load_ptr(wr, _WEAKREF_NEXT)


def _gc0_clear_target(wr, target) -> None:
    while ptr_is_null(wr) == 0:
        if ptr_eq(load_ptr(wr, _WEAKREF_TARGET), target) != 0:
            store_ptr(wr, _WEAKREF_TARGET, null())
            pcc_diagnostics_runtime_log_event_code(4, 2, 0, load_i32(target, 8), target)
        wr = load_ptr(wr, _WEAKREF_NEXT)


def _weakref_invalidate_gc0_target(target) -> None:
    """A terminal GC0 target clears all its refs before any callback.

    Callbacks may drop other weakrefs or replace the global chain. Prepare
    counted holds before clearing targets, then use only that fixed queue
    once user code can run. GC0 never moves these owned weakrefs; the malloc
    slots are not offered as roots to the count-free collectors. A death
    inside an open weak window also finds refs on the late list.
    """
    pending_cell = stack_alloc(16)
    store_ptr(pending_cell, 0, null())
    store_ptr(pending_cell, 8, null())
    _gc0_hold_target_callbacks(global_load_ptr("py_weakref_head"), target, pending_cell)
    _gc0_hold_target_callbacks(global_load_ptr("py_weakref_late_head"), target, pending_cell)
    _gc0_clear_target(global_load_ptr("py_weakref_head"), target)
    _gc0_clear_target(global_load_ptr("py_weakref_late_head"), target)
    _run_callback_queue(load_ptr(pending_cell, 0), 1)


def _invalidate_chain(wr, target) -> None:
    while ptr_is_null(wr) == 0:
        nxt = load_ptr(wr, _WEAKREF_NEXT)
        wr_target = load_ptr(wr, _WEAKREF_TARGET)
        resolved = pcc_gc_note_relocation_read(wr_target)
        if ptr_eq(wr_target, target) != 0 or ptr_eq(resolved, target) != 0:
            store_ptr(wr, _WEAKREF_TARGET, null())
            pcc_diagnostics_runtime_log_event_code(4, 2, 0, load_i32(target, 8), target)
            # Weakref callbacks are an unraisable boundary; the helper also
            # releases the ignored owned result before clearing its exception.
            _weakref_run_callback(wr)
        elif ptr_eq(resolved, wr_target) == 0:
            store_ptr(wr, _WEAKREF_TARGET, resolved)
        wr = nxt


@c_abi_export("py_weakref_invalidate")
def py_weakref_invalidate(target) -> None:
    if ptr_is_null(target) != 0:
        return
    if is_tagged_int(target) != 0:
        return
    if pcc_gc_backend() == 0:
        _weakref_invalidate_gc0_target(target)
        return
    _invalidate_chain(global_load_ptr("py_weakref_head"), target)
    _invalidate_chain(global_load_ptr("py_weakref_late_head"), target)


@c_abi_export("py_dealloc_weakref")
def py_dealloc_weakref(ref) -> None:
    if ptr_is_null(ref) != 0:
        return
    _unlink(ref)
    pcc_diagnostics_runtime_log_event_code(4, 4, 0, 0, ref)
    callback = pcc_gc_load_ptr(ref, ptr_add(ref, 24))
    if ptr_is_null(callback) == 0:
        py_decref(callback)
    pcc_gc_free_object_memory(ref)
