"""Shared Match/Pattern class publication into registered owning slots.

Each class has exactly one cache, root handle and mutex. Their common factory
publishes a single winner and returns an independently owned counted lease.
"""
__pcc_runtime_port__ = True

from pcc.extern import c_abi_export, c_int32, c_int64, c_ptr, c_void, extern
from pcc.unsafe import (
    atomic_cas_i64,
    atomic_load_i64,
    atomic_rmw_i32,
    cstr,
    define_global_i64,
    define_global_ptr_null,
    global_addr,
    global_store_ptr,
    int_to_ptr,
    load_ptr,
    null,
    ptr_is_null,
    ptr_to_int,
    store_ptr,
)
from pcc.runtime.py.py_abi_constants import PYOBJECTHEADER_FLAGS_OFFSET, PY_FLAG_IMMORTAL

py_class_new = extern("py_class_new", (c_ptr, c_ptr, c_int32, c_ptr, c_int32), c_ptr)
py_gc_untrack = extern("py_gc_untrack", (c_ptr,), c_void)
py_runtime_error_if_unset = extern("py_runtime_error_if_unset", (c_ptr, c_ptr), c_ptr)
pcc_mutex_new = extern("pcc_mutex_new", (), c_ptr)
pcc_mutex_free = extern("pcc_mutex_free", (c_ptr,), c_void)
pcc_mutex_lock = extern("pcc_mutex_lock", (c_ptr,), c_int64)
pcc_mutex_unlock = extern("pcc_mutex_unlock", (c_ptr,), c_int64)
pcc_platform_abort = extern("pcc_platform_abort", (), c_void)
pcc_gc_scheduler_root_register_handle = extern("pcc_gc_scheduler_root_register_handle", (c_ptr,), c_ptr)
pcc_gc_publish_owned_root = extern("pcc_gc_publish_owned_root", (c_ptr,), c_int64)
pcc_gc_foreign_lease_acquire = extern("pcc_gc_foreign_lease_acquire", (c_ptr,), c_int64)
pcc_gc_foreign_lease_release = extern("pcc_gc_foreign_lease_release", (c_ptr, c_int64), c_int64)
pcc_gc_root_copy_lease = extern("pcc_gc_root_copy_lease", (c_ptr, c_ptr), c_int64)
pcc_gc_store_root = extern("pcc_gc_store_root", (c_ptr, c_ptr), c_void)
pcc_gc_load_ptr = extern("pcc_gc_load_ptr", (c_ptr, c_ptr), c_ptr)

define_global_ptr_null("pcc_re_match_class")
define_global_ptr_null("pcc_re_match_class_root_handle")
define_global_i64("pcc_re_match_class_mutex_bits", 0)
define_global_ptr_null("pcc_re_pattern_class")
define_global_ptr_null("pcc_re_pattern_class_root_handle")
define_global_i64("pcc_re_pattern_class_mutex_bits", 0)


def _regex_class_error(name: c_ptr, message: c_ptr) -> int:
    py_runtime_error_if_unset(name, message)
    return -1


def _regex_class_mutex(bits_slot: c_ptr) -> c_ptr:
    bits: int = atomic_load_i64(bits_slot, 0, "acquire")
    if bits != 0:
        return int_to_ptr(bits)
    candidate: c_ptr = pcc_mutex_new()
    if ptr_is_null(candidate) != 0:
        return null()
    previous: int = atomic_cas_i64(bits_slot, 0, 0, ptr_to_int(candidate), "acq_rel", "acquire")
    if previous != 0:
        pcc_mutex_free(candidate)
        return int_to_ptr(previous)
    return candidate


def _regex_class_unlock(mutex: c_ptr) -> None:
    if pcc_mutex_unlock(mutex) != 0:
        pcc_platform_abort()


def _regex_class_discard(result: c_ptr, token: int) -> None:
    if token >= 0:
        if pcc_gc_foreign_lease_release(result, token) != 0:
            pcc_platform_abort()
            return
    pcc_gc_store_root(result, null())


