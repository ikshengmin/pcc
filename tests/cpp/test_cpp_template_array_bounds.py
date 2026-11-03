"""Typed class-template array-bound CI obligations, execution UNRUN.

N4659 [temp.param], [temp.dep.expr], [temp.res], [temp.inst], [dcl.array],
[expr.const], [conv.array]. Bounds retain declaration identities and target
constant proofs. Pointer-to-array members exercise concrete layout/stride via
the existing pointer member owner. Class-element array member lifetime remains gated.
"""

from pathlib import Path

import pytest

from pcc.frontends.c.ast import c_ast
from pcc.frontends.cpp import CppDiagnostic, CppParser
from pcc.frontends.cpp.classes import require_complete
from pcc.frontends.cpp.templates import ArrayBoundPattern, dependent
from pcc.frontends.cpp.types import ArrayType, PointerType, _mangle_type
from tests.cpp.owned_execution import compile_host_cpp
from tests.cpp.test_cpp_owned_execution import owned_only


TARGET = 'x86_64-unknown-linux-gnu'
# Exact unchanged TinyXML2 11.0.0 header line 316, upstream 9148bdf719e997d1f474be6bcc7943881046dba1.
# The surrounding declaration below is an authored observer, not DynArray.
UPSTREAM_FIELD = '    T   _pool[INITIAL_SIZE];\r\n'
FIXED_PRIMARY_ORIGINAL = 'template<class T>struct Box{T value[2];};'
RETAINED_PRIMARIES = (
    ('fixed-primary-original-retained', FIXED_PRIMARY_ORIGINAL),
    ('fixed-primary-method-original-retained', 'template<class T>struct Box{T values[2];T f(){return 42;}};'),
    ('dependent-primary-original-retained', 'template<class T,int N>struct S{T values[N];};'),
)


def test_original_tinyxml_field_retains_typed_value_parameter_identity():
    header = Path(__file__).resolve().parents[2] / 'projects' / 'tinyxml2-11.0.0' / 'tinyxml2.h'
    assert header.read_bytes().splitlines(keepends=True)[315] == UPSTREAM_FIELD.encode()
    parser = CppParser(TARGET)
    parser.parse('using size_t=unsigned long;template<class T,size_t INITIAL_SIZE>struct Buffer{\r\n' +
                 UPSTREAM_FIELD + '};', 'tinyxml-field-observer.cpp')
    owner = parser.scope.names['Buffer'].dtype
    dtype = owner.members[0][1]
    assert isinstance(dtype, ArrayType) and dtype.element is owner.parameters[0]
    assert isinstance(dtype.bound, ArrayBoundPattern) and dependent(dtype)
    expression = dtype.bound.expression
    assert expression.node.kind == 'template-value'
    assert expression.node.value is owner.parameters[1]
    assert expression.symbol.template_parameter is owner.parameters[1]
    assert expression.dtype == 'unsigned long'
    assert expression.node.coord.file == 'tinyxml-field-observer.cpp'


def test_fixed_primary_original_is_valid_without_instantiating_array_lifetime():
    parser = CppParser(TARGET)
    tree = parser.parse(FIXED_PRIMARY_ORIGINAL)
    assert not tree.ext
    dtype = parser.scope.names['Box'].dtype.members[0][1]
    assert isinstance(dtype, ArrayType) and dtype.bound == 2


@pytest.mark.parametrize('name,source', RETAINED_PRIMARIES, ids=[row[0] for row in RETAINED_PRIMARIES])
def test_retained_primary_originals_do_not_require_member_array_lifetime(name, source):
    parser = CppParser(TARGET)
    tree = parser.parse(source, name + '.cpp')
    assert not tree.ext and not parser.instance_registry.entries


