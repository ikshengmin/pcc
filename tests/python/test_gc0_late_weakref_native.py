"""GC0 cyclic-finalizer weakref contract from docs/project-intent.md.

Reference: CPython v3.15.0 Python/gc.c:1568-1574 and Lib/test/test_gc.py:239-309.
The reference lane is separately selected with PCC_RUN_CPYTHON_REFERENCE=1
and the registered pcc_gate marker; it requires CPython 3.15.0 exactly. Native
lanes require an explicit source/codegen-matched owned archive and existing
compiler, with no provisioning or foreign fallback. They select GC0 only:
GC1-GC4 late-weakref behavior is outside this regression's scope.

The shared helper fixes Python IR passes off and limits each emitted/reference
program to ten seconds with process-group cleanup. This is not default-pass,
five-GC, bootstrap, Mac, or performance qualification. Compiler routes and
provenance probes are labeled separately; the probe does not select GC mode.
"""

import hashlib
import json
import os
from pathlib import Path
import sys

import pytest

from tests.python.owned_regression_support import (
    _record_execution,
    explicit_owned_runtime,
)


FIXTURE = Path(__file__).with_name("fixtures") / "gc0_late_weakref.py"
EXPECTED = (
    0,
    "late-cleared finalizers=1 early=1 late=0\n"
    "resurrected finalizers=1 early=1 late=0\n"
    "released finalizers=1 early=1 late=1\n",
    "",
)


def _assert_result(result):
    assert (result["returncode"], result["stdout"], result["stderr"]) == EXPECTED, result


@pytest.mark.pcc_gate(env="PCC_RUN_CPYTHON_REFERENCE")
def test_gc0_late_weakref_cpython_reference(tmp_path):
    assert sys.implementation.name == "cpython", "Reference requires CPython"
    assert sys.version_info == (3, 15, 0, "final", 0), (
        "Reference requires CPython 3.15.0", sys.version,
    )
    receipt = {
        "compiler_mode": "cpython-reference",
        "interpreter": sys.executable,
        "interpreter_version": sys.version,
        "source_sha256": hashlib.sha256(FIXTURE.read_bytes()).hexdigest(),
        "status": "RUNNING",
    }
    receipt_path = tmp_path / "gc0-late-weakref-reference.json"
    receipt_path.write_text(json.dumps(receipt, indent=2) + "\n")
    try:
        result = _record_execution(
            tmp_path, "reference", [sys.executable, str(FIXTURE)], dict(os.environ),
        )
        receipt["execution"] = result
        _assert_result(result)
    except Exception as error:
        receipt["status"] = "REFERENCE_FAILED"
        receipt["error"] = type(error).__name__ + ": " + str(error)
        raise
    else:
        receipt["status"] = "PASS"
    finally:
        receipt_path.write_text(json.dumps(receipt, indent=2) + "\n")


@pytest.fixture(autouse=True)
def no_native_provisioning(monkeypatch):
    # Autouse setup precedes the function-scoped compiler fixture, including
    # its native-pcc1 lookup. Strict routing rejects any host fallback.
    monkeypatch.setenv("PCC_TEST_NO_NATIVE_PROVISIONING", "1")
    monkeypatch.setenv("PCC_NO_AUTO_PCC1", "1")
    monkeypatch.setenv("PCC_TEST_COMPILER_STRICT", "1")


@pytest.mark.integration
@pytest.mark.parametrize("provenance_probe", ("0", "2"), ids=("probe0", "probe2"))
@pytest.mark.parametrize(
    "python_program_compiler", ("pcc0", "pcc1"),
    indirect=True, ids=("host-pcc0", "native-pcc1"),
)
def test_gc0_late_weakref_owned_native(
    explicit_owned_runtime, python_program_compiler, request, provenance_probe,
    tmp_path, capfd,
):
    compiler = python_program_compiler
    selected = request.node.callspec.params["python_program_compiler"]
    compiler_mode = "host-pcc0" if selected == "pcc0" else "native-pcc1"
    compiler_binary = None
    if compiler.__module__ == "tests.pcc1_route":
        compiler_mode = "native-pcc1-routed"
        compiler_binary = Path(os.environ["PCC_TEST_COMPILER"]).resolve(strict=True)
    elif selected == "pcc1":
        compiler_binary = request.getfixturevalue("native_pcc1_compiler")
    archive = explicit_owned_runtime
    source = tmp_path / "gc0_late_weakref.py"
    source.write_bytes(FIXTURE.read_bytes())
    binary = tmp_path / "gc0_late_weakref.out"
    receipt = {
        "status": "COMPILING",
        "compiler_fixture_parameter": selected,
        "compiler_mode": compiler_mode,
        "compiler_entrypoint": compiler.__module__ + "." + compiler.__name__,
        "backend": "self",
        "python_ir_passes": os.environ.get("PCC_PYTHON_IR_PASSES"),
        "default_optimization_pipeline_qualification": False,
        "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "runtime_archive": str(archive),
        "runtime_sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
        "runtime_provenance_sha256": hashlib.sha256(
            Path(str(archive) + ".provenance.json").read_bytes()
        ).hexdigest(),
        "gc_backend": 0,
        "ownership_mode": "counted",
        "provenance_probe": provenance_probe,
        "provenance_probe_selects_gc_mode": False,
        "reference": "Separate CPython reference test; not run by this native lane",
        "executions": [],
    }
    if compiler_binary is not None:
        receipt["compiler_binary"] = str(compiler_binary)
        receipt["compiler_binary_sha256"] = hashlib.sha256(
            compiler_binary.read_bytes()
        ).hexdigest()
    receipt_path = tmp_path / "gc0-late-weakref-owned.json"

    def save():
        receipt_path.write_text(json.dumps(receipt, indent=2) + "\n")

    save()
    try:
        compiler(
            str(source), str(binary), backend="self", ir_scaffold_mode="on",
            runtime_archive=str(archive),
        )
        assert binary.is_file(), "Compiler did not emit an executable"
    except Exception as error:
        receipt["status"] = "COMPILE_FAILED"
        receipt["error"] = type(error).__name__ + ": " + str(error)
        save()
        raise
    finally:
        captured = capfd.readouterr()
        (tmp_path / "compiler-wrapper.stdout").write_text(captured.out)
        (tmp_path / "compiler-wrapper.stderr").write_text(captured.err)

    receipt["binary_sha256"] = hashlib.sha256(binary.read_bytes()).hexdigest()
    receipt["status"] = "RUNNING"
    save()
    environment = dict(
        os.environ,
        PCC_GC_BACKEND="0",
        PCC_GC_REFCOUNT_PROVENANCE_PROBE=provenance_probe,
        PCC_TEST_NO_NATIVE_PROVISIONING="1",
        PCC_NO_AUTO_PCC1="1",
        PCC_HOST_PYTHON="/nonexistent/host-python",
        PCC_HOST_PCC="/nonexistent/host-pcc",
        PATH="",
    )
    try:
        result = _record_execution(tmp_path, "gc0", [str(binary)], environment)
        receipt["executions"].append(result)
        _assert_result(result)
    except Exception as error:
        receipt["status"] = "NATIVE_EXECUTION_FAILED"
        receipt["error"] = type(error).__name__ + ": " + str(error)
        raise
    else:
        receipt["status"] = "PASS"
    finally:
        save()
