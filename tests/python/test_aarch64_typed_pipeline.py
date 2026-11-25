"""CI-only real-arena typed pipeline admission; runtime/ABI/GC remain separate gates."""
import pytest

from pcc.backend import arm64_encode as encoder
from pcc.backend import self_backend_aarch64_darwin as reference
from pcc.backend import self_backend_aarch64_semantics as sem
from pcc.backend import self_backend_aarch64_typed_cfg as cfg
from pcc.backend import self_backend_aarch64_typed_tlv as tlv
from pcc.backend import self_backend_aarch64_darwin_tlv as text_tlv
from pcc.backend import macho_spec
from pcc.backend.self_backend_aarch64_fragments import (
    AArch64EmissionFragments, EMISSION_RECORD_EMPTY, EMISSION_RECORD_LABEL,
)
from pcc.backend.self_backend_aarch64_typed_pass_context import TypedPassContext
from pcc.backend.self_backend_value_arena import CompilerInt4


@pytest.mark.parametrize("timed", [False, True])
def test_full_module_typed_pass_order_matches_text_object_without_instruction_parser(monkeypatch, capsys, timed):
    from pcc.backend import arm64_encode as encoder
    from pcc.backend import self_backend_aarch64_darwin as backend
    from pcc.backend import self_backend_aarch64_darwin_mem as producer
    from pcc.backend import indexed_phase_timing as timing
    from pcc.backend.arm64_asm_driver import assemble_file, measure_text_layout
    from pcc.backend.native_object import NativeObject, encode_native_object
    from pcc.backend.self_backend_aarch64_darwin_tlv import tlv_resolver_lines, elide_dead_tlv_resolver_saves
    from pcc.backend.self_backend_target_passes import (
        pair_adjacent_aarch64_64bit_memory_ops,
        AARCH64_MEMORY_PAIR_BARRIER_BEGIN as barrier_begin,
        AARCH64_MEMORY_PAIR_BARRIER_END as barrier_end,
    )
    from pcc.backend.self_backend_aarch64_fragments import EMISSION_DIRECTIVE_TEXT_SECTION, EMISSION_DIRECTIVE_GLOBAL, EMISSION_DIRECTIVE_P2ALIGN

    original = [
        ".section __TEXT,__text,regular,pure_instructions", "", ".p2align 2", ".globl _f", "_f:",
        barrier_begin, "  movz x9, #0", "  stur x9, [x29, #-8]", "  movz x9, #1", barrier_end,
        "  cset w11, eq", "  sturb w11, [x29, #-16]", "  ldurb w12, [x29, #-16]",
        "  cbnz w12, L_done", "", "L_trampoline:", "  b L_done", "", "L_done:", "  blr x10", "L_pcc_smap_1:", "  ret", "L_f_end:",
        "", ".p2align 2", ".globl _g", "_g:",
    ]
    resolver = tlv_resolver_lines("_tls", "x9", 31, emitted=True)
    original += resolver + ["  mov x0, x9", "  ret", "L_g_end:"]
    expected_lines = original
    for name in (
        "_forward_adjacent_stack_store_load", "_forward_one_intervening_stack_store_load",
        "_fold_zero_store_source", "_fold_mov_store_source", "_fold_zero_compare_immediate",
        "_fold_mov_compare_source", "_fold_mov_zero_branch_source", "_fold_mov_arith_self_update",
        "_fold_scratch_def_into_move", "_fold_mov_mov_chain", "_fold_zero_test_branch",
        "_fold_forwarded_cset_branch", "_fold_cset_zero_branch", "_drop_dead_cset_branch_stores",
    ):
        expected_lines = getattr(backend, name)(expected_lines)
    expected_lines = backend._thread_trampoline_branches(expected_lines, {"L_trampoline"})
    expected_lines = backend._fold_cond_branch_to_fallthrough(expected_lines, [])
    expected_lines = backend._drop_fallthrough_uncond_branches(expected_lines)
    expected_lines = backend._drop_unreferenced_empty_local_labels(expected_lines, {"L_trampoline"})
    expected_lines = elide_dead_tlv_resolver_saves(expected_lines)
    expected_lines = pair_adjacent_aarch64_64bit_memory_ops(expected_lines, enabled=True)
    expected = assemble_file("\n".join(expected_lines))
    expected_labels, expected_size = measure_text_layout(expected_lines)
    assert "L_trampoline" in expected_labels
    assert expected_labels["L_pcc_smap_1"] == expected_labels["L_done"] + 4

    def forbidden(*args, **kwargs):
        raise AssertionError("optimized native module parsed instruction text")

    monkeypatch.setattr(encoder, "_encode_one", forbidden)
    monkeypatch.setattr(encoder, "assemble_text_lines", forbidden)
    monkeypatch.setattr(backend, "append_emitted_instruction_record", forbidden)
    _forbid_text(monkeypatch)
    producer.begin_direct_instruction_capture(capture_semantics=True)
    sink = None
    try:
        sink = backend._NativeAArch64Emission(optimize=True)
        sink.append_code_directive(EMISSION_DIRECTIVE_TEXT_SECTION)
        sink.append_empty()
        sink.append_code_directive(EMISSION_DIRECTIVE_P2ALIGN, 2)
        sink.append_code_directive(EMISSION_DIRECTIVE_GLOBAL, symbol="_f")
        sink.append_label("_f")
        sink.append(barrier_begin)
        sink.extend([
            producer.emitted_movewide_instruction_line("movz", "x9", 0),
            producer.emitted_memory_instruction_line("stur", "x9", "x29", -8),
            producer.emitted_movewide_instruction_line("movz", "x9", 1),
            barrier_end,
            producer.emitted_cset_line("w11", "eq"),
            producer.emitted_memory_instruction_line("sturb", "w11", "x29", -16),
            producer.emitted_memory_instruction_line("ldurb", "w12", "x29", -16),
            producer.emitted_branch_line("cbnz", "L_done", "w12"),
        ])
        sink.append_empty()
        sink.append_label("L_trampoline")
        sink.append(producer.emitted_branch_line("b", "L_done"))
        sink.append_empty()
        sink.append_label("L_done")
        sink.append(producer.emitted_indirect_branch_line("blr", "x10"))
        sink.append_label("L_pcc_smap_1")
        sink.append(producer.emitted_fixed_instruction_line("ret"))
        sink.append_label("L_f_end")
        sink.release_captured_function()
        sink.append_empty()
        sink.append_code_directive(EMISSION_DIRECTIVE_P2ALIGN, 2)
        sink.append_code_directive(EMISSION_DIRECTIVE_GLOBAL, symbol="_g")
        sink.append_label("_g")
        sink.extend(tlv_resolver_lines("_tls", "x9", 31, emitted=True))
        sink.append(producer.emitted_move_register_line("x0", "x9"))
        sink.append(producer.emitted_fixed_instruction_line("ret"))
        sink.append_label("L_g_end")
        sink.release_captured_function()
        timer = timing.ModulePhaseTiming("typed.pipeline") if timed else None
        sink.finish_optimized_instructions({"L_trampoline"}, [], phase_timing=timer)
        receipts = [line for line in capsys.readouterr().err.splitlines()
                    if line.startswith("pcc coarse phase ")]
        if timed:
            rows = [dict(field.split("=", 1) for field in line.split()[3:])
                    for line in receipts]
            assert [(row["phase"], row["event"]) for row in rows] == [
                (phase, event) for phase in timing._AARCH64_TYPED_PHASES
                for event in ("begin", "complete")
            ]
            assert all(row["scope"] == "backend_finalize"
                       and row["parent"] == "prepare"
                       and row["module"] == "typed.pipeline"
                       for row in rows)
            assert all("duration_ns" not in row for row in rows[::2])
            assert all(row["clock_ok"] == "1" and int(row["duration_ns"]) >= 0
                       for row in rows[1::2])
            assert timer.totals_ns == [0] * 16
        else:
            assert receipts == []
        assert sink.fragments.closed
        sink.builder.finalize_text_layout()
        assert sink.builder.text_label_offsets() == expected_labels
        assert sink.builder.text_size() == expected_size
        actual = sink.builder.finish()
        text_section = next(section for section in actual[0] if section.sectname == "__text")
        marker_pc = expected_labels["L_pcc_smap_1"]
        assert int.from_bytes(text_section.data[marker_pc - 4:marker_pc], "little") == 0xD63F0140
        assert actual == expected
        assert encode_native_object(NativeObject.from_sections(actual[0], undefined=actual[1])) == encode_native_object(NativeObject.from_sections(expected[0], undefined=expected[1]))
        assert sink.fallback_lines == []
    finally:
        if sink is not None:
            sink.close()
        producer.end_direct_instruction_capture()


