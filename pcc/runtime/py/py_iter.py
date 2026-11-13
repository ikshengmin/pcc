"""pcc-Python runtime ported from the removed C file py_iter.c.

Minimal native iterator wrapper for list / tuple / str / dict keys plus
dispatch to native generator objects.
"""

__pcc_runtime_port__ = True

from pcc.runtime.py.py_abi_constants import (
    C_POINTER_SIZE,
    PYINSTANCEOBJECT_CLS_OFFSET,
    PYOBJECTHEADER_FLAGS_OFFSET,
    PY_FLAG_GC_PINNED,
    PY_TYPE_BYTEARRAY,
    PY_TYPE_BYTES,
    PY_TYPE_DICT,
    PY_TYPE_FILE,
    PY_TYPE_GEN,
    PY_TYPE_INSTANCE,
    PY_TYPE_INT,
    PY_TYPE_ITER,
    PY_TYPE_LIST,
    PY_TYPE_MEMORYVIEW,
    PY_TYPE_SET,
    PY_TYPE_STR,
    PY_TYPE_TUPLE,
    PY_TYPE_USER_CLASS_START,
)

from pcc import i64
from pcc.extern import extern, c_abi_export, c_ptr, c_int32, c_int64, c_void
from pcc.unsafe import (
    cstr,
    define_global_i32,
    global_addr,
    free,
    global_load_ptr,
    is_tagged_int,
    load_i32,
    load_i64,
    load_i8,
    load_ptr,
    malloc,
    memset,
    null,
    ptr_add,
    ptr_is_null,
    stack_alloc,
    store_i32,
    store_i64,
    store_i8,
    store_ptr,
)

py_tuple_iter_next = extern("py_tuple_iter_next", (c_ptr, c_int64), c_ptr)
pcc_gc_frame_enter = extern("pcc_gc_frame_enter", (c_ptr, c_ptr), c_void)
pcc_gc_frame_leave = extern("pcc_gc_frame_leave", (c_ptr,), c_void)
pcc_gc_foreign_lease_acquire = extern("pcc_gc_foreign_lease_acquire", (c_ptr,), c_int64)
pcc_gc_foreign_lease_release = extern("pcc_gc_foreign_lease_release", (c_ptr, c_int64), c_int64)
pcc_gc_root_copy_borrowed_lease = extern("pcc_gc_root_copy_borrowed_lease", (c_ptr, c_ptr), c_int64)
pcc_gc_root_copy_lease = extern("pcc_gc_root_copy_lease", (c_ptr, c_ptr), c_int64)
pcc_gc_note_slot_write_barrier = extern("pcc_gc_note_slot_write_barrier", (c_ptr, c_ptr, c_ptr), c_void)
pcc_gc_store_root = extern("pcc_gc_store_root", (c_ptr, c_ptr), c_void)
pcc_gc_store_ptr = extern("pcc_gc_store_ptr", (c_ptr, c_ptr, c_ptr), c_void)
pcc_platform_abort = extern("pcc_platform_abort", (), c_void)
py_tls_exc_swap_slot = extern("py_tls_exc_swap_slot", (c_ptr,), c_void)

