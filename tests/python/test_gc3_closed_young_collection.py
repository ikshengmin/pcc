"""Closed-domain young cut runs production roots, closure and typed free.

The finite model is not evidence for native machine-word capture or concurrent
mutators. Native rows below provide separate execution evidence.
"""
from pathlib import Path
import os
import subprocess

import pytest

from pcc.runtime.py import py_abi_constants as abi
from tests.owned_c_execution import compile_owned_c_with_runtime
from tests.python.test_gc3_norc_collection_entry import PipelineGC3
from tests.python.runtime_body_model import gc_state_initial_values


class YoungCutGC3(PipelineGC3):
    def __init__(self, *, admitted=True):
        super().__init__()
        # The inherited finite storage deliberately starts empty. Seed every
        # otherwise-unset GC state cell from the production declaration, not
        # guessed zeroes (the remembered allocation budget starts at -1).
        for name, value in gc_state_initial_values().items():
            self.memory.setdefault((name, 0), value)
        self.load_bodies('freestanding_gc_tracing_sweep_collector.py', {
            'pcc_gc_tracing_finalize_unpinned',
        })
        # The concrete registry and cache visitors are real production bodies;
        # the walked machine frames remain this model's explicit finite roots.
        self.load_bodies('freestanding_gc_mapped_roots.py', {
            'pcc_gc_visit_registered_root_slots',
            'pcc_gc_visit_scheduler_root_slots',
            'pcc_gc_visit_builtin_exception_cache_slots',
        })
        self.ns['_pcc_stackmap_roots_mode'] = lambda: 0
        self.ns['pcc_gc_visit_walked_stack_roots'] = self.visit_roots
        self.ns['py_subs_exc_cache_slot'] = lambda index: 0
        self.ns['pcc_platform_getenv'] = lambda name: 0
        if admitted:
            self.ns['pcc_gc_backend3_enable_closed_young_domain']()

    def young(self, *, tag=abi.PY_TYPE_FLOAT, nursery=False):
        obj, node, block = self.value(tag=tag, nursery=nursery)
        self.ns['pcc_gc_backend3_young_link_head'](node)
        return obj, node, block

    def keep(self, obj):
        slot = self.allocate(8)
        self.store(slot, 0, obj)
        self.root_cells.append(slot)
        return slot

    def collect(self):
        return self.ns['pcc_gc_backend3_collect_closed_young']()


@pytest.mark.parametrize('backend', (0, 1, 2, 3, 4))
def test_unproved_native_host_never_reclaims(backend):
    m = YoungCutGC3(admitted=False)
    m.store('pcc_gc_backend_selected', 0, backend)
    obj, _, _ = m.young()
    assert m.collect() == -1
    assert obj in m.index
    assert m.owner_snapshot_refreshes == 0


def test_revocation_is_permanent_even_after_later_generated_entry():
    m = YoungCutGC3()
    m.ns['pcc_gc_backend3_revoke_closed_young_domain']()
    m.ns['pcc_gc_backend3_enable_closed_young_domain']()
    assert m.load('pcc_gc_backend3_closed_young_domain', 0) == 2
    assert m.collect() == -1


@pytest.mark.parametrize('variable', (
    'LD_PRELOAD', 'LD_LIBRARY_PATH',
    'DYLD_INSERT_LIBRARIES', 'DYLD_LIBRARY_PATH',
))
def test_loader_injection_environment_cannot_issue_admission(variable):
    m = YoungCutGC3(admitted=False)
    m.ns['pcc_platform_getenv'] = lambda name: 4096 if name == variable else 0
    m.ns['pcc_gc_backend3_enable_closed_young_domain']()
    assert m.load('pcc_gc_backend3_closed_young_domain', 0) == 2
    assert m.collect() == -1


def test_no_park_region_does_not_attempt_to_suspend_mutators():
    m = YoungCutGC3()
    with m.locked():
        assert m.collect() == -1
    assert m.owner_snapshot_refreshes == 0


