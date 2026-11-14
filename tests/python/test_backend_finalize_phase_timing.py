"""Bounded finalization receipts, unchanged output, and emitted-program witnesses.

These are diagnostic/correctness checks, not Stage2 or performance measurements.
"""

import ast
import copy
import os
from pathlib import Path
import subprocess
import sys
import types

import pytest

from pcc.backend import BackendUnavailable
from pcc.backend import indexed_phase_timing as timing


ROOT = Path(__file__).resolve().parents[2]
EMITTER = "pcc/backend/self_backend_aarch64_darwin.py"
PHASES = ("prepare", "layout", "stackmaps", "unwind", "dwarf", "sections")
TARGETS = ("arm64-apple-darwin23.6.0", "aarch64-unknown-linux-gnu")


class DiagnosticAbort(BaseException):
    pass


class Sink:
    def __init__(self, fault=None):
        self.fault = fault
        self.lines = []

    def write(self, text):
        self.lines.append(text)
        if self.fault == "write":
            raise DiagnosticAbort("diagnostic write")

    def flush(self):
        if self.fault == "flush":
            raise DiagnosticAbort("diagnostic flush")


def _rows(text):
    lines = text.splitlines()
    assert all(line.startswith("pcc coarse phase ") for line in lines)
    return [dict(part.split("=", 1) for part in line.split()[3:]) for line in lines]


def _source_function(name):
    tree = ast.parse((ROOT / EMITTER).read_text())
    return next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == name)


def _tail():
    function = _source_function("_emit_prepared_aarch64_darwin_lines_active")
    index = next(index for index, node in enumerate(function.body)
                 if isinstance(node, ast.For) and ast.unparse(node.iter) == "stack_map_plans.values()")
    body = function.body[index - 1:index + 2]
    assert ast.unparse(body[0].test) == "phase_timing is not None"
    assert ast.unparse(body[2].test) == "native_sink is not None"
    return body


def _compile_body(body, namespace):
    function = ast.parse("def invoke():\n    pass\n").body[0]
    function.body = copy.deepcopy(body)
    tree = ast.Module(body=[function], type_ignores=[])
    exec(compile(ast.fix_missing_locations(tree), "<production finalization tail>", "exec"), namespace)
    return namespace["invoke"]


# Frozen before adding receipts. This guards operation order, aliases and the
# original aggregate start/add placement, not just the names of mock callbacks.
_LEGACY_TAIL = '''
if phase_timing is not None:
    phase_start = phase_timing.start()
# All function bodies and native captures have finished. Retire only the
# private plans' emission storage before final layout/metadata allocate.
# Keep the eager planning and callback order: callbacks or repeated names
# can make a later body reuse an earlier dictionary entry. Record/root
# metadata and exceptional labels remain live for the final consumers.
for plan in stack_map_plans.values():
    if plan.packed_records is not None:
        plan.packed_records.retire_emission_state()
if native_sink is not None:
    if (
        structured_sections is None or structured_counts is None
        or native_undefined is None or native_fallback_lines is None
    ):
        raise BackendUnavailable("native text emission sinks are incomplete")
    if optimize:
        metadata_labels: set[str] = set()
        for func in functions:
            plan = stack_map_plans[func.name]
            packed = plan.packed_records
            if packed is not None:
                exceptional_blocks = packed.exceptional_blocks
            else:
                exceptional_blocks = [record.exceptional_block for record in plan.records]
            for exceptional_block in exceptional_blocks:
                if exceptional_block:
                    metadata_labels.add(_block_label(func.name, exceptional_block))
        native_sink.finish_optimized_instructions(metadata_labels, cold_fallthrough_edges)
    native_sink.builder.finalize_text_layout()
    target_offsets = native_sink.builder.text_label_offsets()
    if functions:
        if emit_stack_map_tables:
            ordered_native_plans = tuple(stack_map_plans[func.name] for func in functions)
            structured_sections.append(build_aarch64_stack_map_section(
                [], ordered_native_plans,
                function_symbol=_function_symbol, block_label=_block_label,
                target_offsets=target_offsets,
            ))
        else:
            for func in functions:
                packed_records = stack_map_plans[func.name].packed_records
                if packed_records is not None:
                    packed_records.close()
        if close_native_tables:
            for func in functions:
                get_indexed_function_kernel(func).close_native_tables()
    if not is_aarch64_linux_triple(triple):
        native_sink.extend(_append_compact_unwind(
            [], functions, _MODULE_SYMBOLS, target_offsets,
            native_sink.builder.text_size(),
        ))
    structured_sections.extend(build_dwarf_sections(debug_plans, target_offsets))
    native_sink.append(".subsections_via_symbols")
    sections, undefined = native_sink.builder.finish(structured_sections)
    structured_sections.clear()
    structured_sections.extend(sections)
    native_undefined.extend(undefined)
    native_fallback_lines.extend(native_sink.fallback_lines)
    structured_counts.extend([
        native_sink.structured_count, len(native_sink.fallback_lines),
        native_sink.unscaled_count, native_sink.move_count,
        native_sink.call_count, native_sink.direct_count,
        native_sink.fragment_record_count,
    ])
    if phase_timing is not None:
        phase_timing.add(7, phase_start)
    return []
'''


