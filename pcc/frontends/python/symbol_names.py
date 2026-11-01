"""Injective ASCII spelling for compiler-owned Python identifier components.

Python-visible names remain Unicode. Explicit external ABI symbols do not
pass through this encoder. Dollar escaping keeps encoded and literal names
distinct without changing ordinary ASCII Python identifiers.
"""


def identifier_symbol(name: str) -> str:
    if name.isascii() and "$" not in name:
        return name
    parts: list[str] = []
    digits = "0123456789abcdef"
    for character in name:
        code = ord(character)
        if code < 128:
            parts.append("$$" if character == "$" else character)
        else:
            encoded = ""
            while code:
                encoded = digits[code & 15] + encoded
                code //= 16
            parts.append("$u" + encoded + "$")
    return "".join(parts)
