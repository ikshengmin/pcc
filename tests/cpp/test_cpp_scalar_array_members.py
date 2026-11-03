"""Scalar/pointer array member CI obligations, execution UNRUN.

N4659 [dcl.array], [dcl.init], [dcl.init.aggr], [class.base.init],
[class.ctor], [class.dtor], [class.copy.assign], [conv.array], [temp.inst].
No indeterminate scalar is read: no-store default initialization is inspected
as a plan, while native observations read only explicitly initialized elements.
Class-element arrays, array DMIs and copy/move synthesis remain separate gaps.
"""

from pathlib import Path

import pytest

from pcc.frontends.c.ast import c_ast
from pcc.frontends.cpp import CppDiagnostic, CppParser
from pcc.frontends.cpp.classes import default_initialize, require_complete
from pcc.frontends.cpp.member_initialization import requires_destruction
from pcc.frontends.cpp.size_queries import object_layout, sizeof_type
from pcc.frontends.cpp.types import ArrayType, ConstType, PointerType, ReferenceType
from tests.cpp.owned_execution import compile_host_cpp
from tests.cpp.test_cpp_owned_execution import owned_only


TARGET = 'x86_64-unknown-linux-gnu'
# Exact TinyXML2 11.0.0 constructor/header-field excerpts, lines 206-211 and
# 315-318, upstream 9148bdf719e997d1f474be6bcc7943881046dba1 (Zlib).
# The observer omits other original members, including allocating D1/new/delete;
# it does not establish unchanged DynArray or the complete project.
UPSTREAM_CTOR = ('    DynArray() :\r\n'
                 '        _mem( _pool ),\r\n'
                 '        _allocated( INITIAL_SIZE ),\r\n'
                 '        _size( 0 )\r\n'
                 '    {\r\n'
                 '    }\r\n')
UPSTREAM_FIELDS = ('    T*  _mem;\r\n'
                   '    T   _pool[INITIAL_SIZE];\r\n'
                   '    size_t _allocated;\t\t// objects allocated\r\n'
                   '    size_t _size;\t\t\t// number objects in use\r\n')
BUFFER = ('using size_t=unsigned long;template<class T,size_t INITIAL_SIZE>class DynArray{public:' +
          UPSTREAM_CTOR +
          'T*data(){return _mem;}T at(size_t i)const{return _pool[i];}'
          'size_t capacity()const{return _allocated;}size_t bytes()const{return sizeof(_pool);}'
          'private:' + UPSTREAM_FIELDS + '};')
FIXED_ORIGINAL = 'struct S{int a[2];};'
DEFAULTED_ORIGINAL = 'struct S{S()=default;int a[2];};'
SIZEOF_ORIGINAL = 'struct S{int a[3];};int cpp_test_entry(){return int(sizeof(S::a))+30;}'
TEMPLATE_ORIGINAL = ('template<class T,int N>struct Buffer{T data[N];};'
                     'int f(){return sizeof(Buffer<int,2>);}')


def descendants(node):
    yield node
    for _, child in node.children():
        yield from descendants(child)


def test_tinyxml_constructor_and_storage_excerpts_are_byte_exact():
    header = Path(__file__).resolve().parents[2] / 'projects' / 'tinyxml2-11.0.0' / 'tinyxml2.h'
    lines = header.read_bytes().splitlines(keepends=True)
    assert b''.join(lines[205:211]) == UPSTREAM_CTOR.encode()
    assert b''.join(lines[314:318]) == UPSTREAM_FIELDS.encode()
    parser = CppParser(TARGET)
    parser.parse(BUFFER)
    owner = parser.scope.names['DynArray'].dtype
    assert isinstance(owner.member_symbols[1].dtype, ArrayType)
    assert owner.constructors.overloads[0].member_pattern.initializers


@pytest.mark.parametrize('target,size,alignment', (
    ('x86_64-unknown-linux-gnu', 32, 8),
    ('aarch64-apple-darwin', 32, 8),
    ('x86_64-pc-windows-msvc', 16, 4),
    ('x86_64-pc-windows-gnu', 16, 4),
))
def test_array_members_reuse_the_shared_target_record_layout(target, size, alignment):
    parser = CppParser(target)
    tree = parser.parse('struct S{char first;long data[2];char last;};')
    owner = parser.scope.names['S'].dtype
    layout = object_layout(owner, parser.types, owner.coord)
    assert (layout.size, layout.alignment) == (size, alignment)
    assert owner.definition.members[1].dtype == ArrayType('long', 2)
    declaration = next(node for node in tree.ext if isinstance(node, c_ast.Decl)
                       and isinstance(node.type, c_ast.Struct) and node.type.decls)
    assert isinstance(declaration.type.decls[1].type, c_ast.ArrayDecl)
    assert declaration.type.decls[1].type.dim.value == '2'


