"""Identity mode14: private scan reduction without bypassing validation."""
import pytest
from pcc.backend import self_backend_aarch64_typed_peepholes as p
from pcc.backend.self_backend_value_arena import CompilerIntArena
from tests.python import test_aarch64_typed_peepholes as o
from tests.python import test_aarch64_branch_live_registers as live
from tests.python import test_aarch64_typed_cfg_snapshot as snapshots


def fixture():
    seq=next(x[2] for x in live.cases() if x[:2]==(14,'register_live'))
    return o._fixture(seq)


def test_private_identity_words_ids_and_scan_witness(monkeypatch):
    f,s,span,lines=fixture()
    events=[];label=p._Pass.function_label
    def observed(self,rid):events.append(('label',rid));return label(self,rid)
    def stack(self,*args):events.append(('inventory',args));return None
    monkeypatch.setattr(p._Pass,'function_label',observed)
    monkeypatch.setattr(p._Pass,'stack',stack)
    try:
        before=o._ids(f,span)
        labels={rid:lines[i] for i,rid in enumerate(before) if lines[i].endswith(':')}
        words,_=live.read(f,s,span,labels)
        nodes=len(f.spans.nodes)
        out=p._run_owned_move_matchers(f,s,span,14)
        after,_=live.read(f,s,out,labels)
        assert after==words and live.execute(after)==live.execute(words), 'identity words oracle'
        assert o._ids(f,out)==before and out!=span
        assert len(f.spans.nodes)==nodes
        # The host route visits every record without an instruction header
        # (labels, directives, empty events) and skips instructions, whose
        # function_label is False without any check that can fail.
        events_only=[rid for rid in before if f.records.get4_unchecked(rid).second<0]
        assert set(labels)<=set(events_only)
        assert events==[('label',rid) for rid in events_only], 'private scan oracle'
    finally:s.close();f.close()


def test_snapshot_root_cursor_and_selfextend():
    f,s,span,_=fixture();cursor=CompilerIntArena()
    try:
        before=snapshots.ids(f,span)
        f.spans.start_cursor(span,cursor)
        first=f.spans.next_record(cursor)
        out=p._run_owned_move_matchers(f,s,span,14)
        f.spans.append(span,before[0])
        assert snapshots.ids(f,out)==before
        f.extend_fragment(out,out)
        assert snapshots.ids(f,out)==before*2
        assert snapshots.ids(f,span)==before+before[:1]
        rest=[]
        while True:
            rid=f.spans.next_record(cursor)
            if rid<0:break
            rest.append(rid)
        assert [first]+rest==before
    finally:cursor.close();s.close();f.close()


def test_bad_count_rebuilds_via_finish(monkeypatch):
    f,s,span,_=fixture();calls=[];finish=p._Pass.finish
    def seen(self):calls.append(True);return finish(self)
    monkeypatch.setattr(p._Pass,'finish',seen)
    try:
        before=o._ids(f,span);root=f.spans._root(span)
        f.spans.spans.set2_unchecked(span.first*2,root.first,root.second+7)
        out=p._run_owned_move_matchers(f,s,span,14)
        assert calls==[True]
        assert o._ids(f,out)==before and f.spans.length(out)==len(before)
    finally:s.close();f.close()


@pytest.mark.parametrize('private',[False,True])
@pytest.mark.parametrize('late_bad_provenance',[False,True])
def test_label_first_error_but_full_initial_validation_has_priority(private,late_bad_provenance):
    f,s,span,_=o._fixture(['func:','L_first:','L_second:',o.I('movz',o.R('w9'),o.N(0))])
    try:
        ids=o._ids(f,span)
        # First bad label is a non-string; later one has an out-of-range index.
        code=f.records.get4_unchecked(ids[1]);f.symbol_names[code.first]=7
        f.records.set(ids[2]*4,10**6)
        if late_bad_provenance:s.headers.set(ids[-1]*4,0)
        cls=o.enc.EncodeError if late_bad_provenance else AttributeError
        match='AArch64 instruction provenance is unproven' if late_bad_provenance else 'startswith'
        with pytest.raises(cls,match=match):
            if private:p._run_owned_move_matchers(f,s,span,14)
            else:p.drop_dead_cset_branch_stores(f,s,span)
        assert o._ids(f,span)==ids
    finally:s.close();f.close()


def test_public_retains_inventory_path(monkeypatch):
    f,s,span,_=fixture();calls=[];stack=p._Pass.stack
    def observed(self,*args):calls.append(args);return stack(self,*args)
    monkeypatch.setattr(p._Pass,'stack',observed)
    try:
        before=o._ids(f,span)
        out=p.drop_dead_cset_branch_stores(f,s,span)
        assert calls and o._ids(f,out)==before
    finally:s.close();f.close()


@pytest.mark.parametrize('plane',['records','headers','tokens'])
def test_native_planes_use_original_signature(monkeypatch,plane):
    f,s,span,_=fixture();arena=f.records if plane=='records' else getattr(s,plane);calls=[]
    def fallback(*args):calls.append(args);return span
    monkeypatch.setattr(p,'_run',fallback)
    try:
        arena._address=1
        assert p._run_owned_move_matchers(f,s,span,14)==span
        assert calls==[(f,s,span,14)]
    finally:arena._address=0;s.close();f.close()


def test_phase_begin_mutation_falls_back(monkeypatch):
    from pcc.backend import indexed_phase_timing as timing
    f,s,span,_=fixture();timer=timing.ModulePhaseTiming('noop14-boundary')
    calls=[];run=p._run;injected=False
    def event(*args,**kwargs):
        nonlocal injected
        if len(args)>=4 and args[1]=='typed_drop_dead_cset_branch_stores' and args[3]=='begin':
            injected=True;timer.complete_backend_finalize=lambda *args:None
    def observed(f,s,span,mode,owned_matcher_only=False):
        calls.append((mode,owned_matcher_only));return run(f,s,span,mode,owned_matcher_only)
    monkeypatch.setattr(timing,'_write_event',event);monkeypatch.setattr(p,'_run',observed)
    try:
        p._run_aarch64_typed_peepholes(f,s,span,phase_timing=timer,owned_stack_windows=True)
        assert injected and (14,False) in calls and (14,True) not in calls
    finally:s.close();f.close()
