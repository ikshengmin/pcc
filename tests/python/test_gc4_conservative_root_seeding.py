"""GC4 tracing mode seeds stack roots and refreshes cycle-local address pins."""
import ast
from pathlib import Path

import pytest
from tests.python.runtime_body_model import install_extern_defaults

RUNTIME = Path(__file__).resolve().parents[2] / "pcc/runtime/py"


class _ConfigAbort(Exception):
    pass


def _configured(backend, *, tracing=0, legacy=0):
    cells = {}
    events = []
    env_names = []
    options = {"PCC_GC_TRACING_ONLY": tracing, "PCC_GC4_TRACING_ONLY": legacy}

    def abort():
        raise _ConfigAbort

    def getenv(name):
        env_names.append(name)
        return name

    def parse_env_i32(name, default, low, high):
        env_names.append(name)
        return options.get(name, default)

    # Single-thread policy model only; configuration races have their own
    # fixture. Keep the aliases used by the actual read-barrier helper too.
    namespace = {
        "global_addr": lambda name: name,
        "load_i32": lambda name, offset: cells.get(name, 0),
        "atomic_load_i32": lambda name, offset, order: cells.get(name, 0),
        "store_i32": lambda name, offset, value: cells.__setitem__(name, value),
        "atomic_store_i32": lambda name, offset, value, order: cells.__setitem__(name, value),
        "store_i64": lambda name, offset, value: cells.__setitem__(name, value),
        "cstr": lambda name: name,
        "pcc_platform_getenv": getenv,
        "_pcc_gc_config_parse_backend": lambda name, fallback: backend,
        "pcc_gc_config_parse_env_i32": parse_env_i32,
        "pcc_platform_write": lambda fd, message, size: events.append(("error", message)),
        "pcc_platform_abort": abort,
        "pcc_thread_enable_conservative_roots": lambda: events.append("snapshots"),
        "pcc_threads_enabled": lambda: 1,
        "pcc_gc_maybe_start_cms_worker": lambda: events.append("worker"),
        "pcc_py_gc_minor_graph_lock": lambda: None,
        "pcc_py_gc_minor_graph_unlock": lambda: None,
        "_graph_lock": lambda: None,
        "_graph_unlock": lambda: None,
        "global_load_ptr": lambda name: cells.get(name, 0),
        "ptr_is_null": lambda value: int(value == 0),
    }
    body("freestanding_gc_forwarding_identity.py", "pcc_gc_refresh_read_barrier", namespace)
    config = body("freestanding_gc_public_collection.py", "pcc_gc_config_ensure", namespace)
    assert config() == backend
    assert config() == backend
    assert not set(options).intersection(env_names)
    return cells, events, namespace


@pytest.mark.parametrize("backend", [0, 1, 2, 3, 4])
def test_collector_identity_selects_counted_gc0_and_count_free_gc1_to_gc4(backend):
    cells, events, _ = _configured(backend)
    assert cells["pcc_gc_backend4_no_refcounts"] == int(backend != 0)
    assert cells["pcc_gc_refcount_atomic"] == int(backend == 0)
    assert events == (["worker"] if backend == 0 else ["snapshots", "worker"])


@pytest.mark.parametrize("backend", [1, 2, 3, 4])
@pytest.mark.parametrize("operation", ["py_incref", "py_decref"])
def test_count_free_configuration_enables_machine_roots_and_suppresses_updates(backend, operation):
    cells, events, namespace = _configured(backend, tracing=1)
    assert cells["pcc_gc_backend4_no_refcounts"] == 1
    assert cells["pcc_gc_refcount_atomic"] == 0
    assert cells["pcc_gc_frame_registry_inactive"] == 0
    assert events == ["snapshots", "worker"]
    namespace.update(
        ptr_is_null=lambda value: value == 0,
        is_tagged_int=lambda value: value & 1,
        _py_incref_checked_tail=lambda value: pytest.fail("ordinary retain"),
        _py_decref_checked_tail=lambda value: pytest.fail("ordinary release"),
    )
    body("py_obj.py", operation, namespace)(4096)
    assert events == ["snapshots", "worker"]


