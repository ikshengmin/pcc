"""Execute the actual backend setter across completion of startup config.

Provider doubles model the pause/graph boundaries. This establishes the
branch/state contract, not native scheduling or weak-memory behavior.
"""
import ast
import os
from pathlib import Path

import pytest

SOURCE = Path(os.environ.get("PCC_SETTER_SOURCE", str(Path(__file__).resolve().parents[2] / "pcc/runtime/py/py_gc_backend.py")))
SELECTED = "pcc_gc_backend_selected"
NO_RC = "pcc_gc_backend4_no_refcounts"
WORKER = "pcc_gc_cms_worker_started"


class Setter:
    def __init__(self, *, initial=0, published=4, no_rc=1, threads=1):
        self.words = {SELECTED: initial, NO_RC: 0, "pcc_gc_config_initialized": 1,
                      "pcc_gc_config_ready": 0, "pcc_gc_backend_frozen": 0}
        self.events = []
        self.depth = 0
        self.stop_count = 0
        self.release_count = 0
        self.freeze_count = 0
        self.thaw_count = 0
        self.injected = False
        self.published = published
        self.no_rc = no_rc
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
        names = {"pcc_gc_set_backend", "_release_cms_worker_pause",
                 "_release_cms_worker_pause_unless_kept"}
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
            self.words.update({SELECTED: self.published, NO_RC: self.no_rc,
                               "pcc_gc_config_ready": 1,
                               "pcc_gc_scheduler_root_storage_mode": 2 if self.no_rc and self.published == 4 else 1})
            self.events.append("config-completed-before-graph")
            self.injected = True
        self.depth = 1

    def unlock(self):
        assert self.depth == 1
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


@pytest.mark.parametrize("backend", (0, 1, 2, 3))
@pytest.mark.parametrize("threads", (0, 1))
def test_config_published_norc4_refuses_racing_different_backend(backend, threads):
    model = Setter(threads=threads)
    assert model.call(backend) == -1
    assert model.injected
    assert model.words[SELECTED] == 4 and model.words[NO_RC] == 1
    assert model.words["pcc_gc_backend_frozen"] == 0
    assert model.words["pcc_gc_scheduler_root_storage_mode"] == 2
    assert model.stop_count == 1 and model.freeze_count == 0
    assert "clear-objects" not in model.events
    model.assert_balanced()


@pytest.mark.parametrize("threads", (0, 1))
def test_same_norc4_backend_remains_resettable(threads):
    model = Setter(threads=threads)
    assert model.call(4) == 0
    assert model.words[SELECTED] == 4 and model.words[NO_RC] == 1
    assert model.words["pcc_gc_scheduler_root_storage_mode"] == 2
    assert model.stop_count == model.freeze_count == 1
    model.assert_balanced()


@pytest.mark.parametrize("backend", (0, 1, 2, 3))
def test_counted_preallocation_backend_choice_remains_legal(backend):
    model = Setter(no_rc=0)
    assert model.call(backend) == 0
    assert model.words[SELECTED] == backend and model.words[NO_RC] == 0
    assert model.freeze_count == 1
    model.assert_balanced()


@pytest.mark.parametrize("initial", (1, 2))
def test_kept_worker_reset_rejection_does_not_release_unowned_pause(initial):
    model = Setter(initial=initial)
    assert model.call(initial) == -1
    assert model.words[SELECTED] == 4 and model.words[NO_RC] == 1
    assert model.stop_count == model.release_count == model.freeze_count == 0
    model.assert_balanced()
