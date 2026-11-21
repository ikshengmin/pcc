"""Native foreign-payload release and C reference ownership contracts."""
import os
import subprocess
from pathlib import Path

import pytest


# Each owned compile may start a frontend worker. Serialize this file's
# native compile cases within xdist's normal six-worker resource budget.
pytestmark = pytest.mark.xdist_group("gc_foreign_handle_native")


FOREIGN_PROGRAM_HEADER = r'''import gc
from pcc.extern import c_int64, c_obj, c_void, extern

new_handle = extern("fixture_new_foreign_handle", (), c_obj)
release_count = extern("fixture_foreign_release_count", (), c_int64)
backend = extern("pcc_gc_backend", (), c_int64)
'''

RELEASE_PROGRAM = FOREIGN_PROGRAM_HEADER + r'''

def create():
    handle = new_handle()
    assert handle is not None

def main():
    create()
    # A normal later allocation ends the allocator's last-allocation root.
    keep = [17, 19]
    for attempt in range(4):
        gc.collect()
    assert release_count() == 1
    assert keep == [17, 19]
    print("foreign-release-once", backend())
main()
'''

C_OWNED_PROGRAM = FOREIGN_PROGRAM_HEADER + r'''
refcount_delta = extern("fixture_foreign_handle_refcount_delta", (c_obj,), c_int64)
hold = extern("fixture_foreign_handle_hold", (c_obj,), c_void)
drop = extern("fixture_foreign_handle_drop", (), c_void)

def create():
    # After this scope exits, the C fixture is the only intended owner.
    handle = new_handle()
    assert handle is not None
    print("foreign-refcount-delta", refcount_delta(handle))
    hold(handle)

def main():
    print("foreign-backend", backend())
    create()
    keep = [17, 19]
    for attempt in range(4):
        gc.collect()
    released = release_count()
    print("foreign-released-while-held", released)
    if released == 0:
        drop()
        print("foreign-released-after-decref", release_count())
    # Do not call drop, read the pointer or otherwise touch the handle after
    # any observed release. Still check that later collections do not release
    # the payload again; this only reads the C fixture's separate counters.
    for attempt in range(4):
        gc.collect()
    assert keep == [17, 19]
    print("foreign-released-after-collection", release_count())
main()
'''


def _build_foreign_program(tmp_path, pcc_runtime_archive, python_program_compiler, program):
    from pcc.backend.owned_object_emit import emit_owned_object
    from pcc.frontends.python.pipeline_targets import host_target_triple
    from tests.python.test_owned_external_tls import _c_ir

    fixture = Path(__file__).with_name("fixtures") / "gc_foreign_handle_payload.c"
    target = host_target_triple()
    helper = tmp_path / "foreign_payload.o"
    helper.write_bytes(emit_owned_object(_c_ir(fixture.read_text(), target), target))
    source = tmp_path / "foreign_collection.py"
    source.write_text(program)
    binary = tmp_path / "foreign_collection"
    python_program_compiler(str(source), str(binary), backend="self",
                            runtime_archive=str(pcc_runtime_archive), object_inputs=(str(helper),))
    return binary


def _run_foreign_program(binary, backend):
    environment = dict(os.environ, PCC_GC_BACKEND=str(backend))
    environment.pop("PCC_GC_TRACING_ONLY", None)
    environment.pop("PCC_GC4_TRACING_ONLY", None)
    return subprocess.run([str(binary)], env=environment, capture_output=True,
                          text=True, timeout=20)


def test_default_collectors_release_foreign_payload_once(
    tmp_path, pcc_runtime_archive, python_program_compiler,
):
    binary = _build_foreign_program(tmp_path, pcc_runtime_archive, python_program_compiler,
                                    RELEASE_PROGRAM)
    for backend in range(5):
        result = _run_foreign_program(binary, backend)
        assert (result.returncode, result.stdout, result.stderr) == (
            0, f"foreign-release-once {backend}\n", ""
        ), (backend, result)


class _KnownEarlyRelease(RuntimeError):
    """Only the exact #6 native ownership failure qualifies as expected."""


def _pending_c_owner(backend):
    return pytest.param(backend, id=f"gc{backend}", marks=pytest.mark.xfail(
        strict=True, raises=_KnownEarlyRelease,
        reason="PCC #6: Py_INCREF is a no-op for this GC; payload released while C holds it",
    ))


@pytest.mark.parametrize("backend", [pytest.param(0, id="gc0"), *map(_pending_c_owner, range(1, 5))])
def test_c_owned_foreign_handle_survives_until_decref(
    tmp_path, pcc_runtime_archive, python_program_compiler, backend,
):
    binary = _build_foreign_program(tmp_path, pcc_runtime_archive, python_program_compiler,
                                    C_OWNED_PROGRAM)
    result = _run_foreign_program(binary, backend)
    assert (result.returncode, result.stderr) == (0, ""), (backend, result)
    known_failure = (
        f"foreign-backend {backend}\n"
        "foreign-refcount-delta 0\n"
        "foreign-released-while-held 1\n"
        "foreign-released-after-collection 1\n"
    )
    if backend != 0 and result.stdout == known_failure:
        raise _KnownEarlyRelease(known_failure)

    # Refcounting GC0 must release synchronously on the final Py_DECREF.
    # Tracing collectors may defer the single release to gc.collect().
    expected = [
        f"foreign-backend {backend}\n"
        "foreign-refcount-delta 1\n"
        "foreign-released-while-held 0\n"
        f"foreign-released-after-decref {after_decref}\n"
        "foreign-released-after-collection 1\n"
        for after_decref in ((1,) if backend == 0 else (0, 1))
    ]
    assert result.stdout in expected, (backend, result)
