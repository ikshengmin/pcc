"""Builtin pointer offsets, N4659 [expr.add], [conv.array], [conv.prom].

https://timsong-cpp.github.io/cppwp/n4659/expr.add
https://timsong-cpp.github.io/cppwp/n4659/conv.array
Tests never require a relative evaluation order between additive operands.
"""
import pytest

from pcc.frontends.c.ast import c_ast
from pcc.frontends.c.parse.plyparser import Coord
from pcc.frontends.cpp import CppDiagnostic, CppParser
from pcc.frontends.cpp.parser import Expression
from pcc.frontends.cpp.templates import Pattern, TypeParameter
from pcc.frontends.cpp.types import FunctionType, PointerType
from tests.cpp.owned_execution import compile_host_cpp
from tests.cpp.test_cpp_owned_execution import owned_only

ORIGINAL = 'int f(){int x=1;int *p=&x;p+1;return 0;}'
CASES = (
    ('array-decay-char-offset', 'int cpp_test_entry(){char a[2]={7,42};return int(*(a+1));}'),
    ('integer-plus-pointer', 'int cpp_test_entry(){int a[2]={7,42};return *(1+a);}'),
    ('signed-negative-offset', 'int cpp_test_entry(){int a[2]={42,7};int*p=&a[1];long n=-1;return *(p+n);}'),
    ('subtract-unsigned-offset', 'int cpp_test_entry(){int a[2]={42,7};int*p=&a[1];unsigned char n=1;return *(p-n);}'),
    ('typed-null-zero', 'int cpp_test_entry(){int*p=nullptr;if(p+0!=nullptr)return 1;if(p-0!=nullptr)return 2;if(0+p!=nullptr)return 3;return 42;}'),
    ('bool-enum-promotion', 'enum E{One=1};int cpp_test_entry(){int a[2]={7,21};bool b=true;return *(a+b)+*(a+One);}'),
    ('independent-once-only-operands', 'int pointers=0;int offsets=0;int*base(int*p){pointers=pointers+1;return p;}int offset(){offsets=offsets+1;return 1;}int cpp_test_entry(){int a[2]={7,42};int*p=base(a)+offset();if(pointers!=1||offsets!=1)return 1;return *p;}'),
    ('formal-row-stride', 'int read(int(*p)[3]){return (*(p+1))[0];}int cpp_test_entry(){int a[2][3]={{0,0,7},{42,0,0}};return read(a);}'),
    ('effectful-return-row-stride', 'typedef long long Row[5];int calls=0;Row*pick(Row*p){calls=calls+1;return p;}int cpp_test_entry(){Row a[2]={{0,7},{42}};int n=int((*(pick(a)+1))[0]);if(calls!=1)return 1;return n;}'),
    ('address-and-sizeof-preserve-array', 'int cpp_test_entry(){int a[2]={7,42};if(sizeof(a)!=2*sizeof(int))return 1;int(*p)[2]=&a;if(p+1-1!=p)return 2;return (*p)[1];}'),
    ('one-past-no-dereference', 'int cpp_test_entry(){int a[2]={42,7};int*p=a;int*end=p+2;if(end-2!=p)return 1;return *(end-2);}'),
    ('pointee-const-preserved', 'int choose(const int*p){return *p;}int choose(int*p){return 7;}int cpp_test_entry(){const int a[2]={7,42};return choose(a+1);}'),
    ('unsigned-long-long-width', 'int read(int*p,unsigned long long n){return *(p+n);}int cpp_test_entry(){int a[2]={7,42};return read(a,1ULL);}'),
    ('template-offset-replayed', 'int*get(){return nullptr;}template<class T>int f(T n){get()+n;return 42;}int cpp_test_entry(){return f(0);}'),
    ('subscript-after-additive-base', 'int pointers=0;int offsets=0;int indexes=0;int*base(int*p){pointers=pointers+1;return p;}int offset(){offsets=offsets+1;return 1;}int index(){indexes=indexes+1;if(pointers!=1||offsets!=1)return 1;return 0;}int cpp_test_entry(){int a[3]={7,42,7};int n=(base(a)+offset())[index()];if(pointers!=1||offsets!=1||indexes!=1)return 1;return n;}'),
)
PROGRAMS = tuple((name, ((name+'.cpp',source),)) for name,source in CASES) + (
    ('original-pointer-addition', (('original.cpp', ORIGINAL), ('entry.cpp','int f();int cpp_test_entry(){return f()+42;}'))),
)


def walk(node):
    yield node
    for _, child in node.children():
        yield from walk(child)


