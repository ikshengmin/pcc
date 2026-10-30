"""Host-only regression for discarded managed reload records."""

from types import SimpleNamespace

import pytest

from pcc.backend import BackendUnavailable
from pcc.backend import self_backend_precise_stackmaps as maps
from pcc.backend.self_backend_kernel import TYPE_KIND_PTR
from pcc.backend.self_backend_value_arena import CompilerInt4


class _Slots:
    def __init__(self, offsets, type_kind=TYPE_KIND_PTR):
        self.offsets = offsets
        self.type_kind = type_kind

    def value_name(self, value_id):
        return "%value" + str(value_id)

    def value_slot_id(self, value_id):
        return value_id if self.offsets[value_id] is not None else -1

    def slot_offset(self, slot_id):
        return self.offsets[slot_id]

    def slot_type_id(self, slot_id):
        return 0

    def type_header(self, type_id):
        assert type_id == 0
        return CompilerInt4(self.type_kind, 0, 0, 0)


def _plan(offsets, origins, *, active=None, ambiguous=(), type_kind=TYPE_KIND_PTR):
    live = maps.PackedManagedLiveness()
    try:
        state = live.append_state(set(range(len(offsets))))
        return maps._planned_managed_reloads(
            SimpleNamespace(name="reload_fixture", frame_size=128),
            _Slots(offsets, type_kind),
            set(origin.root_offset for origin in origins.values())
            if active is None else active,
            live,
            state,
            origins,
            frozenset(ambiguous),
            "x86_64-linux",
        )
    finally:
        live.close()


def _observe_allocations(monkeypatch):
    original = maps.PlannedManagedReload
    calls = []

    def record(*args):
        calls.append(args)
        return original(*args)

    monkeypatch.setattr(maps, "PlannedManagedReload", record)
    return original, calls


@pytest.mark.parametrize("count", [1, 2, 5])
def test_noop_reloads_do_not_allocate_discarded_records(monkeypatch, count):
    _original, calls = _observe_allocations(monkeypatch)
    offsets = [8 * (index + 1) for index in range(count)]
    origins = {
        index: maps._ManagedValueOrigin(-offset, 0)
        for index, offset in enumerate(offsets)
    }
    assert _plan(offsets, origins) == ()
    assert calls == []


def test_mixed_reloads_preserve_order_duplicates_and_derived_same_slot(monkeypatch):
    original, calls = _observe_allocations(monkeypatch)
    origins = {
        0: maps._ManagedValueOrigin(-8, 0),
        1: maps._ManagedValueOrigin(-16, 4),
        2: maps._ManagedValueOrigin(-8, 0),
        3: maps._ManagedValueOrigin(-8, 0),
        4: maps._ManagedValueOrigin(-8, -4),
    }
    result = _plan([8, 16, 24, 24, 32], origins)
    assert result == (
        original(-8, -32, -4),
        original(-8, -24, 0),
        original(-16, -16, 4),
    )
    assert calls == [(-16, -16, 4), (-8, -24, 0), (-8, -24, 0), (-8, -32, -4)]


@pytest.mark.parametrize(
    "offset,root,derived,active,ambiguous,type_kind,message",
    [
        (8, -8, 0, {-8}, (0,), TYPE_KIND_PTR, "ambiguous root provenance"),
        (8, -8, 0, set(), (), TYPE_KIND_PTR, "outlives its active root"),
        (None, -8, 0, {-8}, (), TYPE_KIND_PTR, "no pointer spill slot"),
        (8, -8, 0, {-8}, (), -1, "no pointer spill slot"),
        (0, 0, 0, {0}, (), TYPE_KIND_PTR, "invalid spill slot"),
        (136, -136, 0, {-136}, (), TYPE_KIND_PTR, "invalid spill slot"),
        (7, -7, 0, {-7}, (), TYPE_KIND_PTR, "invalid spill slot"),
        (8, -8, 1 << 31, {-8}, (), TYPE_KIND_PTR, "exceeds x86 imm32"),
        (8, -8, -(1 << 31) - 1, {-8}, (), TYPE_KIND_PTR, "exceeds x86 imm32"),
    ],
)
def test_reload_validation_still_precedes_allocation(
    monkeypatch, offset, root, derived, active, ambiguous, type_kind, message,
):
    _original, calls = _observe_allocations(monkeypatch)
    with pytest.raises(BackendUnavailable, match=message):
        _plan(
            [offset], {0: maps._ManagedValueOrigin(root, derived)},
            active=active, ambiguous=ambiguous, type_kind=type_kind,
        )
    assert calls == []


def test_conflicting_non_noop_reloads_still_fail(monkeypatch):
    _original, calls = _observe_allocations(monkeypatch)
    with pytest.raises(BackendUnavailable, match="share spill offset -24"):
        _plan(
            [8, 24, 24],
            {
                0: maps._ManagedValueOrigin(-8),
                1: maps._ManagedValueOrigin(-8),
                2: maps._ManagedValueOrigin(-16),
            },
        )
    assert calls == [(-8, -24, 0), (-16, -24, 0)]


def test_empty_and_untracked_liveness_allocate_no_reload_records(monkeypatch):
    _original, calls = _observe_allocations(monkeypatch)
    assert _plan([], {}) == ()
    assert _plan([8], {}) == ()
    assert calls == []
