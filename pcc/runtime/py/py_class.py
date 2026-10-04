"""Phase 4c.14: pcc-Python port of py_class.c.

Public ``PyClassObject``, ``PyClassMethod``, ``PyInstanceObject``, descriptor,
header, flag, and type-tag ABI values come from the generated
``py_abi_constants`` module. Numeric copies do not belong in this prose: the
C headers and generator are the layout authority.

Notes on int width: py_class_new / py_instance_get_field / py_instance_
set_field / py_instance_setattr / py_isinstance use int32 in their C
ABI. pcc-Python's int → i64 default applies inside the function body
for arithmetic / comparison (auto-sext) but NOT for direct user-helper
calls. So we keep i32 args (via `: int` which the ABI forces to i32),
inline most logic, and only call extern (C) helpers where the C side
handles its own int width.
"""

__pcc_runtime_port__ = True

from pcc.extern import (
    extern,
    c_abi_export,
    c_ptr,
    c_int32,
    c_int64,
    c_void,
)
from pcc.runtime.py.py_abi_constants import (
    PY_TYPE_NONE,
    PYSTROBJECT_BYTE_LEN_OFFSET,
    PYSTROBJECT_DATA_OFFSET,
    PYTUPLEOBJECT_ITEMS_OFFSET,
)
from pcc.runtime.py.py_abi_constants import (
    PY_FLAG_FUNC_TRANSPARENT_CALL,
    C_POINTER_SIZE,
    DICTENTRY_KEY_OFFSET,
    DICTENTRY_SIZE,
    DICTENTRY_VALUE_OFFSET,
    PYCLASSMETHOD_FUNC_OFFSET,
    PYCLASSMETHOD_NAME_HASH_OFFSET,
    PYCLASSMETHOD_NAME_LENGTH_OFFSET,
    PYCLASSMETHOD_NAME_OFFSET,
    PYCLASSMETHOD_SIZE,
    PYCLASSMETHODOBJECT_FUNC_OFFSET,
    PYCLASSMETHODOBJECT_SIZE,
    PYCLASSOBJECT_ATTRS_OFFSET,
    PYCLASSOBJECT_BASES_OFFSET,
    PYCLASSOBJECT_DEL_METHOD_OFFSET,
    PYCLASSOBJECT_FIELD_NAMES_OFFSET,
    PYCLASSOBJECT_INSTANCE_SIZE_OFFSET,
    PYCLASSOBJECT_METACLASS_OFFSET,
    PYCLASSOBJECT_METHODS_OFFSET,
    PYCLASSOBJECT_MRO_OFFSET,
    PYCLASSOBJECT_NAME_OFFSET,
    PYCLASSOBJECT_N_BASES_OFFSET,
    PYCLASSOBJECT_N_FIELDS_OFFSET,
    PYCLASSOBJECT_N_METHODS_OFFSET,
    PYCLASSOBJECT_N_MRO_OFFSET,
    PYCLASSOBJECT_SIZE,
    PYCLASSOBJECT_TYPE_TAG_ALLOC_OFFSET,
    PYDICTOBJECT_ENTRIES_OFFSET,
    PYDICTOBJECT_ENTRIES_USED_OFFSET,
    PYINSTANCEOBJECT_CLS_OFFSET,
    PYINSTANCEOBJECT_FIELDS_OFFSET,
    PYINSTANCEOBJECT_SIZE,
    PYOBJECTHEADER_FLAGS_OFFSET,
    PYOBJECTHEADER_REFCOUNT_OFFSET,
    PYOBJECTHEADER_TYPE_TAG_OFFSET,
    PY_FLAG_GC_MALLOC_ALLOC,
    PY_FLAG_IMMORTAL,
    PYPROPERTYOBJECT_FDEL_OFFSET,
    PYPROPERTYOBJECT_FGET_OFFSET,
    PYPROPERTYOBJECT_FSET_OFFSET,
    PYPROPERTYOBJECT_SIZE,
    PYSTATICMETHODOBJECT_FUNC_OFFSET,
    PYSTATICMETHODOBJECT_SIZE,
    PY_TYPE_CLASS,
    PY_TYPE_CLASSMETHOD,
    PY_TYPE_DICT,
    PY_TYPE_EXC,
    PY_TYPE_FUNC,
    PY_TYPE_INSTANCE,
    PY_TYPE_LIST,
    PY_TYPE_PROPERTY,
    PY_TYPE_STATICMETHOD,
    PY_TYPE_STR,
    PY_TYPE_TUPLE,
    PY_TYPE_USER_CLASS_START,
    PY_TYPE_VALUEBOX,
)
from pcc.unsafe import (
    atomic_cas_i64,
    atomic_load_i64,
    atomic_rmw_i32,
    atomic_load_i32,
    cstr,
    define_global_i32,
    define_global_i64,
    define_thread_local_i32,
    define_thread_local_ptr_null,
    call_ptr1,
    call_ptr2,
    call_ptr3,
    call_ptr4,
    free,
    function_addr,
    global_addr,
    global_load_ptr,
    global_store_ptr,
    is_tagged_int,
    int_to_ptr,
    load_i8,
    load_i32,
    load_i64,
    load_ptr,
    malloc,
    memset,
    null,
    ptr_add,
    ptr_eq,
    ptr_is_null,
    ptr_to_int,
    realloc,
    stack_alloc,
    store_i8,
    store_i32,
    store_i64,
    store_ptr,
    strlen,
    untag_int,
)

# Number of classes that ever had ``__del__`` installed (class body or a later
# ``cls.__del__ = f``).  Never decremented: a stale nonzero count only costs
# the lookup.  ``py_user_del_dispatch`` skips its per-dealloc MRO walk while
# this is zero -- the common program, and every pcc1 compile.  Mirrors
# ``pcc_class_del_defined_count`` in py_substrate.c / py_class.c.
define_global_i32("pcc_class_del_defined_count", 0)
define_global_i64("pcc_object_new_cache_mutex_bits", 0)
define_global_i32("pcc_class_construct_owned_frame_map", 5)
define_global_i32("pcc_class_construct_borrowed_frame_map", -2)


py_incref = extern("py_incref", (c_ptr,), c_void)
py_decref = extern("py_decref", (c_ptr,), c_void)
py_str_utf8 = extern("py_str_utf8", (c_ptr,), c_ptr)
py_str_new = extern("py_str_new", (c_ptr, c_int64), c_ptr)
py_str_eq = extern("py_str_eq", (c_ptr, c_ptr), c_int64)
py_tuple_new = extern("py_tuple_new", (c_int64,), c_ptr)
py_tuple_get = extern("py_tuple_get", (c_ptr, c_int64), c_ptr)
py_tuple_len = extern("py_tuple_len", (c_ptr,), c_int64)
py_tuple_set_item = extern("py_tuple_set_item", (c_ptr, c_int64, c_ptr), c_void)
py_obj_call = extern("py_obj_call", (c_ptr, c_ptr, c_ptr), c_ptr)
py_obj_call_default = extern("py_obj_call_default", (c_ptr, c_ptr, c_ptr), c_ptr)
py_obj_call_context_is_deferred = extern("py_obj_call_context_is_deferred", (), c_int64)
py_obj_call_slots_sync = extern("py_obj_call_slots_sync", (c_ptr, c_ptr, c_ptr, c_ptr), c_int64)
py_tls_exc_swap_slot = extern("py_tls_exc_swap_slot", (c_ptr,), c_void)
pcc_gc_root_copy_lease = extern("pcc_gc_root_copy_lease", (c_ptr, c_ptr), c_int64)
pcc_gc_root_copy_borrowed_lease = extern("pcc_gc_root_copy_borrowed_lease", (c_ptr, c_ptr), c_int64)
pcc_gc_root_move = extern("pcc_gc_root_move", (c_ptr, c_ptr), c_int64)
pcc_gc_foreign_lease_acquire = extern("pcc_gc_foreign_lease_acquire", (c_ptr,), c_int64)
pcc_gc_foreign_lease_release = extern("pcc_gc_foreign_lease_release", (c_ptr, c_int64), c_int64)
pcc_gc_root_copy_lease_prepare_locked = extern("pcc_gc_root_copy_lease_prepare_locked", (c_ptr, c_ptr, c_int64, c_ptr), c_int64)
pcc_gc_root_copy_lease_finish = extern("pcc_gc_root_copy_lease_finish", (c_ptr,), c_void)
pcc_gc_resolve_root_slot_unlocked = extern("pcc_gc_resolve_root_slot_unlocked", (c_ptr, c_int64), c_ptr)
py_obj_getattr = extern("py_obj_getattr", (c_ptr, c_ptr), c_ptr)
py_func_call_kwargs = extern("py_func_call_kwargs", (c_ptr, c_ptr, c_ptr), c_ptr)
py_call_validate_kwargs = extern("py_call_validate_kwargs", (c_ptr,), c_int64)
py_dict_subclass_getattr = extern("py_dict_subclass_getattr", (c_ptr, c_ptr), c_ptr)
py_func_call_bound_forward = extern("py_func_call_bound_forward", (c_ptr, c_ptr), c_ptr)
py_func_new_bound = extern(
    "py_func_new_bound", (c_ptr, c_ptr, c_ptr, c_ptr), c_ptr
)
py_class_new_abi = extern(
    "py_class_new", (c_ptr, c_ptr, c_int32, c_ptr, c_int32), c_ptr
)
py_dict_new = extern("py_dict_new", (), c_ptr)
py_dict_set = extern("py_dict_set", (c_ptr, c_ptr, c_ptr), c_void)
py_dict_get = extern("py_dict_get", (c_ptr, c_ptr), c_ptr)
py_dict_len = extern("py_dict_len", (c_ptr,), c_int64)
py_dict_keys = extern("py_dict_keys", (c_ptr,), c_ptr)
py_dict_update = extern("py_dict_update", (c_ptr, c_ptr), c_void)
py_dict_del = extern("py_dict_del", (c_ptr, c_ptr), c_int64)
py_builtin_type_class_tag = extern("py_builtin_type_class_tag", (c_ptr,), c_int32)
py_subs_alloc_user_tag = extern("py_subs_alloc_user_tag", (), c_int32)
pcc_capi_is_cext_type_tag = extern("pcc_capi_is_cext_type_tag", (c_int64,), c_int64)
py_int_from_i64 = extern("py_int_from_i64", (c_int64,), c_ptr)
py_bool_from_bit = extern("py_bool_from_bit", (c_int32,), c_ptr)
pcc_mutex_new = extern("pcc_mutex_new", (), c_ptr)
pcc_mutex_free = extern("pcc_mutex_free", (c_ptr,), c_void)
pcc_mutex_lock = extern("pcc_mutex_lock", (c_ptr,), c_int64)
pcc_mutex_unlock = extern("pcc_mutex_unlock", (c_ptr,), c_int64)
pcc_gc_scheduler_root_register_handle = extern(
    "pcc_gc_scheduler_root_register_handle", (c_ptr,), c_ptr
)
pcc_gc_scheduler_root_unregister_handle = extern(
    "pcc_gc_scheduler_root_unregister_handle", (c_ptr,), c_void
)
py_list_len = extern("py_list_len", (c_ptr,), c_int64)
py_list_get = extern("py_list_get", (c_ptr, c_int64), c_ptr)
py_gc_track = extern("py_gc_track", (c_ptr,), c_void)
py_gc_untrack = extern("py_gc_untrack", (c_ptr,), c_void)
pcc_gc_note_object_freeing = extern(
    "pcc_gc_note_object_freeing", (c_ptr,), c_void
)
pcc_gc_object_index_find = extern(
    "pcc_gc_object_index_find", (c_ptr,), c_ptr
)
py_user_del_dispatch = extern("py_user_del_dispatch", (c_ptr,), c_void)
py_weakref_invalidate = extern("py_weakref_invalidate", (c_ptr,), c_void)
py_exc_new = extern("py_exc_new", (c_int64, c_ptr), c_ptr)
py_raise = extern("py_raise", (c_ptr,), c_void)
# py_raise increfs; a caller that created the exception must release it.
py_raise_owned = extern("py_raise_owned", (c_ptr,), c_void)
py_runtime_error_if_unset = extern(
    "py_runtime_error_if_unset", (c_ptr, c_ptr), c_ptr
)
py_err_occurred = extern("py_err_occurred", (), c_int64)
py_current_exception = extern("py_current_exception", (), c_ptr)
py_clear_exception = extern("py_clear_exception", (), c_void)
py_exc_builtin_class = extern("py_exc_builtin_class", (c_int64,), c_ptr)
py_exc_matches = extern("py_exc_matches", (c_ptr, c_ptr), c_int64)
pcc_gc_store_ptr = extern("pcc_gc_store_ptr", (c_ptr, c_ptr, c_ptr), c_void)
pcc_gc_load_ptr = extern("pcc_gc_load_ptr", (c_ptr, c_ptr), c_ptr)
pcc_gc_note_relocation_read = extern("pcc_gc_note_relocation_read", (c_ptr,), c_ptr)
pcc_gc_alloc = extern("pcc_gc_alloc", (c_int64, c_int32, c_int32), c_ptr)
pcc_gc_publish_initialized = extern(
    "pcc_gc_publish_initialized", (c_ptr,), c_void
)
pcc_gc_pointer_register = extern(
    "pcc_gc_pointer_register", (c_ptr,), c_int64
)
pcc_gc_free_object_memory = extern("pcc_gc_free_object_memory", (c_ptr,), c_void)
pcc_py_gc_minor_graph_lock = extern("pcc_py_gc_minor_graph_lock", (), c_void)
pcc_py_gc_minor_graph_unlock = extern("pcc_py_gc_minor_graph_unlock", (), c_void)
pcc_gc_backend = extern("pcc_gc_backend", (), c_int64)
pcc_gc_note_store = extern("pcc_gc_note_store", (), c_void)
pcc_gc_note_write_barrier = extern(
    "pcc_gc_note_write_barrier",
    (c_ptr, c_ptr),
    c_void,
)
pcc_gc_note_slot_write_barrier = extern(
    "pcc_gc_note_slot_write_barrier",
    (c_ptr, c_ptr, c_ptr),
    c_void,
)
pcc_gc_backend4_zpage_register_owner_payload_span = extern(
    "pcc_gc_backend4_zpage_register_owner_payload_span",
    (c_ptr, c_ptr, c_int64),
    c_int64,
)
pcc_gc_backend4_zpage_unregister_owner_payload_span = extern(
    "pcc_gc_backend4_zpage_unregister_owner_payload_span",
    (c_ptr, c_ptr),
    c_int64,
)
pcc_gc_backend4_zpage_retarget_owner_payload_span = extern(
    "pcc_gc_backend4_zpage_retarget_owner_payload_span",
    (c_ptr, c_ptr, c_ptr, c_int64),
    c_int64,
)


# Helpers below take only ptrs / cstrs (no int args). They can be
# called from i32-param functions because pcc-Python doesn't need to
# sext anything at the call boundary.


def _class_require_result(result, helper_name, message):
    if ptr_is_null(result) != 0:
        py_runtime_error_if_unset(helper_name, message)
    return result


def _alloc_user_tag() -> int:
    # The substrate owns the shared counter, reserved tags and exhaustion.
    return py_subs_alloc_user_tag()


def _object_root():
    root = global_load_ptr("py_object_root_cache")
    if ptr_is_null(root) == 0:
        return root

    mro = malloc(C_POINTER_SIZE)
    if ptr_is_null(mro) != 0:
        return null()
    # This root is cached in a raw global pointer, not a relocation-updated
    # root slot.  Give it stable storage and register exact provenance.
    r = malloc(PYCLASSOBJECT_SIZE)
    if ptr_is_null(r) != 0:
        free(mro)
        return null()
    memset(r, 0, PYCLASSOBJECT_SIZE)
    store_i64(r, PYOBJECTHEADER_REFCOUNT_OFFSET, 1)
    store_i32(r, PYOBJECTHEADER_TYPE_TAG_OFFSET, PY_TYPE_CLASS)
    store_i32(
        r,
        PYOBJECTHEADER_FLAGS_OFFSET,
        PY_FLAG_IMMORTAL | PY_FLAG_GC_MALLOC_ALLOC,
    )
    if pcc_gc_pointer_register(r) < 0:
        free(r)
        free(mro)
        return null()
    store_ptr(r, PYCLASSOBJECT_NAME_OFFSET, cstr("object"))
    store_i32(r, PYCLASSOBJECT_N_BASES_OFFSET, 0)
    store_ptr(r, PYCLASSOBJECT_BASES_OFFSET, null())
    store_i32(r, PYCLASSOBJECT_N_MRO_OFFSET, 1)

    store_ptr(mro, 0, r)
    store_ptr(r, PYCLASSOBJECT_MRO_OFFSET, mro)

    store_i32(r, PYCLASSOBJECT_N_METHODS_OFFSET, 0)
    store_ptr(r, PYCLASSOBJECT_METHODS_OFFSET, null())
    store_i32(r, PYCLASSOBJECT_N_FIELDS_OFFSET, 0)
    store_ptr(r, PYCLASSOBJECT_FIELD_NAMES_OFFSET, null())
    store_i32(
        r,
        PYCLASSOBJECT_INSTANCE_SIZE_OFFSET,
        PYINSTANCEOBJECT_SIZE + C_POINTER_SIZE,
    )
    store_i32(r, PYCLASSOBJECT_TYPE_TAG_ALLOC_OFFSET, PY_TYPE_INSTANCE)
    store_ptr(r, PYCLASSOBJECT_DEL_METHOD_OFFSET, null())
    store_ptr(r, PYCLASSOBJECT_ATTRS_OFFSET, null())

    global_store_ptr("py_object_root_cache", r)
    return r


pcc_gc_pointer_is_managed = extern(
    "pcc_gc_pointer_is_managed", (c_ptr,), c_int64
)


def _ptr_can_have_header(o) -> bool:
    return pcc_gc_pointer_is_managed(o) != 0


def _ptr_is_class_of_validated_instance(cls) -> bool:
    """`_ptr_is_class` without the provenance probe, for a class pointer that
    came out of an already-validated instance's `cls` slot.

    The caller has just proved `o` is a managed object whose type tag says
    instance.  `py_instance_new` only ever stores a validated class there, the
    slot is documented as a borrowed uncounted class pointer, and classes carry
    PY_FLAG_IMMORTAL -- so reading this header is safe on the strength of the
    instance being valid.  Probing again cost a second lock plus hash probe on
    the hottest path in the runtime: every attribute access and method dispatch
    ran `_ptr_is_instance`, which paid the probe once for the instance and once
    more for its class.

    Note what this does NOT protect against either way: a stray over-release
    that frees the class and lets the address be reused answers "managed" from
    the index too (see the self-host over-release notes in AGENTS.md).  The
    probe was not buying safety against that; it only excluded a genuinely
    foreign pointer, which this slot cannot hold.  The relocation read is kept.
    """
    cls = pcc_gc_note_relocation_read(cls)
    if ptr_is_null(cls) != 0:
        return False
    return load_i32(cls, PYOBJECTHEADER_TYPE_TAG_OFFSET) == PY_TYPE_CLASS


def _ptr_is_class(o) -> bool:
    o = pcc_gc_note_relocation_read(o)
    if not _ptr_can_have_header(o):
        return False
    return load_i32(o, PYOBJECTHEADER_TYPE_TAG_OFFSET) == PY_TYPE_CLASS


def _note_class_defines_del() -> None:
    n: int = load_i32(global_addr("pcc_class_del_defined_count"), 0)
    store_i32(global_addr("pcc_class_del_defined_count"), 0, n + 1)


def _gc_backend_selected_fast() -> int:
    if load_i32(global_addr("pcc_gc_config_initialized"), 0) == 0:
        return pcc_gc_backend()
    return load_i32(global_addr("pcc_gc_backend_selected"), 0)


def _dealloc_ptr_is_instance(o) -> bool:
    # ``_ptr_is_instance`` for an object whose header this thread is about to
    # free: provenance is already established (we hold the last reference),
    # so the radix-walk ``_ptr_can_have_header`` probe is skipped.
    o = pcc_gc_note_relocation_read(o)
    tag: int = load_i32(o, PYOBJECTHEADER_TYPE_TAG_OFFSET)
    if tag != PY_TYPE_INSTANCE:
        if tag < PY_TYPE_USER_CLASS_START:
            return False
    cls = pcc_gc_load_ptr(o, ptr_add(o, PYINSTANCEOBJECT_CLS_OFFSET))
    if ptr_is_null(cls) != 0:
        return False
    return _ptr_is_class_of_validated_instance(cls)


def _ptr_is_instance(o) -> bool:
    o = pcc_gc_note_relocation_read(o)
    if not _ptr_can_have_header(o):
        return False
    tag: int = load_i32(o, PYOBJECTHEADER_TYPE_TAG_OFFSET)
    if tag != PY_TYPE_INSTANCE:
        if tag < PY_TYPE_USER_CLASS_START:
            return False
    cls = pcc_gc_load_ptr(o, ptr_add(o, PYINSTANCEOBJECT_CLS_OFFSET))
    if ptr_is_null(cls) != 0:
        return False
    return _ptr_is_class_of_validated_instance(cls)


def _class_note_borrowed_metadata_store(cls, value) -> None:
    _class_note_borrowed_metadata_slot_store(cls, null(), value)


def _class_note_borrowed_metadata_slot_store(cls, slot, value) -> None:
    if not _ptr_is_class(cls):
        return
    backend: int = pcc_gc_backend()
    if backend == 1 or backend == 2 or backend == 3 or backend == 4:
        pcc_gc_note_store()
    pcc_gc_note_slot_write_barrier(cls, slot, value)


def _strs_eq(a, b) -> int:
    if ptr_eq(a, b) != 0:
        return 1
    if ptr_is_null(a) != 0:
        return 0
    if ptr_is_null(b) != 0:
        return 0
    a0: int = load_i8(a, 0) & 0xFF
    b0: int = load_i8(b, 0) & 0xFF
    if a0 != b0:
        return 0
    if a0 == 0:
        return 1
    a1: int = load_i8(a, 1) & 0xFF
    b1: int = load_i8(b, 1) & 0xFF
    if a1 != b1:
        return 0
    if a1 == 0:
        return 1
    # One pass, comparing terminators, instead of strlen(a) + strlen(b) + a
    # bounded byte loop.  Those three walked the name up to three times for
    # every method and every field on every attribute access; the C mirror in
    # py_class.c always did this in one pass because it calls strcmp.  Exactly
    # equivalent: unequal lengths are caught when one side reaches its NUL
    # while the other has not, and equal-length strings are decided at the
    # first differing byte or at the shared terminator.  Bytes 0 and 1 are
    # already known equal and nonzero, so the scan resumes at index 2.
    result: int = -1
    i: int = 2
    while result < 0:
        ca: int = load_i8(a, i) & 0xFF
        cb: int = load_i8(b, i) & 0xFF
        if ca != cb:
            result = 0
        elif ca == 0:
            result = 1
        else:
            i = i + 1
    return result


def _method_name_signature(name, out) -> None:
    """Write the hash and length without allocating a Python result tuple."""
    value: int = 2166136261
    length: int = 0
    while True:
        char: int = load_i8(name, length) & 0xFF
        if char == 0:
            store_i32(out, 0, value)
            store_i32(out, 4, length & 0xFFFFFFFF)
            return
        value = ((value ^ char) * 16777619) & 0xFFFFFFFF
        length = length + 1


def _cstr_is_dunder_name(s) -> int:
    if load_i8(s, 0) != 95:
        return 0
    if load_i8(s, 1) != 95:
        return 0
    if strlen(s) != 8:
        return 0
    if load_i8(s, 2) != 110:
        return 0
    if load_i8(s, 3) != 97:
        return 0
    if load_i8(s, 4) != 109:
        return 0
    if load_i8(s, 5) != 101:
        return 0
    if load_i8(s, 6) != 95:
        return 0
    if load_i8(s, 7) != 95:
        return 0
    return 1


def _cstr_is_dunder_mro(s) -> int:
    if load_i8(s, 0) != 95:
        return 0
    if load_i8(s, 1) != 95:
        return 0
    if strlen(s) != 7:
        return 0
    if load_i8(s, 2) != 109:
        return 0
    if load_i8(s, 3) != 114:
        return 0
    if load_i8(s, 4) != 111:
        return 0
    if load_i8(s, 5) != 95:
        return 0
    if load_i8(s, 6) != 95:
        return 0
    return 1


def _cstr_is_dunder_class(s) -> int:
    if load_i8(s, 0) != 95:
        return 0
    if load_i8(s, 1) != 95:
        return 0
    if strlen(s) != 9:
        return 0
    if load_i8(s, 2) != 99:
        return 0
    if load_i8(s, 3) != 108:
        return 0
    if load_i8(s, 4) != 97:
        return 0
    if load_i8(s, 5) != 115:
        return 0
    if load_i8(s, 6) != 115:
        return 0
    if load_i8(s, 7) != 95:
        return 0
    if load_i8(s, 8) != 95:
        return 0
    return 1


def _cstr_is_dunder_dict(s) -> int:
    if load_i8(s, 0) != 95:
        return 0
    if load_i8(s, 1) != 95:
        return 0
    if strlen(s) != 8:
        return 0
    if load_i8(s, 2) != 100:
        return 0
    if load_i8(s, 3) != 105:
        return 0
    if load_i8(s, 4) != 99:
        return 0
    if load_i8(s, 5) != 116:
        return 0
    if load_i8(s, 6) != 95:
        return 0
    if load_i8(s, 7) != 95:
        return 0
    return 1


def _class_lookup_in_mro(cls, name):
    signature = stack_alloc(8)
    _method_name_signature(name, signature)
    name_hash: int = load_i32(signature, 0) & 0xFFFFFFFF
    name_length: int = load_i32(signature, 4)
    n_mro_i32: int = load_i32(cls, PYCLASSOBJECT_N_MRO_OFFSET)
    mro = load_ptr(cls, PYCLASSOBJECT_MRO_OFFSET)
    i: int = 0
    while i < n_mro_i32:
        m = pcc_gc_load_ptr(cls, ptr_add(mro, i * 8))
        if ptr_is_null(m) == 0:
            n_methods_i32: int = load_i32(m, PYCLASSOBJECT_N_METHODS_OFFSET)
            methods = load_ptr(m, PYCLASSOBJECT_METHODS_OFFSET)
            j: int = 0
            while j < n_methods_i32:
                m_off: int = j * PYCLASSMETHOD_SIZE
                if load_i32(methods, m_off + PYCLASSMETHOD_NAME_LENGTH_OFFSET) == name_length:
                    stored_hash: int = load_i32(
                        methods, m_off + PYCLASSMETHOD_NAME_HASH_OFFSET
                    ) & 0xFFFFFFFF
                    if stored_hash == name_hash:
                        m_name = load_ptr(methods, m_off + PYCLASSMETHOD_NAME_OFFSET)
                        if _strs_eq(m_name, name) != 0:
                            method_slot = ptr_add(
                                methods, m_off + PYCLASSMETHOD_FUNC_OFFSET
                            )
                            func = pcc_gc_note_relocation_read(load_ptr(method_slot, 0))
                            store_ptr(method_slot, 0, func)
                            return func
                j = j + 1
        i = i + 1
    return null()


def _lookup_field_index(cls, name):
    if not _ptr_is_class(cls):
        return -1
    if ptr_is_null(name) != 0:
        return -1
    n_fields_i32: int = load_i32(cls, PYCLASSOBJECT_N_FIELDS_OFFSET)
    field_names = load_ptr(cls, PYCLASSOBJECT_FIELD_NAMES_OFFSET)
    if ptr_is_null(field_names) != 0:
        return -1
    i: int = 0
    while i < n_fields_i32:
        fn = load_ptr(field_names, i * C_POINTER_SIZE)
        # print("  field[" + str(i) + "]=" + str(fn))
        if _strs_eq(fn, name) != 0:
            return i
        i = i + 1
    return -1


def _class_attr_cache_epoch() -> int:
    return atomic_load_i32(
        global_addr("py_class_attr_cache_epoch"), 0, "acquire"
    )


def _bump_class_attr_cache_epoch() -> None:
    atomic_rmw_i32(
        "add", global_addr("py_class_attr_cache_epoch"), 0, 1, "release"
    )


@c_abi_export("py_class_attrs_dict")
def py_class_attrs_dict(cls, create: int):
    if not _ptr_is_class(cls):
        return null()
    cls = pcc_gc_note_relocation_read(cls)
    attrs = pcc_gc_load_ptr(cls, ptr_add(cls, PYCLASSOBJECT_ATTRS_OFFSET))
    if ptr_is_null(attrs) != 0 and create != 0:
        created = py_dict_new()
        if ptr_is_null(created) != 0:
            return null()
        pcc_gc_store_ptr(cls, ptr_add(cls, PYCLASSOBJECT_ATTRS_OFFSET), created)
        py_decref(created)
        attrs = pcc_gc_load_ptr(cls, ptr_add(cls, PYCLASSOBJECT_ATTRS_OFFSET))
        # Exposing this mutable dict can later install a descriptor without
        # going through py_class_setattr_raw. Retire field-lookup proofs when
        # the dict first becomes reachable, before a caller can mutate it.
        _bump_class_attr_cache_epoch()
    return attrs


