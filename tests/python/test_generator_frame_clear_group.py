"""Bounded terminal-frame clear primitive, not compiler activation.

Contract: docs/project-intent.md, five-GC roots and Python finalization.
Model tests execute production StorePlans and control individual poll edges;
only the native fixtures witness runtime admission and real relocation.
"""
import pytest

from tests.python.test_generator_frame_take_group import BatchMemory
from tests.python.test_generator_frame_take_protocol import _plan_code
from tests.python.test_generator_moving_frame_take import _function, _native, _run
from tests.python.test_runtime_getter_roots import _Slots, abi


def test_clear_entry_is_no_poll_and_keeps_native_root_until_all_finishes():
    _, wrapper = _function("py_list_native_entry.py", "py_gen_frame_clear_group")
    setup = wrapper.index("pcc_thread_no_park_enter()")
    for gate in ("pcc_gc_config_initialized", "pcc_gc_backend_frozen",
                 "pcc_gc_backend_selected", "pcc_gc_backend4_no_refcounts",
                 "pcc_log_init_state", "pcc_diagnostics_runtime_log_fast_state",
                 "pcc_gc_refcount_provenance_probe"):
        assert wrapper.index(gate) < setup
    assert "pcc_gc_backend()" not in wrapper
    assert setup < wrapper.index("store_ptr(inputs, 0, lst)")
    assert wrapper.index("pcc_gc_native_root_bind_range(inputs, -1)") < wrapper.index("pcc_thread_no_park_exit()")
    assert wrapper.index("pcc_thread_no_park_exit()") < wrapper.index("cleared: int =")
    assert wrapper.index("pcc_gc_native_frame_leave(frame)") < wrapper.rindex("return cleared")
    _, body = _function("py_list.py", "_native_body_py_gen_frame_clear_group")
    lock, unlock = body.index("pcc_py_gc_minor_graph_lock()"), body.index("pcc_py_gc_minor_graph_unlock()")
    assert body.index("memset(store_plans, 0, 2048)") < lock
    assert lock < body.index("pcc_gc_backend_frozen") < body.index("pcc_gc_load_ptr")
    assert body.index("start <= length - count") < body.index("pcc_gc_store_ptr_plan_init")
    assert body.index("pcc_gc_store_ptr_plan_commit_locked") < unlock < body.index("pcc_gc_store_ptr_plan_finish")
    assert "return " not in body[lock:unlock]


class ClearMemory(BatchMemory):
    def __init__(self, count=16, backend=4, phase="none", prior_pin=False):
        super().__init__(count=count, backend=backend, phase=phase)
        self.global_cell("pcc_gc_backend_frozen").fields[0] = 1
        if prior_pin:
            self.lst.flags |= abi.PY_FLAG_GC_PINNED
        self.none = self.make(abi.PY_TYPE_NONE, abi.PY_FLAG_IMMORTAL)
        self.global_cell("py_None").fields[0] = self.none
        self.values = [None if i % 3 == 0 else self.none if i % 3 == 1 else 2 * i + 1
                       for i in range(count)]
        self.items.fields.update({i * 8: value for i, value in enumerate(self.values)})
        ns = self.namespace
        # Restore the actual StorePlan functions, replacing take-specific hooks.
        exec(_plan_code(), ns)
        commit = ns["pcc_gc_store_ptr_plan_commit_locked"]
        finish = ns["pcc_gc_store_ptr_plan_finish"]
        ns["global_load_ptr"] = lambda name: self.read(self.global_cell(name), 0)
        ns["_gc_relocation_candidate"] = lambda value: value is not self.none
        def barrier(owner, slot, value):
            assert self.depth > 0 and owner is self.lst and value is self.none
            self.events.append("barrier")
        ns["pcc_gc_note_slot_write_barrier"] = barrier
        ns["pcc_gc_backend4_note_heap_store_locked"] = barrier
        def checked_commit(plan, owner, slot, value):
            assert self.depth > 0 and owner is self.lst and value is self.none
            self.events.append("commit")
            return commit(plan, owner, slot, value)
        def checked_finish(plan):
            assert self.depth == 0 and self.native_frames
            self.gc("store_finish")
            finish(plan)
            assert self.read(plan, 124) == 7
            self.events.append("finish")
        body = ns["_native_body_py_gen_frame_clear_group"]
        def body_entry(*args):
            self.gc("body_entry")
            return body(*args)
        ns.update(pcc_list_body_py_gen_frame_clear_group=body_entry,
                  pcc_gc_store_ptr_plan_commit_locked=checked_commit,
                  pcc_gc_store_ptr_plan_finish=checked_finish)

    def run(self, start=0, count=None, frame=None):
        return self.namespace["py_gen_frame_clear_group"](
            self.lst if frame is None else frame, start,
            self.count if count is None else count,
        )