def _forbid_text(patch):
    def forbidden(*args, **kwargs):
        raise AssertionError("typed path used instruction text parser or assembler")
    for name in (
        "_encode_one", "assemble_text", "assemble_text_lines",
        "append_emitted_instruction_record", "encode_emitted_move",
        "encode_emitted_unscaled_load_store",
    ):
        patch.setattr(encoder, name, forbidden)
    patch.setattr(reference, "append_emitted_instruction_record", forbidden)


@pytest.mark.parametrize("valid_first", [True, False])
def test_owned_cfg_proof_index_retains_first_edge_and_exact_typed_output(monkeypatch, valid_first):
    fragments = AArch64EmissionFragments(capture_semantics=True)
    try:
        semantics = fragments.semantics
        ctx = TypedPassContext(fragments, semantics)
        events = fragments.new_fragment()
        fragments.append_label(events, "_f")
        fragments.append_label(events, "L_source")
        fragments.append_label(events, "L_pcc_smap_1")
        fragments.append_branch(events, "b.eq", "L_success")
        fragments.append_branch(events, "b", "L_error")
        fragments.append_label(events, "L_success")
        fragments.spans.append(events, ctx.emit("ret", []))
        fragments.append_label(events, "L_error")
        fragments.spans.append(events, ctx.emit("ret", []))
        good = ("L_source", "L_error", "L_success")
        missing = ("L_source", "L_missing_error", "L_success")
        unknown = ("L_missing_source", "L_error", "L_success")
        edges = ([good, missing] if valid_first else [missing, good]) + [unknown]
        ids = [tuple(fragments.symbol_ids.get(name, -1) for name in edge) for edge in edges]
        original = cfg.fold_cond_branch_to_fallthrough
        observed = []

        def signature(span):
            return [(ctx.record(record), ctx.opcode(record), ctx.operands(record))
                    for record in ctx.read_span(span)]

        def check_index(owner, semantic_owner, span, eligible_edges, *, source_edge_index=None):
            assert source_edge_index is not None
            observed.append(source_edge_index)
            first = ids[0]
            assert source_edge_index[first[0]] == (first[1], first[2])
            assert "L_missing_source" not in fragments.symbol_ids
            assert "L_missing_error" not in fragments.symbol_ids
            baseline = original(owner, semantic_owner, span, ids)
            indexed = original(owner, semantic_owner, span, eligible_edges,
                               source_edge_index=source_edge_index)
            assert signature(indexed) == signature(baseline)
            return indexed

        monkeypatch.setattr(cfg, "fold_cond_branch_to_fallthrough", check_index)
        result = cfg.run_aarch64_typed_cfg_passes(fragments, semantics, events, set(), edges)
        assert len(observed) == 1
        opcodes = [ctx.opcode(record) for record in ctx.read_span(result)]
        assert ("b.ne" in opcodes) == valid_first
        assert ("b.eq" in opcodes) != valid_first
    finally:
        fragments.close()


