"""Controlled public GC4 count-free scalar-store coverage.

Every case invokes pcc_gc_store_ptr, not StorePlan. Colors/phase and pending
stop are synthetic; this fixture makes no concurrent collector/stop claim.
The outer lock stabilizes fixture state and also checks nested lock balance.
"""
from pathlib import Path
import os
import subprocess
import pytest
from tests.owned_c_execution import compile_owned_c_with_runtime

ROOT = Path(__file__).resolve().parents[2]

PUBLIC_SOURCE = r'''

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
extern int64_t pcc_thread_no_park_depth(void);
extern int32_t pcc_thread_stop_requested;
extern int32_t pcc_gc_mark_active;
extern int32_t pcc_gc_backend4_genzgc_store_barriers;
extern int32_t pcc_gc_backend4_no_refcounts;
extern int64_t pcc_gc_metric_store_count(void);
/* Exercise the public path with no stabilizing outer graph lock. */
static int checked_public_store(PyObject *owner, PyObject **slot, PyObject *value,
                                PyObject *child, int64_t expected_refs, int backend) {
    int64_t depth=pcc_py_gc_minor_graph_lock_depth();
    int64_t lease=pcc_thread_no_park_depth();
    int64_t stores=pcc_gc_metric_store_count();
    pcc_gc_store_ptr(owner,slot,value);
    if (slot && *slot!=value) return 1;
    if (py_header(child)->refcount!=expected_refs) return 2;
    if (pcc_py_gc_minor_graph_lock_depth()!=depth || pcc_thread_no_park_depth()!=lease) return 3;
    if (pcc_gc_metric_store_count()!=stores+((backend!=0 && slot)?1:0)) return 4;
    return 0;
}

static int public_compatibility(int backend) {
    if (pcc_gc_set_backend(backend)!=0 || pcc_gc_backend()!=backend) return 30;
    if ((pcc_gc_backend4_no_refcounts!=0)!=(backend!=0)) return 31;
    py_gc_disable();
    PyListObject *heap=(PyListObject *)py_list_new(1);
    PyObject *child=py_list_new(0);
    if (!heap || !heap->items || !child) return 32;
    heap->length=1;heap->items[0]=0;
    PyObject *root=0;
    PyObject *tagged=(PyObject *)(uintptr_t)35;
    int64_t refs=py_header(child)->refcount;
    int64_t retained=refs+(backend==0?1:0);
    for (int is_root=0;is_root<2;is_root++) {
        PyObject *owner=is_root?0:(PyObject *)heap;
        PyObject **slot=is_root?&root:&heap->items[0];
        if (checked_public_store(owner,slot,child,child,retained,backend)) return 33;
        /* An unchanged edge must not gain or lose ownership, including GC0. */
        if (checked_public_store(owner,slot,child,child,retained,backend)) return 34;
        if (checked_public_store(owner,slot,tagged,child,refs,backend)) return 35;
        if (checked_public_store(owner,slot,child,child,retained,backend)) return 36;
        if (checked_public_store(owner,slot,0,child,refs,backend)) return 37;
        if (checked_public_store(owner,0,child,child,refs,backend)) return 38;
        if (*slot!=0) return 39;
    }
    puts("PUBLIC_STORE_COMPATIBILITY_OK");
    return 0;
}

int main(int argc,char **argv) {
    if (argc==3 && argv[1][0]=='c') {
        int backend=argv[2][0]-'0';
        if (backend<0 || backend>4 || argv[2][1]!=0) return 29;
        return public_compatibility(backend);
    }
    if (argc != 2 || pcc_gc_set_backend(4) != 0) return 1;
    if (pcc_gc_backend4_no_refcounts != 1) return 17;
    py_gc_disable();
    PyListObject *owner=(PyListObject *)py_list_new(1);
    PyObject *child=py_list_new(0);
    PyObject *from=py_list_new(0);
    if (!owner || !child || !from || !owner->items) return 2;
    pcc_gc_pin((PyObject *)owner);pcc_gc_pin(child);
    int mode=argv[1][0];
    int root=mode=='r' || mode=='R' || mode=='F';
    int major=mode=='m' || mode=='b' || mode=='f' || mode=='R' || mode=='F';
    int generation=mode=='i' || mode=='b' || mode=='f' || mode=='p';
    int marking=major || mode=='g' || mode=='w' || mode=='a' || mode=='u' || mode=='q';
    int old_owner=generation || mode=='d' || mode=='t';
    int old_child=mode=='m' || mode=='g' || mode=='w' || mode=='y' || mode=='a';
    int sentinel=mode=='s' || mode=='t' || mode=='q';
    int unknown=mode=='u' || mode=='d';
    if ((mode=='f' || mode=='F') && pcc_gc_install_forwarding(from,child)!=0) return 3;
    pcc_py_gc_minor_graph_lock();
    if (old_owner) pcc_gc_generational_promote_young_if_known((PyObject *)owner);
    if (old_child) pcc_gc_generational_promote_young_if_known(child);
    if (old_owner && (py_header_flags_load(py_header((PyObject *)owner))&256)==0) return 4;
    if (!old_owner && ((py_header_flags_load(py_header((PyObject *)owner))&128)==0 || (py_header_flags_load(py_header((PyObject *)owner))&256)!=0)) return 18;
    if (!old_child && (py_header_flags_load(py_header(child))&128)==0) return 5;
    if (old_child && ((py_header_flags_load(py_header(child))&256)==0 || (py_header_flags_load(py_header(child))&128)!=0)) return 19;
    if (mode=='m' && ((py_header_flags_load(py_header((PyObject *)owner))&128)==0 || (py_header_flags_load(py_header(child))&256)==0)) return 6;
    if (marking || mode=='y') {
        int owner_color=mode=='g'?16:(mode=='w'?8:32);
        int child_color=mode=='a'?16:8;
        pcc_gc_set_mark_color((PyObject *)owner,owner_color);
        pcc_gc_set_mark_color(child,child_color);
        __atomic_store_n(&pcc_gc_mark_active,marking,__ATOMIC_RELEASE);
        if ((py_header_flags_load(py_header((PyObject *)owner))&56)!=owner_color) return 20;
        if ((py_header_flags_load(py_header(child))&56)!=child_color) return 21;
    }
    if (pcc_gc_mark_active!=marking) return 22;
    int64_t gray=pcc_gc_gray_count_load_acquire();
    int32_t barriers=pcc_gc_backend4_genzgc_store_barriers;
    int64_t depth=pcc_py_gc_minor_graph_lock_depth();
    int64_t refs=py_header(child)->refcount;
    owner->length=1;owner->items[0]=0;
    PyObject *value=(mode=='f' || mode=='F')?from:child;
    if (mode=='z') value=0;
    if (mode=='j') value=(PyObject *)(uintptr_t)35;
    if (sentinel) value=(PyObject *)(uintptr_t)2;
    if (unknown) value=(PyObject *)(uintptr_t)6;
    int64_t lease=pcc_thread_no_park_depth();
    int64_t stores=pcc_gc_metric_store_count();
    if (mode=='p') {
        if (depth<=0 || lease<=0) return 23;
        if (pcc_gc_store_buffer_owner_entries(owner)!=0 || pcc_gc_store_buffer_slot_pending(&owner->items[0])!=0) return 24;
        if (__atomic_load_n(&pcc_thread_stop_requested,__ATOMIC_ACQUIRE)!=0) return 25;
        /* Synthetic pending flag only, under an existing no-park lease.
           This is not real world-stop or collector coordination coverage. */
        __atomic_store_n(&pcc_thread_stop_requested,1,__ATOMIC_RELEASE);
    }
    pcc_gc_store_ptr(root?0:(PyObject *)owner,mode=='N'?0:&owner->items[0],value);
    int pending_ok=1;
    if (mode=='p') {
        pending_ok=__atomic_load_n(&pcc_thread_stop_requested,__ATOMIC_ACQUIRE)==1;
        /* Restore before any early return or outer unlock can leave no-park. */
        __atomic_store_n(&pcc_thread_stop_requested,0,__ATOMIC_RELEASE);
    }
    if (!pending_ok || pcc_thread_no_park_depth()!=lease) return 26;
    if (pcc_gc_metric_store_count()!=stores+(mode=='N'?0:1)) return 27;
    if (pcc_py_gc_minor_graph_lock_depth()!=depth) return 7;
    PyObject *expected=(sentinel || unknown || mode=='z' || mode=='j')?value:child;
    if (mode=='N') expected=0;
    if (owner->items[0]!=expected) return 8;
    if (pcc_gc_gray_count_load_acquire()!=gray+(major?1:0)) return 10;
    if (major && (py_header_flags_load(py_header(child))&16)==0) return 11;
    if (pcc_gc_backend4_genzgc_store_barriers!=barriers+(generation?1:0)) return 12;
    if (pcc_gc_store_buffer_owner_entries(owner)!=(generation?1:0)) return 13;
    if (pcc_gc_store_buffer_slot_pending(&owner->items[0])!=(generation?1:0)) return 14;
    if (pcc_gc_mark_active!=marking) return 15;
    if (pcc_gc_backend4_no_refcounts && py_header(child)->refcount!=refs) return 16;
    pcc_py_gc_minor_graph_unlock();
    /* Independent process per controlled phase fixture. Do not collect a
       synthetically started major phase or claim real concurrent-GC coverage. */
    puts("PUBLIC_DIRECT_STORE_NATIVE_OK");
    return 0;
}
'''