def test_receipt_erasure_restores_exact_legacy_tail_and_never_times_loops():
    body = _tail()

    class EraseReceipts(ast.NodeTransformer):
        def visit_Expr(self, node):
            if _receipt_call(node.value):
                return None
            return self.generic_visit(node)

        def visit_Assign(self, node):
            if _receipt_call(node.value):
                return None
            return self.generic_visit(node)

        def visit_If(self, node):
            node = self.generic_visit(node)
            return node if node.body else None

    tree = ast.Module(body=copy.deepcopy(body), type_ignores=[])
    erased = EraseReceipts().visit(tree)
    assert ast.dump(erased, include_attributes=False) == ast.dump(ast.parse(_LEGACY_TAIL), include_attributes=False)
    calls = [node for stmt in body for node in ast.walk(stmt) if _receipt_call(node)]
    assert sorted((node.args[0].value, node.func.attr) for node in calls) == sorted(
        (phase, method) for phase in PHASES
        for method in ("begin_backend_finalize", "complete_backend_finalize")
    )
    for stmt in body:
        for node in ast.walk(stmt):
            if isinstance(node, (ast.For, ast.While, ast.comprehension, ast.Try)):
                assert not any(_receipt_call(child) for child in ast.walk(node))
    # No resource observer, new policy, global clock, or function/instruction
    # event was introduced into the production emitter.
    assert "publish_worker_resource" not in ast.unparse(ast.Module(body=body, type_ignores=[]))


def _receipt_call(node):
    return (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name) and node.func.value.id == "phase_timing"
            and node.func.attr in ("begin_backend_finalize", "complete_backend_finalize"))


