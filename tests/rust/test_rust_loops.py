"""Unlabeled unit/diverging loops, with actual same-original certification.

https://doc.rust-lang.org/reference/expressions/loop-expr.html
The retained project uses a loop at src/lib.rs:129. Its earlier attribute,
default-method, library and reference-return boundaries remain independent.
All newly authored checks are execution-UNRUN until the explicit CI lanes run.
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
from pcc.frontends.rust.semantics import NEVER
from pcc.frontends.rust.syntax import Parser
from tests.rust.analysis_only import analyze_source
from tests.rust.certification_support import actual_check_prerequisite, certify_source


PROGRAMS = (
    'loop_counter', 'loop_nested', 'loop_diverging', 'loop_unconditional_moves',
    'loop_reference', 'loop_array_iteration', 'loop_statement_grammar',
    'loop_generic_return', 'loop_array_continue',
)
MODEL_PROGRAMS = tuple(name for name in PROGRAMS
                       if name not in {'loop_reference', 'loop_array_iteration', 'loop_array_continue'})


def source(name):
    return Path(__file__).with_name('programs').joinpath(name + '.rs').read_text()


def expression_nodes(expression):
    pending = [expression]
    while pending:
        node = pending.pop()
        yield node
        pending.extend(node.children[1:] if node.kind == 'call' else node.children)


def test_original_project_loop_is_preserved_without_extracting_a_compile_fixture():
    original = Path(__file__).resolve().parents[2].joinpath(
        'projects/fallible-streaming-iterator-0.1.9/src/lib.rs').read_bytes()
    assert len(original) == 21146
    assert sha256(original).hexdigest() == '7fb7428997bdd89c3ee0818ecee642f8d93882c9c66171c9c6194b732f337089'
    lines = original.splitlines()
    assert lines[128].strip() == b'loop {' and lines[133].strip() == b'break;'
    assert lines[49].strip() == b'#[inline]'
    # Existing test_fallible_streaming_iterator_project remains the strict
    # whole-input PCC-success obligation. No expected failure replaces it.


def test_loop_parser_preserves_source_and_complete_block_boundaries():
    text = source('loop_statement_grammar')
    parser = Parser(text)
    functions = parser.parse()
    tuple_function = next(function for function in functions if function.token.text == 'tuple')
    array_function = next(function for function in functions if function.token.text == 'array')
    for function, tail in ((tuple_function, 'tuple'), (array_function, 'array')):
        statement, value = function.body.children
        assert statement.kind == 'statement' and statement.children[0].kind == 'loop'
        assert value.kind == tail
    loops = [node for function in functions for node in expression_nodes(function.body)
             if node.kind == 'loop']
    assert len(loops) == 5
    for node in loops:
        start = offset(parser, node.token)
        assert text[start:start + len(node.token.text)] == 'loop'


@pytest.mark.parametrize('name', MODEL_PROGRAMS)
def test_loop_semantic_model_reuses_existing_body_and_move_owners(name):
    checker = analyze_source(source(name))
    assert checker.loops == [] and checker.type_scope == {}
    assert not checker.abstract_function
    assert any(node.kind == 'loop' for function in checker.functions
               for node in expression_nodes(function.body))


def test_only_associated_breaks_turn_a_loop_from_never_into_unit():
    checker = analyze_source('fn forever()->u64{loop{while true{break;}loop{break;}}}'
                             'fn unit(){loop{break;}}')
    forever = checker.names['forever'].body.children[-1]
    unit = checker.names['unit'].body.children[-1]
    assert forever.kind == unit.kind == 'loop'
    assert forever.ty == NEVER and unit.ty == '()'
    assert checker.loops == []


def test_move_fixed_point_has_no_reachable_exit_without_an_associated_break():
    from pcc.frontends.rust.moves import MoveChecker
    checker = analyze_source(source('loop_diverging'))
    for name in ('forever', 'nested_forever'):
        expression = checker.names[name].body.children[-1]
        assert expression.kind == 'loop' and expression.ty == NEVER
        moves = MoveChecker(checker)
        # An unreachable exit is None, not a reachable empty state {}. The
        # nested_forever original also prevents attributing an inner break
        # to the enclosing loop's fixed point.
        assert moves.expression(expression, {}) is None
        assert moves.loops == []
    once = analyze_source(source('loop_statement_grammar'))
    expression = once.names['tuple'].body.children[0].children[0]
    assert expression.kind == 'loop' and expression.ty == '()'
    assert MoveChecker(once).expression(expression, {}) == {}


@pytest.mark.parametrize('name', PROGRAMS)
def test_every_new_loop_original_requires_actual_check_before_ir(name, monkeypatch):
    from pcc.frontends.rust.lowering import Lowerer
    monkeypatch.setattr(Lowerer, 'lower', lambda *a: pytest.fail('uncertified loop reached IR'))
    with pytest.raises(RustError, match='actual source/options-bound CheckedRustSource'):
        compile_rust(source(name), experimental_rustc_checked=True)


def test_even_an_unused_loop_cannot_enter_through_caller_attestation(monkeypatch):
    from pcc.frontends.rust.lowering import Lowerer
    monkeypatch.setattr(Lowerer, 'lower', lambda *a: pytest.fail('unchecked unused loop reached IR'))
    with pytest.raises(RustError, match='loop control-flow admission'):
        compile_rust('fn unused()->u64{loop{}}fn answer()->u64{42}',
                     experimental_rustc_checked=True)


INVALID = (
    ('body-type', 'fn f(){loop{42u64}}', 'type mismatch', 'E0308'),
    ('unit-result', 'fn f()->u64{loop{break;}}', 'type mismatch', 'E0308'),
    ('break-after-scope', 'fn f(){loop{break;}break;}', 'break outside a loop', 'E0268'),
    ('continue-after-scope', 'fn f(){loop{break;}continue;}', 'continue outside a loop', 'E0268'),
    ('local-escape', 'fn f(){loop{let x=1u64;break;}let y=x;}', 'unknown local', 'E0425'),
    ('move-backedge', 'struct R{x:u64}fn f(value:R){loop{let moved=value;}}', 'moved', 'E0382'),
    ('move-exit', 'struct R{x:u64}fn f(value:R)->R{loop{let moved=value;break;}value}', 'moved', 'E0382'),
    ('move-continue', 'struct R{x:u64}fn f(value:R){loop{let moved=value;continue;}}', 'moved', 'E0382'),
)


@pytest.mark.parametrize('name,text,message,code', INVALID)
def test_invalid_loop_types_scopes_and_move_edges_reject_in_owned_model(name, text, message, code):
    with pytest.raises(RustError, match=message):
        analyze_source(text)


CAPABILITY = (
    ('break-value', 'fn f()->u64{loop{break 42;}}', 'break values'),
    ('explicit-unit-break', 'fn f(){loop{break ();}}', 'break values'),
    ('label', "fn f(){'outer:loop{break 'outer;}}", 'unsupported expression'),
    ('diverging-let', 'fn f(){let value=loop{};}', 'diverging let initializers'),
)


@pytest.mark.parametrize('name,text,message', CAPABILITY)
def test_value_break_labels_and_diverging_bindings_remain_explicit(name, text, message):
    with pytest.raises(RustError, match=message):
        analyze_source(text)


@pytest.mark.xdist_group(name='rust-native-reference')
@pytest.mark.pcc_gate(unavailable=actual_check_prerequisite())
@pytest.mark.parametrize('name', PROGRAMS)
def test_actual_same_input_loop_check_precedes_owned_object_phase(name, tmp_path, monkeypatch):
    text = source(name)
    checked = certify_source(text, tmp_path / 'certifier')
    from pcc.frontends.rust.moves import MoveChecker
    from pcc.frontends.rust.lowering import Lowerer
    continue_edges, active_continue = [], []
    original_expression = Lowerer.expression
    original_finish = Lowerer.finish_iteration_scopes
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
            continue_edges.append((
                depth, len(lowerer.iteration_scopes),
                inner.continue_depth, inner.break_depth,
                outer.continue_depth, outer.break_depth,
                tuple(scope.cursor is not None for scope in lowerer.iteration_scopes),
            ))
        return original_finish(lowerer, depth)
    def forbidden(*args, **kwargs):
        pytest.fail('external process or partial move/loan admission in owned phase')
    with monkeypatch.context() as patch:
        patch.setattr(subprocess, 'Popen', forbidden)
        patch.setattr(MoveChecker, 'check', forbidden)
        patch.setattr(MoveChecker, 'check_function', forbidden)
        if name == 'loop_array_continue':
            patch.setattr(Lowerer, 'expression', observe_expression)
            patch.setattr(Lowerer, 'finish_iteration_scopes', observe_finish)
        result = compile_rust(text, rustc_check=checked)
        assert 'loop.body' in result.ir_text and 'loop.end' in result.ir_text
        if name in {'loop_diverging', 'loop_generic_return'}:
            assert 'unreachable' in result.ir_text
        assert result.emit_object().startswith(b'\x7fELF')
    if name == 'loop_array_continue':
        # The inner continue crosses neither outer iterator nor element scope.
        # Native comparison below also detects retiring the iterator cursor:
        # all three elements must contribute 10 + 12 + 20, not just the first.
        assert continue_edges == [(2, 2, 2, 2, 1, 0, (True, False))]
        assert active_continue == []
    assert result.externally_checked and not result.borrow_checked_by_pcc
    assert (tmp_path / 'certifier' / 'original.rs').read_text() == text


@pytest.mark.xdist_group(name='rust-native-reference')
@pytest.mark.pcc_gate(unavailable=actual_check_prerequisite())
def test_loop_record_cannot_be_replayed_for_changed_original(tmp_path, monkeypatch):
    text = source('loop_counter')
    checked = certify_source(text, tmp_path / 'certifier')
    from pcc.frontends.rust.lowering import Lowerer
    monkeypatch.setattr(Lowerer, 'lower', lambda *a: pytest.fail('changed source reached IR'))
    with pytest.raises(RustError, match='unchanged Rust source bytes'):
        compile_rust(text + '\n', rustc_check=checked)


@pytest.mark.xdist_group(name='rust-native-reference')
@pytest.mark.pcc_gate(unavailable=actual_check_prerequisite())
@pytest.mark.parametrize('name,text,message', CAPABILITY)
def test_valid_unsupported_loop_originals_keep_real_certified_boundaries(name, text, message, tmp_path):
    checked = certify_source(text, tmp_path / name)
    with pytest.raises(RustError, match=message):
        compile_rust(text, rustc_check=checked)


@pytest.mark.integration
@pytest.mark.xdist_group(name='rust-native-reference')
@pytest.mark.pcc_gate(unavailable=actual_check_prerequisite() or (
    None if sys.platform.startswith('linux') and platform.machine() in {'x86_64', 'AMD64'}
    and shutil.which('rustc') and shutil.which('cc') else 'requires Linux native rustc/system-linker reference'))
def test_actual_native_loop_originals_match_rustc(tmp_path, monkeypatch):
    from tests.rust import test_rust_standard_markers as shared
    monkeypatch.setattr(shared, 'PROGRAMS', PROGRAMS)
    monkeypatch.setattr(shared, 'source', source)
    shared.test_actual_native_marker_originals_match_rustc(tmp_path, monkeypatch)


@pytest.mark.integration
@pytest.mark.xdist_group(name='rust-native-reference')
@pytest.mark.pcc_gate(unavailable=actual_check_prerequisite())
def test_actual_rustc_rejects_exact_invalid_loop_originals(tmp_path, monkeypatch):
    from tests.rust import test_rust_supertraits as oracle
    monkeypatch.setattr(oracle, 'INVALID', INVALID)
    oracle.test_genuine_rustc_rejects_the_same_invalid_obligations(tmp_path, monkeypatch)
