"""pcc-Python port of py_process_substrate.c.

Linux/Windows subprocess helpers pass owned argv/environment vectors directly to
the platform process ABI. Directory operations use owned platform primitives.
"""

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
from pcc.unsafe import (
    target_sys_platform,
    close, read, spawn_process_pipe,
    directory_open, directory_next, directory_error, directory_close,
    cstr,
    free,
    global_load_ptr,
    load_i8,
    load_i32,
    load_i64,
    load_ptr,
    malloc,
    memcpy,
    null,
    ptr_add,
    ptr_is_null,
    realloc,
    readlink,
    stack_alloc,
    stat_kind,
    store_i8,
    store_i32,
    store_i64,
    store_ptr,
    strlen,
    unlinkat,
)

fgetc = extern("fgetc", (c_ptr,), c_int32)
fread = extern("fread", (c_ptr, c_size_t, c_size_t, c_ptr), c_size_t)
mkdtemp = extern("pcc_platform_mkdtemp", (c_ptr,), c_ptr)
remove_tree = extern("pcc_platform_remove_tree", (c_ptr,), c_int64)
access = extern("pcc_platform_access", (c_ptr, c_int64), c_int64)
pclose = extern("pclose", (c_ptr,), c_int32)
popen = extern("popen", (c_ptr, c_ptr), c_ptr)
getpid = extern("pcc_platform_getpid", (), c_int64)
getenv = extern("pcc_platform_getenv", (c_ptr,), c_ptr)
platform_env_snapshot = extern("pcc_platform_env_snapshot", (), c_ptr)
platform_env_snapshot_free = extern(
    "pcc_platform_env_snapshot_free", (c_ptr,), c_void
)
platform_spawnp = extern(
    "pcc_platform_spawnp", (c_ptr, c_ptr, c_int64), c_int64
)
platform_waitpid = extern(
    "pcc_platform_waitpid", (c_int64, c_ptr, c_int64), c_int64
)
platform_process_resolve = extern("pcc_platform_process_resolve", (c_ptr, c_ptr), c_ptr)
platform_kill = extern("pcc_platform_kill", (c_int64, c_int64), c_int64)
build_exec_argv = extern("pcc_process_build_exec_argv", (c_ptr,), c_ptr)
free_exec_argv = extern("pcc_process_free_exec_argv", (c_ptr, c_int64), c_void)

py_decref = extern("py_decref", (c_ptr,), c_void)
py_int_from_i64 = extern("py_int_from_i64", (c_int64,), c_ptr)
py_index_i64_checked = extern("py_index_i64_checked", (c_ptr,), c_int64)
py_err_occurred = extern("py_err_occurred", (), c_int64)
pcc_gc_scheduler_root_register_handle = extern(
    "pcc_gc_scheduler_root_register_handle", (c_ptr,), c_ptr
)
pcc_gc_scheduler_root_unregister_handle = extern(
    "pcc_gc_scheduler_root_unregister_handle", (c_ptr,), c_void
)
py_list_append = extern("py_list_append", (c_ptr, c_ptr), c_void)
py_list_new = extern("py_list_new", (c_int64,), c_ptr)
py_obj_getitem = extern("py_obj_getitem", (c_ptr, c_ptr), c_ptr)
py_obj_len = extern("py_obj_len", (c_ptr,), c_int64)
py_dict_entries_used = extern("py_dict_entries_used", (c_ptr,), c_int64)
py_dict_entry_key_at = extern("py_dict_entry_key_at", (c_ptr, c_int64), c_ptr)
py_dict_entry_value_at = extern(
    "py_dict_entry_value_at", (c_ptr, c_int64), c_ptr
)
py_obj_str = extern("py_obj_str", (c_ptr,), c_ptr)
py_program_argv = extern("py_program_argv", (c_int64,), c_ptr)
py_program_executable = extern("py_program_executable", (), c_ptr)
py_bytes_new = extern("py_bytes_new", (c_ptr, c_int64), c_ptr)
py_str_new = extern("py_str_new", (c_ptr, c_int64), c_ptr)
py_str_utf8 = extern("py_str_utf8", (c_ptr,), c_ptr)
py_exc_new = extern("py_exc_new", (c_int64, c_ptr), c_ptr)
py_raise = extern("py_raise", (c_ptr,), c_void)
# py_raise increfs; a caller that created the exception must release it.
py_raise_owned = extern("py_raise_owned", (c_ptr,), c_void)
py_process_normalize_wait_status = extern(
    "py_process_normalize_wait_status", (c_int64,), c_int64
)


