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
from pcc.backend.arm64_encode_producer import encode_producer_indirect_branch
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


# Exact independently specified words for legal semantic-test inputs outside
# assemble_text's proven subset. These are not a new text assembler fallback.
# Arm A64 BR: 0xD61F0000 | (Rn << 5).
# Arm A64 BFI: BFM Xd, Xn, #(-lsb MOD 64), #(width - 1).
# https://support.arm.com/documentation/ddi0602/latest/Base-Instructions/BR--Branch-to-register-
# https://support.arm.com/documentation/ddi0596/2021-03/Base-Instructions/BFI--Bitfield-Insert--an-alias-of-BFM-)
_FIXTURE_WORDS = {
    "  br x19": 0xD61F0260,
    "  bfi x9, x10, #0, #1": 0xB3400149,
}


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
        elif op == "br":
            word = encode_producer_indirect_branch(
                op, sem.semantic_register_spelling(tokens[0].second),
            )
        elif line == "  bfi x9, x10, #0, #1":
            # BFM's sf=1, opc=01, N=1, immr=imms=0, Rn=10, Rd=9.
            word = 0xB3400000 | (10 << 5) | 9
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
            if line in _FIXTURE_WORDS:
                expected_words.append(_FIXTURE_WORDS[line])
            elif line.startswith("  "):
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
    ("fold_forwarded_cset_branch", [I("cset", R("w9"), C("eq")), I("sturb", R("w9"), A()), I("and", R("w9"), R("w9"), N(255,16)), I("cbz", R("w9"), S())]),
    ("fold_cset_zero_branch", [I("cmp", R("w9"), R("w10")), I("cset", R("w9"), C("eq")), I("sturb", R("w9"), A()), I("cbz", R("w9"), S())]),
    ("drop_dead_cset_branch_stores", ["_fn:", I("cmp", R("w9"), R("w10")), I("cset", R("w9"), C("eq")), I("sturb", R("w9"), A()), I("b.ne", S())]),
]


@pytest.mark.parametrize("name,items", CASES, ids=[case[0] for case in CASES])
def test_each_pass_matches_legacy_without_text(monkeypatch, name, items):
    # The stack-store deletion stays disabled until alias-safe deadness exists.
    _differential(monkeypatch, name, items, name != "drop_dead_cset_branch_stores")


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
    if condition not in ("al", "nv"):
        _differential(monkeypatch, "fold_cset_zero_branch", items)
        return

    # AL/NV are in the provenance vocabulary, but are not encodable CSET
    # aliases. Preserve this inverse-domain negative case without pretending
    # that it is an executable instruction. Both existing encoders must reject.
    line = "  cset w9, " + condition
    with pytest.raises(enc.EncodeError, match="bad condition code"):
        enc.assemble_text(line)
    with pytest.raises(enc.EncodeError, match="bad condition code"):
        enc.encode_emitted_cset_parts("w9", condition)

    valid_items = list(items)
    valid_items[1] = I("cset", R("w9"), C("eq"))
    f, s, source, lines = _fixture(valid_items)
    try:
        before = _ids(f, source)
        header = s.require_header(before[1])
        # Deliberate invalid-alias provenance injection. The valid EQ word is
        # merely an opaque preservation sentinel; it is never executed or
        # presented as an encoding of CSET AL/NV.
        s.tokens.set((header.third + 1) * 4 + 1, C(condition).second)
        lines[1] = line
        assert legacy._fold_cset_zero_branch(lines) == lines
        records = [f.records.get4_unchecked(rid) for rid in before]
        tokens = [s.tokens.get4_unchecked(i) for i in range(s.token_count())]

        def forbidden(*args, **kwargs):
            raise AssertionError("invalid-alias negative case tried to encode")

        with monkeypatch.context() as patch:
            for target in ("append_emitted_instruction_record", "encode_emitted_unscaled_load_store", "encode_emitted_move", "assemble_text", "assemble_text_lines", "_encode_one"):
                patch.setattr(enc, target, forbidden)
            patch.setattr(typed, "encode_emitted_branch_base_parts", forbidden)
            result = typed.fold_cset_zero_branch(f, s, source)
        assert _ids(f, source) == before
        assert _ids(f, result) == before
        assert [f.records.get4_unchecked(rid) for rid in before] == records
        assert [s.tokens.get4_unchecked(i) for i in range(s.token_count())] == tokens
        assert s.operand(before[1], 1) == C(condition)
        s.require_complete(f.records, len(f.symbol_names))
        assert f.spans.projection_count == 0
    finally:
        s.close()
        f.close()


