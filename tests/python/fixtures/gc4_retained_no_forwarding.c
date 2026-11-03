/* Native ABI regression: link an already-built PCC runtime, no runtime build.
 * Run with PCC_GC_BACKEND=4 PCC_GC_TRACING_ONLY=1, then repeat tracing-only=0.
 * This intentionally allocates/frees zero-field raw list objects through the
 * runtime allocation API. It performs no relocation and checks that invariant.
 */
#include "py_runtime.h"
#include <stdint.h>
#include <stdio.h>

extern void pcc_gc_free_object_memory(PyObject *object);
extern void *pcc_gc_backend4_retained_page_head;
extern void *pcc_gc_backend4_parked_head;

static int64_t queue_count(void *page) {
    int64_t n = 0;
    while (page != 0 && n < 1000000) {
        n++;
        page = *(void **)((unsigned char *)page + 56);
    }
    return n;
}

int main(void) {
    if (pcc_gc_set_backend(PCC_GC_KIND_COLORED_RELOCATING) != 0) return 2;
    py_gc_disable();
    if (pcc_gc_telemetry(PCC_GC_COUNTER_FORWARDING_ENTRIES) != 0) return 3;
    PyObject *pinned = pcc_gc_alloc(128, PY_TYPE_LIST, 0);
    PyObject *leased = pcc_gc_alloc(128, PY_TYPE_LIST, 0);
    if (pinned == 0 || leased == 0) return 4;
    pcc_gc_publish_initialized(pinned);
    pcc_gc_publish_initialized(leased);
    pcc_gc_pin(pinned);
    int64_t lease = pcc_gc_foreign_lease_acquire(&leased);
    if (lease <= 0) return 5;
    (void)pcc_gc_collect(0);
    if (pcc_gc_backend4_zpage_owner_offset_bytes(pinned) < 0) return 6;
    if (pcc_gc_backend4_zpage_owner_offset_bytes(leased) < 0) return 7;
    pcc_gc_unpin(pinned);
    if (pcc_gc_foreign_lease_release(&leased, lease) < 0) return 8;
    pcc_gc_free_object_memory(pinned);
    pcc_gc_free_object_memory(leased);
    pinned = 0;
    leased = 0;
    (void)pcc_gc_collect(0);

    int64_t initial = pcc_os_heap_in_use_bytes();
    int64_t after_first = 0;
    int64_t maximum = initial;
    PyObject *objects[512];
    for (int round = 0; round < 32; round++) {
        for (int i = 0; i < 512; i++) {
            objects[i] = pcc_gc_alloc(4096, PY_TYPE_LIST, 0);
            if (objects[i] == 0) return 10;
            pcc_gc_publish_initialized(objects[i]);
        }
        for (int i = 0; i < 512; i++) {
            pcc_gc_free_object_memory(objects[i]);
            objects[i] = 0;
        }
        if (pcc_gc_telemetry(PCC_GC_COUNTER_FORWARDING_ENTRIES) != 0) return 11;
        (void)pcc_gc_collect(0);
        int64_t retained = queue_count(pcc_gc_backend4_retained_page_head);
        int64_t parked = queue_count(pcc_gc_backend4_parked_head);
        int64_t requested = pcc_os_heap_in_use_bytes();
        if (requested > maximum) maximum = requested;
        if (retained != 0 || parked != 0) {
            printf("retained-regression round=%d retained=%lld parked=%lld requested=%lld\n",
                   round, (long long)retained, (long long)parked, (long long)requested);
            return 21;
        }
        if (round == 0) after_first = requested;
        if (requested > initial + 8388608 || requested > after_first + 1048576) return 22;
        if (pcc_gc_backend4_zpage_free_pages() > 12) return 23;
        if (pcc_gc_telemetry(PCC_GC_COUNTER_FORWARDING_ENTRIES) != 0) return 24;
    }
    printf("retained-no-forwarding-ok rounds=32 objects=16384 initial=%lld after_first=%lld final=%lld peak_after_collect=%lld\n",
           (long long)initial, (long long)after_first, (long long)pcc_os_heap_in_use_bytes(),
           (long long)maximum);
    return 0;
}
