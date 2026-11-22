"""Source proofs for call-bounded byte growth, without slice lowering."""
from dataclasses import dataclass, replace

from pcc.frontends.go.managed_objects import FixedByteExtent
from pcc.frontends.go.syntax import SliceSyntax


_U32_SHIFT_COUNTS = frozenset(str(number) for number in range(32))


@dataclass(frozen=True)
class ByteAppendSummary:
    shifts: tuple[int, ...]

    @property
    def bytes_per_word(self):
        return len(self.shifts)


def _name(expr, value):
    return expr.kind == "name" and expr.value == value


def _slice_of(dtype, element):
    return isinstance(dtype, SliceSyntax) and dtype.element == element


def _builtin_call(expr, name):
    return expr.kind == "call" and expr.callee is not None and _name(expr.callee, name) and expr.spread is None


def byte_append_summary(program, function_name):
    """Recognize a closed scalar loop returning only its updated input span.

    This deliberately fails closed on indirect calls, additional statements,
    shadowing, captured state and arbitrary expressions. It establishes the
    length delta and absence of retention outside the returned result, not
    that an arbitrary caller has proven capacity, roots or nonescape.
    """
    if any(imported.alias == "." for imported in program.imports):
        return None
    shadowed = {item.token.text for item in (*program.types, *program.functions)}
    shadowed.update(token.text for stmt in (*program.constants, *program.variables) for token in stmt.names)
    if shadowed & {"append", "byte", "uint32"}:
        return None
    functions = [item for item in program.functions if item.token.text == function_name and item.receiver is None]
    if len(functions) != 1:
        return None
    fn = functions[0]
    if (not fn.variadic or fn.type_parameters or len(fn.params) != 2 or len(fn.body) != 2
            or not _slice_of(fn.result, "uint8")):
        return None
    (dst, dst_type), (words, words_type) = fn.params
    if not _slice_of(dst_type, "uint8") or not _slice_of(words_type, "uint32"):
        return None
    if {dst.text, words.text} & {"append", "byte", "_"} or dst.text == words.text:
        return None
    loop, returned = fn.body
    if (loop.kind != "range" or not loop.range_declaration or len(loop.targets) != 2
            or not _name(loop.targets[0], "_") or loop.targets[1].kind != "name"
            or len(loop.body) != 1 or len(loop.exprs) != 1 or not _name(loop.exprs[0], words.text)):
        return None
    word = loop.targets[1].value
    if word in {dst.text, words.text, "append", "byte", "_"}:
        return None
    step = loop.body[0]
    if (step.kind != "=" or len(step.targets) != 1 or not _name(step.targets[0], dst.text)
            or len(step.exprs) != 1 or not _builtin_call(step.exprs[0], "append")
            or returned.kind != "return" or len(returned.exprs) != 1 or not _name(returned.exprs[0], dst.text)):
        return None
    arguments = step.exprs[0].args
    if len(arguments) < 2 or not _name(arguments[0], dst.text):
        return None
    shifts = []
    for arg in arguments[1:]:
        if not _builtin_call(arg, "byte") or len(arg.args) != 1:
            return None
        value = arg.args[0]
        if _name(value, word):
            shifts.append(0)
            continue
        if (value.kind != "binary" or value.value != ">>" or not _name(value.args[0], word)
                or value.args[1].kind != "integer"):
            return None
        text = value.args[1].value
        if text not in _U32_SHIFT_COUNTS:
            return None
        shifts.append(int(text))
    return ByteAppendSummary(tuple(shifts))


def append_within_extent(extent, summary, words):
    """Transfer only the same backing; reallocation is never proven here."""
    if (not isinstance(extent, FixedByteExtent) or not isinstance(summary, ByteAppendSummary)
            or type(words) is not int or words < 0 or not 0 <= extent.length <= extent.capacity
            or summary.bytes_per_word == 0
            or words > (extent.capacity - extent.length) // summary.bytes_per_word):
        raise ValueError("byte append growth exceeds the known capacity or shape")
    return replace(extent, length=extent.length + words * summary.bytes_per_word)
