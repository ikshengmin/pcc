"""Execute runtime getter producers with relocation at their actual park gates."""

import ast
from pathlib import Path

import pytest

from pcc.runtime.py import py_abi_constants as abi
from test_exception_constructor_roots import _Object, _Slots


ROOT = Path(__file__).resolve().parents[2]


class _GetterMemory:
    def __init__(self, backend=4, phase="unregister", prior_pin=False, fail_register=0):
        self.backend, self.phase, self.fail_register = backend, phase, fail_register
        self.objects, self.payloads, self.handles, self.forward = [], [], {}, {}
        self.depth = self.moves = self.pins = self.registrations = 0
        self.error = None
        self.item = self.make(abi.PY_TYPE_STR, abi.PY_FLAG_GC_PINNED if prior_pin else 0)
        self.items = _Slots(8)
        self.items.fields[0] = self.item
        self.payloads.append(self.items)
        self.lst = self.make(abi.PY_TYPE_LIST)
        self.lst.fields.update({abi.PYLISTOBJECT_LENGTH_OFFSET: 1,
                                abi.PYLISTOBJECT_CAPACITY_OFFSET: 1,
                                abi.PYLISTOBJECT_ITEMS_OFFSET: self.items})
        self.namespace = self._namespace()
        self.load("pcc/runtime/py/py_list.py")

    def load(self, relative):
        path = ROOT / relative
        parsed = ast.parse(path.read_text(), filename=str(path))
        functions = [node for node in parsed.body if isinstance(node, ast.FunctionDef)]
        exec(compile(ast.Module(body=functions, type_ignores=[]), str(path), "exec"), self.namespace)

    def make(self, tag, flags=0):
        value = _Object(tag, flags)
        self.objects.append(value)
        return value

    def resolve(self, value):
        while isinstance(value, _Object) and value in self.forward:
            value = self.forward[value]
        return value

    def pointer(self, value):
        return value if isinstance(value, tuple) else (value, 0)

    def add(self, value, offset):
        owner, base = self.pointer(value)
        return owner, base + offset

    def read(self, value, offset):
        value, start = self.pointer(value)
        offset += start
        if isinstance(value, _Object):
            assert value.alive, "stale receiver/field address"
            if offset == 0:
                return value.references
            if offset == abi.PYOBJECTHEADER_TYPE_TAG_OFFSET:
                return value.tag
            if offset == abi.PYOBJECTHEADER_FLAGS_OFFSET:
                return value.flags
        return value.fields.get(offset)

    def write(self, value, offset, item):
        value, start = self.pointer(value)
        offset += start
        if isinstance(value, _Object):
            assert value.alive
            if offset == abi.PYOBJECTHEADER_FLAGS_OFFSET:
                value.flags = item
                return
        value.fields[offset] = item

    def gc(self, phase):
        if self.depth or phase != self.phase or self.backend not in (3, 4):
            return
        for old in list(self.objects):
            if not old.alive or old.flags & (abi.PY_FLAG_IMMORTAL | abi.PY_FLAG_GC_PINNED):
                continue
            new = self.make(old.tag, old.flags)
            new.references, new.fields = old.references, old.fields.copy()
            self.forward[old] = new
            for slot in self.handles.values():
                owner, offset = self.pointer(slot)
                if self.read(owner, offset) is old:
                    self.write(owner, offset, new)
            for obj in self.objects:
                if obj.alive:
                    obj.fields = {offset: new if value is old else value
                                  for offset, value in obj.fields.items()}
            for payload in self.payloads:
                payload.fields = {offset: new if value is old else value
                                  for offset, value in payload.fields.items()}
            if self.item is old:
                self.item = new
            if self.lst is old:
                self.lst = new
            old.alive = False
            self.moves += 1

    def register(self, slot):
        self.registrations += 1
        if self.registrations == self.fail_register:
            return None
        handle = object()
        self.handles[handle] = slot
        return handle

    def unregister(self, handle):
        # Registry lock acquisition can park before unlinking the root.
        self.gc("unregister")
        del self.handles[handle]

    def lock(self):
        self.gc("graph_lock")
        self.depth += 1

    def unlock(self):
        self.depth -= 1
        self.gc("graph_unlock")

    def incref(self, value):
        if isinstance(value, _Object):
            assert value.alive, "stale result during retain"
            if not value.flags & abi.PY_FLAG_IMMORTAL:
                value.references += 1

    def retain(self, plan, value):
        assert self.depth > 0
        self.incref(value)
        plan.fields[0] = value
        return value

    def retain_finish(self, plan):
        self.gc("retain_finish")

    def pin(self, value):
        if isinstance(value, _Object):
            assert value.alive
            value.flags |= abi.PY_FLAG_GC_PINNED
            self.pins += 1

    def take(self, slot, prior):
        owner, offset = self.pointer(slot)
        value = self.read(owner, offset)
        self.write(owner, offset, None)
        if isinstance(value, _Object):
            assert value.alive and value.flags & abi.PY_FLAG_GC_PINNED
            value.flags = (value.flags & ~abi.PY_FLAG_GC_PINNED) | prior
            self.pins -= 1
        return value

    def take_list_item(self, _owner, slot):
        owner, offset = self.pointer(slot)
        value = self.read(owner, offset)
        self.write(owner, offset, None)
        return value

    def _namespace(self):
        namespace = {name: getattr(abi, name) for name in dir(abi) if name.isupper()}
        namespace.update({
            "c_abi_export": lambda _name: lambda function: function,
            "c_ptr": object, "c_int64": int, "null": lambda: None,
            "ptr_is_null": lambda value: value is None,
            "is_tagged_int": lambda value: isinstance(value, int),
            "ptr_eq": lambda first, second: first is second,
            "ptr_add": self.add, "stack_alloc": _Slots,
            "load_ptr": self.read, "load_i64": self.read, "load_i32": self.read,
            "store_ptr": self.write,
            "pcc_gc_load_ptr": lambda _owner, slot: self.read(*self.pointer(slot)),
            "pcc_gc_pointer_is_managed": lambda value: isinstance(value, _Object) and value.alive,
            "pcc_gc_backend": lambda: self.backend,
            "pcc_gc_scheduler_root_register_handle": self.register,
            "pcc_gc_scheduler_root_unregister_handle": self.unregister,
            "pcc_py_gc_minor_graph_lock": self.lock,
            "pcc_py_gc_minor_graph_unlock": self.unlock,
            "pcc_gc_retain_plan_prepare_locked": self.retain,
            "pcc_gc_retain_plan_finish": self.retain_finish,
            "pcc_gc_pin": self.pin, "pcc_gc_take_pinned_slot": self.take,
            "py_incref": self.incref, "pcc_gc_try_take_ptr": self.take_list_item,
            "cstr": lambda text: text, "py_exc_new": lambda tag, text: (tag, text),
            "py_raise_owned": lambda error: setattr(self, "error", error),
        })
        return namespace


