#include "../src/py_internal.h"
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>

enum { ROOT_SOURCE, ROOT_DESTINATION, ROOT_WEAK, ROOT_CLASS, ROOT_COUNT };

typedef struct {
    PyObject *roots[ROOT_COUNT];
    void *handles[ROOT_COUNT];
    int64_t identity;
    int borrowed;
} CopyLifetime;

static int64_t finalized;
static const char *field_names[] = {"payload"};

static void probe_del(PyObject *self) {
    (void)self;
    __atomic_add_fetch(&finalized, 1, __ATOMIC_ACQ_REL);
}

static void *produce_copy(void *argument) {
    CopyLifetime *probe = (CopyLifetime *)argument;
    PyClassObject *cls = py_class_new("PrivateCopyLifetime", 0, 0, field_names, 1);
    if (!cls) return (void *)(uintptr_t)30;
    pcc_gc_store_root(&probe->roots[ROOT_CLASS], (PyObject *)cls);
    py_class_add_method(cls, "__del__", (PyObject *)(uintptr_t)probe_del);
    PyObject *value = py_instance_new(cls);
    if (!value) return (void *)(uintptr_t)31;
    pcc_gc_store_root(&probe->roots[ROOT_SOURCE], value);
    PyObject *payload = py_str_new("private root copy", 17);
    if (!payload) return (void *)(uintptr_t)32;
    py_instance_set_field((PyInstanceObject *)value, 0, payload);
    PyObject *weak = py_weakref_new(value, 0);
    if (!weak) return (void *)(uintptr_t)33;
    pcc_gc_store_root(&probe->roots[ROOT_WEAK], weak);
    probe->identity = pcc_gc_object_id(value);
    int64_t leases = pcc_gc_foreign_lease_count_quiescent();
    /* These unmanaged cells are private to this activation during copy.
     * Only after joining this producer may another thread use the block. */
    if (pcc_gc_root_copy_local(&probe->roots[ROOT_DESTINATION],
                              &probe->roots[ROOT_SOURCE], probe->borrowed) != 0)
        return (void *)(uintptr_t)34;
    if (pcc_gc_foreign_lease_count_quiescent() != leases)
        return (void *)(uintptr_t)35;
    if (probe->roots[ROOT_DESTINATION] != probe->roots[ROOT_SOURCE])
        return (void *)(uintptr_t)36;
    pcc_gc_store_root(&probe->roots[ROOT_SOURCE], 0);
    pcc_gc_release(weak);
    pcc_gc_release(payload);
    pcc_gc_release(value);
    pcc_gc_release((PyObject *)cls);
    /* The runtime trampoline unregisters this thread. No raw target value
     * reaches main, including via the thread result. */
    return 0;
}

static void *read_and_drop_copy(void *argument) {
    CopyLifetime *probe = (CopyLifetime *)argument;
    PyObject *value = pcc_gc_load_ptr(0, &probe->roots[ROOT_DESTINATION]);
    if (!value || pcc_gc_object_id(value) != probe->identity)
        return (void *)(uintptr_t)37;
    if (probe->roots[ROOT_SOURCE] != 0)
        return (void *)(uintptr_t)38;
    PyObject *weak = pcc_gc_load_ptr(0, &probe->roots[ROOT_WEAK]);
    PyObject *target = py_weakref_call(weak);
    int same_target = target == value;
    pcc_gc_release(target);
    if (!same_target)
        return (void *)(uintptr_t)39;
    PyObject *payload = py_instance_get_field((PyInstanceObject *)value, 0);
    if (!payload || py_str_byte_len(payload) != 17)
        return (void *)(uintptr_t)40;
    for (int i = 0; i < 17; i++) {
        if (py_str_byte_at_i64(payload, i) != "private root copy"[i])
            return (void *)(uintptr_t)41;
    }
    pcc_gc_release(payload);
    if (__atomic_load_n(&finalized, __ATOMIC_ACQUIRE) != 0)
        return (void *)(uintptr_t)42;
    pcc_gc_store_root(&probe->roots[ROOT_DESTINATION], 0);
    return 0;
}

static void *check_dead_copy(void *argument) {
    CopyLifetime *probe = (CopyLifetime *)argument;
    PyObject *weak = pcc_gc_load_ptr(0, &probe->roots[ROOT_WEAK]);
    /* Even a failed check runs on a disposable thread: its temporary strong
     * result must not conservatively keep the target alive on the next cut. */
    PyObject *target = py_weakref_call(weak);
    int dead = target == py_None;
    pcc_gc_release(target);
    return (void *)(uintptr_t)(dead ? 0 : 43);
}

static int run_worker(PccThreadMain entry, CopyLifetime *probe) {
    PccThreadHandle *thread = 0;
    void *result = 0;
    if (pcc_thread_start(&thread, entry, probe) != 0) return 44;
    if (pcc_thread_join(thread, &result) != 0) return 45;
    return (int)(uintptr_t)result;
}

