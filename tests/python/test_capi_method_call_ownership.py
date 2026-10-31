"""Checked production-body/IR evidence, not native/five-collector acceptance."""
import ast
from pathlib import Path
import re

import pytest

ROOT = Path(__file__).resolve().parents[2]
RAW = ROOT / 'pcc/runtime/py/py_capi_method_entry.py'
BODY = RAW.with_name('py_capi_method_call_runtime.py')


class MethodModel:
    """Move every unleased object at polls; audit roots, RC, pins and callbacks."""
    TYPE = 0x800
    METHOD = 0x900
    FUNCTION = 0xa00
    NONE = 0xb00

    def __init__(self, *, mode='new', flags=1, raw_self=False, translated=False,
                 failure=None, fail_registration=0, cleanup_error=False, empty_keywords=False, repeated=False, two_keywords=False):
        self.mode = mode
        self.flags = flags
        self.failure = failure
        self.fail_registration = fail_registration
        self.cleanup_error = cleanup_error
        self.empty_keywords = empty_keywords
        self.keyword_count = 0 if empty_keywords else (2 if two_keywords else 1)
        self.events = []
        self.operation_count = 0
        self.register_count = 0
        self.depth = 0
        self.graph = 0
        self.next_raw = 0x10000000
        self.next_object = 0x10000
        self.memory = {self.METHOD + 8: self.FUNCTION}
        self.regions = {}
        self.calloc_blocks = set()
        self.frames = {}
        self.frame_maps = {}
        self.objects = {}
        self.forwarded = {}
        self.tls = 0
        self.original_error = 0
        self.called = 0
        self.relocations = 0
        self.result_created = 0
        self.raw_refs = {self.TYPE: 1}
        self.static_maps = {'pcc_capi_method_borrowed_map': -2,
                            'pcc_capi_method_owned_map': 16}
        self.objects[self.NONE] = dict(kind='none', refs=1, leases=0,
                                     pinned=True, value=None, immortal=True)
        self.self_obj = self.TYPE if raw_self else self.new('self', record=False)
        self.arg0 = self.new('class' if translated else 'int', 2**62, record=False)
        self.arg1 = self.new('int', 31, record=False)
        self.key = self.new('str', 'number', record=False)
        self.kwvalue = self.new('int', 47, record=False)
        if repeated:
            self.arg1 = self.arg0
            self.kwvalue = self.arg0
        positional = [] if (flags & 143) == 4 else [self.arg0]
        if (flags & 143) not in (4, 8):
            positional.append(self.arg1)
        self.callargs = self.tuple(positional, record=False)
        pairs = [(self.key, self.kwvalue)]
        if two_keywords:
            pairs.append((self.new('str', 'second', record=False), self.new('int', 59, record=False)))
        self.kwargs = self.NONE if empty_keywords else self.new('dict', pairs, record=False)
        if not empty_keywords:
            for key, value in pairs:
                self.incref(key)
                self.incref(value)
        self.args = self.tuple([self.callargs, self.kwargs], record=False) if flags & 2 else self.callargs
        self.captures = self.tuple([self.self_obj, self.tag(self.METHOD), self.tag(flags)], record=False)
        self.baseline = {obj: value['refs'] for obj, value in self.objects.items()}
        self.raw_baseline = self.raw_refs.copy()
        self.ns = dict(
            c_ptr=int, cstr=lambda text: text, null=lambda: 0,
            ptr_add=lambda base, offset: base + offset,
            ptr_eq=lambda left, right: int(left == right),
            ptr_is_null=lambda value: int(value == 0), is_tagged_int=lambda value: value & 1,
            int_to_ptr=lambda value: value, load_ptr=self.load, load_i64=self.load,
            load_i32=self.load32, store_ptr=self.store, store_i64=self.store,
            store_i32=self.store, stack_alloc=self.allocate, calloc=self.calloc,
            memset=self.zero, free=self.free, global_addr=lambda name: name,
            global_load_ptr=lambda name: self.NONE,
            pcc_thread_no_park_depth=lambda: self.depth,
            pcc_thread_no_park_enter=self.enter, pcc_thread_no_park_exit=self.exit,
            pcc_gc_note_frame_enter_checked=self.register, pcc_gc_note_frame_leave=self.leave,
            pcc_gc_root_copy_lease=self.copy, pcc_gc_root_copy_borrowed_lease=self.copy,
            pcc_gc_foreign_lease_acquire=self.acquire,
            pcc_gc_foreign_lease_release=self.release,
            pcc_gc_store_root=self.store_root,
            pcc_gc_note_slot_write_barrier=self.barrier,
            pcc_py_gc_minor_graph_lock=self.lock, pcc_py_gc_minor_graph_unlock=self.unlock,
            pcc_gc_pin_slot_for_take=self.pin, pcc_gc_take_pinned_slot=self.take,
            pcc_platform_abort=lambda: (_ for _ in ()).throw(RuntimeError('root failure')),
            pcc_capi_is_type_object_value=self.is_type,
            pcc_capi_builtin_type_token=self.translate,
            py_runtime_error_if_unset=self.error, PyErr_NoMemory=lambda: self.error('memory'),
            py_err_occurred=lambda: int(self.tls != 0),
            py_clear_exception=self.clear_error, py_tls_exc_swap_slot=self.swap_error,
            py_exc_new=lambda tag, message: self.new('error', message), py_raise=self.raise_error,
            py_decref=self.decref_polled, py_tuple_new=lambda count: self.tuple([0] * count),
            py_tuple_set_item=self.set_item, py_int_value_i64=lambda value: value >> 1,
            py_dict_len=self.dict_len, py_list_len=self.list_len, py_dict_items=self.dict_items,
            call_c_abi=self.foreign_typed, call_ptr2=self.foreign2, call_ptr3=self.foreign3,
            call_ptr_ptr_ptr_i64_ptr=self.foreign_fast,
            PY_TYPE_TUPLE=7, PY_TYPE_DICT=6, PY_TYPE_STR=4,
        )
        for path in (BODY, RAW):
            nodes = []
            for node in ast.parse(path.read_text()).body:
                if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id.startswith(('_METHOD_', '_VIEW_')) for t in node.targets):
                    nodes.append(node)
                elif isinstance(node, ast.FunctionDef):
                    node.decorator_list = []
                    nodes.append(node)
            exec(compile(ast.Module(body=nodes, type_ignores=[]), str(path), 'exec'), self.ns)
            if path == BODY:
                for node in nodes:
                    if isinstance(node, ast.FunctionDef):
                        fn = self.ns[node.name]
                        def managed(*args, _fn=fn, **kwargs):
                            self.poll()
                            return _fn(*args, **kwargs)
                        self.ns[node.name] = managed

    @staticmethod
    def tag(value):
        return (value << 1) | 1

    def resolve(self, value):
        while value in self.forwarded:
            value = self.forwarded[value]
        return value

    def operation(self, name):
        self.operation_count += 1
        self.events.append(name)
        return self.operation_count == self.failure

    def contains(self, address):
        return any(base <= address < base + size for base, size in self.regions.items())

    def allocate(self, size):
        base = self.next_raw
        self.next_raw += size + 16
        assert all(base + size <= old or old + old_size <= base for old, old_size in self.regions.items())
        self.regions[base] = size
        return base

    def calloc(self, count, size):
        self.poll()
        if self.operation('calloc'):
            return 0
        result = self.allocate(count * size)
        self.calloc_blocks.add(result)
        return result

    def zero(self, base, value, size):
        assert value == 0
        for address in list(self.memory):
            if base <= address < base + size:
                del self.memory[address]

    def free(self, base):
        self.poll()
        assert base in self.regions
        size = self.regions.pop(base)
        self.calloc_blocks.discard(base)
        assert not any(base <= slot < base + size for slot in self.frames)
        assert not any(isinstance(frame_map, int) and base <= frame_map < base + size for frame_map in self.frame_maps.values()), "free of live dynamic frame map"
        self.zero(base, 0, size)

    def load(self, base, offset):
        address = base + offset
        if base in self.forwarded:
            raise AssertionError(('stale pointer load', base, offset))
        if base in self.objects:
            assert self.depth or self.objects[base]['leases'] or self.objects[base]['pinned'], ('raw unleased read', base, offset)
        assert address in self.memory or self.contains(address), ('bad load', base, offset)
        return self.memory.get(address, 0)

    def load32(self, base, offset):
        if base in self.objects:
            self.require(base)
            return {'tuple': 7, 'dict': 6, 'str': 4, 'int': 1, 'self': 16, 'class': 32, 'error': 20, 'none': 0}[self.objects[base]['kind']]
        return self.load(base, offset)

    def store(self, base, offset, value):
        assert self.contains(base + offset) or base in self.objects
        self.memory[base + offset] = value

    def new(self, kind, value=None, *, record=True):
        if record:
            self.poll()
            if self.operation('alloc:' + kind):
                self.error('allocation')
                return 0
        self.next_object += 128
        obj = self.next_object
        self.objects[obj] = dict(kind=kind, value=value, refs=1, leases=0, pinned=False, immortal=False)
        return obj

    def tuple(self, children, *, record=True):
        result = self.new('tuple', len(children), record=record)
        if not result:
            return 0
        self.memory[result + 16] = len(children)
        for i, child in enumerate(children):
            self.memory[result + 24 + i * 8] = child
            self.incref(child)
        return result

    def incref(self, obj):
        if obj == self.TYPE:
            self.raw_refs[obj] += 1
        elif obj and not obj & 1 and not self.objects[obj]['immortal']:
            self.objects[obj]['refs'] += 1

    def decref(self, obj):
        if obj == self.TYPE:
            self.raw_refs[obj] -= 1
            assert self.raw_refs[obj] >= 1
            return
        if not obj or obj & 1 or self.objects[obj]['immortal']:
            return
        record = self.objects[obj]
        record['refs'] -= 1
        assert record['refs'] >= 0
        if record['refs']:
            return
        assert not record['leases'], ('free leased', obj)
        kind = record['kind']
        if kind == 'tuple':
            children = [self.memory.pop(obj + 24 + i * 8) for i in range(record['value'])]
            self.memory.pop(obj + 16)
        elif kind == 'list':
            children = [self.memory.pop(record['value'][0] + i * 8) for i in range(record['value'][1])]
            self.memory.pop(obj + 32)
        elif kind == 'dict':
            children = [child for pair in record['value'] for child in pair]
        else:
            children = []
        del self.objects[obj]
        # Model the destructor primitive's separately owned updateable child
        # roots, rather than retaining raw sibling addresses across callbacks.
        if children:
            child_roots = self.allocate(len(children) * 8)
            self.frames[child_roots] = len(children)
            for index, child in enumerate(children):
                self.memory[child_roots + index * 8] = child
            for index in range(len(children)):
                child = self.memory[child_roots + index * 8]
                self.memory[child_roots + index * 8] = 0
                self.decref(child)
            del self.frames[child_roots]
        self.events.append('finalize:' + kind)
        self.poll()
        if self.cleanup_error and kind != 'error':
            self.error('cleanup')

    def decref_polled(self, obj):
        self.poll()
        self.decref(obj)

    def poll(self):
        for slots, frame_map in self.frame_maps.items():
            if isinstance(frame_map, int):
                assert self.contains(frame_map)
                assert self.memory[frame_map] == self.frames[slots], "mutated live frame map"
        if self.depth or self.graph:
            return
        moving = [obj for obj, rec in self.objects.items() if not rec['leases'] and not rec['pinned'] and not rec['immortal']]
        if not moving:
            return
        changes = {}
        for old in moving:
            self.next_object += 128
            changes[old] = self.next_object
        for old, new in changes.items():
            rec = self.objects.pop(old)
            self.objects[new] = rec
            self.forwarded[old] = new
            if rec['kind'] == 'tuple':
                self.memory[new + 16] = self.memory.pop(old + 16)
                for i in range(rec['value']):
                    self.memory[new + 24 + i * 8] = self.memory.pop(old + 24 + i * 8)
            elif rec['kind'] == 'list':
                self.memory[new + 32] = self.memory.pop(old + 32)
        for base, count in self.frames.items():
            for i in range(abs(count)):
                addr = base + i * 8
                self.memory[addr] = changes.get(self.memory.get(addr, 0), self.memory.get(addr, 0))
        for obj, rec in self.objects.items():
            if rec['kind'] == 'tuple':
                for i in range(rec['value']):
                    addr = obj + 24 + i * 8
                    self.memory[addr] = changes.get(self.memory[addr], self.memory[addr])
            elif rec['kind'] == 'dict':
                rec['value'] = [(changes.get(k, k), changes.get(v, v)) for k, v in rec['value']]
            elif rec['kind'] == 'list':
                for i in range(rec['value'][1]):
                    addr = rec['value'][0] + i * 8
                    self.memory[addr] = changes.get(self.memory[addr], self.memory[addr])
        self.tls = changes.get(self.tls, self.tls)
        self.relocations += len(changes)

    def rooted(self, slot):
        return any(base <= slot < base + abs(count) * 8 for base, count in self.frames.items())

    def require(self, obj):
        if obj in (0, self.TYPE) or obj & 1:
            return
        assert obj in self.objects, ('dead or stale raw pointer', obj)
        assert self.depth or self.objects[obj]['leases'] or self.objects[obj]['pinned'], ('unleased object', obj)

    def enter(self):
        self.depth += 1

    def exit(self):
        assert self.depth == 1 and self.graph == 0
        self.depth -= 1
        self.poll()

    def register(self, frame_map, slots):
        assert self.depth > 0 and self.graph == 0
        self.register_count += 1
        if self.register_count == self.fail_registration:
            return 0
        count = self.static_maps[frame_map] if isinstance(frame_map, str) else self.memory[frame_map]
        assert 0 < abs(count) <= 100000
        assert slots not in self.frames
        self.frames[slots] = count
        self.frame_maps[slots] = frame_map
        return 1

    def leave(self, slots):
        self.poll()
        del self.frames[slots]
        self.frame_maps.pop(slots, None)

    def copy(self, destination, source):
        self.poll()
        assert self.rooted(destination) and self.memory.get(destination, 0) == 0
        if self.operation('copy'):
            return -1
        obj = self.memory.get(source, 0)
        assert obj != self.TYPE, 'native type must never enter an owning managed slot'
        self.incref(obj)
        self.memory[destination] = obj
        if obj and not obj & 1 and not self.objects[obj]['immortal']:
            self.objects[obj]['leases'] += 1
            return 1
        return 0

    def acquire(self, slot):
        self.poll()
        assert self.rooted(slot)
        if self.operation('lease'):
            return -1
        obj = self.memory.get(slot, 0)
        assert obj != self.TYPE
        if obj and not obj & 1 and not self.objects[obj]['immortal']:
            self.objects[obj]['leases'] += 1
            return 1
        return 0

    def release(self, slot, token):
        self.poll()
        assert token in (0, 1)
        if token:
            obj = self.memory[slot]
            assert self.objects[obj]['leases'] > 0
            self.objects[obj]['leases'] -= 1
        return 0

    def store_root(self, slot, value):
        self.poll()
        assert self.rooted(slot) and value == 0
        old = self.memory.get(slot, 0)
        self.memory[slot] = 0
        self.decref(old)

    def barrier(self, owner, slot, value):
        assert self.graph and self.rooted(slot)
        self.require(value)

    def lock(self):
        self.poll()
        assert not self.graph
        self.graph = 1

    def unlock(self):
        assert self.graph == 1
        self.graph = 0
        self.poll()

    def pin(self, slot, keep):
        self.poll()
        obj = self.memory.get(slot, 0)
        if not obj or obj & 1:
            return 0
        prior = int(self.objects[obj]['pinned'])
        self.objects[obj]['pinned'] = True
        return prior

    def take(self, slot, prior):
        assert not self.rooted(slot)
        obj = self.memory.get(slot, 0)
        self.memory[slot] = 0
        if obj and not obj & 1:
            assert self.objects[obj]['pinned']
            self.objects[obj]['pinned'] = bool(prior)
        return obj

    def is_type(self, value):
        self.poll()
        return int(value == self.TYPE)

    def translate(self, value):
        self.poll()
        self.require(value)
        return self.TYPE if self.objects.get(value, {}).get('kind') == 'class' else value

    def error(self, *message):
        self.poll()
        if not self.tls:
            self.tls = self.new('error', message, record=False)
            if not self.original_error:
                self.original_error = self.tls
        return 0

    def raise_error(self, value):
        self.poll()
        self.require(value)
        assert self.tls == 0
        self.incref(value)
        self.tls = value
        self.original_error = self.original_error or value

    def clear_error(self):
        self.poll()
        error = self.tls
        self.tls = 0
        self.decref(error)

    def swap_error(self, slot):
        self.poll()
        assert self.rooted(slot)
        self.memory[slot], self.tls = self.tls, self.memory.get(slot, 0)

    def set_item(self, obj, index, item):
        self.poll()
        self.require(obj)
        self.require(item)
        slot = obj + 24 + index * 8
        old = self.memory[slot]
        self.incref(item)
        self.memory[slot] = item
        self.decref(old)

    def dict_len(self, obj):
        self.poll()
        self.require(obj)
        return len(self.objects[obj]['value'])

    def list_len(self, obj):
        self.poll()
        self.require(obj)
        return self.objects[obj]['value'][1]

    def dict_items(self, obj):
        self.poll()
        self.require(obj)
        if self.operation('dict_items'):
            self.error('dictionary snapshot')
            return 0
        # One modeled atomic producer: its own separately tested slot protocol
        # returns a NEW private snapshot; this test audits the caller boundary.
        pairs = [self.tuple([k, v], record=False) for k, v in self.objects[obj]['value']]
        items = self.allocate(len(pairs) * 8)
        result = self.new('list', (items, len(pairs)), record=False)
        self.memory[result + 32] = items
        for i, pair in enumerate(pairs):
            self.memory[items + i * 8] = pair
        return result

    def foreign(self, self_obj, args, kwargs=0, vector=0, count=None, names=0):
        assert self.depth == 0 and self.graph == 0
        self.called += 1
        self.require(self_obj)
        if args:
            self.require(args)
        keyword_items = []
        if kwargs:
            self.require(kwargs)
            if kwargs != self.NONE:
                keyword_items = [item for pair in self.objects[kwargs]['value'] for item in pair]
                for item in keyword_items:
                    self.require(item)
        if names:
            self.require(names)
        positional = [self.memory[vector + i * 8] for i in range(count)] if vector else ([self.memory[args + 24 + i * 8] for i in range(self.memory[args + 16])] if args else [])
        for item in positional:
            self.require(item)
        self.argument_leases = [self.objects[item]['leases'] if item in self.objects else 0 for item in positional]
        self.poll()  # Arbitrary callback collection / reentry is allowed.
        for item in keyword_items:
            self.require(item)
        for item in positional:
            self.require(item)
        if self.mode == 'null':
            return 0
        if self.mode == 'error':
            self.error('foreign original')
            return 0
        if self.mode == 'reenter':
            self.mode = 'new'
            inner = self.ns['pcc_capi_method_call_entry'](self.resolve(self.captures), self.resolve(self.args))
            self.mode = 'reenter'
            assert inner
            self.decref(inner)  # Immediate NEW-owner disposal, with no prior poll.
        if self.mode == 'type_error':
            self.incref(self.TYPE)
            self.tls = self.new('error', 'foreign type original', record=False)
            self.original_error = self.tls
            return self.TYPE
        if self.mode in ('self', 'arg', 'args', 'type'):
            result = {'self': self_obj, 'arg': positional[0] if positional else 0,
                      'args': args, 'type': self.TYPE}[self.mode]
            self.incref(result)
            return result
        result = self.new('int', 2**63 - 2, record=False)
        self.result_created = result
        if self.mode == 'result_error':
            # The foreign result stays unregistered until return. This models
            # its lifetime handoff, not arbitrary foreign locals across polls.
            self.tls = self.new('error', 'foreign original', record=False)
            self.original_error = self.tls
        return result

    def foreign2(self, fn, self_obj, arg):
        assert fn == self.FUNCTION
        convention = self.flags & 143
        if convention == 4:
            assert arg == 0, 'METH_NOARGS needs its NULL second argument'
            return self.foreign(self_obj, 0)
        if convention == 8:
            vector = self.allocate(8)
            self.memory[vector] = arg
            return self.foreign(self_obj, 0, vector=vector, count=1)
        return self.foreign(self_obj, arg)

    def foreign3(self, fn, self_obj, args, kwargs):
        assert fn == self.FUNCTION
        return self.foreign(self_obj, args, kwargs)

    def foreign_fast(self, fn, self_obj, vector, positional, names):
        assert fn == self.FUNCTION
        assert self.flags & 143 == 130, 'only keyword FASTCALL has four arguments'
        if self.flags & 2 and not self.empty_keywords:
            assert names and self.memory[names + 16] == self.keyword_count
            for index in range(self.keyword_count):
                self.require(self.memory[names + 24 + index * 8])
                self.require(self.memory[vector + (positional + index) * 8])
        else:
            assert names == 0
        return self.foreign(self_obj, 0, vector=vector, count=positional, names=names)

    def foreign_typed(self, fn, restype, argtypes, values, destination):
        assert fn == self.FUNCTION and self.flags & 143 == 128
        assert restype == 'ptr' and argtypes == ('ptr', 'ptr', 'i64')
        assert len(values) == 3, 'plain FASTCALL has no keyword-name argument'
        assert self.contains(destination) and not self.rooted(destination)
        self_obj, vector, positional = values
        # The intrinsic stores the return immediately; raw operands have no
        # generated managed cleanup between the callback and this raw store.
        result = self.foreign(self_obj, 0, vector=vector, count=positional)
        self.store(destination, 0, result)

    def run(self, *, null_args=False):
        return self.ns['pcc_capi_method_call_entry'](self.resolve(self.captures), 0 if null_args else self.resolve(self.args))

    def assert_balanced(self, result=0):
        assert self.depth == self.graph == 0 and not self.frames and not self.frame_maps
        assert not self.calloc_blocks, ("leaked raw call-view allocations", self.calloc_blocks)
        assert all(rec['leases'] == 0 for rec in self.objects.values())
        for old, refs in self.baseline.items():
            obj = self.resolve(old)
            assert self.objects[obj]['refs'] == refs + int(result == obj and not self.objects[obj]['immortal'])
        assert self.raw_refs[self.TYPE] == self.raw_baseline[self.TYPE] + int(result == self.TYPE)
        expected = {self.resolve(obj) for obj in self.baseline}
        if result and not result & 1 and result != self.TYPE:
            expected.add(result)
        if self.tls:
            expected.add(self.tls)
        assert set(self.objects) == expected


