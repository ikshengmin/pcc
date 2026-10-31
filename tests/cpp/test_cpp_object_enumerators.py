"""Object-enumerator expression sequencing over the existing shared C AST.

N4659 [expr.ref] and [dcl.enum] require evaluation of the object expression;
its enum result is a prvalue, not a load from an imaginary member field.
https://www.open-std.org/jtc1/sc22/wg21/docs/papers/2017/n4659.pdf
Original PCC cases; explicit external G++ is only a C++17 reference.
"""

import re

import pytest

from pcc.frontends.c.ast import c_ast
from pcc.frontends.cpp import CppDiagnostic, CppParser
from tests.cpp.owned_execution import compile_host_cpp
from tests.cpp.test_cpp_class_execution import execute_and_record


ORIGINAL_OBJECT_ENUM_SOURCES = (
    "struct S{enum E{A};};int f(){S s;return s.A;}",
    "struct S{enum E{A};};S*get();int f(){return get()->A;}",
)

OBJECT_ENUM_CASES = (
    ("original-dot-source", ORIGINAL_OBJECT_ENUM_SOURCES[0] +
     "int cpp_test_entry(){return f()+42;}", 42),
    ("const-object-enumerator-is-not-a-const-data-member",
     "struct S{enum E{A=42};};int cpp_test_entry(){const S s{};return s.A;}", 42),
    ("arrow-base-call-executes-once",
     "struct S{enum E{A=40};};S*get(int*n,S*p){*n=*n+1;return p;}"
     "int cpp_test_entry(){int n=0;S s;int value=get(&n,&s)->A;return value+n+1;}", 42),
    ("dot-indirection-base-call-executes-once",
     "struct S{enum E{A=40};};S*get(int*n,S*p){*n=*n+1;return p;}"
     "int cpp_test_entry(){int n=0;S s;int value=(*get(&n,&s)).A;return value+n+1;}", 42),
    ("member-subobject-base-call-executes-once",
     "struct Inner{enum E{A=40};};struct Outer{Inner inner;};"
     "Outer*get(int*n,Outer*p){*n=*n+1;return p;}"
     "int cpp_test_entry(){int n=0;Outer o;int value=get(&n,&o)->inner.A;return value+n+1;}", 42),
    ("aggregate-clause-bases-are-once-and-ordered",
     "struct S{enum E{A=17,B=25};};struct R{int a;int b;};"
     "S*get(int*log,S*p,int digit){*log=*log*10+digit;return p;}"
     "int cpp_test_entry(){int log=0;S s;R r{get(&log,&s,1)->A,get(&log,&s,2)->B};"
     "if(log!=12)return -1;return r.a+r.b;}", 42),
    ("object-enumerator-retains-adl-nominal-type",
     "namespace n{struct S{enum E{A=42};};int take(S::E e){return e;}}"
     "int take(int x){return 1;}int cpp_test_entry(){n::S s;return take(s.A);}", 42),
    ("object-enumerator-constant-range-conversion",
     "struct S{enum E{A=42};};struct R{unsigned char value;};"
     "int cpp_test_entry(){S s;R r{s.A};return r.value;}", 42),
    ("short-circuit-excludes-object-base-effect-and-fact",
     "struct S{enum E{A=42};};struct R{unsigned char value;};"
     "S*get(int*n,S*p){*n=*n+1;return p;}"
     "int cpp_test_entry(){int n=0;S s;const int value=false&&get(&n,&s)->A;"
     "R r{value};if(n!=0)return -1;return r.value+42;}", 42),
)

INVALID_OBJECT_ENUM_CASES = (
    ("enumerator-is-not-an-assignment-lvalue",
     "struct S{enum E{A};};int f(){S s;s.A=S::A;return 0;}", "cpp.not-lvalue"),
    ("enumerator-has-no-object-address",
     "struct S{enum E{A};};int f(){S s;S::E*p=&s.A;return 0;}", "cpp.not-lvalue"),
    ("zero-enumerator-is-not-null-pointer-literal",
     "struct S{enum E{A};};int f(){S s;int*p=s.A;return 0;}", "cpp.invalid-conversion"),
    ("private-object-enumerator-access",
     "class S{enum E{A};};int f(){S s;return s.A;}", "cpp.member-access"),
    ("evaluated-nonconstexpr-base-is-not-a-constant-proof",
     "struct S{enum E{A=42};};struct R{unsigned char value;};"
     "S*get(int*n,S*p){*n=*n+1;return p;}"
     "int f(){int n=0;S s;const int value=get(&n,&s)->A;R r{value};return 0;}",
     "cpp.narrowing-conversion"),
)


