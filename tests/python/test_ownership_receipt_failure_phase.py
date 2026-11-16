"""Failed IR qualification must not be reported as a compiler failure."""

import json
from pathlib import Path

import pytest

from tests.python import owned_regression_support as support


@pytest.mark.parametrize("compiler_fails", [False, True])
@pytest.mark.parametrize("evidence_failure", [AssertionError, OSError])
def test_ownership_receipt_keeps_failure_phase_and_original_exception(
    tmp_path, monkeypatch, capfd, compiler_fails, evidence_failure,
):
    archive = tmp_path / "runtime.a"
    archive.write_bytes(b"test runtime identity")
    source = tmp_path / "program.py"
    source.write_text("pass\n")
    monkeypatch.setattr(support, "assert_reference_program", lambda *args, **kwargs: (
        source, {"returncode": 0, "stdout": "", "stderr": ""},
    ))
    compile_error = RuntimeError("compiler rejected source")
    capture_error = evidence_failure("indexed capture was incomplete")
    observed = []

    def compiler(source_path, binary_path, **kwargs):
        if compiler_fails:
            raise compile_error
        Path(binary_path).write_bytes(b"test output identity")

    def record_capture(*args, **kwargs):
        observed.append(kwargs["completed"])
        raise capture_error

    monkeypatch.setattr(support, "_record_observed_ir_dump", record_capture)
    original = compile_error if compiler_fails else capture_error
    with pytest.raises(type(original)) as caught:
        support.assert_owned_program(
            "pass\n", "", tmp_path, compiler, "pcc0", archive, capfd,
            emitted_ir_check=lambda modules: None,
        )
    assert caught.value is original
    receipt = json.loads((tmp_path / "ownership-regression.json").read_text())
    assert receipt["status"] == ("COMPILE_FAILED" if compiler_fails else "IR_EVIDENCE_FAILED")
    assert receipt["compiler_completed"] is (not compiler_fails)
    assert receipt["executions"] == []
    assert receipt["error"] == type(original).__name__ + ": " + str(original)
    assert observed == [not compiler_fails]
    if compiler_fails:
        assert receipt["ir_capture_error"] == type(capture_error).__name__ + ": " + str(capture_error)
    else:
        assert "ir_capture_error" not in receipt
