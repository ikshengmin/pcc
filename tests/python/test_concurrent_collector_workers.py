"""Owned-native witnesses for concurrent mark work, not concurrent relocation.

Two distinct worker progress observations within one unchanged stop epoch
exclude a delayed counter publication from a preceding stopped-world batch.
"""
import os
from pathlib import Path
import subprocess

import pytest


PROBE = r'''
#include "py_internal.h"
#include <stdint.h>
#include <pthread.h>
#include <stdio.h>
extern int64_t pcc_stop_epoch_py;
extern void *pcc_world_lock_py;
extern int32_t pcc_gc_cms_worker_traces;
extern int64_t pcc_platform_monotonic_us(void);

static int64_t stop_epoch(void) {
    pthread_mutex_lock(pcc_world_lock_py);
    int64_t epoch = pcc_stop_epoch_py;
    pthread_mutex_unlock(pcc_world_lock_py);
    return epoch;
}

int main(void) {
    if (pcc_threads_enabled() != 1) return 2;
    int backend = pcc_gc_backend();
    if (backend < 1 || backend > 4) return 3;
    py_gc_disable();
    PyObject *root = py_list_new(0);
    if (root == 0) return 4;
    void *handle = pcc_gc_scheduler_root_register_handle(&root);
    if (handle == 0) return 5;
    pcc_gc_pin(root);
    for (int i = 0; i < 8192; i++) {
        PyObject *child = py_list_new(0);
        if (child == 0) return 6;
        /* A retained cyclic graph requires real referent traversal. */
        py_list_append(child, child);
        py_list_append(root, child);
        py_decref(child);
        if (py_err_occurred() != 0) return 7;
    }
    /* GC1/2 exempt newly allocated objects from their first automatic mark.
       Explicitly age the retained graph, then create fresh allocation debt. */
    pcc_gc_collect(0);
    for (int i = 0; i < 4096; i++) {
        PyObject *temporary = py_list_new(0);
        if (temporary == 0) return 14;
        py_decref(temporary);
    }
    int64_t epoch = stop_epoch();
    int64_t traces = __atomic_load_n(&pcc_gc_cms_worker_traces, __ATOMIC_ACQUIRE);
    int64_t trace_events[64][5];
    int event_count = 0;
    int observations = 0;
    int mutations = 0;
    int mutations_at_observation = 0;
    int proved = 0;
    int64_t until = pcc_platform_monotonic_us() + 5000000;
    py_gc_enable();
    while (pcc_platform_monotonic_us() < until) {
        /* Real heap mutation between observations, with no collector assist. */
        PyObject *value = py_int_from_i64(mutations & 1);
        py_list_set(root, 0, value);
        py_decref(value);
        if (py_err_occurred() != 0) return 8;
        mutations++;
        int64_t next_epoch = stop_epoch();
        int64_t next_traces = __atomic_load_n(&pcc_gc_cms_worker_traces, __ATOMIC_ACQUIRE);
        int64_t confirm_epoch = stop_epoch();
        if ((epoch != confirm_epoch || next_traces != traces) && event_count < 64) {
            trace_events[event_count][0] = epoch;
            trace_events[event_count][1] = confirm_epoch;
            trace_events[event_count][2] = traces;
            trace_events[event_count][3] = next_traces;
            trace_events[event_count][4] = mutations;
            event_count++;
        }
        if (epoch != next_epoch || epoch != confirm_epoch) {
            epoch = confirm_epoch;
            observations = 0;
            mutations_at_observation = mutations;
        } else if (next_traces > traces && mutations > mutations_at_observation) {
            observations++;
            mutations_at_observation = mutations;
            if (observations >= 2) {
                proved = 1;
                break;
            }
        }
        traces = next_traces;
        /* Runtime poll permits seed/remark STW, but never drives a GC step. */
        pcc_thread_safepoint();
    }
    py_gc_disable();
    if (py_list_len(root) != 8192) return 9;
    PyObject *last = py_list_get(root, 0);
    if (last == 0 || py_int_to_i64(last, 0) != ((mutations - 1) & 1)) return 10;
    py_decref(last);
    printf("backend=%d proved=%d observations=%d mutations=%d traces=%d\n",
           backend, proved, observations, mutations,
           __atomic_load_n(&pcc_gc_cms_worker_traces, __ATOMIC_ACQUIRE));
    if (!proved) {
        for (int i = 0; i < event_count; i++) {
            fprintf(stderr, "epoch=%lld->%lld traces=%lld->%lld mutations=%lld\n",
                    (long long)trace_events[i][0], (long long)trace_events[i][1],
                    (long long)trace_events[i][2], (long long)trace_events[i][3],
                    (long long)trace_events[i][4]);
        }
        return 11;
    }
    /* Explicit collection must preserve the live graph after overlapping work. */
    pcc_gc_collect(0);
    if (py_list_len(root) != 8192) return 12;
    pcc_gc_unpin(root);
    pcc_gc_scheduler_root_unregister_handle(handle);
    py_decref(root);
    if (py_err_occurred() != 0) return 13;
    return 0;
}
'''


@pytest.mark.integration
@pytest.mark.parametrize('backend', [1, 2, 3, 4])
def test_worker_marks_between_heap_mutations_without_stop_epoch_change(
    tmp_path, pcc_runtime_archive, backend
):
    from tests.owned_runtime_c_fixture import link_c_harness

    source = tmp_path / 'worker_overlap.c'
    source.write_text(PROBE)
    binary = tmp_path / 'worker_overlap'
    runtime = Path(__file__).resolve().parents[2] / 'pcc/runtime'
    link_c_harness(source, binary, extra_include_dirs=(str(runtime / 'src'),))
    # The OS may run a short mark to completion before this mutator samples
    # two batches. Retry only a missed overlap witness; corruption, timeout,
    # compile/link failures and other runtime errors fail immediately.
    for attempt in range(3):
        run = subprocess.run(
            [str(binary)], capture_output=True, text=True, timeout=20,
            env=dict(os.environ, PCC_GC_BACKEND=str(backend),
                     PCC_GC_DEBT_THRESHOLD='65536', PATH='/nonexistent'),
        )
        (tmp_path / f'probe-{attempt}.stdout').write_text(run.stdout)
        (tmp_path / f'probe-{attempt}.stderr').write_text(run.stderr)
        if run.returncode != 11:
            break
    assert run.returncode == 0, (backend, run.returncode, run.stdout, run.stderr)
    assert f'backend={backend} proved=1 observations=2 ' in run.stdout
    assert run.stderr == ''
