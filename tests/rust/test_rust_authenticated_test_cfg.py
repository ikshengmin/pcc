"""Authenticated library/test build identity, not an owned test harness.

Rust Reference cfg.test, cfg.predicate and cfg.cfg_attr (2026-11-15):
https://doc.rust-lang.org/reference/conditional-compilation.html
The exact audited --crate-type=lib command excludes --test/--cfg. Its
CheckedRustSource supplies test=False to real parser consumers. Other
authenticated built-ins/custom conditions remain explicit gaps; the separate
OwnedRustConfiguration still uses its own recorded target/option membership.
All execution of these obligations is CI-only and unrun at source review.
"""
from copy import copy
from dataclasses import FrozenInstanceError, replace
from hashlib import sha256
from pathlib import Path
import subprocess

import pytest

from pcc.frontends.rust import CfgOptions, RustError, compile_rust
from pcc.frontends.rust.attribute_metadata import MetaItem
from pcc.frontends.rust.configuration import OwnedRustConfiguration, RustConfiguration
from pcc.frontends.rust.syntax import Parser, Token
from tests.rust.test_rust_certified_lints import (
    ACTUAL_UNAVAILABLE, BODY, NATIVE_UNAVAILABLE, TARGET, certify,
)

PROGRAM = Path(__file__).with_name('programs') / 'certified_cfg_test.rs'


def test_test_build_identity_is_not_an_unauthenticated_default():
    source = '#[cfg(test)]mod omitted{}' + BODY
    for checked in (None, True, object()):
        with pytest.raises(RustError, match='cfg requires an actual source/options-bound'):
            Parser(source, rustc_check=checked).parse()
    with pytest.raises(RustError, match='cfg requires an actual source/options-bound'):
        compile_rust(source, experimental_rustc_checked=True)
    predicate = MetaItem(Token('test', 'name', 1, 7), 'test', 'word', None, (), (6, 10))
    configuration = RustConfiguration('0' * 64, 2021, TARGET, ())
    for unbound in (configuration, replace(configuration, test_harness=True),
                    replace(configuration, test_harness=0)):
        with pytest.raises(RustError, match='unimplemented built-in/target'):
            unbound.predicate(predicate)
    bound = replace(configuration, test_harness=False)
    for different_form in (replace(predicate, form='value', value='enabled'),
                           replace(predicate, form='list')):
        with pytest.raises(RustError, match='unimplemented built-in/target'):
            bound.predicate(different_form)
    with pytest.raises(RustError, match='nonempty Rust cfg inputs'):
        replace(bound, explicit_cfg=('test',)).predicate(predicate)
    with pytest.raises(RustError, match='must be an OwnedRustConfiguration'):
        Parser(source, configuration=replace(configuration, test_harness=False))


@pytest.mark.parametrize('first', ('not(test)', 'not(feature="absent")'))
@pytest.mark.parametrize('second,message', (
    ('#[cfg()]', 'exactly one'),
    ('#[cfg(not())]', 'exactly one'),
    ('#[cfg(feature=1)]', 'literal string'),
    ('#[cfg(not(test,test))]', 'exactly one'),
    ('#[cfg(test]', "expected"),
    ('#[cfg_attr(unix, no_mangle)]', 'unsupported Rust attribute'),
    ('#', 'expected'),
    ('#[', 'unsupported Rust attribute'),
))
@pytest.mark.parametrize('reverse', (False, True))
def test_active_item_still_rejects_bad_configuration_attributes(first, second, message, reverse):
    first = '#[cfg(' + first + ')]'
    attributes = second + first if reverse else first + second
    configuration = OwnedRustConfiguration(TARGET, 2021, CfgOptions())
    # Both first predicates are true. These malformed cfg/unsupported cfg_attr
    # forms must not enter an active item in either attribute order.
    with pytest.raises(RustError, match=message):
        Parser(attributes + 'mod omitted{}' + BODY, configuration=configuration).parse()


