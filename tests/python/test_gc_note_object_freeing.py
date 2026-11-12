"""Execute the production note body with deterministic metadata and lock shims.

Specification: docs/project-intent.md, five-GC lifetime equality. These are
host contract tests; the scalar-sweep native fixture covers the real runtime.
"""
from __future__ import annotations

import pytest

from tests.python.test_gc_free_watch import WatchRuntime


class NoteRuntime(WatchRuntime):
    def __init__(self, backend, state=0, freeing=0, lease=0, minor=0):
        super().__init__()
        self.globals["pcc_gc_backend_selected"][0] = backend
        self.globals["pcc_gc_scalar_sweep_state"] = {0: state}
        self.obj = self.allocate(64)
        self.memory[self.obj] = {12: 0}
        self.node = self.allocate(88)
        self.memory[self.node] = {
            0: self.obj, 8: 64, 16: 0, 24: minor, 32: freeing,
            40: 0, 48: 0, 56: 0, 64: 0, 72: 0, 80: lease,
        }
        self.index = {self.obj: self.node}
        self.events = []
        self.lookups = 0
        self.live_bytes = 64
        self.gray_work = 0
        self.granule = 1
        self.provenance_result = 1
        self.tracked = 1
        self.tls["pcc_gc_last_noted_free"] = -123
        self.tls["pcc_gc_last_alloc"] = self.obj
        self.ns.update({
            "pcc_gc_object_index_find": self.find,
            "pcc_gc_object_index_remove": self.remove,
            "pcc_gc_granule_is_object_start": lambda obj: self.granule,
            "pcc_gc_managed_pointer_index_insert": self.insert_provenance,
            "_identity_remove": lambda obj: self.event("identity", obj),
            "_gc_tracks_objects": lambda: self.tracked,
            "_object_node_freeing": lambda node: self.load(node, 32),
            "_set_object_node_freeing": lambda node, value: self.store(node, 32, value),
            "_object_node_size": lambda node: self.load(node, 8),
            "_object_node_minor_block": lambda node: self.load(node, 24),
            "_object_node_zpage": lambda node: self.load(node, 48),
            "_live_bytes_subtract": self.debit,
            "_gc_gray_count_decrement_acq_rel": self.retire_gray,
            "atomic_rmw_i32": self.atomic_and,
            "_unlink_object_node": lambda node: self.event("unlink", node),
            "_object_node_release": self.release,
            "_forwarding_detach_into_finish": lambda obj, finish: self.event("detach", obj),
            "_forwarding_remove_target": lambda obj, finish: self.event("target", obj),
            "_backend4_finish_remap_retirement": self.finish,
            "_relocation_set_remove": lambda obj: self.event("relocation", obj),
            "_backend4_store_buffer_remove": lambda obj: self.event("store-buffer", obj),
            "_backend4_remembered_set_remove": lambda obj: self.event("remembered", obj),
            "_backend4_zpage_remove": lambda obj: self.event("zpage", obj),
        })
        self.record = self.begin(self.obj)

    def lock(self, delta):
        super().lock(delta)
        self.events.append(("lock" if delta == 1 else "unlock", self.locks))

    def event(self, kind, value):
        assert self.locks == 1
        self.events.append((kind, value))

    def find(self, obj):
        self.event("find", obj)
        self.lookups += 1
        return self.index.get(obj, 0)

    def remove(self, obj):
        self.event("remove", obj)
        return self.index.pop(obj, 0)

    def insert_provenance(self, obj):
        self.event("provenance", obj)
        return self.provenance_result

    def debit(self, size):
        self.event("debit", size)
        self.live_bytes -= size

    def retire_gray(self):
        self.event("gray", -1)
        self.gray_work -= 1

    def atomic_and(self, operation, obj, offset, value, order):
        assert (operation, offset, order) == ("and", 12, "acq_rel")
        self.event("atomic-and", value)
        before = self.load(obj, offset)
        self.store(obj, offset, before & value)
        return before

    def release(self, node):
        self.event("release", node)
        assert node not in self.index.values()
        self.reused.add(node)  # A later read through the cached node must fail.

    def finish(self, frame):
        assert self.locks == 0
        self.events.append(("finish", frame))

    def kinds(self):
        return [kind for kind, _ in self.events]

    def assert_one_transaction(self):
        assert self.lookups == 1
        assert self.kinds().count("lock") == self.kinds().count("unlock") == 1
        assert self.locks == 0


