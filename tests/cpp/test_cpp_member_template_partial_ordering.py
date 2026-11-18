"""Symbolic partial ordering and identical-source C++17 member-call obligations.

No full class-template, NTTP, array, pack or free-function ordering claim.
"""
import os
import platform
import shutil
import struct
import subprocess
import sys
from types import SimpleNamespace

import pytest

from pcc.frontends.cpp import CppDiagnostic, CppParser
from pcc.frontends.cpp.member_template_ordering import compare_member_template_order
from pcc.frontends.cpp.templates import TypeParameter, ValueParameter
from pcc.frontends.cpp.types import ConstType, PointerType, ReferenceType
from tests.cpp.owned_execution import (
    Execution, assert_exact, compile_host_cpp, compile_reference_cpp,
    run_executable, write_host_executable,
)


def origin(owner, shapes):
    parameters = (TypeParameter(0, 'T', None), TypeParameter(1, 'U', None))

    def dtype(shape):
        if shape in ('T', 'U'):
            return parameters[('T', 'U').index(shape)]
        if isinstance(shape, tuple):
            constructor = {'p': PointerType, 'c': ConstType, 'r': ReferenceType}[shape[0]]
            return constructor(dtype(shape[1]))
        return shape

    return SimpleNamespace(class_owner=owner, template_parameters=parameters,
                           parameters=tuple(dtype(s) for s in shapes),
                           method_static=False, method_const=False, variadic=False)


@pytest.mark.parametrize('first,second,count,expected', [
    (('T',), (('p', 'T'),), 1, 1),
    ((('p', 'T'),), (('p', ('c', 'T')),), 1, 1),
    ((('p', ('c', 'T')),), ('T',), 1, -1),
    ((('p', ('p', 'T')),), (('p', 'T'),), 1, -1),
    (('T',), (('r', 'T'),), 1, 0),
    ((('r', 'T'),), (('r', ('c', 'T')),), 1, 1),
    ((('r', ('p', 'T')),), (('r', ('c', ('p', 'T'))),), 1, 1),
    (('T', 'T'), ('T', 'U'), 2, -1),
    (('T', ('p', 'T')), ('T', ('p', 'U')), 2, -1),
    ((('p', 'T'), 'U'), ('T', ('p', 'U')), 2, 0),
    ((('p', 'T'), 'T'), (('p', 'U'), 'U'), 2, 0),
    (('T', 'T'), ('T', 'U'), 1, 0),
    (('T',), (('p', 'T'), 'int'), 1, 1),
])
def test_order_is_bidirectional_structural_deduction(first, second, count, expected):
    owner = object()
    a, b = origin(owner, first), origin(owner, second)
    assert compare_member_template_order(a, b, count) == expected
    assert compare_member_template_order(b, a, count) == -expected
    assert compare_member_template_order(a, a, count) == 0


def test_unsupported_is_not_proved_ambiguity_or_winner():
    owner = object()
    a, b = origin(owner, ('T',)), origin(owner, (('p', 'T'),))
    for attr, value in [('method_static', True), ('method_const', True),
                        ('method_volatile', True), ('method_ref_qualifier', '&&'),
                        ('variadic', True), ('class_owner', object())]:
        old = getattr(b, attr, None)
        setattr(b, attr, value)
        assert compare_member_template_order(a, b, 1) is None
        setattr(b, attr, old)
    b.template_parameters = (ValueParameter(0, 'N', 'int', None),)
    assert compare_member_template_order(a, b, 1) is None
    b = origin(owner, ('int',))
    assert compare_member_template_order(a, b, 1) is None
    a.class_owner = b.class_owner = None
    assert compare_member_template_order(a, b, 1) is None


