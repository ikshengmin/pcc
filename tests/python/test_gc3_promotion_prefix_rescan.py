"""GC3 dirty owners must revisit slots behind an active promotion cursor.

These are HOST actual-source obligation tests, not native execution, thread
scheduling, sole-root survival, or reclamation evidence. PipelineGC3 supplies
the existing finite memory/index/world model. The actual scheduler, remembered
queue, promotion worklist, slot walker, oldification, barrier and telemetry
reset bodies run unchanged. Telemetry reset's GC4 epoch helpers remain opaque,
and owner snapshot refresh is an explicit finite thread boundary.

The mutator primitive is a raw slot publication plus the real write barrier
under the model graph lock. It is not the complete py_list transaction. Every
mutation occurs after the preceding generational step has resumed the world.
Young-list sweeping and extra roots cannot supply the tested promotion: calls
use promote_all_young=0, with no child roots or explicit major collection.
"""

import pytest

from pcc.runtime.py import py_abi_constants as abi
from tests.python.test_gc3_norc_collection_entry import PipelineGC3


YOUNG, OLD, REMEMBERED = 128, 256, 512
WHITE, MALLOC_MANAGED = 8, 262144
OWNER_LENGTH = 65
STEP_BUDGET = 4
GC4_RESET_BOUNDARIES = (
    "_backend4_reset_store_buffer_epoch_state",
    "_backend4_reset_remembered_set_epoch_state",
    "_backend4_reseed_relocation_epoch_state",
    "_backend4_clear_large_deferred_flags",
)


