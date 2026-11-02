"""Local direct-list C1 initialization: source-only C++17 obligations, UNRUN.

N4659 [dcl.init.list]/3.4,3.6,4,7, [over.match.list], [over.best.ics]/6,
[dcl.init.ref], [stmt.jump]/2 and [stmt.return]/3:
https://timsong-cpp.github.io/cppwp/n4659/dcl.init.list
https://timsong-cpp.github.io/cppwp/n4659/over.match.list
https://timsong-cpp.github.io/cppwp/n4659/over.best.ics

These are generic initialization prerequisites, not TinyXML2 project acceptance.
The execution corpus uses the existing fixed-target direct ELF adapter; neither
native pcc1 nor platform/merged-stackmap multi-TU linking is claimed.
"""

import pytest

from pcc.frontends.c.ast import c_ast
from pcc.frontends.cpp import CppDiagnostic, CppParser
from tests.cpp.owned_execution import compile_host_cpp
from tests.cpp.test_cpp_owned_execution import owned_only


ORIGINAL_EMPTY = 'struct S{S();};S::S(){}int f(){S s{};return 0;}'
PAIR_CLASS = "struct S{S(int a,int b):value(a+b){}int value;};"
TRACE_CLASS = ("struct T{T(int*p,int n):p(p),n(n){}~T(){*p=*p*10+n;}"
               "int*p;int n;};")

SINGLE_PROGRAMS = (
    ("original-empty-direct-list", ORIGINAL_EMPTY + "int cpp_test_entry(){return f()+42;}"),
    ("empty-calls-user-provided-body", "struct S{S(){value=42;}int value;};"
     "int cpp_test_entry(){S s{};return s.value;}"),
    ("two-arguments", PAIR_CLASS + "int cpp_test_entry(){S s{20,22};return s.value;}"),
    ("trailing-comma", PAIR_CLASS + "int cpp_test_entry(){S s{20,22,};return s.value;}"),
    ("promotion-overload", "struct S{S(int):value(42){}S(double):value(1){}int value;};"
     "int cpp_test_entry(){short n=7;S s{n};return s.value;}"),
    ("constant-fits-narrow-parameter", "struct S{S(signed char n):value(n){}int value;};"
     "int cpp_test_entry(){S s{42};return s.value;}"),
    ("typed-constant-expression-fits", "struct S{S(unsigned char n):value(n){}int value;};"
     "int cpp_test_entry(){const int n=6;S s{n*7};return s.value;}"),
    ("character-type-overload", "struct S{S(char):value(42){}S(int):value(1){}int value;};"
     "int cpp_test_entry(){S s{'*'};return s.value;}"),
    ("dynamic-integral-widening", "struct S{S(long long n):value(n){}long long value;};"
     "int cpp_test_entry(){int n=42;S s{n};return int(s.value);}"),
    ("floating-promotion", "struct S{S(double n):value(n){}double value;};"
     "int cpp_test_entry(){float n=42.0f;S s{n};return int(s.value);}"),
    ("nullptr-selects-pointer-not-bool", "struct S{S(int*p):value(42){}S(bool):value(1){}int value;};"
     "int cpp_test_entry(){S s{nullptr};return s.value;}"),
    ("array-argument-decay", "struct S{S(int*p):value(p[1]){}int value;};"
     "int cpp_test_entry(){int values[2]={1,42};S s{values};return s.value;}"),
    ("value-snapshot-before-next-clause", PAIR_CLASS +
     "int cpp_test_entry(){int n=20;S s{n,n=22};if(n!=22)return 1;return s.value;}"),
    ("clause-effects-left-to-right-once", "int trace=0;int step(int n){trace=trace*10+n;return n;}"
     "struct S{S(int a,int b,int c){value=1;if(a==1&&b==2&&c==3&&trace==123)value=42;}int value;};"
     "int cpp_test_entry(){S s{step(1),step(2),step(3)};if(trace!=123)return 1;return s.value;}"),
    ("reference-address-not-value-snapshot", "struct S{S(int&r,int ignored){r+=1;value=r;}int value;};"
     "int cpp_test_entry(){int n=1;S s{n,n=41};if(n!=42)return 1;return s.value;}"),
    ("effectful-reference-address-once", "int calls=0;int*receiver(int*p){calls+=1;return p;}"
     "struct S{S(int&r,int ignored){r+=1;value=r;}int value;};"
     "int cpp_test_entry(){int n=1;S s{*receiver(&n),n=41};if(calls!=1||n!=42)return 1;return s.value;}"),
    ("const-reference-direct-binding", "struct S{S(const int&r):value(r){}int value;};"
     "int cpp_test_entry(){const int n=42;S s{n};return s.value;}"),
    ("const-object-cleanup", "struct S{S(int*p):p(p){}~S(){*p=42;}int*p;};"
     "int cpp_test_entry(){int n=0;{const S s{&n};}return n;}"),
    ("for-continue-retains-init-lifetime", TRACE_CLASS +
     "int cpp_test_entry(){int trace=0;for(T init{&trace,9};trace<11;){T body{&trace,1};continue;}"
     "if(trace!=119)return 1;return 42;}"),
    ("for-break-cleans-body-before-init", TRACE_CLASS +
     "int cpp_test_entry(){int trace=0;for(T init{&trace,9};true;){T body{&trace,2};break;}"
     "if(trace!=29)return 1;return 42;}"),
    ("return-value-before-reverse-cleanup", TRACE_CLASS +
     "int work(int*p){T first{p,1};T second{p,2};return 42;}"
     "int cpp_test_entry(){int trace=0;int result=work(&trace);if(trace!=21)return 1;return result;}"),
    ("class-template-concrete-c1", "template<class T>struct Box{Box(T n):value(n){}T value;};"
     "int cpp_test_entry(){Box<int> box{42};return box.value;}"),
)

