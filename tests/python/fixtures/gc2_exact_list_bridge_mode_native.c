/* Query the actual runtime's read-only, GC2-only bridge selector. */
#include "py_runtime.h"
#include <stdio.h>

extern int64_t pcc_gc2_exact_list_bridge_mode(void);

int main(void) {
    int64_t backend = pcc_gc_backend();
    int64_t first = pcc_gc2_exact_list_bridge_mode();
    int64_t second = pcc_gc2_exact_list_bridge_mode();
    if (pcc_current_thread_id() <= 0 || first != second) return 1;
    printf("GC2_EXACT_LIST_MODE backend=%lld mode=%lld\n",
           (long long)backend, (long long)first);
    return 0;
}
