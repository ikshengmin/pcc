"""Execute production scalar ownership/retirement over deterministic raw cells.

Specification: docs/project-intent.md, five-GC lifetime equality. Native owned
C fixtures exercise the actual CMS thread separately; neither is full 1c.
"""
from __future__ import annotations

import ast

import pytest

from tests.python.test_gc_deferred_free import RUNTIME, Runtime, _load

SOURCE = RUNTIME / "freestanding_gc_scalar_sweep.py"
PREFIX = "pcc_gc_scalar_sweep_"
FLAGS = 1024 | 8  # Real GC1/2 OBJECT allocations have no MALLOC_ALLOC bit.


class ScalarRuntime(Runtime):
    def __init__(self, backend=1):
        super().__init__(backend)
        self.provenance, self.identities, self.static, self.live_nodes = set(), set(), set(), set()
        self.granules, self.released_nodes = set(), []
        self.live_bytes = 0
        self.remove_failure = False
        self.unlock_hook = None
        self.define_global("pcc_gc_backend_frozen", 1)
        self.define_global("pcc_gc_object_head", 0)
        self.define_global("pcc_gc_tracing_cycle_epoch", 3)
        self.write(self.global_address("pcc_gc_cms_worker_started"), 0, 1)
        self.ns.update({
            "define_global_null_ptr_array": lambda name, count: self.define_array(name, count),
            "abi_constant": {"object.type.int": 2, "object.type.float": 3, "object.type.str": 4}.__getitem__,
            "pcc_gc_object_index_find": lambda obj: self.nodes.get(obj, 0),
            "pcc_gc_object_index_remove": self.remove_index,
            "pcc_gc_identity_index_find": lambda obj: int(obj in self.identities),
            "pcc_gc_managed_pointer_index_contains": lambda obj: int(obj in self.provenance),
            "pcc_gc_granule_is_object_start": lambda obj: 1 if obj in self.granules else -1,
            "pcc_gc_granule_object_retire": self.retire_granule,
            "pcc_gc_object_node_is_address_pinned": lambda node, obj: int(obj in self.static or self.read(obj, 12) & 64 or self.read(node, 56) & 1 or self.read(node, 80)),
            "pcc_gc_object_node_unlink": self.unlink,
            "pcc_gc_object_node_release": self.release_node,
            "pcc_gc_live_bytes_subtract": self.debit,
            "pcc_gc_note_scalar_sweep_claim": lambda obj: self.noted.append(obj),
        })
        _load(SOURCE, self.ns)

    def define_array(self, name, count):
        self.define_global(name)
        self.memory[self.global_address(name)] = {i * 8: 0 for i in range(count)}

    def lock(self):
        self.graph += 1

    def unlock(self):
        assert self.graph > 0
        self.graph -= 1
        if self.graph == 0 and self.unlock_hook is not None:
            self.unlock_hook()

    def object(self, usable=64, tag=3, flags=FLAGS):
        obj = self.alloc(usable, flags, tag)
        self.write(obj, 0, 731)
        node = self.next_address
        self.next_address += 128
        head = self.scalar("pcc_gc_object_head")
        self.memory[node] = {0: obj, 8: usable, 16: head, 24: 0, 32: 0,
                             40: 0, 48: 0, 56: 0, 64: 0, 72: 0, 80: 0}
        if head:
            self.write(head, 40, node)
        self.write(self.global_address("pcc_gc_object_head"), 0, node)
        self.nodes[obj] = node
        self.live_nodes.add(node)
        self.granules.add(obj)
        self.live_bytes += usable
        return obj, node

    def retire_granule(self, obj):
        assert self.graph > 0
        if obj in self.retirement_failures:
            return -1
        self.granules.remove(obj)
        self.managed.remove(obj)
        self.events.append(("granule", obj, self.thread))
        return 1

    def remove_index(self, obj):
        assert self.graph > 0
        if self.remove_failure:
            return 0
        self.events.append(("index", obj, self.thread))
        return self.nodes.pop(obj)

    def unlink(self, node):
        assert self.graph > 0
        prev, nxt = self.read(node, 40), self.read(node, 16)
        if prev:
            self.write(prev, 16, nxt)
        else:
            self.write(self.global_address("pcc_gc_object_head"), 0, nxt)
        if nxt:
            self.write(nxt, 40, prev)
        self.live_nodes.remove(node)

    def release_node(self, node):
        assert self.graph > 0 and node not in self.live_nodes
        self.released_nodes.append(node)
        self.events.append(("node", node, self.thread))
        self.memory[node] = {0: -999, 16: -998, 80: 0}  # immediate pool reuse poison

    def debit(self, size):
        assert self.graph > 0
        self.live_bytes -= size

    def scalar_call(self, name, *args):
        return self.ns[PREFIX + name](*args)

    def sc(self, metric):
        return self.scalar_call("counter", metric)

    def begin(self):
        self.scalar_call("begin")

    def enqueue(self, pair):
        return self.scalar_call("try_enqueue", *pair)

    def seal(self):
        self.scalar_call("seal")
        self.world = 0

    def drain(self):
        self.thread = 2
        return self.scalar_call("drain", 1, 0, 0)


