"""Borrowed class parameters, N4659 [dcl.init.ref], [expr.call], [over.ics.ref].

https://timsong-cpp.github.io/cppwp/n4659/dcl.init.ref
https://timsong-cpp.github.io/cppwp/n4659/expr.call
Member and explicitly qualified free calls bind existing class lvalues.
Unqualified free class-argument calls, templates, class copies and assignment
operators retain separate gates. All executable obligations locally UNRUN.
"""

from pathlib import Path

import pytest

from pcc.frontends.c.ast import c_ast
from pcc.frontends.cpp import CppDiagnostic, CppParser
from tests.cpp.owned_execution import compile_host_cpp
from tests.cpp.test_cpp_owned_execution import owned_only

# TinyXML2 11.0.0 h:2069,2147; upstream
# 9148bdf719e997d1f474be6bcc7943881046dba1, Zlib. Original statement, authored
# ordinary-method observer: this does not admit the original operator= body.
COPY_NODE = '        _node = ref._node;\r\n'
ORIGINAL_READ = 'struct N{int value;};int read(const N&n){return n.value;}'
ORIGINAL_RESULT_BOUNDARY = 'struct S{int n;};int f(const S&s){return s.n;}'
CASES = (
    ('tinyxml-borrowed-member-source-statement', 'struct XMLNode{int n;};'
     'struct XMLHandle{XMLNode*_node;void copy_from(const XMLHandle&ref){' + COPY_NODE + '}};'
     'int cpp_test_entry(){XMLNode a{17};XMLNode b{42};XMLHandle left{&a};const XMLHandle right{&b};'
     'left.copy_from(right);if(left._node!=&b||right._node!=&b)return 1;return left._node->n;}'),
    ('qualified-free-mutable-borrow', 'struct S{int n;};void add(S&s,int n){s.n+=n;}'
     'int cpp_test_entry(){S s{17};::add(s,25);return s.n;}'),
    ('namespace-qualified-const-borrow', 'namespace N{struct S{int n;};int read(const S&s){return s.n;}}'
     'int cpp_test_entry(){const N::S s{42};return N::read(s);}'),
    ('reference-parameter-returned-as-alias', 'struct S{int n;};S&identity(S&s){return s;}'
     'int cpp_test_entry(){S s{17};S&r=::identity(s);r.n+=25;if(&r!=&s)return 1;return s.n;}'),
    ('const-overload-selection', 'struct S{int n;};int pick(S&s){return 17;}int pick(const S&s){return 25;}'
     'int cpp_test_entry(){S a{1};const S b{2};return ::pick(a)+::pick(b);}'),
    ('out-of-class-member-borrow', 'struct S{int n;int sum(const S&other)const;};'
     'int S::sum(const S&other)const{return n+other.n;}'
     'int cpp_test_entry(){const S a{17};const S b{25};return a.sum(b);}'),
    ('static-member-borrow', 'struct S{int n;static int read(const S&s){return s.n;}};'
     'int cpp_test_entry(){S s{42};return S::read(s);}'),
    ('implicit-member-call-suppresses-adl', 'struct S{int n;int read(const S&s){return s.n;}'
     'int forward(const S&s){return read(s);}};int cpp_test_entry(){S a{1};S b{42};return a.forward(b);}'),
    ('qualified-argument-address-once', 'int calls=0;struct S{int n;};S&select(S*p){++calls;return *p;}'
     'int read(const S&s){return s.n;}int cpp_test_entry(){S s{42};int n=::read(::select(&s));'
     'if(calls!=1)return 1;return n;}'),
    ('member-receiver-before-reference-argument', 'int trace=0;struct S{int n;};'
     'struct H{int read(const S&s){return s.n;}};H*receiver(H*p){trace=trace*10+1;return p;}'
     'S&argument(S*p){trace=trace*10+2;return *p;}'
     'int cpp_test_entry(){H h{};S s{42};int n=receiver(&h)->read(argument(&s));if(trace!=12)return 1;return n;}'),
    ('static-receiver-before-reference-argument', 'int trace=0;struct S{int n;};'
     'struct H{static int read(const S&s){return s.n;}};H*receiver(H*p){trace=trace*10+1;return p;}'
     'S&argument(S*p){trace=trace*10+2;return *p;}'
     'int cpp_test_entry(){H h{};S s{42};int n=receiver(&h)->read(argument(&s));if(trace!=12)return 1;return n;}'),
    ('borrowed-parameter-is-not-destroyed', 'int destroyed=0;struct S{int n;~S(){++destroyed;}};'
     'int read(const S&s){return s.n;}int cpp_test_entry(){int n=0;{S s{42};n=::read(s);'
     'if(destroyed!=0)return 1;}if(destroyed!=1)return 2;return n;}'),
    ('same-object-two-reference-parameters', 'struct S{int n;};int change(S&a,S&b){a.n=17;b.n+=25;return a.n;}'
     'int cpp_test_entry(){S s{0};return ::change(s,s);}'),
    ('scalar-and-pointer-reference-coexist', 'struct S{int n;};int read(const S&s,int&n,int*&p){n+=s.n;p=&n;return *p;}'
     'int cpp_test_entry(){S s{17};int n=25;int*p=nullptr;return ::read(s,n,p);}'),
    ('subobject-reference-argument', 'struct S{int n;};struct Outer{S s;};int read(const S&s){return s.n;}'
     'int cpp_test_entry(){Outer o{{42}};return ::read(o.s);}'),
    ('const-reference-forwarded-qualified', 'struct S{int n;};int read(const S&s){return s.n;}'
     'int forward(const S&s){return ::read(s);}int cpp_test_entry(){S s{42};return ::forward(s);}'),
)
PROGRAMS = tuple((name, ((name+'.cpp', source),)) for name, source in CASES) + (
    ('former-constructor-reference-body-original', (
        ('original.cpp', ORIGINAL_READ),
        ('entry.cpp', 'struct N{int value;};int read(const N&);int cpp_test_entry(){N n{42};return ::read(n);}'),
    )),
    ('former-result-reference-body-original', (
        ('original.cpp', ORIGINAL_RESULT_BOUNDARY),
        ('entry.cpp', 'struct S{int n;};int f(const S&);int cpp_test_entry(){S s{42};return ::f(s);}'),
    )),
    ('incomplete-provider-borrows-identity', (
        ('provider.cpp', 'struct S;S*address(S&s){return &s;}'),
        ('entry.cpp', 'struct S{int n;};S*address(S&);int cpp_test_entry(){S s{42};if(::address(s)!=&s)return 1;return s.n;}'),
    )),
)


