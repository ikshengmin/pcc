"""Slot-owned managed body for the existing C-extension method trampoline.

Every immediate positional/keyword object exposed to C has an independent
counted address lease. Raw native type tokens have a separate metadata lane.
This bounds borrowed argument/interior-pointer safety to this method call; it
is not general foreign NEW/borrowed-reference lifetime or extension unloading.
"""
__pcc_runtime_port__ = True

from pcc.extern import c_abi_export, c_int64, c_ptr, c_void, extern
from pcc.runtime.py.py_abi_constants import PY_TYPE_DICT, PY_TYPE_STR, PY_TYPE_TUPLE
from pcc.unsafe import (
    calloc, cstr, free, global_load_ptr, int_to_ptr, is_tagged_int,
    load_i32, load_i64, load_ptr, memset, null, ptr_add, ptr_eq, ptr_is_null,
    stack_alloc, store_i32, store_i64, store_ptr,
)

pcc_gc_root_copy_lease = extern("pcc_gc_root_copy_lease", (c_ptr, c_ptr), c_int64)
pcc_gc_root_copy_borrowed_lease = extern("pcc_gc_root_copy_borrowed_lease", (c_ptr, c_ptr), c_int64)
pcc_gc_foreign_lease_acquire = extern("pcc_gc_foreign_lease_acquire", (c_ptr,), c_int64)
pcc_gc_foreign_lease_release = extern("pcc_gc_foreign_lease_release", (c_ptr, c_int64), c_int64)
pcc_gc_store_root = extern("pcc_gc_store_root", (c_ptr, c_ptr), c_void)
pcc_gc_note_slot_write_barrier = extern("pcc_gc_note_slot_write_barrier", (c_ptr, c_ptr, c_ptr), c_void)
pcc_py_gc_minor_graph_lock = extern("pcc_py_gc_minor_graph_lock", (), c_void)
pcc_py_gc_minor_graph_unlock = extern("pcc_py_gc_minor_graph_unlock", (), c_void)
pcc_gc_pin_slot_for_take = extern("pcc_gc_pin_slot_for_take", (c_ptr, c_int64), c_int64)
pcc_gc_note_frame_enter_checked = extern("pcc_gc_note_frame_enter_checked", (c_ptr, c_ptr), c_int64)
pcc_gc_note_frame_leave = extern("pcc_gc_note_frame_leave", (c_ptr,), c_void)
pcc_thread_no_park_enter = extern("pcc_thread_no_park_enter", (), c_void)
pcc_thread_no_park_exit = extern("pcc_thread_no_park_exit", (), c_void)
pcc_platform_abort = extern("pcc_platform_abort", (), c_void)
py_runtime_error_if_unset = extern("py_runtime_error_if_unset", (c_ptr, c_ptr), c_ptr)
py_err_occurred = extern("py_err_occurred", (), c_int64)
py_clear_exception = extern("py_clear_exception", (), c_void)
py_tls_exc_swap_slot = extern("py_tls_exc_swap_slot", (c_ptr,), c_void)
py_raise = extern("py_raise", (c_ptr,), c_void)
py_decref = extern("py_decref", (c_ptr,), c_void)
py_exc_new = extern("py_exc_new", (c_int64, c_ptr), c_ptr)
PyErr_NoMemory = extern("PyErr_NoMemory", (), c_ptr)
py_tuple_new = extern("py_tuple_new", (c_int64,), c_ptr)
py_tuple_set_item = extern("py_tuple_set_item", (c_ptr, c_int64, c_ptr), c_void)
py_int_value_i64 = extern("py_int_value_i64", (c_ptr,), c_int64)
py_dict_items = extern("py_dict_items", (c_ptr,), c_ptr)
py_dict_len = extern("py_dict_len", (c_ptr,), c_int64)
py_list_len = extern("py_list_len", (c_ptr,), c_int64)
pcc_capi_is_type_object_value = extern("pcc_capi_is_type_object_value", (c_ptr,), c_int64)
pcc_capi_builtin_type_token = extern("pcc_capi_builtin_type_token", (c_ptr,), c_ptr)
pcc_capi_method_invoke_into = extern(
    "pcc_capi_method_invoke_into",
    (c_ptr, c_ptr, c_ptr, c_ptr, c_ptr, c_ptr, c_int64, c_int64, c_ptr, c_ptr), c_void,
)

