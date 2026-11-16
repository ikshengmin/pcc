"""Owned mode 5 admission: independent flags plus actual old-runner controls."""
import pytest
from pcc.backend import self_backend_aarch64_typed_peepholes as p
from pcc.backend import self_backend_aarch64_owned_compare as kernel
from pcc.backend.self_backend_value_arena import CompilerIntArena
from tests.python import test_aarch64_typed_peepholes as o
from tests.python import test_aarch64_zero_compare_flags as flags


def select_owned(monkeypatch):
    calls = []
    original_run, original_decide = p._run, kernel.decide
    def decide(*args):
        calls.append(args[1:])
        return original_decide(*args)
    def run(f, s, span, mode):
        return p._run_owned_zero_compare(f, s, span) if mode == 5 else original_run(f, s, span, mode)
    monkeypatch.setattr(kernel, 'decide', decide)
    monkeypatch.setattr(p, '_run', run)
    return calls


@pytest.mark.parametrize('width,lhs,changed', [('x','xzr',False), ('w','wzr',False),
                                             ('x','x19',True), ('w','w19',True), ('w','wsp',True)])
@pytest.mark.parametrize('consumed', [False, True])
def test_actual_owned_kernel_preserves_independent_nzcv(monkeypatch, width, lhs, changed, consumed):
    calls = select_owned(monkeypatch)
    flags.check(width, lhs, changed, consumed=consumed)
    assert calls, 'actual owned kernel not reached'


def tail(kind):
    if kind == 'read':
        return [o.I('mov', o.R('w0'), o.R('w9'))]
    if kind == 'alias_read':
        return [o.I('mov', o.R('x0'), o.R('x9'))]
    if kind == 'alias_overwrite':
        return [o.I('movz', o.R('x9'), o.N(1))]
    if kind == 'opaque':
        return ['.p2align 2']
    if kind == 'label':
        return ['L_next:']
    return []


def signature(f, s, span):
    result = []
    for rid in o._ids(f, span):
        code = f.records.get4_unchecked(rid)
        if code.second < 0:
            result.append(code)
        else:
            h = s.require_header(rid)
            result.append((code, h.first, [s.operand(rid, i) for i in range(h.fourth)]))
    return result


@pytest.mark.parametrize('kind', ['read','alias_read','alias_overwrite','opaque','label','eof'])
def test_real_dead_scan_and_old_runner_oracle(monkeypatch, kind):
    sequence = flags.sequence('w','w19') + tail(kind)
    expected = None
    dead_calls, kernel_calls = [], []
    old_dead, old_decide = p._Pass.dead, kernel.decide
    def dead(self, *args):
        dead_calls.append(args)
        return old_dead(self, *args)
    def decide(*args):
        kernel_calls.append(args[1:]); return old_decide(*args)
    for owned in [False, True]:
        f, s, span, _ = o._fixture(sequence)
        try:
            before = o._ids(f, span)
            if owned:
                monkeypatch.setattr(p._Pass, 'dead', dead)
                monkeypatch.setattr(kernel, 'decide', decide)
                out = p._run_owned_zero_compare(f,s,span)
                assert signature(f,s,out) == expected
                assert dead_calls[0] == (2, o.R('w9').second)
                assert kernel_calls
            else:
                out = p._run(f,s,span,5)
                expected = signature(f,s,out)
            assert o._ids(f,span) == before
        finally:
            s.close(); f.close()


@pytest.mark.parametrize('failure', ['dead','producer','output'])
def test_failure_order_primary_error_and_scratch_cleanup(monkeypatch, failure):
    f, s, span, _ = o._fixture(flags.sequence('x','x19'))
    before = o._ids(f,span)
    prefixes = [list(a._values) for a in (f.records,s.headers,s.tokens)]
    contexts, events, closed = [], [], []
    init, dead, new = p._Pass.__init__, p._Pass.dead, p._Pass.new
    append, close = CompilerIntArena.append, CompilerIntArena.close
    error = RuntimeError('owned compare injected ' + failure)
    def initialize(self,*args):
        init(self,*args); contexts.append(self)
    def scan(self,*args):
        events.append('dead')
        if failure == 'dead': raise error
        return dead(self,*args)
    def produce(self,*args):
        events.append('producer')
        assert self.output._values == []
        if failure == 'producer': raise error
        return new(self,*args)
    def output(self,value):
        if contexts and self is contexts[0].output:
            events.append('output'); raise error
        return append(self,value)
    def shutdown(self):
        if contexts and (self is contexts[0].output or self is contexts[0].ids): closed.append(self)
        return close(self)
    monkeypatch.setattr(p._Pass,'__init__',initialize)
    monkeypatch.setattr(p._Pass,'dead',scan)
    monkeypatch.setattr(p._Pass,'new',produce)
    monkeypatch.setattr(CompilerIntArena,'append',output)
    monkeypatch.setattr(CompilerIntArena,'close',shutdown)
    try:
        with pytest.raises(RuntimeError) as caught:
            p._run_owned_zero_compare(f,s,span)
        assert caught.value is error
        expected = ['dead'] if failure == 'dead' else ['dead','producer'] if failure == 'producer' else ['dead','producer','output']
        assert events == expected
        assert closed == [contexts[0].output,contexts[0].ids]
        assert all(a._closed and not a._values for a in closed)
        assert o._ids(f,span) == before
        assert all(a._values[:len(saved)] == saved for a,saved in zip((f.records,s.headers,s.tokens),prefixes))
        s.require_complete(f.records,len(f.symbol_names))
    finally:
        s.close(); f.close()


