"""Pure host timing/policy/dispatch tests; no compiler or native invocation."""

import ast
import io
import sys
import types
from pathlib import Path

import pytest

from pcc.backend import BackendUnavailable
from pcc.backend import indexed_phase_timing as timing_module
from pcc.backend import target_objects
from pcc.backend.indexed_phase_timing import ModulePhaseTiming


TARGETS = (
    "arm64-apple-darwin23.6.0",
    "aarch64-unknown-linux-gnu",
    "x86_64-unknown-linux-gnu",
    "x86_64-pc-windows-msvc",
)


class Probe:
    def __init__(self):
        self.ids = []
        self.starts = 0

    def start(self):
        self.starts += 1
        return self.starts

    def add(self, phase, started):
        assert started > 0
        self.ids.append(phase)


def test_fixed_nanosecond_aggregates_keep_small_contributions(monkeypatch):
    ticks = iter((100, 107, 200, 211))
    monkeypatch.setattr(timing_module.time, "perf_counter_ns", lambda: next(ticks))
    totals = ModulePhaseTiming("sample")
    totals.add(5, totals.start())
    totals.add(5, totals.start())
    assert totals.totals_ns == [0, 0, 0, 0, 0, 18, 0, 0, 0, 0, 0, 0, 0]
    assert len(timing_module._PHASE_NAMES) == 13
    assert not totals.failed


@pytest.mark.parametrize("failure", ("start", "end", "backwards", "invalid-phase", "zero"))
def test_clock_failure_is_best_effort_and_disables_later_reads(monkeypatch, failure):
    calls = []

    def clock():
        calls.append(1)
        if failure == "zero":
            return 0
        if failure == "start" or (failure == "end" and len(calls) == 2):
            raise RuntimeError("clock unavailable")
        return 100 if len(calls) == 1 else (99 if failure == "backwards" else 120)

    monkeypatch.setattr(timing_module.time, "perf_counter_ns", clock)
    totals = ModulePhaseTiming("sample")
    started = totals.start()
    totals.add(13 if failure == "invalid-phase" else 0, started)
    assert totals.failed
    before = len(calls)
    assert totals.start() == 0
    totals.add(0, 100)
    assert len(calls) == before
    assert totals.totals_ns == [0] * 13


@pytest.mark.parametrize("complete", (False, True))
def test_report_is_once_bounded_and_marks_inclusive_fields(complete):
    totals = ModulePhaseTiming("sample")
    totals.target = TARGETS[0]
    totals.route = "indexed-native-sections"
    stream = io.StringIO()
    totals.report(stream, complete)
    totals.report(stream, complete)
    lines = stream.getvalue().splitlines()
    assert len(lines) == 1
    assert "module=sample target=" + TARGETS[0] in lines[0]
    assert "route=indexed-native-sections" in lines[0]
    assert "codegen_complete=" + str(int(complete)) in lines[0]
    assert "clock_ok=1" in lines[0]
    assert "backend_call_inclusive_ns=0" in lines[0]
    assert "function_emit_inclusive_ns=0" in lines[0]
    assert lines[0].count("_ns=") == 13
    assert set(vars(totals)) == {
        "module_name", "target", "route", "totals_ns", "failed", "reported",
    }


def test_reporting_failure_never_replaces_error_and_is_not_retried():
    calls = []

    class BadStream:
        def write(self, text):
            calls.append(text)
            raise OSError("diagnostic stream closed")

    totals = ModulePhaseTiming("sample")
    totals.report(BadStream(), False)
    totals.report(BadStream(), False)
    assert len(calls) == 1


def _stub(monkeypatch, name, **attrs):
    module = types.ModuleType(name)
    for key, value in attrs.items():
        setattr(module, key, value)
    monkeypatch.setitem(sys.modules, name, module)


@pytest.mark.parametrize("target", TARGETS)
@pytest.mark.parametrize("enabled", (False, True))
def test_indexed_dispatch_keeps_target_options_and_output_identity(monkeypatch, target, enabled):
    output = object()
    received = []
    totals = Probe() if enabled else None
    module = types.SimpleNamespace(triple=target)
    plans = [] if target == TARGETS[2] else None

    def arm(actual, **kwargs):
        received.append((actual, kwargs))
        return output

    def x86(text, **kwargs):
        assert text == ""
        received.append((kwargs.pop("module"), kwargs))
        return output

    _stub(monkeypatch, "pcc.backend.self_backend_aarch64_darwin", emit_aarch64_darwin_indexed_module=arm)
    _stub(monkeypatch, "pcc.backend.self_backend_x86_64_linux", _emit_x86_64_module=x86)
    result = target_objects.emit_indexed_assembly(
        module, optimize=False, stack_map_plans_out=plans, phase_timing=totals,
    )
    assert result is output
    assert len(received) == 1 and received[0][0] is module
    assert received[0][1]["phase_timing"] is totals
    if target in TARGETS[:2]:
        assert received[0][1]["optimize"] is False
    else:
        assert received[0][1]["windows"] == (target == TARGETS[3])
        assert received[0][1]["stack_map_plans_out"] is plans


