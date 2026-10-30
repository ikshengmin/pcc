"""Host fault/concurrency model for actual singleton factory source.

No runtime collector is executed. Each owner/lease transition is modeled and
asserted; native five-GC proof remains separate.
"""
import ast
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import threading

import pytest

SOURCE = Path(__file__).resolve().parents[2] / "pcc/runtime/py/py_re_match_class.py"


class Model:
    def __init__(self, failure=None, barrier=None):
        self.failure = failure
        self.barrier = barrier
        self.slots = {"pcc_re_match_class": 0, "pcc_re_match_class_root_handle": 0,
                      "pcc_re_match_class_mutex_bits": 0, 101: 0, 102: 0}
        self.objects = {}
        self.next_object = 1000
        self.tokens = {}
        self.next_token = 1
        self.roots = {101, 102}
        self.mutexes = {}
        self.next_mutex = 2000
        self.atomic_lock = threading.Lock()
        self.local = threading.local()
        self.allocations = []
        self.freed = []
        self.register_count = 0
        self.namespace = {
            "c_ptr": int,
            "PYOBJECTHEADER_FLAGS_OFFSET": 12, "PY_FLAG_IMMORTAL": 1,
            "cstr": lambda value: value, "null": lambda: 0,
            "ptr_is_null": lambda value: int(value == 0),
            "ptr_to_int": lambda value: value, "int_to_ptr": lambda value: value,
            "global_addr": lambda name: name,
            "global_store_ptr": lambda name, value: self.slots.__setitem__(name, value),
            "load_ptr": lambda slot, offset: self.slots[slot],
            "store_ptr": lambda slot, offset, value: self.slots.__setitem__(slot, value),
            "atomic_load_i64": lambda slot, offset, order: self.slots[slot],
            "atomic_cas_i64": self.cas, "atomic_rmw_i32": self.immortalize,
            "pcc_mutex_new": self.mutex_new,
            "pcc_mutex_free": lambda mutex: self.mutexes.pop(mutex),
            "pcc_mutex_lock": self.lock,
            "pcc_mutex_unlock": lambda mutex: self.mutexes[mutex].release() or 0,
            "py_class_new": self.new_class,
            "py_gc_untrack": self.untrack,
            "py_runtime_error_if_unset": self.error,
            "pcc_platform_abort": lambda: (_ for _ in ()).throw(AssertionError("abort")),
            "pcc_gc_scheduler_root_register_handle": self.register,
            "pcc_gc_publish_owned_root": self.publish,
            "pcc_gc_foreign_lease_acquire": self.acquire,
            "pcc_gc_foreign_lease_release": self.release,
            "pcc_gc_root_copy_lease": self.copy,
            "pcc_gc_store_root": self.store_root,
            "pcc_gc_load_ptr": lambda owner, slot: self.slots[slot],
        }
        nodes = [node for node in ast.parse(SOURCE.read_text()).body if isinstance(node, ast.FunctionDef)]
        for node in nodes:
            node.decorator_list = []
        exec(compile(ast.Module(body=nodes, type_ignores=[]), str(SOURCE), "exec"), self.namespace)
        self.factory = self.namespace["pcc_re_match_class_copy_lease"]

    def error(self, operation, detail):
        if not getattr(self.local, "error", None):
            self.local.error = (operation, detail)

    def cas(self, slot, offset, expected, value, *orders):
        with self.atomic_lock:
            old = self.slots[slot]
            if old == expected:
                self.slots[slot] = value
            return old

    def mutex_new(self):
        if self.failure == "mutex":
            return 0
        with self.atomic_lock:
            self.next_mutex += 1
            self.mutexes[self.next_mutex] = threading.Lock()
            return self.next_mutex

    def lock(self, mutex):
        count = getattr(self.local, "locks", 0) + 1
        self.local.locks = count
        if self.failure == "first_lock" and count == 1:
            return -1
        if self.failure == "second_lock" and count == 2:
            return -1
        self.mutexes[mutex].acquire()
        return 0

    def new_class(self, name, bases, count, fields, nfields):
        assert (name, bases, count, fields, nfields) == ("re.Match", 0, 0, 0, 0)
        if self.failure == "allocation":
            return 0
        with self.atomic_lock:
            self.next_object += 1
            result = self.next_object
            self.objects[result] = {"refs": 1, "leases": 0, "immortal": False, "tracked": True}
            self.allocations.append(result)
        if self.barrier:
            self.barrier.wait(timeout=5)
        return result

    def publish(self, slot):
        assert slot in self.roots and self.slots[slot] in self.objects
        return -1 if self.failure == "publish" else 0

    def acquire(self, slot):
        assert slot in self.roots
        if self.failure == "lease":
            return -1
        with self.atomic_lock:
            obj = self.slots[slot]
            self.tokens[slot] = self.tokens.get(slot, 0) + 1
            self.objects[obj]["leases"] += 1
            # Runtime tokens are kind values (0/1/2), not unique handles.
            return 1

    def release(self, slot, token):
        assert slot in self.roots and token == 1
        with self.atomic_lock:
            assert self.tokens[slot] > 0
            obj = self.slots[slot]
            self.tokens[slot] -= 1
            if self.tokens[slot] == 0:
                del self.tokens[slot]
            self.objects[obj]["leases"] -= 1
        return 0

    def register(self, slot):
        assert self.slots[slot] == 0
        if self.failure == "register":
            return 0
        self.register_count += 1
        assert slot not in self.roots
        self.roots.add(slot)
        return 3000

    def copy(self, destination, source):
        assert destination in self.roots and source in self.roots
        assert self.slots[destination] == 0
        if self.failure == "global_copy" and destination == "pcc_re_match_class":
            return -1
        if self.failure == "cached_copy" and source == "pcc_re_match_class":
            return -1
        obj = self.slots[source]
        assert obj in self.objects
        self.slots[destination] = obj
        if not self.objects[obj]["immortal"]:
            self.objects[obj]["refs"] += 1
        return self.acquire(destination)

    def store_root(self, slot, value):
        assert slot in self.roots and value == 0
        obj = self.slots[slot]
        self.slots[slot] = 0
        if obj and not self.objects[obj]["immortal"]:
            self.objects[obj]["refs"] -= 1
            if self.objects[obj]["refs"] == 0:
                assert self.objects[obj]["leases"] == 0
                self.freed.append(obj)
                del self.objects[obj]

    def untrack(self, obj):
        assert self.objects[obj]["leases"] >= 2
        assert self.slots["pcc_re_match_class"] == obj
        self.objects[obj]["tracked"] = False

    def immortalize(self, op, obj, offset, mask, order):
        assert (op, offset, mask, order) == ("or", 12, 1, "relaxed")
        assert self.objects[obj]["leases"] >= 2
        assert self.slots["pcc_re_match_class"] == obj
        self.objects[obj]["immortal"] = True


