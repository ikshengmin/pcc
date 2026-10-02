"""pcc-Python port of py_file.c."""

__pcc_runtime_port__ = True

from pcc.extern import (
    extern,
    c_abi_export,
    c_int32,
    c_int64,
    c_ptr,
    c_size_t,
    c_void,
)
from pcc.runtime.py.py_abi_constants import PYBYTESOBJECT_BYTE_LEN_OFFSET, PYBYTESOBJECT_DATA_OFFSET, PYMEMORYVIEWOBJECT_BASE_OFFSET, PYOBJECTHEADER_FLAGS_OFFSET, PYOBJECTHEADER_REFCOUNT_OFFSET, PYOBJECTHEADER_TYPE_TAG_OFFSET, PY_FLAG_GC_MALLOC_ALLOC, PY_FLAG_IMMORTAL, PY_TYPE_BYTEARRAY, PY_TYPE_BYTES, PY_TYPE_FILE, PY_TYPE_INT, PY_TYPE_MEMORYVIEW
from pcc.runtime.py.py_abi_constants import PY_TYPE_LIST, PY_TYPE_STR, PY_TYPE_TUPLE
from pcc.unsafe import (
    cstr,
    define_global_ptr_null,
    free,
    global_addr,
    global_load_ptr,
    global_store_ptr,
    is_tagged_int,
    load_i8,
    load_i32,
    load_i64,
    load_ptr,
    malloc,
    memcpy,
    memset,
    null,
    ptr_add,
    ptr_eq,
    ptr_is_null,
    realloc,
    store_i8,
    store_i32,
    store_i64,
    store_ptr,
)

fclose = extern("fclose", (c_ptr,), c_int32)
ferror = extern("ferror", (c_ptr,), c_int32)
fflush = extern("fflush", (c_ptr,), c_int32)
fgetc = extern("fgetc", (c_ptr,), c_int32)
fopen = extern("fopen", (c_ptr, c_ptr), c_ptr)
fread = extern("fread", (c_ptr, c_size_t, c_size_t, c_ptr), c_size_t)
# LP64 targets (aarch64-darwin / x86_64-linux): C ``long`` is 64-bit, so
# fseek/ftell take/return c_int64 here.
fseek = extern("fseek", (c_ptr, c_int64, c_int32), c_int32)
ftell = extern("ftell", (c_ptr,), c_int64)
fwrite = extern("fwrite", (c_ptr, c_size_t, c_size_t, c_ptr), c_size_t)
fileno = extern("fileno", (c_ptr,), c_int32)
isatty = extern("isatty", (c_int32,), c_int32)
py_func_new_bound = extern("py_func_new_bound", (c_ptr, c_ptr, c_ptr, c_ptr), c_ptr)
py_obj_iter = extern("py_obj_iter", (c_ptr,), c_ptr)
py_obj_next = extern("py_obj_next", (c_ptr,), c_ptr)
py_err_occurred = extern("py_err_occurred", (), c_int64)
py_current_exception = extern("py_current_exception", (), c_ptr)
py_clear_exception = extern("py_clear_exception", (), c_void)
py_exc_matches = extern("py_exc_matches", (c_ptr, c_ptr), c_int64)
py_exc_builtin_class = extern("py_exc_builtin_class", (c_int64,), c_ptr)
pcc_gc_pointer_register = extern("pcc_gc_pointer_register", (c_ptr,), c_int64)
strcmp = extern("strcmp", (c_ptr, c_ptr), c_int32)

define_global_ptr_null("py_sys_stdin_object")
define_global_ptr_null("py_sys_stdout_object")
define_global_ptr_null("py_sys_stderr_object")

