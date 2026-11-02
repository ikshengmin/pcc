"""Explicit external-check/reference lane; never an owned compiler fallback.

Spec: https://doc.rust-lang.org/edition-guide/rust-2024/gen-keyword.html
https://doc.rust-lang.org/rustc/command-line-arguments.html#--edition-specify-the-edition-to-use
The original sources are checked with the actual audited installation. No fake
subprocess, fabricated certificate, or private seal is used. All UNRUN until CI.
"""
from hashlib import file_digest, sha256
import json
from pathlib import Path
import platform
import shutil
import subprocess
import sys

import pytest

from pcc.frontends.rust import (
    RustError,
    RustExternalCheckError,
    check_rust_source,
    compile_rust,
    compile_rust_checked,
)
from tests.rust.certification_support import actual_check_prerequisite
from tests.rust.test_rust_certified_references import toolchain


EDITIONS = (2015, 2018, 2021, 2024)
PROGRAMS = Path(__file__).with_name('programs')
TARGET = 'x86_64-unknown-linux-gnu'
EXPECTED = bytes.fromhex('1032547698badcfe')


def forbidden(*args, **kwargs):
    pytest.fail('external compiler or partial borrow admission reached the owned phase')


@pytest.mark.xdist_group(name='rust-native-reference')
@pytest.mark.pcc_gate(unavailable=actual_check_prerequisite())
@pytest.mark.parametrize('edition', EDITIONS)
def test_actual_token_binds_selected_edition_and_compile_owned_retains_it(edition, tmp_path, monkeypatch):
    import pcc.frontends.rust as frontend

    path = PROGRAMS / 'edition_context_values.rs'
    original = path.read_bytes()
    checked = check_rust_source(original.decode(), edition=edition, toolchain=toolchain(),
                                evidence_directory=tmp_path / 'check')
    assert checked.provenance.edition == str(edition)
    assert checked.provenance.command[1] == '--edition=' + str(edition)
    assert checked.provenance.source_sha256 == sha256(original).hexdigest()
    assert (tmp_path / 'check' / 'original.rs').read_bytes() == original
    observations = json.loads((tmp_path / 'check' / 'processes.json').read_text())
    assert observations[-1]['command'] == list(checked.provenance.command)
    assert observations[-1]['exit_code'] == 0
    from pcc.frontends.rust.moves import MoveChecker
    with monkeypatch.context() as patch:
        patch.setattr(subprocess, 'Popen', forbidden)
        patch.setattr(MoveChecker, 'check', forbidden)
        patch.setattr(MoveChecker, 'check_function', forbidden)
        result = checked.compile_owned(module_name='selected_edition')
        direct = compile_rust(original.decode(), rustc_check=checked, edition=edition)
        assert result.edition == direct.edition == edition
        assert result.externally_checked and not result.borrow_checked_by_pcc
        assert result.emit_object().startswith(b'\x7fELF')
        if edition == 2021:
            assert compile_rust(original.decode(), rustc_check=checked).edition == 2021
    other = 2021 if edition != 2021 else 2024
    with monkeypatch.context() as patch:
        patch.setattr(frontend, 'Parser', forbidden)
        patch.setattr(subprocess, 'Popen', forbidden)
        with pytest.raises(RustError, match='external rustc check configuration'):
            compile_rust(original.decode(), rustc_check=checked, edition=other)
        if edition != 2021:
            with pytest.raises(RustError, match='external rustc check configuration'):
                compile_rust(original.decode(), rustc_check=checked)
        with pytest.raises(RustError, match='unchanged Rust source bytes'):
            compile_rust(original.decode() + '\n', rustc_check=checked, edition=edition)
    assert path.read_bytes() == original


@pytest.mark.xdist_group(name='rust-native-reference')
@pytest.mark.pcc_gate(unavailable=actual_check_prerequisite())
@pytest.mark.parametrize('name', ('standard_marker_module', 'local_bound_supertraits',
                                 'modules_mutable_reference'))
