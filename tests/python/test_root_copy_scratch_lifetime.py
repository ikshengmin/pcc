"""Production root-copy body ordering, not a native fixed-frame proof.

HandoffModel supplies deterministic graph/lease/store-plan doubles; the shared
loader supplies the real public, locked and nonmoving-publisher bodies. Allocation events are host calls, not native stack reservations. The
whole-module optimized-IR regression in test_root_copy_lease_destination.py
separately checks frame separation. Neither test is a native stale-stack or
concurrent stop-the-world witness, and neither replaces that acceptance gate.
"""

import pytest

from test_foreign_address_leases import RUNTIME
from test_runtime_entry_handoff import HandoffModel
from tests.python.runtime_body_model import load_functions


ENTRIES = ("owned", "borrowed", "local-owned", "local-borrowed")
LOCKED_HELPERS = (
    "_gc_root_copy_lease_locked",
    "_gc_root_copy_borrowed_lease_locked",
    "_gc_root_copy_local_locked",
)


class ScratchLifetimeModel(HandoffModel):
    def __init__(self, backend=3):
        self.allocations = []
        self.initialized = set()
        self.helper_stack = []
        self.no_park_depth = 0
        super().__init__(backend)
        self.ns.update(
            _gc_backend_fast=self.backend,
            pcc_thread_no_park_enter=self.no_park_enter,
            pcc_thread_no_park_exit=self.no_park_exit,
            pcc_gc_note_store=self.note_store,
        )
        # Explicit names always load the production body, even if the base
        # model already loaded it transitively. No copied AST/body executor.
        load_functions(RUNTIME / "py_obj.py", {
            "pcc_gc_root_copy_lease", "pcc_gc_root_copy_borrowed_lease",
            "pcc_gc_root_copy_local", "pcc_gc_root_copy_owned_prepare_locked",
            "_store_root_nonmoving_tracing", *LOCKED_HELPERS,
        }, self.ns)
        for name in LOCKED_HELPERS:
            body = self.ns[name]

            def observed(*args, _name=name, _body=body):
                assert self.depth == 1
                self.events.append(("helper_entry", _name, self.depth))
                self.helper_stack.append(_name)
                try:
                    return _body(*args)
                finally:
                    self.helper_stack.pop()

            self.ns[name] = observed

    def backend(self):
        backend = self.load("pcc_gc_backend_selected")
        self.events.append(("backend", backend, self.depth))
        return backend

    def allocate(self, size):
        # This proves only dynamic source order. The optimized-IR test is
        # required because a branch-local alloca also occupies a fixed frame.
        assert self.depth == 1 and self.helper_stack
        address = super().allocate(size)
        self.allocations.append((address, size, self.helper_stack[-1]))
        self.events.append(("allocate", address, size, self.depth))
        return address

    def plan_init(self, plan, backend):
        assert self.depth == 1
        assert any(address == plan for address, _, _ in self.allocations)
        self.initialized.add(plan)
        self.events.append(("plan_init", plan, backend, self.depth))
        return super().plan_init(plan, backend)

    def plan_commit(self, plan, destination, value):
        assert plan in self.initialized
        self.events.append(("plan_commit", plan, destination, value))
        return super().plan_commit(plan, destination, value)

    def lock(self):
        if self.depth == 0:
            self.events.append(("graph_wait",))
        return super().lock()

    def unlock(self):
        if self.depth == 1:
            assert self.initialized == {address for address, _, _ in self.allocations}
        return super().unlock()

    def no_park_enter(self):
        self.no_park_depth += 1
        self.events.append(("no_park_enter", self.no_park_depth))

    def no_park_exit(self):
        self.no_park_depth -= 1
        assert self.no_park_depth >= 0
        self.events.append(("no_park_exit", self.no_park_depth))

    def note_store(self):
        assert self.no_park_depth == 2
        self.events.append(("note_store", self.no_park_depth))

    def invoke(self, entry, destination, source):
        if entry == "owned":
            return self.copy(destination, source)
        if entry == "borrowed":
            return self.copy_borrowed(destination, source)
        return self.ns["pcc_gc_root_copy_local"](
            destination, source, int(entry == "local-borrowed"),
        )

    def assert_plan_lifetime(self, entry):
        expected = [128, 128] if entry == "borrowed" else [128]
        assert [size for _, size, _ in self.allocations] == expected
        wait = self.events.index(("graph_wait",))
        lock = self.events.index(("lock", 1))
        unlock = self.events.index(("unlock", 0))
        entries = [index for index, event in enumerate(self.events)
                   if event[0] == "helper_entry"]
        assert len(entries) == 1 and wait < lock < entries[0] < unlock
        for index, event in enumerate(self.events):
            if event[0] in ("allocate", "plan_init"):
                assert entries[0] < index < unlock
            elif event[0] == "finish":
                assert index > unlock
        assert self.depth == 0