@pytest.mark.parametrize("target", TARGETS)
@pytest.mark.parametrize("enabled", (False, True))
def test_object_dispatch_preserves_calls_and_exact_return(monkeypatch, target, enabled):
    totals = Probe() if enabled else None
    calls = []
    obj = object()
    payload = b"unchanged-object"

    def assemble(text, *args):
        assert text == "assembly"
        calls.append("assemble")
        return obj

    def encode(actual):
        assert actual is obj
        calls.append("encode")
        return payload

    def arm_assemble(text):
        assert text == "assembly"
        calls.append("assemble")
        return obj, ()

    def arm_encode(actual, *, undefined):
        assert undefined == ()
        return encode(actual)

    def coff(text, *, phase_timing=None):
        assert phase_timing is totals
        # The real COFF split is checked separately below.
        result = encode(assemble(text))
        if phase_timing is not None:
            phase_timing.add(9, phase_timing.start())
            phase_timing.add(10, phase_timing.start())
        return result

    _stub(monkeypatch, "pcc.backend.owned_elf_link", assemble=assemble)
    _stub(monkeypatch, "pcc.backend.elf_x86_64", emit_relocatable=encode)
    _stub(monkeypatch, "pcc.backend.arm64_asm_driver", assemble_file=arm_assemble)
    _stub(monkeypatch, "pcc.backend.native_object", encode_native_object_from_sections=arm_encode)
    _stub(monkeypatch, "pcc.backend.coff_x86_64", assemble_object=coff)
    result = target_objects.encode_assembly_object("assembly", target, phase_timing=totals)
    assert result is payload and calls == ["assemble", "encode"]
    if enabled:
        assert totals.ids == [9, 10]


@pytest.mark.parametrize("enabled", (False, True))
def test_packed_stackmap_route_keeps_consumption_and_symbols(monkeypatch, enabled):
    totals = Probe() if enabled else None
    plans = []
    symbol = object()
    label = object()
    assembled = object()
    encoded = b"packed"
    calls = []

    def assemble(text, actual_plans, **kwargs):
        assert text == "assembly" and actual_plans is plans
        assert kwargs == {
            "function_symbol": symbol, "block_label": label,
            "consume_stack_map_plans": True,
        }
        calls.append("assemble")
        return assembled

    def encode(actual):
        assert actual is assembled
        calls.append("encode")
        return encoded

    _stub(monkeypatch, "pcc.backend.x86_64_asm_driver", assemble_file_with_stack_maps=assemble)
    _stub(monkeypatch, "pcc.backend.self_backend_x86_64_linux", _asm_symbol=symbol, _block_label=label)
    _stub(monkeypatch, "pcc.backend.elf_x86_64", emit_relocatable=encode)
    result = target_objects.encode_assembly_object(
        "assembly", TARGETS[2], stack_map_plans=plans,
        consume_stack_map_plans=True, phase_timing=totals,
    )
    assert result is encoded and calls == ["assemble", "encode"]
    if enabled:
        assert totals.ids == [9, 10]


@pytest.mark.parametrize("failure", ("assemble", "encode"))
@pytest.mark.parametrize("enabled", (False, True))
def test_object_error_identity_and_order_are_preserved(monkeypatch, failure, enabled):
    totals = Probe() if enabled else None
    error = ValueError("original object failure")
    calls = []
    obj = object()

    def assemble(text, target):
        calls.append("assemble")
        if failure == "assemble":
            raise error
        return obj

    def encode(actual):
        assert actual is obj
        calls.append("encode")
        raise error

    _stub(monkeypatch, "pcc.backend.owned_elf_link", assemble=assemble)
    _stub(monkeypatch, "pcc.backend.elf_x86_64", emit_relocatable=encode)
    with pytest.raises(ValueError) as caught:
        target_objects.encode_assembly_object("assembly", TARGETS[2], phase_timing=totals)
    assert caught.value is error
    assert calls == (["assemble"] if failure == "assemble" else ["assemble", "encode"])
    if enabled:
        assert totals.ids == ([] if failure == "assemble" else [9])


@pytest.mark.parametrize("enabled", (False, True))
def test_coff_boundary_is_ordinary_assemble_then_emit(monkeypatch, enabled):
    from pcc.backend import coff_x86_64

    totals = Probe() if enabled else None
    obj = object()
    payload = b"COFF"
    calls = []

    def assemble(text):
        assert text == "text"
        calls.append("assemble")
        return obj

    def emit(actual):
        assert actual is obj
        calls.append("emit")
        return payload

    monkeypatch.setattr(coff_x86_64, "assemble", assemble)
    monkeypatch.setattr(coff_x86_64, "emit_object", emit)
    assert coff_x86_64.assemble_object("text", phase_timing=totals) is payload
    assert calls == ["assemble", "emit"]
    if enabled:
        assert totals.ids == [9, 10]


@pytest.mark.parametrize("enabled", (False, True))
def test_unsupported_target_error_is_unchanged(enabled):
    totals = Probe() if enabled else None
    with pytest.raises(BackendUnavailable, match="^object emission target is unavailable: invalid$"):
        target_objects.encode_assembly_object("assembly", "invalid", phase_timing=totals)
    if enabled:
        assert totals.starts == 0 and totals.ids == []


def test_worker_reuses_existing_gate_and_reports_after_codegen_timer():
    source = Path(__file__).resolve().parents[2]
    text = (source / "pcc/frontends/python/pipeline_frontend_worker_execution.py").read_text()
    tree = ast.parse(text)
    gated = []
    reports = []
    for node in ast.walk(tree):
        if isinstance(node, ast.If) and isinstance(node.test, ast.Name) and node.test.id == "worker_timing":
            if any(isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "ModulePhaseTiming" for n in ast.walk(node)):
                gated.append(node)
        if isinstance(node, ast.Try) and node.finalbody:
            if any(isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "report" and isinstance(n.func.value, ast.Name) and n.func.value.id == "phase_timing" for n in node.finalbody for n in ast.walk(n)):
                reports.append(node)
    assert len(gated) == 1 and len(reports) == 1
    assert 'PCC_INDEXED_PHASE_TIMING' not in text
    assert 'codegen_ms = int((time.monotonic() - codegen_started) * 1000)' in ast.get_source_segment(text, reports[0])
    assert not any(isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "report" for body in reports[0].body for n in ast.walk(body))
