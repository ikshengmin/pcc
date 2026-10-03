"""User data-model protocol dispatch authored in pcc-Python.

This owns the production ABI mirrored by ``src/py_protocol.c``: user dunder
dispatch, generic floor/in-place operators, and inherited dict-subclass
storage/methods.  The C source remains a host-C oracle.
"""

__pcc_runtime_port__ = True

from pcc.runtime.py.py_abi_constants import (
    C_POINTER_SIZE,
    PYCLASSOBJECT_FIELD_NAMES_OFFSET,
    PYTUPLEOBJECT_ITEMS_OFFSET,
    PY_TYPE_DICT,
    PYCLASSOBJECT_N_FIELDS_OFFSET,
    PYINSTANCEOBJECT_CLS_OFFSET,
    PYINSTANCEOBJECT_FIELDS_OFFSET,
    PYOBJECTHEADER_FLAGS_OFFSET,
    PY_TYPE_BOOL,
    PY_TYPE_COMPLEX,
    PY_TYPE_FLOAT,
    PY_TYPE_FUNC,
    PY_TYPE_INSTANCE,
    PY_TYPE_INT,
    PY_TYPE_NONE,
    PY_TYPE_TUPLE,
    PY_TYPE_USER_CLASS_START,
)

from pcc.extern import (
    c_abi_export,
    c_double,
    c_int32,
    c_int64,
    c_ptr,
    c_void,
    extern,
)
from pcc.unsafe import (
    atomic_rmw_i32,
    call_ptr1,
    call_ptr2,
    call_ptr3,
    cstr,
    f64_div,
    f64_signbit,
    define_global_i32,
    define_thread_local_i32,
    global_addr,
    global_load_ptr,
    is_tagged_int,
    load_i8,
    load_i32,
    load_i64,
    load_ptr,
    memset,
    null,
    ptr_add,
    ptr_eq,
    ptr_is_null,
    ptr_to_int,
    stack_alloc,
    store_i8,
    store_i32,
    store_i64,
    store_ptr,
)


py_class_lookup = extern("py_class_lookup", (c_ptr, c_ptr), c_ptr)
py_tuple_new = extern("py_tuple_new", (c_int64,), c_ptr)
py_tuple_len = extern("py_tuple_len", (c_ptr,), c_int64)
py_tuple_get = extern("py_tuple_get", (c_ptr, c_int64), c_ptr)
py_tuple_set_item = extern("py_tuple_set_item", (c_ptr, c_int64, c_ptr), c_void)
py_func_call = extern("py_func_call", (c_ptr, c_ptr), c_ptr)
py_func_new_named = extern("py_func_new_named", (c_ptr, c_ptr, c_ptr), c_ptr)
py_incref = extern("py_incref", (c_ptr,), c_void)
py_decref = extern("py_decref", (c_ptr,), c_void)
py_raise = extern("py_raise", (c_ptr,), c_void)
py_raise_owned = extern("py_raise_owned", (c_ptr,), c_void)
py_exc_new = extern("py_exc_new", (c_int64, c_ptr), c_ptr)
py_exc_new_with_value = extern("py_exc_new_with_value", (c_int64, c_ptr), c_ptr)
py_runtime_error_if_unset = extern(
    "py_runtime_error_if_unset", (c_ptr, c_ptr), c_ptr
)
py_err_occurred = extern("py_err_occurred", (), c_int64)
py_clear_exception = extern("py_clear_exception", (), c_void)
py_tls_exc_swap_slot = extern("py_tls_exc_swap_slot", (c_ptr,), c_void)
pcc_gc_frame_enter = extern("pcc_gc_frame_enter", (c_ptr, c_ptr), c_void)
pcc_gc_frame_leave = extern("pcc_gc_frame_leave", (c_ptr,), c_void)
pcc_gc_foreign_lease_acquire = extern("pcc_gc_foreign_lease_acquire", (c_ptr,), c_int64)
pcc_gc_foreign_lease_release = extern("pcc_gc_foreign_lease_release", (c_ptr, c_int64), c_int64)
pcc_gc_root_copy_borrowed_lease = extern("pcc_gc_root_copy_borrowed_lease", (c_ptr, c_ptr), c_int64)
pcc_gc_note_slot_write_barrier = extern("pcc_gc_note_slot_write_barrier", (c_ptr, c_ptr, c_ptr), c_void)
pcc_py_gc_minor_graph_lock = extern("pcc_py_gc_minor_graph_lock", (), c_void)
pcc_py_gc_minor_graph_unlock = extern("pcc_py_gc_minor_graph_unlock", (), c_void)
pcc_platform_abort = extern("pcc_platform_abort", (), c_void)
py_obj_truthy = extern("py_obj_truthy", (c_ptr,), c_int64)
py_int_to_i64 = extern("py_int_to_i64", (c_ptr, c_ptr), c_int64)
py_int_value_i64 = extern("py_int_value_i64", (c_ptr,), c_int64)
py_int_cmp = extern("py_int_cmp", (c_ptr, c_ptr), c_int32)
py_int_from_i64 = extern("py_int_from_i64", (c_int64,), c_ptr)
py_int_floordiv = extern("py_int_floordiv", (c_ptr, c_ptr), c_ptr)
py_int_mod = extern("py_int_mod", (c_ptr, c_ptr), c_ptr)
py_int_neg = extern("py_int_neg", (c_ptr,), c_ptr)
py_int_xor = extern("py_int_xor", (c_ptr, c_ptr), c_ptr)
py_complex_neg = extern("py_complex_neg", (c_ptr,), c_ptr)
py_obj_type_name = extern("py_obj_type_name", (c_ptr,), c_ptr)
py_str_utf8 = extern("py_str_utf8", (c_ptr,), c_ptr)
py_str_byte_len = extern("py_str_byte_len", (c_ptr,), c_int64)
py_float_to_f64 = extern("py_float_to_f64", (c_ptr,), c_double)
py_float_from_f64 = extern("py_float_from_f64", (c_double,), c_ptr)
floor_c = extern("floor", (c_double,), c_double)
fmod_c = extern("fmod", (c_double, c_double), c_double)
pcc_numeric_float_from_bits = extern("pcc_numeric_float_from_bits", (c_int64,), c_double)
pcc_gc_pin = extern("pcc_gc_pin", (c_ptr,), c_void)
pcc_gc_unpin = extern("pcc_gc_unpin", (c_ptr,), c_void)
pcc_gc_take_pinned_slot = extern("pcc_gc_take_pinned_slot", (c_ptr, c_int64), c_ptr)
pcc_gc_store_root = extern("pcc_gc_store_root", (c_ptr, c_ptr), c_void)
pcc_gc_scheduler_root_register_handle = extern("pcc_gc_scheduler_root_register_handle", (c_ptr,), c_ptr)
pcc_gc_scheduler_root_unregister_handle = extern("pcc_gc_scheduler_root_unregister_handle", (c_ptr,), c_void)
pcc_capi_cext_binary_number = extern("pcc_capi_cext_binary_number", (c_ptr, c_ptr, c_int64), c_ptr)
py_obj_add = extern("py_obj_add", (c_ptr, c_ptr), c_ptr)
py_obj_sub = extern("py_obj_sub", (c_ptr, c_ptr), c_ptr)
py_obj_mul = extern("py_obj_mul", (c_ptr, c_ptr), c_ptr)
py_obj_truediv = extern("py_obj_truediv", (c_ptr, c_ptr), c_ptr)
py_obj_mod = extern("py_obj_mod", (c_ptr, c_ptr), c_ptr)
py_obj_issubclass = extern("py_obj_issubclass", (c_ptr, c_ptr), c_int64)
py_dict_new = extern("py_dict_new", (), c_ptr)
py_dict_len = extern("py_dict_len", (c_ptr,), c_int64)
py_dict_contains = extern("py_dict_contains", (c_ptr, c_ptr), c_int64)
py_dict_get = extern("py_dict_get", (c_ptr, c_ptr), c_ptr)
py_dict_get_default = extern("py_dict_get_default", (c_ptr, c_ptr, c_ptr), c_ptr)
py_dict_set = extern("py_dict_set", (c_ptr, c_ptr, c_ptr), c_void)
py_dict_del = extern("py_dict_del", (c_ptr, c_ptr), c_int64)
py_dict_keys = extern("py_dict_keys", (c_ptr,), c_ptr)
py_dict_values = extern("py_dict_values", (c_ptr,), c_ptr)
py_dict_items = extern("py_dict_items", (c_ptr,), c_ptr)
py_str_new = extern("py_str_new", (c_ptr, c_int64), c_ptr)
pcc_gc_load_ptr = extern("pcc_gc_load_ptr", (c_ptr, c_ptr), c_ptr)
pcc_gc_store_ptr = extern("pcc_gc_store_ptr", (c_ptr, c_ptr, c_ptr), c_void)
pcc_capi_is_cext_type_tag = extern("pcc_capi_is_cext_type_tag", (c_int64,), c_int64)
PyNumber_Index = extern("PyNumber_Index", (c_ptr,), c_ptr)
pcc_capi_cext_inplace_number = extern(
    "pcc_capi_cext_inplace_number", (c_ptr, c_ptr, c_int64), c_ptr
)


define_thread_local_i32("pcc_py_protocol_eq_depth", 0)


def _type_of(obj) -> int:
    if is_tagged_int(obj) != 0:
        return PY_TYPE_INT
    return load_i32(obj, 8)


pcc_gc_pointer_is_managed = extern(
    "pcc_gc_pointer_is_managed", (c_ptr,), c_int64
)


def _ptr_can_have_header(obj) -> int:
    return pcc_gc_pointer_is_managed(obj)


def _is_user_instance(obj) -> int:
    if ptr_is_null(obj) != 0 or is_tagged_int(obj) != 0:
        return 0
    tag: int = _type_of(obj)
    # Dynamic C-extension tags occupy the same high numeric range as pcc user
    # classes but do not have the PyInstanceObject ``cls`` field consumed by
    # _lookup_dunder.
    if pcc_capi_is_cext_type_tag(tag) != 0:
        return 0
    if tag == PY_TYPE_INSTANCE or tag >= PY_TYPE_USER_CLASS_START:
        return 1
    return 0


def _instance_class(obj):
    if _is_user_instance(obj) == 0:
        return null()
    return pcc_gc_load_ptr(obj, ptr_add(obj, PYINSTANCEOBJECT_CLS_OFFSET))


def _lookup_dunder(obj, name):
    cls = _instance_class(obj)
    if ptr_is_null(cls) != 0:
        return null()
    return py_class_lookup(cls, name)


def _protocol_require_result(result, helper_name, message):
    if ptr_is_null(result) != 0:
        py_runtime_error_if_unset(helper_name, message)
    return result


# Match the managed binary callback transaction below. py_func_call borrows
# callable/args and returns one NEW result owner, including aliases. Publish
# that result before tuple/input disposal can run finalizers or move objects.
# Raw C method addresses keep the separate call_ptr1 route.
_PROTOCOL_UNARY_METHOD = 0
_PROTOCOL_UNARY_SELF = 1
_PROTOCOL_UNARY_ARGS = 2
_PROTOCOL_UNARY_RESULT = 3
_PROTOCOL_UNARY_ERROR = 4
_PROTOCOL_UNARY_SLOT_COUNT = 5
_PROTOCOL_UNARY_BORROWED_COUNT = 2

define_global_i32("pcc_protocol_unary_borrowed_map", -2)
define_global_i32("pcc_protocol_unary_owned_map", 5)


def _protocol_unary_adopt(slots: c_ptr, tokens: c_ptr, index: int) -> int:
    # Every NEW producer has already stored into its registered owning slot.
    offset: int = index * C_POINTER_SIZE
    slot = ptr_add(slots, offset)
    token: int = pcc_gc_foreign_lease_acquire(slot)
    if token < 0:
        py_runtime_error_if_unset(cstr("user protocol call"), cstr("unary result owner lease failed"))
        return -1
    store_i64(tokens, offset, token)
    pcc_py_gc_minor_graph_lock()
    pcc_gc_note_slot_write_barrier(null(), slot, load_ptr(slot, 0))
    pcc_py_gc_minor_graph_unlock()
    return 0


def _protocol_unary_drop(slots: c_ptr, tokens: c_ptr, index: int) -> None:
    offset: int = index * C_POINTER_SIZE
    slot = ptr_add(slots, offset)
    if pcc_gc_foreign_lease_release(slot, load_i64(tokens, offset)) != 0:
        pcc_platform_abort()
        return
    store_i64(tokens, offset, 0)
    pcc_gc_store_root(slot, null())


