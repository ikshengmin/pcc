"""``py_int_to_i64`` must read a bool object as 1 or 0.

bool is an int subclass.  The unbox treated any heap object that is not
tagged ``PY_TYPE_INT`` as a foreign number and returned 0 with overflow set,
so an int-typed slot that received a dynamic ``True`` read 0.  pcc1 typed
``False if recv_borrowed else self._owned_release_needed(...)`` as int (the
arithmetic ``common_type(bool, bool)``), unboxed the method's ``True`` to 0
and dropped the release of every owned receiver decided that way -- IR the
host compiler, running the same line under CPython, does not produce.
"""
from __future__ import annotations

from pathlib import Path

from tests.python.gc_production_contract.native_abi_probe import run_native_abi_probe


SOURCE = r'''
    #include "py_internal.h"
    #include <stdint.h>

    int64_t pcc_test_gc_probe(void) {
        int overflow = 7;
        if (py_int_to_i64(py_True, &overflow) != 1 || overflow != 0) return 10;
        overflow = 7;
        if (py_int_to_i64(py_False, &overflow) != 0 || overflow != 0) return 11;
        overflow = 0;
        PyObject *text = py_str_new("x", 1);
        (void)py_int_to_i64(text, &overflow);
        if (overflow == 0) return 12;
        overflow = 7;
        if (py_int_to_i64(py_int_from_i64(41), &overflow) != 41 || overflow != 0) return 13;
        return 0;
    }
'''

def test_bool_objects_unbox_as_zero_and_one(
    tmp_path: Path,
    pcc_runtime_archive: Path,
    python_program_compiler,
) -> None:
    result = run_native_abi_probe(
        tmp_path, SOURCE, pcc_runtime_archive, python_program_compiler,
    )
    assert (result.returncode, result.stdout, result.stderr) == (0, "0\n", ""), result
