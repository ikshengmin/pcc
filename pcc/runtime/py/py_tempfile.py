"""Owned first-class TemporaryDirectory provider.

The manager is an ordinary managed instance. A registered cleanup state owns a
weak reference, never the manager; aliases and bound methods therefore keep the
resource alive without making the finalizer keep its referent alive. Every
managed edge uses the existing class/list/tuple/weakref slot contract.

Currently supported filesystem arguments are str or None. Bytes and custom
PathLike inputs fail explicitly; their encoding contract is not approximated by
str(). ResourceWarning filtering is still an owned-runtime capability gap.
"""

__pcc_runtime_port__ = True

from pcc.extern import extern, c_abi_export, c_int32, c_int64, c_ptr, c_void
from pcc.runtime.py.py_abi_constants import (
    C_POINTER_SIZE, PYOBJECTHEADER_FLAGS_OFFSET, PYOBJECTHEADER_TYPE_TAG_OFFSET,
    PY_FLAG_GC_PINNED, PY_TYPE_STR, PY_TYPE_BYTES, PY_TYPE_LIST,
    PYSTROBJECT_BYTE_LEN_OFFSET,
)
from pcc.unsafe import (
    atomic_cas_i64, atomic_load_i64, cstr, function_addr, define_global_i32,
    define_global_i64, define_global_ptr_null, free, global_addr,
    global_load_ptr, global_store_ptr, int_to_ptr, is_tagged_int,
    load_i8, load_i32, load_i64, load_ptr, malloc, memcpy, memset,
    null, ptr_add, ptr_eq, ptr_is_null, ptr_to_int, readlink,
    stack_alloc, stat_kind, store_i8, store_i32, store_i64, store_ptr,
    strlen, target_sys_platform,
)

py_incref = extern("py_incref", (c_ptr,), c_void)
py_decref = extern("py_decref", (c_ptr,), c_void)
py_err_occurred = extern("py_err_occurred", (), c_int64)
py_clear_exception = extern("py_clear_exception", (), c_void)
py_exc_new = extern("py_exc_new", (c_int64, c_ptr), c_ptr)
py_raise_owned = extern("py_raise_owned", (c_ptr,), c_void)
py_str_new = extern("py_str_new", (c_ptr, c_int64), c_ptr)
py_str_utf8 = extern("py_str_utf8", (c_ptr,), c_ptr)
py_str_concat = extern("py_str_concat", (c_ptr, c_ptr), c_ptr)
py_obj_repr = extern("py_obj_repr", (c_ptr,), c_ptr)
py_obj_truthy = extern("py_obj_truthy", (c_ptr,), c_int64)
py_obj_getattr = extern("py_obj_getattr", (c_ptr, c_ptr), c_ptr)
py_obj_call = extern("py_obj_call", (c_ptr, c_ptr, c_ptr), c_ptr)
py_obj_setattr = extern("py_obj_setattr", (c_ptr, c_ptr, c_ptr), c_int64)
py_int_from_i64 = extern("py_int_from_i64", (c_int64,), c_ptr)
py_int_value_i64 = extern("py_int_value_i64", (c_ptr,), c_int64)
py_list_new = extern("py_list_new", (c_int64,), c_ptr)
py_list_get = extern("py_list_get", (c_ptr, c_int64), c_ptr)
py_list_len = extern("py_list_len", (c_ptr,), c_int64)
py_list_set = extern("py_list_set", (c_ptr, c_int64, c_ptr), c_void)
py_tuple_new = extern("py_tuple_new", (c_int64,), c_ptr)
py_tuple_get = extern("py_tuple_get", (c_ptr, c_int64), c_ptr)
py_tuple_len = extern("py_tuple_len", (c_ptr,), c_int64)
py_tuple_set_item = extern("py_tuple_set_item", (c_ptr, c_int64, c_ptr), c_void)
py_func_new_named = extern("py_func_new_named", (c_ptr, c_ptr, c_ptr), c_ptr)
py_class_new = extern("py_class_new", (c_ptr, c_ptr, c_int32, c_ptr, c_int32), c_ptr)
py_class_setattr = extern("py_class_setattr", (c_ptr, c_ptr, c_ptr), c_int64)
py_weakref_new = extern("py_weakref_new", (c_ptr, c_ptr), c_ptr)
py_os_path_abspath = extern("py_os_path_abspath", (c_ptr,), c_ptr)
py_os_path_exists = extern("py_os_path_exists", (c_ptr,), c_int64)
py_tls_exc_swap_slot = extern("py_tls_exc_swap_slot", (c_ptr,), c_void)
pcc_gc_load_ptr = extern("pcc_gc_load_ptr", (c_ptr, c_ptr), c_ptr)
pcc_py_gc_minor_graph_lock = extern("pcc_py_gc_minor_graph_lock", (), c_void)
pcc_py_gc_minor_graph_unlock = extern("pcc_py_gc_minor_graph_unlock", (), c_void)
pcc_gc_pin = extern("pcc_gc_pin", (c_ptr,), c_void)
pcc_gc_unpin = extern("pcc_gc_unpin", (c_ptr,), c_void)
pcc_gc_frame_enter = extern("pcc_gc_frame_enter", (c_ptr, c_ptr), c_void)
pcc_gc_frame_leave = extern("pcc_gc_frame_leave", (c_ptr,), c_void)
pcc_gc_store_root = extern("pcc_gc_store_root", (c_ptr, c_ptr), c_void)
pcc_gc_take_pinned_slot = extern("pcc_gc_take_pinned_slot", (c_ptr, c_int64), c_ptr)
pcc_gc_note_write_barrier = extern("pcc_gc_note_write_barrier", (c_ptr, c_ptr), c_void)
pcc_gc_scheduler_root_register_handle = extern("pcc_gc_scheduler_root_register_handle", (c_ptr,), c_ptr)
pcc_gc_scheduler_root_unregister_handle = extern("pcc_gc_scheduler_root_unregister_handle", (c_ptr,), c_void)
pcc_gc_root_copy_lease = extern("pcc_gc_root_copy_lease", (c_ptr, c_ptr), c_int64)
pcc_gc_foreign_lease_release = extern("pcc_gc_foreign_lease_release", (c_ptr, c_int64), c_int64)
pcc_mutex_new = extern("pcc_mutex_new", (), c_ptr)
pcc_mutex_free = extern("pcc_mutex_free", (c_ptr,), c_void)
pcc_mutex_lock = extern("pcc_mutex_lock", (c_ptr,), c_int64)
pcc_mutex_unlock = extern("pcc_mutex_unlock", (c_ptr,), c_int64)
getenv = extern("pcc_platform_getenv", (c_ptr,), c_ptr)
mkdtemps = extern("pcc_platform_mkdtemp_suffix", (c_ptr, c_int64), c_int64)
remove_tree = extern("pcc_platform_tempdir_remove_tree", (c_ptr,), c_int64)
atexit = extern("atexit", (c_ptr,), c_int32)
errno_message = extern("pcc_errno_message_into", (c_int32, c_ptr, c_int64), c_int32)

