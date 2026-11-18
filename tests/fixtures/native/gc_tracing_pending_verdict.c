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
       TEMP_FIRST = 5, TEMP_SECOND = 6, INTERIOR = 7, GC_CALLBACK = 8,
       PLAIN_FIRST = 9, PLAIN_SECOND = 10, ROOT_COUNT = 11 };

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
extern void pcc_gc_tracing_candidates_reset(void);
extern void pcc_gc_cms_stop_worker(void);
extern int64_t pcc_thread_start(void **, void *(*)(void *), void *);
extern int64_t pcc_thread_join(void *, void **);
extern int64_t pcc_gc_tracing_finish_commits;
extern PyClassObject *py_class_new(const char *, PyClassObject **, int32_t,
                                  const char **, int32_t);
extern void py_class_add_method(PyClassObject *, const char *, PyObject *);
extern PyObject *py_instance_new(PyClassObject *);
extern void py_instance_set_field(PyObject *, int32_t, PyObject *);
extern PyObject *py_instance_get_field(PyObject *, int32_t);

static PyObject *roots[ROOT_COUNT];
static const int32_t root_map[1] = {ROOT_COUNT};
static PyObject *watched[2]; /* address diagnostics, never roots or owners */
static int mode;
enum { NO_RETAINED_ROOT, REGISTERED_SECOND, CALLER_STACK_SECOND };
static int retained_second;
static int retained_prior_callback_error = -1;
static int64_t partial_collected = -1;
static int held_world;
static int phase;
static int finalizers;
static int weak_callbacks;
static int gc_callbacks;
static int callback_error;
static int finalizer_calls[2];
static int weak_callback_calls[2];
/* Failure-only snapshots contain scalars, never target addresses or roots. */
static int failure_check;
static int64_t failure_collected;
static int failure_known_first;
/* Public, monotonic phase counters only. No target is read for this witness. */
static int64_t handoff_recheck, handoff_cleared;
static int64_t first_weak_rechecks = -1, first_weak_cleared = -1;

static PyObject *weak_callback(PyObject *captures, PyObject *args) {
    (void)captures;
    if (mode == 7 && weak_callbacks == 0) {
        first_weak_rechecks = pcc_gc_telemetry(PCC_GC_COUNTER_SWEEP_RECHECK_CALLS) - handoff_recheck;
        first_weak_cleared = pcc_gc_telemetry(PCC_GC_COUNTER_SWEEP_RECHECK_CANDIDATES_CLEARED) - handoff_cleared;
    }
    weak_callbacks++;
    /* The callback receives the weakref, not its target. Do not create a
     * conservative target alias while checking the early callback order. */
    PyObject *ref = py_tuple_get(args, 0);
    int index = ref == roots[WEAK_FIRST] ? 0 :
                (ref == roots[WEAK_SECOND] ? 1 : -1);
    if (ref == 0 || index < 0) callback_error = 4;
    else if (++weak_callback_calls[index] != 1) callback_error = 5;
    if (finalizers != 0) callback_error = 6;
    if (ref != 0) {
        PyObject *target = py_weakref_call(ref);
        if (target != py_None) callback_error = 7;
        if (target != 0) py_decref(target);
        py_decref(ref);
    }
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
    int index = self == watched[0] ? 0 : (self == watched[1] ? 1 : -1);
    if (index < 0) callback_error = 1;
    else if (++finalizer_calls[index] != 1) callback_error = 8;
    /* A deliberate order mismatch on a rooted control must not hide an
     * earlier bad weak callback, target identity or duplicate. */
    if (retained_second != NO_RETAINED_ROOT && finalizers == 0) {
        retained_prior_callback_error = callback_error;
    }
    if (mode == 2 || mode == 3 || mode == 4 || mode == 7) {
        int count = mode == 4 || mode == 7 ? 2 : 1;
        if (weak_callbacks != count) callback_error = 9;
    }
    finalizers++;
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
    if (backend < 1 || backend > 4) return 101;
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
    static const char *fields[1] = {"peer"};
    PyClassObject *cls = py_class_new("SweepVerdictToken", 0, 0, fields, 1);
    if (!install_root(CLASS, (PyObject *)cls)) return 120;
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
        if (!install_root(PLAIN_FIRST + index, py_weakref_new(self, 0))) return 129;
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
        /* Mode 7 transfers the two owning slots across a real thread join.
         * The collector drops these slots only after reacquiring the world. */
        if (mode != 7) pcc_gc_store_root(&roots[TEMP_FIRST + index], 0);
    }
    pcc_gc_store_root(&roots[INTERIOR], 0);
    if (!phase_clean() || !debt_safe()) return 128;
    return 0;
}

