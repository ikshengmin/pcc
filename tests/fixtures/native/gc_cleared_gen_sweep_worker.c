/* Real ordinary-generator lifetimes on the owned runtime. Mode 1 uses public
 * collection without candidate mutation. Other modes deliberately install
 * candidate flags and a same-stop-epoch completed-mark verdict, then execute
 * production PASS0/PASS1/PASS2 with budget 1. Real post-callback/finalizer
 * reachability checks remain enabled.
 * The resume thunk implements the native generator ABI, not Python lowering. */
#include "py_runtime.h"
#include <stdint.h>
#include <stdio.h>

extern int64_t pcc_gc_deferred_free_begin(int64_t);
extern int64_t pcc_thread_start(void **, void *(*)(void *), void *);
extern int64_t pcc_thread_join(void *, void **);
extern int64_t pcc_gc_deferred_free_close(void);
extern void pcc_gc_scalar_sweep_seal(void);
extern int64_t pcc_gc_scalar_sweep_counter(int64_t);
extern int64_t pcc_gc_scalar_sweep_try_enqueue(void *, void *);
extern int64_t pcc_gc_scalar_sweep_owns(void *);
extern int64_t pcc_gc_tracing_sweep_unreachable(int64_t);
extern int64_t pcc_gc_cms_stop_worker(void);
extern int64_t pcc_platform_sleep_ns(int64_t);
extern int64_t pcc_thread_world_stop_epoch_held(void);
extern int64_t pcc_thread_visit_conservative_words(void (*)(void *, void *), void *);
extern void *pcc_gc_object_index_find(void *);
extern void *py_gc_index_find(void *);
extern void *pcc_gc_scalar_sweep_head;
extern int64_t pcc_gc_tracing_finish_stop_epoch;
extern int64_t pcc_gc_user_finalizer_runs;
extern int32_t py_gc_enabled, py_gc_tracked_count;
extern int32_t pcc_gc_tracking_flag_mode, pcc_gc_tracking_materialized;

#define C(n) pcc_gc_scalar_sweep_counter(n)
#define CHECK(x, n) do { if (!(x)) { if (!first_failure) first_failure = n; return n; } } while (0)
#define MANY 777
#define BOUNDED 12
#define GEN_INLINE 4194304
#define CANDIDATE 1024
#define WHITE 8
#define TRACKED 2
#define GEN_FRAME 24
#define GEN_SEND 48
#define NODE_OWNER 32
#define NODE_NEXT 16
#define NODE_TAG 64
static PyObject *generators[MANY], *frames[MANY], *children[MANY], *sends[MANY];
static void *object_nodes[MANY], *tracking_nodes[MANY];
static void *frame_nodes[MANY], *child_nodes[MANY], *send_nodes[MANY];
static int observed[MANY];
static PyObject *sentinel, *rescued, *weakref_object, *callback_target;
static void *rescue_handle;
static int mode, callback_status, first_failure, setup_status, stage_status, lifecycle_stage;
static int64_t producer, admitted, observed_gen, children_gone, public_gen_gone;
static int64_t bounded_calls, rejected, close_events, weak_events, event_order;
static int64_t finalizer_delta, tracking_checks, tracked_delta, pending_preserved;
static int64_t sentinel_checks, resurrection_checks, tracked_before;
static int64_t snapshot_words, snapshot_hits[4], snapshot_pins[4], snapshot_flags[4];
static int64_t actual_reclaimed, diagnostic_pending;
static int64_t retained_gen, public_collections, initial_finalizers, finalizer_total;
static int64_t final_children_gone, first_epoch_admitted;
static int64_t first_children_gone, first_children_pending, first_children_dead_mask;
static int64_t observed_list;

static int64_t word(void *base, int offset) {
    return *(int64_t *)((char *)base + offset);
}
static void *pointer(void *base, int offset) {
    return *(void **)((char *)base + offset);
}
static void *node_for(PyObject *object) {
    pcc_py_gc_minor_graph_lock();
    void *node = pcc_gc_object_index_find(object);
    pcc_py_gc_minor_graph_unlock();
    return node;
}

/* Read the snapshot made by the production recheck, without refreshing it.
 * These diagnostics run only AFTER the first sweep returned. Looking up the
 * target addresses here cannot introduce a root into that frozen snapshot. */
static void count_snapshot_word(void *value, void *unused) {
    (void)unused;
    uintptr_t raw = (uintptr_t)value;
    uintptr_t base = (uintptr_t)generators[0];
    if (raw >= base && raw < base + 56) snapshot_hits[0]++;
    base = (uintptr_t)frames[0];
    if (raw >= base && raw < base + 40) snapshot_hits[1]++;
    base = (uintptr_t)children[0];
    if (raw >= base && raw < base + 40) snapshot_hits[2]++;
    base = (uintptr_t)sends[0];
    if (raw >= base && raw < base + 40) snapshot_hits[3]++;
}

