"""Local aliases of existing lvalues, N4659 [dcl.init.ref], [basic.scope.pdecl].

https://timsong-cpp.github.io/cppwp/n4659/dcl.init.ref
https://timsong-cpp.github.io/cppwp/n4659/basic.scope.pdecl
https://timsong-cpp.github.io/cppwp/n4659/stmt.dcl
Equals initialization borrows an existing scalar, pointer or class lvalue.
No temporary lifetime extension, class copies or whole TinyXML2 acceptance.
"""

from pathlib import Path

import pytest

from pcc.frontends.c.ast import c_ast
from pcc.frontends.cpp import CppDiagnostic, CppParser
from pcc.frontends.cpp.constants import KNOWN
from tests.cpp.owned_execution import compile_host_cpp
from tests.cpp.test_cpp_owned_execution import owned_only


# TinyXML2 11.0.0, upstream 9148bdf719e997d1f474be6bcc7943881046dba1,
# Zlib license. Exact original declaration; the surrounding observer is authored.
ENTITY_REFERENCE = '                            const Entity& entity = entities[i];\r\n'
ORIGINAL_G = 'int g(){int n=0;int&r=n;return r;}'
ORIGINAL_F = 'int f(){int n=0;int&r=n;return r;}'
ORIGINAL_CLASS = 'struct N{};int f(){N n;N&r=n;return 0;}'
ORIGINAL_STATIC = ('struct N{static int answer(){return 42;}};'
                   'struct H{H(const N&n):value(n.answer()){}int value;};')
ORIGINAL_ENUMERATOR = ('struct N{enum{Answer=42};};'
                       'struct H{H(const N&n):value(n.Answer){}int value;};')
