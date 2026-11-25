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


def source_for(base_body, exception_specification=""):
    return (PREFIX + "void mark(int*p){*p=*p*10+2;}"
            "struct Base{Base(int*p):total(p){} int* total; virtual ~Base()"
            + exception_specification + "{" + base_body + "}};" + SUFFIX)


@pytest.mark.integration
def test_virtual_explicit_closed_destructor_executes_final_overrider(tmp_path):
    units = (("closed.cpp", source_for("*total=*total*10+2;")),)
    binary = tmp_path / "owned"
    write_host_executable(compile_host_cpp(units), binary)
    reference = compile_reference_with_new(units, tmp_path / "reference")
    assert_exact(run_executable(binary), EXPECTED)
    assert_exact(run_executable(reference), EXPECTED)


@pytest.mark.integration
def test_virtual_explicit_helper_needs_no_effect_proof_for_nonthrowing_d1(tmp_path):
    units = (("helper.cpp", source_for("mark(total);")),)
    reference = compile_reference_with_new(units, tmp_path / "reference")
    binary = tmp_path / "owned"
    write_host_executable(compile_host_cpp(units), binary)
    assert_exact(run_executable(reference), EXPECTED)
    assert_exact(run_executable(binary), EXPECTED)


def test_potentially_throwing_open_virtual_helper_remains_gated():
    units = (("helper.cpp", source_for("mark(total);", "noexcept(false)")),)
    with pytest.raises(CppDiagnostic) as error:
        compile_host_cpp(units)
    assert error.value.code == "cpp.unsupported.placement-constructor-effects"


def source_for_internal_helper(specifier, effect, exception_specification=""):
    return (
        PREFIX + effect
        + "struct Object{Object(int*p):total(p){} int* total; "
        + specifier + " ~Object()" + exception_specification + "{mark(total);}};"
        + "int cpp_test_entry(){long long raw[16];int result=1;"
          "Object*p=new(raw)Object(&result);p->~Object();return result;}"
    )


@pytest.mark.integration
@pytest.mark.parametrize("specifier", ("", "virtual"))
@pytest.mark.parametrize("exception_specification", ("", "noexcept(false)"))
def test_explicit_destructor_executes_proved_internal_leaf_call(tmp_path, specifier, exception_specification):
    # The virtual case names a final destructor, so no unknown override can
    # acquire the static owner's proof. Nonvirtual destruction is already a
    # direct D1. The helper is a bound TU-local definition in both cases.
    source = source_for_internal_helper(
        specifier, "static inline void mark(int*p){*p=42;}", exception_specification)
    if specifier:
        declaration = "~Object()" + exception_specification
        source = source.replace(declaration + "{", declaration + " final {")
    units = (("internal.cpp", source),)
    binary = tmp_path / "owned"
    write_host_executable(compile_host_cpp(units), binary)
    reference = compile_reference_with_new(units, tmp_path / "reference")
    assert_exact(run_executable(binary), EXPECTED)
    assert_exact(run_executable(reference), EXPECTED)


INTERNAL_HELPERS = (
    ("", "void later(int*);static inline void mark(int*p){later(p);}",
     (("later.cpp", "void later(int*p){*p=42;}"),)),
    ("virtual", "static inline void mark(int*p){*p=42;}", ()),
)


@pytest.mark.integration
@pytest.mark.parametrize(("specifier", "effect", "extra"), INTERNAL_HELPERS)
def test_nonthrowing_explicit_destructor_allows_external_callees_and_open_overrides(tmp_path, specifier, effect, extra):
    units = (("unproved.cpp", source_for_internal_helper(specifier, effect)),) + extra
    binary = tmp_path / "owned"
    write_host_executable(compile_host_cpp(units), binary)
    reference = compile_reference_with_new(units, tmp_path / "reference")
    assert_exact(run_executable(binary), EXPECTED)
    assert_exact(run_executable(reference), EXPECTED)


@pytest.mark.parametrize(("specifier", "effect", "extra"), INTERNAL_HELPERS)
def test_potentially_throwing_explicit_destructor_keeps_unproved_callees_gated(specifier, effect, extra):
    units = (("unproved.cpp", source_for_internal_helper(specifier, effect, "noexcept(false)")),) + extra
    with pytest.raises(CppDiagnostic) as error:
        compile_host_cpp(units)
    assert error.value.code == "cpp.unsupported.placement-constructor-effects"


def late_member_header(exception_specification=""):
    return PREFIX + "struct O{O(int*p):out(p){} int*out;~O()" + exception_specification + ";void cleanup();};"


def late_member_source(member_body, exception_specification=""):
    return (
        late_member_header(exception_specification)
        + "O::~O()" + exception_specification + "{cleanup();} void dispose(O*p){p->~O();}"
        + member_body +
        "int cpp_test_entry(){long long raw[16];int result=1;"
        "O*p=new(raw)O(&result);dispose(p);return result;}"
    )


@pytest.mark.integration
@pytest.mark.parametrize("exception_specification", ("", "noexcept(false)"))
def test_late_nonvirtual_member_leaf_effect_has_a_real_definition(tmp_path, exception_specification):
    # Both the destructor definition and its call precede the helper
    # definition. Admission depends on its exact bound TU identity.
    source = late_member_source("void O::cleanup(){*out=42;}", exception_specification)
    units = (("late-member.cpp", source),)
    reference = compile_reference_with_new(units, tmp_path / "reference")
    binary = tmp_path / "owned"
    write_host_executable(compile_host_cpp(units), binary)
    assert_exact(run_executable(reference), EXPECTED)
    assert_exact(run_executable(binary), EXPECTED)


