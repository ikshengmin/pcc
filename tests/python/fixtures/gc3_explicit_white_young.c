#include "py_runtime.h"
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
extern void pcc_gc_generational_promote_young_if_known(PyObject *);
extern int64_t pcc_gc_generational_step(int64_t, int64_t);
extern void *pcc_py_gc_minor_current_get(void);
extern void *pcc_gc_backend3_young_list_head(void);
extern void *pcc_gc_object_node_young_next(void *);
extern int64_t pcc_gc_object_node_is_active(void *);
extern int64_t pcc_gc_backend4_old_object_count(void);
extern int64_t pcc_gc_backend4_young_object_count(void);
extern void *pcc_gc_object_list_head(void);
extern void *pcc_gc_object_node_next(void *);
extern void *pcc_gc_identity_ensure(PyObject *);
extern void *pcc_gc_identity_find(PyObject *);
static int64_t identity(PyObject *obj) {
    pcc_py_gc_minor_graph_lock();
    void *record = pcc_gc_identity_ensure(obj);
    int64_t result = record == NULL ? 0 : ((int64_t *)record)[1];
    pcc_py_gc_minor_graph_unlock();
    return result;
}
static int64_t retained_identities(uint64_t *ids) {
    int64_t count = 0, pins = 0, old = 0, pending = 0;
    pcc_py_gc_minor_graph_lock();
    void *node = pcc_gc_object_list_head();
    while (node != NULL) {
        if (pcc_gc_object_node_is_active(node)) {
            PyObject *obj = *((PyObject **)node);
            void *record = pcc_gc_identity_find(obj);
            if (record != NULL) {
                int64_t id = ((int64_t *)record)[1];
                for (int i = 16; i < 64; i++) if (id == ids[i]) {
                    count++;
                    if ((((int32_t *)obj)[3] & 64) || (((int64_t *)node)[7] & 1) || ((int64_t *)node)[10]) pins++;
                    if (((int32_t *)obj)[3] & 256) old++;
                    if (((void **)node)[8] != NULL || ((void **)node)[9] != NULL) pending++;
                }
            }
        }
        node = pcc_gc_object_node_next(node);
    }
    pcc_py_gc_minor_graph_unlock();
    printf("IDENTITIES retained=%lld pinned=%lld old=%lld linked=%lld\n",(long long)count,(long long)pins,(long long)old,(long long)pending);
    return count;
}
static int64_t canonical_young_payloads(void) {
    int64_t count = 0;
    pcc_py_gc_minor_graph_lock();
    void *node = pcc_gc_object_list_head();
    while (node != NULL) {
        if (pcc_gc_object_node_is_active(node)) {
            PyObject *obj = *((PyObject **)node);
            if (((int32_t *)obj)[2] == PY_TYPE_STR && py_str_len(obj) == 5) count++;
        }
        node = pcc_gc_object_node_next(node);
    }
    pcc_py_gc_minor_graph_unlock();
    return count;
}
static int64_t young_nodes(void) {
    int64_t count = 0;
    pcc_py_gc_minor_graph_lock();
    void *node = pcc_gc_backend3_young_list_head();
    while (node != NULL) { count++; node = pcc_gc_object_node_young_next(node); }
    pcc_py_gc_minor_graph_unlock();
    return count;
}
static int produce(PyObject **roots, int old, uint64_t *hidden) {
    pcc_thread_no_park_enter();
    for (int i = 0; i < 64; i++) {
        PyObject *value = py_str_new("young", 5);
        if (i < 16) roots[old+i] = value;
        hidden[i] = identity(value); /* A small stable integer, never an encoded pointer. */
        if (hidden[i] == 0) return 1;
        for (int j = 0; j < i; j++) if (hidden[j] == hidden[i]) return 2;
    }
    pcc_thread_no_park_exit();
    return 0;
}
extern int32_t pcc_gc_explicit_collect_active, pcc_gc_mark_active, pcc_gc_cycle_requested;
extern int32_t pcc_gc_cms_finish_pending, pcc_gc_backend4_auto_step_pending;
extern int32_t pcc_gc_forwarding_population;
extern int32_t pcc_gc_backend4_remap_active, pcc_gc_concurrent_sweep_state, py_gc_collecting;
extern int64_t pcc_gc_sweep_owed(void);
extern int64_t pcc_gc_metric_step, pcc_gc_tracing_finish_commits;
extern int32_t pcc_gc_metric_safepoint;
extern int64_t pcc_py_gc_minor_graph_lock_depth(void);
static int64_t automatic_mark1(void) {
    if (pcc_stop_the_world() != 0) return -20;
    if (pcc_gc_explicit_collect_active || pcc_gc_mark_active ||
        pcc_gc_cms_finish_pending || pcc_gc_backend4_remap_active || pcc_gc_concurrent_sweep_state ||
        py_gc_collecting || pcc_gc_sweep_owed() || !pcc_thread_owns_stopped_world() ||
        pcc_thread_no_park_depth() || pcc_py_gc_minor_graph_lock_depth()) {
        printf("AUTO_NOT_QUIESCENT explicit=%d active=%d requested=%d finish=%d\n",
               pcc_gc_explicit_collect_active,pcc_gc_mark_active,pcc_gc_cycle_requested,pcc_gc_cms_finish_pending);
        pcc_resume_world();
        return -21;
    }
    int requested_before=pcc_gc_cycle_requested;
    int64_t before_deaths=pcc_gc_telemetry(140), before_steps=pcc_gc_metric_step;
    int64_t before_gates=pcc_gc_metric_safepoint, before_commits=pcc_gc_tracing_finish_commits;
    int before_forwards=pcc_gc_forwarding_population;
    py_gc_set_threshold(700,10,10);
    py_gc_enable();
    __atomic_store_n(&pcc_gc_backend4_auto_step_pending,1,__ATOMIC_RELEASE);
    pcc_gc_safepoint();
    py_gc_disable();
    int64_t result=pcc_gc_telemetry(140)-before_deaths;
    printf("AUTO_MARK1 injected_pending=1 requested_before=%d steps=%lld gates=%lld commits=%lld deaths=%lld before_forwards=%d after_forwards=%d active=%d requested=%d explicit=%d pending=%d\n",
           requested_before,(long long)(pcc_gc_metric_step-before_steps),(long long)(pcc_gc_metric_safepoint-before_gates),
           (long long)(pcc_gc_tracing_finish_commits-before_commits),(long long)result,
           before_forwards,pcc_gc_forwarding_population,pcc_gc_mark_active,pcc_gc_cycle_requested,
           pcc_gc_explicit_collect_active,pcc_gc_backend4_auto_step_pending);
    int good=pcc_gc_metric_step>before_steps && pcc_gc_metric_safepoint>before_gates &&
             pcc_gc_tracing_finish_commits==before_commits+1 && !pcc_gc_explicit_collect_active &&
             !pcc_gc_backend4_auto_step_pending && !pcc_gc_mark_active && !pcc_gc_cycle_requested;
    pcc_resume_world();
    return good ? result : -22;
}

