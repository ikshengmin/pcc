"""Execute actual Match constructor/method/finish under an owning-slot model."""
import ast
from pathlib import Path

import pytest

SOURCE = Path(__file__).resolve().parents[2] / "pcc/runtime/py/py_re_match_runtime.py"


class MatchModel:
    def __init__(self, fail=None, cleanup_error=False):
        self.fail = fail
        self.cleanup_error = cleanup_error
        self.events = []
        self.memory = {}
        self.next_raw = 40000
        self.slots = 10000
        self.tokens = 20000
        self.caps = 30000
        for base, size in ((self.slots, 128), (self.tokens, 128), (self.caps, 512)):
            self.memory.update({base + i: 0 for i in range(size)})
        self.next_obj = 100
        self.objects = {
            1: {'kind': 'none', 'refs': 1, 'leases': 0, 'children': [], 'immortal': True},
            2: {'kind': 'class', 'refs': 1, 'leases': 0, 'children': [], 'immortal': True},
        }
        self.tls = 0
        self.original_error = 0
        self.pin = set()
        self.graph = 0
        self.pattern = self.new('pattern', 'pattern', record=False)
        self.text = self.new('text', 'ab', record=False)
        for index, obj in ((2, self.pattern), (3, self.text)):
            self.memory[self.slots + index * 8] = obj
            self.memory[self.tokens + index * 8] = 1
            self.objects[obj]['leases'] += 1
        for index, value in enumerate((0, 2, 0, 2)):
            self.memory[self.caps + index * 8] = value
        namespace = {
            "c_ptr": int,
            'cstr': lambda s: s, 'null': lambda: 0,
            'ptr_add': lambda p, n: p + n,
            'ptr_is_null': lambda p: int(p == 0),
            'load_ptr': lambda p, n: self.memory[p+n],
            'load_i64': lambda p, n: self.memory[p+n],
            'load_i8': lambda p, n: self.memory[p+n],
            'store_ptr': self.store_raw, 'store_i64': self.store_raw,
            'stack_alloc': self.allocate_raw,
            'global_load_ptr': lambda name: 1,
            'py_runtime_error_if_unset': self.set_error,
            'py_err_occurred': lambda: int(self.tls != 0),
            'pcc_gc_foreign_lease_acquire': self.acquire,
            'pcc_gc_foreign_lease_release': self.release,
            'pcc_gc_store_root': self.store_root,
            'pcc_py_gc_minor_graph_lock': self.lock,
            'pcc_py_gc_minor_graph_unlock': self.unlock,
            'pcc_gc_note_slot_write_barrier': self.barrier,
            'pcc_platform_abort': lambda: (_ for _ in ()).throw(RuntimeError('abort')),
            'py_tls_exc_swap_slot': self.swap_error,
            'py_clear_exception': self.clear_error,
            'pcc_gc_pin_slot_for_take': self.pin_result,
            'py_tuple_new': lambda n: self.new('tuple', [0]*n),
            'py_int_from_i64': lambda value: self.new('int', value),
            'py_str_new': lambda pointer, n: self.new('str', bytes(self.memory[pointer+i] for i in range(n))),
            'py_instance_new': self.instance,
            'py_tuple_set_item': self.tuple_set,
            'py_func_new_named': self.function,
            'py_obj_setattr': self.setattr,
            'pcc_re_match_method_address': lambda: 777,
            'pcc_re_match_class_copy_lease': self.class_copy,
            'py_str_utf8': self.utf8,
            'pcc_re_engine_group_names_flags': self.names,
        }
        nodes = []
        for node in ast.parse(SOURCE.read_text()).body:
            if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id.startswith('_RE_') for t in node.targets):
                nodes.append(node)
            elif isinstance(node, ast.FunctionDef) and node.name in {'_re_slot', '_re_adopt', '_re_drop', '_re_finish', '_re_method', '_re_construct'}:
                node.decorator_list = []
                nodes.append(node)
        exec(compile(ast.Module(body=nodes, type_ignores=[]), str(SOURCE), 'exec'), namespace)
        self.ns = namespace

    def operation(self, name):
        self.events.append(name)
        return len(self.events) == self.fail

    def allocate_raw(self, size):
        self.next_raw += size + 16
        self.memory.update({self.next_raw+i: 0 for i in range(size)})
        return self.next_raw

    def store_raw(self, base, offset, value):
        self.memory[base+offset] = value

    def new(self, kind, value=None, record=True):
        if record and self.operation('alloc:'+kind):
            self.set_error('allocation', kind)
            return 0
        self.next_obj += 1
        result = self.next_obj
        self.objects[result] = {'kind': kind, 'value': value, 'refs': 1, 'leases': 0, 'children': [], 'immortal': False}
        return result

    def set_error(self, *args):
        if not self.tls:
            self.tls = self.new('error', args, record=False)
            if not self.original_error:
                self.original_error = self.tls

    def require(self, obj):
        assert obj in self.objects, ('dead pointer', obj)
        assert obj == 1 or self.objects[obj]['leases'] > 0, ('unleased raw pointer', obj, self.objects[obj])

    def acquire(self, slot):
        obj = self.memory[slot]
        assert obj in self.objects
        if obj == 1:
            return 0
        if self.operation('lease'):
            return -1
        self.objects[obj]['leases'] += 1
        return 1

    def release(self, slot, token):
        if token == 0:
            return 0
        assert token == 1
        obj = self.memory[slot]
        assert obj in self.objects and self.objects[obj]['leases'] > 0
        self.objects[obj]['leases'] -= 1
        return 0

    def incref(self, obj):
        if obj and not self.objects[obj]['immortal']:
            self.objects[obj]['refs'] += 1

    def decref(self, obj):
        if not obj or self.objects[obj]['immortal']:
            return
        value = self.objects[obj]
        value['refs'] -= 1
        assert value['refs'] >= 0
        if value['refs'] == 0:
            assert value['leases'] == 0, ('free leased object', obj, value)
            children = value['children'][:]
            del self.objects[obj]
            for child in children:
                self.decref(child)
            if self.cleanup_error and value['kind'] != 'error':
                self.set_error('cleanup', 'finalizer')

    def store_root(self, slot, value):
        assert value == 0
        old = self.memory[slot]
        self.memory[slot] = 0
        self.decref(old)

    def lock(self):
        assert self.graph == 0
        self.graph = 1

    def unlock(self):
        assert self.graph == 1
        self.graph = 0

    def barrier(self, owner, slot, value):
        assert self.graph == 1 and self.memory[slot] == value
        self.require(value)

    def swap_error(self, slot):
        self.tls, self.memory[slot] = self.memory[slot], self.tls

    def clear_error(self):
        old, self.tls = self.tls, 0
        self.decref(old)

    def pin_result(self, slot, prior):
        assert prior == 0
        obj = self.memory[slot]
        if obj:
            assert obj in self.objects
            self.pin.add(obj)
        return 0

    def class_copy(self, slot):
        assert self.memory[slot] == 0
        if self.operation('class_copy'):
            return -1
        self.memory[slot] = 2
        self.objects[2]['leases'] += 1
        return 1

    def instance(self, cls):
        self.require(cls)
        obj = self.new('instance', {})
        if obj:
            self.objects[obj]['children'].append(cls)
            self.incref(cls)
        return obj

    def tuple_set(self, tup, index, item):
        self.require(tup)
        self.require(item)
        if self.operation('tuple_set'):
            self.set_error('tuple', 'set failed')
            return
        value = self.objects[tup]
        assert value['kind'] == 'tuple' and value['value'][index] == 0
        value['value'][index] = item
        value['children'].append(item)
        self.incref(item)

    def function(self, entry, captures, name):
        assert entry == 777
        self.require(captures)
        obj = self.new('function', name)
        if obj:
            self.objects[obj]['children'].append(captures)
            self.incref(captures)
        return obj

    def setattr(self, obj, name, value):
        self.require(obj)
        self.require(value)
        if self.operation('setattr'):
            self.set_error('attribute', 'set failed')
            return -1
        self.objects[obj]['value'][name] = value
        self.objects[obj]['children'].append(value)
        self.incref(value)
        return 0

    def utf8(self, obj):
        self.require(obj)
        assert obj == self.pattern
        return 999

    def names(self, pattern, flags, buffer, capacity):
        assert (pattern, flags, capacity) == (999, 0, 1024)
        if self.operation('names'):
            return -1
        for i, byte in enumerate(b'word\0'):
            self.memory[buffer+i] = byte
        return 1

    def construct(self):
        return self.ns['_re_construct'](self.slots, self.tokens, self.caps, 1, 0)

    def finish(self):
        return self.ns['_re_finish'](self.slots, self.tokens)

    def reachable(self, root):
        found = set()
        todo = [root] if root else []
        while todo:
            obj = todo.pop()
            if obj not in found:
                found.add(obj)
                todo.extend(self.objects[obj]['children'])
        return found