@c_abi_export("py_classmethod_new")
def py_classmethod_new(func):
    if ptr_is_null(func) != 0:
        return null()
    descriptor = pcc_gc_alloc(PYCLASSMETHODOBJECT_SIZE, PY_TYPE_CLASSMETHOD, 0)
    if ptr_is_null(descriptor) != 0:
        return null()
    store_ptr(descriptor, PYCLASSMETHODOBJECT_FUNC_OFFSET, null())
    pcc_gc_store_ptr(
        descriptor,
        ptr_add(descriptor, PYCLASSMETHODOBJECT_FUNC_OFFSET),
        func,
    )
    py_gc_track(descriptor)
    pcc_gc_publish_initialized(descriptor)
    return descriptor


@c_abi_export("py_staticmethod_new")
def py_staticmethod_new(func):
    if ptr_is_null(func) != 0:
        return null()
    descriptor = pcc_gc_alloc(PYSTATICMETHODOBJECT_SIZE, PY_TYPE_STATICMETHOD, 0)
    if ptr_is_null(descriptor) != 0:
        return null()
    store_ptr(descriptor, PYSTATICMETHODOBJECT_FUNC_OFFSET, null())
    pcc_gc_store_ptr(
        descriptor, ptr_add(descriptor, PYSTATICMETHODOBJECT_FUNC_OFFSET), func
    )
    py_gc_track(descriptor)
    pcc_gc_publish_initialized(descriptor)
    return descriptor


@c_abi_export("py_property_new")
def py_property_new(fget, fset, fdel):
    descriptor = pcc_gc_alloc(PYPROPERTYOBJECT_SIZE, PY_TYPE_PROPERTY, 0)
    if ptr_is_null(descriptor) != 0:
        return null()
    store_ptr(descriptor, PYPROPERTYOBJECT_FGET_OFFSET, null())
    store_ptr(descriptor, PYPROPERTYOBJECT_FSET_OFFSET, null())
    store_ptr(descriptor, PYPROPERTYOBJECT_FDEL_OFFSET, null())
    none_obj = global_load_ptr("py_None")
    if ptr_is_null(fget) == 0 and ptr_eq(fget, none_obj) == 0:
        pcc_gc_store_ptr(
            descriptor, ptr_add(descriptor, PYPROPERTYOBJECT_FGET_OFFSET), fget
        )
    if ptr_is_null(fset) == 0 and ptr_eq(fset, none_obj) == 0:
        pcc_gc_store_ptr(
            descriptor, ptr_add(descriptor, PYPROPERTYOBJECT_FSET_OFFSET), fset
        )
    if ptr_is_null(fdel) == 0 and ptr_eq(fdel, none_obj) == 0:
        pcc_gc_store_ptr(
            descriptor, ptr_add(descriptor, PYPROPERTYOBJECT_FDEL_OFFSET), fdel
        )
    py_gc_track(descriptor)
    pcc_gc_publish_initialized(descriptor)
    return descriptor


def _func_signature_valid(signature) -> bool:
    if not _ptr_can_have_header(signature):
        return False
    if load_i32(signature, PYOBJECTHEADER_TYPE_TAG_OFFSET) != PY_TYPE_TUPLE:
        return False
    if py_tuple_len(signature) < 5:
        return False
    magic = py_tuple_get(signature, 0)
    if ptr_is_null(magic) != 0:
        return False
    expected = py_str_new(cstr("__pcc_func_signature_v1__"), 25)
    ok: int = 0
    if ptr_is_null(expected) == 0:
        ok = py_str_eq(magic, expected)
        py_decref(expected)
    py_decref(magic)
    return ok != 0


def _func_signature(func):
    if not _ptr_can_have_header(func):
        return null()
    if load_i32(func, PYOBJECTHEADER_TYPE_TAG_OFFSET) != PY_TYPE_FUNC:
        return null()
    captures = pcc_gc_load_ptr(func, ptr_add(func, 64))
    if not _ptr_can_have_header(captures):
        return null()
    if load_i32(captures, PYOBJECTHEADER_TYPE_TAG_OFFSET) != PY_TYPE_TUPLE or py_tuple_len(captures) != 2:
        return null()
    candidate = py_tuple_get(captures, 1)
    if not _func_signature_valid(candidate):
        if ptr_is_null(candidate) == 0:
            py_decref(candidate)
        return null()
    return candidate


def _bound_signature(func):
    signature = _func_signature(func)
    if ptr_is_null(signature) != 0:
        return null()
    names = py_tuple_get(signature, 1)
    kinds = py_tuple_get(signature, 2)
    has_defaults = py_tuple_get(signature, 3)
    defaults = py_tuple_get(signature, 4)
    if (
        ptr_is_null(names) != 0
        or ptr_is_null(kinds) != 0
        or ptr_is_null(has_defaults) != 0
        or ptr_is_null(defaults) != 0
    ):
        if ptr_is_null(names) == 0:
            py_decref(names)
        if ptr_is_null(kinds) == 0:
            py_decref(kinds)
        if ptr_is_null(has_defaults) == 0:
            py_decref(has_defaults)
        if ptr_is_null(defaults) == 0:
            py_decref(defaults)
        py_decref(signature)
        return null()
    n: int = py_tuple_len(names)
    if (
        n <= 0
        or py_tuple_len(kinds) != n
        or py_tuple_len(has_defaults) != n
        or py_tuple_len(defaults) != n
    ):
        py_decref(names)
        py_decref(kinds)
        py_decref(has_defaults)
        py_decref(defaults)
        py_decref(signature)
        return null()
    out_names = py_tuple_new(n - 1)
    out_kinds = py_tuple_new(n - 1)
    out_has_defaults = py_tuple_new(n - 1)
    out_defaults = py_tuple_new(n - 1)
    out_signature = py_tuple_new(5)
    if (
        ptr_is_null(out_names) != 0
        or ptr_is_null(out_kinds) != 0
        or ptr_is_null(out_has_defaults) != 0
        or ptr_is_null(out_defaults) != 0
        or ptr_is_null(out_signature) != 0
    ):
        if ptr_is_null(out_names) == 0:
            py_decref(out_names)
        if ptr_is_null(out_kinds) == 0:
            py_decref(out_kinds)
        if ptr_is_null(out_has_defaults) == 0:
            py_decref(out_has_defaults)
        if ptr_is_null(out_defaults) == 0:
            py_decref(out_defaults)
        if ptr_is_null(out_signature) == 0:
            py_decref(out_signature)
        py_decref(names)
        py_decref(kinds)
        py_decref(has_defaults)
        py_decref(defaults)
        py_decref(signature)
        return null()
    i: int = 1
    valid: int = 1
    while i < n:
        name = py_tuple_get(names, i)
        kind = py_tuple_get(kinds, i)
        has_default = py_tuple_get(has_defaults, i)
        default_obj = py_tuple_get(defaults, i)
        if (
            ptr_is_null(name) != 0
            or ptr_is_null(kind) != 0
            or ptr_is_null(has_default) != 0
            or ptr_is_null(default_obj) != 0
        ):
            valid = 0
        if valid != 0:
            py_tuple_set_item(out_names, i - 1, name)
            py_tuple_set_item(out_kinds, i - 1, kind)
            py_tuple_set_item(out_has_defaults, i - 1, has_default)
            py_tuple_set_item(out_defaults, i - 1, default_obj)
        if ptr_is_null(name) == 0:
            py_decref(name)
        if ptr_is_null(kind) == 0:
            py_decref(kind)
        if ptr_is_null(has_default) == 0:
            py_decref(has_default)
        if ptr_is_null(default_obj) == 0:
            py_decref(default_obj)
        if valid == 0:
            i = n
        i = i + 1
    magic = null()
    if valid != 0:
        magic = py_tuple_get(signature, 0)
        if ptr_is_null(magic) != 0:
            valid = 0
    if valid != 0:
        py_tuple_set_item(out_signature, 0, magic)
        py_tuple_set_item(out_signature, 1, out_names)
        py_tuple_set_item(out_signature, 2, out_kinds)
        py_tuple_set_item(out_signature, 3, out_has_defaults)
        py_tuple_set_item(out_signature, 4, out_defaults)
    if ptr_is_null(magic) == 0:
        py_decref(magic)
    py_decref(out_names)
    py_decref(out_kinds)
    py_decref(out_has_defaults)
    py_decref(out_defaults)
    py_decref(names)
    py_decref(kinds)
    py_decref(has_defaults)
    py_decref(defaults)
    py_decref(signature)
    if valid == 0:
        py_decref(out_signature)
        return null()
    return out_signature


def _wrap_bound_captures(method, captures):
    signature = _bound_signature(method)
    if ptr_is_null(signature) != 0:
        return captures
    wrapped = py_tuple_new(2)
    if ptr_is_null(wrapped) != 0:
        py_decref(signature)
        return captures
    py_tuple_set_item(wrapped, 0, captures)
    py_tuple_set_item(wrapped, 1, signature)
    py_decref(signature)
    return wrapped


def _call_pyfunc_bound_args(func, bound_args):
    # The outer bound PyFunc already normalized varargs/keyword-only/defaults.
    # Forward its context while consuming those slots without rebinding them.
    return py_func_call_bound_forward(func, bound_args)


def _bound_clear_root(slots, pins, offset: int) -> None:
    value = load_ptr(slots, offset)
    if ptr_is_null(value) == 0 and is_tagged_int(value) == 0:
        # Only the function slot can contain an unmanaged native entry. Its
        # ownership remains in the tuple; it carries no pin lease (pin=-1).
        prior: int = load_i64(pins, offset)
        if prior >= 0:
            pcc_gc_unpin(value)
            if prior != 0:
                atomic_rmw_i32("or", value, 12, 64, "relaxed")
    pcc_gc_store_root(ptr_add(slots, offset), null())


def _bound_entry_build(slots, pins) -> None:
    # output0, captures8, args16, function24, self32, full48,
    # current arg56, arg1 64, arg2 72, saved error80.
    func = py_tuple_get(load_ptr(slots, 8), 0)
    store_ptr(slots, 24, func)
    store_i64(pins, 24, -1)
    if ptr_is_null(func):
        return
    # The slot is already registered. Query can park, so reload after it.
    managed: int = _ptr_can_have_header(func)
    func = load_ptr(slots, 24)
    if managed and is_tagged_int(func) == 0:
        store_i64(pins, 24, load_i32(func, 12) & 64)
        pcc_gc_pin(func)
        pcc_gc_note_write_barrier(null(), func)
    self_obj = py_tuple_get(load_ptr(slots, 8), 1)
    store_ptr(slots, 32, self_obj)
    if ptr_is_null(self_obj) == 0 and is_tagged_int(self_obj) == 0:
        store_i64(pins, 32, load_i32(self_obj, 12) & 64)
        pcc_gc_pin(self_obj)
        pcc_gc_note_write_barrier(null(), self_obj)
    if ptr_is_null(self_obj):
        return
    n_args: int = py_tuple_len(load_ptr(slots, 16))
    func = load_ptr(slots, 24)
    if managed and load_i32(func, PYOBJECTHEADER_TYPE_TAG_OFFSET) == PY_TYPE_FUNC:
        full = py_tuple_new(n_args + 1)
        store_ptr(slots, 48, full)
        if ptr_is_null(full) == 0 and is_tagged_int(full) == 0:
            store_i64(pins, 48, load_i32(full, 12) & 64)
            pcc_gc_pin(full)
            pcc_gc_note_write_barrier(null(), full)
        if ptr_is_null(full):
            return
        py_tuple_set_item(load_ptr(slots, 48), 0, load_ptr(slots, 32))
        i: int = 0
        while i < n_args:
            arg = py_tuple_get(load_ptr(slots, 16), i)
            store_ptr(slots, 56, arg)
            if ptr_is_null(arg) == 0 and is_tagged_int(arg) == 0:
                store_i64(pins, 56, load_i32(arg, 12) & 64)
                pcc_gc_pin(arg)
                pcc_gc_note_write_barrier(null(), arg)
            if ptr_is_null(arg):
                return
            py_tuple_set_item(load_ptr(slots, 48), i + 1, load_ptr(slots, 56))
            _bound_clear_root(slots, pins, 56)
            i = i + 1
        result = _call_pyfunc_bound_args(load_ptr(slots, 24), load_ptr(slots, 48))
        store_ptr(slots, 0, result)
        if ptr_is_null(result) == 0 and is_tagged_int(result) == 0:
            store_i64(pins, 0, load_i32(result, 12) & 64)
            pcc_gc_pin(result)
            pcc_gc_note_write_barrier(null(), result)
    elif n_args >= 0 and n_args <= 3:
        i: int = 0
        while i < n_args:
            arg = py_tuple_get(load_ptr(slots, 16), i)
            store_ptr(slots, 56 + i * 8, arg)
            if ptr_is_null(arg) == 0 and is_tagged_int(arg) == 0:
                store_i64(pins, 56 + i * 8, load_i32(arg, 12) & 64)
                pcc_gc_pin(arg)
                pcc_gc_note_write_barrier(null(), arg)
            if ptr_is_null(arg):
                return
            i = i + 1
        if n_args == 0:
            result = call_ptr1(load_ptr(slots, 24), load_ptr(slots, 32))
        elif n_args == 1:
            result = call_ptr2(load_ptr(slots, 24), load_ptr(slots, 32), load_ptr(slots, 56))
        elif n_args == 2:
            result = call_ptr3(load_ptr(slots, 24), load_ptr(slots, 32), load_ptr(slots, 56), load_ptr(slots, 64))
        else:
            result = call_ptr4(load_ptr(slots, 24), load_ptr(slots, 32), load_ptr(slots, 56), load_ptr(slots, 64), load_ptr(slots, 72))
        store_ptr(slots, 0, result)
        if ptr_is_null(result) == 0 and is_tagged_int(result) == 0:
            store_i64(pins, 0, load_i32(result, 12) & 64)
            pcc_gc_pin(result)
            pcc_gc_note_write_barrier(null(), result)


@c_abi_export("pcc_instance_bound_method_entry")
def _instance_bound_method_entry(captures: c_ptr, args: c_ptr) -> c_ptr:
    slots = stack_alloc(88)
    pins = stack_alloc(88)
    memset(slots, 0, 88)
    memset(pins, 0, 88)
    pcc_gc_frame_enter(global_addr("pcc_bound_callback_frame_map"), slots)
    if ptr_is_null(captures) == 0 and is_tagged_int(captures) == 0:
        store_i64(pins, 8, load_i32(captures, 12) & 64)
        pcc_gc_pin(captures)
    pcc_gc_store_root(ptr_add(slots, 8), captures)
    if ptr_is_null(args) == 0 and is_tagged_int(args) == 0:
        store_i64(pins, 16, load_i32(args, 12) & 64)
        pcc_gc_pin(args)
    pcc_gc_store_root(ptr_add(slots, 16), args)
    _bound_entry_build(slots, pins)
    if ptr_is_null(load_ptr(slots, 0)):
        _class_require_result(null(), cstr("class callback"), cstr("class callback returned NULL without setting an exception"))
        error = py_current_exception()
        if ptr_is_null(error) == 0:
            store_i64(pins, 80, load_i32(error, 12) & 64)
            pcc_gc_pin(error)
            py_incref(error)
            store_ptr(slots, 80, error)
            pcc_gc_note_write_barrier(null(), error)
    prior_result_pin: int = load_i64(pins, 0)
    index: int = 9
    while index > 0:
        if ptr_eq(load_ptr(slots, 0), load_ptr(slots, index * 8)):
            prior_result_pin = load_i64(pins, index * 8)
        index = index - 1
    index = 9
    while index > 0:
        _bound_clear_root(slots, pins, index * 8)
        index = index - 1
    error = load_ptr(slots, 80)
    if ptr_is_null(error) == 0:
        py_raise(error)
        _bound_clear_root(slots, pins, 80)
    result = load_ptr(slots, 0)
    if ptr_is_null(result) == 0 and is_tagged_int(result) == 0:
        atomic_rmw_i32("or", result, 12, 64, "relaxed")
    pcc_gc_frame_leave(slots)
    return pcc_gc_take_pinned_slot(slots, prior_result_pin)


@c_abi_export("py_instance_bind_method")
def py_instance_bind_method(method, self_obj, name):
    if ptr_is_null(method) != 0 or ptr_is_null(self_obj) != 0:
        return null()
    captures = py_tuple_new(2)
    if ptr_is_null(captures) != 0:
        return null()
    py_tuple_set_item(captures, 0, method)
    py_tuple_set_item(captures, 1, self_obj)
    bound_name = name
    if _ptr_can_have_header(method) and load_i32(method, PYOBJECTHEADER_TYPE_TAG_OFFSET) == PY_TYPE_FUNC:
        method_name = load_ptr(method, 72)
        if ptr_is_null(method_name) == 0:
            bound_name = method_name
    bound_captures = _wrap_bound_captures(method, captures)
    bound = py_func_new_bound(
        function_addr("pcc_instance_bound_method_entry"),
        bound_captures,
        bound_name,
        self_obj,
    )
    if ptr_is_null(bound) == 0:
        atomic_rmw_i32("or", bound, 12, PY_FLAG_FUNC_TRANSPARENT_CALL, "relaxed")
    if ptr_eq(bound_captures, captures) == 0:
        py_decref(bound_captures)
    py_decref(captures)
    return bound


def _bound_method_function_slot_locked(bound):
    # The caller holds the graph transaction and an address lease on bound.
    # Only this exact wrapper owns the (method, self) capture convention.
    if not _ptr_can_have_header(bound):
        return null()
    if load_i32(bound, PYOBJECTHEADER_TYPE_TAG_OFFSET) != PY_TYPE_FUNC:
        return null()
    if ptr_eq(load_ptr(bound, 56), function_addr("pcc_instance_bound_method_entry")) == 0:
        return null()
    captures = pcc_gc_resolve_root_slot_unlocked(ptr_add(bound, 64), 0)
    if not _ptr_can_have_header(captures):
        return null()
    if load_i32(captures, PYOBJECTHEADER_TYPE_TAG_OFFSET) != PY_TYPE_TUPLE:
        return null()
    if py_tuple_len(captures) != 2:
        return null()
    source = ptr_add(captures, PYTUPLEOBJECT_ITEMS_OFFSET)
    first = pcc_gc_resolve_root_slot_unlocked(source, 0)
    if not _ptr_can_have_header(first):
        # A native method's first capture may be an untraced code address.
        return null()
    if load_i32(first, PYOBJECTHEADER_TYPE_TAG_OFFSET) == PY_TYPE_TUPLE:
        # _wrap_bound_captures stores (original captures, bound signature).
        if py_tuple_len(first) != 2:
            return null()
        source = ptr_add(first, PYTUPLEOBJECT_ITEMS_OFFSET)
        first = pcc_gc_resolve_root_slot_unlocked(source, 0)
        if not _ptr_can_have_header(first):
            return null()
    if load_i32(first, PYOBJECTHEADER_TYPE_TAG_OFFSET) != PY_TYPE_FUNC:
        return null()
    return source


@c_abi_export("py_bound_method_function")
def py_bound_method_function(bound):
    """NEW live original function, or NULL for non-wrapper/native entries.

    Diagnostic callers already hold the bound object stable. Inspect existing
    owning capture slots under the graph lock; only after managed FUNC proof
    acquire the result reference and pin. No raw code pointer becomes a root.
    """
    result_slot = stack_alloc(C_POINTER_SIZE)
    store_ptr(result_slot, 0, null())
    prior_pin: int = 0
    pcc_py_gc_minor_graph_lock()
    source = _bound_method_function_slot_locked(bound)
    if ptr_is_null(source) == 0:
        original = load_ptr(source, 0)
        prior_pin = load_i32(original, PYOBJECTHEADER_FLAGS_OFFSET) & 64
        py_incref(original)
        pcc_gc_pin(original)
        store_ptr(result_slot, 0, original)
    pcc_py_gc_minor_graph_unlock()
    return pcc_gc_take_pinned_slot(result_slot, prior_pin)


# Instance attribute resolution cache, one table per thread.  Resolving
# `inst.name` walked the MRO for every access: every method table for
# `__getattribute__`, then every class-attribute dict with a freshly allocated
# str key, before a field or method was found -- 56% of a native Stage2
# frontend worker.  An entry records one proven outcome for (class, name).
# The shared class epoch retires every entry on class mutation, relocation or
# deallocation, and the name is copied into the entry so a hit never reads a
# caller buffer or a freed class-owned spelling.
#   +0 class  +8 epoch  +12 kind  +16 payload  +24 name bytes (NUL-terminated)
define_thread_local_ptr_null("py_attr_resolution_table")
_ATTR_RES_ENTRIES = 16384
_ATTR_RES_ENTRY_SIZE = 64
_ATTR_RES_NAME_MAX = 39
_ATTR_RES_FIELD = 1
_ATTR_RES_METHOD = 2
# The class resolves `name` to nothing: no class attribute, field or method in
# the MRO, no `__getattr__`, and not a dict subclass.  Only the instance's own
# dict can still answer.  Generic AST walks probe every node for dozens of
# field names it lacks -- 28% of the lookups in a native Stage2 frontend
# worker -- and each miss walked the MRO twice with a fresh str key.
_ATTR_RES_ABSENT = 4
# A data descriptor (a property, or an object with `__set__`/`__delete__`)
# answers for (class, name); payload is the borrowed descriptor, held by the
# class dict, so it is cached only where methods are (non-moving collectors).
_ATTR_RES_DATA_DESCRIPTOR = 5


def _attr_res_slot(table, cls, name):
    slot: int = (
        (ptr_to_int(cls) >> 4) ^ (ptr_to_int(name) >> 3)
    ) & (_ATTR_RES_ENTRIES - 1)
    return ptr_add(table, slot * _ATTR_RES_ENTRY_SIZE)


def _attr_res_find(cls, name, kind: int):
    entry = _attr_res_find_any(cls, name)
    if ptr_is_null(entry) != 0:
        return null()
    if load_i32(entry, 12) != kind:
        return null()
    return entry


def _attr_res_find_any(cls, name):
    """The live entry for (cls, name) whatever its kind: every kind of one
    (class, name) lands in the same slot, so a caller that accepts several
    kinds compares the name once."""
    table = global_load_ptr("py_attr_resolution_table")
    if ptr_is_null(table) != 0:
        return null()
    entry = _attr_res_slot(table, cls, name)
    if ptr_eq(load_ptr(entry, 0), cls) == 0:
        return null()
    if load_i32(entry, 12) == 0:
        return null()
    if load_i32(entry, 8) != _class_attr_cache_epoch():
        return null()
    i: int = 0
    while i <= _ATTR_RES_NAME_MAX:
        a: int = load_i8(entry, 24 + i) & 0xFF
        b: int = load_i8(name, i) & 0xFF
        if a != b:
            return null()
        if a == 0:
            return entry
        i = i + 1
    return null()


# `__getattribute__` absence, keyed by the class alone.  Every instance
# attribute read asks this first; answering it from the name-keyed table cost
# a 17-byte name compare per access.  Entry: +0 class  +8 epoch.
define_thread_local_ptr_null("py_attr_no_getattribute_table")
_NO_GETATTRIBUTE_ENTRIES = 1024


def _no_getattribute_known(cls) -> int:
    table = global_load_ptr("py_attr_no_getattribute_table")
    if ptr_is_null(table) != 0:
        return 0
    entry = ptr_add(
        table, ((ptr_to_int(cls) >> 4) & (_NO_GETATTRIBUTE_ENTRIES - 1)) * 16
    )
    if ptr_eq(load_ptr(entry, 0), cls) == 0:
        return 0
    if load_i32(entry, 8) != _class_attr_cache_epoch():
        return 0
    return 1


def _no_getattribute_store(cls, epoch: int) -> None:
    table = global_load_ptr("py_attr_no_getattribute_table")
    if ptr_is_null(table) != 0:
        table = malloc(_NO_GETATTRIBUTE_ENTRIES * 16)
        if ptr_is_null(table) != 0:
            return
        memset(table, 0, _NO_GETATTRIBUTE_ENTRIES * 16)
        global_store_ptr("py_attr_no_getattribute_table", table)
    entry = ptr_add(
        table, ((ptr_to_int(cls) >> 4) & (_NO_GETATTRIBUTE_ENTRIES - 1)) * 16
    )
    store_ptr(entry, 0, null())
    store_i32(entry, 8, epoch)
    store_ptr(entry, 0, cls)


def _attr_res_store(cls, name, kind: int, payload: int, epoch: int) -> None:
    # `epoch` is sampled before the lookup that proved the outcome, so a
    # concurrent class mutation during that lookup leaves the entry stale.
    length: int = strlen(name)
    if length > _ATTR_RES_NAME_MAX:
        return
    table = global_load_ptr("py_attr_resolution_table")
    if ptr_is_null(table) != 0:
        table = malloc(_ATTR_RES_ENTRIES * _ATTR_RES_ENTRY_SIZE)
        if ptr_is_null(table) != 0:
            return
        memset(table, 0, _ATTR_RES_ENTRIES * _ATTR_RES_ENTRY_SIZE)
        global_store_ptr("py_attr_resolution_table", table)
    entry = _attr_res_slot(table, cls, name)
    store_i32(entry, 12, 0)
    store_ptr(entry, 0, cls)
    store_i32(entry, 8, epoch)
    store_i64(entry, 16, payload)
    i: int = 0
    while i <= length:
        store_i8(entry, 24 + i, load_i8(name, i))
        i = i + 1
    store_i32(entry, 12, kind)


def _attr_res_method_cacheable() -> int:
    # Methods are cached by address; the forwarding collectors can move them.
    backend: int = pcc_gc_backend()
    if backend == 3 or backend == 4:
        return 0
    return 1


def _instance_reserved_owner_slot(inst, cls):
    # Every native instance physically reserves this owning slot, including
    # slots-only dict/exception subclasses. Public __dict__ visibility is a
    # separate policy; GC, teardown and physical copies must not use it.
    n_fields: int = load_i32(cls, PYCLASSOBJECT_N_FIELDS_OFFSET)
    if n_fields < 0:
        n_fields = 0
    return ptr_add(
        inst, PYINSTANCEOBJECT_FIELDS_OFFSET + n_fields * C_POINTER_SIZE
    )


def _instance_storage_slot_count(cls) -> int:
    # Declared fields, the established dynamic/builtin backing owner, then
    # any appended builtin payload owners. Existing field offsets never move.
    minimum: int = load_i32(cls, PYCLASSOBJECT_N_FIELDS_OFFSET) + 1
    if minimum < 1:
        minimum = 1
    count: int = (load_i32(cls, PYCLASSOBJECT_INSTANCE_SIZE_OFFSET) - PYINSTANCEOBJECT_FIELDS_OFFSET) // C_POINTER_SIZE
    if count < minimum:
        count = minimum
    return count


@c_abi_export("py_class_is_str_subclass")
def py_class_is_str_subclass(cls) -> int:
    if not _ptr_is_class(cls):
        return 0
    n_mro: int = load_i32(cls, PYCLASSOBJECT_N_MRO_OFFSET)
    mro = load_ptr(cls, PYCLASSOBJECT_MRO_OFFSET)
    index: int = 0
    while index < n_mro:
        owner = pcc_gc_load_ptr(cls, ptr_add(mro, index * C_POINTER_SIZE))
        if py_builtin_type_class_tag(owner) == PY_TYPE_STR:
            return 1
        index = index + 1
    return 0


def _instance_builtin_payload_slot(inst, cls):
    # The immutable str payload follows the pre-existing reserved owner.
    count: int = load_i32(cls, PYCLASSOBJECT_N_FIELDS_OFFSET) + 1
    if count < 1:
        count = 1
    if _instance_storage_slot_count(cls) <= count:
        return null()
    return ptr_add(inst, PYINSTANCEOBJECT_FIELDS_OFFSET + count * C_POINTER_SIZE)