# Owning frame layout, registered by py_capi_method_entry before body entry.
_METHOD_RESULT = 0
_METHOD_ERROR = 1
_METHOD_CAPTURES = 2
_METHOD_ARGS = 3
_METHOD_SELF = 4
_METHOD_POINTER = 5
_METHOD_FLAGS = 6
_METHOD_CALLARGS = 7
_METHOD_KWARGS = 8
_METHOD_NAMES = 9
_METHOD_PREPARED = 10
_METHOD_ITEMS = 11
_METHOD_PAIR = 12
_METHOD_ITEM = 13
_METHOD_RAISED = 14
_METHOD_SLOT_COUNT = 16

# Raw call-view control layout. Never registered as a managed root array.
_VIEW_VECTOR = 0
_VIEW_OWNERS = 1
_VIEW_TOKENS = 2
_VIEW_COUNT = 3
_VIEW_REGISTERED = 4
_VIEW_SELF = 5
_VIEW_ITEM = 6
_VIEW_MAPS = 7
_VIEW_WORDS = 8
_VIEW_CHUNK_SLOTS = 65536


def _method_adopt(slots: c_ptr, tokens: c_ptr, index: int) -> int:
    slot: c_ptr = ptr_add(slots, index * 8)
    if ptr_is_null(load_ptr(slot, 0)) != 0:
        py_runtime_error_if_unset(cstr("C method"), cstr("object allocation failed"))
        return 0
    token: int = pcc_gc_foreign_lease_acquire(slot)
    if token < 0:
        py_runtime_error_if_unset(cstr("C method"), cstr("object address lease failed"))
        return 0
    store_i64(tokens, index * 8, token)
    pcc_py_gc_minor_graph_lock()
    pcc_gc_note_slot_write_barrier(null(), slot, load_ptr(slot, 0))
    pcc_py_gc_minor_graph_unlock()
    return 1


def _method_drop(slots: c_ptr, tokens: c_ptr, index: int) -> None:
    slot: c_ptr = ptr_add(slots, index * 8)
    if pcc_gc_foreign_lease_release(slot, load_i64(tokens, index * 8)) != 0:
        pcc_platform_abort()
        return
    store_i64(tokens, index * 8, 0)
    pcc_gc_store_root(slot, null())


def _method_error(slots: c_ptr, tokens: c_ptr, message: c_ptr) -> None:
    if py_err_occurred() != 0:
        return
    store_ptr(slots, _METHOD_RAISED * 8, py_exc_new(3, message))
    if _method_adopt(slots, tokens, _METHOD_RAISED) != 0:
        py_raise(load_ptr(slots, _METHOD_RAISED * 8))


def _method_copy_input(slots: c_ptr, tokens: c_ptr, index: int, source: c_ptr) -> int:
    token: int = pcc_gc_root_copy_borrowed_lease(ptr_add(slots, index * 8), source)
    if token < 0:
        py_runtime_error_if_unset(cstr("C method"), cstr("argument owner copy failed"))
        return 0
    store_i64(tokens, index * 8, token)
    return 1


def _method_copy_field(destination: c_ptr, tokens: c_ptr, index: int, source: c_ptr, raw: c_ptr) -> int:
    # source is an actual owning field of a caller-leased tuple or private
    # snapshot list. The compare-only classifier may poll; reload source, never
    # use its transient comparison operand as a managed object afterwards.
    store_ptr(raw, 0, null())
    if pcc_capi_is_type_object_value(load_ptr(source, 0)) != 0:
        store_ptr(raw, 0, load_ptr(source, 0))
        return 1
    token: int = pcc_gc_root_copy_lease(ptr_add(destination, index * 8), source)
    if token < 0:
        py_runtime_error_if_unset(cstr("C method"), cstr("object field owner copy failed"))
        return 0
    store_i64(tokens, index * 8, token)
    return 1


def _method_is_tuple(value: c_ptr) -> int:
    if ptr_is_null(value) != 0 or is_tagged_int(value) != 0:
        return 0
    return 1 if load_i32(value, 8) == PY_TYPE_TUPLE else 0


