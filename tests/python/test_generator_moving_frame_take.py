"""Private moving-frame take: one transaction, with real relocation witnesses.

The source checks are structural guards, not native acceptance. The two C
controls consume an admitted current runtime and fail if requested relocation
did not occur. The Python control compares finalizer/weakref/resurrection
behavior with CPython under all five collectors.
"""

import ast
import os
from pathlib import Path
import sys

import pytest

from tests.owned_runtime_c_fixture import link_c_harness
from tests.python.process_timeout import run_process_group_timeout


ROOT = Path(__file__).resolve().parents[2]
RUNTIME = ROOT / "pcc/runtime"


def _function(module, name):
    source = (RUNTIME / "py" / module).read_text()
    node = next(node for node in ast.parse(source).body
                if isinstance(node, ast.FunctionDef) and node.name == name)
    return node, ast.get_source_segment(source, node)


def test_take_has_its_own_no_poll_entry_and_does_not_enlarge_getters():
    _, wrapper = _function("py_list_native_entry.py", "py_list_take_for_gen_frame")
    assert wrapper.index("pcc_thread_no_park_enter()") < wrapper.index("store_ptr(inputs, 0, lst)")
    assert wrapper.index("pcc_gc_native_root_bind_range(inputs, -1)") < wrapper.index("pcc_thread_no_park_exit()")
    assert wrapper.index("pcc_thread_no_park_exit()") < wrapper.index("result = pcc_list_body_py_list_take_for_gen_frame")
    assert "py_list_native_entry" in (ROOT / "pcc/frontends/python/owned_runtime_build.py").read_text()

    getter, text = _function("py_list.py", "_list_get_owned_rooted")
    assert [arg.arg for arg in getter.args.args] == ["lst", "i", "raising"]
    assert "stack_alloc(128)" not in text
    setter, text = _function("py_list.py", "_list_set_item_transaction")
    assert [arg.arg for arg in setter.args.args] == ["lst", "i", "item"]
    assert text.count("stack_alloc(128)") == 1


def test_take_rechecks_admission_after_lock_and_keeps_rooted_finish():
    _, body = _function("py_list.py", "_native_body_py_list_take_for_gen_frame")
    locked = body.index("pcc_py_gc_minor_graph_lock()")
    for gate in ("pcc_gc_backend_selected", "pcc_gc_backend4_no_refcounts",
                 "pcc_log_init_state", "pcc_diagnostics_runtime_log_fast_state",
                 "pcc_gc_refcount_provenance_probe"):
        assert body.index(gate) > locked
    assert body.index("pcc_gc_native_root_bind_range(result_slot, 1)") < locked
    publish = body.index("store_ptr(result_slot, 0, value)")
    commit = body.index("pcc_gc_store_ptr_plan_commit_locked")
    unlock = body.index("pcc_py_gc_minor_graph_unlock()")
    assert locked < publish < commit < unlock
    assert unlock < body.index("pcc_gc_retain_plan_finish") < body.index("pcc_gc_store_ptr_plan_finish")
    assert "tag == PY_TYPE_CPY_HANDLE or tag >= PY_TYPE_CEXT_TAG_BASE" in body
    assert "return _list_get_finish(result_slot, result_handle, list_handle_slot)" in body
    _, dispatch = _function("py_gen.py", "py_gen_frame_take")
    branch = dispatch.split("if _frame_list_raw() == 0:", 1)[1]
    assert "index >= 0" in branch and "pcc_log_init_state" in branch
    assert "value = py_list_get(frame, index)\n        py_list_set(frame, index, null())" in branch


