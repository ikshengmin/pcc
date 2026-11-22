"""N4659 [basic.start.static], [dcl.init.aggr], [basic.link]."""
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
    'namespace xml{struct Entity{const char* spelling; int length; char value;};'
    'static const int count=3;'
    'static const Entity entities[count]={{"quote",5,\'q\'},{"amp",3,\'a\'}};'
    'int answer(){return entities[0].length+entities[1].length'
    '+(entities[0].spelling[0]==entities[0].value?34:0)'
    '+entities[2].length+(entities[2].spelling!=nullptr);}}'
    'int cpp_test_entry(){return xml::answer();}'
)


def test_namespace_record_array_is_static_storage_without_initializer_function():
    artifact = compile_host_cpp((("record-array.cpp", PROGRAM),))
    ir = artifact.ir[0]
    assert "internal constant [3 x " in ir or "internal global [3 x " in ir
    assert "@llvm.global_ctors" not in ir


@pytest.mark.integration
def test_namespace_record_array_matches_explicit_cpp17(tmp_path):
    units = (("record-array.cpp", PROGRAM),)
    artifact = compile_host_cpp(units)
    owned = tmp_path / "owned"
    write_host_executable(artifact, owned)
    compiler = shutil.which(os.environ.get("PCC_CPP_ORACLE", "g++"))
    assert compiler
    reference = compile_reference_cpp(units, tmp_path / "reference", compiler=compiler)
    expected = Execution(42, struct.pack("<i", 42), b"")
    assert_exact(run_executable(owned), expected)
    assert_exact(run_executable(reference), expected)


@pytest.mark.parametrize(("source", "code"), [
    ('struct E{int v;~E();};static const E a[1]={{1}};', "cpp.unsupported.class-array-lifetime"),
    ('struct E{int v;};static const E a[1]={1};', "cpp.unsupported.aggregate-brace-elision"),
    ('int f();struct E{int v;};static const E a[1]={{f()}};', "cpp.unsupported.dynamic-initialization"),
])
def test_namespace_record_array_does_not_weaken_source_rules(source, code):
    with pytest.raises(CppDiagnostic) as error:
        CppParser().parse(source)
    assert error.value.code == code
