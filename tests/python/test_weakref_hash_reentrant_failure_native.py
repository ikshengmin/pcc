"""Failed outer weakref hashing must discard a recursive cached success.

Reference: CPython v3.15.0 Objects/weakrefobject.c, weakref_hash_lock_held.
https://github.com/python/cpython/blob/v3.15.0/Objects/weakrefobject.c#L190-L201
The outer hash stores its result even on failure, overwriting a nested success
with the unset -1 sentinel so a later live call retries the referent's hash.
"""
import os
import subprocess
import sys

import pytest


PROGRAM = '''import weakref

calls = []
refs = []

class Key:
    def __hash__(self):
        calls.append(1)
        if len(calls) == 1:
            assert hash(refs[0]) == 73
            raise ValueError("outer hash failed")
        return 73 if len(calls) == 2 else 99


def main():
    target = Key()
    refs.append(weakref.ref(target))
    try:
        hash(refs[0])
        raise AssertionError("outer hash did not fail")
    except ValueError as error:
        assert str(error) == "outer hash failed"
    assert len(calls) == 2
    assert hash(refs[0]) == 99
    assert len(calls) == 3
    assert hash(refs[0]) == 99
    assert len(calls) == 3
    # Keep the target strongly reachable through every hash operation.
    assert refs[0]() is target
    print("reentrant hash retry ok")

main()
'''


def _check(command, environment=None):
    result = subprocess.run(command, capture_output=True, text=True,
                            timeout=20, env=environment)
    assert (result.returncode, result.stdout, result.stderr) == (
        0, "reentrant hash retry ok\n", ""), (
            result.returncode, result.stdout, result.stderr)


@pytest.mark.pcc_gate(env="PCC_RUN_CPYTHON_REFERENCE")
def test_weakref_failed_outer_hash_cpython_reference(tmp_path):
    assert sys.implementation.name == "cpython"
    assert sys.version_info[:3] == (3, 15, 0), sys.version
    source = tmp_path / "weakref_hash_reentry.py"
    source.write_text(PROGRAM)
    _check([sys.executable, str(source)])


@pytest.mark.integration
def test_weakref_failed_outer_hash_native_five_gc(
    tmp_path, pcc_runtime_archive, python_program_compiler,
):
    source = tmp_path / "weakref_hash_reentry.py"
    source.write_text(PROGRAM)
    binary = tmp_path / "weakref_hash_reentry"
    python_program_compiler(str(source), str(binary), backend="self",
                            runtime_archive=str(pcc_runtime_archive))
    for backend in range(5):
        _check([str(binary)], dict(os.environ, PCC_GC_BACKEND=str(backend)))