@pytest.mark.parametrize("backend", (3, 4))
@pytest.mark.parametrize("entry", ENTRIES)
def test_copy_plan_starts_after_wait_and_reloads_the_authoritative_source(backend, entry):
    model = ScratchLifetimeModel(backend)
    source, destination = model.object(), model.empty()
    model.object(2000)
    pending = model.pending_exception

    def while_waiting():
        assert model.depth == 0
        assert not model.allocations and not model.initialized
        assert not any(event[0] == "helper_entry" for event in model.events)
        model.store(source, 0, 2000)

    model.on_lock = while_waiting
    expected_token = 0 if entry.startswith("local-") else 1
    observed = []
    model.on_unlock = lambda: observed.append((
        model.load(destination), model.references[2000], model.count(2000),
    ))
    assert model.invoke(entry, destination, source) == expected_token
    model.assert_plan_lifetime(entry)
    assert observed == [(2000, 2, expected_token)]
    assert model.load(source) == model.load(destination) == 2000
    assert model.count(1000) == 0 and model.references[1000] == 1
    assert set(model.plan_backends.values()) == {backend}
    lock = model.events.index(("lock", 1))
    backend_reads = [(index, event) for index, event in enumerate(model.events)
                     if event[0] == "backend" and event[2] == 1]
    assert backend_reads
    assert all(index > lock and event[1] == backend for index, event in backend_reads)
    assert model.pending_exception is pending
    reload_kind = "borrowed_reload" if "borrowed" in entry else "reload"
    reload = model.events.index((reload_kind, source))
    assert model.events.index(("lock", 1)) < reload
    if "borrowed" in entry:
        assert ("reload", source) not in model.events
    assert ("retain_publish", destination, 1000) not in model.events
    assert model.active() == expected_token
    if expected_token:
        assert model.release(destination, expected_token) == 0
        assert model.active() == model.count(2000) == 0


@pytest.mark.parametrize("backend", (3, 4))
@pytest.mark.parametrize("entry", ENTRIES)
@pytest.mark.parametrize("invalid", ("destination-null", "source-null", "alias"))
def test_invalid_copy_entries_never_enter_a_plan_frame(backend, entry, invalid):
    model = ScratchLifetimeModel(backend)
    source, destination = model.object(), model.empty()
    before = dict(model.memory), dict(model.references), model.pending_exception
    if invalid == "destination-null":
        destination = 0
    elif invalid == "source-null":
        source = 0
    else:
        destination = source
    assert model.invoke(entry, destination, source) == -1
    assert (model.memory, model.references, model.pending_exception) == before
    assert not model.allocations and not model.events


@pytest.mark.parametrize("source_borrowed", (-1, 2))
def test_invalid_local_borrowed_flag_never_enters_a_plan_frame(source_borrowed):
    model = ScratchLifetimeModel()
    source, destination = model.object(), model.empty()
    assert model.ns["pcc_gc_root_copy_local"](destination, source, source_borrowed) == -1
    assert not model.events and not model.allocations


