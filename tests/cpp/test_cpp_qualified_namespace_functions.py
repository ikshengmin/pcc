"""Qualified namespace declarations: C++17 source obligations, CI execution.

N4659 [namespace.memdef]/2, [namespace.qual]/6-7, [basic.lookup.qual]/3:
https://timsong-cpp.github.io/cppwp/n4659/namespace.memdef
https://timsong-cpp.github.io/cppwp/n4659/namespace.qual
https://timsong-cpp.github.io/cppwp/n4659/basic.lookup.qual

These are independently authored originals, not token-rewritten inputs. The
host-owned lane and external G++17 reference are separate; no local execution,
native pcc1, general templates or whole TinyXML2 acceptance is implied.
"""

import hashlib
import json
import os
import shutil
import struct
import subprocess
import traceback

import pytest

from pcc.frontends.cpp import CppDiagnostic, CppParser
from tests.cpp.owned_execution import (
    Execution, assert_exact, compile_host_cpp, compile_reference_cpp,
    run_executable, write_host_executable,
)
from tests.cpp.test_cpp_grouped_names import owned_only


ORIGINAL_QUALIFIED_DEFINITION = 'namespace A {int f();}int A::f(){return 1;}'

CASES = (
    ('original-qualified-definition', (
        ('entry.cpp', ORIGINAL_QUALIFIED_DEFINITION +
         'int cpp_test_entry(){return A::f()+41;}'),)),
    ('parameter-and-body-namespace', (
        ('entry.cpp', 'using I=long;int helper(){return 1;}'
         'namespace A{using I=int;int helper(){return 17;}int f(I);}'
         'int A::f(I value){return helper()+value;}'
         'int cpp_test_entry(){return A::f(25);}'),)),
    ('return-before-name-parameter-after-name', (
        ('entry.cpp', 'using T=long;namespace A{using T=int;long f(T);}'
         'T A::f(T value){return value;}'
         'int cpp_test_entry(){return int(A::f(42));}'),)),
    ('nested-enclosing-namespace', (
        ('entry.cpp', 'namespace A{namespace B{int f(int);}'
         'int B::f(int value){return value;}}'
         'int cpp_test_entry(){return A::B::f(42);}'),)),
    ('namespace-alias-owner', (
        ('entry.cpp', 'namespace A{int f();}namespace Alias=A;'
         'int Alias::f(){return 42;}int cpp_test_entry(){return A::f();}'),)),
    ('qualifier-found-through-directive', (
        ('entry.cpp', 'namespace Outer{namespace Inner{int f();}}'
         'using namespace Outer;int Inner::f(){return 42;}'
         'int cpp_test_entry(){return Outer::Inner::f();}'),)),
    ('qualifier-lookup-ignores-non-type-hiding', (
        ('entry.cpp', 'namespace A{int A=1;int f();int A::f(){return A+41;}}'
         'int cpp_test_entry(){return A::f();}'),)),
    ('inline-namespace-canonical-owner', (
        ('entry.cpp', 'namespace A{inline namespace V{using I=int;int f(I);}}'
         'int A::f(I value){return value;}'
         'int cpp_test_entry(){return A::V::f(42);}'),)),
    ('explicit-inline-namespace-owner', (
        ('entry.cpp', 'namespace A{inline namespace V{int f();}}'
         'int A::V::f(){return 42;}int cpp_test_entry(){return A::f();}'),)),
    ('qualified-redeclaration-same-entity', (
        ('entry.cpp', 'namespace A{int f(int);}int A::f(int);'
         'int A::f(int value){return value;}int cpp_test_entry(){return A::f(42);}'),)),
    ('distinct-overloads-retain-signatures', (
        ('entry.cpp', 'namespace A{int f(int);int f(long);}'
         'int A::f(int value){return value+17;}int A::f(long value){return int(value)+1;}'
         'int cpp_test_entry(){return A::f(23)+A::f(1L);}'),)),
    ('using-declaration-retains-definition-identity', (
        ('entry.cpp', 'namespace A{int f();}using A::f;int A::f(){return 42;}'
         'int cpp_test_entry(){return f();}'),)),
    ('default-in-qualified-definition', (
        ('entry.cpp', 'namespace A{enum E{answer=42};int f(int);}'
         'int A::f(int value=answer){return value;}int cpp_test_entry(){return A::f();}'),)),
    ('inherited-default', (
        ('entry.cpp', 'namespace A{int f(int value=42);}int A::f(int value){return value;}'
         'int cpp_test_entry(){return A::f();}'),)),
    ('default-union-in-same-canonical-scope', (
        ('entry.cpp', 'namespace A{int f(int first,int second=25);}'
         'int A::f(int first=17,int second){return first+second;}'
         'int cpp_test_entry(){return A::f();}'),)),
    ('global-qualification', (
        ('entry.cpp', 'int f(int);int ::f(int value){return value;}'
         'int cpp_test_entry(){return f(42);}'),)),
    ('namespace-main-is-not-program-main', (
        ('entry.cpp', 'namespace A{long main(int);}long A::main(int value){return value;}'
         'int cpp_test_entry(){return int(A::main(42));}'),)),
    ('c-linkage-inherited', (
        ('entry.cpp', 'namespace A{extern "C" int answer(int);}'
         'int A::answer(int value){return value;}'
         'int cpp_test_entry(){return A::answer(42);}'),)),
    ('pointer-result-and-parameter', (
        ('entry.cpp', 'namespace A{int*f(int*);}int*A::f(int*p){return p;}'
         'int cpp_test_entry(){int value=42;return *A::f(&value);}'),)),
    ('adjusted-array-parameter', (
        ('entry.cpp', 'namespace A{int f(int value[2]);}'
         'int A::f(int value[2]){return value[0]+value[1];}'
         'int cpp_test_entry(){int value[2]={17,25};return A::f(value);}'),)),
    ('two-tu-and-eight-argument-slots', (
        ('definition.cpp', 'namespace A{int f(int,int,int,int,int,int,int,int);}'
         'int A::f(int a,int b,int c,int d,int e,int f,int g,int h){'
         'if(a!=1||b!=2||c!=3||d!=4||e!=5||f!=6||g!=7||h!=8)return 1;return 42;}'),
        ('entry.cpp', 'namespace A{int f(int,int,int,int,int,int,int,int);}'
         'int cpp_test_entry(){return A::f(1,2,3,4,5,6,7,8);}'))),
)