class PrefixRescanGC3(PipelineGC3):
    def __init__(self):
        super().__init__()

        def snapshot_stopped_world_owner():
            # thread_kernel_pthread:670-693 refreshes the stop owner's own
            # machine-word snapshot and returns 1; it returns -1 without
            # ownership, or 0 for disabled capture / a missing record.
            # Here GC3.words is the explicit finite snapshot consumed by
            # GC3.visit_words. This fixture models an enabled owner record,
            # and the conservative collector calls this boundary locked.
            # No roots, slot visits, or promotion outcomes are invented.
            assert self.stopped and self.depth > 0
            return 1

        self.ns["pcc_thread_snapshot_stopped_world_owner"] = snapshot_stopped_world_owner
        self.load_bodies("py_gc_backend.py", {
            "pcc_gc_telemetry_reset", "_counter_global", "_atomic_i32_store",
        })
        self.load_bodies("freestanding_gc_root_operations.py", {
            "pcc_gc_metric_shard_reset",
        })
        # Configuration is already initialized by the existing finite model.
        self.ns["_init_config"] = self.ns["pcc_gc_config_ensure"]
        self.gc4_reset_calls = []
        for name in GC4_RESET_BOUNDARIES:
            self.ns[name] = lambda name=name: self.gc4_reset_calls.append(name)
        self.store("pcc_gc_backend3_remembered_owner_allocation_limit", 0, -1)
        self.phase = "setup"
        self.owner = 0
        self.owner_node = 0
        self.slot_visits = []
        self.step_owner_pins = []
        self.slice_starts = []
        self.remembered_handoffs = []
        actual_slot = self.ns["pcc_gc_generational_promote_slot"]
        actual_slice = self.ns["pcc_gc_visit_object_slots_slice"]
        actual_drain = self.ns["pcc_gc_backend3_drain_remembered_owners"]

        def observe_slot(slot, role, context):
            assert self.stopped and self.depth > 0
            self.slot_visits.append((self.phase, slot, self.load(slot, 0)))
            return actual_slot(slot, role, context)

        def observe_slice(obj, cursor, limit, visitor, context, state):
            assert self.stopped and self.depth > 0
            if obj == self.owner:
                self.slice_starts.append((self.phase, cursor))
                self.step_owner_pins.append(
                    self.ns["pcc_gc_generational_object_is_address_pinned"](obj)
                )
            return actual_slice(obj, cursor, limit, visitor, context, state)

        def observe_remembered_drain(budget, detached):
            assert self.stopped and self.depth > 0
            if self.owner and self.load(self.owner, 12) & REMEMBERED:
                self.remembered_handoffs.append((
                    self.phase,
                    self.ns["pcc_gc_backend3_promotion_cursor"](self.owner_node),
                    self.load("pcc_gc_backend3_promotion_head", 0),
                ))
            return actual_drain(budget, detached)

        # Observers delegate every call and never supply promotion success.
        self.ns["pcc_gc_generational_promote_slot"] = observe_slot
        self.ns["pcc_gc_visit_object_slots_slice"] = observe_slice
        self.ns["pcc_gc_backend3_drain_remembered_owners"] = observe_remembered_drain
        self.stopped = False

    def assert_no_masking_roots(self):
        assert self.root_cells == [] and self.module_roots == []
        assert self.words in ([], [self.owner + 16])
        for name in (
            "pcc_gc_frame_head", "pcc_gc_continuation_root_head",
            "pcc_gc_scheduler_root_head", "pcc_gc_mark_active",
            "pcc_gc_explicit_collect_active", "pcc_gc_cycle_requested",
        ):
            assert self.load(name, 0) == 0, name

    def step(self):
        assert not self.stopped and self.depth == 0
        self.assert_no_masking_roots()
        # This is the real nursery-style scheduler mode, not pcc_gc_step's
        # promote-all-young path. No alternate collection entrypoint runs.
        result = self.ns["pcc_gc_generational_step"](STEP_BUDGET, 0)
        assert not self.stopped and self.depth == 0
        self.assert_no_masking_roots()
        return result

    def publish_slot(self, index, child):
        assert not self.stopped and self.depth == 0
        assert 0 <= index < OWNER_LENGTH
        slot = self.load(self.owner, 32) + index * 8
        with self.locked():
            # Mirror the primitive barrier-before-publication order. No
            # other modeled mutator can enter this graph-lock transaction.
            self.ns["pcc_gc_note_slot_write_barrier"](self.owner, slot, child)
            self.store(slot, 0, child)
        assert not self.stopped and self.depth == 0
        assert self.load(self.owner, 12) & REMEMBERED
        assert self.load("pcc_gc_backend3_remembered_owner_head", 0) != 0
        return slot

    def young_child(self):
        assert not self.stopped and self.depth == 0
        child, node, _ = self.value()
        # Keep normal young-list membership rather than omitting the source
        # of a possible masking promotion. Scheduler mode 0 must leave this
        # list alone; the actual slot promotion removes a processed child.
        with self.locked():
            self.ns["pcc_gc_backend3_young_link_head"](node)
        assert self.load("pcc_gc_backend3_young_head", 0) == node
        return child

    def work_snapshot(self):
        names = (
            "pcc_gc_backend3_remembered_owner_head",
            "pcc_gc_backend3_remembered_overflow",
            "pcc_gc_backend3_remembered_scan_cursor",
            "pcc_gc_backend3_remembered_scan_revision",
            "pcc_gc_backend3_promotion_head",
            "pcc_gc_backend3_promotion_tail",
            "pcc_gc_backend3_promotion_revision",
        )
        return (
            tuple(self.load(name, 0) for name in names),
            self.load(self.owner_node, 56),
            self.load(self.owner, 12),
        )

    def reset_preserving_work(self, count):
        assert not self.stopped and self.depth == 0
        before = self.work_snapshot()
        calls_before = len(self.gc4_reset_calls)
        for _ in range(count):
            self.store("pcc_gc_metric_step", 0, 7)
            self.ns["pcc_gc_telemetry_reset"]()
            assert not self.stopped and self.depth == 0
            assert self.load("pcc_gc_metric_step", 0) == 0
            assert self.work_snapshot() == before
        assert self.gc4_reset_calls[calls_before:] == list(GC4_RESET_BOUNDARIES) * count

    def finish_pending_work(self):
        # 65 owner slots plus scalar leaves fit well inside this bound, even
        # if a correct dirty-owner implementation restarts the owner once.
        for _ in range(32):
            if not (
                self.load("pcc_gc_backend3_remembered_owner_head", 0)
                or self.load("pcc_gc_backend3_remembered_overflow", 0)
                or self.load("pcc_gc_backend3_promotion_head", 0)
            ):
                break
            self.step()
        assert self.load("pcc_gc_backend3_remembered_owner_head", 0) == 0
        assert self.load("pcc_gc_backend3_remembered_overflow", 0) == 0
        assert self.load("pcc_gc_backend3_promotion_head", 0) == 0
        assert self.load("pcc_gc_backend3_promotion_tail", 0) == 0
        assert self.load(self.owner, 12) & REMEMBERED == 0


