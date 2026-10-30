"""The collector is selected before the first managed allocation and then fixed.

The maintainer decided (2026-10-30) that the GC backend is chosen at start
and never changes afterwards. PCC_GC_BACKEND, or one pcc_gc_set_backend call
before the first managed object exists (the C probes' pattern), selects it;
the first allocation fixes it, and pcc_gc_set_backend then refuses every
other backend while a same-backend reset stays legal. Code on a non-moving
collector may therefore hold raw object pointers without pins or leases.
"""
from __future__ import annotations

import os
from pathlib import Path
import subprocess

import pytest

from pcc.frontends.python.pipeline import compile_python
from tests.owned_c_execution import compile_owned_c_with_runtime


PROGRAM = r'''
import gc
from pcc.extern import extern, c_int64

set_backend = extern("pcc_gc_set_backend", (c_int64,), c_int64)
current_backend = extern("pcc_gc_backend", (), c_int64)

DEATHS = []


class Node:
    def __init__(self, value):
        self.value = value
        self.peer = None

    def __del__(self):
        DEATHS.append(self.value)


def main():
    selected = current_backend()
    keep = [Node(index) for index in range(8)]
    results = []
    target = 0
    while target < 5:
        results.append((target == selected, set_backend(target), current_backend() == selected))
        target += 1
    cycle = Node(100)
    cycle.peer = Node(101)
    cycle.peer.peer = cycle
    cycle = None
    keep = None
    gc.collect()
    gc.collect()
    refused = [status for same, status, unchanged in results if not same]
    reset = [status for same, status, unchanged in results if same]
    print(refused, reset, all(unchanged for same, status, unchanged in results),
          sorted(DEATHS)[:8], len(DEATHS) >= 8)


main()
'''

EXPECTED = "[-1, -1, -1, -1] [0] True [0, 1, 2, 3, 4, 5, 6, 7] True\n"


@pytest.mark.integration
def test_the_collector_cannot_change_after_the_first_allocation(tmp_path, threaded_pcc_runtime_archive):
    source = tmp_path / "fixed_backend.py"
    source.write_text(PROGRAM, encoding="utf-8")
    executable = tmp_path / "fixed_backend"
    compile_python(str(source), str(executable), backend="self",
                   libpython_mode="off", ir_scaffold_mode="on",
                   runtime_archive=str(threaded_pcc_runtime_archive))
    for backend in range(5):
        ran = subprocess.run([str(executable)], capture_output=True, text=True, timeout=60,
                             env=dict(os.environ, PATH="/nonexistent", PCC_GC_BACKEND=str(backend)))
        assert (ran.returncode, ran.stdout, ran.stderr) == (0, EXPECTED, ""), backend


C_PROGRAM = r"""
#include "py_runtime.h"
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>

extern PyObject *py_int_new_heap(int64_t value);

int main(int argc, char **argv) {
    if (argc != 3) return 1;
    int first = atoi(argv[1]);
    int raw = atoi(argv[2]);
    /* The runtime's C-ABI images registered their literals before main;
       data-segment images leave the choice open. */
    if (pcc_gc_set_backend(first) != 0) return 2;
    if (pcc_gc_backend() != first) return 3;
    /* A malloc'd big int fixes it as surely as a collector allocation. */
    PyObject *object = raw ? py_int_new_heap(INT64_C(1) << 62) : py_list_new(0);
    if (object == 0) return 4;
    for (int other = 0; other < 5; other++) {
        int64_t status = pcc_gc_set_backend(other);
        if (other == first ? status != 0 : status != -1) return 10 + other;
        if (pcc_gc_backend() != first) return 20 + other;
    }
    py_decref(object);
    printf("FIXED %d %d\n", first, raw);
    return 0;
}
"""


@pytest.mark.integration
def test_c_program_selects_after_images_and_its_first_object_fixes_it(tmp_path, pcc_runtime_archive):
    source = tmp_path / "fixed_backend.c"
    source.write_text(C_PROGRAM, encoding="utf-8")
    binary = tmp_path / "fixed_backend_c"
    compile_owned_c_with_runtime(source, binary, runtime_archive=Path(pcc_runtime_archive))
    for backend in range(5):
        for raw in (0, 1):
            ran = subprocess.run([str(binary), str(backend), str(raw)], capture_output=True,
                                 text=True, timeout=60, env=dict(os.environ, PATH="/nonexistent"))
            assert (ran.returncode, ran.stdout, ran.stderr) == (
                0, f"FIXED {backend} {raw}\n", ""), (backend, raw)
