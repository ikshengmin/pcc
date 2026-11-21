/* The private fixture's only managed young references are the declared five.
   Test actual resolved callbacks separately: an incidental stale stack word
   cannot satisfy evidence for a required precise source. */
#include "py_internal.h"
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
extern int64_t pcc_exp2_leaf_init(void *,void *,void *,int64_t,void *,int64_t);
extern void *pcc_exp2_leaf_take(void *,int64_t);
extern int64_t pcc_exp2_leaf_is_start(void *,void *);
extern int64_t pcc_exp2_leaf_remember(void *,void *);
extern int64_t pcc_exp2_leaf_minor(void *,void *,int64_t);
extern void pcc_exp2_leaf_mark_word(void *,void *);
extern int64_t pcc_thread_world_stop_epoch_held(void);
extern int64_t pcc_thread_start(void **,void *(*)(void *),void *);
extern int64_t pcc_thread_join(void *,void **);
extern void pcc_gc_native_frame_enter_borrowed_slot(void *,void *,void *);
extern int64_t pcc_gc_native_frame_leave(void *);
extern void *pcc_gc_scheduler_root_register_handle(void *);
#ifdef ADDED_CUT
extern int64_t pcc_gc_visit_stopped_root_cut(int64_t,void *,void *,void *);
#endif
#define PG 4096
#define COUNT 12
#define CHECK(cond,code) do { if (!(cond)) { printf("FAIL %d\n", code); return code; } } while(0)
typedef struct Cut {
    int64_t state[18],meta[COUNT*2];
    void **remembered[4];
    char *base;
    void *scheduler_slot;
    PyObject *old_edge;
    long exact_bits, cons_bits, exact_count, cons_count;
    int64_t ready,go;
} Cut;
static Cut t;
static void *new_cells(void *v){
    for(int i=0;i<COUNT*64;i++){
        PyStrObject *s=(PyStrObject*)pcc_exp2_leaf_take(t.state,42);
        if (!s) return (void*)1;
        /* Header compatible with actual runtime accessors; no pc/node tables. */
        ((PyObjectHeader*)s)->refcount=1;
        ((PyObjectHeader*)s)->type_tag=PY_TYPE_STR;
        ((PyObjectHeader*)s)->flags=0;
        s->byte_len=1;s->cp_len=1;s->hash=-1;s->data[0]=(char)('A'+i/64);s->data[1]=0;
    }
    return 0;
}
static void exact(void *word,void *ctx){
    Cut*c=(Cut*)ctx;
    if(pcc_exp2_leaf_is_start(c->state,word)){
        unsigned long offset=(uintptr_t)word-(uintptr_t)c->base;
        c->exact_bits |= (1L<<(offset/PG));
        pcc_exp2_leaf_mark_word(c->state,word);
    }
    c->exact_count++;
}
static void conservative(void *word,void *ctx){
    Cut*c=(Cut*)ctx;
    if ((uintptr_t)word == (uintptr_t)c->base+5*PG+41) c->cons_bits|=1L<<5;
    if ((uintptr_t)word == (uintptr_t)c->base+6*PG+41) c->cons_bits|=1L<<6;
    pcc_exp2_leaf_mark_word(c->state,word);
    c->cons_count++;
}
static void *carrier(void *v){
    /* A real native precise frame on a second carrier; separate live interior
       is NOT registered and exists through its conservative park snapshot. */
    int64_t frame[5]={0},range[2]={0};
    void *precise=t.base+4*PG;
    char *volatile interior=t.base+5*PG+41;
    pcc_thread_no_park_enter();
    pcc_gc_native_frame_enter_borrowed_slot(frame,range,&precise);
    pcc_thread_no_park_exit();
    __atomic_store_n(&t.ready,1,__ATOMIC_RELEASE);
    while(!__atomic_load_n(&t.go,__ATOMIC_ACQUIRE)) pcc_thread_safepoint();
    if (interior[0]!=0 || py_str_byte_len((PyObject*)precise)!=1) return (void*)2;
    if (pcc_gc_native_frame_leave(frame)!=0) return (void*)3;
    return 0;
}
static PyObject *make_suspension(void){
    /* Actual stackless continuation: starts unmounted, with its registered
       heap chunk as the precise off-carrier root. No C native stack exists
       for a suspended vthread in this runtime. */
    int map=1;
    PyObject *slots[1]={(PyObject*)(t.base+2*PG)};
    return py_continuation_new(&map,slots,0);
}
#ifdef ADDED_CUT
extern void pcc_gc_backend3_revoke_closed_young_domain(void);
static void count_words(void *value,void *ctx){ (*(long*)ctx)++; }
static void *uncovered_carrier(void *v){
    int64_t mark=pcc_thread_blocking_enter(__builtin_frame_address(0));
    if (!mark) return (void*)1;
    __atomic_store_n(&t.ready,1,__ATOMIC_RELEASE);
    while(!__atomic_load_n(&t.go,__ATOMIC_ACQUIRE)) {}
    pcc_thread_blocking_leave(mark); return 0;
}
static int missing_snapshot(void){
    /* The worker exited into native code BEFORE tracing admission installed
       register/stack snapshots. Refreshing just the owner is insufficient. */
    CHECK(pcc_gc_set_backend(0)==0 && pcc_gc_backend()==0,41);
    void *thread=0,*status=0; long positives=0;
    CHECK(pcc_thread_start(&thread,uncovered_carrier,0)==0,42);
    while(!__atomic_load_n(&t.ready,__ATOMIC_ACQUIRE))pcc_thread_safepoint();
    CHECK(pcc_gc_set_backend(3)==0,43);
    CHECK(pcc_stop_the_world()==0,44);
    int64_t epoch=pcc_thread_world_stop_epoch_held(); CHECK(epoch>0,45);
    CHECK(pcc_gc_visit_stopped_root_cut(epoch,(void*)count_words,(void*)count_words,&positives)<0,46);
    CHECK(pcc_resume_world()==0,47);
    __atomic_store_n(&t.go,1,__ATOMIC_RELEASE);
    CHECK(pcc_thread_join(thread,&status)==0 && !status,48);
    printf("MISSING_CARRIER_SNAPSHOT_DENIED_NO_MINOR\n"); return 0;
}
#endif
int main(int argc,char **argv){
#ifdef ADDED_CUT
    if(argc>1) return missing_snapshot();
#endif
    CHECK(pcc_gc_set_backend(3)==0 && pcc_gc_backend()==3,1);
    py_gc_disable();
    /* First managed allocation freezes GC3's conservative-snapshot policy. */
    CHECK(py_str_new("stock",5)!=0,2);
    t.base=(char*)calloc(COUNT,PG);
    CHECK(t.base && pcc_exp2_leaf_init(t.state,t.base,t.meta,COUNT,t.remembered,4),3);
    void *thread=0,*status=0;
    CHECK(pcc_thread_start(&thread,new_cells,0)==0,4);
    CHECK(pcc_thread_join(thread,&status)==0 && !status,5);
    PyObject *cont=make_suspension();
    CHECK(cont && !py_continuation_is_mounted(cont),6);
    t.scheduler_slot=t.base+3*PG;
    CHECK(pcc_gc_scheduler_root_register_handle(&t.scheduler_slot)!=0,7);
    /* Explicit remembered edge of this closed fixture. Not evidence that a
       production store to an arbitrary old owner would be observed. */
    t.old_edge=(PyObject*)(t.base+7*PG);
    CHECK(pcc_exp2_leaf_remember(t.state,&t.old_edge),8);
    CHECK(pcc_thread_start(&thread,carrier,0)==0,9);
    while(!__atomic_load_n(&t.ready,__ATOMIC_ACQUIRE))pcc_thread_safepoint();
    char *volatile owner_interior=t.base+6*PG+41;
#ifdef ADDED_CUT
    CHECK(pcc_gc_visit_stopped_root_cut(1,(void*)exact,(void*)conservative,&t)<0,10);
    CHECK(!t.state[9]&&!t.exact_count&&!t.cons_count,11);
#endif
    CHECK(pcc_stop_the_world()==0,12);
    int64_t epoch=pcc_thread_world_stop_epoch_held();
    CHECK(epoch>0,13);
#ifdef ADDED_CUT
    CHECK(pcc_gc_visit_stopped_root_cut(epoch+1,(void*)exact,(void*)conservative,&t)<0,14);
    CHECK(pcc_gc_visit_stopped_root_cut(epoch,0,(void*)conservative,&t)<0,15);
    CHECK(!t.state[9]&&!t.exact_count&&!t.cons_count,16);
    CHECK(pcc_gc_visit_stopped_root_cut(epoch,(void*)exact,(void*)conservative,&t)==epoch,17);
#endif
    /* In the red stock control this was the only available private cut. */
    CHECK(pcc_thread_world_stop_epoch_held()==epoch,18);
    int64_t freed=pcc_exp2_leaf_minor(t.state,0,0);
    CHECK(freed>0 && t.state[9]==1,19);
    printf("sources exact=%ld conservative=%ld counts=%ld/%ld promoted=%lld recycled=%lld\n", t.exact_bits,t.cons_bits,t.exact_count,t.cons_count,(long long)t.state[11],(long long)freed);
    CHECK((t.exact_bits&28)==28,20); /* continuation / scheduler / carrier exact */
    CHECK((t.cons_bits&96)==96,21); /* parked carrier and owner interior */
    for(int i=2;i<=7;i++)CHECK(t.meta[2*i]==2,22);
    CHECK(py_str_byte_len(t.old_edge)==1 && ((PyStrObject*)t.old_edge)->data[0]=='H',23);
    CHECK(owner_interior[0]==0,24);
    CHECK(pcc_resume_world()==0,25);
    __atomic_store_n(&t.go,1,__ATOMIC_RELEASE);
    CHECK(pcc_thread_join(thread,&status)==0 && !status,26);
#ifdef ADDED_CUT
    CHECK(pcc_gc_visit_stopped_root_cut(epoch,(void*)exact,(void*)conservative,&t)<0,27);
    CHECK(pcc_stop_the_world()==0,28);
    int64_t epoch2=pcc_thread_world_stop_epoch_held();
    CHECK(epoch2>epoch && pcc_gc_visit_stopped_root_cut(epoch,(void*)exact,(void*)conservative,&t)<0,29);
    pcc_gc_backend3_revoke_closed_young_domain();
    CHECK(pcc_gc_visit_stopped_root_cut(epoch2,(void*)exact,(void*)conservative,&t)<0,30);
    CHECK(pcc_resume_world()==0 && t.state[9]==1,31);
#endif
    printf("WORLDCUT_OK\n");
    return 0;
}