py_incref        = extern("py_incref",        (c_ptr,),                 c_void)
py_decref        = extern("py_decref",        (c_ptr,),                 c_void)
py_list_len      = extern("py_list_len",      (c_ptr,),                 c_int64)
py_list_get      = extern("py_list_get",      (c_ptr, c_int64),         c_ptr)
py_list_new      = extern("py_list_new",      (c_int64,),               c_ptr)
py_list_append   = extern("py_list_append",   (c_ptr, c_ptr),           c_void)
py_tuple_len     = extern("py_tuple_len",     (c_ptr,),                 c_int64)
py_tuple_get     = extern("py_tuple_get",     (c_ptr, c_int64),         c_ptr)
py_str_len       = extern("py_str_len",       (c_ptr,),                 c_int64)
py_str_byte_len  = extern("py_str_byte_len",  (c_ptr,),                 c_int64)
py_str_char_at_byte = extern("py_str_char_at_byte", (c_ptr, c_int64), c_ptr)
py_str_codepoint_byte_len = extern("py_str_codepoint_byte_len", (c_ptr, c_int64), c_int64)
py_bytes_len     = extern("py_bytes_len",     (c_ptr,),                 c_int64)
py_bytes_getitem = extern("py_bytes_getitem", (c_ptr, c_ptr),           c_ptr)
py_int_from_i64  = extern("py_int_from_i64",  (c_int64,),               c_ptr)
py_dict_keys     = extern("py_dict_keys",     (c_ptr,),                 c_ptr)
py_set_items     = extern("py_set_items",     (c_ptr,),                 c_ptr)
py_gen_next      = extern("py_gen_next",      (c_ptr,),                 c_ptr)
py_file_readline = extern("py_file_readline", (c_ptr, c_int64),         c_ptr)
py_tuple_new     = extern("py_tuple_new",     (c_int64,),               c_ptr)
py_tuple_set_item= extern("py_tuple_set_item",(c_ptr, c_int64, c_ptr),  c_void)
py_tuple_get     = extern("py_tuple_get",     (c_ptr, c_int64),         c_ptr)
py_obj_call      = extern("py_obj_call",      (c_ptr, c_ptr, c_ptr),    c_ptr)
py_obj_eq        = extern("py_obj_eq",        (c_ptr, c_ptr),           c_int64)
py_exc_new       = extern("py_exc_new",       (c_int64, c_ptr),         c_ptr)
py_err_occurred  = extern("py_err_occurred",  (),                       c_int64)
py_clear_exception = extern("py_clear_exception", (),                    c_void)
py_current_exception = extern("py_current_exception", (),                c_ptr)
py_exc_builtin_class = extern("py_exc_builtin_class", (c_int64,),         c_ptr)
py_exc_matches   = extern("py_exc_matches",   (c_ptr, c_ptr),           c_int64)
py_raise_owned   = extern("py_raise_owned",   (c_ptr,),                 c_void)
py_obj_type_name = extern("py_obj_type_name", (c_ptr,), c_ptr)
py_obj_getitem = extern("py_obj_getitem", (c_ptr, c_ptr), c_ptr)
py_class_lookup = extern("py_class_lookup", (c_ptr, c_ptr), c_ptr)
py_user_special_dispatch = extern(
    "py_user_special_dispatch",
    (c_ptr, c_ptr, c_ptr, c_ptr, c_int64, c_int64, c_ptr, c_ptr),
    c_ptr,
)
pcc_gc_note_root_slot_barrier = extern(
    "pcc_gc_note_root_slot_barrier", (c_ptr,), c_void
)
py_str_utf8 = extern("py_str_utf8", (c_ptr,), c_ptr)
py_gc_track      = extern("py_gc_track",      (c_ptr,),                 c_void)
pcc_gc_publish_initialized = extern(
    "pcc_gc_publish_initialized", (c_ptr,), c_void
)
pcc_gc_alloc     = extern("pcc_gc_alloc",     (c_int64, c_int32, c_int32), c_ptr)
pcc_gc_free_object_memory = extern(
    "pcc_gc_free_object_memory", (c_ptr,), c_void
)
pcc_gc_load_ptr  = extern("pcc_gc_load_ptr",  (c_ptr, c_ptr),           c_ptr)
pcc_gc_pin = extern("pcc_gc_pin", (c_ptr,), c_void)
pcc_gc_unpin = extern("pcc_gc_unpin", (c_ptr,), c_void)
pcc_gc_take_pinned_slot = extern("pcc_gc_take_pinned_slot", (c_ptr, c_int64), c_ptr)
pcc_py_gc_minor_graph_lock = extern("pcc_py_gc_minor_graph_lock", (), c_void)
pcc_py_gc_minor_graph_unlock = extern("pcc_py_gc_minor_graph_unlock", (), c_void)
py_user_iter_dispatch = extern("py_user_iter_dispatch", (c_ptr,),       c_ptr)
py_user_next_dispatch = extern("py_user_next_dispatch", (c_ptr,),       c_ptr)
pcc_capi_is_cext_type_tag = extern(
    "pcc_capi_is_cext_type_tag", (c_int64,), c_int64
)
pcc_capi_cext_object_iter = extern(
    "pcc_capi_cext_object_iter", (c_ptr,), c_ptr
)
pcc_capi_cext_object_next = extern(
    "pcc_capi_cext_object_next", (c_ptr,), c_ptr
)
py_runtime_error_if_unset = extern(
    "py_runtime_error_if_unset", (c_ptr, c_ptr), c_ptr
)


def _require_result(result, helper_name, message):
    if ptr_is_null(result):
        py_runtime_error_if_unset(helper_name, message)
    return result


def _type_of(obj) -> int:
    if is_tagged_int(obj):
        return PY_TYPE_INT
    return load_i32(obj, 8)


def _iter_new(seq):
    if ptr_is_null(seq):
        return null()
    it = pcc_gc_alloc(32, PY_TYPE_ITER, 0)
    if ptr_is_null(it):
        return null()
    py_incref(seq)
    store_ptr(it, 16, seq)
    store_i64(it, 24, 0)
    py_gc_track(it)
    pcc_gc_publish_initialized(it)
    return it


@c_abi_export("py_iter_callable_new")
def py_iter_callable_new(callable, sentinel):
    # iter(callable, sentinel): reuse PY_TYPE_ITER. The single ``seq`` slot
    # holds a 2-tuple (callable, sentinel); ``index`` is a negative state
    # discriminator that never collides with a sequence iterator's index.
    if ptr_is_null(callable) or ptr_is_null(sentinel):
        return _require_result(
            null(),
            cstr("py_iter_callable_new"),
            cstr("iter(callable, sentinel) received NULL operand"),
        )
    pair = py_tuple_new(2)
    if ptr_is_null(pair):
        return _require_result(
            null(),
            cstr("py_tuple_new"),
            cstr("iter(callable, sentinel) could not allocate its state tuple"),
        )
    py_tuple_set_item(pair, 0, callable)
    py_tuple_set_item(pair, 1, sentinel)
    it = pcc_gc_alloc(32, PY_TYPE_ITER, 0)
    if ptr_is_null(it):
        _require_result(
            null(),
            cstr("py_iter_callable_new"),
            cstr("iter(callable, sentinel) could not allocate iterator state"),
        )
        py_decref(pair)
        return null()
    store_ptr(it, 16, pair)  # owned reference; dealloc decrefs it
    store_i64(it, 24, -1)    # PY_ITER_CALLABLE_ACTIVE
    py_gc_track(it)
    pcc_gc_publish_initialized(it)
    return it


