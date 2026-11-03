"""Declared member C1 calls, source-only C++17 obligations; all UNRUN.

N4659 [class.base.init]/7,9,13, [dcl.init]/8, [class.dtor], [class.access]:
https://timsong-cpp.github.io/cppwp/n4659/class.base.init
https://timsong-cpp.github.io/cppwp/n4659/dcl.init
https://timsong-cpp.github.io/cppwp/n4659/class.dtor

TinyXML2 11.0.0 exact constructor excerpts: tinyxml2.h:149 and
tinyxml2.cpp:798-808, revision 9148bdf719e997d1f474be6bcc7943881046dba1,
Lee Thomason, Zlib; original notice/identity remain at
projects/tinyxml2-11.0.0/{LICENSE.txt,pcc-fixture.json}. Surrounding declarations
and observer are PCC-authored adaptations. In particular, real StrPair/XMLNode
nontrivial/virtual destructors are NOT included or claimed supported. This is
an excerpt prerequisite regression, not unchanged class/project acceptance.

Members must have declared C1 candidates and recursively trivial destruction.
No implicit member constructor, member D1, aggregate copy or unwinding is
synthesized. Output oracles use declaration order between members, never an
unspecified order among arguments/default arguments within a parenthesized call.
Owned output/reference pairs use direct x86_64 ELF, not native pcc1.
"""

import hashlib

import pytest

from pcc.frontends.c.ast import c_ast
from pcc.frontends.cpp import CppDiagnostic, CppParser
from tests.cpp.owned_execution import compile_host_cpp
from tests.cpp.test_cpp_owned_execution import owned_only


ORIGINAL_DECLARATION = 'struct A{};struct S{A a;~S();};'
ORIGINAL_INLINE = 'struct T{};struct S{T value;~S(){}};'
ORIGINAL_PLACEMENT = ('using Size=decltype(sizeof(0));'
                      'void*operator new(Size,void*)noexcept;struct M{M(){}};struct O{M m;O(){}};'
                      'void f(O*p){new(p) O;}')
STRPAIR_CONSTRUCTOR = '    StrPair() : _flags( 0 ), _start( 0 ), _end( 0 ) {}\r\n'
STRPAIR_SHA256 = 'fb420df68e33b9ef33c085d027744054eef9ddbe204b3a9bee38efbd2b0bcf6c'
XMLNODE_CONSTRUCTOR = (
    'XMLNode::XMLNode( XMLDocument* doc ) :\r\n'
    '    _document( doc ),\r\n'
    '    _parent( 0 ),\r\n'
    '    _value(),\r\n'
    '    _parseLineNum( 0 ),\r\n'
    '    _firstChild( 0 ), _lastChild( 0 ),\r\n'
    '    _prev( 0 ), _next( 0 ),\r\n'
    '\t_userData( 0 ),\r\n'
    '    _memPool( 0 )\r\n'
    '{\r\n}\r\n'
)
XMLNODE_SHA256 = 'a070cb3b7ce306c76184bcbd10b84f53c7191d3f39b59f8c356081fcb54f0972'
XML_PROGRAM = (
    'struct XMLDocument;struct MemPool;class StrPair{public:\n' + STRPAIR_CONSTRUCTOR +
    'int Empty()const{if(_flags==0&&_start==nullptr&&_end==nullptr)return 1;return 0;}'
    'private:int _flags;char*_start;char*_end;};'
    'struct XMLNode{explicit XMLNode(XMLDocument*);XMLDocument*_document;XMLNode*_parent;'
    'mutable StrPair _value;int _parseLineNum;XMLNode*_firstChild;XMLNode*_lastChild;'
    'XMLNode*_prev;XMLNode*_next;void*_userData;MemPool*_memPool;};\n' + XMLNODE_CONSTRUCTOR +
    'int cpp_test_entry(){XMLNode node(nullptr);if(!node._value.Empty())return 1;'
    'if(node._document!=nullptr||node._parent!=nullptr||node._parseLineNum!=0)return 1;'
    'if(node._firstChild!=nullptr||node._lastChild!=nullptr||node._prev!=nullptr||node._next!=nullptr)return 1;'
    'if(node._userData!=nullptr||node._memPool!=nullptr)return 1;return 42;}'
)

