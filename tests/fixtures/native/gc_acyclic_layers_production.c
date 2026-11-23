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
extern int64_t py_module_attr_set(const char *, const char *, PyObject *);
extern PyObject *py_module_attr_get(const char *, const char *);
extern int PyObject_SetAttrString(PyObject *, const char *, PyObject *);
extern void pcc_gc_cms_stop_worker(void);
extern void pcc_gc_reassert_pin(PyObject *);
extern int64_t pcc_gc_metric_pin_balance(void);
extern void pcc_gc_begin_explicit_tracing_collect(void);
extern void pcc_gc_end_explicit_tracing_collect(void);
extern int64_t pcc_thread_start(void **, void *(*)(void *), void *);
extern int64_t pcc_thread_join(void *, void **);

enum { CLASS_A, CLASS_B, CLASS_TAIL, WEAK_A, WEAK_B, WEAK_NEW, HOLD, EXT_TYPE, WIDE_LOW, MIXED_EARLIER, MIXED_LATER, ROOTS };
static PyObject *roots[ROOTS];
enum {
    WIDE_FIRST_EDGE = 63, WIDE_FIRST_WORD = 64, WIDE_SECOND_EDGE = 65,
    WIDE_FOURTH_EDGE = 255, WIDE_FOURTH_WORD = 256, WIDE_MAX = 257,
};
static void *nodes[WIDE_MAX];
static PyObject *wide_low;
static int wide_size;
static int rescue, withdrawn, mixed, silent, finalizer_only, late_new, idle_worker, error;
static int worker_started, worker_done, pin_withdraw;
static int peer_mode, peer_request, peer_read, peer_live_after;
static void *peer_thread;
static PyObject *volatile *caller_hold;
static int events[6], event_count;
static int ext_edge_visits;
static int mixed_order, mixed_order_trace, mixed_order_del, mixed_discarded;
static int nested_collect, nested_collect_calls;

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
    if (nested_collect && event >= 1 && event <= 4) {
        ++nested_collect_calls;
        if (py_gc_collect() != 0 || py_err_occurred()) error = 64;
    }
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
        else if (pin_withdraw) {
            pcc_gc_pin(target);
            pcc_gc_unpin(target);
        }
        else if (rescue == 4) pcc_gc_pin(target);
        else if (rescue == 19) pcc_gc_reassert_pin(target);
        else if (rescue == 20) {
            int64_t balance = pcc_gc_metric_pin_balance();
            pcc_gc_unpin(roots[HOLD]);
            pcc_gc_pin(target);
            if (pcc_gc_metric_pin_balance() != balance) error = 49;
        }
        else if (rescue == 21 || rescue == 22) {
            if (py_module_attr_set("pcc_gc9_owner", "kept", target) != 0) error = 60;
        }
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
    if (mixed_order) {
        if (mixed_order_trace != 21 || pcc_thread_world_stop_epoch_held() >= 0) error = 60;
        ++mixed_order_del;
    }
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

static PyObject *mixed_order_observe(PyObject *captures, PyObject *args) {
    (void)captures;
    PyObject *ref = py_tuple_get(args, 0);
    int marker = ref == roots[MIXED_EARLIER] ? 1 : ref == roots[MIXED_LATER] ? 2 : 3;
    if (pcc_thread_world_stop_epoch_held() >= 0 ||
        py_weakref_call(roots[MIXED_EARLIER]) != py_None ||
        py_weakref_call(roots[MIXED_LATER]) != py_None) error = 61;
    if (marker == 3) ++mixed_discarded;
    else mixed_order_trace = mixed_order_trace * 10 + marker;
    if (ref) py_decref(ref);
    return py_int_from_i64(0);
}