@pytest.mark.parametrize("backend", (3, 4))
@pytest.mark.parametrize("count", (2, 15, 16))
@pytest.mark.parametrize("phase", ("no_park_exit", "body_entry", "graph_lock", "graph_unlock", "store_finish"))
@pytest.mark.parametrize("prior_pin", (False, True))
def test_clear_real_plans_keep_root_and_publish_each_none_with_barrier(backend, count, phase, prior_pin):
    memory = ClearMemory(count=count, backend=backend, phase=phase, prior_pin=prior_pin)
    memory.error = sentinel = object()
    assert memory.run() == 1
    assert memory.moves > 0
    assert all(memory.items.fields[cell * 8] is memory.none for cell in range(count))
    assert memory.events.count("commit") == count
    assert memory.events.count("store-note") == count
    assert memory.events.count("barrier") == count
    assert memory.events.count("finish") == count
    assert memory.events.index("finish") > max(i for i, value in enumerate(memory.events) if value == "commit")
    assert bool(memory.lst.flags & abi.PY_FLAG_GC_PINNED) == prior_pin
    assert memory.error is sentinel
    memory.assert_clean()


@pytest.mark.parametrize("gate,after_entry", [(name, later)
    for name in ("config", "frozen", "no_rc", "log_init", "logging", "probe")
    for later in (False, True) if not (name == "config" and later)])
def test_policy_miss_is_all_or_nothing_even_after_entry_poll(gate, after_entry):
    memory = ClearMemory()
    name, value = {"config": ("pcc_gc_config_initialized", 0),
                   "frozen": ("pcc_gc_backend_frozen", 0),
                   "no_rc": ("pcc_gc_backend4_no_refcounts", 0),
                   "log_init": ("pcc_log_init_state", 0),
                   "logging": ("pcc_diagnostics_runtime_log_fast_state", 1),
                   "probe": ("pcc_gc_refcount_provenance_probe", 1)}[gate]
    if after_entry:
        enter = memory.namespace["pcc_list_body_py_gen_frame_clear_group"]
        def changed(*args):
            memory.global_cell(name).fields[0] = value
            return enter(*args)
        memory.namespace["pcc_list_body_py_gen_frame_clear_group"] = changed
    else:
        memory.global_cell(name).fields[0] = value
    assert memory.run() == 0
    assert not memory.events and list(memory.items.fields.values()) == memory.values
    memory.assert_clean()


@pytest.mark.parametrize("start,count", ((-1, 2), (0, 0), (0, 1), (0, 17), (15, 2), (2**63 - 1, 2)))
def test_invalid_group_never_consumes_valid_prefix(start, count):
    memory = ClearMemory()
    assert memory.run(start, count) == 0
    assert not memory.events and list(memory.items.fields.values()) == memory.values
    memory.assert_clean()


@pytest.mark.parametrize("backend", (0, 1, 2))
def test_other_collectors_leave_original_serial_protocol_to_caller(backend):
    memory = ClearMemory(backend=backend)
    assert memory.run() == 0
    assert not memory.events and list(memory.items.fields.values()) == memory.values
    memory.assert_clean()


