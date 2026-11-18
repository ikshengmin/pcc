"""C++17 [depr.c.headers], [depr.ccomplex.syn], [cmplx.over], [dcl.inline].

Only opaque pointers/references to complex<T> and bounded real-scalar norm
overloads are supplied. This is not complex-object, NumPy, or pcc1 acceptance.
The reference compiles the *same* program against its own system headers.
"""
import os
import shutil
import struct
import subprocess

import pytest

from pcc.backend.elf_x86_64 import parse_relocatable
from pcc.frontends.cpp import CppDiagnostic, CppParser
from pcc.frontends.cpp.driver import preprocess_cpp
from tests.cpp.owned_execution import (
    Execution, TARGET, compile_host_cpp, compile_reference_cpp, run_executable, write_host_executable,
)

HEAD = '#include <complex.h>\n#include <ccomplex>\n#include <complex>\n'
FP = ('float', 'double')
INTEGER = ('bool', 'char', 'signed char', 'unsigned char', 'wchar_t', 'short', 'unsigned short',
           'int', 'unsigned int', 'long', 'unsigned long', 'long long', 'unsigned long long')
UNITS = (
    ('provider.cpp', '#include <ccomplex>\n' + '''
double square(int n){ return std::norm(n); }
int kind(std::complex<double>*){return 13;}
int kind(std::complex<float>*){return 17;}
'''),
    ('entry.cpp', HEAD + '''
double square(int);
int kind(std::complex<double>*);
int kind(std::complex<float>*);
int pick(float){return 1;} int pick(double){return 2;} int pick(long double);
int cpp_test_entry(){
    // Implicit nullptr initialization is the supported standard route.
    // Explicit static_cast<T*>(nullptr) remains separately, negatively tested.
    std::complex<double>* zd=nullptr; std::complex<float>* zf=nullptr;
    if (kind(zd) != 13 || kind(zf) != 17) return 1;
    if (pick(std::norm(6.5f)) != 1 || pick(std::norm(6.5)) != 2 ||
        pick(std::norm(-50000)) != 2 || pick(std::norm(true)) != 2) return 2;
    if (square(-50000) != 2500000000.0 || std::norm(-3.5f) != 12.25f ||
        std::norm(-1.25) != 1.5625 || std::norm(true) != 1.0) return 3;
    double sign = -0.0;
    double posinf = 1.0/(-sign);
    double notnumber = sign/sign;
    if (std::norm(sign) != 0.0 || 1.0/std::norm(sign) != posinf ||
        std::norm(-posinf) != posinf || std::norm(notnumber) == std::norm(notnumber)) return 4;
    unsigned long long hi=18446744073709551615ULL;
    double accepted=18446744073709551616.0;
    if (std::norm(hi) != accepted*accepted) return 5;
    return 42;
}
'''),
)
EXPECTED = Execution(42, struct.pack('<i', 42), b'')


def parse(source, target=TARGET):
    parser = CppParser(target_triple=target)
    parser.parse(preprocess_cpp(source, target_triple=target))
    return parser


@pytest.mark.parametrize('suffix', ('complex.h', 'ccomplex', 'complex'))
def test_real_compatibility_header_preserves_lookup_and_reserved_guards(suffix, tmp_path):
    source = '#include <'+suffix+'>\n' + HEAD + '''
#if defined(complex) || defined(I) || defined(__PCC_REAL_NORM) || defined(__PCC_REAL_NORM_FP)
#error leaked C macro or private arithmetic generator
#endif
'''
    std = parse(source).scope.names['std'].namespace_scope
    assert std.names['complex'].kind == 'class-template'
    functions = std.names['norm'].overloads
    poisoned = [f for f in functions if f.template_parameters]
    assert len(poisoned) == 1 and poisoned[0].deleted_template and poisoned[0].defined
    overloads = {f.parameters[0]: f for f in functions if not f.template_parameters}
    assert set(overloads) == {*FP, *INTEGER, 'long double'}
    for operand, f in overloads.items():
        assert f.dtype == ('double' if operand in INTEGER else operand)
        assert f.defined == (operand != 'long double')
        if f.defined:
            assert f.definition_linkage == 'external_odr'
    with pytest.raises(RuntimeError, match='header not found'):
        preprocess_cpp('#include <'+suffix+'>\n', cpp_args=['-nostdinc'])
    (tmp_path/suffix).write_text('int user_owned_header;\n')
    text = preprocess_cpp('#include <'+suffix+'>\n', include_dirs=[str(tmp_path)])
    assert 'int user_owned_header;' in text and 'class complex;' not in text


