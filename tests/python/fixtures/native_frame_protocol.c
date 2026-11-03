/* Contract: docs/project-intent.md, 5-GC Production Equality Rule.
 * Exact descriptor/range execution; no conservative scan can satisfy these
 * address, role, mutation and count checks. Emit with PCC's owned C frontend.
 */
#include "py_runtime.h"
#include <stdint.h>
#include <stdio.h>

typedef struct NativeRange { void **slots; int64_t count; } NativeRange;
typedef struct NativeFrame {
    struct NativeFrame *previous;
    NativeRange *ranges;
    int64_t used;
    int64_t capacity;
    int64_t owner;
} NativeFrame;
typedef struct Seen { int64_t frames; int64_t slots; int64_t borrowed; } Seen;
extern int64_t pcc_gc_native_frame_enter(void *, void *, int64_t);
extern int64_t pcc_gc_native_frame_leave(void *);
extern void *pcc_gc_native_root_bind_range(void *, int64_t);
extern void pcc_gc_native_root_unbind(void *);
extern int64_t pcc_thread_native_root_contains(void *);
extern int64_t pcc_thread_visit_native_root_frames(void *, void *);
extern int64_t pcc_gc_root_registry_revision;

static void visit(void *raw_frame, void *raw_seen) {
    NativeFrame *frame = (NativeFrame *)raw_frame;
    Seen *seen = (Seen *)raw_seen;
    seen->frames += 1;
    for (int64_t i = 0; i < frame->used; i += 1) {
        NativeRange *range = &frame->ranges[i];
        if (!range->slots) continue;
        int64_t n = range->count;
        if (n < 0) { n = -n; seen->borrowed += n; }
        seen->slots += n;
        /* The test requires the actual native range cells to be writable. */
        for (int64_t j = 0; j < n; j += 1) range->slots[j] = (void *)17;
    }
}

int main(int argc, char **argv) {
    if (sizeof(NativeFrame) != 40 || sizeof(NativeRange) != 16) return 1;
    (void)pcc_gc_backend();  /* Admit the explicitly selected GC/mode first. */
    pcc_current_thread_id();
    NativeFrame outer = {0}, inner = {0};
    NativeRange ranges[3], nested[1];
    void *a[2] = {(void *)1, (void *)3};
    void *b[1] = {(void *)5};
    int64_t revision = pcc_gc_root_registry_revision;
    pcc_thread_no_park_enter();
    if (pcc_gc_native_frame_enter(&outer, ranges, 3) != 0) return 2;
    void *first = pcc_gc_native_root_bind_range(a, -2);
    void *second = pcc_gc_native_root_bind_range(b, 1);
    if (!first || !second) return 3;
    if (pcc_gc_root_registry_revision != revision) return 4;
    pcc_thread_no_park_exit();
    if (!pcc_thread_native_root_contains(&a[1])) return 5;
    if (!pcc_thread_native_root_contains(b)) return 6;
    pcc_thread_no_park_enter();
    if (pcc_gc_native_frame_enter(&inner, nested, 1) != 0) return 7;
    pcc_thread_no_park_exit();
    if (argc > 1 && argv[1][0] == 'l') {
        pcc_gc_native_frame_leave(&outer); /* Must fail-stop: non-top pop. */
        return 40;
    }
    if (argc > 1 && argv[1][0] == 'u') {
        pcc_thread_unregister_current(); /* Must not free active descriptors. */
        return 41;
    }
    if (argc > 1 && argv[1][0] == 'e') {
        pcc_thread_blocking_enter(__builtin_frame_address(0));
        pcc_thread_unregister_current(); /* Exited is not running admission. */
        return 42;
    }
    pcc_gc_native_frame_leave(&inner);
    /* Clear first before second: groups are deliberately non-LIFO. */
    pcc_gc_native_root_unbind(first);
    if (pcc_thread_native_root_contains(a)) return 8;
    void *reused = pcc_gc_native_root_bind_range(a, -2);
    if (reused != first) return 9;
    Seen seen = {0};
    if (pcc_stop_the_world() != 0) return 10;
    if (pcc_thread_visit_native_root_frames((void *)visit, &seen) != 1) return 11;
    if (pcc_resume_world() != 0) return 12;
    if (seen.frames != 1 || seen.slots != 3 || seen.borrowed != 2) return 13;
    if (a[0] != (void *)17 || a[1] != (void *)17 || b[0] != (void *)17) return 14;
    pcc_gc_native_root_unbind(second);
    pcc_gc_native_root_unbind(reused);
    if (pcc_gc_native_frame_leave(&outer) != 0) return 15;
    if (pcc_thread_native_root_contains(a)) return 16;
    if (pcc_gc_root_registry_revision != revision) return 17;
    seen.frames = 0;
    if (pcc_stop_the_world() != 0) return 18;
    if (pcc_thread_visit_native_root_frames((void *)visit, &seen) != 0) return 19;
    pcc_resume_world();
    if (seen.frames != 0) return 20;
    pcc_thread_unregister_current();
    puts("native-frame-protocol-ok");
    return 0;
}