@pytest.mark.parametrize("shape", ("null", "tagged", "nonlist", "inline", "deallocating", "capacity", "items", "length"))
def test_malformed_or_other_frame_shape_is_unchanged(shape):
    memory = ClearMemory()
    frame = memory.lst
    if shape == "null":
        frame = None
    elif shape == "tagged":
        frame = 85
    elif shape in ("nonlist", "inline"):
        frame.tag = abi.PY_TYPE_STR if shape == "nonlist" else abi.PY_TYPE_GEN
        if shape == "inline":
            frame.flags |= abi.PY_FLAG_GEN_INLINE_FRAME
    elif shape == "deallocating":
        frame.flags |= abi.PY_FLAG_GC_DEALLOCATING
    elif shape == "capacity":
        frame.fields[abi.PYLISTOBJECT_CAPACITY_OFFSET] = 1
    elif shape == "items":
        frame.fields[abi.PYLISTOBJECT_ITEMS_OFFSET] = None
    elif shape == "length":
        frame.fields[abi.PYLISTOBJECT_LENGTH_OFFSET] = -1
    assert memory.namespace["py_gen_frame_clear_group"](frame, 0, 16) == 0
    assert not memory.events and list(memory.items.fields.values()) == memory.values
    memory.assert_clean()


def test_null_none_and_tagged_old_cells_do_not_bypass_real_store_plans():
    memory = ClearMemory(count=3)
    memory.items.fields[0] = None
    memory.items.fields[8] = memory.none
    memory.items.fields[16] = 85
    assert memory.run() == 1
    assert memory.events.count("commit") == memory.events.count("finish") == 3
    assert all(memory.items.fields[i * 8] is memory.none for i in range(3))
    memory.assert_clean()


@pytest.mark.parametrize("shape", ("heap", "foreign", "immortal", "transitive"))
@pytest.mark.parametrize("position", (0, 15))
def test_any_heap_owner_rejects_the_entire_range_without_prefix_writes(shape, position):
    memory = ClearMemory()
    owner = memory.make(abi.PY_TYPE_LIST)
    if shape == "foreign":
        owner.tag = abi.PY_TYPE_CPY_HANDLE
    elif shape == "immortal":
        owner.flags |= abi.PY_FLAG_IMMORTAL
    elif shape == "transitive":
        # Direct finalizer flags cannot prove safety: a container can own a
        # graph with finalizers/weakrefs. Reject the container without walking.
        owner.fields[0] = memory.item
    memory.items.fields[position * 8] = owner
    before = dict(memory.items.fields)
    assert memory.run() == 0
    assert memory.items.fields == before and not memory.events
    memory.assert_clean()


def test_alias_payload_replacement_is_reloaded_after_graph_acquisition():
    memory = ClearMemory()
    old_items = memory.items
    def changed_lock():
        new_items = _Slots(32 * 8)
        new_items.fields.update(old_items.fields)
        memory.payloads.append(new_items)
        memory.items = new_items
        memory.lst.fields[abi.PYLISTOBJECT_ITEMS_OFFSET] = new_items
        memory.lst.fields[abi.PYLISTOBJECT_CAPACITY_OFFSET] = 32
        memory.lock()
    memory.namespace["pcc_py_gc_minor_graph_lock"] = changed_lock
    assert memory.run() == 1
    assert list(old_items.fields.values()) == memory.values
    assert all(memory.items.fields[i * 8] is memory.none for i in range(16))
    memory.assert_clean()