def _none():
    return global_load_ptr("py_None")


def _empty_str():
    return py_str_new(cstr(""), 0)


def _empty_bytes():
    return py_bytes_new(cstr(""), 0)


def _buf_new():
    st = malloc(24)
    if ptr_is_null(st):
        return null()
    store_ptr(st, 0, null())
    store_i64(st, 8, 0)
    store_i64(st, 16, 0)
    return st


def _buf_data(st):
    return load_ptr(st, 0)


def _buf_len(st) -> int:
    return load_i64(st, 8)


def _buf_append(st, src, n: int) -> int:
    if n <= 0:
        return 0
    buf = load_ptr(st, 0)
    length: int = load_i64(st, 8)
    cap: int = load_i64(st, 16)
    if length + n + 1 > cap:
        new_cap: int = cap
        if new_cap <= 0:
            new_cap = 128
        while new_cap < length + n + 1:
            new_cap = new_cap * 2
        grown = realloc(buf, new_cap)
        if ptr_is_null(grown):
            return -1
        buf = grown
        store_ptr(st, 0, buf)
        store_i64(st, 16, new_cap)
    memcpy(ptr_add(buf, length), src, n)
    length = length + n
    store_i8(buf, length, 0)
    store_i64(st, 8, length)
    return 0


def _buf_free(st) -> None:
    if ptr_is_null(st):
        return
    free(load_ptr(st, 0))
    free(st)


def _buf_detach(st):
    if ptr_is_null(st):
        return null()
    out = load_ptr(st, 0)
    free(st)
    return out


def _append_shell_quoted(st, src) -> int:
    if _buf_append(st, cstr("'"), 1) != 0:
        return -1
    if not ptr_is_null(src):
        i: int = 0
        while load_i8(src, i) != 0:
            if load_i8(src, i) == 39:
                if _buf_append(st, cstr("'\\''"), 4) != 0:
                    return -1
            else:
                if _buf_append(st, ptr_add(src, i), 1) != 0:
                    return -1
            i = i + 1
    return _buf_append(st, cstr("'"), 1)


def _build_shell_command(argv):
    argc: int = py_obj_len(argv)
    if argc <= 0:
        return null()
    st = _buf_new()
    if ptr_is_null(st):
        return null()
    i: int = 0
    while i < argc:
        idx = py_int_from_i64(i)
        arg = py_obj_getitem(argv, idx)
        py_decref(idx)
        arg_str = py_obj_str(arg)
        py_decref(arg)
        raw = py_str_utf8(arg_str)
        if i > 0:
            if _buf_append(st, cstr(" "), 1) != 0:
                py_decref(arg_str)
                _buf_free(st)
                return null()
        if _append_shell_quoted(st, raw) != 0:
            py_decref(arg_str)
            _buf_free(st)
            return null()
        py_decref(arg_str)
        i = i + 1
    return _buf_detach(st)


def _run_shell_command(command, capture_output: int) -> int:
    """Execute one already-built shell command through the owned process ABI."""
    items = malloc(32)
    status = malloc(4)
    if ptr_is_null(items) or ptr_is_null(status):
        free(items)
        free(status)
        return 127
    store_ptr(items, 0, cstr("/bin/sh"))
    store_ptr(items, 8, cstr("-c"))
    store_ptr(items, 16, command)
    store_ptr(items, 24, null())
    store_i32(status, 0, 0)

    child_env = platform_env_snapshot()
    if ptr_is_null(child_env):
        free(items)
        free(status)
        return 127
    pid = platform_spawnp(items, child_env, capture_output)
    platform_env_snapshot_free(child_env)
    free(items)
    if pid <= 0:
        free(status)
        return 127
    waited = platform_waitpid(pid, status, 0)
    if waited != pid:
        free(status)
        return 127
    result = py_process_normalize_wait_status(load_i32(status, 0))
    free(status)
    return result


