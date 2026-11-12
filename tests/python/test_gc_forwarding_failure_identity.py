"""Failed forwarding must not merge distinct live objects' public identities.

Contract: docs/project-intent.md preserves Python identity and recoverable
allocation failure. Actual index, installer, identity getter, py_obj_id and
GC3 type-admission bodies run through runtime_body_model's shared loader.
Raw memory, graph exclusion, allocator, configuration and counted-reference
operations are finite doubles. The decref hook is an observation boundary,
not execution of a finalizer. This is not native ABI or collector proof.

Ordinary allocator failures exercise the public raw installer. Preallocated
metadata OOM during preparation precedes identity mutation; its later duplicate-insert cases
explicitly inject a defensive hidden-publication state, not normal OOM.
The identity-sequence checks use small values, not counter-exhaustion cases.
"""
from __future__ import annotations

import pytest

from pcc.frontends.python.codegen.freestanding_abi_constants import ABI_CONSTANTS
from pcc.runtime.py import py_abi_constants as abi
from tests.python.runtime_body_model import (
    gc_state_initial_values,
    load_functions,
)
from tests.python.test_gc_forwarding_publication_oom import (
    PLAN_METADATA,
    Publication,
)
from tests.python.test_gc_stable_object_membership import ROOT


class IdentityInstall(Publication):
    def __init__(self, backend):
        self.fail_edge_malloc = False
        self.edge_malloc_failures = 0
        self.decref_observer = None
        super().__init__(backend)
        for name, value in gc_state_initial_values().items():
            self.words.setdefault(name, value)
        # Replace Install's out-of-scope type-admission doubles before any
        # install. object() below also replaces its synthetic object shape.
        self.ns.update({name: value for name, value in vars(abi).items()
                        if name.startswith('PY_TYPE_')})
        self.ns.update({
            'abi_constant': ABI_CONSTANTS.__getitem__,
            'ptr_to_int': lambda value: value,
            'pcc_gc_backend': lambda: self.load('pcc_gc_backend_selected'),
        })
        load_functions(
            ROOT / 'freestanding_gc_generational_oldification.py',
            {'pcc_gc_generational_oldify_supported_tag'}, self.ns,
        )
        load_functions(ROOT / 'py_dunder.py', {'py_obj_id'}, self.ns)
        assert self.call('pcc_gc_generational_oldify_supported_tag',
                         abi.PY_TYPE_FLOAT) == 1
        assert self.call('pcc_gc_generational_oldify_supported_tag',
                         abi.PY_TYPE_LIST) == 0

    def object(self):
        # FLOAT has a 16-byte header and an inline 8-byte double payload.
        # Use the generated ABI offsets/tag, not a fake GC3 admission rule.
        size = abi.PYFLOATOBJECT_VALUE_OFFSET + 8
        obj = self.calloc(1, size)
        node = self.calloc(1, 88)
        self.store(obj, abi.PYOBJECTHEADER_REFCOUNT_OFFSET, 3)
        self.store(obj, abi.PYOBJECTHEADER_TYPE_TAG_OFFSET, abi.PY_TYPE_FLOAT)
        self.store(obj, abi.PYFLOATOBJECT_VALUE_OFFSET, 0)
        self.store(node, 0, obj)
        self.store(node, 8, size)
        assert self.call('pcc_gc_object_index_insert', obj, node) == 1
        return obj

    def allocate(self, size):
        if self.fail_edge_malloc and size == 56:
            self.fail_edge_malloc = False
            self.edge_malloc_failures += 1
            self.allocations.append((0, size))
            return 0
        return super().allocate(size)

    def change_count(self, obj, delta):
        super().change_count(obj, delta)
        if delta == -1 and self.decref_observer is not None:
            self.decref_observer(obj)

    def identity(self, obj):
        stable_id = self.call('pcc_gc_object_id', obj)
        assert stable_id > 0
        assert self.call('py_obj_id', obj) == stable_id * 16
        return stable_id

    def preallocated_install(self, source, target, plan):
        self.lock()
        try:
            return self.call('pcc_gc_install_forwarding_preallocated_unlocked',
                             source, target, plan)
        finally:
            self.unlock()

    def assert_committed(self, source, target, stable_id):
        node = self.exact('forwarding', source)
        assert node and self.load(node, 8) == target
        assert self.exact('forwarding_target', target) == node
        assert self.identity(source) == self.identity(target) == stable_id
        assert self.call('pcc_gc_note_relocation_read', source) == target