py_decref = extern("py_decref", (c_ptr,), c_void)
py_incref = extern("py_incref", (c_ptr,), c_void)
py_exc_new = extern("py_exc_new", (c_int64, c_ptr), c_ptr)
py_raise_owned = extern("py_raise_owned", (c_ptr,), c_void)
py_bool_from_bit = extern("py_bool_from_bit", (c_int32,), c_ptr)
py_int_from_i64 = extern("py_int_from_i64", (c_int64,), c_ptr)
py_int_value_i64 = extern("py_int_value_i64", (c_ptr,), c_int64)
py_list_new = extern("py_list_new", (c_int64,), c_ptr)
py_list_append = extern("py_list_append", (c_ptr, c_ptr), c_void)
py_list_get = extern("py_list_get", (c_ptr, c_int64), c_ptr)
py_list_set = extern("py_list_set", (c_ptr, c_int64, c_ptr), c_void)
py_list_len = extern("py_list_len", (c_ptr,), c_int64)
py_tuple_new = extern("py_tuple_new", (c_int64,), c_ptr)
py_tuple_set_item = extern("py_tuple_set_item", (c_ptr, c_int64, c_ptr), c_void)
py_tuple_get = extern("py_tuple_get", (c_ptr, c_int64), c_ptr)
py_tuple_len = extern("py_tuple_len", (c_ptr,), c_int64)
py_obj_call = extern("py_obj_call", (c_ptr, c_ptr, c_ptr), c_ptr)
py_str_splitlines_keepends = extern(
    "py_str_splitlines_keepends", (c_ptr, c_int32), c_ptr
)
py_bytes_new = extern("py_bytes_new", (c_ptr, c_int64), c_ptr)
pcc_gc_alloc = extern("pcc_gc_alloc", (c_int64, c_int32, c_int32), c_ptr)
py_obj_str = extern("py_obj_str", (c_ptr,), c_ptr)
pcc_gc_load_ptr = extern("pcc_gc_load_ptr", (c_ptr, c_ptr), c_ptr)
py_str_byte_len = extern("py_str_byte_len", (c_ptr,), c_int64)
py_str_new = extern("py_str_new", (c_ptr, c_int64), c_ptr)
py_str_utf8 = extern("py_str_utf8", (c_ptr,), c_ptr)


def _type_of(obj) -> int:
    if is_tagged_int(obj):
        return PY_TYPE_INT
    return load_i32(obj, 8)


def _coerce_str(o):
    if ptr_is_null(o):
        return null()
    if _type_of(o) == PY_TYPE_STR:
        return o
    return py_obj_str(o)


def _checked_file(file):
    if ptr_is_null(file):
        return null()
    if _type_of(file) != PY_TYPE_FILE:
        return null()
    if load_i32(file, 24) != 0:
        return null()
    if ptr_is_null(load_ptr(file, 16)):
        return null()
    return file


def _mode_is_binary(mode_s) -> int:
    if ptr_is_null(mode_s):
        return 0
    data = py_str_utf8(mode_s)
    n: int = py_str_byte_len(mode_s)
    i: int = 0
    while i < n:
        if (load_i8(data, i) & 255) == 98:
            return 1
        i = i + 1
    return 0


# Offset 32 of a file object: bits 0-7 hold ``fd + 1`` for a standard stream
# (0 for opened files), bit 8 readable, bit 9 writable.
def _mode_access_bits(mode_s) -> int:
    if ptr_is_null(mode_s):
        return 256
    data = py_str_utf8(mode_s)
    n: int = py_str_byte_len(mode_s)
    bits: int = 0
    i: int = 0
    while i < n:
        c: int = load_i8(data, i) & 255
        if c == 114:  # r
            bits = bits | 256
        elif c == 119 or c == 97 or c == 120:  # w a x
            bits = bits | 512
        elif c == 43:  # +
            bits = bits | 768
        i = i + 1
    return bits


def _file_std_fd_plus1(file) -> int:
    return load_i64(file, 32) & 255


def _file_binary(file) -> int:
    return load_i32(file, 28)