SINGLE_PROGRAMS = (
    ('exact-upstream-member-and-enclosing-constructor-bodies', XML_PROGRAM),
    ('omitted-and-empty-member-call-defaulted-c1', 'struct M{M(int n=21):value(n){}int value;};'
     'struct O{M a;M b;O():b(){}};int cpp_test_entry(){O o;return o.a.value+o.b.value;}'),
    ('multiple-parenthesized-member-arguments', 'struct M{M(int a,int b):value(a+b){}int value;};'
     'struct O{M m;O(int a,int b):m(a,b){}};int cpp_test_entry(){O o(20,22);return o.m.value;}'),
    ('explicit-member-constructor-retains-overload-ranking', 'struct M{explicit M(int n):value(n){}M(long):value(1){}int value;};'
     'struct O{M m;O():m(42){}};int cpp_test_entry(){O o;return o.m.value;}'),
    ('mixed-member-declaration-order-not-spelling-order', 'int trace=0;'
     'int step(int n){trace=trace*10+n;return n;}'
     'struct M{M(int n):value(n){}int value;};'
     'struct O{M a;int middle;M b;O():b(step(3)),middle(step(2)),a(step(1)){trace=trace*10+4;}};'
     'int cpp_test_entry(){O o;if(trace!=1234||o.a.value!=1||o.middle!=2||o.b.value!=3)return 1;return 42;}'),
    ('member-argument-evaluated-once', 'int calls=0;int next(){calls+=1;return 42;}'
     'struct M{M(int n):value(n){}int value;};struct O{M m;O():m(next()){}};'
     'int cpp_test_entry(){O o;if(calls!=1)return 1;return o.m.value;}'),
    ('construction-uses-final-member-address', 'struct M{M(int n):self(this),value(n){}M*self;int value;};'
     'struct O{M m;O():m(42){}};int cpp_test_entry(){O o;if(o.m.self!=&o.m)return 1;return o.m.value;}'),
    ('explicit-user-copy-of-member', 'struct M{M(int n):value(n){}M(const M&other):value(other.value+1){}int value;};'
     'struct O{M m;O(int n):m(n){}O(const O&other):m(other.m){}};'
     'int cpp_test_entry(){O first(41);O second(first);return second.m.value;}'),
    ('member-c1-borrows-class-lvalue', 'struct N{int value;};struct M{M(N&n):p(&n){}N*p;};'
     'struct O{M m;O(N&n):m(n){}};int cpp_test_entry(){N n{1};O o(n);o.m.p->value=42;return n.value;}'),
    ('const-class-member-with-user-provided-c1', 'struct M{M(int n):value(n){}int value;};'
     'struct O{const M m;O():m(42){}};int cpp_test_entry(){O o;return o.m.value;}'),
    ('default-evaluated-for-each-member', 'int calls=0;int next(){calls+=1;return 21;}'
     'struct M{M(int n=next()):value(n){}int value;};struct O{M a;M b;O():b(){}};'
     'int cpp_test_entry(){O o;if(calls!=2)return 1;return o.a.value+o.b.value;}'),
    ('recursive-declared-c1-tree', 'struct Leaf{Leaf(int n):value(n){}int value;};'
     'struct Middle{Leaf leaf;Middle(int n):leaf(n){}};'
     'struct Outer{Middle middle;Outer(int n):middle(n){}};'
     'int cpp_test_entry(){Outer outer(42);return outer.middle.leaf.value;}'),
    ('initialized-member-visible-to-later-member', 'struct M{M(int n):value(n){}int value;};'
     'struct O{M a;M b;O():b(a.value+22),a(20){}};'
     'int cpp_test_entry(){O o;return o.b.value;}'),
    ('private-member-not-private-constructor', 'struct M{M(int n):value(n){}int value;};'
     'class O{M m;public:O():m(42){}int get()const{return m.value;}};'
     'int cpp_test_entry(){O o;return o.get();}'),
    ('outer-d1-after-member-c1-no-member-d1', 'int trace=0;'
     'struct M{M(int n):value(n){}int value;};struct O{M m;O(int n):m(n){}~O(){trace=trace*10+m.value;}};'
     'int cpp_test_entry(){{O first(2);O second(4);if(trace!=0)return 1;}if(trace!=42)return 1;return 42;}'),
    ('return-cleans-outer-with-trivial-member', 'int trace=0;'
     'struct M{M(int n):value(n){}int value;};struct O{M m;O(int n):m(n){}~O(){trace+=m.value;}};'
     'int f(){O local(2);return 40;}int cpp_test_entry(){int value=f();return value+trace;}'),
    ('constructor-body-local-normal-cleanup', 'int trace=0;struct Guard{~Guard(){trace+=2;}};'
     'struct M{M(){trace+=20;}};struct O{M m;O(){Guard guard;trace+=20;return;}};'
     'int cpp_test_entry(){O o;return trace;}'),
    ('outer-d1-with-trivial-plain-record-member', 'int result=0;struct M{int value;};'
     'struct O{M m;~O(){result=m.value;}};'
     'int cpp_test_entry(){{O o;o.m.value=42;}return result;}'),
)

