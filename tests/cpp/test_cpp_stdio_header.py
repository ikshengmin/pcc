"""N4659 [depr.c.headers], [cstdio.syn], [dcl.link]: owned <stdio.h>.

No-call programs qualify header acquisition and cross-object type identity,
not stdio runtime semantics. The external reference gets the original source
with its real system headers. Host pcc0/self Linux x86_64 only, not native pcc1,
NumPy acceptance or a new FILE/va_list ABI on other targets.
"""
import os
import re
import shutil
import struct
import subprocess

import pytest

from pcc.backend.elf_x86_64 import ElfError, _object_symbol_sets, parse_relocatable
from pcc.backend.owned_object_emit import emit_owned_object
from pcc.frontends.cpp import CppDiagnostic, CppParser
from pcc.frontends.cpp.driver import preprocess_cpp
from pcc.frontends.cpp.types import unqualified
from tests.cpp.owned_execution import (
    Execution,
    TARGET,
    compile_host_cpp,
    compile_reference_cpp,
    write_host_executable,
)
from tests.cpp.test_cpp_cctype_header import compile_public
from tests.cpp.test_cpp_class_execution import record_execution
from tests.cpp.test_cpp_cstdio_header import FUNCTIONS
from tests.cpp.test_cpp_owned_execution import owned_only
from tests.cpp.test_cpp_public_driver_execution import link_object


HEADERS = (
    '#include <stdio.h>\n#include <stdio.h>\n',
    '#include <stdio.h>\n#include <cstdio>\n',
    '#include <cstdio>\n#include <stdio.h>\n',
)
BODY = (
    'int cpp_test_entry(){FILE* stream=NULL;size_t value=42;'
    'if(stream!=NULL)return 1;if(EOF>=0)return 2;return int(value);}'
)
CROSS_UNITS = (
    ('provider.cpp', '#include <stdio.h>\n'
     'size_t add_one(size_t n,FILE* stream){if(stream!=NULL)return 1;return n+1;}'),
    ('caller.cpp', '#include <cstdio>\n#include <stdio.h>\n'
     'std::size_t add_one(std::size_t,std::FILE*);'
     'int cpp_test_entry(){return int(add_one(41,NULL));}'),
)


@pytest.mark.parametrize('header', HEADERS)
def test_stdio_inclusion_retains_global_types_and_one_c_linkage_entity(header):
    parser = CppParser(target_triple=TARGET)
    parser.parse(preprocess_cpp(header + BODY, target_triple=TARGET))
    names = parser.scope.names
    standard = names['std'].namespace_scope.names
    for name in ('size_t', 'FILE', 'fpos_t'):
        assert names[name].dtype == standard[name].dtype
    assert unqualified(names['FILE'].dtype).complete
    assert not unqualified(names['fpos_t'].dtype).complete
    for name in FUNCTIONS:
        direct = names[name].overloads[0]
        assert standard[name].overloads[0] is direct
        assert len(names[name].overloads) == 1
        assert direct.language_linkage == 'C' and direct.c_name == name
        assert not direct.defined


def test_stdio_nested_acquisition_uses_owned_declarations(tmp_path, owned_only):
    (tmp_path / 'api.h').write_text('#include <stdio.h>\nusing Stream=FILE;\n')
    source = '#include "api.h"\n#include <cstdio>\n' + BODY
    lowered = preprocess_cpp(source, base_dir=str(tmp_path))
    parser = CppParser()
    parser.parse(lowered)
    assert parser.scope.names['Stream'].dtype == parser.scope.names['FILE'].dtype
    units = (('nested.cpp', lowered),)
    assert compile_host_cpp(units) == compile_host_cpp(units)


def test_stdio_call_keeps_an_unresolved_owned_runtime_dependency(owned_only):
    source = '#include <stdio.h>\nint main(){return putchar(42);}'
    first = compile_public(source, use_compile_cache=False)
    second = compile_public(source, use_compile_cache=False)
    assert first == second
    ir = first[0][1]
    assert re.search(r'declare i32 @"?putchar"?\(i32', ir)
    assert not re.search(r'define .*@"?putchar"?\(', ir)
    obj = emit_owned_object(ir, TARGET)
    assert obj == emit_owned_object(second[0][1], TARGET)
    defined, undefined = _object_symbol_sets(parse_relocatable(obj))
    assert 'putchar' not in defined and undefined == frozenset(('putchar',))
    with pytest.raises(ElfError, match=r'^undefined static ELF symbols: putchar$'):
        link_object(obj)


@pytest.mark.parametrize('tail', (
    'fpos_t position;',
    'int f(){return sizeof(fpos_t);}',
    'int f(){return BUFSIZ;}',
    'int f(){return FOPEN_MAX;}',
    'int f(){return FILENAME_MAX;}',
    'int f(){return L_tmpnam;}',
    'int f(){return TMP_MAX;}',
))
def test_stdio_does_not_invent_position_storage_or_capacity(tail):
    with pytest.raises(CppDiagnostic):
        CppParser().parse(preprocess_cpp('#include <stdio.h>\n' + tail))


def test_stdio_unimplemented_capacity_is_not_preprocessor_zero():
    with pytest.raises(RuntimeError, match='failed to evaluate'):
        preprocess_cpp('#include <stdio.h>\n#if BUFSIZ\nint x;\n#endif\n')


@pytest.mark.parametrize('target', (
    'aarch64-unknown-linux-gnu',
    'x86_64-pc-windows-msvc',
    'aarch64-apple-darwin',
))
def test_stdio_preserves_explicit_unsupported_varargs_targets(target):
    with pytest.raises(RuntimeError, match=r'owned C\+\+ va_list currently requires the Linux x86_64 ABI'):
        preprocess_cpp('#include <stdio.h>\n', target_triple=target)


@pytest.mark.parametrize('units', (
    *((('stdio.cpp', header + BODY),) for header in HEADERS),
    CROSS_UNITS,
), ids=('repeated', 'stdio-first', 'cstdio-first', 'cross-object'))
def test_stdio_originals_have_stable_owned_artifacts(units, owned_only):
    lowered = tuple((name, preprocess_cpp(source)) for name, source in units)
    artifact = compile_host_cpp(lowered)
    assert artifact == compile_host_cpp(lowered)
    assert len(artifact.objects) == len(units)
    assert artifact.owner == 'host-python/pcc-cpp/pcc-self/pcc-elf'


@pytest.mark.integration
@pytest.mark.parametrize('units', (
    *((('stdio.cpp', header + BODY),) for header in HEADERS),
    CROSS_UNITS,
), ids=('repeated', 'stdio-first', 'cstdio-first', 'cross-object'))
def test_stdio_originals_execute_against_system_header_reference(units, tmp_path, monkeypatch):
    with monkeypatch.context() as guard:
        def forbidden(*args, **kwargs):
            raise AssertionError('owned stdio header build invoked an external process')
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
