"""Host-only CFG contracts for the real shared-return cleanup methods.

Only method ASTs are compiled.  No pcc module, native runtime, compiler
entrypoint, generated program, or diagnostic script is imported/executed.
The inline oracle uses the real cleanup's unchanged target=None path.
"""

import __future__
import ast
import builtins
import copy
import inspect
from pathlib import Path
from types import MethodType, SimpleNamespace
import weakref

import pytest


ROOT = Path(__file__).resolve().parents[2]
CODEGEN = ROOT / "pcc/frontends/python/codegen"
METHOD_NAMES = (
    "_emit_owned_local_cleanup",
    "_insert_gc_frame_leave_before_terminator",
    "_shared_return_slot_is_rooted",
    "_shared_return_exit_allowed",
    "_shared_return_exit",
    "_shared_return_pinned_cleanup",
    "_return_through_shared_exit",
)


class PointerType:
    def __init__(self, compatible=True):
        self.compatible = compatible


class VoidType:
    pass


class RawPointerType:
    pass


class Constant:
    def __init__(self, ty, value):
        self.type = ty
        self.value = value


class Slot:
    def __init__(self, label):
        self.label = label

    def __eq__(self, other):
        raise AssertionError("slot equality is not an identity test")

    def __hash__(self):
        raise AssertionError("slot objects must not be hash keys")


class Result:
    pass


class Instruction:
    def __init__(self, opname, args=(), result=None):
        self.opname = opname
        self.args = tuple(args)
        self.result = result


class Block:
    def __init__(self, parent, name):
        self.parent = parent
        self.name = name
        self._instrs = []

    @property
    def is_terminated(self):
        return bool(self._instrs and self._instrs[-1].opname in ("branch", "ret", "ret_void"))


class Function:
    def __init__(self, name="f", return_type=None):
        self.name = name
        self.blocks = []
        self.function_type = SimpleNamespace(return_type=return_type or VoidType())

    def append_basic_block(self, name):
        block = Block(self, name)
        self.blocks.append(block)
        return block


class Builder:
    _END = -1

    def __init__(self, block):
        self._block = block
        self._pos = self._END
        self.fail_callee = None

    @property
    def block(self):
        return self._block

    def emit(self, opname, *args, result=None):
        instruction = Instruction(opname, args, result)
        if self._pos == self._END:
            self._block._instrs.append(instruction)
        else:
            self._block._instrs.insert(self._pos, instruction)
            self._pos += 1
        return instruction

    def position_at_end(self, block):
        self._block = block
        self._pos = self._END

    def position_before(self, instruction):
        for block in self._block.parent.blocks:
            for index, item in enumerate(block._instrs):
                if item is instruction:
                    self._block = block
                    self._pos = index
                    return
        raise AssertionError("instruction not in this function")

    def load(self, slot, name=""):
        result = Result()
        self.emit("load", slot, result=result)
        return result

    def store(self, value, slot):
        self.emit("store", slot, value)

    def call(self, callee, arguments, name=""):
        if callee == self.fail_callee:
            raise ValueError("intentional builder failure")
        result = Result()
        self.emit("call", callee, tuple(arguments), result=result)
        return result

    def branch(self, block):
        self.emit("branch", block)

    def ret_void(self):
        self.emit("ret_void")

    def ret(self, value):
        self.emit("ret", value)