CROSS_DECLARATION = 'struct M{M(int);int value;};struct O{M m;O(int);};'
PROGRAMS = tuple((name, ((name+'.cpp', source),)) for name, source in SINGLE_PROGRAMS) + (
    ('original-trivial-member-d1-declaration', (
        ('original.cpp', ORIGINAL_DECLARATION),
        ('entry.cpp', ORIGINAL_DECLARATION + 'S::~S(){}int cpp_test_entry(){S s;return 42;}'),
    )),
    # This exact original's emitted D1 is not invoked by its observer. Richer
    # rows exercise actual outer D1 calls without duplicated inline definitions.
    ('original-trivial-member-inline-d1', (
        ('original.cpp', ORIGINAL_INLINE), ('entry.cpp', 'int cpp_test_entry(){return 42;}'),
    )),
    # One out-of-line definition per C1: no duplicate inline stack-map identity
    # or merged-stackmap platform capability is inferred from direct ELF.
    ('member-c1-across-translation-units', (
        ('definition.cpp', CROSS_DECLARATION + 'M::M(int n):value(n){}O::O(int n):m(n){}'),
        ('entry.cpp', CROSS_DECLARATION + 'int cpp_test_entry(){O o(42);return o.m.value;}'),
    )),
)

INVALID = (
    ('no-default-member-constructor', 'struct M{M(int){}};struct O{M m;O(){}};', 'cpp.no-viable-overload'),
    ('ambiguous-default-member-constructor', 'struct M{M(int=1){}M(long=2){}};struct O{M m;O():m(){}};', 'cpp.ambiguous-overload'),
    ('private-member-constructor', 'class M{M(){}};struct O{M m;O():m(){}};', 'cpp.member-access'),
    ('member-constructor-arity', 'struct M{M(int){}};struct O{M m;O():m(1,2){}};', 'cpp.no-viable-overload'),
    ('member-constructor-pointer-conversion', 'struct M{M(int*){}};struct O{M m;O():m(42){}};', 'cpp.no-viable-overload'),
    ('duplicate-class-member-initializer', 'struct M{M(){}};struct O{M m;O():m(),m(){}};', 'cpp.duplicate-member-initializer'),
    ('unknown-class-member-initializer', 'struct M{M(){}};struct O{M m;O():missing(){}};', 'cpp.invalid-member-initializer'),
    ('const-class-member-body-write', 'struct M{M(int n):value(n){}int value;};'
     'struct O{const M m;O():m(1){m.value=42;}};', 'cpp.const-assignment'),
    ('const-source-not-mutable-reference', 'struct N{};struct M{M(N&){}};'
     'struct O{M m;O(const N&n):m(n){}};', 'cpp.no-viable-overload'),
    ('private-member-copy-constructor', 'class M{public:M(int){}private:M(const M&){} };'
     'struct O{M m;O(const O&other):m(other.m){}};', 'cpp.member-access'),
)