def _file_bytes_or_str(file, data, n: int):
    if _file_binary(file) != 0:
        return py_bytes_new(data, n)
    return py_str_new(data, n)


def _file_bytes_like_base(value):
    current = value
    while not ptr_is_null(current) and not is_tagged_int(current):
        tag: int = _type_of(current)
        if tag == PY_TYPE_BYTES or tag == PY_TYPE_BYTEARRAY:
            return current
        if tag != PY_TYPE_MEMORYVIEW:
            return null()
        current = pcc_gc_load_ptr(
            current,
            ptr_add(current, PYMEMORYVIEWOBJECT_BASE_OFFSET),
        )
    return null()


@c_abi_export("py_file_open")
def py_file_open(path, mode):
    path_s = _coerce_str(path)
    path_owned = null()
    if not ptr_is_null(path_s) and not ptr_eq(path_s, path):
        path_owned = path_s

    mode_s = null()
    mode_owned = null()
    none = global_load_ptr("py_None")
    if ptr_is_null(mode) or ptr_eq(mode, none) != 0:
        mode_s = py_str_new(cstr("r"), 1)
        mode_owned = mode_s
    else:
        mode_s = _coerce_str(mode)
        if not ptr_is_null(mode_s) and not ptr_eq(mode_s, mode):
            mode_owned = mode_s

    if ptr_is_null(path_s) or ptr_is_null(mode_s):
        py_decref(path_owned)
        py_decref(mode_owned)
        return null()

    binary: int = _mode_is_binary(mode_s)
    access: int = _mode_access_bits(mode_s)
    fp = fopen(py_str_utf8(path_s), py_str_utf8(mode_s))
    py_decref(path_owned)
    py_decref(mode_owned)
    if ptr_is_null(fp):
        # 14 == PY_EXC_OSERROR. Keep the C and pcc-Python runtime mirrors on
        # the same NULL-plus-exception failure contract.
        py_raise_owned(py_exc_new(14, cstr("could not open file")))
        return null()

    out = pcc_gc_alloc(40, PY_TYPE_FILE, 0)
    if ptr_is_null(out):
        fclose(fp)
        return null()
    store_ptr(out, 16, fp)
    store_i32(out, 24, 0)
    store_i32(out, 28, binary)
    store_i64(out, 32, access)
    return out


@c_abi_export("py_file_read_all")
def py_file_read_all(file):
    f = _checked_open_file(file)
    if ptr_is_null(f):
        return null()
    fp = load_ptr(f, 16)

    buf = null()
    length: int = 0
    cap: int = 0
    tmp = malloc(4096)
    if ptr_is_null(tmp):
        return null()
    while True:
        n: int = fread(tmp, 1, 4096, fp)
        if n > 0:
            if length + n + 1 > cap:
                new_cap: int = cap
                if new_cap == 0:
                    new_cap = 4096
                while new_cap < length + n + 1:
                    new_cap = new_cap * 2
                grown = realloc(buf, new_cap)
                if ptr_is_null(grown):
                    free(tmp)
                    free(buf)
                    return null()
                buf = grown
                cap = new_cap
            memcpy(ptr_add(buf, length), tmp, n)
            length = length + n
        if n < 4096:
            if ferror(fp) != 0:
                free(tmp)
                free(buf)
                return null()
            break
    data = buf
    if ptr_is_null(data):
        data = cstr("")
    out = _file_bytes_or_str(f, data, length)
    free(tmp)
    free(buf)
    return out


@c_abi_export("py_file_read")
def py_file_read(file, limit: int):
    if limit < 0:
        return py_file_read_all(file)
    f = _checked_open_file(file)
    if ptr_is_null(f):
        return null()
    fp = load_ptr(f, 16)
    buf = null()
    n: int = 0
    if limit > 0:
        buf = malloc(limit)
        if ptr_is_null(buf):
            return null()
        n = fread(buf, 1, limit, fp)
        if n < limit and ferror(fp) != 0:
            free(buf)
            return null()
    data = buf
    if ptr_is_null(data):
        data = cstr("")
    out = _file_bytes_or_str(f, data, n)
    free(buf)
    return out