# Named fixed frame, shared by leaf entrypoints. Every slot owns one reference.
_TD_SLOTS = 24
_TD_OUTPUT = 23
# Registered native record: only state is managed, with its own root handle.
_TD_NEXT = 0
_TD_PREVIOUS = 8
_TD_STATE = 16
_TD_HANDLE = 24
_TD_RECORD_SIZE = 32
# Managed finalizer state: no strong edge points back to its manager.
_TD_NAME = 0
_TD_IGNORE = 1
_TD_DELETE = 2
_TD_WEAKREF = 3
_TD_SAVED_ERROR = 22

define_global_i32("pcc_tempdir_frame_map", _TD_SLOTS)
define_global_i64("pcc_tempdir_mutex_bits", 0)
define_global_ptr_null("pcc_tempdir_class")
define_global_ptr_null("pcc_tempdir_records")


def _td_none():
    return global_load_ptr("py_None")


def _td_error(tag: int, message):
    py_raise_owned(py_exc_new(tag, message))
    return null()


def _td_hold(slots, pins, index: int, value) -> None:
    # A NEW return enters its registered slot before the next safepoint.
    offset: int = index * C_POINTER_SIZE
    if ptr_is_null(value) and not py_err_occurred():
        _td_error(19, cstr("TemporaryDirectory owned object allocation failed"))
    store_ptr(slots, offset, value)
    if not ptr_is_null(value) and not is_tagged_int(value):
        store_i64(pins, offset, load_i32(value, PYOBJECTHEADER_FLAGS_OFFSET) & PY_FLAG_GC_PINNED)
        pcc_gc_pin(value)
        pcc_gc_note_write_barrier(null(), value)


def _td_drop(slots, pins, index: int) -> None:
    offset: int = index * C_POINTER_SIZE
    value = load_ptr(slots, offset)
    if not ptr_is_null(value) and not is_tagged_int(value):
        pcc_gc_unpin(value)
        keep_result: int = ptr_eq(value, load_ptr(slots, _TD_OUTPUT * C_POINTER_SIZE))
        if keep_result and not load_i64(pins, offset):
            store_i64(pins, _TD_OUTPUT * C_POINTER_SIZE, 0)
        if load_i64(pins, offset) or keep_result:
            store_i32(value, PYOBJECTHEADER_FLAGS_OFFSET,
                      load_i32(value, PYOBJECTHEADER_FLAGS_OFFSET) | PY_FLAG_GC_PINNED)
    pcc_gc_store_root(ptr_add(slots, offset), null())
    store_i64(pins, offset, 0)


def _td_finish(slots, pins):
    index: int = _TD_OUTPUT
    while index > 0:
        index = index - 1
        _td_drop(slots, pins, index)
    pcc_gc_frame_leave(slots)
    return pcc_gc_take_pinned_slot(ptr_add(slots, _TD_OUTPUT * C_POINTER_SIZE),
                                   load_i64(pins, _TD_OUTPUT * C_POINTER_SIZE))


def _td_mutex():
    slot = global_addr("pcc_tempdir_mutex_bits")
    bits: int = atomic_load_i64(slot, 0, "acquire")
    if bits != 0:
        return int_to_ptr(bits)
    candidate = pcc_mutex_new()
    if ptr_is_null(candidate):
        _td_error(19, cstr("TemporaryDirectory registry allocation failed"))
        return null()
    previous: int = atomic_cas_i64(slot, 0, 0, ptr_to_int(candidate), "acq_rel", "acquire")
    if previous != 0:
        pcc_mutex_free(candidate)
        return int_to_ptr(previous)
    return candidate


