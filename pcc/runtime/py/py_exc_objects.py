"""Phase 4c.5: pcc-Python port of py_exc_objects.c.

Exception-object construction, accessor, and deallocation. Handles:
    py_exc_alloc, py_exc_new, py_exc_new_with_class,
    py_exc_set_cause, py_exc_set_context, py_exc_get_message,
    py_dealloc_exc

PyExceptionObject layout (from py_internal.h):
    offset  0   PyObjectHeader    (i64 refcount + i32 tag + i32 flags = 16 bytes)
    offset 16   exc_class         (ptr)
    offset 24   message           (ptr)
    offset 32   cause             (ptr)
    offset 40   context           (ptr)
    offset 48   traceback         (ptr)
    offset 56   n_frames          (i32)
    offset 60   cap_frames        (i32)
    total size: 64 bytes

Public object type tags come from the generated ``py_abi_constants`` module.
The private exception-table code used here is ``PY_EXC_EXCEPTION``.
"""

__pcc_runtime_port__ = True

from pcc.runtime.py.py_abi_constants import PY_TYPE_CLASS, PY_TYPE_EXC, PY_TYPE_INT
from pcc.extern import extern, c_abi_export, c_int32, c_ptr, c_int64, c_void
from pcc.unsafe import (
    define_global_i32,
    free,
    global_addr,
    global_load_ptr,
    is_tagged_int,
    load_i32,
    load_ptr,
    malloc,
    memset,
    null,
    ptr_add,
    ptr_is_null,
    stack_alloc,
    store_i32,
    store_i64,
    store_ptr,
    strlen,
)

py_incref            = extern("py_incref",            (c_ptr,),                    c_void)
py_decref            = extern("py_decref",            (c_ptr,),                    c_void)
py_str_new           = extern("py_str_new",           (c_ptr, c_int64),            c_ptr)
py_exc_builtin_class = extern("py_exc_builtin_class", (c_int64,),                  c_ptr)
pcc_gc_alloc         = extern("pcc_gc_alloc",         (c_int64, c_int32, c_int32), c_ptr)
pcc_gc_load_ptr      = extern("pcc_gc_load_ptr",      (c_ptr, c_ptr), c_ptr)
pcc_gc_publish_initialized = extern(
    "pcc_gc_publish_initialized", (c_ptr,), c_void
)
pcc_gc_store_ptr     = extern("pcc_gc_store_ptr",     (c_ptr, c_ptr, c_ptr), c_void)
pcc_gc_free_object_memory = extern("pcc_gc_free_object_memory", (c_ptr,), c_void)
pcc_gc_frame_enter = extern("pcc_gc_frame_enter", (c_ptr, c_ptr), c_void)
pcc_gc_frame_leave = extern("pcc_gc_frame_leave", (c_ptr,), c_void)
pcc_gc_store_root = extern("pcc_gc_store_root", (c_ptr, c_ptr), c_void)
pcc_gc_pin = extern("pcc_gc_pin", (c_ptr,), c_void)
pcc_gc_take_pinned_slot = extern("pcc_gc_take_pinned_slot", (c_ptr, c_int64), c_ptr)
pcc_py_gc_minor_graph_lock = extern("pcc_py_gc_minor_graph_lock", (), c_void)
pcc_py_gc_minor_graph_unlock = extern("pcc_py_gc_minor_graph_unlock", (), c_void)
define_global_i32("pcc_exc_construct_borrowed_frame_map", -2)
define_global_i32("pcc_exc_construct_owned_frame_map", 2)
pcc_diagnostics_runtime_log_event_code = extern(
    "pcc_diagnostics_runtime_log_event_code", (c_int32, c_int32, c_int64, c_int64, c_ptr), c_void,
)


def _type_of(obj) -> int:
    if is_tagged_int(obj):
        return PY_TYPE_INT       # PY_TYPE_INT
    return load_i32(obj, 8)


def _exc_store_constructed_slot(owned, values, value_offset: int, field_offset: int) -> None:
    # Fresh construction slots contain NULL/None, so this store has no user
    # destructor tail. Reload both operands under the normal graph lease;
    # a callee's implicit poll cannot invalidate the raw field address.
    pcc_py_gc_minor_graph_lock()
    e = pcc_gc_load_ptr(null(), owned)
    value = pcc_gc_load_ptr(null(), ptr_add(values, value_offset))
    pcc_gc_store_ptr(e, ptr_add(e, field_offset), value)
    pcc_py_gc_minor_graph_unlock()


