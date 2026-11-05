"""Borrowed class lvalues/user-written C1 bodies; source obligations, UNRUN.

N4659 [dcl.init.ref], [class.copy.ctor]/1,6, [over.ics.ref], [class.access]:
https://timsong-cpp.github.io/cppwp/n4659/dcl.init.ref
https://timsong-cpp.github.io/cppwp/n4659/class.copy.ctor
https://timsong-cpp.github.io/cppwp/n4659/over.ics.ref

TinyXML2 11.0.0 tinyxml2.h:2062-2063 and 2065-2066 are exact CRLF
constructor excerpts, revision 9148bdf719e997d1f474be6bcc7943881046dba1,
Lee Thomason, Zlib. Original notice remains in
projects/tinyxml2-11.0.0/LICENSE.txt; identity is pinned in pcc-fixture.json.
All surrounding declarations and observers below are PCC-authored adaptations,
not acceptance of unchanged XMLHandle, its assignment operator, or TinyXML2.

Only direct parenthesized construction borrows class lvalues. No implicit
copy synthesis, aggregate copy, class result/materialization, list-first-phase
selection, reference default, or exception-unwinding support is claimed.
The pair helper targets direct x86_64 ELF owned artifacts, not native pcc1.
"""

import hashlib

import pytest

from pcc.frontends.c.ast import c_ast
from pcc.frontends.cpp import CppDiagnostic, CppParser
from pcc.frontends.cpp.types import ConstType, ReferenceType
from tests.cpp.owned_execution import compile_host_cpp
from tests.cpp.test_cpp_owned_execution import owned_only


ORIGINAL = 'struct S{S(const S&);};S::S(const S&other){}'
INCOMPLETE = 'struct N;struct H{H(N&n):p(&n){}N*p;};'
XML_NODE_CONSTRUCTOR = ('    explicit XMLHandle( XMLNode& node ) : _node( &node ) {\r\n'
                        '    }\r\n')
XML_COPY_CONSTRUCTOR = ('    XMLHandle( const XMLHandle& ref ) : _node( ref._node ) {\r\n'
                        '    }\r\n')
XML_NODE_SHA256 = '9207fc7cccb6bb9ec7b51b220ae1a27b8f8fe6dbdbb33a3d7991a1cb8c341574'
XML_COPY_SHA256 = 'd698aec67f6e71b31f1b852ef48334a8f4c267764bed7fedc05168d87d74e464'
XML_PROGRAM = (
    'struct XMLNode{int value;};class XMLHandle{public:\n' + XML_NODE_CONSTRUCTOR +
    XML_COPY_CONSTRUCTOR + 'int Read()const{return _node->value;}private:XMLNode*_node;};'
    'int cpp_test_entry(){XMLNode node{1};XMLHandle first(node);XMLHandle second(first);'
    'node.value=42;return second.Read();}'
)

