"""Independent mode-12 text oracle, typed edge cases, and failure ordering."""
import pytest
from pcc.backend import self_backend_aarch64_owned_cset as kernel
from pcc.backend import self_backend_aarch64_typed_peepholes as typed
from pcc.backend import self_backend_aarch64_semantics as sem
from pcc.backend.self_backend_value_arena import CompilerInt4, CompilerIntArena
from tests.python import test_aarch64_typed_peepholes as oracle

NAME = 'fold_forwarded_cset_branch'


def window(op='cbz', condition='eq', same=False, variant='normal'):
    dst = 'w9' if same else 'w10'
    source = 'x9' if variant == 'cset_x' else 'w9'
    cset = 'csetm' if variant == 'csetm' else 'cset'
    store = 'stur' if variant == 'store_width' else 'sturb'
    store_reg = 'w11' if variant == 'store_reg' else 'w9'
    addr = oracle.A(8 if variant == 'positive_offset' else -8)
    if variant == 'fp':
        addr = (CompilerInt4(sem.TOKEN_ADDRESS, sem.ADDRESS_OFFSET, 2, 0), oracle.R('fp'), oracle.N(-8))
    if variant == 'negative_zero':
        addr = oracle.A(0, 10, sem.IMMEDIATE_HASH | sem.IMMEDIATE_NEGATIVE_ZERO)
    src = 'w11' if variant == 'and_src' else 'w9'
    if variant == 'and_x':
        dst, src = 'x10', 'x9'
    branch_reg = 'x10' if variant == 'branch_alias' else ('w11' if variant == 'branch_reg' else dst)
    immediate = oracle.N(127 if variant == 'value' else 255,
                         10 if variant == 'radix' else 16,
                         sem.IMMEDIATE_HASH | (sem.IMMEDIATE_HEX_UPPER if variant == 'upper' else 0))
    return [oracle.I(cset, oracle.R(source), oracle.C(condition)),
            oracle.I(store, oracle.R(store_reg), addr),
            oracle.I('and', oracle.R(dst), oracle.R(src), immediate),
            oracle.I(op, oracle.R(branch_reg), oracle.S())]


def differential(monkeypatch, sequence):
    calls = []
    original = typed._run
    decide = kernel.decide
    def observed(*args):
        calls.append(args[1:])
        return decide(*args)
    def dispatch(f, s, span, mode):
        return typed._run_owned_cset(f, s, span) if mode == 12 else original(f, s, span, mode)
    monkeypatch.setattr(kernel, 'decide', observed)
    monkeypatch.setattr(typed, '_run', dispatch)
    oracle._differential(monkeypatch, NAME, sequence)
    assert calls, 'actual source kernel was not reached'


@pytest.mark.parametrize('op', ('cbz', 'cbnz'))
@pytest.mark.parametrize('condition', ('eq', 'ne'))
@pytest.mark.parametrize('same', (False, True))
def test_owned_cset_matches_legacy_positive(monkeypatch, op, condition, same):
    differential(monkeypatch, window(op, condition, same))


@pytest.mark.parametrize('variant', ('cset_x', 'store_width', 'store_reg', 'fp',
                                    'positive_offset', 'and_src', 'and_x', 'branch_alias',
                                    'branch_reg', 'value', 'radix', 'upper', 'negative_zero'))
def test_owned_cset_preserves_exact_spelling_and_register_restrictions(monkeypatch, variant):
    differential(monkeypatch, window(variant=variant))


@pytest.mark.parametrize('gap', (1, 2, 3))
@pytest.mark.parametrize('event', ('', 'L_stop:', '.p2align 2', '.pcc_memory_pair_barrier_begin'))
def test_owned_cset_does_not_cross_events(monkeypatch, gap, event):
    sequence = window()
    sequence.insert(gap, event)
    differential(monkeypatch, sequence)


def test_owned_cset_consumes_exactly_four_and_handles_successive_windows(monkeypatch):
    differential(monkeypatch, [oracle.I('nop')] + window() + window('cbnz', 'ne') + [oracle.I('nop')])


@pytest.mark.parametrize('flags', (0, sem.IMMEDIATE_HASH | sem.IMMEDIATE_EXPLICIT_PLUS))
def test_noncanonical_immediate_flags_match_old_typed_runner(flags):
    # These provenance spellings can be outside the fixture text assembler's
    # accepted syntax. Compare old typed behavior without reconstructing text.
    outputs = []
    for owned in (False, True):
        f, s, span, _ = oracle._fixture(window())
        try:
            before = oracle._ids(f, span)
            header = s.require_header(before[2])
            s.tokens.set((header.third + 2) * 4 + 3, flags)
            result = typed._run_owned_cset(f, s, span) if owned else typed._run(f, s, span, 12)
            actual = oracle._ids(f, result)
            assert actual == before
            outputs.append(actual)
        finally:
            s.close(); f.close()
    assert outputs[0] == outputs[1]