@pytest.mark.parametrize('backend', (4,))
@pytest.mark.parametrize('target_observed', (False, True))
@pytest.mark.parametrize('failure', ('edge_malloc', 'source_record', 'reverse_record'))
def test_public_install_oom_preserves_independent_identity_then_retry_commits(
    backend, target_observed, failure,
):
    model = IdentityInstall(backend)
    source_id = model.identity(model.a)
    target_id = model.identity(model.t) if target_observed else None
    assert bool(model.exact('identity', model.t)) == target_observed
    before = model.edge_state()
    record_attempts = model.record_attempts
    if failure == 'edge_malloc':
        model.fail_edge_malloc = True
    else:
        model.record_failure_after = 0 if failure == 'source_record' else 1

    assert model.install(model.a, model.t) == -1
    if failure == 'edge_malloc':
        assert model.edge_malloc_failures == 1
        assert model.record_failures == 0
    else:
        assert model.record_failures == 1
        assert model.record_attempts - record_attempts == (
            1 if failure == 'source_record' else 2
        )
    assert model.edge_state() == before
    assert model.identity(model.a) == source_id
    # The installer may retain prepared identity metadata. It must already
    # have an independent ID, before the next public identity query.
    target_node = model.exact('identity', model.t)
    assert target_node and model.load(target_node, 8) != source_id
    failed_target_id = model.identity(model.t)
    if target_observed:
        assert failed_target_id == target_id
    next_id = model.load('pcc_gc_next_object_id')
    assert next_id == failed_target_id + 1
    independent_id = model.identity(model.u)
    assert independent_id == next_id
    assert len({source_id, failed_target_id, independent_id}) == 3
    assert model.call('pcc_gc_note_relocation_read', model.a) == model.a
    assert model.call('pcc_gc_note_relocation_read', model.t) == model.t

    assert model.install(model.a, model.t) == 0
    model.assert_committed(model.a, model.t, source_id)
    assert model.identity(model.u) == independent_id


@pytest.mark.parametrize('backend', (4,))
@pytest.mark.parametrize('target_observed', (False, True))
def test_retarget_record_oom_preserves_old_edge_and_ids_until_retry(
    backend, target_observed,
):
    model = IdentityInstall(backend)
    source_id = model.identity(model.a)
    assert model.install(model.a, model.t) == 0
    original_node = model.exact('forwarding', model.a)
    # A real half-full reverse table makes the new target require growth.
    # Keep the test bounded with a real small-table rehash and three other
    # valid installed edges, rather than fake reverse entries or statuses.
    cells = model.cells('forwarding_target')
    model.lock()
    try:
        assert model.call('pcc_gc_index_py_rehash_slots',
                          cells[0], cells[1], cells[3], 8) == 0
    finally:
        model.unlock()
    others = [(model.object(), model.object()) for _ in range(3)]
    for source, target in others:
        assert model.install(source, target) == 0
    assert model.load(cells[1]) == 8 and model.load(cells[3]) == 4
    target_id = model.identity(model.u) if target_observed else None
    before = model.edge_state()
    reverse_before = model.state('forwarding_target')
    rc_before = list(model.rc_events)
    model.record_failure_after = 0

    assert model.install(model.a, model.u) == -1
    assert model.record_failures == 1
    assert model.edge_state() == before
    assert model.state('forwarding_target') == reverse_before
    assert model.rc_events == rc_before
    assert model.identity(model.a) == model.identity(model.t) == source_id
    failed_target_id = model.identity(model.u)
    assert failed_target_id != source_id
    if target_observed:
        assert failed_target_id == target_id
    model.assert_committed(model.a, model.t, source_id)

    observations = []

    def observe_old_target_decref(obj):
        assert obj == model.t
        # This primitive models the boundary where decref may call user
        # code. Read the public API and both real indexes at that instant.
        model.assert_committed(model.a, model.u, source_id)
        assert model.exact('forwarding', model.a) == original_node
        assert model.exact('forwarding_target', model.t) == 0
        observations.append(model.call('py_obj_id', model.u))

    model.decref_observer = observe_old_target_decref
    assert model.install(model.a, model.u) == 0
    assert observations == [source_id * 16]
    model.assert_committed(model.a, model.u, source_id)
    assert model.load(model.t, 0) == 3 and model.load(model.u, 0) == 4
    assert model.load('pcc_gc_forwarding_population') == 4
    for source, target in others:
        assert model.load(model.exact('forwarding', source), 8) == target

    # Reinstalling the same edge takes the explicit same-target branch.
    # It must not acquire/release another owner or add another edge.
    same_target_rc = list(model.rc_events)
    population = model.load('pcc_gc_forwarding_population')
    assert model.install(model.a, model.u) == 0
    model.assert_committed(model.a, model.u, source_id)
    assert model.exact('forwarding', model.a) == original_node
    assert model.identity(model.t) == source_id
    assert model.rc_events == same_target_rc
    assert observations == [source_id * 16]
    assert model.load(model.t, 0) == 3 and model.load(model.u, 0) == 4
    assert model.load('pcc_gc_forwarding_population') == population


