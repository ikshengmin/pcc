"""Public grouped-name semantics and actual host-owned emitted execution.

This is not pointer/reference execution or a native pcc1 compiler gate. The
reference compiler is invoked only by the separately labeled integration test.
"""

import builtins
import os
import platform
import shutil
import struct
import subprocess
import sys

import pytest

from pcc.frontends.cpp import CppDiagnostic, CppParser
from tests.cpp.owned_execution import (
    Execution,
    assert_exact,
    compile_host_cpp,
    compile_reference_cpp,
    run_executable,
    write_host_executable,
)


CASES = (
    (
        "function-and-parameter-grouping",
        (("entry.cpp", "int (((combine)))(int ((left)), int (right)){return left*100+right;} "
          "int cpp_test_entry(){int ((value))(4); return combine(value,7);}"),),
        407,
    ),
    (
        "prototype-and-definition-grouping",
        (("entry.cpp", "long ((advance))(long); long (advance)(long (value)){return value+3;} "
          "int cpp_test_entry(){return advance(40l);}"),),
        43,
    ),
    (
        "typedef-name-grouping",
        (("entry.cpp", "typedef int ((Count)); int (bump)(Count (value)){return value+1;} "
          "int cpp_test_entry(){Count ((result))(41); return bump(result);}"),),
        42,
    ),
    (
        "grouping-keeps-lexical-type-hiding",
        (("entry.cpp", "using T=int; int cpp_test_entry(){T ((value)); value=7; "
          "{int (T)(3); value=value+T;} return T(value+2);}"),),
        12,
    ),
    (
        "grouping-keeps-overload-identity",
        (("entry.cpp", "int ((choose))(int (value)){return value+10;} "
          "int (choose)(long ((value))){return value+20;} "
          "int cpp_test_entry(){return choose(2)+choose(3l);}"),),
        35,
    ),
    (
        "grouping-across-translation-units",
        (
            ("helper.cpp", "int ((combine))(int (left), int ((right))){return left*100+right;}"),
            ("entry.cpp", "int (combine)(int,int); "
             "int ((cpp_test_entry))(){return combine(12,34);}"),
        ),
        1234,
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
def test_grouped_cpp_names_owned_compile_is_deterministic(owned_only, name, units, value):
    first = compile_host_cpp(units)
    second = compile_host_cpp(units)
    assert first.owner == "host-python/pcc-cpp/pcc-self/pcc-elf"
    assert first.ir == second.ir
    assert first.objects == second.objects
    assert first.executable == second.executable
    assert first.executable[:4] == b"\x7fELF"


def test_grouped_parameters_keep_type_identity_and_parameter_scope():
    parser = CppParser()
    ast = parser.parse(
        "using T=int; int ((f))(T ((value))); int (f)(int (value)){return value;}",
        "grouping.cpp",
    )
    assert ast.ext[0].name == ast.ext[1].decl.name == "_Z1fi"
    assert len(parser.scope.lookup("f").overloads) == 1
    assert ast.ext[1].decl.type.args.params[0].name == "value"
    assert ast.ext[1].body.block_items[0].expr.name == "value"


@pytest.mark.parametrize(
    "source,code",
    (
        ("int (*function)();", "cpp.unsupported.function-pointers"),
        ("int (&function)();", "cpp.unsupported.references"),
        ("int (&&function)();", "cpp.unsupported.references"),
        ("int Scope::function();", "cpp.unsupported.qualified-names"),
        ("int (::function)();", "cpp.unsupported.qualified-names"),
        ("int function(int (**value));", "cpp.unsupported.multilevel-pointers"),
        ("int function(int (&value));", "cpp.unsupported.references"),
        ("typedef int (**Pointer);", "cpp.unsupported.multilevel-pointers"),
        ("typedef int (&Reference);", "cpp.unsupported.references"),
        ("using T=int; int function(int ((T)), T value);", "cpp.not-a-type"),
        ("int function(int ((value)), int (value));", "cpp.redeclaration"),
        ("using (T)=int;", "cpp.unsupported.syntax"),
    ),
)
def test_grouped_names_never_drop_unimplemented_or_invalid_semantics(
    tmp_path, owned_only, source, code
):
    output = tmp_path / "program"
    with pytest.raises(CppDiagnostic) as error:
        artifact = compile_host_cpp((("negative-grouping.cpp", source),))
        write_host_executable(artifact, output)
    assert error.value.code == code
    assert error.value.coord.file == "negative-grouping.cpp"
    assert error.value.coord.line == 1
    assert not output.exists()


@pytest.mark.integration
@pytest.mark.parametrize("name,units,value", CASES, ids=[case[0] for case in CASES])
def test_grouped_cpp_names_owned_execution_matches_explicit_cpp17_oracle(
    tmp_path, monkeypatch, name, units, value
):
    assert sys.platform == "linux" and platform.machine() in ("x86_64", "AMD64"), (
        "this host-owned execution adapter requires Linux x86_64; an unrun "
        "platform must not count as a qualification pass"
    )
    compiler = shutil.which(os.environ.get("PCC_CPP_ORACLE", "g++"))
    assert compiler, "explicit development oracle unavailable; set PCC_CPP_ORACLE"
    with monkeypatch.context() as guard:

        def forbidden(*args, **kwargs):
            raise AssertionError("owned C++ compile attempted an external process")

        guard.setattr(subprocess, "Popen", forbidden)
        artifact = compile_host_cpp(units)
    owned = tmp_path / "owned"
    write_host_executable(artifact, owned)
    reference = compile_reference_cpp(units, tmp_path / "reference", compiler=compiler)
    expected = Execution(value & 255, struct.pack("<i", value), b"")
    reference_result = run_executable(reference)
    assert_exact(reference_result, expected)
    assert_exact(run_executable(owned), reference_result)
    assert_exact(run_executable(owned), reference_result)
