"""Portable host-model imports do not require the separate POSIX fd oracle.

These controls block only fcntl, not pretend to be a Windows runtime. They
execute the original portable test bodies; descriptor behavior is qualified
separately by the unchanged real POSIX tests, never by fabricated flags.
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
ERRNO = "tests.python.test_native_open_errno"
CACHE = "tests.python.test_owned_file_cache_contract"
FDOPEN = "tests.python.test_owned_fdopen_provider"

PORTABLE_CASES = [
    (ERRNO, "test_open_errno_keeps_original_filename_and_args", [number, tag, bytes_path])
    for number, tag in [(2, 34), (17, 35), (13, 36), (20, 38), (84, 14)]
    for bytes_path in [False, True]
] + [
    (ERRNO, "test_open_errno_metadata_failure_keeps_allocation_exception", []),
    (ERRNO, "test_fopen_restores_seek_errno_after_close", ["linux"]),
    (ERRNO, "test_fopen_restores_seek_errno_after_close", ["darwin"]),
    (ERRNO, "test_fopen_allocation_failure_publishes_enomem_after_close", []),
] + [
    (CACHE, name, [kind])
    for name in [
        "test_temporary_type_cache_follows_registered_root_after_remap",
        "test_failed_type_root_registration_never_publishes_partial_class",
        "test_competing_type_construction_returns_one_published_owner",
        "test_type_initialization_error_prevents_publication_and_preserves_first_exception",
    ] for kind in [0, 1]
] + [
    (CACHE, "test_shutdown_registration_failure_removes_root_and_preserves_error", []),
    (CACHE, "test_type_failure_cleanup_preserves_original_error_over_finalizer_error", []),
]

CHILD = r'''
import builtins, importlib, json, os, sys
import pytest
request = json.loads(sys.stdin.read())
real_import = builtins.__import__
def no_fcntl(name, *args, **kwargs):
    if name == "fcntl" or name.startswith("fcntl."):
        raise ModuleNotFoundError("fcntl is unavailable in this import control", name="fcntl")
    return real_import(name, *args, **kwargs)
sys.modules.pop("fcntl", None)
builtins.__import__ = no_fcntl
modules = {name: importlib.import_module(name) for name in request["modules"]}
if request["mode"] == "case":
    module, function, args = request["case"]
    getattr(modules[module], function)(*args)
elif request["mode"] == "imports":
    fd = modules["tests.python.test_owned_fdopen_provider"]
    assert modules["tests.python.test_native_open_errno"].FileRuntime is fd.FileRuntime
    assert modules["tests.python.test_owned_file_cache_contract"].FileRuntime is fd.FileRuntime
    for module, function, _ in request["cases"]:
        assert callable(getattr(modules[module], function))
elif request["mode"] == "unsupported":
    calls = []
    def forbidden(*args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError("unsupported POSIX oracle caused a filesystem side effect")
    os.open = forbidden
    try:
        if request["boundary"] == "fd_control":
            modules["tests.python.test_owned_fdopen_provider"].FileRuntime().fd_control(-1, 3, 0)
        else:
            cache = modules["tests.python.test_owned_file_cache_contract"]
            cache.FileRuntime = forbidden
            cache.test_append_failure_leaves_descriptor_flags_position_and_ownership_unchanged(None, "stream")
    except ModuleNotFoundError as error:
        assert error.name == "fcntl"
    else:
        raise AssertionError("unsupported POSIX oracle silently claimed support")
    assert not calls
else:
    raise AssertionError(request["mode"])
assert "fcntl" not in sys.modules
print("FILE_MODEL_BOUNDARY_OK")
'''


def _run(request):
    request["modules"] = [FDOPEN, CACHE, ERRNO]
    result = subprocess.run([sys.executable, "-B", "-c", CHILD],
                            input=json.dumps(request), text=True,
                            capture_output=True, cwd=ROOT, timeout=30)
    assert result.returncode == 0, result.stderr
    assert result.stdout == "FILE_MODEL_BOUNDARY_OK\n"


def test_portable_file_model_registration_without_fcntl():
    _run({"mode": "imports", "cases": PORTABLE_CASES})


@pytest.mark.parametrize("case", PORTABLE_CASES,
                         ids=[f"{fn}-{args}" for _, fn, args in PORTABLE_CASES])
def test_original_portable_file_model_body_without_fcntl(case):
    _run({"mode": "case", "case": case})


@pytest.mark.parametrize("boundary", ["fd_control", "append_oracle"])
def test_unsupported_posix_oracle_fails_before_filesystem_side_effect(boundary):
    _run({"mode": "unsupported", "boundary": boundary})
