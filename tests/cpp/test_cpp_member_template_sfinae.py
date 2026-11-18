"""Source tests for direct signature SFINAE, never mocked candidate diagnostics."""
import os
import shutil
import subprocess
import pytest
from pcc.frontends.cpp import CppParser, CppDiagnostic

TARGET = 'x86_64-unknown-linux-gnu'

POSITIVE = (
    'struct D { template<class T> T& f(int); template<class T> int f(long){return 7;} };'
    'int use(){D d;return d.f<void>(0);}',
    'struct D { template<class T> const T& f(int); template<class T> int f(long){return 7;} };'
    'int use(){D d;return d.f<void>(0);}',
)
NEGATIVE = (
    ('struct D {template<class T>T& f(int);};int use(){D d;d.f<void>(0);return 0;}',
     'cpp.no-viable-overload'),
    ('struct D {template<class T>void& f(int);};', 'cpp.invalid-reference-type'),
)

@pytest.mark.parametrize('source', POSITIVE)
def test_direct_reference_substitution_failure_keeps_viable_member(source):
    CppParser(TARGET).parse(source, 'sfinae.cpp')

@pytest.mark.parametrize('source,code', NEGATIVE)
def test_signature_sfinae_does_not_make_invalid_program_valid(source, code):
    with pytest.raises(CppDiagnostic) as error:
        CppParser(TARGET).parse(source, 'sfinae.cpp')
    assert error.value.code == code

@pytest.mark.integration
@pytest.mark.parametrize('source,accepted', [(s, True) for s in POSITIVE] + [(s, False) for s,c in NEGATIVE])
def test_same_sfinae_source_gcc_cpp17_oracle(source, accepted):
    compiler = shutil.which(os.environ.get('PCC_CPP_ORACLE', 'g++'))
    assert compiler, 'explicit C++17 reference required'
    result = subprocess.run([compiler, '-std=c++17', '-pedantic-errors',
                             '-fsyntax-only', '-x', 'c++', '-'],
                            input=source, text=True, capture_output=True)
    assert (result.returncode == 0) is accepted, result.stderr

HARD_BODY_ERROR = (
    'struct D {template<class T>int f(int){return missing_name;} '
    'template<class T>int f(long){return 7;}};'
    'int use(){D d;return d.f<void>(0);}'
)

def test_nondependent_body_error_is_not_candidate_substitution_failure():
    with pytest.raises(CppDiagnostic) as error:
        CppParser(TARGET).parse(HARD_BODY_ERROR, 'hard-error.cpp')
    assert error.value.code != 'cpp.no-viable-overload'
    assert 'missing_name' in str(error.value)

@pytest.mark.integration
def test_body_error_is_also_hard_in_gcc():
    compiler = shutil.which(os.environ.get('PCC_CPP_ORACLE', 'g++'))
    assert compiler, 'explicit C++17 reference required'
    result = subprocess.run([compiler,'-std=c++17','-pedantic-errors',
                             '-fsyntax-only','-x','c++','-'], input=HARD_BODY_ERROR,
                            text=True,capture_output=True)
    assert result.returncode != 0
    assert 'missing_name' in result.stderr
