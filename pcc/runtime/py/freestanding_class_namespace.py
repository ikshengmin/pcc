"""Allocation-free class namespace commit callbacks for graph transactions.

Only the namespace writer calls these internal C ABI entries, with the graph
lock held and its counted no-park lease active. Thread polls remain enabled;
the kernel's verified no-park branch suppresses suspension while locked.
"""

__pcc_freestanding__ = True

from pcc import (
    i64,
)
from pcc.extern import (
    c_abi_export,
    c_ptr,
)
from pcc.runtime.py.py_abi_constants import (
    C_POINTER_SIZE,
    PYCLASSMETHOD_FUNC_OFFSET,
    PYCLASSMETHOD_NAME_OFFSET,
    PYCLASSMETHOD_SIZE,
    PYCLASSOBJECT_ATTRS_OFFSET,
    PYCLASSOBJECT_DEL_METHOD_OFFSET,
    PYCLASSOBJECT_METHODS_OFFSET,
    PYCLASSOBJECT_N_METHODS_OFFSET,
    PYOBJECTHEADER_TYPE_TAG_OFFSET,
    PY_TYPE_CLASS,
)
from pcc.unsafe import (
    atomic_rmw_i32,
    cstr,
    global_addr,
    load_i8,
    load_i32,
    load_ptr,
    null,
    ptr_eq,
    ptr_is_null,
    store_ptr,
)


# Raw internal notification context: it contains a registered owner-slot
# address and an immutable C-string address, never a managed object pointer.
_CLASS_NAMESPACE_CONTEXT_OWNER_SLOT: i64 = 0
_CLASS_NAMESPACE_CONTEXT_NAME: i64 = 1
_CLASS_NAMESPACE_CONTEXT_COUNT: i64 = 2


def _class_namespace_same_name(left: c_ptr, right: c_ptr) -> i64:
    index: i64 = 0
    while True:
        a: i64 = load_i8(left, index)
        b: i64 = load_i8(right, index)
        if a != b:
            return 0
        if a == 0:
            return 1
        index += 1
    return 0


@c_abi_export("py_class_namespace_validate_locked")
def py_class_namespace_validate_locked(context: c_ptr, dictionary: c_ptr) -> i64:
    """Validate the exact namespace before any dictionary commit writes.

    The caller holds the graph lock. The context points to a live registered
    class owner slot; it does not contain a borrowed class address. A mismatch
    leaves the dictionary untouched so the writer can rebuild its plan.
    """
    owner_slot = load_ptr(context, _CLASS_NAMESPACE_CONTEXT_OWNER_SLOT * C_POINTER_SIZE)
    cls = load_ptr(owner_slot, 0)
    if ptr_is_null(cls) != 0:
        return 0
    if load_i32(cls, PYOBJECTHEADER_TYPE_TAG_OFFSET) != PY_TYPE_CLASS:
        return 0
    return ptr_eq(load_ptr(cls, PYCLASSOBJECT_ATTRS_OFFSET), dictionary)


@c_abi_export("py_class_namespace_commit_locked")
def py_class_namespace_commit_locked(context: c_ptr, dictionary: c_ptr) -> None:
    """Invalidate native aliases in the already validated dict transaction.

    Managed callables live only in the owning namespace. A namespace write
    retires every same-name raw table fallback before displaced-owner disposal.
    NULL stores need neither a managed pointer probe nor a shading barrier.
    This helper does not allocate, park, release references or invoke Python.
    """
    owner_slot = load_ptr(context, _CLASS_NAMESPACE_CONTEXT_OWNER_SLOT * C_POINTER_SIZE)
    cls = load_ptr(owner_slot, 0)
    name = load_ptr(context, _CLASS_NAMESPACE_CONTEXT_NAME * C_POINTER_SIZE)
    methods = load_ptr(cls, PYCLASSOBJECT_METHODS_OFFSET)
    count: i64 = load_i32(cls, PYCLASSOBJECT_N_METHODS_OFFSET)
    index: i64 = 0
    if ptr_is_null(methods) == 0:
        while index < count:
            offset: i64 = index * PYCLASSMETHOD_SIZE
            candidate = load_ptr(methods, offset + PYCLASSMETHOD_NAME_OFFSET)
            if _class_namespace_same_name(candidate, name) != 0:
                store_ptr(methods, offset + PYCLASSMETHOD_FUNC_OFFSET, null())
            index += 1
    if _class_namespace_same_name(name, cstr("__del__")) != 0:
        store_ptr(cls, PYCLASSOBJECT_DEL_METHOD_OFFSET, null())
    # Invalidate before deferred decrefs can reenter ordinary attribute lookup.
    atomic_rmw_i32("add", global_addr("py_class_attr_cache_epoch"), 0, 1, "release")