def _regex_class_copy_lease(result: c_ptr, source: c_ptr, handle_slot: c_ptr, mutex_bits: c_ptr, name: c_ptr) -> int:
    """Fill a fresh registered owning slot; return its counted address lease.

    The caller keeps the slot registered and empty on entry, holds no graph,
    container or scheduler lock, and releases the returned token through this
    same slot before dropping the owner. Negative status leaves result empty,
    except invalid nonempty input (which is left untouched). No raw class
    pointer crosses the factory's ordinary managed prologue or its return.
    The class remains movable; every consumer needs the returned lease.
    """
    if ptr_is_null(result) != 0:
        return _regex_class_error(name, cstr("missing registered result slot"))
    if ptr_is_null(load_ptr(result, 0)) == 0:
        return _regex_class_error(name, cstr("result slot is not empty"))
    mutex: c_ptr = _regex_class_mutex(mutex_bits)
    if ptr_is_null(mutex) != 0:
        return _regex_class_error(name, cstr("mutex allocation failed"))
    if pcc_mutex_lock(mutex) != 0:
        return _regex_class_error(name, cstr("mutex acquisition failed"))
    cached: int = 0
    if ptr_is_null(pcc_gc_load_ptr(null(), source)) == 0:
        cached = 1
    _regex_class_unlock(mutex)
    if cached != 0:
        copied: int = pcc_gc_root_copy_lease(result, source)
        if copied < 0:
            return _regex_class_error(name, cstr("cached owner copy failed"))
        return copied

    # Construct outside the publication mutex. A loser must remain collectible:
    # py_class_new_builtin would immortalize it before the winner is known.
    store_ptr(result, 0, py_class_new(name, null(), 0, null(), 0))
    if ptr_is_null(load_ptr(result, 0)) != 0:
        return _regex_class_error(name, cstr("class allocation failed"))
    if pcc_gc_publish_owned_root(result) < 0:
        _regex_class_discard(result, -1)
        return _regex_class_error(name, cstr("class root publication failed"))
    token: int = pcc_gc_foreign_lease_acquire(result)
    if token < 0:
        _regex_class_discard(result, -1)
        return _regex_class_error(name, cstr("class address lease failed"))
    if pcc_mutex_lock(mutex) != 0:
        _regex_class_discard(result, token)
        return _regex_class_error(name, cstr("publication mutex acquisition failed"))
    if ptr_is_null(pcc_gc_load_ptr(null(), source)) == 0:
        _regex_class_unlock(mutex)
        _regex_class_discard(result, token)
        copied = pcc_gc_root_copy_lease(result, source)
        if copied < 0:
            return _regex_class_error(name, cstr("winner owner copy failed"))
        return copied

    if ptr_is_null(load_ptr(handle_slot, 0)) != 0:
        handle: c_ptr = pcc_gc_scheduler_root_register_handle(source)
        if ptr_is_null(handle) != 0:
            _regex_class_unlock(mutex)
            _regex_class_discard(result, token)
            return _regex_class_error(name, cstr("global root registration failed"))
        store_ptr(handle_slot, 0, handle)
    global_token: int = pcc_gc_root_copy_lease(source, result)
    if global_token < 0:
        _regex_class_unlock(mutex)
        _regex_class_discard(result, token)
        return _regex_class_error(name, cstr("global owner publication failed"))
    # Only the published winner acquires builtin-type lifetime semantics.
    # Its result/global leases guard this raw header across untracking.
    py_gc_untrack(load_ptr(result, 0))
    atomic_rmw_i32("or", load_ptr(result, 0), PYOBJECTHEADER_FLAGS_OFFSET, PY_FLAG_IMMORTAL, "relaxed")
    if pcc_gc_foreign_lease_release(source, global_token) != 0:
        pcc_platform_abort()
        return -1
    _regex_class_unlock(mutex)
    return token


@c_abi_export("pcc_re_match_class_copy_lease")
def pcc_re_match_class_copy_lease(result: c_ptr) -> int:
    return _regex_class_copy_lease(result, global_addr("pcc_re_match_class"),
        global_addr("pcc_re_match_class_root_handle"),
        global_addr("pcc_re_match_class_mutex_bits"), cstr("re.Match"))


@c_abi_export("pcc_re_pattern_class_copy_lease")
def pcc_re_pattern_class_copy_lease(result: c_ptr) -> int:
    return _regex_class_copy_lease(result, global_addr("pcc_re_pattern_class"),
        global_addr("pcc_re_pattern_class_root_handle"),
        global_addr("pcc_re_pattern_class_mutex_bits"), cstr("re.Pattern"))
