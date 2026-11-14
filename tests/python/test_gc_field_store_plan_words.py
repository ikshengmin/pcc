"""Host source-model checks for field-store scratch words, not native roots.

Execute unchanged runtime bodies with byte-backed StorePlan intrinsics. This
models a reused 128-byte buffer and observes it at container unlock; it does
not establish native stack layout, stop admission, or object survival.
"""

import ast

import pytest

from tests.python.test_gc4_count_free_store_plans import SOURCE, Store


class FieldPlanWords(Store):
    PLAN_BYTES = 128

    def __init__(self, backend):
        super().__init__(backend=backend)
        self.plan_bytes = bytearray(self.OLD.to_bytes(8, 'little') * 16)
        self.allocations = []
        self.unlock_words = []
        self.locked = False
        self.globals.update(
            pcc_gc_config_initialized=1,
            pcc_gc_backend_selected=backend,
            pcc_gc_list_container_mode=1,
            # Backends 1/2 never select the GC0 atomic owner fast path, so
            # the store keeps its StorePlan transaction in this model.
            pcc_gc_refcount_atomic=0,
            pcc_diagnostics_runtime_log_fast_state=0,
            pcc_debug_runtime_enabled_cache=0,
        )
        self.ns.update(
            load_i32=lambda p, o: self.typed_load(p, o, 4),
            load_i64=lambda p, o: self.typed_load(p, o, 8),
            load_ptr=lambda p, o: self.typed_load(p, o, 8),
            store_i32=lambda p, o, v: self.typed_store(p, o, v, 4),
            store_i64=lambda p, o, v: self.typed_store(p, o, v, 8),
            store_ptr=lambda p, o, v: self.typed_store(p, o, v, 8),
            stack_alloc=self.allocate_plan,
            pcc_container_lock=self.lock,
            pcc_container_unlock=self.unlock,
        )
        names = {
            '_fields_lock_themselves',
            '_gc0_owner_fast_path',
            'pcc_gc_store_field_ptr',
            'pcc_gc_store_field_ptr_if_empty',
        }
        nodes = [node for node in ast.parse(SOURCE.read_text()).body
                 if isinstance(node, ast.FunctionDef) and node.name in names]
        assert {node.name for node in nodes} == names
        for node in nodes:
            node.decorator_list = []
        exec(compile(ast.Module(body=nodes, type_ignores=[]), str(SOURCE), 'exec'), self.ns)

    def typed_load(self, pointer, offset, width):
        # Keep the original fixture's uninitialized/old-reference/RC checks.
        value = self.load(pointer, offset)
        if isinstance(pointer, int) and self.PLAN <= pointer + offset < self.PLAN + self.PLAN_BYTES:
            start = pointer + offset - self.PLAN
            assert start + width <= self.PLAN_BYTES
            return int.from_bytes(self.plan_bytes[start:start + width], 'little')
        return value

    def typed_store(self, pointer, offset, value, width):
        self.store(pointer, offset, value)
        if self.PLAN <= pointer + offset < self.PLAN + self.PLAN_BYTES:
            start = pointer + offset - self.PLAN
            assert start + width <= self.PLAN_BYTES
            self.plan_bytes[start:start + width] = value.to_bytes(width, 'little')

    def zero(self, pointer, value, size):
        super().zero(pointer, value, size)
        self.plan_bytes[:] = bytes(size)

    def allocate_plan(self, size):
        assert size == self.PLAN_BYTES
        assert not self.locked
        self.ready = False
        self.allocations.append((self.PLAN, size))
        return self.PLAN

    def lock(self, owner):
        assert owner == self.OWNER and not self.locked
        self.locked = True
        self.ready = True

    def unlock(self, owner):
        assert owner == self.OWNER and self.locked
        self.locked = False
        self.unlock_words.append(self.scan_words())

    def scan_words(self):
        return [int.from_bytes(self.plan_bytes[start:start + 8], 'little')
                for start in range(0, self.PLAN_BYTES, 8)]

    def invoke(self, value, *, empty=False, slot=None):
        name = 'pcc_gc_store_field_ptr_if_empty' if empty else 'pcc_gc_store_field_ptr'
        return self.ns[name](self.OWNER, self.SLOT if slot is None else slot, value)


@pytest.mark.parametrize('backend', (1, 2))
@pytest.mark.parametrize('value', (0, 37, Store.VALUE, 0x100004000))
def test_field_unlock_keeps_value_and_separate_mode_state_words(backend, value):
    model = FieldPlanWords(backend)
    model.invoke(value)
    assert model.allocations == [(model.PLAN, model.PLAN_BYTES)]
    assert model.words[model.SLOT] == value and model.old_reads == 0
    assert not model.locked
    assert model.unlock_words == [[value, *([0] * 13), backend, (3 << 32) | 1]]
    assert model.scan_words() == [value, *([0] * 13), backend, (7 << 32) | 1]
    barrier = [('barrier', model.OWNER, value)] if value and not value & 1 else []
    assert model.events == ['note', *barrier, ('publish', value)]


@pytest.mark.parametrize('backend', (1, 2))
def test_reused_field_plan_clears_previous_value_and_finished_state(backend):
    model = FieldPlanWords(backend)
    values = [model.VALUE, model.COPY, 0, 37]
    for value in values:
        # An intervening field writer does not clear this caller's scratch.
        model.words[model.SLOT] = model.OLD
        model.invoke(value)
        assert model.scan_words() == [value, *([0] * 13), backend, (7 << 32) | 1]
    assert model.allocations == [(model.PLAN, model.PLAN_BYTES)] * 4
    assert model.unlock_words == [
        [value, *([0] * 13), backend, (3 << 32) | 1] for value in values
    ]
    assert model.old_reads == 0 and not model.locked


@pytest.mark.parametrize('backend', (1, 2))
@pytest.mark.parametrize('empty', (False, True))
def test_null_field_slot_does_not_allocate_or_clear_scratch(backend, empty):
    model = FieldPlanWords(backend)
    before = bytes(model.plan_bytes)
    assert model.invoke(model.VALUE, empty=empty, slot=0) == (0 if empty else None)
    assert bytes(model.plan_bytes) == before
    assert model.allocations == model.unlock_words == model.events == []
    assert not model.locked