@pytest.mark.parametrize("backend", range(5))
def test_native_mode_admission_boundary(backend):
    rt = ScalarRuntime(backend)
    pair = rt.object()
    rt.begin()
    assert rt.enqueue(pair) == int(backend in (1, 2))
    rt.seal()
    assert rt.drain() == int(backend in (1, 2))


@pytest.mark.parametrize("tag", (2, 3, 4))
def test_pending_keeps_index_node_header_and_debits_once_at_claim(tag):
    rt = ScalarRuntime()
    obj, node = rt.object(tag=tag)
    rt.begin()
    assert rt.enqueue((obj, node)) == 1
    assert rt.enqueue((obj, node)) == 0
    assert rt.nodes[obj] == node and rt.read(node, 32) == -rt.sc(11)
    assert rt.read(obj, 0) == 731 and rt.live_bytes == 0
    assert rt.sc(3) == 1 and rt.sc(4) == rt.sc(5) == 0
    assert rt.scalar_call("owns", obj) == 1
    rt.thread = 2
    assert rt.scalar_call("drain", 1, 0, 0) == 0  # BUILDING is not consumable
    rt.thread = 1
    rt.seal()
    assert rt.nodes[obj] == node and rt.live_bytes == 0
    rt.reuse_on_free = True
    assert rt.drain() == 1
    assert rt.sc(4) == rt.sc(5) == rt.sc(6) == 1 and rt.sc(8) == 2
    assert obj not in rt.nodes and node in rt.released_nodes and rt.live_bytes == 0
    assert rt.sc(0) == rt.sc(1) == rt.sc(2) == rt.sc(9) == 0
    assert rt.drain() == 0


@pytest.mark.parametrize("reason", ("tracked", "gray", "black", "fresh", "pin", "minor", "zpage", "immortal", "static", "lease", "cycle-pin", "raw", "identity", "provenance", "list", "cext", "generator", "class", "not-frozen"))
def test_exclusions_keep_synchronous_owner(reason):
    rt = ScalarRuntime()
    obj, node = rt.object()
    bits = {"tracked": 2, "gray": 16, "black": 32, "fresh": 16384,
            "pin": 64, "minor": 4096, "zpage": 65536, "immortal": 1}
    if reason in bits:
        rt.write(obj, 12, FLAGS | bits[reason])
    if reason == "static": rt.static.add(obj)
    if reason == "lease": rt.write(node, 80, 1)
    if reason == "cycle-pin": rt.write(node, 56, 1)
    if reason == "raw": rt.granules.remove(obj)
    if reason == "identity": rt.identities.add(obj)
    if reason == "provenance": rt.provenance.add(obj)
    if reason == "not-frozen": rt.write(rt.global_address("pcc_gc_backend_frozen"), 0, 0)
    if reason in ("list", "cext", "generator", "class"):
        rt.write(obj, 8, {"list": 5, "cext": 65536, "generator": 12, "class": 100}[reason])
    rt.begin()
    assert rt.enqueue((obj, node)) == 0
    assert rt.read(node, 32) == 0 and rt.read(obj, 0) == 731
    rt.seal()
    assert rt.drain() == 0 and not rt.events


@pytest.mark.parametrize("state,stop", ((0, 0), (2, 0), (3, 1), (4, 1), (1, 1)))
def test_missing_starting_stopping_worker_falls_back_before_ownership(state, stop):
    rt = ScalarRuntime()
    rt.write(rt.global_address("pcc_gc_cms_worker_started"), 0, state)
    rt.write(rt.global_address("pcc_gc_cms_worker_stop_requested"), 0, stop)
    pair = rt.object()
    rt.begin()
    assert rt.enqueue(pair) == 0 and rt.sc(0) == 0