static void record_snapshot_object(PyObject *object, int index) {
    void *node = node_for(object);
    snapshot_pins[index] = node ? word(node, 56) : -1;
    snapshot_flags[index] = node ? ((PyObjectHeader *)object)->flags : -1;
}

static void diagnose_existing_snapshot(void) {
    snapshot_words = pcc_thread_visit_conservative_words(count_snapshot_word, 0);
    record_snapshot_object(generators[0], 0);
    record_snapshot_object(frames[0], 1);
    record_snapshot_object(children[0], 2);
    record_snapshot_object(sends[0], 3);
}

/* A normal yield, normal completion, and a GeneratorExit cleanup branch.
 * Inspect owned fields before cleanup to detect an early PASS1 or worker. */
static PyObject *resume_generator(PyObject *gen, PyObject *frame) {
    if (py_current_exception()) {
        if (frame != pointer(gen, GEN_FRAME) || py_gen_frame_len(frame) != 1)
            callback_status = 1;
        PyObject *child = py_gen_frame_get(frame, 0);
        if (!child || ((PyObjectHeader *)child)->type_tag != PY_TYPE_LIST)
            callback_status = 2;
        py_decref(child);
        close_events++;
        event_order = event_order * 10 + 2;
        if (mode == 6) pcc_gc_store_root(&rescued, gen);
        py_gen_set_done(gen);
        /* Preserve the actual injected GeneratorExit for py_gen_close. */
        return 0;
    }
    if (py_gen_state(gen) == 0) {
        py_gen_set_state(gen, 1);
        py_incref(py_None);
        return py_None;
    }
    return py_gen_finish(gen, py_None);
}

static PyObject *weak_callback(PyObject *captures, PyObject *args) {
    (void)captures;
    PyObject *ref = py_tuple_get(args, 0);
    PyObject *target = py_weakref_call(ref);
    if (target != py_None || close_events != 0) callback_status = 3;
    py_decref(target);
    py_decref(ref);
    weak_events++;
    event_order = event_order * 10 + 1;
    if (mode == 7) pcc_gc_store_root(&rescued, callback_target);
    py_incref(py_None);
    return py_None;
}

static PyObject *make_generator(int index) {
    PyObject *frame = py_gen_frame_new(1);
    PyObject *child = py_list_new(0);
    PyObject *send = py_list_new(0);
    if (!frame || !child || !send) return 0;
    py_gen_frame_set(frame, 0, child);
    PyObject *gen = py_gen_new((void *)resume_generator, frame);
    if (!gen) return 0;
    pcc_gc_store_ptr(gen, (PyObject **)((char *)gen + GEN_SEND), send);
    if (index >= 0) {
        generators[index] = gen;
        frames[index] = frame;
        children[index] = child;
        sends[index] = send;
    }
    py_decref(frame);
    py_decref(child);
    py_decref(send);
    return gen;
}

static int check_sentinel(void) {
    CHECK(((PyObjectHeader *)sentinel)->type_tag == PY_TYPE_GEN, 80);
    CHECK((((PyObjectHeader *)sentinel)->flags & GEN_INLINE) == 0, 81);
    PyObject *frame = pointer(sentinel, GEN_FRAME);
    PyObject *send = pointer(sentinel, GEN_SEND);
    CHECK(frame && send && py_gen_frame_len(frame) == 1, 82);
    CHECK(((PyObjectHeader *)send)->type_tag == PY_TYPE_LIST, 83);
    PyObject *child = py_gen_frame_get(frame, 0);
    CHECK(child && ((PyObjectHeader *)child)->type_tag == PY_TYPE_LIST, 84);
    py_decref(child);
    if (pcc_gc_backend() != 0) CHECK(node_for(sentinel) != 0, 85);
    sentinel_checks++;
    return 0;
}

static int drain_worker(void) {
    int rounds = 0;
    while ((C(5) != admitted || C(0) != 0) && rounds++ < 2000) {
        pcc_thread_safepoint();
        pcc_platform_sleep_ns(1000000);
    }
    CHECK(C(3) == admitted && C(4) == admitted && C(5) == admitted, 86);
    CHECK(C(6) == admitted && C(7) == 0, 87);
    CHECK(C(0) == 0 && C(1) == 0 && C(2) == 0 && C(9) == 0, 88);
    if (admitted) CHECK(C(8) > 0 && C(8) != producer, 89);
    return 0;
}

static int finish_worker(void) {
    CHECK(drain_worker() == 0, 102);
    CHECK(pcc_gc_cms_stop_worker() == 0, 90);
    /* Stopping again must reject without consuming any lifetime twice. */
    CHECK(pcc_gc_cms_stop_worker() == -1, 91);
    CHECK(C(4) == admitted && C(5) == admitted, 92);
    CHECK(pcc_gc_telemetry(PCC_GC_COUNTER_ALLOCATOR_DOUBLE_FREES) == 0, 93);
    return check_sentinel();
}

