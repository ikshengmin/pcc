"""Owned reference-result/subscript CI obligations, execution UNRUN.

N4659 [dcl.ref], [dcl.init.ref], [stmt.return], [over.sub], [expr.sub],
[temp.res], [temp.inst], [basic.def.odr].
https://timsong-cpp.github.io/cppwp/n4659/over.sub

SUBSCRIPT_MEMBERS and PEEK_TOP are exact CRLF TinyXML2 11.0.0 excerpts,
tinyxml2.h:253-261 and 263-266, upstream 9148bdf719e997d1f474be6bcc7943881046dba1,
Lee Thomason, Zlib. See projects/tinyxml2-11.0.0/{LICENSE.txt,pcc-fixture.json}.
The pointer-only scaffold, assertion shim and observer are PCC-authored. They
do not replace original DynArray's dependent array, private copy declarations,
sizeof, allocation/deallocation or the unchanged complete xmltest obligation.
"""

from pathlib import Path

import pytest

from pcc.frontends.c.ast import c_ast
from pcc.frontends.cpp import CppDiagnostic, CppParser
from pcc.frontends.cpp.class_template_members import MethodInstance
from pcc.frontends.cpp.types import ConstType, PointerType, ReferenceType
from tests.cpp.owned_execution import compile_host_cpp
from tests.cpp.test_cpp_owned_execution import owned_only


SUBSCRIPT_MEMBERS = (
    '    T& operator[](size_t i) {\r\n'
    '        TIXMLASSERT( i < _size );\r\n'
    '        return _mem[i];\r\n'
    '    }\r\n'
    '\r\n'
    '    const T& operator[](size_t i) const {\r\n'
    '        TIXMLASSERT( i < _size );\r\n'
    '        return _mem[i];\r\n'
    '    }\r\n'
)
PEEK_TOP = (
    '    const T& PeekTop() const            {\r\n'
    '        TIXMLASSERT( _size > 0 );\r\n'
    '        return _mem[ _size - 1];\r\n'
    '    }\r\n'
)
UPSTREAM_SCAFFOLD = (
    'using size_t=unsigned long;void TIXMLASSERT(bool value){}'
    'template<class T>struct DynArray{T*_mem;size_t _size;' +
    SUBSCRIPT_MEMBERS + PEEK_TOP + '};'
)
BOX = ('template<class T>struct Box{T*p;T&operator[](int i){return p[i];}'
       'const T&operator[](int i)const{return p[i];}};')

