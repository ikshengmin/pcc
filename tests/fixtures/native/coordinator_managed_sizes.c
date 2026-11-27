#include "py_runtime.h"
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>

extern int64_t pcc_gc_live_managed_size_snapshot(int64_t, int64_t);
extern int64_t malloc_usable_size(void *);
extern int64_t pcc_allocator_live_requested_bytes(void);
extern int64_t pcc_allocator_live_usable_bytes(void);
extern void *pcc_allocator_alloc_object(int64_t);
extern int64_t pcc_thread_start(void **, void *(*)(void *), void *);
extern int64_t pcc_thread_join(void *, void **);

#define BINS 37
#define WORDS (BINS * 4)
static int64_t ready, done, iterations;

static int snapshot(int64_t *out) {
    int64_t requested=pcc_allocator_live_requested_bytes();
    int64_t usable=pcc_allocator_live_usable_bytes();
    int64_t allocations=pcc_gc_telemetry(PCC_GC_COUNTER_ALLOCATIONS);
    int64_t result=pcc_gc_live_managed_size_snapshot((int64_t)(uintptr_t)out,WORDS*8);
    return result==0 && requested==pcc_allocator_live_requested_bytes()
        && usable==pcc_allocator_live_usable_bytes()
        && allocations==pcc_gc_telemetry(PCC_GC_COUNTER_ALLOCATIONS);
}

static int lane_for(int64_t requested) {
    if (requested<=512) return (int)((requested+15)/16)-1;
    int lane=32;
    int64_t upper=1024;
    while (requested>upper) { upper*=2;lane++; }
    return lane;
}
static int all_words_equal(int64_t *a,int64_t *b) {
    for (int i=0;i<WORDS;i++) if (a[i]!=b[i]) return 0;
    return 1;
}
static int consistent(int64_t *out) {
    int64_t upper=16;
    for (int lane=0;lane<BINS;lane++) {
        int64_t *b=out+lane*4;
        if (b[0]<0 || b[1]<0 || b[2]<b[1] || b[3]<b[1] || b[3]>b[2]
                || b[2]<b[0]*16 || (b[3]&15)!=0) return 0;
        if (lane<32 && b[3]!=b[0]*upper) return 0;
        if (lane>=32 && b[2]!=b[0]*upper) return 0;
        if (lane==31) upper=1024;
        else if (lane<31) upper+=16;
        else upper*=2;
    }
    return 1;
}

static void *worker(void *arg) {
    int64_t id=(int64_t)(uintptr_t)arg;
    if (pcc_current_thread_id()<=0) return (void *)1;
    PyObject *roots[2]={NULL,NULL};
    int32_t map[1]={2};
    pcc_gc_frame_enter(map,roots);
    __atomic_add_fetch(&ready,1,__ATOMIC_ACQ_REL);
    do {
        roots[0]=py_tuple_new(5+id);
        roots[1]=py_tuple_new(61+id);
        if (!roots[0] || !roots[1] || py_err_occurred()) return (void *)2;
        pcc_thread_safepoint();
        pcc_gc_store_root(&roots[0],NULL);
        pcc_gc_store_root(&roots[1],NULL);
        __atomic_add_fetch(&iterations,1,__ATOMIC_ACQ_REL);
        pcc_thread_safepoint();
    } while (!__atomic_load_n(&done,__ATOMIC_ACQUIRE));
    pcc_gc_frame_leave(roots);
    return NULL;
}

