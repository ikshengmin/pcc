"""Typed fixed-array bounds and explicit-brace initialization for C++17.

N4659 [dcl.array], [dcl.fct], [dcl.init.aggr], [conv.array], [expr.sub].
Bounds use C++ typed constant proofs, never C evaluation or source rewriting.
C array layout/emission remains the existing shared owner. Class-array lifetime,
unknown-bound deduction and brace elision are explicit remaining dependencies.
"""
from pcc.frontends.c.ast import c_ast
from pcc.frontends.c.c_abi_layout import c_size_type_name
from pcc.frontends.cpp.classes import is_class_type
from pcc.frontends.cpp.constants import KNOWN, NOT_CONSTANT
from pcc.frontends.cpp.diagnostics import CppDiagnostic
from pcc.frontends.cpp.initializers import ListInitializer, _lower_scalar
from pcc.frontends.cpp.types import ArrayType, ConstType, array_type, unqualified


def make_array_type(element, bound_expression, types, coord):
    if unqualified(element) == "void":
        raise CppDiagnostic("cpp.invalid-array-element", "an array cannot have void elements", coord)
    if is_class_type(element):
        raise CppDiagnostic("cpp.unsupported.class-array-lifetime", "class arrays require element lifetime semantics", coord)
    if array_type(element) and element.bound is None:
        raise CppDiagnostic("cpp.incomplete-array-element", "an array element cannot have unknown bound", coord)
    if bound_expression is None:
        return ArrayType(element, None)
    if not types.integral_operand(bound_expression.dtype):
        raise CppDiagnostic("cpp.invalid-array-bound", "array bound requires an integral converted constant expression", coord)
    fact = bound_expression.constant_fact
    if fact is None or fact.status not in (KNOWN, NOT_CONSTANT):
        raise CppDiagnostic("cpp.unsupported.array-bound-analysis", "array bound constant proof is unmodeled", coord)
    if fact.status == NOT_CONSTANT:
        raise CppDiagnostic("cpp.nonconstant-array-bound", "C++ does not permit a variable-length array", coord)
    size_type = c_size_type_name(types.target_triple)
    if not 0 < fact.value < (1 << types.width(size_type)):
        raise CppDiagnostic("cpp.invalid-array-bound", "array bound must be positive and representable in target size_t", coord)
    return ArrayType(element, fact.value)


def require_complete_array(dtype, coord):
    if not array_type(dtype):
        return
    if dtype.bound is None:
        raise CppDiagnostic("cpp.unsupported.array-bound-deduction", "unknown array bound inference is not implemented", coord)
    require_complete_array(dtype.element, coord)


def const_array_elements(dtype):
    while array_type(dtype):
        dtype = dtype.element
    return isinstance(dtype, ConstType)


def lower_array_initializer(dtype, initializer, types, convert):
    require_complete_array(dtype, initializer.coord)
    values = []
    for index, clause in enumerate(initializer.clauses):
        # A nested array without braces may consume several clauses, so it is
        # a capability gap rather than a premature excess-element diagnosis.
        if array_type(dtype.element):
            if not isinstance(clause, ListInitializer):
                raise CppDiagnostic("cpp.unsupported.array-brace-elision",
                                    "nested arrays require explicit braces at this boundary", clause.node.coord)
            value = lower_array_initializer(dtype.element, clause, types, convert)
        else:
            value = _lower_scalar(dtype.element, clause, types, convert, initializer.coord)
        if index >= dtype.bound:
            raise CppDiagnostic("cpp.too-many-initializers", "too many array initializer clauses", initializer.coord)
        values.append(value)
    # Do not manufacture N expression nodes: C's aggregate initializer owner
    # zero-initializes omitted elements, including empty nested arrays.
    return c_ast.InitList(values, initializer.coord)
