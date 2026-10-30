"""Scalar control-flow programs: host-owned ELF versus explicit C++17 oracle."""

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
    assert_exact,
    compile_host_cpp,
    compile_reference_cpp,
    run_executable,
    write_host_executable,
)
from tests.cpp.test_cpp_owned_execution import owned_only


CASES = (
    (
        "cpp-bool-not-c-int-overload-results",
        "int tag(bool x){return 11;} int tag(int x){return 22;} "
        "int cpp_test_entry(){return tag(1<2)+tag(1==2)+tag(!1)+tag(1&&2);}",
        44,
    ),
    (
        "short-circuit-skips-and-preserves-rhs-effects",
        "int cpp_test_entry(){int x=0;false&&(x=5);true||(x=7);"
        "1&&(x=x+2);0||(x=x+3);return x;}",
        5,
    ),
    (
        "while-continue-and-break",
        "int cpp_test_entry(){int x=0;int sum=0;while(x<7){x=x+1;"
        "if(x==2)continue;if(x==5)break;sum=sum+x;}return sum*10+x;}",
        85,
    ),
    (
        "dangling-else-nearest-if",
        "int cpp_test_entry(){if(1)if(0)return 11;else return 22;return 33;}",
        22,
    ),
    (
        "unbraced-selection-scope",
        "int cpp_test_entry(){int x=7;if(true)int x=2;else int x=3;return x;}",
        7,
    ),
    (
        "instantiated-loop-and-bool-overload",
        "int tag(bool x){return 11;} int tag(int x){return 22;} "
        "template<class T> int count(T n){T x=0;while(x<n){x=x+T(1);"
        "if(x==T(2))continue;if(x==T(5))break;}return tag(x<T(8))+x;} "
        "int cpp_test_entry(){return count(8);}",
        16,
    ),
    (
        "shift-independent-integral-promotions",
        "int tag(int x){return 11;} int tag(long x){return 22;} "
        "int cpp_test_entry(){short x=3;return tag(x<<2l)+(x<<2l)+(32u>>2);}",
        31,
    ),
    (
        "floating-contextual-bool-and-negative-zero",
        "int cpp_test_entry(){if(-0.0)return 11;if(0.5)return 42;else return 33;}",
        42,
    ),
    (
        "bool-values-zero-extend-through-calls",
        "bool identity(bool x){return x;} int widen(bool x){return x;} "
        "int cpp_test_entry(){bool value=identity(true);"
        "return int(value)+widen(value)*2+int(identity(false))*4;}",
        3,
    ),
    (
        "bool-values-convert-to-positive-floating",
        "double widen(bool x){return x;} "
        "int cpp_test_entry(){bool value=true;return int(widen(value)*10.0)"
        "+int(double(value)*20.0)+int(float(value)*4.0);}",
        34,
    ),
    (
        "floating-bool-initialization-call-and-return",
        "bool identity(bool x){return x;} bool from(double x){return x;} "
        "int cpp_test_entry(){bool value=0.5;bool neg=-0.25;"
        "value=identity(value);return int(value)+int(neg)*2"
        "+int(from(0.5))*4+int(from(-0.0))*8;}",
        7,
    ),
    (
        "floating-bool-nan-infinities-and-signed-zeros",
        "bool truth(double x){return bool(x);} int cpp_test_entry(){"
        "double zero=0.0;double nan=zero/zero;double infinity=1.0/zero;"
        "return int(truth(nan))+int(truth(infinity))*2"
        "+int(truth(-infinity))*4+int(truth(zero))*8+int(truth(-zero))*16;}",
        7,
    ),
    (
        "integral-bool-normalization-not-low-bit-truncation",
        "bool truth(int x){return bool(x);} int cpp_test_entry(){"
        "return int(truth(2))+int(truth(-2))*2+int(truth(0))*4;}",
        3,
    ),
    (
        "comparison-signed-unsigned-common-type",
        "int cpp_test_entry(){return int(-1<1u)+int(1u<2l)*2;}",
        2,
    ),
    (
        "alternative-operator-tokens",
        "int cpp_test_entry(){if(not false and (3 bitand 1) not_eq 0)"
        "return (8>>1) xor 1;return 0;}",
        5,
    ),
)


@pytest.mark.parametrize("name,source,value", CASES, ids=[case[0] for case in CASES])
def test_cpp_control_host_owned_artifact_is_deterministic(owned_only, name, source, value):
    units = (("control.cpp", source),)
    assert compile_host_cpp(units) == compile_host_cpp(units)


@pytest.mark.parametrize(
    "source,code",
    (
        ("int cpp_test_entry(){if(int x=2)return x;return 0;}",
         "cpp.unsupported.condition-declaration"),
        ("int cpp_test_entry(){if constexpr(true)return 1;return 0;}",
         "cpp.unsupported.syntax"),
        ("void f(){}int cpp_test_entry(){if(f())return 1;return 0;}", "cpp.void-value"),
        ("int cpp_test_entry(){break;return 0;}", "cpp.jump-outside-loop"),
        ("int cpp_test_entry(){if(true)int x=2;return x;}", "cpp.undeclared"),
    ),
)
def test_cpp_control_rejection_precedes_object_emission(
    owned_only, tmp_path, monkeypatch, source, code,
):
    from pcc.backend import owned_object_emit

    def forbidden(*args, **kwargs):
        raise AssertionError("invalid control flow reached object emission")

    monkeypatch.setattr(owned_object_emit, "emit_owned_object", forbidden)
    destination = tmp_path / "invalid-control"
    with pytest.raises(CppDiagnostic) as error:
        artifact = compile_host_cpp((("invalid-control.cpp", source),))
        write_host_executable(artifact, destination)
    assert error.value.code == code
    assert error.value.coord.file == "invalid-control.cpp"
    assert not destination.exists()


@pytest.mark.integration
@pytest.mark.parametrize("name,source,value", CASES, ids=[case[0] for case in CASES])
def test_cpp_control_owned_execution_matches_explicit_cpp17_reference(
    tmp_path, monkeypatch, name, source, value,
):
    assert sys.platform == "linux" and platform.machine() in ("x86_64", "AMD64"), (
        "this execution adapter requires Linux x86_64; not a skipped qualification"
    )
    compiler = shutil.which(os.environ.get("PCC_CPP_ORACLE", "g++"))
    assert compiler, "explicit C++17 oracle unavailable; set PCC_CPP_ORACLE"
    units = (("control.cpp", source),)
    with monkeypatch.context() as guard:
        def forbidden(*args, **kwargs):
            raise AssertionError("owned C++ control-flow compile launched an external process")

        guard.setattr(subprocess, "Popen", forbidden)
        artifact = compile_host_cpp(units)
    binary = tmp_path / "owned"
    write_host_executable(artifact, binary)
    expected = Execution(value & 255, struct.pack("<i", value), b"")
    assert_exact(run_executable(binary), expected)
    assert_exact(run_executable(binary), expected)
    oracle = compile_reference_cpp(units, tmp_path / "reference", compiler=compiler)
    assert_exact(run_executable(oracle), expected)
