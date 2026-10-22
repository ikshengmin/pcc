"""Small no-libpython C-API core symbols owned by pcc-Python."""

__pcc_runtime_port__ = True

from pcc.runtime.py.py_abi_constants import PY_TYPE_INT

from pcc.extern import c_abi_typed_export, c_int64, c_ptr, c_void, extern
from pcc.unsafe import (
    atomic_load_i64,
    atomic_store_i64,
    define_global_ptr_null,
    frame_address,
    global_load_ptr,
    global_store_ptr,
    int_to_ptr,
    is_tagged_int,
    load_i32,
    load_ptr,
    null,
    ptr_is_null,
    ptr_to_int,
)


py_incref = extern("py_incref", (c_ptr,), c_void)
py_decref = extern("py_decref", (c_ptr,), c_void)
pcc_refcount_forget = extern("pcc_refcount_forget", (c_ptr,), c_void)
py_dict_new = extern("py_dict_new", (), c_ptr)
pcc_gc_pin = extern("pcc_gc_pin", (c_ptr,), c_void)
pcc_thread_blocking_enter = extern(
    "pcc_thread_blocking_enter", (c_ptr,), c_int64
)
pcc_thread_blocking_leave = extern(
    "pcc_thread_blocking_leave", (c_int64,), c_void
)


define_global_ptr_null("pcc_capi_builtins")
# The no-libpython datetime capsule is still an explicit link-readiness
# boundary: PyDateTime_IMPORT is a no-op and the table remains NULL.  The data
# symbol itself is compiler-generated so production does not need a C owner.
define_global_ptr_null("PyDateTimeAPI")


@c_abi_typed_export("pcc_py_type_of", "i64", ("ptr",))
def pcc_py_type_of(obj) -> int:
    if is_tagged_int(obj):
        return PY_TYPE_INT  # PY_TYPE_INT
    return load_i32(obj, 8)


@c_abi_typed_export("PyEval_GetBuiltins", "ptr", ())
def PyEval_GetBuiltins():
    builtins = global_load_ptr("pcc_capi_builtins")
    if ptr_is_null(builtins):
        builtins = py_dict_new()
        if ptr_is_null(builtins):
            return null()
        # PyEval_GetBuiltins returns a borrowed process-lifetime mapping.
        # Pin the cache so tracing/relocating collectors see the same lifetime
        # contract as CPython's interpreter-owned builtins dictionary.
        pcc_gc_pin(builtins)
        global_store_ptr("pcc_capi_builtins", builtins)
    return builtins


@c_abi_typed_export("Py_INCREF", "void", ("ptr",))
def Py_INCREF(obj) -> None:
    py_incref(obj)


@c_abi_typed_export("Py_DECREF", "void", ("ptr",))
def Py_DECREF(obj) -> None:
    py_decref(obj)


@c_abi_typed_export("pcc_capi_refcnt", "i64", ("ptr",))
def pcc_capi_refcnt(obj) -> int:
    if ptr_is_null(obj) or is_tagged_int(obj):
        return 0
    return atomic_load_i64(obj, 0, "acquire")


@c_abi_typed_export("pcc_capi_set_refcnt", "void", ("ptr", "i64"))
def pcc_capi_set_refcnt(obj, refcnt: int) -> None:
    if ptr_is_null(obj) or is_tagged_int(obj):
        return
    pcc_refcount_forget(obj)
    atomic_store_i64(obj, 0, refcnt, "release")


@c_abi_typed_export("PyTraceMalloc_Track", "i32", ("i32", "i64", "i64"))
def PyTraceMalloc_Track(domain: int, ptr: int, size: int) -> int:
    return 0


@c_abi_typed_export("PyTraceMalloc_Untrack", "i32", ("i32", "i64"))
def PyTraceMalloc_Untrack(domain: int, ptr: int) -> int:
    return 0


@c_abi_typed_export("PyEval_SaveThread", "ptr", ())
def PyEval_SaveThread():
    """Py_BEGIN_ALLOW_THREADS: detach around a blocking call.

    The thread enters the blocking state from its caller's frame, which
    stays live until PyEval_RestoreThread, so a stop of the world does not
    wait for a poll the thread cannot make while it blocks. The caller
    touches no object until it restores. The returned token goes back to
    PyEval_RestoreThread."""
    entered: int = pcc_thread_blocking_enter(load_ptr(frame_address(), 0))
    return int_to_ptr(entered + 1)


@c_abi_typed_export("PyEval_RestoreThread", "void", ("ptr",))
def PyEval_RestoreThread(ts) -> None:
    """Py_END_ALLOW_THREADS: wait out a stopped world, then run again."""
    if ptr_is_null(ts) != 0:
        return
    pcc_thread_blocking_leave(ptr_to_int(ts) - 1)


@c_abi_typed_export("Py_IsInitialized", "i32", ())
def Py_IsInitialized() -> int:
    return 1


@c_abi_typed_export(
    "PyUnstable_Object_IsUniqueReferencedTemporary", "i32", ("ptr",)
)
def PyUnstable_Object_IsUniqueReferencedTemporary(op) -> int:
    return 0


@c_abi_typed_export("PyUnstable_Object_IsUniquelyReferenced", "i32", ("ptr",))
def PyUnstable_Object_IsUniquelyReferenced(obj) -> int:
    if pcc_capi_refcnt(obj) == 1:
        return 1
    return 0
