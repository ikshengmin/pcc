"""Single-TU function-template executions, not full C++17/native CLI proof.

The external C++17 compiler is a separately labeled development oracle. Weak
ODR/COMDAT and class templates remain outside this executed arithmetic slice.
"""

import os
import platform
import shutil
import struct
import subprocess
import sys

import pytest

from pcc.backend.elf_x86_64 import ElfError
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
        "distinct-int-double-instances",
        "template<class T> T twice(T x){return x+x;} "
        "int cpp_test_entry(){return twice(3)+int(twice(2.5));}",
        11,
    ),
    (
        "nontemplate-wins-only-conversion-tie",
        "template<class T> int f(T x){return 11;} int f(int x){return 22;} "
        "int cpp_test_entry(){return f(1)+f(1l);}",
        33,
    ),
    (
        "dependent-call-definition-point-candidates",
        "int tag(long x){return 11;} "
        "template<class T> int choose(T x){return tag(x);} "
        "int tag(int x){return 22;} int cpp_test_entry(){return choose(3);}",
        11,
    ),
    (
        "instantiated-promotion-drives-dependent-call",
        "int tag(bool x){return 11;} int tag(int x){return 22;} "
        "template<class T> int choose(T x){return tag(+x);} "
        "int cpp_test_entry(){return choose(true);}",
        22,
    ),
    (
        "two-type-parameters-dependent-cast",
        "template<class T,class U> T combine(T x,U y){return x+T(y);} "
        "int cpp_test_entry(){return combine(3,2.5);}",
        5,
    ),
    (
        "local-alias-and-nested-value-shadowing",
        "template<class T> T f(T x){using U=T;U y=x;{int x=9;y=y+T(x);}"
        "y=y+T(1);return y;} int cpp_test_entry(){return f(2);}",
        12,
    ),
)


MULTI_TU_TEMPLATE = (
    (
        "helper.cpp",
        "template<class T> T id(T x){return x;} int helper(){return id(1);}",
    ),
    (
        "entry.cpp",
        "template<class T> T id(T x){return x;} int helper(); "
        "int cpp_test_entry(){return id(2)+helper();}",
    ),
)


def test_cpp_template_multi_tu_exposes_unimplemented_weak_odr(owned_only, tmp_path):
    output = tmp_path / "unsupported-template-odr"
    with pytest.raises(
        ElfError, match="duplicate strong definition of '_Z2idIiET_S0_'"
    ):
        artifact = compile_host_cpp(MULTI_TU_TEMPLATE)
        write_host_executable(artifact, output)
    assert not output.exists()


@pytest.mark.parametrize("name,source,value", CASES, ids=[case[0] for case in CASES])
def test_cpp_template_host_owned_artifact_is_deterministic(
    owned_only,
    name,
    source,
    value,
):
    units = (("template.cpp", source),)
    assert compile_host_cpp(units) == compile_host_cpp(units)


@pytest.mark.parametrize(
    "source,code,context",
    (
        (
            "template<class T> T add(T x,T y){return x+y;} "
            "int cpp_test_entry(){return add(1,2l);}",
            "cpp.no-viable-overload",
            "",
        ),
        (
            "template<class T> int f(T x){return x%2;} int f(long x){return 22;} "
            "int cpp_test_entry(){return f(1.5);}",
            "cpp.invalid-operands",
            "while instantiating",
        ),
        (
            "template<class T> T id(T); int cpp_test_entry(){return id(4);}",
            "cpp.template-definition",
            "",
        ),
    ),
)
def test_cpp_template_failure_is_not_an_oracle_or_lowering_fallback(
    tmp_path,
    owned_only,
    monkeypatch,
    source,
    code,
    context,
):
    from pcc.backend import owned_object_emit

    def forbidden(*args, **kwargs):
        raise AssertionError("rejected template reached the object emitter")

    monkeypatch.setattr(owned_object_emit, "emit_owned_object", forbidden)
    output = tmp_path / "rejected-template"
    with pytest.raises(CppDiagnostic) as error:
        artifact = compile_host_cpp((("rejected-template.cpp", source),))
        write_host_executable(artifact, output)
    assert error.value.code == code
    assert error.value.coord.file == "rejected-template.cpp"
    assert error.value.coord.line >= 1
    assert context in error.value.message
    assert not output.exists()


@pytest.mark.integration
@pytest.mark.parametrize("name,source,value", CASES, ids=[case[0] for case in CASES])
def test_cpp_template_host_owned_matches_explicit_cpp17_oracle(
    tmp_path,
    monkeypatch,
    name,
    source,
    value,
):
    assert sys.platform == "linux" and platform.machine() in (
        "x86_64",
        "AMD64",
    ), "the executed template adapter is Linux x86_64/LP64, not a skipped qualification"
    compiler = shutil.which(os.environ.get("PCC_CPP_ORACLE", "g++"))
    assert compiler, "explicit C++17 oracle unavailable; set PCC_CPP_ORACLE"
    units = (("template.cpp", source),)
    with monkeypatch.context() as guard:

        def forbidden(*args, **kwargs):
            raise AssertionError(
                "owned C++ template compile attempted an external process"
            )

        guard.setattr(subprocess, "Popen", forbidden)
        artifact = compile_host_cpp(units)
    binary = tmp_path / "owned"
    write_host_executable(artifact, binary)
    expected = Execution(value & 255, struct.pack("<i", value), b"")
    assert_exact(run_executable(binary), expected)
    assert_exact(run_executable(binary), expected)
    oracle = compile_reference_cpp(units, tmp_path / "reference", compiler=compiler)
    assert_exact(run_executable(oracle), expected)


@pytest.mark.integration
def test_cpp_template_multi_tu_explicit_cpp17_reference_only(tmp_path):
    assert sys.platform == "linux" and platform.machine() in (
        "x86_64",
        "AMD64",
    ), "the reference byte observer is Linux x86_64 only"
    compiler = shutil.which(os.environ.get("PCC_CPP_ORACLE", "g++"))
    assert compiler, "explicit C++17 oracle unavailable; set PCC_CPP_ORACLE"
    oracle = compile_reference_cpp(
        MULTI_TU_TEMPLATE,
        tmp_path / "reference",
        compiler=compiler,
    )
    expected = Execution(3, struct.pack("<i", 3), b"")
    assert_exact(run_executable(oracle), expected)
    assert_exact(run_executable(oracle), expected)
