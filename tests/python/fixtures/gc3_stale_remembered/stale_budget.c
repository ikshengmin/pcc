#include "py_internal.h"
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>

/* Existing ABI only. Sequential no-worker queue accounting fixture.
 * No production links, flags, cursors or revisions are assigned here. */
extern int64_t pcc_gc_cms_stop_worker(void);
extern int64_t pcc_gc_generational_step(int64_t, int64_t);
extern void *pcc_gc_object_list_head(void);
extern void *pcc_gc_object_node_next(void *);
extern int64_t pcc_gc_object_node_is_active(void *);
extern int64_t pcc_gc_backend3_promotion_cursor(void *);
extern void *pcc_gc_backend3_remembered_owner_list_head(void);
extern int64_t pcc_gc_backend3_drain_remembered_owners(int64_t, void *);
extern void pcc_gc_backend3_finish_detached_remembered_owners(void *);
extern void *pcc_gc_backend3_promotion_head;
extern void *pcc_gc_backend3_promotion_tail;
extern void *pcc_gc_backend3_remembered_scan_cursor;
extern int64_t pcc_gc_backend3_remembered_scan_revision;
extern int64_t pcc_gc_object_list_revision;
extern int32_t pcc_gc_backend3_remembered_overflow;

enum { OWNERS = 66, QUEUE = 67, OBJECTS = 4096, MAX_STEPS = 256 };
static PyObject *roots[OWNERS];
static PyObject *a;
static PyObject *b;
static PyObject *sentinel;
static int owner_count;
static int registered_count;
static int failed_line;
static const char *failed_test;
static int stopped;
static int locked;
static void *detached;

#define NEED(test) do { if (!(test)) { \
    if (!failed_line) { failed_line = __LINE__; failed_test = #test; } \
    goto done; \
} } while (0)

struct State {
    void *objects[OBJECTS];
    int object_count;
    void *queue[QUEUE];
    void *queue_owners[QUEUE];
    int queue_count;
    void *owner_nodes[OWNERS];
    int owner_positions[OWNERS];
    int32_t flags[OWNERS];
    int64_t cursors[OWNERS];
    void *promotion_next[OWNERS];
    void *promotion_prev[OWNERS];
    void *promotion_head;
    void *promotion_tail;
    void *scan_cursor;
    int64_t revision;
    int64_t scan_revision;
    int32_t overflow;
};
static struct State base;
static struct State before;
static struct State after;
static struct State scratch;
static int ordered[OWNERS];
static int64_t owner_ids[OWNERS];

/* Only inspect live native node fields while graph-locked under STW. */
static void *pointer_at(void *node, int offset) {
    return *(void **)((char *)node + offset);
}

static int begin_batch(void) {
    int ok = 0;
    NEED(!stopped && !locked && detached == 0);
    NEED(pcc_thread_owns_stopped_world() == 0);
    NEED(pcc_stop_the_world() == 0);
    stopped = 1;
    NEED(pcc_thread_owns_stopped_world() != 0);
    NEED(pcc_thread_all_stopped_at_gates() != 0);
    pcc_py_gc_minor_graph_lock();
    locked = 1;
    ok = 1;
done:
    return ok;
}

/* Every admitted helper reaches this path, including assertion failures.
 * Finish exactly once per batch, outside the lock and before resume. */
static int end_batch(void) {
    int ok = 1;
    if (locked) {
        pcc_py_gc_minor_graph_unlock();
        locked = 0;
    }
    if (stopped) {
        pcc_gc_backend3_finish_detached_remembered_owners(detached);
        detached = 0;
        if (pcc_resume_world() != 0) {
            if (!failed_line) {
                failed_line = __LINE__; failed_test = "resume world";
            }
            ok = 0;
        }
        stopped = 0;
        if (pcc_thread_owns_stopped_world() != 0) {
            if (!failed_line) {
                failed_line = __LINE__; failed_test = "world ownership released";
            }
            ok = 0;
        }
    }
    return ok;
}

