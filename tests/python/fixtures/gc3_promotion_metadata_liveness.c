/* Internal slot-contract regression, not an organic workload or QPS probe.
 * The mark-active state is deliberately injected under STW/graph exclusion.
 * Objects, slot walking, copying and forwarding use production runtime code.
 */
#include "py_runtime.h"
#include <stdint.h>
#include <stdio.h>
#include <string.h>

extern void pcc_gc_generational_promote_slot(void *, int64_t, void *);
extern void pcc_gc_generational_promote_shallow_slot(void *, int64_t, void *);
extern int64_t pcc_gc_visit_object_slots(PyObject *, void *, void *);
extern PyObject *py_class_new(const char *, void *, int32_t, void *, int32_t);
extern void py_class_add_method(PyObject *, const char *, PyObject *);
extern PyObject *py_class_lookup(PyObject *, const char *);
extern void pcc_gc_backend4_remap_referents(PyObject *);
extern int32_t pcc_gc_mark_active, pcc_gc_forwarding_population;
extern int64_t pcc_gc_gray_count_load_acquire(void);
extern void pcc_gc_set_mark_color(PyObject *, int64_t);

static int shallow;
static PyObject *alias_source, *alias_target;
static int stale_aliases, healed_aliases;
static void count_aliases(void *slot, int64_t role, void *context) {
    (void)context;
    if (role != 3) return;
    if (*((PyObject **)slot) == alias_source) stale_aliases++;
    if (*((PyObject **)slot) == alias_target) healed_aliases++;
}
static void visit(void *slot, int64_t role) {
    if (shallow) pcc_gc_generational_promote_shallow_slot(slot, role, NULL);
    else pcc_gc_generational_promote_slot(slot, role, NULL);
}
static void walk(PyObject *owner) {
    if (shallow) pcc_gc_visit_object_slots(owner, pcc_gc_generational_promote_shallow_slot, NULL);
    else pcc_gc_visit_object_slots(owner, pcc_gc_generational_promote_slot, NULL);
}
static int flags(PyObject *obj) { return ((int32_t *)obj)[3]; }
static int64_t refs(PyObject *obj) { return *((int64_t *)obj); }

int main(int argc, char **argv) {
    if (argc != 3 || pcc_gc_set_backend(3) != 0) return 1;
    shallow = strcmp(argv[1], "shallow") == 0;
    py_gc_disable();
    if (pcc_gc_collect(1) != 0) return 2;
    pcc_thread_no_park_enter();
    PyObject *source = py_str_new("metadata", 8);
    PyObject *middle = py_str_new("middle", 6);
    PyObject *terminal = py_str_new("terminal", 8);
    /* The runtime weakref API accepts scalar targets; this verifies its slot
     * contract, not CPython's language-level weak-referenceability policy. */
    PyObject *weak = py_weakref_new(source, NULL);
    PyObject *cls = py_class_new("MetadataOwner", NULL, 0, NULL, 0);
    if (strcmp(argv[2], "late-alias") == 0 && cls && source) {
        py_class_add_method(cls, "m", source);
        py_class_add_method(cls, "__del__", source);
    }
    pcc_thread_no_park_exit();
    if (!source || !middle || !terminal || !weak || !cls) return 3;
    if (pcc_stop_the_world() != 0) return 4;
    pcc_py_gc_minor_graph_lock();
    if (pcc_gc_mark_active != 0) return 5;
    if (!(flags(source) & 128) || !(flags(cls) & 128)) return 6;
    pcc_gc_set_mark_color(source, 8);
    pcc_gc_mark_active = 1;
    PyObject *slot = source;
    int before_flags = flags(source);
    int64_t before_refs = refs(source), before_gray = pcc_gc_gray_count_load_acquire();
    int before_forward = pcc_gc_forwarding_population;
    int good = 0;
    if (strcmp(argv[2], "direct") == 0) {
        visit(&slot, 3);
        good = slot == source && flags(source) == before_flags && refs(source) == before_refs;
    } else if (strcmp(argv[2], "weak") == 0) {
        walk(weak);
        good = ((PyObject **)weak)[2] == source && flags(source) == before_flags && refs(source) == before_refs;
    } else if (strcmp(argv[2], "class") == 0) {
        /* A real class MRO includes a borrowed alias to the class itself.
         * That derived metadata must not promote the owner on its own. */
        before_flags = flags(cls);
        before_refs = refs(cls);
        walk(cls);
        good = flags(cls) == before_flags && refs(cls) == before_refs;
    } else if (strcmp(argv[2], "chain") == 0) {
        if (pcc_gc_install_forwarding(source, middle) != 0 ||
            pcc_gc_install_forwarding(middle, terminal) != 0) return 7;
        before_flags = flags(terminal);
        before_refs = refs(terminal);
        before_gray = pcc_gc_gray_count_load_acquire();
        before_forward = pcc_gc_forwarding_population;
        visit(&slot, 3);
        good = slot == terminal && flags(terminal) == before_flags && refs(terminal) == before_refs;
    } else if (strcmp(argv[2], "late-alias") == 0) {
        /* add_method borrows; the caller-owned slot supplies the strong
         * reference. Metadata visited first must not move its child. */
        walk(cls);
        good = flags(source) == before_flags && refs(source) == before_refs;
        good = good && pcc_gc_forwarding_population == before_forward;
        visit(&slot, 1);
        good = good && slot != source && py_class_lookup(cls, "m") == slot;
        good = good && py_class_lookup(cls, "__del__") == slot;
        /* Lookup heals selected method aliases; the shared remap visitor
         * heals every alias before the existing retirement cut can free a
         * source. This is not a promise of eager healing at nursery refill. */
        pcc_gc_backend4_remap_referents(cls);
        alias_source = source;
        alias_target = slot;
        stale_aliases = 0;
        healed_aliases = 0;
        pcc_gc_visit_object_slots(cls, count_aliases, NULL);
        good = good && stale_aliases == 0 && healed_aliases == 3;
        before_forward++;
        before_gray++;
    } else {
        int role = strcmp(argv[2], "strong1") == 0 ? 1 : 2;
        visit(&slot, role);
        good = slot != source && (flags(slot) & 256) && (flags(slot) & 16) && !(flags(slot) & 128);
        before_forward++;
        before_gray++;
    }
    good = good && pcc_gc_forwarding_population == before_forward && pcc_gc_gray_count_load_acquire() == before_gray;
    printf("METADATA adapter=%s case=%s good=%d forwarding=%d gray=%lld\n",
           argv[1], argv[2], good, pcc_gc_forwarding_population, (long long)pcc_gc_gray_count_load_acquire());
    pcc_gc_mark_active = 0;
    pcc_py_gc_minor_graph_unlock();
    pcc_resume_world();
    return good ? 0 : 20;
}
