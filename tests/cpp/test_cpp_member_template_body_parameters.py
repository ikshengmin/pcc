"""Source definition parameter cv and implicit-object availability survive replay."""
import os
import shutil

import pytest
from pcc.frontends.cpp import CppDiagnostic, CppParser
from tests.cpp.owned_execution import (
    Execution, assert_exact, compile_host_cpp, compile_reference_cpp,
    run_executable, write_host_executable,
)

CASES = (
    ('const-value-overload',
     'struct S{int g(int& x){return 1;}int g(const int& x){return 42;}'
     'template<class T>int f(const T x){return g(x);}};'
     'int cpp_test_entry(){S s;return s.f(0);}'),
    ('contrived-object-only-for-ranking',
     'struct S{static int h(int);int h(double);'
     'template<class T>static int f(T x){return h(x);}};'
     'int S::h(int x){return x;}int cpp_test_entry(){return S::f<int>(42);}'),
)


@pytest.mark.integration
@pytest.mark.parametrize('name,source', CASES)
def test_original_parameter_and_object_context_execute(name, source, tmp_path):
    units = ((name + '.cpp', source),)
    compiler = shutil.which(os.environ.get('PCC_CPP_ORACLE', 'g++'))
    assert compiler
    reference = compile_reference_cpp(units, tmp_path / 'reference', compiler=compiler)
    expected = Execution(42, b'\x2a\x00\x00\x00', b'')
    assert_exact(run_executable(reference), expected)
    owned = tmp_path / 'owned'
    write_host_executable(compile_host_cpp(units), owned)
    assert_exact(run_executable(owned), expected)


def test_contrived_object_does_not_make_nonstatic_winner_callable():
    text = ('struct S{static int h(double);int h(int);'
            'template<class T>static int f(T x){return h(x);}};'
            'int main(){return S::f<int>(42);}')
    with pytest.raises(CppDiagnostic) as error:
        CppParser('x86_64-unknown-linux-gnu').parse(text, 'no-this.cpp')
    assert error.value.code == 'cpp.nonstatic-member-without-object'


def test_const_value_parameter_has_no_assignable_binding_after_substitution():
    text = ('struct S{template<class T>int f(const T x){x=42;return x;}};'
            'int main(){S s;return s.f(0);}')
    with pytest.raises(CppDiagnostic) as error:
        CppParser('x86_64-unknown-linux-gnu').parse(text, 'const-object.cpp')
    assert error.value.code == 'cpp.const-assignment'