static int capture_locked(struct State *s) {
    int ok = 0;
    NEED(locked && stopped);
    s->revision = pcc_gc_object_list_revision;
    s->object_count = 0;
    void *node = pcc_gc_object_list_head();
    while (node) {
        NEED(s->object_count < OBJECTS);
        for (int j = 0; j < s->object_count; j++) NEED(s->objects[j] != node);
        s->objects[s->object_count++] = node;
        node = pcc_gc_object_node_next(node);
    }
    for (int i = 0; i < owner_count; i++) {
        PyObject *owner = roots[i];
        NEED(owner != 0);
        node = pcc_gc_object_index_find(owner);
        NEED(node != 0 && pcc_gc_object_node_is_active(node));
        NEED(pointer_at(node, 0) == owner);
        for (int j = 0; j < i; j++) NEED(s->owner_nodes[j] != node);
        int position = -1;
        for (int j = 0; j < s->object_count; j++) {
            if (s->objects[j] == node) { NEED(position == -1); position = j; }
        }
        NEED(position >= 0);
        s->owner_nodes[i] = node;
        s->owner_positions[i] = position;
        s->flags[i] = ((PyObjectHeader *)owner)->flags;
        s->cursors[i] = pcc_gc_backend3_promotion_cursor(node);
        s->promotion_next[i] = pointer_at(node, 64);
        s->promotion_prev[i] = pointer_at(node, 72);
    }
    s->queue_count = 0;
    node = pcc_gc_backend3_remembered_owner_list_head();
    while (node) {
        NEED(s->queue_count < QUEUE);
        for (int j = 0; j < s->queue_count; j++) NEED(s->queue[j] != node);
        s->queue[s->queue_count] = node;
        s->queue_owners[s->queue_count++] = pointer_at(node, 0);
        node = pointer_at(node, 8);
    }
    s->promotion_head = pcc_gc_backend3_promotion_head;
    s->promotion_tail = pcc_gc_backend3_promotion_tail;
    s->scan_cursor = pcc_gc_backend3_remembered_scan_cursor;
    s->scan_revision = pcc_gc_backend3_remembered_scan_revision;
    s->overflow = pcc_gc_backend3_remembered_overflow;
    ok = 1;
done:
    return ok;
}

static int capture(struct State *s) {
    int ok = 0;
    NEED(begin_batch());
    NEED(capture_locked(s));
    ok = 1;
done:
    if (!end_batch()) ok = 0;
    return ok;
}

static int same_graph(struct State *x, struct State *y) {
    if (x->revision != y->revision || x->object_count != y->object_count) return 0;
    for (int i = 0; i < x->object_count; i++)
        if (x->objects[i] != y->objects[i]) return 0;
    for (int i = 0; i < owner_count; i++)
        if (x->owner_nodes[i] != y->owner_nodes[i]) return 0;
    return 1;
}
static int same_queue_suffix(struct State *x, int offset, struct State *y) {
    if (x->queue_count - offset != y->queue_count) return 0;
    for (int i = 0; i < y->queue_count; i++)
        if (x->queue[i + offset] != y->queue[i]
            || x->queue_owners[i + offset] != y->queue_owners[i]) return 0;
    return 1;
}
static int same_promotion(struct State *x, struct State *y) {
    if (x->promotion_head != y->promotion_head
        || x->promotion_tail != y->promotion_tail) return 0;
    for (int i = 0; i < owner_count; i++)
        if (x->cursors[i] != y->cursors[i]
            || x->promotion_next[i] != y->promotion_next[i]
            || x->promotion_prev[i] != y->promotion_prev[i]) return 0;
    return 1;
}
static int same_state(struct State *x, struct State *y) {
    if (!same_graph(x,y) || !same_queue_suffix(x,0,y) || !same_promotion(x,y)
        || x->scan_cursor != y->scan_cursor || x->scan_revision != y->scan_revision
        || x->overflow != y->overflow) return 0;
    for (int i = 0; i < owner_count; i++) if (x->flags[i] != y->flags[i]) return 0;
    return 1;
}
static int idle(struct State *s) {
    if (s->queue_count || s->promotion_head || s->promotion_tail
        || s->scan_cursor || s->scan_revision || s->overflow) return 0;
    for (int i = 0; i < owner_count; i++)
        if (s->cursors[i] || s->promotion_next[i] || s->promotion_prev[i]
            || (s->flags[i] & PY_FLAG_GC_REMEMBERED)) return 0;
    return 1;
}
static int all_old(struct State *s) {
    for (int i = 0; i < owner_count; i++)
        if (!(s->flags[i] & PY_FLAG_GC_OLD)
            || (s->flags[i] & (PY_FLAG_GC_YOUNG | PY_FLAG_GC_RELOCATION_CANDIDATE)))
            return 0;
    return 1;
}
static int young(PyObject *value) {
    int32_t flags = ((PyObjectHeader *)value)->flags;
    return (flags & PY_FLAG_GC_YOUNG) && !(flags & PY_FLAG_GC_OLD);
}
static int pending_owner(struct State *s, int i) {
    return s->promotion_head == s->owner_nodes[i]
        || s->promotion_tail == s->owner_nodes[i]
        || s->promotion_next[i] || s->promotion_prev[i];
}

