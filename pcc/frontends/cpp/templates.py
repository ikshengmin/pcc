"""Typed function-template patterns, independent of token/source replay.

Patterns retain definition-point bindings and dependent type identity. They are
not C AST nodes: only a successfully substituted and checked instance may enter
the existing C lowering path.
"""

from dataclasses import dataclass

from pcc.frontends.cpp.constants import KNOWN, NOT_CONSTANT, converted_fact
from pcc.frontends.cpp.diagnostics import CppDiagnostic
from pcc.frontends.cpp.types import (
    ArrayType, ConstType, PointerType, ReferenceType, _substitution,
    is_enum_type, scalar_mangling_code, unqualified,
)


class TypeParameter:
    def __init__(self, index, name, coord):
        self.index = index
        self.name = name
        self.coord = coord


class ValueParameter:
    """A declaration identity; index is in the complete mixed parameter list."""

    def __init__(self, index, name, dtype, coord):
        self.index = index
        self.name = name
        self.dtype = unqualified(dtype)
        self.coord = coord


@dataclass(frozen=True)
class IntegralArgument:
    """Converted target value identity, never an untyped Python-int cache key.

    The expression and its original tokens belong to TemplateArgument, not to
    equality: f<1+1> and f<2> denote the same specialization after conversion.
    """

    dtype: object
    value: int
    width: int
    unsigned: bool


class TemplateArgument:
    """Parsed type-id or typed expression, preserving original source tokens."""

    def __init__(self, kind, value, coord, tokens=()):
        self.kind = kind
        self.value = value
        self.coord = coord
        self.tokens = tuple(tokens)


class ArrayBoundPattern:
    """A typed definition-point expression, not a guessed concrete extent.

    Only the class-template owner currently substitutes these bounds. Array
    deduction and function-template array signatures remain separate gaps.
    """

    def __init__(self, expression):
        self.expression = expression
        self.coord = expression.node.coord


def value_dependent(expression):
    if dependent(expression.dtype):
        return True
    pattern = expression.node
    if not isinstance(pattern, Pattern):
        return False
    return (pattern.kind == "template-value"
            or pattern.kind == 'sizeof' and dependent(pattern.value)
            or any(value_dependent(child) for child in pattern.children))


def argument_dependent(argument):
    return (dependent(argument.value) if argument.kind == "type"
            else value_dependent(argument.value))


def convert_integral_argument(expression, destination, types, coord):
    """N4659 [temp.arg.nontype]/2, [expr.const]/4 converted constant expression.

    Only non-narrowing integral conversions are admitted. Ordinary assignment
    conversion (including modulo to unsigned or truth conversion to bool) is
    not enough. The existing typed ConstantFact proves evaluation eligibility.
    """
    destination = unqualified(destination)
    if is_enum_type(destination):
        raise CppDiagnostic("cpp.unsupported.enum-templates",
                            "enum non-type template parameters are not implemented", coord)
    if not types.integral(destination):
        raise CppDiagnostic("cpp.unsupported.non-type-template-parameters",
                            "only integral non-type template parameter types are implemented", coord)
    source = unqualified(expression.dtype)
    if not types.integral_operand(source):
        raise CppDiagnostic("cpp.invalid-template-argument",
                            "an integral template argument requires an integral converted constant expression", coord)
    fact = expression.constant_fact
    if fact is None or fact.status != KNOWN:
        code = ("cpp.nonconstant-template-argument" if fact is not None and fact.status == NOT_CONSTANT
                else "cpp.unsupported.template-constant-expression")
        reason = fact.reason if fact is not None else "constant-expression proof is unavailable"
        raise CppDiagnostic(code, reason, coord)
    # Validate the source fact on the active target before testing narrowing.
    checked = converted_fact(fact, source, types, coord)
    if checked.status != KNOWN:
        raise CppDiagnostic("cpp.unsupported.template-constant-expression", checked.reason, coord)
    width, unsigned = types.width(destination), types.unsigned(destination)
    minimum = 0 if unsigned else -(1 << (width - 1))
    maximum = (1 if destination == "bool" else
               (1 << width) - 1 if unsigned else (1 << (width - 1)) - 1)
    if not minimum <= checked.value <= maximum:
        raise CppDiagnostic("cpp.narrowing-template-argument",
                            "template argument is not representable in the parameter type", coord)
    converted = converted_fact(fact, destination, types, coord)
    if converted.status != KNOWN:
        raise CppDiagnostic("cpp.unsupported.template-constant-expression", converted.reason, coord)
    return IntegralArgument(destination, converted.value, width, unsigned)


class Pattern:
    def __init__(self, kind, coord, value=None, children=None):
        self.kind = kind
        self.coord = coord
        self.value = value
        self.children = children if children is not None else []


def dependent(dtype):
    if dtype is None or isinstance(dtype, TypeParameter) or getattr(dtype, 'cpp_class_pattern', False):
        return True
    if isinstance(dtype, ConstType):
        return dependent(dtype.base)
    if isinstance(dtype, PointerType):
        return dependent(dtype.pointee)
    if isinstance(dtype, ArrayType):
        return dependent(dtype.element) or isinstance(dtype.bound, ArrayBoundPattern)
    return isinstance(dtype, ReferenceType) and dependent(dtype.referent)