@pytest.mark.parametrize("backend", (3, 4))
@pytest.mark.parametrize("entry", ENTRIES)
def test_nonempty_destination_initializes_every_plan_before_early_unlock(backend, entry):
    model = ScratchLifetimeModel(backend)
    source, destination = model.object(), model.object(2000)
    before = dict(model.references)
    pending = model.pending_exception
    assert model.invoke(entry, destination, source) == -4
    model.assert_plan_lifetime(entry)
    assert model.references == before and model.active() == 0
    assert model.load(source) == 1000 and model.load(destination) == 2000
    assert model.pending_exception is pending
    assert not any(event[0] in ("reload", "borrowed_reload", "plan_commit")
                   for event in model.events)


@pytest.mark.parametrize("backend", (3, 4))
@pytest.mark.parametrize("entry", ENTRIES)
@pytest.mark.parametrize("value", (0, 7))
def test_null_and_tagged_copy_keep_noop_tokens_with_initialized_plans(backend, entry, value):
    model = ScratchLifetimeModel(backend)
    source, destination = model.empty(90008), model.empty()
    model.store(source, 0, value)
    pending = model.pending_exception
    assert model.invoke(entry, destination, source) == 0
    model.assert_plan_lifetime(entry)
    assert model.load(source) == model.load(destination) == value
    assert model.active() == 0 and not model.references
    assert model.pending_exception is pending


@pytest.mark.parametrize("backend", (3, 4))
@pytest.mark.parametrize("entry", ENTRIES)
def test_failed_commit_finishes_initialized_plans_without_leaking_ownership(backend, entry):
    model = ScratchLifetimeModel(backend)
    source, destination = model.object(), model.empty()
    model.fail_commit = True
    pending = model.pending_exception
    assert model.invoke(entry, destination, source) == -4
    model.assert_plan_lifetime(entry)
    assert model.load(source) == 1000 and model.load(destination) == 0
    assert model.references[1000] == 1 and model.count() == model.active() == 0
    assert model.pending_exception is pending


@pytest.mark.parametrize("backend", (3, 4))
@pytest.mark.parametrize("entry", ("owned", "borrowed"))
@pytest.mark.parametrize("failure", ("overflow", "unproven"))
def test_failed_lease_preserves_owners_and_initializes_borrowed_rollback(backend, entry, failure):
    model = ScratchLifetimeModel(backend)
    source = model.object(node=failure != "unproven", proven=failure != "unproven")
    destination = model.empty()
    if failure == "overflow":
        model.store(model.nodes[1000], 80, 2**63 - 1)
    pending = model.pending_exception
    assert model.invoke(entry, destination, source) == (-2 if failure == "overflow" else -1)
    model.assert_plan_lifetime(entry)
    assert model.load(source) == 1000 and model.load(destination) == 0
    assert model.references[1000] == 1 and model.active() == 0
    assert model.pending_exception is pending
    if failure == "overflow":
        assert model.count() == 2**63 - 1


@pytest.mark.parametrize("backend", (3, 4))
@pytest.mark.parametrize("lease_state", ("overflow", "unproven", "frozen"))
def test_owned_copy_leases_no_destination_and_has_no_rollback_plan(backend, lease_state):
    # No collector moves an object while a mutator runs, so a copy from a
    # stack source leases neither end: a node count at its limit, an unproven
    # source or a frozen lease table cannot fail it, and the locked form has
    # one plan and no rollback.
    model = ScratchLifetimeModel(backend)
    source = model.object(node=lease_state != "unproven", proven=lease_state != "unproven")
    destination = model.empty()
    model.store("pcc_gc_refcount_provenance_probe", 0, 0)
    model.ns["pcc_gc_stack_slot_root_is_conservative"] = lambda slot: int(slot == source)
    if lease_state == "overflow":
        model.store(model.nodes[1000], 80, 2**63 - 1)
    elif lease_state == "frozen":
        model.store("pcc_gc_foreign_lease_active", 0, -1)
    pending = model.pending_exception
    observed = []
    model.on_unlock = lambda: observed.append((model.load(destination), model.references[1000]))
    assert model.copy(destination, source) == 0
    assert [owner for _, _, owner in model.allocations] == [LOCKED_HELPERS[0]]
    (plan, _, _), = model.allocations
    commits = [event[1:] for event in model.events if event[0] == "plan_commit"]
    assert commits == [(plan, destination, 1000)]
    unlock = model.events.index(("unlock", 0))
    assert all(index < unlock for index, event in enumerate(model.events)
               if event[0] in ("allocate", "plan_init", "plan_commit", "helper_entry"))
    assert [event[1] for event in model.events if event[0] == "finish"] == [plan]
    assert all(index > unlock for index, event in enumerate(model.events) if event[0] == "finish")
    assert observed == [(1000, 2)] and model.load(source) == 1000
    assert model.active() == (-1 if lease_state == "frozen" else 0)
    if lease_state == "overflow":
        assert model.count() == 2**63 - 1
    assert model.pending_exception is pending and model.depth == 0


