"""Guarded raw regex entries for slot-owned compile, fullmatch and callbacks.

Every managed body writes its final NEW
result into the provided owning slot, preserves TLS errors, retires every
other temporary/lease, pins the result and returns its prior-pin token. Callers are registered PCC threads and
hold no graph/container/scheduler lock or inherited no-park region. No raw argument may cross an entry poll.
"""
from pcc.extern import c_abi_export, c_int64, c_ptr, c_void, extern
from pcc.unsafe import (
    define_global_i32,
    function_addr,
    global_addr,
    memset,
    null,
    stack_alloc,
    store_ptr,
)

__pcc_runtime_port__ = True

pcc_thread_no_park_depth = extern("pcc_thread_no_park_depth", (), c_int64)
pcc_thread_no_park_enter = extern("pcc_thread_no_park_enter", (), c_void)
pcc_thread_no_park_exit = extern("pcc_thread_no_park_exit", (), c_void)
pcc_gc_note_frame_enter_checked = extern(
    "pcc_gc_note_frame_enter_checked", (c_ptr, c_ptr), c_int64
)
pcc_gc_note_frame_leave = extern("pcc_gc_note_frame_leave", (c_ptr,), c_void)
pcc_gc_take_pinned_slot = extern("pcc_gc_take_pinned_slot", (c_ptr, c_int64), c_ptr)
pcc_platform_abort = extern("pcc_platform_abort", (), c_void)
pcc_re_engine_fullmatch_into = extern(
    "pcc_re_engine_fullmatch_into", (c_ptr, c_ptr, c_int64), c_int64
)

pcc_re_match_method_into = extern("pcc_re_match_method_into", (c_ptr, c_ptr), c_int64)
pcc_re_pattern_fullmatch_into = extern("pcc_re_pattern_fullmatch_into", (c_ptr, c_ptr), c_int64)
pcc_re_pattern_install_into = extern("pcc_re_pattern_install_into", (c_ptr, c_ptr, c_int64), c_int64)
pcc_re_compile_pattern_into = extern("pcc_re_compile_pattern_into", (c_ptr, c_ptr, c_int64), c_int64)
pcc_re_match_instance_into = extern("pcc_re_match_instance_into", (c_ptr,), c_int64)

define_global_i32("pcc_re_entry_borrowed_map", -2)
define_global_i32("pcc_re_entry_result_map", 16)


def _re_guarded(pattern: c_ptr, text: c_ptr, flags: int, kind: int) -> c_ptr:
    # This dedicated runtime-port entry uses the existing no-implicit-poll
    # build policy. Its audited IR has no managed frame prologue or poll.
    # Protection precedes the first operation which might need graph locking.
    if pcc_thread_no_park_depth() != 0:
        pcc_platform_abort()
        return null()
    pcc_thread_no_park_enter()
    borrowed: c_ptr = stack_alloc(16)
    store_ptr(borrowed, 0, pattern)
    store_ptr(borrowed, 8, text)
    result: c_ptr = stack_alloc(128)
    memset(result, 0, 128)
    if pcc_gc_note_frame_enter_checked(global_addr("pcc_re_entry_borrowed_map"), borrowed) != 1:
        # Registration failure cannot safely expose these unrooted arguments
        # to a parking exception allocator. Root-bootstrap failure is fatal.
        pcc_platform_abort()
        return null()
    if pcc_gc_note_frame_enter_checked(global_addr("pcc_re_entry_result_map"), result) != 1:
        pcc_gc_note_frame_leave(borrowed)
        pcc_platform_abort()
        return null()
    pcc_thread_no_park_exit()
    # Only slot addresses cross this managed function's ordinary entry poll.
    prior: int = 0
    if kind == 0:
        prior = pcc_re_engine_fullmatch_into(borrowed, result, flags)
    elif kind == 1:
        prior = pcc_re_match_method_into(borrowed, result)
    elif kind == 2:
        prior = pcc_re_match_instance_into(result)
    elif kind == 3:
        prior = pcc_re_pattern_fullmatch_into(borrowed, result)
    elif kind == 4:
        prior = pcc_re_pattern_install_into(borrowed, result, flags)
    else:
        prior = pcc_re_compile_pattern_into(borrowed, result, flags)
    pcc_gc_note_frame_leave(result)
    pcc_gc_note_frame_leave(borrowed)
    # All potentially parking work is complete; return transfer is raw.
    return pcc_gc_take_pinned_slot(result, prior)


