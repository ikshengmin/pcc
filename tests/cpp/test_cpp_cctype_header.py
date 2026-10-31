"""Owned <cctype> declarations, not a ctype runtime implementation.

Original PCC tests of N4659 [cctype.syn], [namespace.udecl], [dcl.link].
https://www.open-std.org/jtc1/sc22/wg21/docs/papers/2017/n4659.pdf
The native no-call cases qualify inclusion/public compilation only. All actual
ctype calls below remain unresolved external relocations; no ASCII substitute,
host libc, or assembly implementation is used to claim their semantics.
"""
import os
import re
import shutil
import subprocess

import pytest

from pcc.backend.elf_x86_64 import ElfError, _object_symbol_sets, parse_relocatable
from pcc.backend.owned_object_emit import emit_owned_object
from pcc.driver.project import TranslationUnit
from pcc.frontends.c.evaluator.c_evaluator import CEvaluator
from pcc.frontends.cpp import CppDiagnostic, CppParser
from pcc.frontends.cpp.driver import preprocess_cpp
from tests.cpp.owned_execution import Execution, TARGET
from tests.cpp.test_cpp_class_execution import record_execution
from tests.cpp.test_cpp_public_driver_execution import link_object


NAMES = (
    'isalnum', 'isalpha', 'isblank', 'iscntrl', 'isdigit', 'isgraph', 'islower',
    'isprint', 'ispunct', 'isspace', 'isupper', 'isxdigit', 'tolower', 'toupper',
)


def compile_public(source, **kwargs):
    return CEvaluator(backend='self', target_triple=TARGET).compile_translation_units(
        [TranslationUnit('cctype.cpp', None, source)], use_system_cpp=False,
        frontend_opt_level=0, **kwargs,
    )


def parse_header(tail=''):
    parser = CppParser(target_triple=TARGET)
    parser.parse(preprocess_cpp('#include <cctype>\n' + tail, target_triple=TARGET))
    return parser


@pytest.mark.parametrize('name', NAMES)
def test_cctype_std_and_global_are_the_same_c_entity(name):
    parser = parse_header('#include <cctype>\nnamespace chars=std;')
    direct = parser.scope.names[name].overloads[0]
    imported = parser.scope.names['std'].namespace_scope.names[name].overloads[0]
    assert direct is imported
    assert direct.kind == 'function' and direct.language_linkage == 'C'
    assert direct.c_name == name and direct.dtype == 'int'
    assert direct.parameters == ('int',) and direct.arity == 1
    assert not direct.defined and not direct.internal_linkage
    assert parser.scope.names['chars'].namespace_scope is parser.scope.names['std'].namespace_scope


@pytest.mark.parametrize('name', NAMES)
def test_cctype_calls_emit_plain_unresolved_c_symbols_without_definitions(name, monkeypatch):
    source = '#include <cctype>\nint main(){return std::' + name + '(-1)+::' + name + '(255);}'
    def forbidden(*args, **kwargs):
        raise AssertionError('owned cctype compilation invoked an external process')
    monkeypatch.setattr(subprocess, 'Popen', forbidden)
    first = compile_public(source, use_compile_cache=False)
    second = compile_public(source, use_compile_cache=False)
    assert first == second
    ir = first[0][1]
    assert len(re.findall(r'call i32 \(i32\) @"?' + name + r'"?\(', ir)) == 2
    assert re.search(r'declare i32 @"?' + name + r'"?\(i32', ir)
    assert not re.search(r'define .*@"?' + name + r'"?\(', ir)
    # The EOF-shaped -1 and unsigned-char upper value 255 are passed as int,
    # not narrowed by a macro/ASCII expression. This is ABI, not locale proof.
    arguments = re.findall(r'call i32 \(i32\) @"?' + name + r'"?\(i32 ([^)]+)\)', ir)
    assert arguments[1] == '255'
    assert arguments[0] == '-1' or re.search(
        re.escape(arguments[0]) + r' = sub i32 0, 1\b', ir)
    obj = emit_owned_object(ir, TARGET)
    assert obj == emit_owned_object(second[0][1], TARGET)
    defined, undefined = _object_symbol_sets(parse_relocatable(obj))
    assert name not in defined and undefined == frozenset((name,))
    with pytest.raises(ElfError, match=r'^undefined static ELF symbols: ' + name + r'$'):
        link_object(obj)


def test_header_does_not_inject_character_macros_or_eof_definition():
    checks = ''.join('#ifdef ' + name + '\n#error unexpected macro\n#endif\n' for name in (*NAMES, 'EOF'))
    text = preprocess_cpp('#include <cctype>\n' + checks + 'int main(){return 42;}')
    CppParser().parse(text)


@pytest.mark.parametrize('tail,code', (
    ('extern "C" int isalpha(long);', 'cpp.conflicting-c-linkage-type'),
    ('extern "C++" int isalpha(int);', 'cpp.conflicting-language-linkage'),
    ('int main(){return std::isalpha();}', 'cpp.no-viable-overload'),
))
def test_header_declarations_retain_type_and_language_linkage_diagnostics(tail, code):
    with pytest.raises(CppDiagnostic) as error:
        parse_header(tail)
    assert error.value.code == code


