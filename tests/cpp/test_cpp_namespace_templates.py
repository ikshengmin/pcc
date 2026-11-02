"""Namespace function templates: source-only C++17 obligations for CI.

N4659 [temp.pre], [temp.fct], [temp.res], [temp.dep.candidate],
[namespace.udecl], [basic.link], and Itanium ABI 5.1.2 / 5.1.8:
https://timsong-cpp.github.io/cppwp/n4659/temp.fct
https://timsong-cpp.github.io/cppwp/n4659/temp.dep.candidate
https://timsong-cpp.github.io/cppwp/n4659/basic.link
https://itanium-cxx-abi.github.io/cxx-abi/abi.html#mangling

These independently authored original inputs also feed the native-output /
reference lane. No local execution, native pcc1 or complete C++17 claim.
"""

import pytest

from pcc.frontends.c.ast import c_ast
from pcc.frontends.cpp import CppDiagnostic, CppParser
from pcc.frontends.cpp.sema import Scope, Symbol
from pcc.frontends.cpp.templates import TypeParameter, mangle_instance
from tests.cpp.owned_execution import compile_host_cpp
from tests.cpp.test_cpp_grouped_names import owned_only


ORIGINAL = "namespace A {template<class T>T f(T x){return x;}}"

CASES = (
    ("original-namespace-template", (("entry.cpp", ORIGINAL +
      "int cpp_test_entry(){return A::f(42);}"),)),
    ("same-basename-distinct-owners", (("entry.cpp",
      "template<class T>T f(T x){return x+10;}"
      "namespace A{template<class T>T f(T x){return x+14;}}"
      "namespace B{template<class T>T f(T x){return x+18;}}"
      "int cpp_test_entry(){return ::f(0)+A::f(0)+B::f(0);}"),)),
    ("nested-namespace-alias-and-local-hiding", (("entry.cpp",
      "namespace A::B{template<class T>T f(T x){return x;}}namespace Alias=A::B;"
      "int cpp_test_entry(){int A=7;int f=8;return Alias::f(27)+A+f;}"),)),
    ("definition-scope-alias-constant-helper", (("entry.cpp",
      "using I=long;const int offset=1;int helper(int x){return 2;}"
      "namespace A{using I=int;const int offset=17;int helper(I x){return x;}"
      "template<class T>I f(T x){return helper(I(x))+offset;}}"
      "int cpp_test_entry(){int offset=100;return A::f(25L);}"),)),
    ("definition-point-dependent-overload-snapshot", (("entry.cpp",
      "namespace A{int helper(long x){return 42;}"
      "template<class T>int f(T x){return helper(x);}"
      "int helper(int x){return 1;}}int helper(int x){return 2;}"
      "int cpp_test_entry(){return A::f(0);}"),)),
    ("reopened-renamed-forward-declaration", (("entry.cpp",
      "namespace A{template<class T,T N>T f(T x);}"
      "int cpp_test_entry(){return A::f<int,17>(25);}"
      "namespace A{template<class U,U M>U f(U value){return value+M;}}"),)),
    ("using-import-keeps-instance-owner", (("entry.cpp",
      "namespace A{template<class T>T f(T x){return x;}}using A::f;"
      "namespace B{using A::f;}namespace Alias=A;"
      "int cpp_test_entry(){return f(10)+B::f(14)+Alias::f(18);}"),)),
    ("using-snapshot-precedes-new-overload", (("entry.cpp",
      "namespace A{template<class T>int f(T x){return 20;}}using A::f;"
      "namespace A{int f(int x){return 22;}}"
      "int cpp_test_entry(){return f(1)+A::f(1);}"),)),
    ("inline-namespace-canonical-template", (("entry.cpp",
      "namespace A{inline namespace V{template<class T>T f(T x){return x;}}}"
      "int cpp_test_entry(){return A::f(20)+A::V::f(22);}"),)),
    ("namespace-recursion", (("entry.cpp",
      "namespace A{template<class T>T sum(T x){if(x==0)return T(0);"
      "return x+sum(x-T(1));}}int cpp_test_entry(){return A::sum(8)+6;}"),)),
    ("local-type-alias-and-block-shadow", (("entry.cpp",
      "namespace A{template<class T>T f(T x){using U=T;U y=x;"
      "{int x=2;y=y+T(x);}return y;}}int cpp_test_entry(){return A::f(40);}"),)),
    ("namespace-main-not-program-main", (("entry.cpp",
      "namespace A{template<class T>T main(T x){return x;}}"
      "int cpp_test_entry(){return A::main(42);}"),)),
    ("namespace-mixed-type-value-parameters", (("entry.cpp",
      "namespace A::B{template<class T,int N,class U>T add(T x,U y){"
      "return x+T(N)+T(y);}}int cpp_test_entry(){return int(A::B::add<long,2>(10,30));}"),)),
    ("anonymous-internal-specializations-across-tu", (
      ("helper.cpp", "namespace{namespace N{template<class T>T f(T x){return x+10;}}}"
       "int helper(){return N::f(0);}"),
      ("entry.cpp", "namespace{namespace N{template<class T>T f(T x){return x+32;}}}"
       "int helper();int cpp_test_entry(){return N::f(0)+helper();}"))),
)


