"""Configuration and ordinary-park contracts for iterator and tuple.

These tests preserve operation and semantic assertions without claiming GC4
copy/remap, machine-root scanning, unregister-triggered relocation, or native
execution."""
import pytest

from pcc.runtime.py import py_abi_constants as abi
from tests.python.runtime_collector_contract_model import (
    CollectorConfiguration,
)
from test_runtime_iterator_roots import _IteratorMemory
from test_runtime_tuple_roots import _TupleMemory


_ConfiguredIterator = _IteratorMemory
_ConfiguredTuple = _TupleMemory


@pytest.mark.parametrize("backend", range(5))
@pytest.mark.parametrize("requested_count_free", (False, True))
@pytest.mark.parametrize("provenance", (0, 2))
def test_collector_configuration_normalizes_ownership(
    backend, requested_count_free, provenance
):
    policy = CollectorConfiguration(backend, requested_count_free, provenance)
    assert policy.cells["pcc_gc_backend4_no_refcounts"] == int(backend != 0)
    # An inconsistent pre-configuration value is normalized, not skipped.
    assert policy.requested_count_free == requested_count_free


def _assert_real_park_and_slots(memory, aliases):
    assert memory.ordinary_parks and "graph_unlock" in memory.ordinary_parks
    assert memory.init_copy_events and all(event[0] == 1 for event in memory.init_copy_events)
    assert memory.init_ptr_body.__code__.co_filename == str(memory.init_ptr_source)
    assert memory.init_ptr_body.__name__ == "pcc_gc_try_init_ptr_locked"
    assert memory.init_ptr_body.__globals__ is memory.namespace
    assert all(value.alive for value in aliases)
    assert memory.forward == {} and memory.moves == 0
    # Stack storage came from actual production stack_alloc calls. The
    # list's items array is heap storage, not an invented stack root.
    assert memory.stack_slots
    assert all(frame is not memory.items for frame in memory.stack_slots)
    assert any(value in frame.fields.values()
               for frame in memory.stack_slots for value in aliases)
    assert memory.items in memory.payloads
    assert not memory.native_frames and not memory.native_ranges
    memory.assert_balanced()


@pytest.mark.parametrize("backend", (3, 4))
@pytest.mark.parametrize("gate", (1, 2, 3, 4, 5))
@pytest.mark.parametrize("after_unlink", (False, True))
def test_sequence_next_uses_configured_collector(backend, gate, after_unlink):
    # Preserve all original constructor inputs, including after_unlink.
    # Those old unregister ordinals are no longer runtime boundaries.
    memory = _ConfiguredIterator(backend=backend, gate=gate,
                                 after_unlink=after_unlink)
    item, iterator, source = memory.item, memory.iterator, memory.lst
    result = memory.next()
    assert result is item and result.alive and result.references == 2
    assert iterator.fields[24] == 1 and iterator.fields[16] is source
    assert memory.error is None
    assert memory.unregister_calls == 0
    assert (memory.gate, memory.after_unlink) == (gate, after_unlink)
    _assert_real_park_and_slots(memory, (item, iterator, source))


@pytest.mark.parametrize("backend", (3, 4))
@pytest.mark.parametrize("phase", ("unregister", "graph_lock", "graph_unlock", "retain_finish",
                                    "allocation", "store", "publish", "decref",
                                    "register_before", "register_after"))
@pytest.mark.parametrize("after_unlink", (False, True))
def test_tuple_from_list_uses_configured_collector(backend, phase, after_unlink):
    memory = _ConfiguredTuple(backend=backend, phase=phase,
                              after_unlink=after_unlink)
    item, source = memory.item, memory.lst
    result = memory.convert()
    assert result is memory.out and result.alive and result.references == 1
    assert memory.read(result, abi.PYTUPLEOBJECT_LEN_OFFSET) == 2
    assert memory.read(result, abi.PYTUPLEOBJECT_ITEMS_OFFSET) is item
    assert memory.read(result, abi.PYTUPLEOBJECT_ITEMS_OFFSET + 8) is item
    assert item.references == 4
    assert memory.error is None
    assert (memory.phase, memory.after_unlink) == (phase, after_unlink)
    _assert_real_park_and_slots(memory, (item, source, result))
