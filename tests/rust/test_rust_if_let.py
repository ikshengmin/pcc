"""Owned if-let branches with genuine unchanged-input rustc admission.

https://doc.rust-lang.org/reference/expressions/if-expr.html#if-let-patterns
https://doc.rust-lang.org/reference/expressions/match-expr.html
https://doc.rust-lang.org/reference/destructors.html#temporary-scopes
Every authored execution obligation here is UNRUN. The original crate remains
the project acceptance input, not an extracted or rewritten method fixture.
"""
from hashlib import sha256
from pathlib import Path
import platform
import shutil
import subprocess
import sys

import pytest

from pcc.frontends.rust import RustError, compile_rust
from pcc.frontends.rust.attribute_metadata import offset
from pcc.frontends.rust.conditional_patterns import normalize_if_let
from pcc.frontends.rust.syntax import Parser
from tests.rust.analysis_only import analyze_source
from tests.rust.certification_support import actual_check_prerequisite, certify_source


PROGRAMS = (
    'if_let_payloads', 'if_let_shadow', 'if_let_owned_moves', 'if_let_effects',
    'if_let_generics', 'if_let_grammar', 'if_let_patterns', 'if_let_iteration',
    'if_let_modules',
)
MODEL_PROGRAMS = tuple(name for name in PROGRAMS
    if name not in {'if_let_effects', 'if_let_iteration', 'if_let_modules'})


def source(name):
    return Path(__file__).with_name('programs').joinpath(name + '.rs').read_text()


def nodes(expression):
    pending = [expression]
    while pending:
        node = pending.pop()
        yield node
        pending.extend(node.children[1:] if node.kind == 'call' else node.children)


def test_original_project_if_let_inputs_and_earlier_boundaries_are_unchanged():
    original = Path(__file__).resolve().parents[2].joinpath(
        'projects/fallible-streaming-iterator-0.1.9/src/lib.rs').read_bytes()
    assert len(original) == 21146
    assert sha256(original).hexdigest() == '7fb7428997bdd89c3ee0818ecee642f8d93882c9c66171c9c6194b732f337089'
    lines = original.splitlines()
    assert lines[209].strip() == b'if let None = self.get() {'
    assert lines[734].strip() == b'if let None = self.it.next()? {'
    assert lines[865].strip() == b'if let Some(v) = self.it.next()? {'
    assert lines[49].strip() == b'#[inline]'
    assert lines[50].strip().startswith(b'fn next(&mut self)')


def test_if_let_keeps_expression_with_block_statement_and_arm_grammar():
    text = source('if_let_grammar')
    parser = Parser(text)
    functions = parser.parse()
    for name, tail_kind in (('tuple', 'tuple'), ('array', 'array')):
        function = next(function for function in functions if function.token.text == name)
        statement, tail = function.body.children
        expression = statement.children[0]
        assert statement.kind == 'statement' and expression.kind == 'if_let'
        assert expression.value.kind == 'literal' and tail.kind == tail_kind
        start = offset(parser, expression.token)
        assert text[start:start + len(expression.token.text)] == 'if'
    arms = next(function for function in functions if function.token.text == 'arms')
    match = arms.body.children[-1]
    assert [arm.kind for arm in match.children[1:]] == ['if_let', 'if']
    assert match.children[2].children[2].kind == 'if_let'


@pytest.mark.parametrize('text', [
    'fn f(){if let true=false{}}',
    'fn f()->u64{if let true=false{1}else{2}}',
])
def test_normalization_keeps_original_operands_once_and_adds_only_unit_else(text):
    expression = Parser(text).parse()[0].body.children[-1]
    original = tuple(expression.children)
    pattern = expression.value
    normalize_if_let(expression)
    assert expression.kind == 'match'
    assert expression.value[0] is pattern and expression.value[1].kind == 'wildcard'
    assert all(expression.children[index] is node for index, node in enumerate(original))
    assert len(expression.children) == 3
    if len(original) == 2:
        assert expression.children[2].kind == 'unit'


