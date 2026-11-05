
#include <stdint.h>
#include <stdio.h>
extern int64_t pcc_gc_is_cms_worker(void);
extern int64_t pcc_gc_tracing_finish_left_to_mutator(void);

int main(void) {
    /* The zero-initialized current thread is not a collector. This tests
     * the actual cross-object ABI, not startup or a successful join. */
    if (pcc_gc_is_cms_worker() != 0) return 1;
    if (pcc_gc_tracing_finish_left_to_mutator() != 0) return 2;
    puts("cms-tls-link-ok");
    return 0;
}
