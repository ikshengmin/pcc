"""Compiled Python calls four source frontends in one owned static executable.

Signed i32/i64 is the first component contract. The separate unsigned tests
retain full-width obligations, currently missing in Python extern conversion.
Host compilation, native pcc1 prerequisites, and emitted execution are distinct;
none establishes shared heap/GC, exceptions, or Rust borrow checking.
"""
import hashlib
import json
import os
from pathlib import Path
import platform
import subprocess
import sys

import pytest

from tests.multilanguage.owned_execution import FIXTURES, TARGET
from tests.multilanguage.python_extern import CALLER, EXPECTED_STDOUT, compile_providers
from tests.multilanguage.test_scalar_c_abi import forbid_external_owners


# Installed inside the real `python -m pcc` child, not only in pytest's process.
# The caller and all provider originals remain unchanged by this ownership guard.
CLI_GUARD = '''import os, sys
from pathlib import Path

def owned_only(event, args):
    if event in ('subprocess.Popen', 'os.system', 'os.exec', 'os.posix_spawn'):
        raise AssertionError('owned Python CLI attempted an external process: ' + event)
    if event == 'import' and (args[0] == 'llvmlite' or args[0].startswith('llvmlite.')):
        raise AssertionError('owned Python CLI imported LLVM')

sys.addaudithook(owned_only)
Path(os.environ['PCC_TEST_CLI_GUARD_READY']).write_text('installed\\n')
'''


def _compile_owned_providers(monkeypatch):
    def forbidden_bytecode(*args, **kwargs):
        raise AssertionError('Java source provider entered historical bytecode compatibility')

    with monkeypatch.context() as owned:
        forbid_external_owners(owned)
        from pcc.frontends import java
        from pcc.frontends.java import compiler as bytecode

        owned.setattr(java, 'compile_class', forbidden_bytecode)
        owned.setattr(bytecode, 'compile_class', forbidden_bytecode)
        owned.setattr(bytecode, 'parse_class', forbidden_bytecode)
        return compile_providers()


def test_four_source_provider_objects_are_owned_and_byte_stable(monkeypatch):
    from pcc.backend.elf_x86_64 import SHN_UNDEF, parse_relocatable

    first = _compile_owned_providers(monkeypatch)
    assert first == _compile_owned_providers(monkeypatch)
    assert tuple(language for language, _ in first.objects) == ('cpp', 'java', 'go', 'rust')
    exports = dict(first.exports)
    for language, data in first.objects:
        parsed = parse_relocatable(data)
        defined = {symbol.name for symbol in parsed.symbols if symbol.section_index != SHN_UNDEF}
        assert set(exports[language]) <= defined
    assert all((FIXTURES / name).read_bytes() == data for name, data in first.inputs)


def _compile_and_run_python(tmp_path, request, entry, script_bytes, objects, runtime_archive, expected):
    from pcc.backend.elf_x86_64 import parse_static_executable

    script = tmp_path / 'python caller.py'
    script.write_bytes(script_bytes)
    output = tmp_path / 'python-with-source-providers'
    object_paths = []
    for language, data in objects:
        path = tmp_path / (language + ' provider.o')
        path.write_bytes(data)
        object_paths.append(path)
    env = dict(os.environ, PCC_RUNTIME_ARCHIVE=str(runtime_archive), PCC_SELF_LINK='pcc',
               CC='/nonexistent/cc', CXX='/nonexistent/c++', PCC_RUNTIME_CC='/nonexistent/cc',
               PCC_NO_AUTO_PCC1='1')
    guard_ready = tmp_path / 'cli-guard-ready.txt'
    if entry == 'pcc1':
        command = [str(request.getfixturevalue('native_pcc1_compiler'))]
        env.update(PATH='/nonexistent', PCC_HOST_PYTHON='/nonexistent/python',
                   PCC_HOST_PCC='/nonexistent/pcc')
    else:
        command = [sys.executable, '-m', 'pcc']
        guard = tmp_path / 'cli ownership guard'
        guard.mkdir()
        (guard / 'sitecustomize.py').write_text(CLI_GUARD, encoding='utf-8')
        root = Path(__file__).resolve().parents[2]
        env.update(PYTHONPATH=os.pathsep.join((str(guard), str(root))),
                   PCC_TEST_CLI_GUARD_READY=str(guard_ready))
    arguments = ['--backend', 'self']
    for path in object_paths:
        arguments += ['--link-arg', str(path)]
    command += arguments + [str(script), '-o', str(output)]
    # Same existing public Python CLI compile/run limits; no runtime rebuild
    # request, host linker, JVM, or per-language executable is substituted here.
    built = subprocess.run(command, capture_output=True, timeout=300, env=env)
    (tmp_path / 'compile.stdout.bin').write_bytes(built.stdout)
    (tmp_path / 'compile.stderr.bin').write_bytes(built.stderr)
    if entry == 'module':
        assert guard_ready.is_file(), ('CLI ownership guard did not load', built.stderr)
        assert guard_ready.read_bytes() == b'installed\n', 'CLI ownership guard did not load'
    assert built.returncode == 0, built.stderr
    executable = output.read_bytes()
    # Owned static ELF rejects PT_INTERP/PT_DYNAMIC, including libpython/JVM
    # loader dependencies. Host Python is only the explicitly labeled compiler.
    image = parse_static_executable(executable)
    assert image['entry'] != 0
    native_env = dict(env, PATH='/nonexistent', PCC_HOST_PYTHON='/nonexistent/python',
                      PCC_HOST_PCC='/nonexistent/pcc')
    ran = subprocess.run([str(output)], capture_output=True, timeout=30, env=native_env)
    (tmp_path / 'native.stdout.bin').write_bytes(ran.stdout)
    (tmp_path / 'native.stderr.bin').write_bytes(ran.stderr)
    receipt = {
        'compiler_entry': entry, 'target': TARGET,
        'scope': 'compiled Python extern scalar calls, one executable',
        'command': command, 'returncode': ran.returncode,
        'expected_stdout_hex': expected.hex(), 'stdout_hex': ran.stdout.hex(),
        'stderr_hex': ran.stderr.hex(),
        'caller_sha256': hashlib.sha256(script_bytes).hexdigest(),
        'object_sha256': {language: hashlib.sha256(data).hexdigest() for language, data in objects},
        'executable_sha256': hashlib.sha256(executable).hexdigest(),
        'rust_borrow_checked_by_pcc': False, 'rustc_reference_executed': False,
    }
    (tmp_path / 'observation.json').write_text(json.dumps(receipt, indent=2) + '\n')
    assert (ran.returncode, ran.stdout, ran.stderr) == (0, expected, b'')
    assert script.read_bytes() == script_bytes
    assert all(path.read_bytes() == data for path, (_language, data) in zip(object_paths, objects))


