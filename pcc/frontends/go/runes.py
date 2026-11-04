"""Decode a Go rune literal as one scalar value, not UTF-8 bytes.

Byte escapes denote the code point 0..255 here; unlike string literals, an
escaped 0xff is not an ill-formed byte sequence. No Python literal evaluation
or Unicode normalization supplies Go semantics.
"""

from pcc.frontends.go.diagnostics import GoDiagnostic


def literal_value(token):
    text = token.text[1:-1]
    if not text:
        raise GoDiagnostic(token, 'empty rune literal')
    if '\0' in text:
        raise GoDiagnostic(token, 'NUL character in source rune literal')
    if '\n' in text:
        raise GoDiagnostic(token, 'newline in rune literal')
    if text[0] != '\\':
        if len(text) != 1:
            raise GoDiagnostic(token, 'rune literal must contain exactly one character')
        value = ord(text)
    else:
        if len(text) < 2:
            raise GoDiagnostic(token, 'incomplete rune escape')
        escape = text[1]
        simple = {'a': 7, 'b': 8, 'f': 12, 'n': 10, 'r': 13, 't': 9, 'v': 11,
                  '\\': 92, "'": 39}
        if escape in simple:
            value, consumed = simple[escape], 2
        else:
            if escape in '01234567':
                start, count, alphabet, base = 1, 3, '01234567', 8
            elif escape in 'xuU':
                start, count, alphabet, base = 2, {'x': 2, 'u': 4, 'U': 8}[escape], '0123456789abcdefABCDEF', 16
            else:
                raise GoDiagnostic(token, 'invalid rune escape')
            digits = text[start:start + count]
            if len(digits) != count or any(digit not in alphabet for digit in digits):
                raise GoDiagnostic(token, 'invalid rune escape digits')
            value, consumed = int(digits, base), start + count
            if escape not in 'uU' and value > 255:
                raise GoDiagnostic(token, 'rune byte escape overflows byte')
        if consumed != len(text):
            raise GoDiagnostic(token, 'rune literal must contain exactly one character')
    if value > 0x10ffff or 0xd800 <= value <= 0xdfff:
        raise GoDiagnostic(token, 'invalid Unicode scalar in rune literal')
    return value