def test_replacement_preserves_all_symbol_token_fields():
    outputs = []
    for owned in (False, True):
        f, s, span, _ = oracle._fixture(window())
        try:
            before = oracle._ids(f, span)
            header = s.require_header(before[3])
            offset = (header.third + 1) * 4
            s.tokens.set(offset + 2, sem.SYMBOL_PAGE)
            s.tokens.set(offset + 3, 37)
            target = s.tokens.get4_unchecked(header.third + 1)
            expected = (target.first, target.second, target.third, target.fourth)
            result = typed._run_owned_cset(f, s, span) if owned else typed._run(f, s, span, 12)
            actual = oracle._ids(f, result)
            assert len(actual) == 3 and actual[:2] == before[:2]
            replacement = s.require_header(actual[-1])
            token = s.tokens.get4_unchecked(replacement.third + 1)
            observed = (token.first, token.second, token.third, token.fourth)
            assert observed == expected
            outputs.append(observed)
        finally:
            s.close(); f.close()
    assert outputs[0] == outputs[1]


@pytest.mark.parametrize('failure', ('append_a', 'append_b', 'producer', 'append_replacement'))
def test_owned_cset_preserves_append_then_producer_failure_order(monkeypatch, failure):
    f, s, span, _ = oracle._fixture(window())
    before = oracle._ids(f, span)
    prefixes = (list(f.records._values), list(s.headers._values), list(s.tokens._values))
    contexts = []
    events = []
    expected_error = RuntimeError('injected mode12 ' + failure)
    original_init = typed._Pass.__init__
    original_append = CompilerIntArena.append
    original_branch = typed._Pass.new_branch
    original_close = CompilerIntArena.close
    append_count = 0
    closed = []
    def initialize(self, *args):
        original_init(self, *args)
        contexts.append(self)
    def append(self, value):
        nonlocal append_count
        if contexts and self is contexts[0].output:
            append_count += 1
            events.append(('append', append_count))
            fail_at = {'append_a': 1, 'append_b': 2, 'append_replacement': 3}.get(failure)
            if append_count == fail_at:
                raise expected_error
        return original_append(self, value)
    def branch(self, *args):
        events.append(('producer', tuple(self.output._values)))
        assert self.output._values == before[:2]
        if failure == 'producer':
            raise expected_error
        return original_branch(self, *args)
    def close(self):
        if contexts and (self is contexts[0].output or self is contexts[0].ids):
            closed.append(self)
        return original_close(self)
    monkeypatch.setattr(typed._Pass, '__init__', initialize)
    monkeypatch.setattr(CompilerIntArena, 'append', append)
    monkeypatch.setattr(typed._Pass, 'new_branch', branch)
    monkeypatch.setattr(CompilerIntArena, 'close', close)
    try:
        with pytest.raises(RuntimeError) as raised:
            typed._run_owned_cset(f, s, span)
        assert raised.value is expected_error
        if failure == 'append_a':
            assert events == [('append', 1)]
        elif failure == 'append_b':
            assert events == [('append', 1), ('append', 2)]
        else:
            expected = [('append', 1), ('append', 2), ('producer', tuple(before[:2]))]
            if failure == 'append_replacement':
                expected.append(('append', 3))
            assert events == expected
        assert closed == [contexts[0].output, contexts[0].ids]
        assert all(a._closed and a._values == [] for a in closed)
        assert oracle._ids(f, span) == before
        for arena, prefix in zip((f.records, s.headers, s.tokens), prefixes):
            assert arena._values[:len(prefix)] == prefix
        s.require_complete(f.records, len(f.symbol_names))
    finally:
        s.close(); f.close()


def test_public_runner_does_not_dispatch_owned_cset(monkeypatch):
    f, s, span, _ = oracle._fixture(window())
    def forbidden(*args):
        raise AssertionError('public runner entered owned mode12')
    monkeypatch.setattr(typed, '_run_owned_cset', forbidden)
    try:
        result = typed.run_aarch64_typed_peepholes(f, s, span)
        assert oracle._ids(f, result)
    finally:
        s.close(); f.close()
