"""Executed scalar void/return effects, not native pcc1 C++ qualification."""

import os
import platform
import shutil
import struct
import subprocess
import sys

import pytest

from pcc.backend.elf_x86_64 import ElfError
from pcc.frontends.cpp import CppDiagnostic
from tests.cpp.abi_observer import DECLARATIONS, OWNED_ASSEMBLY, REFERENCE_UNIT, RESULT
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
        "explicit-void-discard-keeps-assignment",
        "int cpp_test_entry(){int x=2;void(x=x+3);return x;}",
        5,
        False,
    ),
    (
        "dependent-void-discard-keeps-assignment",
        "template<class T> T f(T x){void(x=x+T(3));return x;} "
        "int cpp_test_entry(){return f(2);}",
        5,
        False,
    ),
    (
        "void-return-call-executes-once",
        DECLARATIONS + "void forward(){return cpp_observe(7);} "
        "int cpp_test_entry(){forward();return " + RESULT + ";}",
        1007,
        True,
    ),
    (
        "template-void-return-call-executes-once",
        DECLARATIONS + "template<class T> void forward(T x){return cpp_observe(x);} "
        "int cpp_test_entry(){forward(7);return " + RESULT + ";}",
        1007,
        True,
    ),
    (
        "empty-return-stops-following-call",
        DECLARATIONS + "void done(){cpp_observe(3);return;cpp_observe(9);} "
        "int cpp_test_entry(){done();return " + RESULT + ";}",
        1003,
        True,
    ),
    (
        "dependent-discarded-void-call-is-not-erased",
        DECLARATIONS
        + "template<class T> void forward(T x){return void(cpp_observe(x));} "
        "int cpp_test_entry(){forward(8);return " + RESULT + ";}",
        1008,
        True,
    ),
    (
        "two-forwarded-calls-retain-count-and-last-value",
        DECLARATIONS + "void forward(int x){return cpp_observe(x);} "
        "int cpp_test_entry(){forward(4);forward(7);return " + RESULT + ";}",
        2007,
        True,
    ),
    (
        "void-alias-parameter-and-implicit-return",
        DECLARATIONS + "using V=void;void done(V){cpp_observe(6);} "
        "int cpp_test_entry(){done();return " + RESULT + ";}",
        1006,
        True,
    ),
)


@pytest.mark.parametrize(
    "name,source,value,observe", CASES, ids=[case[0] for case in CASES]
)
def test_cpp_void_owned_artifact_is_deterministic(
    owned_only, name, source, value, observe
):
    units = (("void.cpp", source),)
    support = (OWNED_ASSEMBLY,) if observe else ()
    first = compile_host_cpp(units, owned_support_assembly=support)
    second = compile_host_cpp(units, owned_support_assembly=support)
    assert first == second
    assert len(first.ir) == 1
    assert len(first.objects) == 1 + int(observe)


def test_cpp_void_observer_is_not_implicitly_synthesized(owned_only, tmp_path):
    output = tmp_path / "missing-observer"
    source = (
        DECLARATIONS + "int cpp_test_entry(){cpp_observe(7);return " + RESULT + ";}"
    )
    with pytest.raises(ElfError, match="undefined static ELF symbols"):
        artifact = compile_host_cpp((("observer.cpp", source),))
        write_host_executable(artifact, output)
    assert not output.exists()


def test_cpp_owned_support_cannot_override_a_source_definition(owned_only, tmp_path):
    output = tmp_path / "duplicate-observer"
    source = "int cpp_count(){return 3;} int cpp_test_entry(){return cpp_count();}"
    with pytest.raises(
        ElfError, match="duplicate strong definition of '_Z9cpp_countv'"
    ):
        artifact = compile_host_cpp(
            (("observer.cpp", source),), owned_support_assembly=(OWNED_ASSEMBLY,)
        )
        write_host_executable(artifact, output)
    assert not output.exists()


@pytest.mark.parametrize(
    "source,code,context",
    (
        ("int cpp_test_entry(){return;}", "cpp.return-value-required", ""),
        (
            "void bad(){return 1;} int cpp_test_entry(){return 0;}",
            "cpp.return-value-in-void",
            "",
        ),
        (
            "void empty(){} int cpp_test_entry(){return empty();}",
            "cpp.return-void-expression",
            "",
        ),
        (
            "template<class T> void f(T x){return x;} void f(long x){} "
            "int cpp_test_entry(){f(7);return 0;}",
            "cpp.return-value-in-void",
            "while instantiating",
        ),
        (
            "void empty(){} int take(int x){return x;} "
            "int cpp_test_entry(){return take(empty());}",
            "cpp.void-value",
            "",
        ),
    ),
)
def test_cpp_void_diagnostic_precedes_emission_and_publishes_no_binary(
    owned_only,
    tmp_path,
    monkeypatch,
    source,
    code,
    context,
):
    from pcc.backend import owned_object_emit

    def forbidden(*args, **kwargs):
        raise AssertionError("invalid void expression reached object emission")

    monkeypatch.setattr(owned_object_emit, "emit_owned_object", forbidden)
    output = tmp_path / "invalid-void"
    with pytest.raises(CppDiagnostic) as error:
        artifact = compile_host_cpp((("invalid-void.cpp", source),))
        write_host_executable(artifact, output)
    assert error.value.code == code
    assert error.value.coord.file == "invalid-void.cpp"
    assert error.value.coord.line >= 1
    assert context in error.value.message
    assert not output.exists()


@pytest.mark.integration
@pytest.mark.parametrize(
    "name,source,value,observe", CASES, ids=[case[0] for case in CASES]
)
def test_cpp_void_effects_match_explicit_cpp17_reference(
    tmp_path, monkeypatch, name, source, value, observe
):
    assert sys.platform == "linux" and platform.machine() in (
        "x86_64",
        "AMD64",
    ), "this scalar ABI observer executes Linux x86_64; not a skipped qualification"
    compiler = shutil.which(os.environ.get("PCC_CPP_ORACLE", "g++"))
    assert compiler, "explicit C++17 oracle unavailable; set PCC_CPP_ORACLE"
    units = (("void.cpp", source),)
    support = (OWNED_ASSEMBLY,) if observe else ()
    with monkeypatch.context() as guard:

        def forbidden(*args, **kwargs):
            raise AssertionError("owned void compile launched an external process")

        guard.setattr(subprocess, "Popen", forbidden)
        artifact = compile_host_cpp(units, owned_support_assembly=support)
    binary = tmp_path / "owned"
    write_host_executable(artifact, binary)
    expected = Execution(value & 255, struct.pack("<i", value), b"")
    assert_exact(run_executable(binary), expected)
    assert_exact(run_executable(binary), expected)
    reference_units = units + ((REFERENCE_UNIT,) if observe else ())
    oracle = compile_reference_cpp(
        reference_units, tmp_path / "reference", compiler=compiler
    )
    assert_exact(run_executable(oracle), expected)
