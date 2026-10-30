"""Algorithm guards for hot pcc-Python runtime paths used by GC bootstrap."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
_GC_BACKEND_PY = _REPO_ROOT / "pcc" / "runtime" / "py" / "py_gc_backend.py"
_GC_PUBLIC_COLLECTION_PY = (
    _REPO_ROOT
    / "pcc" / "runtime"
    / "py"
    / "freestanding_gc_public_collection.py"
)
_GC_FRAME_REGISTRY_PY = (
    _REPO_ROOT / "pcc" / "runtime" / "py" / "freestanding_gc_frame_registry.py"
)
_GC_INCREMENTAL_CONCURRENT_SCHEDULER_PY = (
    _REPO_ROOT
    / "pcc" / "runtime"
    / "py"
    / "freestanding_gc_incremental_concurrent_scheduler.py"
)
_GC_BARRIER_DISPATCHER_PY = (
    _REPO_ROOT
    / "pcc" / "runtime"
    / "py"
    / "freestanding_gc_barrier_dispatcher.py"
)
_GC_GENERATIONAL_PROMOTION_PY = (
    _REPO_ROOT
    / "pcc" / "runtime"
    / "py"
    / "freestanding_gc_generational_promotion.py"
)
_GC_RELOCATION_DRAIN_PY = (
    _REPO_ROOT
    / "pcc" / "runtime"
    / "py"
    / "freestanding_gc_relocation_drain.py"
)
_GC_RELOCATION_SELECTOR_PY = (
    _REPO_ROOT
    / "pcc" / "runtime"
    / "py"
    / "freestanding_gc_relocation_selector.py"
)
_GC_ZPAGE_ALLOCATION_PY = (
    _REPO_ROOT
    / "pcc" / "runtime"
    / "py"
    / "freestanding_gc_zpage_allocation.py"
)
_PY_OBJ_PY = _REPO_ROOT / "pcc" / "runtime" / "py" / "py_obj.py"
_PY_CLASS_PY = _REPO_ROOT / "pcc" / "runtime" / "py" / "py_class.py"


def _source_text(path: Path = _GC_BACKEND_PY) -> str:
    return path.read_text(encoding="utf-8")


def _function_source(name: str, path: Path = _GC_BACKEND_PY) -> str:
    text = _source_text(path)
    module = ast.parse(text)
    lines = text.splitlines()
    for node in module.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            assert node.end_lineno is not None
            return "\n".join(lines[node.lineno - 1 : node.end_lineno])
    raise AssertionError(f"function {name!r} not found in {path}")


def test_init_config_returns_cached_backend_for_hot_dispatch() -> None:
    init_config = _function_source("pcc_gc_config_ensure", _GC_PUBLIC_COLLECTION_PY)
    backend = _function_source("pcc_gc_backend")
    managed = _source_text()

    assert "def pcc_gc_config_ensure() -> i64:" in init_config
    assert 'return load_i32(global_addr("pcc_gc_backend_selected"), 0)' in init_config
    assert "return backend" in init_config
    assert '_init_config = extern("pcc_gc_config_ensure", (), c_int64)' in managed
    assert "return _init_config()" in backend


def test_gc_backend_environment_is_exact_and_fails_closed() -> None:
    py_parser = _function_source(
        "_pcc_gc_config_parse_backend", _GC_PUBLIC_COLLECTION_PY
    )
    py_abort = _function_source(
        "_pcc_gc_config_abort_bad_backend", _GC_PUBLIC_COLLECTION_PY
    )

    assert "first >= 48 and first <= 52" in py_parser
    assert "load_i8(raw, 1) == 0" in py_parser
    assert "_pcc_gc_config_abort_bad_backend()" in py_parser
    assert "pcc_platform_write(2, message, length)" in py_abort
    assert "pcc_platform_abort()" in py_abort


def test_hot_gc_paths_reuse_init_config_backend_value() -> None:
    hot_functions = {
        "pcc_gc_try_minor_alloc": ("pcc_gc_try_minor_alloc", _GC_BACKEND_PY),
        "pcc_gc_backend4_try_zpage_alloc": (
            "pcc_gc_backend4_try_zpage_alloc",
            _GC_ZPAGE_ALLOCATION_PY,
        ),
        "pcc_gc_generational_promote_young_if_known": (
            "pcc_gc_generational_promote_young_if_known",
            _GC_GENERATIONAL_PROMOTION_PY,
        ),
        "pcc_gc_step": ("pcc_gc_step", _GC_BARRIER_DISPATCHER_PY),
        "pcc_gc_note_object_allocated_sized": (
            "pcc_gc_note_object_allocated_sized",
            _GC_BACKEND_PY,
        ),
        "pcc_gc_select_relocation_set": (
            "pcc_gc_select_relocation_set",
            _GC_RELOCATION_SELECTOR_PY,
        ),
        "pcc_gc_backend4_evacuation_page_drain": (
            "pcc_gc_backend4_evacuation_page_drain",
            _GC_RELOCATION_DRAIN_PY,
        ),
        "pcc_gc_note_slot_write_barrier": (
            "pcc_gc_note_slot_write_barrier",
            _GC_BARRIER_DISPATCHER_PY,
        ),
    }

    for name, (function_name, source_path) in hot_functions.items():
        if name == "pcc_gc_step" or name == "pcc_gc_note_slot_write_barrier":
            body = _function_source(function_name, source_path)
            marker = "backend: i64 = _selected_backend()"
            assert marker in body, name
            assert "pcc_gc_backend()" not in body.split(marker, 1)[1], name
            continue
        body = _function_source(function_name, source_path)
        marker = "backend: i64 = _init_config()"
        if marker in body:
            suffix = body.split(marker, 1)[1]
        elif "backend: i64 = pcc_gc_config_ensure()" in body:
            marker = "backend: i64 = pcc_gc_config_ensure()"
            suffix = body.split(marker, 1)[1]
        else:
            assert 'load_i32(global_addr("pcc_gc_config_initialized"), 0) == 0' in body, name
            init_assign = "backend = _init_config()"
            if init_assign not in body:
                init_assign = "backend = pcc_gc_config_ensure()"
            assert init_assign in body, name
            assert 'backend = load_i32(global_addr("pcc_gc_backend_selected"), 0)' in body, name
            suffix = body.split(
                'backend = load_i32(global_addr("pcc_gc_backend_selected"), 0)',
                1,
            )[1]
        assert "pcc_gc_backend()" not in suffix, name

    note_alloc = _function_source(
        "pcc_gc_note_alloc", _GC_INCREMENTAL_CONCURRENT_SCHEDULER_PY
    )
    assert note_alloc.count("pcc_gc_config_ensure()") == 1

    frame_leave = _function_source("pcc_gc_note_frame_leave", _GC_FRAME_REGISTRY_PY)
    marker = "backend: i64 = gc_backend_current()"
    assert marker in frame_leave
    assert "gc_backend_current()" not in frame_leave.split(marker, 1)[1]



@pytest.mark.parametrize("initialized", (False, True))
@pytest.mark.parametrize("backend", range(5))
def test_note_alloc_reuses_ready_config_and_initializes_once(initialized, backend):
    memory = {
        "pcc_gc_config_initialized": int(initialized),
        "pcc_gc_backend_selected": backend if initialized else 99,
        # The allocation count is the offset-8 field of the caller's shard.
        ("pcc_gc_metric_shards", 3 * 128 + 8): 7,
        "pcc_gc_debt_bytes": 100,
        "pcc_gc_backend4_auto_step_pending": 0,
    }
    events = []

    def initialize():
        events.append("initialize")
        return backend

    def store(address, offset, value, *order):
        memory[address] = value

    def rmw(operation, address, offset, value, order):
        assert operation == "add"
        old = memory[(address, offset)]
        memory[(address, offset)] = old + value
        return old

    namespace = {
        "i64": int,
        "global_addr": lambda name: name,
        "load_i32": lambda address, offset: memory[address],
        "atomic_load_i64": lambda address, offset, order: memory[address],
        "store_i32": store,
        "atomic_store_i32": store,
        "atomic_rmw_i64": rmw,
        "pcc_gc_metric_shard": lambda: 3 * 128,
        "pcc_gc_config_ensure": initialize,
        "pcc_gc_maybe_start_cms_worker": lambda: events.append("start"),
        "pcc_gc_cms_note_alloc": lambda size: events.append(("allocate", size)),
        "pcc_gc_tracing_debt_threshold": lambda: 50,
    }
    source = _function_source("pcc_gc_note_alloc", _GC_INCREMENTAL_CONCURRENT_SCHEDULER_PY)
    exec(compile(source, str(_GC_INCREMENTAL_CONCURRENT_SCHEDULER_PY), "exec"), namespace)
    namespace["pcc_gc_note_alloc"](37)
    expected = [] if initialized else ["initialize"]
    if backend:
        expected += ["start", ("allocate", 37)]
    assert events == expected
    assert memory[("pcc_gc_metric_shards", 3 * 128 + 8)] == 8
    assert memory["pcc_gc_backend4_auto_step_pending"] == int(backend == 4)


def test_gc1_auto_step_uses_selected_backend_without_exported_query() -> None:
    body = _function_source(
        "pcc_gc_incremental_maybe_auto_step",
        _GC_INCREMENTAL_CONCURRENT_SCHEDULER_PY,
    )

    assert 'load_i32(global_addr("pcc_gc_backend_selected"), 0) != 1' in body
    assert "pcc_gc_backend()" not in body


def test_py_obj_hot_paths_use_steady_state_backend_fastpath() -> None:
    helper = _function_source("_gc_backend_fast", _PY_OBJ_PY)
    assert 'load_i32(global_addr("pcc_gc_config_initialized"), 0) == 0' in helper
    assert "return pcc_gc_backend()" in helper
    assert 'return load_i32(global_addr("pcc_gc_backend_selected"), 0)' in helper

    inlined_hot_functions = (
        "pcc_gc_load_ptr",
        "pcc_gc_store_ptr",
        "_py_incref_prepare",
        "_py_decref_prepare",
    )
    inline_markers = (
        'load_i32(global_addr("pcc_gc_config_initialized"), 0) == 0',
        "backend = pcc_gc_backend()",
        'backend = load_i32(global_addr("pcc_gc_backend_selected"), 0)',
    )
    for name in inlined_hot_functions:
        body = _function_source(name, _PY_OBJ_PY)
        for marker in inline_markers:
            assert marker in body, name
        assert "backend: int = _gc_backend_fast()" not in body, name

    for name in ("py_incref", "py_decref"):
        entry = _function_source(name, _PY_OBJ_PY)
        tail_name = "_" + name + "_checked_tail"
        assert tail_name + "(o)" in entry
        assert "pcc_gc_backend()" not in entry
        tail = _function_source(tail_name, _PY_OBJ_PY)
        assert "if _gc_backend_fast() != 0:" in tail
        assert "_" + name + "_prepare(o, prepared)" in tail
        assert "pcc_gc_backend()" not in tail

    helper_hot_functions = (
        "pcc_gc_release",
        "pcc_gc_load_borrowed_ptr",
        "pcc_gc_resolve_owned_ptr",
        "pcc_gc_store_root",
        "pcc_gc_collect",
    )

    for name in helper_hot_functions:
        body = _function_source(name, _PY_OBJ_PY)
        marker = "backend: int = _gc_backend_fast()"
        assert marker in body, name
        assert "pcc_gc_backend()" not in body.split(marker, 1)[1], name


def test_pcc_gc_release_skips_backend_query_for_null_and_tagged_ints() -> None:
    py_body = _function_source("pcc_gc_release", _PY_OBJ_PY)
    py_prefix = py_body.split("backend: int = _gc_backend_fast()", 1)[0]
    assert "if ptr_is_null(o) != 0 or is_tagged_int(o) != 0:" in py_prefix
    assert "return" in py_prefix
    assert "_gc_backend_fast()" not in py_prefix


def test_py_gc_track_checks_threads_before_backend_query() -> None:
    for name in ("py_gc_track", "py_gc_untrack"):
        body = _function_source(
            name,
            _REPO_ROOT
            / "pcc" / "runtime"
            / "py"
            / "freestanding_gc_tracking.py",
        )
        assert "if pcc_threads_enabled() != 0 and gc_backend_current() == 4:" in body, name
        assert "if gc_backend_current() == 4 and pcc_threads_enabled() != 0:" not in body, name


def test_class_cstr_equality_rejects_prefix_mismatch_before_strlen() -> None:
    body = _function_source("_strs_eq", _PY_CLASS_PY)

    prefix = body.split("n: int = strlen(a)", 1)[0]
    assert "a0: int = load_i8(a, 0) & 0xFF" in prefix
    assert "b0: int = load_i8(b, 0) & 0xFF" in prefix
    assert "if a0 != b0:" in prefix
    assert "a1: int = load_i8(a, 1) & 0xFF" in prefix
    assert "b1: int = load_i8(b, 1) & 0xFF" in prefix
    assert "if a1 != b1:" in prefix
