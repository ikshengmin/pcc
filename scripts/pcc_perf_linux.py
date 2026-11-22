"""Bounded Linux user-mode task-clock sample transport for pcc_perf.

This layer samples the calling thread or one directly launched cooperative
child. It does not accept an external PID or infer a CPU distribution.
"""

from __future__ import annotations

import ctypes
import fcntl
import hashlib
import mmap
import os
import platform
import selectors
import stat
import struct
import subprocess
import sys
import threading
import time
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass

# Linux UAPI: linux/perf_event.h and the architecture syscall tables.
PERF_EVENT_OPEN = {"x86_64": 298, "aarch64": 241}
PERF_TYPE_SOFTWARE = 1
PERF_COUNT_SW_CPU_CLOCK = 0
PERF_ATTR_SIZE_VER0 = 64
PERF_SAMPLE_IP = 1 << 0
PERF_SAMPLE_TID = 1 << 1
PERF_SAMPLE_PERIOD = 1 << 8
PERF_FORMAT_TOTAL_TIME_ENABLED = 1 << 0
PERF_FORMAT_TOTAL_TIME_RUNNING = 1 << 1
PERF_FORMAT_LOST = 1 << 4
EVENT_READ = struct.Struct("=QQQQ")
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

# This preflight intentionally understands only native static ELF64 ET_EXEC.
ELF_HEADER = struct.Struct("<16sHHIQQQIHHHHHH")
ELF_PROGRAM_HEADER = struct.Struct("<IIQQQQQQ")
ELF64_LE_VERSION_1 = b"\x7fELF\x02\x01\x01"
ELF_ET_EXEC = 2
ELF_MACHINES = {"x86_64": 62, "aarch64": 183}
ELF_PT_LOAD = 1
ELF_PT_DYNAMIC = 2
ELF_PT_INTERP = 3
ELF_PF_X = 1
MAX_SEALED_ELF_BYTES = 128 * 1024 * 1024


@dataclass(frozen=True)
class TaskSample:
    ip: int
    pid: int
    tid: int
    period_ns: int


@dataclass(frozen=True)
class ExecutableRange:
    address: int
    end: int
    file_offset: int


@dataclass(frozen=True)
class ExecutableImage:
    machine: int
    ranges: tuple[ExecutableRange, ...]

    def file_offset(self, ip: int) -> int | None:
        for text in self.ranges:
            if text.address <= ip < text.end:
                return text.file_offset + ip - text.address
        return None


def read_executable_image(data: bytes) -> ExecutableImage:
    """Accept only a static, fixed-address image with file-backed text."""
    if len(data) < ELF_HEADER.size:
        raise ValueError("truncated ELF header")
    (ident, kind, machine, version, entry, phoff, _, _, ehsize, phsize,
     phnum, _, _, _) = ELF_HEADER.unpack_from(data)
    if (ident[:7] != ELF64_LE_VERSION_1 or kind != ELF_ET_EXEC or version != 1
            or machine not in ELF_MACHINES.values()):
        raise ValueError("expected supported little-endian static ELF64 ET_EXEC")
    if (ehsize != ELF_HEADER.size or phsize != ELF_PROGRAM_HEADER.size
            or phnum == 0 or phnum > 1024 or phoff < ehsize or phoff + phsize * phnum > len(data)):
        raise ValueError("invalid ELF program header table")
    ranges = []
    for i in range(phnum):
        tag, flags, offset, vaddr, _, filesz, memsz, align = ELF_PROGRAM_HEADER.unpack_from(
            data, phoff + i * phsize,
        )
        if tag in (ELF_PT_INTERP, ELF_PT_DYNAMIC):
            raise ValueError("dynamic ELF is not supported by the PC preflight")
        if tag != ELF_PT_LOAD:
            continue
        if (filesz > memsz or offset > len(data) or filesz > len(data) - offset
                or vaddr + memsz > (1 << 64)):
            raise ValueError("invalid ELF load extent")
        if align > 1 and ((align & (align - 1)) or (vaddr - offset) % align):
            raise ValueError("invalid ELF load alignment")
        if (flags & ELF_PF_X) and filesz:
            ranges.append(ExecutableRange(vaddr, vaddr + filesz, offset))
    ranges.sort(key=lambda text: text.address)
    if not ranges or any(next_range.address < text.end
                         for text, next_range in zip(ranges, ranges[1:])):
        raise ValueError("missing or overlapping executable ELF ranges")
    image = ExecutableImage(machine, tuple(ranges))
    if image.file_offset(entry) is None:
        raise ValueError("ELF entry is not file-backed executable text")
    return image


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
        PERF_SAMPLE_IP | PERF_SAMPLE_TID | PERF_SAMPLE_PERIOD,
        PERF_FORMAT_TOTAL_TIME_ENABLED | PERF_FORMAT_TOTAL_TIME_RUNNING | PERF_FORMAT_LOST,
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
def _task_ring(tid: int, period_ns: int, data_pages: int = RING_PAGES):
    fd = _open_task(tid, period_ns)
    try:
        size = os.sysconf("SC_PAGE_SIZE") * (1 + data_pages)
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


