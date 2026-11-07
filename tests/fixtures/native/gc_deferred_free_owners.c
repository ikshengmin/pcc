/* Real graph fixture derived from gc_tracing_pending_verdict.c, extended
 * with pre-resume weakref callbacks, finalizer-created live objects and
 * deferred-release counters. No allocator/runtime stubs. */
/* Deterministic sweep-boundary regressions on the real tracing runtime.
 * This fixture injects a pending CANDIDATE verdict on fully initialized graphs
 * under STW and the graph lock, invalidating cached verdict/provenance metadata.
 * It does not reproduce a worker race or
 * the Darwin typed-loop failure. Every mode runs in a fresh process.
 */
#include "py_runtime.h"
#include <stdint.h>
#include <stdio.h>

enum { WHITE = 8, PINNED = 64, CANDIDATE = 1024, FRESH = 16384, DEAD = 524288 };
enum { KEEPER = 0, CLASS = 1, CALLBACK = 2, WEAK_FIRST = 3, WEAK_SECOND = 4,
       TEMP_FIRST = 5, TEMP_SECOND = 6, INTERIOR = 7, GC_CALLBACK = 8, ROOT_COUNT = 9 };

extern int32_t pcc_gc_mark_active;
extern int32_t pcc_gc_backend4_no_refcounts;
extern int32_t pcc_gc_explicit_collect_active;
extern int32_t pcc_gc_trace_extension_roots_pending;
extern int64_t pcc_gc_tracing_finish_claim_epoch;
extern int64_t pcc_gc_tracing_no_candidate_commit;
extern int64_t pcc_gc_tracing_finish_stop_epoch;
extern int64_t pcc_gc_debt_threshold_override;
extern int64_t pcc_gc_debt_bytes;
extern int64_t pcc_gc_gray_count_load_acquire(void);
extern int64_t pcc_gc_object_is_known_no_lock(PyObject *);
extern int64_t pcc_gc_sweep_owed(void);
extern void pcc_gc_begin_explicit_tracing_collect(void);
extern void pcc_gc_end_explicit_tracing_collect(void);
extern int64_t pcc_gc_collect_tracing(void);
extern int64_t pcc_gc_tracing_sweep_unreachable(int64_t);
extern int64_t pcc_gc_cms_stop_worker(void);
extern PyClassObject *py_class_new(const char *, PyClassObject **, int32_t,
                                  const char **, int32_t);
extern void py_class_add_method(PyClassObject *, const char *, PyObject *);
extern PyObject *py_instance_new(PyClassObject *);
extern void py_instance_set_field(PyObject *, int32_t, PyObject *);
extern PyObject *py_instance_get_field(PyObject *, int32_t);

static PyObject *roots[ROOT_COUNT];
static const int32_t root_map[1] = {ROOT_COUNT};
static PyObject *watched[64]; /* address diagnostics, never roots or owners */
static int mode;
static int held_world;
static int phase;
static int finalizers;
static int weak_callbacks;
static int gc_callbacks;
static int callback_error;


static PyObject *weak_callback(PyObject *captures, PyObject *args) {
    (void)captures;
    (void)args;
    if (!pcc_thread_owns_stopped_world()) callback_error = 13;
    weak_callbacks++;
    return py_int_from_i64(0);
}

static PyObject *start_callback(PyObject *captures, PyObject *args) {
    (void)captures;
    (void)args;
    gc_callbacks++;
    if (gc_callbacks == 1) py_list_append(roots[KEEPER], watched[0]);
    return py_int_from_i64(0);
}

static PyObject *token_del(PyObject *self) {
    if (self != watched[0] && self != watched[1]) callback_error = 1;
    finalizers++;
    if (mode == 2) {
        PyObject *newborn = py_list_new(0);
        pcc_gc_store_root(&roots[INTERIOR], newborn);
        py_decref(newborn);
    }
    /* Every callback sees the cycle intact, including the later finalizer in
     * a batch where an earlier callback has already resurrected the graph. */
    PyObject *peer = py_instance_get_field(self, 0);
    if (peer == 0 || (peer != watched[0] && peer != watched[1])) callback_error = 2;
    if (peer != 0) py_decref(peer);
    if (mode == 3 || (mode == 4 && finalizers == 1)) {
        py_list_append(roots[KEEPER], self);
    }
    /* The public entry guard must still prevent nested collection. */
    if (pcc_gc_collect(0) != 0) callback_error = 3;
    return py_int_from_i64(0);
}