static int make_public_garbage(void) {
    for (int i = 0; i < MANY; i++) {
        PyObject *gen = make_generator(i);
        CHECK(gen != 0, 10);
        CHECK(((PyObjectHeader *)gen)->type_tag == PY_TYPE_GEN, 11);
        CHECK((((PyObjectHeader *)gen)->flags & GEN_INLINE) == 0, 12);
        py_decref(gen);
    }
    return 0;
}

static int public_collect(void) {
    /* No candidate/mark mutation, direct enqueue, or surrounding stop here. */
    CHECK(make_public_garbage() == 0, 13);
    (void)pcc_gc_collect(0);
    admitted = C(3);
    if (pcc_gc_backend() == 1 || pcc_gc_backend() == 2) CHECK(admitted > 512, 14);
    else CHECK(admitted == 0, 15);
    CHECK(finish_worker() == 0, 16);
    if (pcc_gc_backend() == 1 || pcc_gc_backend() == 2) {
        for (int i = 0; i < MANY; i++) {
            if (!node_for(generators[i])) public_gen_gone++;
        }
        CHECK(public_gen_gone > 512, 17);
    }
    /* Total counters include any other eligible deaths. Their type is not
     * guessed here; the controlled test below observes actual GEN nodes. */
    return 0;
}

static void set_dead_verdict(PyObject *obj) {
    PyObjectHeader *header = (PyObjectHeader *)obj;
    header->flags = (header->flags & ~(16384 | 16 | 32)) | CANDIDATE | WHITE;
}

/* Call only after establishing the object's live ObjectNode lifetime.
 * TRACKED is the membership contract. Flag mode has no permanent GCNode;
 * temporary nodes belong only to an active stopped-world materialized view.
 * Node mode retains the original identity oracle across every lifecycle. */
static int tracking_matches(PyObject *object, void *expected_node, int compare_identity) {
    if (!(((PyObjectHeader *)object)->flags & TRACKED)) return 0;
    void *node = py_gc_index_find(object);
    if (pcc_gc_tracking_flag_mode) {
        if (!pcc_gc_tracking_materialized) return node == 0;
        if (!pcc_thread_owns_stopped_world()) return 0;
    } else if (compare_identity && node != expected_node) {
        return 0;
    }
    return node && pointer(node, 0) == object;
}

static int require_live_target(PyObject *object, void *expected_node, int tag) {
    void *node = node_for(object);
    CHECK(node && node == expected_node, 162);
    CHECK(word(node, NODE_OWNER) == 0 && tracking_matches(object, 0, 0), 163);
    /* Membership precedes header reads, including regression failures. */
    CHECK(((PyObjectHeader *)object)->type_tag == tag, 164);
    CHECK((((PyObjectHeader *)object)->flags & (CANDIDATE | 524288 | 32 | TRACKED)) ==
          (32 | TRACKED), 165);
    return 0;
}

static int require_pending_list(PyObject *object, void *expected_node) {
    void *node = node_for(object);
    /* Establish this retained lifetime before reading any LIST payload. */
    CHECK(node && node == expected_node && word(node, NODE_OWNER) == -C(11), 178);
    CHECK(word(node, NODE_TAG) == PY_TYPE_LIST && pcc_gc_scalar_sweep_owns(object), 179);
    CHECK(((PyObjectHeader *)object)->type_tag == PY_TYPE_LIST, 180);
    CHECK((((PyObjectHeader *)object)->flags & (CANDIDATE | WHITE | 4 | 524288 | TRACKED)) ==
          (CANDIDATE | WHITE | 4 | 524288), 181);
    CHECK(word(object, 16) == 0 && word(object, 24) == 0 && pointer(object, 32) == 0, 182);
    CHECK(py_gc_index_find(object) == 0, 183);
    observed_list++;
    return 0;
}

static int record_first_child(PyObject *object, void *expected_node, int bit) {
    if (!node_for(object)) {
        CHECK(py_gc_index_find(object) == 0, 166);
        first_children_gone++;
        first_children_dead_mask |= bit;
    } else if (word(node_for(object), NODE_OWNER) < 0) {
        CHECK(require_pending_list(object, expected_node) == 0, 184);
        first_children_pending++;
        first_children_dead_mask |= bit;
    } else {
        CHECK(require_live_target(object, expected_node, PY_TYPE_LIST) == 0, 167);
    }
    return 0;
}