@pytest.mark.parametrize('ending', ('#', '#[', '#[cfg(', '#[cfg(test)', 'mod omitted{', 'mod omitted{]'))
def test_false_item_does_not_hide_eof_or_delimiter_failures(ending):
    configuration = OwnedRustConfiguration(TARGET, 2021, CfgOptions())
    with pytest.raises(RustError):
        Parser('#[cfg(test)]' + ending, configuration=configuration).parse()


@pytest.mark.parametrize('predicate', ('unix', 'custom_not_in_the_set', 'not(custom_not_in_the_set)'))
def test_owned_membership_semantics_remain_independent(predicate):
    configuration = OwnedRustConfiguration(TARGET, 2021, CfgOptions())
    source = '#[cfg(test)]#[cfg(' + predicate + ')]mod omitted{}' + BODY
    parser = Parser(source, configuration=configuration)
    assert [item.token.text for item in parser.parse()] == ['answer']
    omitted, = parser.conditional_items
    assert not omitted.enabled and not parser.modules
    assert [attribute.enabled for attribute in omitted.attributes] == [
        False, predicate != 'custom_not_in_the_set']


@pytest.mark.parametrize('first', ('test', 'feature="absent"'))
def test_owned_false_item_keeps_existing_disabled_cfg_attr_recovery(first):
    declaration = '#[cfg(' + first + ')]#[cfg_attr(unix, no_mangle)]pub fn omitted(){}'
    parser = Parser(declaration + BODY,
                    configuration=OwnedRustConfiguration(TARGET, 2021, CfgOptions()))
    assert [function.token.text for function in parser.parse()] == ['answer']
    item, = parser.conditional_items
    assert not item.enabled and item.item == ()
    assert parser.source[slice(*item.source_span)] == declaration


@pytest.mark.integration
@pytest.mark.xdist_group(name='rust-native-reference')
@pytest.mark.pcc_gate(unavailable=ACTUAL_UNAVAILABLE)
@pytest.mark.parametrize('edition', (2015, 2021))
@pytest.mark.parametrize('predicate,enabled', (
    ('test', False), ('not(test)', True),
    ('all(not(test),not(feature="std"))', True),
    ('any(test,feature="std")', False),
))
def test_actual_library_build_identity_reaches_parser_and_owned_object(
        edition, predicate, enabled, tmp_path, monkeypatch):
    declaration = '#[cfg(' + predicate + ')]mod selected{pub fn value()->u64{1}}'
    source = declaration + '\n' + BODY
    checked = certify(source, tmp_path / 'actual-check', edition=edition)
    with monkeypatch.context() as owned:
        owned.setattr(subprocess, 'Popen', lambda *a, **k: pytest.fail('external tool in owned cfg phase'))
        configuration = checked.configuration(source, edition=edition)
        parser = Parser(source, edition=edition, rustc_check=checked)
        tokens = tuple(parser.tokens)
        parser.parse()
        result = compile_rust(source, edition=edition, rustc_check=checked, target_triple=TARGET)
        assert result.emit_object().startswith(b'\x7fELF')
    item, = parser.conditional_items
    assert item.enabled is enabled and bool(parser.modules) is enabled
    assert result.conditional_items[0].enabled is enabled
    assert ('selected::value' in result.symbols) is enabled
    assert source[slice(*item.source_span)] == declaration
    assert all(a is b for a, b in zip(tokens, parser.tokens))
    assert len(tokens) == len(parser.tokens)
    assert configuration.test_harness is False
    assert configuration.edition == edition and configuration.target_triple == TARGET
    assert configuration.source_sha256 == checked.provenance.source_sha256 == sha256(source.encode()).hexdigest()
    assert configuration.explicit_cfg == checked.provenance.cfg == ()
    assert '--crate-type=lib' in checked.provenance.command and '--test' not in checked.provenance.command
    assert item.attributes[0].configuration == configuration
    assert (tmp_path / 'actual-check/original.rs').read_bytes() == source.encode()
    with pytest.raises(FrozenInstanceError):
        configuration.test_harness = True


