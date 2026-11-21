/* Normal, finite admission and first-exposure counts by allocated size class. */
#include "py_internal.h"
#include <stdio.h>
#include <stdint.h>
extern int64_t pcc_exp2_process_enable(int64_t,int64_t);
extern void* pcc_exp2_process_take(int64_t,int64_t);
extern int64_t pcc_exp2_process_expose(void*,int64_t);
extern int64_t pcc_exp2_process_metric(int64_t);
#define M(k) pcc_exp2_process_metric(k)
#define C(x,n) do {if(!(x)) {printf("FAIL %d\n",n);return n;}}while(0)
int main(void){
  C(pcc_gc_backend()==3 && py_float_from_f64(1.0),1);py_gc_disable();C(pcc_stop_the_world()==0,2);
  C(pcc_exp2_process_enable(1,3)==1 && pcc_resume_world()==0,3);
  for(int k=0;k<2;k++){
    int size=k?127:24, tag=k?PY_TYPE_STR:PY_TYPE_FLOAT;
    for(int reason=1;reason<=3;reason++){
      void *p=pcc_exp2_process_take(size,tag);C(p!=0,4);
      C(pcc_exp2_process_expose(p,reason)==1 && pcc_exp2_process_expose(p,6)==1,5);
    }
    for(int i=0;i<2+k;i++)C(!pcc_exp2_process_take(size,tag),6);
  }
  printf("float misses=%ld pin=%ld lease=%ld image=%ld other=%ld; string misses=%ld pin=%ld lease=%ld image=%ld other=%ld\n",(long)M(70),(long)M(72),(long)M(73),(long)M(74),(long)M(75),(long)M(71),(long)M(76),(long)M(77),(long)M(78),(long)M(79));
  C(M(70)==2 && M(71)==3 && M(72)==1 && M(73)==1 && M(74)==1 && M(75)==0,7);
  C(M(76)==1 && M(77)==1 && M(78)==1 && M(79)==0 && M(12)==6,8);return 0;
}