def test_default_initialization_has_no_array_stores_and_value_initialization_has_bounded_loops():
    parser = CppParser(TARGET)
    tree = parser.parse('struct Default{int data[2][3];Default();};Default::Default(){}'
                        'struct Value{int data[2][3];Value();};Value::Value():data(){}')
    bodies = {node.decl.name: node.body for node in tree.ext if isinstance(node, c_ast.FuncDef)}
    default = parser.scope.names['Default'].dtype.definition.constructors.overloads[0]
    value = parser.scope.names['Value'].dtype.definition.constructors.overloads[0]
    assert not bodies[default.c_name].block_items
    nodes = tuple(descendants(bodies[value.c_name]))
    assert sum(isinstance(node, c_ast.For) for node in nodes) == 2
    assert sum(isinstance(node, c_ast.Assignment) for node in nodes) == 1
    assert not any(isinstance(node, c_ast.FuncCall) for node in nodes)
    assert default.closed_construction_body and value.closed_construction_body


def test_scalar_array_const_deletion_and_destruction_are_element_aware():
    parser = CppParser(TARGET)
    parser.parse('struct S{const int data[2];};struct P{int*data[2];};')
    const = parser.scope.names['S'].dtype.definition
    pointer = parser.scope.names['P'].dtype.definition
    assert const.default_constructor_deleted and const.copy_assignment_deleted
    assert not pointer.default_constructor_deleted and not pointer.copy_assignment_deleted
    assert const.members[0].dtype == ArrayType(ConstType('int'), 2)
    assert not requires_destruction(const.members[0].dtype)
    assert not requires_destruction(pointer.members[0].dtype)


def test_physical_array_layout_overflow_stays_failed_for_repeated_completeness_and_construction():
    parser = CppParser(TARGET)
    parser.parse('template<class T,unsigned long N>struct Huge{T data[N];};'
                 'using H=Huge<int,4611686018427387904ULL>;H*pointer=nullptr;')
    owner = parser.scope.names['H'].dtype
    # Merely retaining pointer/reference type identity does not request layout.
    pointer, reference = PointerType(owner), ReferenceType(owner)
    assert owner.layout_state == 'incomplete' and not owner.complete
    with pytest.raises(CppDiagnostic) as first:
        require_complete(owner, owner.coord)
    assert first.value.code == 'cpp.sizeof-overflow'
    assert owner.layout_state == 'failed' and owner.layout_failure is first.value
    assert not owner.complete and not owner.definition.complete
    operations = (
        lambda: require_complete(owner, owner.coord),
        lambda: default_initialize(owner, owner.coord),
        lambda: sizeof_type(owner, parser.types, owner.coord),
        lambda: default_initialize(owner, owner.coord),
    )
    for operation in operations:
        with pytest.raises(CppDiagnostic) as repeated:
            operation()
        assert repeated.value is first.value
        assert not owner.complete and owner.layout_state == 'failed'
    assert pointer.pointee is owner and reference.referent is owner