def _method_prepare(slots: c_ptr, tokens: c_ptr, view: c_ptr) -> int:
    args: c_ptr = load_ptr(slots, _METHOD_ARGS * 8)
    count: int = load_i64(args, 16)
    raw: c_ptr = ptr_add(view, _VIEW_ITEM * 8)
    changed: int = 0
    index: int = 0
    while index < count:
        if _method_copy_field(slots, tokens, _METHOD_ITEM, ptr_add(args, 24 + index * 8), raw) == 0:
            return 0
        if ptr_is_null(load_ptr(raw, 0)) != 0:
            value: c_ptr = load_ptr(slots, _METHOD_ITEM * 8)
            if ptr_is_null(value) != 0:
                _method_error(slots, tokens, cstr("NULL C method argument"))
                return 0
            if pcc_capi_builtin_type_token(value) != value:
                changed = 1
        _method_drop(slots, tokens, _METHOD_ITEM)
        index = index + 1
    source: c_ptr = ptr_add(slots, _METHOD_ARGS * 8)
    if changed != 0:
        store_ptr(slots, _METHOD_PREPARED * 8, py_tuple_new(count))
        if _method_adopt(slots, tokens, _METHOD_PREPARED) == 0:
            return 0
        index = 0
        while index < count:
            if _method_copy_field(slots, tokens, _METHOD_ITEM, ptr_add(args, 24 + index * 8), raw) == 0:
                return 0
            translated: c_ptr = load_ptr(raw, 0)
            if ptr_is_null(translated) != 0:
                translated = pcc_capi_builtin_type_token(load_ptr(slots, _METHOD_ITEM * 8))
            py_tuple_set_item(load_ptr(slots, _METHOD_PREPARED * 8), index, translated)
            if py_err_occurred() != 0:
                return 0
            _method_drop(slots, tokens, _METHOD_ITEM)
            index = index + 1
        source = ptr_add(slots, _METHOD_PREPARED * 8)
    token: int = pcc_gc_root_copy_lease(ptr_add(slots, _METHOD_CALLARGS * 8), source)
    if token < 0:
        py_runtime_error_if_unset(cstr("C method"), cstr("prepared argument owner copy failed"))
        return 0
    store_i64(tokens, _METHOD_CALLARGS * 8, token)
    return 1


def _method_view_allocate(view: c_ptr, positional: int, keywords: int) -> int:
    # The tuple allocator has the same bounded count. Check multiplication
    # before computing byte sizes; allocation failure remains explicit.
    if positional < 0 or keywords < 0 or positional > 134217728 or keywords > 134217728:
        PyErr_NoMemory()
        return 0
    count: int = positional + keywords * 2
    store_i64(view, _VIEW_COUNT * 8, count)
    if count == 0:
        return 1
    store_ptr(view, _VIEW_VECTOR * 8, calloc(positional + keywords, 8))
    store_ptr(view, _VIEW_OWNERS * 8, calloc(count, 8))
    store_ptr(view, _VIEW_TOKENS * 8, calloc(count, 8))
    chunks: int = (count + _VIEW_CHUNK_SLOTS - 1) // _VIEW_CHUNK_SLOTS
    store_ptr(view, _VIEW_MAPS * 8, calloc(chunks, 4))
    if (ptr_is_null(load_ptr(view, _VIEW_VECTOR * 8)) != 0
            or ptr_is_null(load_ptr(view, _VIEW_OWNERS * 8)) != 0
            or ptr_is_null(load_ptr(view, _VIEW_TOKENS * 8)) != 0
            or ptr_is_null(load_ptr(view, _VIEW_MAPS * 8)) != 0):
        PyErr_NoMemory()
        return 0
    # A compact frame map admits at most 100000 slots. Chunk the arbitrary
    # method arity instead of weakening that registry bound or adding a new
    # call-arity limit. Every map lives until its matching frame is retired.
    chunk: int = 0
    while chunk < chunks:
        chunk_count: int = count - chunk * _VIEW_CHUNK_SLOTS
        if chunk_count > _VIEW_CHUNK_SLOTS:
            chunk_count = _VIEW_CHUNK_SLOTS
        frame_map: c_ptr = ptr_add(load_ptr(view, _VIEW_MAPS * 8), chunk * 4)
        owner_chunk: c_ptr = ptr_add(load_ptr(view, _VIEW_OWNERS * 8), chunk * _VIEW_CHUNK_SLOTS * 8)
        store_i32(frame_map, 0, chunk_count)
        pcc_thread_no_park_enter()
        registered: int = pcc_gc_note_frame_enter_checked(frame_map, owner_chunk)
        pcc_thread_no_park_exit()
        if registered != 1:
            PyErr_NoMemory()
            return 0
        chunk = chunk + 1
        store_i64(view, _VIEW_REGISTERED * 8, chunk)
    return 1