def test_checked_model_raw_allocation_regions_do_not_overlap():
    model = MethodModel()
    first = model.allocate(128)
    second = model.allocate(8)
    third = model.allocate(64)
    assert second >= first + 128
    assert third >= second + 8


@pytest.mark.parametrize('flags', [1, 3, 4, 8, 128, 130, 65, 67, 68])
def test_actual_dispatch_all_supported_flags_roots_children_through_callbacks(flags):
    model = MethodModel(flags=flags)
    result = model.run()
    assert model.called == 1 and not model.tls
    assert model.objects[result]['value'] == 2**63 - 2
    assert model.relocations > 0
    model.assert_balanced(result)


@pytest.mark.parametrize('mode', ['new', 'self', 'arg', 'type', 'null', 'error',
                                'result_error', 'type_error', 'reenter'])
@pytest.mark.parametrize('prior_pin', [False, True])
def test_plain_fastcall_exact_abi_preserves_result_tls_and_prior_pins(mode, prior_pin):
    model = MethodModel(flags=128, mode=mode, cleanup_error=True)
    model.objects[model.self_obj]['pinned'] = prior_pin
    model.objects[model.arg0]['pinned'] = prior_pin
    result = model.run()
    assert model.called == (2 if mode == 'reenter' else 1)
    assert model.objects[model.resolve(model.self_obj)]['pinned'] == prior_pin
    assert model.objects[model.resolve(model.arg0)]['pinned'] == prior_pin
    # With cleanup_error enabled, reentry's explicit disposal of the inner
    # NEW result raises inside the foreign callback, before it returns its
    # outer result. That is result+error, not ignorable boundary-cleanup noise.
    if mode in ('null', 'error', 'result_error', 'type_error', 'reenter'):
        assert result == 0 and model.tls == model.resolve(model.original_error)
    else:
        assert result and not model.tls
    model.assert_balanced(result)