@pytest.mark.parametrize("item,line,word", [
    (I("br", R("x19")), "  br x19", 0xD61F0260),
    (I("bfi", R("x9"), R("x10"), N(0), N(1)),
     "  bfi x9, x10, #0, #1", 0xB3400149),
])
def test_semantic_only_fixture_words_are_exact(item, line, word):
    # This test repair does not extend the text assembler's proven subset.
    with pytest.raises(enc.EncodeError, match="not in the proven subset"):
        enc.assemble_text(line)
    f, s, source, lines = _fixture([item])
    try:
        rid, = _ids(f, source)
        assert lines == [line]
        assert f.records.get4_unchecked(rid).first == word
        assert s.require_header(rid).first == sem.semantic_opcode_id(item[0])
        assert [s.operand(rid, i) for i in range(s.require_header(rid).fourth)] == item[1]
        s.require_complete(f.records, len(f.symbol_names))
    finally:
        s.close()
        f.close()


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


def test_operand_snapshot_validates_once_per_call(monkeypatch):
    items = ["_f:", I("stur", R("x9"), A())]
    f, s, source, _ = _fixture(items)
    p = typed._Pass(f, s, source)
    try:
        ids = _ids(f, source)
        expected = [R("x9"), *A()]
        reads = []
        require_header = s.require_header

        def validated(record_id):
            reads.append(record_id)
            return require_header(record_id)

        def repeated_operand_scan(*_args):
            raise AssertionError("operand snapshot repeated whole-span validation")

        monkeypatch.setattr(s, "require_header", validated)
        monkeypatch.setattr(s, "operand", repeated_operand_scan)
        values = p.tokens(ids[1])
        assert values == expected
        assert reads == [ids[1]]
        values.clear()
        assert p.tokens(ids[1]) == expected
        assert reads == [ids[1], ids[1]]
        assert p.tokens(ids[0]) == p.tokens(-1) == []
        assert reads == [ids[1], ids[1]]
    finally:
        p.close()
        s.close()
        f.close()


@pytest.mark.parametrize("corruption", [
    "opcode", "form", "span_start", "span_count", "short_headers",
    "short_tokens", "register", "address_base", "address_span", "closed",
])
def test_operand_snapshot_revalidates_changed_provenance(corruption):
    f, s, source, _ = _fixture([I("stur", R("x9"), A())])
    p = typed._Pass(f, s, source)
    try:
        rid = _ids(f, source)[0]
        assert p.tokens(rid) == [R("x9"), *A()]
        # Mutate after construction and a successful read: no earlier pass or
        # snapshot is authority to skip validation of the current record.
        if corruption == "opcode":
            s.headers.set(rid * 4, 0)
        elif corruption == "form":
            s.headers.set(rid * 4 + 1, 0)
        elif corruption == "span_start":
            s.headers.set(rid * 4 + 2, -1)
        elif corruption == "span_count":
            s.headers.set(rid * 4 + 3, 5)
        elif corruption == "short_headers":
            s.headers.truncate(len(s.headers) - 1)
        elif corruption == "short_tokens":
            s.tokens.truncate(len(s.tokens) - 1)
        elif corruption == "register":
            s.tokens.set(1, 999)
        elif corruption == "address_base":
            s.tokens.set(8, sem.TOKEN_IMMEDIATE)
        elif corruption == "address_span":
            s.headers.set(rid * 4 + 3, 2)
        else:
            s.close()
        with pytest.raises(RuntimeError if corruption == "closed" else enc.EncodeError):
            p.tokens(rid)
    finally:
        p.close()
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