SINGLE_PROGRAMS = (
    ('exact-upstream-reference-and-copy-c1-bodies', XML_PROGRAM),
    ('borrowed-object-address-alias', 'struct Node{int value;};'
     'struct H{H(Node&n):p(&n){}Node*p;};'
     'int cpp_test_entry(){Node n{1};H h(n);h.p->value=42;return n.value;}'),
    ('two-parameters-alias-one-object', 'struct Node{int value;};'
     'struct H{H(Node&a,Node&b){a.value=20;b.value+=22;}};'
     'int cpp_test_entry(){Node n{0};H h(n,n);return n.value;}'),
    ('const-referent-read', 'struct Node{int value;};'
     'struct H{H(const Node&n):value(n.value){}int value;};'
     'int cpp_test_entry(){const Node n{42};H h(n);return h.value;}'),
    ('cv-overload-before-binding', 'struct Node{int value;};'
     'struct H{H(Node&n):value(20){}H(const Node&n):value(22){}int value;};'
     'int cpp_test_entry(){Node a{1};const Node b{2};H x(a);H y(b);return x.value+y.value;}'),
    ('private-field-user-copy', 'class S{public:S(int n):value(n){}'
     'S(const S&other):value(other.value){}int get()const{return value;}private:int value;};'
     'int cpp_test_entry(){S a(42);S b(a);return b.get();}'),
    ('out-of-line-user-copy', 'struct S{S(int);S(const S&);int value;};'
     'S::S(int n):value(n){}S::S(const S&other):value(other.value){}'
     'int cpp_test_entry(){S a(42);S b(a);return b.value;}'),
    ('copy-with-trailing-default', 'struct S{S(int n):value(n){}'
     'S(const S&other,int extra=2):value(other.value+extra){}int value;};'
     'int cpp_test_entry(){S a(40);S b(a);return b.value;}'),
    ('explicit-user-copy-direct-participation', 'struct S{S(int n):value(n){}'
     'explicit S(const S&other):value(other.value){}int value;};'
     'int cpp_test_entry(){S a(42);S b(a);return b.value;}'),
    ('borrowed-parameter-has-no-destruction', 'int trace=0;'
     'struct S{S(int n):value(n){}S(const S&other):value(other.value){}'
     '~S(){trace=trace*10+value;}int value;};'
     'int cpp_test_entry(){{S first(2);S second(first);second.value=4;'
     'if(trace!=0)return 1;}if(trace!=42)return 1;return 42;}'),
    ('effectful-dereference-address-once', 'struct Node{int value;};int count=0;'
     'Node*choose(Node*p){count+=1;return p;}struct H{H(Node&n):p(&n){}Node*p;};'
     'int cpp_test_entry(){Node n{42};H h(*choose(&n));if(count!=1)return 1;return h.p->value;}'),
    ('effectful-array-element-address-once', 'struct Node{int value;};int count=0;'
     'int index(){count+=1;return 1;}struct H{H(Node&n):p(&n){}Node*p;};'
     'int cpp_test_entry(){Node nodes[2];nodes[1].value=42;H h(nodes[index()]);'
     'if(count!=1)return 1;return h.p->value;}'),
    ('nested-class-subobject-borrow', 'struct Node{int value;};struct Outer{Node node;};'
     'struct H{H(Node&n):p(&n){}Node*p;};'
     'int cpp_test_entry(){Outer outer{{42}};H h(outer.node);return h.p->value;}'),
    ('mutable-subobject-of-const-owner', 'struct Node{int value;};struct Outer{mutable Node node;};'
     'struct H{H(Node&n):p(&n){}Node*p;};'
     'int cpp_test_entry(){const Outer outer{{1}};H h(outer.node);h.p->value=42;return outer.node.value;}'),
    ('const-reference-member-call', 'struct Node{int value;int get()const{return value;}};'
     'struct H{H(const Node&n):value(n.get()){}int value;};'
     'int cpp_test_entry(){const Node n{42};H h(n);return h.value;}'),
    ('namespace-alias-reference-identity', 'namespace N{struct Node{int value;};using Alias=Node;'
     'struct H{H(const Alias&n):value(n.value){}int value;};}'
     'int cpp_test_entry(){N::Node n{42};N::H h(n);return h.value;}'),
    ('forward-borrowed-reference-without-copy', 'struct Node{int value;};'
     'struct Inner{Inner(Node&n){n.value+=20;}};'
     'struct Outer{Outer(Node&n){Inner inner(n);n.value+=22;}};'
     'int cpp_test_entry(){Node n{0};Outer outer(n);return n.value;}'),
    # No N can be instantiated here; this checks emission of address-only C1
    # against an incomplete referent, not an invented object lifetime.
    ('incomplete-referent-address-only-body', INCOMPLETE + 'int cpp_test_entry(){return 42;}'),
)

