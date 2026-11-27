"""Real class-lowering timing contract, host fixture only."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from pcc.frontends.python.codegen import class_gen
from pcc.frontends.python.codegen.layer1 import L1CodeGen
from pcc.frontends.python.py_lift import parse_and_lift
from pcc.frontends.python.type_infer import infer_module


SOURCE = """
class Box:
    def plain(self, value: int) -> int:
        total = value + 1
        return total
    def iterate(self):
        yield 1
        yield 2
    def nested(self):
        class Inner:
            def value(self):
                return 3
        return Inner().value()
"""


class _Clock:
    def __init__(self):
        self.calls = 0

    def monotonic(self):
        self.calls += 1
        return float(self.calls)


def _emit(source=SOURCE):
    module = infer_module(parse_and_lift(source, "method_phase.py", "method_phase"))
    codegen = L1CodeGen(module, ir_scaffold_mode="on")
    codegen._strict_no_libpython = True
    codegen._prefer_native_callable_values = True
    return str(codegen.generate(module))


def _rows(output):
    prefix = "pcc frontend class method phase "
    return [dict(item.split("=", 1) for item in line[len(prefix):].split())
            for line in output.splitlines() if line.startswith(prefix)]


@pytest.fixture(autouse=True)
def _quiet(monkeypatch):
    monkeypatch.delenv("PCC_DEBUG_CODEGEN_PHASES", raising=False)
    monkeypatch.setenv("PCC_PYTHON_IR_PASSES", "off")


@pytest.mark.parametrize("value", [None, "0", "false", "yesplease"])
def test_disabled_method_timing_has_no_clock_or_rows(monkeypatch, capsys, value):
    clock = _Clock()
    monkeypatch.setattr(class_gen, "time", clock, raising=False)
    if value is None:
        monkeypatch.delenv("PCC_PY_FRONTEND_WORKER_TIMING", raising=False)
    else:
        monkeypatch.setenv("PCC_PY_FRONTEND_WORKER_TIMING", value)
    assert _emit()
    assert clock.calls == 0
    assert not _rows(capsys.readouterr().err)


def test_enabled_completed_phase_partition_and_exact_output(monkeypatch, capsys):
    clock = _Clock()
    monkeypatch.setattr(class_gen, "time", clock, raising=False)
    monkeypatch.setenv("PCC_PY_FRONTEND_WORKER_TIMING", "0")
    baseline = _emit()
    capsys.readouterr()
    monkeypatch.setenv("PCC_PY_FRONTEND_WORKER_TIMING", " TRUE ")
    assert _emit() == baseline
    rows = _rows(capsys.readouterr().err)
    assert rows
    plain = next(row for row in rows if row["method"] == "plain")
    generator = next(row for row in rows if row["method"] == "iterate")
    nested = next(row for row in rows if row["method"] == "nested")
    inner = next(row for row in rows if row["method"] == "value")
    assert plain["route"] == "ordinary"
    assert generator["route"] == "continuation"
    assert nested["route"] == "ordinary"
    # Hoisting defers the local class method to its own module definition.
    assert int(nested["end_ms"]) <= int(inner["start_ms"])
    for row in rows:
        boundaries = [int(row[key]) for key in (
            "start_ms", "snapshot_end_ms", "binding_end_ms",
            "boundness_end_ms", "body_end_ms", "return_end_ms",
            "finalize_end_ms", "end_ms")]
        assert boundaries == sorted(boundaries)
        assert int(row["pid"]) > 0
        assert row["module"] == "method_phase"
        assert row["symbol"].startswith("user_method_phase_")
    assert clock.calls <= 8*len(rows)


def test_failed_method_never_reports_complete_or_masks_error(monkeypatch, capsys):
    clock = _Clock()
    monkeypatch.setattr(class_gen, "time", clock, raising=False)
    monkeypatch.setenv("PCC_PY_FRONTEND_WORKER_TIMING", "1")
    original = L1CodeGen._emit_stmts
    def fail(self, statements):
        fd = self.current_func_def
        if fd is not None and fd.name == "plain":
            raise LookupError("original method lowering failure")
        return original(self, statements)
    monkeypatch.setattr(L1CodeGen, "_emit_stmts", fail)
    with pytest.raises(LookupError, match="original method lowering failure"):
        _emit()
    assert not any(row["method"] == "plain" for row in _rows(capsys.readouterr().err))


def test_reentrant_method_intervals_use_same_clock(monkeypatch, capsys):
    clock = _Clock()
    monkeypatch.setattr(class_gen, "time", clock)
    monkeypatch.setenv("PCC_PY_FRONTEND_WORKER_TIMING", "0")
    baseline = _emit()
    capsys.readouterr()
    original = L1CodeGen._emit_stmts
    nested_once = False
    def nested(self, statements):
        nonlocal nested_once
        fd = self.current_func_def
        if fd is not None and fd.name == "plain" and not nested_once:
            nested_once = True
            assert _emit("class Reentrant:\n    def inside(self):\n        return 1\n")
        return original(self, statements)
    monkeypatch.setattr(L1CodeGen, "_emit_stmts", nested)
    monkeypatch.setenv("PCC_PY_FRONTEND_WORKER_TIMING", "1")
    assert _emit() == baseline
    rows = _rows(capsys.readouterr().err)
    plain = next(r for r in rows if r["method"] == "plain")
    inside = next(r for r in rows if r["method"] == "inside")
    assert int(plain["boundness_end_ms"]) <= int(inside["start_ms"])
    assert int(inside["end_ms"]) <= int(plain["body_end_ms"])
    assert plain["pid"] == inside["pid"]


def test_deferred_stub_route_reports_attempt_not_ordinary_body(monkeypatch, capsys):
    from pcc.frontends.python.codegen.errors import CPythonValueRequired
    from pcc.ir.compat import ir
    original = L1CodeGen._emit_stmts
    def deferred(self, statements):
        fd = self.current_func_def
        if fd is not None and fd.name == "plain":
            target = ir.Function(self.module, ir.FunctionType(ir.VoidType(), []),
                                 name="py_cpy_timing_fixture")
            self.builder.call(target, [])
            raise CPythonValueRequired("fixture native-only deferred stub")
        return original(self, statements)
    monkeypatch.setattr(L1CodeGen, "_emit_stmts", deferred)
    clock = _Clock()
    monkeypatch.setattr(class_gen, "time", clock)
    monkeypatch.setenv("PCC_PY_FRONTEND_WORKER_TIMING", "0")
    baseline = _emit()
    assert "strict.nolib.stub" in baseline
    capsys.readouterr()
    monkeypatch.setenv("PCC_PY_FRONTEND_WORKER_TIMING", "1")
    assert _emit() == baseline
    row = next(r for r in _rows(capsys.readouterr().err) if r["method"] == "plain")
    assert row["route"] == "deferred_stub"
    assert row["snapshot_end_ms"] == row["binding_end_ms"] == row["boundness_end_ms"]
    assert row["body_end_ms"] == row["return_end_ms"]


def test_direct_capture_is_byte_exact_with_method_timing(monkeypatch, tmp_path, capsys):
    from pcc.backend.self_backend_indexed_codec import encode_indexed_module_file
    monkeypatch.setenv("PCC_DIRECT_INDEXED_KERNEL_CAPTURE", "1")
    monkeypatch.setenv("PCC_DIRECT_INDEXED_KERNEL_EMIT", "1")
    outputs = []
    for enabled in ("0", "1"):
        monkeypatch.setenv("PCC_PY_FRONTEND_WORKER_TIMING", enabled)
        module = infer_module(parse_and_lift(SOURCE, "method_phase.py", "method_phase"))
        codegen = L1CodeGen(module, ir_scaffold_mode="on")
        codegen._strict_no_libpython = True
        codegen._prefer_native_callable_values = True
        text = codegen.generate(module)
        assert codegen._direct_indexed_module is not None
        assert codegen.module._direct_indexed_fallback_records == 0
        path = tmp_path / ("method_" + enabled + ".pidx")
        encode_indexed_module_file(str(path), codegen._direct_indexed_module)
        outputs.append((text, path.read_bytes()))
        assert bool(_rows(capsys.readouterr().err)) == (enabled == "1")
    assert outputs[0] == outputs[1]
