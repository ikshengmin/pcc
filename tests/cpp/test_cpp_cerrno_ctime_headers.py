"""Original tests of N4659 [cerrno.syn] and [ctime.syn] (C11 7.5 and 7.27).

The owned headers are declaration/macro surfaces over the target C library ABI:
errno designates that library's location function, the error numbers are its
constants, and every <ctime> function is an unresolved C-linkage declaration.
Native/reference cases deliberately call no unresolved routine; the complete
error-number tables are compared with the actual target C library headers.
"""
from pathlib import Path
import os
import platform
import re
import shutil
import subprocess
import sys

import pytest

from pcc.frontends.cpp import CppParser
from pcc.frontends.cpp.driver import preprocess_cpp

ROOT = Path(__file__).resolve().parents[2]
LINUX = 'x86_64-unknown-linux-gnu'
LINUX_ARM = 'aarch64-unknown-linux-gnu'
DARWIN = 'aarch64-apple-darwin'
# Every macro of N4659 [cerrno.syn], in the synopsis order.
ERROR_NUMBERS = (
    'E2BIG', 'EACCES', 'EADDRINUSE', 'EADDRNOTAVAIL', 'EAFNOSUPPORT', 'EAGAIN', 'EALREADY',
    'EBADF', 'EBADMSG', 'EBUSY', 'ECANCELED', 'ECHILD', 'ECONNABORTED', 'ECONNREFUSED',
    'ECONNRESET', 'EDEADLK', 'EDESTADDRREQ', 'EDOM', 'EEXIST', 'EFAULT', 'EFBIG',
    'EHOSTUNREACH', 'EIDRM', 'EILSEQ', 'EINPROGRESS', 'EINTR', 'EINVAL', 'EIO', 'EISCONN',
    'EISDIR', 'ELOOP', 'EMFILE', 'EMLINK', 'EMSGSIZE', 'ENAMETOOLONG', 'ENETDOWN',
    'ENETRESET', 'ENETUNREACH', 'ENFILE', 'ENOBUFS', 'ENODATA', 'ENODEV', 'ENOENT',
    'ENOEXEC', 'ENOLCK', 'ENOLINK', 'ENOMEM', 'ENOMSG', 'ENOPROTOOPT', 'ENOSPC', 'ENOSR',
    'ENOSTR', 'ENOSYS', 'ENOTCONN', 'ENOTDIR', 'ENOTEMPTY', 'ENOTRECOVERABLE', 'ENOTSOCK',
    'ENOTSUP', 'ENOTTY', 'ENXIO', 'EOPNOTSUPP', 'EOVERFLOW', 'EOWNERDEAD', 'EPERM', 'EPIPE',
    'EPROTO', 'EPROTONOSUPPORT', 'EPROTOTYPE', 'ERANGE', 'EROFS', 'ESPIPE', 'ESRCH', 'ETIME',
    'ETIMEDOUT', 'ETXTBSY', 'EWOULDBLOCK', 'EXDEV',
)
TIME_FUNCTIONS = ('clock', 'difftime', 'mktime', 'time', 'timespec_get', 'asctime', 'ctime',
                  'gmtime', 'localtime', 'strftime')
TM_MEMBERS = ('tm_sec', 'tm_min', 'tm_hour', 'tm_mday', 'tm_mon', 'tm_year', 'tm_wday',
              'tm_yday', 'tm_isdst', 'tm_gmtoff', 'tm_zone')


def macro_values_hold(header, values, target):
    """Evaluate each expected value with the owned preprocessor, not by text."""
    checks = ''.join(f'#if !defined({name}) || ({name}) != {value}\n#error {name}\n#endif\n'
                     for name, value in values.items())
    preprocess_cpp(f'#include <{header}>\n' + checks, target_triple=target)


def parse(source, target):
    parser = CppParser(target_triple=target)
    parser.parse(preprocess_cpp(source, target_triple=target), 'original.cpp')
    return parser


def lower(source, target):
    from pcc.frontends.c.evaluator.c_evaluator import _compile_preprocessed_translation_unit_artifact
    text = preprocess_cpp(source, target_triple=target, source_name='original.cpp')
    return _compile_preprocessed_translation_unit_artifact('original.cpp', text, target_triple=target)['ir_text']


