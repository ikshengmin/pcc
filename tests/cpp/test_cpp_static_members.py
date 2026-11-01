"""Original static-member regressions, C++17 N4659, not full class support.

Specification anchors:
https://timsong-cpp.github.io/cppwp/n4659/class.static
https://timsong-cpp.github.io/cppwp/n4659/class.static.mfct
https://timsong-cpp.github.io/cppwp/n4659/over.load

A qualified/unqualified static call has no implicit object parameter. An
object-qualified call still evaluates its base before the explicit arguments.
Mixed static/non-static overload sets and inline/static-data ODR are separately
reported missing capabilities, never silently rewritten to free functions.
"""

import re

import pytest

from pcc.frontends.c.ast import c_ast
from pcc.frontends.cpp import CppDiagnostic, CppParser
from tests.cpp.owned_execution import compile_host_cpp
from tests.cpp.test_cpp_owned_execution import owned_only


# All originals are positive programs; reference and owned execution receive
# identical source text. The smallest declaration was formerly UNSUPPORTED in
# test_cpp_member_functions.py and is retained verbatim as a positive below.
ORIGINAL_STATIC_DECLARATION = 'struct S{static int f();};'
STATIC_CASES = (
    ('qualified-no-object', 'struct S{static int f();};int S::f(){return 42;}'
     'int cpp_test_entry(){return S::f();}'),
    ('specifier-after-type', 'struct S{int static f(int);};int S::f(int n){return n;}'
     'int cpp_test_entry(){return S::f(42);}'),
    ('explicit-overloads', 'struct S{static int f(int);static int f(long);};'
     'int S::f(int n){return n;}int S::f(long n){return 1;}'
     'int cpp_test_entry(){return S::f(42);}'),
    ('void-call-pointer-output', 'struct S{static void set(int*,int);};'
     'void S::set(int*p,int n){*p=n;}int cpp_test_entry(){int n=0;S::set(&n,42);return n;}'),
    ('private-static-in-static-body', 'class S{static int hidden(int);public:static int f();};'
     'int S::hidden(int n){return n;}int S::f(){return hidden(42);}'
     'int cpp_test_entry(){return S::f();}'),
    ('recursive-static-call', 'struct S{static int sum(int);};'
     'int S::sum(int n){if(n==0)return 0;return n+sum(n-1);}'
     'int cpp_test_entry(){return S::sum(8)+6;}'),
    ('class-alias-in-parameter', 'struct S{using I=int;static I f(I);};'
     'S::I S::f(I n){return n;}int cpp_test_entry(){return S::f(42);}'),
    ('private-return-alias', 'class S{using I=int;public:static I f();};'
     'S::I S::f(){return 42;}int cpp_test_entry(){return S::f();}'),
    ('namespace-qualified', 'namespace n{struct S{static int f(int);};}'
     'int n::S::f(int n){return n;}int cpp_test_entry(){return n::S::f(42);}'),
    ('uninitialized-dot-no-value-read', 'struct S{int uninitialized;static int f();};'
     'int S::f(){return 42;}int cpp_test_entry(){S s;return s.f();}'),
    ('const-dot-static-call', 'struct S{int x;static int f();};int S::f(){return 42;}'
     'int cpp_test_entry(){const S s{};return s.f();}'),
    ('no-this-register-shift', 'struct S{static int sum(int,int,int,int,int,int,int,int);};'
     'int S::sum(int a,int b,int c,int d,int e,int f,int g,int h)'
     '{return a+b+c+d+e+f+g+h;}int cpp_test_entry(){return S::sum(1,2,3,4,5,6,7,14);}'),
    ('floating-no-object-register', 'struct S{static double f(double);};'
     'double S::f(double n){return n+17.0;}int cpp_test_entry(){return int(S::f(25.0));}'),
    ('wide-result', 'struct S{static unsigned long long f(unsigned long long);};'
     'unsigned long long S::f(unsigned long long n){return n+4294967296ULL;}'
     'int cpp_test_entry(){if(S::f(8589934592ULL)!=12884901888ULL)return 1;return 42;}'),
    ('member-enum-type-and-value', 'struct S{enum E{A=17};static int f(E);};'
     'int S::f(E n){return n+25;}int cpp_test_entry(){return S::f(S::A);}'),
    ('same-explicit-signature-different-class', 'struct A{static int f(int);};'
     'struct B{static int f(int);};int A::f(int n){return n;}int B::f(int n){return n;}'
     'int cpp_test_entry(){return A::f(17)+B::f(25);}'),
    ('enclosing-c-linkage-does-not-change-members', 'extern "C"{struct S{static int f(int);};}'
     'int S::f(int n){return n;}int cpp_test_entry(){return S::f(42);}'),
    ('parameter-hides-static-name', 'struct S{static int f(int);static int g(int f);};'
     'int S::f(int n){return n;}int S::g(int f){return S::f(f);}'
     'int cpp_test_entry(){return S::g(42);}'),
)

