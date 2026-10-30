"""pcc-Python port of py_print_fmt.c.

High-level print formatting for runtime PyObject* values. Native scalar
float printing is still emitted directly by codegen; this module covers
object-path print(), list/tuple repr, and print_many.
"""

__pcc_runtime_port__ = True

from pcc.extern import extern, c_abi_export, c_ptr, c_int32, c_int64, c_void
from pcc.runtime.py.py_abi_constants import PY_TYPE_ELLIPSIS, PY_TYPE_NOTIMPLEMENTED
from pcc.runtime.py.py_abi_constants import PY_TYPE_CONTINUATION, PY_TYPE_VIRTUAL_THREAD, PY_TYPE_VTHREAD_CHANNEL
from pcc.runtime.py.py_abi_constants import (
    PY_FLAG_EXC_UNICODE_PAYLOAD,
    PY_FLAG_EXC_OS_PAYLOAD,
    PY_TYPE_BOOL,
    PY_TYPE_BYTEARRAY,
    PY_TYPE_BYTES,
    PY_TYPE_COROUTINE,
    PY_TYPE_DICT,
    PY_TYPE_EXC,
    PY_TYPE_FLOAT,
    PY_TYPE_INSTANCE,
    PY_TYPE_INT,
    PY_TYPE_LIST,
    PY_TYPE_NONE,
    PY_TYPE_SET,
    PY_TYPE_STR,
    PY_TYPE_TUPLE,
    PY_TYPE_USER_CLASS_START,
)
from pcc.unsafe import (
    cstr,
    define_thread_local_ptr_null,
    free,
    global_load_ptr,
    global_store_ptr,
    is_tagged_int,
    load_i8,
    load_i32,
    load_i64,
    load_ptr,
    memcpy,
    null,
    ptr_add,
    ptr_eq,
    ptr_is_null,
    stack_alloc,
    store_i8,
    store_i64,
    store_ptr,
    strlen,
    write,
)


py_int_value_i64 = extern("py_int_value_i64", (c_ptr,), c_int64)
py_bigint_to_cstr = extern("py_bigint_to_cstr", (c_ptr,), c_ptr)
py_float_format_fixed = extern("py_float_format_fixed", (c_ptr, c_int64), c_ptr)
py_float_repr_shortest = extern("py_float_repr_shortest", (c_ptr,), c_ptr)
pcc_gc_load_ptr = extern("pcc_gc_load_ptr", (c_ptr, c_ptr), c_ptr)
py_decref = extern("py_decref", (c_ptr,), c_void)
py_exc_get_message = extern("py_exc_get_message", (c_ptr,), c_ptr)
py_obj_str = extern("py_obj_str", (c_ptr,), c_ptr)
py_obj_repr = extern("py_obj_repr", (c_ptr,), c_ptr)
py_exc_matches = extern("py_exc_matches", (c_ptr, c_ptr), c_int64)
py_exc_builtin_class = extern("py_exc_builtin_class", (c_int64,), c_ptr)
fflush = extern("fflush", (c_ptr,), c_int32)
py_incref = extern("py_incref", (c_ptr,), c_void)
py_obj_getattr = extern("py_obj_getattr", (c_ptr, c_ptr), c_ptr)
py_obj_call = extern("py_obj_call", (c_ptr, c_ptr, c_ptr), c_ptr)
py_obj_truthy = extern("py_obj_truthy", (c_ptr,), c_int64)
py_obj_type_name = extern("py_obj_type_name", (c_ptr,), c_ptr)
py_str_concat = extern("py_str_concat", (c_ptr, c_ptr), c_ptr)
py_str_new = extern("py_str_new", (c_ptr, c_int64), c_ptr)
py_str_utf8 = extern("py_str_utf8", (c_ptr,), c_ptr)
py_tuple_new = extern("py_tuple_new", (c_int64,), c_ptr)
py_tuple_set_item = extern("py_tuple_set_item", (c_ptr, c_int64, c_ptr), c_void)
py_exc_new = extern("py_exc_new", (c_int64, c_ptr), c_ptr)
py_raise_owned = extern("py_raise_owned", (c_ptr,), c_void)
# Returns 1 if the unknown-tag object was a CPython PyObject and was
# rendered via PyObject_Str (libpython mode). Returns 0 in strict
# no-libpython mode (the hook variable is NULL there) so the caller
# falls through to ``<object tag=N>``. See py_process.c.
py_format_try_cpy_object_into_fd = extern(
    "py_format_try_cpy_object_into_fd",
    (c_int32, c_ptr, c_int32),
    c_int32,
)
# Render a numpy / C-extension scalar by driving its own tp_repr slot (NOT
# PyObject_Repr, which routes back to pcc and raises for a foreign object).
pcc_capi_cext_object_repr = extern("pcc_capi_cext_object_repr", (c_ptr,), c_ptr)
pcc_capi_is_cext_type_tag = extern("pcc_capi_is_cext_type_tag", (c_int64,), c_int64)