@pytest.mark.parametrize("source", ORIGINAL_OBJECT_ENUM_SOURCES)
def test_original_object_enumerator_sources_are_preserved_verbatim(source):
    CppParser().parse(source, "original-object-enum.cpp")


@pytest.mark.parametrize("name,source,value", OBJECT_ENUM_CASES, ids=[x[0] for x in OBJECT_ENUM_CASES])
def test_object_enumerator_owned_compilation_is_deterministic(name, source, value):
    units = ((name + ".cpp", source),)
    assert compile_host_cpp(units) == compile_host_cpp(units)


@pytest.mark.parametrize("name,source,code", INVALID_OBJECT_ENUM_CASES,
                         ids=[x[0] for x in INVALID_OBJECT_ENUM_CASES])
def test_object_enumerator_diagnostics_precede_emission(name, source, code, monkeypatch):
    from pcc.backend import owned_object_emit
    def forbidden(*args, **kwargs):
        raise AssertionError("invalid object enum expression reached emission")
    monkeypatch.setattr(owned_object_emit, "emit_owned_object", forbidden)
    with pytest.raises(CppDiagnostic) as error:
        compile_host_cpp(((name + ".cpp", source),))
    assert error.value.code == code


def test_discarded_class_object_constant_analysis_is_not_invented():
    source = ("struct S{enum E{A=42};};struct R{unsigned char value;};"
              "int f(){S s;const int value=s.A;R r{value};return 0;}")
    with pytest.raises(CppDiagnostic) as error:
        CppParser().parse(source, "object-constant.cpp")
    assert error.value.code == "cpp.unsupported.narrowing-analysis"


def test_object_enumerator_ast_sequences_one_base_call_without_a_fake_field():
    parser = CppParser()
    ast = parser.parse("struct S{enum E{A=42};};S*get();int f(){return get()->A;}")
    returns = []
    class Visitor(c_ast.NodeVisitor):
        def visit_Return(self, node):
            returns.append(node.expr)
    Visitor().visit(ast)
    assert len(returns) == 1
    nodes = []
    class Nodes(c_ast.NodeVisitor):
        def generic_visit(self, node):
            nodes.append(node)
            super().generic_visit(node)
    Nodes().visit(returns[0])
    assert sum(isinstance(node, c_ast.FuncCall) for node in nodes) == 1
    assert any(isinstance(node, c_ast.ExprList) for node in nodes)
    assert not any(isinstance(node, c_ast.StructRef) for node in nodes)


def test_public_translation_unit_pipeline_keeps_the_effectful_base_call():
    from tests.cpp.test_cpp_public_driver import compile_cpp
    source = OBJECT_ENUM_CASES[2][1]
    first = compile_cpp(source, "public-object-enumerator.cpp", use_system_cpp=False)
    second = compile_cpp(source, "public-object-enumerator.cpp", use_system_cpp=False)
    assert first[0][1] == second[0][1]
    # This uses owned preprocessing and the actual high/low C pass pipeline,
    # not merely the direct-codegen execution adapter. Count calls, not defs.
    assert len(re.findall(r"\bcall\b[^\n]*@_Z3getPiP1S\(", first[0][1])) == 1


@pytest.mark.parametrize("source", (
    "struct S{enum E{A=42};};int f(){S s;return s.A;}",
    "struct Inner{enum E{A=42};};struct Outer{Inner inner;};"
    "Outer*get(Outer*p);int f(Outer*p){return get(p)->inner.A;}",
))
def test_discarded_class_glvalues_do_not_emit_aggregate_loads(source):
    from tests.cpp.test_cpp_public_driver import compile_cpp
    ir = compile_cpp(source, "discarded-glvalue.cpp", use_system_cpp=False)[0][1]
    # Named aggregate loads have a comma immediately after the type; a load
    # of a pointer to that type has '*' and is a different, necessary operation.
    assert not re.search(r'\bload\s+(?:%"[^"]+"|%[\w.$]+)\s*,', ir)
    if "get(" in source:
        assert len(re.findall(r"\bcall\b[^\n]*@_Z3getP5Outer\(", ir)) == 1


@pytest.mark.integration
@pytest.mark.parametrize("name,source,value", OBJECT_ENUM_CASES, ids=[x[0] for x in OBJECT_ENUM_CASES])
def test_object_enumerator_owned_execution_matches_explicit_cpp17_reference(name, source, value, tmp_path, monkeypatch):
    execute_and_record(tmp_path, monkeypatch, ((name + ".cpp", source),), value)
