"""Count-free moving steps protect raw roots from the current stopped world."""

import ast
from pathlib import Path

import pytest


SOURCE = Path(__file__).resolve().parents[2] / (
    "pcc/runtime/py/freestanding_gc_barrier_dispatcher.py"
)


@pytest.mark.parametrize(
    "backend,tracing,owned,stop_result,expected",
    (
        (0, 0, 0, 0, []),
        (1, 0, 0, 0, ["incremental"]),
        (2, 0, 0, 0, ["incremental"]),
        (3, 0, 0, 0, ["stop", "moving", "resume"]),
        (4, 0, 0, 0, ["stop", "moving", "resume"]),
        (4, 1, 0, 0, ["stop", "pins", "moving", "resume"]),
        (4, 1, 1, 0, ["pins", "moving"]),
        (4, 1, 0, 1, ["stop"]),
    ),
)
@pytest.mark.parametrize("explicit", (0, 1))
def test_step_refreshes_current_raw_roots_before_any_moving_work(
    backend, tracing, owned, stop_result, expected, explicit,
):
    events = []
    world = bool(owned)
    cells = {
        "pcc_gc_backend4_no_refcounts": tracing,
        "pcc_gc_explicit_collect_active": explicit,
        "pcc_gc_forwarding_population": 1,
    }

    def stop():
        nonlocal world
        events.append("stop")
        world = stop_result == 0
        return stop_result

    def refresh():
        assert world
        events.append("pins")

    def move(budget, *arguments):
        assert world
        if tracing:
            assert events[-1] == "pins"
        events.append("moving")
        return budget

    def resume():
        nonlocal world
        assert world
        world = False
        events.append("resume")

    namespace = {
        "i64": int,
        "_selected_backend": lambda: backend,
        "global_addr": lambda name: name,
        "load_i32": lambda name, offset: cells.get(name, 0),
        "load_i64": lambda name, offset: cells.get(name, 0),
        "store_i32": lambda name, offset, value: cells.__setitem__(name, value),
        "store_i64": lambda name, offset, value: cells.__setitem__(name, value),
        "pcc_thread_owns_stopped_world": lambda: int(world),
        "pcc_thread_live_count": lambda: 1,
        "pcc_stop_the_world": stop,
        "pcc_resume_world": resume,
        "pcc_gc_pin_conservative_thread_roots": refresh,
        "pcc_gc_precise_stack_diff_if_requested": lambda: None,
        "pcc_platform_monotonic_us": lambda: 10,
        "pcc_gc_tracing_record_pause": lambda start, end: None,
        "pcc_gc_incremental_concurrent_step": lambda budget: events.append("incremental") or budget,
        "pcc_gc_generational_step": move,
        "pcc_gc_backend4_step_remembered_roots": lambda budget: 0,
        "pcc_gc_backend3_drain_promotion_worklist": lambda budget: 0,
        "pcc_gc_tracing_step_cycle": lambda budget: 0,
        "_tracing_work_pending": lambda: 0,
        "pcc_gc_backend4_step_generation_aging": lambda budget: 0,
        "pcc_gc_backend4_evacuation_page_drain": move,
        "pcc_gc_backend4_remap_and_retire_stopped_world": lambda: move(1),
    }
    tree = ast.parse(SOURCE.read_text())
    function = next(node for node in tree.body
                    if isinstance(node, ast.FunctionDef) and node.name == "pcc_gc_step")
    function.decorator_list = []
    exec(compile(ast.Module(body=[function], type_ignores=[]), str(SOURCE), "exec"), namespace)
    processed = namespace[function.name](1)
    assert events == expected
    assert processed == (0 if backend == 0 or stop_result else 1)
    assert world == bool(owned)
