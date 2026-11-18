"""Bounded SysV x86-64 physical result transport, not class lifetime semantics.

Itanium ABI 3.1.3.1 and 3.1.2.1; AMD64 psABI MEMORY result convention.
Source signatures and mangled names must never be changed by this projection.
This module does not grant admission to class expressions or skip construction.
"""
from pcc.frontends.c.ast import c_ast
from pcc.backend.self_backend_target_match import is_x86_64_linux_triple
from pcc.frontends.cpp.classes import is_class_type, trivial_special_members
from pcc.frontends.cpp.constructors import is_copy_constructor
from pcc.frontends.cpp.diagnostics import CppDiagnostic
from pcc.frontends.cpp.types import (
    PointerType, ReferenceType, array_type, contains_function, is_enum_type,
    pointer_type, unqualified,
)


def _unsupported(coord, message):
    raise CppDiagnostic('cpp.unsupported.class-value-abi', message, coord)


def classify_result(parser, dtype, coord):
    """Return 'indirect'/'c-aggregate'/None, or explicitly reject unknown forms.

    'c-aggregate' is classification only, NOT admission of class value semantics.
    User copy declaration suffices even when its body resides in another TU.
    Nontrivial destructors, inherited and nested record special members require
    independent lifetime/classification owners and are deliberately excluded.
    """
    plain = unqualified(dtype)
    if not is_class_type(plain):
        return None
    if not plain.complete:
        _unsupported(coord, 'class result ABI needs a complete class definition')
    if trivial_special_members(plain):
        # Classification only. Preserve the old declaration-only record path
        # without imposing this new physical transport's target requirement.
        return 'c-aggregate'
    if not is_x86_64_linux_triple(parser.types.target_triple):
        _unsupported(coord, 'class result transport requires SysV x86_64 Linux')
    definition = plain.definition
    if (plain.is_union or definition.base is not None or definition.polymorphic
            or definition.destructor is not None):
        _unsupported(coord, 'class result special-member shape is not owned')
    for member in definition.members:
        member_type = unqualified(member.dtype)
        while array_type(member_type):
            member_type = unqualified(member_type.element)
        if not (pointer_type(member_type) or is_enum_type(member_type)
                or parser.types.arithmetic_operand(member_type)):
            _unsupported(coord, 'nested class result subobject semantics are not owned')
    copies = [ctor for ctor in definition.constructors.overloads
              if is_copy_constructor(ctor, plain)]
    if not copies or not all(
            getattr(ctor, 'constructor_user_provided', False)
            and not getattr(ctor, 'constructor_defaulted', False)
            and not getattr(ctor, 'constructor_deleted', False)
            for ctor in copies):
        _unsupported(coord, 'non-trivial-for-calls status is not structurally proven')
    return 'indirect'


class IndirectResult:
    """A separately owned C projection. Never attach this type to source Symbol.

    The physical function returns the supplied address in RAX. Explicitly
    expressing that pointer return also prevents C aggregate size classifiers
    from mistakenly returning a one-word nontrivial class in a GP register.
    """
    def __init__(self, parser, symbol, coord):
        if classify_result(parser, symbol.dtype, coord) != 'indirect':
            _unsupported(coord, 'indirect result requires a proven nontrivial class')
        if (getattr(symbol, 'variadic', False)
                or getattr(symbol, 'template_parameters', ())
                or getattr(symbol, 'template_origin', None) is not None
                or getattr(symbol, 'method_virtual', False)):
            _unsupported(coord, 'variadic/template/virtual class result ABI is not owned')
        if any(is_class_type(t) or contains_function(t) for t in symbol.parameters):
            _unsupported(coord, 'class by-value or callback arguments need separate ABI owners')
        self.result_type = unqualified(symbol.dtype)
        self.pointer_type = PointerType(self.result_type)
        self.linkage_name = symbol.c_name

    def declaration(self, parser, parameter_declarations, hidden_name, coord):
        """Input parameters already include this FIRST when non-static.

        The result pointer is inserted before this, not after it. Caller chooses
        a fresh projection-only name; it is not installed in source Scope.
        Preserve existing storage/inline attributes on the returned declaration.
        """
        hidden = parser._decl(hidden_name, self.pointer_type, coord)
        declaration = parser._decl(self.linkage_name, self.pointer_type, coord)
        declaration.type = c_ast.FuncDecl(
            c_ast.ParamList([hidden] + list(parameter_declarations), coord),
            declaration.type, coord)
        return declaration

    def call(self, result_address, projected_arguments, coord):
        """Arguments include this first; address is evaluated once in the call.

        The lifetime owner must supply fresh, aligned destination storage and
        expose a class prvalue, not this internal pointer result, to C++ users.
        """
        return c_ast.FuncCall(c_ast.ID(self.linkage_name, coord),
                              c_ast.ExprList([result_address] + list(projected_arguments), coord), coord)

    def return_after_construction(self, hidden_name, coord):
        """Invoke only after successful initialization of the result object.

        Construction, copy access/deletion, elision and local cleanup remain
        explicit obligations of the return-expression/lifetime owner.
        """
        return c_ast.Return(c_ast.ID(hidden_name, coord), coord)
