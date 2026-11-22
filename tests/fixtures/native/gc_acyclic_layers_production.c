/* Drives the real sweep from a sealed mark, not a planner-only entry. */
#include <Python.h>
#include "py_internal.h"
#include <stdint.h>
#include <stdio.h>
#include <string.h>

extern void *pcc_gc_object_index_find(PyObject *);
extern void pcc_gc_set_mark_color(PyObject *, int64_t);
extern PyObject *py_list_new(int64_t);
extern void py_list_append(PyObject *, PyObject *);
extern void pcc_gc_tracing_candidates_reset(void);
extern void pcc_gc_tracing_candidates_note(void *);
extern void pcc_gc_tracing_candidates_seal(int64_t);
extern int64_t pcc_gc_tracing_sweep_unreachable(int64_t);
extern int64_t pcc_thread_world_stop_epoch_held(void);
extern int64_t pcc_gc_tracing_finish_commits, pcc_gc_tracing_finish_stop_epoch;
extern int64_t pcc_gc_metric_layer_weak_registry_scans;
extern int PyDict_SetItem(PyObject *, PyObject *, PyObject *);
extern int PyList_Append(PyObject *, PyObject *);
extern int PyObject_SetAttrString(PyObject *, const char *, PyObject *);
extern void pcc_gc_cms_stop_worker(void);
extern void pcc_gc_begin_explicit_tracing_collect(void);
extern void pcc_gc_end_explicit_tracing_collect(void);
extern int64_t pcc_thread_start(void **, void *(*)(void *), void *);
extern int64_t pcc_thread_join(void *, void **);

enum { CLASS_A, CLASS_B, CLASS_TAIL, WEAK_A, WEAK_B, WEAK_NEW, HOLD, EXT_TYPE, WIDE_LOW, ROOTS };
static PyObject *roots[ROOTS];
enum { WIDE_MIN = 63, WIDE_MAX = 65 };
static void *nodes[WIDE_MAX];
static PyObject *wide_low;
static int wide_size;
static int rescue, withdrawn, mixed, silent, finalizer_only, late_new, idle_worker, error;
static int worker_started, worker_done;
static int peer_mode, peer_request, peer_read, peer_live_after;
static void *peer_thread;
static PyObject *volatile *caller_hold;
static int events[6], event_count;
static int ext_edge_visits;

struct ExtensionHolder { PyObject_HEAD PyObject *edge; };

static int traverse_extension(PyObject *self, visitproc visit, void *arg) {
    struct ExtensionHolder *holder = (struct ExtensionHolder *)self;
    if (holder->edge) ++ext_edge_visits;
    Py_VISIT(holder->edge);
    return 0;
}

static PyType_Slot extension_slots[] = {
    {Py_tp_traverse, (void *)traverse_extension}, {0, NULL}
};
static PyType_Spec extension_spec = {
    "layered.ExtensionHolder", sizeof(struct ExtensionHolder), 0,
    Py_TPFLAGS_DEFAULT | Py_TPFLAGS_HAVE_GC, extension_slots
};

static PyObject *new_extension_holder(void) {
    PyObject *ty = PyType_FromSpec(&extension_spec);
    if (!ty) return NULL;
    pcc_gc_store_root(&roots[EXT_TYPE], ty);
    PyObject *obj = PyType_GenericAlloc((PyTypeObject *)ty, 0);
    if (!obj) return NULL;
    pcc_gc_publish_initialized(obj);
    return obj;
}

