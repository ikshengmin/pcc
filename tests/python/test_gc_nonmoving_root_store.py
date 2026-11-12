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
        self.store_depths = []
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
            # The root store counts itself inline (no entry poll).
            "pcc_gc_metric_shard": lambda: 256,
            "atomic_rmw_i64": self.metric_add,
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
        self.store_depths.append(self.depth)
        self.words[base, offset] = value
        self.events.append("store")

    def metric_add(self, op, base, offset, value, order):
        assert (op, base, offset, value, order) == (
            "add", "pcc_gc_metric_shards", 256 + 16, 1, "relaxed")
        self.notes += 1

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
    if active and value_kind in ("object", "foreign"):
        # A cycle is active: the shade runs in a no-park scope after the
        # store, and a pending stop is admitted only at its exit.
        expected = ["store", "enter"] + ["shade"] * shaded + ["exit"]
        assert model.events == expected
        assert model.stopped_roots == [(value, shaded)]
    else:
        # Outside a cycle, or for an immediate, the store owes nothing:
        # no no-park scope, no index probe, no stop admitted here.
        assert model.events == ["store"] and model.stopped_roots == []
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


# GC3/GC4 share the publication of GC1/GC2. No object moves while a mutator
# runs: relocation happens inside a stopped world and its references are
# rewritten before the world resumes (pcc_gc_relocation_settle_stopped), so
# a root store consults no forwarding state and takes no graph lock.
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
    if active and value_kind in ("object", "foreign"):
        assert model.events == ["store", "enter"] + ["shade"] * shaded + ["exit"]
        assert model.stopped_roots == [(value, shaded)]
    else:
        assert model.events == ["store"] and model.stopped_roots == []
        assert model.lookups == 0


@pytest.mark.parametrize("backend", (3, 4))
@pytest.mark.parametrize("color", (GRAY, BLACK, WHITE | DYING))
def test_moving_root_barrier_shades_only_white(backend, color):
    model = RootStore(backend, flags=color)
    model.ns["pcc_gc_store_root"](model.slot, model.obj)
    assert model.gray == 0
    assert model.words[model.obj, 12] == color


@pytest.mark.parametrize("backend", (1, 2, 3, 4))
@pytest.mark.parametrize("entry", ("pcc_gc_store_root", "pcc_gc_store_root_take"))
@pytest.mark.parametrize("forwarding", (1, 17))
def test_a_forwarding_population_does_not_change_root_publication(backend, entry, forwarding):
    # The model's graph lock, plan allocation and refcount calls all fail
    # the test: a count of forwarded objects selects none of them.
    model = RootStore(backend)
    model.globals["pcc_gc_forwarding_population"] = forwarding
    model.ns[entry](model.slot, model.obj)
    assert model.events == ["store", "enter", "shade", "exit"]
    assert model.words[model.slot, 0] == model.obj and model.notes == 1
    assert model.stopped_roots == [(model.obj, 1)]
    assert ("pcc_gc_forwarding_population", 0) not in model.reads
    # The old root is released by no count and is never dereferenced.
    assert not any(base == 0xBAD0 for base, _ in model.reads)


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


class ImmediateRootStore(RootStore):
    """A graph lock that records its use; a root store must not take it."""

    def __init__(self, backend, active=1):
        super().__init__(backend, active)
        self.graph_depth = 0
        self.graph_events = []
        self.ns.update(
            pcc_py_gc_minor_graph_lock=self.lock,
            pcc_py_gc_minor_graph_unlock=self.unlock,
        )

    def lock(self):
        # A graph wait can park: it never starts inside a no-park interval.
        assert self.depth == 0
        self.enter()
        self.graph_depth = 1
        self.graph_events.append("lock")

    def unlock(self):
        assert self.graph_depth == 1
        self.graph_depth = 0
        self.graph_events.append("unlock")
        self.exit()


