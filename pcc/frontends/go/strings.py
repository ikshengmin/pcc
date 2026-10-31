"""Go string literal bytes, not Python string-expression semantics."""

from pcc.frontends.go.syntax import GoDiagnostic

MAX_STRING_BYTES = 1 << 20


def bounded(data, token):
    if len(data) > MAX_STRING_BYTES:
        raise GoDiagnostic(token, "string exceeds implementation limit (1 MiB)")
    return data


def literal_bytes(token):
    text = token.text[1:-1]
    if "\0" in text:
        raise GoDiagnostic(token, "NUL character in source string literal")
    if token.text[0] == "`":
        try:
            return bounded(text.replace("\r", "").encode("utf-8"), token)
        except UnicodeEncodeError:
            raise GoDiagnostic(token, "invalid Unicode scalar in string literal") from None
    output = bytearray()
    escapes = {"a": 7, "b": 8, "f": 12, "n": 10, "r": 13, "t": 9, "v": 11, "\\": 92, '"': 34}
    index = 0
    while index < len(text):
        char = text[index]
        index += 1
        if char == "\n":
            raise GoDiagnostic(token, "newline in interpreted string literal")
        if char != "\\":
            try:
                output.extend(char.encode("utf-8"))
            except UnicodeEncodeError:
                raise GoDiagnostic(token, "invalid Unicode scalar in string literal") from None
        else:
            if index == len(text):
                raise GoDiagnostic(token, "incomplete string escape")
            escape = text[index]
            index += 1
            if escape in escapes:
                output.append(escapes[escape])
            else:
                if escape in "01234567":
                    count, alphabet, base = 3, "01234567", 8
                    index -= 1
                elif escape in "xuU":
                    count, alphabet, base = {"x": 2, "u": 4, "U": 8}[escape], "0123456789abcdefABCDEF", 16
                else:
                    raise GoDiagnostic(token, "invalid string escape")
                digits = text[index:index + count]
                if len(digits) != count or any(digit not in alphabet for digit in digits):
                    raise GoDiagnostic(token, "invalid string escape digits")
                index += count
                value = int(digits, base)
                if escape in "uU":
                    if value > 0x10ffff or 0xd800 <= value <= 0xdfff:
                        raise GoDiagnostic(token, "invalid Unicode scalar in string escape")
                    output.extend(chr(value).encode("utf-8"))
                else:
                    if value > 255:
                        raise GoDiagnostic(token, "string byte escape overflows byte")
                    output.append(value)
        if len(output) > MAX_STRING_BYTES:
            raise GoDiagnostic(token, "string exceeds implementation limit (1 MiB)")
    return bytes(output)
