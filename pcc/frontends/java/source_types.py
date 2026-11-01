"""Java scalar conversion and overload rules, independent of bytecode.

Declarations admit boolean and the five integral primitive types. Narrow
values participate in Java numeric promotion and widening invocation.
No boxing, references, floating point or varargs are approximated here.
"""
from dataclasses import dataclass


INTEGRAL = frozenset(("byte", "short", "char", "int", "long"))
DECLARED = INTEGRAL | {"boolean"}
DESCRIPTORS = {"boolean": "Z", "byte": "B", "short": "S", "char": "C",
               "int": "I", "long": "J", "void": "V"}
ARRAY_COMPONENTS = INTEGRAL | {"boolean"}


@dataclass(frozen=True)
class ArrayType:
    """One-dimensional primitive reference type, not an allocation/layout ABI."""
    component: str

    def __post_init__(self):
        if self.component not in ARRAY_COMPONENTS:
            raise ValueError("unsupported semantic array component")

    def __str__(self):
        return self.component + "[]"


def descriptor(dtype):
    return "[" + DESCRIPTORS[dtype.component] if isinstance(dtype, ArrayType) else DESCRIPTORS[dtype]


def array_reference(dtype):
    return isinstance(dtype, ArrayType) or dtype == "null"


def widening(source, target):
    if source == target:
        return True
    if source == "null" and isinstance(target, ArrayType):
        return True
    return source in INTEGRAL and (target == "long" or target == "int" and source != "long"
                                   or source == "byte" and target == "short")


def promoted(left, right=None, *, shift=False):
    return "long" if left == "long" or not shift and right == "long" else "int"


def conditional_type(yes, no, token):
    """JLS 15.25 primitive standalone conditional typing, not target typing."""
    if "void" in (yes.dtype, no.dtype):
        token.error("conditional operands cannot have void type")
    if yes.dtype == no.dtype:
        return yes.dtype
    if yes.dtype == "null" and isinstance(no.dtype, ArrayType):
        return no.dtype
    if no.dtype == "null" and isinstance(yes.dtype, ArrayType):
        return yes.dtype
    if yes.dtype not in INTEGRAL or no.dtype not in INTEGRAL:
        token.error("reference/boxing conditional operands require the shared Java runtime", code="java.unsupported.reference-conditional")
    if {yes.dtype, no.dtype} == {"byte", "short"}:
        return "short"
    bounds = {"byte": (-128, 127), "short": (-32768, 32767), "char": (0, 65535)}
    for narrow, other in ((yes, no), (no, yes)):
        if (narrow.dtype in bounds and other.dtype == "int" and other.constant is not None
                and bounds[narrow.dtype][0] <= other.constant <= bounds[narrow.dtype][1]):
            return narrow.dtype
    return promoted(yes.dtype, no.dtype)


def select_method(methods, node):
    """JLS 15.12 strict invocation, for fixed-arity primitive-only methods."""
    name = node.value.rsplit(".", 1)[-1]
    actual = tuple(argument.dtype for argument in node.children)
    candidates = [method for method in methods.values()
                  if method.token.text == name and len(method.parameter_types) == len(actual)
                  and all(widening(source, target) for source, target in zip(actual, method.parameter_types))]
    if not candidates:
        return None
    best = [method for method in candidates if all(
        all(widening(source, target) for source, target in zip(method.parameter_types, other.parameter_types))
        for other in candidates)]
    if len(best) != 1:
        node.token.error("ambiguous primitive method invocation " + node.value)
    return best[0]
