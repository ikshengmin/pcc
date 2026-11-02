"""Unevaluated type formation, N4659 [dcl.type.simple]/4.

https://timsong-cpp.github.io/cppwp/n4659/dcl.type.simple#4
Reference/dependent/placeholder types remain explicit capability boundaries.
No operand AST is emitted, and no spelling is substituted into source text.
"""
from pcc.frontends.cpp.diagnostics import CppDiagnostic


def parse_decltype(parser):
    token = parser._expect("decltype")
    parser._expect("(")
    if parser.parsing_template or parser.instantiating_template:
        raise CppDiagnostic("cpp.unsupported.decltype-templates",
                            "dependent decltype and template replay are not implemented", token.coord)
    if parser._peek().value == "auto":
        raise CppDiagnostic("cpp.unsupported.decltype-auto",
                            "decltype(auto) requires placeholder deduction", token.coord)
    parser.unevaluated_depth += 1
    try:
        expression = parser._expression()
        parser._expect(")")
    finally:
        parser.unevaluated_depth -= 1
    if expression.symbol is not None and expression.symbol.kind == "function":
        raise CppDiagnostic("cpp.unsupported.decltype-functions",
                            "function entity decltype requires overload/type formation", token.coord)
    if expression.decltype_entity is not None:
        return expression.decltype_entity
    if expression.lvalue or expression.assignment_result:
        raise CppDiagnostic("cpp.unsupported.references",
                            "decltype of a non-entity glvalue requires a reference type", token.coord)
    return expression.dtype
