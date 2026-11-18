from __future__ import annotations

"""Give ``alloca T, iN count`` its element count before the target parser.

The self-backend parser models every ``alloca`` as one fixed frame slot of
type ``T`` and used to drop the count operand, so a C variable-length array
(``double v[n]``) or ``__builtin_alloca(n)`` got room for a single element
and its stores ran over the saved frame record.  This rewrite runs on IR text
first:

* a constant count becomes an array allocation: ``alloca [N x T]``;
* Linux x86-64 keeps the unsigned count and target element stride separate
  until the checked machine multiplication; other supported targets use the
  existing byte-count intrinsic below;
* a runtime count on those other targets becomes ``call ptr @llvm.pcc.dynamic.alloca(i64 bytes,
  i64 align)``, with ``bytes`` computed as ``n * sizeof(T)`` through the
  ``getelementptr T, ptr null, iN n`` idiom so named types need no layout
  table here.  Targets lower the call by moving the stack pointer and restore
  it from the frame pointer in every epilogue, which also releases the
  storage at function exit like LLVM's dynamic ``alloca``.
"""

from . import BackendUnavailable
from .self_backend_wide_int import (
    _is_int_literal,
    _split_leading_type,
    _split_top_level,
    _symbol_end,
)


DYNAMIC_ALLOCA_INTRINSIC = "llvm.pcc.dynamic.alloca"
X86_DYNAMIC_ARRAY = "llvm.pcc.x86.dynamic.array"
_X86_DYNAMIC_ARRAY_DECL = "declare ptr @" + X86_DYNAMIC_ARRAY + "(i64, i64, i64)"
_DYNAMIC_ALLOCA_DECLARATION = "declare ptr @" + DYNAMIC_ALLOCA_INTRINSIC + "(i64, i64)"


def legalize_dynamic_allocas(ir_text: str, checked_sysv_x86: bool = False) -> str:
    """Return ``ir_text`` with every counted ``alloca`` made explicit."""
    if not _has_counted_alloca(ir_text, checked_sysv_x86):
        return ir_text
    marker = ".pccdyn"
    serial = 0
    while ir_text.find(marker) >= 0:
        serial += 1
        marker = ".pccdyn" + str(serial)
    lines = ir_text.split("\n")
    proof_ids = {}
    if checked_sysv_x86:
        for line in lines:
            text = line.strip()
            if text.startswith("!") and "=" in text:
                number, value = text.split("=", 1)
                number = number.strip()
                if number.startswith("!") and number[1:].isdigit():
                    # Duplicate/unknown metadata cannot authorize an alloca.
                    proof_ids[number] = (number not in proof_ids and value.strip() in (
                        '!{ !"pcc.alloca.function_lifetime.v1" }',
                        '!{!"pcc.alloca.function_lifetime.v1"}',
                    ))
    out: list[str] = []
    changed = False
    dynamic = False
    for line in lines:
        rewritten = _rewrite_alloca_line(line, marker, checked_sysv_x86, proof_ids)
        if not rewritten:
            out.append(line)
            continue
        changed = True
        if len(rewritten) > 1:
            dynamic = True
        out.extend(rewritten)
    if not changed:
        return ir_text
    intrinsic = X86_DYNAMIC_ARRAY if checked_sysv_x86 else DYNAMIC_ALLOCA_INTRINSIC
    if dynamic and ir_text.find("@" + intrinsic + "(") < 0:
        out.append(_X86_DYNAMIC_ARRAY_DECL if checked_sysv_x86 else _DYNAMIC_ALLOCA_DECLARATION)
    return "\n".join(out)


def sysv_dynamic_layout_supported(ir_text):
    # Keep one module fact, not a second spelling-based call parser. Evaluate
    # it on actual decoded calls, including quoted globals. Do not split a
    # large module into lines for a usually absent/single layout directive.
    pos = ir_text.find("datalayout")
    while pos >= 0:
        start = ir_text.rfind("\n", 0, pos) + 1
        end = ir_text.find("\n", pos)
        if end < 0:
            end = len(ir_text)
        text = ir_text[start:end].split(";", 1)[0].strip()
        words = text.split("=", 1)
        if words[0].strip().split() == ["target", "datalayout"]:
            if len(words) != 2 or words[1].strip() not in (
                '""', '"e-m:e-p:64:64-i64:64-n8:16:32:64-S128"',
            ):
                return False
        pos = ir_text.find("datalayout", end)
    return True


def _has_counted_alloca(ir_text: str, checked_sysv_x86: bool = False) -> bool:
    """Whether any alloca carries a count, checking only alloca lines."""
    needle = "alloca" if checked_sysv_x86 else "= alloca "
    index = ir_text.find(needle)
    while index >= 0:
        line_start = ir_text.rfind("\n", 0, index) + 1
        line_end = ir_text.find("\n", index)
        if line_end < 0:
            line_end = len(ir_text)
        if _rewrite_alloca_line(ir_text[line_start:line_end], ".pccdyn", checked_sysv_x86):
            return True
        index = ir_text.find(needle, line_end)
    return False


