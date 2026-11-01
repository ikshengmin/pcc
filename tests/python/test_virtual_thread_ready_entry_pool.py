from __future__ import annotations

import os
import subprocess
import textwrap
from pathlib import Path

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
