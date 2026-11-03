"""CI obligations for externally owned lint semantics, not owned lint parity.

Rust Reference attributes.diagnostics.lint, retrieved 2026-11-03:
https://doc.rust-lang.org/reference/attributes/diagnostics.html
Actual oracle identity is recorded by the existing audited checker. No local
execution or new qualified evidence is claimed by adding these tests.
"""
from copy import copy
from dataclasses import FrozenInstanceError
from hashlib import sha256
from pathlib import Path
import platform
import shutil
import subprocess
import sys

import pytest

from pcc.frontends.rust import (
    RustError, RustExternalCheckError, check_rust_source, compile_rust,
)
from pcc.frontends.rust.syntax import Parser
from tests.rust.certification_support import actual_check_prerequisite
from tests.rust.test_fallible_streaming_iterator_project import (
    ci_prerequisite, reference_process,
)
from tests.rust.test_rust_certified_references import toolchain

TARGET = 'x86_64-unknown-linux-gnu'
ACTUAL_UNAVAILABLE = ci_prerequisite() or actual_check_prerequisite()
NATIVE_UNAVAILABLE = (ACTUAL_UNAVAILABLE or
    (None if sys.platform.startswith('linux') and platform.machine() in {'x86_64', 'AMD64'}
     and shutil.which('cc') else 'requires Linux x86-64 and an external reference system linker'))
PROGRAM = Path(__file__).with_name('programs') / 'certified_crate_lints.rs'
BODY = 'pub fn answer()->u64{0xfedcba9876543210}'


def certify(source, directory, *, edition=2021):
    return check_rust_source(source, toolchain=toolchain(), evidence_directory=directory,
                             target_triple=TARGET, edition=edition)


def test_uncertified_lints_retain_original_syntax_but_cannot_authorize_lowering(monkeypatch):
    from pcc.frontends.rust.lowering import Lowerer
    monkeypatch.setattr(Lowerer, 'lower', lambda *a: pytest.fail('uncertified lint reached IR'))
    source = '//! crate\n#![warn(missing_docs,)]\n' + BODY
    parser = Parser(source, source_path='original.rs')
    with pytest.raises(RustError, match='original.rs: 2:4: missing_docs lint semantics require an actual'):
        parser.parse()
    attribute, = parser.crate_lint_attributes
    assert attribute.level == 'warn' and attribute.lints[0].name == 'missing_docs'
    assert source[slice(*attribute.source_span)] == '#![warn(missing_docs,)]'
    assert source[slice(*attribute.lints[0].source_span)] == 'missing_docs'
    assert parser.crate_documentation[0].text == ' crate'
    with pytest.raises(FrozenInstanceError):
        attribute.level = 'allow'
    with pytest.raises(FrozenInstanceError):
        attribute.lints[0].name = 'dead_code'
    with pytest.raises(RustError, match='missing_docs lint semantics require an actual'):
        compile_rust(source, experimental_rustc_checked=True)
    with pytest.raises(RustError, match='missing_docs lint semantics require an actual'):
        Parser(source, rustc_check=True).parse()


@pytest.mark.parametrize('attribute,message', [
    ('warn()', 'empty lint lists'),
    ('allow(dead_code)', 'only missing_docs'),
    ('warn(warnings)', 'only missing_docs'),
    ('warn(missing_docs, dead_code)', 'only missing_docs'),
    ('deny(missing_docs = "yes")', 'only missing_docs'),
    ('forbid(missing_docs(reason))', 'only missing_docs'),
    ('warn(missing_docs, reason="text")', 'only missing_docs'),
    ('expect(missing_docs)', 'unsupported semantic crate attribute'),
    ('cfg_attr(any(), no_std)', 'cfg_attr requires an actual'),
])
def test_other_semantic_attributes_and_lint_forms_are_not_stripped(attribute, message):
    with pytest.raises(RustError, match=message):
        Parser('#![' + attribute + ']\n' + BODY).parse()


@pytest.mark.parametrize('source,message', [
    ('#[warn(missing_docs)]' + BODY, 'unsupported Rust attribute'),
    ('mod m{#![allow(missing_docs)]fn f(){}}', 'semantic inner module attributes'),
    ('struct R{#[allow(missing_docs)]pub x:u64}', "expected an identifier"),
])
def test_outer_module_and_member_lint_placement_remains_unimplemented(source, message):
    with pytest.raises(RustError, match=message):
        Parser(source).parse()


