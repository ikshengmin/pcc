"""GC1/2 mutator consumers wait for a real managed producer's gray tail.

This is a native runtime component test of the queue-only slice described in
docs/plans/p0-threaded-concurrent-gc-runtime.md. Host pcc owns the C frontend,
emitter and linker; the admitted archive supplies the unmodified runtime.
The selected collector is fixed by real list allocation, never synthesized by
changing the backend or replacing membership, shading or tracing functions.

For deterministic interleaving, the existing worker-stop API holds CMS paused
and the harness stages an active mark after stopped-world whitening. Two real
objects are shaded on a registered producer. Its explicitly permitted kernel
safepoint publishes them while it remains alive. Consuming just one leaves a
real gray credit, so this does not test seed/remark/sweep, complete collection,
generated implicit-poll insertion, native-pcc1 acceptance or performance.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import platform
import sys

import pytest

from tests.python.process_timeout import run_process_group_timeout


ROOT = Path(__file__).resolve().parents[2]

SOURCE = r'''
#include "py_runtime.h"
#include <stdint.h>
#include <stdio.h>

extern int64_t pcc_thread_start(void **, void *(*)(void *), void *);
extern int64_t pcc_thread_join(void *, void **);
extern int64_t pcc_gc_cms_stop_worker(void);
extern int64_t pcc_gc_is_cms_worker(void);
extern int64_t pcc_gc_incremental_concurrent_step(int64_t);
extern void pcc_gc_cms_note_alloc(int64_t);
extern int64_t pcc_gc_tracing_debt_threshold(void);
extern void pcc_gc_prepare_object_list_mark(int64_t);
extern int64_t pcc_gc_object_is_known_no_lock(PyObject *);
extern void *pcc_gc_object_index_find(PyObject *);
extern void pcc_gc_shade_gray_atomic(PyObject *, int64_t);
extern int64_t pcc_gc_gray_count_load_acquire(void);
extern int32_t pcc_gc_nonmoving_count_free, pcc_gc_mark_active;
extern int32_t pcc_gc_cycle_requested, pcc_gc_trace_extension_roots_pending;
extern int32_t pcc_gc_cms_worker_started, pcc_gc_cms_mutator_assists;
extern int32_t pcc_gc_cms_finish_pending, pcc_gc_cms_sweep_pending;
extern int32_t pcc_gc_gray_worklist_fallback_required, pcc_gc_stepmul;
extern int64_t pcc_gc_tracing_cycle_epoch, pcc_gc_tracing_finish_claim_epoch;
extern int64_t pcc_gc_tracing_finish_commits, pcc_gc_debt_bytes;
extern int64_t pcc_gc_debt_threshold_override;
extern void *pcc_gc_trace_cursor, *pcc_gc_trace_cext_pending_obj;
extern void *pcc_gc_gray_published_batches, *pcc_gc_gray_pending_batches;

#define CHECK(condition, code) do { if (!(condition)) { \
    printf("MUTATOR_GRAY_FAIL %d\n", code); return code; } } while (0)

enum { WHITE = 8, GRAY = 16, BLACK = 32, COLOR_MASK = 56 };
static PyObject *objects[2];
static int32_t ready, allow_poll, polled, allow_exit, exited, failure;

static uintptr_t published(void) {
    return __atomic_load_n((uintptr_t *)&pcc_gc_gray_published_batches,
                           __ATOMIC_ACQUIRE);
}

static int color(PyObject *object) {
    return __atomic_load_n(&((PyObjectHeader *)object)->flags, __ATOMIC_ACQUIRE)
        & COLOR_MASK;
}

static int wait_for(int32_t *slot) {
    int64_t deadline = pcc_runtime_monotonic_us() + 5000000;
    while (__atomic_load_n(slot, __ATOMIC_ACQUIRE) == 0) {
        if (pcc_runtime_monotonic_us() >= deadline) return 0;
    }
    return 1;
}

static void *producer(void *unused) {
    (void)unused;
    /* Shade under the real no-park lease, as a container barrier does.
       No request exists yet, so leaving the lease does not publish. */
    pcc_thread_no_park_enter();
    pcc_gc_shade_gray_atomic(objects[0], 0);
    pcc_gc_shade_gray_atomic(objects[1], 0);
    pcc_thread_no_park_exit();
    __atomic_store_n(&ready, 1, __ATOMIC_RELEASE);
    if (!wait_for(&allow_poll)) {
        __atomic_store_n(&failure, 1, __ATOMIC_RELEASE);
        return 0;
    }
    if (pcc_thread_stop_requested_acquire() != 0
            || pcc_thread_no_park_depth() != 0
            || __atomic_load_n(&pcc_gc_gray_worklist_flush_requested,
                               __ATOMIC_ACQUIRE) != 1) {
        __atomic_store_n(&failure, 2, __ATOMIC_RELEASE);
        return 0;
    }
    /* The real kernel poll, not direct worklist_flush or thread teardown. */
    pcc_thread_safepoint();
    __atomic_store_n(&polled, 1, __ATOMIC_RELEASE);
    if (!wait_for(&allow_exit)) {
        __atomic_store_n(&failure, 3, __ATOMIC_RELEASE);
        return 0;
    }
    __atomic_store_n(&exited, 1, __ATOMIC_RELEASE);
    return 0;
}

static int cycle_unchanged(int64_t epoch, int64_t commits, void *cursor) {
    return pcc_gc_mark_active == 1 && pcc_gc_cycle_requested == 1
        && pcc_gc_tracing_cycle_epoch == epoch
        && pcc_gc_tracing_finish_commits == commits
        && pcc_gc_tracing_finish_claim_epoch == 0
        && pcc_gc_cms_finish_pending == 0 && pcc_gc_cms_sweep_pending == 0
        && pcc_gc_trace_cursor == cursor;
}

int main(int argc, char **argv) {
    CHECK(argc == 2, 1);
    int backend = argv[1][0] - '0';
    CHECK((backend == 1 || backend == 2) && pcc_gc_backend() == backend, 2);
    CHECK(pcc_threads_enabled() == 1 && pcc_gc_is_cms_worker() == 0, 3);
    py_gc_disable();
    CHECK(pcc_gc_cms_stop_worker() == 0, 4);
    /* Lifecycle 4 prevents a new CMS worker, without changing GC policy. */
    CHECK(__atomic_load_n(&pcc_gc_cms_worker_started, __ATOMIC_ACQUIRE) == 4, 5);
    pcc_current_thread_id();
    objects[0] = py_list_new(0);
    objects[1] = py_list_new(0);
    CHECK(objects[0] && objects[1] && objects[0] != objects[1], 6);
    CHECK(pcc_gc_backend() == backend && pcc_gc_nonmoving_count_free == 1, 7);
    CHECK(pcc_gc_pointer_is_managed(objects[0]) == 1
          && pcc_gc_pointer_is_managed(objects[1]) == 1, 8);
    CHECK(pcc_gc_stepmul == 100, 9);

    CHECK(pcc_stop_the_world() == 0, 10);
    pcc_py_gc_minor_graph_lock();
    CHECK(pcc_gc_mark_active == 0 && pcc_gc_tracing_finish_claim_epoch == 0
          && pcc_gc_trace_extension_roots_pending == 0
          && pcc_gc_trace_cext_pending_obj == 0, 11);
    pcc_gc_prepare_object_list_mark(1);
    CHECK(pcc_gc_object_is_known_no_lock(objects[0]) == 1
          && pcc_gc_object_is_known_no_lock(objects[1]) == 1, 12);
    void *cursor = pcc_gc_object_index_find(objects[0]);
    CHECK(cursor != 0 && *(PyObject **)cursor == objects[0], 13);
    CHECK(color(objects[0]) == WHITE && color(objects[1]) == WHITE, 14);
    int64_t epoch = pcc_gc_tracing_cycle_epoch + 1;
    int64_t commits = pcc_gc_tracing_finish_commits;
    pcc_gc_tracing_cycle_epoch = epoch;
    pcc_gc_mark_active = 1;
    pcc_gc_cycle_requested = 1;
    /* Startup used a high threshold; lower it only with CMS already joined. */
    pcc_gc_debt_threshold_override = 1024;
    /* A real node that the old cursor consumer would immediately process. */
    pcc_gc_trace_cursor = cursor;
    __atomic_store_n(&pcc_gc_debt_bytes, 4096, __ATOMIC_RELEASE);
    pcc_py_gc_minor_graph_unlock();
    CHECK(pcc_resume_world() == 0 && pcc_thread_owns_stopped_world() == 0, 15);
    py_gc_set_threshold(700, 10, 10);
    py_gc_enable();
    CHECK(py_gc_is_enabled() == 1 && py_gc_get_threshold(0) == 700
          && pcc_gc_tracing_debt_threshold() == 1024, 37);

    void *thread = 0;
    CHECK(pcc_thread_start(&thread, producer, 0) == 0, 16);
    CHECK(wait_for(&ready) && __atomic_load_n(&failure, __ATOMIC_ACQUIRE) == 0, 17);
    CHECK(pcc_gc_gray_count_load_acquire() == 2
          && color(objects[0]) == GRAY && color(objects[1]) == GRAY, 18);
    CHECK(published() == 0
          && pcc_gc_gray_pending_batches == 0
          && __atomic_load_n(&pcc_gc_gray_worklist_fallback_required,
                             __ATOMIC_ACQUIRE) == 0, 19);

    /* The wrapper returns real completed objects and discharges real debt.
       A hidden tail must yield zero, not scan the real cursor or finish. */
    CHECK(pcc_gc_incremental_concurrent_step(1) == 0, 20);
    CHECK(cycle_unchanged(epoch, commits, cursor)
          && pcc_gc_gray_count_load_acquire() == 2
          && color(objects[0]) == GRAY && color(objects[1]) == GRAY, 21);
    CHECK(__atomic_load_n(&pcc_gc_debt_bytes, __ATOMIC_ACQUIRE) == 4096, 22);
    CHECK(__atomic_load_n(&pcc_gc_gray_worklist_flush_requested,
                         __ATOMIC_ACQUIRE) == 1, 23);
    int32_t assists = pcc_gc_cms_mutator_assists;
    pcc_gc_cms_note_alloc(7);
    CHECK(pcc_gc_cms_mutator_assists == assists + 1, 24);
    CHECK(__atomic_load_n(&pcc_gc_debt_bytes, __ATOMIC_ACQUIRE) == 4103
          && pcc_gc_gray_count_load_acquire() == 2
          && cycle_unchanged(epoch, commits, cursor), 25);
    CHECK(published() == 0
          && __atomic_load_n(&polled, __ATOMIC_ACQUIRE) == 0
          && pcc_thread_stop_requested_acquire() == 0, 26);

    __atomic_store_n(&allow_poll, 1, __ATOMIC_RELEASE);
    CHECK(wait_for(&polled) && __atomic_load_n(&failure, __ATOMIC_ACQUIRE) == 0, 27);
    CHECK(__atomic_load_n(&exited, __ATOMIC_ACQUIRE) == 0
          && published() != 0
          && __atomic_load_n(&pcc_gc_gray_worklist_flush_requested,
                             __ATOMIC_ACQUIRE) == 1, 28);
    CHECK(pcc_gc_incremental_concurrent_step(1) == 1, 29);
    CHECK(color(objects[0]) == BLACK && color(objects[1]) == GRAY
          && pcc_gc_gray_count_load_acquire() == 1, 30);
    CHECK(__atomic_load_n(&pcc_gc_debt_bytes, __ATOMIC_ACQUIRE) == 4039
          && cycle_unchanged(epoch, commits, cursor), 31);
    CHECK(__atomic_load_n(&pcc_gc_gray_worklist_flush_requested,
                         __ATOMIC_ACQUIRE) == 0
          && __atomic_load_n(&exited, __ATOMIC_ACQUIRE) == 0
          && pcc_thread_stop_requested_acquire() == 0, 32);
    CHECK(__atomic_load_n(&pcc_gc_cms_worker_started, __ATOMIC_ACQUIRE) == 4
          && pcc_gc_backend() == backend && pcc_gc_is_cms_worker() == 0, 33);

    __atomic_store_n(&allow_exit, 1, __ATOMIC_RELEASE);
    CHECK(pcc_thread_join(thread, 0) == 0
          && __atomic_load_n(&exited, __ATOMIC_ACQUIRE) == 1
          && __atomic_load_n(&failure, __ATOMIC_ACQUIRE) == 0, 34);
    /* Tear down staged state without claiming or executing a collection. */
    py_gc_disable();
    CHECK(pcc_stop_the_world() == 0, 35);
    pcc_py_gc_minor_graph_lock();
    pcc_gc_prepare_object_list_mark(1);
    pcc_gc_mark_active = 0;
    pcc_gc_cycle_requested = 0;
    pcc_gc_trace_cursor = 0;
    pcc_py_gc_minor_graph_unlock();
    CHECK(pcc_resume_world() == 0, 36);
    printf("MUTATOR_GRAY_OK gc=%d hidden=2 wait=0 assist_debt=4103 traced=1 remaining=1\n",
           backend);
    return 0;
}
'''


def _linux_x86_64():
    return platform.system() == "Linux" and platform.machine().lower() in ("x86_64", "amd64")


def _guarded_run(tmp_path, name, command, *, timeout, env):
    """Reuse the repository's process-tree cap, with no shared build lock."""
    result_path = tmp_path / f"{name}.json"
    stdout_path = tmp_path / f"{name}.stdout"
    stderr_path = tmp_path / f"{name}.stderr"
    result = run_process_group_timeout(
        [sys.executable, str(ROOT / "scripts/run_process_tree_sample.py"),
         "--result", str(result_path), "--samples", str(tmp_path / f"{name}.tsv"),
         "--stdout", str(stdout_path), "--stderr", str(stderr_path),
         "--cwd", str(ROOT), "--timeout", str(timeout),
         "--max-tree-rss-bytes", str(1024 ** 3), "--no-performance-lock",
         "--", *command],
        timeout=timeout + 10, cwd=ROOT, env=env,
    )
    stdout = stdout_path.read_text() if stdout_path.exists() else ""
    stderr = stderr_path.read_text() if stderr_path.exists() else ""
    assert result.returncode == 0, result.stdout + result.stderr + stdout + stderr
    receipt = json.loads(result_path.read_text())
    assert (receipt["status"], receipt["returncode"]) == ("COMPLETE", 0), receipt
    return stdout, stderr


