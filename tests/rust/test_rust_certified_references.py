"""Externally checked reference lowering, not an owned borrow checker.

Rust Reference, retrieved 2026-10-31 (actual oracle version is recorded per run):
https://doc.rust-lang.org/reference/types/pointer.html#references--and-mut
https://doc.rust-lang.org/reference/expressions/operator-expr.html#borrow-operators
https://doc.rust-lang.org/reference/expressions/operator-expr.html#assignment-expressions
https://doc.rust-lang.org/reference/expressions/operator-expr.html#compound-assignment-expressions
https://doc.rust-lang.org/reference/expressions/field-expr.html#borrowing
https://doc.rust-lang.org/reference/lifetime-elision.html
https://doc.rust-lang.org/reference/expressions/method-call-expr.html
Feature: rust.references.external-certified-addresses (not full references).
"""
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys

import pytest

from pcc.frontends.rust import (
    CheckedRustSource, ExternalRustcCheck, RustError, RustExternalCheckError,
    RustcToolchain, check_rust_source, compile_rust, compile_rust_checked,
)

TARGET = 'x86_64-unknown-linux-gnu'
PROGRAMS = ('certified_mut_scalar', 'certified_mut_record', 'certified_mut_methods',
            'certified_local_reborrow', 'certified_formal_rebinding',
            'certified_mut_formal_rebinding', 'certified_self_rebinding',
            'certified_nonlexical_reborrow', 'certified_generic_mut', 'certified_inferred_mut',
            'certified_assignment_order', 'certified_compound_assignment_order',
            'certified_field_assignment_order', 'certified_local_slot_rebinding',
            'certified_disjoint_mutable_fields', 'certified_generic_mut_receiver')


def digest(data):
    return hashlib.sha256(data).hexdigest()


def toolchain():
    # Explicit audited installation receipt, never a resolved rustup/PATH proxy.
    receipt = json.loads(Path(os.environ['PCC_RUSTC_INSTALL_RECEIPT']).read_text())
    root = Path(receipt['new_prefix'])
    pin = next(row['sha256'] for row in receipt['installed_files']
               if Path(row['path']) == root / 'bin/rustc')
    return RustcToolchain(root, pin, receipt['version'])


def certify(path, directory):
    original = path.read_bytes()
    checked = check_rust_source(original.decode(), toolchain=toolchain(),
                                evidence_directory=directory, target_triple=TARGET)
    assert path.read_bytes() == original
    assert checked.provenance.source_sha256 == digest(original)
    return checked


@pytest.mark.xdist_group(name='rust-native-reference')
@pytest.mark.pcc_gate(unavailable=None if os.environ.get('PCC_RUSTC_INSTALL_RECEIPT') else 'explicit audited rustc installation receipt required')
@pytest.mark.parametrize('name', PROGRAMS)
def test_actual_external_check_removes_partial_owned_loan_barrier(name, tmp_path, monkeypatch):
    source = Path(__file__).with_name('programs') / (name + '.rs')
    check = certify(source, tmp_path / 'certifier')
    from pcc.frontends.rust.moves import MoveChecker
    monkeypatch.setattr(MoveChecker, 'check', lambda *a: pytest.fail('owned borrow/move admission was executed'))
    monkeypatch.setattr(MoveChecker, 'check_function', lambda *a: pytest.fail('owned abstract move admission was executed'))
    result = compile_rust(source.read_text(), rustc_check=check)
    assert result.externally_checked and not result.borrow_checked_by_pcc
    assert 'ptr' in result.ir_text or '*' in result.ir_text
    assert result.emit_object().startswith(b'\x7fELF')


@pytest.mark.xdist_group(name='rust-native-reference')
@pytest.mark.pcc_gate(unavailable=None if os.environ.get('PCC_RUSTC_INSTALL_RECEIPT') else 'explicit audited rustc installation receipt required')
def test_successful_record_is_bound_to_exact_source_options_and_tool_metadata(tmp_path, monkeypatch):
    path = Path(__file__).with_name('programs') / 'certified_mut_scalar.rs'
    checked = certify(path, tmp_path / 'certifier')
    check = checked.provenance
    from pcc.frontends.rust.lowering import Lowerer
    monkeypatch.setattr(Lowerer, 'lower', lambda *a: pytest.fail('mismatched check reached IR'))
    bad = [replace(check, source_sha256='0' * 64), replace(check, edition='2024'),
           replace(check, target_triple='arm64-apple-darwin'), replace(check, cfg=('flag',)),
           replace(check, dependencies=(('dep', '0' * 64),)), replace(check, overflow_checks=True),
           replace(check, exit_code=1), replace(check, compiler_sha256='missing'),
           replace(check, metadata_sha256='missing'), replace(check, command=('/not/a/check',))]
    for extra in (('-Coverflow-checks=on',), ('--cfg', 'other'),
                  ('--extern', 'dep=/tmp/dep.rlib'), ('@response-file',),
                  ('--edition=2024',), ('--target=arm64-apple-darwin',)):
        bad.append(replace(check, command=check.command + extra))
    bad.append(replace(check, command=check.command[:7] + ('@source-response',) + check.command[8:]))
    for record in bad:
        with pytest.raises(RustError, match='caller-constructed external record'):
            compile_rust(path.read_text(), rustc_check=record)
        with pytest.raises(RustError, match='external rustc check'):
            record.validate(path.read_text(), target_triple=TARGET, overflow_checks=False)
    with pytest.raises(RustError, match='unchanged Rust source bytes'):
        compile_rust(path.read_text() + '\n', rustc_check=checked)


