"""C++17 ordinary one-code-unit character expressions ([lex.ccon]).

Use the target-owned escape decoder already used for C++ preprocessing, but
retain the language's distinct char type in Sema and the C projection. Wide,
Unicode and multicharacter expression semantics remain explicit boundaries.
"""

from pcc.frontends.c.c_character_literals import parse_c_character_constant
from pcc.frontends.cpp.constants import known
from pcc.frontends.cpp.diagnostics import CppDiagnostic


def character_expression(parser, token):
    if not token.value.startswith("'"):
        raise CppDiagnostic("cpp.unsupported.character-literals",
                            "prefixed character expressions require encoding/type semantics", token.coord)
    index = 1
    while index < len(token.value) - 1:
        if token.value[index] == "\\":
            index += 1
            escape = token.value[index:index + 1]
            # The shared C decoder has C11 UCN restrictions. Do not apply
            # them to C++17 [lex.charset]/2, which permits basic characters
            # in literal UCNs; keep that not-yet-owned conversion explicit.
            if escape in ("u", "U"):
                raise CppDiagnostic("cpp.unsupported.character-literals",
                                    "universal character expressions require C++ encoding semantics", token.coord)
            if escape not in "abfnrtv'\"?\\01234567x":
                raise CppDiagnostic("cpp.unsupported.character-literals",
                                    "conditionally-supported escape semantics are not implemented", token.coord)
        index += 1
    try:
        value, dtype, end = parse_c_character_constant(
            token.value, target_triple=parser.types.target_triple, cpp=True,
        )
    except ValueError as error:
        if "not representable" in str(error):
            raise CppDiagnostic("cpp.unsupported.character-literals",
                                "out-of-range numeric character escapes require implementation-defined semantics", token.coord)
        raise CppDiagnostic("cpp.invalid-character-literal", str(error), token.coord)
    if end != len(token.value):
        raise CppDiagnostic("cpp.invalid-character-literal", "trailing character literal input", token.coord)
    if dtype != "char":
        raise CppDiagnostic("cpp.unsupported.character-literals",
                            "multicharacter and multibyte character expressions require encoding semantics", token.coord)
    # Never project a C++ char literal as a C int literal. Also never inherit
    # the integer literal zero's null-pointer-constant status: '\\0' is a char
    # literal, not the integer literal required by [conv.ptr]/1 in C++17.
    from pcc.frontends.cpp.parser import Expression
    integer = parser._literal("int", str(abs(value)), "int", token.coord, abs(value))
    if value < 0:
        integer = parser._unary_expression("-", integer, token.coord)
    return Expression(parser._convert(integer, dtype, token.coord, force=True), dtype,
                      constant_fact=known(dtype, value, token.coord))
