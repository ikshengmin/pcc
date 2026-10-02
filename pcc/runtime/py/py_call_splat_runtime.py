"""pcc-Python helpers for ``*args``, ``**kwargs``, and ``zip(*rows)``."""

__pcc_runtime_port__ = True

from pcc.runtime.py.py_abi_constants import PY_TYPE_DICT, PY_TYPE_INT, PY_TYPE_LIST, PY_TYPE_STR, PY_TYPE_TUPLE

from pcc.extern import c_abi_export, c_int64, c_ptr, c_void, extern
from pcc.unsafe import cstr, global_load_ptr, is_tagged_int, load_i32, null, ptr_eq, ptr_is_null, strlen


py_tuple_new = extern("py_tuple_new", (c_int64,), c_ptr)
py_tuple_len = extern("py_tuple_len", (c_ptr,), c_int64)
py_tuple_get = extern("py_tuple_get", (c_ptr, c_int64), c_ptr)
py_tuple_set_item = extern("py_tuple_set_item", (c_ptr, c_int64, c_ptr), c_void)
py_list_new = extern("py_list_new", (c_int64,), c_ptr)
py_list_len = extern("py_list_len", (c_ptr,), c_int64)
py_list_get = extern("py_list_get", (c_ptr, c_int64), c_ptr)
py_list_append = extern("py_list_append", (c_ptr, c_ptr), c_void)
py_dict_new = extern("py_dict_new", (), c_ptr)
py_dict_update = extern("py_dict_update", (c_ptr, c_ptr), c_void)
py_dict_keys = extern("py_dict_keys", (c_ptr,), c_ptr)
py_dict_contains = extern("py_dict_contains", (c_ptr, c_ptr), c_int64)
py_dict_set = extern("py_dict_set", (c_ptr, c_ptr, c_ptr), c_void)
py_obj_getattr = extern("py_obj_getattr", (c_ptr, c_ptr), c_ptr)
py_obj_iter = extern("py_obj_iter", (c_ptr,), c_ptr)
py_obj_next = extern("py_obj_next", (c_ptr,), c_ptr)
py_obj_type_name = extern("py_obj_type_name", (c_ptr,), c_ptr)
py_str_new = extern("py_str_new", (c_ptr, c_int64), c_ptr)
py_str_concat = extern("py_str_concat", (c_ptr, c_ptr), c_ptr)
py_str_utf8 = extern("py_str_utf8", (c_ptr,), c_ptr)
py_str_eq = extern("py_str_eq", (c_ptr, c_ptr), c_int64)
py_obj_str = extern("py_obj_str", (c_ptr,), c_ptr)
py_obj_repr = extern("py_obj_repr", (c_ptr,), c_ptr)
py_err_occurred = extern("py_err_occurred", (), c_int64)
py_current_exception = extern("py_current_exception", (), c_ptr)
py_clear_exception = extern("py_clear_exception", (), c_void)
py_exc_matches = extern("py_exc_matches", (c_ptr, c_ptr), c_int64)
py_exc_builtin_class = extern("py_exc_builtin_class", (c_int64,), c_ptr)
py_obj_len = extern("py_obj_len", (c_ptr,), c_int64)
py_obj_getitem = extern("py_obj_getitem", (c_ptr, c_ptr), c_ptr)
py_obj_call = extern("py_obj_call", (c_ptr, c_ptr, c_ptr), c_ptr)
py_int_from_i64 = extern("py_int_from_i64", (c_int64,), c_ptr)
py_incref = extern("py_incref", (c_ptr,), c_void)
py_decref = extern("py_decref", (c_ptr,), c_void)
py_exc_new = extern("py_exc_new", (c_int64, c_ptr), c_ptr)
py_raise = extern("py_raise", (c_ptr,), c_void)
# py_raise increfs; a caller that created the exception must release it.
py_raise_owned = extern("py_raise_owned", (c_ptr,), c_void)
py_runtime_error_if_unset = extern(
    "py_runtime_error_if_unset", (c_ptr, c_ptr), c_ptr
)


def _require_result(result, helper_name, message):
    if ptr_is_null(result):
        py_runtime_error_if_unset(helper_name, message)
    return result


def _is_none(o) -> bool:
    return ptr_is_null(o) or ptr_eq(o, global_load_ptr("py_None"))


def _type_of(o) -> int:
    if ptr_is_null(o):
        return -1
    if is_tagged_int(o):
        return PY_TYPE_INT
    return load_i32(o, 8)