CASES = (
    ('original-local-reference-g', ORIGINAL_G + 'int cpp_test_entry(){return g()+42;}'),
    ('original-local-reference-f', ORIGINAL_F + 'int cpp_test_entry(){return f()+42;}'),
    ('original-local-class-reference', ORIGINAL_CLASS + 'int cpp_test_entry(){return f()+42;}'),
    ('original-static-member-through-borrowed-object', ORIGINAL_STATIC +
     'int cpp_test_entry(){N n;H h(n);return h.value;}'),
    ('original-enumerator-through-borrowed-object', ORIGINAL_ENUMERATOR +
     'int cpp_test_entry(){N n;H h(n);return h.value;}'),
    ('tinyxml-entity-reference-line',
     'struct Entity{const char*pattern;int length;char value;};'
     'int cpp_test_entry(){Entity entities[1];entities[0].length=42;int i=0;'
     + ENTITY_REFERENCE + 'return entity.length;}'),
    ('write-through-alias-and-reference-chain',
     'int cpp_test_entry(){int n=1;int&r=n;int&s=r;s+=41;if(&s!=&n||&r!=&n)return 1;return n;}'),
    ('pointer-variable-not-pointee-alias',
     'int cpp_test_entry(){int a=1;int b=42;int*p=&a;int*&r=p;r=&b;'
     'if(p!=&b||a!=1)return 1;return *p;}'),
    ('const-pointer-variable-alias',
     'int cpp_test_entry(){int n=42;int*p=&n;int*const&r=p;return *r;}'),
    ('row-pointer-reference-retains-stride',
     'int cpp_test_entry(){int a[2][3]={{1,2,3},{42,5,6}};int(*p)[3]=a;'
     'int(*&r)[3]=p;++r;return (*p)[0];}'),
    ('const-reference-overload-and-value-type',
     'int choose(int&n){return 1;}int choose(const int&n){return n;}'
     'int cpp_test_entry(){int n=42;const int&r=n;return choose(r);}'),
    ('floating-and-full-width-storage',
     'int cpp_test_entry(){double d=41.5;double&r=d;r+=0.5;'
     'unsigned long long wide=4294967295ULL;unsigned long long&w=wide;++w;'
     'if(wide!=4294967296ULL)return 1;return int(d);}'),
    ('reference-return-and-parameter-alias',
     'int&identity(int&n){return n;}void add(int&n){n+=25;}'
     'int cpp_test_entry(){int n=17;int&r=identity(n);add(r);return n;}'),
    ('effectful-subscript-captured-once',
     'int cpp_test_entry(){int a[2]={17,1};int i=0;int&r=a[i++];r+=25;'
     'if(i!=1||a[1]!=1)return 1;return a[0];}'),
    ('effectful-class-binding-captured-once',
     'struct S{int n;};int calls=0;S*choose(S*p){calls+=1;return p;}'
     'int cpp_test_entry(){S s{42};const S&r=*choose(&s);'
     'if(calls!=1||&r!=&s)return 1;return r.n;}'),
    ('class-member-cv-and-mutable-field',
     'struct S{int n;mutable int cache;int read()const{return n+cache;}};'
     'int cpp_test_entry(){S s{17,0};const S&r=s;r.cache=25;return r.read();}'),
    ('alias-does-not-own-destruction',
     'struct S{int*p;~S();};S::~S(){*p+=1;}'
     'int cpp_test_entry(){int destroyed=0;{S owner{&destroyed};'
     '{S&r=owner;const S&s=r;if(s.p!=&destroyed)return 1;}if(destroyed!=0)return 2;}'
     'if(destroyed!=1)return 3;return 42;}'),
    ('loop-init-reference',
     'int cpp_test_entry(){int n=0;for(int&r=n;r<42;++r){}return n;}'),
    ('switch-braced-alias',
     'int cpp_test_entry(){int n=0;switch(1){case 1:{int&r=n;r=42;break;}default:return 1;}return n;}'),
    ('conditional-lvalue-keeps-selection',
     'int cpp_test_entry(){int a=1;int b=17;bool choose=false;int&r=choose?a:b;'
     'r+=25;if(a!=1)return 1;return b;}'),
    ('reference-decltype-and-sizeof',
     'int cpp_test_entry(){int n=42;int&r=n;using R=decltype(r);R s=n;'
     'if(sizeof(R)!=sizeof(int)||&s!=&n)return 1;return s;}'),
    ('nullptr-reference-never-fetches-indeterminate-referent',
     'using N=decltype(nullptr);int cpp_test_entry(){N n;N&r=n;int*p=r;'
     'if(p!=nullptr)return 1;r;return 42;}'),
    ('scope-shadowing-keeps-qualified-object',
     'int value=17;int cpp_test_entry(){int other=25;{int&value=other;value+=17;'
     'if(::value!=17)return 1;}return other;}'),
)
PROGRAMS = tuple((name, ((name + '.cpp', source),)) for name, source in CASES)


def test_tinyxml_entity_alias_preserves_exact_original_line():
    original = Path(__file__).resolve().parents[2] / 'projects/tinyxml2-11.0.0/tinyxml2.cpp'
    assert original.read_bytes().splitlines(keepends=True)[343] == ENTITY_REFERENCE.encode()


@pytest.mark.parametrize('name,units', PROGRAMS, ids=[row[0] for row in PROGRAMS])
def test_local_reference_artifacts_are_owned_and_stable(owned_only, name, units):
    assert compile_host_cpp(units) == compile_host_cpp(units)


