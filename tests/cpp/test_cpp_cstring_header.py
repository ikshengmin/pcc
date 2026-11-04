"""Original tests of N4659 [cstring.syn], [over.ics.rank], [dcl.link], [expr.cond].

https://www.open-std.org/jtc1/sc22/wg21/docs/papers/2017/n4659.pdf
These qualify declarations and their const-correct overloads, not a string
runtime. Native/reference cases deliberately do not call unresolved routines.
"""
import hashlib
import json
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
from pcc.frontends.cpp.types import ConstType, PointerType
from tests.cpp.owned_execution import Execution, TARGET
from tests.cpp.test_cpp_class_execution import record_execution
from tests.cpp.test_cpp_public_driver_execution import link_object


NAMES = (
    'memcpy', 'memmove', 'strcpy', 'strncpy', 'strcat', 'strncat', 'memcmp',
    'strcmp', 'strcoll', 'strncmp', 'strxfrm', 'memchr', 'strchr', 'strcspn',
    'strpbrk', 'strrchr', 'strspn', 'strstr', 'strtok', 'memset', 'strerror', 'strlen',
)
SEARCH = ('memchr', 'strchr', 'strpbrk', 'strrchr', 'strstr')


def parse_header(tail=''):
    parser = CppParser(target_triple=TARGET)
    parser.parse(preprocess_cpp('#include <cstring>\n' + tail, target_triple=TARGET))
    return parser


def compile_public(source):
    return CEvaluator(backend='self', target_triple=TARGET).compile_translation_units(
        [TranslationUnit('cstring.cpp', None, source)], use_system_cpp=False,
        frontend_opt_level=0, use_compile_cache=False,
    )


@pytest.mark.parametrize('name', NAMES)
def test_cstring_global_and_std_imports_preserve_all_entities(name):
    parser = parse_header('#include <cstring>\nusing std::' + name + ';')
    direct = parser.scope.names[name].overloads
    imported = parser.scope.names['std'].namespace_scope.names[name].overloads
    assert len(direct) == (2 if name in SEARCH else 1)
    assert len(imported) == len(direct)
    assert all(a is b for a, b in zip(direct, imported))
    assert all(not item.defined and not item.internal_linkage for item in direct)
    assert direct[0].language_linkage == 'C' and direct[0].c_name == name


@pytest.mark.parametrize('name', SEARCH)
def test_search_overloads_preserve_const_results_and_distinct_linkage(name):
    parser = parse_header()
    constant, mutable = parser.scope.names[name].overloads
    base = 'void' if name == 'memchr' else 'char'
    assert constant.dtype == PointerType(ConstType(base))
    assert constant.parameters[0] == PointerType(ConstType(base))
    assert mutable.dtype == PointerType(base)
    assert mutable.parameters[0] == PointerType(base)
    assert constant.language_linkage == 'C' and constant.c_name == name
    assert mutable.language_linkage == 'C++' and mutable.c_name != name


CALLS = (
    ('strlen', 'const char* p', 'std::strlen(p)', 'C', False),
    ('memchr', 'const void* p', 'std::memchr(p,0,0)', 'C', True),
    ('memchr', 'void* p', 'std::memchr(p,0,0)', 'C++', True),
    ('strchr', 'const char* p', 'std::strchr(p,0)', 'C', True),
    ('strchr', 'char* p', 'std::strchr(p,0)', 'C++', True),
    ('strpbrk', 'char* p', 'std::strpbrk(p,p)', 'C++', True),
    ('strrchr', 'char* p', 'std::strrchr(p,0)', 'C++', True),
    ('strstr', 'char* p', 'std::strstr(p,p)', 'C++', True),
)


@pytest.mark.parametrize('name,parameter,call,linkage,pointer_result', CALLS)
def test_calls_select_real_unresolved_symbols_without_const_erasure(
        name, parameter, call, linkage, pointer_result, monkeypatch):
    parser = parse_header()
    selected = next(item for item in parser.scope.names[name].overloads
                    if item.language_linkage == linkage)
    expression = call + '!=nullptr' if pointer_result else 'int(' + call + ')'
    source = '#include <cstring>\nint f(' + parameter + '){return ' + expression + ';}'
    def forbidden(*args, **kwargs):
        raise AssertionError('owned cstring compilation invoked an external process')
    monkeypatch.setattr(subprocess, 'Popen', forbidden)
    first = compile_public(source)
    assert first == compile_public(source)
    obj = emit_owned_object(first[0][1], TARGET)
    assert obj == emit_owned_object(first[0][1], TARGET)
    defined, undefined = _object_symbol_sets(parse_relocatable(obj))
    assert selected.c_name not in defined
    assert undefined == frozenset((selected.c_name,))


@pytest.mark.parametrize('source,code', (
    ('char* f(const char* p){return std::strchr(p,0);}', 'cpp.invalid-conversion'),
    ('void* f(const void* p){return std::memchr(p,0,0);}', 'cpp.invalid-conversion'),
    ('int f(){std::strchr(nullptr,0);return 0;}', 'cpp.ambiguous-overload'),
    ('int f(){std::memchr(nullptr,0,0);return 0;}', 'cpp.ambiguous-overload'),
))
def test_const_and_ambiguous_search_diagnostics(source, code):
    with pytest.raises(CppDiagnostic) as error:
        parse_header(source)
    assert error.value.code == code


