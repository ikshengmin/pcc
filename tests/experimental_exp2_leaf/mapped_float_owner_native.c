/* Private scope: test real exact local ranges/frames, not store coverage. */
#include "py_internal.h"
#include <stdint.h>
#include <stdio.h>

#ifdef M10_NATIVE
extern int64_t pcc_exp2_publish_mapped_float(void*,int64_t,void*,const void*);
#define TRY(slot,site,base,map) pcc_exp2_publish_mapped_float(slot,site,base,map)
#else
/* Baseline only has the exact one-word LIFO guard. */
extern int64_t pcc_exp2_lexical_publish_slot(void*);
#define TRY(slot,site,base,map) pcc_exp2_lexical_publish_slot(slot)
#endif
#define C(x,n) do { if(!(x)){ printf("FAIL %d\n",n); return (n); } } while(0)
#define M(k) pcc_exp2_window_metric(k)
#define ENC 6046392306645868487UL

extern int64_t pcc_exp2_window_control(int64_t);
extern int64_t pcc_exp2_window_metric(int64_t);
extern int64_t pcc_exp2_window_expose_cb(void*,int64_t);
extern int64_t pcc_exp2_install_exposure_hook(void*);
extern int64_t pcc_exp2_process_enable(int64_t,int64_t);
extern int64_t pcc_exp2_process_register_gate(void*,void*);
extern void *pcc_exp2_process_take(int64_t,int64_t);
extern int64_t pcc_exp2_process_is_start(void*);
extern int64_t pcc_exp2_process_minor_held(int64_t);
extern int64_t pcc_exp2_window_state;
extern int64_t pcc_thread_world_stop_epoch_held(void);
extern void pcc_gc_backend3_enable_closed_young_domain(void);
extern void pcc_gc_backend3_revoke_closed_young_domain(void);
extern int64_t pcc_thread_start(void **,void *(*)(void *),void *);
extern int64_t pcc_thread_join(void *,void **);
extern int64_t pcc_thread_is_running(void);
extern void pcc_gc_frame_enter_local(void*,void*);
extern void pcc_gc_frame_leave_local(void*);
extern void pcc_gc_frame_enter_lifo_local(void*,void*);
extern void pcc_gc_frame_leave_lifo_local(void*);
extern PyObject *py_gen_frame_new(int64_t);
extern int64_t pcc_gc_local_frame_enter(void*,void*,int64_t,int64_t,int64_t);
extern int64_t pcc_gc_local_frame_leave(void*,int64_t);
extern int64_t py_obj_mul_slots(void*,void*,void*);

static int32_t map16=16, same_count_wrong_map=16, borrowed=-16, borrowed_two=-2, one=1, two=2;
static uintptr_t cells[4];
static int gate_calls, task_calls;
static void **task_opaque_slot;
static int64_t task_result;
static void *decode(int p){return (void*)(cells[p]^ENC);}

__attribute__((noinline))
static int64_t nested(void **slot,void **base) {
    /* A real call frame with independent positive scratch. Seeing an older
       active map elsewhere on this same carrier does not authorize it. */
    void *scratch[2]={NULL,NULL};
    pcc_gc_frame_enter_local(&two,scratch);
    int64_t r=TRY(slot,1,base,&map16);
    pcc_gc_frame_leave_local(scratch);
    return r;
}

static PyObject *task_probe(PyObject *gen, PyObject *frame){
    (void)frame;
    if(py_virtual_thread_current()!=NULL) task_calls++;
    task_result=TRY(task_opaque_slot,5,task_opaque_slot-3,&map16);
    return py_gen_finish(gen,py_None);
}

static void *other_carrier(void *context){
    void **outer=(void **)context;
    void *self[16]={0};
    pcc_gc_frame_enter_local(&map16,self);
    if(TRY(outer+3,1,outer,&map16)!=0)return (void*)1;
    const void *peer=outer[3]; /* old test owner stays live; no task exposes it. */
    self[3]=pcc_exp2_process_take(24,PY_TYPE_FLOAT);
    if(!self[3] || TRY(self+3,5,self,&map16)!=1)return (void*)2;
    self[3]=(void *)peer;
    if(TRY(self+3,5,self,&map16)!=0)return (void*)3;
    self[3]=NULL;
    pcc_gc_frame_leave_local(self);
    return NULL;
}

