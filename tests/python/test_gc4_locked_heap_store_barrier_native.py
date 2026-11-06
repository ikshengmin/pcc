"""Owned native GC4 locked heap barriers and real writer/collector overlap.

The phase cases deliberately set colors/mark-active under the graph lock;
they prove insertion policy, not autonomous collector phase selection. The
pthread case separately drives real explicit collections with rooted unpinned
children. It asserts overlap but does not count actual relocation events.
"""
from pathlib import Path
import os
import subprocess

import pytest

from tests.owned_c_execution import compile_owned_c_with_runtime

ROOT = Path(__file__).resolve().parents[2]

BARRIER_SOURCE = r'''
#include "py_internal.h"
#include <stdint.h>
#include <stdio.h>
extern void pcc_py_gc_minor_graph_lock(void);
extern void pcc_py_gc_minor_graph_unlock(void);
extern int64_t pcc_py_gc_minor_graph_lock_depth(void);
extern void pcc_gc_generational_promote_young_if_known(void *);
extern void pcc_gc_set_mark_color(void *,int64_t);
extern int64_t pcc_gc_gray_count_load_acquire(void);
extern int64_t pcc_gc_store_buffer_owner_entries(void *);
extern int64_t pcc_gc_store_buffer_slot_pending(void *);
extern int32_t pcc_gc_mark_active;
extern int32_t pcc_gc_backend4_genzgc_store_barriers;
extern int32_t pcc_gc_backend4_no_refcounts;
int main(int argc,char **argv) {
    if (argc != 2 || pcc_gc_set_backend(4) != 0) return 1;
    if (pcc_gc_backend4_no_refcounts != 1) return 17;
    py_gc_disable();
    PyListObject *owner=(PyListObject *)py_list_new(1);
    PyObject *child=py_list_new(0);
    PyObject *from=py_list_new(0);
    if (!owner || !child || !from || !owner->items) return 2;
    pcc_gc_pin((PyObject *)owner);pcc_gc_pin(child);
    int mode=argv[1][0];
    int major=mode=='m' || mode=='b' || mode=='f';
    int generation=mode=='i' || mode=='b' || mode=='f';
    if (mode=='f' && pcc_gc_install_forwarding(from,child)!=0) return 3;
    pcc_py_gc_minor_graph_lock();
    if (generation) pcc_gc_generational_promote_young_if_known((PyObject *)owner);
    if (mode=='m') pcc_gc_generational_promote_young_if_known(child);
    if (generation && (py_header_flags_load(py_header((PyObject *)owner))&256)==0) return 4;
    if (generation && (py_header_flags_load(py_header(child))&128)==0) return 5;
    if (mode=='m' && ((py_header_flags_load(py_header((PyObject *)owner))&128)==0 || (py_header_flags_load(py_header(child))&256)==0)) return 6;
    if (major) {
        pcc_gc_set_mark_color((PyObject *)owner,32);
        pcc_gc_set_mark_color(child,8);
        __atomic_store_n(&pcc_gc_mark_active,1,__ATOMIC_RELEASE);
    }
    int64_t gray=pcc_gc_gray_count_load_acquire();
    int32_t barriers=pcc_gc_backend4_genzgc_store_barriers;
    int64_t depth=pcc_py_gc_minor_graph_lock_depth();
    int64_t refs=py_header(child)->refcount;
    owner->length=1;owner->items[0]=0;
    PccGcStoreRootPlan plan;
    pcc_gc_store_ptr_plan_init(&plan,(PyObject *)owner,4);
    PyObject *value=mode=='f'?from:child;
    if (mode=='s') value=(PyObject *)(uintptr_t)2;
    int result=mode=='s' ? pcc_gc_store_ptr_plan_commit_sentinel_aware_locked(&plan,(PyObject *)owner,&owner->items[0],value,value) : pcc_gc_store_ptr_plan_commit_locked(&plan,(PyObject *)owner,&owner->items[0],value);
    if (result!=1 || pcc_py_gc_minor_graph_lock_depth()!=depth) return 7;
    if (owner->items[0]!=(mode=='s'?value:child)) return 8;
    if (pcc_gc_store_ptr_plan_commit_locked(&plan,(PyObject *)owner,&owner->items[0],0)!=0) return 9;
    if (pcc_gc_gray_count_load_acquire()!=gray+(major?1:0)) return 10;
    if (major && (py_header_flags_load(py_header(child))&16)==0) return 11;
    if (pcc_gc_backend4_genzgc_store_barriers!=barriers+(generation?1:0)) return 12;
    if (pcc_gc_store_buffer_owner_entries(owner)!=(generation?1:0)) return 13;
    if (pcc_gc_store_buffer_slot_pending(&owner->items[0])!=(generation?1:0)) return 14;
    if (pcc_gc_mark_active!=(major?1:0)) return 15;
    if (pcc_gc_backend4_no_refcounts && py_header(child)->refcount!=refs) return 16;
    pcc_py_gc_minor_graph_unlock();
    pcc_gc_store_ptr_plan_finish(&plan);
    /* Independent process per controlled phase fixture. Do not collect a
       synthetically started major phase or claim real concurrent-GC coverage. */
    puts("LOCKED_HEAP_BARRIER_NATIVE_OK");
    return 0;
}
'''

