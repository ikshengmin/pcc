"""Owned Win64 RSS observations and atomic worker report publication.

PROCESS_MEMORY_COUNTERS has two DWORDs followed by eight SIZE_Ts: 72 bytes
on the supported 64-bit Windows target, with peak/current working sets at
offsets 8/16. K32GetProcessMemoryInfo returns BOOL; zero is an error, never
a zero-byte sample. See Microsoft's psapi.h PROCESS_MEMORY_COUNTERS and
GetProcessMemoryInfo reference. This is working-set RSS, not commit charge.
"""

from pcc import i64
from pcc.extern import c_abi_export, c_int, c_int64, c_ptr, extern
from pcc.unsafe import (
    free, load_i8, load_i64, load_ptr, malloc, memcpy, memset, null, ptr_add,
    ptr_is_null, ptr_to_int, stack_alloc, store_i32, store_ptr,
)


__pcc_freestanding__ = True

GetCurrentProcess = extern("GetCurrentProcess", (), c_ptr)
K32GetProcessMemoryInfo = extern("K32GetProcessMemoryInfo", (c_ptr, c_ptr, c_int), c_int)
GetFullPathNameW = extern("GetFullPathNameW", (c_ptr, c_int, c_ptr, c_ptr), c_int)
CreateFileW = extern("CreateFileW", (c_ptr, c_int, c_int, c_ptr, c_int, c_int, c_ptr), c_ptr)
SetFileInformationByHandle = extern("SetFileInformationByHandle", (c_ptr, c_int, c_ptr, c_int), c_int)
CloseHandle = extern("CloseHandle", (c_ptr,), c_int)
utf16 = extern("pcc_win_utf16", (c_ptr,), c_ptr)
error = extern("pcc_win_error", (), c_int64)

_COUNTERS_SIZE = 72
_PEAK_WORKING_SET_OFFSET = 8
_WORKING_SET_OFFSET = 16
_RENAME_HEADER_SIZE = 24
_RENAME_NAME_OFFSET = 20
_RENAME_CLEANUP_ERROR_BASE = 65536


@c_abi_export("pcc_windows_rss_bytes")
def _windows_rss_bytes(want_peak: i64) -> i64:
    counters = stack_alloc(_COUNTERS_SIZE)
    memset(counters, 0, _COUNTERS_SIZE)
    store_i32(counters, 0, _COUNTERS_SIZE)
    if K32GetProcessMemoryInfo(GetCurrentProcess(), counters, _COUNTERS_SIZE) == 0:
        return -1
    offset: i64 = _PEAK_WORKING_SET_OFFSET if want_peak != 0 else _WORKING_SET_OFFSET
    return load_i64(counters, offset)


@c_abi_export("pcc_os_current_rss_bytes")
def pcc_os_current_rss_bytes() -> i64:
    return _windows_rss_bytes(0)


@c_abi_export("pcc_os_peak_rss_bytes")
def pcc_os_peak_rss_bytes() -> i64:
    return _windows_rss_bytes(1)


@c_abi_export("pcc_windows_worker_resource_full_path")
def _worker_resource_full_path(path: c_ptr, output: c_ptr) -> i64:
    """Return the UTF-16 character count and one owned absolute path buffer."""
    store_ptr(output, 0, null())
    wide = utf16(path)
    if ptr_is_null(wide):
        return -22
    size: i64 = GetFullPathNameW(wide, 0, null(), null())
    if size <= 0:
        failure: i64 = error()
        free(wide)
        return failure
    # Bound both allocation arithmetic and the DWORD rename-buffer length.
    # Windows native full paths have at most 32767 UTF-16 code units.
    if size > 32768:
        free(wide)
        return -36
    absolute = malloc(size * 2)
    if ptr_is_null(absolute):
        free(wide)
        return -12
    actual: i64 = GetFullPathNameW(wide, size, absolute, null())
    failure = error() if actual == 0 else -36
    free(wide)
    if actual <= 0 or actual >= size:
        free(absolute)
        return failure
    store_ptr(output, 0, absolute)
    return actual


@c_abi_export("pcc_worker_resource_replace")
def pcc_worker_resource_replace(source: c_ptr, destination: c_ptr) -> i64:
    """Replace only a report's closed .tmp, retaining any open old snapshot.

    Inputs are UTF-8 views held by the caller's c_str leases. This leaf never
    allocates managed objects. Zero confirms rename and handle close. Negative
    errno means no commit and confirmed cleanup (or no handle was opened).
    A positive status encodes primary_errno * 65536 + close_errno: zero primary
    means committed; nonzero primary means rename failed. Neither may retry.
    """
    paths = stack_alloc(16)
    memset(paths, 0, 16)
    destination_length: i64 = _worker_resource_full_path(destination, ptr_add(paths, 8))
    if destination_length < 0:
        return destination_length
    source_length: i64 = _worker_resource_full_path(source, paths)
    if source_length < 0:
        free(load_ptr(paths, 8))
        return source_length
    a = load_ptr(paths, 0)
    b = load_ptr(paths, 8)
    valid: i64 = 1 if source_length == destination_length + 4 else 0
    index: i64 = 0
    while valid and index < destination_length * 2:
        if load_i8(a, index) != load_i8(b, index):
            valid = 0
        index = index + 1
    offset: i64 = destination_length * 2
    if valid:
        valid = (load_i8(a, offset) == 46 and load_i8(a, offset + 1) == 0
                 and load_i8(a, offset + 2) == 116 and load_i8(a, offset + 3) == 0
                 and load_i8(a, offset + 4) == 109 and load_i8(a, offset + 5) == 0
                 and load_i8(a, offset + 6) == 112 and load_i8(a, offset + 7) == 0)
    if not valid:
        free(a)
        free(b)
        return -22
    size: i64 = _RENAME_HEADER_SIZE + destination_length * 2
    information = malloc(size)
    if ptr_is_null(information):
        free(a)
        free(b)
        return -12
    memset(information, 0, size)
    # Win64 FILE_RENAME_INFO: Flags@0, RootDirectory@8, Length@16, Name@20.
    # REPLACE_IF_EXISTS | POSIX_SEMANTICS; never IGNORE_READONLY_ATTRIBUTE.
    store_i32(information, 0, 3)
    store_i32(information, 16, destination_length * 2)
    memcpy(ptr_add(information, _RENAME_NAME_OFFSET), b, destination_length * 2)
    # DELETE, all share modes, noninheritable, OPEN_EXISTING, NORMAL.
    handle = CreateFileW(a, 65536, 7, null(), 3, 128, null())
    status: i64 = 0
    if ptr_to_int(handle) == -1:
        status = error()
    else:
        # FileRenameInfoEx=22. Unsupported filesystems fail without fallback.
        if SetFileInformationByHandle(handle, 22, information, size) == 0:
            status = error()  # Preserve this error before close/free.
        if CloseHandle(handle) == 0:
            cleanup_error: i64 = 0 - error()
            status = (0 - status) * _RENAME_CLEANUP_ERROR_BASE + cleanup_error
    free(information)
    free(a)
    free(b)
    return status