static int check_live(PyObject **roots, uint64_t *ids) {
    for (int i=0;i<65;i++) {
        int64_t lease=pcc_gc_foreign_lease_acquire(&roots[i]);
        if (lease<0 || py_str_len(roots[i])!=5 || identity(roots[i])!=(int64_t)ids[i]) return 0;
        pcc_gc_foreign_lease_release(&roots[i],lease);
    }
    return canonical_young_payloads()==65;
}
static int all_live(void) {
    if (pcc_gc_set_backend(3)!=0) return 1;
    py_gc_disable();
    if (pcc_gc_collect(1)!=0) return 2;
    int32_t map=65;
    PyObject **roots=calloc(65,sizeof(PyObject *));
    uint64_t *ids=calloc(65,sizeof(uint64_t));
    pcc_gc_frame_enter(&map,roots);
    pcc_thread_no_park_enter();
    for (int i=0;i<64;i++) {
        roots[i]=py_str_new("young",5);
        ids[i]=(uint64_t)identity(roots[i]);
    }
    pcc_thread_no_park_exit();
    roots[64]=py_str_new("young",5);
    ids[64]=(uint64_t)identity(roots[64]);
    for (int i=0;i<65;i++) {
        if (!ids[i]) return 3;
        for (int j=0;j<i;j++) if (ids[i]==ids[j]) return 4;
    }
    int64_t before_young=young_nodes(),before_old=pcc_gc_backend4_old_object_count();
    int before_forward=pcc_gc_forwarding_population;
    printf("ALL_LIVE_SETUP young=%lld old=%lld forwarding=%d minor_entries=%lld\n",
           (long long)before_young,(long long)before_old,before_forward,(long long)pcc_gc_telemetry(9));
    /* The actual refill's root batch is16. All65 are registered live roots;
       no additional explicit promotion runs before the measured owner. */
    if (before_young!=49 || before_old!=16 || before_forward!=16 || pcc_gc_telemetry(9)!=1) return 5;
    if (!check_live(roots,ids)) return 6;
    int64_t deaths=automatic_mark1();
    int64_t after_young=young_nodes(),after_old=pcc_gc_backend4_old_object_count();
    int after_forward=pcc_gc_forwarding_population;
    printf("ALL_LIVE_AFTER deaths=%lld young=%lld old=%lld forwarding=%d\n",
           (long long)deaths,(long long)after_young,(long long)after_old,after_forward);
    if (deaths!=0 || !check_live(roots,ids)) return 7;
    /* Positive promotion work means the unchanged same-call idle retirement
       predicate does not fire. The next public steps retain that owner. */
    if (after_young!=33 || after_old!=32 || after_forward!=32) return 8;
    int rounds=0;
    while ((young_nodes() || pcc_gc_forwarding_population) && rounds<8) {
        pcc_gc_step(1024);
        rounds++;
    }
    printf("ALL_LIVE_IDLE rounds=%d young=%lld old=%lld forwarding=%d stable_ids=65\n",
           rounds,(long long)young_nodes(),(long long)pcc_gc_backend4_old_object_count(),pcc_gc_forwarding_population);
    if (young_nodes()!=0 || pcc_gc_forwarding_population!=0 || !check_live(roots,ids)) return 9;
    return 0;
}