SHAPES = r'''
#include "py_internal.h"
#include <stdio.h>
#include <stdlib.h>
extern int32_t pcc_log_init_state;
extern int32_t pcc_diagnostics_runtime_log_fast_state;
extern int32_t pcc_gc_refcount_provenance_probe;
static int foreign_token;
static void foreign_release(void *value) { (void)value; }
int main(void) {
    int backend = pcc_gc_backend();
    PyObject *roots[4] = {NULL, NULL, NULL, NULL};
    int32_t map[1] = {4};
    pcc_gc_frame_enter(map, roots);
    roots[0] = py_gen_frame_new(4);
    roots[1] = py_list_new(0);
    if (!roots[0] || !roots[1]) return 1;
    py_gen_frame_set(roots[0], 0, NULL);
    if (py_gen_frame_take(roots[0], 0) != NULL) return 2;
    roots[2] = py_int_from_i64(42);
    py_gen_frame_set(roots[0], 0, roots[2]);
    pcc_gc_store_root(&roots[2], NULL);
    roots[2] = py_gen_frame_take(roots[0], 0);
    int overflow = 0;
    if (py_int_to_i64(roots[2], &overflow) != 42 || overflow) return 3;
    pcc_gc_store_root(&roots[2], NULL);
    if (py_gen_frame_get(roots[0], 0) != NULL) return 4;
    py_gen_frame_set(roots[0], 0, roots[1]);
    roots[2] = py_gen_frame_take(roots[0], 0);
    if (roots[2] != roots[1] || py_gen_frame_get(roots[0], 0) != NULL) return 5;
    if (py_header(roots[2])->flags & PY_FLAG_GC_PINNED) return 6;
    pcc_gc_store_root(&roots[2], NULL);
    pcc_gc_pin(roots[1]);
    py_gen_frame_set(roots[0], 0, roots[1]);
    roots[2] = py_gen_frame_take(roots[0], 0);
    if (roots[2] != roots[1] || !(py_header(roots[2])->flags & PY_FLAG_GC_PINNED)) return 7;
    pcc_gc_unpin(roots[2]);
    pcc_gc_store_root(&roots[2], NULL);
    py_gen_frame_set(roots[0], 3, roots[1]);
    if (py_gen_frame_take(roots[0], 4) != NULL) return 8;
    roots[2] = py_gen_frame_take(roots[0], -1);
    if (roots[2] != roots[1]) return 9;
    pcc_gc_store_root(&roots[2], NULL);
    roots[2] = py_gen_frame_get(roots[0], 3);
    /* The existing nonmoving negative-index path copies but does not clear. */
    if ((backend <= 2 && roots[2] != roots[1]) || (backend >= 3 && roots[2] != NULL)) return 10;
    pcc_gc_store_root(&roots[2], NULL);
    py_cpy_handle_set_release_fn(foreign_release);
    roots[3] = py_cpy_handle_new(&foreign_token);
    if (!roots[3]) return 11;
    py_gen_frame_set(roots[0], 1, roots[3]);
    roots[2] = py_gen_frame_take(roots[0], 1);
    if (roots[2] != roots[3] || py_cpy_handle_get(roots[2]) != &foreign_token) return 12;
    if (py_gen_frame_get(roots[0], 1) != NULL) return 13;
    /* Ordinary startup must initialize logging without writing its gates. */
    const char *log_mode = getenv("PCC_LOG");
    if (backend == 4 && (!log_mode || log_mode[0] == 0)
            && pcc_gc_refcount_provenance_probe == 0) {
        if (pcc_log_init_state != 2 || pcc_diagnostics_runtime_log_fast_state != 0) return 16;
    }
    if (pcc_gc_set_backend(backend) != 0) return 14;
    if (pcc_gc_set_backend((backend + 1) % 5) == 0) return 15;
    for (int i = 0; i < 4; i++) pcc_gc_store_root(&roots[i], NULL);
    pcc_gc_frame_leave(roots);
    py_gc_collect();
    puts("FRAME_TAKE_SHAPES_OK");
    return 0;
}
'''