@pytest.mark.integration
@pytest.mark.xdist_group(name='rust-native-reference')
@pytest.mark.pcc_gate(unavailable=ACTUAL_UNAVAILABLE)
@pytest.mark.parametrize('first', (None, 'not(test)', 'not(feature="absent")'))
@pytest.mark.parametrize('predicate', ('unix', 'pcc_custom_cfg', 'all(test,unix)', 'any(not(test),unix)'))
def test_actual_standalone_or_active_unknown_authenticated_predicate_stays_closed(
        first, predicate, tmp_path, monkeypatch):
    attributes = '#[cfg(' + first + ')]' if first is not None else ''
    source = attributes + '#[cfg(' + predicate + ')]mod omitted{}' + BODY
    checked = certify(source, tmp_path / 'actual-check')
    from pcc.frontends.rust.lowering import Lowerer
    monkeypatch.setattr(Lowerer, 'lower', lambda *a: pytest.fail('unknown cfg reached IR'))
    monkeypatch.setattr(subprocess, 'Popen', lambda *a, **k: pytest.fail('external tool in owned cfg phase'))
    with pytest.raises(RustError, match='unimplemented built-in/target'):
        compile_rust(source, rustc_check=checked)


@pytest.mark.integration
@pytest.mark.xdist_group(name='rust-native-reference')
@pytest.mark.pcc_gate(unavailable=ACTUAL_UNAVAILABLE)
@pytest.mark.parametrize('first', ('test', 'feature="absent"'))
@pytest.mark.parametrize('second,message', (
    ('cfg(unix)', 'unimplemented built-in/target'),
    ('cfg(pcc_custom_cfg)', 'unimplemented built-in/target'),
    ('cfg_attr(unix, no_mangle)', 'unsupported Rust attribute'),
))
@pytest.mark.parametrize('reverse', (False, True))
def test_actual_disabled_item_retains_existing_attribute_order_boundary(
        first, second, message, reverse, tmp_path, monkeypatch):
    attributes = ['#[cfg(' + first + ')]', '#[' + second + ']']
    if reverse:
        attributes.reverse()
    source = ''.join(attributes) + 'pub fn omitted(){}' + BODY
    checked = certify(source, tmp_path / 'actual-check')
    monkeypatch.setattr(subprocess, 'Popen', lambda *a, **k: pytest.fail('external tool in owned cfg phase'))
    if reverse:
        # The adapter still cannot evaluate an unknown first predicate or an
        # active unsupported attribute. No earlier condition disabled it.
        from pcc.frontends.rust.lowering import Lowerer
        monkeypatch.setattr(Lowerer, 'lower', lambda *a: pytest.fail('unowned cfg reached IR'))
        with pytest.raises(RustError, match=message):
            compile_rust(source, rustc_check=checked)
    else:
        # Preserve the existing generic recovery of an already-disabled item.
        # This is not implementation of the omitted cfg/cfg_attr semantics.
        parser = Parser(source, rustc_check=checked)
        assert [function.token.text for function in parser.parse()] == ['answer']
        item, = parser.conditional_items
        assert not item.enabled and item.item == ()
        assert source[slice(*item.source_span)] == ''.join(attributes) + 'pub fn omitted(){}'
        result = compile_rust(source, rustc_check=checked)
        assert result.emit_object().startswith(b'\x7fELF') and 'omitted' not in result.symbols


