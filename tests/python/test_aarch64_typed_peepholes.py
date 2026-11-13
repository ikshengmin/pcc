"""CI-only differential admission for typed AArch64 peepholes.

The legacy text owner is an oracle only. Fixtures supply typed provenance
independently; production passes run with instruction-text encoders forbidden.
"""
import pytest

from pcc.backend import arm64_encode as enc
from pcc.backend import self_backend_aarch64_darwin as legacy
from pcc.backend import self_backend_aarch64_semantics as sem
from pcc.backend import self_backend_aarch64_typed_peepholes as typed
from pcc.backend.self_backend_aarch64_fragments import (
    AArch64EmissionFragments, EMISSION_RECORD_LABEL, EMISSION_RECORD_EMPTY,
    EMISSION_RECORD_DIRECTIVE, EMISSION_RECORD_MEMORY_PAIR_BARRIER,
)
from pcc.backend.self_backend_value_arena import CompilerInt4


def R(name):
    return CompilerInt4(sem.TOKEN_REGISTER, sem.semantic_register_id(name), 0, 0)


def N(value, radix=10, flags=sem.IMMEDIATE_HASH):
    return sem.semantic_immediate_token(value, radix, flags)


def C(name):
    return CompilerInt4(sem.TOKEN_CONDITION, sem.CONDITION_NAMES.index(name), 0, 0)


def S(name="L_end"):
    return ("symbol", name)


def A(offset=-8, radix=10, flags=sem.IMMEDIATE_HASH):
    return (CompilerInt4(sem.TOKEN_ADDRESS, sem.ADDRESS_OFFSET, 2, 0), R("x29"), N(offset, radix, flags))


def I(op, *tokens):
    flat = []
    for token in tokens:
        if isinstance(token, tuple) and token and not isinstance(token[0], str):
            flat.extend(token)
        else:
            flat.append(token)
    return op, flat


def _token_text(token, names):
    if token.first == sem.TOKEN_REGISTER:
        return sem.semantic_register_spelling(token.second)
    if token.first in (sem.TOKEN_IMMEDIATE, sem.TOKEN_WIDE_IMMEDIATE):
        value = sem.semantic_immediate_value(token)
        radix = sem.semantic_immediate_radix(token)
        flags = sem.semantic_immediate_flags(token)
        digits = str(abs(value)) if radix == 10 else hex(abs(value))
        if flags & sem.IMMEDIATE_HEX_UPPER:
            digits = digits.upper().replace("0X", "0x")
        sign = "-" if value < 0 or flags & sem.IMMEDIATE_NEGATIVE_ZERO else ("+" if flags & sem.IMMEDIATE_EXPLICIT_PLUS else "")
        return ("#" if flags & sem.IMMEDIATE_HASH else "") + sign + digits
    if token.first == sem.TOKEN_SYMBOL:
        return names[token.second]
    if token.first == sem.TOKEN_CONDITION:
        return sem.CONDITION_NAMES[token.second]
    if token.first == sem.TOKEN_SHIFT:
        return sem.SHIFT_NAMES[token.second] + (" #" + str(token.third) if token.fourth else "")
    raise AssertionError(token)


def _line(op, tokens, names):
    parts = []
    i = 0
    while i < len(tokens):
        token = tokens[i]
        if token.first == sem.TOKEN_ADDRESS:
            children = tokens[i + 1:i + 1 + token.third]
            parts.append("[" + ", ".join(_token_text(t, names) for t in children) + "]")
            i += token.third + 1
        else:
            parts.append(_token_text(token, names))
            i += 1
    return "  " + op + (" " + ", ".join(parts) if parts else "")


def _ids(owner, span):
    owner.start_cursor(span)
    result = []
    rid = owner.next_record_id()
    while rid >= 0:
        result.append(rid)
        rid = owner.next_record_id()
    return result