@c_abi_export("py_str_payload")
def py_str_payload(value):
    # Borrowed native string storage. The receiver keeps its own class,
    # identity, fields, dictionary and finalizer throughout this view.
    if ptr_is_null(value) != 0 or is_tagged_int(value) != 0:
        return null()
    value = pcc_gc_note_relocation_read(value)
    if load_i32(value, PYOBJECTHEADER_TYPE_TAG_OFFSET) == PY_TYPE_STR:
        return value
    if not _ptr_is_instance(value):
        return null()
    cls = pcc_gc_load_ptr(value, ptr_add(value, PYINSTANCEOBJECT_CLS_OFFSET))
    # Only str currently declares an appended immutable owner. Read its
    # scalar layout immediately; do not hold a borrowed class pointer over
    # another allocating lookup. Return is an immediate borrowed check only.
    count: int = load_i32(cls, PYCLASSOBJECT_N_FIELDS_OFFSET) + 1
    if count < 1:
        count = 1
    offset: int = PYINSTANCEOBJECT_FIELDS_OFFSET + count * C_POINTER_SIZE
    if load_i32(cls, PYCLASSOBJECT_INSTANCE_SIZE_OFFSET) <= offset:
        return null()
    payload = pcc_gc_load_ptr(value, ptr_add(value, offset))
    if ptr_is_null(payload) != 0 or is_tagged_int(payload) != 0:
        return null()
    if load_i32(payload, PYOBJECTHEADER_TYPE_TAG_OFFSET) != PY_TYPE_STR:
        return null()
    return payload


@c_abi_export("py_instance_copy_builtin_payload")
def py_instance_copy_builtin_payload(source, destination) -> None:
    if not _ptr_is_instance(source) or not _ptr_is_instance(destination):
        return
    pcc_py_gc_minor_graph_lock()
    source = pcc_gc_note_relocation_read(source)
    destination = pcc_gc_note_relocation_read(destination)
    cls = pcc_gc_load_ptr(source, ptr_add(source, PYINSTANCEOBJECT_CLS_OFFSET))
    source_slot = _instance_builtin_payload_slot(source, cls)
    if ptr_is_null(source_slot) == 0:
        value = pcc_gc_load_ptr(source, source_slot)
        pcc_gc_store_ptr(destination, _instance_builtin_payload_slot(destination, cls), value)
    pcc_py_gc_minor_graph_unlock()


def _dynamic_attr_slot(inst):
    if not _ptr_is_instance(inst):
        return null()
    cls = pcc_gc_load_ptr(inst, ptr_add(inst, PYINSTANCEOBJECT_CLS_OFFSET))
    flags: int = load_i32(cls, PYOBJECTHEADER_FLAGS_OFFSET)
    if (flags & 2) != 0:
        return null()
    return _instance_reserved_owner_slot(inst, cls)


def _copy_instance_reserved_owner(obj, dst, cls) -> None:
    # The surrounding legacy replace entry still owns its raw object-entry
    # contract. Within this physical copy, heal the owning source and publish
    # the additional destination owner in one graph transaction.
    pcc_py_gc_minor_graph_lock()
    source = _instance_reserved_owner_slot(obj, cls)
    destination = _instance_reserved_owner_slot(dst, cls)
    value = pcc_gc_resolve_root_slot_unlocked(source, 0)
    if ptr_is_null(value) == 0:
        py_incref(value)
        store_ptr(destination, 0, value)
        pcc_gc_note_slot_write_barrier(dst, destination, value)
    pcc_py_gc_minor_graph_unlock()


def _instance_dict_of(inst, cls):
    """The instance dict of a validated instance of `cls` (borrowed), or NULL:
    `_dynamic_attr_slot` without validating `inst` a second time."""
    if (load_i32(cls, PYOBJECTHEADER_FLAGS_OFFSET) & 2) != 0:
        return null()
    n_fields: int = load_i32(cls, PYCLASSOBJECT_N_FIELDS_OFFSET)
    if n_fields < 0:
        n_fields = 0
    return pcc_gc_load_ptr(
        inst,
        ptr_add(inst, PYINSTANCEOBJECT_FIELDS_OFFSET + n_fields * C_POINTER_SIZE),
    )


def _instance_dict_attr(inst, cls, name):
    """`name` from the instance's own dict (a new reference), or NULL, for an
    ABSENT outcome: the class already answered nothing."""
    dyn = _instance_dict_of(inst, cls)
    if ptr_is_null(dyn) != 0:
        return null()
    key = py_str_new(name, strlen(name))
    if ptr_is_null(key) != 0:
        return null()
    got = py_dict_get(dyn, key)
    py_decref(key)
    return got


@c_abi_export("py_instance_vars")
def py_instance_vars(inst):
    if not _ptr_is_instance(inst):
        py_raise_owned(py_exc_new(3, cstr("vars() argument has no __dict__")))
        return null()
    cls = pcc_gc_load_ptr(inst, ptr_add(inst, PYINSTANCEOBJECT_CLS_OFFSET))
    if not _ptr_is_class(cls):
        py_raise_owned(py_exc_new(3, cstr("vars() argument has no __dict__")))
        return null()
    out = py_dict_new()
    if ptr_is_null(out) != 0:
        return null()
    n_fields: int = load_i32(cls, PYCLASSOBJECT_N_FIELDS_OFFSET)
    if n_fields < 0:
        n_fields = 0
    field_names = load_ptr(cls, PYCLASSOBJECT_FIELD_NAMES_OFFSET)
    fields = ptr_add(inst, PYINSTANCEOBJECT_FIELDS_OFFSET)
    i: int = 0
    while i < n_fields:
        field_name = null()
        if ptr_is_null(field_names) == 0:
            field_name = load_ptr(field_names, i * C_POINTER_SIZE)
        if ptr_is_null(field_name) == 0:
            value = pcc_gc_load_ptr(
                inst, ptr_add(fields, i * C_POINTER_SIZE)
            )
            if ptr_is_null(value) == 0:
                key = py_str_new(field_name, strlen(field_name))
                if ptr_is_null(key) != 0:
                    py_decref(out)
                    return null()
                py_dict_set(out, key, value)
                py_decref(key)
        i = i + 1
    dyn_slot = _dynamic_attr_slot(inst)
    if ptr_is_null(dyn_slot) == 0:
        dyn = pcc_gc_load_ptr(inst, dyn_slot)
        if ptr_is_null(dyn) == 0:
            py_dict_update(out, dyn)
    return out


@c_abi_export("py_obj_vars")
def py_obj_vars(o):
    if ptr_is_null(o) != 0:
        py_raise_owned(py_exc_new(3, cstr("vars() argument has no __dict__")))
        return null()
    if is_tagged_int(o) != 0:
        py_raise_owned(py_exc_new(3, cstr("vars() argument has no __dict__")))
        return null()
    if _ptr_is_instance(o):
        return py_instance_vars(o)
    if _ptr_is_class(o):
        attrs = py_class_attrs_dict(o, 1)
        if ptr_is_null(attrs) == 0:
            py_incref(attrs)
            return attrs
    py_raise_owned(py_exc_new(3, cstr("vars() argument has no __dict__")))
    return null()


def _class_attr_lookup_in_mro(cls, name):
    if not _ptr_is_class(cls):
        return null()
    if ptr_is_null(name) != 0:
        return null()
    cls = pcc_gc_note_relocation_read(cls)
    key = py_str_new(name, strlen(name))
    if ptr_is_null(key) != 0:
        return null()
    n_mro: int = load_i32(cls, PYCLASSOBJECT_N_MRO_OFFSET)
    mro = load_ptr(cls, PYCLASSOBJECT_MRO_OFFSET)
    i: int = 0
    while i < n_mro:
        m = pcc_gc_load_ptr(cls, ptr_add(mro, i * 8))
        if ptr_is_null(m) == 0:
            attrs = py_class_attrs_dict(m, 0)
            if ptr_is_null(attrs) == 0:
                value = py_dict_get(attrs, key)
                if ptr_is_null(value) == 0:
                    py_decref(key)
                    return value
        i = i + 1
    py_decref(key)
    return null()


def _descriptor_method(descriptor, name):
    if ptr_is_null(descriptor) != 0:
        return null()
    if is_tagged_int(descriptor) != 0:
        return null()
    if not _ptr_is_instance(descriptor):
        return null()
    desc_cls = pcc_gc_load_ptr(
        descriptor,
        ptr_add(descriptor, PYINSTANCEOBJECT_CLS_OFFSET),
    )
    return _class_lookup_in_mro(desc_cls, name)


def _class_attr_is_property(descriptor) -> bool:
    if ptr_is_null(descriptor) == 0:
        if is_tagged_int(descriptor) == 0:
            if load_i32(descriptor, PYOBJECTHEADER_TYPE_TAG_OFFSET) == PY_TYPE_PROPERTY:
                return True
    return False


def _descriptor_is_data(descriptor) -> bool:
    if ptr_is_null(descriptor) == 0:
        if is_tagged_int(descriptor) == 0:
            if load_i32(descriptor, PYOBJECTHEADER_TYPE_TAG_OFFSET) == PY_TYPE_PROPERTY:
                return True
    if ptr_is_null(_descriptor_method(descriptor, cstr("__set__"))) == 0:
        return True
    if ptr_is_null(_descriptor_method(descriptor, cstr("__delete__"))) == 0:
        return True
    return False


def _descriptor_call_get(descriptor, obj, owner):
    if ptr_is_null(descriptor) == 0:
        if is_tagged_int(descriptor) == 0:
            if load_i32(descriptor, PYOBJECTHEADER_TYPE_TAG_OFFSET) == PY_TYPE_STATICMETHOD:
                func = pcc_gc_load_ptr(
                    descriptor, ptr_add(descriptor, PYSTATICMETHODOBJECT_FUNC_OFFSET)
                )
                py_incref(func)
                return func
            if load_i32(descriptor, PYOBJECTHEADER_TYPE_TAG_OFFSET) == PY_TYPE_PROPERTY:
                fget = pcc_gc_load_ptr(
                    descriptor,
                    ptr_add(descriptor, PYPROPERTYOBJECT_FGET_OFFSET),
                )
                if ptr_is_null(fget) != 0:
                    py_raise_owned(py_exc_new(6, cstr("unreadable attribute")))
                    return null()
                if ptr_eq(obj, global_load_ptr("py_None")) != 0:
                    py_incref(descriptor)
                    return descriptor
                args = py_tuple_new(1)
                if ptr_is_null(args) != 0:
                    return _class_require_result(
                        null(),
                        cstr("py_tuple_new"),
                        cstr("class callback argument tuple allocation failed"),
                    )
                py_tuple_set_item(args, 0, obj)
                out = py_obj_call(fget, args, global_load_ptr("py_None"))
                _class_require_result(
                    out,
                    cstr("property __get__"),
                    cstr("class callback returned NULL without setting an exception"),
                )
                py_decref(args)
                return out
    method = _descriptor_method(descriptor, cstr("__get__"))
    if ptr_is_null(method) != 0:
        return null()
    args = py_tuple_new(3)
    if ptr_is_null(args) != 0:
        return _class_require_result(
            null(),
            cstr("py_tuple_new"),
            cstr("class callback argument tuple allocation failed"),
        )
    py_tuple_set_item(args, 0, descriptor)
    py_tuple_set_item(args, 1, obj)
    py_tuple_set_item(args, 2, owner)
    out = py_obj_call(method, args, global_load_ptr("py_None"))
    _class_require_result(
        out,
        cstr("descriptor __get__"),
        cstr("class callback returned NULL without setting an exception"),
    )
    py_decref(args)
    return out


def _descriptor_call_set(descriptor, obj, value) -> int:
    if ptr_is_null(descriptor) == 0:
        if is_tagged_int(descriptor) == 0:
            if load_i32(descriptor, PYOBJECTHEADER_TYPE_TAG_OFFSET) == PY_TYPE_PROPERTY:
                fset = pcc_gc_load_ptr(
                    descriptor,
                    ptr_add(descriptor, PYPROPERTYOBJECT_FSET_OFFSET),
                )
                if ptr_is_null(fset) != 0:
                    py_raise_owned(py_exc_new(6, cstr("can't set attribute")))
                    return -1
                args = py_tuple_new(2)
                if ptr_is_null(args) != 0:
                    _class_require_result(
                        null(),
                        cstr("py_tuple_new"),
                        cstr("class callback argument tuple allocation failed"),
                    )
                    return -1
                py_tuple_set_item(args, 0, obj)
                py_tuple_set_item(args, 1, value)
                out = py_obj_call(fset, args, global_load_ptr("py_None"))
                _class_require_result(
                    out,
                    cstr("property __set__"),
                    cstr("class callback returned NULL without setting an exception"),
                )
                py_decref(args)
                if ptr_is_null(out) != 0:
                    return -1
                py_decref(out)
                return 0
    method = _descriptor_method(descriptor, cstr("__set__"))
    if ptr_is_null(method) != 0:
        return -1
    args = py_tuple_new(3)
    if ptr_is_null(args) != 0:
        _class_require_result(
            null(),
            cstr("py_tuple_new"),
            cstr("class callback argument tuple allocation failed"),
        )
        return -1
    py_tuple_set_item(args, 0, descriptor)
    py_tuple_set_item(args, 1, obj)
    py_tuple_set_item(args, 2, value)
    out = py_obj_call(method, args, global_load_ptr("py_None"))
    _class_require_result(
        out,
        cstr("descriptor __set__"),
        cstr("class callback returned NULL without setting an exception"),
    )
    py_decref(args)
    if ptr_is_null(out) != 0:
        return -1
    py_decref(out)
    return 0


def _descriptor_call_delete(descriptor, obj) -> int:
    if ptr_is_null(descriptor) == 0:
        if is_tagged_int(descriptor) == 0:
            if load_i32(descriptor, PYOBJECTHEADER_TYPE_TAG_OFFSET) == PY_TYPE_PROPERTY:
                fdel = pcc_gc_load_ptr(
                    descriptor,
                    ptr_add(descriptor, PYPROPERTYOBJECT_FDEL_OFFSET),
                )
                if ptr_is_null(fdel) != 0:
                    py_raise_owned(py_exc_new(6, cstr("can't delete attribute")))
                    return -1
                args = py_tuple_new(1)
                if ptr_is_null(args) != 0:
                    _class_require_result(
                        null(),
                        cstr("py_tuple_new"),
                        cstr("class callback argument tuple allocation failed"),
                    )
                    return -1
                py_tuple_set_item(args, 0, obj)
                out = py_obj_call(fdel, args, global_load_ptr("py_None"))
                _class_require_result(
                    out,
                    cstr("property __delete__"),
                    cstr("class callback returned NULL without setting an exception"),
                )
                py_decref(args)
                if ptr_is_null(out) != 0:
                    return -1
                py_decref(out)
                return 0
    method = _descriptor_method(descriptor, cstr("__delete__"))
    if ptr_is_null(method) != 0:
        return -1
    args = py_tuple_new(2)
    if ptr_is_null(args) != 0:
        _class_require_result(
            null(),
            cstr("py_tuple_new"),
            cstr("class callback argument tuple allocation failed"),
        )
        return -1
    py_tuple_set_item(args, 0, descriptor)
    py_tuple_set_item(args, 1, obj)
    out = py_obj_call(method, args, global_load_ptr("py_None"))
    _class_require_result(
        out,
        cstr("descriptor __delete__"),
        cstr("class callback returned NULL without setting an exception"),
    )
    py_decref(args)
    if ptr_is_null(out) != 0:
        return -1
    py_decref(out)
    return 0


def _classmethod_bind(descriptor, cls):
    if ptr_is_null(descriptor) != 0 or ptr_is_null(cls) != 0:
        return null()
    if (
        is_tagged_int(descriptor) != 0
        or load_i32(descriptor, PYOBJECTHEADER_TYPE_TAG_OFFSET)
        != PY_TYPE_CLASSMETHOD
    ):
        return descriptor
    func = pcc_gc_load_ptr(
        descriptor,
        ptr_add(descriptor, PYCLASSMETHODOBJECT_FUNC_OFFSET),
    )
    if ptr_is_null(func) != 0:
        return null()
    name = null()
    if _ptr_can_have_header(func) and load_i32(func, PYOBJECTHEADER_TYPE_TAG_OFFSET) == PY_TYPE_FUNC:
        name = load_ptr(func, 72)
    return py_instance_bind_method(func, cls, name)


def _metaclass(cls):
    if not _ptr_is_class(cls):
        return null()
    metaclass = pcc_gc_load_ptr(cls, ptr_add(cls, PYCLASSOBJECT_METACLASS_OFFSET))
    if not _ptr_is_class(metaclass):
        return null()
    return pcc_gc_note_relocation_read(metaclass)


def _object_new_cache_mutex() -> c_ptr:
    slot = global_addr("pcc_object_new_cache_mutex_bits")
    bits: int = atomic_load_i64(slot, 0, "acquire")
    if bits != 0:
        return int_to_ptr(bits)
    candidate = pcc_mutex_new()
    if ptr_is_null(candidate) != 0:
        return null()
    installed: int = atomic_cas_i64(
        slot, 0, 0, ptr_to_int(candidate), "acq_rel", "acquire"
    )
    if installed != 0:
        pcc_mutex_free(candidate)
        return int_to_ptr(installed)
    return candidate


def _object_allocator_supports_class(cls) -> bool:
    """An ordinary class layout, excluding other builtin allocation owners."""
    if not _ptr_is_class(cls):
        return False
    cls = pcc_gc_note_relocation_read(cls)
    n_mro: int = load_i32(cls, PYCLASSOBJECT_N_MRO_OFFSET)
    mro = load_ptr(cls, PYCLASSOBJECT_MRO_OFFSET)
    root = global_load_ptr("py_object_root_cache")
    found_root: int = 0
    index: int = 0
    while index < n_mro:
        owner = pcc_gc_load_ptr(cls, ptr_add(mro, index * C_POINTER_SIZE))
        if ptr_is_null(owner) == 0:
            builtin_tag: int = py_builtin_type_class_tag(owner)
            # -1 is object, -2 is an ordinary user class. Other builtin
            # classes need their allocator; object layout is not a fallback.
            if builtin_tag >= PY_TYPE_NONE or builtin_tag == -3:
                return False
            exc_index: int = 0
            while exc_index < 65:
                exception_class = load_ptr(
                    global_addr("py_exc_classes"), exc_index * C_POINTER_SIZE
                )
                if ptr_is_null(exception_class) == 0:
                    if ptr_eq(owner, exception_class) != 0:
                        return False
                exc_index = exc_index + 1
            if ptr_eq(owner, root) != 0:
                found_root = 1
        index = index + 1
    return found_root != 0


def _object_new_signature_formal(names, kinds, has_defaults, defaults, index: int, name, kind: int) -> bool:
    name_obj = py_str_new(name, strlen(name))
    if ptr_is_null(name_obj) != 0:
        return False
    pcc_gc_pin(name_obj)
    py_tuple_set_item(names, index, name_obj)
    py_tuple_set_item(kinds, index, py_int_from_i64(kind))
    py_tuple_set_item(has_defaults, index, py_bool_from_bit(0))
    py_tuple_set_item(defaults, index, global_load_ptr("py_None"))
    pcc_gc_unpin(name_obj)
    py_decref(name_obj)
    return py_err_occurred() == 0


def _object_new_captures() -> c_ptr:
    # The ordinary native function binder owns positional-only cls, *args
    # and **kwargs. Use its existing signature format, without a builtin-only
    # keyword or argument transport.
    names = py_tuple_new(3)
    pcc_gc_pin(names)
    kinds = py_tuple_new(3)
    pcc_gc_pin(kinds)
    has_defaults = py_tuple_new(3)
    pcc_gc_pin(has_defaults)
    defaults = py_tuple_new(3)
    pcc_gc_pin(defaults)
    signature = null()
    wrapper = null()
    valid: int = 0
    if ptr_is_null(names) == 0 and ptr_is_null(kinds) == 0 and ptr_is_null(has_defaults) == 0 and ptr_is_null(defaults) == 0:
        if _object_new_signature_formal(names, kinds, has_defaults, defaults, 0, cstr("cls"), 1):
            if _object_new_signature_formal(names, kinds, has_defaults, defaults, 1, cstr("args"), 3):
                if _object_new_signature_formal(names, kinds, has_defaults, defaults, 2, cstr("kwargs"), 4):
                    valid = 1
    if valid != 0:
        signature = py_tuple_new(5)
        pcc_gc_pin(signature)
        if ptr_is_null(signature) == 0:
            magic = py_str_new(cstr("__pcc_func_signature_v1__"), 25)
            if ptr_is_null(magic) == 0:
                pcc_gc_pin(magic)
                py_tuple_set_item(signature, 0, magic)
                py_tuple_set_item(signature, 1, names)
                py_tuple_set_item(signature, 2, kinds)
                py_tuple_set_item(signature, 3, has_defaults)
                py_tuple_set_item(signature, 4, defaults)
                pcc_gc_unpin(magic)
                py_decref(magic)
                wrapper = py_tuple_new(2)
                pcc_gc_pin(wrapper)
                if ptr_is_null(wrapper) == 0:
                    empty = py_tuple_new(0)
                    if ptr_is_null(empty) == 0:
                        pcc_gc_pin(empty)
                        py_tuple_set_item(wrapper, 0, empty)
                        py_tuple_set_item(wrapper, 1, signature)
                        pcc_gc_unpin(empty)
                        py_decref(empty)
                    else:
                        pcc_gc_unpin(wrapper)
                        py_decref(wrapper)
                        wrapper = null()
    pcc_gc_unpin(signature)
    py_decref(signature)
    pcc_gc_unpin(defaults)
    py_decref(defaults)
    pcc_gc_unpin(has_defaults)
    py_decref(has_defaults)
    pcc_gc_unpin(kinds)
    py_decref(kinds)
    pcc_gc_unpin(names)
    py_decref(names)
    pcc_gc_unpin(wrapper)
    return wrapper


def _object_new_accepts_excess(cls) -> bool:
    current = _class_new_lookup(cls)
    current_pin: int = 0
    if _ptr_can_have_header(current):
        current_pin = load_i32(current, PYOBJECTHEADER_FLAGS_OFFSET) & 64
        pcc_gc_pin(current)
    inherited = _object_new_cached(global_load_ptr("py_object_root_cache"))
    inherited_pin: int = 0
    if _ptr_can_have_header(inherited):
        inherited_pin = load_i32(inherited, PYOBJECTHEADER_FLAGS_OFFSET) & 64
        pcc_gc_pin(inherited)
    same: int = ptr_eq(current, inherited)
    if same == 0 and _ptr_can_have_header(current) and ptr_is_null(inherited) == 0:
        inherited_func = pcc_gc_load_ptr(inherited, ptr_add(inherited, PYSTATICMETHODOBJECT_FUNC_OFFSET))
        current_func = current
        if load_i32(current, PYOBJECTHEADER_TYPE_TAG_OFFSET) == PY_TYPE_STATICMETHOD:
            current_func = pcc_gc_load_ptr(current, ptr_add(current, PYSTATICMETHODOBJECT_FUNC_OFFSET))
        same = ptr_eq(current_func, inherited_func)
    if _ptr_can_have_header(inherited):
        pcc_gc_unpin(inherited)
        if inherited_pin != 0:
            atomic_rmw_i32("or", inherited, PYOBJECTHEADER_FLAGS_OFFSET, 64, "relaxed")
    py_decref(inherited)
    if _ptr_can_have_header(current):
        pcc_gc_unpin(current)
        if current_pin != 0:
            atomic_rmw_i32("or", current, PYOBJECTHEADER_FLAGS_OFFSET, 64, "relaxed")
    py_decref(current)
    if same == 0:
        return False
    init = _class_attr_lookup_in_mro(cls, cstr("__init__"))
    if ptr_is_null(init) == 0:
        py_decref(init)
        return True
    return ptr_is_null(_class_lookup_in_mro(cls, cstr("__init__"))) == 0


def _object_new_entry(captures: c_ptr, args: c_ptr) -> c_ptr:
    if py_tuple_len(args) != 3:
        py_raise_owned(py_exc_new(3, cstr("object.__new__() requires a class argument")))
        return null()
    cls = py_tuple_get(args, 0)
    extra_args = py_tuple_get(args, 1)
    kwargs = py_tuple_get(args, 2)
    has_excess: int = 0
    if py_tuple_len(extra_args) != 0 or py_dict_len(kwargs) != 0:
        has_excess = 1
    py_decref(extra_args)
    py_decref(kwargs)
    if not _ptr_is_class(cls):
        py_decref(cls)
        py_raise_owned(py_exc_new(3, cstr("object.__new__() argument must be a type")))
        return null()
    if not _object_allocator_supports_class(cls):
        py_decref(cls)
        py_raise_owned(py_exc_new(11, cstr("owned object allocator does not implement this builtin class layout")))
        return null()
    if has_excess != 0:
        if not _object_new_accepts_excess(cls):
            py_decref(cls)
            if py_err_occurred() == 0:
                py_raise_owned(py_exc_new(3, cstr("object.__new__() takes exactly one argument for this class")))
            return null()
    result = py_instance_new(cls)
    py_decref(cls)
    return _class_require_result(
        result, cstr("object.__new__"), cstr("object allocation failed without setting an exception")
    )


def _object_new_cache_fill(root, result_slot) -> None:
    """Called with the owned cache mutex, without the GC graph lock."""
    attrs = py_class_attrs_dict(root, 1)
    if ptr_is_null(attrs) != 0:
        _class_require_result(null(), cstr("object.__new__"), cstr("object allocator namespace allocation failed"))
        return
    prior_attrs_pin: int = load_i32(attrs, PYOBJECTHEADER_FLAGS_OFFSET) & 64
    pcc_gc_pin(attrs)
    key = py_str_new(cstr("__new__"), 7)
    if ptr_is_null(key) == 0:
        pcc_gc_pin(key)
        descriptor = py_dict_get(attrs, key)
        if ptr_is_null(descriptor) != 0:
            captures = _object_new_captures()
            pcc_gc_pin(captures)
            function = null()
            if ptr_is_null(captures) == 0 and py_err_occurred() == 0:
                if py_builtin_type_class_tag(root) == PY_TYPE_STR:
                    function = py_func_new_bound(_str_new_entry, captures, cstr("__new__"), null())
                else:
                    function = py_func_new_bound(_object_new_entry, captures, cstr("__new__"), null())
                pcc_gc_pin(function)
            pcc_gc_unpin(captures)
            py_decref(captures)
            if ptr_is_null(function) == 0:
                if py_err_occurred() == 0:
                    descriptor = py_staticmethod_new(function)
                    if ptr_is_null(descriptor) == 0:
                        pcc_gc_pin(descriptor)
                        if py_err_occurred() == 0:
                            py_dict_set(attrs, key, descriptor)
                            if py_err_occurred() == 0:
                                pcc_gc_store_root(result_slot, descriptor)
                        pcc_gc_unpin(descriptor)
                        py_decref(descriptor)
                pcc_gc_unpin(function)
                py_decref(function)
        else:
            prior_pin: int = load_i32(descriptor, PYOBJECTHEADER_FLAGS_OFFSET) & 64
            pcc_gc_pin(descriptor)
            pcc_gc_store_root(result_slot, descriptor)
            pcc_gc_unpin(descriptor)
            if prior_pin != 0:
                atomic_rmw_i32("or", descriptor, PYOBJECTHEADER_FLAGS_OFFSET, 64, "relaxed")
            py_decref(descriptor)
        pcc_gc_unpin(key)
        py_decref(key)
    pcc_gc_unpin(attrs)
    if prior_attrs_pin != 0:
        atomic_rmw_i32("or", attrs, PYOBJECTHEADER_FLAGS_OFFSET, 64, "relaxed")
    if ptr_is_null(load_ptr(result_slot, 0)) != 0:
        _class_require_result(null(), cstr("object.__new__"), cstr("object allocator callable allocation failed"))


def _object_new_cached(root) -> c_ptr:
    slot = stack_alloc(C_POINTER_SIZE)
    store_ptr(slot, 0, null())
    handle = pcc_gc_scheduler_root_register_handle(slot)
    if ptr_is_null(handle) != 0:
        return _class_require_result(null(), cstr("object.__new__"), cstr("object allocator result root registration failed"))
    mutex = _object_new_cache_mutex()
    if ptr_is_null(mutex) != 0 or pcc_mutex_lock(mutex) != 0:
        pcc_gc_scheduler_root_unregister_handle(handle)
        return _class_require_result(null(), cstr("object.__new__"), cstr("object allocator cache lock failed"))
    _object_new_cache_fill(root, slot)
    pcc_mutex_unlock(mutex)
    result = load_ptr(slot, 0)
    prior_pin: int = 0
    if ptr_is_null(result) == 0:
        prior_pin = load_i32(result, PYOBJECTHEADER_FLAGS_OFFSET) & 64
        pcc_gc_pin(result)
    pcc_gc_scheduler_root_unregister_handle(handle)
    return pcc_gc_take_pinned_slot(slot, prior_pin)


def _class_new_return_value(value, key, prior_pin: int) -> c_ptr:
    # The caller pins the owned lookup result before this helper can park.
    # Key cleanup must finish before the raw return reference is unpinned.
    slot = stack_alloc(C_POINTER_SIZE)
    store_ptr(slot, 0, value)
    pcc_gc_unpin(key)
    py_decref(key)
    return pcc_gc_take_pinned_slot(slot, prior_pin)


