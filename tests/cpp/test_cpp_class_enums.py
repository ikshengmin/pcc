"""Class enum/name/access rules and Linux AMD64 nominal ABI observations.

Original cases, not copied suite tests. Rules: N4659 [class.mem], [class.access],
[basic.lookup.qual], [dcl.enum], [basic.lookup.argdep]; Itanium ABI 5.1.2/5.1.8.
https://www.open-std.org/jtc1/sc22/wg21/docs/papers/2017/n4659.pdf
https://itanium-cxx-abi.github.io/cxx-abi/abi.html#mangling
External G++ is only an explicitly labeled C++17 reference. Class enum support
is not constructors/destructors, dependent lookup or TinyXML2 qualification.
"""

import json
import os
import re
import shutil
import subprocess

import pytest

from pcc.backend.elf_x86_64 import parse_relocatable
from pcc.frontends.cpp import CppDiagnostic
from pcc.frontends.cpp.parser import CppParser
from pcc.frontends.cpp.types import mangle_function, PointerType
from tests.cpp.owned_execution import compile_host_cpp
from tests.cpp.test_cpp_class_execution import execute_and_record
from tests.cpp.test_cpp_enum_execution import owned_artifact


ORIGINAL_CLASS_ENUM = "struct S{enum E{A};};int f(){return S::A;}"
ORIGINAL_MEMBER_ALIAS = "struct S{using T=int;};S::T f();"

CLASS_ENUM_CASES = (
    ("original-class-enum-source", ORIGINAL_CLASS_ENUM +
     "int cpp_test_entry(){return f()+42;}", 42),
    ("original-member-alias-source", ORIGINAL_MEMBER_ALIAS +
     "S::T f(){return 42;}int cpp_test_entry(){return f();}", 42),
    ("class-enum-flags-and-provisional-initializers",
     "struct S{enum Mode{A=2,B=8,Combined=A|B,Answer=Combined+32};};"
     "int cpp_test_entry(){return S::Answer;}", 42),
    ("scoped-member-enum-and-enclosing-alias",
     "struct S{enum class E:unsigned char{A=42};};using C=S;using T=C::E;"
     "int cpp_test_entry(){return int(T::A);}", 42),
    ("public-alias-does-not-inherit-private-scoped-enum-name-access",
     "class S{enum class Hidden{A=42};public:using E=Hidden;};"
     "int cpp_test_entry(){return int(S::E::A);}", 42),
    ("private-enumerator-usable-in-own-later-member-initializer",
     "class S{enum Secret{A=17};public:enum E{B=A+25};};"
     "int cpp_test_entry(){return S::B;}", 42),
    ("class-qualification-inside-class-enum-initializer",
     "struct S{enum E{A=17,B=S::A+25};};int cpp_test_entry(){return S::E::B;}", 42),
    ("member-enum-is-not-a-storage-field",
     "struct S{enum E{A=17};int first;E second;};"
     "int cpp_test_entry(){S s{25,S::A};return s.first+s.second;}", 42),
    ("fixed-opaque-member-enum-completed-in-place",
     "struct S{enum class E:unsigned char;E value;enum class E:unsigned char{A=42};};"
     "int cpp_test_entry(){S s{S::E::A};return int(s.value);}", 42),
    ("member-enum-adl-uses-enclosing-namespace",
     "namespace n{struct S{enum E{A=17};};int take(S::E e){return e+25;}}"
     "int take(int x){return 1;}int cpp_test_entry(){return take(n::S::A);}", 42),
    ("public-alias-adl-keeps-original-enclosing-namespace",
     "namespace n{class S{enum class E{A=42};public:using Public=E;};"
     "int take(S::Public e){return int(e);}}using Alias=n::S::Public;"
     "int cpp_test_entry(){return take(Alias::A);}", 42),
    ("nominal-same-spelled-member-enums-overload",
     "struct A{enum class E{V=17};};struct B{enum class E{V=25};};"
     "int take(A::E x){return int(x);}int take(B::E x){return int(x);}"
     "int cpp_test_entry(){return take(A::E::V)+take(B::E::V);}", 42),
    ("class-prefix-substitution-enum-before-owner-pointer",
     "struct S{enum E{A=42};};int take(S::E e,S*p){return e;}"
     "int cpp_test_entry(){S s{};return take(S::A,&s);}", 42),
    ("class-prefix-substitution-owner-pointer-before-enum",
     "namespace n{struct S{enum E{A=42};};int take(S*p,S::E e){return e;}}"
     "int cpp_test_entry(){n::S s{};return take(&s,n::S::A);}", 42),
    ("private-integer-alias-exposed-through-public-alias",
     "class S{using Hidden=int;public:using Value=Hidden;};"
     "int cpp_test_entry(){S::Value value=42;return value;}", 42),
    ("enum-class-named-like-owner-is-not-an-enclosing-member",
     "struct S{enum class E{S=42};};int cpp_test_entry(){return int(S::E::S);}", 42),
)