INVALID = (
    ("redefinition", "namespace A{template<class T>T f(T x){return x;}"
     "template<class U>U f(U x){return x;}}", "cpp.redefinition"),
    ("using-declaration-not-new-owner", "namespace A{template<class T>T f(T x){return x;}}"
     "using A::f;template<class U>U f(U x){return x;}", "cpp.using-declaration-conflict"),
    ("c-language-linkage", 'extern "C"{namespace A{template<class T>T f(T x){return x;}}}',
     "cpp.template-c-linkage"),
    ("conflicting-deduction", "namespace A{template<class T>T f(T x,T y){return x+y;}}"
     "int cpp_test_entry(){return A::f(1,2L);}", "cpp.no-viable-overload"),
    ("invalid-selected-body-no-fallback", "namespace A{template<class T>int f(T x){return x%2;}"
     "int f(long x){return 42;}}int cpp_test_entry(){return A::f(1.5);}", "cpp.invalid-operands"),
    ("nondependent-name-not-visible", "namespace A{template<class T>int f(T x){return missing(1);}"
     "int missing(int x){return x;}}", "cpp.undeclared"),
)


DEFERRED = (
    ("qualified-template-definition", "namespace A{template<class T>T f(T x);}"
     "template<class T>T A::f(T x){return x;}int cpp_test_entry(){return A::f(42);}",
     "cpp.unsupported.namespace-templates"),
    ("namespace-pointer-template", "namespace A{template<class T>T*f(T*x){return x;}}"
     "int cpp_test_entry(){int x=42;return *A::f(&x);}", "cpp.unsupported.pointer-templates"),
)


MULTI_TU_NAMED_TEMPLATE = (
    ("helper.cpp", "namespace A{template<class T>T f(T x){return x;}}"
     "int helper(){return A::f(17);}"),
    ("entry.cpp", "namespace A{template<class T>T f(T x){return x;}}"
     "int helper();int cpp_test_entry(){return A::f(25)+helper();}"),
)


def parse(source):
    parser = CppParser("x86_64-unknown-linux-gnu")
    return parser, parser.parse(source, "namespace-template.cpp")


def bodies(tree):
    return {node.decl.name: node for node in tree.ext if isinstance(node, c_ast.FuncDef)}


@pytest.mark.parametrize("name,units", CASES, ids=[row[0] for row in CASES])
def test_namespace_template_original_compiles_deterministically(owned_only, name, units):
    first = compile_host_cpp(units)
    assert first == compile_host_cpp(units)
    assert first.owner == "host-python/pcc-cpp/pcc-self/pcc-elf"


def test_namespace_specialization_identity_is_not_basename_or_import_scope():
    parser, tree = parse(CASES[1][1][0][1])
    owners = [parser.scope] + [parser.scope.names[n].namespace_scope for n in ("A", "B")]
    origins = [owner.names["f"].overloads[0] for owner in owners]
    instances = [origin.instances[("int",)] for origin in origins]
    assert len({id(instance) for instance in instances}) == 3
    assert [instance.owner for instance in instances] == owners
    assert [instance.c_name for instance in instances] == [
        "_Z1fIiET_S0_", "_ZN1A1fIiEET_S1_", "_ZN1B1fIiEET_S1_",
    ]
    assert set(bodies(tree)) == {"_Z14cpp_test_entryv", *(i.c_name for i in instances)}


