"""Original constructor rules linked to N4659, not full class lifetime support.

https://timsong-cpp.github.io/cppwp/n4659/class.ctor
https://timsong-cpp.github.io/cppwp/n4659/class.base.init
https://timsong-cpp.github.io/cppwp/n4659/dcl.init
https://timsong-cpp.github.io/cppwp/n4659/stmt.dcl

Complete automatic objects with trivial scalar/pointer members; original
TinyXML2's inline constructor remains blocked by its real inline/ODR dependency.
"""

import pytest

from pcc.frontends.c.ast import c_ast
from pcc.frontends.cpp import CppDiagnostic, CppParser
from tests.cpp.owned_execution import compile_host_cpp
from tests.cpp.test_cpp_owned_execution import owned_only


CONSTRUCTOR_CASES = (
    ('cast-subscript-disambiguates-expression', 'struct S{int n;S(int);};S::S(int n):n(n){}'
     'int cpp_test_entry(){int a[1];a[0]=41;S s(int(a[0]+1));return s.n;}'),
    ('parenthesized-zero-cast-is-expression', 'struct S{int n;S(int);};S::S(int n):n(n+42){}'
     'int cpp_test_entry(){S s((int()));return s.n;}'),
    ('assignment-inside-cast-is-expression', 'struct S{int n;S(int);};S::S(int n):n(n){}'
     'int cpp_test_entry(){int x=0;S s(int(x=42));if(x!=42)return 1;return s.n;}'),
    ('functional-cast-argument', 'struct S{int n;S(int);};S::S(int n):n(n){}'
     'int cpp_test_entry(){S s(int(42));return s.n;}'),
    ('alias-cast-argument', 'using I=int;struct S{int n;S(int);};S::S(int n):n(n){}'
     'int cpp_test_entry(){S s(I(42));return s.n;}'),
    ('const-pointer-parameter', 'struct S{int*p;S(int*const);};S::S(int*const p):p(p){}'
     'int cpp_test_entry(){int n=42;S s(&n);return *s.p;}'),
    ('const-enum-parameter', 'enum E{Answer=42};struct S{int n;S(const E);};S::S(const E n):n(n){}'
     'int cpp_test_entry(){S s(Answer);return s.n;}'),
    ('destination-identity', 'struct S{int same;S(S*);};S::S(S*p):same(this==p){}'
     'int cpp_test_entry(){S s(&s);if(s.same!=1)return 1;return 42;}'),
    ('scalar-direct', 'struct S{int value;S(int);};S::S(int n):value(n){}'
     'int cpp_test_entry(){S s(42);return s.value;}'),
    ('default-body', 'struct S{int value;S();};S::S(){value=42;}'
     'int cpp_test_entry(){S s;return s.value;}'),
    ('const-subobject', 'struct S{const int value;S(int);};S::S(int n):value(n){}'
     'int cpp_test_entry(){const S s(42);return s.value;}'),
    ('pointer-subobject', 'struct S{int*p;S(int*);};S::S(int*p):p(p){}'
     'int cpp_test_entry(){int n=42;S s(&n);return *s.p;}'),
    ('overloads', 'struct S{int value;S(int);S(long);};'
     'S::S(int n):value(n){}S::S(long n):value(int(n)+1){}'
     'int cpp_test_entry(){S a(17);S b(24L);return a.value+b.value;}'),
    ('declaration-order', 'int next(int*p){*p=*p+1;return *p;}'
     'struct S{int a;int b;S(int*);};S::S(int*p):b(next(p)),a(next(p)){}'
     'int cpp_test_entry(){int n=0;S s(&n);if(n!=2)return 1;'
     'if(s.a!=1||s.b!=2)return 2;return 42;}'),
    ('initialized-member-reference', 'struct S{int a;int b;S(int);};'
     'S::S(int n):b(a+25),a(n){}int cpp_test_entry(){S s(17);return s.b;}'),
    ('empty-value-initializers', 'struct S{int a;int*p;S();};S::S():p(),a(){}'
     'int cpp_test_entry(){S s;if(s.a!=0||s.p!=nullptr)return 1;return 42;}'),
    ('constructor-argument-once', 'int argument(int*p){*p+=1;return 42;}'
     'struct S{int a;S(int);};S::S(int n):a(n){}'
     'int cpp_test_entry(){int calls=0;S s(argument(&calls));if(calls!=1)return 1;return s.a;}'),
    ('namespace-owner', 'namespace n{struct S{int x;S(int);};}'
     'n::S::S(int n):x(n){}int cpp_test_entry(){n::S s(42);return s.x;}'),
    ('private-field', 'class S{int x;public:S(int);int get()const;};'
     'S::S(int x):x(x){}int S::get()const{return x;}'
     'int cpp_test_entry(){S s(42);S*p=&s;return p->get();}'),
    ('private-constructor-in-owner', 'class S{S(int);public:int x;static int make();};'
     'S::S(int n):x(n){}int S::make(){S s(42);return s.x;}'
     'int cpp_test_entry(){return S::make();}'),
    ('for-init-storage-scope', 'struct S{int x;S(int);};S::S(int n):x(n){}'
     'int cpp_test_entry(){int n=0;for(S s(40);s.x<42;++s.x){++n;}return n+40;}'),
    ('nested-block-lifetime', 'struct S{int x;S(int);};S::S(int n):x(n){}'
     'int cpp_test_entry(){int n=0;{S s(17);n=s.x;}{S s(25);n+=s.x;}return n;}'),
    ('explicit-return', 'struct S{int x;S(int);};S::S(int n):x(n){return;}'
     'int cpp_test_entry(){S s(42);return s.x;}'),
    ('wide-and-floating', 'struct S{unsigned long long n;double d;S(unsigned long long,double);};'
     'S::S(unsigned long long n,double d):n(n),d(d){}'
     'int cpp_test_entry(){S s(8589934592ULL,42.0);if(s.n!=8589934592ULL)return 1;return int(s.d);}'),
    ('body-after-members', 'int tick(int*p){*p=*p*10+1;return 17;}'
     'struct S{int a;S(int*);};S::S(int*p):a(tick(p)){*p=*p*10+2;}'
     'int cpp_test_entry(){int n=0;S s(&n);if(n!=12)return 1;return s.a+25;}'),
    ('empty-class-constructor-effects', 'struct S{S(int*);};S::S(int*p){*p=42;}'
     'int cpp_test_entry(){int n=0;S s(&n);return n;}'),
    ('unnamed-parameter', 'struct S{int x;S(int,int);};S::S(int,int n):x(n){}'
     'int cpp_test_entry(){S s(11,42);return s.x;}'),
)


