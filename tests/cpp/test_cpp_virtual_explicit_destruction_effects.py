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


def source_for_internal_helper(specifier, effect):
    return (
        PREFIX + effect
        + "struct Object{Object(int*p):total(p){} int* total; "
        + specifier + " ~Object(){mark(total);}};"
        + "int cpp_test_entry(){long long raw[16];int result=1;"
          "Object*p=new(raw)Object(&result);p->~Object();return result;}"
    )


@pytest.mark.integration
@pytest.mark.parametrize("specifier", ("", "virtual"))
def test_explicit_destructor_executes_proved_internal_leaf_call(tmp_path, specifier):
    # The virtual case names a final destructor, so no unknown override can
    # acquire the static owner's proof. Nonvirtual destruction is already a
    # direct D1. The helper is a bound TU-local definition in both cases.
    source = source_for_internal_helper(specifier, "static inline void mark(int*p){*p=42;}")
    if specifier:
        source = source.replace("~Object(){", "~Object() final {")
    units = (("internal.cpp", source),)
    binary = tmp_path / "owned"
    write_host_executable(compile_host_cpp(units), binary)
    reference = compile_reference_with_new(units, tmp_path / "reference")
    assert_exact(run_executable(binary), EXPECTED)
    assert_exact(run_executable(reference), EXPECTED)


@pytest.mark.parametrize(("specifier", "effect"), [
    ("", "void later(int*);static inline void mark(int*p){later(p);}"),
    ("virtual", "static inline void mark(int*p){*p=42;}"),
])
def test_explicit_destructor_keeps_unproved_internal_callees_and_overrides_gated(specifier, effect):
    with pytest.raises(CppDiagnostic) as error:
        compile_host_cpp((("unproved.cpp", source_for_internal_helper(specifier, effect)),))
    assert error.value.code == "cpp.unsupported.placement-constructor-effects"


def late_member_source(member_body):
    return (
        PREFIX + "struct O{O(int*p):out(p){} int*out;~O();void cleanup();};"
        "O::~O(){cleanup();} void dispose(O*p){p->~O();}"
        + member_body +
        "int cpp_test_entry(){long long raw[16];int result=1;"
        "O*p=new(raw)O(&result);dispose(p);return result;}"
    )


@pytest.mark.integration
def test_late_nonvirtual_member_leaf_effect_has_a_real_definition(tmp_path):
    # Both the destructor definition and its call precede the helper
    # definition. Admission depends on its exact bound TU identity.
    source = late_member_source("void O::cleanup(){*out=42;}")
    units = (("late-member.cpp", source),)
    reference = compile_reference_with_new(units, tmp_path / "reference")
    binary = tmp_path / "owned"
    write_host_executable(compile_host_cpp(units), binary)
    assert_exact(run_executable(reference), EXPECTED)
    assert_exact(run_executable(binary), EXPECTED)


@pytest.mark.parametrize("body", ["", "void unknown(int*);void O::cleanup(){unknown(out);}"])
def test_pending_member_effects_never_leave_the_tu_unproved(body):
    with pytest.raises(CppDiagnostic) as error:
        compile_host_cpp((("no-proof.cpp", late_member_source(body)),))
    assert error.value.code == "cpp.unsupported.placement-constructor-effects"
    assert "bound direct member leaf bodies" in error.value.message
