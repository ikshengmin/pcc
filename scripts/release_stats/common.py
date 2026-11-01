"""Shared identity, file and process helpers for the release statistics."""

from __future__ import annotations

import datetime
import hashlib
import json
import os
import platform
import subprocess
import sys
from pathlib import Path


HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]


def write_json(path: Path, value) -> None:
    """Write ``value`` so that a reader never sees a half-written file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_name(path.name + ".partial")
    partial.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(partial, path)


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _git(args: list[str]) -> str | None:
    try:
        completed = subprocess.run(
            ["git", *args], cwd=REPO, capture_output=True, text=True, timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if completed.returncode != 0:
        return None
    return completed.stdout.strip()


def pcc_revision(explicit: str | None) -> dict:
    """The measured source: the checkout's HEAD and whether it was modified.

    A tree without Git metadata (an exported snapshot) must name its commit
    with ``--pcc-revision``; the statistics are never left unattributed.
    """
    head = _git(["rev-parse", "HEAD"])
    if head is None:
        if not explicit:
            raise SystemExit(
                "error: this tree has no Git metadata; name the exported commit "
                "with --pcc-revision"
            )
        return {"revision": explicit, "source": "--pcc-revision (exported tree)",
                "modified_paths": None}
    status = _git(["status", "--porcelain", "--untracked-files=no"]) or ""
    modified = sorted(line[3:] for line in status.splitlines() if line.strip())
    if explicit and not head.startswith(explicit):
        raise SystemExit(f"error: --pcc-revision {explicit} is not this checkout's HEAD {head}")
    return {"revision": head, "source": "git", "modified_paths": modified}


def _sysctl(name: str) -> str | None:
    try:
        completed = subprocess.run(["sysctl", "-n", name], capture_output=True,
                                   text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if completed.returncode != 0:
        return None
    return completed.stdout.strip() or None


def machine() -> dict:
    load = os.getloadavg() if hasattr(os, "getloadavg") else None
    return {
        "platform": platform.platform(),
        "machine": platform.machine(),
        "cpu": _sysctl("machdep.cpu.brand_string") or platform.processor(),
        "cpus": os.cpu_count(),
        "memory_bytes": int(_sysctl("hw.memsize") or 0) or None,
        "load_average": list(load) if load else None,
    }


def python_identity(python: str) -> dict:
    completed = subprocess.run(
        [python, "-c", "import sys, platform; print(sys.version.split()[0]); "
                       "print(platform.python_implementation()); print(sys.version)"],
        capture_output=True, text=True, timeout=60,
    )
    if completed.returncode != 0:
        raise SystemExit(f"error: cannot run the reference Python {python}: {completed.stderr}")
    version, implementation, full = completed.stdout.splitlines()[:3]
    return {"executable": python, "version": version, "implementation": implementation,
            "build": full}


def identity(options) -> dict:
    """Everything a stage result is attributed to."""
    return {
        "pcc": pcc_revision(getattr(options, "pcc_revision", None)),
        "reference_python": python_identity(options.python),
        "machine": machine(),
        "started_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
        "command": [sys.executable, *sys.argv],
    }


def run_logged(command: list[str], *, log: Path, timeout: float, cwd: Path | None = None,
               env: dict | None = None) -> subprocess.CompletedProcess | None:
    """Run ``command`` with its output in ``log``; None when it timed out."""
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("w", encoding="utf-8") as stream:
        try:
            return subprocess.run(command, cwd=cwd, env=env, stdout=stream,
                                  stderr=subprocess.STDOUT, text=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            stream.write(f"\n@@release-stats@@ timed out after {timeout} seconds\n")
            return None


def _group_rss_bytes(group: int) -> int:
    completed = subprocess.run(["ps", "-axo", "pgid=,rss="], capture_output=True,
                               text=True, timeout=30)
    total = 0
    for line in completed.stdout.splitlines():
        words = line.split()
        if len(words) == 2 and words[0] == str(group):
            total += int(words[1]) * 1024
    return total


def run_bounded(command: list[str], *, cwd: Path, timeout: float, rss_limit_bytes: int,
                env: dict | None = None, output_dir: Path) -> dict:
    """Run ``command`` in its own process group and stop the whole group when
    it runs longer than ``timeout`` or its resident memory exceeds the limit.

    stdout and stderr go to files in ``output_dir`` (no pipe can fill up);
    the result carries their text, the exit status, why the group was
    stopped (None, "timeout" or "memory") and the peak group RSS observed.
    """
    import signal
    import time

    output_dir.mkdir(parents=True, exist_ok=True)
    stdout_path, stderr_path = output_dir / "stdout.txt", output_dir / "stderr.txt"
    started = time.monotonic()
    killed = None
    peak = 0
    with stdout_path.open("w", encoding="utf-8") as out, \
            stderr_path.open("w", encoding="utf-8") as err:
        process = subprocess.Popen(command, cwd=cwd, env=env, stdout=out, stderr=err,
                                   text=True, start_new_session=True)
        while process.poll() is None:
            elapsed = time.monotonic() - started
            rss = _group_rss_bytes(process.pid)
            peak = max(peak, rss)
            if elapsed > timeout:
                killed = "timeout"
            elif rss > rss_limit_bytes:
                killed = "memory"
            if killed:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait()
                break
            try:
                process.wait(timeout=1.0)
            except subprocess.TimeoutExpired:
                pass
    return {
        "returncode": process.returncode,
        "stdout": stdout_path.read_text(encoding="utf-8", errors="replace"),
        "stderr": stderr_path.read_text(encoding="utf-8", errors="replace"),
        "killed": killed,
        "seconds": round(time.monotonic() - started, 3),
        "peak_rss_bytes": peak,
    }


def first_error_line(text: str, *, compile_output: bool = False) -> str:
    """The most informative line of a failure's output.

    A compile names its cause before the driver's summary line, so the first
    exception line wins there (the worker's own exception when present); a
    program's traceback ends with its exception, so the last one wins.
    """
    import re

    lines = [line.strip() for line in text.splitlines() if line.strip()]
    pattern = re.compile(r"\b\w*(Error|Exception)\b:|^pyperf shim:|error:")
    if compile_output:
        for line in lines:
            if "worker failed: " in line:
                return line.split("worker failed: ", 1)[1][:300]
        for line in lines:
            if pattern.search(line):
                return line[:300]
    else:
        for line in reversed(lines):
            if pattern.search(line):
                return line[:300]
    return lines[-1][:300] if lines else ""


def failure_category(detail: str) -> str:
    """``detail`` with quoted names, paths and numbers replaced, so failures
    of the same kind in different modules count together."""
    import re

    text = re.sub(r"'[^']*'|\"[^\"]*\"", "'…'", detail)
    text = re.sub(r"codegen\[[^\]]*\]", "codegen[…]", text)
    text = re.sub(r"(/[^\s:]+)+", "<path>", text)
    return re.sub(r"\b\d+\b", "N", text)[:200]


def geometric_mean(values: list[float]) -> float | None:
    import math

    positive = [value for value in values if value > 0]
    if not positive:
        return None
    return math.exp(sum(math.log(value) for value in positive) / len(positive))