def _td_os_error(status: int, name):
    tag: int = 14
    if status == -2:
        tag = 34
    elif status == -17:
        tag = 35
    elif status == -13 or status == -1:
        tag = 36
    elif status == -20:
        tag = 38
    elif status == -12:
        tag = 19
    slots = stack_alloc(_TD_SLOTS * C_POINTER_SIZE)
    pins = stack_alloc(_TD_SLOTS * C_POINTER_SIZE)
    memset(slots, 0, _TD_SLOTS * C_POINTER_SIZE)
    memset(pins, 0, _TD_SLOTS * C_POINTER_SIZE)
    pcc_gc_frame_enter(global_addr("pcc_tempdir_frame_map"), slots)
    py_incref(name)
    _td_hold(slots, pins, 0, name)
    message = stack_alloc(256)
    errno_message(0 - status, message, 256)
    _td_hold(slots, pins, 1, py_exc_new(tag, message))
    _td_hold(slots, pins, 2, py_int_from_i64(0 - status))
    _td_hold(slots, pins, 3, py_str_new(message, strlen(message)))
    _td_hold(slots, pins, 4, py_tuple_new(2))
    exc = load_ptr(slots, C_POINTER_SIZE)
    if not ptr_is_null(exc) and not py_err_occurred():
        py_tuple_set_item(load_ptr(slots, 4 * C_POINTER_SIZE), 0, load_ptr(slots, 2 * C_POINTER_SIZE))
        py_tuple_set_item(load_ptr(slots, 4 * C_POINTER_SIZE), 1, load_ptr(slots, 3 * C_POINTER_SIZE))
        py_obj_setattr(exc, cstr("errno"), load_ptr(slots, 2 * C_POINTER_SIZE))
        py_obj_setattr(exc, cstr("strerror"), load_ptr(slots, 3 * C_POINTER_SIZE))
        py_obj_setattr(exc, cstr("filename"), load_ptr(slots, 0))
        py_obj_setattr(exc, cstr("args"), load_ptr(slots, 4 * C_POINTER_SIZE))
        if not py_err_occurred():
            py_incref(exc)
            py_raise_owned(exc)
    return _td_finish(slots, pins)


def _td_text(value, default):
    if ptr_eq(value, _td_none()):
        return default
    if ptr_is_null(value) or is_tagged_int(value):
        return _td_error(3, cstr("TemporaryDirectory paths must be strings"))
    tag: int = load_i32(value, PYOBJECTHEADER_TYPE_TAG_OFFSET)
    if tag != PY_TYPE_STR:
        if tag == PY_TYPE_BYTES:
            return _td_error(11, cstr("pcc TemporaryDirectory: bytes paths require an owned filesystem encoding provider"))
        return _td_error(11, cstr("pcc TemporaryDirectory: custom PathLike arguments require an owned fspath provider"))
    raw = py_str_utf8(value)
    if ptr_is_null(raw):
        return null()
    # utf8 byte length is the native string payload length, not code points.
    length: int = load_i64(value, PYSTROBJECT_BYTE_LEN_OFFSET)
    index: int = 0
    while index < length:
        if load_i8(raw, index) == 0:
            return _td_error(2, cstr("embedded null character"))
        index = index + 1
    return raw


def _td_mkdir_at(root, prefix, suffix, status):
    previous_failure = load_ptr(status, C_POINTER_SIZE)
    if not ptr_is_null(previous_failure):
        free(previous_failure)
    store_ptr(status, C_POINTER_SIZE, null())
    rlen: int = strlen(root)
    if load_i8(prefix, 0) == 47:
        rlen = 0
    plen: int = strlen(prefix)
    slen: int = strlen(suffix)
    slash: int = 0
    if rlen > 0 and load_i8(root, rlen - 1) != 47:
        slash = 1
    size: int = rlen + slash + plen + 6 + slen
    path = malloc(size + 1)
    if ptr_is_null(path):
        store_i64(status, 0, -12)
        return null()
    memcpy(path, root, rlen)
    if slash:
        store_i8(path, rlen, 47)
    memcpy(ptr_add(path, rlen + slash), prefix, plen)
    memcpy(ptr_add(path, rlen + slash + plen), cstr("XXXXXX"), 6)
    memcpy(ptr_add(path, size - slen), suffix, slen)
    store_i8(path, size, 0)
    result: int = mkdtemps(path, slen)
    store_i64(status, 0, result)
    if result < 0:
        store_ptr(status, C_POINTER_SIZE, path)
        return null()
    return path