def _method_view_item(view: c_ptr, index: int, source: c_ptr, vector_index: int) -> int:
    raw: c_ptr = ptr_add(view, _VIEW_ITEM * 8)
    owners: c_ptr = load_ptr(view, _VIEW_OWNERS * 8)
    if _method_copy_field(owners, load_ptr(view, _VIEW_TOKENS * 8), index, source, raw) == 0:
        return 0
    value: c_ptr = load_ptr(raw, 0)
    if ptr_is_null(value) != 0:
        value = load_ptr(owners, index * 8)
    if ptr_is_null(value) != 0:
        py_runtime_error_if_unset(cstr("C method"), cstr("NULL C method argument"))
        return 0
    if vector_index >= 0:
        store_ptr(load_ptr(view, _VIEW_VECTOR * 8), vector_index * 8, value)
    return 1


def _method_view_fill(slots: c_ptr, tokens: c_ptr, view: c_ptr, positional: int, keywords: int, flags: int) -> int:
    args: c_ptr = load_ptr(slots, _METHOD_CALLARGS * 8)
    index: int = 0
    while index < positional:
        if _method_view_item(view, index, ptr_add(args, 24 + index * 8), index) == 0:
            return 0
        index = index + 1
    if keywords == 0:
        return 1
    store_ptr(slots, _METHOD_ITEMS * 8, py_dict_items(load_ptr(slots, _METHOD_KWARGS * 8)))
    if _method_adopt(slots, tokens, _METHOD_ITEMS) == 0:
        return 0
    if py_list_len(load_ptr(slots, _METHOD_ITEMS * 8)) != keywords:
        _method_error(slots, tokens, cstr("C method keyword mapping changed size"))
        return 0
    if flags == 130:
        store_ptr(slots, _METHOD_NAMES * 8, py_tuple_new(keywords))
        if _method_adopt(slots, tokens, _METHOD_NAMES) == 0:
            return 0
    raw: c_ptr = ptr_add(view, _VIEW_ITEM * 8)
    index = 0
    while index < keywords:
        # The snapshot is private and never passed to the callback; its leased
        # list and immutable tuple pairs retain stable payload-slot addresses.
        items: c_ptr = load_ptr(load_ptr(slots, _METHOD_ITEMS * 8), 32)
        if _method_copy_field(slots, tokens, _METHOD_PAIR, ptr_add(items, index * 8), raw) == 0:
            return 0
        pair: c_ptr = load_ptr(slots, _METHOD_PAIR * 8)
        if _method_is_tuple(pair) == 0 or load_i64(pair, 16) != 2:
            _method_error(slots, tokens, cstr("invalid C method keyword item"))
            return 0
        key_index: int = positional + keywords + index
        if _method_view_item(view, key_index, ptr_add(pair, 24), -1) == 0:
            return 0
        key: c_ptr = load_ptr(load_ptr(view, _VIEW_OWNERS * 8), key_index * 8)
        if ptr_is_null(key) != 0 or is_tagged_int(key) != 0 or load_i32(key, 8) != PY_TYPE_STR:
            _method_error(slots, tokens, cstr("keyword names must be strings"))
            return 0
        if _method_view_item(view, positional + index, ptr_add(pair, 32), positional + index) == 0:
            return 0
        if flags == 130:
            py_tuple_set_item(load_ptr(slots, _METHOD_NAMES * 8), index, key)
            if py_err_occurred() != 0:
                return 0
        _method_drop(slots, tokens, _METHOD_PAIR)
        index = index + 1
    return 1