def _mock_tail(monkeypatch, *, enabled=True, linux=False, optimize=True, tables=True,
               close=True, empty=False, failure=None, error_type=ValueError, diagnostic_fault=None):
    operations, reads = [], []
    error = error_type("original operation")
    sink = Sink(diagnostic_fault)

    def clock():
        reads.append(None)
        if diagnostic_fault == "clock":
            raise DiagnosticAbort("diagnostic clock")
        return len(reads) * 10

    monkeypatch.setattr(timing, "sys", types.SimpleNamespace(stderr=sink))
    monkeypatch.setattr(timing.time, "perf_counter_ns", clock)
    timer = timing.ModulePhaseTiming("fixture.finalize") if enabled else None

    def operation(name):
        operations.append(name)
        if failure == name:
            raise error

    packed = types.SimpleNamespace(
        exceptional_blocks=["error", ""],
        retire_emission_state=lambda: operation("retire"), close=lambda: operation("close"),
    )
    plan = types.SimpleNamespace(packed_records=packed)
    functions = [] if empty else [types.SimpleNamespace(name="probe")]
    offsets, section, dwarf, final, undefined = {}, object(), object(), object(), object()
    sections, native_undefined, fallback, counts = [], [], [], []

    def finish_optimized(metadata_labels, cold_edges):
        operation("optimized")
        assert metadata_labels == (set() if empty else {"probe.error"})
        assert cold_edges == []

    def final_offsets():
        operation("offsets")
        return offsets

    def stackmaps(lines, plans, *, function_symbol, block_label, target_offsets):
        operation("stackmaps")
        assert lines == [] and plans == (plan,) and target_offsets is offsets
        return section

    def unwind(lines, funcs, symbols, target_offsets, text_size):
        operation("unwind")
        assert lines == [] and funcs is functions and target_offsets is offsets and text_size == 8
        return ["unwind"]

    def build_dwarf(plans, target_offsets):
        operation("dwarf")
        assert plans == [] and target_offsets is offsets
        return [dwarf]

    def finish(source_sections):
        operation("sections")
        assert source_sections is sections
        assert source_sections == ([section, dwarf] if tables and not empty else [dwarf])
        return [final], [undefined]

    builder = types.SimpleNamespace(
        finalize_text_layout=lambda: operation("layout"), text_label_offsets=final_offsets,
        text_size=lambda: 8, finish=finish,
    )
    native_sink = types.SimpleNamespace(
        builder=builder, finish_optimized_instructions=finish_optimized,
        extend=lambda lines: operation("unwind_extend"),
        append=lambda line: operation("directive"),
        fallback_lines=["fallback"], structured_count=1, unscaled_count=2,
        move_count=3, call_count=4, direct_count=5, fragment_record_count=6,
        close=lambda: operation("sink_close"),
    )
    namespace = dict(
        phase_timing=timer, native_sink=native_sink, stack_map_plans={} if empty else {"probe": plan},
        structured_sections=sections, structured_counts=counts, native_undefined=native_undefined,
        native_fallback_lines=fallback, optimize=optimize, functions=functions,
        cold_fallthrough_edges=[], emit_stack_map_tables=tables, close_native_tables=close,
        triple=TARGETS[int(linux)], debug_plans=[], _MODULE_SYMBOLS=object(),
        BackendUnavailable=BackendUnavailable, _block_label=lambda owner, label: owner + "." + label,
        _function_symbol=lambda name: name, is_aarch64_linux_triple=lambda triple: linux,
        build_aarch64_stack_map_section=stackmaps, build_dwarf_sections=build_dwarf,
        _append_compact_unwind=unwind,
        get_indexed_function_kernel=lambda func: types.SimpleNamespace(close_native_tables=lambda: operation("native_close")),
    )
    invoke = _compile_body(_tail(), namespace)
    return types.SimpleNamespace(
        invoke=invoke, namespace=namespace, operations=operations, reads=reads, error=error,
        sink=sink, timer=timer, result_lists=(sections, native_undefined, fallback, counts),
        expected_lists=([final], [undefined], ["fallback"], [1, 1, 2, 3, 4, 5, 6]),
    )


