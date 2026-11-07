"""Execute production tracing/unlink bodies against finite raw-memory doubles.

Contract: docs/project-intent.md (five collectors and ordinary object lifetime),
and the concurrent-slice node budget in pcc_gc_tracing_step_cycle. These checks
bound object-list nodes with the persistent gray queue explicitly disabled;
one object's outgoing references remain unsliced. No persistent-queue, native
ABI, thread scheduling, or end-to-end collection claim follows here.
"""

from __future__ import annotations

import ast
import os
from pathlib import Path

import pytest


REPO = Path(os.environ.get("PCC_SCAN_BUDGET_REPO", Path(__file__).resolve().parents[2])).resolve()
RUNTIME = REPO / "pcc/runtime/py"
GRAY, BLACK, DYING = 16, 32, 524288
MODES = [(1, 0), (2, 0), (3, 0), (4, 0), (4, 1)]


def load_functions(namespace, filename, names):
    source = RUNTIME / filename
    selected = []
    for node in ast.parse(source.read_text(encoding="utf-8")).body:
        if isinstance(node, ast.FunctionDef) and node.name in names:
            node.decorator_list = []
            selected.append(node)
    assert {node.name for node in selected} == set(names)
    exec(compile(ast.Module(body=selected, type_ignores=[]), str(source), "exec"), namespace)


