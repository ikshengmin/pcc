"""A copied root leases no address: its destination keeps the object.

No collector moves an object while a mutator runs (a relocation settles
inside its stop), so a root copy returns the no-op token on every collector:
the destination root keeps the object alive after the source is rebound or
leaves the stack, and nothing has to be pinned for it. The fixed tracing
collectors publish the copy without the graph lock, threaded GC0 takes one
atomic reference, and the checked forms keep their source lease.

Exercise the production copy/acquire/release bodies through the existing
runtime_body_model loader used by HandoffModel. Stack membership and root
store plans are deterministic doubles, not a native collector simulation.
"""

from pathlib import Path
import re

import pytest

from pcc.runtime.py import py_abi_constants as abi
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
        self.no_park = 0
        self.publications = []
        self.atomic_adds = []
        self.ns.update(
            pcc_thread_no_park_enter=self.no_park_enter,
            pcc_thread_no_park_exit=self.no_park_exit,
            _store_root_nonmoving_tracing=self.publish_traced,
            atomic_rmw_i64=self.atomic_add,
            PYOBJECTHEADER_TYPE_TAG_OFFSET=abi.PYOBJECTHEADER_TYPE_TAG_OFFSET,
            PYOBJECTHEADER_REFCOUNT_OFFSET=abi.PYOBJECTHEADER_REFCOUNT_OFFSET,
            PY_FLAG_IMMORTAL=abi.PY_FLAG_IMMORTAL,
        )

    def fix(self):
        """The collector as pcc_gc_fix_backend and the configuration leave
        it once an object exists: threaded GC0 counts atomically, the four
        tracing collectors are count-free and move nothing under a mutator."""
        backend = self.load("pcc_gc_backend_selected")
        if backend == 0:
            self.store("pcc_gc_refcount_atomic", 0, 1)
        else:
            self.store("pcc_gc_backend4_no_refcounts", 0, 1)
            self.store("pcc_gc_nonmoving_count_free", 0, 1)
        return self

    def no_park_enter(self):
        assert self.depth == 0, "no-park interval opened under the graph lock"
        self.no_park += 1
        self.events.append(("no_park_enter", self.no_park))

    def no_park_exit(self):
        assert self.no_park > 0
        self.no_park -= 1
        self.events.append(("no_park_exit", self.no_park))

    def publish_traced(self, slot, value, backend):
        # The real publisher is one no-park interval of its own
        # (test_gc_nonmoving_root_store.py); here it nests in the copy's.
        assert self.no_park == 1 and self.depth == 0 and slot in self.roots
        self.publications.append((slot, value, backend))
        self.events.append(("publish", slot, value))
        self.store(slot, 0, value)

    def atomic_add(self, operation, base, offset, value, order):
        assert (operation, offset, value, order) == ("add", 0, 1, "relaxed")
        assert self.depth == 0
        self.atomic_adds.append(base)
        old = self.load(base, 0)
        self.store(base, 0, old + 1)
        return old

    def graph_locked(self):
        return [event for event in self.events if event[0] in ("lock", "unlock")]

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


TRACING = (1, 2, 3, 4)


@pytest.mark.parametrize("backend", TRACING)
@pytest.mark.parametrize("copy", ("owned", "borrowed"))
@pytest.mark.parametrize("source_kind", ("stack", "heap"))
@pytest.mark.parametrize("source_exits", (False, True))
def test_tracing_copy_publishes_without_graph_lock_or_lease(
    backend, copy, source_kind, source_exits,
):
    model = DestinationLeaseModel(backend).fix()
    source, destination = model.object(), model.empty()
    if source_kind == "stack":
        model.stack_slots.add(source)
    pending = model.pending_exception
    entry = model.copy if copy == "owned" else model.copy_borrowed

    assert entry(destination, source) == 0
    assert model.publications == [(destination, 1000, backend)]
    assert not model.acquisitions and not model.commits
    assert not model.graph_locked() and model.no_park == 0
    assert [event[0] for event in model.events] == [
        "no_park_enter", "publish", "no_park_exit",
    ]
    assert model.pending_exception is pending
    assert model.count() == model.active() == model.pinned() == 0

    # The source is rebound or leaves the stack: the destination root alone
    # keeps the object, with nothing to release but the no-op token.
    if source_exits:
        model.stack_slots.discard(source)
        model.roots.remove(source)
    model.store(source, 0, 7)
    assert model.load(destination) == 1000
    assert model.release(destination, 0) == 0
    assert model.count() == model.active() == model.pinned() == 0


@pytest.mark.parametrize("backend", TRACING)
@pytest.mark.parametrize("copy", ("owned", "borrowed"))
@pytest.mark.parametrize("value", (0, 7))
def test_tracing_copy_of_an_immediate_is_the_same_publication(backend, copy, value):
    model = DestinationLeaseModel(backend).fix()
    source, destination = model.empty(90008), model.empty()
    model.store(source, 0, value)
    entry = model.copy if copy == "owned" else model.copy_borrowed
    assert entry(destination, source) == 0
    assert model.publications == [(destination, value, backend)]
    assert not model.acquisitions and not model.graph_locked()