@pytest.mark.parametrize('name,source', CONSTRUCTOR_CASES, ids=[row[0] for row in CONSTRUCTOR_CASES])
def test_constructor_rules_lower_without_external_compiler(tmp_path, owned_only, name, source):
    units = ((name+'.cpp', source),)
    artifact = compile_host_cpp(units)
    repeated = compile_host_cpp(units)
    assert artifact.objects == repeated.objects
    assert artifact.executable == repeated.executable
    assert artifact.ir == repeated.ir
    assert artifact.objects


INVALID_CONSTRUCTORS = (
    ('struct S{S();S();};', 'cpp.redeclaration'),
    ('struct S{S();};S::S(int){}', 'cpp.no-matching-member'),
    ('struct S{S();};S::S(){}S::S(){}', 'cpp.redefinition'),
    ('struct S{const int x;S();};S::S():x(1){x=2;}', 'cpp.const-assignment'),
    ('struct S{S(int);};S::S(int n){}int f(){return n;}', 'cpp.undeclared'),
    ('struct S{S(int);};S::S(int n){int n=1;}', 'cpp.redeclaration'),
    ('struct S{S()const;};', 'cpp.constructor-cv'),
    ('struct S{int x;S();};S::S():x(1),x(2){}', 'cpp.duplicate-member-initializer'),
    ('struct S{int x;S();};S::S():missing(1){}', 'cpp.invalid-member-initializer'),
    ('struct S{const int x;S();};S::S(){}', 'cpp.const-initializer'),
    ('class S{S();};S::S(){}int f(){S s;return 0;}', 'cpp.member-access'),
    ('struct S{S(int);};int f(){S s;return 0;}', 'cpp.no-viable-overload'),
    ('struct S{int*p;S();};S::S():p(1){}', 'cpp.invalid-conversion'),
    ('struct S{S();};S::S(){return 1;}', 'cpp.constructor-return-value'),
    ('void f();struct S{S();};S::S(){return f();}', 'cpp.constructor-return-value'),
    ('struct S{S();};S::S(){}int f(){switch(1){case 1:S s;case 2:return 0;}return 0;}',
     'cpp.switch-jump-initialization'),
)