class TraceState:
    def __init__(self, backend=4, tracing=0, stopped=False):
        self.memory = {}
        self.address = 256
        self.nodes = []
        self.objects = {}
        self.removed = set()
        self.visited_nodes = []
        self.visited_payloads = []
        self.completions = []
        self.gray_worklist_events = []
        self.depth = 0
        self.lock_acquisitions = 0
        self.stopped = stopped
        self.store("pcc_gc_backend_selected", 0, backend)
        # Keep this node-budget model on the object-list fallback.
        self.store("pcc_gc_nonmoving_count_free", 0, 0)
        self.store("pcc_gc_backend4_no_refcounts", 0, tracing)
        self.store("pcc_gc_mark_active", 0, 1)
        self.store("pcc_gc_tracing_cycle_epoch", 0, 9)
        noop = lambda *args: None
        ns = {
            "i64": int, "c_ptr": int, "PYOBJECTHEADER_FLAGS_OFFSET": 12,
            "null": lambda: 0, "ptr_is_null": lambda p: int(p == 0),
            "ptr_eq": lambda a, b: int(a == b),
            "is_tagged_int": lambda p: int(isinstance(p, int) and bool(p & 1)),
            "global_addr": lambda name: name,
            "global_load_ptr": lambda name: self.load(name, 0),
            "global_store_ptr": lambda name, value: self.store(name, 0, value),
            "load_i32": self.load, "load_i64": self.load, "load_ptr": self.load,
            "store_i32": self.store, "store_i64": self.store, "store_ptr": self.store,
            "atomic_load_i32": lambda a, o, order: self.load(a, o),
            "atomic_store_i32": lambda a, o, v, order: self.store(a, o, v),
            "atomic_cas_i32": self.cas, "stack_alloc": self.allocate,
            "pcc_py_gc_minor_graph_lock": self.lock,
            "pcc_py_gc_minor_graph_unlock": self.unlock,
            "pcc_thread_owns_stopped_world": lambda: int(self.stopped),
            "pcc_threads_enabled": lambda: 1,
            "pcc_capi_is_cext_type_tag": lambda tag: 0,
            "pcc_gc_visit_object_slots": self.visit_payload,
            "pcc_gc_trace_referents_work": self.trace_fixed_owner_work,
            "pcc_gc_trace_slot": noop,
            "pcc_gc_complete_claimed_tracing_cycle": self.complete,
            # This fixture measures the original object-list fallback budget.
            "pcc_gc_gray_worklist_take_locked": self.take_disabled_gray_worklist,
            "pcc_gc_gray_worklist_flush": lambda: self.gray_worklist_events.append("flush"),
            "_promotion_unlink": noop, "pcc_gc_backend3_young_unlink": noop,
        }
        self.ns = ns
        load_functions(ns, "freestanding_gc_public_collection.py", {
            "pcc_gc_is_cms_worker",
        })
        load_functions(ns, "freestanding_gc_root_operations.py", {
            "pcc_gc_gray_count_load_acquire", "pcc_gc_gray_count_decrement_acq_rel",
            "pcc_gc_set_mark_color",
        })
        load_functions(ns, "freestanding_gc_common_mark_cycle.py", {
            "pcc_gc_trace_referents", "pcc_gc_trace_stack_begin",
            "pcc_gc_trace_stack_drain", "pcc_gc_trace_stack_end",
            "pcc_gc_trace_stack_drain_work",
        })
        load_functions(ns, "freestanding_gc_object_nodes.py", {
            "pcc_gc_object_set_list_head", "pcc_gc_trace_cursor_load",
            "pcc_gc_trace_cursor_store", "pcc_gc_object_node_next",
            "pcc_gc_object_node_prev", "pcc_gc_object_node_set_next",
            "pcc_gc_object_node_set_prev", "pcc_gc_object_node_unlink",
        })
        load_functions(ns, "freestanding_gc_incremental_concurrent_scheduler.py", {
            "pcc_gc_tracing_step_cycle", "pcc_gc_tracing_finish_left_to_mutator",
        })

    def take_disabled_gray_worklist(self):
        self.gray_worklist_events.append("take")
        return 0

    def allocate(self, size):
        result = self.address
        self.address += max(256, size + 16)
        return result

    def load(self, address, offset):
        assert address not in self.removed, "read from a removed cursor node"
        if address in self.nodes and offset == 16:
            self.visited_nodes.append(address)
        return self.memory.get((address, offset), 0)

    def store(self, address, offset, value):
        self.memory[address, offset] = value

    def cas(self, address, offset, expected, desired, success, failure):
        assert self.depth > 0
        observed = self.load(address, offset)
        if observed == expected:
            self.store(address, offset, desired)
        return observed

    def lock(self):
        self.lock_acquisitions += 1
        self.depth += 1

    def unlock(self):
        assert self.depth > 0
        self.depth -= 1

    def trace_fixed_owner_work(self, obj):
        # This node-budget model has fixed one-unit payloads. Variable slot
        # costs use the real visitor in test_gc_whole_owner_work_budget.py.
        self.ns["pcc_gc_trace_referents"](obj)
        return 1

    def visit_payload(self, obj, callback, context):
        assert self.depth > 0
        self.visited_payloads.append(obj)

    def complete(self, epoch, backend):
        assert self.depth == 0
        self.completions.append((epoch, backend))

    @property
    def cursor(self):
        return self.load("pcc_gc_trace_cursor", 0)

    @property
    def gray(self):
        return self.load("pcc_gc_gray_count", 0)

    def node(self, color=BLACK, kind="object"):
        node = self.allocate(88)
        obj = self.allocate(64)
        if kind == "null":
            obj = 0
        elif kind == "tagged":
            obj = 3
        else:
            self.store(obj, 8, 5)
            self.store(obj, 12, color | (DYING if kind == "dying" else 0))
        freeing = int(kind == "freeing")
        self.store(node, 0, obj)
        self.store(node, 8, 64)
        self.store(node, 32, freeing)
        if self.nodes:
            self.store(self.nodes[-1], 16, node)
            self.store(node, 40, self.nodes[-1])
        else:
            self.store("pcc_gc_object_head", 0, node)
        self.nodes.append(node)
        self.objects[node] = obj
        if color & GRAY and not freeing and kind not in {"null", "tagged"}:
            self.store("pcc_gc_gray_count", 0, self.gray + 1)
        return node

    def step(self, budget):
        self.visited_nodes.clear()
        result = self.ns["pcc_gc_tracing_step_cycle"](budget)
        assert self.depth == 0
        assert self.load("pcc_gc_trace_stack_owner", 0) == 0
        assert self.load("pcc_gc_trace_stack_count", 0) == 0
        return result

    def remove(self, node):
        # Execute the actual unlink body, then forbid any later stale read.
        self.lock()
        self.ns["pcc_gc_object_node_unlink"](node)
        self.unlock()
        self.removed.add(node)


