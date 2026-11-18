"""Non-template class member templates: typed, host-owned and C++17 checks.

N4659 [temp.mem], [temp.deduct.call], [temp.over.link], [class.access]:
https://timsong-cpp.github.io/cppwp/n4659/temp.mem
https://timsong-cpp.github.io/cppwp/n4659/temp.deduct.call
https://timsong-cpp.github.io/cppwp/n4659/temp.over.link
https://timsong-cpp.github.io/cppwp/n4659/class.access

These independently authored programs do not replace TinyXML2 or public CLI
qualification. The observer entry programs use the existing limited Linux x86
host adapter, not native pcc1. Identical source tuples feed both compilers.
Original main/printf/state/extern-template programs remain separate obligations.
"""
import builtins
import hashlib
import json
import os
import platform
import re
import shutil
import struct
import subprocess
import sys

import pytest

from pcc.frontends.cpp import CppDiagnostic, CppParser
from tests.cpp.owned_execution import (
    Execution, assert_exact, compile_host_cpp, compile_reference_cpp,
    run_executable, write_host_executable,
)
from tests.cpp.test_cpp_owned_execution import owned_only


TARGET = 'x86_64-unknown-linux-gnu'

CASES = (('inline-scalar',
  (('a.cpp',
    'struct S{template<class T> T f(T x){return x;}};int cpp_test_entry(){S s;return '
    's.f<int>(42);}'),),
  42),
 ('out-field-rename',
  (('a.cpp',
    'struct S{int bias;template<class T>T add(T);};template<class U>inline U S::add(U v){return '
    'this->bias+v;}int cpp_test_entry(){S s={20};return s.add<int>(22);}'),),
  42),
 ('pool-mutation',
  (('a.cpp',
    'template<int N>struct Pool{int n;};struct S{int bias;template<class T,int N>T add(Pool<N>& '
    'p){p.n+=N;return T(bias+p.n);}};int cpp_test_entry(){Pool<4> p={10};S s={3};int '
    'a=s.add<int>(p);int b=s.add<long>(p);return a+b+p.n-14;}'),),
  42),
 ('pool-const',
  (('a.cpp',
    'template<int N>struct Pool{int n;};struct S{template<int N>int get(const Pool<N>& '
    'p)const{return p.n+N;}};int cpp_test_entry(){const Pool<2> p={40};const S s={};return '
    's.get(p);}'),),
  42),
 ('receiver-once',
  (('a.cpp',
    'int calls;struct S{int bias;template<class T>int add(T x){return bias+x;}};S* '
    'select(S*p){calls++;return p;}int cpp_test_entry(){S s={40};return '
    'select(&s)->add<int>(1)+calls;}'),),
  42),
 ('ordinary-vs-template',
  (('a.cpp',
    'struct S{int f(int x){return x+1;}template<class T>int f(T x){return int(x)-1;}};int '
    'cpp_test_entry(){S s;return s.f(10)+s.f<int>(32);}'),),
  42),
 ('two-tu-function-identity',
  (('a.cpp',
    'struct S{template<class T> int f(T v){return int(v);}};int one(){S s;return s.f<int>(21); }'),
   ('b.cpp',
    'struct S{template<class T> int f(T v){return int(v);}};int one();int cpp_test_entry(){S '
    's;return one()+s.f<int>(21);}')),
  42),
 ('late-definition-after-call',
  (('late.cpp',
    'struct S{template<class T>T f(T);};int cpp_test_entry(){S s;return '
    's.f<int>(42);}template<class U>U S::f(U x){return x;}'),),
  42),
 ('different-class-identity',
  (('owners.cpp',
    'struct A{template<class T>int f(T x){return int(x)+10;}};struct B{template<class T>int f(T '
    'x){return int(x)+20;}};int cpp_test_entry(){A a;B b;return a.f<int>(1)+b.f<int>(11);}'),),
  42))