static void note(int event) {
    if (event_count >= 6 || pcc_thread_world_stop_epoch_held() >= 0) error = 5;
    else events[event_count++] = event;
}
static PyObject *weak_event(PyObject *captures, PyObject *args);
static PyObject *finish_a(PyObject *self) {
    (void)self;
    note(1);
    if (peer_mode) {
        __atomic_store_n(&peer_request, 1, __ATOMIC_RELEASE);
        int status;
        while (!(status = __atomic_load_n(&peer_read, __ATOMIC_ACQUIRE))) pcc_thread_safepoint();
        if (status < 0) error = 37;
        // Withdrawal exits the registered thread before the callback closes;
        // the next root cut must not contain its retired raw stack words.
        if (peer_mode != 1 && pcc_thread_join(peer_thread, NULL)) error = 38;
    }
    if (late_new) {
        PyObject *target = py_weakref_call(roots[WEAK_B]);
        PyObject *callback = py_func_new((void *)weak_event, NULL);
        PyObject *ref = target && target != py_None && callback ? py_weakref_new(target, callback) : NULL;
        if (!ref) error = 24;
        else pcc_gc_store_root(&roots[WEAK_NEW], ref);
        if (target) py_decref(target);
    }
    if (wide_low) pcc_gc_store_root(&roots[WIDE_LOW], wide_low);
    if (rescue || withdrawn) {
        PyObject *target = py_weakref_call(roots[WEAK_B]);
        if (!target || target == py_None) error = 6;
        else if (rescue == 2) *caller_hold = target;
        else if (rescue == 5) *caller_hold = (PyObject *)((char *)target + 1);
        else if (rescue == 4) pcc_gc_pin(target);
        else if (rescue == 6 || rescue == 8) py_dict_set(roots[HOLD], py_int_from_i64(7), target);
        else if (rescue == 7) py_instance_set_field((PyInstanceObject *)roots[HOLD], 0, target);
        else if (rescue == 9) py_list_set(roots[HOLD], 0, target);
        else if (rescue == 10) py_set_add(roots[HOLD], target);
        else if (rescue == 14) {
            if (PyDict_SetItem(roots[HOLD], py_int_from_i64(7), target) != 0) error = 31;
        } else if (rescue == 15) {
            if (PyList_Append(roots[HOLD], target) != 0) error = 32;
        } else if (rescue == 16) {
            if (PyObject_SetAttrString(roots[HOLD], "child", target) != 0) error = 33;
        } else if (rescue == 18) {
            PyObject *holder = *caller_hold;
            if (!holder) error = 35;
            else py_list_append(holder, target);
        } else if (rescue == 17) {
            struct ExtensionHolder *holder = (struct ExtensionHolder *)roots[HOLD];
            pcc_gc_store_ptr(roots[HOLD], &holder->edge, target);
        } else if (rescue >= 11 && rescue <= 13) {
            // The incoming value was installed in an unreachable source
            // before the mark. This callback adds no separate source shade.
            PyObject *source = py_instance_get_field((PyInstanceObject *)self, 0);
            if (!source || source == py_None) error = 27;
            else if (rescue == 11) py_dict_update(roots[HOLD], source);
            else if (rescue == 12) {
                if (py_list_set_slice(roots[HOLD], py_int_from_i64(0), py_int_from_i64(1),
                                      py_None, source) != 0) error = 28;
            } else py_set_update(roots[HOLD], source);
            if (source) py_decref(source);
        }
        else if (rescue == 3) py_list_append(roots[HOLD], target);
        else {
            pcc_gc_store_root(&roots[HOLD], target);
            if (withdrawn) pcc_gc_store_root(&roots[HOLD], NULL);
        }
        if (target) py_decref(target);
    }
    return py_int_from_i64(0);
}
static PyObject *finish_b(PyObject *self) {
    (void)self;
    note(3);
    return py_int_from_i64(0);
}
static PyObject *finish_tail(PyObject *self) {
    (void)self;
    note(7);
    return py_int_from_i64(0);
}
static PyObject *weak_event(PyObject *captures, PyObject *args) {
    (void)captures;
    PyObject *ref = py_tuple_get(args, 0);
    if (ref == roots[WEAK_A]) note(2);
    else if (ref == roots[WEAK_B]) note(4);
    else if (ref == roots[WEAK_NEW]) note(8);
    else error = 7;
    if (ref) py_decref(ref);
    return py_int_from_i64(0);
}

