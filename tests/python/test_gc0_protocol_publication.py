"""Keep GC0 protocol owners while avoiding nonmoving relocation work.

The models execute the production bodies. They check the ownership protocol,
not native performance. The integration case exercises rich-comparison result
publication and Python data-model special-method dispatch (section 3.3.13).
https://docs.python.org/3/reference/datamodel.html#special-method-lookup
"""

from pathlib import Path
import os
import subprocess
import sys

import pytest

from test_foreign_address_leases import _functions


ROOT = Path(__file__).resolve().parents[2]
RUNTIME = ROOT / "pcc/runtime/py"
_SLOTS = 64
_TOKENS = 128
_INDEX = 2
_RESULT_SLOT = _SLOTS + _INDEX * 8
_TOKEN_SLOT = _TOKENS + _INDEX * 8
_OBJECT = 4096
_RELOCATED = 8192
_NODE = 12288
_FORWARDING = 16384


class ProtocolModel:
    def __init__(self, backend=0, *, initialized=1, probe=0, logging=0):
        self.memory = {
            ("pcc_gc_config_initialized", 0): initialized,
            ("pcc_gc_backend_selected", 0): backend,
            ("pcc_gc_refcount_provenance_probe", 0): probe,
            ("pcc_diagnostics_runtime_log_fast_state", 0): logging,
            (_RESULT_SLOT, 0): _OBJECT,
            (_TOKEN_SLOT, 0): 0,
        }
        self.events = []
        self.error = "pending exception"
        self.acquire_status = 0 if backend < 3 else 1
        self.leased = False
        self.known = False
        self.forwarded = False
        self.ns = dict(
            C_POINTER_SIZE=8,
            ptr_add=lambda base, offset: base + offset,
            ptr_is_null=lambda value: int(value == 0),
            is_tagged_int=lambda value: value & 1,
            ptr_eq=lambda left, right: int(left == right),
            null=lambda: 0,
            cstr=lambda value: value,
            global_addr=lambda name: name,
            load_i32=self.load,
            load_i64=self.load,
            load_ptr=self.load,
            store_i32=self.store,
            store_i64=self.store,
            store_ptr=self.store,
            pcc_gc_foreign_lease_acquire=self.acquire,
            pcc_gc_foreign_lease_release=self.release,
            pcc_py_gc_minor_graph_lock=lambda: self.events.append(("lock",)),
            pcc_py_gc_minor_graph_unlock=lambda: self.events.append(("unlock",)),
            pcc_gc_note_slot_write_barrier=self.barrier,
            pcc_gc_store_root=self.clear_root,
            pcc_platform_abort=lambda: pytest.fail("unexpected abort"),
            py_runtime_error_if_unset=self.fail,
            _special_error=lambda message: self.fail("slot-based special call", message),
            pcc_gc_object_index_find=self.object_node,
            pcc_gc_forwarding_index_find=self.forwarding,
            py_incref=lambda value: self.events.append(("incref", value)),
            py_decref=lambda value: self.events.append(("decref", value)),
        )
        _functions(RUNTIME / "py_obj.py", {"pcc_gc_publish_owned_root"}, self.ns)
        _functions(RUNTIME / "freestanding_gc_root_operations.py", {
            "pcc_gc_resolve_root_slot_unlocked", "pcc_gc_object_is_known_no_lock",
        }, self.ns)
        _functions(RUNTIME / "py_class.py", {"_special_adopt", "_special_drop"}, self.ns)
        _functions(RUNTIME / "py_protocol_runtime.py", {"_named_adopt", "_named_drop"}, self.ns)

    def address(self, base, offset):
        return (base, offset) if isinstance(base, str) else (base + offset, 0)

    def load(self, base, offset=0):
        return self.memory.get(self.address(base, offset), 0)

    def store(self, base, offset, value):
        self.memory[self.address(base, offset)] = value

    def acquire(self, slot):
        self.events.append(("acquire", slot))
        if self.acquire_status >= 0:
            self.leased = self.acquire_status != 0
            if self.leased:
                self.store(slot, 0, _RELOCATED)
        return self.acquire_status

    def release(self, slot, token):
        self.events.append(("release", slot, token))
        if token != 0:
            assert self.leased
            self.leased = False
        return 0

    def barrier(self, owner, slot, value):
        if self.acquire_status > 0:
            assert self.leased
        self.events.append(("barrier", owner, slot, value))

    def clear_root(self, slot, value):
        assert value == 0 and not self.leased
        self.events.append(("clear", slot, self.load(slot)))
        self.store(slot, 0, 0)

    def fail(self, owner, message):
        self.events.append(("error", owner, message))
        if not self.error:
            self.error = message
        return -1

    def object_node(self, value):
        self.events.append(("object-index", value))
        return _NODE if self.known else 0

    def forwarding(self, value):
        self.events.append(("forwarding-index", value))
        return _FORWARDING if self.forwarded else 0


