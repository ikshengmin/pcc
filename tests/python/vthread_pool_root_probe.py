"""Native assertions for the bounded cache of empty scheduler root slots."""

CACHED_POOL_ROOT_PROBE = r'''
#include "py_runtime.h"
extern void *pcc_vthread_ready_free_py;
extern void *pcc_vthread_join_free_py;
extern void *pcc_vthread_channel_free_py;
extern int64_t pcc_vthread_ready_cached_py;
extern int64_t pcc_vthread_join_free_count_py;
extern int64_t pcc_vthread_channel_free_count_py;

static int64_t cached_pool_roots(void) {
    void *heads[3] = {
        pcc_vthread_ready_free_py,
        pcc_vthread_join_free_py,
        pcc_vthread_channel_free_py
    };
    int64_t expected[3] = {
        pcc_vthread_ready_cached_py,
        pcc_vthread_join_free_count_py,
        pcc_vthread_channel_free_count_py
    };
    int64_t total = 0;
    for (int kind = 0; kind < 3; ++kind) {
        int64_t count = 0;
        void *node = heads[kind];
        while (node != 0) {
            void **words = (void **)node;
            if (words[0] != 0 || words[2] == 0) return -1;
            if (pcc_gc_slot_is_runtime_root((PyObject **)node) != 1) return -1;
            if (++count > 4096) return -1;
            node = words[1];
        }
        if (count != expected[kind]) return -1;
        total += count;
    }
    return total;
}
'''
