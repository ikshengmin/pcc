import pytest
from pcc.backend import self_backend_aarch64_typed_peepholes as typed
from tests.python import test_aarch64_owned_stack as owned
from tests.python import test_aarch64_typed_peepholes as oracle

def test_legacy_stack_negative_id_keeps_short_circuit():
    from pcc.backend import self_backend_aarch64_owned_stack as kernel
    class Negative(int):
        def __mul__(self, other):
            raise AssertionError('negative id multiplication')
    assert kernel.stack(([], [], []), Negative(-1), kernel.STUR) == (-1, 0, 0, 0, 0)

def test_compact_snapshot_is_local_and_keeps_arbitrary_precision():
    values = [1 << 100, 1 << 90, -8, 0, 99, -3, 0, -1]
    snapshot = list(values)
    assert typed._compact_stack_record_snapshot(snapshot)
    assert snapshot == [1 << 90, -3]
    assert values == [1 << 100, 1 << 90, -8, 0, 99, -3, 0, -1]

@pytest.mark.parametrize('where', range(4))
def test_int_subclass_falls_back_without_comparison_or_mutation(where):
    class Value(int):
        def __eq__(self, other):
            raise AssertionError('comparison hook')
    values = [0, 1, 2, 3]
    payload = Value(7)
    values[where] = payload
    before = list(values)
    assert not typed._compact_stack_record_snapshot(values)
    assert all(a is b for a, b in zip(values, before))

@pytest.mark.parametrize('failure', [1, 2, 3, 4])
def test_mode2_copy_and_compaction_failure_keeps_source_and_closes_scratch(monkeypatch, failure):
    fragments, semantics, span, _ = oracle._fixture(owned.items(2, 'basic'))
    before = oracle._ids(fragments, span)
    contexts = []
    original_init = typed._Pass.__init__
    primary = MemoryError('snapshot failure')
    def initialize(self, *args):
        original_init(self, *args)
        contexts.append(self)
    calls = []
    def copy(values):
        calls.append(len(values))
        if len(calls) == failure:
            raise primary
        return list(values)
    def fail_compact(records):
        raise primary
    monkeypatch.setattr(typed._Pass, '__init__', initialize)
    monkeypatch.setattr(typed, 'list', copy, raising=False)
    if failure == 4:
        monkeypatch.setattr(typed, '_compact_stack_record_snapshot', fail_compact)
    try:
        with pytest.raises(MemoryError) as caught:
            typed._run_owned_stack(fragments, semantics, span, 2)
        assert caught.value is primary
        assert len(calls) == (1 if failure == 4 else failure)
        assert contexts[0].ids._closed and contexts[0].output._closed
        assert oracle._ids(fragments, span) == before
    finally:
        fragments.close()
