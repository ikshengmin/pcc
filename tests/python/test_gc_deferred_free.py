"""Execute production deferred-free and collection-owner bodies over raw cells.

These deterministic ordering/interleaving controls complement the real owned
runtime fixtures in test_gc_deferred_free_native.py. They do not prove native
threading, allocator performance or complete collector correctness.
Specification: docs/project-intent.md (five-GC lifetime equality).
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
RUNTIME = ROOT / "pcc/runtime/py"
SOURCE = RUNTIME / "freestanding_gc_deferred_free.py"
PREFIX = "pcc_gc_deferred_free_"
CANDIDATE, DEALLOCATING, MALLOC = 1024, 524288, 262144
TERMINAL = CANDIDATE | DEALLOCATING | MALLOC


def _load(path, namespace, names=None):
    tree = ast.parse(path.read_text())
    body = []
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and (names is None or node.name in names):
            node.decorator_list = []
            body.append(node)
        elif names is None and isinstance(node, (ast.Assign, ast.AnnAssign)):
            if isinstance(node.value, ast.Call):
                continue  # Externs are supplied by the raw-memory boundary.
            body.append(node)
        elif (names is None and isinstance(node, ast.Expr)
              and isinstance(node.value, ast.Call)
              and isinstance(node.value.func, ast.Name)
              and node.value.func.id.startswith("define_")):
            body.append(node)
    if names is not None:
        assert {node.name for node in body} == set(names)
    exec(compile(ast.Module(body, type_ignores=[]), str(path), "exec"), namespace)


class Runtime:
    def __init__(self, backend=1):
        self.backend = backend
        self.memory, self.globals, self.tls, self.tls_defaults = {}, {}, {}, {}
        self.thread, self.next_address = 1, 4096
        self.world, self.graph, self.no_park = 1, 0, 0
        self.managed, self.nodes, self.freed, self.noted = set(), {}, set(), []
        self.events, self.atomics, self.retirement_failures = [], [], set()
        self.live_usable, self.reuse_on_free = 0, False
        self.free_hook = None
        self.ns = {
            "i64": int,
            "define_thread_local_i32": self.define_tls,
            "define_thread_local_ptr_null": self.define_tls,
            "define_global_i64": self.define_global,
            "define_global_ptr_null": self.define_global,
            "global_addr": self.global_address,
            "global_load_ptr": lambda name: self.read(self.global_address(name), 0),
            "global_store_ptr": lambda name, value: self.write(self.global_address(name), 0, value),
            "load_i32": self.read, "load_i64": self.read, "load_ptr": self.read,
            "store_i32": self.write, "store_i64": self.write, "store_ptr": self.write,
            "atomic_load_i64": lambda p, off, order: self.read(p, off),
            "atomic_load_i32": lambda p, off, order: self.read(p, off),
            "atomic_store_i64": lambda p, off, value, order: self.write(p, off, value),
            "atomic_rmw_i64": self.rmw, "atomic_cas_i64": self.cas,
            "null": lambda: 0, "ptr_is_null": lambda p: int(p == 0),
            "is_tagged_int": lambda p: 0, "ptr_eq": lambda a, b: int(a == b),
            "free": self.free, "malloc_usable_size": lambda p: self.read(p, -16),
            "pcc_platform_abort": self.abort,
            "pcc_thread_owns_stopped_world": lambda: int(self.world > 0),
            "pcc_thread_no_park_depth": lambda: self.no_park,
            "pcc_py_gc_minor_graph_lock_depth": lambda: self.graph,
            "pcc_capi_is_cext_type_tag": lambda tag: int(tag == 10001),
            "pcc_py_gc_minor_graph_lock": self.lock,
            "pcc_py_gc_minor_graph_unlock": self.unlock,
            "pcc_gc_is_cms_worker": lambda: int(self.thread == 2),
            "pcc_current_thread_id": lambda: self.thread,
        }
        for name, value in {
            "pcc_gc_backend_selected": backend,
            "pcc_gc_backend4_no_refcounts": int(backend != 0),
            "pcc_gc_config_initialized": 1,
            "pcc_gc_cms_worker_started": 0,
            "pcc_gc_cms_worker_stop_requested": 0,
        }.items():
            self.define_global(name, value)
        self.define_tls("pcc_gc_last_noted_free")
        _load(SOURCE, self.ns)

    def lock(self):
        assert self.graph == 0
        self.graph = 1

    def unlock(self):
        assert self.graph == 1
        self.graph = 0

    def alloc(self, usable=64, flags=TERMINAL, tag=3):
        obj = self.next_address
        self.next_address += max(128, usable + 64)
        self.memory[obj] = {0: 0, 8: tag, 12: flags, -48: 17, -40: 18,
                            -32: -1, -24: usable - 3, -16: usable, -8: 16}
        self.live_usable += usable
        self.managed.add(obj)
        return obj

    def define_global(self, name, value=0):
        if name not in self.globals:
            address = self.next_address
            self.next_address += 64
            self.globals[name] = address
            self.memory[address] = {0: value}

    def define_tls(self, name, value=0):
        self.tls_defaults[name] = value

    def global_address(self, name):
        if name in self.tls_defaults:
            key = self.thread, name
            if key not in self.tls:
                address = self.next_address
                self.next_address += 64
                self.tls[key] = address
                self.memory[address] = {0: self.tls_defaults[name]}
            return self.tls[key]
        return self.globals[name]

    def scalar(self, name):
        return self.read(self.global_address(name), 0)

    def read(self, p, offset):
        assert p not in self.freed, "read after physical free"
        return self.memory[p][offset]

    def write(self, p, offset, value):
        assert p not in self.freed, "write after physical free"
        self.memory[p][offset] = value

    def rmw(self, operation, p, off, value, order):
        self.atomics.append((operation, p, value, order))
        old = self.read(p, off)
        self.write(p, off, old + value if operation == "add" else old - value)
        return old

    def cas(self, p, off, expected, desired, success, failure):
        self.atomics.append(("cas", p, desired, success))
        old = self.read(p, off)
        if old == expected:
            self.write(p, off, desired)
        return old

    def free(self, obj):
        assert obj not in self.freed, "double physical free"
        self.events.append(("free", obj, self.world, self.thread))
        self.live_usable -= self.read(obj, -16)
        if self.free_hook is not None:
            self.free_hook(obj)
        self.freed.add(obj)
        if self.reuse_on_free:
            # Any post-free read would observe a different allocator lifetime.
            self.memory[obj] = {0: -999, 8: -998, -16: -997}

    def abort(self):
        raise RuntimeError("production abort")

    def call(self, function, *args):
        return self.ns[PREFIX + function](*args)

    def counter(self, metric):
        return self.call("counter", metric)

    def install_free_boundary(self):
        def lock():
            self.graph += 1

        def unlock():
            self.graph -= 1

        def note(obj):
            self.events.append(("retire-index", obj))
            self.noted.append(obj)
            self.nodes.pop(obj, None)
            self.ns["global_store_ptr"]("pcc_gc_last_noted_free", obj)

        def unregister(obj):
            self.events.append(("retire-provenance", obj))
            if obj in self.retirement_failures:
                return -1
            self.managed.remove(obj)
            return 0

        self.ns.update({
            "_gc0_terminal_free_has_no_metadata": lambda obj: 0,
            "pcc_gc_pointer_is_managed": lambda obj: int(obj in self.managed),
            "_object_graph_lock": lock, "_object_graph_unlock": unlock,
            "_gc3_forwarded_target_may_die": lambda obj: 1,
            "pcc_gc_object_index_find": lambda obj: self.nodes.get(obj, 0),
            "pcc_gc_note_object_freeing": note,
            "pcc_gc_pointer_unregister": unregister,
            "_minor_block_containing": lambda obj: 0,
        })
        _load(RUNTIME / "py_gc_backend.py", self.ns, {"pcc_gc_free_object_memory"})
        return self.ns["pcc_gc_free_object_memory"]


@pytest.mark.parametrize("backend", range(5))
@pytest.mark.parametrize("reason", ("normal", "prior-stop", "running", "graph", "no-park", "counted"))
def test_begin_admits_only_normal_count_free_nonmoving_owner(backend, reason):
    rt = Runtime(backend)
    if reason == "running":
        rt.world = 0
    if reason == "graph":
        rt.graph = 1
    if reason == "no-park":
        rt.no_park = 1
    if reason == "counted":
        rt.write(rt.global_address("pcc_gc_backend4_no_refcounts"), 0, 0)
    expected = int(backend in (1, 2) and reason == "normal")
    assert rt.call("begin", int(reason == "prior-stop")) == expected
    if expected:
        assert rt.call("begin", 0) == 0
        rt.world = 0
        assert rt.call("close") == 0
    assert [rt.counter(i) for i in range(8)] == [0] * 8


@pytest.mark.parametrize("backend", (1, 2))
def test_intrusive_queue_preserves_allocator_words_and_frees_only_after_resume(backend):
    rt = Runtime(backend)
    objects = [rt.alloc(size) for size in (16, 128, 32768)]
    snapshots = {obj: dict(rt.memory[obj]) for obj in objects}
    assert rt.call("begin", 0) == 1
    for obj in objects:
        assert rt.call("try_enqueue", obj, TERMINAL) == 1
        assert {k: v for k, v in rt.memory[obj].items() if k != 0} == {
            k: v for k, v in snapshots[obj].items() if k != 0}
    assert [rt.counter(i) for i in range(8)] == [3, 32912, 0, 0, 0, 0, 0, 0]
    assert rt.live_usable == 32912 and not rt.events and not rt.atomics
    rt.reuse_on_free = True

    def during_free(obj):
        assert rt.scalar(PREFIX + "active") == 0
        assert rt.counter(7) == 1
        assert rt.world == rt.graph == rt.no_park == 0

    rt.free_hook = during_free
    rt.world = 0
    assert rt.call("close") == 3
    assert rt.call("close") == 0
    assert rt.events == [("free", obj, 0, 1) for obj in objects]
    assert rt.live_usable == 0
    assert [rt.counter(i) for i in range(8)] == [0, 0, 3, 3, 3, 0, 32912, 0]


@pytest.mark.parametrize("extra,remove,tag", [
    (0, CANDIDATE, 3), (0, DEALLOCATING, 3),
    (1, 0, 3), (64, 0, 3), (4096, 0, 3), (16384, 0, 3),
    (65536, 0, 3), (0, 0, 10001),
])
def test_non_candidates_and_special_lifetimes_do_not_enter_queue(extra, remove, tag):
    rt = Runtime()
    obj = rt.alloc(flags=(TERMINAL | extra) & ~remove, tag=tag)
    assert rt.call("begin", 0) == 1
    assert rt.call("try_enqueue", obj, rt.read(obj, 12)) == 0
    assert rt.counter(0) == rt.counter(1) == 0
    rt.world = 0
    rt.call("close")
    assert [rt.counter(i) for i in range(2, 7)] == [0] * 5


@pytest.mark.parametrize("shape", ("blocks", "bytes", "large"))
def test_fixed_caps_keep_original_free_fallback_and_report_coverage(shape):
    rt = Runtime()
    assert rt.call("begin", 0) == 1
    sizes = [16] * 4097 if shape == "blocks" else [524288, 524288, 16] if shape == "bytes" else [1048577]
    admitted, rejected = [], []
    for size in sizes:
        obj = rt.alloc(size)
        if rt.call("try_enqueue", obj, TERMINAL):
            admitted.append(obj)
        else:
            rejected.append(obj)
            rt.free(obj)
        assert rt.counter(0) <= 4096 and rt.counter(1) <= 1048576
    pending = rt.counter(1)
    assert len(rejected) == 1
    assert rt.live_usable == pending
    rt.world = 0
    assert rt.call("close") == len(admitted)
    assert [rt.counter(i) for i in range(2, 7)] == [len(sizes), len(admitted), len(admitted), 1, pending]
    assert rt.live_usable == 0


def test_queue_is_tls_but_completed_accounting_is_process_wide_and_i64():
    rt = Runtime()
    assert rt.call("begin", 0) == 1
    first = rt.alloc(32)
    assert rt.call("try_enqueue", first, TERMINAL) == 1
    rt.thread = 2
    assert rt.counter(0) == rt.counter(1) == 0
    assert rt.call("begin", 0) == 1
    second = rt.alloc(64)
    rt.call("try_enqueue", second, TERMINAL)
    for field in ("attempts", "enqueued", "drained", "cap_fallbacks"):
        rt.write(rt.global_address(PREFIX + field), 0, 1 << 40)
    rt.world = 0
    assert rt.call("close") == 1
    rt.thread = 1
    assert (rt.counter(0), rt.counter(1)) == (1, 32)
    assert rt.call("close") == 1
    assert [rt.counter(i) for i in range(2, 6)] == [(1 << 40) + 2] * 3 + [1 << 40]
    assert rt.counter(6) == 64


@pytest.mark.parametrize("unsafe", ("world", "graph", "no_park"))
def test_close_fails_closed_after_disabling_scope_without_losing_queued_block(unsafe):
    rt = Runtime()
    obj = rt.alloc()
    rt.call("begin", 0)
    rt.call("try_enqueue", obj, TERMINAL)
    rt.world = 0
    setattr(rt, unsafe, 1)
    with pytest.raises(RuntimeError, match="production abort"):
        rt.call("close")
    assert rt.scalar(PREFIX + "active") == 0
    assert (rt.counter(0), rt.counter(1)) == (1, 64)
    assert not rt.freed


@pytest.mark.parametrize("backend", range(5))
@pytest.mark.parametrize("retirement_fails", (False, True))
def test_actual_terminal_boundary_defers_only_after_successful_retirement(backend, retirement_fails):
    rt = Runtime(backend)
    terminal_free = rt.install_free_boundary()
    obj = rt.alloc()
    if retirement_fails:
        rt.retirement_failures.add(obj)
    began = rt.call("begin", 0)
    terminal_free(obj)
    assert rt.events[:2] == [("retire-index", obj), ("retire-provenance", obj)]
    if retirement_fails:
        assert not rt.freed and rt.counter(0) == 0
    elif began:
        assert not rt.freed and rt.counter(0) == 1 and obj not in rt.managed
        # Retired provenance must stop a second terminal call before it even
        # reads the intrusive header, whose refcount word is now queue linkage.
        before = list(rt.events)
        terminal_free(obj)
        assert rt.events == before and rt.counter(0) == 1
    else:
        assert obj in rt.freed
    if began:
        rt.world = 0
        rt.call("close")


class CollectionRuntime(Runtime):
    def __init__(self, backend=1, held=0, reenter=False):
        super().__init__(backend)
        self.world, self.owed, self.reenter = held, 1, reenter
        self.original_held = held
        self.obj = self.alloc()
        terminal_free = self.install_free_boundary()
        for name in ("pcc_gc_explicit_collect_active", "pcc_gc_tracing_finish_commits",
                     "pcc_diagnostics_runtime_log_fast_state"):
            self.define_global(name, 0)

        def stop():
            self.world += 1
            self.events.append(("stop", self.world))
            return 0

        def resume():
            self.world -= 1
            self.events.append(("resume", self.world))
            return 0

        def collect():
            stop()  # The real low-level collection's nested stop/resume.
            terminal_free(self.obj)
            self.owed = 0
            resume()
            assert self.world > 0
            return 1

        def callback(phase, length):
            self.events.append((phase, self.world, self.counter(0)))
            if phase == "start" and self.reenter:
                self.reenter = False
                self.ns["pcc_gc_collect"](0)
            if phase == "stop":
                assert self.counter(0) == 0
                assert self.world == self.original_held

        self.ns.update({
            "_gc_backend_fast": lambda: self.backend,
            "_pcc_gc_fire_callbacks": callback, "cstr": lambda text: text,
            "pcc_stop_the_world": stop, "pcc_resume_world": resume,
            "pcc_thread_safepoint": lambda: None,
            "pcc_runtime_monotonic_us": lambda: 100,
            "pcc_gc_reclaim_retired_index_tables": lambda: None,
            "pcc_gc_begin_explicit_tracing_collect": lambda: self.write(self.global_address("pcc_gc_explicit_collect_active"), 0, 1),
            "pcc_gc_end_explicit_tracing_collect": lambda: self.write(self.global_address("pcc_gc_explicit_collect_active"), 0, 0),
            "pcc_gc_sweep_owed": lambda: self.owed,
            "pcc_gc_step": lambda budget: 0,
            "pcc_gc_collect_tracing": collect,
            "pcc_gc_precise_stack_diff_if_requested": lambda: None,
            "pcc_gc_record_collection_stw_pause": lambda a, b: self.events.append(("pause", self.world)),
            "pcc_gc_external_resource_poll": lambda: self.events.append(("poll", self.world, self.counter(0))),
        })
        _load(RUNTIME / "py_obj.py", self.ns, {"_gc_tracing_automatic_collect", "pcc_gc_collect"})


@pytest.mark.parametrize("entry", ("pcc_gc_collect", "_gc_tracing_automatic_collect"))
@pytest.mark.parametrize("backend", (1, 2, 3, 4))
@pytest.mark.parametrize("held", (0, 1))
def test_real_outer_owner_drains_after_actual_resume_before_poll_callback_return(entry, backend, held):
    rt = CollectionRuntime(backend, held)
    assert rt.ns[entry](0) == 1
    free_event = next(row for row in rt.events if row[0] == "free")
    if backend in (1, 2) and held == 0:
        assert free_event[2] == 0
        assert rt.counter(3) == rt.counter(4) == 1
        assert rt.events.index(("resume", 0)) < rt.events.index(free_event)
    else:
        assert free_event[2] == held + 2
        assert rt.counter(3) == 0
    assert rt.events.index(free_event) < rt.events.index(("poll", held, 0))
    assert rt.counter(0) == rt.counter(1) == 0 and rt.world == held


def test_start_callback_reentry_finishes_before_outer_scope_begins():
    rt = CollectionRuntime(reenter=True)
    assert rt.ns["pcc_gc_collect"](0) == 0
    assert rt.counter(3) == rt.counter(4) == 1
    assert len([row for row in rt.events if row[0] == "free"]) == 1
    assert all(row[2] == 0 for row in rt.events
               if len(row) == 3 and row[0] in ("start", "stop", "poll"))


def test_deferred_free_abi_owner_storage_and_bounded_signature_chunks():
    from pcc.frontends.python.codegen.runtime_abi import (
        FREESTANDING_GC_CROSS_OBJECT_SIGNATURES,
        FREESTANDING_GC_I64_GLOBALS,
        FREESTANDING_GC_PTR_GLOBALS,
        FREESTANDING_GC_THREAD_LOCAL_GLOBALS,
    )
    from pcc.frontends.python.owned_runtime_build import runtime_modules

    signatures = {
        "begin": (("c_int64",), "c_int64"),
        "try_enqueue": (("c_ptr", "c_int64"), "c_int64"),
        "close": ((), "c_int64"), "counter": (("c_int64",), "c_int64"),
        "worker_drain": (("c_int64",), "c_int64"),
        "drain_raw_chain": (("c_ptr", "c_int64", "c_int64"), "c_void"),
    }
    for name, signature in signatures.items():
        assert FREESTANDING_GC_CROSS_OBJECT_SIGNATURES[PREFIX + name] == signature
    globals_seen, tls_seen = set(), set()
    for node in ast.parse(SOURCE.read_text()).body:
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
            call = node.value
            if isinstance(call.func, ast.Name) and call.func.id.startswith("define_"):
                name = ast.literal_eval(call.args[0])
                (tls_seen if "thread_local" in call.func.id else globals_seen).add(name)
    assert tls_seen <= FREESTANDING_GC_THREAD_LOCAL_GLOBALS
    assert globals_seen <= FREESTANDING_GC_I64_GLOBALS | FREESTANDING_GC_PTR_GLOBALS
    assert not globals_seen & FREESTANDING_GC_THREAD_LOCAL_GLOBALS
    modules = runtime_modules(str(ROOT / "pcc/runtime"), "x86_64-unknown-linux-gnu", threads=True)
    assert modules.count(SOURCE.stem) == 1
    for node in ast.parse((ROOT / "pcc/frontends/python/codegen/runtime_abi.py").read_text()).body:
        if isinstance(node, ast.FunctionDef) and node.name.startswith("_cross_object_signatures_part_"):
            literals = [part for part in ast.walk(node) if isinstance(part, ast.Dict)]
            assert len(literals) == 1 and len(literals[0].keys) <= 50
    # No other compilation owner may access the new TLS symbols directly.
    for path in RUNTIME.glob("*.py"):
        if path == SOURCE:
            continue
        text = path.read_text()
        assert not any('"' + name + '"' in text for name in tls_seen), path


def test_owned_allocator_usable_query_requires_exact_raw_signature():
    from pcc.frontends.python.pipeline_freestanding import freestanding_gc_cross_object_runtime_imports
    declaration = 'query = extern("malloc_usable_size", (c_ptr,), c_int64)'
    assert freestanding_gc_cross_object_runtime_imports(declaration) == {"malloc_usable_size"}
    assert not freestanding_gc_cross_object_runtime_imports(declaration.replace("(c_ptr,)", "(c_int64,)"))
    assert not freestanding_gc_cross_object_runtime_imports(declaration.replace("c_int64)", "c_ptr)"))


def _publish(rt, sizes=(32, 64)):
    rt.world = 1
    assert rt.call("begin", 0) == 1
    objects = [rt.alloc(size) for size in sizes]
    for obj in objects:
        assert rt.call("try_enqueue", obj, TERMINAL) == 1
    rt.world = 0
    assert rt.call("close") == len(objects)
    return objects


def test_complete_batch_handoff_is_exclusive_and_asynchronously_freed():
    rt = Runtime()
    rt.write(rt.global_address("pcc_gc_cms_worker_started"), 0, 1)
    objects = _publish(rt)
    assert not rt.freed and (rt.counter(0), rt.counter(1)) == (0, 0)
    assert [rt.counter(i) for i in (3, 4, 8, 9, 10, 11, 12)] == [2, 0, 2, 0, 0, 2, 96]
    # A mutator cannot masquerade as the production consumer.
    assert rt.call("worker_drain", 0) == 0 and not rt.freed
    rt.thread = 2
    rt.reuse_on_free = True
    def during_free(obj):
        assert rt.graph == 0 and rt.counter(7) == 1
        assert (rt.counter(11), rt.counter(12)) == (2, 96)
        assert rt.scalar(PREFIX + "worker_head") == 0
    rt.free_hook = during_free
    assert rt.call("worker_drain", 0) == 2
    assert rt.call("worker_drain", 0) == 0
    assert rt.events == [("free", obj, 0, 2) for obj in objects]
    assert rt.live_usable == 0
    assert [rt.counter(i) for i in (3, 4, 8, 9, 10, 11, 12, 13)] == [2, 2, 2, 2, 0, 0, 0, 2]


@pytest.mark.parametrize("state,stop", [(0, 0), (2, 0), (3, 0), (3, 1), (4, 1), (1, 1)])
def test_unavailable_starting_stopping_or_quarantined_worker_falls_back(state, stop):
    rt = Runtime()
    rt.write(rt.global_address("pcc_gc_cms_worker_started"), 0, state)
    rt.write(rt.global_address("pcc_gc_cms_worker_stop_requested"), 0, stop)
    objects = _publish(rt)
    assert rt.events == [("free", obj, 0, 1) for obj in objects]
    assert [rt.counter(i) for i in (3, 4, 8, 9, 10, 11, 12)] == [2, 2, 0, 0, 2, 0, 0]


def test_busy_mailbox_including_worker_private_chain_forces_bounded_local_assist():
    rt = Runtime()
    rt.write(rt.global_address("pcc_gc_cms_worker_started"), 0, 1)
    first = _publish(rt)
    second = _publish(rt, (128,))
    assert rt.freed == set(second)
    assert rt.counter(11) == 2 and rt.counter(12) == 96
    rt.thread = 2
    later = []
    def during_free(obj):
        rt.free_hook = None
        # Simulate a producer while the CMS worker owns the detached chain.
        assert rt.scalar(PREFIX + "worker_head") == 0 and rt.counter(11) == 2
        rt.thread = 1
        later.extend(_publish(rt, (256,)))
        rt.thread = 2
        assert rt.counter(11) == 2 and rt.counter(12) == 96
    rt.free_hook = during_free
    assert rt.call("worker_drain", 0) == 2
    assert rt.freed == set(first + second + later)
    assert [rt.counter(i) for i in (3, 4, 8, 9, 10, 11)] == [4, 4, 2, 2, 2, 0]


def _install_worker_loop(rt):
    for name, value in {
        "pcc_gc_cms_queue_pushes": 0, "pcc_gc_cms_worker_drains": 0,
        "pcc_gc_mark_active": 0, "py_gc_threshold0": 700, "py_gc_enabled": 1,
        "pcc_gc_gray_worklist_flush_requested": 0, "pcc_gc_backend4_auto_step_pending": 0,
        "pcc_gc_cms_sweep_pending": 0, "pcc_gc_cms_worker_stops": 0,
    }.items():
        rt.define_global(name, value)
    rt.define_tls("pcc_gc_cms_worker_current")
    rt.ns.update({
        "pcc_gc_is_cms_worker": lambda: rt.scalar("pcc_gc_cms_worker_current"),
        "atomic_store_i32": lambda p, off, value, order: rt.write(p, off, value),
        "atomic_rmw_i32": rt.rmw,
        "pcc_platform_sleep_ns": lambda ns: 0,
        "pcc_thread_safepoint_gate": lambda: rt.write(rt.global_address("pcc_gc_cms_worker_stop_requested"), 0, 1),
    })
    _load(RUNTIME / "freestanding_gc_public_collection.py", rt.ns, {"pcc_gc_cms_worker_main_py"})


@pytest.mark.parametrize("pause", ("disabled", "zero-threshold", "stop-before-loop"))
def test_real_worker_loop_drains_terminal_blocks_despite_disabled_marking_or_stop(pause):
    rt = Runtime()
    _install_worker_loop(rt)
    rt.write(rt.global_address("pcc_gc_cms_worker_started"), 0, 1)
    objects = _publish(rt)
    if pause == "disabled":
        rt.write(rt.global_address("py_gc_enabled"), 0, 0)
    elif pause == "zero-threshold":
        rt.write(rt.global_address("py_gc_threshold0"), 0, 0)
    else:
        rt.write(rt.global_address("pcc_gc_cms_worker_started"), 0, 3)
        rt.write(rt.global_address("pcc_gc_cms_worker_stop_requested"), 0, 1)
    rt.thread = 2
    assert rt.ns["pcc_gc_cms_worker_main_py"](0) == 0
    assert rt.freed == set(objects) and rt.counter(9) == 2 and rt.counter(11) == 0
    assert rt.scalar("pcc_gc_cms_worker_current") == 0
    assert rt.scalar("pcc_gc_cms_worker_stops") == 1


def test_final_empty_drain_always_acquires_the_publication_lock():
    rt = Runtime()
    rt.world = 0
    rt.thread = 2
    locks = []
    original = rt.ns["pcc_py_gc_minor_graph_lock"]
    rt.ns["pcc_py_gc_minor_graph_lock"] = lambda: (locks.append(True), original())
    assert rt.call("worker_drain", 0) == 0 and locks == []
    assert rt.call("worker_drain", 1) == 0 and locks == [True]


def test_stop_after_admission_cannot_escape_before_late_publication():
    # Real host threads schedule production bodies at the exact read/lock
    # boundaries. This is an interleaving model, not native runtime evidence.
    import threading
    class ConcurrentRuntime(Runtime):
        @property
        def thread(self):
            return getattr(self.local, "thread", 1)
        @thread.setter
        def thread(self, value):
            if not hasattr(self, "local"):
                self.local = threading.local()
            self.local.thread = value
        @property
        def graph(self):
            return getattr(self.local, "graph", 0)
        @graph.setter
        def graph(self, value):
            self.local.graph = value
    rt = ConcurrentRuntime()
    _install_worker_loop(rt)
    mutex = threading.Lock()
    admitted, finish_attempted, publish = threading.Event(), threading.Event(), threading.Event()
    errors = []
    def lock():
        if rt.thread == 2:
            finish_attempted.set()
        assert mutex.acquire(timeout=2)
        rt.graph = 1
    def unlock():
        rt.graph = 0
        mutex.release()
    rt.ns["pcc_py_gc_minor_graph_lock"] = lock
    rt.ns["pcc_py_gc_minor_graph_unlock"] = unlock
    rt.write(rt.global_address("pcc_gc_cms_worker_started"), 0, 1)
    stop_slot = rt.global_address("pcc_gc_cms_worker_stop_requested")
    def load(p, off, order):
        value = rt.read(p, off)
        if rt.thread == 1 and p == stop_slot and rt.graph:
            admitted.set()
            assert publish.wait(2)
        return value
    rt.ns["atomic_load_i32"] = load
    obj = rt.alloc()
    rt.call("begin", 0)
    rt.call("try_enqueue", obj, TERMINAL)
    rt.world = 0
    def producer():
        try:
            # Scope belongs to logical producer 1 in the model's TLS map.
            rt.thread = 1
            assert rt.call("close") == 1
        except BaseException as exc:
            errors.append(exc)
    def worker():
        try:
            rt.thread = 2
            rt.ns["pcc_gc_cms_worker_main_py"](0)
        except BaseException as exc:
            errors.append(exc)
    a = threading.Thread(target=producer)
    b = threading.Thread(target=worker)
    a.start()
    assert admitted.wait(2)
    rt.write(rt.global_address("pcc_gc_cms_worker_started"), 0, 3)
    rt.write(stop_slot, 0, 1)
    b.start()
    try:
        assert finish_attempted.wait(2), "final drain skipped the empty publication barrier"
        assert not rt.freed and rt.counter(11) == 0
    finally:
        publish.set()
        a.join(2)
        b.join(2)
    assert not a.is_alive() and not b.is_alive() and not errors
    assert rt.freed == {obj} and rt.counter(9) == 1 and rt.counter(11) == 0
    assert rt.scalar("pcc_gc_cms_worker_stops") == 1


def test_every_freestanding_helper_has_a_native_abi_export():
    for node in ast.parse(SOURCE.read_text()).body:
        if isinstance(node, ast.FunctionDef):
            assert any(isinstance(decorator, ast.Call)
                       and isinstance(decorator.func, ast.Name)
                       and decorator.func.id == "c_abi_export"
                       for decorator in node.decorator_list), node.name