@c_abi_export("py_dealloc_iter")
def py_dealloc_iter(o) -> None:
    seq = pcc_gc_load_ptr(o, ptr_add(o, 16))
    if not ptr_is_null(seq):
        py_decref(seq)
    pcc_gc_free_object_memory(o)


@c_abi_export("py_obj_raise_not_iterable")
def py_obj_raise_not_iterable(o) -> None:
    """Raise iter(o)'s TypeError: "'int' object is not iterable"."""
    _raise_typed_object_error(o, cstr("' object is not iterable"))


def _raise_typed_object_error(o, suffix) -> None:
    """TypeError "'<type(o).__name__>" + suffix."""
    message = stack_alloc(160)
    store_i8(message, 0, 39)
    length: int = 1
    name = py_obj_type_name(o)
    if ptr_is_null(name) == 0:
        text = py_str_utf8(name)
        count: int = py_str_byte_len(name)
        if count > 100:
            count = 100
        index: int = 0
        while index < count:
            store_i8(message, length, load_i8(text, index))
            length = length + 1
            index = index + 1
        py_decref(name)
    offset: int = 0
    while load_i8(suffix, offset) != 0:
        store_i8(message, length, load_i8(suffix, offset))
        length = length + 1
        offset = offset + 1
    store_i8(message, length, 0)
    py_raise_owned(py_exc_new(3, message))


@c_abi_export("py_obj_iter")
def py_obj_iter(o):
    return _obj_iter_body(o, 0)


@c_abi_export("py_obj_unpack_iter")
def py_obj_unpack_iter(o):
    """iter(o) for ``a, b = o``: a type with neither __iter__ nor the
    sequence protocol raises CPython's "cannot unpack non-iterable int
    object"; an error raised by an __iter__ itself passes through."""
    return _obj_iter_body(o, 1)


@c_abi_export("py_obj_unpack_ex")
def py_obj_unpack_ex(o, before: int, after: int):
    """CPython's starred unpacking (UNPACK_EX) of ``o``.

    Returns one list of ``before + 1 + after`` items (NEW): the leading
    items, a list of the middle ones, then the trailing items; or NULL with
    the exception set. A non-iterable operand raises "cannot unpack
    non-iterable int object", too few items ValueError "not enough values to
    unpack (expected at least 2, got 1)".
    """
    it = py_obj_unpack_iter(o)
    if ptr_is_null(it) != 0:
        return null()
    items = py_list_new(0)
    if ptr_is_null(items) != 0:
        py_decref(it)
        return null()
    done: int = 0
    while done == 0:
        item = py_obj_iter_next(it)
        if ptr_is_null(item) != 0:
            done = 1
        else:
            py_list_append(items, item)
            py_decref(item)
            if py_err_occurred() != 0:
                done = 2
    py_decref(it)
    if done == 2 or _unpack_iteration_failed() != 0:
        py_decref(items)
        return null()
    count: int = py_list_len(items)
    if count < before + after:
        _raise_not_enough_values(before + after, count)
        py_decref(items)
        return null()
    out = py_list_new(0)
    middle = py_list_new(0)
    if ptr_is_null(out) != 0 or ptr_is_null(middle) != 0:
        if ptr_is_null(out) == 0:
            py_decref(out)
        if ptr_is_null(middle) == 0:
            py_decref(middle)
        py_decref(items)
        return null()
    index: int = 0
    while index < count:
        item = py_list_get(items, index)
        if index >= before and index < count - after:
            py_list_append(middle, item)
        else:
            if index == count - after:
                py_list_append(out, middle)
            py_list_append(out, item)
        py_decref(item)
        index = index + 1
    if after == 0:
        py_list_append(out, middle)
    py_decref(middle)
    py_decref(items)
    if py_err_occurred() != 0:
        py_decref(out)
        return null()
    return out


def _unpack_iteration_failed() -> int:
    """1 when iteration ended on an exception other than StopIteration."""
    current = py_current_exception()
    if ptr_is_null(current) != 0:
        return 0
    if py_exc_matches(current, py_exc_builtin_class(8)) != 0:  # StopIteration
        py_clear_exception()
        return 0
    return 1


def _raise_not_enough_values(expected: int, got: int) -> None:
    """ValueError "not enough values to unpack (expected at least N, got M)"."""
    message = stack_alloc(96)
    length: int = _append_text(message, 0, cstr("not enough values to unpack (expected at least "))
    length = _append_decimal(message, length, expected)
    length = _append_text(message, length, cstr(", got "))
    length = _append_decimal(message, length, got)
    length = _append_text(message, length, cstr(")"))
    store_i8(message, length, 0)
    py_raise_owned(py_exc_new(2, message))


def _append_text(message, length: int, text) -> int:
    offset: int = 0
    while load_i8(text, offset) != 0:
        store_i8(message, length + offset, load_i8(text, offset))
        offset = offset + 1
    return length + offset


def _append_decimal(message, length: int, value: int) -> int:
    digits = stack_alloc(24)
    count: int = 0
    rest: int = value
    if rest == 0:
        store_i8(digits, 0, 48)
        count = 1
    while rest > 0:
        store_i8(digits, count, 48 + rest % 10)
        rest = rest // 10
        count = count + 1
    index: int = 0
    while index < count:
        store_i8(message, length + index, load_i8(digits, count - 1 - index))
        index = index + 1
    return length + count