def test_cset_stores_remain_across_functions_and_prefix_is_untouched(monkeypatch):
    shape = [I("cmp", R("w9"), R("w10")), I("cset", R("w9"), C("eq")), I("sturb", R("w9"), A()), I("b.ne", S())]
    _differential(monkeypatch, "drop_dead_cset_branch_stores", shape + ["_one:"] + shape + ["_two:", I("ldurb", R("w10"), A())] + shape, False)


def test_first_fourteen_pipeline_matches_ordered_legacy(monkeypatch):
    names = [name for name, _items in CASES]
    def reference(lines):
        for name in names:
            lines = getattr(legacy, "_" + name)(lines)
        return lines
    monkeypatch.setattr(legacy, "_run_aarch64_typed_peepholes", reference, raising=False)
    items = ["_one:", I("cmp", R("w9"), R("w10")), I("cset", R("w9"), C("eq")), I("sturb", R("w9"), A()), I("ldurb", R("w10"), A()), I("cbz", R("w10"), S()), I("ret"), "L_end:", I("ret")]
    _differential(monkeypatch, "run_aarch64_typed_peepholes", items, True)


def _aggregate_lane_pass(method, mode, fail_at=None):
    from types import SimpleNamespace

    calls = []
    error = ValueError("aggregate getter failure")
    owner = object.__new__(typed._Pass)
    count = 0

    def code(rid):
        nonlocal count
        count += 1
        calls.append(("code", rid))
        if fail_at == "code" or (fail_at == "second_code" and count == 2):
            raise error
        if mode == "event":
            return CompilerInt4(0, EMISSION_RECORD_EMPTY, 0, -1)
        if method == "function_label":
            # Distinct values make collapsing the two old getter calls wrong.
            return CompilerInt4(count - 1, EMISSION_RECORD_LABEL, 0, -1)
        return CompilerInt4(0, 0, 0, -1)

    def header(rid):
        calls.append(("header", rid))
        if fail_at == "header":
            raise error
        return CompilerInt4(sem.semantic_opcode_id("mov"), sem.FORM_CANONICAL, 3, 1)

    def token(index):
        calls.append(("token", index))
        return R("x9")

    owner.code = code
    owner.fragments = SimpleNamespace(symbol_names=["L_not_function", "_function"])
    owner.semantics = SimpleNamespace(
        require_header=header, tokens=SimpleNamespace(get4_unchecked=token),
    )
    return owner, calls, error


@pytest.mark.parametrize("method", ["op", "tokens", "function_label"])
@pytest.mark.parametrize("mode", ["negative", "event", "instruction"])
def test_bound_pass_aggregate_preserves_short_circuit_and_order(method, mode):
    owner, calls, _ = _aggregate_lane_pass(method, mode)
    rid = -1 if mode == "negative" else 7
    result = getattr(owner, method)(rid)
    empty = {"op": "", "tokens": [], "function_label": False}
    if mode == "negative":
        assert result == empty[method]
        assert calls == []
    elif mode == "event":
        assert result == empty[method]
        assert calls == [("code", 7)]
    elif method == "function_label":
        assert result is True
        assert calls == [("code", 7), ("code", 7)]
    else:
        assert result == ("mov" if method == "op" else [R("x9")])
        assert calls == [("code", 7), ("header", 7)] + ([] if method == "op" else [("token", 3)])


@pytest.mark.parametrize("method,fail_at,expected_calls", [
    ("op", "code", [("code", 7)]),
    ("tokens", "code", [("code", 7)]),
    ("function_label", "code", [("code", 7)]),
    ("op", "header", [("code", 7), ("header", 7)]),
    ("tokens", "header", [("code", 7), ("header", 7)]),
    ("function_label", "second_code", [("code", 7), ("code", 7)]),
])
def test_bound_pass_aggregate_keeps_original_exception(method, fail_at, expected_calls):
    owner, calls, error = _aggregate_lane_pass(method, "instruction", fail_at)
    with pytest.raises(ValueError) as caught:
        getattr(owner, method)(7)
    assert caught.value is error
    assert calls == expected_calls


