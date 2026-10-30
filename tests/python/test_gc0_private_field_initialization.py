"""Fresh fields need one retain; slow paths keep callbacks and moving GC."""
import ast
import os
from pathlib import Path
import subprocess

import pytest

from pcc.runtime.py import py_abi_constants as abi
from tests.owned_c_execution import compile_owned_c_with_runtime
from tests.python.test_bulk_nonterminal_retirement import Model

ROOT = Path(__file__).resolve().parents[2]


def load_body(filename, name, namespace):
    path = ROOT / "pcc/runtime/py" / filename
    node = next(n for n in ast.parse(path.read_text()).body
                if isinstance(n, ast.FunctionDef) and n.name == name)
    node.decorator_list = []
    exec(compile(ast.Module([node], type_ignores=[]), str(path), "exec"), namespace)


class InitModel(Model):
    def __init__(self, backend=0, **settings):
        super().__init__(**settings)
        self.memory["pcc_gc_backend_selected"] = backend
        self.namespace.update(ptr_eq=lambda a, b: a == b, store_ptr=self.store)
        load_body("freestanding_gc_root_operations.py", "pcc_gc_try_init_ptr_locked", self.namespace)

    def store(self, pointer, offset, value):
        self.memory[pointer + offset] = value

    def initialize(self, value):
        self.memory[6000] = value
        self.memory[7000] = 12345  # Unowned bytes; must never be retired.
        return self.namespace["pcc_gc_try_init_ptr_locked"](7000, 6000)


@pytest.mark.parametrize("kind", ("null", "tagged", "immortal", "live"))
def test_private_field_retains_once_and_preserves_source(kind):
    m = InitModel()
    value = 0 if kind == "null" else 7
    if kind in ("immortal", "live"):
        value = m.object(2000, 2, abi.PY_FLAG_IMMORTAL if kind == "immortal" else 0)
    assert m.initialize(value) == 1
    assert m.memory[6000] == value and m.memory[7000] == value
    if kind in ("immortal", "live"):
        assert m.memory[value] == (2 if kind == "immortal" else 3)


@pytest.mark.parametrize("settings", ({"fast": 0}, {"log": 1}, {"backend": 4}, {"active": -1}))
def test_private_field_rejects_diagnostics_and_stale_transition_admission(settings):
    m = InitModel(**settings)
    value = m.object(2000, 2)
    assert m.initialize(value) == 0
    assert m.memory[7000] == 12345 and m.memory[value] == 2


@pytest.mark.parametrize("count,flags,tag", [
    (0, 0, abi.PY_TYPE_LIST), (-1, 0, abi.PY_TYPE_LIST),
    ((1 << 63) - 1, 0, abi.PY_TYPE_LIST),
    (1, abi.PY_FLAG_GC_DEALLOCATING, abi.PY_TYPE_LIST),
    (1, 0, abi.PY_TYPE_CONTINUATION), (1, 0, abi.PY_TYPE_CEXT_TAG_BASE),
])
def test_private_field_leaves_nonordinary_states_for_full_protocol(count, flags, tag):
    m = InitModel()
    value = m.object(2000, count, flags, tag)
    assert m.initialize(value) == 0
    assert m.memory[7000] == 12345 and m.memory[value] == count


def test_private_field_lost_cas_does_not_publish_or_retry():
    m = InitModel(contend=True)
    value = m.object(2000, 2)
    assert m.initialize(value) == 0
    assert m.memory[value] == 1 and m.memory[7000] == 12345
    assert m.memory[6000] == value


def test_private_field_rejects_null_and_alias_addresses():
    m = InitModel()
    fn = m.namespace["pcc_gc_try_init_ptr_locked"]
    assert fn(0, 6000) == 0 and fn(7000, 0) == 0 and fn(6000, 6000) == 0