def _obj_iter_body(o, unpacking: int):
    if ptr_is_null(o):
        return _require_result(
            null(),
            cstr("py_obj_iter"),
            cstr("py_obj_iter received NULL object"),
        )
    tag: int = _type_of(o)
    if (
        tag == PY_TYPE_ITER
        or tag == PY_TYPE_GEN
        or tag == PY_TYPE_FILE
    ):
        py_incref(o)
        return o
    if (
        tag == PY_TYPE_LIST
        or tag == PY_TYPE_TUPLE
        or tag == PY_TYPE_STR
        or tag == PY_TYPE_BYTES
        or tag == PY_TYPE_BYTEARRAY
        or tag == PY_TYPE_MEMORYVIEW
    ):                                      # list / tuple / str / bytes-like
        return _require_result(
            _iter_new(o),
            cstr("py_iter_new"),
            cstr("sequence iterator allocation failed without setting an exception"),
        )
    if tag == PY_TYPE_DICT:                          # dict -> keys iterator
        keys = py_dict_keys(o)
        if ptr_is_null(keys):
            return _require_result(
                null(),
                cstr("py_dict_keys"),
                cstr("dictionary iterator snapshot failed without setting an exception"),
            )
        it = _iter_new(keys)
        if ptr_is_null(it):
            _require_result(
                null(),
                cstr("py_iter_new"),
                cstr("dictionary iterator allocation failed without setting an exception"),
            )
        py_decref(keys)
        return it
    if tag == PY_TYPE_SET:                          # set -> snapshot item list
        items = py_set_items(o)
        if ptr_is_null(items):
            return _require_result(
                null(),
                cstr("py_set_items"),
                cstr("set iterator snapshot failed without setting an exception"),
            )
        it = _iter_new(items)
        if ptr_is_null(it):
            _require_result(
                null(),
                cstr("py_iter_new"),
                cstr("set iterator allocation failed without setting an exception"),
            )
        py_decref(items)
        return it
    if pcc_capi_is_cext_type_tag(tag) != 0:
        it = pcc_capi_cext_object_iter(o)
        if ptr_is_null(it) == 0 or py_err_occurred() != 0:
            return it
    handled = stack_alloc(8)
    store_i64(handled, 0, 0)
    dunder = py_user_special_dispatch(o, cstr("__iter__"), null(), null(), 0, 0, null(), handled)
    if not ptr_is_null(dunder) or py_err_occurred() != 0:
        return dunder
    if load_i64(handled, 0) == 0 and _iter_type_has_getitem(o) != 0:
        # CPython's sequence protocol: a type with __getitem__ and no
        # __iter__ iterates o[0], o[1], ... until IndexError/StopIteration.
        return _require_result(
            _iter_new(o),
            cstr("py_obj_iter"),
            cstr("sequence iterator allocation failed without an exception"),
        )
    if unpacking != 0:
        _raise_cannot_unpack(o)
    else:
        py_obj_raise_not_iterable(o)
    return null()


def _raise_cannot_unpack(o) -> None:
    """TypeError "cannot unpack non-iterable <type(o).__name__> object"."""
    prefix = cstr("cannot unpack non-iterable ")
    message = stack_alloc(160)
    length: int = 0
    while load_i8(prefix, length) != 0:
        store_i8(message, length, load_i8(prefix, length))
        length = length + 1
    name = py_obj_type_name(o)
    if ptr_is_null(name) == 0:
        text = py_str_utf8(name)
        count: int = py_str_byte_len(name)
        if count > 100:
            count = 100
        index: int = 0
        while index < count:
            store_i8(message, length, load_i8(text, index))
            length = length + 1
            index = index + 1
        py_decref(name)
    suffix = cstr(" object")
    offset: int = 0
    while load_i8(suffix, offset) != 0:
        store_i8(message, length, load_i8(suffix, offset))
        length = length + 1
        offset = offset + 1
    store_i8(message, length, 0)
    py_raise_owned(py_exc_new(3, message))


def _iter_type_has_getitem(o) -> int:
    """1 when type(o) is a user class whose MRO defines __getitem__."""
    if ptr_is_null(o) != 0 or is_tagged_int(o) != 0:
        return 0
    tag: int = load_i32(o, 8)
    if tag != PY_TYPE_INSTANCE and tag < PY_TYPE_USER_CLASS_START:
        return 0
    if pcc_capi_is_cext_type_tag(tag) != 0:
        return 0
    cls = pcc_gc_load_ptr(o, ptr_add(o, PYINSTANCEOBJECT_CLS_OFFSET))
    if ptr_is_null(cls) != 0:
        return 0
    return 0 if ptr_is_null(py_class_lookup(cls, cstr("__getitem__"))) != 0 else 1


@c_abi_export("py_obj_next")
def py_obj_next(it_obj):
    return _obj_next_body(it_obj, 0)


@c_abi_export("py_obj_iter_next")
def py_obj_iter_next(it_obj):
    """Iteration consumer entry: NULL without an exception also means done.

    Native sequence exhaustion needs no StopIteration object. User next,
    generator and tuple-wrapper paths retain their existing exception result;
    consumers still match and consume StopIteration from those paths.
    Explicit next(it) keeps py_obj_next and its observable exception/value.
    """
    return _obj_next_body(it_obj, 1)