@pytest.mark.integration
@pytest.mark.xdist_group(name='rust-native-reference')
@pytest.mark.pcc_gate(unavailable=ACTUAL_UNAVAILABLE)
def test_actual_disabled_nested_module_preserves_one_whole_token_boundary(tmp_path, monkeypatch):
    declaration = ('/// Unicode π\r\n#[cfg(test)]\r\nmod hidden{\n'
                   'mod nested{#[cfg(unix)]fn omitted(){unimplemented!();}}\n'
                   'use super::*;\nconst TEXT: &str = r#"}])"#;\n}\n')
    source = declaration + BODY
    checked = certify(source, tmp_path / 'actual-check')
    with monkeypatch.context() as owned:
        owned.setattr(subprocess, 'Popen', lambda *a, **k: pytest.fail('external tool in owned cfg phase'))
        parser = Parser(source, source_path='original.rs', rustc_check=checked)
        tokens = tuple(parser.tokens)
        functions = parser.parse()
        result = compile_rust(source, rustc_check=checked)
        assert result.emit_object().startswith(b'\x7fELF')
    item, = parser.conditional_items
    assert not item.enabled and item.item == ()
    assert source[slice(*item.source_span)] == declaration.rstrip('\n')
    assert [function.token.text for function in functions] == ['answer']
    assert not (parser.modules or parser.uses or parser.consts or parser.macro_expansions)
    assert parser.token.kind == 'eof'
    assert len(tokens) == len(parser.tokens) and all(a is b for a, b in zip(tokens, parser.tokens))
    assert item.tokens == tuple(token for token in tokens
        if item.source_span[0] <= parser.line_offsets[token.line - 1] + token.column - 1 < item.source_span[1])
    assert all(token.source_path == 'original.rs' for token in item.tokens)
    assert item.attributes[0].configuration.source_sha256 == sha256(source.encode()).hexdigest()
    assert (tmp_path / 'actual-check/original.rs').read_bytes() == source.encode()


@pytest.mark.integration
@pytest.mark.xdist_group(name='rust-native-reference')
@pytest.mark.pcc_gate(unavailable=ACTUAL_UNAVAILABLE)
def test_actual_enabled_module_still_rejects_unsupported_contents(tmp_path, monkeypatch):
    source = '#[cfg(not(test))]mod active{use super::*;}' + BODY
    checked = certify(source, tmp_path / 'actual-check')
    from pcc.frontends.rust.lowering import Lowerer
    monkeypatch.setattr(Lowerer, 'lower', lambda *a: pytest.fail('active unsupported module reached IR'))
    monkeypatch.setattr(subprocess, 'Popen', lambda *a, **k: pytest.fail('external tool in owned cfg phase'))
    with pytest.raises(RustError, match='glob use declarations'):
        compile_rust(source, rustc_check=checked)


@pytest.mark.integration
@pytest.mark.xdist_group(name='rust-native-reference')
@pytest.mark.pcc_gate(unavailable=ACTUAL_UNAVAILABLE)
@pytest.mark.parametrize('directive,enabled', (
    ('cfg_attr(test, some_unknown_attribute)', False),
    ('cfg_attr(not(test), cfg_attr(not(test), no_std))', True),
    ('cfg_attr(test, cfg_attr(unix, no_std))', False),
))
def test_actual_test_predicate_preserves_nested_crate_cfg_attr_semantics(
        directive, enabled, tmp_path, monkeypatch):
    source = '#![' + directive + ']\n' + BODY
    checked = certify(source, tmp_path / 'actual-check')
    monkeypatch.setattr(subprocess, 'Popen', lambda *a, **k: pytest.fail('external tool in owned cfg phase'))
    parser = Parser(source, rustc_check=checked)
    parser.parse()
    configured, = parser.crate_cfg_attributes
    assert configured.configuration.test_harness is False
    assert configured.enabled is enabled
    assert (parser.crate_policy.no_std is not None) is enabled
    assert source[slice(*configured.syntax.source_span)] == '#![' + directive + ']'
    assert (tmp_path / 'actual-check/original.rs').read_bytes() == source.encode()


