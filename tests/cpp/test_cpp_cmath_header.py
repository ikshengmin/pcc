"""N4659 [cmath.syn], [c.math], [depr.c.headers]: unary floating declarations.

The C++ types, object relocations, and default include search are mandatory
without a runtime.  A separate integration test admits the real PCC archive
and executes the original program against a separate C++17 system oracle.
This does not qualify all cmath operations, NumPy or a native pcc1 build.
"""
import os
import shutil
import subprocess

import pytest

from pcc.backend.elf_x86_64 import _object_symbol_sets, parse_relocatable
from pcc.backend.owned_object_emit import emit_owned_object
from pcc.driver.project import TranslationUnit
from pcc.frontends.c.evaluator.c_evaluator import CEvaluator
from pcc.frontends.cpp import CppDiagnostic, CppParser
from pcc.frontends.cpp.driver import preprocess_cpp
from tests.cpp.owned_execution import Execution, TARGET, run_executable

NAMES = ('fabs', 'floor', 'ceil', 'rint', 'sqrt', 'exp', 'log', 'log2',
         'log10', 'sin', 'cos', 'tan')
HEAD = '#include <math.h>\n#include <cmath>\n'
SOURCE = HEAD + '''int main(){
    double a=std::fabs(-1764.0);
    double b=::sqrt(a);
    if (b != 42.0) return 1;
    if (std::floor(-1.75) != -2.0 || ::ceil(-1.75) != -1.0) return 2;
    if (::sin(0.0) != 0.0 || std::cos(0.0) != 1.0 || std::tan(0.0) != 0.0) return 3;
    if (::exp(0.0) != 1.0 || std::log(1.0) != 0.0 ||
        ::log2(8.0) != 3.0 || ::log10(1.0) != 0.0) return 4;
    if (std::rint(2.5) != 2.0 || std::rint(3.5) != 4.0) return 5;
    return int(b);
}'''


def compile_unit(source):
    return CEvaluator(backend='self', target_triple=TARGET).compile_translation_units(
        [TranslationUnit('math.cpp', None, source)], use_system_cpp=False,
        use_compile_cache=False, frontend_opt_level=0,
    )


def object_symbols(source):
    ir = compile_unit(source)[0][1]
    return _object_symbol_sets(parse_relocatable(emit_owned_object(ir, TARGET)))


@pytest.mark.parametrize('headers', (HEAD, '#include <cmath>\n#include <math.h>\n',
                                     '#include <math.h>\n#include <math.h>\n'))
def test_cmath_global_and_std_floating_entities_are_identical(headers):
    p = CppParser(target_triple=TARGET)
    checks = ''.join('#ifdef '+name+'\n#error leaked math macro\n#endif\n'
                     for name in (*NAMES, '__PCC_CMATH_UNARY'))
    p.parse(preprocess_cpp(headers+checks, target_triple=TARGET))
    std = p.scope.names['std'].namespace_scope
    for name in NAMES:
        for key,expected in ((name, {('double','C'),('float','C++'),('long double','C++')}),
                             (name+'f', {('float','C')}), (name+'l', {('long double','C')})):
            globals_ = p.scope.names[key].overloads
            imported = std.names[key].overloads
            assert len(globals_) == len(imported) == len(expected)
            assert all(a is b for a,b in zip(globals_,imported))
            assert {(f.dtype,f.language_linkage) for f in globals_} == expected
            assert all(f.parameters == (f.dtype,) and not f.defined for f in globals_)
            assert all(f.c_name == key for f in globals_ if f.language_linkage == 'C')


@pytest.mark.parametrize('name', NAMES)
def test_cmath_real_objects_name_the_selected_abi_not_a_double_surrogate(name):
    tail = 'double probe(double x,float y){return std::'+name+'(x)+::'+name+'(y)+std::'+name+'f(y);}'
    defined, undefined = object_symbols(HEAD+tail)
    assert undefined == {name, '_Z'+str(len(name))+name+'f', name+'f'}
    assert not (undefined & defined)


@pytest.mark.parametrize('header', ('math.h','cmath'))
def test_cmath_header_search_still_respects_nostdinc_and_explicit_include_dirs(header,tmp_path):
    with pytest.raises(RuntimeError,match='header not found:'):
        preprocess_cpp('#include <'+header+'>\n',cpp_args=['-nostdinc'])
    # An explicit user include still outranks the system search path.
    (tmp_path/header).write_text('int my_header;\n')
    text=preprocess_cpp('#include <'+header+'>\n', include_dirs=[str(tmp_path)])
    assert 'int my_header;' in text and 'double sqrt' not in text


@pytest.mark.parametrize('operand,code', (('nullptr','cpp.no-viable-overload'),
    ('0','cpp.ambiguous-overload'),('true','cpp.ambiguous-overload'),
    ('0.5L','cpp.unsupported.extended-float')))
def test_cmath_unsupported_calls_do_not_fall_back_to_a_double_abi(operand,code):
    with pytest.raises(CppDiagnostic) as failure:
        compile_unit(HEAD+'int main(){return int(std::sqrt('+operand+'));}')
    assert failure.value.code == code


@pytest.mark.integration
@pytest.mark.pcc_gate(env='PCC_RUNTIME_ARCHIVE',platforms=('linux',),machines=('x86_64',))
def test_cmath_double_functions_execute_from_admitted_owned_archive(tmp_path,monkeypatch):
    from tests.owned_runtime_c_fixture import admitted_runtime
    archive=admitted_runtime()
    assert object_symbols(SOURCE)[1] == set(NAMES)
    with monkeypatch.context() as guard:
        def forbidden(*args, **kwargs):
            raise AssertionError('external process in PCC owned compile, object emission or link')
        guard.setattr(subprocess,'Popen',forbidden)
        units=compile_unit(SOURCE)
        CEvaluator(backend='self',target_triple=TARGET).emit_executable(
            units, str(tmp_path/'owned'), link_args=[str(archive)])
    expected=Execution(42,b'',b'')
    assert run_executable(tmp_path/'owned') == expected


@pytest.mark.integration
def test_cmath_unmodified_program_against_actual_cpp17_system_headers(tmp_path):
    source=tmp_path/'original.cpp'; source.write_text(SOURCE)
    compiler=shutil.which(os.environ.get('PCC_CPP_ORACLE','g++'))
    assert compiler, 'C++17 reference compiler required for the integration lane'
    subprocess.run([compiler,'-std=c++17','-pedantic-errors','-O0','-fno-builtin',
                    str(source),'-lm','-o',str(tmp_path/'reference')],check=True)
    assert run_executable(tmp_path/'reference') == Execution(42,b'',b'')
