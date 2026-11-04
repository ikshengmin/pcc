"""Retained inline hints with genuine unchanged-input rustc admission.

https://doc.rust-lang.org/reference/attributes/codegen.html#the-inline-attribute
https://doc.rust-lang.org/stable/error_codes/E0539.html
Hints are advisory, not an inlining implementation. Every authored execution
obligation in this new module is UNRUN until its separately authorized lane runs.
The real project's strict whole-original success gate is left unchanged.
"""
from hashlib import sha256
from pathlib import Path
import platform
import shutil
import subprocess
import sys

import pytest

from pcc.frontends.rust import RustError, compile_rust
from pcc.frontends.rust.inline_attributes import collect_inline_hints
from pcc.frontends.rust.syntax import Parser
from tests.rust.analysis_only import analyze_source
from tests.rust.certification_support import actual_check_prerequisite, certify_source


PROGRAMS = (
    'inline_free_hints', 'inline_method_hints', 'inline_duplicates',
    'inline_module_generics', 'inline_exported', 'inline_reference',
)
MODEL_PROGRAMS = tuple(name for name in PROGRAMS
    if name not in {'inline_module_generics', 'inline_reference'})
FOREIGN_DECLARATION = 'extern "C"{#[inline(always)]fn external();}pub fn answer()->u64{42}'
HINT_FORMS = (
    ('hint', '#[inline]'), ('always', '#[inline(always)]'),
    ('never', '#[inline(never)]'), ('always', '#[inline(always,)]'),
)


def source(name):
    return Path(__file__).with_name('programs').joinpath(name + '.rs').read_text()


def parsed_hints(text):
    parser = Parser(text)
    functions = parser.parse()
    return parser, functions, collect_inline_hints(functions, parser.traits)


def test_original_project_inline_before_default_methods_remains_unchanged():
    original = Path(__file__).resolve().parents[2].joinpath(
        'projects/fallible-streaming-iterator-0.1.9/src/lib.rs').read_bytes()
    assert len(original) == 21146
    assert sha256(original).hexdigest() == '7fb7428997bdd89c3ee0818ecee642f8d93882c9c66171c9c6194b732f337089'
    lines = original.splitlines()
    assert lines[49].strip() == b'#[inline]'
    assert lines[50].strip() == b'fn next(&mut self) -> Result<Option<&Self::Item>, Self::Error> {'
    assert lines[56].strip() == b'#[inline]'
    # No extracted/sanitized project method or expected-failure project gate.


@pytest.mark.parametrize('mode,spelling', HINT_FORMS)
def test_supported_hint_forms_preserve_exact_original_offsets(mode, spelling):
    text = '/// café\r\n' + spelling + '\r\nfn f()->u64{42}'
    parser, functions, hints = parsed_hints(text)
    function, hint = functions[0], hints[0]
    attribute = function.inline_attributes[0]
    assert attribute.mode == hint.selected_hint == mode
    assert text[slice(*attribute.source_span)] == spelling
    assert (attribute.token.line, attribute.token.column) == (2, 3)
    assert hint.ignored_reason is None and not hint.applied_by_owned_codegen
    assert function.documentation[0].text == ' café'
    assert parser.source == text


def test_duplicate_hints_keep_order_first_selection_and_interleaved_docs():
    text = source('inline_duplicates')
    _, functions, hints = parsed_hints(text)
    function = functions[0]
    assert [a.mode for a in function.inline_attributes] == ['never', 'always', 'hint']
    assert hints[0].selected_hint == 'never'
    assert [d.text.strip() for d in function.documentation] == [
        'Documentation may appear between repeated hints.', 'The first inline hint wins.']
    assert [text[slice(*a.source_span)] for a in function.inline_attributes] == [
        '#[inline(never)]', '#[inline(always)]', '#[inline]']


def test_required_trait_hint_never_becomes_an_implementation_hint():
    _, _, hints = parsed_hints(source('inline_method_hints'))
    by_name = {hint.declaration: hint for hint in hints}
    required, provided = by_name['<Self as Read>::read'], by_name['<R as Read>::read']
    assert required.attributes[0].mode == 'always'
    assert required.selected_hint is None and required.ignored_reason == 'no_body'
    assert provided.selected_hint == 'never' and provided.ignored_reason is None
    plain = 'trait T{#[inline(always)]fn f(self);}struct R{x:u64}impl T for R{fn f(self){}}'
    _, functions, hints = parsed_hints(plain)
    assert len(hints) == 1 and hints[0].ignored_reason == 'no_body'
    assert functions[0].inline_attributes == ()


def test_exported_definitions_and_foreign_declarations_retain_ignored_hints():
    _, _, hints = parsed_hints(source('inline_exported'))
    assert hints[0].selected_hint is None and hints[0].ignored_reason == 'externally_exported'
    _, _, hints = parsed_hints(FOREIGN_DECLARATION)
    assert hints[0].selected_hint is None and hints[0].ignored_reason == 'no_body'
    assert hints[0].attributes[0].mode == 'always'


