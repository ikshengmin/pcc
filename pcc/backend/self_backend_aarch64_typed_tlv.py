"""Exact provenance-driven counterpart of Darwin TLV save elimination.

The whole module event sequence is retained. Resolver recognition observes the
current post-peephole sequence, never a producer annotation or decoded word.
"""
from __future__ import annotations

from .arm64_encode import lse_atomic_kind
from .self_backend_aarch64_semantics import (
    TOKEN_REGISTER, TOKEN_IMMEDIATE, TOKEN_SYMBOL, TOKEN_ADDRESS, TOKEN_SYSTEM,
    ADDRESS_BASE, ADDRESS_OFFSET, REGISTER_SP, REGISTER_LR,
    SYMBOL_PLAIN, SYMBOL_PAGE, SYMBOL_PAGEOFF, semantic_symbol_uses_register,
)
from .self_backend_aarch64_typed_pass_context import TypedPassContext

_X0 = 1
_X16 = 2
_X17 = 4
_X30 = 8
_NZCV = 16
_ALL = _X0 | _X16 | _X17 | _X30 | _NZCV
_EXIT = _X0 | _X30

# The emitter's save order and frame: four registers, then NZCV through x17.
_SAVE_ORDER = ((0, _X0), (16, _X16), (17, _X17), (30, _X30))
_EMITTED_FRAME = 48
_EMITTED_NZCV_SLOT = 32

# Directives that may appear between a function's instructions and change no
# register. Any other directive (a section switch, data) ends its code.
_CODE_DIRECTIVES = (
    ".p2align", ".align", ".globl", ".private_extern", ".alt_entry",
    ".cfi_", ".loc ", ".pcc_memory_pair_barrier_",
)

_NO_REGISTERS = frozenset({"nop", "bti", "dmb", "dsb", "isb", "hint", "yield", "b"})
_LR_SIGNING = frozenset({
    "paciasp", "pacibsp", "autiasp", "autibsp", "xpaclri",
    "paciaz", "pacibz", "autiaz", "autibz",
})
_RETURNS = frozenset({"ret", "retaa", "retab"})
_REGISTER_BRANCHES = frozenset({"cbz", "cbnz", "tbz", "tbnz"})
_FLAG_READERS = frozenset({
    "csel", "csinc", "csinv", "csneg", "cset", "csetm", "cinc", "cinv", "cneg",
    "fcsel", "ccmp", "ccmn", "fccmp", "fccmpe", "adc", "adcs", "sbc", "sbcs",
    "ngc", "ngcs",
})
_FLAG_WRITERS = frozenset({
    "cmp", "cmn", "tst", "fcmp", "fcmpe", "ccmp", "ccmn", "fccmp", "fccmpe",
    "adds", "subs", "ands", "bics", "negs", "adcs", "sbcs", "ngcs",
})
_COMPARES = frozenset({"cmp", "cmn", "tst", "fcmp", "fcmpe", "ccmp", "ccmn", "fccmp", "fccmpe"})
_STORES = frozenset({
    "str", "stur", "strb", "sturb", "strh", "sturh", "stlr", "stlrb", "stlrh",
    "stlur", "stlurb", "stlurh", "stp", "stnp",
})
_PAIR_LOADS = frozenset({"ldp", "ldnp", "ldpsw", "ldxp", "ldaxp"})
# The first operand is read as well as written.
_PARTIAL_WRITES = frozenset({"movk", "bfi", "bfxil", "bfm"})
# The first operand is the only register written and is not read.
_DESTINATION_FIRST = frozenset({
    "add", "adds", "sub", "subs", "adr", "adrp", "orr", "orn", "and", "ands",
    "eor", "eon", "bic", "bics", "mul", "mneg", "madd", "msub", "smulh",
    "umulh", "smull", "umull", "smaddl", "umaddl", "smsubl", "umsubl", "sdiv",
    "udiv", "lsl", "lsr", "asr", "ror", "lslv", "lsrv", "asrv", "rorv", "neg",
    "negs", "ngc", "ngcs", "adc", "adcs", "sbc", "sbcs", "mvn", "sxtw", "sxth",
    "sxtb", "uxtb", "uxth", "ubfx", "sbfx", "ubfiz", "sbfiz", "ubfm", "sbfm",
    "extr", "csel", "csinc", "csinv", "csneg", "cset", "csetm", "cinc", "cinv",
    "cneg", "fcsel", "ldr", "ldur", "ldrb", "ldurb", "ldrh", "ldurh", "ldrsw",
    "ldursw", "ldrsb", "ldursb", "ldrsh", "ldursh", "ldar", "ldarb", "ldarh",
    "ldapr", "ldaprb", "ldaprh", "ldxr", "ldaxr", "ldxrb", "ldaxrb", "ldxrh",
    "ldaxrh", "stxr", "stlxr", "stxrb", "stlxrb", "stxrh", "stlxrh", "mov",
    "movz", "movn", "fmov", "fcvtzs", "fcvtzu", "fcvtas", "fcvtau", "fcvtms",
    "fcvtmu", "fcvtns", "fcvtnu", "fcvtps", "fcvtpu", "scvtf", "ucvtf", "fadd",
    "fsub", "fmul", "fnmul", "fdiv", "fneg", "fabs", "fsqrt", "fcvt", "fmadd",
    "fmsub", "fnmadd", "fnmsub", "fmax", "fmin", "fmaxnm", "fminnm", "frintm",
    "frintp", "frintz", "frinta", "frintn", "frintx", "frinti", "clz", "cls",
    "rbit", "rev", "rev16", "rev32",
})


