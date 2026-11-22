"""Run existing source branches against a hypothetical GC3 nursery model."""

import ast
from pathlib import Path

import pytest

from pcc.runtime.py import py_abi_constants as abi
from tests.python.gc.gc3_sparse_remembered_model import Model

RUNTIME = Path(__file__).resolve().parents[3] / "pcc/runtime/py"


def _source_function(file, name, bindings):
    path = RUNTIME / file
    body = ast.parse(path.read_text(encoding="utf-8")).body
    function, = (node for node in body if isinstance(node, ast.FunctionDef) and node.name == name)
    function.decorator_list = []
    exec(compile(ast.Module([function], type_ignores=[]), str(path), "exec"), bindings)
    return bindings[name]


def test_gc3_dict_admission_and_fast_init_discard_remembered_owner():
    model = Model()
    model.add_old(1000, "dict-entry-card")
    model.add_young(2000)
    backend_3_no_major = {
        "pcc_gc_backend_selected": 3,
        "pcc_gc_nonmoving_count_free": 1,
        "pcc_gc_foreign_lease_active": 0,
        "pcc_diagnostics_runtime_log_fast_state": 0,
    }
    slot, supplied = 3200, 3300
    memory = {slot: 0, supplied: 2000}
    shades = []

    def store(pointer, offset, value):
        memory[pointer + offset] = value
        assert pointer == slot and offset == 0
        # Mirror the raw store, not a barrier for the old dictionary's card.
        model.cards["dict-entry-card"][1000] = (value,)

    bindings = {
        "c_ptr": int,
        "i64": int,
        "global_addr": lambda name: name,
        "load_i32": lambda ptr, offset=0: (
            backend_3_no_major[ptr] if isinstance(ptr, str) else abi.PY_TYPE_LIST
        ),
        "atomic_load_i64": lambda name, offset, order: backend_3_no_major[name],
        "ptr_is_null": lambda ptr: int(ptr == 0),
        "ptr_eq": lambda a, b: int(a == b),
        "is_tagged_int": lambda _: 0,
        "load_ptr": lambda ptr, offset: memory[ptr + offset],
        "store_ptr": store,
        "pcc_gc_count_free_store_shade": shades.append,
    }
    for name in ("PYOBJECTHEADER_TYPE_TAG_OFFSET", "PY_TYPE_NONE", "PY_TYPE_CONTINUATION",
                 "PY_TYPE_CPY_HANDLE", "PY_TYPE_USER", "PY_TYPE_CEXT_TAG_BASE"):
        bindings[name] = getattr(abi, name)

    admit = _source_function("py_dict.py", "_dict_gc0_admitted_locked", bindings)
    initialize = _source_function("freestanding_gc_root_operations.py", "pcc_gc_try_init_ptr_locked", bindings)
    assert admit() == 1
    assert initialize(slot, supplied) == 1
    assert memory[slot] == 2000 and shades == [2000]

    # The major shade receives only the value. It cannot remember the card.
    assert model.full_reachable_young((1000,)) == {2000}
    assert model.minor((1000,)).live_young == set()


def test_gc3_dict_pair_publication_has_one_owner_boundary_and_no_rollback_edge():
    from tests.python.test_dict_gc0_locked_get_set import Heap, SET_NAMES, _entry, _functions

    heap = Heap()
    heap.flags.update({
        "pcc_gc_backend_selected": 3,
        "pcc_gc_nonmoving_count_free": 1,
        "pcc_gc_refcount_atomic": 0,
    })
    key, rejected, value = heap.obj(), heap.obj(), heap.obj()
    owner = heap.dict([])
    heap.rejected.add(rejected)
    indices = heap.load(owner, abi.PYDICTOBJECT_INDICES_OFFSET)
    target_index = 5 * 8  # Heap.probe uses bucket 5 for an absent key.
    heap.store(indices, target_index, -1)
    model = Model()
    model.add_old(owner, "dictionary")
    for young in (key, value, rejected):
        model.add_young(young)
    publications = []
    dictionary = _functions(heap, SET_NAMES)

    def observe_commit(address, offset, incoming):
        heap.store(address, offset, incoming)
        if (address, offset) != (owner, abi.PYDICTOBJECT_ITEM_COUNT_OFFSET):
            return
        assert heap.locked()
        assert incoming == 1
        assert heap.load(owner, abi.PYDICTOBJECT_ENTRIES_USED_OFFSET) == 1
        assert heap.load(indices, target_index) == 0
        edges = _entry(heap, owner, 0)
        assert edges == (key, value)
        publications.append((owner, edges))
        # Hypothetical remembered write; the current source has no such hook.
        model.store_old("dictionary", owner, edges)

    dictionary["store_i64"] = observe_commit
    set_fast = dictionary["_dict_set_gc0_locked"]
    assert set_fast(owner, key, rejected, 2) == 0
    assert _entry(heap, owner, 0) == (0, 0)
    assert heap.load(owner, abi.PYDICTOBJECT_ITEM_COUNT_OFFSET) == 0
    assert heap.load(indices, target_index) == -1
    assert publications == [] and model.dirty == {}

    assert set_fast(owner, key, value, 2) == 1
    assert publications == [(owner, (key, value))]
    assert model.full_reachable_young((owner,)) == {key, value}
    assert model.minor((owner,)).live_young == {key, value}
    assert model.minor(()).live_young == {key, value}