def test_public_runner_keeps_old_route(monkeypatch):
    f, s, span, _ = o._fixture(flags.sequence('x','x19'))
    def forbidden(*args): raise AssertionError('public runner selected owned compare')
    monkeypatch.setattr(p,'_run_owned_zero_compare',forbidden)
    try:
        p.run_aarch64_typed_peepholes(f,s,span)
    finally:
        s.close(); f.close()


@pytest.mark.parametrize('case', ['shift_zero','shift_nonzero','hex_radix','negative_zero'])
def test_owned_exact_movz_qualification(monkeypatch, case):
    # Metadata provenance is checked independently of machine-word equivalence.
    # A noncanonical spelling must remain untouched even when its value is zero.
    from pcc.backend import self_backend_aarch64_semantics as sem
    sequence = flags.sequence('x', 'x19', shift=0 if case == 'shift_zero' else 16 if case == 'shift_nonzero' else None)
    if case in ('hex_radix','negative_zero'):
        sequence[0] = o.I('movz', o.R('x9'), o.N(0, 16 if case == 'hex_radix' else 10,
                            sem.IMMEDIATE_HASH | (sem.IMMEDIATE_NEGATIVE_ZERO if case == 'negative_zero' else 0)))
    original_decide, calls = kernel.decide, []
    def decide(*args):
        calls.append(args[1:]); return original_decide(*args)
    monkeypatch.setattr(kernel,'decide',decide)
    expected = None
    for owned in (False, True):
        f,s,span,_ = o._fixture(sequence)
        try:
            before = signature(f,s,span)
            out = p._run_owned_zero_compare(f,s,span) if owned else p._run(f,s,span,5)
            actual = signature(f,s,out)
            if not owned:
                expected = actual
                assert len(actual) == (1 if case == 'shift_zero' else 2)
                if case != 'shift_zero': assert actual == before
            else:
                assert actual == expected, 'owned MOVZ qualification oracle'
                assert calls, 'actual owned kernel not reached'
        finally:
            s.close();f.close()


@pytest.mark.parametrize('case,guard', [
    ('hex_radix',' or tokens[at + 6] != 10'),
    ('negative_zero',' or tokens[at + 7] != IMMEDIATE_HASH'),
    ('shift_nonzero',' or tokens[at + 10] != 0'),
])
def test_owned_missing_movz_qualification_mutants(monkeypatch, case, guard):
    # Unmodified positive control restores its instrumentation before mutation.
    with monkeypatch.context() as positive:
        test_owned_exact_movz_qualification(positive,case)
    monkeypatch.setattr(kernel,'decide',flags.mutate(kernel.decide,guard,''))
    with pytest.raises(AssertionError, match=r'^owned MOVZ qualification oracle(?:\n|$)'):
        test_owned_exact_movz_qualification(monkeypatch,case)


@pytest.mark.parametrize('width', ['x','w'])
def test_owned_missing_zr_guard_mutant_killed_by_nzcv(monkeypatch, width):
    with monkeypatch.context() as positive:
        test_actual_owned_kernel_preserves_independent_nzcv(positive,width,width+'zr',False,True)
    monkeypatch.setattr(kernel,'decide',flags.mutate(kernel.decide,' or lhs in (REGISTER_XZR, REGISTER_WZR)',''))
    with pytest.raises(AssertionError, match=r'^output NZCV oracle(?:\n|$)'):
        test_actual_owned_kernel_preserves_independent_nzcv(monkeypatch,width,width+'zr',False,True)