def _fixture(items):
    f, s = AArch64EmissionFragments(), sem.AArch64InstructionSemantics()
    span = f.new_fragment()
    lines = []
    for item in items:
        if isinstance(item, str):
            if item.endswith(":"):
                f.append_label(span, item[:-1])
            elif item == "":
                f._append_record(span, CompilerInt4(0, EMISSION_RECORD_EMPTY, 0, -1))
            else:
                f._append_record(span, CompilerInt4(0, EMISSION_RECORD_DIRECTIVE, 0, -1))
            s.append_unproven()
            lines.append(item)
            continue
        op, raw = item
        tokens = []
        for token in raw:
            if isinstance(token, tuple):
                sid = enc.intern_emitted_symbol(token[1], f.symbol_ids, f.symbol_names)
                token = CompilerInt4(sem.TOKEN_SYMBOL, sid, sem.SYMBOL_PLAIN, 0)
            tokens.append(token)
        line = _line(op, tokens, f.symbol_names)
        lines.append(line)
        symbol = -1
        fixup = 0
        if op in ("b", "cbz", "cbnz") or op.startswith("b."):
            symbol = tokens[-1].second
            fixup = enc.STRUCTURED_FIXUP_BRANCH26 if op == "b" else enc.STRUCTURED_FIXUP_BRANCH19
            word = enc.encode_emitted_branch_base_parts(op, "" if op == "b" or op.startswith("b.") else sem.semantic_register_spelling(tokens[0].second))
        elif op == "bl":
            word, fixup, symbol = 0x94000000, enc.STRUCTURED_FIXUP_CALL, tokens[0].second
        else:
            result = enc.assemble_text(line)
            assert len(result.code) == 4
            word = int.from_bytes(result.code, "little")
        family = enc.EMITTED_INSTRUCTION_MOVE if op == "mov" else enc.EMITTED_INSTRUCTION_SCALAR
        sem.append_semantic_instruction(f, s, span, CompilerInt4(word, family, fixup, symbol), sem.semantic_opcode_id(op), sem.FORM_CANONICAL, tokens)
    return f, s, span, lines


def _differential(monkeypatch, name, items, changed=None):
    f, s, source, lines = _fixture(items)
    try:
        before = _ids(f, source)
        expected = getattr(legacy, "_" + name)(lines)
        # Complete the text oracle before forbidding every text entrypoint.
        expected_words = []
        for line in expected:
            if line.startswith("  "):
                expected_words.append(enc._encode_one(
                    line.strip(), 0, {}, lambda target, width, at: 0,
                    [], set(), lambda target, at: False,
                ))
        def forbidden(*args, **kwargs):
            raise AssertionError("typed peephole used instruction text")
        with monkeypatch.context() as patch:
            for target in ("append_emitted_instruction_record", "encode_emitted_unscaled_load_store", "encode_emitted_move", "assemble_text", "assemble_text_lines", "_encode_one"):
                patch.setattr(enc, target, forbidden)
            result = getattr(typed, name)(f, s, source)
        assert _ids(f, source) == before
        result_ids = _ids(f, result)
        output = []
        words = []
        original_events = {rid: lines[i] for i, rid in enumerate(before) if f.records.get4_unchecked(rid).second < 0}
        for rid in result_ids:
            code = f.records.get4_unchecked(rid)
            if code.second < 0:
                assert rid in before
                output.append(original_events[rid])
            else:
                header = s.require_header(rid)
                tokens = [s.operand(rid, i) for i in range(header.fourth)]
                output.append(_line(sem.OPCODE_NAMES[header.first], tokens, f.symbol_names))
                words.append(code.first)
        assert output == expected
        assert words == expected_words
        if changed is not None:
            assert (output != lines) is changed
        s.require_complete(f.records, len(f.symbol_names))
        assert f.spans.projection_count == 0
    finally:
        s.close()
        f.close()