NATIVE_SHAPES = r'''
#include "py_internal.h"
#include <stdio.h>
#include <stdint.h>
extern int32_t pcc_gc_backend4_no_refcounts, pcc_gc_backend_frozen;
extern int32_t pcc_log_init_state, pcc_diagnostics_runtime_log_fast_state;
extern int32_t pcc_gc_refcount_provenance_probe;
extern int64_t pcc_py_gc_minor_graph_lock_depth(void);
extern int64_t pcc_thread_no_park_depth(void);
static PyObject *seed(int index) {
    if (index % 3 == 0) return NULL;
    if (index % 3 == 1) return py_None;
    return py_int_from_i64(index + 41);
}
int main(void) {
    int backend = pcc_gc_backend();
    py_gc_disable();
    PyObject *roots[3] = {0};
    int32_t map[1] = {3};
    pcc_gc_frame_enter(map, roots);
    roots[0] = py_gen_frame_new(18);
    roots[1] = py_list_new(0);
    roots[2] = py_exc_new(19, "preserved");
    if (!roots[0] || !roots[1] || !roots[2]) return 1;
    int admitted = backend >= 3 && pcc_gc_backend_frozen && pcc_gc_backend4_no_refcounts
        && pcc_log_init_state == 2 && !pcc_diagnostics_runtime_log_fast_state
        && !pcc_gc_refcount_provenance_probe;
    if (backend >= 3 && !pcc_gc_refcount_provenance_probe && !pcc_diagnostics_runtime_log_fast_state && !admitted) return 2;
    for (int i = 0; i < 18; i++)
        if (i % 3 == 2 && !py_mem_ptr_is_tagged_int(seed(i))) return 13;
    int counts[] = {0, 1, 2, 15, 16, 17, 18};
    for (int test = 0; test < 7; test++) {
        for (int i = 0; i < 18; i++) py_gen_frame_set(roots[0], i, seed(i));
        int count = counts[test];
        py_raise(roots[2]);
        int result = py_gen_frame_clear_group(roots[0], 0, count);
        if (result != (admitted && count >= 2 && count <= 16)) return 3;
        if (py_current_exception() != roots[2]) return 4;
        py_clear_exception();
        for (int i = 0; i < 18; i++) {
            PyObject *expected = result && i < count ? py_None : seed(i);
            if (((PyListObject *)roots[0])->items[i] != expected) return 5;
        }
        if (!result) {
            /* This is the unchanged caller fallback, with its original order. */
            for (int i = 0; i < count; i++)
                py_gen_frame_set(pcc_gc_load_ptr(0, &roots[0]), i, py_None);
        }
        for (int i = 0; i < count; i++)
            if (((PyListObject *)roots[0])->items[i] != py_None) return 6;
    }
    for (int i = 0; i < 18; i++) py_gen_frame_set(roots[0], i, seed(i));
    if (py_gen_frame_clear_group(roots[0], -1, 2)
            || py_gen_frame_clear_group(roots[0], 17, 2)
            || py_gen_frame_clear_group(roots[0], INT64_MAX, 2)) return 7;
    for (int i = 0; i < 18; i++)
        if (((PyListObject *)roots[0])->items[i] != seed(i)) return 8;
    /* A rejected partially invalid range is left to the serial caller. */
    py_gen_frame_set(roots[0], 17, py_None);
    py_gen_frame_set(roots[0], 18, py_None);
    if (((PyListObject *)roots[0])->items[17] != py_None
            || ((PyListObject *)roots[0])->items[16] != seed(16)) return 9;
    /* A nonfinalizable container can own a finalizable/weakly observed graph.
       The whole range must reject, not only objects with direct callbacks. */
    for (int i = 0; i < 18; i++) py_gen_frame_set(roots[0], i, seed(i));
    py_list_append(roots[1], roots[2]);
    py_gen_frame_set(roots[0], 15, roots[1]);
    if (py_gen_frame_clear_group(roots[0], 0, 16)) return 11;
    for (int i = 0; i < 18; i++)
        if (((PyListObject *)roots[0])->items[i] != (i == 15 ? roots[1] : seed(i))) return 12;
    py_gen_frame_set(roots[0], 15, seed(15));
    if (pcc_py_gc_minor_graph_lock_depth() || pcc_thread_no_park_depth()) return 10;
    for (int i = 0; i < 3; i++) pcc_gc_store_root(&roots[i], 0);
    pcc_gc_frame_leave(roots);
    py_gc_collect();
    puts("FRAME_CLEAR_GROUP_SHAPES_OK");
    return 0;
}
'''