@pytest.mark.parametrize("enabled", (False, True))
@pytest.mark.parametrize("linux", (False, True))
@pytest.mark.parametrize("optimize", (False, True))
@pytest.mark.parametrize("tables", (False, True))
@pytest.mark.parametrize("close", (False, True))
@pytest.mark.parametrize("empty", (False, True))
def test_native_tail_keeps_every_mode_order_and_handoff(monkeypatch, enabled, linux, optimize, tables, close, empty):
    run = _mock_tail(monkeypatch, enabled=enabled, linux=linux, optimize=optimize,
                     tables=tables, close=close, empty=empty)
    assert run.invoke() == []
    expected = ([] if empty else ["retire"]) + (["optimized"] if optimize else []) + ["layout", "offsets"]
    if not empty:
        expected += ["stackmaps" if tables else "close"] + (["native_close"] if close else [])
    expected += ([] if linux else ["unwind", "unwind_extend"]) + ["dwarf", "directive", "sections"]
    assert run.operations == expected
    assert run.result_lists == run.expected_lists
    phases = [phase for phase in PHASES if phase != "unwind" or not linux]
    rows = _rows("".join(run.sink.lines))
    assert [(row["phase"], row["event"]) for row in rows] == (
        [(phase, event) for phase in phases for event in ("begin", "complete")] if enabled else []
    )
    if enabled:
        assert len(run.reads) == 2 + 2 * len(phases)
        assert len(run.timer.totals_ns) == 16 and run.timer.totals_ns[7] > 0
        assert all(value == 0 for index, value in enumerate(run.timer.totals_ns) if index != 7)
    else:
        assert run.reads == []


@pytest.mark.parametrize("enabled", (False, True))
@pytest.mark.parametrize("failure,phase", (
    ("retire", "prepare"), ("optimized", "prepare"), ("layout", "layout"),
    ("offsets", "layout"), ("stackmaps", "stackmaps"), ("native_close", "stackmaps"),
    ("unwind", "unwind"), ("dwarf", "dwarf"), ("directive", "sections"), ("sections", "sections"),
))
@pytest.mark.parametrize("error_type", (ValueError, DiagnosticAbort))
@pytest.mark.parametrize("diagnostic_fault", (None, "clock", "write", "flush"))
def test_failure_keeps_original_error_cleanup_and_incomplete_receipt(
    monkeypatch, enabled, failure, phase, error_type, diagnostic_fault,
):
    run = _mock_tail(monkeypatch, enabled=enabled, failure=failure,
                     error_type=error_type, diagnostic_fault=diagnostic_fault)
    # Execute the unchanged production scope owner around the extracted tail.
    # Its finally blocks close the native sink/capture before releasing active.
    wrapper = _source_function("_emit_prepared_aarch64_darwin_lines")
    namespace = dict(run.namespace)
    namespace.update(
        _AARCH64_EMISSION_ACTIVE=False, has_source_debug=lambda functions: False,
        _NativeAArch64Emission=lambda **kwargs: run.namespace["native_sink"],
        begin_direct_instruction_capture=lambda **kwargs: run.operations.append("capture_begin"),
        end_direct_instruction_capture=lambda: run.operations.append("capture_end"),
        require_direct_instruction_capture_idle=lambda: None,
        _emit_prepared_aarch64_darwin_lines_active=lambda *args, **kwargs: run.invoke(),
    )
    tree = ast.Module(body=[ast.parse("from __future__ import annotations").body[0], wrapper], type_ignores=[])
    exec(compile(ast.fix_missing_locations(tree), "<production emission scope>", "exec"), namespace)
    invoke = namespace[wrapper.name]
    with pytest.raises(error_type) as caught:
        invoke(types.SimpleNamespace(functions=[]), native_undefined=[], phase_timing=run.timer)
    assert caught.value is run.error
    assert run.operations[0] == "capture_begin"
    assert run.operations[-3:] == [failure, "sink_close", "capture_end"]
    assert namespace["_AARCH64_EMISSION_ACTIVE"] is False
    rows = _rows("".join(run.sink.lines))
    expected = []
    if enabled:
        for current in PHASES[:PHASES.index(phase) + 1]:
            expected.append((current, "begin"))
            if current != phase:
                expected.append((current, "complete"))
        assert run.timer.totals_ns == [0] * 16
    else:
        assert run.reads == []
    assert [(row["phase"], row["event"]) for row in rows] == expected


