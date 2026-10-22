"""GC indexes stay readable without the graph lock while another thread grows them.

The GC4 load barrier (pcc_gc_load_ptr -> pcc_gc_backend4_slot_needs_resolve)
looks up the object and forwarding indexes without the object-graph lock, and
pcc_gc_pointer_is_managed asks the object index and the managed-pointer set
the same way (pcc_gc_index_py_seen). A rehash used to free the table it
replaced and to publish the new slots and capacity in two unordered stores,
so such a reader could probe freed memory or pair a table with another
table's capacity. Tables of those indexes are now retired, not freed, and
published with their capacity in one record that readers load once.

The harness links the freestanding index module alone: one writer thread
grows the object index and the managed-pointer set from empty through
several rehashes while reader threads look up keys that were inserted before
they started and keys that are never inserted. MallocScribble fills freed
memory, so a reader on a freed table reads garbage keys and either answers
wrongly or never meets an empty slot (the timeout).

A retired table is freed once no thread can be probing it: at once while the
process has one registered thread, else by the holder of a stopped world. The
runtime probe below checks both with the managed-pointer set.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from unittest.mock import patch

from pcc.frontends.python import pipeline
from pcc.frontends.python.pipeline_targets import host_target_triple

from tests.owned_c_execution import compile_owned_c_with_runtime


REPO_ROOT = Path(__file__).resolve().parents[2]
INDEX_SOURCE = REPO_ROOT / "pcc" / "runtime" / "py" / "freestanding_gc_index_table.py"

HARNESS = r"""
#include <pthread.h>
#include <stdatomic.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>

void *pcc_gc_object_index_find(void *obj);
int64_t pcc_gc_object_index_insert(void *obj, void *node);
void pcc_gc_object_index_clear(void);
int64_t pcc_gc_managed_pointer_index_insert(void *obj);
int64_t pcc_gc_object_index_seen(void *obj);
int64_t pcc_gc_managed_pointer_index_seen(void *obj);
int64_t pcc_gc_index_reclaim_retired(void);

/* The primary index asks the allocator about slab cells; these keys are not
   allocator cells. */
void *pcc_allocator_object_side_slot(void *ptr, int64_t create) {
    (void)ptr;
    (void)create;
    return NULL;
}

void *pcc_allocator_granule_object_slot(void *ptr) {
    (void)ptr;
    return NULL;
}

#define EARLY 2000
#define LATE 200000
#define READERS 4

static uintptr_t *keys;
static uintptr_t *nodes;
static atomic_int writer_done;
static atomic_long reader_rounds;
static atomic_int failure;

static void *reader(void *arg) {
    long id = (long)arg;
    long round = 0;
    while (atomic_load(&writer_done) == 0 || round < 4) {
        for (long i = id; i < EARLY; i += READERS) {
            void *key = (void *)keys[i];
            if (pcc_gc_object_index_find(key) != (void *)nodes[i]) {
                atomic_store(&failure, 1);
                return 0;
            }
            /* Present keys: no removal runs, so the probe finds them in
               the table it loaded, old or new. */
            if (pcc_gc_object_index_seen(key) != 1) {
                atomic_store(&failure, 2);
                return 0;
            }
            if (pcc_gc_managed_pointer_index_seen(key) != 1) {
                atomic_store(&failure, 3);
                return 0;
            }
            /* An address that is never inserted is never reported. */
            void *absent = (void *)(keys[i] + 8);
            if (pcc_gc_object_index_find(absent) != 0
                || pcc_gc_object_index_seen(absent) != 0
                || pcc_gc_managed_pointer_index_seen(absent) != 0) {
                atomic_store(&failure, 4);
                return 0;
            }
        }
        round = round + 1;
        atomic_fetch_add(&reader_rounds, 1);
    }
    return 0;
}

