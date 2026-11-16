"""Owned mode-8 source-kernel controls; no prototype or expected-result rewrite."""
import pytest
from pcc.backend import self_backend_aarch64_owned_arith as kernel
from pcc.backend import self_backend_aarch64_typed_peepholes as typed
from pcc.backend import self_backend_aarch64_semantics as sem
from pcc.backend.self_backend_value_arena import CompilerInt4, CompilerIntArena
from pcc.backend.arm64_encode import EncodeError
from tests.python import test_aarch64_typed_peepholes as oracle

NAME = 'fold_mov_arith_self_update'


def window(dest='x9', src='x19', op='add', tail='immediate'):
    suffix = [oracle.N(1)]
    if tail in ('register', 'shift', 'blocked'):
        suffix = [oracle.R(dest if tail == 'blocked' else dest[0] + '10')]
    if tail == 'shift':
        suffix.append(CompilerInt4(sem.TOKEN_SHIFT, 0, 1, 1))
    return [oracle.I('mov', oracle.R(dest), oracle.R(src)),
            oracle.I(op, oracle.R(dest), oracle.R(dest), *suffix)]


def differential(monkeypatch, sequence):
    entries = []
    original_run, original_decide = typed._run, kernel.decide
    def decide(*args):
        entries.append(args[1:])
        return original_decide(*args)
    def dispatch(f, s, span, mode):
        return typed._run_owned_arith(f, s, span) if mode == 8 else original_run(f, s, span, mode)
    monkeypatch.setattr(kernel, 'decide', decide)
    monkeypatch.setattr(typed, '_run', dispatch)
    oracle._differential(monkeypatch, NAME, sequence)
    assert entries, 'actual source kernel was not reached'


@pytest.mark.parametrize('op', ('add', 'sub'))
@pytest.mark.parametrize('kind', ('w', 'x'))
@pytest.mark.parametrize('tail', ('immediate', 'register', 'shift', 'blocked'))
def test_owned_arith_matches_legacy(monkeypatch, op, kind, tail):
    differential(monkeypatch, window(kind + '9', kind + '19', op, tail))


@pytest.mark.parametrize('dest', ('x8', 'x9', 'x15', 'x16', 'w8', 'w9', 'w15', 'w16'))
def test_scratch_register_boundaries(monkeypatch, dest):
    differential(monkeypatch, window(dest, dest[0] + '19'))


def test_same_register_move_is_not_eliminated_by_owned_window(monkeypatch):
    differential(monkeypatch, window('x9', 'x9'))


@pytest.mark.parametrize('event', ('', 'L_stop:', '.p2align 2', '.pcc_memory_pair_barrier_begin'))
def test_owned_arith_never_skips_window_events(monkeypatch, event):
    sequence = window()
    sequence.insert(1, event)
    differential(monkeypatch, sequence)


def test_adjacent_windows_preserve_sentinels_and_consume_two(monkeypatch):
    differential(monkeypatch, [oracle.I('nop')] + window() + window('w9', 'w19', 'sub') + [oracle.I('nop')])


@pytest.mark.parametrize('op', ('add', 'sub'))
def test_wsp_uses_real_extended_register_producer(monkeypatch, op):
    differential(monkeypatch, window('w9', 'wsp', op, 'register'))


def test_wsp_shift_preserves_original_producer_error():
    errors = []
    for owned in (False, True):
        f, s, span, _ = oracle._fixture(window('w9', 'wsp', 'add', 'shift'))
        try:
            before = oracle._ids(f, span)
            with pytest.raises(EncodeError, match='WSP with shift needs explicit encoding') as raised:
                if owned:
                    typed._run_owned_arith(f, s, span)
                else:
                    typed._run(f, s, span, 8)
            errors.append(str(raised.value))
            assert oracle._ids(f, span) == before
        finally:
            s.close(); f.close()
    assert errors[0] == errors[1]


@pytest.mark.parametrize('mutation,should_change', (('move_class', False), ('tail_exact', False), ('tail_alias', True)))
def test_live_register_metadata_matches_old_typed_runner(mutation, should_change):
    # Isolated live-metadata controls, not claims about a producer generating
    # mixed-width MOV/ADD spellings accepted by the fixture text assembler.
    observed = []
    for owned in (False, True):
        f, s, span, _ = oracle._fixture(window(tail='register'))
        try:
            before = oracle._ids(f, span)
            rid = before[0] if mutation == 'move_class' else before[1]
            header = s.require_header(rid)
            token_index = 1 if mutation == 'move_class' else 2
            register = sem.semantic_register_id('w19' if mutation == 'move_class' else ('x9' if mutation == 'tail_exact' else 'w9'))
            s.tokens.set((header.third + token_index) * 4 + 1, register)
            result = typed._run_owned_arith(f, s, span) if owned else typed._run(f, s, span, 8)
            ids = oracle._ids(f, result)
            assert (len(ids) == 1) is should_change
            rows = []
            for record_id in ids:
                code = f.records.get4_unchecked(record_id)
                h = s.require_header(record_id)
                parts = [s.tokens.get4_unchecked(h.third + i) for i in range(h.fourth)]
                rows.append(((code.first, code.second, code.third, code.fourth),
                             h.first, [(t.first, t.second, t.third, t.fourth) for t in parts]))
            observed.append(rows)
        finally:
            s.close(); f.close()
    assert observed[0] == observed[1]


