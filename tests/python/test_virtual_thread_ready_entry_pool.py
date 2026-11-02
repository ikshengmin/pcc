from __future__ import annotations

import os
import subprocess
import textwrap
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).absolute().parents[2]
RUNTIME_DIR = REPO_ROOT / "pcc" / "runtime"


def _slice_between(src: str, start: str, end: str) -> str:
    start_idx = src.index(start)
    end_idx = src.index(end, start_idx)
    return src[start_idx:end_idx]


def _compile(tmp_path: Path, source: str, runtime_archive: Path) -> Path:
    src = tmp_path / "vthread_ready_entry_pool_probe.c"
    exe = tmp_path / "vthread_ready_entry_pool_probe.out"
    src.write_text(textwrap.dedent(source).lstrip(), encoding="utf-8")
    build = subprocess.run(
        [
            os.environ.get("CC", "cc"),
            "-std=c11",
            f"-I{RUNTIME_DIR / 'include'}",
            f"-I{RUNTIME_DIR / 'src'}",
            str(src),
            str(runtime_archive),
            "-pthread",
            "-lm",
            "-o",
            str(exe),
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert build.returncode == 0, build.stdout + build.stderr
    return exe


def test_virtual_thread_ready_entry_pool_preserves_roots_across_backends(
    tmp_path: Path, pcc_runtime_archive: Path,
) -> None:
    # The collector is fixed by the first managed allocation, so each one
    # runs in its own process, selected before anything is allocated.
    exe = _compile(
        tmp_path,
        """
        #include "py_runtime.h"
        #include "py_internal.h"
        #include <stdio.h>
        #include <stdlib.h>

        static int check_backend(int64_t backend) {
            if (pcc_gc_set_backend(backend) != 0) return 0;
            pcc_gc_telemetry_reset();
            if (pcc_gc_scheduler_root_count() != 0) return 0;

            for (int64_t i = 0; i < 192; i++) {
                PyObject *vt = py_virtual_thread_new(py_None);
                if (vt == 0) return 0;
                if (py_virtual_thread_start(vt) != 0) return 0;
                pcc_gc_release(vt);

                if (pcc_gc_scheduler_root_count() != 1) return 0;
                if (py_virtual_thread_ready_count() != 1) return 0;
                (void)pcc_gc_collect(0);

                PyObject *ready = py_virtual_thread_poll_ready();
                if (ready == 0) return 0;
                if (pcc_gc_scheduler_root_count() != 0) return 0;
                if (py_type_of(ready) != PY_TYPE_VIRTUAL_THREAD) return 0;
                if (py_virtual_thread_state(ready) != 2) return 0;
                if (py_virtual_thread_complete(ready, py_None) != 0) return 0;
                py_decref(ready);

                if (py_virtual_thread_ready_count() != 0) return 0;
                if (py_virtual_thread_timer_count() != 0) return 0;
                if (py_virtual_thread_io_wait_count() != 0) return 0;
                if (pcc_gc_scheduler_root_count() != 0) return 0;
            }
            if (py_virtual_thread_node_pool_stat(
                    PCC_VTHREAD_NODE_READY,
                    PCC_VTHREAD_POOL_ALLOCATIONS
                ) <= 0) return 0;
            if (py_virtual_thread_node_pool_stat(
                    PCC_VTHREAD_NODE_READY,
                    PCC_VTHREAD_POOL_REUSES
                ) <= 0) return 0;
            int64_t cached = py_virtual_thread_node_pool_stat(
                PCC_VTHREAD_NODE_READY,
                PCC_VTHREAD_POOL_CACHED
            );
            if (cached <= 0 || cached > 4096) return 0;
            return 1;
        }

        int main(int argc, char **argv) {
            if (argc != 2) return 2;
            int64_t backend = atoll(argv[1]);
            int ok = check_backend(backend);
            printf("%lld:%d\\n", (long long)backend, ok);
            return ok ? 0 : (int)(40 + backend);
        }
        """,
        pcc_runtime_archive,
    )
    for backend in range(5):
        proc = subprocess.run([str(exe), str(backend)], capture_output=True, text=True,
                              timeout=60)
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert proc.stdout.strip() == f"{backend}:1"


@pytest.mark.integration
def test_local_deque_overflow_and_queued_roots_on_five_collectors(
    tmp_path: Path, threaded_pcc_runtime_archive: Path,
) -> None:
    """C-linked runtime probe: one carrier deterministically fills its deque."""
    exe = _compile(tmp_path, r'''
        #define _DEFAULT_SOURCE 1
        #include "py_runtime.h"
        #include <stdint.h>
        #include <stdio.h>
        #include <stdlib.h>
        #include <unistd.h>

        extern int64_t py_virtual_thread_carrier_queue_depth(int64_t);
        /* Inspect the actual private layout accessor in this runtime probe. */
        extern void *user_py_virtual_thread_runtime__carrier_queue(int64_t);
        extern void *user_py_virtual_thread_runtime__node_pool_lock_word(int64_t);
        extern void *pcc_vthread_carrier_queues_py[];
        extern void *pcc_vthread_node_pool_locks_py[];
        enum { JOBS = 600 };
        static int seen[JOBS];
        static int executed, parent_done, failure;
        static int64_t parent_identity;

        static void fail(int code) {
            int expected = 0;
            __atomic_compare_exchange_n(&failure, &expected, code, 0,
                                        __ATOMIC_ACQ_REL, __ATOMIC_ACQUIRE);
        }

        static int start(void *resume) {
            int32_t frame_map[1] = {0};
            int result = -1;
            /* Raw C temporaries stay immobile until their queued root owns
               them; collection resumes after both local references retire. */
            (void)pcc_current_thread_id();
            pcc_thread_no_park_enter();
            PyObject *continuation = py_continuation_new_typed(frame_map, NULL, resume);
            if (continuation != NULL) {
                PyObject *thread = py_virtual_thread_new(continuation);
                py_decref(continuation);
                if (thread != NULL) {
                    result = (int)py_virtual_thread_start(thread);
                    py_decref(thread);
                }
            }
            pcc_thread_no_park_exit();
            return result;
        }

        static int64_t child(PyObject *thread, PyObject *continuation) {
            (void)continuation;
            int64_t index = py_virtual_thread_current_identity() - parent_identity - 1;
            if (index < 0 || index >= JOBS) fail(10);
            else if (__atomic_exchange_n(&seen[index], 1, __ATOMIC_ACQ_REL)) fail(11);
            __atomic_add_fetch(&executed, 1, __ATOMIC_ACQ_REL);
            return py_virtual_thread_complete(thread, py_None);
        }

        static int64_t parent(PyObject *thread, PyObject *continuation) {
            (void)continuation;
            parent_identity = py_virtual_thread_current_identity();
            for (int i = 0; i < JOBS; ++i) {
                if (start((void *)&child) != 0) { fail(12); break; }
            }
            /* No other carrier exists; none of these children can run yet. */
            if (py_virtual_thread_carrier_queue_depth(0) != 256) fail(13);
            if (py_virtual_thread_ready_count() != JOBS) fail(14);
            (void)pcc_gc_collect(0);
            __atomic_store_n(&parent_done, 1, __ATOMIC_RELEASE);
            return py_virtual_thread_complete(thread, py_None);
        }

        int main(int argc, char **argv) {
            if (argc != 2 || pcc_gc_set_backend(atoll(argv[1])) != 0) return 2;
            uintptr_t storage = (uintptr_t)pcc_vthread_carrier_queues_py;
            for (int i = 0; i < 64; ++i) {
                uintptr_t queue = (uintptr_t)user_py_virtual_thread_runtime__carrier_queue(i);
                if (queue % 128 || queue < storage || queue + 2176 > storage + 17424 * 8) return 19;
            }
            storage = (uintptr_t)pcc_vthread_node_pool_locks_py;
            for (int i = 1; i <= 3; ++i) {
                uintptr_t word = (uintptr_t)user_py_virtual_thread_runtime__node_pool_lock_word(i);
                if (word % 128 || word < storage || word + 8 > storage + 64 * 8) return 20;
            }
            if (start((void *)&parent) != 0) return 3;
            if (py_virtual_thread_carrier_pool_start(1) != 1) return 4;
            for (int i = 0; i < 20000; ++i) {
                if (__atomic_load_n(&failure, __ATOMIC_ACQUIRE)) break;
                if (__atomic_load_n(&parent_done, __ATOMIC_ACQUIRE)
                    && __atomic_load_n(&executed, __ATOMIC_ACQUIRE) == JOBS) break;
                pcc_thread_safepoint();
                usleep(100);
            }
            if (py_virtual_thread_carrier_pool_stop() != 1) return 5;
            if (failure) return failure;
            if (executed != JOBS || !parent_done) return 15;
            for (int i = 0; i < JOBS; ++i) if (seen[i] != 1) return 16;
            if (py_virtual_thread_ready_count() != 0) return 17;
            if (pcc_gc_scheduler_root_count() != 0) return 18;
            puts("deque-overflow-roots-ok");
            return 0;
        }
    ''', threaded_pcc_runtime_archive)
    for backend in range(5):
        result = subprocess.run([str(exe), str(backend)], capture_output=True,
                                text=True, timeout=30)
        assert (result.returncode, result.stdout, result.stderr) == (
            0, "deque-overflow-roots-ok\n", "",
        ), (backend, result.stdout, result.stderr)