@pytest.mark.parametrize('flags', [3, 130])
def test_multiple_keyword_children_and_snapshot_pairs_survive_callbacks(flags):
    model = MethodModel(flags=flags, two_keywords=True, cleanup_error=True)
    result = model.run()
    assert model.called == 1 and not model.tls
    model.assert_balanced(result)


@pytest.mark.parametrize('mode', ['self', 'arg', 'args', 'type'])
def test_legitimate_new_alias_is_an_independent_owner(mode):
    model = MethodModel(mode=mode)
    result = model.run()
    assert result
    model.assert_balanced(result)


@pytest.mark.parametrize('mode', ['null', 'error', 'result_error', 'type_error'])
def test_result_errors_and_finalizer_errors_preserve_original_tls(mode):
    model = MethodModel(mode=mode, flags=130, cleanup_error=True)
    assert model.run() == 0
    assert model.tls == model.resolve(model.original_error)
    assert model.called == 1
    model.assert_balanced()


def test_success_discards_cleanup_error_and_preserves_result_pin():
    model = MethodModel(mode='self', flags=4, cleanup_error=True)
    model.objects[model.self_obj]['pinned'] = True
    result = model.run(null_args=True)
    assert model.objects[result]['pinned']
    assert not model.tls
    model.assert_balanced(result)


