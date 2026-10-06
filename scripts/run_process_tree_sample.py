#!/usr/bin/env python3
"""Run one command with process-tree RSS sampling and a hard watchdog.

The target runs in a fresh session. Every sample records synchronized aggregate
RSS, retaining observed descendants after reparenting by their OS process-start
identity. Stdout/stderr remain durable separate artifacts. The optional
repository performance lock uses the same implementation as PCC's A/B tools.
"""

from __future__ import annotations

import argparse
import contextlib
import ctypes
import datetime as dt
import errno
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import time
from typing import NamedTuple

if os.name == "nt":
    # The shared memory selector also serves the Windows Job-object guard;
    # the POSIX performance-lock implementation imports fcntl.
    compile_ab = None
else:
    try:
        from . import run_pcc_compile_ab as compile_ab
    except ImportError:
        import run_pcc_compile_ab as compile_ab


_INTERRUPT_REQUESTED = False
_PROCESS_TABLE_TIMEOUTS_S = (5.0, 20.0)
# A native 12-worker phase can briefly delay Darwin's global `ps` snapshot.
# Retry once with a bounded longer wait before killing a healthy, capped tree;
# the RSS cap and process-group watchdog remain active on the next sample.
_SAFETY_PROCESS_TABLE_TIMEOUTS_S = (1.0, 3.0)
_GIB = 1024 * 1024 * 1024
DEFAULT_HOST_MEMORY_RESERVE_BYTES = 8 * _GIB
_MIN_PRESSURED_SWAP_FREE_BYTES = 4 * _GIB
# On a large-RAM / small-swap host (e.g. 96 GiB RAM with a 4 GiB dynamic swap)
# ``vm.swapusage`` always looks "pressured" relative to the tiny swap file even
# though the machine has tens of GiB of reclaimable physical memory and the
# capped tree (<= max_tree_rss + reserve) will never thrash.  The swap-pressure
# refusal is waived only when reclaimable physical memory comfortably clears
# this multiple of the required budget; the hard reclaimable floor below still
# fails closed on a genuinely memory-starved host.
_SWAP_PRESSURE_RECLAIMABLE_MARGIN = 2
_DARWIN_PROC_PIDINFO = None


class _ProcessIdentity(NamedTuple):
    session_id: int
    parent_pid: int
    start_time: tuple[int, int]


class _DarwinBsdInfo(ctypes.Structure):
    # Public Darwin proc_bsdinfo ABI, including both kernel start-time fields:
    # https://github.com/apple-oss-distributions/xnu/blob/main/bsd/sys/proc_info.h
    _fields_ = [
        (name, ctypes.c_uint32)
        for name in (
            "flags", "status", "xstatus", "pid", "ppid", "uid", "gid",
            "ruid", "rgid", "svuid", "svgid", "reserved",
        )
    ] + [
        ("comm", ctypes.c_char * 16),
        ("name", ctypes.c_char * 32),
        ("nfiles", ctypes.c_uint32),
        ("pgid", ctypes.c_uint32),
        ("jobc", ctypes.c_uint32),
        ("ttydev", ctypes.c_uint32),
        ("ttypgid", ctypes.c_uint32),
        ("nice", ctypes.c_int32),
        ("start_sec", ctypes.c_uint64),
        ("start_usec", ctypes.c_uint64),
    ]


def _darwin_bsd_info(pid: int) -> _DarwinBsdInfo | None:
    global _DARWIN_PROC_PIDINFO
    if _DARWIN_PROC_PIDINFO is None:
        function = ctypes.CDLL("/usr/lib/libproc.dylib").proc_pidinfo
        function.argtypes = [
            ctypes.c_int, ctypes.c_int, ctypes.c_uint64,
            ctypes.c_void_p, ctypes.c_int,
        ]
        function.restype = ctypes.c_int
        _DARWIN_PROC_PIDINFO = function
    info = _DarwinBsdInfo()
    # PROC_PIDTBSDINFO = 3. A short result is not a usable process identity.
    size = ctypes.sizeof(info)
    if _DARWIN_PROC_PIDINFO(pid, 3, 0, ctypes.byref(info), size) != size:
        return None
    return info if info.pid == pid else None


def _process_identity(pid: int) -> _ProcessIdentity | None:
    """Read a kernel start identity; ps's second-resolution lstart is unsafe."""
    try:
        if sys.platform.startswith("linux"):
            # comm can contain spaces and closing parentheses. Fields after its
            # final ')' start at stat field 3; starttime is field 22.
            fields = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
            return _ProcessIdentity(
                int(fields[3]), int(fields[1]), (int(fields[19]), 0),
            )
        if sys.platform == "darwin":
            before = _darwin_bsd_info(pid)
            if before is None:
                return None
            session_id = os.getsid(pid)
            after = _darwin_bsd_info(pid)
            if after is None or (before.start_sec, before.start_usec) != (
                after.start_sec, after.start_usec,
            ):
                return None
            return _ProcessIdentity(
                session_id, after.ppid, (after.start_sec, after.start_usec),
            )
    except (OSError, IndexError, ValueError):
        return None
    raise ProcessTreeSampleError("process identities unavailable on " + sys.platform)