@pytest.mark.parametrize("backend", [0, 1, 2])
@pytest.mark.parametrize("value", [0, 7, _OBJECT])
def test_nonmoving_owner_resolution_needs_no_pointer_index(backend, value):
    model = ProtocolModel(backend)
    model.store(_RESULT_SLOT, 0, value)
    model.store(_OBJECT, 0, 5)
    model.store(_OBJECT, 12, 64)
    before = dict(model.memory)
    assert model.ns["pcc_gc_resolve_root_slot_unlocked"](_SLOTS, _INDEX * 8) == value
    assert model.memory == before
    assert model.events == []


@pytest.mark.parametrize("settings", [{"initialized": 0}, {"probe": 1}, {"logging": 1}])
def test_nonmoving_resolver_keeps_uninitialized_and_checked_paths(settings):
    model = ProtocolModel(**settings)
    model.store(_RESULT_SLOT, 0, _OBJECT)
    assert model.ns["pcc_gc_resolve_root_slot_unlocked"](_RESULT_SLOT, 0) == _OBJECT
    assert model.events == [("object-index", _OBJECT), ("forwarding-index", _OBJECT)]


@pytest.mark.parametrize("backend", [3, 4])
@pytest.mark.parametrize("known", [False, True])
def test_moving_resolution_keeps_the_owning_slot_transfer(backend, known):
    model = ProtocolModel(backend)
    model.store(_RESULT_SLOT, 0, _OBJECT)
    model.store(_OBJECT, 12, 2048 | 64)
    model.store(_FORWARDING, 8, _RELOCATED)
    model.known = known
    model.forwarded = True
    assert model.ns["pcc_gc_resolve_root_slot_unlocked"](_RESULT_SLOT, 0) == _RELOCATED
    assert model.load(_RESULT_SLOT) == _RELOCATED
    expected = [("object-index", _OBJECT), ("forwarding-index", _OBJECT)]
    if backend == 3:
        expected += [("incref", _RELOCATED), ("decref", _OBJECT)]
    assert model.events == expected


@pytest.mark.parametrize("adopt", ["_special_adopt", "_named_adopt"])
@pytest.mark.parametrize("prior_pin", [0, 64])
def test_gc0_adoption_preserves_owner_pin_and_pending_exception(adopt, prior_pin):
    model = ProtocolModel()
    model.store(_RESULT_SLOT, 0, _OBJECT)
    model.store(_OBJECT, 0, 5)
    model.store(_OBJECT, 12, prior_pin)
    before = dict(model.memory)
    assert model.ns[adopt](_SLOTS, _TOKENS, _INDEX) == 0
    assert model.memory == before
    assert model.events == []
    assert model.error == "pending exception"