def _class_new_lookup(cls) -> c_ptr:
    """Resolve namespace and native method entries in the same C3 order."""
    root = global_load_ptr("py_object_root_cache")
    key = py_str_new(cstr("__new__"), 7)
    if ptr_is_null(key) != 0:
        return null()
    pcc_gc_pin(key)
    n_mro: int = load_i32(cls, PYCLASSOBJECT_N_MRO_OFFSET)
    mro = load_ptr(cls, PYCLASSOBJECT_MRO_OFFSET)
    i: int = 0
    while i < n_mro:
        owner = pcc_gc_load_ptr(cls, ptr_add(mro, i * C_POINTER_SIZE))
        if ptr_is_null(owner) == 0:
            if ptr_eq(owner, root) != 0:
                pcc_gc_unpin(key)
                py_decref(key)
                if not _object_allocator_supports_class(cls):
                    py_raise_owned(py_exc_new(11, cstr("owned __new__ lookup does not implement this builtin allocator")))
                    return null()
                return _object_new_cached(root)
            attrs = py_class_attrs_dict(owner, 0)
            if ptr_is_null(attrs) == 0:
                value = py_dict_get(attrs, key)
                if ptr_is_null(value) == 0:
                    prior_pin: int = 0
                    if _ptr_can_have_header(value):
                        prior_pin = load_i32(value, PYOBJECTHEADER_FLAGS_OFFSET) & 64
                    pcc_gc_pin(value)
                    return _class_new_return_value(value, key, prior_pin)
            n_methods: int = load_i32(owner, PYCLASSOBJECT_N_METHODS_OFFSET)
            methods = load_ptr(owner, PYCLASSOBJECT_METHODS_OFFSET)
            j: int = 0
            while j < n_methods:
                offset: int = j * PYCLASSMETHOD_SIZE
                method_name = load_ptr(methods, offset + PYCLASSMETHOD_NAME_OFFSET)
                if _strs_eq(method_name, cstr("__new__")) != 0:
                    value = pcc_gc_note_relocation_read(load_ptr(methods, offset + PYCLASSMETHOD_FUNC_OFFSET))
                    prior_pin: int = 0
                    if _ptr_can_have_header(value):
                        prior_pin = load_i32(value, PYOBJECTHEADER_FLAGS_OFFSET) & 64
                    pcc_gc_pin(value)
                    py_incref(value)
                    return _class_new_return_value(value, key, prior_pin)
                j = j + 1
            if py_builtin_type_class_tag(owner) == PY_TYPE_STR:
                pcc_gc_unpin(key)
                py_decref(key)
                return _object_new_cached(owner)
        i = i + 1
    pcc_gc_unpin(key)
    py_decref(key)
    return null()


def _metaclass_call_body(slots, pins):
    # The namespace owns descriptors; the native method table can also
    # contain a raw entry point. Preserve that distinction while binding.
    meta = load_ptr(slots, 4 * C_POINTER_SIZE)
    descriptor = _class_attr_lookup_in_mro(meta, cstr("__call__"))
    borrowed: int = 0
    if ptr_is_null(descriptor) != 0:
        if py_err_occurred() != 0:
            return null()
        descriptor = py_class_lookup(load_ptr(slots, 4 * C_POINTER_SIZE), cstr("__call__"))
        borrowed = 1
    if ptr_is_null(descriptor) != 0:
        # No override, including inherited default type.__call__. The caller
        # performs ordinary type construction instead of calling itself.
        return null()
    _instance_lookup_hold(slots, pins, 5, descriptor, borrowed)
    descriptor = load_ptr(slots, 5 * C_POINTER_SIZE)
    cls = load_ptr(slots, C_POINTER_SIZE)
    meta = load_ptr(slots, 4 * C_POINTER_SIZE)
    if not _ptr_can_have_header(descriptor):
        # py_instance_bind_method's owned wrapper already supports native
        # entries. Never read a PyObject header from an unmanaged code address.
        bound = py_instance_bind_method(descriptor, cls, cstr("__call__"))
    else:
        tag: int = load_i32(descriptor, PYOBJECTHEADER_TYPE_TAG_OFFSET)
        if tag == PY_TYPE_FUNC:
            bound = py_instance_bind_method(descriptor, cls, cstr("__call__"))
        elif tag == PY_TYPE_CLASSMETHOD:
            # A classmethod descriptor belongs to the metaclass, so bind its
            # owner once; do not also prepend the class being constructed.
            bound = _classmethod_bind(descriptor, meta)
        else:
            bound = _descriptor_call_get(descriptor, cls, meta)
            if ptr_is_null(bound) != 0:
                if py_err_occurred() != 0:
                    return null()
                bound = load_ptr(slots, 5 * C_POINTER_SIZE)
                py_incref(bound)
    if ptr_is_null(bound) != 0:
        return _class_require_result(null(), cstr("metaclass __call__ binding"),
                                     cstr("metaclass __call__ binding returned NULL without an exception"))
    _instance_lookup_hold(slots, pins, 6, bound, 0)
    result = py_obj_call(load_ptr(slots, 6 * C_POINTER_SIZE),
                         load_ptr(slots, 2 * C_POINTER_SIZE),
                         load_ptr(slots, 3 * C_POINTER_SIZE))
    return _class_require_result(result, cstr("metaclass __call__"),
                                 cstr("metaclass __call__ returned NULL without an exception"))


@c_abi_export("py_class_metaclass_call")
def py_class_metaclass_call(cls, args, kwargs):
    """One owned override result; NULL/no-error means ordinary construction.

    Special-method lookup uses the actual class's metaclass, never the class
    or instance namespace. Descriptor failures and failed callbacks preserve
    their exception and must not select the default construction path.
    """
    meta = _metaclass(cls)
    if ptr_is_null(meta) != 0:
        return null()
    if py_builtin_type_class_tag(meta) == PY_TYPE_CLASS:
        return null()
    # Reuse the eight-slot lookup lease protocol, including raw-method
    # classification, original pin states, aliasing and exception retirement.
    # result, cls, args, kwargs, metaclass, descriptor, bound callable, error.
    slots = stack_alloc(8 * C_POINTER_SIZE)
    pins = stack_alloc(8 * C_POINTER_SIZE)
    handles = stack_alloc(8 * C_POINTER_SIZE)
    memset(slots, 0, 8 * C_POINTER_SIZE)
    memset(pins, 0, 8 * C_POINTER_SIZE)
    memset(handles, 0, 8 * C_POINTER_SIZE)
    _instance_lookup_hold(slots, pins, 1, cls, 1)
    _instance_lookup_hold(slots, pins, 2, args, 1)
    _instance_lookup_hold(slots, pins, 3, kwargs, 1)
    _instance_lookup_hold(slots, pins, 4, meta, 1)
    count: int = 0
    while count < 8:
        handle = pcc_gc_scheduler_root_register_handle(ptr_add(slots, count * C_POINTER_SIZE))
        if ptr_is_null(handle) != 0:
            break
        store_ptr(handles, count * C_POINTER_SIZE, handle)
        count = count + 1
    if count == 8:
        result = _metaclass_call_body(slots, pins)
        _instance_lookup_hold(slots, pins, 0, result, 0)
    else:
        _class_require_result(null(), cstr("metaclass __call__"),
                              cstr("metaclass temporary root registration failed"))
    prior_result_pin: int = load_i64(pins, 0)
    saved_error_index: int = 7
    saved_error_offset: int = saved_error_index * C_POINTER_SIZE
    if py_err_occurred() != 0:
        _instance_lookup_hold(slots, pins, saved_error_index, py_current_exception(), 1)
    index: int = 6
    while index > 0:
        _instance_lookup_release(slots, pins, index)
        index = index - 1
    if ptr_is_null(load_ptr(slots, saved_error_offset)) == 0:
        py_raise(load_ptr(slots, saved_error_offset))
        _instance_lookup_release(slots, pins, saved_error_index)
    index = 0
    while index < count:
        pcc_gc_scheduler_root_unregister_handle(load_ptr(handles, index * C_POINTER_SIZE))
        index = index + 1
    return pcc_gc_take_pinned_slot(slots, prior_result_pin)


@c_abi_export("py_class_getattr")
def py_class_getattr(cls, name):
    if not _ptr_is_class(cls) or ptr_is_null(name) != 0:
        return null()
    cls = pcc_gc_note_relocation_read(cls)
    if _cstr_is_dunder_dict(name) != 0:
        attrs = py_class_attrs_dict(cls, 1)
        if ptr_is_null(attrs) == 0:
            py_incref(attrs)
        return attrs
    metaclass = _metaclass(cls)
    meta_attr = null()
    if ptr_is_null(metaclass) == 0:
        meta_attr = _class_attr_lookup_in_mro(metaclass, name)
        if ptr_is_null(meta_attr) == 0:
            if _descriptor_is_data(meta_attr):
                out = _descriptor_call_get(meta_attr, cls, metaclass)
                py_decref(meta_attr)
                if ptr_is_null(out) == 0 or py_err_occurred() != 0:
                    return out
            else:
                py_decref(meta_attr)
    if _strs_eq(name, cstr("__new__")) != 0:
        value = _class_new_lookup(cls)
    else:
        value = _class_attr_lookup_in_mro(cls, name)
    if py_err_occurred() != 0:
        return null()
    if ptr_is_null(value) == 0:
        bound = _classmethod_bind(value, cls)
        if ptr_eq(bound, value) == 0:
            py_decref(value)
            return bound
        descriptor_value = _descriptor_call_get(value, global_load_ptr("py_None"), cls)
        if ptr_is_null(descriptor_value) == 0 or py_err_occurred() != 0:
            py_decref(value)
            return descriptor_value
        return bound
    if ptr_is_null(metaclass) == 0:
        meta_attr = _class_attr_lookup_in_mro(metaclass, name)
        if ptr_is_null(meta_attr) == 0:
            out = _descriptor_call_get(meta_attr, cls, metaclass)
            if ptr_is_null(out) == 0 or py_err_occurred() != 0:
                py_decref(meta_attr)
                return out
            return meta_attr
    method = py_class_lookup(cls, name)
    if ptr_is_null(method) == 0:
        py_incref(method)
    return method


@c_abi_export("py_class_setattr_raw")
def py_class_setattr_raw(cls, name, value) -> int:
    if not _ptr_is_class(cls):
        return -1
    if ptr_is_null(name) != 0 or ptr_is_null(value) != 0:
        return -1
    cls = pcc_gc_note_relocation_read(cls)
    attrs = py_class_attrs_dict(cls, 1)
    if ptr_is_null(attrs) != 0:
        return -1
    key = py_str_new(name, strlen(name))
    if ptr_is_null(key) != 0:
        return -1
    if _strs_eq(name, cstr("__del__")) != 0:
        _note_class_defines_del()
    py_dict_set(attrs, key, value)
    py_decref(key)
    _bump_class_attr_cache_epoch()
    return 0


@c_abi_export("py_class_setattr")
def py_class_setattr(cls, name, value) -> int:
    if not _ptr_is_class(cls):
        return -1
    if ptr_is_null(name) != 0 or ptr_is_null(value) != 0:
        return -1
    cls = pcc_gc_note_relocation_read(cls)
    metaclass = _metaclass(cls)
    if ptr_is_null(metaclass) == 0:
        meta_attr = _class_attr_lookup_in_mro(metaclass, name)
        if ptr_is_null(meta_attr) == 0:
            if _descriptor_is_data(meta_attr):
                rc: int = _descriptor_call_set(meta_attr, cls, value)
                py_decref(meta_attr)
                return rc
            py_decref(meta_attr)
    return py_class_setattr_raw(cls, name, value)


@c_abi_export("py_class_delattr")
def py_class_delattr(cls, name) -> int:
    if not _ptr_is_class(cls) or ptr_is_null(name) != 0:
        return -1
    cls = pcc_gc_note_relocation_read(cls)
    metaclass = _metaclass(cls)
    if ptr_is_null(metaclass) == 0:
        meta_attr = _class_attr_lookup_in_mro(metaclass, name)
        if ptr_is_null(meta_attr) == 0:
            if _descriptor_is_data(meta_attr):
                rc: int = _descriptor_call_delete(meta_attr, cls)
                py_decref(meta_attr)
                return rc
            py_decref(meta_attr)
    attrs = py_class_attrs_dict(cls, 0)
    if ptr_is_null(attrs) != 0:
        return -1
    key = py_str_new(name, strlen(name))
    if ptr_is_null(key) != 0:
        return -1
    rc: int = py_dict_del(attrs, key)
    py_decref(key)
    if rc == 0:
        _bump_class_attr_cache_epoch()
    return rc


@c_abi_export("py_class_attrs_dispose")
def py_class_attrs_dispose(cls) -> None:
    if ptr_is_null(cls) != 0:
        return
    attrs = load_ptr(cls, PYCLASSOBJECT_ATTRS_OFFSET)
    if ptr_is_null(attrs) == 0:
        store_ptr(cls, PYCLASSOBJECT_ATTRS_OFFSET, null())
        py_decref(attrs)


@c_abi_export("py_class_attrs_retarget")
def py_class_attrs_retarget(source, destination) -> int:
    if not _ptr_is_class(source) or not _ptr_is_class(destination):
        return -1
    attrs = pcc_gc_load_ptr(source, ptr_add(source, PYCLASSOBJECT_ATTRS_OFFSET))
    if ptr_is_null(attrs) != 0:
        return 0
    existing = pcc_gc_load_ptr(destination, ptr_add(destination, PYCLASSOBJECT_ATTRS_OFFSET))
    if ptr_is_null(existing) == 0:
        return -1
    pcc_gc_store_ptr(destination, ptr_add(destination, PYCLASSOBJECT_ATTRS_OFFSET), attrs)
    return 0


@c_abi_export("py_class_lookup")
def py_class_lookup(cls, name):
    if not _ptr_is_class(cls):
        return null()
    if ptr_is_null(name) != 0:
        return null()
    cls = pcc_gc_note_relocation_read(cls)
    if _cstr_is_dunder_name(name) != 0:
        cls_name = load_ptr(cls, PYCLASSOBJECT_NAME_OFFSET)
        if ptr_is_null(cls_name) != 0:
            return py_str_new(name, 0)
        return py_str_new(cls_name, strlen(cls_name))
    if _cstr_is_dunder_mro(name) != 0:
        n_mro: int = load_i32(cls, PYCLASSOBJECT_N_MRO_OFFSET)
        mro = load_ptr(cls, PYCLASSOBJECT_MRO_OFFSET)
        t = py_tuple_new(n_mro)
        i: int = 0
        while i < n_mro:
            item = pcc_gc_load_ptr(cls, ptr_add(mro, i * 8))
            py_tuple_set_item(t, i, item)
            i = i + 1
        return t
    if _strs_eq(name, cstr("__base__")) != 0:
        n_bases: int = load_i32(cls, PYCLASSOBJECT_N_BASES_OFFSET)
        bases = load_ptr(cls, PYCLASSOBJECT_BASES_OFFSET)
        if n_bases <= 0 or ptr_is_null(bases) != 0:
            return global_load_ptr("py_None")
        return pcc_gc_load_ptr(cls, bases)
    return _class_lookup_in_mro(cls, name)


@c_abi_export("py_class_add_method")
def py_class_add_method(cls, name, func) -> None:
    # ABI contract mirrors py_internal.h: name is immutable/lifetime-borrowed,
    # and callers serialize mutation of this realloc-backed method table.
    if not _ptr_is_class(cls):
        return
    if ptr_is_null(name) != 0:
        return
    signature = stack_alloc(8)
    _method_name_signature(name, signature)
    name_hash: int = load_i32(signature, 0) & 0xFFFFFFFF
    name_length: int = load_i32(signature, 4)
    cls = pcc_gc_note_relocation_read(cls)
    n_methods_i32: int = load_i32(cls, PYCLASSOBJECT_N_METHODS_OFFSET)
    new_n: int = n_methods_i32 + 1
    methods = load_ptr(cls, PYCLASSOBJECT_METHODS_OFFSET)
    new_methods = realloc(methods, new_n * PYCLASSMETHOD_SIZE)
    if ptr_is_null(new_methods) != 0:
        return
    method_off: int = n_methods_i32 * PYCLASSMETHOD_SIZE
    store_ptr(new_methods, method_off + PYCLASSMETHOD_NAME_OFFSET, name)
    store_ptr(new_methods, method_off + PYCLASSMETHOD_FUNC_OFFSET, func)
    store_i32(new_methods, method_off + PYCLASSMETHOD_NAME_HASH_OFFSET, name_hash)
    store_i32(new_methods, method_off + PYCLASSMETHOD_NAME_LENGTH_OFFSET, name_length)
    store_ptr(cls, PYCLASSOBJECT_METHODS_OFFSET, new_methods)
    store_i32(cls, PYCLASSOBJECT_N_METHODS_OFFSET, new_n)
    payload_offset: int = -1
    if ptr_is_null(methods) == 0:
        if ptr_eq(methods, new_methods) == 0:
            payload_offset = pcc_gc_backend4_zpage_retarget_owner_payload_span(
                cls,
                methods,
                new_methods,
                new_n * PYCLASSMETHOD_SIZE,
            )
    if payload_offset < 0:
        if ptr_is_null(methods) == 0:
            if ptr_eq(methods, new_methods) == 0:
                pcc_gc_backend4_zpage_unregister_owner_payload_span(cls, methods)
        pcc_gc_backend4_zpage_register_owner_payload_span(
            cls, new_methods, new_n * PYCLASSMETHOD_SIZE
        )
    _class_note_borrowed_metadata_slot_store(
        cls,
        ptr_add(new_methods, method_off + 8),
        func,
    )
    if _strs_eq(name, cstr("__del__")) != 0:
        # Borrowed update-only alias for GC forwarding.  py_user_del_dispatch
        # deliberately resolves through py_class_lookup rather than treating
        # this slot as a separate semantic cache.
        _note_class_defines_del()
        store_ptr(cls, PYCLASSOBJECT_DEL_METHOD_OFFSET, func)
        _class_note_borrowed_metadata_slot_store(cls, ptr_add(cls, PYCLASSOBJECT_DEL_METHOD_OFFSET), func)
    # A newly installed data descriptor can shadow an already-cached instance
    # field. Release-publish only after the whole method-table/GC metadata
    # transaction is complete, matching the C oracle.
    _bump_class_attr_cache_epoch()


@c_abi_export("py_class_set_metaclass")
def py_class_set_metaclass(cls, metaclass) -> None:
    if not _ptr_is_class(cls):
        return
    cls = pcc_gc_note_relocation_read(cls)
    if ptr_is_null(metaclass) == 0:
        if not _ptr_is_class(metaclass):
            return
        metaclass = pcc_gc_note_relocation_read(metaclass)
    # A metaclass is a real class owner, even while user classes remain
    # immortal. The retaining store also retires the previous relation.
    pcc_gc_store_ptr(
        cls, ptr_add(cls, PYCLASSOBJECT_METACLASS_OFFSET), metaclass,
    )


@c_abi_export("py_instance_new")
def py_instance_new(cls) -> c_ptr:
    if not _ptr_is_class(cls):
        return null()
    cls = pcc_gc_note_relocation_read(cls)
    n_fields_i32: int = load_i32(cls, PYCLASSOBJECT_N_FIELDS_OFFSET)
    if n_fields_i32 < 0:
        n_fields_i32 = 0
    n_slots: int = _instance_storage_slot_count(cls)
    size: int = PYINSTANCEOBJECT_SIZE + n_slots * C_POINTER_SIZE
    inst = pcc_gc_alloc(
        size,
        load_i32(cls, PYCLASSOBJECT_TYPE_TAG_ALLOC_OFFSET),
        0,
    )
    if ptr_is_null(inst) != 0:
        return null()
    memset(
        ptr_add(inst, PYINSTANCEOBJECT_CLS_OFFSET),
        0,
        size - PYINSTANCEOBJECT_CLS_OFFSET,
    )
    store_i64(inst, PYOBJECTHEADER_REFCOUNT_OFFSET, 1)
    type_tag_alloc: int = load_i32(cls, PYCLASSOBJECT_TYPE_TAG_ALLOC_OFFSET)
    store_i32(inst, PYOBJECTHEADER_TYPE_TAG_OFFSET, type_tag_alloc)
    store_ptr(inst, PYINSTANCEOBJECT_CLS_OFFSET, cls)
    py_gc_track(inst)
    # The freshly allocated instance is already a new reference; keep this as
    # a raw pointer expression so return lowering does not retain it again.
    return ptr_add(inst, 0)


@c_abi_export("py_instance_get_field")
def py_instance_get_field(inst, idx: int):
    # Every static `self.field` read lands here.  With the read barrier off
    # and a non-forwarding collector, pcc_gc_load_ptr is a plain load and
    # pcc_gc_note_relocation_read the identity, so this path makes the same
    # checks -- provenance probe included -- without calling them.
    if (
        load_i32(global_addr("pcc_gc_read_barrier_enabled"), 0) == 0
        and load_i32(global_addr("pcc_gc_backend_selected"), 0) != 3
        and load_i32(global_addr("pcc_gc_backend_selected"), 0) != 4
    ):
        if ptr_is_null(inst) != 0 or is_tagged_int(inst) != 0:
            return null()
        if pcc_gc_pointer_is_managed(inst) == 0:
            return null()
        tag: int = load_i32(inst, PYOBJECTHEADER_TYPE_TAG_OFFSET)
        if tag != PY_TYPE_INSTANCE and tag < PY_TYPE_USER_CLASS_START:
            return null()
        cls = load_ptr(inst, PYINSTANCEOBJECT_CLS_OFFSET)
        if ptr_is_null(cls) != 0:
            return null()
        if load_i32(cls, PYOBJECTHEADER_TYPE_TAG_OFFSET) != PY_TYPE_CLASS:
            return null()
        if idx < 0 or idx >= load_i32(cls, PYCLASSOBJECT_N_FIELDS_OFFSET):
            return null()
        field = load_ptr(inst, PYINSTANCEOBJECT_FIELDS_OFFSET + idx * C_POINTER_SIZE)
        if ptr_is_null(field) == 0:
            py_incref(field)
        else:
            return _instance_missing_field_lookup(inst, idx)
        return field
    if not _ptr_is_instance(inst):
        return null()
    if idx < 0:
        return null()
    # Validation heals its own local copy. Derive every subsequent slot from
    # the current receiver, rather than the caller's old relocation address.
    inst = pcc_gc_note_relocation_read(inst)
    cls = pcc_gc_load_ptr(inst, ptr_add(inst, PYINSTANCEOBJECT_CLS_OFFSET))
    n_fields: int = load_i32(cls, PYCLASSOBJECT_N_FIELDS_OFFSET)
    if idx >= n_fields:
        return null()
    fields_base = ptr_add(inst, PYINSTANCEOBJECT_FIELDS_OFFSET)
    v = pcc_gc_load_ptr(inst, ptr_add(fields_base, idx * C_POINTER_SIZE))
    if ptr_is_null(v) == 0:
        py_incref(v)
    else:
        return _instance_missing_field_lookup(inst, idx)
    return v


def _instance_missing_field_lookup(inst, idx: int, default_only: int = 0):
    """A valid but unbound physical slot still obeys ordinary lookup.

    NULL is absence, not Python None. Resolve the stable field name and let
    semantic lookup handle class defaults, descriptors, __getattr__, and the
    missing-attribute exception. Invalid indices never enter this helper.
    """
    return _instance_lookup_rooted(inst, idx, null(), default_only)


def _instance_lookup_rooted(inst, idx: int, name, lookup_kind: int):
    """Keep lookup inputs current while the rooted callback path runs.

    A nonnegative index resolves an unbound physical field's stable name;
    otherwise the caller supplies the attribute name. Lookup kinds are normal
    (0), default (1), and custom __getattribute__ (2).
    """
    roots = stack_alloc(3 * C_POINTER_SIZE)
    handles = stack_alloc(3 * C_POINTER_SIZE)
    pins = stack_alloc(2 * C_POINTER_SIZE)
    memset(roots, 0, 3 * C_POINTER_SIZE)
    memset(handles, 0, 3 * C_POINTER_SIZE)
    memset(pins, 0, 2 * C_POINTER_SIZE)
    pcc_py_gc_minor_graph_lock()
    inst = pcc_gc_note_relocation_read(inst)
    cls = pcc_gc_load_ptr(inst, ptr_add(inst, PYINSTANCEOBJECT_CLS_OFFSET))
    store_ptr(roots, 0, inst)
    store_ptr(roots, C_POINTER_SIZE, cls)
    index: int = 0
    while index < 2:
        value = load_ptr(roots, index * C_POINTER_SIZE)
        store_i64(pins, index * C_POINTER_SIZE, load_i32(value, PYOBJECTHEADER_FLAGS_OFFSET) & 64)
        pcc_gc_pin(value)
        index = index + 1
    index = 0
    while index < 3:
        handle = pcc_gc_scheduler_root_register_handle(ptr_add(roots, index * C_POINTER_SIZE))
        if ptr_is_null(handle) != 0:
            break
        store_ptr(handles, index * C_POINTER_SIZE, handle)
        index = index + 1
    pcc_py_gc_minor_graph_unlock()
    if index == 3:
        cls = pcc_gc_load_ptr(null(), ptr_add(roots, C_POINTER_SIZE))
        if idx >= 0:
            names = load_ptr(cls, PYCLASSOBJECT_FIELD_NAMES_OFFSET)
            name = load_ptr(names, idx * C_POINTER_SIZE)
        if lookup_kind != 0:
            result = _instance_getattr_default_rooted(
                pcc_gc_load_ptr(null(), roots), cls, name, lookup_kind - 1,
            )
        else:
            result = py_obj_getattr(pcc_gc_load_ptr(null(), roots), name)
        store_ptr(roots, 2 * C_POINTER_SIZE, result)
    prior_result_pin: int = 0
    pcc_py_gc_minor_graph_lock()
    result = pcc_gc_load_ptr(null(), ptr_add(roots, 2 * C_POINTER_SIZE))
    if ptr_is_null(result) == 0 and is_tagged_int(result) == 0:
        prior_result_pin = load_i32(result, PYOBJECTHEADER_FLAGS_OFFSET) & 64
        if ptr_eq(result, load_ptr(roots, 0)):
            prior_result_pin = load_i64(pins, 0)
        if ptr_eq(result, load_ptr(roots, C_POINTER_SIZE)):
            prior_result_pin = load_i64(pins, C_POINTER_SIZE)
        pcc_gc_pin(result)
    pcc_py_gc_minor_graph_unlock()
    cleanup_index: int = 0
    pcc_py_gc_minor_graph_lock()
    while cleanup_index < 2:
        value = pcc_gc_load_ptr(null(), ptr_add(roots, cleanup_index * C_POINTER_SIZE))
        pcc_gc_unpin(value)
        if ptr_eq(value, result) != 0 or load_i64(pins, cleanup_index * C_POINTER_SIZE) != 0:
            # Balance this input's pin lease, but preserve the original bit
            # or the independent result lease. No moving window under lock.
            atomic_rmw_i32("or", value, PYOBJECTHEADER_FLAGS_OFFSET, 64, "relaxed")
        cleanup_index = cleanup_index + 1
    pcc_py_gc_minor_graph_unlock()
    cleanup_index = 0
    while cleanup_index < 3:
        handle = load_ptr(handles, cleanup_index * C_POINTER_SIZE)
        if ptr_is_null(handle) == 0:
            pcc_gc_scheduler_root_unregister_handle(handle)
        cleanup_index = cleanup_index + 1
    if index != 3:
        return _class_require_result(null(), cstr("instance field lookup"), cstr("instance field lookup root registration failed"))
    return pcc_gc_take_pinned_slot(ptr_add(roots, 2 * C_POINTER_SIZE), prior_result_pin)


@c_abi_export("py_instance_set_field")
def py_instance_set_field(inst, idx: int, value) -> None:
    if not _ptr_is_instance(inst):
        return
    if idx < 0:
        return
    cls = pcc_gc_load_ptr(inst, ptr_add(inst, PYINSTANCEOBJECT_CLS_OFFSET))
    n_fields: int = load_i32(cls, PYCLASSOBJECT_N_FIELDS_OFFSET)
    if idx >= n_fields:
        return
    fields_base = ptr_add(inst, PYINSTANCEOBJECT_FIELDS_OFFSET)
    pcc_gc_store_ptr(inst, ptr_add(fields_base, idx * C_POINTER_SIZE), value)


@c_abi_export("py_valuebox_new")
def py_valuebox_new(cls) -> c_ptr:
    box = py_instance_new(cls)
    if ptr_is_null(box) != 0:
        return null()
    store_i32(box, PYOBJECTHEADER_TYPE_TAG_OFFSET, PY_TYPE_VALUEBOX)
    # py_instance_new already returned a new reference.
    return ptr_add(box, 0)


@c_abi_export("py_valuebox_get_field")
def py_valuebox_get_field(box, idx: int):
    return py_instance_get_field(box, idx)


@c_abi_export("py_valuebox_set_field")
def py_valuebox_set_field(box, idx: int, value) -> None:
    py_instance_set_field(box, idx, value)