@pytest.mark.parametrize('name', MODEL_PROGRAMS)
def test_hint_metadata_leaves_existing_type_and_move_owners_in_charge(name):
    checker = analyze_source(source(name))
    assert checker.type_scope == {} and not checker.abstract_function
    assert any(function.inline_attributes for function in checker.functions)


def test_generic_instances_preserve_the_declarations_original_attribute_tuple():
    checker = analyze_source(source('inline_free_hints'))
    declaration = checker.names['identity']
    instances = [function for function in checker.functions
                 if function.declaration_name == 'identity' and function.instance_arguments is not None]
    assert instances
    assert all(function.inline_attributes is declaration.inline_attributes for function in instances)
    assert declaration.inline_attributes[0].mode == 'always'


@pytest.mark.parametrize('text', [*(source(name) for name in PROGRAMS), FOREIGN_DECLARATION,
    '#[inline]fn unused(){}fn answer()->u64{42}', 'trait T{#[inline]fn unused(self);}'])
def test_every_inline_context_needs_actual_certification_before_ir(text, monkeypatch):
    from pcc.frontends.rust.lowering import Lowerer
    monkeypatch.setattr(Lowerer, 'lower', lambda *a: pytest.fail('uncertified inline source reached IR'))
    with pytest.raises(RustError, match='actual source/options-bound CheckedRustSource'):
        compile_rust(text, experimental_rustc_checked=True)


def test_metadata_descriptors_are_not_certification_tokens(monkeypatch):
    from pcc.frontends.rust.lowering import Lowerer
    text = source('inline_free_hints')
    _, _, hints = parsed_hints(text)
    monkeypatch.setattr(Lowerer, 'lower', lambda *a: pytest.fail('metadata authorized IR'))
    with pytest.raises(RustError, match='caller-constructed external record'):
        compile_rust(text, rustc_check=hints[0])


INVALID = (
    ('unknown', '#[inline(unknown)]fn f(){}', 'inline requires', 'E0539'),
    ('empty', '#[inline()]fn f(){}', 'inline requires', 'E0805'),
    ('multiple', '#[inline(always,never)]fn f(){}', 'inline requires', 'E0805'),
    ('required-method', 'trait T{#[inline(unknown)]fn f(self);}', 'inline requires', 'E0539'),
    ('provided-method', 'struct R{x:u64}impl R{#[inline(unknown)]fn f(self){}}', 'inline requires', 'E0539'),
)


@pytest.mark.parametrize('name,text,message,code', INVALID)
def test_malformed_inline_metadata_fails_in_its_original_location(name, text, message, code):
    with pytest.raises(RustError, match=message):
        Parser(text).parse()


@pytest.mark.parametrize('text,message', [
    ('#[inline="always"]fn f(){}', 'name-value metadata is not implemented'),
    ('#[inline(always="yes")]fn f(){}', 'nested inline metadata is not implemented'),
    ('#[inline(always())]fn f(){}', 'nested inline metadata is not implemented'),
])
def test_noncanonical_metadata_stays_an_explicit_unqualified_parser_boundary(text, message):
    # rustc 1.91.1's converter has separate lint and permissive nested-meta
    # paths. These exact originals are not asserted to be Rust hard errors,
    # and are not counted as actual reference negatives without execution.
    with pytest.raises(RustError, match=message):
        Parser(text).parse()


@pytest.mark.parametrize('text', [
    '#[inline]struct R{x:u64}', '#[inline]enum E{A}', '#[inline]trait T{}',
    '#[inline]impl R{}', '#[inline]mod m{}', '#[inline]use a::f;',
    '#[inline]extern "C"{}', '#[inline]extern crate self as mine;',
    'trait T{#[inline]type Item;}', 'trait T{type Item;}struct R{x:u64}impl T for R{#[inline]type Item=u64;}',
])
def test_non_function_placements_fail_without_silently_dropping_the_attribute(text):
    with pytest.raises(RustError, match='target or qualifier is not implemented'):
        Parser(text).parse()


@pytest.mark.parametrize('text', [
    '#[inline]', '#[inline]unsafe', '#[inline]extern', '#[inline]extern "C"',
    'trait T{#', 'trait T{#[', 'impl R{#', 'impl R{#[', 'extern "C"{#',
])
def test_truncated_inline_targets_and_member_attributes_have_owned_errors(text):
    with pytest.raises(RustError):
        Parser(text).parse()