@c_abi_export("py_file_write")
def py_file_write(file, text):
    f = _checked_file(file)
    if ptr_is_null(f):
        return null()
    if _file_binary(f) != 0:
        base = _file_bytes_like_base(text)
        if ptr_is_null(base):
            py_raise_owned(
                py_exc_new(
                    3,
                    cstr("a bytes-like object is required for binary write"),
                )
            )
            return null()
        n: int = load_i64(base, PYBYTESOBJECT_BYTE_LEN_OFFSET)
        wrote: int = 0
        if n > 0:
            wrote = fwrite(
                ptr_add(base, PYBYTESOBJECT_DATA_OFFSET),
                1,
                n,
                load_ptr(f, 16),
            )
        return py_int_from_i64(wrote)
    s = _coerce_str(text)
    owned = null()
    if not ptr_is_null(s) and not ptr_eq(s, text):
        owned = s
    if ptr_is_null(s):
        py_decref(owned)
        return null()
    n: int = py_str_byte_len(s)
    wrote: int = 0
    if n > 0:
        wrote = fwrite(py_str_utf8(s), 1, n, load_ptr(f, 16))
    py_decref(owned)
    return py_int_from_i64(wrote)


def _checked_open_file(file):
    """Shared open-file precondition for reads, seek, tell and flush.

    NULL / non-file receivers return null silently; a closed file raises
    ValueError exactly like
    CPython ("I/O operation on closed file.").
    """
    if ptr_is_null(file):
        return null()
    if _type_of(file) != PY_TYPE_FILE:
        return null()
    if load_i32(file, 24) != 0 or ptr_is_null(load_ptr(file, 16)):
        # 2 == PY_EXC_VALUEERROR
        py_raise_owned(py_exc_new(2, cstr("I/O operation on closed file.")))
        return null()
    return file


@c_abi_export("py_file_readline")
def py_file_readline(file, limit: int):
    f = _checked_open_file(file)
    if ptr_is_null(f):
        return null()
    fp = load_ptr(f, 16)

    buf = null()
    length: int = 0
    cap: int = 0
    while True:
        if limit >= 0 and length >= limit:
            break
        ch: int = fgetc(fp)
        if ch < 0:
            if ferror(fp) != 0:
                free(buf)
                return null()
            break
        if length + 2 > cap:
            new_cap: int = 128
            if cap != 0:
                new_cap = cap * 2
            grown = realloc(buf, new_cap)
            if ptr_is_null(grown):
                free(buf)
                return null()
            buf = grown
            cap = new_cap
        store_i8(buf, length, ch)
        length = length + 1
        if ch == 10:
            break
    data = buf
    if ptr_is_null(data):
        data = cstr("")
    out = _file_bytes_or_str(f, data, length)
    free(buf)
    return out


@c_abi_export("py_file_seek")
def py_file_seek(file, offset: int, whence: int):
    f = _checked_open_file(file)
    if ptr_is_null(f):
        return null()
    fp = load_ptr(f, 16)
    # SEEK_SET/SEEK_CUR/SEEK_END are 0/1/2 on both LP64 targets; unknown
    # whence values fall back to SEEK_SET like the C mirror.
    w: int = 0
    if whence == 1:
        w = 1
    if whence == 2:
        w = 2
    rc: int = fseek(fp, offset, w)
    pos: int = -1
    if rc == 0:
        pos = ftell(fp)
    if rc != 0 or pos < 0:
        # 14 == PY_EXC_OSERROR
        py_raise_owned(py_exc_new(14, cstr("Invalid argument")))
        return null()
    return py_int_from_i64(pos)


