"""Owned while-let patterns with actual unchanged-input rustc admission.

https://doc.rust-lang.org/reference/expressions/loop-expr.html#while-let-patterns
https://doc.rust-lang.org/reference/patterns.html
These source-authored obligations are UNRUN. The unchanged real crate keeps its
strict project-success gate; no extracted method stands in for that project.
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
from pcc.frontends.rust.syntax import Parser
from tests.rust.analysis_only import analyze_source
from tests.rust.certification_support import actual_check_prerequisite, certify_source


PROGRAMS = (
    'while_let_payload_flow', 'while_let_shadow', 'while_let_or_tuple',
    'while_let_owned_move', 'while_let_generic_grammar',
    'while_let_nested_iteration', 'while_let_unit',
)
MODEL_PROGRAMS = tuple(name for name in PROGRAMS
    if name not in {'while_let_payload_flow', 'while_let_nested_iteration'})


def source(name):
    return Path(__file__).with_name('programs').joinpath(name + '.rs').read_text()


def nodes(expression):
    pending = [expression]
    while pending:
        node = pending.pop()
        yield node
        pending.extend(node.children[1:] if node.kind == 'call' else node.children)


def test_complete_original_project_keeps_while_let_and_earlier_boundaries():
    original = Path(__file__).resolve().parents[2].joinpath(
        'projects/fallible-streaming-iterator-0.1.9/src/lib.rs').read_bytes()
    assert len(original) == 21146
    assert sha256(original).hexdigest() == '7fb7428997bdd89c3ee0818ecee642f8d93882c9c66171c9c6194b732f337089'
    lines = original.splitlines()
    assert lines[68].strip() == b'while let Some(e) = self.next()? {'
    assert lines[49].strip() == b'#[inline]'
    assert lines[50].strip().startswith(b'fn next(&mut self)')


def test_while_let_parser_preserves_complete_block_and_pattern_syntax():
    text = source('while_let_generic_grammar')
    parser = Parser(text)
    functions = parser.parse()
    for name, tail in (('tuple', 'tuple'), ('array', 'array')):
        function = next(function for function in functions if function.token.text == name)
        statement, value = function.body.children
        expression = statement.children[0]
        assert statement.kind == 'statement' and expression.kind == 'while_let'
        assert expression.value.kind == 'literal' and value.kind == tail
        start = offset(parser, expression.token)
        assert text[start:start + len(expression.token.text)] == 'while'


@pytest.mark.parametrize('name', MODEL_PROGRAMS)
def test_while_let_models_share_patterns_body_and_existing_move_fixed_point(name):
    checker = analyze_source(source(name))
    expressions = [node for function in checker.functions for node in nodes(function.body)
                   if node.kind == 'while_let']
    assert expressions and all(node.ty == '()' for node in expressions)
    assert checker.loops == [] and checker.type_scope == {} and not checker.abstract_function


def test_scrutinee_uses_outer_name_while_body_uses_fresh_pattern_binding():
    checker = analyze_source(source('while_let_shadow'))
    function = checker.names['read']
    expression = next(node for node in nodes(function.body) if node.kind == 'while_let')
    outer = checker.parameters[function.identity][0]
    inner = expression.value.children[0].binding
    assert expression.children[0].binding is outer
    assert outer.name == inner.name == 'value' and outer.serial != inner.serial
    assert outer.ty == 'State' and inner.ty == 'u64'
    assert any(node.kind == 'name' and node.binding is inner for node in nodes(expression.children[1]))


@pytest.mark.parametrize('name', PROGRAMS)
def test_every_new_while_let_original_needs_actual_check_before_ir(name, monkeypatch):
    from pcc.frontends.rust.lowering import Lowerer
    monkeypatch.setattr(Lowerer, 'lower', lambda *a: pytest.fail('unchecked while let reached IR'))
    with pytest.raises(RustError, match='actual source/options-bound CheckedRustSource'):
        compile_rust(source(name), experimental_rustc_checked=True)


def test_unused_while_let_is_not_authorized_by_value_only_attestation(monkeypatch):
    from pcc.frontends.rust.lowering import Lowerer
    monkeypatch.setattr(Lowerer, 'lower', lambda *a: pytest.fail('unused unchecked pattern loop reached IR'))
    with pytest.raises(RustError, match='while let control-flow admission'):
        compile_rust('fn unused(){while let false=true{}}fn answer()->u64{42}',
                     experimental_rustc_checked=True)


INVALID = (
    ('payload-arity', 'enum E{More(u64),Done}fn f(e:E){while let E::More(x,y)=e{}}', 'payload pattern shape', 'E0023'),
    ('literal-type', 'fn f(){while let true=1u64{}}', 'type mismatch', 'E0308'),
    ('unit-result', 'fn f()->u64{while let true=false{}}', 'type mismatch', 'E0308'),
    ('body-type', 'fn f(){while let true=false{1u64}}', 'type mismatch', 'E0308'),
    ('duplicate-binding', 'fn f(){while let (x,x)=(1u64,2u64){break;}}', 'duplicate binding in pattern', 'E0416'),
    ('binding-escape', 'fn f(){while let x=1u64{break;}let y=x;}', 'unknown local', 'E0425'),
    ('move-backedge', 'struct R{x:u64}enum E{More(R),Done}fn f(e:E){while let E::More(value)=e{let moved=value;}}', 'moved', 'E0382'),
    ('break-after-scope', 'fn f(){while let true=false{}break;}', 'break outside a loop', 'E0268'),
    ('continue-after-scope', 'fn f(){while let true=false{}continue;}', 'continue outside a loop', 'E0268'),
)


@pytest.mark.parametrize('name,text,message,code', INVALID)
def test_invalid_while_let_obligations_reject_in_owned_model(name, text, message, code):
    with pytest.raises(RustError, match=message):
        analyze_source(text)


CAPABILITY = (
    ('or-bindings', 'enum E{A(u64),B(u64)}fn f(e:E){while let E::A(x)|E::B(x)=e{break;}}', 'bindings inside or-patterns'),
    ('reference-scrutinee', 'fn f(value:&u64){while let item=value{break;}}', 'not reference matching'),
    ('array-scrutinee', 'fn f(){while let _=[1u64]{break;}}', 'array match coverage'),
    ('diverging-scrutinee', 'fn f(){while let _=loop{}{}}', 'diverging while let scrutinees'),
    ('label', "fn f(){'outer:while let true=true{break 'outer;}}", 'unsupported expression'),
)


@pytest.mark.parametrize('name,text,message', CAPABILITY)
def test_remaining_while_let_domains_fail_explicitly(name, text, message):
    with pytest.raises(RustError, match=message):
        analyze_source(text, moves=False)


@pytest.mark.xdist_group(name='rust-native-reference')
@pytest.mark.pcc_gate(unavailable=actual_check_prerequisite())
@pytest.mark.parametrize('name', PROGRAMS)
def test_actual_same_input_while_let_check_precedes_owned_object(name, tmp_path, monkeypatch):
    text = source(name)
    checked = certify_source(text, tmp_path / 'certifier')
    from pcc.frontends.rust.moves import MoveChecker
    from pcc.frontends.rust.lowering import Lowerer
    continue_edges, active_continue = [], []
    original_expression, original_finish = Lowerer.expression, Lowerer.finish_iteration_scopes
    def observe_expression(lowerer, expression):
        if expression.kind != 'continue':
            return original_expression(lowerer, expression)
        active_continue.append(lowerer)
        try:
            return original_expression(lowerer, expression)
        finally:
            active_continue.pop()
    def observe_finish(lowerer, depth):
        if active_continue and active_continue[-1] is lowerer:
            outer, inner = lowerer.loops
            continue_edges.append((depth, len(lowerer.iteration_scopes),
                inner.continue_depth, inner.break_depth, outer.continue_depth, outer.break_depth,
                inner.continue_block.name.startswith('while.let.condition')))
        return original_finish(lowerer, depth)
    def forbidden(*args, **kwargs):
        pytest.fail('external process or partial move/loan proof entered owned phase')
    with monkeypatch.context() as patch:
        patch.setattr(subprocess, 'Popen', forbidden)
        patch.setattr(MoveChecker, 'check', forbidden)
        patch.setattr(MoveChecker, 'check_function', forbidden)
        if name == 'while_let_nested_iteration':
            patch.setattr(Lowerer, 'expression', observe_expression)
            patch.setattr(Lowerer, 'finish_iteration_scopes', observe_finish)
        result = compile_rust(text, rustc_check=checked)
        assert 'while.let.condition' in result.ir_text and 'while.let.end' in result.ir_text
        assert result.emit_object().startswith(b'\x7fELF')
    if name == 'while_let_nested_iteration':
        assert continue_edges == [(2, 2, 2, 2, 1, 0, True)]
        assert not active_continue
    assert result.externally_checked and not result.borrow_checked_by_pcc
    assert (tmp_path / 'certifier' / 'original.rs').read_text() == text


@pytest.mark.xdist_group(name='rust-native-reference')
@pytest.mark.pcc_gate(unavailable=actual_check_prerequisite())
def test_while_let_record_cannot_authorize_changed_source(tmp_path, monkeypatch):
    text = source('while_let_shadow')
    checked = certify_source(text, tmp_path / 'certifier')
    from pcc.frontends.rust.lowering import Lowerer
    monkeypatch.setattr(Lowerer, 'lower', lambda *a: pytest.fail('changed original reached IR'))
    with pytest.raises(RustError, match='unchanged Rust source bytes'):
        compile_rust(text + '\n', rustc_check=checked)


@pytest.mark.xdist_group(name='rust-native-reference')
@pytest.mark.pcc_gate(unavailable=actual_check_prerequisite())
@pytest.mark.parametrize('name,text,message', CAPABILITY)
def test_valid_unsupported_while_let_originals_remain_certified_gaps(name, text, message, tmp_path):
    checked = certify_source(text, tmp_path / name)
    with pytest.raises(RustError, match=message):
        compile_rust(text, rustc_check=checked)


@pytest.mark.integration
@pytest.mark.xdist_group(name='rust-native-reference')
@pytest.mark.pcc_gate(unavailable=actual_check_prerequisite() or (
    None if sys.platform.startswith('linux') and platform.machine() in {'x86_64', 'AMD64'}
    and shutil.which('rustc') and shutil.which('cc') else 'requires Linux native rustc/system-linker reference'))
def test_actual_native_while_let_originals_match_rustc(tmp_path, monkeypatch):
    from tests.rust import test_rust_standard_markers as shared
    monkeypatch.setattr(shared, 'PROGRAMS', PROGRAMS)
    monkeypatch.setattr(shared, 'source', source)
    shared.test_actual_native_marker_originals_match_rustc(tmp_path, monkeypatch)


@pytest.mark.integration
@pytest.mark.xdist_group(name='rust-native-reference')
@pytest.mark.pcc_gate(unavailable=actual_check_prerequisite())
def test_actual_rustc_rejects_exact_invalid_while_let_originals(tmp_path, monkeypatch):
    from tests.rust import test_rust_supertraits as oracle
    monkeypatch.setattr(oracle, 'INVALID', INVALID)
    oracle.test_genuine_rustc_rejects_the_same_invalid_obligations(tmp_path, monkeypatch)
