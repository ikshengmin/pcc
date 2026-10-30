"""Runtime-free signed conversions through the owned x86/ELF pipeline.

This is host-driven emitted-code evidence, not native-pcc1, SQLite, managed
runtime, bootstrap fixed-point or performance qualification.
"""

import struct

import pytest

from tests.c.test_self_backend_x86_64_aggregate_stores import (
    NATIVE_X86,
    _run_owned_program,
)


WIDTH_GROUPS = [
    pytest.param((8,), id="i8"),
    pytest.param((16,), id="i16"),
    pytest.param((1, 7, 9, 17, 24, 31), id="logical-widths"),
    pytest.param((32, 64), id="unchanged-widths"),
]


def _signed_boundaries(width):
    if width == 1:
        # The one-bit signed integer's true/all-ones value is -1, not +1.
        return (0, -1)
    return (0, 1, -1, -(1 << (width - 1)), (1 << (width - 1)) - 1)


def _expected_float_bits(value, fp_width):
    # Independent standard-library IEEE encoding; no compiler cast helpers.
    # The i64 boundaries are exact powers of two or adjacent all-ones values,
    # so host binary64 -> binary32 does not introduce double-rounding cases.
    encoded = struct.pack("<f" if fp_width == 32 else "<d", value)
    bits = int.from_bytes(encoded, "little")
    return bits if bits < (1 << (fp_width - 1)) else bits - (1 << fp_width)


def _signed_to_float_program(widths, source, fp_width):
    fp_type = "float" if fp_width == 32 else "double"
    lines = ["define i32 @main() {", "entry:"]
    index = 0
    for width in widths:
        for signed_value in _signed_boundaries(width):
            # Canonical low-bit patterns make the logical sign bit observable:
            # e.g. i7 64 and i1 1 must convert to -64.0 and -1.0 respectively.
            literal = str(signed_value & ((1 << width) - 1))
            value = literal
            if source == "load":
                lines.extend([
                    f"  %slot{index} = alloca i{width}",
                    f"  store i{width} {literal}, ptr %slot{index}",
                    f"  %value{index} = load i{width}, ptr %slot{index}",
                ])
                value = f"%value{index}"
            else:
                assert source == "literal"
            expected = _expected_float_bits(signed_value, fp_width)
            lines.extend([
                f"  %fp{index} = sitofp i{width} {value} to {fp_type}",
                f"  %bits{index} = bitcast {fp_type} %fp{index} to i{fp_width}",
                f"  %eq{index} = icmp eq i{fp_width} %bits{index}, {expected}",
            ])
            if index:
                previous = "%eq0" if index == 1 else f"%ok{index - 1}"
                lines.append(f"  %ok{index} = and i1 {previous}, %eq{index}")
            index += 1
    lines.extend([
        f"  %status = select i1 %ok{index - 1}, i32 0, i32 91",
        "  ret i32 %status",
        "}",
    ])
    return "\n".join(lines)


@NATIVE_X86
@pytest.mark.parametrize("widths", WIDTH_GROUPS)
@pytest.mark.parametrize("source", ["literal", "load"])
@pytest.mark.parametrize("fp_width", [32, 64], ids=["float", "double"])
def test_owned_signed_integer_to_float(tmp_path, widths, source, fp_width):
    _run_owned_program(
        _signed_to_float_program(widths, source, fp_width), tmp_path,
    )