@c_abi_export("py_instance_getattr_default")
def py_instance_getattr_default(inst, name):
    if not _ptr_is_instance(inst):
        return null()
    if ptr_is_null(name) != 0:
        return null()
    cls = pcc_gc_load_ptr(inst, ptr_add(inst, PYINSTANCEOBJECT_CLS_OFFSET))
    return _instance_getattr_default(inst, cls, name)


def _instance_getattr_default(inst, cls, name):
    return _instance_getattr_default_body(inst, cls, name, null(), null())


def _instance_lookup_hold(slots, pins, index: int, value, borrowed: int):
    """Keep one slow-lookup temporary current and owned across callbacks.

    Each slot is assigned once. Inputs are already protected by the missing
    field wrapper. Native method entries are not managed objects or owners.
    """
    if ptr_is_null(slots) != 0:
        return value
    slot = ptr_add(slots, index * C_POINTER_SIZE)
    store_ptr(slot, 0, value)
    store_i64(pins, index * C_POINTER_SIZE, -1)
    pcc_py_gc_minor_graph_lock()
    value = pcc_gc_load_ptr(null(), slot)
    store_ptr(slot, 0, value)
    if ptr_is_null(value) == 0 and is_tagged_int(value) == 0:
        if _ptr_can_have_header(value):
            prior: int = load_i32(value, PYOBJECTHEADER_FLAGS_OFFSET) & 64
            other: int = 0
            while other < 8:
                if other != index and ptr_eq(value, load_ptr(slots, other * C_POINTER_SIZE)) != 0:
                    other_pin: int = load_i64(pins, other * C_POINTER_SIZE)
                    if other_pin >= 0:
                        prior = prior & other_pin
                other = other + 1
            store_i64(pins, index * C_POINTER_SIZE, prior)
            pcc_gc_pin(value)
            if borrowed != 0:
                py_incref(value)
    pcc_py_gc_minor_graph_unlock()
    return value


def _instance_lookup_release(slots, pins, index: int) -> None:
    slot = ptr_add(slots, index * C_POINTER_SIZE)
    value = load_ptr(slot, 0)
    if ptr_is_null(value) != 0:
        return
    prior_pin: int = load_i64(pins, index * C_POINTER_SIZE)
    if ptr_is_null(value) == 0 and is_tagged_int(value) == 0:
        if prior_pin < 0:
            # A raw native method pointer has no refcount or pin lease.
            store_ptr(slot, 0, null())
            return
        other: int = 0
        while other < 8:
            if other != index and ptr_eq(value, load_ptr(slots, other * C_POINTER_SIZE)) != 0:
                if load_i64(pins, other * C_POINTER_SIZE) >= 0:
                    prior_pin = prior_pin | 64
            other = other + 1
    # This strict primitive clears the root and balances exactly one lease
    # without a park before decref takes over ownership of the raw argument.
    py_decref(pcc_gc_take_pinned_slot(slot, prior_pin))


def _instance_getattr_default_rooted(inst, cls, name, custom_lookup: int):
    # result, class attribute, dynamic dict, dict key, method, callback key,
    # callback arguments, saved error. Input leases belong to the caller.
    slots = stack_alloc(8 * C_POINTER_SIZE)
    pins = stack_alloc(8 * C_POINTER_SIZE)
    handles = stack_alloc(8 * C_POINTER_SIZE)
    memset(slots, 0, 8 * C_POINTER_SIZE)
    memset(pins, 0, 8 * C_POINTER_SIZE)
    memset(handles, 0, 8 * C_POINTER_SIZE)
    count: int = 0
    while count < 8:
        handle = pcc_gc_scheduler_root_register_handle(ptr_add(slots, count * C_POINTER_SIZE))
        if ptr_is_null(handle) != 0:
            break
        store_ptr(handles, count * C_POINTER_SIZE, handle)
        count = count + 1
    if count == 8:
        if custom_lookup != 0:
            result = _instance_getattr_custom_body(inst, cls, name, slots, pins)
        else:
            result = _instance_getattr_default_body(inst, cls, name, slots, pins)
        _instance_lookup_hold(slots, pins, 0, result, 0)
    prior_result_pin: int = load_i64(pins, 0)
    index: int = 7
    while index > 0:
        if ptr_eq(load_ptr(slots, 0), load_ptr(slots, index * C_POINTER_SIZE)) != 0:
            prior_result_pin = load_i64(pins, index * C_POINTER_SIZE)
        index = index - 1
    saved_error_index: int = 7
    saved_error_offset: int = saved_error_index * C_POINTER_SIZE
    if py_err_occurred() != 0:
        _instance_lookup_hold(slots, pins, saved_error_index, py_current_exception(), 1)
    # Release every owner once, keeping any remaining aliased lease pinned.
    # The result and original exception survive arbitrary finalizer callbacks.
    index = 6
    while index > 0:
        _instance_lookup_release(slots, pins, index)
        index = index - 1
    if ptr_is_null(load_ptr(slots, saved_error_offset)) == 0:
        py_raise(load_ptr(slots, saved_error_offset))
        _instance_lookup_release(slots, pins, saved_error_index)
    index = 0
    while index < count:
        pcc_gc_scheduler_root_unregister_handle(load_ptr(handles, index * C_POINTER_SIZE))
        index = index + 1
    if count != 8:
        return _class_require_result(null(), cstr("instance field lookup"),
                                     cstr("instance field temporary root registration failed"))
    return pcc_gc_take_pinned_slot(slots, prior_result_pin)


def _instance_lookup_descriptor(descriptor, inst, cls, slots, pins):
    if ptr_is_null(slots) != 0:
        return _descriptor_call_get(descriptor, inst, cls)
    if ptr_is_null(descriptor) != 0 or is_tagged_int(descriptor) != 0:
        return null()
    tag: int = load_i32(descriptor, PYOBJECTHEADER_TYPE_TAG_OFFSET)
    if tag == PY_TYPE_STATICMETHOD:
        value = pcc_gc_load_ptr(descriptor, ptr_add(descriptor, PYSTATICMETHODOBJECT_FUNC_OFFSET))
        py_incref(value)
        return value
    count: int = 3
    method = null()
    if tag == PY_TYPE_PROPERTY:
        method = pcc_gc_load_ptr(descriptor, ptr_add(descriptor, PYPROPERTYOBJECT_FGET_OFFSET))
        if ptr_is_null(method) != 0:
            py_raise_owned(py_exc_new(6, cstr("unreadable attribute")))
            return null()
        if ptr_eq(inst, global_load_ptr("py_None")) != 0:
            py_incref(descriptor)
            return descriptor
        count = 1
    else:
        method = _descriptor_method(descriptor, cstr("__get__"))
        if ptr_is_null(method) != 0:
            return null()
    method = _instance_lookup_hold(slots, pins, 4, method, 1)
    args = _instance_lookup_hold(slots, pins, 6, py_tuple_new(count), 0)
    if ptr_is_null(args) != 0:
        return _class_require_result(null(), cstr("py_tuple_new"),
                                     cstr("class callback argument tuple allocation failed"))
    if count == 1:
        py_tuple_set_item(args, 0, inst)
    else:
        py_tuple_set_item(args, 0, descriptor)
        if py_err_occurred() != 0:
            return null()
        py_tuple_set_item(args, 1, inst)
        if py_err_occurred() != 0:
            return null()
        py_tuple_set_item(args, 2, cls)
    if py_err_occurred() != 0:
        return null()
    return _class_require_result(
        py_obj_call(method, args, global_load_ptr("py_None")),
        cstr("descriptor __get__"),
        cstr("class callback returned NULL without setting an exception"),
    )


def _instance_lookup_call(method, inst, key, slots, pins, args_index: int):
    if _ptr_can_have_header(method):
        if load_i32(method, PYOBJECTHEADER_TYPE_TAG_OFFSET) == PY_TYPE_FUNC:
            args = _instance_lookup_hold(slots, pins, args_index, py_tuple_new(2), 0)
            if ptr_is_null(args) != 0:
                return _class_require_result(null(), cstr("py_tuple_new"),
                                             cstr("class callback argument tuple allocation failed"))
            py_tuple_set_item(args, 0, inst)
            if py_err_occurred() != 0:
                return null()
            py_tuple_set_item(args, 1, key)
            if py_err_occurred() != 0:
                return null()
            return py_obj_call(method, args, null())
    return call_ptr2(method, inst, key)


def _instance_getattr_custom_body(inst, cls, name, slots, pins):
    # Empty-field custom lookup uses the same temporary ownership frame as
    # default lookup. Function objects and native method pointers are distinct.
    method = _instance_lookup_hold(
        slots, pins, 4, _class_lookup_in_mro(cls, cstr("__getattribute__")), 1,
    )
    if py_err_occurred() != 0:
        return null()
    if ptr_is_null(method) != 0:
        return _instance_getattr_default_body(inst, cls, name, slots, pins)
    key = _instance_lookup_hold(slots, pins, 5, py_str_new(name, strlen(name)), 0)
    if ptr_is_null(key) != 0:
        return _class_require_result(null(), cstr("py_str_new"),
                                     cstr("instance attribute key allocation failed"))
    got = _instance_lookup_call(method, inst, key, slots, pins, 6)
    _class_require_result(got, cstr("__getattribute__"),
                          cstr("class callback returned NULL without setting an exception"))
    if ptr_is_null(got) == 0:
        return got
    if py_err_occurred() != 0:
        current = _instance_lookup_hold(slots, pins, 1, py_current_exception(), 1)
        attr_cls = py_exc_builtin_class(6)
        if ptr_is_null(attr_cls) == 0:
            if py_exc_matches(current, attr_cls) != 0:
                fallback = _instance_lookup_hold(
                    slots, pins, 2, _class_lookup_in_mro(cls, cstr("__getattr__")), 1,
                )
                if ptr_is_null(fallback) == 0:
                    py_clear_exception()
                    got = _instance_lookup_call(fallback, inst, key, slots, pins, 3)
                    return _class_require_result(
                        got, cstr("__getattr__"),
                        cstr("class callback returned NULL without setting an exception"),
                    )
    return null()


def _instance_getattr_default_body(inst, cls, name, slots, pins):
    """`py_instance_getattr_default` for an instance the caller validated,
    with its class `cls` and a non-NULL `name`."""
    # `__class__` and `__dict__` return below before any outcome is recorded,
    # so a cached entry can never answer for them.
    field_fallback_rooted: int = 0
    if ptr_is_null(slots) == 0:
        field_fallback_rooted = 1
    entry = _attr_res_find_any(cls, name)
    if field_fallback_rooted != 0:
        # Resolve mutable descriptors afresh on the uncommon empty-slot path.
        entry = null()
    # A generic lookup can reach an empty field without the indexed getter.
    # Protect its newly exposed allocating/callback path, while keeping the
    # populated field-cache hit constant-time. The rooted helper enters this
    # body with the guard disabled rather than recursively looking up itself.
    if field_fallback_rooted == 0:
        if ptr_is_null(entry) != 0 or load_i32(entry, 12) == _ATTR_RES_DATA_DESCRIPTOR:
            empty_index: int = _lookup_field_index(cls, name)
            if empty_index >= 0:
                inst = pcc_gc_note_relocation_read(inst)
                empty_value = pcc_gc_load_ptr(inst, ptr_add(inst, PYINSTANCEOBJECT_FIELDS_OFFSET + empty_index * C_POINTER_SIZE))
                if ptr_is_null(empty_value) != 0:
                    return _instance_missing_field_lookup(inst, empty_index, 1)
    if ptr_is_null(entry) == 0:
        kind: int = load_i32(entry, 12)
        if kind == _ATTR_RES_FIELD:
            fields_base_hit = ptr_add(inst, PYINSTANCEOBJECT_FIELDS_OFFSET)
            field_hit = pcc_gc_load_ptr(
                inst, ptr_add(fields_base_hit, load_i64(entry, 16) * C_POINTER_SIZE)
            )
            if ptr_is_null(field_hit) == 0:
                py_incref(field_hit)
                return field_hit
            if field_fallback_rooted == 0:
                return _instance_missing_field_lookup(inst, load_i64(entry, 16), 1)
        if kind == _ATTR_RES_METHOD:
            if ptr_is_null(_instance_dict_of(inst, cls)) != 0:
                return py_instance_bind_method(load_ptr(entry, 16), inst, name)
        if kind == _ATTR_RES_ABSENT:
            return _instance_dict_attr(inst, cls, name)
        if kind == _ATTR_RES_DATA_DESCRIPTOR:
            got_hit = _descriptor_call_get(load_ptr(entry, 16), inst, cls)
            if ptr_is_null(got_hit) == 0:
                return got_hit
            if py_err_occurred() != 0:
                return null()
    outcome_epoch: int = _class_attr_cache_epoch()
    if _cstr_is_dunder_class(name) != 0:
        if ptr_is_null(cls) == 0:
            py_incref(cls)
        return cls
    if _cstr_is_dunder_dict(name) != 0:
        dyn_slot = _dynamic_attr_slot(inst)
        if ptr_is_null(dyn_slot) != 0:
            return null()
        dyn = pcc_gc_load_ptr(inst, dyn_slot)
        if ptr_is_null(dyn) != 0:
            dyn = py_dict_new()
            if ptr_is_null(dyn) != 0:
                return null()
            dyn = _instance_lookup_hold(slots, pins, 2, dyn, 0)
            pcc_gc_store_ptr(inst, dyn_slot, dyn)
            if field_fallback_rooted == 0:
                py_decref(dyn)
        else:
            dyn = _instance_lookup_hold(slots, pins, 2, dyn, 1)
        py_incref(dyn)
        return dyn
    class_attr = _instance_lookup_hold(
        slots, pins, 1, _class_attr_lookup_in_mro(cls, name), 0,
    )
    if py_err_occurred() != 0:
        return null()
    if ptr_is_null(class_attr) == 0:
        if _descriptor_is_data(class_attr):
            if _attr_res_method_cacheable() != 0:
                _attr_res_store(
                    cls, name, _ATTR_RES_DATA_DESCRIPTOR,
                    ptr_to_int(class_attr), outcome_epoch,
                )
            got = _instance_lookup_descriptor(class_attr, inst, cls, slots, pins)
            if ptr_is_null(got) == 0:
                if field_fallback_rooted == 0:
                    py_decref(class_attr)
                return got
            if py_err_occurred() != 0:
                if field_fallback_rooted == 0:
                    py_decref(class_attr)
                return null()
    idx: int = _lookup_field_index(cls, name)
    if idx >= 0:
        # Reached only when no data descriptor shadows the field.
        _attr_res_store(cls, name, _ATTR_RES_FIELD, idx, outcome_epoch)
        fields_base = ptr_add(inst, PYINSTANCEOBJECT_FIELDS_OFFSET)
        v = pcc_gc_load_ptr(inst, ptr_add(fields_base, idx * C_POINTER_SIZE))
        if ptr_is_null(v) == 0:
            py_incref(v)
            if field_fallback_rooted == 0:
                py_decref(class_attr)
            return v
    # A method outcome holds only while the instance has no dynamic dict.
    method_cacheable: int = _attr_res_method_cacheable()
    if idx >= 0:
        # A later direct slot store must immediately shadow a callable class
        # default; a cached method outcome cannot observe that store.
        method_cacheable = 0
    dyn_slot = _dynamic_attr_slot(inst)
    if ptr_is_null(dyn_slot) == 0:
        dyn = pcc_gc_load_ptr(inst, dyn_slot)
        if ptr_is_null(dyn) == 0:
            method_cacheable = 0
            dyn = _instance_lookup_hold(slots, pins, 2, dyn, 1)
            key = _instance_lookup_hold(slots, pins, 3, py_str_new(name, strlen(name)), 0)
            if ptr_is_null(key) != 0:
                return _class_require_result(null(), cstr("py_str_new"),
                                             cstr("instance attribute key allocation failed"))
            got = py_dict_get(dyn, key)
            if field_fallback_rooted == 0:
                py_decref(key)
            if ptr_is_null(got) == 0:
                if field_fallback_rooted == 0:
                    py_decref(class_attr)
                return got
            if py_err_occurred() != 0:
                if field_fallback_rooted == 0:
                    py_decref(class_attr)
                return null()
    if ptr_is_null(class_attr) == 0:
        if is_tagged_int(class_attr) == 0:
            if load_i32(class_attr, PYOBJECTHEADER_TYPE_TAG_OFFSET) == PY_TYPE_FUNC:
                if method_cacheable != 0:
                    _attr_res_store(
                        cls, name, _ATTR_RES_METHOD, ptr_to_int(class_attr),
                        outcome_epoch,
                    )
                bound = py_instance_bind_method(class_attr, inst, name)
                if field_fallback_rooted == 0:
                    py_decref(class_attr)
                return bound
        got = _instance_lookup_descriptor(class_attr, inst, cls, slots, pins)
        if ptr_is_null(got) == 0:
            if field_fallback_rooted == 0:
                py_decref(class_attr)
            return got
        if py_err_occurred() != 0:
            if field_fallback_rooted == 0:
                py_decref(class_attr)
            return null()
        if field_fallback_rooted != 0:
            # The temporary frame owns the lookup reference until cleanup.
            py_incref(class_attr)
        return class_attr
    if ptr_is_null(cls) != 0:
        return null()
    method = _instance_lookup_hold(slots, pins, 4, _class_lookup_in_mro(cls, name), 1)
    if py_err_occurred() != 0:
        return null()
    if ptr_is_null(method) == 0:
        # Method tables may hold raw entry points; only function objects are
        # recorded, so a cached method is always safe to call directly.
        if method_cacheable != 0 and _ptr_can_have_header(method) and load_i32(
            method, PYOBJECTHEADER_TYPE_TAG_OFFSET
        ) == PY_TYPE_FUNC:
            _attr_res_store(
                cls, name, _ATTR_RES_METHOD, ptr_to_int(method), outcome_epoch
            )
        return py_instance_bind_method(method, inst, name)
    # dict-subclass inherited method fallback (get / keys / values / items /
    # pop / setdefault / clear). User methods/attrs above win; this only fires
    # for names the user class did not define. PY_CLASS_FLAG_DICT_SUBCLASS is
    # bit 2 (value 4) in the class header flags at offset 12. Returns a bound
    # native callable, or NULL -> fall through to __getattr__ / AttributeError.
    ds_flags: int = load_i32(cls, PYOBJECTHEADER_FLAGS_OFFSET)
    if (ds_flags & 4) != 0:
        dm = py_dict_subclass_getattr(inst, name)
        if ptr_is_null(dm) == 0:
            return dm
        if py_err_occurred() != 0:
            return null()
    getattr_method = _instance_lookup_hold(
        slots, pins, 4, _class_lookup_in_mro(cls, cstr("__getattr__")), 1,
    )
    if py_err_occurred() != 0:
        return null()
    if ptr_is_null(getattr_method) != 0:
        if (ds_flags & 4) == 0 and idx < 0:
            # No class attribute, field or method answered and the instance
            # dict (checked above) did not either.
            _attr_res_store(cls, name, _ATTR_RES_ABSENT, 0, outcome_epoch)
        return null()
    key = _instance_lookup_hold(slots, pins, 5, py_str_new(name, strlen(name)), 0)
    if ptr_is_null(key) != 0:
        return null()
    # The method-table slot for a compiled __getattr__ holds a PY_TYPE_FUNC
    # object, not a raw code pointer; invoke it via py_obj_call in that case.
    # call_ptr2 alone treats the object as a code address and crashes. Mirrors
    # class_call_binary_method in py_class.c.
    if _ptr_can_have_header(getattr_method):
        if load_i32(getattr_method, PYOBJECTHEADER_TYPE_TAG_OFFSET) == PY_TYPE_FUNC:
            gargs = _instance_lookup_hold(slots, pins, 6, py_tuple_new(2), 0)
            if ptr_is_null(gargs) != 0:
                _class_require_result(
                    null(),
                    cstr("py_tuple_new"),
                    cstr("class callback argument tuple allocation failed"),
                )
                if field_fallback_rooted == 0:
                    py_decref(key)
                return null()
            py_tuple_set_item(gargs, 0, inst)
            if py_err_occurred() != 0:
                if field_fallback_rooted == 0:
                    py_decref(gargs)
                    py_decref(key)
                return null()
            py_tuple_set_item(gargs, 1, key)
            if py_err_occurred() != 0:
                if field_fallback_rooted == 0:
                    py_decref(gargs)
                    py_decref(key)
                return null()
            got = py_obj_call(getattr_method, gargs, null())
            _class_require_result(
                got,
                cstr("__getattr__"),
                cstr("class callback returned NULL without setting an exception"),
            )
            if field_fallback_rooted == 0:
                py_decref(gargs)
                py_decref(key)
            return got
    got = call_ptr2(getattr_method, inst, key)
    _class_require_result(
        got,
        cstr("__getattr__"),
        cstr("class callback returned NULL without setting an exception"),
    )
    if field_fallback_rooted == 0:
        py_decref(key)
    return got


@c_abi_export("py_instance_getattr")
def py_instance_getattr(inst, name):
    if not _ptr_is_instance(inst):
        return null()
    if ptr_is_null(name) != 0:
        return null()
    cls = pcc_gc_load_ptr(inst, ptr_add(inst, PYINSTANCEOBJECT_CLS_OFFSET))
    if ptr_is_null(cls) != 0:
        return null()
    if _no_getattribute_known(cls) != 0:
        return _instance_getattr_default(inst, cls, name)
    getattribute_name = cstr("__getattribute__")
    probe_epoch: int = _class_attr_cache_epoch()
    getattribute_method = _class_lookup_in_mro(cls, getattribute_name)
    if ptr_is_null(getattribute_method) != 0:
        _no_getattribute_store(cls, probe_epoch)
    if ptr_is_null(getattribute_method) == 0:
        # Method-table entries can be native code pointers or PY_TYPE_FUNC
        # objects. The shared callback path distinguishes their ABIs and
        # protects the receiver, callback, key, result and exception owners.
        return _instance_lookup_rooted(inst, -1, name, 2)
    return _instance_getattr_default(inst, cls, name)


@c_abi_export("py_instance_setattr")
def py_instance_setattr(inst, name, value) -> int:
    if not _ptr_is_instance(inst):
        return -1
    if ptr_is_null(name) != 0:
        return -1
    cls = pcc_gc_load_ptr(inst, ptr_add(inst, PYINSTANCEOBJECT_CLS_OFFSET))
    # A FIELD outcome proves no data descriptor shadows the field, so the
    # store goes straight to its slot, as the full walk below would decide.
    entry = _attr_res_find_any(cls, name)
    if ptr_is_null(entry) == 0 and load_i32(entry, 12) == _ATTR_RES_FIELD:
        pcc_gc_store_ptr(
            inst,
            ptr_add(
                inst,
                PYINSTANCEOBJECT_FIELDS_OFFSET + load_i64(entry, 16) * C_POINTER_SIZE,
            ),
            value,
        )
        return 0
    outcome_epoch: int = _class_attr_cache_epoch()
    class_attr = _class_attr_lookup_in_mro(cls, name)
    if ptr_is_null(class_attr) == 0:
        # A property is a data descriptor whether or not it has a setter;
        # _descriptor_method only sees instance descriptors, so a property
        # store used to land in the instance dict, unseen by its getter.
        if _class_attr_is_property(class_attr) or ptr_is_null(
            _descriptor_method(class_attr, cstr("__set__"))
        ) == 0:
            rc: int = _descriptor_call_set(class_attr, inst, value)
            py_decref(class_attr)
            return rc
        py_decref(class_attr)
    idx: int = _lookup_field_index(cls, name)
    if idx >= 0:
        n_fields: int = load_i32(cls, PYCLASSOBJECT_N_FIELDS_OFFSET)
        if idx >= n_fields:
            return -1
        if ptr_is_null(class_attr) != 0:
            # No class attribute at all, so no data descriptor: the same
            # outcome the read path records for this field.
            _attr_res_store(cls, name, _ATTR_RES_FIELD, idx, outcome_epoch)
        fields_base = ptr_add(inst, PYINSTANCEOBJECT_FIELDS_OFFSET)
        pcc_gc_store_ptr(
            inst,
            ptr_add(fields_base, idx * C_POINTER_SIZE),
            value,
        )
        return 0
    if ptr_is_null(value) != 0:
        return -1
    dyn_slot = _dynamic_attr_slot(inst)
    if ptr_is_null(dyn_slot) != 0:
        return -1
    dyn = pcc_gc_load_ptr(inst, dyn_slot)
    if ptr_is_null(dyn) != 0:
        dyn = py_dict_new()
        if ptr_is_null(dyn) != 0:
            return -1
        pcc_gc_store_ptr(inst, dyn_slot, dyn)
        py_decref(dyn)
    key = py_str_new(name, strlen(name))
    py_dict_set(dyn, key, value)
    py_decref(key)
    return 0


@c_abi_export("py_instance_delattr")
def py_instance_delattr(inst, name) -> int:
    if not _ptr_is_instance(inst):
        return -1
    if ptr_is_null(name) != 0:
        return -1
    cls = pcc_gc_load_ptr(inst, ptr_add(inst, PYINSTANCEOBJECT_CLS_OFFSET))
    class_attr = _class_attr_lookup_in_mro(cls, name)
    if ptr_is_null(class_attr) == 0:
        if _class_attr_is_property(class_attr) or ptr_is_null(
            _descriptor_method(class_attr, cstr("__delete__"))
        ) == 0:
            rc: int = _descriptor_call_delete(class_attr, inst)
            py_decref(class_attr)
            return rc
        py_decref(class_attr)
    idx: int = _lookup_field_index(cls, name)
    if idx >= 0:
        n_fields: int = load_i32(cls, PYCLASSOBJECT_N_FIELDS_OFFSET)
        if idx >= n_fields:
            return -1
        fields_base = ptr_add(inst, PYINSTANCEOBJECT_FIELDS_OFFSET)
        old = pcc_gc_load_ptr(
            inst,
            ptr_add(fields_base, idx * C_POINTER_SIZE),
        )
        if ptr_is_null(old) != 0:
            return -1
        store_ptr(fields_base, idx * C_POINTER_SIZE, null())
        py_decref(old)
        return 0
    dyn_slot = _dynamic_attr_slot(inst)
    if ptr_is_null(dyn_slot) != 0:
        return -1
    dyn = pcc_gc_load_ptr(inst, dyn_slot)
    if ptr_is_null(dyn) != 0:
        return -1
    key = py_str_new(name, strlen(name))
    rc: int = py_dict_del(dyn, key)
    py_decref(key)
    return rc


@c_abi_export("py_class_apply_namespace_dict")
def py_class_apply_namespace_dict(cls, ns) -> int:
    if not _ptr_is_class(cls):
        return -1
    if ptr_is_null(ns) != 0:
        py_raise_owned(py_exc_new(3, cstr("type.__new__() argument 3 must be dict")))
        return -1
    if load_i32(ns, PYOBJECTHEADER_TYPE_TAG_OFFSET) != PY_TYPE_DICT:
        py_raise_owned(py_exc_new(3, cstr("type.__new__() argument 3 must be dict")))
        return -1
    keys = py_dict_keys(ns)
    if ptr_is_null(keys) != 0:
        return -1
    n: int = py_list_len(keys)
    i: int = 0
    while i < n:
        key = py_list_get(keys, i)
        if ptr_is_null(key) != 0:
            py_decref(keys)
            return -1
        name = py_str_utf8(key)
        if ptr_is_null(name) != 0:
            py_decref(key)
            py_decref(keys)
            return -1
        value = py_dict_get(ns, key)
        if ptr_is_null(value) != 0:
            py_decref(key)
            py_decref(keys)
            return -1
        rc: int = py_class_setattr_raw(cls, name, value)
        py_decref(value)
        py_decref(key)
        if rc != 0:
            py_decref(keys)
            return rc
        i = i + 1
    py_decref(keys)
    return 0


@c_abi_export("py_isinstance")
def py_isinstance(obj, cls) -> int:
    obj = pcc_gc_note_relocation_read(obj)
    cls = pcc_gc_note_relocation_read(cls)
    # One provenance probe, for `obj`.  `cls` is only compared by address with
    # the validated class of `obj` and its MRO entries, all real classes, so
    # an argument that is not a class never matches -- the answer the former
    # `_ptr_is_class(cls)` probe gave up front.  `_ptr_is_instance` probed
    # `obj` a second time.
    if not _ptr_can_have_header(obj):
        return 0
    tag: int = load_i32(obj, PYOBJECTHEADER_TYPE_TAG_OFFSET)
    if tag == PY_TYPE_EXC:
        # Exception instances match through their exception-class MRO.
        if py_exc_matches(obj, cls) != 0:
            return 1
        return 0
    if tag != PY_TYPE_INSTANCE and tag < PY_TYPE_USER_CLASS_START:
        return 0
    obj_cls = pcc_gc_load_ptr(obj, ptr_add(obj, PYINSTANCEOBJECT_CLS_OFFSET))
    if ptr_is_null(obj_cls) != 0:
        return 0
    if not _ptr_is_class_of_validated_instance(obj_cls):
        return 0
    if ptr_eq(obj_cls, cls) != 0:
        return 1
    n_mro: int = load_i32(obj_cls, PYCLASSOBJECT_N_MRO_OFFSET)
    mro = load_ptr(obj_cls, PYCLASSOBJECT_MRO_OFFSET)
    i: int = 0
    while i < n_mro:
        m = pcc_gc_load_ptr(obj_cls, ptr_add(mro, i * C_POINTER_SIZE))
        if ptr_eq(m, cls) != 0:
            return 1
        i = i + 1
    return 0


