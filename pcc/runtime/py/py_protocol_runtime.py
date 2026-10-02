"""User data-model protocol dispatch authored in pcc-Python.

This owns the production ABI mirrored by ``src/py_protocol.c``: user dunder
dispatch, generic floor/in-place operators, and inherited dict-subclass
storage/methods.  The C source remains a host-C oracle.
"""

__pcc_runtime_port__ = True

from pcc.runtime.py.py_abi_constants import C_POINTER_SIZE, PYCLASSOBJECT_N_FIELDS_OFFSET, PYINSTANCEOBJECT_CLS_OFFSET, PYINSTANCEOBJECT_FIELDS_OFFSET, PYOBJECTHEADER_FLAGS_OFFSET, PY_TYPE_BOOL, PY_TYPE_COMPLEX, PY_TYPE_FLOAT, PY_TYPE_FUNC, PY_TYPE_INSTANCE, PY_TYPE_INT, PY_TYPE_NONE, PY_TYPE_TUPLE, PY_TYPE_USER_CLASS_START

from pcc.extern import c_abi_export, c_double, c_int32, c_int64, c_ptr, c_void, extern
from pcc.unsafe import (
    atomic_rmw_i32,
    call_ptr1,
    call_ptr2,
    call_ptr3,
    cstr,
    f64_div,
    f64_signbit,
    define_thread_local_i32,
    global_addr,
    global_load_ptr,
    is_tagged_int,
    load_i8,
    load_i32,
    load_i64,
    load_ptr,
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


def _call_unary(method, self_obj):
    # A missing method is the lookup sentinel consumed by the caller.  Once a
    # method was selected, every NULL is an error result.
    if ptr_is_null(method) != 0:
        return null()
    if _ptr_can_have_header(method) != 0 and _type_of(method) == PY_TYPE_FUNC:
        args = py_tuple_new(1)
        if ptr_is_null(args) != 0:
            return _protocol_require_result(
                null(),
                cstr("py_tuple_new"),
                cstr("user protocol argument tuple allocation failed"),
            )
        py_tuple_set_item(args, 0, self_obj)
        result = py_func_call(method, args)
        _protocol_require_result(
            result,
            cstr("user protocol call"),
            cstr("user protocol callback returned NULL without an exception"),
        )
        py_decref(args)
        return result
    return _protocol_require_result(
        call_ptr1(method, self_obj),
        cstr("user protocol call"),
        cstr("user protocol callback returned NULL without an exception"),
    )


def _call_binary(method, self_obj, arg):
    if ptr_is_null(method) != 0:
        return null()
    if _ptr_can_have_header(method) != 0 and _type_of(method) == PY_TYPE_FUNC:
        method_pin: int = load_i32(method, PYOBJECTHEADER_FLAGS_OFFSET) & 64
        pcc_gc_pin(method)
        method_slot = stack_alloc(C_POINTER_SIZE)
        store_ptr(method_slot, 0, null())
        method_handle = pcc_gc_scheduler_root_register_handle(method_slot)
        if ptr_is_null(method_handle) != 0:
            pcc_gc_unpin(method)
            if method_pin != 0:
                atomic_rmw_i32("or", method, PYOBJECTHEADER_FLAGS_OFFSET, 64, "relaxed")
            return _protocol_require_result(null(), cstr("user protocol call"), cstr("user method root registration failed"))
        pcc_gc_store_root(method_slot, method)
        args = py_tuple_new(2)
        if ptr_is_null(args) != 0:
            method = load_ptr(method_slot, 0)
            pcc_gc_unpin(method)
            if method_pin != 0:
                atomic_rmw_i32("or", method, PYOBJECTHEADER_FLAGS_OFFSET, 64, "relaxed")
            pcc_gc_store_root(method_slot, null())
            pcc_gc_scheduler_root_unregister_handle(method_handle)
            return _protocol_require_result(
                null(),
                cstr("py_tuple_new"),
                cstr("user protocol argument tuple allocation failed"),
            )
        pcc_gc_pin(args)
        py_tuple_set_item(args, 0, self_obj)
        py_tuple_set_item(args, 1, arg)
        result = py_func_call(load_ptr(method_slot, 0), args)
        prior_result_pin: int = 0
        if _ptr_can_have_header(result) != 0:
            prior_result_pin = load_i32(result, PYOBJECTHEADER_FLAGS_OFFSET) & 64
        if ptr_eq(result, args) != 0:
            prior_result_pin = 0
        if ptr_eq(result, load_ptr(method_slot, 0)) != 0:
            prior_result_pin = method_pin
        pcc_gc_pin(result)
        result_slot = stack_alloc(C_POINTER_SIZE)
        store_ptr(result_slot, 0, result)
        if ptr_is_null(result) != 0:
            _protocol_require_result(
                result,
                cstr("user protocol call"),
                cstr("user protocol callback returned NULL without an exception"),
            )
        pcc_gc_unpin(args)
        # The result can be the argument tuple. Restore its bit before tuple
        # cleanup; this single-bit restoration does not acquire another pin.
        if _ptr_can_have_header(result) != 0:
            atomic_rmw_i32("or", result, PYOBJECTHEADER_FLAGS_OFFSET, 64, "relaxed")
        py_decref(args)
        method = load_ptr(method_slot, 0)
        pcc_gc_unpin(method)
        if method_pin != 0:
            atomic_rmw_i32("or", method, PYOBJECTHEADER_FLAGS_OFFSET, 64, "relaxed")
        if _ptr_can_have_header(result) != 0:
            atomic_rmw_i32("or", result, PYOBJECTHEADER_FLAGS_OFFSET, 64, "relaxed")
        pcc_gc_store_root(method_slot, null())
        pcc_gc_scheduler_root_unregister_handle(method_handle)
        return pcc_gc_take_pinned_slot(result_slot, prior_result_pin)
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
    if (
        ptr_is_null(cls) != 0
        or (load_i32(cls, PYOBJECTHEADER_FLAGS_OFFSET) & 2) != 0
    ):
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