static void *seal_on_departing_thread(void *unused) {
    (void)unused;
    const char *fields[] = {"child", "tail"};
    if (pcc_current_thread_id() <= 0) { error = 8; return NULL; }
    PyClassObject *a_cls = py_class_new("LayerA", NULL, 0, fields, 1);
    int want_tail = mixed == 2 || rescue == 4;
    int bulk = rescue >= 11 && rescue <= 13;
    PyClassObject *b_cls = py_class_new("LayerB", NULL, 0, fields, want_tail ? 2 : 1);
    if (!a_cls || !b_cls) { error = 9; return NULL; }
    pcc_gc_store_root(&roots[CLASS_A], (PyObject *)a_cls);
    pcc_gc_store_root(&roots[CLASS_B], (PyObject *)b_cls);
    if (!silent) {
        py_class_add_method(a_cls, "__del__", (PyObject *)(uintptr_t)finish_a);
        py_class_add_method(b_cls, "__del__", (PyObject *)(uintptr_t)finish_b);
    }
    PyObject *a = py_instance_new(a_cls);
    PyObject *b = py_instance_new(b_cls);
    PyObject *callback = silent || finalizer_only ? NULL : py_func_new((void *)weak_event, NULL);
    if (!a || !b || (!silent && !finalizer_only && !callback)) { error = 10; return NULL; }
    if (rescue == 3 || rescue >= 6) {
        PyObject *holder = rescue == 3 || rescue == 9 || rescue == 12 || rescue == 15 || rescue == 18 ? py_list_new(0) :
                           rescue == 6 || rescue == 8 || rescue == 11 || rescue == 14 ? py_dict_new() :
                           rescue == 7 || rescue == 16 ? py_instance_new(a_cls) :
                           rescue == 17 ? new_extension_holder() : py_set_new();
        if (!holder) { error = 14; return NULL; }
        if (rescue == 18) *caller_hold = holder;
        else pcc_gc_store_root(&roots[HOLD], holder);
        if (rescue == 8) py_dict_set(holder, py_int_from_i64(7), py_None);
        if (rescue == 9 || rescue == 12) py_list_append(holder, py_None);
        pcc_gc_set_mark_color(holder, PY_FLAG_GC_BLACK);
        __atomic_fetch_and(&py_header(holder)->flags, ~PY_FLAG_GC_FRESH_ALLOC, __ATOMIC_RELEASE);
    }
    PyObject *tail = NULL;
    if (want_tail) {
        PyClassObject *tail_cls = py_class_new("LayerTail", NULL, 0, NULL, 0);
        if (!tail_cls) { error = 15; return NULL; }
        pcc_gc_store_root(&roots[CLASS_TAIL], (PyObject *)tail_cls);
        py_class_add_method(tail_cls, "__del__", (PyObject *)(uintptr_t)finish_tail);
        tail = py_instance_new(tail_cls);
        if (!tail) { error = 16; return NULL; }
        py_instance_set_field((PyInstanceObject *)b, 1, tail);
        pcc_gc_unpin(tail);
    }
    if (bulk) {
        tail = rescue == 11 ? py_dict_new() : py_list_new(0);
        if (!tail) { error = 29; return NULL; }
        if (rescue == 11) py_dict_set(tail, py_int_from_i64(7), b);
        else py_list_append(tail, b);
        py_instance_set_field((PyInstanceObject *)a, 0, tail);
        pcc_gc_unpin(tail);
    } else if (mixed) py_instance_set_field((PyInstanceObject *)b, 0, b);
    else if (!rescue && (peer_mode == 0 || peer_mode == 3)) py_instance_set_field((PyInstanceObject *)a, 0, b);
    if (silent == 2) {
        PyClassObject *other = py_class_new("InactiveFinalizer", NULL, 0, NULL, 0);
        if (!other) { error = 17; return NULL; }
        pcc_gc_store_root(&roots[CLASS_TAIL], (PyObject *)other);
        py_class_add_method(other, "__del__", (PyObject *)(uintptr_t)finish_tail);
    }
    if (!silent && !finalizer_only) {
        PyObject *wa = py_weakref_new(a, callback);
        PyObject *wb = py_weakref_new(b, callback);
        if (!wa || !wb) { error = 11; return NULL; }
        pcc_gc_store_root(&roots[WEAK_A], wa);
        pcc_gc_store_root(&roots[WEAK_B], wb);
    }
    pcc_gc_unpin(a);
    pcc_gc_unpin(b);
    pcc_py_gc_minor_graph_lock();
    PyObject *items[] = {a, b, tail};
    for (int i = 0; i < (want_tail || bulk ? 3 : 2); ++i) {
        PyObject *obj = items[i];
        int index = rescue || peer_mode || i >= 2 ? i : 1-i;
        nodes[index] = pcc_gc_object_index_find(obj);
        if (!nodes[index]) error = 12;
        pcc_gc_set_mark_color(obj, PY_FLAG_GC_WHITE);
        __atomic_fetch_and(&py_header(obj)->flags, ~PY_FLAG_GC_FRESH_ALLOC, __ATOMIC_RELEASE);
        __atomic_fetch_or(&py_header(obj)->flags, PY_FLAG_GC_SWEEP_CANDIDATE, __ATOMIC_RELEASE);
    }
    pcc_py_gc_minor_graph_unlock();
    if (wide_size) {
        // The last slot straddles bit positions 62, 63 and 64 between
        // cases. Publish a different low-slot candidate in the same
        // callback, so a modulo-64 alias cannot stand in for either bit.
        int b_slot = rescue ? 1 : 0;
        int a_slot = rescue ? 0 : 1;
        nodes[wide_size - 1] = nodes[b_slot];
        for (int i = 0; i < wide_size - 1; ++i) {
            if (i == a_slot) continue;
            PyObject *obj = py_list_new(0);
            if (!obj) { error = 42; return NULL; }
            pcc_gc_unpin(obj);
            pcc_py_gc_minor_graph_lock();
            nodes[i] = pcc_gc_object_index_find(obj);
            pcc_gc_set_mark_color(obj, PY_FLAG_GC_WHITE);
            __atomic_fetch_and(&py_header(obj)->flags, ~PY_FLAG_GC_FRESH_ALLOC, __ATOMIC_RELEASE);
            __atomic_fetch_or(&py_header(obj)->flags, PY_FLAG_GC_SWEEP_CANDIDATE, __ATOMIC_RELEASE);
            pcc_py_gc_minor_graph_unlock();
            if (!nodes[i]) { error = 43; return NULL; }
            if (i == b_slot) wide_low = obj;
        }
    }
    return NULL;
}