class CapacityModel(InitModel):
    def __init__(self, *, switch=False, full=False, dying=False, miss=False):
        super().__init__()
        self.depth = 0
        self.events = []
        self.switch_mode = switch
        self.memory.update({100: 1000, 200: 3000, 1012: abi.PY_FLAG_GC_DEALLOCATING if dying else 0,
                            1016: 4 if full else 0, 1024: 4, 1032: 2000})
        self.object(3000, 2)
        self.namespace.update({"load_i64": self.load, "store_i64": self.publish,
                               "ptr_add": lambda p, n: p + n,
                               "pcc_py_gc_minor_graph_lock": self.lock,
                               "pcc_py_gc_minor_graph_unlock": self.unlock,
                               # The list's transaction lock (container or graph lock).
                               "_list_lock": lambda _list: self.lock(),
                               "_list_unlock": lambda _list: self.unlock(),
                               "_list_is_sane": self.validate,
                               "pcc_gc_try_init_ptr_locked": (lambda *args: 0) if miss else self.namespace["pcc_gc_try_init_ptr_locked"],
                               # A full list is handed to the doubling path
                               # (test_list_append_growth_gc0's); declining
                               # here keeps this transaction's own contract.
                               "_append_grow_gc0": self.grow})
        self.grown = []
        load_body("py_list.py", "_append_existing_capacity_gc0", self.namespace)

    def grow(self, list_slot, item_slot, items, capacity):
        assert self.depth == 0
        self.grown.append((list_slot, item_slot, items, capacity))
        return 0

    def lock(self):
        assert self.depth == 0
        self.depth = 1
        if self.switch_mode:
            # Backend is already published while the old atomic flag remains.
            self.memory["pcc_gc_backend_selected"] = 4
            self.memory["pcc_gc_foreign_lease_active"] = -1

    def unlock(self):
        assert self.depth == 1
        self.depth = 0

    def validate(self, obj, diagnostic):
        assert self.depth == 1 and obj == 1000
        self.events.append("validate")
        return True

    def publish(self, obj, offset, length):
        assert self.depth == 1 and self.memory[2000] == 3000
        self.events.append("publish-length")
        self.memory[obj + offset] = length

    def run_append(self):
        return self.namespace["_append_existing_capacity_gc0"](100, 200)


def test_capacity_commit_owns_child_before_publishing_length():
    m = CapacityModel()
    assert m.run_append() == 1 and m.depth == 0
    assert m.memory[1016] == 1 and m.memory[2000] == 3000 and m.memory[3000] == 3
    assert m.memory[100] == 1000 and m.memory[200] == 3000
    assert m.events == ["validate", "publish-length"]


@pytest.mark.parametrize("settings", ({"switch": True}, {"full": True}, {"dying": True}, {"miss": True}))
def test_capacity_miss_unlocks_without_publishing(settings):
    m = CapacityModel(**settings)
    before = m.memory[1016]
    assert m.run_append() == 0 and m.depth == 0
    assert m.memory[1016] == before and m.memory[3000] == 2
    assert "publish-length" not in m.events
    if settings.get("switch"):
        assert m.events == []
    assert m.grown == ([(100, 200, 2000, 4)] if settings.get("full") else [])


C_PROBE = r'''
#include "py_runtime.h"
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
extern void pcc_py_gc_minor_graph_lock(void);
extern void pcc_py_gc_minor_graph_unlock(void);
extern int32_t pcc_gc_refcount_atomic;
int main(int argc, char **argv) {
    if (argc != 2 || pcc_gc_set_backend(atoi(argv[1]))) return 1;
    PyObject *roots[2] = {NULL, NULL};
    int32_t map = 2;
    pcc_gc_frame_enter(&map, roots);
    roots[0] = py_list_new(4);
    pcc_py_gc_minor_graph_lock();
    int64_t copied = pcc_gc_try_init_ptr_locked(&roots[1], &roots[0]);
    pcc_py_gc_minor_graph_unlock();
    if (pcc_gc_refcount_atomic) {
        if (copied != 1 || roots[1] != roots[0]) return 2;
        pcc_gc_store_root(&roots[0], NULL);
        py_list_append(roots[1], py_int_from_i64(7));
        if (py_list_len(roots[1]) != 1) return 3;
    } else if (copied != 0 || roots[1] != NULL) return 4;
    pcc_gc_store_root(&roots[1], NULL);
    pcc_gc_store_root(&roots[0], NULL);
    pcc_gc_frame_leave(roots);
    puts("PRIVATE_FIELD_OK");
    return 0;
}
'''


def test_native_private_field_admission_and_retention_five_gc(tmp_path, pcc_runtime_archive):
    source = tmp_path / "field.c"
    source.write_text(C_PROBE)
    output = tmp_path / "field"
    compile_owned_c_with_runtime(source, output, runtime_archive=pcc_runtime_archive)
    for gc in range(5):
        for probe in ("0", "2"):
            ran = subprocess.run([str(output), str(gc)], capture_output=True, text=True, timeout=20,
                                 env=dict(os.environ, PATH="/nonexistent", PCC_GC_REFCOUNT_PROVENANCE_PROBE=probe))
            assert (ran.returncode, ran.stdout, ran.stderr) == (0, "PRIVATE_FIELD_OK\n", ""), (gc, probe)