@c_abi_export("pcc_iter_exhausted_impl")
def _iter_exhausted(quiet: i64) -> None:
    if quiet != 0:
        # The old path selected StopIteration after retiring its temporaries.
        # Clear a cleanup callback's pending error just as consuming that
        # selecting exception would, rather than exposing a different error.
        py_clear_exception()
    else:
        py_raise_owned(py_exc_new(8, null()))


@c_abi_export("pcc_iter_next_impl")
def _obj_next_body(it_obj, quiet_exhaustion: i64):
    if not ptr_is_null(it_obj):
        if is_tagged_int(it_obj) == 0:
            tag: int = load_i32(it_obj, 8)
            if tag == PY_TYPE_GEN:      # PY_TYPE_GEN
                return _require_result(
                    py_gen_next(it_obj),
                    cstr("py_gen_next"),
                    cstr(
                        "generator next returned NULL without StopIteration or an exception"
                    ),
                )
            if tag == PY_TYPE_FILE:
                line = py_file_readline(it_obj, -1)
                if ptr_is_null(line):
                    return _require_result(
                        null(),
                        cstr("py_file_readline"),
                        cstr(
                            "file iterator readline returned NULL without an exception"
                        ),
                    )
                line_tag: int = _type_of(line)
                line_length: int = -1
                if line_tag == PY_TYPE_STR:
                    line_length = py_str_len(line)
                elif line_tag == PY_TYPE_BYTES:
                    line_length = py_bytes_len(line)
                if line_length == 0:
                    py_decref(line)
                    _iter_exhausted(quiet_exhaustion)
                    return null()
                if line_length < 0:
                    py_decref(line)
                    return _require_result(
                        null(),
                        cstr("py_file_readline"),
                        cstr("file iterator readline returned a non-line object"),
                    )
                return line
            if pcc_capi_is_cext_type_tag(tag) != 0:
                item = pcc_capi_cext_object_next(it_obj)
                if ptr_is_null(item) == 0 or py_err_occurred() != 0:
                    return item
    if ptr_is_null(it_obj) or _type_of(it_obj) != PY_TYPE_ITER:
        dunder = py_user_next_dispatch(it_obj)
        if not ptr_is_null(dunder) or py_err_occurred() != 0:
            return dunder
        _raise_typed_object_error(it_obj, cstr("' object is not an iterator"))
        return null()

    if load_i64(it_obj, 24) <= _TUPLE_ITER_START:
        return _tuple_iter_transaction(it_obj, 0)
    it_slot = stack_alloc(8)
    store_ptr(it_slot, 0, it_obj)
    it_obj = load_ptr(it_slot, 0)
    index: int = load_i64(it_obj, 24)
    if index < 0:
        # Callable-iterator: iter(callable, sentinel).
        if index == -2:                       # PY_ITER_CALLABLE_DONE
            _iter_exhausted(quiet_exhaustion)
            return null()
        pair = pcc_gc_load_ptr(it_obj, ptr_add(it_obj, 16))
        callable = py_tuple_get(pair, 0)
        if ptr_is_null(callable):
            return _require_result(
                null(),
                cstr("py_tuple_get"),
                cstr("callable iterator lost its callable"),
            )
        callable_slot = stack_alloc(8)
        store_ptr(callable_slot, 0, callable)
        it_obj = load_ptr(it_slot, 0)
        pair = pcc_gc_load_ptr(it_obj, ptr_add(it_obj, 16))
        sentinel = py_tuple_get(pair, 1)
        if ptr_is_null(sentinel):
            _require_result(
                null(),
                cstr("py_tuple_get"),
                cstr("callable iterator lost its sentinel"),
            )
            py_decref(load_ptr(callable_slot, 0))
            return null()
        sentinel_slot = stack_alloc(8)
        store_ptr(sentinel_slot, 0, sentinel)
        args = py_tuple_new(0)
        if ptr_is_null(args):
            _require_result(
                null(),
                cstr("py_tuple_new"),
                cstr("callable iterator could not allocate its argument tuple"),
            )
            py_decref(load_ptr(callable_slot, 0))
            py_decref(load_ptr(sentinel_slot, 0))
            return null()
        args_slot = stack_alloc(8)
        store_ptr(args_slot, 0, args)
        none_obj = global_load_ptr("py_None")
        result = py_obj_call(
            load_ptr(callable_slot, 0),
            load_ptr(args_slot, 0),
            none_obj,
        )
        result_slot = stack_alloc(8)
        store_ptr(result_slot, 0, result)
        if ptr_is_null(result):
            _require_result(
                null(),
                cstr("py_obj_call"),
                cstr("callable iterator returned NULL without setting an exception"),
            )
        py_decref(load_ptr(args_slot, 0))
        py_decref(load_ptr(callable_slot, 0))
        if ptr_is_null(result):
            py_decref(load_ptr(sentinel_slot, 0))
            return null()
        is_stop: int = py_obj_eq(
            load_ptr(result_slot, 0),
            load_ptr(sentinel_slot, 0),
        )
        had_error: int = py_err_occurred()
        py_decref(load_ptr(sentinel_slot, 0))
        if had_error != 0:
            py_decref(load_ptr(result_slot, 0))
            return null()
        if is_stop != 0:
            py_decref(load_ptr(result_slot, 0))
            it_obj = load_ptr(it_slot, 0)
            store_i64(it_obj, 24, -2)          # PY_ITER_CALLABLE_DONE
            _iter_exhausted(quiet_exhaustion)
            return null()
        return load_ptr(result_slot, 0)

    seq = pcc_gc_load_ptr(it_obj, ptr_add(it_obj, 16))
    if ptr_is_null(seq) != 0:
        # An exhausted sequence-protocol iterator stays exhausted.
        _iter_exhausted(quiet_exhaustion)  # StopIteration
        return null()
    tag: int = _type_of(seq)
    n: int = 0
    step: int = 1
    item = null()
    if tag == PY_TYPE_LIST:
        n = py_list_len(seq)
        if index >= n:
            _iter_exhausted(quiet_exhaustion)
            return null()
        it_obj = load_ptr(it_slot, 0)
        seq = pcc_gc_load_ptr(it_obj, ptr_add(it_obj, 16))
        item = py_list_get(seq, index)
    elif tag == PY_TYPE_TUPLE:
        n = py_tuple_len(seq)
        if index >= n:
            _iter_exhausted(quiet_exhaustion)
            return null()
        it_obj = load_ptr(it_slot, 0)
        seq = pcc_gc_load_ptr(it_obj, ptr_add(it_obj, 16))
        item = py_tuple_get(seq, index)
    elif tag == PY_TYPE_STR:
        # A str iterator's index is the byte offset of its next character.
        # Fetching by codepoint index rescanned a non-ASCII string from its
        # first byte for every item, which made iteration quadratic.
        n = py_str_byte_len(seq)
        if index >= n:
            _iter_exhausted(quiet_exhaustion)
            return null()
        it_obj = load_ptr(it_slot, 0)
        seq = pcc_gc_load_ptr(it_obj, ptr_add(it_obj, 16))
        step = py_str_codepoint_byte_len(seq, index)
        item = py_str_char_at_byte(seq, index)
    elif tag == PY_TYPE_BYTES or tag == PY_TYPE_BYTEARRAY or tag == PY_TYPE_MEMORYVIEW:
        n = py_bytes_len(seq)
        if index >= n:
            _iter_exhausted(quiet_exhaustion)
            return null()
        idx = py_int_from_i64(index)
        if ptr_is_null(idx):
            return _require_result(
                null(),
                cstr("py_int_from_i64"),
                cstr("bytes iterator could not allocate its index"),
            )
        it_obj = load_ptr(it_slot, 0)
        seq = pcc_gc_load_ptr(it_obj, ptr_add(it_obj, 16))
        item = py_bytes_getitem(seq, idx)
        py_decref(idx)
    else:
        # CPython's sequence iterator: seq[index], ending at IndexError or
        # StopIteration, after which the iterator drops its sequence.
        idx = py_int_from_i64(index)
        if ptr_is_null(idx):
            return _require_result(
                null(),
                cstr("py_int_from_i64"),
                cstr("sequence iterator could not allocate its index"),
            )
        it_obj = load_ptr(it_slot, 0)
        seq = pcc_gc_load_ptr(it_obj, ptr_add(it_obj, 16))
        item = py_obj_getitem(seq, idx)
        py_decref(idx)
        if ptr_is_null(item) and py_err_occurred() != 0:
            current = py_current_exception()
            if (py_exc_matches(current, py_exc_builtin_class(5)) != 0  # IndexError
                    or py_exc_matches(current, py_exc_builtin_class(8)) != 0):  # StopIteration
                py_clear_exception()
                it_obj = load_ptr(it_slot, 0)
                pcc_gc_store_ptr(it_obj, ptr_add(it_obj, 16), null())
                _iter_exhausted(quiet_exhaustion)  # StopIteration
                return null()
            return null()
    if ptr_is_null(item):
        return _require_result(
            null(),
            cstr("py_obj_next"),
            cstr("iterator element lookup returned NULL without setting an exception"),
        )
    item_slot = stack_alloc(8)
    store_ptr(item_slot, 0, item)
    it_obj = load_ptr(it_slot, 0)
    store_i64(it_obj, 24, index + step)
    return load_ptr(item_slot, 0)


