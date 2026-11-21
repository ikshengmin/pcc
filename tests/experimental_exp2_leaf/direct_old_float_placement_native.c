/* Scoped test of first-allocation placement. No handler work or performance. */
#include "py_internal.h"
#include <stdio.h>
#include <stdint.h>

#ifdef M11_NATIVE
extern PyObject *pcc_exp2_float_from_f64_old(double);
extern int64_t pcc_exp2_obj_mul_slots_old(void*,void*,void*);
#define OLD_CLOCK pcc_exp2_float_from_f64_old
#define OLD_MUL pcc_exp2_obj_mul_slots_old
#else
extern int64_t py_obj_mul_slots(void*,void*,void*);
#define OLD_CLOCK py_float_from_f64
#define OLD_MUL py_obj_mul_slots
#endif

extern int64_t pcc_exp2_window_control(int64_t);
extern int64_t pcc_exp2_window_metric(int64_t);
extern int64_t pcc_exp2_window_expose_cb(void*,int64_t);
extern int64_t pcc_exp2_window_state;
extern int64_t pcc_exp2_install_exposure_hook(void*);
extern int64_t pcc_exp2_process_enable(int64_t,int64_t);
extern int64_t pcc_exp2_process_register_gate(void*,void*);
extern int64_t pcc_exp2_process_minor_held(int64_t);
extern int64_t pcc_exp2_process_is_start(void*);
extern int64_t pcc_thread_world_stop_epoch_held(void);
extern void *pcc_gc_object_index_find_stable(void*);
extern void pcc_gc_backend3_enable_closed_young_domain(void);
extern int64_t pcc_gc_foreign_lease_acquire(void*);
extern int64_t pcc_gc_foreign_lease_release(void*,int64_t);
extern int64_t pcc_gc_root_move(void*,void*);
extern void pcc_gc_frame_enter_local(void*,void*);
extern void pcc_gc_frame_leave_local(void*);
extern int64_t pcc_thread_is_running(void);

#define M(k) pcc_exp2_window_metric(k)
#define C(x,n) do { if(!(x)){printf("FAIL %d\n",n);return n;} } while(0)
static int map4=4, gates;
static uintptr_t dead_encoded;
#define MARK 70427280125436590UL
static int old_ok(void *p,double v){
    if(!p || pcc_exp2_process_is_start(p) || py_float_to_f64(p)!=v)return 0;
    void **node=(void **)pcc_gc_object_index_find_stable(p);
    return node && node[0]==p && ((int64_t*)node)[1]==24 && ((int64_t*)node)[4]==0;
}
static int64_t fixture_gate(int64_t epoch,void *ctx) {
    if(ctx!=&gates || epoch!=pcc_thread_world_stop_epoch_held())return 0;
    gates++;
    return 1; /* constructed witness has no hidden/heap young edges */
}
static int cut_setup(void){
    pcc_gc_backend3_enable_closed_young_domain();
    C(pcc_stop_the_world()==0,10);
    C(pcc_exp2_process_enable(1,4)==1 &&
      pcc_exp2_install_exposure_hook((void*)pcc_exp2_window_expose_cb)==1 &&
      pcc_exp2_process_register_gate((void*)fixture_gate,&gates)==1,11);
    C(pcc_resume_world()==0,12);
    pcc_exp2_window_state=2;
    return 0;
}

