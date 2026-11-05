"""Local explicit-list array extent deduction, N4659 [dcl.init.aggr].

https://timsong-cpp.github.io/cppwp/n4659/dcl.init.aggr
https://timsong-cpp.github.io/cppwp/n4659/basic.scope.pdecl
Exact TinyXML2 UTF-8 byte declarations remain unchanged. The surrounding
observers are authored and do not establish complete TinyXML2 acceptance.
"""

from pathlib import Path

import pytest

from pcc.frontends.c.ast import c_ast
from pcc.frontends.cpp import CppDiagnostic, CppParser
from pcc.frontends.cpp.types import ArrayType
from tests.cpp.owned_execution import compile_host_cpp
from tests.cpp.test_cpp_owned_execution import owned_only

# TinyXML2 11.0.0 xmltest.cpp:675-676 and 680-683, upstream
# 9148bdf719e997d1f474be6bcc7943881046dba1, Zlib license. Preserve CRLF/tabs.
CORRECT_VALUE = '\t\tconst unsigned char correctValue[] = {\t0xd1U, 0x86U, 0xd0U, 0xb5U, 0xd0U, 0xbdU, 0xd0U, 0xbdU,\r\n\t\t\t\t\t\t\t\t\t\t\t\t0xd0U, 0xbeU, 0xd1U, 0x81U, 0xd1U, 0x82U, 0xd1U, 0x8cU, 0 };\r\n'
ELEMENT_NAME = '\t\tconst unsigned char russianElementName[] = {\t0xd0U, 0xa0U, 0xd1U, 0x83U,\r\n\t\t\t\t\t\t\t\t\t\t\t\t\t\t0xd1U, 0x81U, 0xd1U, 0x81U,\r\n\t\t\t\t\t\t\t\t\t\t\t\t\t\t0xd0U, 0xbaU, 0xd0U, 0xb8U,\r\n\t\t\t\t\t\t\t\t\t\t\t\t\t\t0xd0U, 0xb9U, 0 };\r\n'
ORIGINAL = 'int f(){int a[]={1,2};return 0;}'
CASES = (
    ('tinyxml-russian-attribute-bytes', 'int cpp_test_entry(){' + CORRECT_VALUE +
     'if(sizeof(correctValue)!=17)return 1;int sum=0;'
     'for(int i=0;i<17;++i)sum+=correctValue[i];'
     'if(sum!=2950||correctValue[16]!=0)return 2;return 42;}'),
    ('tinyxml-russian-element-name-bytes', 'int cpp_test_entry(){' + ELEMENT_NAME +
     'if(sizeof(russianElementName)!=15)return 1;int sum=0;'
     'for(int i=0;i<15;++i)sum+=russianElementName[i];'
     'if(sum!=2563||russianElementName[14]!=0)return 2;return 42;}'),
    ('copy-list-size-and-values', 'int cpp_test_entry(){int a[]={17,25};'
     'if(sizeof(a)!=2*sizeof(int))return 1;return a[0]+a[1];}'),
    ('direct-list-trailing-comma', 'int cpp_test_entry(){int a[]{17,25,};return a[0]+a[1];}'),
    ('explicit-scalar-subbraces', 'int cpp_test_entry(){int a[]={{17},{25}};return a[0]+a[1];}'),
    ('initializer-evaluation-order-once',
     'int take(int*p,int digit,int value){*p=*p*10+digit;return value;}'
     'int cpp_test_entry(){int trace=0;int a[]={take(&trace,1,17),take(&trace,2,25)};'
     'if(trace!=12)return 1;return a[0]+a[1];}'),
    ('prior-element-read-after-initialization',
     'int cpp_test_entry(){int a[]={17,a[0]+25};return a[1];}'),
    ('alias-completed-per-object', 'using A=int[];int cpp_test_entry(){A a={17,25};A b={1,2,3};'
     'if(sizeof(a)!=2*sizeof(int)||sizeof(b)!=3*sizeof(int))return 1;return a[0]+a[1];}'),
    ('nested-rows-preserve-stride-and-zeros',
     'int cpp_test_entry(){int a[][3]={{17},{0,0,25}};int(*p)[3]=a;'
     'if(sizeof(a)!=6*sizeof(int)||p[0][1]!=0||p[0][2]!=0)return 1;return p[0][0]+p[1][2];}'),
    ('three-dimensional-explicit-lists',
     'int cpp_test_entry(){int a[][2][2]={{{17}},{{0},{0,25}}};'
     'if(sizeof(a)!=8*sizeof(int)||a[0][1][1]!=0)return 1;return a[0][0][0]+a[1][1][1];}'),
    ('pointer-element-addresses-and-null',
     'int cpp_test_entry(){int x=17;int y=25;int*a[]={&x,&y,nullptr};'
     'if(a[2]!=nullptr)return 1;return *a[0]+*a[1];}'),
    ('nominal-enum-elements', 'enum class E{A=17,B=25};'
     'int cpp_test_entry(){E a[]={E::A,E::B};return int(a[0])+int(a[1]);}'),
    ('full-width-integer-elements',
     'int cpp_test_entry(){unsigned long long a[]={0xf123456789abc000ULL,42ULL};'
     'if(a[0]!=0xf123456789abc000ULL)return 1;return int(a[1]);}'),
    ('floating-elements', 'int cpp_test_entry(){double a[]={17.5,24.5};return int(a[0]+a[1]);}'),
    ('for-init-array-scope', 'int cpp_test_entry(){for(int a[]={17,25};true;){return a[0]+a[1];}return 1;}'),
    ('braced-switch-array', 'int cpp_test_entry(){switch(1){case 1:{int a[]={17,25};'
     'return a[0]+a[1];}default:return 1;}}'),
    ('decltype-retains-completed-bound',
     'int cpp_test_entry(){int a[]={17,25};using A=decltype(a);A b={1,2};int(*p)[2]=&a;'
     'if(sizeof(b)!=sizeof(a))return 1;return (*p)[0]+(*p)[1];}'),
    ('const-alias-elements', 'using A=int[];int cpp_test_entry(){const A a={17,25};'
     'const int*p=a;return p[0]+p[1];}'),
)
PROGRAMS = (
    ('former-unknown-bound-original', (
        ('original.cpp', ORIGINAL),
        ('entry.cpp', 'int f();int cpp_test_entry(){return f()+42;}'),
    )),
) + tuple((name, ((name+'.cpp', source),)) for name, source in CASES)