INVALID = (('private',
  'class M {template<class T> int f(T x){return x;}};int main(){M m;return m.f<int>(1);}',
  'cpp.member-access'),
 ('const-receiver',
  'struct M {template<class T> int f(T x){return x;}};int main(){const M m={};return m.f<int>(1);}',
  'cpp.no-viable-overload'),
 ('virtual-template',
  'struct M {template<class T> virtual int f(T x){return x;}};int main(){}',
  'cpp.virtual-member-template'),
 ('definition-mismatch',
  'struct M {template<class T> int f(T);};template<class T> int M::f(T* p){return 1;}int main(){}',
  'cpp.no-matching-member'),
 ('duplicate-definition',
  'struct M {template<class T> int f(T x){return x;}};template<class T> int M::f(T x){return '
  'x;}int main(){}',
  'cpp.redefinition'),
 ('different-pool-owner',
  'template<int N>struct P{int n;};template<int N>struct Q{int n;};struct M{template<class T,int '
  'N>int f(P<N>& p){return N;}};int main(){M m;Q<3> q={0};return m.f<int>(q);}',
  'cpp.no-viable-overload'),
 ('repeated-deduction-conflict',
  'template<int N>struct P{int n;};struct M{template<class T,int N>int f(P<N>& p,P<N>& q){return '
  'N;}};int main(){M m;P<3> p={0};P<4> q={0};return m.f<int>(p,q);}',
  'cpp.no-viable-overload'),
 ('explicit-value-conflict',
  'template<int N>struct P{int n;};struct M{template<class T,int N>int f(P<N>& p){return N;}};int '
  'main(){M m;P<3> p={0};return m.f<int,4>(p);}',
  'cpp.no-viable-overload'),
 ('const-pool-to-mutable',
  'template<int N>struct P{int n;};struct M{template<int N>int f(P<N>& p){return N;}};int main(){M '
  'm;const P<3> p={0};return m.f(p);}',
  'cpp.no-viable-overload'),
 ('ambiguous',
  'struct M{template<class T>int f(T x,long y){return 1;}template<class T>int f(long x,T y){return '
  '2;}};int main(){M m;return m.f(1,1);}',
  'cpp.ambiguous-overload'))


@pytest.mark.parametrize('name,units,value', CASES, ids=[row[0] for row in CASES])
def test_member_template_same_originals_parse(name, units, value):
    for filename, source in units:
        CppParser(target_triple=TARGET).parse(source, filename)


@pytest.mark.parametrize('name,source,code', INVALID, ids=[row[0] for row in INVALID])
def test_member_template_invalid_original_has_semantic_diagnostic(owned_only, name, source, code):
    filename = name + '.cpp'
    with pytest.raises(CppDiagnostic) as caught:
        CppParser(target_triple=TARGET).parse(source, filename)
    assert caught.value.code == code
    assert not caught.value.code.startswith('cpp.unsupported.')
    assert caught.value.coord.file == filename
    assert caught.value.coord.line >= 1


@pytest.mark.parametrize('name,units,value', CASES, ids=[row[0] for row in CASES])
def test_member_template_host_artifact_owned_and_deterministic(owned_only, name, units, value):
    first = compile_host_cpp(units)
    second = compile_host_cpp(units)
    assert first.owner == second.owner == 'host-python/pcc-cpp/pcc-self/pcc-elf'
    assert first.ir == second.ir
    assert first.objects == second.objects
    assert first.executable == second.executable
    if name == 'two-tu-function-identity':
        from pcc.backend.elf_x86_64 import STB_WEAK, parse_relocatable
        symbols = [
            {s.name: s for s in parse_relocatable(obj).symbols
             if s.section_index and s.name.startswith('_ZN1S1fI')}
            for obj in first.objects
        ]
        assert len(symbols) == 2
        shared = set(symbols[0]).intersection(symbols[1])
        assert len(shared) == 1
        assert all(table[next(iter(shared))].binding == STB_WEAK for table in symbols)


def _reference_compiler():
    compiler = shutil.which(os.environ.get('PCC_CPP_ORACLE', 'g++'))
    assert compiler, 'explicit C++17 reference compiler unavailable; set PCC_CPP_ORACLE'
    return compiler


