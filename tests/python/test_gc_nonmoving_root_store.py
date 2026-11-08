"""Execute count-free root publication with the actual store/shader bodies.

Contract: docs/project-intent.md, shared root lifetime and five collectors.
Kernel stop admission, raw atomics and index membership are the primitive
boundary here; native tests separately exercise the compiled runtime.
"""
import ast
from pathlib import Path

import pytest


RUNTIME = Path(__file__).resolve().parents[2] / "pcc/runtime/py"
WHITE, GRAY, BLACK, DYING = 8, 16, 32, 524288


def load_functions(namespace, filename, names):
    path = RUNTIME / filename
    nodes = [node for node in ast.parse(path.read_text()).body
             if isinstance(node, ast.FunctionDef) and node.name in names]
    assert {node.name for node in nodes} == set(names)
    for node in nodes:
        node.decorator_list = []
    exec(compile(ast.Module(nodes, type_ignores=[]), str(path), "exec"), namespace)


class RootStore:
    def __init__(self, backend, active=1, flags=WHITE):
        self.slot, self.obj = 0x1000, 0x2000
        # The old root is intentionally not dereferenceable. A count-free
        # store must not read its header just to release no ownership.
        self.words = {(self.slot, 0): 0xBAD0, (self.obj, 12): flags}
        self.globals = {
            "pcc_gc_refcount_atomic": 0,
            "pcc_gc_backend4_no_refcounts": 1,
            "pcc_diagnostics_runtime_log_fast_state": 0,
            "pcc_gc_refcount_provenance_probe": 0,
            "pcc_gc_mark_active": active,
            "pcc_gc_forwarding_population": 0,
        }
        self.depth = self.gray = self.notes = self.lookups = 0
        self.events = []
        self.reads = []
        self.gray_worklist_notes = []
        self.stopped_roots = []
        self.ns = {
            "i64": int, "PYOBJECTHEADER_FLAGS_OFFSET": 12,
            "global_addr": lambda name: name,
            "load_i32": self.read, "load_i64": self.read,
            "load_ptr": self.read, "store_ptr": self.write,
            "store_i32": self.write, "store_i64": self.write,
            "atomic_load_i32": lambda base, offset, order: self.read(base, offset),
            "atomic_cas_i32": self.cas,
            "atomic_rmw_i32": self.rmw,
            "ptr_is_null": lambda value: int(value == 0),
            "is_tagged_int": lambda value: value & 1,
            "null": lambda: 0, "_gc_backend_fast": lambda: backend,
            "pcc_thread_no_park_enter": self.enter,
            "pcc_thread_no_park_exit": self.exit,
            "pcc_gc_note_store": self.note,
            "pcc_gc_object_index_seen_stable": self.member,
            "pcc_gc_gray_count_increment_acq_rel": self.increment,
            "pcc_gc_gray_count_decrement_acq_rel": self.decrement,
            # Queue-disabled publication boundary; execute the real shader.
            "pcc_gc_gray_worklist_note": self.gray_worklist_notes.append,
        }
        for name in ("pcc_py_gc_minor_graph_lock", "pcc_py_gc_minor_graph_unlock",
                     "py_incref", "py_decref", "pcc_gc_load_ptr", "stack_alloc"):
            self.ns[name] = lambda *args, name=name: pytest.fail("unexpected " + name)
        load_functions(self.ns, "freestanding_gc_root_operations.py", {"pcc_gc_shade_gray_atomic"})
        load_functions(self.ns, "py_obj.py", {
            "_store_root_nonmoving_tracing", "_store_root_count_free_unforwarded",
            "pcc_gc_store_root", "pcc_gc_store_root_take",
        })

    def read(self, base, offset):
        self.reads.append((base, offset))
        if isinstance(base, str):
            return self.globals[base]
        assert (base, offset) in self.words, "invalid old/foreign header read"
        return self.words[base, offset]

    def write(self, base, offset, value):
        assert self.depth == 1
        self.words[base, offset] = value
        self.events.append("store")

    def cas(self, base, offset, expected, desired, success, failure):
        assert self.depth == 1
        old = self.read(base, offset)
        if old == expected:
            self.words[base, offset] = desired
        self.events.append("shade")
        return old

    def rmw(self, op, base, offset, value, order):
        assert op == "add" and base == "pcc_gc_cms_wb_flushes" and self.depth == 1
        old = self.globals.get(base, 0)
        self.globals[base] = old + value
        return old

    def enter(self):
        assert self.depth == 0
        self.depth = 1
        self.events.append("enter")

    def exit(self):
        assert self.depth == 1
        # A pending stop is admitted only here, after slot publication and
        # all reserved gray credit has been committed or returned.
        self.depth = 0
        self.events.append("exit")
        self.stopped_roots.append((self.words[self.slot, 0], self.gray))

    def member(self, obj):
        assert self.depth == 1
        self.lookups += 1
        return int(obj == self.obj)

    def note(self):
        assert self.depth == 1
        self.notes += 1

    def increment(self):
        assert self.depth == 1
        self.gray += 1

    def decrement(self):
        assert self.depth == 1 and self.gray > 0
        self.gray -= 1