@pytest.mark.parametrize('flags', [3, 130])
@pytest.mark.parametrize('mode', ['new', 'error', 'result_error'])
def test_keyword_mapping_preserves_its_preexisting_boolean_pin(flags, mode):
    model = MethodModel(flags=flags, mode=mode, cleanup_error=True)
    model.objects[model.kwargs]['pinned'] = True
    result = model.run()
    assert model.objects[model.resolve(model.kwargs)]['pinned']
    assert model.called == 1
    model.assert_balanced(result)


@pytest.mark.parametrize('raw_self,translated', [(True, False), (False, True), (True, True)])
def test_native_type_self_and_translated_args_never_enter_managed_leases(raw_self, translated):
    model = MethodModel(raw_self=raw_self, translated=translated)
    result = model.run()
    assert model.called == 1 and not model.tls
    model.assert_balanced(result)


@pytest.mark.parametrize('failure', range(1, 42))
def test_checked_allocation_copy_and_lease_failures_balance_every_owner(failure):
    model = MethodModel(flags=130, failure=failure, cleanup_error=True)
    result = model.run()
    if model.operation_count >= failure:
        assert not result and model.tls
    model.assert_balanced(result)


@pytest.mark.parametrize('registration', [1, 2])
def test_raw_entry_registration_failure_is_fatal_without_polling(registration):
    model = MethodModel(fail_registration=registration)
    with pytest.raises(RuntimeError, match='root failure'):
        model.run()
    assert model.depth == 1 and not model.called and not model.frames
    assert model.relocations == 0


