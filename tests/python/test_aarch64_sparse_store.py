"""Phase-local necessary-opcode positions, not cross-phase cached validation."""
import inspect
import pytest
from pcc.backend import self_backend_aarch64_typed_peepholes as p
from tests.python import test_aarch64_typed_peepholes as o
from tests.python import test_aarch64_store_source_address as safety
from tests.python import test_aarch64_owned_store_select as selection


def route(monkeypatch):
    original=p._run;calls=[]
    def dispatch(f,s,span,mode,owned_matcher_only=False):
        calls.append((mode,owned_matcher_only))
        if owned_matcher_only:return original(f,s,span,mode,True)
        return p._run_owned_store(f,s,span,mode)
    monkeypatch.setattr(p,'_run',dispatch)
    return calls


@pytest.mark.parametrize('private',[False,True])
def test_noncandidate_skips_move_only_in_private_route(monkeypatch,private):
    f,s,span,_=o._fixture([o.I('mov',o.R('x9'),o.R('x19')),o.I('movz',o.R('x0'),o.N(0))])
    calls=[];move=p._Pass.move
    def seen(self,rid):calls.append(rid);return move(self,rid)
    monkeypatch.setattr(p._Pass,'move',seen)
    try:
        ids=o._ids(f,span)
        result=p._run_owned_store(f,s,span,4) if private else p.fold_mov_store_source(f,s,span)
        assert o._ids(f,result)==ids
        assert calls==([] if private else ids[:1]), 'noncandidate matcher oracle'
    finally:s.close();f.close()


def test_candidate_keeps_old_matcher_and_producer_order(monkeypatch):
    f,s,span,_=safety.fixture(4);events=[]
    for name in ('move','store_address_uses','store_replacement_safe','dead','changed_register'):
        fn=getattr(p._Pass,name)
        def seen(self,*args,_name=name,_fn=fn):events.append(_name);return _fn(self,*args)
        monkeypatch.setattr(p._Pass,name,seen)
    try:
        out=p._run_owned_store(f,s,span,4)
        assert len(o._ids(f,out))==1
        assert events==['move','store_address_uses','store_replacement_safe','dead','changed_register']
    finally:s.close();f.close()


@pytest.mark.parametrize('event',['L_barrier:',''])
def test_event_breaks_adjacency(monkeypatch,event):
    f,s,span,_=o._fixture([o.I('mov',o.R('x9'),o.R('x19')),event,o.I('stur',o.R('x9'),o.A())])
    calls=[];move=p._Pass.move
    def seen(self,rid):calls.append(rid);return move(self,rid)
    monkeypatch.setattr(p._Pass,'move',seen)
    try:
        ids=o._ids(f,span);out=p._run_owned_store(f,s,span,4)
        assert o._ids(f,out)==ids and calls==[]
    finally:s.close();f.close()


def test_consumed_windows_and_duplicate_record_occurrences(monkeypatch):
    f,s,span,_=safety.fixture(4);calls=[];move=p._Pass.move
    def seen(self,rid):calls.append(rid);return move(self,rid)
    monkeypatch.setattr(p._Pass,'move',seen)
    try:
        pair=o._ids(f,span);f.extend_fragment(span,span)
        original=o._ids(f,span)
        out=p._run_owned_store(f,s,span,4);ids=o._ids(f,out)
        assert original==pair*2 and o._ids(f,span)==original
        assert len(ids)==2 and calls==[pair[0],pair[0]], 'occurrence and consumption oracle'
        before=[f.records.get4_unchecked(i).first for i in original]
        after=[f.records.get4_unchecked(i).first for i in ids]
        assert safety.decode(after)==safety.decode(before)
    finally:s.close();f.close()


@pytest.mark.parametrize('kwargs,changed',[
    (dict(base='x9'),False),(dict(old='w9',source='w19',base='x9'),False),
    (dict(old='w9',source='wsp'),False),
    (dict(source='x19',base='x19',address='post'),False),
    (dict(source='x19',base='x19'),True),
])
def test_private_existing_independent_store_safety(monkeypatch,kwargs,changed):
    calls=route(monkeypatch);before,after=safety.run_case(4,changed,**kwargs)
    assert (4,True) in calls
    if kwargs.get('address','base')=='base':assert safety.decode(after)==safety.decode(before)


