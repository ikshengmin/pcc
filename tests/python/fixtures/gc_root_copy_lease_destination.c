/* Owned-C test input, not a production C runtime or counted-mode substitute.
 * The source belongs to a worker's actual stack. Its copied destination is
 * an independently registered malloc slot, and every check follows join.
 * The exported CMS pause establishes this direct-ABI test schedule; this is
 * not qualification of a concurrently running collector.
 */
extern void *malloc(long long);
extern void free(void *);
extern void pcc_platform_abort(void);
extern void *py_str_new(const char *, long long);
extern void *py_list_new(long long);
extern long long pcc_gc_backend(void);
extern long long pcc_threads_enabled(void);
extern long long pcc_current_thread_id(void);
extern long long pcc_thread_live_count(void);
extern long long pcc_gc_cms_stop_worker(void);
extern long long pcc_thread_start(void **, void *(*)(void *), void *);
extern long long pcc_thread_join(void *, void **);
extern void pcc_gc_frame_enter_lifo(void *, void *);
extern void pcc_gc_frame_leave_lifo(void *);
extern long long pcc_gc_stack_slot_root_is_conservative(void *);
extern void *pcc_gc_scheduler_root_register_handle(void *);
extern void pcc_gc_scheduler_root_unregister_handle(void *);
extern void pcc_gc_store_root(void *, void *);
extern void pcc_gc_store_root_take(void *, void *);
extern long long pcc_gc_root_copy_lease(void *, void *);
extern long long pcc_gc_root_copy_lease_prepare_locked(void *, void *, long long, void *);
extern void pcc_gc_root_copy_lease_finish(void *);
extern long long pcc_gc_foreign_lease_acquire(void *);
extern long long pcc_gc_foreign_lease_release(void *, long long);
extern long long pcc_gc_foreign_lease_count_quiescent(void);
extern long long pcc_gc_object_is_address_pinned(void *);
extern void pcc_py_gc_minor_graph_lock(void);
extern void pcc_py_gc_minor_graph_unlock(void);
extern void *pcc_gc_resolve_root_slot_unlocked(void *, long long);
extern void *pcc_gc_object_index_find(void *);
extern long long pcc_gc_install_forwarding(void *, void *);
extern void *pcc_gc_forwarding_find(void *);
extern int pcc_gc_backend4_no_refcounts;
extern int pcc_gc_refcount_provenance_probe;
extern int pcc_thread_conservative_roots_enabled;
extern int pcc_gc_cms_worker_started;

struct CopyContext {
    void *destination;
    void *target;
    long long split;
    long long token;
};

static int source_map = 1;

static void require(int condition) {
    if (!condition) pcc_platform_abort();
}

static long long active_count(void) {
    pcc_py_gc_minor_graph_lock();
    long long count = pcc_gc_foreign_lease_count_quiescent();
    pcc_py_gc_minor_graph_unlock();
    return count;
}

long long root_copy_lease_pause_collector(void) {
    pcc_current_thread_id();
    require(pcc_gc_cms_stop_worker() == 0);
    require(pcc_thread_live_count() == 1);
    return 0;
}

static void *copy_worker(void *opaque) {
    struct CopyContext *context = opaque;
    void *source = 0;
    long long plan[32] = {0};
    pcc_gc_frame_enter_lifo(&source_map, &source);
    require(pcc_gc_stack_slot_root_is_conservative(&source) == 1);
    require(pcc_gc_stack_slot_root_is_conservative(&context->destination) == 0);
    long long backend = pcc_gc_backend();
    if (backend == 3) source = py_str_new("copied", 6);
    else source = py_list_new(0);
    require(source != 0);
    pcc_py_gc_minor_graph_lock();
    /* py_str_new uses pcc_gc_alloc and the GC3 allocation note tracks it.
     * py_list_new uses the managed allocator, tracks its object and clears
     * FRESH_ALLOC after publishing its initialized payload. Verify those
     * real constructor outcomes rather than manufacturing header flags. */
    require(pcc_gc_object_index_find(source) != 0);
    int flags = *((int *)((char *)source + 12));
    require((flags & (64 | 2048 | 8192 | 16384 | 524288)) == 0);
    pcc_py_gc_minor_graph_unlock();
    /* A fresh, same-type and same-payload target is private to this fixture.
     * Public forwarding accepts both scalar strings (GC3) and lists (GC4).
     * Root publication consumes the constructor's owned result. */
    if (backend == 3) {
        pcc_gc_store_root_take(&context->target, py_str_new("copied", 6));
    } else {
        pcc_gc_store_root_take(&context->target, py_list_new(0));
    }
    require(context->target != 0 && context->target != source);
    /* Prove the fast no-op source branch is really selected. Never change
     * no-refcount/probe globals, object flags or a collector's mode here. */
    long long source_token = pcc_gc_foreign_lease_acquire(&source);
    require(source_token == 0);
    require(pcc_gc_foreign_lease_release(&source, source_token) == 0);
    if (context->split) {
        pcc_py_gc_minor_graph_lock();
        context->token = pcc_gc_root_copy_lease_prepare_locked(
            &context->destination, &source, 0, plan);
        pcc_py_gc_minor_graph_unlock();
        pcc_gc_root_copy_lease_finish(plan);
    } else {
        context->token = pcc_gc_root_copy_lease(&context->destination, &source);
    }
    pcc_gc_store_root(&source, 0);
    pcc_gc_frame_leave_lifo(&source);
    return 0;
}

