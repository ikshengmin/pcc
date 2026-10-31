"""Checked raw storage over the existing pcc-Python allocator (R0a only).

This is not a managed-object API: the private records below are neither roots
nor Python-tagged objects. Only exact live bases issued here are admitted; no
caller address is dereferenced before lookup. Requested and effective layout
are distinct. Pointer-only tokens cannot detect stale-address ABA, prove unique
ownership, or make invalid output storage safe. Callers must serialize use and
ownership transfer for each allocation, and must not free it through malloc's
unregistered API. Output storage must remain valid and outside the allocation
being resized. No C++ new/delete, Rust Drop, collector, or handle is implemented.

The private lock may nest the existing allocator lock; the allocator never
calls this module. There are no callbacks or managed-payload accesses here.
The simple live list is a correctness-first substrate, not a scalability claim.
The compiler requires explicit exports even for local helpers; symbols with
``pcc_rt_raw_internal_`` are implementation details, not the public raw ABI.
"""

from pcc import i64
from pcc.extern import (
    c_abi_export,
    c_abi_typed_export,
    c_int32,
    c_int64,
    c_ptr,
    c_void,
    extern,
)
from pcc.unsafe import (
    atomic_cas_i64,
    atomic_load_i64,
    atomic_store_i64,
    define_global_i64,
    define_global_ptr_null,
    global_addr,
    global_load_ptr,
    global_store_ptr,
    load_i8,
    load_i64,
    load_ptr,
    malloc,
    free,
    null,
    ptr_eq,
    ptr_is_null,
    store_i8,
    store_i64,
    store_ptr,
)

__pcc_freestanding__ = True

OK = 0
OOM = 1
OVERFLOW = 2
BAD_LAYOUT = 3
ZERO = 1
RAW_FAMILY = 1
# Private, separately allocated live record. Never a prefix on caller memory.
RECORD_NEXT = 0
RECORD_BASE = 8
RECORD_SIZE = 16
RECORD_REQUESTED_ALIGN = 24
RECORD_EFFECTIVE_ALIGN = 32
RECORD_FAMILY = 40
RECORD_BYTES = 48

define_global_i64("pcc_rt_raw_lock", 0)
define_global_ptr_null("pcc_rt_raw_records")

allocate = extern("memalign", (c_int64, c_int64), c_ptr)
errno_get = extern("pcc_errno_get", (), c_int32)
errno_set = extern("pcc_errno_set", (c_int32,), c_void)


@c_abi_export("pcc_rt_raw_internal_lock")
def _lock() -> None:
    word = global_addr("pcc_rt_raw_lock")
    while atomic_cas_i64(word, 0, 0, 1, "acquire", "relaxed") != 0:
        while atomic_load_i64(word, 0, "relaxed") != 0:
            pass


@c_abi_export("pcc_rt_raw_internal_unlock")
def _unlock() -> None:
    atomic_store_i64(global_addr("pcc_rt_raw_lock"), 0, 0, "release")


@c_abi_export("pcc_rt_raw_internal_layout")
def _layout(size: i64, alignment: i64) -> i64:
    if alignment <= 0 or alignment > 1073741824:
        return BAD_LAYOUT
    if (alignment & (alignment - 1)) != 0:
        return BAD_LAYOUT
    # Exact unsigned ABI bits enter an i64 lane: the high half is overflow,
    # never narrowed into an allocator request. Match its page-rounding bound.
    effective: i64 = alignment
    if effective < 16:
        effective = 16
    if size < 0 or size > 9223372036854771664 - effective:
        return OVERFLOW
    return OK


@c_abi_export("pcc_rt_raw_internal_find_locked")
def _find_locked(base) -> c_ptr:
    record = global_load_ptr("pcc_rt_raw_records")
    while not ptr_is_null(record):
        if ptr_eq(load_ptr(record, RECORD_BASE), base):
            return record
        record = load_ptr(record, RECORD_NEXT)
    return null()


@c_abi_export("pcc_rt_raw_internal_matches")
def _matches(record, size: i64, alignment: i64) -> i64:
    if ptr_is_null(record):
        return 0
    return (load_i64(record, RECORD_SIZE) == size
            and load_i64(record, RECORD_REQUESTED_ALIGN) == alignment
            and load_i64(record, RECORD_FAMILY) == RAW_FAMILY)