@pytest.mark.parametrize('headers', (
    '#include <cstring>\n#include <string.h>\n',
    '#include <string.h>\n#include <cstring>\n',
))
def test_c_compatibility_header_is_not_silently_used_as_cpp_string_header(headers):
    # The owned C compatibility string.h currently pulls _fake_typedefs.h and
    # does not supply the C++ overload/wrapper contract. It is deliberately not
    # in the default C++ search path; this is an explicit unimplemented boundary.
    with pytest.raises(RuntimeError, match='header not found: string\\.h'):
        preprocess_cpp(headers, target_triple=TARGET)


@pytest.mark.parametrize('declaration', (
    'extern "C" void* memchr(const void*,int,size_t);',
    'extern "C" char* strchr(const char*,int);',
    'extern "C" char* strpbrk(const char*,const char*);',
    'extern "C" char* strrchr(const char*,int);',
    'extern "C" char* strstr(const char*,const char*);',
))
def test_raw_c_search_signature_does_not_erase_cpp_const_return(declaration):
    # These are the C API signatures, not definitions of C++ mutable overloads.
    # Real mixed-header support needs a shared wrapper/linkage design; accepting
    # these as a second return type for the same C entity would be lossy.
    with pytest.raises(CppDiagnostic) as error:
        parse_header(declaration)
    assert error.value.code == 'cpp.return-type-overload'


NO_CALL_CASES = (
    '#include <cstring>\nint main(){std::size_t n=42;return int(n);}',
    '#include <cstring>\n#include <cstdio>\n#include <cstdlib>\n#include <cstring>\n'
    'namespace bytes=std;using bytes::strchr;int main(){void* p=NULL;if(p==nullptr)return 42;return 0;}',
)


@pytest.mark.integration
@pytest.mark.parametrize('source', NO_CALL_CASES)
def test_public_cstring_declarations_execute_against_explicit_reference(source, tmp_path, monkeypatch):
    with monkeypatch.context() as guard:
        def forbidden(*args, **kwargs):
            raise AssertionError('owned cstring compilation invoked an external process')
        guard.setattr(subprocess, 'Popen', forbidden)
        first = compile_public(source)
        assert first == compile_public(source)
        obj = emit_owned_object(first[0][1], TARGET)
        assert obj == emit_owned_object(first[0][1], TARGET)
        assert not _object_symbol_sets(parse_relocatable(obj))[1]
        executable = link_object(obj)
    binary = tmp_path / 'owned'
    binary.write_bytes(executable)
    binary.chmod(0o700)
    expected = Execution(42, b'', b'')
    record_execution(binary, expected)
    unit = tmp_path / 'cstring.cpp'
    unit.write_text(source)
    oracle = tmp_path / 'oracle'
    compiler = shutil.which(os.environ.get('PCC_CPP_ORACLE', 'g++'))
    assert compiler
    result = subprocess.run([compiler, '-std=c++17', '-pedantic-errors', '-O0', str(unit), '-o', str(oracle)],
                            capture_output=True, timeout=20)
    assert result.returncode == 0, result.stderr
    record_execution(oracle, expected)


# Retain the exact original native input after conditional-expression support.
CONDITIONAL_ORIGINAL = (
    '#include <cstring>\n#include <cstdio>\n#include <cstdlib>\n#include <cstring>\n'
    'namespace bytes=std;using bytes::strchr;int main(){void* p=NULL;return p==nullptr?42:0;}'
)


def test_original_conditional_header_program_is_owned_and_stable(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('owned cstring compilation invoked an external process')
    monkeypatch.setattr(subprocess, 'Popen', forbidden)
    first = compile_public(CONDITIONAL_ORIGINAL)
    assert first == compile_public(CONDITIONAL_ORIGINAL)
    obj = emit_owned_object(first[0][1], TARGET)
    assert obj == emit_owned_object(first[0][1], TARGET)
    assert not _object_symbol_sets(parse_relocatable(obj))[1]


@pytest.mark.integration
def test_original_conditional_header_program_is_accepted_by_explicit_reference(tmp_path, monkeypatch):
    unit = tmp_path / 'cstring-conditional-original.cpp'
    unit.write_text(CONDITIONAL_ORIGINAL)
    original = CONDITIONAL_ORIGINAL.encode('utf-8')
    assert unit.read_bytes() == original
    with monkeypatch.context() as guard:
        def forbidden(*args, **kwargs):
            raise AssertionError('owned cstring compilation invoked an external process')
        guard.setattr(subprocess, 'Popen', forbidden)
        compiled = compile_public(CONDITIONAL_ORIGINAL)
        obj = emit_owned_object(compiled[0][1], TARGET)
        assert not _object_symbol_sets(parse_relocatable(obj))[1]
        executable = link_object(obj)
    owned = tmp_path / 'owned'
    owned.write_bytes(executable)
    owned.chmod(0o700)
    record_execution(owned, Execution(42, b'', b''))
    compiler = shutil.which(os.environ.get('PCC_CPP_ORACLE', 'g++'))
    assert compiler
    binary = tmp_path / 'reference'
    result = subprocess.run([compiler, '-std=c++17', '-pedantic-errors', '-O0', str(unit), '-o', str(binary)],
                            capture_output=True, timeout=20)
    (tmp_path / 'reference-build.json').write_text(json.dumps({
        'argv': result.args, 'returncode': result.returncode,
        'source_sha256': hashlib.sha256(unit.read_bytes()).hexdigest(),
        'expected_source_sha256': hashlib.sha256(original).hexdigest(),
        'stdout_hex': result.stdout.hex(), 'stderr_hex': result.stderr.hex(),
    }, indent=2))
    assert unit.read_bytes() == original
    assert result.returncode == 0, result.stderr
    record_execution(binary, Execution(42, b'', b''))
