"""Retirement liveness and quarantine boundaries, executing runtime bodies."""
import ast
import os
from pathlib import Path
import subprocess

import pytest

RUNTIME = Path(os.environ.get('PCC_RETIREMENT_TEST_SOURCE', Path(__file__).resolve().parents[2])) / 'pcc/runtime/py'


class Runtime:
    def __init__(self):
        self.memory = {}
        self.globals = {}
        self.freed = []
        self.next = 0x10000
        self.world = 1
        self.gated = 1
        self.threads = 2
        self.cascade = 0
        self.stops = 0
        self.resumes = 0
        self.objects = {}
        self.ns = {
            'i64': int, 'c_ptr': int, 'null': lambda: 0,
            'ptr_is_null': lambda x: int(x == 0), 'ptr_eq': lambda a,b: int(a == b),
            'ptr_diff': lambda a,b: a-b, 'is_tagged_int': lambda x: x & 1,
            'global_addr': lambda n: n, 'global_load_ptr': self.get,
            'global_store_ptr': self.set, 'load_ptr': self.load, 'load_i64': self.load,
            'load_i32': self.load, 'store_ptr': self.store, 'store_i64': self.store,
            'store_i32': self.store, 'stack_alloc': self.alloc, 'free': self.freed.append,
            'pcc_gc_backend': lambda: 4, '_selected_backend': lambda: 4,
            'pcc_thread_owns_stopped_world': lambda: self.world,
            'pcc_thread_live_count': lambda: self.threads,
            'pcc_thread_all_stopped_at_gates': lambda: self.gated,
            'pcc_dealloc_cascade_active': lambda: self.cascade,
            'pcc_stop_the_world': self.stop, 'pcc_resume_world': self.resume,
            'pcc_py_gc_minor_graph_lock': lambda: None,
            'pcc_py_gc_minor_graph_unlock': lambda: None,
            'pcc_gc_object_list_head': lambda: self.get('pcc_gc_object_head'),
            'pcc_gc_forwarding_list_head': lambda: self.get('pcc_gc_forwarding_head'),
            'pcc_capi_is_cext_type_tag': lambda tag: 0,
            'pcc_gc_backend4_zpage_clear_active_page': lambda p: None,
            'pcc_gc_backend3_drain_promotion_worklist': lambda b: 0,
            'pcc_gc_tracing_step_cycle': lambda b: 0, '_tracing_work_pending': lambda: 0,
            'pcc_gc_pin_conservative_thread_roots': lambda: None,
            'pcc_gc_precise_stack_diff_if_requested': lambda: None,
            'pcc_gc_tracing_record_pause': lambda *a: None,
            'pcc_platform_monotonic_us': lambda: 100,
            'pcc_gc_backend4_step_remembered_roots': lambda b: 0,
            'pcc_gc_backend4_step_generation_aging': lambda b: 0,
            'pcc_gc_backend4_evacuation_page_drain': lambda b: 0,
            'pcc_gc_backend4_select_relocation_pages': lambda b: 0,
            'pcc_gc_tracing_has_sweep_candidate': lambda: 1,
            'pcc_gc_static_literal_contains': lambda p: 0,
            'pcc_gc_object_index_find': lambda p: self.objects.get(p, 0),
            'PYOBJECTHEADER_FLAGS_OFFSET': 12, 'PY_FLAG_GC_PINNED': 64,
        }
        for name in ['pcc_gc_relocation_finish_source_payloads', 'pcc_gc_forwarding_finish_detached',
                     'pcc_gc_forwarding_finish_dead_targets', 'pcc_gc_identity_finish_detached',
                     'pcc_gc_object_node_finish_detached']:
            self.ns[name] = lambda p: None
        self.install('freestanding_gc_zpage_lifecycle.py', {
            'pcc_gc_backend4_zpage_clear_reusable_state', 'pcc_gc_backend4_zpage_destroy',
        })
        self.install('freestanding_gc_forwarding_retirement.py', {
            '_release_retained_pages', 'pcc_gc_backend4_drain_parked_pages',
            'pcc_gc_backend4_finish_retained_page_releases', 'pcc_gc_backend4_finish_remap_retirement',
            'pcc_gc_backend4_remap_and_retire_unlocked', 'pcc_gc_backend4_remap_and_retire_stopped_world',
        })
        self.install('freestanding_gc_barrier_dispatcher.py', {'pcc_gc_step'})
        self.install('freestanding_gc_public_collection.py', {'pcc_gc_collect_tracing'})
        self.install('freestanding_gc_root_operations.py', {'pcc_gc_object_is_address_pinned'})
        self.set('pcc_gc_config_initialized', 1)
        self.set('pcc_gc_backend_selected', 4)
        self.set('pcc_gc_backend4_no_refcounts', 1)
        self.set('pcc_gc_explicit_collect_active', 1)

    def install(self, filename, names):
        nodes = []
        for node in ast.parse((RUNTIME / filename).read_text()).body:
            if isinstance(node, ast.FunctionDef) and node.name in names:
                node.decorator_list = []
                nodes.append(node)
        assert {n.name for n in nodes} == names
        exec(compile(ast.Module(body=nodes, type_ignores=[]), filename, 'exec'), self.ns)

    def get(self, name):
        return self.globals.get(name, 0)

    def set(self, name, value):
        self.globals[name] = value

    def load(self, pointer, offset):
        return self.globals.get(pointer, 0) if isinstance(pointer, str) else self.memory.get(pointer+offset, 0)

    def store(self, pointer, offset, value):
        if isinstance(pointer, str):
            self.globals[pointer] = value
        else:
            self.memory[pointer+offset] = value

    def alloc(self, size):
        result = self.next
        self.next += ((size+7)//8)*8 + 8
        return result

    def stop(self):
        self.stops += 1
        self.world = 1
        return 0

    def resume(self):
        self.resumes += 1
        self.world = 0
        return 0

    def page(self, queue='retained', live=0, pending=0, forwards=0):
        page = self.alloc(376)
        span = self.alloc(4352)
        self.store(page, 32, live)
        self.store(page, 88, pending)
        self.store(page, 96, forwards)
        self.store(page, 72, span)
        self.store(page, 80, 4096)
        name = 'pcc_gc_backend4_' + ('retained_page_head' if queue == 'retained' else 'parked_head')
        self.store(page, 56, self.get(name))
        self.set(name, page)
        return page, span

    def retire(self):
        return self.ns['pcc_gc_backend4_remap_and_retire_stopped_world']()


def test_retained_empty_page_with_no_forwarding_is_released():
    runtime = Runtime()
    page, span = runtime.page()
    assert runtime.retire() == 0  # return value remains forwarding retirement
    assert runtime.freed == [span, page]
    assert runtime.get('pcc_gc_backend4_remap_epoch') == 1
    assert runtime.get('pcc_gc_cycle_requested') == 0


def test_parked_page_needs_two_complete_remap_epochs():
    runtime = Runtime()
    page, span = runtime.page(queue='parked')
    runtime.retire()
    assert runtime.freed == []
    assert runtime.get('pcc_gc_backend4_parked_head') == 0
    assert runtime.get('pcc_gc_backend4_retained_page_head') == page
    assert runtime.get('pcc_gc_backend4_remap_epoch') == 1
    runtime.retire()
    assert runtime.freed == [span, page]
    assert runtime.get('pcc_gc_backend4_remap_epoch') == 2


@pytest.mark.parametrize('explicit', [0, 1])
def test_step_dispatch_admits_quarantine_work_without_new_mark(explicit):
    runtime = Runtime()
    runtime.set('pcc_gc_explicit_collect_active', explicit)
    page, span = runtime.page()
    assert runtime.ns['pcc_gc_step'](64) == 0
    assert runtime.freed == [span, page]
    assert runtime.get('pcc_gc_cycle_requested') == 0


@pytest.mark.parametrize('condition', ['nongated', 'cascade', 'relocating', 'busy_remap', 'epoch_exhausted', 'wrong_backend'])
def test_new_path_rejects_unsafe_or_conflicting_windows(condition):
    runtime = Runtime()
    page, span = runtime.page()
    if condition == 'nongated': runtime.gated = 0
    elif condition == 'cascade': runtime.cascade = 1
    elif condition == 'relocating': runtime.set('pcc_gc_relocation_set_head', 0x2000)
    elif condition == 'busy_remap': runtime.set('pcc_gc_backend4_remap_active', 1)
    elif condition == 'epoch_exhausted': runtime.set('pcc_gc_backend4_remap_epoch', 9223372036854775807)
    elif condition == 'wrong_backend': runtime.set('pcc_gc_backend_selected', 0)
    runtime.retire()
    assert runtime.freed == []
    assert runtime.get('pcc_gc_backend4_retained_page_head') == page


@pytest.mark.parametrize('kind', ['live', 'pending', 'forwarded'])
def test_ineligible_pages_stay_quarantined(kind):
    runtime = Runtime()
    live = int(kind == 'live')
    page, span = runtime.page(live=live, pending=int(kind == 'pending'), forwards=int(kind == 'forwarded'))
    runtime.retire()
    runtime.retire()
    assert runtime.freed == []
    assert runtime.get('pcc_gc_backend4_retained_page_head') == page
    assert runtime.load(page, 32) == live


@pytest.mark.parametrize('protection', ['explicit_pin', 'conservative_pin', 'foreign_lease'])
def test_actual_sweep_pin_guard_keeps_page_owner_count(protection):
    runtime = Runtime()
    page, span = runtime.page(live=1)
    owner = runtime.alloc(128)
    node = runtime.alloc(88)
    runtime.objects[owner] = node
    flags = 1024
    if protection == 'explicit_pin': flags |= 64
    if protection == 'conservative_pin': runtime.store(node, 56, 1)
    if protection == 'foreign_lease': runtime.store(node, 80, 1)
    runtime.store(owner, 12, flags)
    assert runtime.ns['pcc_gc_object_is_address_pinned'](owner) == 1
    # Run the actual PASS-2 skip/clear verdict, not a duplicate predicate.
    tree = ast.parse((RUNTIME/'freestanding_gc_tracing_sweep_collector.py').read_text())
    sweep = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'pcc_gc_tracing_sweep_unreachable')
    guards = [n for n in ast.walk(sweep) if isinstance(n, ast.If)
              and isinstance(n.test, ast.BoolOp) and isinstance(n.test.op, ast.Or)
              and any(isinstance(c, ast.Call) and isinstance(c.func, ast.Name)
                      and c.func.id == 'pcc_gc_object_is_address_pinned' for c in ast.walk(n.test))]
    assert len(guards) == 1
    runtime.ns.update(obj=owner, flags=flags)
    exec(compile(ast.Module(body=guards, type_ignores=[]), 'actual-sweep-guard', 'exec'), runtime.ns)
    assert runtime.load(owner, 12) & 1024 == 0
    runtime.retire()
    assert runtime.freed == []
    assert runtime.load(page, 32) == 1


