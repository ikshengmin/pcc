"""Execute production list-store bodies against contended state."""
import ast
from pathlib import Path

import pytest


class StoreModel:
    def __init__(self, backend, threads, shrink=False, reject=False):
        self.memory = {1016: 1, 1032: 2000, 2000: 7000}
        self.serial = 10000
        self.depth = 0
        self.calls = []
        self.shrink = shrink
        self.reject = reject
        self.ns = {
            "int": int, "_list_is_sane": lambda *args: True,
            "_list_type_ok": lambda *args: True, "_list_shape_ok": lambda *args: True,
            "pcc_gc_backend": lambda: backend,
            "pcc_threads_enabled": lambda: threads,
            "PYLISTOBJECT_LENGTH_OFFSET": 16, "PYLISTOBJECT_ITEMS_OFFSET": 32,
            "stack_alloc": self.allocate, "load_i64": self.load,
            "load_ptr": self.load, "store_ptr": self.store,
            "ptr_add": lambda p, n: p + n,
            "_prepare_moving_root": self.prepare,
            "_reload_moving_root": lambda slot, handle: self.load(slot, 0),
            "_finish_moving_root": lambda handle: self.calls.append(("root-finish",)),
            "pcc_gc_store_ptr_plan_init": self.initialize,
            "pcc_py_gc_minor_graph_lock": self.lock,
            "pcc_py_gc_minor_graph_unlock": self.unlock,
            # The list's transaction lock (container or graph lock).
            "_list_lock": lambda _list: self.lock(),
            "_list_unlock": lambda _list: self.unlock(),
            "pcc_gc_store_ptr_plan_commit_locked": self.commit,
            "pcc_gc_store_ptr_plan_finish": self.finish,
            "pcc_gc_store_ptr": self.unlocked_store,
        }
        source = Path(__file__).resolve().parents[2] / "pcc/runtime/py/py_list.py"
        wanted = {"_list_set_item_transaction", "_normalize_index"}
        bodies = [node for node in ast.parse(source.read_text()).body
                  if isinstance(node, ast.FunctionDef) and node.name in wanted]
        assert len(bodies) == 2
        exec(compile(ast.Module(bodies, type_ignores=[]), str(source), "exec"), self.ns)

    def allocate(self, size):
        self.serial += size
        return self.serial

    def load(self, pointer, offset):
        return self.memory.get(pointer + offset, 0)

    def store(self, pointer, offset, value):
        self.memory[pointer + offset] = value

    def prepare(self, slot, handle):
        self.store(handle, 0, 0)
        return 0

    def initialize(self, plan, owner, backend):
        assert self.depth == 0 and owner == 1000
        self.calls.append(("init", backend))

    def lock(self):
        assert self.depth == 0
        self.depth = 1
        # Contended acquisition observes the newly published backing array.
        self.memory[1032] = 3000
        self.memory[1016] = 0 if self.shrink else 2
        self.calls.append(("lock",))

    def unlock(self):
        assert self.depth == 1
        self.depth = 0
        self.calls.append(("unlock",))

    def commit(self, plan, owner, slot, value):
        assert self.depth == 1 and owner == 1000 and value == 5000
        assert slot == 3008  # -1 normalized against the post-lock length.
        self.calls.append(("commit", slot))
        if not self.reject:
            self.store(slot, 0, value)
        return int(not self.reject)

    def finish(self, plan):
        assert self.depth == 0
        self.calls.append(("finish",))

    def unlocked_store(self, owner, slot, value):
        assert self.depth == 0
        self.calls.append(("unlocked", slot))
        self.store(slot, 0, value)

    def run(self):
        status = self.ns["_list_set_item_transaction"](1000, -1, 5000)
        assert self.depth == 0
        return status


@pytest.mark.parametrize("backend", range(5))
def test_threaded_store_revalidates_and_commits_under_graph_lock(backend):
    model = StoreModel(backend, 1)
    assert model.run() == 0
    assert model.memory[3008] == 5000
    assert ("unlocked", 2000) not in model.calls
    assert model.calls.index(("unlock",)) < model.calls.index(("finish",))


def test_threads_off_gc0_keeps_existing_nonparking_store():
    model = StoreModel(0, 0)
    assert model.run() == 0
    assert model.calls == [("unlocked", 2000)]
    assert model.memory[2000] == 5000


@pytest.mark.parametrize("shrink,reject", [(True, False), (False, True)])
def test_threaded_gc0_failure_finishes_only_after_unlock(shrink, reject):
    model = StoreModel(0, 1, shrink=shrink, reject=reject)
    assert model.run() == -1
    assert model.calls.index(("unlock",)) < model.calls.index(("finish",))
    assert sum(event[0] == "finish" for event in model.calls) == 1
    if shrink:
        assert not any(event[0] == "commit" for event in model.calls)
