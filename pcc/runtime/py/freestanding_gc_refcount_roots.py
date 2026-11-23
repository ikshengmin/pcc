"""Refcount external-root scan over the raw GC object list."""

from pcc import i64
from pcc.extern import c_abi_export, c_ptr, c_void, extern
from pcc.unsafe import (
    global_load_ptr,
    is_tagged_int,
    load_i32,
    load_i64,
    load_ptr,
    ptr_is_null,
    store_i64,
)


__pcc_freestanding__ = True
