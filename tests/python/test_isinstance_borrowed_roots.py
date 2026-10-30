"""Stable isinstance operands reuse roots while keeping address protection."""
import os
import subprocess
import sys

import pytest

from pcc.frontends.python.pipeline import compile_python
from tests.python.test_shared_call_binding import _emit, _function


@pytest.mark.parametrize("classinfo", ["(str, classes())", "(classes(), list)"])
def test_stable_isinstance_parameter_has_one_lease_and_no_extra_owner(classinfo):
    # Classinfo evaluation can call back and park: the operand keeps an
    # address lease across it.
    body = _function(_emit("def classes():\n    return str\ndef probe(value):\n"
                           "    return isinstance(value, " + classinfo + ")\n"))
    assert body.count("@pcc_gc_borrowed_lease_acquire(") == 1
    assert "isinstance.operand.lease" in body and "isinstance.operand.release" in body
    assert "@py_cleanup_one_lease_preserving_exception(" in body
    assert "isinstance.operand" in body
    assert not any("isinstance.operand" in line and ".tmp.root" in line for line in body.splitlines())


@pytest.mark.parametrize("classinfo", ["str", "(list, dict)"])
def test_builtin_tag_isinstance_reads_stable_parameter_once_without_owner(classinfo):
    # Builtin tag tests inspect the operand at once, with no gate in between:
    # one borrowed read-barrier load replaces the lease, still with no owner.
    body = _function(_emit("def probe(value):\n"
                           "    return isinstance(value, " + classinfo + ")\n"))
    assert "@pcc_gc_borrowed_lease_acquire(" not in body
    assert body.count("@pcc_gc_load_borrowed_ptr(") == 1
    assert not any("isinstance.operand" in line and ".tmp.root" in line for line in body.splitlines())


@pytest.mark.parametrize("source", [
    "def probe(value, kind):\n    value = []\n    return isinstance(value, kind)\n",
    "def probe(value):\n    return isinstance(value.item, list)\n",
    "def probe(value, kind):\n    return isinstance([], kind)\n",
    "def probe(value):\n    def change():\n        nonlocal value\n        value = []\n    return isinstance(value, (list, change()))\n",
])
def test_nonstable_operands_keep_their_independent_owner(source):
    body = _function(_emit(source))
    assert "isinstance.operand" in body and ".tmp.root" in body
    assert "isinstance.operand.lease" not in body


PROGRAM = r'''import gc
import weakref

events = []
keepers = []
refs = []
class Item:
    def __del__(self):
        events.append("drop")
        gc.collect()

def make(mode):
    events.append("classinfo")
    keepers.clear()
    gc.collect()
    assert refs[-1]() is not None
    if mode == 1:
        raise ValueError("classinfo failure")
    if mode == 2:
        return 42
    return Item

def check(value, mode):
    return isinstance(value, (str, make(mode)))

def main():
    iteration = 0
    while iteration < 6:
        mode = iteration % 3
        keepers.append(Item())
        refs.append(weakref.ref(keepers[0]))
        events.clear()
        try:
            result = check(keepers[0], mode)
            assert mode == 0 and result
        except ValueError as error:
            assert mode == 1 and str(error) == "classinfo failure"
        except TypeError:
            assert mode == 2
        gc.collect()
        assert refs[-1]() is None
        assert events == ["classinfo", "drop"]
        iteration += 1
    print("ISINSTANCE_BORROWED_ROOT_OK")
main()
'''


def test_classinfo_callbacks_failures_and_operand_lifetimes_native(tmp_path, pcc_runtime_archive):
    source = tmp_path / "borrowed_isinstance.py"
    source.write_text(PROGRAM)
    reference = subprocess.run([sys.executable, str(source)], capture_output=True, text=True, timeout=20)
    assert (reference.returncode, reference.stdout, reference.stderr) == (0, "ISINSTANCE_BORROWED_ROOT_OK\n", "")
    binary = tmp_path / "borrowed_isinstance"
    compile_python(str(source), str(binary), backend="self", libpython_mode="off",
                   ir_scaffold_mode="on", runtime_archive=str(pcc_runtime_archive))
    for backend in range(5):
        result = subprocess.run([str(binary)], capture_output=True, text=True, timeout=30,
            env=dict(os.environ, PATH="/nonexistent", PCC_GC_BACKEND=str(backend),
                     PCC_GC_MINOR_HEAP_SIZE="4096", PCC_GC_MINOR_ALLOC_MAX="128"))
        (tmp_path / (str(backend) + ".stdout")).write_text(result.stdout)
        (tmp_path / (str(backend) + ".stderr")).write_text(result.stderr)
        assert (result.returncode, result.stdout, result.stderr) == (0, reference.stdout, ""), backend