@c_abi_export("py_super_lookup")
def py_super_lookup(start_cls, from_cls, name):
    if not _ptr_is_class(start_cls):
        return null()
    if not _ptr_is_class(from_cls):
        return null()
    if ptr_is_null(name) != 0:
        return null()
    start_cls = pcc_gc_note_relocation_read(start_cls)
    from_cls = pcc_gc_note_relocation_read(from_cls)
    n_mro: int = load_i32(start_cls, PYCLASSOBJECT_N_MRO_OFFSET)
    mro = load_ptr(start_cls, PYCLASSOBJECT_MRO_OFFSET)
    start: int = -1
    i: int = 0
    while i < n_mro:
        m = pcc_gc_load_ptr(start_cls, ptr_add(mro, i * C_POINTER_SIZE))
        if ptr_eq(m, from_cls) != 0:
            start = i
            i = n_mro  # force-exit
        i = i + 1
    if start < 0:
        exc = py_exc_new(
            3,
            cstr("super(type, obj): obj must be an instance or subtype of type"),
        )
        py_raise_owned(exc)
        return null()

    j: int = start + 1
    while j < n_mro:
        m = pcc_gc_load_ptr(start_cls, ptr_add(mro, j * C_POINTER_SIZE))
        if ptr_is_null(m) == 0:
            n_methods: int = load_i32(m, PYCLASSOBJECT_N_METHODS_OFFSET)
            methods = load_ptr(m, PYCLASSOBJECT_METHODS_OFFSET)
            k: int = 0
            while k < n_methods:
                m_off: int = k * PYCLASSMETHOD_SIZE
                m_name = load_ptr(methods, m_off + PYCLASSMETHOD_NAME_OFFSET)
                if _strs_eq(m_name, name) != 0:
                    method_slot = ptr_add(
                        methods,
                        m_off + PYCLASSMETHOD_FUNC_OFFSET,
                    )
                    func = pcc_gc_note_relocation_read(load_ptr(method_slot, 0))
                    store_ptr(method_slot, 0, func)
                    return func
                k = k + 1
        j = j + 1
    exc = py_exc_new(6, cstr("super object has no attribute"))
    py_raise_owned(exc)
    return null()


pcc_class_retire_metaclass = extern("pcc_class_retire_metaclass", (c_ptr,), c_void)
pcc_class_abort_definition_slots = extern("pcc_class_abort_definition_slots", (c_ptr,), c_void)
define_global_i32("pcc_class_definition_abort_error_map", 1)


@c_abi_export("py_class_abort_definition_slots")
def py_class_abort_definition_slots(class_slot: c_ptr) -> None:
    # Keep exception ownership in the semantic layer. The freestanding entry
    # owns only graph detach and deferred disposal; class_slot is the caller's
    # still-registered root, never a borrowed managed address.
    error = stack_alloc(C_POINTER_SIZE)
    store_ptr(error, 0, null())
    pcc_gc_frame_enter(global_addr("pcc_class_definition_abort_error_map"), error)
    py_tls_exc_swap_slot(error)
    pcc_class_abort_definition_slots(class_slot)
    py_clear_exception()
    py_tls_exc_swap_slot(error)
    pcc_gc_frame_leave(error)


@c_abi_export("py_class_dealloc")
def py_class_dealloc(o) -> None:
    if ptr_is_null(o) != 0:
        return
    attrs = pcc_gc_load_ptr(o, ptr_add(o, PYCLASSOBJECT_ATTRS_OFFSET))
    if ptr_is_null(attrs) == 0:
        store_ptr(o, PYCLASSOBJECT_ATTRS_OFFSET, null())
        py_decref(attrs)
    bases = load_ptr(o, PYCLASSOBJECT_BASES_OFFSET)
    if ptr_is_null(bases) == 0:
        free(bases)
    mro = load_ptr(o, PYCLASSOBJECT_MRO_OFFSET)
    if ptr_is_null(mro) == 0:
        free(mro)
    methods = load_ptr(o, PYCLASSOBJECT_METHODS_OFFSET)
    if ptr_is_null(methods) == 0:
        free(methods)
    field_names = load_ptr(o, PYCLASSOBJECT_FIELD_NAMES_OFFSET)
    if ptr_is_null(field_names) == 0:
        free(field_names)
    # Attribute outcomes are keyed by this address; retire them before a new
    # class can be allocated there or relation cleanup can reenter lookup.
    # The counted relation stays in its authoritative slot across this call.
    _bump_class_attr_cache_epoch()
    # Retire the relation through the existing owning-slot transaction. Its
    # commit detaches before deferred cleanup, without an unrooted raw value
    # spanning a safepoint in the epoch or decref helpers.
    pcc_class_retire_metaclass(o)
    pcc_gc_free_object_memory(o)


def _descriptor_release_slot(owner, offset: int) -> None:
    slot = ptr_add(owner, offset)
    value = pcc_gc_load_ptr(owner, slot)
    store_ptr(owner, offset, null())
    if ptr_is_null(value) == 0:
        py_decref(value)


@c_abi_export("py_descriptor_dealloc")
def py_descriptor_dealloc(o) -> None:
    if ptr_is_null(o) != 0:
        return
    tag: int = load_i32(o, PYOBJECTHEADER_TYPE_TAG_OFFSET)
    if tag == PY_TYPE_PROPERTY:
        _descriptor_release_slot(o, PYPROPERTYOBJECT_FGET_OFFSET)
        _descriptor_release_slot(o, PYPROPERTYOBJECT_FSET_OFFSET)
        _descriptor_release_slot(o, PYPROPERTYOBJECT_FDEL_OFFSET)
    elif tag == PY_TYPE_CLASSMETHOD:
        _descriptor_release_slot(o, PYCLASSMETHODOBJECT_FUNC_OFFSET)
    elif tag == PY_TYPE_STATICMETHOD:
        _descriptor_release_slot(o, PYSTATICMETHODOBJECT_FUNC_OFFSET)
    pcc_gc_free_object_memory(o)


@c_abi_export("py_instance_dealloc")
def py_instance_dealloc(o) -> None:
    if ptr_is_null(o) != 0:
        return
    py_user_del_dispatch(o)
    if load_i64(o, PYOBJECTHEADER_REFCOUNT_OFFSET) > 0:
        py_gc_track(o)
        backend: int = _gc_backend_selected_fast()
        metadata_valid: int = 1
        if backend == 3 or backend == 4:
            if pcc_gc_pointer_is_managed(o) == 0:
                metadata_valid = 0
            elif ptr_is_null(pcc_gc_object_index_find(o)) != 0:
                metadata_valid = 0
        if metadata_valid == 0:
            return
        flags: int = load_i32(o, PYOBJECTHEADER_FLAGS_OFFSET)
        store_i32(o, PYOBJECTHEADER_FLAGS_OFFSET, flags & ~524288)
        return
    # A resurrected instance keeps its live weakrefs. Only the terminal
    # path clears them, after __del__ and before dropping owned fields.
    py_weakref_invalidate(o)
    if _dealloc_ptr_is_instance(o):
        cls = pcc_gc_load_ptr(o, ptr_add(o, PYINSTANCEOBJECT_CLS_OFFSET))
        n_fields: int = load_i32(cls, PYCLASSOBJECT_N_FIELDS_OFFSET)
        fields_base = ptr_add(o, PYINSTANCEOBJECT_FIELDS_OFFSET)
        i: int = 0
        while i < n_fields:
            v = pcc_gc_load_ptr(
                o,
                ptr_add(fields_base, i * C_POINTER_SIZE),
            )
            if ptr_is_null(v) == 0:
                # Null the slot before decref so a finalizer that re-enters
                # py_instance_dealloc (its __del__ arg-tuple holds self and
                # drops self back to 0 on free) reads NULL and does not
                # double-release this field. Mirrors py_class_dealloc.
                store_ptr(fields_base, i * C_POINTER_SIZE, null())
                py_decref(v)
            i = i + 1
        # Detach before deferred decref/finalizers, including slots-only
        # protocol backing stores. The store owner resolves forwarding and
        # preserves the NULL-before-reentrant-cleanup invariant.
        pcc_gc_store_ptr(o, _instance_reserved_owner_slot(o, cls), null())
        payload_slot = _instance_builtin_payload_slot(o, cls)
        if ptr_is_null(payload_slot) == 0:
            pcc_gc_store_ptr(o, payload_slot, null())
    delayed_zpage_note: int = 0
    if _gc_backend_selected_fast() == 4:
        if (load_i32(o, PYOBJECTHEADER_FLAGS_OFFSET) & 65536) != 0:
            delayed_zpage_note = 1
    if delayed_zpage_note == 0:
        pcc_gc_note_object_freeing(o)
        py_gc_untrack(o)
        pcc_gc_free_object_memory(o)
    else:
        py_gc_untrack(o)
        pcc_gc_free_object_memory(o)
        if pcc_gc_pointer_is_managed(o) != 0:
            pcc_gc_note_object_freeing(o)


@c_abi_export("py_dataclass_replace")
def py_dataclass_replace(obj, n_overrides: int, names, values):
    if not _ptr_is_instance(obj):
        return null()
    cls = pcc_gc_load_ptr(obj, ptr_add(obj, PYINSTANCEOBJECT_CLS_OFFSET))
    dst = py_instance_new(cls)
    if ptr_is_null(dst) != 0:
        return null()

    n_fields: int = load_i32(cls, PYCLASSOBJECT_N_FIELDS_OFFSET)
    src_fields = ptr_add(obj, PYINSTANCEOBJECT_FIELDS_OFFSET)
    dst_fields = ptr_add(dst, PYINSTANCEOBJECT_FIELDS_OFFSET)
    i: int = 0
    while i < n_fields:
        v = pcc_gc_load_ptr(obj, ptr_add(src_fields, i * C_POINTER_SIZE))
        if ptr_is_null(v) == 0:
            py_incref(v)
            store_ptr(dst_fields, i * C_POINTER_SIZE, v)
        i = i + 1
    _copy_instance_reserved_owner(obj, dst, cls)
    py_instance_copy_builtin_payload(obj, dst)

    j: int = 0
    while j < n_overrides:
        name_ptr = null()
        if ptr_is_null(names) == 0:
            name_ptr = load_ptr(names, j * C_POINTER_SIZE)
        val_ptr = null()
        if ptr_is_null(values) == 0:
            val_ptr = load_ptr(values, j * C_POINTER_SIZE)
        idx: int = _lookup_field_index(cls, name_ptr)
        if idx < 0:
            py_decref(dst)
            return null()
        # Inline py_instance_set_field — avoid passing idx (which is
        # i64 here, but py_instance_set_field is i32 per ABI).
        if idx < n_fields:
            f_off: int = idx * C_POINTER_SIZE
            old = load_ptr(dst_fields, f_off)
            if ptr_is_null(val_ptr) == 0:
                py_incref(val_ptr)
            store_ptr(dst_fields, f_off, val_ptr)
            if ptr_is_null(old) == 0:
                py_decref(old)
        j = j + 1
    return dst


@c_abi_export("py_dataclass_replace_from_dict")
def py_dataclass_replace_from_dict(obj, overrides):
    if not _ptr_is_instance(obj):
        return null()
    if not _ptr_can_have_header(overrides):
        return null()
    if load_i32(overrides, PYOBJECTHEADER_TYPE_TAG_OFFSET) != PY_TYPE_DICT:
        return null()

    cls = pcc_gc_load_ptr(obj, ptr_add(obj, PYINSTANCEOBJECT_CLS_OFFSET))
    dst = py_instance_new(cls)
    if ptr_is_null(dst) != 0:
        return null()

    n_fields: int = load_i32(cls, PYCLASSOBJECT_N_FIELDS_OFFSET)
    src_fields = ptr_add(obj, PYINSTANCEOBJECT_FIELDS_OFFSET)
    dst_fields = ptr_add(dst, PYINSTANCEOBJECT_FIELDS_OFFSET)
    i: int = 0
    while i < n_fields:
        v = pcc_gc_load_ptr(obj, ptr_add(src_fields, i * C_POINTER_SIZE))
        if ptr_is_null(v) == 0:
            py_incref(v)
            store_ptr(dst_fields, i * C_POINTER_SIZE, v)
        i = i + 1

    _copy_instance_reserved_owner(obj, dst, cls)
    py_instance_copy_builtin_payload(obj, dst)

    entries = load_ptr(overrides, PYDICTOBJECT_ENTRIES_OFFSET)
    entries_used: int = load_i64(overrides, PYDICTOBJECT_ENTRIES_USED_OFFSET)
    j: int = 0
    while j < entries_used:
        ent_off: int = j * DICTENTRY_SIZE
        key = load_ptr(entries, ent_off + DICTENTRY_KEY_OFFSET)
        if ptr_is_null(key) == 0:
            val_ptr = load_ptr(entries, ent_off + DICTENTRY_VALUE_OFFSET)
            name_ptr = py_str_utf8(key)
            idx: int = _lookup_field_index(cls, name_ptr)
            if idx < 0:
                py_decref(dst)
                return null()
            if idx < n_fields:
                f_off: int = idx * C_POINTER_SIZE
                old = load_ptr(dst_fields, f_off)
                if ptr_is_null(val_ptr) == 0:
                    py_incref(val_ptr)
                store_ptr(dst_fields, f_off, val_ptr)
                if ptr_is_null(old) == 0:
                    py_decref(old)
        j = j + 1
    return dst


# C3 keeps the existing 16-byte MergeSeq head@8/len@12 state. Sequence
# values are owned managed tuple snapshots instead of raw MRO addresses;
# the reserved pointer word is never carried across a safepoint.


def _class_construct_input_roots(bases, count: int):
    if count <= 0:
        return null()
    if ptr_is_null(bases):
        return null()
    handles = malloc(count * C_POINTER_SIZE)
    if ptr_is_null(handles):
        return null()
    memset(handles, 0, count * C_POINTER_SIZE)
    pcc_py_gc_minor_graph_lock()
    index: int = 0
    while index < count:
        value = pcc_gc_note_relocation_read(load_ptr(bases, index * C_POINTER_SIZE))
        store_ptr(bases, index * C_POINTER_SIZE, value)
        handle = pcc_gc_scheduler_root_register_handle(ptr_add(bases, index * C_POINTER_SIZE))
        if ptr_is_null(handle):
            break
        store_ptr(handles, index * C_POINTER_SIZE, handle)
        index = index + 1
    pcc_py_gc_minor_graph_unlock()
    if index != count:
        _class_construct_input_leave(handles, count)
        return null()
    return handles


def _class_construct_input_leave(handles, count: int) -> None:
    if ptr_is_null(handles):
        return
    index: int = 0
    while index < count:
        handle = load_ptr(handles, index * C_POINTER_SIZE)
        if ptr_is_null(handle) == 0:
            pcc_gc_scheduler_root_unregister_handle(handle)
        index = index + 1
    free(handles)


def _class_construct_tuple_set(roots, target_offset: int, values, value_offset: int, index: int) -> None:
    pcc_py_gc_minor_graph_lock()
    target = pcc_gc_load_ptr(null(), ptr_add(roots, target_offset))
    value = pcc_gc_load_ptr(null(), ptr_add(values, value_offset))
    py_tuple_set_item(target, index, value)
    pcc_py_gc_minor_graph_unlock()


def _class_construct_base(roots, borrowed, index: int) -> None:
    pcc_py_gc_minor_graph_lock()
    bases = pcc_gc_load_ptr(null(), ptr_add(roots, 8))
    value = pcc_gc_load_ptr(bases, ptr_add(bases, 24 + index * C_POINTER_SIZE))
    store_ptr(borrowed, 0, value)
    pcc_py_gc_minor_graph_unlock()
    value = pcc_gc_note_relocation_read(pcc_gc_load_ptr(null(), borrowed))
    store_ptr(borrowed, 0, value)


def _class_construct_snapshot(roots, borrowed, count: int) -> None:
    # The owner supplies the current payload only inside the existing graph
    # lease. The resulting ordinary tuple owns each class entry independently
    # of a parent's movable raw MRO allocation.
    pcc_py_gc_minor_graph_lock()
    base = pcc_gc_load_ptr(null(), borrowed)
    payload = load_ptr(base, PYCLASSOBJECT_MRO_OFFSET)
    snapshot = pcc_gc_load_ptr(null(), ptr_add(roots, 32))
    index: int = 0
    while index < count:
        value = pcc_gc_load_ptr(base, ptr_add(payload, index * C_POINTER_SIZE))
        py_tuple_set_item(snapshot, index, value)
        index = index + 1
    pcc_py_gc_minor_graph_unlock()


def _class_construct_pick(roots, borrowed, sequence: int, index: int) -> None:
    pcc_py_gc_minor_graph_lock()
    owners = pcc_gc_load_ptr(null(), ptr_add(roots, 16))
    items = pcc_gc_load_ptr(owners, ptr_add(owners, 24 + sequence * C_POINTER_SIZE))
    value = pcc_gc_load_ptr(items, ptr_add(items, 24 + index * C_POINTER_SIZE))
    store_ptr(borrowed, 0, value)
    pcc_py_gc_minor_graph_unlock()
    value = pcc_gc_note_relocation_read(pcc_gc_load_ptr(null(), borrowed))
    store_ptr(borrowed, 0, value)


def _class_construct_equal(roots, borrowed, sequence: int, index: int) -> int:
    pcc_py_gc_minor_graph_lock()
    owners = pcc_gc_load_ptr(null(), ptr_add(roots, 16))
    items = pcc_gc_load_ptr(owners, ptr_add(owners, 24 + sequence * C_POINTER_SIZE))
    value = pcc_gc_load_ptr(items, ptr_add(items, 24 + index * C_POINTER_SIZE))
    candidate = pcc_gc_load_ptr(null(), borrowed)
    result: int = ptr_eq(value, candidate)
    pcc_py_gc_minor_graph_unlock()
    return result


def _class_construct_finish(roots, borrowed, success: int):
    prior_pin: int = 0
    if success != 0:
        pcc_gc_publish_initialized(pcc_gc_load_ptr(null(), roots))
        result = pcc_gc_load_ptr(null(), roots)
        prior_pin = load_i32(result, PYOBJECTHEADER_FLAGS_OFFSET) & 64
        pcc_gc_pin(result)
    else:
        value = pcc_gc_load_ptr(null(), roots)
        if ptr_is_null(value) == 0:
            atomic_rmw_i32("and", value, PYOBJECTHEADER_FLAGS_OFFSET, ~PY_FLAG_IMMORTAL, "relaxed")
    store_ptr(borrowed, 0, null())
    store_ptr(borrowed, 8, null())
    index: int = 4
    while index > 0:
        pcc_gc_store_root(ptr_add(roots, index * C_POINTER_SIZE), null())
        index = index - 1
    if success == 0:
        pcc_gc_store_root(roots, null())
    pcc_gc_frame_leave(borrowed)
    pcc_gc_frame_leave(roots)
    if success == 0:
        return null()
    return pcc_gc_take_pinned_slot(roots, prior_pin)


@c_abi_export("py_class_new")
def py_class_new(name, bases, n_bases: int, field_names, n_fields: int):
    # Fixed frames reuse ordinary tuple/slot contracts; class and MergeSeq
    # public layouts are unchanged. Inputs remain borrowed until copied.
    if n_fields < 0:
        n_fields = 0
    handles = _class_construct_input_roots(bases, n_bases)
    roots = stack_alloc(40)
    memset(roots, 0, 40)
    pcc_gc_frame_enter(global_addr("pcc_class_construct_owned_frame_map"), roots)
    borrowed = stack_alloc(16)
    memset(borrowed, 0, 16)
    pcc_gc_frame_enter(global_addr("pcc_class_construct_borrowed_frame_map"), borrowed)
    if n_bases > 0 and ptr_is_null(handles):
        return _class_construct_finish(roots, borrowed, 0)

    c = pcc_gc_alloc(PYCLASSOBJECT_SIZE, PY_TYPE_CLASS, 0)
    store_ptr(roots, 0, c)
    if ptr_is_null(c):
        _class_construct_input_leave(handles, n_bases)
        return _class_construct_finish(roots, borrowed, 0)
    memset(ptr_add(c, PYCLASSOBJECT_NAME_OFFSET), 0, PYCLASSOBJECT_SIZE - PYCLASSOBJECT_NAME_OFFSET)
    store_i64(c, PYOBJECTHEADER_REFCOUNT_OFFSET, 1)
    store_i32(c, PYOBJECTHEADER_TYPE_TAG_OFFSET, PY_TYPE_CLASS)
    store_i32(c, PYOBJECTHEADER_FLAGS_OFFSET, load_i32(c, PYOBJECTHEADER_FLAGS_OFFSET) | PY_FLAG_IMMORTAL)
    store_ptr(c, PYCLASSOBJECT_NAME_OFFSET, name)
    store_i32(c, PYCLASSOBJECT_N_BASES_OFFSET, n_bases)
    store_i32(c, PYCLASSOBJECT_N_FIELDS_OFFSET, n_fields)

    owners = py_tuple_new(n_bases)
    store_ptr(roots, 8, owners)
    if ptr_is_null(owners):
        _class_construct_input_leave(handles, n_bases)
        return _class_construct_finish(roots, borrowed, 0)
    index: int = 0
    while index < n_bases:
        store_ptr(borrowed, 0, pcc_gc_load_ptr(null(), ptr_add(bases, index * C_POINTER_SIZE)))
        _class_construct_tuple_set(roots, 8, borrowed, 0, index)
        index = index + 1
    _class_construct_input_leave(handles, n_bases)

    if n_bases > 0:
        copied = malloc(n_bases * C_POINTER_SIZE)
        if ptr_is_null(copied):
            return _class_construct_finish(roots, borrowed, 0)
        pcc_py_gc_minor_graph_lock()
        c = pcc_gc_load_ptr(null(), roots)
        owners = pcc_gc_load_ptr(null(), ptr_add(roots, 8))
        index = 0
        while index < n_bases:
            store_ptr(copied, index * C_POINTER_SIZE, pcc_gc_load_ptr(owners, ptr_add(owners, 24 + index * C_POINTER_SIZE)))
            index = index + 1
        store_ptr(c, PYCLASSOBJECT_BASES_OFFSET, copied)
        pcc_gc_backend4_zpage_register_owner_payload_span(c, copied, n_bases * C_POINTER_SIZE)
        pcc_py_gc_minor_graph_unlock()
    if n_fields > 0 and ptr_is_null(field_names) == 0:
        copied_fields = malloc(n_fields * C_POINTER_SIZE)
        if ptr_is_null(copied_fields):
            return _class_construct_finish(roots, borrowed, 0)
        index = 0
        while index < n_fields:
            store_ptr(copied_fields, index * C_POINTER_SIZE, load_ptr(field_names, index * C_POINTER_SIZE))
            index = index + 1
        c = pcc_gc_load_ptr(null(), roots)
        store_ptr(c, PYCLASSOBJECT_FIELD_NAMES_OFFSET, copied_fields)
    user_tag: int = _alloc_user_tag()
    if user_tag < PY_TYPE_NONE:
        _class_require_result(
            null(), cstr("class type tag allocation"),
            cstr("user class type tag space exhausted"),
        )
        return _class_construct_finish(roots, borrowed, 0)
    c = pcc_gc_load_ptr(null(), roots)
    store_i32(c, PYCLASSOBJECT_TYPE_TAG_ALLOC_OFFSET, user_tag)
    n_slots: int = n_fields + 1
    inst_size: int = PYINSTANCEOBJECT_SIZE + n_slots * C_POINTER_SIZE
    if inst_size > 0x7FFFFFFF:
        inst_size = 0x7FFFFFFF
    store_i32(c, PYCLASSOBJECT_INSTANCE_SIZE_OFFSET, inst_size)

    tail_len: int = 0
    if n_bases > 0:
        nseqs: int = n_bases + 1
        seqs = malloc(nseqs * 16)
        if ptr_is_null(seqs):
            return _class_construct_finish(roots, borrowed, 0)
        memset(seqs, 0, nseqs * 16)
        sequences = py_tuple_new(nseqs)
        store_ptr(roots, 16, sequences)
        if ptr_is_null(sequences):
            free(seqs)
            return _class_construct_finish(roots, borrowed, 0)
        cap_total: int = n_bases
        index = 0
        while index < n_bases:
            _class_construct_base(roots, borrowed, index)
            base = pcc_gc_load_ptr(null(), borrowed)
            count: int = load_i32(base, PYCLASSOBJECT_N_MRO_OFFSET)
            snapshot = py_tuple_new(count)
            store_ptr(roots, 32, snapshot)
            if ptr_is_null(snapshot):
                free(seqs)
                return _class_construct_finish(roots, borrowed, 0)
            _class_construct_snapshot(roots, borrowed, count)
            _class_construct_tuple_set(roots, 16, roots, 32, index)
            pcc_gc_store_root(ptr_add(roots, 32), null())
            store_i32(seqs, index * 16 + 12, count)
            cap_total = cap_total + count
            index = index + 1
        _class_construct_tuple_set(roots, 16, roots, 8, n_bases)
        store_i32(seqs, n_bases * 16 + 12, n_bases)
        if cap_total <= 0:
            cap_total = 1
        accumulator = py_tuple_new(cap_total)
        store_ptr(roots, 24, accumulator)
        if ptr_is_null(accumulator):
            free(seqs)
            return _class_construct_finish(roots, borrowed, 0)
        merge_done: int = 0
        while merge_done == 0:
            any_remaining: int = 0
            index = 0
            while index < nseqs:
                if load_i32(seqs, index * 16 + 8) < load_i32(seqs, index * 16 + 12):
                    any_remaining = 1
                index = index + 1
            if any_remaining == 0:
                merge_done = 1
            else:
                found: int = 0
                pick: int = 0
                while pick < nseqs and found == 0:
                    head: int = load_i32(seqs, pick * 16 + 8)
                    if head < load_i32(seqs, pick * 16 + 12):
                        _class_construct_pick(roots, borrowed, pick, head)
                        acceptable: int = 1
                        check: int = 0
                        while check < nseqs:
                            if check != pick:
                                tail: int = load_i32(seqs, check * 16 + 8) + 1
                                limit: int = load_i32(seqs, check * 16 + 12)
                                while tail < limit:
                                    if _class_construct_equal(roots, borrowed, check, tail) != 0:
                                        acceptable = 0
                                        tail = limit
                                    tail = tail + 1
                            check = check + 1
                        if acceptable != 0:
                            found = 1
                    pick = pick + 1
                if found == 0:
                    free(seqs)
                    return _class_construct_finish(roots, borrowed, 0)
                _class_construct_tuple_set(roots, 24, borrowed, 0, tail_len)
                tail_len = tail_len + 1
                index = 0
                while index < nseqs:
                    head = load_i32(seqs, index * 16 + 8)
                    if head < load_i32(seqs, index * 16 + 12):
                        if _class_construct_equal(roots, borrowed, index, head) != 0:
                            store_i32(seqs, index * 16 + 8, head + 1)
                    index = index + 1
        free(seqs)

    root = _object_root()
    store_ptr(borrowed, 8, root)
    append_root: int = 0
    if n_bases == 0:
        if ptr_eq(pcc_gc_load_ptr(null(), roots), pcc_gc_load_ptr(null(), ptr_add(borrowed, 8))) == 0:
            append_root = 1
    mro_len: int = 1 + tail_len + append_root
    mro = malloc(mro_len * C_POINTER_SIZE)
    if ptr_is_null(mro):
        return _class_construct_finish(roots, borrowed, 0)
    pcc_py_gc_minor_graph_lock()
    c = pcc_gc_load_ptr(null(), roots)
    store_ptr(mro, 0, c)
    accumulator = pcc_gc_load_ptr(null(), ptr_add(roots, 24))
    index = 0
    while index < tail_len:
        value = pcc_gc_load_ptr(accumulator, ptr_add(accumulator, 24 + index * C_POINTER_SIZE))
        store_ptr(mro, (1 + index) * C_POINTER_SIZE, value)
        index = index + 1
    if append_root != 0:
        store_ptr(mro, (mro_len - 1) * C_POINTER_SIZE, pcc_gc_load_ptr(null(), ptr_add(borrowed, 8)))
    store_ptr(c, PYCLASSOBJECT_MRO_OFFSET, mro)
    store_i32(c, PYCLASSOBJECT_N_MRO_OFFSET, mro_len)
    pcc_gc_backend4_zpage_register_owner_payload_span(c, mro, mro_len * C_POINTER_SIZE)
    pcc_py_gc_minor_graph_unlock()
    c = pcc_gc_load_ptr(null(), roots)
    if py_class_is_str_subclass(c) != 0:
        # One extra traced owner; __dict__ stays at the existing offset.
        store_i32(c, PYCLASSOBJECT_INSTANCE_SIZE_OFFSET, inst_size + C_POINTER_SIZE)
    return _class_construct_finish(roots, borrowed, 1)