@pytest.mark.parametrize("backend", TRACING)
@pytest.mark.parametrize("copy", ("owned", "borrowed"))
def test_tracing_copy_into_a_full_destination_changes_nothing(backend, copy):
    model = DestinationLeaseModel(backend).fix()
    source, destination = model.object(), model.object(2000)
    entry = model.copy if copy == "owned" else model.copy_borrowed
    assert entry(destination, source) == -4
    assert model.load(source) == 1000 and model.load(destination) == 2000
    assert not model.publications and not model.acquisitions
    assert not model.graph_locked() and model.no_park == 0
    assert [event[0] for event in model.events] == ["no_park_enter", "no_park_exit"]


@pytest.mark.parametrize("flags,counted", ((0, True), (abi.PY_FLAG_IMMORTAL, False)))
def test_threaded_gc0_copy_takes_one_atomic_reference_and_no_graph_lock(flags, counted):
    model = DestinationLeaseModel(0).fix()
    source, destination = model.object(flags=flags), model.empty()
    model.store(1000, 0, 5)
    assert model.copy(destination, source) == 0
    assert model.load(destination) == 1000
    assert model.load(1000, 0) == (6 if counted else 5)
    assert model.atomic_adds == ([1000] if counted else [])
    assert not model.acquisitions and not model.commits and not model.events
    assert model.release(destination, 0) == 0


@pytest.mark.parametrize("value", (0, 7))
def test_threaded_gc0_copy_of_an_immediate_counts_nothing(value):
    model = DestinationLeaseModel(0).fix()
    source, destination = model.empty(90008), model.empty()
    model.store(source, 0, value)
    assert model.copy(destination, source) == 0
    assert model.load(destination) == value
    assert not model.atomic_adds and not model.events


def test_threaded_gc0_copy_into_a_full_destination_changes_nothing():
    model = DestinationLeaseModel(0).fix()
    source, destination = model.object(), model.object(2000)
    model.store(1000, 0, 5)
    assert model.copy(destination, source) == -4
    assert model.load(1000, 0) == 5 and model.load(destination) == 2000
    assert not model.atomic_adds and not model.events


@pytest.mark.parametrize("count,tag", ((-1, 4), (5, 99)))
def test_threaded_gc0_copy_leaves_a_dying_or_foreign_header_to_the_locked_form(count, tag):
    # A negative count is a dying object and tag 99 is no managed kind: the
    # atomic path touches neither, and the graph-locked form decides.
    model = DestinationLeaseModel(0).fix()
    source, destination = model.object(tag=tag), model.empty()
    model.store(1000, 0, count)
    assert model.copy(destination, source) == 0
    assert not model.atomic_adds and model.load(1000, 0) == count
    assert model.acquisitions == [(source, 0)]
    assert [call[1:] for call in model.commits] == [(destination, 1000)]
    assert model.graph_locked() == [("lock", 1), ("unlock", 0)]


@pytest.mark.parametrize("backend", range(5))
@pytest.mark.parametrize("entry", ("ordinary", "split"))
def test_locked_copy_does_not_lease_the_destination(backend, entry):
    # The graph-locked forms (an unfixed collector, the split entry) lease
    # the source as before and never the destination.
    model = DestinationLeaseModel(backend)
    source, destination = model.object(), model.empty()
    model.stack_slots.add(source)
    assert model.copy_entry(entry, destination, source) == 0
    assert model.acquisitions == [(source, 0)]
    assert model.count() == model.active() == 0
    assert model.references[1000] == 2
    model.clear_source(source, exits=True)
    assert model.load(destination) == 1000 and model.references[1000] == 1
    assert model.release(destination, 0) == 0


@pytest.mark.parametrize("backend", range(5))
@pytest.mark.parametrize("entry", ("ordinary", "split"))
def test_two_stack_roots_keep_noop_token(backend, entry):
    model = DestinationLeaseModel(backend)
    source, destination = model.object(), model.empty()
    model.stack_slots.update((source, destination))
    assert model.copy_entry(entry, destination, source) == 0
    assert model.acquisitions == [(source, 0)]
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


@pytest.mark.parametrize("backend", TRACING)
@pytest.mark.parametrize("probe", (1, 2))
def test_the_probe_keeps_a_fixed_tracing_copy_on_the_checked_form(backend, probe):
    model = DestinationLeaseModel(backend, probe=probe).fix()
    source = model.object(flags=262144)
    destination = model.empty()
    assert model.copy(destination, source) == 1
    assert model.acquisitions == [(source, 1)] and not model.publications
    # The checked lease nests its own graph transaction in the copy's.
    locked = model.graph_locked()
    assert locked[0] == ("lock", 1) and locked[-1] == ("unlock", 0)
    assert model.release(destination, 1) == 0 and model.active() == 0