def test_reopened_template_reuses_declaration_and_definition_parameter_identities():
    parser, tree = parse(CASES[5][1][0][1])
    owner = parser.scope.names["A"].namespace_scope
    group = owner.names["f"]
    assert len(group.overloads) == 1
    origin = group.overloads[0]
    assert origin.owner is owner and origin.declaration_scopes == [owner]
    assert origin.parameter_symbols[0].name == "value"
    assert len(origin.instances) == 1
    assert "_ZN1A1fIiLi17EEET_S1_" in bodies(tree)


def test_imports_aliases_and_inline_lookup_share_canonical_instance():
    parser, tree = parse(CASES[6][1][0][1])
    owner = parser.scope.names["A"].namespace_scope
    origin = owner.names["f"].overloads[0]
    assert parser.scope.names["f"].overloads[0] is origin
    assert parser.scope.names["B"].namespace_scope.names["f"].overloads[0] is origin
    assert tuple(origin.instances) == (("int",),)
    assert origin.instances[("int",)].owner is owner
    assert set(bodies(tree)) == {"_Z14cpp_test_entryv", "_ZN1A1fIiEET_S1_"}
    parser, tree = parse(CASES[8][1][0][1])
    inline = parser.scope.names["A"].namespace_scope.names["V"].namespace_scope
    assert inline.names["f"].overloads[0].instances[("int",)].owner is inline
    assert "_ZN1A1V1fIiEET_S2_" in bodies(tree)


def test_internal_namespace_instance_and_its_prototype_keep_static_storage():
    parser, tree = parse(CASES[-1][1][1][1])
    owner = parser.scope.unnamed_namespace.names["N"].namespace_scope
    origin = owner.names["f"].overloads[0]
    instance = origin.instances[("int",)]
    assert instance.owner is owner and instance.internal_linkage
    projected = [node.decl if isinstance(node, c_ast.FuncDef) else node for node in tree.ext]
    declarations = [node for node in projected if node.name == instance.c_name]
    assert len(declarations) == 2
    assert all(node.storage == ["static"] for node in declarations)
    assert instance.c_name == "_ZN12_GLOBAL__N_11N1fIiEET_S2_"


@pytest.mark.parametrize("path,expected", (
    ((), "_Z1fIiET_S0_"),
    (("A",), "_ZN1A1fIiEET_S1_"),
    (("A", "B"), "_ZN1A1B1fIiEET_S2_"),
    (("std",), "_ZSt1fIiET_S0_"),
    (("std", "V"), "_ZNSt1V1fIiEET_S1_"),
))
def test_namespace_prefixes_precede_template_parameter_substitutions(path, expected):
    # Direct ABI model only: do not inject user functions into reserved ::std
    # in executable C++ source. The execution lane compares legal named scopes.
    owner = Scope()
    for part in path:
        owner = owner.open_namespace(part, None)
    parameter = TypeParameter(0, "T", None)
    function = Symbol("f", "function", None, dtype=parameter, parameters=(parameter,))
    function.owner = owner
    function.template_parameters = (parameter,)
    assert mangle_instance(function, ("int",)) == expected


@pytest.mark.parametrize("name,source,code", INVALID, ids=[row[0] for row in INVALID])
def test_invalid_namespace_template_stops_before_emission(name, source, code, monkeypatch):
    from pcc.backend import owned_object_emit

    def forbidden(*args, **kwargs):
        raise AssertionError("invalid namespace template reached object emission")

    monkeypatch.setattr(owned_object_emit, "emit_owned_object", forbidden)
    with pytest.raises(CppDiagnostic) as error:
        compile_host_cpp(((name + ".cpp", source),))
    assert error.value.code == code


@pytest.mark.parametrize("name,source,code", DEFERRED, ids=[row[0] for row in DEFERRED])
def test_valid_remaining_namespace_template_boundaries_are_explicit(name, source, code):
    with pytest.raises(CppDiagnostic) as error:
        parse(source)
    assert error.value.code == code


def test_named_namespace_template_multi_tu_keeps_weak_odr_boundary(owned_only):
    # [basic.def.odr], Itanium 5.2.6: namespace mangling is not COMDAT support.
    # Do not hide an external specialization by marking every instance static.
    from pcc.backend.elf_x86_64 import ElfError

    with pytest.raises(ElfError, match="duplicate strong definition of '_ZN1A1fIiEET_S1_'"):
        compile_host_cpp(MULTI_TU_NAMED_TEMPLATE)