def test_substitution_retains_nested_extents_and_shared_layout_projection():
    parser = CppParser(TARGET)
    tree = parser.parse('template<class T,int N>struct Rows{T (*row)[N+1];};'
                        'using A=Rows<int,2>;using B=Rows<int,1+1>;'
                        'int extent(){return sizeof(A);}')
    a, b = parser.scope.names['A'].dtype, parser.scope.names['B'].dtype
    assert a is b and a.complete and a.layout_state == 'complete'
    assert _mangle_type(a, []) == '4RowsIiLi2EE'
    member = a.definition.members[0]
    assert isinstance(member.dtype, PointerType)
    assert isinstance(member.dtype.pointee, ArrayType)
    assert member.dtype.pointee.element == 'int' and member.dtype.pointee.bound == 3
    declarations = [node for node in tree.ext if isinstance(node, c_ast.Decl)
                    and isinstance(node.type, c_ast.Struct) and node.type.decls]
    declaration = next(node for node in declarations if node.type.name == a.c_tag)
    projected = declaration.type.decls[0].type
    assert isinstance(projected, c_ast.PtrDecl)
    assert isinstance(projected.type, c_ast.ArrayDecl) and projected.type.dim.value == '3'


def test_invalid_dependent_bound_is_lazy_and_failure_is_sticky():
    parser = CppParser(TARGET)
    parser.parse('template<class T,int N>struct Rows{T (*row)[N];};'
                 'using Bad=Rows<int,0>;Bad*pointer=nullptr;')
    dtype = parser.scope.names['Bad'].dtype
    assert not dtype.complete and dtype.layout_state == 'incomplete'
    with pytest.raises(CppDiagnostic) as first:
        require_complete(dtype, dtype.coord)
    assert first.value.code == 'cpp.invalid-array-bound'
    assert dtype.layout_state == 'failed' and dtype.layout_failure is first.value
    with pytest.raises(CppDiagnostic) as repeated:
        require_complete(dtype, dtype.coord)
    assert repeated.value is first.value


def test_each_nested_extent_substitutes_its_own_mixed_list_ordinal():
    parser = CppParser(TARGET)
    parser.parse('template<class T,int N,int M>struct Rows{T (*row)[N][M];};'
                 'using A=Rows<int,2,3>;int f(){return sizeof(A);}')
    dtype = parser.scope.names['A'].dtype.definition.members[0].dtype
    assert isinstance(dtype, PointerType) and dtype.pointee.bound == 2
    assert dtype.pointee.element.bound == 3 and dtype.pointee.element.element == 'int'


INVALID = (
    ('zero', 'template<int N>struct Rows{int (*row)[N];};int f(){return sizeof(Rows<0>);}', 'cpp.invalid-array-bound'),
    ('negative', 'template<int N>struct Rows{int (*row)[N];};int f(){return sizeof(Rows<-1>);}', 'cpp.invalid-array-bound'),
    ('signed-overflow', 'template<int N>struct Rows{int (*row)[N+1];};int f(){return sizeof(Rows<2147483647>);}', 'cpp.nonconstant-array-bound'),
    ('division-zero', 'template<int N>struct Rows{int (*row)[1/N];};int f(){return sizeof(Rows<0>);}', 'cpp.nonconstant-array-bound'),
    ('void-element', 'template<class T,int N>struct Rows{T (*row)[N];};int f(){return sizeof(Rows<void,2>);}', 'cpp.invalid-array-element'),
    ('reference-element', 'using R=int&;template<class T,int N>struct Rows{T (*row)[N];};int f(){return sizeof(Rows<R,2>);}', 'cpp.invalid-array-element'),
    ('unknown-member-bound', 'template<class T>struct Rows{T row[];};int f(){return sizeof(Rows<int>);}', 'cpp.invalid-array-bound'),
    ('nonintegral-dependent-expression', 'template<int N>struct Rows{int (*row)[N+0.5];};', 'cpp.invalid-array-bound'),
)


