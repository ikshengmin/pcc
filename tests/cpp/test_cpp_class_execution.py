"""Host-owned class-object ELF execution, with a separate explicit G++ oracle.

No external C++ compiler participates in PCC compilation. The integration
tests compare the original source's stdout/stderr/status using the existing
owned-execution adapter. These original cases are not GCC/Clang suite imports
and do not certify native pcc1, TinyXML2, or complete C++17 support.
"""

import hashlib
import json
import os
import re
import shutil
import struct
import subprocess

import pytest

from pcc.frontends.cpp import CppDiagnostic
from tests.cpp.owned_execution import (
    Execution, assert_exact, compile_host_cpp, compile_reference_cpp,
    run_executable, write_host_executable,
)
from tests.cpp.test_cpp_class_semantics import (
    CLASS_CASES,
    CLASS_NEGATIVE_CASES,
    CLASS_UNIMPLEMENTED_CASES,
)
from tests.cpp.test_cpp_grouped_names import owned_only
from tests.cpp.test_cpp_pointer_execution import execute_and_compare


@pytest.mark.parametrize("name,source,value", CLASS_CASES, ids=[case[0] for case in CLASS_CASES])
def test_cpp_class_owned_compile_is_deterministic(owned_only, name, source, value):
    units = ((name + ".cpp", source),)
    first = compile_host_cpp(units)
    second = compile_host_cpp(units)
    assert first == second
    assert first.owner == "host-python/pcc-cpp/pcc-self/pcc-elf"
    assert first.executable[:4] == b"\x7fELF"


@pytest.mark.parametrize("source", [source for source, _ in CLASS_NEGATIVE_CASES]
                         + list(CLASS_UNIMPLEMENTED_CASES))
def test_cpp_class_invalid_or_unimplemented_semantics_never_emit(
    owned_only, tmp_path, monkeypatch, source,
):
    from pcc.backend import owned_object_emit

    def forbidden(*args, **kwargs):
        raise AssertionError("invalid class semantics reached object emission")

    monkeypatch.setattr(owned_object_emit, "emit_owned_object", forbidden)
    binary = tmp_path / "invalid-class"
    with pytest.raises(CppDiagnostic):
        artifact = compile_host_cpp((("invalid-class.cpp", source),))
        write_host_executable(artifact, binary)
    assert not binary.exists()


@pytest.mark.integration
@pytest.mark.parametrize("name,source,value", CLASS_CASES, ids=[case[0] for case in CLASS_CASES])
def test_cpp_class_owned_execution_matches_explicit_cpp17_reference(
    tmp_path, monkeypatch, name, source, value,
):
    execute_and_record(tmp_path, monkeypatch, ((name + ".cpp", source),), value)


@pytest.mark.integration
def test_cpp_class_pointer_abi_across_independent_translation_units(tmp_path, monkeypatch):
    units = (
        ("helper.cpp", "namespace data{struct Record{char tag;int value;long tail;};"
         "int read(const Record *p){return p->tag+p->value+p->tail;}}"),
        ("entry.cpp", "namespace data{struct Record{char tag;int value;long tail;};"
         "int read(const Record *);}"
         "int cpp_test_entry(){data::Record r{};r.tag=3;r.value=17;r.tail=22;"
         "return data::read(&r);}"),
    )
    execute_and_record(tmp_path, monkeypatch, units, 42)


def record_execution(binary, expected):
    actual = run_executable(binary)
    assert_exact(actual, expected)
    binary.with_suffix(".stdout.bin").write_bytes(actual.stdout)
    binary.with_suffix(".stderr.bin").write_bytes(actual.stderr)
    binary.with_suffix(".execution.json").write_text(json.dumps({
        "returncode": actual.returncode,
        "stdout_hex": actual.stdout.hex(), "stderr_hex": actual.stderr.hex(),
        "binary_sha256": hashlib.sha256(binary.read_bytes()).hexdigest(),
        "binary": str(binary),
    }, indent=2))


def execute_and_record(tmp_path, monkeypatch, units, value):
    # Reuse the existing owned/reference compilation adapter, then preserve an
    # independently asserted raw execution observation beside each artifact.
    execute_and_compare(tmp_path, monkeypatch, units, value)
    expected = Execution(value & 255, struct.pack("<i", value), b"")
    record_execution(tmp_path / "owned", expected)
    record_execution(tmp_path / "reference" / "oracle", expected)