@pytest.mark.parametrize('target', ('aarch64-unknown-linux-gnu', 'x86_64-pc-windows-msvc'))
def test_target_without_external_odr_keeps_declarations_not_strong_fallback(target):
    parser = parse(HEAD + 'double placeholder(std::complex<double>*);', target)
    overloads = parser.scope.names['std'].namespace_scope.names['norm'].overloads
    scalar = [f for f in overloads if not f.template_parameters]
    assert all(not f.defined for f in scalar) and len(scalar) == len(INTEGER)+3
    assert sum(f.deleted_template for f in overloads) == 1
    with pytest.raises(CppDiagnostic, match='external-ODR'):
        parse('namespace demo{inline int f(int x){return x;}}', target)


@pytest.mark.parametrize('source', (
    'namespace n { inline int global=1; }',
    'namespace n { inline int later(int); }',
    'namespace { inline int f(int n){return n;} }',
    'inline int f(int n){return n;}',
    'namespace n{inline int f(){static int a=0;return ++a;}}',
    'namespace n{inline inline int f(){return 0;}}',
    'namespace n{inline int f(){return 1;} inline int f(){return 2;}}',
    'namespace n{int f();inline double f(){return 1.0;}}',
    HEAD + 'int f(){return sizeof(std::complex<double>);}',
    HEAD + 'int f(){std::complex<float> z; return 42;}',
    HEAD + 'int f(int *p){return std::norm(p);}',
    HEAD + 'enum E{v=3}; double f(){return std::norm(v);}',
    HEAD + 'void f(int *p){std::norm(p);}',
    HEAD + 'void f(int *p){decltype(std::norm(p))* q;}',
    'template<class T> int f(T) = delete; int g(){return f(3);}',
    'template<class T> int f(T) = delete; int g(){return sizeof(f(3));}',
    'template<class T> int f(T) = delete; int g(){decltype(f(3)) x;}',
    'template<class T> int f(T); template<class U> int f(U) = delete;',
    'template<class T> int f(T) = delete; template<class U> int f(U x){return 2;}',
    'template<class T> int f(T) = delete; int g(){return f<int>(3);}',

    HEAD + 'int f(){return int(std::norm(0.5L));}',
    HEAD + 'int f(){return int(std::conj(2));}',
))
def test_invalid_or_unimplemented_operations_never_publish_an_object(source):
    with pytest.raises(CppDiagnostic):
        parse(source)


def test_user_deleted_template_applies_only_after_normal_overload_selection():
    parser = parse('template<class T> int f(T) = delete; int f(int x){return x;} int g(){return f(42);}')
    group = parser.scope.names['f'].overloads
    assert sum(f.deleted_template for f in group) == 1
    assert not next(f for f in group if f.deleted_template).instances


def test_two_original_translation_units_coalesce_typed_definitions_and_execute(tmp_path, monkeypatch):
    # Never invoke a host preprocessor/compiler/assembler/linker on the owned path.
    with monkeypatch.context() as m:
        def forbidden(*args, **kwargs):
            raise AssertionError('external process during owned C++ compile/object/link')
        m.setattr(subprocess, 'Popen', forbidden)
        artifact = compile_host_cpp(tuple((n, preprocess_cpp(s, target_triple=TARGET)) for n,s in UNITS))
    for obj in artifact.objects:
        symbols = parse_relocatable(obj).symbols
        shared = {s.name for s in symbols if s.section_index and s.binding == 2}
        assert {'_ZSt4normf', '_ZSt4normd', '_ZSt4normi', '_ZSt4normy'} <= shared
        assert not any(s.name == '_ZSt4norme' and s.section_index for s in symbols)
    output = tmp_path/'owned-elf'; write_host_executable(artifact, output)
    assert run_executable(output) == EXPECTED


@pytest.mark.integration
def test_scalar_complex_originals_match_separate_system_cxx17(tmp_path):
    compiler = shutil.which(os.environ.get('PCC_CPP_ORACLE', 'g++'))
    assert compiler, 'explicit C++17 test reference is required'
    executable = compile_reference_cpp(UNITS, tmp_path/'reference', compiler=compiler)
    assert run_executable(executable) == EXPECTED
    # Directly compare the rejected calls as well. System headers are the
    # independent external oracle; compiling our bounded header in a second
    # invocation checks its source-level (not just PCC-parser) semantics.
    from pathlib import Path
    owned_include = Path(__file__).resolve().parents[2] / 'pcc/frontends/cpp/include'
    for settings in ((), ('-nostdinc++', '-I' + str(owned_include))):
        def syntax_ok(source):
            action = subprocess.run([compiler, '-std=c++17', *settings, '-x', 'c++',
                                     '-fsyntax-only', '-'], input=source.encode(),
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            return action.returncode == 0
        assert syntax_ok(HEAD + 'double f(){return std::norm(true)+std::norm(-50000);}')
        assert syntax_ok(HEAD + 'double f(char16_t x, char32_t y){return std::norm(x)+std::norm(y);}')
        for invalid in (
            'void f(int *p){std::norm(p);}',
            'void f(int *p){decltype(std::norm(p))* q;}',
            'enum E{v=3}; double f(){return std::norm(v);}',
        ):
            assert not syntax_ok(HEAD + invalid), (settings, invalid)
