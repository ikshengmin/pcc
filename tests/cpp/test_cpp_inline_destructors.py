"""Completed-class inline destructors; authored C++17 CI obligations, UNRUN.

N4659 [class.dtor], [class.mem], [basic.def.odr], [stmt.jump]/2 and
[stmt.return]/3: https://timsong-cpp.github.io/cppwp/n4659/class.dtor
https://timsong-cpp.github.io/cppwp/n4659/stmt.jump

The exact three-line DepthTracker destructor is from TinyXML2 11.0.0,
tinyxml2.h:1974-1976, commit 9148bdf719e997d1f474be6bcc7943881046dba1,
by Lee Thomason under Zlib (projects/tinyxml2-11.0.0/LICENSE.txt and
pcc-fixture.json). Surrounding declarations/observer are PCC-authored, not an
unchanged whole-project or nested-class acceptance claim.
"""

import hashlib

import pytest

from pcc.frontends.c.ast import c_ast
from pcc.frontends.cpp import CppDiagnostic, CppParser
from tests.cpp.owned_execution import compile_host_cpp
from tests.cpp.test_cpp_owned_execution import owned_only


DEPTH_DESTRUCTOR = "\t\t~DepthTracker() {\r\n\t\t\t_document->PopDepth();\r\n\t\t}\r\n"
DEPTH_DESTRUCTOR_SHA256 = "abbbe4fd6fb682136072f15e5fe83260a0a85885c2753b928b6ef7e81c39d5c4"
DEPTH_PROGRAM = (
    "struct XMLDocument{int depth;void PopDepth(){depth-=1;}};"
    "class DepthTracker{public:DepthTracker(XMLDocument*p):_document(p){}" +
    DEPTH_DESTRUCTOR + "private:XMLDocument*_document;};"
    "int cpp_test_entry(){XMLDocument document{43};{DepthTracker guard(&document);}return document.depth;}"
)

TRACE_CLASS = ("struct T{T(int*p,int digit):p(p),digit(digit){}"
               "~T(){*p=*p*10+digit;}private:int*p;int digit;};")


def traced(body, expected):
    return TRACE_CLASS + "int cpp_test_entry(){int trace=0;" + body + (
        "if(trace!=" + str(expected) + ")return 1;return 42;}")


SINGLE_PROGRAMS = (
    ("original-empty-inline-destructor", "struct S{~S(){}};int cpp_test_entry(){S s;return 42;}"),
    ("exact-upstream-depth-destructor", DEPTH_PROGRAM),
    ("reverse-declaration-order", traced("{T first(&trace,1);T second(&trace,2);}", 21)),
    ("nested-scope-order", traced("{T first(&trace,1);{T second(&trace,2);}}", 21)),
    ("false-branch-never-constructed", traced("if(false){T guard(&trace,1);}", 0)),
    ("for-continue-retains-init", traced("for(T init(&trace,9);trace<11;){T body(&trace,1);continue;}", 119)),
    ("for-break-cleans-body-before-init", traced("for(T init(&trace,9);true;){T body(&trace,2);break;}", 29)),
    ("switch-break-retains-outer", traced("{T outer(&trace,1);switch(1){case 1:{T inner(&trace,2);break;}}}", 21)),
    ("return-value-before-cleanup", TRACE_CLASS +
     "int work(int*p){T guard(p,2);*p=1;return *p;}"
     "int cpp_test_entry(){int trace=0;int result=work(&trace);if(result!=1||trace!=12)return 1;return 42;}"),
    ("floating-return-before-cleanup", TRACE_CLASS +
     "double work(int*p){T guard(p,1);return 42.0;}"
     "int cpp_test_entry(){int trace=0;double result=work(&trace);if(trace!=1)return 1;return int(result);}"),
    ("destructor-return-cleans-own-locals", "struct L{int*p;~L(){*p=*p*10+2;}};"
     "struct S{~S(){L local{p};*p=*p*10+1;return;}int*p;};"
     "int cpp_test_entry(){int trace=0;{S s{&trace};}if(trace!=12)return 1;return 42;}"),
    ("later-private-method-and-field", "struct S{~S(){store();}int*p;private:void store(){*p=42;}};"
     "int cpp_test_entry(){int value=0;{S s;s.p=&value;}return value;}"),
    ("const-object-destruction", "struct S{S(int*p):p(p),value(1){}~S(){value=42;*p=value;}int*p;int value;};"
     "int cpp_test_entry(){int value=0;{const S s(&value);}return value;}"),
    ("private-destructor-inside-owner", "class S{~S(){*p=42;}int*p;public:static void work(int*p){S s;s.p=p;}};"
     "int cpp_test_entry(){int value=0;S::work(&value);return value;}"),
    ("inline-namespace-destructor-owner", "namespace A{inline namespace V{struct S{int*p;~S(){*p=42;}};}}"
     "int cpp_test_entry(){int value=0;{A::S s{&value};}return value;}"),
)

