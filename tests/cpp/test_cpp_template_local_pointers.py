"""Typed class-template body-local pointer/cv obligations, execution UNRUN.

N4659 [dcl.ptr], [dcl.type.cv], [dcl.init], [stmt.dcl], [temp.res], [temp.inst].
Locals retain declaration identity and substitute through existing Pattern
replay. No function-template pointer deduction, local class/array/reference
lifetime, callback or allocation capability is implied.
"""

from pathlib import Path

import pytest

from pcc.frontends.c.ast import c_ast
from pcc.frontends.cpp import CppDiagnostic, CppParser
from pcc.frontends.cpp.types import ConstType, PointerType
from tests.cpp.owned_execution import compile_host_cpp
from tests.cpp.test_cpp_owned_execution import owned_only


TARGET = 'x86_64-unknown-linux-gnu'
# Exact TinyXML2 11.0.0 local declarations at tinyxml2.h:233 and :303,
# upstream 9148bdf719e997d1f474be6bcc7943881046dba1, Zlib. The surrounding
# observers below are authored and do not replace PushArr/EnsureCapacity.
UPSTREAM_POINTER = '        T* ret = &_mem[_size];\r\n'
UPSTREAM_CONST = '            const size_t newAllocated = cap * 2;\r\n'
OBSERVER = ('using size_t=unsigned long;template<class T>struct Buffer{T*_mem;size_t _size;'
            'T*reserve(size_t count){' + UPSTREAM_POINTER + '_size+=count;return ret;}'
            'size_t growth(size_t cap){' + UPSTREAM_CONST + 'return newAllocated;}};')


def test_exact_tinyxml_local_declarations_keep_typed_definition_point_symbols():
    header = Path(__file__).resolve().parents[2] / 'projects' / 'tinyxml2-11.0.0' / 'tinyxml2.h'
    lines = header.read_bytes().splitlines(keepends=True)
    assert lines[232] == UPSTREAM_POINTER.encode()
    assert lines[302] == UPSTREAM_CONST.encode()
    parser = CppParser(TARGET)
    tree = parser.parse(OBSERVER, 'local-pattern-observer.cpp')
    owner = parser.scope.names['Buffer'].dtype
    declaration = owner.methods[0].body.children[0]
    symbol, direct = declaration.value
    assert declaration.kind == 'decl' and not direct
    assert isinstance(symbol.dtype, PointerType) and symbol.dtype.pointee is owner.parameters[0]
    returned = owner.methods[0].body.children[-1].children[0]
    assert returned.node.kind == 'name' and returned.node.value is symbol
    const = owner.methods[1].body.children[0].value[0]
    assert const.dtype == ConstType('unsigned long')
    assert not any(isinstance(node, c_ast.FuncDef) for node in tree.ext)
    assert not parser.instance_registry.entries


def test_concrete_pointer_local_projection_preserves_top_and_pointee_const():
    parser = CppParser(TARGET)
    tree = parser.parse('template<class T>struct S{T*p;T read(){T*const local=p;return*local;}'
                        'T read_const(){const T*local=p;return*local;}};'
                        'int run(){int n=42;S<int>s{&n};return s.read()+s.read_const();}')
    definitions = {node.decl.name: node for node in tree.ext if isinstance(node, c_ast.FuncDef)}
    first = definitions['_ZN1SIiE4readEv'].body.block_items[0]
    second = definitions['_ZN1SIiE10read_constEv'].body.block_items[0]
    assert isinstance(first.type, c_ast.PtrDecl) and first.type.quals == ['const']
    assert first.type.type.quals == []
    assert isinstance(second.type, c_ast.PtrDecl) and second.type.quals == []
    assert second.type.type.quals == ['const']


INVALID = (
    ('const-local-assignment', 'template<class T>struct S{int f(){const int local=1;local=2;return local;}};int g(S<int>*p){return p->f();}', 'cpp.const-assignment'),
    ('const-pointee-assignment', 'template<class T>struct S{T*p;int f(){const T*local=p;*local=1;return 0;}};int g(S<int>*p){return p->f();}', 'cpp.const-assignment'),
    ('const-pointer-reassignment', 'template<class T>struct S{T*p;int f(){T*const local=p;local=p;return 0;}};int g(S<int>*p){return p->f();}', 'cpp.const-assignment'),
    ('uninitialized-dependent-const-scalar', 'template<class T>struct S{int f(){const T local;return 0;}};int g(S<int>*p){return p->f();}', 'cpp.const-initializer'),
    ('uninitialized-dependent-const-pointer', 'template<class T>struct S{int f(){T*const local;return 0;}};int g(S<int>*p){return p->f();}', 'cpp.const-initializer'),
    ('non-null-integer-to-pointer', 'template<class T>struct S{int f(){T*local=1;return 0;}};int g(S<int>*p){return p->f();}', 'cpp.invalid-conversion'),
    ('void-pointee-instantiation', 'template<class T>struct S{T*p;int f(){T*local=p;return*local;}};int g(S<void>*p){return p->f();}', 'cpp.invalid-dereference'),
)
GAPS = (
    ('function-template-pointer-local', 'template<class T>T f(T value){T*local=&value;return*local;}', 'cpp.unsupported.pointer-templates'),
    ('function-template-cv-local', 'template<class T>T f(T value){const T local=value;return local;}', 'cpp.unsupported.pointer-templates'),
    ('member-reference-local', 'template<class T>struct S{T*p;T f(){T&local=*p;return local;}};', 'cpp.unsupported.references'),
    ('member-array-local', 'template<class T>struct S{int f(){int local[2]{};return local[0];}};', 'cpp.unsupported.array-templates'),
    ('dependent-class-local', 'struct Tag{Tag(){}};template<class T>struct S{int f(){const T local;return 42;}};int g(S<Tag>*p){return p->f();}', 'cpp.unsupported.member-templates'),
    ('callback-local', 'using Callback=int(int);template<class T>struct S{int f(){Callback*local=nullptr;return 0;}};', 'cpp.unsupported.function-pointers'),
    ('volatile-local', 'template<class T>struct S{int f(){volatile int local=0;return local;}};', 'cpp.unsupported.volatile'),
    ('allocating-new-remains-unowned', 'using size_t=unsigned long;template<class T>struct S{T*f(size_t cap){const size_t newAllocated=cap*2;T*newMem=new T[newAllocated];return newMem;}};', 'cpp.unsupported.allocation-templates'),
)


