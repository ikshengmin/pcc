"""Static integral arrays: N4659 [dcl.init.aggr], [basic.start.static], [stmt.dcl].

https://timsong-cpp.github.io/cppwp/n4659/dcl.init.aggr
https://timsong-cpp.github.io/cppwp/n4659/basic.start.static
An unchanged TinyXML2 declaration is surrounded by authored observers. This
does not establish whole-project acceptance. All execution locally UNRUN.
"""

from pathlib import Path

import pytest

from pcc.frontends.c.ast import c_ast
from pcc.frontends.cpp import CppDiagnostic, CppParser
from pcc.frontends.cpp.types import ArrayType
from tests.cpp.owned_execution import compile_host_cpp
from tests.cpp.test_cpp_owned_execution import owned_only

# TinyXML2 11.0.0 cpp:2722, upstream
# 9148bdf719e997d1f474be6bcc7943881046dba1, Zlib license. Preserve CRLF.
BOM = '        static const unsigned char bom[] = { TIXML_UTF_LEAD_0, TIXML_UTF_LEAD_1, TIXML_UTF_LEAD_2, 0 };\r\n'
ORIGINAL = 'void f(){static int a[2]={17,25};}'
CASES = (
    ('tinyxml-static-bom-bytes', 'int cpp_test_entry(){'
     'const unsigned char TIXML_UTF_LEAD_0=0xefU;'
     'const unsigned char TIXML_UTF_LEAD_1=0xbbU;'
     'const unsigned char TIXML_UTF_LEAD_2=0xbfU;' + BOM +
     'if(sizeof(bom)!=4||bom[0]!=239||bom[1]!=187||bom[2]!=191||bom[3]!=0)return 1;return 42;}'),
    ('fixed-list-values-persist', 'int next(){static int a[2]={16,24};return ++a[0]+ ++a[1];}'
     'int cpp_test_entry(){if(next()!=42)return 1;return next()-2;}'),
    ('zero-initialization-persists', 'int next(){static int a[2];if(a[1]!=0)return 1;return ++a[0];}'
     'int cpp_test_entry(){if(next()!=1)return 1;return next()+40;}'),
    ('empty-list-zeroes-all-elements', 'int cpp_test_entry(){static const int a[3]={};'
     'if(a[0]!=0||a[1]!=0||a[2]!=0)return 1;return 42;}'),
    ('partial-list-zeroes-tail', 'int cpp_test_entry(){static int a[4]={42};'
     'if(a[1]!=0||a[2]!=0||a[3]!=0)return 1;return a[0];}'),
    ('direct-list-and-scalar-subbraces', 'int cpp_test_entry(){static int a[]{{17},{25},};return a[0]+a[1];}'),
    ('nested-deduction-and-zero-padding', 'int cpp_test_entry(){static int a[][3]={{17},{0,0,25}};'
     'if(sizeof(a)!=6*sizeof(int)||a[0][1]!=0||a[0][2]!=0||a[1][0]!=0)return 1;return a[0][0]+a[1][2];}'),
    ('three-dimensional-subaggregates', 'int cpp_test_entry(){static int a[][2][2]={{{17}},{{0},{0,25}}};'
     'if(sizeof(a)!=8*sizeof(int)||a[0][1][1]!=0)return 1;return a[0][0][0]+a[1][1][1];}'),
    ('scoped-enum-elements', 'enum class E{A=17,B=25};int cpp_test_entry(){static E a[3]={E::A,E::B};'
     'if(int(a[2])!=0)return 1;return int(a[0])+int(a[1]);}'),
    ('bool-and-byte-elements', 'int cpp_test_entry(){static bool ready[]={true,false};'
     'static unsigned char a[]={0,255,42};if(!ready[0]||ready[1]||a[0]!=0||a[1]!=255)return 1;return a[2];}'),
    ('local-const-expression-normalized', 'int cpp_test_entry(){const int seed=17;'
     'static int a[]={seed,seed+8};return a[0]+a[1];}'),
    ('full-width-data-not-truncated', 'int cpp_test_entry(){static unsigned long long a[]={0xf123456789abc000ULL,42ULL};'
     'if(a[0]!=0xf123456789abc000ULL)return 1;return int(a[1]);}'),
    ('own-complete-bound-in-initializer', 'int cpp_test_entry(){static int a[2]={sizeof(a),34};return a[0]+a[1];}'),
    ('persistent-array-address', 'int*storage(){static int a[]={17,25};return a;}'
     'int cpp_test_entry(){int*p=storage();p[0]=42;if(storage()!=p||storage()[1]!=25)return 1;return storage()[0];}'),
    ('sibling-spellings-have-distinct-storage', 'int next(){int n=0;'
     '{static int a[]={16};n+=++a[0];}{static int a[]={24};n+=++a[0];}return n;}'
     'int cpp_test_entry(){if(next()!=42)return 1;return next()-2;}'),
    ('unknown-alias-completed-per-object', 'using A=int[];int cpp_test_entry(){'
     'static A a={17,25};static A b={1,2,3};'
     'if(sizeof(a)!=2*sizeof(int)||sizeof(b)!=3*sizeof(int))return 1;return a[0]+a[1];}'),
    ('const-qualified-array-alias', 'using A=int[];int cpp_test_entry(){static const A a={17,25};'
     'const int*p=a;return p[0]+p[1];}'),
    ('for-header-storage-not-reinitialized', 'int total=0;int run(){for(static int a[1]={0};a[0]<2;++a[0])++total;return total;}'
     'int cpp_test_entry(){if(run()!=2)return 1;return run()+40;}'),
    ('out-of-class-constructor-array-owner', 'struct S{S();int n;};'
     'S::S(){static int a[]={0};n=++a[0];}'
     'int cpp_test_entry(){S a;S b;return a.n+b.n+39;}'),
    ('out-of-class-destructor-array-owner', 'int total=0;struct S{~S();};'
     'S::~S(){static int a[]={0};total+=++a[0];}'
     'int cpp_test_entry(){{S a;S b;}return total+39;}'),
)
PROGRAMS = tuple((name, ((name+'.cpp', source),)) for name, source in CASES) + (
    ('former-static-array-original', (
        ('original.cpp', ORIGINAL),
        ('entry.cpp', 'void f();int cpp_test_entry(){f();return 42;}'),
    )),
    ('translation-units-have-distinct-static-arrays', (
        ('a.cpp', 'int a(){static int n[]={20};return ++n[0];}'),
        ('b.cpp', 'int b(){static int n[]={20};return ++n[0];}'),
        ('entry.cpp', 'int a();int b();int cpp_test_entry(){if(a()+b()!=42)return 1;if(a()+b()!=44)return 2;return 42;}'),
    )),
)


