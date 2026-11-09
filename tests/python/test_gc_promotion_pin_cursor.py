"""ObjectNode@56: independent NoRC conservative pin and bounded cursor."""
import pytest

from pcc.runtime.py import py_abi_constants as abi
from tests.python.test_gc3_norc_collection_entry import PipelineGC3
from tests.python.test_gc3_norc_source_retirement import GC3


@pytest.mark.parametrize('backend', [3, 4])
@pytest.mark.parametrize('pinned', [False, True])
def test_repeated_actual_generational_step_finishes_large_owner_across_pin_scans(backend, pinned):
    m = PipelineGC3()
    m.store('pcc_gc_backend_selected', 0, backend)
    m.ns['pcc_gc_config_ensure'] = lambda: backend
    m.ns['pcc_gc_backend'] = lambda: backend
    owner, node, _ = m.value(abi.PY_TYPE_LIST)
    m.store(owner, 12, 8 | 256 | 262144)
    length = 65
    items = m.malloc(length * 8)
    for index in range(length):
        m.store(items, index * 8, 2 * index + 1)
    m.store(owner, 16, length)
    m.store(owner, 24, length)
    m.store(owner, 32, items)
    if pinned:
        m.words = [owner + 16]
    m.ns['pcc_gc_backend3_enqueue_promotion_owner'](owner)
    cursors = []
    step_pins = []
    actual_slice = m.ns['pcc_gc_visit_object_slots_slice']

    def observe_slice(obj, cursor, limit, visitor, context, state):
        if obj == owner:
            cursors.append(cursor)
            query = ('pcc_gc_generational_object_is_address_pinned'
                     if backend == 3 else 'pcc_gc_object_is_address_pinned')
            step_pins.append(m.ns[query](obj))
        return actual_slice(obj, cursor, limit, visitor, context, state)

    m.ns['pcc_gc_visit_object_slots_slice'] = observe_slice
    for attempt in range(16):
        # The real owner starts each call with a fresh conservative scan.
        # Its bounded worklist opportunities still cannot complete this
        # 65-slot list in one 8-unit call.
        m.ns['pcc_gc_generational_step'](8, 0)
        if m.load('pcc_gc_backend3_promotion_head', 0) == 0:
            break
    assert m.load('pcc_gc_backend3_promotion_head', 0) == 0, {
        'cursor_word': m.load(node, 56), 'observed_cursors': cursors,
    }
    assert len(cursors) > 2 and cursors[0] == 0
    assert cursors == sorted(set(cursors))
    assert set(step_pins) == {int(pinned)}
    assert m.load(node, 56) == (int(pinned) if backend == 4 else 0)
    assert m.load('pcc_gc_backend3_step_pin_snapshot', 0) == 0


def test_cursor_alone_is_not_a_pin_and_scans_and_mark_seed_preserve_it():
    m = PipelineGC3()
    owner, node, _ = m.value()
    assert m.ns['pcc_gc_backend3_set_promotion_cursor'](node, 19) == 1
    assert m.load(node, 56) == 38
    assert m.ns['pcc_gc_object_is_address_pinned'](owner) == 0
    m.words = [owner + 16]
    m.ns['pcc_gc_pin_conservative_thread_roots']()
    assert m.load(node, 56) == 39 and m.ns['pcc_gc_object_is_address_pinned'](owner) == 1
    m.ns['pcc_gc_prepare_object_list_mark'](1)
    assert m.load(node, 56) == 38
    assert m.ns['pcc_gc_backend3_promotion_cursor'](node) == 19
    m.words.clear()
    m.ns['pcc_gc_pin_conservative_thread_roots']()
    assert m.load(node, 56) == 38
    assert m.ns['pcc_gc_backend3_set_promotion_cursor'](node, 0) == 1
    assert m.load(node, 56) == 0


@pytest.mark.parametrize('backend', [0, 3, 4])
def test_counted_representation_remains_unpacked(backend):
    m = GC3(counted=True)
    m.store('pcc_gc_backend_selected', 0, backend)
    owner, node, _ = m.value()
    assert m.ns['pcc_gc_backend3_set_promotion_cursor'](node, 19) == 1
    assert m.load(node, 56) == 19
    assert m.ns['pcc_gc_backend3_promotion_cursor'](node) == 19
    assert m.ns['pcc_gc_object_is_address_pinned'](owner) == 0
    assert m.ns['pcc_gc_backend3_set_promotion_cursor'](node, 0) == 1
    assert m.load(node, 56) == 0


def test_packed_cursor_limit_is_checked_before_any_word_write():
    m = GC3()
    owner, node, _ = m.value()
    m.store(node, 56, 1)
    maximum = 4611686018427387903
    assert m.ns['pcc_gc_backend3_set_promotion_cursor'](node, maximum) == 1
    assert m.load(node, 56) == 9223372036854775807
    assert m.ns['pcc_gc_backend3_promotion_cursor'](node) == maximum
    before = m.load(node, 56)
    with pytest.raises(AssertionError, match='runtime invariant abort'):
        m.ns['pcc_gc_backend3_set_promotion_cursor'](node, maximum + 1)
    assert m.load(node, 56) == before
