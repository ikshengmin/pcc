"""Builtin pointer ordering, N4659 [expr.rel], [conv.ptr], [conv.qual].

https://timsong-cpp.github.io/cppwp/n4659/expr.rel
Only standard-defined comparisons are observed: pointers into one array,
same-access members of one non-union class, equal pointers or two typed nulls.
No ordering among unrelated allocations or native-pcc1 acceptance is implied.
"""

from pathlib import Path

import pytest

from pcc.frontends.c.ast import c_ast
from pcc.frontends.c.parse.plyparser import Coord
from pcc.frontends.cpp import CppDiagnostic, CppParser
from pcc.frontends.cpp.parser import Expression
from pcc.frontends.cpp.templates import Pattern, TypeParameter
from pcc.frontends.cpp.types import FunctionType, PointerType
from tests.cpp.owned_execution import compile_host_cpp
from tests.cpp.test_cpp_owned_execution import owned_only


ORIGINAL_OBJECT = 'int f(){int x=1;int *p=&x;return p<p;}'
ORIGINAL_NULL = 'int f(){int*p=nullptr;p<p;return 0;}'
# TinyXML2 11.0.0, upstream 9148bdf719e997d1f474be6bcc7943881046dba1,
# Zlib license. Exact source lines, with authored surrounding observers.
UPSTREAM_LOOP = '                    while ( p < q ) {\r\n'
UPSTREAM_IF = '        if ( p < q ) {\r\n'
CASES = (
    ('original-object-self-order', ORIGINAL_OBJECT + 'int cpp_test_entry(){return f()+42;}'),
    ('original-typed-null-order', ORIGINAL_NULL + 'int cpp_test_entry(){return f()+42;}'),
    ('all-operators-and-one-past',
     'int cpp_test_entry(){int a[4]{};int*p=a;int*q=a+4;'
     'if(!(p<q)||!(p<=q)||!(q>p)||!(q>=p))return 1;'
     'if(q<p||q<=p||p>q||p>=q)return 2;'
     'if(p<p||p>p||!(p<=p)||!(p>=p))return 3;return 42;}'),
    ('mixed-const-and-void-composite',
     'int cpp_test_entry(){char a[4]{};void*p=a;const char*q=a+4;'
     'if(!(p<q)||!(q>p)||p>=q||q<=p)return 1;return 42;}'),
    ('array-pointee-order',
     'bool before(int(*p)[3],const int(*q)[3]){return p<q;}'
     'int cpp_test_entry(){int a[4][3]{};'
     'if(!before(a,a+4)||before(a+4,a)||before(a,a))return 1;return 42;}'),
    ('pointer-object-qualification',
     'int cpp_test_entry(){int* a[4]{};int**p=a;int*const*q=a+4;'
     'if(!(p<q)||!(q>p)||p>=q||q<=p)return 1;return 42;}'),
    ('same-access-record-members',
     'struct Pair{int first;int second;};int cpp_test_entry(){Pair p{17,25};'
     'if(!(&p.first<&p.second)||!(&p.second>&p.first))return 1;return 42;}'),
    ('incomplete-pointee-equal-nulls',
     'struct Opaque;int cpp_test_entry(){Opaque*p=nullptr;const Opaque*q=nullptr;'
     'if(p<q||p>q||!(p<=q)||!(p>=q))return 1;return 42;}'),
    ('independent-operands-once',
     'int left_calls=0;int right_calls=0;'
     'int*left(int*p){left_calls+=1;return p;}'
     'int*right(int*p){right_calls+=1;return p;}'
     'int cpp_test_entry(){int a[4]{};bool yes=left(a)<right(a+3);'
     'if(!yes||left_calls!=1||right_calls!=1)return 1;return 42;}'),
    ('comparison-result-is-bool',
     'int pick(bool n){if(n)return 42;return 1;}int pick(int n){return 2;}'
     'int cpp_test_entry(){int a[2]{};return pick(a<a+1);}'),
    ('class-template-replay',
     'template<class T>struct Span{T*first;bool before(T*last){return first<last;}};'
     'int cpp_test_entry(){int a[2]{};Span<int>s{a};'
     'if(!s.before(a+2)||s.before(a))return 1;return 42;}'),
    ('tinyxml-original-loop',
     'int cpp_test_entry(){char text[43]{};const char*p=text;const char*q=p+42;'
     'int count=0;' + UPSTREAM_LOOP + '++p;++count;}return count;}'),
    ('tinyxml-original-if',
     'int cpp_test_entry(){char text[43]{};const char*p=text;const char*q=p+42;'
     + UPSTREAM_IF + 'return int(q-p);}return 1;}'),
)
PROGRAMS = tuple((name, ((name + '.cpp', source),)) for name, source in CASES)