def _sequence_len(o) -> int:
    tag: int = _type_of(o)
    if tag == PY_TYPE_TUPLE:
        return py_tuple_len(o)
    if tag == PY_TYPE_LIST:
        return py_list_len(o)
    return -1


def _sequence_get(o, index: int):
    if _type_of(o) == PY_TYPE_TUPLE:
        return py_tuple_get(o, index)
    if _type_of(o) == PY_TYPE_LIST:
        return py_list_get(o, index)
    return null()


@c_abi_export("py_call_merge_posargs")
def py_call_merge_posargs(base_tuple, star_args):
    if _is_none(base_tuple):
        base_tuple = py_tuple_new(0)
        if ptr_is_null(base_tuple):
            return _require_result(
                null(),
                cstr("py_tuple_new"),
                cstr("call splat could not allocate the base argument tuple"),
            )
    elif _type_of(base_tuple) != PY_TYPE_TUPLE:
        py_raise_owned(py_exc_new(3, cstr("call args base must be tuple")))
        return null()
    else:
        py_incref(base_tuple)

    base_len: int = py_tuple_len(base_tuple)
    if _is_none(star_args):
        return base_tuple
    star_len: int = _sequence_len(star_args)
    if star_len < 0:
        py_decref(base_tuple)
        py_raise_owned(py_exc_new(3, cstr("*args must be tuple or list")))
        return null()

    out = py_tuple_new(base_len + star_len)
    if ptr_is_null(out):
        _require_result(
            null(),
            cstr("py_tuple_new"),
            cstr("call splat could not allocate the merged argument tuple"),
        )
        py_decref(base_tuple)
        return null()
    i: int = 0
    while i < base_len:
        item = py_tuple_get(base_tuple, i)
        if ptr_is_null(item):
            _require_result(
                null(),
                cstr("py_tuple_get"),
                cstr("call splat could not read a base positional argument"),
            )
            py_decref(out)
            py_decref(base_tuple)
            return null()
        py_tuple_set_item(out, i, item)
        py_decref(item)
        i = i + 1
    i = 0
    while i < star_len:
        item = _sequence_get(star_args, i)
        if ptr_is_null(item):
            _require_result(
                null(),
                cstr("pcc_sequence_get_for_splat"),
                cstr("call splat could not read a starred positional argument"),
            )
            py_decref(out)
            py_decref(base_tuple)
            return null()
        py_tuple_set_item(out, base_len + i, item)
        py_decref(item)
        i = i + 1
    py_decref(base_tuple)
    return out


@c_abi_export("py_zip_star")
def py_zip_star(rows):
    if _is_none(rows):
        return _require_result(
            py_list_new(0),
            cstr("py_list_new"),
            cstr("zip splat could not allocate its result list"),
        )
    nrows: int = _sequence_len(rows)
    if nrows < 0:
        py_raise_owned(py_exc_new(3, cstr("zip(*x): x must be a tuple or list")))
        return null()
    if nrows == 0:
        return _require_result(
            py_list_new(0),
            cstr("py_list_new"),
            cstr("zip splat could not allocate its empty result list"),
        )

    min_len: int = -1
    row_index: int = 0
    while row_index < nrows:
        row = _sequence_get(rows, row_index)
        if ptr_is_null(row):
            return _require_result(
                null(),
                cstr("pcc_sequence_get_for_splat"),
                cstr("zip splat could not read an input row"),
            )
        row_len: int = py_obj_len(row)
        if row_len < 0:
            _require_result(
                null(),
                cstr("py_obj_len"),
                cstr("zip splat row length failed without setting an exception"),
            )
            py_decref(row)
            return null()
        py_decref(row)
        if min_len < 0 or row_len < min_len:
            min_len = row_len
        row_index = row_index + 1
    if min_len < 0:
        min_len = 0

    out = py_list_new(0)
    if ptr_is_null(out):
        return _require_result(
            null(),
            cstr("py_list_new"),
            cstr("zip splat could not allocate its result list"),
        )
    column: int = 0
    while column < min_len:
        column_obj = py_int_from_i64(column)
        if ptr_is_null(column_obj):
            _require_result(
                null(),
                cstr("py_int_from_i64"),
                cstr("zip splat could not allocate a column index"),
            )
            py_decref(out)
            return null()
        tuple_obj = py_tuple_new(nrows)
        if ptr_is_null(tuple_obj):
            _require_result(
                null(),
                cstr("py_tuple_new"),
                cstr("zip splat could not allocate a result row"),
            )
            py_decref(column_obj)
            py_decref(out)
            return null()
        row_index = 0
        while row_index < nrows:
            row = _sequence_get(rows, row_index)
            if ptr_is_null(row):
                _require_result(
                    null(),
                    cstr("pcc_sequence_get_for_splat"),
                    cstr("zip splat could not reload an input row"),
                )
                py_decref(tuple_obj)
                py_decref(column_obj)
                py_decref(out)
                return null()
            element = py_obj_getitem(row, column_obj)
            if ptr_is_null(element):
                _require_result(
                    null(),
                    cstr("py_obj_getitem"),
                    cstr("zip splat element lookup failed without setting an exception"),
                )
                py_decref(row)
                py_decref(tuple_obj)
                py_decref(column_obj)
                py_decref(out)
                return null()
            py_decref(row)
            py_tuple_set_item(tuple_obj, row_index, element)
            py_decref(element)
            row_index = row_index + 1
        py_decref(column_obj)
        py_list_append(out, tuple_obj)
        py_decref(tuple_obj)
        column = column + 1
    return out