@pytest.fixture(scope="module")
def public_store_binary(tmp_path_factory, pcc_runtime_archive):
    directory = tmp_path_factory.mktemp("gc4_public_direct_store")
    source = directory / "public_store.c"
    source.write_text(PUBLIC_SOURCE)
    binary = directory / "public_store"
    compile_owned_c_with_runtime(
        source, binary, runtime_archive=Path(pcc_runtime_archive),
        cpp_args=("-I", str(ROOT / "pcc/runtime/src")),
    )
    return binary

@pytest.mark.integration
@pytest.mark.xdist_group(name="gc4_public_direct_store_native")
@pytest.mark.parametrize("mode", list("imbfsnygwaudtqrRFzjNp"))
def test_gc4_public_direct_store_policy(public_store_binary, mode):
    result = subprocess.run(
        [str(public_store_binary), mode], capture_output=True, text=True,
        timeout=20, env=dict(os.environ, PATH="/nonexistent", PCC_GC_BACKEND="4"),
    )
    assert (result.returncode, result.stdout, result.stderr) == (
        0, "PUBLIC_DIRECT_STORE_NATIVE_OK\n", "",
    ), (mode, result)


@pytest.mark.integration
@pytest.mark.xdist_group(name="gc4_public_direct_store_native")
@pytest.mark.parametrize("backend", range(5))
def test_public_store_five_backend_compatibility(public_store_binary, backend):
    result = subprocess.run(
        [str(public_store_binary), "c", str(backend)], capture_output=True, text=True,
        timeout=20, env=dict(os.environ, PATH="/nonexistent", PCC_GC_BACKEND=str(backend),
                            PCC_GC4_TRACING_ONLY="0", PCC_GC_TRACING_ONLY="0"),
    )
    assert (result.returncode, result.stdout, result.stderr) == (
        0, "PUBLIC_STORE_COMPATIBILITY_OK\n", "",
    ), (backend, result)
