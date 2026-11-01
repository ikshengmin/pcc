"""Owned Unicode 17 XID classification and NFKC for Python identifiers.

Python Language Reference 2.3 / PEP 3131; Unicode Standard Annex 15.
All Unicode data is packed into the compiler; no host Unicode API is used.
"""
from pcc.frontends.python._unicode_identifier_data import (
    COMBINING_KEYS,
    COMBINING_VALUES,
    COMPOSITION_KEYS,
    COMPOSITION_VALUES,
    DECOMPOSITION_KEYS,
    DECOMPOSITION_OFFSETS,
    DECOMPOSITION_VALUES,
    XID_CONTINUE,
    XID_START,
)


def _u32(data: bytes, offset: int) -> int:
    return (data[offset] | (data[offset + 1] << 8)
            | (data[offset + 2] << 16) | (data[offset + 3] << 24))


def _find(data: bytes, value: int, width: int) -> int:
    low = 0
    high = len(data) // width
    while low < high:
        middle = (low + high) // 2
        candidate = _u32(data, middle * width)
        if width == 8:
            candidate |= _u32(data, middle * width + 4) << 32
        if candidate < value:
            low = middle + 1
        elif candidate > value:
            high = middle
        else:
            return middle
    return -1


def _in_ranges(data: bytes, code: int) -> bool:
    low = 0
    high = len(data) // 8
    while low < high:
        middle = (low + high) // 2
        if code < _u32(data, middle * 8):
            high = middle
        elif code > _u32(data, middle * 8 + 4):
            low = middle + 1
        else:
            return True
    return False


def is_identifier_start(code: int) -> bool:
    if code < 128:
        return code == 95 or 65 <= code <= 90 or 97 <= code <= 122
    return _in_ranges(XID_START, code)


def is_identifier_continue(code: int) -> bool:
    if code < 128:
        return is_identifier_start(code) or 48 <= code <= 57
    return _in_ranges(XID_CONTINUE, code)


def _combining(code: int) -> int:
    index = _find(COMBINING_KEYS, code, 4)
    return COMBINING_VALUES[index] if index >= 0 else 0


def _append_ordered(output: list[int], code: int) -> None:
    output.append(code)
    category = _combining(code)
    if category == 0:
        return
    index = len(output) - 1
    while index > 0 and _combining(output[index - 1]) > category:
        output[index] = output[index - 1]
        index -= 1
    output[index] = code


def _decompose(output: list[int], code: int) -> None:
    syllable = code - 0xAC00
    if 0 <= syllable < 11172:
        _append_ordered(output, 0x1100 + syllable // 588)
        _append_ordered(output, 0x1161 + (syllable % 588) // 28)
        trailing = syllable % 28
        if trailing:
            _append_ordered(output, 0x11A7 + trailing)
        return
    index = _find(DECOMPOSITION_KEYS, code, 4)
    if index < 0:
        _append_ordered(output, code)
        return
    start = _u32(DECOMPOSITION_OFFSETS, index * 4)
    end = _u32(DECOMPOSITION_OFFSETS, (index + 1) * 4)
    while start < end:
        _decompose(output, _u32(DECOMPOSITION_VALUES, start * 4))
        start += 1


def _compose(starter: int, code: int) -> int:
    leading = starter - 0x1100
    vowel = code - 0x1161
    if 0 <= leading < 19 and 0 <= vowel < 21:
        return 0xAC00 + (leading * 21 + vowel) * 28
    syllable = starter - 0xAC00
    trailing = code - 0x11A7
    if 0 <= syllable < 11172 and syllable % 28 == 0 and 0 < trailing < 28:
        return starter + trailing
    index = _find(COMPOSITION_KEYS, (starter << 21) | code, 8)
    return _u32(COMPOSITION_VALUES, index * 4) if index >= 0 else 0


def normalize_identifier(value: str) -> str:
    decomposed: list[int] = []
    for character in value:
        _decompose(decomposed, ord(character))
    output: list[int] = []
    starter_index = -1
    starter = 0
    last_category = 0
    for code in decomposed:
        category = _combining(code)
        composite = 0
        if starter_index >= 0 and (last_category < category or last_category == 0):
            composite = _compose(starter, code)
        if composite:
            output[starter_index] = composite
            starter = composite
        else:
            if category == 0:
                starter_index = len(output)
                starter = code
            output.append(code)
            last_category = category
    characters: list[str] = []
    for code in output:
        characters.append(chr(code))
    return "".join(characters)
