"""Private unused-matcher selection retains store safety and public routes."""
import pytest
from pcc.backend import self_backend_aarch64_typed_peepholes as p
from tests.python import test_aarch64_store_source_address as safety


@pytest.mark.parametrize('mode',[3,4])
@pytest.mark.parametrize('private',[False,True])
def test_actual_route_skips_only_unused_matcher(monkeypatch,mode,private):
    f,s,span,_=safety.fixture(mode)
    calls={'zero':0,'move':0}
    zero,move=p._Pass.zero,p._Pass.move
    def observed_zero(self,*args):calls['zero']+=1;return zero(self,*args)
    def observed_move(self,*args):calls['move']+=1;return move(self,*args)
    monkeypatch.setattr(p._Pass,'zero',observed_zero)
    monkeypatch.setattr(p._Pass,'move',observed_move)
    try:
        if private:out=p._run_owned_store(f,s,span,mode)
        else:out=(p.fold_zero_store_source if mode==3 else p.fold_mov_store_source)(f,s,span)
        assert len(safety.o._ids(f,out))==1
        assert calls['zero' if mode==3 else 'move']>0
        assert (calls['move' if mode==3 else 'zero']==0)==private
    finally:s.close();f.close()


@pytest.mark.parametrize('mode,kwargs,changed',[
    (3,dict(base='x9'),False),(4,dict(base='x9'),False),
    (4,dict(old='w9',source='wsp'),False),
    (4,dict(source='x19',base='x19',address='post'),False),
    (4,dict(source='x19',base='x19'),True),
])
def test_private_store_safety_oracles(monkeypatch,mode,kwargs,changed):
    original=p._run
    calls=[]
    def dispatch(f,s,span,selected,owned_store_only=False):
        calls.append(owned_store_only)
        if owned_store_only:return original(f,s,span,selected,True)
        return p._run_owned_store(f,s,span,selected)
    monkeypatch.setattr(p,'_run',dispatch)
    before,after=safety.run_case(mode,changed,**kwargs)
    assert True in calls
    if kwargs.get('address','base')=='base':
        assert safety.decode(before)==safety.decode(after)


@pytest.mark.parametrize('plane',['records','headers','tokens'])
def test_native_gate_keeps_regular_run(monkeypatch,plane):
    f,s,span,_=safety.fixture(3)
    arena=f.records if plane=='records' else getattr(s,plane)
    calls=[]
    def regular(*args):calls.append(args);return span
    monkeypatch.setattr(p,'_run',regular)
    try:
        arena._address=1
        assert p._run_owned_store(f,s,span,3)==span
        assert calls==[(f,s,span,3)]
    finally:arena._address=0;s.close();f.close()


@pytest.mark.parametrize('mode',[3,4])
def test_real_phase_begin_mutation_falls_back_this_round(monkeypatch,mode):
    from pcc.backend import indexed_phase_timing as timing
    timer=timing.ModulePhaseTiming('store-select-boundary')
    f,s,span,_=safety.fixture(mode)
    phase='typed_fold_zero_store_source' if mode==3 else 'typed_fold_mov_store_source'
    calls=[];injected=False;run=p._run
    def event(*args,**kwargs):
        nonlocal injected
        if len(args)>=4 and args[1]==phase and args[3]=='begin' and not injected:
            injected=True
            timer.complete_backend_finalize=lambda *args:None
    def observe(f,s,span,selected,owned_store_only=False):
        calls.append((selected,owned_store_only))
        return run(f,s,span,selected,owned_store_only)
    monkeypatch.setattr(timing,'_write_event',event)
    monkeypatch.setattr(p,'_run',observe)
    try:
        p._run_aarch64_typed_peepholes(f,s,span,phase_timing=timer,owned_stack_windows=True)
        assert injected and (mode,False) in calls and (mode,True) not in calls
    finally:s.close();f.close()


def test_public_full_runner_never_selects_private_store(monkeypatch):
    f,s,span,_=safety.fixture(3)
    def forbidden(*args):raise AssertionError('public selected private store')
    monkeypatch.setattr(p,'_run_owned_store',forbidden)
    try:p.run_aarch64_typed_peepholes(f,s,span)
    finally:s.close();f.close()
