"""Real GC3 step/collect entrypoints compose with the real mark/remap owners."""
from tests.python.test_gc3_norc_source_retirement import GC3


class PipelineGC3(GC3):
    def __init__(self):
        super().__init__()
        for name in ('freestanding_gc_common_mark_cycle.py',
                     'freestanding_gc_object_root_seeding.py',
                     'freestanding_gc_incremental_concurrent_scheduler.py',
                     'freestanding_gc_generational_remembered_owners.py',
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


def test_automatic_idle_step_retires_live_target_shell_without_explicit_mode():
    m = PipelineGC3()
    source, node, block = m.value(nursery=True)
    target = m.copy(source)
    m.keep_target(target)
    m.store('pcc_gc_explicit_collect_active', 0, 0)
    m.stopped = False
    assert m.ns['pcc_gc_step'](1024) > 0
    assert source not in m.index and target in m.index
    assert m.load(block, 32) == 0 and not m.stopped


def test_public_no_candidate_cut_retires_source_and_does_not_start_sweep():
    m = PipelineGC3()
    source, node, block = m.value(nursery=True)
    target = m.copy(source)
    m.keep_target(target)
    m.stopped = False
    # A no-candidate public boundary must never invoke a legacy sweep while
    # completing alias retirement. Its real remap still owns root healing.
    m.ns['pcc_gc_tracing_sweep_unreachable'] = lambda budget: (_ for _ in ()).throw(AssertionError('unexpected sweep'))
    assert m.ns['pcc_gc_collect_tracing']() == 0
    assert source not in m.index and target in m.index
    assert m.load(block, 32) == 0 and not m.stopped


def test_actual_collect_finishes_its_idle_bound_with_pinned_forwarding_source():
    m = PipelineGC3()
    source, node, block = m.value(nursery=True)
    target = m.copy(source)
    m.keep_target(target)
    m.words = [source + 16]
    m.stopped = False
    assert m.ns['pcc_gc_collect'](0) == 0
    assert m.step_results[-4:] == [0, 0, 0, 0]
    assert m.load('pcc_gc_tracing_finish_commits', 0) >= 1
    assert m.load('pcc_gc_mark_active', 0) == 0
    assert source in m.index and target in m.index and m.load(block, 32) == 1
    assert not m.stopped


def test_actual_collect_retires_unpinned_source_while_target_remains_live():
    m = PipelineGC3()
    source, node, block = m.value(nursery=True)
    target = m.copy(source)
    m.keep_target(target)
    m.stopped = False
    assert m.ns['pcc_gc_collect'](0) == 0
    assert m.load('pcc_gc_tracing_finish_commits', 0) >= 1
    assert source not in m.index and target in m.index
    assert m.load(block, 32) == 0 and not m.stopped
