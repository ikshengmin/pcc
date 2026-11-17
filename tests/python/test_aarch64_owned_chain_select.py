"""Mode10 selective matcher, no extra snapshot planes or altered producer."""
import pytest
from pcc.backend import self_backend_aarch64_typed_peepholes as p
from tests.python import test_aarch64_typed_peepholes as o
from tests.python import test_aarch64_w_self_move as wself
from tests.python import test_aarch64_mov_register_domains as domains


def pair(kind='none'):
    if kind=='none':seq=[o.I('movz',o.R('w9'),o.N(0)),o.I('mov',o.R('w0'),o.R('w9'))]
    elif kind=='scratch':seq=[o.I('mov',o.R('w8'),o.R('w20')),o.I('mov',o.R('w0'),o.R('w8'))]
    elif kind=='alias':seq=[o.I('mov',o.R('w9'),o.R('w20')),o.I('mov',o.R('x0'),o.R('x9'))]
    else:seq=[o.I('mov',o.R('w9'),o.R('wsp')),o.I('mov',o.R('wzr'),o.R('w9'))]
    return o._fixture(seq)


def route(monkeypatch):
    run=p._run;calls=[]
    def dispatch(f,s,span,mode,owned_matcher_only=False):
        calls.append((mode,owned_matcher_only))
        if owned_matcher_only:return run(f,s,span,mode,True)
        return p._run_owned_move_matchers(f,s,span,mode)
    monkeypatch.setattr(p,'_run',dispatch)
    return calls


@pytest.mark.parametrize('private',[False,True])
@pytest.mark.parametrize('kind',['none','scratch','alias','mixed'])
def test_reads_second_except_private_first_none(monkeypatch,private,kind):
    f,s,span,_=pair(kind);events=[];move=p._Pass.move
    def observed(self,rid):events.append(rid);return move(self,rid)
    monkeypatch.setattr(p._Pass,'move',observed)
    try:
        ids=o._ids(f,span)
        out=p._run_owned_move_matchers(f,s,span,10) if private else p.fold_mov_mov_chain(f,s,span)
        assert o._ids(f,out)==ids
        assert events==(ids[:1] if private and kind=='none' else ids), 'second matcher oracle'
    finally:s.close();f.close()


def test_present_first_preserves_second_error_before_producer(monkeypatch):
    f,s,span,_=pair('scratch');ids=o._ids(f,span);move=p._Pass.move
    events=[];error=RuntimeError('second move primary')
    def observed(self,rid):
        events.append(rid)
        if rid==ids[1]:raise error
        return move(self,rid)
    def forbidden(*args):raise AssertionError('producer before second matcher')
    monkeypatch.setattr(p._Pass,'move',observed);monkeypatch.setattr(p._Pass,'new_move',forbidden)
    try:
        with pytest.raises(RuntimeError) as caught:p._run_owned_move_matchers(f,s,span,10)
        assert caught.value is error and events==ids
    finally:s.close();f.close()


@pytest.mark.parametrize('private',[False,True])
def test_none_first_still_validates_bad_second_before_matching(monkeypatch,private):
    f,s,span,_=pair();events=[]
    def forbidden(*args):events.append('move');raise AssertionError('matching before validation')
    monkeypatch.setattr(p._Pass,'move',forbidden)
    try:
        ids=o._ids(f,span);s.headers.set(ids[1]*4,0)
        with pytest.raises(o.enc.EncodeError,match='AArch64 instruction provenance is unproven'):
            if private:p._run_owned_move_matchers(f,s,span,10)
            else:p.fold_mov_mov_chain(f,s,span)
        assert events==[]
    finally:s.close();f.close()


@pytest.mark.parametrize('width,case',[('w','roundtrip'),('x','roundtrip'),('w','live_alias')])
def test_actual_private_live_x_and_flags(monkeypatch,width,case):
    calls=route(monkeypatch);wself.check(10,width,case=case)
    assert (10,True) in calls


@pytest.mark.parametrize('source,dest,changed',[('wsp','wzr',False),('wzr','wsp',False),('wsp','w19',True)])
def test_actual_private_sp_domains(monkeypatch,source,dest,changed):
    calls=route(monkeypatch);domains.check(source,dest,changed)
    assert (10,True) in calls


@pytest.mark.parametrize('plane',['records','headers','tokens'])
def test_native_storage_retains_old_signature(monkeypatch,plane):
    f,s,span,_=pair();arena=f.records if plane=='records' else getattr(s,plane);calls=[]
    def fallback(*args):calls.append(args);return span
    monkeypatch.setattr(p,'_run',fallback)
    try:
        arena._address=1
        assert p._run_owned_move_matchers(f,s,span,10)==span
        assert calls==[(f,s,span,10)]
    finally:arena._address=0;s.close();f.close()


def test_public_pipeline_does_not_select_private(monkeypatch):
    f,s,span,_=pair()
    def forbidden(*args):raise AssertionError('public selected private')
    monkeypatch.setattr(p,'_run_owned_move_matchers',forbidden)
    try:p.run_aarch64_typed_peepholes(f,s,span)
    finally:s.close();f.close()


def test_begin_timer_mutation_falls_back(monkeypatch):
    from pcc.backend import indexed_phase_timing as timing
    f,s,span,_=pair();timer=timing.ModulePhaseTiming('chain-select-boundary')
    calls=[];run=p._run;injected=False
    def event(*args,**kwargs):
        nonlocal injected
        if len(args)>=4 and args[1]=='typed_fold_mov_mov_chain' and args[3]=='begin':
            injected=True;timer.complete_backend_finalize=lambda *args:None
    def observed(f,s,span,mode,owned_matcher_only=False):
        calls.append((mode,owned_matcher_only));return run(f,s,span,mode,owned_matcher_only)
    monkeypatch.setattr(timing,'_write_event',event);monkeypatch.setattr(p,'_run',observed)
    try:
        p._run_aarch64_typed_peepholes(f,s,span,phase_timing=timer,owned_stack_windows=True)
        assert injected and (10,False) in calls and (10,True) not in calls
    finally:s.close();f.close()