@c_abi_export("py_enumerate_list")
def py_enumerate_list(iterable, start: int):
    it = py_obj_iter(iterable)
    if ptr_is_null(it) != 0:
        return null()
    it_slot = stack_alloc(8)
    item_slot = stack_alloc(8)
    index_slot = stack_alloc(8)
    store_ptr(it_slot, 0, it)
    store_ptr(item_slot, 0, null())
    store_ptr(index_slot, 0, null())
    out = py_list_new(4)
    if ptr_is_null(out) != 0:
        _require_result(
            null(),
            cstr("py_list_new"),
            cstr("enumerate could not allocate its result list"),
        )
        py_decref(load_ptr(it_slot, 0))
        return null()
    pcc_gc_pin(out)

    index: int = start
    done: int = 0
    while done == 0:
        item = py_obj_next(it)
        if ptr_is_null(item) != 0:
            if py_err_occurred() != 0:
                current = py_current_exception()
                stop = py_exc_builtin_class(8)  # PY_EXC_STOPITERATION
                if py_exc_matches(current, stop) != 0:
                    py_clear_exception()
                else:
                    py_decref(load_ptr(it_slot, 0))
                    pcc_gc_unpin(out)
                    py_decref(out)
                    return null()
            done = 1
        else:
            store_ptr(item_slot, 0, item)
            pair = py_tuple_new(2)
            if ptr_is_null(pair) != 0:
                _require_result(
                    null(),
                    cstr("py_tuple_new"),
                    cstr("enumerate could not allocate an output pair"),
                )
                py_decref(load_ptr(item_slot, 0))
                py_decref(load_ptr(it_slot, 0))
                pcc_gc_unpin(out)
                py_decref(out)
                return null()
            pcc_gc_pin(pair)
            index_obj = py_int_from_i64(index)
            if ptr_is_null(index_obj) != 0:
                _require_result(
                    null(),
                    cstr("py_int_from_i64"),
                    cstr("enumerate could not allocate an index object"),
                )
                py_decref(load_ptr(item_slot, 0))
                pcc_gc_unpin(pair)
                py_decref(pair)
                py_decref(load_ptr(it_slot, 0))
                pcc_gc_unpin(out)
                py_decref(out)
                return null()
            store_ptr(index_slot, 0, index_obj)
            index_obj = load_ptr(index_slot, 0)
            py_tuple_set_item(pair, 0, index_obj)
            py_decref(load_ptr(index_slot, 0))
            py_tuple_set_item(pair, 1, item)
            py_decref(load_ptr(item_slot, 0))
            py_list_append(out, pair)
            if py_err_occurred() != 0:
                pcc_gc_unpin(pair)
                py_decref(pair)
                py_decref(load_ptr(it_slot, 0))
                pcc_gc_unpin(out)
                py_decref(out)
                return null()
            pcc_gc_unpin(pair)
            py_decref(pair)
            index = index + 1

    py_decref(load_ptr(it_slot, 0))
    pcc_gc_unpin(out)
    return out


