"""C++17 N4659 [depr.c.headers], [cstdarg.syn]: the C-named varargs header.

Both owned and reference routes receive the exact same original translation
unit. This is Linux x86_64 header/varargs coverage, not NumPy acceptance.
"""
import os
import shutil
import struct
import subprocess

import pytest

from pcc.frontends.cpp import CppParser
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
    '#include <stdarg.h>\n#include <stdarg.h>\n',
    '#include <stdarg.h>\n#include <cstdarg>\n#include <cstdio>\n',
    '#include <cstdio>\n#include <cstdarg>\n#include <stdarg.h>\n',
)
BODY = (
    'int take(int tag,...){va_list ap;va_list copy;va_start(ap,tag);'
    'va_copy(copy,ap);int a=va_arg(copy,int);int b=va_arg(ap,int);'
    'va_end(copy);va_end(ap);return a+b;}'
    'int cpp_test_entry(){return take(0,21);}'
)


@pytest.mark.parametrize('header', HEADERS)
def test_stdarg_header_preserves_one_nominal_cursor_and_owned_emission(header, owned_only):
    source = header + BODY
    parser = CppParser()
    lowered = preprocess_cpp(source)
    parser.parse(lowered)
    owner = parser.scope.names['__pcc_sysv_va_list'].dtype
    alias = parser.scope.names['va_list'].dtype
    assert alias.bound == 1 and alias.element is owner
    if '<cstdarg>' in header:
        assert parser.scope.names['std'].namespace_scope.names['va_list'].dtype == alias
    units = (('stdarg.cpp', lowered),)
    assert compile_host_cpp(units) == compile_host_cpp(units)


@pytest.mark.integration
@pytest.mark.parametrize('header', HEADERS)
def test_stdarg_originals_execute_against_real_cpp17_reference(header, tmp_path, monkeypatch):
    source = header + BODY
    units = (('stdarg.cpp', source),)
    with monkeypatch.context() as guard:
        def forbidden(*args, **kwargs):
            raise AssertionError('owned stdarg build invoked an external process')
        guard.setattr(subprocess, 'Popen', forbidden)
        artifact = compile_host_cpp((('stdarg.cpp', preprocess_cpp(source)),))
        write_host_executable(artifact, tmp_path / 'owned')
    compiler = shutil.which(os.environ.get('PCC_CPP_ORACLE', 'g++'))
    assert compiler
    reference = compile_reference_cpp(units, tmp_path / 'reference', compiler=compiler)
    expected = Execution(42, struct.pack('<i', 42), b'')
    record_execution(tmp_path / 'owned', expected)
    record_execution(reference, expected)


@pytest.mark.parametrize('target', (
    'aarch64-unknown-linux-gnu',
    'x86_64-pc-windows-msvc',
    'aarch64-apple-darwin',
))
def test_stdarg_header_does_not_borrow_the_sysv_cursor_on_other_targets(target):
    with pytest.raises(RuntimeError, match=r'owned C\+\+ va_list currently requires the Linux x86_64 ABI'):
        preprocess_cpp('#include <stdarg.h>\n', target_triple=target)
