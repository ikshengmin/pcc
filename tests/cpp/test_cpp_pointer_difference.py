"""Builtin pointer subtraction, N4659 [expr.add]/2.2, /5 and /7.

https://timsong-cpp.github.io/cppwp/n4659/expr.add
https://timsong-cpp.github.io/cppwp/n4659/support.types
Observers use the same array/object or two null pointer values. They do not
assume an evaluation order between the two operands or define unrelated-array
subtraction. Complete TinyXML2, native pcc1 and constant-expression proof remain
separate obligations.
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


# Original TinyXML2 11.0.0 source lines, upstream revision
# 9148bdf719e997d1f474be6bcc7943881046dba1, Zlib. Surrounding observers are
# authored, not a replacement for XMLUtil::GetCharacterRef or XMLPrinter.
UPSTREAM_ASSIGNMENT = '        delta = q - p;\r\n'
UPSTREAM_DECLARATION = '                        const size_t delta = q - p;\r\n'
ORIGINAL_NULL = 'int f(){int*p=nullptr;p-p;return 0;}'

CASES = (
    ('tinyxml-character-ref-subtraction',
     'int cpp_test_entry(){char text[43]{};const char*p=text;const char*q=p+42;'
     'long delta=0;' + UPSTREAM_ASSIGNMENT + 'return int(delta);}'),
    ('tinyxml-printer-subtraction',
     'using size_t=unsigned long;int cpp_test_entry(){char text[43]{};'
     'const char*p=text;const char*q=p+42;' + UPSTREAM_DECLARATION +
     'return int(delta);}'),
    ('signed-int-element-count',
     'int cpp_test_entry(){int a[4]{};int*p=&a[0];int*q=&a[3];'
     'if(q-p!=3||p-q!=-3||p-p!=0)return 1;return 42;}'),
    ('mixed-pointee-const',
     'int cpp_test_entry(){int a[4]{};int*p=a;const int*q=a+3;'
     'if(q-p!=3||p-q!=-3)return 1;return 42;}'),
    ('typed-null-zero-and-original-source',
     ORIGINAL_NULL + 'int cpp_test_entry(){int*p=nullptr;const int*q=nullptr;'
     'if(p-q!=0||q-p!=0)return 1;return f()+42;}'),
    ('pointer-to-array-element-stride',
     'long distance(int(*p)[3],const int(*q)[3]){return q-p;}'
     'int cpp_test_entry(){int a[4][3]{};if(distance(a,a+4)!=4)return 1;'
     'if(distance(a+4,a)!=-4)return 2;return 42;}'),
    ('pointer-object-element-stride',
     'int cpp_test_entry(){int* a[4]{};int**p=a;int*const*q=a+4;'
     'if(q-p!=4||p-q!=-4)return 1;return 42;}'),
    ('complete-record-one-past',
     'struct Record{int a;long b;};int cpp_test_entry(){Record r{7,42};'
     'Record*p=&r;const Record*q=p+1;if(q-p!=1||p-q!=-1)return 1;return 42;}'),
    ('independent-operands-once',
     'int left_calls=0;int right_calls=0;'
     'int*left(int*p){left_calls+=1;return p;}'
     'int*right(int*p){right_calls+=1;return p;}'
     'int cpp_test_entry(){int a[4]{};long n=left(a+3)-right(a);'
     'if(n!=3||left_calls!=1||right_calls!=1)return 1;return 42;}'),
    ('target-ptrdiff-overload-identity',
     'int pick(long n){if(n==-3)return 42;return 1;}int pick(long long n){return 2;}'
     'int cpp_test_entry(){int a[4]{};return pick(a-(a+3));}'),
    ('class-template-replay',
     'template<class T>struct Span{T*first;long count(T*last){return last-first;}};'
     'int cpp_test_entry(){int a[4]{};Span<int>s{a};'
     'if(s.count(a+4)!=4)return 1;return 42;}'),
)
PROGRAMS = tuple((name, ((name + '.cpp', source),)) for name, source in CASES)


def test_original_tinyxml_subtraction_lines_are_unchanged():
    source = Path(__file__).resolve().parents[2] / 'projects/tinyxml2-11.0.0/tinyxml2.cpp'
    lines = source.read_bytes().splitlines(keepends=True)
    assert lines[509] == UPSTREAM_ASSIGNMENT.encode()
    assert lines[2679] == UPSTREAM_DECLARATION.encode()


@pytest.mark.parametrize('name,units', PROGRAMS, ids=[row[0] for row in PROGRAMS])
def test_pointer_difference_artifacts_are_owned_and_stable(owned_only, name, units):
    assert compile_host_cpp(units) == compile_host_cpp(units)


@pytest.mark.parametrize('target,expected', (
    ('x86_64-unknown-linux-gnu', 'l'),
    ('arm64-apple-darwin', 'l'),
    ('x86_64-pc-windows-msvc', 'x'),
))
def test_pointer_difference_uses_target_ptrdiff_overload(target, expected):
    tree = CppParser(target).parse(
        'int pick(long);int pick(long long);'
        'int f(int*p,int*q){return pick(p-q);}')
    calls = []

    def visit(node):
        if isinstance(node, c_ast.FuncCall):
            calls.append(node.name.name)
        for _, child in node.children():
            visit(child)

    visit(tree)
    assert calls == ['_Z4pick' + expected]


@pytest.mark.parametrize('source,code', (
    ('long f(int*p,long*q){return p-q;}', 'cpp.invalid-operands'),
    ('long f(void*p,void*q){return p-q;}', 'cpp.invalid-operands'),
    ('long f(int*p,void*q){return p-q;}', 'cpp.invalid-operands'),
    ('struct S;long f(S*p,S*q){return p-q;}', 'cpp.incomplete-class'),
    ('long f(int(*p)[2],int(*q)[3]){return p-q;}', 'cpp.invalid-operands'),
    ('long f(int(*p)[],int(*q)[]){return p-q;}', 'cpp.unsupported.unknown-bound-array-pointers'),
    ('long f(int**p,const int**q){return p-q;}', 'cpp.invalid-operands'),
    ('long f(int*p){return p-nullptr;}', 'cpp.invalid-operands'),
    ('long f(){return nullptr-nullptr;}', 'cpp.invalid-operands'),
    ('long f(int*p,int*q){return p+q;}', 'cpp.invalid-operands'),
    ('long f(int*p,int*q){(p-q)=0;return 0;}', 'cpp.not-lvalue'),
    ('int a[2];long n=&a[1]-&a[0];', 'cpp.unsupported.namespace-array-objects'),
    ('int value;long n=(&value+1)-&value;', 'cpp.unsupported.dynamic-initialization'),
    ('template<class T>long distance(T first,T last){return last-first;}'
     'int cpp_test_entry(){int a[4]{};if(distance(a,a+4)!=4)return 1;'
     'if(distance(7,4)!=-3)return 2;return 42;}', 'cpp.unsupported.array-templates'),
    ('template<class T>long distance(T first,T last){return last-first;}'
     'int cpp_test_entry(){int a[4]{};int*p=a;if(distance(p,p+4)!=4)return 1;'
     'if(distance(7,4)!=-3)return 2;return 42;}', 'cpp.unsupported.pointer-templates'),
))
def test_pointer_difference_rejects_invalid_or_unmodeled_forms(source, code):
    with pytest.raises(CppDiagnostic) as error:
        CppParser().parse(source)
    assert error.value.code == code


def test_function_pointer_difference_fails_before_c_projection():
    parser = CppParser()
    parser.parse('')
    coord = Coord('function-difference.cpp', 1, 1)
    dtype = PointerType(FunctionType('int', ()))
    left = Expression(c_ast.ID('p', coord), dtype)
    right = Expression(c_ast.ID('q', coord), dtype)
    with pytest.raises(CppDiagnostic) as error:
        parser._pointer_additive_expression('-', left, right, coord)
    assert error.value.code == 'cpp.invalid-operands'


def test_dependent_additive_operands_preserve_pattern_before_substitution():
    parser = CppParser()
    parser.parse('')
    parser.template_function = object()
    coord = Coord('dependent-difference.cpp', 1, 1)
    left = Expression(Pattern('name', coord, 'p'), PointerType('int'))
    right = Expression(Pattern('name', coord, 'q'), TypeParameter(0, 'T', coord))
    result = parser._binary_expression('-', left, right, coord)
    assert isinstance(result.node, Pattern) and result.node.kind == 'binary'
    assert result.node.children == [left, right] and result.dtype is None