def test_dynamic_registration_failure_is_recoverable_after_fixed_roots_exist():
    model = MethodModel(fail_registration=3)
    assert model.run() == 0 and model.tls and not model.called
    model.assert_balanced()


def test_native_type_input_is_rejected_without_registering_a_raw_type():
    model = MethodModel()
    assert model.ns['pcc_capi_method_call_entry'](model.TYPE, model.args) == 0
    assert model.tls and not model.called
    model.assert_balanced()


@pytest.mark.parametrize('flags', [0, 2, 5, 9, 12, 17, 33, 129, 132, 512, 641])
def test_unsupported_call_abis_are_explicit(flags):
    model = MethodModel(flags=flags)
    assert model.run() == 0 and model.tls and not model.called
    model.assert_balanced()


def test_dynamic_frames_chunk_at_registry_bound_without_an_arity_shortcut():
    model = MethodModel()
    view = model.allocate(64)
    assert model.ns['_method_view_allocate'](view, 100001, 0) == 1
    assert list(model.frames.values()) == [65536, 34465]
    assert model.memory[view + 32] == 2
    assert model.depth == model.graph == 0



def test_repeated_positional_and_keyword_aliases_have_independent_counts():
    model = MethodModel(mode='arg', flags=130, repeated=True)
    result = model.run()
    assert model.argument_leases == [3, 3]
    model.assert_balanced(result)


