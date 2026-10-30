"""Run production module-state bodies against checked eight-byte pointer slots.

These host component tests prove the requested record extent and its readers,
not native codegen, concurrent publication, collector safety, or unregister /
m_clear / m_free lifecycle support (which this registry does not implement).
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest


RUNTIME = (
    Path(__file__).resolve().parents[2]
    / "pcc/runtime/py/py_capi_module_state_runtime.py"
)


class _Block:
    def __init__(self, size):
        self.size = size
        self.fields = {}
        self.alive = True


class _Memory:
    def __init__(self):
        self.head = _Block(8)
        self.globals = {"pcc_capi_module_states": self.head}
        self.allocations = []
        self.fail_allocation = None
        self.freed = []
        self.known = set()
        self.known_checks = []
        self.pinned = []
        self.decrefs = []
        self.attributes = []
        self.errors = []
        self.modules = []
        self.module_class = object()
        self.ns = dict(
            null=lambda: None,
            ptr_is_null=lambda ptr: ptr is None,
            ptr_eq=lambda left, right: left is right,
            is_tagged_int=lambda ptr: isinstance(ptr, int),
            global_addr=self.globals.__getitem__,
            load_ptr=self.load,
            store_ptr=self.store,
            load_i32=lambda ptr, off: self.load(ptr, off, 4, 0),
            load_i64=lambda ptr, off: self.load(ptr, off, 8, 0),
            ptr_add=self.add,
            stack_alloc=_Block,
            PyMem_Calloc=self.calloc,
            PyMem_Free=self.free,
            PyErr_NoMemory=lambda: self.errors.append("NoMemory"),
            pcc_gc_object_is_known_no_lock=self.is_known,
            pcc_gc_pin=self.pinned.append,
            pcc_runtime_module_class=lambda: self.module_class,
            py_instance_new=self.new_module,
            py_instance_setattr=lambda *args: self.attributes.append(args),
            py_str_new=lambda name, length: name[:length],
            py_decref=self.decrefs.append,
            cstr=lambda text: text,
            strlen=len,
            call_void_ptr2=lambda fn, arg1, arg2: fn(arg1, arg2),
            call_i64_ptr3=lambda fn, arg1, arg2, arg3: fn(arg1, arg2, arg3),
            function_addr=lambda name: self.ns[name],
            PY_TYPE_CEXT_TAG_BASE=1000,
        )
        # Execute the real function bodies, not a copied registry algorithm.
        tree = ast.parse(RUNTIME.read_text(), filename=str(RUNTIME))
        functions = [node for node in tree.body if isinstance(node, ast.FunctionDef)]
        for node in functions:
            node.decorator_list = []
        code = ast.Module(
            body=[ast.ImportFrom(
                module="__future__", names=[ast.alias(name="annotations")], level=0
            ), *functions],
            type_ignores=[],
        )
        exec(compile(ast.fix_missing_locations(code), str(RUNTIME), "exec"), self.ns)

    def add(self, ptr, offset):
        block, base = ptr if isinstance(ptr, tuple) else (ptr, 0)
        return block, base + offset

    def checked(self, ptr, offset, width):
        block, base = ptr if isinstance(ptr, tuple) else (ptr, 0)
        offset += base
        assert block.alive, "access to freed allocation"
        assert 0 <= offset and offset + width <= block.size, (
            f"access [{offset}, {offset + width}) exceeds {block.size}-byte allocation"
        )
        return block, offset

    def load(self, ptr, offset, width=8, default=None):
        block, offset = self.checked(ptr, offset, width)
        return block.fields.get(offset, default)

    def store(self, ptr, offset, value):
        block, offset = self.checked(ptr, offset, 8)
        block.fields[offset] = value

    def calloc(self, count, size):
        self.allocations.append((count, size))
        if len(self.allocations) == self.fail_allocation:
            return None
        return _Block(count * size)

    def free(self, ptr):
        assert ptr.alive and ptr not in self.freed
        ptr.alive = False
        self.freed.append(ptr)

    def is_known(self, module):
        self.known_checks.append(module)
        return int(module in self.known)

    def new_module(self, cls):
        assert cls is self.module_class
        module = object()
        self.modules.append(module)
        self.known.add(module)
        return module

    def definition(self, size=16):
        definition = _Block(96)
        self.store(definition, 32, "bounds_demo")
        self.store(definition, 48, size)
        return definition

    def register(self, module, definition, state):
        return self.ns["pcc_capi_register_module_state"](module, definition, state)

    def find(self, module):
        return self.ns["pcc_capi_find_module_state"](module)


def test_registration_allocates_all_four_pointer_slots_and_preserves_chain():
    memory = _Memory()
    modules = [object() for _ in range(3)]
    definitions = [memory.definition() for _ in modules]
    states = [_Block(16) for _ in modules]
    previous = None
    for module, definition, state in zip(modules, definitions, states):
        assert memory.register(module, definition, state) == 0
        node = memory.load(memory.head, 0)
        assert node.size == 32
        assert node.fields == {0: module, 8: definition, 16: state, 24: previous}
        previous = node
    assert memory.allocations == [(1, 32)] * 3
    for module, definition, state in zip(modules, definitions, states):
        node = memory.find(module)
        assert memory.load(node, 8) is definition
        assert memory.ns["PyModule_GetState"](module) is state
    assert memory.find(object()) is None
    assert memory.ns["PyModule_GetState"](object()) is None
    assert memory.freed == []


@pytest.mark.parametrize("kind", ["null", "tagged", "unknown", "known"])
def test_registration_preserves_module_pin_conditions(kind):
    memory = _Memory()
    module = None if kind == "null" else 1 if kind == "tagged" else object()
    if kind == "known":
        memory.known.add(module)
    assert memory.register(module, None, None) == 0
    assert memory.known_checks == ([module] if kind in ("unknown", "known") else [])
    assert memory.pinned == ([module] if kind == "known" else [])
    assert memory.find(module) is memory.load(memory.head, 0)
    assert memory.ns["PyModule_GetState"](module) is None


@pytest.mark.parametrize("existing", [False, True])
def test_failed_record_allocation_does_not_publish_or_take_ownership(existing):
    memory = _Memory()
    if existing:
        assert memory.register(object(), memory.definition(), _Block(16)) == 0
    head = memory.load(memory.head, 0)
    fields = dict(head.fields) if head is not None else None
    memory.fail_allocation = len(memory.allocations) + 1
    module, definition, state = object(), memory.definition(), _Block(16)
    memory.known.add(module)
    memory.known_checks.clear()
    assert memory.register(module, definition, state) == -1
    assert memory.load(memory.head, 0) is head
    assert (dict(head.fields) if head is not None else None) == fields
    assert memory.known_checks == memory.pinned == memory.decrefs == memory.freed == []
    assert memory.errors == []
    assert state.alive


def test_root_visitor_reads_every_next_and_definition_and_traverse_context():
    memory = _Memory()
    seen = []
    expected = []
    context = object()
    for _ in range(3):
        module, referent = object(), object()
        definition = memory.definition()

        def traverse(actual_module, visit, visit_context, module=module, ref=referent):
            assert actual_module is module
            assert visit(None, visit_context) == 0
            assert visit(ref, visit_context) == 0
            return 0

        memory.store(definition, 72, traverse)
        assert memory.register(module, definition, _Block(16)) == 0
        expected[0:0] = [(module, context), (referent, context)]
    # A record with null fields still has a readable link to the rest of the list.
    assert memory.register(None, None, None) == 0
    memory.ns["pcc_capi_visit_extension_module_state_roots"](
        lambda obj, ctx: seen.append((obj, ctx)), context
    )
    assert seen == expected
    memory.ns["pcc_capi_visit_extension_module_state_roots"](None, context)
    assert seen == expected


def test_type_module_by_definition_reads_the_registered_definition_field():
    memory = _Memory()
    definition, module = memory.definition(), object()
    assert memory.register(module, definition, _Block(16)) == 0
    type_obj, table, count = _Block(400), _Block(8), _Block(8)
    memory.store(type_obj, 392, memory.ns["PY_TYPE_CEXT_TAG_BASE"])
    memory.store(table, 0, module)
    memory.store(count, 0, 1)
    memory.globals.update(pcc_capi_cext_type_modules=table, pcc_capi_cext_type_count=count)
    assert memory.ns["PyType_GetModuleByDef"](type_obj, definition) is module


def test_module_creation_allocates_state_then_complete_record():
    memory = _Memory()
    definition = memory.definition(40)
    module = memory.ns["PyModule_Create2"](definition, 0)
    assert module is memory.modules[0]
    assert memory.allocations == [(1, 40), (1, 32)]
    node = memory.find(module)
    state = memory.ns["PyModule_GetState"](module)
    assert state.size == 40 and state.fields == {}
    assert node.fields == {0: module, 8: definition, 16: state, 24: None}
    assert memory.pinned == [module]
    assert memory.errors == memory.freed == []
    assert memory.attributes == [(module, "__name__", "bounds_demo")]


@pytest.mark.parametrize("failure", [1, 2])
def test_module_creation_allocation_failure_releases_only_owned_state(failure):
    memory = _Memory()
    definition = memory.definition(40)
    memory.fail_allocation = failure
    assert memory.ns["PyModule_Create2"](definition, 0) is None
    assert memory.allocations == [(1, 40), (1, 32)][:failure]
    assert memory.load(memory.head, 0) is None
    assert memory.errors == ["NoMemory"]
    assert memory.decrefs == memory.modules
    assert len(memory.freed) == failure - 1
    if memory.freed:
        assert memory.freed[0].size == 40 and not memory.freed[0].alive
    assert memory.pinned == memory.known_checks == memory.attributes == []


@pytest.mark.parametrize("size", [0, -1])
def test_nonpositive_module_state_keeps_existing_no_record_behavior(size):
    memory = _Memory()
    module = memory.ns["PyModule_Create2"](memory.definition(size), 0)
    assert module is memory.modules[0]
    assert memory.allocations == []
    assert memory.find(module) is None
    assert memory.ns["PyModule_GetState"](module) is None
