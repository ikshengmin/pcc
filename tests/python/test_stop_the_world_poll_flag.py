"""The owner of a stopped world polls on the fast path.

pcc_thread_stop_requested is the flag compiled code and runtime loops poll.
It stayed set for the whole stopped world, so every poll the owner made while
collecting (one per loop iteration of the runtime code a collection runs)
called pcc_thread_safepoint, which looked up two thread-locals to find that
the owner owns the stop: 31% of GC4's samples in st_alloc. The poll flag is
now cleared once every other mutator is parked or exited, and the stopped
world is published separately (pcc_thread_world_stop_py, read by
pcc_thread_stop_requested_acquire); a thread leaving an exited wait or
registering waits on the latter under the world lock.

The probe checks both flags while the main thread holds the world, that a
polling worker stays parked until the resume, and that it runs again after.
"""
from pathlib import Path
import os
import subprocess

import pytest

from _gc_substrate_common import _compile_runtime_probe


PROBE = r'''
#include "py_internal.h"
#include <sched.h>
#include <stdatomic.h>
#include <stdint.h>
#include <stdio.h>
#include <unistd.h>

extern int32_t pcc_thread_stop_requested;
extern int64_t pcc_thread_stop_requested_acquire(void);

static atomic_long progress;
static atomic_int finish;

static void *worker(void *unused) {
    (void)unused;
    while (atomic_load(&finish) == 0) {
        atomic_fetch_add(&progress, 1);
        pcc_thread_safepoint();
    }
    return 0;
}

int main(void) {
    if (pcc_current_thread_id() <= 0) return 2;
    PccThreadHandle *thread = 0;
    if (pcc_thread_start(&thread, worker, 0) != 0) return 3;
    while (atomic_load(&progress) < 1000) sched_yield();
    for (int round = 0; round < 50; round++) {
        if (pcc_stop_the_world() != 0) return 4;
        /* Every other mutator is parked: the owner's polls are fast. */
        if (__atomic_load_n(&pcc_thread_stop_requested, __ATOMIC_ACQUIRE) != 0) return 5;
        if (pcc_thread_stop_requested_acquire() != 1) return 6;
        if (pcc_thread_owns_stopped_world() != 1) return 7;
        pcc_thread_safepoint();
        long seen = atomic_load(&progress);
        usleep(2000);
        /* The worker stays parked although the poll flag is clear. */
        if (atomic_load(&progress) != seen) return 8;
        if (pcc_resume_world() != 0) return 9;
        if (pcc_thread_stop_requested_acquire() != 0) return 10;
        while (atomic_load(&progress) == seen) sched_yield();
    }
    atomic_store(&finish, 1);
    void *result = 0;
    if (pcc_thread_join(thread, &result) != 0) return 11;
    printf("poll-flag-ok\n");
    return 0;
}
'''


@pytest.mark.parametrize("backend", (0, 4))
def test_owner_polls_fast_while_the_world_stays_stopped(tmp_path: Path, backend: int):
    executable = _compile_runtime_probe(
        tmp_path, kind="pcc_python", threaded=True,
        stem="stop_the_world_poll_flag", source_text=PROBE,
    )
    env = {**os.environ, "PCC_GC_BACKEND": str(backend)}
    run = subprocess.run([str(executable)], env=env, capture_output=True,
                         text=True, timeout=60)
    assert run.returncode == 0, f"GC{backend}: {run.returncode} {run.stderr[-2000:]}"
    assert run.stdout == "poll-flag-ok\n"
