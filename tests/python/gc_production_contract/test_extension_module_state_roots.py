"""Single-collector C extension module-state lifetime contract.

Darwin exercises the owned dynamic loader. On other hosts the same module
definition and exec slot are linked into the executable. The static test
covers module state, reference lifetime and the traversal ABI, not loading
a shared object.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from tests.python.gc_production_contract.native_abi_probe import run_native_abi_probe


REPO_ROOT = Path(__file__).absolute().parents[3]


_EXTENSION_SOURCE = r"""
#define PY_SSIZE_T_CLEAN
#include <Python.h>

typedef struct {
    PyObject *items;
} StateDemoState;

static int statedemo_traverse(PyObject *module, visitproc visit, void *arg) {
    StateDemoState *state = (StateDemoState *)PyModule_GetState(module);
    if (state == NULL) return 0;
    Py_VISIT(state->items);
    return 0;
}

static int statedemo_exec(PyObject *module) {
    StateDemoState *state = (StateDemoState *)PyModule_GetState(module);
    if (state == NULL) return -1;
    state->items = PyList_New(0);
    if (state->items == NULL) return -1;
    PyObject *seed = PyLong_FromLong(41);
    if (seed == NULL) return -1;
    if (PyList_Append(state->items, seed) != 0) return -1;
    Py_DECREF(seed);
    return 0;
}

static PyObject *statedemo_push(PyObject *self, PyObject *args) {
    (void)args;
    StateDemoState *state = (StateDemoState *)PyModule_GetState(self);
    if (state == NULL || state->items == NULL) return NULL;
    PyObject *next = PyLong_FromLong(PyList_Size(state->items));
    if (next == NULL) return NULL;
    if (PyList_Append(state->items, next) != 0) return NULL;
    Py_DECREF(next);
    return PyLong_FromLong(PyList_Size(state->items));
}

static PyMethodDef StateDemoMethods[] = {
    {"push", statedemo_push, METH_VARARGS, "append through module state"},
    {NULL, NULL, 0, NULL},
};

static PyModuleDef_Slot StateDemoSlots[] = {
    {Py_mod_exec, (void *)statedemo_exec},
    {0, NULL},
};

static PyModuleDef StateDemoModule = {
    PyModuleDef_HEAD_INIT,
    "statedemo",
    NULL,
    sizeof(StateDemoState),
    StateDemoMethods,
    StateDemoSlots,
    statedemo_traverse,
    NULL,
    NULL,
};

PyMODINIT_FUNC PyInit_statedemo(void) {
    return PyModuleDef_Init(&StateDemoModule);
}
"""


_PROGRAM = (
    "import gc\n"
    "import statedemo\n"
    "\n"
    "print(statedemo.push())\n"
    "gc.collect()\n"
    "print(statedemo.push())\n"
    "gc.collect()\n"
    "print(statedemo.push())\n"
)


@pytest.fixture(scope="module")
def _extension_module_state_exe(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("gc_extension_module_state")
    site = tmp / "site"
    site.mkdir()
    src = tmp / "statedemo.c"
    src.write_text(_EXTENSION_SOURCE, encoding="utf-8")
    ext = site / "statedemo.so"
    cc = os.environ.get("CC", "cc")
    ext_cmd = [
        cc,
        "-shared",
        "-fPIC",
        "-I",
        str(REPO_ROOT / "utils" / "fake_libc_include"),
        "-I",
        str(REPO_ROOT / "pcc" / "runtime" / "include"),
        str(src),
        "-o",
        str(ext),
    ]
    if sys.platform == "darwin":
        ext_cmd.extend(["-undefined", "dynamic_lookup"])
    build_ext = subprocess.run(
        ext_cmd,
        text=True,
        capture_output=True,
        timeout=30,
    )
    assert build_ext.returncode == 0, build_ext.stdout + build_ext.stderr

    main = tmp / "main.py"
    main.write_text(_PROGRAM, encoding="utf-8")
    exe = tmp / "extension_module_state_bin"
    env = os.environ.copy()
    env.pop("LC_ALL", None)
    env["PCC_PACKAGE_SITE"] = str(site)
    build = subprocess.run(
        [
            "uv",
            "run",
            "pcc",
            "--backend",
            "self",
            "--ir-scaffold=on",
            str(main),
            "-o",
            str(exe),
        ],
        text=True,
        capture_output=True,
        timeout=420,
        env=env,
    )
    assert build.returncode == 0, build.stdout + build.stderr
    return str(exe), str(site)


_STATIC_MODULE_DRIVER = r"""
extern PyObject *pcc_capi_module_exec(PyObject *);
extern void pcc_capi_visit_extension_module_state_roots(
    void (*visit)(PyObject *, void *), void *);

static PyObject *test_module;

static void count_state_reference(PyObject *object, void *arg) {
    StateDemoState *state = (StateDemoState *)PyModule_GetState(test_module);
    if (state != NULL && object == state->items) {
        *(int *)arg += 1;
    }
}

int64_t pcc_test_gc_probe(void) {
    test_module = pcc_capi_module_exec(PyInit_statedemo());
    return test_module == NULL ? -1 : 0;
}

int64_t statedemo_push_count(void) {
    if (test_module == NULL) return -1;
    int visits = 0;
    pcc_capi_visit_extension_module_state_roots(count_state_reference, &visits);
    if (visits != 1) return -2;
    PyObject *result = statedemo_push(test_module, NULL);
    if (result == NULL) return -3;
    long count = PyLong_AsLong(result);
    Py_DECREF(result);
    return count;
}
"""

_STATIC_READBACK = """\
import gc
push = extern('statedemo_push_count', (), c_int64)
print(push())
gc.collect()
print(push())
gc.collect()
print(push())
"""


def test_extension_module_state_roots(tmp_path, request):
    if sys.platform == "darwin":
        exe, site = request.getfixturevalue("_extension_module_state_exe")
        env = os.environ.copy()
        env.pop("LC_ALL", None)
        env["PCC_PACKAGE_SITE"] = site
        run = subprocess.run(
            [exe], text=True, capture_output=True, timeout=60, env=env,
        )
        assert run.returncode == 0, run.stderr
        assert run.stdout.splitlines() == ["2", "3", "4"], run.stdout
        return

    from pcc.frontends.python.pipeline import compile_python

    run = run_native_abi_probe(
        tmp_path, _EXTENSION_SOURCE + _STATIC_MODULE_DRIVER,
        request.getfixturevalue("pcc_runtime_archive"), compile_python,
        readback=_STATIC_READBACK,
        include_dirs=(REPO_ROOT / "utils" / "fake_libc_include",),
    )
    assert run.returncode == 0, run.stderr
    assert run.stdout.splitlines() == ["0", "2", "3", "4"], run.stdout
