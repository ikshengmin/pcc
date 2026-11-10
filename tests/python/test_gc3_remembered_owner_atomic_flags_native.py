"""Owned native stale-argument writer/root-publisher regression.

The first fixture controls phase/color under graph and schedules the two
real carriers deliberately. It proves the emitted remember writer and real
public no-graph root path, not autonomous phase selection or object survival.
It exits without collecting the synthetic phase. Ordinary lifecycle overlap
must be validated separately before a broader release-correctness claim.
"""
from pathlib import Path
import os
import subprocess

import pytest

from tests.owned_c_execution import compile_owned_c_with_runtime


ROOT = Path(__file__).resolve().parents[2]
SOURCE = r'''
#include "py_internal.h"
#include <pthread.h>
#include <stdint.h>
#include <stdio.h>
#include <sched.h>
extern void pcc_py_gc_minor_graph_lock(void);
extern void pcc_py_gc_minor_graph_unlock(void);
extern int64_t pcc_py_gc_minor_graph_lock_depth(void);
extern int64_t pcc_thread_no_park_depth(void);
extern void pcc_gc_generational_promote_young_if_known(void *);
extern int64_t pcc_gc_object_is_known_no_lock(void *);
extern int64_t pcc_gc_slot_is_runtime_root(void *);
extern void pcc_gc_set_mark_color(void *,int64_t);
extern int64_t pcc_gc_gray_count_load_acquire(void);
extern void pcc_gc_backend3_remember_owner(void *,int64_t);
extern void pcc_gc_backend3_remembered_scan_probe_config(int64_t);
extern void *pcc_gc_backend3_remembered_owner_list_head(void);
extern int32_t pcc_gc_mark_active;
extern int32_t pcc_gc_forwarding_population;
extern int32_t pcc_gc_backend4_no_refcounts;
extern int32_t pcc_diagnostics_runtime_log_fast_state;
extern int32_t pcc_gc_refcount_provenance_probe;
extern int32_t pcc_gc_backend3_remembered_overflow;
extern void *pcc_gc_backend3_remembered_scan_cursor;
extern int64_t pcc_gc_backend3_remembered_scan_revision;
extern int64_t pcc_gc_backend3_remembered_owner_allocation_limit;
static PyObject *keeper;
static PyObject *published;
static int32_t ready;
static int32_t start;
static int32_t done;
static int32_t leave;
static int32_t failure;
static int32_t final_pin;

static void *root_publisher(void *unused) {
    if (pcc_current_thread_id()<=0) { __atomic_store_n(&failure,1,__ATOMIC_RELEASE); return 0; }
    __atomic_store_n(&ready,1,__ATOMIC_RELEASE);
    while (!__atomic_load_n(&start,__ATOMIC_ACQUIRE)) { pcc_thread_safepoint_gate(); sched_yield(); }
    if (pcc_py_gc_minor_graph_lock_depth()!=0) { __atomic_store_n(&failure,2,__ATOMIC_RELEASE); return 0; }
    /* keeper is a registered root; A owns graph throughout this raw borrow. */
    pcc_gc_store_root(&published,keeper);
    if (final_pin) pcc_gc_pin(keeper); else pcc_gc_unpin(keeper);
    if (pcc_py_gc_minor_graph_lock_depth()!=0 || pcc_thread_no_park_depth()!=0) {
        __atomic_store_n(&failure,3,__ATOMIC_RELEASE); return 0;
    }
    __atomic_store_n(&done,1,__ATOMIC_RELEASE);
    while (!__atomic_load_n(&leave,__ATOMIC_ACQUIRE)) { pcc_thread_safepoint_gate(); sched_yield(); }
    pcc_thread_unregister_current();
    return 0;
}

int main(int argc,char **argv) {
    if (argc!=4 || pcc_gc_set_backend(3)!=0 || pcc_current_thread_id()<=0) return 10;
    int overflow=argv[1][0]=='o';
    final_pin=argv[2][0]=='s';
    int historical=argv[3][0]=='b';
    if (pcc_gc_backend4_no_refcounts!=1 || pcc_diagnostics_runtime_log_fast_state!=0 || pcc_gc_refcount_provenance_probe!=0) return 11;
    py_gc_disable();
    pcc_gc_scheduler_root_register(&keeper);
    pcc_gc_scheduler_root_register(&published);
    if (pcc_gc_slot_is_runtime_root(&keeper)!=1 || pcc_gc_slot_is_runtime_root(&published)!=1) return 28;
    keeper=py_list_new(0);
    if (!keeper) return 12;
    pcc_gc_pin(keeper);
    pcc_py_gc_minor_graph_lock();
    pcc_gc_generational_promote_young_if_known(keeper);
    if (!pcc_gc_object_is_known_no_lock(keeper) || !(py_header_flags_load(py_header(keeper))&256)) return 13;
    if (py_header_flags_load(py_header(keeper))&(128|512)) return 14;
    if (pcc_gc_forwarding_population!=0) return 15;
    pcc_py_gc_minor_graph_unlock();
    pthread_t publisher;
    if (pthread_create(&publisher,0,root_publisher,0)!=0) return 16;
    while (!__atomic_load_n(&ready,__ATOMIC_ACQUIRE)) {
        if (__atomic_load_n(&failure,__ATOMIC_ACQUIRE)) return 17;
        pcc_thread_safepoint_gate(); sched_yield();
    }
    pcc_py_gc_minor_graph_lock();
    if (final_pin) pcc_gc_unpin(keeper); /* Later atomic producer sets it. */
    pcc_gc_set_mark_color(keeper,8);
    __atomic_store_n(&pcc_gc_mark_active,1,__ATOMIC_RELEASE);
    int32_t saved=py_header_flags_load(py_header(keeper));
    int64_t gray=pcc_gc_gray_count_load_acquire();
    int64_t depth=pcc_py_gc_minor_graph_lock_depth();
    if ((saved&56)!=8 || (saved&512) || (saved&64)!=(final_pin?0:64) || pcc_thread_no_park_depth()<=0) return 18;
    pcc_gc_backend3_remembered_scan_probe_config(overflow?0:1);
    void *head_before=pcc_gc_backend3_remembered_owner_list_head();
    __atomic_store_n(&start,1,__ATOMIC_RELEASE);
    while (!__atomic_load_n(&done,__ATOMIC_ACQUIRE)) {
        if (__atomic_load_n(&failure,__ATOMIC_ACQUIRE)) return 19;
        sched_yield(); /* Keep A's graph/no-park hold, no hidden callback. */
    }
    if (pcc_py_gc_minor_graph_lock_depth()!=depth || published!=keeper) return 20;
    int32_t shaded=py_header_flags_load(py_header(keeper));
    if ((shaded&56)!=16 || (shaded&64)!=(final_pin?64:0) || pcc_gc_gray_count_load_acquire()!=gray+1) return 21;
    pcc_gc_backend3_remember_owner(keeper,saved);
    int32_t after=py_header_flags_load(py_header(keeper));
    int32_t expected=historical ? (saved|512) : (shaded|512);
    if (after!=expected || pcc_gc_gray_count_load_acquire()!=gray+1 || pcc_py_gc_minor_graph_lock_depth()!=depth) return 22;
    void *head=pcc_gc_backend3_remembered_owner_list_head();
    if (overflow) {
        if (head!=head_before || pcc_gc_backend3_remembered_overflow!=1 || pcc_gc_backend3_remembered_scan_cursor || pcc_gc_backend3_remembered_scan_revision!=0) return 23;
    } else {
        if (!head || head==head_before || ((void **)head)[0]!=keeper || ((void **)head)[1]!=head_before) return 24;
    }
    if (pcc_gc_backend3_remembered_owner_allocation_limit!=0) return 25;
    pcc_gc_backend3_remember_owner(keeper,after);
    if (pcc_gc_backend3_remembered_owner_list_head()!=head || py_header_flags_load(py_header(keeper))!=after || pcc_gc_backend3_remembered_owner_allocation_limit!=0) return 26;
    pcc_py_gc_minor_graph_unlock();
    __atomic_store_n(&leave,1,__ATOMIC_RELEASE);
    if (pthread_join(publisher,0)!=0 || failure) return 27;
    puts(historical ? "GC3_REMEMBER_NATIVE_STALE" : "GC3_REMEMBER_NATIVE_PRESERVED");
    return 0;
}
'''


@pytest.fixture(scope="module")
def remembered_atomic_binary(tmp_path_factory, pcc_runtime_archive):
    directory = tmp_path_factory.mktemp("gc3_remember_atomic")
    source = directory / "remember_atomic.c"
    source.write_text(SOURCE)
    binary = directory / "remember_atomic"
    compile_owned_c_with_runtime(
        source, binary, runtime_archive=Path(pcc_runtime_archive),
        cpp_args=("-I", str(ROOT / "pcc/runtime/src")),
    )
    return binary


@pytest.mark.integration
@pytest.mark.parametrize("allocation", ["queue", "overflow"])
@pytest.mark.parametrize("pin_change", ["set", "clear"])
def test_gc3_native_remember_preserves_root_shade_and_current_bits(
    remembered_atomic_binary, allocation, pin_change,
):
    result = subprocess.run(
        [str(remembered_atomic_binary), allocation, pin_change, "fixed"],
        capture_output=True, text=True, timeout=20,
        env=dict(os.environ, PATH="/nonexistent", PCC_GC_BACKEND="3"),
    )
    assert (result.returncode, result.stdout, result.stderr) == (
        0, "GC3_REMEMBER_NATIVE_PRESERVED\n", "",
    ), result
