"""Only a GC4 cascade counts itself in the active-cascade word.

pcc_dealloc_with_trash added to pcc_dealloc_cascades_active when an
outermost death began and subtracted when it ended, with acq_rel ordering
on one word every carrier shares, for every collector. Only GC4's zpage
recycling reads the count (pcc_dealloc_cascade_active). A cascade now
counts itself only when it starts under GC4, and uncounts exactly what it
counted, so a collector switch inside a cascade cannot unbalance it.
"""
import ast
from pathlib import Path
import types

import pytest

RUNTIME = Path(__file__).resolve().parents[2] / "pcc/runtime/py/py_obj_dealloc.py"


def run(start_backend, end_backend=None, nested=False):
    words = {"pcc_dealloc_depth": 0, "pcc_dealloc_cascades_active": 0,
             "pcc_gc_backend_selected": start_backend}
    events = []
    tree = ast.parse(RUNTIME.read_text())
    node = next(node for node in tree.body
                if isinstance(node, ast.FunctionDef) and node.name == "pcc_dealloc_with_trash")
    node.decorator_list = []

    def rmw(op, name, offset, value, order):
        assert order == "acq_rel"
        events.append((op, name))
        old = words[name]
        words[name] = old + value if op == "add" else old - value
        return old

    def dispatch(o, tag):
        events.append(("dispatch", o))
        if end_backend is not None:
            words["pcc_gc_backend_selected"] = end_backend
        if nested and o == "outer":
            namespace["pcc_dealloc_with_trash"]("inner", tag)

    namespace = {
        "global_addr": lambda name: name,
        "load_i32": lambda name, offset: words[name],
        "store_i32": lambda name, offset, value: words.__setitem__(name, value),
        "atomic_rmw_i64": rmw,
        "_dealloc_should_defer": lambda tag: False,
        "_trash_enqueue": lambda o, tag: False,
        "_dealloc_dispatch": dispatch,
        "_trash_drain": lambda: events.append(("drain",)),
        "pcc_gc_backend": lambda: words["pcc_gc_backend_selected"],
        "_backend4_sweep_deferred_recycles": lambda: events.append(("sweep",)),
    }
    code = compile(ast.Module([node], type_ignores=[]), str(RUNTIME), "exec")
    for const in code.co_consts:
        if isinstance(const, types.CodeType):
            namespace[const.co_name] = types.FunctionType(const, namespace)
    namespace["pcc_dealloc_with_trash"]("outer", 7)
    assert words["pcc_dealloc_depth"] == 0
    return events, words["pcc_dealloc_cascades_active"]


@pytest.mark.parametrize("backend", (0, 1, 2, 3))
def test_other_collectors_leave_the_shared_word_alone(backend):
    events, active = run(backend, nested=True)
    assert events == [("dispatch", "outer"), ("dispatch", "inner"), ("drain",)]
    assert active == 0


def test_a_gc4_cascade_counts_once_and_sweeps_when_it_was_the_last():
    events, active = run(4, nested=True)
    assert events == [("add", "pcc_dealloc_cascades_active"), ("dispatch", "outer"),
                      ("dispatch", "inner"), ("drain",), ("sub", "pcc_dealloc_cascades_active"),
                      ("sweep",)]
    assert active == 0


@pytest.mark.parametrize("start,end", ((0, 4), (4, 0)))
def test_a_switch_inside_a_cascade_keeps_the_count_balanced(start, end):
    events, active = run(start, end)
    assert active == 0
    assert events.count(("add", "pcc_dealloc_cascades_active")) == events.count(
        ("sub", "pcc_dealloc_cascades_active"))