@pytest.mark.parametrize('name,units', PROGRAMS, ids=[item[0] for item in PROGRAMS])
def test_pointer_offset_original_artifacts_are_byte_stable(owned_only, name, units):
    assert compile_host_cpp(units) == compile_host_cpp(units)


def test_pointer_offset_capture_preserves_array_pointee_and_wide_unsigned_offset():
    tree = CppParser().parse('int read(int(*p)[3],unsigned long long n){return (*(p+n))[0];}')
    captures = [n for n in walk(tree) if isinstance(n,c_ast.Decl) and n.name.startswith('__pcc_cpp_offset_')]
    assert len(captures) == 2
    pointer, offset = captures
    assert isinstance(pointer.type,c_ast.PtrDecl) and isinstance(pointer.type.type,c_ast.ArrayDecl)
    assert pointer.type.type.dim.value == '3'
    assert isinstance(offset.type,c_ast.TypeDecl) and offset.type.type.names == ['unsigned','long','long']


def test_dependent_offset_remains_pattern_until_replay_not_c_locals():
    parser = CppParser()
    parser.parse('')
    parser.template_function = object()
    coord = Coord('template.cpp',1,1)
    lhs = Expression(Pattern('literal',coord,('int','0','int',0)),PointerType('int'))
    rhs = Expression(Pattern('literal',coord,('int','0','int',0)),TypeParameter(0,'T',coord))
    result = parser._binary_expression('+',lhs,rhs,coord)
    assert isinstance(result.node,Pattern) and result.node.kind == 'binary'
    assert result.node.children == [lhs,rhs] and result.dtype is None


@pytest.mark.parametrize('source,code', (
    ('int f(){int*p=nullptr;int*q=nullptr;p+q;return 0;}', 'cpp.invalid-operands'),
    ('int f(){int*p=nullptr;1-p;return 0;}', 'cpp.invalid-operands'),
    ('int f(){nullptr+0;return 0;}', 'cpp.invalid-operands'),
    ('int f(){int*p=nullptr;p+nullptr;return 0;}', 'cpp.invalid-operands'),
    ('int f(){void*p=nullptr;p+0;return 0;}', 'cpp.invalid-operands'),
    ('int f(){void*p=nullptr;return sizeof(p+0);}', 'cpp.invalid-operands'),
    ('struct S;int f(S*p){p+0;return 0;}', 'cpp.incomplete-class'),
    ('enum E{A};int f(){int*p=nullptr;p+1.0;return 0;}', 'cpp.invalid-operands'),
    ('enum class E{A};int f(){int*p=nullptr;p+E::A;return 0;}', 'cpp.invalid-operands'),
    ('int f(){int*p=nullptr;&(p+0);return 0;}', 'cpp.not-lvalue'),
    ('int f(){int*p=nullptr;(p+0)=p;return 0;}', 'cpp.not-lvalue'),
    ('int value;int*p=&value+0;', 'cpp.unsupported.static-initialization-analysis'),
))
def test_pointer_offset_invalid_or_unmodeled_semantics_fail_closed(source,code):
    with pytest.raises(CppDiagnostic) as error:
        CppParser().parse(source)
    assert error.value.code == code


def test_original_typed_null_difference_now_parses():
    # Preserve the original gap input; full owned/reference execution lives in
    # test_cpp_pointer_difference_execution.py, including its zero result.
    CppParser().parse('int f(){int*p=nullptr;p-p;return 0;}')


def test_original_typed_null_order_now_parses():
    # Exact input retained as a positive owned/reference execution obligation
    # in test_cpp_pointer_order_execution.py.
    CppParser().parse('int f(){int*p=nullptr;p<p;return 0;}')


def test_function_pointee_offset_is_invalid_before_c_projection():
    parser = CppParser()
    parser.parse('')
    coord = Coord('function-offset.cpp',1,1)
    lhs = Expression(c_ast.ID('p',coord),PointerType(FunctionType('int',())))
    rhs = Expression(c_ast.Constant('int','0',coord),'int')
    with pytest.raises(CppDiagnostic) as error:
        parser._binary_expression('+',lhs,rhs,coord)
    assert error.value.code == 'cpp.unsupported.function-pointers'
    # The existing callback-value gate precedes additive Sema. Exercise the
    # same typed operands directly too, without weakening either boundary.
    with pytest.raises(CppDiagnostic) as error:
        parser._pointer_additive_expression('+',lhs,rhs,coord)
    assert error.value.code == 'cpp.invalid-operands'
