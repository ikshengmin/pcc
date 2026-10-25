"""Execute the tuple factory against moving roots and ownership conservation."""
from collections import Counter
from pathlib import Path

import pytest

from tests.python.test_foreign_address_leases import _functions

PORT = Path(__file__).resolve().parents[2] / 'pcc/runtime/py/py_tuple.py'


class TupleFactoryMemory:
    def __init__(self, values, backend, failure=None, pending=None):
        self.backend, self.failure, self.pending = backend, failure, pending
        self.memory, self.frames, self.objects, self.leases = {}, {}, set(), {}
        self.children = Counter(values)
        self.count = len(values)
        self.next_address = 1000
        self.locked = False
        self.fresh = None
        self.epoch = 0
        self.tracked = self.published = self.disposals = self.stores = 0
        self.output = self.stack(8)
        self.memory[self.output] = None
        self.frames[self.output] = 1
        self.sources = self.stack(8 * self.count)
        self.source_slots = []
        for index, value in enumerate(values):
            slot = self.stack(8)
            self.source_slots.append(slot)
            self.frames[slot] = 1
            self.memory[slot] = value
            self.memory[self.sources + index * 8] = slot
        self.initial_frames = dict(self.frames)
        self.ns = {
            'i64': int, 'C_POINTER_SIZE': 8, 'PYTUPLEOBJECT_SIZE': 24,
            'PYTUPLEOBJECT_LEN_OFFSET': 16, 'PYTUPLEOBJECT_ITEMS_OFFSET': 24,
            'PY_TYPE_TUPLE': 7, 'null': lambda: None,
            'ptr_is_null': lambda value: value is None, 'ptr_add': lambda p, n: p + n,
            'cstr': lambda value: value, 'global_addr': lambda name: 1,
            'stack_alloc': self.stack, 'memset': self.zero,
            'load_ptr': self.load, 'store_ptr': self.write, 'store_i64': self.write,
            'pcc_gc_alloc': self.allocate, 'pcc_gc_foreign_lease_acquire': self.acquire,
            'pcc_gc_foreign_lease_release': self.release,
            'pcc_py_gc_minor_graph_lock': self.lock,
            'pcc_py_gc_minor_graph_unlock': self.unlock,
            'pcc_gc_resolve_root_slot_unlocked': self.resolve,
            'pcc_gc_backend': lambda: self.backend,
            'pcc_gc_note_store': self.note_store,
            'pcc_gc_note_slot_write_barrier': self.barrier,
            '_tuple_item_can_participate_in_cycle': self.cycle_child,
            'py_gc_track': self.track, 'pcc_gc_publish_initialized': self.publish,
            'py_err_occurred': lambda: self.pending is not None,
            'py_exc_new': lambda kind, text: text, 'py_raise_owned': self.raise_error,
            'py_tls_exc_swap_slot': self.swap, 'py_clear_exception': self.clear_error,
            'pcc_gc_store_root': self.drop_root,
            'pcc_gc_frame_enter': self.enter, 'pcc_gc_frame_leave': self.leave,
            'py_runtime_error_if_unset': self.error_if_unset,
            '_debug_bad_container': lambda *args: pytest.fail('invalid constructor size'),
            'pcc_platform_abort': lambda: pytest.fail('invariant abort'),
        }
        _functions(PORT, {'py_tuple_new', '_tuple_owned_construction_failed',
                          'py_tuple_from_owned_roots'}, self.ns)

    def stack(self, size):
        self.next_address += max(1024, size + 32)
        return self.next_address

    def load(self, address, offset):
        return self.memory.get(address + offset)

    def write(self, address, offset, value):
        self.memory[address + offset] = value
        if value is not None and value == self.fresh:
            assert address + offset in self.frames
            self.fresh = None

    def zero(self, address, value, size):
        for offset in range(0, size, 8):
            self.memory[address + offset] = None

    def owned(self):
        result = Counter()
        for root, width in self.frames.items():
            for index in range(width):
                value = self.load(root, index * 8)
                if value in self.children:
                    result[value] += 1
        for obj in self.objects:
            for index in range(self.load(obj, 16)):
                value = self.load(obj, 24 + index * 8)
                if value in self.children:
                    result[value] += 1
        return result

    def boundary(self):
        assert not self.locked and self.fresh is None
        for obj in self.objects:
            assert self.load(obj, 16) == self.count
            assert all(obj + 24 + i * 8 in self.memory for i in range(self.count))
        if self.backend in (3, 4):
            self.epoch += 1
            moved_children = {v: v.split('/')[0] + '/' + str(self.epoch) for v in self.children}
            for obj in list(self.objects):
                if obj not in {self.load(slot, 0) for slot in self.leases}:
                    new = obj + 1000000
                    for offset in [16] + [24 + i * 8 for i in range(self.count)]:
                        self.memory[new + offset] = self.memory.pop(obj + offset)
                    self.objects.remove(obj)
                    self.objects.add(new)
                    for slot in self.frames:
                        if self.load(slot, 0) == obj:
                            self.memory[slot] = new
            addresses = list(self.frames)
            addresses += [obj + 24 + i * 8 for obj in self.objects for i in range(self.count)]
            for address in addresses:
                value = self.memory.get(address)
                if value in moved_children:
                    self.memory[address] = moved_children[value]
            self.children = Counter({moved_children[v]: n for v, n in self.children.items()})
        assert self.owned() == self.children

    def allocate(self, size, tag, flags):
        self.boundary()
        assert size == 24 + 8 * self.count and tag == 7
        if self.failure == 'header':
            return None
        obj = self.stack(size)
        self.objects.add(obj)
        self.fresh = obj
        return obj

    def acquire(self, slot):
        self.boundary()
        if self.failure == 'lease':
            return -1
        self.leases[slot] = 1
        return 1

    def release(self, slot, token):
        self.boundary()
        assert self.leases.pop(slot) == token
        return 0

    def lock(self):
        self.boundary()
        self.locked = True

    def unlock(self):
        assert self.locked
        self.locked = False
        self.boundary()

    def resolve(self, slot, borrowed):
        assert self.locked and borrowed == 0 and slot in self.source_slots
        return self.load(slot, 0)

    def note_store(self):
        assert self.locked
        self.stores += 1

    def barrier(self, owner, slot, value):
        if owner is None:
            self.boundary()
            assert slot == self.output and self.load(slot, 0) == value
        else:
            assert self.locked and owner in self.objects and value in self.children

    def cycle_child(self, value):
        assert self.locked
        return value.split('/')[0] == 'cycle'

    def track(self, obj):
        self.boundary()
        assert obj in self.objects and all(self.load(s, 0) is None for s in self.source_slots)
        self.tracked += 1

    def publish(self, obj):
        self.boundary()
        assert obj in self.objects and all(self.load(s, 0) is None for s in self.source_slots)
        self.published += 1

    def raise_error(self, error):
        self.pending = error

    def clear_error(self):
        self.pending = None

    def error_if_unset(self, helper, message):
        self.pending = self.pending or message

    def enter(self, width, slot):
        self.frames[slot] = width
        self.boundary()

    def leave(self, slot):
        self.boundary()
        del self.frames[slot]

    def swap(self, slot):
        self.boundary()
        old = self.load(slot, 0)
        self.write(slot, 0, self.pending)
        self.pending = old

    def drop_root(self, slot, value):
        self.boundary()
        assert value is None
        old = self.load(slot, 0)
        self.write(slot, 0, None)
        if old is not None:
            assert old in self.objects
            self.objects.remove(old)
            self.disposals += 1
            self.pending = 'cleanup-error'
        self.boundary()


