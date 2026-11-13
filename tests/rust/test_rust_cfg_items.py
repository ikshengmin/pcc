"""Grammar-owned conditional extern-crate items, CI execution UNRUN.

Rust Reference cfg.attr.attribute, cfg.attr.effect, cfg.option-spec and
items.extern-crate.syntax, retrieved 2026-11-03:
https://doc.rust-lang.org/reference/conditional-compilation.html
https://doc.rust-lang.org/reference/items/extern-crates.html

Configuration comes only from the existing genuine unchanged-source checker.
Inactive declarations retain original tokens, spans and syntax; they neither
bind names nor load dependencies. Other conditional item families, target and
custom cfg, feature flags and active external dependencies remain explicit
boundaries. The original whole-crate success requirement is unchanged.
"""

from copy import copy
from dataclasses import FrozenInstanceError, replace
from hashlib import sha256
from pathlib import Path
import subprocess

import pytest

from pcc.frontends.rust import RustError, compile_rust
from pcc.frontends.rust.syntax import Parser
from tests.rust.test_rust_certified_lints import (
    ACTUAL_UNAVAILABLE, NATIVE_UNAVAILABLE, TARGET, certify,
)


BODY = 'pub fn answer()->u64{0xfedcba9876543210}'
PROGRAM = Path(__file__).with_name('programs') / 'certified_cfg_extern_crate.rs'
UPSTREAM_CFG_EXTERN = '#[cfg(feature = "std")]\nextern crate core;\n'


def test_cfg_items_require_genuine_configuration_and_retain_original_attribute():
    source = UPSTREAM_CFG_EXTERN + BODY
    for checked in (None, True):
        parser = Parser(source, source_path='original.rs', rustc_check=checked)
        tokens = tuple(parser.tokens)
        with pytest.raises(RustError, match='original.rs: 1:3: cfg requires an actual'):
            parser.parse()
        syntax, = parser.item_cfg_syntax
        assert source[slice(*syntax.source_span)] == '#[cfg(feature = "std")]'
        assert syntax.predicate.name == 'feature' and syntax.predicate.value == 'std'
        assert tuple(parser.tokens) == tokens
        assert all(a is b for a, b in zip(tokens, parser.tokens))
        assert not parser.conditional_items and not parser.crate_aliases
        with pytest.raises(FrozenInstanceError):
            syntax.predicate = None
    with pytest.raises(RustError, match='cfg requires an actual'):
        compile_rust(source, experimental_rustc_checked=True)


@pytest.mark.parametrize('attribute,message', (
    ('cfg()', 'exactly one'),
    ('cfg(feature="std",feature="other")', 'exactly one'),
    ('cfg(feature=1)', 'literal string'),
    ('cfg(feature=concat!("std"))', 'literal string'),
))
def test_malformed_predicates_are_not_hidden_by_unavailable_configuration(attribute, message):
    with pytest.raises(RustError, match=message):
        Parser('#[' + attribute + ']extern crate core;' + BODY).parse()


@pytest.mark.integration
@pytest.mark.xdist_group(name='rust-native-reference')
@pytest.mark.pcc_gate(unavailable=ACTUAL_UNAVAILABLE)
@pytest.mark.parametrize('predicates,declaration,enabled', (
    (('feature="std"',), 'extern crate core;', False),
    (('feature=r#"std"#',), 'pub extern crate missing_dependency as omitted;', False),
    (('feature="std"',), 'extern crate self as omitted;', False),
    (('not(feature="std")',), 'extern crate self as configured;', True),
    (('all()',), 'pub extern crate self as configured;', True),
    (('any()',), 'extern crate nonexistent;', False),
    (('feature="std"', 'all()'), 'extern crate core;', False),
    (('all()', 'not(feature="std")'), 'extern crate self as configured;', True),
))
def test_actual_conditional_extern_items_preserve_tokens_spans_and_name_effects(
        predicates, declaration, enabled, tmp_path, monkeypatch):
    attrs = ''.join('#[cfg(' + predicate + ')]\n' for predicate in predicates)
    source = '//! Unicode π and CRLF\r\n' + attrs + declaration + '\n' + BODY
    checked = certify(source, tmp_path / 'actual-check')
    with monkeypatch.context() as guard:
        guard.setattr(subprocess, 'Popen', lambda *a, **k: pytest.fail('external tool in owned item configuration'))
        parser = Parser(source, source_path='original.rs', rustc_check=checked)
        tokens = tuple(parser.tokens)
        parser.parse()
        result = compile_rust(source, rustc_check=checked, target_triple=TARGET)
        assert result.emit_object().startswith(b'\x7fELF')
    item, = parser.conditional_items
    assert item.enabled is enabled and result.conditional_items[0].enabled is enabled
    assert len(item.attributes) == len(predicates)
    assert source[slice(*item.source_span)] == attrs + declaration
    assert item.item.token.text == 'extern'
    assert item.item.crate.text == declaration.split('crate ', 1)[1].split()[0].rstrip(';')
    assert all(token is original for token, original in zip(tokens, parser.tokens))
    assert item.tokens == tuple(token for token in tokens
                                if parser.line_offsets[token.line - 1] + token.column - 1 >= item.source_span[0]
                                and parser.line_offsets[token.line - 1] + token.column - 1 < item.source_span[1])
    assert [alias.token.text for alias in parser.crate_aliases] == (['configured'] if enabled else [])
    for attribute in item.attributes:
        assert attribute.configuration.source_sha256 == sha256(source.encode()).hexdigest()
        assert attribute.configuration.edition == 2021
        assert attribute.configuration.target_triple == TARGET
        assert attribute.configuration.explicit_cfg == ()
    assert (tmp_path / 'actual-check/original.rs').read_bytes() == source.encode()
    with pytest.raises(FrozenInstanceError):
        item.item.alias = None
    with pytest.raises(FrozenInstanceError):
        item.attributes = ()