def _owned_artifact(units, monkeypatch):
    original_import = builtins.__import__

    def checked_import(name, *args, **kwargs):
        if name == 'llvmlite' or name.startswith('llvmlite.'):
            raise AssertionError('owned member-template compilation imported ' + name)
        return original_import(name, *args, **kwargs)

    def forbidden(*args, **kwargs):
        raise AssertionError('owned member-template compilation attempted an external process')

    with monkeypatch.context() as guard:
        guard.setattr(builtins, '__import__', checked_import)
        guard.setattr(subprocess, 'Popen', forbidden)
        return compile_host_cpp(units)


@pytest.mark.integration
@pytest.mark.parametrize('name,units,value', CASES, ids=[row[0] for row in CASES])
def test_member_template_identical_tus_execute_owned_and_cpp17(
    tmp_path, monkeypatch, name, units, value,
):
    assert sys.platform == 'linux' and platform.machine() in ('x86_64', 'AMD64'), (
        'this byte observer requires Linux x86_64; no skipped platform qualification'
    )
    compiler = _reference_compiler()
    identities = {filename: hashlib.sha256(source.encode()).hexdigest()
                  for filename, source in units}
    errors, actual = {}, {}
    for lane in ('owned', 'reference'):
        try:
            if lane == 'owned':
                artifact = _owned_artifact(units, monkeypatch)
                assert artifact.owner == 'host-python/pcc-cpp/pcc-self/pcc-elf'
                binary = tmp_path / 'owned'
                write_host_executable(artifact, binary)
            else:
                binary = compile_reference_cpp(units, tmp_path / 'reference', compiler=compiler)
                for filename, source in units:
                    assert (tmp_path / 'reference' / filename).read_bytes() == source.encode()
            actual[lane] = run_executable(binary)
        except Exception as error:
            # Preserve the second independent observation even if the first
            # compilation fails. Neither lane supplies a fallback artifact.
            errors[lane] = error
    (tmp_path / 'observations.json').write_text(json.dumps({
        'source_sha256': identities,
        'executions': {lane: {'returncode': result.returncode,
                             'stdout_hex': result.stdout.hex(),
                             'stderr_hex': result.stderr.hex()}
                       for lane, result in actual.items()},
        'errors': {lane: repr(error) for lane, error in errors.items()},
    }, indent=2) + '\n')
    if errors:
        raise next(iter(errors.values()))
    expected = Execution(value & 255, struct.pack('<i', value), b'')
    assert_exact(actual['owned'], expected)
    assert_exact(actual['reference'], expected)
    assert_exact(actual['owned'], actual['reference'])


@pytest.mark.integration
@pytest.mark.parametrize('name,source,code', INVALID, ids=[row[0] for row in INVALID])
def test_member_template_same_invalid_source_rejected_by_cpp17(tmp_path, name, source, code):
    compiler = _reference_compiler()
    original = tmp_path / (name + '.cpp')
    original.write_bytes(source.encode())
    result = subprocess.run(
        [compiler, '-std=c++17', '-pedantic-errors', '-fsyntax-only', str(original)],
        capture_output=True, timeout=10,
    )
    (tmp_path / 'reference.stderr').write_bytes(result.stderr)
    (tmp_path / 'reference.json').write_text(json.dumps({
        'source_sha256': hashlib.sha256(original.read_bytes()).hexdigest(),
        'argv': result.args, 'returncode': result.returncode,
        'stdout_hex': result.stdout.hex(), 'stderr_hex': result.stderr.hex(),
        'owned_required_diagnostic': code,
    }, indent=2) + '\n')
    assert original.read_bytes() == source.encode()
    assert result.returncode == 1, result.stderr
    assert result.stdout == b''
    assert os.fsencode(original.name) in result.stderr
    assert re.search(rb':\d+:\d+: (?:fatal )?error:', result.stderr), result.stderr
    assert not any(marker in result.stderr.lower() for marker in (
        b'internal compiler error', b'please submit a full bug report',
        b'segmentation fault', b'stack dump', b'assertion failed',
    )), result.stderr
