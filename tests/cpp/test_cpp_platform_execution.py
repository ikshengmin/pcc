"""Same original C++ cases, distinct cross/owned-native/reference evidence.

Host Python owns PCC compilation here; this is not a native pcc1 gate. No
platform is silently skipped, and reference compiler disagreements stay FAIL.
"""

import hashlib
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess

import pytest

from tests.cpp.owned_execution import (
    Execution,
    assert_exact,
    compile_reference_cpp,
    run_executable,
    write_host_executable,
)
from tests.cpp.platform_execution import (
    DARWIN_TARGET,
    LINUX_TARGET,
    TARGETS,
    compile_native_cpp,
    compile_target_objects,
    darwin_observer_object,
    native_target,
)
from tests.cpp.test_cpp_class_semantics import CLASS_CASES
from tests.cpp.test_cpp_aggregate_initialization import AGGREGATE_CASES


CASES = tuple(
    (kind + "-" + name, source, value)
    for kind, cases in (("class", CLASS_CASES), ("aggregate", AGGREGATE_CASES))
    for name, source, value in cases
) + (
    ("observer-negative-int", "int cpp_test_entry(){return -1234567;}", -1234567),
    ("observer-high-bytes", "int cpp_test_entry(){return 16909060;}", 16909060),
)


def _forbidden_process(*args, **kwargs):
    raise AssertionError("owned C++ compilation launched an external process")


def _save_objects(directory, compiled):
    for index, (ir, obj) in enumerate(zip(compiled.ir, compiled.objects)):
        (directory / f"unit-{index}.ll").write_text(ir)
        (directory / f"unit-{index}.o").write_bytes(obj)


def _record_run(binary, directory, expected, owner, target):
    try:
        actual = run_executable(binary)
    except subprocess.TimeoutExpired as error:
        stdout, stderr = error.stdout or b"", error.stderr or b""
        (directory / "stdout.bin").write_bytes(stdout)
        (directory / "stderr.bin").write_bytes(stderr)
        (directory / "execution.json").write_text(json.dumps({
            "owner": owner, "target": target, "status": "TIMEOUT",
            "returncode": None, "timeout_s": error.timeout,
            "stdout_hex": stdout.hex(), "stderr_hex": stderr.hex(),
            "binary_sha256": hashlib.sha256(binary.read_bytes()).hexdigest(),
        }, indent=2))
        raise
    (directory / "stdout.bin").write_bytes(actual.stdout)
    (directory / "stderr.bin").write_bytes(actual.stderr)
    (directory / "execution.json").write_text(json.dumps({
        "owner": owner, "target": target, "status": "COMPLETE",
        "returncode": actual.returncode,
        "stdout_hex": actual.stdout.hex(), "stderr_hex": actual.stderr.hex(),
        "expected_returncode": expected.returncode,
        "expected_stdout_hex": expected.stdout.hex(),
        "expected_stderr_hex": expected.stderr.hex(),
        "binary_sha256": hashlib.sha256(binary.read_bytes()).hexdigest(),
    }, indent=2))
    assert_exact(actual, expected)


@pytest.mark.parametrize("target", TARGETS)
@pytest.mark.parametrize("name,source,value", CASES, ids=[case[0] for case in CASES])
def test_cpp_platform_cross_objects(tmp_path, monkeypatch, target, name, source, value):
    """Real object emission for both targets, explicitly no load/run claim."""
    units = ((name + ".cpp", source),)
    with monkeypatch.context() as guard:
        guard.setattr(subprocess, "Popen", _forbidden_process)
        compiled = compile_target_objects(units, target=target)
        assert compiled == compile_target_objects(units, target=target)
        if target == DARWIN_TARGET:
            from pcc.backend import macho_spec
            for obj in compiled.objects:
                parsed = macho_spec.parse_object(obj)
                assert parsed.header["cputype"] == 0x0100000C
                assert parsed.header["filetype"] == macho_spec.MH_OBJECT
            observer = darwin_observer_object(compiled.entry_symbol)
            symbols = {s["name"] for s in macho_spec.parse_object(observer).symbols()}
            assert {"_main", "_write", "_" + compiled.entry_symbol} <= symbols
            (tmp_path / "observer.o").write_bytes(observer)
        else:
            assert target == LINUX_TARGET
            for obj in compiled.objects:
                assert obj[:6] == b"\x7fELF\x02\x01"
                assert struct.unpack_from("<H", obj, 18)[0] == 62
    (tmp_path / "source.cpp").write_text(source)
    _save_objects(tmp_path, compiled)
    (tmp_path / "cross-object.json").write_text(json.dumps({
        "owner": "host-python/pcc-cpp/pcc-self", "target": target,
        "executed": False, "linked": False,
        "source_sha256": hashlib.sha256(source.encode()).hexdigest(),
        "objects_sha256": [hashlib.sha256(obj).hexdigest() for obj in compiled.objects],
    }, indent=2))


