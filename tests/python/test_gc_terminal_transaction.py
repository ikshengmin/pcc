"""Production terminal/unregister bodies with deterministic raw-memory owners.

Specification: docs/project-intent.md (five-GC lifetime equality). These host
contracts count transactions, not native instructions or performance. Native
deferred-free and tracking fixtures exercise the real allocation lifecycles.
"""
from __future__ import annotations

import pytest

from tests.python.test_gc_deferred_free import RUNTIME, TERMINAL, Runtime, _load


class TerminalRuntime(Runtime):
    def __init__(self, backend=1, state=2):
        super().__init__(backend)
        self.define_global("pcc_gc_scalar_sweep_state", state)
        self.exact, self.granules = set(), {}
        self.unlock_hook = self.note_hook = None
        self.ns.update({
            "_EMPTY": 0,
            "_object_graph_lock": self.lock,
            "_object_graph_unlock": self.unlock,
            "_gc0_terminal_free_has_no_metadata": lambda obj: 0,
            "_gc3_forwarded_target_may_die": lambda obj: 1,
            "pcc_gc_pointer_is_managed": lambda obj: int(
                self.granules.get(obj) == "live" or obj in self.exact or obj in self.nodes),
            "pcc_gc_object_index_find": self.find,
            "pcc_gc_granule_is_object_start": lambda obj: (
                1 if self.granules.get(obj) == "live" else -1),
            "pcc_gc_granule_object_retire": self.retire,
            # A LIVE exact cell retires with its lifecycle CAS alone; the
            # transaction order checked here is the same retirement.
            "pcc_allocator_object_retire_live": self.retire_validated_live,
            "pcc_gc_managed_pointer_index_remove": self.remove_exact,
            "pcc_gc_note_object_freeing": self.note,
            "_minor_block_containing": lambda obj: 0,
        })
        _load(RUNTIME / "freestanding_gc_scalar_sweep.py", self.ns,
              {"pcc_gc_scalar_sweep_owns"})
        _load(RUNTIME / "py_gc_backend.py", self.ns, {
            "pcc_gc_free_object_memory", "pcc_gc_pointer_unregister",
            "_gc_pointer_unregister_unowned_locked",
        })
        self.terminal = self.ns["pcc_gc_free_object_memory"]
        self.unregister = self.ns["pcc_gc_pointer_unregister"]

    def lock(self):
        assert self.graph == 0, "unexpected nested ownership transaction"
        self.graph = 1
        self.events.append(("lock",))

    def unlock(self):
        assert self.graph == 1
        self.events.append(("unlock",))
        self.graph = 0
        if self.unlock_hook is not None:
            hook, self.unlock_hook = self.unlock_hook, None
            hook()

    def find(self, obj):
        assert self.graph == 1
        self.events.append(("find", obj))
        return self.nodes.get(obj, 0)

    def retire(self, obj):
        self.events.append(("retire", obj, self.graph))
        if obj in self.retirement_failures:
            return -1
        if obj not in self.granules:
            return 0
        self.granules[obj] = "free"
        return 1

    def retire_validated_live(self, obj):
        if obj in self.retirement_failures or self.granules.get(obj) != "live":
            return 0
        self.events.append(("retire", obj, self.graph))
        self.granules[obj] = "free"
        return 1

    def remove_exact(self, obj):
        assert self.graph == 1
        self.events.append(("exact", obj))
        self.exact.discard(obj)
        return 0

    def note(self, obj):
        assert self.graph == 0
        self.events.append(("note", obj))
        if self.note_hook is not None:
            self.note_hook(obj)
            return
        self.nodes.pop(obj, None)
        if self.granules.get(obj) != "live":
            self.exact.add(obj)
        self.ns["global_store_ptr"]("pcc_gc_last_noted_free", obj)

    def object(self, origin="live", noted=True, flags=TERMINAL, tag=5):
        obj = self.alloc(flags=flags, tag=tag)
        if origin != "raw":
            self.granules[obj] = origin
        if origin != "live":
            self.exact.add(obj)
        if noted:
            self.ns["global_store_ptr"]("pcc_gc_last_noted_free", obj)
        return obj

    def owner(self, obj, freeing=0, lease=0):
        node = self.next_address
        self.next_address += 128
        self.memory[node] = {32: freeing, 80: lease}
        self.nodes[obj] = node
        return node

    def free(self, obj):
        assert self.graph == 0, "physical release under graph lock"
        super().free(obj)

    def kinds(self):
        return [event[0] for event in self.events]


@pytest.mark.parametrize("backend", (1, 2))
@pytest.mark.parametrize("state", range(5))
@pytest.mark.parametrize("origin", ("live", "reserved", "free", "raw"))
def test_already_noted_terminal_keeps_one_transaction_through_retirement(backend, state, origin):
    rt = TerminalRuntime(backend, state)
    obj = rt.object(origin)

    def on_unlock():
        assert ("retire", obj, 1) in rt.events
        assert obj not in rt.exact and rt.granules.get(obj) != "live"
        assert obj not in rt.freed

    rt.unlock_hook = on_unlock
    rt.terminal(obj)
    assert rt.kinds() == (["lock", "find", "retire"]
                         + ([] if origin == "live" else ["exact"])
                         + ["unlock", "free"])
    assert rt.graph == 0 and obj in rt.freed
    # A duplicate terminal call must stop before reading the reclaimed header.
    before = list(rt.events)
    rt.terminal(obj)
    assert rt.events == before


