"""Guarded release identity reuse, with actual resolver and published lookup.

Contract: docs/project-intent.md, shared GC roots and ordinary object lifetime.
The deterministic memory/lock model is not native concurrency qualification.
"""
from __future__ import annotations

import ast

import pytest

from tests.python.test_foreign_address_leases import Model, RUNTIME, _functions


CENTRAL = "pcc_gc_foreign_lease_active"
PUBLISHED = "pcc_py_gc_object_published"
FORWARDED = 2048
DYING = 524288
MASK64 = (1 << 64) - 1

# Exact release preimage at 376331386213e548e63a2d2298cd31d27cfea3fb.
# Keep this private oracle independent of the production guard under test.
_REFERENCE_RELEASE = r'''
def pcc_gc_foreign_lease_release(slot, acquired: i64) -> i64:
    # A no-op token must never decrement a counted node registered later.
    if acquired == 0:
        return 0
    if ptr_is_null(slot) != 0 or (acquired != 1 and acquired != 2):
        return -1
    # A mode-only token has no object-index entry. Its owning slot preserves
    # the leased object's stable address; root moves and cross-thread release
    # keep those pointer bits. No header or current-thread state is inspected.
    if acquired == 2:
        value = load_ptr(slot, 0)
        if ptr_is_null(value) != 0 or is_tagged_int(value) != 0:
            return -1
        return _mode_lease_release_shard(_mode_lease_shard_index(value))
    pcc_py_gc_minor_graph_lock()
    status: i64 = -3
    active: i64 = atomic_load_i64(global_addr("pcc_gc_foreign_lease_active"), 0, "acquire")
    if active > 0:
        obj = pcc_gc_resolve_root_slot_unlocked(slot, 0)
        node = pcc_gc_object_index_find(obj)
        if ptr_is_null(node) == 0 and load_i64(node, 32) == 0:
            count: i64 = load_i64(node, 80)
            if count > 0:
                counter = global_addr("pcc_gc_foreign_lease_active")
                while active > 0:
                    observed: i64 = atomic_cas_i64(
                        counter, 0, active, active - 1, "acq_rel", "acquire"
                    )
                    if observed == active:
                        store_i64(node, 80, count - 1)
                        status = 0
                        break
                    active = observed
    pcc_py_gc_minor_graph_unlock()
    return status
'''