def _build_exec_env(env_map):
    """Copy dictionary entries to a terminated, owned UTF-8 environment."""
    count: int = py_dict_entries_used(env_map)
    items = malloc((count + 1) * 8)
    if ptr_is_null(items):
        return null()
    used: int = 0
    index: int = 0
    while index < count:
        key = py_dict_entry_key_at(env_map, index)
        value = py_dict_entry_value_at(env_map, index)
        index = index + 1
        if ptr_is_null(key) or ptr_is_null(value):
            py_decref(key)
            py_decref(value)
            continue
        key_str = py_obj_str(key)
        value_str = py_obj_str(value)
        py_decref(key)
        py_decref(value)
        if ptr_is_null(key_str) or ptr_is_null(value_str):
            py_decref(key_str)
            py_decref(value_str)
            free_exec_argv(items, used)
            return null()
        key_raw = py_str_utf8(key_str)
        value_raw = py_str_utf8(value_str)
        key_size: int = strlen(key_raw)
        value_size: int = strlen(value_raw)
        pair = malloc(key_size + value_size + 2)
        if ptr_is_null(pair):
            py_decref(key_str)
            py_decref(value_str)
            free_exec_argv(items, used)
            return null()
        memcpy(pair, key_raw, key_size)
        store_i8(pair, key_size, 61)
        memcpy(ptr_add(pair, key_size + 1), value_raw, value_size)
        store_i8(pair, key_size + value_size + 1, 0)
        py_decref(key_str)
        py_decref(value_str)
        store_ptr(items, used * 8, pair)
        used = used + 1
    store_ptr(items, used * 8, null())
    return items


def _run_exec_argv(argv, capture_output: int, child_env) -> int:
    count: int = py_obj_len(argv)
    items = build_exec_argv(argv)
    status = malloc(4)
    if ptr_is_null(items) or ptr_is_null(status):
        free_exec_argv(items, count)
        free(status)
        return 127
    store_i32(status, 0, 0)
    pid: int = platform_spawnp(items, child_env, capture_output)
    free_exec_argv(items, count)
    if pid <= 0:
        free(status)
        return 127
    waited: int = platform_waitpid(pid, status, 0)
    result: int = 127
    if waited == pid:
        result = py_process_normalize_wait_status(load_i32(status, 0))
    else:
        if platform_kill(-pid, 9) != 0:
            platform_kill(pid, 9)
        platform_waitpid(pid, status, 0)
    free(status)
    return result


def _check_output_exec_argv(argv):
    count: int = py_obj_len(argv)
    items = build_exec_argv(argv)
    child_env = platform_env_snapshot()
    slot = malloc(8)
    tmp = malloc(4096)
    st = _buf_new()
    if ptr_is_null(items) or ptr_is_null(child_env) or ptr_is_null(slot) or ptr_is_null(tmp) or ptr_is_null(st):
        free_exec_argv(items, count)
        platform_env_snapshot_free(child_env)
        free(slot)
        free(tmp)
        _buf_free(st)
        py_raise_owned(py_exc_new(14, cstr("subprocess allocation failed")))
        return null()
    path = platform_process_resolve(items, child_env)
    pid: int = -2
    if not ptr_is_null(path):
        pid = spawn_process_pipe(path, items, child_env, 1, slot)
    free(path)
    free_exec_argv(items, count)
    platform_env_snapshot_free(child_env)
    if pid <= 0:
        free(slot)
        free(tmp)
        _buf_free(st)
        py_raise_owned(py_exc_new(14, cstr("subprocess spawn failed")))
        return null()
    descriptor: int = load_i32(slot, 0)
    failed: int = 0
    while True:
        amount: int = read(descriptor, tmp, 4096)
        if amount == -4:
            continue
        if amount < 0:
            failed = 1
            break
        if amount == 0:
            break
        if _buf_append(st, tmp, amount) != 0:
            failed = 1
            break
    close(descriptor)
    if failed:
        if platform_kill(-pid, 9) != 0:
            platform_kill(pid, 9)
    waited: int = platform_waitpid(pid, slot, 0)
    if waited != pid or load_i32(slot, 0) != 0:
        failed = 1
    free(slot)
    free(tmp)
    if failed:
        _buf_free(st)
        py_raise_owned(py_exc_new(14, cstr("subprocess failed")))
        return null()
    data = _buf_data(st)
    if ptr_is_null(data):
        data = cstr("")
    result = py_bytes_new(data, _buf_len(st))
    _buf_free(st)
    return result


