/* Private native lexical-operation contract against the M6 allocation and cut. */
#include "py_internal.h"
#include <stdint.h>
#include <stdio.h>

extern void *pcc_exp2_lexical_leaf_slot(void*);
extern void *py_str_encode_with_encoding(void*, void*, void*);
extern int64_t pcc_exp2_lexical_publish_slot(void*);
extern void *pcc_gc_frame_leave_lifo_exp2_take(void*);
extern int64_t pcc_exp2_install_exposure_hook(void*);
extern int64_t pcc_exp2_process_enable(int64_t,int64_t);
extern int64_t pcc_exp2_process_register_gate(void *,void *);
extern void *pcc_exp2_process_take(int64_t,int64_t);
extern int64_t pcc_exp2_process_is_start(void *);
extern int64_t pcc_exp2_process_expose(void *,int64_t);
extern int64_t pcc_exp2_process_minor_held(int64_t);
extern int64_t pcc_exp2_process_metric(int64_t);
extern int64_t pcc_thread_world_stop_epoch_held(void);
extern int64_t pcc_gc_foreign_lease_acquire(void *);
extern int64_t pcc_gc_foreign_lease_release(void *,int64_t);
extern int64_t pcc_gc_pin_slot_for_take(void *,int64_t);
extern void *pcc_gc_take_pinned_slot(void *,int64_t);
extern void pcc_gc_frame_enter_lifo_local(void *,void *);
extern void pcc_gc_frame_leave_lifo_local(void *);
extern int64_t pcc_thread_start(void **,void *(*)(void *),void *);
extern int64_t pcc_thread_join(void *,void **);

#define C(x,n) do { if(!(x)){printf("FAIL %d\n", n);return n;} } while(0)
#define M(i) pcc_exp2_process_metric(i)
#define ENC 6046392306645868487UL