@pytest.mark.parametrize("backend", (1, 2))
@pytest.mark.parametrize("entry", ("pcc_gc_store_root", "pcc_gc_store_root_take"))
@pytest.mark.parametrize("value_kind", ("object", "null", "tagged", "foreign"))
@pytest.mark.parametrize("active", (0, 1))
def test_root_publication_has_no_graph_or_refcount_protocol(backend, entry, value_kind, active):
    model = RootStore(backend, active)
    value = {"object": model.obj, "null": 0, "tagged": 123, "foreign": 0xCAFE}[value_kind]
    model.ns[entry](model.slot, value)
    shaded = int(active and value_kind == "object")
    assert model.words[model.slot, 0] == value
    assert model.notes == 1 and model.depth == 0 and model.gray == shaded
    assert model.stopped_roots == [(value, shaded)]
    assert model.events[0] == "enter" and model.events[-1] == "exit"
    if shaded:
        assert model.events.index("store") < model.events.index("shade")
    if not active or value_kind in ("null", "tagged"):
        assert model.lookups == 0
    assert model.globals["pcc_gc_mark_active"] == active


@pytest.mark.parametrize("backend", (1, 2))
@pytest.mark.parametrize("color", (GRAY, BLACK, WHITE | DYING))
def test_root_barrier_preserves_collector_color_policy(backend, color):
    model = RootStore(backend, flags=color)
    model.ns["pcc_gc_store_root"](model.slot, model.obj)
    shaded = int(backend == 2 and color == BLACK)
    assert model.gray == shaded
    assert model.words[model.obj, 12] == (GRAY if shaded else color)


@pytest.mark.parametrize("entry", ("pcc_gc_store_root", "pcc_gc_store_root_take"))
def test_null_root_address_has_no_effect(entry):
    model = RootStore(1)
    model.ns[entry](0, model.obj)
    assert not model.events and model.notes == model.lookups == 0


@pytest.mark.parametrize("backend", (1, 2))
@pytest.mark.parametrize("probe,logging", ((1, 0), (0, 1)))
def test_checked_or_logged_roots_keep_the_original_plan(backend, probe, logging):
    model = RootStore(backend)
    model.globals.update(pcc_gc_refcount_provenance_probe=probe,
                         pcc_diagnostics_runtime_log_fast_state=logging)
    events = []
    model.ns.update(
        stack_alloc=lambda size: 0x3000,
        pcc_gc_store_root_plan_init=lambda plan, selected: events.append(("init", selected)),
        pcc_py_gc_minor_graph_lock=lambda: events.append("lock"),
        pcc_gc_store_root_plan_commit_locked=lambda *args: events.append("commit"),
        pcc_py_gc_minor_graph_unlock=lambda: events.append("unlock"),
        pcc_gc_store_root_plan_finish=lambda plan: events.append("finish"),
    )
    model.ns["pcc_gc_store_root"](model.slot, model.obj)
    assert events == [("init", backend), "lock", "commit", "unlock", "finish"]
    assert not model.events