INVALID = (
    ('missing-initializer', 'void f(){int&r;}', 'cpp.reference-initializer-required'),
    ('nonconst-reference-to-prvalue', 'void f(){int&r=42;}', 'cpp.invalid-reference-binding'),
    ('drop-const', 'void f(){const int n=42;int&r=n;}', 'cpp.invalid-reference-binding'),
    ('different-scalar-type', 'void f(){int n=42;double&r=n;}', 'cpp.invalid-reference-binding'),
    ('different-class-type', 'struct A{};struct B{};void f(){A a;B&r=a;}', 'cpp.invalid-reference-binding'),
    ('write-through-const-alias', 'void f(){int n=42;const int&r=n;r=1;}', 'cpp.const-assignment'),
    ('const-class-field', 'struct S{int n;};void f(){S s{42};const S&r=s;r.n=1;}', 'cpp.const-assignment'),
    ('case-jumps-over-binding', 'void f(){int n=0;switch(1){case 1:int&r=n;case 2:break;}}',
     'cpp.switch-jump-initialization'),
    ('for-body-redeclaration', 'void f(){int n=0;for(int&r=n;r<1;++r){int r=0;}}', 'cpp.redeclaration'),
)
DEFERRED = (
    ('temporary-const-reference', 'void f(){const int&r=42;}', 'cpp.unsupported.reference-temporaries'),
    ('conversion-temporary', 'void f(){int n=42;const double&r=n;}', 'cpp.unsupported.reference-temporaries'),
    ('parenthesized-initializer', 'void f(){int n=42;int&r(n);}', 'cpp.unsupported.references'),
    ('list-initializer', 'void f(){int n=42;int&r{n};}', 'cpp.unsupported.references'),
    ('assignment-result-binding', 'void f(){int n=0;int&r=(n=42);}', 'cpp.unsupported.references'),
    ('namespace-reference', 'int n;int&r=n;', 'cpp.unsupported.references'),
    ('reference-member', 'struct S{int&r;};', 'cpp.unsupported.references'),
    ('rvalue-reference', 'void f(){int&&r=42;}', 'cpp.unsupported.references'),
    ('array-reference', 'void f(){int a[2];int(&r)[2]=a;}', 'cpp.unsupported.references'),
    ('template-local-reference', 'template<class T>int f(T n){T&r=n;return 0;}', 'cpp.unsupported.references'),
)


@pytest.mark.parametrize('name,source,code', INVALID + DEFERRED, ids=[row[0] for row in INVALID + DEFERRED])
def test_local_reference_diagnostics_preserve_binding_and_lifetime_boundaries(name, source, code):
    with pytest.raises(CppDiagnostic) as caught:
        CppParser().parse(source)
    assert caught.value.code == code


def test_local_reference_projection_captures_address_instead_of_copying_referent():
    tree = CppParser().parse('int f(){int n=17;int&r=n;r=42;return n;}')
    function, = [node for node in tree.ext if isinstance(node, c_ast.FuncDef)]
    reference = function.body.block_items[1]
    assert isinstance(reference.type, c_ast.PtrDecl)
    assert isinstance(reference.init, c_ast.UnaryOp) and reference.init.op == '&'
    assert isinstance(reference.init.expr, c_ast.ID) and reference.init.expr.name == 'n'


def test_reference_binding_precedes_initializer_name_lookup_without_outer_rebinding():
    # This self-binding has undefined runtime behavior. Check lookup only;
    # deliberately never execute it or claim a semantic result for its value.
    tree = CppParser().parse('int r;void f(){int&r=r;}')
    function, = [node for node in tree.ext if isinstance(node, c_ast.FuncDef)]
    reference = function.body.block_items[0]
    assert isinstance(reference.init, c_ast.ID)
    assert reference.init.name == reference.name


def test_nullptr_reference_conversion_evaluates_binding_without_loading_referent():
    tree = CppParser().parse('using N=decltype(nullptr);void f(){N n;N&r=n;int*p=r;r;}')
    function, = [node for node in tree.ext if isinstance(node, c_ast.FuncDef)]
    reference, pointer, discarded = function.body.block_items[1:]
    conversion = pointer.init
    while isinstance(conversion, c_ast.Cast):
        conversion = conversion.expr
    assert isinstance(conversion, c_ast.ExprList)
    address, null = conversion.exprs
    assert isinstance(address, c_ast.ID) and address.name == reference.name
    assert isinstance(null, c_ast.Cast) and isinstance(null.expr, c_ast.Constant)
    assert null.expr.value == '0'
    assert isinstance(discarded, c_ast.ID) and discarded.name == reference.name


def test_discarding_reference_does_not_invent_a_comma_constant_proof():
    class CaptureReturn(CppParser):
        def _return_statement(self, expression, coord):
            self.observed_return = expression
            return super()._return_statement(expression, coord)

    parser = CaptureReturn()
    parser.parse('using N=decltype(nullptr);int f(){N n;N&r=n;return (r,42);}')
    assert parser.observed_return.constant_fact.status != KNOWN