long long root_copy_lease_heap_destination(long long split) {
    long long backend = pcc_gc_backend();
    require(backend == 3 || backend == 4);
    require(pcc_threads_enabled() != 0);
    require(pcc_gc_backend4_no_refcounts == 1);
    require(pcc_gc_refcount_provenance_probe == 0);
    require(pcc_thread_conservative_roots_enabled == 1);
    require(pcc_gc_cms_worker_started == 4);
    require(pcc_thread_live_count() == 1);
    long long baseline = active_count();
    struct CopyContext *context = malloc(sizeof(struct CopyContext));
    require(context != 0);
    context->destination = 0;
    context->target = 0;
    context->split = split;
    context->token = -99;
    void *registration = pcc_gc_scheduler_root_register_handle(&context->destination);
    require(registration != 0);
    void *target_registration = pcc_gc_scheduler_root_register_handle(&context->target);
    require(target_registration != 0);
    require(pcc_gc_stack_slot_root_is_conservative(&context->destination) == 0);
    void *thread = 0;
    void *result = 0;
    require(pcc_thread_start(&thread, copy_worker, context) == 0);
    require(pcc_thread_join(thread, &result) == 0);
    require(pcc_thread_live_count() == 1);
    /* The source thread has exited. Its stack is no longer a lifetime or
     * address guarantee for the registered destination's returned token. */
    require(context->token == 1);
    /* Keep resolve, both public installs, release and healing inside one
     * outer graph transaction. A nested unlock cannot park and conservatively
     * pin the observer's temporary raw source between release and install. */
    pcc_py_gc_minor_graph_lock();
    require(pcc_gc_foreign_lease_count_quiescent() == baseline + 1);
    void *before = pcc_gc_resolve_root_slot_unlocked(&context->destination, 0);
    require(before != 0);
    require(pcc_gc_object_is_address_pinned(before) == 1);
    void *source_node = pcc_gc_object_index_find(before);
    require(source_node != 0 && *((long long *)((char *)source_node + 80)) == 1);
    require(pcc_gc_object_index_find(context->target) != 0);
    require(*((int *)((char *)before + 8)) == *((int *)((char *)context->target + 8)));
    require(pcc_gc_forwarding_find(before) == 0);
    require(pcc_gc_install_forwarding(before, context->target) == -2);
    require(context->destination == before);
    require(pcc_gc_foreign_lease_release(&context->destination, context->token) == 0);
    require(pcc_gc_foreign_lease_count_quiescent() == baseline);
    require(*((long long *)((char *)source_node + 80)) == 0);
    require(pcc_gc_object_is_address_pinned(context->destination) == 0);
    /* GC3 may legally promote the held object in place. Releasing its
     * lease must readmit the public forwarding operation even when it is
     * now OLD; a second young-only oldify_copy would not prove that. */
    require(pcc_gc_install_forwarding(before, context->target) == 0);
    void *edge = pcc_gc_forwarding_find(before);
    require(edge != 0 && ((void **)edge)[1] == context->target);
    void *healed = pcc_gc_resolve_root_slot_unlocked(&context->destination, 0);
    require(healed == context->target && healed != before);
    pcc_py_gc_minor_graph_unlock();
    pcc_gc_store_root(&context->destination, 0);
    pcc_gc_store_root(&context->target, 0);
    pcc_gc_scheduler_root_unregister_handle(registration);
    pcc_gc_scheduler_root_unregister_handle(target_registration);
    free(context);
    return 0;
}
