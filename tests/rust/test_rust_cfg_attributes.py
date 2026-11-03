"""Closed authenticated cfg inputs and owned cfg_attr policy, CI execution UNRUN.

Rust Reference conditional-compilation.html (2026-11-03): cfg.option-spec,
cfg.options.other, cfg.predicate and cfg.attr. No Cargo graph or target cfg
context is inferred, and no original source token is rewritten.
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
PROGRAM = Path(__file__).with_name('programs') / 'certified_cfg_attr.rs'


def test_cfg_attr_requires_authenticated_inputs_and_retains_original_syntax():
    source = '#![cfg_attr(not(feature = "std"), no_std)]\n' + BODY
    parser = Parser(source)
    with pytest.raises(RustError, match='cfg_attr requires an actual'):
        parser.parse()
    syntax, = parser.crate_cfg_syntax
    assert source[slice(*syntax.source_span)] == source.splitlines()[0]
    assert syntax.predicate.children[0].value == 'std'
    assert syntax.attributes[0].name == 'no_std'
    assert parser.crate_policy.no_std is None
    with pytest.raises(FrozenInstanceError):
        syntax.attributes = ()
    with pytest.raises(RustError, match='cfg_attr requires an actual'):
        compile_rust(source, experimental_rustc_checked=True)


@pytest.mark.integration
@pytest.mark.xdist_group(name='rust-native-reference')
@pytest.mark.pcc_gate(unavailable=ACTUAL_UNAVAILABLE)
@pytest.mark.parametrize('predicate,enabled', [
    ('not(feature="std")', True), ('feature="std"', False),
    ('all()', True), ('any()', False), ('not(any())', True),
    ('true', True), ('false', False),
    ('all(not(feature="std"),not(feature="other"))', True),
    ('any(feature="std",feature="other")', False),
])
def test_actual_closed_configuration_applies_or_omits_no_std_without_rewriting(
        predicate, enabled, tmp_path, monkeypatch):
    source = '#![cfg_attr(' + predicate + ', no_std)]\n' + BODY
    checked = certify(source, tmp_path / 'actual-check')
    with monkeypatch.context() as guard:
        guard.setattr(subprocess, 'Popen', lambda *a, **k: pytest.fail('external tool in owned cfg phase'))
        parser = Parser(source, rustc_check=checked)
        parser.parse()
        result = compile_rust(source, rustc_check=checked)
        assert result.emit_object().startswith(b'\x7fELF')
    configured, = result.crate_cfg_attributes
    assert configured == parser.crate_cfg_attributes[0]
    assert configured.enabled is enabled
    assert configured.configuration.explicit_cfg == ()
    assert configured.configuration.target_triple == TARGET
    assert configured.configuration.source_sha256 == sha256(source.encode()).hexdigest()
    assert parser.crate_policy.implicit_library_import == ('core' if enabled else 'std')
    assert source[slice(*configured.syntax.source_span)] == source.splitlines()[0]
    assert (tmp_path / 'actual-check/original.rs').read_bytes() == source.encode()


@pytest.mark.integration
@pytest.mark.xdist_group(name='rust-native-reference')
@pytest.mark.pcc_gate(unavailable=ACTUAL_UNAVAILABLE)
@pytest.mark.parametrize('directive,enabled', [
    ('cfg_attr(all(), cfg_attr(not(feature="std"), no_std))', True),
    ('cfg_attr(any(), some_unknown_attribute)', False),
])
def test_actual_nested_and_inactive_payloads_retain_the_original_metadata(directive, enabled, tmp_path):
    source = '#![' + directive + ']\n' + BODY
    checked = certify(source, tmp_path / 'actual-check')
    parser = Parser(source, rustc_check=checked)
    parser.parse()
    assert (parser.crate_policy.no_std is not None) is enabled
    assert source[slice(*parser.crate_cfg_syntax[0].source_span)] == '#![' + directive + ']'
    if not enabled:
        assert parser.crate_cfg_syntax[0].attributes[0].name == 'some_unknown_attribute'


@pytest.mark.integration
@pytest.mark.xdist_group(name='rust-native-reference')
@pytest.mark.pcc_gate(unavailable=ACTUAL_UNAVAILABLE)
@pytest.mark.parametrize('directive,message', [
    ('cfg_attr(unix, no_std)', 'unimplemented built-in/target'),
    ('cfg_attr(all(feature="std", unix), no_std)', 'unimplemented built-in/target'),
    ('cfg_attr(all(), allow(dead_code))', 'active cfg_attr payload'),
])
def test_actual_check_never_waives_unknown_active_attributes_or_target_configuration(
        directive, message, tmp_path, monkeypatch):
    from pcc.frontends.rust.lowering import Lowerer
    source = '#![' + directive + ']\n' + BODY
    checked = certify(source, tmp_path / 'actual-check')
    monkeypatch.setattr(Lowerer, 'lower', lambda *a: pytest.fail('unsupported cfg context reached IR'))
    with pytest.raises(RustError, match=message):
        compile_rust(source, rustc_check=checked)


@pytest.mark.integration
@pytest.mark.xdist_group(name='rust-native-reference')
@pytest.mark.pcc_gate(unavailable=ACTUAL_UNAVAILABLE)
def test_configuration_cannot_change_authenticated_source_edition_target_or_cfg(tmp_path, monkeypatch):
    from pcc.frontends.rust.lowering import Lowerer
    source = PROGRAM.read_text()
    checked = certify(source, tmp_path / 'actual-check')
    monkeypatch.setattr(Lowerer, 'lower', lambda *a: pytest.fail('mismatched cfg evidence reached IR'))
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
def test_unchanged_cfg_attr_program_executes_owned_and_actual_rustc_reference(tmp_path, monkeypatch):
    from tests.rust import test_rust_certified_lints as native
    monkeypatch.setattr(native, 'PROGRAM', PROGRAM)
    native.test_unchanged_lint_program_has_owned_native_and_actual_rustc_reference_results(tmp_path, monkeypatch)
