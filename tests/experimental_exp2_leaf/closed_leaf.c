#include "py_internal.h"
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <stdlib.h>
extern int64_t pcc_exp2_leaf_init(void*,void*,void*,int64_t,void*,int64_t);
extern int64_t pcc_exp2_leaf_is_start(void*,void*);
extern int64_t pcc_exp2_leaf_remember(void*,void*);
extern int64_t pcc_exp2_leaf_minor(void*,void*,int64_t);
extern int64_t pcc_exp2_leaf_bind(void*);
extern int64_t pcc_exp2_leaf_unbind(void*);
extern int64_t pcc_gc_pointer_is_managed(void*);
extern void* pcc_gc_object_index_find_stable(void*);
extern int64_t pcc_gc_backend(void);
extern int64_t pcc_platform_monotonic_ns(void);
#define NPAGE 32
#define NCELL 64
int main(int argc, char **argv) {
    int backend = pcc_gc_backend();
    int rounds = 3;
    if (argc > 1) rounds=atoi(argv[1]);
    if (rounds < 1 || rounds > 100000) return 41;
    int64_t state[18] = {0};
    int64_t pages[NPAGE * 2] = {0};
    PyObject **rem[8] = {0};
    PyObject *roots[2] = {0};
    PyObject *old_slot=NULL;
    char *data = calloc(NPAGE,4096);
    PyObject *a=py_str_new("Ada:",4), *b=py_str_new("pro",3);
    if (!a||!b||!data||!pcc_exp2_leaf_init(state,data,pages,NPAGE,rem,8)) return 42;
    int active=pcc_exp2_leaf_bind(state);
    if (active!=(backend==3)) return 43;
    uint64_t sum=0, minor_ns=0, max_minor_ns=0, tick,elapsed;
    int64_t start=pcc_platform_monotonic_ns();
    for (int r=0;r<rounds;r++) {
        for (int i=0;i<(NPAGE-2)*NCELL;i++) {
            // These are the ordinary runtime entry points, not a synthetic
            // string layout; this closed fixture exports no borrowed address.
            PyObject *v=py_str_concat(a,b);
            if (!v) return 44;
            sum+=py_str_len(v);
            if (backend==3 && r==0 && i==0) {
                if (!pcc_exp2_leaf_is_start(state,v) || !pcc_gc_pointer_is_managed(v)
                    || pcc_gc_object_index_find_stable(v)) return 45;
                roots[0]=v;
            }
            if (backend==3 && r==0 && i==NCELL) {
                old_slot=v;
                if (!pcc_exp2_leaf_remember(state,&old_slot)) return 46;
            }
            if (backend==0) py_decref(v);
        }
        if (backend==3) {
            tick=pcc_platform_monotonic_ns();
            int64_t freed=pcc_exp2_leaf_minor(state,roots,1);
            elapsed=pcc_platform_monotonic_ns()-tick;
            minor_ns+=elapsed;
            if(elapsed>max_minor_ns)max_minor_ns=elapsed;
            if (freed!=(r==0 ? NPAGE-4:NPAGE-2)) return 47;
            if (py_str_len(roots[0])!=7 || py_str_len(old_slot)!=7) return 48;
            if (((PyStrObject*)roots[0])->data[4]!='p' || ((PyStrObject*)old_slot)->data[6]!='o')return 49;
        }
    }
    int64_t finished=pcc_platform_monotonic_ns();
    if (active && !pcc_exp2_leaf_unbind(state)) return 51;
    if (sum!=(uint64_t)rounds*(NPAGE-2)*NCELL*7) return 52;
    printf("%d %lld %lld %lld %lld %lld %lld %lld %lld %lld %lld\n",backend,(long long)sum,(long long)state[8],(long long)state[9],(long long)state[11],(long long)state[12],(long long)(finished-start),(long long)minor_ns,(long long)max_minor_ns,(long long)state[14],(long long)state[15]);
    free(data);
    return 0;
}