static void *parked_observer(void *unused) {
    (void)unused;
    if (pcc_current_thread_id() <= 0) return NULL;
    __atomic_store_n(&worker_started, 1, __ATOMIC_RELEASE);
    while (!__atomic_load_n(&worker_done, __ATOMIC_ACQUIRE)) pcc_thread_safepoint();
    return NULL;
}

static void *weakref_peer(void *unused) {
    (void)unused;
    if (pcc_current_thread_id() <= 0) {
        __atomic_store_n(&worker_started, -1, __ATOMIC_RELEASE);
        return NULL;
    }
    __atomic_store_n(&worker_started, 1, __ATOMIC_RELEASE);
    while (!__atomic_load_n(&peer_request, __ATOMIC_ACQUIRE)) pcc_thread_safepoint();
    // A raw C activation, not a registered root slot or a barrier store.
    PyObject *volatile borrowed = py_weakref_call(roots[WEAK_B]);
    int good = borrowed && borrowed != py_None;
    if (good && peer_mode == 3) {
        PyObject *callback = py_func_new((void *)weak_event, NULL);
        PyObject *ref = callback ? py_weakref_new(borrowed, callback) : NULL;
        if (!ref) good = 0;
        else pcc_gc_store_root(&roots[WEAK_NEW], ref);
    }
    __atomic_store_n(&peer_read, good ? 1 : -1, __ATOMIC_RELEASE);
    if (peer_mode == 1) {
        while (!__atomic_load_n(&worker_done, __ATOMIC_ACQUIRE)) pcc_thread_safepoint();
        PyObject *after = py_weakref_call(roots[WEAK_B]);
        peer_live_after = good && after == borrowed;
        if (after) py_decref(after);
    }
    if (borrowed) py_decref(borrowed);
    return NULL;
}