static int exact_and_misses(void *ordinary){
    void *own[16]={0}; void *alias[16]={0};
    own[3]=py_float_from_f64(3.25);
    C(pcc_exp2_process_is_start(own[3]),10);
    C(TRY(own+3,1,own,&map16)==0,11);
    volatile int conditional=0;
    if(conditional){ pcc_gc_frame_enter_local(&map16,own); pcc_gc_frame_leave_local(own); }
    C(TRY(own+3,1,own,&map16)==0,12);
    pcc_gc_frame_enter_local(&borrowed,own);
    C(TRY(own+3,1,own,&map16)==0,13);
    pcc_gc_frame_leave_local(own);
    pcc_gc_frame_enter_local(&map16,own);
    C(TRY(own+3,1,own,&map16)==1,14);
    C(TRY(own+3,5,own,&map16)==1 && M(101)==1 && M(105)==1 && M(12)==0,15);
    C(TRY(own+3,3,own,&map16)==0 && TRY(own+3,5,own,&same_count_wrong_map)==0,16);
    C(TRY(own+3,1,own+1,&map16)==0 && TRY((char*)(own+3)+1,1,own,&map16)==0,17);
    alias[3]=own[3];
    C(TRY(alias+3,1,alias,&map16)==0,18);
    pcc_thread_no_park_enter();
    C(pcc_gc_local_frame_enter(&borrowed_two,own+2,2,0,0)==1,33);
    pcc_thread_no_park_exit();
    C(TRY(own+3,1,own,&map16)==0,19);
    C(pcc_gc_local_frame_leave(own+2,0)==1,34);
    C(nested(own+3,own)==0,20);
    void *nested_slot=NULL;
    pcc_gc_frame_enter_lifo_local(&one,&nested_slot);
    C(TRY(own+3,1,own,&map16)==1 && M(101)==2,21);
    pcc_gc_frame_leave_lifo_local(&nested_slot);

    void *th=NULL,*rv=NULL;
    C(pcc_thread_start(&th,other_carrier,own)==0 && pcc_thread_join(th,&rv)==0 && rv==NULL,22);
    C(M(105)==2,23);
    void *narrow[2]={NULL,own[3]};
    pcc_gc_frame_enter_local(&two,narrow);
    C(TRY(narrow+1,1,narrow,&two)==1 && M(101)==3,35);
    pcc_gc_frame_leave_local(narrow);

    task_opaque_slot=own+3;
    PyObject *task_frame=py_gen_frame_new(0);
    PyObject *task_gen=py_gen_new((void*)task_probe,task_frame);
    PyObject *task=py_virtual_thread_new(task_gen);
    C(task && py_virtual_thread_start(task)==0,24);
    C(py_virtual_thread_run_until_idle(8)>=0 && task_calls==1 && task_result==0,25);
    task_opaque_slot=NULL;
    /* Tail heap/gen list slot is not an active caller range. */
    PyListObject *heap=(PyListObject*)py_gen_frame_new(16);
    C(heap && TRY(heap->items+3,5,heap->items,&map16)==0,26);

    own[4]=ordinary;
    C(TRY(own+4,1,own,&map16)==0,27); /* C/ordinary or stock-heap provenance. */
    own[4]=py_str_new("leaf shape mismatch",19);
    C(pcc_exp2_process_is_start(own[4])&&TRY(own+4,1,own,&map16)==0,28);
    own[4]=(char*)own[3]+8;
    C(TRY(own+4,1,own,&map16)==0,29);
    own[4]=NULL;
    pcc_gc_pin(own[3]);
    C(TRY(own+3,1,own,&map16)==0 && M(12)==1,30); /* real outer pin */
    pcc_gc_unpin(own[3]);
    C(TRY(own+3,1,own,&map16)==0,31); /* page is still EXPOSED */
    pcc_gc_frame_leave_local(own);
    C(TRY(own+3,1,own,&map16)==0 && M(101)==3 && M(105)==2,32);
    puts("PASS active owned range, aliases, nested task/carrier, conditional, borrowed, unregistered, interior, outer pin, exposed");
    return 0;
}

