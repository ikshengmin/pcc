"""Separate executable primitive/module fixture; CI-only execution is unrun.

The original private-answer source remains unchanged in test_rust_use_resolution.
This fixture has a public answer for a separate rustc observer. Both compilers
consume the same fixture bytes, not a visibility rewrite of the private input.
"""
import hashlib
import json
from pathlib import Path
import platform
import shutil
import subprocess
import sys

import pytest

from tests.rust.certification_support import actual_check_prerequisite, certify_source
from pcc.frontends.rust import compile_rust


@pytest.mark.integration
@pytest.mark.xdist_group(name='rust-native-reference')
@pytest.mark.pcc_gate(unavailable=actual_check_prerequisite() or (
    None if sys.platform.startswith('linux') and platform.machine() in {'x86_64', 'AMD64'}
    and shutil.which('cc') else 'requires Linux rustc/system-linker reference'))
def test_public_primitive_module_fixture_matches_actual_native_rustc(tmp_path, monkeypatch):
    from pcc.backend.elf_x86_64 import link_static_executable, parse_relocatable
    from pcc.backend.x86_64_asm_driver import assemble_file
    from tests.rust.test_rust_ffi import record_run
    source = Path(__file__).with_name('programs') / 'use_primitive_module_runtime.rs'
    original = source.read_bytes()
    checked = certify_source(original.decode(), tmp_path / 'actual-check')
    # Use the direct executable that actually certified this source, not a
    # similarly named compiler found later on PATH.
    rustc, linker = checked.provenance.command[0], shutil.which('cc')

    def compiler_digest():
        with Path(rustc).open('rb') as stream:
            return hashlib.file_digest(stream, 'sha256').hexdigest()

    assert compiler_digest() == checked.provenance.compiler_sha256
    observer = tmp_path / 'observer.rs'
    observer.write_text('extern crate rust_reference; use std::io::{self, Write}; '
                        'fn main(){io::stdout().write_all(&rust_reference::answer().to_le_bytes()).unwrap();}')
    library, binary = tmp_path / 'librust_reference.rlib', tmp_path / 'reference'
    common = ['--edition=2021', '-C', 'overflow-checks=off', '-C', 'panic=abort']
    commands = [
        [rustc, '--crate-name', 'rust_reference', '--crate-type=rlib', *common, str(source), '-o', str(library)],
        [rustc, *common, '-C', 'linker=' + linker, '--extern', 'rust_reference=' + str(library),
         str(observer), '-o', str(binary)],
    ]
    observations = []
    for index, command in enumerate(commands):
        assert compiler_digest() == checked.provenance.compiler_sha256
        built = subprocess.run(command, capture_output=True, timeout=15)
        (tmp_path / f'compile-{index}.stdout').write_bytes(built.stdout)
        (tmp_path / f'compile-{index}.stderr').write_bytes(built.stderr)
        observations.append({'command': command, 'exit_code': built.returncode})
        (tmp_path / 'compilation.json').write_text(json.dumps(observations, indent=2))
        assert built.returncode == 0, built.stderr
    assert compiler_digest() == checked.provenance.compiler_sha256
    actual = subprocess.run([str(binary)], capture_output=True, timeout=5)
    (tmp_path / 'reference.stdout').write_bytes(actual.stdout)
    (tmp_path / 'reference.stderr').write_bytes(actual.stderr)
    with monkeypatch.context() as denied:
        denied.setattr(subprocess, 'Popen', lambda *args, **kwargs: pytest.fail('owned compile invoked tool'))
        result = compile_rust(original.decode(), rustc_check=checked)
        obj = result.emit_object()
        assembly = ('.intel_syntax noprefix\n.text\n.globl _start\n_start:\n call ' + result.symbols['answer'] +
                    '\n sub rsp,16\n mov QWORD PTR [rsp],rax\n mov eax,1\n mov edi,1\n mov rsi,rsp\n'
                    ' mov edx,8\n syscall\n mov eax,60\n xor edi,edi\n syscall\n')
        image = link_static_executable([parse_relocatable(obj), assemble_file(assembly)])
    (tmp_path / 'program.o').write_bytes(obj)
    expected = (42).to_bytes(8, 'little')
    record_run(image, expected, tmp_path)
    assert (actual.returncode, actual.stdout, actual.stderr) == (0, expected, b'')
    assert actual.stdout == (tmp_path / 'stdout.bin').read_bytes()
    assert source.read_bytes() == original
    (tmp_path / 'comparison.json').write_text(json.dumps({
        'source_sha256': hashlib.sha256(original).hexdigest(),
        'source_kind': 'separate public-answer runtime fixture',
        'rustc': rustc, 'compiler_sha256': checked.provenance.compiler_sha256,
        'compiler_version': checked.provenance.compiler_version,
        'private_original_runtime_claim': False,
        'reference_exit': actual.returncode, 'expected_stdout_hex': expected.hex(),
        'reference_stdout_hex': actual.stdout.hex(),
    }, indent=2))
