"""Tool-scoped crate lint attributes: `#![deny(clippy::print_stdout)]`.

https://doc.rust-lang.org/reference/attributes/diagnostics.html#tool-lint-attributes
A scoped lint `tool::lint` names a lint of an external tool. rustc 1.92.0
accepts the tools clippy, rustdoc, diagnostic, rustfmt, miri, rust_analyzer and
rustc in every edition, rejects any other tool (E0710) and a valued scoped lint
(E0452), and evaluates none of these lints while compiling: their names are
not validated and forbid/allow conflicts between them are not errors. The
attributes are therefore retained as syntax with no compile effect; builtin
lints keep requiring an actual checked source. Each case below was checked
against local rustc 1.92.0.
"""
import platform
import shutil
import sys

import pytest

from pcc.frontends.rust import RustError, compile_rust
from pcc.frontends.rust.attribute_metadata import LINT_TOOLS
from pcc.frontends.rust.syntax import Parser
from tests.rust.test_rust_frontend import test_development_only_rustc_checks_the_same_source as _rustc
from tests.rust.test_rust_patterns import test_original_match_fixture_full_width_owned_execution as _execute
from tests.rust.test_rust_targets import test_original_scalar_programs_cross_emit_arm64_macho_without_source_rewrite as _cross

BODY = ' pub fn f()->u64{1}'


@pytest.mark.parametrize('attributes', [
    '#![deny(clippy::print_stdout, clippy::print_stderr)]',
    '#![deny(clippy::nonexistent_lint)]',
    '#![deny(clippy::a::b)]',
    '#![forbid(clippy::x)] #![allow(clippy::x)]',
    '#![warn(' + ', '.join(tool + '::x' for tool in sorted(LINT_TOOLS)) + ',)]',
])
def test_known_tool_lints_compile_without_a_checked_source(attributes):
    assert 'define' in compile_rust(attributes + BODY, experimental_rustc_checked=True).ir_text


def test_tool_lints_are_retained_as_original_syntax():
    source = '#![deny(clippy::print_stdout, clippy::print_stderr)]' + BODY
    parser = Parser(source)
    parser.parse()
    lint, = parser.crate_lint_attributes
    assert lint.level == 'deny' and lint.builtin_lints == ()
    assert [(item.name, item.token.text, item.token.column) for item in lint.lints] == [
        ('clippy::print_stdout', 'clippy', 9), ('clippy::print_stderr', 'clippy', 31)]
    assert source[slice(*lint.source_span)] == '#![deny(clippy::print_stdout, clippy::print_stderr)]'
    assert [source[slice(*item.source_span)] for item in lint.lints] == [
        'clippy::print_stdout', 'clippy::print_stderr']


@pytest.mark.parametrize('attributes,message', [
    # E0710: unknown tool name `foo` found in scoped lint: `foo::x`
    ('#![deny(foo::x)]', "unknown tool name 'foo'"),
    ('#![deny(core::x)]', "unknown tool name 'core'"),
    # E0452: malformed lint attribute input
    ('#![deny(clippy::print_stdout = "x")]', 'malformed lint attribute input'),
    # Builtin lints beside tool lints keep their checked-source requirement.
    ('#![deny(clippy::print_stdout, missing_docs)]', 'missing_docs lint semantics require an actual'),
    # Accepted by rustc (a bare `clippy` is an unknown builtin lint, a warning;
    # reasons are metadata) but not implemented here.
    ('#![deny(clippy)]', 'only missing_docs word metadata and tool-scoped lints'),
    ('#![deny(clippy::print_stdout, reason = "r")]', 'only missing_docs word metadata and tool-scoped lints'),
])
def test_unknown_tools_malformed_and_unowned_lint_forms_are_explicit(attributes, message):
    with pytest.raises(RustError, match=message):
        compile_rust(attributes + BODY, experimental_rustc_checked=True)


PROGRAMS = ['tool_lints']


@pytest.mark.xdist_group(name='rust-native-reference')
@pytest.mark.pcc_gate(unavailable=None if sys.platform.startswith('linux') and platform.machine() in {'x86_64', 'AMD64'} else 'requires Linux x86-64 owned execution')
@pytest.mark.parametrize('name', PROGRAMS)
def test_tool_lint_fixture_owned_execution(name, tmp_path, monkeypatch):
    _execute(name, tmp_path, monkeypatch)


@pytest.mark.xdist_group(name='rust-native-reference')
@pytest.mark.parametrize('name', PROGRAMS)
def test_tool_lint_fixture_cross_emits_darwin(name, monkeypatch):
    _cross(name, monkeypatch)


@pytest.mark.xdist_group(name='rust-native-reference')
@pytest.mark.pcc_gate(unavailable=None if shutil.which('rustc') else 'development-only rustc is not installed')
@pytest.mark.parametrize('name', PROGRAMS)
def test_development_only_rustc_checks_tool_lint_fixture(name, tmp_path):
    _rustc(name, tmp_path)