def _exc_construct_body(borrowed, owned, msg) -> int:
    if ptr_is_null(pcc_gc_load_ptr(null(), borrowed)):
        cls = py_exc_builtin_class(1)
        store_ptr(borrowed, 0, cls)
        if ptr_is_null(cls):
            return 0
    e = pcc_gc_alloc(64, PY_TYPE_EXC, 0)   # sizeof(PyExceptionObject)
    store_ptr(owned, 0, e)
    if ptr_is_null(e):
        return 0
    # Header is initialized by pcc_gc_alloc; clear the payload tail.
    memset(ptr_add(e, 16), 0, 48)
    store_i64(e, 0, 1)
    store_i32(e, 8, PY_TYPE_EXC)
    _exc_store_constructed_slot(owned, borrowed, 0, 16)
    if not ptr_is_null(msg):
        n: int = strlen(msg)
        s = py_str_new(msg, n)
        store_ptr(owned, 8, s)
        if ptr_is_null(s):
            return 0
    else:
        store_ptr(owned, 8, global_load_ptr("py_None"))
    _exc_store_constructed_slot(owned, owned, 8, 24)
    return 1


def _exc_construct_finish(borrowed, owned, complete: int, log_code: int, log_tag: int):
    prior_pin: int = 0
    if complete != 0:
        pcc_diagnostics_runtime_log_event_code(6, 1, 12, 0, pcc_gc_load_ptr(null(), owned))
    if log_code == 2:
        pcc_diagnostics_runtime_log_event_code(6, 2, log_tag, 0, pcc_gc_load_ptr(null(), owned))
    elif log_code == 8:
        pcc_diagnostics_runtime_log_event_code(6, 8, log_tag, 0, pcc_gc_load_ptr(null(), owned))
    elif log_code == 9:
        pcc_diagnostics_runtime_log_event_code(6, 9, log_tag, 0, pcc_gc_load_ptr(null(), owned))
    if complete != 0:
        pcc_gc_publish_initialized(pcc_gc_load_ptr(null(), owned))
        # Construction remained movable. Only the final raw NEW handoff
        # spans root unregistration, whose graph unlock can itself park.
        result = pcc_gc_load_ptr(null(), owned)
        prior_pin = load_i32(result, 12) & 64
        pcc_gc_pin(result)
    pcc_gc_store_root(ptr_add(owned, 8), null())
    if complete == 0:
        pcc_gc_store_root(owned, null())
    pcc_gc_frame_leave(owned)
    pcc_gc_frame_leave(borrowed)
    if complete == 0:
        return null()
    return pcc_gc_take_pinned_slot(owned, prior_pin)


@c_abi_export("py_exc_alloc")
def py_exc_alloc(cls, msg):
    borrowed = stack_alloc(16)
    store_ptr(borrowed, 0, cls)
    store_ptr(borrowed, 8, null())
    pcc_gc_frame_enter(global_addr("pcc_exc_construct_borrowed_frame_map"), borrowed)
    owned = stack_alloc(16)
    memset(owned, 0, 16)
    pcc_gc_frame_enter(global_addr("pcc_exc_construct_owned_frame_map"), owned)
    complete: int = _exc_construct_body(borrowed, owned, msg)
    return _exc_construct_finish(borrowed, owned, complete, 0, 0)


def _default_exc_alloc(msg):
    # Direct PY_EXC_EXCEPTION default-class lookup. (Previously we
    # avoided calling py_exc_new here because of an int32/int64
    # signature mismatch; now resolved.)
    return py_exc_alloc(null(), msg)


@c_abi_export("py_exc_new")
def py_exc_new(type_tag: int, msg):
    borrowed = stack_alloc(16)
    memset(borrowed, 0, 16)
    pcc_gc_frame_enter(global_addr("pcc_exc_construct_borrowed_frame_map"), borrowed)
    owned = stack_alloc(16)
    memset(owned, 0, 16)
    pcc_gc_frame_enter(global_addr("pcc_exc_construct_owned_frame_map"), owned)
    cls = py_exc_builtin_class(type_tag)
    store_ptr(borrowed, 0, cls)
    complete: int = 0
    if ptr_is_null(cls) == 0:
        complete = _exc_construct_body(borrowed, owned, msg)
    return _exc_construct_finish(borrowed, owned, complete, 2, type_tag)


@c_abi_export("py_exc_new_with_value")
def py_exc_new_with_value(type_tag: int, value):
    borrowed = stack_alloc(16)
    store_ptr(borrowed, 0, null())
    store_ptr(borrowed, 8, value)
    pcc_gc_frame_enter(global_addr("pcc_exc_construct_borrowed_frame_map"), borrowed)
    owned = stack_alloc(16)
    memset(owned, 0, 16)
    pcc_gc_frame_enter(global_addr("pcc_exc_construct_owned_frame_map"), owned)
    cls = py_exc_builtin_class(type_tag)
    store_ptr(borrowed, 0, cls)
    complete: int = 0
    if ptr_is_null(cls) == 0:
        complete = _exc_construct_body(borrowed, owned, null())
    if complete != 0:
        if ptr_is_null(pcc_gc_load_ptr(null(), ptr_add(borrowed, 8))):
            store_ptr(borrowed, 8, global_load_ptr("py_None"))
        _exc_store_constructed_slot(owned, borrowed, 8, 24)
    return _exc_construct_finish(borrowed, owned, complete, 8, type_tag)