def _partially_scanned_owner(pinned):
    m = PrefixRescanGC3()
    m.owner, m.owner_node, _ = m.value(abi.PY_TYPE_LIST)
    m.store(m.owner, 12, WHITE | OLD | MALLOC_MANAGED)
    items = m.malloc(OWNER_LENGTH * 8)
    for index in range(OWNER_LENGTH):
        m.store(items, index * 8, 2 * index + 1)
    m.store(m.owner, 16, OWNER_LENGTH)
    m.store(m.owner, 24, OWNER_LENGTH)
    m.store(m.owner, 32, items)
    if pinned:
        m.words = [m.owner + 16]
    original = m.young_child()
    m.publish_slot(0, original)
    m.step()
    cursor = m.ns["pcc_gc_backend3_promotion_cursor"](m.owner_node)
    assert 0 < cursor < OWNER_LENGTH - 2 * STEP_BUDGET
    assert m.load("pcc_gc_backend3_promotion_head", 0) == m.owner_node
    assert m.load("pcc_gc_backend3_remembered_owner_head", 0) == 0
    assert m.load(m.owner, 12) & REMEMBERED == 0
    # GC3 now queries this pause's words at a move, without materializing
    # conservative pin bits on every heap node for each budgeted step.
    assert m.load(m.owner_node, 56) == 2 * cursor
    assert m.step_owner_pins and m.step_owner_pins[-1] == int(pinned)
    original_target = m.load(items, 0)
    assert original_target != original
    assert m.load(original_target, 12) & OLD
    assert not m.load(original_target, 12) & YOUNG
    assert ("setup", items, original) in m.slot_visits
    return m, items, original_target


def _assert_owner_pin_survives(m, pinned):
    assert m.load(m.owner_node, 56) == 0
    assert m.step_owner_pins and set(m.step_owner_pins) == {int(pinned)}
    assert m.load("pcc_gc_backend3_step_pin_snapshot", 0) == 0


@pytest.mark.parametrize("resets", [0, 1, 2])
@pytest.mark.parametrize("pinned", [False, True])
def test_unmodified_owner_finishes_without_losing_promoted_child(resets, pinned):
    m, items, original_target = _partially_scanned_owner(pinned)
    m.phase = "unchanged"
    m.reset_preserving_work(resets)
    m.finish_pending_work()
    assert m.load(items, 0) == original_target
    assert m.load(original_target, 16) == 271828
    assert m.load(original_target, 12) & OLD
    assert not m.load(original_target, 12) & YOUNG
    _assert_owner_pin_survives(m, pinned)


@pytest.mark.parametrize("position", ["prefix", "suffix"])
@pytest.mark.parametrize("resets", [0, 1, 2])
@pytest.mark.parametrize("pinned", [False, True])
def test_dirty_owner_promotes_changed_slot_before_work_becomes_idle(position, resets, pinned):
    m, items, _ = _partially_scanned_owner(pinned)
    index = 0 if position == "prefix" else OWNER_LENGTH - 1
    m.phase = "changed"
    child = m.young_child()
    expected_payload = 314159
    m.store(child, 16, expected_payload)
    slot = m.publish_slot(index, child)
    m.reset_preserving_work(resets)
    m.finish_pending_work()

    # The real remembered drain must receive the second dirty request while
    # this owner is still partway through its original promotion pass.
    handoffs = [entry for entry in m.remembered_handoffs if entry[0] == "changed"]
    assert handoffs
    _, handoff_cursor, handoff_head = handoffs[0]
    assert handoff_head == m.owner_node and 0 < handoff_cursor < OWNER_LENGTH
    if position == "prefix":
        assert index < handoff_cursor
    else:
        assert handoff_cursor <= index

    _assert_owner_pin_survives(m, pinned)

    # Intended success obligation, including for the prefix case. Neither an
    # empty queue nor a return value alone proves that the new edge was read.
    assert ("changed", slot, child) in m.slot_visits, {
        "position": position, "resets": resets, "handoffs": handoffs,
        "slice_starts": m.slice_starts,
    }
    promoted = m.load(items, index * 8)
    assert promoted != child and promoted in m.index
    assert m.load(promoted, 16) == expected_payload
    assert m.load(promoted, 12) & OLD
    assert not m.load(promoted, 12) & YOUNG
    assert m.load("pcc_gc_backend3_young_head", 0) == 0


