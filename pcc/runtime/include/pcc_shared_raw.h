#ifndef PCC_SHARED_RAW_H
#define PCC_SHARED_RAW_H
#include <stdint.h>
#ifdef __cplusplus
extern "C" {
#endif
/* R0a, raw storage only. No GC ownership, object lifetime, or C++ new semantics.
 * All nonnull inputs must be live exact bases issued by this interface.
 * The size and requested alignment must match, even when effective alignment
 * was stronger. Ownership/use must be serialized by the caller; pointer ABA
 * and invalid output storage are unsafe caller violations.
 * A successful zero-size operation returns a deallocatable aligned base.
 * Failure preserves output and any old allocation. Resize(NULL,...) is invalid.
 * out must point to writable storage outside the allocation being resized.
 * free(NULL,...) succeeds regardless of layout. errno is preserved.
 */
enum { PCC_RT_RAW_OK = 0, PCC_RT_RAW_OOM = 1, PCC_RT_RAW_OVERFLOW = 2,
       PCC_RT_RAW_BAD_LAYOUT = 3, PCC_RT_RAW_ZERO = 1 };
int32_t pcc_rt_raw_alloc(uint64_t size, uint64_t align, uint32_t flags, void **out);
int32_t pcc_rt_raw_resize(void *old, uint64_t old_size, uint64_t align,
                          uint64_t new_size, void **out);
int32_t pcc_rt_raw_free(void *ptr, uint64_t size, uint64_t align);
#ifdef __cplusplus
}
#endif
#endif