@pytest.mark.integration
@pytest.mark.pcc_gate(unavailable=None if sys.platform == 'linux' and platform.machine() == 'x86_64'
                      else 'requires Linux x86-64 owned static executable execution')
@pytest.mark.parametrize('entry', ('module', 'pcc1'))
def test_compiled_python_calls_four_source_frontends_in_one_executable(
    tmp_path, monkeypatch, request, pcc_runtime_archive, entry,
):
    providers = _compile_owned_providers(monkeypatch)
    for language, text in providers.ir:
        (tmp_path / (language + '.ir')).write_text(text, encoding='utf-8')
    for filename, data in providers.inputs:
        (tmp_path / filename).write_bytes(data)
    _compile_and_run_python(tmp_path, request, entry, CALLER.read_bytes(), providers.objects,
                            pcc_runtime_archive, EXPECTED_STDOUT)


UNSIGNED_OBLIGATIONS = (
    ('uint32', "from pcc.extern import extern, c_uint32\nf = extern('__pcc_rust_mix32', (c_uint32, c_uint32), c_uint32)\ndef main():\n    print(f(0xf1234500, 0x100))\nmain()\n", 0xF1234500 ^ 0x100),
    ('uint64', "from pcc.extern import extern, c_uint64\nf = extern('__pcc_rust_mix64', (c_uint64, c_uint64, c_uint64, c_uint64, c_uint64, c_uint64, c_uint64, c_uint64), c_uint64)\ndef main():\n    print(f(0xf123456789abc000, 1, 2, 4, 8, 16, 0x100000020, 0x200000040))\nmain()\n", 0xF123456789ABC000 + 1 + 2 + 4 + 8 + 16 + 0x100000020 + 0x200000040),
)


@pytest.mark.integration
@pytest.mark.pcc_gate(unavailable=None if sys.platform == 'linux' and platform.machine() == 'x86_64'
                      else 'requires Linux x86-64 owned static executable execution')
@pytest.mark.parametrize('name,caller,expected', UNSIGNED_OBLIGATIONS)
def test_full_width_unsigned_python_extern_obligation(
    tmp_path, monkeypatch, request, pcc_runtime_archive, name, caller, expected,
):
    """Known missing conversion, retained as an executable positive obligation.

    Signed boxing of high-bit results is not unsigned success. No skip/xfail,
    wrapping expected output, or low-bit replacement hides this boundary.
    """
    from pcc.frontends.rust import compile_rust

    source = (FIXTURES / 'scalars.rs').read_text(encoding='utf-8')
    with monkeypatch.context() as owned:
        forbid_external_owners(owned)
        rust = compile_rust(source, experimental_rustc_checked=True, overflow_checks=False,
                            module_name='python_unsigned_obligation', target_triple=TARGET)
        obj = rust.emit_object()
    assert not rust.externally_checked and not rust.borrow_checked_by_pcc
    (tmp_path / 'scalars.rs').write_text(source, encoding='utf-8')
    (tmp_path / 'rust.ir').write_text(rust.ir_text, encoding='utf-8')
    _compile_and_run_python(tmp_path, request, 'module', caller.encode('utf-8'), (('rust', obj),),
                            pcc_runtime_archive, (str(expected) + '\n').encode('ascii'))