@c_abi_export("py_subprocess_check_output")
def py_subprocess_check_output(argv):
    if load_i8(target_sys_platform(), 0) == 119 or load_i8(target_sys_platform(), 0) == 108:
        return _check_output_exec_argv(argv)
    cmd = _build_shell_command(argv)
    if ptr_is_null(cmd):
        return _empty_bytes()
    fp = popen(cmd, cstr("r"))
    free(cmd)
    if ptr_is_null(fp):
        return _empty_bytes()

    st = _buf_new()
    tmp = malloc(4096)
    if ptr_is_null(st) or ptr_is_null(tmp):
        free(tmp)
        _buf_free(st)
        pclose(fp)
        return _empty_bytes()
    while True:
        n: int = fread(tmp, 1, 4096, fp)
        if n > 0:
            if _buf_append(st, tmp, n) != 0:
                free(tmp)
                _buf_free(st)
                pclose(fp)
                return _empty_bytes()
        if n < 4096:
            break
    status: int = pclose(fp)
    if status != 0:
        py_raise_owned(py_exc_new(14, cstr("subprocess failed")))  # PY_EXC_OSERROR = 14
        free(tmp)
        _buf_free(st)
        return null()
    data = _buf_data(st)
    length: int = _buf_len(st)
    if ptr_is_null(data):
        data = cstr("")
    result = py_bytes_new(data, length)
    free(tmp)
    _buf_free(st)
    return result


def _append_env_prefix(st, env_map) -> int:
    """Prefix ``env -i 'K=V' ...`` so the child sees exactly ``env_map``.

    CPython's ``env=`` replaces the environment rather than adding to it;
    ``env -i`` is that same semantics for a shell command line.
    """
    if _buf_append(st, cstr("env -i"), 6) != 0:
        return -1
    used: int = py_dict_entries_used(env_map)
    index: int = 0
    while index < used:
        key = py_dict_entry_key_at(env_map, index)
        index = index + 1
        if ptr_is_null(key):
            continue
        value = py_dict_entry_value_at(env_map, index - 1)
        if ptr_is_null(value):
            py_decref(key)
            continue
        key_str = py_obj_str(key)
        value_str = py_obj_str(value)
        py_decref(key)
        py_decref(value)
        pair = _buf_new()
        if ptr_is_null(pair):
            py_decref(key_str)
            py_decref(value_str)
            return -1
        failed: int = 0
        if _buf_append(pair, py_str_utf8(key_str), strlen(py_str_utf8(key_str))) != 0:
            failed = 1
        if failed == 0 and _buf_append(pair, cstr("="), 1) != 0:
            failed = 1
        if failed == 0 and _buf_append(
            pair, py_str_utf8(value_str), strlen(py_str_utf8(value_str))
        ) != 0:
            failed = 1
        py_decref(key_str)
        py_decref(value_str)
        if failed != 0:
            _buf_free(pair)
            return -1
        raw_pair = _buf_detach(pair)
        if ptr_is_null(raw_pair):
            return -1
        if _buf_append(st, cstr(" "), 1) != 0:
            free(raw_pair)
            return -1
        if _append_shell_quoted(st, raw_pair) != 0:
            free(raw_pair)
            return -1
        free(raw_pair)
    if _buf_append(st, cstr(" "), 1) != 0:
        return -1
    return 0