def _protocol_unary_body(slots: c_ptr, tokens: c_ptr, borrowed: c_ptr) -> int:
    index: int = 0
    while index < _PROTOCOL_UNARY_BORROWED_COUNT:
        offset: int = index * C_POINTER_SIZE
        token: int = pcc_gc_root_copy_borrowed_lease(
            ptr_add(slots, offset), ptr_add(borrowed, offset),
        )
        if token < 0:
            py_runtime_error_if_unset(cstr("user protocol call"), cstr("unary input owner copy failed"))
            return -1
        store_i64(tokens, offset, token)
        index += 1
    store_ptr(slots, _PROTOCOL_UNARY_ARGS * C_POINTER_SIZE, py_tuple_new(1))
    if _protocol_unary_adopt(slots, tokens, _PROTOCOL_UNARY_ARGS) != 0:
        return -1
    if ptr_is_null(load_ptr(slots, _PROTOCOL_UNARY_ARGS * C_POINTER_SIZE)) != 0:
        _protocol_require_result(null(), cstr("py_tuple_new"), cstr("user protocol argument tuple allocation failed"))
        return -1
    py_tuple_set_item(
        load_ptr(slots, _PROTOCOL_UNARY_ARGS * C_POINTER_SIZE), 0,
        load_ptr(slots, _PROTOCOL_UNARY_SELF * C_POINTER_SIZE),
    )
    if py_err_occurred() != 0:
        return -1
    store_ptr(slots, _PROTOCOL_UNARY_RESULT * C_POINTER_SIZE, py_func_call(
        load_ptr(slots, _PROTOCOL_UNARY_METHOD * C_POINTER_SIZE),
        load_ptr(slots, _PROTOCOL_UNARY_ARGS * C_POINTER_SIZE),
    ))
    if _protocol_unary_adopt(slots, tokens, _PROTOCOL_UNARY_RESULT) != 0:
        return -1
    if ptr_is_null(load_ptr(slots, _PROTOCOL_UNARY_RESULT * C_POINTER_SIZE)) != 0:
        _protocol_require_result(null(), cstr("user protocol call"), cstr("user protocol callback returned NULL without an exception"))
        return -1
    return 0


def _protocol_unary_pin_result(slot: c_ptr) -> int:
    pcc_py_gc_minor_graph_lock()
    value = pcc_gc_load_ptr(null(), slot)
    prior: int = 0
    if ptr_is_null(value) == 0 and is_tagged_int(value) == 0:
        prior = load_i32(value, PYOBJECTHEADER_FLAGS_OFFSET) & 64
        pcc_gc_pin(value)
    pcc_py_gc_minor_graph_unlock()
    return prior


def _call_unary_function(method, self_obj):
    # Precondition: the selected managed method has a live external owner and
    # a stable address through dispatch classification and this initial pin.
    # A borrowed method-table entry alone does not establish that contract;
    # lookup/replacement before this helper is a separate unresolved boundary.
    # self_obj is borrowed from a live, address-stable caller root.
    method_pin: int = load_i32(method, PYOBJECTHEADER_FLAGS_OFFSET) & 64
    pcc_gc_pin(method)
    borrowed = stack_alloc(_PROTOCOL_UNARY_BORROWED_COUNT * C_POINTER_SIZE)
    store_ptr(borrowed, _PROTOCOL_UNARY_METHOD * C_POINTER_SIZE, method)
    store_ptr(borrowed, _PROTOCOL_UNARY_SELF * C_POINTER_SIZE, self_obj)
    pcc_gc_frame_enter(global_addr("pcc_protocol_unary_borrowed_map"), borrowed)
    slots = stack_alloc(_PROTOCOL_UNARY_SLOT_COUNT * C_POINTER_SIZE)
    tokens = stack_alloc(_PROTOCOL_UNARY_SLOT_COUNT * C_POINTER_SIZE)
    memset(slots, 0, _PROTOCOL_UNARY_SLOT_COUNT * C_POINTER_SIZE)
    memset(tokens, 0, _PROTOCOL_UNARY_SLOT_COUNT * C_POINTER_SIZE)
    pcc_gc_frame_enter(global_addr("pcc_protocol_unary_owned_map"), slots)
    status: int = _protocol_unary_body(slots, tokens, borrowed)
    py_tls_exc_swap_slot(ptr_add(slots, _PROTOCOL_UNARY_ERROR * C_POINTER_SIZE))
    # The result has an independent counted owner even when it aliases method,
    # self, or the argument tuple. Restore the temporary input pin before
    # disposal; no unregistered raw result crosses this operation.
    method_slot = ptr_add(borrowed, _PROTOCOL_UNARY_METHOD * C_POINTER_SIZE)
    store_ptr(method_slot, 0, pcc_gc_take_pinned_slot(method_slot, method_pin))
    memset(borrowed, 0, _PROTOCOL_UNARY_BORROWED_COUNT * C_POINTER_SIZE)
    _protocol_unary_drop(slots, tokens, _PROTOCOL_UNARY_ARGS)
    _protocol_unary_drop(slots, tokens, _PROTOCOL_UNARY_SELF)
    _protocol_unary_drop(slots, tokens, _PROTOCOL_UNARY_METHOD)
    if status != 0:
        _protocol_unary_drop(slots, tokens, _PROTOCOL_UNARY_RESULT)
    py_clear_exception()
    py_tls_exc_swap_slot(ptr_add(slots, _PROTOCOL_UNARY_ERROR * C_POINTER_SIZE))
    result_slot = ptr_add(slots, _PROTOCOL_UNARY_RESULT * C_POINTER_SIZE)
    prior: int = _protocol_unary_pin_result(result_slot)
    if pcc_gc_foreign_lease_release(result_slot, load_i64(tokens, _PROTOCOL_UNARY_RESULT * C_POINTER_SIZE)) != 0:
        pcc_platform_abort()
        return null()
    pcc_gc_frame_leave(slots)
    pcc_gc_frame_leave(borrowed)
    return pcc_gc_take_pinned_slot(result_slot, prior)


def _call_unary(method, self_obj):
    # A missing method is the lookup sentinel consumed by the caller. Once a
    # method was selected, every NULL is an error result.
    if ptr_is_null(method) != 0:
        return null()
    if _ptr_can_have_header(method) != 0 and _type_of(method) == PY_TYPE_FUNC:
        return _call_unary_function(method, self_obj)
    return _protocol_require_result(
        call_ptr1(method, self_obj),
        cstr("user protocol call"),
        cstr("user protocol callback returned NULL without an exception"),
    )


# Only the selected managed PyFunc branch enters these frames. Raw C method
# addresses keep the separate call_ptr2 route below.
_PROTOCOL_BINARY_METHOD = 0
_PROTOCOL_BINARY_SELF = 1
_PROTOCOL_BINARY_ARG = 2
_PROTOCOL_BINARY_ARGS = 3
_PROTOCOL_BINARY_RESULT = 4
_PROTOCOL_BINARY_ERROR = 5
_PROTOCOL_BINARY_SLOT_COUNT = 6
_PROTOCOL_BINARY_BORROWED_COUNT = 3

define_global_i32("pcc_protocol_binary_borrowed_map", -3)
define_global_i32("pcc_protocol_binary_owned_map", 6)


def _protocol_binary_adopt(slots: c_ptr, tokens: c_ptr, index: int) -> int:
    # Every NEW producer has already stored into its registered owning slot.
    offset: int = index * C_POINTER_SIZE
    slot = ptr_add(slots, offset)
    token: int = pcc_gc_foreign_lease_acquire(slot)
    if token < 0:
        py_runtime_error_if_unset(cstr("user protocol call"), cstr("binary result owner lease failed"))
        return -1
    store_i64(tokens, offset, token)
    pcc_py_gc_minor_graph_lock()
    pcc_gc_note_slot_write_barrier(null(), slot, load_ptr(slot, 0))
    pcc_py_gc_minor_graph_unlock()
    return 0


def _protocol_binary_drop(slots: c_ptr, tokens: c_ptr, index: int) -> None:
    offset: int = index * C_POINTER_SIZE
    slot = ptr_add(slots, offset)
    if pcc_gc_foreign_lease_release(slot, load_i64(tokens, offset)) != 0:
        pcc_platform_abort()
        return
    store_i64(tokens, offset, 0)
    pcc_gc_store_root(slot, null())


def _protocol_binary_body(slots: c_ptr, tokens: c_ptr, borrowed: c_ptr) -> int:
    index: int = 0
    while index < _PROTOCOL_BINARY_BORROWED_COUNT:
        offset: int = index * C_POINTER_SIZE
        token: int = pcc_gc_root_copy_borrowed_lease(
            ptr_add(slots, offset), ptr_add(borrowed, offset),
        )
        if token < 0:
            py_runtime_error_if_unset(cstr("user protocol call"), cstr("binary input owner copy failed"))
            return -1
        store_i64(tokens, offset, token)
        index += 1
    store_ptr(slots, _PROTOCOL_BINARY_ARGS * C_POINTER_SIZE, py_tuple_new(2))
    if _protocol_binary_adopt(slots, tokens, _PROTOCOL_BINARY_ARGS) != 0:
        return -1
    if ptr_is_null(load_ptr(slots, _PROTOCOL_BINARY_ARGS * C_POINTER_SIZE)) != 0:
        _protocol_require_result(null(), cstr("py_tuple_new"), cstr("user protocol argument tuple allocation failed"))
        return -1
    py_tuple_set_item(
        load_ptr(slots, _PROTOCOL_BINARY_ARGS * C_POINTER_SIZE), 0,
        load_ptr(slots, _PROTOCOL_BINARY_SELF * C_POINTER_SIZE),
    )
    if py_err_occurred() != 0:
        return -1
    py_tuple_set_item(
        load_ptr(slots, _PROTOCOL_BINARY_ARGS * C_POINTER_SIZE), 1,
        load_ptr(slots, _PROTOCOL_BINARY_ARG * C_POINTER_SIZE),
    )
    if py_err_occurred() != 0:
        return -1
    store_ptr(slots, _PROTOCOL_BINARY_RESULT * C_POINTER_SIZE, py_func_call(
        load_ptr(slots, _PROTOCOL_BINARY_METHOD * C_POINTER_SIZE),
        load_ptr(slots, _PROTOCOL_BINARY_ARGS * C_POINTER_SIZE),
    ))
    if _protocol_binary_adopt(slots, tokens, _PROTOCOL_BINARY_RESULT) != 0:
        return -1
    if ptr_is_null(load_ptr(slots, _PROTOCOL_BINARY_RESULT * C_POINTER_SIZE)) != 0:
        _protocol_require_result(null(), cstr("user protocol call"), cstr("user protocol callback returned NULL without an exception"))
        return -1
    return 0


def _protocol_binary_pin_result(slot: c_ptr) -> int:
    pcc_py_gc_minor_graph_lock()
    value = pcc_gc_load_ptr(null(), slot)
    prior: int = 0
    if ptr_is_null(value) == 0 and is_tagged_int(value) == 0:
        prior = load_i32(value, PYOBJECTHEADER_FLAGS_OFFSET) & 64
        pcc_gc_pin(value)
    pcc_py_gc_minor_graph_unlock()
    return prior