CROSS_DECLARATION = 'struct Node{int value;};struct H{H(const Node&);int value;};'
PROGRAMS = tuple((name, ((name+'.cpp', source),)) for name, source in SINGLE_PROGRAMS) + (
    # The exact formerly-rejected original has only a copy constructor and no
    # constructible seed. Keep that entire TU unchanged; the separate observer
    # does not pretend to call it. Richer rows above exercise actual C1 calls.
    ('exact-formerly-rejected-copy-body', (
        ('original.cpp', ORIGINAL), ('entry.cpp', 'int cpp_test_entry(){return 42;}'),
    )),
    # One out-of-line definition only: no duplicated inline stack-map identity
    # or merged-stackmap/platform acceptance is inferred from direct ELF here.
    ('class-reference-c1-across-translation-units', (
        ('definition.cpp', CROSS_DECLARATION + 'H::H(const Node&n):value(n.value){}'),
        ('entry.cpp', CROSS_DECLARATION + 'int cpp_test_entry(){Node n{42};H h(n);return h.value;}'),
    )),
)

INVALID = (
    ('const-source-cannot-bind-mutable', 'struct N{int value;};struct H{H(N&){}};'
     'int f(){const N n{42};H h(n);return 0;}', 'cpp.no-viable-overload'),
    ('same-layout-not-same-type', 'struct A{int value;};struct B{int value;};struct H{H(A&){}};'
     'int f(){B b{42};H h(b);return 0;}', 'cpp.no-viable-overload'),
    ('pointer-not-reference-argument', 'struct N{};struct H{H(N&){}};'
     'int f(){N n;H h(&n);return 0;}', 'cpp.no-viable-overload'),
    ('const-body-write', 'struct N{int value;};struct H{H(const N&n){n.value=42;}};', 'cpp.const-assignment'),
    ('unrelated-private-field', 'class N{int value;};struct H{H(const N&n){int x=n.value;}};', 'cpp.member-access'),
    ('private-copy-constructor', 'class S{public:S(int){}private:S(const S&){} };'
     'int f(){S a(1);S b(a);return 0;}', 'cpp.member-access'),
    ('duplicate-cv-alias-signature', 'struct N{};using Alias=N;struct H{H(const N&);H(const Alias&);};', 'cpp.redeclaration'),
    ('definition-cv-does-not-match', 'struct N{};struct H{H(N&);};H::H(const N&){}', 'cpp.no-matching-member'),
    ('crossed-reference-ranks-ambiguous', 'struct N{};'
     'struct H{H(N&,const N&){}H(const N&,N&){} };int f(){N n;H h(n,n);return 0;}', 'cpp.ambiguous-overload'),
    ('copy-body-cannot-return-value', 'struct S{S(const S&){return 42;}};', 'cpp.constructor-return-value'),
    ('const-subobject-cannot-bind-mutable', 'struct N{int value;};struct O{N node;};struct H{H(N&){}};'
     'int f(){const O o{{42}};H h(o.node);return 0;}', 'cpp.no-viable-overload'),
    ('nonconst-copy-cannot-copy-const', 'struct S{S(int){}S(S&){} };'
     'int f(){const S a(1);S b(a);return 0;}', 'cpp.no-viable-overload'),
)