__attribute__((noinline))
static int make_escaping(void **out) {
    void *s[4]={0};
    pcc_gc_frame_enter_local(&map4,s);
    s[0]=py_float_from_f64(3.5);
    C(pcc_exp2_process_is_start(s[0])&&M(14)==1&&M(12)==0,20);
    dead_encoded=((uintptr_t)s[0])^MARK;
    s[1]=OLD_CLOCK(4.0);
    C(old_ok(s[1],4.0)&&M(111)==1&&M(14)==1&&M(12)==0,21);
    int64_t lease=pcc_gc_foreign_lease_acquire(s+1);
    C(lease>=0 && pcc_gc_foreign_lease_release(s+1,lease)==0,22);
    C(OLD_MUL(s,s+1,s+2)==0 && old_ok(s[2],14.0),23);
    C(M(111)==1&&M(114)==1&&M(14)==1&&M(15)==24&&M(12)==0&&M(94)==0,24);
    /* Both formerly sticky producers really escape via ordinary address
       consumer/transfer. No site-4 behavior is disabled: the result is old. */
    C(pcc_gc_root_move(out,s+1)==0 && pcc_gc_root_move(out+1,s+2)==0,25);
    s[3]=py_float_from_f64(2.0);
    C(pcc_exp2_process_is_start(s[3])&&((uintptr_t)s[0]>>12)==((uintptr_t)s[3]>>12)&&M(14)==2,26);
    s[0]=s[3]=NULL;
    pcc_gc_frame_leave_local(s);
    return 0;
}
__attribute__((noinline))
static void scrub(void){
    volatile uintptr_t words[2048];
    for(int i=0;i<2048;i++)words[i]=78062788UL+i;
}
static int live_cut(void){
    int r=cut_setup();
    if(r)return r;
    void *out[4]={0};
    pcc_gc_frame_enter_local(&map4,out);
    r=make_escaping(out);
    if(r)return r;
    scrub();
    C(pcc_stop_the_world()==0,30);
    int64_t epoch=pcc_thread_world_stop_epoch_held();
    int64_t recycled=pcc_exp2_process_minor_held(epoch);
    C(pcc_resume_world()==0,31);
    C(recycled>=1&&gates==1&&M(12)==0&&!pcc_exp2_process_is_start((void*)(dead_encoded^MARK)),32);
    C(old_ok(out[0],4.0)&&old_ok(out[1],14.0),33);
    out[0]=out[1]=NULL;
    pcc_gc_frame_leave_local(out);
    printf("PASS escaped old values 4 and 14; minor returned %lld earlier temporary page(s); no sticky transition\n",(long long)recycled);
    return 0;
}

static int ordinary(int mode){
    void *s[4]={0};
    pcc_gc_frame_enter_local(&map4,s);
    s[0]=OLD_CLOCK(-2.0); s[1]=py_float_from_f64(3.0);
    C(s[0]&&s[1]&&!pcc_exp2_process_is_start(s[0]),40);
    C(OLD_MUL(s,s+1,s+2)==0&&s[2]&&py_float_to_f64(s[2])==-6.0&&M(111)==0&&M(114)==0,41);
    s[0]=s[1]=s[2]=NULL;
    pcc_gc_frame_leave_local(s);
    if(mode!=3){puts("PASS collector outside scope unchanged");return 0;}
    pcc_gc_backend3_enable_closed_young_domain();
    C(pcc_exp2_window_control(1)==1&&pcc_exp2_window_control(2)==1,42);
    pcc_gc_frame_enter_local(&map4,s);
    int r=make_escaping(s);
    if(r)return r;
    s[0]=py_float_from_f64(2.25);
    s[1]=py_int_from_i64(2);
    C(OLD_MUL(s,s+1,s+2)==0&&pcc_exp2_process_is_start(s[2])&&py_float_to_f64(s[2])==4.5,43);
    C(M(111)==1&&M(114)==1&&M(94)==1&&M(12)==1,44);
    C(OLD_MUL(s,s+1,s+2)<0&&M(114)==1&&py_err_occurred(),45);
    py_clear_exception();
    s[0]=s[1]=s[2]=NULL;
    pcc_gc_frame_leave_local(s);
    C(pcc_exp2_window_control(3)==1&&py_float_to_f64(OLD_CLOCK(8.0))==8.0&&M(111)==1,46);
    puts("PASS scoped old direct counts 1/1, dynamic FLOAT*INT remains real legacy site4, error result and closed window unchanged");
    return 0;
}
int main(int argc,char **argv){
    int64_t mode=pcc_gc_backend();
    py_gc_disable();
    C(py_float_from_f64(1.0) && pcc_thread_is_running()==1,1); /* freeze + register */
    if(mode==3&&argc>1&&argv[1][0]=='c')return live_cut();
    return ordinary(mode);
}
