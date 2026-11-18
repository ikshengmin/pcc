"""Non-NRVO dereference returns: source and selected constructor obligations.

The mixed-object integration fixture separately checks the unchanged source
`return *this;` for calls between real compiler-generated objects.
"""
import pytest
from pcc.frontends.cpp import CppParser, CppDiagnostic
from pcc.frontends.c.ast import c_ast


def nodes(tree):
    yield tree
    for _,child in tree.children():
        yield from nodes(child)


@pytest.mark.parametrize('operand', ['*p', '(*p)'])
def test_dereference_return_emits_real_copy(operand):
    source=('struct H{int*p;H(const H&x):p(x.p){}};'
            'H f(const H*p){return '+operand+';}')
    ast=CppParser(target_triple='x86_64-unknown-linux-gnu').parse(source)
    calls=[n for n in nodes(ast) if isinstance(n,c_ast.FuncCall)]
    assert any(isinstance(n.name,c_ast.ID) and 'C1' in n.name.name for n in calls)


def test_const_this_copy_is_not_nrvo():
    source=('struct H{int*p;H(const H&x):p(x.p){} H copy()const;};'
            'H H::copy()const{return *this;}')
    ast=CppParser(target_triple='x86_64-unknown-linux-gnu').parse(source)
    assert any(isinstance(n,c_ast.FuncCall) and isinstance(n.name,c_ast.ID)
               and 'C1' in n.name.name for n in nodes(ast))


@pytest.mark.parametrize('source',[
    'struct H{int*p;H(const H&);};H f(H&named){return named;}',
    'struct H{int*p;explicit H(const H&);};H f(const H*p){return *p;}',
    'struct H{int*p;H(H&);};H f(const H*p){return *p;}',
    'class H{int*p;H(const H&);};H f(const H*p){return *p;}',
])
def test_named_explicit_cv_and_access_do_not_silently_copy(source):
    with pytest.raises(CppDiagnostic):
        CppParser(target_triple='x86_64-unknown-linux-gnu').parse(source)