_REGISTER_BITS = {0: _X0, 32: _X0, 16: _X16, 48: _X16,
                  17: _X17, 49: _X17, 30: _X30, 62: _X30, REGISTER_LR: _X30}


def _reg(register):
    return (TOKEN_REGISTER, register, 0, 0)


def _imm(value):
    return (TOKEN_IMMEDIATE, value, 10, 1)


def _slot(offset):
    if offset == 0:
        return [(TOKEN_ADDRESS, ADDRESS_BASE, 1, 0), _reg(REGISTER_SP)]
    return [(TOKEN_ADDRESS, ADDRESS_OFFSET, 2, 0), _reg(REGISTER_SP), _imm(offset)]


def tlv_resolver_instructions(symbol, reg, preserve, *, emitted=False):
    """Selected opcode/operand pairs; no text encoder or assembly rendering."""
    keep_flags = emitted or bool(preserve & _NZCV)
    if emitted:
        stored = [name for name, _bit in _SAVE_ORDER]
        restored = [name for name in stored if name != reg]
        frame = _EMITTED_FRAME
        nzcv_slot = _EMITTED_NZCV_SLOT
    else:
        stored = [name for name, bit in _SAVE_ORDER if preserve & bit and name != reg]
        restored = stored
        nzcv_slot = 8 * len(stored)
        frame = (nzcv_slot + (8 if keep_flags else 0) + 15) // 16 * 16
    slots = {name: 8 * index for index, name in enumerate(stored)}
    instructions = []
    if frame:
        instructions.append(("sub", [_reg(REGISTER_SP), _reg(REGISTER_SP), _imm(frame)]))
    for name in stored:
        instructions.append(("str", [_reg(name)] + _slot(slots[name])))
    if keep_flags:
        instructions.append(("mrs", [_reg(17), (TOKEN_SYSTEM, 0, 0, 0)]))
        instructions.append(("str", [_reg(17)] + _slot(nzcv_slot)))
    instructions.append(("adrp", [_reg(0), (TOKEN_SYMBOL, symbol, SYMBOL_PAGE, 0)]))
    instructions.append(("add", [_reg(0), _reg(0), (TOKEN_SYMBOL, symbol, SYMBOL_PAGEOFF, 0)]))
    instructions.append(("ldr", [_reg(16), (TOKEN_ADDRESS, ADDRESS_BASE, 1, 0), _reg(0)]))
    instructions.append(("blr", [_reg(16)]))
    if keep_flags:
        instructions.append(("ldr", [_reg(17)] + _slot(nzcv_slot)))
        instructions.append(("msr", [(TOKEN_SYSTEM, 0, 0, 0), _reg(17)]))
    if reg != 0:
        instructions.append(("mov", [_reg(reg), _reg(0)]))
    for name in restored:
        instructions.append(("ldr", [_reg(name)] + _slot(slots[name])))
    if frame:
        instructions.append(("add", [_reg(REGISTER_SP), _reg(REGISTER_SP), _imm(frame)]))
    return instructions


def _matches(ctx, record, opcode, operands):
    return ctx.opcode(record) == opcode and ctx.operands(record) == operands


