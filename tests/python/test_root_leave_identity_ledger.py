"""Pure host bookkeeping tests: execute real methods with a recording IR sink.

No compiler module, runtime, native fixture or generated program is imported.
Owned cleanup and shared returns compare against a linear identity scan.
Shared returns use the actual registration producer: a
hand-built duplicate/custom-iterator registry is not valid production state.
The recording sink proves host emission order, not native timing or execution.
"""

import ast
import builtins
import copy
from pathlib import Path
from types import SimpleNamespace
import weakref

import pytest


ROOT = Path(__file__).resolve().parents[2]
CODEGEN = ROOT / "pcc/frontends/python/codegen"
METHODS = {
    "owned": ("ownership_lowering.py", "_emit_owned_local_cleanup", "emitted_slot_leaves", "entry_alloca"),
    "shared": ("return_lowering.py", "_shared_return_exit", "emitted", "base"),
    "register": ("ownership_lowering.py", "_ensure_local_gc_frame_root", "", ""),
    "patch": ("ownership_lowering.py", "_insert_gc_frame_leave_before_terminator", "", ""),
}


def load_method(kind, *, reference=False, counts=None):
    filename, name, ledger, slot = METHODS[kind]
    tree = ast.parse((CODEGEN / filename).read_text())
    method = copy.deepcopy(next(node for node in ast.walk(tree)
                                if isinstance(node, ast.FunctionDef) and node.name == name))
    if reference:
        initializers = 0
        loops = 0
        for node in ast.walk(method):
            if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.target.id == ledger:
                assert isinstance(node.value, ast.Dict)
                node.annotation = ast.Name(id="list", ctx=ast.Load())
                node.value = ast.List(elts=[], ctx=ast.Load())
                initializers += 1
            if not isinstance(node, ast.For):
                continue
            for offset, item in enumerate(node.body):
                if (isinstance(item, ast.Assign) and len(item.targets) == 1
                        and isinstance(item.targets[0], ast.Name) and item.targets[0].id == "slot_id"):
                    assert ast.unparse(node.body[offset + 1].test) == "slot_id in " + ledger
                    assert ast.unparse(node.body[offset + 2].targets[0]) == ledger + "[slot_id]"
                    replacement = ast.parse(
                        "already = False\n"
                        "for done in " + ledger + ":\n"
                        "    if done is " + slot + ":\n"
                        "        already = True\n"
                        "if already:\n"
                        "    continue\n"
                        + ledger + ".append(" + slot + ")\n"
                    ).body
                    node.body[offset:offset + 3] = replacement
                    loops += 1
                    break
        assert (initializers, loops) == (1, 1)
    if counts is not None:
        class CountIdentity(ast.NodeTransformer):
            def visit_Compare(self, node):
                if (isinstance(node.left, ast.Name) and node.left.id == "done"
                        and len(node.ops) == 1 and isinstance(node.ops[0], ast.Is)
                        and isinstance(node.comparators[0], ast.Name)
                        and node.comparators[0].id == slot):
                    return ast.copy_location(ast.Call(func=ast.Name(id="compare_identity", ctx=ast.Load()),
                                                      args=[node.left, node.comparators[0]], keywords=[]), node)
                return self.generic_visit(node)
        method = CountIdentity().visit(method)

    def import_stub(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "__future__":
            return builtins.__import__(name, globals, locals, fromlist, level)
        assert name == "pcc.frontends.python.codegen.frame_root_groups"
        return SimpleNamespace(frame_root_group=lambda host, value: host.group(value))

    def stable_id(value):
        if counts is not None:
            counts["ids"] += 1
        return id(value)

    def compare_identity(left, right):
        counts["comparisons"] += 1
        return left is right

    namespace = {
        "__builtins__": dict(vars(builtins), __import__=import_stub),
        "ir": SimpleNamespace(VoidType=VoidType, PointerType=PointerType, Constant=lambda ty, value: ("constant", value)),
        "_CSTR": PointerType(),
        "id": stable_id,
        "compare_identity": compare_identity,
    }
    module = ast.Module(body=[ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0), method], type_ignores=[])
    exec(compile(ast.fix_missing_locations(module), str(CODEGEN / filename), "exec"), namespace)
    return namespace[name]