static int phase_clean(void) {
    return pcc_gc_mark_active == 0
        && pcc_gc_explicit_collect_active == 0
        && pcc_gc_trace_extension_roots_pending == 0
        && pcc_gc_tracing_finish_claim_epoch == 0
        && pcc_gc_gray_count_load_acquire() == 0;
}

static int debt_safe(void) {
    return pcc_gc_debt_threshold_override == 1099511627776LL
        && pcc_gc_debt_bytes < pcc_gc_debt_threshold_override;
}

static int initialize(void) {
    phase = 1;
    int backend = pcc_gc_backend();
    if (backend < 0 || backend > 4) return 101;
    if (sizeof(void *) != 8 || sizeof(PyObjectHeader) != 16) return 102;
    if (pcc_threads_enabled() != 1) return 103;
    if (pcc_refcount_strategy() != PCC_REFCOUNT_STRATEGY_ATOMIC) return 104;
    if (pcc_current_thread_id() <= 0 || !debt_safe()) return 105;
    if (pcc_stop_the_world() != 0) return 106;
    held_world = 1;
    if (pcc_thread_owns_stopped_world() != 1 || pcc_thread_no_park_depth() != 0) return 107;
    (void)pcc_gc_collect(0);
    if (!phase_clean() || pcc_gc_sweep_owed() != 0) return 108;
    pcc_gc_frame_enter(root_map, roots);
    return 0;
}

static int install_root(int slot, PyObject *value) {
    if (value == 0) return 0;
    pcc_gc_store_root(&roots[slot], value);
    py_decref(value);
    return 1;
}

static int build_graph(void) {
    phase = 2;
    if (mode == 9) {
        /* Fixed batch, no retry-to-success: later ordinary allocations leave
         * only the newest temporary C address in this helper's active slots.
         * We do not clear pins, frame words or registered roots to force death. */
        for (int index = 0; index < 64; index++) {
            PyObject *leaf = pcc_gc_alloc(24, PY_TYPE_FLOAT, 0);
            if (!install_root(TEMP_FIRST, leaf)) return 173;
            *((double *)((char *)roots[TEMP_FIRST] + 16)) = 3.25;
            pcc_gc_publish_initialized(roots[TEMP_FIRST]);
            watched[index] = roots[TEMP_FIRST];
            pcc_gc_store_root(&roots[TEMP_FIRST], 0);
        }
        if (!phase_clean() || !debt_safe()) return 174;
        return 0;
    }
    static const char *fields[1] = {"peer"};
    PyClassObject *cls = py_class_new("SweepVerdictToken", 0, 0, fields, 1);
    if (!install_root(CLASS, (PyObject *)cls)) return 120;
    /* Mode 6 is the independent plain-owner control, not a weakened
     * finalizer expectation: its class never installs a finalizer. */
    if (mode != 6)
        py_class_add_method((PyClassObject *)roots[CLASS], "__del__",
                            (PyObject *)(uintptr_t)token_del);
    if (!install_root(KEEPER, py_list_new(1))) return 121;
    if (!install_root(INTERIOR, py_list_new(1))) return 122;
    if (!install_root(CALLBACK, py_func_new((void *)weak_callback, 0))) return 123;
    if (!install_root(TEMP_FIRST, py_instance_new((PyClassObject *)roots[CLASS]))) return 124;
    int count = mode == 4 || mode == 7 ? 2 : 1;
    if (count == 2 && !install_root(TEMP_SECOND, py_instance_new((PyClassObject *)roots[CLASS]))) return 125;
    int index;
    for (index = 0; index < count; index++) {
        PyObject *self = roots[TEMP_FIRST + index];
        PyObject *peer = mode == 4 ? roots[TEMP_FIRST + 1 - index] : self;
        py_instance_set_field(self, 0, peer);
        if (!install_root(WEAK_FIRST + index, py_weakref_new(self, roots[CALLBACK]))) return 126;
    }
    if (mode == 1) {
        py_list_append(roots[INTERIOR], roots[TEMP_FIRST]);
        py_list_append(roots[KEEPER], roots[INTERIOR]);
    }
    if (mode == 5) {
        if (!install_root(GC_CALLBACK, py_func_new((void *)start_callback, 0))) return 127;
        py_gc_callbacks_append(roots[GC_CALLBACK]);
    }
    for (index = 0; index < count; index++) {
        watched[index] = roots[TEMP_FIRST + index];
        pcc_gc_store_root(&roots[TEMP_FIRST + index], 0);
    }
    pcc_gc_store_root(&roots[INTERIOR], 0);
    if (!phase_clean() || !debt_safe()) return 128;
    return 0;
}