@pytest.mark.parametrize("backend,tracing", MODES)
@pytest.mark.parametrize("budget", [1, 3, 8])
def test_sparse_gray_long_black_prefix_advances_without_debt_credit(backend, tracing, budget):
    state = TraceState(backend, tracing)
    prefix = [state.node() for _ in range(97)]
    target = state.node(GRAY)
    tail = state.node()
    order = prefix + [target, tail]
    offset = 0
    while offset + budget <= len(prefix):
        assert state.step(budget) == 0
        assert state.visited_nodes == order[offset:offset + budget]
        offset += budget
        assert state.cursor == order[offset]
        assert state.gray == 1 and state.visited_payloads == []
    assert state.step(budget) == 1
    assert len(state.visited_nodes) <= budget
    assert state.visited_payloads == [state.objects[target]]
    assert state.gray == 0
    assert state.load(state.objects[target], 12) & 56 == BLACK


@pytest.mark.parametrize("backend,tracing", MODES)
def test_mixed_nodes_preserve_gray_return_count_and_node_budget(backend, tracing):
    state = TraceState(backend, tracing)
    first = state.node(GRAY)
    black = state.node()
    second = state.node(GRAY)
    cursor = state.node()
    third, tail = state.node(GRAY), state.node()
    assert state.step(3) == 2
    assert state.visited_nodes == [first, black, second]
    assert state.cursor == cursor and state.gray == 1
    assert state.visited_payloads == [state.objects[first], state.objects[second]]
    assert state.step(2) == 1
    assert state.visited_nodes == [cursor, third]
    assert state.cursor == 0 and state.gray == 0


@pytest.mark.parametrize("backend,tracing", MODES)
def test_freeing_null_tagged_and_dying_nodes_each_spend_one_scan(backend, tracing):
    state = TraceState(backend, tracing)
    skipped = [state.node(GRAY, "freeing"), state.node(kind="null"),
               state.node(kind="tagged"), state.node(BLACK, "dying")]
    target = state.node(GRAY)
    tail = state.node()
    order = skipped + [target, tail]
    for index, node in enumerate(skipped):
        assert state.step(1) == 0
        assert state.visited_nodes == [node]
        assert state.cursor == order[index + 1]
        assert state.gray == 1 and state.visited_payloads == []
    assert state.step(1) == 1
    assert state.visited_nodes == [target]
    assert state.cursor == 0 and state.gray == 0


@pytest.mark.parametrize("backend,tracing", MODES)
def test_unlinked_next_cursor_is_repaired_before_following_slice(backend, tracing):
    state = TraceState(backend, tracing)
    first, removed = state.node(), state.node()
    target, tail = state.node(GRAY), state.node()
    assert state.step(1) == 0 and state.cursor == removed
    state.remove(removed)
    assert state.cursor == target
    assert state.load(first, 16) == target and state.load(target, 40) == first
    assert state.step(1) == 1
    assert state.visited_nodes == [target]
    assert state.cursor == 0 and state.gray == 0


@pytest.mark.parametrize("backend,tracing", MODES)
def test_removed_tail_cursor_restarts_head_and_finds_new_gray(backend, tracing):
    state = TraceState(backend, tracing)
    first, tail = state.node(GRAY), state.node()
    # A barrier can gray an earlier object after a preceding slice advanced
    # past it. Removing the tail cursor must restart and find that work.
    state.store("pcc_gc_trace_cursor", 0, tail)
    state.remove(tail)
    assert state.cursor == 0
    assert state.step(1) == 1
    assert state.visited_nodes == [first]
    assert state.gray == 0 and state.completions == [(9, backend)]


@pytest.mark.parametrize("backend,tracing", MODES)
def test_stopped_world_crosses_long_black_prefix_in_one_progress_call(backend, tracing):
    state = TraceState(backend, tracing, stopped=True)
    prefix = [state.node() for _ in range(97)]
    target, tail = state.node(GRAY), state.node()
    assert state.step(1) == 1
    assert state.visited_nodes == prefix + [target]
    assert state.cursor == 0 and state.gray == 0
    assert state.visited_payloads == [state.objects[target]]


@pytest.mark.parametrize("budget", [0, -1])
def test_nonpositive_budget_does_not_take_graph_lock_or_advance(budget):
    state = TraceState()
    node = state.node(GRAY)
    state.store("pcc_gc_trace_cursor", 0, node)
    assert state.step(budget) == 0
    assert state.cursor == node and state.gray == 1
    assert state.visited_nodes == []
    assert state.lock_acquisitions == 0


