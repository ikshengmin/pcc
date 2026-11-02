"""Owned declaration acquisition; unsupported C++ types are not erased to admit it.

N4659 [cstdlib.syn]. This file does not qualify runtime stdlib implementation.
"""
from pathlib import Path
import re
import pytest

from pcc.frontends.cpp import CppDiagnostic, CppParser
from pcc.frontends.cpp.driver import preprocess_cpp

ROOT = Path(__file__).resolve().parents[2]
HEADER = ROOT/'pcc/frontends/cpp/include/cstdlib'


def test_cstdlib_preprocessing_is_owned_and_byte_stable(monkeypatch):
    import subprocess
    def forbidden(*args, **kwargs):
        raise AssertionError('owned header preprocessing launched an external process')
    monkeypatch.setattr(subprocess, 'Popen', forbidden)
    source = '#include <cstdlib>\n#include <cstdlib>\n'
    first = preprocess_cpp(source, target_triple='x86_64-unknown-linux-gnu')
    assert first == preprocess_cpp(source, target_triple='x86_64-unknown-linux-gnu')
    assert first.count('using __pcc_c_exit_handler') == 1
    assert re.search(r'char\s*\*\s*\*', first)


def test_cstdlib_does_not_strip_unsupported_declaration_semantics():
    text = HEADER.read_text()
    for declaration in ('using __pcc_c_exit_handler = void();',
                        'using __pcc_cpp_exit_handler = void();',
                        '[[noreturn]] void abort() noexcept;',
                        'int atexit(__pcc_c_exit_handler*) noexcept;',
                        'int atexit(__pcc_cpp_exit_handler*) noexcept;',
                        'double strtod(const char*, char**);',
                        'int mbtowc(wchar_t*, const char*, size_t);',
                        'div_t div(int, int);', 'ldiv_t div(long, long);'):
        assert declaration in text
    assert 'typedef int div_t' not in text
    assert not re.search(r'\b(?:atexit|at_quick_exit)\s*\(\s*void\s*\*', text)


def test_cstdlib_has_complete_typed_declarations_not_runtime_implementations():
    source = preprocess_cpp('#include <cstdlib>\n', target_triple='x86_64-unknown-linux-gnu')
    parser = CppParser()
    parser.parse(source, 'cstdlib-declarations.cpp')
    assert parser.scope.names['strtold'].overloads[0].dtype == 'long double'
    assert parser.scope.names['div'].overloads[0].dtype is parser.scope.names['div_t'].dtype
    assert parser.scope.names['abort'].overloads[0].nonthrowing
    assert parser.scope.names['abort'].overloads[0].noreturn
    assert len(parser.scope.names['std'].namespace_scope.names['atexit'].overloads) == 2
    assert not any(s.defined for name in ('malloc','strtod','div','abort')
                   for s in parser.scope.names[name].overloads)


@pytest.mark.parametrize('name', ['RAND_MAX','MB_CUR_MAX'])
def test_unimplemented_random_or_locale_policy_is_not_fake_numeric_value(name):
    with pytest.raises(RuntimeError, match='failed to evaluate'):
        preprocess_cpp('#include <cstdlib>\n#if '+name+'\nint x;\n#endif\n',
                       target_triple='x86_64-unknown-linux-gnu')


@pytest.mark.parametrize('tail,code',[
    ('int main(){std::abort();return 0;}', 'cpp.unsupported.exceptions'),
    ('int main(){std::div(17,2);return 0;}', 'cpp.unsupported.class-value-abi'),
    ('int main(){std::strtold(nullptr,nullptr);return 0;}', 'cpp.unsupported.extended-float'),
    ('int main(){return RAND_MAX;}', 'cpp.incomplete-class'),
])
def test_header_acquisition_does_not_erase_unsupported_runtime_contracts(tail,code):
    source=preprocess_cpp('#include <cstdlib>\n'+tail,target_triple='x86_64-unknown-linux-gnu')
    with pytest.raises(CppDiagnostic) as error:
        CppParser().parse(source)
    assert error.value.code==code


@pytest.mark.integration
def test_full_owned_header_public_no_call_program_against_actual_cpp17(tmp_path,monkeypatch):
    import os
    import shutil
    import subprocess
    from pcc.backend.owned_object_emit import emit_owned_object
    from pcc.driver.project import TranslationUnit
    from pcc.frontends.c.evaluator.c_evaluator import CEvaluator
    from tests.cpp.owned_execution import Execution, TARGET
    from tests.cpp.test_cpp_class_execution import record_execution
    from tests.cpp.test_cpp_public_driver_execution import link_object
    source='#include <cstdlib>\nint main(){return 42;}'
    with monkeypatch.context() as guard:
        def forbidden(*args,**kwargs):
            raise AssertionError('owned header program invoked external tools')
        guard.setattr(subprocess,'Popen',forbidden)
        def compile_original():
            return CEvaluator(backend='self',target_triple=TARGET).compile_translation_units(
                [TranslationUnit('cstdlib.cpp',None,source)],use_system_cpp=False,
                frontend_opt_level=0,use_compile_cache=False)
        first=compile_original()
        assert first==compile_original()
        obj=emit_owned_object(first[0][1],TARGET)
        assert obj==emit_owned_object(first[0][1],TARGET)
        executable=link_object(obj)
    owned=tmp_path/'owned'
    owned.write_bytes(executable)
    owned.chmod(0o700)
    expected=Execution(42,b'',b'')
    record_execution(owned,expected)
    unit=tmp_path/'cstdlib.cpp'
    unit.write_text(source)
    oracle=tmp_path/'oracle'
    compiler=shutil.which(os.environ.get('PCC_CPP_ORACLE','g++'))
    assert compiler
    result=subprocess.run([compiler,'-std=c++17','-pedantic-errors','-O0',str(unit),'-o',str(oracle)],
                          capture_output=True,timeout=20)
    assert result.returncode==0,result.stderr
    record_execution(oracle,expected)