def _call_binary_function(method, self_obj, arg):
    # Precondition: the selected managed method has a live external owner and
    # a stable address through dispatch classification and this initial pin.
    # A borrowed method-table entry alone does not establish that contract;
    # lookup/replacement before this helper is a separate unresolved boundary.
    # self_obj and arg are borrowed from live, address-stable caller roots.
    method_pin: int = load_i32(method, PYOBJECTHEADER_FLAGS_OFFSET) & 64
    pcc_gc_pin(method)
    borrowed = stack_alloc(_PROTOCOL_BINARY_BORROWED_COUNT * C_POINTER_SIZE)
    store_ptr(borrowed, _PROTOCOL_BINARY_METHOD * C_POINTER_SIZE, method)
    store_ptr(borrowed, _PROTOCOL_BINARY_SELF * C_POINTER_SIZE, self_obj)
    store_ptr(borrowed, _PROTOCOL_BINARY_ARG * C_POINTER_SIZE, arg)
    pcc_gc_frame_enter(global_addr("pcc_protocol_binary_borrowed_map"), borrowed)
    slots = stack_alloc(_PROTOCOL_BINARY_SLOT_COUNT * C_POINTER_SIZE)
    tokens = stack_alloc(_PROTOCOL_BINARY_SLOT_COUNT * C_POINTER_SIZE)
    memset(slots, 0, _PROTOCOL_BINARY_SLOT_COUNT * C_POINTER_SIZE)
    memset(tokens, 0, _PROTOCOL_BINARY_SLOT_COUNT * C_POINTER_SIZE)
    pcc_gc_frame_enter(global_addr("pcc_protocol_binary_owned_map"), slots)
    status: int = _protocol_binary_body(slots, tokens, borrowed)
    py_tls_exc_swap_slot(ptr_add(slots, _PROTOCOL_BINARY_ERROR * C_POINTER_SIZE))
    # The result has an independent counted owner even when it aliases method,
    # either operand, or the argument tuple. Restore the temporary input pin
    # before disposal; no unregistered raw result crosses this operation.
    method_slot = ptr_add(borrowed, _PROTOCOL_BINARY_METHOD * C_POINTER_SIZE)
    store_ptr(method_slot, 0, pcc_gc_take_pinned_slot(method_slot, method_pin))
    memset(borrowed, 0, _PROTOCOL_BINARY_BORROWED_COUNT * C_POINTER_SIZE)
    _protocol_binary_drop(slots, tokens, _PROTOCOL_BINARY_ARGS)
    _protocol_binary_drop(slots, tokens, _PROTOCOL_BINARY_ARG)
    _protocol_binary_drop(slots, tokens, _PROTOCOL_BINARY_SELF)
    _protocol_binary_drop(slots, tokens, _PROTOCOL_BINARY_METHOD)
    if status != 0:
        _protocol_binary_drop(slots, tokens, _PROTOCOL_BINARY_RESULT)
    py_clear_exception()
    py_tls_exc_swap_slot(ptr_add(slots, _PROTOCOL_BINARY_ERROR * C_POINTER_SIZE))
    result_slot = ptr_add(slots, _PROTOCOL_BINARY_RESULT * C_POINTER_SIZE)
    prior: int = _protocol_binary_pin_result(result_slot)
    if pcc_gc_foreign_lease_release(result_slot, load_i64(tokens, _PROTOCOL_BINARY_RESULT * C_POINTER_SIZE)) != 0:
        pcc_platform_abort()
        return null()
    pcc_gc_frame_leave(slots)
    pcc_gc_frame_leave(borrowed)
    return pcc_gc_take_pinned_slot(result_slot, prior)


def _call_binary(method, self_obj, arg):
    if ptr_is_null(method) != 0:
        return null()
    if _ptr_can_have_header(method) != 0 and _type_of(method) == PY_TYPE_FUNC:
        return _call_binary_function(method, self_obj, arg)
    return _protocol_require_result(
        call_ptr2(method, self_obj, arg),
        cstr("user protocol call"),
        cstr("user protocol callback returned NULL without an exception"),
    )


def _call_ternary(method, self_obj, a, b):
    if ptr_is_null(method) != 0:
        return null()
    if _ptr_can_have_header(method) != 0 and _type_of(method) == PY_TYPE_FUNC:
        args = py_tuple_new(3)
        if ptr_is_null(args) != 0:
            return _protocol_require_result(
                null(),
                cstr("py_tuple_new"),
                cstr("user protocol argument tuple allocation failed"),
            )
        py_tuple_set_item(args, 0, self_obj)
        py_tuple_set_item(args, 1, a)
        py_tuple_set_item(args, 2, b)
        result = py_func_call(method, args)
        _protocol_require_result(
            result,
            cstr("user protocol call"),
            cstr("user protocol callback returned NULL without an exception"),
        )
        py_decref(args)
        return result
    return _protocol_require_result(
        call_ptr3(method, self_obj, a, b),
        cstr("user protocol call"),
        cstr("user protocol callback returned NULL without an exception"),
    )


def _write_handled(handled, value: int) -> None:
    if ptr_is_null(handled) == 0:
        store_i64(handled, 0, value)


def _int_result_value(result, valid_nonnegative: int) -> int:
    overflow = stack_alloc(4)
    store_i32(overflow, 0, 0)
    value: int = py_int_to_i64(result, overflow)
    if load_i32(overflow, 0) != 0:
        return 0
    if valid_nonnegative != 0 and value < 0:
        return 0
    return value


def _class_is_dict_subclass(obj) -> int:
    cls = _instance_class(obj)
    if ptr_is_null(cls) != 0:
        return 0
    if (load_i32(cls, PYOBJECTHEADER_FLAGS_OFFSET) & 4) != 0:
        return 1
    return 0


def _dict_subclass_env(obj, create: int):
    if _class_is_dict_subclass(obj) == 0:
        return null()
    cls = _instance_class(obj)
    # Dict item backing uses the reserved hidden slot even for __slots__
    # subclasses. Ordinary dynamic attribute access still enforces its flag.
    if ptr_is_null(cls) != 0:
        return null()
    n_fields: int = load_i32(cls, PYCLASSOBJECT_N_FIELDS_OFFSET)
    if n_fields < 0:
        n_fields = 0
    slot = ptr_add(
        obj,
        PYINSTANCEOBJECT_FIELDS_OFFSET + n_fields * C_POINTER_SIZE,
    )
    env = pcc_gc_load_ptr(obj, slot)
    if ptr_is_null(env) != 0 and create != 0:
        env = py_dict_new()
        if ptr_is_null(env) != 0:
            return null()
        pcc_gc_store_ptr(obj, slot, env)
        py_decref(env)
        env = pcc_gc_load_ptr(obj, slot)
    return env


def _dict_items_key():
    return py_str_new(cstr("\x00pcc.dict.items"), 15)


def _dict_subclass_backing(obj, create: int):
    env = _dict_subclass_env(obj, create)
    if ptr_is_null(env) != 0:
        return null()
    key = _dict_items_key()
    if ptr_is_null(key) != 0:
        return null()
    backing = py_dict_get(env, key)
    if ptr_is_null(backing) != 0 and create != 0:
        backing = py_dict_new()
        if ptr_is_null(backing) != 0:
            py_decref(key)
            return null()
        py_dict_set(env, key, backing)
        py_decref(key)
        py_decref(backing)
        return _dict_subclass_backing(obj, 0)
    py_decref(key)
    if ptr_is_null(backing) == 0:
        py_decref(backing)
    return backing


@c_abi_export("py_user_len_dispatch")
def py_user_len_dispatch(obj, handled) -> int:
    _write_handled(handled, 0)
    method = _lookup_dunder(obj, cstr("__len__"))
    if ptr_is_null(method) != 0:
        if _class_is_dict_subclass(obj) != 0:
            _write_handled(handled, 1)
            backing = _dict_subclass_backing(obj, 0)
            if ptr_is_null(backing) == 0:
                return py_dict_len(backing)
        return 0
    _write_handled(handled, 1)
    result = _call_unary(method, obj)
    if ptr_is_null(result) != 0:
        return 0
    value: int = _int_result_value(result, 1)
    py_decref(result)
    return value


@c_abi_export("py_user_abs_dispatch")
def py_user_abs_dispatch(obj):
    method = _lookup_dunder(obj, cstr("__abs__"))
    if ptr_is_null(method) != 0:
        return null()
    return _call_unary(method, obj)


@c_abi_export("py_user_bool_dispatch")
def py_user_bool_dispatch(obj, handled) -> int:
    _write_handled(handled, 0)
    method = _lookup_dunder(obj, cstr("__bool__"))
    if ptr_is_null(method) != 0:
        return 0
    _write_handled(handled, 1)
    result = _call_unary(method, obj)
    if ptr_is_null(result) != 0:
        return 0
    truth: int = py_obj_truthy(result)
    py_decref(result)
    if truth != 0:
        return 1
    return 0


@c_abi_export("py_obj_index")
def py_obj_index(obj):
    """Return a new Python int via __index__, without a machine-width lane."""
    if ptr_is_null(obj) != 0:
        py_raise_owned(py_exc_new(3, cstr("object cannot be interpreted as an integer")))
        return null()
    tag: int = _type_of(obj)
    if tag == PY_TYPE_INT:
        py_incref(obj)
        return obj
    if tag == PY_TYPE_BOOL:
        if ptr_eq(obj, global_load_ptr("py_True")) != 0:
            return py_int_from_i64(1)
        return py_int_from_i64(0)
    if pcc_capi_is_cext_type_tag(tag) != 0:
        # The owned C-API number implementation owns extension nb_index slots.
        return PyNumber_Index(obj)
    method = _lookup_dunder(obj, cstr("__index__"))
    if ptr_is_null(method) != 0:
        py_raise_owned(py_exc_new(3, cstr("object cannot be interpreted as an integer")))
        return null()
    result = _call_unary(method, obj)
    if ptr_is_null(result) != 0:
        return null()
    result_tag: int = _type_of(result)
    if result_tag == PY_TYPE_INT:
        return result
    if result_tag == PY_TYPE_BOOL:
        # CPython accepts this with a DeprecationWarning. The C-API warning
        # owner still ignores warnings, so do not silently discard that effect.
        py_decref(result)
        py_raise_owned(py_exc_new(11, cstr("pcc operator.index: non-exact __index__ results require owned warning emission")))
        return null()
    py_decref(result)
    py_raise_owned(py_exc_new(3, cstr("__index__ returned non-int")))
    return null()


@c_abi_export("py_obj_index_i64")
def py_obj_index_i64(obj) -> int:
    if ptr_is_null(obj) != 0:
        py_raise_owned(py_exc_new(3, cstr("object cannot be interpreted as an integer")))
        return 0
    if is_tagged_int(obj) != 0:
        return py_int_value_i64(obj)
    tag: int = _type_of(obj)
    if tag == PY_TYPE_INT:
        return py_int_value_i64(obj)
    if tag == PY_TYPE_BOOL:
        if ptr_eq(obj, global_load_ptr("py_True")) != 0:
            return 1
        return 0
    method = _lookup_dunder(obj, cstr("__index__"))
    if ptr_is_null(method) != 0:
        py_raise_owned(py_exc_new(3, cstr("object cannot be interpreted as an integer")))
        return 0
    result = _call_unary(method, obj)
    if ptr_is_null(result) != 0:
        return 0
    if is_tagged_int(result) != 0 or _type_of(result) == PY_TYPE_INT:
        overflow = stack_alloc(4)
        store_i32(overflow, 0, 0)
        value: int = py_int_to_i64(result, overflow)
        py_decref(result)
        if load_i32(overflow, 0) == 0:
            return value
    else:
        py_decref(result)
    py_raise_owned(py_exc_new(3, cstr("__index__ returned non-int")))
    return 0


@c_abi_export("py_index_i64_checked")
def py_index_i64_checked(obj) -> int:
    """Integer protocol for APIs whose platform index conversion must overflow."""
    if ptr_is_null(obj) != 0:
        py_raise_owned(py_exc_new(3, cstr("object cannot be interpreted as an integer")))
        return 0
    value = obj
    owned: int = 0
    tag: int = _type_of(obj)
    if tag == PY_TYPE_BOOL:
        if ptr_eq(obj, global_load_ptr("py_True")) != 0:
            return 1
        return 0
    if tag != PY_TYPE_INT:
        method = _lookup_dunder(obj, cstr("__index__"))
        if ptr_is_null(method) != 0:
            py_raise_owned(py_exc_new(3, cstr("object cannot be interpreted as an integer")))
            return 0
        value = _call_unary(method, obj)
        if ptr_is_null(value) != 0:
            return 0
        owned = 1
        if _type_of(value) != PY_TYPE_INT:
            py_decref(value)
            py_raise_owned(py_exc_new(3, cstr("__index__ returned non-int")))
            return 0
    overflow = stack_alloc(4)
    store_i32(overflow, 0, 0)
    result: int = py_int_to_i64(value, overflow)
    if owned != 0:
        py_decref(value)
    if load_i32(overflow, 0) != 0:
        py_raise_owned(py_exc_new(15, cstr("Python int too large to convert to platform index")))
        return 0
    return result