def _td_mkdtemp(suffix, prefix, directory, slots, pins):
    if load_i8(target_sys_platform(), 0) == 119:
        return _td_error(11, cstr("pcc TemporaryDirectory: Windows filesystem provider is not implemented"))
    suffix_raw = _td_text(suffix, cstr(""))
    if ptr_is_null(suffix_raw):
        return null()
    prefix_raw = _td_text(prefix, cstr("tmp"))
    if ptr_is_null(prefix_raw):
        return null()
    status = stack_alloc(16)
    store_i64(status, 0, -2)
    store_ptr(status, C_POINTER_SIZE, null())
    path = null()
    if not ptr_eq(directory, _td_none()):
        root = _td_text(directory, cstr(""))
        if ptr_is_null(root):
            return null()
        path = _td_mkdir_at(root, prefix_raw, suffix_raw, status)
    else:
        # CPython's default directory search order. A failed candidate does
        # not become an empty successful name and does not suppress errors.
        candidate: int = 0
        while candidate < 7 and ptr_is_null(path):
            root = null()
            if candidate == 0:
                root = getenv(cstr("TMPDIR"))
            elif candidate == 1:
                root = getenv(cstr("TEMP"))
            elif candidate == 2:
                root = getenv(cstr("TMP"))
            elif candidate == 3:
                root = cstr("/tmp")
            elif candidate == 4:
                root = cstr("/var/tmp")
            elif candidate == 5:
                root = cstr("/usr/tmp")
            else:
                root = cstr(".")
            if not ptr_is_null(root) and load_i8(root, 0) != 0:
                # Select the root independently of user prefix/suffix errors.
                # Once a writable candidate is found, errors in the requested
                # name must propagate instead of trying a different directory.
                probe_path = _td_mkdir_at(root, cstr(".pcc_probe_"), cstr(""), status)
                if not ptr_is_null(probe_path):
                    probe_status: int = remove_tree(probe_path)
                    if probe_status < 0:
                        store_i64(status, 0, probe_status)
                        store_ptr(status, C_POINTER_SIZE, probe_path)
                        break
                    free(probe_path)
                    path = _td_mkdir_at(root, prefix_raw, suffix_raw, status)
                    break
            candidate = candidate + 1
    if ptr_is_null(path):
        failed_path = load_ptr(status, C_POINTER_SIZE)
        if not ptr_is_null(failed_path):
            _td_hold(slots, pins, 8, py_str_new(failed_path, strlen(failed_path)))
            free(failed_path)
            if not ptr_is_null(load_ptr(slots, 8 * C_POINTER_SIZE)):
                return _td_os_error(load_i64(status, 0), load_ptr(slots, 8 * C_POINTER_SIZE))
            return null()
        return _td_os_error(load_i64(status, 0), directory)
    _td_hold(slots, pins, 8, py_str_new(path, strlen(path)))
    if ptr_is_null(load_ptr(slots, 8 * C_POINTER_SIZE)):
        remove_tree(path)
        free(path)
        return null()
    # Since 3.12 mkdtemp returns an absolute path even for relative dir.
    result = py_os_path_abspath(load_ptr(slots, 8 * C_POINTER_SIZE))
    if ptr_is_null(result):
        remove_tree(path)
    free(path)
    return result


def _td_remove(name, ignore) -> None:
    raw = _td_text(name, null())
    if ptr_is_null(raw):
        return
    ignored: int = py_obj_truthy(ignore)
    if py_err_occurred():
        return
    # Never follow or unlink a replaced root symlink as if it were a tree.
    probe = stack_alloc(1)
    if readlink(raw, probe, 1) >= 0:
        # shutil.rmtree's root symlink refusal is OSError without an errno.
        # ignore_cleanup_errors does not authorize following the target.
        if ignored == 0:
            _td_error(14, cstr("Cannot call rmtree on a symbolic link"))
        return
    status: int = remove_tree(raw)
    if status < 0 and status != -2 and ignored == 0:
        _td_os_error(status, name)


def _td_register(state) -> int:
    record = malloc(_TD_RECORD_SIZE)
    if ptr_is_null(record):
        _td_error(19, cstr("TemporaryDirectory finalizer allocation failed"))
        return -1
    memset(record, 0, _TD_RECORD_SIZE)
    handle = pcc_gc_scheduler_root_register_handle(ptr_add(record, _TD_STATE))
    if ptr_is_null(handle):
        free(record)
        _td_error(19, cstr("TemporaryDirectory finalizer root registration failed"))
        return -1
    store_ptr(record, _TD_HANDLE, handle)
    pcc_gc_store_root(ptr_add(record, _TD_STATE), state)
    mutex = _td_mutex()
    if ptr_is_null(mutex):
        pcc_gc_scheduler_root_unregister_handle(handle)
        pcc_gc_store_root(ptr_add(record, _TD_STATE), null())
        free(record)
        return -1
    pcc_mutex_lock(mutex)
    previous = global_load_ptr("pcc_tempdir_records")
    store_ptr(record, _TD_NEXT, previous)
    if not ptr_is_null(previous):
        store_ptr(previous, _TD_PREVIOUS, record)
    global_store_ptr("pcc_tempdir_records", record)
    pcc_mutex_unlock(mutex)
    return 0


def _td_detach(state) -> int:
    mutex = _td_mutex()
    if ptr_is_null(mutex):
        return 0
    pcc_mutex_lock(mutex)
    # State identity is resolved under the shared graph lock. No native
    # address is exposed in mutable Python attributes or collection items.
    pcc_py_gc_minor_graph_lock()
    record = global_load_ptr("pcc_tempdir_records")
    while not ptr_is_null(record):
        current = pcc_gc_load_ptr(null(), ptr_add(record, _TD_STATE))
        if ptr_eq(current, state):
            break
        record = load_ptr(record, _TD_NEXT)
    pcc_py_gc_minor_graph_unlock()
    if ptr_is_null(record):
        pcc_mutex_unlock(mutex)
        return 0
    # Unlink before dropping a weakref or registry owner can call arbitrary
    # finalizers. Repeated/manual/weakref/shutdown paths converge here.
    previous = load_ptr(record, _TD_PREVIOUS)
    following = load_ptr(record, _TD_NEXT)
    if ptr_is_null(previous):
        global_store_ptr("pcc_tempdir_records", following)
    else:
        store_ptr(previous, _TD_NEXT, following)
    if not ptr_is_null(following):
        store_ptr(following, _TD_PREVIOUS, previous)
    pcc_mutex_unlock(mutex)
    py_list_set(state, _TD_WEAKREF, _td_none())
    pcc_gc_scheduler_root_unregister_handle(load_ptr(record, _TD_HANDLE))
    pcc_gc_store_root(ptr_add(record, _TD_STATE), null())
    free(record)
    return 1