def test_automatic_sweep_boundary_drains_pages_after_deallocator_return():
    runtime = Runtime()
    allocated = []
    def sweep(budget):
        assert budget == 9223372036854775807
        allocated.extend(runtime.page())
        assert runtime.freed == []
        return 7
    runtime.ns['pcc_gc_tracing_sweep_unreachable'] = sweep
    assert runtime.ns['pcc_gc_collect_tracing']() == 7
    page, span = allocated
    assert runtime.freed == [span, page]
    assert runtime.get('pcc_gc_cycle_requested') == 0


def test_stw_acquired_by_rejected_call_is_balanced():
    runtime = Runtime()
    runtime.world = 0
    runtime.gated = 0
    runtime.page()
    runtime.retire()
    assert (runtime.stops, runtime.resumes, runtime.world) == (1, 1, 0)


def test_eligible_and_live_pages_can_share_quarantine_list():
    runtime = Runtime()
    live, live_span = runtime.page(live=1)
    empty, empty_span = runtime.page()
    runtime.retire()
    assert runtime.freed == [empty_span, empty]
    assert runtime.get('pcc_gc_backend4_retained_page_head') == live
    assert runtime.load(live, 72) == live_span


@pytest.mark.integration
def test_owned_native_empty_pages_are_reclaimed_without_forwarding(
    tmp_path, threaded_pcc_runtime_archive,
):
    from tests.owned_c_execution import compile_owned_c_with_runtime

    source = Path(__file__).with_name('fixtures') / 'gc4_retained_no_forwarding.c'
    binary = tmp_path / 'retained_pages'
    compile_owned_c_with_runtime(
        source, binary, runtime_archive=threaded_pcc_runtime_archive,
    )
    for tracing_only in ('0', '1'):
        result = subprocess.run(
            [str(binary)], capture_output=True, text=True, timeout=25,
            env=dict(
                os.environ, PATH='/nonexistent', PCC_GC_BACKEND='4',
                PCC_GC4_TRACING_ONLY='0', PCC_GC_TRACING_ONLY=tracing_only,
            ),
        )
        assert result.returncode == 0, (tracing_only, result)
        assert result.stderr == '', (tracing_only, result)
        assert result.stdout.startswith(
            'retained-no-forwarding-ok rounds=32 objects=16384 ',
        ), (tracing_only, result)