@pytest.mark.parametrize("backend", (1, 2, 3, 4))
@pytest.mark.parametrize("entry", ROOT_ENTRIES)
@pytest.mark.parametrize("value", IMMEDIATE_ROOTS)
@pytest.mark.parametrize("active", (0, 1))
@pytest.mark.parametrize("forwarding", (0, 1, 17))
def test_immediate_roots_publish_in_one_no_park_interval_without_the_graph_lock(
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
    # An immediate is no object: neither the mark phase nor a forwarding
    # count is read for it.
    assert ("pcc_gc_mark_active", 0) not in model.reads
    assert ("pcc_gc_forwarding_population", 0) not in model.reads
    # It owes no barrier, so it opens no no-park scope either.
    assert model.events == ["store"]
    assert not model.graph_events and model.stopped_roots == []


@pytest.mark.parametrize("backend", (1, 2, 3, 4))
@pytest.mark.parametrize("entry", ROOT_ENTRIES)
@pytest.mark.parametrize("active", (0, 1))
@pytest.mark.parametrize("value", (2, 6, 0xCAFE, 0x2000, 1 << 63))
def test_non_immediate_roots_publish_under_a_forwarding_population(
    backend, entry, active, value,
):
    model = ImmediateRootStore(backend, active)
    model.globals["pcc_gc_forwarding_population"] = 1
    model.gray = 7
    model.ns[entry](model.slot, value)
    member = int(value == model.obj)
    assert model.words[model.slot, 0] == value and model.notes == 1
    assert model.depth == model.graph_depth == 0 and not model.graph_events
    # Index membership decides whether the value has a header to shade; an
    # address the collector does not own is stored and never dereferenced.
    assert model.lookups == active
    assert model.gray == 7 + (active and member)
    assert ((value, 12) in model.reads) == bool(active and member)
    assert ("pcc_gc_forwarding_population", 0) not in model.reads
    # Only an active cycle opens the shade's no-park scope.
    assert model.stopped_roots == ([(value, 7 + member)] if active else [])


@pytest.mark.parametrize("backend", (1, 2, 3, 4))
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
def test_immediates_preserve_counted_and_diagnostic_plan_gates(
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
            # The collector is fixed: no mutator can observe a forwarded
            # object, and none is owed a remap outside a stop.
            "pcc_gc_nonmoving_count_free": 1,
            "pcc_gc_backend4_remap_owed": 0,
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
        # The count is telemetry, not part of the publication protocol: the
        # direct store counts outside any no-park scope, the diagnostic plan
        # inside its graph lock.
        old = self.metrics.get((base, offset), 0)
        self.metrics[base, offset] = old + value
        self.notes += 1
        return old


@pytest.mark.parametrize("backend", (3, 4))
@pytest.mark.parametrize("entry", ROOT_ENTRIES)
@pytest.mark.parametrize("value", IMMEDIATE_ROOTS)
@pytest.mark.parametrize("active", (0, 1))
@pytest.mark.parametrize("fallback", ("probe", "log"))
def test_immediate_direct_and_actual_plan_match_publication_and_store_metrics(
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
        assert not model.gray_worklist_notes and not model.lookups
        assert (0xBAD0, 12) not in model.reads
        # The direct immediate store opens no no-park scope; the plan's
        # graph lock admits a pending stop at its unlock.
        assert model.stopped_roots == ([] if model is direct else [(value, 7)])
    # Only the checked or logged store goes through the plan and its lock.
    assert not direct.plan_events and not direct.logs and not direct.graph_events
    assert planned.plan_events == ["plan"]
    assert planned.graph_events == ["lock", "unlock"]
    # The planned take reloads a tagged root to skip its release; NULL
    # skips the reload. No collector counts a read of an immediate.
    reloads = int(entry == "pcc_gc_store_root_take" and value != 0)
    assert not direct.load_calls and direct.globals["pcc_gc_metric_load"] == 0
    assert planned.load_calls == ([(0, planned.slot)] if reloads else [])
    assert planned.globals["pcc_gc_metric_load"] == 0
    assert planned.logs == ([(2, 3, backend, 0, 0)] if fallback == "log" else [])
    assert planned.words[planned.plan, 124] == 7
    planned.ns["pcc_gc_store_root_plan_finish"](planned.plan)
    assert planned.notes == 1 and len(planned.logs) == int(fallback == "log")


@pytest.mark.parametrize("backend", (3, 4))
@pytest.mark.parametrize("entry", ROOT_ENTRIES)
@pytest.mark.parametrize("value", (0, 35))
@pytest.mark.parametrize("writer_first", (False, True))
def test_immediate_store_and_the_stopped_root_healer_never_interleave(
    backend, entry, value, writer_first,
):
    # A forwarded root is healed only by a stopped world. An immediate store
    # calls nothing between its write and its return, so no stop can fall
    # inside it: the stop sees the slot before the store or after it; the
    # graph lock plays no part.
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

    def stopped_load(base, offset):
        assert model.depth == 0, "the healer ran inside a root store"
        return model.read(base, offset)

    def stopped_store(base, offset, stored):
        assert model.depth == 0 and base == model.slot
        heals.append(stored)
        model.words[base, offset] = stored

    resolver = dict(model.ns)
    resolver.update(
        load_ptr=stopped_load, store_ptr=stopped_store,
        ptr_eq=lambda lhs, rhs: lhs == rhs,
        pcc_gc_object_is_known_no_lock=lambda obj: int(obj in (source, target)),
        pcc_gc_forwarding_index_find=lambda obj: edge if obj == source else 0,
        # The real count-free retain/release have no ownership effect.
        py_incref=lambda obj: None, py_decref=lambda obj: None,
    )
    load_functions(resolver, "freestanding_gc_root_operations.py", {"_resolve_root_slot_mode"})
    if writer_first:
        model.ns[entry](model.slot, value)
    resolved = resolver["_resolve_root_slot_mode"](model.slot, 0, 0)
    if writer_first:
        assert resolved == value and not heals
    else:
        # Only the relocating collector has a forwarded source to heal.
        assert resolved == (target if backend == 4 else source)
        assert heals == ([target] if backend == 4 else [])
        model.ns[entry](model.slot, value)
    assert model.words[model.slot, 0] == value and model.notes == 1
    assert model.depth == model.graph_depth == 0
    assert not model.graph_events
    assert model.events == ["store"]


@pytest.mark.parametrize("backend", (1, 2, 3, 4))
@pytest.mark.parametrize("entry", ROOT_ENTRIES)
@pytest.mark.parametrize("value", (0, 35))
@pytest.mark.parametrize("outer", ("none", "lease", "graph"))
@pytest.mark.parametrize("pending_stop,pending_flush", ((0, 0), (1, 0), (0, 1), (1, 1)))
def test_immediate_store_keeps_real_no_park_and_stop_boundaries(
    backend, entry, value, outer, pending_stop, pending_flush,
):
    # The kernel's own no-park and graph-lock bodies. An immediate store
    # takes no lease, polls nowhere and leaves the graph lock as it found
    # it; a pending stop or flush is admitted once, when the caller's
    # outermost lease is released (or at the caller's next poll).
    model = RootStore(backend)
    registered, graph_level = 562949953421312, 16777216
    outer_lease = 2 if outer == "lease" else int(outer == "graph")
    initial = registered + outer_lease + (graph_level if outer == "graph" else 0)
    held = int(outer == "graph")
    model.globals.update({
        "pcc_gc_forwarding_population": 1,
        "pcc_tls_thread_state_py": initial,
        "g_pcc_py_gc_minor_graph_lock": held,
        "pcc_thread_stop_requested": pending_stop,
        "pcc_gc_gray_worklist_flush_requested": pending_flush,
    })
    polled = []
    model.gray = 7

    def state():
        return model.globals["pcc_tls_thread_state_py"]

    def kernel_write(base, offset, stored):
        if isinstance(base, str):
            model.globals[base] = stored
        else:
            assert base == model.slot
            # At the caller's own lease depth: the store opens none.
            assert (state() & 16777215) == outer_lease
            assert model.globals["g_pcc_py_gc_minor_graph_lock"] == held
            model.words[base, offset] = stored

    def safepoint():
        assert (state() & 16777215) == 0
        assert model.globals["g_pcc_py_gc_minor_graph_lock"] == 0
        polled.append((model.words[model.slot, 0], model.gray))

    model.ns.update(
        store_i64=kernel_write, store_ptr=kernel_write,
        atomic_store_i32=lambda base, offset, stored, order: kernel_write(base, offset, stored),
        atomic_cas_i32=lambda *args: pytest.fail("a root store took the graph lock"),
        _GRAPH_QUEUED_BIT=4503599627370496,
        pcc_thread_safepoint=safepoint,
        _finish_deferred_tripwire=lambda: pytest.fail("no tripwire was deferred"),
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
    assert state() == initial
    assert model.globals["g_pcc_py_gc_minor_graph_lock"] == held
    assert model.gray == 7 and not model.gray_worklist_notes
    if outer != "none":
        assert not polled
        if outer == "graph":
            model.ns["pcc_py_gc_minor_graph_unlock"]()
        else:
            for _ in range(outer_lease):
                model.ns["pcc_thread_no_park_exit"]()
    admitted = (pending_stop or pending_flush) and outer != "none"
    assert polled == ([(value, 7)] if admitted else [])
    assert state() == registered
    assert model.globals["g_pcc_py_gc_minor_graph_lock"] == 0


@pytest.mark.parametrize("backend", (1, 2, 3, 4))
@pytest.mark.parametrize("entry", ROOT_ENTRIES)
def test_immediate_cleanup_never_reads_old_root_or_adds_gray(backend, entry):
    model = ImmediateRootStore(backend)
    model.globals["pcc_gc_forwarding_population"] = 1
    model.gray = 7
    values = (35, (1 << 64) - 1, 0, 0)
    for value in values:
        model.ns[entry](model.slot, value)
    assert model.words[model.slot, 0] == 0 and model.notes == 4
    assert model.depth == model.graph_depth == 0 and model.gray == 7
    assert not model.graph_events
    assert not model.lookups and not model.gray_worklist_notes
    assert not any(base == model.slot for base, _ in model.reads)


def test_root_store_and_phase_read_cannot_be_split_by_a_stop():
    """No call runs between the slot store and the mark-phase read.

    A stop is admitted only at a poll, and every runtime-port function
    begins with one; the mark phase changes only in a stopped world. With
    only intrinsics between the store and the read, the read answers for
    the store itself, so an inactive cycle needs no no-park scope.
    """
    tree = ast.parse((RUNTIME / "py_obj.py").read_text())
    fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef)
              and n.name == "_store_root_nonmoving_tracing")
    first, decision = fn.body[0], fn.body[1]
    assert ast.unparse(first) == "store_ptr(slot, 0, value)"
    assert isinstance(decision, ast.If)
    intrinsics = {"ptr_is_null", "is_tagged_int", "atomic_load_i32", "global_addr"}
    calls = {n.func.id for n in ast.walk(decision.test) if isinstance(n, ast.Call)}
    assert calls == intrinsics
    assert "pcc_gc_mark_active" in ast.unparse(decision.test)
    # The shade's no-park scope opens before anything that could poll.
    assert ast.unparse(decision.body[0]) == "pcc_thread_no_park_enter()"
    assert ast.unparse(decision.body[-1]) == "pcc_thread_no_park_exit()"
    assert "pcc_gc_note_store" not in ast.unparse(fn)