@pytest.mark.parametrize("backend", (1, 2))
@pytest.mark.parametrize("state", (0, 1, 2, 3, 4))
@pytest.mark.parametrize("tag", (3, 15), ids=("scalar", "cleared-gen"))
def test_negative_owner_wins_even_with_already_noted_tls(backend, state, tag):
    rt = TerminalRuntime(backend, state)
    obj = rt.object(tag=tag)
    node = rt.owner(obj, freeing=-37)
    before = dict(rt.memory[obj]), dict(rt.memory[node])
    rt.terminal(obj)
    assert rt.kinds() == ["lock", "find", "unlock"]
    assert (rt.memory[obj], rt.memory[node]) == before
    assert rt.nodes[obj] == node and rt.granules[obj] == "live" and not rt.freed
    if state:
        assert rt.unregister(obj) == -1  # Public callers keep the owner guard.
        assert rt.kinds() == ["lock", "find", "unlock"] * 2


@pytest.mark.parametrize("backend", (1, 2))
def test_positive_lease_blocks_already_noted_terminal_retirement(backend):
    rt = TerminalRuntime(backend)
    obj = rt.object()
    node = rt.owner(obj, lease=1)
    rt.terminal(obj)
    assert rt.kinds() == ["lock", "find", "unlock"]
    assert rt.nodes[obj] == node and rt.granules[obj] == "live" and not rt.freed


@pytest.mark.parametrize("backend", (1, 2))
@pytest.mark.parametrize("noted", (False, True))
def test_failed_granule_retirement_preserves_exact_provenance_and_storage(backend, noted):
    rt = TerminalRuntime(backend)
    obj = rt.object("reserved", noted)
    rt.retirement_failures.add(obj)
    rt.terminal(obj)
    assert rt.graph == 0 and not rt.freed and obj in rt.exact
    assert rt.granules[obj] == "reserved" and "exact" not in rt.kinds()
    assert rt.kinds().count("retire") == 1 and rt.kinds().count("note") == int(not noted)
    assert rt.kinds().count("find") == (1 if noted else 2)


@pytest.mark.parametrize("backend", range(5))
@pytest.mark.parametrize("noted", (False, True))
@pytest.mark.parametrize("origin", ("live", "reserved", "free", "raw"))
def test_only_nonmoving_already_noted_path_fuses_transactions(backend, noted, origin):
    rt = TerminalRuntime(backend)
    obj = rt.object(origin, noted=noted)
    rt.terminal(obj)
    # The nonmoving count-free collectors fuse; GC3 joined them when its
    # nursery (and the minor-block transaction it added) was retired.
    fused = backend in (1, 2, 3) and noted
    transactions = 1 if fused else 2
    transactions += int(not fused and origin != "live")
    assert rt.kinds().count("find") == (1 if fused else 2)
    assert rt.kinds().count("lock") == rt.kinds().count("unlock") == transactions
    assert rt.kinds().count("note") == int(not noted)
    assert ("retire", obj, int(fused)) in rt.events and obj in rt.freed
    assert obj not in rt.exact


@pytest.mark.parametrize("backend", (1, 2))
def test_new_note_path_rechecks_owner_after_unlock_instead_of_caching_unowned(backend):
    rt = TerminalRuntime(backend)
    obj = rt.object(noted=False)
    rt.unlock_hook = lambda: rt.owner(obj, freeing=-73)
    # Model a note observing the newly claimed negative owner and returning.
    rt.note_hook = lambda obj: None
    rt.terminal(obj)
    assert rt.kinds() == ["lock", "find", "unlock", "note", "lock", "find", "unlock"]
    assert rt.granules[obj] == "live" and not rt.freed


@pytest.mark.parametrize("backend", (1, 2))
def test_nested_free_overwriting_last_note_requires_original_public_path(backend):
    rt = TerminalRuntime(backend)
    outer = rt.object()
    nested = rt.object()  # The nested destructor is now the last noted object.
    rt.terminal(nested)
    rt.events.clear()
    rt.terminal(outer)
    assert rt.kinds() == ["lock", "find", "unlock", "note", "lock", "find",
                          "unlock", "retire", "free"]
    assert rt.scalar("pcc_gc_last_noted_free") == outer