static int graph_matches(int count) {
    /* GC1-GC4 are count-free. Header refcounts do not count owning edges;
     * inspect the actual graph before injecting the synthetic verdict.
     * The world is still stopped, and every getter's new reference is released.
     */
    int index;
    for (index = 0; index < count; index++) {
        PyObject *peer = py_instance_get_field(watched[index], 0);
        int same = peer == watched[mode == 4 ? 1 - index : index];
        if (peer != 0) py_decref(peer);
        if (!same) return 0;
    }
    if (py_list_len(roots[KEEPER]) != (mode == 1 ? 1 : 0)) return 0;
    if (mode == 1) {
        PyObject *interior = py_list_get(roots[KEEPER], 0);
        if (interior == 0) return 0;
        int same = 0;
        if (py_list_len(interior) == 1) {
            PyObject *target = py_list_get(interior, 0);
            same = target == watched[0];
            if (target != 0) py_decref(target);
        }
        py_decref(interior);
        if (!same) return 0;
    }
    return 1;
}

static int inject_candidates(int count) {
    phase = 3;
    /* Keep this fixture tied to the production count-free ownership policy,
     * rather than silently accepting a future counted configuration. */
    if (pcc_gc_backend4_no_refcounts != 1) return 159;
    pcc_py_gc_minor_graph_lock();
    int index;
    for (index = 0; index < count; index++) {
        PyObject *obj = watched[index];
        if (pcc_gc_object_is_known_no_lock(obj) != 1) {
            pcc_py_gc_minor_graph_unlock();
            return 130;
        }
        PyObjectHeader *header = (PyObjectHeader *)obj;
        if ((header->flags & (PINNED | DEAD)) != 0) {
            printf("sweep-verdict:precondition backend=%d mode=%d index=%d flags=0x%x refcount=%lld\n",
                   pcc_gc_backend(), mode, index, (unsigned)header->flags,
                   (long long)header->refcount);
            pcc_py_gc_minor_graph_unlock();
            return 131;
        }
    }
    pcc_py_gc_minor_graph_unlock();
    /* Check known-object admission before any field getter. Keep getters
     * outside the graph lock; the stopped world still protects this graph. */
    if (!graph_matches(count)) return 158;
    pcc_py_gc_minor_graph_lock();
    for (index = 0; index < count; index++) {
        PyObjectHeader *header = (PyObjectHeader *)watched[index];
        header->flags = (header->flags & ~FRESH) | CANDIDATE;
    }
    /* These synthetic candidates are not a new completed mark. The owner's
     * reset invalidates the last finish's candidate list without releasing
     * its buffer. Also invalidate the empty-candidate cache and same-world
     * proof, without fabricating a finish commit or weakening sweep-owed. */
    pcc_gc_tracing_candidates_reset();
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

/* Two deliberately independent disposal targets must both be unreachable.
 * A native setup thread legitimately possesses both while it builds and
 * verifies the graph; a full conservative scan must not ignore any such
 * alias. End that thread before the collector releases the temporary roots.
 * The main thread registered roots[] and owns it throughout the handoff. */
static int64_t handoff_finish, handoff_pass0, handoff_forwards;
static int producer_status;
static void *prepare_partial_graph(void *unused) {
    (void)unused;
    if (pcc_current_thread_id() <= 0 || pcc_stop_the_world() != 0) {
        producer_status = 161;
        return 0;
    }
    int status = build_graph();
    if (status == 0) status = inject_candidates(2);
    handoff_finish = pcc_gc_tracing_finish_commits;
    handoff_recheck = pcc_gc_telemetry(PCC_GC_COUNTER_SWEEP_RECHECK_CALLS);
    handoff_cleared = pcc_gc_telemetry(PCC_GC_COUNTER_SWEEP_RECHECK_CANDIDATES_CLEARED);
    handoff_pass0 = pcc_gc_telemetry(PCC_GC_COUNTER_SWEEP_PASS0_CALLS);
    handoff_forwards = pcc_gc_telemetry(PCC_GC_COUNTER_RELOCATION_FORWARDS);
    if (pcc_resume_world() != 0) status = 162;
    producer_status = status;
    return 0;
}

static int handoff_partial_graph(void) {
    if (pcc_resume_world() != 0) return 163;
    held_world = 0;
    void *creator = 0;
    producer_status = 164;
    if (pcc_thread_start(&creator, prepare_partial_graph, 0) != 0) return 164;
    if (pcc_thread_join(creator, 0) != 0) return 165;
    if (producer_status != 0) return producer_status;
    if (pcc_stop_the_world() != 0) return 166;
    held_world = 1;
    /* Reject an intervening collection; do not re-inject lost candidates
     * or disable background work to obtain the desired verdict. */
    if (!phase_clean() || !debt_safe() || finalizers != 0 || weak_callbacks != 0
        || handoff_finish != pcc_gc_tracing_finish_commits
        || handoff_recheck != pcc_gc_telemetry(PCC_GC_COUNTER_SWEEP_RECHECK_CALLS)
        || handoff_pass0 != pcc_gc_telemetry(PCC_GC_COUNTER_SWEEP_PASS0_CALLS)
        || handoff_forwards != pcc_gc_telemetry(PCC_GC_COUNTER_RELOCATION_FORWARDS)) return 167;
    pcc_gc_store_root(&roots[TEMP_FIRST], 0);
    if (retained_second != REGISTERED_SECOND) pcc_gc_store_root(&roots[TEMP_SECOND], 0);
    return 0;
}

static int plain_matches(int index, int live) {
    PyObject *target = py_weakref_call(roots[PLAIN_FIRST + index]);
    int same = target == (live ? watched[index] : py_None);
    if (target != 0) py_decref(target);
    return same;
}

static int inspect_live(int index, int callback_live) {
    PyObject *obj = watched[index];
    if (pcc_gc_object_is_known_no_lock(obj) != 1) return 0;
    if ((((PyObjectHeader *)obj)->flags & CANDIDATE) != 0) return 0;
    PyObject *peer = py_instance_get_field(obj, 0);
    int same = peer == watched[mode == 4 ? 1 - index : index];
    if (peer != 0) py_decref(peer);
    return same && weak_matches(index, callback_live) && plain_matches(index, 1);
}

static int inspect_callback_keeper(void) {
    /* Inspect only AFTER collection; a watch on the C stack before the
     * collection would itself conservatively root the target. */
    if (py_list_len(roots[KEEPER]) != 1) return 0;
    PyObject *kept = py_list_get(roots[KEEPER], 0);
    int same = kept == watched[0];
    if (kept != 0) py_decref(kept);
    return same;
}

static void report_live_verdict_failure(int status, int expected_finalizers,
                                       int expected_weak_callbacks,
                                       int64_t collected, int known_first,
                                       int known_second) {
    /* Failure-only scalar diagnostics: watched addresses may be reclaimed.
     * The native receipt separately records the requested gc_backend. */
    printf("sweep-verdict:detail status=%d phase=%d mode=%d effective_backend=%lld "
           "finalizers=%d expected_finalizers=%d "
           "weak_callbacks=%d expected_weak_callbacks=%d "
           "collected=%lld expected_collected=%s "
           "known_first=%d expected_known_first=1 "
           "known_second=%d gc_callbacks=%d callback_error=%d\n",
           status, phase, mode, (long long)pcc_gc_backend(),
           finalizers, expected_finalizers, weak_callbacks, expected_weak_callbacks,
           (long long)collected, mode == 5 ? "nonnegative-whole-heap" : "0",
           known_first, known_second, gc_callbacks, callback_error);
}

static int run_mode(void) {
    phase = 4;
    int count = mode == 4 || mode == 7 ? 2 : 1;
    int64_t collected;
    if (mode == 6) {
        int before = ((PyObjectHeader *)watched[0])->flags;
        if (pcc_gc_tracing_sweep_unreachable(0) != 0
            || pcc_gc_tracing_sweep_unreachable(-1) != 0) return 140;
        if (finalizers != 0 || weak_callbacks != 0
            || ((PyObjectHeader *)watched[0])->flags != before) return 141;
        /* Leave the no-work check in a safe live state before process exit. */
        py_list_append(roots[KEEPER], watched[0]);
    }
    if (mode == 5) {
        collected = pcc_gc_collect(0);
        /* The public full collection can relocate the now-live graph on
         * GC3/4. Resolve the diagnostic address before checking it. */
        watched[0] = pcc_gc_note_relocation_read(watched[0]);
    } else {
        pcc_gc_begin_explicit_tracing_collect();
        collected = mode == 7 ? pcc_gc_tracing_sweep_unreachable(1) : pcc_gc_collect_tracing();
        pcc_gc_end_explicit_tracing_collect();
    }
    /* Address-index snapshots precede any allocation after reclamation. */
    int known_first = (int)pcc_gc_object_is_known_no_lock(watched[0]);
    int known_second = count == 2 ? (int)pcc_gc_object_is_known_no_lock(watched[1]) : 0;
    if (retained_second != NO_RETAINED_ROOT) partial_collected = collected;
    if (callback_error != 0) {
        failure_check = 1;
        return 142;
    }
    /* Mode 5 uses full-heap gc.collect(), which may reclaim callback temporaries and
     * the disconnected INTERIOR list, not a watched-target-only sweep.
     * Its rescue oracle is the target graph, weak refs and keeper below. */
    if (mode == 1 || mode == 5 || mode == 6) {
        if (finalizers != 0 || weak_callbacks != 0 || collected < 0 || (mode != 5 && collected != 0) || known_first != 1) {
            report_live_verdict_failure(143, 0, 0, collected, known_first, known_second);
            return 143;
        }
        if (!inspect_live(0, 1)) return 144;
        if (mode == 5 && (gc_callbacks != 2 || !inspect_callback_keeper())) return 145;
    } else if (mode == 2) {
        if (finalizers != 1 || weak_callbacks != 1 || collected != 1 || known_first != 0) {
            failure_check = 3;
            failure_collected = collected;
            failure_known_first = known_first;
            return 146;
        }
        if (!weak_matches(0, 0) || !plain_matches(0, 0)) return 147;
        pcc_gc_begin_explicit_tracing_collect();
        if (pcc_gc_collect_tracing() != 0) return 148;
        pcc_gc_end_explicit_tracing_collect();
        if (finalizers != 1 || weak_callbacks != 1) return 149;
    } else if (mode == 3 || mode == 4) {
        /* CPython 3.15 test_finalization.py: test_simple_resurrect and
         * check_resurrecting_chain. Callback refs die before finalizers;
         * plain refs survive when the intact graph is resurrected. */
        if (finalizers != count || weak_callbacks != count || collected != 0 || known_first != 1) {
            report_live_verdict_failure(150, count, count, collected, known_first, known_second);
            return 150;
        }
        if (!inspect_live(0, 0)) return 151;
        if (count == 2 && (known_second != 1 || !inspect_live(1, 0))) return 152;
    } else if (mode == 7) {
        /* Both weakref batches precede budgeted clearing/reclamation. Even
         * the one not yet freed is dead, so neither kind of ref retains it. */
        if (first_weak_rechecks < 1 || first_weak_cleared != 0) return 168;
        if (finalizers != 2 || weak_callbacks != 2 || collected != 1 || known_first + known_second != 1) return 153;
        if (!weak_matches(0, 0) || !weak_matches(1, 0)
            || !plain_matches(0, 0) || !plain_matches(1, 0)) return 154;
        pcc_gc_begin_explicit_tracing_collect();
        int64_t next = pcc_gc_tracing_sweep_unreachable(1);
        pcc_gc_end_explicit_tracing_collect();
        int known_after = (int)pcc_gc_object_is_known_no_lock(watched[0])
                          + (int)pcc_gc_object_is_known_no_lock(watched[1]);
        if (next != 1 || known_after != 0 || finalizers != 2 || weak_callbacks != 2) return 155;
        if (!weak_matches(0, 0) || !weak_matches(1, 0)
            || !plain_matches(0, 0) || !plain_matches(1, 0)) return 156;
    }
    /* Totals alone could hide a duplicate call paired with a missed object.
     * Recheck errors after the repeated collections in modes 2 and 7 too. */
    if (callback_error != 0) {
        failure_check = 2;
        return 142;
    }
    int expected_calls = mode == 2 || mode == 3 || mode == 4 || mode == 7;
    int index;
    for (index = 0; index < count; index++) {
        if (finalizer_calls[index] != expected_calls
            || weak_callback_calls[index] != expected_calls) return 160;
    }
    printf("sweep-verdict:ok backend=%d mode=%d finalizers=%d weak_callbacks=%d gc_callbacks=%d\n",
           pcc_gc_backend(), mode, finalizers, weak_callbacks, gc_callbacks);
    return 0;
}

/* Negative controls for the original two-dead oracle. A target still
 * held by a live native caller is just as real a root as a registered slot.
 * The volatile local is used by address AFTER the full sweep. The unrooted
 * case and the registered-slot control never enter this caller frame. */
static int still_names_second(PyObject *volatile *slot) {
    return *slot == watched[1];
}

static int run_with_caller_stack_root(void) {
    PyObject *volatile keep = watched[1];
    int status = run_mode();
    if (!still_names_second(&keep)) return 169;
    return status;
}

static int verify_retained_second(int status) {
    /* A 142 by itself proves nothing. Verify the first-slice error, exact
     * work, per-object counts, both sorts of ref, and the still-intact graph.
     * Preserve the original rejection status; never convert it to success. */
    if (status != 142) return status != 0 ? status : 170;
    if (failure_check != 1 || retained_prior_callback_error != 0 || callback_error != 9
        || partial_collected != 1 || first_weak_rechecks < 1 || first_weak_cleared != 1
        || weak_callbacks != 1 || finalizers != 1
        || weak_callback_calls[0] != 1 || weak_callback_calls[1] != 0
        || finalizer_calls[0] != 1 || finalizer_calls[1] != 0) return 170;
    if (pcc_gc_object_is_known_no_lock(watched[0]) != 0 || !weak_matches(0, 0)
        || !plain_matches(0, 0) || !inspect_live(1, 1)) return 171;
    return status;
}

static void report_cycle_failure(int status) {
    /* main has already printed the original blocked line. callback_error is
     * the final stored value: later callback checks may have overwritten it.
     * check 1/2 identify first/repeated callback checks; 3 is the dead cycle.
     */
    if (status == 142) {
        printf("sweep-verdict:detail status=142 check=%d phase=%d mode=%d "
               "final_callback_error=%d expected_callback_error=0 finalizers=%d weak_callbacks=%d "
               "finalizer_first=%d finalizer_second=%d weak_first=%d weak_second=%d\n",
               failure_check, phase, mode, callback_error, finalizers, weak_callbacks,
               finalizer_calls[0], finalizer_calls[1],
               weak_callback_calls[0], weak_callback_calls[1]);
    } else if (status == 146) {
        printf("sweep-verdict:detail status=146 check=%d phase=%d mode=%d "
               "finalizers=%d expected_finalizers=1 weak_callbacks=%d expected_weak_callbacks=1 "
               "collected=%lld expected_collected=1 known_first=%d expected_known_first=0 "
               "final_callback_error=%d finalizer_first=%d weak_first=%d\n",
               failure_check, phase, mode, finalizers, weak_callbacks,
               (long long)failure_collected, failure_known_first, callback_error,
               finalizer_calls[0], weak_callback_calls[0]);
    }
}

int main(int argc, char **argv) {
    if ((argc != 2 && argc != 3) || argv[1][0] < '1' || argv[1][0] > '7' || argv[1][1] != 0) return 100;
    mode = argv[1][0] - '0';
    if (argc == 3) {
        if (mode != 7 || (argv[2][0] != 'r' && argv[2][0] != 's') || argv[2][1] != 0) return 100;
        retained_second = argv[2][0] == 'r' ? REGISTERED_SECOND : CALLER_STACK_SECOND;
    }
    int status = initialize();
    if (status == 0 && mode == 7) {
        status = handoff_partial_graph();
    } else if (status == 0) {
        status = build_graph();
        if (status == 0) status = inject_candidates(mode == 4 ? 2 : 1);
    }
    if (status == 0) {
        status = retained_second == CALLER_STACK_SECOND ? run_with_caller_stack_root() : run_mode();
        if (retained_second != NO_RETAINED_ROOT) status = verify_retained_second(status);
    }
    if (status != 0) {
        printf("sweep-verdict:blocked status=%d phase=%d mode=%d\n", status, phase, mode);
        report_cycle_failure(status);
    }
    if (status != 0 && mode == 7) {
        printf("sweep-verdict:before-first-weak rechecks=%lld candidates_cleared=%lld\n",
               (long long)first_weak_rechecks, (long long)first_weak_cleared);
    }
    if (status == 142 && retained_second != NO_RETAINED_ROOT) {
        printf("sweep-verdict:retained root=%s backend=%d collected=%lld first=dead second=live\n",
               retained_second == REGISTERED_SECOND ? "registered" : "caller-stack",
               pcc_gc_backend(), (long long)partial_collected);
    }
    if (held_world != 0) {
        if (pcc_resume_world() != 0
            && (status == 0 || (retained_second != NO_RETAINED_ROOT && status == 142))) status = 157;
        pcc_gc_cms_stop_worker();
    }
    return status;
}
