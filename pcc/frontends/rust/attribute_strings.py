"""Lexical string values for attributes and existing ABI names, not Rust String.

No expression/storage/lifetime lowering is provided by this scanner. Source
spans remain on tokens; cooked values implement Rust escapes, not Python eval.
"""


def scan_string(source, start, token):
    def finish(value, end):
        # Rust consumes an adjacent identifier/keyword as a literal suffix;
        # it must never become a second grammar token (e.g. ABI then `fn`).
        # This frontend has no suffixed string-literal interpretation.
        if end < len(source) and source[end].isidentifier():
            raise token.error('string literal suffixes are not implemented')
        return value, end

    if source[start] == 'r':
        quote = start + 1
        while quote < len(source) and source[quote] == '#':
            quote += 1
        hashes = quote - start - 1
        if quote >= len(source) or source[quote] != '"':
            return None
        if hashes > 255:
            raise token.error('raw string delimiters exceed 255 hashes')
        close = source.find('"' + '#' * hashes, quote + 1)
        if close < 0:
            raise token.error('unterminated raw string')
        value = source[quote + 1:close].replace('\r\n', '\n')
        if '\r' in value:
            raise token.error('bare carriage return in raw string')
        return finish(value, close + 1 + hashes)
    i, output = start + 1, []
    escapes = {'n': '\n', 'r': '\r', 't': '\t', '0': '\0', '\\': '\\', '"': '"', "'": "'"}
    while i < len(source):
        char = source[i]
        if char == '"':
            return finish(''.join(output), i + 1)
        if char == '\r':
            if i + 1 >= len(source) or source[i + 1] != '\n':
                raise token.error('bare carriage return in string')
            char, i = '\n', i + 1
        if char != '\\':
            output.append(char)
            i += 1
            continue
        i += 1
        if i >= len(source):
            break
        escape = source[i]
        if escape in escapes:
            output.append(escapes[escape]); i += 1
        elif escape == 'x':
            digits = source[i + 1:i + 3]
            if len(digits) != 2 or any(d not in '0123456789abcdefABCDEF' for d in digits) or int(digits, 16) > 127:
                raise token.error('invalid ASCII string escape')
            output.append(chr(int(digits, 16))); i += 3
        elif escape == 'u':
            if source[i + 1:i + 2] != '{':
                raise token.error('invalid Unicode string escape')
            close = source.find('}', i + 2)
            digits = source[i + 2:close] if close >= 0 else ''
            if (not digits or digits[0] == '_' or any(d not in '0123456789abcdefABCDEF_' for d in digits)
                    or not 1 <= len(digits.replace('_', '')) <= 6):
                raise token.error('invalid Unicode string escape')
            value = int(digits.replace('_', ''), 16)
            if value > 0x10ffff or 0xd800 <= value <= 0xdfff:
                raise token.error('Unicode string escape is not a scalar value')
            output.append(chr(value)); i = close + 1
        elif escape == '\n' or source[i:i + 2] == '\r\n':
            i += 2 if escape == '\r' else 1
            while i < len(source) and source[i] in ' \t\n\r':
                i += 1
        else:
            raise token.error('unsupported or invalid string escape')
    raise token.error('unterminated string')