@pytest.mark.integration
@pytest.mark.xdist_group(name='rust-native-reference')
@pytest.mark.pcc_gate(unavailable=ACTUAL_UNAVAILABLE)
@pytest.mark.parametrize('levels,documented,warns', [
    (('allow',), False, False),
    (('warn',), False, True),
    (('deny',), True, False),
    (('forbid',), True, False),
    (('warn', 'allow'), False, False),
    (('allow', 'warn'), False, True),
    (('deny', 'allow'), False, False),
    (('forbid', 'deny'), True, False),
])
def test_actual_lint_levels_preserve_order_metadata_and_exact_diagnostic_bytes(
        levels, documented, warns, tmp_path, monkeypatch):
    source = ('//! crate\n' + ''.join('#![' + level + '(missing_docs)]\n' for level in levels)
              + ('/// answer\n' if documented else '') + BODY)
    directory = tmp_path / 'actual-check'
    checked = certify(source, directory)
    stderr = (directory / 'check.stderr').read_bytes()
    assert checked.stderr == stderr
    assert sha256(stderr).hexdigest() == checked.provenance.stderr_sha256
    assert (b'missing documentation' in stderr) is warns
    assert (directory / 'original.rs').read_bytes() == source.encode()
    with monkeypatch.context() as patch:
        patch.setattr(subprocess, 'Popen', lambda *a, **k: pytest.fail('external tool in owned lint phase'))
        parser = Parser(source, rustc_check=checked)
        parser.parse()
        result = compile_rust(source, rustc_check=checked, target_triple=TARGET)
        assert result.emit_object().startswith(b'\x7fELF')
    lint = result.external_rustc_lints
    assert lint.owner == 'external_rustc_lints'
    assert lint.attributes == parser.crate_lint_attributes
    assert tuple(a.level for a in lint.attributes) == levels
    assert lint.stderr == stderr and lint.provenance == checked.provenance
    for attribute in lint.attributes:
        assert source[slice(*attribute.source_span)] == '#![' + attribute.level + '(missing_docs)]'
    with pytest.raises(FrozenInstanceError):
        lint.owner = 'owned'


@pytest.mark.integration
@pytest.mark.xdist_group(name='rust-native-reference')
@pytest.mark.pcc_gate(unavailable=ACTUAL_UNAVAILABLE)
@pytest.mark.parametrize('levels,documented', [
    (('deny',), False),
    (('forbid',), False),
    (('allow', 'deny'), False),
    (('forbid', 'allow'), True),
    (('forbid', 'warn'), True),
])
def test_actual_lint_errors_never_mint_a_token_or_reach_owned_lowering(
        levels, documented, tmp_path, monkeypatch):
    from pcc.frontends.rust import compile_rust_checked
    from pcc.frontends.rust.lowering import Lowerer
    monkeypatch.setattr(Lowerer, 'lower', lambda *a: pytest.fail('rustc-rejected lint reached IR'))
    source = ('//! crate\n' + ''.join('#![' + level + '(missing_docs)]\n' for level in levels)
              + ('/// answer\n' if documented else '') + BODY)
    directory = tmp_path / 'rejected-check'
    with pytest.raises(RustExternalCheckError, match='external rustc rejected input'):
        compile_rust_checked(source, toolchain=toolchain(), evidence_directory=directory,
                             target_triple=TARGET)
    assert not (directory / 'external-check.json').exists()
    assert (directory / 'original.rs').read_bytes() == source.encode()
    assert b'error' in (directory / 'check.stderr').read_bytes()


@pytest.mark.integration
@pytest.mark.xdist_group(name='rust-native-reference')
@pytest.mark.pcc_gate(unavailable=ACTUAL_UNAVAILABLE)
def test_lint_capability_rejects_changed_source_edition_options_and_diagnostic_bytes(tmp_path, monkeypatch):
    from pcc.frontends.rust.lowering import Lowerer
    source = PROGRAM.read_text()
    checked = certify(source, tmp_path / 'actual-check')
    monkeypatch.setattr(Lowerer, 'lower', lambda *a: pytest.fail('mismatched lint evidence reached IR'))
    for options in ({'edition': 2015}, {'target_triple': 'arm64-apple-darwin'},
                    {'overflow_checks': True}):
        with pytest.raises(RustError):
            compile_rust(source, rustc_check=checked, **options)
    with pytest.raises(RustError, match='unchanged Rust source bytes'):
        compile_rust(source + '\n', rustc_check=checked)
    with pytest.raises(RustError, match='unchanged Rust source bytes'):
        Parser(source + '\n', rustc_check=checked).parse()
    with pytest.raises(RustError, match='configuration does not match'):
        Parser(source, rustc_check=checked, edition=2015).parse()
    with pytest.raises(RustError, match='caller-constructed external record'):
        compile_rust(source, rustc_check=checked.provenance)
    damaged = copy(checked)
    object.__setattr__(damaged, '_stderr', checked.stderr + b'changed')
    with pytest.raises(RustError, match='diagnostic bytes do not match'):
        compile_rust(source, rustc_check=damaged)
    with pytest.raises(RustError, match='diagnostic bytes do not match'):
        Parser(source, rustc_check=damaged).parse()


@pytest.mark.integration
@pytest.mark.xdist_group(name='rust-native-reference')
@pytest.mark.pcc_gate(unavailable=ACTUAL_UNAVAILABLE)
def test_actual_check_cannot_admit_other_lints_or_semantic_attributes(tmp_path, monkeypatch):
    from pcc.frontends.rust.lowering import Lowerer
    monkeypatch.setattr(Lowerer, 'lower', lambda *a: pytest.fail('unimplemented attribute reached IR'))
    for index, (prefix, message) in enumerate((
            ('#![allow(dead_code)]', 'only missing_docs'),
            ('#[allow(missing_docs)]', 'unsupported Rust attribute'),
            ('#![cfg_attr(not(any()), no_std)]', None))):
        source = prefix + '\n' + BODY
        checked = certify(source, tmp_path / ('actual-check-' + str(index)))
        if message is None:
            # Preserve this former unsupported original as a positive policy
            # control; whole cfg_attr native execution has its own CI node.
            parser = Parser(source, rustc_check=checked)
            parser.parse()
            assert parser.crate_policy.implicit_library_import == 'core'
        else:
            with pytest.raises(RustError, match=message):
                compile_rust(source, rustc_check=checked)


