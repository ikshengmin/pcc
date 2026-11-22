"""N4659 [basic.start.static], [stmt.dcl], [conv.array]."""
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
    'int sample(bool change){static const char* p="AB"; int was=p[0];'
    'if(change)p="XY";return was;}'
    'int other(){static const char* p="AB";return p[0];}'
    'int cpp_test_entry(){int a=sample(true);int b=sample(false);'
    'return a==65 && b==88 && other()==65 ? 42 : 1;}'
)


def test_local_static_pointer_has_no_runtime_initializer_function():
    artifact = compile_host_cpp((("static-string.cpp", PROGRAM),))
    ir = artifact.ir[0]
    assert ir.count("internal global ptr") == 2
    assert "@llvm.global_ctors" not in ir


@pytest.mark.integration
def test_local_static_pointer_persists_against_explicit_cpp17(tmp_path):
    units = (("static-string.cpp", PROGRAM),)
    artifact = compile_host_cpp(units)
    owned = tmp_path / "owned"
    write_host_executable(artifact, owned)
    compiler = shutil.which(os.environ.get("PCC_CPP_ORACLE", "g++"))
    assert compiler
    reference = compile_reference_cpp(units, tmp_path / "reference", compiler=compiler)
    expected = Execution(42, struct.pack("<i", 42), b"")
    assert_exact(run_executable(owned), expected)
    assert_exact(run_executable(reference), expected)


@pytest.mark.parametrize("initializer", ["", "= text", '{ "text" }'])
def test_local_static_pointer_does_not_expand_to_other_initializers(initializer):
    with pytest.raises(CppDiagnostic) as error:
        CppParser().parse('int f(const char* text){static const char* p'
                          + initializer + ';return 0;}')
    assert error.value.code == "cpp.unsupported.static-initialization-analysis"
