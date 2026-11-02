"""PEP 442: clear weak targets, run reachable weakrefs, then finalize."""
import ast
from pathlib import Path


SOURCE = Path(__file__).resolve().parents[2] / "pcc/runtime/py/py_weakref.py"


def test_all_targets_are_cleared_before_callbacks_and_unreachable_refs_are_silent():
    words = {}
    leases = {}
    freed = []
    callbacks = []
    released_objects = []
    cleared_errors = []
    refs = (0x2000, 0x2100, 0x2200)
    targets = (0x4000, 0x4100, 0x4000)
    for index, (ref, target) in enumerate(zip(refs, targets)):
        words[ref + 12] = 1024 if index == 2 else 0
        words[ref + 16] = target
        words[ref + 24] = 0x8000 + index
        words[ref + 40] = refs[index + 1] if index < 2 else 0
        words[target + 8] = 104
        words[target + 12] = 0
    allocated = []
    tuples = {}

    def malloc(size):
        assert size == 24
        address = 0x10000 + len(allocated) * 32
        allocated.append(address)
        return address

    def acquire(slot):
        ref = words[slot]
        assert ref != refs[2]
        token = ref + 17
        leases[slot] = token
        return token

    def release(slot, token):
        assert leases.pop(slot) == token
        return 0

    def tuple_new(size):
        assert size == 1
        address = 0x20000 + len(tuples) * 16
        tuples[address] = None
        return address

    def call(callback, args, kwargs):
        assert all(words[ref + 16] == 0 for ref in refs)
        ref = tuples[args]
        assert any(words[slot] == ref for slot in leases)
        assert callback == words[ref + 24]
        callbacks.append(ref)
        # A callback may rewrite the global weakref chain; the frozen pending
        # queue must still deliver each reachable callback once.
        words[refs[0] + 40] = 0
        return 0x30000 + len(callbacks)

    def abort():
        raise AssertionError("unexpected fatal weakref batch failure")

    namespace = {
        "global_load_ptr": lambda name: refs[0],
        "load_ptr": lambda base, offset: words[base + offset],
        "load_i64": lambda base, offset: words[base + offset],
        "load_i32": lambda base, offset: words[base + offset],
        "store_ptr": lambda base, offset, value: words.__setitem__(base + offset, value),
        "store_i64": lambda base, offset, value: words.__setitem__(base + offset, value),
        "ptr_is_null": lambda ptr: ptr == 0, "is_tagged_int": lambda ptr: ptr & 1,
        "ptr_add": lambda base, offset: base + offset, "null": lambda: 0,
        "pcc_gc_note_relocation_read": lambda target: target,
        "pcc_gc_tracing_is_sweep_candidate": lambda target: int(target in targets),
        "pcc_capi_is_cext_type_tag": lambda tag: 0,
        "pcc_diagnostics_runtime_log_event_code": lambda *args: None,
        "malloc": malloc, "free": freed.append,
        "pcc_gc_foreign_lease_acquire": acquire, "pcc_gc_foreign_lease_release": release,
        "pcc_platform_abort": abort,
        "pcc_gc_load_ptr": lambda owner, slot: words[slot],
        "py_tuple_new": tuple_new,
        "py_tuple_set_item": lambda args, index, ref: tuples.__setitem__(args, ref),
        "py_obj_call": call, "_py_none": lambda: 0,
        "py_decref": released_objects.append, "py_clear_exception": lambda: cleared_errors.append(True),
    }
    tree = ast.parse(SOURCE.read_text())
    selected = []
    for node in tree.body:
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            if node.target.id.startswith("_PENDING_WEAK_"):
                selected.append(node)
        if isinstance(node, ast.FunctionDef) and node.name in {
            "_weakref_run_callback", "py_weakref_invalidate_tracing_candidates",
        }:
            node.decorator_list = []
            selected.append(node)
    exec(compile(ast.Module(body=selected, type_ignores=[]), str(SOURCE), "exec"), namespace)
    assert namespace["py_weakref_invalidate_tracing_candidates"]() == 2
    assert callbacks == list(refs[:2])
    assert leases == {}
    assert freed == allocated
    assert len(released_objects) == 4
    assert len(cleared_errors) == 2