@pytest.mark.integration
@pytest.mark.parametrize("name,source,value", CASES, ids=[case[0] for case in CASES])
def test_cpp_platform_owned_native_execution(tmp_path, monkeypatch, name, source, value):
    target = native_target()  # Unsupported real hosts fail, never skip.
    with monkeypatch.context() as guard:
        guard.setattr(subprocess, "Popen", _forbidden_process)
        artifact = compile_native_cpp(((name + ".cpp", source),))
    if target == DARWIN_TARGET:
        from pcc.backend import macho_spec
        header = macho_spec.parse_object(artifact.executable).header
        assert header["cputype"] == 0x0100000C
        assert header["filetype"] == macho_spec.MH_EXECUTE
    else:
        assert artifact.executable[:6] == b"\x7fELF\x02\x01"
    (tmp_path / "source.cpp").write_text(source)
    _save_objects(tmp_path, artifact)
    binary = tmp_path / "owned"
    write_host_executable(artifact, binary)
    expected = Execution(value & 255, struct.pack("<i", value), b"")
    for attempt in (1, 2):
        output = tmp_path / f"run-{attempt}"
        output.mkdir()
        _record_run(binary, output, expected, artifact.owner, target)


@pytest.mark.integration
@pytest.mark.parametrize("name,source,value", CASES, ids=[case[0] for case in CASES])
def test_cpp_platform_explicit_cpp17_reference(tmp_path, name, source, value):
    target = native_target()
    requested = os.environ.get("PCC_CPP_ORACLE")
    assert requested, "set an explicit clang++/g++ reference owner"
    compiler = shutil.which(requested)
    assert compiler, "explicit C++17 reference compiler unavailable"
    receipt = {
        "owner": "external-cpp17-reference", "target": target,
        "compiler_invocation": compiler,
        "compiler_resolved": str(Path(compiler).resolve()),
        "compiler_sha256": hashlib.sha256(Path(compiler).read_bytes()).hexdigest(),
        "source_sha256": hashlib.sha256(source.encode()).hexdigest(),
        "options": ["-std=c++17", "-pedantic-errors", "-O0"],
    }
    try:
        binary = compile_reference_cpp(((name + ".cpp", source),),
                                       tmp_path / "reference", compiler=compiler)
    except BaseException as error:
        receipt.update(status="FAIL", error=str(error))
        raise
    else:
        receipt["status"] = "COMPILED"
    finally:
        (tmp_path / "reference-compile.json").write_text(json.dumps(receipt, indent=2))
    expected = Execution(value & 255, struct.pack("<i", value), b"")
    for attempt in (1, 2):
        output = tmp_path / f"run-{attempt}"
        output.mkdir()
        _record_run(binary, output, expected, "external-cpp17-reference", target)


def test_cpp_platform_rejects_unknown_target_before_emission(monkeypatch):
    from pcc.backend import owned_object_emit
    monkeypatch.setattr(owned_object_emit, "emit_owned_object", _forbidden_process)
    with pytest.raises(ValueError, match=r"unsupported C\+\+ test target"):
        compile_target_objects((("entry.cpp", "int cpp_test_entry(){return 0;}"),),
                               target="unknown-unknown-unknown")


def test_cpp_platform_rejects_observer_symbol_injection():
    with pytest.raises(ValueError, match=r"invalid C\+\+ observer entry"):
        darwin_observer_object("entry\nret")


def test_cpp_platform_timeout_keeps_partial_observation(tmp_path, monkeypatch):
    """Reporting-only model; not a compiler or native execution substitute."""
    binary = tmp_path / "reporting-fixture"
    binary.write_bytes(b"not an executable")

    def timeout(_binary):
        raise subprocess.TimeoutExpired(["reporting-fixture"], 10,
                                        output=b"partial", stderr=b"diagnostic")

    monkeypatch.setitem(globals(), "run_executable", timeout)
    with pytest.raises(subprocess.TimeoutExpired):
        _record_run(binary, tmp_path, Execution(0, b"", b""),
                    "reporting-unit-model", "not-a-native-target")
    receipt = json.loads((tmp_path / "execution.json").read_text())
    assert receipt["status"] == "TIMEOUT" and receipt["returncode"] is None
    assert (tmp_path / "stdout.bin").read_bytes() == b"partial"
    assert (tmp_path / "stderr.bin").read_bytes() == b"diagnostic"