@pytest.mark.parametrize('name', MODEL_PROGRAMS)
def test_owned_if_let_models_reuse_match_type_scope_and_move_owners(name):
    checker = analyze_source(source(name))
    expressions = [node for function in checker.functions for node in nodes(function.body)]
    assert not any(node.kind == 'if_let' for node in expressions)
    assert any(node.kind == 'match' and node.token.text == 'if' for node in expressions)
    assert checker.loops == [] and checker.type_scope == {} and not checker.abstract_function


def test_then_binding_shadows_scrutinee_but_never_captures_the_else_arm():
    checker = analyze_source(source('if_let_shadow'))
    function = checker.names['read']
    expression = function.body.children[-1]
    outer = checker.parameters[function.identity][0]
    inner = expression.value[0].children[0].binding
    assert expression.children[0].binding is outer
    assert outer.name == inner.name == 'value' and outer.serial != inner.serial
    assert outer.ty == 'State' and inner.ty == 'u64'
    assert expression.children[1].children[-1].binding is inner
    else_match = expression.children[2].children[-1]
    assert else_match.children[0].binding is outer


def test_generic_replay_starts_from_original_if_let_and_uses_distinct_bindings():
    checker = analyze_source(source('if_let_generics'))
    raw = checker.function_generics.raw_bodies['identity']
    assert raw.children[-1].kind == 'if_let'
    instances = [function for function in checker.functions
                 if function.declaration_name == 'identity' and function.instance_arguments is not None]
    assert len(instances) == 2
    patterns = [function.body.children[-1].value[0] for function in instances]
    assert all(function.body.children[-1].kind == 'match' for function in instances)
    assert len({pattern.binding.serial for pattern in patterns}) == 2


@pytest.mark.parametrize('name', PROGRAMS)
def test_each_original_requires_actual_same_input_check_before_ir(name, monkeypatch):
    from pcc.frontends.rust.lowering import Lowerer
    monkeypatch.setattr(Lowerer, 'lower', lambda *a: pytest.fail('uncertified if let reached IR'))
    with pytest.raises(RustError, match='actual source/options-bound CheckedRustSource'):
        compile_rust(source(name), experimental_rustc_checked=True)


@pytest.mark.parametrize('text', [
    'fn unused(){if let false=true{}}fn answer()->u64{42}',
    'fn unused<T>(value:T){if let item=value{}}fn answer()->u64{42}',
    'mod m{pub fn unused(){if let false=true{}}}pub fn answer()->u64{42}',
    'fn unused(){if false{if let false=true{}}}',
])
def test_unused_or_nested_if_let_cannot_hide_from_original_source_admission(text, monkeypatch):
    from pcc.frontends.rust.lowering import Lowerer
    monkeypatch.setattr(Lowerer, 'lower', lambda *a: pytest.fail('unused uncertified source reached IR'))
    with pytest.raises(RustError, match='if let control-flow admission'):
        compile_rust(text, experimental_rustc_checked=True)


INVALID = (
    ('payload-arity', 'enum E{More(u64),Done}fn f(e:E){if let E::More(x,y)=e{}}', 'payload pattern shape', 'E0023'),
    ('literal-type', 'fn f(){if let true=1u64{}}', 'type mismatch', 'E0308'),
    ('branch-type', 'fn f()->u64{if let true=false{1u64}else{true}}', 'type mismatch', 'E0308'),
    ('duplicate-binding', 'fn f(){if let (x,x)=(1u64,2u64){}}', 'duplicate binding in pattern', 'E0416'),
    ('then-binding-escape', 'fn f(){if let x=1u64{}let y=x;}', 'unknown local', 'E0425'),
    ('else-binding-escape', 'enum E{More(u64),Done}fn f(e:E)->u64{if let E::More(x)=e{x}else{x}}', 'unknown local', 'E0425'),
    ('conditional-move', 'struct R{x:u64}enum E{More(R),Done}fn f(e:E){if let E::More(x)=e{let moved=x;}let again=e;}', 'moved', 'E0382'),
    ('break-without-loop', 'fn f(){if let true=true{break;}}', 'break outside a loop', 'E0268'),
    ('continue-without-loop', 'fn f(){if let true=true{continue;}}', 'continue outside a loop', 'E0268'),
)


