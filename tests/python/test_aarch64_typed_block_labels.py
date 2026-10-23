"""Tiny host label transport checks, without compiling or running a program."""

from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from pcc.backend import self_backend_aarch64_darwin as emitter
from pcc.backend import self_backend_aarch64_darwin_mem as capture
from pcc.backend.arm64_asm_driver import AArch64ModuleBuilder
from pcc.backend.arm64_encode import EncodeError
from pcc.backend.self_backend_ir import PARSED_INSTRUCTION_KIND_CALL
from pcc.backend.self_backend_precise_stackmaps import (
    FunctionStackMapPlan,
    PackedPlannedSafepoints,
)
from pcc.backend.self_backend_value_arena import (
    CompilerInt2,
    CompilerInt4,
    CompilerIntArena,
)


class _TinyKernel:
    def __init__(self, names, layout):
        self.block_names = names
        self.block_layout_ids = CompilerIntArena()
        for block_id in layout:
            self.block_layout_ids.append(block_id)
        self.error_edge_scalars = []

    def block_fact(self, block_id):
        return CompilerInt4(block_id, 1, 0, -1)

    def inline_error_edge_span(self, block_id):
        return CompilerInt2(0, 0)

    def instruction_metadata_by_id(self, instruction_id):
        return CompilerInt4(PARSED_INSTRUCTION_KIND_CALL, instruction_id, 0, 0)


class _TextSink(emitter._NativeAArch64Emission):
    """A subclass deliberately keeps the original assembly-text route."""

    def append(self, line):
        self.observed.append(line)
        return super().append(line)


class _TextBuilder(AArch64ModuleBuilder):
    def append_chunk(self, line):
        self.observed.append(line)
        return super().append_chunk(line)


@contextmanager
def _tiny_case(names, layout=(), sink_class=emitter._NativeAArch64Emission, routes=True):
    capture.begin_direct_instruction_capture()
    sink = None
    kernel = _TinyKernel(names, layout)
    records = PackedPlannedSafepoints(block_names=names)
    try:
        for block_id in range(len(names)):
            if routes:
                for route in ("entry", "suffix", "term"):
                    record = records.append(
                        len(records), f"L_{route}_{block_id}", 0, (), 0, "", 0, (),
                    )
                    if route == "entry":
                        records.add_entry_route(block_id, record)
                    elif route == "suffix":
                        records.add_suffix_route(block_id, 0, record)
                    else:
                        records.add_terminator_route(block_id, record, True)
        records.finish_build()
        plan = FunctionStackMapPlan("probe.$f", 1, 0, "L_end", (), (), (), (), "aarch64-darwin", records)
        sink = sink_class()
        sink.observed = []
        sink.append(".section __TEXT,__text,regular,pure_instructions")
        sink.observed.clear()
        yield kernel, plan, sink
    finally:
        if sink is not None:
            sink.close()
        records.close()
        kernel.block_layout_ids.close()
        capture.end_direct_instruction_capture()


def _install_tiny_parts(monkeypatch):
    # Supply fixed words, not the compiler's instruction selection. The real
    # packed loop, call-PC placement, sink, label parser and section finalizer
    # still execute, on at most three hand-written blocks.
    def instruction(*args):
        return [
            capture.emitted_direct_call_line("external"),
            capture.emitted_move_register_line("x1", "x0"),
        ]

    def terminator(func, kernel, block_id, use_id, next_block_id, direct_targets):
        if next_block_id >= 0:
            return [capture.emitted_branch_line(
                "b", emitter._block_label(func.name, kernel.block_names[next_block_id]),
            )]
        return [capture.emitted_fixed_instruction_line("ret")]

    monkeypatch.setattr(emitter, "_emit_dense_indexed_instruction_parts", instruction)
    monkeypatch.setattr(emitter, "_emit_dense_indexed_terminator", terminator)


def _emit(kernel, plan, sink, debug_plan=None):
    return emitter._emit_dense_indexed_function_blocks(
        SimpleNamespace(name="probe.$f"), kernel, plan, sink, debug_plan,
    )


def _finish(kernel, plan, sink, debug_plan=None):
    assert _emit(kernel, plan, sink, debug_plan) == []
    sink.release_captured_function()
    before = dict(sink.builder.text_label_offsets())
    sink.builder.finalize_text_layout()
    assert sink.builder.text_label_offsets() == before
    sections, undefined = sink.builder.finish()
    return before, sections, undefined, (
        sink.structured_count, sink.direct_count, sink.move_count,
        sink.call_count, sink.fragment_record_count, sink.fallback_lines,
    )


@pytest.mark.parametrize("names, layout", [
    ([], ()),
    (["entry"], ()),
    (["entry", "loop.ready.1", "exit$done"], ()),
    (["quoted name", "λ.一", "punctuation:;\n"], (2, 0, 1)),
])
@pytest.mark.parametrize("routes", [False, True], ids=["no-maps", "packed-maps"])
def test_typed_labels_equal_text_parser_bytes_records_and_offsets(monkeypatch, names, layout, routes):
    _install_tiny_parts(monkeypatch)
    with _tiny_case(names, layout, routes=routes) as args:
        actual = _finish(*args)
    with _tiny_case(names, layout, _TextSink, routes) as args:
        expected = _finish(*args)
    assert actual == expected
    offsets, sections, undefined, counters = actual
    order = layout or range(len(names))
    stride = 16 if routes else 12
    for position, block_id in enumerate(order):
        assert offsets[emitter._block_label("probe.$f", names[block_id])] == position * stride
        if routes:
            assert offsets[f"L_entry_{block_id}"] == position * stride
            assert offsets[f"L_suffix_{block_id}"] == position * stride + 4
            assert offsets[f"L_term_{block_id}"] == position * stride + 12
    assert len(sections) == 1
    assert len(sections[0].data) == len(names) * stride
    assert len(sections[0].relocations) == len(names)
    assert undefined == (["external"] if names else [])


