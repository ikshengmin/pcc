"""Pure stack-window decisions over validated, privately owned scalar lists.

The caller owns and does not mutate these lists while deciding a window.
This module neither validates provenance nor admits owners: the driver must
retain that boundary and use the existing producer for replacement emission.
"""
from __future__ import annotations

from .self_backend_aarch64_semantics import (
    ADDRESS_OFFSET,
    IMMEDIATE_HASH,
    IMMEDIATE_NEGATIVE_ZERO,
    TOKEN_ADDRESS,
    TOKEN_IMMEDIATE,
    TOKEN_REGISTER,
    REGISTER_FP,
    semantic_opcode_id,
)

STUR: int = semantic_opcode_id("stur")
LDUR: int = semantic_opcode_id("ldur")
STURB: int = semantic_opcode_id("sturb")
LDURB: int = semantic_opcode_id("ldurb")


def stack(
    view: tuple[list[int], list[int], list[int]],
    rid: int,
    opcode: int,
) -> tuple[int, int, int, int, int]:
    """Return (register, immediate-kind, value, radix, flags), or a sentinel.

    Sentinel field 1 is zero; every successful result has TOKEN_IMMEDIATE
    there. Do not use register sign as the match discriminator.
    """
    records: list[int] = view[0]
    headers: list[int] = view[1]
    tokens: list[int] = view[2]
    if rid < 0 or records[rid * 4 + 1] < 0:
        return (-1, 0, 0, 0, 0)
    h: int = rid * 4
    if headers[h] != opcode or headers[h + 3] != 4:
        return (-1, 0, 0, 0, 0)
    t: int = headers[h + 2] * 4
    if (
        tokens[t] != TOKEN_REGISTER
        or tokens[t + 4] != TOKEN_ADDRESS
        or tokens[t + 5] != ADDRESS_OFFSET
        or tokens[t + 6] != 2
        or tokens[t + 8] != TOKEN_REGISTER
        or tokens[t + 9] != 29
        or tokens[t + 12] != TOKEN_IMMEDIATE
    ):
        return (-1, 0, 0, 0, 0)
    if not (tokens[t + 15] & IMMEDIATE_HASH) or not (
        tokens[t + 13] < 0 or tokens[t + 15] & IMMEDIATE_NEGATIVE_ZERO
    ):
        return (-1, 0, 0, 0, 0)
    return (
        tokens[t + 1], tokens[t + 12], tokens[t + 13],
        tokens[t + 14], tokens[t + 15],
    )


def register_class(r: int) -> int:
    if 0 <= r <= 30 or r == 130:
        return 1
    if 32 <= r <= 62 or r in (129, 131):
        return 2
    if 64 <= r <= 95:
        return 3
    if 96 <= r <= 127 or r == 128:
        return 4
    return 0


def register_alias(r: int) -> int:
    if 0 <= r <= 62:
        return r % 32
    if 64 <= r <= 127:
        return 1000 + r % 32
    if r in (128, 129):
        # Preserve the existing spelling rules' distinct SP / WSP aliases.
        return 2000 + r
    if r in (130, 131):
        return 2002
    return 3000 + r


def decide(
    view: tuple[list[int], list[int], list[int]],
    mode: int,
    a: int,
    b: int,
    c: int,
) -> tuple[int, int, int, int]:
    """Return (consumed, destination, source, byte), or four zeroes."""
    last: int = b if mode == 1 else c
    if last < 0:
        return (0, 0, 0, 0)
    middle: tuple[int, int, int, int, int] = (-1, 0, 0, 0, 0)
    if mode == 2:
        middle = stack(view, b, LDUR)
        if middle[1] == 0:
            middle = stack(view, b, LDURB)
    for store_op, load_op in ((STUR, LDUR), (STURB, LDURB)):
        store: tuple[int, int, int, int, int] = stack(view, a, store_op)
        load: tuple[int, int, int, int, int] = stack(view, last, load_op)
        if (
            store[1] == 0 or load[1] == 0
            or store[1] != load[1] or store[2] != load[2]
            or store[3] != load[3] or store[4] != load[4]
        ):
            continue
        if mode == 2 and (
            middle[1] == 0
            or middle[0] in (29, 61, REGISTER_FP)
            or (
                middle[1] == store[1] and middle[2] == store[2]
                and middle[3] == store[3] and middle[4] == store[4]
            )
            or register_alias(middle[0]) == register_alias(store[0])
        ):
            continue
        src: int = store[0]
        dst: int = load[0]
        byte: int = int(load_op == LDURB)
        if byte:
            if register_class(dst) != 2 or register_class(src) != 2:
                continue
        elif dst != src and (
            register_class(dst) != register_class(src)
            or register_class(dst) not in (1, 2, 3, 4)
        ):
            continue
        return (mode + 1, dst, src, byte)
    return (0, 0, 0, 0)
