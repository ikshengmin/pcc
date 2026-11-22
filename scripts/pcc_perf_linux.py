"""Bounded Linux user-mode task-clock sample transport for pcc_perf.

This layer only samples the calling thread. It does not attach to a PID,
symbolize addresses, or infer a program's CPU distribution.
"""

from __future__ import annotations

import ctypes
import fcntl
import mmap
import os
import platform
import struct
import sys
import threading
import time
from dataclasses import dataclass

# Linux UAPI: linux/perf_event.h and the architecture syscall tables.
PERF_EVENT_OPEN = {"x86_64": 298, "aarch64": 241}
PERF_TYPE_SOFTWARE = 1
PERF_COUNT_SW_CPU_CLOCK = 0
PERF_ATTR_SIZE_VER0 = 64
PERF_SAMPLE_IP = 1 << 0
PERF_SAMPLE_TID = 1 << 1
PERF_SAMPLE_PERIOD = 1 << 8
PERF_ATTR_DISABLED = 1 << 0
PERF_ATTR_EXCLUDE_KERNEL = 1 << 5
PERF_ATTR_EXCLUDE_HV = 1 << 6
PERF_FLAG_FD_CLOEXEC = 1 << 3
PERF_EVENT_IOC_ENABLE = 0x2400
PERF_EVENT_IOC_DISABLE = 0x2401
PERF_RECORD_LOST = 2
PERF_RECORD_SAMPLE = 9
PERF_RECORD_LOST_SAMPLES = 13
MMAP_DATA_HEAD = 1024
RECORD_HEADER = struct.Struct("=IHH")
SAMPLE = struct.Struct("=QIIQ")
RING_PAGES = 32


@dataclass(frozen=True)
class TaskSample:
    ip: int
    pid: int
    tid: int
    period_ns: int


def _read_frozen_ring(mapping) -> list[TaskSample]:
    """Read only after disabling this non-inherited event."""
    if len(mapping) < MMAP_DATA_HEAD + 32:
        raise ValueError("truncated perf metadata")
    head, tail, data_offset, size = struct.unpack_from("=QQQQ", mapping, MMAP_DATA_HEAD)
    if (data_offset < MMAP_DATA_HEAD + 32 or size == 0 or size & (size - 1)
            or data_offset + size > len(mapping) or head < tail or head - tail > size):
        raise ValueError("invalid perf ring metadata or overrun")
    # The cursor is monotonic, but each record may straddle the ring end.
    def take(position, length):
        offset = position & (size - 1)
        first = min(length, size - offset)
        return (mapping[data_offset + offset:data_offset + offset + first]
                + mapping[data_offset:data_offset + length - first])

    samples = []
    while tail < head:
        if head - tail < RECORD_HEADER.size:
            raise ValueError("truncated perf record header")
        kind, _, length = RECORD_HEADER.unpack(take(tail, RECORD_HEADER.size))
        if length < RECORD_HEADER.size or length > head - tail:
            raise ValueError("invalid perf record size")
        payload = take(tail + RECORD_HEADER.size, length - RECORD_HEADER.size)
        if kind in (PERF_RECORD_LOST, PERF_RECORD_LOST_SAMPLES):
            raise ValueError("perf lost samples; no attribution is valid")
        if kind != PERF_RECORD_SAMPLE or len(payload) != SAMPLE.size:
            raise ValueError(f"unexpected perf record type or layout: {kind}")
        sample = TaskSample(*SAMPLE.unpack(payload))
        if not sample.ip or not sample.pid or not sample.tid or not sample.period_ns:
            raise ValueError("incomplete perf sample")
        samples.append(sample)
        tail += length
    return samples


def _open_self(period_ns: int) -> int:
    if (sys.platform != "linux" or sys.byteorder != "little"
            or platform.machine() not in PERF_EVENT_OPEN):
        raise OSError("task-clock preflight supports Linux x86-64 and arm64")
    attr = bytearray(PERF_ATTR_SIZE_VER0)
    struct.pack_into(
        "=IIQQQQQ", attr, 0, PERF_TYPE_SOFTWARE, len(attr),
        PERF_COUNT_SW_CPU_CLOCK, period_ns,
        PERF_SAMPLE_IP | PERF_SAMPLE_TID | PERF_SAMPLE_PERIOD, 0,
        PERF_ATTR_DISABLED | PERF_ATTR_EXCLUDE_KERNEL | PERF_ATTR_EXCLUDE_HV,
    )
    payload = (ctypes.c_char * len(attr)).from_buffer(attr)
    libc = ctypes.CDLL(None, use_errno=True)
    syscall = libc.syscall
    syscall.restype = ctypes.c_long
    fd = syscall(ctypes.c_long(PERF_EVENT_OPEN[platform.machine()]), payload,
                 ctypes.c_int(0), ctypes.c_int(-1), ctypes.c_int(-1),
                 ctypes.c_ulong(PERF_FLAG_FD_CLOEXEC))
    if fd < 0:
        code = ctypes.get_errno()
        raise OSError(code, os.strerror(code))
    return fd


def task_clock_preflight(cpu_ms: int = 80) -> dict:
    if not 10 <= cpu_ms <= 250:
        raise ValueError("cpu-ms must be between 10 and 250")
    page_size = os.sysconf("SC_PAGE_SIZE")
    period_ns = 500_000
    fd = _open_self(period_ns)
    try:
        with mmap.mmap(fd, page_size * (1 + RING_PAGES), flags=mmap.MAP_SHARED,
                       prot=mmap.PROT_READ | mmap.PROT_WRITE) as mapping:
            deadline = time.monotonic_ns() + 3_000_000_000
            start = time.thread_time_ns()
            fcntl.ioctl(fd, PERF_EVENT_IOC_ENABLE)
            try:
                while time.thread_time_ns() - start < cpu_ms * 1_000_000:
                    if time.monotonic_ns() >= deadline:
                        raise ValueError("task-clock preflight exceeded wall limit")
            finally:
                fcntl.ioctl(fd, PERF_EVENT_IOC_DISABLE)
            samples = _read_frozen_ring(mapping)
    finally:
        os.close(fd)
    if not samples:
        raise ValueError("kernel returned no task-clock samples")
    if {(s.pid, s.tid) for s in samples} != {(os.getpid(), threading.get_native_id())}:
        raise ValueError("task-clock preflight captured an unexpected thread")
    return {"scope": "calling-thread-user-cpu-only", "period_ns": period_ns,
            "samples": len(samples), "distinct_ips": len({s.ip for s in samples})}