def test_tinyxml_borrowed_member_statements_preserve_exact_original_bytes():
    lines = (Path(__file__).resolve().parents[2] / 'projects/tinyxml2-11.0.0/tinyxml2.h').read_bytes().splitlines(keepends=True)
    assert lines[2068] == lines[2146] == COPY_NODE.encode()


@pytest.mark.parametrize('name,units', PROGRAMS, ids=[row[0] for row in PROGRAMS])
def test_class_reference_argument_artifacts_are_owned_and_stable(owned_only, name, units):
    assert compile_host_cpp(units) == compile_host_cpp(units)


INVALID = (
    ('drop-const-argument', 'struct S{};void f(S&s){}void g(){const S s{};::f(s);}', 'cpp.no-viable-overload'),
    ('different-nominal-class', 'struct A{};struct B{};void f(A&a){}void g(){B b;::f(b);}', 'cpp.no-viable-overload'),
    ('pointer-is-not-class-reference', 'struct S{};void f(S&s){}void g(S*p){::f(p);}', 'cpp.no-viable-overload'),
    ('const-reference-body-write', 'struct S{int n;};void f(const S&s){s.n=1;}', 'cpp.const-assignment'),
    ('private-member-through-reference', 'class S{int n;};int f(const S&s){return s.n;}', 'cpp.member-access'),
    ('reference-value-overload-ambiguous', 'struct S{int n;};int f(S);int f(const S&);'
     'int g(){S s{1};return ::f(s);}', 'cpp.ambiguous-overload'),
)
DEFERRED = (
    ('unqualified-free-call', 'struct S{int n;};int f(const S&s){return s.n;}int g(){S s{42};return f(s);}', 'cpp.unsupported.class-values'),
    ('adl-only-free-call', 'namespace N{struct S{int n;};int f(const S&s){return s.n;}}'
     'int g(){N::S s{42};return f(s);}', 'cpp.unsupported.class-values'),
    ('class-by-value-call', 'struct S{int n;};int f(S);int g(){S s{42};return ::f(s);}', 'cpp.unsupported.class-value-abi'),
    ('class-ellipsis-call', 'struct S{int n;};int f(int,...);int g(){S s{42};return ::f(0,s);}', 'cpp.unsupported.class-value-abi'),
    # Variadic members stop at declaration admission, before argument binding.
    ('static-member-class-ellipsis', 'struct S{int n;};struct H{static int f(int,...);};'
     'int g(){S s{42};return H::f(0,s);}', 'cpp.unsupported.variadic-functions'),
    ('assignment-body-still-unowned', 'struct S{void operator=(const S&){} };', 'cpp.unsupported.class-reference-values'),
    ('class-temporary-binding', 'struct S{S(int){} };int f(const S&);int g(){return ::f(S(42));}', 'cpp.unsupported.class-initialization'),
    ('reference-default', 'struct S{};S&object();int f(const S&s=object());', 'cpp.unsupported.default-argument-context'),
    ('class-reference-template', 'template<class T>int f(const T&s){return 42;}', 'cpp.unsupported.references'),
    ('class-template-reference-member', 'template<class T>struct H{int f(const T&s){return 42;}};', 'cpp.unsupported.references'),
    ('variadic-reference-signature', 'struct S{};int f(const S&,...);', 'cpp.unsupported.references'),
)


@pytest.mark.parametrize('name,source,code', INVALID + DEFERRED, ids=[row[0] for row in INVALID + DEFERRED])
def test_class_reference_argument_boundaries(name, source, code):
    with pytest.raises(CppDiagnostic) as caught:
        CppParser().parse(source)
    assert caught.value.code == code


def test_selected_class_reference_projects_address_not_aggregate_value():
    tree = CppParser().parse('struct S{int n;};int read(const S&s){return s.n;}'
                             'int f(){S s{42};return ::read(s);}')
    function = [node for node in tree.ext if isinstance(node, c_ast.FuncDef)][-1]
    declaration, returned = function.body.block_items
    argument, = returned.expr.args.exprs
    assert isinstance(argument, c_ast.UnaryOp) and argument.op == '&'
    assert isinstance(argument.expr, c_ast.ID) and argument.expr.name == declaration.name


def test_unqualified_class_arguments_stop_before_adl(monkeypatch):
    import pcc.frontends.cpp.parser as parser_module

    def forbidden(*args, **kwargs):
        raise AssertionError('unqualified class-value call reached ADL')

    monkeypatch.setattr(parser_module, 'argument_dependent_lookup', forbidden)
    with pytest.raises(CppDiagnostic) as caught:
        CppParser().parse('struct S{};void f(const S&s){}void g(){S s;f(s);}')
    assert caught.value.code == 'cpp.unsupported.class-values'