CONCURRENT_SOURCE = r'''
#include "py_internal.h"
#include <pthread.h>
#include <stdint.h>
#include <stdio.h>
#include <sched.h>
extern void pcc_py_gc_minor_graph_lock(void);
extern void pcc_py_gc_minor_graph_unlock(void);
extern void pcc_gc_generational_promote_young_if_known(void *);
static PyObject *owner;
static PyObject *roots[2];
static int32_t start;
static int32_t writer_done;
static int32_t collector_done;
static int32_t failure;
static int32_t collecting;
static int32_t collector_started;
static int64_t overlap_writes;
static int64_t writes;
static int64_t collections;
static void *writer(void *unused) {
    if (pcc_current_thread_id()<=0) { __atomic_store_n(&failure,1,__ATOMIC_RELEASE);return 0; }
    while (!__atomic_load_n(&start,__ATOMIC_ACQUIRE)) {pcc_thread_safepoint_gate();sched_yield();}
    while (!__atomic_load_n(&collector_started,__ATOMIC_ACQUIRE)) {pcc_thread_safepoint_gate();sched_yield();}
    for (int i=0;i<2000;i++) {
        pcc_thread_safepoint_gate();
        pcc_py_gc_minor_graph_lock();
        PyObject *value=pcc_gc_load_ptr(0,&roots[i&1]);
        PyListObject *list=(PyListObject *)owner;
        pcc_gc_store_ptr(owner,&list->items[0],value);
        if (pcc_gc_load_ptr(owner,&list->items[0])!=value) __atomic_store_n(&failure,2,__ATOMIC_RELEASE);
        writes++;
        if (__atomic_load_n(&collecting,__ATOMIC_ACQUIRE)) overlap_writes++;
        pcc_py_gc_minor_graph_unlock();
    }
    pcc_thread_unregister_current();
    __atomic_store_n(&writer_done,1,__ATOMIC_RELEASE);
    return 0;
}
static void *collector(void *unused) {
    if (pcc_current_thread_id()<=0) {__atomic_store_n(&failure,3,__ATOMIC_RELEASE);return 0;}
    while (!__atomic_load_n(&start,__ATOMIC_ACQUIRE)) {pcc_thread_safepoint_gate();sched_yield();}
    __atomic_store_n(&collector_started,1,__ATOMIC_RELEASE);
    for (int i=0;i<24 || !__atomic_load_n(&writer_done,__ATOMIC_ACQUIRE);i++) {
        __atomic_store_n(&collecting,1,__ATOMIC_RELEASE);
        pcc_gc_collect(0);
        __atomic_store_n(&collecting,0,__ATOMIC_RELEASE);
        collections++;
        pcc_thread_safepoint_gate();
    }
    pcc_thread_unregister_current();
    __atomic_store_n(&collector_done,1,__ATOMIC_RELEASE);
    return 0;
}
int main(void) {
    if (pcc_gc_set_backend(4)!=0 || pcc_current_thread_id()<=0) return 4;
    owner=py_list_new(1);roots[0]=py_list_new(0);roots[1]=py_list_new(0);
    if (!owner || !roots[0] || !roots[1]) return 5;
    pcc_gc_pin(owner);
    pcc_gc_scheduler_root_register(&owner);
    pcc_gc_scheduler_root_register(&roots[0]);
    pcc_gc_scheduler_root_register(&roots[1]);
    pcc_py_gc_minor_graph_lock();
    pcc_gc_generational_promote_young_if_known(owner);
    ((PyListObject *)owner)->length=1;
    ((PyListObject *)owner)->items[0]=0;
    pcc_py_gc_minor_graph_unlock();
    pthread_t a,b;
    if (pthread_create(&a,0,writer,0)!=0 || pthread_create(&b,0,collector,0)!=0) return 6;
    __atomic_store_n(&start,1,__ATOMIC_RELEASE);
    while (!__atomic_load_n(&writer_done,__ATOMIC_ACQUIRE) || !__atomic_load_n(&collector_done,__ATOMIC_ACQUIRE)) {
        pcc_thread_safepoint_gate();sched_yield();
        if (__atomic_load_n(&failure,__ATOMIC_ACQUIRE)!=0) return 7;
    }
    if (pthread_join(a,0)!=0 || pthread_join(b,0)!=0) return 8;
    if (failure || writes!=2000 || collections<24 || overlap_writes==0) return 9;
    pcc_py_gc_minor_graph_lock();
    PyObject *last=pcc_gc_load_ptr(owner,&((PyListObject *)owner)->items[0]);
    if (last!=pcc_gc_load_ptr(0,&roots[1])) return 10;
    pcc_py_gc_minor_graph_unlock();
    puts("LOCKED_HEAP_CONCURRENT_WRITER_COLLECTOR_OK");
    return 0;
}
'''


