"""Checked arithmetic/enum casts; not pointer reinterpretation or class casts.

Original PCC cases for N4659 [expr.cast], [expr.static.cast], [conv.integral],
[conv.fpint], [expr.const]. Explicit conversions are not list initialization;
source expressions/nominal enum identity remain intact until typed lowering.
https://www.open-std.org/jtc1/sc22/wg21/docs/papers/2017/n4659.pdf
"""
import pytest

from pcc.frontends.cpp import CppDiagnostic, CppParser
from tests.cpp.owned_execution import compile_host_cpp
from tests.cpp.test_cpp_class_execution import execute_and_record


CASES = (
    ('c-style-integer', 'int cpp_test_entry(){long b=42L;return (int)b;}'),
    ('c-style-float-truncation', 'int cpp_test_entry(){double b=42.75;return (int)b;}'),
    ('c-style-negative-float-truncation', 'int cpp_test_entry(){double b=-42.75;return -(int)b;}'),
    ('c-style-integer-to-float', 'int cpp_test_entry(){int b=21;double x=(double)b;return (int)(x*2.0);}'),
    ('c-style-multiword-type', 'int cpp_test_entry(){unsigned int x=0xffffffffU;'
     'if((unsigned long long)x!=4294967295ULL)return 1;return 42;}'),
    ('c-style-unsigned-wrap', 'int cpp_test_entry(){int x=-1;unsigned char b=(unsigned char)x;return (int)b-213;}'),
    ('c-style-const-target-prvalue', 'int cpp_test_entry(){long x=42L;return (const int)x;}'),
    ('typedef-type-id', 'using I=int;int cpp_test_entry(){double x=42.5;return (I)x;}'),
    ('qualified-type-id', 'namespace types{using I=int;}int cpp_test_entry(){double x=42.5;return (types::I)x;}'),
    ('shadowed-type-is-parenthesized-value', 'using I=int;int cpp_test_entry(){int I=17;return (I)+25;}'),
    ('parenthesized-functional-conversion', 'int cpp_test_entry(){return (int(42));}'),
    ('cast-precedence-before-multiply', 'int cpp_test_entry(){double x=21.9;return (int)x*2;}'),
    ('cast-of-postfix-result-once', 'int cpp_test_entry(){int x=41;int y=(int)x++;if(x!=42)return 1;return y+1;}'),
    ('c-style-void-retains-side-effect', 'int cpp_test_entry(){int x=17;(void)(x+=25);return x;}'),
    ('static-void-retains-side-effect', 'int cpp_test_entry(){int x=17;static_cast<void>(x+=25);return x;}'),
    ('void-of-void', 'void nothing(){}int cpp_test_entry(){(void)nothing();static_cast<void>(nothing());return 42;}'),
    ('old-static-cast-source-unchanged', 'int f(){return static_cast<int>(1.5);}int cpp_test_entry(){return f()+41;}'),
    ('static-float-to-int', 'int cpp_test_entry(){double x=42.75;return static_cast<int>(x);}'),
    ('static-int-to-double', 'int cpp_test_entry(){int x=42;double y=static_cast<double>(x);return int(y);}'),
    ('scoped-enum-static-cast', 'enum class K{Answer=42};int cpp_test_entry(){K k=K::Answer;return static_cast<int>(k);}'),
    ('unscoped-enum-static-cast', 'enum K{Answer=42};int cpp_test_entry(){K k=Answer;return static_cast<int>(k);}'),
    ('scoped-enum-c-style', 'enum class K{Answer=42};int cpp_test_entry(){return (int)K::Answer;}'),
    ('enum-identity-cast-keeps-overload', 'enum class K{Answer=42};int take(K k){return int(k);}int take(int x){return 1;}'
     'int cpp_test_entry(){return take(static_cast<K>(K::Answer));}'),
    ('enum-to-bool', 'enum class K{Zero,One};int cpp_test_entry(){if(static_cast<bool>(K::Zero))return 1;'
     'if(!static_cast<bool>(K::One))return 2;return 42;}'),
    ('enum-full-width', 'enum class K:unsigned long long{Value=0xfedcba9876543210ULL};'
     'int cpp_test_entry(){K k=K::Value;if(static_cast<unsigned long long>(k)!=0xfedcba9876543210ULL)return 1;return 42;}'),
    ('enum-to-float', 'enum class K{Answer=42};int cpp_test_entry(){double x=static_cast<double>(K::Answer);return int(x);}'),
    ('enum-cast-constant-bound', 'enum class K{Count=2};int cpp_test_entry(){const int n=static_cast<int>(K::Count);'
     'int a[n]={17,25};return a[0]+a[1];}'),
    ('c-style-constant-bound', 'int cpp_test_entry(){const int n=(int)2L;int a[n]={17,25};return a[0]+a[1];}'),
    ('cast-call-evaluated-once', 'double value(int*count){*count=*count+1;return 42.9;}'
     'int cpp_test_entry(){int count=0;int x=(int)value(&count);if(count!=1)return 1;return x;}'),
)

INVALID = (
    ('int f(){int x=1;(int)x=2;return x;}', 'cpp.not-lvalue'),
    ('int f(){int*p=(int)0;return 0;}', 'cpp.invalid-conversion'),
    ('int f(){int*p=static_cast<int>(0);return 0;}', 'cpp.invalid-conversion'),
    ('void nothing();int f(){return (int)nothing();}', 'cpp.void-value'),
    ('void nothing();int f(){return static_cast<int>(nothing());}', 'cpp.void-value'),
)

DEFERRED = (
    ('int f(){int x=0;return static_cast<int>(&x);}', 'cpp.unsupported.static-cast'),
    ('int f(){int x=0;return (long long)&x;}', 'cpp.unsupported.c-style-cast'),
    ('int f(){void*p=nullptr;int*q=(int*)p;return 0;}', 'cpp.unsupported.c-style-cast'),
    ('enum class K{A};int f(){K k=static_cast<K>(0);return 0;}', 'cpp.unsupported.enum-casts'),
    ('enum class K{A};int f(){K k=(K)0;return 0;}', 'cpp.unsupported.enum-casts'),
    ('struct R{};int f(){R r{};return (int)r;}', 'cpp.unsupported.class-conversions'),
    ('template<class T>T f(T x){return static_cast<T>(x);}', 'cpp.unsupported.static-cast'),
    ('template<class T>int f(T x){return (int)x;}', 'cpp.unsupported.c-style-cast'),
)


@pytest.mark.parametrize('name,source', CASES, ids=[case[0] for case in CASES])
def test_arithmetic_casts_compile_twice_identically(name, source):
    units = ((name + '.cpp', source),)
    assert compile_host_cpp(units) == compile_host_cpp(units)


@pytest.mark.parametrize('source,code', INVALID + DEFERRED)
def test_cast_diagnostics_precede_emission(source, code, monkeypatch):
    from pcc.backend import owned_object_emit
    def forbidden(*args, **kwargs):
        raise AssertionError('unsupported cast reached object emission')
    monkeypatch.setattr(owned_object_emit, 'emit_owned_object', forbidden)
    with pytest.raises(CppDiagnostic) as error:
        compile_host_cpp((('cast-negative.cpp', source),))
    assert error.value.code == code


@pytest.mark.integration
@pytest.mark.parametrize('name,source', CASES, ids=[case[0] for case in CASES])
def test_arithmetic_casts_execute_against_explicit_cpp17_reference(name, source, tmp_path, monkeypatch):
    execute_and_record(tmp_path, monkeypatch, ((name + '.cpp', source),), 42)