@pytest.mark.parametrize('name,text,message,code', INVALID)
def test_invalid_if_let_obligations_reject_in_the_noncertifying_model(name, text, message, code):
    with pytest.raises(RustError, match=message):
        analyze_source(text)


def test_missing_else_is_unit_even_when_the_pattern_is_irrefutable():
    with pytest.raises(RustError, match='type mismatch'):
        analyze_source('fn f()->u64{if let value=42u64{value}}')


CAPABILITY = (
    ('or-bindings', 'enum E{A(u64),B(u64)}fn f(e:E)->u64{if let E::A(x)|E::B(x)=e{x}else{0}}', 'bindings inside or-patterns'),
    ('reference-scrutinee', 'fn f(value:&u64)->u64{if let item=value{*item}else{0}}', 'not reference matching'),
    ('array-scrutinee', 'fn f()->u64{if let _=[1u64]{42}else{0}}', 'array match coverage'),
    ('diverging-scrutinee', 'fn f(){if let _=loop{}{}}', 'diverging if let scrutinees'),
    ('reference-pattern', 'fn f()->u64{if let ref value=42u64{*value}else{0}}', 'reference, range'),
)


@pytest.mark.parametrize('name,text,message', CAPABILITY)
def test_unimplemented_pattern_domains_stay_explicit(name, text, message):
    with pytest.raises(RustError, match=message):
        analyze_source(text, moves=False)


@pytest.mark.parametrize('text', [
    'fn f(){if let true=true&&false{}}',
    'fn f(){if let true=true||false{}}',
    'fn f(){let mut value=1u64;if let ()=value=2{}}',
])
def test_excluded_ungrouped_conditions_do_not_silently_become_scrutinees(text):
    # Let chains have edition-specific admission. These are parser boundaries,
    # not an assertion that every exact original is invalid in every edition.
    with pytest.raises(RustError, match='condition chains and ungrouped assignment'):
        Parser(text).parse()


@pytest.mark.xdist_group(name='rust-native-reference')
@pytest.mark.pcc_gate(unavailable=actual_check_prerequisite())
@pytest.mark.parametrize('name', PROGRAMS)
def test_actual_same_input_if_let_check_precedes_owned_object(name, tmp_path, monkeypatch):
    text = source(name)
    checked = certify_source(text, tmp_path / 'certifier')
    from pcc.frontends.rust.moves import MoveChecker
    from pcc.frontends.rust.lowering import Lowerer
    exits = []
    original = Lowerer.expression
    def observe_expression(lowerer, expression):
        if expression.kind in {'break', 'continue'} and name == 'if_let_iteration':
            target, = lowerer.loops
            exits.append((expression.kind, len(lowerer.iteration_scopes),
                target.continue_depth, target.break_depth))
        return original(lowerer, expression)
    def forbidden(*args, **kwargs):
        pytest.fail('external process or partial move/loan proof entered owned phase')
    with monkeypatch.context() as patch:
        patch.setattr(subprocess, 'Popen', forbidden)
        patch.setattr(MoveChecker, 'check', forbidden)
        patch.setattr(MoveChecker, 'check_function', forbidden)
        patch.setattr(Lowerer, 'expression', observe_expression)
        result = compile_rust(text, rustc_check=checked)
        assert 'match.arm' in result.ir_text and 'match.end' in result.ir_text
        assert result.emit_object().startswith(b'\x7fELF')
    if name == 'if_let_iteration':
        assert exits == [('break', 2, 1, 0), ('continue', 2, 1, 0)]
    assert result.externally_checked and not result.borrow_checked_by_pcc
    assert (tmp_path / 'certifier' / 'original.rs').read_text() == text