def _td_finalize_state(state) -> None:
    slots = stack_alloc(_TD_SLOTS * C_POINTER_SIZE)
    pins = stack_alloc(_TD_SLOTS * C_POINTER_SIZE)
    memset(slots, 0, _TD_SLOTS * C_POINTER_SIZE)
    memset(pins, 0, _TD_SLOTS * C_POINTER_SIZE)
    pcc_gc_frame_enter(global_addr("pcc_tempdir_frame_map"), slots)
    py_incref(state)
    _td_hold(slots, pins, 0, state)
    if _td_detach(load_ptr(slots, 0)):
        _td_hold(slots, pins, 1, py_list_get(load_ptr(slots, 0), _TD_DELETE))
        should_delete: int = py_obj_truthy(load_ptr(slots, C_POINTER_SIZE))
        if should_delete and not py_err_occurred():
            _td_hold(slots, pins, 2, py_list_get(load_ptr(slots, 0), _TD_NAME))
            _td_hold(slots, pins, 3, py_list_get(load_ptr(slots, 0), _TD_IGNORE))
            _td_remove(load_ptr(slots, 2 * C_POINTER_SIZE), load_ptr(slots, 3 * C_POINTER_SIZE))
    _td_finish(slots, pins)


def _td_weakref_callback(captures, args):
    slots = stack_alloc(_TD_SLOTS * C_POINTER_SIZE)
    pins = stack_alloc(_TD_SLOTS * C_POINTER_SIZE)
    memset(slots, 0, _TD_SLOTS * C_POINTER_SIZE)
    memset(pins, 0, _TD_SLOTS * C_POINTER_SIZE)
    pcc_gc_frame_enter(global_addr("pcc_tempdir_frame_map"), slots)
    _td_hold(slots, pins, 0, py_tuple_get(captures, 0))
    if not ptr_is_null(load_ptr(slots, 0)):
        _td_finalize_state(load_ptr(slots, 0))
    if not py_err_occurred():
        py_incref(_td_none())
        _td_hold(slots, pins, _TD_OUTPUT, _td_none())
    return _td_finish(slots, pins)


def _td_shutdown() -> None:
    # Reverse creation order, as weakref.finalize's default atexit policy.
    slots = stack_alloc(_TD_SLOTS * C_POINTER_SIZE)
    pins = stack_alloc(_TD_SLOTS * C_POINTER_SIZE)
    memset(slots, 0, _TD_SLOTS * C_POINTER_SIZE)
    memset(pins, 0, _TD_SLOTS * C_POINTER_SIZE)
    pcc_gc_frame_enter(global_addr("pcc_tempdir_frame_map"), slots)
    mutex = _td_mutex()
    while not ptr_is_null(mutex):
        pcc_mutex_lock(mutex)
        record = global_load_ptr("pcc_tempdir_records")
        token: int = -1
        if not ptr_is_null(record):
            token = pcc_gc_root_copy_lease(slots, ptr_add(record, _TD_STATE))
        pcc_mutex_unlock(mutex)
        if ptr_is_null(record) or token < 0:
            break
        _td_finalize_state(load_ptr(slots, 0))
        pcc_gc_foreign_lease_release(slots, token)
        pcc_gc_store_root(slots, null())
        py_clear_exception()
    _td_finish(slots, pins)