def test_gc3_raw_generator_frame_retains_owner_at_the_store_transaction():
    frame, items, first, replacement = 0x1000, 0x2000, 0x3000, 0x4000
    memory = {
        (frame, abi.PYLISTOBJECT_LENGTH_OFFSET): 2,
        (frame, abi.PYLISTOBJECT_ITEMS_OFFSET): items,
        (items, 0): 0,
    }
    events = []

    def store(address, offset, value):
        events.append(("store", address, value))
        memory[address, offset] = value

    bindings = {
        "pcc_gc_backend": lambda: 3,
        "ptr_is_null": lambda pointer: pointer == 0,
        "global_addr": lambda name: name,
        "load_i32": lambda pointer, offset: (
            1 if pointer == "pcc_gc_nonmoving_count_free" else abi.PY_TYPE_LIST
        ),
        "load_i64": lambda address, offset: memory[address, offset],
        "load_ptr": lambda address, offset: memory[address, offset],
        "store_ptr": store,
        "ptr_add": lambda pointer, offset: pointer + offset,
        "pcc_container_lock": lambda owner: events.append(("lock", owner)),
        "pcc_gc_count_free_store_shade": lambda value: events.append(("shade", value)),
        "pcc_container_unlock": lambda owner: events.append(("unlock", owner)),
    }
    for name in ("PY_TYPE_GEN", "PYLISTOBJECT_LENGTH_OFFSET",
                 "PYLISTOBJECT_ITEMS_OFFSET", "C_POINTER_SIZE"):
        bindings[name] = getattr(abi, name)
    _source_function("py_gen.py", "_frame_list_raw", bindings)
    commit = _source_function("py_gen.py", "py_gen_frame_set", bindings)
    for value in (first, replacement, 0):
        events.clear()
        commit(frame, 0, value)
        assert events == [
            ("lock", frame), ("shade", value), ("store", items, value), ("unlock", frame),
        ]
        assert memory[items, 0] == value


@pytest.mark.parametrize("backend", [1, 2, 3, 4])
def test_only_gc3_generic_heap_store_transports_the_owner(backend):
    owner, field, root, young = 10, 20, 30, 40
    flags = {
        "pcc_gc_backend_selected": backend,
        "pcc_gc_nonmoving_count_free": 1,
        "pcc_gc_config_initialized": 1,
        "pcc_gc_backend4_no_refcounts": 1,
        "pcc_diagnostics_runtime_log_fast_state": 0,
        "pcc_gc_refcount_provenance_probe": 0,
    }
    stores = []
    bindings = {
        "ptr_is_null": lambda pointer: pointer == 0,
        "global_addr": lambda name: name,
        "load_i32": lambda name, offset: flags[name],
        "_gc0_owner_fast_path": lambda: 0,
        "_gc_backend_fast": lambda: backend,
        "_store_root_nonmoving_tracing": lambda *args: stores.append(args),
        "_store_heap_nonmoving_gc3": lambda *args: stores.append(args),
    }
    store_heap = _source_function("py_obj.py", "pcc_gc_store_ptr", bindings)
    store_root = _source_function("py_obj.py", "pcc_gc_store_root", bindings)
    store_heap(owner, field, young)
    store_root(root, young)
    assert stores == [
        (owner, field, young) if backend == 3 else (field, young, backend),
        (root, young, backend),
    ]


def test_gc3_root_helper_never_shades_an_unindexed_cell_even_during_major():
    events = []
    state = {"active": 0, "indexed": 0}
    bindings = {
        "ptr_is_null": lambda pointer: pointer == 0,
        "is_tagged_int": lambda _: 0,
        "global_addr": lambda name: name,
        "atomic_load_i32": lambda obj, offset, order: (
            state["active"] if obj == "pcc_gc_mark_active" else 0
        ),
        "pcc_thread_no_park_enter": lambda: events.append(("enter",)),
        "pcc_thread_no_park_exit": lambda: events.append(("exit",)),
        "pcc_gc_object_index_seen_stable": lambda obj: state["indexed"],
        "store_ptr": lambda obj, offset, val: events.append(("store", obj, val)),
        "pcc_gc_object_is_white": lambda obj, flags: 1,
        "pcc_gc_shade_gray_atomic": lambda obj, allow_black: events.append(("shade", obj)),
        "atomic_rmw_i64": lambda *args: None,
        "pcc_gc_metric_shard": lambda: 0,
        "PYOBJECTHEADER_FLAGS_OFFSET": abi.PYOBJECTHEADER_FLAGS_OFFSET,
        "i64": int,
    }
    store = _source_function("py_obj.py", "_store_root_nonmoving_tracing", bindings)
    args = (0x2000, 0x3000, 3)
    raw = [("store", *args[:2])]
    store(*args)
    assert events == raw

    events.clear()
    state["active"] = 1
    store(*args)
    assert events == raw + [("enter",), ("exit",)]

    events.clear()
    state["indexed"] = 1
    store(*args)
    assert events == raw + [("enter",), ("shade", args[1]), ("exit",)]