def _slice_integer_i64(value) -> int:
    overflow = stack_alloc(4)
    store_i32(overflow, 0, 0)
    result: int = py_int_to_i64(value, overflow)
    if load_i32(overflow, 0) == 0:
        return result
    zero = py_int_from_i64(0)
    sign: int = py_int_cmp(value, zero)
    py_decref(zero)
    if sign < 0:
        return -9223372036854775808
    return 9223372036854775807


@c_abi_export("py_slice_index_i64")
def py_slice_index_i64(obj, default: int) -> int:
    """Optional __index__ value, saturated to the platform index range."""
    if ptr_is_null(obj) != 0 or _type_of(obj) == PY_TYPE_NONE:
        return default
    if is_tagged_int(obj) != 0 or _type_of(obj) == PY_TYPE_INT:
        return _slice_integer_i64(obj)
    if _type_of(obj) == PY_TYPE_BOOL:
        if ptr_eq(obj, global_load_ptr("py_True")) != 0:
            return 1
        return 0
    method = _lookup_dunder(obj, cstr("__index__"))
    if ptr_is_null(method) != 0:
        py_raise_owned(py_exc_new(3, cstr("slice indices must be integers or None or have an __index__ method")))
        return 0
    result = _call_unary(method, obj)
    if ptr_is_null(result) != 0:
        return 0
    if is_tagged_int(result) != 0 or _type_of(result) == PY_TYPE_INT:
        value: int = _slice_integer_i64(result)
        py_decref(result)
        return value
    py_decref(result)
    py_raise_owned(py_exc_new(3, cstr("__index__ returned non-int")))
    return 0


@c_abi_export("py_user_contains_dispatch")
def py_user_contains_dispatch(obj, item, handled) -> int:
    _write_handled(handled, 0)
    method = _lookup_dunder(obj, cstr("__contains__"))
    if ptr_is_null(method) != 0:
        if _class_is_dict_subclass(obj) != 0:
            _write_handled(handled, 1)
            backing = _dict_subclass_backing(obj, 0)
            if ptr_is_null(backing) == 0 and py_dict_contains(backing, item) != 0:
                return 1
        return 0
    _write_handled(handled, 1)
    result = _call_binary(method, obj, item)
    if ptr_is_null(result) != 0:
        return 0
    truth: int = py_obj_truthy(result)
    py_decref(result)
    if truth != 0:
        return 1
    return 0


@c_abi_export("py_user_eq_dispatch")
def py_user_eq_dispatch(a, b) -> int:
    depth_addr = global_addr("pcc_py_protocol_eq_depth")
    depth: int = load_i32(depth_addr, 0)
    if depth >= 64:
        return -1
    method = _lookup_dunder(a, cstr("__eq__"))
    if ptr_is_null(method) == 0:
        store_i32(depth_addr, 0, depth + 1)
        result = _call_binary(method, a, b)
        store_i32(depth_addr, 0, depth)
        if ptr_is_null(result) != 0:
            return 0
        if ptr_eq(result, global_load_ptr("py_NotImplemented")) == 0:
            truth: int = py_obj_truthy(result)
            py_decref(result)
            if truth != 0:
                return 1
            return 0
        py_decref(result)
    if _type_of(a) == _type_of(b):
        return -1
    method = _lookup_dunder(b, cstr("__eq__"))
    if ptr_is_null(method) != 0:
        return -1
    store_i32(depth_addr, 0, depth + 1)
    result = _call_binary(method, b, a)
    store_i32(depth_addr, 0, depth)
    if ptr_is_null(result) != 0:
        return 0
    if ptr_eq(result, global_load_ptr("py_NotImplemented")) != 0:
        py_decref(result)
        return -1
    truth: int = py_obj_truthy(result)
    py_decref(result)
    if truth != 0:
        return 1
    return 0


def _order_call(method, self_obj, other) -> int:
    if ptr_is_null(method) != 0:
        return -1
    result = _call_binary(method, self_obj, other)
    if ptr_is_null(result) != 0:
        return 0
    if ptr_eq(result, global_load_ptr("py_NotImplemented")) != 0:
        py_decref(result)
        return -1
    truth: int = py_obj_truthy(result)
    py_decref(result)
    if truth != 0:
        return 1
    return 0


@c_abi_export("py_user_order_dispatch")
def py_user_order_dispatch(a, b, op: int) -> int:
    # ``a < b`` (op 0), ``<=`` (1), ``>`` (4), ``>=`` (5) through user
    # dunders in CPython's order: the rhs's reflected method first when its
    # class is a proper subclass of the lhs class, then the lhs method, then
    # the reflected one.  -1 when no user method answered, so the caller
    # keeps its builtin ordering; a raising method returns 0 with the error
    # set.
    name = cstr("__lt__")
    rname = cstr("__gt__")
    if op == 1:
        name = cstr("__le__")
        rname = cstr("__ge__")
    elif op == 4:
        name = cstr("__gt__")
        rname = cstr("__lt__")
    elif op == 5:
        name = cstr("__ge__")
        rname = cstr("__le__")
    a_cls = _instance_class(a)
    b_cls = _instance_class(b)
    if ptr_is_null(a_cls) != 0 and ptr_is_null(b_cls) != 0:
        return -1
    reflected_done: int = 0
    verdict: int = -1
    if (
        ptr_is_null(a_cls) == 0
        and ptr_is_null(b_cls) == 0
        and ptr_eq(a_cls, b_cls) == 0
        and py_obj_issubclass(b_cls, a_cls) > 0
    ):
        reflected_done = 1
        verdict = _order_call(_lookup_dunder(b, rname), b, a)
        if verdict != -1:
            return verdict
    verdict = _order_call(_lookup_dunder(a, name), a, b)
    if verdict != -1:
        return verdict
    if reflected_done == 0:
        verdict = _order_call(_lookup_dunder(b, rname), b, a)
    return verdict


@c_abi_export("py_user_getitem_dispatch")
def py_user_getitem_dispatch(obj, key):
    method = _lookup_dunder(obj, cstr("__getitem__"))
    if ptr_is_null(method) != 0:
        if _class_is_dict_subclass(obj) != 0:
            return py_dict_subclass_getitem(obj, key)
        return null()
    return _call_binary(method, obj, key)


@c_abi_export("py_user_matmul_dispatch")
def py_user_matmul_dispatch(a, b):
    method = _lookup_dunder(a, cstr("__matmul__"))
    if ptr_is_null(method) == 0:
        result = _call_binary(method, a, b)
        if ptr_eq(result, global_load_ptr("py_NotImplemented")) == 0:
            return result
        py_decref(result)
    method = _lookup_dunder(b, cstr("__rmatmul__"))
    if ptr_is_null(method) == 0:
        result = _call_binary(method, b, a)
        if ptr_eq(result, global_load_ptr("py_NotImplemented")) == 0:
            return result
        py_decref(result)
    py_raise_owned(py_exc_new(3, cstr("unsupported operand type(s) for @")))
    return null()


def _unary_operand_error(value, op: int):
    # CPython: "bad operand type for unary -: 'str'".
    message = stack_alloc(160)
    prefix = cstr("bad operand type for unary ")
    length: int = 0
    while load_i8(prefix, length) != 0:
        store_i8(message, length, load_i8(prefix, length))
        length = length + 1
    store_i8(message, length, op)
    store_i8(message, length + 1, 58)
    store_i8(message, length + 2, 32)
    store_i8(message, length + 3, 39)
    length = length + 4
    name = py_obj_type_name(value)
    if ptr_is_null(name) == 0:
        text = py_str_utf8(name)
        count: int = py_str_byte_len(name)
        if count > 120:
            count = 120
        index: int = 0
        while index < count:
            store_i8(message, length, load_i8(text, index))
            length = length + 1
            index = index + 1
        py_decref(name)
    store_i8(message, length, 39)
    store_i8(message, length + 1, 0)
    py_raise_owned(py_exc_new(3, message))
    return null()


def _py_obj_unary(value, op: int):
    # ``-x`` / ``+x`` / ``~x`` (op '-', '+', '~') with CPython's dispatch.
    # The frontend used to lower a dynamically-typed operand through the i64
    # lane: the lane read a float object as 0, so ``-f`` on a dyn float was
    # the int 0 (pcc1's ``_bits_to_float64`` returned 0.0 for every negative
    # double).
    if ptr_is_null(value) != 0:
        return null()
    tag: int = _type_of(value)
    if tag == PY_TYPE_INT:
        if op == 45:
            return py_int_neg(value)
        if op == 126:
            minus_one = py_int_from_i64(-1)
            result = py_int_xor(value, minus_one)
            py_decref(minus_one)
            return result
        py_incref(value)
        return value
    if tag == PY_TYPE_BOOL:
        bit: int = 0
        if ptr_eq(value, global_load_ptr("py_True")) != 0:
            bit = 1
        if op == 45:
            return py_int_from_i64(0 - bit)
        if op == 126:
            return py_int_from_i64(-1 - bit)
        return py_int_from_i64(bit)
    if tag == PY_TYPE_FLOAT and op != 126:
        if op == 45:
            number: float = py_float_to_f64(value)
            return py_float_from_f64(-number)
        py_incref(value)
        return value
    if tag == PY_TYPE_COMPLEX and op != 126:
        if op == 45:
            return py_complex_neg(value)
        py_incref(value)
        return value
    if _is_user_instance(value) != 0:
        method = null()
        if op == 45:
            method = _lookup_dunder(value, cstr("__neg__"))
        elif op == 43:
            method = _lookup_dunder(value, cstr("__pos__"))
        else:
            method = _lookup_dunder(value, cstr("__invert__"))
        if ptr_is_null(method) == 0:
            return _call_unary(method, value)
    return _unary_operand_error(value, op)


@c_abi_export("py_obj_neg")
def py_obj_neg(value):
    return _py_obj_unary(value, 45)


@c_abi_export("py_obj_pos")
def py_obj_pos(value):
    return _py_obj_unary(value, 43)


@c_abi_export("py_obj_invert")
def py_obj_invert(value):
    return _py_obj_unary(value, 126)


@c_abi_export("py_user_binop_dispatch")
def py_user_binop_dispatch(a, b, name, rname, type_err_msg):
    # CPython's order: the reflected method belongs to an rhs of another
    # type only, and runs first when that type is a subclass of the lhs type
    # that overrides it.
    a_cls = _instance_class(a)
    b_cls = _instance_class(b)
    reflected_ok: int = 1
    if ptr_is_null(a_cls) == 0 and ptr_eq(a_cls, b_cls) != 0:
        reflected_ok = 0
    if reflected_ok != 0 and ptr_is_null(a_cls) == 0 and ptr_is_null(b_cls) == 0:
        first = py_class_lookup(b_cls, rname)
        if (
            ptr_is_null(first) == 0
            and ptr_eq(first, py_class_lookup(a_cls, rname)) == 0
            and py_obj_issubclass(b_cls, a_cls) > 0
        ):
            result = _call_binary(first, b, a)
            if ptr_eq(result, global_load_ptr("py_NotImplemented")) == 0:
                return result
            py_decref(result)
            reflected_ok = 0
    method = _lookup_dunder(a, name)
    if ptr_is_null(method) == 0:
        result = _call_binary(method, a, b)
        if ptr_eq(result, global_load_ptr("py_NotImplemented")) == 0:
            return result
        py_decref(result)
    if reflected_ok != 0:
        method = _lookup_dunder(b, rname)
        if ptr_is_null(method) == 0:
            result = _call_binary(method, b, a)
            if ptr_eq(result, global_load_ptr("py_NotImplemented")) == 0:
                return result
            py_decref(result)
    py_raise_owned(py_exc_new(3, type_err_msg))
    return null()


@c_abi_export("py_obj_floordiv")
def py_obj_floordiv(a, b):
    if ptr_is_null(a) != 0 or ptr_is_null(b) != 0:
        py_raise_owned(py_exc_new(3, cstr("unsupported operand type(s) for //")))
        return null()
    at: int = _type_of(a)
    bt: int = _type_of(b)
    if (at == PY_TYPE_INT or at == PY_TYPE_BOOL) and (bt == PY_TYPE_INT or bt == PY_TYPE_BOOL):
        return py_int_floordiv(a, b)
    a_numeric: int = 1 if at == PY_TYPE_BOOL or at == PY_TYPE_INT or at == PY_TYPE_FLOAT else 0
    b_numeric: int = 1 if bt == PY_TYPE_BOOL or bt == PY_TYPE_INT or bt == PY_TYPE_FLOAT else 0
    if a_numeric != 0 and b_numeric != 0:
        divisor: float = py_float_to_f64(b)
        if divisor == 0.0:
            py_raise_owned(py_exc_new(9, cstr("float floor division by zero")))
            return null()
        return py_float_from_f64(floor_c(py_float_to_f64(a) / divisor))
    if (
        at == PY_TYPE_INSTANCE
        or at >= PY_TYPE_USER_CLASS_START
        or bt == PY_TYPE_INSTANCE
        or bt >= PY_TYPE_USER_CLASS_START
    ):
        return py_user_binop_dispatch(
            a,
            b,
            cstr("__floordiv__"),
            cstr("__rfloordiv__"),
            cstr("unsupported operand type(s) for //"),
        )
    py_raise_owned(py_exc_new(3, cstr("unsupported operand type(s) for //")))
    return null()