@pytest.mark.parametrize('private',[False,True])
def test_initial_bad_provenance_before_sparse_skips(monkeypatch,private):
    f,s,span,_=o._fixture([o.I('movz',o.R('x0'),o.N(0))]);calls=[]
    def forbidden(*args):calls.append(True);raise AssertionError('matcher before validation')
    monkeypatch.setattr(p._Pass,'move',forbidden)
    try:
        ids=o._ids(f,span);s.headers.set(ids[0]*4,0)
        with pytest.raises(o.enc.EncodeError,match='AArch64 instruction provenance is unproven'):
            if private:p._run_owned_store(f,s,span,4)
            else:p.fold_mov_store_source(f,s,span)
        assert calls==[]
    finally:s.close();f.close()


@pytest.mark.parametrize('plane',['records','headers','tokens'])
def test_native_gate_before_sparse_access(monkeypatch,plane):
    f,s,span,_=safety.fixture(4);arena=f.records if plane=='records' else getattr(s,plane);calls=[]
    def fallback(*args):calls.append(args);return span
    monkeypatch.setattr(p,'_run',fallback)
    try:
        arena._address=1
        assert p._run_owned_store(f,s,span,4)==span and calls==[(f,s,span,4)]
    finally:arena._address=0;s.close();f.close()


def test_real_begin_rechecks_timer(monkeypatch):
    selection.test_real_phase_begin_mutation_falls_back_this_round(monkeypatch,4)


def test_restore_noncandidate_matching_mutant_is_detected(monkeypatch):
    test_noncandidate_skips_move_only_in_private_route(monkeypatch,True)
    source=inspect.getsource(p._run)
    old='if sparse_cursor >= len(sparse_positions) or sparse_positions[sparse_cursor] != index:'
    assert source.count(old)==1
    namespace=dict(p._run.__globals__)
    exec(compile(source.replace(old,'if False:'),'<sparse-mutant>','exec'),namespace)
    monkeypatch.setattr(p,'_run',namespace['_run'])
    with pytest.raises(AssertionError,match=r'^noncandidate matcher oracle(?:\n|$)'):
        test_noncandidate_skips_move_only_in_private_route(monkeypatch,True)


def test_missing_str_candidate_mutant_is_detected(monkeypatch):
    original=p._run
    source=inspect.getsource(original);old='(stur_opcode, str_opcode, sturb_opcode, strb_opcode)'
    assert source.count(old)==1
    # Positive control uses the same independent word/shape oracle.
    with monkeypatch.context() as patch:
        route(patch);safety.run_case(4,True)
    namespace=dict(original.__globals__)
    exec(compile(source.replace(old,'(stur_opcode, sturb_opcode, strb_opcode)'),'<sparse-mutant>','exec'),namespace)
    monkeypatch.setattr(p,'_run',namespace['_run']);route(monkeypatch)
    with pytest.raises(AssertionError,match=r'^store rewrite output length(?:\n|$)'):
        safety.run_case(4,True)


@pytest.mark.parametrize('opcode,width',[('str','x'),('stur','x'),('strb','w'),('sturb','w')])
def test_each_store_opcode_is_a_real_private_candidate(monkeypatch,opcode,width):
    # This is a real encoding/shape differential, not a second ISA simulator.
    address=o.A() if opcode in ('stur','sturb') else (o.CompilerInt4(o.sem.TOKEN_ADDRESS,o.sem.ADDRESS_BASE,1,0),o.R('x29'))
    seq=[o.I('mov',o.R(width+'9'),o.R(width+'19')),
         o.I(opcode,o.R(width+'9'),address)]
    f,s,span,lines=o._fixture(seq);calls=[];move=p._Pass.move
    def observed(self,rid):calls.append(rid);return move(self,rid)
    monkeypatch.setattr(p._Pass,'move',observed)
    try:
        ids=o._ids(f,span)
        public=p.fold_mov_store_source(f,s,span)
        public_words=[f.records.get4_unchecked(rid).first for rid in o._ids(f,public)]
        calls.clear()
        private=p._run_owned_store(f,s,span,4)
        private_words=[f.records.get4_unchecked(rid).first for rid in o._ids(f,private)]
        expected_address='[x29, #-8]' if opcode in ('stur','sturb') else '[x29]'
        expected_line='  '+opcode+' '+width+'19, '+expected_address
        raw=o.enc.assemble_text(expected_line).code
        expected=[int.from_bytes(raw[i:i+4],'little') for i in range(0,len(raw),4)]
        assert len(expected)==1
        assert private_words==public_words==expected, 'all store opcodes output oracle'
        assert calls==ids[:1], 'all store opcodes candidate oracle'
        assert o._ids(f,span)==ids
    finally:s.close();f.close()