def test_canonical_sink_never_reparses_block_label_or_blank(monkeypatch):
    _install_tiny_parts(monkeypatch)
    parser_lines = []
    original = AArch64ModuleBuilder.append_chunk

    def observe(self, line):
        parser_lines.append(line)
        return original(self, line)

    monkeypatch.setattr(AArch64ModuleBuilder, "append_chunk", observe)
    with _tiny_case(["entry", "exit"]) as args:
        parser_lines.clear()
        _finish(*args)
    assert parser_lines == []


@pytest.mark.parametrize("custom_builder", [False, True], ids=["sink-subclass", "builder-subclass"])
def test_custom_append_callbacks_retain_label_and_blank_lines(monkeypatch, custom_builder):
    _install_tiny_parts(monkeypatch)
    sink_class = emitter._NativeAArch64Emission if custom_builder else _TextSink
    with _tiny_case(["entry", "exit"], sink_class=sink_class, routes=False) as args:
        kernel, plan, sink = args
        if custom_builder:
            sink.builder.close()
            sink.builder = _TextBuilder(sink.symbol_names)
            sink.builder.observed = []
            sink.builder.append_chunk(".section __TEXT,__text,regular,pure_instructions")
            sink.builder.observed.clear()
        _finish(*args)
        observed = sink.builder.observed if custom_builder else sink.observed
    labels = [line for line in observed if not line or line.endswith(":")]
    assert labels == ["L_probe.$f_entry:", "", "L_probe.$f_exit:"]


def test_text_only_output_keeps_its_original_spacing(monkeypatch):
    _install_tiny_parts(monkeypatch)
    with _tiny_case(["entry", "exit"], routes=False) as (kernel, plan, sink):
        lines = _emit(kernel, plan, None)
        assert lines[0] == "L_probe.$f_entry:"
        assert lines[4:6] == ["", "L_probe.$f_exit:"]


@pytest.mark.parametrize("sink_class", [emitter._NativeAArch64Emission, _TextSink], ids=["typed", "text"])
@pytest.mark.parametrize("state", ["duplicate", "closed", "finalized", "no-section"])
def test_label_failure_and_finalization_order_is_preserved(monkeypatch, sink_class, state):
    _install_tiny_parts(monkeypatch)
    names = ["same-name", "same name"] if state == "duplicate" else ["entry"]
    with _tiny_case(names, sink_class=sink_class, routes=False) as (kernel, plan, sink):
        if state == "closed":
            sink.builder.close()
            expected = "module builder is closed"
        elif state == "no-section":
            sink.builder.current = None
            expected = "label 'L_probe.$f_entry' before any .section"
        elif state == "finalized":
            sink.builder.finalize_text_layout()
            expected = "text layout is finalized"
        else:
            expected = "duplicate label 'L_probe.$f_same_name'"
        if state in ("closed", "no-section"):
            with pytest.raises(EncodeError) as caught:
                _emit(kernel, plan, sink)
            assert str(caught.value) == expected
            assert sink.builder.closed
        else:
            # Text-layout errors remain deferred, and later append work does
            # not replace the first diagnostic or finalize partial metadata.
            assert _emit(kernel, plan, sink) == []
            assert str(sink.builder.current.text_error) == expected
            with pytest.raises(EncodeError) as caught:
                sink.builder.finalize_text_layout()
            assert str(caught.value) == expected


class _TinyDebugPlan:
    def mark(self, block_id, instruction_index):
        return f"L_debug_{block_id}_{instruction_index + 1}:"


def test_debug_markers_stay_after_block_labels_and_before_instructions(monkeypatch):
    _install_tiny_parts(monkeypatch)
    with _tiny_case(["entry", "exit"]) as args:
        actual = _finish(*args, _TinyDebugPlan())
    with _tiny_case(["entry", "exit"], sink_class=_TextSink) as args:
        expected = _finish(*args, _TinyDebugPlan())
    assert actual == expected
    offsets = actual[0]
    assert offsets["L_debug_0_1"] == offsets["L_probe.$f_entry"] == 0
    assert offsets["L_debug_1_1"] == offsets["L_probe.$f_exit"] == 16
    assert offsets["L_debug_0_0"] == 8
    assert offsets["L_debug_1_0"] == 24


def test_custom_sink_blank_callback_exception_is_not_bypassed(monkeypatch):
    _install_tiny_parts(monkeypatch)

    class BlankFailure(_TextSink):
        def append(self, line):
            if line == "":
                self.observed.append(line)
                raise ValueError("custom blank callback")
            return super().append(line)

    with _tiny_case(["entry", "exit"], sink_class=BlankFailure, routes=False) as args:
        with pytest.raises(ValueError, match="custom blank callback"):
            _emit(*args)
        sink = args[2]
        assert sink.observed[0] == "L_probe.$f_entry:"
        assert sink.observed[-1] == ""
        assert "L_probe.$f_exit:" not in sink.observed
        assert sink.builder.text_label_offsets() == {"L_probe.$f_entry": 0}