class VoidType:
    pass


class PointerType:
    pass


class Slot:
    def __init__(self, label):
        self.label = label

    def __eq__(self, other):
        raise AssertionError("IR slots must not be compared by equality")

    def __hash__(self):
        raise AssertionError("IR slots must not be hashed")


def label(value):
    return "None" if value is None else value.label


class Builder:
    _END = -1

    def __init__(self, events):
        self.events = events
        self._block = SimpleNamespace(label="original", _instrs=[])
        self._pos = self._END

    def position_at_end(self, block):
        self.events.append(("position", block.label))
        self._block = block
        self._pos = self._END

    def emit_record(self, kind, value):
        record = SimpleNamespace(kind=kind, value=value, block=self._block)
        if self._pos == self._END:
            self._block._instrs.append(record)
        else:
            self._block._instrs.insert(self._pos, record)
            self._pos += 1

    def position_before(self, instruction):
        self._block = instruction.block
        self._pos = next(index for index, item in enumerate(self._block._instrs) if item is instruction)

    def branch(self, block):
        self.events.append(("branch", block.label))
        self.emit_record("branch", block.label)

    def ret_void(self):
        self.events.append(("ret_void",))

    def ret(self, value):
        self.events.append(("ret", value))

    def load(self, slot, name=""):
        self.events.append(("load", label(slot), name))
        return "loaded"

    def call(self, callee, arguments, name=""):
        self.events.append(("call", callee, arguments, name))
        return "called"


class Host:
    def __init__(self, registry, *, groups=None, payload=(), module=False, return_type=None):
        self.events = []
        self.builder = Builder(self.events)
        self.current_function = SimpleNamespace(name="f", function_type=SimpleNamespace(return_type=return_type or VoidType()))
        self.current_function.append_basic_block = lambda name: SimpleNamespace(label=name, _instrs=[])
        self.current_func_def = None if module else object()
        self._fn_gc_root_slot_registry = {"f": registry}
        self._fn_gc_root_exit_sites = {}
        self._shared_return_exits = {}
        self._gc_rooted_local_names = set()
        self._gc_rooted_local_order = []
        self.frame_map = object()
        self.patches = []
        self._return_handoff_sites = []
        self.env = {}
        self.groups = {} if groups is None else groups
        self.payload = payload
        self.on_leave = None
        self.on_group = None
        self.runtime = {"pcc_gc_load_ptr": "load_ptr", "pcc_gc_store_root": "store_root"}

    def _ir_type_matches(self, left, right):
        return isinstance(left, PointerType) and isinstance(right, PointerType)

    def _gc_one_slot_frame_map(self):
        return self.frame_map

    def _emit_entry_gc_frame_enter(self, frame_map, slot):
        assert frame_map is self.frame_map
        self.events.append(("enter", label(slot)))

    def _patch_fn_err_exit_gc_root_leave(self, name, slot):
        self.events.append(("patch_error", name, label(slot)))

    def _insert_gc_frame_leave_before_terminator(self, block, slot):
        self.patches.append((block, slot))
        self.events.append(("patch_leave", block.label, label(slot)))
        assert load_method("patch")(self, block, slot)

    def _instruction_is_terminator(self, instruction):
        return instruction.kind == "branch"

    def register(self, name, slot):
        load_method("register")(self, name, slot, PointerType())

    def _emit_owned_valueclass_cleanup(self, payload):
        self.events.append(("valueclass_cleanup", payload is not None))

    def _valueclass_payload_owned_roots(self, payload):
        self.events.append(("payload_roots",))
        return self.payload

    def group(self, value):
        self.events.append(("group", label(value)))
        if self.on_group is not None:
            self.on_group(value)
        return self.groups.get(id(value))

    def _emit_gc_frame_leave_for_slot(self, slot):
        self.events.append(("leave", label(slot)))
        self.builder.emit_record("leave", label(slot))
        if self.on_leave is not None:
            self.on_leave(slot)

    def _fresh(self, name):
        return name

    def _alloca_in_entry(self, ty, name, init_null):
        self.events.append(("alloca", name, init_null))
        return Slot(name)

    def _as_gc_ptr(self, slot, name=""):
        self.events.append(("as_ptr", label(slot), name))
        return "slot_ptr"

    def _emit_gc_frame_leave_lifo_for_slot(self, slot):
        self.events.append(("leave_lifo", label(slot)))