static uintptr_t encoded[4];
static int32_t one=1, two=2, borrowed=-1;
static int gate_calls;
/* The generated positive branch is an unconditional precise-root leave. */
static void *try_take(void *slot){
    return pcc_exp2_lexical_leaf_slot(slot)!=NULL ? pcc_gc_frame_leave_lifo_exp2_take(slot) : NULL;
}
static void *decode(int n){return (void *)(encoded[n]^ENC);}
static int64_t gate(int64_t epoch,void *ctx){
    if(pcc_thread_world_stop_epoch_held()!=epoch || ctx!=&gate_calls)return 0;
    gate_calls++;
    return 1;
}
static int fill_pages(void) {
    for(int i=0;i<128;i++){
        PyStrObject *p=(PyStrObject*)pcc_exp2_process_take(127,PY_TYPE_STR);
        C(p,12); p->byte_len=86;p->cp_len=86;p->hash=-1;
        p->data[0]='a'+(i/32);
        if(i%32==0)encoded[i/32]=((uintptr_t)p)^ENC;
    }
    return 0;
}
static void clear_roots(uintptr_t poison) {
    volatile uintptr_t clear[2048];
    for(int j=0;j<2048;j++)clear[j]=poison+(uintptr_t)j;
}
static void *carrier(void *slot){
    if(pcc_exp2_lexical_publish_slot(slot)!=0 || try_take(slot)!=0)return (void*)1;
    return NULL;
}
static int positive(void) {
    void *slot=decode(0);
    void *alias=slot;
    C(pcc_exp2_lexical_publish_slot(&slot)==0 && try_take(&slot)==0,20);
    pcc_gc_frame_enter_lifo_local(&borrowed,&slot);
    C(pcc_exp2_lexical_publish_slot(&slot)==0 && try_take(&slot)==0,21);
    pcc_gc_frame_leave_lifo_local(&slot);
    void *pair[2]={NULL,slot};
    pcc_gc_frame_enter_lifo_local(&two,pair);
    C(pcc_exp2_lexical_publish_slot(&pair[1])==0 && try_take(&pair[1])==0,22);
    C(pcc_exp2_lexical_publish_slot(pair)==0 && try_take(pair)==0,23);
    pcc_gc_frame_leave_lifo_local(pair);
    pcc_gc_frame_enter_lifo_local(&one,&slot);
    C(pcc_exp2_lexical_publish_slot(&alias)==0 && try_take(&alias)==0,24);
    C(pcc_exp2_lexical_publish_slot(((char*)&slot)+1)==0 && try_take(((char*)&slot)+1)==0,25);
    slot=(char*)alias+40;
    C(pcc_exp2_lexical_publish_slot(&slot)==0 && try_take(&slot)==0,26);
    slot=alias;
    C(pcc_exp2_lexical_publish_slot(&slot)==1 && M(12)==0,27);
    void *thread=NULL,*result=NULL;
    C(pcc_thread_start(&thread,carrier,&slot)==0 && pcc_thread_join(thread,&result)==0 && result==NULL,28);
    void *v=try_take(&slot);
    C(v==alias && slot==NULL && M(12)==0 && ((((PyObjectHeader*)v)->flags&PY_FLAG_GC_PINNED)==0),29);
    C(pcc_exp2_lexical_publish_slot(&slot)==0 && try_take(&slot)==0,30);
    return 0;
}
static int legacy_negatives(void) {
    void *outer=decode(2); void *inner=outer;
    pcc_gc_pin(outer);
    pcc_gc_frame_enter_lifo_local(&one,&inner);
    C(pcc_exp2_lexical_publish_slot(&inner)==0 && try_take(&inner)==0 && inner==outer,40);
    int64_t prior=pcc_gc_pin_slot_for_take(&inner,0);
    C(prior==PY_FLAG_GC_PINNED && M(12)==1,41);
    pcc_gc_frame_leave_lifo_local(&inner);
    C(pcc_gc_take_pinned_slot(&inner,prior)==outer && inner==NULL,42);
    C((((PyObjectHeader*)outer)->flags&PY_FLAG_GC_PINNED)!=0,43);
    pcc_gc_unpin(outer);
    C((((PyObjectHeader*)outer)->flags&PY_FLAG_GC_PINNED)==0,44);
    void *unknown=decode(3);
    C(pcc_exp2_lexical_publish_slot(&unknown)==0 && try_take(&unknown)==0,45);
    int64_t token=pcc_gc_foreign_lease_acquire(&unknown);
    C(token>=0 && pcc_gc_foreign_lease_release(&unknown,token)>=0 && M(12)==2,46);
    return 0;
}
static int real_cut(void) {
    void *live=decode(1);
    pcc_gc_frame_enter_lifo_local(&one,&live);
    C(pcc_exp2_lexical_publish_slot(&live)==1 && M(12)==2,51);
    /* This is exactly the borrowed payload the runtime ASCII path hands
       into py_bytes_new, whose allocation may stop the workers before copy. */
    C(pcc_exp2_lexical_leaf_slot(&live)==live,61);
    const char *interior=py_str_utf8(live);
    C(interior!=NULL && interior[0]=='b',62);
    C(pcc_stop_the_world()==0,52);
    int64_t epoch=pcc_thread_world_stop_epoch_held();
    int64_t recycled=pcc_exp2_process_minor_held(epoch);
    C(pcc_resume_world()==0,53);
    printf("cut recycled=%ld sticky=%ld refused=%ld promoted=%ld\n",recycled,M(12),M(11),M(6));
    C(recycled==1 && M(5)==1 && gate_calls==1 && M(12)==2,54);
    C(pcc_exp2_process_is_start(decode(0))==0 && pcc_exp2_process_is_start(live)==1,55);
    C(pcc_exp2_process_is_start(decode(2))==1 && pcc_exp2_process_is_start(decode(3))==1,56);
    C(py_str_byte_len(live)==86 && ((PyStrObject*)live)->data[0]=='b',57);
    C(pcc_exp2_lexical_leaf_slot(&live)==live && M(12)==2 && recycled==1,63);
    void *copied=py_bytes_new(interior,86);
    void* encoded_value=py_str_encode_with_encoding(live,NULL,NULL);
    C(copied && encoded_value && py_bytes_len(copied)==86 && py_bytes_len(encoded_value)==86,64);
    unsigned char*one=(unsigned char*)copied;unsigned char*other=(unsigned char*)encoded_value;
    for(int k=0;k<86;k++) C(one[24+k]==other[24+k] && one[24+k]==(unsigned char)interior[k],65);
    C(M(12)==2,66);
    C(try_take(&live)==decode(1) && live==NULL,58);
    puts("DEFAULT_ENCODE_CUT verified 86 exact bytes from interior across real stop and minor");
    return 0;
}
int main(void) {
    void *ordinary=py_float_from_f64(3.25);
    C(ordinary!=NULL,1);
    pcc_gc_frame_enter_lifo_local(&one,&ordinary);
    C(pcc_exp2_lexical_publish_slot(&ordinary)==0 && try_take(&ordinary)==NULL,2);
    pcc_gc_frame_leave_lifo_local(&ordinary);
    C(pcc_stop_the_world()==0,3);
    C(pcc_exp2_process_enable(1,4)==1 && pcc_exp2_process_register_gate((void*)gate,&gate_calls)==1,4);
    C(pcc_exp2_install_exposure_hook((void*)pcc_exp2_process_expose)==1,5);
    C(pcc_resume_world()==0,6);
    C(fill_pages()==0,7); C(positive()==0,8); C(legacy_negatives()==0,9);
    clear_roots(4000);
    C(real_cut()==0,10);
    puts("PASS dead lexical page returns; live, outer-pin and opaque lease pages stay");
    return 0;
}
