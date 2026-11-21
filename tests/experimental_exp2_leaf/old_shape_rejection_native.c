/* Use the actual collector-registered class, constructor and allocator.
   This probes layout in a held epoch; it does not arm/reclaim nursery pages. */
#include "py_internal.h"
#include <stdint.h>
#include <stdio.h>
#define CHECK(x,n) do { if (!(x)) { printf("FAIL %d\n",n);return n; } } while (0)
extern int64_t pcc_exp2_old_verify_shape(void*,int64_t,int64_t,int64_t);
#ifdef M9_NEW_DETAIL
extern int64_t pcc_exp2_old_verify_shape_detail(void*,int64_t,int64_t,int64_t);
#define DETAIL pcc_exp2_old_verify_shape_detail
#else
/* Identical fixture against the original public conflated verdict. */
#define DETAIL pcc_exp2_old_verify_shape
#endif
extern int64_t pcc_thread_world_stop_epoch_held(void);
extern void *pcc_gc_object_index_find_stable(void*);
int main(void){
    CHECK(pcc_gc_set_backend(3)==0,1);
    py_gc_disable();
    const char *names[2]={"field_a","field_b"};
    PyClassObject *c=py_class_new("ShapeWitness",0,0,names,2);
    CHECK(c!=0 && pcc_gc_object_index_find_stable(c)!=0,2);
    const int64_t size=c->instance_size;
    const int64_t tag=c->type_tag_alloc;
    CHECK(size==48&&tag>=104,3);
    PyInstanceObject *good=(PyInstanceObject*)py_instance_new(c);
    CHECK(good!=0&&good->cls==c&&pcc_gc_object_index_find_stable(good)!=0,4);
    /* Same ordinary allocator/tag/size as py_instance_new, just before its
       first store to the initially zero-filled class slot. */
    PyInstanceObject *pending=(PyInstanceObject*)pcc_gc_alloc(size,tag,0);
    CHECK(pending!=0&&pending->cls==0&&pcc_gc_object_index_find_stable(pending)!=0,5);
    CHECK(pcc_stop_the_world()==0 && pcc_thread_world_stop_epoch_held()>0,6);
    int64_t valid=pcc_exp2_old_verify_shape(good,tag,size,good->h.flags);
    int64_t in_progress=pcc_exp2_old_verify_shape(pending,tag,size,pending->h.flags);
    int64_t d_good=DETAIL(good,tag,size,good->h.flags);
    int64_t d_pending=DETAIL(pending,tag,size,pending->h.flags);
    int64_t d_header=DETAIL(good,tag,8,524288);
    int64_t d_flags=DETAIL(good,tag,size,524288);
    int64_t d_class_fit=DETAIL(good,tag,size-8,good->h.flags);
    CHECK(pcc_resume_world()==0,7);
    printf("valid=%lld progress_public=%lld valid_detail=%lld progress_detail=%lld type=%lld bytes=%lld flags=%d\n",
      (long long)valid,(long long)in_progress,(long long)d_good,(long long)d_pending,
      (long long)tag,(long long)size,pending->h.flags);
    printf("general_size_detail=%lld flags_detail=%lld class_fit_detail=%lld\n",
      (long long)d_header,(long long)d_flags,(long long)d_class_fit);
    CHECK(valid==1 && in_progress==-7 && d_good==1,8);
    CHECK(d_pending==-1021 && d_header==-1001 && d_flags==-1002 && d_class_fit==-1030,9);
    /* Finish the same genuine object; no malformed references injected. */
    pending->cls=c;
    py_incref((PyObject*)c);
    py_gc_track((PyObject*)pending);
    CHECK(pcc_stop_the_world()==0 && pcc_thread_world_stop_epoch_held()>0,10);
    int64_t done=DETAIL(pending,tag,size,pending->h.flags);
    CHECK(pcc_resume_world()==0,11);
    CHECK(done==1,12);
    printf("same_instance_after_class_store=%lld\n",(long long)done);
    return 0;
}
