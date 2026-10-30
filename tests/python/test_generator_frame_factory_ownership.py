"""A bulk frame owns its partial list and preserves errors at every boundary."""

import pytest

from tests.python.test_collection_factory_allocation_errors import (
    ListFactoryModel,
    RUNTIME,
)
from tests.python.test_foreign_address_leases import _functions


class FrameFactoryModel(ListFactoryModel):
    def __init__(self, count, failure=None, pending=None, relocate=False):
        self.count = count
        self.none = 4000
        self.exception_kinds = []
        super().__init__(failure, pending, relocate)
        self.ns.update({
            'i64': int,
            'C_POINTER_SIZE': 8,
            '_GEN_FRAME_FACTORY_VALUE': 0,
            '_GEN_FRAME_FACTORY_ERROR': 1,
            '_GEN_FRAME_FACTORY_SLOT_COUNT': 2,
            'global_load_ptr': self.global_value,
            'py_current_exception': lambda: self.pending,
            'py_runtime_error_if_unset': self.error_if_unset,
        })
        _functions(
            RUNTIME / 'py_gen.py',
            {'_require_result', '_gen_frame_factory_finish', '_gen_frame_factory_oom', 'py_gen_frame_new'},
            self.ns,
        )

    def exception(self, kind, message):
        self.exception_kinds.append(kind)
        return super().exception(kind, message)

    def global_value(self, name):
        assert name == 'py_None'
        return self.none

    def error_if_unset(self, helper, message):
        self.boundary()
        if self.pending is None:
            self.pending = message

    def check_shape(self, obj):
        length = self.memory.get(obj + 16)
        assert length in (0, self.count)
        assert self.memory.get(obj + 24) == max(4, self.count)
        assert obj + 32 in self.memory, 'collector-visible shape is uninitialized'
        if length:
            items = self.memory[obj + 32]
            assert items is not None
            assert all(self.memory[items + index * 8] == self.none for index in range(length))


@pytest.mark.parametrize('relocate', (False, True))
@pytest.mark.parametrize('pending', (None, 'original-error'))
@pytest.mark.parametrize('failure, message', (
    ('header', 'generator frame allocation failed'),
    ('items', 'generator frame slots allocation failed'),
    ('lease', 'generator frame address lease failed'),
))
def test_frame_factory_failure_preserves_pending_error(failure, message, pending, relocate):
    model = FrameFactoryModel(7, failure, pending, relocate)
    assert model.ns['py_gen_frame_new'](7) is None
    assert model.pending == (pending or message)
    assert model.disposals == (0 if failure == 'header' else 1)
    assert model.exception_kinds == ([19] if failure in ('header', 'items') and pending is None else [])
    assert not model.objects and not model.frames and not model.leases and not model.pins


@pytest.mark.parametrize('relocate', (False, True))
@pytest.mark.parametrize('count', (0, 1, 4, 7, 64))
def test_frame_factory_success_transfers_filled_current_owner(count, relocate):
    model = FrameFactoryModel(count, relocate=relocate)
    result = model.ns['py_gen_frame_new'](count)
    assert result in model.objects and model.refs[result] == 1
    model.check_shape(result)
    assert model.memory[result + 16] == count
    assert model.pending is None and model.disposals == 0
    assert not model.frames and not model.leases and not model.pins


@pytest.mark.parametrize('relocate', (False, True))
def test_frame_factory_handoff_reuses_live_address_lease(relocate):
    model = FrameFactoryModel(7, relocate=relocate)
    def forbidden_lock():
        pytest.fail('a private leased factory result needs no graph transaction')
    def leased_pin(obj):
        assert any(model.load(slot, 0) == obj for slot in model.leases)
        model.pin(obj)
    model.ns['pcc_py_gc_minor_graph_lock'] = forbidden_lock
    model.ns['pcc_py_gc_minor_graph_unlock'] = forbidden_lock
    model.ns['pcc_gc_pin'] = leased_pin
    result = model.ns['py_gen_frame_new'](7)
    assert result in model.objects and model.refs[result] == 1
    model.check_shape(result)
    assert not model.frames and not model.leases and not model.pins


@pytest.mark.parametrize('pending', (None, 'original-error'))
@pytest.mark.parametrize('count', (-1, 134217729))
def test_frame_factory_rejects_invalid_size_before_allocation(count, pending):
    model = FrameFactoryModel(count, pending=pending)
    assert model.ns['py_gen_frame_new'](count) is None
    assert model.pending == (pending or 'invalid generator frame size')
    assert not model.objects and not model.frames and not model.leases and not model.pins
