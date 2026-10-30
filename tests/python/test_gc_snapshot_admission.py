"""A tracked snapshot must not acquire an owner after terminal release.

The C probe freezes real, rooted lists at the pre-untrack boundary. It checks
zero refcount and DEALLOCATING separately, and keeps a positive-refcount
FINALIZED object visible: resurrection must not be filtered out. GC4 currently
bypasses this tracked table and therefore has an independently empty result.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest

from pcc.runtime.py.py_abi_constants import (
    PY_FLAG_FINALIZED,
    PY_FLAG_GC_DEALLOCATING,
)
from tests.owned_c_execution import compile_owned_c_with_runtime
from tests.python.process_timeout import run_process_group_timeout


C_SOURCE = r'''
#include "py_runtime.h"
#include <stdint.h>
#include <stdio.h>

/* Filled from pcc.runtime.py.py_abi_constants, the generated public ABI owner.
 * FINALIZED and DEALLOCATING are different states: a live resurrected object
 * may retain FINALIZED, whereas terminal teardown must reject new owners. */
#define TEST_PY_FLAG_FINALIZED @FINALIZED@
#define TEST_PY_FLAG_GC_DEALLOCATING @DEALLOCATING@
extern void pcc_gc_default_table_lock(void);
extern void pcc_gc_default_table_unlock(void);

enum { ZERO_OWNER = 0, DEALLOCATING_OWNER = 1, FINALIZED_OWNER = 2, SNAPSHOT = 3, ROOTS = 4 };
static PyObject *roots[ROOTS];

static int contains_identity(PyObject *values, PyObject *target) {
    int64_t length = py_list_len(values);
    for (int64_t i = 0; i < length; i++) {
        /* Public getter returns a NEW owner, released exactly once here. */
        PyObject *item = py_list_get(values, i);
        if (!item) return -1;
        int same = item == target;
        py_decref(item);
        if (same) return 1;
    }
    return 0;
}

static int fail_stopped(int code, const char *message) {
    fprintf(stderr, "SNAPSHOT_ADMISSION_FAIL %d %s\n", code, message);
    /* A pre-fix snapshot can resurrect/release a fabricated terminal object.
       Exit the process while STW remains owned, without resuming mutators or
       attempting cleanup through potentially invalid object addresses. */
    return code;
}

int main(void) {
    int backend = (int)pcc_gc_backend();
    if (backend < 0 || backend > 4 || pcc_threads_enabled() != 1) return 1;
    py_gc_enable();
    py_gc_set_threshold(0, 10, 10);
    int32_t map = ROOTS;
    pcc_gc_frame_enter(&map, roots);
    for (int i = 0; i < SNAPSHOT; i++) {
        roots[i] = py_list_new(0);
        if (!roots[i]) return 2;
        pcc_gc_pin(roots[i]);
        if (py_gc_is_tracked(roots[i]) != (backend != 4)) return 3;
    }
    while (pcc_stop_the_world() != 0) pcc_thread_safepoint();
    /* Collector lock order: stopped world, then the all-shard table lock.
       get_objects must borrow both owners rather than drop the outer ones. */
    pcc_gc_default_table_lock();
    PyObjectHeader *zero_header = (PyObjectHeader *)roots[ZERO_OWNER];
    PyObjectHeader *dealloc_header = (PyObjectHeader *)roots[DEALLOCATING_OWNER];
    PyObjectHeader *finalized_header = (PyObjectHeader *)roots[FINALIZED_OWNER];
    int64_t zero_saved_refs = __atomic_load_n(&zero_header->refcount, __ATOMIC_ACQUIRE);
    if (zero_saved_refs <= 0
            || __atomic_load_n(&dealloc_header->refcount, __ATOMIC_ACQUIRE) <= 0
            || __atomic_load_n(&finalized_header->refcount, __ATOMIC_ACQUIRE) <= 0)
        return fail_stopped(10, "invalid initial owner");
    if (__atomic_load_n(&dealloc_header->flags, __ATOMIC_ACQUIRE)
            & TEST_PY_FLAG_GC_DEALLOCATING)
        return fail_stopped(11, "already deallocating");

    __atomic_store_n(&zero_header->refcount, 0, __ATOMIC_RELEASE);
    __atomic_fetch_or(&dealloc_header->flags, TEST_PY_FLAG_GC_DEALLOCATING, __ATOMIC_ACQ_REL);
    __atomic_fetch_or(&finalized_header->flags, TEST_PY_FLAG_FINALIZED, __ATOMIC_ACQ_REL);
    roots[SNAPSHOT] = py_gc_get_objects();
    if (!roots[SNAPSHOT]) return fail_stopped(12, "snapshot allocation");
    int zero_seen = contains_identity(roots[SNAPSHOT], roots[ZERO_OWNER]);
    int dealloc_seen = contains_identity(roots[SNAPSHOT], roots[DEALLOCATING_OWNER]);
    int finalized_seen = contains_identity(roots[SNAPSHOT], roots[FINALIZED_OWNER]);
    if (zero_seen != 0 || dealloc_seen != 0 || finalized_seen != (backend != 4))
        return fail_stopped(13, "terminal admission or finalized exclusion");
    if (!pcc_thread_owns_stopped_world())
        return fail_stopped(14, "snapshot resumed borrowed world");
    if (__atomic_load_n(&zero_header->refcount, __ATOMIC_ACQUIRE) != 0)
        return fail_stopped(15, "terminal refcount changed");
    if ((__atomic_load_n(&finalized_header->flags, __ATOMIC_ACQUIRE)
            & TEST_PY_FLAG_FINALIZED) == 0)
        return fail_stopped(16, "FINALIZED was cleared");

    /* Restore fabricated terminal state before releasing the table/world.
       Keep FINALIZED set throughout cleanup; it was never a rejection bit. */
    __atomic_store_n(&zero_header->refcount, zero_saved_refs, __ATOMIC_RELEASE);
    __atomic_fetch_and(&dealloc_header->flags, ~TEST_PY_FLAG_GC_DEALLOCATING, __ATOMIC_ACQ_REL);
    pcc_gc_store_root(&roots[SNAPSHOT], 0);
    pcc_gc_default_table_unlock();
    if (!pcc_thread_owns_stopped_world()) return 17;
    pcc_resume_world();
    for (int i = SNAPSHOT - 1; i >= 0; i--) {
        pcc_gc_unpin(roots[i]);
        pcc_gc_store_root(&roots[i], 0);
    }
    pcc_gc_frame_leave(roots);
    printf("SNAPSHOT_ADMISSION_OK gc=%d zero=0 deallocating=0 finalized=%d\n",
           backend, backend != 4);
    return 0;
}
'''


@pytest.mark.integration
def test_snapshot_rejects_terminal_owners_but_keeps_finalized_live_objects(
    tmp_path: Path, threaded_pcc_runtime_archive,
):
    source = tmp_path / "snapshot_admission.c"
    source.write_text(C_SOURCE.replace("@FINALIZED@", str(PY_FLAG_FINALIZED))
                      .replace("@DEALLOCATING@", str(PY_FLAG_GC_DEALLOCATING)))
    binary = tmp_path / "snapshot_admission"
    compile_owned_c_with_runtime(source, binary, runtime_archive=threaded_pcc_runtime_archive)
    receipt = {"source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
               "binary_sha256": hashlib.sha256(binary.read_bytes()).hexdigest(),
               "runtime": str(threaded_pcc_runtime_archive), "runs": []}
    for backend in range(5):
        result = run_process_group_timeout(
            [str(binary)], timeout=30,
            env=dict(os.environ, PCC_GC_BACKEND=str(backend), PCC_THREADS="1",
                     PCC_REFCOUNT_KIND="atomic", PATH="/nonexistent"),
        )
        (tmp_path / f"gc{backend}.stdout").write_text(result.stdout)
        (tmp_path / f"gc{backend}.stderr").write_text(result.stderr)
        receipt["runs"].append({"backend": backend, "returncode": result.returncode,
                                "stdout": result.stdout, "stderr": result.stderr})
        (tmp_path / "snapshot-admission.json").write_text(json.dumps(receipt, indent=2) + "\n")
        expected = (f"SNAPSHOT_ADMISSION_OK gc={backend} zero=0 deallocating=0 "
                    f"finalized={int(backend != 4)}\n")
        assert (result.returncode, result.stdout, result.stderr) == (0, expected, ""), backend
