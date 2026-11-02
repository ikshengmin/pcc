"""Explicit/converting C1 participation: C++17 obligations, locally UNRUN.

N4659 [dcl.fct.spec]/3, [class.conv.ctor], [over.match.copy], [over.match.list],
[dcl.init.list]/3.4,3.6,4,7 and [dcl.init]/17:
https://timsong-cpp.github.io/cppwp/n4659/class.conv.ctor
https://timsong-cpp.github.io/cppwp/n4659/over.match.copy
https://timsong-cpp.github.io/cppwp/n4659/over.match.list

The exact four-line explicit DepthTracker constructor is from TinyXML2 11.0.0,
tinyxml2.h:1970-1973, revision 9148bdf719e997d1f474be6bcc7943881046dba1,
by Lee Thomason under Zlib; see projects/tinyxml2-11.0.0/LICENSE.txt and its
pcc-fixture.json. Surrounding declarations/observer are PCC-authored. This is
an excerpt regression, not unchanged whole-project/nested-class acceptance.
Execution uses the existing direct ELF host-owned adapter, not native pcc1.
"""

import hashlib

import pytest

from pcc.frontends.c.ast import c_ast
from pcc.frontends.cpp import CppDiagnostic, CppParser
from tests.cpp.owned_execution import compile_host_cpp
from tests.cpp.test_cpp_inline_destructors import DEPTH_DESTRUCTOR
from tests.cpp.test_cpp_owned_execution import owned_only


ORIGINAL_COPY_LIST = "struct S{S(int);};int f(){S s={42};return 0;}"
ORIGINAL_COPY_CONVERTING = "struct S{S(int);};int f(){S s=42;return 0;}"
ORIGINAL_SUPPORT = "struct S{S(int);};S::S(int){}int f();int cpp_test_entry(){return f()+42;}"
DEPTH_CONSTRUCTOR = ("\t\texplicit DepthTracker(XMLDocument * document) {\r\n"
                     "\t\t\tthis->_document = document;\r\n"
                     "\t\t\tdocument->PushDepth();\r\n\t\t}\r\n")
DEPTH_CONSTRUCTOR_SHA256 = "be84d06d63d05d5a651c8d9e1b4f092c98bc2c162e6d4542887db49eafa5905c"
DEPTH_PROGRAM = (
    "struct XMLDocument{int depth;void PushDepth(){depth+=1;}void PopDepth(){depth-=1;}};"
    "class DepthTracker{public:" + DEPTH_CONSTRUCTOR + DEPTH_DESTRUCTOR +
    "private:XMLDocument*_document;};"
    "int cpp_test_entry(){XMLDocument document{41};int result=0;"
    "{DepthTracker guard(&document);result=document.depth;}"
    "if(document.depth!=41)return 1;return result;}"
)

