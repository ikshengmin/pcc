/* An owned C client. The first active uses occur on two registered PCC workers;
 * only the initialization owner mutates the managed GC0 storage. Do not claim
 * support for arbitrary concurrent Java/GC0 application mutation. */
#include "pcc_shared_managed.h"
extern int64_t pcc_current_thread_id(void);
extern int64_t pcc_thread_registered(void);
extern void pcc_thread_safepoint(void);
extern int64_t pcc_thread_start(void **,void *(*)(void *),void *);
extern int64_t pcc_thread_join(void *,void **);
extern int64_t pcc_gc_scheduler_root_count(void);
extern int PRIME_TABLE_RUN(void);
extern int STATIC_COLD(void);
extern int STATIC_USE(void);
extern int STATIC_RUN(void);
extern int STATIC_LAST(void);
extern int STATIC_SUM(void);
extern int STATIC_OTHER(void);
extern int STATIC_PICK(void);
extern int STATIC_NUMS(void);
extern pcc_rt_managed_handle *STATIC_GET(void);
static int64_t owner_guard, ready, start;
static void *active(void *arg){
    if(pcc_current_thread_id()<=0 || !pcc_thread_registered())return (void*)1;
    if(pcc_rt_java_class_publish(&owner_guard,1)!=-1)return (void*)2;
    __atomic_fetch_add(&ready,1,__ATOMIC_RELEASE);
    while(!__atomic_load_n(&start,__ATOMIC_ACQUIRE))pcc_thread_safepoint();
    if(STATIC_USE()!=42 || STATIC_LAST()!=5)return (void*)3;
    return 0;
}
int observe_static_arrays(int64_t roots){
    static int64_t failed; /* Same zeroed process lifetime as the emitted class word. */
    void *threads[2],*status=0;
    pcc_rt_managed_handle *alias;
    if(pcc_rt_java_class_enter(&failed)!=1 || pcc_rt_java_class_enter(&failed)!=0 ||
       pcc_rt_java_class_publish(&failed,2)!=-1 || pcc_rt_java_class_publish(&failed,0)!=0 ||
       pcc_rt_java_class_enter(&failed)!=-1 || pcc_rt_java_class_publish(&failed,1)!=-1)return 90;
    if(pcc_rt_java_class_enter(&owner_guard)!=1 || pcc_rt_java_class_enter(&owner_guard)!=0)return 91;
    /* A JLS constant variable is not an active use of Table. */
    if(STATIC_COLD()!=42 || pcc_gc_scheduler_root_count()!=roots)return 92;
    if(pcc_thread_start(&threads[0],active,0)!=0 || pcc_thread_start(&threads[1],active,0)!=0)return 93;
    while(__atomic_load_n(&ready,__ATOMIC_ACQUIRE)!=2)pcc_thread_safepoint();
    __atomic_store_n(&start,1,__ATOMIC_RELEASE);
    if(pcc_thread_join(threads[0],&status)!=0 || status)return 94;
    if(pcc_thread_join(threads[1],&status)!=0 || status)return 95;
    if(pcc_rt_java_class_publish(&owner_guard,1)!=0 || pcc_rt_java_class_enter(&owner_guard)!=0)return 96;
    /* Two array allocations, but DATA and ALIAS own separate roots to the same
     * object. All three roots must outlive the initializing worker's exit. */
    if(pcc_gc_scheduler_root_count()!=roots+3 || STATIC_LAST()!=5 || STATIC_SUM()!=10)return 97;
    alias=STATIC_GET();
    if(!alias || pcc_gc_scheduler_root_count()!=roots+4 || pcc_rt_managed_element_count(alias)!=3)return 98;
    ((int*)pcc_rt_managed_data(alias))[0]=17;
    if(STATIC_SUM()!=25 || STATIC_LAST()!=5 || STATIC_RUN()!=42)return 99;
    if(((int*)pcc_rt_managed_data(alias))[0]!=37)return 100;
    pcc_rt_managed_drop(alias);
    if(STATIC_USE()!=42 || STATIC_SUM()!=45 || pcc_gc_scheduler_root_count()!=roots+3)return 101;
    if(STATIC_OTHER()!=7 || STATIC_OTHER()!=7 || pcc_gc_scheduler_root_count()!=roots+4)return 102;
    /* The correct in-range number is statically known, but evaluating the
     * index IndexSource.SELECT.length - 1 actively initializes IndexSource. */
    if(STATIC_PICK()!=3 || STATIC_NUMS()!=42 || pcc_gc_scheduler_root_count()!=roots+5)return 103;
    if(PRIME_TABLE_RUN()!=42 || pcc_gc_scheduler_root_count()!=roots+6)return 104;
    return 0;
}
