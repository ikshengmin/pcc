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
    if (dtype is None or isinstance(dtype, TypeParameter) or getattr(dtype, 'cpp_class_pattern', False)
            or getattr(dtype, 'cpp_dependent_type', False)):
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
    if getattr(dtype, 'cpp_template_id_pattern', False):
        return ('template-id', dtype.symbol, tuple(type_key(a) for a in dtype.arguments))
    if isinstance(dtype, ValueParameter):
        return ('value-parameter', dtype.index, type_key(dtype.dtype))
    if isinstance(dtype, IntegralArgument):
        return ('integral-argument', dtype)
    return ("parameter", dtype.index) if isinstance(dtype, TypeParameter) else ("scalar", dtype)


def same_type(lhs, rhs):
    return type_key(lhs) == type_key(rhs)


def substitute(dtype, arguments, instantiate_class=None):
    from pcc.frontends.cpp.types import qualify_const
    if isinstance(dtype, ReferenceType):
        from pcc.frontends.cpp.references import lvalue_reference
        return lvalue_reference(substitute(dtype.referent, arguments, instantiate_class), None)
    if isinstance(dtype, ConstType):
        return qualify_const(substitute(dtype.base, arguments, instantiate_class))
    if isinstance(dtype, PointerType):
        pointee = substitute(dtype.pointee, arguments, instantiate_class)
        if isinstance(unqualified(pointee), ReferenceType):
            raise CppDiagnostic('cpp.invalid-reference-type', 'substitution cannot form a pointer to a reference', None)
        return PointerType(pointee)
    if isinstance(dtype, ArrayType):
        if isinstance(dtype.bound, ArrayBoundPattern):
            raise CppDiagnostic('cpp.unsupported.array-templates',
                                'dependent array bounds require class-template substitution', dtype.bound.coord)
        return ArrayType(substitute(dtype.element, arguments, instantiate_class), dtype.bound)
    if getattr(dtype, 'cpp_template_id_pattern', False):
        if instantiate_class is None:
            raise CppDiagnostic('cpp.unsupported.class-templates',
                                'dependent template-id substitution requires an instantiation context', dtype.coord)
        for arg in dtype.arguments:
            if not (isinstance(arg, (str, TypeParameter, ValueParameter, IntegralArgument,
                                     ConstType, PointerType, ReferenceType, ArrayType))
                    or getattr(arg, 'cpp_class_type', False)
                    or getattr(arg, 'cpp_template_id_pattern', False) or is_enum_type(arg)):
                raise CppDiagnostic('cpp.unsupported.non-type-template-deduction',
                                    'class template value expression substitution is not implemented', dtype.coord)
        converted = tuple(substitute(arg, arguments, instantiate_class) for arg in dtype.arguments)
        if any(arg is None or dependent(arg) for arg in converted):
            raise CppDiagnostic('cpp.unsupported.class-templates',
                                'class template arguments remain dependent after substitution', dtype.coord)
        return instantiate_class(dtype.symbol, converted, dtype.coord)
    if isinstance(dtype, (TypeParameter, ValueParameter)):
        return arguments[dtype.index]
    if isinstance(dtype, Pattern):
        raise CppDiagnostic('cpp.unsupported.non-type-template-deduction',
                            'dependent value expressions require expression substitution', dtype.coord)
    return dtype


def signature_key(function):
    parameters = tuple(("type",) if isinstance(p, TypeParameter)
                       else ("value", type_key(p.dtype)) for p in function.template_parameters)
    return (parameters, type_key(function.dtype),
            tuple(type_key(p) for p in function.parameters))


def _fully_explicit_pattern(dtype, explicit_count):
    """Whether an entire parameter becomes nondependent after the prefix.

    This check belongs at the function-parameter boundary, not recursively at
    individual template-id arguments: fixed arguments inside a dependent
    template-id must still match during structural deduction.
    """
    if isinstance(dtype, (TypeParameter, ValueParameter)):
        return dtype.index < explicit_count
    if isinstance(dtype, ReferenceType):
        return _fully_explicit_pattern(dtype.referent, explicit_count)
    if isinstance(dtype, ConstType):
        return _fully_explicit_pattern(dtype.base, explicit_count)
    if isinstance(dtype, PointerType):
        return _fully_explicit_pattern(dtype.pointee, explicit_count)
    if getattr(dtype, 'cpp_template_id_pattern', False):
        return all(_fully_explicit_pattern(arg, explicit_count) for arg in dtype.arguments)
    if isinstance(dtype, IntegralArgument):
        return True
    # Unknown dependent value expressions must not masquerade as fixed types.
    if (isinstance(dtype, (str, PointerType, ArrayType))
            or getattr(dtype, 'cpp_class_type', False) or is_enum_type(dtype)):
        return not dependent(dtype)
    return False