@pytest.mark.integration
@pytest.mark.xdist_group(name='rust-native-reference')
@pytest.mark.pcc_gate(unavailable=ACTUAL_UNAVAILABLE)
@pytest.mark.parametrize('payload,message', (
    ('cfg_attr(unix, no_std)', 'unimplemented built-in/target'),
    ('allow(dead_code)', 'active cfg_attr payload'),
))
def test_actual_test_mode_does_not_authorize_unknown_active_cfg_attr_payloads(
        payload, message, tmp_path, monkeypatch):
    source = '#![cfg_attr(not(test), ' + payload + ')]\n' + BODY
    checked = certify(source, tmp_path / 'actual-check')
    from pcc.frontends.rust.lowering import Lowerer
    monkeypatch.setattr(Lowerer, 'lower', lambda *a: pytest.fail('unowned cfg_attr semantics reached IR'))
    monkeypatch.setattr(subprocess, 'Popen', lambda *a, **k: pytest.fail('external tool in owned cfg phase'))
    with pytest.raises(RustError, match=message):
        compile_rust(source, rustc_check=checked)


@pytest.mark.integration
@pytest.mark.xdist_group(name='rust-native-reference')
@pytest.mark.pcc_gate(unavailable=ACTUAL_UNAVAILABLE)
def test_actual_test_build_identity_rejects_command_and_source_substitution(tmp_path, monkeypatch):
    source = '#[cfg(test)]mod omitted{}' + BODY
    checked = certify(source, tmp_path / 'actual-check')
    record = checked.provenance
    commands = [record.command + extra for extra in (
        ('--test',), ('--cfg=test',), ('--cfg', 'test'), ('--crate-type=bin',),
        ('--crate-type=lib',), ('@response-file',),
    )]
    commands += [record.command[:5] + ('--test',) + record.command[6:],
                 record.command[:5] + ('--crate-type=bin',) + record.command[6:],
                 record.command[:7] + ('@source-response',) + record.command[8:]]
    from pcc.frontends.rust.lowering import Lowerer
    monkeypatch.setattr(Lowerer, 'lower', lambda *a: pytest.fail('unbound build mode reached IR'))
    monkeypatch.setattr(subprocess, 'Popen', lambda *a, **k: pytest.fail('external tool in owned cfg phase'))
    for command in commands:
        damaged = copy(checked)
        object.__setattr__(damaged, '_record', replace(record, command=command))
        with pytest.raises(RustError, match='command does not bind'):
            damaged.configuration(source, edition=2021)
        with pytest.raises(RustError, match='command does not bind'):
            Parser(source, rustc_check=damaged).parse()
        with pytest.raises(RustError, match='command does not bind'):
            compile_rust(source, rustc_check=damaged)
    with pytest.raises(RustError, match='unchanged Rust source bytes'):
        checked.configuration(source + '\n', edition=2021)
    with pytest.raises(RustError, match='configuration does not match'):
        checked.configuration(source, edition=2015)
    with pytest.raises(RustError, match='configuration does not match'):
        compile_rust(source, rustc_check=checked, target_triple='arm64-apple-darwin')
    with pytest.raises(RustError, match='cannot both select cfg'):
        compile_rust(source, rustc_check=checked, cfg=CfgOptions(test=True))
    with pytest.raises(RustError, match='caller-constructed external record'):
        compile_rust(source, rustc_check=record)
    damaged = copy(checked)
    object.__setattr__(damaged, '_record', replace(record, cfg=('test',)))
    with pytest.raises(RustError, match='configuration does not match'):
        damaged.configuration(source, edition=2021)
    assert (tmp_path / 'actual-check/original.rs').read_bytes() == source.encode()


@pytest.mark.integration
@pytest.mark.xdist_group(name='rust-native-reference')
@pytest.mark.pcc_gate(unavailable=NATIVE_UNAVAILABLE)
def test_unchanged_test_cfg_program_has_owned_native_and_separate_reference_results(tmp_path, monkeypatch):
    from tests.rust import test_rust_certified_lints as native
    monkeypatch.setattr(native, 'PROGRAM', PROGRAM)
    native.test_unchanged_lint_program_has_owned_native_and_actual_rustc_reference_results(tmp_path, monkeypatch)