@pytest.mark.parametrize('header', ['cerrno', 'ctime'])
def test_header_preprocessing_is_owned_and_byte_stable(header, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('owned header preprocessing launched an external process')
    monkeypatch.setattr(subprocess, 'Popen', forbidden)
    source = f'#include <{header}>\n#include <{header}>\n'
    first = preprocess_cpp(source, target_triple=LINUX)
    assert first == preprocess_cpp(source, target_triple=LINUX)
    assert first.count('extern "C"') == 1


@pytest.mark.parametrize('target', [LINUX, LINUX_ARM, DARWIN])
def test_every_cerrno_synopsis_macro_is_defined(target):
    macro_values_hold('cerrno', {}, target)
    checks = ''.join(f'#ifndef {name}\n#error {name}\n#endif\n' for name in ERROR_NUMBERS)
    preprocess_cpp('#include <cerrno>\n' + checks, target_triple=target)


@pytest.mark.parametrize('target,values', [
    # Linux generic numbering: EWOULDBLOCK is EAGAIN and ENOTSUP is EOPNOTSUPP.
    (LINUX, {'EAGAIN': 11, 'EWOULDBLOCK': 11, 'EDEADLK': 35, 'EILSEQ': 84,
             'ENOTSUP': 95, 'EOPNOTSUPP': 95, 'ECANCELED': 125, 'ENOTRECOVERABLE': 131}),
    (LINUX_ARM, {'EAGAIN': 11, 'EILSEQ': 84, 'ENOTSUP': 95}),
    # Darwin numbering keeps ENOTSUP and EOPNOTSUPP distinct.
    (DARWIN, {'EAGAIN': 35, 'EWOULDBLOCK': 35, 'EDEADLK': 11, 'EILSEQ': 92,
              'ENOTSUP': 45, 'EOPNOTSUPP': 102, 'ECANCELED': 89, 'ENOTRECOVERABLE': 104}),
])
def test_error_numbers_are_the_target_library_constants(target, values):
    macro_values_hold('cerrno', {'EDOM': 33, 'ERANGE': 34, **values}, target)


@pytest.mark.parametrize('target,location', [
    (LINUX, '__errno_location'), (LINUX_ARM, '__errno_location'), (DARWIN, '__error')])
def test_errno_h_and_cerrno_share_the_exact_target_macro_and_accessor(target, location, monkeypatch):
    monkeypatch.setattr(subprocess, 'Popen', lambda *a, **k: pytest.fail('external preprocessor invoked'))
    # Compare the full standard macro inventory, not only a selected constant,
    # through both inclusion orders and repeated inclusion. Strip line markers
    # only; every emitted token and the real accessor declaration must match.
    names = '+'.join(ERROR_NUMBERS)
    tail = f'int f(){{errno=EDOM;return errno+{names};}}\n'
    def preprocess(headers):
        return preprocess_cpp(headers + tail, target_triple=target, line_markers=False)
    baseline = preprocess('#include <cerrno>\n')
    for headers in ('#include <errno.h>\n#include <cerrno>\n',
                    '#include <cerrno>\n#include <errno.h>\n',
                    '#include <errno.h>\n#include <errno.h>\n'):
        text = preprocess(headers)
        assert text.split() == baseline.split()
        assert text.count('extern "C"') == 1
        assert '_fake_typedefs.h' not in text
        assert location + '()' in text
        parser = CppParser(target_triple=target)
        parser.parse(text, 'errno.h-original.cpp')
        accessor, = parser.scope.names[location].overloads
        assert accessor.language_linkage == 'C' and not accessor.defined


@pytest.mark.parametrize('target', ['x86_64-pc-windows-msvc', 'x86_64-w64-windows-gnu'])
def test_errno_h_retains_explicit_unsupported_target_abi(target):
    with pytest.raises(RuntimeError, match=r'#error owned C\+\+ <cerrno> has no error-number ABI'):
        preprocess_cpp('#include <errno.h>\n', target_triple=target)


@pytest.mark.parametrize('target,location', [
    (LINUX, '__errno_location'), (LINUX_ARM, '__errno_location'), (DARWIN, '__error')])
def test_errno_designates_the_target_library_location_lvalue(target, location):
    ir = lower('#include <cerrno>\nint f(){errno=0; int* p=&errno; *p=EDOM; return errno;}\n', target)
    assert re.search(rf'declare ptr @{location}\(\)', ir)
    assert len(re.findall(rf'call ptr \(\) @{location}\(\)', ir)) == 3
    other = '__error' if location == '__errno_location' else '__errno_location'
    assert other not in ir


ERRNO_H_PROGRAM = (
    '#include <errno.h>\n#include <cerrno>\n#include <stdlib.h>\n'
    'int main(){errno=0;int* slot=&errno;*slot=EDOM;if(errno!=EDOM)return 1;'
    'errno=0;char* end;long value=::strtol("9999999999999999999999999999",&end,10);'
    'if(value!=9223372036854775807L||*end!=0||errno!=ERANGE||*slot!=ERANGE)return 2;'
    'return errno+8;}'
)


@pytest.mark.parametrize('target,location', [
    (LINUX, '__errno_location'), (LINUX_ARM, '__errno_location'), (DARWIN, '__error')])
def test_errno_h_lowering_preserves_modifiable_location_lvalue(target, location):
    ir = lower('#include <errno.h>\n#include <cerrno>\n'
               'int f(){errno=0;int* p=&errno;*p=EDOM;return errno;}', target)
    assert re.search(rf'declare ptr @{location}\(\)', ir)
    assert len(re.findall(rf'call ptr \(\) @{location}\(\)', ir)) == 3
    other = '__error' if location == '__errno_location' else '__errno_location'
    assert other not in ir


def run_errno_h_native(directory, archive, monkeypatch, target):
    from pcc.backend.owned_elf_link import link_inputs
    from pcc.backend.owned_object_emit import emit_owned_object
    from pcc.driver.project import TranslationUnit
    from pcc.frontends.c.evaluator.c_evaluator import CEvaluator
    from tests.cpp.owned_execution import Execution
    from tests.cpp.test_cpp_class_execution import record_execution
    obj, exe = directory / 'errno-header.o', directory / 'errno-header'
    with monkeypatch.context() as guard:
        guard.setattr(subprocess, 'Popen', lambda *a, **k: pytest.fail('external compiler/linker invoked'))
        units = CEvaluator(backend='self', target_triple=target).compile_translation_units(
            [TranslationUnit('errno-header.cpp', None, ERRNO_H_PROGRAM)], use_system_cpp=False,
            frontend_opt_level=0, use_compile_cache=False)
        obj.write_bytes(emit_owned_object(units[0][1], target))
        link_inputs(target=target, output=str(exe), objects=[str(obj)], archives=[str(archive)])
    record_execution(exe, Execution(42, b'', b''))


@pytest.mark.integration
@pytest.mark.pcc_gate(unavailable=None if sys.platform == 'linux' and platform.machine().lower() in (
    'amd64', 'x86_64', 'aarch64', 'arm64') else 'requires owned Linux errno runtime execution')
def test_errno_h_lvalue_and_strtol_failure_share_runtime_storage(tmp_path, pcc_runtime_archive, monkeypatch):
    from pcc.frontends.python.pipeline_targets import host_target_triple
    run_errno_h_native(tmp_path, pcc_runtime_archive, monkeypatch, host_target_triple())


@pytest.mark.parametrize('header', ['cerrno', 'ctime'])
@pytest.mark.parametrize('target', ['x86_64-pc-windows-msvc', 'x86_64-w64-windows-gnu'])
def test_targets_without_a_modeled_c_library_abi_fail_explicitly(header, target):
    with pytest.raises(RuntimeError, match=r'#error owned C\+\+ <' + header + '> has no'):
        preprocess_cpp(f'#include <{header}>\n', target_triple=target)


@pytest.mark.parametrize('target,clock_type,zone_type', [
    (LINUX, 'long', 'const char*'), (DARWIN, 'unsigned long', 'char*')])
def test_ctime_declarations_follow_the_target_library_abi(target, clock_type, zone_type):
    parser = parse('#include <ctime>\n', target)
    names, std = parser.scope.names, parser.scope.names['std'].namespace_scope.names
    assert names['clock_t'].dtype == clock_type and names['time_t'].dtype == 'long'
    for name in TIME_FUNCTIONS:
        declarations = names[name].overloads
        assert len(declarations) == 1
        assert declarations[0].language_linkage == 'C' and declarations[0].c_name == name
        assert not declarations[0].defined
        assert std[name].overloads == declarations
    for name in ('clock_t', 'size_t', 'time_t', 'timespec', 'tm'):
        assert std[name].dtype is names[name].dtype
    members = names['tm'].dtype.definition.members
    assert tuple(member.name for member in members) == TM_MEMBERS
    assert all(member.dtype == 'int' for member in members[:9])
    assert members[9].dtype == 'long'
    zone = parse(f'#include <ctime>\n{zone_type} z(std::tm* t){{return t->tm_zone;}}\n', target)
    assert zone.scope.names['z'].overloads[0].defined
    timespec = names['timespec'].dtype.definition.members
    assert [(member.name, member.dtype) for member in timespec] == [('tv_sec', 'long'), ('tv_nsec', 'long')]


@pytest.mark.parametrize('name,source', [
    # Calls stay real unresolved C-library routines; no definitions are invented.
    ('clock', 'int main(){return int(std::clock());}'),
    ('gmtime', 'int main(){std::time_t t=0; return std::gmtime(&t)!=nullptr;}'),
])
def test_time_calls_reference_unresolved_c_library_symbols(name, source):
    ir = lower('#include <ctime>\n' + source + '\n', LINUX)
    assert re.search(rf'call \S+ \([^)]*\) @{name}\(', ir)
    assert not re.search(rf'define \S+ @{name}\(', ir)


ORIGINALS = {
    'error-numbers': ('#include <cerrno>\nint main(){return EDOM + ERANGE + EILSEQ;}\n', 151),
    'time-layout': ('#include <ctime>\nint main(){std::tm t{}; std::timespec s{}; '
                    'return int(sizeof t + sizeof s + sizeof(std::clock_t)) + TIME_UTC + '
                    '(std::clock_t(-1) < 0) + int(CLOCKS_PER_SEC / 100000) + t.tm_isdst + int(s.tv_nsec);}\n', 92),
}


def owned_executable(source, monkeypatch):
    from pcc.backend.owned_object_emit import emit_owned_object
    from pcc.driver.project import TranslationUnit
    from pcc.frontends.c.evaluator.c_evaluator import CEvaluator
    from tests.cpp.owned_execution import TARGET
    from tests.cpp.test_cpp_public_driver_execution import link_object
    with monkeypatch.context() as guard:
        def forbidden(*args, **kwargs):
            raise AssertionError('owned header program invoked external tools')
        guard.setattr(subprocess, 'Popen', forbidden)

        def compile_original():
            return CEvaluator(backend='self', target_triple=TARGET).compile_translation_units(
                [TranslationUnit('original.cpp', None, source)], use_system_cpp=False,
                frontend_opt_level=0, use_compile_cache=False)
        first = compile_original()
        assert first == compile_original()
        obj = emit_owned_object(first[0][1], TARGET)
        assert obj == emit_owned_object(first[0][1], TARGET)
        return link_object(obj)


@pytest.mark.integration
@pytest.mark.parametrize('name', ORIGINALS)
def test_header_originals_execute_against_explicit_cpp17_reference(name, tmp_path, monkeypatch):
    from tests.cpp.owned_execution import Execution
    from tests.cpp.test_cpp_class_execution import record_execution
    source, status = ORIGINALS[name]
    owned = tmp_path / 'owned'
    owned.write_bytes(owned_executable(source, monkeypatch))
    owned.chmod(0o700)
    expected = Execution(status, b'', b'')
    record_execution(owned, expected)
    unit = tmp_path / 'original.cpp'
    unit.write_text(source)
    oracle = tmp_path / 'oracle'
    compiler = shutil.which(os.environ.get('PCC_CPP_ORACLE', 'g++'))
    assert compiler, 'explicit C++17 oracle unavailable; set PCC_CPP_ORACLE'
    result = subprocess.run([compiler, '-std=c++17', '-pedantic-errors', '-O0', str(unit), '-o', str(oracle)],
                            capture_output=True, timeout=20)
    assert result.returncode == 0, result.stderr
    record_execution(oracle, expected)


def reference_error_numbers(command, source, tmp_path):
    unit = tmp_path / source[0]
    unit.write_text(source[1] + 'int main(void){\n' + ''.join(
        f'printf("{name} %d\\n", (int){name});\n' for name in ERROR_NUMBERS) + 'return 0;}\n')
    binary = tmp_path / 'reference-errno'
    result = subprocess.run([*command, str(unit), '-o', str(binary)], capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr
    run = subprocess.run([str(binary)], capture_output=True, timeout=10)
    assert run.returncode == 0 and run.stderr == b''
    values = dict(line.split() for line in run.stdout.decode().splitlines())
    assert tuple(values) == ERROR_NUMBERS
    return {name: int(value) for name, value in values.items()}


@pytest.mark.integration
def test_linux_error_numbers_equal_the_actual_cpp17_library_header(tmp_path):
    assert sys.platform.startswith('linux'), 'the Linux C library reference requires a Linux host'
    compiler = shutil.which(os.environ.get('PCC_CPP_ORACLE', 'g++'))
    assert compiler, 'explicit C++17 oracle unavailable; set PCC_CPP_ORACLE'
    values = reference_error_numbers(
        (compiler, '-std=c++17', '-pedantic-errors'),
        ('errno.cpp', '#include <cerrno>\n#include <cstdio>\nusing std::printf;\n'), tmp_path)
    macro_values_hold('cerrno', values, LINUX)


@pytest.mark.integration
@pytest.mark.pcc_gate(unavailable=None if sys.platform == 'darwin' and shutil.which('clang')
                      else 'the Darwin SDK reference requires a macOS host with clang')
def test_darwin_error_numbers_equal_the_actual_sdk_header(tmp_path):
    values = reference_error_numbers(
        (shutil.which('clang'), '-std=c11', '-x', 'c'),
        ('errno.c', '#include <errno.h>\n#include <stdio.h>\n'), tmp_path)
    macro_values_hold('cerrno', values, DARWIN)