/* A real overflow scan, not a normal step: no roots or slot worklist run. */
static int overflow_drain(int64_t budget, struct State *out) {
    int ok = 0;
    NEED(begin_batch());
    NEED(pcc_gc_backend3_drain_remembered_owners(budget, &detached) == budget);
    NEED(detached == 0);
    NEED(capture_locked(out));
    NEED(young(a) && young(b) && young(sentinel));
    ok = 1;
done:
    if (!end_batch()) ok = 0;
    return ok;
}

/* The oracle counts actual detached native nodes before real cleanup.
 * Its own observations add work and are not locked-load instrumentation. */
static int measured_drain(int budget, int mode) {
    int ok = 0;
    NEED(begin_batch());
    NEED(capture_locked(&before));
    NEED(!before.overflow && !before.scan_cursor && !before.scan_revision);
    int n = budget <= 0 ? 0 : budget;
    if (n > before.queue_count) n = before.queue_count;
    int64_t work = pcc_gc_backend3_drain_remembered_owners(budget, &detached);
    NEED(work == n);
    void *node = detached;
    for (int j = n - 1; j >= 0; j--) {
        NEED(node != 0 && node == before.queue[j]);
        NEED(pointer_at(node, 0) == before.queue_owners[j]);
        node = pointer_at(node, 8);
    }
    NEED(node == 0);
    NEED(capture_locked(&after));
    NEED(same_graph(&before,&after));
    NEED(same_queue_suffix(&before,n,&after));
    NEED(!after.overflow && !after.scan_cursor && !after.scan_revision);
    if (n == 0) NEED(same_state(&before,&after));
    if (mode == 1 || (mode == 2 && n < before.queue_count))
        NEED(same_promotion(&before,&after));
    for (int i = 0; i < owner_count; i++) {
        int removed = 0;
        for (int j = 0; j < n; j++)
            if (before.queue_owners[j] == roots[i]) removed = 1;
        int32_t expected = before.flags[i];
        if (removed) expected &= ~PY_FLAG_GC_REMEMBERED;
        NEED(after.flags[i] == expected);
        if (mode == 0 && removed) NEED(pending_owner(&after,i));
    }
    NEED(young(a) && young(b) && young(sentinel));
    ok = 1;
done:
    if (!end_batch()) ok = 0;
    return ok;
}

static int normal_step(void) {
    int ok = 0;
    NEED(pcc_thread_owns_stopped_world() == 0);
    (void)pcc_gc_generational_step(16,0);
    NEED(pcc_thread_owns_stopped_world() == 0);
    /* The scheduler rewrites its registered slots itself. Do not assign a
     * managed root with a raw C store even if the value would be identical. */
    for (int i = 0; i < owner_count; i++)
        NEED(pcc_gc_load_ptr(0, &roots[i]) == roots[i]);
    ok = 1;
done:
    return ok;
}