CASES = [
    ("forward_adjacent_stack_store_load", [I("stur", R("x9"), A()), I("ldur", R("x10"), A())]),
    ("forward_one_intervening_stack_store_load", [I("stur", R("x9"), A()), I("ldur", R("x11"), A(-16)), I("ldur", R("x10"), A())]),
    ("fold_zero_store_source", [I("movz", R("w9"), N(0)), I("sturb", R("w9"), A())]),
    ("fold_mov_store_source", [I("mov", R("w9"), R("w19")), I("sturb", R("w9"), A())]),
    ("fold_zero_compare_immediate", [I("movz", R("w10"), N(0)), I("cmp", R("w9"), R("w10"))]),
    ("fold_mov_compare_source", [I("mov", R("w9"), R("w19")), I("cmp", R("w9"), R("w9"))]),
    ("fold_mov_zero_branch_source", [I("mov", R("w9"), R("w19")), I("cbz", R("w9"), S()), "", I("ret")]),
    ("fold_mov_arith_self_update", [I("mov", R("x9"), R("x19")), I("add", R("x9"), R("x9"), N(1))]),
    ("fold_scratch_def_into_move", [I("movz", R("x9"), N(7)), I("mov", R("x10"), R("x9")), I("mov", R("x19"), R("x10")), I("ret")]),
    ("fold_mov_mov_chain", [I("mov", R("x9"), R("x19")), I("mov", R("x20"), R("x9")), I("ret")]),
    ("fold_zero_test_branch", [I("movz", R("w9"), N(0)), I("cbz", R("w9"), S())]),
    ("fold_forwarded_cset_branch", [I("cset", R("w9"), C("eq")), I("sturb", R("w9"), A()), I("and", R("w10"), R("w9"), N(255,16)), I("cbz", R("w10"), S())]),
    ("fold_cset_zero_branch", [I("cmp", R("w9"), R("w10")), I("cset", R("w9"), C("eq")), I("sturb", R("w9"), A()), I("cbz", R("w9"), S())]),
    ("drop_dead_cset_branch_stores", ["_fn:", I("cmp", R("w9"), R("w10")), I("cset", R("w9"), C("eq")), I("sturb", R("w9"), A()), I("b.ne", S())]),
]


@pytest.mark.parametrize("name,items", CASES, ids=[case[0] for case in CASES])
def test_each_pass_matches_legacy_without_text(monkeypatch, name, items):
    _differential(monkeypatch, name, items, True)


@pytest.mark.parametrize("boundary", ["", "L_stop:", ".p2align 2", ".pcc_memory_pair_barrier_begin"])
@pytest.mark.parametrize("name,items", CASES[:8] + CASES[9:], ids=[case[0] for case in CASES[:8] + CASES[9:]])
def test_intervening_events_preserve_exact_adjacency(monkeypatch, boundary, name, items):
    insertion = 2 if items[0] == "_fn:" else 1
    _differential(monkeypatch, name, items[:insertion] + [boundary] + items[insertion:], False)


@pytest.mark.parametrize("tail", [
    I("stlr", R("w9"), (CompilerInt4(sem.TOKEN_ADDRESS, sem.ADDRESS_BASE, 1, 0), R("x19"))),
    I("movk", R("x9"), N(1)), I("bfi", R("x9"), R("x10"), N(0), N(1)),
    I("csel", R("w11"), R("w9"), R("w10"), C("eq")),
    I("blr", R("x19")), I("br", R("x19")), "L_boundary:", ".p2align 2",
])
def test_store_liveness_uses_before_def_and_stops_at_boundaries(monkeypatch, tail):
    _differential(monkeypatch, "fold_mov_store_source", [I("mov", R("w9"), R("w19")), I("sturb", R("w9"), A()), tail], False)


@pytest.mark.parametrize("immediate", [N(0,16), N(0,10,sem.IMMEDIATE_HASH | sem.IMMEDIATE_NEGATIVE_ZERO), N(0,10,sem.IMMEDIATE_HASH | sem.IMMEDIATE_EXPLICIT_PLUS)])
def test_zero_spelling_is_not_inferred_from_word(monkeypatch, immediate):
    _differential(monkeypatch, "fold_zero_test_branch", [I("movz", R("w9"), immediate), I("cbz", R("w9"), S())], False)


@pytest.mark.parametrize("condition", ["cs", "cc", "hs", "lo", "al", "nv"])
def test_condition_aliases_preserve_original_inverse_domain(monkeypatch, condition):
    items = [I("cmp", R("w9"), R("w10")), I("cset", R("w9"), C(condition)), I("sturb", R("w9"), A()), I("cbz", R("w9"), S())]
    _differential(monkeypatch, "fold_cset_zero_branch", items)


def test_missing_provenance_fails_closed():
    f, s = AArch64EmissionFragments(), sem.AArch64InstructionSemantics()
    try:
        source = f.new_fragment()
        f.append_nop(source)
        with pytest.raises(enc.EncodeError, match="provenance"):
            typed.fold_zero_test_branch(f, s, source)
    finally:
        s.close()
        f.close()