def test_actual_2024_token_reaches_marker_trait_and_module_revalidation(name, tmp_path, monkeypatch):
    path = PROGRAMS / (name + '.rs')
    original = path.read_bytes()
    checked = check_rust_source(original.decode(), edition=2024, toolchain=toolchain(),
                                evidence_directory=tmp_path / 'check')
    from pcc.frontends.rust.moves import MoveChecker
    with monkeypatch.context() as patch:
        patch.setattr(subprocess, 'Popen', forbidden)
        patch.setattr(MoveChecker, 'check', forbidden)
        patch.setattr(MoveChecker, 'check_function', forbidden)
        result = compile_rust(original.decode(), rustc_check=checked, edition=2024)
        assert result.edition == 2024 and result.externally_checked
        assert not result.borrow_checked_by_pcc
        assert result.emit_object().startswith(b'\x7fELF')
    if name == 'standard_marker_module':
        assert result.standard_marker_contract.edition == '2024'
        assert result.standard_marker_contract.source_sha256 == sha256(original).hexdigest()
    assert path.read_bytes() == original


@pytest.mark.xdist_group(name='rust-native-reference')
@pytest.mark.pcc_gate(unavailable=actual_check_prerequisite())
def test_check_then_owned_convenience_entry_preserves_explicit_edition(tmp_path):
    path = PROGRAMS / 'edition_context_values.rs'
    original = path.read_bytes()
    result = compile_rust_checked(original.decode(), edition=2024, toolchain=toolchain(),
                                  evidence_directory=tmp_path / 'check')
    record = json.loads((tmp_path / 'check' / 'external-check.json').read_text())
    assert record['edition'] == '2024' and record['command'][1] == '--edition=2024'
    assert result.edition == 2024 and result.externally_checked
    assert path.read_bytes() == original


@pytest.mark.xdist_group(name='rust-native-reference')
@pytest.mark.pcc_gate(unavailable=actual_check_prerequisite())
def test_real_rustc_edition_difference_is_not_silently_retried_or_rewritten(tmp_path, monkeypatch):
    # This checks the transition adapter's actual command, NOT PCC support for
    # gen keyword semantics. Identical bytes are valid2021/reserved2024.
    source = 'pub fn gen()->u64{42}'
    pin = toolchain()
    checked = check_rust_source(source, edition=2021, toolchain=pin,
                                evidence_directory=tmp_path / '2021')
    assert checked.provenance.edition == '2021'
    from pcc.frontends.rust.lowering import Lowerer
    monkeypatch.setattr(Lowerer, 'lower', forbidden)
    with pytest.raises(RustExternalCheckError, match='reserved keyword'):
        compile_rust_checked(source, edition=2024, toolchain=pin,
                              evidence_directory=tmp_path / '2024')
    for edition in EDITIONS[2:]:
        assert (tmp_path / str(edition) / 'original.rs').read_bytes() == source.encode()
    rejected = json.loads((tmp_path / '2024' / 'processes.json').read_text())
    assert len(rejected) == 3
    assert rejected[-1]['command'][1] == '--edition=2024'
    assert rejected[-1]['exit_code'] != 0
    assert not (tmp_path / '2024' / 'external-check.json').exists()


@pytest.mark.integration
@pytest.mark.xdist_group(name='rust-native-reference')
@pytest.mark.pcc_gate(unavailable=None if sys.platform.startswith('linux') and platform.machine() in {'x86_64', 'AMD64'} else 'requires Linux x86-64 owned execution')
@pytest.mark.parametrize('edition', (2021, 2024))
def test_unchanged_edition_fixture_has_full_width_owned_execution(edition, tmp_path, monkeypatch):
    from tests.rust import test_rust_patterns as native

    def selected(source, **options):
        return compile_rust(source, edition=edition, experimental_rustc_checked=True, **options)

    monkeypatch.setattr(native, 'compile_source', selected)
    native.test_original_match_fixture_full_width_owned_execution(
        'edition_context_values', tmp_path, monkeypatch)
    assert (tmp_path / 'stdout.bin').read_bytes() == EXPECTED
    (tmp_path / 'edition.json').write_text(json.dumps({
        'edition': edition, 'compiler_owner': 'PCC owned frontend/assembler/linker',
        'source_sha256': sha256((PROGRAMS / 'edition_context_values.rs').read_bytes()).hexdigest(),
    }, indent=2) + '\n')


