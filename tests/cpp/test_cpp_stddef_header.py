"""N4659 [depr.c.headers], [cstddef.syn], [conv.ptr]: owned <stddef.h>.

Target assertions are syntax/type coverage, not native target qualification.
Executed artifacts below are host pcc0/self Linux x86_64, not native pcc1.
The external oracle receives the original source with its real system headers.
"""
import os
import shutil
import struct
import subprocess

import pytest

from pcc.frontends.cpp import CppDiagnostic, CppParser
from pcc.frontends.cpp.driver import preprocess_cpp
from tests.cpp.owned_execution import (
    Execution,
    compile_host_cpp,
    compile_reference_cpp,
    write_host_executable,
)
from tests.cpp.test_cpp_class_execution import record_execution
from tests.cpp.test_cpp_owned_execution import owned_only


HEADERS = (
    '#include <stddef.h>\n#include <stddef.h>\n',
    '#include <stddef.h>\n#include <cstddef>\n',
    '#include <cstddef>\n#include <stddef.h>\n',
)
BODY = (
    'int cpp_test_entry(){size_t n=43;ptrdiff_t d=-1;int*p=NULL;'
    'if(p!=nullptr)return 1;if(NULL!=0)return 2;return int(n)+int(d);}'
)
CROSS_UNITS = (
    ('provider.cpp', '#include <stddef.h>\n'
     'size_t add_delta(size_t n,ptrdiff_t d){return n+d;}'),
    ('caller.cpp', '#include <cstddef>\n#include <stddef.h>\n'
     'std::size_t add_delta(std::size_t,std::ptrdiff_t);'
     'int cpp_test_entry(){int*p=NULL;if(p!=nullptr)return 1;'
     'return int(add_delta(43,-1));}'),
)


@pytest.mark.parametrize('target,signed,unsigned', (
    ('x86_64-unknown-linux-gnu', 'long', 'unsigned long'),
    ('aarch64-unknown-linux-gnu', 'long', 'unsigned long'),
    ('x86_64-pc-windows-msvc', 'long long', 'unsigned long long'),
))
@pytest.mark.parametrize('header', HEADERS)
def test_stddef_global_aliases_share_cstddef_identity(header, target, signed, unsigned):
    parser = CppParser(target_triple=target)
    parser.parse(preprocess_cpp(header + BODY, target_triple=target))
    names = parser.scope.names
    std = names['std'].namespace_scope.names
    assert names['size_t'].dtype == unsigned
    assert names['ptrdiff_t'].dtype == signed
    for name in ('size_t', 'ptrdiff_t', 'max_align_t'):
        assert names[name].dtype == std[name].dtype
    assert not names['max_align_t'].dtype.complete


def test_stddef_nested_header_preserves_owned_lookup(tmp_path, owned_only):
    (tmp_path / 'api.h').write_text('#include <stddef.h>\nusing Count=size_t;\n')
    source = '#include "api.h"\n#include <stddef.h>\n' + BODY
    lowered = preprocess_cpp(source, base_dir=str(tmp_path))
    parser = CppParser()
    parser.parse(lowered)
    assert parser.scope.names['Count'].dtype == parser.scope.names['size_t'].dtype
    units = (('nested.cpp', lowered),)
    assert compile_host_cpp(units) == compile_host_cpp(units)


def test_stddef_later_nostdinc_still_disables_owned_default():
    with pytest.raises(RuntimeError, match='stddef.h'):
        preprocess_cpp('#include <stddef.h>\n', cpp_args=['-nostdinc'])


@pytest.mark.parametrize('tail', (
    'int f(){return sizeof(max_align_t);}',
    'struct S{int n;};int f(){return offsetof(S,n);}',
    'int f(){std::byte b;return int(b<<1);}',
))
def test_stddef_does_not_invent_layout_or_byte_support(tail):
    with pytest.raises(CppDiagnostic):
        CppParser().parse(preprocess_cpp('#include <stddef.h>\n' + tail))


@pytest.mark.parametrize('header', HEADERS)
def test_stddef_owned_artifacts_are_stable(header, owned_only):
    units = (('stddef.cpp', preprocess_cpp(header + BODY)),)
    first = compile_host_cpp(units)
    assert first == compile_host_cpp(units)
    assert first.owner == 'host-python/pcc-cpp/pcc-self/pcc-elf'


def test_stddef_aliases_link_across_owned_objects(owned_only):
    lowered = tuple((name, preprocess_cpp(source)) for name, source in CROSS_UNITS)
    first = compile_host_cpp(lowered)
    assert len(first.objects) == 2  # Separate provider and caller objects.
    assert first == compile_host_cpp(lowered)


@pytest.mark.integration
@pytest.mark.parametrize('units', (
    *((('stddef.cpp', header + BODY),) for header in HEADERS),
    CROSS_UNITS,
), ids=('repeated', 'stddef-first', 'cstddef-first', 'cross-object'))
def test_stddef_originals_execute_against_real_system_header_reference(units, tmp_path, monkeypatch):
    with monkeypatch.context() as guard:
        def forbidden(*args, **kwargs):
            raise AssertionError('owned stddef build invoked an external process')
        guard.setattr(subprocess, 'Popen', forbidden)
        lowered = tuple((name, preprocess_cpp(source)) for name, source in units)
        artifact = compile_host_cpp(lowered)
        write_host_executable(artifact, tmp_path / 'owned')
    expected = Execution(42, struct.pack('<i', 42), b'')
    record_execution(tmp_path / 'owned', expected)
    compiler = shutil.which(os.environ.get('PCC_CPP_ORACLE', 'g++'))
    assert compiler, 'C++17 system-header reference compiler is required'
    reference = compile_reference_cpp(units, tmp_path / 'reference', compiler=compiler)
    record_execution(reference, expected)