class ReleaseModel(Model):
    """Model primitives only; resolver, known predicate and lookup are real."""

    def __init__(self, backend=3, *, reference=False, initialized=True,
                 probe=0, logging=0):
        self.audit = []
        self.unreadable_headers = set()
        self.forwarding = {}
        self.on_incref = None
        self.on_decref = None
        super().__init__(backend, initialized=initialized)
        self.store("pcc_gc_refcount_provenance_probe", 0, probe)
        self.store("pcc_diagnostics_runtime_log_fast_state", 0, logging)
        self.pending = object()
        self.ns.update(
            pending_exception=self.pending,
            ptr_diff=lambda left, right: left - right,
            int_to_ptr=lambda value: value,
            logical_shift_right_i64=lambda value, shift: (value & MASK64) >> shift,
            wrapping_mul_i64=lambda left, right: ((left * right + (1 << 63)) & MASK64) - (1 << 63),
            atomic_load_i64=self.atomic_load,
            pcc_gc_forwarding_index_find=self.find_forwarding,
            py_incref=lambda value: self.refcount("incref", value),
            py_decref=lambda value: self.refcount("decref", value),
        )
        _functions(RUNTIME / "freestanding_gc_index_table.py", {
            "pcc_gc_index_py_hash_ptr", "pcc_gc_index_py_find_slot",
            "pcc_gc_index_py_find_published", "pcc_gc_object_index_find",
        }, self.ns)
        self.raw_find = self.ns["pcc_gc_object_index_find"]
        self.ns["pcc_gc_object_index_find"] = self.find
        _functions(RUNTIME / "freestanding_gc_root_operations.py", {
            "pcc_gc_object_is_known_no_lock", "_resolve_root_slot_mode",
            "pcc_gc_resolve_root_slot_unlocked",
        }, self.ns)
        self.raw_resolve = self.ns["pcc_gc_resolve_root_slot_unlocked"]
        self.ns["pcc_gc_resolve_root_slot_unlocked"] = self.resolve_real
        if reference:
            tree = ast.parse(_REFERENCE_RELEASE)
            tree.body[0].name = "_original_release"
            exec(compile(tree, "<original foreign lease release>", "exec"), self.ns)
            self.ns["pcc_gc_foreign_lease_release"] = self.ns["_original_release"]
        self.publish()

    def load(self, base, offset=0):
        assert not (offset == 12 and base in self.unreadable_headers), (
            "dereferenced unknown/retired object header", base)
        self.audit.append(("read", base, offset, self.depth))
        return super().load(base, offset)

    def store(self, base, offset, value):
        self.audit.append(("write", base, offset, value))
        super().store(base, offset, value)

    def atomic_load(self, base, offset, order):
        self.audit.append(("atomic_load", base, offset, order))
        return self.load(base, offset)

    def compare_exchange(self, base, offset, expected, desired, success, failure):
        self.audit.append(("cas_order", base, expected, desired, success, failure))
        return super().compare_exchange(base, offset, expected, desired, success, failure)

    def find(self, value):
        self.audit.append(("find", value, self.depth))
        return self.raw_find(value)

    def find_forwarding(self, value):
        self.audit.append(("forward", value, self.depth))
        return self.forwarding.get(value, 0)

    def resolve_real(self, slot, offset):
        assert self.depth > 0
        self.audit.append(("resolve", slot, offset, self.depth))
        return self.raw_resolve(slot, offset)

    def refcount(self, kind, value):
        assert self.depth > 0
        self.audit.append((kind, value))
        hook = self.on_incref if kind == "incref" else self.on_decref
        if hook is not None:
            hook(value)

    def publish(self):
        # Build a tiny valid exact table, retaining old nodes in model memory.
        slots, record, capacity = 80000, 81000, 16
        for index in range(capacity):
            self.store(slots, index * 16, 0)
            self.store(slots, index * 16 + 8, 0)
        for value, node in self.nodes.items():
            index = self.ns["pcc_gc_index_py_hash_ptr"](value) & (capacity - 1)
            while self.load(slots, index * 16):
                index = (index + 1) & (capacity - 1)
            self.store(slots, index * 16, value)
            self.store(slots, index * 16 + 8, node)
        self.store(record, 0, slots)
        self.store(record, 8, capacity)
        self.store(PUBLISHED, 0, record)

    def object(self, obj=1000, **kwargs):
        slot = super().object(obj, **kwargs)
        self.publish()
        return slot

    def edge(self, source, target):
        record = 60000 + source
        self.forwarding[source] = record
        self.store(record, 8, target)


def _effects(model):
    return [event for event in model.audit if event[0] in
            {"write", "incref", "decref", "cas_order"}]


def _paired_release(setup, *, backend=3, initialized=True, probe=0, logging=0):
    results = []
    for reference in (True, False):
        model = ReleaseModel(backend, reference=reference, initialized=initialized,
                             probe=probe, logging=logging)
        slot, token = setup(model)
        model.audit.clear()
        model.events.clear()
        status = model.release(slot, token)
        assert model.ns["pending_exception"] is model.pending
        assert model.depth == 0
        results.append((model, status))
    (original, old_status), (current, status) = results
    assert status == old_status
    assert current.memory == original.memory
    assert current.nodes == original.nodes
    assert current.forwarding == original.forwarding
    assert current.events == original.events
    assert _effects(current) == _effects(original)
    return original, current, status


def _calls(model, kind):
    return [event for event in model.audit if event[0] == kind]


