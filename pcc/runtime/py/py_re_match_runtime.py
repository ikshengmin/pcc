"""Owned regex Match construction behind the guarded slot-only entry.

The raw entry registers the two borrowed argument slots and all owning slots
before this module's ordinary entry poll. Every managed object address used by a runtime
call has an explicit counted lease; temporary ownership ends in one finish.
"""
__pcc_runtime_port__ = True

from pcc.extern import c_abi_export, c_int32, c_int64, c_ptr, c_void, extern
from pcc.unsafe import (
    cstr, global_load_ptr, load_i8, load_i32, load_i64, load_ptr, memset,
    null, ptr_add, ptr_is_null, stack_alloc, store_i64, store_ptr, strlen,
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
pcc_platform_abort = extern("pcc_platform_abort", (), c_void)
py_runtime_error_if_unset = extern("py_runtime_error_if_unset", (c_ptr, c_ptr), c_ptr)
py_err_occurred = extern("py_err_occurred", (), c_int64)
py_clear_exception = extern("py_clear_exception", (), c_void)
py_tls_exc_swap_slot = extern("py_tls_exc_swap_slot", (c_ptr,), c_void)
py_raise = extern("py_raise", (c_ptr,), c_void)
py_str_check = extern("py_str_check", (c_ptr,), c_int64)
py_exc_new = extern("py_exc_new", (c_int64, c_ptr), c_ptr)
py_str_utf8 = extern("py_str_utf8", (c_ptr,), c_ptr)
py_str_byte_len = extern("py_str_byte_len", (c_ptr,), c_int64)
py_str_new = extern("py_str_new", (c_ptr, c_int64), c_ptr)
py_tuple_new = extern("py_tuple_new", (c_int64,), c_ptr)
py_tuple_set_item = extern("py_tuple_set_item", (c_ptr, c_int64, c_ptr), c_void)
py_int_from_i64 = extern("py_int_from_i64", (c_int64,), c_ptr)
py_instance_new = extern("py_instance_new", (c_ptr,), c_ptr)
py_obj_setattr = extern("py_obj_setattr", (c_ptr, c_ptr, c_ptr), c_int64)
py_func_new_named = extern("py_func_new_named", (c_ptr, c_ptr, c_ptr), c_ptr)
pcc_re_match_class_copy_lease = extern("pcc_re_match_class_copy_lease", (c_ptr,), c_int64)
pcc_re_match_method_address = extern("pcc_re_match_method_address", (), c_ptr)
pcc_re_engine_fullmatch_flags = extern(
    "pcc_re_engine_fullmatch_flags", (c_ptr, c_int64, c_ptr, c_int64, c_int64, c_ptr, c_int32, c_ptr), c_int32
)
pcc_re_engine_group_names_flags = extern("pcc_re_engine_group_names_flags", (c_ptr, c_int64, c_ptr, c_int32), c_int32)

_RE_RESULT = 0
_RE_ERROR = 1
_RE_PATTERN = 2
_RE_TEXT = 3
_RE_CLASS = 4
_RE_SPANS = 5
_RE_NAMES = 6
_RE_ITEM = 7
_RE_CAPTURES = 8
_RE_KIND = 9
_RE_FUNCTION = 10
_RE_RAISED = 11
_RE_SLOTS = 16


def _re_slot(slots: c_ptr, index: int) -> c_ptr:
    return ptr_add(slots, index * 8)


def _re_adopt(slots: c_ptr, tokens: c_ptr, index: int) -> int:
    # The NEW value is already in this registered slot before helper entry.
    slot: c_ptr = ptr_add(slots, index * 8)
    if ptr_is_null(load_ptr(slot, 0)) != 0:
        py_runtime_error_if_unset(cstr("re.Match"), cstr("object allocation failed"))
        return 0
    token: int = pcc_gc_foreign_lease_acquire(slot)
    if token < 0:
        py_runtime_error_if_unset(cstr("re.Match"), cstr("object address lease failed"))
        return 0
    store_i64(tokens, index * 8, token)
    pcc_py_gc_minor_graph_lock()
    pcc_gc_note_slot_write_barrier(null(), slot, load_ptr(slot, 0))
    pcc_py_gc_minor_graph_unlock()
    return 1


def _re_drop(slots: c_ptr, tokens: c_ptr, index: int) -> None:
    slot: c_ptr = ptr_add(slots, index * 8)
    if pcc_gc_foreign_lease_release(slot, load_i64(tokens, index * 8)) != 0:
        pcc_platform_abort()
        return
    store_i64(tokens, index * 8, 0)
    pcc_gc_store_root(slot, null())


def _re_copy_input(slots: c_ptr, tokens: c_ptr, index: int, borrowed: c_ptr, borrowed_index: int) -> int:
    token: int = pcc_gc_root_copy_borrowed_lease(
        ptr_add(slots, index * 8), ptr_add(borrowed, borrowed_index * 8)
    )
    if token < 0:
        py_runtime_error_if_unset(cstr("re.Match"), cstr("argument owner copy failed"))
        return 0
    store_i64(tokens, index * 8, token)
    return 1


def _re_finish(slots: c_ptr, tokens: c_ptr) -> int:
    # Disposing any temporary can run Python finalizers. Save the operation's
    # original exception in a registered slot before starting those releases.
    error: c_ptr = ptr_add(slots, _RE_ERROR * 8)
    py_tls_exc_swap_slot(error)
    failed: int = 0
    if ptr_is_null(load_ptr(error, 0)) == 0:
        failed = 1
        token: int = pcc_gc_foreign_lease_acquire(error)
        if token < 0:
            pcc_platform_abort()
            return 0
        store_i64(tokens, _RE_ERROR * 8, token)
    index: int = _RE_SLOTS - 1
    while index >= _RE_PATTERN:
        _re_drop(slots, tokens, index)
        index = index - 1
    if failed != 0:
        _re_drop(slots, tokens, _RE_RESULT)
    py_clear_exception()
    if pcc_gc_foreign_lease_release(error, load_i64(tokens, _RE_ERROR * 8)) != 0:
        pcc_platform_abort()
        return 0
    store_i64(tokens, _RE_ERROR * 8, 0)
    py_tls_exc_swap_slot(error)
    result: c_ptr = ptr_add(slots, _RE_RESULT * 8)
    prior: int = pcc_gc_pin_slot_for_take(result, 0)
    if pcc_gc_foreign_lease_release(result, load_i64(tokens, _RE_RESULT * 8)) != 0:
        pcc_platform_abort()
        return 0
    store_i64(tokens, _RE_RESULT * 8, 0)
    return prior


def _re_error(slots: c_ptr, tokens: c_ptr, tag: int, message: c_ptr) -> None:
    if py_err_occurred() != 0:
        return
    store_ptr(slots, _RE_RAISED * 8, py_exc_new(tag, message))
    if _re_adopt(slots, tokens, _RE_RAISED) == 0:
        return
    # TLS acquires its own owner; this frame keeps the exception through cleanup.
    py_raise(load_ptr(slots, _RE_RAISED * 8))


def _re_method(slots: c_ptr, tokens: c_ptr, name: c_ptr, kind: int) -> int:
    store_ptr(slots, _RE_CAPTURES * 8, py_tuple_new(4))
    if _re_adopt(slots, tokens, _RE_CAPTURES) == 0:
        return 0
    store_ptr(slots, _RE_KIND * 8, py_int_from_i64(kind))
    if _re_adopt(slots, tokens, _RE_KIND) == 0:
        return 0
    py_tuple_set_item(load_ptr(slots, _RE_CAPTURES * 8), 0, load_ptr(slots, _RE_TEXT * 8))
    py_tuple_set_item(load_ptr(slots, _RE_CAPTURES * 8), 1, load_ptr(slots, _RE_SPANS * 8))
    py_tuple_set_item(load_ptr(slots, _RE_CAPTURES * 8), 2, load_ptr(slots, _RE_KIND * 8))
    py_tuple_set_item(load_ptr(slots, _RE_CAPTURES * 8), 3, load_ptr(slots, _RE_NAMES * 8))
    if py_err_occurred() != 0:
        return 0
    store_ptr(slots, _RE_FUNCTION * 8, py_func_new_named(
        pcc_re_match_method_address(), load_ptr(slots, _RE_CAPTURES * 8), name
    ))
    if _re_adopt(slots, tokens, _RE_FUNCTION) == 0:
        return 0
    status: int = py_obj_setattr(load_ptr(slots, _RE_RESULT * 8), name, load_ptr(slots, _RE_FUNCTION * 8))
    if status != 0 or py_err_occurred() != 0:
        py_runtime_error_if_unset(cstr("re.Match"), cstr("method installation failed"))
        return 0
    _re_drop(slots, tokens, _RE_FUNCTION)
    _re_drop(slots, tokens, _RE_KIND)
    _re_drop(slots, tokens, _RE_CAPTURES)
    return 1


def _re_construct(slots: c_ptr, tokens: c_ptr, caps: c_ptr, ngroups: int, flags: int) -> int:
    class_slot: c_ptr = ptr_add(slots, _RE_CLASS * 8)
    token: int = pcc_re_match_class_copy_lease(class_slot)
    if token < 0:
        py_runtime_error_if_unset(cstr("re.Match"), cstr("class publication failed"))
        return 0
    store_i64(tokens, _RE_CLASS * 8, token)
    store_ptr(slots, _RE_RESULT * 8, py_instance_new(load_ptr(class_slot, 0)))
    if _re_adopt(slots, tokens, _RE_RESULT) == 0:
        return 0
    store_ptr(slots, _RE_SPANS * 8, py_tuple_new((ngroups + 1) * 2))
    if _re_adopt(slots, tokens, _RE_SPANS) == 0:
        return 0
    index: int = 0
    while index < (ngroups + 1) * 2:
        store_ptr(slots, _RE_ITEM * 8, py_int_from_i64(load_i64(caps, index * 8)))
        if _re_adopt(slots, tokens, _RE_ITEM) == 0:
            return 0
        py_tuple_set_item(load_ptr(slots, _RE_SPANS * 8), index, load_ptr(slots, _RE_ITEM * 8))
        if py_err_occurred() != 0:
            return 0
        _re_drop(slots, tokens, _RE_ITEM)
        index = index + 1
    store_ptr(slots, _RE_NAMES * 8, py_tuple_new(ngroups))
    if _re_adopt(slots, tokens, _RE_NAMES) == 0:
        return 0
    names: c_ptr = stack_alloc(1024)
    status: int = pcc_re_engine_group_names_flags(py_str_utf8(load_ptr(slots, _RE_PATTERN * 8)), flags, names, 1024)
    if status < 0:
        py_runtime_error_if_unset(cstr("re.Match"), cstr("group name table unavailable"))
        return 0
    cursor: c_ptr = names
    group: int = 0
    while group < ngroups:
        length: int = 0
        while load_i8(cursor, length) != 0:
            length = length + 1
        if length > 0:
            store_ptr(slots, _RE_ITEM * 8, py_str_new(cursor, length))
        else:
            store_ptr(slots, _RE_ITEM * 8, global_load_ptr("py_None"))
        if _re_adopt(slots, tokens, _RE_ITEM) == 0:
            return 0
        py_tuple_set_item(load_ptr(slots, _RE_NAMES * 8), group, load_ptr(slots, _RE_ITEM * 8))
        if py_err_occurred() != 0:
            return 0
        _re_drop(slots, tokens, _RE_ITEM)
        cursor: c_ptr = ptr_add(cursor, length + 1)
        group = group + 1
    if _re_method(slots, tokens, cstr("group"), 0) == 0:
        return 0
    if _re_method(slots, tokens, cstr("start"), 1) == 0:
        return 0
    if _re_method(slots, tokens, cstr("end"), 2) == 0:
        return 0
    if _re_method(slots, tokens, cstr("span"), 3) == 0:
        return 0
    if _re_method(slots, tokens, cstr("groups"), 4) == 0:
        return 0
    return _re_method(slots, tokens, cstr("groupdict"), 5)


def _re_bytes_ascii(data: c_ptr, length: int) -> int:
    index: int = 0
    while index < length:
        byte: int = load_i8(data, index)
        if byte < 0 or byte >= 128:
            return 0
        index = index + 1
    return 1


def _re_run_fullmatch(slots: c_ptr, tokens: c_ptr, flags: int, start: int, endpos: int) -> None:
    ok: int = 1
    if (flags & ~26) != 0:
        _re_error(slots, tokens, 11, cstr("pcc re: flags outside the native regex subset (no-libpython)"))
        ok = 0
    if ok != 0:
        if py_str_check(load_ptr(slots, _RE_PATTERN * 8)) == 0 or py_str_check(load_ptr(slots, _RE_TEXT * 8)) == 0:
            _re_error(slots, tokens, 3, cstr("fullmatch requires string pattern and text"))
            ok = 0
    if ok != 0:
        if strlen(py_str_utf8(load_ptr(slots, _RE_PATTERN * 8))) != py_str_byte_len(load_ptr(slots, _RE_PATTERN * 8)):
            _re_error(slots, tokens, 11, cstr("pcc re: embedded-NUL patterns outside native engine capability"))
            ok = 0
    if ok != 0:
        if _re_bytes_ascii(py_str_utf8(load_ptr(slots, _RE_TEXT * 8)), py_str_byte_len(load_ptr(slots, _RE_TEXT * 8))) == 0:
            _re_error(slots, tokens, 11, cstr("pcc re: non-ASCII text outside the native regex subset"))
            ok = 0
    if ok != 0:
        if _re_bytes_ascii(py_str_utf8(load_ptr(slots, _RE_PATTERN * 8)), py_str_byte_len(load_ptr(slots, _RE_PATTERN * 8))) == 0:
            _re_error(slots, tokens, 11, cstr("pcc re: non-ASCII pattern outside the native regex subset"))
            ok = 0
    if ok != 0:
        length: int = py_str_byte_len(load_ptr(slots, _RE_TEXT * 8))
        if start < 0:
            start = 0
        if start > length:
            start = length
        if endpos < 0:
            endpos = 0
        if endpos > length:
            endpos = length
        # Do not raise endpos up to start: that changes an invalid window
        # into a successful empty match. The core keeps original offsets.
    if ok != 0:
        caps: c_ptr = stack_alloc(512)
        groups: c_ptr = stack_alloc(8)
        store_i64(groups, 0, 0)
        status: int = pcc_re_engine_fullmatch_flags(
            py_str_utf8(load_ptr(slots, _RE_PATTERN * 8)), flags,
            py_str_utf8(load_ptr(slots, _RE_TEXT * 8)), endpos,
            start, caps, 64, groups,
        )
        if status == 1:
            _re_construct(slots, tokens, caps, load_i64(groups, 0), flags)
        elif status == 0:
            store_ptr(slots, _RE_RESULT * 8, global_load_ptr("py_None"))
            _re_adopt(slots, tokens, _RE_RESULT)
        elif status == -1:
            _re_error(slots, tokens, 11, cstr("pcc re: pattern outside the native regex subset (no-libpython)"))
        elif status == -4:
            _re_error(slots, tokens, 11, cstr("pcc re: non-ASCII text outside the native regex subset"))
        elif status == -2:
            _re_error(slots, tokens, 7, cstr("pcc re: native regex engine recursion-depth limit flag (status=-2)"))
        elif status == -3:
            _re_error(slots, tokens, 7, cstr("pcc re: native regex engine argument/capture-capacity failure (status=-3)"))
        else:
            _re_error(slots, tokens, 7, cstr("pcc re: native regex engine unexpected status"))


@c_abi_export("pcc_re_engine_fullmatch_into")
def pcc_re_engine_fullmatch_into(borrowed: c_ptr, slots: c_ptr, flags: int) -> int:
    tokens: c_ptr = stack_alloc(_RE_SLOTS * 8)
    memset(tokens, 0, _RE_SLOTS * 8)
    ok: int = _re_copy_input(slots, tokens, _RE_PATTERN, borrowed, 0)
    if ok != 0:
        ok = _re_copy_input(slots, tokens, _RE_TEXT, borrowed, 1)
    if ok != 0:
        _re_run_fullmatch(slots, tokens, flags, 0, 9223372036854775807)
    return _re_finish(slots, tokens)


py_tuple_get = extern("py_tuple_get", (c_ptr, c_int64), c_ptr)
py_tuple_len = extern("py_tuple_len", (c_ptr,), c_int64)
py_int_to_i64 = extern("py_int_to_i64", (c_ptr, c_ptr), c_int64)
py_obj_has_index = extern("py_obj_has_index", (c_ptr,), c_int64)
py_obj_index_slots = extern("py_obj_index_slots", (c_ptr, c_ptr), c_int64)
py_index_i64_checked = extern("py_index_i64_checked", (c_ptr,), c_int64)
py_str_byte_slice_i64 = extern("py_str_byte_slice_i64", (c_ptr, c_int64, c_int64), c_ptr)
py_dict_new = extern("py_dict_new", (), c_ptr)
py_dict_set = extern("py_dict_set", (c_ptr, c_ptr, c_ptr), c_void)

_CB_CAPTURES = 2
_CB_ARGS = 3
_CB_TEXT = 4
_CB_SPANS = 5
_CB_NAMES = 6
_CB_KIND = 7
_CB_REQUEST = 8
_CB_ITEM = 9
_CB_NAME = 10
_CB_DEFAULT = 12
_CB_INDEX = 13


def _re_span_value(slots: c_ptr, tokens: c_ptr, index: int) -> int:
    store_ptr(slots, _CB_ITEM * 8, py_tuple_get(load_ptr(slots, _CB_SPANS * 8), index))
    if _re_adopt(slots, tokens, _CB_ITEM) == 0:
        return -1
    overflow: c_ptr = stack_alloc(4)
    memset(overflow, 0, 4)
    value: int = py_int_to_i64(load_ptr(slots, _CB_ITEM * 8), overflow)
    if load_i32(overflow, 0) != 0:
        _re_error(slots, tokens, 7, cstr("invalid internal Match span"))
    _re_drop(slots, tokens, _CB_ITEM)
    return value


def _re_same_name(first: c_ptr, first_length: int, second: c_ptr, second_length: int) -> int:
    if first_length != second_length:
        return 0
    index: int = 0
    while index < first_length:
        if load_i8(first, index) != load_i8(second, index):
            return 0
        index = index + 1
    return 1


def _re_select_group(slots: c_ptr, tokens: c_ptr, argument: int, groups: int) -> int:
    store_ptr(slots, _CB_REQUEST * 8, py_tuple_get(load_ptr(slots, _CB_ARGS * 8), argument))
    if _re_adopt(slots, tokens, _CB_REQUEST) == 0:
        return -1
    selected: int = -1
    if py_str_check(load_ptr(slots, _CB_REQUEST * 8)) != 0:
        group: int = 1
        while group <= groups and selected < 0 and py_err_occurred() == 0:
            store_ptr(slots, _CB_NAME * 8, py_tuple_get(load_ptr(slots, _CB_NAMES * 8), group - 1))
            if _re_adopt(slots, tokens, _CB_NAME) == 0:
                break
            if py_str_check(load_ptr(slots, _CB_NAME * 8)) != 0:
                if _re_same_name(
                    py_str_utf8(load_ptr(slots, _CB_REQUEST * 8)), py_str_byte_len(load_ptr(slots, _CB_REQUEST * 8)),
                    py_str_utf8(load_ptr(slots, _CB_NAME * 8)), py_str_byte_len(load_ptr(slots, _CB_NAME * 8)),
                ) != 0:
                    selected = group
            _re_drop(slots, tokens, _CB_NAME)
            group = group + 1
    elif py_obj_has_index(load_ptr(slots, _CB_REQUEST * 8)) != 0:
        index_slot: c_ptr = ptr_add(slots, _CB_INDEX * 8)
        status: int = py_obj_index_slots(ptr_add(slots, _CB_REQUEST * 8), index_slot)
        if status == 0 and _re_adopt(slots, tokens, _CB_INDEX) != 0:
            overflow: c_ptr = stack_alloc(4)
            memset(overflow, 0, 4)
            selected = py_int_to_i64(load_ptr(index_slot, 0), overflow)
            if load_i32(overflow, 0) != 0:
                selected = -1
        _re_drop(slots, tokens, _CB_INDEX)
    _re_drop(slots, tokens, _CB_REQUEST)
    if py_err_occurred() != 0:
        return -1
    if selected < 0 or selected > groups:
        _re_error(slots, tokens, 5, cstr("no such group"))
        return -1
    return selected


def _re_group_item(slots: c_ptr, tokens: c_ptr, group: int, default_slot: int) -> int:
    lo: int = _re_span_value(slots, tokens, group * 2)
    if py_err_occurred() != 0:
        return 0
    hi: int = _re_span_value(slots, tokens, group * 2 + 1)
    if py_err_occurred() != 0:
        return 0
    if lo >= 0 and hi >= 0:
        store_ptr(slots, _CB_ITEM * 8, py_str_byte_slice_i64(load_ptr(slots, _CB_TEXT * 8), lo, hi))
        return _re_adopt(slots, tokens, _CB_ITEM)
    if default_slot < 0:
        store_ptr(slots, _CB_ITEM * 8, global_load_ptr("py_None"))
        return _re_adopt(slots, tokens, _CB_ITEM)
    # Default already has an owning leased slot. Copy, never steal its owner.
    token: int = pcc_gc_root_copy_lease(
        ptr_add(slots, _CB_ITEM * 8), ptr_add(slots, default_slot * 8)
    )
    if token < 0:
        py_runtime_error_if_unset(cstr("re.Match"), cstr("default owner copy failed"))
        return 0
    store_i64(tokens, _CB_ITEM * 8, token)
    return 1


def _re_callback_result(slots: c_ptr, tokens: c_ptr, kind: int, nargs: int, groups: int) -> None:
    if kind == 4 or kind == 5:
        if nargs > 1:
            _re_error(slots, tokens, 3, cstr("Match groups method accepts at most one default"))
            return
        if nargs == 1:
            store_ptr(slots, _CB_DEFAULT * 8, py_tuple_get(load_ptr(slots, _CB_ARGS * 8), 0))
        else:
            store_ptr(slots, _CB_DEFAULT * 8, global_load_ptr("py_None"))
        if _re_adopt(slots, tokens, _CB_DEFAULT) == 0:
            return
        if kind == 4:
            store_ptr(slots, _RE_RESULT * 8, py_tuple_new(groups))
        else:
            store_ptr(slots, _RE_RESULT * 8, py_dict_new())
        if _re_adopt(slots, tokens, _RE_RESULT) == 0:
            return
        group: int = 1
        while group <= groups and py_err_occurred() == 0:
            include: int = 1
            if kind == 5:
                store_ptr(slots, _CB_NAME * 8, py_tuple_get(load_ptr(slots, _CB_NAMES * 8), group - 1))
                if _re_adopt(slots, tokens, _CB_NAME) == 0:
                    return
                include = py_str_check(load_ptr(slots, _CB_NAME * 8))
            if include != 0:
                if _re_group_item(slots, tokens, group, _CB_DEFAULT) == 0:
                    return
                if kind == 4:
                    py_tuple_set_item(load_ptr(slots, _RE_RESULT * 8), group - 1, load_ptr(slots, _CB_ITEM * 8))
                else:
                    py_dict_set(load_ptr(slots, _RE_RESULT * 8), load_ptr(slots, _CB_NAME * 8), load_ptr(slots, _CB_ITEM * 8))
                _re_drop(slots, tokens, _CB_ITEM)
            if kind == 5:
                _re_drop(slots, tokens, _CB_NAME)
            group = group + 1
        return
    if kind < 0 or kind > 3:
        _re_error(slots, tokens, 7, cstr("invalid internal Match method"))
        return
    if kind != 0 and nargs > 1:
        _re_error(slots, tokens, 3, cstr("Match span method accepts at most one group"))
        return
    if kind == 0 and nargs > 1:
        store_ptr(slots, _RE_RESULT * 8, py_tuple_new(nargs))
        if _re_adopt(slots, tokens, _RE_RESULT) == 0:
            return
        argument: int = 0
        while argument < nargs and py_err_occurred() == 0:
            selected: int = _re_select_group(slots, tokens, argument, groups)
            if selected < 0 or _re_group_item(slots, tokens, selected, -1) == 0:
                return
            py_tuple_set_item(load_ptr(slots, _RE_RESULT * 8), argument, load_ptr(slots, _CB_ITEM * 8))
            _re_drop(slots, tokens, _CB_ITEM)
            argument = argument + 1
        return
    selected: int = 0
    if nargs == 1:
        selected = _re_select_group(slots, tokens, 0, groups)
        if selected < 0:
            return
    lo: int = _re_span_value(slots, tokens, selected * 2)
    if py_err_occurred() != 0:
        return
    hi: int = _re_span_value(slots, tokens, selected * 2 + 1)
    if py_err_occurred() != 0:
        return
    if kind == 0:
        if lo >= 0 and hi >= 0:
            store_ptr(slots, _RE_RESULT * 8, py_str_byte_slice_i64(load_ptr(slots, _CB_TEXT * 8), lo, hi))
        else:
            store_ptr(slots, _RE_RESULT * 8, global_load_ptr("py_None"))
    elif kind == 1:
        store_ptr(slots, _RE_RESULT * 8, py_int_from_i64(lo))
    elif kind == 2:
        store_ptr(slots, _RE_RESULT * 8, py_int_from_i64(hi))
    else:
        store_ptr(slots, _RE_RESULT * 8, py_tuple_new(2))
    if _re_adopt(slots, tokens, _RE_RESULT) == 0:
        return
    if kind == 3:
        store_ptr(slots, _CB_ITEM * 8, py_int_from_i64(lo))
        if _re_adopt(slots, tokens, _CB_ITEM) == 0:
            return
        py_tuple_set_item(load_ptr(slots, _RE_RESULT * 8), 0, load_ptr(slots, _CB_ITEM * 8))
        _re_drop(slots, tokens, _CB_ITEM)
        store_ptr(slots, _CB_ITEM * 8, py_int_from_i64(hi))
        if _re_adopt(slots, tokens, _CB_ITEM) == 0:
            return
        py_tuple_set_item(load_ptr(slots, _RE_RESULT * 8), 1, load_ptr(slots, _CB_ITEM * 8))
        _re_drop(slots, tokens, _CB_ITEM)


@c_abi_export("pcc_re_match_method_into")
def pcc_re_match_method_into(borrowed: c_ptr, slots: c_ptr) -> int:
    tokens: c_ptr = stack_alloc(_RE_SLOTS * 8)
    memset(tokens, 0, _RE_SLOTS * 8)
    ok: int = _re_copy_input(slots, tokens, _CB_CAPTURES, borrowed, 0)
    if ok != 0:
        ok = _re_copy_input(slots, tokens, _CB_ARGS, borrowed, 1)
    if ok != 0 and py_tuple_len(load_ptr(slots, _CB_CAPTURES * 8)) != 4:
        _re_error(slots, tokens, 7, cstr("invalid Match method captures"))
        ok = 0
    if ok != 0:
        store_ptr(slots, _CB_TEXT * 8, py_tuple_get(load_ptr(slots, _CB_CAPTURES * 8), 0))
        ok = _re_adopt(slots, tokens, _CB_TEXT)
    if ok != 0:
        store_ptr(slots, _CB_SPANS * 8, py_tuple_get(load_ptr(slots, _CB_CAPTURES * 8), 1))
        ok = _re_adopt(slots, tokens, _CB_SPANS)
    if ok != 0:
        store_ptr(slots, _CB_KIND * 8, py_tuple_get(load_ptr(slots, _CB_CAPTURES * 8), 2))
        ok = _re_adopt(slots, tokens, _CB_KIND)
    if ok != 0:
        store_ptr(slots, _CB_NAMES * 8, py_tuple_get(load_ptr(slots, _CB_CAPTURES * 8), 3))
        ok = _re_adopt(slots, tokens, _CB_NAMES)
    if ok != 0:
        overflow: c_ptr = stack_alloc(4)
        memset(overflow, 0, 4)
        kind: int = py_int_to_i64(load_ptr(slots, _CB_KIND * 8), overflow)
        if load_i32(overflow, 0) != 0:
            _re_error(slots, tokens, 7, cstr("invalid Match method kind"))
        else:
            nargs: int = 0
            if ptr_is_null(load_ptr(slots, _CB_ARGS * 8)) == 0:
                nargs = py_tuple_len(load_ptr(slots, _CB_ARGS * 8))
            groups: int = (py_tuple_len(load_ptr(slots, _CB_SPANS * 8)) >> 1) - 1
            _re_callback_result(slots, tokens, kind, nargs, groups)
    return _re_finish(slots, tokens)


py_index_i64_checked_slots = extern("py_index_i64_checked_slots", (c_ptr,), c_int64)


def _re_pattern_bound(slots: c_ptr, tokens: c_ptr, index: int) -> int:
    # Args owns the bound while tuple_get creates an independent NEW owner.
    store_ptr(slots, _RE_ITEM * 8, py_tuple_get(load_ptr(slots, 13 * 8), index))
    if _re_adopt(slots, tokens, _RE_ITEM) == 0:
        return 0
    value: int = py_index_i64_checked_slots(ptr_add(slots, _RE_ITEM * 8))
    _re_drop(slots, tokens, _RE_ITEM)
    return value


@c_abi_export("pcc_re_pattern_fullmatch_into")
def pcc_re_pattern_fullmatch_into(borrowed: c_ptr, slots: c_ptr) -> int:
    """Callback captures are (pattern, kind, flags); args are (text, pos?, end?).

    This native function has no keyword signature yet: generic function dispatch
    explicitly rejects keyword arguments before entering this positional bridge.
    """
    tokens: c_ptr = stack_alloc(_RE_SLOTS * 8)
    memset(tokens, 0, _RE_SLOTS * 8)
    ok: int = _re_copy_input(slots, tokens, 12, borrowed, 0)
    if ok != 0:
        ok = _re_copy_input(slots, tokens, 13, borrowed, 1)
    nargs: int = 0
    if ok != 0:
        if ptr_is_null(load_ptr(slots, 13 * 8)) == 0:
            nargs = py_tuple_len(load_ptr(slots, 13 * 8))
        if nargs < 1 or nargs > 3:
            _re_error(slots, tokens, 3, cstr("Pattern.fullmatch expects string, optional pos and endpos"))
            ok = 0
    if ok != 0:
        if ptr_is_null(load_ptr(slots, 12 * 8)) != 0 or py_tuple_len(load_ptr(slots, 12 * 8)) != 3:
            _re_error(slots, tokens, 7, cstr("invalid Pattern.fullmatch captures"))
            ok = 0
    if ok != 0:
        store_ptr(slots, _RE_PATTERN * 8, py_tuple_get(load_ptr(slots, 12 * 8), 0))
        ok = _re_adopt(slots, tokens, _RE_PATTERN)
    if ok != 0:
        store_ptr(slots, _RE_TEXT * 8, py_tuple_get(load_ptr(slots, 13 * 8), 0))
        ok = _re_adopt(slots, tokens, _RE_TEXT)
    flags: int = 0
    if ok != 0:
        store_ptr(slots, _RE_ITEM * 8, py_tuple_get(load_ptr(slots, 12 * 8), 2))
        ok = _re_adopt(slots, tokens, _RE_ITEM)
        if ok != 0:
            flags = py_index_i64_checked_slots(ptr_add(slots, _RE_ITEM * 8))
        _re_drop(slots, tokens, _RE_ITEM)
    start: int = 0
    endpos: int = 9223372036854775807
    if ok != 0 and py_err_occurred() == 0 and nargs >= 2:
        start = _re_pattern_bound(slots, tokens, 1)
    if ok != 0 and py_err_occurred() == 0 and nargs >= 3:
        endpos = _re_pattern_bound(slots, tokens, 2)
    if ok != 0 and py_err_occurred() == 0:
        _re_run_fullmatch(slots, tokens, flags, start, endpos)
    return _re_finish(slots, tokens)


@c_abi_export("pcc_re_match_instance_into")
def pcc_re_match_instance_into(slots: c_ptr) -> int:
    """Build only an instance for legacy callers using the shared class lease."""
    tokens: c_ptr = stack_alloc(_RE_SLOTS * 8)
    memset(tokens, 0, _RE_SLOTS * 8)
    class_slot: c_ptr = ptr_add(slots, _RE_CLASS * 8)
    token: int = pcc_re_match_class_copy_lease(class_slot)
    if token < 0:
        py_runtime_error_if_unset(cstr("re.Match"), cstr("class publication failed"))
    else:
        store_i64(tokens, _RE_CLASS * 8, token)
        store_ptr(slots, _RE_RESULT * 8, py_instance_new(load_ptr(class_slot, 0)))
        _re_adopt(slots, tokens, _RE_RESULT)
    return _re_finish(slots, tokens)


pcc_re_pattern_fullmatch_address = extern("pcc_re_pattern_fullmatch_address", (), c_ptr)


def _re_pattern_install(slots: c_ptr, tokens: c_ptr, flags: int) -> None:
    # Prepare the static return before mutation, then reuse the constructor's
    # general capture/function installation with a different receiver slot.
    store_ptr(slots, _RE_RESULT * 8, global_load_ptr("py_None"))
    if _re_adopt(slots, tokens, _RE_RESULT) == 0:
        return
    _re_pattern_add_method(slots, tokens, 2, cstr("fullmatch"), 6, flags, pcc_re_pattern_fullmatch_address())


@c_abi_export("pcc_re_pattern_install_into")
def pcc_re_pattern_install_into(borrowed: c_ptr, slots: c_ptr, flags: int) -> int:
    """Install only the optional fullmatch method; caller supplies rooted inputs."""
    tokens: c_ptr = stack_alloc(_RE_SLOTS * 8)
    memset(tokens, 0, _RE_SLOTS * 8)
    ok: int = _re_copy_input(slots, tokens, 2, borrowed, 0)
    if ok != 0:
        ok = _re_copy_input(slots, tokens, 3, borrowed, 1)
    if ok != 0:
        _re_pattern_install(slots, tokens, flags)
    return _re_finish(slots, tokens)


pcc_re_normalize_compile_pattern_into = extern("pcc_re_normalize_compile_pattern_into", (c_ptr, c_int64, c_ptr, c_ptr, c_ptr, c_ptr, c_ptr), c_int64)
pcc_re_engine_supported_flags = extern("pcc_re_engine_supported_flags", (c_ptr, c_int64), c_int32)
pcc_re_pattern_class_copy_lease = extern("pcc_re_pattern_class_copy_lease", (c_ptr,), c_int64)
pcc_re_pattern_legacy_address = extern("pcc_re_pattern_legacy_address", (), c_ptr)


def _re_pattern_add_method(slots: c_ptr, tokens: c_ptr, receiver: int, name: c_ptr, kind: int, flags: int, callback: c_ptr) -> int:
    # Pattern instance/result0 and normalized text3 stay leased throughout.
    store_ptr(slots, _RE_CAPTURES * 8, py_tuple_new(3))
    if _re_adopt(slots, tokens, _RE_CAPTURES) == 0:
        return 0
    store_ptr(slots, _RE_KIND * 8, py_int_from_i64(kind))
    if _re_adopt(slots, tokens, _RE_KIND) == 0:
        return 0
    store_ptr(slots, _RE_ITEM * 8, py_int_from_i64(flags))
    if _re_adopt(slots, tokens, _RE_ITEM) == 0:
        return 0
    py_tuple_set_item(load_ptr(slots, _RE_CAPTURES * 8), 0, load_ptr(slots, _RE_TEXT * 8))
    if py_err_occurred() != 0:
        return 0
    py_tuple_set_item(load_ptr(slots, _RE_CAPTURES * 8), 1, load_ptr(slots, _RE_KIND * 8))
    if py_err_occurred() != 0:
        return 0
    py_tuple_set_item(load_ptr(slots, _RE_CAPTURES * 8), 2, load_ptr(slots, _RE_ITEM * 8))
    if py_err_occurred() != 0:
        return 0
    store_ptr(slots, _RE_FUNCTION * 8, py_func_new_named(callback, load_ptr(slots, _RE_CAPTURES * 8), name))
    if _re_adopt(slots, tokens, _RE_FUNCTION) == 0:
        return 0
    status: int = py_obj_setattr(load_ptr(slots, receiver * 8), name, load_ptr(slots, _RE_FUNCTION * 8))
    if status != 0 or py_err_occurred() != 0:
        py_runtime_error_if_unset(cstr("re.Pattern"), cstr("method installation failed"))
        return 0
    _re_drop(slots, tokens, _RE_FUNCTION)
    _re_drop(slots, tokens, _RE_ITEM)
    _re_drop(slots, tokens, _RE_KIND)
    _re_drop(slots, tokens, _RE_CAPTURES)
    return 1


def _re_compile_pattern(slots: c_ptr, tokens: c_ptr, flags: int) -> None:
    if py_str_check(load_ptr(slots, _RE_PATTERN * 8)) == 0:
        _re_error(slots, tokens, 3, cstr("pcc re: re.compile pattern must be a string"))
        return
    # Preserve the existing general verbose/global-inline normalization.
    normalized_token: int = pcc_re_normalize_compile_pattern_into(load_ptr(slots, _RE_PATTERN * 8), flags, ptr_add(slots, _RE_NAMES * 8), ptr_add(slots, 12 * 8), ptr_add(tokens, 12 * 8), ptr_add(slots, 13 * 8), ptr_add(tokens, 13 * 8))
    if normalized_token < 0:
        py_runtime_error_if_unset(cstr("re.Pattern"), cstr("normalization owner lease failed"))
        return
    store_i64(tokens, _RE_NAMES * 8, normalized_token)
    if py_err_occurred() != 0:
        return
    store_ptr(slots, _RE_TEXT * 8, py_tuple_get(load_ptr(slots, _RE_NAMES * 8), 0))
    if _re_adopt(slots, tokens, _RE_TEXT) == 0:
        return
    store_ptr(slots, _RE_SPANS * 8, py_tuple_get(load_ptr(slots, _RE_NAMES * 8), 1))
    if _re_adopt(slots, tokens, _RE_SPANS) == 0:
        return
    engine_flags: int = py_index_i64_checked_slots(ptr_add(slots, _RE_SPANS * 8))
    if py_err_occurred() != 0:
        return
    _re_drop(slots, tokens, _RE_NAMES)
    if strlen(py_str_utf8(load_ptr(slots, _RE_TEXT * 8))) != py_str_byte_len(load_ptr(slots, _RE_TEXT * 8)):
        _re_error(slots, tokens, 11, cstr("pcc re: embedded-NUL patterns outside native engine capability"))
        return
    if pcc_re_engine_supported_flags(py_str_utf8(load_ptr(slots, _RE_TEXT * 8)), engine_flags) == 0:
        _re_error(slots, tokens, 11, cstr("pcc re: pattern outside the native regex subset (no-libpython)"))
        return
    token: int = pcc_re_pattern_class_copy_lease(ptr_add(slots, _RE_CLASS * 8))
    if token < 0:
        py_runtime_error_if_unset(cstr("re.Pattern"), cstr("class publication failed"))
        return
    store_i64(tokens, _RE_CLASS * 8, token)
    store_ptr(slots, _RE_RESULT * 8, py_instance_new(load_ptr(slots, _RE_CLASS * 8)))
    if _re_adopt(slots, tokens, _RE_RESULT) == 0:
        return
    status: int = py_obj_setattr(load_ptr(slots, _RE_RESULT * 8), cstr("pattern"), load_ptr(slots, _RE_PATTERN * 8))
    if status != 0 or py_err_occurred() != 0:
        py_runtime_error_if_unset(cstr("re.Pattern"), cstr("pattern attribute installation failed"))
        return
    callback: c_ptr = pcc_re_pattern_legacy_address()
    if _re_pattern_add_method(slots, tokens, _RE_RESULT, cstr("match"), 0, engine_flags, callback) == 0:
        return
    if _re_pattern_add_method(slots, tokens, _RE_RESULT, cstr("search"), 1, engine_flags, callback) == 0:
        return
    if _re_pattern_add_method(slots, tokens, _RE_RESULT, cstr("findall"), 2, engine_flags, callback) == 0:
        return
    if _re_pattern_add_method(slots, tokens, _RE_RESULT, cstr("sub"), 3, engine_flags, callback) == 0:
        return
    if _re_pattern_add_method(slots, tokens, _RE_RESULT, cstr("split"), 4, engine_flags, callback) == 0:
        return
    if _re_pattern_add_method(slots, tokens, _RE_RESULT, cstr("finditer"), 5, engine_flags, callback) == 0:
        return
    _re_pattern_add_method(slots, tokens, _RE_RESULT, cstr("fullmatch"), 6, engine_flags, pcc_re_pattern_fullmatch_address())


@c_abi_export("pcc_re_compile_pattern_into")
def pcc_re_compile_pattern_into(borrowed: c_ptr, slots: c_ptr, flags: int) -> int:
    tokens: c_ptr = stack_alloc(_RE_SLOTS * 8)
    memset(tokens, 0, _RE_SLOTS * 8)
    if _re_copy_input(slots, tokens, _RE_PATTERN, borrowed, 0) != 0:
        _re_compile_pattern(slots, tokens, flags)
    return _re_finish(slots, tokens)
