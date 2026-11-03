"""Execute the opaque StorePlan ABI, including dirty scratch and sentinels."""

import os
from pathlib import Path
import subprocess

import pytest

from tests.owned_c_execution import compile_owned_c_with_runtime


SOURCE = r'''
#include "py_internal.h"
#include <stdint.h>
#include <stdio.h>
extern void pcc_py_gc_minor_graph_lock(void);
extern void pcc_py_gc_minor_graph_unlock(void);
extern int32_t pcc_gc_backend4_no_refcounts;

static int records_cleared(PccGcStoreRootPlan *plan) {
    for (int i = 1; i < 14; ++i) if (plan->opaque[i] != 0) return 0;
    return 1;
}

int main(void) {
    int64_t backend = pcc_gc_backend();
    py_gc_disable();
    PyObject *old = py_list_new(0);
    PyObject *value = py_list_new(0);
    if (!old || !value) return 10;
    py_incref(old); /* separate C owner and initial root owner */
    PyObject *root = old;
    int64_t old_before = ((PyObjectHeader *)old)->refcount;
    int64_t value_before = ((PyObjectHeader *)value)->refcount;
    int count_free = pcc_gc_backend4_no_refcounts != 0;
    PccGcStoreRootPlan plan;
    for (int i = 0; i < 16; ++i) plan.opaque[i] = (uintptr_t)old;
    pcc_gc_store_root_plan_init(&plan, backend);
    if (count_free && !records_cleared(&plan)) return 11;
    pcc_py_gc_minor_graph_lock();
    if (pcc_gc_store_root_plan_commit_locked(&plan, &root, value) != 1) return 12;
    if (pcc_gc_store_root_plan_commit_locked(&plan, &root, old) != 0) return 13;
    pcc_py_gc_minor_graph_unlock();
    pcc_gc_store_root_plan_finish(&plan);
    pcc_gc_store_root_plan_finish(&plan);
    if (root != value) return 14;
    if (((PyObjectHeader *)old)->refcount != old_before - (count_free ? 0 : 1)) return 15;
    if (((PyObjectHeader *)value)->refcount != value_before + (count_free ? 0 : 1)) return 16;
    if (count_free && !records_cleared(&plan)) return 17;

    /* This invalid object address is a sentinel, never an object header. */
    PyObject *sentinel = (PyObject *)(uintptr_t)2;
    pcc_gc_store_ptr_plan_init(&plan, 0, backend);
    pcc_py_gc_minor_graph_lock();
    if (pcc_gc_store_ptr_plan_commit_sentinel_aware_locked(&plan, 0, &root, sentinel, sentinel) != 1) return 18;
    pcc_py_gc_minor_graph_unlock();
    pcc_gc_store_ptr_plan_finish(&plan);
    if (root != sentinel || ((PyObjectHeader *)value)->refcount != value_before) return 19;
    if (count_free && !records_cleared(&plan)) return 20;
    pcc_gc_store_ptr_plan_init(&plan, 0, backend);
    pcc_py_gc_minor_graph_lock();
    if (pcc_gc_store_ptr_plan_commit_sentinel_aware_locked(&plan, 0, &root, 0, sentinel) != 1) return 21;
    pcc_py_gc_minor_graph_unlock();
    pcc_gc_store_ptr_plan_finish(&plan);
    if (root != 0 || (count_free && !records_cleared(&plan))) return 22;
    py_decref(old);
    py_decref(value);
    puts("STORE_PLAN_NATIVE_OK");
    return 0;
}
'''


@pytest.fixture(scope='module')
def store_plan_binary(tmp_path_factory, pcc_runtime_archive):
    directory = tmp_path_factory.mktemp('count_free_store_plan')
    source = directory / 'store_plan.c'
    source.write_text(SOURCE)
    binary = directory / 'store_plan'
    compile_owned_c_with_runtime(
        source, binary, runtime_archive=Path(pcc_runtime_archive),
        cpp_args=('-I', str(Path(__file__).resolve().parents[2] / 'pcc/runtime/src')),
    )
    return binary


@pytest.mark.integration
@pytest.mark.xdist_group('count_free_store_plan')
@pytest.mark.parametrize('backend,tracing', [(0, 0), (1, 0), (2, 0), (3, 0), (4, 0), (4, 1)])
def test_native_store_plan_ownership_and_dirty_scratch(store_plan_binary, backend, tracing):
    run = subprocess.run(
        [str(store_plan_binary)], capture_output=True, text=True, timeout=20,
        env=dict(os.environ, PATH='/nonexistent', PCC_GC_BACKEND=str(backend),
                 PCC_GC4_TRACING_ONLY='0', PCC_GC_TRACING_ONLY=str(tracing)),
    )
    assert (run.returncode, run.stdout, run.stderr) == (0, 'STORE_PLAN_NATIVE_OK\n', ''), (backend, tracing, run)


