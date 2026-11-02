"""Real original TU obligations for the combined C1 + shared ODR candidate.

These are not sema-only fallbacks. Every integration case requires actual owned
linking/execution plus explicit genuine G++17 compilation of the same TU bytes.
Until shared ODR capability lands these tests fail closed, not skip/xfail.
"""
import pytest

from tests.cpp.owned_execution import compile_host_cpp
from tests.cpp.test_cpp_class_execution import execute_and_record
from tests.cpp.test_cpp_inline_constructors import INLINE_PROGRAMS, UNSIGNED_LATER_METHOD
from tests.cpp.test_cpp_owned_execution import owned_only


SINGLE_PROGRAMS = INLINE_PROGRAMS + (
    ('unsigned-later-method', UNSIGNED_LATER_METHOD),
    ('dmi-shadowing', 'int seed=40;struct S{S(int seed):a(seed){} int a=1;int b=seed+2;};int cpp_test_entry(){S s(9);if(s.a!=9)return 1;return s.b;}'),
    ('later-private-method', 'struct S{S(int x):n(adjust(x)){}private:int adjust(int);public:int n;};int S::adjust(int x){return x+2;}int cpp_test_entry(){S s(40);return s.n;}'),
    ('normal-local-cleanup', 'int count=0;struct Guard{~Guard();};Guard::~Guard(){count=count+1;}struct S{S(){Guard a;Guard b;}int n;};int cpp_test_entry(){S s;if(count!=2)return 1;return 42;}'),
    ('void-return-cleanup', 'int count=0;struct Guard{~Guard();};Guard::~Guard(){count=count+1;}struct S{S(){Guard a;return;}int n;};int cpp_test_entry(){S s;if(count!=1)return 1;return 42;}'),
)

SHARED_CLASS = 'struct S{S(int x):n(x){} int n;};'
MULTI_PROGRAMS = (
    ('two-tu-same-c1', (('provider.cpp', SHARED_CLASS+'int read(){S s(20);return s.n;}'),
                       ('entry.cpp', SHARED_CLASS+'int read();int cpp_test_entry(){S s(22);return s.n+read();}'))),
)
PROGRAMS = tuple((name, ((name+'.cpp', source),)) for name, source in SINGLE_PROGRAMS) + MULTI_PROGRAMS


@pytest.mark.parametrize('name,units', PROGRAMS, ids=[row[0] for row in PROGRAMS])
def test_inline_c1_original_artifacts_are_byte_stable(owned_only, name, units):
    first = compile_host_cpp(units)
    assert first == compile_host_cpp(units)


@pytest.mark.integration
@pytest.mark.parametrize('name,units', PROGRAMS, ids=[row[0] for row in PROGRAMS])
def test_inline_c1_originals_execute_against_real_cpp17_reference(name, units, tmp_path, monkeypatch):
    execute_and_record(tmp_path, monkeypatch, units, 42)