int main(void) {
    static const int arity[]={0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,17,25,29,
                              61,62,125,126,253,254,509,510,1021,1022,2045};
    enum { N=sizeof(arity)/sizeof(arity[0]) };
    PyObject *roots[N]={NULL};
    int32_t map[1]={N};
    int64_t before[WORDS],after[WORDS],final[WORDS],expected[WORDS]={0};
    int64_t guarded[WORDS+2];
    pcc_gc_frame_enter(map,roots);
    roots[0]=py_tuple_new(0);
    if (!roots[0]) return 1;
    pcc_gc_store_root(&roots[0],NULL);
    if (pcc_gc_collect(1)<0 || !snapshot(before) || !consistent(before)) return 2;
    guarded[0]=123456789;guarded[WORDS+1]=987654321;
    if (!snapshot(guarded+1) || guarded[0]!=123456789
            || guarded[WORDS+1]!=987654321 || !all_words_equal(before,guarded+1))
        return 14;
    for (int i=0;i<N;i++) {
        roots[i]=py_tuple_new(arity[i]);
        if (!roots[i]) return 3;
        int64_t requested=24+8*(int64_t)arity[i];
        int lane=lane_for(requested);
        expected[lane*4]++;
        expected[lane*4+1]+=requested;
        expected[lane*4+2]+=malloc_usable_size(roots[i]);
        expected[lane*4+3]+=(requested+15)/16*16;
    }
    if (!snapshot(after) || !consistent(after)) return 4;
    for (int i=0;i<WORDS;i++) if (after[i]-before[i]!=expected[i]) return 5;
    if (pcc_gc_live_managed_size_snapshot((int64_t)(uintptr_t)after,WORDS*8-1)!=-1
            || pcc_gc_live_managed_size_snapshot((int64_t)(uintptr_t)after+1,WORDS*8)!=-1
            || pcc_thread_owns_stopped_world()) return 6;
    if (pcc_stop_the_world()!=0 || !snapshot(final)
            || !pcc_thread_owns_stopped_world()) return 7;
    pcc_resume_world();
    if (!all_words_equal(after,final)) return 8;
    for (int i=0;i<N;i++) pcc_gc_store_root(&roots[i],NULL);
    if (!snapshot(final) || !all_words_equal(before,final)) return 9;

    /* Raw, RESERVED, and large mapped managed allocations affect the
     * global allocator ledger but are deliberately outside LIVE object
     * slab geometry. The diagnostic must not read them through an
     * arbitrary managed-index or malloc pointer. */
    void *raw=malloc(80);
    void *reserved=pcc_allocator_alloc_object(40);
    roots[0]=py_tuple_new(2046);
    if (!raw || !reserved || !roots[0] || !snapshot(final)
            || !all_words_equal(before,final)) return 15;
    free(raw);
    free(reserved);
    pcc_gc_store_root(&roots[0],NULL);
    if (!snapshot(final) || !all_words_equal(before,final)) return 16;

    /* Four actual native carrier histories. During concurrent churn the
     * global byte counters can move after resume; measure cross-bin
     * consistency instead of requiring a false before/after equality. */
    void *threads[4];
    for (int i=0;i<4;i++)
        if (pcc_thread_start(&threads[i],worker,(void *)(uintptr_t)i)!=0) return 10;
    while (__atomic_load_n(&ready,__ATOMIC_ACQUIRE)<4
            || __atomic_load_n(&iterations,__ATOMIC_ACQUIRE)<16)
        pcc_thread_safepoint();
    for (int i=0;i<20;i++) {
        if (pcc_gc_live_managed_size_snapshot((int64_t)(uintptr_t)after,WORDS*8)!=0
                || !consistent(after)) return 11;
    }
    __atomic_store_n(&done,1,__ATOMIC_RELEASE);
    for (int i=0;i<4;i++) {
        void *result=NULL;
        if (pcc_thread_join(threads[i],&result)!=0 || result) return 12;
    }
    if (pcc_gc_collect(1)<0 || !snapshot(final) || !all_words_equal(before,final)
            || pcc_gc_telemetry(PCC_GC_COUNTER_ALLOCATOR_DOUBLE_FREES)!=0
            || py_err_occurred()) return 13;
    pcc_gc_frame_leave(roots);
    puts("MANAGED_SIZE_PROFILE_OK");
    return 0;
}
