"""Count-free moving steps protect raw roots from the current stopped world."""

import ast
import os
import subprocess
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
        # No evacuation page is pending; the edge alone still asks for pins.
        "global_load_ptr": lambda name: cells.get(name, 0),
        "ptr_is_null": lambda value: int(value == 0),
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


RAW_ADDRESS_PROBE = r'''
extern void *py_list_new(long);
extern long py_list_len(void *);
extern void *pcc_gc_object_index_find(void *);
extern void *pcc_gc_forwarding_index_find(void *);
extern long pcc_gc_select_relocation_set(long);
extern long pcc_gc_relocation_set_contains(void *);
extern long pcc_gc_cms_stop_worker(void);
extern long pcc_gc_step(long);

long raw_address_step_probe(void) {
    void *volatile held = py_list_new(0);
    if (!held) return 10;
    /* Isolate explicit moving work from the background collector. */
    pcc_gc_cms_stop_worker();
    long *node = (long *)pcc_gc_object_index_find(held);
    if (!node) return 11;
    /* This raw address was not in the previous mark's conservative set. */
    node[7] = 0;
    if (pcc_gc_select_relocation_set(65536) <= 0) return 12;
    if (pcc_gc_relocation_set_contains(held) != 1) return 13;
    pcc_gc_step(65536);
    /* No read barrier can rewrite the volatile C local. Check raw identity
       before accessing the object or collector-owned node again. */
    if (pcc_gc_forwarding_index_find(held)) return 14;
    node = (long *)pcc_gc_object_index_find(held);
    if (!node || node[7] != 1) return 15;
    if (py_list_len(held) != 0) return 16;
    return 0;
}
'''


@pytest.mark.integration
def test_current_c_stack_root_prevents_selected_object_from_moving(
    tmp_path, pcc_runtime_archive,
):
    from pcc.backend.owned_object_emit import emit_owned_object
    from pcc.frontends.python.pipeline import compile_python
    from pcc.frontends.python.pipeline_targets import host_target_triple
    from tests.python.test_owned_external_tls import _c_ir

    target = host_target_triple()
    helper = tmp_path / "raw_address.o"
    helper.write_bytes(emit_owned_object(_c_ir(RAW_ADDRESS_PROBE, target), target))
    source = tmp_path / "raw_address.py"
    source.write_text('''from pcc.extern import c_int64, extern
probe = extern("raw_address_step_probe", (), c_int64)
def main():
    print(probe())
main()
''')
    binary = tmp_path / "raw_address"
    compile_python(str(source), str(binary), backend="self",
                   runtime_archive=str(pcc_runtime_archive), object_inputs=(str(helper),))
    # Suppress automatic pacing only to expose a selected, not-yet-marked
    # object deterministically. The test executes the real explicit GC step.
    environment = dict(os.environ, PATH="/nonexistent", PCC_GC_BACKEND="4",
                       PCC_GC4_TRACING_ONLY="1", PCC_GC_DEBT_THRESHOLD="1099511627776")
    result = subprocess.run([str(binary)], capture_output=True, text=True,
                            timeout=30, env=environment)
    assert (result.returncode, result.stdout, result.stderr) == (0, "0\n", "")
