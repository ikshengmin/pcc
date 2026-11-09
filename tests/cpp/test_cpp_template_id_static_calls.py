"""Static member calls through a nondependent class template-id.

N4659 [temp.names]/1, [basic.lookup.qual]/1, [class.static.mfct] and
[class.mfct.non-static]/1. ``S<int>::f(args)`` names one specialization and
calls its static member; other members named this way are invalid or
explicit gaps. Every program returns 42 from cpp_test_entry.
"""

import pytest

from pcc.frontends.cpp import CppDiagnostic
from tests.cpp.owned_execution import compile_host_cpp
from tests.cpp.test_cpp_owned_execution import owned_only


CASES = (
    ('two-specializations',
     'template<class T>struct S{static T twice(T v){return v+v;}};'
     'int cpp_test_entry(){return S<int>::twice(20)+(int)S<long>::twice(1);}'),
    ('namespace-qualified-template',
     'namespace N{template<class T>struct S{static T same(T v){return v;}};}'
     'int cpp_test_entry(){return N::S<int>::same(42);}'),
    ('value-template-argument',
     'template<int V>struct K{static int get(){return V;}};int cpp_test_entry(){return K<40>::get()+K<2>::get();}'),
    ('pointer-local-in-static-member',
     'template<class T>struct S{static T f(T value){T*local=&value;return*local;}};'
     'int cpp_test_entry(){return S<int>::f(42);}'),
)
PROGRAMS = tuple((name, ((name + '.cpp', source),)) for name, source in CASES)
INVALID = (
    ('nonstatic-member-without-object',
     'template<class T>struct S{T get(){return 1;}};int cpp_test_entry(){return S<int>::get();}',
     'cpp.nonstatic-member-without-object'),
    ('missing-member',
     'template<class T>struct S{static T f(T v){return v;}};int cpp_test_entry(){return S<int>::g(1);}',
     'cpp.no-member'),
)
DEFERRED = (
    ('functional-cast-of-template-id',
     'template<class T>struct S{T v;S(T x):v(x){}};int cpp_test_entry(){return S<int>(42).v;}',
     'cpp.unsupported.class-initialization'),
)


@pytest.mark.parametrize('name,units', PROGRAMS, ids=[row[0] for row in PROGRAMS])
def test_template_id_static_call_artifacts_are_stable(owned_only, name, units):
    assert compile_host_cpp(units) == compile_host_cpp(units)


@pytest.mark.parametrize('name,source,code', INVALID + DEFERRED, ids=[row[0] for row in INVALID + DEFERRED])
def test_template_id_member_boundaries_precede_ir(owned_only, name, source, code, monkeypatch):
    from pcc.frontends.c.codegen.c_codegen import CCodeGenerator
    def forbidden(*args, **kwargs):
        raise AssertionError('invalid or deferred template-id member reached IR generation')
    monkeypatch.setattr(CCodeGenerator, 'generate_code', forbidden)
    with pytest.raises(CppDiagnostic) as caught:
        compile_host_cpp(((name + '.cpp', source),))
    assert caught.value.code == code