@pytest.mark.integration
@pytest.mark.xdist_group(name='rust-native-reference')
@pytest.mark.pcc_gate(unavailable=ACTUAL_UNAVAILABLE)
def test_unchanged_project_retains_lint_before_the_next_source_boundary(tmp_path):
    from tests.rust.test_fallible_streaming_iterator_project import certify_original, load_inputs
    _, files = load_inputs()
    _, checked = certify_original(files, tmp_path / 'actual-check')
    source = files['src/lib.rs'].decode()
    parser = Parser(source, source_path='src/lib.rs', edition=2015, rustc_check=checked)
    with pytest.raises(RustError, match="src/lib.rs: 19:3: unsupported Rust attribute"):
        parser.parse()
    lint, = parser.crate_lint_attributes
    assert lint.level == 'warn' and lint.lints[0].name == 'missing_docs'
    assert source[slice(*lint.source_span)] == '#![warn(missing_docs)]'
    assert parser.crate_cfg_attributes[0].enabled
    assert parser.crate_policy.implicit_library_import == 'core'
    assert parser.crate_documentation[0].text == ' Fallible, streaming iteration.'
    assert load_inputs()[1] == files
    # This is syntax retention at a capability failure, never an object/package
    # pass. The existing strict original-crate object test is unchanged.


@pytest.mark.integration
@pytest.mark.xdist_group(name='rust-native-reference')
@pytest.mark.pcc_gate(unavailable=NATIVE_UNAVAILABLE)
def test_unchanged_lint_program_has_owned_native_and_actual_rustc_reference_results(tmp_path, monkeypatch):
    from pcc.backend.elf_x86_64 import link_static_executable, parse_relocatable
    from pcc.backend.x86_64_asm_driver import assemble_file
    from tests.rust.test_rust_ffi import record_run
    original = PROGRAM.read_bytes()
    audited = toolchain()
    checked = check_rust_source(original.decode(), toolchain=audited,
                                evidence_directory=tmp_path / 'actual-check', target_triple=TARGET)
    with monkeypatch.context() as patch:
        patch.setattr(subprocess, 'Popen', lambda *a, **k: pytest.fail('external tool entered owned phase'))
        result = compile_rust(original.decode(), rustc_check=checked, target_triple=TARGET)
        obj = result.emit_object()
        observer = assemble_file('''.intel_syntax noprefix
.text
.globl _start
_start:
 call ''' + result.symbols['answer'] + '''
 sub rsp,16
 mov QWORD PTR [rsp],rax
 mov eax,1
 mov edi,1
 mov rsi,rsp
 mov edx,8
 syscall
 mov eax,60
 xor edi,edi
 syscall
''')
        binary = link_static_executable([parse_relocatable(obj), observer])
    owned = tmp_path / 'owned'
    owned.mkdir()
    expected = bytes.fromhex('1032547698badcfe')
    record_run(binary, expected, owned)
    assert result.external_rustc_lints.owner == 'external_rustc_lints'
    assert result.external_rustc_lints.stderr == (tmp_path / 'actual-check/check.stderr').read_bytes()
    assert b'missing documentation' in result.external_rustc_lints.stderr
    compiler = audited.root / 'bin/rustc'
    assert sha256(compiler.read_bytes()).hexdigest() == audited.compiler_sha256
    wrapper = tmp_path / 'reference-observer.rs'
    wrapper.write_text('extern crate lint_reference;\nuse std::io::{self, Write};\n'
                       'fn main(){io::stdout().write_all(&lint_reference::answer().to_le_bytes()).unwrap();}\n')
    library, reference = tmp_path / 'liblint_reference.rlib', tmp_path / 'reference'
    common = ['--edition=2021', '--target=' + TARGET, '-Coverflow-checks=off', '-Cpanic=abort']
    observations = []
    reference_process(tmp_path, 'reference-library', [str(compiler), *common,
        '--crate-name=lint_reference', '--crate-type=rlib', str(PROGRAM), '-o', str(library)], observations)
    reference_process(tmp_path, 'reference-link', [str(compiler), *common,
        '-Clinker=' + shutil.which('cc'), '--extern', 'lint_reference=' + str(library),
        str(wrapper), '-o', str(reference)], observations)
    actual = reference_process(tmp_path, 'reference-run', [str(reference)], observations, timeout=5)
    assert actual.stdout == expected == (owned / 'stdout.bin').read_bytes()
    assert actual.stderr == b''
    assert PROGRAM.read_bytes() == original
    assert sha256(compiler.read_bytes()).hexdigest() == audited.compiler_sha256
