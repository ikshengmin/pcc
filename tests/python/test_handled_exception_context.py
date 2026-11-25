"""Handled-state links borrow already registered exception slots per thread."""

import ast
from pathlib import Path
import threading


SOURCE = Path(__file__).resolve().parents[2] / "pcc/runtime/py/freestanding_gc_root_operations.py"


def _primitives():
    tls = threading.local()
    namespace = {
        "c_ptr": object,
        "i64": int,
        "c_abi_export": lambda _name: lambda function: function,
        "null": lambda: None,
        "ptr_is_null": lambda value: value is None,
        "ptr_eq": lambda lhs, rhs: lhs is rhs,
        "load_ptr": lambda value, offset: value[offset // 8],
        "store_ptr": lambda value, offset, item: value.__setitem__(offset // 8, item),
        "global_load_ptr": lambda _name: getattr(tls, "current", None),
        "global_store_ptr": lambda _name, value: setattr(tls, "current", value),
    }
    tree = ast.parse(SOURCE.read_text())
    functions = [node for node in tree.body if isinstance(node, ast.FunctionDef)
                 and node.name.startswith("py_handled_")]
    exec(compile(ast.Module(body=functions, type_ignores=[]), str(SOURCE), "exec"), namespace)
    return namespace


def test_handled_context_nested_root_relocation_and_detachment():
    runtime = _primitives()
    push, pop = runtime["py_handled_context_push"], runtime["py_handled_context_pop"]
    current, swap = runtime["py_handled_exception_slot"], runtime["py_handled_context_swap"]
    outer, inner = [None, None], [None, None]
    outer_slot, inner_slot = [object()], [object()]
    assert current() is None
    push(outer, outer_slot)
    moved = object()
    outer_slot[0] = moved
    assert current() is outer_slot and current()[0] is moved
    push(inner, inner_slot)
    assert current() is inner_slot
    assert pop(inner) == 1
    assert pop(inner) == 0
    assert current() is outer_slot
    checkpoint = swap(None)
    assert current() is None
    assert swap(checkpoint) is None
    assert current() is outer_slot
    assert pop(outer) == 1
    assert current() is None


def test_handled_context_thread_local_isolation():
    runtime = _primitives()
    barrier = threading.Barrier(3)
    results = []
    main_record, main_slot = [None, None], [object()]
    runtime["py_handled_context_push"](main_record, main_slot)
    def worker():
        record, slot = [None, None], [object()]
        results.append(runtime["py_handled_exception_slot"]() is None)
        runtime["py_handled_context_push"](record, slot)
        barrier.wait(timeout=5)
        results.append(runtime["py_handled_exception_slot"]() is slot)
        barrier.wait(timeout=5)
        runtime["py_handled_context_pop"](record)
        results.append(runtime["py_handled_exception_slot"]() is None)
    threads = [threading.Thread(target=worker) for _ in range(2)]
    for thread in threads:
        thread.start()
    barrier.wait(timeout=5)
    assert runtime["py_handled_exception_slot"]() is main_slot
    barrier.wait(timeout=5)
    for thread in threads:
        thread.join(timeout=5)
        assert not thread.is_alive()
    assert results == [True] * 6
    runtime["py_handled_context_pop"](main_record)


def _implicit_chainer(on_lock=None, *, backend=0):
    state = {"locked": False, "finishes": 0, "acquisitions": 0}
    def load(pointer):
        if isinstance(pointer, tuple):
            obj, offset = pointer
            return obj.get(offset)
        return pointer[0]
    def commit(plan, owner, pointer, value):
        assert state["locked"]
        obj, offset = pointer
        plan["old"] = obj.get(offset)
        obj[offset] = value
        return 1
    def finish(plan):
        assert not state["locked"]
        state["finishes"] += 1
    def lock():
        assert not state["locked"]
        state["acquisitions"] += 1
        if on_lock is not None:
            on_lock()
        state["locked"] = True
    namespace = {
        "c_abi_export": lambda _name: lambda function: function,
        "PY_TYPE_EXC": 8,
        "null": lambda: None,
        "ptr_is_null": lambda value: value is None,
        "ptr_eq": lambda lhs, rhs: lhs is rhs,
        "ptr_add": lambda obj, offset: (obj, offset),
        "is_tagged_int": lambda value: isinstance(value, int),
        "load_i32": lambda obj, offset: obj[offset],
        "stack_alloc": lambda _size: {},
        "pcc_gc_backend": lambda: backend,
        "pcc_gc_load_ptr": lambda _owner, pointer: load(pointer),
        # The raw read of the caller's context slot (empty-context return).
        "load_ptr": lambda pointer, offset: load(pointer) if offset == 0 else None,
        "pcc_gc_store_ptr_plan_init": lambda plan, _owner, _backend: plan.clear(),
        "pcc_gc_store_ptr_plan_commit_locked": commit,
        "pcc_gc_store_ptr_plan_finish": finish,
        "pcc_py_gc_minor_graph_lock": lock,
        "pcc_py_gc_minor_graph_unlock": lambda: state.__setitem__("locked", False),
    }
    source = SOURCE.with_name("py_exc_objects.py")
    tree = ast.parse(source.read_text())
    functions = [node for node in tree.body if isinstance(node, ast.FunctionDef)
                 and node.name == "py_exc_set_implicit_context_slots"]
    exec(compile(ast.Module(body=functions, type_ignores=[]), str(source), "exec"), namespace)
    return namespace["py_exc_set_implicit_context_slots"], state


def test_implicit_context_overwrite_self_and_indirect_cycles():
    chain, state = _implicit_chainer()
    first, second, error = [{8: 8, 40: None} for _ in range(3)]
    explicit_cause = object()
    error[32] = explicit_cause
    chain([error], [first])
    chain([error], [second])
    assert error[40] is second and error[32] is explicit_cause
    chain([error], [error])
    assert error[40] is second
    first[40] = second
    second[40] = error
    chain([error], [first])
    assert error[40] is first and first[40] is second and second[40] is None
    # Pre-existing explicit cycles do not hang and need not be destroyed.
    first[40], second[40] = second, first
    fresh = {8: 8, 40: None}
    chain([fresh], [first])
    assert fresh[40] is first and second[40] is first
    assert not state["locked"] and state["finishes"] == 10


def test_same_object_context_skips_graph_only_on_nonmoving_backends():
    for backend in (0,):
        chain, state = _implicit_chainer(backend=backend)
        previous = {8: 8, 40: None}
        error = {8: 8, 40: previous}
        chain([error], [error])
        assert error[40] is previous
        assert state["acquisitions"] == (0 if 1 <= backend <= 3 else 1)
        assert state["finishes"] == (0 if 1 <= backend <= 3 else 2)
        # A distinct pair that would introduce a cycle still commits under
        # the graph lock, including on the nonmoving fast-path backends.
        current = {8: 8, 40: error}
        seen = state["acquisitions"]
        chain([error], [current])
        assert error[40] is current and current[40] is None
        assert state["acquisitions"] == seen + 1
        assert not state["locked"]


def test_implicit_context_reloads_roots_after_graph_lock_parks():
    old_error, old_context = {8: 8, 40: None}, {8: 8, 40: None}
    error_slot, context_slot = [old_error], [old_context]
    moved_error, moved_context = {8: 8, 40: None}, {8: 8, 40: None}
    def relocate():
        error_slot[0], context_slot[0] = moved_error, moved_context
        old_error.clear()
        old_context.clear()
    chain, state = _implicit_chainer(relocate)
    chain(error_slot, context_slot)
    assert moved_error[40] is moved_context
    assert not state["locked"] and state["finishes"] == 2


def test_handled_cleanup_helpers_have_native_static_method_exports():
    from pcc.frontends.python.codegen.host_contract import L1_CODEGEN_HOST_METHODS
    from pcc.frontends.python.codegen._l1_codegen_static_methods import L1_CODEGEN_STATIC_METHODS
    from pcc.frontends.python.codegen.layer1_support import _default_native_module_exports

    helpers = (
        "_begin_handled_exception_scope", "_emit_handled_exception_scope_exit",
        "_emit_suspend_handled_exception_scopes", "_emit_resume_handled_exception_scopes",
        "_emit_chain_pending_handled_exception", "_exception_selection_owner_slot",
        "_clear_exception_selection_owner_slot", "_emit_exception_class_match",
        "_generator_tuple_build_name", "_generator_expr_may_suspend",
        "_emit_suspending_tuple_literal", "_emit_cancel_pending_return_roots",
        "_emit_owned_return_through_finally", "_emit_generator_yield_expr",
    )
    static = {entry["name"]: entry for entry in L1_CODEGEN_STATIC_METHODS}
    exports = _default_native_module_exports("pcc.frontends.python.codegen.layer1")
    native = {entry["name"]: entry for entry in exports["pcc.frontends.python.codegen.layer1"]["L1CodeGen"]["methods"]}
    for name in helpers:
        assert name in L1_CODEGEN_HOST_METHODS
        assert static[name] == native[name]
    assert tuple(parameter["name"] for parameter in static["_emit_cancel_pending_return_roots"]["call_sig"]) == (
        "self", "loop_exit", "root_base", "only_slot",
    )
    assert tuple(parameter["has_default"] for parameter in static["_emit_cancel_pending_return_roots"]["call_sig"]) == (False, True, True, True)
    assert tuple(parameter["name"] for parameter in static["_emit_owned_return_through_finally"]["call_sig"]) == (
        "self", "value", "stmt", "source_slot",
    )
    assert tuple(parameter["has_default"] for parameter in static["_emit_owned_return_through_finally"]["call_sig"]) == (False, False, False, True)


def test_exception_suppression_flag_has_one_c_python_export_identity():
    import re
    from pcc.runtime.py.py_abi_constants import PY_FLAG_EXC_SUPPRESS_CONTEXT
    from pcc.frontends.python.codegen.port_abi_exports import PORT_ABI_NATIVE_EXPORTS

    root = SOURCE.parents[3]
    header = (root / "pcc/runtime/src/py_internal.h").read_text()
    flags = {name: int(value, 0) for name, value in re.findall(
        r"^#define (PY_FLAG_\w+)\s+(0x[0-9a-fA-F]+|[0-9]+)$", header, re.MULTILINE,
    )}
    flag = PY_FLAG_EXC_SUPPRESS_CONTEXT
    assert flag == flags["PY_FLAG_EXC_SUPPRESS_CONTEXT"] == 0x08000000
    assert all(value & flag == 0 for name, value in flags.items() if name != "PY_FLAG_EXC_SUPPRESS_CONTEXT")
    assert PORT_ABI_NATIVE_EXPORTS["pcc.runtime.py.py_abi_constants"]["PY_FLAG_EXC_SUPPRESS_CONTEXT"]["value"] == flag
    generator = ast.parse((root / "scripts/gen_port_abi_constants.py").read_text())
    inventory = next(node.value for node in generator.body if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.target.id == "FLAGS")
    assert "PY_FLAG_EXC_SUPPRESS_CONTEXT" in ast.literal_eval(inventory)


def test_c_api_explicit_cause_sets_suppression_preserving_gc_bits():
    from pcc.runtime.py.py_abi_constants import PY_FLAG_EXC_SUPPRESS_CONTEXT, PY_TYPE_EXC

    def atomic_or(operation, obj, offset, value, order):
        assert (operation, offset, order) == ("or", 12, "relaxed")
        obj[offset] |= value
    namespace = {
        "c_abi_export": lambda _name: lambda function: function,
        "c_abi_typed_export": lambda *_args: lambda function: function,
        "ptr_is_null": lambda value: value is None,
        "_type_of": lambda value: value[8],
        "PY_TYPE_EXC": PY_TYPE_EXC,
        "PY_FLAG_EXC_SUPPRESS_CONTEXT": PY_FLAG_EXC_SUPPRESS_CONTEXT,
        "pcc_gc_note_relocation_read": lambda value: value,
        "atomic_rmw_i32": atomic_or,
        "ptr_add": lambda owner, offset: offset,
        "pcc_gc_store_ptr": lambda owner, offset, value: owner.__setitem__(offset, value),
        "pcc_diagnostics_runtime_log_event_code": lambda *_args: None,
    }
    for filename, name in (("py_exc_objects.py", "py_exc_set_cause"), ("py_capi_misc_runtime.py", "PyException_SetCause")):
        path = SOURCE.with_name(filename)
        tree = ast.parse(path.read_text())
        function = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == name)
        exec(compile(ast.Module(body=[function], type_ignores=[]), str(path), "exec"), namespace)
    flags = 1 | 2 | 4 | 8 | 64 | 128 | 256 | 262144
    error = {8: PY_TYPE_EXC, 12: flags}
    cause = object()
    for value in (cause, None):
        error[12] = flags
        namespace["PyException_SetCause"](error, value)
        assert error[32] is value
        assert error[12] == flags | PY_FLAG_EXC_SUPPRESS_CONTEXT


def test_generator_close_result_handoff_takes_slot_without_lock_or_pin():
    """Exercise the actual close handoff of a StopIteration value.

    One collector (item 2 (d)): nothing moves, so the handoff neither
    reloads under the graph lock nor pins; it clears the generator root and
    takes the value's slot owner with pcc_gc_take_slot.
    """
    import copy

    source = SOURCE.with_name("py_gen.py")
    tree = ast.parse(source.read_text())
    close = next(node for node in tree.body if isinstance(node, ast.FunctionDef)
                 and node.name == "py_gen_close")
    stopped = next(node for node in ast.walk(close) if isinstance(node, ast.If)
                   and isinstance(node.test, ast.Compare)
                   and isinstance(node.test.left, ast.Name)
                   and node.test.left.id == "stopped")
    clear_index = next(index for index, node in enumerate(stopped.body)
                       if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call)
                       and isinstance(node.value.func, ast.Name)
                       and node.value.func.id == "py_clear_exception")
    handoff = stopped.body[clear_index + 1:]
    value = {12: 0}
    exc_slot = [value]
    gen_slot = [object()]
    events = []

    def forbidden(*_args):
        raise AssertionError("the close handoff needs no graph lock or pin")

    def store_root(slot, new):
        assert new is None
        events.append(("store_root", slot is gen_slot))
        slot[0] = None

    def take_slot(slot):
        assert slot is exc_slot
        events.append(("take",))
        taken, slot[0] = slot[0], None
        return taken

    namespace = {
        "exc_slot": exc_slot, "gen_slot": gen_slot, "gen_root": object(),
        "exc_root": object(), "null": lambda: None,
        "pcc_py_gc_minor_graph_lock": forbidden,
        "pcc_py_gc_minor_graph_unlock": forbidden, "pcc_gc_pin": forbidden,
        "pcc_gc_store_root": store_root,
        "pcc_gc_scheduler_root_unregister_handle": lambda _root: events.append(("unregister",)),
        "pcc_gc_take_slot": take_slot,
    }
    function = ast.FunctionDef(name="handoff", args=ast.arguments(
        posonlyargs=[], args=[], kwonlyargs=[], kw_defaults=[], defaults=[]),
        body=copy.deepcopy(handoff), decorator_list=[])
    unit = ast.fix_missing_locations(ast.Module(body=[function], type_ignores=[]))
    exec(compile(unit, str(source), "exec"), namespace)
    assert namespace["handoff"]() is value
    assert exc_slot[0] is None and gen_slot[0] is None
    assert events[-1] == ("take",) and ("store_root", True) in events
