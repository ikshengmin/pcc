"""Darwin TLV resolver calls that preserve only what is live after them.

Forming a thread-local address inside another instruction calls the TLV
resolver through the variable's descriptor (``blr`` to its first word). The
resolver clobbers x0, x16, x17, LR and NZCV, so the emitter brackets the call
with saves and restores of all five (``_materialize_thread_local_address``).
On the optimized text path this pass computes, per function, which of the
five are live after each such call (a backward dataflow over the function's
basic blocks) and keeps only those saves.

The model is conservative wherever it is not exact: an instruction it does
not know reads all five; a branch to a label outside the function's code, an
indirect branch and falling off the code read all five; a call reads x0 and
writes only LR, so values in x16, x17 and NZCV are assumed to survive it. A
return or tail call reads x0 and LR. Each resolver call is one node of the
dataflow that writes its destination and passes the other registers through,
which is exactly what its rewritten form guarantees.
"""

from __future__ import annotations

from .arm64_encode import lse_atomic_kind
from .self_backend_aarch64_darwin_mem import (
    emitted_addsub_immediate_line,
    emitted_fixed_instruction_line,
    emitted_global_address_lines,
    emitted_memory_instruction_line,
    emitted_move_register_line,
)

_X0 = 1
_X16 = 2
_X17 = 4
_X30 = 8
_NZCV = 16
_ALL = _X0 | _X16 | _X17 | _X30 | _NZCV
_EXIT = _X0 | _X30

_REGISTER_BITS = {
    "x0": _X0, "w0": _X0,
    "x16": _X16, "w16": _X16,
    "x17": _X17, "w17": _X17,
    "x30": _X30, "w30": _X30, "lr": _X30,
}

# The emitter's save order and frame: four registers, then NZCV through x17.
_SAVE_ORDER = (("x0", _X0), ("x16", _X16), ("x17", _X17), ("x30", _X30))
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


def _slot(offset: int) -> str:
    return "[sp]" if offset == 0 else "[sp, #" + str(offset) + "]"


def tlv_resolver_lines(symbol: str, reg: str, preserve: int, *, emitted: bool = False) -> list[str]:
    """One resolver call leaving ``symbol``'s address in ``reg``.

    ``emitted`` reproduces the emitter's text exactly: all four registers are
    stored (the destination too) and restored except the destination, and
    NZCV is carried through x17. Otherwise only the registers in ``preserve``
    other than the destination are kept, in a frame of 16-byte multiples.
    """
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
    lines = []
    if frame:
        lines.append(emitted_addsub_immediate_line("sub", "sp", "sp", frame))
    for name in stored:
        lines.append(emitted_memory_instruction_line("str", name, "sp", slots[name]))
    if keep_flags:
        lines.append(emitted_fixed_instruction_line("mrs x17, nzcv"))
        lines.append(emitted_memory_instruction_line("str", "x17", "sp", nzcv_slot))
    lines.extend(emitted_global_address_lines("x0", symbol, False))
    lines.append(emitted_memory_instruction_line("ldr", "x16", "x0"))
    lines.append(emitted_fixed_instruction_line("blr x16"))
    if keep_flags:
        lines.append(emitted_memory_instruction_line("ldr", "x17", "sp", nzcv_slot))
        lines.append(emitted_fixed_instruction_line("msr nzcv, x17"))
    if reg != "x0":
        lines.append(emitted_move_register_line(reg, "x0"))
    for name in restored:
        lines.append(emitted_memory_instruction_line("ldr", name, "sp", slots[name]))
    if frame:
        lines.append(emitted_addsub_immediate_line("add", "sp", "sp", frame))
    return lines


def _match_resolver_call(lines: list[str], start: int, end: int) -> tuple[int, str, str] | None:
    """(stop, symbol, destination) when the emitter's sequence starts at ``start``."""
    if start + 14 > end or lines[start] != "  sub sp, sp, #" + str(_EMITTED_FRAME):
        return None
    adrp = lines[start + 7]
    if not (adrp.startswith("  adrp x0, ") and adrp.endswith("@PAGE")):
        return None
    symbol = adrp[len("  adrp x0, "):-len("@PAGE")]
    move = lines[start + 13]
    reg = "x0"
    if move.startswith("  mov ") and move.endswith(", x0"):
        reg = move[len("  mov "):-len(", x0")]
    expected = tlv_resolver_lines(symbol, reg, _ALL, emitted=True)
    stop = start + len(expected)
    if stop > end or lines[start:stop] != expected:
        return None
    return stop, symbol, reg


def _mask(text: str) -> int:
    mask = 0
    token = []
    for ch in text + " ":
        if ch.isalnum() or ch == "_":
            token.append(ch)
        elif token:
            mask |= _REGISTER_BITS.get("".join(token), 0)
            token = []
    return mask