@pytest.mark.parametrize('name,source,code', INVALID, ids=[row[0] for row in INVALID])
def test_invalid_array_bounds_fail_before_c_projection(name, source, code):
    with pytest.raises(CppDiagnostic) as error:
        CppParser(TARGET).parse(source, name + '.cpp')
    assert error.value.code == code and error.value.coord.file == name + '.cpp'


def test_direct_scalar_array_member_uses_the_shared_layout_owner():
    # Keep the exact formerly rejected original; the separate scalar-array
    # native/reference corpus executes f and validates its observed result.
    parser = CppParser(TARGET)
    parser.parse('template<class T,int N>struct Buffer{T data[N];};'
                 'int f(){return sizeof(Buffer<int,2>);}')
    instance = next(entry.value for entry in parser.instance_registry.entries.values()
                    if getattr(entry.value, 'template_origin', None) is not None)
    assert instance.complete and instance.definition.members[0].dtype == ArrayType('int', 2)


CASES = (
    ('dependent-row-stride', 'template<class T,int N>struct Rows{T (*row)[N];};int cpp_test_entry(){int data[2][3]={{0,17,0},{0,0,25}};Rows<int,3>b{data};return b.row[0][1]+b.row[1][2];}'),
    ('const-element', 'template<class T,unsigned long N>struct Rows{const T (*row)[N];};int cpp_test_entry(){const int data[1][2]={{17,25}};Rows<int,2>b{data};return b.row[0][0]+b.row[0][1];}'),
    ('definition-point-bound-constant', 'const int step=1;template<class T,int N>struct Rows{T (*row)[N+step];};int cpp_test_entry(){const int step=100;int data[1][3]={{0,0,42}};Rows<int,2>b{data};return b.row[0][2];}'),
    ('dependent-sizeof-bound', 'template<class T,int N>struct Rows{T (*row)[N+sizeof(T)/sizeof(T)];};int cpp_test_entry(){int data[1][3]={{0,0,42}};Rows<int,2>b{data};return b.row[0][2];}'),
    ('mixed-parameter-type', 'template<class T,T N>struct Rows{T (*row)[N];};int cpp_test_entry(){unsigned long data[1][2]={{17,25}};Rows<unsigned long,2>b{data};return int(b.row[0][0]+b.row[0][1]);}'),
    ('nested-array-extents', 'template<class T,int N,int M>struct Rows{T (*row)[N][M];};int cpp_test_entry(){int data[2][2][3]={{{0,17,0},{0,0,0}},{{0,0,0},{0,0,25}}};Rows<int,2,3>b{data};return b.row[0][0][1]+b.row[1][1][2];}'),
    ('pointer-only-zero-defers-layout', 'template<class T,int N>struct Rows{T data[N];};int cpp_test_entry(){Rows<int,0>*p=nullptr;if(p==nullptr)return 42;return 1;}'),
)
PROGRAMS = (tuple((name, ((name + '.cpp', source),)) for name, source in CASES) +
            tuple((name, (('original.cpp', source), ('entry.cpp', 'int cpp_test_entry(){return 42;}')))
                  for name, source in RETAINED_PRIMARIES) + (
    ('cross-tu-bound-and-nominal-abi', (
        ('provider.cpp', 'namespace N{template<class T,int K>struct Rows{T (*row)[K];};}int read(N::Rows<int,3>*p){return p->row[0][1]+p->row[1][2];}'),
        ('entry.cpp', 'namespace N{template<class U,int M>struct Rows{U (*row)[M];};}int read(N::Rows<int,3>*);int cpp_test_entry(){int data[2][3]={{0,17,0},{0,0,25}};N::Rows<int,3>b{data};return read(&b);}'),
    )),
))


@pytest.mark.parametrize('name,units', PROGRAMS, ids=[row[0] for row in PROGRAMS])
def test_template_array_bound_owned_artifacts_are_byte_stable(owned_only, name, units):
    assert compile_host_cpp(units) == compile_host_cpp(units)