@pytest.mark.parametrize("backend,tracing", MODES)
def test_empty_gray_work_finishes_without_visiting_black_object_list(backend, tracing):
    state = TraceState(backend, tracing)
    nodes = [state.node() for _ in range(4096)]
    state.store("pcc_gc_trace_cursor", 0, nodes[0])
    assert state.step(1) == 0
    assert state.visited_nodes == []
    assert state.cursor == 0 and state.gray == 0
    assert state.completions == [(9, backend)]


@pytest.mark.parametrize("backend,tracing", MODES)
def test_last_gray_object_does_not_scan_the_unreachable_or_black_tail(backend, tracing):
    state = TraceState(backend, tracing)
    target = state.node(GRAY)
    for index in range(4096):
        state.node(BLACK if index % 2 else 8)
    assert state.step(512) == 1
    assert state.visited_nodes == [target]
    assert state.visited_payloads == [state.objects[target]]
    assert state.cursor == 0 and state.gray == 0
    assert state.completions == [(9, backend)]


@pytest.mark.parametrize("backend,tracing", MODES)
def test_tracing_the_last_gray_parent_keeps_new_child_work(backend, tracing):
    state = TraceState(backend, tracing)
    parent, child = state.node(GRAY), state.node(8)

    def visit(obj, callback, context):
        state.visit_payload(obj, callback, context)
        if obj == state.objects[parent]:
            state.store(state.objects[child], 12, GRAY)
            state.store("pcc_gc_gray_count", 0, state.gray + 1)

    state.ns["pcc_gc_visit_object_slots"] = visit
    assert state.step(1) == 1
    assert state.cursor == child and state.gray == 1
    assert state.completions == []
    assert state.step(1) == 1
    assert state.visited_payloads == [state.objects[parent], state.objects[child]]
    assert state.cursor == 0 and state.gray == 0
    assert state.completions == [(9, backend)]


@pytest.mark.parametrize("backend,tracing", MODES)
def test_pending_extension_scan_prevents_empty_work_completion(backend, tracing):
    state = TraceState(backend, tracing)
    state.node()
    state.store("pcc_gc_trace_cext_pending_obj", 0, 0x9000)
    assert state.step(1) == 0
    assert state.visited_nodes == [] and state.completions == []


@pytest.mark.parametrize("backend,tracing", MODES)
@pytest.mark.parametrize("worker", [False, True])
@pytest.mark.parametrize("stopped", [False, True])
def test_empty_work_finish_claim_respects_worker_handoff(backend, tracing, worker, stopped):
    state = TraceState(backend, tracing, stopped=stopped)
    state.store("pcc_gc_cms_worker_current", 0, int(worker))
    node = state.node()
    state.store("pcc_gc_trace_cursor", 0, node)

    assert state.step(1) == 0
    assert state.visited_nodes == [] and state.visited_payloads == []
    assert state.cursor == 0 and state.gray == 0
    deferred = worker and backend in (1, 2) and not stopped
    if deferred:
        assert state.completions == []
        assert state.load("pcc_gc_tracing_finish_claim_epoch", 0) == 0
    else:
        # The completion callback records an unlocked claim boundary only.
        # It does not model final remark, sweep or a committed collection.
        assert state.completions == [(9, backend)]
        assert state.load("pcc_gc_tracing_finish_claim_epoch", 0) == 9
        assert state.load("pcc_gc_tracing_finish_claim_backend", 0) == backend


@pytest.mark.parametrize("backend", [1, 2])
def test_new_gray_work_before_mutator_handoff_is_traced_before_finish_claim(backend):
    state = TraceState(backend)
    state.store("pcc_gc_cms_worker_current", 0, 1)
    child = state.node(8)
    assert state.step(1) == 0
    assert state.completions == []
    assert state.load("pcc_gc_tracing_finish_claim_epoch", 0) == 0

    # Model a barrier publishing gray work between worker and mutator calls.
    # The barrier itself is outside this finite raw-memory boundary.
    state.store(state.objects[child], 12, GRAY)
    state.store("pcc_gc_gray_count", 0, 1)
    state.store("pcc_gc_cms_worker_current", 0, 0)
    assert state.step(1) == 1
    assert state.visited_nodes == [child]
    assert state.visited_payloads == [state.objects[child]]
    assert state.load(state.objects[child], 12) & 56 == BLACK
    assert state.cursor == 0 and state.gray == 0
    assert state.completions == [(9, backend)]
    assert state.load("pcc_gc_tracing_finish_claim_epoch", 0) == 9