def methods():
    selected = {}
    for filename in ("ownership_lowering.py", "return_lowering.py"):
        tree = ast.parse((CODEGEN / filename).read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name in METHOD_NAMES:
                assert node.name not in selected
                selected[node.name] = copy.deepcopy(node)
    assert set(selected) == set(METHOD_NAMES)

    def import_stub(name, globals=None, locals=None, fromlist=(), level=0):
        assert name == "pcc.frontends.python.codegen.frame_root_groups"
        return SimpleNamespace(frame_root_group=lambda host, slot: None)

    namespace = {
        "__builtins__": dict(vars(builtins), __import__=import_stub),
        "ir": SimpleNamespace(PointerType=PointerType, VoidType=VoidType,
                              Constant=Constant, IRBuilder=Builder),
        "_CSTR": PointerType(),
        "RawPointerType": RawPointerType,
    }
    module = ast.Module(body=list(selected.values()), type_ignores=[])
    # A compiler flag avoids executing a synthetic __future__ import through
    # the deliberately narrow generator-module import stub.
    code = compile(ast.fix_missing_locations(module), "<pinned-cleanup-methods>", "exec",
                   flags=__future__.annotations.compiler_flag, dont_inherit=True)
    exec(code, namespace)
    return {name: namespace[name] for name in METHOD_NAMES}


class Host:
    def __init__(self, *, inline=False, pointer_return=False):
        self.current_function = Function(return_type=PointerType() if pointer_return else VoidType())
        self.current_func_def = SimpleNamespace(return_ty=object())
        self.ast_module = SimpleNamespace(name="test.module")
        self.builder = Builder(self.current_function.append_basic_block("entry"))
        self.runtime = {name: name for name in ("pcc_gc_unpin", "pcc_gc_store_root", "pcc_gc_load_ptr")}
        self._shared_return_exits = {}
        self._fn_gc_root_exit_sites = {}
        self._fn_gc_root_slot_registry = {"f": []}
        self._return_handoff_sites = []
        self._gc_rooted_local_names = set()
        self._gc_rooted_local_order = []
        self._pinned_gc_rooted_local_names = set()
        self._owned_local_names = set()
        self._owned_local_has_value = set()
        self._current_global_names = set()
        self._current_param_names = set()
        self._exact_int_env_flags = {}
        self._for_target_owned_names = set()
        self._freestanding_module = False
        self._python_library = False
        self._runtime_port_module = False
        self._generator_ctx_stack = []
        self._async_body_depth = 0
        self._suppress_implicit_gc_roots = False
        self._cpy_values = ()
        self.env = {}
        self.serial = 0
        real = methods()
        for name, method in real.items():
            setattr(self, name, MethodType(method, self))
        if inline:
            cleanup = self._emit_owned_local_cleanup

            def inline_cleanup(*args, **kwargs):
                target = kwargs.pop("shared_pinned_target", None)
                result = cleanup(*args, **kwargs)
                return target if target is not None else result

            self._emit_owned_local_cleanup = inline_cleanup

    def register(self, name, slot=None, *, pinned=True, owned=False, ir_type=None):
        slot = Slot(name) if slot is None else slot
        self.env[name] = (slot, PointerType() if ir_type is None else ir_type, object())
        self._gc_rooted_local_names.add(name)
        self._gc_rooted_local_order.append(name)
        self._fn_gc_root_slot_registry[self.current_function.name].append((name, slot))
        if pinned:
            self._pinned_gc_rooted_local_names.add(name)
        if owned:
            self._owned_local_names.add(name)
            self._owned_local_has_value.add(name)
        return slot

    def _fresh(self, prefix):
        self.serial += 1
        return prefix + "." + str(self.serial)

    def _alloca_in_entry(self, ty, name, init_null=False):
        return Slot("return-slot" if name.startswith("ret.shared") else name)

    def _as_gc_ptr(self, slot, name=""):
        return slot

    def _value_available_at_insertion_point(self, value):
        return value

    def _ir_type_matches(self, actual, expected):
        return actual.compatible

    def _emit_owned_valueclass_cleanup(self, skip_payload):
        self.builder.emit("valueclass", skip_payload is not None)

    def _emit_release_owned_local_if_flagged(self, name, slot):
        self.builder.emit("owned", name, slot)

    def _valueclass_payload_owned_roots(self, payload):
        return ()

    def _emit_gc_frame_leave_for_slot(self, slot):
        self.builder.call("frame_leave", [slot])

    def _emit_gc_frame_leave_lifo_for_slot(self, slot):
        self.builder.call("frame_leave_lifo", [slot])

    def _emit_current_gc_frame_enter_lifo(self, frame_map, slot):
        self.builder.call("frame_enter_lifo", [frame_map, slot])

    def _gc_one_slot_frame_map(self):
        return "one-slot-map"

    def _instruction_is_terminator(self, instruction):
        return instruction.opname in ("branch", "ret", "ret_void")

    def new_return(self, *, rooted=False, skip_name=None, value=None):
        entry = self.current_function.append_basic_block(self._fresh("return"))
        self.builder.position_at_end(entry)
        assert self._return_through_shared_exit(value, skip_name=skip_name, rooted=rooted)
        return entry


def trace(entry, initial=None):
    """Interpret only recording-sink operations, never emitted native code."""
    memory = {} if initial is None else dict(initial)
    values = {}
    events = []

    def evaluate(value):
        if isinstance(value, Result):
            return values[id(value)]
        if isinstance(value, Constant):
            return value.value
        return value

    def printable(value):
        return ("slot", value.label) if isinstance(value, Slot) else value

    block = entry
    visited = set()
    while block is not None:
        assert id(block) not in visited, "cleanup CFG unexpectedly loops"
        visited.add(id(block))
        next_block = None
        for instruction in block._instrs:
            op, args = instruction.opname, instruction.args
            if op == "load":
                values[id(instruction.result)] = memory.get(id(args[0]), args[0].label + ":live")
            elif op == "store":
                memory[id(args[0])] = evaluate(args[1])
            elif op == "call":
                callee, operands = args
                actual = tuple(evaluate(value) for value in operands)
                events.append((callee, tuple(printable(value) for value in actual)))
                if callee == "pcc_gc_store_root":
                    memory[id(actual[0])] = actual[1]
                    result = None
                elif callee == "pcc_gc_load_ptr":
                    result = memory.get(id(actual[1]), actual[1].label + ":live")
                else:
                    result = None
                values[id(instruction.result)] = result
            elif op == "branch":
                next_block = args[0]
                break
            elif op == "ret":
                events.append(("return", evaluate(args[0])))
                return events
            elif op == "ret_void":
                events.append(("return", None))
                return events
            elif op == "owned":
                events.append(("owned", args[0], args[1].label))
            elif op == "valueclass":
                events.append(("valueclass", args[0]))
            elif op == "marker":
                events.append(("marker", args[0]))
            else:
                raise AssertionError(op)
        block = next_block
    return events


def static_calls(host, callee):
    return sum(instruction.opname == "call" and instruction.args[0] == callee
               for block in host.current_function.blocks for instruction in block._instrs)


@pytest.mark.parametrize("rooted", [False, True])
@pytest.mark.parametrize("count", [0, 1, 8, 32])
def test_return_paths_match_inline_and_share_suffix_calls(rooted, count):
    runs = []
    for inline in (True, False):
        host = Host(inline=inline, pointer_return=True)
        for index in range(count):
            host.register("root" + str(index))
        entries = [host.new_return(rooted=rooted, value="result") for _ in range(5)]
        runs.append([trace(entry) for entry in entries])
        assert static_calls(host, "pcc_gc_unpin") == count * (5 if inline else 1)
        cleanup_blocks = [block for block in host.current_function.blocks
                          if block.name.startswith("ret.shared.unpin.")]
        assert len(cleanup_blocks) == (0 if inline else count)
        assert len(host._fn_gc_root_exit_sites["f"]) == 1
        if not inline:
            assert len(host._shared_return_exits["f"][3]) == count
    assert runs[0] == runs[1]


def test_aliases_filters_skip_and_late_eligibility_match_inline():
    traces = []
    for inline in (True, False):
        host = Host(inline=inline)
        alias = host.register("first")
        host.register("alias", alias)
        host._gc_rooted_local_order.append("alias")  # name dedup, not slot dedup
        host.register("owned", owned=True)
        host.register("skip", owned=True)
        host.register("global")
        host._current_global_names.add("global")
        host.register("not-pinned", pinned=False)
        host.register("non-pointer", ir_type=object())
        host.register("wrong-pointer", ir_type=PointerType(False))
        host._gc_rooted_local_names.add("missing-env")
        first = host.new_return(skip_name="skip")
        host._pinned_gc_rooted_local_names.discard("alias")
        host.env.pop("first")
        host.register("new")
        second = host.new_return(skip_name="skip")
        pair = trace(first), trace(second)
        traces.append(pair)
        assert [event for event in pair[0] if event[0] == "pcc_gc_unpin"] == [
            ("pcc_gc_unpin", ("first:live",)), ("pcc_gc_unpin", (None,)),
        ]
        assert ("owned", "owned", "owned") in pair[0]
        assert not any(event[0] == "owned" and event[1] == "skip" for event in pair[0])
        assert [event for event in pair[1] if event[0] == "pcc_gc_unpin"] == [
            ("pcc_gc_unpin", ("new:live",)),
        ]
    assert traces[0] == traces[1]


def test_late_root_snapshots_and_real_retro_patch_keep_old_cleanup_unchanged():
    traces = []
    for inline in (True, False):
        host = Host(inline=inline)
        host.register("old")
        first = host.new_return()
        late = host.register("late")
        sites = list(host._fn_gc_root_exit_sites["f"])
        for site in sites:
            assert host._insert_gc_frame_leave_before_terminator(site, late)
        second = host.new_return()
        assert host._fn_gc_root_exit_sites["f"] == sites
        paths = trace(first), trace(second)
        traces.append(paths)
        assert [event[1] for event in paths[0] if event[0] == "pcc_gc_unpin"] == [("old:live",)]
        assert [event[1] for event in paths[1] if event[0] == "pcc_gc_unpin"] == [("late:live",), ("old:live",)]
        for path in paths:
            last_clear = max(index for index, event in enumerate(path) if event[0] == "pcc_gc_store_root")
            first_leave = min(index for index, event in enumerate(path) if event[0] == "frame_leave")
            assert last_clear < first_leave
            assert ("frame_leave", (("slot", "late"),)) in path
    assert traces[0] == traces[1]


def test_same_suffix_distinct_targets_and_runtime_reloads():
    host = Host(pointer_return=True)
    slot = host.register("root")
    plain = host.new_return(value="plain")
    rooted = host.new_return(rooted=True, value="rooted")
    assert static_calls(host, "pcc_gc_unpin") == 2
    assert len(host._shared_return_exits["f"][3]) == 2
    assert ("pcc_gc_unpin", ("first-run",)) in trace(plain, {id(slot): "first-run"})
    assert ("pcc_gc_unpin", ("second-run",)) in trace(plain, {id(slot): "second-run"})
    assert trace(plain)[-1] == ("return", "plain")
    assert trace(rooted)[-1] == ("return", "rooted")


@pytest.mark.parametrize("position", [Builder._END, 0, 1])
def test_builder_insertion_position_restored_on_success_and_cache_hit(position):
    host = Host()
    slot = host.register("root")
    target, _ = host._shared_return_exit(False)
    saved = host.builder._block
    host.builder.emit("marker", "before")
    host.builder.emit("marker", "after")
    original = list(saved._instrs)
    host.builder._pos = position
    head = host._shared_return_pinned_cleanup([slot], target)
    assert host.builder._block is saved and host.builder._pos == position
    assert saved._instrs == original
    assert host._shared_return_pinned_cleanup([slot], target) is head
    assert host.builder._block is saved and host.builder._pos == position


@pytest.mark.parametrize("position", [Builder._END, 1])
def test_partial_node_is_not_published_and_failure_restores_builder(position):
    host = Host()
    slot = host.register("root")
    target, _ = host._shared_return_exit(False)
    saved = host.builder._block
    host.builder.emit("marker", "existing")
    host.builder._pos = position
    host.builder.fail_callee = "pcc_gc_store_root"
    with pytest.raises(ValueError, match="intentional builder failure"):
        host._shared_return_pinned_cleanup([slot], target)
    assert host._shared_return_exits["f"][3] == {}
    assert host.builder._block is saved and host.builder._pos == position
    host.builder.fail_callee = None
    head = host._shared_return_pinned_cleanup([slot], target)
    assert len(host._shared_return_exits["f"][3]) == 1
    assert [event[0] for event in trace(head)] == ["pcc_gc_unpin", "pcc_gc_store_root", "frame_leave", "return"]


def test_function_name_reuse_does_not_reuse_previous_function_nodes():
    host = Host()
    host.register("first-root")
    old_entry = host.new_return()
    old_function = host.current_function
    old_cache = host._shared_return_exits["f"][3]
    host.current_function = Function("f")
    host.builder = Builder(host.current_function.append_basic_block("entry"))
    host.env = {}
    host._gc_rooted_local_names = set()
    host._gc_rooted_local_order = []
    host._pinned_gc_rooted_local_names = set()
    host._fn_gc_root_slot_registry["f"] = []
    host._fn_gc_root_exit_sites["f"] = []
    host.register("second-root")
    new_entry = host.new_return()
    assert host._shared_return_exits["f"][0] is host.current_function
    assert host._shared_return_exits["f"][3] is not old_cache
    assert all(record[2].parent is old_function for record in old_cache.values())
    assert ("pcc_gc_unpin", ("first-root:live",)) in trace(old_entry)
    assert ("pcc_gc_unpin", ("second-root:live",)) in trace(new_entry)


def test_cache_records_retain_both_identity_key_owners():
    host = Host()
    target, _ = host._shared_return_exit(False)
    slot = Slot("ephemeral")
    slot_ref, target_ref = weakref.ref(slot), weakref.ref(target)
    head = host._shared_return_pinned_cleanup([slot], target)
    cache = host._shared_return_exits["f"][3]
    record = cache[(id(slot), id(target))]
    assert record[0] is slot and record[1] is target and record[2] is head
    del slot, target
    assert slot_ref() is not None and target_ref() is not None


@pytest.mark.parametrize("disabled", ["generator", "async", "library", "runtime", "freestanding", "module", "cpy"])
def test_nonshared_return_gates_do_not_create_cleanup_nodes(disabled):
    host = Host(pointer_return=True)
    host.register("root")
    value = "value"
    if disabled == "generator":
        host._generator_ctx_stack = [object()]
    elif disabled == "async":
        host._async_body_depth = 1
    elif disabled == "library":
        host._python_library = True
    elif disabled == "runtime":
        host._runtime_port_module = True
    elif disabled == "freestanding":
        host._freestanding_module = True
    elif disabled == "module":
        host.current_func_def = None
    else:
        host._cpy_values = (value,)
    assert host._return_through_shared_exit(value) is False
    assert host._shared_return_exits == {}
    assert host.builder._block._instrs == []


@pytest.mark.parametrize("shared_flag", [False, True])
def test_none_target_retains_the_original_inline_cleanup(shared_flag):
    host = Host()
    host.register("root")
    entry = host.builder._block
    assert host._emit_owned_local_cleanup(shared_exit_leaves=shared_flag) is None
    assert static_calls(host, "pcc_gc_unpin") == 1
    assert host._shared_return_exits == {}
    assert ("pcc_gc_unpin", ("root:live",)) in trace(entry)


def test_finally_handoff_path_remains_nonshared_in_source():
    tree = ast.parse((CODEGEN / "return_lowering.py").read_text())
    method = next(node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)
                  and node.name == "_emit_owned_return_through_finally")
    cleanups = [node for node in ast.walk(method) if isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute) and node.func.attr == "_emit_owned_local_cleanup"]
    assert cleanups
    assert all(not any(keyword.arg in ("shared_exit_leaves", "shared_pinned_target")
                       for keyword in call.keywords) for call in cleanups)


