"""Real GC3 step/collect entrypoints compose with the real mark/remap owners."""
from tests.python.test_gc3_norc_source_retirement import GC3


class PipelineGC3(GC3):
    def __init__(self):
        super().__init__()
        for name in ('freestanding_gc_common_mark_cycle.py',
                     'freestanding_gc_object_root_seeding.py',
                     'freestanding_gc_incremental_concurrent_scheduler.py',
                     'freestanding_gc_generational_scheduler.py',
                     'freestanding_gc_barrier_dispatcher.py'):
            self.load_bodies(name)
        self.load_bodies('freestanding_gc_root_operations.py', {
            'pcc_gc_mark_root_gray_if_known', 'pcc_gc_gray_count_store_release',
        })
        self.load_bodies('freestanding_gc_public_collection.py', {
            'pcc_gc_collect_tracing', 'pcc_gc_begin_explicit_tracing_collect',
            'pcc_gc_end_explicit_tracing_collect',
        })
        self.load_bodies('py_obj.py', {'pcc_gc_collect', '_gc_backend_fast'})
        # The public collect owner now admits deferred physical frees. Its
        # real backend gate returns zero for this GC3/GC4 model; do not stub
        # the new call or invent an asynchronous worker outcome.
        self.load_bodies('freestanding_gc_deferred_free.py', {
            'pcc_gc_deferred_free_begin',
        })
        self.ns.update({
            'pcc_py_gc_minor_graph_lock_for': self.lock_for,
            'pcc_thread_no_park_depth': lambda: self.depth,
            'pcc_py_gc_minor_graph_lock_depth': lambda: self.depth,
            'pcc_gc_visit_walked_stack_roots': self.visit_roots,
            'pcc_gc_precise_stack_diff_if_requested': lambda: None,
            'py_subs_exc_cache_slot': lambda index: 0,
            'py_tls_exc_get': lambda: 0,
            'py_tls_exc_set': lambda value: None,
            'pcc_platform_monotonic_us': lambda: 0,
            'pcc_runtime_monotonic_us': lambda: 0,
            'unsigned_div_i64': lambda a, b: a // b,
            'pcc_gc_reclaim_retired_index_tables': lambda: 0,
            'pcc_gc_external_resource_poll': lambda: None,
            '_pcc_gc_fire_callbacks': lambda *args: None,
        })
        self.step_results = []
        actual_step = self.ns['pcc_gc_step']

        def bounded_observer(budget):
            assert len(self.step_results) < 12, 'real collect loop failed to reach its idle bound'
            result = actual_step(budget)
            self.step_results.append(result)
            return result

        self.ns['pcc_gc_step'] = bounded_observer

    def lock_for(self, site):
        assert site == 1
        self.ns['pcc_py_gc_minor_graph_lock']()

    def keep_target(self, target):
        slot = self.allocate(8)
        self.store(slot, 0, target)
        self.root_cells.append(slot)
        return slot
