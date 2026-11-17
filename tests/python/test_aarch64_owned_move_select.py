"""Private mode6/7 matcher selection without changing producer contracts."""
import pytest
from pcc.backend import self_backend_aarch64_typed_peepholes as p
from tests.python import test_aarch64_typed_peepholes as o
from tests.python import test_aarch64_mov_compare_sp as compare
from tests.python import test_aarch64_branch_live_registers as branch_live


def pair(mode,kind='normal'):
    dest='w8' if kind=='scratch' else 'x9' if kind=='width' else 'w9'
    src='wsp' if kind=='wsp' else 'x20' if kind=='width' else 'w20'
    first=o.I('movz',o.R(dest),o.N(0)) if kind=='no_move' else o.I('mov',o.R(dest),o.R(src))
    second=o.I('cmp',o.R('w19'),o.R(dest)) if mode==6 else o.I('cbz',o.R(dest),o.S())
    return o._fixture([first,second])


def select(monkeypatch):
    original=p._run
    calls=[]
    def dispatch(f,s,span,mode,owned_matcher_only=False):
        calls.append((mode,owned_matcher_only))
        if owned_matcher_only:return original(f,s,span,mode,True)
        return p._run_owned_move_matchers(f,s,span,mode)
    monkeypatch.setattr(p,'_run',dispatch)
    return calls


@pytest.mark.parametrize('private',[False,True])
def test_mode6_keeps_compare_then_move_and_only_skips_zero(monkeypatch,private):
    f,s,span,_=pair(6)
    events=[]
    for name in ('compare','zero','move'):
        original=getattr(p._Pass,name)
        def observed(self,*args,_name=name,_fn=original):events.append(_name);return _fn(self,*args)
        monkeypatch.setattr(p._Pass,name,observed)
    try:
        out=p._run_owned_move_matchers(f,s,span,6) if private else p.fold_mov_compare_source(f,s,span)
        assert len(o._ids(f,out))==1
        assert events==(['compare','move'] if private else ['compare','zero','move'])
    finally:s.close();f.close()


@pytest.mark.parametrize('kind',['no_move','wsp','width','scratch'])
def test_mode7_only_none_move_skips_branch(monkeypatch,kind):
    f,s,span,_=pair(7,kind)
    events=[]
    move,branch=p._Pass.move,p._Pass.branch
    def get_move(self,*args):events.append('move');return move(self,*args)
    def get_branch(self,*args):events.append('branch');return branch(self,*args)
    monkeypatch.setattr(p._Pass,'move',get_move)
    monkeypatch.setattr(p._Pass,'branch',get_branch)
    try:
        before=o._ids(f,span)
        out=p._run_owned_move_matchers(f,s,span,7)
        assert o._ids(f,out)==before
        assert events==([] if kind=='no_move' else ['move','branch'])
    finally:s.close();f.close()


def test_mode7_existing_move_still_raises_branch_before_producer(monkeypatch):
    f,s,span,_=pair(7,'wsp')
    events=[];error=RuntimeError('branch matching primary')
    def fail(*args):events.append('branch');raise error
    def forbidden(*args):raise AssertionError('producer reached after failing branch')
    monkeypatch.setattr(p._Pass,'branch',fail)
    monkeypatch.setattr(p._Pass,'new_branch',forbidden)
    try:
        with pytest.raises(RuntimeError) as caught:p._run_owned_move_matchers(f,s,span,7)
        assert caught.value is error and events==['branch']
    finally:s.close();f.close()


@pytest.mark.parametrize('source,lhs,rhs,changed',[('wsp','wzr','w9',False),('wsp','w9','w19',True),('x20','xzr','x9',True)])
def test_private_mode6_independent_live_flags(monkeypatch,source,lhs,rhs,changed):
    calls=select(monkeypatch)
    compare.check(source,lhs,rhs,changed)
    assert (6,True) in calls


@pytest.mark.parametrize('name',['target_live','WSP_branch_domain'])
def test_private_mode7_independent_cfg(monkeypatch,name):
    calls=select(monkeypatch)
    branch_live.check(7,name)
    assert (7,True) in calls


@pytest.mark.parametrize('mode',[6,7])
def test_public_full_runner_does_not_choose_owned(monkeypatch,mode):
    f,s,span,_=pair(mode)
    def forbidden(*args):raise AssertionError('public selected private matcher route')
    monkeypatch.setattr(p,'_run_owned_move_matchers',forbidden)
    try:p.run_aarch64_typed_peepholes(f,s,span)
    finally:s.close();f.close()


@pytest.mark.parametrize('plane',['records','headers','tokens'])
def test_native_storage_gate_calls_old_signature(monkeypatch,plane):
    f,s,span,_=pair(6)
    arena=f.records if plane=='records' else getattr(s,plane)
    calls=[]
    def fallback(*args):calls.append(args);return span
    monkeypatch.setattr(p,'_run',fallback)
    try:
        arena._address=1
        assert p._run_owned_move_matchers(f,s,span,6)==span
        assert calls==[(f,s,span,6)]
    finally:arena._address=0;s.close();f.close()


@pytest.mark.parametrize('mode',[6,7])
def test_timer_rechecked_after_real_begin(monkeypatch,mode):
    from pcc.backend import indexed_phase_timing as timing
    timer=timing.ModulePhaseTiming('move-select-boundary')
    f,s,span,_=pair(mode)
    phase='typed_fold_mov_compare_source' if mode==6 else 'typed_fold_mov_zero_branch_source'
    injected=False;calls=[];run=p._run
    def event(*args,**kwargs):
        nonlocal injected
        if len(args)>=4 and args[1]==phase and args[3]=='begin' and not injected:
            injected=True;timer.complete_backend_finalize=lambda *args:None
    def observe(f,s,span,selected,owned_matcher_only=False):
        calls.append((selected,owned_matcher_only))
        return run(f,s,span,selected,owned_matcher_only)
    monkeypatch.setattr(timing,'_write_event',event)
    monkeypatch.setattr(p,'_run',observe)
    try:
        p._run_aarch64_typed_peepholes(f,s,span,phase_timing=timer,owned_stack_windows=True)
        assert injected and (mode,False) in calls and (mode,True) not in calls
    finally:s.close();f.close()


@pytest.mark.parametrize('private',[False,True])
def test_invalid_branch_provenance_is_rejected_before_matchers(monkeypatch,private):
    f,s,span,_=pair(7,'no_move')
    before=o._ids(f,span)
    events=[]
    def forbidden(*args):events.append('matcher');raise AssertionError('matcher before initial validation')
    monkeypatch.setattr(p._Pass,'move',forbidden)
    monkeypatch.setattr(p._Pass,'branch',forbidden)
    try:
        # The first instruction is valid MOVZ, so move(a) would return None.
        # Corruption in the next record must still fail the initial full walk.
        s.headers.set(before[1]*4,0)
        with pytest.raises(o.enc.EncodeError,match='AArch64 instruction provenance is unproven'):
            if private:p._run_owned_move_matchers(f,s,span,7)
            else:p.fold_mov_zero_branch_source(f,s,span)
        assert events==[]
        assert o._ids(f,span)==before
    finally:s.close();f.close()
