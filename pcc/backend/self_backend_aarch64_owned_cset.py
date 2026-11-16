"""Private pure mode-12 decisions over validated owned scalar planes."""
from .self_backend_aarch64_owned_stack import stack, register_class
from .self_backend_aarch64_semantics import (
    TOKEN_REGISTER, TOKEN_CONDITION, TOKEN_SYMBOL, TOKEN_IMMEDIATE,
    IMMEDIATE_HASH, semantic_opcode_id,
)

CSET: int = semantic_opcode_id("cset")
STURB: int = semantic_opcode_id("sturb")
AND: int = semantic_opcode_id("and")
CBZ: int = semantic_opcode_id("cbz")
CBNZ: int = semantic_opcode_id("cbnz")


def decide(view: tuple[list[int], list[int], list[int]], a: int, b: int, c: int, d: int) -> tuple[int, int, int, int, int, int, int]:
    """Return consumed, register, branch opcode, and the complete target token."""
    if d < 0:
        return (0, 0, 0, 0, 0, 0, 0)
    records: list[int] = view[0]
    headers: list[int] = view[1]
    tokens: list[int] = view[2]
    if records[a * 4 + 1] < 0 or records[c * 4 + 1] < 0 or records[d * 4 + 1] < 0:
        return (0, 0, 0, 0, 0, 0, 0)
    if headers[a * 4] != CSET or headers[a * 4 + 3] != 2:
        return (0, 0, 0, 0, 0, 0, 0)
    at: int = headers[a * 4 + 2] * 4
    if tokens[at] != TOKEN_REGISTER or tokens[at + 4] != TOKEN_CONDITION or register_class(tokens[at + 1]) != 2:
        return (0, 0, 0, 0, 0, 0, 0)
    store: tuple[int, int, int, int, int] = stack(view, b, STURB)
    if store[1] == 0:
        return (0, 0, 0, 0, 0, 0, 0)
    op: int = headers[d * 4]
    if op not in (CBZ, CBNZ) or headers[d * 4 + 3] != 2:
        return (0, 0, 0, 0, 0, 0, 0)
    dt: int = headers[d * 4 + 2] * 4
    if tokens[dt] != TOKEN_REGISTER or register_class(tokens[dt + 1]) not in (1, 2) or tokens[dt + 4] != TOKEN_SYMBOL:
        return (0, 0, 0, 0, 0, 0, 0)
    if headers[c * 4] != AND or headers[c * 4 + 3] != 3:
        return (0, 0, 0, 0, 0, 0, 0)
    ct: int = headers[c * 4 + 2] * 4
    if (tokens[ct] != TOKEN_REGISTER or tokens[ct + 4] != TOKEN_REGISTER
        or register_class(tokens[ct + 1]) != 2 or register_class(tokens[ct + 5]) != 2
        or tokens[ct + 8] != TOKEN_IMMEDIATE or tokens[ct + 9] != 255
        or tokens[ct + 10] != 16 or tokens[ct + 11] != IMMEDIATE_HASH):
        return (0, 0, 0, 0, 0, 0, 0)
    if tokens[ct + 1] != tokens[ct + 5]:
        return (0, 0, 0, 0, 0, 0, 0)
    if store[0] != tokens[ct + 5] or tokens[ct + 5] != tokens[at + 1] or tokens[dt + 1] != tokens[ct + 1]:
        return (0, 0, 0, 0, 0, 0, 0)
    return (4, tokens[at + 1], op, tokens[dt + 4], tokens[dt + 5], tokens[dt + 6], tokens[dt + 7])