# GC3/GC4 (count-free, moving): with no forwarded object a root store is the
# same lock-free publication; a white value is shaded in a cycle as by every
# tracing root barrier. These tests hold the modeled forwarding phase fixed.
@pytest.mark.parametrize("backend", (3, 4))
@pytest.mark.parametrize("entry", ("pcc_gc_store_root", "pcc_gc_store_root_take"))
@pytest.mark.parametrize("value_kind", ("object", "null", "tagged", "foreign"))
@pytest.mark.parametrize("active", (0, 1))
def test_unforwarded_moving_root_publication_is_lock_free(backend, entry, value_kind, active):
    model = RootStore(backend, active)
    value = {"object": model.obj, "null": 0, "tagged": 123, "foreign": 0xCAFE}[value_kind]
    model.ns[entry](model.slot, value)
    shaded = int(active and value_kind == "object")
    assert model.words[model.slot, 0] == value
    assert model.notes == 1 and model.depth == 0 and model.gray == shaded
    assert model.stopped_roots == [(value, shaded)]
    if shaded:
        assert model.events.index("store") < model.events.index("shade")
    if not active or value_kind in ("null", "tagged"):
        assert model.lookups == 0


@pytest.mark.parametrize("backend", (3, 4))
@pytest.mark.parametrize("color", (GRAY, BLACK, WHITE | DYING))
def test_moving_root_barrier_shades_only_white(backend, color):
    model = RootStore(backend, flags=color)
    model.ns["pcc_gc_store_root"](model.slot, model.obj)
    assert model.gray == 0
    assert model.words[model.obj, 12] == color


@pytest.mark.parametrize("backend", (3, 4))
def test_forwarded_phase_keeps_the_relocating_plan(backend):
    model = RootStore(backend)
    model.globals["pcc_gc_forwarding_population"] = 1
    events = []
    model.ns.update(
        stack_alloc=lambda size: 0x3000,
        pcc_gc_store_root_plan_init=lambda plan, selected: events.append(("init", selected)),
        pcc_py_gc_minor_graph_lock=lambda: events.append("lock"),
        pcc_gc_store_root_plan_commit_locked=lambda *args: events.append("commit"),
        pcc_py_gc_minor_graph_unlock=lambda: events.append("unlock"),
        pcc_gc_store_root_plan_finish=lambda plan: events.append("finish"),
    )
    model.ns["pcc_gc_store_root"](model.slot, model.obj)
    assert events == [("init", backend), "lock", "commit", "unlock", "finish"]
    # The population test ran inside one no-park interval and stored nothing.
    assert model.events == ["enter", "exit"] and model.notes == 0
    assert model.words[model.slot, 0] == 0xBAD0


# The shared pointer ABI has one immediate tag: low bit one means signed
# int63. None, bool, float and static literals have headers, not new tags.
IMMEDIATE_ROOTS = [
    pytest.param(0, id="null"),
    pytest.param(1, id="int-zero"),
    pytest.param(3, id="int-positive"),
    pytest.param((1 << 64) - 1, id="int-negative"),
    pytest.param((1 << 63) + 1, id="int-min"),
    pytest.param((1 << 63) - 1, id="int-max"),
]
ROOT_ENTRIES = ("pcc_gc_store_root", "pcc_gc_store_root_take")


class GraphWait(Exception):
    """Model a writer suspended at a graph lock held by a root healer."""


class ImmediateRootStore(RootStore):
    def __init__(self, backend, active=1):
        super().__init__(backend, active)
        self.graph_depth = 0
        self.graph_events = []
        self.reader_holds_graph = False
        self.ns.update(
            pcc_py_gc_minor_graph_lock=self.lock,
            pcc_py_gc_minor_graph_unlock=self.unlock,
        )

    def lock(self):
        # The rejected no-park probe must have exited before a graph wait.
        assert self.depth == 0
        if self.reader_holds_graph:
            raise GraphWait
        self.enter()
        self.graph_depth = 1
        self.graph_events.append("lock")

    def unlock(self):
        assert self.graph_depth == 1
        self.graph_depth = 0
        self.graph_events.append("unlock")
        self.exit()

    def write(self, base, offset, value):
        if base == self.slot and self.globals["pcc_gc_forwarding_population"]:
            assert self.graph_depth == 1
        super().write(base, offset, value)


