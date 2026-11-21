/* Diagnostic-only ABI witness. No actual handler seal/admission bypass. */
#include "py_internal.h"
#include <stdint.h>
#include <stdio.h>

extern int64_t pcc_exp2_foreign_lease_acquire_site(void *,int64_t);
extern int64_t pcc_gc_foreign_lease_acquire(void *);
extern int64_t pcc_gc_foreign_lease_release(void *,int64_t);
extern int64_t pcc_exp2_window_expose_cb(void *,int64_t);
extern int64_t pcc_exp2_install_exposure_hook(void *);
extern int64_t pcc_exp2_process_enable(int64_t,int64_t);
extern void *pcc_exp2_process_take(int64_t,int64_t);
extern int64_t pcc_exp2_process_metric(int64_t);
extern int64_t pcc_exp2_window_state;
extern int64_t py_obj_mul_slots(void *,void *,void *);
extern void pcc_gc_frame_enter_lifo_local(void *,void *);
extern void pcc_gc_frame_leave_lifo_local(void *);
static int32_t one=1;

#define C(x,n) do{if(!(x)){printf("FAIL %d\n",n);return n;}}while(0)
#define M(k) pcc_exp2_process_metric(k)
static int64_t reasons[3], depth;
static void *separate;

static int64_t other_hook(void *word,int64_t reason) {
    if (depth==1) { reasons[1]=reason; return 0; }
    reasons[0]=reason; depth=1;
    int64_t token=pcc_exp2_foreign_lease_acquire_site(&separate,4);
    reasons[2]=token;
    depth=0;
    return 0;
}

static int ordinary(void) {
    void *x=py_float_from_f64(2.75);
    C(x!=NULL,30);
    int64_t old=pcc_gc_foreign_lease_acquire(&x);
    C(old>=0 && pcc_gc_foreign_lease_release(&x,old)==0,31);
    int64_t tagged=pcc_exp2_foreign_lease_acquire_site(&x,3);
    C(tagged>=0 && pcc_gc_foreign_lease_release(&x,tagged)==0,32);
    C(py_float_to_f64(x)==2.75 && M(73)==0 && M(93)==0,33);
    C(pcc_gc_foreign_lease_acquire(NULL)==-1 && pcc_exp2_foreign_lease_acquire_site(NULL,3)==-1,34);
    puts("PASS original vs attributed ordinary lease: content, success and refusal unchanged");
    return 0;
}

static int custom(void) {
    void *x=py_float_from_f64(3.25);
    separate=py_float_from_f64(4.25);
    C(x&&separate,39);
    C(pcc_stop_the_world()==0 && pcc_exp2_install_exposure_hook((void*)other_hook)==1,40);
    C(pcc_resume_world()==0,41);
    pcc_exp2_window_state=2;
    int64_t token=pcc_exp2_foreign_lease_acquire_site(&x,1);
    C(token==0 && pcc_gc_foreign_lease_release(&x,token)==0,42);
    C(reasons[0]==2 && reasons[1]==2 && reasons[2]==0 && M(73)==0,43);
    puts("PASS custom exposure hook sees reason 2 at both outer and nested acquires");
    return 0;
}

static int via(void **value,int64_t site) {
    void *prior=*value;
    int64_t token=pcc_exp2_foreign_lease_acquire_site(value,site);
    return token==0 && pcc_gc_foreign_lease_release(value,token)==0 && *value==prior;
}
static int qualified(void) {
    void *left=py_float_from_f64(3.25),*right=py_float_from_f64(4.0),*result=NULL;
    C(left&&right,1);
    C(pcc_stop_the_world()==0 && pcc_exp2_process_enable(1,16)==1,2);
    C(pcc_exp2_install_exposure_hook((void *)pcc_exp2_window_expose_cb)==1,3);
    C(pcc_resume_world()==0,4);
    void *early=pcc_exp2_process_take(24,PY_TYPE_FLOAT);
    C(early&&via(&early,1)&&M(90)==1&&M(91)==0,5);
    pcc_exp2_window_state=2;
    for(int i=1;i<=6;i++){
        void *value=pcc_exp2_process_take(24,PY_TYPE_FLOAT);
        C(value&&via(&value,i)&&M(90+i)==1,6);
        C(via(&value,(i%6)+1)&&M(90+i)==1&&M(73)==i+1,7);
    }
    void *unknown=pcc_exp2_process_take(24,PY_TYPE_FLOAT);
    C(unknown&&via(&unknown,17)&&M(90)==2,8);
    void *legacy=pcc_exp2_process_take(24,PY_TYPE_FLOAT);
    C(legacy&&pcc_gc_foreign_lease_acquire(&legacy)==0&&M(90)==3,9);
    C(M(73)==9,10);
    void *pinned=pcc_exp2_process_take(24,PY_TYPE_FLOAT);
    C(pinned,11);
    pcc_gc_pin(pinned);
    pcc_gc_unpin(pinned);
    C(via(&pinned,3)&&M(72)==1&&M(73)==9&&M(93)==1,12);
    void *s=pcc_exp2_process_take(127,PY_TYPE_STR);
    C(s&&via(&s,4)&&M(77)==1&&M(94)==1&&M(13)==0,13);
    int64_t n=0;
    for(int i=90;i<=96;i++)n+=M(i);
    C(n==9&&M(73)==n&&M(12)==11,14);
    puts("PASS first FLOAT lease sites are exclusive, conserved, token-zero; retained pin and STR classes excluded");
    pcc_gc_frame_enter_lifo_local(&one,&left);
    pcc_gc_frame_enter_lifo_local(&one,&right);
    pcc_gc_frame_enter_lifo_local(&one,&result);
    C(py_obj_mul_slots(&left,&right,&result)==0 && result && py_float_to_f64(result)==13.0,15);
    C(M(94)==2 && M(73)==10 && M(12)==12,16);
    pcc_gc_frame_leave_lifo_local(&result);
    pcc_gc_frame_leave_lifo_local(&right);
    pcc_gc_frame_leave_lifo_local(&left);
    puts("PASS actual py_obj_mul_slots result first leases at native _mul_adopt");
    return 0;
}
int main(int argc,char **argv) {
    if (argc>=2 && argv[1][0]=='o') return ordinary();
    if (argc>=2 && argv[1][0]=='c') return custom();
    return qualified();
}
