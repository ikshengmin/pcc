"""Guarded raw regex entry; managed implementation is supplied separately.

This entry is not dispatched by re yet. A managed body must write its final NEW
result into the provided owning slot, preserve TLS errors, retire every
other temporary/lease, pin the result and return its prior-pin token. Callers are registered PCC threads and
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
    else:
        prior = pcc_re_pattern_fullmatch_into(borrowed, result)
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