int main(void) {
    long total = EARLY + LATE;
    keys = calloc((size_t)total, sizeof(uintptr_t));
    nodes = calloc((size_t)total, sizeof(uintptr_t));
    char *arena = calloc((size_t)total, 16);
    if (keys == 0 || nodes == 0 || arena == 0) return 10;
    for (long i = 0; i < total; i++) {
        keys[i] = (uintptr_t)(arena + i * 16);
        nodes[i] = (uintptr_t)(0x100000 + i * 16);
    }
    for (long i = 0; i < EARLY; i++) {
        if (pcc_gc_object_index_insert((void *)keys[i], (void *)nodes[i]) != 1) return 11;
        if (pcc_gc_managed_pointer_index_insert((void *)keys[i]) != 1) return 12;
    }
    pthread_t threads[READERS];
    for (long r = 0; r < READERS; r++) {
        if (pthread_create(&threads[r], 0, reader, (void *)r) != 0) return 13;
    }
    while (atomic_load(&reader_rounds) < READERS) {
    }
    /* The single writer (production writers hold the graph lock) grows both
       tables through several rehashes while the readers probe. */
    for (long i = EARLY; i < total; i++) {
        if (pcc_gc_object_index_insert((void *)keys[i], (void *)nodes[i]) != 1) return 14;
        if (pcc_gc_managed_pointer_index_insert((void *)keys[i]) != 1) return 15;
    }
    atomic_store(&writer_done, 1);
    for (long r = 0; r < READERS; r++) {
        pthread_join(threads[r], 0);
    }
    if (atomic_load(&failure) != 0) return 20 + atomic_load(&failure);
    /* Every replaced table waited for the readers instead of being freed
       under them; with the readers joined they can go. */
    if (pcc_gc_index_reclaim_retired() < 4) return 30;
    if (pcc_gc_index_reclaim_retired() != 0) return 31;
    for (long i = 0; i < total; i += 997) {
        if (pcc_gc_object_index_find((void *)keys[i]) != (void *)nodes[i]) return 32;
    }
    printf("unlocked-readers-ok %ld\n", atomic_load(&reader_rounds) >= READERS ? 1L : 0L);
    return 0;
}
"""


def _index_object(tmp_path: Path) -> Path:
    llvm_ir = tmp_path / "freestanding_gc_index_table.ll"
    pipeline.compile_python(
        str(INDEX_SOURCE),
        str(llvm_ir),
        emit_llvm_only=True,
        libpython_mode="off",
        python_library=True,
        target_triple=host_target_triple(),
    )
    from pcc.tools.ir_to_obj import emit_object

    obj = tmp_path / "freestanding_gc_index_table.o"
    obj.write_bytes(emit_object(llvm_ir.read_text(encoding="utf-8"),
                                target_triple=host_target_triple()))
    return obj


def test_index_lookups_without_the_lock_survive_concurrent_growth(tmp_path: Path):
    from pcc import build

    harness = tmp_path / "unlocked_readers.c"
    harness.write_text(HARNESS, encoding="utf-8")
    # Link only this freestanding index and its C stubs. A complete runtime
    # would introduce conflicting allocator/index definitions and change the
    # concurrency experiment. Preprocessing, object emission and linking are
    # owned; any external build process or implicit runtime provision fails.
    with (
        patch("subprocess.Popen", side_effect=AssertionError("external build process")),
        patch("pcc.frontends.python.owned_runtime_build.ensure_target_runtime",
              side_effect=AssertionError("standalone index harness linked the full runtime")),
    ):
        obj = _index_object(tmp_path)
        artifact = build(
            harness, backend="self", kind="exe", optimize=1,
            include_dirs=[REPO_ROOT / "utils/fake_libc_include"],
            link_args=[str(obj)], out_dir=tmp_path / "owned",
            use_compile_cache=False,
        )
    executable = Path(artifact.output_path)
    env = {**os.environ, "MallocScribble": "1"}
    for attempt in range(3):
        run = subprocess.run([str(executable)], capture_output=True, text=True,
                             timeout=120, env=env)
        assert run.returncode == 0, f"attempt {attempt}: {run.returncode} {run.stderr[-2000:]}"
        assert run.stdout == "unlocked-readers-ok 1\n"


RECLAIM_PROBE = r"""
#include "py_internal.h"
#include <stdatomic.h>
#include <stdint.h>
#include <stdio.h>

