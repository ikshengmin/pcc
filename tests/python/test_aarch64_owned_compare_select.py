"""Private mode6 compare-first selection preserves validation and ISA guards."""
import pytest
from pcc.backend import self_backend_aarch64_typed_peepholes as p
from tests.python import test_aarch64_typed_peepholes as o
from tests.python import test_aarch64_mov_compare_sp as model
from tests.python import test_aarch64_owned_move_select as selection


def fixture(kind):
    dest='w8' if kind=='scratch' else 'w9'
    src='wsp' if kind=='wsp' else 'w20'
    second=o.I('movz',o.R('w0'),o.N(0)) if kind=='none' else o.I('cmp',o.R('w19'),o.R(dest))
    return o._fixture([o.I('mov',o.R(dest),o.R(src)),second])


@pytest.mark.parametrize('private',[False,True])
@pytest.mark.parametrize('kind',['none','scratch','wsp'])
def test_compare_then_conditional_move(monkeypatch,private,kind):
    f,s,span,_=fixture(kind);events=[]
    for name in ('compare','zero','move'):
        original=getattr(p._Pass,name)
        def observed(self,*args,_fn=original,_name=name):events.append(_name);return _fn(self,*args)
        monkeypatch.setattr(p._Pass,name,observed)
    try:
        ids=o._ids(f,span)
        out=p._run_owned_move_matchers(f,s,span,6) if private else p.fold_mov_compare_source(f,s,span)
        assert o._ids(f,out)==ids
        expected=['compare'] if private and kind=='none' else ['compare','move'] if private else ['compare','zero','move']
        assert events==expected, 'compare selection oracle'
    finally:s.close();f.close()


def test_mode5_not_changed_by_private_flag(monkeypatch):
    f,s,span,_=fixture('none');events=[]
    for name in ('compare','zero','move'):
        original=getattr(p._Pass,name)
        def observed(self,*args,_fn=original,_name=name):events.append(_name);return _fn(self,*args)
        monkeypatch.setattr(p._Pass,name,observed)
    try:
        p._run(f,s,span,5,True)
        assert events==['compare','zero','move']
    finally:s.close();f.close()


@pytest.mark.parametrize('private',[False,True])
def test_first_bad_provenance_precedes_compare_none(monkeypatch,private):
    f,s,span,_=fixture('none');events=[]
    def forbidden(*args):events.append('compare');raise AssertionError('matcher before initial validation')
    monkeypatch.setattr(p._Pass,'compare',forbidden)
    try:
        ids=o._ids(f,span);s.headers.set(ids[0]*4,0)
        with pytest.raises(o.enc.EncodeError,match='AArch64 instruction provenance is unproven'):
            if private:p._run_owned_move_matchers(f,s,span,6)
            else:p.fold_mov_compare_source(f,s,span)
        assert events==[]
    finally:s.close();f.close()


def test_compare_exception_precedes_move(monkeypatch):
    f,s,span,_=fixture('none');events=[];error=RuntimeError('compare primary')
    def fail(*args):events.append('compare');raise error
    def forbidden(*args):raise AssertionError('move after compare failure')
    monkeypatch.setattr(p._Pass,'compare',fail);monkeypatch.setattr(p._Pass,'move',forbidden)
    try:
        with pytest.raises(RuntimeError) as caught:p._run_owned_move_matchers(f,s,span,6)
        assert caught.value is error and events==['compare']
    finally:s.close();f.close()


@pytest.mark.parametrize('source,lhs,rhs,changed',[
    ('wsp','wzr','w9',False),('wsp','wsp','w9',False),
    ('wsp','w9','w19',True),('x20','xzr','x9',True),
])
def test_private_live_nzcv_and_wsp_domains(monkeypatch,source,lhs,rhs,changed):
    calls=selection.select(monkeypatch)
    model.check(source,lhs,rhs,changed)
    assert (6,True) in calls


def test_phase_begin_mutation_uses_old_path(monkeypatch):
    selection.test_timer_rechecked_after_real_begin(monkeypatch,6)