def _divmod_pack(q, r) -> c_ptr:
    out = py_tuple_new(2)
    if ptr_is_null(out) != 0:
        return _protocol_require_result(null(), cstr("divmod"), cstr("divmod result allocation failed"))
    pcc_gc_pin(out)
    py_tuple_set_item(out, 0, q)
    py_tuple_set_item(out, 1, r)
    pcc_gc_unpin(out)
    return out


def _divmod_numeric(a, b, at: int, bt: int) -> c_ptr:
    if at != PY_TYPE_FLOAT and bt != PY_TYPE_FLOAT:
        zero = py_int_from_i64(0)
        is_zero: int = py_int_cmp(b, zero)
        py_decref(zero)
        if is_zero == 0:
            py_raise_owned(py_exc_new(9, cstr("integer division or modulo by zero")))
            return null()
        q = py_int_floordiv(a, b)
        if ptr_is_null(q) != 0:
            return _protocol_require_result(null(), cstr("divmod"), cstr("integer quotient allocation failed"))
        pcc_gc_pin(q)
        r = py_int_mod(a, b)
        if ptr_is_null(r) != 0:
            pcc_gc_unpin(q)
            py_decref(q)
            return _protocol_require_result(null(), cstr("divmod"), cstr("integer remainder allocation failed"))
    else:
        av: float = py_float_to_f64(a)
        bv: float = py_float_to_f64(b)
        if bv == 0.0:
            py_raise_owned(py_exc_new(9, cstr("float divmod()")))
            return null()
        # Use the remainder to calculate the quotient. floor(a/b) alone is
        # wrong near exact multiples (e.g. divmod(1.0, 0.1)). These existing
        # fmod/floor symbols are emitted from the owned numeric substrate.
        rem: float = fmod_c(av, bv)
        div: float = f64_div(av - rem, bv)
        if rem != 0.0:
            if (bv < 0.0) != (rem < 0.0):
                rem = rem + bv
                div = div - 1.0
        elif f64_signbit(bv) != 0:
            rem = pcc_numeric_float_from_bits(-9223372036854775808)
        else:
            rem = 0.0
        quotient: float = 0.0
        if div != 0.0:
            quotient = floor_c(div)
            if div - quotient > 0.5:
                quotient = quotient + 1.0
        elif f64_signbit(f64_div(av, bv)) != 0:
            quotient = pcc_numeric_float_from_bits(-9223372036854775808)
        q = py_float_from_f64(quotient)
        pcc_gc_pin(q)
        r = py_float_from_f64(rem)
        if ptr_is_null(q) != 0 or ptr_is_null(r) != 0:
            pcc_gc_unpin(q)
            py_decref(q)
            py_decref(r)
            return _protocol_require_result(null(), cstr("divmod"), cstr("float divmod result allocation failed"))
    pcc_gc_pin(r)
    out = _divmod_pack(q, r)
    pcc_gc_pin(out)
    pcc_gc_unpin(r)
    py_decref(r)
    pcc_gc_unpin(q)
    py_decref(q)
    pcc_gc_unpin(out)
    return out


def _divmod_impl(a, b) -> c_ptr:
    at: int = _type_of(a)
    bt: int = _type_of(b)
    a_numeric: int = 1 if at == PY_TYPE_BOOL or at == PY_TYPE_INT or at == PY_TYPE_FLOAT else 0
    b_numeric: int = 1 if bt == PY_TYPE_BOOL or bt == PY_TYPE_INT or bt == PY_TYPE_FLOAT else 0
    if a_numeric != 0 and b_numeric != 0:
        return _divmod_numeric(a, b, at, bt)
    if pcc_capi_is_cext_type_tag(at) != 0 or pcc_capi_is_cext_type_tag(bt) != 0:
        result = pcc_capi_cext_binary_number(a, b, 4)
        if ptr_eq(result, global_load_ptr("py_NotImplemented")) == 0:
            return result
        py_decref(result)
    return py_user_binop_dispatch(
        a, b, cstr("__divmod__"), cstr("__rdivmod__"),
        cstr("unsupported operand type(s) for divmod()"),
    )


@c_abi_export("py_obj_divmod")
def py_obj_divmod(a, b) -> c_ptr:
    if ptr_is_null(a) != 0 or ptr_is_null(b) != 0:
        py_raise_owned(py_exc_new(3, cstr("unsupported operand type(s) for divmod()")))
        return null()
    # Runtime calls borrow operands. Hold updateable slots through callbacks;
    # a user dunder may mutate the owners from which either argument came.
    pins = stack_alloc(24)
    store_i64(pins, 0, 0)
    store_i64(pins, 8, 0)
    store_i64(pins, 16, 0)
    if _ptr_can_have_header(a) != 0:
        store_i64(pins, 0, load_i32(a, PYOBJECTHEADER_FLAGS_OFFSET) & 64)
    if _ptr_can_have_header(b) != 0:
        store_i64(pins, 8, load_i32(b, PYOBJECTHEADER_FLAGS_OFFSET) & 64)
    pcc_gc_pin(a)
    pcc_gc_pin(b)
    slots = stack_alloc(24)
    store_ptr(slots, 0, null())
    store_ptr(slots, 8, null())
    store_ptr(slots, 16, null())
    left_handle = pcc_gc_scheduler_root_register_handle(slots)
    right_handle = pcc_gc_scheduler_root_register_handle(ptr_add(slots, 8))
    result_handle = pcc_gc_scheduler_root_register_handle(ptr_add(slots, 16))
    pcc_gc_store_root(slots, a)
    pcc_gc_store_root(ptr_add(slots, 8), b)
    result = null()
    if ptr_is_null(left_handle) == 0 and ptr_is_null(right_handle) == 0 and ptr_is_null(result_handle) == 0:
        result = _divmod_impl(load_ptr(slots, 0), load_ptr(slots, 8))
        if _ptr_can_have_header(result) != 0:
            store_i64(pins, 16, load_i32(result, PYOBJECTHEADER_FLAGS_OFFSET) & 64)
        pcc_gc_pin(result)
        pcc_gc_store_root(ptr_add(slots, 16), result)
        py_decref(result)
    else:
        _protocol_require_result(null(), cstr("divmod"), cstr("divmod operand root registration failed"))
    prior_result_pin: int = load_i64(pins, 16)
    index: int = 2
    while index > 0:
        index = index - 1
        if ptr_eq(load_ptr(slots, 16), load_ptr(slots, index * C_POINTER_SIZE)) != 0:
            prior_result_pin = load_i64(pins, index * C_POINTER_SIZE)
    index = 2
    while index > 0:
        index = index - 1
        value = load_ptr(slots, index * C_POINTER_SIZE)
        pcc_gc_unpin(value)
        if load_i64(pins, index * C_POINTER_SIZE) != 0 and _ptr_can_have_header(value) != 0:
            atomic_rmw_i32("or", value, PYOBJECTHEADER_FLAGS_OFFSET, 64, "relaxed")
        pcc_gc_store_root(ptr_add(slots, index * C_POINTER_SIZE), null())
    pcc_gc_scheduler_root_unregister_handle(right_handle)
    pcc_gc_scheduler_root_unregister_handle(left_handle)
    result = load_ptr(slots, 16)
    if _ptr_can_have_header(result) != 0:
        atomic_rmw_i32("or", result, PYOBJECTHEADER_FLAGS_OFFSET, 64, "relaxed")
    pcc_gc_scheduler_root_unregister_handle(result_handle)
    return pcc_gc_take_pinned_slot(ptr_add(slots, 16), prior_result_pin)


@c_abi_export("py_obj_inplace_op")
def py_obj_inplace_op(a, b, op_code: int):
    if (
        ptr_is_null(a) == 0
        and is_tagged_int(a) == 0
        and op_code >= 0
        and op_code < 6
    ):
        at: int = _type_of(a)
        if pcc_capi_is_cext_type_tag(at) != 0:
            result = pcc_capi_cext_inplace_number(a, b, op_code)
            if ptr_is_null(result) != 0:
                return null()
            if ptr_eq(result, global_load_ptr("py_NotImplemented")) == 0:
                return result
            py_decref(result)
        elif _is_user_instance(a) != 0:
            name = cstr("__iadd__")
            if op_code == 1:
                name = cstr("__isub__")
            elif op_code == 2:
                name = cstr("__imul__")
            elif op_code == 3:
                name = cstr("__itruediv__")
            elif op_code == 4:
                name = cstr("__ifloordiv__")
            elif op_code == 5:
                name = cstr("__imod__")
            method = _lookup_dunder(a, name)
            if ptr_is_null(method) == 0:
                result = _call_binary(method, a, b)
                if ptr_eq(result, global_load_ptr("py_NotImplemented")) == 0:
                    return result
                py_decref(result)
    if op_code == 0:
        return py_obj_add(a, b)
    if op_code == 1:
        return py_obj_sub(a, b)
    if op_code == 2:
        return py_obj_mul(a, b)
    if op_code == 3:
        return py_obj_truediv(a, b)
    if op_code == 4:
        return py_obj_floordiv(a, b)
    if op_code == 5:
        return py_obj_mod(a, b)
    py_raise_owned(py_exc_new(3, cstr("unsupported in-place operand")))
    return null()


@c_abi_export("py_user_setitem_dispatch")
def py_user_setitem_dispatch(obj, key, value, handled) -> int:
    _write_handled(handled, 0)
    method = _lookup_dunder(obj, cstr("__setitem__"))
    if ptr_is_null(method) != 0:
        if _class_is_dict_subclass(obj) != 0:
            backing = _dict_subclass_backing(obj, 1)
            if ptr_is_null(backing) != 0:
                return -1
            py_dict_set(backing, key, value)
            _write_handled(handled, 1)
            if py_err_occurred() != 0:
                return -1
            return 0
        return -1
    _write_handled(handled, 1)
    result = _call_ternary(method, obj, key, value)
    if ptr_is_null(result) != 0:
        return -1
    py_decref(result)
    return 0


@c_abi_export("py_user_delitem_dispatch")
def py_user_delitem_dispatch(obj, key, handled) -> int:
    _write_handled(handled, 0)
    method = _lookup_dunder(obj, cstr("__delitem__"))
    if ptr_is_null(method) != 0:
        if _class_is_dict_subclass(obj) != 0:
            backing = _dict_subclass_backing(obj, 0)
            _write_handled(handled, 1)
            if ptr_is_null(backing) != 0:
                py_raise_owned(py_exc_new_with_value(4, key))
                return -1
            status: int = py_dict_del(backing, key)
            if status < 0 and py_err_occurred() == 0:
                py_raise_owned(py_exc_new_with_value(4, key))
            return status
        return -1
    _write_handled(handled, 1)
    result = _call_binary(method, obj, key)
    if ptr_is_null(result) != 0:
        return -1
    py_decref(result)
    return 0


def _dictsub_nargs(args) -> int:
    if ptr_is_null(args) != 0 or is_tagged_int(args) != 0 or _type_of(args) != PY_TYPE_TUPLE:
        return 0
    return py_tuple_len(args)


def _dictsub_get_entry(captures, args):
    self_obj = py_tuple_get(captures, 0)
    if ptr_is_null(self_obj) != 0:
        return null()
    nargs: int = _dictsub_nargs(args)
    key = null()
    if nargs >= 1:
        key = py_tuple_get(args, 0)
    default = global_load_ptr("py_None")
    if nargs >= 2:
        default = py_tuple_get(args, 1)
    else:
        py_incref(default)
    backing = _dict_subclass_backing(self_obj, 0)
    if ptr_is_null(backing) != 0:
        out = default
        py_incref(out)
    else:
        out = py_dict_get_default(backing, key, default)
    py_decref(self_obj)
    if ptr_is_null(key) == 0:
        py_decref(key)
    py_decref(default)
    return out