def registered_host(registry, **kwargs):
    host = Host([], **kwargs)
    for name, slot in registry:
        host.register(name, slot)
    host.events.clear()
    return host


def invoke(kind, host, *, reference=False, counts=None, **kwargs):
    method = load_method(kind, reference=reference, counts=counts)
    if kind == "shared":
        return method(host, kwargs.get("rooted", False))
    return method(host, **kwargs)


def outcome(kind, make_host, **kwargs):
    results = []
    for reference in (True, False):
        host = make_host()
        try:
            invoke(kind, host, reference=reference, **kwargs)
            error = None
        except Exception as exc:
            error = type(exc), str(exc)
        results.append((host.events, error))
    assert results[0] == results[1]
    return results[1]


@pytest.mark.parametrize("kind", ["owned", "shared"])
@pytest.mark.parametrize("size", [0, 1, 2, 16, 128])
def test_identity_order_and_host_operation_counts(kind, size):
    slots = [Slot(str(index)) for index in range(size)]
    registry = [("same name", value) for value in slots]
    make_host = lambda: registered_host(registry) if kind == "shared" else Host(registry)
    events, error = outcome(kind, make_host)
    assert error is None
    assert [event[1] for event in events if event[0] == "leave"] == [value.label for value in reversed(slots)]
    old = {"ids": 0, "comparisons": 0}
    new = {"ids": 0, "comparisons": 0}
    invoke(kind, make_host(), reference=True, counts=old)
    invoke(kind, make_host(), counts=new)
    assert old == {"ids": 0, "comparisons": size * (size - 1) // 2}
    assert new == {"ids": size, "comparisons": 0}


@pytest.mark.parametrize("kind", ["owned", "shared"])
def test_duplicate_objects_not_duplicate_names_or_equality(kind):
    first, second = Slot("same"), Slot("same")
    if kind == "shared":
        # The sole producer rejects repeated physical storage, not names.
        host = Host([])
        for name, slot in [("x", first), ("x", second), ("renamed", first)]:
            host.register(name, slot)
        assert len(host._fn_gc_root_slot_registry["f"]) == 2
        assert host._fn_gc_root_slot_registry["f"][0][1] is first
        assert host._fn_gc_root_slot_registry["f"][1][1] is second
        assert [row for row in host.events if row[0] == "enter"] == [("enter", "same"), ("enter", "same")]
        assert host._gc_rooted_local_names == {"x", "renamed"}
        events, error = outcome(kind, lambda: registered_host([("x", first), ("x", second), ("alias", first)]))
        assert error is None
        assert [row for row in events if row[0] == "leave"] == [("leave", "same"), ("leave", "same")]
        return
    registry = [("x", first), ("x", second), ("renamed", first), ("none", None), ("none again", None)]
    events, error = outcome(kind, lambda: Host(registry))
    assert error is None
    assert [event for event in events if event[0] == "leave"] == [("leave", "None"), ("leave", "same"), ("leave", "same")]


def test_payload_exclusion_precedes_generator_group_normalization():
    first, second, base = Slot("first"), Slot("second"), Slot("base")
    groups = {id(first): {"base": base}, id(second): {"base": base}}
    events, error = outcome("owned", lambda: Host([("a", first), ("b", second), ("c", base)], groups=groups, payload=(second,)), skip_payload=object())
    assert error is None
    assert [event for event in events if event[0] in ("group", "leave")] == [("group", "base"), ("leave", "base"), ("group", "first")]


@pytest.mark.parametrize("failure", ["group", "leave"])
def test_error_order_and_prefix_are_unchanged(failure):
    first, second, last = Slot("first"), Slot("second"), Slot("last")
    def make_host():
        host = Host([("a", first), ("b", second), ("c", last)])
        def fail(value):
            if value is second:
                raise ValueError("intentional " + failure)
        if failure == "group":
            host.on_group = fail
        else:
            host.on_leave = fail
        return host
    _events, error = outcome("owned", make_host)
    assert error == (ValueError, "intentional " + failure)


@pytest.mark.parametrize("kind", ["owned", "shared"])
@pytest.mark.parametrize("mutation", ["append", "delete", "replace"])
def test_registry_changes_obey_each_path_producer_contract(kind, mutation):
    if kind == "shared":
        # Production emission does not mutate a registry through arbitrary
        # callbacks. Test its actual append/re-registration/partition routes
        # instead of deleting entries under a synthetic reverse iterator.
        first, second = Slot("first"), Slot("second")
        host = registered_host([("same", first)])
        method = load_method("shared")
        unrooted, _ = method(host, False)
        rooted, _ = method(host, True)
        original = host.builder._block
        if mutation == "append":
            host.register("same", second)
            assert len(host.patches) == 2
            assert host.patches[0][0] is unrooted and host.patches[0][1] is second
            assert host.patches[1][0] is rooted and host.patches[1][1] is second
            assert host.builder._block is original
            for block in (unrooted, rooted):
                assert [(item.kind, item.value) for item in block._instrs] == [
                    ("leave", "first"), ("leave", "second"), ("branch", "ret.shared.tail"),
                ]
            patches = list(host.patches)
            host.register("alias", second)
            assert host.patches == patches
        elif mutation == "delete":
            # Deletion is not a registry producer operation. Its actual
            # alias case must neither append nor enter/patch a second time.
            before = list(host.events)
            host.register("renamed", first)
            assert host.events == before and host.patches == []
        else:
            previous = host.current_function
            host.current_function = SimpleNamespace(name="other")
            host.register("same", first)
            assert len(host._fn_gc_root_slot_registry["other"]) == 1
            assert len(host._fn_gc_root_slot_registry["f"]) == 1
            assert host.patches == []
            host.current_function = previous
        assert method(host, False)[0] is unrooted
        assert method(host, True)[0] is rooted
        return
    first, second, last, extra = [Slot(name) for name in ("first", "second", "last", "extra")]
    def make_host():
        registry = [("a", first), ("b", second), ("c", last)]
        host = Host(registry)
        def mutate(value):
            if value is last:
                if mutation == "append":
                    registry.append(("d", extra))
                elif mutation == "delete":
                    del registry[0]
                else:
                    registry[0] = ("replacement", extra)
        host.on_leave = mutate
        return host
    outcome(kind, make_host)


def test_generator_base_changes_are_read_each_iteration():
    first, second, original, replacement = [Slot(name) for name in ("first", "second", "original", "replacement")]
    def make_host():
        group = {"base": original}
        host = Host([("a", first), ("b", second)], groups={id(first): group, id(second): group})
        host.on_leave = lambda value: group.update(base=replacement)
        return host
    events, error = outcome("owned", make_host)
    assert error is None
    assert [event for event in events if event[0] == "leave"] == [("leave", "original"), ("leave", "replacement")]


def test_owned_shared_exit_and_module_bypasses_are_unchanged():
    slot = Slot("slot")
    registry = [("a", slot), ("again", slot)]
    events, error = outcome("owned", lambda: Host(registry), shared_exit_leaves=True)
    assert error is None
    assert events == [("valueclass_cleanup", False)]
    events, error = outcome("owned", lambda: Host(registry, module=True))
    assert error is None
    assert events == [("valueclass_cleanup", False), ("leave", "slot"), ("leave", "slot")]


@pytest.mark.parametrize("rooted", [False, True])
def test_shared_exit_preserves_value_and_cleanup_calls(rooted):
    slot = Slot("local")
    events, error = outcome("shared", lambda: registered_host([("local", slot)], return_type=PointerType()), rooted=rooted)
    assert error is None
    assert ("leave", "local") in events
    assert ("leave_lifo", "ret.shared") in events if rooted else ("load", "ret.shared", "ret.shared.value") in events


def test_shared_exit_cache_and_invocation_local_owned_ledger():
    slot = Slot("slot")
    host = registered_host([("a", slot), ("b", slot)])
    method = load_method("shared")
    first = method(host, False)
    events = list(host.events)
    assert method(host, False) is first
    assert host.events == events
    # Each exit restores a mid-block insertion point, not only block identity.
    saved_block = host.builder._block
    host.builder._pos = 0
    method(host, True)
    assert host.builder._block is saved_block and host.builder._pos == 0
    assert sum(event[0] == "leave" for event in host.events) == 2
    owned = Host([("a", slot), ("b", slot)])
    method = load_method("owned")
    method(owned)
    method(owned)
    assert sum(event[0] == "leave" for event in owned.events) == 2
    assert len(owned._fn_gc_root_exit_sites["f"]) == 1
    # A new IR function with the same text name must not reuse old exit state.
    fresh = Host([]).current_function
    host.current_function = fresh
    new_exit = load_method("shared")(host, False)
    assert new_exit is not first
    assert host._shared_return_exits["f"][0] is fresh
    # Execute only the actual three reset statements, not module generation.
    # They must occur together in one block of _generate_impl.
    tree = ast.parse((CODEGEN / "generation_lowering.py").read_text())
    generate = next(node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and node.name == "_generate_impl")
    fields = {"_fn_gc_root_slot_registry", "_fn_gc_root_exit_sites", "_shared_return_exits"}
    reset_groups = []
    for parent in ast.walk(generate):
        for _field, value in ast.iter_fields(parent):
            if not isinstance(value, list):
                continue
            resets = [node for node in value if isinstance(node, ast.Expr)
                      and isinstance(node.value, ast.Call)
                      and isinstance(node.value.func, ast.Name) and node.value.func.id == "setattr"
                      and len(node.value.args) == 3 and isinstance(node.value.args[1], ast.Constant)
                      and node.value.args[1].value in fields]
            if resets:
                reset_groups.append(resets)
    assert len(reset_groups) == 1
    resets = reset_groups[0]
    assert {node.value.args[1].value for node in resets} == fields
    assert all(ast.unparse(node.value.args[0]) == "self" and ast.unparse(node.value.args[2]) == "{}" for node in resets)
    exec(compile(ast.Module(body=resets, type_ignores=[]), str(CODEGEN / "generation_lowering.py"), "exec"), {"self": host})
    new_slot = Slot("new module")
    host.register("a", new_slot)
    host.events.clear()
    load_method("shared")(host, False)
    assert [event for event in host.events if event[0] == "leave"] == [("leave", "new module")]


@pytest.mark.parametrize("kind", ["owned", "shared"])
def test_ledger_or_registry_retains_identity_owners(kind):
    if kind == "shared":
        host = Host([])
        refs = []
        for index in range(4):
            slot = Slot(str(index))
            refs.append(weakref.ref(slot))
            host.register("same", slot)
        del slot
        host.events.clear()
        host.on_leave = lambda value: all(ref() is not None for ref in refs) or pytest.fail("registry lost its owner")
        invoke(kind, host)
        assert all(ref() is not None for ref in refs)
        host._fn_gc_root_slot_registry = {}
        assert all(ref() is None for ref in refs)
        return
    refs = []
    class EphemeralRegistry:
        def __reversed__(self):
            for index in range(4):
                value = Slot(str(index))
                refs.append(weakref.ref(value))
                yield "temporary", value
    host = Host(EphemeralRegistry())
    host.on_leave = lambda value: all(ref() is not None for ref in refs) or pytest.fail("prior identity owner was released")
    invoke(kind, host)
    assert all(ref() is None for ref in refs)


def test_reentrant_owned_cleanup_has_a_separate_ledger():
    slot = Slot("slot")
    host = Host([("a", slot), ("b", slot)])
    method = load_method("owned")
    reentered = False
    def reenter(value):
        nonlocal reentered
        if not reentered:
            reentered = True
            method(host)
    host.on_leave = reenter
    method(host)
    assert sum(event[0] == "leave" for event in host.events) == 2
