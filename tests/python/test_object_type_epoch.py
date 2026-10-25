"""C retyping invalidates predicate caches without wraparound reuse."""
import ast
import os
from pathlib import Path
import subprocess

import pytest

from tests.owned_c_execution import compile_owned_c_with_runtime


@pytest.mark.parametrize("epoch, expected", [(0, 1), (7, 8), ((1 << 63) - 2, (1 << 63) - 1),
                                             ((1 << 63) - 1, -1), (-1, -1)])
def test_type_publication_and_epoch_saturation_share_the_lock(epoch, expected):
    source = Path(__file__).resolve().parents[2] / "pcc/runtime/py/py_capi_cext_runtime.py"
    node = next(n for n in ast.parse(source.read_text()).body
                if isinstance(n, ast.FunctionDef) and n.name == "pcc_capi_set_type")
    node.decorator_list = []
    state = {"epoch": epoch, "depth": 0, "tag": None, "type": None}
    events = []

    def lock():
        assert state["depth"] == 0
        state["depth"] = 1
        events.append("lock")

    def unlock():
        assert state["depth"] == 1
        state["depth"] = 0
        events.append("unlock")

    def tag_for(value):
        assert state["depth"] == 0
        return 1234

    def store_tag(obj, offset, value):
        assert state["depth"] == 1 and offset == 8
        state["tag"] = value
        events.append("tag")

    def store_type(obj, offset, value):
        assert state["depth"] == 1 and offset == 16
        state["type"] = value
        events.append("type")

    def publish_epoch(slot, offset, value, ordering):
        assert state["depth"] == 1 and state["tag"] == 1234 and state["type"] == "new"
        assert ordering == "release"
        state["epoch"] = value
        events.append("epoch")

    namespace = dict(i64=int, ptr_is_null=lambda p: p is None,
                     pcc_capi_cext_tag_for=tag_for, store_i32=store_tag, store_ptr=store_type,
                     global_addr=lambda name: name,
                     atomic_load_i64=lambda *args: state["epoch"], atomic_store_i64=publish_epoch,
                     pcc_py_gc_minor_graph_lock=lock, pcc_py_gc_minor_graph_unlock=unlock)
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(source), "exec"), namespace)
    namespace["pcc_capi_set_type"]("object", "new")
    assert state["epoch"] == expected and state["depth"] == 0
    assert events == ["lock", "tag", "type"] + ([] if epoch < 0 else ["epoch"]) + ["unlock"]
    previous = list(events)
    namespace["pcc_capi_set_type"](None, "ignored")
    assert events == previous


PROBE = r'''
#include <Python.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
extern int64_t pcc_object_type_epoch;
extern int64_t pcc_gc_set_backend(int64_t);
typedef struct { PyObject_HEAD long value; } EpochObject;
static PyTypeObject FirstType = {
    PyVarObject_HEAD_INIT(NULL, 0)
    .tp_name = "epoch.First", .tp_basicsize = sizeof(EpochObject),
    .tp_flags = Py_TPFLAGS_DEFAULT, .tp_new = PyType_GenericNew,
};
static PyTypeObject SecondType = {
    PyVarObject_HEAD_INIT(NULL, 0)
    .tp_name = "epoch.Second", .tp_basicsize = sizeof(EpochObject),
    .tp_flags = Py_TPFLAGS_DEFAULT, .tp_new = PyType_GenericNew,
};
int main(int argc, char **argv) {
    if (argc != 2 || pcc_gc_set_backend(atoi(argv[1])) != 0) return 1;
    if (PyType_Ready(&FirstType) < 0 || PyType_Ready(&SecondType) < 0) return 2;
    PyObject *object = PyType_GenericNew(&FirstType, NULL, NULL);
    if (!object || Py_TYPE(object) != &FirstType) return 3;
    int64_t previous = __atomic_load_n(&pcc_object_type_epoch, __ATOMIC_ACQUIRE);
    Py_SET_TYPE(object, &SecondType);
    if (Py_TYPE(object) != &SecondType) return 4;
    if (__atomic_load_n(&pcc_object_type_epoch, __ATOMIC_ACQUIRE) <= previous) return 5;
    __atomic_store_n(&pcc_object_type_epoch, INT64_MAX, __ATOMIC_RELEASE);
    Py_SET_TYPE(object, &FirstType);
    if (__atomic_load_n(&pcc_object_type_epoch, __ATOMIC_ACQUIRE) != -1) return 6;
    Py_SET_TYPE(object, &SecondType);
    if (Py_TYPE(object) != &SecondType) return 7;
    if (__atomic_load_n(&pcc_object_type_epoch, __ATOMIC_ACQUIRE) != -1) return 8;
    Py_DECREF(object);
    puts("TYPE_EPOCH_OK");
    return 0;
}
'''


def test_owned_c_type_mutation_epoch_native_five_gc(tmp_path, pcc_runtime_archive):
    source = tmp_path / "epoch.c"
    source.write_text(PROBE)
    binary = tmp_path / "epoch"
    compile_owned_c_with_runtime(source, binary, runtime_archive=pcc_runtime_archive)
    for gc in range(5):
        result = subprocess.run([str(binary), str(gc)], capture_output=True, text=True,
                                timeout=20, env=dict(os.environ, PATH="/nonexistent"))
        assert (result.returncode, result.stdout, result.stderr) == (0, "TYPE_EPOCH_OK\n", ""), gc
