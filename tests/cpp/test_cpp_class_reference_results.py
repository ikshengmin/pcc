"""Borrowed ordinary class results, N4659 [dcl.init.ref], [stmt.return].

https://timsong-cpp.github.io/cppwp/n4659/dcl.init.ref
https://timsong-cpp.github.io/cppwp/n4659/stmt.return
TinyXML2's return statement is unchanged inside authored ordinary methods.
Assignment-operator bodies, class-reference arguments and class-value returns
remain separate boundaries. All executable obligations are locally UNRUN.
"""

from pathlib import Path

import pytest

from pcc.frontends.c.ast import c_ast
from pcc.frontends.cpp import CppDiagnostic, CppParser
from pcc.frontends.cpp.constants import NOT_CONSTANT
from tests.cpp.owned_execution import compile_host_cpp
from tests.cpp.test_cpp_owned_execution import owned_only

# TinyXML2 11.0.0 h:2070,2148; upstream
# 9148bdf719e997d1f474be6bcc7943881046dba1, Zlib. Preserve original CRLF.
RETURN_THIS = '        return *this;\r\n'
ORIGINAL = 'struct L{int n;};L&f();'
ORIGINAL_SIZEOF = 'struct S{int n;};S&get();int f(){return sizeof(get());}'
CASES = (
    ('tinyxml-self-return-statement', 'struct S{int n;S&self();};S&S::self(){' + RETURN_THIS +
     '}int cpp_test_entry(){S s{17};S&r=s.self();r.n+=25;if(&r!=&s)return 1;return s.n;}'),
    ('tinyxml-const-self-return-statement', 'struct S{int n;const S&self()const;};const S&S::self()const{' + RETURN_THIS +
     '}int cpp_test_entry(){const S s{42};const S&r=s.self();if(&r!=&s)return 1;return r.n;}'),
    ('free-function-borrows-existing-object', 'struct S{int n;};S&borrow(S*p){return *p;}'
     'int cpp_test_entry(){S s{17};borrow(&s).n+=25;return s.n;}'),
    ('const-result-adds-qualification', 'struct S{int n;};const S&borrow(S*p){return *p;}'
     'int cpp_test_entry(){S s{42};const S&r=borrow(&s);return r.n;}'),
    ('return-through-local-alias', 'struct S{int n;};S&borrow(S*p){S&r=*p;return r;}'
     'int cpp_test_entry(){S s{42};return borrow(&s).n;}'),
    ('reference-result-forwarded-without-copy', 'struct S{int n;};S&one(S*p){return *p;}S&two(S*p){return one(p);}'
     'int cpp_test_entry(){S s{42};if(&two(&s)!=&s)return 1;return two(&s).n;}'),
    ('effectful-result-base-once', 'int calls=0;struct S{int n;};S&borrow(S*p){++calls;return *p;}'
     'int cpp_test_entry(){S s{17};borrow(&s).n+=25;if(calls!=1)return 1;return s.n;}'),
    ('member-call-through-borrowed-result', 'struct S{int n;int read()const{return n;}};S&borrow(S*p){return *p;}'
     'int cpp_test_entry(){S s{42};return borrow(&s).read();}'),
    ('static-member-returns-class-reference', 'struct S{int n;static S&borrow(S*p){return *p;}};'
     'int cpp_test_entry(){S s{42};if(&S::borrow(&s)!=&s)return 1;return S::borrow(&s).n;}'),
    ('inline-self-method-result', 'struct S{int n;S&self(){return *this;}};'
     'int cpp_test_entry(){S s{42};if(&s.self()!=&s)return 1;return s.self().n;}'),
    ('static-call-discarded-base-still-evaluated', 'int calls=0;struct S{static int answer(){return 42;}};'
     'S&borrow(S*p){++calls;return *p;}'
     'int cpp_test_entry(){S s{};int n=borrow(&s).answer();if(calls!=1)return 1;return n;}'),
    ('enumerator-discarded-base-still-evaluated', 'int calls=0;struct S{enum{Answer=42};};'
     'S&borrow(S*p){++calls;return *p;}'
     'int cpp_test_entry(){S s{};int n=borrow(&s).Answer;if(calls!=1)return 1;return n;}'),
    ('sizeof-result-does-not-call', 'int calls=0;struct S{int n;};S&borrow(S*p){++calls;return *p;}'
     'int cpp_test_entry(){S s{42};if(sizeof(borrow(&s))!=sizeof(S)||calls!=0)return 1;return s.n;}'),
    ('conditional-reference-result-selects-one-object', 'int calls=0;struct S{int n;};S&borrow(S*p){++calls;return *p;}'
     'int cpp_test_entry(){S a{17};S b{25};S&r=false?borrow(&a):borrow(&b);r.n+=17;'
     'if(calls!=1||a.n!=17||&r!=&b)return 1;return b.n;}'),
    ('no-borrowed-result-destructor', 'int destroyed=0;struct S{int n;~S(){++destroyed;}};S&borrow(S*p){return *p;}'
     'int cpp_test_entry(){int n=0;{S s{42};S&r=borrow(&s);n=r.n;if(destroyed!=0)return 1;}'
     'if(destroyed!=1)return 2;return n;}'),
    ('return-address-captured-before-cleanup', 'int trace=0;struct S{int n;};struct G{~G(){trace=trace*10+2;}};'
     'S*select(S*p){trace=trace*10+1;return p;}S&borrow(S*p){G guard;return *select(p);}'
     'int cpp_test_entry(){S s{42};S&r=borrow(&s);if(trace!=12||&r!=&s)return 1;return r.n;}'),
    ('namespace-borrowed-result', 'namespace N{struct S{int n;};S&borrow(S*p){return *p;}}'
     'int cpp_test_entry(){N::S s{42};return N::borrow(&s).n;}'),
    ('returned-reference-overload-keeps-const', 'struct S{int n;S&self(){return *this;}const S&self()const{return *this;}};'
     'int cpp_test_entry(){S a{17};const S b{25};a.self().n+=b.self().n;return a.n;}'),
)
PROGRAMS = tuple((name, ((name+'.cpp', source),)) for name, source in CASES) + (
    ('former-class-result-declaration-original', (
        ('original.cpp', ORIGINAL),
        ('entry.cpp', 'int cpp_test_entry(){return 42;}'),
    )),
    ('former-class-result-sizeof-original', (
        ('original.cpp', ORIGINAL_SIZEOF),
        # Deliberately no get() definition: both links must succeed without
        # emitting a call from the original sizeof operand.
        ('entry.cpp', 'int f();int cpp_test_entry(){if(f()!=sizeof(int))return 1;return 42;}'),
    )),
    ('cross-translation-unit-borrowed-result', (
        ('provider.cpp', 'struct S{int n;};S&borrow(S*p){return *p;}'),
        ('entry.cpp', 'struct S{int n;};S&borrow(S*);int cpp_test_entry(){S s{42};'
         'if(&borrow(&s)!=&s)return 1;return borrow(&s).n;}'),
    )),
)