RELOCATION = r'''
#include "py_internal.h"
#include <stdio.h>
extern PyObject *pcc_gc_generational_oldify_copy(PyObject *);
extern int32_t pcc_log_init_state;
extern int32_t pcc_diagnostics_runtime_log_fast_state;
extern int32_t pcc_gc_forwarding_population;
int main(void) {
    int backend = pcc_gc_backend();
    if (backend != 3 && backend != 4) return 1;
    py_gc_disable();
    PyObject *roots[3] = {NULL, NULL, NULL};
    int32_t map[1] = {3};
    pcc_gc_frame_enter(map, roots);
    roots[0] = py_gen_frame_new(4);
    roots[1] = backend == 3 ? py_bytes_new("moved", 5) : py_list_new(0);
    if (!roots[0] || !roots[1]) return 2;
    py_gen_frame_set(roots[0], 0, roots[1]);
    pcc_gc_unpin(roots[0]);
    pcc_gc_unpin(roots[1]);
    PyObject *source_frame = roots[0];
    PyObject *source_value = roots[1];
    PyObject *moved_frame = source_frame;
    PyObject *moved_value = NULL;
    if (backend == 3) {
        pcc_py_gc_minor_graph_lock();
        moved_value = pcc_gc_generational_oldify_copy(source_value);
        pcc_py_gc_minor_graph_unlock();
    } else {
        pcc_gc_reset_relocation_set();
        if (pcc_gc_select_relocation_set(128) <= 0) return 3;
        if (!pcc_gc_relocation_set_contains(source_frame) || !pcc_gc_relocation_set_contains(source_value)) return 4;
        moved_value = pcc_gc_relocate_copy(source_value, sizeof(PyListObject));
        moved_frame = pcc_gc_relocate_copy(source_frame, sizeof(PyListObject));
        if (!moved_frame || moved_frame == source_frame) return 5;
    }
    if (!moved_value || moved_value == source_value) return 6;
    if (pcc_log_init_state != 2 || pcc_diagnostics_runtime_log_fast_state != 0) return 14;
    if (pcc_gc_forwarding_population <= 0) return 15;
    /* A real forwarding source enters the public take, not a mock root. */
    pcc_gc_telemetry_reset();
    roots[2] = py_gen_frame_take(source_frame, 0);
    int64_t fused_reads = pcc_gc_telemetry(PCC_GC_COUNTER_READ_BARRIERS);
    if (roots[2] != moved_value || (py_header(roots[2])->flags & PY_FLAG_GC_PINNED)) return 7;
    if (pcc_gc_load_ptr(NULL, &roots[0]) != moved_frame) return 8;
    if (pcc_gc_load_ptr(NULL, &roots[1]) != moved_value) return 9;
    if (py_gen_frame_get(roots[0], 0) != NULL) return 10;
    if (backend == 3 && py_bytes_len(roots[2]) != 5) return 11;
    if (backend == 4 && py_list_len(roots[2]) != 0) return 12;
    /* Read barriers are observable only with forwarding/relocation active.
     * Compare after the witnessed copy, never in an idle no-forwarding run.
     * This proves the admitted public call executes fusion, not its fallback;
     * it is not a timing or throughput claim. */
    pcc_gc_store_root(&roots[2], NULL);
    py_gen_frame_set(roots[0], 0, roots[1]);
    if (pcc_gc_forwarding_population <= 0) return 16;
    pcc_gc_telemetry_reset();
    roots[2] = py_list_get(roots[0], 0);
    py_list_set(roots[0], 0, NULL);
    int64_t split_reads = pcc_gc_telemetry(PCC_GC_COUNTER_READ_BARRIERS);
    if (fused_reads <= 0 || fused_reads >= split_reads) {
        fprintf(stderr, "fused=%lld split=%lld\n", (long long)fused_reads, (long long)split_reads);
        return 17;
    }
    pcc_gc_pin(roots[2]);
    py_gen_frame_set(roots[0], 0, roots[2]);
    pcc_gc_store_root(&roots[2], NULL);
    roots[2] = py_gen_frame_take(roots[0], 0);
    if (roots[2] != moved_value || !(py_header(roots[2])->flags & PY_FLAG_GC_PINNED)) return 13;
    pcc_gc_unpin(roots[2]);
    for (int i = 0; i < 3; i++) pcc_gc_store_root(&roots[i], NULL);
    pcc_gc_frame_leave(roots);
    puts("FRAME_TAKE_RELOCATED_OK");
    /* This is a deterministic relocation witness, not a leak/full-cycle gate. */
    return 0;
}
'''


def _native(tmp_path, archive, name, source):
    path = tmp_path / (name + ".c")
    path.write_text(source)
    executable = tmp_path / name
    link_c_harness(path, executable, runtime_archive=archive,
                   extra_include_dirs=(str(RUNTIME / "src"),))
    return executable


def _run(executable, backend, **extra):
    env = dict(os.environ, PCC_GC_BACKEND=str(backend), PCC_LOG="",
               PCC_GC_REFCOUNT_PROVENANCE_PROBE="0")
    env.update(extra)
    return run_process_group_timeout(
        [str(executable)], timeout=20, env=env,
    )


@pytest.mark.parametrize("phase", ("graph_lock", "graph_unlock", "retain_finish"))
@pytest.mark.parametrize("backend", (3, 4))
@pytest.mark.parametrize("prior_pin", (False, True))
def test_source_take_reloads_movable_roots_at_each_park_boundary(phase, backend, prior_pin):
    # Supplement the native witnesses with deterministic relocation at each
    # park boundary. This executes the production functions, but is a model.
    from tests.python.test_runtime_getter_roots import _GetterMemory

    memory = _GetterMemory(backend=backend, phase=phase, prior_pin=prior_pin)
    ns = memory.namespace
    memory.global_cell("pcc_log_init_state").fields[0] = 2
    memory.global_cell("pcc_gc_backend4_no_refcounts").fields[0] = 1
    ns["atomic_load_i32"] = lambda slot, offset, order: memory.read(slot, offset)
    ns["pcc_list_body_py_list_take_for_gen_frame"] = ns["_native_body_py_list_take_for_gen_frame"]
    commits = []

    def commit(plan, owner, slot, value):
        assert memory.depth > 0 and value is None
        old = memory.read(*memory.pointer(slot))
        assert old is memory.item
        assert any(memory.read(*memory.pointer(root)) is old
                   for root in memory.handles.values())
        # Logical ownership model: result retain precedes clearing the cell.
        assert old.references == 2
        old.references -= 1
        memory.write(*memory.pointer(slot), None)
        commits.append(owner)
        return 1

    ns["pcc_gc_store_ptr_plan_init"] = lambda plan, owner, backend: None
    ns["pcc_gc_store_ptr_plan_commit_locked"] = commit
    ns["pcc_gc_store_ptr_plan_finish"] = lambda plan: None
    result = ns["py_list_take_for_gen_frame"](memory.lst, 0)
    assert result is memory.item and result.alive and result.references == 1
    assert memory.items.fields[0] is None and len(commits) == 1
    assert memory.moves > 0
    assert bool(result.flags & ns["PY_FLAG_GC_PINNED"]) == prior_pin
    assert not memory.handles and not memory.native_frames and not memory.native_ranges