@c_abi_export("py_class_new_from_objects")
def py_class_new_from_objects(name_obj, bases_obj, namespace):
    if not _ptr_can_have_header(name_obj) or load_i32(name_obj, PYOBJECTHEADER_TYPE_TAG_OFFSET) != PY_TYPE_STR:
        py_raise_owned(py_exc_new(3, cstr("type.__new__() argument 1 must be str")))
        return null()
    name = py_str_utf8(name_obj)
    if ptr_is_null(name) != 0:
        return null()
    n_bases: int = 0
    bases_kind: int = 0
    none_obj = global_load_ptr("py_None")
    if ptr_is_null(bases_obj) != 0 or ptr_eq(bases_obj, none_obj) != 0:
        n_bases = 0
    elif _ptr_can_have_header(bases_obj) and load_i32(bases_obj, PYOBJECTHEADER_TYPE_TAG_OFFSET) == PY_TYPE_TUPLE:
        n_bases = py_tuple_len(bases_obj)
        bases_kind = 1
    elif _ptr_can_have_header(bases_obj) and load_i32(bases_obj, PYOBJECTHEADER_TYPE_TAG_OFFSET) == PY_TYPE_LIST:
        n_bases = py_list_len(bases_obj)
        bases_kind = 2
    else:
        py_raise_owned(py_exc_new(3, cstr("type.__new__() argument 2 must be tuple")))
        return null()
    if n_bases < 0 or n_bases > 2147483647:
        py_raise_owned(py_exc_new(3, cstr("too many base classes")))
        return null()
    base_array = null()
    if n_bases > 0:
        base_array = malloc(n_bases * 8)
        if ptr_is_null(base_array) != 0:
            return null()
        i: int = 0
        valid: int = 1
        while i < n_bases:
            item = null()
            if bases_kind == 1:
                item = py_tuple_get(bases_obj, i)
            else:
                item = py_list_get(bases_obj, i)
            if not _ptr_is_class(item):
                if ptr_is_null(item) == 0:
                    py_decref(item)
                valid = 0
                i = n_bases
            else:
                store_ptr(base_array, i * 8, item)
            i = i + 1
        if valid == 0:
            free(base_array)
            py_raise_owned(py_exc_new(3, cstr("type.__new__() base must be class")))
            return null()
    cls = py_class_new_abi(name, base_array, n_bases, null(), 0)
    if ptr_is_null(base_array) == 0:
        free(base_array)
    if ptr_is_null(cls) != 0:
        return null()
    if ptr_is_null(namespace) == 0 and ptr_eq(namespace, none_obj) == 0:
        if py_class_apply_namespace_dict(cls, namespace) != 0:
            py_decref(cls)
            return null()
    return cls


@c_abi_export("py_class_mark_slots_only")
def py_class_mark_slots_only(cls) -> None:
    if ptr_is_null(cls) != 0:
        return
    flags: int = load_i32(cls, PYOBJECTHEADER_FLAGS_OFFSET)
    store_i32(cls, PYOBJECTHEADER_FLAGS_OFFSET, flags | 2)


@c_abi_export("py_class_mark_dict_subclass")
def py_class_mark_dict_subclass(cls) -> None:
    # Set PY_CLASS_FLAG_DICT_SUBCLASS (bit 2, value 4): this class subclasses
    # the builtin ``dict``, so dict-inherited item storage / methods are routed
    # to a backing dict in the instance's __dict__ slot (see py_protocol.c).
    if ptr_is_null(cls) != 0:
        return
    flags: int = load_i32(cls, PYOBJECTHEADER_FLAGS_OFFSET)
    store_i32(cls, PYOBJECTHEADER_FLAGS_OFFSET, flags | 4)


@c_abi_export("py_instance_method_call_direct")
def py_instance_method_call_direct(inst, name, full_args):
    """Call ``inst``'s ``name`` method with ``full_args``, which already holds
    ``inst`` at index 0.

    The general protocol for ``self.foo(a, b)`` on a method some subclass
    overrides costs one MRO walk for ``__getattribute__``, one for
    ``__getattr__``, one field-index scan and one for ``foo`` itself, then
    allocates a bound-method object with a two-element captures tuple; the
    caller allocates an argument tuple; ``_instance_bound_method_entry``
    allocates a *second* tuple to prepend ``self``; and all four objects are
    then refcounted down again.  On pcc1 compiling a module that protocol is
    about 40% of self time, with another 13% in the refcounting and
    deallocation it creates.

    Only use the direct path when the *runtime* receiver still has ordinary
    method lookup. Instance/class mutation can invalidate a compile-time
    class-graph proof, so every uncertain shape uses the general protocol.
    """
    if not _ptr_is_instance(inst):
        return _instance_method_call_fallback(inst, name, full_args)
    cls = pcc_gc_load_ptr(inst, ptr_add(inst, PYINSTANCEOBJECT_CLS_OFFSET))
    if ptr_is_null(cls) != 0:
        return _instance_method_call_fallback(inst, name, full_args)
    if ptr_is_null(_class_lookup_in_mro(cls, cstr("__getattribute__"))) == 0:
        return _instance_method_call_fallback(inst, name, full_args)
    class_attr = _class_attr_lookup_in_mro(cls, name)
    if ptr_is_null(class_attr) == 0:
        py_decref(class_attr)
        return _instance_method_call_fallback(inst, name, full_args)
    if _lookup_field_index(cls, name) >= 0:
        return _instance_method_call_fallback(inst, name, full_args)
    dyn_slot = _dynamic_attr_slot(inst)
    if ptr_is_null(dyn_slot) == 0:
        dyn = pcc_gc_load_ptr(inst, dyn_slot)
        if ptr_is_null(dyn) == 0:
            return _instance_method_call_fallback(inst, name, full_args)
    func = _class_lookup_in_mro(cls, name)
    if ptr_is_null(func) != 0:
        return _instance_method_call_fallback(inst, name, full_args)
    if load_i32(func, PYOBJECTHEADER_TYPE_TAG_OFFSET) != PY_TYPE_FUNC:
        return _instance_method_call_fallback(inst, name, full_args)
    return py_func_call_kwargs(func, full_args, null())


@c_abi_export("py_obj_load_method")
def py_obj_load_method(obj, name, out_self):
    """`obj.name` for an immediate positional call, without a bound object.

    When the cached resolution proves `name` is a plain class function that
    nothing on the instance shadows, return that function and store `obj` in
    `*out_self`; `py_obj_call_method` then calls it with `obj` first, which is
    what the bound method would do.  Otherwise `*out_self` stays NULL and the
    ordinary attribute is returned.  Resolution happens here, before the
    caller evaluates arguments, exactly like the attribute load it replaces.
    """
    store_ptr(out_self, 0, null())
    if _ptr_is_instance(obj):
        cls = pcc_gc_load_ptr(obj, ptr_add(obj, PYINSTANCEOBJECT_CLS_OFFSET))
        if ptr_is_null(cls) == 0:
            # A method entry is recorded only past the `__getattribute__`
            # check, and any class mutation retires it.
            entry = _attr_res_find(cls, name, _ATTR_RES_METHOD)
            if ptr_is_null(entry) == 0:
                # Method entries hold only verified function objects.
                func = load_ptr(entry, 16)
                dyn_slot = _dynamic_attr_slot(obj)
                if ptr_is_null(dyn_slot) != 0 or ptr_is_null(
                    pcc_gc_load_ptr(obj, dyn_slot)
                ) != 0:
                    py_incref(func)
                    store_ptr(out_self, 0, obj)
                    return func
    return py_obj_getattr(obj, name)


@c_abi_export("py_obj_call_method")
def py_obj_call_method(method, self_obj, args):
    """Call what `py_obj_load_method` returned, with positional `args`."""
    if ptr_is_null(self_obj) != 0:
        return py_obj_call(method, args, null())
    n: int = 0
    if ptr_is_null(args) == 0:
        n = py_tuple_len(args)
    full_args = py_tuple_new(n + 1)
    if ptr_is_null(full_args) != 0:
        return _class_require_result(
            null(),
            cstr("py_tuple_new"),
            cstr("method call argument tuple allocation failed"),
        )
    py_tuple_set_item(full_args, 0, self_obj)
    i: int = 0
    while i < n:
        item = py_tuple_get(args, i)
        py_tuple_set_item(full_args, i + 1, item)
        if ptr_is_null(item) == 0:
            py_decref(item)
        i = i + 1
    out = py_func_call_kwargs(method, full_args, null())
    py_decref(full_args)
    return out


@c_abi_export("py_obj_call_method_kwargs")
def py_obj_call_method_kwargs(method, self_obj, args, kwargs):
    """`py_obj_call_method` with a keywords dict (or NULL).

    `obj.name(a, key=b)` used to bind a method object -- a fresh captures
    tuple and a copied signature per call -- only to call it once.
    """
    if ptr_is_null(self_obj) != 0:
        return py_obj_call(method, args, kwargs)
    n: int = 0
    if ptr_is_null(args) == 0:
        n = py_tuple_len(args)
    full_args = py_tuple_new(n + 1)
    if ptr_is_null(full_args) != 0:
        return _class_require_result(
            null(),
            cstr("py_tuple_new"),
            cstr("method call argument tuple allocation failed"),
        )
    py_tuple_set_item(full_args, 0, self_obj)
    i: int = 0
    while i < n:
        item = py_tuple_get(args, i)
        py_tuple_set_item(full_args, i + 1, item)
        if ptr_is_null(item) == 0:
            py_decref(item)
        i = i + 1
    out = py_func_call_kwargs(method, full_args, kwargs)
    py_decref(full_args)
    return out


def _instance_method_call_fallback(inst, name, full_args):
    """The general attribute protocol, rebuilding the argument tuple.

    ``full_args`` carries the receiver at index 0 because the fast path wants
    it there; the bound method the general path produces supplies its own, so
    the tail has to be copied out.  This runs only for receivers the fast path
    could not resolve, so the extra tuple is not on any hot path.
    """
    n: int = py_tuple_len(full_args)
    tail = py_tuple_new(n - 1)
    if ptr_is_null(tail) != 0:
        return null()
    i: int = 1
    while i < n:
        item = py_tuple_get(full_args, i)
        py_tuple_set_item(tail, i - 1, item)
        if ptr_is_null(item) == 0:
            py_decref(item)
        i = i + 1
    method = py_obj_getattr(inst, name)
    if ptr_is_null(method) != 0:
        py_decref(tail)
        return null()
    out = py_obj_call(method, tail, null())
    py_decref(method)
    py_decref(tail)
    return out

pcc_gc_frame_enter = extern("pcc_gc_frame_enter", (c_ptr, c_ptr), c_void)

pcc_gc_frame_leave = extern("pcc_gc_frame_leave", (c_ptr,), c_void)

pcc_gc_take_pinned_slot = extern("pcc_gc_take_pinned_slot", (c_ptr, c_int64), c_ptr)

pcc_gc_pin = extern("pcc_gc_pin", (c_ptr,), c_void)

pcc_gc_unpin = extern("pcc_gc_unpin", (c_ptr,), c_void)

define_global_i32("pcc_bound_callback_frame_map", 11)

pcc_gc_store_root = extern("pcc_gc_store_root", (c_ptr, c_ptr), c_void)


# Slot-based call boundary. Every managed scratch slot is registered EMPTY
# before reading any incoming operand. Native code pointers live exclusively
# in the untraced lookup record, never in this managed root array.
pcc_platform_abort = extern("pcc_platform_abort", (), c_void)


def _special_open(slots, tokens, handles) -> int:
    memset(slots, 0, 14 * C_POINTER_SIZE)
    memset(tokens, 0, 14 * C_POINTER_SIZE)
    memset(handles, 0, 14 * C_POINTER_SIZE)
    count: int = 0
    while count < 14:
        handle = pcc_gc_scheduler_root_register_handle(ptr_add(slots, count * C_POINTER_SIZE))
        if ptr_is_null(handle) != 0:
            return count
        store_ptr(handles, count * C_POINTER_SIZE, handle)
        count = count + 1
    return count


def _special_error(message) -> int:
    py_runtime_error_if_unset(cstr("slot-based special call"), message)
    return -1


def _special_copy(slots, tokens, index: int, source, borrowed: int) -> int:
    destination = ptr_add(slots, index * C_POINTER_SIZE)
    token: int = 0
    if ptr_is_null(source) == 0:
        if borrowed != 0:
            token = pcc_gc_root_copy_borrowed_lease(destination, source)
        else:
            token = pcc_gc_root_copy_lease(destination, source)
    if token < 0:
        # Lookup can hold an outer graph transaction. Allocate the diagnostic
        # only in the public caller after that transaction has been released.
        return -1
    store_i64(tokens, index * C_POINTER_SIZE, token)
    return 0


def _special_adopt(slots, tokens, index: int) -> int:
    # The producer must have stored its owned result directly into this
    # already registered empty slot, with no intervening call or poll.
    slot = ptr_add(slots, index * C_POINTER_SIZE)
    token: int = pcc_gc_foreign_lease_acquire(slot)
    if token < 0:
        return _special_error(cstr("special-call result lease acquisition failed"))
    store_i64(tokens, index * C_POINTER_SIZE, token)
    pcc_py_gc_minor_graph_lock()
    pcc_gc_note_slot_write_barrier(null(), slot, load_ptr(slot, 0))
    pcc_py_gc_minor_graph_unlock()
    return 0


def _special_drop(slots, tokens, index: int) -> None:
    slot = ptr_add(slots, index * C_POINTER_SIZE)
    token: int = load_i64(tokens, index * C_POINTER_SIZE)
    if pcc_gc_foreign_lease_release(slot, token) != 0:
        # Returning would leave a counted lease referring to dead stack
        # storage. This is an internal token invariant, not a Python error.
        pcc_platform_abort()
        return
    store_i64(tokens, index * C_POINTER_SIZE, 0)
    pcc_gc_store_root(slot, null())


def _special_close(slots, tokens, handles, count: int, suspended: int) -> None:
    if suspended != 0:
        # Keep callback failure independent of the exception that was pending
        # on entry, and protect it from operand finalizers during cleanup.
        py_tls_exc_swap_slot(ptr_add(slots, 13 * C_POINTER_SIZE))
    index: int = 12
    while index > 0:
        _special_drop(slots, tokens, index)
        index = index - 1
    if suspended != 0:
        py_clear_exception()
        if ptr_is_null(load_ptr(slots, 13 * C_POINTER_SIZE)) == 0:
            pcc_gc_store_root(slots, null())
            py_clear_exception()
            py_tls_exc_swap_slot(ptr_add(slots, 13 * C_POINTER_SIZE))
        else:
            py_tls_exc_swap_slot(slots)
    index = 0
    while index < count:
        pcc_gc_scheduler_root_unregister_handle(load_ptr(handles, index * C_POINTER_SIZE))
        index = index + 1


def _special_name_equal(key, name, length: int) -> int:
    if ptr_is_null(key) != 0 or is_tagged_int(key) != 0:
        return 0
    if load_i32(key, PYOBJECTHEADER_TYPE_TAG_OFFSET) != PY_TYPE_STR:
        return 0
    if load_i64(key, PYSTROBJECT_BYTE_LEN_OFFSET) != length:
        return 0
    index: int = 0
    while index < length:
        if load_i8(key, PYSTROBJECT_DATA_OFFSET + index) != load_i8(name, index):
            return 0
        index = index + 1
    return 1


def _special_native_instance(value) -> bool:
    if ptr_is_null(value) != 0 or is_tagged_int(value) != 0:
        return False
    # Extension tags occupy the same high range, but extension instances do
    # not have PyInstanceObject.cls. Their call protocol belongs to the C-API
    # kernel, not this native class/descriptor lookup.
    tag: int = load_i32(value, PYOBJECTHEADER_TYPE_TAG_OFFSET)
    if pcc_capi_is_cext_type_tag(tag) != 0:
        return False
    return _ptr_is_instance(value)


def _special_lookup_locked(cls, name, record):
    """One C3 pass: namespace then native table at each individual owner.

    record.kind: 0 absent, 1 owning namespace slot, 2 borrowed managed native
    metadata, 3 raw native address. Only record.raw may contain a code pointer.
    No key allocation, descriptor callback or foreign invocation occurs here.
    """
    store_i64(record, 0, 0)
    store_ptr(record, C_POINTER_SIZE, null())
    length: int = strlen(name)
    count: int = load_i32(cls, PYCLASSOBJECT_N_MRO_OFFSET)
    mro = load_ptr(cls, PYCLASSOBJECT_MRO_OFFSET)
    index: int = 0
    while index < count:
        owner = pcc_gc_load_ptr(cls, ptr_add(mro, index * C_POINTER_SIZE))
        if ptr_is_null(owner) == 0:
            attrs = pcc_gc_resolve_root_slot_unlocked(ptr_add(owner, PYCLASSOBJECT_ATTRS_OFFSET), 0)
            if ptr_is_null(attrs) == 0:
                entries = load_ptr(attrs, PYDICTOBJECT_ENTRIES_OFFSET)
                used: int = load_i64(attrs, PYDICTOBJECT_ENTRIES_USED_OFFSET)
                entry: int = 0
                while entry < used:
                    offset: int = entry * DICTENTRY_SIZE
                    # Dictionary entries own their keys. GC3 forwarding must
                    # transfer that slot's reference, not just heal a borrowed
                    # raw copy or overwrite it with the uncounted read barrier.
                    key = pcc_gc_resolve_root_slot_unlocked(ptr_add(entries, offset + DICTENTRY_KEY_OFFSET), 0)
                    if _special_name_equal(key, name, length) != 0:
                        slot = ptr_add(entries, offset + DICTENTRY_VALUE_OFFSET)
                        if ptr_is_null(load_ptr(slot, 0)) == 0:
                            store_i64(record, 0, 1)
                            return slot
                    entry = entry + 1
            methods = load_ptr(owner, PYCLASSOBJECT_METHODS_OFFSET)
            method_count: int = load_i32(owner, PYCLASSOBJECT_N_METHODS_OFFSET)
            entry = 0
            while entry < method_count:
                offset = entry * PYCLASSMETHOD_SIZE
                method_name = load_ptr(methods, offset + PYCLASSMETHOD_NAME_OFFSET)
                if _strs_eq(method_name, name) != 0:
                    slot = ptr_add(methods, offset + PYCLASSMETHOD_FUNC_OFFSET)
                    value = load_ptr(slot, 0)
                    if ptr_is_null(value) == 0:
                        if is_tagged_int(value) != 0 or _ptr_can_have_header(value):
                            store_i64(record, 0, 2)
                            return slot
                        store_i64(record, 0, 3)
                        store_ptr(record, C_POINTER_SIZE, value)
                        return null()
                entry = entry + 1
        index = index + 1
    return null()


def _special_tuple_item(slots, tokens, index: int, tuple_index: int, item: int) -> int:
    # The tuple has a counted address lease; its inline slot address remains
    # valid while copy_lease acquires its own graph transaction.
    owner = load_ptr(slots, tuple_index * C_POINTER_SIZE)
    source = ptr_add(owner, PYTUPLEOBJECT_ITEMS_OFFSET + item * C_POINTER_SIZE)
    return _special_copy(slots, tokens, index, source, 0)


def _special_tuple_new(slots, tokens, index: int, length: int) -> int:
    store_ptr(slots, index * C_POINTER_SIZE, py_tuple_new(length))
    if _special_adopt(slots, tokens, index) != 0:
        return -1
    if ptr_is_null(load_ptr(slots, index * C_POINTER_SIZE)) != 0:
        return _special_error(cstr("special-call argument tuple allocation failed"))
    return 0


def _special_prepend(slots, tokens) -> int:
    # slots6 binding receiver, 2 original args, 7 full args, 8 current item.
    count: int = 0
    if ptr_is_null(load_ptr(slots, 2 * C_POINTER_SIZE)) == 0:
        count = py_tuple_len(load_ptr(slots, 2 * C_POINTER_SIZE))
    if _special_tuple_new(slots, tokens, 7, count + 1) != 0:
        return -1
    py_tuple_set_item(load_ptr(slots, 7 * C_POINTER_SIZE), 0, load_ptr(slots, 6 * C_POINTER_SIZE))
    index: int = 0
    while index < count:
        if _special_tuple_item(slots, tokens, 8, 2, index) != 0:
            return -1
        py_tuple_set_item(load_ptr(slots, 7 * C_POINTER_SIZE), index + 1, load_ptr(slots, 8 * C_POINTER_SIZE))
        _special_drop(slots, tokens, 8)
        if py_err_occurred() != 0:
            return -1
        index = index + 1
    return 0


def _special_validate_arguments(slots) -> int:
    args = load_ptr(slots, 2 * C_POINTER_SIZE)
    if ptr_is_null(args) == 0:
        if is_tagged_int(args) != 0 or load_i32(args, PYOBJECTHEADER_TYPE_TAG_OFFSET) != PY_TYPE_TUPLE:
            py_raise_owned(py_exc_new(3, cstr("call positional arguments must be a tuple")))
            return -1
    kwargs = load_ptr(slots, 3 * C_POINTER_SIZE)
    if ptr_is_null(kwargs) == 0 and ptr_eq(kwargs, global_load_ptr("py_None")) == 0:
        if is_tagged_int(kwargs) != 0 or load_i32(kwargs, PYOBJECTHEADER_TYPE_TAG_OFFSET) != PY_TYPE_DICT:
            py_raise_owned(py_exc_new(3, cstr("call keyword arguments must be a dict")))
            return -1
    return py_call_validate_kwargs(kwargs)


def _special_call_native(slots, tokens, native) -> int:
    count: int = 0
    if ptr_is_null(load_ptr(slots, 2 * C_POINTER_SIZE)) == 0:
        count = py_tuple_len(load_ptr(slots, 2 * C_POINTER_SIZE))
    kwargs = load_ptr(slots, 3 * C_POINTER_SIZE)
    if ptr_is_null(kwargs) == 0 and ptr_eq(kwargs, global_load_ptr("py_None")) == 0:
        if py_dict_len(kwargs) != 0:
            py_raise_owned(py_exc_new(3, cstr("native special method does not accept keyword arguments")))
            return -1
    if count > 3:
        py_raise_owned(py_exc_new(3, cstr("native special method accepts at most three arguments")))
        return -1
    index: int = 0
    while index < count:
        if _special_tuple_item(slots, tokens, 6 + index, 2, index) != 0:
            return -1
        index = index + 1
    if count == 0:
        store_ptr(slots, 12 * C_POINTER_SIZE, call_ptr1(native, load_ptr(slots, C_POINTER_SIZE)))
    elif count == 1:
        store_ptr(slots, 12 * C_POINTER_SIZE, call_ptr2(native, load_ptr(slots, C_POINTER_SIZE), load_ptr(slots, 6 * C_POINTER_SIZE)))
    elif count == 2:
        store_ptr(slots, 12 * C_POINTER_SIZE, call_ptr3(native, load_ptr(slots, C_POINTER_SIZE), load_ptr(slots, 6 * C_POINTER_SIZE), load_ptr(slots, 7 * C_POINTER_SIZE)))
    else:
        store_ptr(slots, 12 * C_POINTER_SIZE, call_ptr4(native, load_ptr(slots, C_POINTER_SIZE), load_ptr(slots, 6 * C_POINTER_SIZE), load_ptr(slots, 7 * C_POINTER_SIZE), load_ptr(slots, 8 * C_POINTER_SIZE)))
    return _special_adopt(slots, tokens, 12)


def _special_bind_and_call(slots, tokens, record) -> int:
    if load_i64(record, 0) == 3:
        return _special_call_native(slots, tokens, load_ptr(record, C_POINTER_SIZE))
    descriptor = load_ptr(slots, 5 * C_POINTER_SIZE)
    tag: int = -1
    if is_tagged_int(descriptor) == 0:
        tag = load_i32(descriptor, PYOBJECTHEADER_TYPE_TAG_OFFSET)
    prepend: int = 0
    if tag == PY_TYPE_FUNC:
        if _special_copy(slots, tokens, 9, ptr_add(slots, 5 * C_POINTER_SIZE), 0) != 0:
            return -1
        if _special_copy(slots, tokens, 6, ptr_add(slots, C_POINTER_SIZE), 0) != 0:
            return -1
        prepend = 1
    elif tag == PY_TYPE_STATICMETHOD or tag == PY_TYPE_CLASSMETHOD:
        offset: int = PYSTATICMETHODOBJECT_FUNC_OFFSET
        if tag == PY_TYPE_CLASSMETHOD:
            offset = PYCLASSMETHODOBJECT_FUNC_OFFSET
        source = ptr_add(load_ptr(slots, 5 * C_POINTER_SIZE), offset)
        result: int = _special_copy(slots, tokens, 9, source, 0)
        if result != 0:
            return -1
        if tag == PY_TYPE_CLASSMETHOD:
            if _special_copy(slots, tokens, 6, ptr_add(slots, 4 * C_POINTER_SIZE), 0) != 0:
                return -1
            prepend = 1
    elif tag == PY_TYPE_PROPERTY:
        source = ptr_add(load_ptr(slots, 5 * C_POINTER_SIZE), PYPROPERTYOBJECT_FGET_OFFSET)
        result = _special_copy(slots, tokens, 11, source, 0)
        if result != 0:
            return -1
        if ptr_is_null(load_ptr(slots, 11 * C_POINTER_SIZE)) != 0:
            py_raise_owned(py_exc_new(6, cstr("unreadable attribute")))
            return -1
        if _special_tuple_new(slots, tokens, 10, 1) != 0:
            return -1
        py_tuple_set_item(load_ptr(slots, 10 * C_POINTER_SIZE), 0, load_ptr(slots, C_POINTER_SIZE))
        if py_obj_call_slots(ptr_add(slots, 11 * C_POINTER_SIZE), ptr_add(slots, 10 * C_POINTER_SIZE), null(), ptr_add(slots, 9 * C_POINTER_SIZE)) != 0:
            return -1
        if _special_adopt(slots, tokens, 9) != 0:
            return -1
    else:
        if _special_tuple_new(slots, tokens, 10, 2) != 0:
            return -1
        py_tuple_set_item(load_ptr(slots, 10 * C_POINTER_SIZE), 0, load_ptr(slots, C_POINTER_SIZE))
        py_tuple_set_item(load_ptr(slots, 10 * C_POINTER_SIZE), 1, load_ptr(slots, 4 * C_POINTER_SIZE))
        get_handled = stack_alloc(8)
        store_i64(get_handled, 0, 0)
        if py_obj_special_call_slots(ptr_add(slots, 5 * C_POINTER_SIZE), cstr("__get__"), ptr_add(slots, 10 * C_POINTER_SIZE), null(), ptr_add(slots, 9 * C_POINTER_SIZE), get_handled) != 0:
            return -1
        if load_i64(get_handled, 0) == 0:
            if _special_copy(slots, tokens, 9, ptr_add(slots, 5 * C_POINTER_SIZE), 0) != 0:
                return -1
        elif _special_adopt(slots, tokens, 9) != 0:
            return -1
    args_slot = ptr_add(slots, 2 * C_POINTER_SIZE)
    if prepend != 0:
        if _special_prepend(slots, tokens) != 0:
            return -1
        args_slot = ptr_add(slots, 7 * C_POINTER_SIZE)
    if py_obj_call_slots(ptr_add(slots, 9 * C_POINTER_SIZE), args_slot, ptr_add(slots, 3 * C_POINTER_SIZE), ptr_add(slots, 12 * C_POINTER_SIZE)) != 0:
        return -1
    return _special_adopt(slots, tokens, 12)


def _special_publish(slots, tokens, result_slot) -> int:
    source = ptr_add(slots, 12 * C_POINTER_SIZE)
    if ptr_is_null(load_ptr(source, 0)) != 0:
        return _special_error(cstr("special-call callback returned NULL without an exception"))
    if py_err_occurred() != 0:
        return -1
    if pcc_gc_root_move(result_slot, source) != 0:
        return _special_error(cstr("special-call result destination must be empty"))
    token: int = load_i64(tokens, 12 * C_POINTER_SIZE)
    store_i64(tokens, 12 * C_POINTER_SIZE, 0)
    if pcc_gc_foreign_lease_release(result_slot, token) != 0:
        pcc_platform_abort()
        return -1
    return 0