@pytest.mark.parametrize("backend", [3, 4])
@pytest.mark.parametrize("dirty", [False, True])
@pytest.mark.parametrize("mode", ["counted", "norc", "norc-pinned"])
def test_duplicate_promotion_owner_rewinds_only_gc3_dirty_work(backend, dirty, mode):
    # Direct cursor/queue contract, not a simulated GC4 mutation transaction.
    # Raw setup selects each existing header/configuration representation.
    m = PrefixRescanGC3()
    m.ns["pcc_gc_config_ensure"] = lambda: backend
    m.store("pcc_gc_backend_selected", 0, backend)
    m.store("pcc_gc_backend4_no_refcounts", 0, int(mode != "counted"))
    owner, node, _ = m.value()
    m.store(owner, 12, WHITE | OLD | MALLOC_MANAGED)
    pin = int(mode == "norc-pinned")
    m.store(node, 56, pin)
    with m.locked():
        m.ns["pcc_gc_backend3_enqueue_promotion_owner"](owner)
        assert m.ns["pcc_gc_backend3_set_promotion_cursor"](node, 13) == 1
        m.store(owner, 12, m.load(owner, 12) | (REMEMBERED if dirty else 0))
        before = (m.load("pcc_gc_backend3_promotion_head", 0),
                  m.load("pcc_gc_backend3_promotion_tail", 0),
                  m.load(node, 64), m.load(node, 72))
        m.ns["pcc_gc_backend3_enqueue_promotion_owner"](owner)
        cursor = 0 if backend == 3 and dirty else 13
        assert m.ns["pcc_gc_backend3_promotion_cursor"](node) == cursor
        assert m.load(node, 56) == (cursor if mode == "counted" else 2 * cursor + pin)
        assert (m.load("pcc_gc_backend3_promotion_head", 0),
                m.load("pcc_gc_backend3_promotion_tail", 0),
                m.load(node, 64), m.load(node, 72)) == before
        # The drain, not enqueue, retains ownership of clearing the dirty bit.
        assert bool(m.load(owner, 12) & REMEMBERED) == dirty


@pytest.mark.parametrize("resets", [0, 1])
@pytest.mark.parametrize("pinned", [False, True])
def test_dirty_overflow_handoff_revisits_prefix_before_work_becomes_idle(resets, pinned):
    m, items, _ = _partially_scanned_owner(pinned)
    m.phase = "overflow"
    child = m.young_child()
    m.store(child, 16, 314159)
    m.ns["pcc_gc_backend3_remembered_scan_probe_config"](0)
    with m.locked():
        m.ns["pcc_gc_note_slot_write_barrier"](m.owner, items, child)
        m.store(items, 0, child)
    assert m.load(m.owner, 12) & REMEMBERED
    assert m.load("pcc_gc_backend3_remembered_owner_head", 0) == 0
    assert m.load("pcc_gc_backend3_remembered_overflow", 0) == 1
    m.reset_preserving_work(resets)
    m.finish_pending_work()
    _assert_owner_pin_survives(m, pinned)
    assert ("overflow", items, child) in m.slot_visits
    promoted = m.load(items, 0)
    assert promoted != child and promoted in m.index
    assert m.load(promoted, 16) == 314159
    assert m.load(promoted, 12) & OLD
    assert not m.load(promoted, 12) & YOUNG
    assert m.load("pcc_gc_backend3_young_head", 0) == 0