def test_reserved_mailbox_covers_detached_raw_drain_and_stale_close_cannot_steal():
    rt = ScalarRuntime()
    first = rt.object()
    rt.begin(); rt.enqueue(first); rt.seal()
    old_token = rt.sc(11)
    def during_free(obj):
        rt.free_hook = None
        assert rt.sc(0) == 3 and not rt.nodes
        rt.thread = 3
        rt.world = 1
        rt.begin()
        second = rt.object()
        assert rt.enqueue(second) == 0  # cannot overwrite detached reservation
        rt.seal()
        rt.thread = 2
    rt.free_hook = during_free
    assert rt.drain() == 1
    rt.thread = 3; rt.world = 1
    second = rt.object()
    rt.begin(); assert rt.enqueue(second) == 1; rt.seal()
    assert rt.sc(11) > old_token
    rt.thread = 1
    rt.write(rt.global_address("pcc_gc_cms_worker_started"), 0, 3)
    assert rt.scalar_call("close") == 0  # TLS belongs to older finished batch
    assert rt.sc(0) == 2 and rt.nodes[second[0]] == second[1]
    rt.thread = 3
    assert rt.scalar_call("close") == 1  # actual producer's finite stop fallback
    assert rt.sc(7) == 1


@pytest.mark.parametrize("change", ("pin", "lease", "static", "tag", "tracked", "provenance", "identity", "granule", "lost-live"))
def test_changed_eligibility_or_failed_retire_keeps_durable_quarantine(change):
    rt = ScalarRuntime()
    obj, node = rt.object()
    rt.begin(); rt.enqueue((obj, node)); rt.seal()
    if change == "pin": rt.write(obj, 12, rt.read(obj, 12) | 64)
    if change == "tracked": rt.write(obj, 12, rt.read(obj, 12) | 2)
    if change == "lease": rt.write(node, 80, 1)
    if change == "static": rt.static.add(obj)
    if change == "tag": rt.write(obj, 8, 5)
    if change == "provenance": rt.provenance.add(obj)
    if change == "identity": rt.identities.add(obj)
    if change == "granule": rt.retirement_failures.add(obj)
    if change == "lost-live": rt.granules.remove(obj)
    assert rt.drain() == 0
    assert rt.sc(0) == 4 and rt.sc(1) == 1 and rt.sc(2) == 64 and rt.sc(9) == 1
    assert rt.nodes[obj] == node and rt.live_bytes == 0 and not rt.freed
    # Never retry an uncertain/failed transition, including shutdown drain.
    assert rt.scalar_call("drain", 1, 1, 0) == 0
    assert rt.sc(9) == 1


def test_impossible_index_removal_mismatch_fails_stop_without_physical_release():
    rt = ScalarRuntime()
    pair = rt.object()
    rt.begin(); rt.enqueue(pair); rt.seal()
    rt.remove_failure = True
    with pytest.raises(RuntimeError, match="production abort"):
        rt.drain()
    assert not rt.freed and not rt.released_nodes and rt.live_bytes == 0


@pytest.mark.parametrize("mode", ("tasks", "bytes"))
def test_fixed_object_and_byte_limits_do_not_expand_raw_caps(mode):
    rt = ScalarRuntime()
    rt.begin()
    pairs = [rt.object(64 if mode == "tasks" else 32768) for _ in range(257 if mode == "tasks" else 3)]
    admitted = 256 if mode == "tasks" else 2
    assert sum(rt.enqueue(pair) for pair in pairs) == admitted
    assert rt.sc(10) == 1
    rt.seal()
    assert rt.drain() == admitted
    assert rt.read(pairs[-1][1], 32) == 0


def test_new_allocation_head_survives_worker_unlink():
    rt = ScalarRuntime()
    first = rt.object()
    rt.begin(); rt.enqueue(first); rt.seal()
    canary = rt.object(flags=262144 | 32)
    assert rt.drain() == 1
    assert rt.scalar("pcc_gc_object_head") == canary[1]
    assert rt.read(canary[1], 16) == 0 and rt.nodes[canary[0]] == canary[1]


def test_pending_survives_actual_mark_preparation_without_recolor_or_cycle_pin_reset():
    rt = ScalarRuntime()
    obj, node = rt.object()
    rt.begin(); rt.enqueue((obj, node))
    flags = rt.read(obj, 12)
    rt.ns.update({"pcc_gc_gray_worklist_clear_stopped": lambda: None,
                  "pcc_gc_gray_count_store_release": lambda value: None})
    _load(RUNTIME / "freestanding_gc_object_root_seeding.py", rt.ns, {"pcc_gc_prepare_object_list_mark"})
    rt.ns["pcc_gc_prepare_object_list_mark"](1)
    rt.write(rt.global_address("pcc_gc_tracing_cycle_epoch"), 0, 991)
    assert rt.nodes[obj] == node and rt.read(node, 32) == -rt.sc(11)
    assert rt.read(obj, 12) == flags and rt.live_bytes == 0
    rt.seal()
    assert rt.drain() == 1