@c_abi_export("pcc_tempdir_init_entry")
def _td_init(captures, args):
    slots = stack_alloc(_TD_SLOTS * C_POINTER_SIZE)
    pins = stack_alloc(_TD_SLOTS * C_POINTER_SIZE)
    memset(slots, 0, _TD_SLOTS * C_POINTER_SIZE)
    memset(pins, 0, _TD_SLOTS * C_POINTER_SIZE)
    pcc_gc_frame_enter(global_addr("pcc_tempdir_frame_map"), slots)
    index: int = 0
    while index < 6:
        _td_hold(slots, pins, index, py_tuple_get(args, index))
        index = index + 1
    _td_hold(slots, pins, 6, _td_mkdtemp(load_ptr(slots, C_POINTER_SIZE),
        load_ptr(slots, 2 * C_POINTER_SIZE), load_ptr(slots, 3 * C_POINTER_SIZE), slots, pins))
    if not ptr_is_null(load_ptr(slots, 6 * C_POINTER_SIZE)):
        _td_hold(slots, pins, 7, py_list_new(4))
        state = load_ptr(slots, 7 * C_POINTER_SIZE)
        if not ptr_is_null(state):
            py_list_set(state, _TD_NAME, load_ptr(slots, 6 * C_POINTER_SIZE))
            py_list_set(state, _TD_IGNORE, load_ptr(slots, 4 * C_POINTER_SIZE))
            py_list_set(state, _TD_DELETE, load_ptr(slots, 5 * C_POINTER_SIZE))
            py_list_set(state, _TD_WEAKREF, _td_none())
            _td_hold(slots, pins, 9, py_tuple_new(1))
            if not ptr_is_null(load_ptr(slots, 9 * C_POINTER_SIZE)):
                py_tuple_set_item(load_ptr(slots, 9 * C_POINTER_SIZE), 0, state)
                _td_hold(slots, pins, 10, py_func_new_named(_td_weakref_callback,
                    load_ptr(slots, 9 * C_POINTER_SIZE), cstr("TemporaryDirectory finalizer")))
                if not ptr_is_null(load_ptr(slots, 10 * C_POINTER_SIZE)):
                    _td_hold(slots, pins, 11, py_weakref_new(load_ptr(slots, 0),
                        load_ptr(slots, 10 * C_POINTER_SIZE)))
                    if not ptr_is_null(load_ptr(slots, 11 * C_POINTER_SIZE)):
                        py_list_set(state, _TD_WEAKREF, load_ptr(slots, 11 * C_POINTER_SIZE))
                        if _td_register(state) == 0:
                            py_obj_setattr(load_ptr(slots, 0), cstr("name"), load_ptr(slots, 6 * C_POINTER_SIZE))
                            py_obj_setattr(load_ptr(slots, 0), cstr("_ignore_cleanup_errors"), load_ptr(slots, 4 * C_POINTER_SIZE))
                            py_obj_setattr(load_ptr(slots, 0), cstr("_delete"), load_ptr(slots, 5 * C_POINTER_SIZE))
                            py_obj_setattr(load_ptr(slots, 0), cstr("_pcc_finalizer_state"), state)
                            if not py_err_occurred():
                                py_incref(_td_none())
                                _td_hold(slots, pins, _TD_OUTPUT, _td_none())
        if ptr_is_null(load_ptr(slots, _TD_OUTPUT * C_POINTER_SIZE)):
            # Preserve the constructor error while cleanup executes. A pending
            # exception must neither skip rollback nor be replaced by it.
            py_tls_exc_swap_slot(ptr_add(slots, _TD_SAVED_ERROR * C_POINTER_SIZE))
            saved = load_ptr(slots, _TD_SAVED_ERROR * C_POINTER_SIZE)
            if not ptr_is_null(saved):
                _td_hold(slots, pins, _TD_SAVED_ERROR, saved)
            if not ptr_is_null(load_ptr(slots, 7 * C_POINTER_SIZE)):
                _td_detach(load_ptr(slots, 7 * C_POINTER_SIZE))
            # A failed constructor never leaks a directory, even delete=False.
            _td_remove(load_ptr(slots, 6 * C_POINTER_SIZE), global_load_ptr("py_True"))
            py_clear_exception()
            saved = load_ptr(slots, _TD_SAVED_ERROR * C_POINTER_SIZE)
            if not ptr_is_null(saved):
                pcc_gc_unpin(saved)
                if load_i64(pins, _TD_SAVED_ERROR * C_POINTER_SIZE):
                    store_i32(saved, PYOBJECTHEADER_FLAGS_OFFSET,
                              load_i32(saved, PYOBJECTHEADER_FLAGS_OFFSET) | PY_FLAG_GC_PINNED)
                py_tls_exc_swap_slot(ptr_add(slots, _TD_SAVED_ERROR * C_POINTER_SIZE))
            if not py_err_occurred():
                _td_error(19, cstr("TemporaryDirectory construction failed"))
    return _td_finish(slots, pins)


@c_abi_export("pcc_tempdir_enter_entry")
def _td_enter(captures, args):
    if py_tuple_len(args) != 1:
        return _td_error(3, cstr("TemporaryDirectory.__enter__ expects no arguments"))
    slots = stack_alloc(_TD_SLOTS * C_POINTER_SIZE)
    pins = stack_alloc(_TD_SLOTS * C_POINTER_SIZE)
    memset(slots, 0, _TD_SLOTS * C_POINTER_SIZE)
    memset(pins, 0, _TD_SLOTS * C_POINTER_SIZE)
    pcc_gc_frame_enter(global_addr("pcc_tempdir_frame_map"), slots)
    _td_hold(slots, pins, 0, py_tuple_get(args, 0))
    _td_hold(slots, pins, _TD_OUTPUT, py_obj_getattr(load_ptr(slots, 0), cstr("name")))
    return _td_finish(slots, pins)


def _td_cleanup_manager(manager) -> None:
    slots = stack_alloc(_TD_SLOTS * C_POINTER_SIZE)
    pins = stack_alloc(_TD_SLOTS * C_POINTER_SIZE)
    memset(slots, 0, _TD_SLOTS * C_POINTER_SIZE)
    memset(pins, 0, _TD_SLOTS * C_POINTER_SIZE)
    pcc_gc_frame_enter(global_addr("pcc_tempdir_frame_map"), slots)
    py_incref(manager)
    _td_hold(slots, pins, 0, manager)
    _td_hold(slots, pins, 1, py_obj_getattr(load_ptr(slots, 0), cstr("_pcc_finalizer_state")))
    if not ptr_is_null(load_ptr(slots, C_POINTER_SIZE)):
        detached: int = _td_detach(load_ptr(slots, C_POINTER_SIZE))
        _td_hold(slots, pins, 2, py_obj_getattr(load_ptr(slots, 0), cstr("name")))
        if not ptr_is_null(load_ptr(slots, 2 * C_POINTER_SIZE)):
            if detached or py_os_path_exists(load_ptr(slots, 2 * C_POINTER_SIZE)):
                _td_hold(slots, pins, 3, py_obj_getattr(load_ptr(slots, 0), cstr("_ignore_cleanup_errors")))
                if not ptr_is_null(load_ptr(slots, 3 * C_POINTER_SIZE)):
                    _td_remove(load_ptr(slots, 2 * C_POINTER_SIZE), load_ptr(slots, 3 * C_POINTER_SIZE))
    _td_finish(slots, pins)


