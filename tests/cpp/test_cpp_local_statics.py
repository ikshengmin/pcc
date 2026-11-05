"""Constant/zero initialized local statics, N4659 [basic.start.static], [stmt.dcl].

https://timsong-cpp.github.io/cppwp/n4659/basic.start.static
https://timsong-cpp.github.io/cppwp/n4659/stmt.dcl
No dynamic guard runtime, inline sharing, switch hoisting or whole TinyXML2
acceptance is established. All executable obligations remain locally UNRUN.
"""

from pathlib import Path

import pytest

from pcc.frontends.c.ast import c_ast
from pcc.frontends.cpp import CppDiagnostic, CppParser
from tests.cpp.owned_execution import compile_host_cpp
from tests.cpp.test_cpp_owned_execution import owned_only

# Exact TinyXML2 11.0.0 cpp:475,481,722-726; upstream
# 9148bdf719e997d1f474be6bcc7943881046dba1, Zlib. Preserve original CRLF/tabs.
CODE_POINT = '    static const uint32_t MAX_CODE_POINT = 0x10FFFF;\r\n'
SEMICOLON = "        static const char SEMICOLON = ';';\r\n"
HEADER_LENGTHS = '    static const int xmlHeaderLen\t\t= 2;\r\n    static const int commentHeaderLen\t= 4;\r\n    static const int cdataHeaderLen\t\t= 9;\r\n    static const int dtdHeaderLen\t\t= 2;\r\n    static const int elementHeaderLen\t= 1;\r\n'
CASES = (
    ('tinyxml-code-point-limit', 'using uint32_t=unsigned int;int cpp_test_entry(){' + CODE_POINT +
     'if(MAX_CODE_POINT!=0x10FFFF)return 1;return 42;}'),
    ('tinyxml-semicolon-constant', 'int cpp_test_entry(){' + SEMICOLON + 'return int(SEMICOLON)-17;}'),
    ('tinyxml-header-length-constants', 'int cpp_test_entry(){' + HEADER_LENGTHS +
     'return xmlHeaderLen+commentHeaderLen+cdataHeaderLen+dtdHeaderLen+elementHeaderLen+24;}'),
    ('zero-initialization-persists', 'int tick(){static int n;return ++n;}'
     'int cpp_test_entry(){if(tick()!=1||tick()!=2)return 1;return 42;}'),
    ('constant-initialization-not-repeated', 'int tick(){static int n=17;n+=25;return n;}'
     'int cpp_test_entry(){if(tick()!=42||tick()!=67)return 1;return 42;}'),
    ('persistent-object-address', 'int*storage(){static int n;return &n;}'
     'int cpp_test_entry(){int*p=storage();*p=42;if(storage()!=p)return 1;return *storage();}'),
    ('sibling-spellings-have-distinct-storage',
     'int sum(){int total=0;{static int n=16;total+=++n;}{static int n=24;total+=++n;}return total;}'
     'int cpp_test_entry(){if(sum()!=42)return 1;return sum()-2;}'),
    ('local-spelling-does-not-hide-qualified-object', 'int n=25;'
     'int cpp_test_entry(){static int n=17;return n+::n;}'),
    ('local-const-source-is-a-static-initializer',
     'int cpp_test_entry(){const int seed=17;static int n=seed+25;return n;}'),
    ('static-const-proof-for-bound-and-case',
     'int cpp_test_entry(){static const int count=2;int a[count]={17,25};'
     'switch(count){case count:return a[0]+a[1];default:return 1;}}'),
    ('scoped-enum-constant', 'enum class E{Answer=42};'
     'int cpp_test_entry(){static E value=E::Answer;return int(value);}'),
    ('scoped-enum-zero-initialization', 'enum class E{Answer=42};'
     'int cpp_test_entry(){static E value;if(int(value)!=0)return 1;value=E::Answer;return int(value);}'),
    ('bool-and-character-storage', 'int cpp_test_entry(){static bool ready=true;static char value=\'*\';'
     'if(!ready)return 1;return int(value);}'),
    ('target-unsigned-wrapping-proof', 'int cpp_test_entry(){static unsigned int n=4294967295U+1U;'
     'if(n!=0U)return 1;return 42;}'),
    ('full-width-static-data', 'int cpp_test_entry(){static unsigned long long n=0xf123456789abc000ULL;'
     'if(n!=0xf123456789abc000ULL)return 1;return 42;}'),
    ('for-init-static-not-reinitialized', 'int loops=0;'
     'int run(){for(static int n=0;n<2;++n)++loops;return loops;}'
     'int cpp_test_entry(){if(run()!=2)return 1;return run()+40;}'),
    ('out-of-class-method-shared-state', 'struct S{int next()const;};'
     'int S::next()const{static int n=16;return ++n;}'
     'int cpp_test_entry(){const S a{};const S b{};if(a.next()!=17)return 1;return b.next()+24;}'),
    ('out-of-class-constructor-owner', 'struct S{S();int n;};'
     'S::S(){static int count=0;n=++count;}'
     'int cpp_test_entry(){S a;S b;return a.n+b.n+39;}'),
    ('out-of-class-destructor-owner', 'int total=0;struct S{~S();};'
     'S::~S(){static int count=0;total+=++count;}'
     'int cpp_test_entry(){{S a;S b;}return total+39;}'),
    ('conditional-static-block', 'int next(bool choose){if(choose){static int n=17;return ++n;}return 0;}'
     'int cpp_test_entry(){if(next(false)!=0||next(true)!=18)return 1;return next(true)+23;}'),
)
PROGRAMS = tuple((name, ((name+'.cpp', source),)) for name, source in CASES) + (
    ('separate-translation-unit-statics', (
        ('a.cpp', 'int a(){static int n=20;return ++n;}'),
        ('b.cpp', 'int b(){static int n=20;return ++n;}'),
        ('entry.cpp', 'int a();int b();int cpp_test_entry(){if(a()+b()!=42)return 1;'
         'if(a()+b()!=44)return 2;return 42;}'),
    )),
)