INVALID = (
    ('const-owner-mutation', 'struct S{int data[2];};int f(){const S s{};s.data[0]=1;return 0;}', 'cpp.const-assignment'),
    ('deleted-const-array-default', 'struct S{const int data[2];};int f(){S s;return 0;}', 'cpp.deleted-default-constructor'),
    ('deleted-defaulted-const-array', 'struct S{S()=default;const int data[2];};int f(){S s;return 0;}', 'cpp.deleted-default-constructor'),
    ('mutable-const-array', 'struct S{mutable const int data[2];};', 'cpp.invalid-mutable'),
    ('nonempty-parenthesized-member', 'struct S{int data[2];S():data(1){}};', 'cpp.invalid-array-initializer'),
    ('array-member-assignment', 'struct S{int data[2];};int f(){S s{};int x[2]{};s.data=x;return 0;}', 'cpp.array-not-assignable'),
    ('array-member-narrowing', 'struct S{int data[2];};int f(){S s{{1.5}};return 0;}', 'cpp.narrowing-conversion'),
    ('array-member-excess', 'struct S{int data[2];};int f(){S s{{1,2,3}};return 0;}', 'cpp.too-many-initializers'),
    ('unknown-member-bound', 'struct S{int data[];};int f(){return sizeof(S);}', 'cpp.invalid-array-bound'),
    ('overflowing-member-storage', 'struct S{int data[4611686018427387904ULL];};int f(){return sizeof(S);}', 'cpp.sizeof-overflow'),
    ('overflowing-template-construction', 'template<class T,unsigned long N>struct Huge{T data[N];};using H=Huge<int,4611686018427387904ULL>;int first(){H a;return 0;}int second(){H b;return 0;}', 'cpp.sizeof-overflow'),
)
GAPS = (
    ('class-array-lifetime', 'struct E{E();~E();};struct S{E data[2];};', 'cpp.unsupported.class-array-lifetime'),
    ('class-array-alias-lifetime', 'struct E{E();~E();};using A=E[2];struct S{A data;};', 'cpp.unsupported.class-array-members'),
    ('braced-constructor-array', 'struct S{int data[2];S():data{17,25}{}};', 'cpp.unsupported.constructor-list-initialization'),
    ('string-constructor-array', 'struct S{char data[2];S():data("x"){}};', 'cpp.unsupported.string-array-initialization'),
    ('array-dmi', 'struct S{int data[2]={17,25};};', 'cpp.unsupported.default-member-initializer-path'),
    ('aggregate-array-brace-elision', 'struct S{int data[2];};int f(){S s{17,25};return s.data[0];}', 'cpp.unsupported.aggregate-brace-elision'),
    ('class-copy-assignment', 'struct S{int data[2];};int f(){S a{},b{};a=b;return 0;}', 'cpp.unsupported.class-assignment'),
    ('function-template-array-decay', 'template<class T>T id(T x){return x;}int f(){int a[2]{};id(a);return 0;}', 'cpp.unsupported.array-templates'),
)


@pytest.mark.parametrize('name,source,code', INVALID + GAPS, ids=[row[0] for row in INVALID + GAPS])
def test_scalar_array_member_invalid_inputs_and_remaining_gaps(name, source, code):
    with pytest.raises(CppDiagnostic) as error:
        CppParser(TARGET).parse(source, name + '.cpp')
    assert error.value.code == code