SHARED_CLASS = "struct S{S(int*p,int n):p(p),n(n){}~S(){*p+=n;}int*p;int n;};"
PROGRAMS = tuple((name, ((name + ".cpp", source),)) for name, source in SINGLE_PROGRAMS) + (
    # Existing fixed-target direct ELF link only: this is not qualification of
    # merged stack-map tables, platform multi-TU linking, or native pcc1.
    # Retain ordinary weak-ODR validation; do not bypass duplicate-ID guards.
    ("two-tu-inline-destructor-odr", (
        ("helper.cpp", SHARED_CLASS + "void helper(int*p){S s(p,17);}"),
        ("entry.cpp", SHARED_CLASS + "void helper(int*);int cpp_test_entry(){int value=0;"
         "helper(&value);{S s(&value,25);}return value;}"),
    )),
)


INVALID = (
    ("duplicate-body", "struct S{~S(){}~S(){}};", "cpp.redeclaration"),
    ("body-then-declaration", "struct S{~S(){}~S();};", "cpp.redeclaration"),
    ("outside-redefinition", "struct S{~S(){}};S::~S(){}", "cpp.redefinition"),
    ("wrong-name", "struct S{~Wrong(){}};", "cpp.invalid-destructor"),
    ("parameter", "struct S{~S(int){}};", "cpp.destructor-parameters"),
    ("cv-qualification", "struct S{~S()const{}};", "cpp.destructor-cv"),
    ("value-return", "struct S{~S(){return 1;}};", "cpp.destructor-return-value"),
    ("void-expression-return", "void f();struct S{~S(){return f();}};", "cpp.destructor-return-value"),
    ("private-destruction", "class S{~S(){}};int f(){S s;return 0;}", "cpp.member-access"),
    ("switch-enters-lifetime", "struct S{~S(){}};int f(){switch(1){case 1:S s;case 2:return 0;}return 0;}",
     "cpp.switch-jump-initialization"),
)


DEFERRED = (
    "struct S{virtual ~S(){}};",
    "struct S{~S()=delete;};",
    "struct S{~S()noexcept{}};",
    "struct S{~S(){throw 1;}};",
    "struct T{~T(){}};struct S{T value;};int f(){S s;return 0;}",
    "struct S{~S(){}};int f(){S array[2];return 0;}",
    "namespace{struct S{~S(){}};}",
)


@pytest.mark.parametrize("name,units", PROGRAMS, ids=[row[0] for row in PROGRAMS])
def test_inline_destructor_originals_compile_deterministically(owned_only, name, units):
    assert compile_host_cpp(units) == compile_host_cpp(units)


def test_inline_destructor_keeps_original_tokens_and_completed_class_context():
    parser = CppParser()
    parser.parse("struct S{~S(){*p=value;}private:int*p;int value;};", "original.cpp")
    definition = parser.scope.names["S"].dtype.definition
    pending, = definition.inline_methods
    assert pending.symbol is definition.destructor and pending.symbol.defined
    assert [token.value for token in pending.tokens] == ["{", "*", "p", "=", "value", ";", "}"]
    assert all(any(token is original for original in parser.cursor.tokens) for token in pending.tokens)
    assert all(token.coord.file == "original.cpp" for token in pending.tokens)
    assert pending.parameter_scope.parent is definition.scope
    assert pending.symbol.closed_normal_body
    assert parser.current_function is parser.member_function is parser.this_symbol is None


def test_d1_prototype_precedes_all_odr_bodies_without_static_internalization():
    parser = CppParser()
    tree = parser.parse("struct S{~S(){store();}void store(){*p=42;}int*p;};")
    definition = parser.scope.names["S"].dtype.definition
    prototypes = [node for node in definition.method_declarations if isinstance(node, c_ast.Decl)]
    bodies = [node for node in definition.method_declarations if isinstance(node, c_ast.FuncDef)]
    assert len(prototypes) == len(bodies) == 2
    assert max(tree.ext.index(node) for node in prototypes) < min(tree.ext.index(node) for node in bodies)
    assert all(node.definition_linkage == "external" for node in prototypes)
    assert all(node.decl.definition_linkage == "external_odr" and not node.decl.storage for node in bodies)
    assert prototypes[0].name == bodies[0].decl.name == "_ZN1SD1Ev"
    assert not definition.destructor.closed_normal_body  # A call is not a noexcept proof.


def test_upstream_depth_destructor_bytes_are_preserved():
    assert hashlib.sha256(DEPTH_DESTRUCTOR.encode()).hexdigest() == DEPTH_DESTRUCTOR_SHA256
    parser = CppParser()
    parser.parse(DEPTH_PROGRAM, "depth-destructor.cpp")
    definition = parser.scope.names["DepthTracker"].dtype.definition
    assert definition.destructor.defined
    assert definition.inline_methods[0].symbol is definition.destructor


@pytest.mark.parametrize("name,source,code", INVALID, ids=[row[0] for row in INVALID])
def test_invalid_inline_destructor_stops_before_emission(name, source, code, monkeypatch):
    from pcc.backend import owned_object_emit

    def forbidden(*args, **kwargs):
        raise AssertionError("invalid destructor reached object emission")

    monkeypatch.setattr(owned_object_emit, "emit_owned_object", forbidden)
    with pytest.raises(CppDiagnostic) as error:
        compile_host_cpp(((name + ".cpp", source),))
    assert error.value.code == code


@pytest.mark.parametrize("source", DEFERRED)
def test_deferred_inline_destructor_lifetimes_remain_explicit(source):
    with pytest.raises(CppDiagnostic) as error:
        CppParser().parse(source)
    assert error.value.code.startswith("cpp.unsupported.")
