"""Only tracing progress can pay GC4 allocation debt; moving work cannot."""
import ast
from pathlib import Path

import pytest

SOURCE = Path(__file__).resolve().parents[2] / "pcc/runtime/py/freestanding_gc_incremental_concurrent_scheduler.py"


@pytest.mark.parametrize("processed,live,marked,finished,expected", (
    (65536, 13314669, 700613, False, 6305650),
    (1, 13314669, 700613, False, 6305650),
    (0, 13314669, 700613, False, 6305650),
    (0, 700613, 700613, False, 0),
    (0, 500000, 700613, False, 0),
    (65536, 13314669, 700613, True, 0),
))
def test_gc4_does_not_credit_nonmarking_work(processed, live, marked, finished, expected):
    state = {
        "pcc_gc_backend_selected": 4,
        "pcc_gc_debt_bytes": 6305650,
        "pcc_gc_live_bytes": live,
        "pcc_gc_tracing_marked_bytes": marked,
        "pcc_gc_backend4_auto_step_pending": 1,
    }
    locks = []
    world = []

    def step(budget):
        assert budget == 65536 and locks == [] and world == ["stopped"]
        if finished:
            # The real pcc_gc_finish_tracing_cycle owns this reset.
            state["pcc_gc_debt_bytes"] = 0
        return processed

    def exchange(operation, name, offset, value, ordering):
        assert operation == "xchg"
        previous = state.get(name, 0)
        state[name] = value
        return previous

    namespace = {
        "i64": int, "global_addr": lambda name: name,
        "load_i32": lambda name, offset: state.get(name, 0),
        "load_i64": lambda name, offset: state.get(name, 0),
        "atomic_load_i64": lambda name, offset, order: state.get(name, 0),
        "store_i32": lambda name, offset, value: state.__setitem__(name, value),
        "atomic_store_i32": lambda name, offset, value, order: state.__setitem__(name, value),
        "atomic_rmw_i64": exchange,
        "pcc_thread_no_park_depth": lambda: 0,
        "pcc_py_gc_minor_graph_lock_depth": lambda: 0,
        "pcc_py_gc_minor_graph_lock": lambda: locks.append(1),
        "pcc_py_gc_minor_graph_unlock": lambda: locks.pop(),
        "pcc_gc_tracing_debt_threshold": lambda: 6305517,
        "pcc_gc_tracing_budget_from_debt": lambda: 65536,
        "pcc_stop_the_world": lambda: world.append("stopped") or 0,
        "pcc_resume_world": lambda: world.pop(),
        "pcc_gc_reclaim_retired_index_tables": lambda: None,
        "pcc_gc_step": step,
        "pcc_gc_tracing_discharge_debt": lambda count: pytest.fail("mixed GC4 work credited as tracing"),
    }
    tree = ast.parse(SOURCE.read_text())
    function = next(node for node in tree.body if isinstance(node, ast.FunctionDef)
                    and node.name == "pcc_gc_backend4_maybe_auto_step")
    function.decorator_list = []
    exec(compile(ast.Module(body=[function], type_ignores=[]), str(SOURCE), "exec"), namespace)
    namespace[function.name]()
    assert state["pcc_gc_debt_bytes"] == expected
    assert state["pcc_gc_backend4_auto_step_pending"] == int(expected >= 6305517)
    assert state["pcc_gc_in_auto_step"] == 0
    assert locks == [] and world == []