@pytest.mark.parametrize("backend", (3, 4))
@pytest.mark.parametrize("entry", ("local-owned", "local-borrowed"))
def test_terminal_local_source_fails_without_a_count_or_owner_change(backend, entry):
    model = ScratchLifetimeModel(backend)
    source, destination = model.object(flags=524288), model.empty()
    pending = model.pending_exception
    assert model.invoke(entry, destination, source) == -1
    model.assert_plan_lifetime(entry)
    assert model.load(source) == 1000 and model.load(destination) == 0
    assert model.references[1000] == 1 and model.count() == model.active() == 0
    assert model.pending_exception is pending


@pytest.mark.parametrize("entry", ENTRIES)
@pytest.mark.parametrize("value", (0, 7))
@pytest.mark.parametrize("nonempty", (False, True))
def test_gc0_immediate_fast_paths_never_enter_a_plan_frame(entry, value, nonempty):
    model = ScratchLifetimeModel(0)
    model.store("pcc_gc_refcount_fast", 0, 1)
    source, destination = model.empty(90008), model.empty()
    model.store(source, 0, value)
    model.store(destination, 0, 9 if nonempty else 0)
    pending = model.pending_exception
    assert model.invoke(entry, destination, source) == (-4 if nonempty else 0)
    assert not model.events and not model.allocations
    assert model.load(source) == value
    assert model.load(destination) == (9 if nonempty else value)
    assert model.pending_exception is pending


@pytest.mark.parametrize("backend", (1, 2))
@pytest.mark.parametrize("entry", ("local-owned", "local-borrowed"))
@pytest.mark.parametrize("case", ("managed", "null", "tagged", "terminal", "nonempty"))
def test_nonmoving_local_no_park_branch_has_no_fallback_scratch(backend, entry, case):
    model = ScratchLifetimeModel(backend)
    model.store("pcc_gc_backend_frozen", 0, 1)
    model.store("pcc_gc_backend4_no_refcounts", 0, 1)
    model.store("pcc_gc_refcount_provenance_probe", 0, 0)
    source = model.object(flags=524288 if case == "terminal" else 0)
    destination = model.empty()
    if case in ("null", "tagged"):
        model.store(source, 0, 0 if case == "null" else 7)
    elif case == "nonempty":
        model.store(destination, 0, 9)
    value = model.load(source)
    pending = model.pending_exception
    expected = -1 if case == "terminal" else (-4 if case == "nonempty" else 0)
    assert model.invoke(entry, destination, source) == expected
    assert not model.allocations and not model.initialized
    assert not any(event[0] in ("graph_wait", "lock", "helper_entry", "finish")
                   for event in model.events)
    assert model.events[0] == ("no_park_enter", 1)
    assert model.events[-1] == ("no_park_exit", 0)
    if expected == 0:
        # The real publisher enters/exits its nested no-park scope. Marking
        # is inactive here; this is not a concurrent marking/barrier model.
        assert ("no_park_enter", 2) in model.events
        assert ("note_store", 2) in model.events
        assert ("no_park_exit", 1) in model.events
    assert model.load(source) == value
    assert model.load(destination) == (value if expected == 0 else (9 if case == "nonempty" else 0))
    assert model.count() == model.active() == 0
    assert model.pending_exception is pending and model.no_park_depth == 0