@pytest.mark.parametrize("backend", (3, 4))
@pytest.mark.parametrize("borrowed", (False, True))
@pytest.mark.parametrize("flags", (0, 1, 64, 128 | 4096, 65536, 262144))
@pytest.mark.parametrize("initialized,probe,logging", (
    (True, 0, 0), (True, 1, 0), (True, 0, 1), (False, 0, 0),
))
def test_exact_live_release_reuses_one_lookup(backend, borrowed, flags,
                                             initialized, probe, logging):
    def setup(model):
        slot = model.object(flags=flags)
        model.store(1000, 0, 7)  # A live external owner for the borrowed case.
        acquire = model.ns["pcc_gc_borrowed_lease_acquire"] if borrowed else model.acquire
        assert acquire(slot) == 1
        # Acquisition initializes configuration; exercise a defensive release
        # before initialization separately without changing object ownership.
        model.store("pcc_gc_config_initialized", 0, int(initialized))
        return slot, 1
    old, current, status = _paired_release(setup, backend=backend,
        initialized=initialized, probe=probe, logging=logging)
    assert status == 0
    assert len(_calls(old, "find")) == 2 and len(_calls(old, "resolve")) == 1
    assert _calls(current, "find") == [("find", 1000, 1)]
    assert _calls(current, "resolve") == _calls(current, "forward") == []
    assert _calls(current, "incref") == _calls(current, "decref") == []
    assert len([event for event in _calls(current, "atomic_load")
                if event[1] == PUBLISHED and event[3] == "acquire"]) == 1
    assert current.load(1000, 12) == flags and current.load(21000) == 1000
    assert current.load(1000) == 7 and current.count() == current.active() == 0
    writes = _calls(current, "write")
    assert writes == [("write", CENTRAL, 0, 0), ("write", 11000, 80, 0)]
    assert _calls(current, "cas_order") == [("cas_order", CENTRAL, 1, 0, "acq_rel", "acquire")]
    assert current.events == [("lock", 1), ("cas", CENTRAL, 1, 0, 1), ("unlock", 0)]


@pytest.mark.parametrize("backend", (3, 4))
def test_release_reloads_only_after_graph_entry_and_keeps_unlock_poll(backend):
    def setup(model):
        slot = model.object()
        other = model.object(2000)
        assert model.acquire(other) == 1
        model.on_lock = lambda: model.store(slot, 0, 2000)
        model.on_unlock = lambda: model.events.append(("poll", model.load(CENTRAL)))
        return slot, 1
    _, current, status = _paired_release(setup, backend=backend)
    assert status == 0 and current.count(1000) == current.count(2000) == 0
    assert _calls(current, "find") == [("find", 2000, 1)]
    assert [(event[1], event[3]) for event in _calls(current, "read")
            if event[1] == 21000] == [(21000, 1)]
    assert current.events[-2:] == [("unlock", 0), ("poll", 0)]


@pytest.mark.parametrize("backend", (3, 4))
@pytest.mark.parametrize("source_state", ("live", "inactive", "freeing", "dying"))
def test_forwarding_fallback_heals_slot_with_original_rc_rules(backend, source_state):
    def setup(model):
        slot = model.object(flags=FORWARDED | (DYING if source_state == "dying" else 0))
        model.object(2000)
        model.store(12000, 80, 1)
        model.store(CENTRAL, 0, 1)
        if source_state == "inactive":
            del model.nodes[1000]
            model.publish()
            model.unreadable_headers.add(1000)
        elif source_state == "freeing":
            model.store(11000, 32, 1)
            model.unreadable_headers.add(1000)
        model.edge(1000, 2000)
        return slot, 1
    _, current, status = _paired_release(setup, backend=backend)
    assert status == 0 and current.load(21000) == 2000 and current.count(2000) == 0
    assert len(_calls(current, "resolve")) == 1
    assert _calls(current, "find")[-1] == ("find", 2000, 1)
    assert _calls(current, "incref") == ([] if backend == 4 else [("incref", 2000)])
    assert _calls(current, "decref") == ([] if backend == 4 else [("decref", 1000)])


