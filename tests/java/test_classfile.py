import pytest

from pcc.frontends.java.classfile import (
    JavaError,
    UnsupportedJava,
    descriptor,
    modified_utf8,
    parse_class,
)
from tests.java.class_builder import ClassBuilder


def sample():
    return ClassBuilder().method("run", bytes([0x10, 42, 0xAC])).build()


def test_parse_independently_authored_class():
    cls = parse_class(sample())
    assert (cls.major, cls.name, cls.super_name) == (49, "Example", "java/lang/Object")
    assert cls.methods[0].code.bytecode == bytes([0x10, 42, 0xAC])


@pytest.mark.parametrize("size", [0, 3, 7, 8, 9, 10, 11, 17, 31, -1])
def test_truncated_class_rejected(size):
    with pytest.raises(JavaError, match="truncated"):
        parse_class(sample()[:size])


def test_trailing_data_rejected():
    with pytest.raises(JavaError, match="trailing"):
        parse_class(sample() + b"\0")


@pytest.mark.parametrize("major,minor,error", [(44, 0, "version"), (70, 0, "version"),
                                             (69, 65535, "preview"), (52, 2, "minor")])
def test_version_validation(major, minor, error):
    data = bytearray(sample())
    data[4:8] = minor.to_bytes(2, "big") + major.to_bytes(2, "big")
    with pytest.raises(JavaError, match=error):
        parse_class(bytes(data))


def test_bad_constant_tag_and_reference():
    data = bytearray(sample())
    data[10] = 99
    with pytest.raises(JavaError, match="tag"):
        parse_class(bytes(data))
    builder = ClassBuilder()
    builder.add(b"\x07\xff\xff")
    with pytest.raises(JavaError, match="index"):
        parse_class(builder.build())


def test_long_reserved_slot():
    builder = ClassBuilder()
    builder.add(b"\x05" + b"\0" * 8)
    with pytest.raises(JavaError, match="reserved second slot"):
        parse_class(builder.build())


def test_duplicate_methods():
    builder = ClassBuilder().method("run", b"\x03\xac").method("run", b"\x04\xac")
    with pytest.raises(JavaError, match="duplicate method"):
        parse_class(builder.build())


@pytest.mark.parametrize("text", ["", "I", "(V)I", "()", "()II", "([V)I", "(L;)I", "(L[I;)I", "(" + "I" * 256 + ")V"])
def test_invalid_method_descriptor(text):
    with pytest.raises(JavaError, match="descriptor"):
        descriptor(text)


def test_full_descriptors_are_parsed_not_admitted():
    assert descriptor("(I[[Ljava/lang/String;J)D") == (("I", "[[Ljava/lang/String;", "J"), "D")


@pytest.mark.parametrize("raw", [b"\0", b"\xc1\x81", b"\xe0\x80\x80", b"\xf0\x90\x80\x80", b"\xc2\x00"])
def test_bad_modified_utf8(raw):
    with pytest.raises(JavaError):
        modified_utf8(raw)


def test_modified_utf8_nul_and_surrogate_pair():
    assert modified_utf8(b"A\xc0\x80\xed\xa0\xbd\xed\xb8\x80") == "A\0\U0001f600"