def _decode_event_read(data: bytes) -> tuple[int, int]:
    if len(data) != EVENT_READ.size:
        raise ValueError("truncated perf read counters")
    value, enabled, running, lost = EVENT_READ.unpack(data)
    if lost:
        raise ValueError(f"kernel counted {lost} lost perf samples")
    if not value or not enabled or running != enabled:
        raise ValueError("perf event did not run for its full enabled time")
    return value, running


def _read_capture(fd: int, mapping) -> tuple[list[TaskSample], int, int]:
    # Even if a full ring cannot enqueue PERF_RECORD_LOST, the explicit
    # kernel lost counter must be zero. Never normalize a multiplexed run.
    clock_ns, running_ns = _decode_event_read(os.read(fd, EVENT_READ.size))
    return _read_frozen_ring(mapping), clock_ns, running_ns


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
        samples, clock_ns, running_ns = _read_capture(fd, mapping)
    _verify_task(samples, os.getpid(), threading.get_native_id())
    return {"scope": "calling-thread-user-cpu-only", "period_ns": period_ns,
            "samples": len(samples), "distinct_ips": len({s.ip for s in samples}),
            "kernel_lost": 0, "clock_ns": clock_ns, "running_ns": running_ns}


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


@contextmanager
def _owned_child(argv, env, pass_fds):
    if sys.platform != "linux":
        raise OSError("child task-clock capture requires Linux")
    with subprocess.Popen(argv, env=env, stdin=subprocess.PIPE,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          bufsize=0, pass_fds=pass_fds) as child:
        try:
            _receive_marker(child.stdout, b"READY\n", 3)
            if child.poll() is not None:
                raise ValueError("owned child exited before sampling")
            yield child
            if child.wait(timeout=3) != 0:
                raise ValueError("owned child returned a failure")
            _verify_marker_eof(child.stdout)
            _verify_marker_eof(child.stderr)
        finally:
            if child.poll() is None:
                child.kill()
                child.wait(timeout=3)


def capture_cooperative_child(argv, *, env: dict, _pass_fds=()) -> list[TaskSample]:
    """Sample the directly spawned, single-task helper between READY and DONE."""
    with _owned_child(argv, env, _pass_fds) as child:
        with _task_ring(child.pid, 500_000) as (fd, mapping):
            with _enabled(fd):
                child.stdin.write(b"G")
                child.stdin.close()
                _receive_marker(child.stdout, b"DONE\n", 3)
            samples, _, _ = _read_capture(fd, mapping)
    _verify_task(samples, child.pid, child.pid)
    return samples


