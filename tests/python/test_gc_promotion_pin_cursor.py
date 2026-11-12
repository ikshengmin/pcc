"""ObjectNode@56: independent NoRC conservative pin and bounded cursor."""
import pytest

from pcc.runtime.py import py_abi_constants as abi
from tests.python.test_gc3_norc_collection_entry import PipelineGC3
from tests.python.test_gc3_norc_source_retirement import GC3


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