NATIVE_RELOCATION = r'''
#include "py_internal.h"
#include <stdio.h>
extern PyObject *pcc_gc_generational_oldify_copy(PyObject *);
int main(void) {
    int backend = pcc_gc_backend();
    if (backend != 3 && backend != 4) return 1;
    py_gc_disable();
    PyObject *roots[2] = {0};
    int32_t map[1] = {2};
    pcc_gc_frame_enter(map, roots);
    roots[0] = py_gen_frame_new(4);
    roots[1] = backend == 3 ? py_bytes_new("moved", 5) : py_list_new(0);
    if (!roots[0] || !roots[1]) return 2;
    for (int i = 0; i < 4; i++) py_gen_frame_set(roots[0], i, roots[1]);
    /* Only nonowning target cells admit; the untouched ends still prove
       GC3 value relocation and GC4 owner/value canonicalization. */
    py_gen_frame_set(roots[0], 1, NULL);
    py_gen_frame_set(roots[0], 2, py_int_from_i64(42));
    pcc_gc_unpin(roots[0]);
    pcc_gc_unpin(roots[1]);
    PyObject *source = roots[0], *value = roots[1], *owner = source, *moved;
    if (backend == 3) {
        pcc_py_gc_minor_graph_lock();
        moved = pcc_gc_generational_oldify_copy(value);
        pcc_py_gc_minor_graph_unlock();
    } else {
        pcc_gc_reset_relocation_set();
        if (pcc_gc_select_relocation_set(128) <= 0
                || !pcc_gc_relocation_set_contains(source)
                || !pcc_gc_relocation_set_contains(value)) return 3;
        moved = pcc_gc_relocate_copy(value, sizeof(PyListObject));
        owner = pcc_gc_relocate_copy(source, sizeof(PyListObject));
        if (!owner || owner == source) return 4;
    }
    if (!moved || moved == value) return 5;
    if (py_gen_frame_clear_group(source, 1, 2) != 1) return 6;
    if (pcc_gc_load_ptr(0, &roots[0]) != owner) return 7;
    if (((PyListObject *)owner)->items[1] != py_None
            || ((PyListObject *)owner)->items[2] != py_None) return 8;
    if (pcc_gc_load_ptr(owner, &((PyListObject *)owner)->items[0]) != moved
            || pcc_gc_load_ptr(owner, &((PyListObject *)owner)->items[3]) != moved) return 9;
    if (py_header(owner)->flags & PY_FLAG_GC_PINNED) return 10;
    for (int i = 0; i < 2; i++) pcc_gc_store_root(&roots[i], 0);
    pcc_gc_frame_leave(roots);
    puts("FRAME_CLEAR_GROUP_RELOCATION_OK");
    return 0;
}
'''


@pytest.mark.integration
def test_native_clear_admission_bounds_exception_and_serial_fallback(tmp_path, pcc_runtime_archive):
    executable = _native(tmp_path, pcc_runtime_archive, "frame_clear_group_shapes", NATIVE_SHAPES)
    for backend in range(5):
        result = _run(executable, backend)
        assert (result.returncode, result.stdout, result.stderr) == (0, "FRAME_CLEAR_GROUP_SHAPES_OK\n", ""), backend
    for backend in (3, 4):
        result = _run(executable, backend, PCC_GC_REFCOUNT_PROVENANCE_PROBE="1")
        assert (result.returncode, result.stdout, result.stderr) == (0, "FRAME_CLEAR_GROUP_SHAPES_OK\n", ""), backend
    for backend in (3, 4):
        result = _run(executable, backend, PCC_LOG="gc", PCC_LOG_FILE=str(tmp_path / f"clear-gc{backend}.log"))
        assert (result.returncode, result.stdout, result.stderr) == (0, "FRAME_CLEAR_GROUP_SHAPES_OK\n", ""), backend


@pytest.mark.integration
def test_native_clear_after_real_gc3_value_and_gc4_owner_relocation(tmp_path, pcc_runtime_archive):
    executable = _native(tmp_path, pcc_runtime_archive, "frame_clear_group_relocation", NATIVE_RELOCATION)
    for backend in (3, 4):
        result = _run(executable, backend, PCC_GC_MINOR_ALLOC_MAX="4096")
        assert (result.returncode, result.stdout, result.stderr) == (0, "FRAME_CLEAR_GROUP_RELOCATION_OK\n", ""), backend