def _type_of(obj) -> int:
    if is_tagged_int(obj):
        return PY_TYPE_INT       # PY_TYPE_INT
    return load_i32(obj, 8)


# One print call's output buffer, on that call's stack:
#   [0] data   [8] used bytes   [16] capacity
# The thread-local names the innermost print in progress. A print writes its
# whole output with one write(), so prints from different threads never
# interleave inside a line, as they do not under CPython's GIL, and an int or
# a repr no longer costs one write() per digit or byte. Pending text is
# written out before code that can print on its own runs (__str__, __repr__,
# an extension's tp_repr): a print nested in that code then lands after the
# text the outer call already produced, as with CPython's shared buffer.
define_thread_local_ptr_null("pcc_tls_print_buffer")


def _write_fd_all(data, n: int) -> None:
    done: int = 0
    failed: int = 0
    while done < n and failed == 0:
        wrote: int = write(1, ptr_add(data, done), n - done)
        if wrote <= 0:
            failed = 1
        else:
            done = done + wrote


def _print_buffer_flush() -> None:
    state = global_load_ptr("pcc_tls_print_buffer")
    if ptr_is_null(state) != 0:
        return
    used: int = load_i64(state, 8)
    if used > 0:
        _write_fd_all(load_ptr(state, 0), used)
        store_i64(state, 8, 0)


def _print_buffer_begin(state, data):
    """Install ``state`` over ``data`` (4096 bytes) and return the outer one."""
    store_ptr(state, 0, data)
    store_i64(state, 8, 0)
    store_i64(state, 16, 4096)
    outer = global_load_ptr("pcc_tls_print_buffer")
    global_store_ptr("pcc_tls_print_buffer", state)
    return outer


def _print_buffer_end(outer) -> None:
    _print_buffer_flush()
    global_store_ptr("pcc_tls_print_buffer", outer)


def _print_buffer_release():
    """Write pending text out and detach the buffer while other code runs.

    That code may print (its print installs its own buffer) or suspend a
    virtual thread that resumes on another carrier thread, so no buffer stays
    installed across it."""
    _print_buffer_flush()
    state = global_load_ptr("pcc_tls_print_buffer")
    global_store_ptr("pcc_tls_print_buffer", null())
    return state


def _print_buffer_reacquire(state) -> None:
    global_store_ptr("pcc_tls_print_buffer", state)


def _write_cstr(p) -> None:
    if ptr_is_null(p) != 0:
        return
    n: int = strlen(p)
    if n > 0:
        _write_lit(p, n)


def _write_lit(p, n: int) -> None:
    if n <= 0:
        return
    state = global_load_ptr("pcc_tls_print_buffer")
    if ptr_is_null(state) != 0:
        _write_fd_all(p, n)
        return
    used: int = load_i64(state, 8)
    capacity: int = load_i64(state, 16)
    if used + n > capacity:
        if used > 0:
            _write_fd_all(load_ptr(state, 0), used)
            store_i64(state, 8, 0)
            used = 0
        if n > capacity:
            _write_fd_all(p, n)
            return
    memcpy(ptr_add(load_ptr(state, 0), used), p, n)
    store_i64(state, 8, used + n)


def _write_byte(c: int) -> None:
    cell = stack_alloc(8)
    store_i8(cell, 0, c)
    _write_lit(cell, 1)


def _flush_stdio() -> None:
    # Keep ordering stable with codegen-emitted printf() calls for
    # native scalar print paths. fflush(NULL) flushes all output streams.
    fflush(null())


def _write_i64(v: int) -> None:
    if v == 0:
        _write_lit(cstr("0"), 1)
        return
    negative: int = 0
    if v < 0:
        negative = 1
        v = 0 - v
    # Digits fill the cell from its end, so the text is written in one piece.
    buf = stack_alloc(32)
    pos: int = 32
    while v > 0:
        digit: int = v % 10
        pos = pos - 1
        store_i8(buf, pos, 48 + digit)
        v = v // 10
    if negative != 0:
        pos = pos - 1
        store_i8(buf, pos, 45)
    _write_lit(ptr_add(buf, pos), 32 - pos)


def _write_hex_nibble(v: int) -> None:
    if v < 10:
        _write_byte(48 + v)
    else:
        _write_byte(87 + v)


