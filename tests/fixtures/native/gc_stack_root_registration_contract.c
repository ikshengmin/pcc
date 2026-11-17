/* Exercise existing root-registration APIs. No new scanner or boundary policy.
 * Null slots deliberately test registration bookkeeping, not object survival. */
#include "py_runtime.h"
#include <stdint.h>
#include <stdio.h>

extern int64_t pcc_gc_stack_slot_root_is_conservative(void *);
extern int64_t pcc_thread_native_slots_conservative(void *);
extern int64_t pcc_gc_frame_slots_conservative(void *);
extern void *pcc_py_gc_scheduler_root_stack_handle;
static PyObject *global_slot;

int main(void) {
    int backend = pcc_gc_backend();
    if (backend < 0 || backend > 4) return 101;
    if (pcc_threads_enabled() != 1 || pcc_current_thread_id() <= 0) return 102;
    if (pcc_refcount_strategy() != PCC_REFCOUNT_STRATEGY_ATOMIC) return 103;
    PyObject *stack_slot = 0;
    int expected = backend != 0;
    int stack_conservative = (int)pcc_gc_stack_slot_root_is_conservative(&stack_slot);
    int global_conservative = (int)pcc_gc_stack_slot_root_is_conservative(&global_slot);
    int native_stack = (int)pcc_thread_native_slots_conservative(&stack_slot);
    int native_global = (int)pcc_thread_native_slots_conservative(&global_slot);
    int frame_stack = (int)pcc_gc_frame_slots_conservative(&stack_slot);
    int frame_global = (int)pcc_gc_frame_slots_conservative(&global_slot);
    if (frame_stack != expected || frame_global != 0) return 109;
    if (stack_conservative != expected || global_conservative != 0
        || native_stack != expected || native_global != 0) return 104;

    void *stack_handle = pcc_gc_scheduler_root_register_handle(&stack_slot);
    if (!stack_handle) return 105;
    void *global_handle = pcc_gc_scheduler_root_register_handle(&global_slot);
    if (!global_handle) {
        pcc_gc_scheduler_root_unregister_handle(stack_handle);
        return 106;
    }
    int stack_sentinel = stack_handle == (void *)&pcc_py_gc_scheduler_root_stack_handle;
    int global_sentinel = global_handle == (void *)&pcc_py_gc_scheduler_root_stack_handle;
    int distinct = stack_handle != global_handle;
    pcc_gc_scheduler_root_unregister_handle(global_handle);
    pcc_gc_scheduler_root_unregister_handle(stack_handle);
    if (stack_sentinel != expected || global_sentinel != 0 || !distinct) return 107;
    if (stack_slot != 0 || global_slot != 0) return 108;
    printf("stack-root-contract:ok backend=%d stack=%d global=%d native_stack=%d native_global=%d frame_stack=%d frame_global=%d stack_sentinel=%d global_sentinel=%d distinct=%d\n",
           backend, stack_conservative, global_conservative, native_stack, native_global, frame_stack, frame_global,
           stack_sentinel, global_sentinel, distinct);
    return 0;
}