@c_abi_export("py_subprocess_run_env")
def py_subprocess_run_env(argv, capture_output: int, env_map) -> int:
    """``subprocess.run(argv, env=env_map)``.

    ``run_runtime_make`` -- the only way pcc1 can rebuild its own runtime
    archive -- passes ``env=``, so without this the archive rebuild could
    never run under ``--python-libpython=off``.
    """
    if ptr_is_null(env_map):
        return py_subprocess_run(argv, capture_output)
    if load_i8(target_sys_platform(), 0) == 119 or load_i8(target_sys_platform(), 0) == 108:
        child_env = _build_exec_env(env_map)
        if ptr_is_null(child_env):
            return 127
        result: int = _run_exec_argv(argv, capture_output, child_env)
        # Both snapshots and dictionary vectors own their terminated strings.
        platform_env_snapshot_free(child_env)
        return result
    body = _build_shell_command(argv)
    if ptr_is_null(body):
        return 127
    st = _buf_new()
    if ptr_is_null(st):
        free(body)
        return 127
    if _append_env_prefix(st, env_map) != 0:
        _buf_free(st)
        free(body)
        return 127
    if _buf_append(st, body, strlen(body)) != 0:
        _buf_free(st)
        free(body)
        return 127
    free(body)
    cmd = _buf_detach(st)
    if ptr_is_null(cmd):
        return 127
    rc: int = _run_shell_command(cmd, capture_output)
    free(cmd)
    return rc


@c_abi_export("py_subprocess_run")
def py_subprocess_run(argv, capture_output: int) -> int:
    if load_i8(target_sys_platform(), 0) == 119 or load_i8(target_sys_platform(), 0) == 108:
        child_env = platform_env_snapshot()
        if ptr_is_null(child_env):
            return 127
        result: int = _run_exec_argv(argv, capture_output, child_env)
        platform_env_snapshot_free(child_env)
        return result
    cmd = _build_shell_command(argv)
    if ptr_is_null(cmd):
        return 127
    rc: int = _run_shell_command(cmd, capture_output)
    free(cmd)
    return rc


@c_abi_export("py_os_getpid")
def py_os_getpid():
    return py_int_from_i64(getpid())


@c_abi_export("py_os_kill")
def py_os_kill(pid_object, signal_object):
    # Convert through __index__, not int(): floats and oversized integers must
    # fail before any signal is sent. The unsafe platform call returns -errno.
    # A user-defined pid.__index__ can collect and relocate the other argument.
    # Keep its slot registered until both conversions have finished.
    signal_slot = stack_alloc(8)
    store_ptr(signal_slot, 0, signal_object)
    signal_root = pcc_gc_scheduler_root_register_handle(signal_slot)
    if ptr_is_null(signal_root):
        py_raise_owned(py_exc_new(19, cstr("could not root signal argument")))
        return null()
    pid: int = py_index_i64_checked(pid_object)
    if py_err_occurred() != 0:
        pcc_gc_scheduler_root_unregister_handle(signal_root)
        return null()
    if pid < -2147483648 or pid > 2147483647:
        pcc_gc_scheduler_root_unregister_handle(signal_root)
        py_raise_owned(py_exc_new(15, cstr("process id does not fit in pid_t")))
        return null()
    signal_number: int = py_index_i64_checked(load_ptr(signal_slot, 0))
    pcc_gc_scheduler_root_unregister_handle(signal_root)
    if py_err_occurred() != 0:
        return null()
    if signal_number < -2147483648 or signal_number > 2147483647:
        py_raise_owned(py_exc_new(15, cstr("signal number does not fit in C int")))
        return null()
    result: int = platform_kill(pid, signal_number)
    if result == -1 or result == -13:
        py_raise_owned(py_exc_new(36, cstr("permission denied sending signal")))
        return null()
    if result == -3:
        py_raise_owned(py_exc_new(39, cstr("no such process")))
        return null()
    if result != 0:
        py_raise_owned(py_exc_new(14, cstr("could not send signal")))
        return null()
    return global_load_ptr("py_None")


@c_abi_export("py_sys_executable_str")
def py_sys_executable_str():
    arg0 = py_program_executable()
    if ptr_is_null(arg0):
        return _empty_str()
    return py_str_new(arg0, strlen(arg0))


