"""Original namespace-enum C++17 regressions and labeled G++ comparisons.

Rules and target-policy references are recorded in test_cpp_enum_model.py.
These cases do not claim complete enum, template, class or TinyXML2 support.
PCC compilation uses its owned frontend/backend; G++ is an independent test
reference only, never a compiler fallback or final runtime dependency.
"""

import pytest

from pcc.frontends.cpp import CppDiagnostic, CppParser
from pcc.frontends.cpp.enums import EnumType
from tests.cpp.owned_execution import compile_host_cpp
from tests.cpp.test_cpp_class_execution import execute_and_record


ENUM_CASES = (
    ("bool-underlying-explicit-bool-initializers",
     "enum E:bool{Off=false,On=true,Again=bool(0)};"
     "int cpp_test_entry(){return 41+On+Off+Again;}", 42),
    ("fixed-bool-enum-promotion-is-not-boolean-conversion",
     "enum First:bool{Off,On};enum Second:bool{Answer=On};"
     "int cpp_test_entry(){return 41+Answer;}", 42),
    ("namespace-unscoped-flags-and-previous-enumerators",
     "namespace flags{enum Mode{A=1,B=1<<1,Both=A|B,Next};}"
     "int cpp_test_entry(){return flags::Both*10+flags::Mode::Next+8;}", 42),
    ("scoped-explicit-conversion-and-same-type-comparisons",
     "enum class E{Low=17,High=25};int cpp_test_entry(){E e=E::High;"
     "return int(E::Low)+int(e)+int(e<E::Low)+int(e!=E::High);}", 42),
    ("fixed-opaque-enum-value-parameter-and-return",
     "enum E:unsigned char;E identity(E e){return e;}enum E:unsigned char{A=41,B};"
     "int cpp_test_entry(){return identity(E::B);}", 42),
    ("scoped-opaque-forward-keeps-default-int-identity",
     "enum class E;E identity(E e){return e;}enum class E{Answer=42};"
     "int cpp_test_entry(){return int(identity(E::Answer));}", 42),
    ("enum-adl-adds-better-original-namespace-overload",
     "int choose(int x){return 11;}namespace n{enum E{A=7};int choose(E e){return 42;}}"
     "int cpp_test_entry(){return choose(n::A);}", 42),
    ("enum-alias-retains-original-associated-namespace",
     "namespace n{enum E{A=7};int choose(E e){return 42;}}"
     "namespace other{using Alias=n::E;}int choose(int n){return 11;}"
     "int cpp_test_entry(){other::Alias e=other::Alias::A;return choose(e);}", 42),
    ("qualified-call-suppresses-enum-adl",
     "int choose(int x){return 42;}namespace n{enum E{A};int choose(E e){return 11;}}"
     "int cpp_test_entry(){return ::choose(n::A);}", 42),
    ("same-underlying-types-retain-nominal-overloads",
     "enum E:int{A};enum F:int{B};int choose(E e){return 17;}int choose(F f){return 25;}"
     "int cpp_test_entry(){return choose(A)+choose(B);}", 42),
    ("same-spelled-namespace-enums-are-distinct",
     "namespace a{enum E:int{V};}namespace b{enum E:int{V};}"
     "int choose(a::E e){return 17;}int choose(b::E e){return 25;}"
     "int cpp_test_entry(){return choose(a::V)+choose(b::V);}", 42),
    ("unfixed-range-promotion-is-not-unsigned-storage-type",
     "enum E{A=7};int choose(int n){return 42;}int choose(unsigned int n){return 11;}"
     "int cpp_test_entry(){return choose(A);}", 42),
    ("fixed-underlying-promotion-wins-over-promoted-underlying",
     "enum E:unsigned char{A=7};int choose(unsigned char n){return 42;}"
     "int choose(int n){return 11;}int cpp_test_entry(){return choose(A);}", 42),
    ("implicit-successor-does-not-wrap-unsigned-body-type",
     "enum E{Maximum=4294967295u,Next};int cpp_test_entry(){return 41+int(Next==4294967296ull);}", 42),
    ("explicit-unsigned-constant-expression-does-wrap",
     "enum E{Maximum=4294967295u,Wrapped=Maximum+1u,Next};"
     "int cpp_test_entry(){return 41+Next+Wrapped;}", 42),
    ("enum-scalar-fields-copy-without-class-value-abi",
     "enum class E:unsigned char{A=17,B=25};struct S{E first;E second;};"
     "int cpp_test_entry(){S s{E::A,E::A};E value=E::B;s.second=value;"
     "return int(s.first)+int(s.second);}", 42),
    ("aggregate-enum-field-single-element-copy-list",
     "enum class E:unsigned char{Answer=42};struct S{E value;};"
     "int cpp_test_entry(){S s{{E::Answer}};return int(s.value);}", 42),
    ("const-fixed-int-enum-proves-integral-narrowing-exception",
     "enum E:int{Answer=42};struct S{unsigned char value;};"
     "int cpp_test_entry(){const E e=Answer;S s{e};return s.value;}", 42),
    ("unfixed-range-contained-conversion-needs-no-constant-read",
     "enum E{Answer=42};struct S{unsigned char value;};"
     "int cpp_test_entry(){E e=Answer;S s{e};return s.value;}", 42),
    ("enum-functional-value-initialization-produces-zero",
     "enum class E:int{Nonzero=7};int cpp_test_entry(){E e=E();return int(e)+42;}", 42),
    ("empty-enum-can-be-zero-value-initialized",
     "enum Empty{};int cpp_test_entry(){Empty e=Empty();return int(e)+42;}", 42),
    ("fixed-enum-complete-during-its-own-body",
     "enum class E:int{Zero=int(E()),Answer=Zero+42};"
     "int cpp_test_entry(){return int(E::Answer);}", 42),
    ("enum-declaration-coexists-with-unrelated-scalar-template",
     "enum E{Answer=7};template<class T>T identity(T x){return x;}"
     "int cpp_test_entry(){return identity(42);}", 42),
    ("enum-pointer-qualification-and-indirect-read",
     "enum E:unsigned char{Answer=42};int read(const E*p){return *p;}"
     "int cpp_test_entry(){E e=Answer;return read(&e);}", 42),
    ("using-unscoped-enumerator-preserves-original-symbol",
     "namespace n{enum E{Answer=42};}using n::E::Answer;"
     "int cpp_test_entry(){return Answer;}", 42),
    ("negative-enumerator-projection-keeps-signed-value",
     "enum E{Negative=-42,Zero};int cpp_test_entry(){return -Negative;}", 42),
    ("full-width-unsigned-enumerator-is-not-signed-or-host-int",
     "enum E{Maximum=18446744073709551615ull};"
     "int cpp_test_entry(){return 42*int(Maximum==18446744073709551615ull);}", 42),
    ("enum-constant-short-circuit-excludes-undefined-operation",
     "enum E{Answer=42+(false&&(1/0))};int cpp_test_entry(){return Answer;}", 42),
    ("underlying-type-alias-and-provisional-fixed-successor",
     "using Byte=unsigned char;enum E:Byte{Before=41,Answer};"
     "int cpp_test_entry(){return Answer;}", 42),
    ("scoped-enum-explicit-conversion-in-another-enumerator",
     "enum class First{Answer=42};enum Second{Answer=int(First::Answer)};"
     "int cpp_test_entry(){return Answer;}", 42),
    ("bool-underlying-range-and-promotions",
     "enum E:bool{Off,On};int cpp_test_entry(){return 41+On;}", 42),
    ("omitted-enum-subobject-value-initializes-to-zero",
     "enum class E:int{Nonzero=7};struct S{int answer;E e;};"
     "int cpp_test_entry(){S s{42};return s.answer+int(s.e);}", 42),
)