int main(int argc, char **argv) {
    rescue = argc > 1 && strcmp(argv[1], "rescue") == 0;
    late_new = argc > 1 && strcmp(argv[1], "late-new") == 0;
    idle_worker = argc > 1 && strcmp(argv[1], "worker") == 0;
    if (argc > 1 && strcmp(argv[1], "peer-hold") == 0) peer_mode = 1;
    if (argc > 1 && strcmp(argv[1], "peer-depart") == 0) peer_mode = 2;
    if (argc > 1 && strcmp(argv[1], "peer-new") == 0) peer_mode = 3;
    silent = argc > 1 && strcmp(argv[1], "silent") == 0;
    if (argc > 1 && strcmp(argv[1], "silent-live-del") == 0) silent = 2;
    finalizer_only = argc > 1 && strcmp(argv[1], "finalizer-only") == 0;
    if (argc > 1 && strcmp(argv[1], "caller") == 0) rescue = 2;
    if (argc > 1 && strcmp(argv[1], "holder") == 0) rescue = 3;
    if (argc > 1 && strcmp(argv[1], "pin") == 0) rescue = 4;
    if (argc > 1 && strcmp(argv[1], "dict-holder") == 0) rescue = 6;
    if (argc > 1 && strcmp(argv[1], "field-holder") == 0) rescue = 7;
    if (argc > 1 && strcmp(argv[1], "dict-replace") == 0) rescue = 8;
    if (argc > 1 && strcmp(argv[1], "list-replace") == 0) rescue = 9;
    if (argc > 1 && strcmp(argv[1], "set-holder") == 0) rescue = 10;
    if (argc > 1 && strcmp(argv[1], "dict-update") == 0) rescue = 11;
    if (argc > 1 && strcmp(argv[1], "list-slice") == 0) rescue = 12;
    if (argc > 1 && strcmp(argv[1], "set-update") == 0) rescue = 13;
    if (argc > 1 && strcmp(argv[1], "capi-dict") == 0) rescue = 14;
    if (argc > 1 && strcmp(argv[1], "capi-list") == 0) rescue = 15;
    if (argc > 1 && strcmp(argv[1], "capi-attr") == 0) rescue = 16;
    if (argc > 1 && strcmp(argv[1], "extension-field") == 0) rescue = 17;
    if (argc > 1 && strcmp(argv[1], "caller-old-holder") == 0) rescue = 18;
    if (argc > 1 && strcmp(argv[1], "interior") == 0) rescue = 5;
    withdrawn = argc > 1 && strcmp(argv[1], "withdrawn") == 0;
    if (argc > 2 && (strcmp(argv[1], "wide-keep") == 0 || strcmp(argv[1], "wide-drop") == 0)) {
        if (strcmp(argv[2], "63") == 0) wide_size = WIDE_MIN;
        else if (strcmp(argv[2], "64") == 0) wide_size = WIDE_MAX - 1;
        else if (strcmp(argv[2], "65") == 0) wide_size = WIDE_MAX;
        else return 44;
        rescue = strcmp(argv[1], "wide-keep") == 0;
        withdrawn = !rescue;
    }
    mixed = argc > 1 && strcmp(argv[1], "mixed") == 0;
    if (argc > 1 && strcmp(argv[1], "mixed-tail") == 0) mixed = 2;
    if (argc > 1 && strcmp(argv[1], "mixed-rescue") == 0) { mixed = 1; rescue = 1; }
    // Unregistered storage in the suspended owner’s caller frame. This slot
    // starts empty; the fixture or callback publishes through its saved address.
    PyObject *volatile raw = NULL;
    caller_hold = &raw;
    if (pcc_current_thread_id() <= 0) return 19;
    pcc_gc_cms_stop_worker();
    for (int i = 0; i < ROOTS; ++i) pcc_gc_scheduler_root_register(&roots[i]);
    void *thread = NULL;
    if (pcc_thread_start(&thread, seal_on_departing_thread, NULL) ||
        pcc_thread_join(thread, NULL)) return 20;
    if (error) return error;
    if (idle_worker) {
        if (pcc_thread_start(&thread, parked_observer, NULL)) return 25;
        while (!__atomic_load_n(&worker_started, __ATOMIC_ACQUIRE)) pcc_thread_safepoint();
    }
    if (peer_mode) {
        if (pcc_thread_start(&peer_thread, weakref_peer, NULL)) return 39;
        int started;
        while (!(started = __atomic_load_n(&worker_started, __ATOMIC_ACQUIRE))) pcc_thread_safepoint();
        if (started < 0) return 40;
    }
    if (pcc_stop_the_world() != 0) return 21;
    pcc_gc_begin_explicit_tracing_collect();
    pcc_py_gc_minor_graph_lock();
    pcc_gc_tracing_candidates_reset();
    int candidate_count = wide_size ? wide_size :
        (mixed == 2 || rescue == 4 || (rescue >= 11 && rescue <= 13) ? 3 : 2);
    for (int i = 0; i < candidate_count; ++i) pcc_gc_tracing_candidates_note(nodes[i]);
    pcc_gc_tracing_finish_commits = 1;
    pcc_gc_tracing_candidates_seal(1);
    pcc_gc_tracing_finish_stop_epoch = pcc_thread_world_stop_epoch_held();
    pcc_py_gc_minor_graph_unlock();
    int64_t before = pcc_gc_telemetry(PCC_GC_COUNTER_SWEEP_RECHECK_CALLS);
    int64_t scans_start = pcc_gc_metric_layer_weak_registry_scans;
    int64_t pass0_start = pcc_gc_telemetry(PCC_GC_COUNTER_SWEEP_PASS0_CALLS);
    int64_t reclaimed = pcc_gc_tracing_sweep_unreachable(candidate_count);
    int64_t scans = pcc_gc_metric_layer_weak_registry_scans - scans_start;
    int64_t pass0 = pcc_gc_telemetry(PCC_GC_COUNTER_SWEEP_PASS0_CALLS) - pass0_start;
    int64_t full = pcc_gc_telemetry(PCC_GC_COUNTER_SWEEP_RECHECK_CALLS) - before;
    pcc_gc_end_explicit_tracing_collect();
    if (pcc_resume_world() != 0) return 22;
    if (idle_worker) {
        __atomic_store_n(&worker_done, 1, __ATOMIC_RELEASE);
        if (pcc_thread_join(thread, NULL)) return 26;
    }
    if (peer_mode == 1) {
        __atomic_store_n(&worker_done, 1, __ATOMIC_RELEASE);
        if (pcc_thread_join(peer_thread, NULL) || !peer_live_after) return 41;
    }
    if (silent || finalizer_only) {
        printf("legacy=%lld reclaimed=%lld callbacks=%d recheck=%lld\n",
               (long long)pass0, (long long)reclaimed, event_count, (long long)full);
        if (finalizer_only) {
            if (event_count != 2 || events[0] != 1 || events[1] != 3) return 18;
            puts("a-del b-del");
        }
        return error;
    }
    PyObject *b = py_weakref_call(roots[WEAK_B]);
    if (rescue == 2 && (raw != b || raw == py_None || raw == NULL)) error = 13;
    if (rescue == 5 && (!b || b == py_None || raw != (PyObject *)((char *)b + 1))) error = 19;
    if (rescue == 17 && (!b || b == py_None || !ext_edge_visits ||
                         ((struct ExtensionHolder *)roots[HOLD])->edge != b)) error = 34;
    if (rescue == 18 && (roots[HOLD] != NULL || !raw || !b || b == py_None)) error = 36;
    note(b == py_None ? 5 : 6);
    const char *names[] = {"", "a-del", "a-weak", "b-del", "b-weak", "b-dead", "b-live", "tail-del", "b-new"};
    for (int i = 0; i < event_count; ++i)
        printf("%s%s", i ? " " : "", names[events[i]]);
    puts("");
    fprintf(stderr, "full=%lld scans=%lld\n", (long long)full, (long long)scans);
    return error;
}