@pytest.mark.parametrize("backend", (1, 2))
@pytest.mark.parametrize("state", range(5), ids=("empty", "building", "ready", "draining", "quarantined"))
def test_ordinary_note_reuses_one_locked_lookup_for_every_mailbox_state(backend, state):
    rt = NoteRuntime(backend, state)
    rt.note(rt.obj)
    rt.assert_one_transaction()
    assert rt.kinds() == ["lock", "find", "identity", "debit", "remove", "unlink", "release", "unlock"]
    assert rt.index == {} and rt.live_bytes == 0
    assert rt.end(rt.record) == 1
    assert rt.tls["pcc_gc_last_noted_free"] == rt.obj


@pytest.mark.parametrize("backend", (1, 2))
@pytest.mark.parametrize("state", range(5), ids=("empty", "building", "ready", "draining", "quarantined"))
def test_negative_owner_is_authoritative_before_watch_even_with_empty_mailbox(backend, state):
    rt = NoteRuntime(backend, state, freeing=-37, lease=1)
    before = dict(rt.memory[rt.node])
    rt.note(rt.obj)
    rt.assert_one_transaction()
    assert rt.kinds() == ["lock", "find", "unlock"]
    assert rt.memory[rt.node] == before and rt.index == {rt.obj: rt.node}
    assert rt.live_bytes == 64 and rt.end(rt.record) == 0
    assert rt.tls["pcc_gc_last_noted_free"] == -123


@pytest.mark.parametrize("backend", (1, 2))
def test_positive_lease_is_rejected_after_watch_without_retiring_metadata(backend):
    rt = NoteRuntime(backend, state=2, lease=1)
    rt.note(rt.obj)
    rt.assert_one_transaction()
    assert rt.kinds() == ["lock", "find", "unlock"]
    assert rt.index == {rt.obj: rt.node} and rt.live_bytes == 64
    assert rt.memory[rt.node][32] == 0 and rt.end(rt.record) == 1
    assert rt.tls["pcc_gc_last_noted_free"] == -123


@pytest.mark.parametrize("backend", (1, 2))
def test_gray_retirement_preserves_other_header_flags(backend):
    rt = NoteRuntime(backend, state=1)
    rt.memory[rt.obj][12] = 16 | 64 | 1024
    rt.gray_work = 1
    rt.note(rt.obj)
    rt.assert_one_transaction()
    assert rt.memory[rt.obj][12] == 64 | 1024
    assert rt.gray_work == 0 and rt.live_bytes == 0


@pytest.mark.parametrize("backend", (1, 2))
@pytest.mark.parametrize("outcome", ("missing", "provenance-failure", "untracked"))
def test_early_exits_balance_lock_and_preserve_last_note_order(backend, outcome):
    rt = NoteRuntime(backend, state=4)
    if outcome == "missing":
        rt.index.clear()
    elif outcome == "provenance-failure":
        rt.granule, rt.provenance_result = -1, -1
    else:
        rt.tracked = 0
    rt.note(rt.obj)
    rt.assert_one_transaction()
    assert rt.live_bytes == 64 and rt.end(rt.record) == 1
    assert "remove" not in rt.kinds() and "finish" not in rt.kinds()
    if outcome == "provenance-failure":
        assert "identity" not in rt.kinds()
        assert rt.tls["pcc_gc_last_noted_free"] == -123
    else:
        assert rt.tls["pcc_gc_last_noted_free"] == rt.obj
    if outcome == "missing":
        assert rt.tls["pcc_gc_last_alloc"] == 0


def test_gc0_keeps_its_original_untracked_transaction():
    rt = NoteRuntime(0)
    rt.tracked = 0
    rt.note(rt.obj)
    rt.assert_one_transaction()
    assert rt.index == {rt.obj: rt.node} and rt.live_bytes == 64
    assert rt.end(rt.record) == 1 and "finish" not in rt.kinds()


@pytest.mark.parametrize("backend", (3, 4))
def test_moving_backend_refetches_node_after_forwarding_work(backend):
    rt = NoteRuntime(backend)
    replacement = rt.allocate(88)
    rt.memory[replacement] = dict(rt.memory[rt.node])

    def replace_node(obj, frame):
        rt.event("replace-node", obj)
        rt.index[obj] = replacement
        rt.reused.add(rt.node)

    rt.ns["_forwarding_detach_into_finish"] = replace_node
    rt.note(rt.obj)
    assert rt.lookups == (2 if backend == 3 else 3)
    assert rt.kinds().count("lock") == rt.kinds().count("unlock") == 1
    assert ("release", replacement) in rt.events
    assert rt.index == {} and rt.live_bytes == 0
    assert rt.end(rt.record) == 1 and rt.kinds()[-1] == "finish"
