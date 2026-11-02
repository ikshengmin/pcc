"""CI-owned same-original integral NTTP execution and Itanium ABI obligations.

Host PCC -> owned objects/executable and a separately labeled C++17 oracle.
No local execution evidence, complete NTTP/class-template or native pcc1 claim.
"""

import os
import re
import shutil
import subprocess

import pytest

from pcc.frontends.cpp import CppDiagnostic
from tests.cpp.owned_execution import compile_host_cpp
from tests.cpp.sizeof_execution import execute_row_pair
from tests.cpp.test_cpp_integral_template_arguments import CASES, NEGATIVE_CASES


@pytest.mark.integration
@pytest.mark.parametrize("name,source", CASES, ids=[row[0] for row in CASES])
def test_integral_nttp_original_program_matches_cpp17(name, source, tmp_path, monkeypatch):
    execute_row_pair(tmp_path, monkeypatch, ((name + ".cpp", source),))


@pytest.mark.parametrize("name,source", CASES, ids=[row[0] for row in CASES])
def test_integral_nttp_owned_artifact_is_deterministic(name, source, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("owned NTTP compilation invoked an external process")
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    units = ((name + ".cpp", source),)
    assert compile_host_cpp(units) == compile_host_cpp(units)


@pytest.mark.parametrize("name,source,code", NEGATIVE_CASES, ids=[row[0] for row in NEGATIVE_CASES])
def test_invalid_nttp_never_reaches_owned_emission(name, source, code, monkeypatch):
    from pcc.backend import owned_object_emit

    def forbidden(*args, **kwargs):
        raise AssertionError("rejected template argument reached object emission")

    monkeypatch.setattr(owned_object_emit, "emit_owned_object", forbidden)
    with pytest.raises(CppDiagnostic) as error:
        compile_host_cpp(((name + ".cpp", source),))
    assert error.value.code == code


@pytest.mark.integration
def test_integral_nttp_itanium_exports_match_explicit_reference(tmp_path, monkeypatch):
    from pcc.backend.elf_x86_64 import parse_relocatable

    source = ("template<int N> int neg(){return N;}"
              "template<bool N> int flag(){return N;}"
              "template<unsigned long long N> int wide(){return N!=0;}"
              "template<class T,T N> T mixed(T x){return x+N;}"
              "int cpp_test_entry(){return neg<-7>()+flag<true>()+"
              "wide<18446744073709551615ull>()+int(mixed<long,2>(45));}")
    with monkeypatch.context() as guard:
        def forbidden(*args, **kwargs):
            raise AssertionError("owned NTTP compilation invoked an external process")
        guard.setattr(subprocess, "Popen", forbidden)
        artifact = compile_host_cpp((("value-abi.cpp", source),))
    compiler = shutil.which(os.environ.get("PCC_CPP_ORACLE", "g++"))
    assert compiler, "explicit C++17 ABI reference is required"
    unit, output = tmp_path / "value-abi.cpp", tmp_path / "reference.s"
    unit.write_text(source)
    result = subprocess.run([compiler, "-std=c++17", "-pedantic-errors", "-O0", "-S",
                             str(unit), "-o", str(output)], capture_output=True, timeout=20)
    assert result.returncode == 0, result.stderr
    reference = set(re.findall(r"^\s*\.(?:weak|globl)\s+(_Z\w+)", output.read_text(), re.MULTILINE))
    actual = {symbol.name for symbol in parse_relocatable(artifact.objects[0]).symbols
              if symbol.binding == 1 and symbol.section_index and symbol.name.startswith("_Z")}
    expected = {"_Z3negILin7EEiv", "_Z4flagILb1EEiv", "_Z4wideILy18446744073709551615EEiv",
                "_Z5mixedIlLl2EET_S0_", "_Z14cpp_test_entryv"}
    assert actual == reference == expected