@pytest.mark.parametrize('name,blocked', (('x9', True), ('x9.part', True), ('part.x9', True),
                                         ('L_x9', False), ('x90', False), ('w9.part', False)))
def test_symbol_spelling_scan_stays_on_original_typed_path(name, blocked):
    outputs = []
    for owned in (False, True):
        f, s, span, _ = oracle._fixture(window())
        try:
            before = oracle._ids(f, span)
            sid = oracle.enc.intern_emitted_symbol(name, f.symbol_ids, f.symbol_names)
            header = s.require_header(before[1])
            at = (header.third + 2) * 4
            for offset, value in enumerate((sem.TOKEN_SYMBOL, sid, sem.SYMBOL_PLAIN, 0)):
                s.tokens.set(at + offset, value)
            # This deliberately exercises the existing live semantic metadata
            # boundary; it is not a text-assembler relocation fixture.
            result = typed._run_owned_arith(f, s, span) if owned else typed._run(f, s, span, 8)
            ids = oracle._ids(f, result)
            assert len(ids) == (2 if blocked else 1)
            rows = []
            for record_id in ids:
                code = f.records.get4_unchecked(record_id)
                h = s.require_header(record_id)
                operands = [s.tokens.get4_unchecked(h.third + i) for i in range(h.fourth)]
                rows.append(((code.first, code.second, code.third, code.fourth), h.first,
                             [(t.first, t.second, t.third, t.fourth) for t in operands]))
            outputs.append(rows)
        finally:
            s.close(); f.close()
    assert outputs[0] == outputs[1]


def test_output_root_and_partial_cursor_remain_independent():
    f, s, span, _ = oracle._fixture(window())
    cursor = CompilerIntArena()
    try:
        before = oracle._ids(f, span)
        prefix = (list(f.records._values), list(s.headers._values), list(s.tokens._values))
        f.spans.start_cursor(span, cursor)
        first = f.spans.next_record(cursor)
        result = typed._run_owned_arith(f, s, span)
        output = oracle._ids(f, result)
        assert result != span and len(output) == 1
        f.spans.append(span, before[0])
        assert oracle._ids(f, result) == output
        f.extend_fragment(result, result)
        assert oracle._ids(f, result) == output * 2
        assert oracle._ids(f, span) == before + before[:1]
        assert [first, f.spans.next_record(cursor)] == before
        assert f.spans.next_record(cursor) == -1
        for arena, saved in zip((f.records, s.headers, s.tokens), prefix):
            assert arena._values[:len(saved)] == saved
    finally:
        cursor.close(); s.close(); f.close()


@pytest.mark.parametrize('failure', ('producer', 'output_append'))
def test_owned_arith_failure_preserves_primary_error_and_closes_scratch(monkeypatch, failure):
    f, s, span, _ = oracle._fixture(window())
    before = oracle._ids(f, span)
    prefix = (list(f.records._values), list(s.headers._values), list(s.tokens._values))
    contexts, events, closed = [], [], []
    original_init = typed._Pass.__init__
    original_changed = typed._Pass.changed_register
    original_append, original_close = CompilerIntArena.append, CompilerIntArena.close
    error = RuntimeError('injected owned arithmetic ' + failure)
    def initialize(self, *args):
        original_init(self, *args)
        contexts.append(self)
    def changed(self, *args):
        events.append('producer')
        assert self.output._values == []
        if failure == 'producer':
            raise error
        return original_changed(self, *args)
    def append(self, value):
        if contexts and self is contexts[0].output:
            events.append('output')
            raise error
        return original_append(self, value)
    def close(self):
        if contexts and (self is contexts[0].ids or self is contexts[0].output):
            closed.append(self)
        return original_close(self)
    monkeypatch.setattr(typed._Pass, '__init__', initialize)
    monkeypatch.setattr(typed._Pass, 'changed_register', changed)
    monkeypatch.setattr(CompilerIntArena, 'append', append)
    monkeypatch.setattr(CompilerIntArena, 'close', close)
    try:
        with pytest.raises(RuntimeError) as raised:
            typed._run_owned_arith(f, s, span)
        assert raised.value is error
        assert events == (['producer'] if failure == 'producer' else ['producer', 'output'])
        assert closed == [contexts[0].output, contexts[0].ids]
        assert all(arena._closed and arena._values == [] for arena in closed)
        assert oracle._ids(f, span) == before
        for arena, saved in zip((f.records, s.headers, s.tokens), prefix):
            assert arena._values[:len(saved)] == saved
        s.require_complete(f.records, len(f.symbol_names))
    finally:
        s.close(); f.close()


def test_public_runner_does_not_select_owned_arith(monkeypatch):
    f, s, span, _ = oracle._fixture(window())
    def forbidden(*args):
        raise AssertionError('public runner entered private owned arithmetic')
    monkeypatch.setattr(typed, '_run_owned_arith', forbidden)
    try:
        result = typed.run_aarch64_typed_peepholes(f, s, span)
        assert oracle._ids(f, result)
    finally:
        s.close(); f.close()