def _write_hex2(v: int) -> None:
    _write_hex_nibble((v >> 4) & 15)
    _write_hex_nibble(v & 15)


def _format_int(o) -> None:
    if is_tagged_int(o):
        _write_i64(py_int_value_i64(o))
        return
    s = py_bigint_to_cstr(o)
    if ptr_is_null(s) != 0:
        return
    _write_cstr(s)
    free(s)


def _format_str(o) -> None:
    n: int = load_i64(o, 16)
    if n > 0:
        _write_lit(ptr_add(o, 40), n)


def _repr_quote(data, n: int) -> int:
    # Single quotes unless the text has a single quote and no double quote
    # (CPython unicode_repr / bytes_repr).
    squote: int = 0
    dquote: int = 0
    i: int = 0
    while i < n:
        c: int = load_i8(data, i) & 255
        if c == 39:
            squote = 1
        elif c == 34:
            dquote = 1
        i = i + 1
    if squote != 0 and dquote == 0:
        return 34
    return 39


def _write_quote(quote: int) -> None:
    if quote == 34:
        _write_lit(cstr("\""), 1)
    else:
        _write_lit(cstr("'"), 1)


def _format_bytes(o) -> None:
    data = ptr_add(o, 24)
    n: int = load_i64(o, 16)
    quote: int = _repr_quote(data, n)
    _write_lit(cstr("b"), 1)
    _write_quote(quote)
    run: int = 0
    i: int = 0
    while i < n:
        c: int = load_i8(data, i) & 255
        if c == 92 or c == quote or c < 32 or c >= 127:
            if i > run:
                _write_lit(ptr_add(data, run), i - run)
            run = i + 1
        if c == 92:                  # '\\'
            _write_lit(cstr("\\\\"), 2)
        elif c == quote and c == 39:
            _write_lit(cstr("\\'"), 2)
        elif c == quote:
            _write_lit(cstr("\\\""), 2)
        elif c == 10:
            _write_lit(cstr("\\n"), 2)
        elif c == 13:
            _write_lit(cstr("\\r"), 2)
        elif c == 9:
            _write_lit(cstr("\\t"), 2)
        elif c < 32 or c >= 127:
            # bytes repr: only printable ASCII (32..126) raw; control, DEL and
            # all high bytes (>=128) escape as \xNN. (c == 127 missed 128..255,
            # so b'\xcf\x80' printed the raw UTF-8 char instead of the escapes.)
            _write_lit(cstr("\\x"), 2)
            _write_hex2(c)
        i = i + 1
    if n > run:
        _write_lit(ptr_add(data, run), n - run)
    _write_quote(quote)


def _format_bytearray(o) -> None:
    # bytearray repr: bytearray(b'...'); byte escaping is identical to bytes
    # (same layout: byte_len @16, data @24), wrapped in bytearray( ... ).
    _write_lit(cstr("bytearray("), 10)
    _format_bytes(o)
    _write_lit(cstr(")"), 1)


def _format_float(o) -> None:
    # CPython shortest-round-trip repr (handles inf/nan and the trailing ".0"
    # internally), via the shared C helper py_float_repr_shortest. The old path
    # used py_float_format_fixed(o, 6) which printed "3.333333" for 10/3.
    s = py_float_repr_shortest(o)
    if ptr_is_null(s) != 0:
        return
    _format_str(s)
    py_decref(s)