@pytest.mark.parametrize("name", ("py_list_get", "py_list_getitem", "py_list_get_for_frame"))
@pytest.mark.parametrize("backend", (3, 4))
@pytest.mark.parametrize("phase", ("graph_unlock", "retain_finish", "unregister"))
def test_list_getter_returns_current_owned_element_after_actual_cleanup_park(name, backend, phase):
    memory = _GetterMemory(backend=backend, phase=phase)
    result = memory.namespace[name](memory.lst, 0)
    assert result is memory.item and result.alive
    assert result.references == 2
    assert memory.moves > 0
    assert not memory.handles and memory.depth == 0 and memory.pins == 0


@pytest.mark.parametrize("backend", (0, 1, 2))
@pytest.mark.parametrize("name", ("py_list_get", "py_list_getitem", "py_list_get_for_frame"))
def test_list_getter_nonmoving_paths_keep_reference_contract(name, backend):
    memory = _GetterMemory(backend=backend)
    result = memory.namespace[name](memory.lst, 0)
    assert result is memory.item
    assert result.references == (1 if backend == 0 and name == "py_list_get_for_frame" else 2)
    assert not memory.handles and not memory.moves and not memory.pins


@pytest.mark.parametrize("name", ("py_list_get", "py_list_getitem", "py_list_get_for_frame"))
def test_list_getter_restores_existing_pin(name):
    memory = _GetterMemory(prior_pin=True)
    result = memory.namespace[name](memory.lst, 0)
    assert result is memory.item and result.flags & abi.PY_FLAG_GC_PINNED
    assert result.references == 2
    assert not memory.handles and not memory.pins


