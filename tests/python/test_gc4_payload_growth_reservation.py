"""Growing a GC4 virtual payload span cannot reclaim intervening objects.

Exercise all three public/internal registration paths through owned native C.
The graph lock makes the harmful allocation order deterministic; the original
multicarrier workload reaches the same order through ordinary container growth.
"""

import os
from pathlib import Path
import subprocess

import pytest

from tests.owned_c_execution import compile_owned_c_with_runtime

SOURCE = r'''
#include "py_runtime.h"
#include <stdint.h>
#include <stdlib.h>
#include <stdio.h>
extern void pcc_py_gc_minor_graph_lock(void);
extern void pcc_py_gc_minor_graph_unlock(void);
extern int64_t pcc_gc_backend4_retarget_mutator_payload_locked(void *, void *, int64_t, void *, int64_t, void *, int64_t);
int main(int argc, char **argv) {
    int mode = argc > 1 ? atoi(argv[1]) : 0;
    int scenario = argc > 2 ? atoi(argv[2]) : 1;
    int64_t new_size = scenario == 2 ? 8192 : 80;
    if (pcc_gc_set_backend(4)) return 10;
    py_gc_disable();
    pcc_py_gc_minor_graph_lock();
    PyObject *a = pcc_gc_alloc(40, PY_TYPE_LIST, 0);
    void *payload = malloc(new_size);
    void *new_payload = malloc(new_size);
    if (!a || !payload || !new_payload) return 11;
    if (pcc_gc_backend4_zpage_register_owner_payload_span(a, payload, 16) < 0) return 12;
    /* An intervening allocation makes this payload a non-tail reservation. */
    PyObject *b = scenario == 0 ? 0 : pcc_gc_alloc(64, PY_TYPE_LIST, 0);
    if (scenario != 0 && !b) return 13;
    int64_t grew;
    if (mode == 0) grew = pcc_gc_backend4_zpage_register_owner_payload_span(a, payload, new_size);
    else if (mode == 1) grew = pcc_gc_backend4_zpage_retarget_owner_payload_span(a, payload, new_payload, new_size);
    else grew = pcc_gc_backend4_retarget_mutator_payload_locked(a, payload, 16, new_payload, new_size, 0, 0);
    if (grew < 0 || (mode == 2 && grew == 0)) return 14;
    if (scenario == 0) b = pcc_gc_alloc(64, PY_TYPE_LIST, 0);
    if (!b) return 13;
    py_decref(a);
    PyObject *c = pcc_gc_alloc(64, PY_TYPE_LIST, 0);
    if (!c) return 15;
    uintptr_t start = (uintptr_t)c, live = (uintptr_t)b;
    if (start < live + 64 && start + 64 > live) {
        puts("LIVE_OBJECT_OVERWRITTEN");
        return 20;
    }
    if (((PyObjectHeader *)b)->refcount != 1 || ((PyObjectHeader *)b)->type_tag != PY_TYPE_LIST) return 21;
    py_decref(c);
    py_decref(b);
    free(new_payload);
    free(payload);
    pcc_py_gc_minor_graph_unlock();
    puts("PAYLOAD_GROWTH_OK");
    return 0;
}
'''


@pytest.mark.parametrize("mode", [0, 1, 2], ids=["register", "retarget", "mutator"])
def test_payload_growth_preserves_intervening_native_objects(
    tmp_path, pcc_runtime_archive, mode,
):
    source = tmp_path / "payload_growth.c"
    source.write_text(SOURCE, encoding="utf-8")
    binary = tmp_path / "payload_growth"
    compile_owned_c_with_runtime(
        source, binary, runtime_archive=Path(pcc_runtime_archive),
    )
    # The failing non-tail shape, valid tail extension, and existing external
    # overflow representation must all retain the neighbouring live object.
    for scenario in (1, 0, 2):
        result = subprocess.run(
            [str(binary), str(mode), str(scenario)],
            capture_output=True, text=True, timeout=15,
            env=dict(os.environ, PATH="/nonexistent"),
        )
        assert (result.returncode, result.stdout, result.stderr) == (
            0, "PAYLOAD_GROWTH_OK\n", "",
        ), (mode, scenario, result.stdout, result.stderr)
