"""The C++17 special-member declaration exception-specifier grammar."""

from pcc.frontends.cpp.diagnostics import CppDiagnostic


def parse_exception_specification(parser):
    """N4659 [except.spec]/1-3: None leaves the declaration's default rule."""
    if parser._peek().value == "noexcept":
        return parser._declaration_noexcept()
    if parser._peek().value == "throw":
        token = parser.cursor.take()
        parser._expect("(")
        if not parser._accept(")"):
            raise CppDiagnostic("cpp.invalid-exception-specification",
                                "C++17 permits only the empty throw() specification", token.coord)
        return True
    return None