@pytest.mark.parametrize("backend", (3, 4))
@pytest.mark.parametrize("entry", ROOT_ENTRIES)
@pytest.mark.parametrize("value", IMMEDIATE_ROOTS)
@pytest.mark.parametrize("active", (0, 1))
@pytest.mark.parametrize("forwarding", (0, 1, 17))
def test_moving_immediate_roots_skip_plan_but_keep_forwarded_graph_lock(
    backend, entry, value, active, forwarding,
):
    model = ImmediateRootStore(backend, active)
    model.globals["pcc_gc_forwarding_population"] = forwarding
    model.gray = 7
    model.ns[entry](model.slot, value)
    assert model.words[model.slot, 0] == value
    assert model.notes == 1 and model.depth == model.graph_depth == 0
    assert model.gray == 7 and not model.gray_worklist_notes and not model.lookups
    assert not any(base in (model.slot, model.obj) for base, _ in model.reads)
    assert ("pcc_gc_mark_active", 0) not in model.reads
    assert ("pcc_gc_forwarding_population", 0) in model.reads
    if forwarding:
        assert model.events == ["enter", "exit", "enter", "store", "exit"]
        assert model.graph_events == ["lock", "unlock"]
        assert model.stopped_roots == [(0xBAD0, 7), (value, 7)]
    else:
        assert model.events == ["enter", "store", "exit"]
        assert not model.graph_events and model.stopped_roots == [(value, 7)]


@pytest.mark.parametrize("backend", (3, 4))
@pytest.mark.parametrize("active", (0, 1))
@pytest.mark.parametrize("value", (2, 6, 0xCAFE, 0x2000, 1 << 63))
def test_forwarded_phase_rejects_every_non_immediate_before_header_or_store(
    backend, active, value,
):
    model = RootStore(backend, active)
    model.globals["pcc_gc_forwarding_population"] = 1
    model.gray = 7
    assert model.ns["_store_root_count_free_unforwarded"](model.slot, value) == 0
    assert model.events == ["enter", "exit"]
    assert model.words[model.slot, 0] == 0xBAD0
    assert model.notes == model.lookups == model.depth == 0
    assert model.gray == 7 and not model.gray_worklist_notes
    assert model.reads == [("pcc_gc_forwarding_population", 0)]


@pytest.mark.parametrize("backend", (3, 4))
@pytest.mark.parametrize("entry", ROOT_ENTRIES)
@pytest.mark.parametrize("value", IMMEDIATE_ROOTS)
@pytest.mark.parametrize("gate,setting", (
    ("pcc_gc_backend4_no_refcounts", 0),
    ("pcc_gc_refcount_provenance_probe", 1),
    ("pcc_gc_refcount_provenance_probe", 2),
    ("pcc_gc_refcount_provenance_probe", 3),
    ("pcc_diagnostics_runtime_log_fast_state", -1),
    ("pcc_diagnostics_runtime_log_fast_state", 1),
))
def test_moving_immediates_preserve_counted_and_diagnostic_plan_gates(
    backend, entry, value, gate, setting,
):
    model = RootStore(backend)
    model.globals.update({"pcc_gc_forwarding_population": 1, gate: setting})

    class PlanSelected(Exception):
        pass

    def select_plan(size):
        assert size == 128
        raise PlanSelected

    model.ns["stack_alloc"] = select_plan
    with pytest.raises(PlanSelected):
        model.ns[entry](model.slot, value)
    assert not model.events and model.notes == model.depth == 0
    assert model.words[model.slot, 0] == 0xBAD0


