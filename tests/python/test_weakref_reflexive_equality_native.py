"""Live weakref self-equality must call a non-reflexive referent's __eq__.

Specification: Python Library Reference, weakref.ref equality; Python Language
Reference, value comparisons and identity-or-equality inside containers.
CPython v3.15.0 Objects/weakrefobject.c weakref_richcompare calls
PyObject_RichCompare on live referents, even for the same weakref handle.
Objects/object.c keeps the identity shortcut in PyObject_RichCompareBool,
not PyObject_RichCompare. This fixture covers boolean == results only.
"""
import os
import subprocess
import sys

import pytest


PROGRAM = '''import weakref

calls = []


class Key:
    def __eq__(self, other):
        calls.append(self is other)
        return False

    def __hash__(self):
        return 7


def callback(reference):
    pass


def main():
    target = Key()
    reference = weakref.ref(target)
    # A callback prevents CPython from reusing its callback-free ref cache.
    distinct = weakref.ref(target, callback)
    assert reference is not distinct

    assert (reference == reference) is False
    assert calls == [True]
    boxed = [reference]
    assert (boxed[0] == boxed[0]) is False
    assert calls == [True, True]
    assert (reference == distinct) is False
    assert calls == [True, True, True]

    # Each container uses identity-or-equality, without calling __eq__.
    assert reference in [reference]
    assert [reference] == [reference]
    assert (reference,) == (reference,)
    assert reference in {reference}
    assert calls == [True, True, True]
    # Distinct handles cannot use the container identity shortcut.
    assert (distinct in [reference]) is False
    assert calls == [True, True, True, True]

    # Keep the referent strongly reachable through every comparison.
    assert reference() is target
    assert distinct() is target
    print("weakref nonreflexive equality ok")


main()
'''


def _check(command, environment=None):
    result = subprocess.run(command, capture_output=True, text=True,
                            timeout=20, env=environment)
    assert (result.returncode, result.stdout, result.stderr) == (
        0, "weakref nonreflexive equality ok\n", ""), (
            result.returncode, result.stdout, result.stderr)


@pytest.mark.pcc_gate(env="PCC_RUN_CPYTHON_REFERENCE")
def test_weakref_nonreflexive_equality_cpython_reference(tmp_path):
    assert sys.implementation.name == "cpython"
    assert sys.version_info[:3] == (3, 15, 0), sys.version
    source = tmp_path / "weakref_equality.py"
    source.write_text(PROGRAM)
    _check([sys.executable, str(source)])


@pytest.mark.integration
def test_weakref_nonreflexive_equality_native_five_gc(
    tmp_path, pcc_runtime_archive, python_program_compiler,
):
    source = tmp_path / "weakref_equality.py"
    source.write_text(PROGRAM)
    binary = tmp_path / "weakref_equality"
    python_program_compiler(str(source), str(binary), backend="self",
                            runtime_archive=str(pcc_runtime_archive))
    for backend in range(5):
        _check([str(binary)], dict(os.environ, PCC_GC_BACKEND=str(backend)))
