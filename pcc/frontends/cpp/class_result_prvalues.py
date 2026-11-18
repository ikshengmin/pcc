"""C++17 same-class prvalues constructed directly in an ABI-owned result slot.

No temporary object, C aggregate return, copy operation, or destructor call is
invented here. The declaration/call ABI owner supplies the complete-object
address; the caller's lifetime owner owns destruction of the result object.
"""
from dataclasses import dataclass
from pcc.frontends.c.ast import c_ast
from pcc.frontends.cpp.diagnostics import CppDiagnostic
from pcc.frontends.cpp.types import PointerType, unqualified
from pcc.frontends.cpp.classes import is_class_type


from pcc.frontends.cpp.class_result_guards import PendingClassResult


@dataclass(frozen=True)
class ClassResultPrvalue(PendingClassResult):
    dtype: object
    arguments: tuple
    coord: object


def require_context(parser, dtype, coord):
    if (parser.template_function is not None or parser.instantiating_template
            or getattr(parser, 'class_template_method', None) is not None):
        raise CppDiagnostic('cpp.unsupported.class-result-prvalue',
                            'class result prvalues in template bodies require a separate Pattern owner', coord)
    if not is_class_type(dtype) or unqualified(parser.return_type) is not unqualified(dtype):
        raise CppDiagnostic('cpp.unsupported.class-result-prvalue',
                            'only a same-class ordinary function result has an owned destination', coord)
    from pcc.frontends.cpp.classes import require_complete
    require_complete(unqualified(dtype), coord)


def parse_prvalue(parser, dtype, coord):
    """Called by _primary only inside the bounded return-expression context."""
    require_context(parser, dtype, coord)
    from pcc.frontends.cpp.constructors import parse_constructor_argument
    parser._expect('(')
    arguments = []
    if not parser._accept(')'):
        while True:
            arguments.append(parse_constructor_argument(parser))
            if parser._accept(')'):
                break
            parser._expect(',')
    from pcc.frontends.cpp.parser import Expression
    parser.has_class_result_plans = True
    return Expression(ClassResultPrvalue(unqualified(dtype), tuple(arguments), coord),
                      unqualified(dtype), False)


def construct_into(parser, expression, destination, coord, *, require_closed=False, construction_calls=None):
    """Produce the real constructor call at a typed, already-evaluated address.

    destination is an Expression of exactly pointer-to-result-class type,
    supplied by the physical ABI owner (typically a hidden parameter ID).
    """
    value = expression.node
    if not isinstance(value, ClassResultPrvalue):
        raise CppDiagnostic('cpp.unsupported.class-result-prvalue',
                            'named objects, conversions and other result expressions require copy/move semantics', coord)
    require_context(parser, value.dtype, coord)
    pointer = unqualified(destination.dtype)
    if not isinstance(pointer, PointerType) or pointer.pointee is not value.dtype:
        raise CppDiagnostic('cpp.class-result-destination',
                            'result destination must be a writable pointer to the exact class', coord)
    definition = value.dtype.definition
    # [class.copy.elision]: the destructor must be accessible/non-deleted even
    # though no temporary is destroyed at the return statement.
    if definition.destructor is None:
        from pcc.frontends.cpp.default_destructors import require_implicit_usable
        require_implicit_usable(definition, coord)
    else:
        from pcc.frontends.cpp.destructors import check_usable
        check_usable(parser.scope, definition.destructor, coord)
    if not definition.constructors.overloads:
        raise CppDiagnostic('cpp.unsupported.class-result-prvalue',
                            'implicit/aggregate result initialization requires its own initialization owner', coord)
    from pcc.frontends.cpp.constructors import constructor_call, closed_construction_body
    owned_calls = {}
    invocation = constructor_call(parser, value.dtype, list(value.arguments), destination.node, coord,
                            require_closed=require_closed, construction_calls=owned_calls,
                            initialization_kind='value' if not value.arguments else 'default',
                            object_type=value.dtype)
    # Constructor body proof alone does not prove evaluation of its arguments
    # or expanded defaults. Traverse the exact emitted tree with only the
    # constructor calls this invocation's semantic owner actually selected.
    if require_closed and not closed_construction_body(invocation, owned_calls):
        raise CppDiagnostic('cpp.unsupported.class-result-effects',
                            'result construction with active cleanup requires closed constructor, argument and default effects', coord)
    if construction_calls is not None:
        construction_calls.update(owned_calls)
    return invocation


def return_into(parser, expression, destination, final_return):
    """Construct before local cleanups, then perform the ABI owner's return.

    final_return must be the ABI owner's C Return node. This module cannot
    guess whether that physical convention returns void or the result address.
    No result destructor is added to the callee's automatic cleanup stack.
    """
    if not isinstance(final_return, c_ast.Return):
        raise TypeError('class result requires an ABI-owned Return node')
    coord = expression.node.coord
    cleanups = parser.lifetimes.cleanup(0, coord)
    construction = construct_into(parser, expression, destination, coord,
                                  require_closed=bool(cleanups))
    return c_ast.Compound([construction] + cleanups + [final_return], coord)