@pytest.mark.parametrize('source,code', INVALID_CONSTRUCTORS)
def test_constructor_invalid_programs_have_owned_diagnostics(source, code):
    with pytest.raises(CppDiagnostic) as error:
        CppParser().parse(source, 'constructor-invalid.cpp')
    assert error.value.code == code


UNSUPPORTED_CONSTRUCTORS = (
    'struct S{S(int);};int f(){S s();return 0;}',
    'struct S{S(int);};int f(){S s(int());return 0;}',
    'struct S{S(int);};int f(){int x=42;S s(int(x));return 0;}',
    'struct S{S(int);};int f(){S s(int(*p)());return 0;}',
    'struct S{S(int);};int f(){S s(int(a[1]));return 0;}',
    'struct S{S(int);};int f(){S s(int(a[1+1]));return 0;}',
    'struct S{S();};S::S():S(){}',
    # A single base is owned now; a virtual base keeps the lifetime boundary.
    'struct B{};struct S:virtual B{S();};',
    'struct A{int x;};struct S{A a;S();};',
    'struct S{S();};S::S(){}int f(){S s;S t=s;return 0;}',
    'struct S{S();};struct A{S s;};int f(){A a;return 0;}',
    'struct S{S();};struct A{S s;};int f(){A a{};return 0;}',
    'struct S{S();};S::S(){}int f(){S s[2];return 0;}',
    'struct S{S();};S::S(){}S global;',
)


@pytest.mark.parametrize('source', UNSUPPORTED_CONSTRUCTORS)
def test_constructor_unimplemented_lifetimes_fail_closed(source):
    with pytest.raises(CppDiagnostic) as error:
        CppParser().parse(source, 'constructor-boundary.cpp')
    assert error.value.code.startswith('cpp.unsupported.')


def test_original_constructor_declaration_is_preserved_as_positive():
    ast = CppParser().parse('struct S{S();};')
    assert ast.ext[1].name == '_ZN1SC1Ev'
    assert len(ast.ext[1].type.args.params) == 1


def test_original_empty_direct_list_constructor_is_preserved_as_positive():
    # Exact formerly rejected valid source; the direct-list corpus supplies
    # its native/reference observer without replacing this original input.
    ast = CppParser().parse('struct S{S();};S::S(){}int f(){S s{};return 0;}')
    assert ast.ext[-1].decl.name == '_Z1fv'


def test_omitted_scalar_initialization_does_not_invent_zero_stores():
    ast = CppParser().parse('struct S{int untouched;int zero;S();};S::S():zero(){}')
    body = ast.ext[-1].body.block_items
    assert len(body) == 1
    assert isinstance(body[0], c_ast.Assignment)
    assert body[0].lvalue.field.name == 'zero'
    assert body[0].rvalue.value == '0'


def test_default_argument_after_parameter_declarator_remains_function_declaration():
    source = 'struct S{S(int);};int f(){int x=42;S s(int(x)=0);return 0;}'
    with pytest.raises(CppDiagnostic) as error:
        CppParser().parse(source, 'constructor-vexing-default.cpp')
    assert error.value.code == 'cpp.unsupported.block-function-declarations'