LATE_MEMBERS = (
    ("", late_member_header() + "void O::cleanup(){*out=42;}"),
    ("void unknown(int*);void O::cleanup(){unknown(out);}", "void unknown(int*p){*p=42;}"),
)


@pytest.mark.integration
@pytest.mark.parametrize("body,extra", LATE_MEMBERS)
def test_nonthrowing_d1_member_effects_can_resolve_across_translation_units(tmp_path, body, extra):
    units = (("no-proof.cpp", late_member_source(body)), ("extra.cpp", extra))
    binary = tmp_path / "owned"
    write_host_executable(compile_host_cpp(units), binary)
    reference = compile_reference_with_new(units, tmp_path / "reference")
    assert_exact(run_executable(binary), EXPECTED)
    assert_exact(run_executable(reference), EXPECTED)


@pytest.mark.parametrize("body,extra", LATE_MEMBERS)
def test_potentially_throwing_pending_member_effects_never_leave_tu_unproved(body, extra):
    with pytest.raises(CppDiagnostic) as error:
        compile_host_cpp((("no-proof.cpp", late_member_source(body, "noexcept(false)")),))
    assert error.value.code == "cpp.unsupported.placement-constructor-effects"
    assert "bound direct member effect graph" in error.value.message


def recursive_member_header(exception_specification=""):
    return (PREFIX + "struct O{O(int*p):out(p){} int*out;~O()" + exception_specification
            + ";void left(int n);void right(int n);};")


def recursive_member_source(body, exception_specification=""):
    return (
        recursive_member_header(exception_specification)
        + "O::~O()" + exception_specification + "{left(2);} void dispose(O*p){p->~O();}"
        + body
        + "int cpp_test_entry(){long long raw[16];int result=0;"
          "O*p=new(raw)O(&result);dispose(p);return result;}"
    )


@pytest.mark.integration
@pytest.mark.parametrize("members", [
    "void O::left(int n){if(n>0)left(n-1);else *out=42;}",
    "void O::left(int n){if(n>0)right(n-1);else *out=42;}"
    "void O::right(int n){left(n);}",
], ids=("self", "mutual"))
@pytest.mark.parametrize("exception_specification", ("", "noexcept(false)"))
def test_recursive_direct_member_component_matches_cpp17(tmp_path, members, exception_specification):
    units = (("recursive.cpp", recursive_member_source(members, exception_specification)),)
    binary = tmp_path / "owned"
    write_host_executable(compile_host_cpp(units), binary)
    reference = compile_reference_with_new(units, tmp_path / "reference")
    assert_exact(run_executable(binary), EXPECTED)
    assert_exact(run_executable(reference), EXPECTED)


RECURSIVE_MEMBERS = (
    ("void O::left(int n){if(n>0)left(n-1);else right(n);}",
     recursive_member_header() + "void O::right(int n){*out=42+n;}"),
    ("void unknown(int*);void O::left(int n){if(n>0)left(n-1);else unknown(out);}",
     "void unknown(int*p){*p=42;}"),
)


@pytest.mark.integration
@pytest.mark.parametrize("members,extra", RECURSIVE_MEMBERS)
def test_nonthrowing_recursive_member_component_can_resolve_outgoing_edges(tmp_path, members, extra):
    units = (("unproved-recursion.cpp", recursive_member_source(members)), ("extra.cpp", extra))
    binary = tmp_path / "owned"
    write_host_executable(compile_host_cpp(units), binary)
    reference = compile_reference_with_new(units, tmp_path / "reference")
    assert_exact(run_executable(binary), EXPECTED)
    assert_exact(run_executable(reference), EXPECTED)


@pytest.mark.parametrize("members,extra", RECURSIVE_MEMBERS)
def test_potentially_throwing_recursive_member_component_keeps_unknown_edges_gated(members, extra):
    with pytest.raises(CppDiagnostic) as error:
        compile_host_cpp((("unproved-recursion.cpp", recursive_member_source(members, "noexcept(false)")),))
    assert error.value.code == "cpp.unsupported.placement-constructor-effects"


@pytest.mark.integration
def test_open_virtual_explicit_destructor_can_select_override_in_another_tu(tmp_path):
    header = PREFIX + (
        "struct Base{Base(int*p):out(p){}int*out;virtual ~Base();};"
        "void destroy(Base*p);"
    )
    units = (
        ("destroy.cpp", header + "Base::~Base(){*out=*out*10+2;}"
                                  "void destroy(Base*p){p->~Base();}"),
        ("caller.cpp", header + "struct Child:Base{Child(int*p):Base(p){}"
                                 "~Child(){*out=*out*10+4;}};"
                                 "int cpp_test_entry(){long long storage[16];int result=0;"
                                 "Base*p=new(storage)Child(&result);destroy(p);return result;}"),
    )
    binary = tmp_path / "owned"
    write_host_executable(compile_host_cpp(units), binary)
    reference = compile_reference_with_new(units, tmp_path / "reference")
    assert_exact(run_executable(binary), EXPECTED)
    assert_exact(run_executable(reference), EXPECTED)
