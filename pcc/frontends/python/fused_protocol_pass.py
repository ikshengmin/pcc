"""fused-protocol: drop calls the fused collector answers without work.

The fused collector is GC0 (reference counting with the cycle collector;
docs/status.md, "One collector"). It never moves an object and owes no
store barrier, so these calls, emitted for the relocating and tracing
collectors, return at once under it:

* pcc_gc_pin / pcc_gc_unpin: nothing reads the pin (pcc_gc_pins_inert);
* pcc_gc_foreign_lease_acquire / pcc_gc_borrowed_lease_acquire answer 0
  and pcc_gc_foreign_lease_release answers 0 for a 0 token;
* pcc_gc_note_slot_write_barrier / pcc_gc_note_write_barrier return;
* pcc_gc_load_ptr / pcc_gc_load_borrowed_ptr read their slot, or answer
  NULL for a NULL slot: the read barrier and the entry poll served the
  collectors that moved objects. Each becomes a load of the slot, through
  a module-level NULL word when the slot may be NULL (LOAD below);
* pcc_gc_resolve_owned_ptr answers its argument (RESOLVE below).

The backend's precise stack maps follow the frame-entry calls only, so they
lose nothing (pcc/backend/self_backend_precise_stackmaps.py).

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
    LOAD: (\\s*)(%NAME) = call ptr (ptr, ptr) @F(ptr VALUE, ptr SLOT) \\s*
          F = pcc_gc_load_ptr | pcc_gc_load_borrowed_ptr, SLOT a VALUE
          becomes ``\\1\\2 = load ptr, ptr SLOT`` for an @NAME slot, a load of
          the NULL word for a null slot, and for a %NAME slot
          ``\\1\\2.pcc.null = icmp eq ptr SLOT, null``,
          ``\\1\\2.pcc.slot = select i1 \\2.pcc.null, ptr @.pcc.gc.null.slot, ptr SLOT``,
          ``\\1\\2 = load ptr, ptr \\2.pcc.slot``.
    RESOLVE: (\\s*)(%NAME) = call ptr (ptr) @pcc_gc_resolve_owned_ptr(ptr VALUE) \\s*
          becomes ``\\1\\2 = select i1 1, ptr VALUE, ptr VALUE``.

A module that loads through the NULL word gets its definition,
``@.pcc.gc.null.slot = internal constant ptr null``, after its last line.

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


_LOAD_FUNCTIONS = ("pcc_gc_load_ptr", "pcc_gc_load_borrowed_ptr")
_RESOLVE_FUNCTIONS = ("pcc_gc_resolve_owned_ptr",)
NULL_SLOT = "@.pcc.gc.null.slot"
NULL_SLOT_DEFINITION = NULL_SLOT + " = internal constant ptr null"


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


def _assigned(line: str):
    """(indent, NAME end, index after ``%NAME = ``) of an assignment, or None."""
    indent_end = _skip_space(line, 0)
    if not line.startswith("%", indent_end):
        return None
    name_end = _name_end(line, indent_end + 1)
    if name_end < 0 or not line.startswith(" = ", name_end):
        return None
    return indent_end, name_end, name_end + 3


def _load_answer(line: str):
    """The replacement lines of a LOAD line, or None."""
    assigned = _assigned(line)
    if assigned is None:
        return None
    indent_end, name_end, index = assigned
    index = _call_arguments(line, index, "ptr", ("ptr, ptr",), _LOAD_FUNCTIONS)
    if index < 0 or not line.startswith("ptr ", index):
        return None
    index = _value_end(line, index + 4)
    if index < 0 or not line.startswith(", ptr ", index):
        return None
    slot_at = index + 6
    slot_end = _value_end(line, slot_at)
    if slot_end < 0 or not line.startswith(")", slot_end) or not _rest_is_space(line, slot_end + 1):
        return None
    indent = line[:indent_end]
    name = line[indent_end:name_end]
    slot = line[slot_at:slot_end]
    if slot == "null":
        return [indent + name + " = load ptr, ptr " + NULL_SLOT]
    if slot.startswith("@"):
        return [indent + name + " = load ptr, ptr " + slot]
    return [
        indent + name + ".pcc.null = icmp eq ptr " + slot + ", null",
        indent + name + ".pcc.slot = select i1 " + name + ".pcc.null, ptr "
        + NULL_SLOT + ", ptr " + slot,
        indent + name + " = load ptr, ptr " + name + ".pcc.slot",
    ]


def _resolve_answer(line: str):
    """The replacement line of a RESOLVE line, or None."""
    assigned = _assigned(line)
    if assigned is None:
        return None
    indent_end, name_end, index = assigned
    index = _call_arguments(line, index, "ptr", ("ptr",), _RESOLVE_FUNCTIONS)
    if index < 0 or not line.startswith("ptr ", index):
        return None
    value_end = _value_end(line, index + 4)
    if value_end < 0 or not line.startswith(")", value_end) or not _rest_is_space(line, value_end + 1):
        return None
    value = line[index + 4:value_end]
    return line[:name_end] + " = select i1 1, ptr " + value + ", ptr " + value


def fused_protocol_text(text: str) -> tuple[str, bool]:
    """Apply the pass to one module's IR text; returns (text, changed)."""
    if "@pcc_gc_" not in text:
        return text, False
    output = []
    changed = False
    null_slot = False
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
            loads = _load_answer(line)
            if loads is not None:
                output.extend(loads)
                if len(loads) > 1 or NULL_SLOT in loads[0]:
                    null_slot = True
                changed = True
                continue
            resolved = _resolve_answer(line)
            if resolved is not None:
                output.append(resolved)
                changed = True
                continue
        output.append(line)
    if not changed:
        return text, False
    if null_slot and (NULL_SLOT + " = ") not in text:
        if output and output[-1] == "":
            output.pop()
            output.append(NULL_SLOT_DEFINITION)
            output.append("")
        else:
            output.append(NULL_SLOT_DEFINITION)
    return "\n".join(output), True
