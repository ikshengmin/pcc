"""Explicit class qualification calls a body; it does not admit qualified data."""
import shutil
import subprocess

import pytest
from pcc.frontends.cpp import CppParser, CppDiagnostic
from pcc.frontends.c.ast import c_ast

TARGET = 'x86_64-unknown-linux-gnu'
POSITIVE = (
    'struct B{virtual int f(){return 42;}};struct D:B{int f()override{return 7;}};int g(D*p){return p->B::f();}',
    'struct B{int f(int n=42){return n;}};struct D:B{};int g(D*p){return p->B::f();}',
    'struct B{int f(int n)const{return n;}};struct D:B{};int g(const D*p){return p->B::f(42);}',
    'struct B{static int f(int n){return n;}};struct D:B{};D*get();int g(){return get()->B::f(42);}',
)
NEGATIVE = (
    ('struct B{int x;};struct D:B{};int g(D*d){return d->B::x;}', 'cpp.unsupported.base-classes'),
    ('struct B{int f(){return 1;}};struct D{};int g(D*p){return p->B::f();}', 'cpp.invalid-member-access'),
    ('class B{int f(){return 1;}};struct D:B{};int g(D*p){return p->B::f();}', 'cpp.member-access'),
    ('struct B{protected:int f(){return 1;}};struct D:B{};int g(D*p){return p->B::f();}', 'cpp.member-access'),
    ('struct B{int f(){return 1;}};class D:private B{};int g(D*p){return p->B::f();}', 'cpp.inaccessible-base'),
    ('struct B{int f();};int g(B*p){return p->B::f();}', 'cpp.unsupported.base-classes'),
)


def walk(node):
    yield node
    for _, child in node.children():
        yield from walk(child)


@pytest.mark.parametrize('source', POSITIVE)
def test_qualified_method_uses_direct_selected_callee(source):
    parser = CppParser(TARGET)
    tree = parser.parse(source, 'qualified.cpp')
    method = parser.scope.lookup('B').dtype.definition.scope.lookup('f').overloads[0]
    calls = [n for n in walk(tree) if isinstance(n, c_ast.FuncCall)]
    assert any(isinstance(n.name, c_ast.ID) and n.name.name == method.c_name for n in calls)
    assert not any(isinstance(n.name, c_ast.StructRef) for n in calls)
    if 'get()' in source:
        assert sum(isinstance(n.name, c_ast.ID) and n.name.name == '_Z3getv' for n in calls) == 1


@pytest.mark.parametrize('source,code', NEGATIVE)
def test_qualified_call_does_not_bypass_data_object_or_access_contract(source, code):
    with pytest.raises(CppDiagnostic) as error:
        CppParser(TARGET).parse(source, 'negative.cpp')
    assert error.value.code == code


@pytest.mark.integration
@pytest.mark.parametrize('source', POSITIVE)
def test_same_qualified_source_against_real_cpp17(source):
    compiler = shutil.which('g++')
    assert compiler, 'a real C++17 reference is required'
    result = subprocess.run([compiler, '-std=c++17', '-pedantic-errors', '-fsyntax-only', '-x', 'c++', '-'],
                            input=source, text=True, capture_output=True, timeout=20)
    assert result.returncode == 0, result.stderr
