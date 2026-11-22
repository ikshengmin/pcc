/* Executable reference arm for #9. This code is not linked into PCC. */
#include "py_internal.h"
#include <stdint.h>
#include <stdio.h>

typedef void (*SlotVisitor)(PyObject **, int64_t, void *);
extern int64_t pcc_gc_visit_object_slots_slice(
    PyObject *, int64_t, int64_t, SlotVisitor, void *, void *);
extern int64_t pcc_thread_world_stop_epoch_held(void);

enum { MAX_VERTICES = 8, MAX_EDGES = 24 };
enum { OWNING_VALUE = 1, OWNING_TYPE = 2, WEAK_OR_DERIVED = 3 };
enum { SLOT_BATCH = 4, NEXT_CURSOR = 0, EXAMINED = 1, STATE_WORDS = 2 };
enum { NO_VERTEX = -1, DONE = -1 };
enum { ACYCLIC_DROP = 1, CYCLICALLY_RETAINED = 2 };

struct Graph {
    PyObject *const *vertices;
    int64_t count, owner, remaining, used, error;
    int64_t head[MAX_VERTICES], incoming[MAX_VERTICES];
    int64_t next[MAX_EDGES], to[MAX_EDGES];
};

static void add_edge(PyObject **slot, int64_t role, void *context) {
    struct Graph *g = context;
    if (role == WEAK_OR_DERIVED || g->error) return;
    if (role != OWNING_VALUE && role != OWNING_TYPE) {
        g->error = 1;
        return;
    }
    PyObject *child = *slot;
    if (!child) return;
    for (int64_t v = 0; v < g->count; ++v) {
        if (child == g->vertices[v]) {
            if (g->used == MAX_EDGES) {
                g->error = 1;
                return;
            }
            g->to[g->used] = v;
            g->next[g->used] = g->head[g->owner];
            g->head[g->owner] = g->used++;
            g->incoming[v]++;
            return;
        }
    }
}

/* The input is a caller-supplied, already validated stopped cohort of
 * resolved objects. Unknown layouts, cap failure or stale epoch refuse
 * without touching the caller's verdict array. */
static int classify(PyObject *const *vertices, int64_t count, int64_t epoch,
                    int64_t slot_budget, int64_t *out) {
    if (count < 0 || count > MAX_VERTICES || epoch < 0
        || pcc_thread_world_stop_epoch_held() != epoch || slot_budget < 0) return 0;
    struct Graph g = {0};
    g.vertices = vertices;
    g.count = count;
    g.remaining = slot_budget;
    for (int64_t owner = 0; owner < count; ++owner) {
        if (!vertices[owner]) return 0;
        for (int64_t before = 0; before < owner; ++before)
            if (vertices[owner] == vertices[before]) return 0;
        g.head[owner] = NO_VERTEX;
    }
    for (int64_t owner = 0; owner < count; ++owner) {
        int64_t cursor = 0;
        g.owner = owner;
        do {
            if (!g.remaining) return 0;
            int64_t limit = g.remaining < SLOT_BATCH ? g.remaining : SLOT_BATCH;
            int64_t state[STATE_WORDS] = {0, 0};
            if (!pcc_gc_visit_object_slots_slice(vertices[owner], cursor, limit,
                                                add_edge, &g, state)
                || g.error || state[EXAMINED] < 0 || state[EXAMINED] > limit
                || (state[NEXT_CURSOR] != DONE && state[NEXT_CURSOR] <= cursor)) return 0;
            g.remaining -= state[EXAMINED];
            cursor = state[NEXT_CURSOR];
        } while (cursor != DONE);
    }
    int64_t ready[MAX_VERTICES], take = 0, append = 0;
    for (int64_t v = 0; v < count; ++v)
        if (!g.incoming[v]) ready[append++] = v;
    while (take < append) {
        int64_t owner = ready[take++];
        for (int64_t e = g.head[owner]; e != NO_VERTEX; e = g.next[e]) {
            int64_t target = g.to[e];
            if (--g.incoming[target] == 0) ready[append++] = target;
        }
    }
    if (pcc_thread_world_stop_epoch_held() != epoch) return 0;
    for (int64_t v = 0; v < count; ++v)
        out[v] = g.incoming[v] ? CYCLICALLY_RETAINED : ACYCLIC_DROP;
    return 1;
}

static PyObject *make_list(void) { return py_list_new(MAX_EDGES + 1); }

int main(void) {
    int64_t backend = pcc_gc_backend();
    if (backend < 1 || backend > 4) return 1;
    py_gc_disable();

    PyObject *plain_owner = make_list(), *plain_child = make_list();
    PyObject *cycle = make_list(), *downstream = make_list();
    PyObject *crowded = make_list();
    if (!plain_owner || !plain_child || !cycle || !downstream || !crowded) return 2;
    py_list_append(plain_owner, plain_child);
    py_list_append(plain_owner, plain_child);
    py_list_append(cycle, cycle);
    py_list_append(cycle, downstream);
    for (int i = 0; i < MAX_EDGES + 1; ++i) py_list_append(crowded, downstream);

    if (pcc_stop_the_world() != 0) return 3;
    int64_t epoch = pcc_thread_world_stop_epoch_held();
    PyObject *vertices[] = {cycle, plain_child, downstream, plain_owner};
    int64_t expected[] = {CYCLICALLY_RETAINED, ACYCLIC_DROP, CYCLICALLY_RETAINED, ACYCLIC_DROP};
    int64_t out[] = {0, 0, 0, 0};
    if (!classify(vertices, 4, epoch, 16, out)) return 4;
    for (int i = 0; i < 4; ++i) if (out[i] != expected[i]) return 5;
    PyObject *overflow[] = {crowded, downstream};
    if (classify(vertices, 4, epoch, 1, out) ||
        classify(overflow, 2, epoch, 64, out)) return 6;
    for (int i = 0; i < 4; ++i) if (out[i] != expected[i]) return 7;
    if (pcc_resume_world() != 0 || classify(vertices, 4, epoch, 16, out)) return 8;
    for (int i = 0; i < 4; ++i) if (out[i] != expected[i]) return 9;
    puts("GC_ACYCLIC_CANDIDATE_PEEL_OK");
    return 0;
}
