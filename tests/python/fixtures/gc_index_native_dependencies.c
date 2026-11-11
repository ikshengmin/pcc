/* Isolated index harnesses have no unpublished allocations or staging rings.
 * Keep the production i32 enable word disabled. If a test starts using staging,
 * fail closed rather than pretending a missing provider drained no objects.
 * Index invariant failures must likewise remain fatal. This fixture supplies
 * no index storage, membership algorithms, allocator, or collector behavior.
 */
#include "gc_index_native_dependencies.h"
#include <stdio.h>
#include <stdlib.h>

int32_t pcc_gc_alloc_stage_enabled = 0;
_Noreturn void pcc_platform_abort(void) { abort(); }
int64_t pcc_gc_alloc_stage_miss_drain(void) {
    fprintf(stderr, "isolated index fixture has no allocation staging provider\n");
    abort();
}
