"""A missing Python frontend module is reported as exactly that.

The diagnostic used to add that the pipeline was "currently Phase 1 MVP and
some components may still be under construction", which described a plan
stage long past and suggested the failure was expected.
"""

from __future__ import annotations

import sys

import pytest


def test_missing_frontend_module_reports_only_the_import_failure(monkeypatch, tmp_path):
    from pcc.frontends.python.pipeline import compile_python
    from pcc.frontends.python.pipeline_modes import PyPipelineError

    source = tmp_path / "program.py"
    source.write_text("print(1)\n", encoding="utf-8")
    # A None entry makes ``from pcc.frontends.python.type_infer import ...``
    # raise ImportError inside the pipeline, as a missing module would.
    monkeypatch.setitem(sys.modules, "pcc.frontends.python.type_infer", None)
    with pytest.raises(PyPipelineError) as raised:
        compile_python(str(source), str(tmp_path / "program"), emit_llvm_only=True)
    message = str(raised.value)
    assert message.startswith("Python frontend module not available: ")
    assert "pcc.frontends.python.type_infer" in message
    assert "Phase" not in message
    assert "under construction" not in message
