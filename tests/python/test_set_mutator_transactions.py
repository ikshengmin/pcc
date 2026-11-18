"""Exercise the production two-phase set transaction including failure/reentry."""
from __future__ import annotations

import pytest

from tests.python.test_set_call_slot_roots import Block, Memory, Object


def _operands(memory, left, right):
    slots, tokens = Block(), Block()
    for index in range(10):
        memory.write(slots, index * 8, None)
        memory.write(tokens, index * 8, -1)
        memory.roots[object()] = memory.add(slots, index * 8)
    target, source = memory.wrap(left), memory.wrap(right)
    for index, obj in ((0, target), (4, source)):
        memory.write(slots, index * 8, obj)
        obj.leases += 1
    return target, source, slots, tokens


@pytest.mark.parametrize("replace", (False, True))
def test_all_old_keys_detached_before_first_unlocked_finalizer(replace):
    memory = Memory("drop")
    target, source, slots, tokens = _operands(memory, {"old A", "old B"}, {42})
    expected = {42} if replace else set()
    releases = []
    normal_drop = memory.ns["pcc_gc_store_root"]

    def retire(slot, new):
        old = memory.read(slot, 0)
        normal_drop(slot, new)
        if isinstance(old, Object) and old.value in {"old A", "old B"}:
            assert memory.depth == 0
            # All old slots are simultaneously gone; all replacement keys
            # are already visible. A callback can mutate or collect here.
            assert memory.values(target) == (expected | ({777} if releases else set()))
            releases.append(old.value)
            if len(releases) == 1:
                memory.write(slots, 56, 777)
                memory.ns["_set_call_lookup"](slots, tokens, 0, 777, 2)
                memory.write(slots, 56, None)
                memory.collect("drop")

    memory.ns["pcc_gc_store_root"] = retire
    assert memory.ns["_set_call_replace"](slots, 0, 4 if replace else -1) == 1
    assert set(releases) == {"old A", "old B"}
    assert memory.values(target) == expected | {777}
    assert memory.values(source) == {42}
    assert not memory.root_groups and not memory.pending_frames and memory.depth == 0
    assert memory.error is None


def test_replacement_retain_failure_keeps_receiver_and_preserves_first_error():
    memory = Memory("drop")
    target, source, slots, _ = _operands(memory, {"old"}, {"first", "second"})
    before = target.fields[40].fields.copy()
    count = 0
    retain = memory.ns["pcc_gc_root_copy_owned_prepare_locked"]
    drop = memory.ns["pcc_gc_store_root"]

    def fail_second(dst, src, plan):
        nonlocal count
        count += 1
        if count == 2:
            return -4
        return retain(dst, src, plan)

    def cleanup(slot, value):
        if isinstance(memory.read(slot, 0), Object):
            assert memory.depth == 0 and memory.values(target) == {"old"}
            drop(slot, value)
            memory.error = (7, "secondary cleanup")
        else:
            drop(slot, value)

    memory.ns.update(pcc_gc_root_copy_owned_prepare_locked=fail_second, pcc_gc_store_root=cleanup)
    assert memory.ns["_set_call_replace"](slots, 0, 4) == 0
    assert target.fields[40].fields.keys() == before.keys()
    assert memory.values(source) == {"first", "second"}
    assert memory.values(target) == {"old"}
    assert memory.error == (7, "cannot retain set replacement contents")
    assert not memory.root_groups and not memory.pending_frames


@pytest.mark.parametrize("failure", ("register", "retain", "span", "retarget", None))
def test_replacement_growth_preflights_before_any_public_change(failure):
    memory = Memory("none")
    target, source, slots, _ = _operands(memory, {1, 5}, set(range(12)))
    target.fields[24] = 8
    old = target.fields[40]
    old.fields = {16: 1, 24: 1, 80: 5, 88: 5, 128: 11}
    source_before = memory.values(source)
    table_before = old.fields.copy()
    calls = []
    config = Block()
    config.fields[0] = 0
    # Counted GC4 requires a successful old-span registration, not a
    # fallible new-span registration after a table is already published.
    def register_span(owner, base, size):
        assert memory.depth == 1 and owner is target and base is old and size == 8 * 16
        calls.append("span")
        return -1 if failure == "span" else 0

    def retarget(owner, base, old_bytes, fresh, new_bytes, pairs, pair_count):
        assert memory.depth == 1 and owner is target and base is old
        assert old_bytes == 8 * 16 and new_bytes == 32 * 16 and pair_count == 2
        for i, item in enumerate((1, 5)):
            old_field, new_field = memory.read(pairs, i * 16), memory.read(pairs, i * 16 + 8)
            assert memory.location(old_field, 0) == (old, item * 16 + 8)
            assert memory.location(new_field, 0) == (fresh, item * 16 + 8)
            assert memory.read(new_field, 0) == item
        calls.append("retarget")
        return 0 if failure == "retarget" else 1

    memory.ns.update(global_addr=lambda name: config if name == "pcc_gc_nonmoving_count_free" else 1,
                     pcc_gc_backend4_zpage_register_owner_payload_span=register_span,
                     pcc_gc_backend4_retarget_mutator_payload_locked=retarget)
    if failure == "register":
        memory.ns["pcc_gc_register_continuation_root_node"] = lambda *args: None
    if failure == "retain":
        memory.ns["pcc_gc_root_copy_owned_prepare_locked"] = lambda *args: -1
    result = memory.ns["_set_call_replace"](slots, 0, 4)
    if failure:
        assert result == 0 and memory.error
        assert target.fields[40] is old and target.fields[24] == 8
        assert old.fields == table_before  # same order, identities, cached hashes and version
        assert calls == ([] if failure in ("register", "retain") else
                         ["span"] if failure == "span" else ["span", "retarget"])
    else:
        assert result == 1 and memory.error is None and calls == ["span", "retarget"]
        assert target.fields[40] is not old and target.fields[24] == 32
        assert memory.read(target.fields[40], 32 * 16) == 12
        assert memory.values(target) == set(range(12))
    assert memory.values(source) == source_before
    assert memory.depth == 0 and not memory.root_groups

@pytest.mark.parametrize("method", ["pop", "clear"])
def test_typed_set_mutator_invokes_bound_method_into_consumer_output(method):
    from tests.python.test_shared_call_binding import _emit, _function

    fn = _function(_emit(
        "def take(*, value, **rest):\n    return value\n"
        "def probe(shared: set):\n    return take(value=shared." + method + "())\n"
    ))
    # The bound method and caller's result remain registered. No raw returned
    # key, or pending mutation error, is exposed before argument disposal.
    assert "@py_obj_call_slots(" in fn
    assert "set." + method + ".invoke" in fn
    assert "set." + method + ".output" in fn
    assert "@py_set_" + method + "(" not in fn