def _reg(name):
    return (sem.TOKEN_REGISTER, sem.semantic_register_id(name), 0, 0)


def _imm(value):
    return (sem.TOKEN_IMMEDIATE, value, 10, sem.IMMEDIATE_HASH)


def _sym(ctx, name, decoration=sem.SYMBOL_PLAIN):
    sid = encoder.intern_emitted_symbol(name, ctx.fragments.symbol_ids, ctx.fragments.symbol_names)
    return (sem.TOKEN_SYMBOL, sid, decoration, 0)


def _slot(offset):
    if offset == 0:
        return [(sem.TOKEN_ADDRESS, sem.ADDRESS_BASE, 1, 0), _reg("sp")]
    return [(sem.TOKEN_ADDRESS, sem.ADDRESS_OFFSET, 2, 0), _reg("sp"), _imm(offset)]


def _event(ctx, span, name=None):
    if name is None:
        event = CompilerInt4(0, EMISSION_RECORD_EMPTY, 0, -1)
    else:
        event = CompilerInt4(_sym(ctx, name)[1], EMISSION_RECORD_LABEL, 0, -1)
    return sem.append_semantic_event(ctx.fragments, ctx.semantics, span, event)


def _render(ctx, rid):
    """Output-only oracle adapter. Never used to construct typed instructions."""
    label = ctx.label(rid)
    if label is not None:
        return ctx.symbol(label) + ":"
    if ctx.is_empty(rid):
        return ""
    tokens = ctx.operands(rid)
    def token_text(token):
        kind, value, extra, flags = token
        if kind == sem.TOKEN_REGISTER:
            return sem.semantic_register_spelling(value)
        if kind == sem.TOKEN_IMMEDIATE:
            assert extra == 10 and flags == sem.IMMEDIATE_HASH
            return "#" + str(value)
        if kind == sem.TOKEN_SYMBOL:
            return ctx.symbol(value) + ("", "@PAGE", "@PAGEOFF", "@GOTPAGE", "@GOTPAGEOFF")[extra]
        if kind == sem.TOKEN_SYSTEM:
            return sem.SYSTEM_NAMES[value]
        if kind == sem.TOKEN_CONDITION:
            return sem.CONDITION_NAMES[value]
        if kind == sem.TOKEN_FLOAT_IMMEDIATE:
            assert flags == sem.IMMEDIATE_HASH
            return "#" + sem.FLOAT_IMMEDIATE_LITERALS[extra]
        raise AssertionError(token)
    args = []
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if token[0] == sem.TOKEN_ADDRESS:
            assert token[1] in (sem.ADDRESS_BASE, sem.ADDRESS_OFFSET)
            args.append("[" + ", ".join(token_text(t) for t in tokens[index + 1:index + 1 + token[2]]) + "]")
            index += token[2] + 1
        else:
            args.append(token_text(token))
            index += 1
    return "  " + ctx.opcode(rid) + (" " + ", ".join(args) if args else "")


