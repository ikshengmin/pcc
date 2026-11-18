"""Explicit destinations for ordinary same-class C++17 returned prvalues.

No ABI selection, token replay, NRVO, copy suppression or reference fallback.
The producing result plan must provide require_closed(parser, coord) and
emit_into(parser, typed_destination, coord). It owns proof and physical calls;
this module owns destination storage and normal-exit lifetime exactly once.
"""
from dataclasses import dataclass
from pcc.frontends.c.ast import c_ast
from pcc.frontends.cpp.classes import is_class_type, require_complete
from pcc.frontends.cpp.diagnostics import CppDiagnostic
from pcc.frontends.cpp.lifetimes import AutomaticLifetimes
from pcc.frontends.cpp.sema import Symbol
from pcc.frontends.cpp.types import PointerType, unqualified


def unsupported(coord, message):
    raise CppDiagnostic('cpp.unsupported.class-result-lifetime', message, coord)


@dataclass(frozen=True)
class ResultDestination:
    """Complete-object storage, never a base/member/overlapping subobject."""
    dtype: object
    address: object
    kind: str


def _check(parser, expression, destination, coord):
    if (parser.template_function is not None or parser.instantiating_template
            or parser.unevaluated_depth):
        unsupported(coord, 'template or unevaluated class result ownership is not implemented')
    if destination.kind not in ('automatic', 'return', 'discarded'):
        unsupported(coord, 'result subobject/reference/receiver ownership is not implemented')
    dtype = unqualified(destination.dtype)
    if (not is_class_type(dtype) or unqualified(expression.dtype) is not dtype
            or expression.lvalue or expression.assignment_result):
        unsupported(coord, 'only exact same-class prvalues construct directly in result storage')
    require_complete(dtype, coord)
    if destination.dtype is not dtype:
        unsupported(coord, 'cv-qualified result destination initialization is not qualified')
    address = destination.address
    if address.dtype != PointerType(dtype):
        raise CppDiagnostic('cpp.class-result-destination', 'result storage pointer type mismatch', coord)
    # Only fresh-local or ABI-hidden IDs, never side-effecting destination code.
    node = address.node
    if not (isinstance(node, c_ast.ID) or isinstance(node, c_ast.UnaryOp)
            and node.op == '&' and isinstance(node.expr, c_ast.ID)):
        unsupported(coord, 'result destination must be a stable owned address')
    plan = expression.node
    if (getattr(plan, 'dtype', None) is not dtype
            or not callable(getattr(plan, 'require_closed', None))
            or not callable(getattr(plan, 'emit_into', None))):
        unsupported(coord, 'returned prvalue has no qualified construction-to-destination plan')
    # Not a noexcept guess or a caller-supplied boolean: producer checks its
    # bound construction/call proof and every argument before emission.
    plan.require_closed(parser, coord)
    from pcc.frontends.cpp.default_destructors import require_implicit_usable
    from pcc.frontends.cpp.destructors import check_usable
    destructor = dtype.definition.destructor
    if destructor is None:
        require_implicit_usable(dtype.definition, coord)
    else:
        check_usable(parser.scope, destructor, coord)
        if not getattr(destructor, 'closed_destruction_body', False):
            unsupported(coord, 'result destructor has no bound closed-destruction proof')
    return plan


def _address(symbol, coord):
    from pcc.frontends.cpp.parser import Expression
    return Expression(c_ast.UnaryOp('&', c_ast.ID(symbol.c_name, coord), coord),
                      PointerType(symbol.dtype))


def initialize_local(parser, symbol, expression, coord):
    """Consume a plain `T name = f();` initializer after the semicolon.

    Parser has already declared the name. Its ordinary construct_local caller
    owns exactly one lexical lifetime registration after successful construction.
    """
    if symbol.initialization_complete:
        raise CppDiagnostic('cpp.class-result-destination', 'result local was already initialized', coord)
    if not parser.lifetimes.frames:
        unsupported(coord, 'result local needs an active lexical lifetime frame')
    destination = ResultDestination(symbol.dtype, _address(symbol, coord), 'automatic')
    plan = _check(parser, expression, destination, coord)
    call = plan.emit_into(parser, destination.address, coord)
    declaration = parser._decl(symbol.c_name, symbol.dtype, coord)
    # This emits no D1 now. The existing lexical owner emits the one matching
    # scope/return/break/continue cleanup on each normal control-flow path.
    symbol.initialization_complete = True
    symbol.jump_entry = 'initialized'
    return [declaration, call]


def forward_return(parser, expression, destination_address, final_return, coord):
    """Forward f() directly to the caller's hidden slot, then clean locals."""
    if not isinstance(final_return, c_ast.Return):
        raise TypeError('final return must be supplied by the physical ABI owner')
    destination = ResultDestination(unqualified(parser.return_type), destination_address, 'return')
    plan = _check(parser, expression, destination, coord)
    call = plan.emit_into(parser, destination.address, coord)
    # Result never enters this function's automatic frames. No NRVO or copy of
    # a named local is accepted by _check's typed plan/value-category gate.
    return c_ast.Compound([call] + parser.lifetimes.cleanup(0, coord) + [final_return], coord)


def discard_full_expression(parser, expression, coord):
    """Only a standalone `f();` statement, not comma/argument/receiver trees."""
    dtype = unqualified(expression.dtype)
    symbol = Symbol('<discarded-result>', 'value', coord, dtype=dtype,
                    c_name=parser._fresh_projection_name('__pcc_cpp_result_'))
    destination = ResultDestination(dtype, _address(symbol, coord), 'discarded')
    plan = _check(parser, expression, destination, coord)
    call = plan.emit_into(parser, destination.address, coord)
    temporary = AutomaticLifetimes()
    temporary.cleanup_destructors = parser.lifetimes.cleanup_destructors
    temporary.enter()
    try:
        temporary.register(parser, symbol)
        cleanup = temporary.current_cleanup(coord)
    finally:
        temporary.leave()
    # A full-expression compound encloses both materialization and D1. No
    # registration escapes to lexical frames, preventing double destruction.
    return c_ast.Compound([parser._decl(symbol.c_name, dtype, coord), call] + cleanup, coord)


def reject_receiver(coord):
    unsupported(coord, 'returned-prvalue call receivers need full-expression materialization and sequencing')
