"""Direct public-ABI witness, using owned C/self emission and real threads.

This requires an explicitly supplied, source/codegen-matched threaded archive.
It never provisions a compiler/runtime and does not establish pcc1 parity,
whole-language acceptance, concurrent-collector acceptance, or a reproduced
lifetime bug in an existing caller.
"""

import hashlib
import json
import os
from pathlib import Path

import pytest

from tests.python.owned_regression_support import _record_execution


PROGRAM = '''from pcc.extern import c_int64, extern
probe = extern("root_copy_lease_heap_destination", (c_int64,), c_int64)
pause = extern("root_copy_lease_pause_collector", (), c_int64)
def main():
    assert pause() == 0
    assert probe(0) == 0
    assert probe(1) == 0
    print("ROOT_COPY_DESTINATION_LEASE_OK")
main()
'''


@pytest.mark.integration
def test_stack_copy_to_heap_survives_source_thread_exit_gc3_gc4(
    tmp_path, monkeypatch, capfd,
):
    from pcc.backend.owned_object_emit import emit_owned_object
    from pcc.frontends.python.pipeline import compile_python
    from pcc.frontends.python.pipeline_targets import host_target_triple
    from tests.python.test_owned_external_tls import _c_ir
    from tests.runtime_fixture_provenance import _verified_test_runtime_archive

    selected = os.environ.get("PCC_THREADED_RUNTIME_ARCHIVE")
    assert selected, "Set PCC_THREADED_RUNTIME_ARCHIVE to a source-matched owned archive"
    monkeypatch.setenv("PCC_TEST_NO_NATIVE_PROVISIONING", "1")
    monkeypatch.setenv("PCC_NO_AUTO_PCC1", "1")
    monkeypatch.setenv("PCC_WITH_THREADS", "1")
    archive, manifest = _verified_test_runtime_archive(selected, threads=True)
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

    fixture = Path(__file__).with_name("fixtures") / "gc_root_copy_lease_destination.c"
    source = tmp_path / "program.py"
    source.write_text(PROGRAM)
    helper = tmp_path / "root_copy_destination.o"
    binary = tmp_path / "program.out"
    target = host_target_triple()
    receipt = {
        "status": "COMPILING",
        "compiler_mode": "pcc0",
        "backend": "self",
        "threads": True,
        "reference": "Not applicable: direct native root/lease ABI",
        "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "helper_source_sha256": hashlib.sha256(fixture.read_bytes()).hexdigest(),
        "runtime_archive": str(archive),
        "runtime_sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
        "runtime_manifest_sha256": hashlib.sha256(
            json.dumps(manifest, sort_keys=True).encode()
        ).hexdigest(),
        "provenance_probe": "0",
        "executions": [],
    }
    receipt_path = tmp_path / "root-copy-destination-regression.json"
    receipt_path.write_text(json.dumps(receipt, indent=2) + "\n")
    try:
        helper.write_bytes(emit_owned_object(_c_ir(fixture.read_text(), target), target))
        compile_python(
            str(source), str(binary), backend="self", ir_scaffold_mode="on",
            runtime_archive=str(archive), object_inputs=(str(helper),),
        )
    except Exception as error:
        receipt["status"] = "COMPILE_FAILED"
        receipt["error"] = type(error).__name__ + ": " + str(error)
        receipt_path.write_text(json.dumps(receipt, indent=2) + "\n")
        raise
    finally:
        captured = capfd.readouterr()
        (tmp_path / "compiler-wrapper.stdout").write_text(captured.out)
        (tmp_path / "compiler-wrapper.stderr").write_text(captured.err)
    assert binary.is_file(), "Compiler did not emit an executable"
    receipt["binary_sha256"] = hashlib.sha256(binary.read_bytes()).hexdigest()
    receipt["status"] = "RUNNING"
    for backend in (3, 4):
        environment = dict(
            os.environ,
            PCC_GC_BACKEND=str(backend),
            PCC_GC_REFCOUNT_PROVENANCE_PROBE="0",
            PCC_GC_MINOR_HEAP_SIZE="1048576",
            PCC_GC_MINOR_ALLOC_MAX="4096",
        )
        result = _record_execution(tmp_path, f"gc{backend}", [str(binary)], environment)
        receipt["executions"].append({"gc_backend": backend, **result})
        observed = (result["returncode"], result["stdout"], result["stderr"])
        expected = (0, "ROOT_COPY_DESTINATION_LEASE_OK\n", "")
        if observed != expected:
            receipt["status"] = "NATIVE_EXECUTION_FAILED"
        receipt_path.write_text(json.dumps(receipt, indent=2) + "\n")
        assert observed == expected, (backend, result)
    receipt["status"] = "PASS"
    receipt_path.write_text(json.dumps(receipt, indent=2) + "\n")