def test_failed_stop_does_not_change_collection_or_root_state():
    m = YoungCutGC3()
    m.stopped = False
    m.ns['pcc_stop_the_world'] = lambda: 1
    assert m.collect() == -1
    assert m.owner_snapshot_refreshes == 0
    assert m.load('pcc_gc_backend3_young_cut_active', 0) == 0


@pytest.mark.parametrize('nursery', (False, True))
def test_independent_cut_frees_unrooted_scalars_without_a_major(nursery):
    m = YoungCutGC3()
    dead, _, dead_block = m.young(nursery=nursery)
    live, _, live_block = m.young(nursery=nursery)
    slot = m.keep(live)
    before = m.load('pcc_gc_tracing_finish_commits', 0)
    assert m.collect() == 1
    assert dead not in m.index and live in m.index
    assert m.load(slot, 0) == live
    assert m.load(live, 12) & 256 and not m.load(live, 12) & 128
    assert not m.forwardings
    assert m.load('pcc_gc_mark_active', 0) == 0
    assert m.load('pcc_gc_tracing_finish_commits', 0) == before
    assert m.load('pcc_gc_backend3_young_cut_commits', 0) == 1
    assert m.load('pcc_gc_backend3_young_cut_freed', 0) == 1
    if nursery:
        assert m.load(dead_block, 32) == 0 and m.load(live_block, 32) == 1


@pytest.mark.parametrize('offset', (0, 8, 16, 31))
def test_conservative_interior_word_retains_young_in_place(offset):
    m = YoungCutGC3()
    keep, _, _ = m.young()
    dead, _, _ = m.young()
    m.words = [keep + offset]
    assert m.collect() == 1
    assert keep in m.index and dead not in m.index
    assert m.load(keep, 12) & 256
    assert not m.forwardings


@pytest.mark.parametrize('kind', ('pin', 'lease', 'immortal', 'forward-target'))
def test_physical_and_identity_owners_survive(kind):
    m = YoungCutGC3()
    obj, node, _ = m.young()
    if kind == 'pin':
        m.store(obj, 12, m.load(obj, 12) | abi.PY_FLAG_GC_PINNED)
    elif kind == 'lease':
        m.store(node, 80, 1)
    elif kind == 'immortal':
        m.store(obj, 12, m.load(obj, 12) | abi.PY_FLAG_IMMORTAL)
    else:
        m.store(obj, 12, m.load(obj, 12) | 8192)
    assert m.collect() == 0
    assert obj in m.index


@pytest.mark.parametrize('registry', ('frame', 'continuation', 'scheduler'))
def test_complete_registry_ignores_equal_stale_promotion_cache(registry):
    m = YoungCutGC3()
    target, _, _ = m.young()
    slot = m.allocate(8)
    cache = m.allocate(8)
    m.store(slot, 0, target)
    m.store(cache, 0, target)  # equality from a previous physical lifetime
    node = m.allocate(64)
    if registry == 'frame':
        m.store(node, 8, slot); m.store(node, 40, 1); m.store(node, 56, cache)
        m.store('pcc_gc_frame_head', 0, node)
    elif registry == 'continuation':
        m.store(node, 8, slot); m.store(node, 24, 1); m.store(node, 40, cache)
        m.store('pcc_gc_continuation_root_head', 0, node)
    else:
        m.store(node, 0, slot)
        m.store('pcc_gc_scheduler_root_head', 0, node)
    assert m.collect() == 0
    assert target in m.index and m.load(target, 12) & 256
    assert m.load(slot, 0) == target


@pytest.mark.parametrize('owner_young', (False, True))
def test_preserved_container_closure_includes_old_to_young_edges(owner_young):
    m = YoungCutGC3()
    target, _, _ = m.young()
    dead, _, _ = m.young()
    owner, node, _ = m.young(tag=abi.PY_TYPE_LIST)
    items = m.allocate(8)
    m.store(items, 0, target)
    m.store(owner, 16, 1); m.store(owner, 24, 1); m.store(owner, 32, items)
    if not owner_young:
        m.ns['pcc_gc_backend3_young_unlink'](node)
        m.store(owner, 12, m.load(owner, 12) & ~128 | 256)
        m.ns['pcc_gc_backend3_remember_owner'](owner, m.load(owner, 12))
    assert m.collect() == 1
    assert target in m.index and owner in m.index and dead not in m.index
    assert m.load(items, 0) == target and m.load(target, 12) & 256
    assert m.load('pcc_gc_backend3_promotion_head', 0) == 0
    assert m.load('pcc_gc_backend3_remembered_owner_head', 0) == 0


