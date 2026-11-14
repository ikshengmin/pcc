"""Java source conversion and overload rules, independent of bytecode.

Declarations admit boolean and all seven numeric primitive types. Narrow
values participate in Java numeric promotion and widening invocation.
The nonlowering class model also carries source-owned nominal references,
explicit unresolved bootstrap String identity and null conversion. No boxing,
inheritance hierarchy, managed representation or varargs is approximated.
"""
from dataclasses import dataclass, field


INTEGRAL = frozenset(("byte", "short", "char", "int", "long"))
FLOATING = frozenset(("float", "double"))
NUMERIC = INTEGRAL | FLOATING
DECLARED = NUMERIC | {"boolean"}
DESCRIPTORS = {"boolean": "Z", "byte": "B", "short": "S", "char": "C",
               "int": "I", "long": "J", "float": "F", "double": "D", "void": "V"}
ARRAY_COMPONENTS = INTEGRAL | {"boolean"}


@dataclass(frozen=True)
class NamedType:
    """Unresolved original type-name tokens, not a managed reference ABI.

    ``arguments`` holds parsed JLS 4.5.1 type arguments of a parameterized
    type: reference types or Wildcard bounds. No erasure, capture or
    parameterized-type identity is computed from them.
    """
    tokens: tuple
    arguments: tuple = ()
    # `new C<>(...)` (JLS 15.9): the diamond, whose arguments are inferred.
    diamond: bool = False

    def __str__(self):
        name = ".".join(token.text for token in self.tokens)
        if self.diamond:
            return name + "<>"
        if not self.arguments:
            return name
        return name + "<" + ",".join(str(argument) for argument in self.arguments) + ">"


@dataclass(frozen=True)
class Wildcard:
    """JLS 4.5.1 `?`, `? extends T` or `? super T`, retained unresolved."""
    token: object
    kind: str | None = None
    bound: object = None

    def __str__(self):
        return "?" if self.kind is None else "? " + self.kind + " " + str(self.bound)


@dataclass(frozen=True)
class ClassType:
    """Canonical supplied source-class identity; no size, address or GC layout."""
    class_name: str

    def __str__(self):
        return self.class_name.replace("/", ".")


@dataclass(frozen=True)
class BootstrapStringType:
    """Expected JLS identity with an unresolved owned definition, never a class."""
    class_name: str = field(default="java/lang/String", init=False)

    def __str__(self):
        return "java.lang.String (unresolved owned bootstrap)"


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
    if isinstance(dtype, (ClassType, BootstrapStringType)):
        return "L" + dtype.class_name + ";"
    return "[" + DESCRIPTORS[dtype.component] if isinstance(dtype, ArrayType) else DESCRIPTORS[dtype]


def array_reference(dtype):
    return isinstance(dtype, ArrayType) or dtype == "null"


def model_reference(dtype):
    return isinstance(dtype, (ArrayType, ClassType, BootstrapStringType)) or dtype == "null"


def widening(source, target):
    if source == target:
        return True
    if source == "null" and isinstance(target, (ArrayType, ClassType, BootstrapStringType)):
        return True
    if source in NUMERIC and target in FLOATING:
        return source != "double"
    return source in INTEGRAL and (target == "long" or target == "int" and source != "long"
                                   or source == "byte" and target == "short")


def promoted(left, right=None, *, shift=False):
    if not shift:
        for dtype in ("double", "float"):
            if dtype in (left, right):
                return dtype
    return "long" if left == "long" or not shift and right == "long" else "int"


def conditional_type(yes, no, token):
    """JLS 15.25 primitive standalone conditional typing, not target typing."""
    if "void" in (yes.dtype, no.dtype):
        token.error("conditional operands cannot have void type")
    if yes.dtype == no.dtype:
        return yes.dtype
    if yes.dtype == "null" and isinstance(no.dtype, (ArrayType, ClassType, BootstrapStringType)):
        return no.dtype
    if no.dtype == "null" and isinstance(yes.dtype, (ArrayType, ClassType, BootstrapStringType)):
        return yes.dtype
    if yes.dtype not in NUMERIC or no.dtype not in NUMERIC:
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
    """JLS 15.12 strict invocation, for fixed-arity admitted source methods."""
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
        domain = "source-reference" if any(isinstance(dtype, (ClassType, BootstrapStringType))
            for method in candidates for dtype in method.parameter_types) else "primitive"
        node.token.error("ambiguous " + domain + " method invocation " + node.value)
    return best[0]