@pytest.mark.parametrize("destination", ["x0", "x9", "x16", "x30"])
def test_real_tlv_rewrites_match_text_words_and_all_page_fixups(monkeypatch, destination):
    original = ["_f:"] + text_tlv.tlv_resolver_lines("_tls", destination, 31, emitted=True)
    original += ["  adrp x19, _external@GOTPAGE", "  ldr x19, [x19, _external@GOTPAGEOFF]", "  ret"]
    expected = text_tlv.elide_dead_tlv_resolver_saves(original)
    assert expected != original
    expected_words = []
    expected_relocations = []
    for line in expected:
        if line.startswith("  "):
            relocs = []
            expected_words.append(encoder._encode_one(line.strip(), 4 * len(expected_words), {},
                lambda target, width, at: 0, relocs, set(), lambda target, at: False))
            expected_relocations.extend((r.offset, r.symbol, r.type, r.pcrel) for r in relocs)
    fragments, semantics = AArch64EmissionFragments(), sem.AArch64InstructionSemantics()
    try:
        with monkeypatch.context() as patch:
            _forbid_text(patch)
            ctx = TypedPassContext(fragments, semantics)
            events = fragments.new_fragment()
            records = [_event(ctx, events, "_f")]
            # Independent producer provenance: do not reuse the TLV optimizer's
            # resolver generator to construct its own input fixture.
            operations = [("sub", [_reg("sp"), _reg("sp"), _imm(48)])]
            for reg, offset in (("x0", 0), ("x16", 8), ("x17", 16), ("x30", 24)):
                operations.append(("str", [_reg(reg)] + _slot(offset)))
            operations += [
                ("mrs", [_reg("x17"), (sem.TOKEN_SYSTEM, 0, 0, 0)]),
                ("str", [_reg("x17")] + _slot(32)),
                ("adrp", [_reg("x0"), _sym(ctx, "_tls", sem.SYMBOL_PAGE)]),
                ("add", [_reg("x0"), _reg("x0"), _sym(ctx, "_tls", sem.SYMBOL_PAGEOFF)]),
                ("ldr", [_reg("x16"), (sem.TOKEN_ADDRESS, sem.ADDRESS_BASE, 1, 0), _reg("x0")]),
                ("blr", [_reg("x16")]),
                ("ldr", [_reg("x17")] + _slot(32)),
                ("msr", [(sem.TOKEN_SYSTEM, 0, 0, 0), _reg("x17")]),
            ]
            if destination != "x0":
                operations.append(("mov", [_reg(destination), _reg("x0")]))
            for reg, offset in (("x0", 0), ("x16", 8), ("x17", 16), ("x30", 24)):
                if reg != destination:
                    operations.append(("ldr", [_reg(reg)] + _slot(offset)))
            operations += [
                ("add", [_reg("sp"), _reg("sp"), _imm(48)]),
                ("adrp", [_reg("x19"), _sym(ctx, "_external", sem.SYMBOL_GOTPAGE)]),
                ("ldr", [_reg("x19"), (sem.TOKEN_ADDRESS, sem.ADDRESS_OFFSET, 2, 0),
                         _reg("x19"), _sym(ctx, "_external", sem.SYMBOL_GOTPAGEOFF)]),
                ("ret", []),
            ]
            records.extend(ctx.emit(op, args) for op, args in operations)
            source = ctx.write_span(records)
            assert [_render(ctx, r) for r in records] == original
            output = tlv.elide_dead_tlv_resolver_saves(fragments, semantics, source)
            result = ctx.read_span(output)
        assert ctx.read_span(source) == records
        assert [_render(ctx, r) for r in result] == expected
        instructions = [ctx.record(r) for r in result if ctx.record(r).second >= 0]
        assert [r.first for r in instructions] == expected_words
        reloc_types = {
            encoder.STRUCTURED_RELOCATION_PAGE21: (macho_spec.ARM64_RELOC_PAGE21, True),
            encoder.STRUCTURED_RELOCATION_PAGEOFF12: (macho_spec.ARM64_RELOC_PAGEOFF12, False),
            encoder.STRUCTURED_RELOCATION_GOT_LOAD_PAGE21: (macho_spec.ARM64_RELOC_GOT_LOAD_PAGE21, True),
            encoder.STRUCTURED_RELOCATION_GOT_LOAD_PAGEOFF12: (macho_spec.ARM64_RELOC_GOT_LOAD_PAGEOFF12, False),
        }
        actual_relocations = []
        for index, record in enumerate(instructions):
            if record.third:
                kind, pcrel = reloc_types[record.third]
                actual_relocations.append((index * 4, ctx.symbol(record.fourth), kind, pcrel))
            else:
                assert record.fourth == -1
        assert actual_relocations == expected_relocations
        assert len(actual_relocations) == 4
        semantics.require_complete(fragments.records, len(fragments.symbol_names))
    finally:
        semantics.close()
        fragments.close()