@c_abi_export("py_file_tell")
def py_file_tell(file):
    f = _checked_open_file(file)
    if ptr_is_null(f):
        return null()
    pos: int = ftell(load_ptr(f, 16))
    if pos < 0:
        # 14 == PY_EXC_OSERROR
        py_raise_owned(py_exc_new(14, cstr("Invalid argument")))
        return null()
    return py_int_from_i64(pos)


@c_abi_export("py_file_flush")
def py_file_flush(file):
    f = _checked_open_file(file)
    if ptr_is_null(f):
        return null()
    fflush(load_ptr(f, 16))
    none = global_load_ptr("py_None")
    py_incref(none)
    return none


@c_abi_export("py_file_fileno")
def py_file_fileno(file):
    f = _checked_open_file(file)
    if ptr_is_null(f):
        return null()
    fd: int = fileno(load_ptr(f, 16))
    if fd < 0:
        py_raise_owned(py_exc_new(14, cstr("could not get file descriptor")))
        return null()
    return py_int_from_i64(fd)


@c_abi_export("py_file_close")
def py_file_close(file) -> None:
    if ptr_is_null(file):
        return
    if _type_of(file) != PY_TYPE_FILE:
        return
    if load_i32(file, 24) == 0:
        fp = load_ptr(file, 16)
        if not ptr_is_null(fp):
            if _file_std_fd_plus1(file) != 0:
                # sys.stdin/stdout/stderr: closefd=False, like CPython.
                fflush(fp)
            else:
                fclose(fp)
                store_ptr(file, 16, null())
            store_i32(file, 24, 1)


@c_abi_export("py_file_readlines")
def py_file_readlines(file):
    """``f.readlines()``: every remaining line, keeping line endings."""
    out = py_list_new(0)
    if ptr_is_null(out):
        return null()
    while True:
        line = py_file_readline(file, -1)
        if ptr_is_null(line):
            py_decref(out)
            return null()
        n: int = 0
        if _type_of(line) == PY_TYPE_STR:
            n = py_str_byte_len(line)
        else:
            n = load_i64(line, PYBYTESOBJECT_BYTE_LEN_OFFSET)
        if n == 0:
            py_decref(line)
            return out
        py_list_append(out, line)
        py_decref(line)


@c_abi_export("py_file_writelines")
def py_file_writelines(file, lines):
    """``f.writelines(iterable)``: write each item; returns None."""
    it = py_obj_iter(lines)
    if ptr_is_null(it):
        return null()
    while True:
        item = py_obj_next(it)
        if ptr_is_null(item):
            py_decref(it)
            if py_err_occurred() != 0:
                # 8 == PY_EXC_STOPITERATION: exhaustion, not a failure.
                if py_exc_matches(py_current_exception(), py_exc_builtin_class(8)) == 0:
                    return null()
                py_clear_exception()
            none = global_load_ptr("py_None")
            py_incref(none)
            return none
        wrote = py_file_write(file, item)
        py_decref(item)
        if ptr_is_null(wrote):
            py_decref(it)
            return null()
        py_decref(wrote)


def _std_stream_new(fd: int):
    """A file object over the runtime's own standard stream ``fd``.

    The freestanding stdio's standard streams carry no buffer, so writes go
    straight to the descriptor and interleave with print() and the direct
    ``sys.stdout.write`` path, and reads consume no more than
    ``sys.stdin.readline()`` does.  Cached in a raw global pointer, so it gets
    stable storage like the ``object`` root.  Offset 32 marks it as standard
    stream ``fd``: it never closes its descriptor (CPython's closefd=False).
    """
    fp = null()
    if fd == 0:
        fp = global_addr("pcc_stdio_stdin_storage")
    elif fd == 1:
        fp = global_addr("pcc_stdio_stdout_storage")
    else:
        fp = global_addr("pcc_stdio_stderr_storage")
    out = malloc(40)
    if ptr_is_null(out):
        return null()
    memset(out, 0, 40)
    store_i64(out, PYOBJECTHEADER_REFCOUNT_OFFSET, 1)
    store_i32(out, PYOBJECTHEADER_TYPE_TAG_OFFSET, PY_TYPE_FILE)
    store_i32(out, PYOBJECTHEADER_FLAGS_OFFSET, PY_FLAG_IMMORTAL | PY_FLAG_GC_MALLOC_ALLOC)
    if pcc_gc_pointer_register(out) < 0:
        free(out)
        return null()
    store_ptr(out, 16, fp)
    store_i32(out, 24, 0)
    store_i32(out, 28, 0)
    if fd == 0:
        store_i64(out, 32, (fd + 1) | 256)
    else:
        store_i64(out, 32, (fd + 1) | 512)
    return out


