"""Executed host-development C++ slice; not public CLI/pcc1 qualification."""

import builtins
import os
import platform
import shutil
import struct
import subprocess
import sys
from types import SimpleNamespace

import pytest

from pcc.backend.elf_x86_64 import ElfError
from pcc.frontends.cpp import CppDiagnostic
from tests.cpp.owned_execution import (
    Execution,
    assert_exact,
    compile_host_cpp,
    compile_reference_cpp,
    entry_definition,
    run_executable,
    write_host_executable,
)


def test_cpp_entry_adapter_handles_semantic_overload_sets():
    integer_argument = SimpleNamespace(
        defined=True,
        kind="function",
        arity=1,
        dtype="int",
        c_name="_Z14cpp_test_entryi",
    )
    no_argument = SimpleNamespace(
        defined=True,
        kind="function",
        arity=0,
        dtype="int",
        c_name="_Z14cpp_test_entryv",
    )
    assert entry_definition(
        SimpleNamespace(overloads=(integer_argument, no_argument))
    ) == ("_Z14cpp_test_entryv")


def test_cpp_entry_adapter_does_not_treat_a_declaration_as_a_definition():
    declaration = SimpleNamespace(defined=False)
    assert entry_definition(SimpleNamespace(overloads=(declaration,))) is None


def test_cpp_entry_adapter_rejects_non_int_result():
    definition = SimpleNamespace(defined=True, kind="function", arity=0, dtype="double")
    with pytest.raises(ValueError, match="int function with no parameters"):
        entry_definition(SimpleNamespace(overloads=(definition,)))


CASES = (
    (
        "declaration-versus-conversion",
        (
            (
                "entry.cpp",
                "using T=int; int cpp_test_entry(){T(a); a=7; return T(a+2);}\n",
            ),
        ),
        9,
    ),
    (
        "value-hides-type",
        (
            (
                "entry.cpp",
                "using T=int; int cpp_test_entry(){int result=3; "
                "{int T=9; T=T+2; result=T;} T(a); a=result+1; return a;}\n",
            ),
        ),
        12,
    ),
    (
        "signed-division-remainder",
        (
            (
                "entry.cpp",
                "int calc(int a,int b){return (a/b)*100+a%b;} "
                "int cpp_test_entry(){return calc(-29,6);}\n",
            ),
        ),
        -405,
    ),
    (
        "precedence-and-bitwise",
        (("entry.cpp", "int cpp_test_entry(){return (0x12340000 | 0b1010)^3*7;}\n"),),
        0x1234001F,
    ),
    (
        "two-translation-units",
        (
            (
                "helper.cpp",
                "using Count=int; int combine(Count a,Count b){return a*1000+b;}\n",
            ),
            (
                "entry.cpp",
                "int combine(int,int); int cpp_test_entry(){return combine(123,456);}\n",
            ),
        ),
        123456,
    ),
)


@pytest.fixture
def owned_only(monkeypatch):
    original_import = builtins.__import__

    def checked_import(name, *args, **kwargs):
        if name == "llvmlite" or name.startswith("llvmlite."):
            raise AssertionError("owned C++ compile imported " + name)
        return original_import(name, *args, **kwargs)

    def forbidden(*args, **kwargs):
        raise AssertionError("owned C++ compile attempted an external process")

    monkeypatch.setattr(builtins, "__import__", checked_import)
    monkeypatch.setattr(subprocess, "Popen", forbidden)


@pytest.mark.parametrize("name,units,value", CASES, ids=[case[0] for case in CASES])
def test_cpp_host_compile_is_owned_and_deterministic(owned_only, name, units, value):
    first = compile_host_cpp(units)
    second = compile_host_cpp(units)
    assert first.owner == "host-python/pcc-cpp/pcc-self/pcc-elf"
    assert first.ir == second.ir
    assert first.objects == second.objects
    assert first.executable == second.executable
    assert first.executable[:4] == b"\x7fELF"


