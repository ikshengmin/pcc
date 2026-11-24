"""fused-protocol: drop calls the fused collector answers without work.

The fused collector is GC0 (reference counting with the cycle collector;
docs/status.md, "One collector"). It never moves an object and owes no
store barrier, so these calls, emitted for the relocating and tracing
collectors, return at once under it:

* pcc_gc_pin / pcc_gc_unpin: nothing reads the pin (pcc_gc_pins_inert);
* pcc_gc_foreign_lease_acquire / pcc_gc_borrowed_lease_acquire answer 0
  and pcc_gc_foreign_lease_release answers 0 for a 0 token;
* pcc_gc_note_slot_write_barrier / pcc_gc_note_write_barrier return.

pcc_gc_load_ptr and pcc_gc_resolve_owned_ptr stay: the backend's precise
stack-map analysis reads a value's root provenance from those calls.

Each is removed or replaced by its answer. No block is split, so the
control-flow graph and every phi are unchanged. pcc has one collector, so
this runs for every program and runtime module.

The line shapes are matched by scanning, not by ``re``: native pcc1 runs
this pass, and its owned regex engine rejects these patterns ("pattern
outside the native regex subset"). The accepted shapes are those of the
former expressions, with VALUE = ``null | %NAME | @NAME`` and NAME one or
more ASCII letters, digits, ``_``, ``.``, ``$`` or ``-``. Every class is
ASCII so host pcc0 and native pcc1 decide alike; the former ``\\w``, ``\\d``
and ``\\s`` also accepted non-ASCII characters, which emitted IR names do
not contain unquoted. Such a line keeps its (inert) call::

    DROP: \\s* call void (ptr|ptr, ptr|ptr, ptr, ptr) @F(ptr VALUE[, ptr VALUE]{0,2}) \\s*
          F = pcc_gc_pin | pcc_gc_unpin | pcc_gc_note_slot_write_barrier
              | pcc_gc_note_write_barrier
    ZERO: (\\s*)(%NAME) = call i64 (ptr|ptr, i64) @F(ptr VALUE[, i64 (-?DIGITS|%NAME)]) \\s*
          F = pcc_gc_foreign_lease_acquire | pcc_gc_borrowed_lease_acquire
              | pcc_gc_foreign_lease_release
          becomes ``\\1\\2 = add i64 0, 0``.

DIGITS are ``0``-``9``; ``\\s`` is the ASCII whitespace of Python's ``\\s``.
"""
from __future__ import annotations

_DROP_TYPES = ("ptr", "ptr, ptr", "ptr, ptr, ptr")
_DROP_FUNCTIONS = (
    "pcc_gc_pin",
    "pcc_gc_unpin",
    "pcc_gc_note_slot_write_barrier",
    "pcc_gc_note_write_barrier",
)
_ZERO_TYPES = ("ptr", "ptr, i64")
_ZERO_FUNCTIONS = (
    "pcc_gc_foreign_lease_acquire",
    "pcc_gc_borrowed_lease_acquire",
    "pcc_gc_foreign_lease_release",
)


_SPACE = "\t\n\x0b\x0c\r\x1c\x1d\x1e\x1f "
_NAME_PUNCTUATION = "_.$-"


def _is_digit(ch: str) -> bool:
    return "0" <= ch <= "9"


def _is_name_char(ch: str) -> bool:
    return ("a" <= ch <= "z" or "A" <= ch <= "Z" or _is_digit(ch)
            or ch in _NAME_PUNCTUATION)


def _skip_space(line: str, index: int) -> int:
    while index < len(line) and line[index] in _SPACE:
        index += 1
    return index


def _name_end(line: str, index: int) -> int:
    """End of NAME at ``index``, or -1 when it is empty."""
    end = index
    while end < len(line) and _is_name_char(line[end]):
        end += 1
    return end if end > index else -1


def _value_end(line: str, index: int) -> int:
    """End of VALUE at ``index``, or -1."""
    if line.startswith("null", index):
        return index + 4
    if index < len(line) and (line[index] == "%" or line[index] == "@"):
        return _name_end(line, index + 1)
    return -1


def _call_arguments(line: str, index: int, ret: str, types, functions) -> int:
    """Index after ``call RET (TYPES) @F(`` at ``index``, or -1."""
    prefix = "call " + ret + " ("
    if not line.startswith(prefix, index):
        return -1
    index += len(prefix)
    for signature in types:
        head = signature + ") @"
        if line.startswith(head, index):
            name_at = index + len(head)
            for function in functions:
                if line.startswith(function + "(", name_at):
                    return name_at + len(function) + 1
            return -1
    return -1


def _rest_is_space(line: str, index: int) -> bool:
    return _skip_space(line, index) == len(line)


def _is_drop(line: str) -> bool:
    index = _call_arguments(line, _skip_space(line, 0), "void", _DROP_TYPES, _DROP_FUNCTIONS)
    if index < 0 or not line.startswith("ptr ", index):
        return False
    index = _value_end(line, index + 4)
    if index < 0:
        return False
    extra = 0
    while extra < 2 and line.startswith(", ptr ", index):
        end = _value_end(line, index + 6)
        if end < 0:
            break
        index = end
        extra += 1
    if not line.startswith(")", index):
        return False
    return _rest_is_space(line, index + 1)


def _zero_answer(line: str):
    """``INDENT%NAME = add i64 0, 0`` for a ZERO line, else None."""
    indent_end = _skip_space(line, 0)
    if not line.startswith("%", indent_end):
        return None
    name_end = _name_end(line, indent_end + 1)
    if name_end < 0 or not line.startswith(" = ", name_end):
        return None
    index = _call_arguments(line, name_end + 3, "i64", _ZERO_TYPES, _ZERO_FUNCTIONS)
    if index < 0 or not line.startswith("ptr ", index):
        return None
    index = _value_end(line, index + 4)
    if index < 0:
        return None
    if line.startswith(", i64 ", index):
        token = index + 6
        digits = token + 1 if line.startswith("-", token) else token
        end = digits
        while end < len(line) and _is_digit(line[end]):
            end += 1
        if end == digits:
            end = _name_end(line, token + 1) if line.startswith("%", token) else -1
        if end < 0:
            return None
        index = end
    if not line.startswith(")", index) or not _rest_is_space(line, index + 1):
        return None
    return line[:name_end] + " = add i64 0, 0"


def fused_protocol_text(text: str) -> tuple[str, bool]:
    """Apply the pass to one module's IR text; returns (text, changed)."""
    if "@pcc_gc_" not in text:
        return text, False
    output = []
    changed = False
    inside = False
    for line in text.split("\n"):
        if not inside:
            inside = line.startswith("define ") and line.rstrip().endswith("{")
            output.append(line)
            continue
        if line == "}":
            inside = False
            output.append(line)
            continue
        if "@pcc_gc_" in line:
            if _is_drop(line):
                changed = True
                continue
            answer = _zero_answer(line)
            if answer is not None:
                output.append(answer)
                changed = True
                continue
        output.append(line)
    return ("\n".join(output), True) if changed else (text, False)