@pytest.mark.integration
@pytest.mark.pcc_gate(probe=_linux_x86_64)
def test_mutator_waits_for_managed_gray_tail_then_traces_published_object(
    tmp_path, threaded_pcc_runtime_archive,
):
    source = tmp_path / "mutator_gray_queue.c"
    source.write_text(SOURCE, encoding="utf-8")
    binary = tmp_path / "mutator_gray_queue"
    # The supervisor launches host Python, never cc/clang. The existing owned
    # helper forbids compiler/linker subprocesses and revalidates the archive.
    compile_code = (
        "from pathlib import Path; import sys; "
        "from tests.owned_c_execution import compile_owned_c_with_runtime; "
        "compile_owned_c_with_runtime(Path(sys.argv[1]), Path(sys.argv[2]), "
        "runtime_archive=Path(sys.argv[3]))"
    )
    _guarded_run(
        tmp_path, "compile",
        [sys.executable, "-c", compile_code, str(source), str(binary),
         str(threaded_pcc_runtime_archive)],
        timeout=90, env=dict(os.environ),
    )
    identity = {
        "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "binary_sha256": hashlib.sha256(binary.read_bytes()).hexdigest(),
        "runtime_sha256": hashlib.sha256(threaded_pcc_runtime_archive.read_bytes()).hexdigest(),
        "scope": "staged GC1/2 runtime component; not a complete collection",
    }
    (tmp_path / "identity.json").write_text(json.dumps(identity, indent=2) + "\n")
    for backend in (1, 2):
        stdout, stderr = _guarded_run(
            tmp_path, f"gc{backend}", [str(binary), str(backend)], timeout=60,
            env=dict(os.environ, PCC_GC_BACKEND=str(backend), PCC_THREADS="1",
                     PCC_REFCOUNT_KIND="atomic", PCC_GC_DEBT_THRESHOLD="1073741824",
                     PCC_GC_STEP_MUL="100"),
        )
        assert (stdout, stderr) == (
            f"MUTATOR_GRAY_OK gc={backend} hidden=2 wait=0 assist_debt=4103 traced=1 remaining=1\n",
            "",
        ), backend