@pytest.mark.parametrize('backend', range(5))
@pytest.mark.parametrize('values', [('a',), ('a', 'b', 'a'), ('a',) * 8, ('cycle', 'a')])
def test_tuple_factory_transfers_each_owner_with_movement_and_tracking(values, backend):
    m = TupleFactoryMemory(values, backend)
    assert m.ns['py_tuple_from_owned_roots'](m.sources, len(values), m.output) == 0
    assert m.load(m.output, 0) in m.objects
    assert all(m.load(slot, 0) is None for slot in m.source_slots)
    assert m.owned() == m.children
    assert m.tracked == int('cycle' in values) and m.published == 1
    assert m.stores == (len(values) if backend else 0)
    assert m.frames == m.initial_frames and not m.leases and m.pending is None


@pytest.mark.parametrize('backend', range(5))
@pytest.mark.parametrize('failure', ['header', 'lease'])
@pytest.mark.parametrize('pending', [None, 'selected-error'])
def test_tuple_factory_failure_preserves_sources_and_selected_exception(backend, failure, pending):
    m = TupleFactoryMemory(('a', 'b', 'a'), backend, failure, pending)
    assert m.ns['py_tuple_from_owned_roots'](m.sources, 3, m.output) == -1
    assert m.load(m.output, 0) is None and not m.objects
    assert all(m.load(slot, 0) in m.children for slot in m.source_slots)
    assert m.owned() == m.children and m.frames == m.initial_frames and not m.leases
    assert m.pending == (pending or 'tuple: out of memory')
    assert m.tracked == m.published == 0 and m.disposals == (failure == 'lease')