@pytest.mark.parametrize("ending", ["empty", "b", "ret", "add"])
@pytest.mark.parametrize("fail_at", [None, "code", "op", "second_op"])
def test_bound_false_path_preserves_traversal_and_short_circuit(ending, fail_at):
    calls = []
    error = ValueError("false path getter failure")
    owner = object.__new__(typed._Pass)

    class Ids:
        def __len__(self):
            calls.append("len")
            return 1 if ending == "empty" else 2

    def rid(index):
        calls.append(("rid", index))
        return 10 + index

    def code(record_id):
        calls.append(("code", record_id))
        if fail_at == "code":
            raise error
        family = EMISSION_RECORD_EMPTY if record_id == 10 else 0
        return CompilerInt4(0, family, 0, -1)

    op_calls = 0

    def op(record_id):
        nonlocal op_calls
        op_calls += 1
        calls.append(("op", record_id))
        if fail_at == "op" or (fail_at == "second_op" and op_calls == 2):
            raise error
        return ending

    owner.ids, owner.rid, owner.code, owner.op = Ids(), rid, code, op
    expected = ["len", ("rid", 0), ("code", 10)]
    if fail_at == "code":
        with pytest.raises(ValueError) as caught:
            owner.false_path(0)
        assert caught.value is error
    else:
        expected += ["len"]
        if ending == "empty":
            expected += ["len"]
            assert owner.false_path(0) is True
        else:
            expected += [("rid", 1), ("code", 11), "len", ("rid", 1), ("op", 11)]
            if fail_at == "op":
                with pytest.raises(ValueError) as caught:
                    owner.false_path(0)
                assert caught.value is error
            else:
                if ending != "b":
                    expected += [("rid", 1), ("op", 11)]
                if fail_at == "second_op" and ending != "b":
                    with pytest.raises(ValueError) as caught:
                        owner.false_path(0)
                    assert caught.value is error
                else:
                    assert owner.false_path(0) is (ending in ("b", "ret"))
    assert calls == expected


def test_bound_pass_opcode_snapshots_names_before_header(monkeypatch):
    from types import SimpleNamespace

    owner, calls, _ = _aggregate_lane_pass("op", "instruction")
    opcode_id = sem.semantic_opcode_id("mov")
    original_names = list(sem.OPCODE_NAMES)
    replacement_names = list(original_names)
    original_names[opcode_id] = "original"
    replacement_names[opcode_id] = "replacement"
    namespace = SimpleNamespace(OPCODE_NAMES=original_names)
    monkeypatch.setattr(typed, "sem", namespace)
    header = owner.semantics.require_header

    def replace_names(rid):
        namespace.OPCODE_NAMES = replacement_names
        return header(rid)

    owner.semantics.require_header = replace_names
    assert owner.op(7) == "original"
    assert namespace.OPCODE_NAMES is replacement_names
    assert calls == [("code", 7), ("header", 7)]


def test_bound_pass_opcode_names_failure_precedes_header(monkeypatch):
    owner, calls, error = _aggregate_lane_pass("op", "instruction")

    class BrokenNames:
        @property
        def OPCODE_NAMES(self):
            calls.append("names")
            raise error

    monkeypatch.setattr(typed, "sem", BrokenNames())
    with pytest.raises(ValueError) as caught:
        owner.op(7)
    assert caught.value is error
    assert calls == [("code", 7), "names"]


def test_bound_function_label_snapshots_names_before_second_code():
    owner, calls, _ = _aggregate_lane_pass("function_label", "instruction")
    original_names = ["L_first", "_original"]
    replacement_names = ["L_first", "L_not_function"]
    owner.fragments.symbol_names = original_names
    code = owner.code

    def replace_names(rid):
        result = code(rid)
        if calls.count(("code", rid)) == 2:
            owner.fragments.symbol_names = replacement_names
        return result

    owner.code = replace_names
    assert owner.function_label(7) is True
    assert owner.fragments.symbol_names is replacement_names
    assert calls == [("code", 7), ("code", 7)]


def test_bound_function_label_names_failure_precedes_second_code():
    owner, calls, error = _aggregate_lane_pass("function_label", "instruction")

    class BrokenNames:
        @property
        def symbol_names(self):
            calls.append("names")
            raise error

    owner.fragments = BrokenNames()
    with pytest.raises(ValueError) as caught:
        owner.function_label(7)
    assert caught.value is error
    assert calls == [("code", 7), "names"]
