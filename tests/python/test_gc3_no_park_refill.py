"""A full GC3 nursery must not start STW inside a no-park region."""
import ast
import os
from pathlib import Path
import subprocess

import pytest

from tests.owned_c_execution import compile_owned_c_with_runtime

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("depth", (0, 1, 2))
@pytest.mark.parametrize("live", (0, 1))
@pytest.mark.parametrize("used", (0, 48))
def test_minor_refill_preserves_no_park_and_uses_existing_fallback(depth, live, used):
    memory = {"pcc_gc_config_initialized": 1, "pcc_gc_backend_selected": 3,
              "pcc_gc_minor_alloc_max": 128, "pcc_gc_minor_arena_fallbacks": 0,
              "pcc_gc_minor_arena_bumps": 0, "pcc_gc_minor_bytes": 0, 132: live}
    state = {"used": used, "pending": None}
    events = []

    def add(address, value):
        memory[address] = memory.get(address, 0) + value
        return memory[address]

    def collect():
        assert depth == 0
        events.append("collect")
        state["used"] = 0

    namespace = {
        "null": lambda: None, "ptr_is_null": lambda value: value is None,
        "global_addr": lambda name: name, "load_i32": lambda p, o: memory[p],
        "ptr_add": lambda p, o: p + o, "_align16": lambda n: (n + 15) // 16 * 16,
        "_set_pending_minor_block": lambda block: state.__setitem__("pending", block),
        "_minor_current": lambda: 100, "_minor_block_used": lambda block: state["used"],
        "_minor_block_size": lambda block: 64, "_minor_block_live": lambda block: live,
        "_minor_block_owner": lambda block: 7, "pcc_current_thread_id": lambda: 7,
        "_set_minor_block_used": lambda block, count: state.__setitem__("used", count),
        "pcc_thread_no_park_depth": lambda: depth, "_minor_collect_reset": collect,
        "_minor_find_reusable_block": lambda count: 100,
        "_minor_new_block": lambda count: pytest.fail("unexpected new block"),
        "_minor_block_base": lambda block: 1000,
        "_atomic_i32_add_fetch": add,
        "_atomic_i64_add_fetch": add,
        "_atomic_i32_store": lambda p, value: memory.__setitem__(p, value),
        "_note_minor_alloc": lambda size: events.append(("allocated", size)),
        "memset": lambda p, value, size: events.append(("zero", p, size)),
    }
    path = ROOT / "pcc/runtime/py/py_gc_backend.py"
    node = next(node for node in ast.parse(path.read_text()).body
                if isinstance(node, ast.FunctionDef) and node.name == "pcc_gc_try_minor_alloc")
    node.decorator_list = []
    exec(compile(ast.Module([node], type_ignores=[]), str(path), "exec"), namespace)
    result = namespace["pcc_gc_try_minor_alloc"](32)
    if used == 48 and live and depth:
        assert result is None and state["pending"] is None
        assert state["used"] == used and events == []
        assert memory["pcc_gc_minor_arena_fallbacks"] == 1
    else:
        assert result == 1000 and state["used"] == 32
        assert state["pending"] == 100
        assert memory["pcc_gc_minor_arena_fallbacks"] == 0
        assert ("collect" in events) == bool(used == 48 and live)
        assert events[-1] == ("zero", 1000, 32)