def test_static_method_signatures_match_actual_generator_formatters():
    # Execute only the two stdlib-only formatter definitions, never the
    # generator module or its compiler-importing main() entrypoint.
    generator = ROOT / "scripts/regen_l1_codegen_static_methods.py"
    tree = ast.parse(generator.read_text())
    names = {"_format_param", "_format_method_entry"}
    selected = [copy.deepcopy(node) for node in tree.body
                if isinstance(node, ast.FunctionDef) and node.name in names]
    assert {node.name for node in selected} == names
    namespace = {"inspect": inspect}
    module = ast.Module(body=selected, type_ignores=[])
    exec(compile(ast.fix_missing_locations(module), "<static-method-formatters>", "exec",
                 flags=__future__.annotations.compiler_flag, dont_inherit=True), namespace)
    real = methods()
    table_lines = (CODEGEN / "_l1_codegen_static_methods.py").read_text().splitlines()
    for name in ("_emit_owned_local_cleanup", "_shared_return_pinned_cleanup"):
        signature = inspect.signature(real[name])
        expected_line = namespace["_format_method_entry"](name, signature)
        actual_lines = [line for line in table_lines
                        if line.startswith("    _append_method(out, " + repr(name) + ",")]
        assert actual_lines == [expected_line]
    cleanup = inspect.signature(real["_emit_owned_local_cleanup"])
    assert list(cleanup.parameters) == [
        "self", "skip_name", "skip_payload", "shared_exit_leaves", "shared_pinned_target",
    ]
    assert cleanup.parameters["skip_name"].default is None
    assert cleanup.parameters["skip_payload"].default is None
    assert cleanup.parameters["shared_exit_leaves"].default is False
    assert cleanup.parameters["shared_pinned_target"].default is None
    helper = inspect.signature(real["_shared_return_pinned_cleanup"])
    assert list(helper.parameters) == ["self", "slots", "target"]
    assert all(parameter.default is inspect.Parameter.empty
               for parameter in helper.parameters.values())
    host_contract = ast.parse((CODEGEN / "host_contract.py").read_text())
    assignments = [node for node in host_contract.body if isinstance(node, ast.Assign)
                   and any(isinstance(target, ast.Name)
                           and target.id == "L1_CODEGEN_HOST_METHODS" for target in node.targets)]
    assert len(assignments) == 1
    host_methods = ast.literal_eval(assignments[0].value)
    assert host_methods.count("_shared_return_pinned_cleanup") == 1