_TIMED_TYPED_PASSES = (
    "forward_adjacent_stack_store_load",
    "forward_one_intervening_stack_store_load",
    "fold_zero_store_source",
    "fold_mov_store_source",
    "fold_zero_compare_immediate",
    "fold_mov_compare_source",
    "fold_mov_zero_branch_source",
    "fold_mov_arith_self_update",
    "fold_scratch_def_into_move",
    "fold_mov_mov_chain",
    "fold_zero_test_branch",
    "fold_forwarded_cset_branch",
    "fold_cset_zero_branch",
    "drop_dead_cset_branch_stores",
    "fold_sign_extending_load",
    "thread_trampoline_branches",
    "fold_cond_branch_to_fallthrough",
    "drop_fallthrough_uncond_branches",
    "drop_unreferenced_empty_local_labels",
    "elide_dead_tlv_resolver_saves",
    "pair_adjacent_aarch64_64bit_memory_ops",
    "drop_loop_separator_nops",
)


@pytest.mark.parametrize("failed_pass", _TIMED_TYPED_PASSES)
def test_typed_pass_failure_retains_original_error_and_unclosed_begin(monkeypatch, capsys, failed_pass):
    from types import SimpleNamespace
    from pcc.backend import indexed_phase_timing as timing
    from pcc.backend import self_backend_aarch64_typed_pair as pair
    from pcc.backend import self_backend_aarch64_typed_peepholes as peepholes

    calls = []
    original = RuntimeError("original optimizer failure")
    span = object()
    fragments = SimpleNamespace(records=[], symbol_names=[], symbol_ids={})
    semantics = SimpleNamespace(require_complete=lambda *_args: None)

    def operation(name):
        def run(_fragments, _semantics, incoming, *_args, **_kwargs):
            assert incoming is span
            calls.append(name)
            if name == failed_pass:
                raise original
            return incoming
        return run

    for index, name in enumerate(_TIMED_TYPED_PASSES):
        # Fifteen peephole-module passes, four CFG passes, the TLV and
        # pairing passes, then the CFG module's loop-separator cleanup.
        owner = (peepholes if index < 15 else cfg if index < 19 else tlv if index == 19
                 else pair if index == 20 else cfg)
        monkeypatch.setattr(owner, name, operation(name))
    timer = timing.ModulePhaseTiming("failed.module")
    with pytest.raises(RuntimeError) as caught:
        result = peepholes.run_aarch64_typed_peepholes(
            fragments, semantics, span, phase_timing=timer,
        )
        cfg.run_aarch64_typed_cfg_passes(
            fragments, semantics, result, set(), [], phase_timing=timer,
        )
    assert caught.value is original
    assert calls == list(_TIMED_TYPED_PASSES[:_TIMED_TYPED_PASSES.index(failed_pass) + 1])
    rows = [dict(field.split("=", 1) for field in line.split()[3:])
            for line in capsys.readouterr().err.splitlines()
            if line.startswith("pcc coarse phase ")]
    phases = ["typed_peephole_validate"]
    for index, name in enumerate(calls):
        if index == 15:
            phases.append("typed_cfg_proofs")
        phases.append("typed_" + name)
    expected = [(phase, event) for phase in phases[:-1] for event in ("begin", "complete")]
    expected.append((phases[-1], "begin"))
    assert [(row["phase"], row["event"]) for row in rows] == expected
    assert "duration_ns" not in rows[-1]
    assert all(row["parent"] == "prepare" for row in rows)
    assert timer.totals_ns == [0] * 16