@c_abi_export("py_exc_new_with_class")
def py_exc_new_with_class(cls, msg):
    borrowed = stack_alloc(16)
    store_ptr(borrowed, 0, cls)
    store_ptr(borrowed, 8, null())
    pcc_gc_frame_enter(global_addr("pcc_exc_construct_borrowed_frame_map"), borrowed)
    owned = stack_alloc(16)
    memset(owned, 0, 16)
    pcc_gc_frame_enter(global_addr("pcc_exc_construct_owned_frame_map"), owned)
    current = pcc_gc_load_ptr(null(), borrowed)
    tag: int = 0
    if ptr_is_null(current) == 0 and is_tagged_int(current) == 0:
        tag = load_i32(current, 8)
    log_code: int = 9
    if tag != PY_TYPE_CLASS:
        store_ptr(borrowed, 0, null())
        log_code = 0
    complete: int = _exc_construct_body(borrowed, owned, msg)
    return _exc_construct_finish(borrowed, owned, complete, log_code, tag)


@c_abi_export("py_exc_set_cause")
def py_exc_set_cause(exc, cause) -> None:
    if ptr_is_null(exc):
        return
    if _type_of(exc) != PY_TYPE_EXC:
        return
    pcc_gc_store_ptr(exc, ptr_add(exc, 32), cause)      # ->cause
    pcc_diagnostics_runtime_log_event_code(6, 5, 0 if ptr_is_null(cause) else 1, 0, exc)


@c_abi_export("py_exc_set_context")
def py_exc_set_context(exc, context) -> None:
    if ptr_is_null(exc):
        return
    if _type_of(exc) != PY_TYPE_EXC:
        return
    pcc_gc_store_ptr(exc, ptr_add(exc, 40), context)      # ->context
    pcc_diagnostics_runtime_log_event_code(6, 6, 0 if ptr_is_null(context) else 1, 0, exc)


@c_abi_export("py_exc_get_message")
def py_exc_get_message(exc):
    if ptr_is_null(exc):
        return null()
    if _type_of(exc) != PY_TYPE_EXC:
        return null()
    return pcc_gc_load_ptr(exc, ptr_add(exc, 24))     # ->message (borrowed)


@c_abi_export("py_exc_get_cause")
def py_exc_get_cause(exc):
    if ptr_is_null(exc):
        return null()
    if _type_of(exc) != PY_TYPE_EXC:
        return null()
    cause = pcc_gc_load_ptr(exc, ptr_add(exc, 32))
    if ptr_is_null(cause):
        cause = global_load_ptr("py_None")
    py_incref(cause)
    return cause


@c_abi_export("py_exc_get_context")
def py_exc_get_context(exc):
    if ptr_is_null(exc):
        return null()
    if _type_of(exc) != PY_TYPE_EXC:
        return null()
    context = pcc_gc_load_ptr(exc, ptr_add(exc, 40))
    if ptr_is_null(context):
        context = global_load_ptr("py_None")
    py_incref(context)
    return context


@c_abi_export("py_exc_traceback_len")
def py_exc_traceback_len(exc) -> int:
    if ptr_is_null(exc):
        return 0
    if _type_of(exc) != PY_TYPE_EXC:
        return 0
    return load_i32(exc, 56)


@c_abi_export("py_dealloc_exc")
def py_dealloc_exc(o) -> None:
    pcc_diagnostics_runtime_log_event_code(6, 7, 12, 0, o)
    # ->exc_class
    cls = pcc_gc_load_ptr(o, ptr_add(o, 16))
    if not ptr_is_null(cls):
        py_decref(cls)
    # ->message
    msg = pcc_gc_load_ptr(o, ptr_add(o, 24))
    if not ptr_is_null(msg):
        py_decref(msg)
    # ->cause
    cause = pcc_gc_load_ptr(o, ptr_add(o, 32))
    if not ptr_is_null(cause):
        py_decref(cause)
    # ->context
    ctx = pcc_gc_load_ptr(o, ptr_add(o, 40))
    if not ptr_is_null(ctx):
        py_decref(ctx)
    # ->traceback (malloc'd array, free not decref)
    tb = load_ptr(o, 48)
    if not ptr_is_null(tb):
        free(tb)
    pcc_gc_free_object_memory(o)