int main(int argc, char **argv) {
    if (argc == 2 && strcmp(argv[1], "all-live") == 0) return all_live();
    if (argc != 3 && argc != 4) return 1;
    int automatic = argc == 4;
    if (automatic && strcmp(argv[3], "injected-mark1") != 0) return 1;
    int backend = atoi(argv[1]);
    int old = atoi(argv[2]);
    if (pcc_gc_set_backend(backend) != 0) return 2;
    py_gc_disable();
    if (automatic && pcc_gc_collect(1) != 0) return 12;
    int32_t map = old + 17;
    PyObject **roots = calloc((size_t)map, sizeof(PyObject *));
    uint64_t *hidden = calloc(64, sizeof(uint64_t));
    pcc_gc_frame_enter(&map, roots);
    char text[256];
    for (int i = 0; i < 256; i++) text[i] = 'o';
    for (int i = 0; i < old; i++) { roots[i] = py_str_new(text, 256); pcc_gc_pin(roots[i]); }
    if (pcc_stop_the_world() != 0) return 3;
    pcc_py_gc_minor_graph_lock();
    for (int i = 0; i < old; i++) pcc_gc_generational_promote_young_if_known(roots[i]);
    pcc_py_gc_minor_graph_unlock();
    pcc_resume_world();
    int64_t base_y = young_nodes();
    if (base_y != 0 || pcc_gc_backend4_old_object_count() != old) return 9;
    int64_t before_bumps = pcc_gc_telemetry(20), before_fallback = pcc_gc_telemetry(21);
    int64_t before_refill = pcc_gc_telemetry(19), before_minor = pcc_gc_telemetry(9);
    if (produce(roots, old, hidden) != 0) return 8;
    void *block = pcc_py_gc_minor_current_get();
    int64_t used = block == NULL ? 0 : ((int64_t *)block)[1];
    int64_t capacity = block == NULL ? 0 : ((int64_t *)block)[2];
    int64_t live = block == NULL ? 0 : ((int64_t *)block)[4];
    roots[old+16] = py_str_new("young", 5);
    uint64_t extra_root_id = (uint64_t)identity(roots[old+16]);
    if (!extra_root_id) return 14;
    for (int i=0;i<64;i++) if (extra_root_id==hidden[i]) return 14;
    int64_t before_y = young_nodes(), before_old = pcc_gc_backend4_old_object_count();
    if (before_y != 65 || before_old != old) return 10;
    int64_t bumps = pcc_gc_telemetry(20)-before_bumps, fallback = pcc_gc_telemetry(21)-before_fallback;
    int64_t refills = pcc_gc_telemetry(19)-before_refill;
    int64_t minor0 = pcc_gc_telemetry(9)-before_minor;
    int64_t processed = pcc_gc_generational_step(1024, 0);
    int64_t after_step_known = -1, after_step_y = young_nodes();
    int64_t p0 = pcc_gc_telemetry(131), p1 = pcc_gc_telemetry(135), p2 = pcc_gc_telemetry(139);
    int64_t deaths = pcc_gc_telemetry(140), stw = pcc_gc_telemetry(119);
    int64_t collected = automatic ? automatic_mark1() : pcc_gc_collect(1);
    if (collected < 0) return 13;
    int64_t after_full_known = -1, survivors = 0;
    int64_t canonical_after = canonical_young_payloads();
    for (int i = 0; i < 17; i++) {
        int64_t lease = pcc_gc_foreign_lease_acquire(&roots[old+i]);
        if (lease < 0 || py_str_len(roots[old+i]) != 5) return 5;
        uint64_t expected_id = i < 16 ? hidden[i] : extra_root_id;
        if ((uint64_t)identity(roots[old+i]) != expected_id) return 11;
        survivors++;
        pcc_gc_foreign_lease_release(&roots[old+i], lease);
    }
    printf("{\"backend\":%d,\"old\":%d,\"initial_young_nodes\":%lld,\"before_young_nodes\":%lld,\"before_old_objects\":%lld,\"minor_bumps\":%lld,\"fallbacks\":%lld,\"new_blocks\":%lld,\"minor_entries\":%lld,\"cursor_used\":%lld,\"capacity\":%lld,\"block_live\":%lld,\"step_processed\":%lld,\"after_step_young_nodes\":%lld,\"garbage_known_after_step\":%lld,\"garbage_known_after_major\":%lld,\"rooted_known_after_major\":%lld,\"major_return\":%lld,\"pass0_nodes\":%lld,\"pass1_nodes\":%lld,\"pass2_nodes\":%lld,\"major_deaths\":%lld,\"major_stops\":%lld,\"canonical_payloads_after_major\":%lld}\n", backend, old, (long long)base_y,(long long)before_y,(long long)before_old,(long long)bumps,(long long)fallback,(long long)refills,(long long)minor0,(long long)used,(long long)capacity,(long long)live,(long long)processed,(long long)after_step_y,(long long)after_step_known,(long long)after_full_known,(long long)survivors,(long long)collected,(long long)(pcc_gc_telemetry(131)-p0),(long long)(pcc_gc_telemetry(135)-p1),(long long)(pcc_gc_telemetry(139)-p2),(long long)(pcc_gc_telemetry(140)-deaths),(long long)(pcc_gc_telemetry(119)-stw),(long long)canonical_after);
    int64_t first_retained = retained_identities(hidden);
    int64_t second = pcc_gc_collect(1);
    int64_t second_canonical = canonical_young_payloads();
    printf("SECOND gc=%d old=%d deaths=%lld canonical=%lld\n", backend,old,(long long)second,(long long)second_canonical);
    int64_t second_retained = retained_identities(hidden);
    if (first_retained != 0 || second_retained != 0) return 6;
    if (collected != 48 || second != 0 || canonical_after != 17 || second_canonical != 17) return 7;
    return survivors == 17 ? 0 : 4;
}
