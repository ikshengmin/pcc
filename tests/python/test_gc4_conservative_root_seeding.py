"""GC4 tracing mode seeds stack roots and refreshes cycle-local address pins."""
import ast
from pathlib import Path

import pytest

RUNTIME = Path(__file__).resolve().parents[2] / "pcc/runtime/py"


def body(module, name, namespace):
    tree = ast.parse((RUNTIME / module).read_text())
    function = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == name)
    function.decorator_list = []
    namespace["i64"] = int
    exec(compile(ast.Module(body=[function], type_ignores=[]), str(RUNTIME / module), "exec"), namespace)
    return namespace[name]


@pytest.mark.parametrize("enabled", (0, 1))
def test_root_seed_selects_one_external_root_owner_after_preparation(enabled):
    events = []
    globals_ = {"pcc_gc_explicit_collect_active": 1, "pcc_gc_backend4_no_refcounts": enabled}
    seed = body("freestanding_gc_common_mark_cycle.py", "pcc_gc_seed_roots", {
        "global_addr": lambda name: name, "load_i32": lambda name, offset: globals_[name],
        "pcc_gc_prepare_object_list_mark": lambda explicit: events.append(("prepare", explicit)),
        "pcc_gc_gray_refcount_external_roots": lambda: events.append("counts"),
        "pcc_gc_gray_conservative_thread_roots": lambda: events.append("machine"),
        "pcc_gc_gray_current_roots": lambda: events.append("registered"),
    })
    seed()
    assert events == [("prepare", 1), "machine" if enabled else "counts", "registered"]


@pytest.mark.parametrize("enabled,conservative,lease,pinned", (
    (0, 5, 0, False), (1, 1, 0, True), (1, 0, 0, False),
    (0, 0, 2, True), (1, 0, 2, True),
))
def test_cycle_pin_is_distinct_from_count_surplus_and_address_leases(enabled, conservative, lease, pinned):
    words = {0x100C: 0, 0x2038: conservative, 0x2050: lease}
    namespace = {
        "ptr_is_null": lambda ptr: ptr == 0, "is_tagged_int": lambda ptr: ptr & 1,
        "pcc_gc_static_literal_contains": lambda ptr: 0,
        "global_addr": lambda name: name,
        "load_i32": lambda base, offset: enabled if isinstance(base, str) else words[base + offset],
        "load_i64": lambda base, offset: words[base + offset],
        "pcc_gc_object_index_find": lambda ptr: 0x2000,
        "PYOBJECTHEADER_FLAGS_OFFSET": 12, "PY_FLAG_GC_PINNED": 64,
    }
    pin = body("freestanding_gc_root_operations.py", "pcc_gc_object_is_address_pinned", namespace)
    assert bool(pin(0x1000)) is pinned


@pytest.mark.parametrize("enabled", (0, 1))
def test_mark_preparation_drops_previous_cycle_conservative_pins(enabled):
    words = {0x1000: 0x2000, 0x1010: 0, 0x1020: 0, 0x1038: 1, 0x200C: 32}
    prepare = body("freestanding_gc_object_root_seeding.py", "pcc_gc_prepare_object_list_mark", {
        "global_addr": lambda name: name, "global_load_ptr": lambda name: 0x1000,
        "load_i32": lambda base, offset: enabled if isinstance(base, str) else words[base + offset],
        "load_i64": lambda base, offset: words[base + offset],
        "load_ptr": lambda base, offset: words[base + offset],
        "store_i32": lambda base, offset, value: words.__setitem__(base + offset, value),
        "store_i64": lambda base, offset, value: words.__setitem__(base + offset, value),
        "ptr_is_null": lambda ptr: ptr == 0, "is_tagged_int": lambda ptr: ptr & 1,
        "pcc_gc_gray_count_store_release": lambda count: None,
    })
    prepare(1)
    assert words[0x1038] == (0 if enabled else 1)
    assert words[0x200C] == 8


@pytest.mark.parametrize("operation", ("py_incref", "py_decref"))
@pytest.mark.parametrize("enabled", (0, 1))
def test_reference_updates_only_skip_the_checked_tail_in_tracing_mode(operation, enabled):
    checked = []
    globals_ = {"pcc_gc_refcount_atomic": 0, "pcc_gc_backend4_no_refcounts": enabled}
    update = body("py_obj.py", operation, {
        "ptr_is_null": lambda ptr: ptr == 0, "is_tagged_int": lambda ptr: ptr & 1,
        "global_addr": lambda name: name, "load_i32": lambda name, offset: globals_[name],
        "_py_incref_checked_tail": checked.append, "_py_decref_checked_tail": checked.append,
    })
    update(0x1000)
    assert checked == ([] if enabled else [0x1000])


@pytest.mark.parametrize("operation", ("_py_incref_prepare", "_py_decref_prepare"))
def test_count_free_store_plans_preserve_the_value_without_touching_its_header(operation):
    words = {}
    namespace = {
        "global_addr": lambda name: name, "load_i32": lambda name, offset: 1,
        "store_i64": lambda base, offset, value: words.__setitem__(base + offset, value),
        "store_ptr": lambda base, offset, value: words.__setitem__(base + offset, value),
    }
    body("py_obj.py", "_py_refcount_prepared_reset", namespace)
    prepare = body("py_obj.py", operation, namespace)
    prepare(0x1000, 0x2000)
    assert words[0x2000] == 0x1000
    assert words[0x2000 + 40] == 0
    assert words[0x2000 + 48] == 0
