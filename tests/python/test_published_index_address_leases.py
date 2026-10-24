"""Positive published-index evidence may use a counted nonmoving lease."""

import pytest

from pcc.runtime.py import py_abi_constants as abi
from tests.python.test_foreign_address_leases import Model, _mode_setter


def atomic_model():
    model = Model(0)
    model.store("pcc_gc_refcount_atomic", 0, 1)
    return model


@pytest.mark.parametrize("flags", (0, 1, 262144, 1 | 262144))
@pytest.mark.parametrize("tag", (abi.PY_TYPE_STR, abi.PY_TYPE_LIST, abi.PY_TYPE_FILE))
def test_positive_exact_index_hit_keeps_independent_mode_guards(flags, tag):
    model = atomic_model()
    slot = model.object(node=False, proven=False, flags=flags, tag=tag)
    probes = []
    def published(value):
        assert model.depth == 0 and model.active() > 0
        assert value == model.load(slot)
        probes.append(value)
        return 1
    model.ns["pcc_gc_managed_pointer_index_seen"] = published
    model.ns["pcc_gc_managed_pointer_index_contains"] = lambda value: int(value == 1000)
    model.ns["pending_exception"] = pending = object()
    tokens = [model.acquire(slot), model.acquire(slot)]
    assert tokens == [2, 2] and probes == [1000, 1000] and model.active() == 2
    assert not any(event[0] == "lock" for event in model.events)
    assert _mode_setter(model)(4) == -1
    assert model.release(slot, tokens[0]) == 0 and model.active() == 1
    assert _mode_setter(model)(0) == -1
    assert model.release(slot, tokens[1]) == 0 and model.active() == 0
    assert _mode_setter(model)(4) == 0
    assert model.ns["pending_exception"] is pending


def test_unknown_published_result_uses_locked_membership():
    model = atomic_model()
    slot = model.object(node=False, proven=False, flags=262144)
    model.ns["pcc_gc_managed_pointer_index_seen"] = lambda value: 0
    def contains(value):
        assert model.depth > 0 and value == 1000
        return 1
    model.ns["pcc_gc_managed_pointer_index_contains"] = contains
    assert model.acquire(slot) == 2 and model.active() == 1
    assert ("reload", slot) in model.events
    assert model.release(slot, 2) == 0 and model.active() == 0


def test_uncertain_probe_drops_guard_then_reloads_after_parking():
    model = atomic_model()
    slot = model.object(node=False, proven=False, flags=262144)
    model.object(2000)
    def parked():
        assert model.active() == 0
        model.store("pcc_gc_refcount_atomic", 0, 0)
        model.store("pcc_gc_backend_selected", 0, 4)
        model.store(slot, 0, 2000)
    model.on_lock = parked
    assert model.acquire(slot) == 1
    assert model.active() == 1 and model.count(2000) == 1
    assert ("reload", slot) in model.events
    assert model.release(slot, 1) == 0 and model.active() == 0


def test_completed_transition_rechecks_mode_before_published_lookup():
    model = atomic_model()
    slot = model.object(node=False, proven=False, flags=262144)
    def switch(base, offset, expected, desired):
        assert (expected, desired) == (0, 1)
        assert _mode_setter(model)(4) == 0
        model.object(2000)
        model.store(slot, 0, 2000)
    model.on_compare_exchange = switch
    model.ns["pcc_gc_managed_pointer_index_seen"] = lambda value: pytest.fail("old mode used after transition")
    assert model.acquire(slot) == 1 and model.count(2000) == 1
    assert model.release(slot, 1) == 0 and model.active() == 0


@pytest.mark.parametrize("backend", range(5))
def test_moving_or_checked_modes_keep_existing_indexed_path(backend):
    model = Model(backend)
    slot = model.object(flags=1, proven=False)
    model.ns["pcc_gc_managed_pointer_index_seen"] = lambda value: pytest.fail("fast gate was disabled")
    assert model.acquire(slot) == 1 and model.count() == 1
    assert model.release(slot, 1) == 0 and model.active() == 0


def test_dying_registered_object_never_uses_positive_index_fast_path():
    model = atomic_model()
    slot = model.object(node=False, proven=False, flags=524288)
    model.ns["pcc_gc_managed_pointer_index_seen"] = lambda value: pytest.fail("dying owner admitted")
    model.ns["pcc_gc_managed_pointer_index_contains"] = lambda value: 1
    assert model.acquire(slot) == -1 and model.active() == 0


def test_unknown_object_still_fails_after_an_uncertain_probe():
    model = atomic_model()
    slot = model.object(node=False, proven=False)
    assert model.acquire(slot) == -1 and model.active() == 0