@c_abi_export("py_sys_stream_object")
def py_sys_stream_object(fd: int):
    """``sys.stdin`` / ``sys.stdout`` / ``sys.stderr`` (fd 0/1/2) as values."""
    cached = null()
    if fd == 0:
        cached = global_load_ptr("py_sys_stdin_object")
    elif fd == 1:
        cached = global_load_ptr("py_sys_stdout_object")
    else:
        cached = global_load_ptr("py_sys_stderr_object")
    if ptr_is_null(cached):
        if fd == 0:
            cached = _std_stream_new(0)
            global_store_ptr("py_sys_stdin_object", cached)
        elif fd == 1:
            cached = _std_stream_new(1)
            global_store_ptr("py_sys_stdout_object", cached)
        else:
            cached = _std_stream_new(2)
            global_store_ptr("py_sys_stderr_object", cached)
        if ptr_is_null(cached):
            return null()
    py_incref(cached)
    return cached


# Dynamic method codes for file objects (``destination.write(text)`` on a
# file that no static type describes).
def _file_method_code(name) -> int:
    if strcmp(name, cstr("write")) == 0:
        return 1
    if strcmp(name, cstr("read")) == 0:
        return 2
    if strcmp(name, cstr("readline")) == 0:
        return 3
    if strcmp(name, cstr("readlines")) == 0:
        return 4
    if strcmp(name, cstr("writelines")) == 0:
        return 5
    if strcmp(name, cstr("flush")) == 0:
        return 6
    if strcmp(name, cstr("close")) == 0:
        return 7
    if strcmp(name, cstr("fileno")) == 0:
        return 8
    if strcmp(name, cstr("seek")) == 0:
        return 9
    if strcmp(name, cstr("tell")) == 0:
        return 10
    if strcmp(name, cstr("isatty")) == 0:
        return 11
    if strcmp(name, cstr("__enter__")) == 0:
        return 12
    if strcmp(name, cstr("__exit__")) == 0:
        return 13
    if strcmp(name, cstr("readable")) == 0:
        return 14
    if strcmp(name, cstr("writable")) == 0:
        return 15
    return 0


def _method_arg_i64(args, index: int, default: int) -> int:
    """Positional int argument ``index`` (None or absent: ``default``)."""
    if ptr_is_null(args) != 0 or index >= py_tuple_len(args):
        return default
    value = py_tuple_get(args, index)
    if ptr_is_null(value) != 0:
        return default
    if ptr_eq(value, global_load_ptr("py_None")) != 0:
        py_decref(value)
        return default
    out: int = py_int_value_i64(value)
    py_decref(value)
    return out