def test_freeing_guard_rejects_pending_before_free_watch_or_pointer_retirement():
    rt = ScalarRuntime()
    obj, node = rt.object()
    rt.begin(); rt.enqueue((obj, node))
    rt.ns.update({"_object_graph_lock": rt.lock, "_object_graph_unlock": rt.unlock})
    _load(RUNTIME / "py_gc_backend.py", rt.ns, {"pcc_gc_note_object_freeing", "pcc_gc_pointer_unregister"})
    rt.ns["pcc_gc_note_object_freeing"](obj)  # returns before absent free-watch globals
    assert rt.ns["pcc_gc_pointer_unregister"](obj) == -1
    assert rt.nodes[obj] == node and obj in rt.granules and not rt.events


def test_seal_is_inside_both_real_collection_world_stops():
    tree = ast.parse((RUNTIME / "py_obj.py").read_text())
    for name in ("pcc_gc_collect", "_gc_tracing_automatic_collect"):
        function = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == name)
        calls = [node for node in ast.walk(function) if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)]
        lines = {node.func.id: node.lineno for node in calls}
        assert lines["pcc_gc_scalar_sweep_seal"] < lines["pcc_resume_world"] < lines["pcc_gc_deferred_free_close"]


def test_scalar_abi_and_no_cross_owner_tls_access():
    from pcc.frontends.python.codegen.runtime_abi import FREESTANDING_GC_CROSS_OBJECT_SIGNATURES, FREESTANDING_GC_RUNTIME_GLOBALS
    for node in ast.parse(SOURCE.read_text()).body:
        if isinstance(node, ast.FunctionDef):
            assert node.name in FREESTANDING_GC_CROSS_OBJECT_SIGNATURES
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
            assert ast.literal_eval(node.value.args[0]) in FREESTANDING_GC_RUNTIME_GLOBALS
    for path in RUNTIME.glob("*.py"):
        if path != SOURCE:
            assert '"pcc_gc_scalar_sweep_producer_token"' not in path.read_text()


@pytest.mark.parametrize("change", ("missing-index", "wrong-node", "wrong-token", "wrong-object"))
def test_structural_owner_mismatch_fails_stop_before_stale_header_access(change):
    rt = ScalarRuntime()
    obj, node = rt.object()
    rt.begin(); rt.enqueue((obj, node)); rt.seal()
    if change == "missing-index": rt.nodes.pop(obj)
    if change == "wrong-node": rt.nodes[obj] = -999
    if change == "wrong-token": rt.write(node, 32, 0)
    if change == "wrong-object": rt.write(node, 0, -999)
    with pytest.raises(RuntimeError, match="production abort"):
        rt.drain()
    assert not rt.freed and not rt.released_nodes


def test_raw_only_empty_scope_closes_without_seal():
    rt = ScalarRuntime()
    rt.begin()
    assert rt.sc(0) == 1
    rt.world = 0
    assert rt.scalar_call("close") == 0 and rt.sc(0) == 0


def test_nonempty_unsealed_close_fails_closed():
    rt = ScalarRuntime()
    pair = rt.object()
    rt.begin(); rt.enqueue(pair)
    rt.world = 0
    with pytest.raises(RuntimeError, match="production abort"):
        rt.scalar_call("close")
    assert rt.sc(0) == 1 and rt.nodes[pair[0]] == pair[1] and not rt.freed


def test_full_scope_rejects_before_index_or_eligibility_probes():
    rt = ScalarRuntime()
    rt.begin()
    for _ in range(256): assert rt.enqueue(rt.object()) == 1
    rt.ns["pcc_gc_object_index_find"] = lambda obj: (_ for _ in ()).throw(AssertionError("capacity fast path probed index"))
    assert rt.enqueue((-999, -998)) == 0 and rt.sc(10) == 1


