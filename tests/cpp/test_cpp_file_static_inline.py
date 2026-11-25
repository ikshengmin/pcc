"""N4659 [basic.link]/3 and [dcl.inline]: static inline stays TU-local."""
import os
import shutil
import struct

import pytest

from pcc.backend.elf_x86_64 import parse_relocatable
from pcc.frontends.cpp import CppDiagnostic, CppParser
from tests.cpp.owned_execution import (
    Execution, assert_exact, compile_host_cpp, compile_reference_cpp,
    run_executable, write_host_executable,
)

UNITS = (
    ("helper.cpp", "static inline int value(){return 17;} int helper(){return value();}"),
    ("entry.cpp", "static inline int value(){return 25;} int helper(); "
                  "int cpp_test_entry(){return helper()+value();}"),
)


def test_file_static_inline_does_not_export_or_coalesce():
    artifact = compile_host_cpp(UNITS)
    local_names = []
    for object_bytes in artifact.objects:
        names = [symbol for symbol in parse_relocatable(object_bytes).symbols
                 if symbol.name.endswith("_ZL5valuev")]
        assert len(names) == 1 and names[0].binding == 0 and names[0].section_index
        local_names.append(names[0].name)
    assert len(set(local_names)) == 2


@pytest.mark.integration
def test_file_static_inline_executes_against_explicit_cpp17(tmp_path):
    artifact = compile_host_cpp(UNITS)
    owned = tmp_path / "owned"
    write_host_executable(artifact, owned)
    compiler = shutil.which(os.environ.get("PCC_CPP_ORACLE", "g++"))
    assert compiler
    reference = compile_reference_cpp(UNITS, tmp_path / "reference", compiler=compiler)
    expected = Execution(42, struct.pack("<i", 42), b"")
    assert_exact(run_executable(owned), expected)
    assert_exact(run_executable(reference), expected)


@pytest.mark.parametrize(("source", "code"), [
    ("int f(); static inline int f(){return 1;}", "cpp.conflicting-function-linkage"),
    ("static inline int x=0;", "cpp.unsupported.inline-declarations"),
    ("static inline int main(){return 0;}", "cpp.invalid-main-linkage"),
])
def test_file_static_inline_does_not_admit_other_storage_shapes(source, code):
    with pytest.raises(CppDiagnostic) as error:
        CppParser().parse(source)
    assert error.value.code == code