@c_abi_export("pcc_tempdir_cleanup_entry")
def _td_cleanup(captures, args):
    if py_tuple_len(args) != 1:
        return _td_error(3, cstr("TemporaryDirectory.cleanup expects no arguments"))
    slots = stack_alloc(_TD_SLOTS * C_POINTER_SIZE)
    pins = stack_alloc(_TD_SLOTS * C_POINTER_SIZE)
    memset(slots, 0, _TD_SLOTS * C_POINTER_SIZE)
    memset(pins, 0, _TD_SLOTS * C_POINTER_SIZE)
    pcc_gc_frame_enter(global_addr("pcc_tempdir_frame_map"), slots)
    _td_hold(slots, pins, 0, py_tuple_get(args, 0))
    if not ptr_is_null(load_ptr(slots, 0)):
        _td_cleanup_manager(load_ptr(slots, 0))
    if not py_err_occurred():
        py_incref(_td_none())
        _td_hold(slots, pins, _TD_OUTPUT, _td_none())
    return _td_finish(slots, pins)


@c_abi_export("pcc_tempdir_exit_entry")
def _td_exit(captures, args):
    if py_tuple_len(args) != 4:
        return _td_error(3, cstr("TemporaryDirectory.__exit__ expects three arguments"))
    slots = stack_alloc(_TD_SLOTS * C_POINTER_SIZE)
    pins = stack_alloc(_TD_SLOTS * C_POINTER_SIZE)
    memset(slots, 0, _TD_SLOTS * C_POINTER_SIZE)
    memset(pins, 0, _TD_SLOTS * C_POINTER_SIZE)
    pcc_gc_frame_enter(global_addr("pcc_tempdir_frame_map"), slots)
    _td_hold(slots, pins, 0, py_tuple_get(args, 0))
    _td_hold(slots, pins, 1, py_obj_getattr(load_ptr(slots, 0), cstr("_delete")))
    if not ptr_is_null(load_ptr(slots, C_POINTER_SIZE)):
        should_delete: int = py_obj_truthy(load_ptr(slots, C_POINTER_SIZE))
        if should_delete and not py_err_occurred():
            # Python resolves self.cleanup at exit time. Instance overrides and
            # subclass methods keep their ordinary binding/exception behavior.
            _td_hold(slots, pins, 2, py_obj_getattr(load_ptr(slots, 0), cstr("cleanup")))
            _td_hold(slots, pins, 3, py_tuple_new(0))
            if not py_err_occurred():
                _td_hold(slots, pins, 4, py_obj_call(load_ptr(slots, 2 * C_POINTER_SIZE),
                    load_ptr(slots, 3 * C_POINTER_SIZE), null()))
        if not py_err_occurred():
            py_incref(_td_none())
            _td_hold(slots, pins, _TD_OUTPUT, _td_none())
    return _td_finish(slots, pins)


@c_abi_export("pcc_tempdir_repr_entry")
def _td_repr(captures, args):
    slots = stack_alloc(_TD_SLOTS * C_POINTER_SIZE)
    pins = stack_alloc(_TD_SLOTS * C_POINTER_SIZE)
    memset(slots, 0, _TD_SLOTS * C_POINTER_SIZE)
    memset(pins, 0, _TD_SLOTS * C_POINTER_SIZE)
    pcc_gc_frame_enter(global_addr("pcc_tempdir_frame_map"), slots)
    _td_hold(slots, pins, 0, py_tuple_get(args, 0))
    _td_hold(slots, pins, 1, py_obj_getattr(load_ptr(slots, 0), cstr("name")))
    if not ptr_is_null(load_ptr(slots, C_POINTER_SIZE)):
        _td_hold(slots, pins, 2, py_obj_repr(load_ptr(slots, C_POINTER_SIZE)))
        _td_hold(slots, pins, 3, py_str_new(cstr("<TemporaryDirectory "), 20))
        if not ptr_is_null(load_ptr(slots, 2 * C_POINTER_SIZE)) and not ptr_is_null(load_ptr(slots, 3 * C_POINTER_SIZE)):
            _td_hold(slots, pins, 4, py_str_concat(load_ptr(slots, 3 * C_POINTER_SIZE), load_ptr(slots, 2 * C_POINTER_SIZE)))
            _td_hold(slots, pins, 5, py_str_new(cstr(">"), 1))
            if not ptr_is_null(load_ptr(slots, 4 * C_POINTER_SIZE)) and not ptr_is_null(load_ptr(slots, 5 * C_POINTER_SIZE)):
                _td_hold(slots, pins, _TD_OUTPUT, py_str_concat(load_ptr(slots, 4 * C_POINTER_SIZE), load_ptr(slots, 5 * C_POINTER_SIZE)))
    return _td_finish(slots, pins)