@pytest.mark.parametrize('dirty_prefix', (False, True))
def test_partial_promotion_queue_closes_suffix_and_new_dirty_prefix(dirty_prefix):
    m = YoungCutGC3()
    owner, node, _ = m.young(tag=abi.PY_TYPE_LIST)
    m.keep(owner)
    items = m.allocate(33 * 8)
    m.store(owner, 16, 33); m.store(owner, 24, 33); m.store(owner, 32, items)
    target, _, _ = m.young()
    dead, _, _ = m.young()
    if not dirty_prefix:
        m.store(items, 20 * 8, target)
    with m.locked():
        m.store('pcc_gc_backend3_oldify_suspended', 0, 1)
        m.ns['pcc_gc_generational_promote_young_if_known'](owner)
        # Create a genuine partially visited old owner, not a fabricated
        # cursor or a successful queue-drain stub.
        assert m.ns['pcc_gc_backend3_drain_promotion_worklist'](16) == 16
        assert m.ns['_promotion_cursor'](node) == 16
        m.store('pcc_gc_backend3_oldify_suspended', 0, 0)
        if dirty_prefix:
            m.store(items, 0, target)
            m.ns['pcc_gc_note_slot_write_barrier'](owner, items, target)
            assert m.load(owner, 12) & 512
    assert m.collect() == 1
    assert target in m.index and dead not in m.index
    assert m.load(target, 12) & 256
    assert m.load('pcc_gc_backend3_promotion_head', 0) == 0
    assert m.load('pcc_gc_backend3_remembered_owner_head', 0) == 0


@pytest.mark.parametrize('state', (
    'pcc_gc_mark_active', 'pcc_gc_explicit_collect_active',
    'pcc_gc_backend4_remap_active', 'pcc_gc_cms_sweep_pending',
    'pcc_gc_cms_finish_pending', 'pcc_gc_concurrent_sweep_state',
    'pcc_gc_tracing_finish_claim_epoch', 'pcc_gc_backend3_remembered_overflow',
    'pcc_gc_backend3_young_cut_active',
))
def test_incomplete_or_competing_owner_refuses_before_any_walk(state):
    m = YoungCutGC3()
    obj, _, _ = m.young()
    m.store(state, 0, 1)
    assert m.collect() == -1
    assert obj in m.index and m.owner_snapshot_refreshes == 0


def test_finished_major_verdict_must_be_consumed_first():
    m = YoungCutGC3()
    m.store('pcc_gc_tracing_finish_commits', 0, 3)
    assert m.collect() == -1
    m.store('pcc_gc_tracing_no_candidate_commit', 0, 3)
    assert m.collect() == 0


def test_weakref_domain_falls_back_without_invalidating_or_finalizing():
    m = YoungCutGC3()
    obj, _, _ = m.young()
    m.store('py_weakref_head', 0, m.allocate(64))
    assert m.collect() == -1
    assert obj in m.index and not m.events


@pytest.mark.parametrize('mutation', ('roots', 'objects', 'revocation', 'nested-snapshot'))
def test_callback_changes_abort_before_first_death(mutation):
    m = YoungCutGC3()
    target, _, _ = m.young()
    visit = m.ns['pcc_gc_visit_registered_root_slots']
    def change(mode, resolve):
        visit(mode, resolve)
        if mutation == 'roots':
            m.store('pcc_gc_root_registry_revision', 0, m.load('pcc_gc_root_registry_revision', 0) + 1)
        elif mutation == 'objects':
            m.young()
        elif mutation == 'revocation':
            m.ns['pcc_gc_backend3_revoke_closed_young_domain']()
        else:
            context = m.load('pcc_gc_backend3_step_pin_snapshot', 0)
            m.store(context, 16, -1)
        return 0
    m.ns['pcc_gc_visit_registered_root_slots'] = change
    assert m.collect() == -1
    assert target in m.index
    assert m.load('pcc_gc_backend3_young_cut_freed', 0) == 0
    assert m.load('pcc_gc_backend3_young_cut_active', 0) == 0


