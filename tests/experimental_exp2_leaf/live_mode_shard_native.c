#include "py_internal.h"
#include <stdio.h>
#include <stdint.h>
extern int64_t pcc_thread_world_stop_epoch_held(void);
extern int64_t pcc_gc_foreign_lease_count_quiescent(void);
extern int64_t pcc_gc_mode_lease_reserve_shard(int64_t);
extern int64_t pcc_gc_mode_lease_release_shard(int64_t);
extern int64_t pcc_gc_foreign_lease_active;
extern void pcc_gc_backend3_enable_closed_young_domain(void);
extern int64_t pcc_exp2_old_domain_status(int64_t);
extern int64_t pcc_exp2_visit_stopped_old_edges(int64_t,void(*)(void*,void*),void*,int64_t*);
static int n;static void trusted(void*a,void*b){(void)a;(void)b;n++;}
int main(void){
    if(pcc_gc_set_backend(3)||!py_list_new(1))return 1;
    py_gc_disable();pcc_gc_backend3_enable_closed_young_domain();
    if(pcc_stop_the_world()!=0)return 2;
    int64_t ep=pcc_thread_world_stop_epoch_held(),audit[12]={0};
    if(ep<=0||pcc_gc_foreign_lease_count_quiescent()!=0||pcc_exp2_old_domain_status(ep)!=1)return 3;
    if(pcc_gc_mode_lease_reserve_shard(17)!=0)return 4;
    int64_t central=pcc_gc_foreign_lease_active;int64_t q=pcc_gc_foreign_lease_count_quiescent();
    int64_t old=pcc_exp2_old_domain_status(ep);int64_t cut=pcc_exp2_visit_stopped_old_edges(ep,trusted,0,audit);
    if(pcc_gc_mode_lease_release_shard(17)!=0)return 5;
    int64_t released=pcc_gc_foreign_lease_count_quiescent(), again=pcc_exp2_old_domain_status(ep);
    if(pcc_resume_world()!=0)return 6;
    printf("central=%lld real_shard_reservation=%lld old_domain=%lld old_cut=%lld callbacks=%d raw_status=%lld released=%lld reopened_domain=%lld\n",(long long)central,(long long)q,(long long)old,(long long)cut,n,(long long)audit[3],(long long)released,(long long)again);
    if(central||q!=1||n||released||again!=1)return 7;
    return old==-3&&cut==-3&&audit[3]==-3?0:8;
}
