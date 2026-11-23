"""Backend 3 scalar copy-oldification orchestration."""
from pcc import i64
from pcc.runtime.py.py_abi_constants import PY_TYPE_BYTEARRAY, PY_TYPE_BYTES, PY_TYPE_COMPLEX, PY_TYPE_CPY_HANDLE, PY_TYPE_FLOAT, PY_TYPE_INT, PY_TYPE_STR

from pcc.extern import c_abi_export, c_int64, c_ptr, c_void, extern
from pcc.unsafe import (
    atomic_rmw_i64,
    free,
    global_addr,
    is_tagged_int,
    load_i32,
    load_i64,
    load_ptr,
    malloc,
    memmove,
    null,
    ptr_is_null,
    store_i32,
    store_i64,
    store_ptr,
)


__pcc_freestanding__ = True







@c_abi_export("pcc_gc_generational_oldify_copy")
def pcc_gc_generational_oldify_copy(from_obj: c_ptr) -> c_ptr:
    return null()
