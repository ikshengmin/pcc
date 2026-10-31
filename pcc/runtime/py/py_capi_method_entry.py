"""No-poll admission/publication for the slot-owned C method call boundary.

Only captures/args and a managed NEW result enter traced slots. PyMethodDef,
function pointers, and registered native type tokens remain process-retained
raw metadata. This module's build policy disables implicit polls; the managed
body retains ordinary polls. Foreign callbacks run with no inherited no-park
region or graph/registry lock. This is not a foreign-reference lifetime registry.
"""
__pcc_runtime_port__ = True

from pcc.extern import c_abi_export, c_int64, c_ptr, c_void, extern
from pcc.unsafe import (
    call_c_abi,
    call_ptr2,
    call_ptr3,
    call_ptr_ptr_ptr_i64_ptr,
    define_global_i32,
    function_addr,
    global_addr,
    load_ptr,
    memset,
    null,
    ptr_is_null,
    stack_alloc,
    store_ptr,
)

pcc_thread_no_park_depth = extern("pcc_thread_no_park_depth", (), c_int64)
pcc_thread_no_park_enter = extern("pcc_thread_no_park_enter", (), c_void)
pcc_thread_no_park_exit = extern("pcc_thread_no_park_exit", (), c_void)
pcc_gc_note_frame_enter_checked = extern("pcc_gc_note_frame_enter_checked", (c_ptr, c_ptr), c_int64)
pcc_gc_note_frame_leave = extern("pcc_gc_note_frame_leave", (c_ptr,), c_void)
pcc_gc_take_pinned_slot = extern("pcc_gc_take_pinned_slot", (c_ptr, c_int64), c_ptr)
pcc_platform_abort = extern("pcc_platform_abort", (), c_void)
pcc_capi_is_type_object_value = extern("pcc_capi_is_type_object_value", (c_ptr,), c_int64)
pcc_capi_method_call_into = extern("pcc_capi_method_call_into", (c_ptr, c_ptr, c_ptr, c_int64), c_int64)

# Shared layout with py_capi_method_call_runtime: result is owning slot zero.
_METHOD_SLOT_COUNT = 16
define_global_i32("pcc_capi_method_borrowed_map", -2)
define_global_i32("pcc_capi_method_owned_map", 16)


@c_abi_export("pcc_capi_method_call_entry")
def pcc_capi_method_call_entry(captures: c_ptr, args: c_ptr) -> c_ptr:
    if pcc_thread_no_park_depth() != 0:
        pcc_platform_abort()
        return null()
    pcc_thread_no_park_enter()
    borrowed: c_ptr = stack_alloc(16)
    store_ptr(borrowed, 0, captures)
    store_ptr(borrowed, 8, args)
    invalid: int = 0
    # The recognizer compares addresses only and cannot invoke user code.
    # Even invalid native-type inputs must not be registered as managed roots.
    if pcc_capi_is_type_object_value(captures) != 0:
        store_ptr(borrowed, 0, null())
        invalid = 1
    if pcc_capi_is_type_object_value(args) != 0:
        store_ptr(borrowed, 8, null())
        invalid = 1
    slots: c_ptr = stack_alloc(_METHOD_SLOT_COUNT * 8)
    memset(slots, 0, _METHOD_SLOT_COUNT * 8)
    raw_result: c_ptr = stack_alloc(8)
    store_ptr(raw_result, 0, null())
    if pcc_gc_note_frame_enter_checked(global_addr("pcc_capi_method_borrowed_map"), borrowed) != 1:
        pcc_platform_abort()
        return null()
    if pcc_gc_note_frame_enter_checked(global_addr("pcc_capi_method_owned_map"), slots) != 1:
        pcc_gc_note_frame_leave(borrowed)
        pcc_platform_abort()
        return null()
    pcc_thread_no_park_exit()
    prior: int = pcc_capi_method_call_into(borrowed, slots, raw_result, invalid)
    pcc_gc_note_frame_leave(slots)
    pcc_gc_note_frame_leave(borrowed)
    if ptr_is_null(load_ptr(raw_result, 0)) == 0:
        # A registered native type's process-retained storage is not pinned,
        # decrefed, or passed to the managed result-transfer primitive.
        return load_ptr(raw_result, 0)
    return pcc_gc_take_pinned_slot(slots, prior)


@c_abi_export("pcc_capi_method_invoke_into")
def pcc_capi_method_invoke_into(
    function: c_ptr, self_obj: c_ptr, args: c_ptr, kwargs: c_ptr,
    vector: c_ptr, names: c_ptr, nargs: int, flags: int,
    result: c_ptr, raw_result: c_ptr,
) -> None:
    """Invoke with leased operands; publish NEW before the first later poll.

    The callback may collect or reenter Python. The short no-park region starts
    only AFTER it returns and ends once its result is in the appropriate lane.
    No ownership is inferred from equality with self/arguments: a legitimate
    NEW alias remains an independent owner in result.
    """
    if pcc_thread_no_park_depth() != 0:
        pcc_platform_abort()
        return
    value: c_ptr = null()
    if flags == 3:
        value = call_ptr3(function, self_obj, args, kwargs)
    elif flags == 4:
        value = call_ptr2(function, self_obj, null())
    elif flags == 8:
        value = call_ptr2(function, self_obj, load_ptr(vector, 0))
    elif flags == 128:
        # Plain FASTCALL has exactly three arguments, unlike FASTCALL|KEYWORDS.
        # These tuples are intrinsic syntax, not managed tuple allocations.
        # Fixed stack storage is raw and lives through the immediate no-poll
        # result publication below; there is no fallible heap allocation here.
        returned: c_ptr = stack_alloc(8)
        call_c_abi(function, "ptr", ("ptr", "ptr", "i64"),
                   (self_obj, vector, nargs), returned)
        value = load_ptr(returned, 0)
    elif flags == 130:
        value = call_ptr_ptr_ptr_i64_ptr(function, self_obj, vector, nargs, names)
    else:
        value = call_ptr2(function, self_obj, args)
    pcc_thread_no_park_enter()
    if pcc_capi_is_type_object_value(value) != 0:
        store_ptr(raw_result, 0, value)
    else:
        store_ptr(result, 0, value)
    pcc_thread_no_park_exit()


@c_abi_export("pcc_capi_method_call_entry_address")
def pcc_capi_method_call_entry_address() -> c_ptr:
    return function_addr("pcc_capi_method_call_entry")