def _method_body(borrowed: c_ptr, slots: c_ptr, tokens: c_ptr, view: c_ptr, raw_result: c_ptr, invalid: int) -> None:
    if invalid != 0:
        _method_error(slots, tokens, cstr("invalid C method capture or args"))
        return
    if py_err_occurred() != 0:
        return
    if _method_copy_input(slots, tokens, _METHOD_CAPTURES, borrowed) == 0:
        return
    if _method_copy_input(slots, tokens, _METHOD_ARGS, ptr_add(borrowed, 8)) == 0:
        return
    captures: c_ptr = load_ptr(slots, _METHOD_CAPTURES * 8)
    if _method_is_tuple(captures) == 0 or load_i64(captures, 16) != 3:
        _method_error(slots, tokens, cstr("invalid C method capture"))
        return
    if ptr_is_null(load_ptr(slots, _METHOD_ARGS * 8)) != 0:
        store_ptr(slots, _METHOD_ARGS * 8, py_tuple_new(0))
        if _method_adopt(slots, tokens, _METHOD_ARGS) == 0:
            return
    if _method_is_tuple(load_ptr(slots, _METHOD_ARGS * 8)) == 0:
        _method_error(slots, tokens, cstr("C method args must be a tuple"))
        return
    if _method_copy_field(slots, tokens, _METHOD_SELF, ptr_add(captures, 24), ptr_add(view, _VIEW_SELF * 8)) == 0:
        return
    raw: c_ptr = ptr_add(view, _VIEW_ITEM * 8)
    if _method_copy_field(slots, tokens, _METHOD_POINTER, ptr_add(captures, 32), raw) == 0:
        return
    if _method_copy_field(slots, tokens, _METHOD_FLAGS, ptr_add(captures, 40), raw) == 0:
        return
    if (ptr_is_null(load_ptr(slots, _METHOD_POINTER * 8)) != 0
            or ptr_is_null(load_ptr(slots, _METHOD_FLAGS * 8)) != 0
            or (ptr_is_null(load_ptr(slots, _METHOD_SELF * 8)) != 0 and ptr_is_null(load_ptr(view, _VIEW_SELF * 8)) != 0)):
        _method_error(slots, tokens, cstr("invalid C method capture"))
        return
    method: c_ptr = int_to_ptr(py_int_value_i64(load_ptr(slots, _METHOD_POINTER * 8)))
    flags: int = py_int_value_i64(load_ptr(slots, _METHOD_FLAGS * 8))
    if py_err_occurred() != 0:
        return
    if ptr_is_null(method) != 0 or ptr_is_null(load_ptr(method, 8)) != 0:
        _method_error(slots, tokens, cstr("invalid C method"))
        return
    if (flags & 48) != 0:
        _method_error(slots, tokens, cstr("C method class/static binding is unsupported"))
        return
    convention: int = flags & 143
    if (flags & ~255) != 0 or (convention != 1 and convention != 3 and convention != 4 and convention != 8 and convention != 128 and convention != 130):
        _method_error(slots, tokens, cstr("unsupported C method flags"))
        return
    args: c_ptr = load_ptr(slots, _METHOD_ARGS * 8)
    if (convention & 2) != 0:
        if load_i64(args, 16) != 2:
            _method_error(slots, tokens, cstr("invalid C keyword method call"))
            return
        if _method_copy_field(slots, tokens, _METHOD_CALLARGS, ptr_add(args, 24), raw) == 0:
            return
        if _method_copy_field(slots, tokens, _METHOD_KWARGS, ptr_add(args, 32), raw) == 0:
            return
        kwargs: c_ptr = load_ptr(slots, _METHOD_KWARGS * 8)
        if (_method_is_tuple(load_ptr(slots, _METHOD_CALLARGS * 8)) == 0
                or ptr_is_null(kwargs) != 0
                or (kwargs != global_load_ptr("py_None") and (is_tagged_int(kwargs) != 0 or load_i32(kwargs, 8) != PY_TYPE_DICT))):
            _method_error(slots, tokens, cstr("invalid C keyword method call"))
            return
    elif _method_prepare(slots, tokens, view) == 0:
        return
    positional: int = load_i64(load_ptr(slots, _METHOD_CALLARGS * 8), 16)
    if convention == 4 and positional != 0:
        _method_error(slots, tokens, cstr("method takes no arguments"))
        return
    if convention == 8 and positional != 1:
        _method_error(slots, tokens, cstr("method takes exactly one argument"))
        return
    keywords: int = 0
    # Both operands are intrinsic raw loads: use explicit pointer identity,
    # not an untyped Python comparison that could pin/unpin or invoke equality.
    if (convention & 2) != 0 and ptr_eq(load_ptr(slots, _METHOD_KWARGS * 8), global_load_ptr("py_None")) == 0:
        keywords = py_dict_len(load_ptr(slots, _METHOD_KWARGS * 8))
    if _method_view_allocate(view, positional, keywords) == 0:
        return
    if _method_view_fill(slots, tokens, view, positional, keywords, convention) == 0:
        return
    if py_err_occurred() != 0:
        return
    self_obj: c_ptr = load_ptr(view, _VIEW_SELF * 8)
    if ptr_is_null(self_obj) != 0:
        self_obj = load_ptr(slots, _METHOD_SELF * 8)
    pcc_capi_method_invoke_into(
        load_ptr(method, 8), self_obj, load_ptr(slots, _METHOD_CALLARGS * 8),
        load_ptr(slots, _METHOD_KWARGS * 8), load_ptr(view, _VIEW_VECTOR * 8),
        load_ptr(slots, _METHOD_NAMES * 8), positional, convention,
        ptr_add(slots, _METHOD_RESULT * 8), raw_result,
    )
    if ptr_is_null(load_ptr(slots, _METHOD_RESULT * 8)) == 0:
        _method_adopt(slots, tokens, _METHOD_RESULT)
    elif ptr_is_null(load_ptr(raw_result, 0)) != 0:
        py_runtime_error_if_unset(cstr("pcc_capi_method_call_entry"), cstr("C extension method returned NULL without setting an exception"))