BARRIER_SOURCE = r'''
#include "py_internal.h"
#include <stdint.h>
#include <stdio.h>
extern int32_t pcc_gc_mark_active;
extern void pcc_py_gc_minor_graph_lock(void);
extern void pcc_py_gc_minor_graph_unlock(void);
extern int64_t pcc_gc_gray_count_load_acquire(void);
extern void pcc_gc_gray_count_store_release(int64_t);
extern void pcc_gc_generational_promote_young_if_known(void *);
extern int64_t pcc_gc_store_buffer_owner_entries(void *);
extern int64_t pcc_gc_store_buffer_slot_pending(void *);

int main(void) {
    if (pcc_gc_set_backend(4) != 0) return 1;
    py_gc_disable();
    PyListObject *owner = (PyListObject *)py_list_new(1);
    PyObject *child = py_list_new(0);
    if (!owner || !child || !owner->items) return 2;
    pcc_gc_pin((PyObject *)owner);
    pcc_gc_pin(child);
    pcc_py_gc_minor_graph_lock();
    pcc_gc_generational_promote_young_if_known((PyObject *)owner);
    if ((py_header_flags_load(py_header((PyObject *)owner)) & 256) == 0) return 3;
    uint32_t flags = py_header_flags_load(py_header(child));
    if ((flags & 128) == 0) return 4;
    int64_t gray_before = pcc_gc_gray_count_load_acquire();
    __atomic_store_n(&py_header(child)->flags, (flags & ~56u) | 8u, __ATOMIC_RELEASE);
    __atomic_store_n(&pcc_gc_mark_active, 1, __ATOMIC_RELEASE);
    PyObject *root = 0;
    PccGcStoreRootPlan plan;
    pcc_gc_store_root_plan_init(&plan, 4);
    if (pcc_gc_store_root_plan_commit_locked(&plan, &root, child) != 1) return 5;
    if ((py_header_flags_load(py_header(child)) & 16) == 0) return 6;
    if (pcc_gc_gray_count_load_acquire() != gray_before + 1) return 7;
    __atomic_store_n(&pcc_gc_mark_active, 0, __ATOMIC_RELEASE);
    __atomic_store_n(&py_header(child)->flags, flags, __ATOMIC_RELEASE);
    pcc_gc_gray_count_store_release(gray_before);
    pcc_py_gc_minor_graph_unlock();
    pcc_gc_store_root_plan_finish(&plan);
    pcc_gc_store_root(&root, 0);

    /* Inactive major marking still requires the old-to-young heap barrier. */
    pcc_py_gc_minor_graph_lock();
    if (pcc_gc_store_buffer_owner_entries(owner) != 0) return 8;
    owner->length = 1;
    owner->items[0] = 0;
    pcc_gc_store_ptr_plan_init(&plan, (PyObject *)owner, 4);
    if (pcc_gc_store_ptr_plan_commit_locked(&plan, (PyObject *)owner, &owner->items[0], child) != 1) return 9;
    if (pcc_gc_store_buffer_owner_entries(owner) != 1) return 10;
    if (pcc_gc_store_buffer_slot_pending(&owner->items[0]) != 1) return 11;
    if ((py_header_flags_load(py_header((PyObject *)owner)) & 512) == 0) return 12;
    if (__atomic_load_n(&pcc_gc_mark_active, __ATOMIC_ACQUIRE) != 0) return 13;
    pcc_py_gc_minor_graph_unlock();
    pcc_gc_store_ptr_plan_finish(&plan);
    pcc_gc_unpin((PyObject *)owner);
    pcc_gc_unpin(child);
    py_decref((PyObject *)owner);
    py_decref(child);
    puts("STORE_PLAN_BARRIERS_OK");
    return 0;
}
'''


@pytest.fixture(scope='module')
def store_plan_barrier_binary(tmp_path_factory, pcc_runtime_archive):
    directory = tmp_path_factory.mktemp('store_plan_barriers')
    source = directory / 'barriers.c'
    source.write_text(BARRIER_SOURCE)
    binary = directory / 'barriers'
    compile_owned_c_with_runtime(
        source, binary, runtime_archive=Path(pcc_runtime_archive),
        cpp_args=('-I', str(Path(__file__).resolve().parents[2] / 'pcc/runtime/src')),
    )
    return binary


@pytest.mark.integration
@pytest.mark.xdist_group('count_free_store_plan')
@pytest.mark.parametrize('tracing', [0, 1])
def test_native_active_roots_and_idle_old_to_young_fields_keep_barriers(store_plan_barrier_binary, tracing):
    run = subprocess.run(
        [str(store_plan_barrier_binary)], capture_output=True, text=True, timeout=20,
        env=dict(os.environ, PATH='/nonexistent', PCC_GC_BACKEND='4',
                 PCC_GC4_TRACING_ONLY='0', PCC_GC_TRACING_ONLY=str(tracing)),
    )
    assert (run.returncode, run.stdout, run.stderr) == (0, 'STORE_PLAN_BARRIERS_OK\n', ''), (tracing, run)
