"""Execute the actual backend setter across completion of startup config.

Provider doubles model the pause/graph boundaries. This establishes the
branch/state contract, not native scheduling or weak-memory behavior.
"""
import ast
import os
import threading
from pathlib import Path

import pytest

SOURCE = Path(os.environ.get("PCC_SETTER_SOURCE", str(Path(__file__).resolve().parents[2] / "pcc/runtime/py/py_gc_backend.py")))
SELECTED = "pcc_gc_backend_selected"
NO_RC = "pcc_gc_backend4_no_refcounts"
WORKER = "pcc_gc_cms_worker_started"


class Setter:
    def __init__(self, *, initial=0, published=4, threads=1, frozen=0, freeze_on_graph=False):
        self.words = {SELECTED: initial, NO_RC: int(initial != 0), "pcc_gc_config_initialized": 1,
                      "pcc_gc_backend_frozen": frozen, "pcc_gc_refcount_atomic": int(initial == 0),
                      "pcc_gc_refcount_fast": int(initial == 0 and threads == 0)}
        self.events = []
        self.depth = 0
        self.stop_count = 0
        self.release_count = 0
        self.freeze_count = 0
        self.thaw_count = 0
        self.injected = False
        self.published = published
        self.threads = threads
        self.freeze_on_graph = freeze_on_graph
        self.snapshots = []
        self.ns = {
            "global_addr": lambda name: name,
            "global_load_ptr": lambda name: self.load(name, 0),
            "global_store_ptr": lambda name, value: self.store(name, 0, value),
            "load_i32": self.load, "load_i64": self.load, "load_ptr": self.load,
            "atomic_load_i32": self.load, "atomic_load_i64": self.load,
            "store_i32": self.store, "store_i64": self.store, "store_ptr": self.store,
            "atomic_store_i32": self.store, "atomic_store_i64": self.store,
            "atomic_cas_i64": self.cas,
            "null": lambda: 0, "ptr_is_null": lambda value: int(value == 0),
            "_init_config": lambda: self.words[SELECTED],
            "pcc_thread_enable_conservative_roots": lambda: self.events.append("enable-conservative"),
            "pcc_gc_tracking_enter_flag_mode": lambda: self.events.append("tracking-flag-mode"),
            # Count-free GC1/GC2 also fix allocation staging in the freeze.
            "pcc_gc_alloc_stage_enable": lambda enabled: self.events.append("alloc-stage"),
            "pcc_threads_enabled": lambda: threads,
            "pcc_py_gc_minor_graph_lock_depth": lambda: self.depth,
            "pcc_thread_no_park_depth": lambda: 0,
            "pcc_thread_owns_stopped_world": lambda: 0,
            "pcc_thread_live_count": lambda: 1,
            "_is_cms_worker": lambda: 0,
            "_stop_cms_worker": self.stop,
            "_maybe_start_cms_worker": lambda: self.events.append("restart"),
            "_object_graph_lock": self.lock, "_object_graph_unlock": self.unlock,
            "_forwarding_head": lambda: 0, "_object_head": lambda: 0,
            "_mode_leases_freeze": self.freeze, "_mode_leases_thaw": self.thaw,
            "_tracing_cycle_epoch_advance_unlocked": lambda: 1,
            "_publish_frame_registry_inactive": lambda: None,
            "_set_gray_count": lambda count: None,
            "_backend_uses_forwarding": lambda: int(self.words[SELECTED] in (3, 4)),
            "_clear_object_list": lambda: self.events.append("clear-objects"),
            "_forwarding_clear_all": lambda: self.events.append("clear-forwarding"),
            "_identity_clear_all": lambda: None,
            "pcc_gc_reset_relocation_set": lambda: None,
            "_backend4_store_buffer_clear": lambda: None,
            "pcc_gc_refresh_read_barrier": lambda: None,
        }
        names = {"pcc_gc_set_backend", "pcc_gc_fix_backend", "_release_cms_worker_pause",
                 "_release_cms_worker_pause_unless_kept", "_nonmoving_count_free_backend"}
        tree = ast.parse(SOURCE.read_text(), filename=str(SOURCE))
        functions = [node for node in tree.body if isinstance(node, ast.FunctionDef)
                     and node.name in names]
        assert {node.name for node in functions} == names
        for node in functions:
            node.decorator_list = []
        exec(compile(ast.Module(body=functions, type_ignores=[]), str(SOURCE), "exec"), self.ns)

    def load(self, base, offset=0, *order):
        return self.words.get((base, offset), self.words.get(base, 0) if offset == 0 else 0)

    def store(self, base, offset, value, *order):
        if base == WORKER and offset == 0 and value == 0 and self.load(WORKER) == 4:
            self.release_count += 1
        if offset == 0:
            self.words[base] = value
        else:
            self.words[base, offset] = value
        self.events.append(("store", base, value))

    def cas(self, base, offset, expected, value, *order):
        old = self.load(base, offset)
        if old == expected:
            self.store(base, offset, value)
        return old

    def stop(self):
        self.stop_count += 1
        self.store(WORKER, 0, 4)
        return 0

    def lock(self):
        assert self.depth == 0
        if not self.injected:
            # The setter passed its early mode check and may already own the
            # worker pause. Config then publishes before it acquires graph.
            self.words.update({SELECTED: self.published, NO_RC: int(self.published != 0),
                               "pcc_gc_refcount_atomic": int(self.published == 0),
                               "pcc_gc_refcount_fast": int(self.published == 0 and self.threads == 0)})
            if self.freeze_on_graph:
                self.words["pcc_gc_backend_frozen"] = 1
            self.events.append("config-completed-before-graph")
            self.injected = True
        self.depth = 1

    def unlock(self):
        assert self.depth == 1
        self.snapshots.append(dict(self.words))
        self.depth = 0

    def freeze(self):
        assert self.depth == 1
        self.freeze_count += 1
        return 0

    def thaw(self):
        assert self.depth == 1
        self.thaw_count += 1

    def call(self, backend):
        return self.ns["pcc_gc_set_backend"](backend)

    def assert_balanced(self):
        assert self.depth == 0
        assert self.stop_count == self.release_count
        assert self.load(WORKER) == 0
        assert self.freeze_count == self.thaw_count
        assert self.load("pcc_gc_foreign_lease_active") == 0