@dataclass(frozen=True)
class TaskCapture:
    tid: int
    clock_ns: int
    running_ns: int
    samples: tuple[TaskSample, ...]


def _owned_task_ids(child) -> tuple[int, ...]:
    if child.poll() is not None:
        raise ValueError("owned cohort exited before release")
    with os.scandir(f"/proc/{child.pid}/task") as entries:
        result = tuple(sorted(int(entry.name) for entry in entries if entry.name.isdecimal()))
    if child.pid not in result:
        raise ValueError("owned cohort has no leader")
    return result


def capture_cooperative_cohort(argv, *, env: dict, expected_tasks: int) -> list[TaskCapture]:
    """Qualify a bounded owned child with a fixed, fully busy task cohort.

    The child announces READY only after forming the pool, and leaves all
    members alive after DONE until an R acknowledgement. This protocol
    intentionally does not approximate a dynamic or arbitrary process.
    """
    if type(expected_tasks) is not int or not 1 <= expected_tasks <= 16:
        raise ValueError("expected-tasks must be between 1 and 16")
    with _owned_child(argv, env, ()) as child:
        tids = _owned_task_ids(child)
        if len(tids) != expected_tasks:
            raise ValueError(f"expected {expected_tasks} owned tasks, saw {len(tids)}")
        receipts = []
        with ExitStack() as rings:
            sessions = [(tid, *rings.enter_context(_task_ring(tid, 500_000))) for tid in tids]
            with ExitStack() as enabled:
                for _, fd, _ in sessions:
                    enabled.enter_context(_enabled(fd))
                child.stdin.write(b"G")
                _receive_marker(child.stdout, b"DONE\n", 3)
            if tids != _owned_task_ids(child):
                raise ValueError("owned cohort changed during capture")
            for tid, fd, mapping in sessions:
                samples, clock_ns, running_ns = _read_capture(fd, mapping)
                _verify_task(samples, child.pid, tid)
                receipts.append(TaskCapture(tid, clock_ns, running_ns, tuple(samples)))
        child.stdin.write(b"R")
        child.stdin.close()
    return receipts


@dataclass(frozen=True)
class SealedSample:
    sample: TaskSample
    file_offset: int | None


def capture_sealed_cooperative_child(binary, expected_sha256: str, *, env: dict) -> list[SealedSample]:
    """Tie the spawned image and its mapped sampled PCs to one open file."""
    if sys.platform != "linux":
        raise OSError("sealed task-clock capture requires Linux")
    fd = os.open(binary, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        metadata = os.fstat(fd)
        if (not stat.S_ISREG(metadata.st_mode) or not 0 < metadata.st_size <= MAX_SEALED_ELF_BYTES
                or metadata.st_mode & (stat.S_ISUID | stat.S_ISGID)):
            raise ValueError("sealed capture needs a bounded unprivileged regular ELF file")
        data = stream.read(metadata.st_size + 1)
        if len(data) != metadata.st_size or hashlib.sha256(data).hexdigest() != expected_sha256:
            raise ValueError("sealed executable has a different SHA-256")
        image = read_executable_image(data)
        if image.machine != ELF_MACHINES.get(platform.machine()):
            raise ValueError("sealed executable is not for the local instruction set")
        # Do not resolve the path again at exec time. Keep the original inode
        # open in the cooperative child, and recheck the bytes after capture.
        samples = capture_cooperative_child(
            [f"/proc/self/fd/{fd}"], env=env, _pass_fds=(fd,),
        )
        stream.seek(0)
        remaining = metadata.st_size
        digest = hashlib.sha256()
        while remaining:
            chunk = stream.read(min(remaining, 1 << 20))
            if not chunk:
                break
            digest.update(chunk)
            remaining -= len(chunk)
        if remaining or stream.read(1) or digest.hexdigest() != expected_sha256:
            raise ValueError("sealed executable changed during capture")
    return [SealedSample(sample, image.file_offset(sample.ip)) for sample in samples]
