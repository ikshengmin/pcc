"""pcc_gc_pointer_is_managed answers the immortal singletons without the graph lock.

Runs the production body over doubles for the three lock-free probes and the
locked chain. The native five-collector answers are covered by
test_runtime_pointer_provenance.py; this pins which path produces them.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from tests.python.runtime_body_model import load_functions


SOURCE = Path(__file__).resolve().parents[2] / "pcc/runtime/py/py_gc_backend.py"
SINGLETONS = {
    "py_None": 0x1000,
    "py_True": 0x1010,
    "py_False": 0x1020,
    "py_NotImplemented": 0x1030,
    "py_Ellipsis": 0x1040,
}
HEAP = 0x7000_0010
INDEXED = 0x7100_0010
REGISTERED = 0x7200_0010
UNKNOWN = 0x7300_0010


class Probe:
    def __init__(self, *, locked_answer=0):
        self.events = []
        self.locked_answer = locked_answer
        self.namespace = {
            "ptr_is_null": lambda value: int(value == 0),
            "is_tagged_int": lambda value: value & 1,
            "ptr_eq": lambda first, second: int(first == second),
            "global_load_ptr": self.load,
            "pcc_gc_granule_is_object_start": lambda obj: self.probe("granule", obj == HEAP),
            "pcc_gc_object_index_seen": lambda obj: self.probe("index", obj == INDEXED),
            "pcc_gc_managed_pointer_index_seen": lambda obj: self.probe(
                "registered", obj == REGISTERED
            ),
            "_object_graph_lock": lambda: self.events.append("lock"),
            "_object_graph_unlock": lambda: self.events.append("unlock"),
            # The locked chain is the authority for everything left.
            "_pointer_is_managed_no_lock": self.locked,
        }
        load_functions(SOURCE, {"pcc_gc_pointer_is_managed"}, self.namespace)

    def load(self, name):
        self.events.append("load:" + name)
        return SINGLETONS[name]

    def probe(self, name, hit):
        self.events.append(name)
        return int(hit)

    def locked(self, obj):
        assert self.events[-1] == "lock"
        self.events.append("locked-chain")
        return self.locked_answer

    def ask(self, obj):
        return self.namespace["pcc_gc_pointer_is_managed"](obj)


@pytest.mark.parametrize("name", sorted(SINGLETONS))
def test_a_singleton_is_answered_before_the_graph_lock(name):
    probe = Probe()
    assert probe.ask(SINGLETONS[name]) == 1
    assert "lock" not in probe.events and "locked-chain" not in probe.events
    # After the three probes a heap object answers, never instead of them.
    assert probe.events[:3] == ["granule", "index", "registered"]


@pytest.mark.parametrize(
    "obj, probes",
    [
        (HEAP, ["granule"]),
        (INDEXED, ["granule", "index"]),
        (REGISTERED, ["granule", "index", "registered"]),
    ],
    ids=["slab-object", "indexed-object", "registered-pointer"],
)
def test_a_heap_object_pays_no_singleton_compare(obj, probes):
    probe = Probe()
    assert probe.ask(obj) == 1
    assert probe.events == probes


@pytest.mark.parametrize("answer", [0, 1])
def test_every_other_pointer_still_takes_the_locked_chain(answer):
    probe = Probe(locked_answer=answer)
    assert probe.ask(UNKNOWN) == answer
    assert probe.events[-3:] == ["lock", "locked-chain", "unlock"]
    assert [event for event in probe.events if event.startswith("load:")] == [
        "load:" + name
        for name in ("py_None", "py_True", "py_False", "py_NotImplemented", "py_Ellipsis")
    ]


@pytest.mark.parametrize("obj", [0, 0x7000_0011], ids=["null", "tagged-int"])
def test_values_without_a_header_are_decided_from_their_bits(obj):
    probe = Probe()
    assert probe.ask(obj) == 0
    assert probe.events == []
