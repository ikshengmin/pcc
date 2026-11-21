/* Same generated converter, real M6 pool/exposure and checked lexical TU.
 * The witness checks exact per-boundary exposures and values, not speed or
 * whole-program coverage. The non-compiler C setup never runs a minor. */
#include "py_internal.h"
#include <stdint.h>
#include <stdio.h>
#include <string.h>
extern int64_t pcc_gc_backend(void);
extern int64_t pcc_exp2_install_exposure_hook(void *);
extern int64_t pcc_exp2_process_enable(int64_t,int64_t);
extern int64_t pcc_exp2_process_metric(int64_t);
extern void *pcc_exp2_process_take(int64_t,int64_t);
extern int64_t pcc_exp2_process_is_start(void *);
extern int64_t pcc_exp2_process_expose(void *,int64_t);
extern double user_float_project_project(PyObject *);
extern double user_float_project_literal(void);
extern double user_float_project_typed(double);

static PyObject *watched;
static int64_t leases, pins;
static int64_t exposure(void *value, int64_t reason) {
    if (value==watched) {
        if (reason==1) pins++;
        if (reason==2) leases++;
    }
    return pcc_exp2_process_expose(value,reason);
}
#define CHECK(p,id) do { if (!(p)) { printf("FAIL %d\n",id); return id; } } while (0)

int main(int argc, char **argv) {
    CHECK(argc==3,10);
    int mode=argv[1][0]-'0';
    int fast=argv[2][0]=='y';
    CHECK(pcc_gc_set_backend(mode)==0 && pcc_gc_backend()==mode,11);
    py_gc_disable();
    PyObject *old=py_float_from_f64(-3.125), *ordinary_str=py_str_new("  5.375  ",9);
    CHECK(old && ordinary_str,12);
    PyObject *f=old,*s=ordinary_str;
    if (mode==3) {
        CHECK(pcc_stop_the_world()==0,13);
        CHECK(pcc_exp2_process_enable(1,4)==1 && pcc_exp2_install_exposure_hook((void*)exposure)==1,14);
        CHECK(pcc_resume_world()==0,15);
        f=(PyObject*)pcc_exp2_process_take(24,PY_TYPE_FLOAT);
        PyStrObject *young_str=(PyStrObject*)pcc_exp2_process_take(50,PY_TYPE_STR);
        CHECK(f && young_str && pcc_exp2_process_is_start(f)==1 && pcc_exp2_process_is_start(young_str)==1,16);
        *(double *)((char *)f+16)=-3.125;
        young_str->byte_len=9; young_str->cp_len=9;young_str->hash=-1;
        memcpy(young_str->data,"  5.375  ",10);
        s=(PyObject*)young_str;
    }
    watched=f;
    CHECK(user_float_project_project(f)==-3.125 && py_err_occurred()==0,17);
    printf("mode=%d exact_leases=%ld exact_pins=%ld exact_sticky=%ld\n",mode,(long)leases,(long)pins,(long)pcc_exp2_process_metric(12));
    CHECK(pins==0 && leases==(mode==3 && !fast ? 1 : 0),18);
    if (mode == 3) CHECK(pcc_exp2_process_metric(12)==(fast ? 0 : 1),19);
    /* Old exact: same float bits but not an occupied nursery start. */
    watched=old;leases=pins=0;
    CHECK(user_float_project_project(old)==-3.125 && py_err_occurred()==0,20);
    CHECK(leases==(mode==3 ? 1 : 0) && pins==0,21);
    /* Real positive slot classification with a *nonfloat* eligible str.
     * Only the exact type check takes this through the old parse+lease. */
    watched=s;leases=pins=0;
    CHECK(user_float_project_project(s)==5.375 && py_err_occurred()==0,22);
    CHECK(leases==(mode==3 ? 1 : 0) && pins==0,23);
    if (mode == 3) {
        CHECK(pcc_exp2_process_metric(12)==(fast ? 1 : 2),24);
        watched=f;leases=pins=0;
        pcc_gc_pin(f);
        CHECK(user_float_project_project(f)==-3.125 && leases==1 && pins==1,25);
        CHECK((((PyObjectHeader*)f)->flags&PY_FLAG_GC_PINNED)!=0,26);
        pcc_gc_unpin(f);
    }
    CHECK(user_float_project_literal()==3.5 && user_float_project_typed(9.5)==9.5 && py_err_occurred()==0,27);
    watched=0;
    puts("EXACT_PROJECTION_REAL_NURSERY_AND_GENERIC_MISS_PARITY");
    return 0;
}
