"""A bulk frame owns its partial list and preserves errors at every boundary."""

import pytest

from pcc.runtime.py.py_abi_constants import (
    PYINLINEGENOBJECT_ITEMS_OFFSET,
    PYINLINEGENOBJECT_LENGTH_OFFSET,
    PY_FLAG_GEN_INLINE_FRAME,
    PY_TYPE_GEN,
)
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
        self.local_events = []
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
            'pcc_gc_frame_enter_local': self.local_enter,
            'pcc_gc_frame_leave_local': self.local_leave,
            'pcc_gc_frame_enter': self.legacy_registration,
            'pcc_gc_frame_leave': self.legacy_registration,
        })
        _functions(
            RUNTIME / 'py_gen.py',
            {'_require_result', '_gen_frame_factory_finish', '_gen_frame_factory_oom', 'py_gen_frame_new'},
            self.ns,
        )

    def global_address(self, name):
        if name == 'pcc_gen_frame_factory_map':
            return 2
        return super().global_address(name)

    def local_enter(self, count, slots):
        assert count == 2 and slots not in self.frames
        assert self.memory[slots] is None and self.memory[slots + 8] is None
        self.local_events.append(('enter', slots))
        self.enter(count, slots)

    def local_leave(self, slots):
        assert self.frames[slots] == 2
        self.local_events.append(('leave', slots))
        self.leave(slots)

    def legacy_registration(self, *args):
        pytest.fail('activation-private factory roots must not use the global registry')

    def assert_local_pair(self):
        assert len(self.local_events) == 2
        assert self.local_events == [('enter', self.local_events[0][1]),
                                     ('leave', self.local_events[0][1])]

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


class InlineFrameFactoryModel(FrameFactoryModel):
    def __init__(self, count, backend=0, failure=None, pending=None):
        self.backend = backend
        super().__init__(count, failure, pending)
        self.ns.update(
            pcc_gc_backend=lambda: backend,
            PY_TYPE_GEN=PY_TYPE_GEN,
            PY_FLAG_GEN_INLINE_FRAME=PY_FLAG_GEN_INLINE_FRAME,
            PYINLINEGENOBJECT_ITEMS_OFFSET=PYINLINEGENOBJECT_ITEMS_OFFSET,
            PYINLINEGENOBJECT_LENGTH_OFFSET=PYINLINEGENOBJECT_LENGTH_OFFSET,
        )
        _functions(RUNTIME / 'py_gen.py', {'py_gen_inline_frame_new'}, self.ns)

    def check_shape(self, obj):
        if self.backend != 0:
            return super().check_shape(obj)
        assert self.memory[obj + 16] is None
        assert self.memory[obj + 24] is None
        assert self.memory[obj + 32] == 0
        assert self.memory[obj + 40] == 0
        assert self.memory[obj + 48] == self.none
        length = self.memory[obj + PYINLINEGENOBJECT_LENGTH_OFFSET]
        assert length in (0, self.count)
        assert all(self.memory[obj + PYINLINEGENOBJECT_ITEMS_OFFSET + index * 8] == self.none
                   for index in range(length))


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
    model.assert_local_pair()


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
    model.assert_local_pair()


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
    model.assert_local_pair()


@pytest.mark.parametrize('pending', (None, 'original-error'))
@pytest.mark.parametrize('count', (-1, 134217729))
def test_frame_factory_rejects_invalid_size_before_allocation(count, pending):
    model = FrameFactoryModel(count, pending=pending)
    assert model.ns['py_gen_frame_new'](count) is None
    assert model.pending == (pending or 'invalid generator frame size')
    assert not model.objects and not model.frames and not model.leases and not model.pins
    assert model.local_events == []


@pytest.mark.parametrize('count', (0, 1, 33))
@pytest.mark.parametrize('backend', range(5))
def test_inline_entry_uses_one_local_pair_including_delegated_collectors(count, backend):
    model = InlineFrameFactoryModel(count, backend)
    result = model.ns['py_gen_inline_frame_new'](count)
    assert result in model.objects and model.refs[result] == 1
    model.check_shape(result)
    length_offset = PYINLINEGENOBJECT_LENGTH_OFFSET if backend == 0 else 16
    assert model.memory[result + length_offset] == count
    model.assert_local_pair()
    assert not model.frames and not model.leases and not model.pins
    assert model.pending is None and model.disposals == 0


@pytest.mark.parametrize('pending', (None, 'original-error'))
@pytest.mark.parametrize('failure,message', (
    ('header', 'inline generator allocation failed'),
    ('lease', 'generator frame address lease failed'),
))
def test_inline_factory_failure_retires_local_roots_and_preserves_error(failure, message, pending):
    model = InlineFrameFactoryModel(33, failure=failure, pending=pending)
    assert model.ns['py_gen_inline_frame_new'](33) is None
    assert model.pending == (pending or message)
    assert model.disposals == (0 if failure == 'header' else 1)
    model.assert_local_pair()
    assert not model.objects and not model.frames and not model.leases and not model.pins


@pytest.mark.parametrize('count', (-1, 134217729))
def test_inline_factory_invalid_size_never_registers_scratch_roots(count):
    model = InlineFrameFactoryModel(count)
    assert model.ns['py_gen_inline_frame_new'](count) is None
    assert model.pending == 'invalid generator frame size'
    assert not model.local_events and not model.objects