static int observe_building(int count) {
    CHECK(C(0) == 1 && C(4) == 0 && C(5) == 0, 30);
    for (int i = 0; i < count; i++) {
        void *node = pcc_gc_object_index_find(generators[i]);
        if (!node || word(node, NODE_OWNER) >= 0 || observed[i]) continue;
        CHECK(node == object_nodes[i] && word(node, NODE_OWNER) == -C(11), 31);
        CHECK(word(node, NODE_TAG) == PY_TYPE_GEN, 32);
        CHECK(((PyObjectHeader *)generators[i])->type_tag == PY_TYPE_GEN, 33);
        CHECK((((PyObjectHeader *)generators[i])->flags & (GEN_INLINE | TRACKED)) == 0, 34);
        CHECK(!pointer(generators[i], GEN_FRAME) && !pointer(generators[i], GEN_SEND), 35);
        CHECK(py_gc_index_find(generators[i]) == 0, 36);
        CHECK(pcc_gc_scalar_sweep_owns(generators[i]) == 1, 37);
        int32_t before = py_gc_tracked_count;
        py_gc_untrack(generators[i]);
        CHECK(py_gc_tracked_count == before && py_gc_index_find(generators[i]) == 0, 38);
        CHECK(pcc_gc_object_index_find(generators[i]) == node, 39);
        tracking_checks++;
        observed[i] = 1;
        observed_gen++;
    }
    return 0;
}

static int prepare_controlled(int count) {
    for (int i = 0; i < count; i++) {
        PyObject *gen = make_generator(i);
        CHECK(gen != 0 && (((PyObjectHeader *)gen)->flags & GEN_INLINE) == 0, 22);
        if (mode >= 3) {
            PyObject *value = py_gen_next(gen);
            CHECK(value == py_None, 23);
            py_decref(value);
        }
        if (mode == 3) {
            CHECK(py_gen_next(gen) == 0 && py_current_exception() != 0, 24);
            py_clear_exception();
            CHECK(py_gen_is_done(gen) == 1, 25);
        } else if (mode == 4) {
            py_gen_finalize(gen);
            CHECK(py_gen_is_done(gen) == 1 && (((PyObjectHeader *)gen)->flags & 4), 26);
        }
        /* next/close replace pending send. Install a real owned send child
         * afterward so PASS1 still has both ordinary GEN edges to clear. */
        pcc_gc_store_ptr(gen, (PyObject **)((char *)gen + GEN_SEND), sends[i]);
        object_nodes[i] = node_for(gen);
        tracking_nodes[i] = py_gc_index_find(gen);
        CHECK(object_nodes[i] && tracking_matches(gen, 0, 0), 27);
        CHECK(tracking_matches(gen, tracking_nodes[i], 1), 28);
        frame_nodes[i] = node_for(frames[i]);
        child_nodes[i] = node_for(children[i]);
        send_nodes[i] = node_for(sends[i]);
        CHECK(frame_nodes[i] && child_nodes[i] && send_nodes[i], 168);
    }
    close_events = 0;
    event_order = 0;
    if (mode == 6 || mode == 7) {
        PyObject *captures = py_tuple_new(0);
        PyObject *callback = py_func_new((void *)weak_callback, captures);
        py_decref(captures);
        CHECK(callback != 0, 29);
        weakref_object = py_weakref_new(generators[0], callback);
        CHECK(weakref_object != 0, 40);
        pcc_gc_pin(weakref_object);
        py_decref(callback);
        callback_target = generators[0];
    }
    return 0;
}

static int install_controlled_verdict(int count) {
    CHECK(pcc_stop_the_world() == 0, 96);
    CHECK(pcc_gc_deferred_free_begin(0) == 1 && C(0) == 1, 97);
    CHECK(pcc_thread_owns_stopped_world() && C(11) > 0, 100);
    CHECK(C(1) == 0 && C(2) == 0 && C(3) == 0 && C(4) == 0 && C(5) == 0, 101);
    for (int i = 0; i < count; i++) {
        set_dead_verdict(generators[i]);
        set_dead_verdict(frames[i]);
        set_dead_verdict(children[i]);
        set_dead_verdict(sends[i]);
        /* With both owned children intact, rejection must not untrack or
         * claim this object. Production PASS1, not this test, clears it. */
        int flags = ((PyObjectHeader *)generators[i])->flags;
        CHECK(pcc_gc_scalar_sweep_try_enqueue(generators[i], object_nodes[i]) == 0, 44);
        CHECK(((PyObjectHeader *)generators[i])->flags == flags, 45);
        CHECK(tracking_matches(generators[i], tracking_nodes[i], 1), 46);
        CHECK(pcc_gc_object_index_find(generators[i]) == object_nodes[i], 47);
        CHECK(word(object_nodes[i], NODE_OWNER) == 0 && C(3) == 0, 48);
        rejected++;
    }
    /* No sweep or claim occurs in this scope. End the empty reservation
     * before the creator exits, so the collecting thread owns a fresh one. */
    pcc_gc_scalar_sweep_seal();
    CHECK(C(0) == 0 && C(1) == 0 && C(2) == 0 && C(3) == 0 && C(12) == 0, 98);
    CHECK(pcc_resume_world() == 0, 99);
    pcc_gc_deferred_free_close();
    return 0;
}