def _python_sys_attr_str(attr):
    code = cstr("import sys; print(getattr(sys, sys.argv[1], ''))")
    st = _buf_new()
    ok: int = 0
    if not ptr_is_null(st):
        if _buf_append(st, cstr("python3 -c "), 11) == 0:
            if _append_shell_quoted(st, code) == 0:
                if _buf_append(st, cstr(" "), 1) == 0:
                    if _append_shell_quoted(st, attr) == 0:
                        ok = 1
    if ok == 0:
        _buf_free(st)
        return _empty_str()
    cmd = _buf_detach(st)
    fp = popen(cmd, cstr("r"))
    free(cmd)
    if ptr_is_null(fp):
        return _empty_str()

    out = _buf_new()
    tmp = malloc(1024)
    if ptr_is_null(out) or ptr_is_null(tmp):
        free(tmp)
        _buf_free(out)
        pclose(fp)
        return _empty_str()
    while True:
        n: int = fread(tmp, 1, 1024, fp)
        if n > 0:
            if _buf_append(out, tmp, n) != 0:
                free(tmp)
                _buf_free(out)
                pclose(fp)
                return _empty_str()
        if n < 1024:
            break
    rc: int = pclose(fp)
    length: int = _buf_len(out)
    data = _buf_data(out)
    while length > 0:
        last: int = load_i8(data, length - 1)
        if last == 10 or last == 13:
            length = length - 1
        else:
            break
    if rc != 0 or ptr_is_null(data):
        free(tmp)
        _buf_free(out)
        return _empty_str()
    result = py_str_new(data, length)
    free(tmp)
    _buf_free(out)
    return result


@c_abi_export("py_sys_prefix_str")
def py_sys_prefix_str(kind: int):
    if kind == 1:
        return _python_sys_attr_str(cstr("base_prefix"))
    return _python_sys_attr_str(cstr("prefix"))


@c_abi_export("py_sysconfig_get_config_var")
def py_sysconfig_get_config_var(name):
    name_str = py_obj_str(name)
    if ptr_is_null(name_str):
        return _none()
    key = py_str_utf8(name_str)
    if ptr_is_null(key) or load_i8(key, 0) == 0:
        py_decref(name_str)
        return _none()

    code = cstr(
        "import sysconfig,sys; "
        "v=sysconfig.get_config_var(sys.argv[1]); "
        "print('' if v is None else v)"
    )
    st = _buf_new()
    ok: int = 0
    if not ptr_is_null(st):
        if _buf_append(st, cstr("python3 -c "), 11) == 0:
            if _append_shell_quoted(st, code) == 0:
                if _buf_append(st, cstr(" "), 1) == 0:
                    if _append_shell_quoted(st, key) == 0:
                        ok = 1
    py_decref(name_str)
    if ok == 0:
        _buf_free(st)
        return _none()
    cmd = _buf_detach(st)
    fp = popen(cmd, cstr("r"))
    free(cmd)
    if ptr_is_null(fp):
        return _none()

    out = _buf_new()
    tmp = malloc(1024)
    if ptr_is_null(out) or ptr_is_null(tmp):
        free(tmp)
        _buf_free(out)
        pclose(fp)
        return _none()
    while True:
        n: int = fread(tmp, 1, 1024, fp)
        if n > 0:
            if _buf_append(out, tmp, n) != 0:
                free(tmp)
                _buf_free(out)
                pclose(fp)
                return _none()
        if n < 1024:
            break
    rc: int = pclose(fp)
    length: int = _buf_len(out)
    data = _buf_data(out)
    while length > 0:
        last: int = load_i8(data, length - 1)
        if last == 10 or last == 13:
            length = length - 1
        else:
            break
    if rc != 0 or ptr_is_null(data) or length == 0:
        free(tmp)
        _buf_free(out)
        return _none()
    result = py_str_new(data, length)
    free(tmp)
    _buf_free(out)
    return result


@c_abi_export("py_os_listdir")
def py_os_listdir(path):
    path_str = py_obj_str(path)
    if ptr_is_null(path_str):
        return null()
    raw = py_str_utf8(path_str)
    stream = directory_open(raw)
    py_decref(path_str)
    if ptr_is_null(stream):
        py_raise_owned(py_exc_new(14, cstr("could not open directory")))
        return null()
    output = py_list_new(0)
    if ptr_is_null(output):
        directory_close(stream)
        return null()
    while True:
        entry = directory_next(stream)
        if ptr_is_null(entry):
            break
        if load_i8(entry, 0) == 46:
            if load_i8(entry, 1) == 0:
                continue
            if load_i8(entry, 1) == 46 and load_i8(entry, 2) == 0:
                continue
        item = py_str_new(entry, strlen(entry))
        if ptr_is_null(item):
            directory_close(stream)
            py_decref(output)
            return null()
        py_list_append(output, item)
        py_decref(item)
    status: int = directory_error(stream)
    directory_close(stream)
    if status < 0:
        py_decref(output)
        py_raise_owned(py_exc_new(14, cstr("could not read directory")))
        return null()
    return output