def test_first_and_cached_factory_return_same_class_with_independent_leases():
    model = Model()
    first = model.factory(101)
    second = model.factory(102)
    assert first == second == 1
    assert model.slots[101] == model.slots[102] == model.slots["pcc_re_match_class"]
    assert len(model.allocations) == 1 and model.register_count == 1
    obj = model.objects[model.slots[101]]
    assert obj["immortal"] and not obj["tracked"] and obj["leases"] == 2
    model.release(101, first)
    model.release(102, second)
    assert not model.tokens


@pytest.mark.parametrize("failure", ["mutex", "first_lock", "allocation", "publish", "lease", "second_lock", "register", "global_copy"])
def test_failure_does_not_publish_class_or_leak_candidate_owner_or_lease(failure):
    model = Model(failure)
    assert model.factory(101) == -1
    assert model.slots[101] == 0 and model.slots["pcc_re_match_class"] == 0
    assert not model.objects and not model.tokens
    assert getattr(model.local, "error", None)
    # A successfully registered empty global root is reused on retry.
    assert model.register_count == (1 if failure == "global_copy" else 0)
    model.failure = None
    assert model.factory(101) > 0
    assert model.register_count == 1


def test_two_real_host_threads_publish_one_identity_and_free_collectible_loser():
    model = Model(barrier=threading.Barrier(2))
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(model.factory, 101)
        second = pool.submit(model.factory, 102)
        tokens = [first.result(timeout=10), second.result(timeout=10)]
    assert all(token > 0 for token in tokens)
    assert model.slots[101] == model.slots[102] == model.slots["pcc_re_match_class"]
    assert len(model.allocations) == 2 and len(model.freed) == 1
    assert len(model.objects) == 1 and model.register_count == 1
    assert next(iter(model.objects.values()))["leases"] == 2


def test_invalid_nonempty_result_is_unchanged():
    model = Model()
    model.slots[101] = 77
    assert model.factory(101) == -1
    assert model.slots[101] == 77
    assert not model.allocations



def test_cached_copy_failure_preserves_global_owner_and_empty_result():
    model = Model()
    first = model.factory(101)
    model.release(101, first)
    model.store_root(101, 0)
    winner = model.slots["pcc_re_match_class"]
    model.failure = "cached_copy"
    assert model.factory(102) == -1
    assert model.slots[102] == 0 and model.slots["pcc_re_match_class"] == winner
    assert len(model.objects) == 1 and not model.tokens


def test_cached_owner_is_reloaded_from_registered_global_after_modeled_relocation():
    model = Model()
    first = model.factory(101)
    model.release(101, first)
    model.store_root(101, 0)
    old = model.slots["pcc_re_match_class"]
    moved = old + 100
    assert model.objects[old]["leases"] == 0
    model.objects[moved] = model.objects.pop(old)
    model.slots["pcc_re_match_class"] = moved
    second = model.factory(102)
    assert second >= 0 and model.slots[102] == moved
    assert len(model.allocations) == 1