@pytest.mark.parametrize("phase", (0, 1))
def test_frame_promotion_drops_graph_lock_before_stopped_stack_walk(phase):
    values = {
        "pcc_gc_backend3_frame_root_scan_phase": phase,
        "pcc_gc_backend3_frame_root_scan_slot": -1,
        "pcc_gc_frame_head": None,
        "pcc_gc_continuation_root_head": None,
        "pcc_gc_backend3_frame_root_scan_cursor": None,
        "pcc_gc_backend3_continuation_root_scan_cursor": None,
    }
    events = []
    state = {"locked": False}

    def lock():
        assert not state["locked"]
        state["locked"] = True
        events.append("lock")

    def unlock():
        assert state["locked"]
        state["locked"] = False
        events.append("unlock")

    def reset():
        assert state["locked"]
        events.append("reset")

    def walk(mode, resolve):
        assert not state["locked"], "STW must not wait while graph/no-park is held"
        assert (mode, resolve) == (2, 0)
        events.append("walk")

    def drain(budget):
        assert not state["locked"] and budget == 8
        events.append("drain")

    namespace = {
        "i64": int, "null": lambda: None, "ptr_is_null": lambda value: value is None,
        "global_addr": lambda name: name, "global_load_ptr": values.__getitem__,
        "global_store_ptr": values.__setitem__, "load_i32": lambda name, offset: values[name],
        "load_i64": lambda name, offset: values[name],
        "store_i32": lambda name, offset, value: values.__setitem__(name, value),
        "store_i64": lambda name, offset, value: values.__setitem__(name, value),
        "pcc_py_gc_minor_graph_lock": lock, "pcc_py_gc_minor_graph_unlock": unlock,
        "_reset_frame_root_scan": reset, "pcc_gc_visit_walked_stack_roots": walk,
        "pcc_gc_backend3_drain_promotion_worklist": drain,
    }
    path = ROOT / "pcc/runtime/py/freestanding_gc_generational_scheduler.py"
    node = next(node for node in ast.parse(path.read_text()).body
                if isinstance(node, ast.FunctionDef)
                and node.name == "pcc_gc_generational_promote_frame_roots")
    node.decorator_list = []
    exec(compile(ast.Module([node], type_ignores=[]), str(path), "exec"), namespace)
    namespace[node.name](8)
    assert events == ["lock", "reset", "unlock", "walk", "drain"]


PROBE = r'''
#include "py_runtime.h"
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
extern int32_t pcc_gc_minor_arena_fallbacks;
extern int32_t pcc_gc_minor_collections;
extern void pcc_gc_generational_promote_frame_roots(int64_t);
int main(int argc, char **argv) {
    enum { COUNT = 80 };
    if (argc != 2 || pcc_gc_set_backend(3) != 0) return 1;
    int graph = atoi(argv[1]);
    int32_t map = COUNT + 1;
    PyObject *roots[COUNT + 1] = {0};
    pcc_gc_frame_enter(&map, roots);
    int32_t before = __atomic_load_n(&pcc_gc_minor_arena_fallbacks, 2);
    if (graph) pcc_py_gc_minor_graph_lock(); else pcc_thread_no_park_enter();
    for (int i = 0; i < COUNT; i++) {
        roots[i] = py_str_new("held", 4);
        if (roots[i] == 0 || pcc_thread_no_park_depth() != 1) return 2;
    }
    if (__atomic_load_n(&pcc_gc_minor_arena_fallbacks, 2) <= before) return 3;
    if (graph) pcc_py_gc_minor_graph_unlock(); else pcc_thread_no_park_exit();
    if (pcc_thread_no_park_depth() != 0) return 4;
    int32_t collections = __atomic_load_n(&pcc_gc_minor_collections, 2);
    roots[COUNT] = py_str_new("next", 4);
    if (roots[COUNT] == 0) return 5;
    if (__atomic_load_n(&pcc_gc_minor_collections, 2) <= collections) return 6;
    pcc_gc_generational_promote_frame_roots(4096);
    for (int i = 0; i <= COUNT; i++) {
        int64_t token = pcc_gc_foreign_lease_acquire(&roots[i]);
        if (token < 0 || py_str_len(roots[i]) != 4) return 7;
        if (pcc_gc_foreign_lease_release(&roots[i], token) != 0) return 8;
        pcc_gc_store_root(&roots[i], NULL);
    }
    pcc_gc_frame_leave(roots);
    puts("NO_PARK_REFILL_OK");
    return 0;
}
'''


@pytest.mark.parametrize("graph", (0, 1), ids=("explicit-scope", "graph-lock"))
def test_native_small_nursery_allocates_without_suspending_owner(tmp_path, pcc_runtime_archive, graph):
    source = tmp_path / "refill.c"
    source.write_text(PROBE)
    output = tmp_path / "refill"
    compile_owned_c_with_runtime(source, output, runtime_archive=pcc_runtime_archive)
    run = subprocess.run([str(output), str(graph)], capture_output=True, text=True, timeout=25,
                         env=dict(os.environ, PATH="/nonexistent", PCC_GC_MINOR_HEAP_SIZE="256",
                                  PCC_GC_MINOR_ALLOC_MAX="128"))
    assert (run.returncode, run.stdout, run.stderr) == (0, "NO_PARK_REFILL_OK\n", "")
