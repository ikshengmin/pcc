/* C is intentional: the current C++ frontend does not admit the void** ABI.
 * This is a raw POD-buffer consumer, not constructors/new/delete or GC proof.
 */
#include "pcc_shared_raw.h"

void *malloc(uint64_t size);
void free(void *ptr);
void *pcc_allocator_alloc_object(int64_t size);
int64_t pcc_allocator_live_requested_bytes(void);
int32_t pcc_errno_get(void);
void pcc_errno_set(int32_t value);
int64_t raw_python_mutate(void *buffer, int64_t count);

struct Pair { int64_t first; int64_t second; };

/* Multiplication belongs at the count-to-byte boundary: the raw ABI takes
 * byte sizes and never sees an already-overflowed language expression. */
int32_t raw_array(uint64_t count, uint64_t width, void **out) {
    if (width != 0 && count > 18446744073709551615ULL / width)
        return PCC_RT_RAW_OVERFLOW;
    return pcc_rt_raw_alloc(count * width, 16, 0, out);
}

int raw_test_entry(void) {
    int64_t before = pcc_allocator_live_requested_bytes();
    uint64_t alignment = 1;
    void *p = (void *)73;
    void *q = (void *)79;
    void *saved;
    int i;
    pcc_errno_set(37);
    while (alignment <= 4096) {
        p = (void *)73;
        if (pcc_rt_raw_alloc(33, alignment, PCC_RT_RAW_ZERO, &p) != 0) return 1;
        if (p == 0 || ((uint64_t)p & (alignment - 1)) != 0) return 2;
        for (i = 0; i < 33; i = i + 1)
            if (((unsigned char *)p)[i] != 0) return 3;
        if (pcc_rt_raw_free(p, 33, alignment == 1 ? 2 : 1) != 3) return 4;
        if (pcc_rt_raw_free(p, 32, alignment) != 3) return 5;
        if (pcc_rt_raw_free(p, 33, alignment) != 0) return 6;
        if (pcc_rt_raw_free(p, 33, alignment) != 3) return 7;
        alignment = alignment * 2;
    }
    if (pcc_errno_get() != 37) return 8;
    p = (void *)73;
    if (pcc_rt_raw_alloc(1, 0, 0, &p) != 3 || p != (void *)73) return 9;
    if (pcc_rt_raw_alloc(1, 3, 0, &p) != 3 || p != (void *)73) return 10;
    if (pcc_rt_raw_alloc(1, 2147483648ULL, 0, &p) != 3) return 11;
    if (pcc_rt_raw_alloc(1, 16, 2, &p) != 3 || p != (void *)73) return 12;
    if (pcc_rt_raw_alloc(1, 16, 2147483648U, &p) != 3) return 13;
    if (pcc_rt_raw_alloc(1, 16, 0, 0) != 3) return 14;
    if (pcc_rt_raw_alloc(9223372036854775808ULL, 16, 0, &p) != 2) return 15;
    if (pcc_rt_raw_alloc(9223372036854771664ULL, 16, 0, &p) != 2) return 16;
    if (raw_array(9223372036854775808ULL, 2, &p) != 2 || p != (void *)73) return 17;
    /* This request is valid but exceeds this executable's enforced 64 MiB
     * address-space limit. It must really fail in the unchanged allocator. */
    if (pcc_rt_raw_alloc(134217728, 16, 0, &p) != 1 || p != (void *)73) return 18;
    if (pcc_rt_raw_alloc(1, 1073741824, 0, &p) != 1 || p != (void *)73) return 19;
    /* Largest admitted size versus its immediate successor, including the
     * requested-1/effective-16 distinction. Admission succeeds, real mmap
     * fails under RLIMIT_AS; the successor must fail before allocator entry. */
    if (pcc_rt_raw_alloc(9223372036854771648ULL, 1, 0, &p) != 1 || p != (void *)73) return 60;
    if (pcc_rt_raw_alloc(9223372036854771649ULL, 1, 0, &p) != 2 || p != (void *)73) return 61;
    if (pcc_rt_raw_alloc(9223372036854767568ULL, 4096, 0, &p) != 1) return 62;
    if (pcc_rt_raw_alloc(9223372036854767569ULL, 4096, 0, &p) != 2) return 63;
    if (pcc_errno_get() != 37) return 20;
    if (pcc_rt_raw_free(0, 18446744073709551615ULL, 0) != 0) return 21;
    if (pcc_rt_raw_free((void *)1, 1, 1) != 3) return 22;
    if (pcc_rt_raw_resize((void *)1, 1, 1, 2, &p) != 3 || p != (void *)73) return 23;
    if (pcc_rt_raw_resize(0, 0, 16, 2, &p) != 3) return 24;
    if (pcc_rt_raw_free(&i, sizeof(i), 4) != 3) return 25;
    saved = malloc(64);
    if (saved == 0 || pcc_rt_raw_free(saved, 64, 16) != 3) return 26;
    if (pcc_rt_raw_resize(saved, 64, 16, 32, &p) != 3) return 27;
    free(saved);
    saved = pcc_allocator_alloc_object(64);
    if (saved == 0 || pcc_rt_raw_free(saved, 64, 16) != 3) return 28;
    if (pcc_rt_raw_resize(saved, 64, 16, 32, &p) != 3) return 29;
    free(saved);

    if (pcc_rt_raw_alloc(64, 64, 0, &p) != 0) return 30;
    for (i = 0; i < 4; i = i + 1) {
        ((struct Pair *)p)[i].first = i * 2;
        ((struct Pair *)p)[i].second = i * 2 + 1;
    }
    if (raw_python_mutate(p, 8) != 164) return 31;
    for (i = 0; i < 8; i = i + 1)
        if (((int64_t *)p)[i] != i + 17) return 32;
    if (pcc_rt_raw_free((unsigned char *)p + 1, 63, 1) != 3) return 33;
    if (pcc_rt_raw_resize((unsigned char *)p + 1, 63, 1, 128, &q) != 3) return 34;
    if (pcc_rt_raw_resize(p, 63, 64, 128, &q) != 3 || q != (void *)79) return 35;
    if (pcc_rt_raw_resize(p, 64, 32, 128, &q) != 3 || q != (void *)79) return 36;
    if (pcc_rt_raw_resize(p, 64, 64, 128, 0) != 3) return 37;
    if (pcc_rt_raw_resize(p, 64, 64, 18446744073709551615ULL, &q) != 2) return 38;
    if (pcc_rt_raw_resize(p, 64, 64, 134217728, &q) != 1 || q != (void *)79) return 39;
    if (pcc_rt_raw_resize(p, 64, 64, 9223372036854771600ULL, &q) != 1 || q != (void *)79) return 64;
    if (pcc_rt_raw_resize(p, 64, 64, 9223372036854771601ULL, &q) != 2 || q != (void *)79) return 65;
    if (pcc_errno_get() != 37) return 40;
    for (i = 0; i < 8; i = i + 1)
        if (((int64_t *)p)[i] != i + 17) return 41;
    saved = p;
    if (pcc_rt_raw_resize(p, 64, 64, 128, &p) != 0 || p == 0 || p == saved) return 42;
    if (((uint64_t)p & 63) != 0) return 43;
    if (pcc_rt_raw_free(saved, 64, 64) != 3) return 44;
    for (i = 0; i < 8; i = i + 1)
        if (((int64_t *)p)[i] != i + 17) return 45;
    if (pcc_rt_raw_resize(p, 128, 64, 16, &p) != 0) return 46;
    if (((int64_t *)p)[0] != 17 || ((int64_t *)p)[1] != 18) return 47;
    if (pcc_rt_raw_resize(p, 16, 64, 0, &p) != 0 || p == 0) return 48;
    if (((uint64_t)p & 63) != 0 || pcc_rt_raw_free(p, 0, 64) != 0) return 49;
    if (pcc_rt_raw_alloc(0, 1, 1, &p) != 0 || p == 0) return 50;
    if (pcc_rt_raw_free(p, 0, 16) != 3 || pcc_rt_raw_free(p, 0, 1) != 0) return 51;

    /* Three simultaneous owners exercise middle/head/tail removal, not just
     * a single-slot registry or accidental allocator-address acceptance. */
    void *a = 0;
    void *b = 0;
    void *c = 0;
    if (pcc_rt_raw_alloc(11, 1, 0, &a) != 0) return 52;
    if (pcc_rt_raw_alloc(13, 2, 0, &b) != 0) return 53;
    if (pcc_rt_raw_alloc(17, 4, 0, &c) != 0) return 54;
    if (pcc_rt_raw_free(b, 13, 2) != 0) return 55;
    if (pcc_rt_raw_free(c, 17, 4) != 0) return 56;
    if (pcc_rt_raw_free(a, 11, 1) != 0) return 57;
    if (pcc_allocator_live_requested_bytes() != before) return 58;
    if (pcc_errno_get() != 37) return 59;
    return 0;
}
