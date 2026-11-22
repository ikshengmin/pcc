"""Bounded Linux user-mode task-clock sample transport for pcc_perf.

This layer samples the calling thread or one directly launched cooperative
child. It does not accept an external PID or infer a CPU distribution.
"""

from __future__ import annotations

import ctypes
import fcntl
import mmap
import os
import platform
import selectors
import struct
import subprocess
import sys
import threading
import time
from contextlib import contextmanager
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
PERF_RECORD_MISC_CPUMODE_MASK = 0b111
PERF_RECORD_MISC_USER = 2
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
        kind, misc, length = RECORD_HEADER.unpack(take(tail, RECORD_HEADER.size))
        if length < RECORD_HEADER.size or length > head - tail:
            raise ValueError("invalid perf record size")
        payload = take(tail + RECORD_HEADER.size, length - RECORD_HEADER.size)
        if kind in (PERF_RECORD_LOST, PERF_RECORD_LOST_SAMPLES):
            raise ValueError("perf lost samples; no attribution is valid")
        if kind != PERF_RECORD_SAMPLE or len(payload) != SAMPLE.size:
            raise ValueError(f"unexpected perf record type or layout: {kind}")
        if (misc & PERF_RECORD_MISC_CPUMODE_MASK) != PERF_RECORD_MISC_USER:
            raise ValueError("unexpected non-user perf sample")
        sample = TaskSample(*SAMPLE.unpack(payload))
        if not sample.ip or not sample.pid or not sample.tid or not sample.period_ns:
            raise ValueError("incomplete perf sample")
        samples.append(sample)
        tail += length
    return samples


def _open_task(tid: int, period_ns: int) -> int:
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
                 ctypes.c_int(tid), ctypes.c_int(-1), ctypes.c_int(-1),
                 ctypes.c_ulong(PERF_FLAG_FD_CLOEXEC))
    if fd < 0:
        code = ctypes.get_errno()
        raise OSError(code, os.strerror(code))
    return fd


@contextmanager
def _task_ring(tid: int, period_ns: int):
    fd = _open_task(tid, period_ns)
    try:
        size = os.sysconf("SC_PAGE_SIZE") * (1 + RING_PAGES)
        with mmap.mmap(fd, size, flags=mmap.MAP_SHARED,
                       prot=mmap.PROT_READ | mmap.PROT_WRITE) as mapping:
            yield fd, mapping
    finally:
        os.close(fd)


@contextmanager
def _enabled(fd: int):
    fcntl.ioctl(fd, PERF_EVENT_IOC_ENABLE)
    try:
        yield
    finally:
        fcntl.ioctl(fd, PERF_EVENT_IOC_DISABLE)


def _verify_task(samples: list[TaskSample], pid: int, tid: int) -> None:
    if not samples:
        raise ValueError("kernel returned no task-clock samples")
    if {(s.pid, s.tid) for s in samples} != {(pid, tid)}:
        raise ValueError("task-clock capture included an unexpected thread")


def task_clock_preflight(cpu_ms: int = 80) -> dict:
    if not 10 <= cpu_ms <= 250:
        raise ValueError("cpu-ms must be between 10 and 250")
    period_ns = 500_000
    with _task_ring(0, period_ns) as (fd, mapping):
        deadline = time.monotonic_ns() + 3_000_000_000
        start = time.thread_time_ns()
        with _enabled(fd):
            while time.thread_time_ns() - start < cpu_ms * 1_000_000:
                if time.monotonic_ns() >= deadline:
                    raise ValueError("task-clock preflight exceeded wall limit")
        samples = _read_frozen_ring(mapping)
    _verify_task(samples, os.getpid(), threading.get_native_id())
    return {"scope": "calling-thread-user-cpu-only", "period_ns": period_ns,
            "samples": len(samples), "distinct_ips": len({s.ip for s in samples})}


def _receive_marker(pipe, marker: bytes, timeout: float) -> None:
    # Read only the fixed protocol width. A broken child cannot grow a
    # parent-side output buffer or hold a ready pipe open indefinitely.
    fd = pipe.fileno()
    deadline = time.monotonic() + timeout
    with selectors.DefaultSelector() as selector:
        selector.register(fd, selectors.EVENT_READ)
        remainder = marker
        while remainder:
            budget = deadline - time.monotonic()
            if budget <= 0 or not selector.select(budget):
                raise ValueError("owned child marker timed out")
            data = os.read(fd, len(remainder))
            if not data or not remainder.startswith(data):
                raise ValueError("owned child sent an invalid marker")
            remainder = remainder[len(data):]


def _verify_marker_eof(pipe) -> None:
    # A descendant holding a copied pipe is a protocol error, not a reason
    # to wait on it after the directly owned process has exited.
    fd = pipe.fileno()
    os.set_blocking(fd, False)
    try:
        if os.read(fd, 1):
            raise ValueError("owned child sent unexpected output")
    except BlockingIOError as exc:
        raise ValueError("owned child left an output pipe open") from exc


def capture_cooperative_child(argv, *, env: dict) -> list[TaskSample]:
    """Sample the directly spawned, single-task helper between READY and DONE.

    Internal test/benchmark transport, for an owned executable with the
    bounded protocol only. No shell, external PID or inherited events.
    """
    if sys.platform != "linux":
        raise OSError("child task-clock capture requires Linux")
    with subprocess.Popen(argv, env=env, stdin=subprocess.PIPE,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          bufsize=0) as child:
        try:
            _receive_marker(child.stdout, b"READY\n", 3)
            if child.poll() is not None:
                raise ValueError("owned child exited before sampling")
            with _task_ring(child.pid, 500_000) as (fd, mapping):
                with _enabled(fd):
                    child.stdin.write(b"G")
                    child.stdin.close()
                    _receive_marker(child.stdout, b"DONE\n", 3)
                samples = _read_frozen_ring(mapping)
            if child.wait(timeout=3) != 0:
                raise ValueError("owned child returned a failure")
            _verify_marker_eof(child.stdout)
            _verify_marker_eof(child.stderr)
        finally:
            if child.poll() is None:
                child.kill()
                child.wait(timeout=3)
    _verify_task(samples, child.pid, child.pid)
    return samples