OUTLINE_CLASS = "struct S{int value;S(int);};"
PROGRAMS = tuple((name, ((name + ".cpp", source),)) for name, source in SINGLE_PROGRAMS) + (
    ("separate-definition-direct-list-call", (
        ("definition.cpp", OUTLINE_CLASS + "S::S(int n):value(n){}"),
        ("entry.cpp", OUTLINE_CLASS + "int cpp_test_entry(){S s{42};return s.value;}"),
    )),
)


INVALID = (
    ("floating-to-integral", "struct S{S(int);};int f(){S s{1.0};return 0;}", "cpp.narrowing-conversion"),
    ("runtime-int-to-char", "struct S{S(signed char);};int f(){int n=42;S s{n};return 0;}", "cpp.narrowing-conversion"),
    ("constant-out-of-range", "struct S{S(signed char);};int f(){S s{128};return 0;}", "cpp.narrowing-conversion"),
    ("negative-to-unsigned", "struct S{S(unsigned char);};int f(){S s{-1};return 0;}", "cpp.narrowing-conversion"),
    ("best-overload-narrows-no-fallback", "struct S{S(int,unsigned char);S(long,long);};"
     "int f(){S s{1,256};return 0;}", "cpp.narrowing-conversion"),
    ("ambiguity-before-narrowing", "struct S{S(unsigned char);S(long);};int f(){S s{256};return 0;}", "cpp.ambiguous-overload"),
    ("private-best-no-fallback", "class S{S(int);public:S(long);};int f(){S s{42};return 0;}", "cpp.member-access"),
    ("char-zero-not-null-pointer", "struct S{S(int*);};int f(){S s{'\\0'};return 0;}", "cpp.no-viable-overload"),
    ("nullptr-not-implicit-bool", "struct S{S(bool);};int f(){S s{nullptr};return 0;}", "cpp.no-viable-overload"),
    ("extra-argument", "struct S{S(int);};int f(){S s{1,2};return 0;}", "cpp.no-viable-overload"),
    ("missing-argument", "struct S{S(int);};int f(){S s{};return 0;}", "cpp.no-viable-overload"),
    ("pointer-to-bool-narrows", "struct S{S(bool);};int f(){int n=1;S s{&n};return 0;}", "cpp.narrowing-conversion"),
    ("const-discarding-reference", "struct S{S(int&);};int f(){const int n=42;S s{n};return 0;}", "cpp.no-viable-overload"),
    ("mutable-reference-to-prvalue", "struct S{S(int&);};int f(){S s{42};return 0;}", "cpp.no-viable-overload"),
    ("switch-enters-initialized-lifetime", "struct S{S(){}~S(){}};"
     "int f(){switch(1){case 1:S s{};case 2:return 0;}return 0;}", "cpp.switch-jump-initialization"),
)