INVALID_CLASS_ENUM_CASES = (
    ("private-enum-type", "class S{enum E{A};};S::E f();", "cpp.member-access"),
    ("private-enumerator", "class S{enum E{A=42};};int f(){return S::A;}", "cpp.member-access"),
    ("protected-enumerator", "struct S{protected:enum E{A};};int f(){return S::A;}", "cpp.member-access"),
    ("private-alias", "class S{using T=int;};S::T f();", "cpp.member-access"),
    ("private-enum-through-owner-alias", "class S{enum class E{A};};using Alias=S;Alias::E f();", "cpp.member-access"),
    ("member-enum-name-is-owner", "struct S{enum S{A};};", "cpp.class-name-member"),
    ("member-enumerator-name-is-owner", "struct S{enum E{S};};", "cpp.class-name-member"),
    ("different-access-redeclaration", "class S{enum E:int;public:enum E:int{A};};", "cpp.enum-access-redeclaration"),
    ("duplicate-member-enum-forward", "struct S{enum E:int;enum E:int;};", "cpp.redeclaration"),
    ("member-enumerator-conflicts-with-data", "struct S{int A;enum E{A};};", "cpp.redeclaration"),
    ("data-conflicts-with-member-enumerator", "struct S{enum E{A};int A;};", "cpp.redeclaration"),
    ("nonstatic-member-not-a-qualified-value", "struct S{int value;};int f(){return S::value;}", "cpp.nonstatic-member-without-object"),
    ("member-using-at-namespace-scope", "struct S{enum E{A};};using S::E;", "cpp.using-class-member"),
    ("enumerator-using-at-namespace-scope", "struct S{enum E{A};};using S::A;", "cpp.using-class-member"),
    ("member-using-at-block-scope", "struct S{enum E{A};};int f(){using S::E;return 0;}", "cpp.using-class-member"),
    ("class-qualified-miss-does-not-find-outer-enumerator", "enum E{A};struct S{};int f(){return S::A;}", "cpp.undeclared"),
    ("scoped-member-enumerator-not-in-class-scope", "struct S{enum class E{A};};int f(){return S::A;}", "cpp.undeclared"),
    ("incomplete-class-qualifier", "struct S;int f(){return S::A;}", "cpp.incomplete-class"),
)

UNIMPLEMENTED_CLASS_ENUM_CASES = (
    # Original object/member cases moved verbatim to test_cpp_object_enumerators.
    "struct S{enum{A};};",
    "struct S{enum E:int;};enum S::E:int{A};",
    "struct S{enum E{A};};template<class T>int f(T x){return S::A;}",
    "struct S{struct Inner{enum E{A};};};",
)


@pytest.mark.parametrize("source", (ORIGINAL_CLASS_ENUM, ORIGINAL_MEMBER_ALIAS))
def test_formerly_gated_original_class_name_sources_are_preserved_verbatim(source):
    CppParser().parse(source, "original.cpp")


@pytest.mark.parametrize("name,source,value", CLASS_ENUM_CASES, ids=[c[0] for c in CLASS_ENUM_CASES])
def test_class_enum_owned_compilation_is_deterministic(name, source, value):
    units = ((name + ".cpp", source),)
    assert compile_host_cpp(units) == compile_host_cpp(units)


@pytest.mark.parametrize("name,source,code", INVALID_CLASS_ENUM_CASES,
                         ids=[c[0] for c in INVALID_CLASS_ENUM_CASES])