@pytest.mark.integration
def test_cpp_class_mangled_exports_match_explicit_cpp17_reference(tmp_path, monkeypatch):
    from pcc.backend.elf_x86_64 import parse_relocatable
    source = ("struct S{};int f(S*a,S*b){return 0;}"
              "namespace n{struct S{};int f(S*a,S*b){return 0;}"
              "int f(const S*a,const S*b){return 0;}}"
              "int f(n::S*a,n::S*b){return 0;}int cpp_test_entry(){return 0;}")
    with monkeypatch.context() as guard:
        def forbidden(*args, **kwargs):
            raise AssertionError("owned class ABI compile launched an external process")
        guard.setattr(subprocess, "Popen", forbidden)
        artifact = compile_host_cpp((("class-abi.cpp", source),))
    unit, output = tmp_path / "class-abi.cpp", tmp_path / "reference.s"
    unit.write_text(source)
    compiler = shutil.which(os.environ.get("PCC_CPP_ORACLE", "g++"))
    assert compiler, "explicit G++17 ABI oracle unavailable"
    result = subprocess.run([compiler, "-std=c++17", "-pedantic-errors", "-O0",
                             "-S", str(unit), "-o", str(output)], capture_output=True, timeout=20)
    assert result.returncode == 0, result.stderr
    reference = set(re.findall(r"^\s*\.globl\s+(_Z\w+)", output.read_text(), re.MULTILINE))
    actual = {symbol.name for symbol in parse_relocatable(artifact.objects[0]).symbols
              if symbol.binding == 1 and symbol.section_index and symbol.name.startswith("_Z")}
    assert actual == reference
    assert len(actual) == 5
    (tmp_path / "owned.o").write_bytes(artifact.objects[0])
    (tmp_path / "exports.json").write_text(json.dumps({"owned": sorted(actual), "reference": sorted(reference)}, indent=2))


@pytest.mark.integration
def test_cpp_class_layout_observed_by_independent_owned_assembly(tmp_path, monkeypatch):
    declaration = ("struct Empty{};struct Record{char tag;bool flag;int value;long tail;"
                   "Record *self;Empty empty;char end;};")
    source = (declaration + "int inspect(const Record*);"
              "int cpp_test_entry(){Record r{};r.tag=3;r.flag=true;r.value=17;"
              "r.tail=4294967296L;r.self=&r;r.end=19;return inspect(&r);}")
    # Independent AMD64 System V / Itanium layout observation: offsets
    # 0/1/4/8/16/24/25, including LP64 high bits, pointer width and empty size.
    assembly = (".intel_syntax noprefix\n.text\n.globl _Z7inspectPK6Record\n"
                "_Z7inspectPK6Record:\n  movsx eax, byte ptr [rdi]\n"
                "  movzx ecx, byte ptr [rdi+1]\n  add eax, ecx\n"
                "  add eax, dword ptr [rdi+4]\n  add eax, dword ptr [rdi+12]\n"
                "  cmp qword ptr [rdi+16], rdi\n  sete cl\n  movzx ecx, cl\n"
                "  add eax, ecx\n  movsx ecx, byte ptr [rdi+25]\n  add eax, ecx\n  ret\n")
    with monkeypatch.context() as guard:
        def forbidden(*args, **kwargs):
            raise AssertionError("owned class layout compile launched an external process")
        guard.setattr(subprocess, "Popen", forbidden)
        artifact = compile_host_cpp((("layout.cpp", source),), owned_support_assembly=(assembly,))
    binary = tmp_path / "owned"
    write_host_executable(artifact, binary)
    expected = Execution(42, struct.pack("<i", 42), b"")
    record_execution(binary, expected)
    (tmp_path / "observer.s").write_text(assembly)
    compiler = shutil.which(os.environ.get("PCC_CPP_ORACLE", "g++"))
    assert compiler
    reference_source = (declaration + "int inspect(const Record*p){return p->tag+int(p->flag)+"
                        "p->value+int(p->tail>>32)+int(p->self==p)+p->end;}")
    oracle = compile_reference_cpp((("layout.cpp", source), ("observer.cpp", reference_source)),
                                  tmp_path / "reference", compiler=compiler)
    record_execution(oracle, expected)