class ImmediatePlanRootStore(ImmediateRootStore):
    """Actual count-free StorePlan, load helper and metric bodies over cells."""

    def __init__(self, backend, active=1):
        super().__init__(backend, active)
        self.plan = 0x3000
        self.plan_events = []
        self.logs = []
        self.metrics = {}
        self.load_calls = []
        self.globals.update({
            "pcc_gc_config_initialized": 1,
            "pcc_gc_backend_selected": backend,
            "pcc_gc_read_barrier_enabled": 1,
            "pcc_gc_metric_load": 0,
        })
        self.ns.update(
            stack_alloc=self.allocate_plan,
            memset=self.clear_plan,
            pcc_gc_pointer_is_managed=lambda value: int(value == self.obj),
            pcc_gc_metric_shard=lambda: 256,
            atomic_rmw_i64=self.metric_add,
            pcc_diagnostics_runtime_log_event_code=lambda *args: self.logs.append(args),
        )
        load_functions(self.ns, "py_gc_backend.py", {"pcc_gc_note_store", "pcc_gc_note_load"})
        load_functions(self.ns, "py_obj.py", {
            "pcc_gc_store_root_plan_init", "_pcc_gc_store_plan_commit_locked",
            "pcc_gc_store_root_plan_commit_locked", "_pcc_gc_store_plan_finish",
            "pcc_gc_store_root_plan_finish", "_gc_forwarding_population",
            "_gc_relocation_candidate", "_ptr_can_have_header", "pcc_gc_load_ptr",
        })
        self.actual_load_ptr = self.ns["pcc_gc_load_ptr"]
        self.ns["pcc_gc_load_ptr"] = self.load_after_store

    def load_after_store(self, owner, slot):
        self.load_calls.append((owner, slot))
        return self.actual_load_ptr(owner, slot)

    def write(self, base, offset, value):
        if base == "pcc_gc_metric_load":
            assert offset == 0 and self.depth == 0
            self.globals[base] = value
        elif base == self.plan:
            self.words[base, offset] = value
        else:
            super().write(base, offset, value)

    def allocate_plan(self, size):
        assert size == 128 and self.depth == 0
        self.plan_events.append("plan")
        return self.plan

    def clear_plan(self, plan, value, size):
        assert plan == self.plan and value == 0 and size == 128
        for offset in range(0, size, 4):
            self.words[plan, offset] = 0

    def metric_add(self, operation, base, offset, value, order):
        assert (operation, base, offset, value, order) == (
            "add", "pcc_gc_metric_shards", 272, 1, "relaxed",
        )
        assert self.depth == self.graph_depth == 1
        old = self.metrics.get((base, offset), 0)
        self.metrics[base, offset] = old + value
        self.notes += 1
        return old


@pytest.mark.parametrize("backend", (3, 4))
@pytest.mark.parametrize("entry", ROOT_ENTRIES)
@pytest.mark.parametrize("value", IMMEDIATE_ROOTS)
@pytest.mark.parametrize("active", (0, 1))
@pytest.mark.parametrize("fallback", ("probe", "log"))
def test_moving_immediate_direct_and_actual_plan_match_publication_and_store_metrics(
    backend, entry, value, active, fallback,
):
    direct = ImmediatePlanRootStore(backend, active)
    planned = ImmediatePlanRootStore(backend, active)
    planned.globals["pcc_gc_refcount_provenance_probe" if fallback == "probe"
                    else "pcc_diagnostics_runtime_log_fast_state"] = 1
    for model in (direct, planned):
        model.globals["pcc_gc_forwarding_population"] = 1
        model.gray = 7
        model.ns[entry](model.slot, value)
        assert model.words[model.slot, 0] == value
        assert model.notes == 1 and model.depth == model.graph_depth == 0
        assert model.gray == 7
        assert model.metrics == {("pcc_gc_metric_shards", 272): 1}
        assert model.graph_events == ["lock", "unlock"]
        assert not model.gray_worklist_notes and not model.lookups
        assert (0xBAD0, 12) not in model.reads
    assert not direct.plan_events and not direct.logs
    assert planned.plan_events == ["plan"]
    assert direct.stopped_roots == [(0xBAD0, 7), (value, 7)]
    assert planned.stopped_roots == [(value, 7)]
    # Count-free direct take no longer reloads a tagged root merely to skip
    # decref. Execute the real load helper: GC3 counts that read with forwarding
    # active; GC4 skips immediate read telemetry. NULL already skips reload.
    # Store metrics match, load metrics need not.
    reloads = int(entry == "pcc_gc_store_root_take" and value != 0)
    assert not direct.load_calls and direct.globals["pcc_gc_metric_load"] == 0
    assert planned.load_calls == ([(0, planned.slot)] if reloads else [])
    assert planned.globals["pcc_gc_metric_load"] == int(backend == 3 and reloads)
    assert planned.logs == ([(2, 3, backend, 0, 0)] if fallback == "log" else [])
    assert planned.words[planned.plan, 124] == 7
    planned.ns["pcc_gc_store_root_plan_finish"](planned.plan)
    assert planned.notes == 1 and len(planned.logs) == int(fallback == "log")


