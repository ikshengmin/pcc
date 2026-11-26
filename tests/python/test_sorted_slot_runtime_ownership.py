"""Production sorted slot transaction at collection points, with aliasing and callbacks."""
import ast
from collections import Counter
from pathlib import Path
import pytest
from tests.python.test_list_extend_iterator_ownership import _ExtensionModel
from tests.python.test_foreign_address_leases import _functions

COMPARE_PORT = Path(__file__).resolve().parents[2] / 'pcc/runtime/py/py_obj_ops_compare.py'


def _listsort_layout():
    """The port's listsort state layout and helper names, read from source."""
    tree = ast.parse(COMPARE_PORT.read_text())
    layout = {node.targets[0].id: node.value.value for node in tree.body
              if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name)
              and node.targets[0].id.startswith('_TIM_')}
    helpers = {node.name for node in tree.body
               if isinstance(node, ast.FunctionDef) and node.name.startswith('_tim_')}
    return layout, helpers


class SortModel(_ExtensionModel):
    def __init__(self, values, *, key=False, compare=False, reverse=False, relocate=False,
                 failure=None, key_alias=False, payload_capacity_slack=0, inline_work_payload=False):
        super().__init__(None, relocate, False)
        self.failure, self.reverse, self.key_alias = failure, reverse, key_alias
        self.key_calls = self.compare_calls = self.allocations = self.list_allocations = 0
        self.output_seen_at_disposal = False
        self.external = 1000000
        self.depth = 0
        self.payload_swaps = []
        self.work_list = None
        self.work_length_reads = 0
        self.payload_capacity_slack = payload_capacity_slack
        self.inline_work_payload = inline_work_payload
        self.freed = set()
        self.source[0].payload = []
        for index, value in enumerate(values):
            obj = self.new('element-' + str(index), fresh=False)
            obj[0].value, obj[0].label = value, index
            self.source[0].payload.append(obj)
        self.key = self.new('key-callable', fresh=False) if key else None
        self.compare = self.new('compare-callable', fresh=False) if compare else None
        self.frames = {self.external: 4}
        for i, value in enumerate([self.source, self.key, self.compare, None]):
            self.store(self.external, i * 8, value)
        self.ns.update({
            '_SORT_SOURCE': 0, '_SORT_KEY': 1, '_SORT_COMPARE': 2,
            '_SORT_VALUES': 3, '_SORT_KEYS': 4,
            '_SORT_ARGS': 5, '_SORT_ITEM': 6, '_SORT_LEFT': 7,
            '_SORT_RIGHT': 8, '_SORT_REVERSE': 9, '_SORT_ERROR': 10, '_SORT_COUNT': 11, '_SORT_BYTES': 8,
            'PYLISTOBJECT_LENGTH_OFFSET': 16,
            'PYLISTOBJECT_CAPACITY_OFFSET': 24,
            'PYLISTOBJECT_ITEMS_OFFSET': 32, 'PYLISTOBJECT_SIZE': 40, 'C_POINTER_SIZE': 8,
            'ptr_eq': lambda a, b: int(a == b), 'ptr_add': self.pointer_add,
            'pcc_py_gc_minor_graph_lock': self.lock,
            'pcc_py_gc_minor_graph_unlock': self.unlock,
            'pcc_gc_load_ptr': self.gc_load,
            'global_addr': lambda name: 11,
            # The sorted frame registers its owned map (11 slots) by address.
            'pcc_gc_frame_enter': self.enter,
            'pcc_gc_frame_leave': self.leave,
            'pcc_gc_root_copy': self.root_copy,
            'pcc_gc_root_move': self.move,
            'py_list_new': self.list_new, 'py_list_len': self.length,
            'py_list_get': self.get, 'py_list_extend': self.extend,
            'py_tuple_new': self.tuple_new, 'py_tuple_set_item': self.set_item,
            'py_obj_call_slots': self.call,
            'py_obj_truthy': self.truth,
            'py_obj_lt': self.less,
            '_type_of': lambda pointer: 1, 'PY_TYPE_NONE': 0,
            'malloc': self.malloc, 'free': self.free,
            # This model is the general protocol; the GC0 plain-order
            # path is test_sorted_gc0_plain_order's.
            '_sorted_gc0': lambda source_slot, reverse, result_slot: 0,
            'memmove': self.move_words,
        })
        layout, helpers = _listsort_layout()
        self.ns.update(layout)
        _functions(COMPARE_PORT,
                   {'_sorted_error', '_sorted_drop', '_sorted_adopt', '_sorted_copy',
                    '_sorted_arguments', '_sorted_prepare_keys', '_sorted_less',
                    '_sorted_merge_indices', '_sorted_reorder_values',
                    '_sorted_slots_body', 'py_obj_sorted_slots'} | helpers, self.ns)

    def root_copy(self, destination, source):
        # pcc_gc_root_copy: retain into the destination, a status only; the
        # one collector takes no lease (item 2 (d) 4c).
        self.boundary()
        self.copy_count += 1
        if self.failure == 'copy-' + str(self.copy_count):
            return -1
        value = self.check(self.load(source, 0))
        value[0].refs += 1
        self.store(destination, 0, value)
        return 0

    def move_words(self, destination, source, size):
        # The listsort port moves whole index words; overlap reads first.
        words = [self.load(source, offset) for offset in range(0, size, 8)]
        for offset, word in zip(range(0, size, 8), words):
            self.store(destination, offset, word)
        return destination

    def alloc(self, size):
        # Frames, payloads and index buffers must never overlap.
        address = self.next_address
        self.next_address += ((size + 7) // 8) * 8 + 64
        return address

    def pointer_add(self, base, offset):
        if isinstance(base, tuple):
            assert offset == 40
            return getattr(self.check(base)[0], 'inline_payload_base', -1)
        return base + offset

    def load(self, base, offset):
        if isinstance(base, tuple):
            obj = self.check(base)[0]
            if offset == 16:
                if obj is self.work_list:
                    self.work_length_reads += 1
                    if self.failure == 'work-list-changed' and self.work_length_reads == 2:
                        return len(obj.payload) + 1
                return len(obj.payload)
            if offset in (24, 32):
                slack = self.payload_capacity_slack if obj is self.work_list else 0
                capacity = max(4, len(obj.payload) + slack)
                if not hasattr(obj, 'capacity') or obj.capacity < capacity:
                    obj.capacity = capacity
                    obj.payload_base = self.alloc(capacity * 8)
                    if self.inline_work_payload and obj is self.work_list and capacity == 4:
                        obj.inline_payload_base = obj.payload_base
                if offset == 24:
                    return obj.capacity
                if obj is self.work_list and self.failure == 'missing-payload':
                    return None
                for index, value in enumerate(obj.payload):
                    self.memory[obj.payload_base + index * 8] = value
                return obj.payload_base
            raise AssertionError(offset)
        return super().load(base, offset)

    def store(self, base, offset, value):
        if isinstance(base, tuple):
            assert self.depth == 1 and offset == 32
            obj = self.check(base)[0]
            assert obj is self.work_list
            old = obj.payload_base
            count = len(obj.payload)
            assert old != value and old not in self.freed and value not in self.freed
            # The old owner remains live until this graph transaction publishes
            # the exact same owned pointers in their new order, with a zero tail.
            assert [self.check(self.load(old, i * 8)) for i in range(count)] == obj.payload
            replacement = [self.check(self.load(value, i * 8)) for i in range(count)]
            assert Counter(replacement) == Counter(obj.payload)
            assert all(self.load(value, i * 8) is None for i in range(count, obj.capacity))
            self.payload_swaps.append((old, value, count, obj.capacity))
            self.events.append(('payload-publish', old, value))
            obj.payload_base = value
            obj.payload = replacement
            return
        super().store(base, offset, value)

    def lock(self):
        assert self.depth == 0
        self.boundary()
        self.depth = 1

    def unlock(self):
        assert self.depth == 1
        self.depth = 0

    def gc_load(self, owner, slot):
        assert self.depth == 1
        self.check(owner)
        return self.check(self.load(slot, 0))

    def boundary(self):
        # The one collector never moves an object (item 2 (d) 4c): a runtime
        # boundary is a collection point, counted, and raw pointers stay
        # valid, as in the base model.
        assert self.fresh is None, 'runtime boundary before NEW result publication'
        if getattr(self, 'depth', 0):
            return
        if self.relocate:
            self.collections += 1

    def fresh_copy(self, value):
        self.check(value)
        value[0].refs += 1
        self.fresh = value
        return value

    def list_new(self, size):
        self.boundary()
        self.list_allocations += 1
        if ((self.failure == 'list-allocation' and self.list_allocations == 2)
                or (self.failure == 'values-allocation' and self.list_allocations == 1)):
            return None
        obj = self.new('list')
        obj[0].payload = []
        if self.list_allocations == 1:
            self.work_list = obj[0]
        return obj

    def tuple_new(self, size):
        self.boundary()
        obj = self.new('arguments')
        obj[0].payload = [None] * size
        return obj

    def length(self, value):
        self.boundary()
        return len(self.check(value)[0].payload)

    def get(self, value, index):
        self.boundary()
        return self.fresh_copy(self.check(value)[0].payload[index])

    def set_item(self, container, index, value):
        self.boundary()
        self.check(container)
        self.check(value)
        value[0].refs += 1
        container[0].payload[index] = value

    def append(self, container, value):
        self.boundary()
        self.check(container)
        self.check(value)
        value[0].refs += 1
        container[0].payload.append(value)

    def extend(self, destination, source):
        self.boundary()
        self.check(destination)
        self.check(source)
        for value in source[0].payload:
            value[0].refs += 1
            destination[0].payload.append(value)
        if self.failure == 'iteration':
            self.pending = self.new('iteration-error', fresh=False)

    def call(self, callable_slot, args_slot, kwargs_slot, result_slot):
        self.boundary()
        fn = self.check(self.load(callable_slot, 0))
        args = self.check(self.load(args_slot, 0))[0].payload
        if fn[0].name == 'key-callable':
            self.key_calls += 1
            if self.failure == 'key' and self.key_calls == 2:
                self.pending = self.new('key-error', fresh=False)
                return -1
            if self.key_alias:
                result = self.fresh_copy(args[0])
            else:
                result = self.new('key-result')
                result[0].value = args[0][0].value
        else:
            self.compare_calls += 1
            if self.failure == 'compare':
                self.pending = self.new('compare-error', fresh=False)
                return -1
            result = self.new('comparison-result')
            result[0].value = args[0][0].value < args[1][0].value
        self.store(result_slot, 0, result)
        return 0

    def less(self, left, right):
        self.boundary()
        self.check(left)
        self.check(right)
        self.compare_calls += 1
        if self.failure == 'compare':
            self.pending = self.new('compare-error', fresh=False)
            return 0
        return int(left[0].value < right[0].value)

    def truth(self, value):
        self.boundary()
        return int(self.check(value)[0].value)

    def malloc(self, size):
        self.boundary()
        self.allocations += 1
        if self.failure == 'allocation' and self.allocations == 2:
            return None
        ptr = self.alloc(size)
        # malloc does not promise zero-fill. A poisoned tail proves the
        # production memset actually clears every unused scratch slot.
        for offset in range(0, size, 8):
            self.memory[ptr + offset] = -999999
        return ptr

    def free(self, ptr):
        self.boundary()
        if ptr is None:
            return
        assert self.depth == 0, 'payload freed before graph transaction unlocked'
        assert ptr not in self.freed
        assert all(ptr != getattr(obj, 'payload_base', None)
                   for obj in self.objects if obj.refs > 0)
        self.freed.add(ptr)
        self.events.append(('free', ptr))

    def move(self, destination, source):
        self.boundary()
        if self.failure == 'publish':
            return -1
        assert self.load(destination, 0) is None
        self.store(destination, 0, self.load(source, 0))
        self.store(source, 0, None)
        return 0

    def drop(self, value):
        if value is None:
            return
        self.check(value)
        obj = value[0]
        obj.refs -= 1
        assert obj.refs >= 0
        if obj.refs == 0:
            for index in range(len(getattr(obj, 'payload', []))):
                child = obj.payload[index]
                obj.payload[index] = None
                self.drop(child)
            obj.payload = []
            if obj.name == 'list' and getattr(obj, 'payload_base', None) is not None:
                # A failed result publication still owns the transferred raw
                # backing until dropping this work list. Model its terminal free.
                self.free(obj.payload_base)
            if obj.name == 'key-result':
                self.boundary()
                if self.load(self.external, 24) is not None:
                    self.output_seen_at_disposal = True
                self.clear_error()
                self.pending = self.new('disposal-error', fresh=False)

    def run(self):
        return self.ns['py_obj_sorted_slots'](
            self.external, self.external + 8 if self.key else None,
            self.external + 16 if self.compare else None,
            int(self.reverse), self.external + 24,
        )


@pytest.mark.parametrize('relocate', (False, True))
@pytest.mark.parametrize('reverse', (False, True))
@pytest.mark.parametrize('key,compare,key_alias', ((False, False, False), (True, False, False), (True, False, True), (False, True, False)))
@pytest.mark.parametrize('values', ([], [2], [3, 1, 2, 1, 3, 0]))
def test_sorted_slots_stable_owners(values, key, compare, key_alias, reverse, relocate):
    model = SortModel(values, key=key, compare=compare, reverse=reverse, relocate=relocate, key_alias=key_alias)
    assert model.run() == 0
    result = model.check(model.load(model.external, 24))
    observed = [(v[0].value, v[0].label) for v in result[0].payload]
    assert observed == sorted(zip(values, range(len(values))), key=lambda v: v[0], reverse=reverse)
    assert model.key_calls == (len(values) if key else 0)
    assert model.list_allocations == (2 if key else 1)
    assert result[0] is not model.source[0]
    assert [v[0].value for v in model.source[0].payload] == values
    assert len(model.payload_swaps) == int(len(values) > 1)
    if model.payload_swaps:
        old, new, count, capacity = model.payload_swaps[0]
        assert count == len(values) and capacity >= count
        assert result[0].payload_base == new and old in model.freed and new not in model.freed
        assert model.events.index(('payload-publish', old, new)) < model.events.index(('free', old))
    assert model.depth == 0
    assert model.pending is None
    assert model.frames == {model.external: 4}
    assert all(v[0].refs == 2 for v in result[0].payload)
    assert all(obj.refs == 0 for obj in model.objects
               if obj.name in ('list', 'arguments', 'key-result', 'comparison-result') and obj is not result[0])
    if key and values and not key_alias:
        assert model.output_seen_at_disposal


@pytest.mark.parametrize('relocate', (False, True))
@pytest.mark.parametrize('failure', ('iteration', 'key', 'compare', 'allocation', 'publish', 'copy-1', 'list-allocation', 'values-allocation', 'work-list-changed', 'missing-payload'))
def test_sorted_slots_error_preserves_primary_during_disposal(failure, relocate):
    model = SortModel([3, 1, 2], key=True, relocate=relocate, failure=failure)
    assert model.run() < 0
    assert model.load(model.external, 24) is None
    assert model.pending[0].name == {
        'iteration': 'iteration-error', 'key': 'key-error', 'compare': 'compare-error',
        'allocation': 'sorted: cannot allocate index buffer',
        'publish': 'sorted: cannot transfer result owner',
        'copy-1': 'sorted: cannot retain input',
        'list-allocation': 'sorted: missing owned value',
        'values-allocation': 'sorted: missing owned value',
        'work-list-changed': 'sorted: work list changed during comparison',
        'missing-payload': 'sorted: missing work list payload',
    }[failure]
    assert model.frames == {model.external: 4}
    source = model.load(model.external, 0)
    assert all(v[0].refs == 1 for v in source[0].payload)
    assert all(obj.refs == 0 for obj in model.objects
               if obj.name in ('list', 'arguments', 'key-result', 'comparison-result'))
    if failure == 'publish':
        assert len(model.payload_swaps) == 1
        old, new, _count, _capacity = model.payload_swaps[0]
        assert old in model.freed and new in model.freed
        assert model.events.index(('payload-publish', old, new)) < model.events.index(('free', old)) < model.events.index(('free', new))
    else:
        assert model.payload_swaps == []
    assert model.depth == 0


@pytest.mark.parametrize('relocate', (False, True))
def test_sorted_publishes_owned_payload_before_old_backing_free(relocate):
    model = SortModel([3, 1, 3, 0, 2], relocate=relocate, payload_capacity_slack=3)
    assert model.run() == 0
    result = model.load(model.external, 24)[0]
    assert [(v[0].value, v[0].label) for v in result.payload] == [
        (0, 3), (1, 1), (2, 4), (3, 0), (3, 2),
    ]
    assert len(model.payload_swaps) == 1
    old, new, count, capacity = model.payload_swaps[0]
    assert (new, count, capacity) == (result.payload_base, len(result.payload), result.capacity)
    assert capacity > count
    assert model.events.index(('payload-publish', old, new)) < model.events.index(('free', old))
    assert old in model.freed and new not in model.freed
    assert all(model.load(new, i * 8) is None for i in range(count, capacity))
    assert model.depth == 0


def test_sorted_retires_inline_payload_only_after_publishing_new_owner():
    model = SortModel([3, 1, 2], inline_work_payload=True)
    assert model.run() == 0
    result = model.load(model.external, 24)[0]
    assert [v[0].value for v in result.payload] == [1, 2, 3]
    assert len(model.payload_swaps) == 1
    old, new, count, capacity = model.payload_swaps[0]
    assert old == result.inline_payload_base and old not in model.freed
    assert new == result.payload_base and new not in model.freed
    assert count == 3 and capacity == 4
    assert all(model.load(old, i * 8) is None for i in range(4))
    assert model.depth == 0