def test_tinyxml_bom_declaration_preserves_exact_original_bytes():
    path = Path(__file__).resolve().parents[2] / 'projects/tinyxml2-11.0.0/tinyxml2.cpp'
    assert path.read_bytes().splitlines(keepends=True)[2721] == BOM.encode()


@pytest.mark.parametrize('name,units', PROGRAMS, ids=[row[0] for row in PROGRAMS])
def test_static_array_artifacts_are_owned_and_stable(owned_only, name, units):
    assert compile_host_cpp(units) == compile_host_cpp(units)


INVALID = (
    ('const-needs-initializer', 'void f(){static const int a[2];}', 'cpp.const-initializer'),
    ('const-element-write', 'void f(){static const int a[]={42};a[0]=1;}', 'cpp.const-assignment'),
    ('empty-deduced-list', 'void f(){static int a[]={};}', 'cpp.invalid-array-bound'),
    ('excess-fixed-list', 'void f(){static int a[1]={1,2};}', 'cpp.too-many-initializers'),
    ('narrowing-byte', 'void f(){static unsigned char a[]={256};}', 'cpp.narrowing-conversion'),
    ('narrowing-floating', 'void f(){static int a[]={1.5};}', 'cpp.narrowing-conversion'),
    ('nested-scalar-braces', 'void f(){static int a[1]={{{1}}};}', 'cpp.invalid-scalar-initializer'),
    ('nominal-enum-conversion', 'enum class E{A=1};void f(){static E a[]={1};}', 'cpp.invalid-conversion'),
    ('own-name-is-incomplete-before-deduction', 'void f(){static int a[]={sizeof(a)};}', 'cpp.invalid-sizeof'),
    ('alias-is-not-completed', 'using A=int[];void f(){static A a={42};sizeof(A);}', 'cpp.invalid-sizeof'),
    ('same-block-redeclaration', 'void f(){static int a[1];static int a[1];}', 'cpp.redeclaration'),
)
GAP = 'cpp.unsupported.static-initialization-analysis'
DEFERRED = (
    ('dynamic-call-element', 'int get(){return 42;}void f(){static int a[]={get()};}', 'cpp.unsupported.dynamic-initialization'),
    ('parameter-element', 'void f(int n){static int a[]={n};}', 'cpp.unsupported.dynamic-initialization'),
    ('mutable-local-element', 'void f(){int n=42;static int a[]={n};}', 'cpp.unsupported.dynamic-initialization'),
    ('const-array-value-not-modeled', 'void f(){static const int a[]={42};static int n=a[0];}', 'cpp.unsupported.dynamic-initialization'),
    ('pointer-elements', 'void f(){static int*a[]={nullptr};}', GAP),
    ('floating-elements', 'void f(){static double a[]={1.5};}', GAP),
    ('class-elements', 'struct S{int n;};void f(){static S a[]={{42}};}', GAP),
    ('string-initialization', 'void f(){static char a[]="x";}', GAP),
    ('brace-elision', 'void f(){static int a[][2]={1,2,3,4};}', 'cpp.unsupported.array-brace-elision'),
    ('inline-function', 'inline int f(){static int a[]={42};return a[0];}', GAP),
    ('inline-constructor', 'struct S{S(){static int a[]={42};}};int f(){S s;return 42;}', GAP),
    ('template-function', 'template<class T>int f(T x){static int a[]={42};return a[0];}', GAP),
    ('switch-declaration', 'int f(int n){switch(n){case 1:static int a[]={42};case 2:return a[0];}return 0;}', GAP),
)


