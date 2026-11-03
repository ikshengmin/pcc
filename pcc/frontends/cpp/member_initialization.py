"""Declared C1 construction with bounded, source-proven member lifetimes.

N4659 [class.base.init]/7,9,13: initialize the actual member subobject in
declaration order, before the enclosing constructor body. This is neither
aggregate assignment nor synthesis of an implicit special member. Class-element
arrays, implicit special members and unknown construction effects remain gaps.
Members with declared D1s require the enclosing constructor's closed-path proof.
"""

from pcc.frontends.c.ast import c_ast
from pcc.frontends.cpp.classes import is_class_type
from pcc.frontends.cpp.diagnostics import CppDiagnostic
from pcc.frontends.cpp.types import array_type, unqualified


class MemberArguments:
    """Typed original parenthesized arguments, not an aggregate value."""

    def __init__(self, expressions):
        self.expressions = tuple(expressions)


def requires_destruction(dtype):
    """No emitted member D1 is needed only if the complete tree is trivial."""
    if array_type(dtype):
        # Scalar/pointer arrays have no element D1. Retain the conservative
        # boundary for class-element arrays even if one reaches this query.
        while array_type(dtype):
            dtype = dtype.element
        return is_class_type(dtype)
    if not is_class_type(dtype):
        return False
    definition = unqualified(dtype).definition
    return (definition.destructor is not None
            and not getattr(definition.destructor, 'destructor_trivial', False)) or any(
        requires_destruction(member.dtype) for member in definition.members)


def require_declared_constructor(member):
    definition = unqualified(member.dtype).definition
    if requires_destruction(member.dtype) and definition.destructor is None:
        raise CppDiagnostic("cpp.unsupported.destructor-subobjects",
                            "member construction requires a declared destructor, not implicit D1 synthesis", member.coord)
    if not definition.constructors.overloads:
        raise CppDiagnostic("cpp.unsupported.constructor-subobjects",
                            "implicit member default/value/copy construction requires special-member semantics", member.coord)


def initialize(parser, member, this, initializer, *, construction_calls=None):
    """Call a selected C1 directly at the member's address; never zero/copy it."""
    require_declared_constructor(member)
    if requires_destruction(member.dtype) and construction_calls is None:
        raise CppDiagnostic("cpp.unsupported.destructor-subobjects",
                            "member construction requires an enclosing closed-construction proof", member.coord)
    from pcc.frontends.cpp.constructors import constructor_call
    field = c_ast.StructRef(c_ast.ID(this.c_name, member.coord), "->",
                           c_ast.ID(member.c_name, member.coord), member.coord)
    address = c_ast.UnaryOp("&", field, member.coord)
    arguments = [] if initializer is None else list(initializer.expressions)
    # Omission default-initializes; an explicit empty () value-initializes.
    # The selected C1 owns whether value initialization first zeroes storage;
    # this must not be guessed from the mere presence of a declaration.
    return constructor_call(parser, unqualified(member.dtype), arguments, address, member.coord,
                            construction_calls=construction_calls,
                            initialization_kind="value" if initializer is not None and not arguments else "default",
                            object_type=member.dtype)
