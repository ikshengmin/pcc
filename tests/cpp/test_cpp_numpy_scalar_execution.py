"""Exact NumPy source-function fixture, not a NumPy TU or package build proof."""

import os
from pathlib import Path
import platform
import shutil
import struct
import subprocess
import sys

import pytest

from pcc.frontends.cpp import CppParser
from pcc.frontends.cpp.types import ArithmeticTypes
from tests.cpp.abi_stream import DECLARATION, OWNED_ASSEMBLY, REFERENCE_UNIT
from tests.cpp.owned_execution import (
    Execution,
    assert_exact,
    compile_host_cpp,
    compile_reference_cpp,
    run_executable,
    write_host_executable,
)
from tests.cpp.test_cpp_owned_execution import owned_only

TARGET = "x86_64-unknown-linux-gnu"
FIXTURE = Path(__file__).parent / "fixtures/numpy-2.4.4/halffloat_scalar.cpp"
DRIVER = """
int cpp_test_entry()
{
    unsigned int h = 0;
    while (h < 65536u) {
        cpp_emit(npy_half_iszero(h));
        cpp_emit(npy_half_isinf(h));
        cpp_emit(npy_half_isfinite(h));
        cpp_emit(npy_half_signbit(h));
        cpp_emit(npy_half_copysign(h, 0x8000u));
        cpp_emit(npy_half_copysign(h, 0u));
        h = h + 1;
    }
    return 0;
}
"""


def expected_observations():
    """Independent integer/range predicates, not aggregate result counts."""
    records = []
    for half in range(65536):
        zero = int(half in (0, 32768))
        infinite = int(half in (31744, 64512))
        finite = int(not (31744 <= half < 32768 or 64512 <= half < 65536))
        negative = int(half >= 32768)
        negative_copy = half if negative else half + 32768
        positive_copy = half - 32768 if negative else half
        records.append(
            struct.pack(
                "<6i", zero, infinite, finite, negative, negative_copy, positive_copy
            )
        )
    # The existing startup/reference driver also writes cpp_test_entry's int.
    return b"".join(records) + struct.pack("<i", 0)


def source_units():
    assert ArithmeticTypes(TARGET).width("unsigned short") == 16
    return (
        (
            "numpy-halffloat-scalar.cpp",
            DECLARATION
            + "typedef unsigned short npy_half;\n"
            + FIXTURE.read_text()
            + DRIVER,
        ),
    )


def test_numpy_scalar_fixture_retains_all_five_real_function_definitions():
    parser = CppParser(TARGET)
    parser.parse(source_units()[0][1], "numpy-halffloat-scalar.cpp")
    for name in (
        "npy_half_iszero",
        "npy_half_isinf",
        "npy_half_isfinite",
        "npy_half_signbit",
        "npy_half_copysign",
    ):
        candidates = parser.scope.lookup(name).overloads
        assert len(candidates) == 1 and candidates[0].defined
        assert candidates[0].parameters[0] == "unsigned short"
    assert (
        parser.scope.lookup("npy_half_copysign").overloads[0].dtype == "unsigned short"
    )


def test_numpy_scalar_host_owned_output_is_deterministic(owned_only):
    assert compile_host_cpp(
        source_units(), owned_support_assembly=(OWNED_ASSEMBLY,)
    ) == compile_host_cpp(source_units(), owned_support_assembly=(OWNED_ASSEMBLY,))


@pytest.mark.integration
def test_numpy_scalar_all_half_patterns_owned_execution_and_cpp17_reference(
    tmp_path, monkeypatch
):
    assert sys.platform == "linux" and platform.machine() in (
        "x86_64",
        "AMD64",
    ), "this fixture uses an explicit Linux x86_64/LP64 type profile"
    compiler = shutil.which(os.environ.get("PCC_CPP_ORACLE", "g++"))
    assert compiler, "explicit C++17 oracle unavailable; set PCC_CPP_ORACLE"
    units = source_units()
    with monkeypatch.context() as guard:

        def forbidden(*args, **kwargs):
            raise AssertionError(
                "owned NumPy scalar compile launched an external process"
            )

        guard.setattr(subprocess, "Popen", forbidden)
        artifact = compile_host_cpp(units, owned_support_assembly=(OWNED_ASSEMBLY,))
    binary = tmp_path / "owned"
    write_host_executable(artifact, binary)
    expected = Execution(0, expected_observations(), b"")
    assert_exact(run_executable(binary), expected)
    assert_exact(run_executable(binary), expected)
    oracle = compile_reference_cpp(
        units + (REFERENCE_UNIT,), tmp_path / "reference", compiler=compiler
    )
    assert_exact(run_executable(oracle), expected)


@pytest.mark.integration
def test_cpp_stream_observer_preserves_signed_and_full_width_bytes(
    tmp_path, monkeypatch
):
    assert sys.platform == "linux" and platform.machine() in (
        "x86_64",
        "AMD64",
    ), "the raw scalar stream observer executes Linux x86_64 only"
    compiler = shutil.which(os.environ.get("PCC_CPP_ORACLE", "g++"))
    assert compiler, "explicit C++17 oracle unavailable; set PCC_CPP_ORACLE"
    units = (
        (
            "stream.cpp",
            DECLARATION
            + "int cpp_test_entry(){cpp_emit(-7);cpp_emit(0x12345678);return 42;}",
        ),
    )
    with monkeypatch.context() as guard:

        def forbidden(*args, **kwargs):
            raise AssertionError(
                "owned scalar stream compile launched an external process"
            )

        guard.setattr(subprocess, "Popen", forbidden)
        artifact = compile_host_cpp(units, owned_support_assembly=(OWNED_ASSEMBLY,))
    binary = tmp_path / "owned"
    write_host_executable(artifact, binary)
    expected = Execution(42, struct.pack("<3i", -7, 0x12345678, 42), b"")
    assert_exact(run_executable(binary), expected)
    oracle = compile_reference_cpp(
        units + (REFERENCE_UNIT,), tmp_path / "reference", compiler=compiler
    )
    assert_exact(run_executable(oracle), expected)