@pytest.mark.parametrize("backend", (3, 4))
@pytest.mark.parametrize("entry", ROOT_ENTRIES)
@pytest.mark.parametrize("value", (0, 35))
@pytest.mark.parametrize("writer_first", (False, True))
def test_forwarded_immediate_store_serializes_with_actual_shared_root_healer(
    backend, entry, value, writer_first,
):
    model = ImmediateRootStore(backend)
    model.globals.update({
        "pcc_gc_forwarding_population": 1,
        "pcc_gc_config_initialized": 1,
        "pcc_gc_backend_selected": backend,
    })
    source, target, edge = model.obj, 0x4000, 0x5000
    model.words.update({(model.slot, 0): source, (source, 12): WHITE | 2048,
                        (edge, 8): target})
    heals = []

    def reader_load(base, offset):
        captured = model.read(base, offset)
        if base == model.slot and not writer_first:
            # Reader owns graph and has captured P, before its eventual Q
            # heal. A writer cannot complete its NULL/tagged publication now.
            with pytest.raises(GraphWait):
                model.ns[entry](model.slot, value)
            assert model.words[model.slot, 0] == source and model.notes == 0
            assert model.depth == 0
        return captured

    def reader_store(base, offset, stored):
        assert model.reader_holds_graph and base == model.slot
        heals.append(stored)
        model.words[base, offset] = stored

    resolver = dict(model.ns)
    resolver.update(
        load_ptr=reader_load, store_ptr=reader_store,
        ptr_eq=lambda lhs, rhs: lhs == rhs,
        pcc_gc_object_is_known_no_lock=lambda obj: int(obj == source),
        pcc_gc_forwarding_index_find=lambda obj: edge if obj == source else 0,
        # The real count-free retain/release have no ownership effect.
        py_incref=lambda obj: None, py_decref=lambda obj: None,
    )
    load_functions(resolver, "freestanding_gc_root_operations.py", {"_resolve_root_slot_mode"})
    if writer_first:
        model.ns[entry](model.slot, value)
    model.reader_holds_graph = True
    resolved = resolver["_resolve_root_slot_mode"](model.slot, 0, 0)
    model.reader_holds_graph = False
    if writer_first:
        assert resolved == value and not heals
    else:
        assert resolved == target and heals == [target]
        # Resume the suspended writer at an available lock. Repeating its
        # side-effect-free probe models admission, not a second publication.
        model.ns[entry](model.slot, value)
    assert model.words[model.slot, 0] == value and model.notes == 1
    assert model.depth == model.graph_depth == 0
    assert model.graph_events == ["lock", "unlock"]


