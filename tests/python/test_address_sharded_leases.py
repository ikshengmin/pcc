"""Public 0/1/2 tokens retain complete mode exclusion across address shards."""
import ast

import pytest

from pcc.runtime.py import py_abi_constants as abi
from test_foreign_address_leases import Model, RUNTIME, _mode_setter
from test_runtime_entry_handoff import HandoffModel


CENTRAL = "pcc_gc_foreign_lease_active"
SHARDS = "pcc_gc_mode_lease_shards"
COUNT = 32
STRIDE = 128
MAXIMUM = (1 << 63) - 1


def model(backend=0):
    result = Model(backend)
    result.store("pcc_gc_refcount_atomic", 0, int(backend == 0))
    return result


def index(value, address):
    return value.ns["_mode_lease_shard_index"](address)


def counts(value):
    return [value.load(SHARDS, shard * STRIDE) for shard in range(COUNT)]


def addresses(value, collide=False):
    first = 65536
    for second in range(first + 16, first + 65536, 16):
        if (index(value, first) == index(value, second)) == collide:
            return first, second
    pytest.fail("could not find requested address-shard relation")


def leaf(value, address):
    return value.object(address, node=False, flags=262144, tag=abi.PY_TYPE_INT)


def test_storage_has_32_separate_128_byte_counters():
    tree = ast.parse((RUNTIME / "freestanding_gc_root_operations.py").read_text())
    definitions = [node.value for node in tree.body if isinstance(node, ast.Expr)
                   and isinstance(node.value, ast.Call)
                   and isinstance(node.value.func, ast.Name)
                   and node.value.func.id == "define_global_i64_array"
                   and node.value.args and isinstance(node.value.args[0], ast.Constant)
                   and node.value.args[0].value == SHARDS]
    assert len(definitions) == 1
    assert len(definitions[0].args) == COUNT * STRIDE // 8 + 1
    assert all(isinstance(node, ast.Constant) and node.value == 0
               for node in definitions[0].args[1:])


@pytest.mark.parametrize("collide", (False, True))
def test_distinct_objects_and_collisions_keep_independent_tokens(collide):
    value = model()
    first, second = addresses(value, collide)
    sources = [leaf(value, first), leaf(value, second)]
    assert [value.acquire(source) for source in sources] == [2, 2]
    expected = [0] * COUNT
    expected[index(value, first)] += 1
    expected[index(value, second)] += 1
    assert counts(value) == expected and value.load(CENTRAL) == 0
    assert value.active() == 2
    assert value.release(sources[1], 2) == 0 and value.active() == 1
    assert value.release(sources[0], 2) == 0 and value.active() == 0


def test_root_copy_move_and_release_keep_public_token_and_address_shard():
    value = HandoffModel(0)
    value.store("pcc_gc_refcount_atomic", 0, 1)
    source = value.object(node=False, flags=262144, tag=abi.PY_TYPE_INT)
    copied, moved = value.empty(90000), value.empty(90008)
    assert value.acquire(source) == 2
    token = value.copy(copied, source)
    assert token == 2 and value.references[1000] == 2 and value.active() == 2
    before = counts(value)
    assert value.move(moved, copied) == 0
    assert value.load(copied) == 0 and value.load(moved) == value.load(source)
    assert counts(value) == before and value.references[1000] == 2
    assert value.release(moved, token) == 0 and value.active() == 1
    assert value.release(source, 2) == 0 and value.active() == 0


@pytest.mark.parametrize("occupied", range(COUNT))
def test_transition_partial_freeze_restores_prefix_and_preserves_live_shard(occupied):
    value = model()
    value.store(SHARDS, occupied * STRIDE, 7)
    before = counts(value)
    setter = _mode_setter(value)
    value.ns["_tracing_cycle_epoch_advance_unlocked"] = lambda: pytest.fail("mutated epoch with live shard")
    assert setter(4) == -1
    assert value.load(CENTRAL) == 0 and counts(value) == before
    assert value.load("pcc_gc_backend_selected") == 0 and value.active() == 7


def test_full_freeze_and_thaw_leave_central_reserved_until_last():
    value = model()
    value.store(CENTRAL, 0, -1)
    assert value.ns["pcc_gc_mode_leases_freeze"]() == 0
    assert counts(value) == [-1] * COUNT and value.active() == -1
    assert value.ns["_mode_lease_reserve_shard"](17) == -1
    value.ns["pcc_gc_mode_leases_thaw"]()
    assert counts(value) == [0] * COUNT and value.load(CENTRAL) == -1
    assert value.ns["_mode_lease_reserve_shard"](17) == -1
    value.store(CENTRAL, 0, 0)
    assert value.active() == 0