def test_class_enum_access_and_name_errors_precede_emission(name, source, code, monkeypatch):
    from pcc.backend import owned_object_emit
    def forbidden(*args, **kwargs):
        raise AssertionError("invalid class enum semantics reached object emission")
    monkeypatch.setattr(owned_object_emit, "emit_owned_object", forbidden)
    with pytest.raises(CppDiagnostic) as error:
        compile_host_cpp(((name + ".cpp", source),))
    assert error.value.code == code


@pytest.mark.parametrize("source", UNIMPLEMENTED_CLASS_ENUM_CASES)
def test_class_enum_remaining_dependencies_are_explicit(source):
    with pytest.raises(CppDiagnostic) as error:
        CppParser().parse(source, "class-enum-boundary.cpp")
    assert error.value.code.startswith("cpp.unsupported.")


def test_class_enum_identity_access_and_storage_are_separate():
    parser = CppParser()
    parser.parse("struct S{enum E{A=42};using Alias=E;int value;};")
    owner = parser.scope.lookup("S").dtype
    scope = owner.definition.scope
    dtype = scope.names["E"].dtype
    assert dtype.declaration_scope is scope and dtype.enclosing_class is owner
    assert dtype.qualified_name == ("S", "E") and scope.names["Alias"].dtype is dtype
    assert dtype.scope.names["A"] is scope.names["A"]
    assert scope.names["A"].owner is scope
    assert scope.names["E"].access == scope.names["A"].access == "public"
    assert [member.name for member in owner.definition.members] == ["value"]
    assert mangle_function("f", (dtype, PointerType(owner))) == "_Z1fN1S1EEPS_"
    assert mangle_function("f", (PointerType(owner), dtype)) == "_Z1fP1SNS_1EE"


@pytest.mark.integration
@pytest.mark.parametrize("name,source,value", CLASS_ENUM_CASES, ids=[c[0] for c in CLASS_ENUM_CASES])
def test_class_enum_owned_execution_matches_explicit_cpp17_reference(name, source, value, tmp_path, monkeypatch):
    execute_and_record(tmp_path, monkeypatch, ((name + ".cpp", source),), value)


@pytest.mark.integration
def test_member_enum_value_abi_across_independent_translation_units(tmp_path, monkeypatch):
    declaration = (
        "namespace n{struct S{enum class E:unsigned char{Value=42};};"
        "S::E echo(S::E value);}"
    )
    units = (("helper.cpp", declaration + "namespace n{S::E echo(S::E value){return value;}}"),
             ("entry.cpp", declaration + "int cpp_test_entry(){return int(n::echo(n::S::E::Value));}"))
    execute_and_record(tmp_path, monkeypatch, units, 42)


@pytest.mark.integration
def test_member_enum_class_prefix_substitutions_match_independent_symbols(tmp_path, monkeypatch):
    source = (
        "struct S{enum class E{A};};int f(S::E a,S*b){return 0;}"
        "int f(S*a,S::E b){return 0;}int f(S::E a,S::E b){return 0;}"
        "namespace n{struct S{enum class E{A};};int f(S::E a,S*b){return 0;}"
        "int f(S*a,const S::E*b,S::E c){return 0;}}int cpp_test_entry(){return 0;}"
    )
    artifact = owned_artifact((("member-enum-symbols.cpp", source),), monkeypatch)
    unit, assembly = tmp_path / "member-enum-symbols.cpp", tmp_path / "reference.s"
    unit.write_text(source)
    compiler = shutil.which(os.environ.get("PCC_CPP_ORACLE", "g++"))
    assert compiler, "explicit external C++17 reference unavailable"
    result = subprocess.run([compiler, "-std=c++17", "-pedantic-errors", "-O0", "-S", str(unit),
                             "-o", str(assembly)], capture_output=True, timeout=20)
    assert result.returncode == 0, result.stderr
    reference = set(re.findall(r"^\s*\.globl\s+(_Z\w+)", assembly.read_text(), re.M))
    actual = {symbol.name for symbol in parse_relocatable(artifact.objects[0]).symbols
              if symbol.binding == 1 and symbol.section_index and symbol.name.startswith("_Z")}
    assert actual == reference and len(actual) == 6
    (tmp_path / "owned.o").write_bytes(artifact.objects[0])
    (tmp_path / "exports.json").write_text(json.dumps({"owned": sorted(actual), "reference": sorted(reference)}, indent=2))