static int64_t closed_fixture_gate(int64_t epoch,void *context) {
    /* This case creates no heap/foreign young owners. No young address is
       marked here; the real registered/saved-stack cut must keep the live one.
       This fixture does not imitate an admitted image's whole-old verifier. */
    if(context!=&gate_calls || pcc_thread_world_stop_epoch_held()!=epoch)return 0;
    gate_calls++;
    return 1;
}
static int fill(void){
    for(int i=0;i<3*128;i++){
        void *p=pcc_exp2_process_take(24,PY_TYPE_FLOAT);
        if(!p)return 0;
        if(i%128==0)cells[i/128]=((uintptr_t)p)^ENC;
    }
    return 1;
}
__attribute__((noinline))
static int dead_publication(void) {
    void *slots[16]={0}; slots[7]=decode(0);
    pcc_gc_frame_enter_local(&map16,slots);
    int status=TRY(slots+7,1,slots,&map16)==1;
    slots[7]=NULL;
    pcc_gc_frame_leave_local(slots);
    return status;
}
__attribute__((noinline))
static void scrub(void){
    volatile uintptr_t slots[2048];
    for(int i=0;i<2048;i++)slots[i]=70447000UL+i;
}
static int setup_cut(void) {
    pcc_gc_backend3_enable_closed_young_domain();
    C(pcc_stop_the_world()==0,40);
    C(pcc_exp2_process_enable(2,4)==1
      && pcc_exp2_install_exposure_hook((void*)pcc_exp2_window_expose_cb)==1
      && pcc_exp2_process_register_gate((void*)closed_fixture_gate,&gate_calls)==1,41);
    C(pcc_resume_world()==0,42);
    /* A controlled fixture-specific gate (not a sealed-handler replay). */
    pcc_exp2_window_state=2;
    return 0;
}
static int cut(void) {
    int setup=setup_cut();
    if(setup)return setup;
    C(fill() && dead_publication(),43);
    scrub();
    void *live[16]={0}; live[9]=decode(1);
    pcc_gc_frame_enter_local(&map16,live);
    C(TRY(live+9,5,live,&map16)==1 && M(101)==1 && M(105)==1 && M(12)==0,44);
    C(pcc_stop_the_world()==0,45);
    int64_t e=pcc_thread_world_stop_epoch_held(), pages=pcc_exp2_process_minor_held(e);
    C(pcc_resume_world()==0,46);
    C(pages>0 && gate_calls==1 && M(12)==0 && M(2)==1,47);
    C(!pcc_exp2_process_is_start(decode(0)) && pcc_exp2_process_is_start(live[9]),48);
    C(TRY(live+9,5,live,&map16)==0 && M(105)==1,49); /* already OLD */
    live[9]=NULL;
    pcc_gc_frame_leave_local(live);
    printf("PASS actual held cut recycled=%lld and retained the live mapped page (no sticky); OLD denied\n",(long long)pages);
    return 0;
}
static int shared_escape(void *ordinary) {
    int setup=setup_cut();
    if(setup)return setup;
    void *slots[16]={0};
    pcc_gc_frame_enter_local(&map16,slots);
    slots[3]=py_float_from_f64(2.0);
    slots[4]=ordinary; /* allocated before the process pool */
    C(slots[3] && TRY(slots+3,1,slots,&map16)==1 && M(101)==1 && M(12)==0,50);
    C(py_obj_mul_slots(slots+3,slots+4,slots+5)==0 && slots[5],51);
    uintptr_t temp=(uintptr_t)slots[3], final=(uintptr_t)slots[5];
    C(temp!=final && (temp>>12)==(final>>12) && pcc_exp2_process_is_start(slots[5]),52);
    C(M(12)==1 && M(94)==1 && M(73)==1 && TRY(slots+3,1,slots,&map16)==0,53);
    slots[3]=NULL;
    slots[6]=py_float_from_f64(33.0);
    C(pcc_exp2_process_is_start(slots[6]) && ((uintptr_t)slots[6]>>12)!=(final>>12),54);
    C(pcc_stop_the_world()==0,55);
    int64_t e=pcc_thread_world_stop_epoch_held(), pages=pcc_exp2_process_minor_held(e);
    C(pcc_resume_world()==0,56);
    C(pages>=0 && gate_calls==1 && M(101)==1 && M(105)==0 && M(12)==1
      && pcc_exp2_process_is_start(slots[5]) && py_float_to_f64(slots[5])==14.0,57);
    slots[4]=slots[5]=slots[6]=NULL;
    pcc_gc_frame_leave_local(slots);
    puts("PASS accepted temp shared a page with the original mul-adopt escape; page stopped bumping and final value survived");
    return 0;
}

int main(int argc,char **argv){
    int64_t backend=pcc_gc_backend(); py_gc_disable();
    void *ordinary=py_float_from_f64(7.0),*pre[16]={0};
    C(ordinary && pcc_thread_is_running()==1,1);
    pre[3]=ordinary; pcc_gc_frame_enter_local(&map16,pre);
    C(TRY(pre+3,1,pre,&map16)==0 && M(101)==0,2);
    pcc_gc_frame_leave_local(pre);
    if(backend!=3){ puts("PASS unchanged out-of-window collector");return 0; }
    if(argc>1&&argv[1][0]=='c')return cut();
    if(argc>1&&argv[1][0]=='e')return shared_escape(ordinary);
    pcc_gc_backend3_enable_closed_young_domain();
    C(pcc_exp2_window_control(1)==1 && pcc_exp2_window_control(2)==1,3);
    int r=exact_and_misses(ordinary);
    if(r)return r;
    pre[3]=py_float_from_f64(81.0);
    C(pcc_exp2_process_is_start(pre[3]),6);
    pcc_gc_frame_enter_local(&map16,pre);
    pcc_gc_backend3_revoke_closed_young_domain();
    C(TRY(pre+3,5,pre,&map16)==0 && M(105)==2,4);
    pcc_gc_frame_leave_local(pre);
    C(pcc_exp2_window_control(3)==1,5);
    return 0;
}