CAPABILITY = (
    # The exact former default-body control is preserved as an actual-checked
    # positive in test_rust_default_methods, not discarded or rewritten.
    ('const-function', '#[inline]const fn f()->u64{42}', 'target or qualifier is not implemented'),
    ('closure-statement', 'fn f(){#[inline]||{};}', 'unsupported'),
    ('cold', '#[cold]#[inline]fn f(){}', 'unsupported Rust attribute'),
)


@pytest.mark.parametrize('name,text,message', CAPABILITY)
def test_other_codegen_forms_remain_explicit_gaps(name, text, message):
    with pytest.raises(RustError, match=message):
        Parser(text).parse()


@pytest.mark.xdist_group(name='rust-native-reference')
@pytest.mark.pcc_gate(unavailable=actual_check_prerequisite())
@pytest.mark.parametrize('name,text', [(name, source(name)) for name in PROGRAMS]
    + [('foreign-declaration', FOREIGN_DECLARATION)]
    + [('form-' + str(index), spelling + 'fn f()->u64{42}')
       for index, (_, spelling) in enumerate(HINT_FORMS)])
def test_actual_same_input_inline_check_precedes_owned_object(name, text, tmp_path, monkeypatch):
    checked = certify_source(text, tmp_path / 'certifier')
    from pcc.frontends.rust.moves import MoveChecker
    def forbidden(*args, **kwargs):
        pytest.fail('external process or partial move/loan proof entered owned phase')
    with monkeypatch.context() as patch:
        patch.setattr(subprocess, 'Popen', forbidden)
        patch.setattr(MoveChecker, 'check', forbidden)
        patch.setattr(MoveChecker, 'check_function', forbidden)
        result = compile_rust(text, rustc_check=checked)
        assert result.emit_object().startswith(b'\x7fELF')
    hints = result.function_inline_hints
    assert hints and all(not hint.applied_by_owned_codegen for hint in hints)
    assert 'alwaysinline' not in result.ir_text and 'noinline' not in result.ir_text
    for hint in hints:
        for attribute in hint.attributes:
            assert text[slice(*attribute.source_span)].startswith('#[inline')
    if name == 'inline_module_generics':
        assert len(hints) == 3 and len({hint.declaration for hint in hints}) == 3
    assert result.externally_checked and not result.borrow_checked_by_pcc
    assert result.external_rustc_lints.stderr == checked.stderr
    assert (tmp_path / 'certifier' / 'original.rs').read_text() == text


@pytest.mark.xdist_group(name='rust-native-reference')
@pytest.mark.pcc_gate(unavailable=actual_check_prerequisite())
def test_inline_token_cannot_authorize_changed_hint_bytes_or_edition(tmp_path, monkeypatch):
    text = source('inline_free_hints')
    checked = certify_source(text, tmp_path / 'certifier')
    from pcc.frontends.rust.lowering import Lowerer
    monkeypatch.setattr(Lowerer, 'lower', lambda *a: pytest.fail('changed source/options reached IR'))
    with pytest.raises(RustError, match='unchanged Rust source bytes'):
        compile_rust(text.replace('#[inline(never)]', '#[inline(always)]'), rustc_check=checked)
    with pytest.raises(RustError):
        compile_rust(text, rustc_check=checked, edition=2015)


@pytest.mark.xdist_group(name='rust-native-reference')
@pytest.mark.pcc_gate(unavailable=actual_check_prerequisite())
@pytest.mark.parametrize('name,text,message', CAPABILITY)
def test_valid_unsupported_inline_originals_remain_certified_gaps(name, text, message, tmp_path):
    checked = certify_source(text, tmp_path / name)
    with pytest.raises(RustError, match=message):
        compile_rust(text, rustc_check=checked)


@pytest.mark.integration
@pytest.mark.xdist_group(name='rust-native-reference')
@pytest.mark.pcc_gate(unavailable=actual_check_prerequisite() or (
    None if sys.platform.startswith('linux') and platform.machine() in {'x86_64', 'AMD64'}
    and shutil.which('rustc') and shutil.which('cc') else 'requires Linux native rustc/system-linker reference'))
def test_actual_native_inline_originals_match_rustc(tmp_path, monkeypatch):
    from tests.rust import test_rust_standard_markers as shared
    monkeypatch.setattr(shared, 'PROGRAMS', PROGRAMS)
    monkeypatch.setattr(shared, 'source', source)
    shared.test_actual_native_marker_originals_match_rustc(tmp_path, monkeypatch)


@pytest.mark.integration
@pytest.mark.xdist_group(name='rust-native-reference')
@pytest.mark.pcc_gate(unavailable=actual_check_prerequisite())
def test_actual_rustc_rejects_exact_invalid_inline_originals(tmp_path, monkeypatch):
    from tests.rust import test_rust_supertraits as oracle
    monkeypatch.setattr(oracle, 'INVALID', INVALID)
    oracle.test_genuine_rustc_rejects_the_same_invalid_obligations(tmp_path, monkeypatch)
