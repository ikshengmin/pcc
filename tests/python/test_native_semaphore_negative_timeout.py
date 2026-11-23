"""An empty native semaphore treats negative timeouts as a try-once."""

import os
from pathlib import Path
import subprocess
import sys

import pytest


PROGRAM = '''from threading import Semaphore
import sys
import time


def exercise():
    sem = Semaphore(0)
    print("int64 boundary", sem.acquire(timeout=-9223372036.854776))
    print("below int64", sem.acquire(timeout=-9223372036.854778))
    print("negative float", sem.acquire(timeout=-1e300))
    print("negative infinity", sem.acquire(timeout=float("-inf")))
    print("negative int", sem.acquire(timeout=-10000000000))
    print("negative boxed int", sem.acquire(timeout=int("-1" + "0" * 40)))
    too_large_int = int("-1" + "0" * 400)
    try:
        sem.acquire(timeout=too_large_int)
    except OverflowError:
        print("int conversion overflow")
    print("zero", sem.acquire(timeout=0))
    start = time.monotonic()
    expired = sem.acquire(timeout=0.02)
    print("positive finite", expired, time.monotonic() - start >= 0.015)
    try:
        sem.acquire(timeout=float("inf"))
    except OverflowError:
        print("positive infinity overflow")
    try:
        sem.acquire(False, "invalid")
    except ValueError:
        print("nonblocking invalid timeout rejected")
    try:
        sem.acquire(False, -1e300)
    except ValueError:
        print("nonblocking with timeout rejected")
    sem.release()
    print("available bypasses conversion", sem.acquire(timeout=too_large_int))
    sem.release()
    print("available bypasses invalid timeout", sem.acquire(timeout="invalid"))
    sem.release()
    print("available bypasses NaN", sem.acquire(timeout=float("nan")))
    # Preserve PCC's existing NaN error. Do not drive CPython's empty-NaN
    # loop, which cannot use NaN as an elapsed deadline.
    if len(sys.argv) > 1:
        try:
            sem.acquire(timeout=float("nan"))
        except ValueError:
            print("empty NaN rejected")


exercise()
'''


def _compile(tmp_path: Path, output: Path, *, ir_only: bool = False) -> None:
    from pcc.frontends.python.pipeline import compile_python

    entry = tmp_path / "semaphore_negative_timeout.py"
    entry.write_text(PROGRAM, encoding="utf-8")
    compile_python(str(entry), str(output), backend="self", ir_scaffold_mode="on", emit_llvm_only=ir_only)


@pytest.mark.integration
def test_native_semaphore_negative_timeout_matches_cpython(tmp_path: Path) -> None:
    reference = subprocess.run([sys.executable, "-c", PROGRAM], capture_output=True, timeout=10, check=True)
    exe = tmp_path / "semaphore_negative_timeout"
    _compile(tmp_path, exe)
    expected = reference.stdout + b"empty NaN rejected\n"
    for backend in (0,):
        run = subprocess.run([str(exe), "--check-pcc-empty-nan"], capture_output=True, timeout=10,
                             env=dict(os.environ, PCC_GC_BACKEND=str(backend)))
        assert run.returncode == 0, (backend, run.stderr)
        assert run.stdout == expected, (backend, run.stdout, expected)
