"""The runtime-log event entry stays small enough to be inlined across modules.

``pcc_diagnostics_runtime_log_event_code`` returned at once when logging
was off, yet py_obj_getitem_i64 and the virtual-thread joins and queue
called it unconditionally: 17 calls and frames per handler request that
did nothing (docs/status.md, item 3). The entry is now one relaxed load
and a call to ``pcc_diagnostics_runtime_log_event_code_enabled``; after the
runtime's own passes it remains a candidate of the archive's cross-module
import (``runtime_cross_module_inline``), so callers test the fast state
inline.
"""
from pathlib import Path

from pcc.frontends.python import owned_runtime_build
from pcc.frontends.python.pipeline import compile_python
from pcc.frontends.python.runtime_cross_module_inline import cross_module_inline_table
from pcc.ir.optimization.driver import optimize_ir

ROOT = Path(__file__).resolve().parents[2]


def test_the_log_entry_is_an_import_candidate_after_runtime_passes(tmp_path):
    source = ROOT / "pcc" / "runtime" / "py" / "py_runtime_log.py"
    ir_path = tmp_path / "py_runtime_log.ll"
    compile_python(str(source), str(ir_path), emit_llvm_only=True, python_library=True,
                   backend="self", target_triple="arm64-apple-darwin")
    passes = owned_runtime_build.runtime_ir_passes(str(ROOT / "pcc" / "runtime"))
    optimized = optimize_ir(ir_path.read_text(encoding="utf-8"), passes)
    table = cross_module_inline_table({"py_runtime_log": optimized})
    entry = table["pcc_diagnostics_runtime_log_event_code"]
    body = "\n".join(entry["body"])
    assert "pcc_diagnostics_runtime_log_fast_state" in body
    # The off case returns before anything else is read.
    first = body.index("pcc_diagnostics_runtime_log_fast_state")
    assert "call" not in body[:first], body
    assert "pcc_diagnostics_runtime_log_event_code_enabled" in optimized