def _dict_clone(source):
    out = py_dict_new()
    if ptr_is_null(out):
        return _require_result(
            null(),
            cstr("py_dict_new"),
            cstr("call splat could not allocate the merged keyword dictionary"),
        )
    if not _is_none(source):
        if _type_of(source) != PY_TYPE_DICT:
            py_decref(out)
            py_raise_owned(py_exc_new(3, cstr("kwargs base must be dict")))
            return null()
        py_dict_update(out, source)
    return out


@c_abi_export("py_call_merge_kwargs")
def py_call_merge_kwargs(base_kwargs, star_kwargs):
    out = _dict_clone(base_kwargs)
    if ptr_is_null(out):
        return _require_result(
            null(),
            cstr("pcc_dict_clone"),
            cstr("call splat could not clone its keyword dictionary"),
        )
    if _is_none(star_kwargs):
        return out
    if _type_of(star_kwargs) != PY_TYPE_DICT:
        py_decref(out)
        py_raise_owned(py_exc_new(3, cstr("**kwargs must be dict")))
        return null()
    py_dict_update(out, star_kwargs)
    return out


def _cat_owned(acc, piece):
    if ptr_is_null(acc) or ptr_is_null(piece):
        if not ptr_is_null(acc):
            py_decref(acc)
        if not ptr_is_null(piece):
            py_decref(piece)
        return null()
    out = py_str_concat(acc, piece)
    py_decref(acc)
    py_decref(piece)
    return out


def _raise_type_error_text(text) -> None:
    if not ptr_is_null(text):
        py_raise_owned(py_exc_new(3, py_str_utf8(text)))  # 3 == PY_EXC_TYPEERROR
        py_decref(text)


def _call_lit(text):
    return py_str_new(text, strlen(text))


def _call_optional_attr(callable_obj, name):
    value = py_obj_getattr(callable_obj, name)
    if ptr_is_null(value) and py_err_occurred() != 0:
        if py_exc_matches(py_current_exception(), py_exc_builtin_class(6)) != 0:
            py_clear_exception()
    return value


def _call_error_prefix(callable_obj):
    """NEW diagnostic prefix; called only after a keyword collision.

    The defining callable supplies its identity, never the importing caller.
    Objects without a qualname use their repr, like CPython's function display.
    """
    if ptr_is_null(callable_obj):
        return _call_lit(cstr(""))
    qualname = _call_optional_attr(callable_obj, cstr("__qualname__"))
    if ptr_is_null(qualname):
        if py_err_occurred() != 0:
            return null()
        return _cat_owned(py_obj_repr(callable_obj), _call_lit(cstr(" ")))
    module = _call_optional_attr(callable_obj, cstr("__module__"))
    if ptr_is_null(module) and py_err_occurred() != 0:
        py_decref(qualname)
        return null()
    name = py_obj_str(qualname)
    py_decref(qualname)
    if not _is_none(module):
        builtins = _call_lit(cstr("builtins"))
        is_builtin: int = 0
        if _type_of(module) == PY_TYPE_STR and not ptr_is_null(builtins):
            is_builtin = py_str_eq(module, builtins)
        py_decref(builtins)
        if is_builtin == 0:
            name = _cat_owned(_cat_owned(py_obj_str(module), _call_lit(cstr("."))), name)
    if not ptr_is_null(module):
        py_decref(module)
    return _cat_owned(name, _call_lit(cstr("() ")))


