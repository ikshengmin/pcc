"""Typed dependent array-new syntax in class template members.

N4659 [expr.new]/1,5-7,18,20-21. Definition-point expressions and type
identities survive until member ODR-use. The first bound is not a fixed-array
constant bound: runtime integral counts and zero are permitted. Instantiation
re-checks the substituted element and count, then lowers through the owned
global allocation functions (heap_allocation.py). Cookies, element
construction and cleanup for class elements remain unowned.
"""

from pcc.frontends.c.c_abi_layout import c_size_type_name
from pcc.frontends.cpp.classes import is_class_type, require_complete
from pcc.frontends.cpp.constants import KNOWN, not_constant
from pcc.frontends.cpp.diagnostics import CppDiagnostic
from pcc.frontends.cpp.templates import Pattern, dependent, value_dependent
from pcc.frontends.cpp.types import (
    PointerType, ReferenceType, array_type, const_qualified, function_type,
    is_enum_type, unqualified,
)


def check_element(element, coord):
    if dependent(element):
        return
    if (isinstance(unqualified(element), ReferenceType)
            or unqualified(element) == 'void' or function_type(element)):
        raise CppDiagnostic('cpp.invalid-new-type', 'new[] requires a complete object element type', coord)
    leaf = element
    while array_type(leaf):
        if leaf.bound is None:
            raise CppDiagnostic('cpp.invalid-new-type', 'new[] requires a complete object element type', coord)
        leaf = leaf.element
    require_complete(leaf, coord)
    if is_class_type(leaf):
        raise CppDiagnostic('cpp.unsupported.class-array-lifetime',
                            'array-new class elements require constructor, destructor and failure cleanup semantics', coord)
    if is_enum_type(leaf) and not unqualified(leaf).complete:
        raise CppDiagnostic('cpp.incomplete-enum', 'new[] requires a complete enum element type', coord)
    if const_qualified(element):
        raise CppDiagnostic('cpp.const-initializer', 'new[] const scalar elements require an initializer', coord)


def check_count(parser, count, coord):
    if dependent(count.dtype):
        return
    if is_class_type(count.dtype):
        raise CppDiagnostic('cpp.unsupported.class-conversions',
                            'array-new class bounds require user-defined conversion semantics', coord)
    if not parser.types.integral_operand(count.dtype):
        size_type = c_size_type_name(parser.types.target_triple)
        if parser.types.conversion_rank(count.dtype, size_type, count.null_pointer) is None:
            raise CppDiagnostic('cpp.invalid-new-array-bound',
                                'an array-new bound must implicitly convert to target size_t', coord)
        raise CppDiagnostic('cpp.unsupported.array-new',
                            'array-new nonintegral converted bounds require additional conversion semantics', coord)
    fact = count.constant_fact
    if fact is not None and fact.status == KNOWN and fact.value < 0:
        raise CppDiagnostic('cpp.invalid-new-array-bound',
                            'a constant array-new bound cannot be negative', coord)
    # NOT_CONSTANT/UNMODELED are not VLA errors: first dimensions can be
    # runtime expressions. Their size/negative/failure checks require the heap
    # owner below. Zero must not inherit make_array_type's positive-bound rule.


def parse_array_new(parser, global_only, coord, element=None):
    from pcc.frontends.cpp.parser import Expression
    if parser.unevaluated_depth:
        raise CppDiagnostic('cpp.unsupported.array-new',
                            'unevaluated dependent array-new requires allocation type/lookup checking', coord)
    if element is None:
        element = parser._abstract_pointer_type(parser._type(check_lowering=False))
    if not parser._accept('['):
        raise CppDiagnostic('cpp.unsupported.allocation-templates',
                            'dependent non-array allocation is not implemented', coord)
    if parser._peek().value == ']':
        raise CppDiagnostic('cpp.unsupported.array-new', 'array-new bound inference is not implemented', coord)
    count = parser._expression()
    parser._require_value(count)
    parser._expect(']')
    if parser._peek().value in ('[', '(', '{'):
        raise CppDiagnostic('cpp.unsupported.array-new',
                            'multidimensional or initialized dependent array-new is not implemented', coord)
    check_element(element, coord)
    check_count(parser, count, coord)
    result = PointerType(element)
    return Expression(Pattern('new-array', coord, (global_only, element), [count]), result,
                      constant_fact=not_constant(result, 'C++17 new-expression', coord))


def instantiate_array_new(parser, pattern, bindings, environment):
    global_only, element = pattern.value
    element = parser._substitute_pattern_type(element, bindings, pattern.coord)
    check_element(element, pattern.coord)
    count = parser._instantiate_expression(pattern.children[0], bindings, environment)
    check_count(parser, count, pattern.coord)
    from pcc.frontends.cpp.heap_allocation import lower_new
    return lower_new(parser, element, count, 'default', [], global_only, pattern.coord)
