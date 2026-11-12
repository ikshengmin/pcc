"""Ordinary (narrow) string literals as C++ expressions.

N4659 [lex.string]/8,9,13,14, [lex.ccon]/7-8, [conv.array]. An ordinary string
literal is an lvalue of type `const char[N]`; adjacent literals concatenate
after their escape sequences are decoded (phase 6), so "\\x1" "2" stays two
characters. The execution character set is UTF-8: a source character outside
the basic set and a universal-character-name are encoded as UTF-8 bytes.
Numeric escapes must fit in an unsigned char. The decoded bytes are re-spelled
with three-digit octal escapes, so the shared C owner stores exactly these
bytes (it would otherwise truncate non-ASCII characters) and no escape can
merge with a following digit. Prefixed (L, u, U, u8) and raw literals and
conditionally-supported escapes remain explicit gaps.
"""
from pcc.frontends.c.ast import c_ast
from pcc.frontends.cpp.constants import not_constant
from pcc.frontends.cpp.diagnostics import CppDiagnostic
from pcc.frontends.cpp.types import ArrayType, qualify_const

_SIMPLE = {'n': 10, 't': 9, 'r': 13, 'a': 7, 'b': 8, 'f': 12, 'v': 11,
           '\\': 92, "'": 39, '"': 34, '?': 63}
_HEX = '0123456789abcdefABCDEF'


def unsupported(coord, message):
    raise CppDiagnostic('cpp.unsupported.string-literals', message, coord)


def invalid(coord, message):
    raise CppDiagnostic('cpp.invalid-string-literal', message, coord)


def decode(spelling, coord):
    """The bytes of one ordinary string literal token (without the NUL)."""
    if not (len(spelling) >= 2 and spelling[0] == '"' and spelling[-1] == '"'):
        unsupported(coord, 'prefixed and raw string literals require encoding semantics')
    body, out, index = spelling[1:-1], bytearray(), 0
    while index < len(body):
        char = body[index]
        if char != '\\':
            out += char.encode('utf-8')
            index += 1
            continue
        index += 1
        if index >= len(body):
            invalid(coord, 'a string literal cannot end with a backslash')
        escape = body[index]
        if escape in _SIMPLE:
            out.append(_SIMPLE[escape])
            index += 1
        elif escape in '01234567':
            end = index
            while end < len(body) and end < index + 3 and body[end] in '01234567':
                end += 1
            value = int(body[index:end], 8)
            if value > 0xFF:
                invalid(coord, 'an octal escape does not fit in a narrow character')
            out.append(value)
            index = end
        elif escape == 'x':
            end = index + 1
            while end < len(body) and body[end] in _HEX:
                end += 1
            if end == index + 1:
                invalid(coord, 'a hexadecimal escape requires at least one digit')
            value = int(body[index + 1:end], 16)
            if value > 0xFF:
                invalid(coord, 'a hexadecimal escape does not fit in a narrow character')
            out.append(value)
            index = end
        elif escape in 'uU':
            width = 4 if escape == 'u' else 8
            digits = body[index + 1:index + 1 + width]
            if len(digits) != width or any(digit not in _HEX for digit in digits):
                invalid(coord, 'a universal-character-name requires ' + str(width) + ' hexadecimal digits')
            point = int(digits, 16)
            if point > 0x10FFFF or 0xD800 <= point <= 0xDFFF:
                invalid(coord, 'a universal-character-name must designate a valid code point')
            out += chr(point).encode('utf-8')
            index += 1 + width
        else:
            unsupported(coord, 'conditionally-supported escape sequences are not implemented')
    return bytes(out)


def spelling_for(data):
    """An ASCII C spelling that stores exactly `data`."""
    return '"' + ''.join(chr(byte) if 32 <= byte < 127 and byte not in (34, 92) else '\\%03o' % byte
                         for byte in data) + '"'


def literal_expression(spelling, length, coord):
    from pcc.frontends.cpp.parser import Expression
    dtype = ArrayType(qualify_const('char'), length)
    return Expression(c_ast.Constant('string', spelling, coord), dtype, True,
                      constant_fact=not_constant(dtype, 'string literal object', coord))


def string_expression(parser, token):
    """One or more adjacent ordinary string literal tokens."""
    data = decode(token.value, token.coord)
    while parser._peek().kind in ('STRING_LITERAL', 'WSTRING_LITERAL'):
        following = parser.cursor.take()
        if following.kind != 'STRING_LITERAL':
            unsupported(following.coord, 'prefixed and raw string literals require encoding semantics')
        data += decode(following.value, following.coord)
    spelling, length = spelling_for(data), len(data) + 1
    if parser.template_function is not None:
        from pcc.frontends.cpp.parser import Expression
        from pcc.frontends.cpp.templates import Pattern
        dtype = ArrayType(qualify_const('char'), length)
        return Expression(Pattern('string-literal', token.coord, (spelling, length)), dtype, True,
                          constant_fact=not_constant(dtype, 'string literal object', token.coord))
    return literal_expression(spelling, length, token.coord)