@pytest.mark.parametrize("backend", [0, 3])
def test_removed_tracing_selector_does_not_override_gc0_or_gc3(backend):
    cells, events, _ = _configured(backend, tracing=1)
    default_cells, default_events, _ = _configured(backend)
    assert (cells, events) == (default_cells, default_events)


@pytest.mark.parametrize("backend", [0, 1, 2, 3])
def test_removed_gc4_selector_does_not_restrict_other_backends(backend):
    cells, events, _ = _configured(backend, legacy=1)
    default_cells, default_events, _ = _configured(backend)
    assert (cells, events) == (default_cells, default_events)


def test_removed_gc4_selector_leaves_gc4_default_ownership_unchanged():
    cells, events, _ = _configured(4, legacy=1)
    default_cells, default_events, _ = _configured(4)
    assert (cells, events) == (default_cells, default_events)
    assert cells["pcc_gc_backend4_no_refcounts"] == 1
    assert events == ["snapshots", "worker"]


def body(module, name, namespace):
    tree = ast.parse((RUNTIME / module).read_text())
    function = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == name)
    function.decorator_list = []
    namespace["i64"] = int
    exec(compile(ast.Module(body=[function], type_ignores=[]), str(RUNTIME / module), "exec"), namespace)
    # Cross-object entries the model leaves unbound (no object cells here).
    install_extern_defaults(namespace)
    return namespace[name]


@pytest.mark.parametrize("enabled", (0, 1))
@pytest.mark.parametrize("chain_cut", (False, True))
def test_root_seed_selects_one_external_root_owner_after_preparation(enabled, chain_cut):
    events = []
    globals_ = {"pcc_gc_explicit_collect_active": 1, "pcc_gc_backend4_no_refcounts": enabled}

    def machine():
        # Without a chain cut the conservative walk shades listed pins too.
        events.append("machine" if chain_cut else "machine+listed")
        return 0 if chain_cut else 1

    seed = body("freestanding_gc_common_mark_cycle.py", "pcc_gc_seed_roots", {
        "global_addr": lambda name: name, "load_i32": lambda name, offset: globals_[name],
        "pcc_gc_prepare_object_list_mark": lambda explicit: events.append(("prepare", explicit)),
        "pcc_gc_gray_refcount_external_roots": lambda: events.append("counts"),
        "pcc_gc_gray_conservative_and_listed_pinned_roots": machine,
        "pcc_gc_gray_registered_roots": lambda: events.append("registered"),
        "pcc_gc_gray_current_roots": lambda: events.append("listed+registered"),
    })
    seed()
    if not enabled:
        roots = ["counts", "listed+registered"]
    elif chain_cut:
        roots = ["machine", "listed+registered"]
    else:
        roots = ["machine+listed", "registered"]
    assert events == [("prepare", 1)] + roots


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
    gray_worklist_events = []
    prepare = body("freestanding_gc_object_root_seeding.py", "pcc_gc_prepare_object_list_mark", {
        "global_addr": lambda name: name, "global_load_ptr": lambda name: 0x1000,
        "load_i32": lambda base, offset: enabled if isinstance(base, str) else words[base + offset],
        "load_i64": lambda base, offset: words[base + offset],
        "load_ptr": lambda base, offset: words[base + offset],
        "store_i32": lambda base, offset, value: words.__setitem__(base + offset, value),
        "store_i64": lambda base, offset, value: words.__setitem__(base + offset, value),
        "ptr_is_null": lambda ptr: ptr == 0, "is_tagged_int": lambda ptr: ptr & 1,
        "pcc_gc_gray_count_store_release": lambda count: None,
        # This pin-reset fixture owns no persistent gray-queue storage.
        "pcc_gc_gray_worklist_clear_stopped": lambda: gray_worklist_events.append("clear"),
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