def _has_path_separator(s) -> int:
    if ptr_is_null(s):
        return 0
    i: int = 0
    while load_i8(s, i) != 0:
        if load_i8(s, i) == 47:
            return 1
        i = i + 1
    return 0


def _which_direct(cmd):
    if ptr_is_null(cmd) or load_i8(cmd, 0) == 0:
        return _none()
    if access(cmd, 1) != 0:
        return _none()
    return py_str_new(cmd, strlen(cmd))


def _shell_is_space(c: int) -> int:
    if c == 32 or c == 9 or c == 10 or c == 13 or c == 12 or c == 11:
        return 1
    return 0


@c_abi_export("py_shlex_split")
def py_shlex_split(text):
    text_str = py_obj_str(text)
    if ptr_is_null(text_str):
        return py_list_new(0)
    raw = py_str_utf8(text_str)
    if ptr_is_null(raw):
        py_decref(text_str)
        return py_list_new(0)
    raw_len: int = strlen(raw)
    buf = malloc(raw_len + 1)
    out = py_list_new(4)
    if ptr_is_null(buf) or ptr_is_null(out):
        free(buf)
        py_decref(text_str)
        if not ptr_is_null(out):
            py_decref(out)
        return py_list_new(0)

    in_single: int = 0
    in_double: int = 0
    escaped: int = 0
    in_token: int = 0
    n: int = 0
    i: int = 0
    while i < raw_len:
        ch: int = load_i8(raw, i)
        if escaped != 0:
            store_i8(buf, n, ch)
            n = n + 1
            escaped = 0
            in_token = 1
            i = i + 1
            continue
        if in_single != 0:
            if ch == 39:
                in_single = 0
            else:
                store_i8(buf, n, ch)
                n = n + 1
            in_token = 1
            i = i + 1
            continue
        if in_double != 0:
            if ch == 34:
                in_double = 0
            elif ch == 92:
                escaped = 1
            else:
                store_i8(buf, n, ch)
                n = n + 1
            in_token = 1
            i = i + 1
            continue
        if _shell_is_space(ch) != 0:
            if in_token != 0:
                part = py_str_new(buf, n)
                py_list_append(out, part)
                py_decref(part)
                n = 0
                in_token = 0
            i = i + 1
            continue
        if ch == 39:
            in_single = 1
            in_token = 1
            i = i + 1
            continue
        if ch == 34:
            in_double = 1
            in_token = 1
            i = i + 1
            continue
        if ch == 92:
            escaped = 1
            in_token = 1
            i = i + 1
            continue
        store_i8(buf, n, ch)
        n = n + 1
        in_token = 1
        i = i + 1
    if escaped != 0:
        store_i8(buf, n, 92)
        n = n + 1
    if in_token != 0:
        part2 = py_str_new(buf, n)
        py_list_append(out, part2)
        py_decref(part2)
    free(buf)
    py_decref(text_str)
    return out


@c_abi_export("py_shutil_which")
def py_shutil_which(name):
    name_str = py_obj_str(name)
    if ptr_is_null(name_str):
        return _none()
    cmd = py_str_utf8(name_str)
    if ptr_is_null(cmd) or load_i8(cmd, 0) == 0:
        py_decref(name_str)
        return _none()
    if _has_path_separator(cmd) != 0:
        direct = _which_direct(cmd)
        py_decref(name_str)
        return direct

    path_env = getenv(cstr("PATH"))
    if ptr_is_null(path_env) or load_i8(path_env, 0) == 0:
        py_decref(name_str)
        return _none()

    cmd_len: int = strlen(cmd)
    path_len: int = strlen(path_env)
    seg: int = 0
    while True:
        end: int = seg
        while end < path_len and load_i8(path_env, end) != 58:
            end = end + 1
        dir_len: int = end - seg
        dir_ptr = ptr_add(path_env, seg)
        if dir_len == 0:
            dir_ptr = cstr(".")
            dir_len = 1
        need_slash: int = 0
        if dir_len > 0:
            if load_i8(dir_ptr, dir_len - 1) != 47:
                need_slash = 1
        total: int = dir_len + need_slash + cmd_len
        candidate = malloc(total + 1)
        if ptr_is_null(candidate):
            py_decref(name_str)
            return _none()
        pos: int = 0
        memcpy(candidate, dir_ptr, dir_len)
        pos = pos + dir_len
        if need_slash != 0:
            store_i8(candidate, pos, 47)
            pos = pos + 1
        memcpy(ptr_add(candidate, pos), cmd, cmd_len)
        pos = pos + cmd_len
        store_i8(candidate, pos, 0)
        if access(candidate, 1) == 0:
            out = py_str_new(candidate, pos)
            free(candidate)
            py_decref(name_str)
            return out
        free(candidate)
        if end >= path_len:
            break
        seg = end + 1
    py_decref(name_str)
    return _none()


