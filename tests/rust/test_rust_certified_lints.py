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


def observe_function_owners(monkeypatch):
    # Inline parsers copy the parent's instance dictionary. Patch the class so
    # each call binds to its actual parser, never to a copied parent closure.
    owners = {}
    finish_configured_item = Parser.finish_configured_item

    def retain_function_owner(parser, pending, functions):
        if parser in owners:
            assert owners[parser] is functions
        else:
            owners[parser] = functions
        return finish_configured_item(parser, pending, functions)

    monkeypatch.setattr(Parser, 'finish_configured_item', retain_function_owner)
    return owners


def test_function_owner_observer_distinguishes_parent_and_inline_child(monkeypatch):
    parser = Parser('fn before(){}mod child{fn first(){}fn second(){}}fn after(){}')
    original_finish = Parser.finish_configured_item
    with monkeypatch.context() as patch:
        owners = observe_function_owners(patch)
        functions = parser.parse()
    assert Parser.finish_configured_item is original_finish
    assert owners[parser] is functions
    assert [function.token.text for function in functions] == ['before', 'after']
    child, = [owner for owner in owners if owner is not parser]
    module, = parser.modules
    assert module.body is not None
    assert owners[child] is module.body.functions
    assert owners[child] is not functions
    assert [function.token.text for function in owners[child]] == ['first', 'second']
    assert all(function is not nested for function in functions for nested in owners[child])
    assert 'finish_configured_item' not in parser.__dict__
    assert 'finish_configured_item' not in child.__dict__