def _td_init_signature(slots, pins):
    _td_hold(slots, pins, 1, py_tuple_new(6))
    _td_hold(slots, pins, 2, py_tuple_new(6))
    _td_hold(slots, pins, 3, py_tuple_new(6))
    _td_hold(slots, pins, 4, py_tuple_new(6))
    index: int = 0
    while index < 6:
        name = cstr("self")
        if index == 1:
            name = cstr("suffix")
        elif index == 2:
            name = cstr("prefix")
        elif index == 3:
            name = cstr("dir")
        elif index == 4:
            name = cstr("ignore_cleanup_errors")
        elif index == 5:
            name = cstr("delete")
        _td_hold(slots, pins, 5, py_str_new(name, strlen(name)))
        py_tuple_set_item(load_ptr(slots, C_POINTER_SIZE), index, load_ptr(slots, 5 * C_POINTER_SIZE))
        _td_drop(slots, pins, 5)
        _td_hold(slots, pins, 5, py_int_from_i64(2 if index == 5 else 0))
        py_tuple_set_item(load_ptr(slots, 2 * C_POINTER_SIZE), index, load_ptr(slots, 5 * C_POINTER_SIZE))
        _td_drop(slots, pins, 5)
        py_tuple_set_item(load_ptr(slots, 3 * C_POINTER_SIZE), index,
                          global_load_ptr("py_False") if index == 0 else global_load_ptr("py_True"))
        default = _td_none()
        if index == 4:
            default = global_load_ptr("py_False")
        elif index == 5:
            default = global_load_ptr("py_True")
        py_tuple_set_item(load_ptr(slots, 4 * C_POINTER_SIZE), index, default)
        index = index + 1
    _td_hold(slots, pins, 5, py_tuple_new(5))
    _td_hold(slots, pins, 6, py_str_new(cstr("__pcc_func_signature_v1__"), 25))
    py_tuple_set_item(load_ptr(slots, 5 * C_POINTER_SIZE), 0, load_ptr(slots, 6 * C_POINTER_SIZE))
    index = 1
    while index < 5:
        py_tuple_set_item(load_ptr(slots, 5 * C_POINTER_SIZE), index, load_ptr(slots, index * C_POINTER_SIZE))
        index = index + 1
    _td_hold(slots, pins, 7, py_tuple_new(0))
    _td_hold(slots, pins, 8, py_tuple_new(2))
    py_tuple_set_item(load_ptr(slots, 8 * C_POINTER_SIZE), 0, load_ptr(slots, 7 * C_POINTER_SIZE))
    py_tuple_set_item(load_ptr(slots, 8 * C_POINTER_SIZE), 1, load_ptr(slots, 5 * C_POINTER_SIZE))
    return load_ptr(slots, 8 * C_POINTER_SIZE)


def _td_add_method(cls, name, entry, captures, slots, pins) -> None:
    _td_hold(slots, pins, 9, py_func_new_named(entry, captures, name))
    if not ptr_is_null(load_ptr(slots, 9 * C_POINTER_SIZE)):
        py_class_setattr(cls, name, load_ptr(slots, 9 * C_POINTER_SIZE))
    _td_drop(slots, pins, 9)


@c_abi_export("py_tempdir_type")
def py_tempdir_type():
    mutex = _td_mutex()
    if ptr_is_null(mutex):
        return null()
    pcc_mutex_lock(mutex)
    cls = global_load_ptr("pcc_tempdir_class")
    if not ptr_is_null(cls):
        py_incref(cls)
        pcc_mutex_unlock(mutex)
        return cls
    pcc_mutex_unlock(mutex)
    slots = stack_alloc(_TD_SLOTS * C_POINTER_SIZE)
    pins = stack_alloc(_TD_SLOTS * C_POINTER_SIZE)
    memset(slots, 0, _TD_SLOTS * C_POINTER_SIZE)
    memset(pins, 0, _TD_SLOTS * C_POINTER_SIZE)
    pcc_gc_frame_enter(global_addr("pcc_tempdir_frame_map"), slots)
    _td_hold(slots, pins, 0, py_class_new(cstr("TemporaryDirectory"), null(), 0, null(), 0))
    cls = load_ptr(slots, 0)
    if not ptr_is_null(cls):
        captures = _td_init_signature(slots, pins)
        _td_add_method(cls, cstr("__init__"), function_addr("pcc_tempdir_init_entry"), captures, slots, pins)
        _td_add_method(cls, cstr("__enter__"), function_addr("pcc_tempdir_enter_entry"), load_ptr(slots, 7 * C_POINTER_SIZE), slots, pins)
        _td_add_method(cls, cstr("__exit__"), function_addr("pcc_tempdir_exit_entry"), load_ptr(slots, 7 * C_POINTER_SIZE), slots, pins)
        _td_add_method(cls, cstr("cleanup"), function_addr("pcc_tempdir_cleanup_entry"), load_ptr(slots, 7 * C_POINTER_SIZE), slots, pins)
        _td_add_method(cls, cstr("__repr__"), function_addr("pcc_tempdir_repr_entry"), load_ptr(slots, 7 * C_POINTER_SIZE), slots, pins)
        _td_hold(slots, pins, 10, py_str_new(cstr("tempfile"), 8))
        py_class_setattr(cls, cstr("__module__"), load_ptr(slots, 10 * C_POINTER_SIZE))
        if not py_err_occurred():
            pcc_mutex_lock(mutex)
            existing = global_load_ptr("pcc_tempdir_class")
            if not ptr_is_null(existing):
                py_incref(existing)
                _td_hold(slots, pins, _TD_OUTPUT, existing)
            elif atexit(_td_shutdown) != 0:
                _td_error(19, cstr("TemporaryDirectory could not register shutdown cleanup"))
            else:
                # Classes are immortal in the existing class owner; its normal
                # attrs traversal retains all callable/signature metadata.
                global_store_ptr("pcc_tempdir_class", cls)
                py_incref(cls)
                _td_hold(slots, pins, _TD_OUTPUT, cls)
            pcc_mutex_unlock(mutex)
    return _td_finish(slots, pins)