@pytest.mark.parametrize("failure", ["clock", "write", "flush"])
def test_typed_phase_diagnostic_failure_does_not_escape(monkeypatch, failure):
    from pcc.backend import indexed_phase_timing as timing

    class DiagnosticAbort(BaseException):
        pass

    class BrokenSink:
        def write(self, _line):
            if failure == "write":
                raise DiagnosticAbort("write")

        def flush(self):
            if failure == "flush":
                raise DiagnosticAbort("flush")

    def clock():
        if failure == "clock":
            raise DiagnosticAbort("clock")
        return 100

    monkeypatch.setattr(timing.sys, "stderr", BrokenSink())
    monkeypatch.setattr(timing.time, "perf_counter_ns", clock)
    timer = timing.ModulePhaseTiming("sample")
    original = ValueError("compiler error")
    with pytest.raises(ValueError) as caught:
        try:
            raise original
        finally:
            for phase in timing._AARCH64_TYPED_PHASES:
                timer.complete_backend_finalize(phase, timer.begin_backend_finalize(phase))
    assert caught.value is original
    assert timer.failed == (failure == "clock")
    assert timer.totals_ns == [0] * 16


@pytest.mark.parametrize("distance", [199999, 200000, 200001])
@pytest.mark.parametrize("operation", ["thread", "fold"])
def test_real_span_uses_event_positions_at_two_hundred_thousand(monkeypatch, operation, distance):
    # Repeating one immutable EMPTY record is a real persistent-span operation,
    # not a fake context or a reduced guard. Enumeration still has 200000+
    # events, while semantic/record storage remains constant-sized.
    if operation == "thread":
        prefix = ["_f:", "  b.eq L_trampoline", "  b L_end", "L_trampoline:", "  b L_target"]
        branch_index = 1
        suffix = ["L_target:", "  ret", "L_end:"]
        edges = []
    else:
        prefix = ["_f:", "L_source:", "  b.eq L_success", "  b L_error", "L_success:", "  ret"]
        branch_index = 2
        suffix = ["L_error:", "  ret"]
        edges = [("L_source", "L_error", "L_success")]
    padding = distance + branch_index - len(prefix)
    original = prefix + [""] * padding + suffix
    if operation == "thread":
        expected = reference._thread_trampoline_branches(original)
        expected_branch = "  b.eq " + ("L_target" if distance <= 200000 else "L_trampoline")
    else:
        expected = reference._fold_cond_branch_to_fallthrough(original, edges)
        expected_branch = "  b.ne L_error" if distance <= 200000 else "  b.eq L_success"
    assert expected[branch_index] == expected_branch
    fragments, semantics = AArch64EmissionFragments(), sem.AArch64InstructionSemantics()
    try:
        with monkeypatch.context() as patch:
            _forbid_text(patch)
            ctx = TypedPassContext(fragments, semantics)
            events = fragments.new_fragment()
            blank = _event(ctx, events)
            def mark(name):
                return _event(ctx, events, name)
            def branch(op, name):
                return ctx.emit(op, [_sym(ctx, name)])
            if operation == "thread":
                ids = [mark("_f"), branch("b.eq", "L_trampoline"), branch("b", "L_end"),
                       mark("L_trampoline"), branch("b", "L_target")]
                tail = [mark("L_target"), ctx.emit("ret", []), mark("L_end")]
            else:
                ids = [mark("_f"), mark("L_source"), branch("b.eq", "L_success"), branch("b", "L_error"),
                       mark("L_success"), ctx.emit("ret", [])]
                tail = [mark("L_error"), ctx.emit("ret", [])]
            source = ctx.write_span(ids)
            repeat = ctx.write_span([blank])
            remaining = padding
            while remaining:
                if remaining & 1:
                    fragments.spans.extend(source, repeat)
                remaining >>= 1
                if remaining:
                    # Self-extension snapshots the old immutable root.
                    fragments.spans.extend(repeat, repeat)
            for record_id in tail:
                fragments.spans.append(source, record_id)
            ids += [blank] * padding + tail
            assert len(fragments.spans.nodes) // 3 < 128
            assert len(fragments.records) // 4 < 16
            assert ctx.read_span(source) == ids
            if operation == "thread":
                output = cfg.thread_trampoline_branches(fragments, semantics, source)
            else:
                edge_ids = [tuple(fragments.symbol_ids[n] for n in edge) for edge in edges]
                output = cfg.fold_cond_branch_to_fallthrough(fragments, semantics, source, edge_ids)
            result = ctx.read_span(output)
        assert [_render(ctx, r) for r in result] == expected
        assert ctx.read_span(source) == ids
    finally:
        semantics.close()
        fragments.close()