INVALID_ENUM_CASES = (
    ("bool-enum-rejects-integer-zero-conversion-sequence",
     "enum E:bool{A=0};", "cpp.enum-converted-constant"),
    ("bool-enum-rejects-integer-one-conversion-sequence",
     "enum E:bool{A=1u};", "cpp.enum-converted-constant"),
    ("bool-enum-rejects-non-bool-enum-boolean-conversion",
     "enum First:unsigned char{A=1};enum Second:bool{B=A};", "cpp.enum-converted-constant"),
    ("enumerator-value-is-not-an-enum-qualifier",
     "enum E{A=42};int cpp_test_entry(){return A::A;}", "cpp.not-a-namespace"),
    ("qualified-enumerator-value-is-not-a-further-qualifier",
     "enum E{A=42};int cpp_test_entry(){return E::A::A;}", "cpp.not-a-namespace"),
    ("enum-object-is-not-an-enum-qualifier",
     "enum class E{A=42};int f(){E e=E::A;return int(e::A);}", "cpp.not-a-namespace"),
    ("scoped-no-implicit-integer-conversion",
     "enum class E{A};int f(){int n=E::A;return n;}", "cpp.invalid-conversion"),
    ("scoped-no-implicit-bool-condition",
     "enum class E{A};int f(){if(E::A)return 1;return 0;}", "cpp.invalid-conversion"),
    ("scoped-enumerator-using-is-not-cxx17",
     "enum class E{A};using E::A;", "cpp.using-scoped-enumerator"),
    ("zero-enumerator-is-not-null-pointer-literal",
     "enum E{Zero=0};int f(){int*p=Zero;return 0;}", "cpp.invalid-conversion"),
    ("converted-zero-enumerator-is-not-null-pointer-literal",
     "enum E{Zero=0};int f(){int*p=int(Zero);return 0;}", "cpp.invalid-conversion"),
    ("fixed-enumerator-positive-overflow", "enum E:unsigned char{A=256};", "cpp.enum-value-range"),
    ("fixed-enumerator-negative-overflow", "enum E:unsigned char{A=-1};", "cpp.enum-value-range"),
    ("fixed-successor-cannot-widen", "enum E:unsigned char{A=255,B};", "cpp.enum-value-range"),
    ("bool-successor-cannot-wrap", "enum E:bool{A,B,C};", "cpp.enum-value-range"),
    ("implicit-successor-no-supported-integer",
     "enum E{A=18446744073709551615ull,B};", "cpp.enum-value-range"),
    ("duplicate-enumerator", "enum E{A,A};", "cpp.redeclaration"),
    ("namespace-enumerator-conflicts-across-enums", "enum E{A};enum F{A};", "cpp.redeclaration"),
    ("enum-redefinition", "enum E{};enum E{};", "cpp.enum-redefinition"),
    ("scopedness-mismatch", "enum E:int;enum class E{};", "cpp.enum-redeclaration"),
    ("underlying-type-mismatch", "enum E:int;enum E:unsigned int{};", "cpp.enum-underlying-mismatch"),
    ("enum-base-is-not-floating", "enum E:long double{};", "cpp.enum-underlying-type"),
    ("floating-enumerator-is-not-integral", "enum E{A=1.0};", "cpp.enum-initializer-type"),
    ("evaluated-nonconstexpr-call",
     "int value(){return 7;}enum E{A=value()};", "cpp.enum-constant-expression"),
    ("evaluated-division-by-zero", "enum E{A=1/0};", "cpp.enum-constant-expression"),
    ("evaluated-signed-overflow", "enum E{A=2147483647+1};", "cpp.enum-constant-expression"),
    ("scoped-arithmetic-has-no-implicit-promotion",
     "enum class E{A};int f(){return int(E::A+E::A);}", "cpp.invalid-operands"),
    ("distinct-scoped-enums-cannot-compare",
     "enum class E{A};enum class F{B};int f(){return int(E::A==F::B);}", "cpp.invalid-operands"),
    ("same-underlying-does-not-permit-distinct-enum-copy",
     "enum E:int{A};enum F:int{B};int f(){E e=B;return 0;}", "cpp.invalid-conversion"),
    ("integer-to-enum-implicit-copy-is-invalid",
     "enum E:int{A};int f(){E e=0;return 0;}", "cpp.invalid-conversion"),
    ("mutable-fixed-enum-does-not-prove-narrowing",
     "enum E:int{A=42};struct S{unsigned char value;};int f(){E e=A;S s{e};return 0;}",
     "cpp.narrowing-conversion"),
    ("unfixed-type-incomplete-until-closing-brace",
     "enum E{A=int(E())};", "cpp.incomplete-enum"),
)