CASES = (
    ('explicit-array-subaggregate', 'struct S{char first;int data[2];char last;};int cpp_test_entry(){S s{1,{17,25},2};return s.data[0]+s.data[1];}'),
    ('omitted-array-subaggregate', 'struct S{int first;int data[2];int last;};int cpp_test_entry(){S s{0};return s.data[0]+s.data[1]+s.last+42;}'),
    ('empty-array-subaggregate', 'struct S{int data[2];};int cpp_test_entry(){S s{};return s.data[0]+s.data[1]+42;}'),
    ('nested-array-member', 'struct S{int data[2][2];};int cpp_test_entry(){S s{{{0,17},{0,25}}};return s.data[0][1]+s.data[1][1];}'),
    ('pointer-array-member', 'struct S{int*data[2];};int cpp_test_entry(){int n=42;S s{{nullptr,&n}};if(s.data[0]!=nullptr)return 1;return *s.data[1];}'),
    ('const-array-aggregate', 'struct S{const int data[2];};int cpp_test_entry(){S s{{17,25}};return s.data[0]+s.data[1];}'),
    ('mutable-array-const-owner', 'struct S{mutable int data[1];};int cpp_test_entry(){const S s{{0}};s.data[0]=42;return s.data[0];}'),
    ('constructor-array-value-init', 'struct S{int data[2];S():data(){}};int cpp_test_entry(){S s;if(s.data[1]!=0)return 1;s.data[0]=42;return s.data[0];}'),
    ('constructor-pointer-array-zero', 'struct S{int*data[2];S():data(){}};int cpp_test_entry(){S s;if(s.data[0]!=nullptr||s.data[1]!=nullptr)return 1;return 42;}'),
    ('constructor-const-array-zero', 'struct S{const int data[2];S():data(){}};int cpp_test_entry(){S s;return s.data[0]+s.data[1]+42;}'),
    ('constructor-nested-array-zero', 'struct S{int data[2][3];S():data(){}};int cpp_test_entry(){S s;if(s.data[0][0]!=0||s.data[1][2]!=0)return 1;s.data[1][2]=42;return s.data[1][2];}'),
    ('repeated-array-value-construction', 'struct S{int data[2];S():data(){}};int cpp_test_entry(){for(int i=0;i<2;++i){S s;if(s.data[0]!=0||s.data[1]!=0)return 1;s.data[0]=17;s.data[1]=25;if(s.data[0]+s.data[1]!=42)return 1;}return 42;}'),
    ('constructor-omission-then-assignment', 'struct S{int data[2];S(){}};int cpp_test_entry(){S s;s.data[0]=17;s.data[1]=25;return s.data[0]+s.data[1];}'),
    ('defaulted-array-aggregate', 'struct S{S()=default;int data[2];};int cpp_test_entry(){S s{{17,25}};return s.data[0]+s.data[1];}'),
    ('defaulted-deleted-array-aggregate-bypass', 'struct S{S()=default;const int data[2];};int cpp_test_entry(){S s{{17,25}};return s.data[0]+s.data[1];}'),
    ('scalar-array-destructor', 'int observed=0;struct S{int data[2];S():data(){data[0]=17;data[1]=25;}~S(){observed=data[0]+data[1];}};int work(){S s;return 0;}int cpp_test_entry(){work();return observed;}'),
    ('template-array-constructor-and-decay', BUFFER + 'int cpp_test_entry(){DynArray<int,3>b;b.data()[0]=17;b.data()[2]=25;if(b.capacity()!=3||b.bytes()!=3*sizeof(int))return 1;return b.at(0)+b.at(2);}'),
    ('template-pointer-array-zero', 'template<class T,int N>struct S{T data[N];S():data(){}T get(int i){return data[i];}};int cpp_test_entry(){S<int*,2>s;if(s.get(0)!=nullptr||s.get(1)!=nullptr)return 1;return 42;}'),
    ('record-array-with-scalar-array-member', 'struct S{int data[2];};int cpp_test_entry(){S s[2];s[0].data[0]=17;s[1].data[1]=25;return s[0].data[0]+s[1].data[1];}'),
    ('array-member-record-layout', 'struct S{char first;long data[2];char last;};int cpp_test_entry(){S s{1,{17,25},2};if(sizeof(S)!=32)return 1;return int(s.data[0]+s.data[1]);}'),
)
PROGRAMS = tuple((name, ((name + '.cpp', source),)) for name, source in CASES) + (
    ('fixed-member-original-retained', (('original.cpp', FIXED_ORIGINAL), ('entry.cpp', 'int cpp_test_entry(){return 42;}'))),
    ('defaulted-member-original-retained', (('original.cpp', DEFAULTED_ORIGINAL), ('entry.cpp', 'int cpp_test_entry(){return 42;}'))),
    ('sizeof-member-original-retained', (('original.cpp', SIZEOF_ORIGINAL),)),
    ('template-member-original-retained', (('original.cpp', TEMPLATE_ORIGINAL), ('entry.cpp', 'int f();int cpp_test_entry(){return f()+34;}'))),
    ('cross-tu-dependent-array-layout', (
        ('provider.cpp', 'namespace N{template<class T,int K>struct S{char first;T data[K];char last;};}int read(N::S<int,2>*p){return p->data[0]+p->data[1];}'),
        ('entry.cpp', 'namespace N{template<class U,int M>struct S{char first;U data[M];char last;};}int read(N::S<int,2>*);int cpp_test_entry(){N::S<int,2>s{1,{17,25},2};return read(&s);}'),
    )),
)


@pytest.mark.parametrize('name,units', PROGRAMS, ids=[row[0] for row in PROGRAMS])
def test_scalar_array_member_owned_artifacts_are_byte_stable(owned_only, name, units):
    assert compile_host_cpp(units) == compile_host_cpp(units)


PLACEMENT_DECLARATION = 'using size_type=unsigned long;void* operator new(size_type,void*) noexcept;'
PLACEMENT_CASES = (
    ('defaulted-array-value-init-with-padding', 'struct S{S()=default;char first;int data[2];char last;};int cpp_test_entry(){long long raw[4];void*storage=raw;S*p=new(storage)S();unsigned char*bytes=static_cast<unsigned char*>(storage);for(size_type i=0;i<sizeof(S);++i){if(bytes[i]!=0)return 1;}p->data[0]=17;p->data[1]=25;p->~S();return 42;}'),
    ('array-explicit-destruction', 'int observed=0;struct S{int data[2];S():data(){data[0]=17;data[1]=25;}~S(){observed=data[0]+data[1];}};int cpp_test_entry(){long long raw[4];S*p=new(raw)S;p->~S();return observed;}'),
    ('repeated-defaulted-array-value-construction', 'struct S{S()=default;int data[2];};int cpp_test_entry(){long long raw[4];void*storage=raw;S*p=new(storage)S();p->data[0]=17;p->data[1]=25;p->~S();p=new(storage)S();if(p->data[0]!=0||p->data[1]!=0)return 1;p->data[0]=42;return p->data[0];}'),
)
PLACEMENT_PROGRAMS = tuple((name, ((name + '.cpp', PLACEMENT_DECLARATION + source),))
                           for name, source in PLACEMENT_CASES)
