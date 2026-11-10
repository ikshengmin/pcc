"""A copied heap root must own its lease after a stack source disappears.

Exercise the production copy/acquire/release bodies through the existing
runtime_body_model loader used by HandoffModel. Stack membership and root
store plans are deterministic doubles, not a native collector simulation.
This is a direct exported-ABI regression, not a claim that an existing caller
already releases or rebinds its source before releasing the copied lease.
"""

from pathlib import Path
import re

import pytest

from test_foreign_address_leases import RUNTIME
from test_runtime_entry_handoff import HandoffModel
from tests.python.runtime_body_model import load_functions


class DestinationLeaseModel(HandoffModel):
    def __init__(self, backend=3, *, probe=0):
        super().__init__(backend)
        # HandoffModel defaults to probe=1, which disables stack token zero
        # and therefore cannot expose this production-only transfer defect.
        self.store("pcc_gc_refcount_provenance_probe", 0, probe)
        self.stack_slots = set()
        self.acquisitions = []
        self.commits = []
        self.ns["pcc_gc_stack_slot_root_is_conservative"] = (
            lambda slot: int(slot in self.stack_slots)
        )
        acquire = self.ns["pcc_gc_foreign_lease_acquire"]

        def observed_acquire(slot):
            token = acquire(slot)
            self.acquisitions.append((slot, token))
            self.events.append(("acquire", slot, token))
            return token

        self.ns["pcc_gc_foreign_lease_acquire"] = observed_acquire

    def plan_commit(self, plan, destination, value):
        self.commits.append((plan, destination, value))
        return super().plan_commit(plan, destination, value)

    def copy_entry(self, entry, destination, source):
        if entry == "ordinary":
            return self.copy(destination, source)
        plan = self.allocate(256)
        self.lock()
        token = self.ns["pcc_gc_root_copy_lease_prepare_locked"](
            destination, source, 0, plan,
        )
        assert self.depth == 1
        assert not any(event[0] == "finish" for event in self.events)
        self.unlock()
        self.ns["pcc_gc_root_copy_lease_finish"](plan)
        return token

    def clear_source(self, source, *, exits):
        # Model the source owner's normal release separately from the copy.
        # Do not decrement the destination's lease while dropping this owner.
        plan = self.allocate(128)
        self.plan_init(plan, self.load("pcc_gc_backend_selected"))
        self.lock()
        assert self.plan_commit(plan, source, 0) == 1
        self.unlock()
        self.plan_finish(plan)
        if exits:
            self.stack_slots.remove(source)
            self.roots.remove(source)
        else:
            # A tagged new value demonstrates rebinding, not just a stale
            # managed address left in the original conservative stack slot.
            self.store(source, 0, 7)


@pytest.mark.parametrize("backend", (3, 4))
@pytest.mark.parametrize("entry", ("ordinary", "split"))
@pytest.mark.parametrize("source_exits", (False, True))
def test_heap_destination_owns_lease_after_stack_source_lifetime(
    backend, entry, source_exits,
):
    model = DestinationLeaseModel(backend)
    source, destination = model.object(), model.empty()
    model.stack_slots.add(source)
    assert destination not in model.stack_slots
    observed = []
    model.on_unlock = lambda: observed.append((
        model.load(destination), model.count(), model.references[1000],
    ))
    pending = model.pending_exception

    token = model.copy_entry(entry, destination, source)
    assert token == 1
    assert model.acquisitions == [(source, 0), (destination, 1)]
    assert observed == [(1000, 1, 2)]
    assert model.pending_exception is pending
    model.clear_source(source, exits=source_exits)
    assert model.load(source) == (0 if source_exits else 7)
    assert model.load(destination) == 1000
    assert model.references[1000] == 1
    assert model.count() == model.active() == model.pinned() == 1

    # This is the actual forwarding commit guard, not a fake "move" whose
    # answer is computed directly from the expected lease count.
    model.object(2000)
    model.ns["pcc_gc_object_is_known_no_lock"] = (
        lambda value: int(value in model.nodes)
    )
    load_functions(RUNTIME / "freestanding_gc_forwarding_identity.py", {
        "pcc_gc_install_forwarding_unlocked",
    }, model.ns)
    model.lock()
    assert model.ns["pcc_gc_install_forwarding_unlocked"](1000, 2000) == -2
    model.unlock()
    assert model.count(1000) == 1 and model.count(2000) == 0
    assert model.load(destination) == 1000
    assert model.release(destination, token) == 0
    assert model.count() == model.active() == model.pinned() == 0
    assert model.references[1000] == 1  # release consumes the lease, not root


