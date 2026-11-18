"""Non-deduced parameters and explicit pointer substitution at a call site."""
import os
import shutil
import pytest
from pcc.frontends.cpp import CppDiagnostic, CppParser
from tests.cpp.owned_execution import (
    Execution, assert_exact, compile_host_cpp, compile_reference_cpp,
    run_executable, write_host_executable,
)


CASES = (
    ('undeduced-value-not-viable',
     'struct S{template<class T,int N> int f(T x){return N;}'
     'template<class T> int f(T x){return int(x);}};'
     'int cpp_test_entry(){S s;return s.f(42);}'),
    ('explicit-pointer-does-not-use-deduction',
     'struct S{template<class T,class U>int f(T* p,U x){return int(x);}};'
     'int cpp_test_entry(){S s;int i;return s.f<void>(&i,42);}'),
)


@pytest.mark.parametrize('name,source', CASES)
def test_member_template_explicit_binding_accepts_program(name, source):
    CppParser('x86_64-unknown-linux-gnu').parse(source, name + '.cpp')


@pytest.mark.integration
@pytest.mark.parametrize('name,source', CASES)
def test_selected_viable_specialization_executes(name, source, tmp_path):
    units = ((name + '.cpp', source),)
    compiler = shutil.which(os.environ.get('PCC_CPP_ORACLE', 'g++'))
    assert compiler, 'explicit G++ C++17 reference required'
    reference = compile_reference_cpp(units, tmp_path / 'reference', compiler=compiler)
    owned = tmp_path / 'owned'
    write_host_executable(compile_host_cpp(units), owned)
    expected = Execution(42, b'\x2a\x00\x00\x00', b'')
    assert_exact(run_executable(reference), expected)
    assert_exact(run_executable(owned), expected)


def test_all_candidates_with_undeduced_nontype_argument_are_nonviable():
    text = ('struct S{template<class T,int N>int f(T x){return N;}};'
            'int main(){S s;return s.f(42);}')
    with pytest.raises(CppDiagnostic) as error:
        CppParser('x86_64-unknown-linux-gnu').parse(text, 'missing.cpp')
    assert error.value.code == 'cpp.no-viable-overload'