static void *create_controlled_garbage(void *unused) {
    (void)unused;
    int count = mode >= 5 ? 1 : BOUNDED;
    setup_status = prepare_controlled(count);
    if (!setup_status) setup_status = install_controlled_verdict(count);
    if (pcc_thread_owns_stopped_world()) pcc_resume_world();
    return 0;
}

static int controlled_sweep(void) {
    CHECK(pcc_gc_backend() == 1 || pcc_gc_backend() == 2, 20);
    int count = mode >= 5 ? 1 : BOUNDED;
    if (!rescue_handle) rescue_handle = pcc_gc_scheduler_root_register_handle(&rescued);
    CHECK(rescue_handle != 0, 21);
    /* Construction, candidate injection, and rejection probes all finish on
     * a joined thread. The collecting thread does not read any target pointer
     * until AFTER its first production sweep and real reachability recheck.
     * Merely delaying those reads until STW is insufficient: the recheck
     * refreshes the owner's then-current conservative snapshot. */
    void *creator = 0;
    CHECK(pcc_thread_start(&creator, create_controlled_garbage, 0) == 0, 94);
    CHECK(pcc_thread_join(creator, 0) == 0 && setup_status == 0, 95);
    PyObject *pending = 0;
    if (mode == 5) {
        pending = py_exc_new(PY_EXC_TYPEERROR, "pending before generator sweep");
        CHECK(pending != 0, 41);
        py_raise_owned(pending);
    }
    tracked_before = py_gc_tracked_count;
    int64_t finalizers_before = pcc_gc_user_finalizer_runs;
    initial_finalizers = finalizers_before;
    CHECK(pcc_stop_the_world() == 0, 42);
    CHECK(pcc_gc_deferred_free_begin(0) == 1 && C(0) == 1, 43);
    /* Controlled completed-mark verdict for this stop, not a real mark.
     * This skips only the pre-PASS0 refresh; the production weakref and
     * post-finalizer refreshes remain unchanged and inspect real roots. */
    pcc_gc_tracing_finish_stop_epoch = pcc_thread_world_stop_epoch_held();
    int64_t reclaimed = 0;
    for (int step = 0; step < count * 4 + 1; step++) {
        int64_t result = pcc_gc_tracing_sweep_unreachable(1);
        CHECK(result >= 0 && result <= 1, 49);
        bounded_calls++;
        reclaimed += result;
        actual_reclaimed = reclaimed;
        if ((mode == 5 || mode == 6) && step == 0) {
            diagnose_existing_snapshot();
            diagnostic_pending = py_current_exception() == pending;
        }
        CHECK(observe_building(count) == 0, 50);
        if (result == 0) break;
    }
    finalizer_delta = pcc_gc_user_finalizer_runs - finalizers_before;
    CHECK(callback_status == 0, 51);
    if (mode == 5 || mode == 6) {
        /* Each child has its own lifetime verdict. A conservative root can
         * retain a child independently even after its GEN has been claimed. */
        CHECK(record_first_child(frames[0], frame_nodes[0], 1) == 0, 169);
        CHECK(record_first_child(children[0], child_nodes[0], 2) == 0, 170);
        CHECK(record_first_child(sends[0], send_nodes[0], 4) == 0, 171);
        CHECK(reclaimed == observed_gen + first_children_gone + first_children_pending, 172);
        children_gone = first_children_gone;
    }
    if (mode == 5 && observed_gen == 1) {
        /* A platform with no residual conservative GEN word may retire it
         * immediately. Prove the actual claim, not merely a queue count. */
        CHECK(C(3) == 1 + observed_list && tracking_checks == 1, 152);
        CHECK(node_for(generators[0]) == object_nodes[0], 153);
        CHECK(word(object_nodes[0], NODE_OWNER) == -C(11), 154);
        CHECK(((PyObjectHeader *)generators[0])->type_tag == PY_TYPE_GEN, 155);
        CHECK((((PyObjectHeader *)generators[0])->flags & (GEN_INLINE | TRACKED)) == 0, 156);
        CHECK(!pointer(generators[0], GEN_FRAME) && !pointer(generators[0], GEN_SEND), 157);
        CHECK(py_gc_index_find(generators[0]) == 0 && pcc_gc_scalar_sweep_owns(generators[0]) == 1, 158);
        CHECK(finalizer_delta == 1 && close_events == 1 && diagnostic_pending == 1, 161);
        first_epoch_admitted = 1;
    } else if (mode == 5 || mode == 6) {
        /* A real conservative root is a live verdict, not a failed queue
         * admission. The old one-round oracle incorrectly demanded freeing
         * this GEN while the refreshed owner snapshot still held it. */
        CHECK(observed_gen == 0 && C(3) == observed_list, 103);
        /* Establish live indexed lifetimes before touching any payload.
         * A synchronous-free regression must fail here, not read freed GEN
         * or list bytes merely because no asynchronous claim was counted. */
        CHECK(require_live_target(generators[0], object_nodes[0], PY_TYPE_GEN) == 0, 107);
        CHECK(tracking_matches(generators[0], tracking_nodes[0], 1), 108);
        CHECK((first_children_dead_mask & 3) == 0, 149);
        CHECK(pointer(generators[0], GEN_FRAME) == frames[0], 104);
        CHECK(py_gen_frame_len(frames[0]) == 1, 105);
        PyObject *child = py_gen_frame_get(frames[0], 0);
        CHECK(child == children[0], 106);
        py_decref(child);
        CHECK(close_events == finalizer_delta && close_events <= 1, 111);
        CHECK(weak_events == (mode == 6 ? 1 : 0), 112);
        if (mode == 5 || close_events == 0) {
            CHECK(snapshot_hits[0] > 0 && (snapshot_pins[0] & 1), 113);
            CHECK(rescued == 0, 114);
        } else {
            CHECK(rescued == generators[0], 115);
        }
        if (close_events) {
            CHECK(py_gen_is_done(generators[0]) == 1, 116);
            CHECK(pointer(generators[0], GEN_SEND) == py_None, 117);
        } else {
            CHECK(mode == 6 && first_children_dead_mask == 0, 119);
            CHECK(pointer(generators[0], GEN_SEND) == sends[0], 120);
        }
        if (mode == 5) CHECK(close_events == 1 && diagnostic_pending == 1, 121);
        else CHECK(event_order == (close_events ? 12 : 1), 122);
        retained_gen++;
    } else if (mode == 7) {
        CHECK(rescued == generators[0] && reclaimed == 0 && observed_gen == 0, 52);
        CHECK(object_nodes[0] && node_for(rescued) == object_nodes[0], 177);
        CHECK(pointer(rescued, GEN_FRAME) == frames[0], 53);
        CHECK(py_gen_frame_len(frames[0]) == 1 && tracking_matches(rescued, tracking_nodes[0], 1), 54);
        CHECK(word(object_nodes[0], NODE_OWNER) == 0, 55);
        CHECK((((PyObjectHeader *)rescued)->flags & CANDIDATE) == 0, 56);
        CHECK(weak_events == 1 && event_order == 1, 57);
        CHECK(close_events == 0 && finalizer_delta == 0, 58);
        resurrection_checks++;
    } else {
        CHECK(reclaimed == count * 4 && observed_gen == count, 60);
        CHECK(finalizer_delta == 0 && close_events == 0, 61);
        for (int i = 0; i < count; i++) {
            CHECK(require_pending_list(frames[i], frame_nodes[i]) == 0, 63);
            CHECK(require_pending_list(children[i], child_nodes[i]) == 0, 185);
            CHECK(require_pending_list(sends[i], send_nodes[i]) == 0, 186);
            CHECK(!py_gc_index_find(frames[i]) && !py_gc_index_find(children[i]) && !py_gc_index_find(sends[i]), 64);
        }
        tracked_delta = tracked_before - py_gc_tracked_count;
        if (mode == 2) CHECK(tracked_delta == count * 4, 65);
    }
    if (pending) {
        CHECK(py_current_exception() == pending, 66);
        pending_preserved++;
        py_clear_exception();
    }
    admitted = C(3);
    /* Reconcile the independently observed target GEN and zero-shell LIST
     * claims; do not assign unrelated scalar/exception deaths to either. */
    CHECK(admitted == observed_gen + observed_list, 67);
    pcc_gc_scalar_sweep_seal();
    CHECK(C(12) == admitted && C(4) == 0 && C(5) == 0, 68);
    CHECK(pcc_resume_world() == 0, 69);
    pcc_gc_deferred_free_close();
    /* These modes must discard the entire observer stack before subsequent
     * real marking. Keep the CMS worker and the resurrection root alive. */
    if (mode == 5 || mode == 6) {
        CHECK(drain_worker() == 0, 123);
        if (first_children_dead_mask & 1) CHECK(node_for(frames[0]) == 0, 187);
        if (first_children_dead_mask & 2) CHECK(node_for(children[0]) == 0, 188);
        if (first_children_dead_mask & 4) CHECK(node_for(sends[0]) == 0, 189);
        return 0;
    }
    CHECK(finish_worker() == 0, 70);
    for (int i = 0; i < count; i++) {
        if (observed[i]) CHECK(node_for(generators[i]) == 0 && py_gc_index_find(generators[i]) == 0, 71);
        if (mode < 5) {
            CHECK(!node_for(frames[i]) && !node_for(children[i]) && !node_for(sends[i]), 190);
            children_gone += 3;
        }
    }
    if (rescued) {
        CHECK(mode == 7 && py_gen_is_done(rescued) == 0, 74);
        PyObject *closed = py_gen_close(rescued);
        CHECK(closed == py_None && close_events == 1, 75);
        py_decref(closed);
    }
    pcc_gc_store_root(&rescued, 0);
    pcc_gc_scheduler_root_unregister_handle(rescue_handle);
    rescue_handle = 0;
    return 0;
}