DEFERRED = (
    ("nested-braced-argument", "struct S{S(int);};int f(){S s{{42}};return 0;}",
     "cpp.unsupported.constructor-list-initialization"),
    ("same-type-copy-list-element", "struct S{S(int);S(const S&);};int f(){S a(42);S b{a};return 0;}",
     "cpp.unsupported.class-initialization"),
    ("class-reference-candidate-set", "struct Values{};struct S{S(int);S(const Values&);};"
     "int f(){S s{42};return 0;}", "cpp.unsupported.constructor-list-initialization"),
    ("reference-temporary", "struct S{S(const int&);};int f(){S s{42};return 0;}",
     "cpp.unsupported.reference-temporaries"),
    ("floating-constant-proof", "struct S{S(float);};int f(){S s{2.0};return 0;}",
     "cpp.unsupported.narrowing-analysis"),
)


@pytest.mark.parametrize("name,units", PROGRAMS, ids=[row[0] for row in PROGRAMS])
def test_direct_list_constructor_originals_compile_deterministically(owned_only, name, units):
    assert compile_host_cpp(units) == compile_host_cpp(units)


def test_selected_arguments_are_sequenced_value_snapshots_before_c1():
    tree = CppParser().parse(PAIR_CLASS + "int f(){int n=20;S s{n,n=22};return s.value;}")
    items = tree.ext[-1].body.block_items
    assert isinstance(items[1], c_ast.Decl)  # Object storage precedes its initializer.
    assert isinstance(items[2], c_ast.StmtExpr)
    first, second, call = items[2].stmt.block_items
    assert isinstance(first, c_ast.Decl) and isinstance(first.init, c_ast.ID)
    assert isinstance(second, c_ast.Decl) and isinstance(second.init, c_ast.Assignment)
    assert isinstance(call, c_ast.FuncCall) and call.name.name == "_ZN1SC1Eii"
    assert [arg.name for arg in call.args.exprs[1:]] == [first.name, second.name]
    assert isinstance(call.args.exprs[0], c_ast.UnaryOp) and call.args.exprs[0].op == "&"


def test_empty_list_does_not_zero_omitted_fields_of_user_provided_constructor():
    tree = CppParser().parse("struct S{S(){}int value;};int f(){S s{};return 0;}")
    constructor = next(node for node in tree.ext if isinstance(node, c_ast.FuncDef)
                       and node.decl.name == "_ZN1SC1Ev")
    assert constructor.body.block_items == []
    assert tree.ext[-1].body.block_items[0].init is None


@pytest.mark.parametrize("name,source,code", INVALID, ids=[row[0] for row in INVALID])
def test_invalid_direct_list_constructor_stops_before_emission(name, source, code, monkeypatch):
    from pcc.backend import owned_object_emit

    def forbidden(*args, **kwargs):
        raise AssertionError("invalid direct-list construction reached object emission")

    monkeypatch.setattr(owned_object_emit, "emit_owned_object", forbidden)
    with pytest.raises(CppDiagnostic) as error:
        compile_host_cpp(((name + ".cpp", source),))
    assert error.value.code == code


@pytest.mark.parametrize("name,source,code", DEFERRED, ids=[row[0] for row in DEFERRED])
def test_deferred_direct_list_constructor_domains_remain_explicit(name, source, code):
    with pytest.raises(CppDiagnostic) as error:
        CppParser().parse(source)
    assert error.value.code == code
