/* A normal off-stack registered owner, private to this single-thread fixture.
 * Register the empty cell before Python constructs its value. All publication
 * and clearing use the ordinary root store; no collector/stack instrumentation.
 */
typedef struct TestPyObject TestPyObject;
extern void *pcc_gc_scheduler_root_register_handle(TestPyObject **slot);
extern void pcc_gc_scheduler_root_unregister_handle(void *handle);
extern void pcc_gc_store_root(TestPyObject **slot, TestPyObject *value);
extern long long pcc_gc_slot_is_runtime_root(TestPyObject **slot);

static TestPyObject *cell;
static void *registration;

long long test_gc_callback_root_open(void) {
    if (cell || registration || pcc_gc_slot_is_runtime_root(&cell)) return 0;
    registration = pcc_gc_scheduler_root_register_handle(&cell);
    return registration != 0 && pcc_gc_slot_is_runtime_root(&cell) == 1;
}

long long test_gc_callback_root_put(TestPyObject *value) {
    if (!registration || cell || !value) return 0;
    pcc_gc_store_root(&cell, value);
    return cell != 0;
}

long long test_gc_callback_root_present(void) {
    return registration != 0 && cell != 0 && pcc_gc_slot_is_runtime_root(&cell) == 1;
}

long long test_gc_callback_root_close(void) {
    if (!registration) return 0;
    pcc_gc_store_root(&cell, 0);
    pcc_gc_scheduler_root_unregister_handle(registration);
    registration = 0;
    return cell == 0 && pcc_gc_slot_is_runtime_root(&cell) == 0;
}
