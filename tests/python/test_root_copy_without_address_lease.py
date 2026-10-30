"""Copied owners stay traced without reserving their address for slot consumers."""
import os
import subprocess
import sys

from pcc.frontends.python.pipeline import compile_python
from tests.owned_c_execution import compile_owned_c_with_runtime


PROBE = r'''
#include "py_runtime.h"
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
extern int64_t pcc_gc_foreign_lease_count_quiescent(void);
int main(int argc, char **argv) {
    if (argc != 2 || pcc_gc_set_backend(atoi(argv[1])) != 0) return 1;
    PyObject *roots[2] = {NULL, NULL};
    int32_t map = 2;
    pcc_gc_frame_enter(&map, roots);
    roots[0] = py_list_new(4);
    if (!roots[0]) return 2;
    int64_t plan[16];
    pcc_py_gc_minor_graph_lock();
    int64_t before = pcc_gc_foreign_lease_count_quiescent();
    int64_t status = pcc_gc_root_copy_owned_prepare_locked(&roots[1], &roots[0], plan);
    int64_t after = pcc_gc_foreign_lease_count_quiescent();
    pcc_py_gc_minor_graph_unlock();
    pcc_gc_store_root_plan_finish(plan);
    if (status || before != after || roots[0] != roots[1]) return 3;
    pcc_py_gc_minor_graph_lock();
    status = pcc_gc_root_copy_owned_prepare_locked(&roots[1], &roots[0], plan);
    pcc_py_gc_minor_graph_unlock();
    pcc_gc_store_root_plan_finish(plan);
    if (status != -4) return 4;
    pcc_gc_store_root(&roots[0], NULL);
    pcc_gc_collect(1);
    int64_t lease = pcc_gc_foreign_lease_acquire(&roots[1]);
    if (lease < 0) return 5;
    py_list_append(roots[1], py_int_from_i64(73));
    if (py_list_len(roots[1]) != 1) return 6;
    PyObject *item = py_list_get(roots[1], 0);
    if (py_int_value_i64(item) != 73) return 7;
    py_decref(item);
    if (pcc_gc_foreign_lease_release(&roots[1], lease)) return 8;
    pcc_gc_store_root(&roots[1], NULL);
    pcc_gc_frame_leave(roots);
    puts("OWNED_COPY_NO_LEASE_OK");
    return 0;
}
'''


def test_owned_c_root_copy_retains_without_an_address_lease(tmp_path, pcc_runtime_archive):
    source = tmp_path / "copy.c"
    source.write_text(PROBE)
    binary = tmp_path / "copy"
    compile_owned_c_with_runtime(source, binary, runtime_archive=pcc_runtime_archive)
    for gc in range(5):
        result = subprocess.run([str(binary), str(gc)], capture_output=True, text=True, timeout=20,
                                env=dict(os.environ, PATH="/nonexistent"))
        assert (result.returncode, result.stdout, result.stderr) == (0, "OWNED_COPY_NO_LEASE_OK\n", ""), gc


PROGRAM = '''import gc
import weakref

events = []
class Value:
    def __init__(self, name):
        self.name = name
    def __del__(self):
        events.append(self.name)

def main():
    first = Value("first")
    second = Value("second")
    reference = weakref.ref(first)
    data = {"a": first, "b": second}
    values = list(data.values())
    items = list(data.items())
    keys = list(data.keys())
    assert keys == ["a", "b"]
    assert values[0] is first and items[0][1] is first
    data.clear()
    first = None
    second = None
    gc.collect()
    assert reference() is values[0] and events == []
    values.clear()
    gc.collect()
    assert events == []
    items.clear()
    gc.collect()
    assert reference() is None and sorted(events) == ["first", "second"]
    print("DICT_SNAPSHOT_OWNERS_OK")
main()
'''


def test_native_dict_snapshots_keep_identity_and_finalizers_five_gc(tmp_path, pcc_runtime_archive):
    source = tmp_path / "snapshot.py"
    source.write_text(PROGRAM)
    reference = subprocess.run([sys.executable, str(source)], capture_output=True, text=True, timeout=15)
    assert reference.returncode == 0, reference.stderr
    binary = tmp_path / "snapshot"
    compile_python(str(source), str(binary), backend="self", libpython_mode="off",
                   ir_scaffold_mode="on", runtime_archive=str(pcc_runtime_archive))
    for gc in range(5):
        result = subprocess.run([str(binary)], capture_output=True, text=True, timeout=25,
                                env=dict(os.environ, PATH="/nonexistent", PCC_GC_BACKEND=str(gc)))
        assert (result.returncode, result.stdout, result.stderr) == (0, reference.stdout, ""), gc
