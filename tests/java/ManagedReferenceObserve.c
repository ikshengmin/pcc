/* Native client of the public owning-reference ABI. Replaces only export
 * tokens with their public encoded symbols; Java source bytes are unchanged. */
#include "pcc_shared_managed.h"
extern int64_t pcc_current_thread_id(void);
extern int64_t pcc_thread_registered(void);
extern int64_t pcc_gc_backend(void);
extern int64_t pcc_gc_scheduler_root_count(void);
extern int ARRAY_ENTRY(void);
extern int DECLARED_ENTRY(void);
extern pcc_rt_managed_handle *ARRAY_RELAY(int);
extern pcc_rt_managed_handle *TOKEN_FORWARD(pcc_rt_managed_handle *);
extern int TOKEN_ENTRY(pcc_rt_managed_handle *,pcc_rt_managed_handle *);
static const pcc_rt_managed_descriptor_v1 opaque = {1, 0, 0, 1, 0, 0};
int main(void) {
    pcc_rt_managed_handle *left=0,*right=0,*returned=0,*array=0;
    int64_t roots;
    int i;
    if (pcc_current_thread_id() <= 0 || !pcc_thread_registered() || pcc_gc_backend()!=0) return 70;
    roots=pcc_gc_scheduler_root_count();
    for(i=0;i<4;i++) {
        if (DECLARED_ENTRY()!=42 || pcc_gc_scheduler_root_count()!=roots) return 82;
        if (ARRAY_ENTRY()!=42 || pcc_gc_scheduler_root_count()!=roots) return 71;
        array=ARRAY_RELAY(137);
        if (!array || pcc_gc_scheduler_root_count()!=roots+1) return 72;
        if (pcc_rt_managed_element_count(array)!=2 || pcc_rt_managed_payload_size(array)!=8) return 73;
        if (((int*)pcc_rt_managed_data(array))[0]!=137 || ((int*)pcc_rt_managed_data(array))[1]!=42) return 74;
        pcc_rt_managed_drop(array);
        if (pcc_gc_scheduler_root_count()!=roots) return 75;
    }
    /* Opaque real GC0 allocations, not invented Java constructors or payload layout.
     * The supplied nominal reference ABI does not inspect their payloads. */
    if (pcc_rt_managed_alloc(&opaque,0,&left) || pcc_rt_managed_alloc(&opaque,0,&right)) return 76;
    if (TOKEN_ENTRY(left,right)!=42 || pcc_gc_scheduler_root_count()!=roots+2) return 77;
    returned=TOKEN_FORWARD(left);
    if (!returned || returned==left || !pcc_rt_managed_identity_equal(returned,left)) return 78;
    if (pcc_gc_scheduler_root_count()!=roots+3) return 79;
    pcc_rt_managed_drop(left);
    if (TOKEN_ENTRY(returned,right)!=42 || pcc_gc_scheduler_root_count()!=roots+2) return 80;
    pcc_rt_managed_drop(right);pcc_rt_managed_drop(returned);
    if (TOKEN_FORWARD(0)!=0 || pcc_gc_scheduler_root_count()!=roots) return 81;
    return 0;
}