@pytest.mark.parametrize("backend", (3, 4))
@pytest.mark.parametrize("entry", ROOT_ENTRIES)
@pytest.mark.parametrize("value", (0, 35))
@pytest.mark.parametrize("outer", ("none", "lease", "graph"))
@pytest.mark.parametrize("pending_stop,pending_flush", ((0, 0), (1, 0), (0, 1), (1, 1)))
@pytest.mark.parametrize("tripwire", (False, True))
def test_forwarded_immediate_store_keeps_real_graph_lock_and_stop_boundaries(
    backend, entry, value, outer, pending_stop, pending_flush, tripwire,
):
    model = RootStore(backend)
    registered, graph_level, tripwire_bit = 562949953421312, 16777216, 2251799813685248
    outer_lease = 2 if outer == "lease" else int(outer == "graph")
    initial = registered + outer_lease + (graph_level if outer == "graph" else 0)
    model.globals.update({
        "pcc_gc_forwarding_population": 1,
        "pcc_tls_thread_state_py": initial,
        "g_pcc_py_gc_minor_graph_lock": int(outer == "graph"),
        "pcc_thread_stop_requested": pending_stop,
        "pcc_gc_gray_worklist_flush_requested": pending_flush,
    })
    polled, finished = [], []
    model.gray = 7

    def state():
        return model.globals["pcc_tls_thread_state_py"]

    def kernel_write(base, offset, stored):
        if isinstance(base, str):
            model.globals[base] = stored
        else:
            assert base == model.slot
            assert state() & 16777215 and state() & 281474959933440
            assert model.globals["g_pcc_py_gc_minor_graph_lock"] == 1
            model.words[base, offset] = stored

    def note():
        assert state() & 16777215 and state() & 281474959933440
        model.notes += 1
        if tripwire:
            model.globals["pcc_tls_thread_state_py"] |= tripwire_bit

    def safepoint():
        assert (state() & 16777215) == 0
        assert model.globals["g_pcc_py_gc_minor_graph_lock"] == 0
        polled.append((model.words[model.slot, 0], model.gray))

    def finish_tripwire():
        assert model.globals["g_pcc_py_gc_minor_graph_lock"] == 0
        assert state() & 16777215
        finished.append(model.words[model.slot, 0])

    def lock_cas(base, offset, expected, desired, success, failure):
        assert base == "g_pcc_py_gc_minor_graph_lock" and offset == 0
        old = model.globals[base]
        assert old == expected == 0
        model.globals[base] = desired
        return old

    model.ns.update(
        store_i64=kernel_write, store_ptr=kernel_write,
        atomic_store_i32=lambda base, offset, stored, order: kernel_write(base, offset, stored),
        atomic_cas_i32=lock_cas, _GRAPH_QUEUED_BIT=4503599627370496,
        pcc_gc_note_store=note, pcc_thread_safepoint=safepoint,
        _finish_deferred_tripwire=finish_tripwire,
        pcc_platform_abort=lambda: pytest.fail("graph/no-park imbalance"),
        pcc_current_thread_id=lambda: pytest.fail("registered thread re-registered"),
    )
    load_functions(model.ns, "freestanding_thread_kernel_pthread.py", {
        "pcc_thread_no_park_enter", "pcc_thread_no_park_exit",
        "pcc_thread_graph_lock_acquire_for", "pcc_thread_graph_lock_release",
    })
    load_functions(model.ns, "freestanding_runtime_high_substrate.py", {
        "pcc_py_gc_minor_graph_lock", "pcc_py_gc_minor_graph_unlock",
    })
    model.ns[entry](model.slot, value)
    assert model.words[model.slot, 0] == value and model.notes == 1
    assert (state() & ~tripwire_bit) == initial
    assert model.gray == 7 and not model.gray_worklist_notes
    if outer != "none":
        assert not polled
        if outer == "graph":
            model.ns["pcc_py_gc_minor_graph_unlock"]()
        else:
            for _ in range(outer_lease):
                model.ns["pcc_thread_no_park_exit"]()
    expected_polls = [(0xBAD0, 7), (value, 7)] if outer == "none" else [(value, 7)]
    assert polled == (expected_polls if pending_stop or pending_flush else [])
    assert finished == ([value] if tripwire else [])
    assert state() == registered
    assert model.globals["g_pcc_py_gc_minor_graph_lock"] == 0


@pytest.mark.parametrize("backend", (3, 4))
@pytest.mark.parametrize("entry", ROOT_ENTRIES)
def test_forwarded_immediate_cleanup_never_reads_old_root_or_adds_gray(backend, entry):
    model = ImmediateRootStore(backend)
    model.globals["pcc_gc_forwarding_population"] = 1
    model.gray = 7
    values = (35, (1 << 64) - 1, 0, 0)
    for value in values:
        model.ns[entry](model.slot, value)
    assert model.words[model.slot, 0] == 0 and model.notes == 4
    assert model.depth == model.graph_depth == 0 and model.gray == 7
    assert model.graph_events == ["lock", "unlock"] * 4
    assert not model.lookups and not model.gray_worklist_notes
    assert not any(base == model.slot for base, _ in model.reads)
