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
    print("foreign-release-once")
main()
'''

C_OWNED_PROGRAM = FOREIGN_PROGRAM_HEADER + r'''
refcount_delta = extern("fixture_foreign_handle_refcount_delta", (c_obj,), c_int64)
hold = extern("fixture_foreign_handle_hold", (c_obj,), c_void)
drop = extern("fixture_foreign_handle_drop", (), c_void)
hold_second = extern("fixture_foreign_handle_hold_second", (c_obj,), c_void)
drop_second = extern("fixture_foreign_handle_drop_second", (), c_void)
check_raw_owner = extern("fixture_foreign_handle_check_raw_owner", (), c_int64)
held_refcount = extern("fixture_foreign_handle_held_refcount", (), c_int64)
set_held_refcount = extern("fixture_foreign_handle_set_held_refcount", (c_int64,), c_int64)

def create():
    # After this scope exits, the C fixture is the only intended owner.
    handle = new_handle()
    assert handle is not None
    print("foreign-refcount-delta", refcount_delta(handle))
    hold(handle)
    hold_second(handle)

def main():
    create()
    keep = [17, 19]
    for attempt in range(4):
        gc.collect()
    released = release_count()
    print("foreign-released-while-held", released)
    assert released == 0
    assert check_raw_owner() == 1
    refs = held_refcount()
    assert refs >= 2
    print("foreign-refcount-after-set", set_held_refcount(1))
    # A numerical override smaller than the external owner count must
    # not alter the collector's two independent persistent C holds.
    for attempt in range(4):
        gc.collect()
    assert release_count() == 0
    assert check_raw_owner() == 1
    assert held_refcount() == 1
    assert set_held_refcount(refs) == refs
    drop_second()
    for attempt in range(4):
        gc.collect()
    released = release_count()
    print("foreign-released-with-one-owner", released)
    assert released == 0
    assert check_raw_owner() == 1
    assert held_refcount() == refs - 1
    drop()
    print("foreign-released-after-decref", release_count())
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


def _run_foreign_program(binary):
    return subprocess.run([str(binary)], env=dict(os.environ, PATH="/nonexistent"),
                          capture_output=True, text=True, timeout=20)


def test_collector_releases_foreign_payload_once(
    tmp_path, pcc_runtime_archive, python_program_compiler,
):
    binary = _build_foreign_program(tmp_path, pcc_runtime_archive, python_program_compiler,
                                    RELEASE_PROGRAM)
    result = _run_foreign_program(binary)
    assert (result.returncode, result.stdout, result.stderr) == (
        0, "foreign-release-once\n", ""
    ), result


def test_c_owned_foreign_handle_survives_until_decref(
    tmp_path, pcc_runtime_archive, python_program_compiler,
):
    binary = _build_foreign_program(tmp_path, pcc_runtime_archive, python_program_compiler,
                                    C_OWNED_PROGRAM)
    result = _run_foreign_program(binary)
    assert (result.returncode, result.stdout, result.stderr) == (
        0,
        "foreign-refcount-delta 1\n"
        "foreign-released-while-held 0\n"
        "foreign-refcount-after-set 1\n"
        "foreign-released-with-one-owner 0\n"
        "foreign-released-after-decref 1\n"
        "foreign-released-after-collection 1\n",
        "",
    ), result
