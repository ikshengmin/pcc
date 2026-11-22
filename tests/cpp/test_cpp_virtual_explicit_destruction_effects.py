"""N4659 [class.dtor]/14: unqualified explicit destruction stays virtual."""
import struct

import pytest

from pcc.frontends.cpp import CppDiagnostic
from tests.cpp.owned_execution import (
    Execution, assert_exact, compile_host_cpp, run_executable, write_host_executable,
)
from tests.cpp.test_cpp_placement_new import compile_reference_with_new

PREFIX = "using size_type=unsigned long;void* operator new(size_type,void*) noexcept;"
SUFFIX = (
    "struct Child:Base{Child(int*p):Base(p){} ~Child(){*total=*total*10+4;}};"
    "void dispose(Base* p){p->~Base();}"
    "int cpp_test_entry(){long long raw[16];int result=0;"
    "Base*p=new(raw)Child(&result);dispose(p);return result;}"
)
EXPECTED = Execution(42, struct.pack("<i", 42), b"")


def source_for(base_body):
    return (PREFIX + "void mark(int*p){*p=*p*10+2;}"
            "struct Base{Base(int*p):total(p){} int* total; virtual ~Base(){"
            + base_body + "}};" + SUFFIX)


@pytest.mark.integration
def test_virtual_explicit_closed_destructor_executes_final_overrider(tmp_path):
    units = (("closed.cpp", source_for("*total=*total*10+2;")),)
    binary = tmp_path / "owned"
    write_host_executable(compile_host_cpp(units), binary)
    reference = compile_reference_with_new(units, tmp_path / "reference")
    assert_exact(run_executable(binary), EXPECTED)
    assert_exact(run_executable(reference), EXPECTED)


@pytest.mark.integration
def test_virtual_explicit_helper_effect_is_not_assumed_safe(tmp_path):
    units = (("helper.cpp", source_for("mark(total);")),)
    reference = compile_reference_with_new(units, tmp_path / "reference")
    assert_exact(run_executable(reference), EXPECTED)
    with pytest.raises(CppDiagnostic) as error:
        compile_host_cpp(units)
    assert error.value.code == "cpp.unsupported.placement-constructor-effects"