def _format(o) -> None:
    if ptr_is_null(o) != 0:
        _write_lit(cstr("<null>"), 6)
        return
    if is_tagged_int(o):
        _format_int(o)
        return

    tag: int = load_i32(o, 8)
    if tag == PY_TYPE_ELLIPSIS:
        _write_lit(cstr("Ellipsis"), 8)
    elif tag == PY_TYPE_NOTIMPLEMENTED:
        _write_lit(cstr("NotImplemented"), 14)
    elif tag == PY_TYPE_NONE:                    # PY_TYPE_NONE
        _write_lit(cstr("None"), 4)
    elif tag == PY_TYPE_BOOL:                  # PY_TYPE_BOOL
        if ptr_eq(o, global_load_ptr("py_True")) != 0:
            _write_lit(cstr("True"), 4)
        else:
            _write_lit(cstr("False"), 5)
    elif tag == PY_TYPE_INT:                  # PY_TYPE_INT
        _format_int(o)
    elif tag == PY_TYPE_FLOAT:                  # PY_TYPE_FLOAT
        _format_float(o)
    elif tag == PY_TYPE_STR:                  # PY_TYPE_STR
        _format_str(o)
    elif tag == PY_TYPE_BYTES:                 # PY_TYPE_BYTES
        _format_bytes(o)
    elif tag == PY_TYPE_BYTEARRAY:                 # PY_TYPE_BYTEARRAY
        _format_bytearray(o)
    elif (
        tag == PY_TYPE_LIST
        or tag == PY_TYPE_TUPLE
        or tag == PY_TYPE_DICT
        or tag == PY_TYPE_SET
    ):
        # str(container) builds the whole repr first, as CPython does, so a
        # print run by an element's __repr__ lands before this line's text.
        held = _print_buffer_release()
        text = py_obj_repr(o)
        _print_buffer_reacquire(held)
        if ptr_is_null(text) == 0:
            _format_str(text)
            py_decref(text)
    elif tag == PY_TYPE_COROUTINE:                 # PY_TYPE_COROUTINE
        _write_lit(cstr("<coroutine object>"), 18)
    elif tag == PY_TYPE_CONTINUATION:
        _write_lit(cstr("<continuation object>"), 21)
    elif tag == PY_TYPE_VIRTUAL_THREAD:
        _write_lit(cstr("<virtual thread object>"), 23)
    elif tag == PY_TYPE_VTHREAD_CHANNEL:
        _write_lit(cstr("<vthread channel object>"), 24)
    elif tag == PY_TYPE_EXC:
        if (load_i32(o, 12) & (PY_FLAG_EXC_UNICODE_PAYLOAD | PY_FLAG_EXC_OS_PAYLOAD)) != 0:
            held = _print_buffer_release()
            rendered = py_obj_str(o)
            _print_buffer_reacquire(held)
            if ptr_is_null(rendered) == 0:
                _format_str(rendered)
                py_decref(rendered)
        else:
            # Preserve ordinary exception printing's existing single-message
            # path; explicitly marked private payloads need shared rendering.
            msg = py_exc_get_message(o)
            if ptr_is_null(msg) == 0:
                if py_exc_matches(o, py_exc_builtin_class(4)) != 0:
                    held = _print_buffer_release()
                    r = py_obj_repr(msg)
                    _print_buffer_reacquire(held)
                    if ptr_is_null(r) == 0:
                        _format_str(r)
                        py_decref(r)
                else:
                    _format(msg)
    else:
        # User-class instances and other objects: str(x) routes through
        # py_obj_str, which dispatches __str__ (then __repr__). This is what
        # print() uses in CPython; without it print(<instance>) rendered the
        # opaque "<object tag=N>" even when the class defines __str__. A NULL
        # result (no __str__/__repr__) falls back to the libpython hook then
        # "<object tag=N>".
        held = _print_buffer_release()
        s = py_obj_str(o)
        if ptr_is_null(s) != 0:
            # numpy / C-extension scalar (e.g. an ndarray element): py_obj_str
            # has no pcc dispatch for it, so drive its own tp_repr slot rather
            # than printing the opaque <object tag=N>.
            if pcc_capi_is_cext_type_tag(tag) != 0:
                s = pcc_capi_cext_object_repr(o)
        # The CPython-object renderer writes to fd 1 itself.
        rendered: int = 0
        if ptr_is_null(s) != 0:
            rendered = py_format_try_cpy_object_into_fd(1, o, tag)
        _print_buffer_reacquire(held)
        if ptr_is_null(s) == 0:
            _format_str(s)
            py_decref(s)
        elif rendered == 0:
            _write_lit(cstr("<object tag="), 12)
            _write_i64(tag)
            _write_lit(cstr(">"), 1)


@c_abi_export("py_print")
def py_print(o) -> None:
    _flush_stdio()
    data = stack_alloc(4096)
    state = stack_alloc(24)
    outer = _print_buffer_begin(state, data)
    _format(o)
    _write_lit(cstr("\n"), 1)
    _print_buffer_end(outer)


@c_abi_export("py_print_many")
def py_print_many(args_tuple, sep, end) -> None:
    _flush_stdio()
    data = stack_alloc(4096)
    state = stack_alloc(24)
    outer = _print_buffer_begin(state, data)
    sep_data = cstr(" ")
    sep_len: int = 1
    end_data = cstr("\n")
    end_len: int = 1
    none = global_load_ptr("py_None")

    if ptr_is_null(sep) == 0:
        if ptr_eq(sep, none) == 0:
            if _type_of(sep) == PY_TYPE_STR:
                sep_data = ptr_add(sep, 40)
                sep_len = load_i64(sep, 16)
    if ptr_is_null(end) == 0:
        if ptr_eq(end, none) == 0:
            if _type_of(end) == PY_TYPE_STR:
                end_data = ptr_add(end, 40)
                end_len = load_i64(end, 16)

    length: int = 0
    if ptr_is_null(args_tuple) == 0:
        length = load_i64(args_tuple, 16)
    i: int = 0
    while i < length:
        if i > 0:
            _write_lit(sep_data, sep_len)
        _format(pcc_gc_load_ptr(args_tuple, ptr_add(args_tuple, 24 + i * 8)))
        i = i + 1
    _write_lit(end_data, end_len)
    _print_buffer_end(outer)