UNIMPLEMENTED_ENUM_CASES = (
    ("local-enum-identity", "int f(){enum E{A};return A;}"),
    ("class-member-enum-and-qualified-access", "struct S{enum E{A};};int f(){return S::A;}"),
    ("anonymous-enum-identity", "enum{A=42};int f(){return A;}"),
    ("enum-template-deduction",
     "enum E{A};template<class T>T id(T x){return x;}int f(){id(A);return 0;}"),
    ("enum-template-nondependent-name",
     "enum E{A};template<class T>int f(T x){return A;}int g(){return f(1);}"),
    ("enum-return-into-dependent-template-body",
     "enum E{A};E get(){return A;}template<class T>int f(T x){return get()+x;}"
     "int g(){return f(1);}"),
    ("scoped-enum-return-comparison-in-template",
     "enum class E{A};E get(){return E::A;}"
     "template<class T>bool f(T x){return get()==get();}"
     "int cpp_test_entry(){return int(f(1));}"),
    ("scoped-enum-return-explicit-cast-in-template",
     "enum class E{A};E get(){return E::A;}"
     "template<class T>int f(T x){return int(get());}"
     "int cpp_test_entry(){return f(1);}"),
    ("enum-pointer-return-in-template",
     "enum class E{A};E *get(){return nullptr;}"
     "template<class T>bool f(T x){return get()==nullptr;}"
     "int cpp_test_entry(){return int(f(1));}"),
    ("integer-to-enum-explicit-cast", "enum E:int{A};int f(){return int(E(0));}"),
    ("direct-list-fixed-enum", "enum class E:int{A};int f(){E e{0};return int(e);}"),
    ("copy-list-same-enum", "enum E{A};int f(){E e={A};return e;}"),
    ("unmodeled-signed-constant-policy", "enum E{A=-1>>1};"),
    ("cxx17-range-versus-storage-edge", "enum E{A=-2147483647-1};"),
    ("unscoped-tag-hiding", "enum E{E};"),
    ("enum-definition-object-declarator", "enum E{A} object;"),
    ("bare-unfixed-elaborated-declaration", "enum E;"),
)