def _dictsub_keys_entry(captures, args):
    self_obj = py_tuple_get(captures, 0)
    if ptr_is_null(self_obj) != 0:
        return null()
    backing = _dict_subclass_backing(self_obj, 1)
    out = null()
    if ptr_is_null(backing) == 0:
        out = py_dict_keys(backing)
    py_decref(self_obj)
    return out


def _dictsub_values_entry(captures, args):
    self_obj = py_tuple_get(captures, 0)
    if ptr_is_null(self_obj) != 0:
        return null()
    backing = _dict_subclass_backing(self_obj, 1)
    out = null()
    if ptr_is_null(backing) == 0:
        out = py_dict_values(backing)
    py_decref(self_obj)
    return out


def _dictsub_items_entry(captures, args):
    self_obj = py_tuple_get(captures, 0)
    if ptr_is_null(self_obj) != 0:
        return null()
    backing = _dict_subclass_backing(self_obj, 1)
    out = null()
    if ptr_is_null(backing) == 0:
        out = py_dict_items(backing)
    py_decref(self_obj)
    return out


def _dictsub_pop_entry(captures, args):
    self_obj = py_tuple_get(captures, 0)
    if ptr_is_null(self_obj) != 0:
        return null()
    nargs: int = _dictsub_nargs(args)
    key = null()
    if nargs >= 1:
        key = py_tuple_get(args, 0)
    backing = _dict_subclass_backing(self_obj, 0)
    existing = null()
    if ptr_is_null(backing) == 0 and ptr_is_null(key) == 0:
        existing = py_dict_get(backing, key)
    if ptr_is_null(existing) == 0:
        out = existing
        py_dict_del(backing, key)
    elif nargs >= 2:
        out = py_tuple_get(args, 1)
    else:
        py_raise_owned(py_exc_new(4, cstr("pop(): key not found")))
        out = null()
    py_decref(self_obj)
    if ptr_is_null(key) == 0:
        py_decref(key)
    return out


def _dictsub_setdefault_entry(captures, args):
    self_obj = py_tuple_get(captures, 0)
    if ptr_is_null(self_obj) != 0:
        return null()
    nargs: int = _dictsub_nargs(args)
    key = null()
    if nargs >= 1:
        key = py_tuple_get(args, 0)
    default = global_load_ptr("py_None")
    if nargs >= 2:
        default = py_tuple_get(args, 1)
    else:
        py_incref(default)
    backing = _dict_subclass_backing(self_obj, 1)
    out = null()
    if ptr_is_null(backing) == 0 and ptr_is_null(key) == 0:
        out = py_dict_get(backing, key)
        if ptr_is_null(out) != 0:
            py_dict_set(backing, key, default)
            out = default
            py_incref(out)
    py_decref(self_obj)
    if ptr_is_null(key) == 0:
        py_decref(key)
    py_decref(default)
    return out


def _dictsub_clear_entry(captures, args):
    self_obj = py_tuple_get(captures, 0)
    if ptr_is_null(self_obj) != 0:
        return null()
    env = _dict_subclass_env(self_obj, 0)
    if ptr_is_null(env) == 0:
        key = _dict_items_key()
        if ptr_is_null(key) == 0:
            fresh = py_dict_new()
            if ptr_is_null(fresh) == 0:
                py_dict_set(env, key, fresh)
                py_decref(fresh)
            py_decref(key)
    py_decref(self_obj)
    none = global_load_ptr("py_None")
    py_incref(none)
    return none


def _cstr_equal(value, literal) -> int:
    if ptr_is_null(value) != 0 or ptr_is_null(literal) != 0:
        return 0
    i: int = 0
    while True:
        a: int = load_i8(value, i) & 255
        b: int = load_i8(literal, i) & 255
        if a != b:
            return 0
        if a == 0:
            return 1
        i = i + 1
    return 0


@c_abi_export("py_dict_subclass_getattr")
def py_dict_subclass_getattr(obj, name):
    if _class_is_dict_subclass(obj) == 0 or ptr_is_null(name) != 0:
        return null()
    captures = py_tuple_new(1)
    if ptr_is_null(captures) != 0:
        return null()
    py_tuple_set_item(captures, 0, obj)
    fn = null()
    if _cstr_equal(name, cstr("get")) != 0:
        fn = py_func_new_named(_dictsub_get_entry, captures, name)
    elif _cstr_equal(name, cstr("keys")) != 0:
        fn = py_func_new_named(_dictsub_keys_entry, captures, name)
    elif _cstr_equal(name, cstr("values")) != 0:
        fn = py_func_new_named(_dictsub_values_entry, captures, name)
    elif _cstr_equal(name, cstr("items")) != 0:
        fn = py_func_new_named(_dictsub_items_entry, captures, name)
    elif _cstr_equal(name, cstr("pop")) != 0:
        fn = py_func_new_named(_dictsub_pop_entry, captures, name)
    elif _cstr_equal(name, cstr("setdefault")) != 0:
        fn = py_func_new_named(_dictsub_setdefault_entry, captures, name)
    elif _cstr_equal(name, cstr("clear")) != 0:
        fn = py_func_new_named(_dictsub_clear_entry, captures, name)
    py_decref(captures)
    return fn


@c_abi_export("py_dict_subclass_getitem")
def py_dict_subclass_getitem(obj, key):
    backing = _dict_subclass_backing(obj, 0)
    if ptr_is_null(backing) == 0 and ptr_is_null(key) == 0:
        value = py_dict_get(backing, key)
        if ptr_is_null(value) == 0:
            return value
    missing = _lookup_dunder(obj, cstr("__missing__"))
    if ptr_is_null(missing) == 0:
        return _call_binary(missing, obj, key)
    py_raise_owned(py_exc_new(4, cstr("key not found")))
    return null()


# Builtin-base initialization starts from caller-owned slots. The hidden
# dictionary environment and its backing remain the generic storage owners.
pcc_gc_root_copy_lease = extern("pcc_gc_root_copy_lease", (c_ptr, c_ptr), c_int64)
pcc_gc_root_copy_borrowed_lease = extern("pcc_gc_root_copy_borrowed_lease", (c_ptr, c_ptr), c_int64)
pcc_gc_foreign_lease_acquire = extern("pcc_gc_foreign_lease_acquire", (c_ptr,), c_int64)
pcc_gc_foreign_lease_release = extern("pcc_gc_foreign_lease_release", (c_ptr, c_int64), c_int64)
pcc_gc_resolve_root_slot_unlocked = extern("pcc_gc_resolve_root_slot_unlocked", (c_ptr, c_int64), c_ptr)
pcc_gc_store_ptr_plan_init = extern("pcc_gc_store_ptr_plan_init", (c_ptr, c_ptr, c_int64), c_void)
pcc_gc_store_ptr_plan_commit_locked = extern("pcc_gc_store_ptr_plan_commit_locked", (c_ptr, c_ptr, c_ptr, c_ptr), c_int64)
pcc_gc_store_ptr_plan_finish = extern("pcc_gc_store_ptr_plan_finish", (c_ptr,), c_void)
pcc_gc_note_slot_write_barrier = extern("pcc_gc_note_slot_write_barrier", (c_ptr, c_ptr, c_ptr), c_void)
pcc_py_gc_minor_graph_lock = extern("pcc_py_gc_minor_graph_lock", (), c_void)
pcc_py_gc_minor_graph_unlock = extern("pcc_py_gc_minor_graph_unlock", (), c_void)
pcc_gc_backend = extern("pcc_gc_backend", (), c_int64)
pcc_platform_abort = extern("pcc_platform_abort", (), c_void)
py_tls_exc_swap_slot = extern("py_tls_exc_swap_slot", (c_ptr,), c_void)
py_clear_exception = extern("py_clear_exception", (), c_void)
py_dict_set_slots = extern("py_dict_set_slots", (c_ptr, c_ptr, c_ptr), c_int64)
py_dict_setdefault_slots = extern("py_dict_setdefault_slots", (c_ptr, c_ptr, c_ptr, c_ptr), c_int64)
py_dict_update_slots = extern("py_dict_update_slots", (c_ptr, c_ptr), c_int64)


# Scratch indices shared by the root, lease-token and registration arrays.
# Slot zero saves entry TLS; the final slot preserves an error during cleanup.
_BUILTIN_INIT_OLD_EXCEPTION_SLOT = 0
_BUILTIN_INIT_RECEIVER_SLOT = 1
_BUILTIN_INIT_FROM_CLASS_SLOT = 2
_BUILTIN_INIT_ARGS_SLOT = 3
_BUILTIN_INIT_KWARGS_SLOT = 4
_BUILTIN_INIT_CLASS_SLOT = 5
_BUILTIN_INIT_ENV_SLOT = 6
_BUILTIN_INIT_KEY_SLOT = 7
_BUILTIN_INIT_DICT_SLOT = 8
_BUILTIN_INIT_SOURCE_SLOT = 9
_BUILTIN_INIT_TEMP_SLOT = 10
_BUILTIN_INIT_ERROR_SLOT = 11
_BUILTIN_INIT_SLOT_COUNT = 12


def _builtin_init_error(message) -> int:
    py_runtime_error_if_unset(cstr("builtin-base initializer"), message)
    return -1


def _builtin_init_open(slots, tokens, handles) -> int:
    memset(slots, 0, _BUILTIN_INIT_SLOT_COUNT * C_POINTER_SIZE)
    memset(tokens, 0, _BUILTIN_INIT_SLOT_COUNT * C_POINTER_SIZE)
    memset(handles, 0, _BUILTIN_INIT_SLOT_COUNT * C_POINTER_SIZE)
    count: int = 0
    while count < _BUILTIN_INIT_SLOT_COUNT:
        handle = pcc_gc_scheduler_root_register_handle(ptr_add(slots, count * C_POINTER_SIZE))
        if ptr_is_null(handle) != 0:
            return count
        store_ptr(handles, count * C_POINTER_SIZE, handle)
        count = count + 1
    return count


def _builtin_init_copy(slots, tokens, index: int, source, borrowed: int) -> int:
    if ptr_is_null(source) != 0:
        return _builtin_init_error(cstr("initializer requires authoritative source slots"))
    token: int = 0
    if borrowed != 0:
        token = pcc_gc_root_copy_borrowed_lease(ptr_add(slots, index * C_POINTER_SIZE), source)
    else:
        token = pcc_gc_root_copy_lease(ptr_add(slots, index * C_POINTER_SIZE), source)
    if token < 0:
        return _builtin_init_error(cstr("initializer source transfer failed"))
    store_i64(tokens, index * C_POINTER_SIZE, token)
    return 0


def _builtin_init_adopt(slots, tokens, index: int) -> int:
    slot = ptr_add(slots, index * C_POINTER_SIZE)
    if ptr_is_null(load_ptr(slot, 0)) != 0:
        return _builtin_init_error(cstr("initializer allocation returned NULL without an exception"))
    token: int = pcc_gc_foreign_lease_acquire(slot)
    if token < 0:
        return _builtin_init_error(cstr("initializer result lease failed"))
    store_i64(tokens, index * C_POINTER_SIZE, token)
    pcc_py_gc_minor_graph_lock()
    pcc_gc_note_slot_write_barrier(null(), slot, load_ptr(slot, 0))
    pcc_py_gc_minor_graph_unlock()
    return -1 if py_err_occurred() != 0 else 0


def _builtin_init_drop(slots, tokens, index: int) -> None:
    slot = ptr_add(slots, index * C_POINTER_SIZE)
    if pcc_gc_foreign_lease_release(slot, load_i64(tokens, index * C_POINTER_SIZE)) != 0:
        pcc_platform_abort()
        return
    store_i64(tokens, index * C_POINTER_SIZE, 0)
    pcc_gc_store_root(slot, null())