def test_tinyxml_unknown_bound_declarations_preserve_exact_original_bytes():
    path = Path(__file__).resolve().parents[2] / 'projects/tinyxml2-11.0.0/xmltest.cpp'
    lines = path.read_bytes().splitlines(keepends=True)
    assert b''.join(lines[674:676]) == CORRECT_VALUE.encode()
    assert b''.join(lines[679:683]) == ELEMENT_NAME.encode()


@pytest.mark.parametrize('name,units', PROGRAMS, ids=[row[0] for row in PROGRAMS])
def test_deduced_local_array_artifacts_are_owned_and_stable(owned_only, name, units):
    assert compile_host_cpp(units) == compile_host_cpp(units)


INVALID = (
    ('empty-copy-list', 'void f(){int a[]={};}', 'cpp.invalid-array-bound'),
    ('empty-direct-list', 'void f(){int a[]{};}', 'cpp.invalid-array-bound'),
    ('narrowing-byte', 'void f(){unsigned char a[]={256};}', 'cpp.narrowing-conversion'),
    ('narrowing-floating', 'void f(){int a[]={1.5};}', 'cpp.narrowing-conversion'),
    ('nested-row-excess', 'void f(){int a[][2]={{1,2,3}};}', 'cpp.too-many-initializers'),
    ('const-element-write', 'void f(){const int a[]={42};a[0]=1;}', 'cpp.const-assignment'),
    ('own-name-incomplete-in-initializer',
     'void f(){int a[4];{int a[]={int(sizeof(a))};}}', 'cpp.invalid-sizeof'),
    ('alias-not-completed-by-object', 'using A=int[];void f(){A a={42};sizeof(A);}', 'cpp.invalid-sizeof'),
    ('jump-bypasses-initialization', 'void f(){switch(1){case 1:int a[]={42};case 2:break;}}',
     'cpp.switch-jump-initialization'),
    ('inner-bound-stays-required', 'void f(){int a[][]={{42}};}', 'cpp.incomplete-array-element'),
)
DEFERRED = (
    ('brace-elision', 'void f(){int a[][2]={1,2,3,4};}', 'cpp.unsupported.array-brace-elision'),
    ('class-array-initialization', 'struct S{int n;};void f(){S a[]={{42}};}',
     'cpp.unsupported.class-array-initialization'),
    ('string-bound-deduction', 'void f(){char a[]="x";}', 'cpp.unsupported.array-bound-deduction'),
    ('namespace-array', 'int a[]={1,2};', 'cpp.unsupported.namespace-array-objects'),
    ('template-array', 'template<class T>int f(T x){int a[]={1,2};return a[0];}',
     'cpp.unsupported.array-templates'),
)


@pytest.mark.parametrize('name,source,code', INVALID + DEFERRED, ids=[row[0] for row in INVALID + DEFERRED])
def test_deduced_array_boundaries(name, source, code):
    with pytest.raises(CppDiagnostic) as caught:
        CppParser().parse(source)
    assert caught.value.code == code


def test_deduction_projects_complete_extent_without_mutating_unknown_bound_alias():
    parser = CppParser()
    tree = parser.parse('using A=int[];void f(){A a={17,25};A b={42};}')
    alias = parser.scope.names['A'].dtype
    assert isinstance(alias, ArrayType) and alias.bound is None
    function, = [node for node in tree.ext if isinstance(node, c_ast.FuncDef)]
    a, b = function.body.block_items
    assert isinstance(a.type, c_ast.ArrayDecl) and a.type.dim.value == '2'
    assert isinstance(b.type, c_ast.ArrayDecl) and b.type.dim.value == '1'
    assert len(a.init.exprs) == 2 and len(b.init.exprs) == 1