@pytest.mark.parametrize("name,source,value", ENUM_CASES, ids=[case[0] for case in ENUM_CASES])
def test_namespace_enum_compilation_is_deterministic_and_owned(name, source, value):
    units = ((name + ".cpp", source),)
    first, second = compile_host_cpp(units), compile_host_cpp(units)
    assert first == second
    assert first.owner == "host-python/pcc-cpp/pcc-self/pcc-elf"
    assert first.executable[:4] == b"\x7fELF"


def _reject_before_emission(name, source, monkeypatch, code=None):
    from pcc.backend import owned_object_emit

    def forbidden(*args, **kwargs):
        raise AssertionError("invalid/unimplemented enum semantics reached emission")

    monkeypatch.setattr(owned_object_emit, "emit_owned_object", forbidden)
    with pytest.raises(CppDiagnostic) as error:
        compile_host_cpp(((name + ".cpp", source),))
    if code is None:
        assert error.value.code.startswith("cpp.unsupported.")
    else:
        assert error.value.code == code
    assert error.value.coord.file == name + ".cpp"


@pytest.mark.parametrize("name,source,code", INVALID_ENUM_CASES,
                         ids=[case[0] for case in INVALID_ENUM_CASES])
def test_invalid_enum_semantics_are_diagnosed_before_emission(name, source, code, monkeypatch):
    _reject_before_emission(name, source, monkeypatch, code)


@pytest.mark.parametrize("name,source", UNIMPLEMENTED_ENUM_CASES,
                         ids=[case[0] for case in UNIMPLEMENTED_ENUM_CASES])
def test_unimplemented_enum_shapes_do_not_flatten_into_c_integers(name, source, monkeypatch):
    _reject_before_emission(name, source, monkeypatch)


def test_public_parser_retains_enum_identity_in_alias_and_function_signature():
    parser = CppParser("x86_64-unknown-linux-gnu")
    parser.parse("namespace n{enum E:unsigned char{A=42};}using Alias=n::E;"
                 "Alias identity(Alias e){return e;}")
    dtype = parser.scope.lookup("n").namespace_scope.lookup("E").dtype
    alias = parser.scope.lookup("Alias")
    function = parser.scope.lookup("identity").overloads[0]
    assert isinstance(dtype, EnumType) and dtype.defined
    assert alias.dtype is dtype
    assert function.dtype is dtype and function.parameters == (dtype,)
    assert dtype.underlying == "unsigned char"
    assert dtype.qualified_name == ("n", "E")


@pytest.mark.integration
@pytest.mark.parametrize("name,source,value", ENUM_CASES, ids=[case[0] for case in ENUM_CASES])
def test_namespace_enum_owned_execution_matches_explicit_cpp17_reference(
    name, source, value, tmp_path, monkeypatch,
):
    execute_and_record(tmp_path, monkeypatch, ((name + ".cpp", source),), value)