def _match_resolver_call(ctx, records, start, end):
    if start + 14 > end:
        return None
    adrp = ctx.operands(records[start + 7])
    if (ctx.opcode(records[start + 7]) != "adrp" or len(adrp) != 2
            or adrp[0] != _reg(0) or adrp[1][0] != TOKEN_SYMBOL
            or adrp[1][2:] != (SYMBOL_PAGE, 0)):
        return None
    symbol = adrp[1][1]
    reg = 0
    move = ctx.operands(records[start + 13])
    if (ctx.opcode(records[start + 13]) == "mov" and len(move) == 2
            and move[0][0] == TOKEN_REGISTER and move[1] == _reg(0)):
        reg = move[0][1]
    expected = tlv_resolver_instructions(symbol, reg, _ALL, emitted=True)
    stop = start + len(expected)
    if stop > end:
        return None
    for record, (opcode, operands) in zip(records[start:stop], expected):
        if not _matches(ctx, record, opcode, operands):
            return None
    return stop, symbol, reg


def _mask(ctx, tokens):
    mask = 0
    for kind, value, decoration, addend in tokens:
        if kind == TOKEN_REGISTER:
            mask |= _REGISTER_BITS.get(value, 0)
        elif kind == TOKEN_SYMBOL:
            # Match the original lexical conservatism, including register-like
            # symbol components, rather than narrowing to architectural reads.
            symbol = ctx.symbol(value)
            for register, bit in _REGISTER_BITS.items():
                if semantic_symbol_uses_register(symbol, register):
                    mask |= bit
    return mask


def _effects(ctx, record):
    opcode = ctx.opcode(record)
    if not opcode:
        return 0, 0
    operands = ctx.operands(record)
    if opcode in _NO_REGISTERS:
        return 0, 0
    if opcode.startswith("b."):
        return _NZCV, 0
    if opcode in _REGISTER_BRANCHES:
        return _mask(ctx, operands[:-1]), 0
    if opcode in _RETURNS:
        return _EXIT, 0
    if opcode == "bl":
        return _X0, _X30
    if opcode == "blr":
        return _X0 | _mask(ctx, operands), _X30
    if opcode in _LR_SIGNING:
        return _X30, 0
    if opcode == "mrs":
        return (_NZCV if operands[1:] == [(TOKEN_SYSTEM, 0, 0, 0)] else 0), _mask(ctx, operands[:1])
    if opcode == "msr":
        return _mask(ctx, operands[1:]), (_NZCV if operands[:1] == [(TOKEN_SYSTEM, 0, 0, 0)] else 0)
    lse_kind = lse_atomic_kind(opcode)
    if lse_kind:
        if lse_kind == 1:
            return _mask(ctx, operands[:1] + operands[2:]), _mask(ctx, operands[1:2])
        return _mask(ctx, operands), _mask(ctx, operands[:1])
    flags_read = _NZCV if opcode in _FLAG_READERS else 0
    flags_written = _NZCV if opcode in _FLAG_WRITERS else 0
    if opcode in _STORES or opcode in _COMPARES:
        return _mask(ctx, operands) | flags_read, flags_written
    if opcode in _PAIR_LOADS:
        return _mask(ctx, operands[2:]), _mask(ctx, operands[:2])
    if opcode in _PARTIAL_WRITES:
        return _mask(ctx, operands), _mask(ctx, operands[:1])
    if opcode in _DESTINATION_FIRST:
        return _mask(ctx, operands[1:]) | flags_read, _mask(ctx, operands[:1]) | flags_written
    return _ALL, 0


def _is_function_label(ctx, record):
    label = ctx.label(record)
    if label is None:
        return False
    name = ctx.symbol(label)
    return bool(name) and not name.startswith((" ", ".", "L_"))


def _ends_code(ctx, record):
    return ctx.is_directive(record) and ctx.directive_kind(record) not in (0, 1, 2, 3, 4, 6, 7, 8)


def _branch(ctx, record):
    opcode = ctx.opcode(record)
    if opcode == "br":
        return "indirect", None
    if opcode in _RETURNS:
        return "return", None
    if opcode == "b" or opcode.startswith("b.") or opcode in _REGISTER_BRANCHES:
        operands = ctx.operands(record)
        target = operands[-1]
        # Exact label lookup requires an undecorated, zero-addend symbol.
        # Decorated local targets retain the old conservative local exit.
        if target[0] != TOKEN_SYMBOL:
            return "indirect", None
        return ("jump" if opcode == "b" else "cond"), target
    return None