def type_key(dtype):
    if isinstance(dtype, ReferenceType):
        return ("lvalue-reference", type_key(dtype.referent))
    if isinstance(dtype, ConstType):
        return ("const", type_key(dtype.base))
    if isinstance(dtype, PointerType):
        return ("pointer", type_key(dtype.pointee))
    if isinstance(dtype, ArrayType):
        return ("array", type_key(dtype.element), dtype.bound)
    return ("parameter", dtype.index) if isinstance(dtype, TypeParameter) else ("scalar", dtype)


def same_type(lhs, rhs):
    return type_key(lhs) == type_key(rhs)


def substitute(dtype, arguments):
    from pcc.frontends.cpp.types import qualify_const
    if isinstance(dtype, ReferenceType):
        return ReferenceType(substitute(dtype.referent, arguments))
    if isinstance(dtype, ConstType):
        return qualify_const(substitute(dtype.base, arguments))
    if isinstance(dtype, PointerType):
        pointee = substitute(dtype.pointee, arguments)
        if isinstance(unqualified(pointee), ReferenceType):
            raise CppDiagnostic('cpp.invalid-reference-type', 'substitution cannot form a pointer to a reference', None)
        return PointerType(pointee)
    if isinstance(dtype, ArrayType):
        if isinstance(dtype.bound, ArrayBoundPattern):
            raise CppDiagnostic('cpp.unsupported.array-templates',
                                'dependent array bounds require class-template substitution', dtype.bound.coord)
        return ArrayType(substitute(dtype.element, arguments), dtype.bound)
    return arguments[dtype.index] if isinstance(dtype, TypeParameter) else dtype


def signature_key(function):
    parameters = tuple(("type",) if isinstance(p, TypeParameter)
                       else ("value", type_key(p.dtype)) for p in function.template_parameters)
    return (parameters, type_key(function.dtype),
            tuple(type_key(p) for p in function.parameters))


def deduce(function, arguments, explicit_arguments=None, types=None):
    """Bind an explicit prefix, then deduce only remaining by-value types.

    Values are never inferred from function arguments or non-deduced
    expressions. Parameter ordinals, not source spellings, survive redeclaration.
    """
    if len(function.parameters) != len(arguments):
        return None
    explicit = () if explicit_arguments is None else explicit_arguments
    if len(explicit) > len(function.template_parameters):
        raise CppDiagnostic("cpp.template-argument-count", "too many template arguments", explicit[-1].coord)
    bindings = [None] * len(function.template_parameters)
    for parameter, argument in zip(function.template_parameters, explicit):
        if isinstance(parameter, TypeParameter):
            if argument.kind != "type":
                raise CppDiagnostic("cpp.template-argument-kind", "template parameter requires a type argument", argument.coord)
            bindings[parameter.index] = argument.value
        else:
            if argument.kind != "value":
                raise CppDiagnostic("cpp.template-argument-kind", "template parameter requires a value argument", argument.coord)
            dtype = substitute(parameter.dtype, bindings)
            if dependent(dtype):
                raise CppDiagnostic("cpp.unsupported.non-type-template-deduction",
                                    "non-type parameter type requires an earlier explicit type argument", argument.coord)
            bindings[parameter.index] = convert_integral_argument(argument.value, dtype, types, argument.coord)
    for parameter, argument in zip(function.parameters, arguments):
        if not isinstance(parameter, TypeParameter) or parameter.index < len(explicit):
            continue
        previous = bindings[parameter.index]
        if previous is not None and previous != argument:
            return None
        bindings[parameter.index] = argument
    for parameter, binding in zip(function.template_parameters, bindings):
        if binding is None and isinstance(parameter, ValueParameter):
            raise CppDiagnostic("cpp.unsupported.non-type-template-deduction",
                                "non-type template arguments must be provided explicitly", parameter.coord)
    if any(binding is None for binding in bindings):
        return None
    return tuple(bindings)


def mangle_argument(argument):
    if isinstance(argument, IntegralArgument):
        value = ("n" + str(-argument.value) if argument.value < 0 else str(argument.value))
        return "L" + scalar_mangling_code(argument.dtype) + value + "E"
    return scalar_mangling_code(argument)


def mangle_instance(function, arguments):
    """Itanium 5.1.2/5.1.8: namespace-owned scalar function templates.

    Encode the declaring namespace, never a using-declaration's scope or a
    namespace alias spelling. Namespace prefixes enter the substitution table
    before the template prefix and dependent parameter types; ::std is the
    ABI's predefined substitution and does not consume a numbered entry.
    """
    path = function.owner.namespace_path() if function.owner is not None else ()
    name = str(len(function.name)) + function.name
    name += "I" + "".join(mangle_argument(t) for t in arguments) + "E"
    namespace_count = len(path)
    if path == ("std",):
        name = "St" + name
        namespace_count = 0
    elif path:
        prefix, parts = "", path
        if path[0] == "std":
            prefix, parts = "St", path[1:]
            namespace_count -= 1
        name = "N" + prefix + "".join(str(len(part)) + part for part in parts) + name + "E"
    # Scalar template arguments add no substitution entries. The template
    # prefix itself follows the namespace prefixes, before the first T_.
    substitutions = {}
    encoded = []
    for dtype in (function.dtype,) + function.parameters:
        if not isinstance(dtype, TypeParameter):
            encoded.append(scalar_mangling_code(dtype))
            continue
        if dtype.index in substitutions:
            encoded.append(_substitution(substitutions[dtype.index]))
        else:
            substitutions[dtype.index] = namespace_count + 1 + len(substitutions)
            encoded.append("T_" if dtype.index == 0 else "T" + str(dtype.index - 1) + "_")
    if not function.parameters:
        encoded.append("v")
    return "_Z" + name + "".join(encoded)
