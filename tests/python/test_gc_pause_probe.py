from __future__ import annotations

import ast
from pathlib import Path

import pytest

from pcc.diagnostics import _gc_pause_layout as layout
from pcc.diagnostics.gc_pause_probe import make_overlay, instrument_module, render_changes


ROOT = Path(__file__).resolve().parents[2]
CORE = Path("pcc/runtime/py/py_obj.py")
SWEEP = Path("pcc/runtime/py/freestanding_gc_tracing_sweep_collector.py")
ABI = Path("pcc/frontends/python/codegen/runtime_abi.py")


def names(code):
    return {node.name: node for node in ast.parse(code).body if isinstance(node, ast.FunctionDef)}


def calls(node, name):
    return [child for child in ast.walk(node) if isinstance(child, ast.Call)
            and isinstance(child.func, ast.Name) and child.func.id == name]


def test_overlay_has_two_original_collection_windows_and_nested_phases():
    diff = render_changes(ROOT)
    core = names(diff[CORE])
    for name in ("_gc_tracing_automatic_collect", "pcc_gc_collect"):
        function = core[name]
        assert len(calls(function, "pcc_exp_pause_begin")) == 1
        [alloc] = [node for node in calls(function, "stack_alloc")
                   if ast.unparse(node.args[0]) == "_PAUSE_CONTEXT_BYTES"]
        assert isinstance(alloc.args[0], ast.Name)
        [private] = calls(function, "pcc_exp_pause_finish")
        [public] = calls(function, "pcc_gc_record_collection_stw_pause")
        assert ast.unparse(private.args[-1]) == ast.unparse(public.args[-1]) == "exp_end_us"
    sweep = names(diff[SWEEP])
    assert calls(sweep["_sweep_clear_and_reclaim"], "pcc_exp_pause_switch")
    assert calls(sweep["_user_code_window_open"], "pcc_exp_pause_window")
    assert calls(sweep["_user_code_window_close"], "pcc_exp_pause_window")
    for code in diff.values():
        ast.parse(code)


def test_scope_shape_drift_is_loud_and_cannot_modify_the_checkout(tmp_path):
    source = (ROOT / CORE).read_text().replace(
        "pcc_gc_record_collection_stw_pause(collection_stw_start, pcc_runtime_monotonic_us())",
        "pcc_gc_record_collection_stw_pause(collection_stw_start, 247)")
    with pytest.raises(ValueError, match="window"):
        instrument_module("py_obj", source)
    with pytest.raises(ValueError):
        make_overlay(ROOT, ROOT / "pcc" / "not-an-overlay")
    with pytest.raises(FileExistsError):
        make_overlay(ROOT, tmp_path)


def test_returns_evaluate_once_and_restore_outer_phase():
    code = '''from pcc.extern import extern, c_int64
x = extern("x", (), c_int64)
def _capture_thread_words(x: i64) -> i64:
    if x < 1:
        return bar(1)
    return x
def _pin_snapshot_owners() -> None:
    if bar(0):
        return
    baz()
'''
    output = names(instrument_module("freestanding_gc_conservative_roots", code))
    f = output["_capture_thread_words"]
    assert len(calls(f, "bar")) == 1
    assert len(calls(f, "pcc_exp_pause_switch")) == 3  # enter + both exits
    assert len(calls(output["_pin_snapshot_owners"], "pcc_exp_pause_switch")) == 3


def test_callback_reacquire_anchor_is_required():
    text = (ROOT / SWEEP).read_text().replace(
        "pcc_thread_world_reacquire_held(load_i64(window, 0))", "different_call(window)")
    with pytest.raises(ValueError, match="window"):
        instrument_module(SWEEP.stem, text)


def test_generated_primitives_are_declared_and_result_reader_is_bounded():
    diff = render_changes(ROOT)
    abi = ast.parse(diff[ABI]).body
    for name in ("FREESTANDING_GC_THREAD_LOCAL_GLOBALS", "FREESTANDING_GC_PTR_GLOBALS"):
        [declaration] = [node for node in abi if isinstance(node, ast.AnnAssign)
                          and isinstance(node.target, ast.Name) and node.target.id == name]
        assert "pcc_exp_pause_owner" in {n.value for n in ast.walk(declaration)
                                          if isinstance(n, ast.Constant)}
    functions = names(diff[CORE])
    reader = ast.unparse(functions["pcc_exp_pause_value"])
    assert "i < 0 or i >= _PAUSE_RESULT_SLOTS" in reader
    assert "i * _PAUSE_WORD_BYTES" in reader
    assert 'define_thread_local_ptr_null("pcc_exp_pause_owner")' in diff[CORE]