def _rewrite_alloca_line(line: str, marker: str, checked_sysv_x86: bool = False, proof_ids=None) -> list[str]:
    """Replacement lines for a counted alloca, or ``[]`` to keep ``line``."""
    stripped = line.strip()
    if not stripped.startswith("%"):
        return []
    name_end = _symbol_end(stripped, 0)
    if checked_sysv_x86:
        rhs = stripped[name_end:].lstrip()
        if not rhs.startswith("="):
            return []
        rhs = rhs[1:].lstrip()
        if not rhs.startswith("alloca") or len(rhs) <= 6 or not rhs[6].isspace():
            return []
        rest = rhs[6:].lstrip()
    else:
        if not stripped.startswith(" = alloca ", name_end):
            return []
        rest = stripped[name_end + len(" = alloca "):].lstrip()
    result = stripped[:name_end]
    if checked_sysv_x86 and (rest.startswith(("inalloca ", "swifterror ")) or "addrspace(" in rest):
        parts = _split_top_level(rest)
        if len(parts) > 1:
            ty, value = _split_leading_type(parts[1])
            if ty.startswith("i") and value and not _is_int_literal(value):
                raise BackendUnavailable("x86_64 dynamic alloca requires plain storage in the default address space")
    if rest.startswith("inalloca "):
        return []
    element_type, rest = _split_leading_type(rest)
    if not rest.startswith(","):
        return []
    items = _split_top_level(rest[1:])
    if not items or items[0].startswith("align ") or items[0].startswith("addrspace("):
        return []
    count_type, count = _split_leading_type(items[0])
    if not count_type.startswith("i") or not count:
        return []
    tail = ""
    align = 16
    for item in items[1:]:
        tail = tail + ", " + item
        if item.startswith("align ") and _is_int_literal(item[len("align "):].strip()):
            align = int(item[len("align "):].strip())
    indent = line[:len(line) - len(line.lstrip())]
    if _is_int_literal(count):
        elements = int(count)
        if elements < 1:
            elements = 1
        return [indent + result + " = alloca [" + str(elements) + " x " + element_type + "]" + tail]
    if checked_sysv_x86:
        if ";" in line:
            raise BackendUnavailable("x86_64 dynamic alloca cannot take a lifetime proof from commented text")
        if proof_ids is None:  # presence scan; actual lowering always has a table
            return [line]
        return _sysv_counted_array_line(indent, result, marker, element_type, count_type, count,
                                        items[1:], proof_ids)
    end = _derived(result, marker + "end")
    size = _derived(result, marker + "bytes")
    if align < 16:
        align = 16
    return [
        indent + end + " = getelementptr " + element_type + ", ptr null, " + count_type + " " + count,
        indent + size + " = ptrtoint ptr " + end + " to i64",
        indent + result + " = call ptr @" + DYNAMIC_ALLOCA_INTRINSIC
        + "(i64 " + size + ", i64 " + str(align) + ")",
    ]


def _sysv_counted_array_line(indent, result, marker, element_type, count_type, count, options, proof_ids):
    # The old byte-only intrinsic used a GEP of a variable count. Keep the
    # unsigned count separate from the target's element allocation stride so
    # the machine multiply can reject wrap *before* changing the stack.
    if count_type not in ("i1", "i8", "i16", "i32", "i64"):
        raise BackendUnavailable("x86_64 dynamic alloca requires a supported unsigned count width")
    # Unmarked or stripped IR might be a C block-scoped VLA, for which
    # the frontend does not yet emit every required stackrestore. Retain
    # the rejection rather than inferring function lifetime from absence.
    proof = None
    plain = []
    prefix = "!pcc.alloca.function_lifetime "
    for option in options:
        if option.startswith(prefix):
            if proof is not None:
                raise BackendUnavailable("x86_64 dynamic alloca has duplicate function-lifetime proof")
            proof = option[len(prefix):]
        else:
            plain.append(option)
    if not proof_ids.get(proof, False):
        raise BackendUnavailable("x86_64 dynamic alloca requires a verified function-lifetime proof")
    options = plain
    align = 16
    if options:
        if len(options) != 1 or not options[0].startswith("align "):
            raise BackendUnavailable("x86_64 dynamic alloca requires default address space and plain alignment")
        raw = options[0][len("align "):].strip()
        if not raw.isdigit():
            raise BackendUnavailable("x86_64 dynamic alloca requires a positive constant alignment")
        align = int(raw)
        if align <= 0 or align > 4294967296 or align & (align - 1):
            raise BackendUnavailable("x86_64 dynamic alloca requires a valid power-of-two alignment")
    elif element_type not in ("i1", "i8", "i16", "i32", "i64", "ptr", "float", "double"):
        # Without an explicit alignment a vector/aggregate can require more
        # than the ordinary stack alignment. Do not guess its ABI alignment.
        raise BackendUnavailable("x86_64 dynamic alloca needs explicit alignment for this element type")
    end = _derived(result, marker + "end")
    stride = _derived(result, marker + "stride")
    lines = [indent + end + " = getelementptr " + element_type + ", ptr null, i64 1",
             indent + stride + " = ptrtoint ptr " + end + " to i64"]
    if count_type != "i64":
        unsigned = _derived(result, marker + "count")
        lines.append(indent + unsigned + " = zext " + count_type + " " + count + " to i64")
        count = unsigned
    lines.append(indent + result + " = call ptr @" + X86_DYNAMIC_ARRAY + "(i64 " + count
                 + ", i64 " + stride + ", i64 " + str(max(align, 16)) + ")")
    return lines


def _derived(name: str, suffix: str) -> str:
    if name.startswith('%"'):
        return name[:-1] + suffix + '"'
    if _is_int_literal(name[1:]):
        return '%"' + name[1:] + suffix + '"'
    return name + suffix