INVALID = (
    ('namespace A{}int A::f(){return 0;}', 'cpp.namespace-member-not-found'),
    ('namespace A{int f(int);}int A::f(long value){return 0;}', 'cpp.no-matching-namespace-function'),
    ('namespace A{int f();}namespace Other{int A::f(){return 0;}}', 'cpp.namespace-definition-scope'),
    ('namespace A{int f();}namespace A{namespace Inner{int A::f(){return 0;}}}', 'cpp.namespace-definition-scope'),
    ('namespace A{int f();}long A::f(){return 0;}', 'cpp.return-type-overload'),
    ('namespace A{int f();}int A::f(){return 1;}int A::f(){return 2;}', 'cpp.redefinition'),
    ('namespace B{int f();}namespace A{using namespace B;}int A::f(){return 0;}', 'cpp.namespace-member-not-found'),
    ('namespace B{int f();}namespace A{using B::f;}int A::f(){return 0;}', 'cpp.namespace-member-not-found'),
    ('namespace A{int value;}int A::value(){return 0;}', 'cpp.namespace-member-not-found'),
    # [namespace.qual]/6: cross-namespace class/enum names are not hidden by
    # functions. Validate the full lookup set before declaration-owner filtering.
    ('namespace A{struct f{};inline namespace V{int f();}}int A::f(){return 42;}', 'cpp.ambiguous-name'),
    ('namespace A{int f();inline namespace V{struct f{};}}int A::f(){return 42;}', 'cpp.ambiguous-name'),
    ('namespace A{enum f{value};inline namespace V{int f();}}int A::f(){return 42;}', 'cpp.ambiguous-name'),
    ('namespace A{int f;inline namespace V{int f();}}int A::f(){return 42;}', 'cpp.ambiguous-name'),
    ('namespace A{int f();}int A::f(){return later();}namespace A{int later();}', 'cpp.undeclared'),
    ('using T=long;namespace A{using T=int;int f(T);}T A::f(T value){return value;}', 'cpp.return-type-overload'),
    ('namespace A{using T=int;int f(int,int);}int A::f(int T,T value){return value;}', 'cpp.not-a-type'),
    ('namespace A{int f(int value=1);}int A::f(int value=1){return value;}', 'cpp.default-argument-redefinition'),
    ('namespace A{int f(int,int);}int A::f(int first=1,int second){return first+second;}', 'cpp.default-argument-trailing'),
)

DEFERRED = (
    # Valid source; neither a namespace-object definition nor cross-inline
    # overload-context selection is implemented by this function-only slice.
    'namespace A{extern int value;}int A::value=42;',
    'namespace A{int f();}int (A::f)(){return 42;}',
    'namespace A{inline namespace V{int f(int);}int f(long);}'
    'int A::f(int value){return value;}',
)


@pytest.mark.parametrize('name,units', CASES, ids=[row[0] for row in CASES])
def test_qualified_namespace_original_compiles_deterministically(owned_only, name, units):
    first = compile_host_cpp(units)
    assert first == compile_host_cpp(units)
    assert first.owner == 'host-python/pcc-cpp/pcc-self/pcc-elf'


def test_canonical_owner_and_body_namespace_are_retained():
    parser = CppParser()
    tree = parser.parse('namespace A{using I=int;int f(I);}using A::f;'
                        'int A::f(I value){return value;}')
    owner = parser.scope.names['A'].namespace_scope
    symbol = owner.names['f'].overloads[0]
    assert parser.scope.names['f'].overloads[0] is symbol
    assert symbol.owner is owner and symbol.declaration_scopes == [owner]
    assert symbol.defined and symbol.parameters == ('int',)
    assert symbol.c_name == '_ZN1A1fEi'
    assert tree.ext[-1].decl.name == symbol.c_name