def test_prepare_receipt_begins_before_existing_sink_validation(monkeypatch):
    run = _mock_tail(monkeypatch)
    run.namespace["structured_counts"] = None
    with pytest.raises(BackendUnavailable, match="native text emission sinks are incomplete"):
        run.invoke()
    assert run.operations == ["retire"]
    assert [(row["phase"], row["event"]) for row in _rows("".join(run.sink.lines))] == [("prepare", "begin")]


_IR = '''define i64 @probe(i64 %value) {
entry:
  %sum = add i64 %value, 7
  ret i64 %sum
}
'''


@pytest.mark.parametrize("target", TARGETS)
@pytest.mark.parametrize("optimize", (False, True))
@pytest.mark.parametrize("tables", (False, True))
@pytest.mark.parametrize("empty", (False, True))
def test_real_transport_timing_preserves_encoded_sections(monkeypatch, target, optimize, tables, empty):
    from pcc.backend.self_backend_aarch64_darwin import emit_aarch64_darwin_indexed_transport
    from pcc.backend.self_backend_parse import parse_self_backend_module
    from pcc.backend.native_object import encode_native_object_from_sections

    monkeypatch.setenv("PCC_PRECISE_STACKMAP_TABLES", "1" if tables else "0")
    monkeypatch.delenv("PCC_SELF_TARGET_PASS_TRANSPORT", raising=False)
    source = 'target triple = "' + target + '"\n' + ("" if empty else _IR)
    outcomes = []
    sink = Sink()
    monkeypatch.setattr(timing, "sys", types.SimpleNamespace(stderr=sink))
    for enabled in (False, True):
        sink.lines.clear()
        timer = timing.ModulePhaseTiming("fixture.transport") if enabled else None
        transport = emit_aarch64_darwin_indexed_transport(
            parse_self_backend_module(source), optimize=optimize, phase_timing=timer,
        )
        assert transport.native_finalized
        assert transport.fallback_instruction_count == 0
        sections, undefined = transport.assemble_sections()
        outcomes.append((sections, undefined, encode_native_object_from_sections(sections, undefined=undefined)))
        phases = [phase for phase in PHASES if phase != "unwind" or target == TARGETS[0]]
        assert [(row["phase"], row["event"]) for row in _rows("".join(sink.lines))] == (
            [(phase, event) for phase in phases for event in ("begin", "complete")] if enabled else []
        )
    assert outcomes[0] == outcomes[1]


PROGRAM = '''from pcc.backend.indexed_phase_timing import ModulePhaseTiming

def main():
    timer = ModulePhaseTiming("fixture.finalize")
    for phase in ("prepare", "layout", "stackmaps", "unwind", "dwarf", "sections"):
        started = timer.begin_backend_finalize(phase)
        timer.complete_backend_finalize(phase, started)
    assert len(timer.totals_ns) == 16
    for value in timer.totals_ns:
        assert value == 0
    assert not timer.failed
    assert timer.begin_backend_finalize("unknown") == 0
    timer.complete_backend_finalize("unknown", 1)
    print("finalize receipts preserve sixteen aggregates")

main()
'''


def _check_program_receipts(stderr):
    rows = _rows(stderr)
    assert [(row["phase"], row["event"]) for row in rows] == [
        (phase, event) for phase in PHASES for event in ("begin", "complete")
    ]
    for row in rows:
        assert row["scope"] == row["parent"] == "backend_finalize"
        assert row["module"] == "fixture.finalize" and row["clock_ok"] == "1"
        assert ("duration_ns" in row) is (row["event"] == "complete")
        if row["event"] == "complete":
            assert int(row["duration_ns"]) >= 0


