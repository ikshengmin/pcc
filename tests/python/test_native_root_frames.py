"""Native handle-frame regressions against the production pcc-Python runtime.

The C files are owned-compiled test callers, not a second runtime or a host-cc
oracle. Exact slot rewrites establish native-frame traversal independently of
conservative stack scanning. Specification: docs/project-intent.md, runtime
layering and the 5-GC Production Equality Rule; docs/compiler-contract.md,
dependency ownership contract.
"""
from __future__ import annotations

import os
from pathlib import Path
import signal
import subprocess

from tests.owned_c_execution import compile_owned_c_with_runtime


FIXTURES = Path(__file__).with_name("fixtures")


def _compile(tmp_path, runtime_archive, fixture):
    executable = tmp_path / fixture
    compile_owned_c_with_runtime(
        FIXTURES / (fixture + ".c"),
        executable,
        runtime_archive=runtime_archive,
    )
    return executable


def _run(executable, *arguments, backend=0, tracing_only=0, minor_alloc_max=16):
    return subprocess.run(
        [str(executable), *arguments],
        capture_output=True,
        text=True,
        timeout=20,
        env=dict(
            os.environ,
            PATH="/nonexistent",
            PCC_GC_BACKEND=str(backend),
            PCC_GC_TRACING_ONLY=str(tracing_only),
            PCC_GC4_TRACING_ONLY="0",
            PCC_GC_MINOR_ALLOC_MAX=str(minor_alloc_max),
        ),
    )


def test_native_frame_ranges_and_lifetime_fail_stops(tmp_path, pcc_runtime_archive):
    executable = _compile(tmp_path, pcc_runtime_archive, "native_frame_protocol")
    for backend, tracing_only in ((0, 0), (4, 1)):
        result = _run(executable, backend=backend, tracing_only=tracing_only)
        assert (result.returncode, result.stdout, result.stderr) == (
            0, "native-frame-protocol-ok\n", ""
        ), (backend, tracing_only, result)
        for action in ("lifo", "unregister", "exited"):
            result = _run(
                executable, action, backend=backend, tracing_only=tracing_only,
            )
            # These run directly, without a shell: an unrelated failure or
            # returning the fixture's sentinel is not a passing abort check.
            assert result.returncode in (-signal.SIGABRT, 128 + signal.SIGABRT), (
                backend, tracing_only, action, result
            )
            assert result.stdout == "", (action, result.stdout)


def test_native_frame_other_thread_exited_stack_is_rewritten(tmp_path, pcc_runtime_archive):
    executable = _compile(tmp_path, pcc_runtime_archive, "native_frame_parked")
    result = _run(executable)
    assert (result.returncode, result.stdout, result.stderr) == (
        0, "native-frame-parked-ok\n", ""
    ), result


def test_native_frame_gc3_owned_and_borrowed_healing(tmp_path, pcc_runtime_archive):
    executable = _compile(tmp_path, pcc_runtime_archive, "native_frame_gc3_roles")
    # The 52-byte string must enter the nursery (64 bytes after alignment).
    # The fixture quiesces CMS through its real lifecycle API and verifies
    # YOUNG/unpinned state; it never bypasses the production relocation gate.
    result = _run(executable, backend=3, minor_alloc_max=256)
    assert (result.returncode, result.stdout, result.stderr) == (
        0,
        "gc3-before-gray source_rc=1 target_rc=2 known=0 managed=1 edge=1 slot_old=1\n"
        "native-frame-gc3-roles-ok\n",
        "",
    ), result
