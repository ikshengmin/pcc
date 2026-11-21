/* Independent carrier receives a real PyStrObject built on the first carrier.
 * Data, directory, metadata and table remain valid to process exit. */
#include "py_internal.h"
#include <stdint.h>
#include <pthread.h>
#include <stdio.h>
#include <stdlib.h>
extern int64_t pcc_exp2_leaf_init(void*,void*,void*,int64_t,void*,int64_t);
extern int64_t pcc_exp2_leaf_bind(void*);
extern int64_t pcc_exp2_leaf_unbind(void*);
extern int64_t pcc_exp2_leaf_is_start(void*,void*);
extern int64_t pcc_gc_pointer_is_managed(void*);
extern void *pcc_gc_object_index_find_stable(void*);
#ifndef EXP2_PRE_SHARE
extern int64_t pcc_exp2_leaf_install(void*);
#endif
static int64_t state[18], meta[16], rem[8], table[2], overlaps[3];
static void *second_writer(void *arg) {
    if (pcc_exp2_leaf_bind(state)) { pcc_exp2_leaf_unbind(state); return (void*)65; }
    return 0;
}
static void *consumer(void *arg) {
    PyObject *v=(PyObject *)arg;
    if (!pcc_exp2_leaf_is_start(state,v)) return (void*)60;
    if (!pcc_gc_pointer_is_managed(v)) return (void*)61;
    if (pcc_gc_pointer_is_managed(((char*)v)+8)) return (void*)62;
    if (pcc_gc_object_index_find_stable(v)!=0) return (void*)63;
    if (py_str_len(v)!=7 || ((PyStrObject*)v)->data[4]!='p') return (void*)64;
    return 0;
}
int main(void) {
    char *data=calloc(8,4096);
    PyObject *a=py_str_new("Ada:",4),*b=py_str_new("pro",3);
    if (!a || !b || !data || !pcc_exp2_leaf_init(state,data,meta,8,rem,8)) return 51;
    table[0]=1; table[1]=(int64_t)state;
#ifndef EXP2_PRE_SHARE
    overlaps[0]=2; overlaps[1]=(int64_t)state; overlaps[2]=(int64_t)state;
    if(pcc_exp2_leaf_install(overlaps)) return 58;
    if(!pcc_exp2_leaf_install(table) || pcc_exp2_leaf_install(table)) return 52;
#endif
    if (!pcc_exp2_leaf_bind(state)) return 53;
    PyObject *v=py_str_concat(a,b);
    if (!pcc_exp2_leaf_is_start(state,v)) return 54;
    pthread_t thread;
    void *result=0;
    if (pthread_create(&thread,0,second_writer,0)) return 56;
    if (pthread_join(thread,&result)) return 57;
    if(result) return (int)(int64_t)result;
    if (!pcc_exp2_leaf_unbind(state)) return 55;
    if (pthread_create(&thread,0,consumer,v)) return 56;
    if (pthread_join(thread,&result)) return 57;
    if(result) return (int)(int64_t)result;
    printf("foreign_owner_str=7 intrusive_record=0 producer_unbound=1\n");
    return 0;
}