@pytest.mark.parametrize('old_objects', (64, 256, 1024))
def test_old_heap_size_does_not_add_work_to_young_cut(old_objects):
    m = YoungCutGC3()
    for _ in range(old_objects):
        obj, _, _ = m.value()
        m.store(obj, 12, m.load(obj, 12) & ~128 | 256)
    dead, dead_node, _ = m.young()
    reads = []
    index = m.ns['pcc_gc_object_index_find']
    m.ns['pcc_gc_object_index_find'] = lambda obj: reads.append(obj) or index(obj)
    m.ns['pcc_gc_object_list_head'] = lambda: pytest.fail('old heap walk')
    node_next = m.ns['pcc_gc_object_node_next']
    def unlink_next(node):
        assert node == dead_node, 'walked an old node instead of unlinking the dead young node'
        return node_next(node)
    m.ns['pcc_gc_object_node_next'] = unlink_next
    assert m.collect() == 1
    assert dead not in m.index
    # Exact lookup count is independent of n, 4n, 16n old objects.
    assert len(reads) == 6


@pytest.fixture(scope='module')
def native_young_cut(tmp_path_factory, pcc_runtime_archive):
    source = Path(__file__).with_name('fixtures') / 'gc3_closed_young_collection.c'
    executable = tmp_path_factory.mktemp('young-cut-native') / 'young-cut'
    compile_owned_c_with_runtime(source, executable, runtime_archive=pcc_runtime_archive)
    return executable


@pytest.mark.parametrize('backend, case', (
    *((backend, 'unproved') for backend in range(5)),
    *((backend, 'dead') for backend in range(5)),
    *((backend, 'scalars') for backend in range(5)),
    *((3, case) for case in ('stack', 'old-edge', 'weak', 'revoked', 'thread')),
))
def test_native_closed_young_cut(native_young_cut, backend, case):
    """Controlled native admission, not an unmodified application's proof."""
    env = dict(os.environ, PATH='/nonexistent', PCC_GC_BACKEND=str(backend))
    for name in ('LD_PRELOAD', 'LD_LIBRARY_PATH', 'DYLD_INSERT_LIBRARIES', 'DYLD_LIBRARY_PATH'):
        env.pop(name, None)
    result = subprocess.run([str(native_young_cut), str(backend), case],
                            text=True, capture_output=True, timeout=20, env=env)
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert result.stderr == ''
    assert f'YOUNG backend={backend} case={case} good=1' in result.stdout


@pytest.mark.parametrize('member', (
    'freestanding_gc_state', 'freestanding_gc_generational_scheduler',
    'freestanding_gc_mapped_roots', 'py_gc_backend', 'py_extension_loader_runtime',
    'freestanding_gc_frame_registry', 'freestanding_gc_root_registry',
))
def test_owned_runtime_members_validate_the_exact_abi(tmp_path, monkeypatch, member):
    """Compile the changed closure, not only a Python-executed body model."""
    from pcc.frontends.python.owned_runtime_build import (
        _build_runtime_module, runtime_build_config, runtime_ir_passes,
    )
    from pcc.backend.elf_x86_64 import parse_relocatable
    def no_external(*args, **kwargs):
        raise AssertionError('owned member compile started an external process')
    monkeypatch.setattr(subprocess, 'Popen', no_external)
    root = Path(__file__).resolve().parents[2] / 'pcc/runtime'
    _build_runtime_module(str(root), member, 'x86_64-unknown-linux-gnu',
                          str(tmp_path), runtime_ir_passes(str(root)),
                          runtime_build_config())
    obj = parse_relocatable((tmp_path / (member + '.o')).read_bytes())
    assert obj.sections