def _print_file_text_arg(value, default, which):
    """NEW str for print's ``sep``/``end``: None means the default; any other
    non-str raises CPython's TypeError.  NULL on error."""
    none = global_load_ptr("py_None")
    if ptr_is_null(value) != 0 or ptr_eq(value, none) != 0:
        return py_str_new(default, 1)
    if _type_of(value) == PY_TYPE_STR:
        py_incref(value)
        return value
    name = py_obj_type_name(value)
    msg = py_str_new(which, _cstr_len(which))
    msg = _print_cat(msg, py_str_new(cstr(" must be None or a string, not "), 31))
    msg = _print_cat(msg, name)
    if ptr_is_null(msg) == 0:
        py_raise_owned(py_exc_new(3, py_str_utf8(msg)))  # 3 == PY_EXC_TYPEERROR
        py_decref(msg)
    return null()


def _cstr_len(p) -> int:
    n: int = 0
    while load_i8(p, n) != 0:
        n = n + 1
    return n


def _print_cat(acc, piece):
    """Concatenate two owned strs; releases both.  NULL if either is NULL."""
    if ptr_is_null(acc) != 0 or ptr_is_null(piece) != 0:
        if ptr_is_null(acc) == 0:
            py_decref(acc)
        if ptr_is_null(piece) == 0:
            py_decref(piece)
        return null()
    out = py_str_concat(acc, piece)
    py_decref(acc)
    py_decref(piece)
    return out


def _print_call_method(obj, name, arg) -> int:
    """``obj.name(arg)`` (``obj.name()`` when ``arg`` is NULL); -1 on error."""
    method = py_obj_getattr(obj, name)
    if ptr_is_null(method) != 0:
        return -1
    n: int = 1
    if ptr_is_null(arg) != 0:
        n = 0
    call_args = py_tuple_new(n)
    if ptr_is_null(call_args) != 0:
        py_decref(method)
        return -1
    if n == 1:
        py_tuple_set_item(call_args, 0, arg)
    result = py_obj_call(method, call_args, null())
    py_decref(call_args)
    py_decref(method)
    if ptr_is_null(result) != 0:
        return -1
    py_decref(result)
    return 0


@c_abi_export("py_print_to_file")
def py_print_to_file(file, args_tuple, sep, end, flush) -> int:
    """``print(*args, sep=, end=, file=, flush=)`` for a file that is not a
    statically known standard stream.  Like CPython, each ``str(arg)``, each
    separator and the end string is a separate ``file.write`` call, then
    ``file.flush()`` when ``flush`` is truthy.  ``file=None`` is stdout.
    -1 with an exception set on failure.
    """
    none = global_load_ptr("py_None")
    sep_s = _print_file_text_arg(sep, cstr(" "), cstr("sep"))
    if ptr_is_null(sep_s) != 0:
        return -1
    end_s = _print_file_text_arg(end, cstr("\n"), cstr("end"))
    if ptr_is_null(end_s) != 0:
        py_decref(sep_s)
        return -1
    if ptr_is_null(file) != 0 or ptr_eq(file, none) != 0:
        py_print_many(args_tuple, sep_s, end_s)
        py_decref(sep_s)
        py_decref(end_s)
        return 0
    length: int = 0
    if ptr_is_null(args_tuple) == 0:
        length = load_i64(args_tuple, 16)
    rc: int = 0
    i: int = 0
    while i < length and rc == 0:
        if i > 0:
            rc = _print_call_method(file, cstr("write"), sep_s)
        if rc == 0:
            item = pcc_gc_load_ptr(args_tuple, ptr_add(args_tuple, 24 + i * 8))
            text = py_obj_str(item)
            if ptr_is_null(text) != 0:
                rc = -1
            else:
                rc = _print_call_method(file, cstr("write"), text)
                py_decref(text)
        i = i + 1
    if rc == 0:
        rc = _print_call_method(file, cstr("write"), end_s)
    py_decref(sep_s)
    py_decref(end_s)
    if rc != 0:
        return -1
    if ptr_is_null(flush) == 0 and ptr_eq(flush, none) == 0:
        if py_obj_truthy(flush) != 0:
            return _print_call_method(file, cstr("flush"), null())
    return 0