SINGLE_PROGRAMS = (
    ("exact-depth-constructor-and-destructor", DEPTH_PROGRAM),
    ("explicit-default-initialization", "struct S{explicit S():value(42){}int value;};"
     "int cpp_test_entry(){S s;return s.value;}"),
    ("explicit-default-direct-list", "struct S{explicit S():value(42){}int value;};"
     "int cpp_test_entry(){S s{};return s.value;}"),
    ("explicit-parenthesized", "struct S{explicit S(int n):value(n){}int value;};"
     "int cpp_test_entry(){S s(42);return s.value;}"),
    ("explicit-direct-list", "struct S{explicit S(int n):value(n){}int value;};"
     "int cpp_test_entry(){S s{42};return s.value;}"),
    ("explicit-multiple-parameters", "struct S{explicit S(int a,int b):value(a+b){}int value;};"
     "int cpp_test_entry(){S s{20,22};return s.value;}"),
    ("copy-filter-does-not-mutate-direct-overloads", "struct S{explicit S(int):value(1){}S(long):value(42){}int value;};"
     "int cpp_test_entry(){S copy=7;S direct{7};if(direct.value!=1)return 1;return copy.value;}"),
    ("private-explicit-not-copy-candidate", "class S{explicit S(int):value(1){}public:S(long):value(42){}int value;};"
     "int cpp_test_entry(){S s=7;return s.value;}"),
    ("copy-list-nonexplicit-best", "struct S{S(int):value(42){}explicit S(long):value(1){}int value;};"
     "int cpp_test_entry(){S s={7};return s.value;}"),
    ("copy-list-empty-nonexplicit-default", "struct S{S():value(42){}int value;};"
     "int cpp_test_entry(){S s={};return s.value;}"),
    ("copy-list-clauses-snapshot-in-order", "struct S{S(int a,int b):value(a+b){}int value;};"
     "int cpp_test_entry(){int n=20;S s={n,n=22};if(n!=22)return 1;return s.value;}"),
    ("scalar-copy-allows-ordinary-narrowing", "struct S{S(int n):value(n){}int value;};"
     "int cpp_test_entry(){S s=42.75;return s.value;}"),
    ("scalar-copy-binds-reference-parameter", "struct S{S(int&r){r=42;value=r;}int value;};"
     "int cpp_test_entry(){int n=1;S s=n;if(n!=42)return 1;return s.value;}"),
    ("scalar-copy-pointer-to-bool", "struct S{S(bool b){value=1;if(b)value=42;}int value;};"
     "int cpp_test_entry(){int n=1;S s=&n;return s.value;}"),
    ("explicit-out-of-line-definition", "struct S{explicit S(int);int value;};S::S(int n):value(n){}"
     "int cpp_test_entry(){S s{42};return s.value;}"),
    ("const-copy-destination-cleanup", "struct S{S(int*p):p(p){}~S(){*p=42;}int*p;};"
     "int cpp_test_entry(){int n=0;{const S s=&n;}return n;}"),
    ("copy-construction-and-cleanup-once", "int live=0;struct S{S(int){live+=1;}~S(){live-=1;}};"
     "int cpp_test_entry(){{S a=42;if(live!=1)return 1;{S b={42};if(live!=2)return 1;}"
     "if(live!=1)return 1;}if(live!=0)return 1;return 42;}"),
    ("private-explicit-inside-owner", "class S{explicit S(int n):value(n){}int value;"
     "public:static int work(){S s{42};return s.value;}};int cpp_test_entry(){return S::work();}"),
    ("namespace-alias-explicit-owner", "namespace A{struct S{explicit S(int n):value(n){}int value;};}"
     "using Alias=A::S;int cpp_test_entry(){Alias s{42};return s.value;}"),
    ("class-template-nonexplicit-copy", "template<class T>struct Box{Box(T n):value(n){}T value;};"
     "int cpp_test_entry(){Box<int> a=42;Box<int> b={0};return a.value+b.value;}"),
)

PROGRAMS = tuple((name, ((name + ".cpp", source),)) for name, source in SINGLE_PROGRAMS) + (
    ("original-copy-list", (("original.cpp", ORIGINAL_COPY_LIST), ("support.cpp", ORIGINAL_SUPPORT))),
    ("original-copy-converting", (("original.cpp", ORIGINAL_COPY_CONVERTING), ("support.cpp", ORIGINAL_SUPPORT))),
)

INVALID = (
    ("explicit-only-scalar-copy", "struct S{explicit S(int);};int f(){S s=42;return 0;}", "cpp.no-viable-overload"),
    ("explicit-only-copy-list", "struct S{explicit S(int);};int f(){S s={42};return 0;}", "cpp.explicit-copy-list"),
    ("explicit-copy-list-before-reference-temporary", "struct S{explicit S(const int&);};"
     "int f(){S s={42};return 0;}", "cpp.explicit-copy-list"),
    ("explicit-default-copy-list", "struct S{explicit S();};int f(){S s={};return 0;}", "cpp.explicit-copy-list"),
    ("explicit-copy-list-best-no-fallback", "struct S{explicit S(int);S(long);};int f(){S s={42};return 0;}", "cpp.explicit-copy-list"),
    ("copy-filter-leaves-ambiguity", "struct S{explicit S(int);S(long);S(unsigned long);};"
     "int f(){S s=1;return 0;}", "cpp.ambiguous-overload"),
    ("copy-list-ambiguity-before-explicit", "struct S{explicit S(long);S(unsigned long);};"
     "int f(){S s={1};return 0;}", "cpp.ambiguous-overload"),
    ("copy-list-narrowing-before-explicit", "struct S{explicit S(int,unsigned char);S(long,long);};"
     "int f(){S s={1,256};return 0;}", "cpp.narrowing-conversion"),
    ("private-explicit-copy-list-best", "class S{explicit S(int);public:S(long);};"
     "int f(){S s={42};return 0;}", "cpp.member-access"),
    ("copy-list-nonexplicit-narrowing", "struct S{S(int);};int f(){S s={1.0};return 0;}", "cpp.narrowing-conversion"),
    ("scalar-copy-nullptr-not-bool", "struct S{S(bool);};int f(){S s=nullptr;return 0;}", "cpp.no-viable-overload"),
    ("explicit-field", "struct S{explicit int n;};", "cpp.invalid-explicit"),
    ("explicit-ordinary-method", "struct S{explicit int f();};", "cpp.invalid-explicit"),
    ("explicit-destructor", "struct S{explicit ~S();};", "cpp.invalid-explicit"),
    ("explicit-duplicate-constructor", "struct S{explicit S(int);S(int);};", "cpp.redeclaration"),
    ("explicit-cv-constructor", "struct S{explicit S()const;};", "cpp.constructor-cv"),
    ("explicit-constructor-return-value", "struct S{explicit S(){return 1;}};", "cpp.constructor-return-value"),
    ("explicit-wrong-constructor-name", "struct S{explicit Wrong();};", "cpp.invalid-explicit"),
    ("copy-list-pointer-narrowing-before-explicit", "struct S{explicit S(bool);};"
     "int f(){int n=1;S s={&n};return 0;}", "cpp.narrowing-conversion"),
)

