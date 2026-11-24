"""The runtime's C weakref call keeps the target alive until released."""
from __future__ import annotations

import subprocess

from pcc.backend.owned_object_emit import emit_owned_object
from pcc.frontends.python.pipeline_targets import host_target_triple
from tests.python.test_owned_external_tls import _c_ir


_C_PROBE = r"""
typedef struct PyObject PyObject;
extern PyObject *py_set_new(void);
extern PyObject *py_weakref_new(PyObject *target, PyObject *callback);
extern PyObject *py_weakref_call(PyObject *ref);
extern void pcc_gc_release(PyObject *value);
extern long long pcc_gc_collect(int reason);
extern PyObject *const py_None;

long long weakref_lifetime(void) {
    /* A set is weakrefable in both PCC and CPython; a list is not. */
    PyObject *target = py_set_new();
    if (!target) return 1;
    PyObject *ref = py_weakref_new(target, 0);
    if (!ref) return 2;
    PyObject *first = py_weakref_call(ref);
    if (first != target) return 3;

    /* Only the owned result of dereferencing the weakref keeps it alive. */
    pcc_gc_release(target);
    PyObject *second = py_weakref_call(ref);
    if (second != first) return 4;
    pcc_gc_release(second);
    pcc_gc_release(first);
    pcc_gc_collect(0);
    PyObject *after = py_weakref_call(ref);
    long long result = after == py_None ? 0 : 5;
    pcc_gc_release(after);
    pcc_gc_release(ref);
    return result;
}
"""

_PROGRAM = """\
from pcc.extern import extern, c_int64

weakref_lifetime = extern("weakref_lifetime", (), c_int64)
print(weakref_lifetime())
"""


def test_t4_weakref_callable_and_dealloc_clear_native(
    tmp_path, pcc_runtime_archive, python_program_compiler,
):
    target = host_target_triple()
    helper = tmp_path / "weakref_probe.o"
    helper.write_bytes(emit_owned_object(_c_ir(_C_PROBE, target), target))
    source = tmp_path / "weakref_probe.py"
    source.write_text(_PROGRAM, encoding="utf-8")
    executable = tmp_path / "weakref_probe.out"
    python_program_compiler(
        str(source), str(executable), backend="self",
        runtime_archive=str(pcc_runtime_archive), object_inputs=(str(helper),),
    )
    proc = subprocess.run([str(executable)], capture_output=True, text=True, timeout=30)
    assert (proc.returncode, proc.stdout, proc.stderr) == (0, "0\n", "")