@pytest.mark.parametrize("flags", (FORWARDED, FORWARDED | DYING, DYING))
@pytest.mark.parametrize("target", ("missing", 0, 1000))
def test_missing_null_or_self_forwarding_preserves_flag_cleanup(flags, target):
    def setup(model):
        slot = model.object(flags=flags)
        model.store(11000, 80, 1)
        model.store(CENTRAL, 0, 1)
        if target != "missing":
            model.edge(1000, target)
        return slot, 1
    _, current, status = _paired_release(setup)
    # DYING does not itself reject release; the unchanged tail uses node/count.
    assert status == 0 and current.count() == current.active() == 0
    assert current.load(1000, 12) == (flags if flags & DYING else flags & ~FORWARDED)
    assert current.load(21000) == 1000
    assert len(_calls(current, "resolve")) == 1
    assert not _calls(current, "incref") and not _calls(current, "decref")


@pytest.mark.parametrize("mutation", ("replace", "remove", "freeing", "empty_count", "rewrite_slot"))
@pytest.mark.parametrize("hook_kind", ("incref", "decref"))
def test_fallback_discards_node_across_refcount_callbacks(mutation, hook_kind):
    def setup(model):
        slot = model.object(flags=FORWARDED)
        model.object(2000)
        model.object(3000)
        model.store(11000, 80, 9)  # Tentative source node must never decrement.
        model.store(12000, 80, 5)
        model.store(13000, 80, 7)
        model.store(CENTRAL, 0, 1)
        model.edge(1000, 2000)
        def callback(value):
            assert value == (2000 if hook_kind == "incref" else 1000)
            if mutation == "replace":
                model.nodes[2000] = 42000
                model.store(42000, 80, 1)
                model.publish()
            elif mutation == "remove":
                del model.nodes[2000]
                model.publish()
            elif mutation == "freeing":
                model.store(12000, 32, 1)
            elif mutation == "empty_count":
                model.store(12000, 80, 0)
            else:
                model.store(slot, 0, 3000)
        if hook_kind == "incref":
            model.on_incref = callback
        else:
            model.on_decref = callback
        return slot, 1
    _, current, status = _paired_release(setup)
    assert status == (-3 if mutation in {"remove", "freeing", "empty_count"} else 0)
    assert current.load(11000, 80) == 9 and current.load(13000, 80) == 7
    assert _calls(current, "find")[-1] == ("find", 2000, 1)
    last_rc = max(i for i, event in enumerate(current.audit) if event[0] in {"incref", "decref"})
    last_find = max(i for i, event in enumerate(current.audit) if event[0] == "find")
    assert last_find > last_rc
    if mutation == "replace":
        assert current.load(12000, 80) == 5 and current.load(42000, 80) == 0
    if mutation == "rewrite_slot":
        # The resolver returns its resolved object even when a decrement
        # callback rewrites the root again; do not invent an extra reload.
        assert current.load(21000) == (3000 if hook_kind == "decref" else 2000)
        assert current.load(12000, 80) == 4


@pytest.mark.parametrize("backend", (3, 4))
def test_acquire_and_release_each_heal_only_one_forwarding_edge(backend):
    def setup(model):
        slot = model.object(flags=FORWARDED)
        model.object(2000, flags=FORWARDED)
        model.object(3000)
        model.edge(1000, 2000)
        model.edge(2000, 3000)
        assert model.acquire(slot) == 1
        assert model.load(slot) == 2000 and model.count(2000) == 1
        # A malformed two-edge lease does not authorize borrowing another
        # object's count. The unchanged resolver heals once; tail rejects.
        return slot, 1
    _, current, status = _paired_release(setup, backend=backend)
    assert status == -3 and current.load(21000) == 3000
    assert current.count(2000) == 1 and current.count(3000) == 0
    assert current.active() == 1