DEFERRED = (
    ("class-copy-expression", "struct S{S(int);};int f(){S a=42;S b=a;return 0;}", "cpp.unsupported.class-initialization"),
    ("class-copy-list-element", "struct S{S(int);};int f(){S a=42;S b={a};return 0;}", "cpp.unsupported.class-initialization"),
    ("copy-reference-temporary", "struct S{S(const int&);};int f(){S s=42;return 0;}", "cpp.unsupported.reference-temporaries"),
    ("class-reference-copy-candidates", "struct Values{};struct S{S(int);S(const Values&);};"
     "int f(){S s=42;return 0;}", "cpp.unsupported.constructor-list-initialization"),
    ("explicit-conversion-function", "struct S{explicit operator bool()const{return true;}};", "cpp.unsupported.classes"),
    ("combined-explicit-constexpr", "struct S{explicit constexpr S(int n):value(n){}int value;};", "cpp.unsupported.classes"),
)


@pytest.mark.parametrize("name,units", PROGRAMS, ids=[row[0] for row in PROGRAMS])
def test_explicit_constructor_originals_compile_deterministically(owned_only, name, units):
    assert compile_host_cpp(units) == compile_host_cpp(units)


@pytest.mark.parametrize("source", (ORIGINAL_COPY_LIST, ORIGINAL_COPY_CONVERTING))
def test_exact_formerly_rejected_copy_initialization_original_is_positive(source):
    assert CppParser().parse(source).ext[-1].decl.name == "_Z1fv"


def test_explicit_declaration_identity_survives_definition_and_filtered_calls():
    parser = CppParser()
    tree = parser.parse("struct S{explicit S(int);S(long);};S::S(int){}S::S(long){}"
                        "int f(){S copy=1;S direct{1};return 0;}")
    constructors = parser.scope.names["S"].dtype.definition.constructors.overloads
    assert [item.constructor_explicit for item in constructors] == [True, False]
    assert all(item.defined for item in constructors)
    assert [item.c_name for item in constructors] == ["_ZN1SC1Ei", "_ZN1SC1El"]
    items = tree.ext[-1].body.block_items
    assert items[1].name.name == constructors[1].c_name
    assert isinstance(items[3], c_ast.StmtExpr)
    assert items[3].stmt.block_items[-1].name.name == constructors[0].c_name


def test_upstream_explicit_constructor_bytes_and_semantic_owner_are_preserved():
    assert hashlib.sha256(DEPTH_CONSTRUCTOR.encode()).hexdigest() == DEPTH_CONSTRUCTOR_SHA256
    parser = CppParser()
    parser.parse(DEPTH_PROGRAM)
    definition = parser.scope.names["DepthTracker"].dtype.definition
    constructor, = definition.constructors.overloads
    assert constructor.constructor_explicit and constructor.defined
    assert definition.inline_constructors[0].symbol is constructor


@pytest.mark.parametrize("name,source,code", INVALID, ids=[row[0] for row in INVALID])
def test_invalid_explicit_constructor_stops_before_emission(name, source, code, monkeypatch):
    from pcc.backend import owned_object_emit

    def forbidden(*args, **kwargs):
        raise AssertionError("invalid explicit/copy initialization reached object emission")

    monkeypatch.setattr(owned_object_emit, "emit_owned_object", forbidden)
    with pytest.raises(CppDiagnostic) as error:
        compile_host_cpp(((name + ".cpp", source),))
    assert error.value.code == code


@pytest.mark.parametrize("name,source,code", DEFERRED, ids=[row[0] for row in DEFERRED])
def test_explicit_constructor_unowned_domains_remain_explicit(name, source, code):
    with pytest.raises(CppDiagnostic) as error:
        CppParser().parse(source)
    assert error.value.code == code
