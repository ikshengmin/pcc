"""Executed arithmetic-overload choices, with a separately named C++17 oracle.

These are host frontend -> owned target-code executions, not a pcc1 C++ gate.
The original long-double-candidate TU is retained as a capability-negative
case: it cannot be compiled yet, even when that overload would not be selected.
That gap is not counted as successful owned C++17 program execution.
"""

import os
import platform
import shutil
import struct
import subprocess
import sys

import pytest

from pcc.frontends.cpp import CppDiagnostic

from tests.cpp.owned_execution import (
    Execution,
    TARGET,
    assert_exact,
    compile_host_cpp,
    compile_reference_cpp,
    run_executable,
    write_host_executable,
)
from tests.cpp.test_cpp_owned_execution import owned_only

CASES = (
    (
        "short-promotion-and-long-identity",
        "int f(int x){return 11;} int f(long x){return 22;} "
        "int cpp_test_entry(){short s=2;return f(s)+f(1l);}",
        33,
    ),
    (
        "float-promotion-beats-integer-conversion",
        "int f(double x){return 11;} int f(long x){return 22;} "
        "int cpp_test_entry(){return f(2.5f);}",
        11,
    ),
    (
        "equal-width-long-long-remains-distinct",
        "int f(long x){return 11;} int f(long long x){return 22;} "
        "int cpp_test_entry(){return f(1ll);}",
        22,
    ),
    (
        "bool-literals-select-bool",
        "int f(bool x){return 11;} int f(int x){return 22;} "
        "int cpp_test_entry(){return f(true)+f(false)+f(1);}",
        44,
    ),
    (
        "unsigned-int-plus-long-is-long-lp64",
        "int f(unsigned int x){return 11;} int f(long x){return 22;} "
        "int cpp_test_entry(){unsigned int x=1u;long y=2l;return f(x+y);}",
        22,
    ),
)


EXTENDED_FLOAT_SOURCE = (
    "int f(double x){return 11;} int f(long double x){return 22;} "
    "int cpp_test_entry(){return f(2.5f);}"
)


def test_cpp_host_parser_uses_the_owned_emitter_target(owned_only, monkeypatch):
    from pcc.frontends import cpp

    original_parser = cpp.CppParser
    targets = []

    def selected_parser(*, target_triple):
        targets.append(target_triple)
        return original_parser(target_triple=target_triple)

    monkeypatch.setattr(cpp, "CppParser", selected_parser)
    artifact = compile_host_cpp((("entry.cpp", "int cpp_test_entry(){return 7;}"),))
    assert targets == [TARGET]
    assert f'target triple = "{TARGET}"' in artifact.ir[0]


def test_cpp_extended_float_candidate_rejects_before_unsupported_backend_lowering(
    owned_only,
    monkeypatch,
):
    from pcc.backend import owned_object_emit

    def forbidden(*args, **kwargs):
        raise AssertionError(
            "unsupported extended-float ABI reached the object emitter"
        )

    monkeypatch.setattr(owned_object_emit, "emit_owned_object", forbidden)
    with pytest.raises(CppDiagnostic) as error:
        compile_host_cpp((("unsupported-extended-float.cpp", EXTENDED_FLOAT_SOURCE),))
    assert error.value.code == "cpp.unsupported.extended-float"
    assert error.value.coord.file == "unsupported-extended-float.cpp"


@pytest.mark.parametrize("name,source,value", CASES, ids=[case[0] for case in CASES])
def test_cpp_overload_host_owned_artifact_is_deterministic(
    owned_only,
    name,
    source,
    value,
):
    first = compile_host_cpp((("overload.cpp", source),))
    second = compile_host_cpp((("overload.cpp", source),))
    assert first == second


@pytest.mark.integration
@pytest.mark.parametrize("name,source,value", CASES, ids=[case[0] for case in CASES])
def test_cpp_overload_host_owned_matches_explicit_cpp17_oracle(
    tmp_path,
    monkeypatch,
    name,
    source,
    value,
):
    assert sys.platform == "linux" and platform.machine() in (
        "x86_64",
        "AMD64",
    ), "the executed overload adapter is Linux x86_64/LP64; not a skipped qualification"
    compiler = shutil.which(os.environ.get("PCC_CPP_ORACLE", "g++"))
    assert compiler, "explicit C++17 oracle unavailable; set PCC_CPP_ORACLE"
    units = (("overload.cpp", source),)
    with monkeypatch.context() as guard:

        def forbidden(*args, **kwargs):
            raise AssertionError("owned C++ compile attempted an external process")

        guard.setattr(subprocess, "Popen", forbidden)
        artifact = compile_host_cpp(units)
    binary = tmp_path / "owned"
    write_host_executable(artifact, binary)
    expected = Execution(value & 255, struct.pack("<i", value), b"")
    assert_exact(run_executable(binary), expected)
    assert_exact(run_executable(binary), expected)
    oracle = compile_reference_cpp(units, tmp_path / "reference", compiler=compiler)
    assert_exact(run_executable(oracle), expected)
