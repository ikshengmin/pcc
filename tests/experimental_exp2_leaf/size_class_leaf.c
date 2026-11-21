#include "py_internal.h"
#include <stdint.h>
#include <stdlib.h>
#include <stdio.h>
extern int64_t pcc_exp2_leaf_init(void*,void*,void*,int64_t,void*,int64_t);
#ifndef EXP2_OLD_CLASSES
extern int64_t pcc_exp2_leaf_init_sized(void*,void*,void*,int64_t,void*,int64_t,int64_t);
#endif
extern int64_t pcc_exp2_leaf_bind(void*);
extern int64_t pcc_exp2_leaf_unbind(void*);
extern int64_t pcc_exp2_leaf_is_start(void*,void*);
extern int64_t pcc_exp2_leaf_minor(void*,void*,int64_t);
extern void *pcc_gc_object_index_find_stable(void*);
static const char msg[]="abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789abcdefghijklmnopqrstuvwx"; /* 86 */
int main(void) {
    int64_t sf[18]={0},ss[18]={0},mf[16]={0},ms[16]={0},r1[2]={0},r2[2]={0};
    char *af=calloc(8,4096),*as=calloc(8,4096); int backend=pcc_gc_backend();
    if(!af||!as||sizeof(msg)!=87) return 59;
#ifdef EXP2_OLD_CLASSES
    if(!pcc_exp2_leaf_init(sf,af,mf,8,r1,2)||!pcc_exp2_leaf_init(ss,as,ms,8,r2,2))return 60;
#else
    if(!pcc_exp2_leaf_init_sized(sf,af,mf,8,r1,2,32)||!pcc_exp2_leaf_init_sized(ss,as,ms,8,r2,2,128))return 60;
#endif
    if (pcc_exp2_leaf_bind(sf)!=(backend==3))return 61;
    PyObject *v=py_float_from_f64(3.25);
    if (!v || py_float_to_f64(v)!=3.25)return 62;
    if (pcc_exp2_leaf_is_start(sf,v)!=(backend==3))return 63;
    PyObject *wrong=py_str_new("abc",3);
    if (pcc_exp2_leaf_is_start(sf,wrong))return 64;
    if (backend==3 && (!pcc_exp2_leaf_unbind(sf) || pcc_gc_object_index_find_stable(v))) return 65;
    if (pcc_exp2_leaf_bind(ss)!=(backend==3))return 66;
    PyObject *str=py_str_new(msg,86);
    if (!str||py_str_len(str)!=86||((PyStrObject*)str)->data[85]!='x')return 67;
    if (pcc_exp2_leaf_is_start(ss,str)!=(backend==3))return 68;
    PyObject *too_big=py_str_new(0,95);
    if (!too_big || pcc_exp2_leaf_is_start(ss,too_big))return 69;
    if (backend==3 && (pcc_exp2_leaf_minor(ss,&str,1)!=0 || ss[11]!=1 || sf[14]!=24 || ss[14]!=127 || pcc_gc_object_index_find_stable(str)))return 70;
    printf("gc=%d float=3.25 str=86 requested=%lld,%lld\n",backend,(long long)sf[14],(long long)ss[14]);return 0;
}
