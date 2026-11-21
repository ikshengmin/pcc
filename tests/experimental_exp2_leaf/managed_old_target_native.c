/* Native managed mutation regression: see the actual old-list backing store
 * through the normal stopped old traversal; no substitute old-edges callback
 * and no artificial calls to either remembered or reference observer. */
#include "py_internal.h"
#include <stdint.h>
#include <stdio.h>
extern int64_t pcc_exp2_window_control(int64_t);
extern int64_t pcc_exp2_window_metric(int64_t);
extern int64_t pcc_exp2_window_stop_minor(int64_t);
extern int64_t pcc_exp2_process_is_start(void*);
extern int64_t pcc_thread_world_stop_epoch_held(void);
extern int64_t pcc_thread_is_running(void);
extern void *pcc_gc_object_index_find_stable(void*);
#define M(k) pcc_exp2_window_metric(k)
#define C(p,n) do{if(!(p)){printf("FAIL %d st=%ld tag=%ld why=%ld dirty=%ld actual=%ld sticky=%ld list=%ld frame=%ld\n",n,(long)M(30),(long)M(32),(long)M(31),(long)M(50),(long)M(51),(long)M(12),(long)M(63),(long)M(67));return n;}}while(0)
static void scrub(void){volatile uintptr_t words[1024];for(int i=0;i<1024;i++)words[i]=(uintptr_t)(i+710103);}
static int fill(PyObject *a, PyObject *frame) {
    char payload[86];
    for(int k=0;k<7*32;k++) {
        for(int j=0;j<86;j++) payload[j]=(char)('A'+k/32);
        PyObject *s = py_str_new(payload,86);
        C(s && pcc_exp2_process_is_start(s) && !pcc_gc_object_index_find_stable(s),10);
        if(k==0) py_list_append(a,s);         /* A stays in a real list. */
        if(k==32)py_gen_frame_set(frame,0,s); /* B overwritten, conservative extra page. */
        if(k==64)py_gen_frame_set(frame,0,s); /* C stays in real heap generator frame list. */
    }
    return 0;
}
int main(int argc, char **argv){
    py_set_program_args(argc,(const char **)argv);
    C(pcc_gc_backend()==3,1);py_gc_disable();
    C(pcc_current_thread_id()>0 && pcc_thread_is_running()==1,2);
    C(py_float_from_f64(5.0)!=NULL,3);
    PyObject *a=py_list_new(4);PyObject *frame=py_gen_frame_new(1);
    C(a && frame && pcc_gc_object_index_find_stable(a) && pcc_gc_object_index_find_stable(frame),4);
    /* py_gen_frame_set with a list is an ordinary production frame path. */
    /* An independently checked, exactly bound test image may admit;
       generic C hosts, changed inputs, and unverified images must not. */
    if(pcc_exp2_window_control(4)!=1) {
        C(pcc_exp2_window_control(1)<0 && M(0)==0 && M(5)==0,16);
        puts("UNQUALIFIED IMAGE OR INPUT: no admission and no recycling");
        return 16;
    }
    C(pcc_exp2_window_control(1)==1 && pcc_exp2_window_control(2)==1,5);
    C(fill(a,frame)==0,6);scrub();
    C(M(0)==7*32 && M(12)==0 && M(63)>=1 && M(67)>=2,7);
    C(pcc_stop_the_world()==0,8);
    int64_t e=pcc_thread_world_stop_epoch_held();
    int64_t reclaimed=pcc_exp2_window_stop_minor(e);
    C(pcc_resume_world()==0,9);
    C(reclaimed>0 && M(2)==1 && M(34)==0 && M(35)>=2 && M(36)>=2,11);
    C(M(50)==3 && M(51)==2 && M(52)==1 && M(53)==0,12);
    PyObject *l=py_list_get(a,0); PyObject *g=py_list_get(frame,0);
    C(l && g && l!=g && pcc_exp2_process_is_start(l) && pcc_exp2_process_is_start(g),13);
    C(py_str_byte_len(l)==86 && py_str_byte_len(g)==86,14);
    for(int j=0;j<86;j++) C(py_str_utf8(l)[j]=='A' && py_str_utf8(g)[j]=='C',15);
    printf("managed list+generator-frame: nursery=%ld oldOwners=%ld oldSlots=%ld journal=%ld nativeRef=%ld stale=%ld missed=%ld recycled=%ld promoted=%ld listWrites=%ld frameWrites=%ld sticky=%ld\n",
      (long)M(0),(long)M(35),(long)M(36),(long)M(50),(long)M(51),(long)M(52),(long)M(53),(long)M(5),(long)M(6),(long)M(63),(long)M(67),(long)M(12));
    return 0;
}