@pytest.mark.parametrize('flags', [128, 130])
def test_nested_callback_can_reenter_and_collect_without_held_locks(flags):
    model = MethodModel(mode='reenter', flags=flags)
    result = model.run()
    assert result and model.called == 2 and not model.tls
    assert model.objects[result]['value'] == 2**63 - 2
    model.assert_balanced(result)


@pytest.mark.parametrize('flags', [3, 130])
def test_none_keyword_mapping_keeps_the_existing_empty_keyword_convention(flags):
    model = MethodModel(flags=flags, empty_keywords=True)
    result = model.run()
    assert model.called == 1 and not model.tls
    model.assert_balanced(result)


def test_inherited_no_park_fails_before_registration_or_callback():
    model = MethodModel()
    model.depth = 1
    with pytest.raises(RuntimeError, match='root failure'):
        model.run()
    assert model.register_count == model.called == model.operation_count == 0
    assert model.depth == 1 and not model.frames


def test_later_chunk_registration_failure_unwinds_only_registered_chunks():
    model = MethodModel(fail_registration=2)
    slots = model.allocate(128)
    tokens = model.allocate(128)
    raw_result = model.allocate(8)
    view = model.allocate(64)
    model.frames[slots] = 16
    assert model.ns['_method_view_allocate'](view, 100001, 0) == 0
    assert list(model.frames.values()) == [16, 65536]
    assert model.memory[view + 32] == 1
    # Failed registration occurs before filling any owner. Model the no-op
    # NULL slot drop directly so this unwind test does not simulate 65536
    # redundant moving collections; production finish still runs its loop.
    def empty_drop(base, leases, index):
        assert model.memory.get(base + index * 8, 0) == 0
        assert model.memory.get(leases + index * 8, 0) == 0
    model.ns['_method_drop'] = empty_drop
    assert model.ns['_method_finish'](slots, tokens, view, raw_result) == 0
    assert model.frames == {slots: 16}
    assert model.tls == model.resolve(model.original_error)
    assert not any(rec['leases'] for rec in model.objects.values())
    assert not model.calloc_blocks
    for index in (0, 1, 2, 7):
        assert model.memory[view + index * 8] not in model.regions


