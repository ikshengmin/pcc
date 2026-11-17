"""Phase-local branch/chain positions preserve live registers and overlap."""
import inspect
import pytest
from pcc.backend import self_backend_aarch64_typed_peepholes as p
from tests.python import test_aarch64_typed_peepholes as o
from tests.python import test_aarch64_owned_move_select as select
from tests.python import test_aarch64_owned_chain_select as chain_select
from tests.python import test_aarch64_branch_live_registers as branch
from tests.python import test_aarch64_mov_register_domains as domains
from tests.python import test_aarch64_w_self_move as wself


def run(f,s,span,mode):return p._run_owned_move_matchers(f,s,span,mode)


def overlap(monkeypatch,refused=False):
    pairs=[('w9','wzr'),('wsp','w9'),('w9','wsp'),('w0','w9')] if refused else [
        ('w9','w20'),('w10','w9'),('w11','w10'),('w0','w11')]
    pairs.append(('x1','sp' if refused else 'x0'))
    f,s,span,_=o._fixture([o.I('mov',o.R(dst),o.R(src)) for dst,src in pairs]);calls=[];move=p._Pass.move
    def observed(self,rid):calls.append(rid);return move(self,rid)
    monkeypatch.setattr(p._Pass,'move',observed)
    try:
        ids=o._ids(f,span);before=[f.records.get4_unchecked(r).first for r in ids]
        out=run(f,s,span,10);outids=o._ids(f,out)
        after=[f.records.get4_unchecked(r).first for r in outids]
        assert len(outids)==(4 if refused else 3), 'overlap consume oracle'
        assert domains.effect(after)==domains.effect(before), 'overlap SP and live output oracle'
        # Rejected -2 at0 leaves position1 available; successful consume2 at2
        # discards position3. Tail alone cannot form a candidate.
        expected=[ids[0],ids[1],ids[1],ids[2],ids[2],ids[3]] if refused else [ids[0],ids[1],ids[2],ids[3]]
        assert calls==expected, 'overlap matcher order oracle'
        if refused:assert outids[:2]==ids[:2]
        assert o._ids(f,span)==ids
    finally:s.close();f.close()


@pytest.mark.parametrize('refused',[False,True])
def test_overlapping_positions_and_minus_two(monkeypatch,refused):overlap(monkeypatch,refused)


@pytest.mark.parametrize('mode',[7,10])
@pytest.mark.parametrize('event',[None,'L_stop:',''])
def test_noncandidate_and_event_skip_matchers(monkeypatch,mode,event):
    first=o.I('mov',o.R('w9'),o.R('w20'))
    last=o.I('cbz',o.R('w9'),o.S()) if mode==7 else o.I('mov',o.R('w0'),o.R('w9'))
    seq=[first,o.I('movz',o.R('w0'),o.N(1))] if event is None else [first,event,last]
    f,s,span,_=o._fixture(seq)
    def forbidden(*args):raise AssertionError('sparse skip oracle')
    monkeypatch.setattr(p._Pass,'move',forbidden)
    try:
        ids=o._ids(f,span);out=run(f,s,span,mode)
        assert o._ids(f,out)==ids
    finally:s.close();f.close()


@pytest.mark.parametrize('name,opcode',[('target_live','cbz'),('target_live','cbnz'),('WSP_branch_domain','cbz')])
def test_private_branch_cfg_and_live_registers(monkeypatch,name,opcode):
    calls=select.select(monkeypatch);branch.check(7,name,opcode)
    assert (7,True) in calls


@pytest.mark.parametrize('width',['w','x'])
def test_private_chain_zero_extension(monkeypatch,width):
    calls=select.select(monkeypatch);wself.check(10,width)
    assert (10,True) in calls


@pytest.mark.parametrize('source,dest,changed',[('wsp','wzr',False),('wzr','wsp',False),('wsp','w19',True)])
def test_private_chain_sp_domain(monkeypatch,source,dest,changed):
    calls=select.select(monkeypatch);domains.check(source,dest,changed)
    assert (10,True) in calls


@pytest.mark.parametrize('mode',[7,10])
def test_full_initial_validation_of_non_candidates(mode):
    f,s,span,_=o._fixture([o.I('movz',o.R('w0'),o.N(1))])
    try:
        rid=o._ids(f,span)[0];s.headers.set(rid*4,0)
        with pytest.raises(o.enc.EncodeError,match='AArch64 instruction provenance is unproven'):run(f,s,span,mode)
    finally:s.close();f.close()


@pytest.mark.parametrize('mode',[7,10])
def test_actual_phase_begin_fallback(monkeypatch,mode):
    if mode==7:select.test_timer_rechecked_after_real_begin(monkeypatch,7)
    else:chain_select.test_begin_timer_mutation_falls_back(monkeypatch)


@pytest.mark.parametrize('mode',[7,10])
def test_native_storage_old_signature(monkeypatch,mode):
    f,s,span,_=o._fixture([o.I('movz',o.R('w0'),o.N(1))]);calls=[]
    def fallback(*args):calls.append(args);return span
    monkeypatch.setattr(p,'_run',fallback)
    try:
        f.records._address=1
        assert run(f,s,span,mode)==span and calls==[(f,s,span,mode)]
    finally:f.records._address=0;s.close();f.close()


def test_overlap_stale_position_mutant(monkeypatch):
    with monkeypatch.context() as patch:overlap(patch)
    source=inspect.getsource(p._run);old='sparse_positions[sparse_cursor] < index:'
    assert source.count(old)==1
    namespace=dict(p._run.__globals__)
    exec(compile(source.replace(old,'sparse_positions[sparse_cursor] < index - 1:'),'<overlap-mutant>','exec'),namespace)
    monkeypatch.setattr(p,'_run',namespace['_run'])
    with pytest.raises(AssertionError,match=r'^overlap consume oracle(?:\n|$)'):overlap(monkeypatch)


def test_missing_chain_candidate_mutant(monkeypatch):
    with monkeypatch.context() as patch:overlap(patch)
    source=inspect.getsource(p._run);old='mode == 10 and next_opcode == mov_opcode'
    assert source.count(old)==1
    namespace=dict(p._run.__globals__)
    exec(compile(source.replace(old,'mode == 10 and next_opcode == cmp_opcode'),'<missing-chain-mutant>','exec'),namespace)
    monkeypatch.setattr(p,'_run',namespace['_run'])
    with pytest.raises(AssertionError,match=r'^overlap consume oracle(?:\n|$)'):overlap(monkeypatch)


def branch_candidate_oracle():
    f,s,span,_=o._fixture([o.I('mov',o.R('w9'),o.R('w0')),o.I('cbz',o.R('w9'),o.S())])
    try:
        before=o._ids(f,span);out=run(f,s,span,7);ids=o._ids(f,out)
        assert len(ids)==2 and ids[0]==before[0]
        assert f.records.get4_unchecked(ids[1]).first&31==0, 'branch candidate output oracle'
    finally:s.close();f.close()


def test_missing_branch_candidate_mutant(monkeypatch):
    branch_candidate_oracle()
    source=inspect.getsource(p._run);old='mode == 7 and next_opcode in (cbz_opcode, cbnz_opcode)'
    assert source.count(old)==1
    namespace=dict(p._run.__globals__)
    exec(compile(source.replace(old,'mode == 7 and next_opcode == cmp_opcode'),'<missing-branch-mutant>','exec'),namespace)
    monkeypatch.setattr(p,'_run',namespace['_run'])
    with pytest.raises(AssertionError,match=r'^branch candidate output oracle(?:\n|$)'):branch_candidate_oracle()