def _check_keyword_unique(out, key, callable_obj) -> int:
    """Check before fetching a mapping value; non-string keys bind later."""
    if py_dict_contains(out, key) != 0:
        text = _call_error_prefix(callable_obj)
        text = _cat_owned(text, _call_lit(cstr("got multiple values for keyword argument '")))
        text = _cat_owned(text, py_obj_str(key))
        text = _cat_owned(text, _call_lit(cstr("'")))
        _raise_type_error_text(text)
        return -1
    if py_err_occurred() != 0:
        return -1
    return 0


@c_abi_export("py_call_merge_kwargs_unique")
def py_call_merge_kwargs_unique(base_kwargs, star_kwargs):
    """Compatibility merge without callable-qualified error context."""
    return py_call_merge_kwargs_for_call(base_kwargs, star_kwargs, null())


@c_abi_export("py_call_merge_kwargs_for_call")
def py_call_merge_kwargs_for_call(base_kwargs, star_kwargs, callable_obj):
    """``f(**a, **b)``: merge ``b`` into ``a`` with the identity of ``f``.

    A key already present is a TypeError, as in CPython (``py_call_merge_kwargs``
    updates silently). Non-string keys survive merging until call binding so
    later operands still execute. A non-mapping operand stops evaluation with
    CPython 3.15's unqualified diagnostic. NEW reference, NULL on failure.
    """
    out = _dict_clone(base_kwargs)
    if ptr_is_null(out):
        return null()
    if _is_none(star_kwargs):
        text = _call_lit(cstr("Value after ** must be a mapping, not NoneType"))
        _raise_type_error_text(text)
        py_decref(out)
        return null()
    keys = null()
    if _type_of(star_kwargs) == PY_TYPE_DICT:
        keys = py_dict_keys(star_kwargs)
    else:
        keys_method = py_obj_getattr(star_kwargs, cstr("keys"))
        if ptr_is_null(keys_method):
            if py_err_occurred() != 0:
                if py_exc_matches(py_current_exception(), py_exc_builtin_class(6)) == 0:
                    py_decref(out)
                    return null()
            py_clear_exception()
            text = _call_lit(cstr("Value after ** must be a mapping, not "))
            text = _cat_owned(text, py_obj_type_name(star_kwargs))
            _raise_type_error_text(text)
            py_decref(out)
            return null()
        no_args = py_tuple_new(0)
        if ptr_is_null(no_args):
            py_decref(keys_method)
            py_decref(out)
            return null()
        keys = py_obj_call(keys_method, no_args, null())
        py_decref(no_args)
        py_decref(keys_method)
    if ptr_is_null(keys):
        py_decref(out)
        return null()
    it = py_obj_iter(keys)
    py_decref(keys)
    if ptr_is_null(it):
        py_decref(out)
        return null()
    while True:
        key = py_obj_next(it)
        if ptr_is_null(key):
            py_decref(it)
            if py_err_occurred() != 0:
                if py_exc_matches(py_current_exception(), py_exc_builtin_class(8)) == 0:
                    py_decref(out)
                    return null()
                py_clear_exception()
            return out
        if _check_keyword_unique(out, key, callable_obj) != 0:
            py_decref(key)
            py_decref(it)
            py_decref(out)
            return null()
        value = py_obj_getitem(star_kwargs, key)
        if ptr_is_null(value):
            py_decref(key)
            py_decref(it)
            py_decref(out)
            return null()
        py_dict_set(out, key, value)
        py_decref(key)
        py_decref(value)
        if py_err_occurred() != 0:
            py_decref(it)
            py_decref(out)
            return null()


@c_abi_export("py_obj_call_splat")
def py_obj_call_splat(callable_obj, base_args, star_args, base_kwargs, star_kwargs):
    args = py_call_merge_posargs(base_args, star_args)
    if ptr_is_null(args):
        return _require_result(
            null(),
            cstr("py_call_merge_posargs"),
            cstr("call splat could not merge positional arguments"),
        )
    kwargs = py_call_merge_kwargs(base_kwargs, star_kwargs)
    if ptr_is_null(kwargs):
        _require_result(
            null(),
            cstr("py_call_merge_kwargs"),
            cstr("call splat could not merge keyword arguments"),
        )
        py_decref(args)
        return null()
    out = py_obj_call(callable_obj, args, kwargs)
    if ptr_is_null(out):
        _require_result(
            null(),
            cstr("py_obj_call"),
            cstr("call splat callee returned NULL without setting an exception"),
        )
    py_decref(args)
    py_decref(kwargs)
    return out