@pytest.mark.parametrize("published", range(5))
@pytest.mark.parametrize("backend", range(5))
def test_preallocation_choice_derives_ownership_after_config_publication(published, backend):
    model = Setter(published=published)
    assert model.call(backend) == 0
    assert model.words[SELECTED] == backend
    assert model.words[NO_RC] == int(backend != 0)
    assert model.words["pcc_gc_backend_frozen"] == 0
    assert model.words["pcc_gc_refcount_atomic"] == int(backend == 0)
    assert model.freeze_count == model.thaw_count == 1
    model.assert_balanced()


@pytest.mark.parametrize("published", range(5))
@pytest.mark.parametrize("threads", (0, 1))
def test_allocation_freeze_wins_between_early_check_and_graph(published, threads):
    requested = (published + 1) % 5
    model = Setter(published=published, threads=threads, freeze_on_graph=True)
    assert model.call(requested) == -1
    assert model.words[SELECTED] == published
    assert model.words[NO_RC] == int(published != 0)
    assert model.words["pcc_gc_backend_frozen"] == 1
    assert model.freeze_count == 0 and "clear-objects" not in model.events
    model.assert_balanced()


@pytest.mark.parametrize("backend", range(5))
@pytest.mark.parametrize("threads", (0, 1))
def test_same_backend_reset_preserves_default_policy_after_allocation(backend, threads):
    model = Setter(initial=backend, published=backend, threads=threads, frozen=1)
    assert model.call(backend) == 0
    assert model.words[SELECTED] == backend and model.words[NO_RC] == int(backend != 0)
    assert model.freeze_count == model.thaw_count == 1
    model.assert_balanced()


@pytest.mark.parametrize("initial", (1, 2))
def test_kept_worker_reset_rejection_does_not_release_unowned_pause(initial):
    model = Setter(initial=initial)
    assert model.call(initial) == -1
    assert model.words[SELECTED] == 4 and model.words[NO_RC] == 1
    assert model.stop_count == model.release_count == model.freeze_count == 0
    model.assert_balanced()


@pytest.mark.parametrize("initial,backend", [(0, 1), (0, 2), (0, 3), (0, 4), (1, 0), (4, 0)])
@pytest.mark.parametrize("threads", (0, 1))
def test_fast_count_flags_commit_with_ownership_before_graph_unlock(initial, backend, threads):
    model = Setter(initial=initial, published=initial, threads=threads)
    assert model.call(backend) == 0
    published = next(row for row in model.snapshots if row[SELECTED] == backend)
    assert published[NO_RC] == int(backend != 0)
    assert published["pcc_gc_refcount_atomic"] == int(backend == 0)
    assert published["pcc_gc_refcount_fast"] == int(backend == 0 and threads == 0)
    model.assert_balanced()


class FreezeRace(Setter):
    """The actual freeze body must serialize with the setter's commit lock."""
    def __init__(self):
        self.local = threading.local()
        self.graph = threading.RLock()
        self.start = threading.Event()
        self.attempted_or_frozen = threading.Event()
        self.finished = threading.Event()
        self.frozen_backend = None
        self.failure = []
        super().__init__(published=0)
        self.injected = True
        self.ns['_tracing_cycle_epoch_advance_unlocked'] = self.advance

    @property
    def depth(self):
        return getattr(self.local, 'depth', 0)

    @depth.setter
    def depth(self, value):
        self.local.depth = value

    def lock(self):
        if threading.current_thread().name == 'allocation-freezer':
            self.attempted_or_frozen.set()
        self.graph.acquire()
        self.depth += 1

    def unlock(self):
        assert self.depth > 0
        self.snapshots.append(dict(self.words))
        self.depth -= 1
        self.graph.release()

    def store(self, base, offset, value, *order):
        if base == 'pcc_gc_backend_frozen' and value == 1:
            self.frozen_backend = self.words[SELECTED]
            self.attempted_or_frozen.set()
        super().store(base, offset, value, *order)

    def advance(self):
        assert self.depth == 1
        self.start.set()
        assert self.attempted_or_frozen.wait(2), 'allocator did not reach the freeze boundary'
        return 1

    def allocate(self):
        try:
            assert self.start.wait(2)
            self.ns['pcc_gc_fix_backend']()
        except BaseException as exc:
            self.failure.append(exc)
        finally:
            self.finished.set()


@pytest.mark.parametrize('backend', [1, 2])
def test_actual_freeze_cannot_publish_old_policy_inside_setter_graph_commit(backend):
    model = FreezeRace()
    thread = threading.Thread(target=model.allocate, name='allocation-freezer')
    thread.start()
    try:
        result = model.call(backend)
        assert model.finished.wait(2)
    finally:
        thread.join(timeout=2)
    assert not thread.is_alive() and not model.failure
    assert result == 0
    assert model.frozen_backend == backend == model.words[SELECTED]
    assert model.words[NO_RC] == 1 and model.words['pcc_gc_backend_frozen'] == 1
    model.assert_balanced()