INVALID_STATIC_CASES = (
    ('struct S{static int f()const;};', 'cpp.static-member-cv'),
    ('struct S{static int f();};int S::f()const{return 0;}', 'cpp.static-member-cv'),
    ('struct S{static int f();int f();};', 'cpp.static-nonstatic-overload'),
    ('struct S{int f();static int f();};', 'cpp.static-nonstatic-overload'),
    ('struct S{int f()const;static int f();};', 'cpp.static-nonstatic-overload'),
    ('struct S{static int f();};static int S::f(){return 0;}', 'cpp.invalid-member-storage'),
    ('struct S{static int f();};int S::f(){return this==nullptr;}', 'cpp.this-outside-member'),
    ('struct S{int x;static int f();};int S::f(){return x;}', 'cpp.nonstatic-member-without-object'),
    ('struct S{int g();static int f();};int S::f(){return g();}', 'cpp.nonstatic-member-without-object'),
    ('struct S{static int f();static int f();};', 'cpp.redeclaration'),
    ('struct S{static int f();};long S::f(){return 0;}', 'cpp.conflicting-return-type'),
    ('struct S{static int f(int);};int S::f(long n){return 0;}', 'cpp.no-matching-member'),
    ('struct S{static int f();};int S::f(){return 1;}int S::f(){return 2;}', 'cpp.redefinition'),
    ('class S{static int f();};int S::f(){return 42;}int cpp_test_entry(){return S::f();}', 'cpp.member-access'),
    ('class S{static int f(int);public:static int f(long);};int run(){return S::f(1);}', 'cpp.member-access'),
    ('struct S{extern int f();};', 'cpp.invalid-member-storage'),
)

DEFERRED_STATIC_CASES = (
    ('struct S{static int f(){return 42;}};', 'cpp.unsupported.inline-member-functions'),
    ('struct S{static int value;};', 'cpp.unsupported.static-data-members'),
    ('struct S{static int f(int);int f(long);};', 'cpp.unsupported.mixed-static-overloads'),
    ('struct S{int f(int);static int f(long);};', 'cpp.unsupported.mixed-static-overloads'),
    ('struct S{static int f();};template<class T>int run(T n){return S::f();}', 'cpp.unsupported.member-templates'),
)


def test_original_static_declaration_has_no_implicit_object_parameter():
    parser = CppParser()
    unit = parser.parse(ORIGINAL_STATIC_DECLARATION)
    record, function = unit.ext
    assert isinstance(record.type, c_ast.Struct)
    assert function.name == '_ZN1S1fEv'
    assert isinstance(function.type, c_ast.FuncDecl)
    assert function.type.args.params == []
    method = parser.scope.lookup('S').dtype.definition.scope.names['f'].overloads[0]
    assert method.method_static and not method.method_const
    assert method.parameters == ()


@pytest.mark.parametrize('name,source', STATIC_CASES, ids=[row[0] for row in STATIC_CASES])
def test_static_owned_object_is_deterministic(owned_only, name, source):
    units = ((name + '.cpp', source),)
    assert compile_host_cpp(units) == compile_host_cpp(units)


@pytest.mark.parametrize('source,code', INVALID_STATIC_CASES + DEFERRED_STATIC_CASES)
def test_static_invalid_or_deferred_is_explicit_before_emission(source, code, monkeypatch):
    from pcc.backend import owned_object_emit

    def forbidden(*args, **kwargs):
        raise AssertionError('invalid static-member semantics reached object emission')

    monkeypatch.setattr(owned_object_emit, 'emit_owned_object', forbidden)
    with pytest.raises(CppDiagnostic) as error:
        compile_host_cpp((('static-negative.cpp', source),))
    assert error.value.code == code


def test_static_dot_call_does_not_read_indeterminate_object():
    source = dict(STATIC_CASES)['uninitialized-dot-no-value-read']
    artifact = compile_host_cpp((('static-dot.cpp', source),))
    assert not re.search(r'\bload\s+(?:%"[^\"]+"|%[\w.$]+)\s*,', '\n'.join(artifact.ir))


def test_tinyxml2_original_static_declaration_shapes_are_admitted():
    # Exact declaration lines from the checked-in upstream tinyxml2.h, not
    # rewritten implementations or a claim that the original TU now compiles.
    from pathlib import Path

    header = (Path(__file__).parents[2] / 'projects/tinyxml2-11.0.0/tinyxml2.h').read_text()
    declarations = (
        'static const char* ReadBOM( const char* p, bool* hasBOM );',
        'static void ConvertUTF32ToUTF8( unsigned long input, char* output, int* length );',
        'static void ToStr( int v, char* buffer, int bufferSize );',
        'static void ToStr( unsigned v, char* buffer, int bufferSize );',
        'static void ToStr( bool v, char* buffer, int bufferSize );',
        'static void ToStr( float v, char* buffer, int bufferSize );',
        'static void ToStr( double v, char* buffer, int bufferSize );',
    )
    assert all(declaration in header for declaration in declarations)
    parser = CppParser()
    parser.parse('class XMLUtil{public:' + ''.join(declarations) + '};')
    scope = parser.scope.lookup('XMLUtil').dtype.definition.scope
    assert len(scope.names['ToStr'].overloads) == 5
    assert all(method.method_static for group in (scope.names['ReadBOM'], scope.names['ConvertUTF32ToUTF8'], scope.names['ToStr'])
               for method in group.overloads)
