"""Exact-source receiver-first lookup oracles; prepared statically, unrun.

Host Linux x86_64 PCC frontend/self/ELF ownership, not native pcc1.
"""
import builtins
import hashlib
import json
import platform
import shutil
import subprocess
import sys
import pytest
from pcc.frontends.cpp import CppDiagnostic, CppParser
from tests.cpp.owned_execution import (Execution, assert_exact, compile_host_cpp,
    compile_reference_cpp, run_executable, write_host_executable)

TARGET = 'x86_64-unknown-linux-gnu'
# Original alias-probe source retained from the previous review, byte-for-byte.
ALIAS_SHADOW = (
    'struct Base{Base(){} virtual int f(){return 1;}};using Label=Base;'
    'struct Derived:Base{Derived():Base(){} using Label=Derived;int f()override{return 42;}};'
    'int cpp_test_entry(){Derived obj;Derived*p=&obj;return p->Label::f();}'
)
PRIVATE_ALIAS = (
    'struct Base{int f(){return 42;}};using Label=Base;'
    'struct Derived:Base{private: using Label=Base;};int g(Derived*p){return p->Label::f();}'
)
POSITIVE = (
    ('original_alias_shadow', ALIAS_SHADOW),
    ('public_same_type_alias',
     'struct Base{Base(){} int f(){return 42;}};using Label=Base;'
     'struct Derived:Base{Derived():Base(){} public:using Label=Base;};'
     'int cpp_test_entry(){Derived obj;return obj.Label::f();}'),
    ('inherited_alias_shadow',
     'struct Base{Base(){} virtual int f(){return 1;}};using Label=Base;'
     'struct Middle:Base{Middle():Base(){} using Label=Middle;int f()override{return 42;}};'
     'struct Derived:Middle{Derived():Middle(){}};'
     'int cpp_test_entry(){Derived obj;Derived*p=&obj;return p->Label::f();}'),
)
INVALID = (
    ('original_private_alias', PRIVATE_ALIAS, 'cpp.member-access'),
    ('inherited_private_alias',
     'struct Base{int f(){return 42;}};using Label=Base;'
     'struct Middle:Base{private:using Label=Base;};struct Derived:Middle{};'
     'int g(Derived*p){return p->Label::f();}', 'cpp.member-access'),
    ('unrelated_receiver_alias',
     'struct Base{int f(){return 42;}};struct Other{int f(){return 7;}};using Label=Base;'
     'struct Derived:Base{using Label=Other;};int g(Derived*p){return p->Label::f();}',
     'cpp.invalid-member-access'),
)
# Legal type-only qualifier lookup is broader than this bounded owner. Keep
# this as an explicit PCC Unsupported capability, NOT a semantic-negative pass.
NON_TYPE_BOUNDARY = (
    'struct Base{Base(){} virtual int f(){return 1;}};using Label=Base;'
    'struct Middle:Base{Middle():Base(){} using Label=Middle;int f()override{return 42;}};'
    'struct Derived:Middle{int Label;Derived():Middle(),Label(0){}};'
    'int cpp_test_entry(){Derived obj;return obj.Label::f();}'
)


def _host():
    if sys.platform != 'linux' or platform.machine() not in ('x86_64', 'AMD64'):
        pytest.skip('Linux x86_64 only')


def _compiler():
    compiler = shutil.which('g++')
    if compiler is None:
        pytest.skip('actual G++ unavailable; no substitute')
    return compiler


def _owned(units, monkeypatch):
    original_import = builtins.__import__
    def checked_import(name, *args, **kwargs):
        if name == 'llvmlite' or name.startswith('llvmlite.'):
            raise AssertionError('owned compilation imported external backend')
        return original_import(name, *args, **kwargs)
    def forbidden(*args, **kwargs):
        raise AssertionError('owned compilation started external process')
    with monkeypatch.context() as guard:
        guard.setattr(builtins, '__import__', checked_import)
        guard.setattr(subprocess, 'Popen', forbidden)
        return compile_host_cpp(units)


