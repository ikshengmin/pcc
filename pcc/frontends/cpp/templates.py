"""Typed function-template patterns, independent of token/source replay.

Patterns retain definition-point bindings and dependent type identity. They are
not C AST nodes: only a successfully substituted and checked instance may enter
the existing C lowering path.
"""

from pcc.frontends.cpp.types import scalar_mangling_code


class TypeParameter:
    def __init__(self, index, name, coord):
        self.index = index
        self.name = name
        self.coord = coord


class Pattern:
    def __init__(self, kind, coord, value=None, children=None):
        self.kind = kind
        self.coord = coord
        self.value = value
        self.children = children if children is not None else []


def dependent(dtype):
    return dtype is None or isinstance(dtype, TypeParameter)


def type_key(dtype):
    return ("parameter", dtype.index) if isinstance(dtype, TypeParameter) else ("scalar", dtype)


def same_type(lhs, rhs):
    return type_key(lhs) == type_key(rhs)


def substitute(dtype, arguments):
    return arguments[dtype.index] if isinstance(dtype, TypeParameter) else dtype


def signature_key(function):
    return (len(function.template_parameters), type_key(function.dtype),
            tuple(type_key(p) for p in function.parameters))


def deduce(function, arguments):
    """Deduce each by-value type parameter consistently; no common-type guess."""
    if len(function.parameters) != len(arguments):
        return None
    bindings = [None] * len(function.template_parameters)
    for parameter, argument in zip(function.parameters, arguments):
        if not isinstance(parameter, TypeParameter):
            continue
        previous = bindings[parameter.index]
        if previous is not None and previous != argument:
            return None
        bindings[parameter.index] = argument
    if any(binding is None for binding in bindings):
        return None
    return tuple(bindings)


def mangle_instance(function, arguments):
    """Itanium encoding for unqualified by-value arithmetic type templates."""
    prefix = "_Z" + str(len(function.name)) + function.name
    prefix += "I" + "".join(scalar_mangling_code(t) for t in arguments) + "E"
    # The template prefix occupies substitution slot zero. Template parameter
    # references are separate candidates from their actual scalar arguments.
    substitutions = {}
    encoded = []
    for dtype in (function.dtype,) + function.parameters:
        if not isinstance(dtype, TypeParameter):
            encoded.append(scalar_mangling_code(dtype))
            continue
        if dtype.index in substitutions:
            index = substitutions[dtype.index]
            digits = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
            number = ""
            while True:
                number = digits[index % 36] + number
                index //= 36
                if index == 0:
                    break
            encoded.append("S" + number + "_")
        else:
            substitutions[dtype.index] = len(substitutions)
            encoded.append("T_" if dtype.index == 0 else "T" + str(dtype.index - 1) + "_")
    if not function.parameters:
        encoded.append("v")
    return prefix + "".join(encoded)
