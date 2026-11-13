"""Typed TLV exact-match, event-boundary and conservative fixed-point cases."""
import pytest

from pcc.backend import self_backend_aarch64_typed_tlv as typed
from pcc.backend import self_backend_aarch64_darwin_tlv as reference
from pcc.backend.self_backend_aarch64_semantics import (
    TOKEN_REGISTER, TOKEN_SYMBOL, TOKEN_IMMEDIATE, TOKEN_ADDRESS, TOKEN_SYSTEM,
    ADDRESS_BASE, SYMBOL_PLAIN, REGISTER_LR, semantic_register_spelling,
)


class Events:
    def __init__(self):
        self.events = []
        self.symbols = ["_function", "_descriptor", "L_loop", "_tail", "L_missing", "x16.part"]

    def emit(self, opcode, operands):
        return self.add(opcode, operands)

    def add(self, opcode="", operands=(), label=None, directive=0, barrier=False):
        index = len(self.events)
        self.events.append((opcode, list(operands), label, directive, barrier))
        return index

    def opcode(self, record):
        return self.events[record][0]

    def operands(self, record):
        return list(self.events[record][1])

    def label(self, record):
        return self.events[record][2]

    def symbol(self, symbol):
        return self.symbols[symbol]

    def is_directive(self, record):
        return bool(self.events[record][3] or self.events[record][4])

    def directive_kind(self, record):
        return self.events[record][3]

    def resolver(self, reg=9):
        return [self.emit(op, args) for op, args in typed.tlv_resolver_instructions(1, reg, typed._ALL, emitted=True)]


def _render(ctx, opcode, tokens):
    # Test-only oracle adapter. The production owner never renders assembly.
    args = []
    index = 0
    while index < len(tokens):
        kind, value, second, third = tokens[index]
        if kind == TOKEN_REGISTER:
            args.append(semantic_register_spelling(value))
        elif kind == TOKEN_IMMEDIATE:
            args.append("#" + str(value))
        elif kind == TOKEN_SYMBOL:
            args.append(ctx.symbol(value) + ("", "@PAGE", "@PAGEOFF")[second])
        elif kind == TOKEN_SYSTEM:
            args.append("nzcv")
        elif kind == TOKEN_ADDRESS:
            body = semantic_register_spelling(tokens[index + 1][1])
            if value != ADDRESS_BASE:
                body += ", #" + str(tokens[index + 2][1])
            args.append("[" + body + "]")
            index += second
        else:
            raise AssertionError(kind)
        index += 1
    return "  " + opcode + (" " + ", ".join(args) if args else "")


@pytest.mark.parametrize("reg", [0, 9, 16, 17, 30, REGISTER_LR])
@pytest.mark.parametrize("preserve", range(32))
@pytest.mark.parametrize("emitted", [False, True])
def test_resolver_frame_and_exact_spelling_match_reference(reg, preserve, emitted):
    ctx = Events()
    actual = [_render(ctx, op, args) for op, args in typed.tlv_resolver_instructions(1, reg, preserve, emitted=emitted)]
    assert actual == reference.tlv_resolver_lines("_descriptor", semantic_register_spelling(reg), preserve, emitted=emitted)


@pytest.mark.parametrize("destination", [0, 9, 16, 17, 30])
@pytest.mark.parametrize("ending", ["ret", "unknown", "eof", "indirect", "local", "tail", "loop", "call", "flags", "symbol"])
def test_complete_sequence_matches_reference(destination, ending):
    ctx = Events()
    records = [ctx.add(label=0)] + ctx.resolver(destination)
    text = ["_function:"] + reference.tlv_resolver_lines("_descriptor", "x" + str(destination), 31, emitted=True)
    endings = {
        "ret": [("ret", [])], "unknown": [("brk", [typed._imm(0)])], "eof": [],
        "indirect": [("br", [typed._reg(9)])],
        "local": [("b", [(TOKEN_SYMBOL, 4, SYMBOL_PLAIN, 0)])],
        "tail": [("b", [(TOKEN_SYMBOL, 3, SYMBOL_PLAIN, 0)])],
        "loop": [("cbnz", [typed._reg(16), (TOKEN_SYMBOL, 2, SYMBOL_PLAIN, 0)])],
        "call": [("bl", [(TOKEN_SYMBOL, 3, SYMBOL_PLAIN, 0)]), ("ret", [])],
        "flags": [("b.eq", [(TOKEN_SYMBOL, 3, SYMBOL_PLAIN, 0)])],
        "symbol": [("adrp", [typed._reg(9), (TOKEN_SYMBOL, 5, 1, 0)]), ("ret", [])],
    }
    if ending == "loop":
        records.insert(1, ctx.add(label=2))
        text.insert(1, "L_loop:")
    for op, args in endings[ending]:
        records.append(ctx.emit(op, args))
        text.append(_render(ctx, op, args))
    out = typed.elide_dead_tlv_resolver_saves_records(ctx, records)
    rendered = [ctx.symbol(ctx.label(record)) + ":" if ctx.label(record) is not None else _render(ctx, ctx.opcode(record), ctx.operands(record)) for record in out]
    assert rendered == reference.elide_dead_tlv_resolver_saves(text)


@pytest.mark.parametrize("position", range(1, 19))
def test_intervening_event_prevents_sequence_recognition(position):
    ctx = Events()
    original = ctx.resolver()
    records = [ctx.add(label=0)] + original[:position] + [ctx.add()] + original[position:] + [ctx.emit("ret", [])]
    assert typed.elide_dead_tlv_resolver_saves_records(ctx, records) == records


@pytest.mark.parametrize("mutation", ["hex", "width", "alias", "offset_zero", "removed_save"])
def test_previous_peephole_changes_are_not_authorized_by_resolver_origin(mutation):
    ctx = Events()
    records = ctx.resolver()
    if mutation == "hex":
        records[0] = ctx.emit("sub", [typed._reg(128), typed._reg(128), (TOKEN_IMMEDIATE, 48, 16, 1)])
    elif mutation == "width":
        records[1] = ctx.emit("str", [typed._reg(32)] + typed._slot(0))
    elif mutation == "alias":
        records[13] = ctx.emit("orr", [typed._reg(9), typed._reg(130), typed._reg(0)])
    elif mutation == "offset_zero":
        records[1] = ctx.emit("str", [typed._reg(0), (TOKEN_ADDRESS, 1, 2, 0), typed._reg(128), typed._imm(0)])
    else:
        del records[1]
    assert typed._match_resolver_call(ctx, records, 0, len(records)) is None


@pytest.mark.parametrize("kind", range(1, 12))
def test_directive_function_boundaries_preserve_event_identity(kind):
    ctx = Events()
    prefix = [ctx.add(label=0), ctx.add(directive=kind)]
    records = prefix + ctx.resolver() + [ctx.emit("ret", [])]
    out = typed.elide_dead_tlv_resolver_saves_records(ctx, records)
    assert out[:2] == prefix
    if kind in (5, 9, 10, 11):
        assert out == records
    else:
        assert len(out) < len(records)


def test_symbol_token_reads_and_lr_spelling_are_not_architectural_aliases():
    ctx = Events()
    assert typed._mask(ctx, [(TOKEN_SYMBOL, 5, SYMBOL_PLAIN, 0)]) == typed._X16
    assert typed._mask(ctx, [typed._reg(REGISTER_LR)]) == typed._X30
    assert typed._mask(ctx, [typed._reg(132)]) == 0
