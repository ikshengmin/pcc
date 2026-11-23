"""Owned process-call boundary for conservative roots.

The initial thread's application stack starts at the call to main, not in
the platform loader or startup routine. Its caller's SP is an exact exclusive
bound, even if a compiler omitted a frame record inside main.
"""
from pcc import i64
from pcc.extern import c_abi_export, c_ptr
from pcc.unsafe import (
    call_c_abi, capture_gc_registers, define_thread_local_ptr_null, frame_address,
    global_load_ptr, global_store_ptr, load_i32, stack_alloc, store_ptr,
)

__pcc_freestanding__ = True

define_thread_local_ptr_null("pcc_process_main_anchor")

_PROCESS_MAIN_FRAME: i64 = 0
_PROCESS_MAIN_LIMIT: i64 = 8
_PROCESS_MAIN_ANCHOR_BYTES: i64 = 16
_REGISTER_CAPTURE_BYTES: i64 = 128
_INT_RESULT_BYTES: i64 = 4


@c_abi_export("pcc_process_main_boundary")
def pcc_process_main_boundary() -> c_ptr:
    return global_load_ptr("pcc_process_main_anchor")


@c_abi_export("pcc_process_call_main")
def pcc_process_call_main(entry: c_ptr, argc: i64, argv: c_ptr,
                          envp: c_ptr, apple: c_ptr) -> i64:
    old_anchor = global_load_ptr("pcc_process_main_anchor")
    anchor = stack_alloc(_PROCESS_MAIN_ANCHOR_BYTES)
    scratch = stack_alloc(_REGISTER_CAPTURE_BYTES)
    result = stack_alloc(_INT_RESULT_BYTES)
    store_ptr(anchor, _PROCESS_MAIN_FRAME, frame_address())
    store_ptr(anchor, _PROCESS_MAIN_LIMIT, capture_gc_registers(scratch))
    global_store_ptr("pcc_process_main_anchor", anchor)
    call_c_abi(entry, "i32", ("i32", "ptr", "ptr", "ptr"),
               (argc, argv, envp, apple), result)
    global_store_ptr("pcc_process_main_anchor", old_anchor)
    return load_i32(result, 0)
