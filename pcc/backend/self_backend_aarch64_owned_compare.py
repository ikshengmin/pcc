"""Private owned mode-5 eligibility; liveness and compare emission remain live."""
from .self_backend_aarch64_owned_stack import register_class
from .self_backend_aarch64_semantics import TOKEN_REGISTER, TOKEN_IMMEDIATE, TOKEN_SHIFT, IMMEDIATE_HASH, REGISTER_XZR, REGISTER_WZR, semantic_opcode_id
MOVZ: int = semantic_opcode_id("movz")
CMP: int = semantic_opcode_id("cmp")

def decide(view: tuple[list[int], list[int], list[int]], a: int, b: int) -> tuple[int, int]:
    if b < 0:
        return (-1, -1)
    records: list[int] = view[0]
    headers: list[int] = view[1]
    tokens: list[int] = view[2]
    if records[a * 4 + 1] < 0 or records[b * 4 + 1] < 0:
        return (-1, -1)
    count: int = headers[a * 4 + 3]
    if headers[a * 4] != MOVZ or count not in (2, 3):
        return (-1, -1)
    at: int = headers[a * 4 + 2] * 4
    if tokens[at] != TOKEN_REGISTER or register_class(tokens[at + 1]) not in (1, 2):
        return (-1, -1)
    zero: int = tokens[at + 1]
    if tokens[at + 4] != TOKEN_IMMEDIATE or tokens[at + 5] != 0 or tokens[at + 6] != 10 or tokens[at + 7] != IMMEDIATE_HASH:
        return (-1, -1)
    if count == 3 and (tokens[at + 8] != TOKEN_SHIFT or tokens[at + 9] != 0 or tokens[at + 10] != 0 or tokens[at + 11] != 1):
        return (-1, -1)
    if headers[b * 4] != CMP or headers[b * 4 + 3] != 2:
        return (-1, -1)
    bt: int = headers[b * 4 + 2] * 4
    if tokens[bt] != TOKEN_REGISTER or tokens[bt + 4] != TOKEN_REGISTER:
        return (-1, -1)
    lhs: int = tokens[bt + 1]
    rhs: int = tokens[bt + 5]
    kind: int = register_class(lhs)
    if kind not in (1, 2) or kind != register_class(rhs) or rhs != zero or lhs == zero or lhs in (REGISTER_XZR, REGISTER_WZR):
        return (-1, -1)
    return (lhs, zero)