extern void *pcc_py_gc_index_retired_tables;
extern int64_t pcc_py_gc_managed_pointer_cap;
extern int64_t pcc_gc_managed_pointer_index_seen(void *obj);
extern int64_t pcc_gc_reclaim_retired_index_tables(void);

/* Registered addresses only: the managed-pointer set never reads them. */
enum { ALONE = 6000, SHARED = 30000 };
static _Alignas(16) unsigned char fake_objects[(ALONE + SHARED) * 16];
static atomic_int release_worker;
static atomic_int worker_ready;

static int64_t retired_blocks(void) {
    int64_t count = 0;
    void **node = (void **)pcc_py_gc_index_retired_tables;
    while (node != 0) {
        count++;
        node = (void **)node[0];
    }
    return count;
}

static void *worker(void *unused) {
    (void)unused;
    atomic_store(&worker_ready, 1);
    while (atomic_load(&release_worker) == 0) {
        pcc_thread_safepoint();
    }
    return 0;
}

int main(void) {
    int64_t backend = pcc_gc_backend();
    int64_t cap_before = pcc_py_gc_managed_pointer_cap;
    /* One registered thread: every table the set outgrows is freed at once. */
    for (int i = 0; i < ALONE; i++) {
        PyObject *obj = (PyObject *)(fake_objects + i * 16);
        if (pcc_gc_pointer_register(obj) != 1) return 2;
        if (retired_blocks() != 0) return 3;
        if (pcc_gc_managed_pointer_index_seen(obj) != 1) return 4;
    }
    if (pcc_py_gc_managed_pointer_cap <= cap_before) return 5;

    PccThreadHandle *thread = 0;
    if (pcc_thread_start(&thread, worker, 0) != 0) return 6;
    while (atomic_load(&worker_ready) == 0) {
    }
    /* A second registered thread may be probing: replaced tables stay. */
    int64_t cap_shared = pcc_py_gc_managed_pointer_cap;
    for (int i = ALONE; i < ALONE + SHARED; i++) {
        if (pcc_gc_pointer_register((PyObject *)(fake_objects + i * 16)) != 1) return 7;
    }
    if (pcc_py_gc_managed_pointer_cap <= cap_shared) return 8;
    int64_t kept = retired_blocks();
    if (kept < 2) return 9;
    if (pcc_gc_reclaim_retired_index_tables() != -1) return 10;
    if (retired_blocks() != kept) return 11;
    /* The holder of a stopped world frees them. */
    if (pcc_stop_the_world() != 0) return 12;
    if (pcc_gc_reclaim_retired_index_tables() != kept) return 13;
    if (retired_blocks() != 0) return 14;
    if (pcc_resume_world() != 0) return 15;

    atomic_store(&release_worker, 1);
    void *result = 0;
    if (pcc_thread_join(thread, &result) != 0) return 16;
    for (int i = 0; i < ALONE + SHARED; i++) {
        PyObject *obj = (PyObject *)(fake_objects + i * 16);
        if (pcc_gc_managed_pointer_index_seen(obj) != 1) return 17;
        if (pcc_gc_pointer_unregister(obj) != 1) return 18;
    }
    printf("reclaim-ok %lld %lld\n", (long long)backend, (long long)kept);
    return 0;
}
"""


def test_retired_tables_are_freed_when_no_thread_can_probe_them(
    tmp_path: Path, pcc_runtime_archive,
):
    source = tmp_path / "index_retired_reclaim.c"
    source.write_text(RECLAIM_PROBE, encoding="utf-8")
    executable = tmp_path / "index_retired_reclaim"
    compile_owned_c_with_runtime(
        source, executable, runtime_archive=pcc_runtime_archive,
        cpp_args=("-I" + str(REPO_ROOT / "pcc/runtime/src"),),
    )
    for backend in (0, 4):
        run = subprocess.run([str(executable)], capture_output=True, text=True,
                             timeout=120, env={**os.environ, "PCC_GC_BACKEND": str(backend)})
        assert run.returncode == 0, f"GC{backend}: {run.returncode} {run.stderr[-2000:]}"
        assert run.stdout.startswith(f"reclaim-ok {backend} "), run.stdout