@pytest.fixture
def clocked_runtime():
    """Exercise the generated accounting functions with a controlled clock."""
    from pcc.diagnostics._gc_pause_kernel import RUNTIME

    state = {"tls": None, "out": [], "time": 0}

    def store(p, off, value):
        p[off // layout.WORD_BYTES] = value

    def load(p, off):
        return p[off // layout.WORD_BYTES]

    def clear(p, _value, size):
        assert size == len(p) * layout.WORD_BYTES
        p[:] = [0] * len(p)

    native = {
        "define_thread_local_ptr_null": lambda *_: None,
        "define_global_i64_array": lambda _name, *slots: state["out"].extend(slots),
        "c_abi_export": lambda _: lambda f: f,
        "memset": clear,
        "load_i64": load, "store_i64": store,
        "load_ptr": load, "store_ptr": store,
        "global_load_ptr": lambda _: state["tls"],
        "global_store_ptr": lambda _, v: state.__setitem__("tls", v),
        "global_addr": lambda _: state["out"],
        "pcc_runtime_monotonic_us": lambda: state["time"], "ptr_is_null": lambda p: int(p is None),
    }
    exec(RUNTIME, native)
    assert len(state["out"]) == layout.SLOTS
    return state, native, [0] * (layout.CONTEXT_BYTES // layout.WORD_BYTES)


def test_exclusive_intervals_ties_zero_and_invalid_time(clocked_runtime):
    from pcc.diagnostics.gc_pause_report import phase_report

    state, native, ctx = clocked_runtime
    switch = native["pcc_exp_pause_switch"]
    begin, finish = native["pcc_exp_pause_begin"], native["pcc_exp_pause_finish"]
    assert switch(3) == -1
    begin(ctx, 100)
    state["time"] = 105
    assert switch(2) == 0               # Outer mark starts.
    state["time"] = 112
    assert switch(5) == 2               # Nested stack capture.
    state["time"] = 115
    assert switch(2) == 5
    state["time"] = 116
    native["pcc_exp_pause_window"](1)    # World running, not charged to mark.
    state["time"] = 120
    native["pcc_exp_pause_window"](0)
    finish(ctx, 100, 125)
    begin(ctx, 200)
    finish(ctx, 200, 225)               # Tied max: keep the first event's phases.
    begin(ctx, 250)
    finish(ctx, 250, 250)               # Public counter rounds zero to 1 µs.
    report = phase_report((3, 51, 25), state["out"])
    assert report["total_us"] == 50    # 0µs resolution does not charge runtime_other.
    assert report["zero_us_windows"] == 1
    assert report["public_telemetry"] == {"windows": 3, "total_us": 51, "maximum_us": 25}
    assert report["longest_window_us"]["runtime_other"] == 5
    assert report["longest_window_us"]["mark_other"] == 13
    assert report["longest_window_us"]["capture_sort_root_words"] == 3
    assert report["longest_window_us"]["world_released_until_reacquire"] == 4
    assert report["aggregate_us"]["runtime_other"] == 30
    assert state["tls"] is None
    begin(ctx, 330)
    state["time"] = 329
    switch(2)
    finish(ctx, 330, 339)
    assert state["out"][layout.INVALID] != 0 and state["tls"] is None


def test_only_same_tick_windows_leave_all_phase_values_zero(clocked_runtime):
    from pcc.diagnostics.gc_pause_report import phase_report

    state, native, ctx = clocked_runtime
    for start in (100, 200):
        native["pcc_exp_pause_begin"](ctx, start)
        native["pcc_exp_pause_finish"](ctx, start, start)
    read = native["pcc_exp_pause_value"]
    report = phase_report([2, 2, 1], [read(i) for i in range(layout.SLOTS)])
    assert report["windows"] == report["zero_us_windows"] == 2
    assert report["total_us"] == report["maximum_us"] == 0
    assert report["public_telemetry"] == {"windows": 2, "total_us": 2, "maximum_us": 1}
    assert not any(report["aggregate_us"].values()) and not any(report["longest_window_us"].values())
    assert read(-1) == read(layout.SLOTS) == -1
    assert state["tls"] is None


def test_fresh_overlay_can_attest_the_complete_compiler(tmp_path):
    from pcc.tools.runtime_archive_provenance import _runtime_emitter_source_identity
    from pcc.driver.bootstrap_cache_identity import bootstrap_source_files

    before = (ROOT / CORE).read_bytes()
    output = tmp_path / "overlay"
    make_overlay(ROOT, output)
    assert len(_runtime_emitter_source_identity(output)) == 64
    assert output / "scripts" / "bootstrap.py" in bootstrap_source_files(output)
    assert (output / CORE).read_bytes() != before
    assert (ROOT / CORE).read_bytes() == before