static void *first_lifecycle_stage(void *unused) {
    (void)unused;
    producer = pcc_current_thread_id();
    stage_status = controlled_sweep();
    if (pcc_thread_owns_stopped_world()) pcc_resume_world();
    return 0;
}

static int check_public_lifecycle(int resurrection_stage) {
    /* No target pointer is loaded on this fresh thread before real marking.
     * All checks below occur after collection and leave with this thread. */
    producer = pcc_current_thread_id();
    (void)pcc_gc_collect(0);
    public_collections++;
    admitted = C(3);
    CHECK(drain_worker() == 0, 124);
    finalizer_total = pcc_gc_user_finalizer_runs - initial_finalizers;
    CHECK(callback_status == 0 && close_events <= 1 && finalizer_total <= 1, 125);
    if (resurrection_stage) {
        CHECK(weak_events == 1, 126);
        if (!rescued) {
            CHECK(close_events == 0 && finalizer_total == 0, 127);
            return 0;
        }
        CHECK(rescued == generators[0] && close_events == 1 && finalizer_total == 1, 128);
        CHECK(require_live_target(rescued, object_nodes[0], PY_TYPE_GEN) == 0, 132);
        CHECK(tracking_matches(rescued, tracking_nodes[0], 1), 133);
        CHECK(require_live_target(frames[0], frame_nodes[0], PY_TYPE_LIST) == 0, 173);
        CHECK(require_live_target(children[0], child_nodes[0], PY_TYPE_LIST) == 0, 174);
        CHECK(event_order == 12 && py_gen_is_done(rescued) == 1, 129);
        CHECK(pointer(rescued, GEN_FRAME) == frames[0] && py_gen_frame_len(frames[0]) == 1, 130);
        PyObject *child = py_gen_frame_get(frames[0], 0);
        CHECK(child == children[0], 131);
        py_decref(child);
        CHECK(pcc_gc_scalar_sweep_owns(rescued) == 0, 134);
        CHECK(pointer(rescued, GEN_SEND) == py_None, 135);
        if (node_for(sends[0])) {
            CHECK(require_live_target(sends[0], send_nodes[0], PY_TYPE_LIST) == 0, 175);
        } else {
            CHECK(py_gc_index_find(sends[0]) == 0, 176);
        }
        resurrection_checks++;
        /* Drop the intentional root here, then retire this whole observer
         * frame by joining the thread. Do not retain target addresses on a
         * parked parent stack during the final public collection. */
        pcc_gc_store_root(&rescued, 0);
        return 0;
    }
    CHECK(finalizer_total == 1 && close_events == 1, 136);
    public_gen_gone = node_for(generators[0]) == 0;
    final_children_gone = (node_for(frames[0]) == 0) +
                          (node_for(children[0]) == 0) + (node_for(sends[0]) == 0);
    if (public_gen_gone) CHECK(py_gc_index_find(generators[0]) == 0, 137);
    if (final_children_gone == 3) {
        CHECK(!py_gc_index_find(frames[0]) && !py_gc_index_find(children[0]) && !py_gc_index_find(sends[0]), 138);
    }
    return 0;
}