@pytest.mark.parametrize('backend', (4,))
@pytest.mark.parametrize('target_observed', (False, True))
@pytest.mark.parametrize('failed_metadata', range(6))
def test_prepare_metadata_oom_precedes_identity_change_and_allows_retry(
    backend, target_observed, failed_metadata,
):
    model = IdentityInstall(backend)
    source_id = model.identity(model.a)
    target_id = model.identity(model.t) if target_observed else None
    target_node = model.exact('identity', model.t)
    before = model.edge_state()
    next_id = model.load('pcc_gc_next_object_id')
    model.record_failure_after = failed_metadata
    model.guard_allocators = True
    try:
        assert model.call('pcc_gc_forwarding_install_plan_prepare', model.a, model.t) == 0
    finally:
        model.guard_allocators = False
    assert model.depth == 0
    assert model.load('pcc_gc_next_object_id') == next_id
    assert model.record_failures == 1 and model.edge_state() == before
    assert model.identity(model.a) == source_id
    assert model.exact('identity', model.t) == target_node
    if target_observed:
        assert model.identity(model.t) == target_id != source_id
    # For an unobserved target no metadata has been installed yet. Keep it
    # unobserved through retry so the production fresh-target branch runs.
    plan = model.call('pcc_gc_forwarding_install_plan_prepare', model.a, model.t)
    assert plan
    with model.allocation_free_commit():
        assert model.call('pcc_gc_install_forwarding_preallocated_unlocked',
                          model.a, model.t, plan) == 0
    model.assert_committed(model.a, model.t, source_id)
    model.call('pcc_gc_forwarding_install_plan_finish', plan)
    assert model.freed.count(plan) == 1


@pytest.mark.parametrize('target_observed', (False, True))
@pytest.mark.parametrize('missing_offset', PLAN_METADATA)
def test_missing_metadata_retry_preserves_independent_target_identity(
    target_observed, missing_offset,
):
    model = IdentityInstall(4)
    source_id = model.identity(model.a)
    target_id = model.identity(model.t) if target_observed else None
    target_node = model.exact('identity', model.t)
    plan = model.call('pcc_gc_forwarding_install_plan_prepare', model.a, model.t)
    assert plan
    saved = model.load(plan, missing_offset)
    assert saved
    model.store(plan, missing_offset, 0)
    before = model.edge_state()
    next_id = model.load('pcc_gc_next_object_id')
    with model.allocation_free_commit():
        assert model.call('pcc_gc_install_forwarding_preallocated_unlocked',
                          model.a, model.t, plan) == -1
    assert model.edge_state() == before
    assert model.load('pcc_gc_next_object_id') == next_id
    assert model.identity(model.a) == source_id
    assert model.exact('identity', model.t) == target_node
    if target_observed:
        assert model.identity(model.t) == target_id != source_id
    model.store(plan, missing_offset, saved)
    with model.allocation_free_commit():
        assert model.call('pcc_gc_install_forwarding_preallocated_unlocked',
                          model.a, model.t, plan) == 0
    model.assert_committed(model.a, model.t, source_id)
    model.call('pcc_gc_forwarding_install_plan_finish', plan)
    assert model.freed.count(plan) == 1


@pytest.mark.parametrize('backend', (4,))
@pytest.mark.parametrize('hidden_kind,target_observed', (
    ('forwarding', False),
    ('forwarding', True),
    ('forwarding_target', True),
))
def test_defensive_preallocated_hidden_duplicate_keeps_target_identity(
    backend, hidden_kind, target_observed,
):
    model = IdentityInstall(backend)
    assert model.install(model.a, model.t) == 0
    source, target = ((model.a, model.u) if hidden_kind == 'forwarding'
                      else (model.b, model.t))
    source_id = model.identity(source)
    target_id = model.identity(target) if target_observed else None
    publication_cell = 'pcc_py_gc_' + hidden_kind + '_published'
    publication = model.load(publication_cell)
    assert publication
    # Deliberately invalid/legacy hidden publication. Ordinary record OOM
    # cannot create this state. The real exact index still owns its key;
    # no insertion/lookup return value is replaced with a failure stub.
    model.store(publication_cell, 0, 0)
    key = source if hidden_kind == 'forwarding' else target
    assert model.exact(hidden_kind, key) != 0
    assert model.find(hidden_kind, key) == 0
    plan = model.call('pcc_gc_forwarding_install_plan_prepare', source, target)
    assert plan
    before = model.edge_state()
    rc_before = list(model.rc_events)
    try:
        assert model.preallocated_install(source, target, plan) == -1
        assert model.record_failures == 0
        assert model.edge_state() == before and model.rc_events == rc_before
        assert model.identity(source) == source_id
        target_node = model.exact('identity', target)
        assert target_node and model.load(target_node, 8) != source_id
        failed_target_id = model.identity(target)
        if target_observed:
            assert failed_target_id == target_id
        # A retained fresh target identity consumed its own sequence value;
        # later independent identity creation must not reuse that value.
        next_id = model.load('pcc_gc_next_object_id')
        if not target_observed:
            assert next_id == failed_target_id + 1
        independent = model.object()
        independent_id = model.identity(independent)
        assert independent_id == next_id
        assert independent_id not in (source_id, failed_target_id)
    finally:
        model.store(publication_cell, 0, publication)
        model.call('pcc_gc_forwarding_install_plan_finish', plan)
    assert model.freed.count(plan) == 1
    original = model.exact('forwarding', model.a)
    assert original and model.load(original, 8) == model.t
    assert model.exact('forwarding_target', model.t) == original
