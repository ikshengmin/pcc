"""Actual development-only Rust runtime differential, not metadata acceptance.

Rust Reference (retrieved 2026-10-31); oracle rustc 1.91.1 / edition 2021:
https://doc.rust-lang.org/reference/items/implementations.html#inherent-implementations
https://doc.rust-lang.org/reference/items/associated-items.html#methods
https://doc.rust-lang.org/reference/expressions/method-call-expr.html

Each unchanged source is a separate rlib crate. A separate std-based observer
uses its public answer; no source inclusion, token editing, extracted function,
fake panic handler, or PCC replacement of the reference compiler/linker occurs.
The external Rust std/system-linker dependency belongs ONLY to this oracle.
"""
import hashlib
import json
from pathlib import Path
import platform
import shutil
import subprocess
import sys

import pytest

from tests.rust.test_rust_methods import PROGRAMS
from tests.rust.test_rust_patterns import test_original_match_fixture_full_width_owned_execution as _native


def digest(data):
    return hashlib.sha256(data).hexdigest()


@pytest.mark.integration
@pytest.mark.pcc_gate(unavailable=None if (
    sys.platform.startswith('linux') and platform.machine() in {'x86_64', 'AMD64'}
    and shutil.which('rustc') and shutil.which('cc')
) else 'requires Linux x86-64, development rustc and system C linker')
def test_unchanged_inherent_programs_match_actual_rustc_native_execution(tmp_path, monkeypatch):
    # One node keeps this six-program reference lane sequential without changing
    # the repository's default six workers or any process-tree resource limit.
    rustc = shutil.which('rustc')
    linker = shutil.which('cc')
    version = subprocess.run([rustc, '-vV'], capture_output=True, timeout=15, check=True)
    (tmp_path / 'rustc-version.stdout').write_bytes(version.stdout)
    (tmp_path / 'rustc-version.stderr').write_bytes(version.stderr)
    observer = ('extern crate rust_reference;\n'
                'use std::io::{self, Write};\n'
                'fn main() { io::stdout().write_all(&rust_reference::answer().to_le_bytes()).unwrap(); }\n')
    for name in PROGRAMS:
        directory = tmp_path / name
        directory.mkdir()
        source = Path(__file__).parent / 'programs' / (name + '.rs')
        original = source.read_bytes()
        wrapper = directory / 'observer.rs'
        wrapper.write_text(observer)
        library = directory / 'librust_reference.rlib'
        binary = directory / 'rustc-reference'
        common = ['--edition=2021', '-C', 'overflow-checks=off', '-C', 'panic=abort']
        commands = [
            [rustc, '--crate-name', 'rust_reference', '--crate-type=rlib', *common,
             str(source), '-o', str(library)],
            [rustc, '--crate-name', 'rust_reference_observer', *common,
             '-C', 'linker=' + linker, '--extern', 'rust_reference=' + str(library),
             str(wrapper), '-o', str(binary)],
        ]
        compilation = []
        for index, command in enumerate(commands):
            result = subprocess.run(command, capture_output=True, timeout=15)
            (directory / f'compile-{index}.stdout').write_bytes(result.stdout)
            (directory / f'compile-{index}.stderr').write_bytes(result.stderr)
            compilation.append({'command': command, 'exit_code': result.returncode,
                                'stdout_sha256': digest(result.stdout), 'stderr_sha256': digest(result.stderr)})
            (directory / 'compilation.json').write_text(json.dumps(compilation, indent=2) + '\n')
            assert result.returncode == 0, result.stderr.decode(errors='replace')
        actual = subprocess.run([str(binary)], capture_output=True, timeout=5)
        (directory / 'reference.stdout').write_bytes(actual.stdout)
        (directory / 'reference.stderr').write_bytes(actual.stderr)
        # PCC's path still forbids ALL external tool invocation during its
        # compilation/assembly/linking; only executing the owned ELF is allowed.
        owned = directory / 'pcc-owned'
        owned.mkdir()
        _native(name, owned, monkeypatch)
        expected = bytes.fromhex('1032547698badcfe')
        record = {
            'purpose': 'runtime', 'source_path': 'tests/rust/programs/' + name + '.rs',
            'source_sha256': digest(original), 'source_unchanged': source.read_bytes() == original,
            'reference_kind': 'native_execution', 'reference_owner': 'external rustc rlib + std observer + system C linker',
            'version': version.stdout.decode(), 'rustc': rustc, 'linker': linker,
            'observer_sha256': digest(wrapper.read_bytes()), 'compile': compilation,
            'reference_command': [str(binary)], 'binary_sha256': digest(binary.read_bytes()),
            'actual': {'stdout_sha256': digest(actual.stdout), 'stderr_sha256': digest(actual.stderr), 'exit_code': actual.returncode},
            'expected': {'stdout_sha256': digest(expected), 'stderr_sha256': digest(b''), 'exit_code': 0},
            'pcc_stdout_sha256': digest((owned / 'stdout.bin').read_bytes()),
        }
        (directory / 'runtime-differential.json').write_text(json.dumps(record, indent=2) + '\n')
        assert record['source_unchanged']
        assert actual.returncode == 0 and actual.stderr == b''
        assert actual.stdout == expected == (owned / 'stdout.bin').read_bytes()