static void *public_resurrection_stage(void *unused) {
    (void)unused;
    stage_status = check_public_lifecycle(1);
    return 0;
}

static void *public_retirement_stage(void *unused) {
    (void)unused;
    stage_status = check_public_lifecycle(0);
    return 0;
}

static int join_lifecycle_stage(void *(*entry)(void *)) {
    void *thread = 0;
    stage_status = 0;
    CHECK(pcc_thread_start(&thread, entry, 0) == 0, 139);
    CHECK(pcc_thread_join(thread, 0) == 0 && stage_status == 0, 140);
    return 0;
}

static int lifecycle_with_public_followup(void) {
    rescue_handle = pcc_gc_scheduler_root_register_handle(&rescued);
    CHECK(rescue_handle != 0, 141);
    lifecycle_stage = 1;
    CHECK(join_lifecycle_stage(first_lifecycle_stage) == 0, 142);
    if (mode == 6) {
        lifecycle_stage = 2;
        for (int round = 0; round < 4 && resurrection_checks == 0; round++) {
            CHECK(join_lifecycle_stage(public_resurrection_stage) == 0, 143);
        }
        CHECK(resurrection_checks == 1, 144);
    }
    lifecycle_stage = 3;
    for (int round = 0; round < 4 && (public_gen_gone != 1 || final_children_gone != 3); round++) {
        CHECK(join_lifecycle_stage(public_retirement_stage) == 0, 145);
    }
    CHECK(public_gen_gone == 1 && final_children_gone == 3, 146);
    CHECK(finalizer_total == 1 && close_events == 1 && admitted > 0, 147);
    children_gone = final_children_gone;
    lifecycle_stage = 4;
    CHECK(finish_worker() == 0, 148);
    pcc_gc_scheduler_root_unregister_handle(rescue_handle);
    rescue_handle = 0;
    return 0;
}