def _rewrite_function(ctx, records, start, end):
    calls: dict[int, tuple[int, int, int]] = {}
    index = start
    while index < end:
        if _matches(ctx, records[index], "sub", [_reg(REGISTER_SP), _reg(REGISTER_SP), _imm(_EMITTED_FRAME)]):
            match = _match_resolver_call(ctx, records, index, end)
            if match is not None:
                calls[match[0]] = (index, match[1], match[2])
                index = match[0]
                continue
        index += 1
    if not calls:
        return records[start:end]

    leaders = {start}
    labels: dict[int, int] = {}
    for index in range(start, end):
        line = records[index]
        label = ctx.label(line)
        if label is not None and ctx.symbol(label).startswith("L_"):
            leaders.add(index)
            labels[label] = index
        elif _branch(ctx, line) is not None and index + 1 < end:
            leaders.add(index + 1)
    starts = sorted(leaders)
    block_of = {line_index: block for block, line_index in enumerate(starts)}
    stops = starts[1:] + [end]

    successors: list[list[int]] = []
    exits: list[int] = []
    for block, (first, stop) in enumerate(zip(starts, stops)):
        following = [block + 1] if block + 1 < len(starts) else []
        fall_exit = 0 if following else _ALL
        branch = _branch(ctx, records[stop - 1]) if stop > first else None
        targets: list[int] = []
        exit_mask = 0
        if branch is None:
            targets, exit_mask = following, fall_exit
        else:
            kind, target = branch
            if kind in ("jump", "cond"):
                if target[2:] == (SYMBOL_PLAIN, 0) and target[1] in labels:
                    targets.append(block_of[labels[target[1]]])
                elif ctx.symbol(target[1]).startswith("L_"):
                    exit_mask = _ALL
                else:
                    exit_mask = _EXIT
                if kind == "cond":
                    targets.extend(following)
                    exit_mask |= fall_exit
            elif kind == "indirect":
                exit_mask = _ALL
            else:
                exit_mask = _EXIT
        successors.append(targets)
        exits.append(exit_mask)

    def transfer(block: int, live: int, record: dict[int, int] | None) -> int:
        cursor = stops[block]
        first = starts[block]
        while cursor > first:
            call = calls.get(cursor)
            if call is not None:
                call_start, _symbol, reg = call
                if record is not None:
                    record[call_start] = live
                live &= ~_REGISTER_BITS.get(reg, 0)
                cursor = call_start
                continue
            line = records[cursor - 1]
            if ctx.opcode(line):
                read, written = _effects(ctx, line)
                live = (live & ~written) | read
            cursor -= 1
        return live

    live_in = [0] * len(starts)
    changed = True
    while changed:
        changed = False
        for block in range(len(starts) - 1, -1, -1):
            live_out = exits[block]
            for successor in successors[block]:
                live_out |= live_in[successor]
            live = transfer(block, live_out, None)
            if live != live_in[block]:
                live_in[block] = live
                changed = True

    live_after: dict[int, int] = {}
    for block in range(len(starts)):
        live_out = exits[block]
        for successor in successors[block]:
            live_out |= live_in[successor]
        transfer(block, live_out, live_after)

    out: list[int] = []
    index = start
    by_start = {call_start: (stop, symbol, reg) for stop, (call_start, symbol, reg) in calls.items()}
    while index < end:
        call = by_start.get(index)
        if call is None:
            out.append(records[index])
            index += 1
            continue
        stop, symbol, reg = call
        out.extend(ctx.emit(opcode, operands) for opcode, operands in tlv_resolver_instructions(symbol, reg, live_after[index]))
        index = stop
    return out


def elide_dead_tlv_resolver_saves_records(ctx, records):
    if not any(_matches(ctx, record, "blr", [_reg(16)]) for record in records):
        return records
    out = []
    index = 0
    while index < len(records):
        if not _is_function_label(ctx, records[index]):
            out.append(records[index])
            index += 1
            continue
        stop = index + 1
        while stop < len(records) and not _is_function_label(ctx, records[stop]) and not _ends_code(ctx, records[stop]):
            stop += 1
        out.extend(_rewrite_function(ctx, records, index, stop))
        index = stop
    return out


def elide_dead_tlv_resolver_saves(fragments, semantics, span):
    ctx = TypedPassContext(fragments, semantics)
    return ctx.write_span(elide_dead_tlv_resolver_saves_records(ctx, ctx.read_span(span)))