def _deduce_type_pattern(formal, actual, bindings, explicit_count):
    """Match one supported type pattern, not arbitrary value expressions.

    Integral bindings come only from concrete class-specialization arguments.
    A direct ValueParameter inside a TemplateIdPattern is deducible; arithmetic
    expressions, array bounds, derived-class matching and dependent qualifiers
    are deliberately not added here. The caller still owns conversions and the
    source expression's reference binding/value category.
    """
    if isinstance(formal, TypeParameter):
        if formal.index < explicit_count:
            return same_type(bindings[formal.index], actual)
        previous = bindings[formal.index]
        if previous is not None and not same_type(previous, actual):
            return False
        bindings[formal.index] = actual
        return True
    if isinstance(formal, ReferenceType):
        actual = actual.referent if isinstance(actual, ReferenceType) else actual
        return _deduce_type_pattern(formal.referent, actual, bindings, explicit_count)
    if isinstance(formal, ConstType):
        return _deduce_type_pattern(formal.base, unqualified(actual), bindings, explicit_count)
    if isinstance(formal, PointerType):
        actual = unqualified(actual)
        if not isinstance(actual, PointerType):
            return False
        return _deduce_type_pattern(formal.pointee, actual.pointee, bindings, explicit_count)
    if getattr(formal, 'cpp_template_id_pattern', False):
        actual = unqualified(actual)
        if getattr(actual, 'template_origin', None) is not formal.symbol:
            return False
        actual_arguments = actual.template_arguments
        if len(formal.arguments) != len(actual_arguments):
            return False
        for parameter, argument in zip(formal.arguments, actual_arguments):
            if isinstance(parameter, ValueParameter):
                if parameter.index < explicit_count:
                    if bindings[parameter.index] != argument:
                        return False
                    continue
                if not isinstance(argument, IntegralArgument):
                    return False
                destination = substitute(parameter.dtype, bindings)
                if dependent(destination):
                    raise CppDiagnostic('cpp.unsupported.non-type-template-deduction',
                                        'deduced integral parameter type must be independently known', parameter.coord)
                # Unlike explicitly supplied converted constant expressions,
                # deduction from a template-id requires the same value type.
                if not same_type(destination, argument.dtype):
                    return False
                previous = bindings[parameter.index]
                if previous is not None and previous != argument:
                    return False
                bindings[parameter.index] = argument
            elif isinstance(parameter, IntegralArgument):
                if parameter != argument:
                    return False
            elif isinstance(parameter, TypeParameter) or getattr(parameter, 'cpp_template_id_pattern', False):
                if not _deduce_type_pattern(parameter, argument, bindings, explicit_count):
                    return False
            elif isinstance(parameter, (str, ConstType, PointerType, ReferenceType)) or getattr(parameter, 'cpp_class_type', False):
                if dependent(parameter):
                    raise CppDiagnostic('cpp.unsupported.non-type-template-deduction',
                                        'this dependent class argument shape is not implemented', formal.coord)
                if not same_type(parameter, argument):
                    return False
            else:
                raise CppDiagnostic('cpp.unsupported.non-type-template-deduction',
                                    'only direct class template arguments can be deduced', formal.coord)
        return True
    if dependent(formal):
        raise CppDiagnostic('cpp.unsupported.non-type-template-deduction',
                            'this dependent parameter shape is not implemented', getattr(formal, 'coord', None))
    return same_type(formal, actual)


def deduce(function, arguments, explicit_arguments=None, types=None, *, unbound_is_nonviable=False):
    """Bind an explicit prefix, then deduce supported remaining type patterns.

    Values are inferred only from direct class-template argument positions,
    never runtime argument values or arithmetic expressions. Reference patterns
    preserve referent cv; the caller must supply the original argument type and
    separately enforce reference binding. Parameter ordinals survive renaming.
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
    # Once every template parameter is explicit, deduction is unnecessary.
    # The overload resolver checks substituted parameter conversions separately.
    if len(explicit) == len(function.template_parameters):
        return tuple(bindings)
    for parameter, argument in zip(function.parameters, arguments):
        if _fully_explicit_pattern(parameter, len(explicit)):
            continue
        # A by-value T discards top-level cv. A T& pattern does not.
        source = argument if isinstance(parameter, ReferenceType) else unqualified(argument)
        if not _deduce_type_pattern(parameter, source, bindings, len(explicit)):
            return None
    for parameter, binding in zip(function.template_parameters, bindings):
        if binding is None and isinstance(parameter, ValueParameter):
            if unbound_is_nonviable:
                # This parameter has neither a deduced nor explicit argument,
                # and there are no defaults in the admitted template syntax.
                return None
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
    """Namespace-owned function template ABI, sharing all substitution entries."""
    from pcc.frontends.cpp.types import _mangle_type
    from pcc.frontends.cpp.template_abi import mangle_template_type
    path = function.owner.namespace_path() if function.owner is not None else ()
    substitutions = [('namespace-prefix', path[:i + 1])
                     for i in range(len(path)) if path[:i + 1] != ('std',)]
    substitutions.append(('function-template-prefix', function))
    encoded_arguments = ''.join(mangle_argument(a) if isinstance(a, IntegralArgument)
                                else _mangle_type(a, substitutions) for a in arguments)
    name = str(len(function.name)) + function.name + 'I' + encoded_arguments + 'E'
    if path == ('std',):
        name = 'St' + name
    elif path:
        prefix, parts = ('St', path[1:]) if path[0] == 'std' else ('', path)
        name = 'N' + prefix + ''.join(str(len(p)) + p for p in parts) + name + 'E'
    signature = mangle_template_type(function.dtype, substitutions)
    signature += ''.join(mangle_template_type(p, substitutions) for p in function.parameters) or 'v'
    return '_Z' + name + signature