@pytest.fixture(scope="module")
def locked_heap_binaries(tmp_path_factory, pcc_runtime_archive):
    directory = tmp_path_factory.mktemp("gc4_locked_heap")
    binaries = {}
    for name, source in (("barrier", BARRIER_SOURCE), ("concurrent", CONCURRENT_SOURCE)):
        path = directory / (name + ".c")
        path.write_text(source)
        binary = directory / name
        compile_owned_c_with_runtime(
            path, binary, runtime_archive=Path(pcc_runtime_archive),
            cpp_args=("-I", str(ROOT / "pcc/runtime/src")),
        )
        binaries[name] = binary
    return binaries


def run_gc4(binary, *args):
    return subprocess.run(
        [str(binary), *args], capture_output=True, text=True, timeout=20,
        env=dict(os.environ, PATH="/nonexistent", PCC_GC_BACKEND="4"),
    )


@pytest.mark.integration
@pytest.mark.parametrize("mode", ["i", "m", "b", "f", "s", "n"],
                         ids=["idle-generation", "active-major", "both", "forwarded", "sentinel", "idle-no-edge"])
def test_gc4_locked_heap_barrier_policy(locked_heap_binaries, mode):
    result = run_gc4(locked_heap_binaries["barrier"], mode)
    assert (result.returncode, result.stdout, result.stderr) == (
        0, "LOCKED_HEAP_BARRIER_NATIVE_OK\n", "",
    ), (mode, result)


@pytest.mark.integration
@pytest.mark.parametrize("repeat", range(3))
def test_gc4_locked_heap_store_overlaps_real_collection(locked_heap_binaries, repeat):
    result = run_gc4(locked_heap_binaries["concurrent"])
    assert (result.returncode, result.stdout, result.stderr) == (
        0, "LOCKED_HEAP_CONCURRENT_WRITER_COLLECTOR_OK\n", "",
    ), (repeat, result)
