"""Algorithm guards for hot pcc-Python runtime paths used by GC bootstrap."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
_GC_NOTE_ALLOC_PY = (
    _REPO_ROOT / "pcc/runtime/py/freestanding_gc_incremental_concurrent_scheduler.py"
)
_PY_OBJ_PY = _REPO_ROOT / "pcc" / "runtime" / "py" / "py_obj.py"
_PY_CLASS_PY = _REPO_ROOT / "pcc" / "runtime" / "py" / "py_class.py"


def _function_source(name: str, path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    module = ast.parse(text)
    lines = text.splitlines()
    for node in module.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            assert node.end_lineno is not None
            return "\n".join(lines[node.lineno - 1 : node.end_lineno])
    raise AssertionError(f"function {name!r} not found in {path}")


@pytest.mark.parametrize("initialized", (False, True))
def test_note_alloc_reuses_ready_config_and_initializes_once(initialized):
    memory = {
        "pcc_gc_config_initialized": int(initialized),
        # The allocation count is the offset-8 field of the caller's shard.
        ("pcc_gc_metric_shards", 3 * 128 + 8): 7,
        "pcc_gc_debt_bytes": 100,
        "pcc_gc_backend4_auto_step_pending": 0,
    }
    events = []

    def initialize():
        events.append("initialize")
        return 0

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
    source = _function_source("pcc_gc_note_alloc", _GC_NOTE_ALLOC_PY)
    exec(compile(source, str(_GC_NOTE_ALLOC_PY), "exec"), namespace)
    namespace["pcc_gc_note_alloc"](37)
    expected = [] if initialized else ["initialize"]
    assert events == expected
    assert memory[("pcc_gc_metric_shards", 3 * 128 + 8)] == 8
    assert memory["pcc_gc_backend4_auto_step_pending"] == 0


def test_pcc_gc_release_skips_backend_query_for_null_and_tagged_ints() -> None:
    py_body = _function_source("pcc_gc_release", _PY_OBJ_PY)
    py_prefix = py_body.split("backend: int = _gc_backend_fast()", 1)[0]
    assert "if ptr_is_null(o) != 0 or is_tagged_int(o) != 0:" in py_prefix
    assert "return" in py_prefix
    assert "_gc_backend_fast()" not in py_prefix


def test_tracking_admits_raw_threads_before_header_access() -> None:
    tracking = _REPO_ROOT / "pcc/runtime/py/freestanding_gc_tracking.py"
    for name in ("py_gc_track", "py_gc_untrack"):
        body = _function_source(name, tracking)
        assert body.index("pcc_current_thread_id()") < body.index("_flag_only_tracking()"), name
        assert body.index("_flag_only_tracking()") < body.index("atomic_rmw_i32("), name
        before_admission = body.split("pcc_current_thread_id()", 1)[0]
        assert "load_i32(obj" not in before_admission, name


def test_class_cstr_equality_rejects_prefix_mismatch_before_strlen() -> None:
    body = _function_source("_strs_eq", _PY_CLASS_PY)

    prefix = body.split("n: int = strlen(a)", 1)[0]
    assert "a0: int = load_i8(a, 0) & 0xFF" in prefix
    assert "b0: int = load_i8(b, 0) & 0xFF" in prefix
    assert "if a0 != b0:" in prefix
    assert "a1: int = load_i8(a, 1) & 0xFF" in prefix
    assert "b1: int = load_i8(b, 1) & 0xFF" in prefix
    assert "if a1 != b1:" in prefix