@pytest.mark.parametrize('name,source', POSITIVE, ids=[r[0] for r in POSITIVE])
def test_receiver_first_production_parser(name, source):
    CppParser(TARGET).parse(source, name + '.cpp')


@pytest.mark.parametrize('name,source,code', INVALID, ids=[r[0] for r in INVALID])
def test_alias_access_and_nominal_base_not_lexical_fallback(name, source, code):
    with pytest.raises(CppDiagnostic) as caught:
        CppParser(TARGET).parse(source, name + '.cpp')
    assert caught.value.code == code
    assert not caught.value.code.startswith('cpp.unsupported.')


def test_non_type_collision_keeps_explicit_unsupported():
    with pytest.raises(CppDiagnostic) as caught:
        CppParser(TARGET).parse(NON_TYPE_BOUNDARY, 'non_type.cpp')
    assert caught.value.code == 'cpp.unsupported.base-classes'


@pytest.mark.integration
@pytest.mark.parametrize('name,source', POSITIVE, ids=[r[0] for r in POSITIVE])
def test_identical_source_owned_and_gcc17_return42(tmp_path, monkeypatch, name, source):
    _host()
    compiler = _compiler()
    units = ((name + '.cpp', source),)
    actual, errors = {}, {}
    for lane in ('owned', 'gcc'):
        try:
            if lane == 'owned':
                artifact = _owned(units, monkeypatch)
                assert artifact.owner == 'host-python/pcc-cpp/pcc-self/pcc-elf'
                binary = tmp_path / 'owned'
                write_host_executable(artifact, binary)
            else:
                binary = compile_reference_cpp(units, tmp_path / 'gcc', compiler=compiler)
                assert (tmp_path / 'gcc' / units[0][0]).read_bytes() == source.encode()
            actual[lane] = run_executable(binary)
        except Exception as error:
            errors[lane] = error
    (tmp_path / 'observations.json').write_text(json.dumps({
        'source_sha256': hashlib.sha256(source.encode()).hexdigest(),
        'executions': {lane: {'returncode': r.returncode, 'stdout_hex': r.stdout.hex(),
                              'stderr_hex': r.stderr.hex()} for lane, r in actual.items()},
        'errors': {lane: repr(error) for lane, error in errors.items()},
    }, indent=2) + '\n')
    if errors:
        raise next(iter(errors.values()))
    for result in actual.values():
        assert_exact(result, Execution(42, b'\x2a\x00\x00\x00', b''))


@pytest.mark.integration
@pytest.mark.parametrize('name,source,code', INVALID, ids=[r[0] for r in INVALID])
def test_same_invalid_source_gcc17_rejects(tmp_path, name, source, code):
    _host()
    path = tmp_path / (name + '.cpp')
    path.write_bytes(source.encode())
    result = subprocess.run([_compiler(), '-std=c++17', '-pedantic-errors',
                             '-fsyntax-only', str(path)], capture_output=True, timeout=10)
    (tmp_path / 'gcc.stderr').write_bytes(result.stderr)
    assert path.read_bytes() == source.encode()
    assert result.returncode == 1 and result.stdout == b''
    assert b'error:' in result.stderr
    assert b'internal compiler error' not in result.stderr.lower()


@pytest.mark.integration
def test_non_type_collision_is_not_a_semantic_negative(tmp_path):
    _host()
    units = (('non_type.cpp', NON_TYPE_BOUNDARY),)
    binary = compile_reference_cpp(units, tmp_path / 'gcc', compiler=_compiler())
    assert (tmp_path / 'gcc' / 'non_type.cpp').read_bytes() == NON_TYPE_BOUNDARY.encode()
    assert_exact(run_executable(binary), Execution(42, b'\x2a\x00\x00\x00', b''))