def test_tinyxml_static_declarations_preserve_exact_original_bytes():
    path = Path(__file__).resolve().parents[2] / 'projects/tinyxml2-11.0.0/tinyxml2.cpp'
    lines = path.read_bytes().splitlines(keepends=True)
    assert lines[474] == CODE_POINT.encode()
    assert lines[480] == SEMICOLON.encode()
    assert b''.join(lines[721:726]) == HEADER_LENGTHS.encode()


@pytest.mark.parametrize('name,units', PROGRAMS, ids=[row[0] for row in PROGRAMS])
def test_local_static_artifacts_are_owned_and_stable(owned_only, name, units):
    assert compile_host_cpp(units) == compile_host_cpp(units)


INVALID = (
    ('const-needs-initializer', 'void f(){static const int n;}', 'cpp.const-initializer'),
    ('write-const-static', 'void f(){static const int n=42;n=1;}', 'cpp.const-assignment'),
    ('duplicate-static', 'void f(){static static int n;}', 'cpp.invalid-storage-specifier'),
    ('conflicting-storage', 'void f(){static extern int n;}', 'cpp.invalid-storage-specifier'),
    ('same-block-redeclaration', 'void f(){static int n;static int n;}', 'cpp.redeclaration'),
    ('for-body-redeclaration', 'void f(){for(static int n=0;n<1;++n){int n=0;}}', 'cpp.redeclaration'),
)
STATIC_GAP = 'cpp.unsupported.static-initialization-analysis'
DEFERRED = (
    ('dynamic-call', 'int get(){return 42;}int f(){static int n=get();return n;}',
     'cpp.unsupported.dynamic-initialization'),
    ('mutable-local-is-not-a-constant', 'int f(){int seed=42;static int n=seed;return n;}',
     'cpp.unsupported.dynamic-initialization'),
    ('mutable-static-is-not-a-constant', 'int f(){static int seed=42;static int n=seed;return n;}',
     'cpp.unsupported.dynamic-initialization'),
    ('parameter-is-not-a-constant', 'int f(int seed){static int n=seed;return n;}',
     'cpp.unsupported.dynamic-initialization'),
    # Keep the preexisting inline-method original unchanged; only its precise
    # capability diagnostic has changed in test_cpp_inline_methods.py.
    ('inline-method-original', 'struct S{int f(){static int n=0;return n;}};', STATIC_GAP),
    ('inline-constructor', 'struct S{S(){static int n=0;}};int f(){S s;return 0;}', STATIC_GAP),
    ('inline-destructor', 'struct S{~S(){static int n=0;}};int f(){S s;return 0;}', STATIC_GAP),
    ('function-template', 'template<class T>int f(T x){static int n=0;return n;}', STATIC_GAP),
    ('switch-before-first-label', 'int f(int n){switch(n){static int x=41;case 1:return ++x;}return 0;}', STATIC_GAP),
    ('switch-between-labels', 'int f(int n){switch(n){case 1:static int x=41;case 2:return ++x;}return 0;}', STATIC_GAP),
    ('switch-after-terminated-case', 'int f(int n){switch(n){case 1:return 1;static int x=42;case 2:return x;}return 0;}', STATIC_GAP),
    ('switch-nested-static', 'int f(int n){switch(n){case 1:{static int x=42;return x;}}return 0;}', STATIC_GAP),
    ('pointer-static', 'void f(){static int*p=nullptr;}', STATIC_GAP),
    ('array-static', 'void f(){static int a[2]={17,25};}', STATIC_GAP),
    ('class-static', 'struct S{int n;};void f(){static S s{42};}', STATIC_GAP),
    ('reference-static', 'int f(){int n=42;static int&r=n;return r;}', 'cpp.unsupported.references'),
    ('floating-static', 'void f(){static double n=42.0;}', STATIC_GAP),
    ('list-static', 'void f(){static int n{42};}', STATIC_GAP),
    ('reordered-storage', 'void f(){int static n=42;}', 'cpp.unsupported.syntax'),
)


@pytest.mark.parametrize('name,source,code', INVALID + DEFERRED, ids=[row[0] for row in INVALID + DEFERRED])
def test_local_static_boundaries(name, source, code):
    with pytest.raises(CppDiagnostic) as caught:
        CppParser().parse(source)
    assert caught.value.code == code


def test_static_projection_retains_storage_and_normalizes_cpp_constant_proof():
    tree = CppParser().parse('void f(){const int seed=17;static int n=seed+25;}')
    function, = [node for node in tree.ext if isinstance(node, c_ast.FuncDef)]
    static = function.body.block_items[1]
    assert static.storage == ['static']
    assert isinstance(static.init, c_ast.Cast)
    assert isinstance(static.init.expr, c_ast.Constant) and static.init.expr.value == '42'


def test_sibling_static_names_have_distinct_c_projection_identity():
    tree = CppParser().parse('void f(){{static int n=17;}{static int n=25;}}')
    function, = [node for node in tree.ext if isinstance(node, c_ast.FuncDef)]
    first, second = [block.block_items[0] for block in function.body.block_items]
    assert first.storage == second.storage == ['static']
    assert first.name != second.name