@pytest.mark.parametrize("entry", ("ordinary", "split"))
def test_copied_destination_lease_blocks_gc3_oldification_after_source_exit(entry):
    model = DestinationLeaseModel(3)
    source = model.object(flags=128 | 4096)
    destination = model.empty()
    model.stack_slots.add(source)
    token = model.copy_entry(entry, destination, source)
    assert token == 1
    model.clear_source(source, exits=True)
    model.ns.update(
        pcc_gc_object_is_known_no_lock=lambda value: int(value in model.nodes),
        pcc_gc_forwarding_find=lambda value: 0,
        pcc_gc_generational_object_is_address_pinned=model.pinned,
    )
    load_functions(RUNTIME / "freestanding_gc_generational_oldification.py", {
        "pcc_gc_generational_oldify_copy",
    }, model.ns)
    model.lock()
    assert model.ns["pcc_gc_generational_oldify_copy"](1000) == 0
    model.unlock()
    assert model.load(destination) == 1000 and model.count() == 1
    assert model.release(destination, token) == 0


@pytest.mark.parametrize("backend", (3, 4))
@pytest.mark.parametrize("entry", ("ordinary", "split"))
@pytest.mark.parametrize("failure", ("overflow", "unproven", "frozen"))
def test_destination_acquire_failure_rolls_back_before_outer_unlock(
    backend, entry, failure,
):
    model = DestinationLeaseModel(backend)
    source = model.object(node=failure != "unproven", proven=failure != "unproven")
    destination = model.empty()
    model.stack_slots.add(source)
    if failure == "overflow":
        model.store(model.nodes[1000], 80, 2**63 - 1)
    elif failure == "frozen":
        model.store("pcc_gc_foreign_lease_active", 0, -1)
    expected = -2 if failure == "overflow" else -1
    pending = model.pending_exception
    observed = []
    model.on_unlock = lambda: observed.append((
        model.load(destination), model.references[1000],
    ))

    assert model.copy_entry(entry, destination, source) == expected
    assert model.acquisitions == [(source, 0), (destination, expected)]
    assert model.load(source) == 1000 and model.load(destination) == 0
    assert model.references[1000] == 1 and observed == [(0, 1)]
    assert model.pending_exception is pending
    assert [call[1:] for call in model.commits] == [
        (destination, 1000), (destination, 0),
    ]
    first_plan, rollback_plan = (call[0] for call in model.commits)
    assert first_plan != rollback_plan
    assert [event[1] for event in model.events if event[0] == "finish"] == [
        first_plan, rollback_plan,
    ]
    outer_unlock = model.events.index(("unlock", 0))
    assert all(index > outer_unlock for index, event in enumerate(model.events)
               if event[0] == "finish")
    assert model.active() == (-1 if failure == "frozen" else 0)
    if failure != "unproven":
        assert model.count() == (2**63 - 1 if failure == "overflow" else 0)


@pytest.mark.parametrize("backend", (3, 4))
@pytest.mark.parametrize("entry", ("ordinary", "split"))
def test_two_conservative_stack_roots_keep_noop_token(backend, entry):
    model = DestinationLeaseModel(backend)
    source, destination = model.object(), model.empty()
    model.stack_slots.update((source, destination))
    assert model.copy_entry(entry, destination, source) == 0
    assert model.acquisitions == [(source, 0), (destination, 0)]
    assert model.load(destination) == 1000 and model.references[1000] == 2
    assert model.count() == model.active() == 0
    assert model.release(destination, 0) == 0


@pytest.mark.parametrize("backend", range(5))
@pytest.mark.parametrize("entry", ("ordinary", "split"))
@pytest.mark.parametrize("probe", (1, 2))
@pytest.mark.parametrize("node,expected", ((True, 1), (False, 2)))
def test_checked_source_tokens_transfer_once_without_destination_reacquire(
    backend, entry, probe, node, expected,
):
    model = DestinationLeaseModel(backend, probe=probe)
    source = model.object(node=node, flags=262144)
    destination = model.empty()
    model.stack_slots.add(source)
    assert model.copy_entry(entry, destination, source) == expected
    assert model.acquisitions == [(source, expected)]
    assert model.active() == 1 and model.references[1000] == 2
    assert model.release(destination, expected) == 0
    assert model.active() == 0


@pytest.mark.parametrize("backend", (3, 4))
@pytest.mark.parametrize("entry", ("ordinary", "split"))
@pytest.mark.parametrize("node,expected", ((True, 1), (False, 2)))
def test_production_heap_source_tokens_transfer_once(backend, entry, node, expected):
    model = DestinationLeaseModel(backend)
    source = model.object(node=node, flags=262144)
    destination = model.empty()
    assert model.copy_entry(entry, destination, source) == expected
    assert model.acquisitions == [(source, expected)]
    assert model.active() == 1
    assert model.release(destination, expected) == 0
    assert model.active() == 0


