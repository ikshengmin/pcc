"""Declared C1 construction with bounded, source-proven member lifetimes.

N4659 [class.base.init]/7,9,13: initialize the actual member subobject in
declaration order, before the enclosing constructor body. This is neither
aggregate assignment nor synthesis of an implicit special member. Arrays,
implicit special members and unknown construction effects remain explicit gaps.
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
        return True  # Arrays have their own, currently unavailable, owner.
    if not is_class_type(dtype):
        return False
    definition = unqualified(dtype).definition
    return definition.destructor is not None or any(
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
    # Every admitted declared C1 is user-provided (defaulted/deleted C1 forms
    # remain gated). Both omitted/default initialization and explicit () value
    # initialization therefore call it, with no preceding whole-object zeroing.
    return constructor_call(parser, unqualified(member.dtype), arguments, address, member.coord,
                            construction_calls=construction_calls)
