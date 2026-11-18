"""Integer bitcasts preserve NaN sign, quietness and payload on every host."""
import pytest

from pcc.backend.self_backend_parse import decode_value_token
from pcc.backend.wide_float import encode_float_bits


@pytest.mark.parametrize("bits", [
    0x7f800000, 0xff800000,
    0x7fc00000, 0xffc00000,
    0x7fc12345, 0xffc12345,
    0x7f800001, 0xff800001,
    0x7fffffff, 0xffffffff,
])
def test_float_bitcast_special_encoding_is_bit_exact(bits):
    token = decode_value_token(f"bitcast (i32 {bits} to float)")
    expected = ((bits >> 31) << 63) | (2047 << 52) | ((bits & 0x7fffff) << 29)
    assert token == f"0x{expected:016X}"
    assert encode_float_bits(token, 32) == bits


@pytest.mark.parametrize("bits", [
    0x00000000, 0x80000000, 0x00000001, 0x80000001,
    0x007fffff, 0x807fffff, 0x00800000, 0x80800000,
    0x3f800000, 0xbf800000, 0x3f812345, 0xbf812345,
    0x7f7fffff, 0xff7fffff,
])
def test_float_bitcast_finite_encoding_matches_ieee_reference(bits):
    import struct

    value = struct.unpack("<f", struct.pack("<I", bits))[0]
    expected = struct.unpack("<Q", struct.pack("<d", value))[0]
    token = decode_value_token(f"bitcast (i32 {bits} to float)")
    assert token == f"0x{expected:016X}"
    assert encode_float_bits(token, 32) == bits


@pytest.mark.parametrize("triple", [
    "x86_64-unknown-linux-gnu", "aarch64-unknown-linux-gnu",
    "arm64-apple-darwin23.6.0",
])
@pytest.mark.parametrize("bits", [
    0x7f800000, 0xff800000, 0x7fc12345, 0xffc12345,
    0x7f800001, 0xff800001,
])
def test_target_emission_matches_explicit_ieee_encoding(triple, bits):
    from pcc.backend.self_backend_dispatch import emit_self_asm

    expected = ((bits >> 31) << 63) | (2047 << 52) | ((bits & 0x7fffff) << 29)
    prefix = f'target triple = "{triple}"\ndefine float @value() {{\nentry:\n  ret float '
    suffix = '\n}\n'
    bitcast_ir = prefix + f"bitcast (i32 {bits} to float)" + suffix
    literal_ir = prefix + f"0x{expected:016X}" + suffix
    assert emit_self_asm(bitcast_ir) == emit_self_asm(literal_ir)