static int child_matches(int index, int64_t identity, double value) {
    PyObject *child = py_list_get(roots[index],0);
    if (!child) return 0;
    int32_t flags = ((PyObjectHeader *)child)->flags;
    int ok = py_list_len(roots[index]) == 1 && py_obj_id(child) == identity
        && py_float_to_f64(child) == value && (flags & PY_FLAG_GC_OLD)
        && !(flags & PY_FLAG_GC_YOUNG);
    pcc_gc_release(child);
    return ok;
}

int main(int argc, char **argv) {
    int ok = 0;
    int mode = -1;
    int count = 0;
    int budget = 0;
    int resets = 0;
    NEED(argc == 5);
    mode = atoi(argv[1]); count = atoi(argv[2]);
    budget = atoi(argv[3]); resets = atoi(argv[4]);
    NEED((mode == 0 && count == 17 && budget == 4 && resets == 0)
        || (mode == 1 && count == 2 && budget == 1 && resets == 0)
        || (mode == 1 && count == 65 && budget == 16 && resets == 2)
        || (mode == 2 && count == 17 && budget == 4 && resets == 0));
    owner_count = count + (mode != 0);
    py_gc_disable();
    NEED(pcc_gc_set_backend(PCC_GC_KIND_GENERATIONAL_MINOR_MAJOR) == 0);
    NEED(pcc_threads_enabled() == 1);
    /* Before any managed allocation. No claim of joining an active worker.
     * Driver must set MINOR_ALLOC_MAX=16: disable alone does not stop refill. */
    NEED(pcc_gc_cms_stop_worker() == 0);
    NEED(pcc_thread_owns_stopped_world() == 0);
    for (int i = 0; i < owner_count; i++) {
        pcc_gc_scheduler_root_register(&roots[i]); registered_count++;
        pcc_gc_store_root(&roots[i], py_list_new(1));
        NEED(roots[i] != 0);
        py_list_append(roots[i],py_int_from_i64(1000+i));
        NEED(py_list_len(roots[i]) == 1);
    }
    for (int step = 0; step < MAX_STEPS; step++) {
        NEED(capture(&scratch));
        if (all_old(&scratch) && idle(&scratch)) break;
        NEED(normal_step());
    }
    NEED(capture(&scratch)); NEED(all_old(&scratch) && idle(&scratch));
    a = py_float_from_f64(101.25); b = py_float_from_f64(202.5);
    sentinel = py_float_from_f64(303.75);
    NEED(a && b && sentinel && young(a) && young(b) && young(sentinel));
    int64_t aid = py_obj_id(a), bid = py_obj_id(b), sid = py_obj_id(sentinel);
    NEED(aid && bid && sid && aid != bid && aid != sid && bid != sid);
    for (int i = 0; i < owner_count; i++) {
        owner_ids[i] = py_obj_id(roots[i]);
        NEED(owner_ids[i] != 0);
    }
    NEED(capture(&base)); NEED(all_old(&base) && idle(&base));
    /* Select by actual observed order, not allocation order or host model. */
    int found = 0;
    for (int j = 0; j < base.object_count; j++)
        for (int i = 0; i < owner_count; i++)
            if (base.owner_positions[i] == j) ordered[found++] = i;
    NEED(found == owner_count);
    int anchor = ordered[0];
    int k = base.owner_positions[anchor] + 1;
    if (mode != 0) {
        NEED(k < base.object_count);
        pcc_gc_backend3_remembered_scan_probe_config(0);
        py_list_set(roots[anchor],0,a);
        NEED(capture(&scratch)); NEED(same_graph(&base,&scratch));
        NEED(scratch.queue_count == 0 && scratch.overflow == 1);
        NEED(!scratch.scan_cursor && !scratch.scan_revision);
        NEED(scratch.flags[anchor] & PY_FLAG_GC_REMEMBERED);
        NEED(overflow_drain(k,&scratch)); NEED(same_graph(&base,&scratch));
        NEED(scratch.scan_cursor == base.objects[k]);
        NEED(scratch.scan_revision == base.revision && scratch.overflow == 1);
        NEED(!(scratch.flags[anchor] & PY_FLAG_GC_REMEMBERED));
        NEED(pending_owner(&scratch,anchor));
    }
    pcc_gc_backend3_remembered_scan_probe_config(-1);
    /* Mixed anchor is enqueued FIRST, so it becomes the live tail. */
    int start = mode == 1 ? 1 : 0;
    for (int j = start; j < owner_count; j++) {
        int i = ordered[j];
        NEED(capture(&before));
        py_list_set(roots[i],0,b);
        NEED(capture(&after)); NEED(same_graph(&base,&after));
        NEED(after.queue_count == before.queue_count + 1);
        NEED(after.queue_owners[0] == roots[i]);
        NEED(same_queue_suffix(&after,1,&before));
        NEED(after.flags[i] & PY_FLAG_GC_REMEMBERED);
        NEED(after.scan_cursor == before.scan_cursor);
        NEED(after.scan_revision == before.scan_revision);
        NEED(after.overflow == before.overflow);
        NEED(young(a) && young(b) && young(sentinel));
    }
    NEED(capture(&before));
    NEED(before.queue_count == count + (mode == 2));
    if (mode != 0) {
        NEED(overflow_drain(base.object_count-k,&after));
        NEED(same_graph(&base,&after)); NEED(same_queue_suffix(&before,0,&after));
        NEED(!after.overflow && !after.scan_cursor && !after.scan_revision);
        for (int j = 1; j < owner_count; j++) {
            int i = ordered[j];
            NEED(!(after.flags[i] & PY_FLAG_GC_REMEMBERED));
            NEED(pending_owner(&after,i));
        }
        NEED(!!(after.flags[anchor] & PY_FLAG_GC_REMEMBERED) == (mode == 2));
        if (mode == 2) NEED(after.queue_owners[after.queue_count-1] == roots[anchor]);
    }
    NEED(capture(&before));
    for (int r = 0; r < resets; r++) {
        pcc_gc_telemetry_reset();
        NEED(capture(&after)); NEED(same_state(&before,&after));
        NEED(young(a) && young(b) && young(sentinel));
    }
    NEED(measured_drain(0,mode)); NEED(measured_drain(-1,mode));
    for (int round = 0; round < QUEUE; round++) {
        NEED(capture(&scratch));
        if (!scratch.queue_count) break;
        NEED(measured_drain(budget,mode));
    }
    NEED(capture(&scratch)); NEED(scratch.queue_count == 0);
    for (int step = 0; step < MAX_STEPS; step++) {
        NEED(capture(&scratch));
        if (idle(&scratch)) break;
        NEED(normal_step()); NEED(young(sentinel));
    }
    NEED(capture(&scratch)); NEED(idle(&scratch) && all_old(&scratch));
    for (int stable = 0; stable < 5; stable++) {
        if (stable) NEED(normal_step());
        NEED(capture(&scratch)); NEED(idle(&scratch) && all_old(&scratch));
        for (int i = 0; i < owner_count; i++) {
            NEED(py_obj_id(roots[i]) == owner_ids[i]);
            NEED(child_matches(i,mode == 1 && i == anchor ? aid : bid,
                               mode == 1 && i == anchor ? 101.25 : 202.5));
        }
        NEED(py_obj_id(sentinel) == sid && py_float_to_f64(sentinel) == 303.75);
        NEED(young(sentinel));
    }
    ok = 1;
done:
    /* Batch helpers already release their own admission; this is defensive.
     * On failure do not run GC or repair pending state to fabricate success. */
    if (!end_batch()) ok = 0;
    if (ok) {
        pcc_gc_backend3_remembered_scan_probe_config(-1);
        pcc_gc_release(a); pcc_gc_release(b); pcc_gc_release(sentinel);
        for (int i = 0; i < registered_count; i++) {
            pcc_gc_store_root(&roots[i],0);
            pcc_gc_scheduler_root_unregister(&roots[i]);
        }
        puts("gc3-stale-budget-native-ok");
    } else {
        fprintf(stderr,"line %d: %s\n",failed_line,failed_test ? failed_test : "failure");
    }
    return ok ? 0 : 1;
}
