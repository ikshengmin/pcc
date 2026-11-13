"""Adjacent memory pairing with exact source-form and barrier eligibility."""
from __future__ import annotations

from . import BackendUnavailable
from .self_backend_aarch64_semantics import (
    TOKEN_ADDRESS, TOKEN_IMMEDIATE, TOKEN_REGISTER,
    ADDRESS_BASE, ADDRESS_OFFSET, REGISTER_SP, REGISTER_XZR, IMMEDIATE_HASH,
)


def _transfer(ctx, record_id):
    opcode = ctx.opcode(record_id)
    if opcode not in ("ldr", "str", "ldur", "stur"):
        return None
    tokens = ctx.operands(record_id)
    if len(tokens) not in (3, 4):
        return None
    reg, address, base = tokens[:3]
    if (reg[0] != TOKEN_REGISTER or not (0 <= reg[1] <= 30 or reg[1] == REGISTER_XZR)
            or base[0] != TOKEN_REGISTER or not (0 <= base[1] <= 30 or base[1] == REGISTER_SP)):
        return None
    offset = 0
    if address == (TOKEN_ADDRESS, ADDRESS_BASE, 1, 0) and len(tokens) == 3:
        pass
    elif address == (TOKEN_ADDRESS, ADDRESS_OFFSET, 2, 0) and len(tokens) == 4:
        immediate = tokens[3]
        if immediate[0] != TOKEN_IMMEDIATE or not immediate[3] & IMMEDIATE_HASH:
            return None
        offset = immediate[1]
    else:
        return None
    if offset % 8:
        return None
    return opcode[:2] + "r", reg[1], base[1], offset


def _pair(ctx, first_id, second_id):
    first = _transfer(ctx, first_id)
    second = _transfer(ctx, second_id)
    if first is None or second is None:
        return None
    opcode, first_reg, base, offset = first
    second_opcode, second_reg, second_base, second_offset = second
    if (opcode == second_opcode == "str" and first_reg == second_reg == REGISTER_XZR
            and base == second_base and second_offset == offset - 8):
        offset, second_offset = second_offset, offset
    if (opcode != second_opcode or base != second_base or second_offset != offset + 8
            or offset < -512 or offset > 504):
        return None
    if opcode == "ldr" and (first_reg == second_reg or first_reg == base or second_reg == base):
        return None
    tokens = [(TOKEN_REGISTER, first_reg, 0, 0), (TOKEN_REGISTER, second_reg, 0, 0)]
    tokens.append((TOKEN_ADDRESS, ADDRESS_OFFSET if offset else ADDRESS_BASE, 2 if offset else 1, 0))
    tokens.append((TOKEN_REGISTER, base, 0, 0))
    if offset:
        tokens.append((TOKEN_IMMEDIATE, offset, 10, IMMEDIATE_HASH))
    return ctx.emit("ldp" if opcode == "ldr" else "stp", tokens)


def pair_adjacent_aarch64_64bit_memory_ops_records(ctx, records, *, enabled=True):
    out = []
    depth = 0
    exclusive = False
    index = 0
    while index < len(records):
        record_id = records[index]
        barrier = ctx.memory_pair_barrier(record_id)
        if barrier is not None:
            if barrier:
                depth += 1
            elif depth <= 0:
                raise BackendUnavailable("self AArch64 memory-pair pass saw an unmatched barrier end")
            else:
                depth -= 1
            index += 1
            continue
        opcode = ctx.opcode(record_id)
        if opcode.startswith(("ldaxr", "ldxr", "ldaxp", "ldxp")):
            exclusive = True
        if enabled and depth == 0 and not exclusive and index + 1 < len(records):
            if ctx.memory_pair_barrier(records[index + 1]) is None:
                paired = _pair(ctx, record_id, records[index + 1])
                if paired is not None:
                    out.append(paired)
                    index += 2
                    continue
        out.append(record_id)
        if opcode == "clrex" or opcode.startswith(("stlxr", "stxr", "stlxp", "stxp")):
            exclusive = False
        index += 1
    if depth != 0:
        raise BackendUnavailable("self AArch64 memory-pair pass saw an unterminated barrier")
    return out


def pair_adjacent_aarch64_64bit_memory_ops(fragments, semantics, span, *, enabled=True):
    from .self_backend_aarch64_typed_pass_context import TypedPassContext
    ctx = TypedPassContext(fragments, semantics)
    return ctx.write_span(pair_adjacent_aarch64_64bit_memory_ops_records(ctx, ctx.read_span(span), enabled=enabled))
