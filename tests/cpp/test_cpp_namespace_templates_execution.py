"""Same-input native-output/reference obligations; authored and UNRUN locally.

Specification/source identities live in test_cpp_namespace_templates. This
host-PCC-owned ELF lane is not native pcc1 or multi-TU weak-ODR qualification.
"""

import os
import re
import shutil
import struct
import subprocess

import pytest

from tests.cpp.owned_execution import (
    Execution, assert_exact, compile_host_cpp, compile_reference_cpp, run_executable,
)
from tests.cpp.sizeof_execution import execute_row_pair
from tests.cpp.test_cpp_namespace_templates import (
    CASES, DEFERRED, INVALID, MULTI_TU_NAMED_TEMPLATE,
)


@pytest.mark.integration
@pytest.mark.parametrize("name,units", CASES, ids=[row[0] for row in CASES])
def test_namespace_template_originals_match_cpp17(name, units, tmp_path, monkeypatch):
    execute_row_pair(tmp_path, monkeypatch, units)


@pytest.mark.integration
@pytest.mark.parametrize("name,source,code", INVALID, ids=[row[0] for row in INVALID])
def test_invalid_namespace_template_original_is_rejected_by_reference(name, source, code, tmp_path):
    compiler = shutil.which(os.environ.get("PCC_CPP_ORACLE", "g++"))
    assert compiler, "explicit C++17 negative reference is required"
    unit = tmp_path / (name + ".cpp")
    unit.write_text(source)
    result = subprocess.run([compiler, "-std=c++17", "-pedantic-errors", "-fsyntax-only", str(unit)],
                            capture_output=True, timeout=20)
    (tmp_path / "reference.stdout.bin").write_bytes(result.stdout)
    (tmp_path / "reference.stderr.bin").write_bytes(result.stderr)
    assert result.returncode != 0


@pytest.mark.integration
@pytest.mark.parametrize("name,source,code", DEFERRED, ids=[row[0] for row in DEFERRED])
def test_deferred_namespace_template_original_is_valid_reference_only(name, source, code, tmp_path):
    compiler = shutil.which(os.environ.get("PCC_CPP_ORACLE", "g++"))
    assert compiler, "explicit C++17 positive reference is required"
    reference = compile_reference_cpp(((name + ".cpp", source),), tmp_path / "reference", compiler=compiler)
    assert_exact(run_executable(reference), Execution(42, struct.pack("<i", 42), b""))


@pytest.mark.integration
def test_named_namespace_template_multi_tu_is_valid_reference_only(tmp_path):
    compiler = shutil.which(os.environ.get("PCC_CPP_ORACLE", "g++"))
    assert compiler, "explicit C++17 multi-TU reference is required"
    reference = compile_reference_cpp(MULTI_TU_NAMED_TEMPLATE, tmp_path / "reference", compiler=compiler)
    assert_exact(run_executable(reference), Execution(42, struct.pack("<i", 42), b""))


@pytest.mark.integration
def test_namespace_template_exports_match_itanium_reference(tmp_path, monkeypatch):
    from pcc.backend.elf_x86_64 import parse_relocatable

    source = ("namespace A{template<class T>T f(T x){return x;}"
              "namespace B{template<class T,int N,class U>T add(T x,U y){return x+T(N)+T(y);}}"
              "inline namespace V{template<int N>int value(){return N;}}}"
              "int cpp_test_entry(){return A::f(10)+int(A::B::add<long,2>(10,10))+A::value<10>();}")
    with monkeypatch.context() as guard:
        def forbidden(*args, **kwargs):
            raise AssertionError("owned namespace template compilation invoked an external process")
        guard.setattr(subprocess, "Popen", forbidden)
        artifact = compile_host_cpp((("namespace-template-abi.cpp", source),))
    compiler = shutil.which(os.environ.get("PCC_CPP_ORACLE", "g++"))
    assert compiler, "explicit C++17 ABI reference is required"
    unit, output = tmp_path / "namespace-template-abi.cpp", tmp_path / "reference.s"
    unit.write_text(source)
    result = subprocess.run([compiler, "-std=c++17", "-pedantic-errors", "-O0", "-S",
                             str(unit), "-o", str(output)], capture_output=True, timeout=20)
    assert result.returncode == 0, result.stderr
    reference = set(re.findall(r"^\s*\.(?:weak|globl)\s+(_Z\w+)", output.read_text(), re.MULTILINE))
    actual = {symbol.name for symbol in parse_relocatable(artifact.objects[0]).symbols
              if symbol.binding == 1 and symbol.section_index and symbol.name.startswith("_Z")}
    assert actual == reference == {
        "_ZN1A1fIiEET_S1_", "_ZN1A1B3addIlLi2EiEET_S2_T1_",
        "_ZN1A1V5valueILi10EEEiv", "_Z14cpp_test_entryv",
    }