def _file_method_entry(captures, args):
    f = py_tuple_get(captures, 0)
    code_obj = py_tuple_get(captures, 1)
    if ptr_is_null(f) != 0 or ptr_is_null(code_obj) != 0:
        return null()
    code: int = py_int_value_i64(code_obj)
    py_decref(code_obj)
    nargs: int = 0
    if ptr_is_null(args) == 0:
        nargs = py_tuple_len(args)
    none = global_load_ptr("py_None")
    result = null()
    if code == 1 or code == 5:
        if nargs != 1:
            py_raise_owned(py_exc_new(3, cstr("expected exactly one argument")))
        else:
            arg = py_tuple_get(args, 0)
            if code == 1:
                result = py_file_write(f, arg)
            else:
                result = py_file_writelines(f, arg)
            py_decref(arg)
    elif code == 2:
        result = py_file_read(f, _method_arg_i64(args, 0, -1))
    elif code == 3:
        result = py_file_readline(f, _method_arg_i64(args, 0, -1))
    elif code == 4:
        result = py_file_readlines(f)
    elif code == 6:
        result = py_file_flush(f)
    elif code == 7:
        py_file_close(f)
        py_incref(none)
        result = none
    elif code == 8:
        result = py_file_fileno(f)
    elif code == 9:
        result = py_file_seek(f, _method_arg_i64(args, 0, 0), _method_arg_i64(args, 1, 0))
    elif code == 10:
        result = py_file_tell(f)
    elif code == 11:
        checked = _checked_open_file(f)
        if ptr_is_null(checked) == 0:
            result = py_bool_from_bit(isatty(fileno(load_ptr(f, 16))))
    elif code == 12:
        checked = _checked_open_file(f)
        if ptr_is_null(checked) == 0:
            py_incref(f)
            result = f
    elif code == 13:
        py_file_close(f)
        result = global_load_ptr("py_False")
    elif code == 14 or code == 15:
        checked = _checked_open_file(f)
        if ptr_is_null(checked) == 0:
            wanted: int = 256
            if code == 15:
                wanted = 512
            result = py_bool_from_bit((load_i64(f, 32) & wanted) != 0)
    py_decref(f)
    return result


@c_abi_export("py_file_type_kind")
def py_file_type_kind(file) -> int:
    """0 TextIOWrapper, 1 BufferedReader, 2 BufferedWriter, 3 BufferedRandom."""
    if ptr_is_null(file) or _type_of(file) != PY_TYPE_FILE:
        return 0
    if _file_binary(file) == 0:
        return 0
    access: int = load_i64(file, 32) & 768
    if access == 768:
        return 3
    if access == 512:
        return 2
    return 1


@c_abi_export("py_file_getattr")
def py_file_getattr(file, name):
    """Attribute load on a file object: ``closed`` and bound methods.

    NULL without an exception when ``name`` is not a file attribute.
    """
    if ptr_is_null(file) or _type_of(file) != PY_TYPE_FILE or ptr_is_null(name):
        return null()
    if strcmp(name, cstr("closed")) == 0:
        return py_bool_from_bit(load_i32(file, 24))
    code: int = _file_method_code(name)
    if code == 0:
        return null()
    captures = py_tuple_new(2)
    if ptr_is_null(captures):
        return null()
    py_tuple_set_item(captures, 0, file)
    code_obj = py_int_from_i64(code)
    py_tuple_set_item(captures, 1, code_obj)
    py_decref(code_obj)
    fn = py_func_new_bound(_file_method_entry, captures, name, file)
    py_decref(captures)
    return fn


# Keep fileinput state indexes as integer literals at use sites below. The
# pcc-Python runtime path cannot safely rely on module-level integer constants
# during early bootstrap module initialization.


def _state_get(state, index: int):
    return py_list_get(state, index)


def _state_get_i64(state, index: int) -> int:
    item = py_list_get(state, index)
    out: int = 0
    none = global_load_ptr("py_None")
    if not ptr_is_null(item) and ptr_eq(item, none) == 0:
        out = py_int_value_i64(item)
    py_decref(item)
    return out


def _state_set_i64(state, index: int, value: int) -> None:
    obj = py_int_from_i64(value)
    py_list_set(state, index, obj)


def _files_len(files) -> int:
    if ptr_is_null(files):
        return 0
    tag: int = _type_of(files)
    if tag == PY_TYPE_STR:
        return 1
    if tag == PY_TYPE_LIST:
        return py_list_len(files)
    if tag == PY_TYPE_TUPLE:
        return py_tuple_len(files)
    return 0