SINGLE_PROGRAMS = (
    ('exact-upstream-member-excerpts', UPSTREAM_SCAFFOLD +
     'int cpp_test_entry(){int values[2]={1,41};DynArray<int>b{values,2};b[0]=1;'
     'const DynArray<int>c{values,2};return c[0]+c.PeekTop();}'),
    ('mutable-reference-alias', BOX +
     'int cpp_test_entry(){int values[2]={0,1};Box<int>b{values};b[0]=42;'
     'if(&b[0]!=&values[0])return 1;return values[0];}'),
    ('const-reference-result', BOX +
     'int cpp_test_entry(){int values[1]={42};const Box<int>b{values};return b[0];}'),
    ('pointer-object-reference-result', BOX +
     'int cpp_test_entry(){int value=42;int*values[1]={nullptr};Box<int*>b{values};'
     'b[0]=&value;if(&b[0]!=&values[0])return 1;return *values[0];}'),
    ('effectful-subscript-once', BOX +
     'int cpp_test_entry(){int values[2]={0,0};int i=0;Box<int>b{values};'
     'b[i++]=42;if(i!=1)return 1;return values[0];}'),
    ('explicit-operator-call', BOX +
     'int cpp_test_entry(){int values[1]={0};Box<int>b{values};b.operator[](0)=42;return b[0];}'),
    ('reference-method-pattern-call',
     'template<class T>struct Box{T*p;T&get(){return *p;}T&forward(){return get();}};'
     'int cpp_test_entry(){int value=0;Box<int>b{&value};b.forward()=42;return value;}'),
    ('commuted-dependent-builtin',
     'template<class T>struct Box{T*p;T&get(int i){return i[p];}};'
     'int cpp_test_entry(){int values[2]={1,42};Box<int>b{values};return b.get(1);}'),
    ('ordinary-member-subscript',
     'struct Box{int*p;int&operator[](int i){return p[i];}};'
     'int cpp_test_entry(){int values[1]={0};Box b{values};b[0]=42;return values[0];}'),
    ('ordinary-reference-call-address-once',
     'int calls=0;int&get(int*p){++calls;return *p;}'
     'int cpp_test_entry(){int n=0;get(&n)=42;if(calls!=1||&get(&n)!=&n||calls!=2)return 1;return n;}'),
    ('reference-return-address-before-cleanup',
     'struct Guard{int*p;~Guard(){*p=42;}};int&get(int*p){Guard guard{p};return *p;}'
     'int cpp_test_entry(){int n=0;int*p=&get(&n);if(p!=&n)return 1;return n;}'),
    ('rhs-before-receiver-before-index', BOX +
     'int trace=0;int rhs(){trace=trace*10+1;return 42;}'
     'Box<int>*receiver(Box<int>*p){trace=trace*10+2;return p;}'
     'int index(){trace=trace*10+3;return 0;}'
     'int cpp_test_entry(){int values[1]={0};Box<int>b{values};'
     '(*receiver(&b))[index()]=rhs();if(trace!=123)return 1;return values[0];}'),
    ('unused-reference-result-body',
     'template<class T>struct Box{T*p;T&operator[](int i){return p[i];}};'
     'int cpp_test_entry(){Box<int>*p=nullptr;if(p==nullptr)return 42;return 1;}'),
    ('unused-valid-void-pointer-body',
     'template<class T>struct Box{T*p;T&get(){return*p;}};'
     'int cpp_test_entry(){Box<void>*p=nullptr;if(p==nullptr)return 42;return 1;}'),
)
PROGRAMS = tuple((name, ((name+'.cpp', source),)) for name, source in SINGLE_PROGRAMS) + (
    ('two-tu-reference-subscript-odr', (
        ('helper.cpp', BOX + 'int read(Box<int>*p){return (*p)[0];}'),
        ('entry.cpp', BOX + 'int read(Box<int>*);'
         'int cpp_test_entry(){int values[1]={21};Box<int>b{values};return read(&b)+b[0];}'),
    )),
    ('original-reference-result-prototype', (
        ('original.cpp', 'int& f();'),
        ('entry.cpp', 'int value=42;int&f(){return value;}int cpp_test_entry(){return f();}'),
    )),
)


@pytest.mark.parametrize('name,units', PROGRAMS, ids=[row[0] for row in PROGRAMS])
def test_reference_subscript_originals_compile_deterministically(owned_only, name, units):
    assert compile_host_cpp(units) == compile_host_cpp(units)


def test_upstream_excerpts_remain_exact_source_bytes():
    header = Path(__file__).parents[2] / 'projects/tinyxml2-11.0.0/tinyxml2.h'
    original = header.read_bytes()
    assert SUBSCRIPT_MEMBERS.encode() in original
    assert PEEK_TOP.encode() in original


def test_template_reference_results_have_typed_subscript_patterns_and_no_unused_bodies():
    parser = CppParser()
    tree = parser.parse(BOX)
    owner = parser.scope.names['Box'].dtype
    group = owner.member_scope.names['operator[]']
    assert [method.dtype for method in group.overloads] == [
        ReferenceType(owner.parameters[0]), ReferenceType(ConstType(owner.parameters[0]))]
    for record in owner.methods:
        returned = record.body.children[0].children[0]
        assert returned.lvalue and returned.node.kind == 'subscript'
        assert returned.node.children[0].symbol is owner.member_scope.names['p']
    assert not parser.instance_registry.entries and not tree.ext


def test_specialized_reference_results_keep_abi_address_and_member_mangling():
    parser = CppParser()
    tree = parser.parse(BOX +
                        'int f(Box<int>*p,const Box<int>*q){return (*p)[0]+(*q)[0];}')
    contexts = [entry.context for entry in parser.instance_registry.entries.values()
                if isinstance(entry.context, MethodInstance)]
    assert {context.symbol.c_name for context in contexts} == {'_ZN3BoxIiEixEi', '_ZNK3BoxIiEixEi'}
    assert {context.symbol.dtype for context in contexts} == {
        ReferenceType('int'), ReferenceType(ConstType('int'))}
    for node in tree.ext:
        if isinstance(node, c_ast.FuncDef) and node.decl.name in {c.symbol.c_name for c in contexts}:
            assert isinstance(node.decl.type.type, c_ast.PtrDecl)
            assert node.decl.definition_linkage == 'external_odr'