def _builtin_init_close(slots, tokens, handles, count: int, suspended: int) -> None:
    if suspended != 0:
        py_tls_exc_swap_slot(ptr_add(slots, _BUILTIN_INIT_ERROR_SLOT * C_POINTER_SIZE))
    index: int = _BUILTIN_INIT_TEMP_SLOT
    while index > 0:
        _builtin_init_drop(slots, tokens, index)
        index = index - 1
    if suspended != 0:
        py_clear_exception()
        if ptr_is_null(load_ptr(slots, _BUILTIN_INIT_ERROR_SLOT * C_POINTER_SIZE)) == 0:
            pcc_gc_store_root(slots, null())
            py_clear_exception()
            py_tls_exc_swap_slot(ptr_add(slots, _BUILTIN_INIT_ERROR_SLOT * C_POINTER_SIZE))
        else:
            py_tls_exc_swap_slot(slots)
    index = 0
    while index < count:
        pcc_gc_scheduler_root_unregister_handle(load_ptr(handles, index * C_POINTER_SIZE))
        index = index + 1


def _builtin_init_env(slots, tokens) -> int:
    count: int = load_i32(load_ptr(slots, _BUILTIN_INIT_CLASS_SLOT * C_POINTER_SIZE), PYCLASSOBJECT_N_FIELDS_OFFSET)
    if count < 0:
        count = 0
    offset: int = PYINSTANCEOBJECT_FIELDS_OFFSET + count * C_POINTER_SIZE
    if _builtin_init_copy(slots, tokens, _BUILTIN_INIT_ENV_SLOT,
            ptr_add(load_ptr(slots, _BUILTIN_INIT_RECEIVER_SLOT * C_POINTER_SIZE), offset), 0) != 0:
        return -1
    if ptr_is_null(load_ptr(slots, _BUILTIN_INIT_ENV_SLOT * C_POINTER_SIZE)) == 0:
        return 0
    store_ptr(slots, _BUILTIN_INIT_TEMP_SLOT * C_POINTER_SIZE, py_dict_new())
    if _builtin_init_adopt(slots, tokens, _BUILTIN_INIT_TEMP_SLOT) != 0:
        return -1
    plan = stack_alloc(128)
    pcc_gc_store_ptr_plan_init(plan, load_ptr(slots, _BUILTIN_INIT_RECEIVER_SLOT * C_POINTER_SIZE), pcc_gc_backend())
    committed: int = 1
    pcc_py_gc_minor_graph_lock()
    receiver = load_ptr(slots, _BUILTIN_INIT_RECEIVER_SLOT * C_POINTER_SIZE)
    field = ptr_add(receiver, offset)
    # Allocation may reenter and create an environment. Never replace it.
    if ptr_is_null(pcc_gc_resolve_root_slot_unlocked(field, 0)) != 0:
        committed = pcc_gc_store_ptr_plan_commit_locked(plan, receiver, field,
            load_ptr(slots, _BUILTIN_INIT_TEMP_SLOT * C_POINTER_SIZE))
    pcc_py_gc_minor_graph_unlock()
    pcc_gc_store_ptr_plan_finish(plan)
    _builtin_init_drop(slots, tokens, _BUILTIN_INIT_TEMP_SLOT)
    if committed == 0:
        return _builtin_init_error(cstr("initializer environment publication failed"))
    return _builtin_init_copy(slots, tokens, _BUILTIN_INIT_ENV_SLOT,
        ptr_add(load_ptr(slots, _BUILTIN_INIT_RECEIVER_SLOT * C_POINTER_SIZE), offset), 0)


def _builtin_init_dict(slots, tokens) -> int:
    nargs: int = py_tuple_len(load_ptr(slots, _BUILTIN_INIT_ARGS_SLOT * C_POINTER_SIZE))
    if nargs > 1:
        py_raise_owned(py_exc_new(3, cstr("dict expected at most 1 argument")))
        return -1
    if (load_i32(load_ptr(slots, _BUILTIN_INIT_CLASS_SLOT * C_POINTER_SIZE), PYOBJECTHEADER_FLAGS_OFFSET) & 4) == 0:
        py_raise_owned(py_exc_new(3, cstr("dict.__init__ requires a dict instance")))
        return -1
    if _builtin_init_env(slots, tokens) != 0:
        return -1
    store_ptr(slots, _BUILTIN_INIT_KEY_SLOT * C_POINTER_SIZE, _dict_items_key())
    if _builtin_init_adopt(slots, tokens, _BUILTIN_INIT_KEY_SLOT) != 0:
        return -1
    store_ptr(slots, _BUILTIN_INIT_TEMP_SLOT * C_POINTER_SIZE, py_dict_new())
    if _builtin_init_adopt(slots, tokens, _BUILTIN_INIT_TEMP_SLOT) != 0:
        return -1
    if py_dict_setdefault_slots(ptr_add(slots, _BUILTIN_INIT_ENV_SLOT * C_POINTER_SIZE),
            ptr_add(slots, _BUILTIN_INIT_KEY_SLOT * C_POINTER_SIZE), ptr_add(slots, _BUILTIN_INIT_TEMP_SLOT * C_POINTER_SIZE),
            ptr_add(slots, _BUILTIN_INIT_DICT_SLOT * C_POINTER_SIZE)) != 0:
        return -1
    if _builtin_init_adopt(slots, tokens, _BUILTIN_INIT_DICT_SLOT) != 0:
        return -1
    _builtin_init_drop(slots, tokens, _BUILTIN_INIT_TEMP_SLOT)
    if nargs == 1:
        if _builtin_init_copy(slots, tokens, _BUILTIN_INIT_SOURCE_SLOT,
                ptr_add(load_ptr(slots, _BUILTIN_INIT_ARGS_SLOT * C_POINTER_SIZE), PYTUPLEOBJECT_ITEMS_OFFSET), 0) != 0:
            return -1
        if py_dict_update_slots(ptr_add(slots, _BUILTIN_INIT_DICT_SLOT * C_POINTER_SIZE),
                                ptr_add(slots, _BUILTIN_INIT_SOURCE_SLOT * C_POINTER_SIZE)) != 0:
            return -1
    kwargs = load_ptr(slots, _BUILTIN_INIT_KWARGS_SLOT * C_POINTER_SIZE)
    if ptr_is_null(kwargs) == 0 and _type_of(kwargs) != PY_TYPE_NONE:
        return py_dict_update_slots(ptr_add(slots, _BUILTIN_INIT_DICT_SLOT * C_POINTER_SIZE),
                                    ptr_add(slots, _BUILTIN_INIT_KWARGS_SLOT * C_POINTER_SIZE))
    return 0


def _builtin_init_exception(slots, tokens) -> int:
    kwargs = load_ptr(slots, _BUILTIN_INIT_KWARGS_SLOT * C_POINTER_SIZE)
    if ptr_is_null(kwargs) == 0 and _type_of(kwargs) != PY_TYPE_NONE:
        if py_dict_len(kwargs) != 0:
            py_raise_owned(py_exc_new(3, cstr("BaseException.__init__ takes no keyword arguments")))
            return -1
    cls = load_ptr(slots, _BUILTIN_INIT_CLASS_SLOT * C_POINTER_SIZE)
    names = load_ptr(cls, PYCLASSOBJECT_FIELD_NAMES_OFFSET)
    count: int = load_i32(cls, PYCLASSOBJECT_N_FIELDS_OFFSET)
    index: int = 0
    while index < count:
        if _cstr_equal(load_ptr(names, index * C_POINTER_SIZE), cstr("args")) != 0:
            receiver = load_ptr(slots, _BUILTIN_INIT_RECEIVER_SLOT * C_POINTER_SIZE)
            pcc_gc_store_ptr(receiver,
                ptr_add(receiver, PYINSTANCEOBJECT_FIELDS_OFFSET + index * C_POINTER_SIZE),
                load_ptr(slots, _BUILTIN_INIT_ARGS_SLOT * C_POINTER_SIZE))
            return -1 if py_err_occurred() != 0 else 0
        index = index + 1
    if _builtin_init_env(slots, tokens) != 0:
        return -1
    store_ptr(slots, _BUILTIN_INIT_KEY_SLOT * C_POINTER_SIZE, py_str_new(cstr("args"), 4))
    if _builtin_init_adopt(slots, tokens, _BUILTIN_INIT_KEY_SLOT) != 0:
        return -1
    return py_dict_set_slots(ptr_add(slots, _BUILTIN_INIT_ENV_SLOT * C_POINTER_SIZE),
        ptr_add(slots, _BUILTIN_INIT_KEY_SLOT * C_POINTER_SIZE), ptr_add(slots, _BUILTIN_INIT_ARGS_SLOT * C_POINTER_SIZE))


def _builtin_init_validate(slots, tokens) -> int:
    if _is_user_instance(load_ptr(slots, _BUILTIN_INIT_RECEIVER_SLOT * C_POINTER_SIZE)) == 0:
        py_raise_owned(py_exc_new(3, cstr("builtin-base initializer requires an instance")))
        return -1
    if _builtin_init_copy(slots, tokens, _BUILTIN_INIT_CLASS_SLOT,
            ptr_add(load_ptr(slots, _BUILTIN_INIT_RECEIVER_SLOT * C_POINTER_SIZE), PYINSTANCEOBJECT_CLS_OFFSET), 1) != 0:
        return -1
    matches: int = py_obj_issubclass(load_ptr(slots, _BUILTIN_INIT_CLASS_SLOT * C_POINTER_SIZE), load_ptr(slots, _BUILTIN_INIT_FROM_CLASS_SLOT * C_POINTER_SIZE))
    if matches < 0 or py_err_occurred() != 0:
        return -1
    if matches == 0:
        py_raise_owned(py_exc_new(3, cstr("super(type, obj): obj must be an instance or subtype of type")))
        return -1
    return 0


@c_abi_export("py_builtin_super_validate_slots")
def py_builtin_super_validate_slots(receiver_slot, from_class_slot) -> int:
    # Attribute-call evaluation binds super before evaluating call operands.
    slots = stack_alloc(_BUILTIN_INIT_SLOT_COUNT * C_POINTER_SIZE)
    tokens = stack_alloc(_BUILTIN_INIT_SLOT_COUNT * C_POINTER_SIZE)
    handles = stack_alloc(_BUILTIN_INIT_SLOT_COUNT * C_POINTER_SIZE)
    count: int = _builtin_init_open(slots, tokens, handles)
    suspended: int = 0
    status: int = -1
    if count == _BUILTIN_INIT_SLOT_COUNT:
        py_tls_exc_swap_slot(slots)
        suspended = 1
        status = _builtin_init_copy(slots, tokens, _BUILTIN_INIT_RECEIVER_SLOT, receiver_slot, 0)
        if status == 0:
            status = _builtin_init_copy(slots, tokens, _BUILTIN_INIT_FROM_CLASS_SLOT, from_class_slot, 0)
        if status == 0:
            status = _builtin_init_validate(slots, tokens)
    if status != 0:
        _builtin_init_error(cstr("super binding failed without an exception"))
    _builtin_init_close(slots, tokens, handles, count, suspended)
    return status


def _builtin_init_dispatch(receiver_slot, from_slot, args_slot, kwargs_slot, kind: int) -> int:
    slots = stack_alloc(_BUILTIN_INIT_SLOT_COUNT * C_POINTER_SIZE)
    tokens = stack_alloc(_BUILTIN_INIT_SLOT_COUNT * C_POINTER_SIZE)
    handles = stack_alloc(_BUILTIN_INIT_SLOT_COUNT * C_POINTER_SIZE)
    count: int = _builtin_init_open(slots, tokens, handles)
    suspended: int = 0
    status: int = -1
    if count == _BUILTIN_INIT_SLOT_COUNT:
        py_tls_exc_swap_slot(slots)
        suspended = 1
        status = _builtin_init_copy(slots, tokens, _BUILTIN_INIT_RECEIVER_SLOT, receiver_slot, 0)
        if status == 0:
            status = _builtin_init_copy(slots, tokens, _BUILTIN_INIT_FROM_CLASS_SLOT, from_slot, 0)
        if status == 0:
            status = _builtin_init_copy(slots, tokens, _BUILTIN_INIT_ARGS_SLOT, args_slot, 0)
        if status == 0:
            status = _builtin_init_copy(slots, tokens, _BUILTIN_INIT_KWARGS_SLOT, kwargs_slot, 0)
        if status == 0:
            status = _builtin_init_validate(slots, tokens)
        if status == 0:
            args = load_ptr(slots, _BUILTIN_INIT_ARGS_SLOT * C_POINTER_SIZE)
            kwargs = load_ptr(slots, _BUILTIN_INIT_KWARGS_SLOT * C_POINTER_SIZE)
            if ptr_is_null(args) != 0 or _type_of(args) != PY_TYPE_TUPLE:
                status = _builtin_init_error(cstr("initializer args must be a tuple"))
            elif ptr_is_null(kwargs) == 0 and _type_of(kwargs) != PY_TYPE_NONE and _type_of(kwargs) != PY_TYPE_DICT:
                status = _builtin_init_error(cstr("initializer keywords must be a dict"))
        if status == 0:
            status = _builtin_init_dict(slots, tokens) if kind == 1 else _builtin_init_exception(slots, tokens)
    if status != 0:
        _builtin_init_error(cstr("builtin-base initialization failed without an exception"))
    _builtin_init_close(slots, tokens, handles, count, suspended)
    return status


