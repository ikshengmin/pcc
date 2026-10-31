"""Java scalar conversion and overload rules, independent of bytecode.

Declarations currently admit boolean/int/long. byte/short/char cast values
participate in Java unary/binary numeric promotion and widening invocation.
No boxing, references, floating point or varargs are approximated here.
"""
INTEGRAL = frozenset(("byte", "short", "char", "int", "long"))
DECLARED = frozenset(("boolean", "int", "long"))
DESCRIPTORS = {"boolean": "Z", "int": "I", "long": "J", "void": "V"}


def widening(source, target):
    if source == target:
        return True
    return source in INTEGRAL and (target == "long" or target == "int" and source != "long")


def promoted(left, right=None, *, shift=False):
    return "long" if left == "long" or not shift and right == "long" else "int"


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