def _effects(line: str) -> tuple[int, int]:
    """(read, written) subsets of x0/x16/x17/LR/NZCV for one instruction line."""
    body = line[2:]
    if body.startswith(("//", ";")) or not body:
        return 0, 0
    opcode, _sep, rest = body.partition(" ")
    if opcode in _NO_REGISTERS:
        return 0, 0
    if opcode.startswith("b."):
        return _NZCV, 0
    if opcode in _REGISTER_BRANCHES:
        return _mask(rest.rpartition(", ")[0]), 0
    if opcode in _RETURNS:
        return _EXIT, 0
    if opcode == "bl":
        return _X0, _X30
    if opcode == "blr":
        return _X0 | _mask(rest), _X30
    if opcode in _LR_SIGNING:
        return _X30, 0
    if opcode == "mrs":
        dest, _sep2, source = rest.partition(", ")
        return (_NZCV if source == "nzcv" else 0), _mask(dest)
    if opcode == "msr":
        target, _sep2, source = rest.partition(", ")
        return _mask(source), (_NZCV if target == "nzcv" else 0)
    lse_kind = lse_atomic_kind(opcode)
    if lse_kind:
        # LD<op>/SWP read Rs and the address and write Rt; CAS reads all and writes Rs.
        first, _sep2, after = rest.partition(", ")
        second, _sep3, address = after.partition(", ")
        if lse_kind == 1:
            return _mask(first) | _mask(address), _mask(second)
        return _mask(rest), _mask(first)
    flags_read = _NZCV if opcode in _FLAG_READERS else 0
    flags_written = _NZCV if opcode in _FLAG_WRITERS else 0
    if opcode in _STORES or opcode in _COMPARES:
        return _mask(rest) | flags_read, flags_written
    if opcode in _PAIR_LOADS:
        first, _sep2, after = rest.partition(", ")
        second, _sep3, address = after.partition(", ")
        return _mask(address), _mask(first) | _mask(second)
    if opcode in _PARTIAL_WRITES:
        dest, _sep2, _tail = rest.partition(", ")
        return _mask(rest), _mask(dest)
    if opcode in _DESTINATION_FIRST:
        dest, _sep2, tail = rest.partition(", ")
        return _mask(tail) | flags_read, _mask(dest) | flags_written
    return _ALL, 0


def _is_function_label(line: str) -> bool:
    return bool(line) and not line.startswith((" ", ".", "L_")) and line.endswith(":")


def _ends_code(line: str) -> bool:
    return line.startswith(".") and not line.startswith(_CODE_DIRECTIVES)


def _branch(line: str) -> tuple[str, str] | None:
    """(kind, target) of a block-ending instruction: jump, cond, indirect or return."""
    if not line.startswith("  "):
        return None
    opcode, _sep, rest = line[2:].partition(" ")
    if opcode == "b":
        return "jump", rest
    if opcode.startswith("b."):
        return "cond", rest
    if opcode in _REGISTER_BRANCHES:
        return "cond", rest.rpartition(", ")[2]
    if opcode == "br":
        return "indirect", ""
    if opcode in _RETURNS:
        return "return", ""
    return None


def _rewrite_function(lines: list[str], start: int, end: int) -> list[str]:
    calls: dict[int, tuple[int, str, str]] = {}
    index = start
    while index < end:
        if lines[index] == "  sub sp, sp, #" + str(_EMITTED_FRAME):
            match = _match_resolver_call(lines, index, end)
            if match is not None:
                calls[match[0]] = (index, match[1], match[2])
                index = match[0]
                continue
        index += 1
    if not calls:
        return lines[start:end]

    leaders = {start}
    labels: dict[str, int] = {}
    for index in range(start, end):
        line = lines[index]
        if line.startswith("L_") and line.endswith(":"):
            leaders.add(index)
            labels[line[:-1]] = index
        elif _branch(line) is not None and index + 1 < end:
            leaders.add(index + 1)
    starts = sorted(leaders)
    block_of = {line_index: block for block, line_index in enumerate(starts)}
    stops = starts[1:] + [end]

    successors: list[list[int]] = []
    exits: list[int] = []
    for block, (first, stop) in enumerate(zip(starts, stops)):
        following = [block + 1] if block + 1 < len(starts) else []
        fall_exit = 0 if following else _ALL
        branch = _branch(lines[stop - 1]) if stop > first else None
        targets: list[int] = []
        exit_mask = 0
        if branch is None:
            targets, exit_mask = following, fall_exit
        else:
            kind, target = branch
            if kind in ("jump", "cond"):
                if target in labels:
                    targets.append(block_of[labels[target]])
                elif target.startswith("L_"):
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
            line = lines[cursor - 1]
            if line.startswith("  "):
                read, written = _effects(line)
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

    out: list[str] = []
    index = start
    by_start = {call_start: (stop, symbol, reg) for stop, (call_start, symbol, reg) in calls.items()}
    while index < end:
        call = by_start.get(index)
        if call is None:
            out.append(lines[index])
            index += 1
            continue
        stop, symbol, reg = call
        out.extend(tlv_resolver_lines(symbol, reg, live_after[index]))
        index = stop
    return out


def elide_dead_tlv_resolver_saves(lines: list[str]) -> list[str]:
    """Keep only the live registers' saves around each Darwin TLV resolver call."""
    if "  blr x16" not in lines:
        return lines
    out: list[str] = []
    index = 0
    while index < len(lines):
        if not _is_function_label(lines[index]):
            out.append(lines[index])
            index += 1
            continue
        stop = index + 1
        while stop < len(lines) and not _is_function_label(lines[stop]) and not _ends_code(lines[stop]):
            stop += 1
        out.extend(_rewrite_function(lines, index, stop))
        index = stop
    return out
