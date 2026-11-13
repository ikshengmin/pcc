"""Shared execution checks for owned-runtime source regressions.

Use the existing archive/compiler fixtures and process-group timeout helper.
Callers supply an explicit source-matched archive; these regressions never
provision a runtime or compiler. Native compiler parameters require a current
pcc1 selected by the existing python_program_compiler fixture.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import sys
import time

import pytest

from tests.python.process_timeout import run_process_group_timeout


@pytest.fixture
def explicit_owned_runtime(request, monkeypatch):
    selected = os.environ.get("PCC_RUNTIME_ARCHIVE")
    assert selected, "Set PCC_RUNTIME_ARCHIVE to a source-matched owned archive"
    monkeypatch.setenv("PCC_TEST_NO_NATIVE_PROVISIONING", "1")
    monkeypatch.setenv("PCC_NO_AUTO_PCC1", "1")
    monkeypatch.setenv("PCC_TEST_COMPILER_STRICT", "1")
    archive = request.getfixturevalue("pcc_runtime_archive")
    assert archive == Path(selected).resolve(strict=True)

    # The shared fixture verifies archive members and runtime source hashes.
    # Also bind it to the code generator under test before any compilation.
    from pcc.tools.runtime_archive_provenance import (
        manifest_is_stale_for_current_codegen,
    )

    manifest = json.loads(Path(str(archive) + ".provenance.json").read_text())
    assert not manifest_is_stale_for_current_codegen(manifest), (
        "Selected runtime was built by a different code generator"
    )
    for name, value in {
        "PCC_RUNTIME_CC": "pcc",
        "PCC_RUNTIME_HIGH": "py",
        "PCC_SELF_LINK": "pcc",
        "PCC_SELF_OBJ": "pcc",
        "PCC_IR_TO_OBJ_EMITTER": "pcc",
        "PCC_PYTHON_IR_PASSES": "off",
        "PCC_DISABLE_PY_RUN_CACHE": "1",
        "PCC_HOST_PYTHON": "/nonexistent/host-python",
        "PCC_HOST_PCC": "/nonexistent/host-pcc",
        "PATH": "",
    }.items():
        monkeypatch.setenv(name, value)
    return archive


def _record_execution(directory, label, command, environment):
    start_ns = time.monotonic_ns()
    result = run_process_group_timeout(command, env=environment, timeout=10)
    end_ns = time.monotonic_ns()
    (directory / (label + ".stdout")).write_text(result.stdout)
    (directory / (label + ".stderr")).write_text(result.stderr)
    record = {
        "command": list(command),
        "returncode": result.returncode,
        "stdout": result.stdout,
        "stderr": result.stderr,
        "start_ns": start_ns,
        "end_ns": end_ns,
        "elapsed_ns": end_ns - start_ns,
    }
    (directory / (label + ".json")).write_text(json.dumps(record, indent=2) + "\n")
    return record


def assert_reference_program(program, expected, directory, *, arguments=()):
    source = directory / "program.py"
    source.write_text(program)
    environment = dict(os.environ)
    environment.pop("LC_ALL", None)
    result = _record_execution(
        directory, "reference", [sys.executable, str(source), *arguments], environment,
    )
    assert (result["returncode"], result["stdout"], result["stderr"]) == (
        0, expected, "",
    ), result
    return source, result


def assert_owned_program(
    program,
    expected,
    directory,
    compiler,
    compiler_mode,
    archive,
    capfd,
    *,
    provenance_probe=None,
    backends=tuple(range(5)),
    reference_arguments=(),
    emitted_ir_check=None,
):
    backends = tuple(backends)
    assert backends and len(backends) == len(set(backends))
    assert all(type(backend) is int and 0 <= backend < 5 for backend in backends)
    source, oracle = assert_reference_program(
        program, expected, directory, arguments=reference_arguments,
    )
    binary = directory / "program.out"
    requested_compiler_mode = compiler_mode
    if compiler.__module__ == "tests.pcc1_route":
        # Whole-suite native routing can replace the nominal pcc0 fixture.
        compiler_mode = "pcc1-routed"
    receipt = {
        "status": "RUNNING",
        "run_id": os.environ.get("PCC_NATIVE_OBJECT_GATE_RUN_ID", ""),
        "compiler_fixture_parameter": requested_compiler_mode,
        "compiler_mode": compiler_mode,
        "backend": "self",
        "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "binary_path": str(binary.resolve()),
        "runtime_archive": str(archive),
        "runtime_sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
        "reference": oracle,
        "gc_backends": list(backends),
        "executions": [],
    }
    receipt_path = directory / "ownership-regression.json"
    receipt_path.write_text(json.dumps(receipt, indent=2) + "\n")
    try:
        # Capture the IR consumed by this executable emission, not a second
        # host/native --emit-llvm invocation with merely matching source.
        with pytest.MonkeyPatch.context() as patch:
            if emitted_ir_check is not None:
                dump = directory / "executable-ir"
                dump.mkdir()  # Fresh and empty: stale output is never evidence.
                patch.setenv("PCC_DEBUG_SELF_IR_DUMP_DIR", str(dump))
            compiler(
                str(source), str(binary), backend="self", ir_scaffold_mode="on",
                runtime_archive=str(archive),
            )
        if emitted_ir_check is not None:
            emitted = sorted(dump.glob("self_backend_input_*.ll"))
            assert emitted, "Executable compiler produced no self-backend IR receipt"
            # Preserve module scope: internal map names may legally repeat.
            emitted_ir_check([path.read_text() for path in emitted])
            receipt["executable_input_ir"] = {
                path.name: hashlib.sha256(path.read_bytes()).hexdigest()
                for path in emitted
            }
            assert hashlib.sha256(source.read_bytes()).hexdigest() == receipt["source_sha256"]
            assert hashlib.sha256(archive.read_bytes()).hexdigest() == receipt["runtime_sha256"]
    except Exception as error:
        receipt["status"] = "COMPILE_FAILED"
        receipt["error"] = type(error).__name__ + ": " + str(error)
        receipt_path.write_text(json.dumps(receipt, indent=2) + "\n")
        raise
    finally:
        captured = capfd.readouterr()
        (directory / "compiler-wrapper.stdout").write_text(captured.out)
        (directory / "compiler-wrapper.stderr").write_text(captured.err)
    assert binary.is_file(), "Compiler did not emit an executable"
    # compile_python and the native CLI have no libpython selector: their
    # current pipeline is strict. Establish the output property independently
    # rather than copying that intended policy into a successful receipt.
    from scripts.owned_image_linkage import inspect_owned_image

    try:
        linkage = inspect_owned_image(binary.read_bytes())
    except Exception as error:
        receipt["status"] = "LINKAGE_FAILED"
        receipt["error"] = type(error).__name__ + ": " + str(error)
        receipt_path.write_text(json.dumps(receipt, indent=2) + "\n")
        raise
    receipt["linkage"] = linkage
    receipt["libpython"] = linkage["libpython"]
    receipt["binary_sha256"] = linkage["binary_sha256"]
    for backend in backends:
        environment = dict(
            os.environ,
            PCC_GC_BACKEND=str(backend),
            PCC_TEST_NO_NATIVE_PROVISIONING="1",
            PCC_NO_AUTO_PCC1="1",
            PCC_HOST_PYTHON="/nonexistent/host-python",
            PCC_HOST_PCC="/nonexistent/host-pcc",
            PATH="",
        )
        environment.pop("LC_ALL", None)
        if provenance_probe is not None:
            environment["PCC_GC_REFCOUNT_PROVENANCE_PROBE"] = provenance_probe
        result = _record_execution(
            directory, f"gc{backend}", [str(binary)], environment,
        )
        receipt["executions"].append({"gc_backend": backend, **result})
        matches = all(result[key] == oracle[key] for key in (
            "returncode", "stdout", "stderr",
        ))
        if not matches:
            receipt["status"] = "NATIVE_EXECUTION_FAILED"
        receipt_path.write_text(json.dumps(receipt, indent=2) + "\n")
        assert matches, (compiler_mode, backend, result)
    receipt["status"] = "PASS"
    receipt_path.write_text(json.dumps(receipt, indent=2) + "\n")