INVALID = [
    ('fn f(){let mut x=1;let p=&x;x=2;let y=*p;}', 'E0506'),
    ('fn f(){let mut x=1;let p=&mut x;let q=&mut x;*p+=*q;}', 'E0499'),
    ('fn f()->&u64{let x=1;&x}', 'E0106'),
    ('struct R{x:u64} fn f(){let r=R{x:1};let p=&r;let q=r;let n=p.x;}', 'E0505'),
    ('fn f(p:&u64){*p=1;}', 'E0594'),
]


@pytest.mark.xdist_group(name='rust-native-reference')
@pytest.mark.pcc_gate(unavailable=None if os.environ.get('PCC_RUSTC_INSTALL_RECEIPT') else 'explicit audited rustc installation receipt required')
@pytest.mark.parametrize('source,code', INVALID)
def test_borrow_invalid_original_is_rejected_in_external_phase(source, code, tmp_path, monkeypatch):
    path = tmp_path / 'original.rs'
    path.write_text(source)
    from pcc.frontends.rust.lowering import Lowerer
    monkeypatch.setattr(Lowerer, 'lower', lambda *a: pytest.fail('rejected source reached PCC IR'))
    with pytest.raises(RustExternalCheckError, match=code):
        compile_rust_checked(source, toolchain=toolchain(), evidence_directory=tmp_path / 'certifier')
    assert not (tmp_path / 'certifier' / 'checked.rmeta').exists()
    assert path.read_text() == source


@pytest.mark.integration
@pytest.mark.xdist_group(name='rust-native-reference')
@pytest.mark.pcc_gate(unavailable=None if sys.platform.startswith('linux') and platform.machine() in {'x86_64', 'AMD64'} and shutil.which('rustc') and shutil.which('cc') and os.environ.get('PCC_RUSTC_INSTALL_RECEIPT') else 'requires Linux pinned rustc/system linker reference')
def test_certified_originals_have_full_width_owned_and_external_native_results(tmp_path, monkeypatch):
    from tests.rust import test_rust_method_reference as reference
    from tests.rust import test_rust_patterns as owned
    for name in PROGRAMS:
        directory = tmp_path / name
        directory.mkdir()
        path = Path(__file__).with_name('programs') / (name + '.rs')
        check = certify(path, directory / 'certifier')
        with monkeypatch.context() as patch:
            patch.setattr(reference, 'PROGRAMS', (name,))
            patch.setattr(owned, 'compile_source', lambda source, **options: compile_rust(source, rustc_check=check))
            reference.test_unchanged_inherent_programs_match_actual_rustc_native_execution(directory, patch)


def test_inferred_reference_shape_preserves_mutability_and_lifetime_metadata():
    from pcc.frontends.rust.call_inference import partial
    from pcc.frontends.rust.semantics import Checker
    from pcc.frontends.rust.types import SharedReference
    original = SharedReference('u64', True, "'a")
    resolved = partial(original)
    assert resolved.mutable and resolved.lifetime == "'a"
    assert not Checker([], externally_checked=True).copy_type(resolved)
    assert Checker([], externally_checked=True).copy_type(SharedReference('u64'))


def test_forged_provenance_record_is_not_an_admission_token(monkeypatch):
    source = 'fn f(p:&mut u64){*p=1;}'
    fake = ExternalRustcCheck(
        digest(source.encode()), '2021', TARGET, (), (), False, '0' * 64,
        'rustc fake', ('/fake/rustc', '--edition=2021', '--target=' + TARGET,
                       '-Coverflow-checks=off', '--emit=metadata', '--crate-type=lib',
                       '--crate-name=certified_input', '/fake/source.rs', '-o', '/fake/output.rmeta'),
        0, '0' * 64, '0' * 64)
    # This deliberately demonstrates that provenance shape alone is not proof.
    fake.validate(source, target_triple=TARGET, overflow_checks=False)
    from pcc.frontends.rust.lowering import Lowerer
    monkeypatch.setattr(Lowerer, 'lower', lambda *a: pytest.fail('forged check reached IR'))
    with pytest.raises(RustError, match='caller-constructed external record'):
        compile_rust(source, rustc_check=fake)
    with pytest.raises(RustError, match='only be created by check_rust_source'):
        CheckedRustSource(source, fake)


@pytest.mark.xdist_group(name='rust-native-reference')
@pytest.mark.pcc_gate(unavailable=None if os.environ.get('PCC_RUSTC_INSTALL_RECEIPT') else 'explicit audited rustc installation receipt required')
def test_supported_transition_entry_checks_then_compiles_and_rejects_stale_evidence(tmp_path):
    source = 'pub fn answer()->u64{let mut n=41;let p=&mut n;*p+=1;n}'
    result = compile_rust_checked(source, toolchain=toolchain(), evidence_directory=tmp_path / 'fresh')
    assert result.externally_checked and not result.borrow_checked_by_pcc
    assert result.emit_object().startswith(b'\x7fELF')
    with pytest.raises(FileExistsError):
        check_rust_source(source, toolchain=toolchain(), evidence_directory=tmp_path / 'fresh')


@pytest.mark.xdist_group(name='rust-native-reference')
def test_external_adapter_requires_pinned_direct_tool_before_execution(tmp_path, monkeypatch):
    from pcc.frontends.rust import external_check
    monkeypatch.setattr(external_check.subprocess, 'run', lambda *a, **k: pytest.fail('unverified tool executed'))
    with pytest.raises(RustExternalCheckError, match='audited installation pin'):
        check_rust_source('fn f(){}', toolchain=RustcToolchain(tmp_path / 'absent', '0' * 64, 'rustc fake'),
                          evidence_directory=tmp_path / 'evidence')
    assert not (tmp_path / 'evidence').exists()