CASES = (
    ('pointer-over-value', 'struct M{template<class T>int f(T x){return 17;}'
     'template<class U>int f(U* x){return 42;}};'
     'int cpp_test_entry(){M m;int x=0;return m.f(&x);}'),
    ('const-pointer-over-pointer', 'struct M{template<class T>int f(T* x){return 17;}'
     'template<class U>int f(const U* x){return 42;}};'
     'int cpp_test_entry(){M m;const int x=0;return m.f(&x);}'),
    ('repeated-binding', 'struct M{template<class T>int f(T x,T y){return 42;}'
     'template<class U,class V>int f(U x,V y){return 17;}};'
     'int cpp_test_entry(){M m;return m.f(1,2);}'),
    ('reference-const', 'struct M{template<class T>int f(T& x){return 17;}'
     'template<class U>int f(const U& x){return 42;}};'
     'int cpp_test_entry(){M m;const int x=0;return m.f(x);}'),
    ('double-pointer', 'struct M{template<class T>int f(T* x){return 17;}'
     'template<class U>int f(U** x){return 42;}};'
     'int cpp_test_entry(){M m;int x=0;int*p=&x;return m.f(&p);}'),
)
AMBIGUOUS = (
    ('value-reference-tie', 'struct M{template<class T>int f(T x){return 17;}'
     'template<class U>int f(U& x){return 42;}};'
     'int cpp_test_entry(){M m;int x=0;return m.f(x);}'),
    ('crossed-pointer-constraints', 'struct M{template<class T,class U>int f(T* x,U y){return 17;}'
     'template<class T,class U>int f(T x,U* y){return 42;}};'
     'int cpp_test_entry(){M m;int x=0;return m.f(&x,&x);}'),
)


@pytest.mark.parametrize('name,source', AMBIGUOUS, ids=[x[0] for x in AMBIGUOUS])
def test_supported_incomparable_templates_are_ambiguous(name, source):
    with pytest.raises(CppDiagnostic) as caught:
        CppParser(target_triple='x86_64-unknown-linux-gnu').parse(source, name+'.cpp')
    assert caught.value.code == 'cpp.ambiguous-overload'


@pytest.mark.integration
@pytest.mark.parametrize('name,source', CASES, ids=[x[0] for x in CASES])
def test_same_source_ordering_has_owned_and_cpp17_execution(tmp_path, monkeypatch, name, source):
    assert sys.platform == 'linux' and platform.machine() in ('x86_64', 'AMD64')
    compiler = shutil.which(os.environ.get('PCC_CPP_ORACLE', 'g++'))
    assert compiler
    units = ((name+'.cpp', source),)
    oracle = compile_reference_cpp(units, tmp_path/'reference', compiler=compiler)
    expected = Execution(42, struct.pack('<i', 42), b'')
    assert (tmp_path/'reference'/(name+'.cpp')).read_bytes() == source.encode()
    assert_exact(run_executable(oracle), expected)

    def forbidden(*a, **k):
        raise AssertionError('owned partial-ordering compile attempted external process')

    with monkeypatch.context() as guard:
        guard.setattr(subprocess, 'Popen', forbidden)
        artifact = compile_host_cpp(units)
    assert artifact.owner == 'host-python/pcc-cpp/pcc-self/pcc-elf'
    owned = tmp_path/'owned'
    write_host_executable(artifact, owned)
    assert_exact(run_executable(owned), expected)


@pytest.mark.integration
@pytest.mark.parametrize('name,source', AMBIGUOUS, ids=[x[0] for x in AMBIGUOUS])
def test_same_source_ambiguity_has_cpp17_diagnostic(tmp_path, name, source):
    compiler = shutil.which(os.environ.get('PCC_CPP_ORACLE', 'g++'))
    assert compiler
    path = tmp_path/(name+'.cpp')
    path.write_bytes(source.encode())
    result = subprocess.run([compiler, '-std=c++17', '-pedantic-errors', '-fsyntax-only', str(path)],
                            capture_output=True, timeout=10)
    assert result.returncode == 1, result.stderr
    assert b'ambiguous' in result.stderr.lower()
    assert b'internal compiler error' not in result.stderr.lower()
    assert result.stdout == b''
    assert path.read_bytes() == source.encode()