# Tuple-subclass iterators retain the wrapper, not only its private payload.
# Encode index i as -3-i so ordinary and callable iterators keep their layouts.
_TUPLE_ITER_START = -3
_TUPLE_ITER_OLD_ERROR = 0
_TUPLE_ITER_INPUT = 1
_TUPLE_ITER_WRAPPER = 2
_TUPLE_ITER_RESULT = 3
_TUPLE_ITER_ERROR = 4
_TUPLE_ITER_COUNT = 5

define_global_i32("pcc_tuple_iter_borrowed_map", -1)
define_global_i32("pcc_tuple_iter_owned_map", 5)


def _tuple_iter_adopt(slots, tokens, index: int) -> int:
    slot = ptr_add(slots, index * C_POINTER_SIZE)
    token: int = pcc_gc_foreign_lease_acquire(slot)
    if token < 0:
        py_runtime_error_if_unset(cstr("tuple iterator"), cstr("result owner lease failed"))
        return -1
    store_i64(tokens, index * C_POINTER_SIZE, token)
    pcc_gc_note_root_slot_barrier(slot)
    return 0


def _tuple_iter_drop(slots, tokens, index: int) -> None:
    slot = ptr_add(slots, index * C_POINTER_SIZE)
    if pcc_gc_foreign_lease_release(slot, load_i64(tokens, index * C_POINTER_SIZE)) != 0:
        pcc_platform_abort()
        return
    store_i64(tokens, index * C_POINTER_SIZE, 0)
    pcc_gc_store_root(slot, null())


def _tuple_iter_body(slots, tokens, borrowed, create: int) -> int:
    input_slot = ptr_add(slots, _TUPLE_ITER_INPUT * C_POINTER_SIZE)
    token: int = pcc_gc_root_copy_borrowed_lease(input_slot, borrowed)
    if token < 0:
        py_runtime_error_if_unset(cstr("tuple iterator"), cstr("input owner copy failed"))
        return -1
    store_i64(tokens, _TUPLE_ITER_INPUT * C_POINTER_SIZE, token)
    result_slot = ptr_add(slots, _TUPLE_ITER_RESULT * C_POINTER_SIZE)
    if create != 0:
        store_ptr(result_slot, 0, pcc_gc_alloc(32, PY_TYPE_ITER, 0))
        if ptr_is_null(load_ptr(result_slot, 0)) == 0:
            store_ptr(load_ptr(result_slot, 0), 16, null())
            store_i64(load_ptr(result_slot, 0), 24, _TUPLE_ITER_START)
        if _tuple_iter_adopt(slots, tokens, _TUPLE_ITER_RESULT) != 0:
            return -1
        if ptr_is_null(load_ptr(result_slot, 0)) != 0:
            py_runtime_error_if_unset(cstr("tuple iterator"), cstr("iterator allocation failed"))
            return -1
        iterator = load_ptr(result_slot, 0)
        pcc_gc_store_ptr(iterator, ptr_add(iterator, 16), load_ptr(input_slot, 0))
        py_gc_track(load_ptr(result_slot, 0))
        pcc_gc_publish_initialized(load_ptr(result_slot, 0))
        if py_err_occurred() != 0:
            return -1
        return 0
    iterator = load_ptr(input_slot, 0)
    wrapper_slot = ptr_add(slots, _TUPLE_ITER_WRAPPER * C_POINTER_SIZE)
    token = pcc_gc_root_copy_lease(wrapper_slot, ptr_add(iterator, 16))
    if token < 0:
        py_runtime_error_if_unset(cstr("tuple iterator"), cstr("wrapper owner copy failed"))
        return -1
    store_i64(tokens, _TUPLE_ITER_WRAPPER * C_POINTER_SIZE, token)
    if ptr_is_null(load_ptr(wrapper_slot, 0)) != 0:
        py_raise_owned(py_exc_new(8, null()))
        return -1
    index: int = _TUPLE_ITER_START - load_i64(load_ptr(input_slot, 0), 24)
    store_ptr(result_slot, 0, py_tuple_iter_next(load_ptr(wrapper_slot, 0), index))
    if _tuple_iter_adopt(slots, tokens, _TUPLE_ITER_RESULT) != 0:
        return -1
    if ptr_is_null(load_ptr(result_slot, 0)) != 0:
        py_runtime_error_if_unset(cstr("tuple iterator"), cstr("tuple next returned NULL without an exception"))
        return -1
    if py_err_occurred() != 0:
        return -1
    store_i64(load_ptr(input_slot, 0), 24, _TUPLE_ITER_START - index - 1)
    return 0