@c_abi_export("py_obj_special_call_slots")
def py_obj_special_call_slots(receiver_slot, name, args_slot, kwargs_slot, result_slot, handled) -> int:
    """Call a named type-level special method from authoritative owning roots.

    Status 0 means absent or success; handled distinguishes them. It is zero
    only on absence, one on selection or any failure. result_slot receives one
    owned result on success. NULL args/kwargs slot pointers denote emptiness.
    """
    if ptr_is_null(handled) == 0:
        store_i64(handled, 0, 1)
    if ptr_is_null(receiver_slot) != 0 or ptr_is_null(result_slot) != 0 or ptr_is_null(name) != 0:
        return _special_error(cstr("special call requires source and output root slots"))
    slots = stack_alloc(14 * C_POINTER_SIZE)
    tokens = stack_alloc(14 * C_POINTER_SIZE)
    handles = stack_alloc(14 * C_POINTER_SIZE)
    count: int = _special_open(slots, tokens, handles)
    status: int = -1
    suspended: int = 0
    if count == 14:
        py_tls_exc_swap_slot(slots)
        suspended = 1
        status = _special_copy(slots, tokens, 1, receiver_slot, 0)
        if status == 0:
            status = _special_copy(slots, tokens, 2, args_slot, 0)
        if status == 0:
            status = _special_copy(slots, tokens, 3, kwargs_slot, 0)
        if status == 0:
            status = _special_validate_arguments(slots)
        if status == 0:
            record = stack_alloc(2 * C_POINTER_SIZE)
            memset(record, 0, 2 * C_POINTER_SIZE)
            receiver = load_ptr(slots, C_POINTER_SIZE)
            owner_source = null()
            if _ptr_is_class(receiver):
                owner_source = ptr_add(receiver, PYCLASSOBJECT_METACLASS_OFFSET)
            elif _special_native_instance(receiver):
                owner_source = ptr_add(receiver, PYINSTANCEOBJECT_CLS_OFFSET)
            if ptr_is_null(owner_source) == 0:
                status = _special_copy(slots, tokens, 4, owner_source, 1)
            if status == 0 and ptr_is_null(load_ptr(slots, 4 * C_POINTER_SIZE)) == 0:
                plan = stack_alloc(256)
                prepared: int = 0
                pcc_py_gc_minor_graph_lock()
                method_slot = _special_lookup_locked(load_ptr(slots, 4 * C_POINTER_SIZE), name, record)
                kind: int = load_i64(record, 0)
                if kind == 1 or kind == 2:
                    token: int = pcc_gc_root_copy_lease_prepare_locked(ptr_add(slots, 5 * C_POINTER_SIZE), method_slot, 1 if kind == 2 else 0, plan)
                    prepared = 1
                    if token < 0:
                        status = -1
                    else:
                        store_i64(tokens, 5 * C_POINTER_SIZE, token)
                pcc_py_gc_minor_graph_unlock()
                if prepared != 0:
                    pcc_gc_root_copy_lease_finish(plan)
            if status == 0:
                if load_i64(record, 0) == 0:
                    if ptr_is_null(handled) == 0:
                        store_i64(handled, 0, 0)
                else:
                    status = _special_bind_and_call(slots, tokens, record)
                    if status == 0:
                        status = _special_publish(slots, tokens, result_slot)
    else:
        _special_error(cstr("special-call root registration failed"))
    if status < 0:
        _special_error(cstr("special-call binding failed without an exception"))
    _special_close(slots, tokens, handles, count, suspended)
    return status


@c_abi_export("py_obj_call_slots")
def py_obj_call_slots(callable_slot, args_slot, kwargs_slot, result_slot) -> int:
    """Slot entry for generic calls; raw py_obj_call remains compatibility ABI."""
    if ptr_is_null(callable_slot) != 0 or ptr_is_null(result_slot) != 0:
        return _special_error(cstr("call requires source and output root slots"))
    slots = stack_alloc(14 * C_POINTER_SIZE)
    tokens = stack_alloc(14 * C_POINTER_SIZE)
    handles = stack_alloc(14 * C_POINTER_SIZE)
    count: int = _special_open(slots, tokens, handles)
    status: int = -1
    suspended: int = 0
    if count == 14:
        py_tls_exc_swap_slot(slots)
        suspended = 1
        status = _special_copy(slots, tokens, 1, callable_slot, 0)
        if status == 0:
            status = _special_copy(slots, tokens, 2, args_slot, 0)
        if status == 0:
            status = _special_copy(slots, tokens, 3, kwargs_slot, 0)
        if status == 0:
            status = _special_validate_arguments(slots)
        if status == 0:
            selected = stack_alloc(8)
            store_i64(selected, 0, 0)
            callable_obj = load_ptr(slots, C_POINTER_SIZE)
            tag: int = -1
            if ptr_is_null(callable_obj) == 0 and is_tagged_int(callable_obj) == 0:
                tag = load_i32(callable_obj, PYOBJECTHEADER_TYPE_TAG_OFFSET)
            if tag == PY_TYPE_STATICMETHOD:
                source = ptr_add(callable_obj, PYSTATICMETHODOBJECT_FUNC_OFFSET)
                status = _special_copy(slots, tokens, 9, source, 0)
                if status == 0:
                    status = py_obj_call_slots(ptr_add(slots, 9 * C_POINTER_SIZE), ptr_add(slots, 2 * C_POINTER_SIZE), ptr_add(slots, 3 * C_POINTER_SIZE), ptr_add(slots, 12 * C_POINTER_SIZE))
                store_i64(selected, 0, 1)
            elif _ptr_is_class(callable_obj) and py_obj_call_context_is_deferred() != 0:
                status = py_obj_call_slots_sync(ptr_add(slots, C_POINTER_SIZE), ptr_add(slots, 2 * C_POINTER_SIZE), ptr_add(slots, 3 * C_POINTER_SIZE), ptr_add(slots, 12 * C_POINTER_SIZE))
                store_i64(selected, 0, 1)
            elif _ptr_is_class(callable_obj) or _special_native_instance(callable_obj):
                status = py_obj_special_call_slots(ptr_add(slots, C_POINTER_SIZE), cstr("__call__"), ptr_add(slots, 2 * C_POINTER_SIZE), ptr_add(slots, 3 * C_POINTER_SIZE), ptr_add(slots, 12 * C_POINTER_SIZE), selected)
            if status == 0 and load_i64(selected, 0) == 0:
                store_ptr(slots, 12 * C_POINTER_SIZE, py_obj_call_default(load_ptr(slots, C_POINTER_SIZE), load_ptr(slots, 2 * C_POINTER_SIZE), load_ptr(slots, 3 * C_POINTER_SIZE)))
            if status == 0:
                status = _special_adopt(slots, tokens, 12)
            if status == 0:
                status = _special_publish(slots, tokens, result_slot)
    else:
        _special_error(cstr("call root registration failed"))
    if status < 0:
        _special_error(cstr("call failed without an exception"))
    _special_close(slots, tokens, handles, count, suspended)
    return status


@c_abi_export("py_obj_special_present")
def py_obj_special_present(value, name) -> int:
    """Type-level presence only, for an independently address-leased value.

    The caller retains an owning source slot and its counted address lease.
    This graph transaction reads namespace/method slots without binding a
    descriptor or invoking user code. A present None descriptor still counts
    as present; raw native method addresses are never traced as objects.
    """
    if ptr_is_null(value) != 0 or is_tagged_int(value) != 0 or ptr_is_null(name) != 0:
        return 0
    found: int = 0
    record = stack_alloc(2 * C_POINTER_SIZE)
    pcc_py_gc_minor_graph_lock()
    owner_source = null()
    if _ptr_is_class(value):
        owner_source = ptr_add(value, PYCLASSOBJECT_METACLASS_OFFSET)
    elif _special_native_instance(value):
        owner_source = ptr_add(value, PYINSTANCEOBJECT_CLS_OFFSET)
    if ptr_is_null(owner_source) == 0:
        owner = pcc_gc_resolve_root_slot_unlocked(owner_source, 1)
        if ptr_is_null(owner) == 0:
            _special_lookup_locked(owner, name, record)
            if load_i64(record, 0) != 0:
                found = 1
    pcc_py_gc_minor_graph_unlock()
    return found


pcc_class_namespace_acquire_slots = extern(
    "pcc_class_namespace_acquire_slots", (c_ptr, c_ptr, c_ptr, c_ptr, c_ptr), c_int64
)
pcc_class_namespace_install_slots = extern(
    "pcc_class_namespace_install_slots", (c_ptr, c_ptr, c_ptr, c_ptr, c_ptr, c_ptr, c_ptr), c_int64
)
pcc_gc_store_ptr_plan_init = extern(
    "pcc_gc_store_ptr_plan_init", (c_ptr, c_ptr, c_int64), c_void
)
pcc_gc_store_ptr_plan_finish = extern("pcc_gc_store_ptr_plan_finish", (c_ptr,), c_void)
py_dict_namespace_set_slots = extern(
    "py_dict_namespace_set_slots", (c_ptr, c_ptr, c_ptr, c_ptr), c_int64
)
py_dict_namespace_del_slots = extern(
    "py_dict_namespace_del_slots", (c_ptr, c_ptr, c_ptr), c_int64
)

# Owning namespace writer. No graph lock spans a
# semantic function call or local integer assignment in this module.
_CLASS_WRITE_CLASS = 1
_CLASS_WRITE_VALUE = 2
_CLASS_WRITE_DICT = 3
_CLASS_WRITE_KEY = 4
_CLASS_WRITE_CREATED = 5
_CLASS_WRITE_SLOT_COUNT = 14
_CLASS_WRITE_CONTEXT_OWNER_SLOT = 0
_CLASS_WRITE_CONTEXT_NAME = 1
_CLASS_WRITE_CONTEXT_COUNT = 2


def _class_write_acquire_namespace(slots: c_ptr, tokens: c_ptr, create: int) -> int:
    output = ptr_add(slots, _CLASS_WRITE_DICT * C_POINTER_SIZE)
    token_slot = ptr_add(tokens, _CLASS_WRITE_DICT * C_POINTER_SIZE)
    class_slot = ptr_add(slots, _CLASS_WRITE_CLASS * C_POINTER_SIZE)
    plan = stack_alloc(256)
    prepared = stack_alloc(C_POINTER_SIZE)
    status: int = pcc_class_namespace_acquire_slots(class_slot, output, token_slot, plan, prepared)
    if load_i64(prepared, 0) != 0:
        pcc_gc_root_copy_lease_finish(plan)
    if status != 0:
        return status
    if create == 0:
        return 0
    created_slot = ptr_add(slots, _CLASS_WRITE_CREATED * C_POINTER_SIZE)
    store_ptr(created_slot, 0, py_dict_new())
    if _special_adopt(slots, tokens, _CLASS_WRITE_CREATED) != 0:
        return -1
    if ptr_is_null(load_ptr(created_slot, 0)) != 0:
        return _special_error(cstr("class namespace allocation failed"))
    write_plan = stack_alloc(128)
    pcc_gc_store_ptr_plan_init(write_plan, load_ptr(class_slot, 0), pcc_gc_backend())
    status = pcc_class_namespace_install_slots(class_slot, created_slot, output, token_slot, write_plan, plan, prepared)
    pcc_gc_store_ptr_plan_finish(write_plan)
    if load_i64(prepared, 0) != 0:
        pcc_gc_root_copy_lease_finish(plan)
    _special_drop(slots, tokens, _CLASS_WRITE_CREATED)
    return status


def _class_write_namespace_body(slots: c_ptr, tokens: c_ptr, name: c_ptr, value_slot: c_ptr, remove: int) -> int:
    class_slot = ptr_add(slots, _CLASS_WRITE_CLASS * C_POINTER_SIZE)
    if remove == 0:
        if _special_copy(slots, tokens, _CLASS_WRITE_VALUE, value_slot, 0) != 0:
            return -1
    key_slot = ptr_add(slots, _CLASS_WRITE_KEY * C_POINTER_SIZE)
    store_ptr(key_slot, 0, py_str_new(name, strlen(name)))
    if _special_adopt(slots, tokens, _CLASS_WRITE_KEY) != 0:
        return -1
    if ptr_is_null(load_ptr(key_slot, 0)) != 0:
        return _special_error(cstr("class namespace key allocation failed"))
    # Discovery is conservative: a failed installation may leave this hint
    # positive, but no successful __del__ installation may leave it zero.
    if _strs_eq(name, cstr("__del__")) != 0 and remove == 0:
        _note_class_defines_del()
    context = stack_alloc(_CLASS_WRITE_CONTEXT_COUNT * C_POINTER_SIZE)
    store_ptr(context, _CLASS_WRITE_CONTEXT_OWNER_SLOT * C_POINTER_SIZE, class_slot)
    store_ptr(context, _CLASS_WRITE_CONTEXT_NAME * C_POINTER_SIZE, name)
    while True:
        acquired: int = _class_write_acquire_namespace(slots, tokens, 0 if remove != 0 else 1)
        if acquired < 0:
            return _special_error(cstr("class namespace owner acquisition failed"))
        if acquired == 0:
            return 1
        if remove != 0:
            status: int = py_dict_namespace_del_slots(
                ptr_add(slots, _CLASS_WRITE_DICT * C_POINTER_SIZE), key_slot, context,
            )
        else:
            status = py_dict_namespace_set_slots(
                ptr_add(slots, _CLASS_WRITE_DICT * C_POINTER_SIZE), key_slot,
                ptr_add(slots, _CLASS_WRITE_VALUE * C_POINTER_SIZE), context,
            )
        if status != -2:
            return status
        # A validated receiver's namespace identity changed before commit.
        # Reacquire its current owner; neither a stale dictionary nor an
        # invalid receiver is silently treated as a successful mutation.
        _special_drop(slots, tokens, _CLASS_WRITE_DICT)


@c_abi_export("py_class_write_namespace_slots")
def py_class_write_namespace_slots(class_slot: c_ptr, name: c_ptr, value_slot: c_ptr, remove: int) -> int:
    """Internal owning-slot writer; 0 success, 1 absent delete, -1 error.

    Inputs are registered owning roots. name is an immutable C string whose
    caller-owned storage remains valid throughout the call. Raw callbacks are
    never supplied as values; their publication has a separate native ABI.
    """
    if ptr_is_null(class_slot) != 0 or ptr_is_null(name) != 0:
        return _special_error(cstr("class namespace write requires owner and name"))
    if remove == 0 and ptr_is_null(value_slot) != 0:
        return _special_error(cstr("class namespace write requires a value owner"))
    slots = stack_alloc(_CLASS_WRITE_SLOT_COUNT * C_POINTER_SIZE)
    tokens = stack_alloc(_CLASS_WRITE_SLOT_COUNT * C_POINTER_SIZE)
    handles = stack_alloc(_CLASS_WRITE_SLOT_COUNT * C_POINTER_SIZE)
    count: int = _special_open(slots, tokens, handles)
    status: int = -1
    suspended: int = 0
    if count == _CLASS_WRITE_SLOT_COUNT:
        py_tls_exc_swap_slot(slots)
        suspended = 1
        status = _special_copy(slots, tokens, _CLASS_WRITE_CLASS, class_slot, 0)
        if status == 0:
            status = _class_write_namespace_body(slots, tokens, name, value_slot, remove)
    if status < 0:
        _special_error(cstr("class namespace write failed without an exception"))
    _special_close(slots, tokens, handles, count, suspended)
    return status


# The constructor reuses the fourteen-slot special-call frame. Slot zero and
# thirteen retain its existing exception protocol; names make semantic owners
# explicit while preserving the shared _special_publish result position.
_STR_NEW_CLASS = 1
_STR_NEW_ARGS = 2
_STR_NEW_KWARGS = 3
_STR_NEW_OBJECT = 4
_STR_NEW_ENCODING = 5
_STR_NEW_ERRORS = 6
_STR_NEW_TEXT = 7
_STR_NEW_KEY = 8
_STR_NEW_KEY_VALUE = 9
_STR_NEW_PAYLOAD_CLASS = 10
_STR_NEW_RESULT = 12

define_global_i32("pcc_str_new_borrowed_map", -3)
define_global_i32("pcc_str_new_result_map", 1)
py_obj_str = extern("py_obj_str", (c_ptr,), c_ptr)
py_str_byte_len = extern("py_str_byte_len", (c_ptr,), c_int64)
py_bytes_decode_with_encoding = extern("py_bytes_decode_with_encoding", (c_ptr, c_ptr, c_ptr), c_ptr)


def _str_new_entry(captures: c_ptr, args: c_ptr) -> c_ptr:
    # The ordinary native binder resolved cls, *args and **kwargs. args is
    # leased by that binder throughout the raw constructor call.
    return py_str_subclass_new(
        load_ptr(args, PYTUPLEOBJECT_ITEMS_OFFSET),
        load_ptr(args, PYTUPLEOBJECT_ITEMS_OFFSET + C_POINTER_SIZE),
        load_ptr(args, PYTUPLEOBJECT_ITEMS_OFFSET + 2 * C_POINTER_SIZE),
    )


def _str_new_exact_argument(slots, tokens, index: int) -> int:
    value = load_ptr(slots, index * C_POINTER_SIZE)
    if ptr_is_null(value) != 0:
        return 0
    if load_i32(value, PYOBJECTHEADER_TYPE_TAG_OFFSET) == PY_TYPE_STR:
        return 0
    # Codec helpers use the exact-string raw view ABI. Snapshot a subtype
    # under its backing-owner lease instead of passing the wrapper through.
    store_ptr(slots, _STR_NEW_KEY_VALUE * C_POINTER_SIZE, py_str_exact_copy(value))
    if _special_adopt(slots, tokens, _STR_NEW_KEY_VALUE) != 0:
        return -1
    if ptr_is_null(load_ptr(slots, _STR_NEW_KEY_VALUE * C_POINTER_SIZE)) != 0:
        return -1
    _special_drop(slots, tokens, index)
    status: int = _special_copy(slots, tokens, index, ptr_add(slots, _STR_NEW_KEY_VALUE * C_POINTER_SIZE), 0)
    _special_drop(slots, tokens, _STR_NEW_KEY_VALUE)
    return status


def _str_new_body(slots, tokens) -> int:
    cls = load_ptr(slots, _STR_NEW_CLASS * C_POINTER_SIZE)
    if py_class_is_str_subclass(cls) == 0:
        py_raise_owned(py_exc_new(3, cstr("str.__new__ requires a str subtype")))
        return -1
    if _special_validate_arguments(slots) != 0:
        return -1
    nargs: int = 0
    args = load_ptr(slots, _STR_NEW_ARGS * C_POINTER_SIZE)
    if ptr_is_null(args) == 0:
        nargs = py_tuple_len(args)
    if nargs > 3:
        py_raise_owned(py_exc_new(3, cstr("str() takes at most 3 arguments")))
        return -1
    index: int = 0
    while index < nargs:
        if _special_tuple_item(slots, tokens, _STR_NEW_OBJECT + index, _STR_NEW_ARGS, index) != 0:
            return -1
        index = index + 1
    kwargs = load_ptr(slots, _STR_NEW_KWARGS * C_POINTER_SIZE)
    if ptr_is_null(kwargs) == 0 and ptr_eq(kwargs, global_load_ptr("py_None")) == 0:
        matched: int = 0
        index = 0
        while index < 3:
            name = cstr("object")
            if index == 1:
                name = cstr("encoding")
            elif index == 2:
                name = cstr("errors")
            store_ptr(slots, _STR_NEW_KEY * C_POINTER_SIZE, py_str_new(name, strlen(name)))
            if _special_adopt(slots, tokens, _STR_NEW_KEY) != 0:
                return -1
            store_ptr(slots, _STR_NEW_KEY_VALUE * C_POINTER_SIZE, py_dict_get(kwargs, load_ptr(slots, _STR_NEW_KEY * C_POINTER_SIZE)))
            if _special_adopt(slots, tokens, _STR_NEW_KEY_VALUE) != 0:
                return -1
            if ptr_is_null(load_ptr(slots, _STR_NEW_KEY_VALUE * C_POINTER_SIZE)) == 0:
                matched = matched + 1
                if index < nargs:
                    py_raise_owned(py_exc_new(3, cstr("str() argument supplied by name and position")))
                    return -1
                if _special_copy(slots, tokens, _STR_NEW_OBJECT + index, ptr_add(slots, _STR_NEW_KEY_VALUE * C_POINTER_SIZE), 0) != 0:
                    return -1
            _special_drop(slots, tokens, _STR_NEW_KEY_VALUE)
            _special_drop(slots, tokens, _STR_NEW_KEY)
            index = index + 1
        if matched != py_dict_len(kwargs):
            py_raise_owned(py_exc_new(3, cstr("invalid keyword argument for str()")))
            return -1
    value = load_ptr(slots, _STR_NEW_OBJECT * C_POINTER_SIZE)
    encoding = load_ptr(slots, _STR_NEW_ENCODING * C_POINTER_SIZE)
    errors = load_ptr(slots, _STR_NEW_ERRORS * C_POINTER_SIZE)
    if ptr_is_null(encoding) == 0 or ptr_is_null(errors) == 0:
        if ptr_is_null(encoding) == 0:
            if ptr_is_null(py_str_payload(encoding)) != 0:
                py_raise_owned(py_exc_new(3, cstr("str() encoding must be str")))
                return -1
        if ptr_is_null(errors) == 0:
            if ptr_is_null(py_str_payload(errors)) != 0:
                py_raise_owned(py_exc_new(3, cstr("str() errors must be str")))
                return -1
        if _str_new_exact_argument(slots, tokens, _STR_NEW_ENCODING) != 0:
            return -1
        if _str_new_exact_argument(slots, tokens, _STR_NEW_ERRORS) != 0:
            return -1
        encoding = load_ptr(slots, _STR_NEW_ENCODING * C_POINTER_SIZE)
        errors = load_ptr(slots, _STR_NEW_ERRORS * C_POINTER_SIZE)
        if ptr_is_null(value) != 0:
            store_ptr(slots, _STR_NEW_TEXT * C_POINTER_SIZE, py_str_new(cstr(""), 0))
        else:
            store_ptr(slots, _STR_NEW_TEXT * C_POINTER_SIZE, py_bytes_decode_with_encoding(value, encoding, errors))
    elif ptr_is_null(value) != 0:
        store_ptr(slots, _STR_NEW_TEXT * C_POINTER_SIZE, py_str_new(cstr(""), 0))
    else:
        store_ptr(slots, _STR_NEW_TEXT * C_POINTER_SIZE, py_obj_str(value))
    if _special_adopt(slots, tokens, _STR_NEW_TEXT) != 0:
        return -1
    if py_err_occurred() != 0:
        return -1
    text = load_ptr(slots, _STR_NEW_TEXT * C_POINTER_SIZE)
    payload = py_str_payload(text)
    if ptr_is_null(payload) != 0:
        py_raise_owned(py_exc_new(3, cstr("__str__ returned non-string")))
        return -1
    # A __str__ override can itself return a subclass. The immutable storage
    # owner must be an exact native string, not a hidden second user object.
    if ptr_eq(payload, text) == 0:
        store_ptr(slots, _STR_NEW_KEY_VALUE * C_POINTER_SIZE, py_str_exact_copy(text))
        if _special_adopt(slots, tokens, _STR_NEW_KEY_VALUE) != 0:
            return -1
        _special_drop(slots, tokens, _STR_NEW_TEXT)
        if _special_copy(slots, tokens, _STR_NEW_TEXT, ptr_add(slots, _STR_NEW_KEY_VALUE * C_POINTER_SIZE), 0) != 0:
            return -1
        _special_drop(slots, tokens, _STR_NEW_KEY_VALUE)
    if py_builtin_type_class_tag(cls) == PY_TYPE_STR:
        return _special_copy(slots, tokens, _STR_NEW_RESULT, ptr_add(slots, _STR_NEW_TEXT * C_POINTER_SIZE), 0)
    store_ptr(slots, _STR_NEW_RESULT * C_POINTER_SIZE, py_instance_new(cls))
    if _special_adopt(slots, tokens, _STR_NEW_RESULT) != 0:
        return -1
    result = load_ptr(slots, _STR_NEW_RESULT * C_POINTER_SIZE)
    if ptr_is_null(result) != 0:
        return _special_error(cstr("str subtype allocation failed"))
    pcc_gc_store_ptr(result, _instance_builtin_payload_slot(result, cls), load_ptr(slots, _STR_NEW_TEXT * C_POINTER_SIZE))
    return -1 if py_err_occurred() != 0 else 0


def _str_exact_copy_body(slots, tokens) -> int:
    value = load_ptr(slots, _STR_NEW_CLASS * C_POINTER_SIZE)
    if ptr_is_null(value) != 0 or is_tagged_int(value) != 0:
        py_raise_owned(py_exc_new(3, cstr("string copy requires str")))
        return -1
    if load_i32(value, PYOBJECTHEADER_TYPE_TAG_OFFSET) == PY_TYPE_STR:
        source = ptr_add(slots, _STR_NEW_CLASS * C_POINTER_SIZE)
    else:
        if not _ptr_is_instance(value):
            py_raise_owned(py_exc_new(3, cstr("string copy requires str")))
            return -1
        if _special_copy(slots, tokens, _STR_NEW_PAYLOAD_CLASS,
                ptr_add(value, PYINSTANCEOBJECT_CLS_OFFSET), 1) != 0:
            return -1
        cls = load_ptr(slots, _STR_NEW_PAYLOAD_CLASS * C_POINTER_SIZE)
        if py_class_is_str_subclass(cls) == 0:
            py_raise_owned(py_exc_new(3, cstr("string copy requires str")))
            return -1
        source = _instance_builtin_payload_slot(value, cls)
    # The receiver is leased, so its payload slot remains addressable while
    # the child acquires its own counted address lease. This child lease, not
    # a receiver root/pin, protects UTF-8 bytes across allocation and polls.
    if _special_copy(slots, tokens, _STR_NEW_TEXT, source, 0) != 0:
        return -1
    payload = load_ptr(slots, _STR_NEW_TEXT * C_POINTER_SIZE)
    if ptr_is_null(payload) != 0:
        return _special_error(cstr("str subtype has no initialized payload"))
    store_ptr(slots, _STR_NEW_RESULT * C_POINTER_SIZE,
              py_str_new(py_str_utf8(payload), py_str_byte_len(payload)))
    return _special_adopt(slots, tokens, _STR_NEW_RESULT)


def _str_rooted_entry(cls, args, kwargs, copy_only: int):
    borrowed = stack_alloc(3 * C_POINTER_SIZE)
    store_ptr(borrowed, 0, cls)
    store_ptr(borrowed, C_POINTER_SIZE, args)
    store_ptr(borrowed, 2 * C_POINTER_SIZE, kwargs)
    pcc_gc_frame_enter(global_addr("pcc_str_new_borrowed_map"), borrowed)
    output = stack_alloc(C_POINTER_SIZE)
    store_ptr(output, 0, null())
    pcc_gc_frame_enter(global_addr("pcc_str_new_result_map"), output)
    slots = stack_alloc(14 * C_POINTER_SIZE)
    tokens = stack_alloc(14 * C_POINTER_SIZE)
    handles = stack_alloc(14 * C_POINTER_SIZE)
    count: int = _special_open(slots, tokens, handles)
    status: int = -1
    suspended: int = 0
    if count == 14:
        py_tls_exc_swap_slot(slots)
        suspended = 1
        status = _special_copy(slots, tokens, _STR_NEW_CLASS, borrowed, 1)
        if status == 0:
            status = _special_copy(slots, tokens, _STR_NEW_ARGS, ptr_add(borrowed, C_POINTER_SIZE), 1)
        if status == 0:
            status = _special_copy(slots, tokens, _STR_NEW_KWARGS, ptr_add(borrowed, 2 * C_POINTER_SIZE), 1)
        if status == 0:
            if copy_only != 0:
                status = _str_exact_copy_body(slots, tokens)
            else:
                status = _str_new_body(slots, tokens)
        if status == 0:
            status = _special_publish(slots, tokens, output)
    if status != 0:
        _special_error(cstr("str subtype construction failed without an exception"))
    _special_close(slots, tokens, handles, count, suspended)
    prior: int = 0
    value = load_ptr(output, 0)
    if ptr_is_null(value) == 0:
        prior = load_i32(value, PYOBJECTHEADER_FLAGS_OFFSET) & 64
        pcc_gc_pin(value)
    pcc_gc_frame_leave(output)
    pcc_gc_frame_leave(borrowed)
    return pcc_gc_take_pinned_slot(output, prior)


@c_abi_export("py_str_subclass_new")
def py_str_subclass_new(cls, args, kwargs):
    return _str_rooted_entry(cls, args, kwargs, 0)


@c_abi_export("py_str_exact_copy")
def py_str_exact_copy(value):
    return _str_rooted_entry(value, null(), null(), 1)
