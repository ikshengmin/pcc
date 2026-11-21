/* Bounded native GC protocol test. All references here belong to this process. */
#include "py_internal.h"
#include <stdint.h>
#include <stdio.h>
extern int64_t pcc_exp2_process_enable(int64_t,int64_t);
extern int64_t pcc_exp2_process_register_gate(void*,void*);
extern void* pcc_exp2_process_take(int64_t,int64_t);
extern int64_t pcc_exp2_process_is_start(void*);
extern int64_t pcc_exp2_note_old_target(void*,int64_t);
extern void pcc_exp2_process_old_reference(void*,int64_t);
extern int64_t pcc_exp2_process_metric(int64_t);
extern int64_t pcc_exp2_process_minor_held(int64_t);
extern int64_t pcc_thread_world_stop_epoch_held(void);
static void *old_slots[2];  /* standing in for a known, separately enumerated old owner */
static int allow;
static int64_t visit(int64_t epoch,void*arg) {
  if(!allow) return 0;
  pcc_exp2_process_old_reference(old_slots[0],12);
  pcc_exp2_process_old_reference(old_slots[1],12);
  return pcc_thread_world_stop_epoch_held()==epoch;
}
#define M(k) pcc_exp2_process_metric(k)
#define C(x,n) do {if(!(x)){printf("FAIL %d\n",n);return n;}}while(0)
int main(void){
  /* The test stores three valid bytestrings on three distinct in-process pages. */
  C(pcc_gc_backend()==3,1);py_gc_disable();C(py_float_from_f64(5.0)!=NULL,21);
  C(pcc_stop_the_world()==0,2);C(pcc_exp2_process_enable(1,6)==1,3);
  C(pcc_exp2_process_register_gate((void*)visit,NULL)==1,4);C(pcc_resume_world()==0,5);
  void *p[3]={NULL,NULL,NULL};
  for(int i=0;i<96;i++){void *v=pcc_exp2_process_take(127,PY_TYPE_STR);C(v,6);if(i%32==0)p[i/32]=v;}
  C(pcc_exp2_note_old_target(NULL,3)==0,7);
  C(pcc_exp2_note_old_target(p[0],3)==1 && pcc_exp2_note_old_target(p[0],3)==1,8);
  C(pcc_exp2_note_old_target(p[1],4)==1,9);
  old_slots[0]=p[0];old_slots[1]=p[2]; /* The independent full reference sees P0 and P2. */
  C(pcc_stop_the_world()==0,10);
  int64_t e=pcc_thread_world_stop_epoch_held();
  C(pcc_exp2_process_minor_held(e)<0,11);
  C(M(50)==0 && M(51)==0 && M(52)==0 && M(53)==0 && M(63)==2 && M(64)==1,12);
  allow=1;C(pcc_exp2_process_minor_held(e)>=0,13);C(pcc_resume_world()==0,14);
  C(M(50)==2 && M(51)==2 && M(52)==1 && M(53)==1 && M(54)==12,15);
  C(pcc_exp2_process_is_start(p[0]) && pcc_exp2_process_is_start(p[2]),16);
  C(pcc_exp2_note_old_target(p[0],3)==0,17); /* promoted, not a fresh young target */
  C(pcc_stop_the_world()==0,18);e=pcc_thread_world_stop_epoch_held();
  C(pcc_exp2_process_minor_held(e)>=0 && pcc_resume_world()==0,19);
  C(M(50)==2 && M(51)==2 && M(52)==1 && M(53)==1,20);
  printf("remembered=%ld reference=%ld extra=%ld missed=%ld missingType=%ld refused=%ld\n",
       (long)M(50),(long)M(51),(long)M(52),(long)M(53),(long)M(54),(long)M(11));
  return 0;
}