def _tuple_iter_transaction(value, create: int):
    # Raw entry requires a caller-owned address lease, as for the protocol
    # adapter. Every NEW item is published before any lease or cleanup call.
    borrowed = stack_alloc(C_POINTER_SIZE)
    store_ptr(borrowed, 0, value)
    pcc_gc_frame_enter(global_addr("pcc_tuple_iter_borrowed_map"), borrowed)
    slots = stack_alloc(_TUPLE_ITER_COUNT * C_POINTER_SIZE)
    tokens = stack_alloc(_TUPLE_ITER_COUNT * C_POINTER_SIZE)
    memset(slots, 0, _TUPLE_ITER_COUNT * C_POINTER_SIZE)
    memset(tokens, 0, _TUPLE_ITER_COUNT * C_POINTER_SIZE)
    pcc_gc_frame_enter(global_addr("pcc_tuple_iter_owned_map"), slots)
    py_tls_exc_swap_slot(ptr_add(slots, _TUPLE_ITER_OLD_ERROR * C_POINTER_SIZE))
    status: int = _tuple_iter_body(slots, tokens, borrowed, create)
    error_slot = ptr_add(slots, _TUPLE_ITER_ERROR * C_POINTER_SIZE)
    py_tls_exc_swap_slot(error_slot)
    store_ptr(borrowed, 0, null())
    if status != 0 and create == 0 and ptr_is_null(load_ptr(error_slot, 0)) == 0:
        if _tuple_iter_adopt(slots, tokens, _TUPLE_ITER_ERROR) == 0:
            stop_class = py_exc_builtin_class(8)
            stopped: int = py_exc_matches(load_ptr(error_slot, 0), stop_class)
            if stopped != 0:
                # The wrapper scratch owner keeps finalizers outside the heap
                # store. Repeated next sees NULL and raises StopIteration.
                iterator = load_ptr(slots, _TUPLE_ITER_INPUT * C_POINTER_SIZE)
                pcc_gc_store_ptr(iterator, ptr_add(iterator, 16), null())
    _tuple_iter_drop(slots, tokens, _TUPLE_ITER_WRAPPER)
    _tuple_iter_drop(slots, tokens, _TUPLE_ITER_INPUT)
    if status != 0:
        _tuple_iter_drop(slots, tokens, _TUPLE_ITER_RESULT)
    py_clear_exception()
    if ptr_is_null(load_ptr(error_slot, 0)) == 0:
        pcc_gc_store_root(ptr_add(slots, _TUPLE_ITER_OLD_ERROR * C_POINTER_SIZE), null())
        py_clear_exception()
        if pcc_gc_foreign_lease_release(error_slot, load_i64(tokens, _TUPLE_ITER_ERROR * C_POINTER_SIZE)) != 0:
            pcc_platform_abort()
            return null()
        py_tls_exc_swap_slot(error_slot)
    else:
        py_tls_exc_swap_slot(ptr_add(slots, _TUPLE_ITER_OLD_ERROR * C_POINTER_SIZE))
    result_slot = ptr_add(slots, _TUPLE_ITER_RESULT * C_POINTER_SIZE)
    pcc_py_gc_minor_graph_lock()
    result = pcc_gc_load_ptr(null(), result_slot)
    prior: int = 0
    if ptr_is_null(result) == 0 and is_tagged_int(result) == 0:
        prior = load_i32(result, PYOBJECTHEADER_FLAGS_OFFSET) & PY_FLAG_GC_PINNED
        pcc_gc_pin(result)
    pcc_py_gc_minor_graph_unlock()
    if pcc_gc_foreign_lease_release(result_slot, load_i64(tokens, _TUPLE_ITER_RESULT * C_POINTER_SIZE)) != 0:
        pcc_platform_abort()
        return null()
    pcc_gc_frame_leave(slots)
    pcc_gc_frame_leave(borrowed)
    return pcc_gc_take_pinned_slot(result_slot, prior)


@c_abi_export("py_tuple_iter_new")
def py_tuple_iter_new(wrapper):
    return _tuple_iter_transaction(wrapper, 1)
