"""Resource protocol tests: real host arena owners; not native malloc evidence."""
import pytest
from pcc.backend import self_backend_precise_stackmaps as maps
from pcc.backend.self_backend_value_arena import CompilerIntArena

FIELDS = ('scalars','spans','location_scalars','location_group_spans','reload_scalars',
          'entry_routes','suffix_routes','terminator_routes','entry_route_spans',
          'suffix_route_spans','terminator_route_spans')

class Cancellation(BaseException):
    pass

@pytest.mark.parametrize('fail_at', range(2,12))
@pytest.mark.parametrize('error_type', (MemoryError, Cancellation))
def test_constructor_releases_prior_owners_and_preserves_error(monkeypatch, fail_at, error_type):
    created=[]
    primary=error_type('allocation injection')
    def allocate(*args, **kwargs):
        if len(created)+1==fail_at:
            raise primary
        arena=CompilerIntArena(*args,**kwargs)
        created.append(arena)
        return arena
    monkeypatch.setattr(maps,'CompilerIntArena',allocate)
    try:
        with pytest.raises(error_type) as caught:
            maps.PackedPlannedSafepoints(capacity=3)
        assert caught.value is primary
        assert len(created)==fail_at-1
        assert all(arena._closed for arena in created)
    finally:
        for arena in created: arena.close()

def test_success_retains_all_owners_and_borrowed_block_names():
    names=['entry','done']
    packed=maps.PackedPlannedSafepoints(capacity=3,block_names=names)
    try:
        assert packed.block_names is names
        assert len({id(getattr(packed,n)) for n in FIELDS})==11
        assert all(not getattr(packed,n)._closed for n in FIELDS)
        assert len(packed)==0
    finally:
        packed.close()
    assert names==['entry','done']

@pytest.mark.parametrize('allocation_index', range(11))
@pytest.mark.parametrize('after_append', (False, True))
def test_pending_owner_at_append_boundary(monkeypatch, allocation_index, after_append):
    import inspect
    import sys
    ctor=maps.PackedPlannedSafepoints.__init__
    lines,start=inspect.getsourcelines(ctor)
    targets=[start+i for i,line in enumerate(lines) if line.strip()=='allocated.append(pending)']
    assert len(targets)==11
    line_number=targets[allocation_index]+(1 if after_append else 0)
    created=[];closed=[]
    original_close=CompilerIntArena.close
    primary=MemoryError('append boundary injection')
    def allocate(*args,**kwargs):
        arena=CompilerIntArena(*args,**kwargs);created.append(arena);return arena
    def close(arena):
        closed.append(arena)
        original_close(arena)
    def trace(frame,event,arg):
        if frame.f_code is ctor.__code__ and event=='line' and frame.f_lineno==line_number:
            raise primary
        return trace
    monkeypatch.setattr(maps,'CompilerIntArena',allocate)
    monkeypatch.setattr(CompilerIntArena,'close',close)
    try:
        sys.settrace(trace)
        with pytest.raises(MemoryError) as caught:
            maps.PackedPlannedSafepoints(capacity=3)
        sys.settrace(None)
        assert caught.value is primary
        assert len(created)==allocation_index+1
        assert len(closed)==len(created)
        assert {id(x) for x in closed}=={id(x) for x in created}
        assert all(x._closed for x in created)
    finally:
        sys.settrace(None)
        for arena in created:original_close(arena)

@pytest.mark.parametrize('cleanup_failure_at', (0,3,9))
def test_cleanup_failure_preserves_primary_and_continues(monkeypatch,cleanup_failure_at):
    created=[];closed=[]
    original_close=CompilerIntArena.close
    primary=MemoryError('final allocation failure')
    def allocate(*args,**kwargs):
        if len(created)==10:raise primary
        arena=CompilerIntArena(*args,**kwargs);created.append(arena);return arena
    def close(arena):
        closed.append(arena)
        if arena is created[cleanup_failure_at]:raise Cancellation('cleanup failure')
        original_close(arena)
    monkeypatch.setattr(maps,'CompilerIntArena',allocate)
    monkeypatch.setattr(CompilerIntArena,'close',close)
    try:
        with pytest.raises(MemoryError) as caught:maps.PackedPlannedSafepoints(capacity=3)
        assert caught.value is primary
        assert len(closed)==10
        assert {id(x) for x in closed}=={id(x) for x in created}
        assert all(x._closed for i,x in enumerate(created) if i!=cleanup_failure_at)
        assert not created[cleanup_failure_at]._closed
    finally:
        for arena in created:original_close(arena)