def test_tinyxml_ordering_excerpts_are_unmodified():
    source = Path(__file__).resolve().parents[2] / 'projects/tinyxml2-11.0.0/tinyxml2.cpp'
    lines = source.read_bytes().splitlines(keepends=True)
    assert lines[2678] == UPSTREAM_LOOP.encode()
    assert lines[2706] == UPSTREAM_IF.encode()


@pytest.mark.parametrize('name,units', PROGRAMS, ids=[row[0] for row in PROGRAMS])
def test_pointer_order_artifacts_are_owned_and_stable(owned_only, name, units):
    assert compile_host_cpp(units) == compile_host_cpp(units)


INVALID_OPERANDS = (
    ('integer-null', 'int*p', 'p', '0'),
    ('null-pointer-literal', 'int*p', 'p', 'nullptr'),
    ('reversed-null-pointer-literal', 'int*p', 'nullptr', 'p'),
    ('two-null-pointer-literals', '', 'nullptr', 'nullptr'),
    ('different-object-pointees', 'int*p,long*q', 'p', 'q'),
    ('different-pointer-depths', 'int*p,int**q', 'p', 'q'),
)


@pytest.mark.parametrize('op', ('<', '<=', '>', '>='))
@pytest.mark.parametrize('name,parameters,left,right', INVALID_OPERANDS,
                         ids=[row[0] for row in INVALID_OPERANDS])
def test_pointer_order_invalid_operands_fail_before_emission(op, name, parameters, left, right):
    source = 'bool f(' + parameters + '){return ' + left + op + right + ';}'
    with pytest.raises(CppDiagnostic) as error:
        CppParser().parse(source)
    assert error.value.code == 'cpp.invalid-operands'


def test_ordering_does_not_change_equality_null_pointer_conversions():
    CppParser().parse('bool f(int*p){return p==nullptr||p==0||nullptr==p||0==p;}')


def test_function_pointer_ordering_retains_value_capability_gate():
    parser = CppParser()
    parser.parse('')
    coord = Coord('function-order.cpp', 1, 1)
    dtype = PointerType(FunctionType('int', ()))
    left = Expression(c_ast.ID('p', coord), dtype)
    right = Expression(c_ast.ID('q', coord), dtype)
    with pytest.raises(CppDiagnostic) as error:
        parser._binary_expression('<', left, right, coord)
    assert error.value.code == 'cpp.unsupported.function-pointers'


def test_dependent_pointer_order_is_bool_pattern_until_substitution():
    parser = CppParser()
    parser.parse('')
    parser.template_function = object()
    coord = Coord('dependent-order.cpp', 1, 1)
    left = Expression(Pattern('name', coord, 'p'), PointerType('int'))
    right = Expression(Pattern('name', coord, 'q'), TypeParameter(0, 'T', coord))
    result = parser._binary_expression('<', left, right, coord)
    assert isinstance(result.node, Pattern) and result.node.kind == 'binary'
    assert result.node.children == [left, right] and result.dtype == 'bool'
