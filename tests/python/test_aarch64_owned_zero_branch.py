"""Owned mode11 actual entry, independent relocation/CFG and failure oracles."""
import pytest
from pcc.backend import self_backend_aarch64_typed_peepholes as p
from pcc.backend import self_backend_aarch64_owned_zero_branch as kernel
from tests.python import test_aarch64_zero_branch_live as base


def owned(monkeypatch):
    calls=[]
    original,decide=p._run,kernel.decide
    def observed(*args):calls.append(args[1:]);return decide(*args)
    def dispatch(f,s,span,mode):
        return p._run_owned_zero_branch(f,s,span) if mode==11 else original(f,s,span,mode)
    monkeypatch.setattr(kernel,'decide',observed)
    monkeypatch.setattr(p,'_run',dispatch)
    return calls


@pytest.mark.parametrize('width',['w','x'])
@pytest.mark.parametrize('branch,dead',[('cbz',False),('cbnz',False),('cbz',True)])
def test_owned_live_target_with_independent_cfg(monkeypatch,width,branch,dead):
    calls=owned(monkeypatch)
    base.check(width,branch,dead)
    assert calls,'actual owned kernel not reached'


def test_owned_symbol_four_fields(monkeypatch):
    calls=owned(monkeypatch)
    base.test_branch_symbol_all_four_fields_retained()
    assert calls,'actual owned kernel not reached'


def test_owned_failed_producer_before_output(monkeypatch):
    calls=owned(monkeypatch)
    base.test_new_branch_failure_precedes_retaining_zero(monkeypatch)
    assert calls,'actual owned kernel not reached'


@pytest.mark.parametrize('fail_at',[1,2])
def test_owned_output_failure_after_successful_producer(monkeypatch,fail_at):
    calls=owned(monkeypatch)
    base.test_output_failure_after_successful_branch_producer(monkeypatch,fail_at)
    assert calls,'actual owned kernel not reached'


def qualification(case):
    from tests.python import test_aarch64_typed_peepholes as o
    from pcc.backend.self_backend_value_arena import CompilerInt4
    tokens=[o.R('w9'),o.N(0,16 if case=='radix' else 10,
                        o.sem.IMMEDIATE_HASH | (o.sem.IMMEDIATE_NEGATIVE_ZERO if case=='flags' else 0))]
    if case=='shift':tokens.append(CompilerInt4(o.sem.TOKEN_SHIFT,0,16,1))
    seq=[o.I('movz',*tokens),o.I('cbz',o.R('x9' if case=='alias' else 'w9'),o.S('L_end'))]
    if case=='event':seq.insert(1,'')
    results=[]
    for selected in (False,True):
        f,s,span,_=o._fixture(seq)
        try:
            before=o._ids(f,span)
            out=p._run_owned_zero_branch(f,s,span) if selected else p._run(f,s,span,11)
            values=[f.records.get4_unchecked(rid) for rid in o._ids(f,out)]
            if not selected:assert o._ids(f,out)==before
            results.append(values)
        finally:s.close();f.close()
    assert results[0]==results[1], 'owned zero-branch qualification oracle'


@pytest.mark.parametrize('case',['radix','flags','shift','alias','event'])
def test_owned_qualification_negatives(case):qualification(case)


@pytest.mark.parametrize('case,guard',[
    ('radix',' or tokens[at + 6] != 10'),
    ('flags',' or tokens[at + 7] != IMMEDIATE_HASH'),
    ('shift',' or tokens[at + 10] != 0'),
    ('alias',' or tokens[bt + 1] != zero'),
])
def test_missing_qualification_mutant_is_killed(monkeypatch,case,guard):
    import inspect
    qualification(case)
    source=inspect.getsource(kernel.decide)
    assert source.count(guard)==1, 'unique mutation anchor'
    namespace=dict(kernel.decide.__globals__)
    exec(compile(source.replace(guard,''),'<owned-zero-branch-mutant>','exec'),namespace)
    monkeypatch.setattr(kernel,'decide',namespace['decide'])
    with pytest.raises(AssertionError,match=r'^owned zero-branch qualification oracle(?:\n|$)'):
        qualification(case)


def test_real_public_runner_never_selects_owned(monkeypatch):
    from tests.python import test_aarch64_typed_peepholes as o
    f,s,span,_=o._fixture(base.sequence())
    def forbidden(*args):raise AssertionError('public selected owned zero branch')
    monkeypatch.setattr(p,'_run_owned_zero_branch',forbidden)
    try:p.run_aarch64_typed_peepholes(f,s,span)
    finally:s.close();f.close()


def nonzero_qualification():
    from tests.python import test_aarch64_typed_peepholes as o
    seq=[o.I('movz',o.R('w9'),o.N(1)),o.I('cbz',o.R('w9'),o.S('L_end'))]
    results=[]
    for selected in (False,True):
        f,s,span,_=o._fixture(seq)
        try:
            before=o._ids(f,span)
            out=p._run_owned_zero_branch(f,s,span) if selected else p._run(f,s,span,11)
            if not selected:assert o._ids(f,out)==before
            results.append([f.records.get4_unchecked(rid) for rid in o._ids(f,out)])
        finally:s.close();f.close()
    assert results[0]==results[1], 'owned nonzero MOVZ oracle'


def test_nonzero_movz_is_not_folded():nonzero_qualification()


def test_missing_zero_value_guard_is_killed(monkeypatch):
    import inspect
    nonzero_qualification()
    source=inspect.getsource(kernel.decide)
    guard=' or tokens[at + 5] != 0'
    assert source.count(guard)==1,'unique mutation anchor'
    namespace=dict(kernel.decide.__globals__)
    exec(compile(source.replace(guard,''),'<owned-nonzero-mutant>','exec'),namespace)
    monkeypatch.setattr(kernel,'decide',namespace['decide'])
    with pytest.raises(AssertionError,match=r'^owned nonzero MOVZ oracle(?:\n|$)'):
        nonzero_qualification()
