"""Private owned mode-8 window eligibility; producers stay on the original path."""
from .self_backend_aarch64_owned_stack import register_class
from .self_backend_aarch64_semantics import TOKEN_REGISTER, semantic_opcode_id
MOV: int = semantic_opcode_id("mov")
ADD: int = semantic_opcode_id("add")
SUB: int = semantic_opcode_id("sub")

def decide(view: tuple[list[int], list[int], list[int]], a: int, b: int) -> tuple[int, int]:
    if b < 0:
        return (-1, -1)
    records: list[int] = view[0]
    headers: list[int] = view[1]
    tokens: list[int] = view[2]
    if records[a * 4 + 1] < 0 or records[b * 4 + 1] < 0:
        return (-1, -1)
    if headers[a * 4] != MOV or headers[a * 4 + 3] != 2:
        return (-1, -1)
    at: int = headers[a * 4 + 2] * 4
    if tokens[at] != TOKEN_REGISTER or tokens[at + 4] != TOKEN_REGISTER:
        return (-1, -1)
    dest: int = tokens[at + 1]
    src: int = tokens[at + 5]
    kind: int = register_class(dest)
    if dest == src or kind not in (1, 2) or kind != register_class(src):
        return (-1, -1)
    if not (9 <= dest <= 15 or 41 <= dest <= 47):
        return (-1, -1)
    if headers[b * 4] not in (ADD, SUB) or headers[b * 4 + 3] < 3:
        return (-1, -1)
    bt: int = headers[b * 4 + 2] * 4
    if tokens[bt] != TOKEN_REGISTER or tokens[bt + 4] != TOKEN_REGISTER:
        return (-1, -1)
    if tokens[bt + 1] != dest or tokens[bt + 5] != dest:
        return (-1, -1)
    return (dest, src)