def _files_get(files, index: int):
    if ptr_is_null(files):
        return null()
    tag: int = _type_of(files)
    if tag == PY_TYPE_STR:
        if index != 0:
            return null()
        py_incref(files)
        return files
    if tag == PY_TYPE_LIST:
        return py_list_get(files, index)
    if tag == PY_TYPE_TUPLE:
        return py_tuple_get(files, index)
    return null()


def _fileinput_open_text(filename):
    mode = py_str_new(cstr("r"), 1)
    file = py_file_open(filename, mode)
    py_decref(mode)
    return file


def _open_next(state) -> int:
    files = _state_get(state, 0)
    idx: int = _state_get_i64(state, 2)
    nfiles: int = _files_len(files)
    while idx < nfiles:
        filename = _files_get(files, idx)
        _state_set_i64(state, 2, idx + 1)
        idx = idx + 1
        if ptr_is_null(filename):
            continue
        py_list_set(state, 6, filename)
        file = _fileinput_open_text(filename)
        if ptr_is_null(file):
            py_decref(files)
            return 0
        text = py_file_read_all(file)
        py_file_close(file)
        py_decref(file)
        if ptr_is_null(text):
            py_decref(files)
            return 0
        lines = py_str_splitlines_keepends(text, 1)
        py_decref(text)
        if ptr_is_null(lines):
            py_decref(files)
            return 0
        py_list_set(state, 3, lines)
        _state_set_i64(state, 4, 0)
        if py_list_len(lines) > 0:
            py_decref(files)
            return 1
    py_decref(files)
    return 0


@c_abi_export("py_fileinput_new")
def py_fileinput_new(files, openhook):
    state = py_list_new(7)
    zero = py_int_from_i64(0)
    empty = py_list_new(0)
    none = global_load_ptr("py_None")
    if ptr_is_null(files):
        py_list_append(state, none)
    else:
        py_list_append(state, files)
    if ptr_is_null(openhook):
        py_list_append(state, none)
    else:
        py_list_append(state, openhook)
    py_list_append(state, zero)
    py_list_append(state, empty)
    py_list_append(state, zero)
    py_list_append(state, zero)
    py_list_append(state, none)
    return state


@c_abi_export("py_fileinput_readline")
def py_fileinput_readline(state):
    if ptr_is_null(state):
        return py_str_new(cstr(""), 0)
    while True:
        lines = _state_get(state, 3)
        line_idx: int = _state_get_i64(state, 4)
        nlines: int = py_list_len(lines)
        if line_idx < nlines:
            line = py_list_get(lines, line_idx)
            py_decref(lines)
            _state_set_i64(state, 4, line_idx + 1)
            total: int = _state_get_i64(state, 5)
            _state_set_i64(state, 5, total + 1)
            return line
        py_decref(lines)
        if _open_next(state) == 0:
            return py_str_new(cstr(""), 0)


@c_abi_export("py_fileinput_filename")
def py_fileinput_filename(state):
    filename = _state_get(state, 6)
    if ptr_is_null(filename):
        none = global_load_ptr("py_None")
        py_incref(none)
        return none
    return filename


@c_abi_export("py_fileinput_lineno")
def py_fileinput_lineno(state):
    return py_int_from_i64(_state_get_i64(state, 5))


@c_abi_export("py_fileinput_filelineno")
def py_fileinput_filelineno(state):
    return py_int_from_i64(_state_get_i64(state, 4))


@c_abi_export("py_fileinput_isfirstline")
def py_fileinput_isfirstline(state):
    first: int = 0
    if _state_get_i64(state, 4) == 1:
        first = 1
    return py_bool_from_bit(first)


@c_abi_export("py_fileinput_close")
def py_fileinput_close(state):
    none = global_load_ptr("py_None")
    py_incref(none)
    return none
