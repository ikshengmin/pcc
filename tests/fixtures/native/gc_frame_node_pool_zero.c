/* Byte-exact cached-node initialization through the real owned runtime.
 * These raw registry nodes are never published as roots or managed objects.
 * This tests allocator/TLS behavior, not collector concurrency or performance.
 */
#include <stdint.h>
#include <stdio.h>
#include <string.h>

extern int64_t pcc_gc_backend(void);
extern int64_t pcc_gc_set_backend(int64_t);
extern void *pcc_gc_frame_node_alloc(int64_t);
extern void pcc_gc_frame_node_release(void *);
extern int64_t pcc_gc_frame_node_tls_pool_cached_count(void);
extern void pcc_gc_frame_node_tls_pool_drain(void);
extern int64_t pcc_thread_start(void **, void *(*)(void *), void *);
extern int64_t pcc_thread_join(void *, void **);

enum { HEADER_BYTES = 64, NEXT_OFFSET = 16, COUNT_OFFSET = 40,
       BUCKETS = 17, CYCLES = 8, LIMIT = 1024, CAP_PROBES = 1031 };

static int backend;
static int pooled;
static int byte_checks;
static int reuse_checks;

static int fail(int code, const char *phase) {
    fprintf(stderr, "FRAME_NODE_ZERO_FAIL gc=%d phase=%s code=%d\n",
            backend, phase, code);
    return code;
}

#define CHECK(condition, code, phase) do { \
    if (!(condition)) return fail(code, phase); \
} while (0)

static int all_zero(void *node, int count) {
    if (node == 0) return 0;
    unsigned char *bytes = (unsigned char *)node;
    for (int i = 0; i < HEADER_BYTES + count * 8; i++) {
        if (bytes[i] != 0) return 0;
    }
    /* Includes flag padding@52..55 and every stable-value byte@64 onwards. */
    byte_checks++;
    return 1;
}

static void poison_release(void *node, int count, int poison) {
    memset(node, poison, HEADER_BYTES + count * 8);
    *(int64_t *)((unsigned char *)node + COUNT_OFFSET) = count;
    pcc_gc_frame_node_release(node);
}

static int exact_bucket_reuse(void) {
    for (int count = 0; count < BUCKETS; count++) {
        pcc_gc_frame_node_tls_pool_drain();
        void *first = pcc_gc_frame_node_alloc(count);
        void *second = pcc_gc_frame_node_alloc(count);
        CHECK(all_zero(first, count) && all_zero(second, count), 10, "fresh-bytes");
        CHECK(first != second, 11, "distinct-live-nodes");
        for (int cycle = 0; cycle < CYCLES; cycle++) {
            poison_release(first, count, 0xA1 + cycle);
            poison_release(second, count, 0xB1 + cycle);
            CHECK(pcc_gc_frame_node_tls_pool_cached_count() == (pooled ? 2 : 0),
                  12, "release-count");
            if (pooled) {
                CHECK(*(void **)((unsigned char *)second + NEXT_OFFSET) == first,
                      13, "nonzero-pool-link");
            }
            void *popped = pcc_gc_frame_node_alloc(count);
            CHECK(all_zero(popped, count), 14, "first-pop-bytes");
            CHECK(pcc_gc_frame_node_tls_pool_cached_count() == (pooled ? 1 : 0),
                  15, "first-pop-count");
            void *other = pcc_gc_frame_node_alloc(count);
            CHECK(all_zero(other, count), 16, "second-pop-bytes");
            CHECK(pcc_gc_frame_node_tls_pool_cached_count() == 0, 17, "second-pop-count");
            if (pooled) {
                CHECK(popped == second && other == first, 18, "same-node-reuse");
                reuse_checks += 2;
            }
            first = popped;
            second = other;
        }
        poison_release(first, count, 0x7E);
        poison_release(second, count, 0x7D);
        pcc_gc_frame_node_tls_pool_drain();
        CHECK(pcc_gc_frame_node_tls_pool_cached_count() == 0, 19, "bucket-drain");
        first = pcc_gc_frame_node_alloc(count);
        CHECK(all_zero(first, count), 20, "drain-recreate");
        poison_release(first, count, 0x75);
        pcc_gc_frame_node_tls_pool_drain();
    }
    return 0;
}