def test_owned_lints_retain_original_syntax(monkeypatch):
    # Without an actual check the owned missing_docs lint decides the level
    # (stage 8, tests/rust/test_rust_owned_lints.py); `warn` never rejects.
    source = '//! crate\n#![warn(missing_docs,)]\n' + BODY
    parser = Parser(source, source_path='original.rs')
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
    assert not compile_rust(source).externally_checked
    # A supplied record still has to be an actual CheckedRustSource.
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
def test_unchanged_project_retains_lint_before_the_next_source_boundary(tmp_path, monkeypatch):
    from tests.rust.test_fallible_streaming_iterator_project import certify_original, load_inputs
    from pcc.frontends.rust.syntax import Function, Implementation
    _, files = load_inputs()
    _, checked = certify_original(files, tmp_path / 'actual-check')
    original = files['src/lib.rs']
    assert len(original) == 21146
    assert sha256(original).hexdigest() == '7fb7428997bdd89c3ee0818ecee642f8d93882c9c66171c9c6194b732f337089'
    source = original.decode('utf-8')
    parser = Parser(source, source_path='src/lib.rs', edition=2015, rustc_check=checked)
    # Observe the real function owner, not a reconstructed list of declarations:
    # finish_configured_item must remove disabled methods as well as their impl.
    # The exact authenticated library source now parses through cfg(test) to
    # EOF. Lint/syntax retention still does not imply whole-crate compilation.
    with monkeypatch.context() as owned:
        function_owners = observe_function_owners(owned)
        owned.setattr(subprocess, 'Popen', lambda *a, **k: pytest.fail('external tool entered owned parser'))
        functions = parser.parse()
    parsed_functions = function_owners[parser]
    assert parsed_functions is functions
    assert any(owner is not parser for owner in function_owners)
    assert all(owned_functions is not functions for owner, owned_functions in function_owners.items()
               if owner is not parser)
    assert (parser.token.text, parser.token.line, parser.token.column) == ('<eof>', 900, 1)
    assert parser.token.source_path == 'src/lib.rs' and not parser.modules
    assert ['::'.join(token.text for token in item.path) for item in parser.uses] == [
        'core::cmp', 'core::marker::PhantomData']
    assert [(item.token.line, item.token.column, item.source_span) for item in parser.uses] == [
        (22, 1, (659, 673)), (23, 1, (674, 704))]
    assert all(item.token.source_path == 'src/lib.rs' for item in parser.uses)
    assert [source[slice(*item.source_span)] for item in parser.uses] == [
        'use core::cmp;', 'use core::marker::PhantomData;']
    lint, = parser.crate_lint_attributes
    assert lint.level == 'warn' and lint.lints[0].name == 'missing_docs'
    missing_docs, = lint.lints
    assert (lint.token.line, lint.token.column, lint.source_span) == (16, 4, (548, 570))
    assert (missing_docs.form, missing_docs.value, missing_docs.children) == ('word', None, ())
    assert (missing_docs.token.line, missing_docs.token.column) == (16, 9)
    assert source[slice(*missing_docs.source_span)] == 'missing_docs'
    assert source[slice(*lint.source_span)] == '#![warn(missing_docs)]'
    configured_crate, = parser.crate_cfg_attributes
    assert configured_crate.enabled
    assert configured_crate.syntax.source_span == (571, 613)
    assert source[slice(*configured_crate.syntax.source_span)] == '#![cfg_attr(not(feature = "std"), no_std)]'
    no_std, = configured_crate.syntax.attributes
    assert no_std.name == 'no_std' and no_std.form == 'word'
    assert parser.crate_policy.no_std is no_std.token
    assert (no_std.token.line, no_std.token.column) == (17, 35)
    assert parser.crate_policy.no_core is None and parser.crate_policy.edition == 2015
    assert parser.crate_policy.implicit_library_import == 'core'
    assert parser.crate_policy.implicit_extern_prelude == ('core',)
    assert parser.crate_policy.standard_prelude_path == ('core', 'prelude', 'rust_2015')

    # Exhaust all three retained disabled items, including the newly handled
    # test module. Keep every original extern/Box<I> retention obligation.
    conditional, boxed, test_module = parser.conditional_items
    assert not conditional.enabled and not parser.crate_aliases
    assert conditional.item.crate.text == 'core' and conditional.item.token.line == 20
    assert conditional.item.token.column == 1 and conditional.item.crate.column == 14
    assert conditional.item.alias is None and not conditional.item.public
    assert conditional.attributes[0].syntax.token.line == 19
    assert conditional.source_span == conditional.item.source_span == (615, 657)
    assert source[slice(*conditional.source_span)] == '#[cfg(feature = "std")]\nextern crate core;'
    assert not boxed.enabled and boxed.source_span == (8781, 9347)
    boxed_source = source[slice(*boxed.source_span)]
    assert boxed_source == '\n'.join(source.splitlines()[333:361])
    assert sha256(boxed_source.encode('utf-8')).hexdigest() == 'dc35eff5d806f419c229e7c1cd9b01b7f332175631e5fa776575ac1106ac0dba'
    for item, line, span in ((conditional, 19, (615, 638)), (boxed, 334, (8781, 8804))):
        attribute, = item.attributes
        assert not attribute.enabled
        assert (attribute.syntax.token.line, attribute.syntax.token.column) == (line, 3)
        assert attribute.syntax.source_span == span
        assert source[slice(*span)] == '#[cfg(feature = "std")]'
        predicate = attribute.syntax.predicate
        assert (predicate.name, predicate.form, predicate.value, predicate.children) == ('feature', 'value', 'std', ())
        assert attribute.configuration == configured_crate.configuration
        assert all(token.source_path == 'src/lib.rs' for token in item.tokens)
    assert [(item.tokens[0].text, item.tokens[0].line, item.tokens[0].column,
             item.tokens[-1].text, item.tokens[-1].line, item.tokens[-1].column)
            for item in (conditional, boxed)] == [
        ('#', 19, 1, ';', 20, 18), ('#', 334, 1, '}', 361, 1)]
    # The disabled impl parsed fully: keep its four methods and associated types
    # as retained syntax, while proving none remains in the live owners.
    assert len(boxed.item) == 5
    boxed_impl, = [item for item in boxed.item if isinstance(item, Implementation)]
    boxed_methods = [item for item in boxed.item if isinstance(item, Function)]
    assert str(boxed_impl.owner) == 'Box<I>' and boxed_impl.trait == 'FallibleStreamingIterator'
    assert (boxed_impl.token.line, boxed_impl.token.column) == (335, 1)
    assert [(item.token.text, item.token.line, item.token.column, item.value)
            for item in boxed_impl.associated_types] == [
        ('Item', 339, 10, 'I::Item'), ('Error', 340, 10, 'I::Error')]
    assert [(item.token.text, item.token.line) for item in boxed_methods] == [
        ('advance', 343), ('get', 348), ('size_hint', 353), ('next', 358)]
    assert all(method.implementation is boxed_impl for method in boxed_methods)
    assert all(item is not boxed_impl and str(item.owner) != 'Box<I>' for item in parser.implementations)
    assert parsed_functions is not None and parsed_functions
    assert all(all(function is not method for function in parsed_functions) for method in boxed_methods)
    assert all(function.implementation is None or str(function.implementation.owner) != 'Box<I>'
               for function in parsed_functions)
    # cfg(test)'s whole token-bounded module is retained but never admitted.
    assert [item.source_span for item in parser.item_cfg_syntax] == [(615, 638), (8781, 8804), (20923, 20935)]
    test_cfg = parser.item_cfg_syntax[-1]
    assert (test_cfg.predicate.name, test_cfg.predicate.form) == ('test', 'word')
    assert source[slice(*test_cfg.source_span)] == '#[cfg(test)]'
    test_attribute, = test_module.attributes
    assert test_attribute.syntax is test_cfg and not test_attribute.enabled
    assert test_attribute.configuration == configured_crate.configuration
    assert test_attribute.configuration.test_harness is False
    assert not test_module.enabled and test_module.item == ()
    assert test_module.source_span == (20923, 21145)
    assert source[slice(*test_module.source_span)] == '\n'.join(source.splitlines()[892:899])
    assert all(token.source_path == 'src/lib.rs' for token in test_module.tokens)
    assert [(token.text, token.line, token.column)
            for token in (test_module.tokens[0], test_module.tokens[-1])] == [
        ('#', 893, 1), ('}', 899, 1)]
    assert all(function.token.text not in {'_is_object_safe', '_is_object_safe_double'}
               for function in functions)

    assert parser.crate_documentation[0].text == ' Fallible, streaming iteration.'
    assert len(parser.crate_documentation) == 15
    assert [source[slice(*doc.source_span)] for doc in parser.crate_documentation] == source.splitlines()[:15]
    assert all(doc.inner and doc.token.source_path == 'src/lib.rs' for doc in parser.crate_documentation)
    assert [doc.text for doc in parser.crate_documentation[:14]] == [line[3:] for line in source.splitlines()[:14]]
    html_root, = parser.crate_documentation[-1].options
    assert (html_root.name, html_root.form, html_root.value) == (
        'html_root_url', 'value', 'https://docs.rs/fallible-streaming-iterator/0.1')
    configuration = configured_crate.configuration
    assert configuration.source_sha256 == checked.provenance.source_sha256 == sha256(original).hexdigest()
    assert configuration.edition == 2015 and checked.provenance.edition == '2015'
    assert configuration.target_triple == checked.provenance.target_triple == TARGET
    assert configuration.explicit_cfg == checked.provenance.cfg == checked.provenance.dependencies == ()
    assert (tmp_path / 'actual-check/original.rs').read_bytes() == original
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
