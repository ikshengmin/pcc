#include <stdint.h>
extern int64_t pcc_gc_tracing_finish_commits;
extern int64_t pcc_gc_tracing_candidate_no_pending_gen_commit;
extern int32_t pcc_class_del_defined_count;
extern void *py_weakref_head;
extern void *py_weakref_late_head;
extern int64_t pcc_gc_backend(void);
int64_t native_generator_counter(int64_t k) {
    switch (k) {
        case 0: return pcc_gc_backend();
        case 1: return pcc_gc_tracing_finish_commits;
        case 2: return pcc_gc_tracing_candidate_no_pending_gen_commit;
        case 3: return pcc_class_del_defined_count;
        case 4: return py_weakref_head != 0 || py_weakref_late_head != 0;
        default: return -1;
    }
}
