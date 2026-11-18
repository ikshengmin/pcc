"""Source return expressions connected to a caller-owned result destination."""
from pcc.frontends.c.ast import c_ast
from pcc.frontends.cpp.diagnostics import CppDiagnostic
from pcc.frontends.cpp.types import unqualified


def parse_return_expression(parser):
    old = getattr(parser, 'reading_class_return_expression', False)
    parser.reading_class_return_expression = True
    try:
        return None if parser._peek().value == ';' else parser._expression()
    finally:
        parser.reading_class_return_expression = old


def finish_return(parser, expression, coord):
    from pcc.frontends.cpp.parser import Expression
    from pcc.frontends.cpp.class_result_prvalues import ClassResultPrvalue, return_into
    abi, hidden = parser.class_result_projection
    if expression is None:
        raise CppDiagnostic('cpp.return-value-required', 'class return needs a result object', coord)
    from pcc.frontends.cpp.class_result_copy import eligible, return_copy
    if (expression.lvalue and not eligible(expression)) or unqualified(expression.dtype) is not abi.result_type:
        raise CppDiagnostic('cpp.unsupported.class-result-prvalue',
                            'named or converted class results need separate copy/move ownership', coord)
    storage = Expression(c_ast.ID(hidden, coord), abi.pointer_type)
    final = abi.return_after_construction(hidden, coord)
    if eligible(expression):
        statement = return_copy(parser, expression, storage, final, coord)
    elif isinstance(expression.node, ClassResultPrvalue):
        statement = return_into(parser, expression, storage, final)
    else:
        from pcc.frontends.cpp.class_result_lifetimes import forward_return
        statement = forward_return(parser, expression, storage, final, coord)
    from pcc.frontends.cpp.class_result_body import record_constructed_return
    record_constructed_return(parser, final)
    return statement