@pytest.mark.parametrize('name,source,code', INVALID + DEFERRED, ids=[row[0] for row in INVALID + DEFERRED])
def test_static_array_boundaries(name, source, code):
    with pytest.raises(CppDiagnostic) as caught:
        CppParser().parse(source)
    assert caught.value.code == code


def test_static_array_projection_normalizes_leaves_and_preserves_unknown_alias():
    parser = CppParser()
    tree = parser.parse('using A=int[];void f(){const int seed=17;static A a={seed,seed+8};static A b={42};}')
    assert isinstance(parser.scope.names['A'].dtype, ArrayType)
    assert parser.scope.names['A'].dtype.bound is None
    function, = [node for node in tree.ext if isinstance(node, c_ast.FuncDef)]
    first, second = function.body.block_items[1:]
    assert first.storage == second.storage == ['static']
    assert first.name != second.name
    assert first.type.dim.value == '2' and second.type.dim.value == '1'
    assert isinstance(first.init, c_ast.InitList)
    assert all(isinstance(value, c_ast.Cast) and isinstance(value.expr, c_ast.Constant)
               for value in first.init.exprs)
    assert [value.expr.value for value in first.init.exprs] == ['17', '25']


def test_uninitialized_static_array_keeps_compact_zero_storage_projection():
    tree = CppParser().parse('void f(){static int a[1000000];}')
    function, = [node for node in tree.ext if isinstance(node, c_ast.FuncDef)]
    declaration, = function.body.block_items
    assert declaration.storage == ['static']
    assert declaration.type.dim.value == '1000000'
    assert declaration.init is None
