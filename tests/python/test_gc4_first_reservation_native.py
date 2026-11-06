"""Native first-reservation zeroing with GC4 NoRC/conservative roots enabled.

The cached case recycles a genuinely empty private page through runtime APIs.
It does not simulate object death, suppress collection, or claim GC liveness.
"""

from pathlib import Path
import subprocess

from tests.owned_c_execution import compile_owned_c_with_runtime

SOURCE = r"""
/* Exercise an honestly empty, privately prepared page, not simulated GC death.
 * All metadata/list mutations belong to real runtime entries. */
#include "py_runtime.h"
#include <stdint.h>
#include <stdio.h>
extern void *pcc_gc_backend4_zpage_track_page_prepare(void *, void *, int64_t);
extern void pcc_gc_backend4_zpage_recycle(void *);
extern void *pcc_gc_backend4_zpage_active_page(int64_t, int64_t);
extern int64_t pcc_gc_backend4_free_page_count_for_class(int64_t);
extern int64_t pcc_gc_backend4_free_page_limit_for_class(int64_t);
extern void pcc_py_gc_minor_graph_lock(void);
extern void pcc_py_gc_minor_graph_unlock(void);
extern int32_t pcc_thread_conservative_roots_enabled;
extern int32_t pcc_gc_backend4_no_refcounts;
static int64_t word(void *p, int offset) {
    return *(int64_t *)((unsigned char *)p+offset);
}
static void *pointer(void *p, int offset) {
    return *(void **)((unsigned char *)p+offset);
}
int main(int argc, char **argv) {
    if (argc!=3) return 1;
    int64_t size=128;
    int64_t capacity=4096;
    int64_t page_class=0;
    if (argv[1][0]=='m') { size=8192; capacity=65536; page_class=1; }
    else if (argv[1][0]!='s') return 1;
    if (pcc_gc_set_backend(4)!=0) return 2;
    pcc_current_thread_id();
    if (pcc_thread_conservative_roots_enabled!=1 || pcc_gc_backend4_no_refcounts!=1) return 3;
    int cached=argv[2][0]=='c';
    if (!cached && argv[2][0]!='f') return 1;
    void *page=0;
    unsigned char *span=0;
    pcc_py_gc_minor_graph_lock();
    int64_t free_before=pcc_gc_backend4_free_page_count_for_class(page_class);
    int empty=pcc_gc_backend4_zpage_active_page(page_class,1)==0 && free_before==0;
    pcc_py_gc_minor_graph_unlock();
    if (!empty) return 16;
    if (cached) {
        int64_t live_before=pcc_gc_backend4_zpage_count();
        page=pcc_gc_backend4_zpage_track_page_prepare(0,0,size);
        if (!page) return 4;
        span=(unsigned char *)pointer(page,72);
        if (!span || word(page,16)!=capacity || word(page,80)!=capacity) return 5;
        if (word(page,8)!=0 || word(page,32)!=0 || word(page,64)!=0 || word(page,88)!=0 || word(page,96)!=0 || pointer(page,112)!=0) return 6;
        if (pcc_gc_backend4_zpage_count()!=live_before) return 7;
        /* The page has no owner/reservation and is still private: only payload
         * bytes are poisoned. No header, flags, lists, roots or trailer writes. */
        for (int64_t i=0;i<capacity;i++) span[i]=0x5b;
        pcc_py_gc_minor_graph_lock();
        if (pcc_gc_backend4_free_page_count_for_class(page_class)!=free_before) {
            pcc_py_gc_minor_graph_unlock(); return 15;
        }
        int64_t limit=pcc_gc_backend4_free_page_limit_for_class(page_class);
        if (limit<=0 || free_before>=limit || pcc_gc_backend4_zpage_active_page(page_class,1)!=0) {
            pcc_py_gc_minor_graph_unlock(); return 8;
        }
        pcc_gc_backend4_zpage_recycle(page);
        if (pcc_gc_backend4_free_page_count_for_class(page_class)!=free_before+1) {
            pcc_py_gc_minor_graph_unlock(); return 9;
        }
        pcc_py_gc_minor_graph_unlock();
    }
    /* Ordinary allocation must prepare or consume the selected page, reset
     * its full payload and complete real object tracking before returning. */
    PyObject *obj=pcc_gc_alloc(size,PY_TYPE_LIST,0);
    if (!obj || (cached && (void *)obj!=(void *)span)) return 10;
    if (!cached) {
        pcc_py_gc_minor_graph_lock();
        page=pcc_gc_backend4_zpage_active_page(page_class,1);
        pcc_py_gc_minor_graph_unlock();
        if (!page) return 17;
        span=(unsigned char *)pointer(page,72);
        if ((void *)span!=(void *)obj || word(page,16)!=capacity || word(page,80)!=capacity) return 18;
    }
    if ((((int32_t *)obj)[3]&65536)==0) return 11;
    pcc_py_gc_minor_graph_lock();
    int good=pcc_gc_backend4_zpage_active_page(page_class,1)==page
        && pcc_gc_backend4_free_page_count_for_class(page_class)==free_before
        && word(page,8)==size && word(page,32)==1 && word(page,64)==size
        && word(page,88)==0 && word(page,96)==0 && pointer(page,112)!=0;
    pcc_py_gc_minor_graph_unlock();
    if (!good || pcc_gc_backend4_zpage_owner_offset_bytes(obj)!=0) return 12;
    /* Header bytes are initialized by pcc_gc_alloc. Everything else, including
     * unused capacity, must have been zeroed by the actual reset. */
    for (int64_t i=16;i<capacity;i++) if (span[i]!=0) return 13;
    pcc_gc_publish_initialized(obj);
    if (pcc_thread_conservative_roots_enabled!=1 || pcc_gc_backend4_no_refcounts!=1) return 14;
    printf("ZPAGE_RESET_PUBLICATION_ZERO_OK %s %lld\n",cached?"cached":"fresh",(long long)size);
    return 0;
}
"""


def test_gc4_fresh_and_cached_first_reservation_zero(
    tmp_path: Path, threaded_pcc_runtime_archive: Path
) -> None:
    source = tmp_path / "zpage_first_reservation.c"
    source.write_text(SOURCE, encoding="utf-8")
    executable = tmp_path / "zpage_first_reservation"
    compile_owned_c_with_runtime(
        source, executable, runtime_archive=threaded_pcc_runtime_archive
    )
    # Each mode gets a new runtime, so fresh means no active or cached page.
    for mode in ("fresh", "cached"):
        for page_class, size in (("s", 128), ("m", 8192)):
            result = subprocess.run(
                [str(executable), page_class, mode],
                capture_output=True,
                text=True,
                timeout=30,
            )
            assert (result.returncode, result.stdout, result.stderr) == (
                0,
                f"ZPAGE_RESET_PUBLICATION_ZERO_OK {mode} {size}\n",
                "",
            ), (mode, size, result)