def test_real_entry_and_body_ir_enforce_the_slot_boundary(tmp_path, monkeypatch):
    from pcc.frontends.python.owned_runtime_build import _compile_runtime_module

    def function_body(text, symbol):
        matches = re.findall(r'^define[^\n]*@' + re.escape(symbol) + r'\([^\n]*\).*?^}',
                             text, re.M | re.S)
        assert len(matches) == 1, (symbol, len(matches))
        return matches[0]

    def next_reachable_call(text, offset):
        # Follow the actual plain-FASTCALL successor path, not unrelated
        # keyword/other-convention blocks appearing later in textual IR.
        labels = list(re.finditer(r'(?m)^([\w.]+):\n', text))
        blocks = {label.group(1): text[label.end():
                  labels[index + 1].start() if index + 1 < len(labels) else len(text)]
                  for index, label in enumerate(labels)}
        end = next((label.start() for label in labels if label.start() >= offset), len(text))
        remaining = text[offset:end]
        visited = set()
        while True:
            call = re.search(r'(?m)^\s*(?:%[^\s]+ = )?call\b[^\n]*$', remaining)
            if call is not None:
                return call.group(0)
            branch = re.search(r'(?m)^\s*br label %([\w.]+)\s*$', remaining)
            assert branch is not None, remaining
            target = branch.group(1)
            assert target not in visited, target
            visited.add(target)
            remaining = blocks[target]

    monkeypatch.delenv('PCC_IMPLICIT_THREAD_SAFEPOINTS', raising=False)
    texts = {}
    bridge_source = RAW.with_name('py_capi_method_bridge_runtime.py')
    for source in (RAW, BODY, bridge_source):
        output = tmp_path / (source.stem + '.ll')
        _compile_runtime_module(source.stem, str(source), str(output), 'x86_64-unknown-linux-gnu')
        texts[source] = output.read_text()
        assert 'strict.nolib.stub' not in texts[source]
    raw = texts[RAW]
    for name in ('pcc_capi_method_call_entry', 'pcc_capi_method_invoke_into',
                 'pcc_capi_method_call_entry_address'):
        body = function_body(raw, name)
        assert '@pcc_thread_stop_requested' not in body
        assert '@pcc_gc_frame_enter(' not in body
        assert '@pcc_gc_store_root' not in body
    entry = function_body(raw, 'pcc_capi_method_call_entry')
    assert entry.count('@pcc_gc_note_frame_enter_checked(') == 2
    assert entry.index('@pcc_thread_no_park_enter(') < entry.index('@pcc_gc_note_frame_enter_checked(')
    assert entry.index('@pcc_thread_no_park_exit(') < entry.index('@pcc_capi_method_call_into(')
    invoke = function_body(raw, 'pcc_capi_method_invoke_into')
    assert '@pcc_capi_is_type_object_value(' in invoke
    assert '@pcc_thread_no_park_enter(' in invoke and '@pcc_thread_no_park_exit(' in invoke
    assert re.search(r'call ptr \(ptr, ptr\) %[^\n]+\(ptr [^,]+, ptr null\)', invoke)
    # The plain and keyword FASTCALL prototypes must be distinct. The generic
    # typed intrinsic uses syntax-only operands and immediate raw result store,
    # never a managed temporary/cleanup or a fourth dummy argument.
    fast = re.search(r'(?m)^\s*(%[^ ]+) = call ptr \(ptr, ptr, i64\) %[^\n]+$', invoke)
    assert fast is not None
    assert len(re.findall(r'call ptr \(ptr, ptr, i64\) %', invoke)) == 1
    assert len(re.findall(r'call ptr \(ptr, ptr, i64, ptr\) %', invoke)) == 1
    after_fast = invoke[fast.end():]
    store = re.search(r'store ptr ' + re.escape(fast.group(1)) + r', ptr %[^\n]+', after_fast)
    assert store is not None and 'call ' not in after_fast[:store.start()]
    assert '@pcc_thread_no_park_enter(' in next_reachable_call(invoke, fast.end())
    assert '@py_tuple_new(' not in invoke and '@pcc_gc_retain' not in invoke
    assert '@pcc_gc_release' not in invoke
    assert '@pcc_gc_pin(' not in invoke and '@pcc_gc_unpin(' not in invoke
    # Audit every production body/helper, not generated module initialization
    # (which legitimately roots import/function metadata) or declarations.
    # Those unrelated symbols previously made this assertion fail even though
    # none of the method functions had an implicit managed scratch frame.
    managed_bodies = {}
    for node in ast.parse(BODY.read_text()).body:
        if isinstance(node, ast.FunctionDef):
            symbol = (node.name if node.name == 'pcc_capi_method_call_into'
                      else 'user_' + BODY.stem + '_' + node.name)
            managed_bodies[node.name] = function_body(texts[BODY], symbol)
    assert 'pcc_capi_method_call_into' in managed_bodies
    for name, body in managed_bodies.items():
        assert '@pcc_thread_stop_requested' in body, name
        # Scalars, slot addresses and metadata must never create implicit
        # managed cleanup or boxed-index arithmetic in any method helper.
        forbidden = ('@py_tuple_get(', '@pcc_gc_frame_enter',
                     '@pcc_gc_store_root_take(', '@py_int_add(', '@py_int_mul(',
                     '@pcc_gc_pin(', '@pcc_gc_unpin(', '@pcc_gc_retain',
                     '@py_obj_eq_value(', '@py_obj_ne_value(')
        offending_lines = [line for line in body.splitlines()
                           if any(symbol in line for symbol in forbidden)]
        assert not offending_lines, (name, offending_lines)
    managed = '\n'.join(managed_bodies.values())
    assert '@pcc_gc_root_copy_lease(' in managed
    assert '@pcc_gc_root_copy_borrowed_lease(' in managed
    assert '@pcc_capi_method_invoke_into(' in managed
    assert '@py_tls_exc_swap_slot(' in managed


def test_build_policies_and_fixed_layout_match():
    from pcc.frontends.python.owned_runtime_build import _NO_IMPLICIT_POLL_MODULES
    assert RAW.stem in _NO_IMPLICIT_POLL_MODULES
    assert BODY.stem not in _NO_IMPLICIT_POLL_MODULES
    makefile = (ROOT / 'pcc/runtime/Makefile').read_text()
    rule = makefile.split('$(OBJDIR_PY)/py_capi_method_entry.o:', 1)[1].split('\n\n', 1)[0]
    assert 'PCC_IMPLICIT_THREAD_SAFEPOINTS=0' in rule
    assert '_METHOD_SLOT_COUNT = 16' in RAW.read_text()
    assert '_METHOD_SLOT_COUNT = 16' in BODY.read_text()
    bridge = RAW.with_name('py_capi_method_bridge_runtime.py').read_text()
    assert 'def pcc_capi_method_call_entry(' not in bridge
    assert 'return pcc_capi_method_call_entry_address()' in bridge
    assert 'function_addr("pcc_capi_method_call_entry")' in RAW.read_text()
