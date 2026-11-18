"""Non-NRVO return *pointer: real copy-initialization of caller result storage.

N4659 [stmt.return], [dcl.init], [over.match.copy]. A spelled dereference is
not a move-eligible id-expression. Named locals/parameters and lowered reference
IDs remain excluded even when their C node happens to be UnaryOp('*').
"""
from pcc.frontends.c.ast import c_ast
from pcc.frontends.cpp.classes import is_class_type
from pcc.frontends.cpp.diagnostics import CppDiagnostic
from pcc.frontends.cpp.types import PointerType, unqualified


def mark_explicit_dereference(expression):
    """Only call from the source unary '*' parser, never reference lowering."""
    if is_class_type(expression.dtype):
        expression.explicit_class_dereference = True
    return expression


def eligible(expression):
    return (getattr(expression, 'explicit_class_dereference', False)
            and expression.lvalue and not expression.assignment_result
            and isinstance(expression.node, c_ast.UnaryOp) and expression.node.op == '*')


def return_copy(parser, expression, destination, final_return, coord):
    from pcc.frontends.cpp.class_result_prvalues import require_context
    from pcc.frontends.cpp.class_result_abi import classify_result
    from pcc.frontends.cpp.constructor_lists import converting_candidates
    from pcc.frontends.cpp.sema import resolve_overload
    from pcc.frontends.cpp.constructors import (
        is_copy_constructor, convert_constructor_argument, register_pending_construction,
        closed_construction_body,
    )
    from pcc.frontends.cpp.default_constructors import require_usable, request_definition
    from pcc.frontends.cpp.default_arguments import expand
    from pcc.frontends.cpp.class_template_members import request_body
    if not eligible(expression):
        raise CppDiagnostic('cpp.unsupported.class-result-prvalue',
                            'copy return requires an explicit same-class pointer dereference', coord)
    dtype = unqualified(parser.return_type)
    require_context(parser, expression.dtype, coord)
    if classify_result(parser, dtype, coord) != 'indirect':
        raise CppDiagnostic('cpp.unsupported.class-value-abi', 'copy result needs owned indirect ABI', coord)
    if destination.dtype != PointerType(dtype):
        raise CppDiagnostic('cpp.class-result-destination', 'copy return destination has wrong type', coord)
    if not isinstance(final_return, c_ast.Return):
        raise TypeError('copy result requires the physical ABI return node')
    from pcc.frontends.cpp.default_destructors import require_implicit_usable
    # Current classifier proves no declared D1 and only scalar/pointer members.
    require_implicit_usable(dtype.definition, coord)
    candidates = converting_candidates(dtype.definition.constructors)
    selected = resolve_overload(candidates, [expression.dtype], parser.types,
                                coord, [expression.null_pointer], [True])
    if not is_copy_constructor(selected, dtype):
        raise CppDiagnostic('cpp.unsupported.class-result-prvalue',
                            'selected non-copy conversion needs a separate result owner', coord)
    parser.scope.check_member_access(selected, coord)
    require_usable(selected, coord, object_type=dtype)
    request_body(parser, selected, coord)
    register_pending_construction(parser, selected)
    arguments = expand(parser, selected, [expression], coord)
    converted = [convert_constructor_argument(parser, arg, target, coord)
                 for arg, target in zip(arguments, selected.parameters)]
    call = c_ast.FuncCall(c_ast.ID(selected.c_name, coord),
                          c_ast.ExprList([destination.node] + converted, coord), coord)
    from pcc.frontends.cpp.class_result_effects import record_constructor
    record_constructor(parser, call, selected)
    cleanup = parser.lifetimes.cleanup(0, coord)
    # Unknown external copy is permitted only with no outstanding cleanup.
    # When cleanup exists inspect exact emitted arguments/defaults AND the
    # selected constructor proof, not just a boolean supplied by a caller.
    if cleanup and not closed_construction_body(call, {call: selected}):
        raise CppDiagnostic('cpp.unsupported.class-result-effects',
                            'copy result with cleanup requires closed copy and argument effects', coord)
    request_definition(parser, selected)
    return c_ast.Compound([call] + cleanup + [final_return], coord)