def test_compiled_receipts_match_same_source_reference(
    tmp_path, pcc_runtime_archive, python_program_compiler,
):
    source = tmp_path / "finalize_receipts.py"
    source.write_text(PROGRAM, encoding="utf-8")
    environment = dict(os.environ, PYTHONPATH=str(ROOT))
    reference = subprocess.run([sys.executable, str(source)], capture_output=True,
                               text=True, timeout=20, env=environment)
    assert reference.returncode == 0, reference.stderr
    assert reference.stdout == "finalize receipts preserve sixteen aggregates\n"
    _check_program_receipts(reference.stderr)
    binary = tmp_path / "finalize_receipts"
    python_program_compiler(str(source), str(binary), backend="self", runtime_archive=str(pcc_runtime_archive))
    for backend in range(5):
        ran = subprocess.run([str(binary)], capture_output=True, text=True, timeout=20,
                             env=dict(environment, PCC_GC_BACKEND=str(backend)))
        assert ran.returncode == 0, f"GC{backend}: {ran.stdout}; {ran.stderr}"
        assert ran.stdout == reference.stdout, f"GC{backend}"
        _check_program_receipts(ran.stderr)


@pytest.mark.pcc_gate(platforms=("linux", "darwin"), machines=("aarch64", "arm64"))
def test_native_aarch64_compile_emits_actual_finalization_receipts(
    tmp_path, monkeypatch, capfd, pcc_runtime_archive, python_program_compiler,
):
    monkeypatch.setenv("PCC_PY_FRONTEND_WORKER_TIMING", "1")
    monkeypatch.setenv("PCC_DIRECT_INDEXED_KERNEL_EMIT", "1")
    monkeypatch.setenv("PCC_DIRECT_INDEXED_KERNEL_CAPTURE", "1")
    monkeypatch.setenv("PCC_DIRECT_INDEXED_NATIVE_OBJECT", "1")
    monkeypatch.setenv("PCC_PY_FRONTEND_JOBS", "1")
    # Select the production native sink, not an inherited sidecar/text/deferred
    # experiment. Leave resource guards and correctness checks unchanged.
    for name in (
        "PCC_DIRECT_INDEXED_KERNEL_VALIDATE", "PCC_TEXT_INDEXED_KERNEL_EMIT",
        "PCC_DIRECT_INDEXED_SIDECAR", "PCC_INDEXED_HANDOFF_REQUEST",
        "PCC_PY_FRONTEND_IN_PROCESS_CODEGEN", "PCC_HOST_INDEXED_PROCESS_SPLIT",
        "PCC_DEFER_SELF_LINK_PLAN", "PCC_DEFER_FRONTEND_CODEGEN_PLAN",
    ):
        monkeypatch.delenv(name, raising=False)
    source = tmp_path / "finalize_native.py"
    source.write_text("def main():\n    print(6 * 7)\n\nmain()\n", encoding="utf-8")
    reference = subprocess.run([sys.executable, str(source)], capture_output=True, text=True, timeout=20)
    assert reference.returncode == 0 and reference.stderr == ""
    binary = tmp_path / "finalize_native"
    capfd.readouterr()
    python_program_compiler(str(source), str(binary), backend="self",
                            runtime_archive=str(pcc_runtime_archive), ir_scaffold_mode="off")
    captured = capfd.readouterr().err
    saved = Path(str(binary) + ".compile.stderr")
    if saved.exists():
        captured += saved.read_text(encoding="utf-8")
    lines = [line for line in captured.splitlines() if line.startswith("pcc coarse phase scope=backend_finalize ")]
    rows = _rows("\n".join(lines))
    assert rows, "compiler route did not execute instrumented native finalization"
    modules = {}
    for row in rows:
        modules.setdefault(row["module"], []).append((row["phase"], row["event"]))
    phases = [phase for phase in PHASES if phase != "unwind" or sys.platform == "darwin"]
    expected = [(phase, event) for phase in phases for event in ("begin", "complete")]
    assert all(events == expected for events in modules.values())
    for backend in range(5):
        ran = subprocess.run([str(binary)], capture_output=True, text=True, timeout=20,
                             env=dict(os.environ, PCC_GC_BACKEND=str(backend)))
        assert ran.returncode == 0, f"GC{backend}: {ran.stdout}; {ran.stderr}"
        assert ran.stdout == reference.stdout and ran.stderr == "", f"GC{backend}"