def test_constructor_installs_six_methods_and_finish_leaves_only_reachable_output():
    model = MatchModel()
    assert model.construct() == 1
    result = model.memory[model.slots]
    assert set(model.objects[result]['value']) == {'group', 'start', 'end', 'span', 'groups', 'groupdict'}
    assert model.finish() == 0
    assert model.tls == 0 and model.pin == {result}
    assert all(obj['leases'] == 0 for obj in model.objects.values())
    assert set(model.objects) == model.reachable(result) | {1, 2}
    assert all(model.memory[model.slots+i*8] == 0 for i in range(1, 16))


def test_every_constructor_allocation_lease_and_setter_failure_cleans_owners_and_preserves_error():
    baseline = MatchModel()
    assert baseline.construct() == 1
    assert {'class_copy', 'alloc:instance', 'lease', 'alloc:tuple', 'alloc:int', 'alloc:str', 'alloc:function', 'tuple_set', 'setattr', 'names'} <= set(baseline.events)
    for failed in range(1, len(baseline.events)+1):
        model = MatchModel(fail=failed, cleanup_error=True)
        assert model.construct() == 0, (failed, baseline.events[failed-1])
        original = model.tls
        assert original != 0
        model.fail = None  # Cleanup itself is separately tested below.
        model.finish()
        assert model.memory[model.slots] == 0, (failed, baseline.events[failed-1])
        assert model.tls == original, (failed, baseline.events[failed-1])
        assert set(model.objects) == {1, 2, original}, (failed, baseline.events[failed-1], model.objects)
        assert all(obj['leases'] == 0 for obj in model.objects.values())
        assert all(model.memory[model.slots+i*8] == 0 for i in range(16))


def test_error_slot_lease_failure_is_explicit_fail_stop_not_silent_cleanup():
    model = MatchModel()
    model.set_error('original', 'error')
    model.fail = 1
    with pytest.raises(RuntimeError, match='abort'):
        model.finish()
    assert model.memory[model.slots+8] == model.original_error