@pytest.mark.parametrize("opcode", ["csel", "csinv", "uxtw", "fmov"])
def test_full_twenty_passes_preserve_new_adapter_shapes(monkeypatch, opcode):
    from pcc.backend.self_backend_aarch64_typed_peepholes import run_aarch64_typed_peepholes
    from pcc.backend.self_backend_target_passes import pair_adjacent_aarch64_64bit_memory_ops
    if opcode in ("csel", "csinv"):
        original = ["_f:", "  " + opcode + " x9, x1, x2, eq", "  mov x19, x9", "  movz x9, #7", "  ret"]
        operations = [
            (opcode, [_reg("x9"), _reg("x1"), _reg("x2"),
                      (sem.TOKEN_CONDITION, sem.CONDITION_NAMES.index("eq"), 0, 0)]),
            ("mov", [_reg("x19"), _reg("x9")]),
            ("movz", [_reg("x9"), _imm(7)]), ("ret", []),
        ]
    elif opcode == "uxtw":
        original = ["_f:", "  uxtw x9, w1", "  mov x19, x9", "  movz x9, #7", "  ret"]
        operations = [("uxtw", [_reg("x9"), _reg("w1")]),
                      ("mov", [_reg("x19"), _reg("x9")]),
                      ("movz", [_reg("x9"), _imm(7)]), ("ret", [])]
    else:
        # FP destination is deliberately outside integer MOV scratch renaming.
        original = ["_f:", "  fmov d9, #1.0", "  fmov d19, d9", "  ret"]
        operations = [("fmov", [_reg("d9"), (sem.TOKEN_FLOAT_IMMEDIATE, 0x70, 0, sem.IMMEDIATE_HASH)]),
                      ("fmov", [_reg("d19"), _reg("d9")]), ("ret", [])]
    expected = original
    for name in (
        "_forward_adjacent_stack_store_load", "_forward_one_intervening_stack_store_load",
        "_fold_zero_store_source", "_fold_mov_store_source", "_fold_zero_compare_immediate",
        "_fold_mov_compare_source", "_fold_mov_zero_branch_source", "_fold_mov_arith_self_update",
        "_fold_scratch_def_into_move", "_fold_mov_mov_chain", "_fold_zero_test_branch",
        "_fold_forwarded_cset_branch", "_fold_cset_zero_branch", "_drop_dead_cset_branch_stores",
    ):
        expected = getattr(reference, name)(expected)
    expected = reference._thread_trampoline_branches(expected, set())
    expected = reference._fold_cond_branch_to_fallthrough(expected, [])
    expected = reference._drop_fallthrough_uncond_branches(expected)
    expected = reference._drop_unreferenced_empty_local_labels(expected, set())
    expected = text_tlv.elide_dead_tlv_resolver_saves(expected)
    expected = pair_adjacent_aarch64_64bit_memory_ops(expected, enabled=True)
    if opcode in ("csel", "csinv"):
        assert expected[1] == "  " + opcode + " x19, x1, x2, eq"
        assert "  mov x19, x9" not in expected
    if opcode == "fmov":
        assert expected == original
    expected_words = [encoder._encode_one(line.strip(), 0, {}, lambda target, width, at: 0,
        [], set(), lambda target, at: False) for line in expected if line.startswith("  ")]
    fragments, semantics = AArch64EmissionFragments(), sem.AArch64InstructionSemantics()
    try:
        with monkeypatch.context() as patch:
            _forbid_text(patch)
            ctx = TypedPassContext(fragments, semantics)
            events = fragments.new_fragment()
            records = [_event(ctx, events, "_f")]
            records += [ctx.emit(op, args) for op, args in operations]
            source = ctx.write_span(records)
            result = run_aarch64_typed_peepholes(fragments, semantics, source)
            result = cfg.run_aarch64_typed_cfg_passes(fragments, semantics, result, set(), [])
            output = ctx.read_span(result)
        assert ctx.read_span(source) == records
        assert [_render(ctx, r) for r in output] == expected
        assert [ctx.record(r).first for r in output if ctx.record(r).second >= 0] == expected_words
        assert all(ctx.record(r).third == 0 and ctx.record(r).fourth == -1
                   for r in output if ctx.record(r).second >= 0)
    finally:
        semantics.close()
        fragments.close()