@c_abi_export("py_dict_subclass_init_slots")
def py_dict_subclass_init_slots(receiver_slot, from_class_slot, args_slot, kwargs_slot) -> int:
    return _builtin_init_dispatch(receiver_slot, from_class_slot, args_slot, kwargs_slot, 1)


@c_abi_export("py_exception_subclass_init_slots")
def py_exception_subclass_init_slots(receiver_slot, from_class_slot, args_slot, kwargs_slot) -> int:
    return _builtin_init_dispatch(receiver_slot, from_class_slot, args_slot, kwargs_slot, 2)


# Index entry owns slot addresses, including across the compiled thread-entry
# poll before this function's body. Passing the address of a raw callee copy
# does not satisfy this ABI: both caller slots must already be registered.
# The older raw index/slice APIs above remain compatibility boundaries.
pcc_gc_root_move = extern("pcc_gc_root_move", (c_ptr, c_ptr), c_int64)
py_obj_special_call_slots = extern("py_obj_special_call_slots", (c_ptr, c_ptr, c_ptr, c_ptr, c_ptr, c_ptr), c_int64)


# Scratch indices shared by the root, lease-token and registration arrays.
# Slot zero saves entry TLS; the final slot preserves an error during cleanup.
_INDEX_OLD_EXCEPTION_SLOT = 0
_INDEX_RECEIVER_SLOT = 1
_INDEX_RESULT_SLOT = 2
_INDEX_ERROR_SLOT = 3
_INDEX_SLOT_COUNT = 4


def _index_slot_error(message) -> int:
    py_runtime_error_if_unset(cstr("slot-based index"), message)
    return -1


def _index_slot_open(slots, tokens, handles) -> int:
    # old TLS, receiver, integer result, new TLS. No input value is loaded
    # until every potentially parking registration has finished.
    memset(slots, 0, _INDEX_SLOT_COUNT * C_POINTER_SIZE)
    memset(tokens, 0, _INDEX_SLOT_COUNT * C_POINTER_SIZE)
    memset(handles, 0, _INDEX_SLOT_COUNT * C_POINTER_SIZE)
    count: int = 0
    while count < _INDEX_SLOT_COUNT:
        handle = pcc_gc_scheduler_root_register_handle(ptr_add(slots, count * C_POINTER_SIZE))
        if ptr_is_null(handle) != 0:
            return count
        store_ptr(handles, count * C_POINTER_SIZE, handle)
        count = count + 1
    return count


def _index_slot_copy(slots, tokens, index: int, source) -> int:
    token: int = pcc_gc_root_copy_lease(ptr_add(slots, index * C_POINTER_SIZE), source)
    if token < 0:
        return _index_slot_error(cstr("index source root copy failed"))
    store_i64(tokens, index * C_POINTER_SIZE, token)
    return 0


def _index_slot_adopt(slots, tokens) -> int:
    # A producer has already published its NEW result directly into this
    # registered empty root. Lease acquisition reloads it after any wait.
    slot = ptr_add(slots, _INDEX_RESULT_SLOT * C_POINTER_SIZE)
    token: int = pcc_gc_foreign_lease_acquire(slot)
    if token < 0:
        return _index_slot_error(cstr("index result lease acquisition failed"))
    store_i64(tokens, _INDEX_RESULT_SLOT * C_POINTER_SIZE, token)
    pcc_py_gc_minor_graph_lock()
    pcc_gc_note_slot_write_barrier(null(), slot, load_ptr(slot, 0))
    pcc_py_gc_minor_graph_unlock()
    return 0


def _index_slot_close(slots, tokens, handles, count: int, suspended: int) -> None:
    if suspended != 0:
        py_tls_exc_swap_slot(ptr_add(slots, _INDEX_ERROR_SLOT * C_POINTER_SIZE))
    index: int = _INDEX_RESULT_SLOT
    while index > 0:
        if index < count:
            slot = ptr_add(slots, index * C_POINTER_SIZE)
            if pcc_gc_foreign_lease_release(slot, load_i64(tokens, index * C_POINTER_SIZE)) != 0:
                pcc_platform_abort()
                return
            pcc_gc_store_root(slot, null())
        index = index - 1
    if suspended != 0:
        # A callback/validation error wins over old TLS and finalizer errors.
        # On success restore the entry exception after releasing operands.
        py_clear_exception()
        if ptr_is_null(load_ptr(slots, _INDEX_ERROR_SLOT * C_POINTER_SIZE)) == 0:
            pcc_gc_store_root(slots, null())
            py_clear_exception()
            py_tls_exc_swap_slot(ptr_add(slots, _INDEX_ERROR_SLOT * C_POINTER_SIZE))
        else:
            py_tls_exc_swap_slot(slots)
    index = 0
    while index < count:
        pcc_gc_scheduler_root_unregister_handle(load_ptr(handles, index * C_POINTER_SIZE))
        index = index + 1


def _index_slot_dispatch(slots, tokens) -> int:
    receiver_slot = ptr_add(slots, _INDEX_RECEIVER_SLOT * C_POINTER_SIZE)
    result_slot = ptr_add(slots, _INDEX_RESULT_SLOT * C_POINTER_SIZE)
    receiver = load_ptr(receiver_slot, 0)
    if ptr_is_null(receiver) != 0:
        py_raise_owned(py_exc_new(3, cstr("object cannot be interpreted as an integer")))
        return -1
    tag: int = _type_of(receiver)
    if tag == PY_TYPE_INT:
        return _index_slot_copy(slots, tokens, _INDEX_RESULT_SLOT, receiver_slot)
    if tag == PY_TYPE_BOOL:
        truth: int = ptr_eq(receiver, global_load_ptr("py_True"))
        store_ptr(result_slot, 0, py_int_from_i64(1 if truth != 0 else 0))
    elif pcc_capi_is_cext_type_tag(tag) != 0:
        # The input owns an independent address lease through the raw C-API
        # call; the NEW result's first operation is publication in its root.
        store_ptr(result_slot, 0, PyNumber_Index(load_ptr(receiver_slot, 0)))
    else:
        handled = stack_alloc(8)
        store_i64(handled, 0, 0)
        status: int = py_obj_special_call_slots(receiver_slot, cstr("__index__"), null(), null(), result_slot, handled)
        if status != 0:
            return -1
        if load_i64(handled, 0) == 0:
            py_raise_owned(py_exc_new(3, cstr("object cannot be interpreted as an integer")))
            return -1
    if _index_slot_adopt(slots, tokens) != 0:
        return -1
    if ptr_is_null(load_ptr(result_slot, 0)) != 0:
        return _index_slot_error(cstr("index callback returned NULL without an exception"))
    if py_err_occurred() != 0:
        return -1
    result_tag: int = _type_of(load_ptr(result_slot, 0))
    if result_tag == PY_TYPE_INT:
        return 0
    if result_tag == PY_TYPE_BOOL:
        # Preserve the existing explicit capability boundary: CPython emits
        # a DeprecationWarning here, and owned warning emission is not ready.
        py_raise_owned(py_exc_new(11, cstr("pcc operator.index: non-exact __index__ results require owned warning emission")))
        return -1
    py_raise_owned(py_exc_new(3, cstr("__index__ returned non-int")))
    return -1


@c_abi_export("py_obj_index_slots")
def py_obj_index_slots(receiver_slot, result_slot) -> int:
    """Publish one arbitrary-precision int owner; status 0/-1 is success/error.

    Receiver is an authoritative registered owning CALLER root. Result is a
    distinct, empty registered owning CALLER root. No raw operand may be
    materialized into a nominal local slot just before entering this helper.
    """
    if ptr_is_null(receiver_slot) != 0 or ptr_is_null(result_slot) != 0:
        return _index_slot_error(cstr("index requires source and output root slots"))
    slots = stack_alloc(_INDEX_SLOT_COUNT * C_POINTER_SIZE)
    tokens = stack_alloc(_INDEX_SLOT_COUNT * C_POINTER_SIZE)
    handles = stack_alloc(_INDEX_SLOT_COUNT * C_POINTER_SIZE)
    count: int = _index_slot_open(slots, tokens, handles)
    status: int = -1
    suspended: int = 0
    if count == _INDEX_SLOT_COUNT:
        py_tls_exc_swap_slot(slots)
        suspended = 1
        if ptr_eq(receiver_slot, result_slot) != 0 or ptr_is_null(load_ptr(result_slot, 0)) == 0:
            status = _index_slot_error(cstr("index result destination must be distinct and empty"))
        else:
            status = _index_slot_copy(slots, tokens, _INDEX_RECEIVER_SLOT, receiver_slot)
            if status == 0:
                status = _index_slot_dispatch(slots, tokens)
            if status == 0:
                status = pcc_gc_root_move(result_slot, ptr_add(slots, _INDEX_RESULT_SLOT * C_POINTER_SIZE))
                if status == 0:
                    token: int = load_i64(tokens, _INDEX_RESULT_SLOT * C_POINTER_SIZE)
                    store_i64(tokens, _INDEX_RESULT_SLOT * C_POINTER_SIZE, 0)
                    if pcc_gc_foreign_lease_release(result_slot, token) != 0:
                        pcc_platform_abort()
                        return -1
    else:
        _index_slot_error(cstr("index root registration failed"))
    if status != 0:
        _index_slot_error(cstr("index conversion failed without an exception"))
    _index_slot_close(slots, tokens, handles, count, suspended)
    return status


def _index_slot_checked(receiver_slot, container_index: int) -> int:
    if ptr_is_null(receiver_slot) != 0:
        _index_slot_error(cstr("checked index requires a source root slot"))
        return 0
    slots = stack_alloc(_INDEX_SLOT_COUNT * C_POINTER_SIZE)
    tokens = stack_alloc(_INDEX_SLOT_COUNT * C_POINTER_SIZE)
    handles = stack_alloc(_INDEX_SLOT_COUNT * C_POINTER_SIZE)
    count: int = _index_slot_open(slots, tokens, handles)
    status: int = -1
    suspended: int = 0
    result: int = 0
    if count == _INDEX_SLOT_COUNT:
        py_tls_exc_swap_slot(slots)
        suspended = 1
        status = _index_slot_copy(slots, tokens, _INDEX_RECEIVER_SLOT, receiver_slot)
        if status == 0:
            status = _index_slot_dispatch(slots, tokens)
        if status == 0:
            overflow = stack_alloc(4)
            store_i32(overflow, 0, 0)
            result = py_int_to_i64(load_ptr(slots, _INDEX_RESULT_SLOT * C_POINTER_SIZE), overflow)
            if load_i32(overflow, 0) != 0:
                if container_index != 0:
                    py_raise_owned(py_exc_new(5, cstr("cannot fit integer into an index-sized integer")))
                else:
                    py_raise_owned(py_exc_new(15, cstr("Python int too large to convert to platform index")))
                status = -1
            elif py_err_occurred() != 0:
                status = -1
    else:
        _index_slot_error(cstr("checked index root registration failed"))
    if status != 0:
        _index_slot_error(cstr("checked index conversion failed without an exception"))
        result = 0
    _index_slot_close(slots, tokens, handles, count, suspended)
    return result


@c_abi_export("py_index_i64_checked_slots")
def py_index_i64_checked_slots(receiver_slot) -> int:
    """Checked platform conversion from an authoritative owning CALLER root.

    Unlike slice saturation, conversion overflow raises OverflowError. The
    integer remains rooted and leased through conversion/errors/cleanup.
    """
    return _index_slot_checked(receiver_slot, 0)


@c_abi_export("py_obj_index_i64_slots")
def py_obj_index_i64_slots(receiver_slot) -> int:
    """Container index conversion; only integer narrowing raises IndexError.

    Callback exceptions, including OverflowError from __index__, propagate
    unchanged. This is not a saturating slice-bound conversion.
    """
    return _index_slot_checked(receiver_slot, 1)