@pytest.mark.parametrize(
    "units,message",
    (
        ((), "no translation units"),
        ((("unit.cpp", "int helper(){return 7;}"),), "does not define"),
        ((("unit.cpp", "int cpp_test_entry(int a){return a;}"),), "no parameters"),
        (
            (
                ("unit.cpp", "int cpp_test_entry(){return 7;}"),
                ("unit.cpp", "int helper(){return 1;}"),
            ),
            "duplicate",
        ),
    ),
)
def test_cpp_project_rejects_invalid_fixture_contract(owned_only, units, message):
    with pytest.raises(ValueError, match=message):
        compile_host_cpp(units)


def test_cpp_project_link_does_not_synthesize_missing_definitions(owned_only):
    with pytest.raises(ElfError, match="undefined static ELF symbols"):
        compile_host_cpp(
            (("entry.cpp", "int absent(); int cpp_test_entry(){return absent();}"),)
        )


@pytest.mark.parametrize(
    "source,code",
    (
        ("struct Guard { ~Guard(); };", "cpp.unsupported.classes"),
        ("template<class T> T value(T x){return x;}", "cpp.unsupported.templates"),
        ("int cpp_test_entry(){throw 1;}", "cpp.unsupported.exceptions"),
        ("int cpp_test_entry(){return missing;}", "cpp.undeclared"),
        (
            "#include <vector>\nint cpp_test_entry(){return 0;}",
            "cpp.unsupported.preprocessor",
        ),
    ),
)
def test_cpp_negative_compile_retains_diagnostic_and_produces_no_binary(
    tmp_path,
    owned_only,
    source,
    code,
):
    output = tmp_path / "program"
    with pytest.raises(CppDiagnostic) as error:
        artifact = compile_host_cpp((("negative.cpp", source),))
        write_host_executable(artifact, output)
    assert error.value.code == code
    assert error.value.coord.file == "negative.cpp"
    assert error.value.coord.line >= 1
    assert not output.exists()


@pytest.mark.parametrize(
    "field,actual,expected",
    (
        ("stdout", Execution(0, b"\xff", b""), Execution(0, b"\\xff", b"")),
        ("stdout", Execution(0, b"a\r\n", b""), Execution(0, b"a\n", b"")),
        ("stderr", Execution(0, b"", b"\xff"), Execution(0, b"", b"\\xff")),
        ("returncode", Execution(3, b"same", b""), Execution(0, b"same", b"")),
    ),
)
def test_cpp_exact_outcome_never_uses_display_or_exit_only_equality(
    field, actual, expected
):
    with pytest.raises(AssertionError, match=field + " mismatch"):
        assert_exact(actual, expected)


def test_cpp_real_process_capture_retains_raw_bytes(tmp_path):
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import os; os.write(1,b'\\xff\\r\\n'); "
            "os.write(2,b'\\x80'); raise SystemExit(7)",
        ],
        capture_output=True,
        text=False,
        timeout=10,
    )
    assert_exact(
        Execution(result.returncode, result.stdout, result.stderr),
        Execution(7, b"\xff\r\n", b"\x80"),
    )


def _require_linux_x86_64():
    assert sys.platform == "linux" and platform.machine() in ("x86_64", "AMD64"), (
        "this C++ execution adapter requires Linux x86_64; select a supported "
        "target explicitly rather than counting a skip as qualification"
    )


@pytest.mark.integration
@pytest.mark.parametrize("name,units,value", CASES, ids=[case[0] for case in CASES])
def test_cpp_host_owned_executable_exact_outcome(
    tmp_path, monkeypatch, name, units, value
):
    _require_linux_x86_64()
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


@pytest.mark.integration
@pytest.mark.parametrize("name,units,value", CASES, ids=[case[0] for case in CASES])
def test_cpp_host_owned_matches_explicit_cpp17_oracle(tmp_path, name, units, value):
    _require_linux_x86_64()
    compiler = os.environ.get("PCC_CPP_ORACLE", "g++")
    compiler = shutil.which(compiler)
    assert compiler, "explicit development oracle unavailable; set PCC_CPP_ORACLE"
    artifact = compile_host_cpp(units)
    owned = tmp_path / "owned"
    write_host_executable(artifact, owned)
    reference = compile_reference_cpp(units, tmp_path / "reference", compiler=compiler)
    expected = Execution(value & 255, struct.pack("<i", value), b"")
    reference_result = run_executable(reference)
    assert_exact(reference_result, expected)
    assert_exact(run_executable(owned), reference_result)