def test_tinyxml_self_return_statements_preserve_exact_original_bytes():
    path = Path(__file__).resolve().parents[2] / 'projects/tinyxml2-11.0.0/tinyxml2.h'
    lines = path.read_bytes().splitlines(keepends=True)
    assert lines[2069] == lines[2147] == RETURN_THIS.encode()


@pytest.mark.parametrize('name,units', PROGRAMS, ids=[row[0] for row in PROGRAMS])
def test_class_reference_result_artifacts_are_owned_and_stable(owned_only, name, units):
    assert compile_host_cpp(units) == compile_host_cpp(units)


INVALID = (
    ('drop-result-const', 'struct S{};S&f(const S*p){return *p;}', 'cpp.invalid-reference-binding'),
    ('different-class-result', 'struct A{};struct B{};A&f(B*p){return *p;}', 'cpp.invalid-reference-binding'),
    ('pointer-is-not-class-lvalue', 'struct S{};S&f(S*p){return p;}', 'cpp.invalid-reference-binding'),
    ('missing-reference-return', 'struct S{};S&f(){return;}', 'cpp.return-value-required'),
    ('void-reference-return', 'struct S{};void nothing(){}S&f(){return nothing();}', 'cpp.return-void-expression'),
    ('modify-const-result', 'struct S{int n;};const S&f(S*p){return *p;}void g(S*p){f(p).n=1;}', 'cpp.const-assignment'),
    ('nonconst-alias-of-const-result', 'struct S{};const S&f(S*p){return *p;}void g(S*p){S&r=f(p);}', 'cpp.invalid-reference-binding'),
)
DEFERRED = (
    ('class-copy-from-result', 'struct S{int n;};S&f(S*p){return *p;}void g(S*p){S copy=f(p);}', 'cpp.unsupported.class-initialization'),
    ('assignment-operator-body', 'struct S{S&operator=(const S&other){return *this;}};', 'cpp.unsupported.class-reference-values'),
    ('class-value-return', 'struct S{};S f(){S s;return s;}', 'cpp.unsupported.class-value-abi'),
    ('discarded-class-result-statement', 'struct S{};S&f(S*p){return *p;}void g(S*p){f(p);}', 'cpp.unsupported.class-values'),
    ('template-class-reference-result', 'struct S{};template<class T>struct Box{S&get();};', 'cpp.unsupported.references'),
    ('dependent-class-reference-result', 'struct S{};template<class T>struct Box{T*p;T&get(){return *p;}};'
     'S&f(Box<S>*p){return p->get();}', 'cpp.unsupported.references'),
    ('ordinary-class-reference-call-in-template', 'struct S{};S&borrow(S*);S*address();'
     'template<class T>int f(T x){return sizeof(borrow(address()));}', 'cpp.unsupported.references'),
    ('class-result-enumerator-is-not-static-proof', 'struct S{enum{N=42};};S&f(S*p){return *p;}'
     'int g(S*p){static const int n=f(p).N;return n;}', 'cpp.unsupported.dynamic-initialization'),
)


@pytest.mark.parametrize('name,source,code', INVALID + DEFERRED, ids=[row[0] for row in INVALID + DEFERRED])
def test_class_reference_result_boundaries(name, source, code):
    with pytest.raises(CppDiagnostic) as caught:
        CppParser().parse(source)
    assert caught.value.code == code


def test_class_reference_call_is_no_fetch_lvalue_but_retains_effectful_address():
    class CaptureReturn(CppParser):
        def _return_statement(self, expression, coord):
            self.last_return_expression = expression
            return super()._return_statement(expression, coord)

    parser = CaptureReturn()
    tree = parser.parse('struct S{int n;};S&one(S*p){return *p;}S&two(S*p){return one(p);}')
    expression = parser.last_return_expression
    assert expression.lvalue and expression.discarded_known
    assert isinstance(expression.node, c_ast.UnaryOp) and expression.node.op == '*'
    assert isinstance(expression.discarded_node, c_ast.FuncCall)
    assert expression.discarded_node is expression.node.expr
    assert expression.constant_fact.status == NOT_CONSTANT
    function = [node for node in tree.ext if isinstance(node, c_ast.FuncDef)][-1]
    result, = function.body.block_items
    assert isinstance(result, c_ast.Return) and isinstance(result.expr, c_ast.FuncCall)


def test_incomplete_class_reference_result_needs_no_layout():
    CppParser().parse('struct S;S&borrow(S*p){return *p;}S*address(S*p){return &borrow(p);}')