static int run(void) {
    producer = pcc_current_thread_id();
    CHECK(producer > 0 && pcc_threads_enabled() == 1, 1);
    CHECK(pcc_refcount_strategy() == PCC_REFCOUNT_STRATEGY_ATOMIC, 2);
    py_gc_enabled = 0;
    sentinel = make_generator(-1);
    CHECK(sentinel != 0, 3);
    pcc_gc_pin(sentinel);
    if (pcc_gc_backend() != 0) {
        int rounds = 0;
        while (pcc_thread_live_count() < 2 && rounds++ < 2000) {
            pcc_thread_safepoint();
            pcc_platform_sleep_ns(1000000);
        }
        CHECK(pcc_thread_live_count() == 2, 4);
    }
    CHECK(check_sentinel() == 0, 5);
    if (mode == 1) return public_collect();
    if (mode == 5 || mode == 6) return lifecycle_with_public_followup();
    return controlled_sweep();
}

int main(int argc, char **argv) {
    if (argc != 2) return 98;
    mode = argv[1][0] - '0';
    if (mode < 1 || mode > 7 || argv[1][1]) return 99;
    int status = run();
    if (pcc_thread_owns_stopped_world()) pcc_resume_world();
    printf("cleared-gen-sweep backend=%d mode=%d status=%d first_failure=%d tracking_flags=%d tracking_view=%d producer=%lld worker=%lld "
           "admitted=%lld observed_gen=%lld children_gone=%lld public_gen_gone=%lld "
           "retired=%lld released=%lld worker_retired=%lld local=%lld pending=%lld "
           "state=%lld quarantine=%lld chunks=%lld peak_objects=%lld peak_bytes=%lld "
           "bounded_calls=%lld rejected=%lld finalizers=%lld closes=%lld weak=%lld "
           "tracking_checks=%lld tracked_delta=%lld pending_preserved=%lld "
           "sentinel_checks=%lld resurrection_checks=%lld double_frees=%lld actual_reclaimed=%lld "
           "retained_gen=%lld public_collections=%lld finalizer_total=%lld "
           "lifecycle_stage=%d final_children_gone=%lld event_order=%lld first_epoch_admitted=%lld "
           "first_children_gone=%lld first_children_dead_mask=%lld "
           "first_children_pending=%lld observed_list=%lld",
           pcc_gc_backend(), mode, status, first_failure,
           pcc_gc_tracking_flag_mode, pcc_gc_tracking_materialized,
           (long long)producer, (long long)C(8),
           (long long)admitted, (long long)observed_gen, (long long)children_gone,
           (long long)public_gen_gone, (long long)C(4), (long long)C(5),
           (long long)C(6), (long long)C(7), (long long)C(1), (long long)C(0),
           (long long)C(9), (long long)C(13), (long long)C(14), (long long)C(15),
           (long long)bounded_calls, (long long)rejected, (long long)finalizer_delta,
           (long long)close_events, (long long)weak_events, (long long)tracking_checks,
           (long long)tracked_delta, (long long)pending_preserved,
           (long long)sentinel_checks, (long long)resurrection_checks,
           (long long)pcc_gc_telemetry(PCC_GC_COUNTER_ALLOCATOR_DOUBLE_FREES),
           (long long)actual_reclaimed, (long long)retained_gen,
           (long long)public_collections, (long long)finalizer_total,
           lifecycle_stage, (long long)final_children_gone, (long long)event_order,
           (long long)first_epoch_admitted,
           (long long)first_children_gone, (long long)first_children_dead_mask,
           (long long)first_children_pending, (long long)observed_list);
    if (snapshot_words > 0) {
        /* Omit uncaptured diagnostics. A -1 pin/flags pair means the
         * object's index was already removed, so its bytes were not read. */
        printf(" snapshot_words=%lld diagnostic_pending=%lld "
               "root_gen=%lld root_frame=%lld root_child=%lld root_send=%lld "
               "pin_gen=%lld flags_gen=%lld pin_frame=%lld flags_frame=%lld "
               "pin_child=%lld flags_child=%lld pin_send=%lld flags_send=%lld",
               (long long)snapshot_words, (long long)diagnostic_pending,
               (long long)snapshot_hits[0], (long long)snapshot_hits[1],
               (long long)snapshot_hits[2], (long long)snapshot_hits[3],
               (long long)snapshot_pins[0], (long long)snapshot_flags[0],
               (long long)snapshot_pins[1], (long long)snapshot_flags[1],
               (long long)snapshot_pins[2], (long long)snapshot_flags[2],
               (long long)snapshot_pins[3], (long long)snapshot_flags[3]);
    }
    printf("\n");
    return status;
}
