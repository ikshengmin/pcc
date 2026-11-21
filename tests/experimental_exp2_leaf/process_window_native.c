/* Probe the integrated owned runtime, no mock gate or bridge. Run each mode in
 * a new process. The positive is isolated from the normal escaped C ABI. */
#include "py_internal.h"
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
extern int64_t pcc_exp2_window_control(int64_t);
extern int64_t pcc_exp2_window_metric(int64_t);
extern int64_t pcc_exp2_window_stop_minor(int64_t);
extern int64_t pcc_exp2_process_is_start(void *);
extern int64_t pcc_exp2_process_contains(void *);
extern int64_t pcc_exp2_process_expose(void *,int64_t);
extern void *pcc_exp2_process_take(int64_t,int64_t);
extern int64_t pcc_thread_world_stop_epoch_held(void);
extern int64_t pcc_thread_is_running(void);
extern void *pcc_gc_object_index_find_stable(void *);
extern int64_t pcc_exp2_exposure_is_revoked(void);
#define C(x,k) do { if (!(x)) {printf("FAIL %d window=%ld reason=%ld type=%ld admitted=%ld attempts=%ld denied=%ld\n", (k),(long)M(30),(long)M(31),(long)M(32),(long)M(0),(long)M(33),(long)M(34));return(k);} } while(0)
#define M(i) pcc_exp2_window_metric(i)
static int fill(void) {
 for(int i=0;i<3*128+1;i++){
  PyObject *f=py_float_from_f64(3.25); if(!pcc_exp2_process_is_start(f) || pcc_gc_object_index_find_stable(f))return 1;
 }
 return 0;
}
static void clear_dead(void) { volatile uintptr_t v[2048];for(int i=0;i<2048;i++)v[i]=612389L+i*2; }
int main(int argc,char **argv){
 if(argc!=2)return 1;
 int b=pcc_gc_backend(); C(pcc_current_thread_id()>0 && pcc_thread_is_running()==1,2);
 PyObject *first=py_float_from_f64(42.5); C(first!=0 && !pcc_exp2_process_is_start(first),3);
 if(b!=3){C(pcc_exp2_window_control(1)==0 && M(30)==0 && M(0)==0,4);printf("GC%d_BASE_CONTROL_OK\n",b);return 0;}
 if(argv[1][0]=='d'){ C(pcc_exp2_window_control(1)<0 && M(0)==0 && M(2)==0,5);puts("C_HOST_NOT_CLOSED_REFUSED");return 0;}
 /* The controlled C fixture mirrors the sanctioned generated image entry. */
 pcc_gc_backend3_enable_closed_young_domain();
 C(pcc_exp2_window_control(1)==1 && M(0)==0 && M(30)==1,6);
 C(pcc_exp2_window_control(2)==1 && M(30)==2,7);
 if(argv[1][0]=='r'){
  PyObject *f=py_float_from_f64(1.0); C(pcc_exp2_process_is_start(f),8);
  C(pcc_exp2_process_expose(NULL,128)==-1 && M(13)==1,9);
  C(pcc_exp2_process_take(24,PY_TYPE_FLOAT)==NULL && pcc_exp2_process_is_start(f),10);
  C(pcc_stop_the_world()==0,11); C(pcc_exp2_window_stop_minor(pcc_thread_world_stop_epoch_held())<0,12);C(pcc_resume_world()==0 && M(2)==0,13);puts("OPAQUE_NULL_REJECTS_FURTHER_BUMPS_AND_MINOR");return 0;
 }
 if(argv[1][0]=='b'){
  PyObject *f=py_float_from_f64(1.0); C(f&&pcc_exp2_process_is_start(f),14);
  int64_t v=pcc_thread_blocking_enter(NULL); C(v!=0 && pcc_thread_is_running()==0,15);
  void *shouldfail=pcc_exp2_process_take(24,PY_TYPE_FLOAT);pcc_thread_blocking_leave(v);
  C(!shouldfail && pcc_thread_is_running()==1,16); puts("BLOCKING_LANE_NOT_A_MUTATOR");return 0;
 }
 C(fill()==0 && M(14)==385 && M(1)==24*385,17);
 /* Keep just one ordinary live exact or interior object on caller's stack;
    the current collector may conservatively keep additional page co-tenants. */
 volatile void *hold=py_str_new("kept",4); C(pcc_exp2_process_is_start((void *)hold),18);
 clear_dead();
 C(pcc_stop_the_world()==0,19);int64_t epoch=pcc_thread_world_stop_epoch_held();C(epoch>0,20);
 int64_t reclaimed=pcc_exp2_window_stop_minor(epoch);
 C(pcc_resume_world()==0,21);
 C(reclaimed>0 && M(2)==1 && M(5)==reclaimed && M(6)>0 && M(4)>0,22);
 C(pcc_exp2_process_is_start((void *)hold) && py_str_byte_len((PyObject *)hold)==4,23);
 C(pcc_exp2_window_control(3)==1 && M(30)==3 && !pcc_exp2_process_is_start(py_float_from_f64(5.0)),24);
 printf("WHOLE_OWNED_RUNTIME_OK allocations=%ld requested=%ld minors=%ld recycled=%ld promoted=%ld max_minor_us=%ld old_owners=%ld old_slots=%ld\n",(long)M(0),(long)M(1),(long)M(2),(long)M(5),(long)M(6),(long)M(4),(long)M(35),(long)M(36));return 0;
}