def _aggregate_lane_context(family, fail_at=None):
    from types import SimpleNamespace

    calls = []
    error = ValueError("aggregate getter failure")
    ctx = object.__new__(TypedPassContext)

    def record(rid):
        calls.append(("record", rid))
        if fail_at == "record":
            raise error
        return CompilerInt4(0, family, 0, -1)

    def header(rid):
        calls.append(("header", rid))
        if fail_at == "header":
            raise error
        return CompilerInt4(sem.semantic_opcode_id("mov"), sem.FORM_CANONICAL, 3, 1)

    def token(index):
        calls.append(("token", index))
        return CompilerInt4(sem.TOKEN_REGISTER, 9, 0, 0)

    ctx.record = record
    ctx.semantics = SimpleNamespace(
        require_header=header, tokens=SimpleNamespace(get4_unchecked=token),
    )
    return ctx, calls, error


@pytest.mark.parametrize("method,family,expected,after_record", [
    ("opcode", 0, "mov", [("header", 7)]),
    ("opcode", EMISSION_RECORD_EMPTY, "", []),
    ("operands", 0, [(sem.TOKEN_REGISTER, 9, 0, 0)], [("header", 7), ("token", 3)]),
    ("operands", EMISSION_RECORD_EMPTY, [], []),
    ("is_empty", EMISSION_RECORD_EMPTY, True, []),
    ("is_empty", 0, False, []),
])
def test_bound_context_aggregate_preserves_getter_order(method, family, expected, after_record):
    ctx, calls, _ = _aggregate_lane_context(family)
    assert getattr(ctx, method)(7) == expected
    assert calls == [("record", 7), *after_record]


@pytest.mark.parametrize("kind,expected", [
    ("directive", True), ("barrier", True), ("empty", False),
])
def test_bound_context_directive_aggregate_preserves_getter_order(kind, expected):
    from pcc.backend.self_backend_aarch64_fragments import (
        EMISSION_RECORD_DIRECTIVE, EMISSION_RECORD_MEMORY_PAIR_BARRIER,
    )
    family = {"directive": EMISSION_RECORD_DIRECTIVE,
              "barrier": EMISSION_RECORD_MEMORY_PAIR_BARRIER,
              "empty": EMISSION_RECORD_EMPTY}[kind]
    ctx, calls, _ = _aggregate_lane_context(family)
    assert ctx.is_directive(7) is expected
    assert calls == [("record", 7)]


@pytest.mark.parametrize("method,fail_at,expected_calls", [
    ("opcode", "record", [("record", 7)]),
    ("operands", "record", [("record", 7)]),
    ("is_empty", "record", [("record", 7)]),
    ("is_directive", "record", [("record", 7)]),
    ("opcode", "header", [("record", 7), ("header", 7)]),
    ("operands", "header", [("record", 7), ("header", 7)]),
])
def test_bound_context_aggregate_keeps_original_exception(method, fail_at, expected_calls):
    ctx, calls, error = _aggregate_lane_context(0, fail_at)
    with pytest.raises(ValueError) as caught:
        getattr(ctx, method)(7)
    assert caught.value is error
    assert calls == expected_calls


def test_bound_context_opcode_snapshots_names_before_header(monkeypatch):
    from pcc.backend import self_backend_aarch64_typed_pass_context as context_module

    ctx, calls, _ = _aggregate_lane_context(0)
    opcode_id = sem.semantic_opcode_id("mov")
    original_names = list(context_module.OPCODE_NAMES)
    replacement_names = list(original_names)
    original_names[opcode_id] = "original"
    replacement_names[opcode_id] = "replacement"
    monkeypatch.setattr(context_module, "OPCODE_NAMES", original_names)
    header = ctx.semantics.require_header

    def replace_names(rid):
        monkeypatch.setattr(context_module, "OPCODE_NAMES", replacement_names)
        return header(rid)

    ctx.semantics.require_header = replace_names
    assert ctx.opcode(7) == "original"
    assert context_module.OPCODE_NAMES is replacement_names
    assert calls == [("record", 7), ("header", 7)]


def test_bound_context_opcode_missing_names_precedes_header(monkeypatch):
    from pcc.backend import self_backend_aarch64_typed_pass_context as context_module

    ctx, calls, _ = _aggregate_lane_context(0)
    monkeypatch.delattr(context_module, "OPCODE_NAMES")
    with pytest.raises(NameError, match="OPCODE_NAMES"):
        ctx.opcode(7)
    assert calls == [("record", 7)]
