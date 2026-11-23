"""A same-stop negative generator hint cannot suppress an observer later."""
import ast
from pathlib import Path
import pytest

SOURCE = (Path(__file__).resolve().parents[2] / "pcc/runtime/py/freestanding_gc_tracing_sweep_collector.py")


@pytest.mark.parametrize(
    "finish_epoch,hint,classes,weak,pending,visits,builder",
    [
        (41, 17, 0, False, False, [], []),  # Valid negative: no second walk.
        (41, 16, 0, False, True, [201], [41]),  # Stale commit.
        (40, 17, 0, False, True, [201], [41]),  # World ran after the mark.
        (41, 17, 1, False, True, [201], [41]),  # Class may run __del__.
        (41, 17, 0, True, True, [], [41]),  # A registered weakref still matters.
    ],
)
def test_observer_census_requires_current_stop_and_no_observers(
    finish_epoch, hint, classes, weak, pending, visits, builder,
):
    counters = {
        "pcc_gc_tracing_candidate_commit": 17,
        "pcc_gc_tracing_finish_commits": 17,
        "pcc_gc_tracing_finish_stop_epoch": finish_epoch,
        "pcc_gc_tracing_candidate_no_pending_gen_commit": hint,
        "pcc_class_del_defined_count": classes,
    }
    observed = []
    planned = []
    env = {
        "i64": int,
        "ptr_is_null": lambda p: p == 0,
        "global_addr": lambda key: key,
        "global_load_ptr": lambda key: int(weak and key == "py_weakref_head"),
        "load_i32": lambda p, _off: counters.get(p, 15 if p == 301 else 0),
        "load_i64": lambda p, _off: counters.get(p, 0),
        "pcc_gc_gray_count_load_acquire": lambda: 0,
        "null": lambda: 0,
        "pcc_gc_object_node_is_active": lambda node: observed.append(node) or 1,
        "load_ptr": lambda p, off: {101: {0: 201, 8: 202}, 201: {0: 301}, 202: {0: 302}}[p][off],
        "abi_constant": {"object.header.type_tag_offset": 8, "object.type.gen": 15}.__getitem__,
        "pcc_gc_tracing_is_sweep_candidate": lambda _obj: 1,
        "py_user_del_pending": lambda _obj: int(pending),
        "pcc_gc_callback_layers_build": lambda _nodes, _count, epoch: planned.append(epoch) or 501,
    }
    parsed = ast.parse(SOURCE.read_text())
    function = next(
        n for n in parsed.body if isinstance(n, ast.FunctionDef) and n.name == "_review_layer_plan"
    )
    function.decorator_list = []
    exec(compile(ast.Module(body=[function], type_ignores=[]), str(SOURCE), "exec"), env)
    assert env["_review_layer_plan"](41, 3, 101, 2, 41) == (501 if builder else 0)
    assert (observed, planned) == (visits, builder)
