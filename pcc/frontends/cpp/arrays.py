"""Typed fixed-array bounds and explicit-brace initialization for C++17.

N4659 [dcl.array], [dcl.fct], [dcl.init.aggr], [conv.array], [expr.sub].
Bounds use C++ typed constant proofs, never C evaluation or source rewriting.
C array layout/emission remains the existing shared owner. Class-array lifetime,
unknown-bound deduction and brace elision are explicit remaining dependencies.
"""
from pcc.frontends.c.ast import c_ast
from pcc.frontends.c.c_abi_layout import c_size_type_name
from pcc.frontends.cpp.classes import is_class_type, require_complete
from pcc.frontends.cpp.constants import KNOWN, NOT_CONSTANT
from pcc.frontends.cpp.diagnostics import CppDiagnostic
from pcc.frontends.cpp.initializers import ListInitializer, _lower_scalar
from pcc.frontends.cpp.types import ArrayType, ConstType, ReferenceType, array_type, function_type, unqualified


def check_array_element(element, coord, allow_class_element=False):
    """Share element legality at definition time and after substitution."""
    if isinstance(element, ReferenceType):
        raise CppDiagnostic("cpp.invalid-array-element", "an array cannot have reference elements", coord)
    if unqualified(element) == "void" or function_type(element):
        raise CppDiagnostic("cpp.invalid-array-element", "an array cannot have void or function elements", coord)
    if is_class_type(element):
        if not allow_class_element:
            raise CppDiagnostic("cpp.unsupported.class-array-lifetime", "class arrays require element lifetime semantics", coord)
        require_complete(element, coord)
    if array_type(element) and element.bound is None:
        raise CppDiagnostic("cpp.incomplete-array-element", "an array element cannot have unknown bound", coord)


def make_array_type(element, bound_expression, types, coord, allow_class_element=False):
    check_array_element(element, coord, allow_class_element)
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


def require_trivial_member_array(dtype, coord):
    """Admit complete scalar/pointer elements, never a class-element lifetime.

    Declaration, construction and destruction share this predicate. Completeness
    and the existing typed bound owner remain prerequisites to C projection.
    """
    if dtype.bound is None:
        raise CppDiagnostic('cpp.invalid-array-bound', 'an array data member requires a bound', coord)
    require_complete_array(dtype, coord)
    leaf = dtype
    while array_type(leaf):
        leaf = leaf.element
    if is_class_type(leaf):
        raise CppDiagnostic('cpp.unsupported.class-array-members',
                            'class array members require element special-member and lifetime semantics', coord)
    from pcc.frontends.cpp.types import is_enum_type
    if is_enum_type(leaf) and not unqualified(leaf).complete:
        raise CppDiagnostic('cpp.incomplete-enum', 'an array member requires a complete enum element', coord)
    return leaf


def zero_array_initializer(dtype, coord):
    """An explicit array subaggregate preserves all nested zero-init boundaries."""
    require_trivial_member_array(dtype, coord)
    return c_ast.InitList([], coord)


def zero_array_member(parser, dtype, field, coord):
    """Value-initialize each scalar element without array assignment or runtime.

    Omitted member initialization does not call this owner. Bounded loops keep
    code size independent of the extent and reuse C's typed element addresses.
    The field is the constructor's pure, already-bound this/member projection.
    """
    require_trivial_member_array(dtype, coord)
    name = parser._fresh_projection_name('__pcc_cpp_array_zero_')
    index = c_ast.ID(name, coord)
    size_type = c_size_type_name(parser.types.target_triple)
    declaration = parser._decl(name, size_type, coord, c_ast.Constant('int', '0', coord))
    element = c_ast.ArrayRef(field, index, coord)
    if array_type(dtype.element):
        body = zero_array_member(parser, dtype.element, element, coord)
    else:
        # As with scalar member (), the shared C assignment owner converts
        # zero to the declared scalar storage type, including null pointers.
        body = c_ast.Assignment('=', element, c_ast.Constant('int', '0', coord), coord)
    condition = c_ast.BinaryOp('<', c_ast.ID(name, coord),
                               parser._sizeof_result(dtype.bound, coord).node, coord)
    loop = c_ast.For(None, condition, c_ast.UnaryOp('p++', c_ast.ID(name, coord), coord), body, coord)
    # Match the ordinary frontend's for-header scope projection. The C For
    # owner scopes its body, so the index belongs to a real enclosing compound.
    return c_ast.Compound([declaration, loop], coord)


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


def require_trivial_record_array(dtype, coord):
    """No code is needed for a trivially default-initialized/destroyed array.

    This is a generic local storage capability, not a va_list type exception.
    Nontrivial/const/nested subobjects and explicit initialization remain gated.
    """
    require_complete_array(dtype, coord)
    element = dtype
    while array_type(element):
        element = element.element
    require_complete(element, coord)
    owner = unqualified(element)
    definition = owner.definition
    if (isinstance(element, ConstType) or definition.constructors.overloads
            or definition.destructor is not None
            or any(is_class_type(member.dtype)
                   or getattr(member, 'default_initializer_tokens', None) is not None
                   for member in definition.members)):
        raise CppDiagnostic('cpp.unsupported.class-array-lifetime',
                            'record arrays require trivial default initialization and destruction', coord)
    from pcc.frontends.cpp.classes import default_initialize
    default_initialize(element, coord)