INVALID = (
    ('operator-data-member', 'template<class T>struct S{int operator[];};', 'cpp.invalid-subscript-operator'),
    ('free-subscript', 'int operator[](int);', 'cpp.invalid-subscript-operator'),
    ('static-subscript', 'struct S{static int operator[](int);};', 'cpp.invalid-subscript-operator'),
    ('zero-parameters', 'struct S{int operator[]();};', 'cpp.invalid-subscript-operator'),
    ('two-parameters', 'struct S{int operator[](int,int);};', 'cpp.invalid-subscript-operator'),
    ('default-parameter', 'struct S{int operator[](int=0);};', 'cpp.invalid-subscript-operator'),
    ('template-static-subscript', 'template<class T>struct S{static T operator[](int);};',
     'cpp.invalid-subscript-operator'),
    ('mutable-reference-to-rvalue', 'int&f(){return 42;}', 'cpp.invalid-reference-binding'),
    ('discarded-const-return', 'int&f(const int*p){return *p;}', 'cpp.invalid-reference-binding'),
    ('const-result-write', BOX+'int f(){int a[1]={0};const Box<int>b{a};b[0]=42;return 0;}',
     'cpp.const-assignment'),
    ('bad-dependent-index', 'template<class T>struct Box{int*p;int get(T i){return p[i];}};'
     'int f(Box<double>*p){return p->get(0.5);}', 'cpp.invalid-subscript'),
    ('bad-nondependent-index', 'template<class T>struct Box{T*p;T get(){return p[0.5];}};',
     'cpp.invalid-subscript'),
    ('void-reference-substitution', 'template<class T>struct Box{T*p;T&get();};'
     'int f(Box<void>*p){return p->get();}', 'cpp.invalid-reference-type'),
)

BOUNDARIES = (
    ('unowned-template-assignment-operator', 'template<class T>struct S{int operator=();};',
     'cpp.unsupported.class-templates'),
    ('unowned-template-allocation-operator', 'template<class T>struct S{int operator new();};',
     'cpp.unsupported.class-templates'),
    ('unowned-template-deallocation-operator', 'template<class T>struct S{int operator delete();};',
     'cpp.unsupported.class-templates'),
    # Exact former assignment original: test_cpp_explicit_assignment.PROGRAMS.
    ('reference-temporary', 'const int&f(){return 42;}', 'cpp.unsupported.reference-temporaries'),
    ('reference-member', 'struct S{int&r;};', 'cpp.unsupported.references'),
    ('reference-parameter-pattern', 'template<class T>struct S{T&f(T&n){return n;}};',
     'cpp.unsupported.references'),
    ('reference-collapsing-substitution', 'using R=int&;template<class T>struct S{T&get();};'
     'int f(S<R>*p){return p->get();}', 'cpp.unsupported.references'),
    ('enum-reference-pattern', 'enum E{A};template<class T>struct S{E&get();};',
     'cpp.unsupported.enum-templates'),
    # allocation-use moved verbatim to test_cpp_heap_allocation's MOVED_ORIGINALS.
)


@pytest.mark.parametrize('name,source,code', INVALID + BOUNDARIES,
                         ids=[row[0] for row in INVALID + BOUNDARIES])
def test_invalid_or_unowned_reference_subscript_stops_before_ir(name, source, code, monkeypatch):
    from pcc.frontends.c.codegen.c_codegen import CCodeGenerator

    def forbidden(*args, **kwargs):
        raise AssertionError('invalid or unowned reference/subscript reached IR generation')

    monkeypatch.setattr(CCodeGenerator, 'generate_code', forbidden)
    with pytest.raises(CppDiagnostic) as error:
        compile_host_cpp(((name+'.cpp', source),))
    assert error.value.code == code