def _method_finish(slots: c_ptr, tokens: c_ptr, view: c_ptr, raw_result: c_ptr) -> int:
    # Save the operation's exception before ANY decref/finalizer cleanup.
    error: c_ptr = ptr_add(slots, _METHOD_ERROR * 8)
    py_tls_exc_swap_slot(error)
    failed: int = 0
    if ptr_is_null(load_ptr(error, 0)) == 0:
        failed = 1
        token: int = pcc_gc_foreign_lease_acquire(error)
        if token < 0:
            pcc_platform_abort()
            return 0
        store_i64(tokens, _METHOD_ERROR * 8, token)
    if load_i64(view, _VIEW_REGISTERED * 8) != 0:
        count: int = load_i64(view, _VIEW_COUNT * 8)
        registered_count: int = load_i64(view, _VIEW_REGISTERED * 8) * _VIEW_CHUNK_SLOTS
        if count > registered_count:
            count = registered_count
        index: int = count - 1
        while index >= 0:
            _method_drop(load_ptr(view, _VIEW_OWNERS * 8), load_ptr(view, _VIEW_TOKENS * 8), index)
            index = index - 1
        chunk: int = load_i64(view, _VIEW_REGISTERED * 8) - 1
        while chunk >= 0:
            pcc_gc_note_frame_leave(ptr_add(load_ptr(view, _VIEW_OWNERS * 8), chunk * _VIEW_CHUNK_SLOTS * 8))
            chunk = chunk - 1
    if ptr_is_null(load_ptr(view, _VIEW_MAPS * 8)) == 0:
        free(load_ptr(view, _VIEW_MAPS * 8))
    index = _VIEW_TOKENS
    while index >= _VIEW_VECTOR:
        if ptr_is_null(load_ptr(view, index * 8)) == 0:
            free(load_ptr(view, index * 8))
        index = index - 1
    index = _METHOD_SLOT_COUNT - 1
    while index >= _METHOD_CAPTURES:
        _method_drop(slots, tokens, index)
        index = index - 1
    if failed != 0:
        # A NEW alias still owns one reference, independently of every input.
        _method_drop(slots, tokens, _METHOD_RESULT)
        if ptr_is_null(load_ptr(raw_result, 0)) == 0:
            # Native types use raw storage, but a legitimate NEW type result
            # still owns a reference. Release it exactly once on failure.
            # Heap-type registry/unregister lifetime remains a separate gap.
            py_decref(load_ptr(raw_result, 0))
            store_ptr(raw_result, 0, null())
    py_clear_exception()
    if pcc_gc_foreign_lease_release(error, load_i64(tokens, _METHOD_ERROR * 8)) != 0:
        pcc_platform_abort()
        return 0
    store_i64(tokens, _METHOD_ERROR * 8, 0)
    py_tls_exc_swap_slot(error)
    result: c_ptr = ptr_add(slots, _METHOD_RESULT * 8)
    prior: int = pcc_gc_pin_slot_for_take(result, 0)
    if pcc_gc_foreign_lease_release(result, load_i64(tokens, _METHOD_RESULT * 8)) != 0:
        pcc_platform_abort()
        return 0
    store_i64(tokens, _METHOD_RESULT * 8, 0)
    return prior


@c_abi_export("pcc_capi_method_call_into")
def pcc_capi_method_call_into(borrowed: c_ptr, slots: c_ptr, raw_result: c_ptr, invalid: int) -> int:
    tokens: c_ptr = stack_alloc(_METHOD_SLOT_COUNT * 8)
    memset(tokens, 0, _METHOD_SLOT_COUNT * 8)
    view: c_ptr = stack_alloc(_VIEW_WORDS * 8)
    memset(view, 0, _VIEW_WORDS * 8)
    _method_body(borrowed, slots, tokens, view, raw_result, invalid)
    return _method_finish(slots, tokens, view, raw_result)
