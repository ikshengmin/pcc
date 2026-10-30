"""Changed pointers require locked revalidation even on an address-shard collision."""
from test_address_sharded_leases import SHARDS, addresses, counts, index, leaf, model
from test_foreign_address_leases import _mode_setter


def test_pointer_change_same_shard_releases_then_revalidates_under_lock():
    value = model()
    old, new = addresses(value, collide=True)
    assert old != new and index(value, old) == index(value, new)
    source = leaf(value, old)
    leaf(value, new)
    setter = _mode_setter(value)
    transition_done = []
    fallback_locks = []
    event_start = []

    def replace_across_completed_transition(base, offset, expected, desired):
        assert base == SHARDS and (expected, desired) == (0, 1)
        assert setter(4) == 0
        value.store(source, 0, new)
        assert setter(0) == 0
        event_start.append(len(value.events))
        transition_done.append(True)

    def observe_lock():
        if transition_done:
            fallback_locks.append(True)
        value.lock()

    value.ns["pcc_py_gc_minor_graph_lock"] = observe_lock
    value.on_compare_exchange = replace_across_completed_transition
    assert value.acquire(source) == 2
    assert fallback_locks == [True]
    assert ("reload", source) in value.events[event_start[0]:]
    reservations = [(event[2], event[3]) for event in value.events[event_start[0]:]
                    if event[0] == "cas" and event[1] == SHARDS]
    assert reservations == [(0, 1), (1, 0), (0, 1)]
    expected = [0] * 32
    expected[index(value, new)] = 1
    assert counts(value) == expected and value.active() == 1
    assert value.release(source, 2) == 0 and value.active() == 0