@pytest.mark.parametrize("kind", ("null", "tagged", "unknown", "missing_node", "publication_miss", "freeing", "zero_node"))
def test_failed_identity_guard_does_not_read_unknown_header_or_consume_guard(kind):
    def setup(model):
        slot = model.object()
        model.store(11000, 80, 1)
        model.store(CENTRAL, 0, 1)
        if kind in {"null", "tagged", "unknown"}:
            value = {"null": 0, "tagged": 7, "unknown": 4000}[kind]
            model.store(slot, 0, value)
            model.unreadable_headers.add(value)
        elif kind == "missing_node":
            del model.nodes[1000]
            model.publish()
            model.unreadable_headers.add(1000)
        elif kind == "publication_miss":
            # Authoritative/list node still exists; optional reader record
            # was not published (e.g. OOM), so exact find must still miss.
            model.store(PUBLISHED, 0, 0)
            model.unreadable_headers.add(1000)
        elif kind == "freeing":
            model.store(11000, 32, 1)
            model.unreadable_headers.add(1000)
        else:
            # A published key with a null node value is a miss, not identity.
            model.nodes[1000] = 0
            model.publish()
            model.unreadable_headers.add(1000)
        return slot, 1
    _, current, status = _paired_release(setup)
    assert status == -3 and current.active() == 1
    assert current.load(11000, 80) == 1 and not _calls(current, "cas_order")
    assert len(_calls(current, "resolve")) == 1


@pytest.mark.parametrize("central", (0, -1, -7))
def test_inactive_central_never_loads_slot_or_node(central):
    def setup(model):
        slot = model.object()
        model.store(11000, 80, 1)
        model.store(CENTRAL, 0, central)
        return slot, 1
    _, current, status = _paired_release(setup)
    assert status == -3 and not _calls(current, "find") and not _calls(current, "resolve")
    assert not any(event[1] in {21000, 1000, 11000} for event in _calls(current, "read"))
    assert not _calls(current, "write")


@pytest.mark.parametrize("count", (0, -1, -7))
def test_bad_node_count_never_consumes_another_owners_central_guard(count):
    def setup(model):
        slot = model.object()
        other = model.object(2000)
        assert model.acquire(other) == 1
        model.store(11000, 80, count)
        return slot, 1
    _, current, status = _paired_release(setup)
    assert status == -3 and current.active() == 1 and current.count(2000) == 1
    assert current.count(1000) == count and not _calls(current, "write")


@pytest.mark.parametrize("token,slot_value,expected", (
    (0, 0, 0), (0, 21000, 0), (1, 0, -1), (2, 0, -1),
    (-1, 21000, -1), (3, 21000, -1), (2, 21000, -3),
))
def test_token_dispatch_keeps_noop_null_and_invalid_paths(token, slot_value, expected):
    def setup(model):
        model.object()
        model.store(11000, 80, 2)
        model.store(CENTRAL, 0, 2)
        return slot_value, token
    _, current, status = _paired_release(setup)
    assert status == expected
    assert not _calls(current, "find") and not _calls(current, "resolve")
    assert not any(event[0] == "lock" for event in current.events)
    assert current.count() == 2 and current.load(CENTRAL) == 2


@pytest.mark.parametrize("value", (0, 7, 4000))
def test_mode_token_uses_only_address_shard_and_never_header(value):
    def setup(model):
        slot = model.object()
        model.store(slot, 0, value)
        model.unreadable_headers.add(value)
        return slot, 2
    _, current, status = _paired_release(setup)
    assert status == (-1 if value in {0, 7} else -3)
    assert not _calls(current, "find") and not _calls(current, "resolve")


@pytest.mark.parametrize("observed", (0, -1, 2))
def test_central_cas_retry_keeps_acquire_release_order_and_decrement_sequence(observed):
    def setup(model):
        slot = model.object()
        assert model.acquire(slot) == 1
        model.on_compare_exchange = lambda *args: model.store(CENTRAL, 0, observed)
        return slot, 1
    _, current, status = _paired_release(setup)
    assert status == (0 if observed == 2 else -3)
    assert current.count() == (0 if observed == 2 else 1)
    assert all(event[-2:] == ("acq_rel", "acquire") for event in _calls(current, "cas_order"))
    if observed == 2:
        assert [(event[2], event[3]) for event in _calls(current, "cas_order")] == [(1, 0), (2, 1)]
        assert _calls(current, "write")[-2:] == [("write", CENTRAL, 0, 1), ("write", 11000, 80, 0)]