static int inject_candidates(int count) {
    phase = 3;
    pcc_py_gc_minor_graph_lock();
    int index;
    for (index = 0; index < count; index++) {
        PyObject *obj = watched[index];
        if (pcc_gc_object_is_known_no_lock(obj) != 1) {
            pcc_py_gc_minor_graph_unlock();
            return 130;
        }
        PyObjectHeader *header = (PyObjectHeader *)obj;
        /* Count-free stores do not increment this initial header word.
         * Reachability comes from the constructed graph and registered roots. */
        int64_t expected_refcount = pcc_gc_backend4_no_refcounts ? 1 : (mode == 1 ? 2 : 1);
        if ((header->flags & (PINNED | DEAD)) != 0 || header->refcount != expected_refcount) {
            pcc_py_gc_minor_graph_unlock();
            return 131;
        }
        header->flags = (header->flags & ~FRESH) | CANDIDATE;
    }
    /* These synthetic candidates are not a new completed mark. Invalidate
     * the last mark's empty-candidate cache and same-world proof, without
     * fabricating a finish commit or weakening the sweep-owed assertion. */
    pcc_gc_tracing_no_candidate_commit = -1;
    pcc_gc_tracing_finish_stop_epoch = -1;
    pcc_py_gc_minor_graph_unlock();
    return pcc_gc_sweep_owed() == 1 && finalizers == 0 ? 0 : 132;
}

static int weak_matches(int index, int live) {
    PyObject *target = py_weakref_call(roots[WEAK_FIRST + index]);
    int same = target == (live ? watched[index] : py_None);
    if (target != 0) py_decref(target);
    return same;
}

static int inspect_live(int index) {
    PyObject *obj = watched[index];
    if (pcc_gc_object_is_known_no_lock(obj) != 1) return 0;
    if ((((PyObjectHeader *)obj)->flags & CANDIDATE) != 0) return 0;
    PyObject *peer = py_instance_get_field(obj, 0);
    int same = peer == watched[mode == 4 ? 1 - index : index];
    if (peer != 0) py_decref(peer);
    return same && weak_matches(index, 1);
}

