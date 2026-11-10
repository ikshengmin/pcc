"""Finalizer-created weakrefs follow the post-resurrection silent-clear phase.

Reference: CPython v3.15.0 Python/gc.c:1554-1574 and docs/project-intent.md.
Still-dead targets lose both late weakrefs without callbacks. Resurrection
preserves them until a later true death, when the callback sees both cleared.
Producer/consumer thread exit removes conservative target aliases.

The reference lane is separately selected with PCC_RUN_CPYTHON_REFERENCE=1
and requires CPython 3.15.0 exactly. Owned pcc0/pcc1 lanes execute the same
source under all five GCs, using an explicit source/codegen-matched runtime
and existing compiler fixtures, with no provisioning or foreign fallback.
The shared helper fixes Python IR passes off and limits each emitted/reference
program to ten seconds with process-group cleanup; collection attempts stay
bounded at eight. Probe 2 does not select GC mode. This is not default-pass,
bootstrap, Mac, or performance qualification. Execution is CI-only.
"""

import hashlib
import json
import os
from pathlib import Path
import sys
import textwrap

import pytest

from tests.python.owned_regression_support import (
    _record_execution,
    explicit_owned_runtime,
)


PROGRAM = textwrap.dedent("""\
    import gc
    import threading
    import weakref

    refs = []
    saved = []
    observations = []
    finalized = []
    checked = []

    def callback(ref):
        observations.append(ref() is None)
        observations.append(refs[0]() is None)
        # Reentrant collection must not redeliver this pending callback.
        gc.collect()

    class Item:
        def __init__(self, resurrect):
            self.cycle = self
            self.payload = 777
            self.resurrect = resurrect

        def __del__(self):
            finalized.append(self.resurrect)
            refs.append(weakref.ref(self))
            refs.append(weakref.ref(self, callback))
            if self.resurrect:
                saved.append(self)

    def produce_dead():
        Item(False)

    def produce_resurrected():
        Item(True)

    def check_and_drop_resurrected():
        checked.append(refs[0]() is saved[0])
        checked.append(refs[1]() is saved[0])
        checked.append(saved[0].payload == 777)
        saved.clear()

    def run_worker(function):
        worker = threading.Thread(target=function)
        worker.start()
        worker.join()

    def main():
        gc.disable()
        run_worker(produce_dead)
        for attempt in range(8):
            gc.collect()
            if len(finalized) == 1:
                break
        assert finalized == [False], finalized
        assert len(refs) == 2
        # Late refs to a still-dead cyclic target clear without callbacks.
        assert refs[0]() is None and refs[1]() is None
        assert observations == [], observations
        refs.clear()
        run_worker(produce_resurrected)
        for attempt in range(8):
            gc.collect()
            if len(saved) == 1:
                break
        assert finalized == [False, True], finalized
        assert len(refs) == 2 and len(saved) == 1
        assert observations == [], observations
        # Preserve both late refs across resurrection, then drop the last
        # external owner on a thread whose stack will no longer be scanned.
        run_worker(check_and_drop_resurrected)
        assert checked == [True, True, True], checked
        for attempt in range(8):
            gc.collect()
            if len(observations) == 2:
                break
        assert observations == [True, True], observations
        assert finalized == [False, True], finalized
        assert refs[0]() is None and refs[1]() is None
        print('FINALIZER_CREATED_WEAKREFS_OK')

    main()
""")


EXPECTED = (0, "FINALIZER_CREATED_WEAKREFS_OK\n", "")


def _assert_result(result):
    assert (result["returncode"], result["stdout"], result["stderr"]) == EXPECTED, result


@pytest.mark.pcc_gate(env="PCC_RUN_CPYTHON_REFERENCE")
def test_finalizer_created_weakrefs_cpython_reference(tmp_path):
    assert sys.implementation.name == "cpython", "Reference requires CPython"
    assert sys.version_info == (3, 15, 0, "final", 0), (
        "Reference requires CPython 3.15.0", sys.version,
    )
    source = tmp_path / "program.py"
    source.write_text(PROGRAM)
    receipt = {
        "compiler_mode": "cpython-reference",
        "interpreter": sys.executable,
        "interpreter_version": sys.version,
        "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "status": "RUNNING",
    }
    receipt_path = tmp_path / "finalizer-created-weakrefs-reference.json"
    receipt_path.write_text(json.dumps(receipt, indent=2) + "\n")
    try:
        result = _record_execution(
            tmp_path, "reference", [sys.executable, str(source)], dict(os.environ),
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
    # Set guards before the function-scoped compiler fixture can look up pcc1.
    monkeypatch.setenv("PCC_TEST_NO_NATIVE_PROVISIONING", "1")
    monkeypatch.setenv("PCC_NO_AUTO_PCC1", "1")
    monkeypatch.setenv("PCC_TEST_COMPILER_STRICT", "1")


@pytest.mark.integration
@pytest.mark.parametrize(
    "python_program_compiler", ("pcc0", "pcc1"),
    indirect=True, ids=("host-pcc0", "native-pcc1"),
)
def test_finalizer_created_weakrefs_native_five_gc(
    explicit_owned_runtime, python_program_compiler, request, tmp_path, capfd,
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
    source = tmp_path / "program.py"
    source.write_text(PROGRAM)
    binary = tmp_path / "program.out"
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
        "provenance_probe": "2",
        "provenance_probe_selects_gc_mode": False,
        "reference": "Separate CPython reference test; not run by this native lane",
        "executions": [],
    }
    if compiler_binary is not None:
        receipt["compiler_binary"] = str(compiler_binary)
        receipt["compiler_binary_sha256"] = hashlib.sha256(
            compiler_binary.read_bytes()
        ).hexdigest()
    receipt_path = tmp_path / "finalizer-created-weakrefs-owned.json"

    def save():
        receipt_path.write_text(json.dumps(receipt, indent=2) + "\n")

    save()
    try:
        try:
            compiler(
                str(source), str(binary), backend="self", ir_scaffold_mode="on",
                runtime_archive=str(archive),
            )
        finally:
            captured = capfd.readouterr()
            (tmp_path / "compiler-wrapper.stdout").write_text(captured.out)
            (tmp_path / "compiler-wrapper.stderr").write_text(captured.err)
        assert captured.err == "", captured.err
        assert binary.is_file(), "Compiler did not emit an executable"
    except Exception as error:
        receipt["status"] = "COMPILE_FAILED"
        receipt["error"] = type(error).__name__ + ": " + str(error)
        save()
        raise

    receipt["binary_sha256"] = hashlib.sha256(binary.read_bytes()).hexdigest()
    receipt["status"] = "RUNNING"
    save()
    try:
        for backend in range(5):
            environment = dict(
                os.environ,
                PCC_GC_BACKEND=str(backend),
                PCC_GC_REFCOUNT_PROVENANCE_PROBE="2",
                PCC_TEST_NO_NATIVE_PROVISIONING="1",
                PCC_NO_AUTO_PCC1="1",
                PCC_HOST_PYTHON="/nonexistent/host-python",
                PCC_HOST_PCC="/nonexistent/host-pcc",
                PATH="",
            )
            result = _record_execution(
                tmp_path, f"gc{backend}", [str(binary)], environment,
            )
            receipt["executions"].append({
                "gc_backend": backend,
                "ownership_mode": "counted" if backend == 0 else "count-free",
                **result,
            })
            save()
            _assert_result(result)
    except Exception as error:
        receipt["status"] = "NATIVE_EXECUTION_FAILED"
        receipt["error"] = type(error).__name__ + ": " + str(error)
        raise
    else:
        receipt["status"] = "PASS"
    finally:
        save()