@c_abi_export("pcc_rt_raw_internal_alloc")
def _alloc(size: i64, alignment: i64, flags: i64, out) -> i64:
    if ptr_is_null(out) or (flags & -2) != 0:
        return BAD_LAYOUT
    status: i64 = _layout(size, alignment)
    if status != OK:
        return status
    effective: i64 = alignment
    if effective < 16:
        effective = 16
    # Reserve bookkeeping first; no untracked payload can escape on failure.
    record = malloc(RECORD_BYTES)
    if ptr_is_null(record):
        return OOM
    base = allocate(effective, size)
    if ptr_is_null(base):
        free(record)
        return OOM
    if flags == ZERO:
        index: i64 = 0
        while index < size:
            store_i8(base, index, 0)
            index = index + 1
    store_ptr(record, RECORD_BASE, base)
    store_i64(record, RECORD_SIZE, size)
    store_i64(record, RECORD_REQUESTED_ALIGN, alignment)
    store_i64(record, RECORD_EFFECTIVE_ALIGN, effective)
    store_i64(record, RECORD_FAMILY, RAW_FAMILY)
    _lock()
    store_ptr(record, RECORD_NEXT, global_load_ptr("pcc_rt_raw_records"))
    global_store_ptr("pcc_rt_raw_records", record)
    _unlock()
    store_ptr(out, 0, base)
    return OK


@c_abi_typed_export("pcc_rt_raw_alloc", "i32", ("i64", "i64", "i32", "ptr"))
def pcc_rt_raw_alloc(size: i64, alignment: i64, flags: i64, out) -> i64:
    saved_errno: i64 = errno_get()
    status: i64 = _alloc(size, alignment, flags, out)
    errno_set(saved_errno)
    return status


@c_abi_export("pcc_rt_raw_internal_resize")
def _resize(old, old_size: i64, alignment: i64, new_size: i64, out) -> i64:
    if ptr_is_null(out):
        return BAD_LAYOUT
    # NULL is not a live checked allocation. This is deliberately not realloc.
    _lock()
    record = _find_locked(old)
    if not _matches(record, old_size, alignment):
        _unlock()
        return BAD_LAYOUT
    status: i64 = _layout(new_size, alignment)
    if status != OK:
        _unlock()
        return status
    effective: i64 = load_i64(record, RECORD_EFFECTIVE_ALIGN)
    replacement = allocate(effective, new_size)
    if ptr_is_null(replacement):
        _unlock()
        return OOM
    count: i64 = old_size
    if count > new_size:
        count = new_size
    index: i64 = 0
    while index < count:
        store_i8(replacement, index, load_i8(old, index))
        index = index + 1
    # Reuse the live record; all fallible operations precede ownership transfer.
    store_ptr(record, RECORD_BASE, replacement)
    store_i64(record, RECORD_SIZE, new_size)
    free(old)
    _unlock()
    store_ptr(out, 0, replacement)
    return OK


@c_abi_typed_export("pcc_rt_raw_resize", "i32", ("ptr", "i64", "i64", "i64", "ptr"))
def pcc_rt_raw_resize(old, old_size: i64, alignment: i64, new_size: i64, out) -> i64:
    saved_errno: i64 = errno_get()
    status: i64 = _resize(old, old_size, alignment, new_size, out)
    errno_set(saved_errno)
    return status


@c_abi_export("pcc_rt_raw_internal_free")
def _free(base, size: i64, alignment: i64) -> i64:
    if ptr_is_null(base):
        return OK
    _lock()
    previous = null()
    record = global_load_ptr("pcc_rt_raw_records")
    while not ptr_is_null(record):
        if ptr_eq(load_ptr(record, RECORD_BASE), base):
            break
        previous = record
        record = load_ptr(record, RECORD_NEXT)
    if not _matches(record, size, alignment):
        _unlock()
        return BAD_LAYOUT
    following = load_ptr(record, RECORD_NEXT)
    if ptr_is_null(previous):
        global_store_ptr("pcc_rt_raw_records", following)
    else:
        store_ptr(previous, RECORD_NEXT, following)
    free(base)
    free(record)
    _unlock()
    return OK


@c_abi_typed_export("pcc_rt_raw_free", "i32", ("ptr", "i64", "i64"))
def pcc_rt_raw_free(base, size: i64, alignment: i64) -> i64:
    saved_errno: i64 = errno_get()
    status: i64 = _free(base, size, alignment)
    errno_set(saved_errno)
    return status
