"""N4659 [stmt.dcl], [dcl.init.aggr], [basic.start.static], [conv.ptr]."""
import os
import shutil
import struct

import pytest

from pcc.frontends.cpp import CppDiagnostic, CppParser
from tests.cpp.owned_execution import (
    Execution, assert_exact, compile_host_cpp, compile_reference_cpp,
    run_executable, write_host_executable,
)

PROGRAM = (
    'int count(bool change){static const char* values[]={"a","BB",0};'
    'int sum=0;for(int i=0;values[i];++i){sum+=values[i][0];}'
    'if(change)values[1]="CC";return sum;}'
    'int cpp_test_entry(){int a=count(true);int b=count(false);'
    'return a==163 && b==164 ? 42 : 1;}'
)


def test_local_static_string_pointer_array_is_a_constant_initializer():
    artifact = compile_host_cpp((("static-strings.cpp", PROGRAM),))
    ir = artifact.ir[0]
    assert "internal global [3 x ptr]" in ir
    assert "@llvm.global_ctors" not in ir


@pytest.mark.integration
def test_local_static_string_pointer_array_matches_explicit_cpp17(tmp_path):
    units = (("static-strings.cpp", PROGRAM),)
    artifact = compile_host_cpp(units)
    owned = tmp_path / "owned"
    write_host_executable(artifact, owned)
    compiler = shutil.which(os.environ.get("PCC_CPP_ORACLE", "g++"))
    assert compiler
    reference = compile_reference_cpp(units, tmp_path / "reference", compiler=compiler)
    expected = Execution(42, struct.pack("<i", 42), b"")
    assert_exact(run_executable(owned), expected)
    assert_exact(run_executable(reference), expected)


@pytest.mark.parametrize("declaration", [
    "static const char* a[]={text,0};",
    "static const char* a[1];",
    'static const char* a[1][2]={{"a",0}};',
])
def test_local_static_string_pointer_array_rejects_unproved_variants(declaration):
    with pytest.raises(CppDiagnostic) as error:
        CppParser().parse('int f(const char* text){'+declaration+'return 0;}')
    assert error.value.code == "cpp.unsupported.static-initialization-analysis"