@pytest.mark.parametrize('count', [0, -1, 134217729])
def test_invalid_tuple_transfer_count_does_not_change_owners(count):
    m = TupleFactoryMemory(('a',), 4)
    before = dict(m.memory)
    assert m.ns['py_tuple_from_owned_roots'](m.sources, count, m.output) == -1
    assert m.memory == before and m.frames == m.initial_frames
    assert not m.objects and not m.leases and m.pending


PROGRAM = '''import gc
import weakref
events = []
class Item:
    def __del__(self):
        events.append("drop")
        gc.collect()
def materialize():
    item = Item()
    ref = weakref.ref(item)
    source = {"first": item, "second": item}
    pairs = list(source.items())
    assert pairs[0][1] is item and pairs[1][1] is item
    source.clear()
    return pairs, ref
def cycle_shape():
    item = Item()
    ref = weakref.ref(item)
    owner = [item]
    pair = (owner, owner)
    owner.append(pair)
    gc.collect()
    assert pair[0] is owner and pair[1] is owner and owner[1] is pair
    owner.clear()
    return ref
def exercise():
    pairs, ref = materialize()
    gc.collect()
    assert ref() is not None and pairs[0][1] is pairs[1][1]
    pairs.clear()
    gc.collect()
    assert ref() is None
    ref = cycle_shape()
    gc.collect()
    assert ref() is None
    data = {"a": 1, "b": 2}
    assert list(data.keys()) == ["a", "b"]
    assert list(data.values()) == [1, 2]
def main():
    for index in range(16):
        exercise()
    gc.collect()
    assert len(events) == 32
    print("TUPLE_ROOT_TRANSFER_OK")
main()
'''


THREAD_PROGRAM = '''import gc
import threading

def construct(tag, output):
    for index in range(48):
        value = [tag, index]
        source = {"first": value, "second": value}
        pairs = list(source.items())
        aliases = (source, source, value, value)
        gc.collect()
        assert pairs[0][1] is value and pairs[1][1] is value
        assert aliases[0] is source and aliases[1] is source
        source.clear()
        gc.collect()
        assert aliases[2] is value and aliases[3] is value
        assert pairs[0][1] == [tag, index]
    output.append(tag)
def main():
    output = []
    first = threading.Thread(target=construct, args=(1, output))
    second = threading.Thread(target=construct, args=(2, output))
    first.start()
    second.start()
    first.join()
    second.join()
    assert sorted(output) == [1, 2]
    print("TUPLE_ROOT_THREADS_OK")
main()
'''


@pytest.mark.integration
@pytest.mark.parametrize("program,expected", [(PROGRAM, "TUPLE_ROOT_TRANSFER_OK"), (THREAD_PROGRAM, "TUPLE_ROOT_THREADS_OK")], ids=("lifetimes", "concurrent-collection"))
def test_tuple_transfer_preserves_aliases_finalizers_and_reachable_cycles(
    tmp_path, pcc_runtime_archive, python_program_compiler, program, expected,
):
    import os
    import subprocess

    source = tmp_path / 'tuple_transfer.py'
    source.write_text(program)
    binary = tmp_path / 'tuple_transfer'
    python_program_compiler(str(source), str(binary), backend='self', libpython_mode='off',
                            ir_scaffold_mode='on', runtime_archive=str(pcc_runtime_archive))
    for backend in range(5):
        result = subprocess.run(
            [str(binary)], capture_output=True, text=True, timeout=30,
            env=dict(os.environ, PATH='/nonexistent', PCC_GC_BACKEND=str(backend),
                     PCC_GC_MINOR_ALLOC_MAX='4096'),
        )
        (tmp_path / ('gc' + str(backend) + '.stdout')).write_text(result.stdout)
        (tmp_path / ('gc' + str(backend) + '.stderr')).write_text(result.stderr)
        assert (result.returncode, result.stdout, result.stderr) == (0, expected + '\n', ''), backend
