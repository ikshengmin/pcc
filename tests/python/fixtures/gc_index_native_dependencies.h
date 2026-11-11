#ifndef PCC_TEST_GC_INDEX_NATIVE_DEPENDENCIES_H
#define PCC_TEST_GC_INDEX_NATIVE_DEPENDENCIES_H

#include <stdint.h>

/* Exact raw ABI required by the isolated, real freestanding index object. */
extern int32_t pcc_gc_alloc_stage_enabled;
_Noreturn void pcc_platform_abort(void);
int64_t pcc_gc_alloc_stage_miss_drain(void);

#endif
