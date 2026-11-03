/* Exact other-thread native roots across an original blocking token.
 * No managed stack map or conservative snapshot can perform these writes.
 * Contract: docs/project-intent.md shared GC slot/root contract; registered
 * native blocking activations remain live until blocking_leave and pop.
 */
#include "py_runtime.h"
#include <stdint.h>
#include <stdio.h>

typedef struct NativeRange { void **slots; int64_t count; } NativeRange;
typedef struct NativeFrame {
    struct NativeFrame *previous;
    NativeRange *ranges;
    int64_t used, capacity, owner;
} NativeFrame;
typedef struct Shared {
    int64_t ready, release_wait, leaving, resumed;
    NativeFrame *frame;
    void **owned_slot;
    void **borrowed_slots;
    int64_t owner_thread;
} Shared;
typedef struct Visit {
    Shared *shared;
    int64_t phase, frames, slots, error;
} Visit;

extern int64_t pcc_gc_native_frame_enter(void *, void *, int64_t);
extern int64_t pcc_gc_native_frame_leave(void *);
extern void *pcc_gc_native_root_bind_range(void *, int64_t);
extern void pcc_gc_native_root_unbind(void *);
extern int64_t pcc_thread_visit_native_root_frames(void *, void *);
extern int64_t pcc_thread_start(void *, void *, void *);
extern int64_t pcc_thread_join(void *, void *);
extern int64_t pcc_thread_is_running(void);
extern void pcc_platform_abort(void);
extern int sched_yield(void);
extern int32_t pcc_thread_conservative_roots_enabled;

static void visit(void *raw_frame, void *raw_context) {
    NativeFrame *frame = (NativeFrame *)raw_frame;
    Visit *context = (Visit *)raw_context;
    Shared *shared = context->shared;
    context->frames += 1;
    if (frame != shared->frame || frame->owner != shared->owner_thread ||
        frame->used != 2 || frame->capacity != 2) {
        context->error = 1;
        return;
    }
    if (frame->ranges[0].slots != shared->owned_slot || frame->ranges[0].count != 1 ||
        frame->ranges[1].slots != shared->borrowed_slots || frame->ranges[1].count != -2) {
        context->error = 2;
        return;
    }
    context->slots += 3;
    if (context->phase == 0) {
        if (shared->owned_slot[0] != (void *)33 || shared->borrowed_slots[0] != (void *)35 ||
            shared->borrowed_slots[1] != (void *)37) context->error = 3;
        shared->owned_slot[0] = (void *)65;
        shared->borrowed_slots[0] = (void *)67;
        shared->borrowed_slots[1] = (void *)69;
    } else {
        if (shared->owned_slot[0] != (void *)65 || shared->borrowed_slots[0] != (void *)67 ||
            shared->borrowed_slots[1] != (void *)69) context->error = 4;
    }
}

static void *worker(void *argument) {
    Shared *shared = (Shared *)argument;
    NativeFrame frame = {0};
    NativeRange ranges[2];
    void *owned[1] = {(void *)33};
    void *borrowed[2] = {(void *)35, (void *)37};
    pcc_thread_no_park_enter();
    if (pcc_gc_native_frame_enter(&frame, ranges, 2) != 0) pcc_platform_abort();
    void *owned_token = pcc_gc_native_root_bind_range(owned, 1);
    void *borrowed_token = pcc_gc_native_root_bind_range(borrowed, -2);
    if (!owned_token || !borrowed_token) pcc_platform_abort();
    pcc_thread_no_park_exit();
    shared->frame = &frame;
    shared->owned_slot = owned;
    shared->borrowed_slots = borrowed;
    shared->owner_thread = pcc_current_thread_id();
    int64_t entered = pcc_thread_blocking_enter(__builtin_frame_address(0));
    if (entered != 1) pcc_platform_abort();
    __atomic_store_n(&shared->ready, 1, __ATOMIC_RELEASE);
    /* Only raw synchronization/platform calls while exited. No managed/root
     * cell is read until this exact original token has restored admission. */
    while (!__atomic_load_n(&shared->release_wait, __ATOMIC_ACQUIRE)) sched_yield();
    __atomic_store_n(&shared->leaving, 1, __ATOMIC_RELEASE);
    pcc_thread_blocking_leave(entered);
    __atomic_store_n(&shared->resumed, 1, __ATOMIC_RELEASE);
    int64_t result = 0;
    if (pcc_thread_is_running() != 1) result = 20;
    if (owned[0] != (void *)65 || borrowed[0] != (void *)67 || borrowed[1] != (void *)69) result = 21;
    pcc_gc_native_root_unbind(owned_token);  /* Clearing need not be LIFO. */
    pcc_gc_native_root_unbind(borrowed_token);
    if (pcc_gc_native_frame_leave(&frame) != 0) result = 22;
    return (void *)result; /* Trampoline unregisters after this scope exits. */
}

int main(void) {
    if (pcc_gc_set_backend(0) != 0) return 1;
    pcc_current_thread_id();
    if (pcc_thread_conservative_roots_enabled != 0) return 2;
    Shared shared = {0};
    void *thread = 0;
    if (pcc_thread_start(&thread, (void *)worker, &shared) != 0) return 3;
    while (!__atomic_load_n(&shared.ready, __ATOMIC_ACQUIRE)) pcc_thread_safepoint();
    if (shared.owner_thread == pcc_current_thread_id()) return 4;
    if (pcc_stop_the_world() != 0) return 5;
    Visit first = {&shared, 0, 0, 0, 0};
    if (pcc_thread_visit_native_root_frames((void *)visit, &first) != 1) return 6;
    if (first.frames != 1 || first.slots != 3 || first.error) return 7;
    __atomic_store_n(&shared.release_wait, 1, __ATOMIC_RELEASE);
    while (!__atomic_load_n(&shared.leaving, __ATOMIC_ACQUIRE)) sched_yield();
    if (__atomic_load_n(&shared.resumed, __ATOMIC_ACQUIRE)) return 8;
    /* The worker is attempting its original blocking_leave. Its frame must
     * remain published and writable until this stopped epoch is released. */
    Visit second = {&shared, 1, 0, 0, 0};
    if (pcc_thread_visit_native_root_frames((void *)visit, &second) != 1) return 9;
    if (second.frames != 1 || second.slots != 3 || second.error) return 10;
    if (__atomic_load_n(&shared.resumed, __ATOMIC_ACQUIRE)) return 11;
    if (pcc_resume_world() != 0) return 12;
    void *result = 0;
    if (pcc_thread_join(thread, &result) != 0 || result != 0) return 13;
    if (!__atomic_load_n(&shared.resumed, __ATOMIC_ACQUIRE)) return 14;
    if (pcc_thread_live_count() != 1) return 15;
    if (pcc_stop_the_world() != 0) return 16;
    Visit empty = {&shared, 1, 0, 0, 0};
    if (pcc_thread_visit_native_root_frames((void *)visit, &empty) != 0 || empty.frames != 0) return 17;
    pcc_resume_world();
    pcc_thread_unregister_current();
    puts("native-frame-parked-ok");
    return 0;
}