@pytest.mark.parametrize("backend", (1, 2))
def test_origin_flags_are_captured_before_retirement_and_queue_runs_unlocked(backend):
    rt = TerminalRuntime(backend)
    obj = rt.object()
    original_retire = rt.ns["pcc_gc_granule_object_retire"]

    def retire(obj):
        result = original_retire(obj)
        rt.write(obj, 12, 0)
        return result

    def enqueue(obj, flags):
        assert rt.graph == 0 and flags == TERMINAL
        rt.events.append(("enqueue", obj))
        return 1

    rt.ns["pcc_gc_granule_object_retire"] = retire
    rt.ns["pcc_gc_deferred_free_try_enqueue"] = enqueue
    rt.terminal(obj)
    assert rt.kinds() == ["lock", "find", "retire", "unlock", "enqueue"]
    assert not rt.freed


@pytest.mark.parametrize("backend,flags", ((1, 0), (2, 0), (1, 65536), (2, 65536)))
def test_non_raw_allocation_origins_keep_existing_no_free_exit(backend, flags):
    rt = TerminalRuntime(backend)
    obj = rt.object(flags=flags)
    rt.terminal(obj)
    assert rt.kinds() == ["lock", "find", "retire", "unlock"]
    assert not rt.freed


class Gc0TerminalRuntime(TerminalRuntime):
    """GC0's terminal object: _gc0_terminal_free_has_no_metadata has proved
    the atomic counts, no lease, no identity list and a LIVE exact cell."""

    def __init__(self, state=0):
        super().__init__(0, state)
        self.define_tls("pcc_gc_free_watch")
        self.retire_live_result = None
        self.ns.update({
            "_gc0_terminal_free_has_no_metadata": lambda obj: 1,
            "_FREE_WATCH_OBJECT": 8,
            "_FREE_WATCH_NOTED": 16,
            "pcc_allocator_object_retire_live": self.retire_live,
        })

    def retire_live(self, obj):
        self.events.append(("retire_live", obj, self.graph))
        if self.retire_live_result is not None:
            return self.retire_live_result
        if self.granules.get(obj) != "live":
            return 0
        self.granules[obj] = "free"
        return 1

    def watch(self, obj):
        record = self.next_address
        self.next_address += 64
        self.memory[record] = {0: 0, 8: obj, 16: 0}
        self.ns["global_store_ptr"]("pcc_gc_free_watch", record)
        return record


def test_gc0_terminal_free_retires_its_cell_without_another_probe():
    """The predicate's proof stands in for the note and unregister probes:
    one lifecycle CAS outside the graph lock, then the physical free."""
    rt = Gc0TerminalRuntime()
    obj = rt.object(noted=False)
    rt.terminal(obj)
    assert rt.kinds() == ["retire_live", "free"]
    assert ("retire_live", obj, 0) in rt.events
    assert rt.granules[obj] == "free" and obj in rt.freed
    assert rt.scalar("pcc_gc_last_noted_free") == obj


def test_gc0_terminal_free_reports_its_death_to_a_watch():
    rt = Gc0TerminalRuntime()
    obj = rt.object(noted=False)
    record = rt.watch(obj)
    other = rt.object(noted=False)
    rt.terminal(other)
    assert rt.read(record, 16) == 0  # Another object's death is not this one.
    rt.terminal(obj)
    assert rt.read(record, 16) == 1 and obj in rt.freed


def test_gc0_terminal_free_after_its_note_keeps_the_watch_untouched():
    rt = Gc0TerminalRuntime()
    obj = rt.object(noted=True)
    record = rt.watch(obj)
    rt.terminal(obj)
    # The note that set last_noted_free already answered the watch.
    assert rt.read(record, 16) == 0
    assert rt.kinds() == ["retire_live", "free"] and obj in rt.freed


def test_gc0_terminal_free_falls_back_when_the_cell_is_no_longer_live():
    rt = Gc0TerminalRuntime()
    obj = rt.object("reserved", noted=False)
    rt.terminal(obj)
    # The ordinary unregister decides: it retires the RESERVED cell and
    # drops its exact key under the graph lock.
    assert rt.kinds()[:2] == ["retire_live", "retire"]
    assert "exact" in rt.kinds() and obj in rt.freed and obj not in rt.exact


def test_gc0_terminal_free_leaves_a_failed_retirement_to_the_unregister():
    rt = Gc0TerminalRuntime()
    obj = rt.object(noted=False)
    rt.retire_live_result = 0
    rt.retirement_failures.add(obj)
    rt.terminal(obj)
    assert rt.kinds() == ["retire_live", "retire"]
    assert not rt.freed and rt.granules[obj] == "live"


@pytest.mark.parametrize("state", (1, 2, 3, 4))
def test_gc0_terminal_free_keeps_the_scalar_sweep_owner_check(state):
    rt = Gc0TerminalRuntime(state)
    obj = rt.object(noted=False)
    owned = {}
    rt.ns["pcc_gc_scalar_sweep_owns"] = lambda o: owned.get(o, 0)
    owned[obj] = 1
    rt.terminal(obj)
    assert "retire_live" not in rt.kinds() and not rt.freed
    owned[obj] = 0
    rt.terminal(obj)
    assert rt.kinds() == ["retire_live", "free"] and obj in rt.freed