@pytest.mark.parametrize('name,source,code', INVALID + GAPS, ids=[row[0] for row in INVALID + GAPS])
def test_local_pointer_and_cv_constraints_keep_exact_failures(name, source, code):
    with pytest.raises(CppDiagnostic) as error:
        CppParser(TARGET).parse(source, name + '.cpp')
    assert error.value.code == code


CASES = (
    ('exact-local-declaration-excerpts', OBSERVER + 'int cpp_test_entry(){int data[3]={17,25,0};Buffer<int>b{data,0};int*first=b.reserve(2);int*second=b.reserve(1);if(first!=&data[0]||second!=&data[2]||b._size!=3||b.growth(21)!=42)return 1;return first[0]+first[1];}'),
    ('local-pointer-mutation', 'template<class T>struct S{T*p;T f(){T*local=p;*local=42;return*local;}};int cpp_test_entry(){int n=0;S<int>s{&n};return s.f();}'),
    ('top-const-pointer-keeps-mutable-pointee', 'template<class T>struct S{T*p;T f(){T*const local=p;*local=42;return*local;}};int cpp_test_entry(){int n=0;S<int>s{&n};return s.f();}'),
    ('const-pointee-read', 'template<class T>struct S{T*p;T f()const{const T*local=p;return*local;}};int cpp_test_entry(){int n=42;const S<int>s{&n};return s.f();}'),
    ('const-dependent-scalar-local', 'template<class T>struct S{T*p;T f(){const T local=*p;return local;}};int cpp_test_entry(){int n=42;S<int>s{&n};return s.f();}'),
    ('local-pointer-reassignment', 'template<class T>struct S{T*p;T f(){T*local=nullptr;local=p;return*local;}};int cpp_test_entry(){int n=42;S<int>s{&n};return s.f();}'),
    ('direct-initialized-pointer-local', 'template<class T>struct S{T*p;T f(){T*local(p);return*local;}};int cpp_test_entry(){int n=42;S<int>s{&n};return s.f();}'),
    ('substituted-const-element', 'template<class T>struct S{T*p;T f(){T*local=p;return*local;}};int cpp_test_entry(){const int n=42;S<const int>s{&n};return s.f();}'),
    ('shadowed-local-declaration-identity', 'template<class T>struct S{T*p;T f(){T*local=p;{T*local=p+1;*local=25;}return*local+p[1];}};int cpp_test_entry(){int data[2]={17,0};S<int>s{data};return s.f();}'),
    ('subscript-address-evaluated-once', 'template<class T>struct S{T*p;int f(int i){T*local=&p[i++];if(i!=1)return 1;return int(*local);}};int cpp_test_entry(){int data[2]={42,1};S<int>s{data};return s.f(0);}'),
    ('injected-self-pointer-local', 'template<class T>struct S{T value;T f(){S*local=this;return local->value;}};int cpp_test_entry(){S<int>s{42};return s.f();}'),
    ('static-member-pointer-local', 'template<class T>struct S{static T f(T value){T*local=&value;return*local;}};int cpp_test_entry(){return S<int>::f(42);}'),
    ('full-width-local-value', 'template<class T>struct S{T*p;T f(){const T*local=p;return local[0]+local[1];}};int cpp_test_entry(){unsigned long long data[2]={4294967296ULL,8589934592ULL};S<unsigned long long>s{data};if(s.f()!=12884901888ULL)return 1;return 42;}'),
    ('unused-invalid-pointer-body-remains-lazy', 'template<class T>struct S{T*p;int bad(){T*local=p;return*local;}int get(){return 42;}};int cpp_test_entry(){S<void>s{nullptr};return s.get();}'),
)
PROGRAMS = tuple((name, ((name + '.cpp', source),)) for name, source in CASES) + (
    ('cross-tu-local-pointer-member-odr', (
        ('provider.cpp', 'template<class T>struct S{T*p;T get(){T*local=p;return*local;}};int read(S<int>*p){return p->get();}'),
        ('entry.cpp', 'template<class U>struct S{U*p;U get(){U*local=p;return*local;}};int read(S<int>*);int cpp_test_entry(){int n=21;S<int>s{&n};return read(&s)+s.get();}'),
    )),
)


@pytest.mark.parametrize('name,units', PROGRAMS, ids=[row[0] for row in PROGRAMS])
def test_template_local_pointer_owned_artifacts_are_byte_stable(owned_only, name, units):
    assert compile_host_cpp(units) == compile_host_cpp(units)
