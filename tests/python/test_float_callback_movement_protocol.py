"""Verify that float callback CI evidence retains both live scenarios."""
import ast
from pathlib import Path

import pytest

from tests.python.test_native_float_callback_movement import MODES, _record_float_execution


@pytest.mark.parametrize("failed_index", (None, 0, 1))
def test_float_receipt_preserves_every_execution_and_failure(failed_index):
    receipt = {"status": "RUNNING", "executions": [], "source_sha256": "source"}
    for index, mode in enumerate(MODES):
        row = dict(execution_mode=mode, stdout=f"evidence-{index}",
                   status="NATIVE_EXECUTION_FAILED" if index == failed_index else "PASS")
        _record_float_execution(receipt, row)
        row["stdout"] = "OVERWRITTEN_LOCAL"
        assert receipt["executions"][-1]["stdout"] == f"evidence-{index}"
        expected = ("NATIVE_EXECUTION_FAILED" if failed_index is not None and failed_index <= index
                    else "PASS" if index == len(MODES) - 1 else "RUNNING")
        assert receipt["status"] == expected
    assert [row["execution_mode"] for row in receipt["executions"]] == list(MODES)
    assert receipt["source_sha256"] == "source"


def test_float_receipt_rejects_reordered_modes():
    receipt = {"status": "RUNNING", "executions": []}
    with pytest.raises(AssertionError):
        _record_float_execution(receipt, dict(execution_mode=MODES[-1], status="PASS"))
    assert receipt == {"status": "RUNNING", "executions": []}


def test_float_ci_allowlisted_receipt_writes_aggregate_not_last_mode():
    source = Path(__file__).with_name("test_native_float_callback_movement.py")
    module = ast.parse(source.read_text())
    function = next(node for node in module.body if isinstance(node, ast.FunctionDef)
                    and node.name == "test_canonical_float_callback_cleanup")
    loop = next(node for node in function.body if isinstance(node, ast.For))
    statements = [ast.unparse(node) for node in loop.body]
    record_at = statements.index("_record_float_execution(receipt, current)")
    writes = [(index, value) for index, value in enumerate(statements)
              if value.startswith("receipt_path.write_text(")]
    assert len(writes) == 1
    assert writes[0][0] > record_at
    assert "json.dumps(receipt, indent=2)" in writes[0][1]