def _owned_process_pids(observed_processes, current_pids):
    """Retain proven births and discover children in their live sessions.

    A numeric session ID alone cannot establish ownership after its last known
    process exits: both PIDs and SIDs can be reused. Only a matching start
    identity anchors a session, and a reused observed PID is never readmitted.
    Fresh kernel parent IDs also avoid trusting a stale ps ancestry row.
    """
    current = {}
    for pid in current_pids:
        identity = _process_identity(pid)
        if identity is not None:
            previous = observed_processes.get(pid)
            if previous is None or previous.start_time == identity.start_time:
                current[pid] = identity
    owned = current.keys() & observed_processes.keys()
    while True:
        sessions = {current[pid].session_id for pid in owned}
        discovered = {
            pid for pid, identity in current.items()
            if pid not in owned and (
                identity.parent_pid in owned or identity.session_id in sessions
            )
        }
        if not discovered:
            break
        owned.update(discovered)
    observed_processes.update({pid: current[pid] for pid in owned})
    return owned


def _owned_tree_rows(table, observed_processes):
    return {
        pid: table[pid] for pid in sorted(_owned_process_pids(observed_processes, table))
    }


def _signal_owned_pid(pid, expected_identity, signum):
    """Revalidate the process birth at delivery; pin Linux targets with pidfds."""
    descriptor = None
    try:
        if hasattr(os, "pidfd_open") and hasattr(signal, "pidfd_send_signal"):
            try:
                descriptor = os.pidfd_open(pid, 0)
            except OSError as exc:
                if exc.errno != errno.ENOSYS:
                    return False
        current = _process_identity(pid)
        if current is None or current.start_time != expected_identity.start_time:
            return False
        if descriptor is None:
            os.kill(pid, signum)
        else:
            signal.pidfd_send_signal(descriptor, signum)
        return True
    except (ProcessLookupError, PermissionError):
        return False
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _request_interrupt(_signum, _frame) -> None:
    global _INTERRUPT_REQUESTED
    _INTERRUPT_REQUESTED = True


class ProcessTreeSampleError(RuntimeError):
    def __init__(self, message: str, *, retry_count: int = 0) -> None:
        super().__init__(message)
        self.retry_count = retry_count


def _persist(path: Path, payload: object) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _process_table(
    *,
    timeouts_s: tuple[float, ...] = _PROCESS_TABLE_TIMEOUTS_S,
    include_command: bool = True,
) -> tuple[dict[int, tuple[int, int, str]], int]:
    run = None
    retry_count = 0
    for timeout_s in timeouts_s:
        try:
            run = subprocess.run(
                (
                    ["ps", "-ww", "-Ao", "pid=,ppid=,rss=,command="]
                    if include_command
                    else ["ps", "-Ao", "pid=,ppid=,rss="]
                ),
                check=False,
                text=True,
                capture_output=True,
                timeout=timeout_s,
            )
            break
        except subprocess.TimeoutExpired as exc:
            retry_count += 1
            if timeout_s == timeouts_s[-1]:
                raise ProcessTreeSampleError(
                    "ps timed out after bounded process-table retries: "
                    + ",".join(str(value) for value in timeouts_s)
                    + " seconds",
                    retry_count=retry_count,
                ) from exc
    if run is None:
        raise ProcessTreeSampleError("ps produced no process-table result")
    if run.returncode != 0:
        raise ProcessTreeSampleError("ps failed: " + run.stderr.strip())
    rows: dict[int, tuple[int, int, str]] = {}
    for raw in run.stdout.splitlines():
        fields = raw.strip().split(None, 3)
        if len(fields) < 3:
            continue
        try:
            pid = int(fields[0])
            ppid = int(fields[1])
            rss_bytes = int(fields[2]) * 1024
        except ValueError:
            continue
        command = fields[3] if len(fields) >= 4 else ""
        rows[pid] = (ppid, rss_bytes, command)
    return rows, retry_count


