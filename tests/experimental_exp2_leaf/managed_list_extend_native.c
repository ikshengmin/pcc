/* One private managed old-list regression. The normal native old-cut is the
 * only reference visitor, and the ordinary list operation is the only source
 * of the measured kind-2 write. No C-provided origin or visitor is installed. */
#include "py_internal.h"
#include <stdint.h>
#include <stdio.h>
extern int64_t pcc_exp2_window_control(int64_t);
extern int64_t pcc_exp2_window_metric(int64_t);
extern int64_t pcc_exp2_window_stop_minor(int64_t);
extern int64_t pcc_exp2_process_is_start(void *);
extern int64_t pcc_thread_world_stop_epoch_held(void);
extern int64_t pcc_thread_is_running(void);
extern void *pcc_gc_object_index_find_stable(void *);
#define M(k) pcc_exp2_window_metric(k)
#define LIVE 3
#define ADMITTED (4*128)

static int failed(int n) {
    printf("FAIL %d state=%ld epoch=%ld attempts=%ld refused=%ld tag=%ld reason=%ld kind2=%ld "
           "journal=%ld ref=%ld extras=%ld misses=%ld floatSticky=%ld recycled=%ld\n",
           n, (long)M(30), (long)M(18), (long)M(33), (long)M(34),
           (long)M(32), (long)M(31), (long)M(62), (long)M(50), (long)M(51),
           (long)M(52), (long)M(53), (long)M(73), (long)M(5));
    return n;
}
#define C(condition,n) do { if (!(condition)) return failed(n); } while (0)

static void scrub(void) {
    volatile uintptr_t words[1024];
    for (int i=0; i<1024; ++i) words[i]=(uintptr_t)(i+710103);
}

static int populate(PyObject *dest) {
    PyObject *src=py_list_new(4);
    C(src && src!=dest && pcc_gc_object_index_find_stable(src), 20);
    for (int k=0; k<LIVE; ++k) {
        PyObject *v=py_float_from_f64(9.25+(double)k);
        C(v && pcc_exp2_process_is_start(v) && !pcc_gc_object_index_find_stable(v), 21);
        py_list_append(src,v);
    }
    C(py_list_len(src)==LIVE && py_list_len(dest)==0 && M(2)==0, 22);
    int64_t before=M(62);
    py_list_extend(dest,src);
    C(py_list_len(dest)==LIVE && py_list_len(src)==LIVE, 23);
    C(M(62)==before+LIVE, 24);

    /* Do not read or pass a child to main before the cut. Empty the actual
       source store even though the full-old visitor sees all indexed owners,
       not just reachable ones. Dropping the local alone is not sufficient. */
    py_list_clear(src);
    C(py_list_len(src)==0 && M(62)==before+LIVE, 25);
    src=NULL;

    /* Three complete pages of independent leaf allocations, plus the unused
       cells of the first FLOAT page. Never publish any of these dead values. */
    for (int k=LIVE; k<ADMITTED; ++k) {
        PyObject *dead=py_float_from_f64(-99.75);
        C(dead && pcc_exp2_process_is_start(dead) && !pcc_gc_object_index_find_stable(dead), 26);
    }
    return 0;
}

int main(int argc, char **argv) {
    py_set_program_args(argc,(const char **)argv);
    C(pcc_gc_backend()==3, 1);
    py_gc_disable();
    C(pcc_current_thread_id()>0 && pcc_thread_is_running()==1, 2);
    PyObject *dest=py_list_new(4);
    C(dest && pcc_gc_object_index_find_stable(dest), 3);
    if(pcc_exp2_window_control(4)!=1) {
        C(pcc_exp2_window_control(1)<0 && M(39)==0 && M(0)==0 && M(2)==0 && M(5)==0, 4);
        puts("UNQUALIFIED IMAGE OR INPUT: no admission and no recycling");
        return 16;
    }
    C(pcc_exp2_window_control(1)==1 && pcc_exp2_window_control(2)==1, 5);
    C(populate(dest)==0, 6);
    scrub();
    C(M(0)==ADMITTED && M(14)==ADMITTED && M(16)==0 &&
      M(12)==0 && M(73)==0 && M(50)==0 && M(51)==0 && M(33)==0, 7);

    C(pcc_stop_the_world()==0, 8);
    int64_t epoch=pcc_thread_world_stop_epoch_held();
    int64_t recycled=pcc_exp2_window_stop_minor(epoch);
    int64_t still_held=pcc_thread_world_stop_epoch_held();
    C(pcc_resume_world()==0, 9);

    C(epoch>0 && still_held==epoch && M(18)==epoch &&
      M(2)==1 && M(33)==1 && M(34)==0, 10);
    C(M(62)==LIVE && M(50)==1 && M(51)==1 && M(52)==0 && M(53)==0 &&
      M(35)>=2 && M(36)>=LIVE, 11);
    C(recycled>0 && M(5)==recycled && M(6)>=1 && M(6)+recycled==4 && M(12)==0 && M(73)==0, 12);
    C(pcc_exp2_window_control(3)==1 && py_list_len(dest)==LIVE, 13);
    for (int k=0; k<LIVE; ++k) {
        PyObject *v=py_list_get(dest,k);
        C(v && pcc_exp2_process_is_start(v) && py_float_to_f64(v)==9.25+(double)k, 14);
    }
    printf("managed list.extend: epoch=%ld admittedFloat=%ld kind2Delta=%ld fullOldPages=%ld "
           "journalPages=%ld stale=%ld missed=%ld recycled=%ld promoted=%ld "
           "floatSticky=%ld payloads=%d\n",
           (long)epoch, (long)M(14), (long)M(62), (long)M(51), (long)M(50),
           (long)M(52), (long)M(53), (long)recycled, (long)M(6), (long)M(73), LIVE);
    return 0;
}
