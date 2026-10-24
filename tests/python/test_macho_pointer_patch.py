"""Host parity for the native Mach-O scalar pointer-patch path.

These tests run the production merge and executable linker on small inputs.
The raw-memory intrinsics are modeled with host struct operations; this is
not native execution, a timing measurement, or a bootstrap qualification.
"""

import struct
from types import SimpleNamespace

import pytest

from pcc.backend import macho_exec as executable
from pcc.backend import macho_obj as obj, macho_spec as spec
from pcc.backend.native_object import (
    NativeObject,
    decode_packed_native_object,
    encode_native_object,
)


def _raw_words(monkeypatch):
    calls = []

    def abi_constant(name):
        assert name in ("object.bytes.data_offset", "object.bytearray.data_offset")
        return 24

    def load64(buffer, offset):
        calls.append(("load64", buffer, offset - 24, None))
        return struct.unpack_from("<q", buffer, offset - 24)[0]

    def store64(buffer, offset, value):
        assert type(buffer) is bytearray
        assert -(1 << 63) <= value < (1 << 63)
        calls.append(("store64", buffer, offset - 24, value))
        struct.pack_into("<q", buffer, offset - 24, value)

    monkeypatch.setattr(executable, "_NATIVE_PATCH_WORDS", True)
    monkeypatch.setattr(executable, "abi_constant", abi_constant)
    monkeypatch.setattr(executable, "load_i64", load64)
    monkeypatch.setattr(executable, "store_i64", store64)
    monkeypatch.setattr(executable, "load_i32", lambda b, o: struct.unpack_from("<i", b, o - 24)[0])
    monkeypatch.setattr(executable, "load_i8", lambda b, o: struct.unpack_from("<b", b, o - 24)[0])
    monkeypatch.setattr(executable, "store_i32", lambda b, o, v: struct.pack_into("<I", b, o - 24, v & 0xFFFFFFFF))
    return calls


def _forbidden_struct(*args):
    pytest.fail("valid native scalar patch allocated a struct result")


@pytest.mark.parametrize("offset", (0, 1, 8))
@pytest.mark.parametrize("value", (0, 1, (1 << 63) - 1, 1 << 63, (1 << 64) - 1))
def test_native_u64_patch_matches_little_endian_without_payload(monkeypatch, offset, value):
    calls = _raw_words(monkeypatch)
    buffer = bytearray(range(16))
    expected = bytearray(buffer)
    struct.pack_into("<Q", expected, offset, value)
    monkeypatch.setattr(executable, "struct", SimpleNamespace(pack_into=_forbidden_struct))
    executable._write_patch_u64(buffer, offset, value)
    assert buffer == expected
    assert len(calls) == 1 and calls[0][0] == "store64"
    assert calls[0][1] is buffer and calls[0][2] == offset
    assert calls[0][3] == (value if value < (1 << 63) else value - (1 << 64))


@pytest.mark.parametrize("offset", (0, 1, 8))
@pytest.mark.parametrize("value", (-(1 << 63), -1, 0, 1, (1 << 63) - 1))
def test_native_signed64_patch_read_has_no_struct_tuple(monkeypatch, offset, value):
    calls = _raw_words(monkeypatch)
    buffer = bytearray(range(16))
    struct.pack_into("<q", buffer, offset, value)
    original = bytes(buffer)
    monkeypatch.setattr(executable, "struct", SimpleNamespace(unpack_from=_forbidden_struct))
    assert executable._read_patch_signed64(buffer, offset) == value
    assert bytes(buffer) == original
    assert len(calls) == 1 and calls[0][0] == "load64"
    assert calls[0][1] is buffer and calls[0][2] == offset


class _Int(int):
    pass


class _Bytearray(bytearray):
    pass


class _Index:
    def __index__(self):
        return 1


def _outcome(action, buffer):
    try:
        value = action(buffer)
    except Exception as error:
        return ("error", type(error), str(error), bytes(buffer))
    return ("ok", value, bytes(buffer))


@pytest.mark.parametrize("buffer_type", (bytearray, _Bytearray, bytes, memoryview))
@pytest.mark.parametrize("offset", (-17, -8, -1, 0, 1, 8, 9, True, _Int(1), _Index(), 1.5))
def test_signed64_read_keeps_struct_bounds_and_coercion(monkeypatch, buffer_type, offset):
    calls = _raw_words(monkeypatch)
    before = buffer_type(bytes(range(16)))
    expected = _outcome(lambda b: struct.unpack_from("<q", b, offset)[0], before)
    actual = _outcome(lambda b: executable._read_patch_signed64(b, offset), before)
    assert actual == expected
    native = buffer_type is bytearray and type(offset) is int and 0 <= offset <= 8
    assert bool(calls) is native