extern int64_t pcc_gc_deferred_free_counter(int64_t);
extern int32_t pcc_gc_backend4_auto_step_pending;
static int route;
static int64_t before_enqueued, before_drained;
static int owner_callbacks;
static PyObject *owner_callback(PyObject *captures, PyObject *args) {
    (void)captures; (void)args;
    owner_callbacks++;
    if (pcc_gc_deferred_free_counter(0) || pcc_gc_deferred_free_counter(1)) callback_error = 10;
    if (owner_callbacks == 2) {
        /* Physical release may still belong to CMS. TLS ownership must
         * already be closed; equality is checked after quiescent join below. */
        if (route != 2 && pcc_thread_owns_stopped_world()) callback_error = 12;
    }
    return py_int_from_i64(0);
}
static int owners(void) {
    int status = initialize();
    if (status) return status;
    status = build_graph();
    if (status) return status;
    if (!install_root(GC_CALLBACK, py_func_new((void *)owner_callback, 0))) return 160;
    py_gc_callbacks_append(roots[GC_CALLBACK]);
    if (pcc_gc_backend() != 0) {
        status = inject_candidates(mode == 9 ? 64 : (mode == 4 ? 2 : 1));
        if (status) return status;
    }
    before_enqueued = pcc_gc_deferred_free_counter(3);
    before_drained = pcc_gc_deferred_free_counter(4);
    if (route != 2 && route != 3 && route != 5) {
        if (pcc_resume_world()) return 161;
        held_world = 0;
    }
    if (route == 4) {
        __atomic_store_n(&pcc_gc_backend4_auto_step_pending, 1, __ATOMIC_RELEASE);
        pcc_gc_safepoint();
    } else if (route == 3 || route == 5) {
        pcc_gc_begin_explicit_tracing_collect();
        if (route == 5) (void)pcc_gc_tracing_sweep_unreachable(4096);
        else (void)pcc_gc_collect_tracing();
        pcc_gc_end_explicit_tracing_collect();
    } else {
        (void)pcc_gc_collect(0);
    }
    if (mode != 2 && mode != 6 && mode != 9) {
        watched[0] = pcc_gc_note_relocation_read(watched[0]);
        if (mode == 4) watched[1] = pcc_gc_note_relocation_read(watched[1]);
    }
    int known = 0;
    if (mode != 9)
        known = pcc_gc_backend() == 0 ? (int)pcc_gc_pointer_is_managed(watched[0])
            : (int)pcc_gc_object_is_known_no_lock(watched[0]);
    if (callback_error) return 162;
    if (mode == 2) {
        int weak_dead = weak_matches(0, 0);
        if (known || finalizers != 1 || weak_callbacks != 1 || !weak_dead) {
            int64_t queued = pcc_gc_deferred_free_counter(3) - before_enqueued;
            int64_t drained = pcc_gc_deferred_free_counter(4) - before_drained;
            printf("owner-dead-mismatch known=%d finalizers=%d weak_callbacks=%d "
                   "weak_dead=%d callback_error=%d owner_callbacks=%d queued=%lld "
                   "drained=%lld owns_stop=%lld mark_active=%d explicit_active=%d\n",
                   known, finalizers, weak_callbacks, weak_dead, callback_error,
                   owner_callbacks, (long long)queued, (long long)drained,
                   (long long)pcc_thread_owns_stopped_world(), pcc_gc_mark_active,
                   pcc_gc_explicit_collect_active);
            return 163;
        }
        if (!roots[INTERIOR] || !pcc_gc_pointer_is_managed(roots[INTERIOR])) return 171;
    } else if (mode == 9) {
        if (finalizers != 0 || weak_callbacks != 0) return 175;
    } else if (mode == 6) {
        /* Post-resume callbacks may reuse the former target's address. The
         * rooted weakref is the lifetime oracle; known is diagnostic only. */
        int weak_dead = weak_matches(0, 0);
        if (finalizers != 0 || weak_callbacks != 1 || !weak_dead) {
            int64_t queued = pcc_gc_deferred_free_counter(3) - before_enqueued;
            printf("owner-plain-mismatch weak_dead=%d weak_callbacks=%d "
                   "finalizers=%d known=%d owner_callbacks=%d queued=%lld\n",
                   weak_dead, weak_callbacks, finalizers, known, owner_callbacks,
                   (long long)queued);
            return 172;
        }
    } else if (mode == 3 || mode == 4) {
        if (!known || finalizers != (mode == 4 ? 2 : 1) || weak_callbacks) return 164;
        watched[0] = pcc_gc_note_relocation_read(watched[0]);
        if (mode == 4) watched[1] = pcc_gc_note_relocation_read(watched[1]);
        if (pcc_gc_backend() != 0 && !inspect_live(0)) return 165;
    } else if (!known || finalizers || weak_callbacks) return 166;
    /* Keep all semantic checks above at their original boundary. Only the
     * physical completion oracle moves to a successful worker join. */
    if (held_world) {
        if (pcc_resume_world() != 0) return 177;
        held_world = 0;
    }
    if (pcc_gc_cms_stop_worker() != 0) return 178;
    if (pcc_gc_deferred_free_counter(11) || pcc_gc_deferred_free_counter(12)) return 179;
    int64_t queued = pcc_gc_deferred_free_counter(3) - before_enqueued;
    int64_t drained = pcc_gc_deferred_free_counter(4) - before_drained;
    if (queued != drained || pcc_gc_deferred_free_counter(0) || pcc_gc_deferred_free_counter(1)) return 167;
    if (route == 2 || route == 3 || route == 5 || pcc_gc_backend() == 0 || pcc_gc_backend() > 2) {
        if (queued) return 168;
    } else if ((mode == 2 || mode == 6 || mode == 9) && queued == 0) return 169;
    if (owner_callbacks != ((route == 3 || route == 5) ? 0 : 2)) return 170;
    if (mode == 9)
        printf("owner-leaf-batch candidates=64 queued=%lld drained=%lld\n",
               (long long)queued, (long long)drained);
    return 0;
}
int main(int argc, char **argv) {
    if (argc != 3) return 99;
    route = argv[1][0] - '0'; mode = argv[2][0] - '0';
    int status = owners();
    if (held_world) pcc_resume_world();
    pcc_gc_cms_stop_worker();
    printf("deferred-owners backend=%d route=%d mode=%d status=%d\n", pcc_gc_backend(), route, mode, status);
    return status;
}