def _process_command(pid: int, *, timeout_s: float = 0.25) -> str:
    """Read one argv after the safety-critical lean RSS table succeeds."""

    try:
        run = subprocess.run(
            ["ps", "-ww", "-p", str(int(pid)), "-o", "command="],
            check=False,
            text=True,
            capture_output=True,
            timeout=timeout_s,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    if run.returncode != 0:
        return ""
    return run.stdout.strip()


def _tree_rows(
    root_pid: int,
    table: dict[int, tuple[int, int, str]],
) -> dict[int, tuple[int, int, str]]:
    children: dict[int, list[int]] = {}
    for pid, (ppid, _rss, _command) in table.items():
        children.setdefault(ppid, []).append(pid)
    selected: dict[int, tuple[int, int, str]] = {}
    pending = [root_pid]
    while pending:
        pid = pending.pop()
        if pid in selected:
            continue
        row = table.get(pid)
        if row is None:
            continue
        selected[pid] = row
        pending.extend(children.get(pid, ()))
    return selected


def _termination_process_states(timeout_s: float) -> dict[int, str] | None:
    """Read liveness within the remaining grace, excluding exited zombies."""
    try:
        run = subprocess.run(
            ["ps", "-Ao", "pid=,stat="],
            capture_output=True,
            text=True,
            timeout=timeout_s,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if run.returncode != 0:
        return None
    states = {}
    for line in run.stdout.splitlines():
        fields = line.split()
        if len(fields) == 2 and fields[0].isdigit():
            states[int(fields[0])] = fields[1]
    return states


def _terminate_owned_processes(
    process: subprocess.Popen[bytes],
    observed_processes: dict[int, _ProcessIdentity],
) -> None:
    term_signaled: set[int] = set()

    def signal_owned(signum: int, states=None) -> None:
        owned = _owned_process_pids(
            observed_processes,
            observed_processes if states is None else states,
        )
        for pid in sorted(owned):
            if pid == os.getpid():
                continue
            if pid == process.pid and process.returncode is not None:
                # poll()/wait() retired this child. Its numeric PID/PGID is
                # never a signal target again, including after successful exit.
                continue
            if signum == signal.SIGTERM and pid in term_signaled:
                continue
            if _signal_owned_pid(pid, observed_processes[pid], signum):
                if signum == signal.SIGTERM:
                    term_signaled.add(pid)
        if process.pid not in observed_processes and process.returncode is None:
            # Identity telemetry can fail before the first sample. Popen owns
            # this unreaped child and checks its exit state before signaling.
            with contextlib.suppress(ProcessLookupError, PermissionError):
                process.send_signal(signum)

    previous_sigint = signal.getsignal(signal.SIGINT)
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    try:
        deadline = time.monotonic() + 2.0
        signal_owned(signal.SIGTERM, _termination_process_states(0.25))
        while True:
            remaining_s = deadline - time.monotonic()
            if remaining_s <= 0:
                break
            states = _termination_process_states(min(0.25, remaining_s))
            if states is None:
                # Failed liveness telemetry cannot extend the hard deadline.
                break
            # Discover children before reaping the root, while its start
            # identity can still establish ownership of its surviving session.
            signal_owned(signal.SIGTERM, states)
            owned = _owned_session_pids(process.pid, observed_processes, states)
            root_exited = process.poll() is not None
            if root_exited and not any(
                not states[pid].startswith("Z") for pid in owned
            ):
                break
            # The supervisor may exit first. Its living descendants retain
            # only the remainder of the same bounded grace to finish flushing.
            time.sleep(min(0.05, max(0.0, deadline - time.monotonic())))
        signal_owned(signal.SIGKILL)
        with contextlib.suppress(subprocess.TimeoutExpired):
            process.wait(timeout=2)
    finally:
        signal.signal(signal.SIGINT, previous_sigint)


def _owned_session_pids(root_pid, observed_processes, current_pids):
    return _owned_process_pids(observed_processes, current_pids) - {root_pid}


def _recorded_environment(environment: dict[str, str]) -> dict[str, str]:
    if environment.get("PCC_STAGE1_CHECKPOINT_STAGE") == "1":
        # The source launcher binds unknown settings by digest.  Preserve that
        # privacy boundary in durable watchdog receipts too.
        from bootstrap_stage1_checkpoint import recorded_guard_environment

        return recorded_guard_environment(environment)
    fixed = {
        "HOME",
        "LANG",
        "PATH",
        "PYTHONDONTWRITEBYTECODE",
        "PYTHONHASHSEED",
        "PYTHONPYCACHEPREFIX",
        "TMPDIR",
        "XDG_CACHE_HOME",
    }
    return {
        key: environment[key]
        for key in sorted(environment)
        if key.startswith("PCC_") or key in fixed
    }


def _parse_scaled_bytes(raw: str) -> int:
    match = re.fullmatch(r"([0-9]+(?:\.[0-9]+)?)([KMGT]?)", raw.strip())
    if match is None:
        raise ProcessTreeSampleError("cannot parse resource size: " + raw)
    scale = {
        "": 1,
        "K": 1024,
        "M": 1024 * 1024,
        "G": _GIB,
        "T": 1024 * _GIB,
    }[match.group(2)]
    return int(float(match.group(1)) * scale)


def _parse_vm_stat_reclaimable(raw: str) -> int:
    header = re.search(r"page size of ([0-9]+) bytes", raw)
    if header is None:
        raise ProcessTreeSampleError("vm_stat did not report its page size")
    page_size = int(header.group(1))
    pages: dict[str, int] = {}
    for line in raw.splitlines():
        name, separator, value = line.partition(":")
        if separator != ":":
            continue
        digits = value.strip().rstrip(".")
        if digits.isdigit():
            pages[name.strip()] = int(digits)
    reclaimable_names = (
        "Pages free",
        "Pages inactive",
        "Pages speculative",
        "Pages purgeable",
    )
    if not any(name in pages for name in reclaimable_names):
        raise ProcessTreeSampleError("vm_stat has no reclaimable-page counters")
    return sum(pages.get(name, 0) for name in reclaimable_names) * page_size


def _parse_swapusage(raw: str) -> tuple[int, int, int]:
    values = {}
    for name in ("total", "used", "free"):
        match = re.search(r"\b" + name + r"\s*=\s*([0-9.]+[KMGT]?)", raw)
        if match is None:
            raise ProcessTreeSampleError("vm.swapusage is missing " + name)
        values[name] = _parse_scaled_bytes(match.group(1))
    return values["total"], values["used"], values["free"]


def _darwin_resource_observation() -> dict[str, int]:
    if sys.platform != "darwin":
        raise ProcessTreeSampleError(
            "Darwin memory preflight is unavailable on " + sys.platform
        )
    vm_stat = subprocess.run(
        ["/usr/bin/vm_stat"],
        check=False,
        text=True,
        capture_output=True,
        timeout=5,
    )
    if vm_stat.returncode != 0:
        raise ProcessTreeSampleError("vm_stat failed: " + vm_stat.stderr.strip())
    swap = subprocess.run(
        ["/usr/sbin/sysctl", "-n", "vm.swapusage"],
        check=False,
        text=True,
        capture_output=True,
        timeout=5,
    )
    if swap.returncode != 0:
        raise ProcessTreeSampleError(
            "vm.swapusage failed: " + swap.stderr.strip()
        )
    reclaimable_bytes = _parse_vm_stat_reclaimable(vm_stat.stdout)
    swap_total_bytes, swap_used_bytes, swap_free_bytes = _parse_swapusage(
        swap.stdout
    )
    disk_free_bytes = shutil.disk_usage("/").free
    return {
        "platform": "darwin", "reclaimable_bytes": reclaimable_bytes,
        "disk_free_bytes": disk_free_bytes, "swap_total_bytes": swap_total_bytes,
        "swap_used_bytes": swap_used_bytes, "swap_free_bytes": swap_free_bytes,
    }


def _validate_darwin_resource_observation(
    *, max_tree_rss_bytes: int, reserve_bytes: int, reclaimable_bytes: int,
    disk_free_bytes: int, swap_total_bytes: int, swap_used_bytes: int,
    swap_free_bytes: int, platform: str = "darwin",
) -> dict:
    required_bytes = int(max_tree_rss_bytes) + int(reserve_bytes)
    if reclaimable_bytes < required_bytes:
        raise ProcessTreeSampleError(
            "insufficient reclaimable memory for guarded process tree"
        )
    if disk_free_bytes < required_bytes:
        raise ProcessTreeSampleError(
            "insufficient disk space for guarded process tree and swap reserve"
        )
    ample_physical_headroom = (
        reclaimable_bytes
        >= required_bytes * _SWAP_PRESSURE_RECLAIMABLE_MARGIN
    )
    if (
        swap_total_bytes > 0
        and swap_used_bytes * 2 > swap_total_bytes
        and swap_free_bytes < _MIN_PRESSURED_SWAP_FREE_BYTES
        and not ample_physical_headroom
    ):
        raise ProcessTreeSampleError(
            "swap is already pressured; refusing guarded process tree"
        )
    return {
        "max_tree_rss_bytes": int(max_tree_rss_bytes),
        "reserve_bytes": int(reserve_bytes),
        "required_reclaimable_and_disk_free_bytes": required_bytes,
        "reclaimable_bytes": reclaimable_bytes,
        "disk_free_bytes": disk_free_bytes,
        "swap_total_bytes": swap_total_bytes,
        "swap_used_bytes": swap_used_bytes,
        "swap_free_bytes": swap_free_bytes,
        "swap_pressure_waived_by_reclaimable": bool(ample_physical_headroom),
    }


def _darwin_resource_preflight(*, max_tree_rss_bytes: int, reserve_bytes: int) -> dict:
    return _validate_darwin_resource_observation(
        max_tree_rss_bytes=max_tree_rss_bytes, reserve_bytes=reserve_bytes,
        **_darwin_resource_observation(),
    )


def _linux_memory_observation(proc_root: Path = Path("/proc")) -> dict:
    """MemAvailable capped by every visible ancestor cgroup memory limit."""
    memory = {}
    for line in (proc_root / "meminfo").read_text().splitlines():
        fields = line.split()
        if len(fields) >= 2:
            memory[fields[0].rstrip(":")] = int(fields[1]) * 1024
    if memory.get("MemAvailable", 0) <= 0:
        raise ProcessTreeSampleError("Linux MemAvailable is unavailable")
    available = memory["MemAvailable"]
    memberships = []
    for line in (proc_root / "self/cgroup").read_text().splitlines():
        _identifier, controllers, path = line.split(":", 2)
        if not controllers or "memory" in controllers.split(","):
            memberships.append(("cgroup2" if not controllers else "cgroup", path))
    limits = []
    matched_memberships = set()
    for line in (proc_root / "self/mountinfo").read_text().splitlines():
        fields = line.split()
        separator = fields.index("-")
        kind = fields[separator + 1]
        if kind not in ("cgroup", "cgroup2"):
            continue
        if kind == "cgroup" and "memory" not in fields[separator + 3].split(","):
            continue
        decode = lambda text: re.sub(r"\\([0-7]{3})", lambda match: chr(int(match[1], 8)), text)
        mount_root = Path(decode(fields[3]))
        mount = Path(decode(fields[4]))
        for member_kind, member_path in memberships:
            if member_kind != kind:
                continue
            try:
                relative = Path(os.path.normpath(member_path)).relative_to(mount_root)
            except ValueError:
                # A cgroup namespace can report its own root as '/', while
                # mountinfo retains the host-side root of that same mount.
                if member_path == "/":
                    relative = Path(".")
                else:
                    continue
            matched_memberships.add((member_kind, member_path))
            current = mount / relative
            while True:
                limit_file = current / ("memory.max" if kind == "cgroup2" else "memory.limit_in_bytes")
                usage_file = current / ("memory.current" if kind == "cgroup2" else "memory.usage_in_bytes")
                if limit_file.is_file():
                    raw = limit_file.read_text().strip()
                    if raw != "max":
                        limit = int(raw)
                        usage = int(usage_file.read_text().strip())
                        if limit < 0 or usage < 0:
                            raise ProcessTreeSampleError("invalid Linux cgroup memory observation")
                        remaining = max(0, limit - usage)
                        available = min(available, remaining)
                        limits.append({"path": str(current), "limit_bytes": limit,
                                       "usage_bytes": usage, "remaining_bytes": remaining})
                if current == mount:
                    break
                current = current.parent
    return {"platform": "linux", "available_bytes": available,
            "mem_available_bytes": memory["MemAvailable"], "cgroup_limits": limits,
            "cgroup_limits_verified": all(member in matched_memberships for member in memberships)}


def _windows_memory_observation() -> dict:
    import ctypes

    class MemoryStatus(ctypes.Structure):
        _fields_ = [("length", ctypes.c_uint32), ("load", ctypes.c_uint32)] + [
            (name, ctypes.c_uint64) for name in (
                "total_physical", "available_physical", "total_pagefile",
                "available_pagefile", "total_virtual", "available_virtual",
                "available_extended_virtual",
            )
        ]

    status = MemoryStatus()
    status.length = ctypes.sizeof(status)
    query = ctypes.WinDLL("kernel32", use_last_error=True).GlobalMemoryStatusEx
    query.argtypes = [ctypes.POINTER(MemoryStatus)]
    query.restype = ctypes.c_int
    if not query(ctypes.byref(status)):
        raise ProcessTreeSampleError("Windows available physical memory is unavailable")
    return {"platform": "win32", "available_bytes": int(status.available_physical)}


def _host_memory_observation() -> dict:
    if sys.platform == "darwin":
        return _darwin_resource_observation()
    if sys.platform.startswith("linux"):
        return _linux_memory_observation()
    if sys.platform == "win32":
        return _windows_memory_observation()
    raise ProcessTreeSampleError("automatic memory budget is unavailable on " + sys.platform)


def configured_host_memory_reserve_bytes(environment=None) -> int:
    """Parse the existing shared Bootstrap reserve without observing memory."""
    environment = os.environ if environment is None else environment
    key = "PCC_BOOTSTRAP_HOST_MEMORY_RESERVE_BYTES"
    raw = str(environment.get(key, "") or "").strip()
    try:
        value = int(raw) if raw else DEFAULT_HOST_MEMORY_RESERVE_BYTES
    except ValueError as exc:
        raise ProcessTreeSampleError("invalid bootstrap resource limit: " + key + "=" + raw) from exc
    if value <= 0:
        raise ProcessTreeSampleError("invalid bootstrap resource limit: " + key + "=" + str(value))
    if (value > DEFAULT_HOST_MEMORY_RESERVE_BYTES
            and environment.get("PCC_BOOTSTRAP_UNSAFE_HIGH_MEMORY_JOBS") != "1"):
        raise ProcessTreeSampleError("unsafe bootstrap resource limit: " + key + "=" + str(value))
    return value


def select_tree_memory_budget(
    explicit: int, *, default_ceiling: int = 16 * _GIB,
    reserve_bytes=None, external_budget=None, observation=None,
) -> dict:
    """Select auto limits; keep explicit and already-guarded caps authoritative."""
    if reserve_bytes is None:
        reserve_bytes = configured_host_memory_reserve_bytes()
    if explicit < 0 or default_ceiling <= 0 or reserve_bytes < 0:
        raise ProcessTreeSampleError("invalid memory budget selection")
    if external_budget not in (None, ""):
        try:
            guarded = int(external_budget)
        except (TypeError, ValueError) as exc:
            raise ProcessTreeSampleError("active worker tree budget is invalid") from exc
        if guarded <= 0:
            raise ProcessTreeSampleError("active worker tree budget must be positive")
        if explicit and explicit != guarded:
            raise ProcessTreeSampleError(
                "explicit memory budget differs from the active guard cap; "
                "restart under a matching guard (explicit=" + str(explicit)
                + ", guard=" + str(guarded) + ")"
            )
        return {"max_tree_rss_bytes": explicit or guarded,
                "selection_kind": "explicit" if explicit else "external_guard",
                "explicit_requested_bytes": explicit, "external_guard_bytes": guarded,
                "configured_host_memory_reserve_bytes": reserve_bytes}
    # Non-Darwin explicit limits retain their existing guard contract. The
    # Darwin reserve/swap admission remains mandatory for explicit caps.
    if explicit and observation is None and sys.platform != "darwin":
        return {"max_tree_rss_bytes": explicit, "selection_kind": "explicit",
                "explicit_requested_bytes": explicit, "platform": sys.platform,
                "configured_host_memory_reserve_bytes": reserve_bytes}
    observed = _host_memory_observation() if observation is None else dict(observation)
    if (not explicit and observed["platform"] == "linux"
            and not observed.get("cgroup_limits_verified", False)):
        raise ProcessTreeSampleError(
            "Linux cgroup memory limits cannot be verified; provide an explicit or inherited guard cap"
        )
    preflight = None
    if observed["platform"] == "darwin":
        memory_limit = observed["reclaimable_bytes"]
        pressured = (observed["swap_total_bytes"] > 0
                     and observed["swap_used_bytes"] * 2 > observed["swap_total_bytes"]
                     and observed["swap_free_bytes"] < _MIN_PRESSURED_SWAP_FREE_BYTES)
        if pressured:
            memory_limit //= _SWAP_PRESSURE_RECLAIMABLE_MARGIN
        maximum = min(memory_limit, observed["disk_free_bytes"]) - reserve_bytes
    else:
        # Preserve the old half-memory default, using observed availability
        # and container headroom instead of the machine's total physical RAM.
        maximum = int(observed["available_bytes"]) // 2
    selected = explicit or min(default_ceiling, maximum)
    if selected <= 0:
        raise ProcessTreeSampleError("available resources cannot admit a positive automatic tree budget")
    if observed["platform"] == "darwin":
        preflight = _validate_darwin_resource_observation(
            max_tree_rss_bytes=selected, reserve_bytes=reserve_bytes, **observed,
        )
    return {"max_tree_rss_bytes": selected,
            "selection_kind": "explicit" if explicit else "automatic",
            "explicit_requested_bytes": explicit, "default_ceiling_bytes": default_ceiling,
            "configured_host_memory_reserve_bytes": reserve_bytes,
            "observation": observed, "resource_preflight": preflight}


def _process_snapshot(
    tree: dict[int, tuple[int, int, str]],
    command_cache: dict[int, str] | None = None,
) -> list[dict[str, object]]:
    ordered = sorted(
        tree.items(),
        key=lambda item: (-item[1][1], item[0]),
    )
    return [
        {
            "pid": pid,
            "ppid": row[0],
            "rss_bytes": row[1],
            "command": (
                row[2]
                if row[2]
                else "" if command_cache is None else command_cache.get(pid, "")
            ),
            "manifest_paths": _command_manifest_paths(
                row[2]
                if row[2]
                else "" if command_cache is None else command_cache.get(pid, "")
            ),
        }
        for pid, row in ordered
    ]


def _command_manifest_paths(command: str) -> list[str]:
    paths = []
    for token in str(command).split():
        candidate = token.strip("'\"")
        if candidate.endswith(".manifest") and candidate not in paths:
            paths.append(candidate)
    return paths


def _run(args: argparse.Namespace) -> dict[str, object]:
    global _INTERRUPT_REQUESTED
    _INTERRUPT_REQUESTED = False
    if args.timeout <= 0 or args.interval <= 0 or args.progress_interval <= 0:
        raise ProcessTreeSampleError("timeouts and intervals must be positive")
    if args.max_tree_rss_bytes < 0:
        raise ProcessTreeSampleError("max tree RSS must be zero or positive")
    if args.darwin_preflight_reserve_bytes < 0:
        raise ProcessTreeSampleError("preflight reserve must be zero or positive")
    if args.darwin_preflight_reserve_bytes and args.max_tree_rss_bytes <= 0:
        raise ProcessTreeSampleError("Darwin preflight requires a positive RSS cap")
    command = list(args.command)
    if command and command[0] == "--":
        command = command[1:]
    if not command:
        raise ProcessTreeSampleError("missing command after --")

    result_path = Path(args.result).expanduser().absolute()
    samples_path = Path(args.samples).expanduser().absolute()
    stdout_path = Path(args.stdout).expanduser().absolute()
    stderr_path = Path(args.stderr).expanduser().absolute()
    for path in (result_path, samples_path, stdout_path, stderr_path):
        if path.exists():
            raise ProcessTreeSampleError("refusing existing output: " + str(path))
        path.parent.mkdir(parents=True, exist_ok=True)
    cwd = Path(args.cwd).expanduser().resolve()
    environment = os.environ.copy()
    # Read-only observation transport for the shared host/native worker policy.
    # The guard below remains the only enforcement owner. Its synchronized
    # rows include wrappers and descendants that self RSS cannot observe.
    worker_state_path = str(result_path) + ".worker-rss.tsv"
    if args.max_tree_rss_bytes > 0:
        if Path(worker_state_path).exists():
            raise ProcessTreeSampleError("refusing existing output: " + worker_state_path)
        environment["PCC_WORKER_TREE_STATE_PATH"] = worker_state_path
        environment["PCC_WORKER_TREE_BUDGET_BYTES"] = str(args.max_tree_rss_bytes)
    started_utc = dt.datetime.now(dt.timezone.utc).isoformat()
    started = time.monotonic()
    payload: dict[str, object] = {
        "schema": "pcc.process_tree_sample.v1",
        "status": "RUNNING",
        "started_at_utc": started_utc,
        "command": command,
        "cwd": str(cwd),
        "environment": _recorded_environment(environment),
        "timeout_s": args.timeout,
        "interval_s": args.interval,
        "max_tree_rss_bytes": args.max_tree_rss_bytes,
        "darwin_preflight_reserve_bytes": args.darwin_preflight_reserve_bytes,
    }
    _persist(result_path, payload)
    if args.darwin_preflight_reserve_bytes:
        try:
            payload["resource_preflight"] = _darwin_resource_preflight(
                max_tree_rss_bytes=args.max_tree_rss_bytes,
                reserve_bytes=args.darwin_preflight_reserve_bytes,
            )
            _persist(result_path, payload)
        except BaseException as exc:
            payload.update(
                {
                    "status": "PREFLIGHT_REJECTED",
                    "completed_at_utc": dt.datetime.now(
                        dt.timezone.utc
                    ).isoformat(),
                    "elapsed_s": time.monotonic() - started,
                    "error": type(exc).__name__ + ": " + str(exc),
                }
            )
            _persist(result_path, payload)
            if isinstance(exc, ProcessTreeSampleError):
                raise
            raise ProcessTreeSampleError(
                "resource preflight failed: " + type(exc).__name__ + ": " + str(exc)
            ) from exc

    lock_context = (
        compile_ab._performance_lock()
        if args.performance_lock
        else contextlib.nullcontext()
    )
    samples: list[dict[str, object]] = []
    known_pids: set[int] = set()
    observed_processes: dict[int, _ProcessIdentity] = {}
    peak_tree_rss = 0
    peak_process_count = 0
    process_table_retry_count = 0
    timed_out = False
    interrupted = False
    memory_limited = False
    sampler_error = ""
    largest_process_observed: dict[str, object] = {
        "pid": 0,
        "ppid": 0,
        "rss_bytes": 0,
        "command": "",
        "manifest_paths": [],
        "elapsed_s": 0.0,
    }
    terminal_processes: list[dict[str, object]] = []
    command_cache: dict[int, str] = {}
    command_lookup_failure_count = 0
    process_table_timeouts = (
        _SAFETY_PROCESS_TABLE_TIMEOUTS_S
        if args.max_tree_rss_bytes > 0
        else _PROCESS_TABLE_TIMEOUTS_S
    )
    with contextlib.ExitStack() as launch_context:
        try:
            launch_context.enter_context(lock_context)
        except Exception as exc:
            payload.update(
                {
                    "status": "LOCK_REJECTED",
                    "completed_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
                    "elapsed_s": time.monotonic() - started,
                    "error": type(exc).__name__ + ": " + str(exc),
                }
            )
            _persist(result_path, payload)
            raise ProcessTreeSampleError(
                "performance lock rejected before target launch: " + str(exc)
            ) from exc
        with stdout_path.open("wb") as stdout_stream, stderr_path.open(
            "wb"
        ) as stderr_stream, samples_path.open("w", encoding="utf-8") as samples_stream:
            samples_stream.write(
                "elapsed_s\ttree_rss_bytes\tprocess_count\tlargest_pid\t"
                "largest_rss_bytes\tlargest_command\n"
            )
            samples_stream.flush()
            process = subprocess.Popen(
                command,
                cwd=cwd,
                env=environment,
                stdout=stdout_stream,
                stderr=stderr_stream,
                start_new_session=True,
            )
            deadline = started + args.timeout
            next_progress = started
            try:
                root_identity = _process_identity(process.pid)
                if root_identity is None:
                    raise ProcessTreeSampleError("cannot identify the launched process")
                observed_processes[process.pid] = root_identity
                while True:
                    table, retries = _process_table(
                        timeouts_s=process_table_timeouts,
                        include_command=args.max_tree_rss_bytes <= 0,
                    )
                    process_table_retry_count += retries
                    tree = _owned_tree_rows(table, observed_processes)
                    known_pids.update(tree)
                    tree_rss = sum(row[1] for row in tree.values())
                    if args.max_tree_rss_bytes > 0:
                        state_tmp = worker_state_path + ".tmp"
                        with open(state_tmp, "w", encoding="utf-8") as state_stream:
                            state_stream.write(
                                "pcc.worker-tree-rss.v1\n" + str(time.monotonic())
                                + "\n" + str(args.max_tree_rss_bytes) + "\n"
                            )
                            for pid, row in tree.items():
                                state_stream.write(str(pid) + "\t" + str(row[0]) + "\t" + str(row[1]) + "\n")
                        os.replace(state_tmp, worker_state_path)
                    process_count = len(tree)
                    peak_tree_rss = max(peak_tree_rss, tree_rss)
                    peak_process_count = max(peak_process_count, process_count)
                    largest_pid = 0
                    largest_rss = 0
                    largest_command = ""
                    for pid, (_ppid, rss_bytes, command_name) in tree.items():
                        if command_name:
                            command_cache[pid] = command_name
                        if rss_bytes > largest_rss:
                            largest_pid = pid
                            largest_rss = rss_bytes
                            largest_command = command_name
                    if largest_pid and not largest_command:
                        largest_command = command_cache.get(largest_pid, "")
                        if not largest_command:
                            largest_command = _process_command(largest_pid)
                            if largest_command:
                                command_cache[largest_pid] = largest_command
                            else:
                                command_lookup_failure_count += 1
                    elapsed = time.monotonic() - started
                    terminal_processes = _process_snapshot(tree, command_cache)
                    if largest_rss > int(
                        largest_process_observed["rss_bytes"]
                    ):
                        largest_process_observed = {
                            "pid": largest_pid,
                            "ppid": tree.get(largest_pid, (0, 0, ""))[0],
                            "rss_bytes": largest_rss,
                            "command": largest_command,
                            "manifest_paths": _command_manifest_paths(
                                largest_command
                            ),
                            "elapsed_s": round(elapsed, 6),
                        }
                    sample = {
                        "elapsed_s": round(elapsed, 6),
                        "tree_rss_bytes": tree_rss,
                        "process_count": process_count,
                        "largest_pid": largest_pid,
                        "largest_rss_bytes": largest_rss,
                        "largest_command": largest_command,
                    }
                    samples.append(sample)
                    safe_largest_command = largest_command.replace(
                        "\t", " "
                    ).replace("\n", " ")
                    samples_stream.write(
                        str(sample["elapsed_s"])
                        + "\t"
                        + str(tree_rss)
                        + "\t"
                        + str(process_count)
                        + "\t"
                        + str(largest_pid)
                        + "\t"
                        + str(largest_rss)
                        + "\t"
                        + safe_largest_command
                        + "\n"
                    )
                    samples_stream.flush()
                    now = time.monotonic()
                    if now >= next_progress:
                        print(
                            "elapsed={:.1f}s processes={} tree_rss={} peak_rss={}".format(
                                elapsed,
                                process_count,
                                tree_rss,
                                peak_tree_rss,
                            ),
                            flush=True,
                        )
                        next_progress = now + args.progress_interval
                        payload.update(
                            {
                                "elapsed_s": elapsed,
                                "sample_count": len(samples),
                                "peak_tree_rss_bytes": peak_tree_rss,
                                "peak_process_count": peak_process_count,
                                "process_table_retry_count": (
                                    process_table_retry_count
                                ),
                                "command_lookup_failure_count": (
                                    command_lookup_failure_count
                                ),
                                "largest_process_observed": (
                                    largest_process_observed
                                ),
                            }
                        )
                        _persist(result_path, payload)
                    if (
                        args.max_tree_rss_bytes > 0
                        and tree_rss > args.max_tree_rss_bytes
                    ):
                        memory_limited = True
                        _terminate_owned_processes(process, observed_processes)
                        returncode = process.returncode
                        break
                    returncode = process.poll()
                    if returncode is not None:
                        # A wrapper can exit while one of its children keeps
                        # running in a different process group. Only select
                        # recorded live sessions, including private sessions
                        # created by nested build harnesses.
                        after_exit, retries = _process_table(
                            timeouts_s=process_table_timeouts,
                            include_command=False,
                        )
                        process_table_retry_count += retries
                        remaining = _owned_session_pids(
                            process.pid, observed_processes, after_exit,
                        )
                        if remaining:
                            known_pids.update(remaining)
                            payload["post_exit_cleanup_pids"] = sorted(remaining)
                            _terminate_owned_processes(process, observed_processes)
                        break
                    if _INTERRUPT_REQUESTED:
                        interrupted = True
                        _terminate_owned_processes(process, observed_processes)
                        returncode = process.returncode
                        break
                    if now >= deadline:
                        timed_out = True
                        _terminate_owned_processes(process, observed_processes)
                        returncode = process.returncode
                        break
                    time.sleep(args.interval)
            except KeyboardInterrupt:
                _terminate_owned_processes(process, observed_processes)
                interrupted = True
                returncode = process.returncode
            except BaseException as exc:
                _terminate_owned_processes(process, observed_processes)
                process_table_retry_count += int(
                    getattr(exc, "retry_count", 0) or 0
                )
                sampler_error = type(exc).__name__ + ": " + str(exc)
                returncode = process.returncode

    payload.update(
        {
            "status": (
                "SAMPLER_ERROR"
                if sampler_error
                else (
                    "MEMORY_LIMIT"
                    if memory_limited
                    else (
                        "INTERRUPTED"
                        if interrupted
                        else ("TIMEOUT" if timed_out else "COMPLETE")
                    )
                )
            ),
            "completed_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
            "elapsed_s": time.monotonic() - started,
            "returncode": returncode,
            "sample_count": len(samples),
            "process_table_retry_count": process_table_retry_count,
            "command_lookup_failure_count": command_lookup_failure_count,
            "process_table_timeouts_s": list(process_table_timeouts),
            "peak_tree_rss_bytes": peak_tree_rss,
            "peak_process_count": peak_process_count,
            "known_process_count": len(known_pids),
            "largest_process_observed": largest_process_observed,
            "terminal_processes": terminal_processes,
            "artifacts": {
                "samples": str(samples_path),
                "stdout": str(stdout_path),
                "stderr": str(stderr_path),
            },
        }
    )
    if sampler_error:
        payload["error"] = sampler_error
    _persist(result_path, payload)
    if sampler_error:
        raise ProcessTreeSampleError(sampler_error)
    return payload


def run(args: argparse.Namespace) -> dict[str, object]:
    """Run with the same cooperative SIGINT contract when imported or invoked."""
    previous_sigint = signal.getsignal(signal.SIGINT)
    signal.signal(signal.SIGINT, _request_interrupt)
    try:
        return _run(args)
    finally:
        signal.signal(signal.SIGINT, previous_sigint)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--result", required=True)
    parser.add_argument("--samples", required=True)
    parser.add_argument("--stdout", required=True)
    parser.add_argument("--stderr", required=True)
    parser.add_argument("--cwd", default=".")
    parser.add_argument("--timeout", type=float, required=True)
    parser.add_argument("--interval", type=float, default=0.25)
    parser.add_argument("--progress-interval", type=float, default=30.0)
    parser.add_argument(
        "--max-tree-rss-bytes",
        type=int,
        default=0,
        help="terminate the owned process group when aggregate RSS exceeds this cap",
    )
    parser.add_argument(
        "--darwin-preflight-reserve-bytes",
        type=int,
        default=0,
        help="before launch, require RSS cap plus this much reclaimable memory",
    )
    parser.add_argument(
        "--performance-lock",
        action=argparse.BooleanOptionalAction,
        default=True,
    )
    parser.add_argument("command", nargs=argparse.REMAINDER)
    return parser


def main(argv: list[str] | None = None) -> int:
    try:
        result = run(_parser().parse_args(argv))
    except (OSError, ValueError, ProcessTreeSampleError) as exc:
        print("process-tree sample error: " + str(exc), file=sys.stderr)
        return 2
    if result["status"] == "TIMEOUT":
        return 124
    if result["status"] == "MEMORY_LIMIT":
        return 125
    if result["status"] == "INTERRUPTED":
        return 130
    return int(result["returncode"])


if __name__ == "__main__":
    # Keep repeated CLI interrupts cooperative through receipt flush. Ignore
    # them during interpreter finalization, which resets Python handlers to the
    # OS default before the process has exited. Imported run() restores callers.
    signal.signal(signal.SIGINT, _request_interrupt)
    exit_code = main()
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    raise SystemExit(exit_code)