@pytest.mark.parametrize("adopt", ["_special_adopt", "_named_adopt"])
@pytest.mark.parametrize("backend", [1, 2, 3, 4])
def test_other_collectors_keep_the_lease_until_the_owner_is_dropped(adopt, backend):
    model = ProtocolModel(backend)
    model.store(_RESULT_SLOT, 0, _OBJECT)
    assert model.ns[adopt](_SLOTS, _TOKENS, _INDEX) == 0
    current = _RELOCATED if backend >= 3 else _OBJECT
    token = model.acquire_status
    assert model.load(_RESULT_SLOT) == current
    assert model.load(_TOKEN_SLOT) == token
    assert model.events == [
        ("acquire", _RESULT_SLOT), ("lock",),
        ("barrier", 0, _RESULT_SLOT, current), ("unlock",),
    ]
    model.ns[adopt.replace("adopt", "drop")](_SLOTS, _TOKENS, _INDEX)
    assert model.events[-2:] == [
        ("release", _RESULT_SLOT, token), ("clear", _RESULT_SLOT, current),
    ]
    assert model.load(_RESULT_SLOT) == 0 and model.load(_TOKEN_SLOT) == 0


@pytest.mark.parametrize("adopt", ["_special_adopt", "_named_adopt"])
@pytest.mark.parametrize("settings", [{"initialized": 0}, {"probe": 1}, {"logging": 1}])
def test_gc0_adoption_keeps_configuration_probe_and_logging_protocols(adopt, settings):
    model = ProtocolModel(**settings)
    model.store(_RESULT_SLOT, 0, _OBJECT)
    assert model.ns[adopt](_SLOTS, _TOKENS, _INDEX) == 0
    assert [event[0] for event in model.events] == ["acquire", "lock", "barrier", "unlock"]


@pytest.mark.parametrize("adopt", ["_special_adopt", "_named_adopt"])
@pytest.mark.parametrize("status", [-1, -2, -3])
def test_checked_adoption_failure_never_publishes_or_overwrites_an_exception(adopt, status):
    model = ProtocolModel(probe=1)
    model.store(_RESULT_SLOT, 0, _OBJECT)
    model.acquire_status = status
    before = dict(model.memory)
    assert model.ns[adopt](_SLOTS, _TOKENS, _INDEX) == -1
    assert model.memory == before
    assert [event[0] for event in model.events] == ["acquire", "error"]
    assert model.error == "pending exception"


_NATIVE_SOURCE = '''import gc
import weakref

events = []

class Answer:
    def __bool__(self):
        gc.collect()
        return True
    def __del__(self):
        events.append("answer")

class Compare:
    def __eq__(self, other):
        gc.collect()
        return Answer()

class BadCompare:
    def __eq__(self, other):
        raise ValueError("eq failure")

def equal(left, right):
    return bool(left == right)

def main():
    left = Compare()
    right = Compare()
    reference = weakref.ref(left)
    assert equal(left, right)
    gc.collect()
    assert reference() is left
    assert events == ["answer"]
    try:
        equal(BadCompare(), right)
    except ValueError as error:
        assert str(error) == "eq failure"
    else:
        raise AssertionError("lost comparison error")
    assert equal(left, right)
    gc.collect()
    assert events == ["answer", "answer"]
    print("GC0_PROTOCOL_PUBLICATION_OK")

main()
'''


@pytest.mark.integration
def test_protocol_publication_executes_rich_comparison_on_five_collectors(tmp_path, pcc_runtime_archive):
    from pcc.frontends.python.pipeline import compile_python

    source = tmp_path / "protocol_publication.py"
    source.write_text(_NATIVE_SOURCE)
    reference = subprocess.run(
        [sys.executable, str(source)], capture_output=True, text=True, timeout=20,
    )
    assert (reference.returncode, reference.stdout, reference.stderr) == (
        0, "GC0_PROTOCOL_PUBLICATION_OK\n", "",
    )
    binary = tmp_path / "protocol_publication"
    compile_python(
        str(source), str(binary), backend="self", ir_scaffold_mode="on",
        runtime_archive=str(pcc_runtime_archive),
    )
    for backend in range(5):
        result = subprocess.run(
            [str(binary)], capture_output=True, text=True, timeout=30,
            env=dict(os.environ, PATH="/nonexistent", PCC_GC_BACKEND=str(backend)),
        )
        assert (result.returncode, result.stdout, result.stderr) == (
            0, reference.stdout, "",
        ), backend