@pytest.mark.parametrize("backend", (0, 1, 2))
@pytest.mark.parametrize("entry", ("ordinary", "split"))
def test_nonmoving_production_copy_does_not_acquire_destination(backend, entry):
    model = DestinationLeaseModel(backend)
    source, destination = model.object(), model.empty()
    model.stack_slots.add(source)
    assert model.copy_entry(entry, destination, source) == 0
    assert model.acquisitions == [(source, 0)]
    assert model.count() == model.active() == 0
    assert model.references[1000] == 2


@pytest.mark.parametrize("backend", (3, 4))
@pytest.mark.parametrize("entry", ("ordinary", "split"))
def test_failed_source_acquire_never_publishes_or_acquires_destination(backend, entry):
    model = DestinationLeaseModel(backend)
    source = model.object(node=False, proven=False)
    destination = model.empty()
    assert model.copy_entry(entry, destination, source) == -1
    assert model.acquisitions == [(source, -1)]
    assert not model.commits
    assert model.load(source) == 1000 and model.load(destination) == 0
    assert model.references[1000] == 1 and model.active() == 0


@pytest.mark.parametrize("backend", (3, 4))
@pytest.mark.parametrize("entry", ("ordinary", "split"))
def test_failed_publication_does_not_attempt_destination_acquire(backend, entry):
    model = DestinationLeaseModel(backend)
    source, destination = model.object(), model.empty()
    model.stack_slots.add(source)
    model.fail_commit = True
    assert model.copy_entry(entry, destination, source) == -4
    assert model.acquisitions == [(source, 0)]
    assert model.load(source) == 1000 and model.load(destination) == 0
    assert model.references[1000] == 1 and model.count() == model.active() == 0


@pytest.mark.parametrize("backend", (3, 4))
def test_two_heap_copies_keep_independent_leases_after_source_exit(backend):
    model = DestinationLeaseModel(backend)
    source = model.object()
    first, second = model.empty(90000), model.empty(90008)
    model.stack_slots.add(source)
    first_token = model.copy(first, source)
    second_token = model.copy(second, source)
    assert first_token == second_token == 1
    model.clear_source(source, exits=True)
    assert model.references[1000] == model.count() == model.active() == 2
    assert model.release(first, first_token) == 0
    assert model.count() == model.active() == model.pinned() == 1
    assert model.release(second, second_token) == 0
    assert model.count() == model.active() == model.pinned() == 0


def test_optimized_copy_keeps_rollback_scratch_in_separate_cold_frame(tmp_path):
    # Match the final runtime-IR pipeline used by the refcount layout test.
    # A branch-local alloca still reserves native frame space on every entry;
    # the rollback helper must remain a real call after optimization.
    from pcc.frontends.python.pipeline import compile_python
    from pcc.frontends.python.owned_runtime_build import runtime_ir_passes
    from pcc.ir.optimization.driver import optimize_ir

    root = Path(__file__).resolve().parents[2]
    output = tmp_path / "py_obj.ll"
    compile_python(
        str(root / "pcc/runtime/py/py_obj.py"), str(output),
        emit_llvm_only=True, python_library=True,
    )
    text = optimize_ir(output.read_text(), runtime_ir_passes(str(root / "pcc/runtime")))

    def body(name):
        found = re.search(
            r"^define[^\n]*@" + re.escape(name) + r"\([^\n]*\n(.*?)^}",
            text, re.M | re.S,
        )
        assert found is not None, name
        return found.group(1)

    cold_name = "user_py_obj__gc_root_copy_lease_rollback"
    ordinary = body("pcc_gc_root_copy_lease")
    cold = body(cold_name)
    assert "@" + cold_name + "(" in ordinary
    for function in (ordinary, cold):
        allocations = [line for line in function.splitlines() if "alloca " in line]
        assert len(allocations) == 1, allocations
        assert re.search(r"\balloca\s+\[128\s+x\s+i8\]", allocations[0]) or re.search(
            r"\balloca\s+i8,\s+i(?:32|64)\s+128\b", allocations[0]
        ), allocations
    init = cold.index("@pcc_gc_store_root_plan_init(")
    clear = cold.index("@pcc_gc_store_root_plan_commit_locked(")
    unlock = cold.index("@pcc_py_gc_minor_graph_unlock(")
    finishes = [match.start() for match in re.finditer(
        r"@pcc_gc_store_root_plan_finish\(", cold,
    )]
    assert init < clear < unlock
    assert len(finishes) == 2 and all(position > unlock for position in finishes)