@pytest.mark.parametrize("backend", (3, 4))
def test_overlapping_release_legacy_pin_and_double_release(backend):
    def setup(model):
        slot = model.object(flags=64)
        assert [model.acquire(slot), model.acquire(slot)] == [1, 1]
        assert model.release(slot, 1) == 0
        assert model.count() == 1 and model.load(1000, 12) == 64
        return slot, 1
    old, current, status = _paired_release(setup, backend=backend)
    assert status == 0 and current.count() == 0 and current.load(1000, 12) == 64
    for model in (old, current):
        other = model.object(2000)
        assert model.acquire(other) == 1
        before = dict(model.memory)
        assert model.release(21000, 1) == -3
        assert model.memory == before and model.count(2000) == 1


def test_release_guard_ast_preserves_common_tail_and_short_circuit_dominance():
    source = (RUNTIME / "freestanding_gc_root_operations.py").read_text()
    actual = next(node for node in ast.parse(source).body
                  if isinstance(node, ast.FunctionDef)
                  and node.name == "pcc_gc_foreign_lease_release")
    reference = ast.parse(_REFERENCE_RELEASE).body[0]
    active = next(node for node in actual.body if isinstance(node, ast.If)
                  and ast.unparse(node.test) == "active > 0")
    old_active = next(node for node in reference.body if isinstance(node, ast.If)
                      and ast.unparse(node.test) == "active > 0")
    assert ast.unparse(active.body[0]) == "obj = load_ptr(slot, 0)"
    assert ast.unparse(active.body[1]) == "node = pcc_gc_object_index_find(obj)"
    guard = active.body[2]
    expected = ast.parse('''if (ptr_is_null(node) != 0
        or load_i64(node, 32) != 0
        or (load_i32(obj, PYOBJECTHEADER_FLAGS_OFFSET) & (2048 | 524288)) != 0):
    obj = pcc_gc_resolve_root_slot_unlocked(slot, 0)
    node = pcc_gc_object_index_find(obj)
''').body[0]
    assert ast.dump(guard) == ast.dump(expected)
    # Removing exactly the guarded replacement must reconstruct the entire
    # preimage, including token paths, graph entry, CAS loop and unlock poll.
    active.body = old_active.body[:2] + active.body[3:]
    actual.decorator_list = []
    assert ast.dump(actual) == ast.dump(reference)


@pytest.mark.parametrize("backend", range(5))
def test_mode_token_keeps_shard_balance_after_node_registration(backend):
    def setup(model):
        slot = model.object(node=False, flags=262144)
        assert model.acquire(slot) == 2
        model.nodes[1000] = 11000
        model.store(11000, 80, 5)
        model.publish()
        return slot, 2
    _, current, status = _paired_release(setup, backend=backend, probe=1)
    assert status == 0 and current.active() == 0 and current.count() == 5
    assert not _calls(current, "find") and not _calls(current, "resolve")
    assert not any(event[0] == "lock" for event in current.events)


@pytest.mark.parametrize("backend", (0, 1, 2))
def test_configured_nonmoving_acquire_remains_noop(backend):
    def setup(model):
        slot = model.object()
        assert model.acquire(slot) == 0
        model.store(11000, 80, 5)
        model.store(CENTRAL, 0, 5)
        return slot, 0
    _, current, status = _paired_release(setup, backend=backend)
    assert status == 0 and current.count() == 5 and current.active() == 5
    assert not _calls(current, "find") and not _calls(current, "resolve")
    assert not _calls(current, "write")


def test_duplicate_list_metadata_keeps_exact_published_node_authoritative():
    def setup(model):
        slot = model.object()
        model.store(11000, 80, 3)
        # A duplicate object-list record does not change the existing exact
        # index semantics: only the indexed node is inspected/decremented.
        model.nodes[1000] = 42000
        model.store(42000, 80, 1)
        model.store(11000, 0, 42000)
        model.store(CENTRAL, 0, 1)
        model.publish()
        return slot, 1
    _, current, status = _paired_release(setup)
    assert status == 0 and current.load(11000, 80) == 3
    assert current.load(42000, 80) == 0 and current.active() == 0