def test_thaw_tail_race_rolls_back_exact_reservation_before_header_read():
    value = model()
    source = leaf(value, 65536)
    selected = index(value, 65536)

    def complete_thaw_but_hold_central(base, offset, expected, desired):
        assert (base, offset, expected, desired) == (SHARDS, selected * STRIDE, 0, 1)
        value.store(CENTRAL, 0, -1)
        assert value.ns["pcc_gc_mode_leases_freeze"]() == 0
        value.ns["pcc_gc_mode_leases_thaw"]()

    original = value.ns["load_i32"]
    def forbid_header(base, offset=0):
        assert base != 65536, "header read while transition still owns central sentinel"
        return original(base, offset)

    value.ns["load_i32"] = forbid_header
    value.on_compare_exchange = complete_thaw_but_hold_central
    assert value.acquire(source) == -1
    assert counts(value) == [0] * COUNT and value.load(CENTRAL) == -1


@pytest.mark.parametrize("collide", (False, True))
def test_completed_transition_pointer_change_never_debits_rewritten_slot(collide):
    value = model()
    old, new = addresses(value, collide)
    source = leaf(value, old)
    leaf(value, new)
    setter = _mode_setter(value)

    def move_across_completed_modes(base, offset, expected, desired):
        assert base == SHARDS and offset == index(value, old) * STRIDE
        assert setter(4) == 0
        value.store(source, 0, new)
        assert setter(0) == 0

    value.on_compare_exchange = move_across_completed_modes
    assert value.acquire(source) == 2
    expected = [0] * COUNT
    expected[index(value, new)] = 1
    assert counts(value) == expected and value.load(CENTRAL) == 0
    assert value.release(source, 2) == 0 and value.active() == 0


@pytest.mark.parametrize("backend", range(5))
@pytest.mark.parametrize("target", range(5))
def test_mode_and_same_mode_reset_refuse_live_mode_token(backend, target):
    value = model(backend)
    source = leaf(value, 65536)
    assert value.acquire(source) == 2
    setter = _mode_setter(value)
    assert setter(target) == -1
    assert value.active() == 1 and value.load("pcc_gc_backend_selected") == backend
    assert value.release(source, 2) == 0
    assert setter(target) == 0 and value.active() == 0


def test_mixed_indexed_and_mode_guards_preserve_central_and_shard_owners():
    value = model(3)
    indexed = value.object(65536)
    mode = leaf(value, 131072)
    assert value.acquire(indexed) == 1 and value.acquire(mode) == 2
    assert value.load(CENTRAL) == 1 and sum(counts(value)) == 1 and value.active() == 2
    assert value.release(mode, 2) == 0 and value.load(CENTRAL) == 1
    assert value.release(indexed, 1) == 0 and value.active() == 0


def test_shard_overflow_is_nonwrapping_and_preserves_other_counters():
    value = model()
    source = leaf(value, 65536)
    selected = index(value, 65536)
    value.store(SHARDS, selected * STRIDE, MAXIMUM)
    before = counts(value)
    assert value.acquire(source) == -2
    assert counts(value) == before and value.load(CENTRAL) == 0


def test_individual_caps_do_not_wrap_unrepresentable_diagnostic_sum():
    value = model()
    source = leaf(value, 65536)
    value.store(CENTRAL, 0, MAXIMUM)
    assert value.acquire(source) == 2  # The shard has its own counter capacity.
    assert value.active() == -2 and value.load(CENTRAL) == MAXIMUM
    assert value.release(source, 2) == 0 and value.active() == MAXIMUM
    indexed = value.object(131072)
    value.store("pcc_gc_refcount_atomic", 0, 0)
    assert value.acquire(indexed) == -2  # Existing central overflow stays checked.
    assert value.count(131072) == 0 and value.load(CENTRAL) == MAXIMUM


@pytest.mark.parametrize("token", (-1, 3, 4, MAXIMUM))
def test_invalid_tokens_do_not_mutate_any_ledger(token):
    value = model()
    source = leaf(value, 65536)
    assert value.acquire(source) == 2
    before = dict(value.memory)
    assert value.release(source, token) == -1 and value.memory == before
    assert value.release(source, 2) == 0


def test_mode_underflow_cannot_consume_another_shard_or_central_owner():
    value = model()
    first, second = addresses(value)
    empty, live = leaf(value, first), leaf(value, second)
    assert value.acquire(live) == 2
    value.store(CENTRAL, 0, 1)
    before = dict(value.memory)
    assert value.release(empty, 2) == -3 and value.memory == before
    assert value.release(live, 2) == 0 and value.load(CENTRAL) == 1


@pytest.mark.parametrize("location", ("central", 0, 31))
def test_quiescent_diagnostic_reports_any_transition_sentinel(location):
    value = model()
    if location == "central":
        value.store(CENTRAL, 0, -1)
    else:
        value.store(SHARDS, location * STRIDE, -1)
    assert value.active() == -1


def test_quiescent_diagnostic_detects_cross_shard_sum_overflow():
    value = model()
    value.store(SHARDS, 0, MAXIMUM)
    value.store(SHARDS, STRIDE, 1)
    before = dict(value.memory)
    assert value.active() == -2 and value.memory == before
