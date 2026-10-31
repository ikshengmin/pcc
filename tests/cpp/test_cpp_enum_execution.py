"""Independent Linux AMD64 enum ABI observations; not whole-C++17 proof."""

import json
import os
import re
import shutil
import struct
import subprocess

import pytest

from pcc.backend.elf_x86_64 import parse_relocatable
from tests.cpp.owned_execution import (
    Execution, compile_host_cpp, compile_reference_cpp, write_host_executable,
)
from tests.cpp.test_cpp_class_execution import execute_and_record, record_execution


DECLARATIONS = (
    "enum class Small:unsigned char{Value=17};"
    "enum class Wide:unsigned long{Value=4294967321ul};"
)


def owned_artifact(units, monkeypatch, assembly=()):
    with monkeypatch.context() as guard:
        def forbidden(*args, **kwargs):
            raise AssertionError("owned enum compile invoked an external process")

        guard.setattr(subprocess, "Popen", forbidden)
        first = compile_host_cpp(units, owned_support_assembly=assembly)
        assert first == compile_host_cpp(units, owned_support_assembly=assembly)
    return first


def compare_observer(tmp_path, monkeypatch, source, assembly, reference):
    artifact = owned_artifact((("entry.cpp", source),), monkeypatch, (assembly,))
    binary = tmp_path / "owned"
    write_host_executable(artifact, binary)
    expected = Execution(42, struct.pack("<i", 42), b"")
    record_execution(binary, expected)
    (tmp_path / "observer.s").write_text(assembly)
    (tmp_path / "entry.cpp").write_text(source)
    compiler = shutil.which(os.environ.get("PCC_CPP_ORACLE", "g++"))
    assert compiler, "explicit C++17 reference unavailable"
    oracle = compile_reference_cpp((("entry.cpp", source), ("observer.cpp", reference)),
                                   tmp_path / "reference", compiler=compiler)
    record_execution(oracle, expected)


@pytest.mark.integration
def test_enum_small_and_wide_argument_abi_observed_by_owned_assembly(tmp_path, monkeypatch):
    source = (DECLARATIONS + "int inspect(Small,Wide);"
              "int cpp_test_entry(){return inspect(Small::Value,Wide::Value);}")
    # System V integer argument registers: Small's low byte in dil, LP64 Wide
    # in rsi. Check both halves, not merely the low 32 bits of the wide enum.
    assembly = (
        ".intel_syntax noprefix\n.text\n.globl _Z7inspect5Small4Wide\n"
        "_Z7inspect5Small4Wide:\n"
        "  cmp dil, 17\n  jne .Lbad\n  cmp esi, 25\n  jne .Lbad\n"
        "  shr rsi, 32\n  cmp esi, 1\n  jne .Lbad\n  mov eax, 42\n  ret\n"
        ".Lbad:\n  mov eax, -1\n  ret\n"
    )
    reference = (DECLARATIONS + "int inspect(Small s,Wide w){"
                 "if(int(s)!=17)return -1;"
                 "if((long(w)&4294967295l)!=25)return -1;"
                 "if((long(w)>>32)!=1)return -1;return 42;}")
    compare_observer(tmp_path, monkeypatch, source, assembly, reference)


@pytest.mark.integration
def test_enum_field_storage_alignment_observed_independently(tmp_path, monkeypatch):
    declarations = DECLARATIONS + "struct Record{char tag;Small small;Wide wide;};"
    source = (declarations + "int inspect(const Record*);"
              "int cpp_test_entry(){char tag=7;Record r{tag,Small::Value,Wide::Value};"
              "return inspect(&r);}")
    # Distinct char/byte-enum fields followed by an 8-byte aligned enum at 8.
    # This observes offsets 0/1/8 and both wide halves, not every record ABI.
    assembly = (
        ".intel_syntax noprefix\n.text\n.globl _Z7inspectPK6Record\n"
        "_Z7inspectPK6Record:\n"
        "  movzx eax, byte ptr [rdi]\n  cmp eax, 7\n  jne .Lbad\n"
        "  movzx eax, byte ptr [rdi+1]\n  cmp eax, 17\n  jne .Lbad\n"
        "  mov eax, dword ptr [rdi+8]\n  cmp eax, 25\n  jne .Lbad\n"
        "  mov eax, dword ptr [rdi+12]\n  cmp eax, 1\n  jne .Lbad\n  mov eax, 42\n  ret\n"
        ".Lbad:\n  mov eax, -1\n  ret\n"
    )
    reference = (declarations + "int inspect(const Record*p){"
                 "if(p->tag!=7)return -1;if(int(p->small)!=17)return -1;"
                 "if((long(p->wide)&4294967295l)!=25)return -1;"
                 "if((long(p->wide)>>32)!=1)return -1;return 42;}")
    compare_observer(tmp_path, monkeypatch, source, assembly, reference)


@pytest.mark.integration
def test_nominal_enum_parameters_and_returns_across_independent_units(tmp_path, monkeypatch):
    declaration = "namespace n{enum class E:unsigned char{Value=42};E echo(E); }"
    units = (("helper.cpp", declaration + "namespace n{E echo(E e){return e;}}"),
             ("entry.cpp", declaration + "int cpp_test_entry(){return int(n::echo(n::E::Value));}"))
    execute_and_record(tmp_path, monkeypatch, units, 42)


@pytest.mark.integration
def test_enum_exports_preserve_nominal_substitutions_against_cpp17_reference(tmp_path, monkeypatch):
    source = (
        "enum class E{A};int f(E a,E b){return 0;}int f(E*a,E*b){return 0;}"
        "namespace n{enum class E{A};int f(E a,E b){return 0;}"
        "int f(const E*a,const E*b){return 0;}}"
        "int f(n::E a,n::E*b){return 0;}int cpp_test_entry(){return 0;}"
    )
    artifact = owned_artifact((("enum-exports.cpp", source),), monkeypatch)
    unit, output = tmp_path / "enum-exports.cpp", tmp_path / "reference.s"
    unit.write_text(source)
    compiler = shutil.which(os.environ.get("PCC_CPP_ORACLE", "g++"))
    assert compiler, "explicit C++17 ABI reference unavailable"
    result = subprocess.run([compiler, "-std=c++17", "-pedantic-errors", "-O0",
                             "-S", str(unit), "-o", str(output)], capture_output=True, timeout=20)
    assert result.returncode == 0, result.stderr
    reference = set(re.findall(r"^\s*\.globl\s+(_Z\w+)", output.read_text(), re.MULTILINE))
    actual = {symbol.name for symbol in parse_relocatable(artifact.objects[0]).symbols
              if symbol.binding == 1 and symbol.section_index and symbol.name.startswith("_Z")}
    assert actual == reference
    assert len(actual) == 6
    (tmp_path / "owned.o").write_bytes(artifact.objects[0])
    (tmp_path / "exports.json").write_text(json.dumps({"owned": sorted(actual),
                                                       "reference": sorted(reference)}, indent=2))