@pytest.mark.integration
@pytest.mark.xdist_group(name='rust-native-reference')
@pytest.mark.pcc_gate(unavailable=actual_check_prerequisite() or (
    None if sys.platform.startswith('linux') and platform.machine() in {'x86_64', 'AMD64'}
    and shutil.which('cc') else 'requires Linux x86-64 and an explicit system-linker reference'))
def test_actual_2024_reference_matches_same_source_owned_native_observation(tmp_path, monkeypatch):
    path = PROGRAMS / 'edition_context_values.rs'
    original = path.read_bytes()
    pin = toolchain()
    checked = check_rust_source(original.decode(), edition=2024, toolchain=pin,
                                evidence_directory=tmp_path / 'check')
    compiler = checked.provenance.command[0]
    observer = tmp_path / 'observer.rs'
    observer.write_text('extern crate edition_reference;\n'
                        'use std::io::{self,Write};\n'
                        'fn main(){io::stdout().write_all(&edition_reference::answer().to_le_bytes()).unwrap();}\n')
    library, binary = tmp_path / 'libedition_reference.rlib', tmp_path / 'reference'
    common = ['--edition=2024', '--target=' + TARGET, '-Coverflow-checks=off', '-Cpanic=abort']
    commands = [
        [compiler, *common, '--crate-name=edition_reference', '--crate-type=rlib',
         str(path), '-o', str(library)],
        [compiler, *common, '--crate-name=edition_observer', '-Clinker=' + shutil.which('cc'),
         '--extern', 'edition_reference=' + str(library), str(observer), '-o', str(binary)],
    ]
    observations = []
    for index, command in enumerate(commands):
        result = subprocess.run(command, capture_output=True, timeout=15)
        (tmp_path / f'reference-compile-{index}.stdout').write_bytes(result.stdout)
        (tmp_path / f'reference-compile-{index}.stderr').write_bytes(result.stderr)
        observations.append({'command': command, 'returncode': result.returncode,
                             'stdout_sha256': sha256(result.stdout).hexdigest(),
                             'stderr_sha256': sha256(result.stderr).hexdigest()})
        (tmp_path / 'reference-compilation.json').write_text(json.dumps(observations, indent=2) + '\n')
        assert result.returncode == 0, result.stderr.decode(errors='replace')
    reference = subprocess.run([str(binary)], capture_output=True, timeout=5)
    (tmp_path / 'reference.stdout').write_bytes(reference.stdout)
    (tmp_path / 'reference.stderr').write_bytes(reference.stderr)
    owned = tmp_path / 'owned'
    owned.mkdir()
    test_unchanged_edition_fixture_has_full_width_owned_execution(2024, owned, monkeypatch)
    with Path(compiler).open('rb') as executable:
        compiler_sha256_after = file_digest(executable, 'sha256').hexdigest()
    record = {
        'edition': 2024, 'source_sha256': sha256(original).hexdigest(),
        'source_unchanged': path.read_bytes() == original,
        'reference_owner': 'explicit audited rustc + std observer + system C linker',
        'compiler_sha256': checked.provenance.compiler_sha256,
        'compiler_sha256_after': compiler_sha256_after,
        'compiler_version': checked.provenance.compiler_version,
        'reference_returncode': reference.returncode,
        'reference_stdout_sha256': sha256(reference.stdout).hexdigest(),
        'reference_stderr_sha256': sha256(reference.stderr).hexdigest(),
        'reference_binary_sha256': sha256(binary.read_bytes()).hexdigest(),
        'observer_sha256': sha256(observer.read_bytes()).hexdigest(),
        'owned_stdout_sha256': sha256((owned / 'stdout.bin').read_bytes()).hexdigest(),
    }
    (tmp_path / 'edition-runtime-differential.json').write_text(json.dumps(record, indent=2) + '\n')
    assert record['source_unchanged']
    assert compiler_sha256_after == checked.provenance.compiler_sha256
    assert (reference.returncode, reference.stdout, reference.stderr) == (0, EXPECTED, b'')
    assert reference.stdout == (owned / 'stdout.bin').read_bytes()
