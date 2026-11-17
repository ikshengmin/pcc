"""Sparse modes3/6 preserve real qualifications and initial validation."""
import inspect
import pytest
from pcc.backend import self_backend_aarch64_typed_peepholes as p
from tests.python import test_aarch64_typed_peepholes as o
from tests.python import test_aarch64_store_source_address as store
from tests.python import test_aarch64_mov_compare_sp as flags
from tests.python import test_aarch64_owned_store_select as store_select
from tests.python import test_aarch64_owned_move_select as move_select


def invoke(f,s,span,mode):
    return p._run_owned_store(f,s,span,mode) if mode==3 else p._run_owned_move_matchers(f,s,span,mode)


def route(patch):
    original=p._run;calls=[]
    def dispatch(f,s,span,mode,owned_matcher_only=False):
        calls.append((mode,owned_matcher_only))
        if owned_matcher_only:return original(f,s,span,mode,True)
        return invoke(f,s,span,mode)
    patch.setattr(p,'_run',dispatch)
    return calls


@pytest.mark.parametrize('mode',[3,6])
@pytest.mark.parametrize('event',[None,'L_boundary:',''])
def test_sparse_skips_noncandidates_and_event_windows(monkeypatch,mode,event):
    first=o.I('movz',o.R('w9'),o.N(0)) if mode==3 else o.I('mov',o.R('w9'),o.R('w20'))
    last=o.I('sturb',o.R('w9'),o.A()) if mode==3 else o.I('cmp',o.R('w19'),o.R('w9'))
    seq=[first,o.I('movz',o.R('w0'),o.N(1))] if event is None else [first,event,last]
    f,s,span,_=o._fixture(seq);calls=[]
    def forbidden(*args):calls.append(True);raise AssertionError('noncandidate matcher oracle')
    monkeypatch.setattr(p._Pass,'zero' if mode==3 else 'compare',forbidden)
    try:
        ids=o._ids(f,span);out=invoke(f,s,span,mode)
        assert o._ids(f,out)==ids and calls==[]
    finally:s.close();f.close()


@pytest.mark.parametrize('mode',[3,6])
def test_consume_two_with_repeated_occurrences(monkeypatch,mode):
    seq=([o.I('movz',o.R('w9'),o.N(0)),o.I('sturb',o.R('w9'),o.A())] if mode==3 else
         [o.I('mov',o.R('w9'),o.R('w20')),o.I('cmp',o.R('w19'),o.R('w9'))])
    f,s,span,_=o._fixture(seq);calls=[];name='zero' if mode==3 else 'compare';original=getattr(p._Pass,name)
    def seen(self,rid):calls.append(rid);return original(self,rid)
    monkeypatch.setattr(p._Pass,name,seen)
    try:
        pair=o._ids(f,span);f.extend_fragment(span,span)
        out=invoke(f,s,span,mode)
        assert len(o._ids(f,out))==2, 'sparse consume oracle'
        assert calls==[pair[0] if mode==3 else pair[1]]*2
        assert o._ids(f,span)==pair*2
    finally:s.close();f.close()


@pytest.mark.parametrize('old,base,changed',[('x9','x9',False),('w9','x9',False),('x9','x20',True)])
def test_private_zero_address_independent_effects(monkeypatch,old,base,changed):
    calls=route(monkeypatch);before,after=store.run_case(3,changed,old=old,base=base)
    assert (3,True) in calls and store.decode(before)==store.decode(after)


@pytest.mark.parametrize('source,lhs,rhs,changed',[('wsp','wzr','w9',False),('wsp','w9','w19',True),('x20','xzr','x9',True)])
def test_private_compare_live_flags(monkeypatch,source,lhs,rhs,changed):
    calls=route(monkeypatch);flags.check(source,lhs,rhs,changed)
    assert (6,True) in calls


@pytest.mark.parametrize('mode',[3,6])
@pytest.mark.parametrize('private',[False,True])
def test_initial_validation_still_precedes_sparse_decision(monkeypatch,mode,private):
    f,s,span,_=o._fixture([o.I('movz',o.R('w0'),o.N(1))])
    try:
        rid=o._ids(f,span)[0];s.headers.set(rid*4,0)
        with pytest.raises(o.enc.EncodeError,match='AArch64 instruction provenance is unproven'):
            if private:invoke(f,s,span,mode)
            else:p._run(f,s,span,mode)
    finally:s.close();f.close()


@pytest.mark.parametrize('mode',[3,6])
def test_phase_begin_recheck_still_applies(monkeypatch,mode):
    if mode==3:store_select.test_real_phase_begin_mutation_falls_back_this_round(monkeypatch,3)
    else:move_select.test_timer_rechecked_after_real_begin(monkeypatch,6)


@pytest.mark.parametrize('mode',[3,6])
def test_missing_first_opcode_mutant_detected(monkeypatch,mode):
    # The positive control and mutant must reach the same exact consume oracle.
    with monkeypatch.context() as patch:test_consume_two_with_repeated_occurrences(patch,mode)
    source=inspect.getsource(p._run)
    old='sem.semantic_opcode_id("movz" if mode == 3 else "mov")'
    new='sem.semantic_opcode_id("mov")' if mode==3 else 'sem.semantic_opcode_id("movz")'
    assert source.count(old)==1
    namespace=dict(p._run.__globals__)
    exec(compile(source.replace(old,new),'<sparse-first-mutant>','exec'),namespace)
    monkeypatch.setattr(p,'_run',namespace['_run'])
    with pytest.raises(AssertionError,match=r'^sparse consume oracle(?:\n|$)'):
        test_consume_two_with_repeated_occurrences(monkeypatch,mode)


@pytest.mark.parametrize('mode',[3,6])
def test_native_gate_keeps_original_run_signature(monkeypatch,mode):
    f,s,span,_=o._fixture([o.I('movz',o.R('w0'),o.N(1))]);calls=[]
    def fallback(*args):calls.append(args);return span
    monkeypatch.setattr(p,'_run',fallback)
    try:
        f.records._address=1
        assert invoke(f,s,span,mode)==span and calls==[(f,s,span,mode)]
    finally:f.records._address=0;s.close();f.close()


@pytest.mark.parametrize('number',[o.N(1),o.N(0,16),o.N(0,10,o.sem.IMMEDIATE_HASH | o.sem.IMMEDIATE_NEGATIVE_ZERO)])
def test_zero_opcode_candidate_does_not_bypass_exact_immediate(monkeypatch,number):
    f,s,span,_=o._fixture([o.I('movz',o.R('w9'),number),o.I('sturb',o.R('w9'),o.A())])
    seen=[];zero=p._Pass.zero
    def observed(self,rid):seen.append(rid);return zero(self,rid)
    monkeypatch.setattr(p._Pass,'zero',observed)
    try:
        ids=o._ids(f,span);out=invoke(f,s,span,3)
        assert o._ids(f,out)==ids and seen==ids[:1]
    finally:s.close();f.close()