@pytest.mark.integration
def test_native_take_shapes_and_diagnostic_fallbacks(tmp_path, pcc_runtime_archive):
    executable = _native(tmp_path, pcc_runtime_archive, "frame_shapes", SHAPES)
    for backend in range(5):
        ran = _run(executable, backend)
        assert (ran.returncode, ran.stdout, ran.stderr) == (0, "FRAME_TAKE_SHAPES_OK\n", ""), backend
    for backend in (3, 4):
        for override in ({"PCC_GC_REFCOUNT_PROVENANCE_PROBE": "1"},
                         {"PCC_LOG": "gc", "PCC_LOG_FILE": str(tmp_path / f"gc{backend}.log")}):
            env = dict(os.environ, PCC_GC_BACKEND=str(backend), PCC_LOG="",
                       PCC_GC_REFCOUNT_PROVENANCE_PROBE="0")
            env.update(override)
            ran = run_process_group_timeout([str(executable)], timeout=20, env=env)
            assert (ran.returncode, ran.stdout) == (0, "FRAME_TAKE_SHAPES_OK\n"), (backend, ran.stderr)


@pytest.mark.integration
def test_native_take_after_gc3_oldification_and_gc4_owner_value_relocation(tmp_path, pcc_runtime_archive):
    executable = _native(tmp_path, pcc_runtime_archive, "frame_relocation", RELOCATION)
    for backend in (3, 4):
        ran = _run(executable, backend, PCC_GC_MINOR_ALLOC_MAX="4096")
        assert (ran.returncode, ran.stdout, ran.stderr) == (0, "FRAME_TAKE_RELOCATED_OK\n", ""), backend


FINALIZERS = '''import gc
import weakref
events = []
rescued = []
class Token:
    def __init__(self, label, rescue):
        self.label = label
        self.rescue = rescue
    def __del__(self):
        events.append(self.label)
        if self.rescue:
            rescued.append(self)
        gc.collect()
def callback_a(ref):
    events.append("weak-a")
    gc.collect()
def callback_b(ref):
    events.append("weak-b")
    gc.collect()
def values():
    a = Token("a", False)
    b = Token("b", True)
    yield weakref.ref(a, callback_a), weakref.ref(b, callback_b)
    a = None
    gc.collect()
    yield None
    b = None
    gc.collect()
    yield None
def exercise():
    g = values()
    watches = next(g)
    gc.collect()
    assert events == []
    next(g)
    for i in range(4):
        gc.collect()
    assert sorted(events) == ["a", "weak-a"]
    assert watches[0]() is None and watches[1]() is not None
    next(g)
    for i in range(4):
        gc.collect()
    assert sorted(events) == ["a", "b", "weak-a"]
    assert len(rescued) == 1 and watches[1]() is rescued[0]
    rescued.clear()
    g.close()
    for i in range(4):
        gc.collect()
    assert watches[1]() is None
    assert sorted(events) == ["a", "b", "weak-a", "weak-b"]
exercise()
print("FRAME_TAKE_FINALIZERS_OK")
'''


@pytest.mark.integration
def test_resumed_take_preserves_finalizers_weakrefs_and_resurrection(tmp_path, monkeypatch, pcc_runtime_archive):
    from pcc.frontends.python.pipeline import compile_python

    path = tmp_path / "frame_finalizers.py"
    path.write_text(FINALIZERS)
    reference = run_process_group_timeout([sys.executable, str(path)], timeout=20)
    assert (reference.returncode, reference.stdout, reference.stderr) == (0, "FRAME_TAKE_FINALIZERS_OK\n", "")
    # Explicit list frames make GC0/1/2 useful controls for the same program.
    monkeypatch.setenv("PCC_INLINE_GENERATOR_FRAMES", "0")
    executable = tmp_path / "frame_finalizers"
    compile_python(str(path), str(executable), backend="self",
                   ir_scaffold_mode="on", runtime_archive=str(pcc_runtime_archive))
    for backend in range(5):
        ran = _run(executable, backend)
        assert (ran.returncode, ran.stdout, ran.stderr) == (0, reference.stdout, ""), backend