@pytest.mark.xdist_group(name='rust-native-reference')
@pytest.mark.pcc_gate(unavailable=actual_check_prerequisite())
def test_if_let_token_cannot_authorize_changed_source_or_edition(tmp_path, monkeypatch):
    text = source('if_let_payloads')
    checked = certify_source(text, tmp_path / 'certifier')
    from pcc.frontends.rust.lowering import Lowerer
    monkeypatch.setattr(Lowerer, 'lower', lambda *a: pytest.fail('changed original reached IR'))
    with pytest.raises(RustError, match='unchanged Rust source bytes'):
        compile_rust(text.replace('State::More(40)', 'State::More(41)'), rustc_check=checked)
    with pytest.raises(RustError):
        compile_rust(text, rustc_check=checked, edition=2015)


@pytest.mark.xdist_group(name='rust-native-reference')
@pytest.mark.pcc_gate(unavailable=actual_check_prerequisite())
@pytest.mark.parametrize('edition', [2015, 2018, 2021, 2024])
def test_each_admitted_edition_requires_its_own_actual_source_check(edition, tmp_path, monkeypatch):
    from pcc.frontends.rust import check_rust_source
    from tests.rust.test_rust_certified_references import toolchain
    from pcc.frontends.rust.moves import MoveChecker
    text = source('if_let_payloads')
    checked = check_rust_source(text, toolchain=toolchain(),
        evidence_directory=tmp_path / 'certifier', edition=edition)
    def forbidden(*args, **kwargs):
        pytest.fail('external process or partial move/loan proof entered owned phase')
    with monkeypatch.context() as patch:
        patch.setattr(subprocess, 'Popen', forbidden)
        patch.setattr(MoveChecker, 'check', forbidden)
        patch.setattr(MoveChecker, 'check_function', forbidden)
        result = compile_rust(text, rustc_check=checked, edition=edition)
        assert result.emit_object().startswith(b'\x7fELF')
    assert result.edition == edition and result.externally_checked
    assert (tmp_path / 'certifier' / 'original.rs').read_text() == text


@pytest.mark.xdist_group(name='rust-native-reference')
@pytest.mark.pcc_gate(unavailable=actual_check_prerequisite())
def test_valid_edition_2024_chain_remains_an_explicit_capability_gap(tmp_path):
    from pcc.frontends.rust import check_rust_source
    from tests.rust.test_rust_certified_references import toolchain
    text = 'fn f(){if let true=true&&false{}}'
    checked = check_rust_source(text, toolchain=toolchain(),
        evidence_directory=tmp_path / 'certifier', edition=2024)
    with pytest.raises(RustError, match='condition chains and ungrouped assignment'):
        compile_rust(text, rustc_check=checked, edition=2024)


@pytest.mark.xdist_group(name='rust-native-reference')
@pytest.mark.pcc_gate(unavailable=actual_check_prerequisite())
@pytest.mark.parametrize('name,text,message', CAPABILITY)
def test_valid_unsupported_if_let_originals_remain_certified_gaps(name, text, message, tmp_path):
    checked = certify_source(text, tmp_path / name)
    with pytest.raises(RustError, match=message):
        compile_rust(text, rustc_check=checked)


@pytest.mark.integration
@pytest.mark.xdist_group(name='rust-native-reference')
@pytest.mark.pcc_gate(unavailable=actual_check_prerequisite() or (
    None if sys.platform.startswith('linux') and platform.machine() in {'x86_64', 'AMD64'}
    and shutil.which('rustc') and shutil.which('cc') else 'requires Linux native rustc/system-linker reference'))
def test_actual_native_if_let_originals_match_rustc(tmp_path, monkeypatch):
    from tests.rust import test_rust_standard_markers as shared
    monkeypatch.setattr(shared, 'PROGRAMS', PROGRAMS)
    monkeypatch.setattr(shared, 'source', source)
    shared.test_actual_native_marker_originals_match_rustc(tmp_path, monkeypatch)


@pytest.mark.integration
@pytest.mark.xdist_group(name='rust-native-reference')
@pytest.mark.pcc_gate(unavailable=actual_check_prerequisite())
def test_actual_rustc_rejects_exact_invalid_if_let_originals(tmp_path, monkeypatch):
    from tests.rust import test_rust_supertraits as oracle
    monkeypatch.setattr(oracle, 'INVALID', INVALID)
    oracle.test_genuine_rustc_rejects_the_same_invalid_obligations(tmp_path, monkeypatch)
