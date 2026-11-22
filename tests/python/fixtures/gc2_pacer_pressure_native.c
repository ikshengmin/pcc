/* Exercise the review policy and a real assist inside a controlled stop. */
#include "py_runtime.h"
#include <stdint.h>
#include <stdio.h>

extern int64_t pcc_gc2_pacer_mode(void);
extern int64_t pcc_gc2_pacer_goal(int64_t, int64_t);
extern int64_t pcc_gc2_pacer_trigger(int64_t, int64_t);
extern int64_t pcc_gc2_pacer_budget(int64_t, int64_t, int64_t, int64_t);
extern int64_t pcc_gc2_pacer_due(void);
extern int64_t pcc_gc_tracing_step_cycle(int64_t);
extern void pcc_gc_cms_publish_alloc(int64_t, int64_t);
extern void pcc_gc_cms_note_alloc_flush(void);
extern int64_t pcc_gc_debt_bytes;
extern int64_t pcc_gc_live_bytes;
extern int64_t pcc_gc_tracing_marked_bytes;
extern int32_t pcc_gc_mark_active;
extern int32_t pcc_gc_cms_mutator_assists;

enum { MIB = 1024 * 1024, BATCH = 4096 };

static int check_math(void) {
    if (pcc_gc2_pacer_goal(0, 100) != 4 * MIB) return 1;
    if (pcc_gc2_pacer_goal(64 * MIB, 50) != 96 * MIB) return 2;
    if (pcc_gc2_pacer_goal(INT64_MAX - 99, 10000) != INT64_MAX) return 3;
    if (pcc_gc2_pacer_trigger(64 * MIB, 128 * MIB) != 112 * MIB) return 4;
    if (pcc_gc2_pacer_budget(128 * MIB, 112 * MIB, 112 * MIB - 1, BATCH) != 0)
        return 5;
    if (pcc_gc2_pacer_budget(128 * MIB, 112 * MIB, 120 * MIB, BATCH) != 96)
        return 6;
    if (pcc_gc2_pacer_budget(128 * MIB, 112 * MIB, 128 * MIB, 1) != 2)
        return 7;
    if (pcc_gc2_pacer_budget(INT64_MAX, 0, INT64_MAX - 1, 32000) != 512)
        return 8;
    return 0;
}

static int check_pressure(void) {
    py_gc_disable();
    PyObject *root = NULL;
    void *handle = pcc_gc_scheduler_root_register_handle(&root);
    PyObject *owner = py_list_new(0);
    if (!handle || !owner) return 10;
    pcc_gc_store_root(&root, owner);
    for (int i = 0; i < 512; i++) {
        PyObject *value = py_list_new(0);
        if (!value) return 11;
        py_list_append(owner, value);
    }
    pcc_gc_cms_note_alloc_flush();
    if (pcc_stop_the_world() != 0) return 12;
    pcc_gc_tracing_marked_bytes = 0;
    pcc_gc_live_bytes = 3 * MIB - 1;
    pcc_gc_debt_bytes = 0;
    if (pcc_gc2_pacer_due() != 0) return 13;
    pcc_gc_live_bytes = 3 * MIB;
    if (pcc_gc2_pacer_due() != 1) return 14;
    pcc_gc_tracing_step_cycle(1);
    if (!pcc_gc_mark_active) return 15;
    int32_t assists = __atomic_load_n(&pcc_gc_cms_mutator_assists, __ATOMIC_ACQUIRE);
    pcc_gc_debt_bytes = 0;
    pcc_gc_cms_publish_alloc(BATCH, 1);
    if (pcc_gc_cms_mutator_assists != assists || pcc_gc_debt_bytes != BATCH)
        return 16;
    py_gc_enable();
    pcc_gc_live_bytes = 4 * MIB;
    pcc_gc_cms_publish_alloc(BATCH, 1);
    if (pcc_gc_cms_mutator_assists != assists + 1) return 17;
    if (pcc_gc_debt_bytes >= 2 * BATCH || pcc_gc_live_bytes != 4 * MIB ||
        pcc_gc2_pacer_due() != 1)
        return 18;
    py_gc_disable();
    if (pcc_resume_world() != 0 || root != owner) return 19;
    pcc_gc_store_root(&root, NULL);
    pcc_gc_scheduler_root_unregister_handle(handle);
    puts("GC2_PACER_PRESSURE_OK");
    return 0;
}

int main(int argc, char **argv) {
    if (argc != 2) return 20;
    int rc = check_math();
    if (rc) return rc;
    (void)pcc_gc_backend();
    if (argv[1][0] == 'o' && argv[1][1] == 'n' && !argv[1][2]) {
        if (pcc_gc2_pacer_mode() != 100) return 21;
        return check_pressure();
    }
    if (pcc_gc2_pacer_mode() != -1 || pcc_gc2_pacer_due() != -1) return 22;
    puts("GC2_PACER_DISABLED_OK");
    return 0;
}
