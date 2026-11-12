"""The stdlib ``struct`` provider's float-bit conversions match the compiler's.

``pcc/stdlib/_pcc_float_bits.py`` cannot import ``pcc.ir.support`` (a compiled
program's module closure has no compiler packages), so it carries its own copy
of the IEEE 754 conversions in ``pcc/ir/support/_float_bits.py``. Its docstring
asked readers to keep the two in step; this test is what holds them there.
A drift would make ``struct.pack('d', x)`` in a compiled program disagree with
the constants the compiler folds for the same ``x``.

IEEE 754-2019 section 3.4 (binary interchange formats) defines the bit layouts.
"""

from __future__ import annotations

import ast
import inspect
import math
import struct

import pytest

import pcc.ir.support._float_bits as compiler_copy
import pcc.stdlib._pcc_float_bits as provider_copy

SHARED = (
    "_bits_to_float16",
    "_bits_to_float32",
    "_bits_to_float64",
    "_float16_to_bits",
    "_float32_to_bits",
    "_float64_to_bits",
    "_narrow_bits_to_float64",
    "_narrow_float_to_bits",
    "_round_shift_to_even",
    "_sign_bit",
)

VALUES = (
    0.0, -0.0, 1.0, -1.0, 0.1, 1.5, 65504.0, 65520.0, 3.4028234663852886e38,
    1e-45, 5e-324, 2.2250738585072014e-308, 1e308, math.inf, -math.inf,
)


def _source_ast(module, name):
    return ast.dump(ast.parse(inspect.getsource(getattr(module, name))))


@pytest.mark.parametrize("name", SHARED)
def test_provider_copy_is_source_identical_to_the_compiler_copy(name):
    assert _source_ast(provider_copy, name) == _source_ast(compiler_copy, name), (
        f"{name} drifted between pcc/stdlib/_pcc_float_bits.py and "
        "pcc/ir/support/_float_bits.py"
    )


def test_every_provider_function_is_covered():
    """A new provider function must be added to SHARED or justified here."""
    own = {
        name
        for name, value in vars(provider_copy).items()
        if inspect.isfunction(value) and value.__module__ == provider_copy.__name__
    }
    assert own == set(SHARED)


@pytest.mark.parametrize("value", VALUES)
def test_both_copies_produce_the_ieee_bits_struct_produces(value):
    expected64 = struct.unpack("<Q", struct.pack("<d", value))[0]
    assert compiler_copy._float64_to_bits(value) == expected64
    assert provider_copy._float64_to_bits(value) == expected64
    expected32 = struct.unpack("<I", struct.pack("<f", value))[0] if abs(value) <= 3.4028234663852886e38 or math.isinf(value) else None
    if expected32 is not None:
        assert compiler_copy._float32_to_bits(value) == expected32
        assert provider_copy._float32_to_bits(value) == expected32
    assert provider_copy._bits_to_float64(expected64) == compiler_copy._bits_to_float64(expected64)


def test_nan_round_trips_through_both_copies():
    bits = struct.unpack("<Q", struct.pack("<d", math.nan))[0]
    assert math.isnan(compiler_copy._bits_to_float64(bits))
    assert math.isnan(provider_copy._bits_to_float64(bits))