@c_abi_export("py_tempdir_new")
def py_tempdir_new(prefix):
    prefix_str = py_obj_str(prefix)
    prefix_raw = py_str_utf8(prefix_str)
    if ptr_is_null(prefix_raw) or load_i8(prefix_raw, 0) == 0:
        prefix_raw = cstr("tmp")
    windows: int = 1 if load_i8(target_sys_platform(), 0) == 119 else 0
    root = getenv(cstr("TMPDIR"))
    if windows and (ptr_is_null(root) or load_i8(root, 0) == 0):
        root = getenv(cstr("TEMP"))
        if ptr_is_null(root) or load_i8(root, 0) == 0:
            root = getenv(cstr("TMP"))
    if ptr_is_null(root) or load_i8(root, 0) == 0:
        root = cstr(".") if windows else cstr("/tmp")
    root_len: int = strlen(root)
    prefix_len: int = strlen(prefix_raw)
    need_slash: int = 0
    if root_len > 0 and load_i8(root, root_len - 1) != 47:
        need_slash = 1
    total: int = root_len + need_slash + prefix_len + 6
    tmpl = malloc(total + 1)
    if ptr_is_null(tmpl):
        py_decref(prefix_str)
        return _empty_str()
    pos: int = 0
    memcpy(tmpl, root, root_len)
    pos = pos + root_len
    if need_slash != 0:
        store_i8(tmpl, pos, 47)
        pos = pos + 1
    memcpy(ptr_add(tmpl, pos), prefix_raw, prefix_len)
    pos = pos + prefix_len
    memcpy(ptr_add(tmpl, pos), cstr("XXXXXX"), 6)
    pos = pos + 6
    store_i8(tmpl, pos, 0)

    made = mkdtemp(tmpl)
    length: int = 0
    if not ptr_is_null(made):
        length = pos
    out = py_str_new(tmpl, length)
    free(tmpl)
    py_decref(prefix_str)
    return out


@c_abi_export("py_tempdir_cleanup")
def py_tempdir_cleanup(path) -> None:
    path_str = py_obj_str(path)
    if ptr_is_null(path_str):
        return
    raw = py_str_utf8(path_str)
    if not ptr_is_null(raw) and load_i8(raw, 0) != 0:
        status: int = remove_tree(raw)
        if status < 0 and status != -2:
            py_raise_owned(py_exc_new(14, cstr("could not remove temporary directory")))
    py_decref(path_str)


@c_abi_export("py_shutil_rmtree")
def py_shutil_rmtree(path, ignore_errors: int):
    path_str = py_obj_str(path)
    if ptr_is_null(path_str):
        if ignore_errors:
            return _none()
        py_raise_owned(py_exc_new(3, cstr("path must be string-like")))
        return null()
    raw = py_str_utf8(path_str)
    status: int = -22
    if not ptr_is_null(raw) and load_i8(raw, 0) != 0:
        # shutil.rmtree refuses a file or a directory symlink. The platform
        # tree primitive deliberately unlinks those when reached as children.
        probe = stack_alloc(1)
        if readlink(raw, probe, 1) < 0 and stat_kind(raw) == 2:
            status = remove_tree(raw)
        else:
            status = -20
    py_decref(path_str)
    if status < 0 and not ignore_errors:
        py_raise_owned(py_exc_new(14, cstr("could not remove directory tree")))
        return null()
    return _none()
