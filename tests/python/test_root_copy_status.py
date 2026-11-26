"""The one collector's root copies return a status and take no lease.

``pcc_gc_root_copy`` and ``pcc_gc_root_copy_borrowed`` (py_runtime.h) retain
a source root's value into an empty destination root. They replace the
lease forms in code generation (docs/status.md, item 2 (d) 4c): no token is
returned, so nothing is released afterwards.
"""

import os
from pathlib import Path
import subprocess

import pytest

from tests.owned_c_execution import compile_owned_c_with_runtime


ROOT = Path(__file__).resolve().parents[2]

PROGRAM = r'''
#include "py_runtime.h"
#include <stdint.h>
#include <stdio.h>

static int64_t refs(PyObject *object) {
    return ((PyObjectHeader *)object)->refcount;
}

static int check(int64_t (*copy)(PyObject **, PyObject **), int base) {
    PyObject *source = (PyObject *)py_list_new(0);
    if (!source) return base + 1;
    PyObject *destination = 0;
    int64_t before = refs(source);
    int64_t leases = pcc_gc_foreign_lease_count_quiescent();
    /* Success: one more owner in the destination, no lease. */
    if (copy(&destination, &source) != 0) return base + 2;
    if (destination != source || refs(source) != before + 1) return base + 3;
    if (pcc_gc_foreign_lease_count_quiescent() != leases) return base + 4;
    /* A nonempty destination is refused with both owners unchanged. */
    PyObject *other = (PyObject *)py_list_new(0);
    if (!other) return base + 5;
    PyObject *occupied = other;
    if (copy(&occupied, &source) != -4) return base + 6;
    if (occupied != other || refs(source) != before + 1) return base + 7;
    /* The same slot as both ends, and NULL slots, are invalid. */
    if (copy(&source, &source) != -1) return base + 8;
    if (copy(0, &source) != -1 || copy(&destination, 0) != -1) return base + 9;
    /* An empty source copies NULL without a count. */
    PyObject *empty = 0, *copied = 0;
    if (copy(&copied, &empty) != 0 || copied != 0) return base + 10;
    if (refs(source) != before + 1) return base + 11;
    py_decref(destination);
    py_decref(other);
    py_decref(source);
    return 0;
}

int main(void) {
    int status = check(pcc_gc_root_copy, 0);
    if (status == 0) status = check(pcc_gc_root_copy_borrowed, 20);
    if (status != 0) return status;
    puts("ROOT_COPY_STATUS_OK");
    return 0;
}
'''


@pytest.mark.parametrize("archive", ("pcc_runtime_archive", "threaded_pcc_runtime_archive"))
def test_root_copies_return_status_without_a_lease(tmp_path, request, archive):
    source = tmp_path / "root_copy_status.c"
    binary = tmp_path / "root_copy_status"
    source.write_text(PROGRAM)
    compile_owned_c_with_runtime(
        source, binary, runtime_archive=Path(request.getfixturevalue(archive)),
    )
    run = subprocess.run(
        [str(binary)], capture_output=True, text=True, timeout=30,
        env=dict(os.environ, PATH="/nonexistent"),
    )
    assert (run.returncode, run.stdout, run.stderr) == (0, "ROOT_COPY_STATUS_OK\n", "")