@pytest.mark.parametrize('source,code', INVALID)
def test_qualified_namespace_invalid_before_emission(source, code, monkeypatch):
    from pcc.backend import owned_object_emit

    def forbidden(*args, **kwargs):
        raise AssertionError('invalid namespace definition reached object emission')

    monkeypatch.setattr(owned_object_emit, 'emit_owned_object', forbidden)
    with pytest.raises(CppDiagnostic) as error:
        compile_host_cpp((('invalid.cpp', source),))
    assert error.value.code == code


@pytest.mark.parametrize('source', DEFERRED)
def test_qualified_namespace_remaining_valid_gaps(source):
    with pytest.raises(CppDiagnostic) as error:
        CppParser().parse(source)
    assert error.value.code == 'cpp.unsupported.qualified-names'


def _capture(binary):
    actual = run_executable(binary)
    binary.with_suffix('.stdout.bin').write_bytes(actual.stdout)
    binary.with_suffix('.stderr.bin').write_bytes(actual.stderr)
    binary.with_suffix('.execution.json').write_text(json.dumps({
        'returncode': actual.returncode, 'stdout_hex': actual.stdout.hex(),
        'stderr_hex': actual.stderr.hex(), 'binary': str(binary),
        'binary_sha256': hashlib.sha256(binary.read_bytes()).hexdigest(),
    }, indent=2))
    return actual


@pytest.mark.integration
@pytest.mark.parametrize('name,units', CASES, ids=[row[0] for row in CASES])
def test_qualified_namespace_same_original_owned_and_reference(tmp_path, monkeypatch, name, units):
    originals = tmp_path / 'originals'
    originals.mkdir()
    for filename, source in units:
        (originals / filename).write_text(source)
    actual = owned_error = None
    try:
        with monkeypatch.context() as guard:
            def forbidden(*args, **kwargs):
                raise AssertionError('owned namespace compilation invoked an external process')
            guard.setattr(subprocess, 'Popen', forbidden)
            artifact = compile_host_cpp(units)
        for index, (ir, obj) in enumerate(zip(artifact.ir, artifact.objects)):
            (tmp_path / ('owned-' + str(index) + '.ll')).write_text(ir)
            (tmp_path / ('owned-' + str(index) + '.o')).write_bytes(obj)
        binary = tmp_path / 'owned'
        write_host_executable(artifact, binary)
        actual = _capture(binary)
    except Exception as error:
        owned_error = error
        (tmp_path / 'owned-error.txt').write_text(traceback.format_exc())
    compiler = shutil.which(os.environ.get('PCC_CPP_ORACLE', 'g++'))
    assert compiler, 'explicit C++17 reference required'
    reference = compile_reference_cpp(units, tmp_path / 'reference', compiler=compiler)
    oracle = _capture(reference)
    for filename, _ in units:
        assert (originals / filename).read_bytes() == (tmp_path / 'reference' / filename).read_bytes()
    if owned_error is not None:
        raise owned_error
    expected = Execution(42, struct.pack('<i', 42), b'')
    assert_exact(actual, expected)
    assert_exact(oracle, expected)


@pytest.mark.integration
@pytest.mark.parametrize('source,code', INVALID)
def test_qualified_namespace_invalid_original_reference(source, code, tmp_path):
    compiler = shutil.which(os.environ.get('PCC_CPP_ORACLE', 'g++'))
    assert compiler
    path = tmp_path / 'invalid.cpp'
    path.write_text(source)
    result = subprocess.run([compiler, '-std=c++17', '-pedantic-errors', '-fsyntax-only', str(path)],
                            capture_output=True, timeout=20)
    (tmp_path / 'reference.stdout.bin').write_bytes(result.stdout)
    (tmp_path / 'reference.stderr.bin').write_bytes(result.stderr)
    (tmp_path / 'reference.json').write_text(json.dumps({'returncode': result.returncode}))
    assert result.returncode != 0


@pytest.mark.integration
@pytest.mark.parametrize('source', DEFERRED)
def test_remaining_valid_namespace_gaps_reference_only(source, tmp_path):
    compiler = shutil.which(os.environ.get('PCC_CPP_ORACLE', 'g++'))
    assert compiler
    path = tmp_path / 'valid-gap.cpp'
    path.write_text(source)
    result = subprocess.run([compiler, '-std=c++17', '-pedantic-errors', '-fsyntax-only', str(path)],
                            capture_output=True, timeout=20)
    (tmp_path / 'reference.stdout.bin').write_bytes(result.stdout)
    (tmp_path / 'reference.stderr.bin').write_bytes(result.stderr)
    (tmp_path / 'reference.json').write_text(json.dumps({'returncode': result.returncode}))
    assert result.returncode == 0