@pytest.mark.parametrize("entry", ("ordinary", "split"))
@pytest.mark.parametrize("node,expected", ((True, 1), (False, 2)))
def test_unfixed_relocating_heap_source_token_transfers_once(entry, node, expected):
    model = DestinationLeaseModel(4)
    source = model.object(node=node, flags=262144)
    destination = model.empty()
    assert model.copy_entry(entry, destination, source) == expected
    assert model.acquisitions == [(source, expected)]
    assert model.active() == 1
    assert model.release(destination, expected) == 0
    assert model.active() == 0


@pytest.mark.parametrize("entry", ("ordinary", "split"))
def test_failed_source_acquire_never_publishes_or_acquires_destination(entry):
    model = DestinationLeaseModel(4)
    source = model.object(node=False, proven=False)
    destination = model.empty()
    assert model.copy_entry(entry, destination, source) == -1
    assert model.acquisitions == [(source, -1)]
    assert not model.commits
    assert model.load(source) == 1000 and model.load(destination) == 0
    assert model.references[1000] == 1 and model.active() == 0


@pytest.mark.parametrize("backend", range(5))
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


@pytest.mark.parametrize("backend", TRACING)
def test_two_copies_are_two_roots_and_no_lease(backend):
    model = DestinationLeaseModel(backend).fix()
    source = model.object()
    first, second = model.empty(90000), model.empty(90008)
    model.stack_slots.add(source)
    assert model.copy(first, source) == model.copy(second, source) == 0
    model.stack_slots.discard(source)
    model.roots.remove(source)
    model.store(source, 0, 0)
    assert model.load(first) == model.load(second) == 1000
    assert model.count() == model.active() == model.pinned() == 0
    assert not model.acquisitions and not model.graph_locked()
    assert model.release(first, 0) == model.release(second, 0) == 0


def test_optimized_copy_keeps_all_plan_scratch_out_of_public_frames(tmp_path):
    # Match the final runtime-IR pipeline used by the refcount layout test.
    # A branch-local alloca still reserves native frame space on every entry;
    # all three locked helpers must remain real calls after optimization. A host body model cannot prove this fixed-frame
    # property or reproduce a native conservative-stack retention witness.
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

    def calls(function, name):
        return list(re.finditer(
            r"\b(?:call|invoke)\b[^\n]*@" + re.escape(name) + r"\(",
            function,
        ))

    def scratch_sizes(function):
        sizes = []
        for line in function.splitlines():
            array = re.search(r"\balloca\s+\[(\d+)\s+x\s+i8\]", line)
            counted = re.search(r"\balloca\s+i8,\s+i(?:32|64)\s+(\d+)\b", line)
            if array or counted:
                sizes.append(int((array or counted).group(1)))
        return sizes

    locked = {}
    for entry, helper, expected_sizes in (
        ("pcc_gc_root_copy_lease", "_gc_root_copy_lease_locked", [128]),
        ("pcc_gc_root_copy_borrowed_lease", "_gc_root_copy_borrowed_lease_locked", [128, 128]),
        ("pcc_gc_root_copy_local", "_gc_root_copy_local_locked", [128]),
    ):
        public = body(entry)
        helper_name = "user_py_obj_" + helper
        helper_body = body(helper_name)
        locked[entry] = helper_body
        # Check the complete optimized public function, including its fast
        # and invalid-input paths, rather than a separately compiled slice.
        assert not any(size >= 128 for size in scratch_sizes(public)), entry
        call = calls(public, helper_name)
        lock = calls(public, "pcc_py_gc_minor_graph_lock")
        assert len(call) == len(lock) == 1, entry
        assert lock[0].start() < call[0].start(), entry
        assert scratch_sizes(helper_body) == expected_sizes, helper_name
        allocations = [line for line in helper_body.splitlines() if "alloca " in line]
        assert len(allocations) == len(expected_sizes), allocations
        assert not calls(helper_body, "pcc_py_gc_minor_graph_lock"), helper_name
        # A normal thread-entry poll is suppressed by the held graph lock's
        # no-park scope; an independent collector gate must not be inserted.
        assert not calls(helper_body, "pcc_gc_safepoint"), helper_name

    ordinary = locked["pcc_gc_root_copy_lease"]
    for function, expected in (
        (ordinary, 1), (locked["pcc_gc_root_copy_borrowed_lease"], 2),
    ):
        inits = calls(function, "pcc_gc_store_root_plan_init")
        unlocks = calls(function, "pcc_py_gc_minor_graph_unlock")
        assert len(inits) == expected and unlocks
        assert max(call.start() for call in inits) < min(call.start() for call in unlocks)
    local = locked["pcc_gc_root_copy_local"]
    assert len(calls(local, "pcc_gc_root_copy_owned_prepare_locked")) == 1
    assert len(calls(local, "pcc_gc_store_root_plan_init")) == 1
    assert not calls(body("pcc_gc_root_copy_local"), "memset")