static int check_traced_lifetime(int borrowed) {
    CopyLifetime *probe = calloc(1, sizeof(CopyLifetime));
    if (!probe) return 46;
    probe->borrowed = borrowed;
    __atomic_store_n(&finalized, 0, __ATOMIC_RELEASE);
    for (int i = 0; i < ROOT_COUNT; i++) {
        probe->handles[i] = pcc_gc_scheduler_root_register_handle(&probe->roots[i]);
        if (!probe->handles[i]) return 47;
    }
    int status = run_worker(produce_copy, probe);
    if (status != 0) return status;
    pcc_gc_collect(0);
    /* source is empty and the producer stack is gone: destination is the
     * only strong instance root. Main never loads the instance itself. */
    status = run_worker(read_and_drop_copy, probe);
    if (status != 0) return status;
    for (int attempt = 0; attempt < 8; attempt++) {
        pcc_gc_collect(0);
        status = run_worker(check_dead_copy, probe);
        if (status == 0) break;
        if (status != 43) return status;
    }
    if (status != 0) return status;
    if (__atomic_load_n(&finalized, __ATOMIC_ACQUIRE) != 1) return 48;
    /* Another collection must not run __del__ a second time. */
    pcc_gc_collect(0);
    if (__atomic_load_n(&finalized, __ATOMIC_ACQUIRE) != 1) return 49;
    for (int i = 0; i < ROOT_COUNT; i++) {
        pcc_gc_store_root(&probe->roots[i], 0);
        pcc_gc_scheduler_root_unregister_handle(probe->handles[i]);
    }
    free(probe);
    return 0;
}

static void *check_copy_controls(void *argument) {
    CopyLifetime *probe = (CopyLifetime *)argument;
    int backend = (int)pcc_gc_backend();
    int borrowed = probe->borrowed;
    PyObject *source = py_str_new("private root copy", 17);
    PyObject *destination = 0;
    if (!source) return (void *)(uintptr_t)11;
    void *source_handle = pcc_gc_scheduler_root_register_handle(&source);
    void *destination_handle = pcc_gc_scheduler_root_register_handle(&destination);
    if (!source_handle || !destination_handle) return (void *)(uintptr_t)12;
    int64_t refs = ((PyObjectHeader *)source)->refcount;
    int64_t leases = pcc_gc_foreign_lease_count_quiescent();
    if (pcc_gc_root_copy_local(&source, &source, borrowed) != -1) return (void *)(uintptr_t)13;
    if (pcc_gc_root_copy_local(&destination, &source, 2) != -1) return (void *)(uintptr_t)14;
    if (pcc_gc_root_copy_local(&destination, &source, borrowed) != 0) return (void *)(uintptr_t)15;
    if (destination != source) return (void *)(uintptr_t)16;
    if (backend == 0 && ((PyObjectHeader *)source)->refcount != refs + 1) return (void *)(uintptr_t)17;
    if (pcc_gc_foreign_lease_count_quiescent() != leases) return (void *)(uintptr_t)18;
    if (pcc_gc_root_copy_local(&destination, &source, borrowed) != -4) return (void *)(uintptr_t)19;
    if (backend == 0 && ((PyObjectHeader *)source)->refcount != refs + 1) return (void *)(uintptr_t)20;
    /* Only rooted owners survive the call; no address lease should
       prevent an otherwise legal same-backend reset. */
    if (pcc_gc_set_backend(backend) != 0) return (void *)(uintptr_t)21;
    pcc_gc_collect(0);
    if (destination != source) return (void *)(uintptr_t)22;
    pcc_gc_store_root(&destination, 0);
    if (backend == 0 && ((PyObjectHeader *)source)->refcount != refs) return (void *)(uintptr_t)23;
    pcc_gc_store_root(&source, 0);
    pcc_gc_scheduler_root_unregister_handle(destination_handle);
    pcc_gc_scheduler_root_unregister_handle(source_handle);
    return 0;
}

int main(void) {
    /* Collector selection is immutable after the first managed allocation.
     * pytest launches a fresh process for each PCC_GC_BACKEND value. */
    int backend = (int)pcc_gc_backend();
    if (pcc_gc_set_backend(backend) != 0) return 10;
    for (int borrowed = 0; borrowed < 2; borrowed++) {
        CopyLifetime controls = {0};
        controls.borrowed = borrowed;
        /* Even the string control runs on a disposable stack. A stale
         * address from it must not pin a later recycled instance address. */
        int status = run_worker(check_copy_controls, &controls);
        if (status != 0) return status;
        if (backend != 0) {
            status = check_traced_lifetime(borrowed);
            if (status != 0) return status;
        }
    }
    puts("PRIVATE_ROOT_COPY_OK");
    return 0;
}