static void *seal_on_departing_thread(void *unused) {
    (void)unused;
    const char *fields[] = {"child", "tail"};
    if (pcc_current_thread_id() <= 0) { error = 8; return NULL; }
    PyClassObject *a_cls = py_class_new("LayerA", NULL, 0, fields, 1);
    int want_tail = mixed == 2 || rescue == 4 || rescue == 19 || rescue == 20;
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
    PyObject *callback = (silent && silent != 5) || finalizer_only ? NULL : py_func_new((void *)weak_event, NULL);
    if (!a || !b || (!silent && !finalizer_only && !callback)) { error = 10; return NULL; }
    if (rescue == 21 && py_module_attr_set("pcc_gc9_owner", "kept", py_None) != 0) {
        error = 61;
        return NULL;
    }
    if (rescue == 3 || (rescue >= 6 && rescue <= 20)) {
        PyObject *holder = rescue == 3 || rescue == 9 || rescue == 12 || rescue == 15 || rescue == 18 ? py_list_new(0) :
                           rescue == 6 || rescue == 8 || rescue == 11 || rescue == 14 ? py_dict_new() :
                           rescue == 7 || rescue == 16 ? py_instance_new(a_cls) :
                           rescue == 17 ? new_extension_holder() : py_set_new();
        if (!holder) { error = 14; return NULL; }
        if (rescue == 20) pcc_gc_pin(holder);
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
    if (silent == 2 || silent == 4) {
        PyClassObject *other = py_class_new("InactiveFinalizer", NULL, 0, NULL, 0);
        if (!other) { error = 17; return NULL; }
        pcc_gc_store_root(&roots[CLASS_TAIL], (PyObject *)other);
        py_class_add_method(other, "__del__", (PyObject *)(uintptr_t)finish_tail);
    }
    if (silent == 3 || silent == 4) {
        PyObject *live = py_instance_new(a_cls);
        PyObject *unrelated = py_list_new(0);
        if (!live || !unrelated) { error = 45; return NULL; }
        pcc_gc_store_root(&roots[HOLD], live);
        pcc_gc_store_root(&roots[WEAK_NEW], unrelated);
        // These refs have no edge to either sealed candidate, even when
        // an unrelated class elsewhere has a real __del__ method.
        for (int i = 0; i < 128; ++i) {
            PyObject *ref = py_weakref_new(live, NULL);
            if (!ref) { error = 46; return NULL; }
            py_list_append(unrelated, ref);
        }
    }
    if ((!silent || silent == 5) && !finalizer_only) {
        PyObject *wa = py_weakref_new(a, callback);
        PyObject *wb = py_weakref_new(b, callback);
        if (!wa || !wb) { error = 11; return NULL; }
        pcc_gc_store_root(&roots[WEAK_A], wa);
        pcc_gc_store_root(&roots[WEAK_B], wb);
    }
    PyObject *discarded = NULL;
    if (mixed_order) {
        PyObject *cb = py_func_new((void *)mixed_order_observe, NULL);
        PyObject *earlier = cb ? py_weakref_new(tail, cb) : NULL;
        PyObject *later = cb ? py_weakref_new(tail, cb) : NULL;
        discarded = cb ? py_weakref_new(tail, cb) : NULL;
        if (!earlier || !later || !discarded) { error = 62; return NULL; }
        pcc_gc_store_root(&roots[MIXED_EARLIER], earlier);
        pcc_gc_store_root(&roots[MIXED_LATER], later);
        pcc_gc_unpin(discarded);
    }
    pcc_gc_unpin(a);
    pcc_gc_unpin(b);
    pcc_py_gc_minor_graph_lock();
    if (discarded) {
        nodes[3] = pcc_gc_object_index_find(discarded);
        if (!nodes[3]) error = 63;
        pcc_gc_set_mark_color(discarded, PY_FLAG_GC_WHITE);
        __atomic_fetch_and(&py_header(discarded)->flags, ~PY_FLAG_GC_FRESH_ALLOC, __ATOMIC_RELEASE);
        __atomic_fetch_or(&py_header(discarded)->flags, PY_FLAG_GC_SWEEP_CANDIDATE, __ATOMIC_RELEASE);
    }
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
        // The last slot straddles both a mask-word boundary and the
        // retired four-word bound. Also shade a distinct low slot so
        // a modulo-64 alias cannot stand in for either bit.
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

static void *publish_between_stops(void *unused) {
    (void)unused;
    PyObject *b = py_weakref_call(roots[WEAK_B]);
    if (!b || b == py_None) { error = 45; return NULL; }
    pcc_gc_store_root(&roots[HOLD], b);
    py_decref(b);
    return NULL;
}

int main(int argc, char **argv) {
    int prior = argc > 1 && (strcmp(argv[1], "prior-chain") == 0 ||
                             strcmp(argv[1], "prior-live") == 0);
    int prior_live = argc > 1 && strcmp(argv[1], "prior-live") == 0;
    rescue = argc > 1 && strcmp(argv[1], "rescue") == 0;
    late_new = argc > 1 && strcmp(argv[1], "late-new") == 0;
    nested_collect = argc > 1 && strcmp(argv[1], "nested-collect") == 0;
    idle_worker = argc > 1 && strcmp(argv[1], "worker") == 0;
    if (argc > 1 && strcmp(argv[1], "peer-hold") == 0) peer_mode = 1;
    if (argc > 1 && strcmp(argv[1], "peer-depart") == 0) peer_mode = 2;
    if (argc > 1 && strcmp(argv[1], "peer-new") == 0) peer_mode = 3;
    silent = argc > 1 && strcmp(argv[1], "silent") == 0;
    if (argc > 1 && strcmp(argv[1], "silent-live-del") == 0) silent = 2;
    if (argc > 1 && strcmp(argv[1], "silent-unrelated-weakrefs") == 0) silent = 3;
    if (argc > 1 && strcmp(argv[1], "silent-unrelated-weakrefs-live-del") == 0) silent = 4;
    if (argc > 1 && strcmp(argv[1], "only-weakrefs") == 0) silent = 5;
    finalizer_only = argc > 1 && strcmp(argv[1], "finalizer-only") == 0;
    if (argc > 1 && strcmp(argv[1], "caller") == 0) rescue = 2;
    if (argc > 1 && strcmp(argv[1], "holder") == 0) rescue = 3;
    if (argc > 1 && strcmp(argv[1], "pin") == 0) rescue = 4;
    if (argc > 1 && strcmp(argv[1], "pin-reassert") == 0) rescue = 19;
    if (argc > 1 && strcmp(argv[1], "pin-transfer") == 0) rescue = 20;
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
    if (argc > 1 && strcmp(argv[1], "module-existing") == 0) rescue = 21;
    if (argc > 1 && strcmp(argv[1], "module-new") == 0) rescue = 22;
    pin_withdraw = argc > 1 && strcmp(argv[1], "pin-withdraw") == 0;
    withdrawn = pin_withdraw || (argc > 1 && strcmp(argv[1], "withdrawn") == 0);
    if (argc > 2 && (strcmp(argv[1], "wide-keep") == 0 || strcmp(argv[1], "wide-drop") == 0)) {
        if (strcmp(argv[2], "63") == 0) wide_size = WIDE_FIRST_EDGE;
        else if (strcmp(argv[2], "64") == 0) wide_size = WIDE_FIRST_WORD;
        else if (strcmp(argv[2], "65") == 0) wide_size = WIDE_SECOND_EDGE;
        else if (strcmp(argv[2], "255") == 0) wide_size = WIDE_FOURTH_EDGE;
        else if (strcmp(argv[2], "256") == 0) wide_size = WIDE_FOURTH_WORD;
        else if (strcmp(argv[2], "257") == 0) wide_size = WIDE_MAX;
        else return 44;
        rescue = strcmp(argv[1], "wide-keep") == 0;
        withdrawn = !rescue;
    }
    mixed = argc > 1 && strcmp(argv[1], "mixed") == 0;
    if (argc > 1 && strcmp(argv[1], "mixed-tail") == 0) mixed = 2;
    if (argc > 1 && strcmp(argv[1], "mixed-weak-order") == 0) { mixed = 2; mixed_order = 1; }
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
    int candidate_count = mixed_order ? 4 : wide_size ? wide_size :
        (mixed == 2 || rescue == 4 || rescue == 19 || rescue == 20 ||
         (rescue >= 11 && rescue <= 13) ? 3 : 2);
    for (int i = 0; i < candidate_count; ++i) pcc_gc_tracing_candidates_note(nodes[i]);
    pcc_gc_tracing_finish_commits = 1;
    pcc_gc_tracing_candidates_seal(1);
    pcc_gc_tracing_finish_stop_epoch = pcc_thread_world_stop_epoch_held();
    pcc_py_gc_minor_graph_unlock();
    int64_t before = pcc_gc_telemetry(PCC_GC_COUNTER_SWEEP_RECHECK_CALLS);
    if (prior) {
        if (pcc_resume_world() != 0) return 46;
        if (prior_live && (pcc_thread_start(&thread, publish_between_stops, NULL) ||
                           pcc_thread_join(thread, NULL) || error)) return 47;
        if (pcc_stop_the_world() != 0 ||
            pcc_thread_world_stop_epoch_held() == pcc_gc_tracing_finish_stop_epoch) return 48;
    }
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
    if (mixed_order) {
        printf("mixed %d %d %d\n", mixed_order_trace, mixed_order_del, mixed_discarded);
        return error || mixed_order_trace != 21 || mixed_order_del != 1 || mixed_discarded != 0;
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
    if (rescue == 21 || rescue == 22) {
        PyObject *stored = py_module_attr_get("pcc_gc9_owner", "kept");
        if (stored == NULL || stored == py_None || stored != b) error = 62;
        if (stored) py_decref(stored);
    }
    note(b == py_None ? 5 : 6);
    if (nested_collect && nested_collect_calls != 4) error = 65;
    const char *names[] = {"", "a-del", "a-weak", "b-del", "b-weak", "b-dead", "b-live", "tail-del", "b-new"};
    for (int i = 0; i < event_count; ++i)
        printf("%s%s", i ? " " : "", names[events[i]]);
    puts("");
    fprintf(stderr, "full=%lld scans=%lld\n", (long long)full, (long long)scans);
    return error;
}