static int oversized_and_capacity(void) {
    for (int count = 17; count <= 31; count += 14) {
        void *node = pcc_gc_frame_node_alloc(count);
        CHECK(all_zero(node, count), 30, "oversized-fresh");
        poison_release(node, count, 0xC7);
        CHECK(pcc_gc_frame_node_tls_pool_cached_count() == 0, 31, "oversized-bypass");
    }
    void *nodes[CAP_PROBES];
    for (int i = 0; i < CAP_PROBES; i++) {
        nodes[i] = pcc_gc_frame_node_alloc(i % BUCKETS);
        CHECK(all_zero(nodes[i], i % BUCKETS), 32, "capacity-fresh");
    }
    for (int i = 0; i < CAP_PROBES; i++) {
        poison_release(nodes[i], i % BUCKETS, 0xD3);
        int expected = i + 1;
        if (expected > LIMIT) expected = LIMIT;
        if (!pooled) expected = 0;
        CHECK(pcc_gc_frame_node_tls_pool_cached_count() == expected, 33, "shared-cap");
    }
    if (pooled) {
        for (int i = 0; i < LIMIT; i++) {
            nodes[i] = pcc_gc_frame_node_alloc(i % BUCKETS);
            CHECK(all_zero(nodes[i], i % BUCKETS), 34, "capacity-reuse-bytes");
            CHECK(pcc_gc_frame_node_tls_pool_cached_count() == LIMIT - i - 1,
                  35, "capacity-pop-count");
        }
        for (int i = 0; i < LIMIT; i++)
            poison_release(nodes[i], i % BUCKETS, 0xE5);
    }
    pcc_gc_frame_node_tls_pool_drain();
    pcc_gc_frame_node_tls_pool_drain();
    pcc_gc_frame_node_release(0);
    CHECK(pcc_gc_frame_node_tls_pool_cached_count() == 0, 36, "idempotent-drain");
    return 0;
}

struct WorkerState {
    void *parent_node;
    int status;
};

static void *check_tls(void *raw) {
    struct WorkerState *state = (struct WorkerState *)raw;
    if (pcc_gc_frame_node_tls_pool_cached_count() != 0) {
        state->status = 41;
        return raw;
    }
    void *node = pcc_gc_frame_node_alloc(3);
    if (!all_zero(node, 3) || (pooled && node == state->parent_node)) {
        state->status = 42;
        return raw;
    }
    poison_release(node, 3, 0xF5);
    if (pcc_gc_frame_node_tls_pool_cached_count() != (pooled ? 1 : 0)) {
        state->status = 43;
        return raw;
    }
    pcc_gc_frame_node_tls_pool_drain();
    if (pcc_gc_frame_node_tls_pool_cached_count() != 0) state->status = 44;
    return raw;
}

static int thread_local_storage(void) {
    void *parent_node = pcc_gc_frame_node_alloc(3);
    CHECK(all_zero(parent_node, 3), 40, "parent-fresh");
    poison_release(parent_node, 3, 0xF3);
    struct WorkerState state;
    state.parent_node = pooled ? parent_node : 0;
    state.status = 0;
    void *thread = 0;
    CHECK(pcc_thread_start(&thread, check_tls, &state) == 0, 45, "thread-start");
    void *result = 0;
    CHECK(pcc_thread_join(thread, &result) == 0, 46, "thread-join");
    CHECK(result == &state && state.status == 0, 47, "worker-tls");
    CHECK(pcc_gc_frame_node_tls_pool_cached_count() == (pooled ? 1 : 0),
          48, "parent-cache-retained");
    void *popped = pcc_gc_frame_node_alloc(3);
    CHECK(all_zero(popped, 3), 49, "parent-reuse-bytes");
    if (pooled) CHECK(popped == parent_node, 50, "parent-reuse-identity");
    poison_release(popped, 3, 0xF1);
    pcc_gc_frame_node_tls_pool_drain();
    return 0;
}

int main(void) {
    backend = (int)pcc_gc_backend();
    CHECK(pcc_gc_set_backend(backend) == 0, 2, "backend-reset");
    pooled = backend == 3 || backend == 4;
    pcc_gc_frame_node_tls_pool_drain();
    int status = exact_bucket_reuse();
    if (status != 0) return status;
    status = oversized_and_capacity();
    if (status != 0) return status;
    status = thread_local_storage();
    if (status != 0) return status;
    CHECK(pcc_gc_frame_node_tls_pool_cached_count() == 0, 51, "final-empty");
    printf("FRAME_NODE_ZERO_OK gc=%d buckets=%d cycles=%d byte_checks=%d reuse_checks=%d cached=0\n",
           backend, BUCKETS, CYCLES, byte_checks, reuse_checks);
    return 0;
}