@c_abi_export("pcc_re_engine_fullmatch_guarded")
def pcc_re_engine_fullmatch_guarded(pattern: c_ptr, text: c_ptr, flags: int) -> c_ptr:
    return _re_guarded(pattern, text, flags, 0)


@c_abi_export("pcc_re_match_method_guarded")
def pcc_re_match_method_guarded(captures: c_ptr, args: c_ptr) -> c_ptr:
    return _re_guarded(captures, args, 0, 1)


@c_abi_export("pcc_re_match_method_address")
def pcc_re_match_method_address() -> c_ptr:
    return function_addr("pcc_re_match_method_guarded")


@c_abi_export("pcc_re_match_instance_guarded")
def pcc_re_match_instance_guarded() -> c_ptr:
    return _re_guarded(null(), null(), 0, 2)


@c_abi_export("pcc_re_pattern_fullmatch_guarded")
def pcc_re_pattern_fullmatch_guarded(captures: c_ptr, args: c_ptr) -> c_ptr:
    return _re_guarded(captures, args, 0, 3)


@c_abi_export("pcc_re_pattern_fullmatch_address")
def pcc_re_pattern_fullmatch_address() -> c_ptr:
    return function_addr("pcc_re_pattern_fullmatch_guarded")


@c_abi_export("pcc_re_pattern_install_guarded")
def pcc_re_pattern_install_guarded(instance: c_ptr, pattern: c_ptr, flags: int) -> c_ptr:
    return _re_guarded(instance, pattern, flags, 4)


@c_abi_export("py_re_compile_obj")
def py_re_compile_obj(pattern: c_ptr, flags: int) -> c_ptr:
    return _re_guarded(pattern, null(), flags, 5)


@c_abi_export("py_re_engine_fullmatch_flags")
def py_re_engine_fullmatch_flags(pattern: c_ptr, text: c_ptr, flags: int) -> c_ptr:
    return _re_guarded(pattern, text, flags, 0)


@c_abi_export("py_re_fullmatch")
def py_re_fullmatch(pattern: c_ptr, text: c_ptr) -> c_ptr:
    return _re_guarded(pattern, text, 0, 0)


@c_abi_export("py_re_fullmatch_flags")
def py_re_fullmatch_flags(pattern: c_ptr, text: c_ptr, flags: int) -> c_ptr:
    return _re_guarded(pattern, text, flags, 0)


pcc_gc_root_copy_borrowed_lease = extern("pcc_gc_root_copy_borrowed_lease", (c_ptr, c_ptr), c_int64)
define_global_i32("pcc_re_publish_borrowed_map", -1)


@c_abi_export("pcc_re_publish_copy_lease")
def pcc_re_publish_copy_lease(value: c_ptr, output: c_ptr) -> int:
    """Copy a live source into a registered empty output; never mutate pin bits.

    The source's caller-owned lifetime spans this call. Entry has no held locks
    or inherited no-park. Negative copy status leaves output unchanged/empty.
    """
    if pcc_thread_no_park_depth() != 0:
        pcc_platform_abort()
        return -1
    pcc_thread_no_park_enter()
    borrowed: c_ptr = stack_alloc(8)
    store_ptr(borrowed, 0, value)
    if pcc_gc_note_frame_enter_checked(global_addr("pcc_re_publish_borrowed_map"), borrowed) != 1:
        pcc_platform_abort()
        return -1
    pcc_thread_no_park_exit()
    token: int = pcc_gc_root_copy_borrowed_lease(output, borrowed)
    pcc_gc_note_frame_leave(borrowed)
    return token