@pytest.mark.parametrize("offset,value", (
    (-17, 1), (-8, 1), (-1, 1), (9, 1), (True, 1), (_Int(1), 1),
    (_Index(), 1), (1.5, 1), (0, True), (0, _Int(1)), (0, _Index()),
    (0, -1), (0, 1 << 64), (0, 1.5), (9, -1), (-17, 1 << 64),
))
@pytest.mark.parametrize("buffer_type", (bytearray, _Bytearray, bytes, memoryview))
def test_u64_write_keeps_struct_errors_and_mutation_timing(monkeypatch, buffer_type, offset, value):
    calls = _raw_words(monkeypatch)
    expected_buffer = buffer_type(bytes(range(16)))
    actual_buffer = buffer_type(bytes(range(16)))
    expected = _outcome(lambda b: struct.pack_into("<Q", b, offset, value), expected_buffer)
    actual = _outcome(lambda b: executable._write_patch_u64(b, offset, value), actual_buffer)
    assert actual == expected
    assert calls == []


def test_big_endian_execution_keeps_explicit_little_endian_struct(monkeypatch):
    calls = _raw_words(monkeypatch)
    monkeypatch.setattr(executable, "sys", SimpleNamespace(byteorder="big"))
    buffer = bytearray(8)
    executable._write_patch_u64(buffer, 0, (1 << 64) - 1)
    assert buffer == b"\xff" * 8
    assert executable._read_patch_signed64(buffer, 0) == -1
    assert calls == []


def _pointer_object():
    text = obj.Section(
        "__text", "__TEXT", data=bytes.fromhex("000080d2c0035fd6"),
        align_log2=2, flags=spec.S_REGULAR | spec.S_ATTR_PURE_INSTRUCTIONS,
        symbols=(obj.TextSymbol("_main", 0, external=True),),
    )
    data = obj.Section(
        "__data", "__DATA", data=struct.pack("<qqq", 0, -8, 7), align_log2=3,
        symbols=(obj.TextSymbol("_counter", 0, external=True),),
        relocations=(
            obj.Relocation(0, "_main", spec.ARM64_RELOC_UNSIGNED, False, 3),
            obj.Relocation(8, "_counter", spec.ARM64_RELOC_UNSIGNED, False, 3),
            obj.Relocation(16, "_puts", spec.ARM64_RELOC_UNSIGNED, False, 3),
        ),
    )
    tls = obj.Section(
        "__thread_data", "__DATA", data=struct.pack("<Q", 3), align_log2=3,
        flags=0x11, symbols=(obj.TextSymbol("_tls_value", 0, external=True),),
    )
    descriptor = obj.Section(
        "__thread_vars", "__DATA", data=b"\0" * 24, align_log2=3, flags=0x13,
        symbols=(obj.TextSymbol("_tls", 0, external=True),),
        relocations=(
            obj.Relocation(0, "__tlv_bootstrap", spec.ARM64_RELOC_UNSIGNED, False, 3),
            obj.Relocation(16, "_tls_value", spec.ARM64_RELOC_UNSIGNED, False, 3),
        ),
    )
    return NativeObject.from_sections(
        (text, data, tls, descriptor), undefined=("_puts", "__tlv_bootstrap"),
    )


@pytest.mark.parametrize("packed", (False, True))
@pytest.mark.parametrize("source_view", (False, True))
def test_real_merge_and_signed_image_match_for_data_tls_and_chains(monkeypatch, packed, source_view):
    monkeypatch.setattr(executable, "_libsystem_exports_symbol", lambda name: name in ("_puts", "__tlv_bootstrap"))

    def inputs():
        value = _pointer_object()
        return [decode_packed_native_object(encode_native_object(value)) if packed else value]

    monkeypatch.setattr(executable, "_NATIVE_PATCH_WORDS", False)
    expected = executable.link_executable(
        inputs(), _consume_inputs=source_view, _direct_source_view=source_view,
    )
    calls = _raw_words(monkeypatch)
    actual = executable.link_executable(
        inputs(), _consume_inputs=source_view, _direct_source_view=source_view,
    )
    assert actual == expected
    assert spec.parse_object(actual).header["filetype"] == spec.MH_EXECUTE
    assert any(call[0] == "load64" for call in calls)
    assert any(call[0] == "store64" and call[3] < 0 for call in calls)
    assert any(call[0] == "store64" and call[3] >= 0 for call in calls)