DEFERRED = (
    ('nontrivial-member-destruction', 'struct M{M(){}~M(){}};struct O{M m;O():m(){}};', 'cpp.unsupported.destructor-subobjects'),
    ('transitive-member-destruction', 'struct Leaf{~Leaf(){}};struct M{Leaf leaf;};'
     'struct O{M m;O(){}};', 'cpp.unsupported.destructor-subobjects'),
    ('implicit-member-value-initialization', 'struct M{int value;};struct O{M m;O():m(){}};', 'cpp.unsupported.constructor-subobjects'),
    ('implicit-member-default-initialization', 'struct M{int value;};struct O{M m;O(){m.value=42;}};', 'cpp.unsupported.constructor-subobjects'),
    ('implicit-enclosing-constructor', 'struct M{M(){}};struct O{M m;};int f(){O o;return 0;}', 'cpp.unsupported.constructor-subobjects'),
    ('scalar-dmi-with-class-member', 'struct M{M(){}};struct O{M m;int n=42;O():m(){}};', 'cpp.unsupported.default-member-initializer-path'),
    ('braced-class-member-initializer', 'struct M{M(int){}};struct O{M m;O():m{42}{}};', 'cpp.unsupported.constructor-list-initialization'),
    ('class-member-array', 'struct M{M(){}};struct O{M m[2];O(){}};', 'cpp.unsupported.class-array-lifetime'),
    ('class-member-template-pattern', 'struct M{M(int){}};template<class T>struct O{M m;O(T n):m(n){}};', 'cpp.unsupported.class-templates'),
    ('implicit-member-copy-not-invented', 'struct M{M(int){}};struct O{M m;O(const O&other):m(other.m){}};', 'cpp.unsupported.class-initialization'),
)


@pytest.mark.parametrize('name,units', PROGRAMS, ids=[row[0] for row in PROGRAMS])
def test_member_constructor_original_artifacts_are_stable(owned_only, name, units):
    assert compile_host_cpp(units) == compile_host_cpp(units)


@pytest.mark.parametrize('source', (ORIGINAL_DECLARATION, ORIGINAL_INLINE))
def test_formerly_rejected_trivial_member_destructor_originals_are_positive(source):
    parser = CppParser()
    parser.parse(source)
    assert parser.scope.names['S'].dtype.definition.destructor is not None


def test_member_initialization_calls_c1_at_field_address_without_aggregate_stores():
    parser = CppParser()
    parser.parse('struct M{M(int n){}};struct O{M first;M second;O():second(2),first(1){}};')
    pending, = parser.scope.names['O'].dtype.definition.inline_constructors
    first, second = pending.function.body.block_items
    for call, name, value in ((first, 'first', '1'), (second, 'second', '2')):
        assert isinstance(call, c_ast.FuncCall) and 'C1E' in call.name.name
        address, argument = call.args.exprs
        assert isinstance(address, c_ast.UnaryOp) and address.op == '&'
        assert isinstance(address.expr, c_ast.StructRef) and address.expr.type == '->'
        assert address.expr.field.name == name and argument.value == value


def test_empty_and_omitted_member_calls_do_not_zero_user_provided_constructor_storage():
    parser = CppParser()
    parser.parse('struct M{M(){}int value;};struct O{M first;M second;O():second(){}};')
    pending, = parser.scope.names['O'].dtype.definition.inline_constructors
    assert len(pending.function.body.block_items) == 2
    assert all(isinstance(node, c_ast.FuncCall) and len(node.args.exprs) == 1
               for node in pending.function.body.block_items)


def test_upstream_constructor_excerpt_bytes_are_preserved():
    assert hashlib.sha256(STRPAIR_CONSTRUCTOR.encode()).hexdigest() == STRPAIR_SHA256
    assert hashlib.sha256(XMLNODE_CONSTRUCTOR.encode()).hexdigest() == XMLNODE_SHA256


@pytest.mark.parametrize('name,source,code', INVALID, ids=[row[0] for row in INVALID])
def test_invalid_member_constructor_stops_before_emission(name, source, code, monkeypatch):
    from pcc.backend import owned_object_emit

    def forbidden(*args, **kwargs):
        raise AssertionError('invalid member construction reached object emission')

    monkeypatch.setattr(owned_object_emit, 'emit_owned_object', forbidden)
    with pytest.raises(CppDiagnostic) as error:
        compile_host_cpp(((name+'.cpp', source),))
    assert error.value.code == code


@pytest.mark.parametrize('name,source,code', DEFERRED, ids=[row[0] for row in DEFERRED])
def test_unowned_member_lifetime_domains_remain_explicit(name, source, code):
    with pytest.raises(CppDiagnostic) as error:
        CppParser().parse(source)
    assert error.value.code == code