@pytest.mark.integration
@pytest.mark.xdist_group(name='rust-native-reference')
@pytest.mark.pcc_gate(unavailable=ACTUAL_UNAVAILABLE)
@pytest.mark.parametrize('predicate,message', (
    ('unix', 'unimplemented built-in/target'),
    ('target_os="linux"', 'unimplemented built-in/target'),
    ('pcc_custom_cfg', 'unimplemented built-in/target'),
    ('all(feature="std",unix)', 'unimplemented built-in/target'),
))
def test_actual_cfg_never_guesses_target_custom_or_short_circuited_context(
        predicate, message, tmp_path, monkeypatch):
    from pcc.frontends.rust.lowering import Lowerer
    source = '#[cfg(' + predicate + ')]extern crate core;' + BODY
    checked = certify(source, tmp_path / 'actual-check')
    monkeypatch.setattr(Lowerer, 'lower', lambda *a: pytest.fail('unknown configuration reached IR'))
    with pytest.raises(RustError, match=message):
        compile_rust(source, rustc_check=checked)


@pytest.mark.integration
@pytest.mark.xdist_group(name='rust-native-reference')
@pytest.mark.pcc_gate(unavailable=ACTUAL_UNAVAILABLE)
@pytest.mark.parametrize('source,message', (
    ('#[cfg(all())]extern crate core;' + BODY, 'external crate dependency loading'),
    ('#[cfg(all())]extern crate self as _;' + BODY, 'anonymous crate aliases'),
    # Every item kind is now gated; a disabled item is still parsed in full,
    # as rustc strips only parsed items, so its macro body keeps that boundary.
    ('#[cfg(feature="std")]fn omitted(){unimplemented!();}' + BODY, 'macro invocations require'),
))
def test_actual_conditional_items_keep_active_dependency_and_other_item_boundaries(
        source, message, tmp_path, monkeypatch):
    from pcc.frontends.rust.lowering import Lowerer
    checked = certify(source, tmp_path / 'actual-check')
    monkeypatch.setattr(Lowerer, 'lower', lambda *a: pytest.fail('unowned conditional item reached IR'))
    with pytest.raises(RustError, match=message):
        compile_rust(source, rustc_check=checked)


@pytest.mark.integration
@pytest.mark.xdist_group(name='rust-native-reference')
@pytest.mark.pcc_gate(unavailable=ACTUAL_UNAVAILABLE)
def test_actual_disabled_outlined_module_is_dropped_without_its_file(tmp_path):
    # Former boundary row ('#[cfg(feature="std")]mod absent;', 'only for extern crate').
    source = '#[cfg(feature="std")]mod absent;' + BODY
    checked = certify(source, tmp_path / 'actual-check')
    parser = Parser(source, rustc_check=checked)
    parser.parse()
    item, = parser.conditional_items
    assert not item.enabled and not parser.modules
    assert compile_rust(source, rustc_check=checked).externally_checked


@pytest.mark.integration
@pytest.mark.xdist_group(name='rust-native-reference')
@pytest.mark.pcc_gate(unavailable=ACTUAL_UNAVAILABLE)
def test_actual_inline_module_retains_inactive_item_in_original_coordinate_space(tmp_path):
    source = 'mod inner{\n' + UPSTREAM_CFG_EXTERN + '}\n' + BODY
    checked = certify(source, tmp_path / 'actual-check')
    parser = Parser(source, source_path='original.rs', rustc_check=checked)
    parser.parse()
    assert not parser.conditional_items
    body = parser.modules[0].body
    item, = body.conditional_items
    assert not item.enabled and not body.crate_aliases
    assert item.item.token.source_path == 'original.rs' and item.item.token.line == 3
    assert source[slice(*item.source_span)] == UPSTREAM_CFG_EXTERN.rstrip('\n')
    assert item.attributes[0].configuration.source_sha256 == sha256(source.encode()).hexdigest()


@pytest.mark.integration
@pytest.mark.xdist_group(name='rust-native-reference')
@pytest.mark.pcc_gate(unavailable=ACTUAL_UNAVAILABLE)
def test_actual_item_context_rejects_source_edition_target_and_cfg_changes(tmp_path, monkeypatch):
    from pcc.frontends.rust.lowering import Lowerer
    source = UPSTREAM_CFG_EXTERN + BODY
    checked = certify(source, tmp_path / 'actual-check')
    monkeypatch.setattr(Lowerer, 'lower', lambda *a: pytest.fail('mismatched conditional-item context reached IR'))
    for changed, options in ((source + '\n', {}), (source, {'edition': 2015}),
                             (source, {'target_triple': 'arm64-apple-darwin'})):
        with pytest.raises(RustError):
            compile_rust(changed, rustc_check=checked, **options)
    damaged = copy(checked)
    object.__setattr__(damaged, '_record', replace(checked.provenance, cfg=('feature="std"',)))
    with pytest.raises(RustError, match='configuration does not match'):
        compile_rust(source, rustc_check=damaged)


@pytest.mark.integration
@pytest.mark.xdist_group(name='rust-native-reference')
@pytest.mark.pcc_gate(unavailable=NATIVE_UNAVAILABLE)
def test_unchanged_conditional_item_program_executes_owned_and_actual_reference(tmp_path, monkeypatch):
    from tests.rust import test_rust_certified_lints as native
    monkeypatch.setattr(native, 'PROGRAM', PROGRAM)
    native.test_unchanged_lint_program_has_owned_native_and_actual_rustc_reference_results(tmp_path, monkeypatch)