@pytest.mark.parametrize("register", ["w9", "x9", "s9", "d9"])
def test_same_register_forwarding_deletes_only_nonbyte_load(monkeypatch, register):
    _differential(monkeypatch, "forward_adjacent_stack_store_load", [I("stur", R(register), A()), I("ldur", R(register), A())], True)


def test_same_register_byte_forwarding_keeps_mask(monkeypatch):
    _differential(monkeypatch, "forward_adjacent_stack_store_load", [I("sturb", R("w9"), A()), I("ldurb", R("w9"), A())], True)


def test_stack_offsets_compare_spelling_not_integer(monkeypatch):
    _differential(monkeypatch, "forward_adjacent_stack_store_load", [I("stur", R("x9"), A()), I("ldur", R("x10"), A(-8, 16))], False)


@pytest.mark.parametrize("middle", ["w9", "x9"])
def test_middle_load_alias_prevents_forwarding(monkeypatch, middle):
    _differential(monkeypatch, "forward_one_intervening_stack_store_load", [I("stur", R("x9"), A()), I("ldur", R(middle), A(-16)), I("ldur", R("x10"), A())], False)


def test_explicit_lsl_zero_matches_movz_but_nonzero_does_not(monkeypatch):
    for amount, changed in ((0, True), (16, False)):
        shift = CompilerInt4(sem.TOKEN_SHIFT, 0, amount, 1)
        _differential(monkeypatch, "fold_zero_test_branch", [I("movz", R("w9"), N(0), shift), I("cbz", R("w9"), S())], changed)


def test_arithmetic_wsp_selects_extended_register_encoding(monkeypatch):
    _differential(monkeypatch, "fold_mov_arith_self_update", [I("mov", R("w9"), R("wsp")), I("add", R("w9"), R("w9"), R("w10"))], True)


@pytest.mark.parametrize("tail,changed", [(I("bl", S("_callee")), True), (I("ret"), True), (I("blr", R("x19")), False), (I("br", R("x19")), False)])
def test_scratch_scan_distinguishes_direct_and_indirect_calls(monkeypatch, tail, changed):
    _differential(monkeypatch, "fold_scratch_def_into_move", [I("movz", R("x9"), N(7)), I("mov", R("x19"), R("x9")), tail], changed)


@pytest.mark.parametrize("opcode,first,second,changed", [
    ("ldadd", "w10", "w9", True), ("ldadd", "w9", "w10", False),
    ("swp", "w10", "w9", True), ("cas", "w9", "w10", False),
])
def test_lse_roles_preserve_use_before_def(monkeypatch, opcode, first, second, changed):
    address = (CompilerInt4(sem.TOKEN_ADDRESS, sem.ADDRESS_BASE, 1, 0), R("x19"))
    _differential(monkeypatch, "fold_mov_store_source", [I("mov", R("w9"), R("w19")), I("sturb", R("w9"), A()), I(opcode, R(first), R(second), address)], changed)


def test_dead_cset_loads_are_function_scoped_and_prefix_is_untouched(monkeypatch):
    shape = [I("cmp", R("w9"), R("w10")), I("cset", R("w9"), C("eq")), I("sturb", R("w9"), A()), I("b.ne", S())]
    _differential(monkeypatch, "drop_dead_cset_branch_stores", shape + ["_one:"] + shape + ["_two:", I("ldurb", R("w10"), A())] + shape, True)


def test_first_fourteen_pipeline_matches_ordered_legacy(monkeypatch):
    names = [name for name, _items in CASES]
    def reference(lines):
        for name in names:
            lines = getattr(legacy, "_" + name)(lines)
        return lines
    monkeypatch.setattr(legacy, "_run_aarch64_typed_peepholes", reference, raising=False)
    items = ["_one:", I("cmp", R("w9"), R("w10")), I("cset", R("w9"), C("eq")), I("sturb", R("w9"), A()), I("ldurb", R("w10"), A()), I("cbz", R("w10"), S()), I("ret"), "L_end:", I("ret")]
    _differential(monkeypatch, "run_aarch64_typed_peepholes", items, True)
