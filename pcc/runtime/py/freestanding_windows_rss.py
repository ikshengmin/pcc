"""Owned Win64 process RSS and high-water RSS observations.

PROCESS_MEMORY_COUNTERS has two DWORDs followed by eight SIZE_Ts: 72 bytes
on the supported 64-bit Windows target, with peak/current working sets at
offsets 8/16. K32GetProcessMemoryInfo returns BOOL; zero is an error, never
a zero-byte sample. See Microsoft's psapi.h PROCESS_MEMORY_COUNTERS and
GetProcessMemoryInfo reference. This is working-set RSS, not commit charge.
"""

from pcc import i64
from pcc.extern import c_abi_export, c_int, c_ptr, extern
from pcc.unsafe import load_i64, memset, stack_alloc, store_i32


__pcc_freestanding__ = True

GetCurrentProcess = extern("GetCurrentProcess", (), c_ptr)
K32GetProcessMemoryInfo = extern("K32GetProcessMemoryInfo", (c_ptr, c_ptr, c_int), c_int)

_COUNTERS_SIZE = 72
_PEAK_WORKING_SET_OFFSET = 8
_WORKING_SET_OFFSET = 16


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