@pytest.mark.parametrize("name", ("py_list_get", "py_list_getitem", "py_list_get_for_frame"))
@pytest.mark.parametrize("backend", (3, 4))
@pytest.mark.parametrize("value", (None, 85))
def test_list_getter_null_and_tagged_elements_need_no_pin(name, backend, value):
    memory = _GetterMemory(backend=backend)
    memory.items.fields[0] = value
    assert memory.namespace[name](memory.lst, -1) == value
    assert not memory.handles and not memory.pins and memory.error is None


@pytest.mark.parametrize("name", ("py_list_get", "py_list_getitem", "py_list_get_for_frame"))
@pytest.mark.parametrize("failed_root", (1, 2))
def test_list_getter_registration_failure_does_not_acquire_element_owner(name, failed_root):
    memory = _GetterMemory(fail_register=failed_root)
    assert memory.namespace[name](memory.lst, 0) is None
    assert memory.item.references == 1
    assert not memory.handles and not memory.pins


@pytest.mark.parametrize("name", ("py_list_get", "py_list_getitem", "py_list_get_for_frame"))
def test_list_getter_out_of_range_balances_roots_and_keeps_raising_contract(name):
    memory = _GetterMemory()
    assert memory.namespace[name](memory.lst, 1) is None
    assert memory.item.references == 1
    assert not memory.handles and not memory.pins
    assert memory.error == ((5, "list index out of range") if name == "py_list_getitem" else None)


class _FieldMemory(_GetterMemory):
    def __init__(self, backend=4, stale_receiver=False):
        super().__init__(backend=backend, phase="before_call")
        self.klass = self.make(abi.PY_TYPE_CLASS, abi.PY_FLAG_IMMORTAL)
        self.klass.fields[abi.PYCLASSOBJECT_N_FIELDS_OFFSET] = 1
        self.record = self.make(abi.PY_TYPE_USER_CLASS_START)
        self.record.fields[abi.PYINSTANCEOBJECT_CLS_OFFSET] = self.klass
        self.record.fields[abi.PYINSTANCEOBJECT_FIELDS_OFFSET] = self.item
        self.original = self.record
        self.globals = {name: _Slots(8) for name in
                        ("pcc_gc_read_barrier_enabled", "pcc_gc_backend_selected")}
        self.globals["pcc_gc_read_barrier_enabled"].fields[0] = int(backend in (3, 4))
        self.globals["pcc_gc_backend_selected"].fields[0] = backend
        self.namespace.update({"global_addr": self.globals.__getitem__,
                               "pcc_gc_note_relocation_read": self.resolve})
        self.load("pcc/runtime/py/py_class.py")
        if stale_receiver:
            self.gc("before_call")
            self.record = self.resolve(self.record)


@pytest.mark.parametrize("backend", (3, 4))
@pytest.mark.parametrize("name", ("py_instance_get_field", "py_valuebox_get_field"))
def test_instance_field_getter_uses_healed_receiver_before_deriving_field_slot(backend, name):
    memory = _FieldMemory(backend=backend, stale_receiver=True)
    assert not memory.original.alive and memory.record.alive
    result = memory.namespace[name](memory.original, 0)
    assert result is memory.item and result.alive
    assert result.references == 2
    assert not memory.handles and memory.depth == 0 and memory.pins == 0


@pytest.mark.parametrize("backend", (0, 1, 2))
def test_instance_field_getter_keeps_nonmoving_fast_path(backend):
    memory = _FieldMemory(backend=backend)
    result = memory.namespace["py_instance_get_field"](memory.record, 0)
    assert result is memory.item and result.references == 2
    assert not memory.moves and not memory.handles and not memory.pins
