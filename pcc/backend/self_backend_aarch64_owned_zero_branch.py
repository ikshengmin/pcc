"""Private owned mode-11 eligibility; branch emission and zero definition remain live."""
from .self_backend_aarch64_owned_stack import register_class
from .self_backend_aarch64_semantics import TOKEN_REGISTER, TOKEN_IMMEDIATE, TOKEN_SHIFT, IMMEDIATE_HASH, TOKEN_SYMBOL, semantic_opcode_id
MOVZ: int = semantic_opcode_id("movz")
CBZ: int = semantic_opcode_id("cbz")
CBNZ: int = semantic_opcode_id("cbnz")

def decide(view: tuple[list[int], list[int], list[int]], a: int, b: int) -> tuple[int, int, int, int, int]:
    if b < 0:
        return (-1, 0, 0, 0, 0)
    records: list[int] = view[0]
    headers: list[int] = view[1]
    tokens: list[int] = view[2]
    if records[a * 4 + 1] < 0 or records[b * 4 + 1] < 0:
        return (-1, 0, 0, 0, 0)
    count: int = headers[a * 4 + 3]
    if headers[a * 4] != MOVZ or count not in (2, 3):
        return (-1, 0, 0, 0, 0)
    at: int = headers[a * 4 + 2] * 4
    if tokens[at] != TOKEN_REGISTER or register_class(tokens[at + 1]) not in (1, 2):
        return (-1, 0, 0, 0, 0)
    zero: int = tokens[at + 1]
    if tokens[at + 4] != TOKEN_IMMEDIATE or tokens[at + 5] != 0 or tokens[at + 6] != 10 or tokens[at + 7] != IMMEDIATE_HASH:
        return (-1, 0, 0, 0, 0)
    if count == 3 and (tokens[at + 8] != TOKEN_SHIFT or tokens[at + 9] != 0 or tokens[at + 10] != 0 or tokens[at + 11] != 1):
        return (-1, 0, 0, 0, 0)
    opcode: int = headers[b * 4]
    if opcode not in (CBZ, CBNZ) or headers[b * 4 + 3] != 2:
        return (-1, 0, 0, 0, 0)
    bt: int = headers[b * 4 + 2] * 4
    if tokens[bt] != TOKEN_REGISTER or tokens[bt + 1] != zero or tokens[bt + 4] != TOKEN_SYMBOL:
        return (-1, 0, 0, 0, 0)
    return (opcode, tokens[bt + 4], tokens[bt + 5], tokens[bt + 6], tokens[bt + 7])
