#include "py_internal.h"
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
extern int64_t pcc_exp2_leaf_init(void*,void*,void*,int64_t,void*,int64_t);
extern int64_t pcc_exp2_leaf_bind(void*);
extern int64_t pcc_exp2_leaf_is_start(void*,void*);
extern int64_t pcc_exp2_leaf_minor(void*,void*,int64_t);
int main(void) {
    int64_t state[18],meta[2],rem[2]; char *data=calloc(1,4096);
    if(!data || !pcc_exp2_leaf_init(state,data,meta,1,rem,2) ||
       !pcc_exp2_leaf_bind(state)) return 40;
    PyObject* written=py_str_new("secret!",7);
    if(!written || !pcc_exp2_leaf_is_start(state,written)) return 41;
    if(pcc_exp2_leaf_minor(state,0,0)!=1) return 42;
    PyObject* zero=py_str_new(0,7);
    if (!zero || zero!=written || !pcc_exp2_leaf_is_start(state,zero))return 43;
    for(int i=0;i<8;i++) if(((PyStrObject*)zero)->data[i]!=0)return 44;
    printf("recycled_str_bytes_zero=8\n");return 0;
}