COPY_CLASS = 'struct S{S(int){}S(const S&){} };'
DEFERRED = (
    ('class-copy-initialization', COPY_CLASS + 'int f(){S a(1);S b=a;return 0;}', 'cpp.unsupported.class-initialization'),
    ('class-direct-list-initialization', COPY_CLASS + 'int f(){S a(1);S b{a};return 0;}', 'cpp.unsupported.class-initialization'),
    ('class-copy-list-initialization', COPY_CLASS + 'int f(){S a(1);S b={a};return 0;}', 'cpp.unsupported.class-initialization'),
    ('implicit-copy-not-synthesized', 'struct S{S(int){} };int f(){S a(1);S b(a);return 0;}', 'cpp.unsupported.class-initialization'),
    ('extra-required-parameter-not-copy-constructor', 'struct S{S(int){}S(const S&,int){} };'
     'int f(){S a(1);S b(a);return 0;}', 'cpp.unsupported.class-initialization'),
    ('class-reference-function-body', 'struct N{int value;};int read(const N&n){return n.value;}', 'cpp.unsupported.class-reference-values'),
    ('class-reference-assignment-body', 'struct S{void operator=(const S&){} };', 'cpp.unsupported.class-reference-values'),
    # The original namespace object precedes the reference default and needs its own lifetime owner.
    ('class-reference-default', 'struct N{};N node;struct H{H(const N&n=node){}};', 'cpp.unsupported.static-class-lifetime'),
    ('incomplete-reference-list-candidate-set', 'struct N{};struct H{H(int){}H(const N&){} };'
     'int f(){H h{42};return 0;}', 'cpp.unsupported.constructor-list-initialization'),
    ('class-temporary-materialization', COPY_CLASS + 'int f(){S b(S(42));return 0;}', 'cpp.unsupported.class-initialization'),
)


@pytest.mark.parametrize('name,units', PROGRAMS, ids=[row[0] for row in PROGRAMS])
def test_constructor_class_reference_original_artifacts_are_stable(owned_only, name, units):
    assert compile_host_cpp(units) == compile_host_cpp(units)


def test_exact_formerly_rejected_body_retains_reference_signature_and_pointer_projection():
    parser = CppParser()
    tree = parser.parse(ORIGINAL)
    owner = parser.scope.names['S'].dtype
    constructor, = owner.definition.constructors.overloads
    assert constructor.defined and constructor.c_name == '_ZN1SC1ERKS_'
    parameter, = constructor.parameters
    assert isinstance(parameter, ReferenceType) and isinstance(parameter.referent, ConstType)
    assert parameter.referent.base is owner
    body, = (node for node in tree.ext if isinstance(node, c_ast.FuncDef))
    projected = body.decl.type.args.params[1].type
    assert isinstance(projected, c_ast.PtrDecl) and projected.type.quals == ['const']
    assert not body.body.block_items  # No synthesized copy or referent cleanup.


def test_constructor_argument_is_address_not_class_value_load():
    parser = CppParser()
    tree = parser.parse('struct N{int value;};struct H{H(N&n):p(&n){}N*p;};'
                        'int f(){N n{42};H h(n);return h.p->value;}')
    body = tree.ext[-1].body.block_items
    call = body[2]
    assert isinstance(call, c_ast.FuncCall)
    argument = call.args.exprs[1]
    assert isinstance(argument, c_ast.UnaryOp) and argument.op == '&'
    assert isinstance(argument.expr, c_ast.ID) and argument.expr.name == body[0].name


def test_incomplete_class_reference_body_can_take_address_without_completing_referent():
    CppParser().parse(INCOMPLETE)


def test_upstream_constructor_excerpt_bytes_are_preserved():
    assert hashlib.sha256(XML_NODE_CONSTRUCTOR.encode()).hexdigest() == XML_NODE_SHA256
    assert hashlib.sha256(XML_COPY_CONSTRUCTOR.encode()).hexdigest() == XML_COPY_SHA256


@pytest.mark.parametrize('name,source,code', INVALID, ids=[row[0] for row in INVALID])
def test_invalid_class_reference_constructor_stops_before_emission(name, source, code, monkeypatch):
    from pcc.backend import owned_object_emit

    def forbidden(*args, **kwargs):
        raise AssertionError('invalid class reference constructor reached object emission')

    monkeypatch.setattr(owned_object_emit, 'emit_owned_object', forbidden)
    with pytest.raises(CppDiagnostic) as error:
        compile_host_cpp(((name+'.cpp', source),))
    assert error.value.code == code


@pytest.mark.parametrize('name,source,code', DEFERRED, ids=[row[0] for row in DEFERRED])
def test_unowned_class_reference_domains_remain_explicit(name, source, code):
    with pytest.raises(CppDiagnostic) as error:
        CppParser().parse(source)
    assert error.value.code == code