def test_actual_claim_notification_uses_producer_tls_and_matching_watch():
    rt = ScalarRuntime()
    rt.define_tls("pcc_gc_free_watch")
    rt.ns.update(_FREE_WATCH_OBJECT=0, _FREE_WATCH_NOTED=16)
    _load(RUNTIME / "py_gc_backend.py", rt.ns, {"pcc_gc_note_scalar_sweep_claim"})
    obj, node = rt.object()
    watch = rt.next_address; rt.next_address += 64
    rt.memory[watch] = {0: obj, 8: 0, 16: 0}
    rt.write(rt.global_address("pcc_gc_free_watch"), 0, watch)
    rt.begin(); rt.enqueue((obj, node))
    assert rt.read(watch, 16) == 1
    assert rt.scalar("pcc_gc_last_noted_free") == obj
    rt.seal(); assert rt.drain() == 1
    assert rt.scalar("pcc_gc_free_watch") == rt.scalar("pcc_gc_last_noted_free") == 0
    rt.thread = 1
    assert rt.read(watch, 16) == 1 and rt.scalar("pcc_gc_last_noted_free") == obj


def test_pending_raw_memory_and_capi_free_cannot_steal_task():
    rt = ScalarRuntime()
    obj, node = rt.object()
    rt.begin(); rt.enqueue((obj, node))
    rt.ns.update({"_object_graph_lock": rt.lock, "_object_graph_unlock": rt.unlock,
                  "_gc0_terminal_free_has_no_metadata": lambda obj: 0,
                  "pcc_gc_pointer_is_managed": lambda obj: int(obj in rt.managed),
                  "PyMem_Free": rt.free})
    _load(RUNTIME / "py_gc_backend.py", rt.ns, {"pcc_gc_note_object_freeing", "pcc_gc_pointer_unregister", "pcc_gc_free_object_memory"})
    _load(RUNTIME / "py_capi_memory_runtime.py", rt.ns, {"PyObject_Free"})
    rt.ns["pcc_gc_free_object_memory"](obj)
    rt.ns["PyObject_Free"](obj)
    assert rt.nodes[obj] == node and obj in rt.granules and not rt.freed
    rt.seal(); assert rt.drain() == 1


def test_claim_notification_leaves_different_object_watch_unmodified():
    rt = ScalarRuntime()
    rt.define_tls("pcc_gc_free_watch")
    rt.ns.update(_FREE_WATCH_OBJECT=0, _FREE_WATCH_NOTED=16)
    _load(RUNTIME / "py_gc_backend.py", rt.ns, {"pcc_gc_note_scalar_sweep_claim"})
    pair = rt.object()
    watch = rt.next_address; rt.next_address += 64
    rt.memory[watch] = {0: -999, 8: 0, 16: 0}
    rt.write(rt.global_address("pcc_gc_free_watch"), 0, watch)
    rt.begin(); assert rt.enqueue(pair) == 1
    assert rt.read(watch, 16) == 0 and rt.scalar("pcc_gc_last_noted_free") == pair[0]


def test_nonmoving_object_family_admission_does_not_require_moving_origin_flag():
    rt = ScalarRuntime()
    pair = rt.object(flags=1024 | 8)
    rt.begin()
    assert rt.enqueue(pair) == 1
    rt.seal()
    assert rt.drain() == 1


@pytest.mark.parametrize("backend", range(5))
def test_actual_allocation_fallback_sets_origin_bit_only_on_moving_backends(backend):
    tree = ast.parse((RUNTIME / "py_obj.py").read_text())
    allocation = next(node for node in tree.body
                      if isinstance(node, ast.FunctionDef) and node.name == "pcc_gc_alloc")
    fallback = [node for node in allocation.body if isinstance(node, ast.If)
                and any(isinstance(call, ast.Call) and isinstance(call.func, ast.Name)
                        and call.func.id == "pcc_allocator_alloc_object"
                        for call in ast.walk(node))]
    assert len(fallback) == 1
    namespace = dict(obj=0, backend=backend, stored_flags=0, size=64,
                     ptr_is_null=lambda value: int(value == 0),
                     pcc_allocator_alloc_object=lambda size: 4096,
                     memset=lambda *args: None)
    exec(compile(ast.Module(fallback, type_ignores=[]), "actual-allocation-fallback", "exec"), namespace)
    assert namespace["obj"] == 4096
    assert namespace["stored_flags"] == (262144 if backend in (3, 4) else 0)


@pytest.mark.parametrize("origin_bit", (0, 262144))
def test_raw_allocations_cannot_impersonate_object_family_with_origin_flag(origin_bit):
    rt = ScalarRuntime()
    obj, node = rt.object(flags=FLAGS | origin_bit)
    rt.granules.remove(obj)
    rt.begin()
    assert rt.enqueue((obj, node)) == 0 and rt.read(node, 32) == 0