def test_std_import_does_not_create_a_second_overload_candidate():
    parser = parse_header('using std::isalpha;int f(int x){return isalpha(x);}')
    assert len(parser.scope.names['isalpha'].overloads) == 1


@pytest.mark.parametrize('header', ('ctype.h', 'stdio.h', 'vector'))
def test_missing_cpp_headers_do_not_fall_back_to_c_or_host_headers(header):
    with pytest.raises(RuntimeError, match='header not found: ' + re.escape(header)):
        preprocess_cpp('#include <' + header + '>\n')


def test_explicit_nostdinc_disables_owned_defaults():
    with pytest.raises(RuntimeError, match='header not found: cctype'):
        preprocess_cpp('#include <cctype>\n', cpp_args=['-nostdinc'])


@pytest.mark.parametrize('kind', ('include_dirs', '-I', '-isystem'))
def test_explicit_header_paths_take_precedence_without_source_rewriting(tmp_path, kind):
    (tmp_path / 'cctype').write_text('#define SELECTED 42\n')
    args = {'include_dirs': [str(tmp_path)]} if kind == 'include_dirs' else {'cpp_args': [kind, str(tmp_path)]}
    text = preprocess_cpp('#include <cctype>\nint main(){return SELECTED;}\n', **args)
    assert 'return 42' in text and 'extern "C"' not in text


def test_changed_included_bytes_invalidate_the_public_compilation_cache(tmp_path, monkeypatch):
    from pcc.frontends.c.evaluator import c_evaluator

    include = tmp_path / 'include'
    include.mkdir()
    header = include / 'cctype'
    source = '#include <cctype>\nint main(){return SELECTED;}\n'
    kwargs = dict(include_dirs=[str(include)], use_compile_cache=True, cache_dir=str(tmp_path / 'cache'))
    header.write_text('#define SELECTED 42\n')
    first = compile_public(source, **kwargs)
    with monkeypatch.context() as hit_guard:
        def unexpected_miss(*args, **kwargs):
            raise AssertionError('unchanged header missed the existing compile cache')
        hit_guard.setattr(c_evaluator, '_invoke_compile_preprocessed_translation_unit_artifact', unexpected_miss)
        cached = compile_public(source, **kwargs)
    # This contract is emitted IR/object byte identity, not Python container
    # identity of the public serialized return descriptor. That separate API
    # currently exposes a tuple before JSON storage and a list after loading;
    # the original full-result equality repro is retained independently.
    assert cached[0][0:2] == first[0][0:2]
    assert cached[0][3] == first[0][3]
    first_object = emit_owned_object(first[0][1], TARGET)
    assert emit_owned_object(cached[0][1], TARGET) == first_object
    assert len(list((tmp_path / 'cache').rglob('*.json'))) == 1
    header.write_text('#define SELECTED 17\n')
    second = compile_public(source, **kwargs)
    assert first[0][1] != second[0][1]
    assert emit_owned_object(second[0][1], TARGET) != first_object
    assert len(list((tmp_path / 'cache').rglob('*.json'))) == 2
    assert 'ret i32 42' in first[0][1] and 'ret i32 17' in second[0][1]


NO_CALL_CASES = (
    '#include <cctype>\nint main(){int isalpha=42;return isalpha;}',
    '#include <cctype>\n#include <cctype>\nnamespace chars=std;using chars::isblank;int main(){return 42;}',
)


@pytest.mark.integration
@pytest.mark.parametrize('source', NO_CALL_CASES)
def test_public_header_inclusion_executes_but_does_not_claim_ctype_runtime(source, tmp_path, monkeypatch):
    with monkeypatch.context() as guard:
        def forbidden(*args, **kwargs):
            raise AssertionError('owned header compilation invoked an external process')
        guard.setattr(subprocess, 'Popen', forbidden)
        first = compile_public(source, use_compile_cache=False)
        assert first == compile_public(source, use_compile_cache=False)
        obj = emit_owned_object(first[0][1], TARGET)
        assert obj == emit_owned_object(first[0][1], TARGET)
        assert not _object_symbol_sets(parse_relocatable(obj))[1]
        executable = link_object(obj)
    binary = tmp_path / 'owned'
    binary.write_bytes(executable)
    binary.chmod(0o700)
    expected = Execution(42, b'', b'')
    record_execution(binary, expected)
    unit = tmp_path / 'cctype.cpp'
    unit.write_text(source)
    oracle = tmp_path / 'oracle'
    compiler = shutil.which(os.environ.get('PCC_CPP_ORACLE', 'g++'))
    assert compiler
    result = subprocess.run([compiler, '-std=c++17', '-pedantic-errors', '-O0', str(unit), '-o', str(oracle)],
                            capture_output=True, timeout=20)
    assert result.returncode == 0, result.stderr
    record_execution(oracle, expected)
