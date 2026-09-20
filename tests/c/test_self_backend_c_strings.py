"""LLVM c-string decoding preserves byte and malformed-escape behavior."""

import itertools
import re

import pytest

from pcc.backend import BackendUnavailable
from pcc.backend import self_backend_parse as parser


def _reference(token):
    body = token[2:-1]
    data = bytearray()
    index = 0
    while index < len(body):
        if body[index] == "\\":
            if index + 1 >= len(body):
                raise BackendUnavailable("truncated escape")
            if index + 2 < len(body) and re.fullmatch(r"[0-9A-Fa-f]{2}", body[index + 1:index + 3]):
                data.append(int(body[index + 1:index + 3], 16))
                index += 3
                continue
            data.append(ord(body[index + 1]))
            index += 2
        else:
            data.append(ord(body[index]))
            index += 1
    return bytes(data)


def test_c_string_decodes_every_byte_without_regex(monkeypatch):
    token = 'c"' + ''.join('\\%02X' % value for value in range(256)) + '"'

    def unexpected_regex(*args, **kwargs):
        raise AssertionError("byte decoding must not compile a regex")

    monkeypatch.setattr(parser.re, "fullmatch", unexpected_regex)
    assert parser.decode_llvm_c_string(token) == bytes(range(256))


def test_c_string_escape_boundaries_match_previous_decoder():
    alphabet = "09aAfFgGx\\\"\n\x00é١"
    for left, right in itertools.product(alphabet, repeat=2):
        token = 'c"before\\' + left + right + 'after"'
        try:
            expected = _reference(token)
        except (BackendUnavailable, ValueError) as error:
            with pytest.raises(type(error)):
                parser.decode_llvm_c_string(token)
        else:
            assert parser.decode_llvm_c_string(token) == expected, repr(token)
    assert parser.decode_llvm_c_string('c""') == b""
    assert parser.decode_llvm_c_string('c"tail\\f"') == b"tailf"
    with pytest.raises(ValueError):
        parser.decode_llvm_c_string('c"☃"')


@pytest.mark.parametrize("token", ['"plain"', 'c"missing', 'c"end\\"'])
def test_c_string_rejects_invalid_or_truncated_tokens(token):
    with pytest.raises(BackendUnavailable):
        parser.decode_llvm_c_string(token)
